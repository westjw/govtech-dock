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
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "discovery_log.json"
SUSPECTS = DATA / "ats_suspects.json"

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
    raw = company.get("name", "")
    name = _norm(raw)
    host = _norm((company.get("website") or "").split("//")[-1].split("/")[0]
                 .replace("www.", "").split(".")[0])
    # A parenthetical usually names the acquirer: "Simpleview (Granicus)" posts on
    # Granicus's board, and rejecting that loses a real, correct find.
    paren = [_norm(x) for x in re.findall(r"\(([^)]{2,40})\)", raw)]
    first = _norm(re.split(r"[^A-Za-z0-9]+", raw)[0]) if raw else ""

    for other in [name, host, *paren]:
        if not other:
            continue
        if sl in other or other in sl:
            return True
        if len(sl) >= 5 and len(other) >= 5 and sl[:5] == other[:5]:
            return True
    # A slug built from the company's first word plus a suffix is theirs:
    # "MCM Technology LLC" posts at rippling:mcmjobs.
    if len(first) >= 3 and sl.startswith(first):
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


# Discovery probes do not need the fetcher's patience. At 20 seconds a request
# and up to eight paths per company, one dead domain costs 160 seconds of worker
# time, which is what turned a 1,245-company sweep into a multi-hour run.
PROBE_TIMEOUT = 6


def get(url: str) -> str:
    import requests
    try:
        r = requests.get(url, headers=ats.UA, timeout=PROBE_TIMEOUT,
                         allow_redirects=True)
    except Exception:
        return ""
    return r.text if r.status_code == 200 else ""


# A hard ceiling per company. requests' timeout governs each socket operation,
# not the whole request, so a server trickling bytes can hold a worker open
# indefinitely: a 1,245-company sweep ran 10.5 hours, past the point where every
# fetch timing out would have finished, and wrote nothing. No company is worth
# more than this.
COMPANY_BUDGET = 25


def probe(company: dict) -> dict:
    """Look for an ATS on a company's site. Returns a result record."""
    site = (company.get("website") or "").rstrip("/")
    if not site:
        return {"id": company["id"], "found": None, "note": "no website on file"}
    started = time.monotonic()
    for path in [""] + CAREER_PATHS:
        if time.monotonic() - started > COMPANY_BUDGET:
            return {"id": company["id"], "found": None,
                    "note": f"gave up after {COMPANY_BUDGET}s"}
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
                saved, ats.TIMEOUT = ats.TIMEOUT, 10
                try:
                    jobs = ats.fetch(block)
                finally:
                    ats.TIMEOUT = saved
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
    ap.add_argument("--review", action="store_true",
                    help="list boards that read fine but whose slug did not match")
    ap.add_argument("--confirm", metavar="COMPANY_ID",
                    help="accept a reviewed board for this company")
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    log = load_log()

    if a.confirm:
        sus = json.loads(SUSPECTS.read_text()) if SUSPECTS.exists() else {}
        entry = sus.get(a.confirm)
        if not entry:
            print(f"no reviewable board for {a.confirm!r}", file=sys.stderr)
            return 1
        by_id = {c["id"]: c for c in companies}
        if a.confirm not in by_id:
            print(f"no company {a.confirm!r}", file=sys.stderr)
            return 1
        by_id[a.confirm]["ats"] = entry["ats"]
        (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")
        sus.pop(a.confirm)
        SUSPECTS.write_text(json.dumps(sus, indent=1) + "\n")
        print(f"{by_id[a.confirm]['name']} now monitored via "
              f"{entry['ats']['type']}:{entry['ats']['ref']}")
        return 0

    if a.review:
        sus = json.loads(SUSPECTS.read_text()) if SUSPECTS.exists() else {}
        if not sus:
            print("nothing awaiting review")
            return 0
        by_id = {c["id"]: c for c in companies}
        print(f"{len(sus)} board(s) read fine but the slug did not match the company.")
        print("These are usually acquisitions. Confirm the ones that are real:\n")
        for cid, e in sus.items():
            name = by_id.get(cid, {}).get("name", cid)
            print(f"  {name}")
            print(f"    board:   {e['ats']['type']}:{e['ats']['ref']}")
            print(f"    accept:  python3 scripts/discover_ats.py --confirm {cid}")
        return 0

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

    print(f"probing {len(todo)} companies with {a.workers} workers...", flush=True)
    today = dt.date.today().isoformat()
    results = []
    by_id_all = {c["id"]: c for c in companies}

    def checkpoint():
        """Write what we have. Without this a long sweep that is interrupted, or
        that someone has to stop, loses every probe it completed."""
        for r in results:
            log[r["id"]] = {"on": today, "found": bool(r.get("found")),
                            "note": r["note"]}
            if a.write and r.get("found"):
                by_id_all[r["id"]]["ats"] = r["found"]
        LOG.write_text(json.dumps(log, indent=1) + "\n")
        if a.write:
            (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(probe, todo), 1):
            results.append(r)
            if i % 50 == 0:
                hits = sum(1 for x in results if x.get("found"))
                print(f"  {i}/{len(todo)}, {hits} board(s) found so far", flush=True)
                checkpoint()
    checkpoint()

    # Persist the near-misses. A readable board whose slug does not match is
    # usually an acquisition the name gives no hint of: Bonfire Interactive posts
    # on Euna's board because Euna bought them. No string comparison can know
    # that, and a person can confirm it in seconds, so these are kept for review
    # rather than guessed at in either direction.
    suspect = [r for r in results if r.get("suspect")]
    if suspect:
        prior = json.loads(SUSPECTS.read_text()) if SUSPECTS.exists() else {}
        for r in suspect:
            prior[r["id"]] = {"ats": r["suspect"], "note": r["note"], "on": today}
        SUSPECTS.write_text(json.dumps(prior, indent=1) + "\n")
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
