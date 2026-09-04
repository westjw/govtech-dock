#!/usr/bin/env python3
"""What each company page is still missing, and what is blocking it.

    python3 scripts/ledger.py                  # the whole map
    python3 scripts/ledger.py --id rain-bird   # one company
    python3 scripts/ledger.py --blocked        # what is waiting on what

THE POINT. The admin's queues are seventeen separate questions and a person
answering one of them cannot see that the answer opened three more. Wiring
Rain Bird's board turned ten invisible roles into ten live ones, made its
description writable for the first time, and put three unclassified titles
into another queue - and nothing said so. A queue that cannot tell you what
your last ruling bought is a queue that feels like filing.

So: one place that knows what a finished company page looks like, which
facts a given company has, and which of the missing ones are BLOCKED on a
fact nobody has yet rather than merely absent. That last distinction is the
whole idea. 877 companies have no board, and for every one of them the
description, the roles and the competitors are not gaps a person can close -
they are downstream of a board nobody has found. Working them in any other
order is work that will be redone.

IT DESCRIBES, IT DOES NOT RULE. Nothing here writes, and nothing here decides
that a company SHOULD have a fact - a company with no public job board is a
finished state, not a gap, and `absent` is how coverage.py has said that for
months. The ledger reports what is true and what depends on what; a person
still decides whether it matters.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402

# WHAT A FINISHED COMPANY PAGE HOLDS, and what each fact needs first.
# `needs` is the honest dependency, not a workflow somebody preferred: a
# description is written FROM the company's own pages, so it cannot start
# before there is a website to read. Roles come off a board. Competitors are
# judged against a description of what the company sells.
FACTS = {
    "website":     {"label": "a website",            "needs": []},
    "board":       {"label": "a job board",          "needs": ["website"]},
    "description": {"label": "a description",        "needs": ["website"]},
    "competitors": {"label": "a competitor shortlist", "needs": ["description"]},
    "founded":     {"label": "a founding year",      "needs": []},
    "location":    {"label": "a location",           "needs": []},
}

# The queue that answers each missing fact, so the ledger can point rather
# than describe. None means no queue asks this question yet.
ANSWERED_BY = {
    "website": "websites", "board": "boards", "description": "profiles",
    "competitors": None, "founded": "founded", "location": None,
}

# OPEN ROLES ARE NOT A FACT ANYBODY SUPPLIES, and the first version of this
# file listed 813 companies as "missing" them. A company with a readable
# board and nothing open is not incomplete - it is not hiring, which is a
# finished state and one this project has always been careful to say out
# loud. Roles are reported beside the ledger, never inside it.


def facts(c: dict, org: dict | None) -> dict:
    """Which facts this company has. One place, so every reader agrees."""
    o = org or {}
    ats = (c.get("ats") or {}).get("type")
    prof = c.get("profile")
    return {
        "website": bool(c.get("website")),
        # `unknown` means nobody has looked yet; a real type means we have one
        "board": ats not in (None, "unknown"),
        "description": isinstance(prof, dict) and bool(prof.get("paragraphs")),
        "competitors": bool(c.get("competitors")),
        "founded": bool(c.get("year_founded")),
        "location": bool(c.get("location")),
    }


def state(c: dict, org: dict | None) -> dict:
    """have / missing / blocked, and what each blocked fact is waiting on.

    MISSING AND BLOCKED ARE DIFFERENT FACTS and conflating them is what makes
    a queue feel endless. A description with no website is not work waiting
    for a person; it is work waiting for a different ruling.
    """
    has = facts(c, org)
    have = sorted(k for k, v in has.items() if v)
    missing, blocked = [], {}
    for k, v in has.items():
        if v:
            continue
        unmet = [n for n in FACTS[k]["needs"] if not has.get(n)]
        if unmet:
            blocked[k] = unmet
        else:
            missing.append(k)
    return {"id": c.get("id"), "name": c.get("name"),
            "have": have, "missing": sorted(missing), "blocked": blocked,
            "complete": not missing and not blocked}


def unlocked(before: dict, after: dict) -> dict:
    """What a ruling bought: facts gained, and work it made possible.

    This is the sentence the admin could never say. Wiring a board does not
    only add a board - it takes `roles` out of blocked and puts it in reach,
    which is the difference between "one row ruled" and "one row ruled, and
    here is what it opened".
    """
    gained = sorted(set(after["have"]) - set(before["have"]))
    freed = sorted(set(before["blocked"]) - set(after["blocked"])
                   - set(after["have"]))
    return {"gained": gained, "now_possible": freed,
            "still_blocked": after["blocked"]}


def load() -> tuple[list, dict]:
    companies = admin.read_companies()
    board = json.loads((DATA / "board.json").read_text())
    return companies, {o["id"]: o for o in board.get("organizations", [])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--blocked", action="store_true")
    a = ap.parse_args()
    companies, orgs = load()

    if a.id:
        c = next((x for x in companies if x.get("id") == a.id), None)
        if not c:
            print(f"no company {a.id!r}")
            return 1
        s = state(c, orgs.get(a.id))
        print(f"{s['name']}")
        print(f"  has      : {', '.join(FACTS[k]['label'] for k in s['have']) or 'nothing'}")
        print(f"  missing  : {', '.join(FACTS[k]['label'] for k in s['missing']) or 'nothing'}")
        for k, on in sorted(s["blocked"].items()):
            q = ANSWERED_BY.get(on[0])
            print(f"  blocked  : {FACTS[k]['label']} waits on "
                  f"{', '.join(FACTS[n]['label'] for n in on)}"
                  + (f"  ({q} queue)" if q else ""))
        return 0

    rows = [state(c, orgs.get(c.get("id"))) for c in companies]
    n = len(rows)
    done = sum(1 for r in rows if r["complete"])
    print(f"{n} companies · {done} complete ({done/n:.1%})\n")
    hiring = sum(1 for c in companies if (orgs.get(c.get("id")) or {}).get("open_roles"))
    print(f"  ({hiring} are hiring right now, which is a fact about them "
          f"rather than a gap in the map)\n")
    have = collections.Counter(k for r in rows for k in r["have"])
    print("what the map holds:")
    for k in FACTS:
        print(f"  {FACTS[k]['label']:24} {have[k]:5}  {have[k]/n:5.0%}")

    if a.blocked:
        print("\nwhat is waiting on what:")
        pairs = collections.Counter(
            (k, on[0]) for r in rows for k, on in r["blocked"].items())
        for (k, on), cnt in pairs.most_common():
            q = ANSWERED_BY.get(on)
            print(f"  {cnt:5} × {FACTS[k]['label']:24} waits on {FACTS[on]['label']}"
                  + (f"  -> the {q} queue" if q else ""))
        print("\nwork a person can actually start now:")
        openable = collections.Counter(k for r in rows for k in r["missing"])
        for k, cnt in openable.most_common():
            q = ANSWERED_BY.get(k)
            print(f"  {cnt:5} × {FACTS[k]['label']:24}"
                  + (f"  -> the {q} queue" if q else "  (no queue asks this yet)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
