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
    """Every (city, state) the board names, with how many postings sit there."""
    board = json.loads((DATA / "board.json").read_text())
    seen = collections.Counter()
    for p in board.get("postings", []):
        g = roles.geography(p.get("location") or "", p.get("title") or "")
        off = g.get("office")
        if off and off.get("city") and off.get("state"):
            seen[(off["city"], off["state"])] += 1
    return seen


def ask(city: str, state: str) -> dict | None:
    """One lookup, or None. None means unresolved, never a default."""
    q = f"{city}, {state}, United States"
    try:
        r = requests.get(ENDPOINT, params={"q": q, "format": "json", "limit": 1,
                                           "countrycodes": "us"},
                         headers={"User-Agent": UA}, timeout=20)
        if not r.ok:
            return None
        hits = r.json()
    except Exception:
        return None
    if not hits:
        return None
    h = hits[0]
    try:
        lat, lon = float(h["lat"]), float(h["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    # A US city that lands outside the US bounding box means the geocoder
    # matched something else with the same name. Refuse it rather than plot it.
    if not (18.0 <= lat <= 72.0 and -180.0 <= lon <= -66.0):
        return None
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

    found = missed = 0
    for i, (city, state) in enumerate(todo, 1):
        got = ask(city, state)
        key = f"{city}|{state}"
        if got:
            have[key] = got
            found += 1
        else:
            # Recorded as a failure so the next run knows it asked, and so
            # nobody mistakes an absent city for one nobody looked up.
            have[key] = {"lat": None, "lon": None,
                         "query": f"{city}, {state}, United States",
                         "matched": None, "source": "nominatim: no match"}
            missed += 1
            print(f"  no match: {city}, {state}", flush=True)
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)} ({found} found)", flush=True)
        time.sleep(PAUSE)

    OUT.write_text(json.dumps(have, indent=1, sort_keys=True) + "\n")
    live = sum(1 for v in have.values() if v.get("lat") is not None)
    print(f"\n{found} resolved, {missed} unresolved this run")
    print(f"{OUT.name} now holds {len(have)} cities, {live} with coordinates")
    print("\nA city with no coordinate is left with lat null. It is NOT at "
          "0,0 and must never be filtered as though it were somewhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
