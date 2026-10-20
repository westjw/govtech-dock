"""Land a conference-site read: dates, city, and a directory url. --write to land.

    python3 scripts/apply_conference_reads.py --in reads.json [--write]

THE INPUT IS A READ, NOT A RULING. Each row carries what a page said and the
url it was read off, so every field written here can be argued with later.

FOUR REFUSALS, and each one is a way this has gone wrong before:

  A DATE WITH NO SOURCE IS NOT WRITTEN. dates_source is the url the string was
  read off. Without it the catalogue holds a date nobody can check, and
  somebody books travel around it.

  "unreadable" IS NOT "none". A fetch that 403s or renders empty tells us
  nothing about the conference, so the row is left exactly as it was rather
  than being stamped with an absence we did not observe. This is the same rule
  scan_pagetext holds and the reason `Unknown` exists beside `None found`.

  A DIRECTORY ONLY LANDS IF THE ADVERSARIAL CHECK CONFIRMED IT. The first
  reader of IACLEA's floor counted 50 names and said "no pagination control
  was present, so 50 appears to be the whole list". The page is infinite
  scroll; it holds 99, and the read stopped at "KeyTrak" - dead on the letter
  K, the alphabetical tell. An unchecked directory url is how twenty rows in
  the admin queue came to point at association navigation.

  NOTHING OVERWRITES A FIND WITH A BLANK. A row that already carries an
  exhibitor_url keeps it unless this read confirmed a different one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import admin                                                    # noqa: E402
import conference_dates                                         # noqa: E402

GOOD_DATE = ("high", "medium")


def _unread_note(c: dict) -> bool:
    """The row's note says its page could not be read."""
    return "could not read" in (c.get("dates_note") or "")


def _ruled_not_a_floor(tag: str | None) -> bool:
    """A reader read the floor staged for `tag` and found nothing real on it.

    read_floor.apply writes found: false with a "reader: every one of the N
    names was the site's own text" why. The sweeper's own refusals (a page it
    could not read, too few names) are NOT this: they say nothing about
    whether the address is right, so they never let a new one in.
    """
    if not tag:
        return False
    safe = re.sub(r"[^A-Za-z0-9]+", "_", tag).strip("_")
    try:
        d = json.loads((DATA / f"exhibitors_{safe}.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return d.get("found") is False and str(d.get("why") or "").startswith("reader:")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    today = dt.date.today().isoformat()

    reads = json.loads(pathlib.Path(a.src).read_text())
    if isinstance(reads, dict):
        reads = reads.get("events", reads)
    by_tag = {r["tag"]: r for r in reads}

    path = DATA / "conferences.json"
    doc = json.loads(path.read_text())
    rows = doc.get("conferences", doc) if isinstance(doc, dict) else doc

    dated = placed = floors = skipped = disputed = 0
    lines: list[str] = []
    for c in rows:
        r = by_tag.get(c.get("event_tag"))
        if not r:
            continue
        what = []
        conf = str(r.get("dates_confidence") or "").lower()

        new_edition = False
        if r.get("dates") and conf in GOOD_DATE and r.get("dates_source"):
            if c.get("dates") != r["dates"]:
                was = conference_dates.parsed(c.get("dates") or "")
                now = conference_dates.parsed(r["dates"])
                new_edition = bool(was and now and was.year != now.year)
                c["dates"] = r["dates"]
                # A DATE READ OFF THEIR PAGE ANSWERS THE NOTE. "No next edition
                # announced" left beside the date it was waiting for says both.
                c.pop("dates_note", None)
                c["dates_source"] = r["dates_source"]
                c["dates_confidence"] = conf
                c["dates_checked_on"] = today
                # THE EDITION THE DATES NOW DESCRIBE. next_edition holds its
                # year ("2026", "2026 Fall Meeting"); left alone, a 2027 date
                # sat under "2026" and the row read as one edition while
                # saying another. The year follows the date it was read with.
                held = conference_dates.parsed(r["dates"])
                ne = str(c.get("next_edition") or "")
                m = re.search(r"20\d\d", ne)
                if held and not m:
                    c["next_edition"] = str(held.year)
                elif held and int(m.group(0)) != held.year:
                    c["next_edition"] = ne.replace(m.group(0), str(held.year), 1)
                dated += 1
                what.append(f"dates={r['dates']!r}")
            elif c.get("dates_checked_on") != today or _unread_note(c):
                # A READ THAT CONFIRMS IS STILL A READ. The page states the
                # dates already on file, so nothing changes but the fact that
                # somebody looked and when - without this stamp a confirmed
                # row kept reading "never checked" and the next pass paid to
                # read it again (97 of 139 rows, 2026-10-15).
                c["dates_checked_on"] = today
                if not c.get("dates_source"):
                    c["dates_source"] = r["dates_source"]
                    c["dates_confidence"] = conf
                # AND IT ANSWERS "COULD NOT READ". Fourteen rows kept a note
                # saying their page turned the crawler away after a reader in
                # a browser had read the same dates off it (2026-10-20); the
                # row said both. A note about some other thing stays.
                if _unread_note(c):
                    c.pop("dates_note", None)
                what.append("dates confirmed as on file")
        elif r.get("dates") and not r.get("dates_source"):
            skipped += 1
            what.append("dates REFUSED - no source url")
        elif conf == "unreadable":
            # Recorded so the next run does not re-spend the fetch, and so the
            # row says WHY it is still blank rather than looking unattempted.
            c["dates_note"] = (f"read {today}: could not read a next-edition "
                               f"date - {str(r.get('note') or r.get('directory_note') or '')[:160]}")
            what.append("unreadable, noted")
        elif conf == "unannounced":
            # THE SITE READ FINE AND SAYS NOTHING ABOUT A NEXT EDITION. The
            # date on file stays (it is the last edition, labelled past), and
            # the row says when somebody looked and what they saw, so the
            # next pass starts from that rather than from nothing.
            c["dates_note"] = (f"read {today}: no next edition announced on the "
                               f"organisation's site - {str(r.get('note') or '')[:160]}")
            c["dates_checked_on"] = today
            what.append("unannounced, noted")

        if new_edition:
            # A NEW EDITION IS SOMEWHERE ELSE. The city on file is where the
            # edition that ended was held; kept beside next year's dates it
            # says ASBO 2027 is in Pittsburgh when its own page says Phoenix.
            # The read's city is the new edition's, and a read that names none
            # leaves the row with none rather than with last year's.
            if c.get("city") != (r.get("city") or None):
                c["city"] = r.get("city") or None
                placed += 1
                what.append(f"city={c['city']!r} (the new edition's)")
        elif r.get("city") and not c.get("city"):
            c["city"] = r["city"]
            placed += 1
            what.append(f"city={r['city']!r}")

        chk = (r.get("check") or {}).get("verdict")
        n = (r.get("check") or {}).get("names_seen") or r.get("names_visible")
        if r.get("directory_url") and chk == "confirmed":
            held = c.get("exhibitor_url")
            if not held:
                c["exhibitor_url"] = r["directory_url"]
                c["exhibitor_url_source"] = (
                    f"read {today}, confirmed by a second reader: "
                    f"{n} company names visible")
                floors += 1
                what.append(f"FLOOR FOUND ({n} names)")
            elif held.rstrip("/") != r["directory_url"].rstrip("/") \
                    and _ruled_not_a_floor(c.get("event_tag")):
                # THE URL ON FILE IS NOT A FIND. A reader already read the
                # floor staged from it and ruled every name the site's own
                # navigation ("this page is not a floor"), so there is no real
                # find for a second answer to replace - only a menu, and a
                # confirmed list one click away from it. Vermont League's row
                # pointed at a page whose 121 names were "Advocacy Chat" and
                # "Dental Insurance" while its Business Alliance members sat
                # behind a link (2026-10-20). The old address is kept.
                c["exhibitor_url_was"] = held
                c["exhibitor_url"] = r["directory_url"]
                c["exhibitor_url_source"] = (
                    f"read {today}, confirmed by a second reader: {n} company "
                    f"names visible; replaces {held}, which a reader ruled not a floor")
                c.pop("exhibitor_url_candidate", None)
                c.pop("exhibitor_url_candidate_note", None)
                floors += 1
                what.append(f"FLOOR REPLACED ({n} names; the old one was ruled not a floor)")
            elif held.rstrip("/") != r["directory_url"].rstrip("/"):
                # A SECOND ANSWER IS NOT A CORRECTION. The url on file may be
                # one of the twenty the admin queue already flags as pointing
                # at association navigation - but it may equally be the right
                # one, and this read cannot tell. Overwriting on a machine's
                # say-so is how a real find gets replaced by a worse one; so
                # the candidate is recorded BESIDE it, with the name count
                # that argues for it, and a person rules. Same shape as
                # candidate_url in find_event_directories.
                c["exhibitor_url_candidate"] = r["directory_url"]
                c["exhibitor_url_candidate_note"] = (
                    f"read {today}: a second reader confirmed {n} company "
                    f"names here. The url on file is {held} - rule which.")
                disputed += 1
                what.append(f"SECOND FLOOR ({n} names) - queued against the "
                            f"one on file")
        elif r.get("directory_url") and chk != "confirmed":
            skipped += 1
            what.append(f"directory REFUSED - second reader said {chk!r}")

        if what:
            lines.append(f"  {c['event_tag'][:38]:38} {'; '.join(what)}")

    print(f"{len(reads)} read(s) against the catalogue")
    print("\n".join(lines))
    print(f"\n  {dated} date(s), {placed} city(ies), {floors} exhibitor "
          f"directory(ies), {disputed} queued against a url on file, "
          f"{skipped} field(s) refused")

    if not a.write:
        print("\n  LOOKED ONLY. --write to land it.")
        return 0

    out = {**doc, "conferences": rows} if isinstance(doc, dict) else rows
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    json.loads(tmp.read_text())          # it parses or nothing moves
    tmp.replace(path)
    print(f"  written to {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
