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
import gzip
import hashlib
import json
import os
import pathlib
import re
import struct
import sys
import tempfile
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
    if pruned or legacy_archive(data).exists():
        normalize(data, live=kept, rows=pruned)
    fd, tmp = tempfile.mkstemp(dir=str(data), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        for r in kept:
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
CAP = 40_000_000                                   # bytes, per file
GITHUB_WARNS = 50 * 1024 * 1024                    # past this, a push is warned
_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
_PART = re.compile(r"(\d{4}-(?:0[1-9]|1[0-2]))(?:\.([1-9][0-9]*))?\.jsonl(\.gz)?")
# what record() writes first: {"id": "...", "at": "2026-10-...
_HEAD_AT = re.compile(rb'\{"id": "[^"\\]*", "at": "(\d{4}-(?:0[1-9]|1[0-2]))')
_GZ_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"   # deflate, no name, mtime 0, OS 255


def _data(data=None) -> pathlib.Path:
    return pathlib.Path(data) if data is not None else LOG.parent


def archive_dir(data=None) -> pathlib.Path:
    """The archive folder: next to the live journal unless told otherwise."""
    return _data(data) / ARCHIVE_DIR


def legacy_archive(data=None) -> pathlib.Path:
    """The old single file. Nothing writes it now; wherever it is found it is
    folded into the month files and removed (normalize)."""
    return _data(data) / LEGACY_ARCHIVE


def is_archive_path(path: str) -> bool:
    """A repo-relative path that belongs to the archive, old file included."""
    return path == f"data/{LEGACY_ARCHIVE}" or path.startswith(f"data/{ARCHIVE_DIR}/")


def part_name(month: str, n: int, sealed: bool) -> str:
    return f"{month}{'' if n == 1 else f'.{n}'}.jsonl{'.gz' if sealed else ''}"


def parse_part(name: str) -> tuple[str, int, bool] | None:
    """(month, part number, sealed) for a name part_name() writes, else None."""
    m = _PART.fullmatch(name)
    if not m or m.group(2) == "1":
        return None
    return m.group(1), int(m.group(2) or 1), bool(m.group(3))


def month_of(row, today: dt.date | None = None) -> str:
    """The month a row is archived under: its own "at", else its id's date."""
    if isinstance(row, dict):
        for v in (row.get("at"), row.get("id")):
            s = str(v or "")[:7]
            if _MONTH.fullmatch(s):
                return s
    return (today or dt.date.today()).isoformat()[:7]


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
    """The month a line's own at (or id) names; None for a line that does not
    say - such a line is never moved to another month's file."""
    m = _HEAD_AT.match(line)
    if m:
        return m.group(1).decode()
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if isinstance(row, dict):
        for v in (row.get("at"), row.get("id")):
            s = str(v or "")[:7]
            if _MONTH.fullmatch(s):
                return s
    return None


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
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".", suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, p)


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


def archived_lines(data=None, legacy: bool = True):
    """Every archived line, part by part in reading order - and then the old
    single file's, if one is still waiting to be folded in."""
    for _m, _n, _s, p in archive_parts(data):
        yield from lines_of(p.name, p.read_bytes())
    old = legacy_archive(data)
    if legacy and old.is_file():
        yield from lines_of(old.name, old.read_bytes())


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
        self.did = {"filed": 0, "known": 0, "folded": 0, "refiled": 0, "sealed": [],
                    "split": []}

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

    def file(self, line: bytes, m: str | None = None, check: bool = True) -> bool:
        """Append one line to its month. False when the line is already
        archived (checked when `check`)."""
        m = m or line_month(line, self.today)
        if check and _digest(line) in self.seen(m):
            self.did["known"] += 1
            return False
        p = self._tail(m, len(line) + 1)
        size = self._sz(p)                 # before the append: a new part is 0
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as fh:
            fh.write(line + b"\n")
        self._size[p] = size + len(line) + 1
        if m in self._seen:
            self._seen[m].add(_digest(line))
        self.did["filed"] += 1
        return True

    def refile(self, m: str) -> None:
        """A row sitting in ANOTHER month's open part goes to its own month, by
        line; the part keeps the rest. Git puts one there when a merge follows
        the old single file into a month file it judges a rename of it - a
        branch from before the migration, merging main - and a plain union of
        two versions can too."""
        parts = self.parts.get(m, {})
        for n in sorted(n for (n, s) in parts if not s):
            p = parts.get((n, False))
            lines = self._lines(p) if p is not None else []
            away = [ln for ln in lines if own_month(ln) not in (None, m)]
            if not away:
                continue
            keep = [ln for ln in lines if own_month(ln) in (None, m)]
            if keep:
                blob = b"".join(ln + b"\n" for ln in keep)
                _write_bytes(p, blob)
                self._size[p] = len(blob)
            else:
                parts.pop((n, False))
                self._size.pop(p, None)
                p.unlink(missing_ok=True)
            self._seen.pop(m, None)
            for ln in away:
                self.file(ln, own_month(ln), check=True)
            self.did["refiled"] += len(away)

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
            for ln in self._lines(p):
                self.file(ln, m, check=True)
            p.unlink(missing_ok=True)
        opens = self.proper_open(m)
        if not any(self._sz(parts[(n, False)]) > CAP for n in opens):
            return
        lines = [ln for n in opens for ln in self._lines(parts[(n, False)])]
        want = {opens[0] + i: c for i, c in enumerate(_chunks(lines, CAP))}
        for n, blob in want.items():
            p = self.dir / part_name(m, n, False)
            _write_bytes(p, blob)
            parts[(n, False)] = p
            self._size[p] = len(blob)
        for n in opens:
            if n not in want:
                p = parts.pop((n, False))
                self._size.pop(p, None)
                p.unlink(missing_ok=True)
        self.did["split"].append(m)

    def seal(self, m: str) -> None:
        """Every open part of a finished month into gzip part(s), numbered
        after the last sealed one, then the open parts removed. A line a
        sealed part already holds, or one sealed a moment ago, is not sealed
        twice."""
        parts = self.parts.get(m, {})
        opens = self.proper_open(m)
        if not opens:
            return
        sm = self.sealed_max(m)
        seen = self._sealed_digests(m) if sm else set()
        lines, away = [], []
        for n in opens:
            for ln in self._lines(parts[(n, False)]):
                if own_month(ln) not in (None, m):
                    away.append(ln)          # another month's row: never sealed here
                    continue
                d = _digest(ln)
                if d not in seen:
                    seen.add(d)
                    lines.append(ln)
        for i, blob in enumerate(_seal_blobs(lines) if lines else []):
            p = self.dir / part_name(m, sm + 1 + i, True)
            _write_bytes(p, blob)
            parts[(sm + 1 + i, True)] = p
        for n in opens:
            p = parts.pop((n, False))
            self._size.pop(p, None)
            p.unlink(missing_ok=True)
        self._seen.pop(m, None)
        self.did["sealed"].append(m)
        for ln in away:
            self.file(ln, own_month(ln), check=True)
        self.did["refiled"] += len(away)


def finished(month: str, live: list, today: dt.date | None = None) -> bool:
    """Can this journal never hand the month another row: the calendar is
    past it and nothing left in the live journal is from it or before it."""
    today = today or dt.date.today()
    if month >= today.isoformat()[:7]:
        return False
    return all(month_of(r, today) > month for r in live)


def normalize(data=None, live: list | None = None, rows=(), lines=(),
              today: dt.date | None = None, full: bool = False,
              seal: bool = True) -> dict:
    """File rows into the archive and bring it to its rules. Returns what it did.

      rows   journal entries just pruned from the live journal (dicts). An
             open month is appended to without a read; a row for a month
             with a sealed part is checked first.
      lines  archived lines from somewhere else - another side of a merge,
             a desk's snapshot. Every one is checked; only what this archive
             lacks is filed.
      live   the live journal as it stands after this write (read from disk
             when not given): what decides which months are finished.
      full   also read every open part and move a row of another month to
             its own (refile) - what a merge can leave. --fold, --resolve and
             the desk publisher do; the everyday write does not read.
      seal   seal the finished months (False for a resolver in the middle of
             a merge or rebase: it settles rows and nothing more, and the next
             write or publish seals).

    In order: the old single file is folded in and removed; `rows`, then
    `lines`, are filed; with `full`, rows in another month's file move;
    strays are folded and oversized open parts re-split; every finished
    month is sealed."""
    a = _Archive(data, today)
    old = legacy_archive(a.data)
    if old.is_file():
        for ln in a._lines(old):
            a.did["folded"] += a.file(ln, check=True)
        old.unlink()
    for r in rows or ():
        m = month_of(r, a.today)
        a.file(json.dumps(r).encode(), m, check=bool(a.sealed_max(m)))
    for ln in lines or ():
        a.file(ln, check=True)
    if full:
        for m in sorted(a.parts):
            a.refile(m)
    for m in sorted(a.parts):
        a.tidy(m)
    if not seal:
        return a.did
    live = _live_rows(a.data) if live is None else live
    for m in sorted(a.parts):
        if a.proper_open(m) and finished(m, live, a.today):
            a.seal(m)
    return a.did


def archive_problems(data=None) -> tuple[list[str], list[str]]:
    """(what breaks the archive's rules and needs a person, what the next
    write, publish or `--fold` puts right by itself) on disk."""
    bad, look = [], []
    d = archive_dir(data)
    fold = "python3 scripts/journal.py --fold"
    if legacy_archive(data).is_file():
        look.append(f"{LEGACY_ARCHIVE} is still there: the next journal write folds it "
                    f"into {ARCHIVE_DIR}/ (or: {fold})")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    for name in names:
        if parse_part(name) is None and not name.startswith("."):
            bad.append(f"{ARCHIVE_DIR}/{name} is not a part name ({part_name('2026-10', 1, True)}, "
                       f"{part_name('2026-10', 2, False)}, ...): nothing reads it")
    sealed_max: dict = {}
    for m, n, s, p in archive_parts(data):
        if s:
            sealed_max[m] = max(sealed_max.get(m, 0), n)
    for m, n, s, p in archive_parts(data):
        raw = p.read_bytes()
        try:
            lines = lines_of(p.name, raw)
        except (OSError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
            bad.append(f"{p.name} does not decompress: {exc}")
            continue
        if len(raw) > CAP and len(lines) > 1:
            (bad if len(raw) > GITHUB_WARNS else look).append(
                f"{p.name} is {len(raw):,} bytes, past the cap of {CAP:,}: the next journal "
                f"write re-splits it (or: {fold})")
        if not s and n <= sealed_max.get(m, 0):
            look.append(f"{p.name} is open beside a sealed part numbered {sealed_max[m]} (a "
                        f"merge kept a file the other side sealed): the next journal write "
                        f"folds it (or: {fold})")
        away = 0
        for ln in lines:
            om = own_month(ln)
            if om is None:
                try:
                    json.loads(ln)
                except ValueError:
                    look.append(f"{p.name} holds a line that is not JSON ({len(ln):,} bytes)")
                continue
            if om != m:
                away += 1
        if away and s:
            bad.append(f"{p.name} is sealed with {away} row(s) of another month in it")
        elif away:
            look.append(f"{p.name} holds {away} row(s) of another month (a merge put them "
                        f"there): {fold} moves them, as the next desk publish does")
    return bad, look


def resolve(root, paths: list[str], upstream: int = 2) -> str:
    """Settle the archive paths of a stopped rebase (upstream=2, git's stage
    for the branch being rebased onto) or merge (upstream=3, the branch being
    merged in). The upstream's version of each path stands - a sealed file
    main has is never replaced - and every line any stage holds that the
    archive then lacks is filed by the rules above, then staged. Nothing is
    sealed here: the next write or publish does that."""
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
    lines: list = []
    for p in paths:
        for n in (1, upstream, other):
            lines += lines_of(p, stage(n, p))
    for p in paths:
        up = stage(upstream, p)
        # GIT FOLLOWS THE OLD SINGLE FILE INTO A PART IT JUDGES A RENAME OF
        # IT, so one side's version of a part can be that whole file. It
        # never stands over the other side's proper part, whichever way the
        # merge runs (main into the migration's branch, or the branch into
        # main); its rows are filed with every other stage's.
        theirs = stage(other, p)
        if not proper(p, up) and proper(p, theirs):
            up = theirs
        subprocess.run(["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", p],
                       cwd=root, check=True, capture_output=True)
        f = root / p
        if up is None:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(up)
    did = normalize(root / "data", lines=lines, full=True, seal=False)
    for p in (f"data/{ARCHIVE_DIR}", f"data/{LEGACY_ARCHIVE}"):
        listed = subprocess.run(["git", "ls-files", "--", p], cwd=root, capture_output=True,
                                text=True).stdout.strip()
        if listed or (root / p).exists():
            subprocess.run(["git", "add", "-A", "--", p], cwd=root, check=True,
                           capture_output=True)
    return (f"the journal archive: {len(paths)} path(s) settled, {did['filed']} row(s) "
            f"filed, {did['known']} already archived, {did['refiled']} moved to their own "
            f"month")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="The admin journal's archive, data/journal_archive/.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fold", action="store_true",
                   help="fold the old single file and any stray part into the month files, "
                        "and seal every finished month")
    g.add_argument("--check", action="store_true", help="say what breaks the archive's rules")
    g.add_argument("--resolve", action="store_true",
                   help="inside a stopped merge of main (or rebase onto it): settle the "
                        "conflicted archive paths and stage them")
    ap.add_argument("--upstream", type=int, choices=(2, 3),
                    help="git's stage number for main (default: 3 in a merge, 2 in a rebase)")
    a = ap.parse_args(argv)
    if a.fold:
        did = normalize(full=True)
        print(f"folded {did['folded']} row(s) from {LEGACY_ARCHIVE}, filed {did['filed']}, "
              f"{did['known']} already archived, {did['refiled']} moved to their own month; "
              f"sealed {did['sealed'] or 'none'}; re-split {did['split'] or 'none'}")
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
        up = 3 if merging else 2
    print(resolve(root, paths, upstream=up))
    return 0


if __name__ == "__main__":
    sys.exit(main())
