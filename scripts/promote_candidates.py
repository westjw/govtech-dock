#!/usr/bin/env python3
"""Promote researched conference candidates into companies.json.

A candidate arrives from an exhibitor floor with a name, a website and a
guess. This script only accepts one once a research pass has answered the
questions a card actually needs: is this a govtech product company, what do
they sell and to whom, which sector and category, where are they.

Deliberately NOT here: applicant-tracking discovery. Cards land with
ats.type "unknown", which the schema already means as "needs discovery" and
which refresh.py skips. Hiring status stays "Unknown" rather than "None
found", because nothing has looked yet and a false "None found" silently
deletes a warm door.

Three outcomes, and only the first writes a company:
  govtech       -> a card, validated against the same invariants selftest
                   enforces, with source_event recorded
  supplier      -> moved to suppliers.json; research disagreed with the
                   floor's first read
  scope_review  -> horizontal vendors (data platforms, general IT) that sell
                   to government among everyone else. Not a call this script
                   makes: it queues them for a person, the way the federal
                   roles were queued.

  python scripts/promote_candidates.py researched/*.json [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

SUFFIX = re.compile(r"\b(inc|llc|corp(oration)?|co|company|ltd|lp|group|"
                    r"holdings?|international|intl|usa?)\b\.?", re.I)



def _issued_event_tags() -> set:
    """Every event tag data/conferences.json has actually issued.

    Includes prior_tags: an event that renamed still issued the old tag, and
    the descriptions written under it are not wrong.
    """
    import json as _json
    path = ROOT / "data" / "conferences.json"
    try:
        raw = _json.loads(path.read_text())
    except (OSError, _json.JSONDecodeError):
        return set()
    confs = raw if isinstance(raw, list) else raw.get("conferences", [])
    out = set()
    for c in confs:
        if c.get("event_tag"):
            out.add(c["event_tag"])
        out.update(c.get("prior_tags") or [])
    return out

def norm(name: str) -> str:
    # parentheticals go the way kebab() sends them, or "SoundThinking
    # (ShotSpotter)" dedupes differently than it ids and lands twice
    s = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", SUFFIX.sub(" ", s)).strip()


# THE SAME COMPANY UNDER A LOOSER NAME, which is what a conference floor
# writes. norm() strips legal suffixes, so "Alteryx, Inc" already matched
# "Alteryx" - and nothing matched "Tyler Tech" to Tyler Technologies,
# "Sagitec" to Sagitec Solutions, "Kofile Technologies" to Kofile, or
# "GoGuardian and Pear Deck Learning" to GoGuardian. On 2026-09-02 that was
# 28 of 59 proposed cards: twenty-eight duplicate records of companies the
# board already tracks, which is the pile the Duplicates queue exists to
# prevent rather than fill.
#
# So one normalised name containing the other, on a word boundary, is a
# match. SIX CHARACTERS MINIMUM either side, because "CORE" or "Vue" inside
# a longer name is a coincidence and "Sagitec" inside "Sagitec Solutions" is
# not. It reports WHICH name it matched, so a wrong match is visible rather
# than a silent skip.
def same_company(key: str, known: dict) -> str | None:
    if key in known:
        return known[key]
    if len(key) < 6:
        return None
    for other, real in known.items():
        if len(other) < 6:
            continue
        a, b = (key, other) if len(key) <= len(other) else (other, key)
        if a == b or re.search(rf"(^|\s){re.escape(a)}(\s|$)", b):
            return real
    return None


# A DISTINCTIVE WORD SHARED WITH SOMETHING ALREADY ON FILE. same_company
# above catches a name that contains another whole; it does not catch "RTA"
# against "RTA Fleet Management", "CORE" against "CORE Business
# Technologies", "Slate" against "Slate Solutions", "Vue, Xylem" against
# "Xylem", or a singular against a plural. Ten of thirty proposed cards were
# that shape on 2026-09-02.
#
# Tuning the matcher further is the wrong move: some of those ARE the same
# company and some are two companies sharing a word, and no regex tells them
# apart. So a shared distinctive word does not skip and does not write - it
# QUEUES, in the Duplicates file a person already rules from. Agents propose,
# people rule, and the queue exists precisely for this.
#
# GENERIC WORDS DO NOT COUNT, or every "Solutions" on a conference floor
# would match every other one.
GENERIC = {
    "solutions", "solution", "systems", "system", "software", "technologies",
    "technology", "services", "service", "group", "company", "corp", "inc",
    "llc", "government", "public", "national", "american", "data", "cloud",
    "digital", "smart", "city", "gov", "the", "and", "for", "of", "by",
    "management", "consulting", "partners", "associates", "international",
    "global", "enterprise", "platform", "network", "media", "health", "care",
}


def _words(name: str) -> set:
    """Distinctive words in a name, INCLUDING what is in parentheses.

    norm() drops parentheticals on purpose, so an id stays stable - but a
    parenthetical is exactly where a brand name lives. "Cybersoft
    Technologies (PrimeroEdge)" normalised to "cybersoft technologies", so
    a floor listing called "SchoolCafe + PrimeroEdge" matched nothing and
    was about to become a second card for a company already on the board.
    """
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    return {w for w in words if len(w) >= 3 and w not in GENERIC}


def shares_a_name(key_name: str, by_word: dict) -> str | None:
    """An existing record that shares a distinctive word. Returns its name."""
    for w in _words(key_name):
        if w in by_word:
            return by_word[w]
    return None


def kebab(name: str) -> str:
    name = re.sub(r"\s*\([^)]*\)", "", name or "")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # THROUGH admin, so the write is journalled and admin_undo can take it
    # back. This script can add hundreds of cards in one run; on 2026-09-02 a
    # sibling script tagged 988 descriptions wrongly and the only reason that
    # was recoverable in twenty commands was the journal's before-images.
    import admin
    companies = admin.read_companies()
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    schema = json.loads((DATA / "schema.json").read_text())
    cats = {s["name"]: set(s["categories"]) for s in schema["sectors"]}
    taken_ids = {c["id"] for c in companies} | {s["id"] for s in suppliers}
    known = {norm(c["name"]): c["name"] for c in companies}
    known.update({norm(s["name"]): s["name"] for s in suppliers})

    by_word = {}
    for real in list(known.values()):
        for w in _words(real):
            by_word.setdefault(w, real)

    rows = []
    for pat in a.files:
        for f in sorted(glob.glob(pat)):
            try:
                d = json.loads(pathlib.Path(f).read_text())
            except json.JSONDecodeError:
                print(f"  skip {f}: not valid JSON yet")
                continue
            rows.extend(d.get("researched", d if isinstance(d, list) else []))

    added, to_supplier, queued, skipped = [], [], [], []
    review = json.loads((DATA / "scope_review_queue.json").read_text()) \
        if (DATA / "scope_review_queue.json").exists() else {"items": []}
    seen_review = {norm(i["name"]) for i in review["items"]}

    for r in rows:
        name = (r.get("company_name") or r.get("name") or "").strip()
        if not name:
            continue
        key = norm(name)
        verdict = r.get("verdict")
        match = same_company(key, known)
        if match:
            skipped.append((name, f"already on file as {match!r}"
                            if norm(match) != key else "already on file"))
            continue
        if verdict == "scope_review":
            if key not in seen_review:
                review["items"].append({
                    "name": name, "website": r.get("website"),
                    "description": r.get("description"),
                    "why": r.get("notes") or "horizontal vendor: sells to "
                                             "government among everyone else",
                    "source_event": r.get("source_event"),
                    "queued_on": "2026-08-23"})
                seen_review.add(key)
                queued.append(name)
            continue
        if verdict == "supplier":
            suppliers.append({
                "id": kebab(name), "name": name, "website": r.get("website"),
                "location": r.get("hq_location"), "year_founded": r.get("year_founded"),
                "sector": r.get("sector") if r.get("sector") in cats else "General Gov",
                "category": "Suppliers & Services",
                "description": r.get("description") or "",
                "ats": {"type": "unknown", "ref": None},
                "hiring": {"status": "Unknown", "note": "not researched",
                           "roles": [], "checked": None}})
            known[key] = name
            to_supplier.append(name)
            continue
        if verdict != "govtech":
            skipped.append((name, f"verdict {verdict!r}"))
            continue

        sector, cat = r.get("sector"), r.get("category")
        if sector not in cats or cat not in cats[sector]:
            skipped.append((name, f"sector/category not in schema: {sector} / {cat}"))
            continue
        if not (r.get("description") or "").strip():
            skipped.append((name, "no description"))
            continue

        near = shares_a_name(name, by_word)
        if near:
            # NOT a card and NOT a skip. Two companies can share a word and
            # one company can be written two ways; only a person can say
            # which this is.
            if key not in seen_review:
                review["items"].append({
                    "name": name, "website": r.get("website"),
                    "description": r.get("description"),
                    "why": (f"possible duplicate of {near!r} - they share a "
                            f"distinctive word. Merge, or confirm they are two "
                            f"companies and this becomes a card."),
                    "source_event": r.get("source_event"),
                    "queued_on": dt.date.today().isoformat()})
                seen_review.add(key)
            queued.append(name)
            continue

        cid = kebab(name)
        n = 2
        while cid in taken_ids:
            cid = f"{kebab(name)}-{n}"
            n += 1
        taken_ids.add(cid)
        # "exhibited at X" IS A CLAIM THAT SOMEBODY STOOD ON A FLOOR, and it
        # must name a real event. source_event is whatever the research pass
        # called itself, and a pass called "HHS conference exhibitor research
        # 2026-08-25" is not a conference: writing it here invented an event,
        # put it on the Conferences tab, and asserted these ten companies
        # exhibited at something nobody has evidence they attended.
        # selftest::check refuses any tag the catalog never issued, which is
        # what caught it. A pass label still gets recorded, in `source`, where
        # it is provenance rather than a claim about the company.
        ev = r.get("source_event")
        event = ev if ev in _issued_event_tags() else None
        desc = r["description"].strip().rstrip(".")
        companies.append({
            "id": cid, "name": name, "website": r.get("website"),
            "location": r.get("hq_location"), "year_founded": r.get("year_founded"),
            "sector": sector, "category": cat,
            "description": f"{desc} - exhibited at {event}" if event else desc,
            # ATS discovery is a later pass. "unknown" is the schema's word
            # for needs-discovery and refresh.py skips it; "Unknown" hiring
            # says nothing has looked, which is true and is not "None found".
            "ats": {"type": "unknown", "ref": None},
            "hiring": {"status": "Unknown", "note": "board not discovered yet",
                       "roles": [], "checked": None},
            "govtech": True, "vendor_type": "GovTech Product",
            "source": (f"conference sweep: {ev}" if event
                       else (f"research pass: {ev}" if ev else "research pass")),
            "researched": True})
        known[key] = name
        added.append(name)

    print(f"{len(added)} cards | {len(to_supplier)} to suppliers | "
          f"{len(queued)} queued for scope review | {len(skipped)} skipped")
    for n, why in skipped[:8]:
        print(f"    skip {n[:34]:36} {why}")
    if a.dry_run:
        return 0

    # Validate the whole file the way admin does, then land atomically. A bad
    # batch is refused entire rather than half-written.
    problem = admin.validate(companies)
    if problem:
        print(f"REFUSED: {problem}", file=sys.stderr)
        return 1
    # force: the counts were printed above, which is the condition
    # journal.BLAST asks for before a write touching more than 25 records.
    bad = admin.save_companies(
        companies, "promote-candidates",
        f"{len(added)} researched candidate(s) promoted to cards, "
        f"{len(to_supplier)} filed as suppliers",
        by="agent:promote_candidates", force=True)
    if bad:
        print(f"REFUSED: {bad}", file=sys.stderr)
        return 1
    (DATA / "suppliers.json").write_text(json.dumps(suppliers, indent=1,
                                                    ensure_ascii=False))
    (DATA / "scope_review_queue.json").write_text(json.dumps(review, indent=1,
                                                             ensure_ascii=False))
    print(f"companies.json now {len(companies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
