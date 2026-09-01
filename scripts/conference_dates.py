#!/usr/bin/env python3
"""Keep the conference calendar honest. Confirms dates; never invents one.

    python3 scripts/conference_dates.py              # what has gone stale, no network
    python3 scripts/conference_dates.py --confirm    # also re-read the event pages
    python3 scripts/conference_dates.py --confirm --limit 20

WHY THIS DOES NOT SCRAPE DATES, which is the design and not a shortcut.

The obvious engine reads each event's own page, pulls the date out and updates
the catalogue. Measured on eight events before writing a line of it: only three
pages yielded a date-shaped string at all, and the three were wrong.

  NACo Annual        we hold July 23-26 2027. Its page offers "Dec. 3-5, 2026"
                     and "Feb. 11-15 2028" - two OTHER NACo events.
  NACo Legislative   we hold February 19-23 2027. Its page offers
                     "Feb. 21-24, 2026", last year's edition.
  USCM               we hold January 20-22 2027, which the page does carry -
                     alongside "March 15-21, 2027", a different meeting.

An association runs several events and publishes them all on one site. Picking
which one is "the" date is judgement, and a scraper doing it would propose
replacing correct entries with wrong ones - the exact false-presence this
project refuses everywhere else. 111 of 126 dates are already `high`
confidence, so the machine would mostly be degrading good data.

SO IT MAKES THE WEAKEST CLAIM THAT IS STILL USEFUL: does the date we already
hold still appear on the event's own page?

  confirmed    the page still says what we say. The strongest thing an
               automated check can honestly assert.
  not found    the page no longer carries it. NOT "the date changed" - the
               page may have been redesigned, or the date may sit in an image
               or a script. A flag for a person, never an edit.
  unreadable   we could not fetch it. We learned nothing, which is different
               from learning the date is gone.

AND THE HALF THAT NEEDS NO NETWORK AT ALL. An event whose date has passed and
whose next edition is unrecorded is stale by arithmetic - no page required, no
false positives possible, and it is the case that actually rots a calendar.
That runs by default; --confirm is the optional, slower half.

Nothing here writes to data/conferences.json. It prints, and with --json it
writes a report for a person to work from.
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

import ats                                            # noqa: E402

_MON = ("january|february|march|april|may|june|july|august|september|"
        "october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|"
        "nov|dec")
_DATE = re.compile(
    rf"\b({_MON})\.?\s+(\d{{1,2}})(?:\s*(?:[-–—]|to)\s*"
    rf"(?:({_MON})\.?\s+)?(\d{{1,2}}))?,?\s+(20\d\d)", re.I)
_MONTH_N = {m[:3]: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def load() -> list:
    return json.loads((DATA / "conferences.json").read_text())["conferences"]


def parsed(text: str) -> dt.date | None:
    """The first date in a string, as a date. None when there is not one.

    Only ever reads the START of a range. A conference that runs the 17th to
    the 21st is stale on the 22nd, and the end date does not change that.
    """
    m = _DATE.search(text or "")
    if not m:
        return None
    mon = _MONTH_N.get(m.group(1).lower()[:3])
    if not mon:
        return None
    try:
        return dt.date(int(m.group(5)), mon, int(m.group(2)))
    except ValueError:
        return None


def stale(rows: list, today: dt.date) -> list:
    """Events whose date has passed. Arithmetic, no network, no false positives.

    This is what actually rots a calendar: nobody notices an event went by,
    and it sits there looking like something you could still book.
    """
    out = []
    for c in rows:
        d = parsed(c.get("dates") or "")
        if d and d < today:
            out.append({"conference": c["conference"], "tag": c.get("event_tag"),
                        "dates": c["dates"], "was": d.isoformat(),
                        "days_ago": (today - d).days,
                        "next_edition": c.get("next_edition"),
                        "url": c.get("url")})
    out.sort(key=lambda r: -r["days_ago"])
    return out


def undated(rows: list) -> list:
    """Events carrying no readable date, with the reason already on the record."""
    return [{"conference": c["conference"], "why": c.get("dates_confidence"),
             "dates": c.get("dates"), "url": c.get("url")}
            for c in rows if not parsed(c.get("dates") or "")]


def confirm(c: dict) -> dict:
    """Does the event's own page still carry the date we hold?

    THE WEAKEST CLAIM THAT IS STILL USEFUL. It never proposes a date - see the
    module docstring for the three events that proved why. It answers one
    question: is what we say still on their page.
    """
    held = parsed(c.get("dates") or "")
    url = c.get("url")
    if not held or not url:
        return {"conference": c["conference"], "state": "no date on file"}
    try:
        html = ats._get(url).text
    except Exception as e:                            # noqa: BLE001
        # We learned nothing. That is not the same as the date being gone,
        # and recording it as a change would be a fact about our fetcher
        # published as a fact about their conference.
        return {"conference": c["conference"], "state": "unreadable",
                "why": type(e).__name__}
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    for m in _DATE.finditer(text):
        d = parsed(m.group(0))
        if d == held:
            return {"conference": c["conference"], "state": "confirmed",
                    "held": held.isoformat(), "saw": m.group(0)}
    # THE YEAR IS OFTEN NOT BESIDE THE MONTH, and requiring it there called
    # nine of fourteen events "not found" that had not moved at all. ARMA
    # writes "ARMA InfoCon 2026 October 25-28" - year first, then the range -
    # and ICMA's programme lists "Sunday, October 18" with the year only in
    # the page title. A conference site says its year once and its dates
    # everywhere.
    #
    # So the fallback is month-and-start-day together, WITH the held year
    # present somewhere on the page. Weaker than a full match and stated as
    # such: it says the page still carries this date, not that it parsed one.
    loose = re.compile(
        rf"\b{held.strftime('%B')}\.?\s+0?{held.day}\b", re.I)
    if loose.search(text) and str(held.year) in text:
        return {"conference": c["conference"], "state": "confirmed",
                "held": held.isoformat(),
                "saw": loose.search(text).group(0) + f" (+{held.year} on the page)",
                "how": "month and start day, with the year elsewhere on the page"}
    seen = sorted({m.group(0) for m in _DATE.finditer(text)})[:4]
    return {"conference": c["conference"], "state": "not found",
            "held": held.isoformat(), "url": url,
            "other_dates_on_the_page": seen,
            "note": "the page does not carry our date. It may have been "
                    "redesigned, or the date may be in an image. This is a "
                    "flag for a person, not a change."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="also re-read each event page (slow, one fetch each)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):              # pragma: no cover
        pass

    rows = load()
    today = dt.date.today()
    st, un = stale(rows, today), undated(rows)

    report = {"generated": today.isoformat(), "total": len(rows),
              "stale": st, "undated": un}

    print(f"{len(rows)} conferences on file\n")
    print(f"  {len(st)} whose date has already passed:")
    for r in st[:25]:
        nxt = f"  next edition on file: {r['next_edition']}" if r.get("next_edition") else \
              "  no next edition recorded"
        print(f"    {r['conference'][:30]:32} {r['dates'][:24]:26} "
              f"{r['days_ago']:4}d ago{nxt}")
    if not st:
        print("    none - every dated event is still ahead")
    print(f"\n  {len(un)} with no readable date, and each says why:")
    for r in un:
        print(f"    {r['conference'][:30]:32} {str(r['why'] or 'unknown'):14} "
              f"{str(r['dates'] or '')[:30]}")

    if a.confirm:
        todo = [c for c in rows if parsed(c.get("dates") or "")]
        if a.limit:
            todo = todo[:a.limit]
        print(f"\n  re-reading {len(todo)} event page(s) to confirm what we hold")
        got = []
        for c in todo:
            r = confirm(c)
            got.append(r)
            if r["state"] != "confirmed":
                print(f"    {r['state']:12} {r['conference'][:34]}")
        from collections import Counter
        print(f"\n    {dict(Counter(r['state'] for r in got))}")
        print("    'not found' is a flag, never an edit: an association runs "
              "several\n    events on one site and picking which date is THE "
              "date is judgement.")
        report["confirmed"] = got

    if a.json:
        out = DATA / "conference_date_report.json"
        out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
