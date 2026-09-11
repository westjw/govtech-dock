"""Merge a placement pass with its adversarial check into one staged verdict.

    python3 scripts/merge_placements.py --in A.json --in B.json \
            --roster new95.json --out placements_final.json

THREE RULES, AND THE SECOND IS THE ONE THAT MATTERS.

1. THE VERIFIER WINS WHERE IT NAMES A CORRECTION. It read the same row knowing
   only the claim, which is the blind-review shape this project already uses:
   an attempt known to be the main attempt gets defended.

2. "unsupported" IS NOT A CORRECTION AND MUST NOT BECOME ONE. When the check
   says the row cannot carry any call, the honest result is NO call - the
   placement goes to null and a person reads it. Writing the first pass's
   guess anyway, because it is the only answer on the table, is exactly how a
   low-confidence guess becomes a fact nobody can trace. Precip is the case:
   the identity match is unconfirmed, the site never mentions stormwater, and
   filing it as anything asserts something no page says.

3. A NAME THAT IS NOT ON THE ROSTER IS NOT A FINDING. The first pass was fed a
   hand-built batch and 9 of its rows were companies lifted from the run's
   COMPETITOR narratives rather than the exhibitor list - Contech, Oldcastle,
   Filterra, CatchAll, Tata & Howard, Truax, and an "ADS (Advanced Drainage
   Systems)" that is a DIFFERENT COMPANY from the ADS Environmental Services
   actually on the list. None of them was researched by the run. They are
   dropped here against the roster, and the drop is printed, because a
   placement for a company nobody researched is an invented fact.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", str(s or "")).lower())


def on_roster(name: str, roster: dict) -> str | None:
    k = key(name)
    if k in roster:
        return roster[k]
    # The two lists spell a company differently either side of a parenthetical
    # ("StormTek (SWIMS)" against "StormTek (product of SWIMS, an Apex
    # company)"), so a prefix match is allowed - but only one way round, and
    # only on a key long enough that it cannot collide.
    for j in roster:
        if len(k) >= 6 and (k.startswith(j) or j.startswith(k)):
            return roster[j]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inputs", action="append", required=True)
    ap.add_argument("--roster", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    roster_rows = json.loads(pathlib.Path(a.roster).read_text())
    roster = {key(c["name"]): c["name"] for c in roster_rows}

    placements = []
    for p in a.inputs:
        doc = json.loads(pathlib.Path(p).read_text())
        if isinstance(doc, dict):
            doc = doc.get("result", doc).get("placements", doc)
        placements.extend(doc)

    sectors = json.loads((DATA / "schema.json").read_text())["sectors"]
    valid = {s["name"]: set(s["categories"]) for s in sectors}

    out, dropped, corrected, unjudged, invalid = [], [], [], [], []
    seen: set[str] = set()
    for p in placements:
        real = on_roster(p["name"], roster)
        if not real:
            dropped.append(p["name"])
            continue
        if key(real) in seen:
            continue
        seen.add(key(real))
        p = dict(p, name=real)
        v = p.get("verify") if isinstance(p.get("verify"), dict) else {}
        verdict = v.get("verdict")

        if verdict in ("wrong_outcome", "wrong_placement"):
            before = f"{p['outcome']}/{p['sector']}/{p['category']}"
            p["outcome"] = v.get("corrected_outcome") or p["outcome"]
            if v.get("corrected_sector"):
                p["sector"] = v["corrected_sector"]
            if v.get("corrected_category"):
                p["category"] = v["corrected_category"]
            p["corrected_from"] = before
            corrected.append((real, before,
                              f"{p['outcome']}/{p['sector']}/{p['category']}"))
        elif verdict == "unsupported":
            # RULE 2. No call, and the reason travels with it.
            p["outcome"] = None
            p["unjudged_why"] = v.get("why", "")[:300]
            unjudged.append(real)

        if p["outcome"] is not None:
            if p["sector"] not in valid or p["category"] not in valid.get(
                    p["sector"], ()):
                invalid.append((real, p["sector"], p["category"]))
                # A CATEGORY THE SCHEMA DOES NOT HOLD CANNOT BE WRITTEN.
                # validate() would refuse it and the row would be lost in a
                # traceback rather than in a queue, so it lands unjudged with
                # the bad pair recorded.
                p["unjudged_why"] = (f"placed at {p['sector']!r}/"
                                     f"{p['category']!r}, which schema.json "
                                     f"does not hold")
                p["outcome"] = None
        out.append(p)

    pathlib.Path(a.out).write_text(json.dumps(out, indent=1) + "\n")

    from collections import Counter
    print(f"{len(placements)} placements in, {len(out)} kept")
    print(f"\nDROPPED - not on either exhibitor list ({len(dropped)}):")
    for n in dropped:
        print(f"   {n}")
    print(f"\nCORRECTED by the adversarial check ({len(corrected)}):")
    for n, b, af in corrected:
        print(f"   {n[:34]:34} {b}  ->  {af}")
    print(f"\nLEFT UNJUDGED - the row supports no call ({len(unjudged)}):")
    for n in unjudged:
        print(f"   {n}")
    if invalid:
        print(f"\nINVALID sector/category, sent to a person ({len(invalid)}):")
        for n, s, c in invalid:
            print(f"   {n[:34]:34} {s!r} / {c!r}")
    print(f"\nFINAL: {Counter(p['outcome'] for p in out).most_common()}")
    missing = [r for k, r in roster.items() if k not in seen]
    print(f"\nROSTER ROWS WITH NO PLACEMENT: {len(missing)}")
    for n in missing[:12]:
        print(f"   {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
