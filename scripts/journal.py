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

import contextlib
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
import pathlib
import re
import struct
import sys
import tempfile
import threading
import time
import zlib

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
# THE FIELDS OF A COMPANY THE ADMIN OWNS: what selftest's
# check_journal_matches_reality holds companies.json to (the last after-image
# of each company against the record), and so what the desk publisher's own
# merge entry must never vouch for unless a side journalled it - the only
# guard against a write no entry holds is that check. One list, read by both.
OWNER_FIELDS = ("brands", "also_known_as", "parent", "board_owner", "year_founded",
                "location", "website", "sector", "category", "description")
# THE DESK PUBLISHER'S OWN ID SPACE: its merge entries are numbered
# `<day>#publish-<n>`, never `<day>#<n>`. It wrote them in the plain space from
# the merged journal while the desk went on numbering its own: the desk's next
# ruling took the same id (2026-11-26#10 twice on main, 2026-11-25 third
# review), and find / admin_undo reach only one of two entries under one id.
PUBLISH_SPACE = "publish-"

# THE IDS THIS PROCESS WROTE, so the desk admin can say which entries a
# ruling made without re-reading a 30 MB file twice per request: do_POST
# clears it before an action and hands what is here back with the reply, and
# the shift's Undo sends those ids to act_undo (2026-10-23).
WRITTEN: list[str] = []
# True while the desk admin runs one request (do_POST). Every entry it writes
# is marked `desk`, and every entry after the first carries the first's id as
# `same_ruling_as`, so one click that writes two files counts as one ruling.
DESK = False
# KEYS A BATCH UNDO IS TAKING BACK OUT: records that batch itself added
# (desk.act_batch_undo). Removing them is the reverse of an addition, and an
# addition never counted toward RUNAWAY, so neither may its undo - otherwise
# a batch of 1,811 supplier rulings could be written and never taken back,
# because removing them is 90% of the file (2026-11-01). Set only for the
# length of one record() call, and it lifts RUNAWAY for exactly these keys.
# Set ONLY through undoing_adds(), which cannot leave it set (a refused undo
# that left it would hand the exemption to the next request's write).
UNDOING_ADDS: set = set()


@contextlib.contextmanager
def undoing_adds(keys):
    global UNDOING_ADDS
    UNDOING_ADDS = set(keys or ())
    try:
        yield
    finally:
        UNDOING_ADDS = set()


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


def _write_entries(rows: list[dict]) -> None:
    """Write the live journal as exactly these rows, archiving what it prunes.

    Under journal_lock(): a caller that reads the journal to write it back -
    record(), admin_undo - holds the lock across the read too, so a second
    writer starts from what the first wrote rather than from what both read."""
    global _CACHE
    # WHAT IS PRUNED IS ARCHIVED, NOT DROPPED. The journal is the ONLY record
    # of boards/websites/duplicates/founded rulings and of every before-image,
    # and it kept 500 entries: every journal-only ruling from 2026-08-28 to
    # 09-08 was already gone, and the 09-08 sitting of 101 rulings would have
    # followed after ~500 more writes - "your best sitting" silently falling
    # and the undo for those rulings unrecoverable. Pruned rows go to the
    # archive (below: one file per month) BEFORE the live file is rewritten,
    # so a crash between the two leaves a row in both places, never in
    # neither; the live file stays small for the reads that happen on every
    # admin request.
    pruned = rows[:-KEEP] if len(rows) > KEEP else []
    kept = rows[-KEEP:]
    data = LOG.parent
    with journal_lock(data):
        if pruned or legacy_archive(data).exists() or folding_archive(data).exists():
            normalize(data, live=kept, rows=pruned)       # on disk when it returns
        fd, tmp = tempfile.mkstemp(dir=str(data), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            for r in kept:
                fh.write(json.dumps(r) + "\n")
            fh.flush()
            _to_disk(fh.fileno(), LOG)
        os.replace(tmp, LOG)
        _dir_to_disk(data)
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


_ID_HEADS: dict = {}       # id space -> the head of a row numbered in it
_ID_SCAN: dict = {}        # (space, archive part) -> (inode, bytes read, {day: highest #n})


def _id_head(space: str = ""):
    if space not in _ID_HEADS:
        _ID_HEADS[space] = re.compile(rb'(?:^|\n)\{"id": "(\d{4}-\d{2}-\d{2})#'
                                      + re.escape(space.encode()) + rb'(\d+)"')
    return _ID_HEADS[space]


_ID_AT_HEAD = _id_head("")


def _scan_ids(blob: bytes, top: dict, space: str = "") -> None:
    for m in _id_head(space).finditer(blob):
        d, n = m.group(1).decode(), int(m.group(2))
        if n > top.get(d, 0):
            top[d] = n


def _archived_top(data, day: str, space: str = "") -> int:
    """The highest #n of `day` (in id space `space`) among the archived rows
    of day's month. An open part is read only past what an earlier call in
    this process read (an append moves only its end); a part rewritten or
    sealed is read whole, once - reading a month per click is the cost the
    archive keeps off the admin."""
    best = 0
    for m, _n, sealed, p in archive_parts(data):
        if m != day[:7]:
            continue
        try:
            st = p.stat()
            ino, size, top = _ID_SCAN.get((space, str(p)), (None, 0, {}))
            if ino != st.st_ino or st.st_size < size or (sealed and st.st_size != size):
                top = {}
                raw = p.read_bytes()
                _scan_ids(gzip.decompress(raw) if sealed else raw, top, space)
                size = len(raw)
            elif st.st_size > size:
                with open(p, "rb") as fh:
                    fh.seek(size)
                    tail = fh.read()
                _scan_ids(tail, top, space)
                size += len(tail)
            _ID_SCAN[(space, str(p))] = (st.st_ino, size, top)
        except (OSError, EOFError, zlib.error):
            continue
        best = max(best, top.get(day, 0))
    return best


def next_id(rows: list[dict] | None = None, archive=None, lines=(), space: str = "") -> str:
    """The next free id for today: past the highest of today's numbers in
    `rows` (the live journal when not given) - and in the archive's month of
    today (`archive`, a data folder; the live journal's own when `rows` is
    read here), and in `lines` (raw archived lines held elsewhere). Never one
    already used. `space` numbers in an id space of its own -
    `<day>#<space><n>` (the desk publisher's, PUBLISH_SPACE) - which no
    number in the plain space can ever equal, nor be equalled by.

    This counted today's entries and added one, which collides the moment the
    prune drops an early entry for the same day: the count falls, the number
    is handed out again, and admin_undo --undo takes whichever the lookup
    finds first. data/admin_journal.jsonl carries 2026-09-13#5 TWICE today -
    two different rulings under one id, one of which cannot be addressed.
    Counting was still short of it: with today's #8..#10 live and #1..#7
    pruned, three entries made #4 again (2026-11-24 second review); the
    highest number is what is past every id handed out, and the archive
    holds the ones pruned.
    """
    if rows is None:
        rows = _entries()
        archive = LOG.parent if archive is None else archive
    today = dt.date.today().isoformat()
    used = {str(r.get("id", "")) for r in rows if isinstance(r, dict)}
    top = {}
    for blob in lines or ():
        _scan_ids(blob, top, space)
    n = top.get(today, 0)
    for i in used:
        day, _, k = i.partition("#")
        if day == today and k.startswith(space) and k[len(space):].isdigit():
            n = max(n, int(k[len(space):]))
    if archive is not None:
        n = max(n, _archived_top(archive, today, space))
    n += 1
    while f"{today}#{space}{n}" in used:
        n += 1
    return f"{today}#{space}{n}"


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
    rewritten = sum(1 for k, c in changes.items() if c["before"] is not None
                    and not (c["after"] is None and k in UNDOING_ADDS))
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
           why: str = "", force: bool = False,
           extra: dict | None = None) -> tuple[str | None, str | None]:
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
    # the read, the new entry and the write are one step under the journal's
    # lock: two writers in one checkout (the desk, a session's script) each
    # read the same journal, and the second's write dropped the first's entry
    # and re-filed what the first had pruned and sealed
    with journal_lock():
        return _record(name, changes, action, by, why, extra)


def _record(name, changes, action, by, why, extra) -> tuple[str | None, str | None]:
    rows = _entries()
    entry = {
        "id": next_id(rows, archive=LOG.parent),
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "file": name,
        "action": action,
        "by": by or "owner",
        # the why is not decoration: it is what makes a ruling reviewable
        # later, and CLAUDE.md requires every ruling to carry one
        "why": why or "",
        "n": len(changes),
        "changes": changes,
        **({"desk": True} if DESK else {}),
        **({"same_ruling_as": WRITTEN[0]} if DESK and WRITTEN else {}),
        # an undo says which entry it reversed (undo_of): reversals() and the
        # receipt read it, and journal.undone() refuses undoing one twice
        **(extra or {}),
    }
    rows.append(entry)
    _write_entries(rows)
    WRITTEN.append(entry["id"])
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


# --- THE ARCHIVE: WHAT IS PRUNED, ONE FILE PER MONTH ----------------------
#
# Pruned rows were kept in ONE append-only file,
# data/admin_journal.archive.jsonl. By 2026-11-24 it was 71.6 MB and growing
# about 1 MB a day; GitHub warns at 50 MB and refuses any push carrying a
# file over 100 MB, so within a month every push - the nightly's too - would
# have failed. The owner's ruling (2026-11-24, "Build it"): one archive file
# per month, finished months compressed, the current month in plain text
# (its appends delta-compress in git). data/journal_archive/ holds:
#
#   2026-09.jsonl.gz      a SEALED month: gzip, written once, never appended
#                         to and never rewritten
#   2026-11.jsonl         an OPEN month: plain JSON lines, appended to
#   2026-10.2.jsonl(.gz)  the same month continued in a numbered part (the
#                         first part carries no number)
#
# WHICH MONTH A ROW BELONGS TO: its own "at", as the writer stamped it
# (2026-10-31T23:59:00-04:00 is October). Not the month it was pruned in: a
# row is filed under the same name whoever prunes it and whenever, so two
# copies of the journal pruning one entry name one file, and the second copy
# can be seen for what it is.
#
# A ROW THAT NAMES NO MONTH of its own - no dated "at" and no dated id - is
# filed under UNDATED ("0000-01"), one fixed name, for the same reason. It
# was filed under the month it was pruned in, and the same row pruned again a
# month later (a merge, a checkout that was behind) was filed again in the
# next month, where no check ever met the first copy (2026-11-24 review).
#
# WHEN A MONTH IS FINISHED, AND SEALED: at a write where the calendar is past
# the month AND no entry left in the live journal is from that month or an
# earlier one. Rows leave the live journal oldest first, so from that write
# on this journal can never hand the month another row. In practice a month
# is sealed about a week after it ends, when its last entry is pruned; until
# then it is the second plain month beside the current one. Sealing writes
# every open part of the month into gzip part(s) and then removes them.
#
# A ROW FOR A MONTH ALREADY SEALED - from a checkout that was behind, a
# merge, the old single file folded in - is never put into the gzip: it
# starts a new numbered part after it, sealed in its turn.
#
# A ROW ALREADY ARCHIVED IS NOT ARCHIVED AGAIN. A row is recognised by its
# whole line - the rule merge_data.merge_lines uses for a journal line - and
# checked against its month wherever the month's lines are read anyway: when
# the month is sealed (the first copy of a line is the one kept), when the
# old file or a stray part is folded in, when another side's rows are filed
# (publish.py, merge_decisions.py, --resolve), and when a row arrives for a
# month that already has a sealed part. The everyday prune into an open
# month only appends, as the old file did: reading a 40 MB month on every
# click is the cost this file exists to keep off the admin.
#
# A ROW IN ANOTHER MONTH'S FILE goes home. Git puts one there: a branch from
# before the migration, merging main, follows the old single file into the
# month file it judges a rename of it and adds the branch's rows to that
# file, whatever their month (the open parts carry no merge=union for this
# reason - see .gitattributes). The full pass - --fold, --resolve, every desk
# publish - reads each open part and moves such a row to its own month, and
# a seal, which reads its month anyway, files one in its own month rather
# than sealing it where it sits.
#
# ONE WRITER AT A TIME. Every write of the journal - record(), admin_undo, a
# fold, a resolve, and the desk publisher's snapshot and sync of its checkout
# - holds journal_lock(), an exclusive file lock beside LOG (data/
# .journal.lock, never data/.admin.lock: the desk holds that one around its
# whole action, in the same process), across its whole read, prune, file,
# seal and rewrite. Two writers in one checkout - the desk and a session's
# script - each listed a month's parts; the first sealed them, and the
# second's own seal REPLACED the first's gzip with the one row it had pruned:
# 41 rows to 1 (2026-11-24 review). Under the lock the second starts from
# what the first left. Behind it, for whatever writes without it (an older
# copy of this code, git itself): a seal re-reads the folder just before it
# reads the month, creates every sealed part EXCLUSIVELY - a name that exists
# is never replaced; the seal re-reads and seals only what no sealed part then
# holds - and removes an open part only while every line in it is sealed.
#
# THE OLD CODE NEVER TAKES THE LOCK. A desk not yet switched over appends what
# it prunes to the old single file, by name, whenever it likes; a fold that
# read the file and then removed it lost every row appended in between (126
# of 400 in a race, 2026-11-24 second review). So a fold first MOVES the file
# aside (FOLDING): the old code's next append makes a new old file, which the
# next write folds, and an append already under way through a handle opened
# before the move lands in the moved copy - read again after its lines are
# filed, and removed only once it has stood still for SETTLE seconds
# (fold_old). Readers read the moved copy as they read the old file; a fold
# cut off part way leaves it, and the next write finishes it first. THE WAIT
# IS A HEURISTIC, NOT A GUARD: an old-code write stalled past SETTLE (memory
# pressure, a stopped process) lands in the moved copy after it is gone, and
# is lost - kept at 0.2 s and 0.6 s, lost at 1.5 s (2026-11-25 third review).
# The guard is the owner's step 1: the old desk is stopped before anything
# folds its file.
#
# THE CONSTRUCTIVE STEP FIRST. Every move of a line - a seal, a row going home
# from another month's file, a re-split, a fold, a resolve - writes the line
# where it goes before it is taken from where it was, so a failure part way
# (a full disk, a kill) leaves a line in two places, never in none. A re-split
# writes the new parts under numbers past every part of the month and only
# then removes the old ones; a resolve takes a conflicted path out of git's
# index only after every stage's rows are filed. A line in two places is
# recognised by its whole line and kept once (the seal; the full pass, below).
# AND ON DISK: what was appended is fsynced (F_FULLFSYNC on macOS - fsync
# alone leaves it in the drive's cache) before anything is removed or
# rewritten on the strength of it (flush), a part written whole (a seal, a
# rewrite) is on disk with its name before it is used, and the live journal's
# rewrite follows the archive's append to disk - nothing was fsynced, so a
# power cut could keep the removal and lose the copy (2026-11-24 second review).
#
# A TORN APPEND IS NEVER GLUED TO THE NEXT ROW. A kill or a full disk part way
# through an append leaves a part without its final newline, and the next
# append wrote its row onto the end of the fragment: one line that is no JSON,
# the row in it unreadable, sealed so for good. An append to a part that does
# not end in a newline now writes the newline first. The fragment is then a
# line of its own that does not parse; when it is a byte-prefix of a whole
# line (one that parses) the month or the live journal holds - the row it was
# cut from, pruned again, because the live journal is rewritten only after
# the archive - it is a TORN COPY: left out of the seal, taken out by the full
# pass, and never carried to another copy of the archive. THE OLD CODE STILL
# GLUES: its append to the old single file has no newline guard, so a desk's
# old file can hold a fragment with the next row on the same line. Such a
# line is split wherever it is read - the fold, the full pass, the seal, the
# publisher's carry (unglue: the row is the end of the line, from a row's
# head) - and its pieces filed as any line is. Any other line that does not
# parse is kept where it is, its month is not sealed while it is there, and
# archive_problems names it for a person by file and line number (the old
# file too); a desk publish that would carry one is refused, naming the
# desk's file and line.
#
# THE FULL PASS (--fold, --resolve, every desk publish) also takes out of a
# month's open parts every line a sealed part of it holds, every second copy
# of a line its open parts hold and every torn copy of a row held whole - a
# merge that kept an open part the other side had sealed left October's part
# 2 holding 189 rows the sealed October held too (2026-11-24 review), and a
# torn copy on main refused every desk publish (second review) - and takes
# out a SEALED part every line of which other sealed parts of the month hold,
# the later of two alike: a branch's late part, sealed at once, beside main's
# seal of the same rows, merged cleanly and stood on main twice (second
# review). A sealed part sharing only some lines with others stays whole. The
# everyday write does not read a month to look; the seal, which reads it
# anyway, keeps one copy of a line.
#
# NO FILE PASSES CAP. An open part that a row would carry past it continues
# in the next numbered part, a seal splits the same way, and an open part a
# union merge pushed over is re-split at the next write. (One row bigger than
# CAP would sit alone in its part; the largest yet is 3.3 MB.)
#
# DETERMINISTIC BYTES. A sealed part is gzip at level 9 with mtime 0, no file
# name and the OS byte 255, the container written here because zlib's own
# gzip header carries the platform's OS code: the same rows give the same
# bytes, so git sees no change where there is none. The deflate stream is
# zlib's, and two different zlib builds could disagree on it - which is why
# nothing below depends on two seals matching: a merge keeps main's sealed
# file and files the other side's rows by line.
#
# THE ARCHIVE LIVES NEXT TO LOG, wherever LOG points (archive_dir()). The old
# file had its own path fixed at import, so a sandbox that moved LOG and not
# it appended every pruned row to the owner's real archive (2026-09-26), and
# merge_families.use_data_dir still moved LOG alone.

ARCHIVE_DIR = "journal_archive"
LEGACY_ARCHIVE = "admin_journal.archive.jsonl"     # the old single file
FOLDING = "admin_journal.archive.folding.jsonl"    # the old file, moved aside while it is folded
SETTLE = 1.0                                       # seconds the moved file must stand still
LOCK_NAME = ".journal.lock"                        # journal_lock(), beside LOG
LOCK_WAIT = 300                                    # seconds a writer waits for it
CAP = 40_000_000                                   # bytes, per file
GITHUB_WARNS = 50 * 1024 * 1024                    # past this, a push is warned
UNDATED = "0000-01"                                # a row that names no month of its own
_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
_PART = re.compile(r"(\d{4}-(?:0[1-9]|1[0-2]))(?:\.([1-9][0-9]*))?\.jsonl(\.gz)?")
# what record() writes first: {"id": "...", "at": "2026-10-...
_HEAD_AT = re.compile(rb'\{"id": "[^"\\]*", "at": "(\d{4}-(?:0[1-9]|1[0-2]))')
_ROW_HEAD = b'{"id": "'                            # where a row record() wrote begins
_MARK = re.compile(rb"(?:<{7}|={7}|>{7}|\|{7})(?: |$)")   # a conflict marker git writes
_GZ_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"   # deflate, no name, mtime 0, OS 255
_HELD = threading.local()                          # the locks this thread holds, and how deep


def _data(data=None) -> pathlib.Path:
    return pathlib.Path(data) if data is not None else LOG.parent


@contextlib.contextmanager
def journal_lock(data=None):
    """The journal's own lock, across processes: an exclusive flock on
    LOCK_NAME beside the live journal (the comment above, ONE WRITER AT A
    TIME). Re-entrant within a thread - record() holds it around
    _write_entries, which takes it too - and a second thread waits like a
    second process."""
    p = _data(data) / LOCK_NAME
    key = os.path.realpath(str(p))        # one key however the folder is spelled
    held = _HELD.__dict__.setdefault("paths", {})
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"the journal has been held by another writer for over "
                                       f"{LOCK_WAIT}s ({p}); nothing was written") from None
                time.sleep(0.02)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
            fcntl.flock(fh, fcntl.LOCK_UN)


def archive_dir(data=None) -> pathlib.Path:
    """The archive folder: next to the live journal unless told otherwise."""
    return _data(data) / ARCHIVE_DIR


def legacy_archive(data=None) -> pathlib.Path:
    """The old single file. Nothing writes it now; wherever it is found it is
    folded into the month files and removed (normalize)."""
    return _data(data) / LEGACY_ARCHIVE


def folding_archive(data=None) -> pathlib.Path:
    """The old single file moved aside while a fold reads it (normalize). It
    is there only during a fold, or after one cut off part way - and then the
    next fold finishes it; readers read it as they read the old file."""
    return _data(data) / FOLDING


def is_archive_path(path: str) -> bool:
    """A repo-relative path that belongs to the archive: the old single file
    (or that file moved aside by a fold), or a name the archive writes,
    directly under data/journal_archive/. Any other file there is no part of
    it - nothing reads it - so nothing carries or settles its lines: a
    session's scratch copy of fixture rows left there was published into
    main's month file, and deleted from the desk (2026-11-24 review)."""
    if path in (f"data/{LEGACY_ARCHIVE}", f"data/{FOLDING}"):
        return True
    head, _, name = path.rpartition("/")
    return head == f"data/{ARCHIVE_DIR}" and parse_part(name) is not None


def part_name(month: str, n: int, sealed: bool) -> str:
    return f"{month}{'' if n == 1 else f'.{n}'}.jsonl{'.gz' if sealed else ''}"


def parse_part(name: str) -> tuple[str, int, bool] | None:
    """(month, part number, sealed) for a name part_name() writes, else None."""
    m = _PART.fullmatch(name)
    if not m or m.group(2) == "1":
        return None
    return m.group(1), int(m.group(2) or 1), bool(m.group(3))


def month_of(row, today: dt.date | None = None) -> str:
    """The month a row is archived under: its own "at", else its id's date,
    else UNDATED - never the day it is pruned on (`today` is accepted and
    not used, for the callers that pass it)."""
    if isinstance(row, dict):
        for v in (row.get("at"), row.get("id")):
            s = str(v or "")[:7]
            if _MONTH.fullmatch(s):
                return s
    return UNDATED


def line_month(line: bytes, today: dt.date | None = None) -> str:
    m = _HEAD_AT.match(line)
    if m:
        return m.group(1).decode()
    try:
        row = json.loads(line)
    except ValueError:
        row = None
    return month_of(row, today)


def own_month(line: bytes) -> str | None:
    """The month a line's own at (or id) names, UNDATED for a row that names
    none; None for a line that does not parse and does not say - such a line
    is never moved to another month's file."""
    m = _HEAD_AT.match(line)
    if m:
        return m.group(1).decode()
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return month_of(row)


def _parses(line: bytes) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


def _torn_copy(line: bytes, whole) -> bool:
    """Is this line (one that does not parse) the start of a WHOLE line in
    `whole` - a line that parses: what a torn append leaves of a row that
    was then filed in full? A line that is itself no JSON vouches for no
    fragment: the glued line a fragment was cut out of is not its row."""
    return any(len(w) > len(line) and w.startswith(line) and _parses(w) for w in whole)


def unglue(line: bytes) -> list[bytes]:
    """A line the OLD code glued, split into [the fragment, the whole row];
    any other line as [line].

    The old code (e86b86da and before) appended to the old single file with
    no newline guard: an append cut off part way (a kill, a full disk) left
    a fragment without its newline, and its next append wrote a row onto
    the end of it - one line that is no JSON, the row in it unreadable, and
    every desk publish refused while `--check` said nothing (2026-11-24
    second review). The row is the END of the line: the first place a
    row's head ({"id": ") starts a suffix that is one whole JSON object. A
    head inside a row (a companies.json before-image begins {"id": " too)
    starts no such suffix - more follows the object it opens - so the split
    is only ever at the row itself. The fragment stays a line of its own,
    never dropped: a torn copy when the row it was cut from is held whole
    somewhere (the seal leaves it out), else a line for a person."""
    return [line] if _parses(line) else _split_glued(line)


def _split_glued(line: bytes) -> list[bytes]:
    """unglue() for a line already known not to parse."""
    i = line.find(_ROW_HEAD, 1)
    while i > 0:
        tail = line[i:]
        try:
            row = json.loads(tail)
        except ValueError:
            row = None
        if isinstance(row, dict):
            head = line[:i].strip()
            return [head, tail] if head else [tail]
        i = line.find(_ROW_HEAD, i + 1)
    return [line]


def numbered_lines(name: str, raw: bytes | None) -> list[tuple[int, bytes]]:
    """(line number, line) for every non-empty line of one file's bytes (a
    .gz name decompressed), numbered as an editor numbers them - so a line
    a person must read can be named where it is."""
    if not raw:
        return []
    if str(name).endswith(".gz"):
        raw = gzip.decompress(raw)
    return [(i, s) for i, s in enumerate((ln.strip() for ln in raw.split(b"\n")), 1) if s]


def _head(line: bytes, n: int = 90) -> str:
    """The start of a line, for a message: which row it was cut from."""
    return line[:n].decode("utf-8", "replace")


def _to_disk(fd: int, what=None) -> None:
    """Make what this descriptor wrote durable before anything is removed on
    the strength of it: F_FULLFSYNC on macOS (fsync there leaves the data
    in the drive's cache), fsync elsewhere. `what` names it for the
    selftest's recorder, nothing else."""
    try:
        if hasattr(fcntl, "F_FULLFSYNC"):
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
    except OSError:
        pass
    os.fsync(fd)


def _dir_to_disk(d: pathlib.Path) -> None:
    """A folder's entries (a part created, renamed or removed) made durable."""
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        _to_disk(fd, d)
    except OSError:
        pass
    finally:
        os.close(fd)


def _file_to_disk(p: pathlib.Path) -> None:
    try:
        with open(p, "rb") as fh:
            _to_disk(fh.fileno(), p)
    except FileNotFoundError:
        pass


def gz(data: bytes) -> bytes:
    """Deterministic gzip: the same bytes in always give the same bytes out."""
    co = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS, 8, zlib.Z_DEFAULT_STRATEGY)
    return (_GZ_HEADER + co.compress(data) + co.flush()
            + struct.pack("<II", zlib.crc32(data) & 0xFFFFFFFF, len(data) & 0xFFFFFFFF))


def lines_of(name: str, raw: bytes | None) -> list[bytes]:
    """The non-empty lines of one part's bytes (a .gz name is decompressed)."""
    if not raw:
        return []
    if str(name).endswith(".gz"):
        raw = gzip.decompress(raw)
    return [s for s in (ln.strip() for ln in raw.split(b"\n")) if s]


def _digest(line: bytes) -> bytes:
    return hashlib.sha256(line).digest()


def _write_bytes(p: pathlib.Path, blob: bytes) -> None:
    """Replace p with blob in one step (an open part, rewritten), on disk
    before the name moves."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            _to_disk(fh.fileno(), p)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _dir_to_disk(p.parent)


def _create(p: pathlib.Path, blob: bytes) -> bool:
    """A NEW file at p holding blob, whole or not at all, and on disk -
    bytes and name - before this returns; False, and nothing written, when
    p already exists. What a sealed part is written with: a name that exists
    is never replaced."""
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            _to_disk(fh.fileno(), p)
        try:
            os.link(tmp, p)
        except FileExistsError:
            return False
        except OSError:                    # a filesystem with no hard links
            try:
                fd2 = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                return False
            os.close(fd2)
            os.replace(tmp, p)
        _dir_to_disk(p.parent)
        return True
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def archive_parts(data=None) -> list[tuple[str, int, bool, pathlib.Path]]:
    """(month, n, sealed, path) for every part, in reading order: by month,
    then part number. A name part_name() would not write is not a part."""
    d = archive_dir(data)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for name in names:
        k = parse_part(name)
        if k and (d / name).is_file():
            out.append((*k, d / name))
    out.sort(key=lambda t: (t[0], t[1], not t[2]))
    return out


def _read_archive(data, legacy: bool) -> list[bytes]:
    out = []
    for _m, _n, _s, p in archive_parts(data):
        out += lines_of(p.name, p.read_bytes())
    if legacy:
        for p in [p for p in (folding_archive(data), legacy_archive(data)) if p.is_file()]:
            out += lines_of(p.name, p.read_bytes())
    return out


def archived_lines(data=None, legacy: bool = True):
    """Every archived line, part by part in reading order - and then the old
    single file's (a copy a fold has moved aside first), if one is still
    waiting to be folded in.

    READ WITHOUT THE LOCK, AND WHOLE. A writer may seal, re-split or fold
    while this reads, so a file listed a moment ago can be gone, its lines
    in a new part: that raised FileNotFoundError out of a reader (2026-11-24
    second review). The read starts again from the listing when a file it
    listed has vanished; after a few tries it waits for the journal's lock
    and reads between two writes."""
    for attempt in range(5):
        try:
            lines = _read_archive(data, legacy)
            break
        except FileNotFoundError:
            continue
    else:
        with journal_lock(data):
            lines = _read_archive(data, legacy)
    yield from lines


def archived_rows(data=None, legacy: bool = True):
    """archived_lines, parsed; a line that is not JSON is passed over."""
    for ln in archived_lines(data, legacy):
        try:
            yield json.loads(ln)
        except ValueError:
            continue


def _live_rows(data) -> list:
    p = _data(data) / LOG.name
    try:
        text = p.read_text()
    except OSError:
        return []
    out = []
    for ln in text.splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _live_lines(data) -> list[bytes]:
    p = _data(data) / LOG.name
    try:
        raw = p.read_bytes()
    except OSError:
        return []
    return [s for s in (ln.strip() for ln in raw.split(b"\n")) if s]


def _chunks(lines: list[bytes], limit: int) -> list[bytes]:
    out, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > limit:
            out.append(b"".join(cur))
            cur, size = [], 0
        cur.append(ln + b"\n")
        size += len(ln) + 1
    if cur:
        out.append(b"".join(cur))
    return out


def _seal_blobs(lines: list[bytes]) -> list[bytes]:
    """gzip blobs of these lines, in order, each within CAP."""
    limit = CAP * 4
    while True:
        plain = _chunks(lines, limit)
        blobs = [gz(c) for c in plain]
        if all(len(b) <= CAP or c.count(b"\n") == 1 for b, c in zip(blobs, plain)):
            return blobs
        limit = max(limit // 2, 1)


class _Archive:
    """One data directory's archive, as filing sees it."""

    def __init__(self, data=None, today: dt.date | None = None):
        self.data = _data(data)
        self.dir = archive_dir(self.data)
        self.today = today or dt.date.today()
        self.parts: dict = {}              # month -> {(n, sealed): path}
        for m, n, s, p in archive_parts(self.data):
            self.parts.setdefault(m, {})[(n, s)] = p
        self._seen: dict = {}              # month -> digests of its lines
        self._size: dict = {}              # open part -> its size
        self._nl: set = set()              # open parts known to end in a newline
        self._touched: set = set()         # open parts appended to, not yet on disk for sure
        self.whole: set = set()            # whole lines held elsewhere (a torn copy's test)
        self._live: set | None = None      # the live journal's lines, read once when asked
        self.did = {"filed": 0, "known": 0, "folded": 0, "refiled": 0, "sealed": [],
                    "split": [], "deduped": 0, "torn": 0, "held": [], "trimmed": 0,
                    "unglued": 0, "dropped": [], "odd": []}

    def relist(self, m: str) -> dict:
        """The month's parts as the folder holds them now: another writer may
        have sealed it since this archive was listed."""
        self.parts[m] = {(n, s): p for mm, n, s, p in archive_parts(self.data) if mm == m}
        self._seen.pop(m, None)
        return self.parts[m]

    def sealed_max(self, m: str) -> int:
        return max((n for (n, s) in self.parts.get(m, {}) if s), default=0)

    def proper_open(self, m: str) -> list[int]:
        sm = self.sealed_max(m)
        return sorted(n for (n, s) in self.parts.get(m, {}) if not s and n > sm)

    def _lines(self, p: pathlib.Path) -> list[bytes]:
        return lines_of(p.name, p.read_bytes()) if p.is_file() else []

    def _sealed_digests(self, m: str) -> set:
        return {_digest(ln) for (n, s), p in sorted(self.parts.get(m, {}).items())
                if s for ln in self._lines(p)}

    def seen(self, m: str) -> set:
        if m not in self._seen:
            self._seen[m] = {_digest(ln) for _k, p in sorted(self.parts.get(m, {}).items())
                             for ln in self._lines(p)}
        return self._seen[m]

    def _sz(self, p: pathlib.Path) -> int:
        if p not in self._size:
            self._size[p] = p.stat().st_size if p.exists() else 0
        return self._size[p]

    def _torn(self, line: bytes, m: str) -> bool:
        """Is this line a torn copy of a row held whole: in `whole` (what the
        caller says is held elsewhere), in the live journal beside this
        archive, or in the month's parts? The LIVE JOURNAL TOO, as the seal
        and archive_problems read it: an archive append cut off part way
        (a full disk) stops the write before the live journal is rewritten,
        so the row it was cutting is whole there and nowhere else - and the
        full pass kept the fragment that `--check` called a torn copy, and
        every publish was refused over it (2026-11-25 third review)."""
        if _torn_copy(line, self.whole):
            return True
        if self._live is None:
            self._live = set(_live_lines(self.data))
        if _torn_copy(line, self._live):
            return True
        return any(_torn_copy(line, self._lines(p))
                   for _k, p in sorted(self.parts.get(m, {}).items()))

    def _tail(self, m: str, need: int) -> pathlib.Path:
        """The open part a line of `need` bytes goes into: the month's last
        part when it is open and has room, else a new part after it."""
        parts = self.parts.setdefault(m, {})
        top = max((n for (n, _s) in parts), default=0)
        if top and (top, False) in parts and top > self.sealed_max(m):
            p = parts[(top, False)]
            if self._sz(p) == 0 or self._sz(p) + need <= CAP:
                return p
        p = self.dir / part_name(m, top + 1, False)
        parts[(top + 1, False)] = p
        return p

    def _append(self, p: pathlib.Path, blob: bytes) -> None:
        """Append to an open part. A part that does not end in a newline (an
        append torn part way) gets one first: the new row is a line of its
        own, never glued to the fragment."""
        size = self._sz(p)
        lead = b""
        if size and p not in self._nl:
            with open(p, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    lead = b"\n"
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as fh:
            fh.write(lead + blob)
        self._nl.add(p)
        self._touched.add(p)
        self._size[p] = size + len(lead) + len(blob)

    def flush(self) -> None:
        """What this pass appended, on disk - the parts and the folder -
        before a line is taken from anywhere on the strength of its copy
        here. The constructive step first holds through a power cut only
        if the constructive step reached the disk (2026-11-24 second review:
        nothing was fsynced). A part written whole (_create, _write_bytes)
        is on disk when written."""
        for p in sorted(self._touched):
            _file_to_disk(p)
        if self._touched:
            _dir_to_disk(self.dir)
        self._touched.clear()

    def file(self, line: bytes, m: str | None = None, check: bool = True) -> bool:
        """Append one line to its month. False when the line is already
        archived, or is a torn copy of a row held whole (checked when
        `check`). A line the old code glued (unglue) is filed as its two
        pieces, each in its own month; a line that is no JSON and no torn
        copy is filed and listed in did["odd"], for a person."""
        m = m or line_month(line, self.today)
        if check and _digest(line) in self.seen(m):
            self.did["known"] += 1
            return False
        if not _parses(line):
            pieces = _split_glued(line)
            if len(pieces) > 1:
                self.did["unglued"] += 1
                self.whole.update(x for x in pieces if _parses(x))
                return any([self.file(x, None, check) for x in pieces])
            if check and self._torn(line, m):
                self.did["torn"] += 1
                return False
            self.did["odd"].append(line)
        p = self._tail(m, len(line) + 1)
        self._append(p, line + b"\n")
        if m in self._seen:
            self._seen[m].add(_digest(line))
        self.did["filed"] += 1
        return True

    def _rewrite(self, m: str, n: int, lines: list[bytes]) -> None:
        """An open part rewritten to hold just these lines; removed when none.
        What leaves it is held elsewhere - on disk first (flush)."""
        self.flush()
        parts = self.parts.setdefault(m, {})
        p = parts.get((n, False)) or self.dir / part_name(m, n, False)
        if lines:
            blob = b"".join(ln + b"\n" for ln in lines)
            _write_bytes(p, blob)
            self._size[p] = len(blob)
            self._nl.add(p)
        else:
            parts.pop((n, False), None)
            self._size.pop(p, None)
            p.unlink(missing_ok=True)
        self._seen.pop(m, None)

    def refile(self, m: str) -> None:
        """A row sitting in ANOTHER month's open part goes to its own month, by
        line - filed there first, and only then taken out of this part. Git
        puts one there when a merge follows the old single file into a month
        file it judges a rename of it - a branch from before the migration,
        merging main - and a plain union of two versions can too. A line the
        old code glued (unglue) is split here first, and each piece goes by
        its own month: the row in it is read again."""
        parts = self.parts.get(m, {})
        for n in sorted(n for (n, s) in parts if not s):
            p = parts.get((n, False))
            raw = self._lines(p) if p is not None else []
            lines = [x for ln in raw for x in unglue(ln)]
            away = [ln for ln in lines if own_month(ln) not in (None, m)]
            if not away and len(lines) == len(raw):
                continue
            for ln in away:
                self.file(ln, own_month(ln), check=True)
            self._rewrite(m, n, [ln for ln in lines if own_month(ln) in (None, m)])
            self.did["refiled"] += len(away)
            self.did["unglued"] += len(lines) - len(raw)

    def dedupe(self, m: str) -> None:
        """The full pass's reading of a month: a line a sealed part holds
        leaves the open parts, a line the open parts hold twice keeps its
        first copy, and a TORN COPY - no JSON, the start of a whole row the
        month (or another copy's lines) holds - leaves them too: the seal
        would leave it out anyway, and until then the publisher's parse of
        a staged part refused every desk publish over it (2026-11-24 second
        review). The lines removed are held elsewhere, whole."""
        parts = self.parts.get(m, {})
        opens = sorted(n for (n, s) in parts if not s)
        if not opens:
            return
        held = self._sealed_digests(m) if any(s for (_n, s) in parts) else set()
        for n in opens:
            lines = self._lines(parts[(n, False)])
            keep, torn = [], 0
            for ln in lines:
                d = _digest(ln)
                if d in held:
                    continue
                if not _parses(ln) and self._torn(ln, m):
                    torn += 1
                    continue
                held.add(d)
                keep.append(ln)
            if len(keep) != len(lines):
                self.did["deduped"] += len(lines) - len(keep) - torn
                self.did["torn"] += torn
                self._rewrite(m, n, keep)

    def drop_sealed_copies(self, m: str) -> None:
        """A SEALED part every line of which other sealed parts of the month
        hold is taken out, the highest number first - of two alike, the first
        stays. A branch that sealed a month, merged main and sealed a late row
        at once (2026-10.2.jsonl.gz) met main's own seal of the same rows: the
        merge kept main's 2026-10.jsonl.gz, the branch's late part merged
        cleanly, and its row was on main twice, sealed, for good (2026-11-24
        second review). Nothing is lost: every line of the part removed is in
        a sealed part that stays. A part sharing only SOME lines stays whole
        (a sealed file is never rewritten); archive_problems notes it."""
        parts = self.relist(m)
        sealed = sorted((n for (n, s) in parts if s), reverse=True)
        if len(sealed) < 2:
            return
        held = {}
        for n in sealed:
            try:
                held[n] = {_digest(ln) for ln in self._lines(parts[(n, True)])}
            except (OSError, EOFError, zlib.error, gzip.BadGzipFile):
                held[n] = None                 # unreadable: never taken out, vouches for nothing
        for n in sealed:
            mine = held.get(n)
            if not mine:
                continue
            others = set()
            for k, v in held.items():
                if k != n and v:
                    others |= v
            if mine <= others:
                p = parts.pop((n, True))
                p.unlink(missing_ok=True)
                held.pop(n)
                self.did["dropped"].append(p.name)
        self._seen.pop(m, None)

    def _new_open(self, m: str, n: int, blob: bytes) -> int:
        """A new open part at the first free number from n; its number."""
        parts = self.parts.setdefault(m, {})
        while (n, False) in parts or not _create(self.dir / part_name(m, n, False), blob):
            n += 1
        p = self.dir / part_name(m, n, False)
        parts[(n, False)] = p
        self._size[p] = len(blob)
        self._nl.add(p)
        return n

    def tidy(self, m: str) -> None:
        """A stray open part (numbered at or below a sealed one: a merge kept a
        file the other side had sealed, or a seal stopped half way) is folded
        into the month by line; an open part past CAP is re-split."""
        parts = self.parts.get(m, {})
        sm = self.sealed_max(m)
        for n in sorted(n for (n, s) in parts if not s and n <= sm):
            p = parts.pop((n, False))
            self._size.pop(p, None)
            self._seen.pop(m, None)
            lines = self._lines(p)
            self.whole.update(lines)
            for ln in lines:                    # into the month's own part first...
                self.file(ln, m, check=True)
            self.flush()
            p.unlink(missing_ok=True)           # ...then the stray goes
        opens = self.proper_open(m)
        over = [n for n in opens if self._sz(parts[(n, False)]) > CAP]
        if all(len(self._lines(parts[(n, False)])) <= 1 for n in over):
            return                  # (one row bigger than CAP sits alone in its part)
        # RE-SPLIT, the new parts first: under numbers past every part of the
        # month, so no part holding a line is replaced before the line is
        # written again; a line the old parts held twice is written once
        lines, seen = [], set()
        for n in opens:
            for ln in self._lines(parts[(n, False)]):
                d = _digest(ln)
                if d not in seen:
                    seen.add(d)
                    lines.append(ln)
        top = max(n for (n, _s) in parts)
        for blob in _chunks(lines, CAP):
            top = self._new_open(m, top + 1, blob)
        self.flush()
        for n in opens:
            p = parts.pop((n, False))
            self._size.pop(p, None)
            p.unlink(missing_ok=True)
        self._seen.pop(m, None)
        self.did["split"].append(m)

    def seal(self, m: str) -> None:
        """Every open part of a finished month into gzip part(s), numbered
        after the last sealed one, then the open parts removed. A line a
        sealed part already holds, or one sealed a moment ago, is not sealed
        twice; another month's row goes home before anything is sealed; a
        torn copy is left out, and a month holding any other line that does
        not parse is not sealed at all. A line the old code glued is read as
        its two pieces (unglue)."""
        parts = self.relist(m)                  # the folder as it is now
        opens = self.proper_open(m)
        if not opens:
            return
        sm = self.sealed_max(m)
        seen = self._sealed_digests(m) if sm else set()
        lines, away = [], []
        for n in opens:
            for ln in (x for raw in self._lines(parts[(n, False)]) for x in unglue(raw)):
                if own_month(ln) not in (None, m):
                    away.append(ln)          # another month's row: never sealed here
                    continue
                d = _digest(ln)
                if d not in seen:
                    seen.add(d)
                    lines.append(ln)
        odd = [ln for ln in lines if not _parses(ln)]
        if odd:
            whole = set(lines) | self.whole | set(_live_lines(self.data))
            torn = {ln for ln in odd if _torn_copy(ln, whole)}
            if len(torn) < len(odd):
                self.did["held"].append(m)   # archive_problems names it for a person
                return
            lines = [ln for ln in lines if ln not in torn]
            self.did["torn"] += len(torn)
        for ln in away:                      # 1. another month's row goes home
            self.file(ln, own_month(ln), check=True)
        self.did["refiled"] += len(away)
        pending = lines                      # 2. the gzip, never over a file
        for _attempt in range(16):
            if not pending:
                break
            base, made = self.sealed_max(m), 0
            for blob in _seal_blobs(pending):
                p = self.dir / part_name(m, base + 1 + made, True)
                if not _create(p, blob):
                    break                    # a sealed part appeared under this one
                self.parts[m][(base + 1 + made, True)] = p
                made += 1
            else:
                pending = []
                break
            self.relist(m)
            have = self._sealed_digests(m)
            pending = [ln for ln in pending if _digest(ln) not in have]
        else:
            raise RuntimeError(f"{ARCHIVE_DIR}/{m}: no free name for a sealed part")
        # 3. only now the open parts go, each while every line in it is sealed,
        # went home or was a torn copy (a line written since stays, to be
        # folded by the next write) - and once what went home is on disk
        self.flush()
        done = seen | {_digest(ln) for ln in away}
        for n in opens:
            p = self.dir / part_name(m, n, False)
            if all(_digest(x) in done for ln in self._lines(p) for x in unglue(ln)):
                self.parts[m].pop((n, False), None)
                self._size.pop(p, None)
                p.unlink(missing_ok=True)
        self._seen.pop(m, None)
        self.did["sealed"].append(m)

    def trim_live(self) -> list:
        """The live journal back to its own rules after two copies of it were
        merged by line (the desk publisher's worktree): a row the archive
        holds leaves it - one side had pruned it, and the union brought it
        back - and it keeps its last KEEP entries, the older ones filed by
        line first. Returns the rows it keeps."""
        p = self.data / LOG.name
        try:
            raw = p.read_bytes()
        except OSError:
            return []
        lines = [s for s in (ln.strip() for ln in raw.split(b"\n")) if s]
        keep = [ln for ln in lines if _digest(ln) not in self.seen(line_month(ln, self.today))]
        dropped = len(lines) - len(keep)
        pruned = keep[:-KEEP] if len(keep) > KEEP else []
        keep = keep[len(pruned):]
        for ln in pruned:
            self.file(ln, line_month(ln, self.today), check=True)
        if dropped or pruned:
            self.flush()
            fd, tmp = tempfile.mkstemp(dir=str(self.data), suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(b"".join(ln + b"\n" for ln in keep))
                fh.flush()
                _to_disk(fh.fileno(), p)
            os.replace(tmp, p)
            _dir_to_disk(self.data)
        self.did["trimmed"] = dropped + len(pruned)
        out = []
        for ln in keep:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out

    def _fold_blob(self, blob: bytes) -> None:
        lines = [s for s in (ln.strip() for ln in blob.split(b"\n")) if s]
        self.whole.update(x for ln in lines for x in unglue(ln) if _parses(x))
        for ln in lines:
            self.did["folded"] += self.file(ln, check=True)

    def fold_old(self, p: pathlib.Path, since: float) -> None:
        """The old single file, MOVED ASIDE to `p` (normalize), folded in by
        line and then removed.

        A desk still on the old code never takes the journal's lock: it
        appends to the old file by name, at any moment. The fold read the
        file, filed its lines and removed it, and whatever the old code
        appended in between went with it - 126 of 400 rows in a race
        (2026-11-24 second review). Now the file is moved aside before it is
        read, so the old code's next append makes a new old file, folded by
        the next write; an append already under way, through a handle opened
        before the move, still lands in `p` - so `p` is read again after its
        lines are filed, and removed only once it has stood still for SETTLE
        seconds since it was moved (`since`) or last grew, everything in it
        filed. Bytes after its last newline, once it stands still, are an
        append cut off part way: a line of their own (a torn copy, by the
        usual test, or a line for a person). SETTLE is a heuristic: a write
        stalled longer than it is lost - stopping the old desk first (the
        owner's step 1) is what makes the fold safe."""
        off, sig, quiet_from = 0, None, since
        while True:
            st = os.stat(p)
            now_sig = (st.st_size, st.st_mtime_ns)
            if sig is not None and now_sig != sig:
                quiet_from = time.monotonic()
            sig = now_sig
            with open(p, "rb") as fh:
                fh.seek(off)
                chunk = fh.read()
            end = chunk.rfind(b"\n") + 1
            if end:
                self._fold_blob(chunk[:end])
                off += end
            quiet = time.monotonic() - quiet_from
            if quiet >= SETTLE:
                st = os.stat(p)
                if (st.st_size, st.st_mtime_ns) == sig and st.st_size == off + len(chunk) - end:
                    if chunk[end:].strip():
                        self._fold_blob(chunk[end:])
                    self.flush()
                    p.unlink()
                    _dir_to_disk(p.parent)
                    return
                continue
            time.sleep(min(0.05, max(SETTLE - quiet, 0.001)))


def finished(month: str, live: list, today: dt.date | None = None) -> bool:
    """Can this journal never hand the month another row: the calendar is
    past it and nothing left in the live journal is from it or before it.
    An undated live row decides only UNDATED, the one month it goes to."""
    today = today or dt.date.today()
    if month >= today.isoformat()[:7]:
        return False
    for r in live:
        m = month_of(r, today)
        if m == UNDATED and month != UNDATED:
            continue
        if m <= month:
            return False
    return True


def normalize(data=None, live: list | None = None, rows=(), lines=(),
              today: dt.date | None = None, full: bool = False,
              seal: bool = True, known=(), trim: bool = False) -> dict:
    """File rows into the archive and bring it to its rules. Returns what it did.

      rows   journal entries just pruned from the live journal (dicts). An
             open month is appended to without a read; a row for a month
             with a sealed part is checked first.
      lines  archived lines from somewhere else - another side of a merge,
             a desk's snapshot. Every one is checked; only what this archive
             lacks is filed, and a torn copy of a line held whole is not.
      known  whole lines held elsewhere (the desk's live journal): only what
             a torn copy is told by.
      live   the live journal as it stands after this write (read from disk
             when not given): what decides which months are finished.
      full   also read every open part: split a line the old code glued and
             move a row of another month to its own (refile); take out of
             the open parts what a sealed part or an earlier copy holds, and
             a torn copy of a row held whole (dedupe); take out a sealed part
             whose every line other sealed parts hold (drop_sealed_copies) -
             what a merge can leave. --fold, --resolve and the desk publisher
             do; the everyday write does not read.
      trim   also bring the live journal on disk to its rules (trim_live):
             the desk publisher, whose merge of two live journals by line
             brings back what one side pruned.
      seal   seal the finished months (False for a resolver in the middle of
             a merge or rebase: it settles rows and nothing more, and the next
             write or publish seals).

    In order, under journal_lock(): the old single file is moved aside,
    folded in and removed (fold_old; a copy a fold cut off part way left
    aside is finished first); `rows`, then `lines`, are filed; with `full`,
    glued lines are split, rows in another month's file move, second copies
    and torn copies go, and so does a sealed part other sealed parts hold
    whole; strays are folded and oversized open parts re-split; with `trim`,
    the live journal is trimmed; every finished month is sealed; and what
    was appended is on disk before this returns (flush)."""
    with journal_lock(data):
        a = _Archive(data, today)
        lines = list(lines or ())
        a.whole.update(known or ())
        a.whole.update(x for ln in lines for x in unglue(ln) if _parses(x))
        aside, old = folding_archive(a.data), legacy_archive(a.data)
        if aside.is_file():                 # a fold cut off part way: finished first
            a.fold_old(aside, time.monotonic())
        if old.is_file():
            os.rename(old, aside)           # the old code's next append makes a new one
            _dir_to_disk(a.data)
            a.fold_old(aside, time.monotonic())
        for r in rows or ():
            m = month_of(r, a.today)
            a.file(json.dumps(r).encode(), m, check=bool(a.sealed_max(m)))
        for ln in lines:
            a.file(ln, check=True)
        if full:
            for m in sorted(a.parts):
                a.refile(m)
            for m in sorted(a.parts):
                a.dedupe(m)
            for m in sorted(a.parts):
                a.drop_sealed_copies(m)
        for m in sorted(a.parts):
            a.tidy(m)
        if trim:
            live = a.trim_live()
        if seal:
            live = _live_rows(a.data) if live is None else live
            for m in sorted(a.parts):
                if a.proper_open(m) and finished(m, live, a.today):
                    a.seal(m)
        # on disk before the caller takes a row from anywhere on the strength
        # of it here: _write_entries rewrites the live journal next
        a.flush()
        return a.did


def _odd_lines(numbered, whole) -> tuple[list, list, list]:
    """Of one file's (line number, line) pairs that do not parse: the lines
    that are no torn copy of a row held whole (for a person), the torn
    copies, and the glued lines whose fragment is a torn copy (the full pass
    splits them) - each as (line number, the piece that does not parse, the
    whole line it sits in)."""
    other, torn, glued = [], [], []
    for i, ln in numbered:
        pieces = unglue(ln)
        for x in pieces:
            if _parses(x):
                continue
            if not _torn_copy(x, whole):
                other.append((i, x, ln))
            elif len(pieces) > 1:
                glued.append((i, x, ln))
            else:
                torn.append((i, x, ln))
    return other, torn, glued


def glued_after(piece: bytes, line: bytes) -> list[tuple[str, int]]:
    """(id, bytes) of each whole row the old code glued onto `piece` in
    `line` - nothing when the line is the piece alone."""
    out = []
    for x in unglue(line):
        if x == piece or not _parses(x):
            continue
        try:
            rid = json.loads(x).get("id")
        except (ValueError, AttributeError):
            rid = None
        out.append((str(rid or "a row"), len(x)))
    return out


def describe_piece(piece: bytes, line: bytes) -> str:
    """A line that is no JSON, as a person must be told it before cutting:
    its size, and - when the old code glued a whole row onto it - the
    fragment's size apart from the line's, and the row after it, which is
    kept. A message that gave the fragment's size alone led a person to
    cut the whole line, and the row glued onto it went with it (2026-11-25
    third review)."""
    glued = glued_after(piece, line)
    if not glued:
        return f"{len(piece):,} bytes, '{_head(piece, 70)}'"
    rows = ", ".join(f"{rid} ({n:,} bytes)" for rid, n in glued)
    return (f"{len(line):,} bytes: a fragment of {len(piece):,} bytes, '{_head(piece, 70)}', "
            f"with a whole row the old code glued after it - {rows} - which is kept")


def _where(name: str, items) -> str:
    return "; ".join(f"line {t[0]}: {describe_piece(t[1], t[2] if len(t) > 2 else t[1])}"
                     for t in items[:3]) + \
        (f"; and {len(items) - 3} more" if len(items) > 3 else "")


def split_first(items, fold: str) -> str:
    """What a person does before cutting a fragment the old code glued a
    whole row onto: nothing is cut from such a line until it is split."""
    if not any(len(t) > 2 and glued_after(t[1], t[2]) for t in items):
        return ""
    return (f" A line with a whole row glued after its fragment is split first: {fold} (the "
            f"desk stopped) files the row and leaves the fragment alone on a line of its own in "
            f"{ARCHIVE_DIR}/, which --check then names - only the fragment's bytes are cut")


def archive_problems(data=None) -> tuple[list[str], list[str]]:
    """(what breaks the archive's rules and needs a person, what the next
    write, publish or `--fold` puts right by itself) on disk. Every line is
    parsed - the old single file's too, and a copy of it a fold moved aside
    (`--check` in a desk's checkout said "the archive keeps its rules" over
    a line its old code had glued, while every publish was refused,
    2026-11-24 second review). A line that is no JSON is a torn copy of a
    row held whole (a note: the seal leaves it out), a row the old code
    glued onto a torn copy (a note: the fold and the full pass split it),
    or a line a person must read - named by file and line number."""
    bad, look = [], []
    d = archive_dir(data)
    fold = "python3 scripts/journal.py --fold"
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    for name in names:
        if parse_part(name) is None and not name.startswith("."):
            bad.append(f"{ARCHIVE_DIR}/{name} is not a part name ({part_name('2026-10', 1, True)}, "
                       f"{part_name('2026-10', 2, False)}, ...): nothing reads it")
    sealed_max: dict = {}
    by_month: dict = {}
    for m, n, s, p in archive_parts(data):
        by_month.setdefault(m, []).append((n, s, p))
        if s:
            sealed_max[m] = max(sealed_max.get(m, 0), n)
    live = _live_lines(data)
    # every file read once, whole: a torn copy's row may be held whole in
    # another month's file, the old file or the live journal
    months: dict = {}                       # m -> [(name, n, sealed, size, lines, odd)]
    for m, items in by_month.items():
        for n, s, p in items:
            raw = p.read_bytes()
            try:
                numbered = numbered_lines(p.name, raw)
            except (OSError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
                bad.append(f"{p.name} does not decompress: {exc}")
                continue
            odd = [(i, ln) for i, ln in numbered if not _parses(ln)]
            months.setdefault(m, []).append((p.name, n, s, len(raw),
                                             [ln for _i, ln in numbered], odd))
    olds = []                               # (name, odd)
    whole = set(live)
    for p, what in ((folding_archive(data), f"a fold of {LEGACY_ARCHIVE} was cut off part way "
                     f"(or is under way)"), (legacy_archive(data), "it is still there")):
        if p.is_file():
            look.append(f"{p.name}: {what}: the next journal write folds it into "
                        f"{ARCHIVE_DIR}/ (or: {fold})")
            numbered = numbered_lines(p.name, p.read_bytes())
            odd = [(i, ln) for i, ln in numbered if not _parses(ln)]
            whole.update(ln for _i, ln in numbered)
            whole.update(x for _i, ln in odd for x in unglue(ln))
            olds.append((p.name, odd))
    for items in months.values():
        whole.update(ln for *_x, lines, _odd in items for ln in lines)
        whole.update(x for *_x, odd in items for _i, ln in odd for x in unglue(ln))
    for name, odd in olds:
        other, torn, glued = _odd_lines(odd, whole)
        if other:
            bad.append(f"{name} holds {len(other)} line(s) that are not JSON, and no row held "
                       f"whole starts with them ({_where(name, other)}): what an append cut off "
                       f"part way left of a row no file holds whole now. A person reads each; "
                       f"once he has, it is removed from {name} (a copy kept outside the "
                       f"repository) - a desk publish is refused while it is there."
                       + split_first(other, fold))
        if glued:
            look.append(f"{name} holds {len(glued)} row(s) the old code glued onto a torn copy of "
                        f"a row held whole ({_where(name, glued)}): the fold, and a desk "
                        f"publish, split them and file the row")
        if torn:
            look.append(f"{name} holds {len(torn)} torn copy(ies) of a row held whole "
                        f"({_where(name, torn)}): the fold leaves them out")
    for m, items in months.items():
        read = []                                   # (name, sealed, lines)
        for name, n, s, size, lines, odd in items:
            if size > CAP and len(lines) > 1:
                (bad if size > GITHUB_WARNS else look).append(
                    f"{name} is {size:,} bytes, past the cap of {CAP:,}: the next journal "
                    f"write re-splits it (or: {fold})")
            if not s and n <= sealed_max.get(m, 0):
                look.append(f"{name} is open beside a sealed part numbered {sealed_max[m]} (a "
                            f"merge kept a file the other side sealed): the next journal write "
                            f"folds it (or: {fold})")
            unparsed = {ln for _i, ln in odd}
            away = sum(1 for ln in lines if ln not in unparsed and own_month(ln) != m)
            if away and s:
                bad.append(f"{name} is sealed with {away} row(s) of another month in it")
            elif away:
                look.append(f"{name} holds {away} row(s) of another month (a merge put them "
                            f"there): {fold} moves them, as the next desk publish does")
            other, torn, glued = _odd_lines(odd, whole)
            if s and (other or torn or glued):
                odd = sorted(other + torn + glued)
                bad.append(f"{name} is sealed with {len(odd)} line(s) that are not JSON "
                           f"({_where(name, odd)}): a sealed file is never rewritten; a person "
                           f"reads them")
            elif other:
                bad.append(f"{name} holds {len(other)} line(s) that are not JSON and no torn copy "
                           f"of a row held whole ({_where(name, other)}): a person reads and "
                           f"removes them; the month is not sealed while they are there."
                           + split_first(other, fold))
            if glued and not s:
                look.append(f"{name} holds {len(glued)} row(s) glued onto a torn copy of a row "
                            f"held whole ({_where(name, glued)}): {fold} splits them, as the "
                            f"next desk publish does")
            if torn and not s:
                look.append(f"{name} holds {len(torn)} torn copy(ies) of a row held whole (an "
                            f"append cut off part way; {_where(name, torn)}): the seal leaves "
                            f"them out, and {fold} takes them out")
            read.append((name, s, lines))
        copies: dict = {}
        sealed_copies: dict = {}
        for _name, s, lines in read:
            for ln in lines:
                k = _digest(ln)
                copies[k] = copies.get(k, 0) + 1
                if s:
                    sealed_copies[k] = sealed_copies.get(k, 0) + 1
        twice = [k for k, c in copies.items() if c > 1]
        if twice:
            fixed = [k for k in twice if sealed_copies.get(k, 0) > 1]
            if len(twice) > len(fixed):
                look.append(f"{m}: {len(twice) - len(fixed)} line(s) held twice in its parts (a "
                            f"merge kept an open part the other side sealed, or one row was "
                            f"pruned twice): {fold} keeps one, as the next desk publish and the "
                            f"seal do")
            if fixed:
                sealed_sets = {name: {_digest(ln) for ln in lines}
                               for name, s, lines in read if s}
                spare = [name for name, mine in sealed_sets.items() if mine and mine <= set().union(
                    *(v for k, v in sealed_sets.items() if k != name))]
                if spare:
                    look.append(f"{m}: {', '.join(sorted(spare))} hold(s) only lines other sealed "
                                f"parts of the month hold (two sides sealed the same rows): {fold} "
                                f"takes the later one out, as the next desk publish does")
                else:
                    look.append(f"{m}: {len(fixed)} line(s) held by two sealed parts: a sealed "
                                f"file is written once, so both copies stay; they read as the "
                                f"same row")
    return bad, look


def resolve(root, paths: list[str], upstream: int = 2) -> str:
    """Settle the archive paths of a stopped rebase (upstream=2, git's stage
    for the branch being rebased onto) or merge (upstream=3 when the branch
    merged in is main; 2 when main is the one merging it in - main() works it
    out). The upstream's version of each path stands - a sealed file main has
    is never replaced, and a path main REMOVED stays removed - and every line
    any stage holds that the archive then lacks is filed by the rules above,
    then staged. Nothing is sealed here: the next write or publish does that."""
    import subprocess
    root = pathlib.Path(root)

    def stage(n: int, p: str) -> bytes | None:
        r = subprocess.run(["git", "show", f":{n}:{p}"], cwd=root, capture_output=True)
        return r.stdout if r.returncode == 0 else None
    other = 3 if upstream == 2 else 2

    def proper(p: str, raw: bytes | None) -> bool:
        """Is this a part of its own month: every row its month's?"""
        k = parse_part(p.rsplit("/", 1)[-1])
        try:
            return bool(k) and raw is not None and \
                all(own_month(ln) in (None, k[0]) for ln in lines_of(p, raw))
        except (OSError, EOFError, zlib.error):
            return False
    with journal_lock(root / "data"):
        lines: list = []
        plan: dict = {}
        for p in paths:
            for n in (1, upstream, other):
                lines += lines_of(p, stage(n, p))
            # AND WHAT THE WORKTREE HOLDS THERE: a resolve cut off part way
            # (below) may have filed rows into this very file. Conflict
            # markers are git's, never a row.
            f = root / p
            if f.is_file():
                try:
                    lines += [ln for ln in lines_of(p, f.read_bytes()) if not _MARK.match(ln)]
                except (OSError, EOFError, zlib.error, gzip.BadGzipFile):
                    pass
            up = stage(upstream, p)
            # GIT FOLLOWS THE OLD SINGLE FILE INTO A PART IT JUDGES A RENAME OF
            # IT, so one side's version of a part can be that whole file. It
            # never stands over the other side's proper part, whichever way the
            # merge runs (main into the migration's branch, or the branch into
            # main); its rows are filed with every other stage's. A path the
            # upstream does not have at all is not that case: main sealed the
            # month and removed the part, and writing the other side's copy
            # back would hold every row of it twice - beside the gzip that
            # holds them (2026-11-24 review: 189 rows, 6.9 MB, on main twice).
            theirs = stage(other, p)
            if up is not None and not proper(p, up) and proper(p, theirs):
                up = theirs
            plan[p] = up
        # THE CONSTRUCTIVE STEP FIRST, here too: the worktree takes upstream's
        # version of each path and every line any stage holds is filed by the
        # rules WHILE THE INDEX STILL HOLDS EVERY STAGE - so a resolve cut off
        # part way (a full disk) leaves its paths conflicted, and the next
        # resolve reads all of it again. The stages went first, and a crash in
        # the filing lost the other side's rows with nothing left conflicted
        # to say so (2026-11-24 second review).
        for p, up in plan.items():
            f = root / p
            if up is None:
                f.unlink(missing_ok=True)
            else:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(up)
        did = normalize(root / "data", lines=lines, full=True, seal=False)
        for p in paths:                     # only now: the stages, then the archive staged
            subprocess.run(["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", p],
                           cwd=root, check=True, capture_output=True)
        for p in (f"data/{ARCHIVE_DIR}", f"data/{LEGACY_ARCHIVE}", f"data/{FOLDING}"):
            listed = subprocess.run(["git", "ls-files", "--", p], cwd=root, capture_output=True,
                                    text=True).stdout.strip()
            if listed or (root / p).exists():
                subprocess.run(["git", "add", "-A", "--", p], cwd=root, check=True,
                               capture_output=True)
    return (f"the journal archive: {len(paths)} path(s) settled, {did['filed']} row(s) "
            f"filed, {did['known']} already archived, {did['refiled']} moved to their own "
            f"month, {did['deduped']} second copies taken out")


def main_side(root) -> int | None:
    """In a stopped MERGE, git's stage number for main's side: 3 when the
    branch merged in is main (origin/main, or main), 2 when HEAD is main and
    a branch is being merged into it; None when neither side is main.
    `up = 3` for every merge let a branch merged INTO main replace main's
    sealed file with its own (2026-11-24 review)."""
    import subprocess
    root = pathlib.Path(root)

    def sha(ref: str) -> str | None:
        r = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"],
                           cwd=root, capture_output=True, text=True)
        return r.stdout.strip() or None if r.returncode == 0 else None
    remote, local = sha("refs/remotes/origin/main"), sha("refs/heads/main")
    head, theirs = sha("HEAD"), sha("MERGE_HEAD")
    on_main = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=root,
                             capture_output=True, text=True).stdout.strip() == "refs/heads/main"
    if remote and theirs == remote:
        return 3
    if remote and head == remote:
        return 2
    if (on_main or (local and head == local)) and theirs != local:
        return 2
    if local and theirs == local:
        return 3
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="The admin journal's archive, data/journal_archive/.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fold", action="store_true",
                   help="fold the old single file and any stray part into the month files, "
                        "and seal every finished month")
    g.add_argument("--check", action="store_true", help="say what breaks the archive's rules")
    g.add_argument("--resolve", action="store_true",
                   help="inside a stopped merge with main (either way) or rebase onto it: "
                        "settle the conflicted archive paths and stage them")
    ap.add_argument("--upstream", type=int, choices=(2, 3),
                    help="git's stage number for main's side (default: 2 in a rebase; in a "
                         "merge, the side that is origin/main or main)")
    a = ap.parse_args(argv)
    if a.fold:
        did = normalize(full=True)
        print(f"folded {did['folded']} row(s) from {LEGACY_ARCHIVE}, filed {did['filed']}, "
              f"{did['known']} already archived, {did['refiled']} moved to their own month, "
              f"{did['deduped']} second copies and {did['torn']} torn copies taken out, "
              f"{did['unglued']} glued line(s) split; sealed {did['sealed'] or 'none'}; "
              f"re-split {did['split'] or 'none'}; sealed copies taken out "
              f"{did['dropped'] or 'none'}"
              + (f"; NOT sealed, holding a line that is not JSON: {did['held']} "
                 f"(python3 scripts/journal.py --check)" if did["held"] else ""))
        return 0
    if a.check:
        bad, look = archive_problems()
        for x in look:
            print(f"note: {x}")
        for x in bad:
            print(f"BAD: {x}")
        print("the archive keeps its rules" if not bad else f"{len(bad)} problem(s)")
        return 1 if bad else 0
    import subprocess
    root = LOG.parent.parent
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=root,
                       capture_output=True, text=True)
    paths = [p for p in r.stdout.split() if is_archive_path(p)]
    if not paths:
        print("no archive path is conflicted")
        return 0
    up = a.upstream
    if up is None:
        merging = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                                 cwd=root, capture_output=True).returncode == 0
        up = main_side(root) if merging else 2
        if up is None:
            print("refusing: in this merge neither HEAD nor MERGE_HEAD is main (origin/main or "
                  "main), so there is no telling whose archive files stand. Say it: "
                  "--upstream 2 when HEAD is the side whose files stand, --upstream 3 when "
                  "MERGE_HEAD is.")
            return 2
    print(resolve(root, paths, upstream=up))
    return 0


if __name__ == "__main__":
    sys.exit(main())
