#!/usr/bin/env python3
"""What fraction of this market can we actually see?

"839 of 1,722 monitored" is the wrong number, in both directions. It counts a
careers page nothing can enumerate the same as a Greenhouse API, and it counts a
company that has no job board at all as a gap to be closed. A field audit of the
companies with no board on file found that roughly 55-63% of them are genuinely
boardless - small SLED vendors hiring on LinkedIn or by email - so no rule
reaches them, because there is nothing to reach.

This reports the honest split instead:

  structured   a real API. Titles, locations, links. This is the number to move.
  page only    a careers page a person can read and a fetcher mostly cannot.
  blocked      a bot wall or a transport error. We learned nothing. NOT a zero.
  absent       checked, and there is no public board. A finished state.
  unchecked    never probed, or probed before the current rules existed.

The denominator that matters is not every company; it is every company that has
a board to find. Against that, coverage is much higher than the raw fraction
suggests - and the remaining gap is honest work rather than a scoreboard.

  python scripts/coverage.py [--by-sector]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Types that return structured postings. `html` is a page scan: it can prove a
# role is there, never that one is not.
STRUCTURED = {"ashby", "greenhouse", "lever", "workable", "recruitee", "breezy",
              "smartrecruiters", "bamboohr", "workday", "rippling", "jazzhr",
              "icims", "jibe", "paylocity", "oracle", "adp"}

# Log notes that mean "we did not read the page", as opposed to "we read it and
# there was nothing". Keeping these apart is the whole point of the file.
BLOCKED_HINTS = ("blocked", "could not fetch", "gave up after", "unreadable",
                 "not a URL")


def state(company: dict, log_entry: dict | None, org: dict | None) -> str:
    kind = (company.get("ats") or {}).get("type")
    if kind in STRUCTURED:
        return "structured"
    if kind == "html":
        return "page only"
    note = (log_entry or {}).get("note", "")
    if (log_entry or {}).get("retry_soon") or any(h in note for h in BLOCKED_HINTS):
        return "blocked"
    if note.startswith("no board found"):
        return "absent"
    return "unchecked"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-sector", action="store_true")
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    log = json.loads((DATA / "discovery_log.json").read_text()) \
        if (DATA / "discovery_log.json").exists() else {}
    board = json.loads((DATA / "board.json").read_text()) \
        if (DATA / "board.json").exists() else {}
    orgs = {o["id"]: o for o in board.get("organizations", [])}

    rows = [(c, state(c, log.get(c["id"]), orgs.get(c["id"]))) for c in companies]
    counts = collections.Counter(s for _, s in rows)
    total = len(rows)

    producing = sum(1 for c, s in rows
                    if orgs.get(c["id"], {}).get("open_roles", 0) > 0)

    print(f"{total} govtech companies\n")
    order = ["structured", "page only", "blocked", "absent", "unchecked"]
    width = max(len(k) for k in order)
    for k in order:
        n = counts.get(k, 0)
        bar = "#" * round(n / total * 46)
        print(f"  {k:<{width}}  {n:>5}  {n / total * 100:>4.1f}%  {bar}")

    # A company with no board is not a gap. Excluding the ones we have checked
    # and found boardless is the difference between a coverage number that can
    # be worked and one that can only be apologised for.
    reachable = total - counts.get("absent", 0)
    seen = counts.get("structured", 0) + counts.get("page only", 0)
    print(f"\n  {producing} companies currently show at least one open posting")
    print(f"\n  Against every company:            "
          f"{seen}/{total} = {seen / total * 100:.0f}%")
    if reachable:
        print(f"  Against those with a board to find: "
              f"{seen}/{reachable} = {seen / reachable * 100:.0f}%")
    print(f"\n  The number to move is 'structured' ({counts.get('structured', 0)}). "
          f"'page only' is a\n  worklist for the capture bookmarklet, not coverage.")
    if counts.get("blocked"):
        print(f"  'blocked' ({counts['blocked']}) is not a zero - those probes "
              f"learned nothing and\n  requeue in 7 days.")

    if a.by_sector:
        print()
        by = collections.defaultdict(collections.Counter)
        for c, s in rows:
            by[c["sector"]][s] += 1
        for sec in sorted(by, key=lambda x: -sum(by[x].values())):
            cc = by[sec]
            tot = sum(cc.values())
            print(f"  {sec:<20} {tot:>4}  "
                  + "  ".join(f"{k}:{cc.get(k, 0)}" for k in order))
    return 0


if __name__ == "__main__":
    sys.exit(main())
