#!/usr/bin/env python3
"""Confirm who each supplier is - step 1 of moving the suppliers to SLED HQ.

The owner (2026-10-02): move the suppliers onto SLED HQ and sort them in
detail. Sorting needs to know WHO each one is first, and the 7,919 records
mostly cannot say: 5,474 have no website, nearly every description is one line
off a conference listing, and two listings can be one company. So this answers
three questions per supplier, and only these three:

  1. Is it a DUPLICATE - of another supplier, or of a company already on the
     board?
  2. Where none is on file, can a website be FOUND - a domain guessed from
     the name and accepted only when the page's own identity fields name the
     company (find_websites.identifies, the same strict door the board uses;
     a live page proves only that a domain resolves).
  3. Where one is on file, is it THEIRS?

Every answer is said as what we managed to read, never as what the company
is: "no candidate domain named them" is not "they have no website", and a site
that would not answer is "could not read", not "not theirs".

MEASURED, NOT ASSUMED. The first full run (2026-10-03) was audited by four
independent reviewers over a 214-record random sample, each opening the pages
first-hand. What they found rewrote four verdicts, and each rule below says
which finding it answers:

  - "names somebody else" was right 1 time in 25: the site on file was theirs
    under an acronym, a brand or a parent (PGW, VC3, Ovivo, Delta Pride). It is
    `unconfirmed` now - likely theirs, said as unconfirmed.
  - "no website found" was right 0 times in 25: 21 had a findable site, many
    on an acronym or a .org (nrtcca.org, aspph.org, policeforum.org), one only
    on www., one under the first part of its name ("McGriff, A Marsh &
    McLennan Agency"). The guesser now tries those shapes - and every guess
    still has to be named by the page.
  - a record off an association's own site was a menu item 18 times in 20
    (`listing_menu`); only a name that reads like a company is searched.
  - "unreadable" was a dead deep link on a live site 6 times in 15; the home
    page is tried before saying so.
  - found sites were wrong 5 times in 30: a parked lander behind a ww547
    redirect, a GoDaddy "Launching Soon" page, and same-named businesses
    (charityengine.com is a volunteer-computing project). The lander shapes are
    refused; a page that says nothing of what the record says they sell goes
    to review.
  - duplicates were wrong 5 times in 25: two agencies on one .gov host, a
    division on its parent's domain (Aramark Collegiate Hospitality), a wrong
    website on file, two companies sharing a name. A shared host now needs
    near-identical names, and a name match needs websites that agree.

IT WRITES ONE FILE, data/supplier_identity.json - a staged finding with its
evidence. It never writes suppliers.json: landing a website on a record is a
separate step a person rules on (agents propose, people rule).

    python3 scripts/supplier_identity.py --sample 200      # measure first
    python3 scripts/supplier_identity.py --ids a,b,c       # just these
    python3 scripts/supplier_identity.py --write            # everything
    python3 scripts/supplier_identity.py --stats            # what the file says
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import html as html_lib
import json
import pathlib
import random
import re
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import find_websites as fw  # noqa: E402

OUT = "supplier_identity.json"
RETRY_DAYS = 60

# What each verdict means, in the words the report and SLED HQ will use. A
# verdict is about what we READ, never a claim about the company.
VERDICTS = {
    "theirs": "the website on file names them",
    "unconfirmed": "the website on file answers but does not state their full name - "
                   "usually an acronym, brand or parent; likely theirs, not confirmed",
    "found": "no website was on file; a guessed domain names them",
    "found_review": "a guessed domain names them, but a person should confirm it",
    "parked": "the website on file is a for-sale or holding page",
    "unreadable": "the website on file did not answer our crawler, nor did its home page - "
                  "most are bot walls that refuse an identified crawler (by house rule it "
                  "never poses as a browser), so the site usually exists",
    "not_found": "no website on file, and no guessed domain named them or let our crawler "
                 "read it - NOT a finding that they have no website",
    "duplicate": "the same company as another record (see duplicate_of)",
    "related": "on the same website as another record and named like it - the same "
               "company or a division of it; a person decides, never merged",
    "not_a_company": "the name is not a company: an address, a number or site navigation",
    "listing_menu": "a page off an association's own site, most likely a menu item",
    "listing_site": "the website on file is the association page that listed them, "
                    "and no site of their own was found",
}


def host(url: str | None) -> str:
    if not url:
        return ""
    u = url if "//" in url else "https://" + url
    h = urllib.parse.urlsplit(u).netloc.lower().split(":")[0]
    return h.removeprefix("www.")


def root(h: str) -> str:
    """The registrable part, roughly: the last two labels, three for a
    country second level (co.uk). Good enough to say two records share a
    site; never used to build a URL."""
    parts = [p for p in h.split(".") if p]
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac") \
            and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def public_sector(h: str) -> bool:
    """A government or campus host carries many unrelated offices: ct.gov
    holds both the emergency-management division and Correctional
    Enterprises. Sharing one says nothing about being one."""
    return bool(re.search(r"\.(gov|mil|edu)$|\.(state|k12|ci|co)\.[a-z]{2}\.us$|\.gov\.[a-z]{2}$",
                          h or ""))


# Sites many unrelated listings point at - a marketplace, a social profile,
# a site builder. Two suppliers sharing one of these share nothing.
SHARED_HOSTS = {"linkedin.com", "facebook.com", "google.com", "sites.google.com",
                "wixsite.com", "squarespace.com", "godaddysites.com",
                "business.site", "amazon.com", "youtube.com", "instagram.com",
                "x.com", "twitter.com", "yelp.com", "bbb.org", "github.io",
                "brandbucket.com"}


# WHAT A HARVESTED DIRECTORY LEAVES BEHIND. Association sites were swept as
# exhibitor lists and their menus came along: every one of the 57 records on
# ccm-ct.org and the 31 on ncsea.org is a menu item ("Advocacy", "Career
# Center", "2026 CCM Convention"). Only EXACT shapes are refused here - the
# exhibitor sweep's anchored patterns, a bare address, a bare number. Its
# broader rules (a name starting with a verb or "The", one containing " / ")
# are right for a page being harvested and wrong for a record already kept:
# they would refuse "The Home Depot" and "Neptune Benson / Xylem".
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
NUMBER = re.compile(r"^\s*(booth\s*#?\s*)?[#\d][\d\s#-]*$", re.I)


def clean_name(name: str) -> str:
    """The company part of a listed name. "Flood Mitigation Industry
    Association - Roderick Scott - <address>" is a real association with a
    contact glued on; the auditor was right that refusing it as "not a
    company" lost it."""
    n = ADDRESS.sub(" ", name or "")
    n = re.sub(r"\s+-\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s*(-\s*)?$", " ", n)   # " - First Last"
    return re.sub(r"\s+", " ", n).strip(" -|,")


def junk_reason(name: str) -> str | None:
    import sweep_exhibitors as sw
    raw = sw.clean(name or "")
    if not raw:
        return "the record has no name"
    if ADDRESS.search(raw) and len(re.sub(r"[^A-Za-z]", "", clean_name(raw))) < 3:
        return "the name is an email address"
    n = clean_name(raw)
    if NUMBER.match(n):
        return "the name is a number or booth number"
    for rx in (sw.NAV, sw.TIER, sw.SECTIONS, sw.JUNK):
        if rx.search(n):
            return "the name is site navigation"
    return None


def listing_hosts(suppliers: list) -> set:
    """Hosts that are a LISTING, not a company's site: shared by three or more
    records, most of whose names do not carry the host's own name. misheriff.org
    holds 68 records and names none of them; cgi.com holds three and they are
    all CGI - one company listed three times, a duplicate, not a listing."""
    by: dict = {}
    for s in suppliers:
        r = root(host(s.get("website")))
        if r:
            by.setdefault(r, []).append(s)
    out = set()
    for r, rows in by.items():
        if len(rows) < 3:
            continue
        label = re.sub(r"[^a-z0-9]", "", r.split(".")[0])
        carry = sum(1 for s in rows if label and label in name_key(s.get("name")))
        if carry * 2 < len(rows):
            out.add(r)
    return out


# A name that reads like an organisation rather than a page. Off an
# association's site, only these are searched: 18 of 20 such records in the
# audit were menu items ("Opioid Settlement Resources", "Attendee Toolkit").
COMPANY_MARK = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|corp|corporation|co|company|companies|group|gmbh|"
    r"s\.?a|a/?s|ag|b\.?v|plc|pllc|llp|lp|pc|p\.c|associates|partners|"
    r"university|college|institute|foundation|industries|manufacturing|mfg|"
    r"technologies|technology|systems|solutions|services|labs|bank|insurance|"
    r"consulting|engineers|engineering|architects|enterprises|international)\b\.?",
    re.I)


def reads_like_a_company(name: str) -> bool:
    return bool(COMPANY_MARK.search(name or ""))


def name_key(name: str) -> str:
    """A name with legal suffixes and punctuation gone. "Acme, Inc." and
    "ACME Inc" are one key; "Acme Systems" is another (STOP words stay -
    they are what separates two real companies)."""
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
             if w and w not in fw.LEGAL]
    return "".join(words)


def duplicates(suppliers: list, companies: list, listing: set | None = None) -> dict:
    """{supplier id: {"of": id, "on_board": bool, "why": str}}.

    The first record of a group (by id) stands; the rest point at it. A
    supplier that matches a company on the board points at the company -
    the board's record is the one with research behind it.

    A SHARED SITE IS NOT ONE COMPANY by itself: a division sits on its
    parent's domain, a .gov host carries a dozen agencies, a wrong website on
    file lands on somebody else's. So a shared host counts only with the same
    name key. A SHARED NAME is not one company either - goSafe and GoSafe are
    two businesses - so a name match counts only when the websites, where
    both are on file, agree."""
    out: dict = {}
    by_host: dict = {}
    by_name: dict = {}
    by_site: dict = {}             # host -> [(key, kind, id)] for "related"
    skip = SHARED_HOSTS | (listing if listing is not None else listing_hosts(suppliers))

    def usable(h):
        return h and h not in skip and not public_sector(h)
    for c in companies:
        r = root(host(c.get("website")))
        k = name_key(c.get("name"))
        if usable(r) and len(k) >= 4:
            by_host.setdefault((r, k), ("board", c["id"], r))
            by_site.setdefault(r, []).append((k, "board", c["id"]))
        if len(k) >= 4:
            by_name.setdefault(k, ("board", c["id"], r))
    seen_ids = set()
    for s in sorted(suppliers, key=lambda x: x["id"]):
        if s["id"] in seen_ids:
            continue
        seen_ids.add(s["id"])
        if junk_reason(s.get("name")):
            continue                      # not a company: nothing to be a copy of
        r = root(host(s.get("website")))
        k = name_key(clean_name(s.get("name")))
        hit, why = None, ""
        if usable(r) and len(k) >= 4 and (r, k) in by_host:
            hit, why = by_host[(r, k)], f"same name and website ({r})"
        elif len(k) >= 4 and k in by_name:
            other = by_name[k]
            if not (usable(r) and usable(other[2]) and r != other[2]):
                hit, why = other, "same name" + (f" and website ({r})" if r and r == other[2] else "")
        if hit:
            out[s["id"]] = {"of": hit[1], "on_board": hit[0] == "board", "why": why}
            continue
        # SAME SITE, NAMED LIKE IT: "CGI Transcend" on cgi.com, "Aramark
        # Collegiate Hospitality" on aramark.com. The audit called the first
        # a duplicate and the second a division; nothing in the record tells
        # them apart, so neither is merged - a person decides.
        if usable(r) and len(k) >= 3:
            for k2, kind, oid in by_site.get(r, []):
                if k2 and (k2 in k or k in k2):
                    out[s["id"]] = {"of": oid, "on_board": kind == "board", "relation": "related",
                                    "why": f"same website ({r}) and one name contains the other"}
                    break
            if s["id"] in out:
                continue
        if usable(r) and len(k) >= 4:
            by_host.setdefault((r, k), ("supplier", s["id"], r))
            by_site.setdefault(r, []).append((k, "supplier", s["id"]))
        if len(k) >= 4:
            by_name.setdefault(k, ("supplier", s["id"], r))
    return out


def fetch(url: str):
    """A live fetch, never the HTTP cache. A cached 304 knows only the
    address that was asked for, not where it landed: maddiefund.com,
    ezdock.com and projectlifesaver.net all redirect to the company's real
    site, and from the cache they were recorded as the lookalike."""
    import ats
    ats.HTTP_CACHE = None
    return ats._get(url)


# A PARKING SERVICE'S LANDER, which find_websites._parked does not know: a
# numbered www-subdomain carrying a token ("ww547.miraclerecreation.com/?tkn=")
# and the registrar's "launching soon" holding page. Both answered the
# identity check by repeating the domain's own words.
LANDER_URL = re.compile(r"//ww\d+\.|[?&]tkn=", re.I)
HOLDING = re.compile(r"launching soon|future home of|website coming soon|"
                     r"this site is under construction|domain (is )?parked", re.I)


def _identity_text(html: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", m))
                    for pat in (fw.TITLE, fw.META, fw.H1)
                    for m in pat.findall(html or "")[:3])


def is_lander(url: str, html: str) -> bool:
    return bool(LANDER_URL.search(url or "")) or bool(HOLDING.search(_identity_text(html)))


# WHAT THEY SELL, checked against what the page says. "Charity Engine" the
# fundraising CRM and charityengine.com the volunteer-computing project share
# a name and nothing else; the record's own description is the only thing
# that can tell them apart. Words that say nothing about a business are left
# out, and a description with fewer than two telling words is not checked -
# most of these are "exhibited at NIGP 2026" and say nothing.
DESC_NOISE = {"exhibited", "exhibitor", "equipment", "components", "services",
              "service", "association", "media", "products", "product",
              "solutions", "company", "companies", "systems", "provider",
              "providers", "distributor", "reseller", "engineering", "consulting",
              "non-tech", "including", "government", "public", "local", "state",
              "their", "which", "other", "about", "through", "across", "agencies"}


def telling_words(description: str) -> list[str]:
    d = re.sub(r"\b(?:-\s*)?exhibited at\b.*$", " ", description or "", flags=re.I)
    words = re.findall(r"[a-z]{5,}", d.lower())
    return [w for w in dict.fromkeys(words) if w not in DESC_NOISE]


def describes(html: str, description: str) -> bool | None:
    """True when the page mentions what the record says they do, False when
    it mentions none of it, None when the record says too little to check."""
    words = telling_words(description)
    if len(words) < 2:
        return None
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html or "")).lower()
    stems = [w[:6] for w in words]
    return any(st in text for st in stems)


def check_own(s: dict, get=fetch) -> dict:
    """Is the website on file theirs?"""
    url = s["website"]
    tried_root = False
    try:
        r = get(url)
        failed = None if getattr(r, "status_code", 200) < 400 else f"HTTP {r.status_code}"
    except Exception as exc:                            # noqa: BLE001
        r, failed = None, f"{type(exc).__name__}: {str(exc)[:120]}"
    if failed:
        # A DEAD DEEP LINK IS NOT A DEAD SITE. Six of 15 "unreadable" in the
        # audit answered at their home page.
        # And an http:// address whose site answers only on https
        # (beacon.org, hungerfordterry.com time out on port 80).
        scheme = urllib.parse.urlsplit(url if "//" in url else "https://" + url).scheme or "https"
        homes = [f"{scheme}://{host(url)}"] + ([f"https://{host(url)}"] if scheme == "http" else [])
        for home in homes:
            if not host(url) or home.rstrip("/") == url.rstrip("/"):
                continue
            tried_root = True
            try:
                r2 = get(home)
                if getattr(r2, "status_code", 200) < 400:
                    r, failed = r2, None
                    break
            except Exception:                           # noqa: BLE001
                pass
    if failed:
        return {"verdict": "unreadable", "url": url, "why": failed,
                **({"home_tried": True} if tried_root else {})}
    html = getattr(r, "text", "") or ""
    final = str(getattr(r, "url", url) or url).rstrip("/")
    ev = {"url": url, "final": final}
    if tried_root:
        ev["deep_link_dead"] = True
    if fw._parked(html) or is_lander(final, html):
        return dict(ev, verdict="parked", why="for-sale or holding page")
    base = host(final).rsplit(".", 1)[0]
    name = clean_name(s.get("name", ""))
    note = fw.identity_note(html, name, base, s.get("also_known_as"))
    ev.update(says=note["says"], present=note["present"], missing=note["missing"])
    if host(final) and root(host(final)) != root(host(url)):
        ev["moved_to"] = host(final)
    if note["ok"]:
        if describes(html, s.get("description", "")) is False:
            # names them, sells something else: a same-named business
            # (azimuth.co.nz, clearflow.net in the audit)
            return dict(ev, verdict="unconfirmed",
                        why="the page names them but mentions nothing the record says they sell")
        return dict(ev, verdict="theirs")
    names = [n["name"] for n in fw.name_candidates(html, base)[:2]]
    return dict(ev, verdict="unconfirmed", page_names=names,
                why="the page does not state their full name")


SMALL = {"of", "and", "for", "the", "a", "an", "&", "at", "in", "on", "to"}
ORG_WORD = re.compile(r"\b(association|foundation|institute|society|council|forum|"
                      r"network|coalition|league|alliance|federation|academy|"
                      r"commission|board|center|centre|program|project)\b", re.I)
EDU_WORD = re.compile(r"\b(university|college|school)\b", re.I)


def name_parts(name: str) -> list[str]:
    """The listed name, then its parts: "McGriff, A Marsh & McLennan Agency"
    is McGriff; "Syrinx / EnPhytoBox" is either; "(ASPPH)" is an acronym the
    record hands over."""
    n = clean_name(name)
    parts = [n]
    for m in re.findall(r"\(([^)]{2,40})\)", n):
        parts.append(m)
    bare = re.sub(r"\([^)]*\)", " ", n)
    for piece in re.split(r"\s*/\s*|\s+\|\s+|\s+-\s+|,\s+an?\s+|\s+dba\s+|\s+d/b/a\s+", bare,
                          flags=re.I):
        piece = piece.strip(" ,")
        if piece and piece.lower() != n.lower():
            parts.append(piece)
    return list(dict.fromkeys(p for p in parts if re.search(r"[A-Za-z]{2}", p)))


def guesses(name: str) -> list[tuple[str, str]]:
    """(url, the name the page must state) pairs, most likely first.

    find_websites.candidates for the whole name, then the shapes the audit
    found it missing. EVERY guess keeps the strict door: an acronym domain
    must still carry the FULL name in its own title (nrtcca.org does), and a
    part of the name must be named by the page as that part - and goes to a
    person, because a fragment can belong to anybody."""
    out: list = []
    full = clean_name(name)
    tlds = ((".edu", ".org", ".com") if EDU_WORD.search(full)
            else (".org", ".com", ".net") if ORG_WORD.search(full) else (".com", ".org", ".net"))
    for url in fw.candidates(full):
        out.append((url, full))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", re.sub(r"\([^)]*\)", " ", full)) if w]
    sig = [w for w in words if w.lower() not in SMALL and w.lower() not in fw.LEGAL]
    if len(sig) >= 3:
        acro = "".join(w[0] for w in sig).lower()
        if 3 <= len(acro) <= 8:
            out += [(f"https://{acro}{t}", full) for t in tlds[:2]]
    if len(sig) >= 2:
        fl = (sig[0] + sig[-1]).lower()
        out += [(f"https://{fl}{t}", full) for t in tlds[:2]]
    for part in name_parts(full)[1:]:
        tk = fw.tokens(part)
        if not tk:
            continue
        joined = "".join(tk)
        if len(joined) >= 3:
            out += [(f"https://{joined}{t}", part) for t in tlds[:2]]
    # ONE ADDRESS, EVERY NAME IT WAS GUESSED FOR. mcgriff.com is the full
    # name's first-word guess AND the part "McGriff"'s own; deduping by
    # address kept only the first, the full name failed on a page titled
    # "McGriff", and the part was never asked.
    seen: dict = {}
    for url, must in out:
        seen.setdefault(url, [])
        if must not in seen[url]:
            seen[url].append(must)
    return [(u, ms) for u, ms in list(seen.items())[:18]]


def find_site(s: dict, get=fetch) -> dict:
    """No website on file: guess domains, accept only one that names them."""
    name = clean_name(s.get("name", ""))
    tried = 0
    plan = guesses(name)
    # THE www. RETRY IS A SECOND PASS. Run on every guess, it let an earlier
    # guess answer first (pavementmanagement.com before the group's own
    # domain) and found sites fell from 25 of 30 to 22. Only when no apex
    # named them is each guess tried again with www. (centrisys-cnp.com
    # answers only there).
    failed: set = set()
    for url, musts in plan + [(u.replace("://", "://www.", 1), m) for u, m in plan]:
        if "://www." in url and url.replace("://www.", "://", 1) not in failed:
            continue                  # the apex answered; www. adds nothing
        base = url.split("//", 1)[1].removeprefix("www.").rsplit(".", 1)[0]
        tried += 1
        try:
            r = get(url)
        except Exception:                               # noqa: BLE001
            failed.add(url)
            continue
        if getattr(r, "status_code", 200) >= 400:
            continue
        html = getattr(r, "text", "") or ""
        final = str(getattr(r, "url", url) or url).rstrip("/")
        if is_lander(final, html):
            continue
        must = next((m for m in musts
                     if fw.identifies(html, m, base, s.get("also_known_as"))), None)
        if must is None:
            continue
        note = fw.identity_note(html, must, base)
        row = {"url": final, "says": note["says"], "tried": tried}
        fragment = must != name
        if fragment:
            return dict(row, verdict="found_review", matched_on=must,
                        why="the page names part of their listed name")
        if fw.short_name(name):
            return dict(row, verdict="found_review", why="the name is too short to be sure")
        if describes(html, s.get("description", "")) is False:
            return dict(row, verdict="found_review",
                        why="the page names them but mentions nothing the record says they sell")
        return dict(row, verdict="found")
    return {"verdict": "not_found", "tried": tried,
            "why": "no guessed domain named them" if tried else "the name gives nothing to guess"}


def check(s: dict, get=fetch, listing: set = frozenset()) -> dict:
    r = root(host(s.get("website")))
    if s.get("website") and r in listing:
        # The site on file is where they were LISTED. A name that reads like
        # an organisation is searched for its own site; anything else off an
        # association's own site was a menu item 18 times in 20.
        if reads_like_a_company(s.get("name", "")) and len(fw.tokens(clean_name(s.get("name", "")))) >= 2:
            row = find_site(s, get)
            if row["verdict"] == "not_found":
                row = dict(row, verdict="listing_site")
            elif row["verdict"] == "found":
                row = dict(row, verdict="found_review",
                           why="the name came off an association site that also yielded menu items")
        else:
            row = {"verdict": "listing_menu", "tried": 0,
                   "why": "off an association's own site and does not read like a company"}
        row["listing_host"] = r
    else:
        row = check_own(s, get) if s.get("website") else find_site(s, get)
    row["checked_on"] = dt.date.today().isoformat()
    return row


def _scrub(row: dict) -> dict:
    """No address leaves this script: a page title can carry one, and the
    findings file may be committed to a public repository."""
    for k, v in list(row.items()):
        if isinstance(v, str):
            row[k] = ADDRESS.sub("[address]", v)
        elif isinstance(v, list):
            row[k] = [ADDRESS.sub("[address]", x) if isinstance(x, str) else x for x in v]
    return row


def run(suppliers: list, companies: list, prior: dict, *, workers: int = 12,
        get=fetch, progress=None, all_suppliers: list | None = None) -> dict:
    """{id: row} for every supplier given. Duplicates are settled offline
    first and never fetched; a supplier checked within RETRY_DAYS keeps its
    row."""
    every = all_suppliers if all_suppliers is not None else suppliers
    listing = listing_hosts(every)
    dups = duplicates(every, companies, listing)
    today = dt.date.today()
    rows: dict = {}
    todo = []
    seen = set()
    for s in suppliers:
        if s["id"] in seen:
            continue                    # two records under one id: the first is checked
        seen.add(s["id"])
        jr = junk_reason(s.get("name"))
        if jr:
            rows[s["id"]] = {"verdict": "not_a_company", "why": jr,
                             "checked_on": today.isoformat()}
            continue
        if s["id"] in dups:
            rows[s["id"]] = {"verdict": dups[s["id"]].get("relation", "duplicate"),
                             "duplicate_of": dups[s["id"]]["of"],
                             "on_board": dups[s["id"]]["on_board"],
                             "why": dups[s["id"]]["why"],
                             "checked_on": today.isoformat()}
            continue
        old = prior.get(s["id"])
        try:
            fresh = old and (today - dt.date.fromisoformat(old["checked_on"])).days < RETRY_DAYS \
                and old.get("verdict") in VERDICTS \
                and old.get("verdict") not in ("duplicate", "related", "not_a_company") and \
                old.get("website_was") == s.get("website")
        except (KeyError, ValueError, TypeError):
            fresh = False
        if fresh:
            rows[s["id"]] = old
        else:
            todo.append(s)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check, s, get, listing): s for s in todo}
        for i, f in enumerate(cf.as_completed(futs), 1):
            s = futs[f]
            try:
                row = f.result()
            except Exception as exc:                    # noqa: BLE001
                row = {"verdict": "unreadable", "why": f"crashed: {type(exc).__name__}",
                       "checked_on": today.isoformat()}
            row["website_was"] = s.get("website")
            rows[s["id"]] = _scrub(row)
            if progress and i % 100 == 0:
                progress(i, len(todo))
    for s in suppliers:
        rows[s["id"]].setdefault("website_was", s.get("website"))
    return rows


def summary(rows: dict) -> collections.Counter:
    return collections.Counter(r.get("verdict") for r in rows.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", type=int, help="check a random sample of this many")
    ap.add_argument("--ids", help="check just these ids (comma separated)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--fresh", action="store_true", help="ignore rows already on file")
    ap.add_argument("--dump", help="write the rows of a sample or --ids run here")
    ap.add_argument("--write", action="store_true", help="write data/supplier_identity.json")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    import admin
    stored = admin.read(OUT, {})
    prior = stored.get("rows") if isinstance(stored.get("rows"), dict) else {}
    if a.stats:
        c = summary(prior)
        print(f"{len(prior)} suppliers checked (as of {stored.get('generated')})")
        for k, n in c.most_common():
            print(f"  {n:>5}  {k:13} {VERDICTS.get(k, '')}")
        return 0
    suppliers = admin.read("suppliers.json", [])
    companies = admin.read_companies()
    pick = suppliers
    partial = bool(a.sample or a.ids)
    if a.ids:
        want = set(a.ids.split(","))
        pick = [s for s in suppliers if s["id"] in want]
    elif a.sample:
        rnd = random.Random(a.seed)
        pick = rnd.sample(suppliers, min(a.sample, len(suppliers)))
    t0 = time.time()
    rows = run(pick, companies, {} if (partial or a.fresh) else prior, workers=a.workers,
               all_suppliers=suppliers,
               progress=lambda i, n: print(f"  {i}/{n} checked, {time.time() - t0:.0f}s",
                                           flush=True))
    took = time.time() - t0
    c = summary(rows)
    print(f"\n{len(rows)} suppliers in {took:.0f}s")
    for k, n in c.most_common():
        print(f"  {n:>5}  {n * 100 // max(len(rows), 1):>3}%  {k:13} {VERDICTS.get(k, '')}")
    ids = collections.Counter(s["id"] for s in suppliers)
    shared = sorted(i for i, n in ids.items() if n > 1)
    if shared and not partial:
        print(f"\n{len(shared)} id(s) carried by more than one record: {', '.join(shared[:8])}")
    if a.dump:
        pathlib.Path(a.dump).write_text(json.dumps(rows, indent=1))
    if a.sample:
        print(f"\nprojected for all {len(suppliers)}: about "
              f"{took * len(suppliers) / max(len(pick), 1) / 60:.0f} min")
    if not a.write:
        print("\n(nothing written: --write records data/supplier_identity.json)")
        return 0
    if a.sample:
        print("\n--write records the full run only; a sample is a measurement")
        return 1
    if a.ids:
        # a re-check of named records lands over their rows and nothing else
        merged = dict(prior)
        merged.update(rows)
        rows = merged
    admin.write_atomic(OUT, {"generated": dt.datetime.now().astimezone().isoformat(
        timespec="seconds"), "verdicts": VERDICTS, "shared_ids": shared, "rows": rows})
    print(f"\nwrote data/{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
