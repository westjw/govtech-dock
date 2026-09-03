#!/usr/bin/env python3
"""What a company's own news page says happened, and when. A parser, not a judge.

    python3 scripts/news.py --measure              # over every stored news page
    python3 scripts/news.py --measure --id brinc
    python3 scripts/news.py --write                # data/news.json

THE OWNER'S RULING: parser plus door plus kill switch, no per-item ruling.
~20,000 items across 2,000 companies cannot be ruled one by one and do not
need to be, because nothing here decides anything. It republishes a headline
and a date that the company's own site printed, on a URL on the company's
own domain, and it refuses whenever either is missing. That is the same class
as salary.py: a derived fact behind a door, never an agent's judgment.

NO DATE, NO ITEM. This is the whole discipline. An index page that lists a
headline with no date beside it yields nothing from that page; the article
itself is then read (fetch_profiles follows one hop) for JSON-LD
datePublished, article:published_time, a <meta> date, a <time datetime>, or
a date printed near the <h1>. A headline that has no date anywhere is not
recorded, because a timeline entry with a guessed date is a false fact about
when something happened, and "recent news" with the wrong year is worse than
no news.

MEASURED BEFORE TRUSTED. --measure prints, per company, what was found, what
was dated, and what the door refused by rule, plus a sample, so the extractor
is judged on the stored pages before its output reaches a public page. The
first survey of 39 stored pages found <time datetime> on 2, JSON-LD on 11,
and plain text dates on 29 - which is why text dates near the anchor are a
first-class source here and not a fallback.
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

import fetch_profiles as fp                                    # noqa: E402

STORE = DATA / "news.json"
KEEP = 25                # items kept per company, newest first
MAX_HEAD = 200
MIN_HEAD = 12
NEAR = 400               # chars either side of an anchor a date may sit in

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
TEXT_DATE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"
    r"|\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b",
    re.I)
# NOT \b AFTER THE DAY. A JSON-LD date is "2026-06-24T14:47:57+00:00", and
# between "4" and "T" there is no word boundary, so every ISO timestamp on
# every site failed to parse and the extractor fell through to weaker sources
# or refused the item outright. 158 of 337 items were refused for "no date"
# because of this one anchor.
ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)(?!\d)")
US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b")

# CHROME THAT LOOKS LIKE A HEADLINE. Measured on the stored pages; each of
# these was an anchor text with four or more words that is not an item.
NAV_CHROME = re.compile(
    r"^(read more|learn more|view all|see all|load more|more news|all news|"
    r"back to (news|blog|top)|skip to (main )?content|subscribe to our|"
    r"sign up for|request a demo|contact (us|sales)|privacy policy|"
    r"cookie (policy|settings)|share (this|on)|follow us|next|previous|"
    r"page \d+|\d+ of \d+)\b", re.I)

# KIND, BY ORDERED RULE, and the rule is recorded on the item so a person can
# see why a headline was filed where it was. First match wins.
NEWS_RULES = (
    ("contract", re.compile(
        r"\b(award(ed|s)?|select(ed|s)|chooses|chose|picks?|taps?|deploys?|"
        r"goes live|went live|"
        r"contract|partners? with|signs?|renew(al|ed|s)|adopts?|implements?|rollout|"
        r"rolls? out)\b", re.I)),
    ("funding", re.compile(
        r"\b(raise[sd]?|funding|series [a-e]\b|investment|invest(s|ed)|acqui(res?|red|sition)|"
        r"merger|merges?|valuation|round)\b", re.I)),
    ("leadership", re.compile(
        r"\b(appoint(s|ed)?|names?|joins? (as|the)|hires?|promot(es|ed)|welcomes|"
        r"new (ceo|cfo|cto|coo|cro|chief|president|vp|vice president|head of)|"
        r"board of directors|as (ceo|cfo|cto|coo|cro|president))\b", re.I)),
    ("product", re.compile(
        r"\b(launch(es|ed)?|introduc(es|ed|ing)|unveil(s|ed)?|releas(es|ed)|"
        r"now available|announces? (new|the)|rolls? out|update[sd]?|version \d|"
        r"integrat(es|ion) with)\b", re.I)),
)


def norm(s: str) -> str:
    s = htmllib.unescape(s or "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"[­​-‍﻿]", "", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def parse_date(s: str) -> str | None:
    """An ISO date out of the forms sites actually print, or None. Never guesses."""
    if not s:
        return None
    m = ISO_DATE.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _iso(y, mo, d)
    m = TEXT_DATE.search(s)
    if m:
        if m.group(1):
            mo, d, y = MONTHS.get(m.group(1)[:3].lower()), int(m.group(2)), int(m.group(3))
        else:
            d, mo, y = int(m.group(4)), MONTHS.get(m.group(5)[:3].lower()), int(m.group(6))
        return _iso(y, mo, d) if mo else None
    m = US_DATE.search(s)
    if m:
        return _iso(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None


def _iso(y: int, mo: int, d: int) -> str | None:
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None          # February 30 is not a date; Date() would roll it


def text_of(html: str) -> str:
    return fp.text_of(html)


# ------------------------------------------------------------ index pages --
A_TAG = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
HREF = re.compile(r"""href\s*=\s*(["'])(.*?)\1""", re.I | re.S)
HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.I | re.S)
TIME_DT = re.compile(r"""<time\b[^>]*datetime\s*=\s*["']([^"']+)["']""", re.I)
INNER = re.compile(r"<[^>]+>")


# WHAT A CARD PUTS IN FRONT OF THE HEADLINE. Anchor text is the whole card,
# so it arrives as "08/10/26 BRINC Drones Adds...", "Article The Large Load
# Imperative...", "Customer stories 'We have a plan'...". The date and the
# category are true and belong in their own fields, not inside the sentence a
# reader sees. Anchored to the START only, and only the shapes measured on the
# stored pages.
LEAD_DATE = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|20\d\d-\d\d-\d\d|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+20\d\d|"
    r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+20\d\d)"
    r"[\s|·\u2013\u2014-]*", re.I)
LEAD_LABEL = re.compile(
    r"^(article|blog|news|press release|press|customer stor(y|ies)|case stud(y|ies)|"
    r"insight|insights|resource|resources|story|stories|update|updates|"
    r"announcement|webinar|guide|report|whitepaper|white paper|ebook|podcast|video)"
    r"[\s:|·\u2013\u2014-]+(?=[A-Z0-9])", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(INNER.sub(" ", s or ""))).strip()


def _headline(s: str) -> tuple[str, str | None]:
    """(headline, a date the card printed in front of it), both cleaned."""
    s = _clean(s)
    date = None
    m = LEAD_DATE.match(s)
    if m:
        date = parse_date(m.group(0))
        s = s[m.end():].strip()
    for _ in range(2):                    # "Article Customer stories Foo"
        m2 = LEAD_LABEL.match(s)
        if not m2:
            break
        s = s[m2.end():].strip()
    return s, date


def _block(html: str, i: int, j: int) -> str:
    """The enclosing list item / article / card around an anchor, roughly:
    back to the nearest <li|article|div and forward to its close, bounded."""
    start = max(html.rfind("<li", 0, i), html.rfind("<article", 0, i), html.rfind("<div", 0, i))
    start = start if start >= 0 else max(0, i - NEAR)
    end = html.find("</li>", j)
    end = end if end >= 0 and end - j < 3000 else min(len(html), j + NEAR)
    return html[max(start, i - NEAR):end]


def items_from_index(html: str, base: str) -> list[dict]:
    """Dated headlines from a list page; undated ones come back with date None
    so the article hop can supply it. Same-site article-shaped links only."""
    host = _host(base)
    out, seen = [], set()
    for m in A_TAG.finditer(html or ""):
        attrs, inner = m.group(1), m.group(2)
        hm = HREF.search(attrs)
        if not hm:
            continue
        u = up.urljoin(base, htmllib.unescape(hm.group(2).strip()).split("#")[0])
        parts = up.urlsplit(u)
        if parts.scheme not in ("http", "https") or _host(u) != host:
            continue
        if not fp.ARTICLE.search(parts.path or ""):
            continue
        key = _norm_url(u)
        if key in seen:
            continue
        block = _block(html, m.start(), m.end())
        head, lead_date = _headline(inner)
        if len(head.split()) < 4 or NAV_CHROME.match(head):
            # the anchor is "Read more" or an image; the headline is the
            # nearest heading in the same card
            hs = [_headline(h)[0] for h in HEADING.findall(block)]
            hs = [h for h in hs if len(h.split()) >= 4 and not NAV_CHROME.match(h)]
            head = hs[0] if hs else ""
        if not head:
            continue
        seen.add(key)
        date, src = lead_date, "card-date"
        tm = TIME_DT.search(block)
        if not date and tm:
            date, src = parse_date(tm.group(1)), "time"
        if not date:
            date, src = parse_date(_clean(block)), "index-text"
        out.append({"url": u, "headline": head[:MAX_HEAD], "date": date,
                    "date_source": src if date else None})
    return out


# ---------------------------------------------------------- article pages --
META = re.compile(r"""<meta\b[^>]*(?:property|name)\s*=\s*["']([^"']+)["'][^>]*content\s*=\s*["']([^"']*)["']""", re.I)
META_R = re.compile(r"""<meta\b[^>]*content\s*=\s*["']([^"']*)["'][^>]*(?:property|name)\s*=\s*["']([^"']+)["']""", re.I)
LD = re.compile(r"<script\b[^>]*application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)
H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)


def _metas(html: str) -> dict:
    d = {}
    for k, v in META.findall(html or ""):
        d.setdefault(k.lower(), htmllib.unescape(v))
    for v, k in META_R.findall(html or ""):
        d.setdefault(k.lower(), htmllib.unescape(v))
    return d


def _ld_date(html: str) -> str | None:
    for blob in LD.findall(html or ""):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', blob)
        if m:
            return parse_date(m.group(1))
    return None


def item_from_article(html: str, url: str) -> dict:
    """{url, headline, date, date_source}; date None when the page states none."""
    metas = _metas(html)
    date, src = _ld_date(html), "json-ld"
    if not date:
        date, src = parse_date(metas.get("article:published_time", "")), "og:published"
    if not date:
        for k in ("date", "pubdate", "publish-date", "dc.date", "dc.date.issued", "sailthru.date"):
            if metas.get(k):
                date, src = parse_date(metas[k]), f"meta:{k}"
                if date:
                    break
    if not date:
        tm = TIME_DT.search(html or "")
        date, src = (parse_date(tm.group(1)) if tm else None), "time"
    if not date:
        h1 = H1.search(html or "")
        if h1:
            i = h1.start()
            date, src = parse_date(_clean((html or "")[max(0, i - 300):i + 600])), "near-h1"
    # THE <h1> FIRST, og:title SECOND. The door checks a headline against the
    # page TEXT, and text_of strips <head>, so an og:title carrying a suffix
    # the visible heading lacks ("... Launch Press Release") is refused as
    # not-on-the-page - a true item lost to a formatting difference. The h1
    # is the thing a reader actually saw.
    h1 = H1.search(html or "")
    head = _headline(h1.group(1))[0] if h1 else ""
    if len(head.split()) < 4:
        head = _headline(metas.get("og:title") or "")[0] or head
    if not head:
        tt = TITLE.search(html or "")
        head = _headline(tt.group(1))[0] if tt else ""
        head = re.split(r"\s+[|–—-]\s+", head)[0]      # strip the site suffix
    return {"url": url, "headline": head[:MAX_HEAD], "date": date,
            "date_source": src if date else None}


# ------------------------------------------------------------------ door --
def kind(headline: str) -> tuple[str, str | None]:
    for name, rx in NEWS_RULES:
        m = rx.search(headline or "")
        if m:
            return name, m.group(0)
    return "press", None


def check_news_item(item: dict, texts: dict[str, str], company: dict,
                    today: str | None = None) -> str | None:
    """A sentence naming the rule that refuses the item, or None to keep it."""
    url, head, date = item.get("url") or "", item.get("headline") or "", item.get("date")
    if not date:
        return "1. no date stated on the index or the article"
    today = today or dt.date.today().isoformat()
    if date > today:
        return f"2. date {date} is in the future"
    floor = str(company.get("year_founded") or 2000)
    if date[:4] < str(floor)[:4] and date[:4] < "2000":
        return f"2. date {date} is before {floor}"
    site_host = _host(company.get("website") or "")
    h = _host(url)
    if not site_host or not (h == site_host or h.endswith("." + site_host)):
        return f"3. {h!r} is not the company's own domain"
    if len(head) < MIN_HEAD or len(head) > MAX_HEAD:
        return f"4. headline length {len(head)} is outside {MIN_HEAD}..{MAX_HEAD}"
    # A SECTION INDEX IS NOT AN ITEM. /resources/insights/ matches the article
    # shape (a path segment plus a slug), so a listing page yielded its own
    # og:title - "CredibleMind" - as a news item dated by its JSON-LD. An item
    # is a sentence about something that happened; four words is the same
    # floor the index parser already applies to an anchor.
    if len(head.split()) < 4:
        return f"4. headline {head!r} is {len(head.split())} words, not a story"
    if NAV_CHROME.match(head):
        return f"5. headline {head[:40]!r} is navigation"
    if re.fullmatch(r"[\d\s.,%$]+", head):
        return "5. headline is a bare number"
    hay = norm(" ".join(texts.values()))
    if norm(head) not in hay:
        return f"6. headline {head[:40]!r} does not appear verbatim on any fetched page"
    return None


def _host(u: str) -> str:
    h = (up.urlsplit(u or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _norm_url(u: str) -> str:
    p = up.urlsplit(u)
    return f"{_host(u)}{(p.path or '/').rstrip('/').lower()}"


# ---------------------------------------------------------------- extract --
def extract(company: dict, rec: dict, today: str | None = None) -> dict:
    """{state, items, refused} for one company from its stored news pages."""
    pages = [pg for pg in (rec.get("news") or []) if pg.get("html")]
    if rec.get("unread"):
        return {"state": "unread", "items": [], "refused": []}
    if not pages:
        return {"state": "no_news_page" if rec.get("no_news_page") else "unread",
                "items": [], "refused": []}
    texts = {pg["url"]: pg.get("text") or text_of(pg["html"]) for pg in pages}
    by_url: dict[str, dict] = {}
    # AN "INDEX" MAY ITSELF BE AN ARTICLE. fetch_profiles follows /news/ from
    # a homepage, and on a site whose newsroom link points at the latest post
    # that page IS the post - brinc's did. Parsed as an index, its own
    # headline came back as an anchor pointing at itself with no date beside
    # it, and the site's biggest story ("Raises $125 Million") was refused.
    # Any page whose own url is article-shaped yields its own item first, and
    # its links are still read, because a post page also lists related posts.
    for pg in pages:
        if pg.get("from_index"):
            continue
        if fp.ARTICLE.search(up.urlsplit(pg["url"]).path or ""):
            own = item_from_article(pg["html"], pg["url"])
            if own.get("headline"):
                by_url[_norm_url(pg["url"])] = own
        for it in items_from_index(pg["html"], pg["url"]):
            by_url.setdefault(_norm_url(it["url"]), it)
    # article pages: the authoritative date, and a headline if the index had none
    for pg in pages:
        if not pg.get("from_index"):
            continue
        art = item_from_article(pg["html"], pg["url"])
        k = _norm_url(pg["url"])
        cur = by_url.get(k)
        if cur:
            if art["date"] and (not cur["date"] or art["date_source"] in ("json-ld", "og:published")):
                cur["date"], cur["date_source"] = art["date"], art["date_source"]
            if not cur.get("headline") and art["headline"]:
                cur["headline"] = art["headline"]
        elif art["headline"]:
            by_url[k] = art
    kept, refused = [], []
    for it in by_url.values():
        why = check_news_item(it, texts, company, today)
        if why:
            refused.append({**it, "why": why})
            continue
        k_, rule = kind(it["headline"])
        kept.append({**it, "kind": k_, "kind_rule": rule})
    kept.sort(key=lambda x: x["date"], reverse=True)
    state = "items" if kept else "none_found"
    return {"state": state, "items": kept[:KEEP], "refused": refused}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--sample", type=int, default=3)
    a = ap.parse_args()

    import admin
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    by_id = {c["id"]: c for c in seq if c.get("id")}
    idx = fp.index()
    ids = a.id or [cid for cid, e in idx.items() if e.get("news")]
    today = dt.date.today().isoformat()

    results = {}
    for cid in ids:
        rec = fp.load(cid)
        c = by_id.get(cid)
        if not rec or not c:
            continue
        results[cid] = extract(c, rec, today)

    if a.measure or not a.write:
        tot_items = tot_ref = 0
        by_rule: dict[str, int] = {}
        by_src: dict[str, int] = {}
        print(f"{'company':22} {'state':12} {'items':>5} {'refused':>7}  top refusal")
        for cid, r in sorted(results.items(), key=lambda kv: -len(kv[1]["items"])):
            tot_items += len(r["items"]); tot_ref += len(r["refused"])
            rules: dict[str, int] = {}
            for x in r["refused"]:
                k = x["why"].split(".")[0]
                rules[k] = rules.get(k, 0) + 1
                by_rule[k] = by_rule.get(k, 0) + 1
            for x in r["items"]:
                by_src[x["date_source"] or "?"] = by_src.get(x["date_source"] or "?", 0) + 1
            top = max(rules.items(), key=lambda kv: kv[1])[0] if rules else ""
            print(f"{cid[:22]:22} {r['state']:12} {len(r['items']):5} {len(r['refused']):7}  {top}")
        print(f"\n{len(results)} companies · {tot_items} items kept · {tot_ref} refused")
        print("refusals by rule:", dict(sorted(by_rule.items())))
        print("kept, by date source:", by_src)
        kinds: dict[str, int] = {}
        for r in results.values():
            for x in r["items"]:
                kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        print("kept, by kind:", kinds)
        print(f"\nsample of kept items:")
        shown = 0
        for cid, r in results.items():
            for x in r["items"][:1]:
                print(f"  {cid:20} {x['date']}  [{x['kind']}]  {x['headline'][:70]}")
                print(f"  {'':20} {x['url'][:90]}   ({x['date_source']})")
                shown += 1
            if shown >= a.sample * 3:
                break
        print(f"\nsample of refused:")
        shown = 0
        for cid, r in results.items():
            for x in r["refused"][:1]:
                print(f"  {cid:20} {x['why'][:60]:62} {x['headline'][:50]!r}")
                shown += 1
            if shown >= a.sample * 2:
                break

    if a.write:
        store = json.loads(STORE.read_text()) if STORE.exists() else {}
        for cid, r in results.items():
            cur = store.get(cid) or {"items": []}
            have = {_norm_url(x["url"]): x for x in cur.get("items") or []}
            for x in r["items"]:
                k = _norm_url(x["url"])
                if k not in have:
                    have[k] = {**x, "first_seen": today}
            items = sorted(have.values(), key=lambda x: x["date"], reverse=True)[:KEEP]
            store[cid] = {"checked_on": today, "state": r["state"] if items or r["state"] != "items" else "items",
                          "items": items}
        STORE.write_text(json.dumps(store, indent=1, sort_keys=True))
        print(f"\nwrote {STORE.relative_to(ROOT)}: {len(store)} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
