#!/usr/bin/env python3
"""Apply what a person recorded while working the capture list.

    python3 scripts/apply_task_notes.py             # say what would change
    python3 scripts/apply_task_notes.py --write     # change it

THE DIVISION OF LABOUR, and it is the same one the web admin already runs on.
The capture extension APPENDS AN OPINION to data/task_notes.json through an
open endpoint; this applies it to companies.json in Python, behind validate(),
through save_companies so it is journalled and undoable.

That split is what lets `task-note` live in OPEN_ACTIONS at all. The line those
actions draw is not "no writes" - capture writes manual.json and submit writes
submissions.json - it is that an open action may write to a STAGING file and
never to the map. A bug in an extension can then mis-record a note and cannot
corrupt the dataset.

FIVE KINDS, because the useful answer on a company's website is often not
"here are their jobs":

  board      the address of a board a fetcher can read. Not stored as typed -
             find_ats reads the page and says which ATS is behind it, the same
             routine the admin's verify button uses. A note that names a page
             with no ATS behind it becomes `html`, which is honest: that is a
             page we can scan and not a board we can enumerate.
  founded    a founding year.
  website    a website, for the 40 companies that have none on file.
  posts-at   where they advertise when we cannot read a board - LinkedIn, a
             mailing list. Recorded and LINKED, never counted, because `ats`
             means monitored and filing LinkedIn there would make refresh try,
             fail, and record a zero.
  nothing    a person looked and there was nothing to find. The most easily
             lost finding of the five and the reason the queue keeps
             re-offering companies somebody already checked, so it is stored
             as a fact rather than dropped.

EVERY NOTE IS VALIDATED AFTER IT IS APPLIED AND ROLLED BACK IF IT BREAKS
ANYTHING. A note that cannot land stays pending and says why, rather than being
silently discarded - the person who wrote it is not here to be asked again.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import add_company                                      # noqa: E402
import admin                                            # noqa: E402

NOTES = "task_notes.json"


def pending() -> list:
    try:
        return [n for n in json.loads((DATA / NOTES).read_text())
                if not n.get("applied")]
    except Exception:                                   # noqa: BLE001
        return []


def apply_one(note: dict, c: dict) -> tuple[bool, str]:
    """Put one note onto one company record. Returns (changed, what happened)."""
    kind, value = note.get("kind"), note.get("value")

    if kind == "board":
        # READ THE PAGE, do not trust the address. A person pastes what they
        # are looking at; which ATS is behind it is a question only a fetch
        # answers, and the same one the admin's verify button asks.
        try:
            block, why, _ = add_company.find_ats(value)
        except Exception as exc:                        # noqa: BLE001
            return False, f"could not read that page: {type(exc).__name__}"
        if not block:
            c["ats"] = {"type": "html", "ref": value}
            return True, f"html page scan ({why or 'no ATS detected'})"
        c["ats"] = block
        return True, f"{block.get('type')}/{block.get('ref')}"

    if kind == "founded":
        was = c.get("year_founded")
        c["year_founded"] = str(value)
        return True, f"{was or 'unknown'} -> {value}"

    if kind == "website":
        was = c.get("website")
        c["website"] = value
        return True, f"{was or 'none'} -> {value}"

    if kind == "posts-at":
        # ITS OWN FIELD, NOT `ats`. `ats` means monitored; filing LinkedIn
        # there would make refresh try, fail, and record a zero. The card says
        # "they post here and we are not counting it".
        where = str(value).strip().lower()
        c["posts_at"] = {"where": where, "label": str(value).strip(),
                         "url": value if str(value).startswith("http") else None,
                         "note": None}
        return True, f"posts at {where}"

    if kind == "nothing":
        # A person looked and found nothing. Recorded on the record itself so
        # the queue stops re-offering it as unchecked - which is the whole
        # reason this kind exists.
        note_txt = "checked by hand, nothing to enumerate here"
        c["ats_note"] = note_txt
        return True, note_txt

    return False, f"unknown kind {kind!r}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    todo = pending()
    if not todo:
        print("no task notes waiting")
        return 0

    rows = json.loads((DATA / NOTES).read_text())
    companies = admin.read_companies()
    by_id = {c["id"]: c for c in companies}
    done, failed = [], []

    for note in todo:
        cid = note.get("company_id")
        c = by_id.get(cid)
        if c is None:
            failed.append((cid, note.get("kind"),
                           "no such company - merged away since the note"))
            continue
        before = json.loads(json.dumps(c))
        ok, what = apply_one(note, c)
        if not ok:
            c.clear(); c.update(before)
            failed.append((cid, note.get("kind"), what))
            continue
        err = admin.validate(companies)
        if err:
            # ROLLED BACK, NOT DROPPED. The note stays pending and says why.
            c.clear(); c.update(before)
            failed.append((cid, note.get("kind"), err))
            continue
        done.append((cid, note.get("kind"), what))
        for r in rows:
            if (r.get("company_id") == cid and r.get("kind") == note.get("kind")
                    and r.get("at") == note.get("at")):
                r["applied"] = True

    print(f"{len(done)} note(s) would apply, {len(failed)} left pending\n"
          if not a.write else f"{len(done)} applied, {len(failed)} left pending\n")
    for cid, kind, what in done:
        print(f"  {kind:9} {cid[:26]:28} {what[:52]}")
    for cid, kind, why in failed:
        print(f"  PENDING {kind:9} {str(cid)[:22]:24} {str(why)[:48]}")

    if not a.write or not done:
        if done:
            print("\n  LOOKED ONLY. Re-run with --write to apply them.")
        return 0

    # THROUGH save_companies, so the write is journalled with an author and a
    # reason and admin_undo can take it back. Never write_atomic directly.
    bad = admin.save_companies(
        companies, "apply-task-notes",
        f"{len(done)} note(s) recorded from the capture extension",
        by="owner")
    if bad:
        print(f"refused: {bad}")
        return 1
    admin.write_atomic(NOTES, rows)
    print(f"\n  applied and journalled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
