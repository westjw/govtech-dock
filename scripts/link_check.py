#!/usr/bin/env python3
"""Does every link the board publishes still lead where it says?

    python3 scripts/link_check.py              # check and report; nothing written
    python3 scripts/link_check.py --write      # data/link_health.json
    python3 scripts/link_check.py --id fathom

THE CHECK THAT RE-ASKS. find_websites.identifies() judges a website once, at
discovery, and site_identity.py re-reads only the pages cached back then. A
domain that was a vendor's in March and a casino in September passes both,
because neither goes back and looks: gwfathom.com was taken down from the
company record on 2026-09-13 for serving crypto-casino spam and its CAREERS
link, on the same domain, was still on the public page three weeks later
(found 2026-10-05). So this fetches every outbound link the board publishes,
live - each company's website, its careers page and where it posts - and
says what each one is today.

FOUR FINDINGS, and only the first two hide a link:
  spam      the page's subject is gambling: three or more different gambling
            words, or an unmistakable one in its title - a job board sending
            visitors to slot sites under a vendor's name. Not a judgement call.
  for_sale  a for-sale or parking page, said in the words a reader sees (or a
            final address on a domain marketplace). Nothing of theirs is there.
  moved     the link now lands on a different registrable domain - usually an
            acquisition (cartegraph.com names OpenGov). The link may well be
            right; the RECORD may not be. Shown to a person, never hidden.
  unread    the fetch failed or was refused. A fact about the fetch, never about
            the company: a bot wall is not a dead link. With no HTTP answer at
            all, `why` names which silence it was (a domain gone from DNS is
            the only one that says the address is dead), and `plain_http` is
            where the same address goes over http - evidence for the record,
            and a for-sale page there hides the link like one over https.

build_board reads link_health.json and does not publish a link flagged spam or
for_sale (the record is untouched; a person fixes it from the admin). Paced
per host through ats._get, identified, live (no HTTP cache - a cached 304
hides the redirect that is the whole finding).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
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
import ats            # noqa: E402
import site_identity  # noqa: E402

OUT = DATA / "link_health.json"
HIDE = ("spam", "for_sale")
MARKET = re.compile(r"(^|\.)(hugedomains|dan|afternic|sedo|dynadot|undeveloped|"
                    r"domainmarket|buydomains|bodis|parkingcrew|sedoparking)\.(com|net|io)$", re.I)
LANDER = re.compile(r"/lander(/|$|\?)", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
TAGS = re.compile(r"<(script|style|noscript)\b.*?</\1>|<[^>]+>", re.S | re.I)


def registrable(host: str) -> str:
    """example.co.uk -> example.co.uk, www.a.example.com -> example.com (close enough
    for "did it leave the domain": a second-level public suffix keeps three labels)."""
    h = (host or "").lower().strip(".")
    if h.startswith("www."):
        h = h[4:]
    parts = h.split(".")
    if len(parts) >= 3 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def brand_changed(url: str, final: str) -> bool:
    """A redirect onto a different NAME, not just a new address for the same one.
    automotus.co -> automotus.ai and aurelian.io -> aurelian.com are the same
    company moving house; cartegraph.com -> opengov.com is a purchase."""
    a = re.sub(r"[^a-z0-9]", "", registrable(up.urlsplit(url).hostname or "").split(".")[0])
    b = re.sub(r"[^a-z0-9]", "", registrable(up.urlsplit(final).hostname or "").split(".")[0])
    if not a or not b or a == b:
        return False
    return not (a in b or b in a or a[:5] == b[:5])


def links_of(c: dict) -> list[tuple[str, str]]:
    """(field, url) for every link the board publishes for this company."""
    out = []
    if (c.get("website") or "").strip():
        out.append(("website", c["website"].strip()))
    ats_ = c.get("ats") or {}
    if ats_.get("type") == "html" and str(ats_.get("ref") or "").startswith("http"):
        out.append(("board", ats_["ref"]))
    pa = c.get("posts_at")
    if isinstance(pa, dict) and str(pa.get("url") or "").startswith("http"):
        out.append(("posts_at", pa["url"]))
    return out


# A WORD IS NOT A SUBJECT. The first full run flagged 33 links as spam and 31
# were vendors: BondLink says "slot" seven times (time slots), Trimble "spin"
# ten (a loading spinner's name in the page text). The three real hijacks
# carry FIVE or more different gambling words - gwfathom.com casino, spins,
# jackpot, sportsbook, betting - so a page is spam when it uses at least
# three different ones, or when its title names an unmistakable one.
STRONG = re.compile(r"\b(casinos?|gacor|togel|judi|maxwin|sportsbooks?|taruhan|"
                    r"situs\s+slot|slots?\s+online|mahjong\s+ways)\b", re.I)
SPAM_DISTINCT = 3

# A FOR-SALE PAGE SAYS SO, in words a reader sees. find_websites._parked also
# counts a registrar's NAME anywhere in the first 4KB, which is right for
# rejecting a guessed domain at discovery and wrong for hiding a published
# link: legacymark.com is a real cemetery-software vendor whose page loads a
# GoDaddy script. Here only the visible text and the title count.
FOR_SALE = re.compile(r"domain (is )?for sale|buy this domain|this domain may be for sale|"
                      r"[\w-]+\.(com|net|io|co|us|ai|org)\s+(is\s+)?for sale|"
                      r"domains for sale|parked domain|parked free|domain parking", re.I)


def is_spam(title: str, text: str) -> bool:
    hits = {m.group(0).lower().rstrip("s") for m in site_identity.SPAM.finditer(text or "")}
    total = len(site_identity.SPAM.findall(text or ""))
    return (len(hits) >= SPAM_DISTINCT and total >= site_identity.SPAM_MIN) \
        or bool(STRONG.search(title or ""))


def judge(url: str, status: int | None, final: str, html: str) -> dict:
    """What the link is today."""
    m = TITLE.search(html or "")
    title = htmllib.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:160] if m else ""
    if status is None or status >= 400 or not html:
        return {"kind": "unread", "status": status, "final": final, "title": title}
    text = htmllib.unescape(TAGS.sub(" ", html[:300000]))
    if is_spam(title, text):
        return {"kind": "spam", "status": status, "final": final, "title": title}
    fhost = (up.urlsplit(final).hostname or "")
    if FOR_SALE.search(title + " " + text[:6000]) or MARKET.search(fhost) \
            or LANDER.search(up.urlsplit(final).path or ""):
        return {"kind": "for_sale", "status": status, "final": final, "title": title}
    if registrable(fhost) != registrable(up.urlsplit(url).hostname or ""):
        return {"kind": "moved", "status": status, "final": final, "title": title}
    return {"kind": "ok", "status": status, "final": final, "title": title}


# WHICH SILENCE. The 2026-10-07 run listed 61 websites as answering nothing,
# with no status and no reason, and they were five different facts: 17
# domains gone from DNS, a dozen certificates expired (thecitybase.com,
# escapetech.com - both now redirect, over plain http, to the company that
# bought them), timeouts, and dropped connections. Only the first says the
# address is dead; a person fixing the record needs to know which it is.
SILENCES = (
    ("no such domain", re.compile(r"NameResolution|getaddrinfo|nodename nor servname|"
                                  r"Name or service not known|No address associated", re.I)),
    ("certificate expired", re.compile(r"certificate has expired", re.I)),
    ("certificate is for another address", re.compile(r"[Hh]ostname mismatch|doesn't match|"
                                                      r"not valid for", re.I)),
    ("certificate not trusted", re.compile(r"CERTIFICATE_VERIFY_FAILED|SSLCertVerification", re.I)),
    ("secure connection failed", re.compile(r"SSLError|SSL:|TLSV1|WRONG_VERSION", re.I)),
    ("timed out", re.compile(r"Timeout|timed out", re.I)),
    ("connection refused", re.compile(r"ConnectionRefused|Connection refused", re.I)),
    ("connection dropped", re.compile(r"ConnectionReset|Connection reset|Connection aborted|"
                                      r"RemoteDisconnected", re.I)),
)


def why_unanswered(err: str) -> str:
    """The reason a fetch got no HTTP answer at all, from the error's text."""
    for why, pat in SILENCES:
        if pat.search(err or ""):
            return why
    return "network error"


def fetch(url: str) -> tuple[int | None, str, str, str]:
    """(status or None, final url, body, why there was no answer). Live: no
    HTTP cache."""
    ats.HTTP_CACHE = None
    try:
        r = ats._get(url)
        return 200, getattr(r, "url", url) or url, getattr(r, "text", "") or "", ""
    except ats.AtsError as exc:
        m = re.search(r"HTTP (\d{3})", str(exc))
        if m:
            return int(m.group(1)), url, "", ""
        return None, url, "", why_unanswered(str(exc))
    except Exception as exc:                                # noqa: BLE001
        return None, url, "", why_unanswered(repr(exc))


def moved_to(row: dict) -> str | None:
    """Where this link lands on another domain, if it does: the link itself,
    or - when the https address gave no answer - the same address over plain
    http. thecitybase.com's certificate has expired and over http it goes to
    Euna; that redirect is the acquisition evidence, even though a visitor
    clicking the https link sees only a certificate warning."""
    if row.get("kind") == "moved":
        return row.get("final")
    plain = row.get("plain_http") or {}
    if plain.get("kind") == "moved":
        return plain.get("final")
    return None


def check(companies: list, get=fetch, workers: int = 8) -> dict:
    """{company_id: [{field, url, kind, status, final, title, why?, plain_http?}]}
    for every link."""
    jobs = [(c["id"], field, url) for c in companies for field, url in links_of(c)]
    seen: dict = {}
    out: dict = {}

    def one(url):
        status, final, body, why = (tuple(get(url)) + ("",))[:4]
        res = judge(url, status, final, body)
        if status is None:
            res["why"] = why or "network error"
        # PLAIN HTTP, AS EVIDENCE ONLY. The published https link stays unread
        # - that is what a visitor gets - but where the same address goes over
        # http says whether the company moved, was bought, or let the domain
        # go to a for-sale page. A dead domain has nothing to ask.
        if status is None and res["why"] != "no such domain" \
                and url.lower().startswith("https://"):
            s2, f2, b2, _w = (tuple(get("http://" + url[8:])) + ("",))[:4]
            if s2 == 200 and b2:
                plain = judge(url, s2, f2, b2)
                res["plain_http"] = {"kind": plain["kind"], "final": f2, "title": plain["title"]}
        return res

    urls = sorted({u for _, _, u in jobs})
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, u): u for u in urls}
        for n, fut in enumerate(cf.as_completed(futs), 1):
            seen[futs[fut]] = fut.result()
            if n % 250 == 0:
                print(f"  ... {n}/{len(urls)} links", flush=True)
    for cid, field, url in jobs:
        out.setdefault(cid, []).append({"field": field, "url": url, **seen[url]})
    return out


def hidden(health: dict | None) -> set:
    """The urls build_board must not publish: flagged spam or for_sale, by
    the link itself or by the same address over plain http."""
    bad = set()
    for rows in ((health or {}).get("links") or {}).values():
        for r in rows:
            # an https link that does not answer, on a domain that over plain
            # http is for sale, is not theirs either (platinumtr.com, 10-08)
            if r.get("kind") in HIDE or (r.get("plain_http") or {}).get("kind") in HIDE:
                bad.add(r.get("url"))
    return bad


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--id", default="")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    companies = json.loads((DATA / "companies.json").read_text())
    if a.id:
        companies = [c for c in companies if c["id"] in set(a.id.split(","))]
    res = check(companies, workers=a.workers)
    flagged = {cid: [r for r in rows if r["kind"] != "ok"] for cid, rows in res.items()}
    flagged = {k: v for k, v in flagged.items() if v}
    counts: dict = {}
    for rows in res.values():
        for r in rows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(f"{sum(len(v) for v in res.values())} links checked: " +
          ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    whys: dict = {}
    for rows in res.values():
        for r in rows:
            if r.get("why"):
                whys[r["why"]] = whys.get(r["why"], 0) + 1
    if whys:
        print("  no answer: " + ", ".join(f"{n} {w}" for w, n in sorted(whys.items(), key=lambda x: -x[1])))
    for kind in ("spam", "for_sale"):
        for cid, rows in sorted(flagged.items()):
            for r in rows:
                if r["kind"] == kind or (r.get("plain_http") or {}).get("kind") == kind:
                    t = r["title"] or (r.get("plain_http") or {}).get("title") or ""
                    print(f"  {kind.upper():9} {cid}: {r['field']} {r['url']} -> {t[:70]!r}")
    if a.write and not a.id:
        OUT.write_text(json.dumps({
            "note": "What every link the board publishes led to when last checked "
                    "(scripts/link_check.py). build_board does not publish a link "
                    "flagged spam or for_sale. Only links that are not ok are listed. "
                    "'why' says why a link gave no answer; 'plain_http' is where the "
                    "same address went over http, as evidence only.",
            "checked_on": dt.date.today().isoformat(),
            "counts": counts, "links": flagged}, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
