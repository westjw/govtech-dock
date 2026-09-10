#!/usr/bin/env python3
"""Stage national conference organisations into data/national_events.json.

WHY THIS IS NOT register_state_events.py
----------------------------------------
That script builds `payload["events"]` from its CSV alone and replaces the
file. Run against a new catalogue it would erase 359 rows, 204 org_urls, 15
directory_urls and 13 promoted flags. There is no merge path in it, and
adding one would change what it means for the chapter file. So this is a
sibling: same envelope, same status vocabulary, MERGE BY KEY.

It also models a different shape. state_events.json is one organisation to
one event by construction - register_state_events skips a repeated org_code
outright. A national body runs several: APA has a national conference, an
online edition, a policy meeting and 47 chapter conferences. The key here is
therefore (org_code, event_name), not org_code.

WHAT IS BEING STAGED, AND WHY NONE OF IT IS ASSERTED
----------------------------------------------------
The source registry says so itself, at the top of its own file: "Everything
below is recalled and unverified except the five events marked VERIFIED."
Five of roughly 840. One directory URL is named in the whole document.

So every row lands `verified: false` and `status: "needs_url"`, which is the
same starting state a chapter event gets, and nothing here reaches
conferences.json. The organisation's own page has to say it first.

HARVEST IS A SEPARATE QUESTION FROM PUBLISH
-------------------------------------------
The owner's ruling: everything eventually reaches the Conferences tab; only
SLED vendor-bearing rows are ever swept. A training event still sells
sponsorships and still belongs on the tab, and an ADJACENT trade show is a
real vendor list that is not this board's audience. Both publish. Neither is
harvested. `harvest` records that, once, here, rather than being re-derived
at every stage by whoever is reading.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "national_events.json"

# The registry's own status legend, verbatim from CONFERENCE_BLOCKS.md.
VENDOR_BEARING = {"VERIFIED", "LIKELY", "TABLETOP", "SPONSOR"}
NO_VENDORS = {"TRAINING"}
SCOPES = {"SLED", "ADJACENT", "PROVIDER", "INDUSTRY"}

NOTE = (
    "National conference organisations staged from CONFERENCE_BLOCKS.md and "
    "sources_national2.csv (Wyeth, 2026-09-09). NOT conferences.json: nothing "
    "here has a verified exhibitor directory, and the source registry states "
    "that everything in it is recalled and unverified except five events. "
    "est_exhibitors is an estimate and is never a count. harvest=false means "
    "the row may reach the Conferences tab and must never be swept - training "
    "events and non-SLED audiences. Rows are keyed (org_code, event_name); a "
    "national body runs several events, unlike a state chapter."
)


def key(org_code: str, event_name: str) -> str:
    """The row identity. A national body runs more than one event.

    THE ORGANISATION'S OWN NAME IS NOT PART OF THE EVENT'S. One catalogue
    writes "AAFCO Annual Meeting" and the other writes "Annual Meeting", and
    they are the same meeting - 47 events were staged twice on that
    difference alone. Worse than untidy: two rows for one event are each
    other's SIBLING, and find_event_directories refuses to hand a directory
    to a row that names nothing its siblings do not. So ARSL's exhibitor
    list, plainly theirs, could not be given to either of ARSL's two rows.
    The duplicates were not noise around the harvest, they were blocking it.
    """
    code = (org_code or "").strip().upper()
    name = (event_name or "").strip()
    if code and name.upper().startswith(code + " "):
        name = name[len(code):].strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{code}::{slug}"[:120]


# Fields only one catalogue carries. sources_national2.csv is the only source
# of a department - and stage_promote refuses any row whose department has no
# CATALOG_PLACE entry - so a collapse that keeps the other row does not merely
# lose a string, it makes that organisation's conference unpromotable forever.
# CONFERENCE_BLOCKS.md is the only source of `block`. Whichever row survives,
# half the record dies unless the fields are unioned.
_UNION = ("block", "department", "vertical", "tier", "source_type",
          "est_exhibitors", "org_name", "org_url", "directory_url",
          "candidate_url", "directory_note", "status", "scope", "harvest",
          "promoted", "verified", "key")


def fuse(a: dict, b: dict) -> dict:
    """One event staged twice, made one row, losing nothing either observed.

    Three cases, and the third is the one that matters. One side empty and
    one filled: take the filled one. Both equal: keep it. Both filled and
    DIFFERENT: never pick - the two catalogues looked at the same event and
    said different things, and that disagreement is a fact worth keeping,
    not one worth flattening. registry_status disagrees on 31 of the 47.
    """
    out = dict(a)
    # the name that names the organisation survives: "AAFCO Annual Meeting"
    # over "Annual Meeting", because it is what a reader can place
    names = [x.get("event_name") or "" for x in (a, b)]
    out["event_name"] = max(names, key=len)
    for f in _UNION:
        av, bv = a.get(f), b.get(f)
        if av in (None, "", False) and bv not in (None, "", False):
            out[f] = bv
    if a.get("registry_status") != b.get("registry_status"):
        out["registry_status"] = a.get("registry_status") or b.get("registry_status")
        out["registry_status_alt"] = b.get("registry_status") or a.get("registry_status")
    origins = []
    for x in (a, b):
        o = x.get("origin")
        origins += o if isinstance(o, list) else ([o] if o else [])
    # a row two documents produced has two origins, and the audit trail is
    # what the field is for
    out["origin"] = sorted(set(origins))
    out["harvest"] = bool(a.get("harvest")) or bool(b.get("harvest"))
    return out


def harvestable(scope: str, status: str) -> bool:
    """Sweep this floor, or only publish it?"""
    return (scope or "SLED").upper() == "SLED" and (status or "").upper() in VENDOR_BEARING


def row(org_code, org_name, event_name, *, vertical="", department="",
        block="", scope="SLED", registry_status="UNKNOWN", est=None,
        source_type="", tier="", origin="") -> dict:
    return {
        "key": key(org_code, event_name),
        "org_code": (org_code or "").strip().upper(),
        "org_name": (org_name or "").strip(),
        "event_name": (event_name or "").strip(),
        "vertical": (vertical or "").strip(),
        # The registry groups by BLOCK ("Block 2 - Parks & Recreation"). That
        # is not a department in this repo's vocabulary, and CATALOG_PLACE
        # maps departments. Keeping them apart means the mapping pass has
        # something real to map rather than a heading to re-parse.
        "block": (block or "").strip(),
        "department": (department or "").strip(),
        "scope": (scope or "SLED").strip().upper(),
        "tier": (tier or "").strip(),
        "est_exhibitors": est,
        "source_type": (source_type or "").strip(),
        # what the registry claimed, kept as its own word
        "registry_status": (registry_status or "UNKNOWN").strip().upper(),
        "harvest": harvestable(scope, registry_status),
        # NOTHING HERE IS OBSERVED YET.
        "verified": False,
        "origin": origin,
        "org_url": None,
        "directory_url": None,
        "status": "needs_url",
        "promoted": False,
    }


# --------------------------------------------------------------- the CSV ----

CSV_FIELDS = ("org_code", "org_name", "event_name", "vertical", "department",
              "tier", "est_exhibitors", "source_type", "scope")


def from_csv(path: pathlib.Path) -> list:
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{path} has no rows")
    missing = [f for f in CSV_FIELDS if f not in rows[0]]
    if missing:
        sys.exit(f"{path} is missing column(s): {missing}")
    out = []
    for r in rows:
        try:
            est = int(str(r.get("est_exhibitors") or "").strip() or 0) or None
        except ValueError:
            est = None
        # The CSV carries status=todo, which is a workflow marker rather than
        # a claim about the floor. source_type is what it actually says: an
        # expo has a hall, a sponsor page is the vendor list.
        st = "LIKELY" if (r.get("source_type") or "").strip() == "expo" else "SPONSOR"
        out.append(row(r["org_code"], r["org_name"], r["event_name"],
                       vertical=r.get("vertical"), department=r.get("department"),
                       scope=r.get("scope"), registry_status=st, est=est,
                       source_type=r.get("source_type"), tier=r.get("tier"),
                       origin="sources_national2.csv"))
    return out


# ------------------------------------------------- CONFERENCE_BLOCKS.md ----
#
# The tables are not one shape. Four appear:
#   | Event | Timing | Status | Registry |
#   | Event type | Status | Registry |
#   | Event | Org | Status | Registry |
#   | Event | Org | Timing | Status | Registry |
# so the header row is read for column positions rather than assumed. A table
# whose header names neither an event nor a status is not an event table and
# is skipped - the document also holds summary and backlog tables.

_H_EVENT = ("event", "event type")
_H_ORG = ("org", "organisation", "organization")
_H_STATUS = ("status",)
ALL_STATUS = VENDOR_BEARING | NO_VENDORS | {"UNKNOWN"} | SCOPES


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _code_from_name(text: str) -> str:
    """A stable code for an organisation that has no acronym."""
    t = re.sub(r"[^A-Za-z0-9]+", "", (text or "")).upper()
    return t[:16] or "UNSPECIFIED"


def _acronym(text: str) -> str:
    """The organisation code a cell names, or "" if it names none.

    Requires a leading token of 3-16 characters carrying at least two capitals
    and no lowercase word after it that would make it a sentence. "NASPD
    (State Park Directors)" yields NASPD; "State associations" yields nothing,
    which is the whole point.
    """
    t = (text or "").strip()
    m = re.match(r"^([A-Z][A-Za-z0-9&/.'-]{2,15})(?:\s|\(|,|$)", t)
    if not m:
        return ""
    tok = m.group(1)
    if sum(1 for ch in tok if ch.isupper()) < 2:
        return ""
    return tok


# The scope legend is not a column. It is a table at the foot of the document,
# "Scope exceptions - non-SLED rows in Blocks 1-10", naming events and
# organisations per block. Parsed separately and applied by name, because a
# row's own status column never carries it.
def scope_exceptions(text: str) -> dict:
    out: dict = {}
    i = text.rfind("Scope exception")
    if i < 0:
        return out
    for line in text[i:].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 3:
            continue
        scope = cells[2].strip().upper()
        if scope not in SCOPES or scope == "SLED":
            continue
        for nm in re.split(r",(?![^(]*\))", cells[1]):
            nm = nm.strip()
            if nm:
                out[nm.lower()] = scope
    return out


def from_blocks(path: pathlib.Path) -> list:
    text = path.read_text()
    exceptions = scope_exceptions(text)
    out, seen = [], set()
    block = org_code = org_name = ""
    hdr = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            block = line[3:].strip()
            hdr = None
            continue
        if line.startswith("### "):
            head = line[4:].strip()
            m = re.match(r"^([A-Z][A-Za-z0-9&/_.'-]{1,16})\s*[—–-]\s*(.+)$", head)
            if m:
                org_code, org_name = m.group(1), m.group(2)
            else:
                # a topic heading ("Aquatics"); the Org column names the body
                org_code, org_name = "", head
            hdr = None
            continue
        if not line.startswith("|"):
            hdr = None
            continue
        cells = _cells(line)
        if set("".join(cells)) <= set("-: "):
            continue                      # the separator row
        low = [c.lower() for c in cells]
        if hdr is None:
            if any(c in _H_EVENT for c in low) and any(c in _H_STATUS for c in low):
                hdr = {"event": next(i for i, c in enumerate(low) if c in _H_EVENT),
                       "status": next(i for i, c in enumerate(low) if c in _H_STATUS),
                       "org": next((i for i, c in enumerate(low) if c in _H_ORG), None)}
            continue
        if len(cells) <= hdr["status"]:
            continue
        ev = cells[hdr["event"]]
        st = cells[hdr["status"]].upper()
        if not ev or st not in ALL_STATUS:
            continue
        code = org_code
        name = org_name
        if hdr["org"] is not None and len(cells) > hdr["org"] and cells[hdr["org"]]:
            name = cells[hdr["org"]]
            # AN ACRONYM IS A CODE; PROSE IS NOT. "NASPD (State Park
            # Directors)" names an organisation. "State associations" and
            # "Chapter events" describe a class of them, and minting a code
            # from those produced STATE, FL, US - 500 distinct organisations
            # where the registry names 252. When the cell is not an acronym
            # the section heading still owns the row.
            # THE ORG COLUMN NAMES THIS ROW'S BODY, so it owns the code -
            # the section heading does not. Inheriting the section's code
            # when the cell had no acronym filed "Major Cities Chiefs Assn"
            # under POLICE and "Fiber Broadband Assn" under UTILITIES, which
            # says two different associations are one.
            code = _acronym(name) or _code_from_name(name)
        if not code:
            # NOT ONE SHARED CODE FOR ALL OF THEM. Esri, Bobit and Nan McKay
            # & Assoc are real organisations that simply do not go by an
            # acronym; filing 121 of them under UNSPECIFIED would collide
            # every one with every other the moment anything groups by
            # organisation.
            code = _acronym(name) or _code_from_name(name)
        # A scope word in the status column IS the scope; the row's vendor
        # status is then unknown rather than assumed.
        scope, status = ("SLED", st) if st not in SCOPES else (st, "UNKNOWN")
        k = key(code, ev)
        if k in seen:
            continue
        seen.add(k)
        # the foot-of-document exceptions table, applied by name
        for probe in (ev, name, code):
            hit = exceptions.get((probe or "").strip().lower())
            if hit:
                scope = hit
                break
        out.append(row(code, name, ev, block=block, scope=scope,
                       registry_status=status, origin="CONFERENCE_BLOCKS.md"))
    return out


# ------------------------------------------------------------- the merge ----


def load() -> dict:
    if OUT.exists():
        doc = json.loads(OUT.read_text())
        # An existing file may predate the stamp, or have been written by a
        # tool that dropped it. Put it back rather than carrying the gap
        # forward - find_event_directories refuses an unstamped registry.
        doc["registry"] = "national"
        return doc
    return {"registry": "national", "note": NOTE, "events": []}


def merge(existing: list, fresh: list) -> tuple:
    """Fresh rows in, observed facts kept, corrected parses not left behind.

    THE POINT OF THIS SCRIPT. Re-running an ingest must never cost an org_url
    somebody's fetch established, a directory_url a judge accepted, or a
    promoted flag. Only the descriptive half is refreshed; everything a later
    stage wrote is carried across untouched.

    AND A CORRECTED PARSE MUST NOT LEAVE A GHOST. The key carries the
    organisation code, so improving how codes are read changes keys - fixing
    the rule that filed "Major Cities Chiefs Assn" under POLICE moved nine
    rows and left nine orphans behind, still on file, still claiming to be
    events. "Never lose work" cannot mean "never fix a parse".

    So a row the parser no longer produces is dropped ONLY when it carries
    nothing observed. One that has a url, a directory or a promotion is kept
    and reported, because that is somebody's work and no re-parse gets to
    decide it was a mistake.
    """
    by_key = {r["key"]: r for r in existing}
    fresh_keys = {f["key"] for f in fresh}
    added = updated = 0
    for f in fresh:
        cur = by_key.get(f["key"])
        if cur is None:
            by_key[f["key"]] = f
            added += 1
            continue
        for field in ("org_name", "event_name", "vertical", "department",
                      "block", "scope", "tier", "est_exhibitors", "source_type",
                      "registry_status", "harvest", "origin"):
            if f.get(field) not in (None, "") and cur.get(field) != f[field]:
                cur[field] = f[field]
                updated += 1
    dropped, orphaned = 0, []
    for k in list(by_key):
        if k in fresh_keys:
            continue
        r = by_key[k]
        if r.get("org_url") or r.get("directory_url") or r.get("promoted"):
            orphaned.append(k)          # kept, and said out loud
            continue
        del by_key[k]
        dropped += 1
    return (sorted(by_key.values(), key=lambda r: r["key"]),
            added, updated, dropped, orphaned)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=pathlib.Path)
    ap.add_argument("--blocks", type=pathlib.Path)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if not a.csv and not a.blocks:
        return ap.error("give --csv, --blocks, or both")

    fresh = []
    if a.csv:
        fresh += from_csv(a.csv)
    if a.blocks:
        fresh += from_blocks(a.blocks)
    # a key can arrive from both inputs; the CSV is the more structured, so
    # it is read first and wins on conflict
    # A DEDUPE THAT DISCARDS A ROW LOSES THE HALF ONLY THAT ROW CARRIES.
    # This used to `continue` on a repeated key, which threw away the blocks
    # row whole - and with it `block`, in every one of the 47 pairs - before
    # merge() ever saw it, with no message.
    seen, deduped = {}, []
    for r in fresh:
        at = seen.get(r["key"])
        if at is None:
            seen[r["key"]] = len(deduped)
            deduped.append(r)
        else:
            deduped[at] = fuse(deduped[at], r)

    payload = load()
    before = len(payload.get("events") or [])
    events, added, updated, dropped, orphaned = merge(
        payload.get("events") or [], deduped)
    payload["note"] = NOTE
    payload["events"] = events

    kept = sum(1 for r in events if r.get("org_url") or r.get("directory_url")
               or r.get("promoted"))
    print(f"  read     {len(deduped)} row(s) from the registry")
    print(f"  on file  {before} -> {len(events)}  (+{added} new, "
          f"{updated} field(s) refreshed, -{dropped} the parser no longer produces)")
    if orphaned:
        print(f"  KEPT     {len(orphaned)} row(s) the parser no longer produces but "
              f"which carry observed work: {orphaned[:4]}")
    print(f"  kept     {kept} row(s) that already carry an observed fact")
    print(f"  harvest  {sum(1 for r in events if r['harvest'])} of {len(events)}"
          f"  ({sum(1 for r in events if not r['harvest'])} publish-only)")
    if not a.write:
        print("\n  dry run. nothing written. pass --write")
        return 0
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
