#!/usr/bin/env python3
"""Print a company page's Conferences box as text, off data/board.json.

    python3 scripts/show_conferences_box.py <company id> [<company id> ...]

The weekly conference scrub spot-checks two companies it tagged before it
commits (owner, 2026-10-28: the box must show what they went to and are
going to). This is build_site.co_conferences, the static page's own box,
with the markup taken off - one command with no quoting to get wrong.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site as bs                                         # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    board = json.loads((ROOT / "data" / "board.json").read_text())
    orgs = {o.get("id"): o for o in board.get("organizations") or []}
    bad = 0
    for cid in argv:
        o = orgs.get(cid)
        if not o:
            print(f"{cid}: no such company on the board")
            bad += 1
            continue
        box = bs.co_conferences(o, board)
        text = re.sub(r"\s*\|\s*(\|\s*)+", " | ", re.sub(r"<[^>]+>", " | ", box)).strip(" |")
        print(f"{cid}: {text or '(no Conferences box: no conference tag reaches this company)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
