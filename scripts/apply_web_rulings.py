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


def _pending(name: str) -> dict:
    """Opinions from the web that the daily run has not applied yet."""
    path = DATA / name
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: r for k, r in rows.items()
            if isinstance(r, dict) and r.get("applied") is False}


def apply_founded(dry: bool) -> int:
    """A confirmed founding year, from the web.

    One field, and the year was already on the card, so the only thing that
    can go wrong is a company that has since been merged away. That fails
    loudly and stays pending rather than being dropped.
    """
    pending = _pending("web_founded_rulings.json")
    if not pending:
        return 0
    rows = json.loads((DATA / "web_founded_rulings.json").read_text())
    companies = admin.read_companies()
    by_id = {c["id"]: c for c in companies}
    done, failed = [], []
    for cid, r in pending.items():
        c = by_id.get(cid)
        if c is None:
            failed.append((cid, "no such company - merged away since the ruling"))
            continue
        was = c.get("year_founded")
        c["year_founded"] = int(r["year"])       # an int, like every other year on the map
        err = admin.validate(companies)
        if err:
            c["year_founded"] = was
            failed.append((cid, err))
            continue
        done.append((cid, was, r["year"]))
        rows[cid]["applied"] = True
    print(f"founding years: {len(done)} applied, {len(failed)} left pending")
    for cid, was, now in done[:8]:
        print(f"  {cid}: {was or 'unknown'} -> {now}")
    for cid, why in failed[:5]:
        print(f"  PENDING {cid}: {why}")
    if dry or not done:
        return 0
    bad = admin.save_companies(companies, "apply-web-founded",
                               f"{len(done)} founding year(s) from the web admin",
                               by="owner")
    if bad:
        print(f"refused: {bad}")
        return 1
    admin.write_atomic("web_founded_rulings.json", rows)
    return 0


def apply_merges(dry: bool) -> int:
    """A merge ruled from the web, applied here through act_merge.

    NOT reimplemented: act_merge is where "a merge never loses research"
    actually lives - the survivor inherits what it lacks, a discovered ATS
    beats an unknown one, the dropped name becomes an alias, and `also`
    placements union. A second copy of that logic in this file would drift
    from the first one, and the merge is the ruling this project can least
    afford to get quietly wrong.
    """
    pending = _pending("web_merge_rulings.json")
    if not pending:
        return 0
    rows = json.loads((DATA / "web_merge_rulings.json").read_text())
    done, failed = [], []
    if dry:
        # A DRY RUN THAT MERGES IS NOT A DRY RUN. act_merge writes
        # companies.json itself, so calling it here and skipping only the
        # bookkeeping at the end left --dry-run performing every merge for
        # real - caught when the second pass reported "company not found" for
        # a record the first pass had already folded away.
        by_id = {c["id"] for c in admin.read_companies()}
        for key, r in pending.items():
            missing = [x for x in (r["keep"], r["drop"]) if x not in by_id]
            if missing:
                failed.append((key, f"not on file: {', '.join(missing)}"))
            else:
                done.append((key, f"would merge {r['drop']} into {r['keep']}"))
        print(f"merges: {len(done)} would apply, {len(failed)} would stay pending")
        for _k, msg in done[:8]:
            print(f"  {msg}")
        for k, why in failed[:5]:
            print(f"  PENDING {k}: {why}")
        return 0
    for key, r in pending.items():
        # act_merge reads and writes companies.json itself, one merge at a
        # time, journalled. Slower than batching and correct: a refused merge
        # leaves the others alone instead of taking the batch down with it.
        out = admin.act_merge({"keep": r["keep"], "drop": r["drop"],
                               "why": r.get("why") or "",
                               "by": r.get("by") or "owner"})
        if out.get("error"):
            failed.append((key, out["error"]))
            continue
        done.append((key, out.get("message", "")))
        rows[key]["applied"] = True
    print(f"merges: {len(done)} applied, {len(failed)} left pending")
    for key, msg in done[:8]:
        print(f"  {msg}")
    for key, why in failed[:5]:
        print(f"  PENDING {key}: {why}")
    if not done:
        return 0
    admin.write_atomic("web_merge_rulings.json", rows)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rc = apply_founded(a.dry_run) or apply_merges(a.dry_run)

    path = DATA / "placement_rulings.json"
    if not path.exists():
        print("no placement rulings on file")
        return rc
    rulings = json.loads(path.read_text())
    pending = {cid: r for cid, r in rulings.items()
               if isinstance(r, dict) and r.get("applied") is False}
    if not pending:
        print("no placement rulings pending")
        return rc

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
