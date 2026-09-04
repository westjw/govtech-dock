#!/usr/bin/env python3
"""Read a company's news from the feed it already publishes for machines.

    python3 scripts/feeds.py --measure          # how many companies have one
    python3 scripts/feeds.py --id brinc         # what one company's feed says

WHY A FEED AND NOT THE PAGE. `news.py` parses newsroom HTML, and it works -
226 items kept against 107 refused on the first 19 companies - but every
hard-won line of it is about recovering a date from markup that was never
meant to be read: a `<time datetime>` two of thirty-nine pages carried,
JSON-LD hidden in a `<script>`, an `ISO_DATE` pattern whose trailing `\\b`
silently failed on every timestamp because there is no word boundary between
`4` and `T`.

A feed states the date in a field. Measured across the newsrooms already on
disk:

    declare an RSS/Atom feed in the page head        676
    mention any wire service at all                   97
    neither                                          575

That 97 is Business Wire, PR Newswire, GlobeNewswire, AccessWire and PRWeb
COMBINED, so a wire-service strategy reaches about one company in fourteen.
The feed reaches seven times more, is published deliberately for machines, and
carries a date nobody has to infer.

WHAT THIS CHANGES ABOUT COST, which is the reason it matters at four sweeps a
day. An unchanged feed answers 304 through `ats._get`'s conditional request
and there is nothing more to do: no HTML to parse and, crucially, no second
hop. 12,559 article pages are held on disk purely because an index page rarely
dates its own items; a feed dates them, so those fetches never happen.

WHAT IT DOES NOT CHANGE. The door. `news.check_news_item` rules on a feed item
exactly as it rules on a parsed one - the headline must appear verbatim in
something we fetched, the date must be real and not in the future, the host
must be the company's own. The feed body is what it checks the headline
against, which is true by construction and is the point: a feed is the
company's own words about itself, which is the same standard every other
engine here is held to.

HTML STAYS THE FALLBACK, always. 575 companies publish no feed, and for them
`news.py` is the only reader there is. A feed is a better source, not a
replacement, and a company that stops publishing one must fall back rather
than go quiet.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.parse as up
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                                      # noqa: E402
import news                                                     # noqa: E402

# A <link rel="alternate" type="application/rss+xml"> in the head. Attribute
# order varies, so the tag is matched first and its parts read out of it -
# the same ordering lesson as NLC's entity-encoded hrefs, where a pattern that
# tried to do both at once stopped at the first surprise.
LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
REL_ALT = re.compile(r'\brel\s*=\s*["\']?alternate\b', re.I)
FEED_TYPE = re.compile(r'\btype\s*=\s*["\']?application/(rss|atom)\+xml', re.I)
HREF = re.compile(r'\bhref\s*=\s*["\']([^"\']+)["\']', re.I)

# Tried only when the head declares nothing. Ordered by how often they answer.
COMMON = ("/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
          "/news/feed", "/blog/feed", "/index.xml")

MAX_ITEMS = 40

# A BLOG FEED IS NOT A NEWS FEED, and Motorola Solutions is the case that
# settled it. Their declared feed is /blog/rss.xml and it returns ten dated
# items on their own domain, every one of which would pass check_news_item:
# "Apartment Intercom Systems for Multi-Tenant Buildings", "Gym Access
# Control: Secure Guest Check-in", "Biometric Access Control: Benefits & How
# to Set Up a System". That is search-engine copy, and a news timeline full of
# it is worse than an empty one - an empty one is honest.
#
# `news.kind` already separates them and nobody had asked it to: all ten fall
# through every NEWS_RULE to the "press" default. So a feed whose address says
# blog is held to the stricter bar - an item must match a real rule (a
# contract, funding, a leadership change, a product launch) - while a /news or
# /press feed keeps its press releases, because a press release IS the news.
BLOGGY = re.compile(r"/(blog|insights?|resources?|learn|guides?)(/|\.|$)", re.I)
NEWSY = re.compile(r"/(news|press|announce|media|newsroom)", re.I)


def feed_shape(url: str) -> str:
    """"news", "blog", or "unknown" - what the address says it is."""
    if NEWSY.search(url or ""):
        return "news"
    if BLOGGY.search(url or ""):
        return "blog"
    return "unknown"


def feed_urls(html: str, base: str) -> list[str]:
    """Feed addresses this page declares, most-declared first, absolute."""
    out: list[str] = []
    for tag in LINK_TAG.findall(html or ""):
        if not (REL_ALT.search(tag) and FEED_TYPE.search(tag)):
            continue
        m = HREF.search(tag)
        if not m:
            continue
        # THE HREF IS UNESCAPED BEFORE IT IS JOINED, not after. A feed link
        # written &#x2F; survives urljoin as a literal and resolves to
        # nothing; the same ordering bug lost 30 of 49 NLC chapter links.
        href = (m.group(1).replace("&amp;", "&").replace("&#x2F;", "/")
                .replace("&#47;", "/").strip())
        u = up.urljoin(base, href)
        if u not in out:
            out.append(u)
    return out


def _text(el, *names) -> str:
    for n in names:
        for child in el:
            if child.tag.split("}")[-1].lower() == n:
                if (child.text or "").strip():
                    return child.text.strip()
                # Atom puts the address in an attribute, not the body
                if child.get("href"):
                    return child.get("href").strip()
    return ""


def items(body: str, base: str, shape: str | None = None) -> list[dict]:
    """{url, headline, date} per entry. Never guesses a date it cannot read.

    A FEED WITHOUT A DATE IS NOT AN ITEM, exactly as an undated headline on a
    page is not one. The whole reason to prefer a feed is that it states the
    date; one that does not has no advantage over the HTML and gets none.

    `shape` decides how strict the bar is - see BLOGGY above. It defaults to
    reading the address, so a caller cannot forget.
    """
    body = (body or "").strip()
    # A DECLARED FEED CAN LIE. Mark43's page head declares /feed/ and that
    # address serves 228KB of HTML. Refusing to parse it is right; saying
    # nothing about why is not, because "0 items" reads as "no news".
    if body[:200].lstrip().lower().startswith("<!doctype html") or "<html" in body[:200].lower():
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        head = _text(el, "title")
        link = _text(el, "link", "id")
        raw = _text(el, "pubdate", "published", "updated", "date")
        if not (head and link):
            continue
        out.append({"url": up.urljoin(base, link), "headline": head,
                    "date": news.parse_date(raw), "date_source": "feed"})
        if len(out) >= MAX_ITEMS:
            break
    if (shape or feed_shape(base)) == "blog":
        # only items a real rule recognises; "press" is the fallback and on a
        # blog it means "we could not tell what this is", which is not news
        out = [it for it in out if news.kind(it["headline"])[0] != "press"]
    return out


def read(url: str) -> tuple[list, bool, str]:
    """(items, was_unchanged, note). A 304 is the cheap, common answer."""
    try:
        resp = ats._get(url)
    except Exception as exc:                       # ats raises its own type
        return [], False, str(exc)[:100]
    # ats._get hands back a _Cached on 304, which is how a watch sweep knows
    # there is nothing new and can skip the article hop entirely.
    unchanged = type(resp).__name__ == "_Cached"
    body = getattr(resp, "text", "") or ""
    got = items(body, url)
    if not got and ("<html" in body[:200].lower()
                    or body.lstrip()[:15].lower().startswith("<!doctype")):
        return [], unchanged, "that address serves HTML, not a feed"
    return got, unchanged, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--id")
    a = ap.parse_args()
    import admin
    import fetch_profiles as fp

    if a.id:
        c = next((x for x in admin.read_companies() if x.get("id") == a.id), None)
        if not c:
            print(f"no company {a.id!r}")
            return 1
        rec = fp.load(a.id) or {}
        pages = [p for p in (rec.get("news") or []) if p.get("html")]
        urls: list[str] = []
        for pg in pages:
            urls += [u for u in feed_urls(pg["html"], pg["url"]) if u not in urls]
        print(f"{c['name']}: {len(urls)} feed(s) declared")
        for u in urls:
            got, unchanged, note = read(u)
            print(f"  {u}")
            print(f"    {len(got)} item(s) [{feed_shape(u)} feed]"
                  + (" (304, unchanged)" if unchanged else "")
                  + (f"  {note}" if note else ""))
            for it in got[:5]:
                print(f"      {it['date'] or '(no date)'}  {it['headline'][:64]}")
        return 0

    if a.measure:
        companies = admin.read_companies()
        have = 0
        for c in companies:
            rec = fp.load(c["id"])
            if not rec:
                continue
            for pg in (rec.get("news") or []):
                if pg.get("html") and feed_urls(pg["html"], pg["url"]):
                    have += 1
                    break
        print(f"{have} of {len(companies)} companies declare a feed on a "
              f"newsroom page already on disk")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
