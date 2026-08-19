#!/usr/bin/env python3
"""Merge an expanded company list without losing work already done.

A wider list is worth taking, but taking it wholesale would discard every ATS
discovery made since: seven boards found by hand or by rendering would revert to
"unknown" and silently stop being monitored. Whichever entry knows more about a
company's board wins, per field, rather than whole records replacing each other.

Precedence, in order:
  1. our ATS block, when we have a real one and the incoming entry says unknown
  2. the incoming entry for everything else, since it is the researched source
  3. our hiring state, which the incoming list does not carry

  python scripts/merge_companies.py <incoming.json> [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

REAL_ATS = lambda a: (a or {}).get("type") not in (None, "unknown", "")  # noqa: E731

# Fields the incoming list adds. Kept so the extra research is not thrown away.
CARRY = ("govtech", "researched", "source", "vendor_type")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("incoming")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    incoming = json.loads(pathlib.Path(a.incoming).read_text())
    # Suppliers, equipment vendors and associations sell INTO government but are
    # not govtech products. They are worth cataloguing and are not what a SLED
    # software job board monitors, so they live in their own file and only the
    # govtech set drives the board.
    incoming = [c for c in incoming if c.get("govtech")]
    current = json.loads((DATA / "companies.json").read_text())
    by_name = {c["name"].lower(): c for c in current}

    merged, kept_ats, kept_hiring, new_count = [], 0, 0, 0
    seen_ids: set[str] = set()

    for inc in incoming:
        ours = by_name.get(inc["name"].lower())
        entry = dict(inc)
        if ours:
            # Our board discovery outranks an incoming "unknown": losing it would
            # stop monitoring a company that is currently monitored.
            if REAL_ATS(ours.get("ats")) and not REAL_ATS(inc.get("ats")):
                entry["ats"] = ours["ats"]
                kept_ats += 1
            # Hiring state is ours; the incoming list has never fetched a board.
            if ours.get("hiring") and ours["hiring"].get("status") != "Unknown":
                entry["hiring"] = ours["hiring"]
                kept_hiring += 1
            for f in ("location", "year_founded", "description"):
                if not entry.get(f) and ours.get(f):
                    entry[f] = ours[f]
        else:
            new_count += 1
        entry.setdefault("hiring", {"status": "Unknown", "note": "not yet checked",
                                    "roles": [], "checked": None})
        # ids must stay unique: the board keys postings on "<id>::<title>".
        base = entry.get("id") or entry["name"].lower().replace(" ", "-")
        uid, n = base, 2
        while uid in seen_ids:
            uid, n = f"{base}-{n}", n + 1
        entry["id"] = uid
        seen_ids.add(uid)
        merged.append(entry)

    dropped = [c["name"] for c in current if c["name"].lower()
               not in {i["name"].lower() for i in incoming}]

    t = collections.Counter((c.get("ats") or {}).get("type") for c in merged)
    fetchable = sum(n for k, n in t.items() if k not in (None, "unknown"))
    print(f"{len(merged)} companies ({new_count} new, {len(current)} were tracked)")
    print(f"  preserved {kept_ats} ATS discovery(ies) the incoming list had as unknown")
    print(f"  preserved {kept_hiring} known hiring state(s)")
    print(f"  {fetchable} fetchable, {t.get('unknown', 0) + t.get(None, 0)} need discovery")
    if dropped:
        print(f"  WARNING: {len(dropped)} currently-tracked companies are absent from "
              f"the incoming list and would be dropped:")
        for d in dropped[:10]:
            print(f"     {d}")

    sectors = collections.Counter(c.get("sector") for c in merged)
    print("\n  by sector:")
    for s, n in sectors.most_common():
        print(f"    {n:>5}  {s}")

    if not a.write:
        print("\ndry run. re-run with --write to replace data/companies.json")
        return 0
    if dropped:
        print("\nrefusing to write while tracked companies would be dropped.",
              file=sys.stderr)
        return 1
    (DATA / "companies.json").write_text(json.dumps(merged, indent=2) + "\n")
    print(f"\nwrote data/companies.json ({len(merged)} companies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
