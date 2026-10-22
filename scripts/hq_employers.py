#!/usr/bin/env python3
"""Read which companies have a verified recruiter on SLED HQ, and say where a
company's "Claim this page" goes.

    python3 scripts/hq_employers.py            # read the feed and report; nothing written
    python3 scripts/hq_employers.py --write    # data/hq_employers.json

THE CLAIM MOVES TO HQ (owner's list, 2026-10-22, item 29: "when HQ deploys,
set SLEDJOBS_FEED_TOKEN, then move the claim link to HQ"). The recruiter
side lives on SLED HQ (owner, 2026-10-01) and its login is the one login, so
a person at a company claims it there. Two things follow, and this file
holds both:

  - CLAIMS_ON_HQ. False until HQ answers on its host. True, every "Claim this
    page" (the /c/ pages and the app) opens HQ's /claim?company=<id> instead
    of this site's /claim. One line, because the switch is a date, not a
    design: the day HQ is live it flips, and the day it is not it flips back.
    No claim was ever made on this site's own form (data/claims.json is
    empty), so nothing on file is stranded by the move.
  - The verified list. HQ publishes the companies that have a verified
    recruiter right now (docs/feeds/sledjobs-employers.md in SLED HQ, v1):
    `{"employers": [{"company_id", "verified_since"}]}`, the whole list every
    time. A listed company's page says so and offers "Post a job" on HQ.

WHAT AN ERROR MEANS, as for the jobs feed (hq_jobs.py, whose reader this
uses): any answer but 200 keeps the list on file. An error is never "nobody
is verified". A 200 is the whole list, so a company missing from it has no
verified recruiter now and loses its mark.

A LIST NOBODY HAS READ FOR A WEEK IS NOT ABOUT THE PRESENT. HQ's contract
asks for it: a revocation reaches the board only on a good read. build_board
shows no mark from a list read more than STALE_DAYS ago, and says so in its
log. Nor on a company that has lost its website since the read: nothing
publishes without one.

REFUSED, one row at a time, each with its reason: a company this board does
not hold, a company with no website on file (owner, 2026-10-04: nothing
publishes without one; HQ enforces it too and this does not take its word),
a date that is not YYYY-MM-DD or is in the future, a company listed twice.

The token and the body never reach a log line: the repo and its Actions logs
are public. The feed carries no person, by HQ's contract, and nothing about
a person is kept here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import brand  # noqa: E402
import hq_jobs  # noqa: E402

FEED = f"https://hq.{brand.DOMAIN}/api/feed/employers"
OUT = DATA / "hq_employers.json"
STALE_DAYS = 7

# The switch (above). HQ's claim page takes the SLED JOBS id as ?company=.
CLAIMS_ON_HQ = False
CLAIM_AT = f"https://hq.{brand.DOMAIN}/claim?company="
POST_AT = f"https://hq.{brand.DOMAIN}/recruit"


def claim_at() -> str | None:
    """Where a company's claim link starts, or None for this site's own."""
    return CLAIM_AT if CLAIMS_ON_HQ else None


def refuse_reason(row, companies: dict, today: dt.date, seen: set) -> str | None:
    if not isinstance(row, dict):
        return "not an object"
    cid = row.get("company_id")
    if not isinstance(cid, str):
        return "company_id is not a string"
    c = companies.get(cid)
    if not c:
        return f"company {cid!r} is not on this board"
    if not (c.get("website") or "").strip():
        return "the company has no website on file"
    if cid in seen:
        return "listed twice"
    since = row.get("verified_since")
    # the anchored pattern first: fromisoformat alone takes "20261002" and the
    # ISO week "2026-W40-1", and the page prints the first seven characters
    if not isinstance(since, str) or not hq_jobs.DATE.match(since):
        return "verified_since is not YYYY-MM-DD"
    try:
        day = dt.date.fromisoformat(since)
    except ValueError:
        return "verified_since is not YYYY-MM-DD"
    if day > today:
        return "verified_since is in the future"
    return None


def sort_feed(body: dict, companies: dict, today: dt.date) -> tuple[list, list]:
    took, refused, seen = [], [], set()
    for row in body.get("employers") or []:
        why = refuse_reason(row, companies, today, seen)
        if why:
            refused.append({"company_id": row.get("company_id") if isinstance(row, dict) else None,
                            "why": why})
            continue
        seen.add(row["company_id"])
        took.append({"company_id": row["company_id"], "verified_since": row["verified_since"]})
    took.sort(key=lambda r: r["company_id"])
    return took, refused


def verified(doc: dict | None, now: dt.datetime | None = None) -> dict:
    """{company_id: verified_since} to show, or {} when the list is too old to
    speak for the present (or was never read)."""
    if not doc or not doc.get("read_at"):
        return {}
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        read = dt.datetime.fromisoformat(doc["read_at"])
    except ValueError:
        return {}
    if read.tzinfo is None or now - read > dt.timedelta(days=STALE_DAYS):
        return {}
    return {r["company_id"]: r["verified_since"] for r in doc.get("employers") or []}


def main(argv: list | None = None, get=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("SLEDJOBS_FEED_TOKEN", "").strip()
    on_file = json.loads(OUT.read_text()) if OUT.exists() else None
    kept = len((on_file or {}).get("employers") or [])
    if not token:
        print(f"SLED HQ employers feed not configured (no SLEDJOBS_FEED_TOKEN); "
              f"{kept} verified employer(s) on file kept")
        return 0
    status, body, note = hq_jobs.read_feed(token, get, url=FEED, key="employers")
    if body is None:
        print(f"SLED HQ employers feed {note}; {kept} verified employer(s) on file kept "
              f"- an error is never 'nobody is verified'")
        return 0
    companies = {c["id"]: c for c in json.loads((DATA / "companies.json").read_text())}
    # HQ's dates are UTC dates: compared with this machine's local date, a
    # recruiter verified after 8pm New York time read as "in the future"
    took, refused = sort_feed(body, companies, dt.datetime.now(dt.timezone.utc).date())
    print(f"SLED HQ employers feed answered 200: {len(took)} verified, {len(refused)} refused")
    for r in refused[:20]:
        print(f"  refused {r['company_id']}: {r['why']}")
    if a.write:
        OUT.write_text(json.dumps({
            "note": "Companies with a verified recruiter on SLED HQ, as its feed sent them, "
                    "less the refused. Written by scripts/hq_employers.py; read by build_board.",
            "read_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "generated_at": body.get("generated_at"),
            "employers": took, "refused": refused}, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
