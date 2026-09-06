#!/usr/bin/env python3
"""Ask who competes with a company that already has a write-up.

    python3 scripts/find_rivals.py --category Police --limit 5 --dry-run
    python3 scripts/find_rivals.py --id brinc

The v2 competitor engine's runner. The brief and the door were written first
and nothing called them, which in this repo is not a neutral state: a door
that is never consulted reads exactly like a door that works.

WHAT THIS IS AND IS NOT. It briefs, it asks, and it hands the answer to
agents.ingest, which is the only door. It does not search: THE PIPELINE HAS
NO WEB. That is the whole reason `searches` is required on a proposal and why
an add without one is refused - a model naming a competitor with nothing
behind it is completing a pattern about a company name, and this repo exists
because that failure looks exactly like research.

So this runner produces proposals from what the model already knows, and the
door refuses every one of them that carries an add. That sounds useless and
is not: it is the honest half. What lands from here is the KEEP and DROP
judgment on edges already on file, plus a recorded set of search queries that
a routine with the web can run later. A run with no web is allowed to say
"here is what I would look up" and is not allowed to say "here is what I
found".

USE --web ONLY FROM A ROUTINE THAT ACTUALLY HAS ONE. The flag does not give
this script a browser; it tells the door that the caller performed the
searches recorded in the proposal, and the door still fetches and verifies
every source before anything can be accepted (promote_rivals.py --verify).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                                   # noqa: E402
import llm                                                      # noqa: E402

RULES = """You are deciding who a buyer would put on a shortlist beside ONE
company, and what you return is a PROPOSAL that a door checks and a person
reads. Nothing you say is published by saying it.

You are given that company's WRITE-UP, built from its own pages and already
accepted, plus the edges already on file and the roster of companies this
board tracks in the same category.

RETURN:
  keep   - ids from `existing` that still belong. A subset, never a way to add.
  drop   - ids from `existing` that do not, each with a reason of at least 15
           characters saying why. Proposed only; a person applies them.
  add    - competitors NOT already on the list. Each needs a name, a website,
           a reason, and a `source` of {url, quote} from a page that is not
           the company's own site and not LinkedIn.
  searches - EVERY search you ran, as {q, hits}.

THE HARD RULE: an `add` with no recorded search is refused. If you have no
web access in this run, return `add: []` and put the queries you WOULD run
into `searches` with empty hits. That is a complete and useful answer. What
is not acceptable is naming a company you remember and presenting it as
something you found.

DO NOT RETURN IDS FOR ADDS. Give the name and the website; resolution to a
board id happens at the door, and an agent that resolves ids invents them.

keep plus add may not exceed 8. A buyer carries two to six names into a room,
not a category listing.

An empty answer is a real one: nobody here competes with them, said out loud.

Return JSON only:
{"id": "<the company id>", "confidence": "high|medium|low",
 "why": "the thesis in one sentence", "keep": [...], "drop": [...],
 "add": [...], "searches": [...]}
"""


def honest(got: dict, has_web: bool) -> tuple[dict, int]:
    """(proposal, adds dropped). The run's own honesty, enforced not asked for.

    Without web access the caller cannot have FOUND anything, so an add is a
    remembered company name whatever the proposal says about it - and a
    remembered name with a plausible url attached is the exact shape this
    engine exists to refuse. The door would catch a missing `searches`; it
    cannot catch a run that invented one. This can, because it knows whether
    the caller had a browser.

    The keep and drop judgment survives, because that is made against edges
    already on file and needs no web at all.
    """
    if has_web or not got.get("add"):
        return got, 0
    n = len(got["add"])
    got = dict(got)
    got["add"] = []
    got["why"] = ((got.get("why") or "") +
                  f" [{n} add(s) dropped: this run had no web]").strip()
    return got, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--web", action="store_true",
                    help="the CALLER has web access and performed the searches "
                         "recorded in the proposal. This script never has one")
    a = ap.parse_args()

    briefs = agents.brief_rival_web(limit=a.limit, category=a.category,
                                    ids=a.id or None)
    if not briefs:
        print("no company is ready for this. A company needs an ACCEPTED "
              "write-up first: the whole design is understand them, then "
              "search. promote_profiles.py --gate lands those.")
        return 0
    print(f"{len(briefs)} company(ies) with a write-up and no web pass yet")
    if a.dry_run:
        b = dict(briefs[0])
        b["roster"] = b["roster"][:3] + [{"...": f"{len(b['roster'])} total"}]
        print(f"\n--- rules ---\n{RULES[:700]}\n...")
        print(f"\n--- brief for {b['name']} ---\n{json.dumps(b, indent=1)[:1800]}")
        return 0
    if not llm.key():
        print("no key configured: nothing asked, nothing spent.")
        return 0

    out, refused = [], 0
    for b in briefs:
        got = llm.ask(RULES, json.dumps(b, indent=1), "rival-web",
                      model=a.model, max_tokens=llm.MAX_OUTPUT, thinking=False)
        if got is None:
            print(f"  {b['name']}: no usable answer")
            continue
        got.setdefault("id", b["id"])
        got["key"] = b["key"]
        got["kind"] = "rival"
        got["existing"] = b["existing"]
        got["website"] = b.get("website")
        got, dropped = honest(got, a.web)
        if dropped:
            print(f"  {b['name']}: dropped {dropped} add(s) - this run had no "
                  f"web, so they were remembered, not found")
        out.append(got)
    rep = agents.ingest("rival", out, model=f"agent:rival-web")
    refused = len(rep.get("refused") or [])
    print(f"\n{rep.get('kept', 0)} proposal(s) stored pending, {refused} refused")
    for r in (rep.get("refused") or [])[:6]:
        print(f"  refused {r.get('key','?')}: {str(r.get('why'))[:96]}")
    calls, usd = llm.spent()
    print(f"{calls} call(s), ${usd:.2f}")
    print("\nRead them:  promote_rivals.py --category <cat>")
    print("Verify web sources before accepting:  promote_rivals.py --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
