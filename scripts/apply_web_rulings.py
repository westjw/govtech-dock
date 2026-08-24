#!/usr/bin/env python3
"""Apply rulings recorded by the web admin to the dataset.

The web admin only APPENDS opinions - a placement ruling says where a
company belongs, it does not move it. This script does the moving, here in
Python where validate() lives, so a web bug can mis-record an opinion but
can never corrupt the map. Runs in the daily workflow after checkout and
before the board build; idempotent, so a ruling applied yesterday is a
no-op today.

Vendor-scope rulings need no applying: the queues read the decision file
directly. Placement rulings carry applied:false until this runs.

  python scripts/apply_web_rulings.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import admin  # noqa: E402  (validate + write_atomic live there)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    path = DATA / "placement_rulings.json"
    if not path.exists():
        print("no placement rulings on file")
        return 0
    rulings = json.loads(path.read_text())
    pending = {cid: r for cid, r in rulings.items()
               if isinstance(r, dict) and r.get("applied") is False}
    if not pending:
        print("nothing pending")
        return 0

    # through read_companies so save_companies has a real before-image to
    # diff against - reading the file directly leaves the journal comparing
    # against whatever the last caller happened to load
    companies = admin.read_companies()
    by_id = {c["id"]: c for c in companies}
    moved, failed = [], []
    for cid, r in pending.items():
        c = by_id.get(cid)
        if c is None:
            failed.append((cid, "no such company"))
            continue
        was = (c["sector"], c["category"])
        c["sector"], c["category"] = r["sector"], r["category"]
        err = admin.validate(companies)
        if err:
            # revert just this one and keep going; the ruling stays pending
            c["sector"], c["category"] = was
            failed.append((cid, err))
            continue
        moved.append((cid, was, (r["sector"], r["category"])))
        r["applied"] = True

    print(f"{len(moved)} applied, {len(failed)} left pending")
    for cid, was, now in moved[:10]:
        print(f"  {cid}: {was[0]}/{was[1]} -> {now[0]}/{now[1]}")
    for cid, why in failed[:5]:
        print(f"  PENDING {cid}: {why}")
    if a.dry_run:
        return 0
    # THROUGH save_companies, NOT write_atomic. This is the daily run that
    # applies every ruling the owner makes from his phone, and it was writing
    # companies.json directly - so those writes had no before-image and no
    # undo, while CLAUDE.md promised every admin write was reversible. A
    # review caught it. The rulings arriving here are exactly the ones a
    # person is most likely to want back: they were made on a small screen,
    # away from the evidence, and applied hours later by a cron job nobody
    # watches.
    bad = admin.save_companies(
        companies, "apply-web-rulings",
        f"{len(moved)} placement ruling(s) from the web admin", by="owner")
    if bad:
        print(f"refused: {bad}")
        print("no rulings were applied; they stay pending for the next run")
        return 1
    admin.write_atomic("placement_rulings.json", rulings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
