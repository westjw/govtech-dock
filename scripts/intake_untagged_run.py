"""Land a research list that belongs to NO event. --write to land it.

    python3 scripts/intake_untagged_run.py data/exhibitors_run_govit.json [--write]

conference_intake is the right script for a floor. It refuses this file, and
correctly: `resolve_tag` demands a tag conferences.json actually issued, because
on 2026-09-02 a fallback to the staged file's own name wrote "AWWA ACE" and
"3CMA" into 988 descriptions. The Gov IT list arrived with no show named - the
run GUESSED a GMIS-style exhibitor list and said so - and a guess is not a tag.

So this does the same three-way split MINUS the half that needs a tag:

  already on file  ->  NOTHING. No description tag, no source tag. There is no
                       event to claim, and "researched on a list somebody
                       pasted" is not a fact the Conferences tab can render.
  new, not govtech ->  a suppliers.json row, same shape conference_intake writes
  new, govtech     ->  govtech_candidates.json, source_event null

A VENDOR STILL NEVER ENTERS companies.json HERE. Sector, category, ATS and a
description need research and a person's ruling; that boundary is the whole
reason the candidates queue exists, and a list with no event is weaker
provenance than a floor, not stronger.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STAGE = DATA / "conference_intake"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
from conference_intake import kebab, norm                       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--default-sector", default="General Gov")
    a = ap.parse_args()

    staged = json.loads(pathlib.Path(a.file).read_text())
    if staged.get("event_tag"):
        print(f"refused: {a.file} carries event_tag "
              f"{staged['event_tag']!r} - run conference_intake instead, so "
              f"the tag reaches descriptions and the Conferences tab.",
              file=sys.stderr)
        return 1
    if staged.get("quality") in ("menu", "doubtful"):
        print(f"refused: capture graded {staged.get('quality')!r}",
              file=sys.stderr)
        return 1

    companies = admin.read_companies()
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    valid = {s["name"] for s in
             json.loads((DATA / "schema.json").read_text())["sectors"]}
    if a.default_sector not in valid:
        raise SystemExit(f"unknown --default-sector {a.default_sector!r}")

    known = {}
    for rows in (companies, suppliers):
        for r in rows:
            known[norm(r["name"])] = r["name"]
            for aka in r.get("also_known_as", []) or []:
                known[norm(aka)] = r["name"]

    on_file, new_sup, candidates, unjudged = [], [], [], []
    seen: set[str] = set()
    for ex in staged["exhibitors"]:
        k = norm(ex["name"])
        if not k or k in seen:
            continue
        seen.add(k)
        if k in known:
            on_file.append((ex["name"], known[k]))
            continue
        # A NULL is "nobody judged this", and it must not fall into the
        # suppliers bucket by default - that is a positive claim about the
        # company's business model. It waits for a person.
        if ex.get("is_govtech") is None:
            unjudged.append(ex["name"])
            continue
        if ex["is_govtech"]:
            candidates.append({"name": ex["name"], "website": ex.get("website"),
                               "vertical": ex.get("vertical"),
                               "description": ex.get("description"),
                               "source_event": None,
                               "source_note": staged.get("source_note")})
        else:
            new_sup.append({
                "id": kebab(ex["name"]), "name": ex["name"],
                "website": ex.get("website"), "location": None,
                "year_founded": None,
                "sector": (ex.get("sector") if ex.get("sector") in valid
                           else a.default_sector),
                "category": "Suppliers & Services",
                "description": ex.get("description"),
                "ats": {"type": "unknown", "ref": None},
                "hiring": {"status": "Unknown", "note": "not researched",
                           "roles": [], "checked": None},
            })

    print(f"{a.file}: {len(staged['exhibitors'])} staged")
    print(f"  {len(on_file)} already on file - NOTHING written (no event to tag)")
    print(f"  {len(new_sup)} new suppliers")
    print(f"  {len(candidates)} govtech candidates for research")
    print(f"  {len(unjudged)} unjudged, held back for a person: "
          f"{', '.join(unjudged) or 'none'}")
    if not a.write:
        print("\n  LOOKED ONLY. --write to land it.")
        return 0

    admin.write_atomic("suppliers.json", suppliers + new_sup)
    STAGE.mkdir(exist_ok=True)
    cp = STAGE / "govtech_candidates.json"
    existing = json.loads(cp.read_text()) if cp.exists() else []
    have = {norm(c["name"]) for c in existing}
    added = 0
    for c in candidates:
        if norm(c["name"]) not in have:
            existing.append(c)
            added += 1
    cp.write_text(json.dumps(existing, indent=1, ensure_ascii=False) + "\n")
    print(f"\n  wrote {len(new_sup)} suppliers; {added} candidates added "
          f"({len(existing)} on file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
