#!/usr/bin/env python3
"""Make every admin write reversible, and every bulk write one unit.

WHAT THIS DEFENDS AGAINST

write_atomic() already guarantees a write is never PARTIAL, and validate()
guarantees companies.json is never STRUCTURALLY invalid. Neither of those is
the failure this repo actually fears.

The failure this repo fears is a write that is complete, valid, and wrong.
validate() checks that "Cybersecurity" is a real category; it cannot check
that this company belongs in it. One click on "All out" writes a ruling for
108 companies, and all 108 pass every check we have.

Three things make that worse than an ordinary bug:

  1. Rulings are stored in the decision files, not companies.json, and those
     get NO validation at all - write_atomic is the whole of it.
  2. A ruling is never re-asked. act_vendor_scope_all skips any name already
     in the file (`if k in d: continue`), which is correct for a right answer
     and permanent for a wrong one.
  3. A wrong "out of scope" is INVISIBLE. The company stops appearing, nothing
     errors, no count looks odd, and there is no moment at which you would
     think to go and look. That is the asymmetric error the whole project is
     built around: a false negative silently deletes a real opportunity.

So the protection is not more validation - there is nothing left to validate.
It is a before-image, kept for every write, so that a wrong answer is
recoverable and a silent one becomes visible.

WHAT IT DOES

- Records the BEFORE state of exactly the records an action touched (a diff,
  not a copy of the file), with who did it, when, and why.
- Treats a bulk action as ONE entry, so undoing it restores all 108 or none.
- Refuses an action that touches more than BLAST records unless the caller
  passes force=True having shown the person the count. A cap that silently
  truncated would be worse than no cap.
- Refuses to undo a record something else has changed since, and names it,
  because undoing out of order quietly reverts newer work.

It does NOT stop you making a wrong ruling. Nothing can. It makes the wrong
ruling cost a minute instead of being permanent and unnoticed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "admin_journal.jsonl"

# How many records one action may touch before it has to be confirmed. The
# theme buttons legitimately rule 108 vendors at once, so this cannot block -
# it forces the caller to have counted out loud first.
BLAST = 25
# Above this share of a whole file, refuse even WITH force. A single admin
# action rewriting a third of the dataset is not a ruling, it is a bug.
#
# Only applied once a file is big enough for a share to mean anything: on a
# decision file with three rulings in it, changing two IS 67% and is also
# completely normal. A percentage gate that fires on small files would block
# the first few rulings of every new queue, which is exactly when someone is
# most likely to give up on the tool.
RUNAWAY = 0.34
RUNAWAY_FLOOR = 60
KEEP = 500          # entries retained; older ones are pruned on write


def snapshot(payload) -> dict:
    """key -> record, for either shape of file we store.

    companies.json is a list keyed by id; the decision files are already
    dicts. Everything below works on the dict form so one code path covers
    both.
    """
    if isinstance(payload, list):
        return {c.get("id"): c for c in payload if isinstance(c, dict)}
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def diff(before, after) -> dict:
    """{key: {"before": ..., "after": ...}} for everything that moved."""
    b, a = snapshot(before), snapshot(after)
    out = {}
    for k in set(b) | set(a):
        if b.get(k) != a.get(k):
            out[k] = {"before": b.get(k), "after": a.get(k)}
    return out


def _entries() -> list[dict]:
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue          # a torn line is not a reason to lose the rest
    return rows


def _write_entries(rows: list[dict]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        for r in rows[-KEEP:]:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, LOG)


def next_id(rows: list[dict] | None = None) -> str:
    rows = _entries() if rows is None else rows
    today = dt.date.today().isoformat()
    n = sum(1 for r in rows if str(r.get("id", "")).startswith(today)) + 1
    return f"{today}#{n}"


def check(name: str, before, after, force: bool = False) -> tuple[dict, str | None]:
    """Look at what an action would change. Returns (changes, refusal)."""
    changes = diff(before, after)
    if not changes:
        return changes, None
    # REWRITING, NOT APPENDING. The share is taken over records that already
    # existed and were changed or removed, against the file as it was. A pure
    # addition is not a rewrite and must not trip this: the first bulk ruling
    # into an empty decision file is 100% of it by the old arithmetic, so
    # "All out" on 108 vendors - the exact scenario this journal was written
    # for - was refused outright, and force could not get past it because
    # force only lifts BLAST. Volume is BLAST's job; this one is about
    # destruction.
    was = snapshot(before)
    total = max(len(was), 1)
    rewritten = sum(1 for c in changes.values() if c["before"] is not None)
    share = rewritten / total
    if total >= RUNAWAY_FLOOR and share > RUNAWAY:
        return changes, (
            f"refusing: this would rewrite {rewritten} of {total} existing "
            f"records in {name} ({share:.0%}). An admin action that rewrites a "
            f"third of a file is a bug, not a ruling. Nothing was written.")
    if len(changes) > BLAST and not force:
        return changes, (
            f"this would change {len(changes)} records in {name}, over the "
            f"limit of {BLAST}. Confirm the count and send it again. "
            f"Nothing was written.")
    return changes, None


def record(name: str, before, after, action: str, by: str = "owner",
           why: str = "", force: bool = False) -> tuple[str | None, str | None]:
    """Journal an about-to-happen write. Returns (entry_id, refusal).

    Call this BEFORE write_atomic and abort the write on a refusal. Writing
    the journal first means the worst case is a journal entry for a write that
    then failed - harmless, and visible - rather than a write with no way back.
    """
    changes, refusal = check(name, before, after, force)
    if refusal:
        return None, refusal
    if not changes:
        return None, None
    rows = _entries()
    entry = {
        "id": next_id(rows),
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "file": name,
        "action": action,
        "by": by or "owner",
        # the why is not decoration: it is what makes a ruling reviewable
        # later, and CLAUDE.md requires every ruling to carry one
        "why": why or "",
        "n": len(changes),
        "changes": changes,
    }
    rows.append(entry)
    _write_entries(rows)
    return entry["id"], None


def find(entry_id: str) -> dict | None:
    for r in reversed(_entries()):
        if r.get("id") == entry_id:
            return r
    return None


def recent(limit: int = 20, day: str | None = None) -> list[dict]:
    rows = _entries()
    if day:
        rows = [r for r in rows if str(r.get("at", "")).startswith(day)]
    return rows[-limit:]


def undone(entry_id: str) -> bool:
    """Has an undo already been journalled for this entry?"""
    return any(r.get("undo_of") == entry_id for r in _entries())


def plan_undo(entry: dict, current) -> tuple[dict, list[str]]:
    """What the file becomes if this entry is reversed, and what has moved on.

    A key whose current value no longer matches what the entry left behind was
    changed by something later. Restoring it would silently revert that newer
    work, so those keys are reported rather than quietly included.
    """
    cur = snapshot(current)
    restored = dict(cur)
    conflicts = []
    for key, ch in (entry.get("changes") or {}).items():
        if cur.get(key) != ch.get("after"):
            conflicts.append(key)
            continue
        if ch.get("before") is None:
            restored.pop(key, None)          # the action created it
        else:
            restored[key] = ch["before"]
    return restored, conflicts


def as_payload(original, restored: dict):
    """Put a restored key->record map back into the file's own shape."""
    if isinstance(original, list):
        # keep the original ordering for the records that survive, then any
        # that the undo brought back
        order = [c.get("id") for c in original if isinstance(c, dict)]
        seen = set()
        out = []
        for cid in order:
            if cid in restored and cid not in seen:
                out.append(restored[cid])
                seen.add(cid)
        for cid, rec in restored.items():
            if cid not in seen:
                out.append(rec)
        return out
    return restored
