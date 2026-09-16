#!/usr/bin/env python3
"""What happened between a company and this board, stored as transitions.

    python3 scripts/employer_log.py                    # the whole trail
    python3 scripts/employer_log.py --company tyler-technologies
    python3 scripts/employer_log.py --on 2026-08-01    # what was true that day
    python3 scripts/employer_log.py --funnel           # claims -> proposals

WHY A LOG AND NOT A FIELD

`data/claims.json` says who holds a company page today. It cannot say when
they claimed it, how long they waited before sending anything, how many of
their corrections we accepted, or whether the six companies that claimed in
August are still here in November. Those are the only questions worth asking
about an employer relationship, and NONE of them is answerable from current
state - not because the code is missing, but because the shape is wrong.

That is the single most repeated finding in the competitor research the owner
brought in: 32 of 137 recruiting vendors carry a reporting weakness, and the
reason is almost always a schema decision rather than a UI gap. A product that
stores where each thing IS cannot answer what the pipeline LOOKED LIKE on a
date, and retrofitting the answer is a rewrite. So the log goes in before there
is anything much to log, which is the only time it is cheap.

The rule, stated once: STORE THE TRANSITION, NEVER THE STATE. `state()` below
replays the transitions to get the state, and that direction is the whole
design. Anything that writes a current value here instead of the moment it
changed has broken the file.

WHAT IS NOT IN HERE, DELIBERATELY

Postings. `data/hiring_history/*.json` already holds an append-only snapshot of
what each company was advertising, refresh.py is the only thing that writes it,
and the daily diff reads it. Re-recording an opening here would be a second
source of truth for the same fact, kept in sync by hand - which is precisely
the failure ("two databases with a sync produce drift, and drift makes every
downstream number a lie") that the one-record rule exists to prevent. This log
covers what a COMPANY DID. What a company is advertising stays where it is.

NO PERSON EVER LANDS IN THIS FILE. The claimant is a DOMAIN and a token tail,
never an address, exactly as sync_claims decided and for the same reason: this
repository is public, and a tracked file is a published file. The scrub below
works by VALUE rather than by key name, because the field somebody adds next
year will not be called `email`.

APPEND, NEVER REWRITE. A line is added with one O_APPEND write and nothing
here ever opens the file for writing. write_atomic() replaces a file, which is
right for companies.json and wrong for an audit trail: a bug in a rewrite path
can truncate history, and history that can be truncated is not evidence. Same
rule the hiring snapshots follow - never hand-edit this file either.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import re
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "employer_events.jsonl"

# Anything address-shaped, matched loosely on purpose: this refuses, and a
# false positive costs a dropped field where a false negative publishes
# somebody's mailbox. Mirrors selftest::check_no_person_in_the_repo.
EMAILY = re.compile(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", re.I)

# THE TRANSITIONS, and each one is a thing that HAPPENED at a moment. A kind
# that reads as a state ("claimed", "active") is the mistake this file is
# built to refuse - see `record()`.
#
# Every kind carries the fields it needs and no others. A kind with no writer
# is not declared: the agent-proposal queue carried a tab, a count and no
# renderer for a month while 131 rows sat unreachable, and a declared-but-
# unwritten event kind is the same defect one layer down. To add one, add it
# here with its required fields AND the code that writes it, in one change.
KINDS = {
    # --- the claim, which is the whole of the employer relationship today ---
    "claim_started": {
        "what": "Somebody at the company asked to claim the page and we mailed "
                "the domain. Proves nothing yet - it is the top of the funnel.",
        "needs": ("domain",),
    },
    "claim_confirmed": {
        "what": "They read the mail at the company's own domain. This is the "
                "moment a claim becomes real.",
        "needs": ("domain",),
    },
    "claim_verified": {
        "what": "The owner looked at a confirmed claim and let it through. THIS "
                "IS THE ONLY ABUSE CONTROL THE FREE TIER HAS: posting is free "
                "because the cost of a bad actor is paid once, here, by a "
                "person, rather than metered forever as a posting fee. A "
                "confirmed address proves somebody reads mail at the domain; "
                "it does not prove the company wants them speaking for it.",
        "needs": ("domain",),
    },
    "claim_refused": {
        "what": "The owner looked and said no. The `why` is the whole value: a "
                "gate that refuses without a reason cannot be audited, and the "
                "next person to look at a similar claim learns nothing.",
        "needs": ("domain", "why"),
    },
    "claim_released": {
        "what": "They handed the claim back, or we took it back. Either way the "
                "page stops saying the company stands behind it.",
        "needs": ("domain",),
    },
    # --- what a claimant sends us, and what we did about it -----------------
    "proposal_sent": {
        "what": "A claimant proposed a correction. A claim says who may send "
                "us things; it never says which of them are true.",
        "needs": ("domain", "proposal_kind"),
    },
    "proposal_accepted": {
        "what": "A person read the proposal and applied it to the map.",
        "needs": ("proposal_kind",),
    },
    "proposal_rejected": {
        "what": "A person read it and said no. The `why` is the training data - "
                "a rejection with no reason teaches nothing later.",
        "needs": ("proposal_kind", "why"),
    },
    "proposal_self_served": {
        "what": "A VERIFIED claimant changed their own page and NOBODY READ IT "
                "FIRST. Deliberately not `proposal_accepted`: that kind says a "
                "person read the proposal and applied it, and recording an "
                "unreviewed edit under it would make every accept-rate this "
                "log reports a lie about how much of the map a person has "
                "actually seen. The gate moved to the claim, once; it did not "
                "disappear.",
        "needs": ("domain", "proposal_kind"),
    },
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def scrub(rec: dict) -> dict:
    """Drop anything that looks like a person's address, by value.

    Checked on every write rather than trusted at the call site, because the
    caller that gets this wrong will be a new one written in a hurry.

    IT SAYS WHAT IT DROPPED. A silent removal is how somebody comes to believe
    a field is on file when it is not - the same defect as an admin action
    reporting "updated" over a body it never read. The names of the removed
    fields are kept; their contents, which are the whole problem, are not.
    """
    out, dropped = {}, []
    for k, v in rec.items():
        if isinstance(v, str) and EMAILY.search(v):
            dropped.append(k)
        else:
            out[k] = v
    if dropped:
        out["scrubbed"] = sorted(dropped)
    return out


def record(kind: str, company_id: str, by: str, why: str | None = None,
           at: str | None = None, **fields) -> dict:
    """Append one transition. Returns the line as written.

    `by` IS REQUIRED AND HAS NO DEFAULT. Nine admin actions once called
    save_companies with the action name alone, so an agent's patch and a
    script's ruling were both journalled as the owner's rulings, and nothing
    noticed because a default is invisible at the call site. The same trap is
    available here and is closed the same way: name the author or get a
    TypeError. `selftest::check_writes_name_their_author` covers the callers.

    `at` is when the thing HAPPENED, which is not always when we heard about
    it: a claim confirmed in Cloudflare KV at 03:00 reaches the repo when
    sync_claims next runs. Pass the real moment or the number is a lie about
    latency.
    """
    if kind not in KINDS:
        # BY NAME, not a raise with a generic message. The caller is usually a
        # new writer with a plausible-sounding kind, and "unknown kind" sends
        # them looking in the wrong file.
        raise ValueError(
            f"{kind!r} is not a transition this log records. Known kinds: "
            + ", ".join(sorted(KINDS))
            + ". Add it to KINDS together with the code that writes it.")
    if not by or not isinstance(by, str):
        raise ValueError(
            "every event names its author: 'owner', 'agent:<name>', "
            "'script:<name>' or 'claimant'. There is no default on purpose.")
    if "@" in by:
        # Authorship here is a handle. A signed-in address stamped into a
        # ruling is the exact write check_no_person_in_the_repo refuses.
        raise ValueError(f"{by!r} is an address, not a handle")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", company_id or ""):
        raise ValueError(f"{company_id!r} is not a company id")

    missing = [f for f in KINDS[kind]["needs"]
               if not (fields.get(f) or (f == "why" and why))]
    if missing:
        raise ValueError(f"{kind} needs {', '.join(missing)}")

    ev = {"at": at or _now(), "kind": kind, "company_id": company_id, "by": by}
    if why:
        ev["why"] = str(why)[:600]
    ev.update(fields)
    # A TIMESTAMP IS NOT A KEY - claim.js learned this when three proposals
    # sent in one click landed on the same millisecond and overwrote each
    # other. Nothing here is keyed by time, but two events at the same second
    # still have to be tellable apart when something references one.
    ev["event_id"] = secrets.token_hex(4)
    ev = scrub(ev)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(ev, sort_keys=False) + "\n"
    # O_APPEND: the write lands at the end whatever else is writing, and no
    # code path here can shorten the file.
    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return ev


def seen_keys() -> set[tuple[str, str]]:
    """(kind, source_key) for every event that carries one.

    Read once by a caller replaying a batch, rather than per line, so a sync
    of a few hundred proposals is one pass over the file instead of a few
    hundred.
    """
    return {(e["kind"], e["source_key"]) for e in events()
            if e.get("source_key")}


def record_once(kind: str, company_id: str, by: str, source_key: str,
                seen: set | None = None, **kw) -> dict | None:
    """Record a transition that came from somewhere else, at most once.

    THE LOG IS A PROJECTION OF CLOUDFLARE KV, and a projection has to be
    replayable. sync_claims runs nightly over the same KV records; without
    this, one confirmed claim becomes one `claim_confirmed` event per night
    and every count built on the log is wrong by however many times the job
    has run. `source_key` is the fact's identity in the system it came from -
    a KV key, or the fields that make the upstream event unique.

    Returns None when the event was already on file. A caller that ignores
    that return and reports "recorded" either way is telling somebody a write
    landed when nothing changed, which is the defect `act_patch` shipped.
    """
    if not source_key:
        raise ValueError("record_once needs the upstream identity of the fact")
    seen = seen_keys() if seen is None else seen
    if (kind, source_key) in seen:
        return None
    ev = record(kind, company_id, by, source_key=source_key, **kw)
    seen.add((kind, source_key))
    return ev


def events(company_id: str | None = None, since: str | None = None,
           until: str | None = None) -> list[dict]:
    """Every event, oldest first, optionally narrowed.

    `until` is INCLUSIVE of the date given, because "what did it look like on
    1 March" means at the end of 1 March, not at midnight before it.
    """
    if not LOG.exists():
        return []
    out = []
    for raw in LOG.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt line is reported, never skipped in silence: a trail
            # that quietly drops what it cannot read is not a trail.
            print(f"employer_log: unreadable line: {raw[:80]}", file=sys.stderr)
            continue
        if company_id and ev.get("company_id") != company_id:
            continue
        day = str(ev.get("at", ""))[:10]
        if since and day < since:
            continue
        if until and day > until:
            continue
        out.append(ev)
    return out


def state(company_id: str, on: str | None = None) -> dict:
    """Replay the transitions to get the state, on a date or today.

    THIS DIRECTION IS THE POINT. Every number the employer side will ever
    report - how many companies held a claim in August, how long a proposal
    waits, whether accept rates are moving - is this function over a date
    range. If a caller ever finds it easier to read a stored current value,
    the stored value is the bug.
    """
    st = {"company_id": company_id, "on": on or dt.date.today().isoformat(),
          "claim_domains": [], "claims_started": 0,
          "proposals_sent": 0, "proposals_accepted": 0, "proposals_rejected": 0,
          "proposals_self_served": 0,
          "first_seen": None, "last_seen": None}
    held: set[str] = set()
    # VERIFICATION IS PER DOMAIN, NOT PER COMPANY. Two people at one company
    # hold separate claims and the owner rules on each; letting one person's
    # pass carry the other's would make the gate a formality for anybody who
    # could find a colleague already through it.
    ok: set[str] = set()
    for ev in events(company_id, until=on):
        k = ev["kind"]
        st["first_seen"] = st["first_seen"] or ev["at"]
        st["last_seen"] = ev["at"]
        if k == "claim_started":
            st["claims_started"] += 1
        elif k == "claim_confirmed":
            held.add(ev.get("domain", ""))
        elif k == "claim_verified":
            ok.add(ev.get("domain", ""))
        elif k == "claim_refused":
            ok.discard(ev.get("domain", ""))
        elif k == "claim_released":
            held.discard(ev.get("domain", ""))
            ok.discard(ev.get("domain", ""))
        elif k == "proposal_sent":
            st["proposals_sent"] += 1
        elif k == "proposal_accepted":
            st["proposals_accepted"] += 1
        elif k == "proposal_rejected":
            st["proposals_rejected"] += 1
        elif k == "proposal_self_served":
            st["proposals_self_served"] += 1
    st["claim_domains"] = sorted(d for d in held if d)
    st["claimed"] = bool(st["claim_domains"])
    # A DOMAIN THAT WAS VERIFIED AND THEN RELEASED IS NOT VERIFIED. Both sets
    # are replayed, and this is their intersection rather than `ok` alone.
    st["verified_domains"] = sorted(d for d in (held & ok) if d)
    st["verified"] = bool(st["verified_domains"])
    return st


def verified_claims() -> dict:
    """{company_id: [claim_tail, ...]} for every claim the owner let through.

    THE ONE ANSWER TO "MAY THIS EDIT LAND UNREVIEWED", and it is replayed from
    the transitions like everything else here rather than read off a stored
    flag. Two readers depend on it and they depend on it for different
    reasons:

      - sync_claims, to decide whether a correction goes to the queue or
        straight onto the map. That is an authorisation decision, and it is
        made HERE - from an append-only file in git that only the owner's gate
        writes - and never from the `verified` flag the Worker keeps on its
        own KV record. The Worker is allowed to be wrong; a Worker whose word
        granted map access would make a bug in the claim endpoint into a way
        to edit the board.
      - build_site, to publish the tails so the endpoint can tell a verified
        claimant their edit goes live rather than into a queue. That one is
        only a message, which is why it is safe to publish.

    A RELEASED OR REFUSED CLAIM DROPS OUT. Verification is not a property the
    company keeps; it is the state of one claim, and handing the claim back
    ends it.
    """
    out: dict = {}
    for ev in events():
        cid, tail = ev.get("company_id"), ev.get("claim_tail")
        if not cid or not tail:
            continue
        held = out.setdefault(cid, set())
        if ev["kind"] == "claim_verified":
            held.add(tail)
        elif ev["kind"] in ("claim_refused", "claim_released"):
            held.discard(tail)
    return {c: sorted(t) for c, t in out.items() if t}


def funnel(since: str | None = None, until: str | None = None) -> dict:
    """Where companies drop out between hearing about us and correcting us.

    Every stage reports the count AND what we do not know, because a funnel
    that prints only what it counted reads as complete. A claim started
    yesterday has not failed to confirm; it has not had time.
    """
    evs = events(since=since, until=until)
    by_kind = collections.Counter(e["kind"] for e in evs)
    started = {e["company_id"] for e in evs if e["kind"] == "claim_started"}
    confirmed = {e["company_id"] for e in evs if e["kind"] == "claim_confirmed"}
    verified = {e["company_id"] for e in evs if e["kind"] == "claim_verified"}
    refused = {e["company_id"] for e in evs if e["kind"] == "claim_refused"}
    proposed = {e["company_id"] for e in evs if e["kind"] == "proposal_sent"}
    ruled = by_kind["proposal_accepted"] + by_kind["proposal_rejected"]
    return {
        "events": len(evs),
        "companies_started": len(started),
        "companies_confirmed": len(confirmed),
        # THE STAGE THAT IS A PERSON. Everything either side of it is a
        # machine answering in milliseconds; this one waits on the owner
        # opening a queue, so it is where the funnel will actually bend and
        # the number worth watching is the WAIT, not the pass rate.
        "companies_verified": len(verified),
        "companies_refused": len(refused),
        "companies_confirmed_awaiting_the_owner":
            len(confirmed - verified - refused),
        "companies_that_sent_something": len(proposed),
        "proposals_sent": by_kind["proposal_sent"],
        "proposals_accepted": by_kind["proposal_accepted"],
        "proposals_rejected": by_kind["proposal_rejected"],
        # NOT AN ACCEPT RATE UNTIL SOMETHING WAS RULED. A rate over zero
        # rulings prints 0% and reads as "we reject everything", which is the
        # same false claim as a page scan reporting "no listings" when it
        # could not read the page.
        "accept_rate": (round(by_kind["proposal_accepted"] / ruled, 3)
                        if ruled else None),
        "proposals_awaiting_a_person": by_kind["proposal_sent"] - ruled,
        # NOT COUNTED AS ACCEPTED ANYWHERE ABOVE, on purpose. These landed
        # without a person reading them, and folding them into the accept
        # rate would inflate it with rulings nobody made.
        "proposals_self_served": by_kind["proposal_self_served"],
    }


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--company", help="one company id")
    ap.add_argument("--on", help="replay to this date (YYYY-MM-DD)")
    ap.add_argument("--since")
    ap.add_argument("--funnel", action="store_true")
    a = ap.parse_args()

    if not LOG.exists():
        print("No employer events on file yet. That is a real answer: nothing "
              "has claimed a page since this log started.")
        return 0
    if a.funnel:
        print(json.dumps(funnel(since=a.since, until=a.on), indent=1))
        return 0
    if a.company and a.on:
        print(json.dumps(state(a.company, a.on), indent=1))
        return 0
    for ev in events(company_id=a.company, since=a.since, until=a.on):
        why = f"  ({ev['why']})" if ev.get("why") else ""
        print(f"{ev['at'][:19]}  {ev['kind']:<20} {ev['company_id']:<28} "
              f"by {ev['by']}{why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
