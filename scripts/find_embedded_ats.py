#!/usr/bin/env python3
"""Find the real job board hiding inside a careers page nothing can enumerate.

THE IDEA, AND WHY THE LAST PASS MISSED IT

888 companies have a careers page a person can read and no fetcher can. The
standing explanation was that they are third-party widgets in iframes and
JS-only lists, and that no fetcher we write will read them. That is true of
the LIST. It is not true of the PAGE.

A page that renders its jobs through a widget still has to name the widget.
Circuit's careers page - "page scan found no listings", filed page-only,
contributing nothing for months - contains this, three times, in plain HTML:

  recruiting.paylocity.com/recruiting/jobs/All/8fd19852-.../TFR-Transit-Inc

That is a real, fetchable, structured board. Nobody had to render anything;
the reference was sitting in the source the whole time. Discovery went looking
for a board AT a URL and never looked for a board MENTIONED BY one.

WHY THIS PROPOSES AND NEVER WRITES

Note what Circuit's board calls itself: TFR Transit Inc. Probably Circuit's
operating entity, and "probably" is exactly how a company ends up pointed at
somebody else's job board - which this project has now done once, with
Concourse, and put seven of a corporate-finance company's postings on a
govtech board.

So every hit here is a PROPOSAL with its evidence attached: what was found,
where in the page, what the board calls itself, and how many postings it
returns. A person rules. The asymmetric error rule cuts the same way it always
does - a missed board costs a re-run, a wrong board publishes another
company's jobs under a name that is not theirs.

  python scripts/find_embedded_ats.py [--limit 200] [--all] [--include-unknown]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ats            # noqa: E402
import add_company    # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "embedded_ats.json"

# Each entry: how the ATS appears in a page, and how to lift the reference out.
# Ordered so the most specific pattern wins - a greenhouse job link and a
# greenhouse embed script look different and only one carries the slug.
SIGS: list[tuple[str, str, str]] = [
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]{2,40})", "slug"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]{2,40})", "slug"),
    ("lever",      r"jobs\.lever\.co/([a-z0-9_-]{2,40})", "slug"),
    ("lever",      r"api\.lever\.co/v0/postings/([a-z0-9_-]{2,40})", "slug"),
    ("ashby",      r"jobs\.ashbyhq\.com/([a-z0-9_.-]{2,40})", "slug"),
    ("ashby",      r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9_.-]{2,40})", "slug"),
    ("workable",   r"apply\.workable\.com/(?:api/v1/widget/accounts/)?([a-z0-9_-]{2,40})", "slug"),
    ("recruitee",  r"([a-z0-9-]{2,40})\.recruitee\.com", "slug"),
    ("breezy",     r"([a-z0-9-]{2,40})\.breezy\.hr", "slug"),
    ("bamboohr",   r"([a-z0-9-]{2,40})\.bamboohr\.com/(?:jobs|careers)", "slug"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]{2,40})", "slug"),
    ("jazzhr",     r"([a-z0-9-]{2,40})\.applytojob\.com", "slug"),
    # paylocity stores the WHOLE board url as its ref, because the fetcher
    # GETs it directly - there is no JSON API to build a url from a slug
    ("paylocity",  r"(https://recruiting\.paylocity\.com/recruiting/jobs/All/[0-9a-f-]{36}/[A-Za-z0-9_-]+)", "url"),
    ("rippling",   r"ats\.rippling\.com/([a-z0-9-]{2,60})/jobs", "slug"),
    ("workday",    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([A-Za-z0-9_-]+)", "workday"),
    ("icims",      r"([a-z0-9-]{2,40})\.icims\.com", "slug"),
]

# Slugs that belong to the ATS vendor rather than to any customer. Matching one
# means the page linked to the vendor's own site, not to a board.
JUNK = {"www", "app", "api", "help", "docs", "support", "about", "careers",
        "jobs", "embed", "static", "assets", "cdn", "js", "css", "img",
        "greenhouse", "lever", "ashby", "workable", "recruitee", "breezy",
        "bamboohr", "smartrecruiters", "paylocity", "rippling", "icims"}


def careers_urls(c: dict) -> list[str]:
    """Where a careers page might be, most likely first."""
    out = []
    ref = (c.get("ats") or {}).get("ref")
    if isinstance(ref, str) and ref.startswith("http"):
        out.append(ref)
    site = (c.get("website") or "").rstrip("/")
    if site:
        out += [f"{site}/careers", f"{site}/jobs", site]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:3]


def scan(html: str) -> list[dict]:
    """Every ATS reference the page mentions, best first."""
    found, seen = [], set()
    for kind, pat, shape in SIGS:
        for m in re.finditer(pat, html or "", re.I):
            if shape == "workday":
                tenant, wd, site = m.group(1), m.group(2), m.group(3)
                ref = [tenant, site, wd]
                key = (kind, tenant, site)
            else:
                g = m.group(1)
                if g.lower() in JUNK:
                    continue
                ref = g
                key = (kind, g.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append({"type": kind, "ref": ref,
                          "saw": m.group(0)[:120],
                          # how many times the page mentions it: a board linked
                          # once in a footer is weaker than one embedded thrice
                          "times": len(re.findall(re.escape(m.group(0)), html))})
    found.sort(key=lambda f: -f["times"])
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-unknown", action="store_true",
                    help="also sweep the companies with no board on file")
    ap.add_argument("--pause", type=float, default=0.3)
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    kinds = {"html"} | ({"unknown"} if a.include_unknown else set())
    todo = [c for c in companies
            if (c.get("ats") or {}).get("type") in kinds and c.get("website")]
    if not a.all:
        todo = todo[:a.limit]

    print(f"scanning {len(todo)} careers pages for an embedded board\n", flush=True)
    hits, checked = [], 0
    for i, c in enumerate(todo, 1):
        html = ""
        for url in careers_urls(c):
            try:
                r = add_company.fetch(url)
                html = r[0] if isinstance(r, tuple) else r
                if html and len(html) > 400:
                    break
            except Exception:
                continue
        checked += 1
        for f in scan(html)[:2]:
            # a reference is a claim; verify it returns real postings before
            # it is worth anybody's attention
            block = {"type": f["type"], "ref": f["ref"]}
            try:
                ok, detail = add_company.verify(block)
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}"
            if ok:
                row = {"id": c["id"], "name": c["name"], "was": c["ats"]["type"],
                       "found": block, "saw": f["saw"], "times": f["times"],
                       "verified": detail}
                hits.append(row)
                print(f"  {c['name'][:28]:30} {f['type']:14} {detail[:40]}", flush=True)
                break
        if i % 50 == 0:
            print(f"  ... {i}/{len(todo)}, {len(hits)} found", flush=True)
        time.sleep(a.pause)

    OUT.write_text(json.dumps(hits, indent=1) + "\n")
    print(f"\n{len(hits)} verified boards found in {checked} pages")
    print(f"written to {OUT.relative_to(ROOT)}")
    print("\nNOT written to companies.json. Every one needs a person to confirm "
          "the board belongs to this company - Circuit's says 'TFR Transit Inc', "
          "which is probably its operating entity and is exactly the shape of "
          "the mistake that put a finance company's jobs on this board.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
