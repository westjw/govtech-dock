#!/usr/bin/env python3
"""Catalogue the suppliers, vendors and associations that are not govtech.

They sell into government and belong in the market map, but they are not
software companies and are not what a SLED job board monitors. Keeping them in
their own file means the board stays about govtech while nothing is thrown away,
and turning monitoring on for them later needs no rework: the record shape is
identical.

  python scripts/build_suppliers.py
"""
from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def main() -> int:
    rows = json.loads((DATA / "suppliers.json").read_text())
    by_type = collections.Counter(r.get("vendor_type") for r in rows)
    by_sector = collections.Counter(r.get("sector") for r in rows)
    payload = {
        "generated": __import__("datetime").date.today().isoformat(),
        "total": len(rows),
        "with_website": sum(1 for r in rows if r.get("website")),
        "vendor_types": dict(by_type),
        "sectors": dict(by_sector),
        "suppliers": [{"id": r["id"], "name": r["name"], "sector": r.get("sector"),
                       "category": r.get("category"),
                       "vendor_type": r.get("vendor_type"),
                       "description": r.get("description"),
                       "website": r.get("website"),
                       "source": r.get("source")} for r in rows],
    }
    (DATA / "suppliers_view.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(f"{len(rows)} suppliers catalogued, "
          f"{payload['with_website']} with a website")
    for k, n in by_type.most_common():
        print(f"  {n:>5}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
