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


RULED = DATA / "conference_date_rulings.json"


def rulings() -> dict:
    try:
        return json.loads(RULED.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def q_calendar(companies=None, board=None) -> list:
    """Conferences whose date needs a person, strongest signal first.

    THE CALENDAR IS THE ONE PART OF THIS BOARD THAT ROTS ON A CLOCK. A company
    that stops hiring is still a true row; a conference that happened last
    month and still shows a date is a page telling somebody to book a flight
    to an event that is over. Six are in that state today and one of them went
    by 190 days ago.

    Three kinds of row, in the order a person should meet them, because they
    are different questions and only the first is urgent:

      PASSED       the date is behind us and no next edition is recorded.
                   Arithmetic - no page was read, no false positive possible.
      NOT FOUND    the event's own page no longer carries the date we hold.
                   A flag, never a change: see confirm() for why this file
                   refuses to read a new date off a page.
      UNDATED      no date on file at all, with the reason already recorded -
                   unannounced, or a page we could not read.

    A ruled event drops out and is never re-asked, the same rule every other
    queue here follows.
    """
    rows = load()
    ruled = rulings()
    today = dt.date.today()
    report = {}
    try:
        rep = json.loads((DATA / "conference_date_report.json").read_text())
        report = {r["conference"]: r for r in (rep.get("confirmed") or [])}
    except (OSError, json.JSONDecodeError, KeyError):
        pass                       # never confirmed yet; the other two still work

    out = []
    for c in rows:
        tag = c.get("event_tag") or c["conference"]
        if tag in ruled:
            continue
        held = parsed(c.get("dates") or "")
        seen = report.get(c["conference"]) or {}
        if held and held < today and not c.get("next_edition"):
            out.append({"kind": "passed", "rank": 0,
                        "says": f"ran {(today - held).days} days ago and no "
                                f"next edition is on file"})
        elif not held:
            out.append({"kind": "undated", "rank": 2,
                        "says": f"no date on file - recorded as "
                                f"{c.get('dates_confidence') or 'unknown'}"})
        elif seen.get("state") == "not found":
            other = seen.get("other_dates_on_the_page") or []
            out.append({"kind": "not_found", "rank": 1,
                        "says": "their own page no longer carries this date"
                                + (f"; it does carry {', '.join(other[:3])}"
                                   if other else ""),
                        "other_dates": other})
        else:
            continue
        out[-1].update({
            "id": tag, "conference": c["conference"], "dates": c.get("dates"),
            "url": c.get("url"), "city": c.get("city"),
            "department": c.get("department"), "flagship": c.get("flagship"),
            "confidence": c.get("dates_confidence"),
            "source": c.get("dates_source"),
            "confirmed_state": seen.get("state"),
        })
    out.sort(key=lambda r: (r["rank"], r["conference"]))
    return out


def rule(event_tag: str, outcome: str, dates: str = "", why: str = "",
         by: str = "owner") -> dict:
    """Record a decision about one event's date. Returns the stored ruling.

    outcome is one of:
      set          a date, which the caller must supply and which must parse
      unannounced  checked, and they have not published one yet
      ended        the event does not run any more
      ok           the date on file is right and the flag was noise

    NOTHING HERE TOUCHES conferences.json. The ruling is an opinion appended
    to its own file, and `conference_dates.py --apply` folds it into the
    catalogue in one place where the parse is checked - the same division the
    web admin uses, and for the same reason: a bug in the recording half must
    not be able to corrupt the catalogue.
    """
    if outcome not in ("set", "unannounced", "ended", "ok"):
        raise ValueError("outcome must be set, unannounced, ended or ok")
    if outcome == "set":
        if not parsed(dates):
            raise ValueError(
                f"{dates!r} does not read as a date. Give it the way the "
                f"catalogue writes them - 'October 17-21, 2026' - so the "
                f"calendar can place it")
    store = rulings()
    store[event_tag] = {"outcome": outcome, "dates": dates.strip() or None,
                        "why": (why or "").strip() or None,
                        "on": dt.date.today().isoformat(), "by": by}
    RULED.write_text(json.dumps(store, indent=1) + "\n")
    return store[event_tag]


def apply_rulings(rows: list | None = None) -> dict:
    """Fold recorded rulings into the catalogue. The ONE place it is edited.

    Returns a report rather than printing, so a caller can refuse to write.
    """
    rows = rows if rows is not None else load()
    ruled = rulings()
    changed, skipped = [], []
    by_tag = {(c.get("event_tag") or c["conference"]): c for c in rows}
    for tag, r in ruled.items():
        c = by_tag.get(tag)
        if not c:
            skipped.append((tag, "no such event on file"))
            continue
        if r["outcome"] == "set":
            if not parsed(r.get("dates") or ""):
                skipped.append((tag, "the ruled date no longer parses"))
                continue
            c["dates"] = r["dates"]
            c["dates_confidence"] = "owner"
            c["dates_source"] = f"ruled {r['on']} by {r['by']}"
            changed.append(tag)
        elif r["outcome"] == "unannounced":
            c["dates"] = None
            c["dates_confidence"] = "unannounced"
            c["dates_source"] = f"ruled {r['on']} by {r['by']}"
            changed.append(tag)
        elif r["outcome"] == "ended":
            c["dates_confidence"] = "ended"
            c["dates_source"] = f"ruled {r['on']} by {r['by']}"
            changed.append(tag)
        # "ok" changes nothing on purpose: the flag was noise and the row
        # simply stops being asked about.
    return {"changed": changed, "skipped": skipped, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="also re-read each event page (slow, one fetch each)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="fold recorded rulings into data/conferences.json")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):              # pragma: no cover
        pass

    rows = load()
    today = dt.date.today()

    if a.apply:
        # THE ONE PLACE THE CATALOGUE IS EDITED. The admin appends an opinion
        # and this folds it in, so a bug in the recording half cannot corrupt
        # the file - the same division the web admin uses.
        rep = apply_rulings(rows)
        if not rep["changed"] and not rep["skipped"]:
            print("no rulings to apply. Work the Conference dates queue in "
                  "the admin first.")
            return 0
        whole = json.loads((DATA / "conferences.json").read_text())
        whole["conferences"] = rep["rows"]
        tmp = DATA / "conferences.json.tmp"
        tmp.write_text(json.dumps(whole, indent=1) + "\n")
        try:
            back = json.loads(tmp.read_text())
            assert len(back["conferences"]) == len(rows)
        except Exception as e:                        # noqa: BLE001
            tmp.unlink(missing_ok=True)
            print(f"refused: the result did not read back cleanly ({e})",
                  file=sys.stderr)
            return 1
        tmp.replace(DATA / "conferences.json")
        print(f"applied {len(rep['changed'])} ruling(s): "
              f"{', '.join(rep['changed'][:8])}")
        for tag, why in rep["skipped"]:
            print(f"  skipped {tag}: {why}")
        print("  build_board.py picks these up on the next run.")
        return 0
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
