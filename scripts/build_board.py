#!/usr/bin/env python3
"""Build the SLED job board and market intelligence dataset.

refresh.py answers one question per company: is anyone hiring an AE? That is the
right question for a prospecting tracker and the wrong one for a job board,
because it discards every non-sales opening before anything is stored.

This keeps the whole board. For each company it fetches every posting, tags it
with a role family and a US determination, and writes:

  data/board.json     every open posting, plus per-company aggregates
  data/history/*.json dated snapshot of posting ids, for repost detection

The market-intelligence signal is the family mix. A company hiring twelve
engineers and no sellers is in a different phase than one hiring eight AEs, and
that difference is invisible if you only ever counted AEs.

  python scripts/build_board.py [--limit N] [--company id] [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402
import roles          # noqa: E402

try:
    import render_fetch                            # noqa: E402  optional
except ImportError:                                # pragma: no cover
    render_fetch = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "history"

# sector -> buyer motion tier. 1 = municipal SaaS full-cycle, 2 = adjacent.
TIER = {"General Gov": 1, "Public Works": 1, "Parks & Rec": 1,
        "Public Safety": 2, "Transit & Parking": 2, "K-12 Schools": 2,
        # Municipal utilities and airports are public-sector buyers with the same
        # procurement motion, one step further from the city-hall relationship.
        "Utilities & Energy": 2, "Airports & Aviation": 2}

# Which applicant-tracking systems actually rank applicants. A resume seeded with
# exact keywords helps on the first two and does close to nothing on the third.
# Kept in lockstep with job-hunter's core/parsers.py RANKS_* sets: a company
# scored "hard" in one repo and "soft" in the other would condition a resume
# score on a reader the dock says does not exist. oracle/paylocity/rippling
# were added when discovery learned those ATS families.
RANKS_HARD = {"workday", "icims", "taleo", "successfactors", "adp", "paycom",
              "oracle"}
RANKS_SOFT = {"greenhouse", "lever", "ashby", "smartrecruiters", "jazzhr",
              "workable", "recruitee", "jobvite", "bamboohr", "breezy",
              "rippling", "paylocity"}


def ats_tier(kind: str | None) -> str:
    k = (kind or "").lower()
    return "hard" if k in RANKS_HARD else "soft" if k in RANKS_SOFT else "none"


def board_url(c: dict) -> str | None:
    """Where a person can go look themselves. Matters most where extraction fails."""
    a = c.get("ats") or {}
    kind, ref = a.get("type"), a.get("ref")
    if isinstance(ref, str) and ref.startswith("http"):
        return ref
    pat = {"greenhouse": "https://job-boards.greenhouse.io/{r}",
           "lever": "https://jobs.lever.co/{r}",
           "ashby": "https://jobs.ashbyhq.com/{r}",
           "breezy": "https://{r}.breezy.hr",
           "recruitee": "https://{r}.recruitee.com",
           "workable": "https://apply.workable.com/{r}",
           "jazzhr": "https://{r}.applytojob.com/apply",
           "bamboohr": "https://{r}.bamboohr.com/careers",
           "rippling": "https://ats.rippling.com/{r}/jobs",
           "smartrecruiters": "https://careers.smartrecruiters.com/{r}",
           "icims": "https://{r}.icims.com/jobs/search?ss=1"}
    if kind in pat and isinstance(ref, str):
        return pat[kind].format(r=ref)
    return c.get("website")


# Some companies belong here for a slice of what they do. Anthropic and OpenAI
# sell into state and local government, but their boards are 500 and 750
# postings of research and infrastructure, and importing all of it would bury
# the market this board is about under a horizontal company's headcount.
#
# This is NOT the same as Palantir or Verkada carrying engineering reqs. Those
# ARE govtech companies, and what they are building is real signal about where
# they are. A horizontal vendor's ML researcher is not.
#
# So: `sled_only: true` on a company keeps the roles that name the public sector
# and drops the rest. The company still appears with an honest count.
SLED_ROLE = re.compile(
    r"public[- ]sector|state (and|&) local|SLED|gov(ernment|tech)?|"
    r"civic|municipal|federal|public safety|K-?12|higher[- ]ed", re.I)

# ...but not another country's public sector. "Account Executive - Public
# Sector (ASEAN)" and "Account Director, Public Sector - Tokyo" both matched,
# and neither is this market. This board is US state and local.
NOT_OUR_GOV = re.compile(
    r"\bASEAN\b|\bEMEA\b|\bAPAC\b|\bLATAM\b|\bUK\b|Tokyo|Japan|Singapore|"
    r"Korea|India|Australia|Canada|Germany|France|Netherlands|Nordics|Ireland|"
    r"Brazil|Mexico|Dubai|\bUAE\b|Israel", re.I)


def phase(families: dict) -> str:
    """What the family mix says about where a company is."""
    total = sum(families.values())
    if total < 4:
        return "too few openings to read"
    gtm = families.get("gtm", 0)
    build = families.get("engineering", 0) + families.get("product", 0) + \
        families.get("data", 0)
    absorb = families.get("cs", 0) + families.get("field", 0)
    if build > gtm * 1.5:
        return "building: hiring mostly engineers and product"
    if gtm > build * 1.5:
        return "selling: a go-to-market push"
    if absorb > gtm:
        return "absorbing: delivery and support for customers already won"
    return "mixed: building and selling at once"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--company")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-render", action="store_true",
                    help="skip the browser fallback even if Playwright is installed")
    ap.add_argument("--write-partial", action="store_true",
                    help="allow a --limit/--company run to overwrite the full board")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--render-budget", type=float, default=900,
                    help="seconds to spend on the browser fallback in total "
                         "(default 900). Rendering is sequential and costs "
                         "~27s a page, so an uncapped render phase can run "
                         "longer than the entire parallel fetch.")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    if a.company:
        companies = [c for c in companies if c["id"] == a.company]
    if a.limit:
        companies = companies[:a.limit]

    today = dt.date.today().isoformat()
    postings, orgs, unreadable, rendered = [], [], 0, 0

    # Fetch in parallel. Sequentially, 362 boards plus renders took 71 minutes,
    # which does not fit a daily job. Most of that is waiting on sockets, so
    # concurrency is the whole fix. Rendering stays sequential afterwards: it is
    # heavy, and only a handful of pages need it.
    def read_board(c):
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        if kind in (None, "unknown") or ref is None:
            return c, [], None, True, False       # nothing on file to read
        try:
            if kind == "html" and isinstance(ref, str):
                return c, ats.fetch_html_titles(ref), None, True, False
            return c, ats.fetch(c["ats"]), None, True, False
        except Exception as exc:
            return c, [], str(exc)[:60], True, kind == "html"

    render_started = time.monotonic()
    # Two companies pointing at ONE board is not two boards. It happens after an
    # acquisition: both the product and its acquirer end up with the parent's
    # careers URL, and the same postings get counted under both names. Twenty
    # refs were shared this way, double-counting 112 of 704 quota-carrying
    # roles - a 16% inflation of the single number this board exists to report.
    #
    # The board belongs to whoever the slug names. Everyone else sharing it is
    # marked and contributes nothing, rather than silently doubling the total.
    shared: dict[tuple, list] = collections.defaultdict(list)
    for c in companies:
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        if kind in (None, "unknown") or ref is None:
            continue
        shared[(kind, json.dumps(ref, sort_keys=True))].append(c)

    owns: dict[str, str] = {}          # company id -> id of the board's owner
    unowned: set[str] = set()          # holders the slug does not name
    for (kind, ref_json), group in shared.items():
        if len(group) < 2:
            continue
        ref = json.loads(ref_json)
        slug = ref if isinstance(ref, str) else " ".join(str(x) for x in ref)
        norm = lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())
        ns = norm(slug)

        def closeness(c):
            """How much of this company's name the slug accounts for.

            Taking the first name that merely CONTAINS the slug gave the Xplor
            board to "PerfectMind (Xplor Recreation)" over "Xplor Recreation",
            which is the company the slug is actually named after. The slug
            covering more of the name is the better claim.
            """
            n = norm(c["name"])
            if not n or not ns:
                return 0.0
            if n in ns or ns in n:
                return len(ns) / max(len(n), len(ns))
            return 0.0

        holder = max(group, key=closeness)
        unverified = closeness(holder) == 0
        if unverified:
            # Nobody is named by the slug. Three Catalis brands share
            # catalisgov.com and none of them IS Catalis, so whoever holds it
            # holds it arbitrarily. Keep the pick stable and say so, rather
            # than letting an arbitrary attribution look decided.
            holder = group[0]
            unowned.add(holder["id"])
        for c in group:
            if c["id"] != holder["id"]:
                owns[c["id"]] = holder["id"]

    fetched = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(read_board, companies), 1):
            fetched.append(res)
            if i % 250 == 0:
                # flush: redirected stdout is block-buffered, so an unflushed
                # progress line is invisible for the whole run. A 57-minute
                # rebuild looked hung when it was working fine.
                print(f"  fetched {i}/{len(companies)}...", flush=True)

    for c, jobs, err, enumerable, may_render in fetched:
        if c["id"] in owns:
            # Somebody else's board. Keep the company on the list with its real
            # state, and no postings: a shared board is one board.
            jobs, err = [], None
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        no_board = kind in (None, "unknown") or ref is None

        if err and may_render and not a.no_render and render_fetch is not None \
                and render_fetch.available() \
                and time.monotonic() - render_started < a.render_budget:
            try:
                jobs, err = render_fetch.fetch_rendered(ref), None
                rendered += 1
            except Exception:
                jobs = []
            if rendered and rendered % 10 == 0:
                print(f"  rendered {rendered} board(s), "
                      f"{time.monotonic() - render_started:.0f}s spent", flush=True)
        if err and not jobs:
            if kind == "html":
                enumerable = False
                err = None
            else:
                unreadable += 1
        if not jobs and kind == "html" and not err:
            enumerable = False

        fams = collections.Counter()
        kept = 0
        sled_only = bool(c.get("sled_only"))
        dropped_offtopic = 0
        for j in jobs:
            title = (j.get("title") or "").strip()
            if roles.is_junk(title) or roles.is_evergreen(title):
                continue
            if sled_only and (not SLED_ROLE.search(title)
                              or NOT_OUR_GOV.search(title)):
                dropped_offtopic += 1
                continue
            loc = j.get("location") or ""
            fam = roles.family(title)
            terr = roles.territory(loc, title)
            fams[fam] += 1
            kept += 1
            postings.append({
                "id": f"{c['id']}::{title}",
                "company": c["name"], "company_id": c["id"],
                "title": title, "family": fam,
                "quota_carrying": roles.is_quota_carrying(title),
                "seniority": roles.seniority(title),
                "states": terr["states"], "region": terr["region"],
                "work_mode": terr["work_mode"],
                "location": loc, "is_us": roles.is_us(loc, title),
                "url": j.get("url") or board_url(c),
                "sector": c["sector"], "category": c["category"],
                "first_seen": today, "source": "ats",
            })

        orgs.append({
            "id": c["id"], "name": c["name"], "sector": c["sector"],
            "category": c["category"], "location": c.get("location"),
            "year_founded": c.get("year_founded"), "description": c.get("description"),
            "website": c.get("website"), "board_url": board_url(c),
            "ats": kind, "ats_ranks": ats_tier(kind),
            "tier": TIER.get(c["sector"]),
            "vendor_type": c.get("vendor_type"), "govtech": c.get("govtech"),
            "parent": c.get("parent"), "ats_note": c.get("ats_note"),
            "open_roles": kept, "families": dict(fams), "phase": phase(fams),
            "quota_roles": sum(1 for p in postings
                               if p["company_id"] == c["id"] and p["quota_carrying"]),
            "unreadable": err,
            "sled_only": sled_only or None,
            "offtopic_dropped": dropped_offtopic or None,
            "shares_board_with": owns.get(c["id"]),
            "board_owner_unverified": c["id"] in unowned or None,
            # A company with nothing on file has not failed; it has never been
            # tried. Counting 4,137 of those as "unreadable" made a discovery
            # backlog look like a systemic fetch failure.
            "no_board_on_file": no_board,
            "enumerable": enumerable,
        })

    # Merge hand-checked findings. These come from companies the fetchers cannot
    # read at all, so an automated run must never delete them: absence from this
    # run means the fetcher still cannot see the company, not that the role closed.
    # Only `manual.py none` closes a manual posting.
    manual_path = DATA / "manual.json"
    manual_count = 0
    if manual_path.exists():
        man = json.loads(manual_path.read_text())
        checks = man.get("checks", {})
        by_id = {o["id"]: o for o in orgs}
        for mp in man.get("postings", []):
            postings.append({**mp, "source": "manual"})
            manual_count += 1
            o = by_id.get(mp["company_id"])
            if o is not None:
                o["open_roles"] += 1
                o["families"][mp["family"]] = o["families"].get(mp["family"], 0) + 1
                if mp.get("quota_carrying"):
                    o["quota_roles"] += 1
                o["phase"] = phase(o["families"])
        for org in orgs:
            chk = checks.get(org["id"])
            if chk:
                org["checked_by_hand"] = chk.get("checked_on")

    # carry first_seen forward so a posting keeps its original date
    prev_path = DATA / "board.json"
    if prev_path.exists():
        prev = {p["id"]: p for p in json.loads(prev_path.read_text()).get("postings", [])}
        for p in postings:
            if p["id"] in prev:
                p["first_seen"] = prev[p["id"]]["first_seen"]

    for mp in postings:
        if "seniority" not in mp:            # manual entries predate these fields
            t = roles.territory(mp.get("location", ""), mp.get("title", ""))
            mp.update(seniority=roles.seniority(mp.get("title", "")),
                      states=t["states"], region=t["region"], work_mode=t["work_mode"])

    fam_totals = collections.Counter(p["family"] for p in postings)
    sector_totals = collections.Counter(p["sector"] for p in postings)
    payload = {
        "generated": today,
        "companies_read": len(companies), "unreadable": unreadable,
        "rendered": rendered,
        "no_board_on_file": sum(1 for o in orgs if o.get("no_board_on_file")),
        "manual_postings": manual_count,
        "totals": {
            "postings": len(postings),
            "quota_carrying": sum(1 for p in postings if p["quota_carrying"]),
            "us": sum(1 for p in postings if p["is_us"] is True),
            "non_us": sum(1 for p in postings if p["is_us"] is False),
            "families": dict(fam_totals), "sectors": dict(sector_totals),
        },
        "organizations": orgs,
        "postings": postings,
    }

    if owns:
        by_holder = collections.Counter(owns.values())
        print(f"{len(owns)} company(ies) share a board with another and were not "
              f"counted twice:")
        names = {c["id"]: c["name"] for c in companies}
        for follower, holder in sorted(owns.items())[:8]:
            print(f"   {names.get(follower, follower)[:30]:<30} -> "
                  f"{names.get(holder, holder)[:30]}")
        if len(owns) > 8:
            print(f"   ... and {len(owns) - 8} more")
        if unowned:
            print(f"   ({len(unowned)} of those boards are held arbitrarily: the slug "
                  f"names none of the companies sharing it)")

    filtered = [o for o in orgs if o.get("offtopic_dropped")]
    if filtered:
        tot = sum(o["offtopic_dropped"] for o in filtered)
        print(f"{tot} off-topic posting(s) dropped from {len(filtered)} horizontal "
              f"vendor(s) marked sled_only:")
        for o in sorted(filtered, key=lambda x: -x["offtopic_dropped"])[:6]:
            print(f"   {o['name'][:26]:<26} kept {o['open_roles']:>3}, "
                  f"dropped {o['offtopic_dropped']}")

    no_board = sum(1 for o in orgs if o.get("no_board_on_file"))
    print(f"{len(companies)} companies: {len(companies) - no_board} with a board on "
          f"file, {no_board} awaiting discovery")
    print(f"  {unreadable} boards unreadable, {rendered} recovered by rendering")
    print(f"{len(postings)} open postings, "
          f"{payload['totals']['quota_carrying']} quota-carrying")
    for f, n in fam_totals.most_common():
        print(f"  {n:>4}  {roles.LABEL.get(f, f)}")
    if a.dry_run:
        print("\n(dry run, nothing written)")
        return 0

    # A partial run must never overwrite the full board. --limit and --company are
    # for testing a fetcher, and writing 3 companies over 137 silently destroys the
    # dataset the site reads. Learned the hard way.
    if (a.limit or a.company) and not a.write_partial:
        print("\npartial run, not written. Pass --write-partial to overwrite the "
              "full board on purpose.")
        return 0

    DATA.mkdir(exist_ok=True)
    HISTORY.mkdir(exist_ok=True)
    prev_path.write_text(json.dumps(payload, indent=1) + "\n")
    # snapshot only the ids: enough for repost detection, small enough to keep
    (HISTORY / f"{today}.json").write_text(json.dumps(
        {"date": today, "ids": sorted(p["id"] for p in postings)}, indent=1) + "\n")
    print(f"\nwrote data/board.json and data/history/{today}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
