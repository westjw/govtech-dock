#!/usr/bin/env python3
"""Mint, list and revoke the codes that let somebody into the Job Hunter beta.

    python3 scripts/beta_codes.py                          # what is outstanding
    python3 scripts/beta_codes.py --mint 5 --note "GFOA floor" --write
    python3 scripts/beta_codes.py --revoke JH-7QK2-4M9X --why "asked to be removed" --write

THE CODE IS THE INVITATION, AND THE OWNER IS THE ONLY SOURCE OF ONE. That is
the same shape as the claim gate: everything downstream of it is self-serve
precisely BECAUSE this step is a person handing something to somebody. A code
nobody minted cannot exist, and a beta with an open door is not a closed beta.

WHAT A REDEEMED CODE PROVES, WHICH IS LESS THAN IT LOOKS LIKE. It proves the
holder was given a code by the owner and typed it in. It does not identify
them, does not verify an address, and is not a password - a code is a bearer
token and anybody it is forwarded to holds it too. That is acceptable for a
closed beta whose whole purpose is "let me hand this to twelve people I have
met", and it is NOT acceptable as the only thing between a stranger and
somebody else's employment history. Auth is a separate thing and is not
built; see SPEC-jobhunter.md.

ONE CODE, ONE PERSON. Minting five codes rather than one shared code is what
makes a revoke mean something: a shared code can only be turned off for
everybody at once, and the day one person forwards it there is no way to tell
whose it was. The extra work is a loop.

THE REDEMPTION IS A CONSENT RECORD, and that is deliberate rather than
incidental. SPEC-jobhunter.md (2026-09-18) says a consent record, a
right-to-delete path and a retention period must exist before a real person
is onboarded. The row this writes - which code, redeemed when, what text they
were shown - is the first of the three. It is not the other two, and this
script says so rather than letting a redeemed code read as permission to
start processing somebody's resume.

NO ADDRESSES. The redemption stores what the person typed and the code they
used, never an email - check_no_addresses refuses one in anything tracked
here, and a beta list is not a reason to start keeping a contact database in
a public repository.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

LOG = DATA / "beta_codes.jsonl"
PREFIX = "JH"
# Crockford base32 without I, L, O and U: a code is read off a screen and
# typed by somebody else, and those four are what a person gets wrong.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def mint_one() -> str:
    """JH-XXXX-XXXX. 40 bits from secrets, which is not a guessable space."""
    pick = lambda n: "".join(secrets.choice(ALPHABET) for _ in range(n))
    return f"{PREFIX}-{pick(4)}-{pick(4)}"


def events() -> list[dict]:
    """Every minting and revocation, oldest first."""
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue          # a torn line is not a reason to lose the rest
    return out


def state() -> dict:
    """{code: {minted_on, note, revoked_on, why}} replayed from the log."""
    codes: dict = {}
    for ev in events():
        c = ev.get("code")
        if not c:
            continue
        if ev.get("kind") == "minted":
            codes[c] = {"minted_on": ev.get("on"), "note": ev.get("note") or ""}
        elif ev.get("kind") == "revoked" and c in codes:
            codes[c]["revoked_on"] = ev.get("on")
            codes[c]["why"] = ev.get("why") or ""
    return codes


def append(rows: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def push(kv, codes: dict) -> tuple[int, str]:
    """Publish the live set to KV, which is what the endpoint reads."""
    live = {c: {"minted_on": v["minted_on"], "note": v.get("note", "")}
            for c, v in codes.items() if not v.get("revoked_on")}
    try:
        kv.put("beta:codes", live)
    except Exception as exc:                                # noqa: BLE001
        return 0, f"{type(exc).__name__}: {str(exc)[:70]}"
    return len(live), ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mint", type=int, metavar="N")
    ap.add_argument("--note", default="", help="who or where it is going")
    ap.add_argument("--revoke", metavar="CODE")
    ap.add_argument("--why", default="", help="required to revoke")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    today = dt.date.today().isoformat()
    codes = state()

    if a.revoke:
        c = a.revoke.strip().upper()
        if c not in codes:
            print(f"no code {c!r} was ever minted.")
            return 1
        if codes[c].get("revoked_on"):
            print(f"{c} was already revoked on {codes[c]['revoked_on']}.")
            return 0
        if not a.why:
            print("--why is required to revoke. A revocation with no reason "
                  "cannot be reviewed later, which is the whole point of "
                  "keeping the log.")
            return 1
        if not a.write:
            print(f"would revoke {c}. dry run: nothing written.")
            return 0
        append([{"kind": "revoked", "code": c, "on": today, "why": a.why}])
        print(f"revoked {c}.")
        codes = state()

    elif a.mint:
        if a.mint < 1 or a.mint > 50:
            print("mint between 1 and 50. A beta you cannot name the holders "
                  "of is not a closed one.")
            return 1
        fresh = []
        while len(fresh) < a.mint:
            c = mint_one()
            if c not in codes and c not in fresh:
                fresh.append(c)
        if not a.write:
            print(f"would mint {a.mint}:")
            for c in fresh:
                print(f"  {c}")
            print("\ndry run: nothing written. These are NOT the codes that "
                  "would be minted - re-running picks new ones.")
            return 0
        append([{"kind": "minted", "code": c, "on": today, "note": a.note}
                for c in fresh])
        print(f"minted {a.mint}" + (f" for {a.note!r}" if a.note else "") + ":")
        for c in fresh:
            print(f"  {c}")
        codes = state()

    live = {c: v for c, v in codes.items() if not v.get("revoked_on")}
    dead = {c: v for c, v in codes.items() if v.get("revoked_on")}
    print(f"\n{len(live)} live code(s), {len(dead)} revoked.")
    for c, v in sorted(live.items(), key=lambda kv: kv[1]["minted_on"] or ""):
        print(f"  {c}  minted {v['minted_on']}"
              + (f"  {v['note']}" if v.get("note") else ""))

    # PUBLISH, or say plainly that nothing was published. A code that exists
    # only in this file opens no door, and a mint that looks like it worked
    # while the endpoint has never heard of the code is the corridor problem
    # this repo keeps finding.
    if a.write and (a.mint or a.revoke):
        import sync_claims
        kv = sync_claims._kv()
        if kv is None:
            print("\nNOT PUBLISHED: no CF_* secrets here, so the endpoint "
                  "cannot see these yet. They are recorded in "
                  "data/beta_codes.jsonl; the nightly run publishes them.")
        else:
            n, why = push(kv, codes)
            print(f"\npublished {n} live code(s) to KV."
                  if not why else f"\nPUBLISH FAILED: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
