#!/usr/bin/env python3
"""Propose a company entry from nothing but a URL.

Submit a link, and this works out the name, what they do, which sector and
category they belong in, and which applicant-tracking system fronts their
board, so the company card and the monitoring start themselves.

It PROPOSES rather than adds. Three reasons, and they are the whole design:

Sector and category are judgment, and a wrong one pollutes the market
intelligence for every company in that sector. A confident guess with its
evidence shown beats a silent assignment.

Once this is public, a submission is untrusted input. Anything that writes
straight to the dataset is a way for a stranger to put arbitrary text on the
board.

The ATS guess has to be verified by an actual fetch before monitoring can be
promised. A company added with a wrong slug looks monitored and is not, which is
worse than not being added.

  python scripts/add_company.py https://example.com [--write] [--json]

--write appends to data/companies.json after the checks pass. Without it, the
proposal is printed for review, which is what the public flow uses: an Action
runs this on an issue submission and opens a pull request.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CAREER_PATHS = ["/careers", "/careers/", "/company/careers", "/about/careers",
                "/jobs", "/careers/open-positions", "/about-us/careers", "/join-us"]

# ATS fingerprints, matched against page HTML. Ordered so a structured API wins
# over a generic careers URL.
ATS_MARKERS = [
    ("ashby", r"jobs\.ashbyhq\.com/(?:embed\?org=)?([a-z0-9._-]+)"),
    ("greenhouse", r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?"
                   r"([a-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-z0-9_-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([a-z0-9-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("breezy", r"([a-z0-9-]+)\.breezy\.hr"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
    ("jazzhr", r"([a-z0-9-]+)\.applytojob\.com"),
    ("rippling", r"ats\.rippling\.com/([a-z0-9-]+)"),
    ("icims", r"([a-z0-9-]+)\.icims\.com"),
]

# Sector and category suggestions. First match wins, so order encodes priority.
SECTOR_HINTS: list[tuple[str, str, str]] = [
    ("Public Safety", "Police", r"\b(police|law enforcement|patrol|body.?cam|evidence|"
                                r"gunshot|dispatch|911|cad\b|first responder)\b"),
    ("Public Safety", "Fire", r"\b(fire|wildfire|wildland|ems\b|paramedic|ambulance)\b"),
    ("Public Works", "Waste & Recycling", r"\b(waste|recycl|refuse|garbage|sanitation|"
                                          r"hauler|landfill)\b"),
    ("Public Works", "Water", r"\b(water|sewer|wastewater|stormwater|hydrant|leak)\b"),
    ("Public Works", "Streets", r"\b(street|road|pavement|pothole|sidewalk|snow|"
                                r"right of way)\b"),
    ("Public Works", "Fleet & Asset Mgmt", r"\b(fleet|asset management|maintenance "
                                           r"management|public works)\b"),
    ("Transit & Parking", "Parking & Curb", r"\b(parking|curb|meter|enforcement)\b"),
    ("Transit & Parking", "Rider Experience", r"\b(transit|bus|paratransit|rail|mobility|"
                                          r"microtransit)\b"),
    ("K-12 Schools", "School Safety", r"\b(school safety|student safety|campus)\b"),
    ("K-12 Schools", "Transportation", r"\b(school bus|student transport)\b"),
    ("Parks & Rec", "Recreation Management", r"\b(parks|recreation|rec department|"
                                      r"facility booking|camp registration)\b"),
    ("General Gov", "Permitting & Licensing", r"\b(permit|licens|inspection|"
                                              r"code enforcement|zoning|land use)\b"),
    ("General Gov", "Procurement & Payments", r"\b(procure|bid|rfp|sourcing|contract management)\b"),
    ("General Gov", "Citizen Services", r"\b(citizen|resident|constituent|311|"
                                        r"agenda|meeting management|civic)\b"),
    ("General Gov", "Finance & ERP", r"\b(budget|finance|payments|billing|revenue|"
                                        r"utility billing|tax)\b"),
    ("General Gov", "HR & Workforce", r"\b(workforce|human resources|hiring|payroll)\b"),
]

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{20,300})',
                  re.I)
OG_DESC = re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']'
                     r'([^"\']{20,300})', re.I)
OG_SITE = re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']'
                     r'([^"\']{2,60})', re.I)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(s or "")).strip()


def fetch(url: str) -> tuple[str, str]:
    """Return (html, note). Discovery is not monitoring: a WAF that answers 403
    while still sending the whole page is readable for identification purposes,
    and discarding it on status alone loses real companies. zencity.io does
    exactly this. A short body is a block page and stays discarded."""
    import requests
    try:
        r = requests.get(url, headers=ats.UA, timeout=ats.TIMEOUT,
                         allow_redirects=True)
    except Exception as exc:
        return "", f"unreachable: {type(exc).__name__}"
    if r.status_code == 200:
        return r.text, ""
    # A block page can be large: zencity.io answers 403 with 75KB whose title is
    # just "403". Size alone is not evidence the real page came through, so check
    # that it looks like a company site rather than a refusal.
    if len(r.text) > 4000 and not _is_block_page(r.text):
        return r.text, f"HTTP {r.status_code} but the real page was served anyway"
    return "", f"HTTP {r.status_code} (blocked)"


_BLOCK_TITLE = re.compile(r"<title[^>]*>\s*(\d{3}|[^<]{0,60}?(?:forbidden|access denied|"
                          r"blocked|attention required|just a moment|are you a robot|"
                          r"security check)[^<]{0,40})\s*</title>", re.I | re.S)


def _is_block_page(html: str) -> bool:
    if _BLOCK_TITLE.search(html):
        return True
    # A real company homepage carries description metadata; a refusal does not.
    return not (DESC.search(html) or OG_DESC.search(html) or OG_SITE.search(html))


def guess_identity(home: str, url: str) -> tuple[str, str]:
    """Company name and one-line description from the homepage metadata."""
    name = ""
    m = OG_SITE.search(home)
    if m:
        name = clean(m.group(1))
    if not name:
        m = TITLE.search(home)
        if m:
            # "Acme | Permitting software for cities" -> "Acme"
            name = re.split(r"\s*[|\-–—:]\s*", clean(m.group(1)))[0].strip()
    if not name:
        name = urllib.parse.urlparse(url).netloc.replace("www.", "").split(".")[0].title()
    desc = ""
    for pat in (DESC, OG_DESC):
        m = pat.search(home)
        if m:
            desc = clean(m.group(1))
            break
    return name[:60], desc[:220]


def guess_sector(blob: str) -> tuple[str | None, str | None, str, list[str]]:
    """Sector, category, confidence, and the evidence.

    Scored by how often each rule's vocabulary appears, not by which rule happens
    to sit first in the list. One incidental word should not decide a sector:
    "revenue" alone put a strategy company in Budget & Finance, which then
    distorts the market intelligence for everyone else in that sector.
    """
    scored = []
    for sector, category, pat in SECTOR_HINTS:
        found = re.findall(pat, blob, re.I)
        if found:
            terms = {(f if isinstance(f, str) else f[0]).lower() for f in found}
            scored.append((len(found), len(terms), sector, category, sorted(terms)[:4]))
    if not scored:
        return None, None, "none", []
    scored.sort(reverse=True)
    n, distinct, sector, category, terms = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0
    # A single incidental term is always low confidence, whether or not anything
    # else matched. The old formula compared against a runner-up of zero, so one
    # hit on "revenue" reported medium.
    confidence = ("high" if distinct >= 3 and n >= max(runner * 2, 3) else
                  "medium" if distinct >= 2 else "low")
    evidence = [f"{s} / {c}: {cnt} hit(s) on {t}" for cnt, _, s, c, t in scored[:4]]
    return sector, category, confidence, evidence


def find_ats(url: str, paths: list[str] | None = None
             ) -> tuple[dict | None, str | None, list[str]]:
    """Probe the site for an ATS. Returns (ats, careers_url, notes).

    paths narrows the probe. The admin's retry button passes just the root
    and /careers, because the admin server is single-threaded and a full
    eight-path probe of a slow host freezes every other request while it
    runs. Bulk discovery keeps the full list.
    """
    base = url.rstrip("/")
    notes = []
    for path in [""] + (CAREER_PATHS if paths is None else paths):
        page_url = base + path
        html, _ = fetch(page_url)
        if not html:
            continue
        for kind, pat in ATS_MARKERS:
            m = re.search(pat, html, re.I)
            if m:
                slug = next((g for g in m.groups() if g), "")
                if slug and slug not in ("www", "jobs", "careers", "api", "embed"):
                    notes.append(f"found {kind} marker on {page_url}")
                    return {"type": kind, "ref": slug}, page_url, notes
        if path and re.search(r"\b(open positions|join our team|current openings|"
                              r"we are hiring|view (all )?jobs)\b", html, re.I):
            notes.append(f"careers page found at {page_url}, no ATS marker")
            return {"type": "html", "ref": page_url}, page_url, notes
    notes.append("no careers page or ATS marker found")
    return None, None, notes


def verify(ats_block: dict) -> tuple[bool, str]:
    """Actually fetch the board. An unverified slug looks monitored and is not."""
    try:
        jobs = ats.fetch(ats_block)
    except Exception as exc:
        return False, str(exc)[:70]
    real = [j for j in jobs if (j.get("title") or "").strip()]
    return True, f"{len(real)} posting(s) readable"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    url = a.url if a.url.startswith("http") else "https://" + a.url
    companies = json.loads((DATA / "companies.json").read_text())
    suppliers_path = DATA / "suppliers.json"
    suppliers = json.loads(suppliers_path.read_text()) if suppliers_path.exists() else []
    host = urllib.parse.urlparse(url).netloc.replace("www.", "")
    dupe = next((c for c in companies + suppliers
                 if host and host in (c.get("website") or "")), None)
    if dupe:
        print(f"already tracked: {dupe['name']} ({dupe['id']})")
        return 1

    home, fetch_note = fetch(url)
    if not home:
        print(f"could not read {url}: {fetch_note}", file=sys.stderr)
        return 1
    name, desc = guess_identity(home, url)
    ats_block, careers, notes = find_ats(url)
    if fetch_note:
        notes.insert(0, fetch_note)
    sector, category, confidence, why = guess_sector(f"{name} {desc} {home[:20000]}")

    verified, detail = (False, "no ATS found")
    if ats_block:
        verified, detail = verify(ats_block)

    entry = {
        "id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
        "name": name, "website": url, "location": None, "year_founded": None,
        "sector": sector, "category": category, "description": desc,
        "ats": ats_block or {"type": "unknown", "ref": None},
        "hiring": {"status": "Unknown", "note": "just added", "roles": [],
                   "checked": None},
    }

    if a.json:
        print(json.dumps({"entry": entry, "verified": verified, "detail": detail,
                          "sector_confidence": confidence, "sector_evidence": why,
                          "ats_notes": notes}, indent=1))
        return 0

    print(f"PROPOSED: {name}")
    print(f"  {url}")
    print(f"  {desc or '(no description found)'}")
    print()
    print(f"  sector    {sector or 'UNKNOWN'} / {category or 'UNKNOWN'}"
          f"   confidence: {confidence}")
    for w in why:
        print(f"            {w}")
    if not why:
        print("            nothing matched, so this needs a human to categorise")
    print(f"  ats       {ats_block or 'none found'}")
    for n in notes:
        print(f"            {n}")
    print(f"  board     {'VERIFIED, ' + detail if verified else 'NOT VERIFIED: ' + detail}")
    print()

    blockers = []
    if not sector:
        blockers.append("no sector could be inferred")
    elif confidence == "low":
        blockers.append(f"the sector guess rests on one incidental term, so "
                        f"{sector} / {category} needs confirming")
    if not verified:
        blockers.append("the board could not be read, so monitoring cannot be promised")
    if blockers:
        print("needs a human before it can be added:")
        for b in blockers:
            print(f"  - {b}")
    else:
        print("ready to add. Monitoring starts on the next daily build.")

    if a.write:
        if blockers:
            print("\nrefusing to write while blockers remain.", file=sys.stderr)
            return 1
        companies.append(entry)
        (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")
        print(f"\nappended to companies.json ({len(companies)} companies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
