#!/usr/bin/env python3
"""Three-way merges of the data files, on PARSED JSON, never on text.

WHY. The desk admin writes companies.json with indent 1 and the nightly
refresh writes it with indent 2, so git sees every line of a 6 MB file change
on both sides and a text merge of the owner's rulings onto the nightly's
hiring refresh is a conflict on every line. Parsed, they almost never touch
the same thing: the nightly changes `hiring` on every company and `ats` on a
few; a person changes posts_at, a name, a parent. On 2026-09-27 the owner's 27
rulings were merged onto four upstream commits exactly this way, by hand, and
checked two independent ways. This module is that hand merge, written down,
so the 15-minute publisher and the nightly's push step can both do it.

THE RULE, per record and per top-level field:
  - a field changed on one side only takes that side's value;
  - a field changed the same way on both sides is fine;
  - a field changed DIFFERENTLY on both sides is a CONFLICT - nothing is
    guessed, the caller stops and a person is told;
  - a record deleted on one side (a merge of duplicates, a demotion) whose
    only change on the other side is in UPSTREAM_ONLY fields is deleted;
    any other change to a record the other side deleted is a conflict;
  - a record added on one side is added.

Serialisation is the caller's: companies.json is committed with indent 2
(what refresh.py writes), decision files with admin.write_atomic's indent 1.
"""
from __future__ import annotations

import json
import re

# What the nightly may change on a record the owner merged away without that
# being a conflict: the daily hiring read and a discovered board. Measured
# over the last 12 commits touching companies.json (2026-09-28): every bot
# refresh changed only `hiring`, discovery only `ats`.
UPSTREAM_ONLY = ("hiring", "ats")

_ABSENT = object()


class Conflict(Exception):
    pass


def _changed_fields(a: dict, b: dict) -> set:
    keys = set(a) | set(b)
    return {k for k in keys if a.get(k, _ABSENT) != b.get(k, _ABSENT)}


def merge_records(base: dict, ours: dict, theirs: dict, *, key: str,
                  deletable: tuple = UPSTREAM_ONLY) -> tuple[dict, list]:
    """Three-way over {id: record}. Returns (merged, conflicts)."""
    merged: dict = {}
    conflicts: list = []
    for rid in list(dict.fromkeys(list(theirs) + list(ours) + list(base))):
        b, o, t = base.get(rid), ours.get(rid), theirs.get(rid)
        if o == t:
            if o is not None:
                merged[rid] = o
            continue
        if b is None:                     # added on one or both sides
            if o is None:
                merged[rid] = t
            elif t is None:
                merged[rid] = o
            else:
                conflicts.append(f"{key}:{rid} added differently on both sides")
                merged[rid] = t
            continue
        if o is None or t is None:        # deleted on one side
            kept, gone_side = (t, "ours") if o is None else (o, "theirs")
            changed = _changed_fields(b, kept) if kept is not None else set()
            if kept is None or changed <= set(deletable):
                continue                  # the deletion stands
            conflicts.append(f"{key}:{rid} deleted by {gone_side} but changed "
                             f"on the other side ({', '.join(sorted(changed))})")
            merged[rid] = kept
            continue
        out = dict(t)
        oc, tc = _changed_fields(b, o), _changed_fields(b, t)
        for f in oc:
            if f in tc and o.get(f, _ABSENT) != t.get(f, _ABSENT):
                conflicts.append(f"{key}:{rid}.{f} changed differently on both sides")
                continue
            if f in o:
                out[f] = o[f]
            else:
                out.pop(f, None)
        merged[rid] = out
    return merged, conflicts


def merge_companies(base: list, ours: list, theirs: list) -> tuple[list, list]:
    """companies.json: a list keyed by id. Upstream's order is kept; a record
    only ours has goes where it sits in ours."""
    B = {c["id"]: c for c in base if isinstance(c, dict) and c.get("id")}
    O = {c["id"]: c for c in ours if isinstance(c, dict) and c.get("id")}
    T = {c["id"]: c for c in theirs if isinstance(c, dict) and c.get("id")}
    merged, conflicts = merge_records(B, O, T, key="companies")
    order = [c["id"] for c in theirs if c.get("id") in merged]
    seen = set(order)
    for c in ours:
        if c.get("id") in merged and c["id"] not in seen:
            order.append(c["id"])
            seen.add(c["id"])
    return [merged[i] for i in order], conflicts


def merge_list_by_id(base: list, ours: list, theirs: list, name: str) -> tuple[list, list]:
    """A list of records with ids (suppliers.json). No field may be deleted
    silently on either side: a deletion only stands against an unchanged
    record."""
    B = {c["id"]: c for c in base if isinstance(c, dict) and c.get("id")}
    O = {c["id"]: c for c in ours if isinstance(c, dict) and c.get("id")}
    T = {c["id"]: c for c in theirs if isinstance(c, dict) and c.get("id")}
    merged, conflicts = merge_records(B, O, T, key=name, deletable=())
    order = [c["id"] for c in theirs if c.get("id") in merged]
    seen = set(order)
    order += [c["id"] for c in ours if c.get("id") in merged and c["id"] not in seen]
    return [merged[i] for i in order], conflicts


def merge_dict(base: dict, ours: dict, theirs: dict, name: str) -> tuple[dict, list]:
    """A decision file keyed by id. One nested level is merged key by key
    (admin_dismissed is {queue: {key: record}})."""
    merged: dict = {}
    conflicts: list = []
    for k in list(dict.fromkeys(list(theirs) + list(ours) + list(base))):
        b, o, t = base.get(k, _ABSENT), ours.get(k, _ABSENT), theirs.get(k, _ABSENT)
        if o == t:
            if o is not _ABSENT:
                merged[k] = o
            continue
        if all(isinstance(x, dict) or x is _ABSENT for x in (b, o, t)) and \
                (isinstance(o, dict) and isinstance(t, dict)) and \
                all(isinstance(v, dict) for v in list(o.values()) + list(t.values())):
            sub, c = merge_records(b if isinstance(b, dict) else {}, o, t, key=f"{name}:{k}",
                                   deletable=())
            merged[k] = sub
            conflicts += c
            continue
        if o == b:
            if t is not _ABSENT:
                merged[k] = t
        elif t == b:
            if o is not _ABSENT:
                merged[k] = o
        else:
            conflicts.append(f"{name}:{k} changed differently on both sides")
            if t is not _ABSENT:
                merged[k] = t
    return merged, conflicts


def merge_lines(base: list[str], ours: list[str], theirs: list[str]) -> list[str]:
    """An append-only JSONL file (the journal, identity labels): every line
    either side has, once, ordered by the entry's `at` where it has one.
    A line is identified by its whole content."""
    seen: dict = {}
    for line in theirs + ours:
        s = line.strip()
        if s and s not in seen:
            seen[s] = None

    def at(line: str) -> str:
        try:
            return str(json.loads(line).get("at") or "")
        except ValueError:
            return ""
    return sorted(seen, key=at)


# ------------------------------------------------------------------ by file

# Files a build DRAWS. They are never merged: the side that is building takes
# its own, and the publisher's quick rebuild redraws the board afterwards.
GENERATED = ("data/board.json", "data/removed.json", "data/latest_diff.json",
             "data/render_attempts.json", "data/refresh_render_attempts.json")
GENERATED_DIRS = ("data/detail/", "data/history/", "data/hiring_history/",
                  "exports/")

# journal.SHAPES files: a record is not one top-level key, so they are merged
# through the journal's own reader and writer.
SHAPED = ("manual.json", "task_notes.json", "submissions.json", "logic_notes.json")


# Crawl records, one per company, that carry the day they were read. Both
# sides reading the same company is not a disagreement: the later read is the
# truth (a desk "check their news now" against news.yml's sweep).
FRESHEST = {"news.json": "checked_on", "site_pages_index.json": "fetched_on"}


def merge_freshest(base: dict, ours: dict, theirs: dict, stamp: str) -> dict:
    out = dict(theirs)
    for k in dict.fromkeys(list(ours) + list(base)):
        o, t, b = ours.get(k), theirs.get(k), base.get(k)
        if o == t or o == b:
            continue                       # theirs already has it, or ours did not touch it
        if t == b or t is None:
            if o is None:
                out.pop(k, None)
            else:
                out[k] = o
            continue
        if o is None:
            continue
        if str((o or {}).get(stamp) or "") > str((t or {}).get(stamp) or ""):
            out[k] = o
    return out


def is_generated(path: str) -> bool:
    return path in GENERATED or any(path.startswith(d) for d in GENERATED_DIRS)


def dumps_like(obj, like: str | None) -> str:
    """Serialise the way the file on the other side is serialised, so a merge
    rewrites only what it changed: companies.json is committed with indent 2
    (refresh.py), decision files with indent 1 (admin.write_atomic)."""
    indent = 1
    ascii_only = True
    if like:
        m = re.search(r"\n( +)\S", like)
        indent = len(m.group(1)) if m else None
        ascii_only = like.isascii()
    return json.dumps(obj, indent=indent, ensure_ascii=ascii_only) + "\n"


def _loads(text: str | None):
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Conflict(f"not JSON: {exc}") from exc


def merge_text(path: str, base: str | None, ours: str | None,
               theirs: str | None) -> tuple[str | None, list]:
    """Three-way merge of one file's TEXT. Returns (merged text, conflicts);
    merged is None when the file is deleted. `ours` is the side whose edits
    are being carried onto `theirs` (the publisher: the desk; the nightly's
    resolver: the bot's replayed commit)."""
    if ours == theirs:
        return ours, []
    if ours == base:
        return theirs, []
    if theirs == base:
        return ours, []
    name = path.rsplit("/", 1)[-1]
    if is_generated(path):
        return None, [f"{path}: a generated file changed on both sides; it is "
                      f"redrawn, never merged"]
    if ours is None or theirs is None:
        return (theirs if ours is None else ours), [
            f"{path}: deleted on one side and changed on the other"]
    try:
        if name.endswith(".jsonl"):
            lines = merge_lines((base or "").splitlines(), ours.splitlines(),
                                theirs.splitlines())
            return "\n".join(lines) + ("\n" if lines else ""), []
        b, o, t = _loads(base), _loads(ours), _loads(theirs)
    except Conflict as exc:
        return theirs, [f"{path}: {exc}"]
    if name in FRESHEST and isinstance(o, dict) and isinstance(t, dict):
        merged, conflicts = merge_freshest(b if isinstance(b, dict) else {}, o, t,
                                           FRESHEST[name]), []
    elif path == "data/companies.json" and isinstance(o, list) and isinstance(t, list):
        merged, conflicts = merge_companies(b if isinstance(b, list) else [], o, t)
    elif name in SHAPED:
        import journal
        B = journal.snapshot(b, name) if b is not None else {}
        merged_map, conflicts = merge_records(B, journal.snapshot(o, name),
                                              journal.snapshot(t, name),
                                              key=name, deletable=())
        merged = journal.as_payload(t, merged_map, name)
    elif isinstance(o, list) and isinstance(t, list) and all(
            isinstance(x, dict) and x.get("id") for x in o + t):
        merged, conflicts = merge_list_by_id(b if isinstance(b, list) else [], o, t, name)
    elif isinstance(o, dict) and isinstance(t, dict):
        merged, conflicts = merge_dict(b if isinstance(b, dict) else {}, o, t, name)
    else:
        return theirs, [f"{path}: changed on both sides and not a shape that "
                        f"merges by record"]
    return dumps_like(merged, theirs), conflicts
