#!/usr/bin/env python3
"""Pull the pages a company writes about itself. Mechanical half only.

    python3 scripts/fetch_profiles.py                      # look only
    python3 scripts/fetch_profiles.py --write --limit 40
    python3 scripts/fetch_profiles.py --write --id verkada

WHY THIS EXISTS. Two engines need the same thing and neither should fetch its
own: the full company write-up the page is built for (what they sell, who buys
it, named customers) and the news timeline. Both are answers only a company's
own site can supply, and both are ruined by the same failure, which is a model
filling three paragraphs from what it happens to remember about a name.

SO THE TEXT IS STORED, AND THE DOOR CHECKS AGAINST IT. Every later claim about
a company has to appear in bytes we actually fetched, on a URL we can print.
That is not a style preference: 2,058 company pages are about to be public and
indexed, and a plausible sentence about a real firm's real customers, invented,
is a defamation-shaped problem rather than a typo.

STRICTLY MECHANICAL. This finds pages, fetches them, strips them to text and
stops. It does not decide what a company sells and it does not decide what
counts as news. `agents.py` briefs those judgments and a person rules on them.

WHAT IT REFUSES, each refusal recorded as a fact rather than an empty field:
a site that will not resolve, a page under 200 characters of text, and a
homepage with no link that looks like an about or a news page. `unread` is
never `none`: a company whose site we could not read has not been found to
have no customers, and the difference is the whole project.

PACED THROUGH ats._get, which already gates per host, honours 429 with a
per-host back-off, and caches with ETag and If-Modified-Since. Re-running this
over 2,018 sites costs almost nothing on the second pass, which is what makes
a nightly news read affordable.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as htmllib
import json
import pathlib
import re
import sys
import urllib.parse as up

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                   # noqa: E402
import ats                                                     # noqa: E402

STORE = DATA / "site_pages.json"

# WHAT WE ARE LOOKING FOR, and the two buckets are kept apart because the
# engines that read them ask different questions. `about` answers "what is
# this company"; `news` answers "what happened, and when".
ABOUT = re.compile(r"/(about|about-us|company|who-we-are|our-story|mission|"
                   r"platform|product|products|solutions|customers|clients|"
                   r"case-stud(y|ies)|why-us)(/|$|\?)", re.I)
NEWS = re.compile(r"/(news|newsroom|press|press-releases|media|announcements|"
                  r"blog|insights|resources/news|company/news)(/|$|\?)", re.I)

# Pages that look like the above and are not. A careers page mentions the
# company constantly and describes none of what it sells; a privacy policy
# is the single most about-shaped document on any site.
NOT_A_PAGE = re.compile(r"/(careers?|jobs?|privacy|terms|legal|cookie|login|"
                        r"signin|sign-in|support|contact|pricing|demo|"
                        r"request|subscribe|sitemap|search)(/|$|\?)", re.I)

MIN_TEXT = 200          # below this a "page" is a shell, a redirect or a wall
MAX_PAGES = 6           # per company, per bucket
MAX_TEXT = 24000        # per page, stored

TAG = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1>", re.I | re.S)
ANY = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\r\f\v]+")
BLANK = re.compile(r"\n\s*\n\s*\n+")
HREF = re.compile(r"""<a\b[^>]*?href\s*=\s*(["'])(.*?)\1""", re.I | re.S)


def text_of(body: str) -> str:
    """HTML to readable text. Block tags become newlines so a list of press
    items does not arrive as one run-on line that no date parser can split."""
    s = TAG.sub(" ", body or "")
    s = re.sub(r"</(p|div|li|tr|h[1-6]|section|article|header)>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = ANY.sub(" ", s)
    s = htmllib.unescape(s)
    s = WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return BLANK.sub("\n\n", s).strip()


def links(body: str, base: str) -> list[str]:
    """Absolute, same-registrable-domain links, in page order, deduped.

    SAME SITE ONLY. A company's press page routinely links out to the trade
    outlet that covered them, and following that would file a magazine's
    words as the company's own. The engines that read this store are told
    every URL is first-party; that has to be true here or nowhere.
    """
    host = (up.urlsplit(base).hostname or "").lower().removeprefix("www.")
    out, seen = [], set()
    for _, href in HREF.findall(body or ""):
        href = htmllib.unescape(href.strip()).split("#")[0]
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        u = up.urljoin(base, href)
        parts = up.urlsplit(u)
        if parts.scheme not in ("http", "https"):
            continue
        h = (parts.hostname or "").lower().removeprefix("www.")
        if not h or not (h == host or h.endswith("." + host) or host.endswith("." + h)):
            continue
        # DEDUPE ON THE PAGE, NOT THE SPELLING. www.brincdrones.com/about/
        # and brincdrones.com/about/ are one page, and the first pass fetched
        # both - a wasted request at somebody else's expense, and the same
        # words stored twice under two URLs, which would later read as two
        # independent sources for one claim.
        norm = (h, (parts.path or "/").rstrip("/").lower(), parts.query)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(u)
    return out


def pick(urls: list[str], want: re.Pattern) -> list[str]:
    return [u for u in urls
            if want.search(up.urlsplit(u).path or "/")
            and not NOT_A_PAGE.search(up.urlsplit(u).path or "/")][:MAX_PAGES]


def grab(url: str) -> dict:
    """One page, as {url, text, chars} or {url, unread: why}."""
    try:
        resp = ats._get(url)
    except Exception as exc:                      # ats raises its own type
        return {"url": url, "unread": str(exc)[:120]}
    body = getattr(resp, "text", "") or ""
    txt = text_of(body)
    if len(txt) < MIN_TEXT:
        return {"url": url, "unread": f"only {len(txt)} chars of text"}
    return {"url": url, "text": txt[:MAX_TEXT], "chars": len(txt)}


def visit(company: dict) -> dict:
    """Everything one company's own site will tell us, or why it would not."""
    site = (company.get("website") or "").strip()
    out = {"id": company["id"], "name": company.get("name"),
           "website": site or None,
           "fetched_on": dt.date.today().isoformat(),
           "about": [], "news": [], "unread": None}
    if not site:
        out["unread"] = "no website on file"
        return out

    home = grab(site)
    if home.get("unread"):
        # THE HOMEPAGE IS THE WHOLE SITE'S READABILITY. If it will not open,
        # nothing below it will, and guessing /about on a dead host just
        # spends somebody's server twelve more times.
        out["unread"] = f"homepage: {home['unread']}"
        return out
    out["about"].append(home)

    try:
        body = ats._get(site).text
    except Exception:
        body = ""
    found = links(body, site)
    def same_page(a: str, b: str) -> bool:
        pa, pb = up.urlsplit(a), up.urlsplit(b)
        return ((pa.hostname or "").lower().removeprefix("www.")
                == (pb.hostname or "").lower().removeprefix("www.")
                and (pa.path or "/").rstrip("/").lower()
                == (pb.path or "/").rstrip("/").lower())

    for u in pick(found, ABOUT):
        if same_page(u, site):
            continue
        out["about"].append(grab(u))
    for u in pick(found, NEWS):
        out["news"].append(grab(u))

    if not out["news"]:
        # A REAL FINDING, and the one the news engine must not paper over.
        # No news page is not no news, and the company page has to say the
        # first thing rather than the second.
        out["no_news_page"] = True
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--sector")
    ap.add_argument("--category")
    ap.add_argument("--refetch", action="store_true",
                    help="revisit companies already in the store")
    a = ap.parse_args()

    companies = admin.read_companies()
    if isinstance(companies, dict):
        companies = list(companies.values())
    store = json.loads(STORE.read_text()) if STORE.exists() else {}

    rows = [c for c in companies if c.get("id")]
    if a.id:
        rows = [c for c in rows if c["id"] in set(a.id)]
    if a.sector:
        rows = [c for c in rows if c.get("sector") == a.sector]
    if a.category:
        rows = [c for c in rows if c.get("category") == a.category]
    if not a.refetch:
        rows = [c for c in rows if c["id"] not in store]
    if a.limit:
        rows = rows[:a.limit]

    print(f"{len(rows)} company site(s) to read"
          f"{'' if a.write else ' -- LOOKING ONLY'}\n")
    if not a.write:
        for c in rows[:12]:
            print(f"  {c['id'][:28]:30} {(c.get('website') or '(no website)')[:52]}")
        if len(rows) > 12:
            print(f"  ...and {len(rows) - 12} more")
        print("\n  Re-run with --write to fetch them.")
        return 0

    got = {"about": 0, "news": 0, "unread": 0}
    for i, c in enumerate(rows, 1):
        rec = visit(c)
        store[c["id"]] = rec
        if rec.get("unread"):
            got["unread"] += 1
        got["about"] += sum(1 for p in rec["about"] if p.get("text"))
        got["news"] += sum(1 for p in rec["news"] if p.get("text"))
        if i % 25 == 0:
            print(f"  ... {i}/{len(rows)}")
            STORE.write_text(json.dumps(store, indent=1, sort_keys=True))

    STORE.write_text(json.dumps(store, indent=1, sort_keys=True))
    readable = sum(1 for v in store.values() if not v.get("unread"))
    nonews = sum(1 for v in store.values() if v.get("no_news_page"))
    print(f"\n  {got['about']} about-page(s), {got['news']} news page(s), "
          f"{got['unread']} site(s) unread this run")
    print(f"  store now holds {len(store)} companies, {readable} readable, "
          f"{nonews} with no news page found")
    print("\n  UNREAD IS NOT EMPTY. A site we could not open has not been "
          "found to\n  have no customers and no news; it has not been read. "
          "Nothing here\n  writes a description in its place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
