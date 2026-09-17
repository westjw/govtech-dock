#!/usr/bin/env python3
"""What a company sells, and who buys it. Two dimensions, both multi-valued.

    python3 scripts/tags.py                 # the spread, over both registries
    python3 scripts/tags.py --id tyler-technologies

WHY DERIVED AND NEVER STORED. `sells` is already on every record as
`vendor_type`, and `buyers` is already there as the buyer verdict. Writing a
`tags` list onto each company would be a second copy of both, kept in step by
hand - which is the failure this repo names as "two databases with a sync
produce drift, and drift makes every downstream number a lie". So the tags are
computed here, from the fields that already exist, and there is one writer per
fact. To change a tag you change the fact underneath it, and that fact is
already gated: `sled` moves when the buyer pass answers, not when somebody
edits a label.

It is the same call `buyer_sled_eligible` makes and for the same reason -
"DERIVED HERE, ASSERTED NOWHERE".

THE VOCABULARY IS IN schema.json, not in this file. Sectors and categories
live there because a name the schema does not hold files a company nowhere;
a tag is the same kind of thing. This module reads it.

TWO VALUES ARE DELIBERATELY UNASSIGNED AND THAT IS NOT AN OVERSIGHT.
`federal` is out of scope for this board (CLAUDE.md, owner 2026-09-12) and
nothing in the dataset establishes it anyway. `private` needs
`names_other_buyers`, which is null on all 366 answered records - so there is
no page to read it off, and inventing it would be the one thing this project
refuses. They stay in the vocabulary because the question is real; they stay
empty because the evidence is not here.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def vocabulary() -> dict:
    """The tag vocabulary as schema.json holds it."""
    return json.loads((DATA / "schema.json").read_text()).get("tags") or {}


def tags_for(rec: dict, vocab: dict | None = None) -> list[str]:
    """Every tag this record's own fields support, sorted. No invention.

    A record whose vendor_type the vocabulary does not hold gets NO sells tag
    rather than a guessed one - the same rule `validate()` applies to a sector
    the schema does not know.
    """
    v = vocab if vocab is not None else vocabulary()
    out = []
    sells = (v.get("sells") or {}).get(rec.get("vendor_type") or "")
    if sells:
        out.append(sells)
    # A `yes` is a claim about what their own pages say, and it is the only
    # buyer verdict that supports a tag. `no` and `unclear` are answers, and
    # neither of them says "sled".
    if rec.get("sells_to_gov") == "yes":
        out.append("sled")
    return sorted(set(out))


def spread(records: list, vocab: dict | None = None) -> dict:
    """{tag: count} plus how many records carry nothing at all."""
    import collections
    v = vocab if vocab is not None else vocabulary()
    n = collections.Counter()
    bare = 0
    for r in records:
        t = tags_for(r, v)
        if not t:
            bare += 1
        n.update(t)
    return {"tags": dict(n.most_common()), "untagged": bare,
            "records": len(records)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", help="one company or supplier")
    a = ap.parse_args()
    v = vocabulary()
    cos = json.loads((DATA / "companies.json").read_text())
    sup = json.loads((DATA / "suppliers.json").read_text())
    if a.id:
        rec = next((r for r in cos + sup if r.get("id") == a.id), None)
        if rec is None:
            print(f"no record {a.id!r}", file=sys.stderr)
            return 2
        print(f"{rec['name']}  vendor_type={rec.get('vendor_type')!r} "
              f"sells_to_gov={rec.get('sells_to_gov')!r}")
        print(f"  tags: {tags_for(rec, v) or '(none its fields support)'}")
        return 0
    for label, rows in (("companies", cos), ("suppliers", sup)):
        s = spread(rows, v)
        print(f"{label}: {s['records']:,} records, {s['untagged']:,} carry no tag")
        for t, n in s["tags"].items():
            print(f"   {t:18} {n:>6,}")
    unassigned = [k for k in (v.get("buyers") or {}) if k != "sled"]
    print(f"\nin the vocabulary and assigned to nothing: {unassigned} "
          f"- see this module's docstring for why")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
