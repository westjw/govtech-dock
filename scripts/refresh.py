#!/usr/bin/env python3
"""Refresh the "Hiring AEs?" status for every company in data/companies.json.

Usage:
  python scripts/refresh.py                 # refresh everything
  python scripts/refresh.py --company obvio # refresh one company (by id or name)
  python scripts/refresh.py --dry-run       # fetch + classify, write nothing
  python scripts/refresh.py --ci            # quieter output for GitHub Actions
  python scripts/refresh.py --force         # replace today's snapshot if re-running

What a run does:
  1. For each company, fetch its job board via scripts/ats.py and classify
     titles via scripts/classify.py.
  2. Companies whose board can't be read keep status "Unknown" (with the error
     in the note). Companies with ats.type == "unknown" are skipped and listed
     at the end - ask Claude Code to discover their ATS (see .claude/commands).
  3. Write a dated snapshot to data/hiring_history/YYYY-MM-DD.json, update the
     hiring block in data/companies.json, compute the diff vs the previous
     snapshot into data/latest_diff.json, and update data/meta.json.

The script is fully deterministic - no AI calls. Fuzzy work (new ATS discovery,
odd titles) is Claude Code's job, interactively.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402
import classify       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "hiring_history"


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def dump_json(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")


def previous_snapshot(today: str):
    """Snapshot to diff against. If a run already happened today, that run is
    the baseline - otherwise a same-day re-run would report every company as
    unchanged-from-nothing and lose the real movement."""
    if (HISTORY / f"{today}.json").exists():
        return today
    snaps = sorted(p.stem for p in HISTORY.glob("*.json") if p.stem != today)
    return snaps[-1] if snaps else None


# Hosts that run job boards for other people. A board on one of these is not
# somebody else's company site, it is a vendor doing its job.
_ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "bamboohr.com",
    "myworkdayjobs.com", "workday.com", "recruitee.com", "breezy.hr", "gusto.com",
    "smartrecruiters.com", "jazzhr.com", "paylocity.com", "icims.com",
    "rippling.com", "applytojob.com", "taleo.net", "successfactors.com",
    "jobscore.com", "comeet.com", "teamtailor.com", "trinethire.com",
    "applicantpro.com", "hirehive.com", "hrmdirect.com", "ttcportals.com",
    "jobvite.com", "dayforcehcm.com", "adp.com", "ycombinator.com",
    "gnahiring.com", "trakstar.com", "oraclecloud.com", "paycomonline.net",
)


def _someone_elses_site(website: str, ref) -> bool:
    """Is this board on a domain belonging to a different COMPANY?

    Five cards said "Yes - AE-type role" on the strength of a page scan of
    somebody else's careers page. Cartegraph's board is opengov.com, and its
    own description reads "part of OpenGov". ACTIVE's is activenetwork.com.
    Aladtec's is tcpsoftware.com. Every one showed zero postings, because
    build_board already refuses to count a shared board twice - so a visitor
    saw "Yes, hiring an AE" with nothing to click and no way to check.

    A page scan is the weakest evidence there is: it proves some AE-ish words
    appeared somewhere on a page. Those words appearing on the PARENT's page
    say nothing whatever about the subsidiary, and CLAUDE.md is explicit that
    reporting a parent's requisition as theirs is a false Yes.

    An ATS host does not count. greenhouse.io is not a company whose jobs
    might be mistaken for this one's; it is the filing cabinet.
    """
    from urllib.parse import urlparse

    def root(u):
        h = (urlparse(u or "").hostname or "").lower().replace("www.", "")
        parts = h.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else h

    if not isinstance(ref, str) or not ref.startswith("http"):
        return False
    site, board = root(website), root(ref)
    if not site or not board or site == board:
        return False
    return board not in _ATS_HOSTS


def _try_render(kind: str, ref) -> dict | None:
    """Read a JS-shelled careers page with a real browser, or return None.

    build_board.py has done this for a long time and refresh.py did not, so the
    two pipelines disagreed in public: the board listed a Strategic Account
    Executive for Frontline Education while that company's own card said
    Unknown. Eleven AE roles sat on the board under cards that would not admit
    the company was hiring.

    Optional exactly as it is there - imported lazily, skipped when Playwright
    is absent, and never able to turn a working run into a failing one. A page
    that will not render keeps the Unknown it already had, which is the honest
    answer.

    Returns None rather than an Unknown dict so the caller keeps its own,
    better-worded failure note: "page too small - likely JS-rendered" says more
    than "rendered and still nothing".
    """
    if kind != "html" or not ref:
        return None
    try:
        import render_fetch
        if not render_fetch.available():
            return None
        jobs = ats.plain_rows(render_fetch.fetch_rendered(ref))
        status, note, roles = classify.rollup(jobs)
    except Exception:
        return None
    if status == "Unknown":
        return None
    return {"status": status, "note": (note + " [rendered]")[:60], "roles": roles}


def check_company(comp: dict) -> dict:
    """Return {"status", "note", "roles"} for one company."""
    kind = comp["ats"]["type"]
    ref = comp["ats"].get("ref")
    if kind == "unknown":
        return {"status": "Unknown", "note": "no ATS on file", "roles": [], "skipped": True}
    try:
        jobs = ats.fetch(comp["ats"])
        status, note, roles = classify.rollup(jobs)
    except ats.AtsError as exc:
        # "page too small - likely JS-rendered" is the MOST render-appropriate
        # failure there is, and the first version of this fallback never saw it
        # because this branch returns before the rollup. Caselle said exactly
        # that and stayed Unknown while the board carried an Account Manager
        # for it.
        rendered = _try_render(kind, ref)
        if rendered:
            return rendered
        return {"status": "Unknown", "note": str(exc)[:40], "roles": []}
    except Exception as exc:  # noqa: BLE001 - deliberately everything
        # One malformed job among ~1,150 boards - a null title, a payload
        # that came back as a list, a workday ref of the wrong shape - used
        # to kill the entire run, and because every write happens after the
        # loop, 40 minutes of successful fetches died with it. The review
        # reproduced nineteen distinct ways in. A company whose board cannot
        # be understood is Unknown, exactly like one whose board cannot be
        # reached; it is never a reason to lose everyone else's snapshot.
        return {"status": "Unknown",
                "note": f"fetcher crashed: {type(exc).__name__}"[:40],
                "roles": []}
    # A PAGE SCAN THAT READ NOTHING IS A SHELL, AND build_board ALREADY KNOWS
    # WHAT TO DO ABOUT IT. It falls back to a real browser; this did not, so
    # the two pipelines disagreed in public: the board listed a Strategic
    # Account Executive for Frontline Education while the company's own card
    # said Unknown. Eleven AE roles were on the board under cards that would
    # not admit the company was hiring.
    #
    # Optional exactly as it is there - imported lazily, skipped entirely when
    # Playwright is absent, and never allowed to turn a working run into a
    # failing one. A render that raises leaves the Unknown alone, which is the
    # honest answer it already had.
    if status == "Unknown":
        rendered = _try_render(kind, ref)
        if rendered:
            status, note, roles = (rendered["status"], rendered["note"],
                                   rendered["roles"])
    # A page-scan verdict read off someone else's careers page is not evidence
    # about this company - see _someone_elses_site. Downgraded to Unknown with
    # the reason named, rather than to "None found": we still have not looked
    # at THEIR board, and saying we found nothing would be the other false
    # claim.
    if (kind == "html" and status in ("Yes", "Sales (non-AE)")
            and _someone_elses_site(comp.get("website"), ref)):
        return {"status": "Unknown",
                "note": "board is another company's - not read"[:60],
                "roles": []}
    if kind == "html" and status == "Yes":
        note = (note + " [page scan - verify]")[:60]
    return {"status": status, "note": note, "roles": roles}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="refresh a single company by id or name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--force", action="store_true",
                    help="overwrite today's snapshot if a run already happened today")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    if (HISTORY / f"{today}.json").exists() and not (args.force or args.dry_run):
        print(f"data/hiring_history/{today}.json already exists - a run already "
              f"happened today.\nRe-run with --force to replace it (the diff will "
              f"then be against that run), or --dry-run to look without writing.",
              file=sys.stderr)
        return 1

    companies = load_json(DATA / "companies.json")
    prev_name = previous_snapshot(today)
    prev = load_json(HISTORY / f"{prev_name}.json")["companies"] if prev_name else {}

    targets = companies
    if args.company:
        needle = args.company.lower()
        targets = [c for c in companies
                   if needle in (c["id"], c["name"].lower())]
        if not targets:
            print(f"no company matching {args.company!r}", file=sys.stderr)
            return 1

    snapshot, changes, skipped = {}, [], []
    for comp in companies:
        if comp not in targets:
            # keep last known state for companies outside a --company run
            snapshot[comp["id"]] = {k: comp["hiring"][k] for k in ("status", "note", "roles")}
            continue
        result = check_company(comp)
        if result.pop("skipped", False):
            skipped.append(comp["name"])
        old = prev.get(comp["id"], {}).get("status", comp["hiring"]["status"])
        if result["status"] != old:
            changes.append({"company": comp["name"], "id": comp["id"],
                            "from": old, "to": result["status"], "note": result["note"]})
        if not args.ci:
            mark = "*" if result["status"] != old else " "
            print(f"{mark} {comp['name']:<34} {result['status']:<15} {result['note']}")
        comp["hiring"] = {**result, "checked": today}
        snapshot[comp["id"]] = result
        time.sleep(args.delay)

    counts = {}
    for entry in snapshot.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    print(f"\n{len(targets)} checked | " +
          " | ".join(f"{k}: {v}" for k, v in sorted(counts.items())) +
          f" | {len(changes)} changed")
    if skipped:
        print(f"needs ATS discovery ({len(skipped)}): " + ", ".join(sorted(skipped)))
    for ch in changes:
        print(f"  {ch['company']}: {ch['from']} -> {ch['to']}  {ch['note']}")

    if args.dry_run:
        print("\n(dry run - nothing written)")
        return 0

    dump_json(HISTORY / f"{today}.json", {"date": today, "companies": snapshot})
    dump_json(DATA / "companies.json", companies)
    dump_json(DATA / "latest_diff.json", {"date": today, "previous": prev_name, "changes": changes})
    dump_json(DATA / "meta.json", {"last_run": today, "previous_run": prev_name,
                                   "counts": counts, "method": "ats-api refresh"})
    print(f"\nwrote data/hiring_history/{today}.json and updated companies/meta/diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
