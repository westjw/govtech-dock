#!/usr/bin/env python3
"""Land a conference exhibitor sweep.

Input: a staged JSON file of exhibitors judged by an agent pass:

  {"conference": "GFOA 2026", "exhibitors": [
      {"name": "OpenGov", "website": "https://opengov.com",
       "is_govtech": true, "vertical": "Finance & ERP",
       "description": "Budgeting and ERP for local government"},
      {"name": "Pitney Bowes", "website": "...", "is_govtech": false,
       "vertical": "Mailing equipment"}]}

The owner's rulings (2026-08-23) drive the three-way split:
  - already on file (companies OR suppliers): extend its exhibited-at note,
    idempotently. Nothing else changes: a sweep never downgrades research.
  - new and NOT govtech: a suppliers.json row, catalogued but unresearched,
    in the exact shape the PWX sweep established.
  - new and govtech: staged to data/conference_intake/govtech_candidates.json
    for the research pass. A tech vendor never enters companies.json from a
    booth list alone: sector, category, ATS and description need research,
    and validate() would refuse a hollow row anyway - correctly.

  python scripts/conference_intake.py staged/gfoa.json [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STAGE = DATA / "conference_intake"

SUFFIX = re.compile(r"\b(inc|llc|corp(oration)?|co|company|ltd|lp|group|"
                    r"holdings?|international|intl|usa?)\b\.?", re.I)


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import admin                                            # noqa: E402

def norm(name: str) -> str:
    # parentheticals go the way kebab() sends them, or "SoundThinking
    # (ShotSpotter)" dedupes differently than it ids and lands twice
    s = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def kebab(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def add_event(desc: str | None, event: str) -> str:
    """'... - exhibited at PWX 2026' -> '... - exhibited at PWX 2026, X'."""
    desc = desc or ""
    m = re.search(r"exhibited at ([^;.\n]+)", desc)
    if not m:
        return (desc + (" - " if desc else "") + f"exhibited at {event}").strip()
    events = [e.strip() for e in m.group(1).split(",")]
    if event in events:
        return desc
    return desc.replace(m.group(1), m.group(1) + ", " + event)


def issued_tags() -> set:
    """Every event tag the catalog has ever issued, current and prior."""
    confs = json.loads((DATA / "conferences.json").read_text())["conferences"]
    out = {c["event_tag"] for c in confs if c.get("event_tag")}
    for c in confs:
        out.update(c.get("prior_tags") or [])
    return out


def resolve_tag(staged: dict, override: str | None) -> tuple[str | None, str | None]:
    """The string that lands in descriptions, or why it must not.

    THE TAG, NEVER THE NAME. This used to fall back to staged["conference"] -
    "AWWA ACE", "3CMA", "AIRA National Meeting (American Immunization
    Registry Association)" - and on 2026-09-02 wrote those into 988
    descriptions. A tag is "<name> <year>", assigned in conferences.json so
    acronyms cannot collide, and selftest refuses any description carrying a
    tag the catalog never issued. So the catalog is asked here, before the
    write, and a staged file with no issued tag is refused rather than
    guessed at.
    """
    tag = (override or staged.get("event_tag") or "").strip()
    if not tag:
        return None, ("the staged file carries no event_tag and none was given; "
                      "conferences.json assigns them")
    if tag not in issued_tags():
        return None, (f"{tag!r} is not a tag conferences.json ever issued - a "
                      f"description carrying it would fail selftest")
    return tag, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--event-tag",
                    help="the string that lands in descriptions ('GFOA 2026'). "
                         "Comes from conferences.json, because acronyms collide: "
                         "three associations are called APPA and a bare "
                         "'APPA 2026' cannot be read six months later. Overrides "
                         "whatever the staged file called itself.")
    ap.add_argument("--default-sector", default="General Gov",
                    help="sector for suppliers the classifier left unplaced; "
                         "set from the conference's buyer block, so a courts "
                         "vendor's stenography firm files under Courts & "
                         "Justice rather than the generic bucket")
    a = ap.parse_args()

    staged = json.loads(pathlib.Path(a.file).read_text())
    event, why = resolve_tag(staged, a.event_tag)
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return 1
    # THROUGH admin, so the write below is journalled. This script was one of
    # the seven that wrote companies.json directly; the day it extended 196
    # descriptions with "GFOA 2026", every one of those records disagreed
    # with the journal's last after-image and nothing could say which was
    # right. read_companies() keeps the before-image save_companies needs.
    companies = admin.read_companies()
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    valid_sectors = {s["name"] for s in
                     json.loads((DATA / "schema.json").read_text())["sectors"]}
    if a.default_sector not in valid_sectors:
        raise SystemExit(f"unknown --default-sector {a.default_sector!r}")

    by_name: dict[str, tuple[str, dict]] = {}
    for kind, rows in (("company", companies), ("supplier", suppliers)):
        for r in rows:
            by_name[norm(r["name"])] = (kind, r)
            for aka in r.get("also_known_as", []) or []:
                by_name[norm(aka)] = (kind, r)

    tagged = new_suppliers = 0
    candidates = []
    seen_in_file: set[str] = set()
    for ex in staged["exhibitors"]:
        key = norm(ex["name"])
        if not key or key in seen_in_file:
            continue
        seen_in_file.add(key)
        hit = by_name.get(key)
        if hit:
            _, row = hit
            row["description"] = add_event(row.get("description"), event)
            tagged += 1
        elif ex.get("is_govtech"):
            candidates.append({"name": ex["name"], "website": ex.get("website"),
                               "vertical": ex.get("vertical"),
                               "description": ex.get("description"),
                               "source_event": event})
        else:
            suppliers.append({
                "id": kebab(ex["name"]), "name": ex["name"],
                "website": ex.get("website"), "location": None,
                "year_founded": None,
                "sector": (ex.get("sector")
                           if ex.get("sector") in valid_sectors
                           else a.default_sector),
                "category": "Suppliers & Services",
                "description": add_event(ex.get("vertical")
                                         or ex.get("description"), event),
                "ats": {"type": "unknown", "ref": None},
                "hiring": {"status": "Unknown", "note": "not researched",
                           "roles": [], "checked": None},
            })
            new_suppliers += 1

    print(f"{event}: {len(staged['exhibitors'])} staged -> {tagged} already on "
          f"file (note extended), {new_suppliers} new suppliers, "
          f"{len(candidates)} govtech candidates for research")
    if a.dry_run:
        return 0

    err = admin.validate(companies)
    if err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    admin.write_atomic("suppliers.json", suppliers)
    # force: the counts were printed above, which is what journal.BLAST asks
    # for before a write touching more than 25 records. A dry run is the
    # person looking; the run without it is the person saying go.
    bad = admin.save_companies(
        companies, "conference-intake",
        f"{event}: {tagged} on file tagged, {new_suppliers} suppliers filed",
        by="agent:conference-intake", force=True)
    if bad:
        print(f"refused: {bad}", file=sys.stderr)
        return 1
    STAGE.mkdir(exist_ok=True)
    cand_path = STAGE / "govtech_candidates.json"
    existing = json.loads(cand_path.read_text()) if cand_path.exists() else []
    have = {norm(c["name"]) for c in existing}
    for c in candidates:
        if norm(c["name"]) not in have:
            existing.append(c)
    cand_path.write_text(json.dumps(existing, indent=1, ensure_ascii=False))
    print(f"candidates on file: {len(existing)}")
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
