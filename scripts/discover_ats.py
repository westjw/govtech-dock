#!/usr/bin/env python3
"""Find the job board for companies that have none on file, in bulk.

4,121 of 4,499 companies have no ATS recorded, which means the map knows they
exist and cannot tell you whether they are hiring. This probes each one's site
for an applicant-tracking system, verifies the find with a real fetch, and
writes back only what actually works.

Three properties it needs to survive the scale:

RESUMABLE. Every attempt is logged with its date, so a rerun skips what was
tried recently instead of starting over. At this size the work happens across
many sessions, and forgetting what was already probed would make it endless.

VERIFIED. A discovered slug is fetched before it is written. An unverified guess
produces a company that looks monitored and is not, which is worse than one
still marked unknown, because nothing prompts anyone to look again.

POLITE. Concurrency is bounded and each host sees a handful of requests. The
point is to find a public job board, not to hammer anyone's marketing site.

FIVE PLATFORMS ARE THE WIRING DOOR'S, NOT THIS FILE'S (2026-11-22). Jobvite,
Teamtailor, ApplicantPro (isolved), Pinpoint and ADP were filed here as type
"html" - the vendor's careers address, read as a page - long after ats.py had
a reader for each. They are now named off a page the way find_site_boards.py
names them (its sightings(), its patterns, its refs: FINDER_KINDS), and a
board found that way is not written on a slug that looks like the name.
Once the sweep is over the finder reads the company's site its own way, and
its record goes to scripts/wire_found.py's own judgement - the owner's rule
of 2026-11-18, two checks that must agree: the company's own site points to
the board, and the board itself names the company. WIRE is written; HOLD is
filed in his "Boards we found" queue; SKIP says why in the log. See
read_their_sites() and through_the_door().

  python scripts/discover_ats.py [--limit 300] [--workers 8] [--write]
  python scripts/discover_ats.py --stats
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import html as html_lib
import threading
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "discovery_log.json"
SUSPECTS = DATA / "ats_suspects.json"

# Retry a failed probe after this long. Companies add job boards; a permanent
# "nothing found" would freeze a company out of monitoring forever.
RETRY_DAYS = 45

CAREER_PATHS = ["/careers", "/careers/", "/jobs", "/company/careers",
                "/about/careers", "/join-us", "/careers/open-positions"]

# Ordered: a structured API beats a generic careers page.
#
# Every pattern is anchored to a full hostname, never a bare product word. A
# company site published a post about *greenhouse gas* and a substring test fired
# on it. Markers are matched against page bodies only - never response headers,
# because WordPress.com stamps "x-hacker: ... visit join.a8c.com" on every
# response it serves, which makes a bare join.com test hit a company with no
# board at all.
# A HOST LABEL IS PINNED AT BOTH ENDS. Every pattern that opens on a bare
# character class before a literal (`([a-z0-9-]+)\.recruitee\.com`) tried
# every position in a run of letters and backtracked the whole run each time:
# quadratic in the run's length. A careers page carrying a base64 font or
# image is one such run, a megabyte long. On 2026-10-19 one page held a search
# for over half an hour, and because a regex search never lets go of the GIL,
# all six workers stopped with it. `L` starts a label only where no label
# character precedes it and caps it at DNS's 63, which finds exactly what the
# old patterns found on any real address and is linear on everything else.
L = r"(?<![a-z0-9-])[a-z0-9-]{1,63}"
MARKERS: list[tuple[str, str]] = [
    ("ashby", r"jobs\.ashbyhq\.com/(?:embed\?org=)?([a-zA-Z0-9._-]+)"),
    ("greenhouse", r"(?:job-boards|boards)\.greenhouse\.io/"
                   r"(?:embed/job_board(?:/js)?\?for=)?([a-zA-Z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([a-zA-Z0-9-]+)"),
    ("recruitee", rf"({L})\.recruitee\.com"),
    ("breezy", rf"({L})\.breezy\.hr"),
    ("bamboohr", rf"({L})\.bamboohr\.com"),
    ("jazzhr", rf"({L})\.applytojob\.com"),
    ("rippling", r"ats\.rippling\.com/([a-zA-Z0-9-]+)"),
    ("icims", rf"({L})\.icims\.com"),
    # Families a field audit found on real govtech careers pages, in plain HTML,
    # that the script simply had no pattern for. This was the single highest
    # recovery-per-line change available.
    ("workday", rf"({L})\.(wd\d+)\.myworkdayjobs\.com/(?:en-US/)?([A-Za-z0-9_-]+)"),
    ("workday", r"(wd\d+)\.myworkdaysite\.com/(?:en-US/)?recruiting/"
                r"([a-z0-9-]+)/([A-Za-z0-9_-]+)"),
    ("paylocity", r"(recruiting\.paylocity\.com/recruiting/jobs/All/[0-9a-f-]{36}/[^\s\"'<>]*)"),
    ("oracle", r"((?<![a-z0-9])[a-z0-9]{1,63}\.fa\.[a-z0-9]{1,63}\.oraclecloud\.com)"),
    # (Jobvite, Teamtailor, ApplicantPro and isolved, Pinpoint and ADP stood
    # here as "html" - see FINDER_KINDS, which read_marker asks between the
    # entries above and the careers pages below)
    ("html", rf"({L}\.paycomonline\.net/v4/ats/web\.php/jobs\?clientkey=[0-9A-F]{{32}})"),
    ("html", rf"({L}\.clearcompany\.com|{L}\.dayforcehcm\.com|"
             r"recruiting\d*\.ultipro\.com/[A-Z0-9_]+|"
             rf"{L}\.taleo\.net|{L}\.avature\.net|{L}\.csod\.com)"),
    # HiBob is deliberately last and deliberately never slug-probed: every
    # subdomain returns a byte-identical 1342-byte shell, so a probe proves
    # nothing. It only counts when the company's own site links to it.
    ("html", rf"({L}\.careers\.hibob\.com)"),
]

# Marker types whose captured group is a full URL rather than a slug. These skip
# slug_matches - the host is already pinned to a specific tenant id, and the URL
# came off the company's own page.
URL_MARKERS = {"paylocity", "oracle"}
RESERVED = {"www", "jobs", "careers", "api", "embed", "app", "static", "cdn", "js"}
# The ATS vendor's own name is never the tenant. On jobs.jobvite.com/acmesoft the
# readable label is "jobvite", and checking THAT against the company would pass
# nothing and fail everything.
VENDOR_HOSTS = {
    "jobvite", "greenhouse", "lever", "ashbyhq", "workable", "recruitee",
    "breezy", "bamboohr", "applytojob", "rippling", "icims", "paylocity",
    "oraclecloud", "adp", "paycomonline", "teamtailor", "hibob",
    "myworkdayjobs", "myworkdaysite", "smartrecruiters", "applicantpro",
    "clearcompany", "isolvedhire", "dayforcehcm", "ultipro", "pinpointhq",
    "taleo", "avature", "csod", "workforcenow",
}

# PLATFORMS ATS.PY READS AND THIS FILE NEVER LEARNT TO NAME. Each was a
# careers page here ("html": the vendor's address, read as a page - ADP's and
# isolved's are JavaScript shells, so "found but unreadable" every week),
# while ats.FETCHERS read each one as a board and find_site_boards.py named
# each one's ref. NOT ANOTHER TABLE: they are read off a page by the finder's
# own sightings(), patterns and refs, so a board found here is spelt exactly
# as the wiring door, the owner's holds and the queue spell it (a hold under
# teamtailor/<host> never met this file's html/<the same address>). And a
# board found here is the wiring door's to judge, on the finder's own record
# of the site, not slug_matches' (read_their_sites, through_the_door): five
# platforms, and the first boards this file hands over rather than writes.
FINDER_KINDS = ("adp", "applicantpro", "teamtailor", "pinpoint", "jobvite")
# who files what discovery holds for the owner (board_proposals.file_found
# takes an agent's handle, never a person's)
FILED_BY = "agent:discovery"
# Companies one run hands the door. The door asks each board two or three
# questions, one at a time, after the sweep; and what it holds is filed in one
# journal entry, which over journal.BLAST (25) records must be confirmed by a
# person - nobody is watching this run. The rest are asked again in
# RETRY_SOON_DAYS.
DOOR_AT_ONCE = 25
# Seconds the finder may spend reading the sites of the companies handed to
# the door, after the sweep: each is one company's own site walked the
# finder's way (at most four pages, and find_site_boards.COMPANY_SECONDS).
# The eight weekly runs to 2026-11-22 used 7 to 16 of the job's 90 minutes.
# A company the time does not reach is asked again in RETRY_SOON_DAYS.
DOOR_SECONDS = 20 * 60
# Between two companies' sites, the finder's own courtesy (its --pause).
FINDER_PAUSE = 0.3


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def slug_matches(slug: str, company: dict) -> bool:
    """Does a discovered slug plausibly belong to this company?

    A marker on a page is not proof it is theirs: an embedded widget, an agency
    script or a partner logo can leave one behind. "Loop" resolved to
    bamboohr:boxclever, which would have monitored somebody else's board under
    Loop's name. A slug has to share ground with the company name or domain, or
    it gets flagged for review rather than written.
    """
    sl = _norm(slug)
    if not sl:
        return False
    raw = company.get("name", "")
    name = _norm(raw)
    host = _norm((company.get("website") or "").split("//")[-1].split("/")[0]
                 .replace("www.", "").split(".")[0])
    # A parenthetical names the acquirer. That is a real signal and a dangerous
    # one: "Simpleview (Granicus)" really does post on Granicus's board, but
    # "RoadBotics (Michelin)" posting on Michelin's Workday would file a tyre
    # company's reqs under a road-scanning startup. Both look identical here, so
    # a parenthetical match is routed to review rather than written - see
    # paren_only() and the ats_suspects queue.
    paren = [_norm(x) for x in re.findall(r"\(([^)]{2,40})\)", raw)]
    first = _norm(re.split(r"[^A-Za-z0-9]+", raw)[0]) if raw else ""

    for other in [name, host]:
        if not other:
            continue
        if sl in other or other in sl:
            return True
        if len(sl) >= 5 and len(other) >= 5 and sl[:5] == other[:5]:
            return True
    # A slug built from the company's first word plus a suffix is theirs:
    # "MCM Technology LLC" posts at rippling:mcmjobs.
    if len(first) >= 3 and sl.startswith(first):
        return True
    return False


def paren_only(slug: str, company: dict) -> bool:
    """True when a slug matches ONLY through the acquirer in parentheses.

    Those are real finds and they are also the single most dangerous write this
    script can make, so they go to a person instead of into companies.json.
    """
    if slug_matches(slug, company):
        return False
    sl = _norm(slug)
    for other in [_norm(x) for x in
                  re.findall(r"\(([^)]{2,40})\)", company.get("name", ""))]:
        if other and (sl in other or other in sl
                      or (len(sl) >= 5 and len(other) >= 5 and sl[:5] == other[:5])):
            return True
    return False

_lock = threading.Lock()


def load_log() -> dict:
    return json.loads(LOG.read_text()) if LOG.exists() else {}


# A refusal is not an answer. A blocked or errored probe learned nothing, so it
# comes back around in days rather than sitting out the full window as though it
# had proved something.
RETRY_SOON_DAYS = 7


# The buyer-motion order: the closest markets first. It is the TIEBREAK, not
# the sort. discovery.yml promised "--limit takes the oldest first, so the
# backlog drains in order" while this sorted by sector alone and clipped to
# 300 - so every sector outside this map (Higher Education, Courts, HHS,
# Housing) sat last on every Sunday, and 1,426 companies probed on three
# August days all fall due in the same October week, five runs deep.
# Oldest first drains that; a never-probed company sorts before any of them.
SECTOR_ORDER = {"General Gov": 0, "Public Works": 1, "Parks & Rec": 2,
                "Public Safety": 3, "Transit & Parking": 4, "K-12 Schools": 5,
                "Utilities & Energy": 6, "Airports & Aviation": 7}


def probe_order(c: dict, log: dict) -> tuple:
    """Sort key: last probe date ascending (never probed = first), then sector."""
    on = (log.get(c.get("id")) or {}).get("on") or ""
    return (on, SECTOR_ORDER.get(c.get("sector"), 9))


def stale(entry: dict | None) -> bool:
    if not entry:
        return True
    try:
        age = (dt.date.today() - dt.date.fromisoformat(entry["on"])).days
    except (KeyError, ValueError):
        return True
    return age >= (RETRY_SOON_DAYS if entry.get("retry_soon") else RETRY_DAYS)


# Discovery probes do not need the fetcher's patience. At 20 seconds a request
# and up to eight paths per company, one dead domain costs 160 seconds of worker
# time, which is what turned a 1,245-company sweep into a multi-hour run.
PROBE_TIMEOUT = 8


# A bot wall answers, so a naive fetcher reads it as a page and then reads the
# absence of an ATS marker as an absence of a board. That is the page-scan
# mistake wearing a different hat, and a field audit found ~70 of 633 "no board
# found" records were really this. Detecting it changes nothing about coverage
# and everything about honesty.
BLOCK_PAT = re.compile(
    r"sgcaptcha|/\.well-known/(sg)?captcha|__cf_chl|cf-browser-verification|"
    r"Checking your browser|Just a moment|_Incapsula_|Pardon Our Interruption|"
    r"reese84|PerimeterX|Attention Required", re.I)


class Fetch:
    """The outcome of one request, not just its body.

    `ok` means we read the page. `blocked` and `error` mean we did not, and must
    never be recorded as "no board" - they requeue on a short cadence instead of
    the 45-day one.
    """

    __slots__ = ("text", "status", "url", "outcome")

    def __init__(self, text="", status=0, url="", outcome="error"):
        self.text, self.status, self.url, self.outcome = text, status, url, outcome

    def __bool__(self):
        return self.outcome == "ok" and bool(self.text)


# Everything past this size is not a careers page. An unbounded read means one
# link to a PDF or a video holds a worker for the whole file, and with 12
# workers sharing one GIL, one pathological page can starve the entire sweep.
BODY_CAP = 600_000


def get(url: str) -> Fetch:
    import requests
    # PACED, like every other fetch here. This function keeps its own body
    # cap and split timeouts - both were earned and neither belongs in
    # ats._get - so it borrows the gate rather than the fetcher.
    #
    # It matters more here than anywhere else: this is the ONLY script that
    # has ever earned a 403. All 99 blocks in discovery_log came from sweeps
    # run by this file, eight workers wide against company front doors, with
    # nothing spacing them. The board crawl, which is paced, took zero.
    def one(u):
        ats._host_gate(u)
        # (connect, read) rather than one number. A bare timeout still let an
        # ssl.read sit for over a minute, which stalls a worker and - with an
        # in-order result iterator - stalled the entire sweep's reporting.
        return requests.get(u, headers=ats.UA, timeout=(5, PROBE_TIMEOUT),
                            allow_redirects=False, stream=True)
    try:
        # robots.txt first, at every redirect hop (owner's ruling, 2026-10-09).
        # A refusal is its own outcome: it says nothing about the company's
        # OTHER pages, and is no reason to stop looking at them.
        r = ats.follow(url, one)
        raw = b""
        for chunk in r.iter_content(chunk_size=65536):
            raw += chunk
            if len(raw) >= BODY_CAP:
                break
        r.close()
    except ats.RobotsRefused as exc:
        return Fetch(url=url, outcome="robots", status=0, text=str(exc))
    except Exception as exc:
        return Fetch(url=url, outcome="error", status=0,
                     text=type(exc.__cause__ or exc).__name__)
    # THE SAME CHARSET RULE THE REST OF THE FETCHERS USE. This decoded with
    # r.encoding, which requests sets to Latin-1 for any text/* body that
    # carries no charset - so a UTF-8 page arrived here with its punctuation
    # in three glued characters and every marker match ran against that.
    enc = ats.best_charset(raw, (r.headers or {}).get("content-type", ""), r.encoding)
    try:
        body = raw.decode(enc, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    # 202 is SiteGround's tell; the rest are the usual refusals.
    if r.status_code in (202, 403, 429, 503):
        return Fetch(status=r.status_code, url=str(r.url), outcome="blocked")
    # The size gate is load-bearing: a real 135KB page can carry "Just a moment"
    # inside a Turnstile widget on its contact form. Only a small body that is
    # *mostly* the challenge counts.
    if len(body) < 2048 and BLOCK_PAT.search(body):
        return Fetch(status=r.status_code, url=str(r.url), outcome="blocked")
    if r.status_code == 404:
        return Fetch(status=404, url=str(r.url), outcome="notfound")
    # A 5xx that still ships a full page is worth parsing - one company serves
    # 445KB of real site under a 500.
    if r.status_code >= 400 and len(body) < 5000:
        return Fetch(status=r.status_code, url=str(r.url), outcome="error")
    return Fetch(text=body, status=r.status_code, url=str(r.url), outcome="ok")


def _strip_blocks(html: str, tag: str) -> str:
    """Remove <tag>...</tag> blocks with str.find, not a regex.

    The regex version ((?is)<(script|...)\b.*?</\1>) rescans to end-of-page for
    every unclosed opener, which on a page whose JS bundle contains literal
    "<script" strings is quadratic - and a thread stuck in a regex holds the
    GIL, so one such page froze all twelve workers at once.
    """
    low, out, i = html.lower(), [], 0
    opener, closer = "<" + tag, "</" + tag
    while True:
        j = low.find(opener, i)
        if j < 0:
            out.append(html[i:])
            return "".join(out)
        out.append(html[i:j])
        k = low.find(closer, j)
        if k < 0:
            return "".join(out)          # unclosed: drop the rest, it is script
        end = low.find(">", k)
        i = (end + 1) if end > 0 else len(html)


def visible(html: str) -> str:
    """Text with markup, script and style removed - what a reader would see."""
    t = html or ""
    for tag in ("script", "style", "noscript"):
        t = _strip_blocks(t, tag)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()


def fingerprint(html: str) -> str:
    return hashlib.md5(visible(html).encode("utf-8", "replace")).hexdigest()


# A hard ceiling per company. requests' timeout governs each socket operation,
# not the whole request, so a server trickling bytes can hold a worker open
# indefinitely: a 1,245-company sweep ran 10.5 hours, past the point where every
# fetch timing out would have finished, and wrote nothing. No company is worth
# more than this.
COMPANY_BUDGET = 45   # default; --budget raises it for a retry sweep


# Follow the site's own careers link instead of only guessing paths. 718 probes
# found nothing, and a guessed path list cannot cover /company/join,
# /life-at-x, /work-with-us and the rest. The homepage already knows where its
# careers page is.
# Any href, quoted or not. The old pattern required both quotes and matched on
# anchor TEXT alone, which missed unquoted attributes, "./about-us/careers"
# relatives, and bare http:// links. Small individually; together they were
# silently dropping links the rest of the pipeline depended on.
HREF = re.compile(r"""href\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
CAREER_HREF = re.compile(r"(career|jobs?|join-?us|work-?with-?us|employment|"
                         r"vacanc|open-?positions?|hiring|opportunit)", re.I)
# A path that IS a careers page, rather than one that merely mentions the word.
CAREER_PATH = re.compile(r"/(careers?|jobs?|join-?us|work-?with-?us|employment|"
                         r"vacanc\w*|open-?positions?)(/|$|\.\w+$)", re.I)
# A page whose PATH does not say careers has to say it loudly in the title. A
# bare "job" matched a blog post called "Why jobs matter", which would then have
# been recorded as this company's job board.
CAREER_TITLE = re.compile(r"\b(careers?|join (our|the) team|work (with|for) us|"
                          r"open (positions?|roles?)|we(\'re| are) hiring|"
                          r"job openings?|vacancies|now hiring)\b", re.I)
# Article-shaped paths never qualify on a title alone, whatever it says.
ARTICLE_PATH = re.compile(r"/(blog|news|press|resources?|insights?|articles?|"
                          r"stories|posts?|events?|webinars?)(/|$)", re.I)
# One hop past the homepage. A field audit found three companies whose board was
# an ATS already in MARKERS, sitting behind /about - purely a reach problem.
HOP_PATHS = ["/about", "/about-us", "/company", "/team", "/our-team"]
# An honest empty board. "No current vacancies" is a real page saying a real
# thing, and recording it as a failed probe loses that.
NO_OPENINGS = re.compile(r"no (current |open |available )?(vacanc|opening|position|role)s?\b|"
                         r"check back|not currently hiring", re.I)


def career_links(html: str, resp_url: str) -> list[str]:
    """Careers-looking links, ranked.

    urljoin resolves against the RESPONSE url, not base + "/". The old form
    resolved relatives under the wrong directory and once inflated one company's
    job-link count from 0 to 42 against a page that had none.
    """
    scored, seen = [], set()
    for m in HREF.finditer(html or ""):
        href = html_lib.unescape(m.group(1).strip("\"'")).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        if href.startswith("#"):
            continue
        try:
            url = urllib.parse.urljoin(resp_url, href)
        except ValueError:
            continue
        if not url.startswith("http"):
            continue
        parts = urllib.parse.urlsplit(url)
        # A careers subdomain counts even when the path says nothing.
        subdomain = parts.netloc.split(".")[0].lower() in ("careers", "jobs")
        if not (CAREER_HREF.search(parts.path + "?" + parts.query) or subdomain):
            continue
        key = url.split("#")[0].rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        # A short path ending /careers beats a long blog slug that happens to
        # contain the word.
        rank = (0 if CAREER_PATH.search(parts.path) or subdomain else 1,
                len(parts.path))
        scored.append((rank, key))
    scored.sort()
    return [u for _, u in scored[:8]]


def identity_of(kind: str, groups: list, ref) -> str:
    """The tenant name inside a discovered board reference.

    Every board has one, whatever shape the ref takes, and it is the only thing
    that can be checked against the company. Skipping the check for
    awkwardly-shaped refs is how a Workday board belonging to Terex nearly got
    written as ZenRobotics' - 31 of the parent's postings under the
    subsidiary's name, which is the one thing this file exists to prevent.
    """
    if kind == "workday":
        # Two host shapes: acme.wd12.myworkdayjobs.com/Site -> (acme, wd12, Site)
        # and wd3.myworkdaysite.com/recruiting/acme/Site -> (wd3, acme, Site).
        # The tenant is whichever element is not the pod.
        return next((g for g in groups[:2] if not re.fullmatch(r"wd\d+", g or "")), "")
    text = ref if isinstance(ref, str) else " ".join(str(g) for g in groups)
    # Some vendors key a board by an opaque id with no company name anywhere in
    # it. There is nothing to check, so these must not be written on trust -
    # they return "" and the caller routes them to review.
    if re.search(r"workforcenow\.adp\.com|paycomonline\.net", text, re.I):
        return ""
    if kind == "paylocity":
        # .../jobs/All/<uuid>/<Company-Name>
        tail = text.rstrip("/").rsplit("/", 1)[-1]
        return "" if re.fullmatch(r"[0-9a-f-]{36}", tail) else tail
    host = text.split("//")[-1].split("/")[0]
    labels = host.split(".")
    label = labels[0]
    if label in RESERVED and len(labels) > 1:
        label = labels[1]
    if label in RESERVED or label in VENDOR_HOSTS:
        # No host to read - fall back to the first path segment, which is where
        # jobs.jobvite.com/<company> keeps it.
        segs = [x for x in text.split("//")[-1].split("/")[1:] if x]
        label = segs[0] if segs else ""
    return label


def _marker(kind: str, pat: str, html: str) -> tuple[dict, str] | None:
    """One MARKERS entry over the page: (block, tenant name), or None."""
    m = re.search(pat, html, re.I)
    if not m:
        return None
    groups = [g for g in m.groups() if g]
    if not groups:
        return None
    if kind == "workday":
        if len(groups) < 3:
            return None
        block = {"type": kind, "ref": list(groups[:3])}
        return block, identity_of(kind, groups, block["ref"])
    ref = groups[0]
    if kind in URL_MARKERS or "/" in ref or "." in ref:
        if not ref.startswith("http"):
            ref = "https://" + ref
        ident = identity_of(kind, groups, ref)
        if ident.lower() in RESERVED or ident.lower() in VENDOR_HOSTS:
            return None           # the vendor's own app host, not a tenant
        if not ident:
            # No tenant name anywhere. That is fine when the reference still
            # pins one employer - Paycom's ?clientkey= does - and meaningless
            # for a bare vendor host that identifies nobody and should not
            # reach the review queue.
            rest = ref.split("//", 1)[-1]
            if "?" not in rest and len(rest.split("/", 1)) < 2:
                return None
            if "?" not in rest and not rest.split("/", 1)[1].strip("/"):
                return None
        return {"type": kind, "ref": ref}, ident
    if ref.lower() in RESERVED:
        return None
    return {"type": kind, "ref": ref}, identity_of(kind, groups, ref)


def finder_board(html: str, page_url: str = "", website: str = "") -> dict | None:
    """The first board of FINDER_KINDS the page names, as find_site_boards.py
    reads it: its sightings() over the page (comments are not the page, the
    escapes a link wraps an address in are undone, an element is told from
    plain text), its patterns, and the ref each of its makers spells - the
    one ats.FETCHERS takes and the owner's holds are keyed by. None when the
    page names none, or only a vendor's furniture (app.teamtailor.com, a
    Jobvite __assets__ path). Asked only of a page that holds one of the
    finder's own hints for these platforms, so most pages cost one scan."""
    import find_site_boards as fsb      # here: it imports this module
    low = (html or "").lower()
    if not any(h in low for k in FINDER_KINDS for h in fsb.HINTS[k]):
        return None
    rows, _others = fsb.sightings(html or "", page_url or "", website or "")
    for row in rows:
        if row["type"] in FINDER_KINDS:
            return {"type": row["type"], "ref": row["ref"]}
    return None


def read_their_sites(todo: list[dict], on_file: dict, seconds: float,
                     pages: dict | None = None) -> tuple[dict, list[str]]:
    """Each company's own site read by find_site_boards.look - the finder's
    own walk, in its static mode with its plain reader: the website, its
    careers links, the careers pages after a board has turned up, a redirect
    stopped where it points, where every page landed, and every reference
    with the element it sat in. ({id: the finder's record}, [ids the time
    did not reach]).

    THE DOOR IS SHOWN WHAT THE FINDER SHOWS IT, AND NOTHING DISCOVERY MADE.
    A record rebuilt from the pages this probe happened to read (blind
    review, 2026-11-22) stopped at the first page naming a board, so it never
    saw the Paycom frame on their /careers or the careers link leaving for
    their parent's site, and the door wired what the finder's own record
    would have held. `on_file` is find_site_boards.site_index of every
    record; the finder's courtesy pause is kept between companies.

    THE PAGE THE PROBE FOUND THE BOARD ON is where the finder starts, beside
    the website, as it starts at a careers address on file - and, as with
    that address, only when the page is on their own site (look() asks):
    the finder's four pages and its one guess at /careers do not reach a
    careers page linked from /about, which the probe's one hop does (live,
    2026-11-22: Copenhagen Optimization's Teamtailor board). The finder
    still reads the page itself and says whose it is; the door asks again.
    `pages` is {company id: that page}.
    """
    import asyncio

    import find_site_boards as fsb
    started = time.monotonic()
    out: dict = {}
    later: list[str] = []

    async def walk() -> None:
        for c in todo:
            if time.monotonic() - started > seconds:
                later.append(c["id"])
                continue
            rec = fsb._blank(c)
            page = (pages or {}).get(c["id"])
            mine = (c.get("ats") or {}).get("ref")
            # (a careers address of their own already on file is the one
            # the finder starts at, as it always does)
            theirs = isinstance(mine, str) and mine.startswith("http") \
                and fsb.own_site(mine, str(c.get("website") or ""))
            walk = dict(c, ats={"type": "html", "ref": page}) \
                if isinstance(page, str) and page.startswith("http") and not theirs else c
            try:
                await asyncio.wait_for(
                    fsb.look(walk, "static", fsb.read_plain, plain=fsb.read_plain, rec=rec,
                             on_file=on_file),
                    timeout=fsb.COMPANY_SECONDS["static"] + 30)
            except asyncio.TimeoutError:
                rec["error"] = fsb.TIMED_OUT
            except Exception as exc:                    # noqa: BLE001 - one site, not the run
                rec = {**fsb._blank(c), "error": f"{fsb.CRASHED}: {type(exc).__name__}"}
            out[c["id"]] = fsb._no_addresses(rec)
            await asyncio.sleep(FINDER_PAUSE)
    asyncio.run(walk())
    return out, later


def through_the_door(records: dict, companies: list, by_id: dict, *, write: bool,
                     today: str, stopped_at: dict | None = None
                     ) -> tuple[dict, collections.Counter]:
    """The finder's records of the companies whose sites named a board on
    FINDER_KINDS, judged by the wiring door and by nothing else: ({company
    id: the probe's result as it now stands}, counts). `records` are
    read_their_sites' records of companies nobody holds; `stopped_at` is the
    board each probe stopped at, for the words.

    THE OWNER'S RULE OF 2026-11-18, UNCHANGED BECAUSE IT IS THE SAME CODE. A
    found board is wired only when two separate checks agree - the company's
    own site points to the board, and the board itself names the company -
    and nothing on file argues with it; a parent's board, a name mismatch and
    anything doubtful go to his "Boards we found" queue. That judgement is
    scripts/wire_found.py's, on a finder results file, which is what it
    reads (load_results): plan() reads each board, asks it whose it is and
    weighs what the site showed; decide() asks again, at the write, what has
    moved. This writes what they decide, and only of FINDER_KINDS:

      WIRE  the record takes the board as the door's apply writes it
            (wire_found.wire_record: the board, and both checks on ats_note)
      HOLD  filed in Boards we found by board_proposals.file_found, the
            queue's one append door, as FILED_BY - never forced
      SKIP  said in the log; a failure of the day (`unreadable`) is asked
            again in RETRY_SOON_DAYS

    A board of another platform the door settles on - their site's own
    Gusto board, beside a Jobvite one a parent's page named - is not this
    sweep's to wire or file (blind review, 2026-11-22: discovery had wired
    platforms it never wrote before): it is said, and left for the board
    sweep. A dry run wires and files nothing, and asks everything again
    soon: a look is not an answer. Nothing it decides names a board or a
    company in what it prints - the workflow's log is public.
    """
    import shutil
    import tempfile

    import admin
    import board_proposals
    import find_site_boards as fsb
    import wire_found as wf
    out: dict = {}
    n = collections.Counter()
    stopped_at = stopped_at or {}
    if not records:
        return out, n

    def about(cid: str) -> str:
        b = stopped_at.get(cid) if isinstance(stopped_at.get(cid), dict) else {}
        return board_proposals.show(b.get("type"), b.get("ref")) if b.get("type") else "a board"

    def unasked(why: str):
        for cid in records:
            out[cid] = {"id": cid, "found": None, "retry_soon": True,
                        "note": f"{about(cid)} was found and the wiring door could not be "
                                f"asked ({why[:120]}); asked again in {RETRY_SOON_DAYS} days"}
        n["unasked"] += len(records)
        return out, n

    # THE DOOR JUDGES THE DIRECTORY THIS RUN WRITES, OR NOTHING. wire_found
    # and board_proposals read admin.DATA; were it another directory, the
    # holds, the queue and the records would be somebody else's.
    if admin.DATA.resolve() != DATA.resolve() or board_proposals.FOUND.resolve() \
            != (DATA / board_proposals.FOUND.name).resolve():
        return unasked(f"the wiring door reads {admin.DATA}, and this run writes {DATA}")
    if not callable(getattr(wf.verify_boards, "proven", None)):
        return unasked("verify_boards.proven is not in this checkout, so check 2 cannot run")
    quiet = lambda *_a, **_k: None          # noqa: E731 - the door's lines name names
    try:
        # the results file, as the finder writes it and the door reads it,
        # outside data/ and gone when the door has read it
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="discovery-door-"))
        try:
            fsb._save(scratch / "site_boards.json", "static", records)
            results, _files = wf.load_results([scratch / "site_boards.json"])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        rows = wf.plan(results, companies, wf.read_board(), say=quiet,
                       stores=wf.read_stores())
        # what the door settles on on another platform is not this sweep's
        rows = [r if r["action"] == "SKIP" or r.get("type") in FINDER_KINDS else
                {**r, "action": "SKIP", "kind": "elsewhere",
                 "why": (f"the wiring door settled on {wf.show(r.get('type'), r.get('ref'))}, "
                         f"a board on a platform this sweep does not hand over: the board "
                         f"sweep's")}
                for r in rows]
        with admin.desk_lock():
            # read again under the lock, as the door's apply reads them
            stores, board = wf.read_stores(), wf.read_board()
            d = wf.decide({"rows": rows}, companies, FILED_BY,
                          dt.datetime.now().astimezone(), stores=stores, board=board)
            rep = board_proposals.file_found(
                d["queued"], FILED_BY, write=write, force=False, companies=companies,
                why=(f"{len(d['queued'])} board(s) the weekly discovery sweep found on a "
                     f"platform it reads the finder's way, held for the owner: the wiring "
                     f"door's two checks did not agree, or something on file argues; each "
                     f"row says which"))
    except board_proposals.Unreadable as exc:
        return unasked(str(exc))
    except Exception as exc:                            # noqa: BLE001
        # AN UNATTENDED SWEEP IS NOT LOST TO THE DOOR. Whatever else this run
        # found is still written; these are asked again in a week.
        print(f"  (the wiring door failed: {type(exc).__name__})", file=sys.stderr)
        return unasked(f"the door failed: {type(exc).__name__}")
    if write:
        # the board on the record as the door's apply writes it, once nothing
        # is left to fail before the queue has answered
        for row in d["wire"]:
            wf.wire_record(by_id[row["id"]], row, today, noted=row["id"] in d["noted"])
    wired = {r["id"] for r in d["wire"]}
    left = {x["row"]["id"]: x for x in d["left"]}
    added = set(rep["added"])
    not_taken = {s.get("id"): s.get("why") for s in rep["skipped"] if isinstance(s, dict)}
    would = "" if write else "would be "        # a dry run says what a write would do
    for row in rows:
        cid = row["id"]
        b = (wf.show(row["type"], row["ref"]) if row.get("type") and row.get("ref") is not None
             else about(cid))
        res = {"id": cid, "found": None}
        if cid in wired:
            res.update(found={"type": row["type"], "ref": row["ref"]},
                       postings=row.get("postings"),
                       note=f"{b} {would}wired: {row.get('postings')} posting(s) readable; "
                            f"their own site points to it and it names them (the wiring "
                            f"door's two checks)")
            n["wired"] += 1
        elif cid in left:
            res.update(retry_soon=True,
                       note=f"{b} passed the wiring door's checks and was left alone at the "
                            f"write ({str(left[cid]['why'])[:120]}); asked again in "
                            f"{RETRY_SOON_DAYS} days")
            n["left"] += 1
        elif row["action"] == "HOLD":
            first = str((row.get("doubts") or [""])[0])[:120]
            if rep["error"]:
                res.update(retry_soon=True,
                           note=f"{b} is held for the owner, and filing it failed "
                                f"({str(rep['error'])[:100]}); asked again in "
                                f"{RETRY_SOON_DAYS} days")
                n["unfiled"] += 1
            elif cid in added:
                res["note"] = (f"{b} reads ({row.get('postings')} posting(s)) and {would or 'is '}"
                               f"held for the owner in Boards we found: {first}")
                n["held"] += 1
            else:
                res["note"] = (f"{b} is held for the owner; the queue did not take it "
                               f"({str(not_taken.get(cid) or 'no row')[:100]})")
                n["not_taken"] += 1
        else:
            kind = row.get("kind") or "?"
            res["note"] = f"{b}: {str(row.get('why') or kind)[:160]}"
            if kind == "unreadable":
                res["retry_soon"] = True
                n["again"] += 1
            n["elsewhere" if kind == "elsewhere" else "skipped"] += 1
        if not write:
            # a look is not an answer: nothing was written or filed
            res["retry_soon"] = True
        out[cid] = res
    return out, n


def read_marker(html: str, company: dict, page_url: str = "") -> tuple[dict | None, str]:
    """First ATS marker in the page, as a fetchable block, plus the tenant name
    to check it against. Body only, never headers - WordPress.com stamps an ad
    for join.a8c.com on every response it serves.

    In MARKERS' order - a structured API beats a generic careers page - with
    the finder's platforms (FINDER_KINDS) between the two: after every board
    MARKERS names, before every careers page. A FINDER_KINDS board carries no
    tenant name to check: it is not this file's to check (through_the_door).
    """
    for kind, pat in MARKERS:
        if kind != "html":
            hit = _marker(kind, pat, html)
            if hit:
                return hit
    block = finder_board(html, page_url, company.get("website") or "")
    if block:
        return block, ""
    for kind, pat in MARKERS:
        if kind == "html":
            hit = _marker(kind, pat, html)
            if hit:
                return hit
    return None, ""


def probe(company: dict) -> dict:
    """Look for an ATS on a company's site. Returns a result record.

    The outcome vocabulary matters as much as the finding. "blocked" and
    "fetch error" are not "no board found": a bot wall answers, and reading its
    answer as an absence is the same mistake a page scan makes when it reports a
    board is empty because it could not read it.
    """
    site = (company.get("website") or "").rstrip("/")
    if not site.startswith("http"):
        return {"id": company["id"], "found": None,
                "note": f"website on file is not a URL: {site[:40]!r}"}
    started = time.monotonic()

    home = get(site)
    if home.outcome == "robots":
        # Not retried soon: it is the site's standing answer, not a bot wall
        # having a bad day. "could not fetch" keeps it out of none-found.
        return {"id": company["id"], "found": None,
                "note": f"could not fetch the site: {home.text[:80]}"}
    if home.outcome == "blocked":
        return {"id": company["id"], "found": None, "retry_soon": True,
                "note": f"blocked at the door (HTTP {home.status})"}
    if home.outcome != "ok":
        return {"id": company["id"], "found": None, "retry_soon": True,
                "note": f"could not fetch the site ({home.text or home.status})"}

    # A control request first. One host in five answers 200 to a path that
    # cannot exist, so on those hosts a 200 carries no information at all - and
    # accepting careers pages without this test would write ~127 records
    # pointing at homepages.
    ctl = get(site + "/zz-no-such-page-8471")
    bad = {fingerprint(home.text)}
    catch_all = False
    if ctl.outcome == "ok":
        bad.add(fingerprint(ctl.text))
        catch_all = True

    links = career_links(home.text, home.url)
    # Only fall back to guessed paths when the page offered nothing. Measured:
    # 3 companies in 90 needed a guess, and skipping them buys the budget that
    # the control request and the one-hop crawl cost.
    guesses = [] if len(links) >= 2 or catch_all else [site + p for p in CAREER_PATHS]
    targets, seen = [], set()
    for t in links + guesses:
        t = t.rstrip("/")
        if t and t not in seen and t != site:
            seen.add(t)
            targets.append(t)

    block, slug = read_marker(home.text, company, home.url)
    where = home.url                # the page the board was named on
    pages = [(site, home.text)]
    careers_url, careers_empty = None, False
    hopped = False

    while True:
        if block is None:
            for target in targets:
                if time.monotonic() - started > COMPANY_BUDGET:
                    return {"id": company["id"], "found": None, "retry_soon": True,
                            "note": f"gave up after {COMPANY_BUDGET}s"}
                r = get(target)
                if r.outcome == "robots":
                    continue          # that page is closed to us; the next may not be
                if r.outcome == "blocked":
                    where = (target[len(site):] or "/") if target.startswith(site) else target
                    return {"id": company["id"], "found": None, "retry_soon": True,
                            "note": f"blocked at {where} (HTTP {r.status})"}
                if r.outcome != "ok":
                    continue
                if fingerprint(r.text) in bad:
                    continue          # soft 404: the homepage wearing a new URL
                pages.append((r.url, r.text))
                block, slug = read_marker(r.text, company, r.url)
                if block is not None:
                    where = r.url
                    break
                if careers_url is None and _is_careers(r.url, r.text):
                    careers_url = r.url
                    careers_empty = bool(NO_OPENINGS.search(visible(r.text)[:4000]))
        if block is not None or hopped:
            break
        # Nothing yet. Try one hop into the pages that tend to hide a careers
        # link, and re-run link extraction on them.
        hopped = True
        if time.monotonic() - started > COMPANY_BUDGET - 6:
            break
        more = []
        for hp in HOP_PATHS:
            if time.monotonic() - started > COMPANY_BUDGET - 3:
                break
            r = get(site + hp)
            if r.outcome != "ok" or fingerprint(r.text) in bad:
                continue
            block, slug = read_marker(r.text, company, r.url)
            if block is not None:
                where = r.url
                break
            more += [u for u in career_links(r.text, r.url)
                     if u.rstrip("/") not in seen]
        if block is not None:
            break
        targets = []
        for u in more:
            k = u.rstrip("/")
            if k not in seen:
                seen.add(k)
                targets.append(k)
        if not targets:
            break

    if block is not None and block["type"] in FINDER_KINDS:
        # NOT READ, AND NOT NAME-CHECKED, HERE. Once the sweep is over, the
        # finder reads their site its own way and the wiring door judges what
        # it found (main, through_the_door). Until it has, a crash or a
        # timeout between here and there must not leave this company asleep
        # for RETRY_DAYS.
        return {"id": company["id"], "found": None, "retry_soon": True,
                "door": {"board": block, "page": where},
                "note": f"{block['type']}:{str(block['ref'])[:40]} named on a page read, for "
                        f"the wiring door's two checks"}

    if block is not None:
        ref = block["ref"]
        identity = slug
        try:
            saved, ats.TIMEOUT = ats.TIMEOUT, 10
            try:
                jobs = ats.fetch(block)
            finally:
                ats.TIMEOUT = saved
        except Exception as exc:
            return {"id": company["id"], "found": None, "retry_soon": True,
                    "note": f"{block['type']}:{str(ref)[:34]} found but unreadable "
                            f"({str(exc)[:36]})"}
        real = [j for j in jobs if (j.get("title") or "").strip()]
        # Every board gets the name test, whatever shape its ref takes. A board
        # found on a company's own careers page is routinely the parent's: an
        # acquired product line's careers link points at the acquirer.
        if not identity:
            # No company name anywhere in the reference, so nothing can confirm
            # it belongs here. A person can, in seconds.
            return {"id": company["id"], "found": None, "suspect": block,
                    "note": f"{block['type']} board reads ({len(real)} posting(s)) but "
                            f"its reference carries no company name, so it cannot "
                            f"be checked automatically"}
        if paren_only(identity, company):
            return {"id": company["id"], "found": None, "suspect": block,
                    "note": f"{block['type']}:{identity} reads ({len(real)} posting(s)) "
                            f"and matches the acquirer named in "
                            f"{company['name']!r}; confirm it is scoped to this "
                            f"product line before wiring it up"}
        if not slug_matches(identity, company):
            return {"id": company["id"], "found": None, "suspect": block,
                    "note": f"{block['type']}:{identity} reads ({len(real)} posting(s)), "
                            f"but does not match {company['name']!r}; "
                            f"likely a parent or another company"}
        return {"id": company["id"], "found": block,
                "note": f"{block['type']}:{str(ref)[:40]}, {len(real)} posting(s) readable",
                "postings": len(real)}

    if careers_url:
        # Honest about what this is worth: a careers page with no ATS behind it
        # is a page a person can read and a text scan can sometimes mine. It is
        # not a structured board, and counting it as one inflates the number
        # that matters.
        note = "careers page, no ATS behind it"
        if careers_empty:
            note = "careers page, says it has no openings"
        # Show the path, not a blind slice of the URL: a redirect can change the
        # host, and site-length slicing then prints "eers/" for "/careers/".
        shown = urllib.parse.urlsplit(careers_url).path or "/"
        return {"id": company["id"], "found": {"type": "html", "ref": careers_url},
                "note": f"{note} ({shown[:44]})",
                "postings": 0, "weak": True}

    if catch_all:
        return {"id": company["id"], "found": None,
                "note": "no board found; host answers 200 to any path, so a "
                        "negative here is weak"}
    return {"id": company["id"], "found": None, "note": "no board found"}


def _is_careers(url: str, html: str) -> bool:
    """Is this actually a careers page?

    The old test was a phrase gate - "open positions", "we're hiring" and
    friends. Measured against real careers pages found by hand, it matched 3 of
    29. Path and title are far better signals, and the length floor keeps a
    redirect stub or an empty shell from qualifying.
    """
    path = urllib.parse.urlsplit(url).path
    heads = re.findall(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)[:1]
    heads += re.findall(r"<h1[^>]*>(.*?)</h1>", html or "", re.I | re.S)[:2]
    head = re.sub(r"<[^>]+>", " ", " ".join(heads))
    if CAREER_PATH.search(path):
        pass
    elif CAREER_TITLE.search(head) and not ARTICLE_PATH.search(path):
        pass
    else:
        return False
    return len(visible(html)) >= 400


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--budget", type=int,
                    help="seconds per company before giving up (default 25). "
                         "104 companies were cut off by the default, and a slow "
                         "site is not the same as a site with no board.")
    ap.add_argument("--only", metavar="FILE",
                    help="probe only the company ids listed in this file, one "
                         "per line, ignoring the retry window")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--review", action="store_true",
                    help="list boards that read fine but whose slug did not match")
    ap.add_argument("--confirm", metavar="COMPANY_ID",
                    help="accept a reviewed board for this company")
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    log = load_log()

    if a.confirm:
        sus = json.loads(SUSPECTS.read_text()) if SUSPECTS.exists() else {}
        entry = sus.get(a.confirm)
        if not entry:
            print(f"no reviewable board for {a.confirm!r}", file=sys.stderr)
            return 1
        by_id = {c["id"]: c for c in companies}
        if a.confirm not in by_id:
            print(f"no company {a.confirm!r}", file=sys.stderr)
            return 1
        # a board the owner holds - for this company or under any other
        # record's id - is not confirmed here either: it is his to settle
        import board_proposals
        try:
            held_ids = board_proposals.held_for_the_owner(DATA)
            boards_held = board_proposals.held_boards_for_the_owner(DATA, companies)
        except board_proposals.Unreadable as exc:
            print(f"{exc}. Nothing was written.", file=sys.stderr)
            return 2
        block = entry.get("ats") or {}
        if a.confirm in held_ids or board_proposals.held_board(
                boards_held, block.get("type"), block.get("ref"), a.confirm,
                board_proposals.their_names(by_id[a.confirm])):
            print(f"{a.confirm}: that board, or this company, is held for the owner (a "
                  f"reader's doubt, a board waiting in Boards we found or Agent proposals, or "
                  f"a refusal); "
                  f"it is his to settle there. Nothing was written.", file=sys.stderr)
            return 1
        by_id[a.confirm]["ats"] = entry["ats"]
        (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")
        sus.pop(a.confirm)
        SUSPECTS.write_text(json.dumps(sus, indent=1) + "\n")
        print(f"{by_id[a.confirm]['name']} now monitored via "
              f"{entry['ats']['type']}:{entry['ats']['ref']}")
        return 0

    if a.review:
        sus = json.loads(SUSPECTS.read_text()) if SUSPECTS.exists() else {}
        if not sus:
            print("nothing awaiting review")
            return 0
        by_id = {c["id"]: c for c in companies}
        print(f"{len(sus)} board(s) read fine but the slug did not match the company.")
        print("These are usually acquisitions. Confirm the ones that are real:\n")
        for cid, e in sus.items():
            name = by_id.get(cid, {}).get("name", cid)
            print(f"  {name}")
            print(f"    board:   {e['ats']['type']}:{e['ats']['ref']}")
            print(f"    accept:  python3 scripts/discover_ats.py --confirm {cid}")
        return 0

    if a.stats:
        t = collections.Counter((c.get("ats") or {}).get("type") for c in companies)
        probed = len(log)
        found = sum(1 for v in log.values() if v.get("found"))
        pending = [c for c in companies
                   if (c.get("ats") or {}).get("type") in (None, "unknown")
                   and c.get("website") and stale(log.get(c["id"]))]
        print(f"{len(companies)} companies")
        print(f"  {sum(n for k, n in t.items() if k not in (None, 'unknown'))} have a board")
        print(f"  {t.get('unknown', 0) + t.get(None, 0)} do not")
        print(f"  {probed} probed so far, {found} of those produced a working board")
        print(f"  {len(pending)} ready to probe now")
        return 0

    if a.budget:
        # A slow site is not a site with no board. 104 companies were cut off by
        # the default budget mid-probe and recorded as a failure, which is the
        # same false-absence mistake the page scans make.
        global COMPANY_BUDGET
        COMPANY_BUDGET = a.budget

    # NEVER PROBE A COMPANY A PERSON RULED OUT. Concourse was ruled out on
    # 2026-08-24 and its wrong board unwired; the 10-04 sweep saw "no board,
    # has a website" and wired ashby/concourse straight back, overwriting the
    # note that warned against it. Applies to --only lists too: a list is a
    # decision about what to probe, not a reversal of a ruling.
    ruled = {c["id"] for c in companies if c.get("out_of_scope")}
    # NOR ONE THE OWNER HOLDS. A reader's doubt that stands, a row a door filed
    # for him in Boards we found, a board refused in either store, or a board
    # proposal still pending in Agent proposals (blind review, 2026-11-21: the
    # sweep wrote a pending company's board): each is a company whose board is
    # his to settle, and a slug that matches the name is
    # exactly what the doubted board's slug does (the readers doubted JustFOIA's
    # own board, "an MCCi brand"). The second blind review of the wiring door
    # (2026-11-19) had this sweep probe a doubted company and write the doubted
    # board, unattended. Asked of the files beside the companies.json this run
    # writes; one that is on disk and does not read stops the run here, before
    # the first probe - read as empty, it would hold nobody.
    # AND NO BOARD HE HOLDS, UNDER ANY RECORD'S ID. A company merged into its
    # duplicate at the desk leaves its doubt, its door-filed row and its
    # refusals under the id that was dropped, so the survivor is held by
    # nobody - and this sweep would probe it, find the very board, and write
    # it. So a board found is written only when no hold of his names it
    # (board_proposals.held_board: the reader the wiring door uses too, a
    # Paylocity board compared by every address on both sides - seventh blind
    # review, 2026-11-22).
    import board_proposals
    try:
        held = board_proposals.held_for_the_owner(DATA)
        boards_held = board_proposals.held_boards_for_the_owner(DATA, companies)
    except board_proposals.Unreadable as exc:
        print(f"{exc}. Which companies the owner holds cannot be read, so nothing is "
              f"probed and nothing is written.", file=sys.stderr)
        return 2
    if a.only:
        wanted = {ln.strip() for ln in pathlib.Path(a.only).read_text().splitlines()
                  if ln.strip()}
        if wanted & ruled:
            print(f"  ({len(wanted & ruled)} id(s) in the list were ruled out of scope "
                  f"by a person and are not probed)")
        wanted -= ruled
        # counts only: the workflow's log is public
        if wanted & set(held):
            print(f"  ({len(wanted & set(held))} id(s) in the list are held for the owner - "
                  f"a reader's doubt, a board waiting in Boards we found or Agent proposals, "
                  f"or a refusal - "
                  f"and are not probed)")
        wanted -= set(held)
        todo = [c for c in companies if c["id"] in wanted and c.get("website")]
        missing = wanted - {c["id"] for c in todo}
        if missing:
            print(f"  ({len(missing)} id(s) in the list have no website or no record)")
    else:
        due = [c for c in companies
               if (c.get("ats") or {}).get("type") in (None, "unknown")
               and c.get("website") and stale(log.get(c["id"]))
               and c["id"] not in ruled]
        todo = [c for c in due if c["id"] not in held]
        if len(todo) != len(due):
            print(f"  ({len(due) - len(todo)} compan{'y is' if len(due) - len(todo) == 1 else 'ies are'} "
                  f"held for the owner - a reader's doubt, a board waiting in Boards we "
                  f"found or Agent proposals, or a refusal - and not probed)")
    todo.sort(key=lambda c: probe_order(c, log))
    # An explicit id list is already the decision about scope. Clipping it to
    # --limit silently probed 300 of 768 and reported as though that were all
    # of them.
    if not a.only:
        todo = todo[:a.limit]
    if not todo:
        print("nothing to probe. every company either has a board or was tried recently.")
        return 0

    print(f"probing {len(todo)} companies with {a.workers} workers...", flush=True)
    today = dt.date.today().isoformat()
    results = []
    by_id_all = {c["id"]: c for c in companies}

    def checkpoint():
        """Write what we have. Without this a long sweep that is interrupted, or
        that someone has to stop, loses every probe it completed."""
        for r in results:
            log[r["id"]] = {"on": today, "found": bool(r.get("found")),
                            "note": r["note"]}
            if r.get("retry_soon"):
                log[r["id"]]["retry_soon"] = True
            if r.get("weak"):
                log[r["id"]]["weak"] = True
            if a.write and r.get("found"):
                by_id_all[r["id"]]["ats"] = r["found"]
        LOG.write_text(json.dumps(log, indent=1) + "\n")
        if a.write:
            (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")

    # as_completed, not map. map yields in submission order, so one company
    # whose socket hangs blocks every finished result behind it: a 768-company
    # sweep ran 37 minutes and printed nothing while the workers were in fact
    # working. Results now land as they finish, and so do the checkpoints.
    left_alone, near_misses_held = [], []

    def unless_held(r):
        """The result, or - when the board found is one the owner holds under
        any record's id - a result that writes nothing. The log says so in
        words that name no board and no doubt (it is committed, and public).

        A NEAR MISS TOO (blind review, 2026-11-21). A board that read and did
        not match the name is kept in ats_suspects.json, which feeds the
        Acquisitions queue and --review's "accept" line, and is printed in
        the run's public log: a board somebody refused for another record
        went back in front of the owner that way. Held, it is counted and
        neither kept nor printed."""
        f = r.get("found")
        c = by_id_all.get(r.get("id")) or {}
        if isinstance(f, dict) and board_proposals.held_board(
                boards_held, f.get("type"), f.get("ref"), r.get("id"),
                board_proposals.their_names(c)):
            left_alone.append(r["id"])
            return {"id": r["id"], "found": None,
                    "note": "a board was found and is held for the owner under another "
                            "record, so it was not written"}
        s = r.get("suspect")
        if isinstance(s, dict) and board_proposals.held_board(
                boards_held, s.get("type"), s.get("ref"), r.get("id"),
                board_proposals.their_names(c)):
            near_misses_held.append(r["id"])
            return {"id": r["id"], "found": None,
                    "note": "a board was found and is held for the owner under another "
                            "record, so it was not kept for review"}
        return r

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futures = {ex.submit(probe, c): c for c in todo}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            try:
                results.append(unless_held(fut.result()))
            except Exception as exc:
                c = futures[fut]
                results.append({"id": c["id"], "found": None, "retry_soon": True,
                                "note": f"probe crashed: {type(exc).__name__}"})
            if i % 25 == 0:
                hits = sum(1 for x in results if x.get("found"))
                print(f"  {i}/{len(todo)}, {hits} board(s) found so far", flush=True)
                checkpoint()
    checkpoint()

    # THE FINDER'S PLATFORMS GO THROUGH THE WIRING DOOR, once the sweep is
    # over: twins, holders and the owner's queue are only known then. The
    # board the probe stopped at - wherever it was seen, as every board found
    # here is - goes to the screen above first, unchanged; then the finder
    # reads their site its own way (read_their_sites), and every board its
    # record names on their own site goes to the same screen. A company any
    # of them is held for under another record is left alone, and its log
    # names no board. (Live, 2026-11-22: OPEXUS's and Baseline's sites led to
    # ADP boards held for Casepoint and HydroPoint, named on pages not their
    # own.) What is left, the door judges (through_the_door).
    door_said = collections.Counter()
    door = {r["id"]: i for i, r in enumerate(results) if r.get("door")}
    if door:
        import find_site_boards as fsb
        settled, stopped_at = {}, {}

        def again(cid: str, words: str, count: str) -> None:
            settled[cid] = {"id": cid, "found": None, "retry_soon": True,
                            "note": f"{board_proposals.show(**stopped_at[cid])}: {words}; asked "
                                    f"again in {RETRY_SOON_DAYS} days"}
            door_said[count] += 1

        # oldest probe first, as the sweep is; a board found for two companies
        # is handed over with both or with neither, so the door sees them as
        # twins (a cap that split them would let the first be wired)
        rank = {c["id"]: k for k, c in enumerate(todo)}
        groups: dict = {}
        for cid in sorted(door, key=lambda x: rank.get(x, len(rank))):
            r = results[door[cid]]
            b = r["door"]["board"]
            stopped_at[cid] = {"kind": b["type"], "ref": b["ref"]}
            got = unless_held({"id": cid, "found": b})
            if got.get("found") is None:
                results[door[cid]] = got
                continue
            groups.setdefault(board_proposals.board_key(b["type"], b["ref"]), []).append(cid)
        handed: list = []
        for ids in groups.values():
            if len(handed) + len(ids) <= DOOR_AT_ONCE:
                handed += ids
            else:
                for cid in ids:
                    again(cid, f"one of more than {DOOR_AT_ONCE} boards for the wiring door this "
                               f"run", "later")
        records, unread = read_their_sites(
            [by_id_all[cid] for cid in handed], fsb.site_index(companies), DOOR_SECONDS,
            pages={cid: results[door[cid]]["door"].get("page") for cid in handed})
        for cid in unread:
            again(cid, "the finder's reading of their site did not fit in this run's time",
                  "later")
        ready = {}
        for cid, rec in records.items():
            if fsb.unfinished(rec):
                # our side's failure, not their site's answer
                again(cid, f"the finder's reading of their site did not finish "
                           f"({str(rec.get('error') or '')[:60]})", "unasked")
                continue
            for cand in rec.get("candidates") or []:
                got = unless_held({"id": cid, "found": {"type": cand.get("type"),
                                                       "ref": cand.get("ref")}})
                if got.get("found") is None:
                    results[door[cid]] = got
                    break
            else:
                ready[cid] = rec
        said, counts = through_the_door(
            ready, companies, by_id_all, write=a.write, today=today,
            stopped_at={cid: {"type": b["kind"], "ref": b["ref"]}
                        for cid, b in stopped_at.items()})
        settled.update(said)
        door_said.update(counts)
        for cid, res in settled.items():
            results[door[cid]] = dict(res, door_said=True)
        checkpoint()
    if door_said:
        # counts only: the workflow's log is public
        words = (("wired", "wired on its two checks"),
                 ("held", "held for the owner in Boards we found"),
                 ("not_taken", "held, and already waiting or ruled on in that queue"),
                 ("unfiled", "held, and not filed"), ("left", "left alone at the write"),
                 ("skipped", "skipped"),
                 ("elsewhere", "settled on another platform's board, left for the board sweep"),
                 ("later", "over this run's share"), ("unasked", "not asked"))
        said = [f"{door_said[k]} {w}" for k, w in words if door_said[k]]
        again_n = sum(door_said[k] for k in ("unfiled", "left", "again", "later", "unasked"))
        print(f"  ({sum(door_said[k] for k, _w in words)} board(s) on "
              f"{', '.join(FINDER_KINDS)} went to the wiring door: {'; '.join(said)}"
              + (f"; {again_n} asked again in {RETRY_SOON_DAYS} days" if again_n else "")
              + (" - a dry run wires and files nothing" if not a.write else "") + ")")
    if left_alone:
        # counts only: the workflow's log is public
        print(f"  ({len(left_alone)} board(s) found {'is' if len(left_alone) == 1 else 'are'} "
              f"held for the owner under another record - a reader's doubt, a board "
              f"waiting in Boards we found or Agent proposals, or a refusal - and not "
              f"written)")
    if near_misses_held:
        # counts only, like the line above
        print(f"  ({len(near_misses_held)} near miss(es) - a board that read and did not "
              f"match the name - {'is' if len(near_misses_held) == 1 else 'are'} held for "
              f"the owner under another record, and not kept for review)")

    # Persist the near-misses. A readable board whose slug does not match is
    # usually an acquisition the name gives no hint of: Bonfire Interactive posts
    # on Euna's board because Euna bought them. No string comparison can know
    # that, and a person can confirm it in seconds, so these are kept for review
    # rather than guessed at in either direction.
    suspect = [r for r in results if r.get("suspect")]
    if suspect:
        prior = json.loads(SUSPECTS.read_text()) if SUSPECTS.exists() else {}
        for r in suspect:
            prior[r["id"]] = {"ats": r["suspect"], "note": r["note"], "on": today}
        SUSPECTS.write_text(json.dumps(prior, indent=1) + "\n")
        print(f"\n{len(suspect)} slug(s) read fine but do not match the company, "
              "so they are not being written:")
        for r in suspect[:8]:
            print(f"   {r['id']:<28} {r['note'][:74]}")

    found = [r for r in results if r.get("found")]
    strong = [r for r in found if not r.get("weak")]
    weak = [r for r in found if r.get("weak")]
    # (what the wiring door asks again is counted on its own line, above)
    blocked = [r for r in results if r.get("retry_soon") and not r.get("door_said")]
    by_kind = collections.Counter(r["found"]["type"] for r in strong)
    # Structured boards and careers pages are not the same finding, and adding
    # them together is how a discovery sweep reports a number nobody can use.
    print(f"\n{len(strong)} of {len(todo)} produced a structured board:")
    for k, n in by_kind.most_common():
        print(f"  {n:>4}  {k}")
    with_jobs = sum(1 for r in strong if r.get("postings"))
    print(f"  {with_jobs} of those currently have readable postings")
    if weak:
        print(f"\n{len(weak)} more have a careers page with no ATS behind it. "
              "A person can read those; a fetcher mostly cannot.")
    if blocked:
        print(f"{len(blocked)} were blocked or unreachable - not a negative, "
              f"and requeued in {RETRY_SOON_DAYS} days.")

    for r in results:
        log[r["id"]] = {"on": today, "found": bool(r.get("found")), "note": r["note"]}
        if r.get("retry_soon"):
            log[r["id"]]["retry_soon"] = True
        if r.get("weak"):
            log[r["id"]]["weak"] = True

    if not a.write:
        print("\ndry run. re-run with --write to record the discoveries.")
        for r in found[:12]:
            print(f"   {r['id']:<30} {r['note']}")
        return 0

    by_id = {c["id"]: c for c in companies}
    for r in found:
        by_id[r["id"]]["ats"] = r["found"]
    (DATA / "companies.json").write_text(json.dumps(companies, indent=2) + "\n")
    LOG.write_text(json.dumps(log, indent=1) + "\n")
    fetchable = sum(1 for c in companies
                    if (c.get("ats") or {}).get("type") not in (None, "unknown"))
    print(f"\nwrote {len(found)} board(s). {fetchable} of {len(companies)} "
          f"companies are now monitored.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
