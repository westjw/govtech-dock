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


# --- WHAT COUNTS AS A RECORD, PER FILE ------------------------------------
#
# snapshot() knows two shapes: a list of dicts keyed by "id", and a dict that
# is already key -> record. Four of the admin's files are neither, which is
# why they were never journalled - and why routing them through
# save_decisions WITHOUT this registry would have been worse than leaving
# them alone:
#
#   task_notes.json   a bare LIST whose rows carry no "id". snapshot keyed
#                     every row None, so four notes collapsed into one entry
#                     and an undo would have restored whichever row won the
#                     collision. amcs-group already has two rows.
#   manual.json       {"checks": {id: rec}, "postings": [rec]} - two
#                     collections in one file, and act_capture writes to
#                     both in a single call.
#   submissions.json  {"items": [rec]}
#   logic_notes.json  {"notes": [rec]}
#
# A shape is a pair. to_records turns the file into key -> record, so diff,
# BLAST, RUNAWAY and plan_undo work per record instead of treating a whole
# file as one opaque value. from_records puts the surviving records back in
# the file's own shape, carrying over any sibling key the action never
# touched.
#
# THE KEY MUST BE STABLE AND UNIQUE. These files are append-only, so a
# natural key repeats: two task notes for one company in the same second key
# alike. A repeat gets #n rather than overwriting, because a record that
# disappears at snapshot time is a record no undo can bring back.


def _keyed(rows, fields, prefix: str = "") -> dict:
    out: dict = {}
    seen: dict = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        base = prefix + "|".join(str(r.get(f) or "") for f in fields)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out[base if not n else f"{base}#{n}"] = r
    return out


def _wrapped(key: str, fields: list):
    """A file whose records live one level down, under a single key."""
    def to_records(payload):
        d = payload if isinstance(payload, dict) else {}
        return _keyed(d.get(key) or [], fields)

    def from_records(original, records):
        d = dict(original) if isinstance(original, dict) else {}
        d[key] = list(records.values())
        return d
    return to_records, from_records


def _manual_to(payload) -> dict:
    d = payload if isinstance(payload, dict) else {}
    out = {f"checks|{cid}": rec
           for cid, rec in (d.get("checks") or {}).items()}
    out.update(_keyed(d.get("postings") or [], ["id"], prefix="postings|"))
    return out


def _manual_from(original, records):
    d = dict(original) if isinstance(original, dict) else {}
    checks: dict = {}
    postings: list = []
    for k, rec in records.items():
        if k.startswith("checks|"):
            checks[k.split("|", 1)[1]] = rec
        elif k.startswith("postings|"):
            postings.append(rec)
    d["checks"], d["postings"] = checks, postings
    return d


SHAPES = {
    # a note is one company's answer on one kind; "at" is what separates a
    # correction from the note it corrects, both of which are real records
    "task_notes.json": (
        lambda p: _keyed(p if isinstance(p, list) else [],
                         ["company_id", "kind", "at"]),
        lambda o, r: list(r.values())),
    "manual.json": (_manual_to, _manual_from),
    "submissions.json": _wrapped("items", ["id"]),
    "logic_notes.json": _wrapped("notes", ["queue", "id", "at"]),
}


def snapshot(payload, name: str = "") -> dict:
    """key -> record, for any shape of file we store.

    companies.json is a list keyed by id; the decision files are already
    dicts. Everything below works on the dict form so one code path covers
    both. A file in SHAPES brings its own reader, reached by NAME - the
    payload alone cannot say which file it came from, which is the reason
    those four files went unjournalled for as long as they did.
    """
    shape = SHAPES.get(name)
    if shape:
        return shape[0](payload)
    if isinstance(payload, list):
        return {c.get("id"): c for c in payload if isinstance(c, dict)}
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def diff(before, after, name: str = "") -> dict:
    """{key: {"before": ..., "after": ...}} for everything that moved."""
    b, a = snapshot(before, name), snapshot(after, name)
    out = {}
    for k in set(b) | set(a):
        if b.get(k) != a.get(k):
            out[k] = {"before": b.get(k), "after": a.get(k)}
    return out


# READ ONCE PER FILE STATE, NOT ONCE PER CALLER.
#
# _entries() re-read and re-parsed 25 MB from disk on every call, and nothing
# in the process remembered it. Measured 2026-09-18: one /api/triage - the
# admin's Start tab, the first thing that draws - takes 2,979 ms, of which
# 1,649 ms (55%) is EIGHT separate full reads of the same unchanged file in
# one request. sessions(), reversals(), rulings_by_queue() via unlocks(),
# receipt() three times, and two more from the queue builders through
# founded_provenance(). None of them writes; all eight parse the same bytes.
#
# The fingerprint is (st_mtime_ns, st_size), so a write by ANOTHER process -
# a CLI ruling while the server is up - is seen. It is a heuristic rather
# than a guarantee: two writes inside one mtime tick landing on the same byte
# count would be missed. APFS gives nanosecond mtimes and journal entries
# differ in length, so that is not reachable in practice, and the fallback if
# it ever were is a stale READ, never a lost write - every writer goes
# through _write_entries, which invalidates.
_CACHE: tuple | None = None


def _fingerprint() -> tuple | None:
    try:
        st = LOG.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _entries() -> list[dict]:
    """Every journal entry, oldest first. The list is a copy; the dicts are not.

    admin_undo.py:112 mutates the dicts it gets back (`r["undo_of"] = ...`)
    and immediately writes them through _write_entries, so sharing them is
    correct there - the mutation is meant to be persisted. A caller that
    mutates an entry WITHOUT writing it back would silently change what every
    later reader sees in this process. Don't; copy the entry first.
    """
    global _CACHE
    if not LOG.exists():
        _CACHE = None
        return []
    fp = _fingerprint()
    if _CACHE is not None and _CACHE[0] == fp:
        return list(_CACHE[1])
    rows = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue          # a torn line is not a reason to lose the rest
    # the fingerprint is taken AFTER the read: if the file moved under us,
    # the next call re-reads rather than trusting a list from a state we
    # never actually saw whole
    _CACHE = (_fingerprint(), rows)
    return list(rows)


ARCHIVE = DATA / "admin_journal.archive.jsonl"


def _write_entries(rows: list[dict]) -> None:
    global _CACHE
    # WHAT IS PRUNED IS ARCHIVED, NOT DROPPED. The journal is the ONLY record
    # of boards/websites/duplicates/founded rulings and of every before-image,
    # and it kept 500 entries: every journal-only ruling from 2026-08-28 to
    # 09-08 was already gone, and the 09-08 sitting of 101 rulings would have
    # followed after ~500 more writes - "your best sitting" silently falling
    # and the undo for those rulings unrecoverable. Pruned rows go to an
    # append-only archive; the live file stays small for the reads that
    # happen on every admin request.
    pruned = rows[:-KEEP] if len(rows) > KEEP else []
    if pruned:
        with open(ARCHIVE, "a") as fh:
            for r in pruned:
                fh.write(json.dumps(r) + "\n")
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        for r in rows[-KEEP:]:
            fh.write(json.dumps(r) + "\n")
    os.replace(tmp, LOG)
    # INVALIDATE, do not refresh from `rows`. Rebuilding the cache from what
    # we meant to write would make the cache authoritative over the file, and
    # the one thing this file must never do is disagree with its own disk.
    #
    # BELT, NOT BRACES, AND MEASURED AS SUCH: removing this line does not fail
    # check_the_journal_is_read_once_per_file_state, because os.replace moves
    # the fingerprint and the next read re-parses anyway. It is here for the
    # one case the fingerprint cannot see - a rewrite landing on the same
    # (mtime_ns, size) - and it is deliberately not claimed as guarded.
    _CACHE = None


def next_id(rows: list[dict] | None = None) -> str:
    """The next free id for today. Never one already in the file.

    This counted today's entries and added one, which collides the moment the
    prune drops an early entry for the same day: the count falls, the number
    is handed out again, and admin_undo --undo takes whichever the lookup
    finds first. data/admin_journal.jsonl carries 2026-09-13#5 TWICE today -
    two different rulings under one id, one of which cannot be addressed.
    """
    rows = _entries() if rows is None else rows
    today = dt.date.today().isoformat()
    used = {str(r.get("id", "")) for r in rows}
    n = sum(1 for i in used if i.startswith(today)) + 1
    while f"{today}#{n}" in used:
        n += 1
    return f"{today}#{n}"


def check(name: str, before, after, force: bool = False) -> tuple[dict, str | None]:
    """Look at what an action would change. Returns (changes, refusal)."""
    changes = diff(before, after, name)
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
    was = snapshot(before, name)
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
    cur = snapshot(current, entry.get("file") or "")
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


def as_payload(original, restored: dict, name: str = ""):
    """Put a restored key->record map back into the file's own shape."""
    shape = SHAPES.get(name)
    if shape:
        return shape[1](original, restored)
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
