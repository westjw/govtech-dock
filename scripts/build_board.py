#!/usr/bin/env python3
"""Build the SLED job board and market intelligence dataset.

refresh.py answers one question per company: is anyone hiring an AE? That is the
right question for a prospecting tracker and the wrong one for a job board,
because it discards every non-sales opening before anything is stored.

This keeps the whole board. For each company it fetches every posting, tags it
with a role family and a US determination, and writes:

  data/board.json     every open posting, plus per-company aggregates
  data/history/*.json dated snapshot of posting ids, for repost detection

The market-intelligence signal is the family mix. A company hiring twelve
engineers and no sellers is in a different phase than one hiring eight AEs, and
that difference is invisible if you only ever counted AEs.

  python scripts/build_board.py [--limit N] [--company id] [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402
import roles          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "history"

# sector -> buyer motion tier. 1 = municipal SaaS full-cycle, 2 = adjacent.
TIER = {"General Gov": 1, "Public Works": 1, "Parks & Rec": 1,
        "Public Safety": 2, "Transit & Parking": 2, "K-12 Schools": 2}

# Which applicant-tracking systems actually rank applicants. A resume seeded with
# exact keywords helps on the first two and does close to nothing on the third.
RANKS_HARD = {"workday", "icims", "taleo", "successfactors", "adp", "paycom"}
RANKS_SOFT = {"greenhouse", "lever", "ashby", "smartrecruiters", "jazzhr",
              "workable", "recruitee", "jobvite", "bamboohr", "breezy"}


def ats_tier(kind: str | None) -> str:
    k = (kind or "").lower()
    return "hard" if k in RANKS_HARD else "soft" if k in RANKS_SOFT else "none"


def board_url(c: dict) -> str | None:
    """Where a person can go look themselves. Matters most where extraction fails."""
    a = c.get("ats") or {}
    kind, ref = a.get("type"), a.get("ref")
    if isinstance(ref, str) and ref.startswith("http"):
        return ref
    pat = {"greenhouse": "https://job-boards.greenhouse.io/{r}",
           "lever": "https://jobs.lever.co/{r}",
           "ashby": "https://jobs.ashbyhq.com/{r}",
           "breezy": "https://{r}.breezy.hr",
           "recruitee": "https://{r}.recruitee.com",
           "workable": "https://apply.workable.com/{r}",
           "jazzhr": "https://{r}.applytojob.com/apply",
           "bamboohr": "https://{r}.bamboohr.com/careers",
           "rippling": "https://ats.rippling.com/{r}/jobs",
           "smartrecruiters": "https://careers.smartrecruiters.com/{r}",
           "icims": "https://{r}.icims.com/jobs/search?ss=1"}
    if kind in pat and isinstance(ref, str):
        return pat[kind].format(r=ref)
    return c.get("website")


def phase(families: dict) -> str:
    """What the family mix says about where a company is."""
    total = sum(families.values())
    if total < 4:
        return "too few openings to read"
    gtm = families.get("gtm", 0)
    build = families.get("engineering", 0) + families.get("product", 0) + \
        families.get("data", 0)
    absorb = families.get("cs", 0) + families.get("field", 0)
    if build > gtm * 1.5:
        return "building: hiring mostly engineers and product"
    if gtm > build * 1.5:
        return "selling: a go-to-market push"
    if absorb > gtm:
        return "absorbing: delivery and support for customers already won"
    return "mixed: building and selling at once"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--company")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write-partial", action="store_true",
                    help="allow a --limit/--company run to overwrite the full board")
    ap.add_argument("--delay", type=float, default=0.4)
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    if a.company:
        companies = [c for c in companies if c["id"] == a.company]
    if a.limit:
        companies = companies[:a.limit]

    today = dt.date.today().isoformat()
    postings, orgs, unreadable = [], [], 0

    for c in companies:
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        enumerable = True
        try:
            if kind == "html" and isinstance(ref, str):
                # The html fetcher returns page text for keyword scanning, never
                # discrete titles, so an html company contributed nothing to the
                # board. Try enumerating the links first; most of these pages are
                # JS-rendered and cannot be, which is why they are html-type.
                jobs = ats.fetch_html_titles(ref)
            else:
                jobs = ats.fetch(c["ats"])
            err = None
        except Exception as exc:
            jobs, err = [], str(exc)[:60]
            if kind == "html":
                # A board that exists but cannot be enumerated is a different
                # state from a board that cannot be reached. Saying "no roles"
                # for either is the lie worth avoiding.
                enumerable = False
                err = None
            else:
                unreadable += 1

        fams = collections.Counter()
        kept = 0
        for j in jobs:
            title = (j.get("title") or "").strip()
            if roles.is_junk(title) or roles.is_evergreen(title):
                continue
            loc = j.get("location") or ""
            fam = roles.family(title)
            us = roles.is_us(loc, title)
            fams[fam] += 1
            kept += 1
            postings.append({
                "id": f"{c['id']}::{title}",
                "company": c["name"], "company_id": c["id"],
                "title": title, "family": fam,
                "quota_carrying": roles.is_quota_carrying(title),
                "location": loc, "is_us": us,
                "url": j.get("url") or board_url(c),
                "sector": c["sector"], "category": c["category"],
                "first_seen": today, "source": "ats",
            })

        orgs.append({
            "id": c["id"], "name": c["name"], "sector": c["sector"],
            "category": c["category"], "location": c.get("location"),
            "year_founded": c.get("year_founded"), "description": c.get("description"),
            "website": c.get("website"), "board_url": board_url(c),
            "ats": (c.get("ats") or {}).get("type"),
            "ats_ranks": ats_tier((c.get("ats") or {}).get("type")),
            "tier": TIER.get(c["sector"]),
            "open_roles": kept, "families": dict(fams), "phase": phase(fams),
            "quota_roles": sum(1 for p in postings
                               if p["company_id"] == c["id"] and p["quota_carrying"]),
            "unreadable": err,
            "enumerable": enumerable,
        })
        time.sleep(a.delay)

    # Merge hand-checked findings. These come from companies the fetchers cannot
    # read at all, so an automated run must never delete them: absence from this
    # run means the fetcher still cannot see the company, not that the role closed.
    # Only `manual.py none` closes a manual posting.
    manual_path = DATA / "manual.json"
    manual_count = 0
    if manual_path.exists():
        man = json.loads(manual_path.read_text())
        checks = man.get("checks", {})
        by_id = {o["id"]: o for o in orgs}
        for mp in man.get("postings", []):
            postings.append({**mp, "source": "manual"})
            manual_count += 1
            o = by_id.get(mp["company_id"])
            if o is not None:
                o["open_roles"] += 1
                o["families"][mp["family"]] = o["families"].get(mp["family"], 0) + 1
                if mp.get("quota_carrying"):
                    o["quota_roles"] += 1
                o["phase"] = phase(o["families"])
        for org in orgs:
            chk = checks.get(org["id"])
            if chk:
                org["checked_by_hand"] = chk.get("checked_on")

    # carry first_seen forward so a posting keeps its original date
    prev_path = DATA / "board.json"
    if prev_path.exists():
        prev = {p["id"]: p for p in json.loads(prev_path.read_text()).get("postings", [])}
        for p in postings:
            if p["id"] in prev:
                p["first_seen"] = prev[p["id"]]["first_seen"]

    fam_totals = collections.Counter(p["family"] for p in postings)
    sector_totals = collections.Counter(p["sector"] for p in postings)
    payload = {
        "generated": today,
        "companies_read": len(companies), "unreadable": unreadable,
        "manual_postings": manual_count,
        "totals": {
            "postings": len(postings),
            "quota_carrying": sum(1 for p in postings if p["quota_carrying"]),
            "us": sum(1 for p in postings if p["is_us"] is True),
            "non_us": sum(1 for p in postings if p["is_us"] is False),
            "families": dict(fam_totals), "sectors": dict(sector_totals),
        },
        "organizations": orgs,
        "postings": postings,
    }

    print(f"{len(companies)} companies read, {unreadable} unreadable")
    print(f"{len(postings)} open postings, "
          f"{payload['totals']['quota_carrying']} quota-carrying")
    for f, n in fam_totals.most_common():
        print(f"  {n:>4}  {roles.LABEL.get(f, f)}")
    if a.dry_run:
        print("\n(dry run, nothing written)")
        return 0

    # A partial run must never overwrite the full board. --limit and --company are
    # for testing a fetcher, and writing 3 companies over 137 silently destroys the
    # dataset the site reads. Learned the hard way.
    if (a.limit or a.company) and not a.write_partial:
        print("\npartial run, not written. Pass --write-partial to overwrite the "
              "full board on purpose.")
        return 0

    DATA.mkdir(exist_ok=True)
    HISTORY.mkdir(exist_ok=True)
    prev_path.write_text(json.dumps(payload, indent=1) + "\n")
    # snapshot only the ids: enough for repost detection, small enough to keep
    (HISTORY / f"{today}.json").write_text(json.dumps(
        {"date": today, "ids": sorted(p["id"] for p in postings)}, indent=1) + "\n")
    print(f"\nwrote data/board.json and data/history/{today}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
