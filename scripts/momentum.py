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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import roles  # noqa: E402

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


def _title_of(pid: str) -> str:
    """The title out of a posting id.

    `company::title::hash`, and the title itself can contain a colon, so it is
    everything between the first and last separator rather than field [1].
    """
    parts = pid.split("::")
    return "::".join(parts[1:-1]) if len(parts) >= 3 else (
        parts[1] if len(parts) == 2 else "")


def per_company(ids: set, quota_only: bool = False) -> collections.Counter:
    """Postings per company id, read straight off the snapshot. See rule 2.

    BOTH SIDES MUST BE COUNTED THE SAME WAY, and the first version of this was
    not. It took `keep=quota`, a set of TODAY's quota-carrying posting ids, and
    the caller passed it for `now` and omitted it for `was` - so the comparison
    was quota-roles-today against all-roles-then. 125 companies came out with a
    negative `added` and the function returned nobody. That empty output was
    reported as "the honest empty"; it was arithmetic error wearing honesty's
    clothes, which is worse than a wrong number because it argues for itself.

    A set of today's ids cannot answer "was this a seller req in August" - the
    posting may be gone. But the id CONTAINS the title, and classify is the
    same rule the board uses, so the question can be asked of both snapshots
    identically. That is the only basis on which a difference means anything.
    """
    c: collections.Counter = collections.Counter()
    for i in ids:
        if quota_only and not roles.is_quota_carrying(_title_of(i)):
            continue
        c[i.split("::")[0]] += 1
    return c


def surge() -> dict:
    """The comparable window, whatever length it happens to be.

    THIS TOOK A `window_days: int = 7` PARAMETER AND IGNORED IT. Nothing
    passed one, and passing 30 would have returned the identical answer,
    because rule 1 decides the window: walk back to the oldest snapshot that
    still shares ids with today and stop. A caller reading the signature would
    have believed they were asking about a week.

    The real window is a fact about our own history, not a setting - it is
    eight days today because that is how far back the id scheme reaches, and
    it lengthens on its own. `since` is returned so a reader never has to
    assume it, and every caller prints it.
    """
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
    names = {o["id"]: o["name"] for o in board.get("organizations", [])}

    was = per_company(base_ids, quota_only=True)
    now = per_company(now_ids, quota_only=True)

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
