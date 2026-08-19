#!/usr/bin/env python3
"""Find a website for companies that have only a name.

2,957 companies arrived from conference exhibitor lists with a name, a sector
and nothing to visit, which means they cannot be probed for a job board and can
never be monitored. A website is the key that unlocks everything downstream.

There is no search API here, so this guesses domains from the name and then
proves each guess. Proof is the whole point: a live page is not evidence, since
parked domains, squatters and unrelated businesses all answer on the obvious
name. A candidate is accepted only when the page itself identifies the company.

Priority is govtech products first. A SLED job board cares about the 240 software
companies far more than the 1,785 equipment vendors and distributors, so a
partial run should still be the most useful partial run.

  python scripts/find_websites.py [--limit 200] [--govtech-only] [--write]
  python scripts/find_websites.py --stats
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import html as html_lib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "website_log.json"
RETRY_DAYS = 60

TLDS = (".com", ".io", ".net", ".co", ".us", ".ai", ".org")
STOP = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "the",
        "and", "group", "holdings", "technologies", "technology", "solutions",
        "systems", "services", "software", "usa", "us", "international"}

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
META = re.compile(r'<meta[^>]+(?:name|property)=["\'](?:description|og:site_name|'
                  r'og:title)["\'][^>]+content=["\']([^"\']{3,300})', re.I)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
PARKED = re.compile(r"domain (is )?for sale|buy this domain|parked (free )?(at|by)|"
                    r"godaddy|namecheap|sedo|hugedomains|this domain may be for sale|"
                    r"under construction|coming soon", re.I)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Legal suffixes carry no identity and are always dropped. The rest of STOP is
# only dropped when enough remains to still identify the company.
LEGAL = {"inc", "llc", "ltd", "corp", "corporation", "co", "the"}


def tokens(name: str) -> list[str]:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", (name or "").lower())
             if w and len(w) > 1 and w not in LEGAL]
    trimmed = [w for w in words if w not in STOP]
    # Dropping generic words can erase the whole name: "A-Frame Solutions" became
    # "frame" and matched frame.com, an unrelated company. Keep them when too
    # little is left to identify anyone.
    return trimmed if len(trimmed) >= 2 else words


def candidates(name: str) -> list[str]:
    """Domain guesses, most likely first."""
    tk = tokens(name)
    if not tk:
        return []
    joined = "".join(tk)
    hyphen = "-".join(tk)
    first = tk[0]
    bases = [joined]
    if hyphen != joined:
        bases.append(hyphen)
    if len(tk) > 1 and len(first) > 3:
        bases.append(first)
    bases.append("get" + joined)
    out = []
    for b in bases[:4]:
        if not (2 < len(b) < 40):
            continue
        for tld in TLDS[:4 if b == joined else 2]:
            out.append(f"https://{b}{tld}")
    return out[:10]


def identifies(html: str, name: str) -> bool:
    """Does this page actually claim to be this company?

    A live page proves a domain resolves, nothing more. Parked pages, squatters
    and unrelated businesses all answer on the obvious name, so acceptance
    requires the company's own words in the page's own identity fields.
    """
    if not html or PARKED.search(html[:4000]):
        return False
    ident = " ".join(
        html_lib.unescape(re.sub(r"<[^>]+>", " ", m))
        for pat in (TITLE, META, H1)
        for m in pat.findall(html)[:3])
    ident_n = norm(ident)
    if not ident_n:
        return False
    tk = tokens(name)
    if not tk:
        return False
    full = norm(name)
    if len(full) >= 6 and full in ident_n:
        return True
    # every distinctive word present, which catches "Orange Data" on a page
    # titled "Orange Data | Permit resolution for cities"
    strong = [t for t in tk if len(t) > 3]
    if not strong:
        return False
    if not all(norm(t) in ident_n for t in strong):
        return False
    # One short generic word is not an identification. "Frame" appearing on
    # frame.com does not make it A-Frame Solutions.
    if not (len(strong) > 1 or len(strong[0]) >= 6):
        return False
    # A parent-company page is not the subsidiary's site. "Amazon Web Services"
    # matched aboutamazon.eu on the word "amazon" alone, which is the wrong site
    # to send anyone to for AWS jobs. When the name has several distinctive
    # words, most of them have to appear, not just the strongest one.
    if len(tk) >= 2:
        present = sum(1 for t in tk if norm(t) in ident_n)
        if present < max(2, len(tk) - 1):
            return False
    return True


def probe(company: dict) -> dict:
    name = company.get("name", "")
    for url in candidates(name):
        try:
            r = ats._get(url)
        except Exception:
            continue
        if identifies(r.text, name):
            return {"id": company["id"], "url": str(r.url).rstrip("/"),
                    "note": "page identifies the company"}
    return {"id": company["id"], "url": None, "note": "no candidate domain identified it"}


def load_log() -> dict:
    return json.loads(LOG.read_text()) if LOG.exists() else {}


def stale(e: dict | None) -> bool:
    if not e:
        return True
    try:
        return (dt.date.today() - dt.date.fromisoformat(e["on"])).days >= RETRY_DAYS
    except (KeyError, ValueError):
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--govtech-only", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    log = load_log()
    missing = [c for c in companies if not c.get("website")]

    if a.stats:
        found = sum(1 for v in log.values() if v.get("url"))
        print(f"{len(companies)} companies, {len(missing)} without a website")
        by = collections.Counter(c.get("vendor_type") for c in missing)
        for k, n in by.most_common():
            print(f"  {n:>5}  {k}")
        print(f"\n{len(log)} probed, {found} produced a verified site")
        return 0

    todo = [c for c in missing if stale(log.get(c["id"]))]
    if a.govtech_only:
        todo = [c for c in todo if c.get("govtech")]
    # GovTech products first: a partial run should be the most useful one.
    todo.sort(key=lambda c: (0 if c.get("govtech") else 1,
                             0 if c.get("vendor_type") == "GovTech Product" else 1))
    todo = todo[:a.limit]
    if not todo:
        print("nothing to probe")
        return 0

    print(f"probing {len(todo)} companies for a website...")
    results = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(probe, todo), 1):
            results.append(r)
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}...")

    hits = [r for r in results if r["url"]]
    print(f"\n{len(hits)} of {len(todo)} identified ({len(hits) * 100 // len(todo)}%)")
    for r in hits[:12]:
        print(f"   {r['id']:<30} {r['url']}")

    today = dt.date.today().isoformat()
    for r in results:
        log[r["id"]] = {"on": today, "url": r["url"], "note": r["note"]}

    if not a.write:
        print("\ndry run. re-run with --write to record them.")
        return 0
    by_id = {c["id"]: c for c in companies}
    for r in hits:
        by_id[r["id"]]["website"] = r["url"]
    (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")
    LOG.write_text(json.dumps(log, indent=1) + "\n")
    left = sum(1 for c in companies if not c.get("website"))
    print(f"\nwrote {len(hits)} website(s). {left} companies still have none.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
