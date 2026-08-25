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
# "domain for sale" only catches resellers that use the word "domain".
# Spaceship and NamePros write "GetOracle.com for sale", which read as a live
# page and put ten for-sale listings into the dataset. Requiring a domain-shaped
# token before "for sale" keeps a real page that happens to list property.
# WORD BOUNDARIES ON THE BARE VENDOR NAMES. `sedo` was an unanchored
# alternative, so it matched inside "onmou-SEDO-wn" - and a WordPress
# lazy-load listener puts "mousedown" in the first 4KB of a great many company
# homepages. bentley.com and soilflo.com were both judged PARKED and rejected
# by identifies() for exactly that reason, which fails in the honest direction
# ("no website found") and is still wrong: the page is real and the company is
# named right there in the title.
PARKED = re.compile(r"domain (is )?for sale|buy this domain|parked (free )?(at|by)|"
                    r"\bgodaddy\b|\bnamecheap\b|\bsedo\b|\bhugedomains\b|"
                    r"this domain may be for sale|"
                    r"under construction|coming soon|"
                    r"[\w-]+\.(com|net|io|co|us|ai|org)\s+(is\s+)?for sale|"
                    r"spaceship\.com|\bnamepros\b|domains for sale|parked domain|"
                    r"human verification", re.I)


def _parked(html: str) -> bool:
    """Is this a for-sale or holding page?

    Two windows, because one was not enough in either direction.

    The first 4KB, as before - a parked page says so immediately, and reading
    a whole 130KB document for this on every candidate is waste.

    AND the <title>, wherever it sits. A DomainMarket listing for
    vocaltechnologies.com carries "Technology Domains for Sale" IN ITS TITLE
    and still passed, because 4KB of inline gclid tracking script pushed the
    title to byte 4162. It missed by 162 bytes, and the page went on to
    satisfy the identity check outright: the for-sale headline contains both
    of the company's name tokens, which is the whole business model of a
    domain squatter.
    """
    if not html:
        return False
    if PARKED.search(html[:4000]):
        return True
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:60000], re.S | re.I)
    return bool(m and PARKED.search(m.group(1)))


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


def identifies(html: str, name: str, base: str | None = None,
               aliases: list | None = None) -> bool:
    """Does this page actually claim to be this company?

    A live page proves a domain resolves, nothing more. Parked pages, squatters
    and unrelated businesses all answer on the obvious name, so acceptance
    requires the company's own words in the page's own identity fields.
    """
    if not html or _parked(html):
        return False
    # A recorded alias is a person's answer to this exact question, so it is
    # checked as a name in its own right. This is the ONLY thing that loosens
    # the check, and it only loosens it for a company somebody has looked at:
    # "EagleView Technologies" fails on a page that says "Eagleview", and goes
    # on failing until a human says those are the same company.
    for alt in (aliases or []):
        if alt and alt.strip().lower() != (name or "").strip().lower():
            if identifies(html, alt, base):
                return True
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
    # An exact domain match is evidence in its own right. adobe.com for "Adobe"
    # is a different kind of claim than frame.com for "A-Frame Solutions": the
    # first is the whole name, the second is a fragment of it. Short names fail
    # the generic-word guard below and can otherwise never be resolved at all,
    # which left ADP, Adobe and Auror permanently websiteless.
    if base and base == "".join(tk) and all(norm(t) in ident_n for t in tk):
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


def short_name(name: str) -> bool:
    """Is this name too short to identify a company on its own?

    A one-word name under six characters matches whatever else holds the
    domain: kodex.* is a Samsung ETF brand, band.us is a group-chat app,
    blitz.com is not this company. The page really does say the word, so no
    amount of text matching separates them - only a person can. These get
    queued instead of written, because a wrong website is the key discovery
    uses to find a job board, and would attribute another company's postings
    to a govtech company.
    """
    tk = tokens(name)
    return len(tk) == 1 and len(tk[0]) < 6


def probe(company: dict) -> dict:
    name = company.get("name", "")
    for url in candidates(name):
        base = url.split("//", 1)[1].rsplit(".", 1)[0]
        try:
            r = ats._get(url)
        except Exception:
            continue
        if identifies(r.text, name, base):
            return {"id": company["id"], "url": str(r.url).rstrip("/"),
                    "note": "page identifies the company",
                    "review": short_name(name)}
    return {"id": company["id"], "url": None, "note": "no candidate domain identified it",
            "review": False}


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

    queued = [r for r in results if r["url"] and r.get("review")]
    if queued:
        rp = DATA / "website_review.json"
        pending = json.loads(rp.read_text()) if rp.exists() else {}
        names = {c["id"]: c["name"] for c in companies}
        for r in queued:
            pending[r["id"]] = {"name": names.get(r["id"]), "url": r["url"],
                                "found_on": dt.date.today().isoformat()}
        rp.write_text(json.dumps(pending, indent=1) + "\n")
        print(f"\n{len(queued)} short-name match(es) queued in data/website_review.json "
              f"for a person to confirm, not written:")
        for r in queued:
            print(f"   {names.get(r['id'], r['id']):<26} {r['url']}")

    hits = [r for r in results if r["url"] and not r.get("review")]
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

def identity_note(html: str, name: str, base: str | None = None,
                  aliases: list | None = None) -> dict:
    """Explain the identity check, rather than only passing or failing it.

    identifies() is deliberately strict and stays that way: it is the only
    thing between a squatter and the dataset, and a false yes is far worse
    than a false no. But its "no" covers two very different situations, and
    telling them apart is a person's job, not a regex's:

      EagleView Technologies / eagleview.com, titled
        "Geospatial Intelligence, Aerial Imagery and Data | Eagleview"
      Acme Software Systems / acme.com, titled
        "Acme Plumbing - Boston"

    Both are "the page says our first word and not the rest". The first is a
    company using a shorter brand name; the second is a different business
    that got the obvious domain. Nothing in the page distinguishes them, so
    the honest move is to hand over what matched and what did not, and say
    which words are missing.
    """
    tk = tokens(name)
    ident = " ".join(
        html_lib.unescape(re.sub(r"<[^>]+>", " ", m))
        for pat in (TITLE, META, H1)
        for m in pat.findall(html or "")[:3])
    ident_n = norm(ident)
    present = [t for t in tk if norm(t) in ident_n]
    missing = [t for t in tk if norm(t) not in ident_n]
    return {
        "ok": identifies(html, name, base, aliases),
        "present": present,
        "missing": missing,
        "domain_is_lead": bool(base and present and base == "".join(
            tk[:len(present)]) and tk[:len(present)] == present),
        "says": ident.strip()[:120],
    }

# A company name is a few words. Everything below refuses anything that is not
# shaped like one, because the alias it produces is written into the dataset and
# then used to match pages forever after. A 286-character marketing paragraph
# got recorded as a company alias once; these are the guards that stop it.
NAME_MAX_CHARS = 60
NAME_MAX_WORDS = 6
_GENERIC_NAME = re.compile(
    r"^(home|welcome|about|contact|careers?|jobs?|index|untitled|homepage|"
    r"our (website|company)|menu|loading)$", re.I)


def plausible_name(s: str) -> bool:
    s = (s or "").strip()
    if not (2 <= len(s) <= NAME_MAX_CHARS):
        return False
    if len(s.split()) > NAME_MAX_WORDS:
        return False
    if _GENERIC_NAME.match(s.replace(".", "")):
        return False
    # a sentence, not a name
    if re.search(r"[.!?]\s+\S", s) or s.endswith((",", ".", ":", ";")):
        return False
    # marketing prose gives itself away with these
    if re.search(r"\b(we|our|your|is|are|helps?|ensures?|provides?)\b", s, re.I):
        return False
    return True


_SIGNALS = [
    # (regex, source label, how much to trust it)
    (r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)',
     "the site's own og:site_name", 100),
    (r'<meta[^>]+name=["\']application-name["\'][^>]+content=["\']([^"\']+)',
     "the site's application-name", 90),
    (r'<meta[^>]+name=["\']apple-mobile-web-app-title["\'][^>]+content=["\']([^"\']+)',
     "the name it uses on a phone home screen", 85),
    (r'"@type"\s*:\s*"Organization".{0,400}?"name"\s*:\s*"([^"]{2,60})"',
     "its schema.org Organization name", 95),
    (r'<img[^>]+(?:logo|brand)[^>]*\salt=["\']([^"\']{2,60})["\']',
     "the alt text on its logo", 60),
]


def name_candidates(html: str, base: str | None = None) -> list[dict]:
    """What this page calls itself, ranked, so nobody has to retype it.

    Asking a person to transcribe the name off a page they are already looking
    at is busywork, and busywork is where a marketing sentence ends up in a
    name field. The page nearly always states its own name in a machine
    readable place - og:site_name exists precisely to answer "what is this
    site called" - so read it, and only ask when the page genuinely does not
    say.
    """
    if not html:
        return []
    out, seen = [], set()

    def add(value, why, score):
        v = html_lib.unescape(re.sub(r"\s+", " ", value or "")).strip(" -|·—")
        if not plausible_name(v) or v.lower() in seen:
            return
        seen.add(v.lower())
        out.append({"name": v, "why": why, "score": score})

    for pat, why, score in _SIGNALS:
        for m in re.findall(pat, html, re.I | re.S)[:2]:
            add(m, why, score)

    # title segments, preferring the one the domain corroborates
    m = TITLE.search(html)
    if m:
        title = html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        for seg in re.split(r"\s*[|·—–]\s*|\s+-\s+", title):
            seg = seg.strip()
            squashed = re.sub(r"[^a-z0-9]", "", seg.lower())
            backed = bool(base and squashed and base in squashed)
            add(seg, "the part of the page title the domain backs up" if backed
                     else "a piece of the page title", 80 if backed else 40)

    out.sort(key=lambda c: -c["score"])
    return out[:5]
