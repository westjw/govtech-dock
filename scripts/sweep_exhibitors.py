#!/usr/bin/env python3
"""Fetch an exhibitor directory and stage it for conference_intake.

    python3 scripts/sweep_exhibitors.py --list          # what is ready
    python3 scripts/sweep_exhibitors.py --all           # look, write nothing
    python3 scripts/sweep_exhibitors.py --all --write   # stage them
    python3 scripts/sweep_exhibitors.py --tag "GFOA 2026" --write

WHAT WAS MISSING. conference_intake.py takes a FILE - a staged JSON list of
exhibitors - and turns it into companies, suppliers and candidates. Nothing
produced that file. All seven staged files on disk were assembled by hand,
which is why 45 conferences carry an exhibitor_url and only twelve events have
ever been swept. This is the fetcher between the two.

WHAT IT REFUSES TO DO. It does not decide whether an exhibitor is govtech, it
does not write to companies.json, and it does not mark a conference swept.
It fetches one page, reads the names off it, and writes a staging file a
person reviews before conference_intake runs. Every downstream gate stays
exactly where it was.

THE HARD PART IS TELLING AN EXHIBITOR FROM THE FURNITURE. An exhibitor
directory is a page of company names, and so is the navigation, the footer,
the sponsor tiers and the "browse by category" rail. The rules here are the
ones capture.js learned on job boards, because the problem is identical:

  ANCHORED nav matching, never substring. `^(home|about|register)$` and not
  `register`, or every "Registration Systems Inc" on the floor is deleted.
  This exact bug was introduced and caught in scripts/capture.js on
  2026-09-02; the guard against it lives in selftest.

  A NAME IS NOT A SENTENCE. Exhibitor names are short. A run of text past
  ~70 characters is a description, a tagline or a whole paragraph that
  happened to sit in a link.

  POSITION BEATS PATTERN. The list is whatever repeats. A page whose
  candidates are mostly unique one-offs is a page about a conference rather
  than a list of who is standing at it.

AND IT SAYS WHEN IT FAILED. `found: false` with a `why` is a real answer and
the staged file records it, because "we could not read this directory" and
"nobody exhibited" are opposite facts and this project files them apart. An
empty read is never written as an empty exhibitor list.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                              # noqa: E402

# ANCHORED. A substring match here deletes real companies: "register" would
# take out Registration Systems, "contact" would take out Contact Technologies.
NAV = re.compile(
    r"^(home|about( us)?|contact( us)?|register(ation)?|login|log in|sign in|"
    r"search|menu|back|next|previous|more|read more|learn more|view all|"
    r"see all|all exhibitors|exhibitors?|sponsors?|schedule|agenda|program|"
    r"sessions?|speakers?|hotels?|travel|venue|faq|help|privacy|terms|"
    r"cookies?|accessibility|sitemap|donate|join|renew|membership|news|"
    r"blog|events?|conference|annual (conference|meeting)|expo|trade show|"
    r"floor plan|exhibit(ing)?|become an exhibitor|book a booth|"
    r"[a-z]|[0-9]{1,4}|page [0-9]+)$", re.I)

# A tier label is not a company. These head the sections a directory is
# grouped into and would otherwise be filed as the biggest exhibitor there.
TIER = re.compile(
    r"^(platinum|gold|silver|bronze|diamond|titanium|premier|presenting|"
    r"supporting|contributing|patron|host|media|partner|sponsors?|"
    r"exhibitors?|vendors?)(\s+(sponsors?|partners?|level|tier))?$", re.I)

# NAVIGATION IS A PHRASE, NOT JUST A WORD. The first version of this filter
# was anchored single words and let "Become a Member", "About Webinars" and
# "Access the Agenda Here." through - IMLA staged 69 menu items as exhibitors
# and IAAO staged "Accessibility Statement" and "Author FAQ". What they have
# in common is that they START WITH A VERB OR A PREPOSITION. Company names
# almost never do; site chrome almost always does.
NAV_START = re.compile(
    r"^(about|become|access|view|browse|see|read|learn|join|renew|submit|"
    r"download|explore|discover|find|get|go|click|visit|watch|listen|"
    r"subscribe|follow|share|print|email|call|apply|order|shop|buy|"
    r"request|schedule|book|reserve|add|adding|manage|update|edit|create|"
    r"search|filter|sort|show|hide|skip|jump|return|continue|start|"
    r"how|why|what|when|where|who|our|your|my|the|this|these|all)\b",
    re.I)

# A breadcrumb, a menu path or a sentence. None of them is a company.
NOT_A_NAME = re.compile(r"\s/\s|\s>\s|\s\|\s|\.\.\.$|\?$", re.I)

# THE FOUR THINGS THAT GOT THROUGH ON THE FIRST REAL SWEEP, each seen in the
# staged output of a directory that was otherwise correct:
#
#   a template fragment the page never rendered - AWWA and NAHRO both yielded
#   "' + filterConfig.label + '", which is javascript that reached the markup
#   a booth number - "Booth #1158" repeated down AWWA's and NAHRO's lists
#   the list's own heading - NACUBO offered "2026 Exhibitor List"
#   a filter control - "All", on NLC and NatCon
#
# None is a judgement call and all four are exact, so they are filtered rather
# than left for a person to strike out 40 times.
JUNK = re.compile(
    r"^booth\s*#?\s*\d+|"                          # booth numbers
    r"['\"]\s*\+|\+\s*['\"]|\{\{|\$\{|function\s*\(|"    # template/script debris
    r"^\d{4}\s+(exhibitor|sponsor|vendor)|"        # "2026 Exhibitor List"
    r"^(all|none|other|misc|n/?a|tbd|tba)$", re.I)

# SITE SECTIONS THAT SURVIVED THE FIRST TWO FILTERS, read off GFOA's staged
# file after the chevron fix: "Bylaws", "Privacy Policy", "Staff Directory",
# "Member Communities", "Learning Dashboard", "Empty cart Cart", "Networking
# and Social Events", "Registration for 2027 Opens Fall 2026". Each is exact
# and none is a judgement, so they are filtered rather than struck out by hand.
SECTIONS = re.compile(
    r"^(bylaws|privacy (policy|statement)|terms of (use|service)|staff( directory)?|"
    r"leadership|board of directors|member(ship)? (communities|benefits|directory)|"
    r"learning (dashboard|center|centre)|my (account|dashboard|cart)|"
    r"(empty )?cart( cart)?|checkout|dashboard|newsletter|press( room)?|"
    r"media kit|advertis(e|ing)|exhibitor (prospectus|kit|guide|resources)|"
    r"sponsorship( opportunities)?|code of conduct|photo gallery|"
    r"networking( and| &)? social events|mobile app|plan your (trip|visit)|"
    r"things to do|cancellation policy|health (and|&) safety|attendee (info|faq|"
    r"information)|(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*"
    r"\b(event|session|reception|finale|keynote)s?|registration (for|opens|rates|"
    r"fees|info).*|call for (proposals|speakers|sessions|presentations))$", re.I)

MIN_NAMES = 8          # fewer than this is a page about an event, not a list
MAX_NAME = 70          # past this it is a description, not a name
NAV_SHARE = 0.30       # more chrome than this and the page is not a directory


# A CHEVRON IS NOT PART OF A NAME. GFOA's directory renders every menu item
# twice - once plain, once with a trailing "›" - and the trailing glyph beat
# both the dedupe key and the anchored NAV filter: "Exhibitors ›" is not
# "exhibitors", so it survived, twice. Stripped before anything else looks.
_ARROWS = "\u203a\u00bb\u2192\u25b8\u25be\u25b6\u276f\u00ab\u2039\u2190<>"


# A CALL TO ACTION RIDING ON A NAME. NC League's partner page writes each
# sponsor as "CavMac Learn More" - the name and its button in one anchor - and
# the row was filed under that whole string, twice (2026-10-10).
CTA_TAIL = re.compile(r"[\s\-\u2013\u2014|:]*\b(learn more|read more|more info(rmation)?|find out more|"
                      r"visit (the )?(web)?site|view (profile|website|details)|website|details|"
                      r"click here)\s*$", re.I)


# A CALL TO ACTION IN FRONT OF A NAME. PTI's sponsor buttons read "CONTACT
# BOTCOPY", "CONTACT GOOGLE" (2026-10-16). "Contact Us" and its kin stay as
# they are, for the nav filter to drop whole.
CTA_HEAD = re.compile(r"^(contact|visit|meet)\s+(?!(us|me|sales|support|info|information|the team|"
                      r"our team|staff|form|page|details|now)\b)(?=\S)", re.I)


# A SPONSORSHIP TIER RIDING ON A NAME. Sched's exhibitor directory writes
# "Esri - Diamond Sponsor", "VertiGIS & YP Meet-Up Sponsor" and "Terracon Data
# Solutions - Workshop & GeoPoker/Milwaukee Landmark Crawl Sponsor" (GIS-Pro
# 2026, 2026-10-25), and as written none of them matched the company already
# on the board, so each would have come back as a new candidate.
# - After a dash or a bar, the whole label goes, up to six words, "&" and "/"
#   inside it included (SPONSOR_LABEL).
# - Without one, only words a sponsorship is named with go (SPONSOR_WORDS).
#   An "&" is a separator only when every word after it is one of those:
#   "Black & Veatch Gold Sponsor" is Black & Veatch, never "Black", which the
#   first version cut it to (second review, 2026-10-26).
SPONSOR_LABEL = re.compile(r"\s+(?:-|\u2013|\u2014|\|)\s+(?:[A-Za-z&][\w'/&-]*\s+){0,6}"
                           r"(?:co-)?(?:sponsor|participant)\s*$", re.I)
_SPONSOR_WORD = (r"(?:diamond|platinum|gold|silver|bronze|copper|titanium|title|premier|presenting|"
                 r"supporting|contributing|signature|official|exclusive|major|community|media|event|"
                 r"exhibit|exhibitor|showcase|workshop|session|track|keynote|plenary|reception|lunch|"
                 r"luncheon|breakfast|dinner|coffee|social|happy|hour|lanyard|badge|wi-?fi|app|charging|"
                 r"station|registration|meet-?up|yp|networking|crawl|party|hackathon|student|"
                 r"scholarship|awards?|gala|golf|tote|bag|hotel|key|notebook|water|bottle)")
SPONSOR_WORDS = re.compile(rf"(?:\s+&)?(?:\s+{_SPONSOR_WORD}){{0,4}}\s+(?:co-)?(?:sponsor|participant)\s*$",
                           re.I)


def clean(s: str) -> str:
    s = s or ""
    # DECODED UNTIL IT STOPS CHANGING: alt="Election Systems &amp;amp; Software"
    # survived one pass as "&amp;" on NASS's sponsor wall (2026-10-16).
    for _ in range(3):
        u = html_lib.unescape(s)
        if u == s:
            break
        s = u
    s = re.sub(r"\s+", " ", s).strip()
    s = CTA_TAIL.sub("", s).strip()
    s = CTA_HEAD.sub("", s).strip()
    s = SPONSOR_LABEL.sub("", s).strip()
    s = SPONSOR_WORDS.sub("", s).strip()
    return s.strip(_ARROWS + " ")


def host_chrome(host: str | None):
    """The association's own pages, named after itself.

    GFOA's directory offered "Careers at GFOA", "GFOA's Research & Consulting
    Center" and "GFOA's 121st Annual Conference" as exhibitors. A name that
    possesses the host, or sits "at" it, is the host's site and not a company
    on its floor. "Illinois GFOA" - a chapter that really does exhibit - has
    neither shape and is kept.
    """
    h = re.escape((host or "").strip())
    if not h:
        return None
    return re.compile(rf"\b{h}['\u2019]s\b|\bat {h}\b|^{h}\b.*\b(annual|conference|"
                      rf"summit|expo|meeting)\b", re.I)


def looks_like_a_name(s: str, host: str | None = None) -> bool:
    if not s or len(s) < 2 or len(s) > MAX_NAME:
        return False
    if NAV.match(s) or TIER.match(s) or SECTIONS.match(s):
        return False
    hc = host_chrome(host)
    if hc and hc.search(s):
        return False
    if s.count(".") > 2 or s.startswith(("http", "©", "#")):
        return False
    # AN EMAIL ADDRESS IS NOT AN EXHIBITOR, and it is worse than noise here.
    # Association footers are full of them, and this project keeps addresses
    # off the tracked side of the line entirely, so staging one puts a
    # contact detail into a committed file. Caught by the address guard on
    # 2026-09-11, after five association footers put seven of them into three
    # capture files - and caught a second time when this very comment quoted
    # two of them as examples.
    if "@" in s and re.search(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", s):
        return False
    # A name has letters. A price, a booth number and a date do not qualify.
    if not re.search(r"[A-Za-z]{2}", s):
        return False
    # Sentences end in punctuation and contain verbs; names rarely do either.
    if s.endswith((".", "!", "?")) and len(s.split()) > 6:
        return False
    if NOT_A_NAME.search(s) or JUNK.search(s):
        return False
    # "Become a Member", "About Webinars", "Access the Agenda Here." - chrome
    # reads as an instruction, and an instruction opens with a verb. A real
    # name that happens to start this way (All Traffic Solutions, The Gordian
    # Group) is lost, which is the right side to err on: a company wrongly
    # dropped shows up at the next conference, a menu item wrongly kept is
    # published as an exhibitor.
    if NAV_START.match(s) and len(s.split()) > 1:
        return False
    return True


def host_of(tag: str | None) -> str | None:
    """"GFOA 2026" -> "GFOA". The association's short name, off the tag."""
    m = re.match(r"([A-Za-z&]{2,})", str(tag or ""))
    return m.group(1) if m else None


EXHIBITOR_COLUMN = re.compile(r"\b(company|companies|exhibitor|vendor|firm|organi[sz]ation|name)s?\b", re.I)
BOOTH_COLUMN = re.compile(r"\bbooth", re.I)
_CELL = re.compile(r"<t([dh])\b[^>]*>(.*?)</t\1>", re.S | re.I)


BOOTH_CELL = re.compile(r"^booth\s*(#|no\.?|number)?\s*[\w]*\d[\w]*(\s*[,/&-]\s*\w*\d\w*)*$", re.I)


def booth_column_pair(rows: list[str]) -> tuple[int | None, int | None]:
    """(name column, first row) for a table with no header whose every row
    says "Booth #N" in one cell - NACE's 2026 list, 57 rows of "Vaisala Inc |
    Booth #229" (2026-10-15). The page states the floor in each row instead of
    once at the top. The name is the cell beside the booth, to its left."""
    pairs = []
    for r in rows:
        cells = [clean(re.sub(r"<[^>]+>", " ", c)) for _k, c in _CELL.findall(r)]
        j = next((k for k, c in enumerate(cells) if BOOTH_CELL.match(c)), None)
        if j is not None and len(cells) >= 2:
            pairs.append(j)
    if len(pairs) < MIN_NAMES or len(pairs) < 0.8 * max(1, len(rows)):
        return None, None
    j = Counter(pairs).most_common(1)[0][0]
    return (j - 1 if j > 0 else 1), j


def table_of_exhibitors(page: str, host: str | None = None) -> list[dict]:
    """The company column of a table whose header says it is the floor.

    THE MENU CROWDED OUT THE LIST. The Illinois APCO chapter's "2026 Vendors"
    page is a table of 80 vendors under a header row "Company/Firm | Booth
    Number" - and harvest() read the site's 23 menu anchors first, found more
    than MIN_NAMES, and never looked at a cell. The page graded `menu` and a
    real floor read as nothing (2026-10-15).

    Narrow on purpose: a header row naming BOTH a company column and a booth
    column, in the first three rows, is the page saying what the table is. A
    sponsor table ("Company | Level") or a schedule is not taken by this
    rule. Every cell still passes looks_like_a_name, and the result still
    goes through suspicious(), quality() and reads_as_a_menu() downstream.
    """
    best: list[dict] = []
    for t in re.finditer(r"<table\b.*?</table>", page, re.S | re.I):
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", t.group(0), re.S | re.I)
        col = start = booth = None
        for i, r in enumerate(rows[:3]):
            heads = [clean(re.sub(r"<[^>]+>", " ", c)) for _k, c in _CELL.findall(r)]
            hit = next((j for j, h in enumerate(heads)
                        if EXHIBITOR_COLUMN.search(h) and not BOOTH_COLUMN.search(h)), None)
            if hit is not None and any(BOOTH_COLUMN.search(h) for h in heads):
                col, start = hit, i + 1
                break
        if col is None:
            # Only the rows that carry a booth: the header row ("Name |
            # Booth") and any stray row are not exhibitors.
            col, booth = booth_column_pair(rows)
            start = 0
        if col is None:
            continue
        got: dict[str, dict] = {}
        for r in rows[start:]:
            cells = _CELL.findall(r)
            if len(cells) <= max(col, booth or 0):
                continue
            if booth is not None and not BOOTH_CELL.match(
                    clean(re.sub(r"<[^>]+>", " ", cells[booth][1]))):
                continue
            raw = cells[col][1]
            name = clean(re.sub(r"<[^>]+>", " ", raw))
            if not looks_like_a_name(name, host):
                continue
            a = re.search(r'href="(https?://[^"]+)"', raw, re.I)
            site = a.group(1) if a else None
            if name.lower() not in got or (site and not got[name.lower()]["website"]):
                got[name.lower()] = {"name": name, "website": site}
        if len(got) > len(best):
            best = list(got.values())
    return best


# THE SITE'S BUILDER IS NOT ON ITS FLOOR. Illinois APCO's vendor page ends
# "Government Websites by <a>CivicPlus&reg;</a>", and CivicPlus - a company
# on the board - was tagged as exhibiting at a 911 conference (2026-10-15).
# The floor reader never saw it: a name already on the board is settled and
# skips the reader. "Sponsored by" is NOT here; that one names a sponsor.
CREDIT_LINE = re.compile(r"\b(powered|websites?|site|designed|developed|built|hosted|"
                         r"created|crafted|made|platform)\s+by\s*[:\-\u2013]?\s*$", re.I)


def credited(page: str, at: int) -> bool:
    """Does the text just before position `at` read as a credit line?"""
    before = html_lib.unescape(re.sub(r"<[^>]+>", " ", page[max(0, at - 160):at]))
    return bool(CREDIT_LINE.search(re.sub(r"\s+", " ", before)))


# A BOOTH NUMBER IS NOT PART OF A NAME. Floor apps print "1035 PCTEL, Inc."
# in one cell: APCO 2026's browser read carried a booth on 246 of 253 names,
# ICC's 112 of 119, NAPT's 76 of 87 (2026-10-15), and "1035 PCTEL, Inc."
# matches no company on the board, so a known exhibitor was never tagged.
# A real name can start with digits (3M, 2J Antennas, 360 Photo Booth), so
# the prefix is taken off only on a floor where most names carry one.
BOOTH_PREFIX = re.compile(r"^(?:booth\s*#?\s*)?\d{1,5}[A-Za-z]?"
                          r"(?:\s*[-/&,]\s*\d{1,5}[A-Za-z]?)*\s+(?=\S)", re.I)
BOOTH_SHARE = 0.7


def strip_booths(names: list[str]) -> list[str]:
    """The names without their booth numbers, when the floor prints them.

    Two ways a floor shows it prints booths: most of its names carry a
    number, or the numbered names are ALSO on the page without one. WRPA's
    map-dynamics floor lists "102 Allplay Systems" in its A-Z block and
    "Allplay Systems" again under its category, so only 76 of 165 names were
    numbered and none was stripped - every exhibitor would have been filed
    twice (2026-10-20). A numbered name whose bare form stands beside it is a
    booth, not a name like "3M".
    """
    hits = [n for n in names if BOOTH_PREFIX.match(n or "")]
    bare = {(n or "").strip().lower() for n in names if not BOOTH_PREFIX.match(n or "")}
    twins = sum(1 for n in hits if BOOTH_PREFIX.sub("", n, count=1).strip().lower() in bare)
    if len(hits) < MIN_NAMES or (len(hits) < BOOTH_SHARE * len(names)
                                 and twins < max(MIN_NAMES, len(hits) // 2)):
        return list(names)
    return [BOOTH_PREFIX.sub("", n or "", count=1) for n in names]


# THE PLATFORM NAMES ITS OWN FIELD. a2z prints each exhibitor as
# <a class="exhibitorName">AccuTemp Products</a> - an anchor with no href, so
# the anchor pass skipped all 289 on SNA's 2027 floor and kept its 20 menu
# links (2026-10-16). An element whose class says it holds the exhibitor's or
# company's name is read, on top of the rest.
NAME_FIELD = re.compile(
    r'<(a|span|div|td|h[2-6]|strong|p|li)\b[^>]*class="[^"]*\b(?:exhibitor[-_]?name|company[-_]?name|'
    r'exh[-_]?name|exhibitorname|companyname)\b[^"]*"[^>]*>(.*?)</\1>', re.S | re.I)


def named_fields(page: str, host: str | None = None) -> list[dict]:
    """Names out of elements the platform marks as an exhibitor's name."""
    out: dict[str, dict] = {}
    for m in NAME_FIELD.finditer(page):
        inner = m.group(2)
        name = clean(re.sub(r"<[^>]+>", " ", inner))
        if not looks_like_a_name(name, host) or name.lower() in out:
            continue
        a = re.search(r'href="(https?://[^"]+)"', m.group(0), re.I)
        out[name.lower()] = {"name": name, "website": a.group(1) if a else None}
    return list(out.values())


# A LOGO WALL STATES ITS NAMES IN ALT TEXT. ISTELive's, NCSHA's and NASS's
# sponsor pages print no company name in text, and each logo is a link to the
# sponsor's own site carrying alt="Dell Technologies" (2026-10-16). Read ONLY
# for a row a reader marked "image": elsewhere a strip of linked logos is as
# often the association's year-round partners, which is not this event's list.
LOGO_ALT_JUNK = re.compile(r"logo|icon|image|banner|sponsor|exhibitor|facebook|twitter|linkedin|"
                           r"instagram|youtube|tiktok|placeholder|photo|graphic|button|arrow|"
                           r"opens in a new|\.(png|jpe?g|svg|gif|webp)$|[a-z]+[-_][a-z0-9]+[-_]", re.I)


LOGO_AT_EVENT = re.compile(
    r"\s+(?:at|@)\s+(?=[^\n]{3,90}$)[^\n]*\b(?:20\d\d|summit|conference|expo|show|"
    r"congress|convention|forum|meeting)\b[^\n]*$", re.I)


# A LOGO'S ALT THAT ENDS IN "Logo" NAMES THE COMPANY BEFORE IT: "GotchaCot
# Logo" and "Aeolus Pet Logo" were dropped whole by LOGO_ALT_JUNK, which
# matches "logo" anywhere, off a floor graded as read (second review,
# 2026-10-26).
LOGO_SUFFIX = re.compile(r"\s*(?:[-\u2013|:]\s*)?\blogo(?:\s+image)?\s*$", re.I)
CHROME = re.compile(r"<(header|nav)\b.*?</\1>", re.S | re.I)


def logo_names(page: str, base: str, host: str | None = None,
               unread: list | None = None) -> list[dict]:
    """Linked logos off the page's own host, named by their alt text.

    NOT THE ORGANISER'S OWN. humanepro.org's header, nav and footer link the
    organiser's brand, humaneworld.org, by a logo whose alt is "Humane World
    for Animals", and it was staged as a sponsor of its own Animal Care Expo
    (second review, 2026-10-26). A logo linking a host the header or nav
    links is the site's, wherever it sits: the header's, and the footer's
    copy of the brand, never a sponsor wall that sits in a footer.
    `unread` collects the alt of every sponsor-shaped logo not read as a
    name, so the staged floor says what it left out."""
    import urllib.parse as up

    def host_of_url(u: str) -> str:
        return up.urlsplit(u).netloc.lower().replace("www.", "")
    own = host_of_url(base or "")
    chrome = [(m.start(), m.end()) for m in CHROME.finditer(page)]
    chrome_hosts = {host_of_url(h) for a, b in chrome
                    for h in re.findall(r'<a\b[^>]*href="(https?://[^"]+)"', page[a:b], re.I)}
    out: dict[str, dict] = {}
    for m in re.finditer(r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        where = host_of_url(href)
        if not own or where == own or where in chrome_hosts:
            continue
        for alt in re.findall(r'<img\b[^>]*\balt="([^"]{2,120})"', inner, re.I):
            # "Calix at Broadband Communities Summit 2027": an organiser that
            # writes each sponsor's logo alt as "<sponsor> at <the event>".
            # host_chrome rightly refuses "at <host>" in a NAME ("Careers at
            # GFOA" is the host's own page) and so refused every sponsor on
            # Terrapinn's walls (2026-10-20). The logo links off the host's
            # site, to the sponsor's own, so the suffix is the event and the
            # name is what stands before it.
            name = clean(LOGO_SUFFIX.sub("", clean(LOGO_AT_EVENT.sub("", alt))))
            if LOGO_ALT_JUNK.search(name) or not looks_like_a_name(name, host):
                if unread is not None and not LOGO_ALT_JUNK.search(LOGO_SUFFIX.sub("", alt)):
                    unread.append(alt)
                continue
            out.setdefault(name.lower(), {"name": name, "website": href})
    return list(out.values())


def harvest(page: str, host: str | None = None, logos_from: str | None = None,
            unread_logos: list | None = None) -> list[dict]:
    """Names off an exhibitor directory, with a website where the page gave one.

    Reads anchors first - a directory almost always links each exhibitor to
    its own site or to a detail page - then falls back to repeated block
    elements for the directories that are plain text in a table. A table
    whose header says it is the floor (table_of_exhibitors) is read on top
    of both, whatever they found.

    ADDED, NEVER INSTEAD. Returning the table alone was tried first and
    measured on the 75 live floors (2026-10-15): 8 carry such a table, and on
    the Connecticut Conference of Municipalities' page the table holds the
    exhibitors while four sponsors - Tyler Technologies Digital Services
    among them - are linked logos outside it. Replacing the harvest would
    have dropped them. The menu anchors that ride along are what the floor
    reader deletes; a company dropped here is never seen again.
    """
    out: dict[str, dict] = {}

    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href, inner = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        name = clean(inner)
        if not looks_like_a_name(name, host):
            continue
        if credited(page, m.start()):
            continue
        site = href if href.startswith("http") else None
        # An anchor pointing back into the conference's own site is a detail
        # page, not the exhibitor's website. Recorded as None rather than
        # guessed - a wrong website is worse than a missing one.
        key = name.lower()
        if key not in out or (site and not out[key]["website"]):
            out[key] = {"name": name, "website": site}

    if len(out) < MIN_NAMES:
        for m in re.finditer(
                r"<(td|li|h[2-5]|strong|b)\b[^>]*>(.*?)</\1>", page, re.S | re.I):
            name = clean(re.sub(r"<[^>]+>", " ", m.group(2)))
            if looks_like_a_name(name, host) and name.lower() not in out:
                out[name.lower()] = {"name": name, "website": None}

    extra = logo_names(page, logos_from, host, unread_logos) if logos_from else []
    for e in table_of_exhibitors(page, host) + named_fields(page, host) + extra:
        key = e["name"].lower()
        if key not in out or (e["website"] and not out[key]["website"]):
            out[key] = e

    rows = list(out.values())
    bare = strip_booths([r["name"] for r in rows])
    if bare != [r["name"] for r in rows]:
        kept: dict[str, dict] = {}
        for r, nm in zip(rows, bare):
            nm = clean(nm)
            if not looks_like_a_name(nm, host):
                continue
            if nm.lower() not in kept or (r["website"] and not kept[nm.lower()]["website"]):
                kept[nm.lower()] = dict(r, name=nm)
        rows = list(kept.values())
    return rows


def suspicious(names: list[dict]) -> str | None:
    """Reasons to doubt this is an exhibitor list at all.

    A page that yields six names yielded its navigation. A page where every
    name appears once and none repeat is prose. Saying so is the point: an
    unreadable directory recorded as an empty one is a false absence, and the
    conference would then read as "nobody exhibited".
    """
    if len(names) < MIN_NAMES:
        return (f"only {len(names)} name(s) survived the filter, which is a page "
                f"about an event rather than a list of who is standing at it")
    words = Counter()
    for n in names:
        for w in n["name"].lower().split():
            words[w] += 1
    if names and words and words.most_common(1)[0][1] > len(names) * 0.6:
        top = words.most_common(1)[0][0]
        return (f"{top!r} appears in more than half the names, so these are "
                f"probably section headings or menu items rather than companies")
    return None


# A company's name carries a corporate marker surprisingly often. An
# association's own site sections carry abstract nouns instead.
CORP = re.compile(r"\b(inc|llc|corp|corporation|co|ltd|limited|plc|group|"
                  r"holdings|partners|associates|technologies|technology|"
                  r"systems|solutions|software|services|labs|works|company|"
                  r"consulting|engineering|industries|international|global|"
                  r"america|usa)\b\.?|&", re.I)
SECTION = re.compile(r"^(advocacy|awards|benefits|board of directors|committees|"
                     r"resources|membership|publications|education|training|"
                     r"leadership|governance|history|mission|staff|chapters|"
                     r"collections|authors?|abstracts?|posters?|papers?|"
                     r"proceedings|archives?|newsletters?|webinars?)\b", re.I)
# THE SAME VOCABULARY, ANYWHERE IN THE NAME. SECTION is anchored, so it reads
# "Advocacy" and misses "Advocacy Chats Recordings", "2026 Fall Conference"
# and "Annual Conference & Expo Sponsors" - which is how eleven pages of pure
# association navigation were all graded `good`.
#
# MEASURED, on 2026-09-10, over ten directories known to be real floors and
# nine known to be site maps. The rate of names carrying any of these words
# ANYWHERE separates them with nothing in between:
#
#   real floors   EDUCAUSE 0%  PLA 0%  GFOA 0%  AWWA 0%  SNA 0%  NACUBO 0%
#                 CoSN 1%  NatCon 2%  NAHRO 2%  3CMA 5%
#   site maps     KLC 12%  Vermont 18%  NC League 30%  NYCOM 32%
#                 ITS America 33%  CCAP 42%  WRPA 47%  Ohio ASBO 56%
#
# Corporate markers do NOT separate them - 3CMA is a real floor at 11% and
# Ohio ASBO is a menu at 16% - which is why the old rule passed all eleven.
SITE_WORD = re.compile(r"\b(advocacy|training|membership|conferences?|"
                       r"newsletters?|director(y|ies)|resources?|awards?|"
                       r"committees?|login|register|registration|policy|"
                       r"about|contact|events?|programs?|blogs?|news|"
                       r"chapters?|affiliates?|sponsorships?|exhibiting|"
                       r"schedule|hours|booth|legend|print|attend|"
                       r"webinars?|podcasts?|publications?)\b", re.I)


def quality(names: list[dict]) -> tuple[str, str]:
    """How much this page looks like a floor rather than a site map.

    MEASURED ON THE SURVIVORS, and that wording is deliberate. The first
    version of this counted how many names matched the navigation patterns -
    after looks_like_a_name had already deleted every name that matched them.
    It read 0% on every page including the two that were pure navigation, and
    would have staged 58 IMLA menu items and 23 IAAO paper titles as
    exhibitors. A counter placed after the filter that removes its input
    measures the filter, which is the defect this codebase keeps finding.

    So this looks for what a real exhibitor list HAS rather than for what the
    filter already took out: corporate markers, and the absence of the
    abstract nouns an association names its own sections with.

    No single signal separates them cleanly and this does not pretend
    otherwise - it returns a grade and a sentence, and a person decides which
    files to run intake on. Measured against six directories read on
    2026-09-02: NLC 31% corporate, GFOA 19%, PSHRA 16%, 3CMA 7% all read as
    real floors; IAAO 0% corporate with 13% section nouns and NASCIO 2%/23%
    were the association's own site.
    """
    if not names:
        return "empty", "nothing survived the filter"
    corp = sum(1 for n in names if CORP.search(n["name"])) / len(names)
    sect = sum(1 for n in names if SECTION.match(n["name"])) / len(names)
    site = sum(1 for n in names if SITE_WORD.search(n["name"])) / len(names)
    note = (f"{corp:.0%} carry a corporate marker, {sect:.0%} name a site "
            f"section, {site:.0%} use the words a site names itself with")
    # THE STRONGEST SIGNAL FIRST. Ten real floors top out at 5% and nine site
    # maps start at 12%, so this is the line - and it is the one that was
    # missing when eleven menus graded `good` and 596 navigation items and
    # county job postings sat one command away from the board.
    if site >= 0.10:
        return "menu", (note + " - this reads as the association's own "
                        "navigation rather than a floor")
    if sect >= 0.10 or corp < 0.05:
        return "doubtful", note + " - this reads as the association's own pages"
    if corp >= 0.15:
        return "good", note
    return "mixed", note + " - worth a look before running intake"


def merge_into(fresh: dict, out: pathlib.Path) -> bool:
    """Carry a staged file's rulings onto a re-sweep. False means do not write.

    A re-sweep used to rebuild the file from bare {name, website} rows, so
    every is_govtech flag classify had written - and any a person had set -
    was gone. And a directory that answered last week and refuses today
    overwrote the good list with the refusal. Both are the sweep destroying
    work it did not do. Flags travel by name; a refusal never replaces a
    find - it is printed, and the good file stays.
    """
    try:
        old = json.loads(out.read_text())
    except Exception:                                   # noqa: BLE001
        return True
    if old.get("found") and not fresh.get("found"):
        print(f"        kept: {out.name} holds {len(old.get('exhibitors') or [])} "
              f"name(s) from an earlier sweep and today's read refused - not "
              f"overwriting a find with a refusal ({str(fresh.get('why'))[:50]})")
        return False
    flags = {(e.get("name") or "").lower(): e for e in (old.get("exhibitors") or [])
             if "is_govtech" in e}
    for e in fresh.get("exhibitors") or []:
        was = flags.get((e.get("name") or "").lower())
        if was:
            for k in ("is_govtech", "is_govtech_why"):
                if k in was:
                    e[k] = was[k]
    return True


def restage(files: list) -> int:
    """Re-run the name filter over what is already staged. No network.

    For the day the filter learns something - a chevron, a tier label - and
    the 26 files on disk were read before it did. Duplicates that the fix
    now folds together keep whichever copy carried a ruling.
    """
    for f in files:
        d = json.loads(f.read_text())
        if not d.get("found"):
            continue
        before = d.get("exhibitors") or []
        kept: dict[str, dict] = {}
        dropped = []
        host = host_of(d.get("event_tag"))
        bare = strip_booths([e.get("name") or "" for e in before])
        for e, raw in zip(before, bare):
            name = clean(raw)
            if not looks_like_a_name(name, host):
                dropped.append(e.get("name"))
                continue
            key = name.lower()
            row = dict(e, name=name)
            if key in kept:
                # keep the ruling, whichever copy carried it, and any website
                have = kept[key]
                for k in ("is_govtech", "is_govtech_why"):
                    if k not in have and k in row:
                        have[k] = row[k]
                if not have.get("website") and row.get("website"):
                    have["website"] = row["website"]
                continue
            kept[key] = row
        after = sorted(kept.values(), key=lambda r: r["name"].lower())
        if ([e.get("name") for e in after] == [e.get("name") for e in before]
                and not dropped):
            continue
        d["exhibitors"] = after
        d["restaged"] = (f"{len(before)} -> {len(after)} names after re-filtering "
                         f"on {dt.date.today().isoformat()}")
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1) + "\n")
        json.loads(tmp.read_text())
        tmp.replace(f)
        print(f"  {f.name[:40]:42} {len(before):4} -> {len(after):4}"
              + (f"   dropped: {', '.join(str(x)[:22] for x in dropped[:4])}" if dropped else ""))
    return 0


# A NAVIGATION TREE, WHICH IS WHAT A SPONSORSHIP PAGE YIELDS. Seven APA
# chapter "directories" were accepted by find_event_directories on 2026-09-02
# and every one of them was the chapter site's own menu: 146 names off APA
# Florida, of which the companies numbered zero - "Knowledge Center",
# "Sections Overview", "Back to Main Menu", "Atlantic Coast Section". Wiring
# those into conferences.json would have published seven conference pages
# whose exhibitors were menu items.
#
# MEASURED BEFORE USE, against the 26 staged floors on disk that produced real
# companies, and against those seven pages:
#
#              worst real floor      the seven APA pages
#   nav        0.05  (NASWA)         0.315 0.234 0.227 0.237 (four of seven)
#   tree       0.053 (NAFA)          0.247 0.219 0.172 0.152 (the same four)
#
# The threshold is 0.10, roughly twice the worst real floor and half the
# lowest offender. It catches four of the seven outright; the other three are
# smaller menus without the "Overview" habit and are caught by the grade rule
# in find_event_directories instead.
#
# TWO OTHER SIGNALS WERE MEASURED AND DO NOT WORK. Written down so the next
# person does not spend the afternoon I spent:
#
#   THE LINK PROFILE. "A real exhibitor list links out, navigation links back
#   into the site" is false. AWWA ACE is 97% same-host and NAHRO 98%, because
#   a real directory links each exhibitor to its own detail page on the
#   association's site. Same-host share separates nothing.
#
#   OVERLAP WITH THE HOME PAGE. "Navigation appears on every page, so subtract
#   the home page" is elegant and confounded: AIRA, NAMPI, NASWA, NHCAA and
#   NAFA all score 1.0 because their exhibitor list IS on the home page or one
#   redirect from it. It cannot tell a menu from a single-page site.
NAV_TAIL = re.compile(r"\boverview$|^back to\b|\bhome$|^skip to\b", re.I)
NAVISH = 0.10


def navish(names: list[dict]) -> tuple[float, float]:
    """(nav-tail share, name-tree share) - how much of this list is a menu.

    NAV TAIL: a name ending in "Overview" or starting with "Back to" is a
    menu's own furniture and never a company.

    NAME TREE: a name that is another harvested name plus a trailing word -
    "Sections" and "Sections Overview" and "Sun Coast Section" - is a
    hierarchy. Exhibitor lists are flat; menus nest.
    """
    if not names:
        return 0.0, 0.0
    n = len(names)
    nav = sum(1 for x in names if NAV_TAIL.search(x.get("name") or ""))
    low = {(x.get("name") or "").lower() for x in names}
    tree = sum(1 for a in low if a and any(a != b and b and a.startswith(b + " ")
                                           for b in low))
    return nav / n, tree / n


def reads_as_a_menu(names: list[dict]) -> str | None:
    """Why this list is the site's navigation, or None if it is not."""
    nav, tree = navish(names)
    if nav >= NAVISH:
        return (f"{nav:.0%} of the names end in 'Overview' or start with 'Back to' "
                f"- this is the site's own menu, not a list of exhibitors")
    if tree >= NAVISH:
        return (f"{tree:.0%} of the names are another name plus a word "
                f"(\"Sections\", \"Sections Overview\") - a menu nests, an "
                f"exhibitor list is flat")
    return None


def never_swept(c: dict) -> bool:
    """A row whose floor files nobody: ruled out of scope (`sled: false`), or
    listed and never swept (`harvest: false`: an ADJACENT, PROVIDER or
    INDUSTRY event, or a training one; owner, 2026-09-09). The sweep, the
    browser read and the intake all ask this one question."""
    return c.get("sled") is False or c.get("harvest") is False


def ready(conferences: list, tag: str | None, images: bool = False) -> list:
    # NEVER SWEPT MEANS HERE TOO, as render_floor.walled already held for
    # `sled: false`: TechConnect 2027 carried a reader's directory address and
    # this took it, so its floor reached the suppliers file (2026-10-26,
    # reverted).
    out = [c for c in conferences if not never_swept(c) and (c.get("exhibitor_url")
           or (images and c.get("fetchability") == "image" and c.get("exhibitor_url_candidate")))]
    if tag:
        out = [c for c in out if (c.get("event_tag") or "") == tag]
    return out


def sweep(conf: dict) -> dict:
    """One conference, one page, one staged result - found or honestly not."""
    image = conf.get("fetchability") == "image"
    url = conf.get("exhibitor_url") or (conf.get("exhibitor_url_candidate") if image else None)
    staged = {
        "event_tag": conf.get("event_tag"),
        "conference": conf.get("conference"),
        "found": False,
        "source_url": url,
        "source_note": "",
        "exhibitors": [],
        "why": None,
    }
    try:
        page = ats._page(url).text
    except Exception as exc:                            # noqa: BLE001
        staged["why"] = f"{type(exc).__name__}: {str(exc)[:90]}"
        return staged

    unread: list = []
    names = harvest(page, host_of(conf.get("event_tag")), logos_from=url if image else None,
                    unread_logos=unread)
    doubt = suspicious(names)
    grade, note = quality(names)
    if not doubt and grade == "doubtful":
        doubt = (f"this url is the association's own site rather than a list "
                 f"of who exhibits at it ({note})")
    if doubt:
        staged["why"] = doubt
        staged["source_note"] = (
            f"{len(page)//1024}KB read, {len(names)} candidate name(s) after "
            f"filtering. Not staged as exhibitors: {doubt}")
        return staged

    staged["found"] = True
    staged["quality"] = grade
    staged["exhibitors"] = sorted(names, key=lambda r: r["name"].lower())
    with_site = sum(1 for n in names if n["website"])
    staged["source_note"] = (
        f"Read from {url} by scripts/sweep_exhibitors.py. {len(names)} name(s), "
        f"{with_site} with a website the page itself linked. Quality: {grade} "
        f"({note}). Names only - "
        f"nothing here decides whether a company is govtech, and no website "
        f"was inferred where the page did not give one."
        + (f" {len(unread)} linked logo(s) were not read as a name, so a person "
           f"should look: {', '.join(repr(a) for a in unread[:12])}." if unread else ""))
    if unread:
        staged["logos_unread"] = unread
    return staged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="what is ready to sweep")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tag", help="one event tag")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--restage", action="store_true",
                    help="re-filter the staged files on disk; no network")
    ap.add_argument("--images", action="store_true",
                    help="also read rows a reader marked as a logo wall (fetchability image), "
                         "from their candidate address, naming each linked logo by its alt text")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.restage:
        return restage(sorted(DATA.glob("exhibitors_*.json")))

    conf = json.loads((DATA / "conferences.json").read_text())["conferences"]
    todo = ready(conf, a.tag, images=a.images)

    if a.list or not (a.all or a.tag):
        by = Counter(str(c.get("fetchability")) for c in todo)
        print(f"{len(conf)} conference(s); {len(todo)} carry an exhibitor_url")
        print("  fetchability:", dict(by))
        print(f"  {len(conf) - len(todo)} have no exhibitor_url and need one found\n")
        for c in todo:
            done = (DATA / f"exhibitors_{re.sub(r'[^A-Za-z0-9]+', '_', c.get('event_tag') or '')}.json")
            print(f"  {str(c.get('event_tag'))[:24]:26} {str(c.get('fetchability')):11} "
                  f"{'staged' if done.exists() else '':7} {str(c.get('exhibitor_url'))[:52]}")
        if not (a.all or a.tag):
            print("\n  --all to sweep them, --write to stage the results.")
        return 0

    if a.limit:
        todo = todo[:a.limit]

    found = missed = 0
    for c in todo:
        st = sweep(c)
        tag = st["event_tag"] or c.get("conference")
        if st["found"]:
            found += 1
            print(f"  {st.get('quality','?'):9} {str(tag)[:24]:26} "
                  f"{len(st['exhibitors']):4} name(s)")
        else:
            missed += 1
            print(f"  {'refused':9} {str(tag)[:24]:26} {str(st['why'])[:58]}")
        if a.write:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", str(tag)).strip("_")
            out = DATA / f"exhibitors_{safe}.json"
            if not merge_into(st, out):
                continue
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1) + "\n")
            try:
                json.loads(tmp.read_text())
            except json.JSONDecodeError:
                tmp.unlink(missing_ok=True)
                print(f"        refused: {out.name} did not parse", file=sys.stderr)
                continue
            tmp.replace(out)

    print(f"\n  {found} directory(ies) read, {missed} not")
    if a.write:
        print(f"  staged into data/exhibitors_*.json. NOTHING has reached "
              f"companies.json - run conference_intake.py on a file to do that.")
    else:
        print("  LOOKED ONLY. Re-run with --write to stage them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
