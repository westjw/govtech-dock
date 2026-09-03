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


def pull(kv) -> tuple[dict, list]:
    """Returns (claims by company id, claim proposals)."""
    claims: dict = {}
    for key in kv.keys("claim:"):
        rec = kv.get(key.split("/")[-1] if "/" in key else key) or {}
        if not isinstance(rec, dict) or not rec.get("confirmed"):
            continue
        cid = rec.get("company_id")
        if not cid:
            continue
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
    return claims, props


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
    claims, props = pull(kv)
    print(f"{len(claims)} confirmed claim(s), {len(props)} proposal(s) waiting")
    for cid, rec in sorted(claims.items()):
        print(f"  {cid:28} {rec['domain']}  since {rec['on']}  "
              f"{rec['people']} person(s)")
    blob = json.dumps(claims)
    if EMAILY.search(blob):
        print("REFUSING: an address reached the claims file", file=sys.stderr)
        return 1
    if not a.write:
        print("\ndry run: nothing written")
        return 0
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
