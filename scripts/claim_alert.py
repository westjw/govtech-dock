#!/usr/bin/env python3
"""Say, once, that claims are waiting on the owner's gate.

    python3 scripts/claim_alert.py $RUNNER_TEMP/claims.md

Prints "alert" when something is waiting, "quiet" otherwise, and writes the
issue body. Modelled on scripts/alert.py, which refresh.yml already uses the
same way.

WHY THIS EXISTS. The owner's hand verification is, in CLAUDE.md's words, the
only abuse control the free tier has. It is a PERSON, on purpose. But nothing
told that person: a company could claim its page, confirm an address at its
own domain, and then wait for the owner to happen to run verify_claims.py
from a terminal. Every other part of the ladder worked. This is the same
shape as the Agent proposals tab that had a count and no renderer, and as
sync_claims.py itself, which shipped whole and had no caller for a day - the
repo keeps building good doors and forgetting the corridor.

IT CARRIES NO NAMES, AND THAT IS THE DESIGN. westjw/govtech-dock is public,
so a GitHub issue is published. Who is claiming which page before the owner
has ruled is the claimant's business and a live commercial signal; the
address is forbidden in anything tracked here by check_no_addresses. So this
says HOW MANY and HOW LONG, and the detail stays behind verify_claims.py,
which reads KV and runs on the owner's own machine. A notification's job is
to say "go and look", not to be the queue.

It is also read-only by construction: it calls waiting() and nothing else. No
CI step may ever rule on a claim - check_every_pipeline_script_has_a_caller
refuses a workflow that passes verify_claims.py a ruling flag.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sync_claims                                              # noqa: E402
import verify_claims                                            # noqa: E402

TITLE = "Claims are waiting on your verification"


def body(rows: list[dict], today: str) -> str:
    """The issue text. Counts and waits only - never a name or an address."""
    ages = []
    for r in rows:
        when = (r.get("confirmed_at") or r.get("created") or "")[:10]
        try:
            ages.append((dt.date.fromisoformat(today)
                         - dt.date.fromisoformat(when)).days)
        except ValueError:
            continue
    oldest = max(ages) if ages else None
    n = len(rows)
    out = [f"**{n} claim{'s' if n != 1 else ''}** confirmed an address at "
           f"their own domain and {'are' if n != 1 else 'is'} waiting on your "
           f"gate."]
    if oldest is not None:
        out.append(f"\nThe longest has been waiting **{oldest} day"
                   f"{'s' if oldest != 1 else ''}**.")
    out += [
        "\nNothing of theirs can land until you rule. That gate is the only "
        "abuse control the free tier has, so it is a person on purpose.",
        "\n```bash",
        "python3 scripts/verify_claims.py",
        "```",
        "\nThat lists who they are, re-checks each domain against the website "
        "on file, and takes `--verify <id> --tail <tail> --write` or "
        "`--refuse <id> --tail <tail> --why ...`.",
        "\n*No names here on purpose: this repository is public, and who is "
        "claiming which page before you have ruled is theirs, not ours. The "
        "detail lives behind the command above.*",
    ]
    return "\n".join(out)


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    # the one KV client, the one place its secrets are read
    kv = sync_claims._kv()
    if kv is None:
        # Same rule as every other optional secret here: a refresh must never
        # fail, or even shout, because nobody set up claiming.
        print("quiet")
        return 0
    rows = verify_claims.waiting(kv)
    if not rows:
        print("quiet")
        return 0
    if out:
        out.write_text(body(rows, dt.date.today().isoformat()))
    print("alert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
