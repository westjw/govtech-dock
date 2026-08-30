#!/usr/bin/env python3
"""Which companies are actually pushing right now, from our own snapshots.

    python3 scripts/momentum.py
    python3 scripts/momentum.py --json

A badge saying "active" is better than a number saying "247 clicks", and not
only because it reads better. A click counter measures OUR TRAFFIC. This
measures THEIR HIRING, which is the thing a job seeker actually wants to know
and the thing this board is uniquely able to see: 13 daily snapshots of every
posting at 2,113 companies.

It also means no visitor is tracked to produce it. Nothing is counted about the
person reading the page.

FOUR RULES, AND EACH ONE EXISTS BECAUSE THE OBVIOUS VERSION IS WRONG.

1. THE BASELINE MUST BE COMPARABLE. Posting ids gained a url+location hash on
   2026-08-23, so the 08-22 and 08-23 snapshots share not one id in three
   thousand. Diffing across that boundary reports every posting as new: the
   first run of this reported Palantir +307 and Samsara +217, which is a schema
   change wearing a hiring boom's clothes. Walk forward to the oldest snapshot
   that genuinely overlaps.

2. COUNT FROM THE SNAPSHOT, NOT THROUGH TODAY'S BOARD. Looking up the baseline
   ids in the current posting list silently drops everything that has since
   come off, so the "before" number is only what survived. That produced
   "Samsara went from 0 to 217". The posting id is company::title::hash, so the
   snapshot can be counted on its own.

3. A COMPANY WE COULD NOT READ BEFORE IS NOT A COMPANY THAT WAS NOT HIRING.
   This is the one that matters. Granicus reads 0 to 53 and Adobe 0 to 9 over
   the last week, and neither of them started hiring last week: we started
   READING THEM. Publishing that as their momentum would be printing our own
   crawler's history as somebody else's news, which is the same error as
   reporting a page we could not read as a company with no jobs. A company
   with no postings at the baseline is excluded, because from a snapshot alone
   "they had none" and "we could not see them" are indistinguishable.

4. QUOTA-CARRYING ROLES ONLY. A company opening nine engineering reqs is not
   news to a seller. This board is for people who carry a number.

WHAT IT SAYS TODAY: almost nothing, and that is the correct output. Over the
current comparable window exactly one company qualifies, by one role. The badge
stays dark until the history is deep enough to mean something, the same way the
home banner drops a slide rather than printing a zero. It lights itself as the
snapshots accumulate; nobody has to come back and switch it on.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Two snapshots that share less than this fraction of ids are not two readings
# of the same board. See rule 1.
COMPARABLE = 0.2

# Below this a "surge" is one requisition, which every company posts sometimes.
# It is a floor on the CHANGE, not on the company's size: a two-person vendor
# going from one seller to three is the news, and a giant adding three to two
# hundred is not.
MIN_ADDED = 2
MIN_GROWTH = 0.5          # and it has to be half again as many, at least


def load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def snapshots() -> list[tuple[str, set]]:
    out = []
    for f in sorted((DATA / "history").glob("*.json")):
        d = load(f)
        if d and isinstance(d.get("ids"), list):
            out.append((d.get("date") or f.stem, set(d["ids"])))
    return out


def per_company(ids: set, keep: set | None = None) -> collections.Counter:
    """Postings per company id, read straight off the snapshot. See rule 2."""
    c: collections.Counter = collections.Counter()
    for i in ids:
        if keep is not None and i not in keep:
            continue
        c[i.split("::")[0]] += 1
    return c


def surge(window_days: int = 7) -> dict:
    snaps = snapshots()
    if len(snaps) < 2:
        return {"ready": False,
                "why": "fewer than two snapshots; there is nothing to compare"}
    today, now_ids = snaps[-1]
    base = None
    for date, ids in snaps[:-1]:
        if not ids or not now_ids:
            continue
        if len(ids & now_ids) / min(len(ids), len(now_ids)) >= COMPARABLE:
            base = (date, ids)
            break
    if base is None:
        return {"ready": False,
                "why": "no snapshot on file is comparable with today; the "
                       "posting id scheme changed and nothing since is old "
                       "enough to diff against"}
    base_date, base_ids = base

    board = load(DATA / "board.json") or {}
    quota = {p["id"] for p in board.get("postings", []) if p.get("quota_carrying")}
    names = {o["id"]: o["name"] for o in board.get("organizations", [])}

    was = per_company(base_ids)
    now = per_company(now_ids, keep=quota)

    rows = []
    for cid, n in now.items():
        before = was.get(cid, 0)
        if before == 0:
            continue                      # rule 3
        added = n - before
        # max(before, 1) so removing the rule-3 exclusion above produces a
        # WRONG ANSWER rather than a ZeroDivisionError. A guard whose removal
        # crashes cannot be mutation-tested, and a crash is not the failure
        # anybody would actually ship - the failure is a company being called
        # active because we started reading it.
        if added < MIN_ADDED or added / max(before, 1) < MIN_GROWTH:
            continue
        rows.append({"id": cid, "name": names.get(cid, cid),
                     "was": before, "now": n, "added": added,
                     "growth": round(added / max(before, 1), 2)})
    rows.sort(key=lambda r: (-r["growth"], -r["added"]))
    return {"ready": True, "since": base_date, "on": today,
            "days": len(snaps), "companies": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = surge()
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    if not r["ready"]:
        print(r["why"])
        return 0
    if not r["companies"]:
        # THE HONEST EMPTY. Not "no companies are hiring" - that would be a
        # claim about the market. This is a claim about the window: seven days
        # of comparable history is not long enough for a surge to show, and
        # saying so is the same discipline as the weekly report refusing to
        # dress a quiet week as a good one.
        print(f"nothing to call active yet.\n")
        print(f"  {r['days']} snapshots on file, comparable back to {r['since']}.")
        print(f"  No company we can read at both ends has grown its "
              f"quota-carrying\n  roles by {int(MIN_GROWTH*100)}% and at least "
              f"{MIN_ADDED} roles in that window.")
        print(f"\n  That is a fact about the window, not about the market. The "
              f"badge\n  switches itself on as the history deepens.")
        return 0
    print(f"active since {r['since']} ({r['days']} snapshots on file)\n")
    for c in r["companies"]:
        print(f"  {c['name'][:30]:32} {c['was']:>3} -> {c['now']:<3} "
              f"(+{c['added']}, {c['growth']:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
