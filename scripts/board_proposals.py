#!/usr/bin/env python3
"""The queue for boards found hiding inside a careers page.

find_embedded_ats.py reads a careers page nothing could enumerate and pulls
out the ATS reference the page itself names - Circuit's had a Paylocity board
sitting in its HTML three times over, worth 8 readable postings, after months
of "page scan found no listings".

None of it is written to companies.json, and this is the file that explains
why. Two failure modes, both already met in the real data:

  THE PARENT'S BOARD. Prepared - assistive AI for 911 centres - links to
  Axon's greenhouse board. 500 postings, every one stamped company_name
  "Axon", because Axon acquired them. Wiring that up reports a parent's
  requisitions as the subsidiary's, which is the one thing CLAUDE.md forbids
  by name.

  THE OPERATING ENTITY. Circuit's own board calls itself "TFR Transit Inc".
  Probably right, and "probably" is how a corporate-finance Concourse ended
  up with seven postings on a govtech board.

So each proposal arrives with the evidence a person needs to rule in one
look: what the board calls itself, how many postings it holds, and a sample
of titles. The rule is the same one this repo applies to a stranger's
submission - a claim, not a fact.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FOUND = DATA / "embedded_ats.json"
RULED = DATA / "board_proposal_rulings.json"


def _read(p: pathlib.Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def q_board_proposals(companies, board) -> list:
    """Boards found inside a careers page, awaiting a yes or no.

    Ordered by how much is riding on the answer: a board with 100 postings
    puts 100 rows in front of visitors the moment it is accepted, and a wrong
    one puts 100 of somebody else's.
    """
    found = _read(FOUND, [])
    ruled = _read(RULED, {})
    by_id = {c["id"]: c for c in companies}
    out = []
    for f in found:
        if f["id"] in ruled:
            continue
        c = by_id.get(f["id"])
        if not c:
            continue
        # a board that already produces postings does not need this
        if (c.get("ats") or {}).get("type") not in ("html", "unknown"):
            continue
        n = f.get("postings") or 0
        out.append({
            "id": f["id"], "name": f["name"],
            "description": c.get("description"),
            "website": c.get("website"),
            "sector": c.get("sector"), "category": c.get("category"),
            "found": f["found"],
            "saw": f.get("saw"),
            "times": f.get("times", 1),
            "board_calls_itself": f.get("board_calls_itself"),
            "identity": f.get("identity", "unknown"),
            "identity_why": f.get("identity_why", ""),
            "postings": n,
            "verified": f.get("verified"),
            "titles": f.get("titles") or [],
            # the two things that should make somebody slow down
            "warn_parent": f.get("identity") == "MISMATCH",
            "warn_scale": n >= 150,
        })
    out.sort(key=lambda r: (r["warn_parent"], -r["postings"]))
    return out


def rule(company_id: str, accept: bool, why: str = "", by: str = "owner") -> dict:
    """Record a decision. Accepting does NOT write the ats - the admin does
    that through its own validated path, so this file only ever holds an
    opinion."""
    import datetime as dt
    ruled = _read(RULED, {})
    ruled[company_id] = {"accept": bool(accept), "why": why.strip(),
                         "by": by, "on": dt.date.today().isoformat()}
    RULED.write_text(json.dumps(ruled, indent=1, sort_keys=True) + "\n")
    return ruled[company_id]


def main() -> int:
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    rows = q_board_proposals(companies, board)
    if not rows:
        print("no board proposals waiting."
              " Run scripts/find_embedded_ats.py to make some.")
        return 0
    parent = [r for r in rows if r["warn_parent"]]
    scale = [r for r in rows if r["warn_scale"] and not r["warn_parent"]]
    clean = [r for r in rows if not r["warn_parent"] and not r["warn_scale"]]
    print(f"{len(rows)} boards found inside a careers page\n")
    if parent:
        print(f"REFUSE unless you know better ({len(parent)}) - "
              f"the board says it belongs to somebody else:")
        for r in parent:
            print(f"  {r['name'][:26]:28} board says \"{r['board_calls_itself']}\" "
                  f"· {r['postings']} postings")
    if scale:
        print(f"\nLOOK FIRST ({len(scale)}) - more postings than this company "
              f"plausibly has:")
        for r in scale:
            print(f"  {r['name'][:26]:28} {r['postings']} postings via "
                  f"{r['found']['type']}")
    print(f"\nREADY TO RULE ({len(clean)}):")
    for r in clean[:30]:
        who = f' — board says "{r["board_calls_itself"]}"' if r.get("board_calls_itself") else ""
        print(f"  {r['name'][:26]:28} {r['found']['type']:12} "
              f"{r['postings']:>4} postings{who}")
    if len(clean) > 30:
        print(f"  ... and {len(clean) - 30} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
