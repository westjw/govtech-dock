#!/usr/bin/env python3
"""Which companies already on the board are horizontal vendors?

    python3 scripts/scope_sweep.py            # print the shortlist
    python3 scripts/scope_sweep.py --write    # into the admin's queue

THE PROBLEM THIS ANSWERS. Accepting a board proposal loads that company's
WHOLE job board - 6,132 postings today, of which 1,394 are engineering. For a
real govtech company that is right: Verkada's engineering reqs are signal
about where a govtech vendor is going. For a horizontal vendor it is noise,
and worse, it is noise wearing this board's name.

Vendor scope already has the answer for that case - `sled`, which sets
`sled_only` and keeps only the roles naming the public sector. But that
answer could only ever be given to a NAME coming through the front door.
A company already on the board could not be flagged at all: build_board reads
`sled_only` and nothing in the admin, the CLI or the pipeline could set it.
Zero companies carry it. This is the shortlist for the person who can.

IT PROPOSES. The queue's own docstring says why, and it is the rule this
whole project turns on: a pattern that quietly rules either way is worse than
a question, and a wrong "not govtech" is invisible and permanent.

TWO SIGNALS, BOTH MEASURED BEFORE THEY WERE TRUSTED, and the first one had to
be read the opposite way round from how it was written down:

  ANOTHER INDUSTRY IN A SALES TITLE. A govtech company has no "Account
  Executive, Healthcare". A horizontal vendor segments by vertical and
  government is one of them. Measured across 150 companies with 3+ sales
  roles: 23 name another industry. Samsara (13 of 116), Motive (9 of 66),
  Emburse, Ookla, ROLLER - all genuinely horizontal. It also flags Handtevy,
  which is real EMS software, so it is a shortlist and not a verdict.

  THE PUBLIC SECTOR NAMED AS A SEGMENT. A govtech company's sales roles do
  not say "public sector", because the whole company is. A horizontal
  vendor's do, because it is one team. OpenAI (4 of 4) and Anthropic (7 of 7)
  are the cleanest examples on the board. This was first written down as
  "a LOW share means horizontal", which is backwards, and the measurement is
  what caught it.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import build_board                                              # noqa: E402

OUT = "scope_sweep.json"
SALES = {"gtm", "field"}
MIN_ROLES = 3          # under this, one odd title is the whole signal

# Another industry, named in a sales title. Kept narrow on purpose: the
# generic words a vendor uses about ANY market ("enterprise", "commercial")
# are here because a govtech company does not segment that way either.
OTHER_VERTICAL = re.compile(
    r"\b(healthcare|health system|life science|pharma|financial services|fintech|"
    r"banking|insurance|retail|e-?commerce|manufactur|automotive|energy sector|"
    r"oil (and|&) gas|telco|telecom|media (and|&) entertainment|hospitality|"
    r"gaming|logistics|enterprise sales|commercial sales|mid-?market|smb)\b", re.I)


def measure(companies: list, board: dict) -> list:
    """One row per company with enough sales roles to say anything."""
    cos = {c["id"]: c for c in companies if c.get("id")}
    titles: dict = collections.defaultdict(list)
    for p in board.get("postings", []):
        if p.get("family") in SALES:
            titles[p["company_id"]].append(p.get("title") or "")
    rows = []
    for cid, ts in titles.items():
        c = cos.get(cid)
        if not c or len(ts) < MIN_ROLES:
            continue
        other = [t for t in ts if OTHER_VERTICAL.search(t)]
        sled = [t for t in ts if build_board.SLED_ROLE.search(t)]
        # A COMPANY ALREADY FLAGGED IS NOT A QUESTION.
        if c.get("sled_only"):
            continue
        why = []
        if other:
            why.append(f"{len(other)} of {len(ts)} sales titles name another "
                       f"industry: {', '.join(sorted({OTHER_VERTICAL.search(t).group(0) for t in other}))[:90]}")
        if sled and len(sled) / len(ts) >= 0.5:
            why.append(f"{len(sled)} of {len(ts)} sales titles label the public "
                       f"sector as a segment, which a govtech company has no "
                       f"reason to do")
        if not why:
            continue
        rows.append({
            "id": cid, "name": c.get("name"),
            "sector": c.get("sector"), "category": c.get("category"),
            "description": c.get("description"),
            "sales_roles": len(ts),
            "other_vertical": len(other),
            "sled_labelled": len(sled),
            "examples": sorted(set(other + sled))[:6],
            "why": why,
            # what accepting costs, said plainly: this is how many postings
            # would stop counting if the answer is sled
            "postings": sum(1 for p in board.get("postings", [])
                            if p["company_id"] == cid),
        })
    rows.sort(key=lambda r: (-r["other_vertical"] - r["sled_labelled"], r["name"] or ""))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    companies = admin.read_companies()
    board = json.loads((DATA / "board.json").read_text())
    rows = measure(companies, board)
    flagged = sum(1 for c in companies if c.get("sled_only"))
    print(f"{len(rows)} compan(y/ies) look horizontal and are not flagged "
          f"({flagged} already carry sled_only)\n")
    for r in rows:
        print(f"  {r['name'][:34]:36} {r['sector']} / {r['category']}")
        print(f"    {r['postings']:4} postings, {r['sales_roles']} sales")
        for w in r["why"]:
            print(f"    - {w}")
        if r["examples"]:
            print(f"    e.g. {r['examples'][0][:74]}")
    if not a.write:
        print("\ndry run: nothing written")
        return 0
    bad = admin.save_decisions(OUT, {"generated": board.get("generated"), "rows": rows},
                               "scope-sweep",
                               why=f"{len(rows)} possible horizontal vendor(s) on the board",
                               by="scope-sweep", force=len(rows) > 25)
    if bad:
        print(f"REFUSED: {bad}", file=sys.stderr)
        return 1
    print(f"\nwrote data/{OUT}; they appear in the admin's Horizontal check queue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
