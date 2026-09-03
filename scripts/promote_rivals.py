#!/usr/bin/env python3
"""Rule on competitor shortlists, and write the accepted ones into the map.

    python3 scripts/promote_rivals.py                       # what is waiting
    python3 scripts/promote_rivals.py --show verkada
    python3 scripts/promote_rivals.py --category Police     # read the whole set
    python3 scripts/promote_rivals.py --accept verkada --accept brinc
    python3 scripts/promote_rivals.py --reject auror --why "retail, not police"
    python3 scripts/promote_rivals.py --accept-category Police   # after reading

AGENTS PROPOSE, PEOPLE RULE. `agents.py` says it at length and this is the
door for one kind: nothing here writes a competitor onto a public company page
without somebody having said yes. The map is the thing 2,058 strangers will
read, and "Verkada competes with Palantir" is a claim about two real firms.

WHY THIS IS A SCRIPT AND NOT AN ADMIN QUEUE, for now. The admin's queues each
render evidence a person needs to rule, and a competitor set's evidence is the
other company's own one-line description sitting next to the reason given. That
is a real screen and it should exist. This is the path that unblocks ruling
today; the queue is the better grip and comes next.

--accept-category IS DELIBERATELY NOT A SHORTCUT PAST READING. It refuses
unless --category was printed in this shell first, because a bulk accept over
132 companies is exactly the "one click writes a ruling for 108 companies"
shape journal.py exists to catch. The journal records it as one entry, so
admin_undo.py can take the whole thing back.
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
import agents                                                   # noqa: E402

SEEN = DATA / ".rivals_read"          # which categories have been printed


def pending(store: dict, category: str | None = None) -> list[dict]:
    out = [p for k, p in store.items()
           if p.get("kind") == "rival" and p.get("status") == "pending"
           and (not category or p.get("category") == category)]
    out.sort(key=lambda p: (p.get("category") or "", p.get("id") or ""))
    return out


def show(p: dict, names: dict) -> None:
    who = names.get(p["id"], p["id"])
    print(f"\n  {who}  [{p.get('confidence')}]  {p.get('sector')} / {p.get('category')}")
    if p.get("why"):
        print(f"     thesis: {p['why'][:150]}")
    if not p.get("rivals"):
        print("     NO COMPETITOR on this roster, asserted")
        return
    for r in p["rivals"]:
        print(f"     - {names.get(r['id'], r['id'])[:30]:32} {r.get('why','')[:74]}")


def write_accepted(store: dict, ids: list[str], by: str, why: str) -> int:
    """Land accepted shortlists on companies.json as ONE journalled write."""
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    index = {c["id"]: c for c in seq if c.get("id")}
    today = dt.date.today().isoformat()
    wrote = 0
    for cid in ids:
        p = store.get(f"rival:{cid}")
        if not p or cid not in index:
            continue
        # THE EDGE CARRIES ITS REASON ONTO THE PAGE. A competitor with no
        # stated overlap is the category listing again, and the page has a
        # line for the reason precisely so a reader can disagree with it.
        index[cid]["competitors"] = [
            {"id": r["id"], "why": r.get("why", "").strip()}
            for r in (p.get("rivals") or [])
        ]
        index[cid]["competitors_checked_on"] = today
        # An asserted empty is a FINDING and has to survive the round trip,
        # or the page cannot tell "nobody competes with them" from "nobody
        # has looked" - which are the two states this whole engine exists to
        # keep apart.
        index[cid]["competitors_none_found"] = bool(
            p.get("none_found") or not p.get("rivals"))
        p["status"] = "accepted"
        p["ruled_by"], p["ruled_on"], p["ruled_why"] = by, today, why
        wrote += 1
    if wrote:
        # ACTION IS POSITIONAL AND NAMED, and `by` is passed explicitly: the
        # journal is what admin_undo reads, and CLAUDE.md records 86 agent
        # writes that had to be re-attributed by hand because a caller let
        # `by` default to the owner.
        refused = admin.save_companies(
            companies, "promote-rivals",
            why=why or f"accepted {wrote} competitor shortlist(s)", by=by,
            force=wrote > 50)
        if refused:
            print(f"  REFUSED by the journal: {refused}")
            return 0
        agents.save(store)
    return wrote


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--show")
    ap.add_argument("--accept", action="append", default=[])
    ap.add_argument("--reject", action="append", default=[])
    ap.add_argument("--accept-category")
    ap.add_argument("--why", default="")
    # NO DEFAULT AUTHOR. CLAUDE.md: `by` defaulting to "owner" is "a trap for
    # every write that is not", and 86 writes in companies.json had to be
    # re-attributed by hand because of it. This script is run by agents as
    # readily as by Wyeth, so it refuses to guess which.
    ap.add_argument("--by", default=None,
                    help='who is ruling: "owner", or "agent:<label>". '
                         'Required for anything that writes.')
    a = ap.parse_args()

    # READING NEEDS NO AUTHOR; RULING DOES. Requiring it on --show made a
    # read-only command demand an attribution for a write it never performs,
    # which teaches people to type a value to get past a prompt - and a value
    # typed to get past a prompt is the wrong value.
    if (a.accept or a.reject or a.accept_category) and not a.by:
        ap.error("--by is required to rule: \"owner\", or \"agent:<label>\". "
                 "The journal is what admin_undo reads and what says whose "
                 "judgment a ruling was")

    store = agents.load()
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    names = {c["id"]: c.get("name", c["id"]) for c in seq if c.get("id")}

    if a.show:
        p = store.get(f"rival:{a.show}")
        if not p:
            print(f"no competitor proposal on file for {a.show!r}")
            return 1
        show(p, names)
        return 0

    if a.reject:
        for cid in a.reject:
            p = store.get(f"rival:{cid}")
            if not p:
                print(f"  no proposal for {cid!r}")
                continue
            p["status"] = "rejected"
            p["ruled_by"] = a.by
            p["ruled_on"] = dt.date.today().isoformat()
            p["ruled_why"] = a.why or "rejected"
        agents.save(store)
        print(f"  rejected {len(a.reject)}. The company stays researchable; "
              f"nothing was deleted.")

    if a.accept:
        n = write_accepted(store, a.accept, a.by, a.why or "accepted by hand")
        print(f"  wrote {n} shortlist(s) to companies.json, journalled as one entry")
        return 0

    if a.accept_category:
        read = json.loads(SEEN.read_text()) if SEEN.exists() else []
        if a.accept_category not in read:
            print(f"  REFUSED. Nothing has printed the {a.accept_category} "
                  f"shortlists in this checkout yet.\n"
                  f"  A bulk accept over a whole category is the one-click "
                  f"ruling for 108 companies\n"
                  f"  that journal.py exists to catch. Read them first:\n"
                  f"      python3 scripts/promote_rivals.py "
                  f"--category {a.accept_category}")
            return 1
        ids = [p["id"] for p in pending(store, a.accept_category)]
        n = write_accepted(store, ids, a.by,
                           a.why or f"accepted all {a.accept_category} shortlists")
        print(f"  wrote {n} shortlist(s), journalled as ONE entry. "
              f"Undo with:\n      python3 scripts/admin_undo.py")
        return 0

    rows = pending(store, a.category)
    if not rows:
        print("nothing waiting" + (f" in {a.category}" if a.category else ""))
        return 0

    if a.category:
        for p in rows:
            show(p, names)
        read = json.loads(SEEN.read_text()) if SEEN.exists() else []
        if a.category not in read:
            read.append(a.category)
            SEEN.write_text(json.dumps(read))
        empties = sum(1 for p in rows if not p.get("rivals"))
        print(f"\n  {len(rows)} shortlist(s) in {a.category}, "
              f"{sum(len(p.get('rivals') or []) for p in rows)} edges, "
              f"{empties} asserting no competitor.")
        print(f"  Accept the set:  python3 scripts/promote_rivals.py "
              f"--accept-category {a.category}")
        return 0

    by_cat: dict[str, int] = {}
    for p in rows:
        by_cat[f"{p.get('sector')} / {p.get('category')}"] = \
            by_cat.get(f"{p.get('sector')} / {p.get('category')}", 0) + 1
    print(f"{len(rows)} competitor shortlist(s) waiting on a ruling\n")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4}  {cat}")
    print("\n  Read one category:  python3 scripts/promote_rivals.py "
          "--category <name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
