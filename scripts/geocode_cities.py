#!/usr/bin/env python3
"""Coordinates for the cities the board actually names, so distance is real.

WHY THIS FILE EXISTS AT ALL

"Roles within 50 miles of Denver" needs a latitude and a longitude for Denver
and for every city on the board. There is no way to derive those from the text
"Denver, CO", and there is exactly one honest way to get them: ask a
gazetteer and write down what it said. Guessing a coordinate is the same class
of mistake as guessing a founding year, except worse - a wrong year is visibly
wrong to somebody who knows the company, and a coordinate that is 40 miles out
is invisible and silently rearranges the board.

So this asks, records the answer with the query that produced it, and leaves
anything it could not resolve OUT. A city with no coordinate is not at 0,0.

WHY NOMINATIM AND NOT A KEY

No new dependencies, ever (stdlib + requests + openpyxl), and no account for
anybody to set up. Nominatim is OpenStreetMap's own geocoder and is free. Its
usage policy asks for at most one request a second and a real User-Agent that
identifies the application, and both are honoured below - if you are tempted
to raise the rate, the correct move is to run it less often instead. The whole
run is ~300 cities, once, and the result is committed.

WHAT IT WILL NOT DO

It never geocodes a bare city with no state. "Springfield" is in 30-odd
states and the closest match to a query is not the one the posting meant;
resolving that by picking the biggest is how a job in Springfield, MA turns up
in a search around Springfield, IL.

  python3 scripts/geocode_cities.py            # only cities not yet on file
  python3 scripts/geocode_cities.py --recheck  # ask again about everything
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import roles  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "cities.json"

ENDPOINT = "https://nominatim.openstreetmap.org/search"
# Their policy asks for an identifying agent with contact details. A generic
# one gets the whole project blocked and deserves to be.
UA = "sled-jobs-board/1.0 (+https://github.com/westjw/govtech-dock)"
PAUSE = 1.1        # their policy is 1/sec; this is deliberately over it


def cities_on_board() -> collections.Counter:
    """Every (city, state) the board names, with how many postings sit there.

    AND EVERY CITY A CONFERENCE IS HELD IN. This read postings only, so the
    lookup covered the cities companies have desks in and nothing else - and
    the conferences tab, which wants to say how far an event is, could place
    69 of its 138 venues. Anaheim, Savannah, Grapevine and New Orleans host
    conferences and employ nobody we track, so they were simply absent, and a
    distance a reader cannot see reads as "not near me" rather than "we could
    not place it".

    Venues are counted once each: the weight orders the queue by how many
    postings sit somewhere, and one conference is not busier than a city with
    forty desks in it.
    """
    board = json.loads((DATA / "board.json").read_text())
    seen = collections.Counter()
    for p in board.get("postings", []):
        g = roles.geography(p.get("location") or "", p.get("title") or "")
        off = g.get("office")
        if off and off.get("city") and off.get("state"):
            seen[(off["city"], off["state"])] += 1
    for c in board.get("conferences", []):
        got = venue_city(c.get("city"))
        if got:
            seen[got] += 1
    return seen


def venue_city(text: str | None) -> tuple | None:
    """("Denver", "CO") out of "Denver, CO". None when it is not that shape.

    Deliberately narrow. "Calgary, AB, Canada" returns None rather than
    ("Calgary", "AB"): ask() searches the United States only, so a Canadian
    venue would come back either empty or as the wrong Calgary, and a
    coordinate forty miles out is invisible where a blank is not.
    """
    parts = [x.strip() for x in (text or "").split(",")]
    if len(parts) != 2:
        return None
    city, state = parts
    if not city or not re.fullmatch(r"[A-Z]{2}", state):
        return None
    return (city, state)


# "We asked and there is no such place" - a real answer, worth writing down.
# Distinct from None, which now means only "we could not ask".
NO_MATCH = object()


def ask(city: str, state: str):
    """A coordinate, NO_MATCH, or None.

    THE ABSENCE TRAP, IN A GEOCODER. This returned None for three different
    things - a genuine no-match, an HTTP error, and a dead socket - and the
    caller wrote all three down identically as "nominatim: no match". So a
    rate limit became a permanent record that a city does not exist, and
    because the row was then on file, no later run would ever ask again.

    Measured on 2026-09-10: a 206-city run recorded 107 failures, and Spokane
    WA, Bozeman MT, Miami Beach FL and Grapevine TX were among them. All four
    answer on a single request. They were not missing; we were throttled.

    That is this project's oldest rule wearing a different hat - a page scan
    never proves absence - and the cost is the same shape: a distance filter
    silently answering "nothing near you" about a city it was rate-limited
    out of asking about.
    """
    q = f"{city}, {state}, United States"
    hits = None
    for attempt in range(3):
        try:
            r = requests.get(ENDPOINT, params={"q": q, "format": "json",
                                               "limit": 1,
                                               "countrycodes": "us"},
                             headers={"User-Agent": UA}, timeout=20)
        except Exception:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (429, 503, 502, 504):
            # being asked to slow down is not an answer about the city
            time.sleep(5 * (attempt + 1))
            continue
        if not r.ok:
            return None
        try:
            hits = r.json()
        except Exception:
            return None
        break
    if hits is None:
        return None                      # never asked successfully
    if not hits:
        return NO_MATCH                  # asked, and there is no such place
    h = hits[0]
    try:
        lat, lon = float(h["lat"]), float(h["lon"])
    except (KeyError, TypeError, ValueError):
        return NO_MATCH        # it answered, the answer was unusable
    # A US city that lands outside the US bounding box means the geocoder
    # matched something else with the same name. Refuse it rather than plot it.
    if not (18.0 <= lat <= 72.0 and -180.0 <= lon <= -66.0):
        return NO_MATCH        # it matched something, elsewhere on earth
    return {"lat": round(lat, 4), "lon": round(lon, 4),
            "query": q, "matched": h.get("display_name", "")[:120],
            "source": "nominatim.openstreetmap.org"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recheck", action="store_true",
                    help="ask again about cities already on file")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    have = json.loads(OUT.read_text()) if OUT.exists() else {}
    board = cities_on_board()
    todo = [(c, s) for (c, s) in board
            if a.recheck or f"{c}|{s}" not in have]
    todo.sort(key=lambda k: -board[k])          # busiest cities first
    if a.limit:
        todo = todo[:a.limit]

    print(f"{len(board)} cities on the board, {len(have)} already on file")
    print(f"asking about {len(todo)}, one a second\n", flush=True)

    found = missed = unasked = 0
    for i, (city, state) in enumerate(todo, 1):
        got = ask(city, state)
        key = f"{city}|{state}"
        if got is None:
            # WE NEVER ASKED. Write nothing: an absent row is one the next run
            # picks up, and a "no match" row is one it skips forever.
            unasked += 1
            print(f"  could not ask: {city}, {state} "
                  f"(left off file so the next run retries)", flush=True)
        elif got is NO_MATCH:
            # Recorded as a failure so the next run knows it asked, and so
            # nobody mistakes an absent city for one nobody looked up.
            have[key] = {"lat": None, "lon": None,
                         "query": f"{city}, {state}, United States",
                         "matched": None, "source": "nominatim: no match"}
            missed += 1
            print(f"  no match: {city}, {state}", flush=True)
        else:
            have[key] = got
            found += 1
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)} ({found} found)", flush=True)
        time.sleep(PAUSE)

    OUT.write_text(json.dumps(have, indent=1, sort_keys=True) + "\n")
    live = sum(1 for v in have.values() if v.get("lat") is not None)
    print(f"\n{found} resolved, {missed} genuinely not found, "
          f"{unasked} never asked (left off file to retry)")
    print(f"{OUT.name} now holds {len(have)} cities, {live} with coordinates")
    print("\nA city with no coordinate is left with lat null. It is NOT at "
          "0,0 and must never be filtered as though it were somewhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
