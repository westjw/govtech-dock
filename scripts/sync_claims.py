#!/usr/bin/env python3
"""Pull confirmed claims and claimant proposals out of KV into the repo.

    python3 scripts/sync_claims.py            # dry run, prints what it found
    python3 scripts/sync_claims.py --write

THE DIVISION OF LABOUR IS THE DESIGN, and it is the web admin's, for the same
reason. The Worker holds a KV binding and can therefore append opinions and
nothing else; Python reads them, and the map only ever changes behind
`validate()` and the journal. A bug in the claim endpoint can record a wrong
proposal. It cannot corrupt the board.

NO PERSON EVER LANDS IN THIS REPOSITORY. The KV record holds the claimant's
address because mail has to be sent to it; what comes across here is the
COMPANY, the DOMAIN and the date. The repo is about to be public and an email
is a person - the same rule the Users board follows, arrived at from the other
direction. `check_no_person_in_the_repo` is the standing guard.

A claimant's edits arrive as `claim` proposals in the ordinary agent queue,
beside the evidence, and a person accepts them. A claim is a relationship, not
a permission: it says who may send us corrections, never which corrections are
true. The one thing it changes on its own is the badge.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402
import employer_log                                             # noqa: E402

CLAIMS = "claims.json"
EMAILY = re.compile(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", re.I)


def _kv():
    """The same client send_digests uses, or None when nothing is configured.

    Every secret here is optional on purpose: a nightly run must never fail
    because nobody set up claiming.
    """
    acct = os.environ.get("CF_ACCOUNT_ID")
    ns = os.environ.get("CF_KV_NAMESPACE_ID")
    tok = os.environ.get("CF_API_TOKEN")
    if not (acct and ns and tok):
        return None
    import send_digests
    return send_digests.KV(acct, ns, tok)


def scrub(rec: dict) -> dict:
    """What may cross into the repo. Anything address-shaped is dropped by
    VALUE, not by key name, because a field somebody adds later will not be
    called `email`."""
    out = {}
    for k, v in rec.items():
        if isinstance(v, str) and EMAILY.search(v):
            continue
        out[k] = v
    return out


def pull(kv) -> tuple[dict, list, list]:
    """Returns (claims by company id, claim proposals, the whole trail).

    The trail carries the UNCONFIRMED claims too, which the claims file
    deliberately does not: a claim that was started and never confirmed is not
    a claim, so it must never reach the badge - but it is the top of the
    funnel, and a funnel that only counts the people who finished cannot tell
    you where anybody stopped. It goes to employer_log, scrubbed, and nowhere
    else.
    """
    claims: dict = {}
    trail: list = []
    for key in kv.keys("claim:"):
        rec = kv.get(key.split("/")[-1] if "/" in key else key) or {}
        if not isinstance(rec, dict) or not rec.get("company_id"):
            continue
        # THE TAIL, NEVER THE TOKEN. The KV key IS the credential - it is the
        # whole of a claimant's identity, since this project has no passwords
        # - and this repository is public. Six characters distinguish two
        # people at one company and grant nothing, which is the same trade
        # claim.js already made when it stamped `token_tail` on a proposal.
        tok = (key.split("/")[-1] if "/" in key else key).split(":", 1)[-1]
        trail.append(dict(scrub(rec), token_tail=tok[-6:]))
        if not rec.get("confirmed"):
            continue
        cid = rec.get("company_id")
        on = str(rec.get("confirmed_at") or rec.get("created") or "")[:7]
        prior = claims.get(cid) or {}
        # the EARLIEST confirmation is the one the badge names: a second
        # person joining later does not restate when the company arrived
        claims[cid] = {"domain": rec.get("domain"), "on": min(on, prior["on"]) if prior.get("on") else on,
                       "people": (prior.get("people") or 0) + 1}
    props = []
    for key in kv.keys("claimprop:"):
        rec = kv.get(key) or {}
        if isinstance(rec, dict) and rec.get("company_id"):
            props.append(dict(scrub(rec), _key=key))
    return claims, props, trail


def log_trail(trail: list, props: list, write: bool) -> dict:
    """Project the KV records into the employer event log.

    THE LOG IS NOT A SECOND CLAIMS FILE. claims.json answers "who holds this
    page today", which is the one thing the badge needs. This answers "when
    did that happen, and what happened before it" - the questions current
    state structurally cannot hold, and the ones every employer-side number
    will be built from. Both come from the same KV records, so neither can
    drift from the other; they are two projections, not two sources.

    Replayable by construction: each event carries the identity of the KV fact
    it came from, so a nightly run over the same records adds nothing the
    second time.
    """
    seen = employer_log.seen_keys()
    added: dict = {"claim_started": 0, "claim_confirmed": 0, "proposal_sent": 0}
    for rec in trail:
        cid, dom = rec.get("company_id"), rec.get("domain")
        tail = rec.get("token_tail") or ""
        if not (cid and dom and tail):
            continue
        for kind, when in (("claim_started", rec.get("created")),
                           ("claim_confirmed", rec.get("confirmed_at"))):
            if not when:
                continue
            if not write:
                if (kind, f"{cid}:{tail}:{when}") not in seen:
                    added[kind] += 1
                continue
            if employer_log.record_once(
                    kind, cid, by="script:sync-claims",
                    source_key=f"{cid}:{tail}:{when}", seen=seen,
                    at=when, domain=dom, token_tail=tail):
                added[kind] += 1
    for p in props:
        cid, key = p.get("company_id"), p.get("_key")
        kind, dom = p.get("kind"), p.get("by_domain")
        # A proposal with no domain on it cannot be attributed, and an event
        # that cannot say who sent it is worse than no event: it inflates
        # every count built on the log while proving nothing. It is left
        # unlogged and the sync says how many, rather than filled in.
        if not (cid and key and kind and dom):
            added["unattributable"] = added.get("unattributable", 0) + 1
            continue
        if not write:
            if ("proposal_sent", key) not in seen:
                added["proposal_sent"] += 1
            continue
        if employer_log.record_once(
                "proposal_sent", cid, by="claimant", source_key=key, seen=seen,
                at=p.get("at"), domain=dom, proposal_kind=kind,
                token_tail=p.get("token_tail") or ""):
            added["proposal_sent"] += 1
    return added


def as_proposals(props: list, companies: list) -> list:
    """Claimant edits in the shape agents.ingest takes."""
    names = {c["id"]: c.get("name") for c in companies if c.get("id")}
    out = []
    for p in props:
        cid = p["company_id"]
        if cid not in names:
            continue
        out.append({
            "key": f"claim:{cid}:{p.get('at','')}",
            "kind": "claim", "id": cid, "name": names[cid],
            "confidence": "medium",
            "why": f"sent by somebody at {p.get('by_domain') or 'the company'}",
            "evidence": f"claimed page, confirmed at {p.get('by_domain')}",
            "edit": {k: v for k, v in p.items() if not k.startswith("_")},
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    kv = _kv()
    if kv is None:
        print("no CF_* secrets set; claiming is not configured. Nothing to do.")
        return 0
    claims, props, trail = pull(kv)
    print(f"{len(claims)} confirmed claim(s), {len(props)} proposal(s) waiting, "
          f"{len(trail)} claim record(s) in KV")
    for cid, rec in sorted(claims.items()):
        print(f"  {cid:28} {rec['domain']}  since {rec['on']}  "
              f"{rec['people']} person(s)")
    blob = json.dumps(claims)
    if EMAILY.search(blob):
        print("REFUSING: an address reached the claims file", file=sys.stderr)
        return 1
    would = log_trail(trail, props, write=False)
    new = {k: v for k, v in would.items() if v}
    print("  employer log: " + (", ".join(f"{v} {k}" for k, v in new.items())
                                if new else "nothing new to record"))
    if not a.write:
        print("\ndry run: nothing written")
        return 0
    log_trail(trail, props, write=True)
    bad = admin.save_decisions(CLAIMS, claims, "sync-claims",
                               why=f"{len(claims)} confirmed claim(s)",
                               by="sync-claims", force=len(claims) > 25)
    if bad:
        print(f"REFUSED: {bad}", file=sys.stderr)
        return 1
    rows = as_proposals(props, admin.read_companies())
    if rows:
        rep = agents.ingest("claim", rows, model="claim:company")
        print(f"  {rep['kept']} proposal(s) into the queue, "
              f"{len(rep['refused'])} refused at the door")
    print(f"  wrote data/{CLAIMS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
