#!/usr/bin/env python3
"""Assemble the public site into public/, by allowlist.

The repo is private and data/ is full of working files: discovery logs with
per-company failure notes, the acquisition review queue, the 2,777-company
supplier list, submissions, website probe logs. A static host pointed at the
repo root would serve every one of them. So this ships an ALLOWLIST - the two
files the site actually reads - rather than excluding things one at a time and
hoping the list stays complete.

It also sanitises board.json. The site renders a "board could not be read"
chip, and the underlying string is a raw fetch error carrying the ATS API URL
and the company's slug ("HTTP 404 for https://api.lever.co/v0/postings/apptegy").
That is a debugging detail, not something to publish, and it hands a reader the
exact endpoint we probe. The public build replaces it with the fact, and keeps
the detail in the private repo where it is useful.

  python scripts/build_site.py [--out public]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Everything the public site is allowed to serve. Adding a file here is a
# deliberate act; nothing is included by walking a directory.
SHIP = ["index.html"]

# Organization fields the site never reads. Dropping them is not security -
# the data is public job postings - it is not publishing internal bookkeeping
# under a domain that looks authoritative.
DROP_ORG = {"no_board_on_file", "vendor_type", "govtech"}


def sanitize(board: dict) -> dict:
    """Strip debugging detail out of the copy that goes public."""
    stripped = 0
    for o in board.get("organizations", []):
        for k in DROP_ORG:
            o.pop(k, None)
        if o.get("unreadable"):
            # Keep the FACT (the site renders a chip from it) and drop the
            # endpoint. "HTTP 404 for https://api.lever.co/v0/postings/<slug>"
            # tells a reader which API we hit and under what name.
            raw = str(o["unreadable"])
            m = re.match(r"HTTP (\d{3})", raw)
            o["unreadable"] = (f"the board returned HTTP {m.group(1)}" if m
                               else "the board could not be read automatically")
            stripped += 1
        if o.get("ats_note"):
            # Internal review notes, e.g. "cleared on audit: quorum.com sells
            # disaster recovery, not government affairs software".
            o.pop("ats_note", None)
    board["_public"] = True
    return board, stripped


# A refresh that breaks quietly is the failure mode a daily unattended deploy
# invents. Every fetcher this repo has broken in some new way, and the symptom
# is always the same shape: the board still builds, it is just suddenly much
# smaller. Publishing that at 06:00 replaces a good board with a bad one and
# nobody finds out until they look.
#
# So the gate refuses to build on a sharp DROP, and only a drop: growth is
# never suspicious here, and a threshold that fires on growth would have
# blocked every real improvement this month (2,273 -> 4,033 -> 4,199).
MAX_DROP = 0.25          # postings, day over day
MAX_HIRING_DROP = 0.40   # companies showing at least one opening


class StaleData(Exception):
    pass


def previous_snapshot() -> tuple[str, int] | None:
    """The most recent history snapshot BEFORE today's, as (date, count).

    build_board writes one per run, so today's exists by the time this runs.
    """
    snaps = sorted((ROOT / "data" / "history").glob("*.json"))
    if len(snaps) < 2:
        return None
    prev = json.loads(snaps[-2].read_text())
    return prev.get("date", snaps[-2].stem), len(prev.get("ids", []))


def sanity_check(board: dict) -> list[str]:
    """Reasons this board should not be published. Empty means go."""
    bad = []
    postings = len(board.get("postings", []))
    hiring = sum(1 for o in board.get("organizations", []) if o.get("open_roles"))

    if postings == 0:
        bad.append("the board has no postings at all")
    if hiring == 0:
        bad.append("no company shows a single opening")

    prev = previous_snapshot()
    if prev is None:
        # Nothing to compare against is not a failure, it is a first run.
        return bad
    prev_date, prev_n = prev
    if prev_n and postings < prev_n * (1 - MAX_DROP):
        drop = (1 - postings / prev_n) * 100
        bad.append(f"postings fell {drop:.0f}% since {prev_date} "
                   f"({prev_n} -> {postings}), past the {MAX_DROP:.0%} limit")

    # A big fall in companies-with-openings usually means a fetcher broke
    # rather than a market that emptied overnight.
    meta = ROOT / "data" / "meta.json"
    if meta.exists():
        m = json.loads(meta.read_text())
        was = m.get("companies_hiring")
        if was and hiring < was * (1 - MAX_HIRING_DROP):
            bad.append(f"companies with an opening fell from {was} to {hiring}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--force", action="store_true",
                    help="publish even if the sanity gate objects. For when you "
                         "have looked and the drop is real.")
    ap.add_argument("--check-only", action="store_true",
                    help="run the gate and exit; write nothing")
    a = ap.parse_args()

    board_src = json.loads((ROOT / "data" / "board.json").read_text())
    objections = sanity_check(board_src)
    if objections:
        print("the sanity gate is refusing to publish this board:", file=sys.stderr)
        for o in objections:
            print(f"  - {o}", file=sys.stderr)
        if not a.force:
            print("\nA board that shrinks overnight is usually a broken fetcher, not a\n"
                  "market that emptied. Look at the run first. If the drop is real,\n"
                  "re-run with --force.", file=sys.stderr)
            return 1
        print("  (--force given, publishing anyway)", file=sys.stderr)
    else:
        prev = previous_snapshot()
        if prev:
            print(f"sanity gate: {len(board_src['postings'])} postings against "
                  f"{prev[1]} on {prev[0]}, within limits")
    if a.check_only:
        return 0

    out = ROOT / a.out
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    for name in SHIP:
        shutil.copy2(ROOT / name, out / name)

    board, stripped = sanitize(board_src)
    # separators: the site is served gzipped, but 300KB of whitespace is still
    # 300KB the browser has to parse.
    (out / "data" / "board.json").write_text(
        json.dumps(board, separators=(",", ":")))

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {a.out}/: {len(SHIP)} page(s) + data/board.json")
    print(f"  {len(board['postings'])} postings, "
          f"{len(board['organizations'])} organizations")
    print(f"  {stripped} internal error string(s) replaced with the plain fact")
    print(f"  {size / 1e6:.2f} MB on disk, roughly {size / 1e6 * 0.1:.2f} MB over the wire")

    # Say what was deliberately left behind, so the omission is visible rather
    # than assumed.
    left = sorted(p.name for p in (ROOT / "data").iterdir()
                  if p.name != "board.json")
    print(f"\nnot shipped: {', '.join(left)}")
    print("also not shipped: scripts/, .github/, CLAUDE.md, admin.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
