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
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import admin                                                    # noqa: E402

GOOD_DATE = ("high", "medium")


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

        if r.get("dates") and conf in GOOD_DATE and r.get("dates_source"):
            if c.get("dates") != r["dates"]:
                c["dates"] = r["dates"]
                c["dates_source"] = r["dates_source"]
                c["dates_confidence"] = conf
                c["dates_checked_on"] = today
                dated += 1
                what.append(f"dates={r['dates']!r}")
        elif r.get("dates") and not r.get("dates_source"):
            skipped += 1
            what.append("dates REFUSED - no source url")
        elif conf == "unreadable":
            # Recorded so the next run does not re-spend the fetch, and so the
            # row says WHY it is still blank rather than looking unattempted.
            c["dates_note"] = (f"read {today}: could not read a next-edition "
                               f"date - {str(r.get('directory_note') or '')[:160]}")
            what.append("unreadable, noted")

        if r.get("city") and not c.get("city"):
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
