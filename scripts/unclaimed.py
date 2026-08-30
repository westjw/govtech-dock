#!/usr/bin/env python3
"""Research that was done and never used. Prints it; changes nothing.

    python3 scripts/unclaimed.py
    python3 scripts/unclaimed.py --file proposed_websites.json

data/ holds six proposed_*.json files that nothing reads. A survey of the
repository called them "480KB of data files with no consumer anywhere", which
reads like a cleanup job. It is not. Opened, they are 612 researched records -
a company, a website, a founding year, a sector with a reason, an HQ - each one
somebody's afternoon, sitting in a file no page renders and no script imports.

The 15 SHARD copies beside them (proposed_websites_0.json and friends) really
were junk: byte-identical subsets of their parents, checked and deleted. The
parents are not junk. They are a queue with no door.

THIS IS NOT THAT DOOR. Nothing here writes: a proposal is a claim, and CLAUDE.md
is explicit that a claim reaches companies.json only through a person ruling on
it with the evidence in front of them. What this does is make the pile VISIBLE -
how many records, how many are about companies we already hold, how many name a
company that does not exist here yet, and how many carry a verdict somebody
already reached and nobody ever applied.

That distinction matters because the failure here is not disk space. It is that
a fact can be researched, written down, and then be invisible - which is the
same failure as a company being hiring, readable, and reported as a zero. The
fix for both is to say out loud what is there.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# What each file was for, in the words of whoever would have to act on it. A
# filename is not a description, and "proposed_locations" tells a person
# nothing about whether it is worth an hour.
ABOUT = {
    "proposed_conferences.json":
        "shows to add to the conference catalogue, with dates and venues",
    "proposed_founded.json":
        "founding years found by research, for companies whose year is blank",
    "proposed_hhs_cards.json":
        "health and human services vendors researched into full company cards",
    "proposed_hhs_exhibitors.json":
        "exhibitors swept off HHS conference floors, not yet triaged",
    "proposed_locations.json":
        "headquarters found for companies carrying no location",
    "proposed_websites.json":
        "websites found for companies with none on file",
}

# Keys a record might use for the company it is about. Different passes wrote
# different shapes, which is itself part of why nothing ever consumed them.
NAME_KEYS = ("company_name", "name", "company", "id", "company_id")


def load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def rows(obj) -> list:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("proposals", "records", "rows", "items"):
            if isinstance(obj.get(k), list):
                return obj[k]
        # a plain id -> record mapping
        if all(isinstance(v, dict) for v in obj.values()):
            return [{"id": k, **v} for k, v in obj.items()]
    return []


def named(r) -> str:
    if not isinstance(r, dict):
        return ""
    for k in NAME_KEYS:
        v = r.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="just this one")
    a = ap.parse_args()

    companies = load(DATA / "companies.json") or []
    if isinstance(companies, dict):
        companies = companies.get("companies", [])
    have_ids = {c.get("id") for c in companies}
    have_names = {slug(c.get("name") or "") for c in companies}
    # what a record would be FILLING IN, so "91 founding years" can be reported
    # as "of which 63 are still blank today" - the only number that says whether
    # the pile is still worth an hour
    blank_year = {c.get("id") for c in companies if not c.get("year_founded")}
    blank_site = {c.get("id") for c in companies if not c.get("website")}
    blank_loc = {c.get("id") for c in companies if not c.get("location")}
    FILLS = {"proposed_founded.json": ("a blank founding year", blank_year),
             "proposed_websites.json": ("a blank website", blank_site),
             "proposed_locations.json": ("a blank location", blank_loc)}

    files = sorted(DATA.glob("proposed_*.json"))
    if a.file:
        files = [f for f in files if f.name == a.file]
        if not files:
            print(f"no data/{a.file}", file=sys.stderr)
            return 1

    total = 0
    for f in files:
        rs = rows(load(f))
        if not rs:
            continue
        total += len(rs)
        names = [named(r) for r in rs]
        known = sum(1 for n in names if n and (slug(n) in have_names or n in have_ids))
        print(f"\n{f.name}  ({len(rs)} record{'s' if len(rs) != 1 else ''})")
        print(f"  {ABOUT.get(f.name, 'no description on file for this one')}")
        print(f"  {known} name a company already on the board, "
              f"{len(rs) - known} do not")
        fill = FILLS.get(f.name)
        if fill:
            label, blanks = fill
            still = sum(1 for n in names
                        if n and (n in blanks or slug(n) in
                                  {slug(c.get('name') or '') for c in companies
                                   if c.get('id') in blanks}))
            print(f"  {still} of them would fill {label} that is STILL blank today")
        # a verdict somebody already reached and nobody applied is the sharpest
        # version of this: the judgment call was made, and it went nowhere
        verdicts: dict[str, int] = {}
        for r in rs:
            v = r.get("verdict") if isinstance(r, dict) else None
            if isinstance(v, str):
                verdicts[v] = verdicts.get(v, 0) + 1
        if verdicts:
            print("  verdicts already reached: "
                  + ", ".join(f"{v} {k}" for k, v in
                              sorted(verdicts.items(), key=lambda kv: -kv[1])))
        for r in rs[:2]:
            n = named(r)
            if n:
                print(f"    e.g. {n}")

    print(f"\n{total:,} researched record{'s' if total != 1 else ''} across "
          f"{len(files)} file(s), and nothing in this repository reads any of "
          f"them.\nEach one needs a person to rule on it, the way every other "
          f"claim here does.\nNothing was written by running this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
