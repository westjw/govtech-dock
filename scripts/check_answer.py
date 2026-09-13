#!/usr/bin/env python3
"""Run the write-up and buyer doors over an answer, and write nothing.

    python3 scripts/check_answer.py answer.json
    python3 scripts/check_answer.py answer.json --buyer-only

THE DOOR HAS ONLY EVER BEEN REACHABLE THROUGH INGEST, which means the only
way to find out whether a write-up passes was to store it - and a refusal
stored is a company brief_profile will never offer again. That is right for an
overnight run, where the refusal is the record. It is wrong for anyone
checking their work before submitting it: a person writing a description by
hand, or an agent asked to write one, had no way to ask.

So this is the same two functions, agents.check_profile and agents.check_buyer,
against the same corpus they use at intake - the FULL stored page text, not the
trimmed brief - and it stores nothing at all. Exit 0 when both doors accept.

WHY IT MATTERS THAT THIS IS NOT A SECOND DOOR. A checker that reimplemented
the rules would drift from the one that rules at intake, and then "it passed
the check and was refused anyway" becomes a thing people learn to ignore.
There is one door; this dials it.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                                   # noqa: E402
import write_profiles as wp                                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="a JSON file holding ONE answer")
    ap.add_argument("--id", help="the company it is about, if the file omits it")
    ap.add_argument("--buyer-only", action="store_true",
                    help="the file is a flat buyer answer, with no write-up")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing; the exit code is the answer")
    a = ap.parse_args()

    try:
        got = json.loads(pathlib.Path(a.file).read_text())
    except Exception as exc:
        print(f"could not read {a.file}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(got, dict):
        print("that file is not one answer object", file=sys.stderr)
        return 2

    cid = a.id or got.get("id")
    if not cid:
        print("no company id, in the file or on the command line", file=sys.stderr)
        return 2
    # THE ID IS PINNED THE SAME WAY write_profiles PINS IT. A file naming
    # another company would otherwise be checked against that company's pages.
    prof, sc = wp.split_answer(got, cid, a.buyer_only)

    texts = agents._profile_texts({"id": cid})
    if not texts:
        print(f"no cached pages for {cid!r}, so nothing can be verified against "
              f"them. That is a fact about data/site_pages/, not about the "
              f"answer.", file=sys.stderr)
        return 2

    out = []
    if not a.buyer_only:
        out.append(("write-up", agents.check_profile(prof, texts)))
    out.append(("buyer", wp._buyer_verdict(sc, got, a.buyer_only)))

    bad = 0
    for label, why in out:
        bad += bool(why)
        if not a.quiet:
            print(f"{label:9} {'REFUSED  ' + why if why else 'passes the door'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
