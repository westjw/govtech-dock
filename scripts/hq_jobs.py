#!/usr/bin/env python3
"""Read the jobs employers post on SLED HQ, for the board to list.

    python3 scripts/hq_jobs.py            # read the feed and report; nothing written
    python3 scripts/hq_jobs.py --write    # data/hq_jobs.json

THE RECRUITER SIDE LIVES IN SLED HQ (owner, 2026-10-01): a person claims a
company, posts jobs there, and the public board must list them. HQ never
writes into this repository - it publishes a feed and this reads it, the way
manual.json captures are read. The contract was agreed with the SLED HQ
session on 2026-10-04 and is restated where each rule is enforced.

WHAT AN ERROR MEANS. Any answer but 200 - 404 (a wrong or missing token, by
HQ's design), 503 (locked, misconfigured or withheld), 500, a timeout - keeps
the jobs already on file and says so. An error is never "no jobs": that is
the false "None found" this repository exists to refuse, aimed at the one
source that is ours. A 200 carries the WHOLE list, so a job missing from a
200 is closed or withdrawn.

WHAT IS REFUSED, one job at a time and never the whole feed, each refusal
kept with its reason so a person can see what HQ sent that the board would
not publish:
  - a company_id this board does not hold;
  - a company with no website on file (owner, 2026-10-04: nothing publishes
    without one - HQ enforces it too, and this does not take its word);
  - an apply url anywhere but https://hq.sledjobs.com/ (a poisoned feed must
    not be able to point the board's Apply buttons at another site);
  - a family, seniority, work mode, region or state outside roles.py's
    vocabulary - drift is loud, never quietly mapped to "other";
  - a closing date already past.

NOTHING PRIVATE IS KEPT AND NOTHING PRIVATE IS PRINTED. The repo and its
Actions logs are public: the token and the body never reach a log line, only
the status and the counts. The description is not stored at all - the board
carries no job-ad text from any source (build_board.derived) and the apply
page on HQ is where it lives. Without SLEDJOBS_FEED_TOKEN this does nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import brand  # noqa: E402
import roles  # noqa: E402

# HQ lives on a subdomain of the board's own domain, read from brand.json
# like every other place the domain appears (a rebrand is one edit).
FEED = f"https://hq.{brand.DOMAIN}/api/feed/jobs"
APPLY_PREFIX = f"https://hq.{brand.DOMAIN}/"
OUT = DATA / "hq_jobs.json"
SENIORITIES = ("junior", "mid", "senior", "leadership")
MODES = ("onsite", "hybrid", "remote", "not stated")
PERIODS = ("year", "month", "hour")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The fields the board uses. Anything else HQ sends - "ignore fields you
# don't know" is the contract - is dropped here, the description included.
KEEP = ("id", "company_id", "title", "family", "seniority", "quota_carrying",
        "work_mode", "office", "territory", "comp", "posted", "closes", "url",
        "updated_at")


def refuse_reason(job: dict, companies: dict, today: dt.date) -> str | None:
    """Why this job may not be listed, or None."""
    if not isinstance(job, dict):
        return "not an object"
    if not str(job.get("id") or "").startswith("hq::"):
        return "id is not hq::<public_id>"
    c = companies.get(job.get("company_id"))
    if not c:
        return f"company {job.get('company_id')!r} is not on this board"
    if not (c.get("website") or "").strip():
        return "the company has no website on file"
    if not (job.get("title") or "").strip():
        return "no title"
    url = job.get("url") or ""
    if not url.startswith(APPLY_PREFIX):
        return f"the apply url is not on {APPLY_PREFIX}"
    if job.get("family") not in roles.FAMILIES:
        return f"family {job.get('family')!r} is not in roles.py"
    if job.get("seniority") is not None and job["seniority"] not in SENIORITIES:
        return f"seniority {job.get('seniority')!r} is not in roles.py"
    if job.get("work_mode") not in MODES:
        return f"work mode {job.get('work_mode')!r} is not in roles.py"
    terr = job.get("territory") or {}
    if terr.get("region") is not None and terr["region"] not in roles.REGIONS:
        return f"region {terr.get('region')!r} is not in roles.py"
    bad = [s for s in terr.get("states") or [] if s not in roles.US_CODES]
    if bad:
        return f"state(s) {bad} are not USPS codes"
    office = job.get("office") or {}
    if office.get("state") and office["state"] not in roles.US_CODES:
        return f"office state {office.get('state')!r} is not a USPS code"
    comp = job.get("comp")
    if comp is not None:
        if not isinstance(comp, dict) or comp.get("period") not in PERIODS:
            return "comp is not {min, max, currency, period}"
        for k in ("min", "max"):
            if comp.get(k) is not None and not isinstance(comp[k], (int, float)):
                return f"comp {k} is not a number"
    for k in ("posted", "closes"):
        if job.get(k) is not None and not DATE.match(str(job[k])):
            return f"{k} is not YYYY-MM-DD"
    if not job.get("posted"):
        return "no posted date"
    if job.get("closes") and dt.date.fromisoformat(job["closes"]) < today:
        return "the closing date has passed"
    return None


def sort_feed(body: dict, companies: dict, today: dt.date) -> tuple[list, list]:
    """(accepted jobs trimmed to KEEP, refusals as {id, why})."""
    took, refused = [], []
    for job in body.get("jobs") or []:
        why = refuse_reason(job, companies, today)
        if why:
            refused.append({"id": (job or {}).get("id") if isinstance(job, dict) else None,
                            "why": why})
            continue
        took.append({k: job.get(k) for k in KEEP})
    took.sort(key=lambda j: j["id"])
    return took, refused


def read_feed(token: str, get=None) -> tuple[int | None, dict | None, str]:
    """(status, parsed body or None, a note safe to print)."""
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(FEED, headers={"Authorization": f"Bearer {token}",
                               "User-Agent": "SLED JOBS board (feed reader)"},
                timeout=30)
    except Exception as exc:                                # noqa: BLE001
        return None, None, f"no answer ({type(exc).__name__})"
    if r.status_code != 200:
        return r.status_code, None, f"answered {r.status_code}"
    try:
        body = r.json()
    except ValueError:
        return 200, None, "answered 200 with a body that is not JSON"
    if not isinstance(body, dict) or body.get("v") != 1 or not isinstance(body.get("jobs"), list):
        return 200, None, f"answered a feed of version {body.get('v') if isinstance(body, dict) else '?'!r}, not 1"
    return 200, body, "answered 200"


def main(argv: list | None = None, get=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("SLEDJOBS_FEED_TOKEN", "").strip()
    on_file = json.loads(OUT.read_text()) if OUT.exists() else None
    kept = len((on_file or {}).get("jobs") or [])
    if not token:
        print(f"SLED HQ feed not configured (no SLEDJOBS_FEED_TOKEN); "
              f"{kept} HQ job(s) on file kept")
        return 0
    status, body, note = read_feed(token, get)
    if body is None:
        print(f"SLED HQ feed {note}; {kept} HQ job(s) on file kept - an error is never 'no jobs'")
        return 0
    companies = {c["id"]: c for c in json.loads((DATA / "companies.json").read_text())}
    today = dt.date.today()
    took, refused = sort_feed(body, companies, today)
    print(f"SLED HQ feed answered 200: {len(took)} job(s) to list, {len(refused)} refused")
    for r in refused[:20]:
        print(f"  refused {r['id']}: {r['why']}")
    if a.write:
        OUT.write_text(json.dumps({
            "note": "Jobs employers posted on SLED HQ, as its feed sent them, less "
                    "the refused. Written by scripts/hq_jobs.py; read by build_board.",
            "read_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "generated_at": body.get("generated_at"),
            "jobs": took, "refused": refused}, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
