#!/usr/bin/env python3
"""Find the exhibitor directories behind the state and local chapter events.

    python3 scripts/find_event_directories.py --parents        # stage 1, look
    python3 scripts/find_event_directories.py --parents --write
    python3 scripts/find_event_directories.py --directories --write   # stage 2

THE PROBLEM. data/state_events.json holds 359 chapter conferences, every one
with an empty directory_url - and a conference with no exhibitor directory
cannot produce a single company. 130 of the 359 also carry a GENERATED
organisation name ("APWA {state} Chapter"), which the generator itself calls
scaffolding. So two things have to be found per event, and neither may be
guessed: where the organisation actually lives, and where its exhibitor list is.

WHERE THE URLS COME FROM, AND WHY NOT A SEARCH ENGINE. Every event carries a
parent_national - NLC, NRPA, APWA, NACo - and 24 of the 26 parents are already
on file in conferences.json with a URL. A national association LISTS ITS OWN
CHAPTERS: NLC keeps a state-league directory, APWA a chapter map, NRPA an
affiliate list. Reading that page hands over the chapter's real name and real
site, published by the body the chapter belongs to. That is a fact with a
source, which a domain guessed from a name is not.

STAGE 1 (--parents): for each parent, fetch its site, find the chapters /
affiliates / state-leagues page, and match each outbound link to an event by
state name. Writes org_name_confirmed and org_url onto the event.

STAGE 2 (--directories): for each event with an org_url, fetch it, find the
exhibit / sponsor / expo page one link away, fetch THAT, and ask
sweep_exhibitors' own harvest() and quality() whether it is a list of
companies. Writes directory_url and status.

WHAT IT RECORDS WHEN IT FAILS. `status` is one of needs_url, org_found,
directory_found, no_chapter_page, no_directory_link, not_a_directory. Each is
a different fact and none is written as another. An event this cannot resolve
is left exactly where it was, for a person.

Paced by ats.HOST_PAUSE. Stage 1 is ~50 requests; stage 2 is up to two per
event.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import pathlib
import re
import sys
import urllib.parse as up

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                              # noqa: E402
import sweep_exhibitors as sweep                        # noqa: E402

EVENTS = DATA / "state_events.json"

CHAPTER_LINK = re.compile(
    r"chapter|affiliate|state league|state municipal|state association|"
    r"section|regional|find your|our members|member (leagues|associations)|"
    r"state (partners|societies|organi[sz]ations)", re.I)
DIRECTORY_LINK = re.compile(
    r"exhibit|sponsor|expo|trade ?show|vendor|marketplace|solution center|"
    r"partner (directory|list)|who.?s exhibiting", re.I)

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}


def links(page: str, base: str) -> list[tuple[str, str]]:
    """Every outbound anchor, text and resolved href.

    THE HREF IS UNESCAPED BEFORE THE FRAGMENT IS CUT, and that ordering is
    the whole of NLC's 49 chapters. NLC writes its links entity-encoded -
    href="http&#x3A;&#x2F;&#x2F;www.akml.org" - and the first version
    excluded "#" from the href pattern to skip fragment links, so it matched
    "http&" and stopped dead on every one of them. 19 links read off a page
    carrying 49. Unescape first, then drop a real fragment.
    """
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', page, re.S | re.I):
        text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        href = html_lib.unescape(m.group(1)).split("#")[0].strip()
        if not href or not text:
            continue
        href = up.urljoin(base, href)
        if href.startswith("http"):
            out.append((text, href))
    return out


def anchors_with_offsets(page: str, base: str) -> list:
    """(text, href, position) for every outbound-shaped anchor."""
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', page, re.S | re.I):
        text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        href = html_lib.unescape(m.group(1)).split("#")[0].strip()
        if not href:
            continue
        href = up.urljoin(base, href)
        if href.startswith("http"):
            out.append((text, href, m.start()))
    return out


NEAR = 700          # characters between a state heading and its link


def link_near_state(page: str, base: str, state: str, skip) -> str | None:
    """The link that FOLLOWS a state's name on the page.

    Half the chapter listings do not put the state in the link. The National
    Sheriffs' Association writes the url as the link text -
    "https://www.calsheriffs.org/" under a California heading - and WEF
    labels every one of them "Web Site". Neither carries the word
    "California" or "Texas" where a text match can see it, and neither can be
    guessed from the domain: calsheriffs.org is California's and flsheriffs
    is Florida's, but so might a dozen other abbreviations be.

    Position is the evidence instead: the association printed the state, then
    printed its link. Bounded to {NEAR} characters so a state mentioned in
    prose three paragraphs up cannot claim an unrelated link.
    """
    hits = [m.start() for m in re.finditer(rf"\b{re.escape(state)}\b", page, re.I)]
    if not hits:
        return None
    best = None
    for text, href, pos in anchors_with_offsets(page, base):
        if skip(href):
            continue
        for h in hits:
            if 0 <= pos - h <= NEAR and (best is None or pos - h < best[0]):
                best = (pos - h, href)
    return best[1] if best else None


def fetch(url: str) -> str | None:
    try:
        return ats._get(url).text
    except Exception:                                   # noqa: BLE001
        return None


# THE ASSOCIATION'S OWN SITE, which is not its conference's site. NRPA's
# conference is at conference.nrpa.org and its affiliate list is at nrpa.org;
# IACP's conference has a domain of its own (theiacpconference.org) that says
# nothing about chapters. A first pass derived the root from the conference
# url and found no chapters page on 15 of 23 parents for exactly this reason
# - and matched "NSA" to nasact.org, because NSA is a substring of NASACT.
#
# So the parents are a table. These are the well-known domains of national
# associations, one line each, and they are looked up rather than inferred.
# A code not in the table is skipped and says so; it is not guessed.
PARENT_SITES = {
    "NLC": "https://www.nlc.org/", "NACO": "https://www.naco.org/",
    "NRPA": "https://www.nrpa.org/", "APWA": "https://www.apwa.org/",
    "AWWA": "https://www.awwa.org/", "WEF": "https://www.wef.org/",
    "SWANA": "https://swana.org/", "APTA": "https://www.apta.com/",
    "GFOA": "https://www.gfoa.org/", "NIGP": "https://www.nigp.org/",
    "GMIS": "https://www.gmis.org/", "IACP": "https://www.theiacp.org/",
    "NSA": "https://www.sheriffs.org/", "IAFC": "https://www.iafc.org/",
    "APCO": "https://www.apcointl.org/", "IAEM": "https://www.iaem.org/",
    "IIMC": "https://www.iimc.com/", "IAAO": "https://www.iaao.org/",
    "ICC": "https://www.iccsafe.org/", "APA": "https://www.planning.org/",
    "ASBO": "https://asbointl.org/", "NSBA": "https://www.nsba.org/",
    "COSN": "https://www.cosn.org/", "APHA": "https://www.apha.org/",
    "APHSA": "https://aphsa.org/", "EREPUBLIC": "https://www.govtech.com/",
}


# THE PAGE ON WHICH EACH PARENT LISTS ITS OWN CHAPTERS, looked up rather than
# inferred - the same rule PARENT_SITES follows above, and for the same
# reason. The first version of stage 1 took the FIRST link on the home page
# whose text matched /chapter|affiliate|section/ and walked it. That found
# apcointl.org/technology/spectrum for APCO, awwa.org/careercenter for AWWA,
# apha.org/membership for APHA, and nothing at all for ten parents whose home
# page carries no such link. 26 parents, 0 events resolved.
#
# Researched 2026-09-02 (one agent per association, each reading the
# association's own site). The wording differs per body and that is exactly
# why it cannot be guessed: AWWA has SECTIONS, WEF has MEMBER ASSOCIATIONS,
# NLC has STATE MUNICIPAL LEAGUES, APHA has AFFILIATES, NACo has STATE
# ASSOCIATIONS.
PARENT_LISTINGS = {
    # Already on file: this is the org_url_source recorded on all 21 APA rows
    # by the first pass, so it is an observation rather than a lookup.
    "APA": ("https://www.planning.org/chapters/", "reads in raw html"),
    "APCO": ("https://www.apcointl.org/community/chapters/",
        "reads in raw html"),
    "APHA": ("https://www.apha.org/about-apha/affiliates/state-and-regional-public-health-associations",
        "reads in raw html"),
    "APHSA": ("https://aphsa.org/state-human-services-organizations/",
        "JS-RENDERED - the names are not in the html a fetcher gets"),
    "APWA": ("https://www.apwa.org/connections-networking/apwa-chapters/find-an-apwa-chapter/",
        "reads in raw html"),
    "ASBO": ("https://www.asbointl.org/web/Web/About/Find_an_Affiliate.aspx",
        "reads in raw html"),
    "AWWA": ("https://www.awwa.org/local-sections/",
        "reads in raw html"),
    "COSN": ("https://www.cosn.org/membership-overview/cosn-chapters/",
        "reads in raw html"),
    "GFOA": ("https://www.gfoa.org/state-provincial-gfoa/sponsors",
        "reads in raw html"),
    "GMIS": ("https://www.gmis.org/page/StateChapters",
        "JS-RENDERED - the names are not in the html a fetcher gets"),
    "IAAO": ("https://www.iaao.org/membership/affiliates/",
        "reads in raw html"),
    "ICC": ("https://www.iccsafe.org/membership/chapters/icc-chapters-and-boardstaff-liaison-map/",
        "JS-RENDERED - the names are not in the html a fetcher gets"),
    "IIMC": ("https://www.iimc.com/152/Municipal-Clerks-Association-Websites",
        "JS-RENDERED - the names are not in the html a fetcher gets"),
    "NACO": ("https://www.naco.org/page/state-associations-affiliates-and-affinity-organizations",
        "reads in raw html"),
    "NIGP": ("https://www.nigp.org/directory/chapters",
        "reads in raw html"),
    "NLC": ("https://www.nlc.org/membership/state-municipal-leagues/",
        "reads in raw html"),
    "NRPA": ("https://www.nrpa.org/about-national-recreation-and-park-association/state-and-national-affiliates/",
        "reads in raw html"),
    "NSA": ("https://www.sheriffs.org/state-sheriffs-associations/",
        "reads in raw html"),
    "SWANA": ("https://swana.org/community/chapters/chapter-contacts",
        "reads in raw html"),
    "WEF": ("https://www.wef.org/membership--community/membership-center/wef-member-associations/ma-resource-center/wef-member-associations-contacts/",
        "reads in raw html"),
}

# NO STATE BODIES AT ALL, which is an answer and not a gap. Recorded so the
# search is not run again.
NO_CHAPTERS = {
    "APTA": "APTA (American Public Transportation Association) has no state chapters or affiliates of its own and publishes no list of state transit associations. ",
    "IAEM": "IAEM-USA has no state chapters. Its own IAEM-USA page (https://www.iaem.org/global/iaem-usa/) states the council is made up of 'IAEM-USA Membership Re",
}

# HAS STATE BODIES AND PUBLISHES NO LIST OF THEM. Also an answer: these need a
# person or another source, not another sweep of the same site.
NO_LISTING_PUBLISHED = {
    "EREPUBLIC": "e.Republic RUNS its state Digital Government Summits itself rather than through chapters, so there is no chapter listing to read. events.govtech.com is a webinar and event calendar; matching states against it produced one link to a webinar called 'Responsible AI in Government' filed as Indiana's summit. Needs its own matcher against the event calendar, not this one.",
    "IACP": "IACP definitely HAS state bodies — its Division of State Associations of Chiefs of Police (SACOP, https://www.theiacp.org/working-group/division/state",
    "IAFC": "IAFC's own site publishes no list of state fire chiefs associations. I pulled the raw HTML of the home page and extracted every internal link in its f",
    "NSBA": "NSBA definitely has state chapters - its own 'Our Members' page says 'Our members are state school boards associations and the U.S. territory of the V",
}


def on_parent_host(url: str, parent_site: str | None) -> bool:
    """Is this address the national body's own server, subdomains included?"""
    def host(u):
        return up.urlsplit(u or "").netloc.lower().replace("www.", "")
    here, parent = host(url), host(parent_site)
    return bool(parent) and (here == parent or here.endswith("." + parent))


def parent_sites() -> dict:
    events = json.loads(EVENTS.read_text())["events"]
    codes = sorted({e["parent_national"] for e in events if e.get("parent_national")})
    missing = [c for c in codes if c not in PARENT_SITES]
    if missing:
        print(f"  no site on file for parent(s) {missing}; skipped, not guessed")
    return {c: PARENT_SITES[c] for c in codes if c in PARENT_SITES}


def stage_parents(write: bool) -> int:
    """Give every event its organisation's real url, from the parent's own list.

    ONE FETCH PER PARENT, of a page looked up in PARENT_LISTINGS rather than
    guessed from the home page. The guessing version resolved 0 of 338.
    """
    doc = json.loads(EVENTS.read_text())
    events = doc["events"]
    todo = [e for e in events if not e.get("org_url")]
    codes = sorted({e.get("parent_national") for e in todo if e.get("parent_national")})
    print(f"{len(todo)} event(s) without an organisation url, "
          f"under {len(codes)} parent(s)\n")
    found = 0
    for code in codes:
        mine = [e for e in todo if e.get("parent_national") == code]
        entry = PARENT_LISTINGS.get(code)
        if not entry:
            why = (NO_CHAPTERS.get(code) or NO_LISTING_PUBLISHED.get(code)
                   or "no chapter listing on file")
            print(f"  {code:10} {'-':46} {len(mine):3} event(s) skipped: {why[:60]}")
            for e in mine:
                e["status"] = "no_chapter_listing"
                e["status_why"] = why
            continue
        listing, note = entry
        page = fetch(listing)
        if not page:
            print(f"  {code:10} {listing[:44]:46} the listing did not answer")
            continue
        home_host = up.urlsplit(listing).netloc.lower().replace("www.", "")
        list_path = up.urlsplit(listing).path.rstrip("/")
        outbound = []
        for txt, h in links(page, listing):
            host = up.urlsplit(h).netloc.lower().replace("www.", "")
            path = up.urlsplit(h).path
            if host != home_host or (list_path and path.startswith(list_path + "/")):
                if h.rstrip("/") != listing.rstrip("/"):
                    outbound.append((txt, h))
        hit = 0
        for e in mine:
            geo = (e.get("geo") or "").lower()
            state = next((s for s in STATES if re.search(rf"\b{s}\b", geo)), None)
            if not state:
                continue
            ab = STATES[state].lower()
            for txt, h in outbound:
                # A LOGIN PAGE IS NOT A CHAPTER SITE. myiacp.org/NC__Login
                # matched North Carolina on the two letters in its path and
                # became that chapter's "organisation url"; everything walked
                # from there led to IACP's national conference.
                if is_sign_in(up.urlsplit(h).path):
                    continue
                # NOR THE PARENT'S OWN SUBDOMAIN. awwa.org/local-sections
                # links to ace.awwa.org, the national conference; matching a
                # state to it filed AWWA's own event as the California-Nevada
                # Section's.
                if on_parent_host(h, PARENT_SITES.get(code)):
                    continue
                blob = f"{txt} {up.urlsplit(h).path}".lower()
                closed = re.sub(r"[^a-z0-9]+", "", f"{txt}{up.urlsplit(h).netloc}").lower()
                # NEVER AFTER A DOT, because that is a TLD and not a state.
                # NIGP's directory writes some links as the bare domain, so
                # "nigpabchapter.ca" matched California on the .ca of a
                # CANADIAN chapter - the NIGP Alberta Chapter, filed as
                # California. Same shape as the [A-Z]{2} bug that once put 24
                # postings in London, UK and Montreal, QB.
                if (re.search(rf"\b{state}\b", blob)
                        or state.replace(" ", "") in closed
                        or re.search(rf"(^|[^a-z.]){ab}([^a-z]|$)", blob)):
                    e["org_url"] = h
                    e["org_url_source"] = listing
                    e["org_name_observed"] = txt.strip()[:120]
                    e["status"] = "org_found"
                    hit += 1
                    break
            if not e.get("org_url"):
                # The state is on the page but not in the link. Position is
                # the evidence: the association printed the state, then its
                # link. Same skip rules - a login page is not a chapter site.
                near = link_near_state(
                    page, listing, state,
                    lambda h: (is_sign_in(up.urlsplit(h).path)
                               or up.urlsplit(h).netloc.lower().replace("www.", "")
                                  == home_host))
                if near:
                    e["org_url"] = near
                    e["org_url_source"] = listing
                    e["org_name_observed"] = f"(the link following '{state}' on the listing)"
                    e["status"] = "org_found"
                    hit += 1
        found += hit
        flag = "" if "reads in raw html" in note else "  (JS-RENDERED)"
        print(f"  {code:10} {listing[:44]:46} {len(outbound):3} outbound, "
              f"matched {hit}/{len(mine)}{flag}")
    print(f"\n  {found} event(s) now have an organisation url from their "
          f"parent's own listing")
    if write:
        _save(doc)
    else:
        print("  LOOKED ONLY. Re-run with --write.")
    return 0


# A LOGIN PAGE IS NOT A CHAPTER SITE, and these words do not sit tidily at a
# path segment's start. The url this rule exists for is myiacp.org/NC__Login,
# and a first version matched "/login" and let its own founding example
# straight through; NSA writes /s/Sign_In and /OnlineJoinMain.aspx. So the
# path is split on punctuation AND on camelCase, and the TOKENS are checked -
# which keeps "registered", "portalside" and "Joinville" out of it.
SIGN_IN_WORDS = {"login", "signin", "logon", "register", "account", "portal",
                 "store", "join", "signup"}


def _tokens(path: str) -> set:
    parts = re.split(r"[^A-Za-z]+", path)
    out = set()
    for part in parts:
        for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+", part):
            out.add(word.lower())
    # sign_in and log_in arrive as two tokens; join the adjacent pairs back up
    flat = [w.lower() for part in parts
            for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+", part)]
    for a, b in zip(flat, flat[1:]):
        out.add(a + b)
    return out


def is_sign_in(url_path: str) -> bool:
    return bool(_tokens(url_path) & SIGN_IN_WORDS)


CONFERENCE_LINK = re.compile(
    r"annual (conference|meeting|convention)|conference|convention|"
    r"summit|expo|symposium|training institute", re.I)

# Names hidden in a picture. APA Washington's sponsor page is one file called
# "Thank You Sponsors.png"; APA North Carolina's is "2026 Fall Conference
# Sponsors.jpg". A person reading that page sees the companies and no fetcher
# ever will, which is a different fact from "there is no list here" and is
# exactly the capture extension's job.
SPONSOR_IMAGE = re.compile(r"sponsor|exhibitor|thank ?you|partners", re.I)


def image_only(page: str) -> str | None:
    """A sponsor list that is a picture. Returns the file, or None."""
    for m in re.finditer(r'<img\b[^>]*>', page, re.I):
        tag = m.group(0)
        src = re.search(r'src="([^"]+)"', tag, re.I)
        alt = re.search(r'alt="([^"]*)"', tag, re.I)
        blob = up.unquote(f"{src.group(1) if src else ''} {alt.group(1) if alt else ''}")
        if SPONSOR_IMAGE.search(blob) and not re.search(r"logo|header|banner", blob, re.I):
            return blob.strip()[:90]
    return None


def owns(page: str, url: str, geo: str | None,
         org_url: str | None = None, parent_site: str | None = None) -> bool:
    """Does this exhibitor list belong to the STATE chapter, or to its parent?

    THE TRAP, caught on 2026-09-02. North Carolina Police Chiefs' org url is
    myiacp.org/NC__Login - IACP's own login page - and walking its links
    reaches events.rdmobile.com/Exhibitors/Index/20070, a real exhibitor list
    with 36 companies on it. It is IACP's NATIONAL 2026 Technology Conference.
    The words "north carolina" appear on it zero times.

    Accepting that would tag three dozen companies as having exhibited at a
    North Carolina chapter conference they never attended - the conference
    version of CLAUDE.md's never-point-a-company-at-its-parent's-board rule,
    and a false fact published on a page per event.

    Three ways to establish ownership, in order:

    THE CHAPTER'S OWN DOMAIN settles it. nyplanning.org/about/sponsors is the
    New York chapter's however little the page repeats "New York" - the site
    is the evidence. Never the parent's domain, which is where the trap lived.

    THE ADDRESS, flattened AND closed up, because a url writes it without the
    space: northcarolina.planning.org.

    THE PAGE, in its title, its first heading, or three times in the body.

    ON THE PARENT'S OWN SERVER the two-letter abbreviation does not count.
    myiacp.org/NC/exhibitors is either the North Carolina chapter's area or
    IACP's own page with an unlucky path segment, and "nc" cannot tell them
    apart - that loose two-letter match is what produced the trap in the
    first place. The full state name is required there; anything less goes to
    a person with the url in front of them.

    Deliberately strict, and it will refuse a real one - a conference titled
    only "2026 NCPCA Annual Meeting" names its state solely inside an
    acronym. A refusal costs one ruling; a wrong accept costs a permanent
    invented fact, so the trade runs this way.
    """
    # EIGHT ROWS NAME NO SINGLE STATE - "Multi-state", "WA/OR/ID", "TN/KY",
    # "NC/SC". The first version returned True for those, which switched this
    # guard off exactly where a regional body is most likely to be handed its
    # national parent's event. With no state to look for, the only evidence
    # left is the site itself, so the same-host rule below has to carry it
    # alone and everything else is refused to a person.
    state = next((s for s in STATES if re.search(rf"\b{s}\b", (geo or "").lower())), None)
    if not state and not (geo or "").strip():
        return True                       # not a state event at all

    def host(u):
        return up.urlsplit(u or "").netloc.lower().replace("www.", "")
    here, theirs, parent = host(url), host(org_url), host(parent_site)
    # A SUBDOMAIN OF THE PARENT IS THE PARENT. ace.awwa.org is AWWA's national
    # ACE conference, and stage 1 had handed it to the California-Nevada
    # Section as that section's own site; comparing hosts exactly let it pass,
    # because ace.awwa.org is not awwa.org. The page says "California" zero
    # times and is titled "Become an Exhibitor - American Water Works
    # Association".
    on_parent = bool(parent) and (here == parent or here.endswith("." + parent))
    if here and theirs and here == theirs and not on_parent:
        return True

    if not state:
        return False                      # a regional geo we cannot check
    flat = re.sub(r"[^a-z0-9]+", " ", up.unquote(url).lower())
    if re.search(rf"\b{state}\b", flat) or state.replace(" ", "") in flat.replace(" ", ""):
        return True
    head = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)) for m in
                    re.finditer(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>",
                                page[:200000], re.S | re.I)).lower()
    if re.search(rf"\b{state}\b", head):
        return True
    if len(re.findall(rf"\b{state}\b", page, re.I)) >= 3:
        return True
    if on_parent:
        return False                      # the abbreviation is not evidence here
    ab = STATES[state].lower()
    return bool(re.search(rf"\b{ab}\b", flat) or re.search(rf"\b{ab}\b", head))


def judge(page: str, url: str, host_hint: str | None, geo: str | None = None,
          org_url: str | None = None, parent_site: str | None = None
          ) -> tuple[str, str, list]:
    """Is this page a list of exhibitors? Returns (verdict, why, names).

    THE GATE THAT LET SEVEN MENUS THROUGH. It used to accept any page whose
    harvest was not `suspicious` and graded good OR mixed - and a chapter's
    sponsorship page grades "good" on nav alone, because association menus
    say Group, Services, Resources and Partners as readily as vendors do.
    Seven APA chapter menus were filed as exhibitor directories on
    2026-09-02; 146 names off APA Florida contained no company at all.

    Three verdicts now, and the middle one is the point:

      directory   the harvest reads as companies and grades `good`
      needs_person  it grades `mixed` - a real floor sometimes does (3CMA,
                  CoSN, PRIMA all did), and so does a menu. That is a
                  judgement, so it goes to a person with the evidence rather
                  than being written as a fact. Agents propose, people rule.
      no          suspicious, or reads as a menu, or grades doubtful
    """
    names = sweep.harvest(page, host_hint)
    doubt = sweep.suspicious(names)
    if doubt:
        return "no", doubt, names
    if not owns(page, url, geo, org_url, parent_site):
        return "wrong_event", ("this list is not on the chapter's own site and "
                               "names the state nowhere - it reads as the "
                               "national parent's event, not this chapter's"), names
    menu = sweep.reads_as_a_menu(names)
    if menu:
        return "no", menu, names
    grade, note = sweep.quality(names)
    if grade == "good":
        return "directory", f"{len(names)} names, good ({note})", names
    if grade == "mixed":
        return "needs_person", f"{len(names)} names, mixed ({note})", names
    return "no", f"{len(names)} names, {grade} ({note})", names


def candidates(page: str, base: str) -> list:
    """Directory links on this page, then directory links one conference deep.

    A chapter publishes its exhibitor list on the CONFERENCE page, not on the
    chapter home. Following the conference link first is what turns "no
    directory link on the home page" into a real answer.
    """
    direct = [(t, h) for t, h in links(page, base)
              if DIRECTORY_LINK.search(t) or DIRECTORY_LINK.search(h)]
    confs = [(t, h) for t, h in links(page, base)
             if CONFERENCE_LINK.search(t) or CONFERENCE_LINK.search(h)]
    return direct[:3], confs[:2]


def stage_directories(write: bool, limit: int | None, recheck: bool = False) -> int:
    doc = json.loads(EVENTS.read_text())
    if recheck:
        # RE-JUDGE WHAT WAS ALREADY ACCEPTED. The gate that accepted the first
        # seven was wrong, and a stored verdict from a wrong gate is not
        # evidence. Every row with an org url is re-read and re-decided.
        todo = [e for e in doc["events"] if e.get("org_url")]
        for e in todo:
            e.pop("directory_url", None)
            e.pop("candidate_url", None)
    else:
        todo = [e for e in doc["events"] if e.get("org_url") and not e.get("directory_url")]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} event(s) with an org url and no directory yet\n")
    got = person = 0
    for e in todo:
        hint = e.get("parent_national")
        page = fetch(e["org_url"])
        if not page:
            e["status"] = "org_unreachable"; continue
        direct, confs = candidates(page, e["org_url"])
        # the conference page's own directory links, one hop deeper
        for _t, h in confs:
            cp = fetch(h)
            if cp:
                more, _ = candidates(cp, h)
                direct += more
        if not direct:
            e["status"] = "no_directory_link"
            e.pop("directory_note", None)
            continue
        best = maybe = shot = wrong = None
        for _t, h in direct[:5]:
            d = fetch(h)
            if not d:
                continue
            verdict, why, names = judge(d, h, hint, e.get("geo"),
                                        e.get("org_url"),
                                        PARENT_SITES.get(hint or ""))
            if verdict == "directory":
                best = (h, why); break
            if verdict == "wrong_event" and not wrong:
                wrong = (h, why)
            if verdict == "needs_person" and not maybe:
                maybe = (h, why)
            if not shot:
                pic = image_only(d)
                shot = (h, pic) if pic else None
        if best:
            e["directory_url"], e["status"] = best[0], "directory_found"
            e["directory_note"] = best[1]
            got += 1
            print(f"  ok      {e['event_name'][:38]:40} {best[1][:34]:36} {best[0]}")
        elif maybe:
            # NOT a directory_url. A candidate a person rules on; writing it
            # would publish a maybe as a fact.
            e["status"] = "needs_person"
            e["candidate_url"], e["directory_note"] = maybe[0], maybe[1]
            person += 1
            print(f"  person  {e['event_name'][:38]:40} {maybe[1][:34]:36} {maybe[0]}")
        elif shot:
            e["status"] = "list_is_an_image"
            e["candidate_url"] = shot[0]
            e["directory_note"] = (f"the sponsor list on this page is a picture "
                                   f"({shot[1]}) - a person can read it, a fetcher "
                                   f"cannot. One for the capture extension.")
            print(f"  image   {e['event_name'][:38]:40} {shot[1][:40]}")
        elif wrong:
            e["status"] = "parents_event"
            e["candidate_url"], e["directory_note"] = wrong[0], wrong[1]
            print(f"  parent  {e['event_name'][:38]:40} {wrong[0]}")
        else:
            e["status"] = "not_a_directory"
            e.pop("directory_note", None)
            print(f"  --      {e['event_name'][:38]:40} links found, none read as a list")
    print(f"\n  {got} directory url(s) found, {person} candidate(s) for a person")
    if write:
        _save(doc)
    else:
        print("  LOOKED ONLY. Re-run with --write.")
    return 0


# WHERE A CHAPTER EVENT SITS IN THE CATALOG. state_events.json speaks its own
# vocabulary ("Municipal Government", "Water & Wastewater") and conferences.json
# speaks the site's ("Cities (elected)", "Water"). Every pair below already
# exists in conferences.json - checked before this table was written, because a
# block or department the site does not know would file the event nowhere.
CATALOG_PLACE = {
    "911/Dispatch": ("Public safety", "911 / dispatch"),
    "Assessment": ("Finance, procurement, HR, IT", "Assessors / property tax"),
    "Building & Code": ("Community development", "Code enforcement"),
    "County Government": ("Executive / administration", "Counties"),
    "District Technology": ("K-12 education", "Technology directors"),
    "Emergency Management": ("Public safety", "Emergency management"),
    "Finance & Budget": ("Finance, procurement, HR, IT", "Finance / budget"),
    "Fire": ("Public safety", "Fire"),
    "Human Services": ("Health and human services", "Human services"),
    "Local Government IT": ("Finance, procurement, HR, IT", "IT / CIO (local)"),
    "Municipal Clerks": ("Clerk, records, elections, legal", "Municipal clerks"),
    "Municipal Government": ("Executive / administration", "Cities (elected)"),
    "Parks & Recreation": ("Parks, recreation, libraries", "Parks & recreation"),
    "Planning & Zoning": ("Community development", "Planning & zoning"),
    "Police": ("Public safety", "Police"),
    "Procurement": ("Finance, procurement, HR, IT", "Procurement (local)"),
    "Public Health": ("Health and human services", "Local public health"),
    "Public Works": ("Public works and infrastructure", "Public works"),
    "School Boards": ("K-12 education", "School boards"),
    "School Business": ("K-12 education", "Business officials"),
    "Sheriffs": ("Public safety", "Sheriffs"),
    "Solid Waste": ("Public works and infrastructure", "Solid waste"),
    "State/Local IT": ("Finance, procurement, HR, IT", "IT / CIO (state)"),
    "Transit": ("Transportation", "Transit"),
    "Water & Wastewater": ("Public works and infrastructure", "Water"),
}

EVENT_WORD = re.compile(r"conference|convention|summit|expo|symposium|"
                        r"annual meeting|institute|congress|forum", re.I)


def confirmed_name(e: dict) -> str | None:
    """The organisation's real name, or None if it is still a generated guess.

    130 of the 359 staged rows carry name_confidence "pattern" - the name was
    made by filling a state into a template, and the generator says so itself.
    conferences.json is PUBLIC, a page per event, so a generated name promoted
    there is an invented organisation on a live site.

    What settles it is the parent's own listing. NLC publishes "Alaska
    Municipal League"; NACo publishes "Association of County Commissions of
    Alabama". stage_parents records that link text as org_name_observed, and
    THAT is the confirmation register_state_events asks for. A row matched by
    position rather than by name carries a placeholder there instead, and is
    not confirmed by it.
    """
    seen = (e.get("org_name_observed") or "").strip()
    if seen and not seen.startswith("(the link following"):
        return seen
    if e.get("name_confidence") == "named":
        return (e.get("org_name") or "").strip() or None
    return None


GENERIC_TITLE = re.compile(r"^(home|welcome|index|main|default|untitled)\b", re.I)


def name_from_own_site(e: dict) -> str | None:
    """The organisation's name as ITS OWN SITE states it.

    The second way to confirm a generated name, and for some rows the only
    one. A chapter matched by POSITION on the parent's listing - the state in
    a heading, the link labelled "Web Site" - has no name from that listing,
    which left three sheriffs' associations carrying 117 to 135 exhibitors
    unpromotable behind a name nobody had confirmed.

    So the site is asked. It is read, not guessed: the title or first heading,
    required to NAME THE STATE, so a generic "Home" or another body's page
    cannot answer for it.
    """
    state = next((s for s in STATES if re.search(rf"\b{s}\b", (e.get("geo") or "").lower())), None)
    if not state or not e.get("org_url"):
        return None
    page = fetch(e["org_url"])
    if not page:
        return None
    for m in re.finditer(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", page[:200000],
                         re.S | re.I):
        cand = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
        cand = re.split(r"\s+[|\u2013\u2014-]\s+", cand)[0].strip()
        if (3 < len(cand) <= 80 and not GENERIC_TITLE.match(cand)
                and re.search(rf"\b{state}\b", cand, re.I)):
            return cand
    return None


def event_name_and_year(page: str, url: str) -> tuple[str | None, str | None]:
    """What the directory page calls its event, and which year it is for.

    Read, never built. A tag is permanent - it lands inside company
    descriptions - so an event this cannot name does not get promoted.
    """
    heads = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
             for m in re.finditer(r"<(?:title|h1|h2)[^>]*>(.*?)</(?:title|h1|h2)>",
                                  page[:200000], re.S | re.I)]
    name = next((h for h in heads if EVENT_WORD.search(h) and len(h) <= 90), None)
    years = re.findall(r"\b(20[2-3]\d)\b", " ".join(heads[:4]) + " " + url)
    if not years:
        years = re.findall(r"\b(20[2-3]\d)\b", page[:60000])
    year = max(years) if years else None
    return name, year


def _tag(base: str, year: str, taken: set, geo: str | None = None) -> str:
    """A catalog tag: '<name> <year>', unique, in the charset selftest allows.

    THE CHARSET IS NOT COSMETIC. conferences.json's own rule is
    ^[\w &.'/-]+ 20\d\d$ and a tag lands permanently inside company
    descriptions, so a parenthesis in a disambiguator makes a row the build
    refuses - which is how the first version of this failed its own guard.
    Collisions are broken with the STATE, which also means something, and
    only then with a number.
    """
    def clean(s):
        return re.sub(r"\s+", " ", re.sub(r"[^\w &.'/-]+", " ", s or "")).strip(" -")

    base = clean(base)[:44].strip()
    tag = f"{base} {year}"
    if tag not in taken:
        return tag
    if geo:
        tag = f"{base} {clean(geo)} {year}"[:60]
        if tag not in taken:
            return tag
    n = 2
    while f"{base} {n} {year}" in taken:
        n += 1
    return f"{base} {n} {year}"


def stage_promote(write: bool) -> int:
    """Move confirmed chapter events into the public conference catalog.

    NOTHING DID THIS. `promoted` was written by register_state_events and read
    by no one, so a directory found here could never become a company: the
    chain from staged event to conferences.json to sweep to intake had no
    first link. This is that link, and it refuses far more than it takes.

    Four things must all be true, and each is a fact rather than a guess:
    a directory that a fetch read as a list of companies; an organisation
    name the parent's own listing confirms; an event name the directory page
    states; and a year. Anything missing and the row says which.
    """
    doc = json.loads(EVENTS.read_text())
    cat_p = DATA / "conferences.json"
    cat = json.loads(cat_p.read_text())
    taken = {c.get("event_tag") for c in cat["conferences"] if c.get("event_tag")}
    known_urls = {c.get("exhibitor_url") for c in cat["conferences"] if c.get("exhibitor_url")}

    ready = [e for e in doc["events"]
             if e.get("status") == "directory_found" and not e.get("promoted")]
    print(f"{len(ready)} event(s) with a directory and not yet promoted\n")
    added, refused = [], []
    for e in ready:
        org = confirmed_name(e)
        name_source = e.get("org_url_source")
        if not org:
            org = name_from_own_site(e)
            name_source = e.get("org_url")
        if not org:
            refused.append((e["org_code"], "the organisation name is still a "
                            "generated guess - neither the parent's listing nor "
                            "the site's own title confirms it"))
            continue
        place = CATALOG_PLACE.get(e.get("department") or "")
        if not place:
            refused.append((e["org_code"], f"no catalog place for department "
                                           f"{e.get('department')!r}"))
            continue
        if e["directory_url"] in known_urls:
            refused.append((e["org_code"], "that exhibitor url is already in the "
                                           "catalog under another event"))
            continue
        page = fetch(e["directory_url"])
        if not page:
            refused.append((e["org_code"], "the directory stopped answering"))
            continue
        name, year = event_name_and_year(page, e["directory_url"])
        if not year:
            refused.append((e["org_code"], "the page names no year, and a tag "
                                           "without one cannot be read back"))
            continue
        tag = _tag(org, year, taken, e.get("geo"))
        taken.add(tag)
        row = {
            "block": place[0], "department": place[1],
            # The event's own name where the page states one, else the
            # organisation the parent's listing confirmed. Never the generated
            # event_name, which is scaffolding.
            "conference": name or org,
            "flagship": False, "swept": False,
            "exhibitor_url": e["directory_url"],
            "fetchability": "readable",
            "event_tag": tag,
            "url": e.get("org_url"),
            "dates": None, "city": None,
            "dates_source": e["directory_url"],
            "dates_confidence": "unannounced",
            "discovery_notes": (
                f"State chapter event promoted from data/state_events.json on "
                f"{dt.date.today().isoformat()}. Organisation name confirmed as "
                f"{org!r} by {name_source}. Directory: "
                f"{e.get('directory_note')}. Event name "
                + (f"read from the page as {name!r}." if name else
                   "not stated on the page; the organisation's name stands in.")),
            "state_event": {"org_code": e["org_code"], "geo": e.get("geo"),
                            "parent_national": e.get("parent_national")},
        }
        cat["conferences"].append(row)
        e["promoted"] = True
        e["promoted_tag"] = tag
        added.append((e["org_code"], tag, e["directory_url"]))

    for code, tag, url in added:
        print(f"  promote  {code:14} {tag[:38]:40} {url[:52]}")
    for code, why in refused:
        print(f"  refuse   {code:14} {why[:80]}")
    print(f"\n  {len(added)} promoted, {len(refused)} refused")
    if not write:
        print("  LOOKED ONLY. Re-run with --write.")
        return 0
    if added:
        tmp = cat_p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cat, indent=1) + "\n")
        json.loads(tmp.read_text())
        tmp.replace(cat_p)
        _save(doc)
        print(f"  wrote {len(added)} conference(s) into conferences.json")
    return 0


def _save(doc: dict) -> None:
    tmp = EVENTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n")
    json.loads(tmp.read_text())
    tmp.replace(EVENTS)
    print("  saved")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parents", action="store_true")
    ap.add_argument("--directories", action="store_true")
    ap.add_argument("--promote", action="store_true",
                    help="move confirmed events into conferences.json")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--recheck", action="store_true",
                    help="re-judge rows that already carry a directory url")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.parents:
        return stage_parents(a.write)
    if a.directories:
        return stage_directories(a.write, a.limit, a.recheck)
    if a.promote:
        return stage_promote(a.write)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
