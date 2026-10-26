#!/usr/bin/env python3
"""Bring the supplier export up to date with suppliers.json.

    python3 scripts/supplier_refresh.py            # how far behind it is; nothing changes
    python3 scripts/supplier_refresh.py --write    # check, read and rebuild

Owner's list, 2026-10-22, item 28: "have the supplier export rebuild after
each intake, so it stops going stale." It went stale twice. Built 10-05, it
still listed 1,248 removed suppliers on 10-19 and had never placed 502 new
ones; rebuilt 10-19, it was 511 behind three days later. Every floor intake
files suppliers and nothing rebuilt the export, because the rebuild needed
the one checkout holding the page cache. The cache is now one per machine
(supplier_pages.cache_dir), so this runs from any checkout here.

THE THREE STEPS CLAUDE.md LISTED BY HAND, in their order, each its own
process so each keeps its own refusals:
  1. supplier_identity.py --ids <the suppliers it has never judged> --write
                                        (skipped when there are none)
  2. supplier_pages.py --write          reads every site step 1 trusts that
                                        has no page yet, and nothing else
  3. supplier_categories.py --write     rebuilds the export from all of it,
                                        refusing as it always has when the
                                        cache is missing or a published
                                        supplier lost its page

STEPS 2 AND 3 RUN ON EVERY --write, even when no supplier is new. The ids are
not all the export is built from: an intake whose exhibitors are all on file
only adds the show to their tags, and the show's sector is an assignment of
its own and can publish a held supplier. 14 of the 54 intakes in the journal
filed no supplier and tagged up to 1,073, and an id-only test called every
one of them "nothing to do" (review, 2026-10-22). A ruling, a duplicate id
and a conference's department move the export the same way. Step 2 reads
nothing when nothing is new; step 3 is a local rebuild of about three
minutes. A batch of floors can pass --no-hq-export to conference_intake and
run this once at the end.

conference_intake.py runs this after every write that filed or tagged a
supplier (--no-hq-export skips it). A step that fails stops the run and the
export stays as it was: a half-made export is worse than a late one, and
selftest's note says how far behind it is until somebody runs this.

Steps 1 and 2 fetch other people's websites, through the same paced,
robots-honouring readers they always used. No model is called.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import supplier_categories as SC  # noqa: E402


def unjudged(suppliers: list, ident: dict) -> list[str]:
    """Suppliers step 1 must look at, in file order, once each: never looked
    at, or judged under a website that has changed since (`website_was`).

    A MERGE MOVES A WEBSITE. The survivor inherits its twin's address, and
    its verdict was from when it had none: Hill's Pet Nutrition, NACA and
    Petco Love stayed held as "not found" with their own website on file
    (2026-10-26), and so did a corrected one until somebody re-ran step 1."""
    # an id two rows share (supplier_identity's shared_ids) is judged once,
    # under one row's website: it is current when any of its rows matches
    current = {s["id"] for s in suppliers if s["id"] in ident
               and (ident[s["id"]].get("website_was") or "") == (s.get("website") or "")}
    seen, out = set(), []
    for s in suppliers:
        if s["id"] not in current and s["id"] not in seen:
            seen.add(s["id"])
            out.append(s["id"])
    return out


def step(args: list[str]) -> int:
    print(f"$ python3 scripts/{' '.join(args)}", flush=True)
    return subprocess.call([sys.executable, str(ROOT / "scripts" / args[0]), *args[1:]],
                           cwd=ROOT)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    ident = json.loads((DATA / "supplier_identity.json").read_text())["rows"]
    export = json.loads(SC.OUT.read_text()) if SC.OUT.exists() else {}
    d = SC.drift(export, suppliers)
    todo = unjudged(suppliers, ident)
    print(f"the supplier export (built {export.get('generated', 'never')}) is "
          f"{len(d['new'])} supplier(s) behind and lists {len(d['gone'])} gone; "
          f"{len(todo)} supplier(s) never judged or judged under another website; page cache: {SC.PAGES}")
    if not a.write:
        print("(nothing changed: --write runs the three steps)")
        return 0
    if not SC.PAGES.is_dir():
        print(f"refused: no page cache at {SC.PAGES}. Copy it to {SC.SP.SHARED} "
              f"first; without it every published supplier would read as unread.",
              file=sys.stderr)
        return 1
    if todo and step(["supplier_identity.py", "--ids", ",".join(todo), "--write"]):
        print("stopped: step 1 failed, the export is unchanged", file=sys.stderr)
        return 1
    if step(["supplier_pages.py", "--write"]):
        print("stopped: step 2 failed, the export is unchanged", file=sys.stderr)
        return 1
    if step(["supplier_categories.py", "--write"]):
        print("stopped: step 3 refused, the export is unchanged", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
