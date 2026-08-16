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


def check_company(comp: dict) -> dict:
    """Return {"status", "note", "roles"} for one company."""
    kind = comp["ats"]["type"]
    if kind == "unknown":
        return {"status": "Unknown", "note": "no ATS on file", "roles": [], "skipped": True}
    try:
        jobs = ats.fetch(comp["ats"])
    except ats.AtsError as exc:
        return {"status": "Unknown", "note": str(exc)[:40], "roles": []}
    status, note, roles = classify.rollup(jobs)
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
