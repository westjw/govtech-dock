#!/usr/bin/env python3
"""Look at what the admin changed, and take it back.

  python scripts/admin_undo.py                    what changed recently
  python scripts/admin_undo.py --today            just today
  python scripts/admin_undo.py --show 2026-08-23#4
  python scripts/admin_undo.py --undo 2026-08-23#4
  python scripts/admin_undo.py --reopen 2026-08-23#4

--undo puts the records back exactly as they were.

--reopen is the one that matters for scope rulings. A ruling is never
re-asked: act_vendor_scope_all skips any name already in the file, which is
right for a correct answer and permanent for a wrong one. Reopening deletes
the ruling rather than reversing it, so the company returns to the queue and
gets asked again with fresh eyes. Use it when you are not sure you were right,
which is a different thing from being sure you were wrong.

Undoing is refused when a record has moved on since - restoring it would
silently revert whatever changed it. The conflicting keys are named so you can
decide, rather than being told "no".
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import journal  # noqa: E402

DATA = journal.DATA


def _load(name: str):
    p = DATA / name
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _label(rec) -> str:
    if not isinstance(rec, dict):
        return "-"
    for k in ("name", "call", "status", "sector"):
        if rec.get(k):
            return str(rec[k])[:40]
    return "-"


def show(entry: dict, verbose: bool = False) -> None:
    print(f"{entry['id']}  {entry['at']}")
    print(f"  {entry['action']} on {entry['file']} by {entry['by']}"
          f" - {entry['n']} record(s)")
    if entry.get("why"):
        print(f"  why: {entry['why']}")
    if journal.undone(entry["id"]):
        print("  (already undone)")
    items = list((entry.get("changes") or {}).items())
    for key, ch in items[: (None if verbose else 6)]:
        was, now = ch.get("before"), ch.get("after")
        if was is None:
            print(f"    + {key}  ->  {_label(now)}")
        elif now is None:
            print(f"    - {key}  (was {_label(was)})")
        else:
            print(f"    ~ {key}  {_label(was)}  ->  {_label(now)}")
    if not verbose and len(items) > 6:
        print(f"    ... and {len(items) - 6} more (--show for all)")


def apply(entry: dict, drop: bool, force: bool) -> int:
    """Write the reversal. drop=True deletes the records instead of restoring."""
    current = _load(entry["file"])
    if current is None:
        print(f"{entry['file']} no longer exists; nothing to undo")
        return 1
    restored, conflicts = journal.plan_undo(entry, current)
    if drop:
        restored = dict(journal.snapshot(current))
        for key in entry.get("changes") or {}:
            restored.pop(key, None)
        conflicts = []
    if conflicts and not force:
        print(f"refusing: {len(conflicts)} record(s) changed after this action, "
              f"so putting them back would revert newer work:")
        for k in conflicts[:10]:
            print(f"    {k}")
        if len(conflicts) > 10:
            print(f"    ... and {len(conflicts) - 10} more")
        print("Re-run with --force to restore them anyway.")
        return 1

    payload = journal.as_payload(current, restored)

    # companies.json goes back through the same gate every other write uses.
    # An undo that could write an invalid dataset would be a second way in.
    if entry["file"] == "companies.json":
        import admin
        bad = admin.validate(payload)
        if bad:
            print(f"refusing: the restored file would be invalid - {bad}")
            return 1

    entry_id, refusal = journal.record(
        entry["file"], current, payload,
        action=("reopen" if drop else "undo"), by="owner",
        why=f"{'reopened' if drop else 'undid'} {entry['id']}", force=True)
    # mark it so the listing can say so, and so a double-undo is visible
    if entry_id:
        rows = journal._entries()
        for r in rows:
            if r.get("id") == entry_id:
                r["undo_of"] = entry["id"]
        journal._write_entries(rows)
    if refusal:
        print(refusal)
        return 1

    import admin
    admin.write_atomic(entry["file"], payload)
    n = len(entry.get("changes") or {}) - len(conflicts)
    verb = "reopened" if drop else "restored"
    print(f"{verb} {n} record(s) in {entry['file']}  (journalled as {entry_id})")
    if drop:
        print("Those companies will be asked again the next time you open the queue.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--show", metavar="ID")
    ap.add_argument("--undo", metavar="ID")
    ap.add_argument("--reopen", metavar="ID",
                    help="delete the rulings so the queue asks again")
    ap.add_argument("--last", action="store_true", help="act on the newest entry")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.last:
        rows = journal.recent(1)
        if not rows:
            print("nothing in the journal yet")
            return 0
        a.undo = a.undo or rows[-1]["id"]

    for flag, drop in ((a.undo, False), (a.reopen, True)):
        if not flag:
            continue
        entry = journal.find(flag)
        if not entry:
            print(f"no journal entry {flag}")
            return 1
        show(entry)
        print()
        return apply(entry, drop=drop, force=a.force)

    if a.show:
        entry = journal.find(a.show)
        if not entry:
            print(f"no journal entry {a.show}")
            return 1
        show(entry, verbose=True)
        return 0

    import datetime as dt
    rows = journal.recent(a.limit,
                          day=dt.date.today().isoformat() if a.today else None)
    if not rows:
        print("nothing in the journal yet - no admin writes have been recorded.")
        return 0
    for r in rows:
        show(r)
        print()
    print(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}. "
          f"Undo one with:  python scripts/admin_undo.py --undo <id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
