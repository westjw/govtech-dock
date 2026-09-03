#!/usr/bin/env python3
"""The one door every agent proposal passes to become a fact on the map.

    python3 scripts/proposal_rulings.py                          # what waits
    python3 scripts/proposal_rulings.py --show read:aladtec
    python3 scripts/proposal_rulings.py --accept read:aladtec --by owner
    python3 scripts/proposal_rulings.py --reject board:x --why "parent's" --by owner

THE OTHER END OF THE SPINE. agents.py has written proposals into
data/agent_proposals.json since August, and the admin grew a queue tab for
them with no renderer behind it and no action to accept one - 131 rows a
person could count and never rule. promote_rivals.py was written as a CLI
for one kind because there was nowhere else to put it. This is the applier
for EVERY kind, in one place, used by the admin action, the web admin's
apply step, and the CLI alike, so the rules hold identically whoever is at
the keyboard.

ACCEPTING IS DISPATCHED BY KIND, AND EACH KIND KEEPS ITS OWN GATE. A read
proposal lands through act_capture, which dedupes on the board's key and
names the titles that do not read like jobs. A board proposal is verified
with a real fetch and asked whose it is, and a MISMATCH refuses without
force - the two failures already met here are a subsidiary pointing at its
parent's Workday and an operating entity under another name. A bucket
proposal lands through act_place, which refuses a low-confidence placement
taken in silence. A rival proposal lands through promote_rivals, cap and
all. Nothing here reimplements a gate that exists; a gate reimplemented in
two places is two gates that drift.

A KIND WITH NO APPLIER SAYS SO. profile, news and claim are declared in
agents.KINDS ahead of their appliers so the queue can show them; accepting
one returns an explicit refusal naming the kind. It does not pretend to
land it, and it does not raise KeyError inside a request handler.

REJECTING DELETES NOTHING. The row is marked rejected with who, when and
why, and the company stays proposable. A ruling is training data, and a
reject that vanished would teach the next run nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402

# Kinds this door cannot land, and WHY each one, because "no applier yet" is
# two different facts. `profile` has an applier - promote_profiles.py - but it
# lands a whole category at once behind a gate review, which is the owner's
# ruling on how 2,000 write-ups reach public pages; ruling one here would walk
# past the gate. `news` is a parser with no proposals at all. `claim` and
# `card` are genuinely unbuilt.
ELSEWHERE = {"profile": "ruled in batches: promote_profiles.py --gate <category>"}
NO_APPLIER = ("profile", "news", "claim", "card")


def _stamp(p: dict, status: str, by: str, why: str) -> None:
    p["status"] = status
    p["ruled_by"] = by
    p["ruled_on"] = dt.date.today().isoformat()
    p["ruled_why"] = (why or "").strip() or None


def _save_store(store: dict, action: str, why: str, by: str) -> str | None:
    """The proposal store goes through the journal like every decision file."""
    return admin.save_decisions("agent_proposals.json", store, action,
                                why=why or "", by=by)


def _accept_read(p: dict, by: str, why: str, force: bool) -> dict:
    companies = admin.read_companies()
    c = next((x for x in companies if x["id"] == p.get("id")), None)
    warn = admin.proposal_warn(p, c)
    if warn and not force:
        return {"error": f"{warn}. Pass force to accept anyway."}
    if not p.get("postings"):
        # A "read produced nothing" is a RECORD, not a task: accepting it
        # writes no posting and only closes the row.
        return {"ok": True, "message": "recorded: the read found nothing; "
                                       "no posting written"}
    return admin.act_capture({"company_id": p["id"], "jobs": p["postings"],
                              "page_url": p.get("evidence") or "", "by": by})


def _accept_board(p: dict, by: str, why: str, force: bool) -> dict:
    import add_company, verify_boards
    cid = p.get("id")
    kind, ref = (p.get("ats_type") or "").strip(), (p.get("ats_ref") or "").strip()
    if not kind or not ref:
        return {"error": "this board proposal names no ats type or ref"}
    companies = admin.read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    block = {"type": kind, "ref": ref}
    ok, detail = add_company.verify(block)
    if not ok:
        return {"error": f"the board does not read right now: {detail}. "
                         f"Nothing was written; a slug is never wired on a "
                         f"proposal alone"}
    said = verify_boards.board_says(kind, ref)
    who = verify_boards.judge(c, said)
    if who["verdict"] == "MISMATCH" and not force:
        return {"error": f"{who['why']}. If they were acquired, record the "
                         f"parent first - otherwise this reports somebody "
                         f"else's requisitions as {c['name']}'s"}
    c["ats"] = block
    if said.get("name") and said["name"].lower() != c["name"].lower():
        c["board_owner"] = said["name"]
    err = admin.validate(companies)
    if err:
        return {"error": err}
    bad = admin.save_companies(companies, "proposal-board",
                               why or f"accepted the {kind} board an agent found "
                                      f"for {c['name']}", by=by)
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{c['name']} now reads from {kind}: {detail}"}


def _accept_bucket(p: dict, by: str, why: str, force: bool) -> dict:
    saw = p.get("saw") or {}
    return admin.act_place({"id": p["id"], "sector": p.get("sector"),
                            "category": p.get("category"), "why": why,
                            "was": saw.get("filed_now"),
                            "proposed": f"{p.get('sector')} / {p.get('category')}",
                            "confidence": p.get("confidence"),
                            "description": saw.get("description"), "by": by})


def _accept_rival(p: dict, by: str, why: str, force: bool, store: dict) -> dict:
    import promote_rivals
    n = promote_rivals.write_accepted(store, [p["id"]], by, why)
    if not n:
        return {"error": "the shortlist could not be written; see the journal"}
    return {"ok": True, "message": f"shortlist written for {p.get('name') or p['id']}"}


def rule(store: dict, key: str, accept: bool, why: str = "", by: str = "",
         force: bool = False) -> dict:
    """Accept or reject one proposal. Returns {ok, message} or {error}."""
    if not by:
        return {"error": "a ruling needs an author: owner, or agent:<label>"}
    p = store.get(key)
    if not isinstance(p, dict):
        return {"error": f"no proposal on file under {key!r}"}
    if p.get("status") != "pending":
        return {"error": f"{key} was already ruled {p.get('status')} by "
                         f"{p.get('ruled_by')} on {p.get('ruled_on')}"}
    kind = p.get("kind")

    if not accept:
        _stamp(p, "rejected", by, why)
        bad = _save_store(store, "proposal-reject", why, by)
        if bad:
            return {"error": bad}
        return {"ok": True, "message": f"rejected {key}; the company stays "
                                       f"proposable, nothing was deleted"}

    if kind in NO_APPLIER:
        where = ELSEWHERE.get(kind)
        return {"error": (f"a {kind} proposal is not ruled here: {where}"
                          if where else
                          f"no applier for a {kind} proposal yet. The queue can "
                          f"show it; nothing can land it. That is a refusal, "
                          f"not a landing")}
    if kind == "read":
        res = _accept_read(p, by, why, force)
    elif kind == "board":
        res = _accept_board(p, by, why, force)
    elif kind == "bucket":
        res = _accept_bucket(p, by, why, force)
    elif kind == "rival":
        res = _accept_rival(p, by, why, force, store)
    else:
        return {"error": f"unknown proposal kind {kind!r}"}
    if res.get("error"):
        return res
    # promote_rivals stamps and saves its own rows; every other kind is
    # stamped here, and the store is written through the journal either way
    if kind != "rival":
        _stamp(p, "accepted", by, why)
    bad = _save_store(store, "proposal-accept", why, by)
    if bad:
        return {"error": bad}
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show")
    ap.add_argument("--accept", action="append", default=[])
    ap.add_argument("--reject", action="append", default=[])
    ap.add_argument("--why", default="")
    ap.add_argument("--by", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    store = agents.load()

    if a.show:
        p = store.get(a.show)
        print(json.dumps(p, indent=1) if p else f"nothing under {a.show!r}")
        return 0 if p else 1

    if a.accept or a.reject:
        if not a.by:
            ap.error("--by is required to rule: \"owner\", or \"agent:<label>\"")
        for key in a.accept:
            res = rule(store, key, True, a.why, a.by, a.force)
            print(f"  {key}: {res.get('message') or 'REFUSED: ' + res.get('error', '')}")
        for key in a.reject:
            res = rule(store, key, False, a.why, a.by, a.force)
            print(f"  {key}: {res.get('message') or 'REFUSED: ' + res.get('error', '')}")
        return 0

    pending = [(k, p) for k, p in store.items()
               if isinstance(p, dict) and p.get("status") == "pending"]
    by_kind: dict[str, int] = {}
    for _, p in pending:
        by_kind[p.get("kind", "?")] = by_kind.get(p.get("kind", "?"), 0) + 1
    print(f"{len(pending)} proposal(s) waiting on a ruling")
    for k, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        tail = (f"   ({ELSEWHERE[k]})" if k in ELSEWHERE
                else "   (no applier yet)" if k in NO_APPLIER else "")
        print(f"  {n:4}  {k}{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
