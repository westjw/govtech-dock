#!/usr/bin/env python3
"""Find the ATS behind a careers page we cannot enumerate.

    python3 scripts/find_boards.py --limit 40          # look, propose nothing
    python3 scripts/find_boards.py --limit 40 --write  # land proposals

WHY THIS AND NOT ANOTHER READ PASS. The read agent's own trial is the argument:
of 25 page-only careers pages, three named an enumerable ATS one link away -
Autura's iframe hands over a Greenhouse slug, Nallian's page exposes a Workable
address, Dominion's careers link is already a Paylocity board. Reading a page is
a snapshot somebody has to re-take by hand and it decays the same night.
FINDING THE BOARD IS PERMANENT: it becomes an `ats` entry and refresh.py keeps
it current forever. DebtBook is not fourteen rows, it is a Greenhouse board.

781 companies currently hold a careers URL that will not enumerate, and every
one of them shows zero jobs on the public board.

WHAT IS DETERMINISTIC HERE AND WHAT IS NOT, which is the whole design.

CLAUDE.md: "Briefs are built here rather than by the agent because an agent
that gathers its own context gathers different context every run, and two
proposals that disagree then cannot be compared." So this script does all the
work a regex can do and none of the work it cannot:

  DETERMINISTIC, and done here
    - fetch the careers page ONCE (no crawl, no pagination, no login)
    - pull every ATS host and slug out of the markup, including iframe srcs,
      which is where Autura's Greenhouse board was hiding
    - VERIFY each candidate against that ATS's real API and count the rows
    - compare those rows against the company we hold

  JUDGMENT, and left to a person
    - is this board THIS company's, or its parent's?

That second question is the only one that matters and no fetch can answer it.
CLAUDE.md is explicit: "Never point a company at its parent's job board.
Several here were acquired and their careers pages redirect to the parent's
Workday. Wiring that up would report a parent-company AE req as the
subsidiary's, which is a false 'Yes'." So a verified board is a PROPOSAL, never
a write - it lands in the same queue every other agent uses and waits.

A SLUG IS NEVER TRUSTED BECAUSE IT LOOKS RIGHT. CLAUDE.md again: "Always verify
a slug with a real fetch before writing it - Lever slugs are lowercase, and an
off-site careers link occasionally lands on another company's board." Every
candidate below is fetched. One that 404s is discarded silently; one that
answers is proposed with its row count and three sample titles as the evidence
a person rules on.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                        # noqa: E402
import ats                                           # noqa: E402

# Every host whose slug this project can actually enumerate, and the group in
# its pattern that IS the slug. Ordered so the more specific host wins.
#
# `html` is deliberately absent: finding another page we cannot read is not a
# finding. So is `unknown`. If a pattern here ever stops matching what
# ats.py fetches, selftest::check_find_boards_targets_real_fetchers says so.
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # THE EMBED FORM FIRST, because it is the commonest and the one the first
    # draft of this file got wrong. Autura serves
    # `boards.greenhouse.io/embed/job_board/js?for=autura` - note the `/js`,
    # which a pattern written from memory as `/embed/job_board?for=` misses
    # entirely. Every form below was read off a real page, not recalled.
    ("greenhouse", re.compile(
        r"greenhouse\.io/embed/job_board(?:/js)?\?for=([a-z0-9_-]{2,60})", re.I)),
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)\.greenhouse\.io/([a-z0-9_-]{2,60})", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]{2,60})", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9-]{2,60})", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9._-]{2,60})", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9._-]{2,60})", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9-]{2,60})/?", re.I)),
    # Workable mails from <slug>@jobs.workablemail.com, and Nallian's careers
    # page carries no other reference to its board at all - the address in the
    # "no perfect match yet, send your resume to" line is the only tell.
    ("workable", re.compile(r"([a-z0-9-]{2,60})@jobs\.workablemail\.com", re.I)),
    ("recruitee", re.compile(r"([a-z0-9-]{2,60})\.recruitee\.com", re.I)),
    ("breezy", re.compile(r"([a-z0-9-]{2,60})\.breezy\.hr", re.I)),
    ("smartrecruiters", re.compile(
        r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]{2,60})", re.I)),
    ("bamboohr", re.compile(r"([a-z0-9-]{2,60})\.bamboohr\.com", re.I)),
)

# Slugs that are the ATS's own furniture rather than an employer. Matching one
# of these means the page embedded a widget's boilerplate, not a board.
NOT_A_SLUG = {"www", "api", "app", "jobs", "job", "careers", "boards", "embed",
              "assets", "cdn", "static", "help", "support", "blog", "docs",
              "developer", "developers", "status", "login", "account", "my"}


def candidates(html: str) -> list[tuple[str, str]]:
    """Every (ats_type, slug) an ATS host in this markup could name.

    Reads the RAW markup rather than a stripped-text version on purpose: the
    slug usually lives in an iframe src or a script tag, which is exactly what
    a text extractor throws away. Autura's Greenhouse board is an iframe.
    """
    out, seen = [], set()
    for kind, rx in PATTERNS:
        for m in rx.finditer(html or ""):
            slug = m.group(1)
            if slug.lower() in NOT_A_SLUG:
                continue
            key = (kind, slug.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append((kind, slug))
    return out


def verify(kind: str, slug: str) -> dict | None:
    """Fetch the candidate board for real. None when it does not answer.

    THE POINT OF THE WHOLE SCRIPT. A slug lifted out of markup is a guess
    until something fetches it: Lever slugs are lowercase, embeds carry stale
    names, and an off-site careers link occasionally lands on another
    company's board entirely. What comes back - the row count and the first
    few titles - is also the evidence a person needs to rule on ownership,
    which is the one question this script cannot answer for them.
    """
    fn = ats.FETCHERS.get(kind)
    if fn is None:
        return None
    try:
        rows = fn(slug)
    except Exception:                        # noqa: BLE001
        # A board that errors is not a board that is wrong; it is one we
        # learned nothing about. Discarded silently rather than proposed.
        return None
    if not rows:
        return None
    titles = [(r.get("title") or "").strip() for r in rows]
    titles = [t for t in titles if t]
    return {"type": kind, "ref": slug, "rows": len(rows),
            "sample": titles[:3],
            "quota": sum(1 for t in titles if ats_quota(t))}


def ats_quota(title: str) -> bool:
    try:
        import roles
        return bool(roles.is_quota_carrying(title))
    except Exception:                        # noqa: BLE001
        return False


def worklist(limit: int | None = None) -> list[dict]:
    """Companies holding a careers page that yields nothing.

    Ordered by whether the company has EVER shown a quota-carrying role, so a
    run with a small limit spends it on the companies this board exists for.
    """
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    live = {o["id"]: o for o in board.get("organizations", [])}
    done = agents.load()
    out = []
    for c in companies:
        a = c.get("ats") or {}
        url = a.get("ref") or a.get("url") or c.get("careers_url")
        if a.get("type") != "html" or not url:
            continue
        if (live.get(c["id"]) or {}).get("open_roles"):
            continue                          # already yields; leave it alone
        if f"board:{c['id']}" in done:
            continue                          # already proposed once
        out.append({"id": c["id"], "name": c["name"], "url": url,
                    "sector": c.get("sector"),
                    "ever": bool((live.get(c["id"]) or {}).get("quota_roles"))})
    out.sort(key=lambda r: (not r["ever"], r["name"]))
    return out[:limit] if limit else out


def look(row: dict) -> dict:
    """One page, once. Returns the row with whatever was found on it."""
    try:
        resp = ats._get(row["url"])
        html = resp.text or ""
    except Exception as e:                    # noqa: BLE001
        return {**row, "error": f"{type(e).__name__}", "found": []}
    found = []
    for kind, slug in candidates(html):
        v = verify(kind, slug)
        if v:
            found.append(v)
    return {**row, "found": found}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--write", action="store_true",
                    help="land proposals; the default only looks")
    a = ap.parse_args()

    rows = worklist(a.limit)
    print(f"{len(rows)} careers page(s) to look at "
          f"(of {len(worklist())} on the worklist)\n")
    proposals, hits = [], 0
    for r in rows:
        got = look(r)
        if got.get("error"):
            print(f"  {r['name'][:30]:32} could not read it ({got['error']})")
            continue
        if not got["found"]:
            print(f"  {r['name'][:30]:32} no ATS named on the page")
            continue
        hits += 1
        best = max(got["found"], key=lambda f: (f["quota"], f["rows"]))
        others = [f for f in got["found"] if f is not best]
        print(f"  {r['name'][:30]:32} {best['type']}/{best['ref']} "
              f"-> {best['rows']} row(s), {best['quota']} quota-carrying")
        for t in best["sample"]:
            print(f"      {t[:66]}")
        if others:
            print(f"      (+{len(others)} other host(s) on the page)")
        proposals.append({
            "kind": "board", "key": f"board:{r['id']}",
            "id": r["id"], "name": r["name"],
            "confidence": "medium",
            "ats_type": best["type"], "ats_ref": best["ref"],
            "rows": best["rows"], "quota": best["quota"],
            "sample": best["sample"],
            "evidence": r["url"],
            "why": (f"{r['url']} names {best['type']} board "
                    f"{best['ref']!r}, which answers with {best['rows']} "
                    f"posting(s). Verified by fetching it."),
            "saw": {"method": "one page load, no crawl, no login",
                    "page": r["url"],
                    "candidates": [f"{f['type']}/{f['ref']}" for f in got["found"]],
                    "verified_by": "a real fetch of the ATS api",
                    "not_checked": "whether this board is theirs or a parent's"},
        })

    print(f"\n  {hits} of {len(rows)} page(s) named a board that answered")
    if not a.write:
        print("  LOOKED ONLY. Nothing was written. Re-run with --write.")
        return 0
    rep = agents.ingest("board", proposals, model="find_boards.py")
    print(f"  landed {rep['kept']} proposal(s); {len(rep['refused'])} refused")
    for x in rep["refused"]:
        print(f"    {x['key']}: {x['why']}")
    print("  They are PROPOSALS. Nothing reaches companies.json until a person "
          "rules on whether each board is that company's or a parent's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
