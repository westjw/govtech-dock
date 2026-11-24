#!/usr/bin/env python3
"""Which job board does a company's OWN website point to, and where on the page?

THE OWNER'S RULE, AND THE HALF OF IT THIS FILE IS (2026-11-18)

A program may wire a found board to a company only when two separate checks
agree: the company's own website points to the board, and the board itself
names that company as the employer. Parent-company boards, name mismatches and
anything doubtful go to his "Boards we found" queue instead.

This file is the FIRST check, with its evidence written down. It asks no board
anything (scripts/verify_boards.py is the second check), wires nothing, and
never writes data/. It writes one results file, wherever --out says, and the
wiring door reads it.

WHY THE OLDER FINDERS COULD NOT BE THE FIRST CHECK

find_embedded_ats, find_boards and discover_ats all read "a page" and report
any board the markup names. None of them asks whose page it was:

  - they start from the careers address on file, and for 87 companies that
    address is not on the company's own site at all: a vendor's host, or a
    parent's site (Noggin's is Motorola's);
  - they let the library follow redirects and throw the landing address away,
    so a careers path that hops to the acquirer's site reads as the company's
    own page (Simpleview's goes to Granicus);
  - they keep what was matched and how often, never WHERE: an iframe that IS
    the careers page and a slug in a partner's logo link weigh the same
    (Loop's page carried another company's BambooHR address).

So here every page is read one redirect hop at a time, the address it LANDED
on is kept, and a reference counts as the company's pointer only when that
landing address is on the company's own website host. A reference seen
anywhere else is kept too, under "off_site", so a person can see it - it is
never a candidate.

"THEIR OWN SITE" IS NARROWER THAN "THE SAME HOST" (the blind review of
2026-11-18). Of the 1,968 companies on the list, 41 are on file with a
website that is a page inside a larger site (MILO Range is faac.com/milo/),
19 with one that is a named part of a host (security.gallagher.com), and 12
with the very address another record gives. Judged by host alone, FAAC's
careers page was MILO's own page and FAAC's whole board was MILO's
candidate, word for word what FAAC's own record got. So the page must be inside the part of the host the website on file
names, and must not be a page some other record on file has as good a claim
to; `Whose` below is the whole rule. What fails it is still read once and
still written down - under off_site, with the reason in the page's note.
(That errs toward a person: a company on file at about.acme.com whose
careers page is on acme.com is not told apart from a product line on file
at product.parent.com, and both go to his queue.)

WHAT A CANDIDATE IS, AND IS NOT

A candidate says: this address, on this company's own site, names this board,
in this kind of element, this many times. It is a claim about the PAGE. It is
not a claim about the board. Prepared's own careers page links Axon's
Greenhouse board because Axon bought them: check one passes there and only
the board's stated name catches it. Nothing in this file's output is a reason
to wire anything by itself.

WHAT IT NEVER SAYS

"No board". A page robots.txt closes, a bot wall, a timeout and a site that
left for another host are each written down as what they were. A company with
no page of status "read" was not looked at, and its empty candidate list
means nothing.

  python3 scripts/find_site_boards.py --out /tmp/boards.json --limit 40
  python3 scripts/find_site_boards.py --out /tmp/boards.json --only acme,other-co --show
  python3 scripts/find_site_boards.py --out /tmp/rendered.json --render --shard 1/8

THE RESULTS FILE (the wiring door reads exactly this):

  {"generated": ISO time, "mode": "static" | "render",
   "companies": {"<company id>": {
     "name", "website", "was": the ats type on file ("html" or "unknown";
         another type only for a company named with --only --include-wired),
     "pages": [{"asked", "final", "same_site", "status", "note"}],
         status: read | robots | blocked | error | left-site
     "candidates": [{"type", "ref", "element", "saw", "times", "page", "hop"}],
         element: href | iframe | script | text | request | frame | redirect
     "off_site":   [the same shape, seen on a page that is not their own site],
     "elsewhere":  [{"vendor", "saw", "page", "element", "times"}],
     "left_site":  [{"from", "to_host"}],
     "job_like_links": int or null, "says_no_openings": bool,
     "error": str or null}}}

`ref` is exactly what that type's fetcher in ats.py takes: a slug, the whole
Paylocity address, an ADP cid, and for Workday the list [tenant, pod, site].
A slug is spelt as the page spelt it wherever the platform tells spellings
apart (Lever and SmartRecruiters do), and that is all this file can know: it
asks no board, so whoever verifies the ref may find a page wrote it wrong.
`page` is the address the page landed on; for a redirect it is the own-site
address whose answer pointed at the board, and `saw` is where it pointed.
For an off_site row it is the address the reference was really seen on: the
other site's page, or the other site's address that did the redirecting.

`generated` is when the OLDEST record in the file was read. A run that picks
a file up again keeps the first run's stamp, so nothing in the file is older
than its date says.

A page's status: `read` (landed on their own site and was read); `left-site`
(landed somewhere that is not their own site, read or deliberately not:
another host, which `left_site` names, or a part of the same host that is
not theirs, which the note explains; a redirect straight to a board is one
of these AND a candidate); `robots`, `blocked`, `error` (not read, with the
reason in `note`, and same_site still saying where it was).

ONE CASE READS `read` AND STILL GIVES NO CANDIDATE: two records on file with
the same website. The site was read, and it is theirs - and the other
record's just as much, so which of them a board belongs to is not something
the site can say. Its references go to off_site and the page's note names
the other record.

`elsewhere` is a tally, not a finding about ownership. Its vendors are the
platforms ats.py has no reader for (jobvite, ukg, pinpoint ...); the forms of
a platform it does read that name no board (gusto-posting, paylocity-other,
adp-other, workday-no-site, workday-myworkdaysite, greenhouse-shortlink) or
that sit where its reader does not ask (greenhouse-eu, lever-eu); where a
company posts instead (linkedin, indeed ...); and `other:<site>` for a job
frame or a careers link that leaves for a host nothing here knows.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import hashlib
import html as html_lib
import json
import os
import pathlib
import re
import socket
import sys
import threading
import time
import typing
import urllib.parse
import weakref

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ats            # noqa: E402
import discover_ats   # noqa: E402
import find_boards    # noqa: E402
import privacy        # noqa: E402
import render_fetch   # noqa: E402
import robots         # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# How many addresses one company may cost. A sweep is a crawl whoever starts
# it, and the point is to read where a site's careers link goes, not to walk
# the site. A browser page costs the site every script and stylesheet it
# loads, so the browser gets one fewer.
PAGES = {"static": 4, "render": 3}
# Seconds one company may hold the run. requests' timeout governs one socket
# read, not a request, so a server trickling bytes needs a clock of its own
# (discover_ats lost a 10-hour sweep to exactly that).
COMPANY_SECONDS = {"static": 75, "render": 150}
CONNECT_TIMEOUT, READ_TIMEOUT = 5, 10
# Past this a body is not a careers page. Larger than discover_ats' cap on
# purpose: that sweep ran twelve workers and needed the speed; this one reads
# one company at a time, and a board's iframe is often the last thing on a
# long page.
PAGE_CAP = 1_500_000
SAVE_EVERY = 5
# Browser pages open at one time, in the one browser a sweep starts. Each is
# somebody's whole page with its scripts and frames; two is what one runner
# renders without the pages starving each other, and it is the number the
# workflow's timings were measured at.
RENDER_AT_ONCE = 2


# ------------------------------------------------------------- whose page

def _host(url: str) -> str:
    u = url or ""
    try:
        h = urllib.parse.urlparse(u if "//" in u else "https://" + u).netloc
    except ValueError:              # "http://[not-an-address": a page can write anything
        return ""
    return h.lower().split(":")[0].removeprefix("www.")


def own_site(page: str, website: str) -> bool:
    """The page's host is the website's host, or one sits under the other
    (careers.acme.com and acme.com).

    THE SAME RULE AS page_belt._same_site, word for word, and selftest holds
    the two together. It is written out here because page_belt imports the
    whole admin server, and a finder that runs on a bare CI machine should
    not need the admin to know what a host is.

    IT IS THE FIRST HALF OF WHOSE PAGE AN ADDRESS IS, AND NOT ENOUGH ALONE.
    It decides which links the walk may follow and which addresses it may
    ask. Whether what a page says is the COMPANY'S pointer is `Whose.of`.
    """
    a, b = _host(page), _host(website)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


# A locale in an address is not a part of a site: acme.com/en-us is acme.com,
# and so is us.acme.com. THE LIST IS SHORT ON PURPOSE. A word left off it
# costs a company whose site lives under /fi its automatic answer (its
# careers page files under off_site, and a person looks). A word wrongly on
# it turns a product line's path into "the whole host" and hands that
# product the parent's board, which is the mistake this section exists to
# stop. "it" is Italian and it is also somebody's IT division: not here.
_LOCALE_WORDS = frozenset({"en", "us", "uk", "gb", "ca", "au", "de", "fr", "es", "eu",
                           "usa", "eng", "intl", "global"})
# "en-US", "fr_ca": the one shape that is a locale whatever its letters.
_LOCALE_ONLY = re.compile(r"^[a-z]{2}[-_][a-z]{2}$", re.I)
# The page a site's front door answers at is not a part of the site either.
_FRONT_DOOR = frozenset({"home", "index", "default", "main", "welcome"})
_FILE_NAME = re.compile(r"\.[a-z0-9]{2,5}$", re.I)
_TWO_PART_TLD = {"co", "com", "org", "net", "gov", "ac", "edu"}


def _site_of(host: str) -> str:
    """acme.co.uk from careers.acme.co.uk. By rule of thumb, not by the public
    suffix list: near enough to name a platform in a tally, to say which
    label of a host could be a locale, and to keep two records of one site
    on one machine."""
    labels = host.lower().split(".")
    n = 3 if (len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _TWO_PART_TLD) else 2
    return ".".join(labels[-n:])


def _locale(word: str) -> bool:
    w = word.lower()
    if w in _LOCALE_WORDS or _LOCALE_ONLY.match(w):
        return True
    a, sep, b = w.replace("_", "-").partition("-")
    return bool(sep) and a in _LOCALE_WORDS and b in _LOCALE_WORDS      # usa_en


def _bare(host: str) -> str:
    """A host with a locale label in front taken off: us.acme.com is acme.com."""
    labels = host.split(".")
    floor = len(_site_of(host).split("."))
    while len(labels) > floor and _locale(labels[0]):
        labels.pop(0)
    return ".".join(labels)


def _segments(url: str) -> list[str]:
    """An address's path, lower case, with any locale in front of it taken
    off: /us/en/surveillance/x is surveillance, x."""
    try:
        path = urllib.parse.urlsplit(url if "//" in url else "https://" + url).path
    except ValueError:
        return []
    segs = [s for s in path.lower().split("/") if s]
    while segs and _locale(segs[0]):
        segs.pop(0)
    return segs


class Ground(typing.NamedTuple):
    """The part of the web a website on file names: a host and, when the
    website is a page inside a larger site, the path it lives under ("" is
    the whole host)."""
    host: str
    base: str


def ground(website: str) -> "Ground | None":
    """What a website on file claims, and no more than it claims.

      www.faac.com/milo/            faac.com, under /milo only
      security.gallagher.com        that host, and not gallagher.com above it
      www.sage.com/en-us/           all of sage.com: a locale is not a part
      lexisnexis.com/en-us/x.page   all of lexisnexis.com: a file in the root
    """
    host = _host(website or "")
    if not host:
        return None
    segs = _segments(website)
    if segs and _FILE_NAME.search(segs[-1]):
        segs.pop()
    if segs and segs[-1] in _FRONT_DOOR:
        segs.pop()
    return Ground(_bare(host), "/" + "/".join(segs) if segs else "")


def _within(page: str, g: Ground) -> bool:
    """Is this address in that part of the web: on the host or under it, and
    under the path when there is one."""
    h = _bare(_host(page))
    if not h or not (h == g.host or h.endswith("." + g.host)):
        return False
    if not g.base:
        return True
    rel = "/" + "/".join(_segments(page))
    return h == g.host and (rel == g.base or rel.startswith(g.base + "/"))


def site_index(companies: list) -> dict:
    """Every website on file by host: {host: [(Ground, company id), ...]}.

    FROM EVERY RECORD, wired or not and in scope or not. A record that
    already reads its own board still owns its site, and the product line on
    file inside that site must not be handed the board it already holds.
    """
    out: dict = {}
    for c in companies:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        g = ground(str(c.get("website") or "").strip())
        if g:
            out.setdefault(g.host, []).append((g, c["id"]))
    return out


class Whose:
    """Whose page an address is, for one company.

    THE HOST IS NOT THE ANSWER (the blind review of 2026-11-18). Companies
    are on file with a website that is a page inside another site, with one
    that is a named part of another site's host, and eleven hosts are the
    website of two or three records at once. Judged by host, the larger
    site's careers page was every one of those companies' "own page", and
    one board was handed to two records with nothing to say so.

    `of(address)` answers one of four things, and only the first makes a
    candidate:

      own     inside what this company's website names, and no other record
              on file has as good a claim to the page
      shared  inside it, and another record on file gives the SAME website:
              the site is read as theirs, but its references are filed under
              off_site, because the site cannot say which record a board is
      part    on the same host family and not theirs: outside the path their
              website names (faac.com/about/careers for faac.com/milo/), on
              the host their website is only a part of (gallagher.com for
              security.gallagher.com), or inside a part another record
              names. Read once, filed under off_site, never walked further
      other   another site altogether

    With no list of the other records (`on_file`) the first and third rules
    still hold; the two that need the list cannot, so a sweep always gives it.
    """

    def __init__(self, company: dict, on_file: dict | None = None):
        site = str(company.get("website") or "").strip()
        if site and "//" not in site:
            site = "https://" + site
        self.site, self.id = site, company.get("id")
        self.mine = ground(site)
        self.on_file = on_file or {}
        self._known: dict[str, tuple[str, str]] = {}

    def of(self, url: str) -> tuple[str, str]:
        hit = self._known.get(url)
        if hit is None:
            hit = self._known[url] = self._judge(url)
        return hit

    def theirs(self, url: str) -> bool:
        return self.of(url)[0] in ("own", "shared")

    def _judge(self, url: str) -> tuple[str, str]:
        me = self.mine
        if me is None or not own_site(url, self.site):
            return "other", ""
        h = _host(url)
        if not _within(url, me):
            if me.base:
                return "part", (f"on {h}, outside {me.host}{me.base}, which is all of that "
                                f"site the website on file names")
            return "part", (f"on {h}, and the website on file ({_host(self.site)}) is "
                            f"only a part of that site")
        mine = (me.host.count("."), me.base.count("/"))
        better, equal = [], []
        labels = _bare(h).split(".")
        for i in range(max(1, len(labels) - 1)):
            for g, cid in self.on_file.get(".".join(labels[i:]), ()):
                if cid == self.id or not _within(url, g):
                    continue
                theirs = (g.host.count("."), g.base.count("/"))
                if theirs > mine:
                    better.append(cid)
                elif theirs == mine:
                    equal.append(cid)
        if better:
            return "part", (f"inside the part of this site that is on file as another "
                            f"record's website ({', '.join(sorted(set(better))[:3])})")
        if equal:
            return "shared", (f"this website is also on file for "
                              f"{', '.join(sorted(set(equal))[:3])}, and a site cannot say "
                              f"which record a board belongs to")
        return "own", ""


# ------------------------------------------------------------ the patterns
#
# NOT A FIFTH TABLE. Four hand-kept tables already say how a board looks in a
# page (discover_ats.MARKERS, add_company.ATS_MARKERS, find_embedded_ats.SIGS,
# find_boards.PATTERNS) and they have drifted apart. So the forms those tables
# already read in the right shape are taken from them BY REFERENCE: every
# entry of find_boards.PATTERNS (each one read off a real page), and the
# slug-shaped entries of discover_ats.MARKERS. Only what no table reads right
# is written here, and each of those says why.
#
# ONE CHECK HOLDS ALL OF IT: selftest runs every pattern below, borrowed or
# written here, against a sample page and hands the result to the real
# fetcher with the network stubbed. A pattern whose output the fetcher would
# ask at the wrong address fails there - which is how find_embedded_ats lost
# every Workday board for three months ([tenant, SITE, pod]).

L = discover_ats.L          # one host label, pinned at its start (see there)
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# "en-US/", "fr-CA/": a locale in front of a Workday site or a Rippling board
# is not the site. discover_ats skips only en-US. (_LOCALE_ONLY, above, is
# the same shape standing alone.)
LOCALE = r"(?:[a-z]{2}-[a-z]{2}/)?"

# A literal every form of a platform's address contains. A pattern only runs
# over text that holds its hint, which is what keeps sixty patterns over a
# megabyte of somebody's JavaScript from costing anything. selftest drives
# its samples through this gate, so a wrong hint cannot hide a pattern.
HINTS = {
    "greenhouse": ("greenhouse.io",), "lever": ("lever.co",),
    "ashby": ("ashbyhq.com",), "workable": ("workable",),
    "recruitee": ("recruitee.com",), "breezy": ("breezy.hr",),
    "smartrecruiters": ("smartrecruiters.com",), "bamboohr": ("bamboohr.com",),
    "workday": ("myworkdayjobs.com",), "rippling": ("rippling.com",),
    "jazzhr": ("applytojob.com", "jazz.co"), "icims": ("icims.com",),
    "paylocity": ("paylocity.com",), "adp": ("workforcenow.adp.com",),
    "gusto": ("jobs.gusto.com",), "gem": ("jobs.gem.com",),
    "hirebridge": ("hirebridge.com",),
    "applicantpro": ("applicantpro.com", "isolvedhire.com"),
    "teamtailor": ("teamtailor",),
    "pinpoint": ("pinpointhq.com",),
    "zohorecruit": ("zohorecruit.",),
    "comeet": ("comeet.",),
    "jobvite": ("jobvite.com",),
    "jobvitefeed": ("jobvite.com",),
}
# Jibe (iCIMS Talent Cloud) lives on the company's own careers host and its
# address names nobody. What gives it away is the script host its pages load.
# The ref is then the host of the page it was seen on, which is what
# ats.fetch_jibe takes.
JIBE_MARK = "jibecdn.com"

# A host is lower case whatever a page wrote, so the platforms that name a
# board by its host are lowered. Everything else is kept as the page spelled
# it, because guessing a case is guessing a board: SmartRecruiters' company
# id tells Xplor from xplor, and SO DOES LEVER. This file lowered Lever's
# slugs at first, on the repo's own word that they are lower case. Asked on
# 2026-11-18, api.lever.co answered nine postings for "Wayground", the
# spelling on file and on that company's page, and HTTP 404 for "wayground":
# the lowered ref would have lost a board the page named correctly. Two of
# the 33 Lever boards on file are spelt with a capital.
LOWERED = {"recruitee", "breezy", "bamboohr", "jazzhr", "icims", "pinpoint"}

# The vendor's own furniture, never a customer. Three tables' worth, joined
# rather than copied, plus what turned up here: Workable's /j/ short links,
# iCIMS' image hosts (c-123-acme.i.icims.com), Breezy's asset host, the
# "oneclick-ui" segment of a SmartRecruiters apply link, and the placeholder
# names that vendors' own help pages use.
FURNITURE = (set(discover_ats.RESERVED) | set(find_boards.NOT_A_SLUG)
             | set(discover_ats.VENDOR_HOSTS) | {
    "j", "i", "v0", "v1", "v3", "wday", "apply", "about", "css", "img",
    "images", "media", "fonts", "public", "widget", "widgets", "search",
    "postings", "posting", "oneclick-ui", "sr-jobs", "ni", "job-boards",
    "job_boards", "attachments", "assets-cdn", "careers-page", "resources",
    "marketing", "partners", "community", "learn", "id", "auth", "secure",
    "sso", "signin", "signup", "accounts", "email", "mail", "go", "info",
    "get", "try", "privacy", "terms", "legal", "pricing", "contact",
    "yourcompany", "your-company", "companyname", "company-name", "example",
    "undefined", "null", "none", "true", "false",
    # what a browser asks a vendor's host for besides the board: Cloudflare's
    # /cdn-cgi/ (JustFOIA's Rippling frame made four such requests and the
    # first run read "cdn-cgi" as a second board), build output, the icon
    "cdn-cgi", "_next", "favicon", "robots", "sitemap", "manifest", "dist",
    "build", "icons", "locales", "graphql"})
_NUMBERED = re.compile(r"^(?:www|cdn|static|assets?|images?|img|media|app|api|files?)\d*$")


def _furniture(slug: str) -> bool:
    low = slug.lower()
    return low in FURNITURE or bool(_NUMBERED.match(low))


class _Pat(typing.NamedTuple):
    kind: str
    rx: "re.Pattern"
    make: typing.Callable       # match -> (ref, key) or None
    origin: str                 # which table it came from, for selftest's words


def _slug(kind: str):
    """A maker for the plain case: the first group that matched is the slug."""
    def make(m):
        s = next((g for g in m.groups() if g), "").strip().rstrip("._-")
        if kind == "ashby" and "%" in s:
            s = urllib.parse.unquote(s)        # Flock's board is "flock safety"
        if kind in LOWERED:
            s = s.lower()
        if not s or _furniture(s) or s.lower() == kind:
            return None
        if kind == "rippling" and _LOCALE_ONLY.match(s):
            return None
        if kind == "gem" and not ats._GEM_SLUG.match(s):
            return None
        return s, s.lower()
    return make


def _workday(m):
    # (tenant, POD, site): the order ats.fetch_workday unpacks. The pod is
    # never guessable, so it is taken from the address or not at all.
    tenant, pod, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
    if (_furniture(tenant) or site.lower() == "wday" or _LOCALE_ONLY.match(site)):
        return None
    return [tenant, pod, site], f"{tenant}/{site.lower()}"


def _paylocity(m):
    # THE WHOLE ADDRESS IS THE REF: fetch_paylocity GETs it, there is no API
    # to build one from a slug. Two shapes are in use: /All/<uuid>/<Name> (all
    # 38 on file) and /List/<number>/<Name> (FAAC's, seen 2026-11-18).
    which = "All" if m.group(1).lower() == "all" else "List"
    ident, tail = m.group(2).lower(), (m.group(3) or "").rstrip(".")
    ref = f"https://recruiting.paylocity.com/recruiting/jobs/{which}/{ident}"
    return (f"{ref}/{tail}" if tail else ref), ident


def _adp(m):
    # THE cid IS THE REF, with "|<ccId>" only when the link names a career
    # centre other than ADP's default (ats.fetch_adp reads both forms).
    cid = m.group(1).lower()
    cc = re.search(r"\bccId=([0-9]+_[0-9]+)", m.group(0), re.I)
    cc = cc.group(1) if cc else ""
    return (f"{cid}|{cc}" if cc and cc != ats.ADP_CCID else cid), cid


def _gusto(m):
    seg = m.group(1)
    return (seg, seg.lower()) if ats._GUSTO_REF.match(seg) else None


def _hirebridge(m):
    # The company id, from any address on Hirebridge's three hosts that
    # carries one (an iframe of the career centre, a link to the classic
    # list, a posting, the job-alerts page). Never a name or slug: there is
    # none in these addresses.
    return m.group(1), m.group(1)


def _applicantpro(m):
    # The whole tenant host is the ref: the label alone does not say which of
    # the platform's two domains the board is on, and it is not the
    # company's name ("casellejobs").
    label = m.group(1).lower()
    if _furniture(label):
        return None
    host = f"{label}.{m.group(2).lower()}.com"
    return host, host


# Teamtailor's own hosts under teamtailor.com, never a customer's career site
# ("tt" is the "Powered by Teamtailor" link every career site carries, seen on
# Voda, AM and Labster's on 2026-11-21)
_TEAMTAILOR_OWN = {"tt", "app", "www", "career", "careers", "scripts", "assets-aws", "images",
                   "videos", "screenshots", "fonts", "docs", "api", "support", "status",
                   "blog", "help", "partners", "marketplace", "go", "na", "eu"}


def _teamtailor_hosted(m):
    # <label>.teamtailor.com, or <label>.na.teamtailor.com: the region label
    # is part of the site's address (fonrochelightingamerica.na), and the
    # older patterns elsewhere read "na" as the slug.
    label, region = m.group(1).lower(), (m.group(2) or "").lower()
    if label in _TEAMTAILOR_OWN or _furniture(label):
        return None
    host = f"{label}{region}.teamtailor.com"
    return f"https://{host}", host


def _zoho_link(m):
    # a link to the career site or to a posting on it: the site is
    # https://<portal>.zohorecruit.<tld>/jobs/<PageName>, whatever follows
    ref = f"https://{m.group(1).lower()}/jobs/{m.group(2)}"
    if _furniture(m.group(1).split(".")[0]):
        return None
    return ref, ref.lower()


def _zoho_embed(m):
    # rec_embed_js.load({site: 'https://<portal>.zohorecruit.com', page_name:
    # 'Careers', ...}): NEXGEN's site names its board this way only. A bare
    # host is not a board without its page's name.
    body = m.group(1)
    site = re.search(r"\bsite\s*:\s*[\"'](https://[a-z0-9-]{1,63}\.zohorecruit\.(?:com|eu|in))/?[\"']",
                     body, re.I)
    page = re.search(r"\bpage_name\s*:\s*[\"']([A-Za-z0-9_-]{1,80})[\"']", body)
    if not site or not page:
        return None
    ref = f"{site.group(1).lower()}/jobs/{page.group(1)}"
    return ref, ref.lower()


_UID = r"[0-9A-F]{2}\.[0-9A-F]{3}"


def _comeet_pair(m):
    # the company's own iframe or API call, which hands its browser the uid
    # and the public token together
    uid, token = m.group(1).upper(), m.group(2).upper()
    return f"{uid}|{token}", uid.lower()


def _comeet_init(m):
    # COMEET.init({"token": ..., "company-uid": ...}): one pattern per key,
    # because Cognyte's object is not JSON (a trailing comma), and its
    # "company-name" is a display name, not the board
    body = m.group(1)
    t = re.search(r"[\"']token[\"']\s*:\s*[\"']([0-9A-F]{20,64})[\"']", body, re.I)
    u = re.search(rf"[\"']company-uid[\"']\s*:\s*[\"']({_UID})[\"']", body, re.I)
    if not t or not u:
        return None
    return f"{u.group(1).upper()}|{t.group(1).upper()}", u.group(1).lower()


def _comeet_hosted(m):
    slug, uid = m.group(1), m.group(2).upper()
    if _furniture(slug):
        return None
    return f"https://www.comeet.com/jobs/{slug}/{uid}", uid.lower()


def _jobvite_account(m):
    # the company id, exactly as the page wrote it: ids mind case (q6NaVfwI),
    # and one that is not in Jobvite's shape is no account this reads
    account = m.group(1)
    return (account, account) if ats.JOBVITE_ACCOUNT.fullmatch(account) else None


def _here(kind, pattern, make=None):
    return _Pat(kind, re.compile(pattern, re.I), make or _slug(kind), "find_site_boards")


# Marker kinds whose first group is a slug in the shape the fetcher takes.
# NOT borrowed, and why: workday (skips only en-US, and has no form for the
# API address a rendered page calls), rippling (reads "embed" as the board in
# ats.rippling.com/embed/<board>/jobs), paylocity (no scheme, and no /List/
# shape), oracle (ats.fetch_oracle reads site CX_1 only, so a tenant with a
# named site cannot be read: filed under "elsewhere" until it can).
_FROM_MARKERS = {"ashby", "greenhouse", "lever", "smartrecruiters", "workable",
                 "recruitee", "breezy", "bamboohr", "jazzhr", "icims"}

BOARDS: list[_Pat] = (
    [_Pat(kind, rx, _slug(kind), f"find_boards.PATTERNS[{i}]")
     for i, (kind, rx) in enumerate(find_boards.PATTERNS)]
    + [_Pat(kind, re.compile(pat, re.I), _slug(kind), f"discover_ats.MARKERS[{i}]")
       for i, (kind, pat) in enumerate(discover_ats.MARKERS) if kind in _FROM_MARKERS]
    + [
        # Greenhouse's application-form embed, and the older API host.
        # (boards-api. is in find_boards; the lookbehind keeps this one from
        # matching inside it.)
        _here("greenhouse", r"greenhouse\.io/embed/job_app\?for=([a-z0-9_-]{2,60})"),
        _here("greenhouse", r"(?<![a-z0-9-])api\.greenhouse\.io/v1/boards/([a-z0-9_-]{2,60})"),
        # Lever's API, which a page that draws its own list calls (Softdocs,
        # seen 2026-11-18).
        _here("lever", r"(?<![a-z0-9.-])api\.lever\.co/v0/postings/([a-z0-9_-]{2,60})"),
        # A board whose name has a space in it arrives as %20, and every
        # borrowed pattern stops at the percent sign: "flock".
        _here("ashby", r"jobs\.ashbyhq\.com/([a-z0-9._-]{1,60}(?:%20[a-z0-9._-]{1,60}){1,4})"),
        # Workable's two API addresses (the first is the one ats.py itself
        # asks) and the older <account>.workable.com careers host.
        _here("workable", r"apply\.workable\.com/api/v[13]/(?:widget/)?accounts/([a-z0-9-]{2,60})"),
        _here("workable", rf"({L})\.workable\.com"),
        _here("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]{2,60})"),
        _here("smartrecruiters", r"jobs\.smartrecruiters\.com/oneclick-ui/company/([A-Za-z0-9_-]{2,60})"),
        # Workday: any locale skipped, and the API address a rendered page
        # calls (/wday/cxs/<tenant>/<site>/jobs), where the site is the
        # FOURTH path segment and "wday" is not a site.
        _here("workday", rf"({L})\.(wd\d+)\.myworkdayjobs\.com/{LOCALE}([A-Za-z0-9_-]+)", _workday),
        _here("workday", rf"({L})\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[A-Za-z0-9_-]+/"
                         r"([A-Za-z0-9_-]+)", _workday),
        # Rippling: a locale and the word "embed" both sit in front of the
        # board (JustFOIA's iframe, seen 2026-11-18); and its API address.
        _here("rippling", rf"ats\.rippling\.com/{LOCALE}(?:embed/)?([a-z0-9-]{{2,60}})"),
        _here("rippling", r"api\.rippling\.com/platform/api/ats/v1/board/([a-z0-9-]{2,60})"),
        # JazzHR's embed script names the same account its board host does.
        _here("jazzhr", r"app\.jazz\.co/widgets/basic/create/([a-z0-9-]{2,60})"),
        _here("paylocity", rf"recruiting\.paylocity\.com/recruiting/jobs/(All|List)/"
                           rf"({UUID}|\d{{1,9}})(?:/([A-Za-z0-9_.-]+))?", _paylocity),
        # ADP: the careers page a person opens, and the API that page calls.
        _here("adp", r"workforcenow\.adp\.com/mascsr/default/(?:mdf/recruitment/recruitment\.html"
                     r"|careercenter/public/events/staffing/v1/job-requisitions)"
                     rf"\?[^\s\"'<>]*?\bcid=({UUID})[^\s\"'<>]*", _adp),
        _here("gusto", rf"jobs\.gusto\.com/boards/([a-z0-9][a-z0-9-]*-{UUID})", _gusto),
        _here("gem", r"jobs\.gem\.com/([a-z0-9][a-z0-9._-]{0,80})"),
        _here("hirebridge", r"(?<![a-z0-9-])(?:recruit|hbapi|alerts)\.hirebridge\.com/"
                            r"[^\s\"'<>]{0,200}?(?:[?&]|&amp;)cid=([0-9]{1,7})(?![0-9])", _hirebridge),
        _here("applicantpro", r"(?<![a-z0-9-])([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
                              r"\.(applicantpro|isolvedhire)\.com(?![a-z0-9-])", _applicantpro),
        _here("teamtailor", r"(?<![a-z0-9.-])([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.na)?"
                            r"\.teamtailor\.com(?![a-z0-9-])", _teamtailor_hosted),
        _here("pinpoint", r"(?<![a-z0-9.-])([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
                          r"\.pinpointhq\.com(?![a-z0-9-])"),
        _here("zohorecruit", r"(?<![a-z0-9.-])https?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                             r"\.zohorecruit\.(?:com|eu|in))/jobs/([A-Za-z0-9_-]{1,80})"
                             r"(?![A-Za-z0-9_-])", _zoho_link),
        _here("zohorecruit", r"rec_embed_js\.load\(\s*\{([^{}]{0,2000})\}", _zoho_embed),
        _here("comeet", rf"comeet\.co/jobs/({_UID})/(?:social|{_UID}/apply)\?[^\s\"'<>]*?"
                        r"\btoken=([0-9A-F]{20,64})", _comeet_pair),
        _here("comeet", rf"comeet\.com?/careers-api/2\.0/company/({_UID})/positions"
                        r"[^\s\"'<>]*?\btoken=([0-9A-F]{20,64})", _comeet_pair),
        _here("comeet", r"COMEET\.init\(\s*\{([^{}]{0,3000})\}", _comeet_init),
        _here("comeet", rf"(?<![a-z0-9-])www\.comeet\.com/jobs/([A-Za-z0-9_-]{{1,80}})/({_UID})"
                        r"(?=[/\"'?#\s<]|$)", _comeet_hosted),
        # a link to the career site or one of its postings; never its
        # __assets__ (the first character cannot be an underscore)
        _here("jobvite", r"(?<![a-z0-9.-])jobs\.jobvite\.com/([A-Za-z0-9][A-Za-z0-9_-]{0,80})"
                         r"(?![A-Za-z0-9_-])"),
        # A JOBVITE ACCOUNT NAMED BY ITS COMPANY ID on app.jobvite.com: the
        # widget's script and its frame (ci=), a talent network link, or a
        # CompanyJobs page (c=) - Tyler Technologies' careers pages, seen
        # 2026-11-23. Its board is the account's XML feed
        # (ats.fetch_jobvitefeed). Where their site names a jobs.jobvite.com
        # career site, that stays the board and these are jobvite-tenant, as
        # they were before the feed had a reader (jobvite_account_forms,
        # jobvite_account_board). hire.jobvite.com is left a tenant form: no
        # site measured here names an account there.
        _here("jobvitefeed", r"(?<![a-z0-9.-])app\.jobvite\.com/(?:CompanyJobs/(?:Careers|Job|Xml)\.aspx"
                             r"|TalentNetwork/[^\s\"'<>?#]{0,200}|__assets__/scripts/[^\s\"'<>?#]{0,200})"
                             r"\?(?:[^\s\"'<>#]{0,300}?[&;])?ci?=([A-Za-z0-9]{8})(?![A-Za-z0-9])",
              _jobvite_account),
    ])

# Job platforms nothing here can read. Kept as a TALLY, because which reader
# is worth writing next is a question the repo could not answer: no tool
# recorded what a careers page actually loads. Also here: forms of platforms
# we DO read that carry no board in the address (a Gusto posting, a Paylocity
# job page), and Workday's other host, which this door does not lift yet
# (ats.fetch_workday reads its pod-first refs since 2026-11-22; lifting one
# is the next step), named apart so they are not counted as missing readers.
_B = r"(?<![a-z0-9-])"
ELSEWHERE: list[tuple[str, str, "re.Pattern"]] = [
    (vendor, hint, re.compile(pat, re.I)) for vendor, hint, pat in (
        # A JOBVITE ADDRESS THAT NAMES AN ACCOUNT (a company code, c= or ci=, on
        # app./hire.jobvite.com: its CompanyJobs pages and talent network) or a
        # job of one (j?cj=) but no career site this door can read: another
        # account beside their board, and a doubt (blind review, 2026-11-22).
        # IN ANY PATH there (review 12's verifier, 2026-11-23): one the account
        # forms do not list - CompanyJobs/Apply.aspx?c=, an __assets__ page's
        # ci= - was "any other jobvite.com address", which names no board, so
        # beside the widget it was its furniture and as a link a note, and
        # the door wired the widget's account with another named beside it.
        ("jobvite-tenant", "jobvite.com",
         r"(?<![a-z0-9.-])(?:app|hire)\.jobvite\.com/[^\s\"'<>]{0,500}?\b(?:c|ci)=[A-Za-z0-9]{5,12}"),
        # a link to ONE job (j?cj=), whose account the link does not show
        ("jobvite-joblink", "jobvite.com",
         r"(?<![a-z0-9.-])(?:app|hire)\.jobvite\.com/j/?\?[^\s\"'<>]{0,300}?\bcj=[A-Za-z0-9]{5,12}"),
        ("jobvite-other", "jobvite.com", _B + r"jobvite\.com"),
        # A ZOHO TENANT'S HOST WITH NO PAGE NAMED is a tenant whose board this
        # door cannot name - another portal than theirs, maybe a parent's -
        # and is filed apart from Zoho's own hosts, as a doubt (blind review,
        # 2026-11-21: it was dropped beside their board, or kept as a note).
        ("zohorecruit-tenant", "zohorecruit.",
         r"(?<![a-z0-9.-])(?!(?:www|static|cdn|help|blog|api|accounts)\.)"
         r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.zohorecruit\.(?:com|eu|in)(?![a-z0-9-])"),
        ("zohorecruit-other", "zohorecruit.",
         r"(?<![a-z0-9-])(?:(?:www|static|cdn|help|blog|api|accounts)\.)?zohorecruit\.(?:com|eu|in)"
         r"(?![a-z0-9-])"),
        ("ukg", "ultipro.", _B + r"ultipro\.(?:com|ca)"),
        ("ukg", "ukg.net", _B + r"ukg\.net"),
        ("dayforce", "dayforcehcm.com", _B + r"dayforcehcm\.com"),
        ("paycom", "paycomonline.", _B + r"paycomonline\.(?:net|com)"),
        ("paycor", "recruitingbypaycor.com", _B + r"recruitingbypaycor\.com"),
        ("pinpoint-other", "pinpointhq.com", _B + r"pinpointhq\.com"),
        ("applicantpro-other", "applicantpro.com", _B + r"applicantpro\.com"),
        ("teamtailor-other", "teamtailor", _B + r"teamtailor(?:-cdn)?\.com"),
        # a Comeet address naming a company uid with no token to read it by
        ("comeet-tenant", "comeet.",
         r"comeet\.(?:co|com)/jobs/(?:[A-Za-z0-9_-]{1,80}/)?[0-9A-F]{2}\.[0-9A-F]{3}(?![0-9A-F])"),
        ("comeet-other", "comeet.", _B + r"comeet\.(?:co|com)(?![a-z0-9-])"),
        ("eightfold", "eightfold.ai", _B + r"eightfold\.ai"),
        ("phenom", "phenompeople.com", _B + r"phenompeople\.com"),
        # SAP SUCCESSFACTORS' LEGACY PORTALS, career<N>.successfactors.(com|eu)
        # and career<N>.sapsf.(com|eu|cn) (?company=...): robots.txt says
        # Disallow: /, so one is never a board, and the CSB site of the same
        # tenant cannot be worked out from it (CUES's, Genfare's and Gainwell's
        # careers links, 2026-11-20). A platform nothing here reads, as they
        # were before successfactors had a reader (the name moved off the
        # type's own: ELSEWHERE never files a type the crawl reads).
        ("successfactors-legacy", "successfactors.",
         r"(?<![a-z0-9.-])career[0-9]*\.successfactors\.(?:com|eu)(?![a-z0-9-])"),
        ("successfactors-legacy", "sapsf.",
         r"(?<![a-z0-9.-])career[0-9]*\.sapsf\.(?:com|eu|cn)(?![a-z0-9-])"),
        # ...and every other address on SAP's hosts: what a Career Site Builder
        # board loads (its rmkcdn assets, its jQuery from hcm<N>. or
        # performancemanager<N>., its Unify API host), a platform we read in
        # a form that names no board - and never a board's ref
        ("successfactors-other", "successfactors.",
         r"(?<![a-z0-9.-])(?!career[0-9]*\.)(?:[a-z0-9-]{1,63}\.)*successfactors\.(?:com|eu)"
         r"(?![a-z0-9-])"),
        ("successfactors-other", "sapsf.",
         r"(?<![a-z0-9.-])(?!career[0-9]*\.)(?:[a-z0-9-]{1,63}\.)*sapsf\.(?:com|eu|cn)"
         r"(?![a-z0-9-])"),
        ("taleo", "taleo.net", _B + r"taleo\.net"),
        ("oraclecloud", "oraclecloud.com", r"[a-z0-9-]{1,63}\.fa\.[a-z0-9-]{1,63}\.oraclecloud\.com"),
        ("clearcompany", "clearcompany.com", _B + r"clearcompany\.com"),
        ("hrmdirect", "hrmdirect.com", _B + r"hrmdirect\.com"),
        ("applicantpro-other", "isolvedhire.com", _B + r"isolvedhire\.com"),
        ("trinethire", "trinethire.com", _B + r"trinethire\.com"),
        ("hibob", "hibob.com", _B + r"careers\.hibob\.com"),
        ("avature", "avature.net", _B + r"avature\.net"),
        ("csod", "csod.com", _B + r"csod\.com"),
        ("personio", "personio.", _B + r"personio\.(?:de|com)(?![a-z0-9-])"),
        ("jobscore", "jobscore.com", _B + r"jobscore\.com"),
        ("careerplug", "careerplug.com", _B + r"careerplug\.com"),
        ("hireology", "hireology.com", _B + r"hireology\.com"),
        # where a company posts instead of keeping a board of its own
        ("linkedin", "linkedin.com", _B + r"linkedin\.com/(?:company/[^/\s\"'<>]+/jobs|jobs/)"),
        ("indeed", "indeed.com", _B + r"indeed\.com/(?:cmp|jobs|viewjob)"),
        ("ycombinator", "ycombinator.com", _B + r"ycombinator\.com/companies/[a-z0-9-]+/jobs"),
        ("ycombinator", "workatastartup.com", _B + r"workatastartup\.com"),
        ("wellfound", "wellfound.com", _B + r"wellfound\.com/company/[a-z0-9-]+/jobs"),
        # a platform we read, in a form that names no board
        ("workday-myworkdaysite", "myworkdaysite.com", _B + r"wd\d+\.myworkdaysite\.com"),
        ("workday-no-site", "myworkdayjobs.com", _B + r"wd\d+\.myworkdayjobs\.com"),
        ("gusto-posting", "jobs.gusto.com", _B + r"jobs\.gusto\.com/postings/"),
        ("paylocity-other", "paylocity.com", _B + r"recruiting\.paylocity\.com"),
        ("adp-other", "adp.com", _B + r"(?:workforcenow|myjobs|recruiting)\.adp\.com"),
        ("greenhouse-shortlink", "grnh.se", _B + r"grnh\.se/[a-z0-9]+"),
        # A PLATFORM WE READ, ON A HOST ITS READER DOES NOT ASK. A board kept
        # in Greenhouse's or Lever's European region has its own address
        # (job-boards.eu.greenhouse.io/<x>, jobs.eu.lever.co/<x>), and
        # ats.fetch_greenhouse and ats.fetch_lever ask the American API only.
        # Handed over as an ordinary ref, <x> would be asked where it does
        # not live: a 404, or whichever other employer holds that name there.
        # So they are counted here until a reader can ask the European host.
        ("greenhouse-eu", "eu.greenhouse.io", _B + r"(?:[a-z0-9-]+\.)?eu\.greenhouse\.io"),
        ("lever-eu", "eu.lever.co", _B + r"(?:[a-z0-9-]+\.)?eu\.lever\.co"),
    )]
# (and no board pattern may read one of those hosts as the ordinary kind:
# find_boards' embed form is anchored at "greenhouse.io/embed", which the
# European host ends in too)
_OTHER_REGION = re.compile(r"\.eu\.(?:greenhouse\.io|lever\.co)", re.I)


def _other_region(text: str, m) -> bool:
    """Does the match sit on a European host. Read off the one host the match
    is part of (back to where its host begins), so an American board linked
    beside a European one is still read as itself."""
    a, floor = m.start(), max(0, m.start() - 40)
    while a > floor and (text[a - 1].isalnum() or text[a - 1] in ".-"):
        a -= 1
    return bool(_OTHER_REGION.search(text, a, m.end()))

# Forms that name a TENANT of a platform we read but no board this door can
# name. Not in _NO_BOARD_IN_IT: beside a board of the same platform they are a
# second account, and a doubt - unless they name the very tenant the board is
# (same_tenant). The door says so in those words.
TENANT_FORMS = {"zohorecruit-tenant": "zohorecruit", "comeet-tenant": "comeet",
                "jobvite-tenant": "jobvite", "jobvite-joblink": "jobvite"}
_TENANT_UID = re.compile(r"(?<![0-9A-F])([0-9A-F]{2}\.[0-9A-F]{3})(?![0-9A-F])", re.I)


def tenant_of(kind: str, text) -> str:
    """The tenant an address names, for the platforms with tenant forms: a
    Zoho portal's host, a Comeet company uid; "" otherwise."""
    s = str(text or "")
    if kind == "zohorecruit":
        m = re.search(r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.zohorecruit\.(?:com|eu|in))", s, re.I)
        return m.group(1).lower() if m else ""
    if kind == "comeet":
        m = _TENANT_UID.search(s.split("|", 1)[0] if "|" in s else s)
        return m.group(1).lower() if m else ""
    # (jobvite: a company code names an account and a slug names a career
    # site, and nothing on a page ties one to the other - so a Jobvite
    # account form is never the board's own tenant, and stays a doubt)
    return ""


def same_tenant(row: dict, candidates) -> bool:
    """A tenant form naming the tenant a board of the same platform on the
    page or the site already is: theirs, said again."""
    kind = TENANT_FORMS.get(str(row.get("vendor") or ""))
    t = tenant_of(kind, row.get("saw")) if kind else ""
    return bool(t) and any(c.get("type") == kind and tenant_of(kind, c.get("ref")) == t
                           for c in candidates)


def jobvite_account_forms(boards: dict, others: dict) -> tuple[dict, dict]:
    """One page's tally, where it names a Jobvite account by its company id
    (a jobvitefeed board) AND a jobs.jobvite.com career site: the career site
    is the page's board, and the account is filed as the form it was before
    its feed had a reader - jobvite-tenant, another account beside their
    board, a doubt. A slug names a career site and an id an account, and
    nothing on a page ties one to the other (tenant_of). (sightings' rows,
    keyed (kind, key, element) and (vendor, element).)"""
    kinds = {kind for kind, _key, _el in boards}
    if "jobvitefeed" not in kinds or "jobvite" not in kinds:
        return boards, others
    for bkey in [b for b in boards if b[0] == "jobvitefeed"]:
        row = boards.pop(bkey)
        form = others.setdefault(("jobvite-tenant", bkey[2]), {
            "vendor": "jobvite-tenant", "element": bkey[2], "saw": row["saw"], "times": 0})
        form["times"] += row["times"]
    return boards, others


# The forms above that belong to a platform we read. A redirect that lands on
# one is walked one hop further, because the platform's own next hop usually
# IS the board (a grnh.se short link, a Workday tenant with no site named).
# A redirect to any other platform stops where it is.
_NO_BOARD_IN_IT = {"workday-no-site": "workday", "gusto-posting": "gusto",
                   "paylocity-other": "paylocity", "adp-other": "adp",
                   "greenhouse-shortlink": "greenhouse", "teamtailor-other": "teamtailor",
                   "applicantpro-other": "applicantpro", "pinpoint-other": "pinpoint",
                   "zohorecruit-other": "zohorecruit", "comeet-other": "comeet",
                   "jobvite-other": "jobvite", "successfactors-other": "successfactors"}
# A platform's form that is somewhere else to hire through on any other page
# and the board's own furniture on the board's own pages: a Career Site
# Builder page names its tenant's legacy portal in its j2w.init ssoUrl, and
# Indra's front page links it for a candidate's sign-in (2026-11-20). Kept
# off those pages only, and only where the address names that board's own
# tenant (its company= is the page's ssoCompanyId) or no tenant at all: a
# portal of another tenant beside the board is another account, and stays
# a platform nothing reads, as anywhere else (seventh blind review,
# 2026-11-22). See sightings and _board_furniture.
_ON_ITS_OWN_BOARD = {"successfactors-legacy": "successfactors"}
_SF_COMPANY = re.compile(r"[?&;]company=([^&#\s\"'<>]{1,80})", re.I)
_SF_SSO = re.compile(r"[\"']ssoCompanyId[\"']\s*:\s*[\"']([^\"'<>]{1,80})[\"']")


def legacy_tenant(address) -> str:
    """The tenant a SuccessFactors legacy portal's address names - its
    company= - lower-cased, or "" for one that names none (a bare host)."""
    m = _SF_COMPANY.search(str(address or ""))
    return urllib.parse.unquote(m.group(1)).strip().lower() if m else ""

_ANY_HINT = re.compile("|".join(sorted(
    {re.escape(h) for hs in HINTS.values() for h in hs}
    | {re.escape(h) for _, h, _ in ELSEWHERE} | {re.escape(JIBE_MARK)},
    key=len, reverse=True)), re.I)
# Hosts of platforms we read. A link to one that names no board is worth one
# look (the posting it opens usually links its board); a link that already
# names the board is not, and neither is a platform we cannot read.
_READABLE_HOST = re.compile(
    r"(?:^|\.)(?:greenhouse\.io|grnh\.se|lever\.co|ashbyhq\.com|workable\.com|"
    r"recruitee\.com|breezy\.hr|bamboohr\.com|smartrecruiters\.com|"
    r"myworkdayjobs\.com|applytojob\.com|icims\.com)$|^ats\.rippling\.com$|"
    r"^recruiting\.paylocity\.com$|^workforcenow\.adp\.com$|^jobs\.gusto\.com$|"
    r"^jobs\.gem\.com$", re.I)


def find_refs(text: str) -> tuple[list[tuple], list[tuple]]:
    """Every board, and every platform nothing reads, that `text` names.

    Boards come back as (start, end, kind, ref, key); the rest as (start, end,
    vendor). WHERE TWO PATTERNS OF ONE KIND OVERLAP, THE ONE THAT STARTS FIRST
    AND RUNS LONGEST STANDS: "jobs.ashbyhq.com/flock%20safety" is one board
    called "flock safety", not also one called "flock", and a page that names
    a board once is counted once however many tables know its form.
    """
    low = text.lower()
    hits = []
    for p in BOARDS:
        if not any(h in low for h in HINTS[p.kind]):
            continue
        for m in p.rx.finditer(text):
            if p.kind in ("greenhouse", "lever") and _other_region(text, m):
                continue                # the European host: counted under ELSEWHERE
            made = p.make(m)
            if made:
                hits.append((m.start(), m.end(), p.kind, made[0], made[1]))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept, ends = [], {}
    for h in hits:
        if h[0] < ends.get(h[2], -1):
            continue
        ends[h[2]] = h[1]
        kept.append(h)
    others = []
    for vendor, hint, rx in ELSEWHERE:
        if hint not in low:
            continue
        for m in rx.finditer(text):
            # the address of a board we can read is not also "elsewhere"
            if any(k[0] <= m.start() < k[1] or m.start() <= k[0] < m.end() for k in kept):
                continue
            others.append((m.start(), m.end(), vendor))
    # AN ADDRESS THAT NAMES A TENANT is that, and not also the same platform's
    # form that names nothing: comeet.co/jobs/<UID> was counted both ways
    tenants = [(s, e, TENANT_FORMS[v]) for s, e, v in others if v in TENANT_FORMS]
    others = [o for o in others
              if not (o[2] in _NO_BOARD_IN_IT and any(
                  k == _NO_BOARD_IN_IT[o[2]] and s < o[1] and o[0] < e for s, e, k in tenants))]
    return kept, others


def bare_segment(text: str, hit: tuple) -> bool:
    """The board was read from the FIRST PATH SEGMENT of a vendor's page host
    (ats.rippling.com/<board>, jobs.lever.co/<board>) and from nothing more
    specific. In a link or a frame that is a board. Among the addresses a
    browser asks for it is as often the vendor's own furniture - the host
    that serves the board serves its scripts beside it - so a request read
    this way counts only when something else on the page names the same
    board (see sightings)."""
    seg = text[hit[0]:hit[1]]
    return seg.count("/") == 1 and "?" not in seg and "@" not in seg


def refs_in_address(url: str) -> tuple[list[tuple], list[tuple]]:
    """find_refs over one address, with the escapes a link wraps it in undone."""
    return find_refs(soften(url or ""))


# ------------------------------------------------------- where in the page

_JSON_ESC = re.compile(r"\\u00(2f|26|3d|3f|3a)|\\/", re.I)
_PCT_ESC = re.compile(r"%(2f|3a|3f|3d|26)", re.I)


def soften(text: str) -> str:
    """The same text with the three wrappings a page puts an address in taken
    off: HTML entities (&amp;), a script's escaped slashes (https:\\/\\/), and
    the percent-escapes of a link that carries another link (...?url=https%3A).
    Only the characters that structure an address are decoded; a %20 inside a
    board's name stays as the page wrote it."""
    if "&" in text:
        text = html_lib.unescape(text)
    if "\\" in text:
        text = _JSON_ESC.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else "/", text)
    if "%" in text:
        text = _PCT_ESC.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text


def _script_at(low: str, i: int) -> int:
    """Where the next <script> tag opens at or after `i` (and not a
    <script-loader> somebody's framework made up), or -1."""
    while True:
        j = low.find("<script", i)
        if j < 0 or not (low[j + 7:j + 8].isalnum() or low[j + 7:j + 8] in ("-", "_")):
            return j
        i = j + 7


def _apart(page: str) -> tuple[str, list[str]]:
    """The page with its comments and its script blocks taken out, and the
    script blocks.

    ONE WALK, TAKING WHICHEVER OPENS FIRST. The first version lifted every
    <script> block out and cut the comments from what was left, so a script
    somebody had commented out was still a script: the embed a company
    retired by wrapping it in <!-- --> read as the page loading that board,
    labelled "script", and when the company had moved to a platform nothing
    reads it was the only candidate on the page (the blind review of
    2026-11-18; a browser shown the same page never asked for the script).
    A comment that opens outside a script is cut whole, with whatever is
    inside it. A "<!--" INSIDE a script is the old way of hiding a script
    from a browser that could not run one, and it is script.

    By str.find and not a regex: a lazy regex rescans to the end of the page
    for every opener nobody closed, which froze a twelve-worker sweep once
    (discover_ats._strip_blocks). Each opener is searched for again only
    when the walk has passed the last one found, so this is one pass however
    many of either the page holds.
    """
    low, body, scripts, i, n = ats.lower_same(page), [], [], 0, len(page)
    c, s = low.find("<!--"), _script_at(low, 0)
    while i < n:
        if 0 <= c < i:
            c = low.find("<!--", i)
        if 0 <= s < i:
            s = _script_at(low, i)
        if c < 0 and s < 0:
            break
        if s < 0 or 0 <= c < s:
            body.append(page[i:c])
            # ("<!-->" is a whole comment, so the closer is looked for from
            # the opener's own dashes)
            end = low.find("-->", c + 2)
            if end < 0:
                return "".join(body), scripts       # never closed: the rest is comment
            i = end + 3
        else:
            body.append(page[i:s])
            end = low.find("</script", s)
            if end < 0:
                scripts.append(page[s:])            # never closed: the rest is script
                return "".join(body), scripts
            scripts.append(page[s:end])
            close = low.find(">", end)
            i = close + 1 if close >= 0 else n
    body.append(page[i:])
    return "".join(body), scripts


_TAG = re.compile(r"<(a|area|iframe|frame|embed|object|meta|link)\b[^<>]{0,6000}>", re.I)
_REFRESH = re.compile(r"http-equiv\s*=\s*[\"']?refresh", re.I)
# A <link rel="alternate"> is a link the page publishes - the feed of what is
# on it - and is filed as one. Every other <link> (a stylesheet, a preload, a
# font) is the page's furniture and stays text.
_ALTERNATE = re.compile(r"\brel\s*=\s*[\"']?[^\"'>]*\balternate\b", re.I)


def zones(page: str) -> list[tuple[str, str]]:
    """The page as (element, text) pieces, so a reference can say where it sat.

      script   every <script> block, its src and its body
      iframe   the opening tags of <iframe>, <frame>, <embed>, <object>
               (the whole tag: a lazy-loaded frame keeps its address in
               data-src, not src)
      href     the opening tags of <a> and <area>, and a <link
               rel="alternate"> (a feed the page publishes)
      redirect a <meta http-equiv="refresh"> - the page sends its reader on
      text     everything else: visible words, and the attributes of every
               other tag

    A REFERENCE INSIDE AN HTML COMMENT IS NOT ON THE PAGE, whatever kind of
    element was commented out. A commented-out link or embed is something
    the company took down, and the older finders count it (_apart).
    """
    body, scripts = _apart(page)
    pieces = {"href": [], "iframe": [], "redirect": []}
    text, at = [], 0
    for m in _TAG.finditer(body):
        tag = m.group(1).lower()
        if tag == "meta":
            if not _REFRESH.search(m.group(0)):
                continue                         # an ordinary meta tag is text
            where = "redirect"
        elif tag == "link":
            if not _ALTERNATE.search(m.group(0)):
                continue                         # a stylesheet or a preload is text
            where = "href"
        else:
            where = "href" if tag in ("a", "area") else "iframe"
        pieces[where].append(m.group(0))
        text.append(body[at:m.start()])
        at = m.end()
    text.append(body[at:])
    out = [("script", "\n".join(scripts)), ("iframe", "\n".join(pieces["iframe"])),
           ("redirect", "\n".join(pieces["redirect"])), ("href", "\n".join(pieces["href"])),
           ("text", " ".join(text))]
    return [(el, t) for el, t in out if t.strip()]


_STOP = frozenset(" \t\r\n\"'<>`|\\^{}")


# Query parameters that carry a PERSON, not a board (the results leave as
# artifacts of a public repository, and the door copies from them into the
# owner's queue). Jobvite's own addresses carry a recruiter's name in the
# talent network's sd= and a posting link's __jvsd= (Tyler's careers page,
# 2026-11-19), and name= and contact= there are people too. Anywhere else only
# a key that says it is a person's - a recruiter, a first, last or full name,
# a contact's name, an e-mail - is: a site's own name=All is a search filter,
# and rewriting it made an address the site never served (sixth blind review,
# 2026-11-22: Rain Bird's careers page). A name's later words go with it
# ('sd=Pat Example', 'Pat&#32;Example'), and so does a Jobvite address
# percent-encoded inside another one ('%26sd%3DPat%2520Example'). So does
# the part of a name after an apostrophe between letters ('Pat O'Brien',
# "D'Angelo"): every pattern stopped at it and kept "'Brien" (eighth blind
# review, 2026-11-22). Between letters only, so a quote that ends an
# attribute ('...sd=Pat'>) still ends the value.
_PARAM_SEP = r"(?:[?&;]|%26)"         # ('&amp;', '&#38;', '&#x26;' all end in ';')
# (between letters, or a letter's percent-escape - requests sends 'D'Ávila'
# as "D'%C3%81vila" - and as a straight or a curly quote, raw or an entity:
# blind review of the eighth review's fixes, 2026-11-22. Every spelling of
# one: the straight quote, the right and left curly quotes and the modifier
# letter apostrophe (U+02BC) as a named or a numeric reference, a numeric
# one with or without its ';', and each escaped twice ('&amp;#39;') - blind
# review 9, 2026-11-23: "Brien" was kept after &lsquo;, &#700;,
# &CloseCurlyQuote;, '&#39' and &amp;#39;. A bare '&amp;', '&#38;' or
# '&#x26;' is none of these, so it still ends the value: it is the next
# key's separator. An apostrophe before a space ("O' Brien") or at a value's
# start (last_name='Brien) is left: it reads the same as a quote that ends
# the attribute the address sits in.)
#
# A CHARACTER REFERENCE IS THE CHARACTER IT STANDS FOR (blind review 10,
# 2026-11-23): a letter written as one ('Jos&#233;', 'D&#039;&Aacute;vila',
# PHP's htmlentities) and a mark typed for an apostrophe ('O&acute;Brien',
# '&#8242;', '&#xFF07;') go with the name as their raw characters do, a
# reference escaped once or twice more ('&amp;amp;#39;') too, and inside a
# percent-encoded address its percent-encoded form ('O%26%2339%3BBrien').
# Only a reference to what ends a value raw ends it: '&' (the next key's
# separator, '&amp;', '&#38;', '&#x26;'), '#', a quote, '<', '>' and a line
# break - and an apostrophe's own spellings go only between letters, as
# above (in a percent-encoded address a quote can end no attribute, so
# there they go anywhere).
_REF = (r"&(?:amp;){0,2}(?!(?:amp|num|quot|lt|gt|apos|newline|#0*(?:38|35|34|39|60|62|13|10)"
        r"|#x0*(?:26|23|22|27|3c|3e|d|a))(?![0-9a-z]))"
        r"(?:#[0-9]{1,7};?|#x[0-9a-f]{1,6};?|[a-z][a-z0-9]{1,31};)")
_ENC_REF = (r"%26(?:amp%3b){0,2}(?!(?:amp|num|quot|lt|gt|newline|%23(?:0*(?:38|35|34|60|62|13|10)"
            r"|x0*(?:26|23|22|3c|3e|d|a)))(?![0-9a-z]))"
            r"(?:%23(?:[0-9]{1,7}|x[0-9a-f]{1,6})(?:%3b|;)?|[a-z][a-z0-9]{1,31}(?:%3b|;))")
_APOSTROPHE = (r"(?:(?<=[^\W\d_])|(?<=%[0-9A-Fa-f]{2}))"
               r"(?:'|&(?:amp;){0,2}(?:#(?:0*(?:39|700|8216|8217)|x0*(?:27|2bc|2018|2019));?"
               r"|apos;|[lr]squo;|rsquor;|(?:Open|Close)CurlyQuote;))"
               rf"(?=[^\W\d_]|%[0-9A-Fa-f]{{2}}|{_REF})")
_PARAM_VALUE = rf"""(?:[^&#"'<>\r\n]|{_APOSTROPHE}|{_REF})*"""
_PERSON_PARAM = re.compile(
    rf"({_PARAM_SEP}(?:recruiter(?:_?(?:name|email))?|contact_?name|first_?name|last_?name|"
    rf"full_?name|e-?mail)=){_PARAM_VALUE}", re.I)
_JV_PERSON_PARAM = re.compile(rf"({_PARAM_SEP}(?:(?:__jv|jv)?sd|name|contact)=){_PARAM_VALUE}", re.I)
_JV_ENCODED_PERSON = re.compile(r"((?:%3f|%26)(?:(?:__jv|jv)?sd|name|contact|recruiter\w*)%3d)"
                                rf"(?:{_ENC_REF}|{_REF}|(?!%26)(?:[^&#\s\"'<>]|{_APOSTROPHE}))*", re.I)
_ENCODED_PERSON = re.compile(r"((?:%3f|%26)(?:recruiter(?:_?(?:name|email))?|contact_?name|first_?name|"
                             rf"last_?name|full_?name|e-?mail)%3d)"
                             rf"(?:{_ENC_REF}|{_REF}|(?!%26)(?:[^&#\s\"'<>]|{_APOSTROPHE}))*", re.I)
_JV_ADDRESS = re.compile(rf"""(?:app|hire|jobs)\.jobvite\.com(?:/|%2f)(?:[^"'<>\r\n]|{_APOSTROPHE})*""", re.I)


def _no_person(text: str) -> str:
    """`text` with every person's name taken out of the addresses in it: the
    Jobvite keys inside Jobvite's own addresses, the person keys anywhere."""
    text = _JV_ADDRESS.sub(lambda m: _JV_ENCODED_PERSON.sub(
        r"\1[removed]", _JV_PERSON_PARAM.sub(r"\1[removed]", m.group(0))), text or "")
    return _ENCODED_PERSON.sub(r"\1[removed]", _PERSON_PARAM.sub(r"\1[removed]", text))


def _short(text: str) -> str:
    """Text as it goes into the results file: any person's e-mail address
    taken out FIRST, and then cut to 160 characters. Cut first and redacted
    after, the words that replace an address pushed a row past the length
    the file promises (170 was reached), and an address cut in half was no
    longer recognised as one and went through. A person's name in an
    address's query goes the same way (_PERSON_PARAM)."""
    return privacy.redact(_no_person((text or "")[:4000]))[:160]


def _address(text: str, start: int, end: int) -> str:
    """The whole address a match sits in, as the page wrote it (no cut, no
    redaction: read here, never written)."""
    a, floor = start, max(0, start - 60)
    while a > floor and text[a - 1] not in _STOP:
        a -= 1
    b, ceil = end, min(len(text), start + 2000)
    while b < ceil and text[b] not in _STOP:
        b += 1
    return text[a:b]


_WHOLE = 4000       # an address running further than this off a match is not known whole


def _whole_address(text: str, start: int, end: int) -> str:
    """The whole address a match sits in, from where that address BEGINS to
    where it ends, as the page wrote it - or "" where either end lies more
    than _WHOLE characters off, so it is not known whole. (_address looks 60
    characters back, which is less than the address a match sits in: a
    jobs.jobvite.com loader written near the start of an app.jobvite.com
    address was read as on app.jobvite.com - review 12's verifier,
    2026-11-23.)"""
    lo, hi = max(0, start - _WHOLE), min(len(text), end + _WHOLE)
    a = max(text.rfind(c, lo, start) for c in _STOP) + 1
    if a == 0 and lo > 0:
        return ""
    ends = [i for i in (text.find(c, end, hi) for c in _STOP) if i >= 0]
    if not ends and hi < len(text):
        return ""
    return text[a:min(ends) if ends else hi]


# Jobvite's own keys for an account (c=, ci=) and a job (j=, cj=), with a
# value in the shape the tenant forms read
_JV_NAMES_ONE = re.compile(r"\b(?:c|ci|j|cj)=[A-Za-z0-9]{5,12}", re.I)
_JV_MENTION = re.compile(_B + r"jobvite\.com", re.I)       # (jobvite-other's own pattern)


def jobvite_widget_asset(address: str) -> bool:
    """Is this WHOLE address (_whole_address) one the account widget loads
    beside itself: on app.jobvite.com, the widget's own host, naming no
    account and no job, and holding no other Jobvite address? Only these
    are the widget's furniture on its page (sightings). One that names another
    account in a path the account forms do not list is jobvite-tenant
    before it gets here (ELSEWHERE); a jobs.jobvite.com career site's
    loader is not on the widget's host; and one inside an app.jobvite.com
    address makes that address no furniture either (review 12's verifier,
    2026-11-23: each was dropped beside the widget, and the door wired its
    feed with no doubt)."""
    return (bool(address) and _host(address) == ats.JOBVITE_FEED_HOST
            and len(_JV_MENTION.findall(address)) == 1 and not _JV_NAMES_ONE.search(address))


def _saw(text: str, start: int, end: int) -> str:
    """The matched text widened to the whole address as the page wrote it,
    at most 160 characters, with any person's e-mail address taken out (the
    wiring door quotes this into a ruling, and no address reaches a tracked
    file)."""
    a, floor = start, max(0, start - 60)
    while a > floor and text[a - 1] not in _STOP:
        a -= 1
    # (widened past the 160 that are kept, so an address lying across that
    # mark is still whole when it is looked for)
    b, ceil = end, min(len(text), a + 2000)
    while b < ceil and text[b] not in _STOP:
        b += 1
    return _short(text[a:b])


_ADDRESS = re.compile(r"(?:https?:)?//[^\s\"'<>]+", re.I)
_JOBISH = re.compile(r"career|job|recruit|hiring|hire|appl(?:y|icant)|talent|"
                     r"position|vacanc|opening|employment", re.I)
# Hosts that are on every marketing site and are nobody's job board: ad and
# analytics frames, consent banners, chat, video, forms. Their addresses
# carry the page's own address along ("...;~oref=https://acme.com/careers"),
# which is how a DoubleClick frame read as a job platform on the first run.
_NOT_A_PLATFORM = re.compile(
    r"(?:^|\.)(?:doubleclick\.net|google(?:apis|tagmanager|syndication|adservices|-analytics)?\.com|"
    r"gstatic\.com|youtube(?:-nocookie)?\.com|youtu\.be|vimeo\.com|wistia\.(?:com|net)|"
    r"facebook\.(?:com|net)|twitter\.com|x\.com|linkedin\.com|instagram\.com|tiktok\.com|"
    r"hubspot\.(?:com|net)|hsforms\.(?:com|net)|hs-sites\.com|hubspotusercontent[\w-]*\.net|"
    r"marketo\.(?:com|net)|pardot\.com|salesforce\.com|force\.com|"
    r"cookiebot\.com|onetrust\.com|cookielaw\.org|trustarc\.com|osano\.com|"
    r"recaptcha\.net|hcaptcha\.com|cloudflare\.com|cloudflareinsights\.com|"
    r"intercom\.io|drift\.com|qualified\.com|zendesk\.com|livechatinc\.com|tawk\.to|"
    r"6sense\.com|demandbase\.com|zoominfo\.com|bing\.com|clarity\.ms|hotjar\.com|"
    r"adsrvr\.org|adnxs\.com|criteo\.com|calendly\.com|typeform\.com|"
    r"userway\.org|accessibe\.com|stripe\.com|paypal\.com)$", re.I)


def _board_row(kind: str, element: str, saw: str) -> dict:
    return {"type": kind, "element": element, "saw": saw, "times": 0,
            "_spelt": collections.Counter()}


def _tally(text: str, element: str, page_url: str, website: str,
           boards: dict, others: dict, maybe: dict) -> None:
    """Count what one piece of a page names into `boards` and `others`.
    `maybe` takes the requests that name a board only by a bare first path
    segment, to be believed or not once the whole page has been read."""
    text = soften(text)
    if _ANY_HINT.search(text):
        kept, oth = find_refs(text)
        for hit in kept:
            start, end, kind, ref, key = hit
            into = maybe if (element == "request" and bare_segment(text, hit)) else boards
            row = into.setdefault((kind, key, element),
                                  _board_row(kind, element, _saw(text, start, end)))
            row["times"] += 1
            row["_spelt"][json.dumps(ref)] += 1
        for start, end, vendor in oth:
            # (a legacy portal is kept apart by the tenant its whole address
            # names, which a 160-character `saw` can cut off; and a Jobvite
            # address naming no board by its WHOLE address's host and by
            # whether it is the account widget's own asset - the key's last
            # part, which sightings reads - so the widget's assets are never
            # one row with a career site's loader, or with an app.jobvite.com
            # address holding one: jobvite_widget_asset)
            if vendor in _ON_ITS_OWN_BOARD:
                key = (vendor, element, legacy_tenant(_address(text, start, end)))
            elif vendor == "jobvite-other":
                whole = _whole_address(text, start, end)
                key = (vendor, element, _host(whole) if whole else "", jobvite_widget_asset(whole))
            else:
                key = (vendor, element)
            row = others.setdefault(key, {
                "vendor": vendor, "element": element,
                "saw": _saw(text, start, end), "times": 0})
            row["times"] += 1
        at = text.lower().find(JIBE_MARK)
        if at >= 0 and _host(page_url):
            host = urllib.parse.urlsplit(page_url).netloc.lower()
            row = boards.setdefault(("jibe", host, element), _board_row(
                "jibe", element, _saw(text, at, at + len(JIBE_MARK))))
            row["times"] += text.lower().count(JIBE_MARK)
            row["_spelt"][json.dumps(host)] += 1
    for host, start, end in successfactors_links(text, website):
        row = boards.setdefault(("successfactors", host, element), _board_row(
            "successfactors", element, _saw(text, start, end)))
        row["times"] += 1
        row["_spelt"][json.dumps(f"https://{host}")] += 1
    if element in ("iframe", "frame"):
        # A FRAME FROM A HOST NOTHING HERE KNOWS, whose address reads like a
        # job page, is very likely a platform nobody has listed yet. Named
        # "other:<its site>" so the tally can find the next one to write.
        for m in _ADDRESS.finditer(text):
            url = m.group(0)
            host = _host(url)
            if not host:
                continue
            where = urllib.parse.urlsplit(url if url.startswith("http") else "https:" + url)
            if (own_site(url, website) or _NOT_A_PLATFORM.search(host)
                    or not _JOBISH.search(where.netloc + where.path)
                    or any(refs_in_address(url))):
                continue
            row = others.setdefault((f"other:{_site_of(host)}", element), {
                "vendor": f"other:{_site_of(host)}", "element": element,
                "saw": _short(url), "times": 0})
            row["times"] += 1


_ORDER = {"iframe": 0, "frame": 1, "script": 2, "request": 3, "redirect": 4,
          "href": 5, "text": 6}


def _rows(boards: dict, others: dict) -> tuple[list[dict], list[dict]]:
    out = []
    for row in boards.values():
        # the spelling the page used most: Xplor and xplor are one board, and
        # only one of them is the id SmartRecruiters answers to
        row["ref"] = json.loads(row.pop("_spelt").most_common(1)[0][0])
        out.append({k: row[k] for k in ("type", "ref", "element", "saw", "times")})
    out.sort(key=lambda r: (_ORDER.get(r["element"], 9), -r["times"], r["type"], str(r["ref"])))
    rest = sorted(others.values(), key=lambda r: (r["vendor"], _ORDER.get(r["element"], 9)))
    return out, rest


# A TEAMTAILOR CAREER SITE ON A DOMAIN OF THE COMPANY'S OWN names no
# teamtailor.com host anywhere (Heimdall Power's, read 2026-11-19). The page
# gives itself away twice over: its assets come from teamtailor-cdn.com, and
# it publishes the feed Teamtailor gives every career site, <host>/jobs.rss,
# as its rel=alternate. BOTH must be on the one page - a feed link alone is
# any blog's - and the board is the feed's host. A pattern cannot see the
# two together (each piece of a page is read apart), so this reads the page.
_LINK_TAG = re.compile(r"<link\b[^<>]{0,1200}>", re.I)
_TT_FEED = re.compile(r"\bhref\s*=\s*[\"']?https://([a-z0-9.-]{4,253})/jobs\.rss(?=[\"'\s>?#]|$)", re.I)


def teamtailor_own_sites(page: str, page_url: str = "") -> list[tuple[str, str]]:
    """[(host, the tag that named it)] for a Teamtailor career site served on
    the company's own domain, read off the page as a reader sees it (an
    address in a comment is not on the page). THE FEED'S HOST IS THE PAGE'S
    OWN: a career site publishes its feed on itself, so a rel=alternate to
    another host is that host's business, never this page's board (blind
    review, 2026-11-21: any host a page author wrote was asked)."""
    body, scripts = _apart(page or "")
    here = _host(page_url)
    if "teamtailor-cdn.com" not in (body + "\n".join(scripts)).lower():
        return []
    out = []
    for m in _LINK_TAG.finditer(body):
        tag = m.group(0)
        f = _TT_FEED.search(tag) if _ALTERNATE.search(tag) else None
        host = f.group(1).lower() if f else ""
        if host and ats._HOSTNAME.fullmatch(host) and not host.endswith(".teamtailor.com") \
                and host.removeprefix("www.") == here:
            out.append((host, tag[:160]))
    return out


# SAP SUCCESSFACTORS' CAREER SITE BUILDER (CSB) serves a board from the
# employer's own careers host (jobs.gainwelltechnologies.com,
# careers.landisgyr.com), and its address names no vendor. Two things give one
# away (research of 2026-11-20 on six CSB hosts):
#
#   THE PAGE IS ONE: its own markup - its scripts, and the other tags'
#     attributes (a stylesheet) - loads CSB's machinery: assets from its
#     site's folder on rmkcdn.successfactors.com/<8 hex>/, the j2w.init({...
#     "ssoCompanyId" ...}) block, jQuery from (hcm|performancemanager)<N>.
#     (sapsf|successfactors).(com|eu)/verp/vmod_v1/ui/extlib/, or its own
#     /platform/js/j2w/ scripts. The board is THAT page's origin, never the
#     SAP host it loads from. Never read off its links or frames, nor off what
#     a browser saw it ask for: a frame's requests are not the page's markup,
#     and the page that frames a board is not the board.
#   A LINK to <host>/go/<Name>/<digits>/ (a CSB category page) or to
#     <host>/search/?...sortColumn=... (its search) on a host of the
#     company's own - the same registrable domain as its website. Those
#     shapes on anybody else's host prove nothing, and SAP's hosts are never
#     a board.
#
# The legacy portals stay under `elsewhere` (successfactors-legacy, above):
# robots-closed, never a board.
_CSB_MARK = re.compile(
    r"(?<![a-z0-9.-])rmkcdn\.successfactors\.com/[0-9a-f]{8}/"
    r"|\bj2w\.init\(\s*\{[^{}]{0,4000}?[\"']ssoCompanyId[\"']"
    r"|(?<![a-z0-9.-])(?:hcm|performancemanager)[0-9]+\.(?:sapsf|successfactors)\.(?:com|eu)"
    r"/verp/vmod_v1/ui/extlib/", re.I)
# CSB's own scripts, by the path the site serves them at: a relative address,
# or one on the page's own host (successfactors_tells holds it to that)
_CSB_J2W = re.compile(r"(?<=[\"'(=\s])(?:(?:https?:)?//([a-z0-9][a-z0-9.-]{0,252}))?/platform/js/j2w/",
                      re.I)
_CSB_LINK = re.compile(
    r"(?<![a-z0-9.-])https?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)"
    r"/(?:go/[^/\s\"'<>?#]{1,200}/[0-9]{3,12}(?=[/?#\"'\s<>]|$)"
    r"|search/\?(?:[^\s\"'<>#]{0,300}?&)?sortColumn=)", re.I)
_SAP_HOSTS = re.compile(r"(?:^|\.)(?:successfactors\.(?:com|eu)|sapsf\.(?:com|eu|cn))$", re.I)


def successfactors_links(text: str, website: str) -> list[tuple[str, int, int]]:
    """[(host, start, end)] for every link in `text` to a CSB category or
    search page on a host of the company's own (the registrable domain of its
    website), never on one of SAP's."""
    low = (text or "").lower()
    if not website or ("/go/" not in low and "sortcolumn=" not in low):
        return []
    site = _site_of(_host(website))
    out = []
    for m in _CSB_LINK.finditer(text):
        host = m.group(1).lower()
        if site and _site_of(_host(host)) == site and not _SAP_HOSTS.search(host) \
                and ats._HOSTNAME.fullmatch(host):
            out.append((host, m.start(), m.end()))
    return out


def successfactors_tells(pieces, page_url: str) -> list[tuple[str, str]]:
    """[(element, what showed it)] where a page's own markup - `pieces` is
    zones(page) - shows the page to be a CSB board: its script blocks and the
    attributes of its other tags only."""
    here = _host(page_url)
    out = []
    for element, text in pieces:
        low = text.lower()
        if element not in ("script", "text") or not (
                "rmkcdn.successfactors.com" in low or "j2w" in low or "vmod_v1" in low):
            continue
        text = soften(text)
        m = _CSB_MARK.search(text)
        if not m:
            m = next((j for j in _CSB_J2W.finditer(text)
                      if not j.group(1) or _host("https://" + j.group(1)) == here), None)
        if m:
            out.append((element, _saw(text, m.start(), m.end())))
    return out


def successfactors_origin(page_url: str) -> str:
    """The origin a CSB board at this page is read at (ats.successfactors_origin
    of its host), or "" where it cannot be one."""
    try:
        netloc = urllib.parse.urlsplit(page_url or "").netloc
        return ats.successfactors_origin(f"https://{netloc}") if netloc else ""
    except (ats.AtsError, ValueError):
        return ""


def sightings(page: str, page_url: str, website: str, *,
              requests_seen: typing.Iterable[str] = (),
              frames_seen: typing.Iterable[str] = ()) -> tuple[list[dict], list[dict]]:
    """Every board and every unread platform a page names, with the element
    each sat in and how many times. A browser adds two kinds of evidence the
    markup cannot give: the addresses the page asked for while it drew itself
    ("request") and the addresses its frames ended up on ("frame")."""
    boards: dict = {}
    others: dict = {}
    maybe: dict = {}
    pieces = zones(page or "")
    for element, text in pieces:
        _tally(text, element, page_url, website, boards, others, maybe)
    for element, urls in (("request", requests_seen), ("frame", frames_seen)):
        urls = [u for u in urls if isinstance(u, str) and u.startswith("http")]
        if urls:
            _tally("\n".join(urls), element, page_url, website, boards, others, maybe)
    # A REQUEST THAT NAMES A BOARD ONLY BY ITS FIRST PATH SEGMENT is believed
    # when anything else on the page names that board, and dropped when it
    # stands alone (bare_segment says why).
    named = {(kind, key) for kind, key, _el in boards}
    for (kind, key, element), row in maybe.items():
        if (kind, key) in named:
            have = boards.setdefault((kind, key, element), _board_row(kind, element, row["saw"]))
            have["times"] += row["times"]
            have["_spelt"].update(row["_spelt"])
    # PAYLOCITY ANSWERS AT TWO ADDRESSES FOR ONE BOARD: FAAC's page frames
    # /List/2115/FAAC-Incorporated and the frame lands on /All/<uuid>/
    # FAAC-Incorporated. Left alone that reads as a page naming two boards,
    # which is a doubt it has not earned. Same registered name on one page is
    # one board, and the /All/ address is the one every reader on file takes.
    by_name: dict = {}
    for (kind, key, _el), row in boards.items():
        ref = json.loads(row["_spelt"].most_common(1)[0][0])
        if kind == "paylocity" and "/All/" in ref and ref.count("/") == 7:    # .../All/<uuid>/<Name>
            by_name[ref.rsplit("/", 1)[-1].lower()] = (key, row["_spelt"])
    for (kind, key, element) in [k for k in boards if k[0] == "paylocity"]:
        row = boards[(kind, key, element)]
        ref = json.loads(row["_spelt"].most_common(1)[0][0])
        twin = (by_name.get(ref.rsplit("/", 1)[-1].lower())
                if "/List/" in ref and ref.count("/") == 7 else None)
        if twin:
            del boards[(kind, key, element)]
            have = boards.setdefault((kind, twin[0], element), _board_row(kind, element, row["saw"]))
            have["times"] += row["times"]
            if not have["_spelt"]:
                have["_spelt"].update(twin[1])
    for host, saw in teamtailor_own_sites(page or "", page_url):
        row = boards.setdefault(("teamtailor", host, "href"), _board_row("teamtailor", "href", saw))
        row["times"] += 1
        row["_spelt"][json.dumps(f"https://{host}")] += 1
    # A CAREER SITE BUILDER PAGE IS ITS OWN BOARD, at its own origin; what it
    # says of SAP's hosts is its own furniture, its tenant's legacy portal
    # included (_ON_ITS_OWN_BOARD)
    origin = successfactors_origin(page_url)
    tells = successfactors_tells(pieces, page_url) if origin else []
    for element, saw in tells:
        host = urllib.parse.urlsplit(origin).hostname or ""
        row = boards.setdefault(("successfactors", host, element),
                                _board_row("successfactors", element, saw))
        row["times"] += 1
        row["_spelt"][json.dumps(origin)] += 1
    if tells:
        # the board's own tenant is the ssoCompanyId its j2w.init names; a
        # portal naming another is another account, and stays
        own = {m.group(1).strip().lower() for _el, text in pieces
               for m in _SF_SSO.finditer(soften(text))}

        def its_own(k) -> bool:
            return (_ON_ITS_OWN_BOARD.get(k[0]) == "successfactors" and len(k) > 2
                    and (k[2] == "" or k[2] in own))
        others = {k: v for k, v in others.items() if not its_own(k)}
    boards, others = jobvite_account_forms(boards, others)
    # an address of a platform we read that names no board says nothing once
    # the page HAS named that platform's board (Paylocity's own scripts, 23
    # of them, beside the frame that loaded them)
    kinds = {kind for kind, _key, _el in boards}
    others = {k: v for k, v in others.items() if _NO_BOARD_IN_IT.get(k[0]) not in kinds}
    # ...and beside a Jobvite ACCOUNT (a jobvitefeed board), what its widget
    # loads beside itself, and nothing else Jobvite on the page: an address on
    # app.jobvite.com naming no account or job and holding no other Jobvite
    # address, read off the whole address (jobvite_widget_asset, the key's
    # last part). A career site's loader is a board nothing ties to the
    # account, and so is any address it sits in (blind review 12 and its
    # verifier, 2026-11-23).
    if "jobvitefeed" in kinds:
        others = {k: v for k, v in others.items() if not (k[0] == "jobvite-other" and k[-1] is True)}
    named = [{"type": kind, "ref": json.loads(row["_spelt"].most_common(1)[0][0])}
             for (kind, _key, _el), row in boards.items() if row["_spelt"]]
    others = {k: v for k, v in others.items() if not same_tenant(v, named)}
    return _rows(boards, others)


# -------------------------------------------------------- reading one page

class _Pointed(Exception):
    """An answer's Location names a job platform on another host. Raised from
    inside one hop so the walk stops BEFORE that address is asked for: where
    the company's site points is the finding, and opening the board is not
    this file's business."""

    def __init__(self, to: str, frm: str):
        super().__init__(to)
        self.to, self.frm = to, frm


def _request(url: str):
    """One request, redirects off, paced and named like every other fetch."""
    ats._host_gate(url)
    return requests.get(url, headers=ats.UA, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                        allow_redirects=False, stream=True)


PEEK = 4096      # enough of a body to know a bot-check page from a real one
# 202 is SiteGround's challenge; the rest are the usual refusals. A refusal
# is an answer: nothing asks that site again this run, in either mode.
REFUSALS = (202, 403, 429, 503)


def _hang_up(resp) -> None:
    """End an answer that is still arriving, from outside the thread reading
    it. Closing a socket does not wake a thread asleep in a read on it;
    shutting it down does, so that comes first. Best effort: where the
    socket sits inside requests is not something the library promises."""
    raw = getattr(resp, "raw", None)
    conn = getattr(raw, "connection", None) or getattr(raw, "_connection", None)
    try:
        sock = getattr(conn, "sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except Exception:                        # noqa: BLE001
        pass
    try:
        if resp is not None:
            resp.close()
    except Exception:                        # noqa: BLE001
        pass


def _gave_up(url: str, state: dict) -> dict:
    asked = list(state.get("asked") or ())
    return {"asked": url, "final": asked[-1] if asked else url, "status": "error",
            "note": "no whole answer before this company's time was up", "html": "",
            "pointer": None, "hops": asked}


def land(url: str, website: str, *, body: bool = True,
         deadline: float | None = None) -> dict:
    """Ask for `url` and say where it landed, one redirect hop at a time.

    EVERY HOP GOES THROUGH ats.follow, so robots.txt is asked before each one
    (owner's ruling, 2026-10-09). The single request it makes per hop also
    looks at where the answer points: when a redirect leaves for an address
    that names a board, or a platform nothing reads, the walk stops there and
    that address is the finding. The platform is never asked - not its
    robots.txt, not its page - which also means a board whose robots.txt
    would have refused us is still recorded as where the company's site
    points. (The one exception is a readable platform's address that names no
    board, a short link or a tenant with no site: that hop is asked, robots.txt
    first, because its own next hop usually is the board.)

    Returns {"asked", "final", "status", "note", "html", "pointer", "hops"}
    with status ok | pointer | robots | blocked | error. `hops` is every
    address that was asked, in order; a pointer carries them too, because
    whether a redirect is the COMPANY'S pointer depends on whose addresses
    it passed through, and that is `absorb`'s to say. `html` is filled only
    for ok and only when `body` is set. A browser that is about to open the
    page itself passes body=False: the walk then reads just enough of the
    answer to tell a bot-check page from a real one, because A SITE THAT
    TURNED THE PLAIN REQUEST AWAY IS NOT ASKED AGAIN WITH A BROWSER. That is
    stricter than the nightly renderer, which opens whatever the plain fetch
    could not read; a sweep of every company's site errs toward taking no
    for an answer.

    THE CLOCK IS KEPT FROM OUTSIDE THE READ. requests' timeout governs one
    socket read, so a server sending nine bytes every half second, of its
    headers or of its page, never trips it; and a test made between chunks
    never runs while one chunk is still arriving (measured 2026-11-18:
    land() came back after 25 seconds against a 3-second deadline, which was
    when the server hung up). So the reading is done in a thread of its own.
    When the company's time is up this function answers "error" without it,
    hangs up on whatever was still arriving, and the thread asks for nothing
    more. It is a daemon thread: one stuck in a server's headers, where
    there is no answer yet to hang up on, is left behind rather than waited
    for when the run ends.
    """
    if deadline is None:
        return _land(url, website, body, None, {})
    state: dict = {}
    if time.monotonic() >= deadline:
        return _gave_up(url, state)
    done: list = []
    reader = threading.Thread(
        target=lambda: done.append(_land(url, website, body, deadline, state)),
        daemon=True, name="find-site-boards-read")
    reader.start()
    reader.join(max(0.0, deadline - time.monotonic()))
    if done:
        return done[0]
    state["stop"] = True
    _hang_up(state.get("resp"))
    return _gave_up(url, state)


def _land(url: str, website: str, body: bool, deadline: float | None, state: dict) -> dict:
    """The reading `land` describes. `state` is how the caller reaches in:
    it holds the addresses asked so far and the answer now arriving, and
    "stop" in it means ask for nothing more."""
    asked: list[str] = state.setdefault("asked", [])
    out = {"asked": url, "final": url, "status": "error", "note": "", "html": "",
           "pointer": None, "hops": asked}
    ahead = {"url": None}

    def one(u: str):
        if state.get("stop"):
            raise ats.AtsError("stopped: this company's time was up")
        resp = _request(u)
        state["resp"] = resp
        asked.append(u)
        loc = ((getattr(resp, "headers", None) or {}).get("Location")
               if getattr(resp, "status_code", 200) in ats.REDIRECTS else None)
        ahead["url"] = urllib.parse.urljoin(u, loc) if loc else None
        if loc and not own_site(ahead["url"], website):
            boards, others = refs_in_address(ahead["url"])
            if boards or any(v not in _NO_BOARD_IN_IT for _s, _e, v in others):
                resp.close()
                raise _Pointed(ahead["url"], u)
        return resp

    resp = None
    try:
        resp = ats.follow(url, one)
        out["final"] = asked[-1] if asked else url
        code = getattr(resp, "status_code", 200)
        ctype = (getattr(resp, "headers", None) or {}).get("content-type", "") or ""
        if code in REFUSALS:
            out.update(status="blocked", note=f"HTTP {code}")
        elif code != 200:
            out.update(note=f"HTTP {code}")
        elif ctype and not re.search(r"html|xml|text/plain|json", ctype, re.I):
            out.update(note=f"not a page ({ctype.split(';')[0][:40]})")
        else:
            raw, cap, late = b"", (PAGE_CAP if body else PEEK), False
            for chunk in resp.iter_content(chunk_size=65536 if body else PEEK):
                raw += chunk
                late = bool(state.get("stop") or (deadline and time.monotonic() > deadline))
                if late or len(raw) >= cap:
                    break
            enc = ats.best_charset(raw, ctype, getattr(resp, "encoding", None))
            try:
                text = raw.decode(enc, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            if late and len(raw) < cap:
                # half a page is not a page: what it had not yet sent is
                # where the board's frame usually is
                out.update(note="the page was still arriving when this company's time was up")
            # a small body that is mostly a challenge is a bot wall that
            # answered 200 (discover_ats.BLOCK_PAT, and its size gate)
            elif len(text) < 2048 and discover_ats.BLOCK_PAT.search(text):
                out.update(status="blocked", note="a bot-check page, served as HTTP 200")
            else:
                out.update(status="ok", html=text if body else "")
    except _Pointed as p:
        out.update(status="pointer", final=p.to,
                   note="redirects to a job platform's address; the platform was not asked",
                   pointer={"to": p.to, "from": p.frm, "hops": list(asked)})
    except ats.RobotsRefused as exc:
        out.update(status="robots", final=ahead["url"] or (asked[-1] if asked else url),
                   note=str(exc)[:160])
    except ats.AtsError as exc:              # robots.txt did not answer; a redirect loop
        out.update(final=ahead["url"] or (asked[-1] if asked else url), note=str(exc)[:160])
    except Exception as exc:                 # noqa: BLE001 - DNS, TLS, a timeout
        out.update(final=asked[-1] if asked else url,
                   note=f"network error: {type(exc.__cause__ or exc).__name__}")
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:                # noqa: BLE001
                pass
    out["hops"] = list(asked)
    return out


_NOT_A_LINK = re.compile(r"[{}<>\\\s;`$]|%7B", re.I)


def _anchors(page: str, base: str) -> list[tuple[str, str]]:
    """(absolute address, visible words) for every link a reader could follow.

    ONLY <a> AND <area>, with comments and scripts cut first. The first run of
    this file borrowed discover_ats' looser reading (any href-shaped text) and
    followed "href = current.state;" out of Planet's JavaScript, a
    "{{ data.permalink }}" template out of Vector Solutions' markup, an RSS
    feed and a WordPress oEmbed address - four requests to four pages that
    do not exist, on sites that had done nothing to earn them (2026-11-18).

    AND NOT WITH ats._ANCHOR. That pattern reads a link's words with a lazy
    match to the closing tag, so every anchor nobody closed is scanned to the
    end of the page: 20,000 of them held this file for longer than anybody
    waited, and a regex never lets go of the GIL, so no clock here could have
    stopped it. The opening tag is matched (bounded), and the words are the
    text up to the next closing tag found by str.find, or a line's worth.
    """
    page = _apart(page)[0]
    low = ats.lower_same(page)
    out, seen = [], set()
    for m in _TAG.finditer(page):
        if m.group(1).lower() not in ("a", "area"):
            continue
        h = discover_ats.HREF.search(m.group(0))
        if not h:
            continue
        href = html_lib.unescape(h.group(1).strip("\"'")).strip()
        if (not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:"))
                or _NOT_A_LINK.search(href)):
            continue
        try:
            url = urllib.parse.urljoin(base, href)
        except ValueError:
            continue
        end = low.find("</a", m.end(), m.end() + 2000)
        inner = page[m.end():end] if end >= 0 else page[m.end():m.end() + 200]
        words = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^<>]*>", " ", inner))).strip()
        if (url, words) not in seen:
            seen.add((url, words))
            out.append((url, words))
    return out


_ANY_TAG = re.compile(r"<[^<>]{0,4000}>")


def visible(page: str) -> str:
    """What a reader would see of a page: its words, with markup, scripts,
    styles and comments gone.

    discover_ats.visible WITH ITS TAG STRIP BOUNDED. Theirs is "<[^>]+>",
    which runs from every "<" to the next ">" wherever that is. On a page
    of "<" with no ">" after it (a page cut off mid-tag, a megabyte of
    somebody's comparisons) each one is scanned to the end of the page:
    250,000 of them held this file for 168 seconds, and twice that where
    two pages are compared - inside a regex, with the GIL in hand, where no
    clock could reach it (the blind review of 2026-11-18). A tag is not four
    thousand characters long. Comments are cut as well, which theirs does
    not do: "no open positions" in a paragraph somebody commented out is
    not the page saying so.
    """
    t = _apart(page or "")[0]
    for tag in ("style", "noscript"):
        t = discover_ats._strip_blocks(t, tag)
    return re.sub(r"\s+", " ", _ANY_TAG.sub(" ", t)).strip()


async def read_plain(url: str, website: str, deadline: float | None = None) -> dict:
    """One address with plain requests: what `land` found, plus the page's
    links and its visible words."""
    pr = await asyncio.to_thread(land, url, website, body=True, deadline=deadline)
    if pr["status"] == "ok":
        pr["links"] = _anchors(pr["html"], pr["final"])
        pr["words"] = visible(pr["html"])
    return pr


# ------------------------------------------------------------- in a browser

# Pictures, video and fonts name no board, and not loading them is the
# cheapest courtesy a browser can pay a site it is only reading.
_SKIP_RESOURCES = {"image", "media", "font"}
# How long the browser's side waits on a question asked off to one side: a
# robots.txt, or an address walked before the browser may go to it.
ASIDE_SECONDS = 30
# How many addresses one page may have walked on its behalf (see _Session.rule)
# and how many of its requests are kept to be read.
WALKS_PER_PAGE = 8
ASKS_KEPT = 3000


async def _aside(fn, *args, seconds: float = ASIDE_SECONDS):
    """`fn(*args)` in a thread of its own, waited on for `seconds` and no
    longer (asyncio.TimeoutError after that).

    NOT asyncio.to_thread, for anything that can wait on somebody's server
    without a clock of its own. That borrows a thread from the loop's pool,
    and a pool's thread stuck in a slow answer is waited for when the run
    ends: twelve seconds measured on Python 3.11 for a twelve-second server,
    and on 3.12 five minutes before the loop gives up on it and the
    interpreter then waits anyway, with the job's two-hour limit as the only
    bound left. A daemon thread is simply left behind.
    """
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def work():
        try:
            got, err = fn(*args), None
        except BaseException as exc:         # noqa: BLE001 - handed to whoever asked
            got, err = None, exc

        def hand():
            if answer.done():
                return
            if err is not None:
                answer.set_exception(err)
            else:
                answer.set_result(got)
        try:
            loop.call_soon_threadsafe(hand)
        except RuntimeError:                 # the run ended while a server dawdled
            pass
    threading.Thread(target=work, daemon=True, name="find-site-boards-aside").start()
    return await asyncio.wait_for(answer, seconds)


async def _allowed(url: str) -> bool:
    """May this crawler read `url`: robots.allowed, asked off the event loop
    and on a clock. No answer in time is a no. robots.txt is asked BEFORE a
    page is read, and one that never answered has not said yes."""
    try:
        return bool(await _aside(robots.allowed, url))
    except Exception:                        # noqa: BLE001 - out of time, or robots.py itself failing
        return False


def _above(frame) -> list[str]:
    """The addresses of the documents a frame sits inside, nearest first."""
    out, f = [], frame.parent_frame
    while f is not None and len(out) < 20:
        out.append(f.url)
        f = f.parent_frame
    return out


def _shut(page) -> None:
    """Close a window we did not ask for. (Nothing loaded in it: `rule`
    below lets no request through from any page but the one we opened.)"""
    task = asyncio.ensure_future(page.close())
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


class _Session:
    """What one browser context is doing for us: the one page it has open,
    whose company that is, and what the page's navigations ran into.

    ONE ROUTE FOR THE WHOLE CONTEXT, NOT ONE PER PAGE. A page's own route
    never sees a window that page opens: one call to window.open loaded an
    address robots.txt closes, pictures and all, with nobody asked, and the
    window then sat open for the rest of that company's walk (the blind
    review of 2026-11-18). Routed at the context, a request from any page
    but the one we opened is refused outright. We asked for one page, and a
    second window is not it.
    """

    def __init__(self):
        self.page = None
        self.website = ""
        self.deadline: float | None = None
        self.opened = False                  # the first address has gone out
        self.stopped: dict | None = None     # what a later address of the main frame ran into
        self.pointed: dict = {}              # frame -> the walk whose answer pointed at a platform
        self.moved = False                   # the main frame was let go somewhere new
        self.walks = 0                       # addresses walked for this page so far
        self.walking = 0
        self.quiet = asyncio.Event()
        self.quiet.set()

    def begin(self, page, website: str, deadline: float | None) -> None:
        self.page, self.website, self.deadline = page, website, deadline
        self.opened, self.stopped, self.moved, self.pointed = False, None, False, {}
        self.walks = 0

    def end(self) -> None:
        self.page = None

    async def rule(self, route) -> None:
        """What the browser may ask for.

        ROBOTS.TXT AT EVERY STEP A BROWSER TAKES ON ITS OWN (owner's ruling,
        2026-10-09), and no board is opened to learn what an address already
        says:

          - the first address of the page was walked hop by hop by `land`
            before the browser was given it;
          - ANYWHERE ELSE THE MAIN FRAME GOES (a script's redirect, a meta
            refresh) IS WALKED THE SAME WAY FIRST, before the browser may
            go. A walk that ends at a page lets the browser follow. A walk
            whose answer points at a job platform stops there, as it does
            without a browser: where it pointed is the finding, and the
            browser never opens the board. A walk robots.txt or the site
            refuses stops the browser too, and the refused address is what
            is written down (the first version let the browser run into
            its own refusal and recorded the company as having left for a
            host called "chromewebdata");
          - a frame at one of the company's own addresses is walked first
            as well, because Playwright's router sees only the FIRST
            address of a server redirect: such a frame, answering 302 to a
            portal robots.txt closes, was loaded and its address kept;
          - anybody else's frame is asked of its own robots.txt at its
            first address. Where it redirects after that the router cannot
            see (the limit render_fetch documents), so where each frame
            ENDED is asked again before anything is kept of it (`_Watch`).

        Nothing is submitted: a navigation that is not a GET is refused.
        """
        req, page = route.request, self.page
        try:
            frame = req.frame
            ours = page is not None and frame.page == page
        except Exception:                    # noqa: BLE001 - a new window has no frame yet
            frame, ours = None, False
        if not ours or req.resource_type in _SKIP_RESOURCES:
            await route.abort("blockedbyclient")
            return
        if req.resource_type != "document":
            await route.continue_()
            return
        url, top = req.url, frame == page.main_frame
        if req.method != "GET":
            await route.abort("blockedbyclient")
            return
        if top and not self.opened:
            self.opened = True
            await route.continue_()
            return
        if not top and not own_site(url, self.website):
            if await _allowed(url):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")
            return
        if top and not own_site(url, self.website):
            named, forms = refs_in_address(url)
            if named or any(v not in _NO_BOARD_IN_IT for _s, _e, v in forms):
                # the page sends its reader straight to an address that names
                # a board, or a platform nothing reads: that address is the
                # finding, and nothing is asked of the platform to learn it
                self.stopped = self.stopped or {
                    "asked": url, "final": url, "status": "pointer", "note": "", "html": "",
                    "hops": [], "pointer": {"to": url, "from": page.url, "hops": []}}
                await route.abort("blockedbyclient")
                return
        if self.walks >= WALKS_PER_PAGE:
            # A page that keeps sending its reader on, or frames a dozen of
            # its own addresses, is not walked without end: every walk is a
            # request of the company's site beside the browser's own.
            if top:
                self.stopped = self.stopped or {
                    "asked": url, "final": url, "status": "error", "html": "", "hops": [url],
                    "pointer": None, "note": "",
                    "say": f"the page had sent the browser on {WALKS_PER_PAGE} times and went "
                           f"to send it on again; this address was not asked"}
            await route.abort("blockedbyclient")
            return
        self.walks += 1
        self.walking += 1
        self.quiet.clear()
        try:
            res = await asyncio.to_thread(
                land, url, self.website, body=False,
                deadline=min(self.deadline or float("inf"), time.monotonic() + ASIDE_SECONDS))
        finally:
            self.walking -= 1
            if not self.walking:
                self.quiet.set()
        if res["status"] == "ok":
            self.moved = self.moved or top
            await route.continue_()
            return
        if top:
            self.stopped = self.stopped or res
        elif res["status"] == "pointer":
            self.pointed[frame] = res
        await route.abort("blockedbyclient")


_SESSIONS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_SESSIONS_HELD: dict = {}       # for a context object that cannot be weakly held


async def _session(ctx) -> _Session:
    """A context's session, made the first time the context is used, which is
    also when its one route is set. (Kept by the context itself, weakly, so
    a closed context takes its session with it; a context that cannot be
    held that way is held outright, which keeps its id from being reused
    and a new context from being mistaken for one already routed.)"""
    try:
        sess = _SESSIONS.get(ctx)
    except TypeError:
        sess = (_SESSIONS_HELD.get(id(ctx)) or (None, None))[1]
    if sess is None:
        sess = _Session()

        async def only_allowed(route):
            try:
                await sess.rule(route)
            except Exception:                # noqa: BLE001 - the page closed with a request still waiting on us
                try:
                    await route.abort("blockedbyclient")
                except Exception:            # noqa: BLE001
                    pass
        await ctx.route("**/*", only_allowed)
        try:
            _SESSIONS[ctx] = sess
        except TypeError:
            _SESSIONS_HELD[id(ctx)] = (ctx, sess)
    return sess


class _Watch:
    """What one page did while it drew, each thing with the document that did
    it: where every frame was sent, and what every document asked for.

    A LIST OF ADDRESSES IS NOT ENOUGH. The first version kept every address
    the page's frames ended on and every address anything asked for, and
    filed them all as what the company's page points to. A careers page
    that frames a page of its parent's site, which in turn frames the
    parent's board, came out pointing at the parent's board twice over, as a
    frame and as a request (the blind review of 2026-11-18). Which document
    put a frame there, and which document made a request, is what says whose
    pointer it is; `absorb` judges that, and this only keeps the facts.
    """

    def __init__(self, page):
        self.page = page
        self.trail: list[str] = []           # every address the main frame was sent to, in order
        self.hops: dict = {}                 # frame -> every address that frame was sent to
        self.asks: list[dict] = []           # what was asked for, and by which document
        self.answers: list = []              # the main frame's answers, in order

    def asked(self, req) -> None:
        try:
            frame = req.frame
            if req.is_navigation_request():
                self.hops.setdefault(frame, []).append(req.url)
                if frame == self.page.main_frame:
                    self.trail.append(req.url)
            elif req.resource_type not in _SKIP_RESOURCES and len(self.asks) < ASKS_KEPT:
                self.asks.append({"url": req.url, "doc": frame.url, "up": _above(frame)})
        except Exception:                    # noqa: BLE001 - a request with no frame to its name
            pass

    def answered(self, resp) -> None:
        try:
            req = resp.request
            if req.is_navigation_request() and req.frame == self.page.main_frame:
                self.answers.append(resp)
        except Exception:                    # noqa: BLE001
            pass

    def answer(self, final: str):
        """The answer the browser was given for the document it ended on:
        the last one for that address, or failing that (a script rewrote
        the address bar) the last document it was given at all."""
        want = final.split("#")[0]
        docs = [r for r in self.answers if r.status not in ats.REDIRECTS]
        for resp in reversed(docs):
            if resp.url.split("#")[0] == want:
                return resp
        return docs[-1] if docs else None

    async def evidence(self, sess: _Session) -> tuple[list[dict], list[dict]]:
        """(frames, requests): where each frame ended, with the documents it
        sits inside and the addresses it went through; and what each
        document asked for, with the documents that one sits inside.

        NOTHING IS KEPT OF A FRAME THAT ENDED ON A PAGE ROBOTS.TXT CLOSES:
        not its address, not what it asked for, not a frame inside it.
        """
        main, frames, shut = self.page.main_frame, [], set()
        for f in self.page.frames:
            if f == main:
                continue
            walked = sess.pointed.get(f)
            if walked is not None:
                # sent to one of the company's own addresses, whose answer
                # pointed at a job platform: the platform was not opened,
                # and where the answer pointed is where this frame was going
                frames.append({"url": walked["pointer"]["to"], "up": _above(f),
                               "hops": list(walked.get("hops") or ())})
                continue
            url = f.url or ""
            if not url.startswith("http"):
                continue                     # about:blank, or a frame that was refused
            if not await _allowed(url):
                shut.add(url)
                continue
            frames.append({"url": url, "up": _above(f), "hops": list(self.hops.get(f, ()))})
        frames = [x for x in frames if not shut.intersection(x["up"])]
        asks = [a for a in self.asks
                if a["doc"] not in shut and not shut.intersection(a["up"])]
        return frames, asks


async def _settle(page, sess: _Session) -> None:
    """Let the page draw: until its network goes quiet (ten seconds at most,
    some pages never do), then two seconds more for what a script adds after
    that. A page on its way somewhere else is waited for until that address
    has been walked, and the page it was let through to is given the same
    time to draw, once."""
    for _ in range(2):
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:                    # noqa: BLE001
            pass
        if sess.stopped is not None:
            return
        await page.wait_for_timeout(2000)
        try:
            await asyncio.wait_for(sess.quiet.wait(), ASIDE_SECONDS)
        except asyncio.TimeoutError:
            return
        if sess.stopped is not None or not sess.moved:
            return
        sess.moved = False


def _stopped(pr: dict, res: dict, trail: list[str]) -> dict:
    """What a page comes to when its own script sent the browser on to an
    address the walk would not open. The address it was sent to is the
    finding (a pointer), or the reason it was not read."""
    before = trail[:-1] if trail and trail[-1] == res["asked"] else list(trail)
    hops = before + list(res.get("hops") or [res["asked"]])
    if res["status"] == "pointer":
        to = res["pointer"]["to"]
        return {**pr, "status": "pointer", "final": to, "html": "",
                "note": "a script on the page sends the browser to a job platform's "
                        "address; the platform was not opened",
                "pointer": {"to": to, "from": res["pointer"]["from"], "hops": hops}}
    why = res.get("say") or {
        "robots": "a script on the page sends the browser to a page robots.txt closes",
        "blocked": "a script on the page sends the browser to an address that turned "
                   "the plain request away"}.get(
        res["status"], "a script on the page sends the browser to an address that did not answer")
    return {**pr, "status": res["status"], "final": res["final"], "html": "",
            "note": f"{why} ({res['note']})" if res.get("note") else why}


async def read_rendered(ctx, url: str, website: str, deadline: float | None = None,
                        landed: dict | None = None) -> dict:
    """One address in the browser: what the page drew, where its frames went
    and what its documents asked for.

    WHAT THE BROWSER MAY ASK FOR is `_Session.rule`: robots.txt at every
    step, no board opened, no second window, nothing submitted.

    AND HOW THE SITE ANSWERED THE BROWSER IS LOOKED AT. The first version
    never looked: a site that let the plain request in and answered the
    browser 403 "Access denied" was recorded as read, with no board on it,
    and then asked again (the blind review of 2026-11-18). A refusal given
    to the browser is the same refusal: the status the page's own document
    came back with, and the small challenge page served as a 200, are read
    here exactly as `land` reads them.
    """
    pr = landed or await asyncio.to_thread(land, url, website, body=False, deadline=deadline)
    if pr["status"] != "ok":
        return pr
    sess = await _session(ctx)
    page = await ctx.new_page()
    watch = _Watch(page)
    sess.begin(page, website, deadline)
    page.on("request", watch.asked)
    page.on("response", watch.answered)
    page.on("popup", _shut)
    try:
        try:
            await page.goto(pr["final"], wait_until="domcontentloaded", timeout=25000)
        except Exception:                    # noqa: BLE001
            # A page whose script moves it on before it has finished loading
            # interrupts the load that was asked for. That is not a page
            # failing to open: where it went (or was stopped from going) is
            # still to be read below.
            if sess.stopped is None and not (sess.moved or sess.walking):
                raise
        await _settle(page, sess)
        if sess.stopped is not None:
            return _stopped(pr, sess.stopped, watch.trail)
        final = page.url or ""
        if not final.startswith("http"):
            return {**pr, "status": "error", "html": "",
                    "note": "the browser ended on no page at all"}
        if not await _allowed(page.url):
            return {**pr, "status": "robots", "final": final, "html": "",
                    "note": "the browser ended on a page robots.txt closes"}
        resp = watch.answer(final)
        code = resp.status if resp is not None else None
        if code in REFUSALS:
            return {**pr, "status": "blocked", "final": final, "html": "",
                    "note": f"HTTP {code}, answered to the browser"}
        if code is not None and code != 200:
            return {**pr, "status": "error", "final": final, "html": "",
                    "note": f"HTTP {code}, answered to the browser"}
        html = (await page.content())[:2 * PAGE_CAP]
        # (the size gate is on what the server SENT, as it is without a
        # browser: a page's drawn markup is swollen by its own scripts)
        served = html
        try:
            if resp is not None and int(resp.headers.get("content-length") or 0) < 2048:
                served = await resp.text()
        except Exception:                    # noqa: BLE001 - the body is gone, or was never text
            pass
        if len(served) < 2048 and discover_ats.BLOCK_PAT.search(served):
            return {**pr, "status": "blocked", "final": final, "html": "",
                    "note": "a bot-check page, shown to the browser"}
        pr = {**pr, "opened": pr["final"], "final": final, "html": html,
              "hops": list(watch.trail)}
        try:
            pr["words"] = await page.inner_text("body")
        except Exception:                    # noqa: BLE001
            pr["words"] = ""
        links = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.href || '', (e.innerText || '').trim()])")
        # a JS-built page can put an object where an href belongs (discover_js)
        pr["links"] = [(h, re.sub(r"\s+", " ", t or "")) for h, t in links
                       if isinstance(h, str) and h.startswith("http")]
        pr["frames"], pr["requests"] = await watch.evidence(sess)
    except Exception as exc:                 # noqa: BLE001
        if sess.stopped is not None:
            return _stopped(pr, sess.stopped, watch.trail)
        return {**pr, "status": "error", "html": "",
                "note": "the browser could not open it: " + " ".join(str(exc).split())[:100]}
    finally:
        sess.end()
        try:
            await page.close()
        except Exception:                    # noqa: BLE001 - it went with the browser
            pass
    return pr


# ----------------------------------------------------- which links to follow

# The words a site uses for the page its openings are on. "text or href",
# because a nav link reading "Careers" goes to /company/life-here as often as
# to /careers, and discover_ats reads the href alone.
_LISTING = re.compile(
    r"\b(?:open (?:positions?|roles?|jobs)|(?:current|job|career|all) "
    r"(?:openings?|opportunities|listings?|positions)|"
    r"(?:view|see|browse|explore|search) (?:all |our |open |current )?"
    r"(?:jobs|openings|positions|roles|opportunities)|job (?:board|search|listings?)|"
    r"openings)\b", re.I)
# THE WORDS MUST BE A CAREERS LABEL, NOT MERELY HOLD THE WORD. Searched for
# anywhere in a link's words, "career" and "job" sent the first run to Vector
# Solutions' "Career Technical Education" and "Job Safety Analysis" product
# pages (2026-11-18). A nav link says "Careers", "Jobs at Acme", "Join our
# team", "We're hiring" - and little else.
_CAREERS = re.compile(
    r"^(?:(?:our |view |see |explore )?careers?(?: page| site| opportunities| with us|"
    r" (?:at|@) [\w .&'-]{1,30})?|jobs?(?: openings| opportunities| (?:at|@) [\w .&'-]{1,30})?|"
    r"join (?:us|our team|the team)|work (?:with|for|at) us|"
    r"we(?:'|\u2019)?re hiring|we are hiring|now hiring|hiring|vacancies|"
    r"employment(?: opportunities)?)[\s!.>\u00bb\u2192]*$", re.I)
_LISTING_PATH = re.compile(r"open-?(?:positions?|roles?)|openings|"
                           r"job-?(?:board|listings?|search|openings?)", re.I)
_NOT_A_PAGE = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|png|jpe?g|gif|svg|webp|mp4|mov|ics|"
                         r"xml|rss|json)$|/(?:feed|rss|wp-json)(?:/|$)", re.I)
# The path ENDS at careers: /careers, /company/jobs/. A path that only passes
# through it (/careers/perks, /careers/what-we-offer) is one of a careers
# page's own sub-pages, worth one look and no more.
_CAREERS_PAGE = re.compile(r"/(?:careers?|jobs?|join-?us|work-?with-?us|employment|"
                           r"vacanc\w*|open-?positions?)/?$", re.I)


_QUERY_IS_THE_PAGE = re.compile(r"\.(?:php|aspx?|jsp|cfm|do)$", re.I)


def _key(url: str) -> str:
    """One page, one key. /careers and /careers?hsLang=en are the same page
    (Deckard's was read twice before this dropped the query); only a site
    root or a script endpoint is told apart by its query (?page_id=12)."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    path = p.path.rstrip("/")
    query = p.query if (not path or _QUERY_IS_THE_PAGE.search(path)) else ""
    return f"{_host(url)}{path}{'?' + query if query else ''}"


def pick_links(links: list[tuple[str, str]], website: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """From a page's links: the ones worth reading next, best first, and the
    careers-looking ones that leave for a host nothing here knows.

    Worth reading: a link that stays on the company's own site and reads like
    careers (its words or its address), and a link straight to a platform we
    read whose address does not already name the board (a Gusto posting, a
    Paylocity job page: the page it opens usually names its board). A link
    that already names a board is the finding itself and is not followed,
    and neither is one to a platform's address that is counted under
    `elsewhere` for some other reason (a board in its European region): the
    one look is for an address that names nothing yet.
    """
    best: dict[str, dict] = {}
    strays: list[tuple[str, str]] = []
    for n, (url, words) in enumerate(links):
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        url = url.split("#")[0]
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            continue
        path = parts.path or "/"
        words = words if len(words) <= 60 else ""       # a paragraph is not a nav link
        sub = parts.netloc.lower().split(".")[0] in ("careers", "jobs")
        if _NOT_A_PAGE.search(path):
            continue
        if _LISTING.search(words) or _LISTING_PATH.search(path):
            rank = 0
        elif _CAREERS_PAGE.search(path) or sub or _CAREERS.search(words):
            rank = 1
        elif (discover_ats.CAREER_PATH.search(path)
              or discover_ats.CAREER_HREF.search(path + "?" + parts.query)):
            rank = 2
        else:
            continue
        if discover_ats.ARTICLE_PATH.search(path) and rank > 0:
            continue                    # a blog post called "Why jobs matter"
        named, forms = refs_in_address(url)
        if own_site(url, website):
            pass
        elif (_READABLE_HOST.search(_host(url)) and not named
              and all(v in _NO_BOARD_IN_IT for _s, _e, v in forms)):
            rank += 3                   # after every page of their own
        else:
            if not named and not forms and not _NOT_A_PLATFORM.search(_host(url)):
                strays.append((url, words))
            continue
        row = {"url": url, "rank": rank, "length": len(path), "n": n}
        k = _key(url)
        if k not in best or (row["rank"], row["length"]) < (best[k]["rank"], best[k]["length"]):
            best[k] = row
    return sorted(best.values(), key=lambda r: (r["rank"], r["length"], r["n"])), strays


def _inside(page: str, tag: str) -> str:
    """The words of the page's first <tag>, found with str.find. The lazy
    regex this replaces (<title[^>]*>(.*?)</title>) rescans to the end of the
    page from every opener that is never closed."""
    low = ats.lower_same(page[:400_000])
    i = low.find("<" + tag)
    j = low.find(">", i) if i >= 0 else -1
    if j < 0:
        return ""
    k = low.find("</" + tag, j, j + 600)
    inner = page[j + 1:k] if k >= 0 else page[j + 1:j + 200]
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^<>]*>", " ", inner))).strip()


def careers_page(url: str, page: str, words: str) -> bool:
    """Is this a careers page? discover_ats._is_careers' rule, with its own
    patterns: the path says so, or the title or first heading does and the
    path is not an article's; and there is a page's worth of words on it."""
    path = urllib.parse.urlsplit(url).path
    if not discover_ats.CAREER_PATH.search(path):
        head = _inside(page, "title") + " " + _inside(page, "h1")
        if not discover_ats.CAREER_TITLE.search(head) or discover_ats.ARTICLE_PATH.search(path):
            return False
    return len(words or "") >= 400


def job_like(links: list[tuple[str, str]]) -> int:
    """How many of a page's links read like postings, by render_fetch's own
    tests (a job-shaped address AND words that read like a title)."""
    n = 0
    for href, words in links:
        words = re.sub(r"\s+", " ", words or "").strip()
        if not (6 <= len(words) <= 160) or render_fetch.NAV.match(words):
            continue
        if ats._JOB_COUNT.search(words):
            continue
        if render_fetch.JOB_HREF.search(href or "") and render_fetch.TITLEISH.search(words):
            n += 1
    return n


# ------------------------------------------------------------- one company

def _blank(company: dict) -> dict:
    return {"name": company.get("name"), "website": company.get("website"),
            "was": (company.get("ats") or {}).get("type") or "unknown",
            "pages": [], "candidates": [], "off_site": [], "elsewhere": [],
            "left_site": [], "job_like_links": None, "says_no_openings": False,
            "error": None}


def _leave(rec: dict, frm: str, to: str) -> None:
    host = _host(to)
    if host and not any(x["to_host"] == host for x in rec["left_site"]):
        rec["left_site"].append({"from": frm, "to_host": host})


def _is_web(url) -> bool:
    return isinstance(url, str) and url.startswith("http")


def _same_board(row: dict) -> tuple:
    return row["type"], json.dumps(row["ref"]).lower()


def _by_document(pr: dict, who: Whose) -> tuple[list[str], list[str], dict]:
    """What a browser saw on one of the company's own pages, sorted by whose
    document did it. Returns (frames, requests, through): the addresses that
    are the page's own evidence, and for everything else a map from the
    address of the document it was really seen through to what was seen
    there ({"frames": [...], "requests": [...]}).

    THE COMPANY'S PAGE POINTS TO WHAT ITS OWN DOCUMENTS DO, AND ONLY THAT:

      a request counts when the document that made it is the company's own,
        and so is every document that one sits inside;
      a frame counts when every document it sits inside is the company's
        own, and every address it went through on the way there was the
        company's own or on the host it ended at - the rule a redirect is
        held to, because a frame sent to the parent's site and redirected
        on to the parent's board is the parent's pointer.

    What a frame of somebody else's site asked for, a frame inside such a
    frame, and a frame that arrived by way of somebody else's address are
    that other site's doing: C Co's careers page framed a page of its
    parent's site, that page framed the parent's board, and the first
    version filed the parent's board as what C Co's page points to.
    """
    frames, asks, through = [], [], {}

    def other(where: str) -> dict:
        return through.setdefault(where, {"frames": [], "requests": []})

    for f in pr.get("frames") or ():
        if isinstance(f, str):               # a bare address: seen by the page's own document
            f = {"url": f}
        url = f.get("url")
        if not _is_web(url):
            continue
        not_theirs = next((u for u in f.get("up") or () if _is_web(u) and not who.theirs(u)), None) \
            or next((h for h in f.get("hops") or ()
                     if _is_web(h) and h != url and not who.theirs(h) and _host(h) != _host(url)),
                    None)
        if not_theirs:
            other(not_theirs)["frames"].append(url)
        else:
            frames.append(url)
    for a in pr.get("requests") or ():
        if isinstance(a, str):
            a = {"url": a}
        url = a.get("url")
        if not _is_web(url):
            continue
        not_theirs = next((u for u in [a.get("doc"), *(a.get("up") or ())]
                           if _is_web(u) and not who.theirs(u)), None)
        if not_theirs:
            other(not_theirs)["requests"].append(url)
        else:
            asks.append(url)
    return frames, asks, through


def _flat(items) -> list[str]:
    return [u for u in ((x if isinstance(x, str) else x.get("url")) for x in items or ())
            if _is_web(u)]


def absorb(rec: dict, pr: dict, hop: int, who: "Whose | str", guessed: bool = False,
           via: str | None = None) -> bool:
    """File one page's reading into the company's record. True when it was a
    page of their own site that could be read.

    THE WHOLE RULE IS IN WHICH LIST A REFERENCE LANDS IN. `candidates` only
    from a page that is the company's own and nobody else's (`Whose.of` says
    "own"), or from an address of their own whose answer pointed straight at
    the board. Everything else that names a board goes to `off_site`: a page
    that left the site, a page outside the part of a host that is theirs, a
    website two records share, a frame inside somebody else's frame, a
    redirect that went by way of somebody else's address. `via` is the
    own-site page a followed link was found on, so "left for" can say from
    where. (`who` may be a bare website, for a caller with no record in
    hand: the host and path rules then hold and the ones that need the
    other records cannot.)
    """
    if not isinstance(who, Whose):
        who = Whose({"website": who})
    website = who.site
    final = pr["final"]
    kind, why = who.of(final)
    theirs = kind in ("own", "shared")
    page = {"asked": pr["asked"], "final": final, "same_site": theirs,
            "status": pr["status"], "note": privacy.redact(pr.get("note") or "")}
    readable = False
    left_from = pr["asked"] if own_site(pr["asked"], website) else (via or pr["asked"])
    # A BROWSER THAT ENDED ON A BOARD'S OWN ADDRESS, by a redirect only a
    # browser is given, has found the same pointer a server's redirect is.
    # It is held to the same rule (every address on the way), and the
    # board's own page is not read for more.
    if (pr["status"] == "ok" and kind == "other" and pr.get("opened")
            and refs_in_address(final)[0]):
        hops = [h for h in (pr.get("hops") or ()) if h != final] or [pr["opened"]]
        pr = {**pr, "status": "pointer",
              "pointer": {"to": final, "from": hops[-1], "hops": hops}}
        page["note"] = "the browser was sent on to a job board's address"
    if pr["status"] == "pointer":
        ptr = pr["pointer"]
        to = ptr["to"]
        hops = list(ptr.get("hops") or ()) or [ptr.get("from") or pr["asked"]]
        kinds = [who.of(h)[0] for h in hops]
        ours = [h for h, k in zip(hops, kinds) if k in ("own", "shared")]
        # "STRAIGHT" MEANS: every address asked on the way was the company's
        # own, or was on the host the answer finally pointed at (a Workday
        # tenant's front door sending its reader on to its one site). Own
        # site -> the parent's site -> the parent's board, and own site -> a
        # link shortener -> a board, are somebody else's redirect and not
        # the company's pointer.
        straight = bool(ours) and all(k in ("own", "shared") or _host(h) == _host(to)
                                      for h, k in zip(hops, kinds))
        mine = straight and "shared" not in kinds
        # (what pointed: their own last address when the pointer is theirs,
        # and otherwise the address that really did the pointing)
        seen_on = ours[-1] if straight else hops[-1]
        boards, others = refs_in_address(to)
        for _s, _e, bkind, ref, _k in boards:
            (rec["candidates"] if mine else rec["off_site"]).append({
                "type": bkind, "ref": ref, "element": "redirect", "saw": _short(to),
                "times": 1, "page": seen_on, "hop": hop})
        for vendor in dict.fromkeys(v for _s, _e, v in others):
            rec["elsewhere"].append({"vendor": vendor, "saw": _short(to), "page": seen_on,
                                     "element": "redirect", "times": 1})
        page["status"], page["same_site"] = "left-site", False
        doubt = ""
        if boards and not straight:
            doubt = ("it got there by way of an address that is not the company's own, "
                     "so it is filed under off_site")
        elif boards and not mine:
            doubt = "filed under off_site: " + next(
                (who.of(h)[1] for h, k in zip(hops, kinds) if k == "shared"), "")
        page["note"] = "; ".join(x for x in (page["note"], doubt) if x)
        for h, k in zip(hops, kinds):
            if k == "other" and _host(h) != _host(to):
                _leave(rec, left_from, h)    # the site it went through on the way
        _leave(rec, left_from, to)
    elif pr["status"] == "ok":
        if theirs:
            frames, asks, through = _by_document(pr, who)
        else:
            # not their page: whose frame is whose no longer matters, all of
            # it is filed as seen on a page that is not theirs
            frames, asks, through = _flat(pr.get("frames")), _flat(pr.get("requests")), {}
        boards, others = sightings(pr["html"], final, website,
                                   requests_seen=asks, frames_seen=frames)
        own = kind == "own"
        for row in boards:
            (rec["candidates"] if own else rec["off_site"]).append({**row, "page": final, "hop": hop})
        # what another site's frame inside the page named. A board the
        # company's own page already points to is not said again from there:
        # the frame it embeds asking that same board for its list adds a row
        # under off_site and tells nobody anything.
        named = {_same_board(r) for r in boards} if own else set()
        inside = 0
        for where, seen in through.items():
            rows, _ = sightings("", where, website, requests_seen=seen["requests"],
                                frames_seen=seen["frames"])
            for row in rows:
                if _same_board(row) not in named:
                    rec["off_site"].append({**row, "page": where, "hop": hop})
                    inside += 1
        if theirs:
            page["status"], readable = "read", True
            # what the company's own page uses; a parent's page that the site
            # left for says nothing about this company's platform
            for row in others:
                rec["elsewhere"].append({"vendor": row["vendor"], "saw": row["saw"],
                                         "page": final, "element": row["element"],
                                         "times": row["times"]})
            links = pr.get("links") or []
            n = job_like(links)
            rec["job_like_links"] = max(rec["job_like_links"] or 0, n)
            # "PLAINLY": a careers page, saying so, with no postings beside
            # the sentence. "Check back soon" under twelve open roles is not it.
            if (n < 2 and careers_page(final, pr["html"], pr.get("words") or "")
                    and discover_ats.NO_OPENINGS.search(pr.get("words") or "")):
                rec["says_no_openings"] = True
            page["note"] = f"{len(boards)} board reference(s)"
            if kind == "shared":
                page["note"] += f", filed under off_site: {why}"
            if inside:
                page["note"] += (f"; {inside} more inside another site's frame on this "
                                 f"page, filed under off_site")
        else:
            page["status"] = "left-site"
            page["note"] = (f"{why if kind == 'part' else 'landed on another site'}; "
                            f"{len(boards)} board reference(s) there, filed under off_site")
            if kind == "other":
                _leave(rec, left_from, final)
    else:
        if kind == "other":
            # refused or broken, and somewhere else: both facts are kept
            _leave(rec, left_from, final)
        elif kind == "part":
            page["note"] = f"{page['note']}; {why}".strip("; ")
    if not theirs and pr["status"] != "pointer":
        # where it landed may itself be a platform's address (a Gusto posting,
        # a Workday tenant with no site): that much is known without reading it
        for vendor in dict.fromkeys(v for _s, _e, v in refs_in_address(final)[1]):
            rec["elsewhere"].append({"vendor": vendor, "saw": _short(final),
                                     "page": pr["asked"], "element": "redirect", "times": 1})
    if guessed and "guessed path" not in page["note"]:
        # the site was never seen to link this address; we typed it
        page["note"] = (page["note"] + " (a guessed path)").strip()
    rec["pages"].append(page)
    return readable


# The nonsense path discover_ats asks, for the same reason: one host in five
# answers 200 to an address that cannot exist, and on those a 200 from a
# guessed /careers says nothing at all (it once wrote ~127 records pointing
# at home pages).
CONTROL_PATH = "/zz-no-such-page-8471"
GUESSES = ("/careers", "/jobs")


def same_page(a: str, b: str) -> bool:
    """Two answers that are one page: the same visible words, or the same
    title over words of the same length. The second half is for the not-found
    page that prints the address it was asked for, which no fingerprint of
    the whole text can match."""
    if not a or not b:
        return False
    va, vb = visible(a), visible(b)
    if va == vb:
        return True
    ta, tb = _inside(a, "title"), _inside(b, "title")
    if not ta or ta != tb:
        return False
    return abs(len(va) - len(vb)) <= 0.03 * max(len(va), len(vb), 1)


async def look(company: dict, mode: str, read, plain=read_plain,
               rec: dict | None = None, on_file: dict | None = None) -> dict:
    """Walk one company's own site for the board it points to.

    In order: the website; the careers address on file when it is on their
    own site; then the careers-looking links those pages offer. `read(url,
    website, deadline)` reads one address (a browser in render mode);
    `plain` is always the plain reader, used to test a guessed path before
    anything is made of it. `rec` is filled in place, so a caller that has to
    stop the walk still holds what it had found. `on_file` is site_index()
    of every record on file: without it nothing can know that the page
    being read is another record's website too.
    """
    rec = _blank(company) if rec is None else rec
    who = Whose(company, on_file)
    site = who.site
    if not site.startswith("http") or not _host(site):
        rec["error"] = "the website on file is not an address"
        return rec
    deadline = time.monotonic() + COMPANY_SECONDS[mode]
    queue: list[dict] = []
    done: set[str] = set()
    landed: dict[str, dict] = {}        # the row written for each address a read landed on
    counter = [0]

    def push(url: str, hop: int, rank: int, length: int = 0, via: str | None = None) -> None:
        k = _key(url)
        if k in done or any(_key(q["url"]) == k for q in queue):
            return
        counter[0] += 1
        queue.append({"url": url, "hop": hop, "rank": rank, "length": length,
                      "n": counter[0], "via": via})

    def offer(pr: dict, hop: int) -> None:
        picked, lost = pick_links(pr.get("links") or [], site)
        for row in picked:
            push(row["url"], hop + 1, row["rank"], row["length"], via=pr["final"])
        strays.extend((u, w, pr["final"]) for u, w in lost)

    push(site, 0, -2)
    ref = (company.get("ats") or {}).get("ref")
    on_record = isinstance(ref, str) and ref.startswith("http") and own_site(ref, site)
    if on_record:
        push(ref, 0, -1)
    pages = 0
    home = None                 # the website's own page, once read
    careers_read = False
    answered = False            # a careers link the site itself offered has been asked
    guessed = False
    weak = looks = 0
    strays: list[tuple[str, str, str]] = []
    while pages < PAGES[mode]:
        if time.monotonic() > deadline:
            rec["error"] = (f"gave up after {COMPANY_SECONDS[mode]}s with "
                            f"{len(queue)} address(es) unread")
            break
        if not queue:
            # GUESS ONLY WHEN THE SITE OFFERED NOTHING, AND BELIEVE A GUESS
            # ONLY WHEN IT IS A REAL PAGE. The home page was read, no careers
            # page has been, and nothing is left to follow: /careers and
            # /jobs are worth one ask each. A guess counts when the site
            # answers 200 with a page that is neither its home page nor the
            # page it gives an address that cannot exist.
            #
            # "OFFERED NOTHING" IS MEANT. A site whose own careers link was
            # followed has answered, whatever the link came to: a redirect
            # to its board, a page on its parent's site, a dead address. So
            # has one whose careers link plainly leaves for another host, or
            # links the platform it posts on. The first version went on to
            # ask such a site for an address that cannot exist and for
            # /careers besides - two requests, one of them nonsense on
            # purpose, of every company whose careers link goes straight to
            # its board (the blind review of 2026-11-18).
            if (guessed or home is None or careers_read or answered or strays
                    or rec["candidates"] or rec["off_site"]
                    or any(e.get("element") == "href" for e in rec["elsewhere"])):
                break
            guessed = True
            root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(home["final"]))
            ctl = await plain(root + CONTROL_PATH, site, deadline)
            if ctl["status"] == "blocked":
                break                   # the site has said no; nothing is guessed at it
            fakes = [home["html"]] + ([ctl["html"]] if ctl["status"] == "ok" else [])
            for g in GUESSES:
                if pages >= PAGES[mode] or time.monotonic() > deadline:
                    break
                if _key(root + g) in done:
                    continue
                pages += 1
                got = await plain(root + g, site, deadline)
                got["note"] = ((got.get("note") or "") + " (a guessed path)").strip()
                turned_away = None
                if got["status"] == "ok":
                    path = urllib.parse.urlsplit(got["final"]).path.rstrip("/")
                    # (landing on a page already read is the site sending a
                    # path it does not have back to one it does)
                    if own_site(got["final"], site) and (
                            not path or _key(got["final"]) in done
                            or any(same_page(got["html"], f) for f in fakes)):
                        done.add(_key(root + g))
                        rec["pages"].append({
                            "asked": got["asked"], "final": got["final"],
                            "same_site": who.theirs(got["final"]), "status": "error",
                            "note": "a guessed path, and not a real page: the site answers "
                                    "it with its home page or its not-found page"})
                        continue
                    if mode == "render" and own_site(got["final"], site):
                        # (the plain read above asked robots.txt at every hop)
                        shown = await read(root + g, site, deadline, landed={**got, "html": ""})
                        if shown["status"] in ("ok", "pointer"):
                            got = shown
                            got["note"] = ((got.get("note") or "") + " (a guessed path)").strip()
                        else:
                            # THE BROWSER WAS TURNED AWAY FROM A PAGE THE PLAIN
                            # REQUEST HAD JUST READ. What was read is still
                            # read: the first version threw the plain body
                            # away with its board's frame in it, and wrote
                            # the refusal down as a page with no board.
                            turned_away = shown
                done.update((_key(root + g), _key(got["final"])))
                found = absorb(rec, got, 1, who, guessed=True)
                landed[_key(got["final"])] = rec["pages"][-1]
                if turned_away is not None:
                    rec["pages"][-1]["note"] += "; read without the browser, which was turned away"
                    absorb(rec, turned_away, 1, who, guessed=True)
                refused = "blocked" in (got["status"], (turned_away or {}).get("status"))
                if found:
                    careers_read = True
                    if not refused:
                        offer(got, 1)
                if refused:
                    queue.clear()       # a refusal is not asked twice, by either reader
                    break
                if found or got["status"] == "pointer":
                    # one real careers page is the answer, and so is a
                    # /careers that sends its reader straight to a platform
                    break
            if not queue:
                break
            continue
        queue.sort(key=lambda q: (q["rank"], -q["hop"], q["length"], q["n"]))
        item = queue.pop(0)
        # ONE WEAK LINK AND ONE LOOK AT A PLATFORM, NO MORE. A link whose
        # address merely contains "job" is a product page as often as a
        # careers page, and a company with no careers link would otherwise
        # have three of those read to learn nothing.
        # Neither is tried at all once a page has named a board: the answer
        # is in hand, and the rest would be requests made for nothing.
        if item["rank"] >= 2:
            if rec["candidates"] or (weak if item["rank"] == 2 else looks):
                continue
            weak, looks = weak + (item["rank"] == 2), looks + (item["rank"] >= 3)
        if item["hop"] > 0 and item["rank"] % 3 <= 1:
            answered = True             # (a link that reads as careers, on a platform or not)
        done.add(_key(item["url"]))
        pages += 1
        pr = await read(item["url"], site, deadline)
        key = _key(pr["final"])
        if pr["status"] == "ok" and key in done and key != _key(item["url"]):
            # IT LANDED ON A PAGE ALREADY ASKED: say so, count nothing twice,
            # and say of it what was said of that page. An acquired company
            # whose website and careers address both redirect to the buyer's
            # front page was written down as left-site and then as "read" -
            # and one "read" is what tells the run's own count, and the
            # door, that a company's site was looked at.
            was = landed.get(key) or {}
            theirs = was.get("same_site", who.theirs(pr["final"]))
            status = (was.get("status") if was.get("status") in ("read", "left-site")
                      else ("read" if theirs else "left-site"))
            rec["pages"].append({
                "asked": pr["asked"], "final": pr["final"], "same_site": theirs,
                "status": status,
                "note": ("the same page as an address already read" if status == "read"
                         else "lands on the same page, not on their own site, as an "
                              "address already asked")})
            continue
        done.add(key)
        readable = absorb(rec, pr, item["hop"], who, via=item["via"])
        landed[key] = rec["pages"][-1]
        if pr["status"] == "blocked":
            # A REFUSAL IS NOT ASKED TWICE. The site said no to this crawler;
            # its other pages are not tried to see whether they agree.
            break
        if not readable:
            continue
        if item["hop"] == 0 and home is None and _key(item["url"]) == _key(site):
            home = pr
        if item["hop"] > 0 or (on_record and _key(item["url"]) == _key(ref)):
            careers_read = True
        offer(pr, item["hop"])
    # Careers-looking links that leave for a host nothing here knows: a
    # platform nobody has listed, or a parent's careers site. Named, capped,
    # and never followed.
    seen_hosts: set[str] = set()
    for url, _words, on_page in strays:
        name = _site_of(_host(url))
        if name in seen_hosts or len(seen_hosts) >= 6:
            continue
        seen_hosts.add(name)
        rec["elsewhere"].append({"vendor": f"other:{name}",
                                 "saw": _short(url), "page": on_page,
                                 "element": "href", "times": 1})
    jobvite_account_board(rec)
    rec["elsewhere"] = [e for e in rec["elsewhere"] if not _board_furniture(e, rec["candidates"])
                        and not same_tenant(e, rec["candidates"])]
    rec.update(_no_addresses(rec))
    return rec


def jobvite_account_board(rec: dict) -> None:
    """The whole site's say on a Jobvite account named by its company id (a
    jobvitefeed candidate), filled in place.

    WHERE THEIR SITE NAMES A jobs.jobvite.com CAREER SITE, on any page, that
    is the candidate, and every account form is filed as it was before the
    feed had a reader: jobvite-tenant, a doubt (the page-level rule is
    jobvite_account_forms; this is the same across pages). Otherwise the
    account is the board.

    ITS FURNITURE IS NOT SORTED HERE: the widget's own assets beside it were
    left out as its page was read (sightings, jobvite_widget_asset), off the
    whole address. Here only `saw` was left, cut to 160 characters and its
    people taken out, and a page's other Jobvite address - a jobs.jobvite.com
    career site's loader, written near the start of an app.jobvite.com
    address - read as the widget's own and was dropped, and the door wired
    the feed with no doubt (review 12's verifier, 2026-11-23)."""
    feeds = [c for c in rec["candidates"] if c.get("type") == "jobvitefeed"]
    if not feeds:
        return
    if any(c.get("type") == "jobvite" for c in rec["candidates"]):
        rec["candidates"] = [c for c in rec["candidates"] if c.get("type") != "jobvitefeed"]
        rec["elsewhere"].extend({"vendor": "jobvite-tenant", "saw": c.get("saw"),
                                 "page": c.get("page"), "element": c.get("element"),
                                 "times": c.get("times") or 1} for c in feeds)


def _board_host(kind: str, ref) -> str:
    """The host a board is served from, where the finder reads pages ON the
    board's own host: a Teamtailor career site on a domain of the company's
    own is their site, and is walked, and so is a SuccessFactors Career Site
    Builder board, which is always on theirs. (A board on a vendor's host is
    never walked - a link that names a board is not followed - so no other
    type has pages here to tell apart.)"""
    if kind in ("teamtailor", "successfactors") and isinstance(ref, str):
        return _host(ref)
    return ""


def _board_furniture(row: dict, candidates: list) -> bool:
    """A form of a platform we read that names no board, seen on a page served
    by that platform's board ITSELF - the page's host is the board's host -
    is the board's own furniture. Powerfleet's Teamtailor site answers its
    second page of jobs as a fragment with no <head>, so no feed link, and
    its pictures come from teamtailor-cdn.com (2026-11-21): that is the board
    showing its own jobs, not a second place they hire through. The page
    level rule (sightings) cannot see it, because the board is named on the
    site's other pages. So is a legacy portal's address a Career Site Builder
    board's own pages name (_ON_ITS_OWN_BOARD): its tenant's sign-in."""
    vendor = str(row.get("vendor") or "")
    kind = _NO_BOARD_IN_IT.get(vendor) or _ON_ITS_OWN_BOARD.get(vendor)
    saw = str(row.get("saw") or "")
    if vendor in _ON_ITS_OWN_BOARD and (legacy_tenant(saw) or len(saw) >= 160):
        # a legacy portal naming a tenant (or that may, past the cut) is the
        # board's own only where its page showed the board's tenant (sightings)
        return False
    page = _host(str(row.get("page") or ""))
    return bool(kind and page) and any(
        c.get("type") == kind and _board_host(kind, c.get("ref")) == page for c in candidates)


def _no_addresses(rec: dict) -> dict:
    """The record with any person's e-mail address taken out of every string
    in it. `saw` and the notes were redacted as they were written; the
    ADDRESSES of pages were not, and an address can carry one in its query
    (a redirect to ...?to=somebody@...). The file is an artifact of a public
    repository, and the door copies from it into a public one. A person's
    name in an address's query goes the same way, in every address the record
    keeps - the pages asked and landed on, the hops, the pointers - not only
    in `saw` (fifth blind review, 2026-11-22: a talent-network redirect kept
    sd=<a recruiter's name> in pages[].final)."""
    return _no_names(privacy.redact_deep(rec))


def _no_names(obj):
    """_PERSON_PARAM over every string in a JSON-shaped value; the shape is kept."""
    if isinstance(obj, str):
        return _no_person(obj)
    if isinstance(obj, list):
        return [_no_names(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _no_names(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- the list

def _read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def refused_ids() -> set[str]:
    """Companies whose found board a person (or a two-reader review) refused.

    A BOARD SOMEBODY REFUSED MUST NOT COME BACK. Concourse's Ashby board was
    unwired by hand after seven of a finance company's postings reached the
    public site, and the next sweep proposed it straight back. Both places a
    refusal is kept are read: the "Boards we found" rulings, and rejected
    board proposals.
    """
    out = {k for k, v in _read_json(DATA / "board_proposal_rulings.json", {}).items()
           if isinstance(v, dict) and not v.get("accept")}
    for k, v in _read_json(DATA / "agent_proposals.json", {}).items():
        if k.startswith("board:") and isinstance(v, dict) and v.get("status") == "rejected":
            out.add(v.get("id") or k.split(":", 1)[1])
    return out


def in_shard(key: str, k: int, n: int) -> bool:
    """A split that is the same on every machine and every run (Python's own
    hash() is salted per process, so it is not)."""
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % n == k - 1


def shard_key(company: dict) -> str:
    """What a company is split by: THE SITE ITS WEBSITE IS ON, not its id.

    Split by id, the eleven hosts that are the website of two or three
    records each (a product line on file beside its parent, one company
    entered twice) were dealt to different machines, and the workflow's
    promise that no site is read by two machines at once was untrue for
    them. Two records of one site now go to one machine, where the one
    gate every request at a host queues behind can space them.
    """
    host = _host(str(company.get("website") or ""))
    return _site_of(host) if host else str(company.get("id") or "")


def worklist(companies: list[dict], *, only: set[str] | None = None,
             include_wired: bool = False) -> tuple[list[dict], dict]:
    """Who gets looked at, and a count of who does not and why.

    Companies with a careers page nothing enumerates (`html`) or no board on
    file (`unknown`), that have a website. NEVER a company a person ruled out
    of scope and never one whose found board was refused - and an --only list
    does not change that: a list is a decision about what to look at, not a
    reversal of a ruling (discover_ats, the Concourse lesson).
    """
    refused = refused_ids()
    skipped = collections.Counter()
    todo = []
    known = {c.get("id") for c in companies}
    if only:
        skipped["not on file"] = len(only - known)
    for c in companies:
        cid = c.get("id")
        if only is not None and cid not in only:
            continue
        kind = (c.get("ats") or {}).get("type") or "unknown"
        if kind not in ("html", "unknown") and not (only is not None and include_wired):
            if only is not None:        # (unasked, it was never on the list)
                skipped["a structured board is already on file"] += 1
        elif c.get("out_of_scope"):
            skipped["ruled out of scope by a person"] += 1
        elif cid in refused:
            skipped["a found board was already refused"] += 1
        elif not c.get("website"):
            skipped["no website on file"] += 1
        else:
            todo.append(c)
    return todo, {k: v for k, v in skipped.items() if v}


# ----------------------------------------------------------------- the run

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _save(path: pathlib.Path, mode: str, companies: dict, since: str | None = None) -> None:
    """The whole file, written beside itself and moved into place, so a run
    that is stopped mid-write leaves the last good file and not half of one.
    `since` is the stamp the file carries: when its oldest record was read."""
    doc = {"generated": since or _now(), "mode": mode, "companies": companies}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# What a record says when the look failed on OUR side: this program crashing,
# our browser not finishing. Neither is a fact about the company's site, and
# neither is an answer the site gave.
CRASHED, TIMED_OUT = "the look crashed", "the look did not finish in time"


def unfinished(rec) -> bool:
    """A record a resumed run must make again, because nothing was learned.

    ONLY OUR OWN FAILURES. A run that picks a file up again skipped everyone
    already in it, so a company whose look had crashed, or whose browser had
    not finished, was never looked at again and sat in the file under a
    fresh date (the blind review of 2026-11-18). NOT a site that refused
    (robots.txt, a bot wall): a refusal is an answer, and a resumed run does
    not ask it twice. And not a site that was merely slow or unreachable:
    that is written down as not read, which is what it was.
    """
    return not _whole(rec) or str(rec.get("error") or "").startswith((CRASHED, TIMED_OUT))


def _whole(rec) -> bool:
    """Is this a record in this file's shape: every field there, and the
    lists lists."""
    return (isinstance(rec, dict) and set(_blank({})) <= set(rec)
            and all(isinstance(rec[k], list)
                    for k in ("pages", "candidates", "off_site", "elsewhere", "left_site")))


def _line(cid: str, rec: dict) -> str:
    read = sum(1 for p in rec["pages"] if p["status"] == "read")
    bits = [f"{read}/{len(rec['pages'])} page(s) read"]
    for label, key in (("points to", "candidates"), ("off-site", "off_site")):
        seen = collections.OrderedDict()
        for r in rec[key]:
            ref = "/".join(r["ref"]) if isinstance(r["ref"], list) else r["ref"]
            seen.setdefault(f"{r['type']}:{ref}", []).append(f"{r['element']} x{r['times']}")
        if seen:
            bits.append(label + " " + "; ".join(f"{k} ({', '.join(v)})" for k, v in seen.items()))
    if rec["elsewhere"]:
        bits.append("elsewhere " + ", ".join(dict.fromkeys(r["vendor"] for r in rec["elsewhere"])))
    if rec["left_site"]:
        bits.append("leaves for " + ", ".join(x["to_host"] for x in rec["left_site"]))
    bad = [f"{p['status']} ({p['note']})" for p in rec["pages"]
           if p["status"] in ("robots", "blocked", "error")]
    if bad:
        bits.append("not read: " + "; ".join(bad))
    if rec["says_no_openings"]:
        bits.append("says no openings")
    if rec["error"]:
        bits.append("ERROR " + rec["error"])
    return f"  {cid[:30]:31}" + " | ".join(bits)


def summary(companies: dict) -> list[str]:
    """Counts only. The Actions log is public, and a company's name beside
    "blocked" or "left for another site" is not ours to publish there."""
    n = len(companies)
    pointed = [r for r in companies.values() if r["candidates"]]
    unread = [r for r in companies.values()
              if not any(p["status"] == "read" for p in r["pages"])]
    left = [r for r in companies.values() if r["left_site"]]
    nothing = [r for r in companies.values() if not r["candidates"]
               and any(p["status"] == "read" for p in r["pages"])]
    doubted = [r for r in nothing if r["off_site"]]
    by_kind = collections.Counter()
    for r in pointed:
        for kind in {c["type"] for c in r["candidates"]}:
            by_kind[kind] += 1
    # a tenant form or a no-board form belongs to a platform we read: it is
    # not one with "no reader" (third review's Jobvite reviewer, 2026-11-21)
    by_vendor, by_form = collections.Counter(), collections.Counter()
    for r in companies.values():
        for v in {e["vendor"].split(":")[0] for e in r["elsewhere"]}:
            (by_form if v in TENANT_FORMS or v in _NO_BOARD_IN_IT else by_vendor)[v] += 1
    out = [f"{n} companies in the file",
           f"  {len(pointed)} whose own site points to a board we can read",
           f"  {len(nothing)} read, no readable board named as their own"
           + (f" ({len(doubted)} of them with one named where it is not theirs to "
              f"claim: off_site)" if doubted else ""),
           f"  {len(unread)} NOT READ (robots.txt, a bot wall, an error): not a 'no board'",
           f"  {len(left)} whose site leaves for another host",
           f"  {sum(1 for r in companies.values() if r['says_no_openings'])} "
           f"whose careers page says it has no openings",
           f"  {sum(1 for r in companies.values() if r['error'])} where the look itself "
           f"went wrong (the record says how)"]
    if by_kind:
        out.append("  by platform: " + ", ".join(f"{k} {v}" for k, v in by_kind.most_common()))
    if by_vendor:
        out.append("  no reader for: " + ", ".join(f"{k} {v}" for k, v in by_vendor.most_common()))
    if by_form:
        out.append("  forms of platforms we read: "
                   + ", ".join(f"{k} {v}" for k, v in by_form.most_common()))
    return out


class BrowserGone(Exception):
    """The browser a sweep was reading with has died. Carries the kind of
    error and nothing else: what Playwright says when it dies names the
    address it was opening, and the log this ends up in is public."""


async def sweep(todo: list[dict], a, out: pathlib.Path, results: dict,
                on_file: dict | None = None, since: str | None = None) -> int:
    """Look at each company in turn and keep the file current. Returns how
    many were left for another run (the --budget ran out). `on_file` is
    site_index() of every record, handed to each look."""
    mode = "render" if a.render else "static"
    started = time.monotonic()
    fresh = [0]
    left = [0]

    def over() -> bool:
        return bool(a.budget) and time.monotonic() - started > a.budget

    def keep(c: dict, rec: dict) -> None:
        # (again here: a look that was stopped from outside never reached
        # its own last line)
        rec = results[c["id"]] = _no_addresses(rec)
        fresh[0] += 1
        if a.show:
            print(_line(c["id"], rec), flush=True)
        if fresh[0] % SAVE_EVERY == 0:
            _save(out, mode, results, since)
            if not a.show:
                print(f"  ... {fresh[0]}/{len(todo)}", flush=True)

    if not a.render:
        for c in todo:
            if over():
                left[0] += 1
                continue
            rec = _blank(c)
            try:
                # (every read inside the look is on the company's own clock;
                # this one is over all of it, for whatever that clock missed)
                await asyncio.wait_for(look(c, "static", read_plain, rec=rec, on_file=on_file),
                                       timeout=COMPANY_SECONDS["static"] + 30)
            except asyncio.TimeoutError:
                rec["error"] = TIMED_OUT
            except Exception as exc:         # noqa: BLE001 - one site must not end the run
                rec = {**_blank(c), "error": f"{CRASHED}: {type(exc).__name__}"}
            keep(c, rec)
            await asyncio.sleep(a.pause)
        return left[0]

    # ONE BROWSER, TWO PAGES AT A TIME (RENDER_AT_ONCE). Playwright is
    # imported here and nowhere else, so a plain run and the self-test never
    # need the browser (CLAUDE.md: it stays out of the run path).
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit("--render needs Playwright: pip install playwright && "
                         "python -m playwright install chromium") from exc
    gate = asyncio.Semaphore(RENDER_AT_ONCE)
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        async def one(c: dict) -> None:
            async with gate:
                if over():
                    left[0] += 1
                    return
                rec, ctx = _blank(c), None
                try:
                    # (no service workers: a request one makes has no page to
                    # its name, and the context's one route refuses whatever
                    # it cannot trace to the page we opened)
                    ctx = await browser.new_context(user_agent=render_fetch.UA,
                                                    viewport={"width": 1280, "height": 1000},
                                                    service_workers="block")

                    async def read(url, website, deadline=None, landed=None):
                        return await read_rendered(ctx, url, website, deadline, landed)
                    await asyncio.wait_for(look(c, "render", read, rec=rec, on_file=on_file),
                                           timeout=COMPANY_SECONDS["render"] + 60)
                except asyncio.TimeoutError:
                    rec["error"] = TIMED_OUT
                except Exception as exc:     # noqa: BLE001
                    # OUR BROWSER DYING IS NOT A FACT ABOUT THEIR SITE. Written
                    # down as this company's result it would be skipped as
                    # "already looked at" by the run that resumes; the run
                    # stops instead and the company is asked again.
                    if not browser.is_connected():
                        raise BrowserGone(type(exc).__name__) from None
                    rec["error"] = f"{CRASHED}: {type(exc).__name__}"
                finally:
                    try:
                        if ctx is not None:
                            await ctx.close()
                    except Exception:        # noqa: BLE001 - it went with the browser
                        pass
                if not browser.is_connected():
                    # (a page that failed to open is written down as that and
                    # the walk goes on, so a browser that died under it comes
                    # out of the look as "a page did not open")
                    raise BrowserGone("closed")
                keep(c, rec)
                await asyncio.sleep(a.pause)
        try:
            await asyncio.gather(*[one(c) for c in todo])
        finally:
            try:
                await browser.close()
            except Exception:                # noqa: BLE001 - already gone
                pass
    return left[0]


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):     # pragma: no cover
        pass
    ap = argparse.ArgumentParser(
        description="Find the board each company's own website points to. Writes one "
                    "results file and nothing else.")
    ap.add_argument("--out", required=True,
                    help="where the results go. REQUIRED, and never inside data/: these "
                         "are findings, and only the wiring door writes data/")
    ap.add_argument("--render", action="store_true",
                    help="read each page in one Chromium (needs Playwright), and also "
                         "record what the page asked for and what its frames loaded")
    ap.add_argument("--limit", type=int, default=0,
                    help="look at no more than this many companies this run")
    ap.add_argument("--only", default="",
                    help="only these company ids, comma-separated")
    ap.add_argument("--shard", default="",
                    help="K/N: the K-th of N equal parts of the list, split by the site "
                         "each website is on, the same on every machine")
    ap.add_argument("--budget", type=int, default=0,
                    help="seconds for the whole run; whoever is left waits for the next")
    ap.add_argument("--pause", type=float, default=0.3,
                    help="seconds to rest between companies")
    ap.add_argument("--include-wired", action="store_true",
                    help="with --only: also look at a company that already has a "
                         "structured board on file (its record says which, under 'was')")
    ap.add_argument("--show", action="store_true",
                    help="print one line per company (ids and what was found). Off by "
                         "default: a workflow's log is public and carries counts only")
    a = ap.parse_args(argv)

    out = pathlib.Path(a.out).expanduser().resolve()
    if out == DATA.resolve() or DATA.resolve() in out.parents:
        print(f"--out {a.out} is inside data/. These are findings for the wiring door "
              f"to read, not data: write them somewhere else.", file=sys.stderr)
        return 2
    mode = "render" if a.render else "static"
    shard = None
    if a.shard:
        m = re.fullmatch(r"(\d+)/(\d+)", a.shard.strip())
        if not m or not (1 <= int(m.group(1)) <= int(m.group(2))):
            print(f"--shard takes K/N with 1 <= K <= N, not {a.shard!r}", file=sys.stderr)
            return 2
        shard = (int(m.group(1)), int(m.group(2)))

    results: dict = {}
    since = _now()
    if out.exists():
        # RESUMABLE: whoever is in the file already, with something learned,
        # is not asked again (`unfinished` says who is). A file from the
        # other mode is refused rather than mixed - a company read without a
        # browser has not been looked at by one.
        prior = _read_json(out, None)
        if not isinstance(prior, dict) or not isinstance(prior.get("companies"), dict):
            print(f"{out} exists and is not a results file of this script; "
                  f"move it or name another --out", file=sys.stderr)
            return 2
        if prior.get("mode") != mode:
            print(f"{out} was written in {prior.get('mode')} mode and this run is "
                  f"{mode}: give each mode its own --out", file=sys.stderr)
            return 2
        # (a record that is not this file's shape is not kept: it would be
        # counted in the summary below as if it said something)
        results = {k: v for k, v in prior["companies"].items() if _whole(v)}
        # THE FILE KEEPS THE DATE OF ITS OLDEST RECORD. It has one date, and
        # stamping it afresh put today's on findings read on another day.
        if isinstance(prior.get("generated"), str) and prior["generated"] and results:
            since = prior["generated"]

    companies = json.loads((DATA / "companies.json").read_text())
    only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
    todo, skipped = worklist(companies, only=only, include_wired=a.include_wired)
    if shard:
        todo = [c for c in todo if in_shard(shard_key(c), *shard)]
    again = sum(1 for c in todo if c["id"] in results and unfinished(results[c["id"]]))
    already = sum(1 for c in todo if c["id"] in results) - again
    todo = [c for c in todo if c["id"] not in results or unfinished(results[c["id"]])]
    if a.limit:
        todo = todo[:a.limit]
    print(f"find_site_boards ({mode}): {len(todo)} to look at"
          + (f", {already} already in the file" if already else "")
          + (f", {again} of them again because the last look at them failed on our side"
             if again else "")
          + (f", part {shard[0]} of {shard[1]}" if shard else ""))
    for why, n in sorted(skipped.items()):
        print(f"  {n} not looked at: {why}")
    out.parent.mkdir(parents=True, exist_ok=True)
    left, failed = 0, ""
    try:
        left = asyncio.run(sweep(todo, a, out, results, site_index(companies), since))
    except BrowserGone as exc:
        failed = f"the browser stopped ({exc})"
    except Exception as exc:                 # noqa: BLE001
        # COUNTS ONLY, EVEN WHEN IT BREAKS. A traceback names the address
        # that was being opened, and a workflow's log is public. A person
        # running it by hand with --show gets the whole of it.
        if a.show:
            raise
        failed = f"the run stopped on an error of its own ({type(exc).__name__})"
    finally:
        _save(out, mode, results, since)
    for line in summary(results):
        print(line)
    if left:
        print(f"  {left} left for another run (the --budget ran out): run it again "
              f"with the same --out")
    print(f"written to {out}")
    print("NOTHING WAS WIRED AND NOTHING IN data/ CHANGED. Each candidate is what a "
          "page says, not whose board it is: the board has not been asked.")
    if failed:
        print(f"{failed}. What had been read is in the file; whoever was not reached "
              f"is not in it. Run it again with the same --out.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
