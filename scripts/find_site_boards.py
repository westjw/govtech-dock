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
`page` is the address the page landed on; for a redirect it is the own-site
address whose answer pointed at the board, and `saw` is where it pointed.

A page's status: `read` (landed on their own site and was read); `left-site`
(landed on another host, read or deliberately not; `left_site` names the
host, and a redirect straight to a board is one of these AND a candidate);
`robots`, `blocked`, `error` (not read, with the reason in `note`, and
same_site still saying where it was).

`elsewhere` is a tally, not a finding about ownership. Its vendors are the
platforms ats.py has no reader for (jobvite, ukg, pinpoint ...); the forms of
a platform it does read that name no board (gusto-posting, paylocity-other,
adp-other, workday-no-site, workday-myworkdaysite, greenhouse-shortlink);
where a company posts instead (linkedin, indeed ...); and `other:<site>` for
a job frame or a careers link that leaves for a host nothing here knows.
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
import sys
import time
import typing
import urllib.parse

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
    """
    a, b = _host(page), _host(website)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


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
# is not the site. discover_ats skips only en-US.
LOCALE = r"(?:[a-z]{2}-[a-z]{2}/)?"
_LOCALE_ONLY = re.compile(r"^[a-z]{2}[-_][a-z]{2}$", re.I)

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
}
# Jibe (iCIMS Talent Cloud) lives on the company's own careers host and its
# address names nobody. What gives it away is the script host its pages load.
# The ref is then the host of the page it was seen on, which is what
# ats.fetch_jibe takes.
JIBE_MARK = "jibecdn.com"

# Hosts and Lever's slugs are lower case whatever a page wrote. Everything
# else is kept as the page spelled it: SmartRecruiters' company id is
# case-sensitive (Xplor, Skyward1), and guessing a case is guessing a board.
LOWERED = {"lever", "recruitee", "breezy", "bamboohr", "jazzhr", "icims"}

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
        # Greenhouse's EU boards, its application-form embed, and the older
        # API host. (boards-api. is in find_boards; the lookbehind keeps this
        # one from matching inside it.)
        _here("greenhouse", r"(?:boards|job-boards)\.eu\.greenhouse\.io/"
                            r"(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]{2,60})"),
        _here("greenhouse", r"greenhouse\.io/embed/job_app\?for=([a-z0-9_-]{2,60})"),
        _here("greenhouse", r"(?<![a-z0-9-])api\.greenhouse\.io/v1/boards/([a-z0-9_-]{2,60})"),
        # Lever's API (what a page that draws its own list calls; Softdocs,
        # seen 2026-11-18) and its EU host.
        _here("lever", r"api\.(?:eu\.)?lever\.co/v0/postings/([a-z0-9_-]{2,60})"),
        _here("lever", r"jobs\.eu\.lever\.co/([a-z0-9_-]{2,60})"),
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
    ])

# Job platforms nothing here can read. Kept as a TALLY, because which reader
# is worth writing next is a question the repo could not answer: no tool
# recorded what a careers page actually loads. Also here: forms of platforms
# we DO read that carry no board in the address (a Gusto posting, a Paylocity
# job page, Workday's other host, whose pod-first refs ats.fetch_workday
# cannot take today), named apart so they are not counted as missing readers.
_B = r"(?<![a-z0-9-])"
ELSEWHERE: list[tuple[str, str, "re.Pattern"]] = [
    (vendor, hint, re.compile(pat, re.I)) for vendor, hint, pat in (
        ("jobvite", "jobvite.com", _B + r"jobvite\.com"),
        ("zohorecruit", "zohorecruit.", _B + r"zohorecruit\.(?:com|eu|in)"),
        ("ukg", "ultipro.", _B + r"ultipro\.(?:com|ca)"),
        ("ukg", "ukg.net", _B + r"ukg\.net"),
        ("dayforce", "dayforcehcm.com", _B + r"dayforcehcm\.com"),
        ("paycom", "paycomonline.", _B + r"paycomonline\.(?:net|com)"),
        ("paycor", "recruitingbypaycor.com", _B + r"recruitingbypaycor\.com"),
        ("pinpoint", "pinpointhq.com", _B + r"pinpointhq\.com"),
        ("applicantpro", "applicantpro.com", _B + r"applicantpro\.com"),
        ("teamtailor", "teamtailor", _B + r"teamtailor(?:-cdn)?\.com"),
        ("comeet", "comeet.", _B + r"comeet\.(?:co|com)(?![a-z0-9-])"),
        ("eightfold", "eightfold.ai", _B + r"eightfold\.ai"),
        ("phenom", "phenompeople.com", _B + r"phenompeople\.com"),
        ("successfactors", "successfactors.", _B + r"successfactors\.(?:com|eu)"),
        ("successfactors", "sapsf.", _B + r"sapsf\.(?:com|eu|cn)"),
        ("taleo", "taleo.net", _B + r"taleo\.net"),
        ("oraclecloud", "oraclecloud.com", r"[a-z0-9-]{1,63}\.fa\.[a-z0-9-]{1,63}\.oraclecloud\.com"),
        ("clearcompany", "clearcompany.com", _B + r"clearcompany\.com"),
        ("hrmdirect", "hrmdirect.com", _B + r"hrmdirect\.com"),
        ("isolved", "isolvedhire.com", _B + r"isolvedhire\.com"),
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
    )]

# The forms above that belong to a platform we read. A redirect that lands on
# one is walked one hop further, because the platform's own next hop usually
# IS the board (a grnh.se short link, a Workday tenant with no site named).
# A redirect to any other platform stops where it is.
_NO_BOARD_IN_IT = {"workday-no-site": "workday", "gusto-posting": "gusto",
                   "paylocity-other": "paylocity", "adp-other": "adp",
                   "greenhouse-shortlink": "greenhouse"}

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


def _cut(page: str, opener: str, closer: str) -> str:
    """`page` without its opener...closer runs, by str.find and not a regex:
    a lazy regex rescans to the end of the page for every unclosed opener,
    which froze a twelve-worker sweep once (discover_ats._strip_blocks)."""
    low, out, i = page.lower(), [], 0
    while True:
        j = low.find(opener, i)
        if j < 0:
            out.append(page[i:])
            return "".join(out)
        out.append(page[i:j])
        k = low.find(closer, j + len(opener))
        if k < 0:
            return "".join(out)
        i = k + len(closer)


_TAG = re.compile(r"<(a|area|iframe|frame|embed|object|meta)\b[^<>]{0,6000}>", re.I)
_REFRESH = re.compile(r"http-equiv\s*=\s*[\"']?refresh", re.I)


def zones(page: str) -> list[tuple[str, str]]:
    """The page as (element, text) pieces, so a reference can say where it sat.

      script   every <script> block, its src and its body
      iframe   the opening tags of <iframe>, <frame>, <embed>, <object>
               (the whole tag: a lazy-loaded frame keeps its address in
               data-src, not src)
      href     the opening tags of <a> and <area>
      redirect a <meta http-equiv="refresh"> - the page sends its reader on
      text     everything else: visible words, and the attributes of every
               other tag

    A REFERENCE INSIDE AN HTML COMMENT IS NOT ON THE PAGE. A commented-out
    link is something the company took down, and the older finders count it.
    Comments are cut before anything is read (script blocks first, so a
    script wrapped in the old <!-- --> guard is still a script).
    """
    low, scripts, rest, i = page.lower(), [], [], 0
    while True:
        j = low.find("<script", i)
        if j < 0:
            rest.append(page[i:])
            break
        rest.append(page[i:j])
        end = low.find("</script", j)
        if end < 0:
            scripts.append(page[j:])            # unclosed: the rest is script
            break
        scripts.append(page[j:end])
        close = low.find(">", end)
        i = close + 1 if close > 0 else len(page)
    body = _cut("".join(rest), "<!--", "-->")
    pieces = {"href": [], "iframe": [], "redirect": []}
    text, at = [], 0
    for m in _TAG.finditer(body):
        tag = m.group(1).lower()
        if tag == "meta":
            if not _REFRESH.search(m.group(0)):
                continue                         # an ordinary meta tag is text
            where = "redirect"
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


def _saw(text: str, start: int, end: int) -> str:
    """The matched text widened to the whole address as the page wrote it,
    at most 160 characters, with any person's e-mail address taken out (the
    wiring door quotes this into a ruling, and no address reaches a tracked
    file)."""
    a, floor = start, max(0, start - 60)
    while a > floor and text[a - 1] not in _STOP:
        a -= 1
    b, ceil = end, min(len(text), a + 160)
    while b < ceil and text[b] not in _STOP:
        b += 1
    return privacy.redact(text[a:b][:160])


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
_TWO_PART_TLD = {"co", "com", "org", "net", "gov", "ac", "edu"}


def _site_of(host: str) -> str:
    """acme.co.uk from careers.acme.co.uk; near enough for a tally."""
    labels = host.lower().split(".")
    n = 3 if (len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _TWO_PART_TLD) else 2
    return ".".join(labels[-n:])


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
            row = others.setdefault((vendor, element), {
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
                "saw": privacy.redact(url[:160]), "times": 0})
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
    for element, text in zones(page or ""):
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
    # an address of a platform we read that names no board says nothing once
    # the page HAS named that platform's board (Paylocity's own scripts, 23
    # of them, beside the frame that loaded them)
    kinds = {kind for kind, _key, _el in boards}
    others = {k: v for k, v in others.items() if _NO_BOARD_IN_IT.get(k[0]) not in kinds}
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

    Returns {"asked", "final", "status", "note", "html", "pointer"} with
    status ok | pointer | robots | blocked | error. `html` is filled only for
    ok and only when `body` is set. A browser that is about to open the page
    itself passes body=False: the walk then reads just enough of the answer
    to tell a bot-check page from a real one, because A SITE THAT TURNED THE
    PLAIN REQUEST AWAY IS NOT ASKED AGAIN WITH A BROWSER. That is stricter
    than the nightly renderer, which opens whatever the plain fetch could
    not read; a sweep of every company's site errs toward taking no for an
    answer.
    """
    out = {"asked": url, "final": url, "status": "error", "note": "", "html": "",
           "pointer": None}
    asked: list[str] = []
    ahead = {"url": None}

    def one(u: str):
        resp = _request(u)
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
        if code in (202, 403, 429, 503):
            # 202 is SiteGround's challenge; the rest are the usual refusals.
            # A refusal is an answer: nothing asks this site again this run.
            out.update(status="blocked", note=f"HTTP {code}")
        elif code != 200:
            out.update(note=f"HTTP {code}")
        elif ctype and not re.search(r"html|xml|text/plain|json", ctype, re.I):
            out.update(note=f"not a page ({ctype.split(';')[0][:40]})")
        else:
            raw, cap = b"", (PAGE_CAP if body else PEEK)
            for chunk in resp.iter_content(chunk_size=65536 if body else PEEK):
                raw += chunk
                if len(raw) >= cap or (deadline and time.monotonic() > deadline):
                    break
            enc = ats.best_charset(raw, ctype, getattr(resp, "encoding", None))
            try:
                text = raw.decode(enc, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            # a small body that is mostly a challenge is a bot wall that
            # answered 200 (discover_ats.BLOCK_PAT, and its size gate)
            if len(text) < 2048 and discover_ats.BLOCK_PAT.search(text):
                out.update(status="blocked", note="a bot-check page, served as HTTP 200")
            else:
                out.update(status="ok", html=text if body else "")
    except _Pointed as p:
        # "Straight" means: since it left the company's site, the walk has
        # been on this one host only. Own site -> a link shortener -> a board
        # is somebody else's redirect, not the company's pointer.
        off = [u for u in asked if not own_site(u, website)]
        own = [u for u in asked if own_site(u, website)]
        out.update(status="pointer", final=p.to,
                   note="redirects to a job platform's address; the platform was not asked",
                   pointer={"to": p.to, "from": own[-1] if own else p.frm,
                            "straight": bool(own) and all(_host(u) == _host(p.to) for u in off)})
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
    page = discover_ats._strip_blocks(_cut(page, "<!--", "-->"), "script")
    low = page.lower()
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


async def read_plain(url: str, website: str, deadline: float | None = None) -> dict:
    """One address with plain requests: what `land` found, plus the page's
    links and its visible words."""
    pr = await asyncio.to_thread(land, url, website, body=True, deadline=deadline)
    if pr["status"] == "ok":
        pr["links"] = _anchors(pr["html"], pr["final"])
        pr["words"] = discover_ats.visible(pr["html"])
    return pr


# Pictures, video and fonts name no board, and not loading them is the
# cheapest courtesy a browser can pay a site it is only reading.
_SKIP_RESOURCES = {"image", "media", "font"}


async def read_rendered(ctx, url: str, website: str, deadline: float | None = None,
                        landed: dict | None = None) -> dict:
    """One address in the browser.

    ROBOTS.TXT AT EVERY STEP A BROWSER TAKES ON ITS OWN, the three layers
    render_fetch keeps: the address's redirects are walked first, every hop
    asked (`land`); any page navigation after that - a script's redirect, a
    frame - is asked before it is made and aborted when refused; and the page
    finally read must itself be one we may read, or nothing from it is kept.
    """
    pr = landed or await asyncio.to_thread(land, url, website, body=False, deadline=deadline)
    if pr["status"] != "ok":
        return pr
    seen: list[str] = []
    page = await ctx.new_page()
    page.on("request", lambda r: seen.append(r.url))

    async def only_allowed(route):
        req = route.request
        if req.resource_type in _SKIP_RESOURCES:
            await route.abort("blockedbyclient")
        elif req.resource_type == "document" and not await asyncio.to_thread(
                robots.allowed, req.url):
            await route.abort("blockedbyclient")
        else:
            await route.continue_()
    await page.route("**/*", only_allowed)
    try:
        await page.goto(pr["final"], wait_until="domcontentloaded", timeout=25000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:                    # noqa: BLE001 - some pages never go idle
            pass
        await page.wait_for_timeout(2000)
        if not await asyncio.to_thread(robots.allowed, page.url):
            return {**pr, "status": "robots", "final": page.url, "html": "",
                    "note": "a script sent the browser to a page robots.txt closes"}
        pr["opened"], pr["final"] = pr["final"], page.url
        pr["html"] = (await page.content())[:2 * PAGE_CAP]
        try:
            pr["words"] = await page.inner_text("body")
        except Exception:                    # noqa: BLE001
            pr["words"] = ""
        links = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.href || '', (e.innerText || '').trim()])")
        # a JS-built page can put an object where an href belongs (discover_js)
        pr["links"] = [(h, re.sub(r"\s+", " ", t or "")) for h, t in links
                       if isinstance(h, str) and h.startswith("http")]
        pr["frames"] = [f.url for f in page.frames if f != page.main_frame]
        pr["requests"] = list(seen)
    except Exception as exc:                 # noqa: BLE001
        return {**pr, "status": "error", "html": "",
                "note": "the browser could not open it: " + " ".join(str(exc).split())[:100]}
    finally:
        await page.close()
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
    that already names a board is the finding itself and is not followed.
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
        if own_site(url, website):
            pass
        elif _READABLE_HOST.search(_host(url)) and not refs_in_address(url)[0]:
            rank += 3                   # after every page of their own
        else:
            if not any(refs_in_address(url)) and not _NOT_A_PLATFORM.search(_host(url)):
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
    low = page[:400_000].lower()
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


def absorb(rec: dict, pr: dict, hop: int, website: str, guessed: bool = False,
           via: str | None = None) -> bool:
    """File one page's reading into the company's record. True when it was a
    page on their own site that could be read.

    THE WHOLE RULE IS IN WHICH LIST A REFERENCE LANDS IN. `candidates` only
    from a page whose landing address is on the company's own host, or from
    an own-site address that redirected straight to the board. Everything
    else that names a board goes to `off_site`. `via` is the own-site page a
    followed link was found on, so "left for" can say from where.
    """
    final = pr["final"]
    same = own_site(final, website)
    page = {"asked": pr["asked"], "final": final, "same_site": same,
            "status": pr["status"], "note": privacy.redact(pr.get("note") or "")}
    readable = False
    left_from = pr["asked"] if own_site(pr["asked"], website) else (via or pr["asked"])
    # A SCRIPT THAT SENDS THE BROWSER STRAIGHT TO A BOARD is the same pointer
    # a server's redirect is: the company's own address was opened, and where
    # it took the reader names the board. The board's own page is not read
    # for more.
    opened = pr.get("opened")
    if (pr["status"] == "ok" and not same and opened and own_site(opened, website)
            and refs_in_address(final)[0]):
        for _s, _e, kind, ref, _k in refs_in_address(final)[0]:
            rec["candidates"].append({"type": kind, "ref": ref, "element": "redirect",
                                      "saw": privacy.redact(final[:160]), "times": 1,
                                      "page": opened, "hop": hop})
        page.update(status="left-site",
                    note="a script on the page sent the browser to a job board's address")
        _leave(rec, left_from, final)
        rec["pages"].append(page)
        return False
    if pr["status"] == "pointer":
        ptr = pr["pointer"]
        boards, others = refs_in_address(ptr["to"])
        mine = ptr["straight"] and own_site(ptr["from"], website)
        for _s, _e, kind, ref, _k in boards:
            (rec["candidates"] if mine else rec["off_site"]).append({
                "type": kind, "ref": ref, "element": "redirect",
                "saw": privacy.redact(ptr["to"][:160]), "times": 1,
                "page": ptr["from"], "hop": hop})
        for vendor in dict.fromkeys(v for _s, _e, v in others):
            rec["elsewhere"].append({"vendor": vendor, "saw": privacy.redact(ptr["to"][:160]),
                                     "page": ptr["from"], "element": "redirect", "times": 1})
        page["status"] = "left-site"
        _leave(rec, left_from, ptr["to"])
    elif pr["status"] == "ok":
        boards, others = sightings(pr["html"], final, website,
                                   requests_seen=pr.get("requests") or (),
                                   frames_seen=pr.get("frames") or ())
        for row in boards:
            (rec["candidates"] if same else rec["off_site"]).append({**row, "page": final, "hop": hop})
        if same:
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
        else:
            page["status"] = "left-site"
            page["note"] = (f"landed on another site; {len(boards)} board reference(s) "
                            f"there, filed under off_site")
            _leave(rec, left_from, final)
    elif not same:
        # refused or broken, and somewhere else: both facts are kept
        _leave(rec, left_from, final)
    if not same and pr["status"] != "pointer":
        # where it landed may itself be a platform's address (a Gusto posting,
        # a Workday tenant with no site): that much is known without reading it
        for vendor in dict.fromkeys(v for _s, _e, v in refs_in_address(final)[1]):
            rec["elsewhere"].append({"vendor": vendor, "saw": privacy.redact(final[:160]),
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
    va, vb = discover_ats.visible(a), discover_ats.visible(b)
    if va == vb:
        return True
    ta, tb = _inside(a, "title"), _inside(b, "title")
    if not ta or ta != tb:
        return False
    return abs(len(va) - len(vb)) <= 0.03 * max(len(va), len(vb), 1)


async def look(company: dict, mode: str, read, plain=read_plain,
               rec: dict | None = None) -> dict:
    """Walk one company's own site for the board it points to.

    In order: the website; the careers address on file when it is on their
    own site; then the careers-looking links those pages offer. `read(url,
    website, deadline)` reads one address (a browser in render mode);
    `plain` is always the plain reader, used to test a guessed path before
    anything is made of it. `rec` is filled in place, so a caller that has to
    stop the walk still holds what it had found.
    """
    rec = _blank(company) if rec is None else rec
    site = (company.get("website") or "").strip()
    if site and "//" not in site:
        site = "https://" + site
    if not site.startswith("http") or not _host(site):
        rec["error"] = "the website on file is not an address"
        return rec
    deadline = time.monotonic() + COMPANY_SECONDS[mode]
    queue: list[dict] = []
    done: set[str] = set()
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
    on_file = isinstance(ref, str) and ref.startswith("http") and own_site(ref, site)
    if on_file:
        push(ref, 0, -1)
    pages = 0
    home = None                 # the website's own page, once read
    careers_read = False
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
            if guessed or home is None or careers_read:
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
                            "same_site": own_site(got["final"], site), "status": "error",
                            "note": "a guessed path, and not a real page: the site answers "
                                    "it with its home page or its not-found page"})
                        continue
                    if mode == "render" and own_site(got["final"], site):
                        # (the plain read above asked robots.txt at every hop)
                        got = await read(root + g, site, deadline, landed={**got, "html": ""})
                        got["note"] = ((got.get("note") or "") + " (a guessed path)").strip()
                done.update((_key(root + g), _key(got["final"])))
                if absorb(rec, got, 1, site, guessed=True):
                    careers_read = True
                    offer(got, 1)
                    break               # one real careers page is the answer
                if got["status"] == "blocked":
                    queue.clear()
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
        done.add(_key(item["url"]))
        pages += 1
        pr = await read(item["url"], site, deadline)
        if pr["status"] == "ok" and _key(pr["final"]) in done and _key(pr["final"]) != _key(item["url"]):
            # it landed on a page already read: say so, count nothing twice
            rec["pages"].append({"asked": pr["asked"], "final": pr["final"],
                                 "same_site": own_site(pr["final"], site), "status": "read",
                                 "note": "the same page as an address already read"})
            continue
        done.add(_key(pr["final"]))
        readable = absorb(rec, pr, item["hop"], site, via=item["via"])
        if pr["status"] == "blocked":
            # A REFUSAL IS NOT ASKED TWICE. The site said no to this crawler;
            # its other pages are not tried to see whether they agree.
            break
        if not readable:
            continue
        if item["hop"] == 0 and home is None and _key(item["url"]) == _key(site):
            home = pr
        if item["hop"] > 0 or (on_file and _key(item["url"]) == _key(ref)):
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
                                 "saw": privacy.redact(url[:160]), "page": on_page,
                                 "element": "href", "times": 1})
    return rec


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


def in_shard(company_id: str, k: int, n: int) -> bool:
    """A split that is the same on every machine and every run (Python's own
    hash() is salted per process, so it is not)."""
    return int(hashlib.sha1(company_id.encode("utf-8")).hexdigest(), 16) % n == k - 1


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

def _save(path: pathlib.Path, mode: str, companies: dict) -> None:
    """The whole file, written beside itself and moved into place, so a run
    that is stopped mid-write leaves the last good file and not half of one."""
    doc = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "mode": mode, "companies": companies}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


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
    by_kind = collections.Counter()
    for r in pointed:
        for kind in {c["type"] for c in r["candidates"]}:
            by_kind[kind] += 1
    by_vendor = collections.Counter()
    for r in companies.values():
        for v in {e["vendor"].split(":")[0] for e in r["elsewhere"]}:
            by_vendor[v] += 1
    out = [f"{n} companies in the file",
           f"  {len(pointed)} whose own site points to a board we can read",
           f"  {len(nothing)} read, no readable board named",
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
    return out


async def sweep(todo: list[dict], a, out: pathlib.Path, results: dict) -> int:
    """Look at each company in turn and keep the file current. Returns how
    many were left for another run (the --budget ran out)."""
    mode = "render" if a.render else "static"
    started = time.monotonic()
    fresh = [0]
    left = [0]

    def over() -> bool:
        return bool(a.budget) and time.monotonic() - started > a.budget

    def keep(c: dict, rec: dict) -> None:
        results[c["id"]] = rec
        fresh[0] += 1
        if a.show:
            print(_line(c["id"], rec), flush=True)
        if fresh[0] % SAVE_EVERY == 0:
            _save(out, mode, results)
            if not a.show:
                print(f"  ... {fresh[0]}/{len(todo)}", flush=True)

    if not a.render:
        for c in todo:
            if over():
                left[0] += 1
                continue
            try:
                rec = await look(c, "static", read_plain)
            except Exception as exc:         # noqa: BLE001 - one site must not end the run
                rec = {**_blank(c), "error": f"the look crashed: {type(exc).__name__}"}
            keep(c, rec)
            await asyncio.sleep(a.pause)
        return left[0]

    # ONE BROWSER, TWO PAGES AT A TIME. Playwright is imported here and
    # nowhere else, so a plain run and the self-test never need the browser
    # (CLAUDE.md: it stays out of the run path).
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit("--render needs Playwright: pip install playwright && "
                         "python -m playwright install chromium") from exc
    gate = asyncio.Semaphore(2)
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        async def one(c: dict) -> None:
            async with gate:
                if over():
                    left[0] += 1
                    return
                rec = _blank(c)
                ctx = await browser.new_context(user_agent=render_fetch.UA,
                                                viewport={"width": 1280, "height": 1000})

                async def read(url, website, deadline=None, landed=None):
                    return await read_rendered(ctx, url, website, deadline, landed)
                try:
                    await asyncio.wait_for(look(c, "render", read, rec=rec),
                                           timeout=COMPANY_SECONDS["render"] + 60)
                except asyncio.TimeoutError:
                    rec["error"] = "the browser did not finish in time"
                except Exception as exc:     # noqa: BLE001
                    # OUR BROWSER DYING IS NOT A FACT ABOUT THEIR SITE. Written
                    # down as this company's result it would be skipped as
                    # "already looked at" by the run that resumes; the run
                    # stops instead and the company is asked again.
                    if not browser.is_connected():
                        raise
                    rec["error"] = f"the look crashed: {type(exc).__name__}"
                finally:
                    try:
                        await ctx.close()
                    except Exception:        # noqa: BLE001 - it went with the browser
                        pass
                keep(c, rec)
                await asyncio.sleep(a.pause)
        try:
            await asyncio.gather(*[one(c) for c in todo])
        finally:
            await browser.close()
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
                    help="K/N: the K-th of N equal parts of the list, the same split "
                         "on every machine")
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
    if out.exists():
        # RESUMABLE: whoever is in the file already is not asked again. A
        # file from the other mode is refused rather than mixed - a company
        # read without a browser has not been looked at by one.
        prior = _read_json(out, None)
        if not isinstance(prior, dict) or not isinstance(prior.get("companies"), dict):
            print(f"{out} exists and is not a results file of this script; "
                  f"move it or name another --out", file=sys.stderr)
            return 2
        if prior.get("mode") != mode:
            print(f"{out} was written in {prior.get('mode')} mode and this run is "
                  f"{mode}: give each mode its own --out", file=sys.stderr)
            return 2
        results = prior["companies"]

    companies = json.loads((DATA / "companies.json").read_text())
    only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
    todo, skipped = worklist(companies, only=only, include_wired=a.include_wired)
    if shard:
        todo = [c for c in todo if in_shard(c["id"], *shard)]
    already = sum(1 for c in todo if c["id"] in results)
    todo = [c for c in todo if c["id"] not in results]
    if a.limit:
        todo = todo[:a.limit]
    print(f"find_site_boards ({mode}): {len(todo)} to look at"
          + (f", {already} already in the file" if already else "")
          + (f", part {shard[0]} of {shard[1]}" if shard else ""))
    for why, n in sorted(skipped.items()):
        print(f"  {n} not looked at: {why}")
    out.parent.mkdir(parents=True, exist_ok=True)
    left = 0
    try:
        left = asyncio.run(sweep(todo, a, out, results))
    finally:
        _save(out, mode, results)
    for line in summary(results):
        print(line)
    if left:
        print(f"  {left} left for another run (the --budget ran out): run it again "
              f"with the same --out")
    print(f"written to {out}")
    print("NOTHING WAS WIRED AND NOTHING IN data/ CHANGED. Each candidate is what a "
          "page says, not whose board it is: the board has not been asked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
