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
MARKERS: list[tuple[str, str]] = [
    ("ashby", r"jobs\.ashbyhq\.com/(?:embed\?org=)?([a-zA-Z0-9._-]+)"),
    ("greenhouse", r"(?:job-boards|boards)\.greenhouse\.io/"
                   r"(?:embed/job_board(?:/js)?\?for=)?([a-zA-Z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("workable", r"apply\.workable\.com/([a-zA-Z0-9-]+)"),
    ("recruitee", r"([a-zA-Z0-9-]+)\.recruitee\.com"),
    ("breezy", r"([a-zA-Z0-9-]+)\.breezy\.hr"),
    ("bamboohr", r"([a-zA-Z0-9-]+)\.bamboohr\.com"),
    ("jazzhr", r"([a-zA-Z0-9-]+)\.applytojob\.com"),
    ("rippling", r"ats\.rippling\.com/([a-zA-Z0-9-]+)"),
    ("icims", r"([a-zA-Z0-9-]+)\.icims\.com"),
    # Families a field audit found on real govtech careers pages, in plain HTML,
    # that the script simply had no pattern for. This was the single highest
    # recovery-per-line change available.
    ("workday", r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:en-US/)?([A-Za-z0-9_-]+)"),
    ("workday", r"(wd\d+)\.myworkdaysite\.com/(?:en-US/)?recruiting/"
                r"([a-z0-9-]+)/([A-Za-z0-9_-]+)"),
    ("paylocity", r"(recruiting\.paylocity\.com/recruiting/jobs/All/[0-9a-f-]{36}/[^\s\"'<>]*)"),
    ("oracle", r"([a-z0-9]+\.fa\.[a-z0-9]+\.oraclecloud\.com)"),
    ("html", r"(workforcenow\.adp\.com/mascsr/default/mdf/recruitment/"
             r"recruitment\.html\?cid=[0-9a-f-]{36}[^\s\"'<>]*)"),
    ("html", r"([a-z0-9-]+\.paycomonline\.net/v4/ats/web\.php/jobs\?clientkey=[0-9A-F]{32})"),
    ("html", r"(jobs\.jobvite\.com/[a-z0-9-]+)"),
    ("html", r"([a-z0-9-]+\.teamtailor\.com)"),
    ("html", r"([a-z0-9-]+\.applicantpro\.com|[a-z0-9-]+\.clearcompany\.com|"
             r"[a-z0-9-]+\.isolvedhire\.com|[a-z0-9-]+\.dayforcehcm\.com|"
             r"recruiting\d*\.ultipro\.com/[A-Z0-9_]+|[a-z0-9-]+\.pinpointhq\.com|"
             r"[a-z0-9-]+\.taleo\.net|[a-z0-9-]+\.avature\.net|[a-z0-9-]+\.csod\.com)"),
    # HiBob is deliberately last and deliberately never slug-probed: every
    # subdomain returns a byte-identical 1342-byte shell, so a probe proves
    # nothing. It only counts when the company's own site links to it.
    ("html", r"([a-z0-9-]+\.careers\.hibob\.com)"),
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
    # A parenthetical usually names the acquirer: "Simpleview (Granicus)" posts on
    # Granicus's board, and rejecting that loses a real, correct find.
    paren = [_norm(x) for x in re.findall(r"\(([^)]{2,40})\)", raw)]
    first = _norm(re.split(r"[^A-Za-z0-9]+", raw)[0]) if raw else ""

    for other in [name, host, *paren]:
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

_lock = threading.Lock()


def load_log() -> dict:
    return json.loads(LOG.read_text()) if LOG.exists() else {}


# A refusal is not an answer. A blocked or errored probe learned nothing, so it
# comes back around in days rather than sitting out the full window as though it
# had proved something.
RETRY_SOON_DAYS = 7


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


def get(url: str) -> Fetch:
    import requests
    try:
        r = requests.get(url, headers=ats.UA, timeout=PROBE_TIMEOUT,
                         allow_redirects=True)
    except Exception as exc:
        return Fetch(url=url, outcome="error", status=0,
                     text=type(exc).__name__)
    body = r.text or ""
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


def visible(html: str) -> str:
    """Text with markup, script and style removed - what a reader would see."""
    t = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html or "")
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


def read_marker(html: str, company: dict) -> tuple[dict | None, str]:
    """First ATS marker in the page, as a fetchable block, plus the tenant name
    to check it against. Body only, never headers - WordPress.com stamps an ad
    for join.a8c.com on every response it serves."""
    for kind, pat in MARKERS:
        m = re.search(pat, html, re.I)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if not groups:
            continue
        if kind == "workday":
            if len(groups) < 3:
                continue
            block = {"type": kind, "ref": list(groups[:3])}
            return block, identity_of(kind, groups, block["ref"])
        ref = groups[0]
        if kind in URL_MARKERS or "/" in ref or "." in ref:
            if not ref.startswith("http"):
                ref = "https://" + ref
            return {"type": kind, "ref": ref}, identity_of(kind, groups, ref)
        if ref.lower() in RESERVED:
            continue
        return {"type": kind, "ref": ref}, identity_of(kind, groups, ref)
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

    block, slug = read_marker(home.text, company)
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
                if r.outcome == "blocked":
                    return {"id": company["id"], "found": None, "retry_soon": True,
                            "note": f"blocked at {target[len(site):] or '/'} "
                                    f"(HTTP {r.status})"}
                if r.outcome != "ok":
                    continue
                if fingerprint(r.text) in bad:
                    continue          # soft 404: the homepage wearing a new URL
                pages.append((r.url, r.text))
                block, slug = read_marker(r.text, company)
                if block is not None:
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
            block, slug = read_marker(r.text, company)
            if block is not None:
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

    if a.only:
        wanted = {ln.strip() for ln in pathlib.Path(a.only).read_text().splitlines()
                  if ln.strip()}
        todo = [c for c in companies if c["id"] in wanted and c.get("website")]
        missing = wanted - {c["id"] for c in todo}
        if missing:
            print(f"  ({len(missing)} id(s) in the list have no website or no record)")
    else:
        todo = [c for c in companies
                if (c.get("ats") or {}).get("type") in (None, "unknown")
                and c.get("website") and stale(log.get(c["id"]))]
    # Sector order is the buyer-motion order: the closest markets first, so a
    # partial run is still the most useful partial run.
    order = {"General Gov": 0, "Public Works": 1, "Parks & Rec": 2,
             "Public Safety": 3, "Transit & Parking": 4, "K-12 Schools": 5,
             "Utilities & Energy": 6, "Airports & Aviation": 7}
    todo.sort(key=lambda c: order.get(c.get("sector"), 9))
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

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(probe, todo), 1):
            results.append(r)
            if i % 50 == 0:
                hits = sum(1 for x in results if x.get("found"))
                print(f"  {i}/{len(todo)}, {hits} board(s) found so far", flush=True)
                checkpoint()
    checkpoint()

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
    blocked = [r for r in results if r.get("retry_soon")]
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
