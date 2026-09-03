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
import hashlib
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

# ONE FILE PER COMPANY, GITIGNORED, AND A SMALL INDEX THAT IS NOT. The first
# version kept every company's page text in a single committed JSON. At three
# companies that was 39KB; at 2,024 it is ~100MB of other people's words in
# the history of a repository that is going public, which is exactly the
# species .gitignore already refuses for http_cache. So the bodies live in
# data/site_pages/<id>.json and never reach git, and what IS committed is
# which pages were read, when, and a sha of each - enough for a proposal
# that quotes a page to say which bytes it was checked against, six months
# on, without the bytes themselves being in the repo.
DIR = DATA / "site_pages"
INDEX = DATA / "site_pages_index.json"


def load(cid: str) -> dict | None:
    f = DIR / f"{cid}.json"
    return json.loads(f.read_text()) if f.exists() else None


def save(rec: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{rec['id']}.json").write_text(json.dumps(rec, indent=1, sort_keys=True))


def index() -> dict:
    return json.loads(INDEX.read_text()) if INDEX.exists() else {}


def index_entry(rec: dict) -> dict:
    """What the committed index knows about one company: never the text."""
    slim = lambda pages: [{"url": p["url"], "chars": p.get("chars"), "sha": p.get("sha")}
                          if p.get("text") else {"url": p["url"], "unread": p.get("unread")}
                          for p in pages]
    return {"fetched_on": rec.get("fetched_on"), "website": rec.get("website"),
            "unread": rec.get("unread"), "unread_on": rec.get("unread_on"),
            "about": slim(rec.get("about") or []), "news": slim(rec.get("news") or []),
            "no_news_page": bool(rec.get("no_news_page"))}


def save_index(idx: dict) -> None:
    INDEX.write_text(json.dumps(idx, indent=1, sort_keys=True))

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
MAX_HTML = 160000       # raw markup kept for news pages only

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


def grab(url: str, keep_html: bool = False) -> dict:
    """One page, as {url, text, chars, sha} or {url, unread: why}."""
    try:
        resp = ats._get(url)
    except Exception as exc:                      # ats raises its own type
        return {"url": url, "unread": str(exc)[:120]}
    body = getattr(resp, "text", "") or ""
    txt = text_of(body)
    if len(txt) < MIN_TEXT:
        return {"url": url, "unread": f"only {len(txt)} chars of text"}
    out = {"url": url, "text": txt[:MAX_TEXT], "chars": len(txt),
           # the sha is of the WHOLE text, not the stored slice, so a quote
           # checked later is checked against what the page actually said
           "sha": hashlib.sha256(txt.encode("utf-8")).hexdigest()[:16]}
    if keep_html:
        # THE NEWS EXTRACTOR NEEDS ATTRIBUTES. text_of drops <head> and every
        # <time datetime>, article:published_time and JSON-LD date before a
        # parser can see them, so news pages keep their markup, capped.
        out["html"] = body[:MAX_HTML]
    return out


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
        out["news"].append(grab(u, keep_html=True))

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
    ap.add_argument("--skip-unread-days", type=int, default=7,
                    help="do not re-ask a host that would not answer within N days")
    ap.add_argument("--workers", type=int, default=4,
                    help="companies in flight at once. ats._host_gate already "
                         "serialises callers to ONE host, so this parallelises "
                         "across hosts and a rude server stalls only its own lane")
    a = ap.parse_args()

    companies = admin.read_companies()
    if isinstance(companies, dict):
        companies = list(companies.values())
    idx = index()
    today = dt.date.today()

    rows = [c for c in companies if c.get("id")]
    if a.id:
        rows = [c for c in rows if c["id"] in set(a.id)]
    if a.sector:
        rows = [c for c in rows if c.get("sector") == a.sector]
    if a.category:
        rows = [c for c in rows if c.get("category") == a.category]
    if not a.refetch:
        rows = [c for c in rows if c["id"] not in idx]
    # A HOST THAT WOULD NOT ANSWER LAST WEEK IS NOT ASKED AGAIN TONIGHT. The
    # discovery log learned this the hard way: two degraded runs wrote 55
    # false "gave up" notes that persisted for weeks. Here the refusal is
    # dated and expires, so a site that was down is retried and a site that
    # is gone stops costing a request a night.
    def recently_unread(c):
        e = idx.get(c["id"]) or {}
        if not e.get("unread") or not e.get("unread_on"):
            return False
        try:
            return (today - dt.date.fromisoformat(e["unread_on"])).days < a.skip_unread_days
        except ValueError:
            return False
    rows = [c for c in rows if not recently_unread(c)]
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

    import concurrent.futures as cf
    got = {"about": 0, "news": 0, "unread": 0}
    done = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        for rec in pool.map(visit, rows):
            if rec.get("unread"):
                rec["unread_on"] = today.isoformat()
                got["unread"] += 1
            got["about"] += sum(1 for p in rec["about"] if p.get("text"))
            got["news"] += sum(1 for p in rec["news"] if p.get("text"))
            save(rec)
            idx[rec["id"]] = index_entry(rec)
            done += 1
            if done % 25 == 0:
                print(f"  ... {done}/{len(rows)}")
                save_index(idx)

    save_index(idx)
    readable = sum(1 for v in idx.values() if not v.get("unread"))
    nonews = sum(1 for v in idx.values() if v.get("no_news_page"))
    print(f"\n  {got['about']} about-page(s), {got['news']} news page(s), "
          f"{got['unread']} site(s) unread this run")
    print(f"  index now holds {len(idx)} companies, {readable} readable, "
          f"{nonews} with no news page found; bodies in {DIR.relative_to(ROOT)}/")
    print("\n  UNREAD IS NOT EMPTY. A site we could not open has not been "
          "found to\n  have no customers and no news; it has not been read. "
          "Nothing here\n  writes a description in its place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
