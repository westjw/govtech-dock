#!/usr/bin/env python3
"""Find the job board for companies that have none on file, in bulk.

4,121 of 4,499 companies have no ATS recorded, which means the map knows they
exist and cannot tell you whether they are hiring. This probes each one's site
for an applicant-tracking system, verifies the find with a real fetch, and
writes back only what actually works.

Three properties it needs to survive the scale:

RESUMABLE. Every attempt is logged with its date, so a rerun skips what was
tried recently instead of starting over. At this size the work happens across
many sessions, and forgetting what was already probed would make it endless.

VERIFIED. A discovered slug is fetched before it is written. An unverified guess
produces a company that looks monitored and is not, which is worse than one
still marked unknown, because nothing prompts anyone to look again.

POLITE. Concurrency is bounded and each host sees a handful of requests. The
point is to find a public job board, not to hammer anyone's marketing site.

  python scripts/discover_ats.py [--limit 300] [--workers 8] [--write]
  python scripts/discover_ats.py --stats
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "discovery_log.json"

# Retry a failed probe after this long. Companies add job boards; a permanent
# "nothing found" would freeze a company out of monitoring forever.
RETRY_DAYS = 45

CAREER_PATHS = ["/careers", "/careers/", "/jobs", "/company/careers",
                "/about/careers", "/join-us", "/careers/open-positions"]

# Ordered: a structured API beats a generic careers page.
MARKERS: list[tuple[str, str]] = [
    ("ashby", r"jobs\.ashbyhq\.com/(?:embed\?org=)?([a-zA-Z0-9._-]+)"),
    ("greenhouse", r"(?:job-boards|boards)\.greenhouse\.io/"
                   r"(?:embed/job_board(?:/js)?\?for=)?([a-zA-Z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([a-zA-Z0-9-]+)"),
    ("recruitee", r"([a-zA-Z0-9-]+)\.recruitee\.com"),
    ("breezy", r"([a-zA-Z0-9-]+)\.breezy\.hr"),
    ("bamboohr", r"([a-zA-Z0-9-]+)\.bamboohr\.com"),
    ("jazzhr", r"([a-zA-Z0-9-]+)\.applytojob\.com"),
    ("rippling", r"ats\.rippling\.com/([a-zA-Z0-9-]+)"),
    ("icims", r"([a-zA-Z0-9-]+)\.icims\.com"),
]
RESERVED = {"www", "jobs", "careers", "api", "embed", "app", "static", "cdn", "js"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def slug_matches(slug: str, company: dict) -> bool:
    """Does a discovered slug plausibly belong to this company?

    A marker on a page is not proof it is theirs: an embedded widget, an agency
    script or a partner logo can leave one behind. "Loop" resolved to
    bamboohr:boxclever, which would have monitored somebody else's board under
    Loop's name. A slug has to share ground with the company name or domain, or
    it gets flagged for review rather than written.
    """
    sl = _norm(slug)
    if not sl:
        return False
    name = _norm(company.get("name", ""))
    host = _norm((company.get("website") or "").split("//")[-1].split("/")[0]
                 .replace("www.", "").split(".")[0])
    for other in (name, host):
        if not other:
            continue
        if sl in other or other in sl:
            return True
        # a short shared prefix catches "escribe" vs "escribemeetings"
        if len(sl) >= 5 and len(other) >= 5 and sl[:5] == other[:5]:
            return True
    return False

_lock = threading.Lock()


def load_log() -> dict:
    return json.loads(LOG.read_text()) if LOG.exists() else {}


def stale(entry: dict | None) -> bool:
    if not entry:
        return True
    try:
        age = (dt.date.today() - dt.date.fromisoformat(entry["on"])).days
    except (KeyError, ValueError):
        return True
    return age >= RETRY_DAYS


def get(url: str) -> str:
    try:
        return ats._get(url).text
    except Exception:
        return ""


def probe(company: dict) -> dict:
    """Look for an ATS on a company's site. Returns a result record."""
    site = (company.get("website") or "").rstrip("/")
    if not site:
        return {"id": company["id"], "found": None, "note": "no website on file"}
    for path in [""] + CAREER_PATHS:
        html = get(site + path)
        if not html:
            continue
        for kind, pat in MARKERS:
            m = re.search(pat, html, re.I)
            if not m:
                continue
            slug = next((g for g in m.groups() if g), "")
            if not slug or slug.lower() in RESERVED:
                continue
            block = {"type": kind, "ref": slug}
            try:
                jobs = ats.fetch(block)
            except Exception as exc:
                return {"id": company["id"], "found": None,
                        "note": f"{kind}:{slug} found but unreadable ({str(exc)[:40]})"}
            real = [j for j in jobs if (j.get("title") or "").strip()]
            if not slug_matches(slug, company):
                return {"id": company["id"], "found": None,
                        "suspect": block,
                        "note": f"{kind}:{slug} reads, but the slug does not match "
                                f"{company['name']!r}; likely someone else's board"}
            return {"id": company["id"], "found": block,
                    "note": f"{kind}:{slug}, {len(real)} posting(s) readable",
                    "postings": len(real)}
        # A careers page with no ATS marker is still better than nothing: the
        # html path can sometimes enumerate it, and a person can always read it.
        if path and re.search(r"\b(open positions|current openings|join our team|"
                              r"we're hiring|we are hiring|view (all )?jobs|"
                              r"apply now)\b", html, re.I):
            return {"id": company["id"], "found": {"type": "html", "ref": site + path},
                    "note": f"careers page at {path}, no ATS marker", "postings": 0}
    return {"id": company["id"], "found": None, "note": "no board found"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    log = load_log()

    if a.stats:
        t = collections.Counter((c.get("ats") or {}).get("type") for c in companies)
        probed = len(log)
        found = sum(1 for v in log.values() if v.get("found"))
        pending = [c for c in companies
                   if (c.get("ats") or {}).get("type") in (None, "unknown")
                   and c.get("website") and stale(log.get(c["id"]))]
        print(f"{len(companies)} companies")
        print(f"  {sum(n for k, n in t.items() if k not in (None, 'unknown'))} have a board")
        print(f"  {t.get('unknown', 0) + t.get(None, 0)} do not")
        print(f"  {probed} probed so far, {found} of those produced a working board")
        print(f"  {len(pending)} ready to probe now")
        return 0

    todo = [c for c in companies
            if (c.get("ats") or {}).get("type") in (None, "unknown")
            and c.get("website") and stale(log.get(c["id"]))]
    # Sector order is the buyer-motion order: the closest markets first, so a
    # partial run is still the most useful partial run.
    order = {"General Gov": 0, "Public Works": 1, "Parks & Rec": 2,
             "Public Safety": 3, "Transit & Parking": 4, "K-12 Schools": 5,
             "Utilities & Energy": 6, "Airports & Aviation": 7}
    todo.sort(key=lambda c: order.get(c.get("sector"), 9))
    todo = todo[:a.limit]
    if not todo:
        print("nothing to probe. every company either has a board or was tried recently.")
        return 0

    print(f"probing {len(todo)} companies with {a.workers} workers...")
    today = dt.date.today().isoformat()
    results = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(probe, todo), 1):
            results.append(r)
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}...")

    suspect = [r for r in results if r.get("suspect")]
    if suspect:
        print(f"\n{len(suspect)} slug(s) read fine but do not match the company, "
              "so they are not being written:")
        for r in suspect[:8]:
            print(f"   {r['id']:<28} {r['note'][:74]}")

    found = [r for r in results if r.get("found")]
    by_kind = collections.Counter(r["found"]["type"] for r in found)
    print(f"\n{len(found)} of {len(todo)} produced a board:")
    for k, n in by_kind.most_common():
        print(f"  {n:>4}  {k}")
    with_jobs = sum(1 for r in found if r.get("postings"))
    print(f"  {with_jobs} of those currently have readable postings")

    for r in results:
        log[r["id"]] = {"on": today, "found": bool(r.get("found")), "note": r["note"]}

    if not a.write:
        print("\ndry run. re-run with --write to record the discoveries.")
        for r in found[:12]:
            print(f"   {r['id']:<30} {r['note']}")
        return 0

    by_id = {c["id"]: c for c in companies}
    for r in found:
        by_id[r["id"]]["ats"] = r["found"]
    (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")
    LOG.write_text(json.dumps(log, indent=1) + "\n")
    fetchable = sum(1 for c in companies
                    if (c.get("ats") or {}).get("type") not in (None, "unknown"))
    print(f"\nwrote {len(found)} board(s). {fetchable} of {len(companies)} "
          f"companies are now monitored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
