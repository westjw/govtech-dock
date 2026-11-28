"""ATS fetchers.

Each fetcher returns a list of rows for a company's open roles, or raises
AtsError when the board can't be read (network error, 404 slug, JS-walled
page). Callers treat AtsError as status "Unknown".

A row is:

    {"title": str, "location": str, "url": str,
     "jd":   plain-text job description, or "" when the board gave none,
     "comp": None, or {"min", "max", "currency", "period", "source", "raw"}}

jd and comp are guaranteed by plain_rows(); a fetcher for a board that
publishes neither simply does not set them. **An empty jd is not a job with no
description and a null comp is not a job that pays nothing** - both mean "the
board did not tell us", the same way an unreadable page means Unknown and not
"None found".

Supported types (data/companies.json -> ats.type):
  ashby, greenhouse, lever, workable, recruitee, breezy, smartrecruiters,
  bamboohr, workday, rippling, jazzhr, icims, paylocity, oracle
                               -> structured JSON APIs / server-rendered boards
  html                         -> fetch page, strip tags, scan visible text (weak signal)
  unknown                      -> not fetchable; needs ATS discovery (ask Claude Code)

Where the description lives, per board (measured, 2026-08-23):

  free, in the list response we already make
    lever        descriptionPlain + lists + additionalPlain (all five fields;
                 no one of them is the whole ad)
    ashby        descriptionPlain, and structured pay behind ?includeCompensation
    greenhouse   content, behind ?content=true - same endpoint, same request
    workable     description, behind ?details=true - same endpoint, same request
    recruitee    description + requirements, and a structured salary block
    breezy       pay only ("$160,000 - $200,000 / year"); description is not there
    paylocity    a 110-character preview, cut mid-word, and never text-parsed
    html         schema.org JobPosting blocks already embedded in the page -
                 read because the page is already downloaded, but rare: none of
                 ten careers indexes sampled carried one. Ten is not 887, so
                 this is "not seen yet", not "does not happen".
    jobvitefeed  description, every job's whole ad, in the account's one feed:
                 always each row's jd, flag or no flag (2026-11-23)

  one extra request per posting (behind FETCH_DETAILS, off by default)
    bamboohr     /careers/<id>/detail  -> description + a compensation field
    smartrecruiters  /postings/<id>    -> jobAd.sections
    workday      /wday/cxs/<t>/<site><externalPath> -> jobPostingInfo.jobDescription
    breezy       the posting page      -> schema.org JobPosting
    jazzhr       the posting page      -> schema.org JobPosting
    icims        the posting page      -> schema.org JobPosting
    rippling     the posting page      -> __NEXT_DATA__ apiData.jobPost
    successfactors  the posting page   -> its itemprop="description" element
                    (a Unify site's costs nothing more: its row is read from that page)
    phenom       the posting page      -> ddo.jobDetail.data.job.description (a row's
                 descriptionTeaser is ML-written, never the ad: 2026-11-27)

  nothing published
    oracle       no company on file uses it; unverified either way

  not asked at all
    trinethire   the posting page carries the ad; it is never asked, flag or
                 no flag: app.trinethire.com's robots.txt leaves ai-input
                 unset, so reading its ad text is the owner's call (2026-11-24)
"""
from __future__ import annotations

import contextlib
import html as html_lib
import json
import gzip
import hashlib
import os
import pathlib
import datetime as dt
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import xml.parsers.expat
from collections import Counter

import requests

# robots.txt is asked before every page (owner's ruling, 2026-10-09). A HARD
# import, unlike salary's below, on purpose: without it the crawler would read
# pages it has been asked to leave alone, and that has to fail loudly.
import robots

# salary.py turns free description text into the same comp block. It is a
# sibling module written alongside this one, and the import is guarded on
# purpose: a hard import means that if it is ever missing or broken at import
# time, ats.py fails to import, refresh.py and build_board.py die with it, and
# the board reports nothing open anywhere. That is a false "nobody is hiring" -
# the one failure this project cannot afford. A missing parser costs us the
# text-parsed salaries and nothing else.
try:
    import salary
except ImportError:                       # pragma: no cover - see comment above
    salary = None                         # type: ignore[assignment]

# Conventional bot identification: Mozilla/5.0 prefix, a name, and a contact URL.
# This is the standard well-behaved-crawler format, not a browser impersonation.
# Many WAFs reject anything without the Mozilla prefix outright, which is what was
# turning Pavilion into a 405. Where a site blocks this too (Daxko returns 429),
# that is a deliberate refusal and it goes to the manual worklist rather than
# getting a spoofed Chrome string.
UA = {"User-Agent": "Mozilla/5.0 (compatible; govtech-dock/1.0; "
                    "+https://github.com/westjw/govtech-dock)"}
TIMEOUT = 20

# Seven boards only publish the description on the posting page. Reading them
# turns ONE request per company into one per posting - roughly 900 extra calls
# a day at today's counts - on somebody else's API, and the daily refresh is
# already slow. So it is off, and turning it on is a decision the owner makes:
#
#     GOVTECH_DOCK_JD_DETAILS=1 python scripts/build_board.py
#     ats.FETCH_DETAILS = True          # or set it from a caller
#
# The boards that hand the description over in the list response we already
# make are always read, flag or no flag. Those are free.
FETCH_DETAILS = os.environ.get("GOVTECH_DOCK_JD_DETAILS", "") not in ("", "0")
DETAIL_PAUSE = 0.2                        # seconds between per-posting requests

# WORKDAY IS READ FROM THE OWNER'S MAC, AND THE CRAWL READS WHAT IT READ
# (owner, 2026-11-17: "Read Workday from my Mac"). From 2026-11-16 Workday
# answered 429 to GitHub's runners from the FIRST request of a run - 39 of 42
# tenants, Motorola at index 11 included - while the same code read all 42 from
# the owner's machine the same hour. GitHub's addresses are shared with every
# scraper on Azure; ours are not. So scripts/workday_snapshot.py runs on the
# Mac (launchd, deploy/launchd/) and commits data/workday_snapshot.json, and a
# run with GOVTECH_DOCK_WORKDAY_SNAPSHOT=1 (refresh.yml sets it) takes a
# Workday board from that file instead of asking Workday.
#
# The file never hides a failure. A board the Mac could not read is an error
# here too, said as the Mac's error. A board it has not read in
# WORKDAY_SNAPSHOT_MAX_AGE, or never read, is asked live - and when that live
# read fails, the failure names the snapshot's age, because the fix is then
# the Mac's job, not the board.
WORKDAY_SNAPSHOT = os.environ.get("GOVTECH_DOCK_WORKDAY_SNAPSHOT", "") not in ("", "0")
WORKDAY_SNAPSHOT_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "workday_snapshot.json"
WORKDAY_SNAPSHOT_MAX_AGE = dt.timedelta(hours=48)
_WD_SNAP: dict | None = None
_WD_SNAP_LOCK = threading.Lock()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def workday_key(ref) -> str:
    """The snapshot's key for one Workday ref: the ref itself, as JSON."""
    return json.dumps([str(x) for x in ref])


def _workday_snapshot() -> dict:
    global _WD_SNAP
    with _WD_SNAP_LOCK:
        if _WD_SNAP is None:
            try:
                _WD_SNAP = json.loads(WORKDAY_SNAPSHOT_PATH.read_text())
            except (OSError, ValueError):
                _WD_SNAP = {}
        return _WD_SNAP


def _workday_from_snapshot(ref, now: dt.datetime | None = None) -> tuple[list[dict] | None, str]:
    """(rows, "") when the Mac read this board recently; (None, why not) otherwise.

    Raises AtsError for a board the Mac read recently and could not read."""
    entry = (_workday_snapshot().get("boards") or {}).get(workday_key(ref))
    if not entry:
        return None, "the Mac's Workday snapshot has no entry for this board"
    try:
        at = dt.datetime.fromisoformat(entry["read_at"])
    except (KeyError, TypeError, ValueError):
        return None, "the Mac's Workday snapshot entry has no readable date"
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    age = (now or _utcnow()) - at
    if age > WORKDAY_SNAPSHOT_MAX_AGE:
        return None, (f"the Mac last read this board {age.total_seconds() / 3600:.0f}h ago "
                      f"(~/Library/Logs/sledjobs-workday.log says why)")
    if entry.get("error"):
        raise AtsError(f"{entry['error']} (read on the owner's machine, {entry['read_at'][:16]})")
    # Copies: a caller that edits a row must not edit the next caller's row.
    return [dict(r) for r in entry.get("rows") or []], ""


class AtsError(Exception):
    pass


class RobotsRefused(AtsError, robots.Refused):
    """robots.txt asks crawlers not to read the page, or its server would not
    say (a 5xx). scripts/robots.py. A robots.txt that did not answer at all is
    NOT this: it is a network error, raised as one, so it is retried as one."""


class RateLimited(AtsError):
    """The server asked us to slow down, which is not the same as a refusal.

    _get used to turn a 429 into an AtsError spelled exactly like a 404, so
    the discovery log recorded "blocked at the door" for a site that was
    being cooperative and telling us when to come back. The 7-day requeue
    then asked again at the same speed, having learned nothing.

    Carries `retry_after` in seconds when the server named one.
    """

    def __init__(self, url: str, seconds: float | None):
        self.retry_after = seconds
        when = f", asked for {seconds:g}s" if seconds else ", no Retry-After given"
        super().__init__(f"HTTP 429 rate limited for {url}{when}")


def plain(s: str) -> str:
    """The text a person reads, from whatever the board encoded it as.

    A board's markup carries a job title in one of two escaped forms, and the
    fetchers here cut both out with a regex, which - unlike a JSON or HTML
    parser - hands back the escape rather than the character. Routeware's
    "Product Manager - Customer Engagement & Education" arrived as
    "... Customer Engagement \\u0026 Education" from the page's embedded JSON,
    and reads "... &amp; Education" in the same page's anchor text.

    Neither is what the job is called, and a title is not just displayed: it is
    part of the posting id, the scope-ruling key and the alert match. So the
    escapes are undone once, here at the edge, instead of in every consumer.

    JSON first, then HTML: a title double-encoded as \\u0026amp; has to come
    apart in the order it was put together.
    """
    if "\\" in s:
        # The regex took a JSON string value out of the page without parsing
        # it, so \\u0026, \\/ and friends are still literal text. Re-parse just
        # that value. A lone backslash that is not an escape raises and leaves
        # the title exactly as found - a wrong title beats a dropped posting.
        try:
            s = json.loads(f'"{s}"')
        except ValueError:
            pass
    return _unescape(s)


def _unescape(s: str) -> str:
    """Entities out, whitespace collapsed - the half plain() and plain_html()
    share.

    unescape also turns &nbsp; into U+00A0, which the whitespace collapse folds
    into an ordinary space so two titles that look identical are.
    """
    return re.sub(r"\s+", " ", html_lib.unescape(s)).strip()


# Block-level tags become a line break before every tag is dropped; everything
# else is noise once the text is out.
_SCRIPTY = re.compile(r"<(script|style)[^>]*>.*?</\1\s*>", re.S | re.I)
# A COMMENTED-OUT JOB IS A JOB THE EMPLOYER TOOK DOWN, and it must not read as
# a live one. `<[^>]+>` eats `<!--<tr class="...">` as a single tag - from the
# `<` to the FIRST `>` - so the comment opener disappears and everything inside
# survives as page text. Pantonium's careers table renders one Operations
# opening and says "Positions Opening Soon!" under Sales & Marketing, with
# three Sales Director rows commented out below it; the scan read those, matched
# AE_PAT on "Sales Director", and the board published "Yes - AE-type role" for
# roles nobody can apply to. On an `html` board the page text IS the evidence,
# so it has to be the text a person actually sees.
#
# TERMINATED COMMENTS ONLY, deliberately. A browser hides everything after an
# unterminated `<!--` too, but a regex that drops to end-of-string on a stray
# opener would manufacture an empty board out of one typo - and a false "no
# openings" is the failure this file is most careful about. Scripts and styles
# are already gone above, which is where a bare `<!--` usually lives.
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLOCKY = re.compile(r"</?(?:p|div|br|li|ul|ol|tr|td|th|h[1-6]|section|article|"
                     r"blockquote|table|thead|tbody|header|footer|figure|hr)\b"
                     r"[^>]*/?>", re.I)


def plain_html(s: str) -> str:
    """A job description as a person reads it: no tags, entities resolved.

    Three things here are load-bearing, and the obvious shorter version gets
    each of them wrong.

    Greenhouse answers ?content=true with HTML that has been HTML-escaped once
    more - "&lt;p&gt;&amp;nbsp;&lt;/p&gt;". Unescaping once turns it back into
    markup, and only then is there anything to strip. A payload carrying no "<"
    of its own but plenty of "&lt;" is exactly that case and nothing else is,
    so the test is on the payload rather than on which board sent it.

    Block ends become newlines BEFORE the tags go, because a bullet list is
    where "Base salary: $140,000 - $180,000" usually sits, and closing the tags
    without a break glues that line onto the sentence above it - which is how a
    money regex ends up reading a requisition number as a salary.

    And the text is never truncated. Pay is stated at the BOTTOM of a job ad, so
    a length cap would manufacture "no salary stated" out of a long one, and an
    invented absence is worse here than a large string.
    """
    if not s:
        return ""
    if "<" not in s and "&lt;" in s:
        s = html_lib.unescape(s)
    s = _SCRIPTY.sub(" ", s)
    # BEFORE the block and tag passes, or `<!--` is eaten as a tag and the
    # dead markup inside the comment becomes page text. See _COMMENT.
    s = _COMMENT.sub(" ", s)
    s = _BLOCKY.sub("\n", s)
    s = _ANYTAG.sub(" ", s)
    lines = (_unescape(line) for line in s.split("\n"))
    return "\n".join(line for line in lines if line)


def plain_rows(rows: list[dict]) -> list[dict]:
    """Apply plain() to every title and location a fetcher returns.

    Public because build_board.py has one more source of rows - the Playwright
    fallback in render_fetch.py - that does not come through fetch().

    Also the single place jd and comp are guaranteed to exist, and the single
    place free text is parsed for pay. A fetcher for a board that publishes
    neither field just does not set them and gets "" and None here; that is a
    normal board, not an error. Doing the text parse once, here, is what keeps
    fifteen fetchers from each growing their own money regex.
    """
    for r in rows:
        r["title"] = plain(r.get("title") or "")
        r["location"] = plain(r.get("location") or "")
        r.setdefault("jd", "")
        r.setdefault("comp", None)
        r.pop("_detail_url", None)        # how a fetcher found the jd, not board data
        if r["comp"] is None and r["jd"] and not r.get("_jd_is_teaser"):
            r["comp"] = _text_comp(r["jd"], r)
    return rows


def _text_comp(jd: str, row: dict | None = None) -> dict | None:
    """Pay parsed out of the description, when the API stated none itself.

    Wrapped because a parser bug must not cost us a posting: the description is
    a nice-to-have and the job is the product. Falling back to None also keeps
    the honest reading - no salary found means not stated, never zero.

    WHERE THE ROW IS decides what a bare "$" is (salary.parse's `abroad`): on
    a row the board will list outside the US (_abroad) it names no currency,
    and a range resting on it alone is no pay stated - blind review 10,
    2026-11-23: an Esri Canada posting in Canada, its pay read from the ad's
    third part, was published as paying US dollars.
    """
    if salary is None:
        return None
    try:
        got = salary.parse(jd, abroad=_abroad(row) if row else False)
    except Exception:                     # noqa: BLE001 - see docstring
        return None
    if got:
        got.setdefault("source", "text")
    return got or None


# A country a board names for a row, read as build_board reads it for is_us.
_US_COUNTRY = ("us", "usa", "united states", "united states of america")


def _abroad(row: dict) -> bool:
    """Whether the board will list this row outside the US: build_board's
    is_us false, read the same way - roles.is_us of its location and title,
    and where they settle nothing, a country its board named (office_hint)
    that is not the US, 24 characters or fewer. A place that says nothing
    (no location, 'Remote') is not abroad."""
    import roles as _roles
    us = _roles.is_us(row.get("location") or "", (row.get("title") or "").strip())
    hint = row.get("office_hint")
    if us is None and isinstance(hint, dict) and hint.get("country"):
        k = str(hint["country"]).strip().lower().replace(".", "")
        if k in _US_COUNTRY:
            us = True
        elif len(k) <= 24:
            us = False
    return us is False


# --- conditional requests ---------------------------------------------------
#
# A crawl re-fetches 1,140 boards in full every run, and most of them have not
# changed since the last one. HTTP has had an answer to this since 1997: keep
# the ETag or Last-Modified the server handed us, send it back, and let the
# server say 304 Not Modified with no body at all.
#
# WHAT THIS DOES AND DOES NOT CLAIM. It saves bandwidth, both ours and theirs,
# and a 304 is the cheapest possible request. It does NOT skip parsing and it
# does NOT reuse last run's postings - that is the `--reuse-postings` idea
# CLAUDE.md describes, and that file is explicit that it wants a careful pass
# with the owner rather than a quick one, because the postings flow through the
# same loop that builds `orgs` and a bug there corrupts what the public site
# reads. So this stays underneath the fetchers: a 304 hands back the stored
# body and every fetcher above parses it exactly as it always did, none of them
# aware anything happened.
#
# ONLY WHEN THE SERVER OFFERS A VALIDATOR. No ETag and no Last-Modified means
# nothing is stored, which keeps the cache to the hosts that actually support
# this and stops it growing without bound.
#
# IN CI THIS IS INERT unless the workflow restores the directory between runs -
# a GitHub runner is a fresh machine every time. Locally it works immediately.
HTTP_CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "http_cache"
# Domains whose answers are never written to the cache, whatever headers they
# send - the domain and every host under it. Hirebridge's feed carries
# internal requisition data beside the public ad (see fetch_hirebridge); it
# sends no ETag today, and if it ever starts to, or answers from another of
# its hosts, that is still not a reason to keep a copy of it. A Jobvite
# account's XML feed names each job's hiring manager, a person, on every row
# (fetch_jobvitefeed): its host is here for the same reason. So is JobScore's
# domain: a board's feed names its hiring team - hiring managers and
# recruiters, with LinkedIn addresses and photos - on its entries, and a
# posting page prints the recruiter's name (fetch_jobscore). The domain and
# every host under it: careers.jobscore.com, where the feed and the postings
# are, and www.jobscore.com, which serves the same feed as its self link.
# So is ClearCompany's career-site API (fetch_clearcompany): an e-mail-to-apply
# posting's applyLink there is an e-mail address, often a person's, and the
# same host serves each posting's hiring team, names and photos.
NEVER_CACHED = frozenset({"hirebridge.com", "app.jobvite.com", "jobscore.com",
                          "careers-api.clearcompany.com"})


def never_cached(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in NEVER_CACHED)


class _Cached:
    """Enough of a requests.Response for the five things anybody reads.

    `.url` is one of them. find_websites.probe reads it off every _get result
    to record which candidate domain identified the company, and a cached 304
    that lacked it crashed the whole run on the first company whose homepage
    had not changed since the last probe.
    """

    def __init__(self, text: str, url: str = "", headers=None):
        self.text = text
        self.url = url
        self.status_code = 200
        self.from_cache = True
        # what the server said about the page that a reader asks of it, kept
        # with the body (KEPT_HEADERS) and laid under what the 304 itself said
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})

    def json(self):
        return json.loads(self.text)


def _cache_paths(url: str):
    key = hashlib.sha256(url.encode()).hexdigest()[:40]
    return HTTP_CACHE / f"{key}.json", HTTP_CACHE / f"{key}.body.gz"


def _cache_read(url: str):
    meta_p, _ = _cache_paths(url)
    try:
        return json.loads(meta_p.read_text())
    except Exception:                          # noqa: BLE001 - a cache miss
        return None


def _cache_body(url: str) -> str | None:
    _, body_p = _cache_paths(url)
    try:
        return gzip.decompress(body_p.read_bytes()).decode("utf-8", "replace")
    except Exception:                          # noqa: BLE001
        return None


# The headers a reader reads off a page, kept with its body: a 304 replays the
# body, and without them an unchanged answer is not the answer it was.
# Teamtailor's frame-ancestors is how an empty feed on a domain of the
# company's own shows it is Teamtailor's (2026-11-22: Heimdall Power's read
# as "not known" on its second read, from the cache, and as a zero on its
# first).
KEPT_HEADERS = ("Content-Security-Policy",)


def _cache_write(url: str, resp) -> None:
    etag = resp.headers.get("ETag")
    lastmod = resp.headers.get("Last-Modified")
    if not etag and not lastmod:
        return                                 # nothing to revalidate with
    if never_cached(url):
        return                                 # see NEVER_CACHED
    try:
        HTTP_CACHE.mkdir(parents=True, exist_ok=True)
        meta_p, body_p = _cache_paths(url)
        body_p.write_bytes(gzip.compress(resp.text.encode("utf-8"), 6))
        kept = {k: resp.headers.get(k) for k in KEPT_HEADERS if resp.headers.get(k)}
        meta_p.write_text(json.dumps(
            {"url": url, "etag": etag, "last_modified": lastmod, "headers": kept}))
    except OSError:
        pass                                   # a cache we cannot write is not an error


# --- one request at a time, per host ---------------------------------------
#
# WHY THIS EXISTS. Nothing paced this crawler. It fetches 1,140 boards and,
# since the paging fixes, makes roughly 1,570 requests - as fast as the network
# allows, at servers nobody here owns. 79 companies already sit behind a bot
# wall that refuses identified crawlers on the first request; those were never
# earned, but the way to earn more is to arrive in a burst.
#
# PER HOST, NOT GLOBAL, because that is where the load actually lands. 866 of
# these are separate company websites hit once each and the gate never fires
# for them. It fires exactly where we hammer: 65 companies share
# boards-api.greenhouse.io, a Workday tenant now serves ten pages of one
# search, an iCIMS portal eleven.
#
# THE LOCK IS HELD ACROSS THE SLEEP, and that is the whole design. build_board
# runs a ThreadPoolExecutor, so releasing before sleeping would let two workers
# both read a stale timestamp, both decide they may go, and both hit the host
# together - which is the burst this is meant to prevent. Holding it queues
# same-host callers behind each other and leaves different hosts free to run in
# parallel, which is the behaviour we want on both sides.
#
# The stamp is taken BEFORE the request rather than after, so the interval
# means "requests started at least HOST_PAUSE apart" - a slow response does not
# then buy the next caller a free pass.
HOST_PAUSE = float(os.environ.get("GOVTECH_DOCK_HOST_PAUSE", "0.5"))
_HOST_LAST: dict[str, float] = {}
_HOST_NOT_BEFORE: dict[str, float] = {}     # a 429's Retry-After, per host
_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_TABLE = threading.Lock()


# ONE SERVICE, ONE GATE (2026-11-16). Every Workday tenant is a host of its
# own (motorolasolutions.wd5.myworkdayjobs.com, badgermeter.wd5...), so the
# per-host gate never paced Workday at all: its 42 boards went out together
# from build_board's ten workers, and on 2026-11-16 Workday answered 39 of
# them "429 rate limited" in both crawls of the day - 24% of the board, and
# the sanity gate refused to publish it twice. The same tenant read from here
# alone answered at once. Workday's limit is on the caller, not the tenant,
# so its tenants share one gate (and one backoff). Every host not named here
# keeps its own gate.
_SHARED_GATES = ((re.compile(r"(^|\.)(myworkdayjobs|myworkdaysite)\.com$"), "workday"),
                 # (one gate however the address spells its host - a port, a
                 # user part - so the pace below cannot be stepped round)
                 (re.compile(r"^app\.jobvite\.com$"), "app.jobvite.com"))
# A 429 that names no Retry-After still backs its gate off this long: a
# server that says only "too many" has said to wait, and asking again at once
# is the burst that earned the 429.
RATE_LIMIT_PAUSE = float(os.environ.get("GOVTECH_DOCK_429_PAUSE", "15"))
# A HOST THAT HAS SAID HOW OFTEN IT MAY BE ASKED is asked no oftener than
# that, beside HOST_PAUSE (2026-11-23). app.jobvite.com answered a second
# feed request a second after the first with 429 and Retry-After 30 (the
# research of 2026-11-20), so its requests start JOBVITE_FEED_PAUSE apart:
# fetch_jobvitefeed's one request a board, and its identity probe's.
JOBVITE_FEED_PAUSE = 30.0
_GATE_PAUSES = {"app.jobvite.com": JOBVITE_FEED_PAUSE}


def _gate_key(url: str) -> str:
    """The gate a request queues at: its host, or the service it shares."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    name = host.rsplit("@", 1)[-1].split(":")[0]
    for rx, key in _SHARED_GATES:
        if rx.search(name):
            return key
    return host


def _host_gate(url: str) -> None:
    host = _gate_key(url)
    if not host:
        return
    # A HOST'S OWN PACE AND A 429'S HOLD ARE KEPT WHATEVER HOST_PAUSE IS: the
    # gate is off only where all three are nothing (blind review 12,
    # 2026-11-23: GOVTECH_DOCK_HOST_PAUSE=0 returned before either was read,
    # and two feed requests went to app.jobvite.com back to back)
    pause = max(HOST_PAUSE, _GATE_PAUSES.get(host, 0.0))
    with _HOST_TABLE:                       # only guards the dicts, never a sleep
        if pause <= 0 and _HOST_NOT_BEFORE.get(host, 0.0) <= time.monotonic():
            return
        lock = _HOST_LOCKS.setdefault(host, threading.Lock())
    with lock:
        now = time.monotonic()
        last = _HOST_LAST.get(host)
        wait = pause - (now - last) if last is not None else 0.0
        # A SERVER'S OWN INSTRUCTION OUTRANKS OUR INTERVAL. The first version
        # folded a 429's Retry-After into _HOST_LAST as a stamp in the future,
        # written outside this lock - so a same-host worker already asleep
        # here woke, stamped "now" over it, and the backoff was gone before
        # anybody honoured it. A separate not-before time, read here and
        # written under the table lock, cannot be overwritten by a waker.
        with _HOST_TABLE:
            hold = _HOST_NOT_BEFORE.get(host, 0.0) - now
        wait = max(wait, hold)
        if wait > 0:
            time.sleep(wait)
        _HOST_LAST[host] = time.monotonic()


def _back_off(url: str, resp) -> None:
    """A 429 is an instruction. Record it where the gate will read it."""
    wait = _retry_after(resp.headers.get("Retry-After"))
    hold = wait or RATE_LIMIT_PAUSE
    if hold:
        host = _gate_key(url)
        with _HOST_TABLE:
            _HOST_LOCKS.setdefault(host, threading.Lock())
            _HOST_NOT_BEFORE[host] = max(_HOST_NOT_BEFORE.get(host, 0.0),
                                         time.monotonic() + min(hold, 300))
    raise RateLimited(url, wait)


def _retry_after(raw: str | None) -> float | None:
    """Seconds from a Retry-After header, which comes in two spellings.

    RFC 9110 allows either a delay in seconds or an HTTP date. Both appear in
    the wild; a parser that reads only the integer form silently treats the
    date form as no header at all, which is how a server's explicit
    instruction gets ignored.
    """
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(raw)
        if when is None:
            return None
        now = dt.datetime.now(when.tzinfo)
        return max(0.0, (when - now).total_seconds())
    except Exception:                                  # noqa: BLE001
        return None


REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 10


def ask_robots(url: str) -> None:
    """Raise unless robots.txt lets this crawler read `url`."""
    v = robots.verdict(url)
    if v.ok:
        return
    if v.kind == "network":
        raise AtsError(v.why) from v.error        # "network error: ..." - retried
    raise RobotsRefused(v.why, v.kind, v.error)


def follow(url: str, one, *, hops: int = MAX_HOPS, may=None):
    """Fetch `url`, following redirects HERE so every hop asks robots.txt.

    `one(u)` makes exactly one request with redirects off. A library that
    follows redirects for us asks only the first host: a careers page that
    301s to an iCIMS portal saying "Disallow: /" was read through the
    company's own permission (blind review, 2026-10-09).

    `may(u)`, when given, is asked of every hop BEFORE anything is sent -
    its robots.txt included - and raises to refuse it. A hop off a board's
    own host is refused there, so the host a redirect names is never asked
    for anything (blind review, 2026-11-21: the refusal used to come after
    that host's robots.txt had been fetched).
    """
    for _ in range(hops + 1):
        # an address read as a browser reads it: a tab or a line break inside
        # an href is dropped (WHATWG URL), a backslash in an http(s) path is
        # a slash, and anything else unplain refused
        url = _TAB_OR_BREAK.sub("", url) if isinstance(url, str) else url
        url = _browser_path(url)
        plain_address(url)
        trinethire_not_career(url)
        if may is not None:
            may(url)
        ask_robots(url)
        resp = one(url)
        # (a stand-in response with no status is not a redirect either)
        loc = (getattr(resp, "headers", None) or {}).get("Location") \
            if getattr(resp, "status_code", 200) in REDIRECTS else None
        if not loc:
            return resp
        try:
            resp.close()
        except Exception:                          # noqa: BLE001
            pass
        # A REDIRECT TO NO ADDRESS IS THAT REQUEST FAILING, NOT THE READER
        # CRASHING ('https://[' raised ValueError out of every reader: blind
        # review 11, 2026-11-23)
        try:
            url = urllib.parse.urljoin(url, loc)
        except ValueError as exc:
            raise AtsError(f"{url} redirects to {str(loc)[:120]!r}, which is no address ({exc})") from exc
    raise AtsError(f"more than {hops} redirects from {url}")


_UNPLAIN = re.compile(r"[\x00-\x1f\x7f]")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def lower_same(s: str) -> str:
    """`s` lowercased with its length kept, so a place found in the copy is
    the same place in `s`. Python lowers 'I' with a dot above (U+0130) to
    two characters, and every cut after one landed a character late: a row
    was lost, a taken-down row published, a JSON-LD block cut short
    (seventh blind review, 2026-11-22; 'Istanbul' with that letter is on the
    board). It is lowered as 'I' is; should any other character ever do the
    same, only ASCII is lowered."""
    low = s.replace("\u0130", "I").lower()
    return low if len(low) == len(s) else s.translate(_ASCII_LOWER)
_TAB_OR_BREAK = re.compile(r"[\t\n\r]")


def _browser_path(u):
    """`u` with each backslash in an http(s) address's PATH read as a slash,
    as a browser reads it (WHATWG URL) and a server that resolves backslashes
    serves it. The library would send '/jobs\\..\\private/x' as
    '/jobs%5C..%5Cprivate/x', robots.txt was asked about it as written, and
    the page served was /private/x (eighth blind review, 2026-11-22): read so
    in follow(), the path robots.txt and plain_address are asked about is the
    path sent. The host part is left as it is - a backslash there names
    another host, which plain_address refuses - and so is the query."""
    if not isinstance(u, str) or "\\" not in u:
        return u
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return u                                 # (plain_address says why)
    if parts.scheme not in ("http", "https") or "\\" not in parts.path:
        return u
    cut = min([k for k in (u.find("?"), u.find("#")) if k >= 0] or [len(u)])
    head = u[:cut]
    if not head.endswith(parts.path):
        return u
    at = len(head) - len(parts.path)
    return head[:at] + parts.path.replace("\\", "/") + u[cut:]


def plain_address(u: str) -> None:
    """Raise unless `u` names its host the same way to every reader of it.

    THE HOST A RULE CHECKS MUST BE THE HOST THE REQUEST GOES TO (fifth blind
    review, 2026-11-22). 'https://evil.example\\@jobs.jobvite.com/x' is
    jobs.jobvite.com to urllib.parse - which every host rule, the board
    readers' path rules and robots.txt ask - and evil.example to the library
    that sends it, so a redirect steered a board's reader and its probe to a
    page nobody had asked about. A crawler sends no credentials, so an '@'
    in the host part is refused; so is a control character anywhere, a path
    that climbs ('.' or '..', as written or as sent), and any address whose
    prepared request names another host or port than the one read here.
    Asked of every hop in follow(), before anything is sent."""
    if not isinstance(u, str) or _UNPLAIN.search(u):
        raise AtsError(f"{u!r} is not a plain address (a control character)")
    try:
        parts = urllib.parse.urlsplit(u)
        parts.hostname                             # (an unclosed '[' raises here or above)
    except ValueError as exc:
        raise AtsError(f"{u[:160]!r} is not an address ({exc})") from exc
    # (a backslash anywhere is left to the comparison below: in the host part
    # it makes the prepared request name another host, and after the host -
    # in a path or a query - it steers none)
    # (a backslash in the host part makes the host or the port the prepared
    # request names differ from the one read here, which is refused below)
    if "@" in (parts.netloc or ""):
        raise AtsError(f"{u} is not a plain address (an '@' in its host part)")
    here = (parts.hostname or "").lower().rstrip(".")
    try:
        prepared = urllib.parse.urlsplit(requests.Request("GET", u).prepare().url)
        sent = (prepared.hostname or "").lower().rstrip(".")
        # THE PORT AND THE PATH TOO (seventh blind review): ':44\\3' left the
        # host alone and sent the request to port 44, and '%2e%2e' was asked
        # of robots.txt as written and sent as '..', which a server resolves.
        # A RAW '..' TOO (eighth blind review): the library resolves it before
        # it prepares the request, so the prepared path never shows it - and
        # '/jobs/../private/x' was asked of robots.txt as written and sent as
        # '/private/x'. So the path as written is read as a server may read
        # it (robots.climbs: '%2e' a dot; a backslash, '%2f' and '%5c' a slash)
        default = {"http": 80, "https": 443}
        if (parts.port or default.get(parts.scheme)) != (prepared.port or default.get(prepared.scheme)):
            raise AtsError(f"{u} is not a plain address: its port reads otherwise when sent")
        if robots.climbs(parts.path) or any(seg in (".", "..") for seg in prepared.path.split("/")):
            raise AtsError(f"{u} is not a plain address: its path climbs ('.' or '..'), so the page "
                           f"served is not the path asked about")
        # a name in another script, encoded the way the request itself is
        # (UTS 46, as requests does: 'stra\u00dfe' is xn--strae-oqa, where
        # Python's own codec would say 'strasse' - sixth blind review)
        want = here if here.isascii() else \
            requests.models.PreparedRequest._get_idna_encoded_host(here).lower()
    except AtsError:
        raise
    except Exception as exc:                       # noqa: BLE001 - nothing is sent
        raise AtsError(f"{u} is not an address the library can send ({exc})") from exc
    if sent != want:
        raise AtsError(f"{u} is not a plain address: its host reads as {here!r} here and "
                       f"{sent!r} when sent")


def _one(url: str, **kw):
    """One request, no redirects followed, paced and named like _get."""
    _host_gate(url)
    try:
        return requests.get(url, headers=UA, timeout=TIMEOUT,
                            allow_redirects=False, **kw)
    except requests.RequestException as exc:
        raise AtsError(f"network error: {exc}") from exc


def resolve(url: str) -> str:
    """Where `url` lands, every hop asked first, no body read. For a browser
    that is about to open it: it follows redirects without asking anybody."""
    resp = follow(url, lambda u: _one(u, stream=True))
    resp.close()
    return str(resp.url or url)


def _page(url: str, *, same_host: bool = False, may=None, **kw):
    """_get for a PAGE, which asks the site's robots.txt first, at every
    redirect hop (owner's ruling, 2026-10-09; scripts/robots.py). The boards'
    JSON feeds go through _get directly: they are interfaces published for
    programs.

    `same_host`: and never leave the host that was asked. A board's own
    address that redirects somewhere else is not the board - a dead Jobvite
    slug lands on the vendor's support page, a dead JazzHR one on its
    marketing site - and a vendor's page about itself is not a list of
    anybody's jobs. verify_boards._board_page holds the probes to the same
    rule. The hop is refused BEFORE it is asked for."""
    home = (urllib.parse.urlsplit(url).hostname or "").lower()

    def stay(u: str):
        there = (urllib.parse.urlsplit(u).hostname or "").lower()
        if same_host and there != home:
            raise AtsError(f"{url} leads off to {there}, which is not the board")
        if may is not None:
            may(u)                    # a reader's own rule for every hop (Jobvite's path)
    return follow(url, lambda u: _get(u, allow_redirects=False, **kw),
                  may=stay if (same_host or may is not None) else None)


def _get(url: str, *, fresh: bool = False, **kw):
    """`fresh`: ask without the cache's validators, for an answer whose headers
    are wanted and which the cache replayed without them."""
    _host_gate(url)
    headers = dict(UA)
    meta = _cache_read(url) if HTTP_CACHE and not fresh else None
    if meta:
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise AtsError(f"network error: {exc}") from exc
    if resp.status_code == 304:
        body = _cache_body(url)
        if body is not None:
            said = requests.structures.CaseInsensitiveDict((meta or {}).get("headers") or {})
            said.update(getattr(resp, "headers", None) or {})
            return _Cached(body, url, headers=said)
        # The server says nothing changed and we no longer hold the body. That
        # is our bookkeeping failing, not the site refusing, so ask again
        # plainly rather than reporting a board we cannot read - and ask
        # through the gate, because it is a second request at the same host.
        _host_gate(url)
        resp = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    if resp.status_code == 429:
        # HONOUR IT, not just report it. Per host, so one rude server does
        # not slow down 1,139 innocent ones.
        _back_off(url, resp)
    # a redirect, handed back to follow() when the caller asked to see them
    if (kw.get("allow_redirects") is False and resp.status_code in REDIRECTS
            and resp.headers.get("Location")):
        return resp
    if resp.status_code != 200:
        raise AtsError(f"HTTP {resp.status_code} for {url}")
    _fix_encoding(resp)
    if HTTP_CACHE:
        _cache_write(url, resp)
    return resp


def best_charset(raw: bytes, ctype: str, declared: str | None) -> str:
    """Which charset a text/* body is really in. ONE RULE, ONE PLACE.

    A body served without a charset in its Content-Type is UTF-8 if it
    decodes as UTF-8. requests follows the 1999 RFC and calls it Latin-1,
    which turns every curly quote and trademark sign into two or three
    glued characters. A DECLARED charset is always believed - guessing over
    a server that told us the answer is how you break the pages that were
    working. Callers that stream raw bytes use this directly; those that
    read resp.text get it through _fix_encoding.
    """
    if "charset" in (ctype or "").lower():
        return declared or "utf-8"
    try:
        raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return declared or "utf-8"
    return "utf-8"


def _fix_encoding(resp) -> None:
    """A text/* body with no charset in its header is UTF-8 if it decodes as
    UTF-8. requests follows the 1999 RFC and calls it Latin-1, which turns
    every curly apostrophe on such a page into three characters glued to
    the word before it. Eleven of the first 157 company sites shipped that
    way, and the write-up door refused true customer names on them. Fixed
    here so the cache and everything downstream see the page as served."""
    ctype = (resp.headers or {}).get("content-type", "") if hasattr(resp, "headers") else ""
    try:
        raw = resp.content
    except Exception:
        return
    resp.encoding = best_charset(raw, ctype, resp.encoding)


def _post_json(url: str, body: dict | list) -> dict | list:
    _host_gate(url)
    try:
        resp = requests.post(url, json=body, headers={**UA, "Content-Type": "application/json"},
                             timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise AtsError(f"network error: {exc}") from exc
    if resp.status_code == 429:
        _back_off(url, resp)             # Workday pages ten deep; it will say so
    if resp.status_code != 200:
        raise AtsError(f"HTTP {resp.status_code} for {url}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AtsError("non-JSON response") from exc


def _json(resp: requests.Response) -> dict | list:
    try:
        return resp.json()
    except ValueError as exc:
        raise AtsError("non-JSON response") from exc


# ------------------------------------------------------------------ pay, per contract
#
# One assembly point for every board. Each ATS spells the same three facts -
# a range, a currency, a period - differently enough that fifteen ad-hoc
# readings would disagree about which of them was missing, and "missing" is the
# answer that matters most here.

_PERIODS = ("year", "month", "week", "day", "hour")


def _period(raw: str | None) -> str | None:
    """One of the contract's five periods, or None.

    Every board spells the interval its own way: Ashby "1 YEAR", schema.org
    "YEAR", Lever "per-year-salary", Recruitee "yearly", Breezy "/ year". None
    rather than a default of "year" is the whole point - an hourly rate filed
    as a yearly one is wrong by a factor of two thousand, and it would read as
    a plausible salary rather than as an obvious error.
    """
    s = (raw or "").lower()
    if not s:
        return None
    for p in _PERIODS:
        if p in s:                        # "1 year", "yearly", "per-year-salary"
            return p
    if "annual" in s or "annum" in s:
        return "year"
    if "daily" in s:                      # the one word that does not contain its period
        return "day"
    return None


def _money(v, cents: bool = False) -> int | None:
    """Whole currency units from whatever the API stored, or None.

    Greenhouse counts cents; everyone else counts units. A few boards type the
    figure into a text field, so a clean numeric string is accepted too -
    but only a clean one. Anything that is not plainly a number returns None,
    because a figure we could not read is not a figure of zero.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = re.sub(r"[,\s$]", "", v)
        if not re.fullmatch(r"\d+(?:\.\d+)?", s):
            return None
        v = float(s)
    if not isinstance(v, (int, float)):
        return None
    n = int(round(v / 100)) if cents else int(round(v))
    return n if n > 0 else None


def _raw_from(low: int | None, high: int | None,
              currency: str | None, period: str | None) -> str:
    """A checkable rendering of a pay field that arrived as numbers.

    The contract keeps `raw` so a person can go and check the figure. Where the
    board handed over integers instead of the string it printed, transcribing
    those integers IS the source text - nothing is added, rounded or guessed,
    and a reader can compare it against the API response directly.
    """
    span = "-".join(str(x) for x in (low, high) if x is not None)
    return " ".join(part for part in (span, (currency or "").upper() or None,
                                      f"per {period}" if period else None) if part)


def _comp(low, high, currency, period, raw: str = "") -> dict | None:
    """The contract's comp block, or None when the board stated no figure."""
    low, high = _money(low), _money(high)
    if low is None and high is None:
        return None
    if low is not None and high is not None and low > high:
        low, high = high, low             # a couple of boards fill them backwards
    return {"min": low, "max": high,
            "currency": (currency or "").strip().upper() or None,
            "period": period, "source": "ats",
            "raw": raw or _raw_from(low, high, currency, period)}


def _ats_text(raw: str) -> dict | None:
    """A pay string that came from a dedicated compensation field, not prose.

    Breezy prints "$160,000 - $200,000 / year" and Ashby "$140K - $200K". Those
    are the board's own pay field, so the figure is as trustworthy as a
    structured one and `source` stays "ats" - `source` records where the number
    came from, not how hard it was to read. Only the number-shaped part of the
    reading is shared with salary.py, because two money regexes in one repo is
    two answers to the same question.
    """
    if not raw or salary is None:
        return None
    # salary.py refuses a bare "$140K - $200K", and it is right to: in prose, a
    # dollar figure with no pay word beside it is more often a contract value
    # or a budget than a wage. A dedicated compensation field is not prose -
    # the field IS the pay word - so the label the parser looks for is supplied
    # on a second attempt. Only the label. Every figure still comes from the
    # board, and if the parser still declines, the answer stays "not stated".
    try:
        got = salary.parse(raw) or salary.parse(f"Compensation: {raw}")
    except Exception:                     # noqa: BLE001 - a parser bug is not a lost row
        return None
    if not got:
        return None
    got["source"] = "ats"
    got["raw"] = raw                      # the pay field verbatim, so it can be checked
    return got


# schema.org/JobPosting is what Breezy, JazzHR and iCIMS all put on the posting
# page and none of them put in the list. Same shape on all three, so it is read
# once here rather than three times badly.
_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def _schema_comp(node: dict) -> dict | None:
    b = node.get("baseSalary")
    if not isinstance(b, dict):
        return None
    value = b.get("value") if isinstance(b.get("value"), dict) else {}
    low, high = value.get("minValue"), value.get("maxValue")
    if low is None and high is None:
        # a single figure rather than a range; the contract allows min == max
        low = high = value.get("value")
    return _comp(low, high, b.get("currency") or node.get("salaryCurrency"),
                 _period(value.get("unitText")))


def _schema_posting(url: str) -> tuple[str, dict | None]:
    """Description and pay from a posting page's JobPosting block.

    Returns ("", None) for every failure, including a page that simply has no
    block. A description we could not read is not a posting we lose.
    """
    try:
        raw = _page(url).text
    except AtsError:
        return "", None
    for block in _LD.findall(raw):
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        for node in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(node, dict) or node.get("@type") != "JobPosting":
                continue
            return plain_html(node.get("description") or ""), _schema_comp(node)
    return "", None


def _details(rows: list[dict], read) -> list[dict]:
    """Fill jd/comp from one request per posting. Only ever called behind
    FETCH_DETAILS.

    Two rules. A pause between calls, because this turns one request per company
    into one per posting on an API that is not ours. And any failure at all is
    swallowed, because the row already holds its title, location and url, and
    those are the product - a description that would not load must never take
    the job listing down with it.
    """
    for i, row in enumerate(rows):
        if i:
            time.sleep(DETAIL_PAUSE)
        try:
            jd, comp = read(row)
        except Exception:                 # noqa: BLE001 - see docstring
            continue
        if jd:
            row["jd"] = jd
        if comp and not row.get("comp"):
            row["comp"] = comp
    return rows


# ---------------------------------------------------------------- structured APIs

JIBE_LIMIT = 100          # the API honours it; 86 was BigBear's whole board
JIBE_MAX_PAGES = 40       # 4,000 openings is far past anything on this map


def fetch_jibe(ref: str) -> list[dict]:
    """Jibe, the front end iCIMS Talent Cloud puts on a company careers site.

    THIS IS NOT WHAT fetch_icims READS. A classic iCIMS portal serves its list
    inside an iframe and `?in_iframe=1` unwraps it; a Jibe site answers that
    same parameter with a 143-byte script that bounces the browser back to the
    careers page, and serves an Angular shell at the portal URL - which is why
    BigBear.ai read as a JS wall with no board. The jobs are behind a plain
    JSON API on the careers host, and the apply links still point back at the
    iCIMS portal, so the two really are one system with two doors.

    `ref` is the careers host ("careers.bigbear.ai") or a full https URL to it.
    The API reports totalCount and honours limit, so one request takes the
    whole board; the limit is capped rather than trusted, and the count is
    checked against what arrived so a silently truncated page is not read as
    a shrinking board.
    """
    base = ref if ref.startswith("http") else f"https://{ref}"
    base = base.rstrip("/")
    if not base.endswith("/api/jobs"):
        base = f"{base}/api/jobs"
    data = _json(_get(f"{base}?page=1&limit={JIBE_LIMIT}"))
    jobs = data.get("jobs") or []
    total = data.get("totalCount")
    if isinstance(total, int) and total > len(jobs):
        # PAGED, not truncated. A board bigger than one page must not report
        # one page as the whole of it - that is a fall this fetcher would
        # invent, and the publish gate would be right to refuse it.
        page = 2
        while len(jobs) < total and page <= JIBE_MAX_PAGES:
            more = (_json(_get(f"{base}?page={page}&limit={JIBE_LIMIT}")).get("jobs") or [])
            if not more:
                break
            jobs += more
            page += 1
    out = []
    seen = set()
    for j in jobs:
        d = j.get("data") if isinstance(j, dict) else None
        if not isinstance(d, dict):
            continue
        url = d.get("apply_url") or ""
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": d.get("title", "") or "",
                    "location": d.get("full_location") or d.get("location_name") or "",
                    "url": url,
                    "jd": plain_html(d.get("description") or ""),
                    "posted": posted_date(d.get("create_date")),
                    "office_hint": office_hint(city=d.get("city"),
                                               region=d.get("state"),
                                               country=d.get("country"))})
    if not out:
        raise AtsError("no jobs parsed from Jibe API")
    return out


def fetch_ashby(slug: str) -> list[dict]:
    # includeCompensation=true is the same request with one more parameter, and
    # Ashby is the only board here that publishes a pay range as numbers on the
    # list endpoint. descriptionPlain comes back either way.
    url = (f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}"
           "?includeCompensation=true")
    data = _json(_get(url))
    jobs = data.get("jobs", [])
    return [{"title": j.get("title", ""), "location": j.get("location", "") or "",
             "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
             "jd": plain_html(j.get("descriptionPlain") or j.get("descriptionHtml") or ""),
             "posted": posted_date(j.get("publishedAt")),
             # workplaceType is explicit; isRemote is deliberately unused,
             # because False means not-remote and not onsite.
             "mode": work_mode(j.get("workplaceType")),
             "office_hint": _ashby_office(j),
             "comp": _ashby_comp(j)} for j in jobs]


def _ashby_office(j: dict) -> dict | None:
    """Ashby's schema.org postal address, named out rather than transposed.

    The first version mapped the keys with a comprehension that stripped the
    "address" prefix - addressLocality became `locality`, which is not what
    office_hint takes, and it raised on the first real row. Three named
    arguments cannot be wrong in a way that survives a read.
    """
    pa = (j.get("address") or {}).get("postalAddress") or {}
    if not pa:
        return None
    return office_hint(city=pa.get("addressLocality"),
                       region=pa.get("addressRegion"),
                       country=pa.get("addressCountry"))


def _ashby_comp(j: dict) -> dict | None:
    """Ashby's tiers, narrowed to the salary component.

    A tier bundles salary with equity and bonus, and each component carries its
    own min/max. Taking the first one would file "Offers Equity" (min None, max
    None) or an equity percentage as the pay range.
    """
    c = j.get("compensation") or {}
    if not c:
        return None
    # The employer can switch the range off on the public posting and Ashby
    # still returns the tier on this endpoint. Republishing a figure a company
    # chose not to show is not something a public board should do.
    if j.get("shouldDisplayCompensationOnJobPostings") is False:
        return None
    raw = (c.get("compensationTierSummary")
           or c.get("scrapeableCompensationSalarySummary") or "")
    parts = list(c.get("summaryComponents") or [])
    for tier in c.get("compensationTiers") or []:
        parts.extend(tier.get("components") or [])
    for part in parts:
        if (part.get("compensationType") or "").lower() != "salary":
            continue
        got = _comp(part.get("minValue"), part.get("maxValue"),
                    part.get("currencyCode"), _period(part.get("interval")),
                    raw or part.get("summary") or "")
        if got:
            return got
    # A tier summary with no salary component still names a figure a person can
    # check ("$140K - $200K"), and it is the board's own pay field.
    return _ats_text(raw)


# WHEN THE EMPLOYER SAYS THEY POSTED IT, which is not a date this project has
# ever held. Every posting on the board carries `first_seen`, and that is OUR
# date - the day our crawler first saw the row. On 2026-08-31 every one of the
# 4,442 postings had a first_seen inside thirteen days, so a requisition opened
# ten months ago and one opened yesterday looked identical to a reader. Ashby
# hands back publishedAt 2025-10-19 for a role this board dates to August.
#
# ONLY THE PUBLISH DATE, NEVER THE UPDATE DATE. Greenhouse's updated_at moves
# when anything is edited and Recruitee ships created_at, published_at and
# updated_at side by side. "Posted" and "last touched" are different claims
# about a job, and a board that shows the second under the first's label makes
# every edited req look new. Each fetcher below names one field and no
# fallback chain.
#
# AND NEVER OUR OWN DATE. A board that gives no publish date leaves this None
# and the column stays empty. Falling back to first_seen would print our
# crawler's history as the employer's, invisibly, on every row - the same
# class of error as reporting "no jobs" for a page we could not read.
_EPOCH_MS = re.compile(r"^\d{12,13}$")


def posted_date(raw) -> str | None:
    """An employer's stated publish date as YYYY-MM-DD, or None.

    Four wire formats across seven boards, all observed live rather than
    remembered: ISO8601 with an offset (Greenhouse), ISO8601 with a Z
    (Ashby, SmartRecruiters, Breezy), epoch milliseconds (Lever), and
    "YYYY-MM-DD HH:MM:SS UTC" (Recruitee). Workable sends a bare date.

    Anything it cannot read with certainty returns None. A date parser that
    guesses is how 01/02 becomes January the second in one row and the first
    of February in the next.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and _EPOCH_MS.match(raw)):
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return None
        # Milliseconds if it is thirteen digits, seconds if ten. Both appear in
        # the wild and a seconds value read as milliseconds lands in 1970.
        if n > 10_000_000_000:
            n //= 1000
        try:
            return dt.datetime.fromtimestamp(n, dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    t = raw.strip().replace(" UTC", "").replace("Z", "+00:00")
    t = t.replace(" ", "T", 1) if "T" not in t and " " in t else t
    try:
        return dt.datetime.fromisoformat(t).date().isoformat()
    except ValueError:
        pass
    try:                                  # a bare YYYY-MM-DD
        return dt.date.fromisoformat(t[:10]).isoformat()
    except ValueError:
        return None


# WHAT THE BOARD ITSELF SAYS ABOUT MODE AND PLACE, which is a better answer
# than anything a regex gets out of a location string. work_mode reads "not
# stated" on 79% of postings and office parses on 37%, and both of those
# numbers are about OUR reading, not about what employers published: four of
# the seven structured boards state the mode outright and hand back a
# structured address, in responses this project already downloads.
#
#   ashby      workplaceType "OnSite"/"Remote"/"Hybrid", and
#              address.postalAddress {addressLocality, addressRegion,
#              addressCountry} - region as a FULL NAME, "California"
#   lever      workplaceType "remote"/"hybrid"/"onsite" (lowercase), country
#              as an ISO-2 code
#   workable   telecommuting bool, plus city / state / country as full names
#   recruitee  remote and hybrid booleans, plus city / country / country_code
#
# Both helpers below return None rather than a guess. That is the whole point:
# a field this project fills from the employer's own statement is worth more
# than one it infers, and a field it cannot fill must stay empty.

# The mode words every one of those boards uses, lowercased. Anything not on
# this list is a value we have not seen and do not understand, and gets None.
_MODES = {"onsite": "onsite", "on-site": "onsite", "on site": "onsite",
          "inoffice": "onsite", "in-office": "onsite",
          "remote": "remote", "fullyremote": "remote", "fully remote": "remote",
          "hybrid": "hybrid"}


def work_mode(raw) -> str | None:
    """The board's own word for how a role is worked, or None.

    NEVER INFERRED FROM A NEGATIVE. Workable sends `telecommuting: false` and
    Ashby sends `isRemote: false`, and neither means onsite - it means NOT
    REMOTE, which is onsite or hybrid and the board did not say which.
    Reading either as "onsite" would publish a claim about somebody's job that
    their own posting does not make, which is the same species of error as
    reporting "no jobs" for a page we could not read. Callers pass the
    explicit field where one exists and nothing where it does not.
    """
    if raw is None or isinstance(raw, bool):
        return None
    key = str(raw).strip().lower().replace("_", "")
    return _MODES.get(key) or _MODES.get(key.replace(" ", ""))


def office_hint(city=None, region=None, country=None) -> dict | None:
    """A structured office from a board's own address fields, or None.

    A US STATE ONLY WHERE THE ADDRESS IS ACTUALLY IN THE US. The board's state
    pages and map are keyed on two-letter US codes, and these boards send full
    region names for everywhere on earth - Ashby sent "California" and
    Workable sent "England" in the same shape. Filing the second as a state is
    precisely the trap CITY_CASES pins, where "London, UK" and "Montreal, QB"
    put 24 postings in states that do not exist.

    So: the country must read as the United States before a region becomes a
    state, and the region must resolve through roles.STATE_NAMES, which knows
    California and does not know England. A non-US address still returns its
    city and country - that is true and useful - with no state on it.
    """
    import roles as _roles
    c = (str(city).strip() if city else "") or None
    r = (str(region).strip() if region else "") or None
    k = (str(country).strip() if country else "") or None
    if not (c or r or k):
        return None
    us = bool(k) and k.lower().replace(".", "") in (
        "us", "usa", "united states", "united states of america")
    state = None
    if us and r:
        rl = r.lower()
        state = (_roles.STATE_NAMES.get(rl)
                 or (r.upper() if r.upper() in _roles.US_CODES else None))
    out = {"city": c, "state": state, "country": k}
    return out if any(out.values()) else None


def fetch_greenhouse(slug: str) -> list[dict]:
    # ?content=true is the same endpoint and the same single request; without it
    # the payload simply omits `content`. Everything else is byte-identical, so
    # no posting id moves.
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _json(_get(url))
    return [{"title": j.get("title", ""),
             "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", ""),
             "jd": plain_html(j.get("content") or ""),
             # first_published, NOT updated_at - the latter moves on any edit.
             "posted": posted_date(j.get("first_published")),
             "comp": _greenhouse_comp(j)} for j in data.get("jobs", [])]


def _greenhouse_comp(j: dict) -> dict | None:
    """pay_input_ranges, which most Greenhouse boards here leave unfilled.

    Greenhouse counts cents in min_cents/max_cents and some payloads use plain
    min_value/max_value instead, so both are read and neither is assumed - a
    cents figure read as units is a hundredfold overstatement, which on a
    salary would look like a typo nobody catches.

    raw is left for _comp to transcribe rather than taking the range's `title`:
    that field holds a label like "Salary Range", which is not the figure and
    would give a reader nothing to check.
    """
    for rng in j.get("pay_input_ranges") or []:
        if "min_cents" in rng or "max_cents" in rng:
            low = _money(rng.get("min_cents"), cents=True)
            high = _money(rng.get("max_cents"), cents=True)
        else:
            low, high = rng.get("min_value"), rng.get("max_value")
        got = _comp(low, high, rng.get("currency_type") or rng.get("currency"),
                    _period(rng.get("interval") or rng.get("pay_period")))
        if got:
            return got
    return None


def fetch_lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _json(_get(url))
    if not isinstance(data, list):
        raise AtsError("unexpected lever payload")
    return [{"title": j.get("text", ""),
             "location": (j.get("categories") or {}).get("location", "") or "",
             "url": j.get("hostedUrl", ""),
             "jd": _lever_jd(j),
             # createdAt is epoch MILLISECONDS on this board.
             "posted": posted_date(j.get("createdAt")),
             "mode": work_mode(j.get("workplaceType")),
             # Lever sends an ISO-2 country and no city or region, so this can
             # place a role in a country and never in a state.
             "office_hint": office_hint(country=j.get("country")),
             "comp": _lever_comp(j)} for j in data]


def _lever_jd(j: dict) -> str:
    """Lever splits one job ad across five fields and no single one of them is
    the ad.

    descriptionPlain is the opening paragraph only - 555 characters on
    everbridge's AE req. The responsibilities and requirements are in `lists`,
    and the pay range, when there is one, is usually last, in `additionalPlain`.
    Reading only the description is how a Lever posting that states a salary
    comes back looking like it states none.

    Deduplicated by containment rather than equality because `description`
    embeds `opening` verbatim, so the two are unequal and still the same words.
    Description goes first for that reason: the superset arrives before the
    subset that would otherwise repeat inside it.
    """
    parts = [j.get("descriptionPlain") or j.get("descriptionBodyPlain") or "",
             j.get("openingPlain") or ""]
    for lst in j.get("lists") or []:
        parts.append(lst.get("text") or "")
        parts.append(lst.get("content") or "")
    parts.append(j.get("additionalPlain") or "")
    parts.append(j.get("salaryDescriptionPlain") or "")
    out: list[str] = []
    for part in parts:
        part = plain_html(part)
        if part and part not in "\n\n".join(out):
            out.append(part)
    return "\n\n".join(out)


def _lever_comp(j: dict) -> dict | None:
    r = j.get("salaryRange") or {}
    got = _comp(r.get("min"), r.get("max"), r.get("currency"),
                _period(r.get("interval")))
    if got:
        return got
    return _ats_text(plain_html(j.get("salaryDescriptionPlain")
                                or j.get("salaryDescription") or ""))


def fetch_workable(slug: str) -> list[dict]:
    # details=true is the same widget endpoint with one more parameter. Verified
    # against details=false on a live account: identical fields and identical
    # values, plus `description`. Workable publishes no pay field of its own, so
    # a Workable salary only ever comes out of the description text.
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    data = _json(_get(url))
    jobs = data.get("jobs", [])
    # ONE JOB, ONE ROW PER COUNTRY. Workable lists a job once for each
    # country it is open in, with the same address; with no city on either,
    # the location read as "" twice and two postings got one posting_id -
    # IRTH's Cloud Engineer in the US and in Canada broke the nightly build's
    # self-test (2026-11-01). Only the rows that would collide take their
    # state and country as the location; every other row keeps exactly the
    # location (and so the posting_id) it always had.
    seen = Counter((j.get("url", ""), j.get("city", "") or "") for j in jobs)

    def where(j):
        city = j.get("city", "") or ""
        if seen[(j.get("url", ""), city)] < 2:
            return city
        return ", ".join(x for x in (city, j.get("state") or "", j.get("country") or "") if x)
    return [{"title": j.get("title", ""), "location": where(j),
             "url": j.get("url", ""),
             "posted": posted_date(j.get("published_on")),
             # telecommuting True means remote. FALSE MEANS NOT REMOTE, which
             # is onsite or hybrid and this board did not say which, so it
             # asserts nothing.
             "mode": "remote" if j.get("telecommuting") is True else None,
             "office_hint": office_hint(j.get("city"), j.get("state"),
                                        j.get("country")),
             "jd": plain_html(j.get("description") or "")}
            for j in jobs]


def fetch_recruitee(slug: str) -> list[dict]:
    url = f"https://{slug}.recruitee.com/api/offers/"
    data = _json(_get(url))
    out = []
    for j in data.get("offers", []):
        # recruitee keeps the ad in two fields and the requirements half is
        # where "Salary range" tends to sit, so both are read.
        jd = [plain_html(j.get("description") or ""),
              plain_html(j.get("requirements") or "")]
        s = j.get("salary") or {}
        out.append({"title": j.get("title", ""), "location": j.get("location", "") or "",
                    "url": j.get("careers_url", ""),
                    # published_at, not created_at and not updated_at - this
                    # board ships all three and they mean different things.
                    "posted": posted_date(j.get("published_at")),
                    # Two booleans rather than a word. Both false says nothing
                    # - it is not a statement that the role is onsite.
                    "mode": ("remote" if j.get("remote") is True
                             else "hybrid" if j.get("hybrid") is True else None),
                    "office_hint": office_hint(j.get("city"),
                                               country=j.get("country")),
                    "jd": "\n\n".join(p for p in jd if p),
                    "comp": _comp(s.get("min"), s.get("max"), s.get("currency"),
                                  _period(s.get("period")))})
    return out


def fetch_breezy(slug: str) -> list[dict]:
    url = f"https://{slug}.breezy.hr/json"
    data = _json(_get(url))
    if not isinstance(data, list):
        raise AtsError("unexpected breezy payload")
    # Breezy prints the range in the list ("$160,000 - $200,000 / year"), so pay
    # is free here. Only the description costs a request, and the posting page
    # carries a schema.org block with both.
    out = [{"title": j.get("name", ""),
            "location": (j.get("location") or {}).get("name", "") or "",
            "url": j.get("url", ""),
            "posted": posted_date(j.get("published_date")),
            "comp": _ats_text(j.get("salary") or "")} for j in data]
    if FETCH_DETAILS:
        _details(out, lambda r: _schema_posting(r["url"]))
    return out


def fetch_bamboohr(slug: str) -> list[dict]:
    """https://<slug>.bamboohr.com/careers/list -> {"result": [...]}.
    Location arrives as parts (city/state/country), not one string.

    The list carries no description at all. /careers/<id>/detail carries both a
    description and a `compensation` field, one request per posting.
    """
    url = f"https://{slug}.bamboohr.com/careers/list"
    data = _json(_get(url))
    if not isinstance(data, dict) or "result" not in data:
        raise AtsError("unexpected bamboohr payload")
    out = []
    for j in data.get("result", []):
        # atsLocation FIRST, because `location` is {city: null, state: null}
        # on every row of every board checked and this read it alone. 48 of
        # the 266 bamboohr postings on the live board carry a completely empty
        # location string and 166 have no parsed office, while atsLocation
        # holds "Denver, Colorado, United States" one key over. An empty
        # location is not a role with no place - it is a place we did not
        # look for, and the map and every /s/<state> page paid for it.
        loc = j.get("atsLocation") or j.get("location") or {}
        parts = [loc.get(k) for k in ("city", "state", "country")]
        out.append({"title": j.get("jobOpeningName", ""),
                    "location": ", ".join(p for p in parts if p),
                    "office_hint": office_hint(loc.get("city"),
                                               loc.get("state"),
                                               loc.get("country")),
                    "url": f"https://{slug}.bamboohr.com/careers/{j.get('id', '')}",
                    "_detail_url": f"https://{slug}.bamboohr.com/careers/"
                                   f"{j.get('id', '')}/detail"})
    if FETCH_DETAILS:
        _details(out, lambda r: _bamboohr_detail(r["_detail_url"]))
    return out


def _bamboohr_detail(url: str) -> tuple[str, dict | None]:
    data = _json(_get(url))
    jo = ((data or {}).get("result") or {}).get("jobOpening") or {}
    jd = plain_html(jo.get("description") or "")
    pay = jo.get("compensation")
    # bamboohr keeps `compensation` on the detail record and leaves it null
    # unless the employer filled it in - null on every sample on file so far.
    # Both shapes degrade to None, so an unfamiliar one costs the structured
    # reading and falls through to the description text, never a wrong figure.
    if isinstance(pay, dict):
        return jd, _comp(pay.get("min"), pay.get("max"), pay.get("currency"),
                         _period(pay.get("period") or pay.get("interval")))
    return jd, _ats_text(pay if isinstance(pay, str) else "")


def fetch_smartrecruiters(slug: str) -> list[dict]:
    """The postings list carries no ad text and no pay; /postings/<id> carries
    the ad in named sections. One request per posting."""
    # PAGED. This used to ask for limit=100 once and read the reply, which is
    # SmartRecruiters' maximum page size and not the size of anybody's board:
    # Xplor answers totalFound=251 to the very same request whose `content`
    # holds 100. That is 151 postings the board had the right to and never
    # asked for, on one company. The reply also echoes `offset`, and past the
    # end returns an empty page rather than wrapping (verified 2026-09-01 on
    # Xplor: offset 300 and offset 900 both return zero rows), so this walk
    # terminates on its own. `totalFound` is stable across offsets here and
    # could serve as a bound, but is deliberately not used as one - Workday
    # taught this file that an advertised total is not a promise, and an empty
    # page is a fact.
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    rows = _paged(
        lambda off: _json(_get(f"{base}?limit={SR_PAGE}&offset={off}"))
                    .get("content", []),
        lambda j: j.get("id") or j.get("name", ""),
        page=SR_PAGE, max_pages=SR_MAX_PAGES, label=slug)
    out = []
    for j in rows:
        loc = j.get("location") or {}
        out.append({"title": j.get("name", ""),
                    "location": ", ".join(x for x in [loc.get("city"), loc.get("region")] if x),
                    "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}",
                    "posted": posted_date(j.get("releasedDate")),
                    # THE ADDRESS, NOT THE MODE. This board's location block
                    # also carries `remote` and `hybrid` booleans, and they
                    # are not read on purpose: Xplor's board sets remote true
                    # on 92 of 100 postings that every one of them gives a
                    # real city for ("Phoenix, AZ, United States"). Whatever
                    # that flag means to SmartRecruiters, it does not mean
                    # what work_mode means here, and publishing it would put
                    # 92 false "remote" labels on one company. A field we do
                    # not understand is a field we do not publish.
                    "office_hint": office_hint(loc.get("city"),
                                               loc.get("region"),
                                               loc.get("country")),
                    "_detail_url": j.get("ref") or
                                   f"https://api.smartrecruiters.com/v1/companies/"
                                   f"{slug}/postings/{j.get('id', '')}"})
    if FETCH_DETAILS:
        _details(out, lambda r: (_smartrecruiters_jd(r["_detail_url"]), None))
    return out


def _smartrecruiters_jd(url: str) -> str:
    sections = ((_json(_get(url)).get("jobAd") or {}).get("sections") or {})
    # Named order first so the ad reads the way the employer wrote it, then
    # anything else the tenant added, so a custom section is not silently lost.
    order = ["companyDescription", "jobDescription", "qualifications",
             "additionalInformation"]
    order += [k for k in sections if k not in order]
    out = []
    for key in order:
        sec = sections.get(key) or {}
        body = plain_html(sec.get("text") or "")
        if body:
            out.append("\n".join(x for x in (sec.get("title") or "", body) if x))
    return "\n\n".join(out)


# --- Workday paging ------------------------------------------------------
#
# Workday caps `limit` at 20. 50 and 100 both answer HTTP 400, so a bigger
# page is not available and the only way to the rest of a result set is the
# offset.
#
# PAST THE END IT WRAPS. Verified live 2026-09-01 against motorolasolutions,
# whose "account executive" search holds 161 postings: offsets 180, 200 and
# 300 each returned 20 rows byte-identical to offset 0. So the obvious stop
# condition - an empty page - NEVER ARRIVES, and a loop written that way
# re-requests page one against somebody else's server until something else
# breaks it. The stop is a page that contributes no path we have not already
# seen, which the wrap-around satisfies by construction.
#
# `total` is not used as a bound because it is only true on the first reply:
# the same walk reported total=161 at offset 0, total=0 at offsets 20 through
# 160 while returning 20 real rows each time, then 161 again once it wrapped.
WD_PAGE = 20
WD_MAX_PAGES = 60          # 1,200 postings for one search term at one company
SR_PAGE = 100              # SmartRecruiters caps limit at 100; 200 and 500 both return 100
SR_MAX_PAGES = 40          # 4,000 postings at one company
ICIMS_MAX_PAGES = 60       # iCIMS pages by NUMBER, ~20 a page, so 1,200


def _paged(fetch_page, key, page: int = WD_PAGE,
           max_pages: int = WD_MAX_PAGES, label: str = "") -> list:
    """Walk offset pages until one adds nothing new, then stop.

    `fetch_page(offset)` returns the raw rows at that offset; `key(row)` is
    the identity that decides whether a row has been seen. Rows come back in
    first-seen order with duplicates removed.

    TWO STOP CONDITIONS, because the boards do not agree on how a walk ends.
    SmartRecruiters returns an empty page past the end, which is the polite
    answer. Workday WRAPS to page one and would loop forever on that
    condition alone. So this stops on either an empty page or a page that
    adds nothing new, and the second covers the first - a fetcher that only
    checked for empty would hammer Workday until something else broke it.

    The ceiling is a backstop against a wrap we failed to detect, not a limit
    on real employers. If it fires it SAYS SO: a paginator that quietly
    returns the first N of something larger is the exact defect this function
    exists to remove, and re-introducing it one level up would be worse than
    the original.
    """
    out, seen = [], set()
    for i in range(max_pages):
        rows = fetch_page(i * page)
        if not rows:
            break
        # ONE PASS, ADDING AS IT GOES. A page that repeats a key inside itself
        # used to keep both copies - the filter compared against what earlier
        # pages had seen and nothing else. The docstring promised duplicates
        # removed; now it is true within a page as well as across them.
        fresh = []
        for r in rows:
            k = key(r)
            if k in seen:
                continue
            seen.add(k)
            fresh.append(r)
        if not fresh:
            break
        out.extend(fresh)
    else:
        print(f"  paging: hit the {max_pages}-page ceiling"
              f"{' on ' + label if label else ''} after {len(out)} row(s); "
              f"this result is TRUNCATED", file=sys.stderr)
    return out


# A Workday pod, the whole part: wd1, wd5, wd12, wd503.
WD_POD = re.compile(r"wd\d+")


def workday_board(ref) -> tuple[str, str, str]:
    """(tenant, jobs search, board page) for a Workday ref, on its own host.

    Workday keeps boards on two hosts, and a ref is three strings on both:

        [tenant, pod, site]   acme.wd12.myworkdayjobs.com/Careers
        [pod, tenant, site]   wd3.myworkdaysite.com/recruiting/modaxo/Tadera

    WHERE THE POD SITS is the only thing that tells them apart: the pod is
    whichever of the first two reads "wd" and digits, as
    discover_ats.identity_of reads it. Second place wins a tie, because
    [tenant, pod, site] is the order of every Workday ref on file (42 on
    2026-11-22). The pod is never guessable; it comes out of the URL found.
    Past the host the two are one service, /wday/cxs/<tenant>/<site>/jobs on
    either, so fetch_workday reads both by one set of rules.

    THE FIRST VERSION (2026-08-20) TOOK THE POD-FIRST HOST ONLY WHEN BOTH
    PARTS BEGAN "wd". The address it was written for, (wd3, modaxo, Tadera),
    was asked at wd3.modaxo.myworkdayjobs.com, which is nobody's. A
    tenant-first board whose tenant began "wd" (wdc.wd5...) went to the
    other host. The checks of that host skipped the routing or named their
    tenant "wdtenant" (check_a_workday_board_is_asked_at_its_own_host).
    """
    first, second, site = (str(x) for x in ref)
    if WD_POD.fullmatch(second) or not WD_POD.fullmatch(first):
        tenant, host, board = first, f"https://{first}.{second}.myworkdayjobs.com", site
    else:
        tenant, host, board = (second, f"https://{first}.myworkdaysite.com",
                               f"recruiting/{second}/{site}")
    return tenant, f"{host}/wday/cxs/{tenant}/{site}/jobs", f"{host}/en-US/{board}"


def fetch_workday(ref: list) -> list[dict]:
    """Workday, on either of its two board hosts (workday_board says which).

    Both are read by the rules measured on myworkdayjobs.com: the same two
    searches, the same request body, paged to the end, the ad read from the
    search's own prefix. Only the address differs. The myworkdaysite copy
    this replaced sent a body of its own and linked postings without the
    locale, and no ref ever reached it, so neither was measured. No board
    on file sits on that host (2026-11-22): the first one wired there is the
    first time it is read live.
    """
    tenant, api, board = workday_board(ref)
    out, seen = [], set()
    # Two targeted queries beat crawling everything; _paged then takes ALL of
    # each query rather than its first 20.
    for term in ("account executive", "sales"):
        rows = _paged(
            lambda off, t=term: _post_json(
                api, {"limit": WD_PAGE, "offset": off, "searchText": t}
            ).get("jobPostings", []),
            lambda j: j.get("externalPath", j.get("title", "")),
            page=WD_PAGE, max_pages=WD_MAX_PAGES, label=f"{tenant}/{term}")
        for j in rows:
            k = j.get("externalPath", j.get("title", ""))
            if k in seen:                      # the two terms overlap heavily
                continue
            seen.add(k)
            path = j.get("externalPath", "") or ""
            out.append({"title": j.get("title", ""),
                        "location": j.get("locationsText", "") or "",
                        "url": board + path,
                        "_detail_url": _workday_detail_url(api, path)})
    return _workday_details(out)


def _workday_detail_url(api: str, path: str) -> str:
    """The jobs endpoint minus "/jobs", plus the posting's externalPath.

    Both Workday hosts answer the ad on the same /wday/cxs/<tenant>/<site>
    prefix they answer the search on, which is why this is derived from `api`
    rather than rebuilt from the ref.
    """
    return (api[:-len("/jobs")] if api.endswith("/jobs") else api) + (path or "")


def _workday_details(rows: list[dict]) -> list[dict]:
    """The search response carries title, location and path and nothing else.

    Workday states pay inside the description prose ("The salary range for this
    position is...") rather than in a field, so a Workday range only ever
    reaches us through the text parser, and only when details are on.
    """
    if FETCH_DETAILS:
        _details(rows, lambda r: (_workday_jd(r["_detail_url"]), None))
    return rows


def _workday_jd(url: str) -> str:
    info = (_json(_get(url)) or {}).get("jobPostingInfo") or {}
    return plain_html(info.get("jobDescription") or "")


def workday_total(ref: list) -> int:
    """How many postings the WHOLE board holds, by Workday's own count.

    fetch_workday asks two sales searches and nothing else, so the rows it
    returns say nothing about the size of the board: a tenant with thousands
    of openings answers "sales" with a couple of hundred. A door that must
    leave the giants alone (wire_found.py, the owner's ruling of 2026-11-18)
    cannot learn that from the read, and Workday does state it: `total` on
    the FIRST reply to a search - the only reply it is true on, see the
    paging note above - and an empty search matches every posting.

    Measured 2026-11-18 on motorolasolutions: 868 by this count, on a day the
    two searches returned 385 rows. No board on file reads as 500 rows
    through the reader, so a giant was never going to be seen that way.

    One request, in the shape and at the address fetch_workday already uses
    (workday_board, so the two cannot disagree about a host). Raises AtsError
    when the board will not answer or states no total: "the size is unknown"
    is the caller's answer to give, not a zero made up here.
    """
    _, api, _ = workday_board(ref)
    data = _post_json(api, {"limit": WD_PAGE, "offset": 0, "searchText": ""})
    total = data.get("total") if isinstance(data, dict) else None
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise AtsError("Workday stated no total for this board")
    return total


def fetch_paylocity(ref: str) -> list[dict]:
    """Paylocity publishes no JSON API; the board ships its data in the page.

    The list lives in a window.pageData assignment, so the titles are exact
    rather than scraped out of markup.
    """
    resp = _page(ref)
    m = re.search(r"window\.pageData\s*=\s*(\{.*?\})\s*;", resp.text, re.S)
    if not m:
        raise AtsError("paylocity board carried no pageData block")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise AtsError(f"paylocity pageData did not parse: {exc}") from exc
    out = []
    for j in data.get("Jobs", []) or []:
        title = (j.get("JobTitle") or "").strip()
        if not title:
            continue
        out.append({"title": title,
                    "location": (j.get("LocationName") or j.get("Location") or "").strip(),
                    "url": j.get("JobUrl") or ref,
                    # Paylocity's Description is a ~110-character preview cut
                    # mid-word, and the posting page carries neither pageData
                    # nor a schema.org block to get the rest from. It ships as
                    # the description because it IS what the board gave us, but
                    # it is flagged so no money regex ever runs over it: a
                    # truncation turns "$140,0" into $140, a figure nobody
                    # advertised, and a made-up number is worse than no number.
                    "jd": plain_html(j.get("Description") or ""),
                    "_jd_is_teaser": True})
    return out


def fetch_oracle(ref: str) -> list[dict]:
    """Oracle Recruiting Cloud. `ref` is the host it was discovered on.

    UNVERIFIED for descriptions: no company on file uses this type, so there
    was nothing live to probe. The requisition list is documented to carry a
    ShortDescription on some tenants and not others, so it is read when present
    and left empty when not - which is the honest outcome either way. Nobody
    should read an empty Oracle jd as "this tenant publishes no descriptions"
    until one has actually been looked at.
    """
    host = ref if ref.startswith("http") else f"https://{ref}"
    api = (f"{host.rstrip('/')}/hcmRestApi/resources/latest/"
           "recruitingCEJobRequisitions?onlyData=true"
           "&finder=findReqs;siteNumber=CX_1")
    data = _json(_get(api))
    out = []
    items = (data or {}).get("items") or []
    for block in items:
        for j in block.get("requisitionList", []) or []:
            title = (j.get("Title") or "").strip()
            if not title:
                continue
            out.append({"title": title,
                        "location": (j.get("PrimaryLocation") or "").strip(),
                        "url": f"{host.rstrip('/')}/hcmUI/CandidateExperience/en/"
                               f"sites/CX_1/job/{j.get('Id', '')}",
                        "jd": plain_html(j.get("ShortDescription")
                                         or j.get("ExternalDescriptionStr") or "")})
    return out


# ------------------------------------------------- server-rendered HTML boards

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANYTAG = re.compile(r"<[^>]+>")


def _page_text(url: str) -> str:
    resp = _page(url)
    text = _TAG.sub(" ", resp.text)
    # A COMMENTED-OUT JOB IS NOT AN OPENING, and this is the extractor
    # that decides it. _ANYTAG below runs from a bare `<!--` to the next
    # `>`, which swallows a comment holding only block tags and leaks one
    # holding an <a href>: Pantonium's three commented-out Sales Director
    # rows each carry an "Apply now!" anchor, so AE_PAT matched and the
    # board published "Yes - AE-type role" for roles nobody can apply to.
    text = _COMMENT.sub(" ", text)
    text = _ANYTAG.sub(" ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text)


_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_rippling(slug: str) -> list[dict]:
    # Rippling boards are server-rendered; job titles appear in page text and in
    # embedded JSON. Try embedded JSON first, fall back to text scan.
    url = f"https://ats.rippling.com/{slug}/jobs"
    resp = _page(url)
    titles = re.findall(r'"name"\s*:\s*"([^"]{4,90})"\s*,\s*"[^"]*url', resp.text)
    if not titles:
        return [{"title": "", "location": "", "url": url, "_pagetext": _strip(resp.text)}]
    # The same __NEXT_DATA__ block those titles were cut out of also holds each
    # posting's own url, which is the only place a Rippling description lives.
    # It is attached as a private key rather than as the row's url on purpose:
    # posting ids are hashed from url + location, so promoting the per-posting
    # url would re-key every Rippling row on the board and break every saved
    # role and shared link pointing at one. Changing that is the owner's call,
    # not a side effect of adding descriptions.
    detail = _rippling_urls(resp.text)
    out = [{"title": t, "location": "", "url": url,
            "_detail_url": detail.get(plain(t), "")} for t in titles]
    if FETCH_DETAILS:
        _details([r for r in out if r["_detail_url"]],
                 lambda r: _rippling_detail(r["_detail_url"]))
    return out


def _rippling_urls(raw: str) -> dict[str, str]:
    """title -> posting url, from the board's embedded Next.js payload."""
    m = _NEXT_DATA.search(raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    props = ((data.get("props") or {}).get("pageProps") or {})
    found: dict[str, str] = {}
    for query in ((props.get("dehydratedState") or {}).get("queries") or []):
        body = (query.get("state") or {}).get("data")
        for item in (body or {}).get("items", []) if isinstance(body, dict) else []:
            if item.get("name") and item.get("url"):
                found[plain(item["name"])] = item["url"]
    return found


def _rippling_detail(url: str) -> tuple[str, dict | None]:
    m = _NEXT_DATA.search(_page(url).text)
    if not m:
        return "", None
    api = (((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {})
           .get("apiData") or {})
    post = api.get("jobPost") or {}
    body = post.get("description")
    if isinstance(body, dict):
        # rippling keeps the ad in named sections (company, role, requirements)
        jd = "\n\n".join(plain_html(v) for v in body.values() if isinstance(v, str))
    else:
        jd = plain_html(body if isinstance(body, str) else "")
    return jd.strip(), _rippling_comp(post.get("payRangeDetails")
                                      or api.get("payRangeDetails"))


def _rippling_comp(ranges) -> dict | None:
    """payRangeDetails, whose filled-in shape is UNVERIFIED.

    Every Rippling posting probed so far returns []. Rather than guess at key
    names, this reads only the ones that are unambiguous and returns None for
    anything else - in which case the description text parser still gets its
    turn. Neither path can invent a figure; the worst case is a range we had
    and did not use, which shows up as "not stated" and not as a wrong number.
    """
    for r in ranges or []:
        if not isinstance(r, dict):
            continue
        got = _comp(r.get("min") if "min" in r else r.get("minValue"),
                    r.get("max") if "max" in r else r.get("maxValue"),
                    r.get("currency") or r.get("currencyCode"),
                    _period(r.get("interval") or r.get("period")
                            or r.get("frequency")))
        if got:
            return got
    return None


def fetch_jazzhr(slug: str) -> list[dict]:
    # JazzHR hosted boards are server-rendered lists of <a> job links. The list
    # page carries only an Organization block; the ad and, when the employer set
    # one, a structured baseSalary are on the posting page.
    url = f"https://{slug}.applytojob.com/apply/"
    resp = _page(url)

    # MEASURE THE TITLE, NOT THE PADDING. The bound used to live inside the
    # pattern as {4,90}, which counted the anchor's RAW inner text - and
    # JazzHR indents its markup by 77 characters. That left thirteen for the
    # actual title, so anything longer than "Sales Manager" was thrown away
    # before anyone could strip it. Measured across all twelve boards on
    # 2026-09-01: 51 real postings, of which the old pattern matched 2. VGSI
    # alone advertises 31 and published none of them.
    links = re.findall(
        r'<a[^>]+href="(https?://[^"]*applytojob\.com/apply/[^"]+)"[^>]*>([^<]+)</a>',
        resp.text)
    out = []
    for u, raw in links:
        title = html_lib.unescape(raw).strip()
        if 4 <= len(title) <= 90:
            out.append({"title": title, "location": "", "url": u})

    # AN UNREADABLE BOARD IS NOT AN EMPTY ONE. This used to return [] whatever
    # happened, so a board we could not parse published as a company with "0
    # open roles" - a false absence, which this project treats as the one
    # error that never corrects itself. Three of these boards really do say
    # they have nothing open, and that is a different fact from silence: when
    # the page says so we report the zero, and when it does not we raise and
    # the company is recorded Unknown.
    if not out:
        page = resp.text.lower()
        if not any(s in page for s in ("no open positions", "there are no open",
                                       "no current openings", "no openings at")):
            raise AtsError("no jobs parsed from JazzHR board and the page does "
                           "not say it has none")

    if FETCH_DETAILS:
        _details(out, lambda r: _schema_posting(r["url"]))
    return out


def _strip(raw: str) -> str:
    text = _TAG.sub(" ", raw)
    text = _COMMENT.sub(" ", text)   # see _page_text; same leak, same fix
    text = _ANYTAG.sub(" ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text))


def fetch_icims(ref: str) -> list[dict]:
    """iCIMS portals serve a ~600-character shell at the plain URL and put the
    actual listing inside an iframe, which is why these boards read as empty
    JS walls. ?in_iframe=1 returns the real server-rendered list.

    ref is either a portal slug ("careers-granicus") or a full URL. Titles live
    in the anchor's title attribute as "<req id> - <title>".
    """
    base = ref if ref.startswith("http") else f"https://{ref}.icims.com/jobs/search?ss=1"
    joiner = "&" if "?" in base else "?"

    # PAGED TO THE END, AND KEYED ON THE LINK.
    #
    # This used to read three pages on the strength of a comment reading
    # "portals page at 50; 150 is plenty". Measured on 2026-09-01, Bruker's
    # portal serves NINETEEN on its first page and twenty thereafter: eleven
    # pages, 204 anchors, then an empty page. Three pages of that is 59, not
    # 150, and the board published 54. The premise was wrong by more than
    # double and the cap was written to it.
    #
    # It also deduped on the TITLE, after stripping the requisition number off
    # the front - which is the exact rule fetch_html_titles documents eighty
    # lines below as the one never to use ("DEDUP ON THE LINK, NOT THE TITLE").
    # iCIMS rows carry no location, so once the req id is gone two genuinely
    # different postings are byte-identical and the second is dropped. On
    # Bruker that is 204 distinct hrefs collapsing to 177 titles: 27 real
    # requisitions deleted, twelve of them called "Field Service Engineer".
    #
    # The old early break compounded both: `if not new` counted new TITLES, so
    # a page full of distinct reqs that happened to repeat a name ended the
    # crawl. _paged stops on a page that adds no new LINK, which is a fact
    # about the portal rather than about its naming.
    rows = _paged(
        lambda pr: re.findall(
            r'<a\s+href="([^"]+)"[^>]*class="iCIMS_Anchor"[^>]*title="([^"]+)"',
            _page(f"{base}{joiner}in_iframe=1&pr={pr}").text),
        lambda pair: html_lib.unescape(pair[0]),
        page=1, max_pages=ICIMS_MAX_PAGES, label=str(ref))

    out = []
    for href, raw in rows:
        title = re.sub(r"^\d+\s*-\s*", "", html_lib.unescape(raw)).strip()
        if title:
            out.append({"title": title, "location": "",
                        "url": html_lib.unescape(href)})
    if not out:
        raise AtsError("no jobs parsed from iCIMS portal")
    # The list markup carries titles and hrefs only. Each posting page carries a
    # schema.org JobPosting; iCIMS tenants rarely fill in its baseSalary, so an
    # iCIMS range almost always has to come out of the description text.
    if FETCH_DETAILS:
        _details(out, lambda r: _schema_posting(r["url"]))
    return out


# A careers page lists jobs as links. Anchor text plus a job-shaped href is
# enough to enumerate most of them, which is the difference between a company
# appearing on a job board and being invisible on it.
_ANCHOR = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
# "View job: Senior Support Specialist" - a link's accessible name, when its
# visible text is only a button label (see fetch_html_titles)
_ARIA_ANY = re.compile(r'\baria-label=["\']([^"\']{3,120})["\']', re.I)
_ARIA_JOB = re.compile(r'\baria-label=["\'](?:view|see|open|apply(?: for| to)?)\s+'
                       r'(?:this\s+)?(?:job|role|position|opening|posting)\s*[:\-\u2013\u2014]\s*'
                       r'([^"\']{3,90})["\']', re.I)
_JOB_HREF = re.compile(r"/(job|jobs|career|careers|position|opening|vacanc|"
                       r"apply|posting|req)[/\-_?=]|jobId|requisition", re.I)
_TITLEISH = re.compile(r"\b(engineer|developer|manager|director|analyst|specialist|"
                       r"executive|representative|coordinator|associate|lead|architect|"
                       r"designer|scientist|consultant|administrator|technician|"
                       r"supervisor|officer|president|counsel|accountant|recruiter|"
                       r"marketer|strategist|advocate|partner|intern|apprentice|"
                       r"operator|driver|installer|trainer|writer|editor|"
                       r"controller|planner|advisor|agent)\b", re.I)
# Navigation and marketing links that would otherwise pass as titles.
_HEADING = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.S | re.I)

_NAV = re.compile(r"^(apply|apply now|learn more|read more|view (all|jobs|openings)|"
                  r"see (all|more)|careers?|jobs?|open (roles|positions)|back|next|"
                  r"previous|home|about|contact|search|filter|all departments?|"
                  r"privacy|terms|cookie)s?$", re.I)

# A COUNT IS NEVER A JOB TITLE. _NAV is anchored, so it catches a link reading
# "Jobs" and cannot catch "Engineer jobs 555,845 open jobs" - which is what
# LinkedIn's browse-by-title rail says, and what fourteen of CORE Business
# Technologies' twenty postings on the public board actually were. They passed
# every test above: the href is a real /jobs/ URL and the text reads like a
# title, because it begins with one.
#
# The board's claim about itself is the reason this matters more than a
# cosmetic wrong row: the site tells visitors "nothing here is scraped from an
# aggregator", and these were LinkedIn's own furniture counted as somebody's
# openings.
_JOB_COUNT = re.compile(r"\b\d[\d,]*\s*\+?\s*(open\s+)?(jobs|positions|roles|"
                        r"openings|vacancies)\b", re.I)


# A CARD'S BUTTON IS NOT PART OF THE JOB'S NAME. Adobe's careers page puts
# "Apply Now" inside the same anchor as the title and gives the card no
# heading, so the flattening below produced nine postings called "Apply Now
# Account Manager, Channel Sales" - a title that is wrong on the public board,
# wrong in the posting id, wrong as an alert match and wrong as the key a scope
# ruling is stored under. It is the same defect as uveye's "More Details Less
# Details" tail and gets the same treatment: take the label off rather than get
# clever about the rest.
#
# ANCHORED AND LEADING ONLY. "Apply" appears legitimately inside real titles -
# "Application Engineer", "Applied Scientist" - so this matches a whole
# call-to-action at the very start of the string and nowhere else. The word
# boundary is what keeps "Applied Scientist" intact.
_CTA_LEAD = re.compile(
    r"^(?:apply\s+now|apply\s+today|apply|view\s+job|view\s+details|"
    r"job\s+details|see\s+details|learn\s+more|read\s+more)\b[\s:\u2013\u2014-]*",
    re.I)


# AND THE SAME LABEL ON THE OTHER END, which is where it turned out to be more
# common. Eleven postings on the board read "Account Executive, Fire Read More"
# and "Business Development Manager (Remote) Sales Sydney, Australia Apply now"
# - the second being a whole card flattened into a title, which is the uveye
# "More Details Less Details" case wearing different words.
#
# It matters more than tidiness: stripping the tail is what lets
# render_fetch.split_location find the location afterwards. With "Apply now"
# still attached the splitter reads it as the tail and gives up; without it,
# "Sydney, Australia" comes out as a location and the title shortens to match.
#
# CONSERVATIVE ON THIS SIDE. A leading "Apply" is unambiguous; a trailing bare
# "details" or "more" is not, and a title could plausibly end in either. So the
# tail rule takes only whole call-to-action PHRASES, and repeats them, because
# a flattened card can carry two ("More Details Less Details").
_CTA_TAIL = re.compile(
    r"[\s:\u2013\u2014-]*\b(?:apply\s+now|apply\s+today|read\s+more|"
    r"read\s+less|learn\s+more|see\s+more|see\s+details|view\s+job|"
    r"view\s+details|job\s+details|more\s+details|less\s+details|apply)\s*$",
    re.I)


def strip_cta(text: str) -> str:
    """A button label off either end of a flattened card, or the text unchanged.

    Refuses to empty the string: a card whose entire text IS the button label
    is not a job with a blank name, it is a link we should not have taken, and
    returning "" here would file it as one. Handing the label back unchanged
    lets the title tests below reject it as they already do.
    """
    out = _CTA_LEAD.sub("", text).strip()
    # repeat on the tail: "... More Details Less Details" is two labels
    for _ in range(3):
        nxt = _CTA_TAIL.sub("", out).strip()
        if nxt == out:
            break
        out = nxt
    return out or text


# A CARD IS SEVERAL LINES, NOT ONE SENTENCE. A board renders a listing as a
# stack of blocks - department, title, location, employment type, pay - and the
# flattening below used to run them together into a single string that was then
# filed as the job's name. Four Doorman postings reached the public board called
# "Full Stack Engineer New York, NY $120k - 145k", and thirty of Prepared's read
# "Network Engineer, Axon 911 New York, New York, United States".
#
# That is three faults in one string. The title is wrong on the page, in the
# posting id, in the scope-ruling key and in every alert match. The location
# field is left EMPTY - fetch_html_titles hard-coded "location": "" and returned
# it for all 681 rows it produced - so roles.geography() has nothing to read and
# the role appears on no map and no /s/<state> page. And the pay is inside the
# title rather than a comp field, where salary.py never looks.
#
# The blocks are still in the markup. _BLOCKY already knows which tags end a
# line, because plain_html() needs the same fact to keep a salary line off the
# sentence above it. Splitting on it here costs nothing and hands back the lines
# the board laid out.
# SPANS STACKED WITH NOTHING BETWEEN THEM ARE LINES TOO. _card_lines has always
# split on the markup's OWN newlines as well as on block tags, so a formatted
# page already comes apart correctly: Dossier writes its location and employment
# type as sibling spans on separate source lines and they arrive as two lines.
# A minifier removes exactly that newline. ease-health and k16 Solutions emit
# the same stacked structure with the tags touching, `</span><span`, and eleven
# postings came out named "Engineering Software Engineer Remote, U.S. -
# Full-time" and "Full-time - Remote Software Engineer View role". Treating the
# touching form as the formatted form is consistency, not a new rule.
#
# MEASURED: across every readable html board, thirteen cards split here at all -
# eleven corrected a title or recovered a desk, two changed nothing, none was
# made worse.
#
# THE ABSENT SPACE IS NOT MEASURED, and is a guard rather than a finding. A span
# is an inline tag, and "Senior <span>Engineer</span>" is one name; requiring the
# tags to touch keeps a spaced pair together. Dropping that requirement changes
# no row in the corpus, so nothing here proves it is needed - it is kept because
# the failure it prevents renames a job, and because it can only ever split
# less.
_SPAN_STACK = re.compile(r"</span\s*><span\b", re.I)


def _card_lines(inner: str) -> list[str]:
    """The lines a job card renders as, from the markup inside its anchor."""
    s = _SPAN_STACK.sub("</span>\n<span", inner)
    s = _SCRIPTY.sub(" ", s)
    s = _COMMENT.sub(" ", s)      # a commented-out card line is not a line
    s = _BLOCKY.sub("\n", s)
    s = _ANYTAG.sub(" ", s)
    return [line for line in (_unescape(x) for x in s.split("\n")) if line]


# A LOCATION LINE. Either a work mode stated on its own - "Remote - US",
# "Hybrid" - or a "Place, Place" address. The comma is what keeps ordinary card
# furniture out: "Full Time", "Sales", "View role" and "Engineering" all fail it.
_CARD_LOC = re.compile(r"^(?:[Rr]emote\b|[Hh]ybrid\b|[Oo]n-?site\b"
                       r"|[A-Z][A-Za-z .'-]*,\s*[A-Z][A-Za-z .]+)")

# A PAY LINE, and nothing that merely contains money. Anchored at both ends so a
# sentence with a dollar figure in it - a quota, a deal size, a book of business -
# cannot match; only a line that IS a range, which on a card is the pay field.
_PAY_CHIP = re.compile(
    r"^(?:[$£€]|USD|CAD|AUD|NZD|GBP|EUR)\s?[\d,.]+\s*[kK]?\s*(?:[-\u2013\u2014]|to)\s*"
    r"(?:[$£€]|USD|CAD|AUD|NZD|GBP|EUR)?\s?[\d,.]+\s*[kK]?"
    r"(?:\s*(?:/|per\s+)(?:hour|hr|year|yr|month|mo|week|wk|day))?$")


def card_fields(lines: list[str], flat: str) -> tuple[str, str, dict | None]:
    """(title, location, comp) from a card's lines, or the flat text unchanged.

    POSITION FIRST, PATTERN SECOND - the rule CLAUDE.md records the capture
    harvester learning, and the reason this reads the lines in order and only
    looks for a location among the ones AFTER the title. Testing the location
    pattern first stole the title whenever one looked like a place.

    The title is the first line that reads like a job, not flatly the first
    line, because boards stack a DEPARTMENT above it: Dossier's cards begin
    "Development & Product Management" and Beanstack's begin "Engineering
    Team". Taking line one there would name eleven postings after a department -
    and _TITLEISH would then reject every one of them, deleting the roles. The
    department chips do not contain a job word and the titles under them do, so
    the job word is what tells them apart.

    NEVER LOSE A ROLE TO AN OVER-EAGER SPLIT. When no single line reads like a
    job - the card is one block, or the job word straddles two lines - this
    hands back the flattened text exactly as before. A slightly long title beats
    a truncated one, and beats a dropped posting by much more.
    """
    # A CHIP IS NEVER THE TITLE: "Sales · Full-time · Executive" carries a job
    # word ("Executive") and was published as Cellebrite's VP Sales posting,
    # whose name line has none (review, 2026-10-08). With no other line that
    # reads like a job, the card's first line is its name - if it is not a
    # chip itself.
    def chip(line):
        return line.count("\u00b7") >= 2 or line.count(" | ") >= 2
    idx = next((i for i, line in enumerate(lines)
                if _TITLEISH.search(line) and not chip(line)), None)
    if idx is None and lines and not chip(lines[0]) and 6 <= len(lines[0]) <= 90 \
            and any(chip(line) for line in lines[1:]):
        idx = 0
    if idx is None:
        return flat, "", None
    title = strip_cta(lines[idx])
    if not (6 <= len(title) <= 90) or _NAV.match(title):
        return flat, "", None
    loc, comp = "", None
    for line in lines[idx + 1:]:
        # A location never contains a job word. Without that, a card whose
        # second line is a team name reads as the desk.
        if not loc and len(line) <= 64 and _CARD_LOC.match(line) \
                and not _TITLEISH.search(line):
            loc = line
        elif comp is None and _PAY_CHIP.match(line):
            # THE ANCHOR IS THE CARD, NOT A WORD. salary.py refuses a bare
            # "$120k - 145k" on purpose - in the prose of a sales JD an
            # unanchored range is as likely to be a quota as a wage, and it
            # documents that the fix for a miss is a new anchored form, never a
            # loosened one. Here the anchor is structural: this line is the
            # card's own pay field, sitting on its own under the title. Saying
            # so supplies the anchor and changes nothing else - every sanity
            # bound, the M-multiplier refusal and the percentage refusal all
            # still apply, and still return None when they should.
            comp = salary.parse(f"Salary: {line}") if salary else None
    if not loc:
        # A CHIP ABOVE THE TITLE, read only once the lines below have come up
        # empty. ZeroEyes, Leo Technologies and k16 Solutions stack the place
        # first and the role under it, so twelve postings kept a blank location
        # with the answer sitting right there in the markup.
        #
        # THIS IS NOT THE RULE CLAUDE.md WARNS ABOUT. What went wrong there was
        # testing the location pattern to decide WHICH LINE THE TITLE WAS, and
        # "Database Administrator, Infrastructure - UK" duly came back as a job
        # called Manchester. The title here is already settled - chosen above by
        # position and by carrying a job word, without the location pattern
        # being consulted at all - so nothing found in this loop can move it.
        # All it can do is fill a field that would otherwise stay empty.
        #
        # WHAT THE TWELVE ACTUALLY BUY, counted rather than assumed: one becomes
        # a US desk (Boca Raton, FL). Ten state a work mode that read "not
        # stated" before. One - "Conshohocken, PA / Honolulu, HI" - names two
        # cities and correctly yields no desk at all. The ZeroEyes rows say
        # "Remote / Hybrid / Conshohocken, PA", and REMOTE_RE reads that as
        # eligibility rather than a seat, so Conshohocken does NOT become an
        # office. That is the existing rule working, not this one failing.
        #
        # BELOW BEATS ABOVE, and no board proves it. Not one card in the corpus
        # has a location line on both sides of its title, so the ordering here
        # is a preference for the documented rule rather than a finding, and
        # gets no test of its own: a case for it would have to be invented.
        for line in lines[:idx]:
            if len(line) <= 64 and _CARD_LOC.match(line) \
                    and not _TITLEISH.search(line):
                loc = line
                break
    return title, loc, comp


def fetch_html_titles(url: str) -> list[dict]:
    """Enumerate job titles from a server-rendered careers page.

    Deliberately conservative: a link counts only if its href looks like a job
    URL and its text reads like a job title. Requires at least two hits, because
    one match is far more likely to be a stray link than a real board.
    """
    resp = _page(url)
    seen, out = set(), []
    for m in _ANCHOR.finditer(resp.text):
        href, inner = m.group(1), m.group(2)
        # A LINK THAT WRAPS THE WHOLE CARD. uveye.com/careers puts the title,
        # the location, the employment type and a "More Details / Less Details"
        # toggle inside one <a>, so flattening it gave sixteen postings titled
        # "Supply Chain Analyst Teaneck, NJ Full-time More Details Less Details".
        # The title is right there in its own <h3>; taking it is both more
        # accurate and less clever than trying to strip the tail off.
        #
        # Only when there is EXACTLY ONE heading. Two headings mean the anchor
        # is a section rather than a card, and guessing which is the title is
        # the kind of cleverness that puts a location in the title field.
        heads = _HEADING.findall(inner)
        picked = heads[0][1] if len(heads) == 1 else inner
        text = re.sub(r"\s+", " ", _ANYTAG.sub(" ", picked)).strip()
        text = strip_cta(html_lib.unescape(text))
        # A CARD WHOSE LINK SAYS ONLY "View Job". Therap's five postings each
        # link with that text and name the role in aria-label ("View job:
        # Senior Support Specialist"); the title gate dropped all five, and
        # only the three also linked from the header menu reached the board
        # (fourth review, 2026-10-08). Used only when the visible text is not
        # a title, and only the role named after "View job:".
        named = False         # the page's own label says this link is a job
        aria_loc = ""
        open_tag = m.group(0)[:m.group(0).find(">") + 1]
        if not _TITLEISH.search(text):
            aria = _ARIA_JOB.search(open_tag)
            if aria:
                text, named = html_lib.unescape(aria.group(1)).strip(), True
            elif not text:
                # AN EMPTY LINK LAID OVER A CARD, named only by its label -
                # Webflow's pattern: Ekin's "Customer Success Manager | Poland"
                # (2026-10-08). ONLY in that "Title | Place" form: a label that
                # is a sentence ("Read more about Senior Frontend Engineer",
                # Nedap's group vacancies page) is a button, not a title
                # (review, 2026-10-08). The title gate below still applies.
                aria = _ARIA_ANY.search(open_tag)
                label = html_lib.unescape(aria.group(1)).strip() if aria else ""
                if " | " in label and not _CTA_LEAD.match(label):
                    text, _, aria_loc = (x.strip() for x in label.partition(" | "))
        if not (6 <= len(text) <= 90) or _NAV.match(text):
            continue
        if _JOB_COUNT.search(text):
            continue          # "Engineer jobs 555,845 open jobs" is a rail, not a role
        if not (_JOB_HREF.search(href) and (named or _TITLEISH.search(text))):
            continue
        # DEDUP ON THE LINK, NOT THE TITLE. It used to key on the title, which
        # worked only because the titles were dirty: Samsara's card text
        # carried the location, so "Product Operations Manager Remote - Canada"
        # and "... Remote - US" were different strings and both survived.
        # Cleaning the titles collapsed 241 rows to 192 - forty-nine postings
        # deleted by a fix meant to tidy them.
        #
        # The url is what actually distinguishes two postings; two links are
        # two advertisements even when the role is the same, and CLAUDE.md is
        # explicit that the per-location ROWS all stay and only the counting
        # changes. opening_id already collapses them for the headline.
        key = urllib.parse.urljoin(url, html_lib.unescape(href))
        if key in seen:
            continue
        seen.add(key)
        # The gates above stay on the flattened text: they decide whether this
        # link is a posting at all, and they have their own scars. Only once it
        # IS one do the card's lines get read, for the three fields that were
        # being run together into the title.
        title, loc, comp = card_fields(_card_lines(inner), text)
        loc = loc or aria_loc
        out.append({"title": title, "location": loc, "comp": comp,
                    "url": urllib.parse.urljoin(url, html_lib.unescape(href))})
    if len(out) < 2:
        raise AtsError("no enumerable job links on the page")
    # A careers page that lists jobs for search engines embeds a JobPosting
    # block per role, in the page we have already downloaded. Free, when it is
    # there. Matched on the exact title because that is the only key the two
    # halves share, and a near-match would file one job's pay under another's.
    ld = _page_postings(resp.text)
    for row in out:
        jd, comp = ld.get(plain(row["title"]), ("", None))
        if jd:
            row["jd"] = jd
        if comp:
            row["comp"] = comp
    return plain_rows(out)


def _page_postings(raw: str) -> dict[str, tuple[str, dict | None]]:
    """title -> (description, comp) for every JobPosting block in a page."""
    found: dict[str, tuple[str, dict | None]] = {}
    for block in _LD.findall(raw):
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        for node in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(node, dict) or node.get("@type") != "JobPosting":
                continue
            title = plain(str(node.get("title") or ""))
            if title:
                found[title] = (plain_html(node.get("description") or ""),
                                _schema_comp(node))
    return found


def fetch_html(url: str) -> list[dict]:
    """Weakest fetcher: returns one pseudo-job carrying the page text for the
    classifier to scan. If the page is a JS shell (very little text), raise."""
    text = _page_text(url)
    if len(text) < 400:
        raise AtsError("page too small - likely JS-rendered")
    return [{"title": "", "location": "", "url": url, "_pagetext": text}]



# --- ADP WorkforceNow --------------------------------------------------------
#
# The largest single vendor hole on the board. Ten companies sit in the
# discovery log as "html:https://workforcenow.adp.com/... found but unreadable
# (page too small - likely JS-rendered)". That page IS a shell: ~5KB that draws
# nothing until a script asks ADP's career-center API for the requisitions.
# The API is public and answers JSON, verified 2026-09-02 against Zenner USA
# (11 requisitions) with a cid read off the company's own careers page.
#
# ref is the portal's `cid` - a UUID - optionally followed by "|<ccId>". Most
# portals answer on the default ccId; the few that need another carry it.
# Nothing here guesses a cid: it comes from a link the company published.
ADP_CCID = "19000101_000001"


def fetch_adp(ref: str) -> list[dict]:
    cid, _, cc = str(ref).partition("|")
    cc = cc or ADP_CCID
    api = ("https://workforcenow.adp.com/mascsr/default/careercenter/public/events/"
           f"staffing/v1/job-requisitions?cid={cid}&ccId={cc}&lang=en_US&locale=en_US")
    data = _json(_get(api))
    out = []
    for r in data.get("jobRequisitions") or []:
        title = plain(r.get("requisitionTitle") or "")
        if not title:
            continue
        # requisitionLocations[].nameCode.shortName reads "Minol - Addison, TX"
        # - an ADP location label, a site name then a place. The address block
        # beside it was empty on every row seen, so the label is what there is.
        locs = []
        for L in r.get("requisitionLocations") or []:
            s = ((L.get("nameCode") or {}).get("shortName") or "").strip()
            if s:
                locs.append(s.split(" - ", 1)[-1] if " - " in s else s)
        item = r.get("itemID") or ""
        url = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
               f"recruitment.html?cid={cid}&ccId={cc}&jobId={item}&lang=en_US")
        out.append({"title": title, "location": ", ".join(dict.fromkeys(locs)),
                    "url": url, "posted": posted_date(r.get("postDate"))})
    return out

# ------------------------------------------------------------------ Gusto
#
# jobs.gusto.com is server-rendered HTML with no API, no JSON-LD and no
# embedded payload - a parser reads the markup or reads nothing. It earns a
# place beside the JSON boards for the reason `html` does not: it states its
# own emptiness in words, so a zero here is a fact the employer published
# rather than a fetcher's silence. STRUCTURED means enumerable, not JSON.
#
# FIVE STATES, each with a POSITIVE marker, checked in a fixed order:
#   populated  "Open Positions" + /postings/ hrefs      -> rows
#   empty      "There are no open positions currently"  -> [] -> None found
#   closed     "This job board is closed."              -> Unknown
#   gone       body says "404 Error" (served as 404)    -> Unknown
#   anything else                                       -> Unknown
#
# CLOSED IS NOT EMPTY, and that distinction is the whole point of this comment.
# A closed board means the employer MOVED - City Detect left this vendor for
# Gem and their Gusto board is still live and still takes applications - not
# that they stopped hiring. Reading it as a zero deletes a warm door.
GUSTO_HOST = "jobs.gusto.com"
GUSTO_BASE = "https://jobs.gusto.com/"

_GUSTO_REF = re.compile(
    r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I)
# Measured on every live board: "Careers at MD Ally", "Careers at Kaiden AI".
# The closed board's title is the bare word "Careers", which is why the
# trailing \S matters - it is what separates a real board from a shell.
_GUSTO_CHROME = re.compile(r"<title>\s*Careers at\s+\S", re.I)
_GUSTO_GONE = re.compile(r"404 Error|can'?t find the page you'?re looking for", re.I)
_GUSTO_CLOSED = re.compile(r"this\s+job\s+board\s+is\s+closed", re.I)
_GUSTO_EMPTY = re.compile(r"there\s+are\s+no\s+open\s+positions", re.I)
_GUSTO_CARD = re.compile(r'<a\b[^>]*href="(/postings/[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_GUSTO_HREF = re.compile(r'href="(/postings/[^"]+)"')
_GUSTO_UL = re.compile(r'<ul[^>]*class="[^"]*divide-y[^"]*"[^>]*>(.*?)</ul>', re.S | re.I)
_GUSTO_LI = re.compile(r"<li\b[^>]*>", re.I)
_GUSTO_H3 = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S | re.I)
_GUSTO_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
_GUSTO_PAGED = re.compile(r'rel="next"|href="[^"]*\?[^"]*\bpage=\d', re.I)
# The empty boards are 8,586-8,779 bytes; the 404 body is 2,898. A truncated
# response that happened to retain one sentence must never be read as a claim.
_GUSTO_MIN_BYTES = 4000
_GUSTO_TYPES = {"full time", "part time", "contractor", "internship",
                "temporary", "intern", "contract", "seasonal"}
_MIDDOT = "\u00b7"


def _gusto_seg(ref: str) -> str:
    """The board path segment, from a bare segment or a full URL.

    Liberal about what is on file, strict about what reaches the network. The
    segment is NEVER derived from the company id: kaiden-ai's board is
    `kaiden-incorporated-868aff01-...` and tranquility-ai's is
    `tranquility-ai-inc-bb9f0c71-...`.
    """
    s = str(ref or "").strip()
    if s.lower().startswith("http"):
        parts = urllib.parse.urlsplit(s)
        if parts.netloc.lower() != GUSTO_HOST:
            raise AtsError("gusto ref is not a jobs.gusto.com board")
        path = parts.path
        s = path.split("/boards/", 1)[-1] if "/boards/" in path else path
    seg = s.split("?")[0].strip("/")
    if not _GUSTO_REF.match(seg):
        raise AtsError(f"gusto ref is not a board id: {ref!r}")
    return seg


def _gusto_ptext(frag: str) -> str:
    """One card <p>'s visible text.

    _ANYTAG is quote-blind, and that is deliberate: two svgs on the live
    city-detect board carry `class="h-5 w-5 inline mr-1 width="16"` - an
    unclosed quote. A quote-aware tag regex leaks `16"` into the title. Do not
    "fix" this into an attribute-aware pattern.
    """
    return _unescape(
        _ANYTAG.sub(" ", _COMMENT.sub(" ", _SCRIPTY.sub(" ", frag)))).strip()


def _gusto_is_meta(line: str) -> bool:
    """A pay/employment chip rather than a place."""
    return (_MIDDOT in line or line.lower() in _GUSTO_TYPES
            or bool(_PAY_CHIP.match(line)))


def fetch_gusto(ref: str) -> list[dict]:
    raw = _page(GUSTO_BASE + "boards/" + _gusto_seg(ref)).text
    text = plain_html(raw)
    hrefs = set(_GUSTO_HREF.findall(raw))

    # 1. Truncation. Before any sentence on the page is read as a statement.
    if len(raw.encode("utf-8", "ignore")) < _GUSTO_MIN_BYTES:
        raise AtsError("gusto board unreadable: page truncated")
    # 2. Gone. Keyed on the BODY, not the status code, so it holds whether the
    #    vendor serves this as 404 (it does today) or as a 200 shell later.
    if _GUSTO_GONE.search(text):
        raise AtsError("gusto board not found: page says 404")
    # 3. Closed. HTTP 200, no postings, no company chrome. They moved.
    if _GUSTO_CLOSED.search(text):
        raise AtsError("gusto board is closed - find where they moved")
    # 4. Contradiction: it both lists postings and disclaims them.
    if hrefs and _GUSTO_EMPTY.search(text):
        raise AtsError("gusto board both lists postings and disclaims them")
    # 5. Pagination. Unknown for a big board is loud and fixable; a truncated
    #    board published as complete is the SmartRecruiters 100-of-251 failure.
    if _GUSTO_PAGED.search(raw):
        raise AtsError("gusto board looks paginated - unhandled")
    # 6. Populated. STRICTLY BEFORE the empty branch, so a page carrying any
    #    application link can never be read as empty whatever its scripts say.
    if hrefs:
        return _gusto_rows(raw, hrefs)
    # 7. THE ONLY EMPTY RETURN. The vendor's own sentence, in text that has had
    #    <script>/<style> stripped by plain_html, on a page whose title names
    #    the employer, over the byte floor, with no /postings/ anywhere.
    if _GUSTO_EMPTY.search(text) and _GUSTO_CHROME.search(raw):
        return []
    # 8. Looks like dead code and is not. Every unrecognised shape is Unknown.
    raise AtsError("gusto board markup did not match any known shape")


def _gusto_rows(raw: str, hrefs: set) -> list[dict]:
    """Parse the populated branch, then refuse to report a partial read."""
    out = []
    for href, inner in _GUSTO_CARD.findall(raw):
        h3s = _GUSTO_H3.findall(inner)
        if not h3s:
            raise AtsError("gusto card has no title")
        if len(h3s) > 1:
            # We would not know which pay belongs to which title, and a salary
            # published against the wrong job is the one field where being
            # wrong is worse than being silent.
            raise AtsError("gusto anchor wraps 2+ headings - grouping changed")
        title = _unescape(_ANYTAG.sub(" ", h3s[0])).strip()
        if not title:
            raise AtsError("gusto card title is empty")
        lines = [t for t in (_gusto_ptext(x) for x in _GUSTO_P.findall(inner)) if t]
        # POSITION FIRST, PATTERN SECOND: the first line that is not a chip is
        # the place. Testing the location pattern first steals a title that
        # looks like one.
        location = next((t for t in lines if not _gusto_is_meta(t)), "")
        comps = []
        for line in (t for t in lines if _gusto_is_meta(t)):
            for seg in line.split(_MIDDOT):
                got = _ats_text(seg.strip())
                if got:
                    comps.append(got)
        if len(comps) > 1:
            raise AtsError("gusto card states two pay ranges")
        out.append({"title": title, "location": location,
                    "url": urllib.parse.urljoin(GUSTO_BASE, href),
                    "comp": comps[0] if comps else None})

    # Every href is represented. Asymmetric: extra rows are fine, a missing
    # one is a deleted job nothing counts.
    missing = hrefs - {urllib.parse.urlsplit(r["url"]).path for r in out}
    if missing:
        raise AtsError(f"gusto parsed {len(out)} of {len(hrefs)} postings")
    # The page's own card count. ALSO ASYMMETRIC, deliberately: fewer rows than
    # cards means one went unread; more is fine (a <ul> whose class we no
    # longer recognise still yielded its anchors). Written as equality this
    # false-alarms the day Gusto splits a board by department.
    li_total = sum(len(_GUSTO_LI.findall(b)) for b in _GUSTO_UL.findall(raw))
    if len(out) < li_total:
        raise AtsError(f"gusto read {len(out)} rows from {li_total} cards")
    return out


# ------------------------------------------------------------------ Gem
#
# jobs.gem.com renders NOTHING to a fetch - every page, board index and job
# alike, is the same ~4KB JavaScript shell with no JobPosting JSON-LD. The
# shell calls one public GraphQL endpoint that needs no auth, no key and no
# Referer, and answers ~1.6KB of structured JSON.
#
# THE DISCRIMINATOR IS `jobBoardExternal`. A live board with nothing open and a
# slug that does not exist return a BYTE-IDENTICAL `"jobPostings": []`; only
# the board object tells them apart. One `or {}` added by somebody tidying up
# turns every retired or mistyped slug into a public "None found".
GEM_HOST = "jobs.gem.com"
GEM_API = "https://jobs.gem.com/api/public/graphql/batch"

_GEM_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$", re.I)
_MISSING = object()

# FULL adds exactly one field to the board's own captured query:
# descriptionHtml on jobPostings, which returns the real ad (2,700-4,100
# characters on City Detect's three roles) in the SAME single POST.
_GEM_QUERY_FULL = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { extId title descriptionHtml
      locations { city isoCountry isRemote }
      job { locationType employmentType } } }
  jobBoardExternal(vanityUrlPath: $boardId) { id teamDisplayName }
}"""

# The field we added is the field most likely to be removed. Without this
# fallback a schema change to it would turn every Gem company Unknown.
_GEM_QUERY_MIN = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { extId title
      locations { city isoCountry isRemote }
      job { locationType employmentType } } }
  jobBoardExternal(vanityUrlPath: $boardId) { id teamDisplayName }
}"""


def _gem_slug(ref: str) -> str:
    """The vanity slug. NEVER derived from the company id - City Detect's id
    is `city-detect` and its Gem slug is `citydetect`."""
    s = str(ref or "").strip()
    if s.lower().startswith("http"):
        parts = urllib.parse.urlsplit(s)
        if parts.netloc.lower() != GEM_HOST:
            raise AtsError("gem ref is not a jobs.gem.com board")
        segs = [x for x in parts.path.split("/") if x]
        s = segs[0] if segs else ""
    if not _GEM_SLUG.match(s):
        raise AtsError(f"gem ref is not a board slug: {ref!r}")
    return s


def _gem_payload(slug: str) -> tuple[dict, bool]:
    """(data, had_descriptions). Always decided on an error-free response."""
    for query, full in ((_GEM_QUERY_FULL, True), (_GEM_QUERY_MIN, False)):
        body = [{"operationName": "JobBoardList",
                 "variables": {"boardId": slug}, "query": query}]
        payload = _post_json(GEM_API, body)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise AtsError("gem: unexpected graphql envelope")
        if payload[0].get("errors"):
            if full:
                continue                  # retry once without the added field
            raise AtsError("gem: graphql errors")
        data = payload[0].get("data")
        if not isinstance(data, dict):
            raise AtsError("gem: unexpected graphql envelope")
        return data, full
    raise AtsError("gem: graphql errors")


def fetch_gem(ref: str) -> list[dict]:
    slug = _gem_slug(ref)
    data, described = _gem_payload(slug)
    # THE discriminator. Unknown, never "None found".
    if data.get("jobBoardExternal") is None:
        raise AtsError(f"gem board does not exist: {slug}")
    # A MISSING key proves nothing; only a present-and-empty list proves
    # absence. `or {}` here would read every drift as "nobody is hiring".
    node = data.get("oatsExternalJobPostings", _MISSING)
    if node is _MISSING or not isinstance(node, dict):
        raise AtsError("gem: postings node missing")
    posts = node.get("jobPostings", _MISSING)
    if posts is _MISSING or not isinstance(posts, list):
        raise AtsError("gem: postings list missing")
    if not posts:
        return []                         # live board, nothing open
    out = []
    for p in posts:
        title = (p.get("title") or "").strip()
        if not title:
            continue
        locs = [l for l in (p.get("locations") or []) if isinstance(l, dict)]
        job = p.get("job") or {}
        remote = (job.get("locationType") == "REMOTE"
                  or (locs and all(l.get("isRemote") for l in locs)))
        # locations[].name MUST NEVER reach a row. Gem's own UI prints team
        # labels there - "City Detect (AL)", "City Detect Hardware Team" - and
        # roles.geography reads "(AL)" as an Alabama desk. That is the
        # two-capitals bug this repo has already recorded three times.
        cities = list(dict.fromkeys(c for c in (l.get("city") for l in locs) if c))
        if remote:
            location = "Remote"
        else:
            # Slash-joined so a multi-site req yields NO desk rather than
            # picking one of them.
            location = " / ".join(cities)
        isos = {l.get("isoCountry") for l in locs if l.get("isoCountry")}
        hint = None
        if len(locs) == 1 and not remote and locs[0].get("city"):
            hint = office_hint(city=locs[0].get("city"),
                               country=locs[0].get("isoCountry"))
        elif len(isos) == 1:
            # Sets no office (build_board gates that on city/state) and does
            # settle is_us from the employer's own field.
            hint = office_hint(country=next(iter(isos)))
        row = {"title": title, "location": location,
               "url": f"https://{GEM_HOST}/{slug}/{p.get('extId')}",
               # On the MIN retry the field was never asked for. Zero it
               # explicitly rather than trusting the server not to send one:
               # "" means "the board did not tell us", and a stale
               # description would be read for pay by plain_rows.
               "jd": plain_html(p.get("descriptionHtml") or "") if described else "",
               "mode": work_mode(job.get("locationType")),
               "comp": None}
        if hint:
            row["office_hint"] = hint
        out.append(row)
    if not out:
        raise AtsError("gem: every posting had a blank title")
    return out


# ------------------------------------------- boards read since 2026-11-21
#
# Each was measured live before a line was written (scratchpad research,
# 2026-11-19, re-read 2026-11-21), and each is read the way its own board
# page reads itself. Every request is a PAGE request (_page): robots.txt is
# asked at every hop, because several of these feeds sit on a tenant's own
# host, and a tenant can close its host (two Pinpoint tenants did).

def _text(raw) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _dated(raw, form: str) -> str | None:
    """A date in the one written form a board uses, or None. posted_date
    refuses to guess an order in general, rightly; these boards each write
    one order only, so each reader names its own."""
    try:
        return dt.datetime.strptime(_text(raw), form).date().isoformat()
    except ValueError:
        return None


# ---- hirebridge: hbapi.hirebridge.com/careercenter/v2/GetJobListings?cid=N
#
# The feed Hirebridge's own career centre calls from every visitor's browser:
# one GET, the whole board, descriptions included (CentralSquare 13, EAB 42,
# Tribute 13, Amerlux 0, measured 2026-11-19 and 11-21). The ref is the
# company id, a number in the board's address ("7724").
#
# THE FEED IS OVER-BROAD. Each row also carries the requisition behind it:
# internal notes that name staff, hiring-manager and coordinator ids, and the
# ad meant for internal applicants (descriptioninternal). None of it is on the
# public pages. So this copies the public fields BY NAME and nothing else, and
# the host is in NEVER_CACHED: no raw answer from it is ever written to disk.
#
# The body is a JSON STRING holding the JSON array, so it is decoded twice.
# An empty board is the string "[]" - and so is a cid nobody holds, which is
# why an empty feed reads as a zero here but never as a name (verify_boards).

# The query the career centre itself sends. addloc=0 is the part that
# matters: without it a job open in three places comes back as three rows
# with one jid (EAB, 2026-11-21: 93 rows for 42 jobs).
HIREBRIDGE_FEED = ("https://hbapi.hirebridge.com/careercenter/v2/GetJobListings"
                   "?cid={cid}&language=en&addloc=0&startrow=1&endrow=5000")
_HIREBRIDGE_CID = re.compile(r"[0-9]{1,7}")


HIREBRIDGE_LIST = "https://recruit.hirebridge.com/v3/jobs/list.aspx?cid={cid}"


def hirebridge_rows(ref) -> list[dict]:
    cid = str(ref if ref is not None else "").strip()
    if not _HIREBRIDGE_CID.fullmatch(cid):
        raise AtsError(f"not a hirebridge company id: {ref!r}")
    data = _json(_page(HIREBRIDGE_FEED.format(cid=cid), same_host=True))
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise AtsError("hirebridge feed did not parse") from exc
    if not isinstance(data, list) or not all(isinstance(j, dict) for j in data):
        raise AtsError("unexpected hirebridge payload")
    if not data:
        _hirebridge_account(cid)
    return data


def _hirebridge_account(cid: str) -> None:
    """AN EMPTY FEED IS A ZERO ONLY WHEN THE ACCOUNT ANSWERS. A live board with
    nothing open and a company id nobody holds both answer "[]" (9999999,
    measured 2026-11-19); the classic career centre tells them apart. A live
    one is titled "<Name> Career Center"; a dead id is sent, on the same host,
    to /v3/Application/AppErrMsg.aspx, titled "Hirebridge ATS Error". Anything
    else is not known, and is not a zero."""
    resp = _page(HIREBRIDGE_LIST.format(cid=cid), same_host=True)
    page, landed = resp.text or "", str(getattr(resp, "url", "") or "")
    if "apperrmsg.aspx" in landed.lower() or "Hirebridge ATS Error" in page:
        raise AtsError(f"hirebridge: no company holds id {cid} (its career centre "
                       f"answers with Hirebridge's error page)")
    if not re.search(r"<title>[^<]*\bCareer Center\s*</title>", page, re.I):
        raise AtsError(f"hirebridge: the feed for {cid} is empty and its career centre "
                       f"did not answer as a live board, so the zero is not known")


def fetch_hirebridge(ref) -> list[dict]:
    out, seen = [], set()
    for j in hirebridge_rows(ref):
        title = _text(j.get("jobtitle"))
        # one row per job: a second row with a jid already read is the same
        # job in another place, and the first row already lists its places
        jid = _text(j.get("joblistid"))
        if not title or (jid and jid in seen):
            continue
        seen.add(jid)
        # joblocname is the first place and additionalLocations the rest:
        # neither is the whole list alone (EAB 612567: Philadelphia, PA, then
        # Richmond, VA and Washington, D.C.)
        places = [_text(j.get("joblocname"))]
        for extra in j.get("additionalLocations") or []:
            if isinstance(extra, dict):
                places.append(_text(extra.get("name")))
        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        url = _text(j.get("url"))
        out.append({
            "title": title,
            "location": "; ".join(dict.fromkeys(p for p in places if p)),
            "url": urllib.parse.urljoin("https://recruit.hirebridge.com/", url) if url else "",
            # M/D/YYYY ('9/29/2026', '11/19/2026'), and the posting page's
            # JSON-LD datePosted agrees. Blank on one EAB row: None, never
            # createdate, which is when the requisition was opened.
            "posted": _dated(j.get("publicdate"), "%m/%d/%Y"),
            # isremote TRUE means remote. False is "not stated": it is false on
            # six of the seven CentralSquare roles whose title says Remote.
            "mode": "remote" if loc.get("isremote") is True else None,
            "office_hint": office_hint(loc.get("city"), loc.get("state"), loc.get("country")),
            # description + skills (the qualifications half), as recruitee's
            # two fields are joined. Never descriptioninternal, and never
            # extrainfo, which is the EEO paragraph.
            "jd": "\n\n".join(p for p in (plain_html(j.get("description") or ""),
                                          plain_html(j.get("skills") or "")) if p)})
    return out


# ---- applicantpro: <label>.applicantpro.com or <label>.isolvedhire.com
#
# One platform on two root domains (isolved runs it), so the ref is the whole
# tenant host. Its /jobs/ page names the domain id the list is asked by, and
# the list is the call that page's own JobListings component makes:
# /core/jobs/<domainId>?getParams=<the page's own getParams, as JSON>. Two
# requests a board (Caselle 17, Black Mountain 2, LYT 3, Springbrook 3).
#
# A DISABLED SITE ANSWERS 200: Genasys's /jobs/ lands, on the same host, on
# /notset.php?...&disabled=1 saying "This career site has been disabled". That
# is a board that is gone, never a board with nothing open.

_APPLICANTPRO_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:applicantpro|isolvedhire)\.com")
_APPLICANTPRO_LIST = re.compile(
    r"componentData\s*:\s*\{\s*organizationId\s*:\s*[0-9]+\s*,\s*domainId\s*:\s*([0-9]+)"
    r"\s*,\s*getParams\s*:\s*")


def applicantpro_page(ref) -> tuple[str, str]:
    """(host, the board's /jobs/ page), or AtsError for a host that is not an
    ApplicantPro tenant's or a site that has been disabled."""
    host = _text(ref).lower()
    if host.startswith("http"):
        host = (urllib.parse.urlsplit(host).hostname or "").lower()
    if not _APPLICANTPRO_HOST.fullmatch(host) or host.startswith("www."):
        raise AtsError(f"not an applicantpro board host: {ref!r}")
    resp = _page(f"https://{host}/jobs/", same_host=True)
    page = resp.text or ""
    if "notset.php" in str(getattr(resp, "url", "") or "") or "has been disabled" in page:
        raise AtsError(f"applicantpro: the career site at {host} has been disabled")
    return host, page


def applicantpro_jobs(host: str, page: str) -> list:
    """The board's whole list, asked the way its own page asks for it."""
    m = _APPLICANTPRO_LIST.search(page)
    if not m:
        raise AtsError("applicantpro board page carried no job list")
    try:
        params, _end = json.JSONDecoder().raw_decode(page, m.end())
    except ValueError as exc:
        raise AtsError("applicantpro getParams did not parse") from exc
    if not isinstance(params, dict):
        raise AtsError("applicantpro getParams is not an object")
    api = (f"https://{host}/core/jobs/{m.group(1)}?"
           + urllib.parse.urlencode({"getParams": json.dumps(params, separators=(",", ":"))}))
    d = _json(_page(api, same_host=True))
    data = _dig_dict(d, "data")
    jobs = data.get("jobs") if data else None
    if not (isinstance(d, dict) and d.get("success") is True and isinstance(jobs, list)):
        raise AtsError("unexpected applicantpro payload")
    return jobs


def fetch_applicantpro(ref) -> list[dict]:
    host, page = applicantpro_page(ref)
    out = []
    for j in applicantpro_jobs(host, page):
        if not isinstance(j, dict) or not _text(j.get("title")):
            continue
        city, state = _text(j.get("city")), _text(j.get("abbreviation"))
        country = _text(j.get("iso3"))
        # a city of "Remote" is a way of working, not a place
        if city.lower() == "remote":
            city = ""
        place = ", ".join(p for p in (city, state or _text(j.get("stateName")), country) if p)
        out.append({"title": _text(j.get("title")),
                    "location": place,
                    "url": _text(j.get("jobUrl")) or f"https://{host}/jobs/{j.get('id')}",
                    "posted": _dated(j.get("startDateRef"), "%b %d, %Y"),
                    "mode": work_mode(j.get("workplaceType")),
                    "office_hint": office_hint(city or None, state or None, country or None),
                    "comp": _applicantpro_comp(j, country)})
    if FETCH_DETAILS:
        # The ad is on each posting's own page, in a whole schema.org block.
        # Its pay is NOT taken: the block's baseSalary carries the same
        # floor-to-ceiling figures _applicantpro_comp refuses on commission
        # roles, and a figure it refused must not come back by another door.
        _details(out, lambda r: (_schema_posting(r["url"])[0], None))
    return out


def _dig_dict(obj, key) -> dict | None:
    v = obj.get(key) if isinstance(obj, dict) else None
    return v if isinstance(v, dict) else None


# What a pay note says when min..max is not a base range: commission, a
# bonus, on-target or total earnings (blind review, 2026-11-21: "$70K base;
# $140K on-target earnings" was printed as a 70-140K salary).
_NOT_BASE = re.compile(r"\bcommissions?\b|\bote\b|on.?target|\btotal (cash |target )?"
                       r"(compensation|comp|earnings|pay)\b|\btarget earnings\b|"
                       r"\bbonus(es)?\b|\bvariable\b|\buncapped\b|\bincentives?\b|\bequity\b",
                       re.I)


def _applicantpro_comp(j: dict, country: str) -> dict | None:
    # min/max are a pay range only on a 'Salary' or 'Hourly' row. On 'Base +
    # Commission' they run from the base floor to the OTE ceiling (LYT's
    # Senior AE: 82000-248000 against a stated base of $82k-$124k), which no
    # reader may print as pay; and 'Base + Bonus' does not say which.
    if _text(j.get("payType")) not in ("Salary", "Hourly"):
        return None
    if _NOT_BASE.search(_text(j.get("payDetails"))):
        return None
    return _comp(j.get("minSalary"), j.get("maxSalary"),
                 "USD" if country.upper() in ("USA", "US") else None,
                 _period(j.get("payTypeFrame")))


# ---- teamtailor: a career site at <label>.teamtailor.com, at
# <label>.na.teamtailor.com (its North American data centre), or on a domain
# of the company's own that it points at Teamtailor (careers.heimdallpower.com)
#
# Every Teamtailor career site publishes /jobs.json, a JSON Feed of the whole
# board, and announces it for programs (its /.well-known/api-catalog). The ref
# is the site's origin. ON A DOMAIN OF THEIR OWN THE HOST CANNOT VOUCH FOR THE
# VENDOR, so the body must: a JSON Feed whose feed_url is on the host that was
# asked and whose every item carries Teamtailor's _jobposting block. Measured
# 2026-11-19 and 11-21: Fonroche 11, Copenhagen Optimization 3, AM 2, and
# four sites with nothing open that still name themselves.

def teamtailor_feed(ref) -> tuple[str, dict]:
    """(host, the site's feed), or AtsError for anything that is not one."""
    raw = _text(ref).rstrip("/")
    parts = urllib.parse.urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.path or parts.query or parts.fragment \
            or parts.port or parts.username or not _HOSTNAME.fullmatch(host):
        raise AtsError(f"not a teamtailor career site: {ref!r}")
    resp = _page(f"https://{host}/jobs.json", same_host=True)
    d = _json(resp)
    items = d.get("items") if isinstance(d, dict) else None
    feed = (urllib.parse.urlsplit(str(d.get("feed_url") or "")).hostname or "").lower() \
        if isinstance(d, dict) else ""
    if not (isinstance(items, list) and feed == host
            and str(d.get("version") or "").startswith("https://jsonfeed.org/version/")
            and all(isinstance(i, dict) and isinstance(i.get("_jobposting"), dict)
                    for i in items)):
        raise AtsError(f"{host}/jobs.json is not a Teamtailor career site's feed")
    if not items and not host.endswith(".teamtailor.com"):
        # AN EMPTY FEED VOUCHES FOR NOTHING: any publisher's JSON Feed with no
        # items passes the checks above. Teamtailor names its app as the one
        # page allowed to frame the site (Heimdall Power's, 2026-11-21); an
        # empty feed on a domain of their own without that is not a zero.
        def marked(r) -> bool:
            csp = str((getattr(r, "headers", None) or {}).get("Content-Security-Policy") or "")
            return bool(re.search(r"(?<![a-z0-9.-])app(?:\.na)?\.teamtailor\.com\b", csp, re.I))
        if not marked(resp) and getattr(resp, "from_cache", False):
            # a replay from a cache kept before its headers were: asked once
            # more, plainly, so the answer is the server's own and is kept so
            resp = _page(f"https://{host}/jobs.json", same_host=True, fresh=True)
            d2 = _json(resp)
            if not (isinstance(d2, dict) and d2.get("items") == []):
                raise AtsError(f"{host}/jobs.json changed between two reads")
        if not marked(resp):
            raise AtsError(f"{host}/jobs.json lists nothing and its answer does not say it is "
                           f"Teamtailor's, so the zero is not known")
    return host, d


_HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def fetch_teamtailor(ref) -> list[dict]:
    _host, d = teamtailor_feed(ref)
    out = []
    for item in d["items"]:
        title = _text(item.get("title"))
        if not title:
            continue
        jp = item["_jobposting"]
        places, first = [], None
        for loc in jp.get("jobLocation") or []:
            a = loc.get("address") if isinstance(loc, dict) else None
            if not isinstance(a, dict):
                continue
            first = first or a
            place = ", ".join(p for p in (_text(a.get("addressLocality")),
                                          _text(a.get("addressRegion")),
                                          _text(a.get("addressCountry"))) if p)
            if place:
                places.append(place)
        # schema.org's own word for a remote role; absent on every row read,
        # so a role is remote here only when the site says so in those words
        remote = _text(jp.get("jobLocationType")).upper() == "TELECOMMUTE"
        out.append({"title": title,
                    "location": "; ".join(dict.fromkeys(places)),
                    "url": _text(item.get("url")),
                    "posted": posted_date(item.get("date_published")),
                    "mode": "remote" if remote else None,
                    "office_hint": office_hint(first.get("addressLocality"),
                                               first.get("addressRegion"),
                                               first.get("addressCountry")) if first else None,
                    "jd": plain_html(item.get("content_html") or ""),
                    # baseSalary only when the employer filled Teamtailor's
                    # salary field (1 posting of 14 read); else the ad's text
                    "comp": _schema_comp(jp)})
    return out

# ---- pinpoint: <slug>.pinpointhq.com
#
# /postings.json is the feed Pinpoint's own careers widget calls from the
# browser: the whole board, the ad in four HTML fields, and pay with a flag
# saying whether the employer shows it. It has no date; /jobs.rss, which the
# board itself links as rel=alternate, has one per job, joined by job.id.
# robots.txt is per tenant and two tenants close the whole host (Infor's and
# Coursedog's), so both are asked as pages. reporting_to names a hiring
# manager on some rows and is never read. Measured 2026-11-19 and 11-21:
# Clearview AI 7, Eptura 5, Pathify 4, CARTO 1, Democracy Works 0.

_PINPOINT_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_RSS_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_RSS_LINK = re.compile(r"<link>\s*(.*?)\s*</link>", re.S)
_RSS_DATE = re.compile(r"<pubDate>\s*(.*?)\s*</pubDate>", re.S)


def pinpoint_base(ref) -> str:
    slug = _text(ref).lower()
    if not _PINPOINT_SLUG.fullmatch(slug) or slug in ("www", "app", "api"):
        raise AtsError(f"not a pinpoint board: {ref!r}")
    return f"https://{slug}.pinpointhq.com"


def _rfc822_day(raw) -> str | None:
    """The day an RFC 822 stamp names, in the offset it was written in - never
    moved to UTC, which shifts a midnight stamp east of Greenwich a day back."""
    import email.utils
    try:
        return email.utils.parsedate_to_datetime(_text(raw)).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None


def fetch_pinpoint(ref) -> list[dict]:
    base = pinpoint_base(ref)
    d = _json(_page(f"{base}/postings.json", same_host=True))
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list) or not all(isinstance(j, dict) for j in rows):
        raise AtsError("unexpected pinpoint payload")
    # the dates, from the board's own RSS; a feed that will not read costs
    # the dates, never the board
    dates: dict[str, str] = {}
    try:
        for it in _RSS_ITEM.findall(_page(f"{base}/jobs.rss", same_host=True).text or ""):
            link, day = _RSS_LINK.search(it), _RSS_DATE.search(it)
            job = link.group(1).rstrip("/").rsplit("/", 1)[-1] if link else ""
            if job.isdigit() and day and _rfc822_day(day.group(1)):
                dates[job] = _rfc822_day(day.group(1))
    except AtsError:
        pass
    out = []
    for j in rows:
        title = _text(j.get("title"))
        if not title:
            continue
        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        job = j.get("job") if isinstance(j.get("job"), dict) else {}
        # the ad is four fields, each under the heading the employer gave it
        parts = []
        for key in ("description", "key_responsibilities", "skills_knowledge_expertise",
                    "benefits"):
            body = plain_html(j.get(key) or "")
            if body:
                head = _text(j.get(f"{key}_header"))
                parts.append(f"{head}\n{body}" if head else body)
        comp = None
        if j.get("compensation_visible") is True:
            comp = _comp(j.get("compensation_minimum"), j.get("compensation_maximum"),
                         j.get("compensation_currency"), _period(j.get("compensation_frequency")))
        out.append({"title": title,
                    # the employer's own words ('Hybrid - Washington, DC',
                    # 'Remote USA'); city and province are unreliable (London's
                    # province reads 'United Kingdom') and there is no country,
                    # so only the city goes into the hint
                    "location": _text(loc.get("name")),
                    "url": _text(j.get("url")),
                    "posted": dates.get(_text(job.get("id"))),
                    "mode": work_mode(j.get("workplace_type")),
                    "office_hint": office_hint(loc.get("city")),
                    "jd": "\n\n".join(parts),
                    "comp": comp})
    return out

# ---- zohorecruit: https://<portal>.zohorecruit.<com|eu|in>/jobs/<PageName>
#
# The career site's own page carries the whole list, as JSON in a hidden
# input (id="jobs"), and the board's facts in another (id="meta": the list's
# address and the account's name). Both sit at the very end of a page of
# some 1.8 MB, after an inlined asset map, so the page is read whole. The ref
# is the page's address on Zoho's own host - a custom domain serves the same
# board (careers.spektrasystems.com), and the vendor host is the one a probe
# can hold every read to.
#
# CLOSED JOBS ARE IN THE LIST: a row with Publish false is a job kept on view
# and closed (GovEase's only row, a 2025 opening). Only Publish true is read.
# Every wrong address answers 200 with a page saying so, so a page without
# the input is an error, never a zero; the input's literal [] is a zero.
# Measured 2026-11-19 and 11-21: RedMane 6, NEXGEN 5, Cantata 4, Cuseum 0.

_ZOHO_REF = re.compile(r"https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.zohorecruit\.(?:com|eu|in)"
                       r"/jobs/[A-Za-z0-9_-]{1,80}")


def _zoho_input(page: str, ident: str):
    """The JSON a hidden <input id=...> holds, or None. Found by the id and
    then its own tag, because the value comes before the id and is the
    whole list, escaped."""
    i = page.rfind(f'id="{ident}"')
    if i < 0:
        return None
    start, end = page.rfind("<input", 0, i), page.find(">", i)
    if start < 0 or end < 0:
        return None
    m = re.search(r'\bvalue="([^"]*)"', page[start:end + 1])
    if not m:
        return None
    try:
        return json.loads(html_lib.unescape(m.group(1)))
    except ValueError:
        return None


def zoho_board(ref) -> tuple[str, list, dict]:
    """(the board's address, its rows, its meta), or AtsError."""
    url = _text(ref).rstrip("/")
    if not _ZOHO_REF.fullmatch(url):
        raise AtsError(f"not a zoho recruit career site: {ref!r}")
    page = _page(url, same_host=True).text or ""
    rows, meta = _zoho_input(page, "jobs"), _zoho_input(page, "meta")
    if not isinstance(rows, list) or not isinstance(meta, dict) \
            or not all(isinstance(j, dict) for j in rows):
        raise AtsError(f"{url} carried no job list (Zoho answers 200 for a page that "
                       f"does not exist)")
    return url, rows, meta


def fetch_zohorecruit(ref) -> list[dict]:
    url, rows, meta = zoho_board(ref)
    base = _text(meta.get("list_url")) or url
    if (urllib.parse.urlsplit(base).hostname or "") != urllib.parse.urlsplit(url).hostname:
        base = url
    out = []
    for j in rows:
        title = _text(j.get("Posting_Title") or j.get("Job_Opening_Name"))
        if not title or j.get("Publish") is not True or not _text(j.get("id")).isdigit():
            continue
        city, state, country = (_text(j.get(k)) for k in ("City", "State", "Country"))
        remote = j.get("Remote_Job") is True
        jd = _text(j.get("Job_Description"))
        row = {"title": title,
               "location": ", ".join(p for p in (city, state, country) if p) or
                           ("Remote" if remote else ""),
               # the row has no address; the list's own plus the id answers
               # with the job, whatever its title becomes
               "url": f"{base}/{_text(j.get('id'))}",
               "posted": posted_date(j.get("Date_Opened")),
               # a bool: True is remote, False is not "onsite"
               "mode": "remote" if remote else None,
               "office_hint": office_hint(city or None, state or None, country or None),
               "jd": jd}
        if len(jd) <= 160 and jd.endswith("..."):
            # the list's teaser on a board laid out that way (Spektra's):
            # what the board gave, and never read for money
            row["_jd_is_teaser"] = True
        out.append(row)
    return out

# ---- comeet (now "Spark Hire Recruit"; hosts unchanged)
#
# The list is Comeet's careers API, one request a company, with the ad on
# &details=true: www.comeet.co/careers-api/2.0/company/<UID>/positions?token=.
# The UID is two hex digits, a dot, three ("F2.004"); the token is the public
# one the company's own careers page hands every visitor's browser - in its
# iframe, its COMEET.init call, or on its hosted page. So a ref is either
# "<UID>|<TOKEN>", as the company's page gave them, or the hosted board
# https://www.comeet.com/jobs/<slug>/<UID>, whose page carries the token
# (COMPANY_DATA) and is read for it each time. Measured 2026-11-19 and 11-21:
# Claroty 37, Cognyte 38, Mprest 12, Imagry 6, Bond Sports 3.
#
# Never read: the referral bounty (company_referrals_reward, "3000$", is not
# pay), the apply alias (email), the categories (one names a hiring manager),
# and location.is_remote, which is true on every Hybrid row. There is no
# posting date: time_updated is an edit stamp.

_COMEET_UID = r"[0-9A-F]{2}\.[0-9A-F]{3}"
_COMEET_PAIR = re.compile(rf"({_COMEET_UID})\|([0-9A-F]{{20,64}})")
_COMEET_HOSTED = re.compile(rf"https://www\.comeet\.com/jobs/([A-Za-z0-9_-]{{1,80}})/({_COMEET_UID})")


def comeet_board(ref) -> tuple[str, list, dict]:
    """(uid, every position, the hosted page's COMPANY_DATA or {})."""
    raw = _text(ref).rstrip("/")
    company: dict = {}
    pair, hosted = _COMEET_PAIR.fullmatch(raw), _COMEET_HOSTED.fullmatch(raw)
    if pair:
        uid, token = pair.group(1), pair.group(2)
    elif hosted:
        page = _page(raw, same_host=True).text or ""
        m = re.search(r"COMPANY_DATA\s*=\s*(?=\{)", page)
        try:
            company = json.JSONDecoder().raw_decode(page, m.end())[0] if m else {}
        except ValueError:
            company = {}
        uid, token = hosted.group(2), _text(company.get("token") if isinstance(company, dict) else "")
        if not isinstance(company, dict) or _text(company.get("company_uid")) != uid \
                or not re.fullmatch(r"[0-9A-F]{20,64}", token):
            raise AtsError(f"{raw} carried no company data for {uid}")
    else:
        raise AtsError(f"not a comeet board: {ref!r}")
    rows = _json(_page(f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions"
                       f"?token={token}&details=true", same_host=True))
    if not isinstance(rows, list) or not all(isinstance(j, dict) for j in rows):
        raise AtsError("unexpected comeet payload")
    return uid, rows, company


def fetch_comeet(ref) -> list[dict]:
    _uid, rows, _company = comeet_board(ref)
    out = []
    for j in rows:
        title = _text(j.get("name"))
        url = _text(j.get("url_comeet_hosted_page"))
        if not title or not url.startswith("https://www.comeet.com/jobs/"):
            continue
        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        details = sorted((d for d in j.get("details") or [] if isinstance(d, dict)),
                         key=lambda d: d.get("order") if isinstance(d.get("order"), int) else 99)
        out.append({"title": title,
                    "location": _text(loc.get("name")),
                    # one row per position uid, even where copies of one role
                    # in several places share the employer's own page
                    "url": url,
                    "posted": None,
                    "mode": work_mode(j.get("workplace_type")),
                    "office_hint": office_hint(loc.get("city"), loc.get("state"), loc.get("country")),
                    "jd": "\n\n".join(p for p in (plain_html(d.get("value") or "") for d in details)
                                       if p)})
    return out

# ---- jobvite: jobs.jobvite.com/<slug>
#
# The career site's list page, server-rendered with every opening on it:
# <td class="jv-job-list-name"><a href="/<slug>/job/<id>"> and its location
# cell. The ref is the slug. An embedded board's list page and postings send a
# browser on to the employer's own careers page (OverDrive's, 2026-11-19);
# asked with nl=1&fr=false they answer themselves. A dead slug is sent to
# search.jobvite.com (?invalid=1), so it is an error and never a zero - and a
# list with no rows is an error too, until a real empty board has been read.
# The XML feed beside it (app.jobvite.com) is not read: it names each job's
# hiring manager, and its host answers 429 to a second ask.
#
# JOBVITE'S TENANTS SHARE ONE HOST AND DIFFER BY PATH, so a rule held to the
# host follows a redirect from one company's board to another's (blind review,
# 2026-11-22). Every read here is held to jobs.jobvite.com/<slug>/, before the
# hop is asked anything, and a row is read only under the asked slug.
# Measured 2026-11-19 and 11-21: Ookla 28, Gigamon 22, Absolute 18,
# Uplight 5, OverDrive 5.

_JOBVITE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
JOBVITE_HOST = "jobs.jobvite.com"
_JOBVITE_NAME = re.compile(
    r'<td class="jv-job-list-name">\s*<a href="(/([A-Za-z0-9_-]+)/job/[A-Za-z0-9]{5,16})"[^>]*>'
    r'(.*?)</a>', re.S)
_JV_WHERE = '<td class="jv-job-list-location">'
_JV_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")


def jobvite_slug(ref) -> str:
    slug = _text(ref)
    if not _JOBVITE_SLUG.fullmatch(slug):
        raise AtsError(f"not a jobvite career site: {ref!r}")
    return slug


def _jobvite_read(url: str, slug: str):
    """A page of this career site and no other: every hop on jobs.jobvite.com
    and under /<slug>/, refused before it is asked anything."""
    want = "/" + slug.lower()

    def stay(u: str):
        parts = urllib.parse.urlsplit(u)
        raw = urllib.parse.unquote(parts.path or "/")
        path = raw.lower()
        # A PATH THAT DOES NOT SAY PLAINLY WHERE IT GOES is not held to the
        # slug at all: urljoin keeps a Location's dot segments, the library
        # removes them when it sends, so /<slug>/../<other>/ passed a prefix
        # test and asked another tenant's page (blind review, 2026-11-22).
        # Every Jobvite path is plain words (/<slug>/jobs, /<slug>/job/<id>),
        # so a segment that is anything else - a dot, '..;', an escape left
        # after one decoding (%252e), '//', a backslash - is refused (fifth
        # blind review: '..;' and double escapes passed a dot test).
        segs = raw.split("/")[1:]
        if segs and segs[-1] == "":
            segs = segs[:-1]                    # a trailing slash
        if not all(_JV_SEGMENT.fullmatch(seg) for seg in segs):
            raise AtsError(f"{url} leads off to {parts.hostname}{parts.path}, a path that does "
                           f"not say plainly which career site it is")
        if (parts.hostname or "").lower() != JOBVITE_HOST or not (
                path == want or path.startswith(want + "/")):
            raise AtsError(f"{url} leads off to {parts.hostname}{parts.path}, which is not "
                           f"this career site")
    return _page(url, same_host=True, may=stay)


def _jobvite_self(url: str, slug: str) -> str:
    """The page at `url`, or - when it sends a browser to the employer's own
    page - the same page asked for itself (nl=1&fr=false). Only a hop off the
    career site is answered that way; a refusal, a 404 or a 429 is an error."""
    try:
        return _jobvite_read(url, slug).text or ""
    except AtsError as exc:
        if "leads off" not in str(exc):
            raise
    joiner = "&" if "?" in url else "?"
    return _jobvite_read(url + joiner + "nl=1&fr=false", slug).text or ""


def jobvite_page(ref) -> tuple[str, str]:
    """(the list's address, the list page), or AtsError."""
    slug = jobvite_slug(ref)
    base = f"https://{JOBVITE_HOST}/{slug}/jobs"
    return base, _jobvite_self(base, slug)


def _jv_text(raw: str) -> str:
    # (<[^<>]*>: a tag never spans another '<', so a run of unclosed ones is
    # stripped in one pass - <[^>]+> took time with the square of it)
    return _text(html_lib.unescape(re.sub(r"<[^<>]*>", " ", raw or "")))


_TAG_END = frozenset(" \t\n\r\f/>")


def _tag_at(low: str, name: str, i: int) -> int:
    """Where the next <name tag starts at or after i - that tag, not a longer
    name that begins the same (<template-row> is not <template>) - or -1."""
    probe = "<" + name
    while True:
        j = low.find(probe, i)
        if j < 0:
            return -1
        k = j + len(probe)
        if k >= len(low) or low[k] in _TAG_END:
            return j
        i = k


# Elements whose content is raw text to a browser (scripting on) and no
# rows of anybody's: dropped from the page a reader sees. <title> is raw text
# too, kept for the probe; <plaintext> makes the rest of the page text.
_RAW_DROPPED = frozenset({"script", "style", "textarea", "xmp", "iframe", "noembed",
                          "noframes", "noscript"})
# Content a reader never sees as the page: a template's (inert) and an SVG or
# MathML island's (graphics; its <title> is not the document's).
_HIDDEN = frozenset({"template", "svg", "math"})
_FOREIGN = frozenset({"svg", "math"})
# HTML's breakout tags: a start tag of one of these inside svg or math ends
# the island (the HTML standard's foreign-content rule), and so does the end
# tag of an HTML element, which closes the HTML element the island sits in -
# an unclosed <svg> in a header does not hide the page after </header>
# (seventh blind review, 2026-11-22). Names SVG shares with HTML (a, title,
# style, script, font) are left to SVG.
_BREAKOUT = frozenset({"b", "big", "blockquote", "body", "br", "center", "code", "dd", "div", "dl", "dt",
                       "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6", "head", "hr", "i", "img", "li",
                       "listing", "menu", "meta", "nobr", "ol", "p", "pre", "ruby", "s", "small", "span",
                       "strong", "strike", "sub", "sup", "table", "tt", "u", "ul", "var"})
_HTML_END = _BREAKOUT | frozenset({"html", "header", "footer", "main", "nav", "section", "article",
                                   "aside", "form", "label", "button", "td", "th", "tr", "tbody", "thead",
                                   "tfoot", "caption", "figure", "figcaption", "details", "summary",
                                   "fieldset", "address", "picture", "object"})
_SPACE = " \t\n\r\f"
_ATTR = re.compile(r"""([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?""")


def _tag_end(low: str, j: int) -> int:
    """Where the tag whose attributes start at j ends - the index of its '>'
    - reading a quoted attribute value as text, as a browser does: a '>' or a
    '<!--' inside data-x="..." is no markup (sixth blind review, 2026-11-22).
    -1 when the tag never ends (an open quote runs to the end of the page).
    Linear: the next '>' is looked for again only once passed."""
    n = len(low)
    gt = low.find(">", j)
    while True:
        if 0 <= gt < j:
            gt = low.find(">", j)
        eq = low.find("=", j, gt if gt >= 0 else n)
        if eq < 0:
            return gt
        k = eq + 1
        while k < n and low[k] in _SPACE:
            k += 1
        if k < n and low[k] in "\"'":
            close = low.find(low[k], k + 1)
            if close < 0:
                return -1
            j = close + 1
        else:
            j = k                               # an unquoted value: up to a space or '>'


def _script_end(low: str, i: int, nxt: dict) -> int:
    """Where a script's body that starts at i ends (its '</script'), or -1,
    by HTML's script-data states: a '<!--' opens an escaped stretch, a
    '<script' inside it a double-escaped one in which '</script' does not
    end the script, and '-->' ends both. `nxt` is the walk's own record of
    where each mark comes next, kept across scripts: a page's later scripts
    never search again what its earlier ones searched (a page of 20,000
    scripts looked for '<!--' to its end once per script)."""
    find = {"<!--": lambda at: low.find("<!--", at), "-->": lambda at: low.find("-->", at),
            "<script": lambda at: _tag_at(low, "script", at),
            "</script": lambda at: _tag_at(low, "/script", at)}
    for m, f in find.items():
        if m not in nxt:
            nxt[m] = f(i)
    state, pos = 0, i
    want = {0: ("</script", "<!--"), 1: ("-->", "</script", "<script"), 2: ("-->", "</script")}
    while True:
        for m, f in find.items():
            if 0 <= nxt[m] < pos:
                nxt[m] = f(pos)
        hits = [(nxt[m], m) for m in want[state] if nxt[m] >= 0]
        if not hits:
            return -1
        at, m = min(hits)
        if m == "</script":
            if state != 2:
                return at
            state, pos = 1, at + 8
        elif m == "<!--":
            # '<!-->' and '<!--->' end the escape they open at once (the
            # standard's escaped-dash states); only a longer one is escaped
            state, pos = ((0, at + 5) if low.startswith(">", at + 4) else
                          (0, at + 6) if low.startswith("->", at + 4) else (1, at + 4))
        elif m == "-->":
            state, pos = 0, at + 3
        else:
            state, pos = 2, at + 7


def _ld_type(attrs: str) -> bool:
    """Does a <script> tag's own type attribute say application/ld+json? (Its
    language, its id or a data attribute saying so does not.)"""
    for m in _ATTR.finditer(attrs):
        if m.group(1).lower() == "type":
            v = next((g for g in m.group(2, 3, 4) if g is not None), "")
            return v.strip().lower() == "application/ld+json"
    return False


def _walk(page: str) -> tuple[str, list[str]]:
    """(the page as a reader sees it, its ld+json blocks): one pass over the
    markup, tag by tag, as a browser parses it.

    - A tag's quoted attribute values are text: a '<template>', '<!--' or
      '<script>' inside data-x="..." opens nothing (sixth blind review).
    - A comment is not page: it ends at '-->' or '--!>', '<!-->' and
      '<!--->' are empty ones, and one never closed takes the rest.
    - <script>, <style>, <textarea>, <xmp>, <iframe>, <noembed>, <noframes>
      and <noscript> are raw text and not page (a '<!--' or a <title> inside
      one is nothing); a script runs past a '</script>' its own '<!--<script>'
      escaped. <plaintext> makes the rest of the page text.
    - <title> is raw text, kept for the probe with its '<' written as '&lt;'
      - an unclosed one holds the rest of the page, rows and all.
    - <template> content is inert, nested ones counted; an <svg> or <math>
      island is no page either (its <title> is not the document's), and
      inside one <style> and <script> are not raw text.
    - A JSON-LD block is a top-level <script> whose own type attribute says
      application/ld+json.
    A commented-out row is a job the employer took down (ats._COMMENT's
    rule). Every '<' is looked at once and every search goes forward only,
    so no page holds a batch up."""
    page = page or ""
    low, n = lower_same(page), len(page)
    out, blocks, stack = [], [], []
    held = Counter()                            # what the stack holds, so no tag searches it
    keep_from, i = 0, 0
    shut = {c: low.find(c) for c in ("-->", "--!>")}
    marks: dict = {}                            # _script_end's next places, kept across scripts

    def cut(a: int, b: int) -> None:            # page[a:b] is not page
        nonlocal keep_from
        if not stack:
            out.append(page[keep_from:a])
            keep_from = b

    def plain_tag(a: int, gt: int) -> str:
        # a kept tag whose attribute values hold '<' or '>' is passed on with
        # them escaped: the readers after this one match markup by its text,
        # and found a <title> or a row inside data-x="..." (sixth blind review)
        return page[a] + page[a + 1:gt].replace("<", "&lt;").replace(">", "&gt;") + ">"

    def keep_tag(a: int, gt: int) -> None:
        nonlocal keep_from
        if not stack and (low.find("<", a + 1, gt) >= 0 or low.find(">", a + 1, gt) >= 0):
            out.append(page[keep_from:a])
            out.append(plain_tag(a, gt))
            keep_from = gt + 1

    while True:
        a = low.find("<", i)
        if a < 0:
            break
        if low.startswith("<!--", a):
            if low.startswith(">", a + 4):
                cut(a, a + 5)
                i = a + 5
                continue
            if low.startswith("->", a + 4):
                cut(a, a + 6)
                i = a + 6
                continue
            for c in shut:
                if 0 <= shut[c] < a + 4:
                    shut[c] = low.find(c, a + 4)
            ends = [(e, len(c)) for c, e in shut.items() if e >= 0]
            if not ends:
                cut(a, n)                       # never closed: the rest is a comment
                i = n
                break
            e, w = min(ends)
            cut(a, e + w)
            i = e + w
            continue
        c1 = low[a + 1:a + 2]
        if c1 in ("!", "?"):
            # a doctype, a processing instruction or a bogus comment, to its
            # '>'; a CDATA section only inside svg or math, to its ']]>'
            if low.startswith("<![cdata[", a) and (held["svg"] or held["math"]):
                e = low.find("]]>", a + 9)
                end = e + 3 if e >= 0 else n
            else:
                e = low.find(">", a + 2)
                end = e + 1 if e >= 0 else n
            cut(a, end)
            i = end
            if e < 0:
                break
            continue
        closing = c1 == "/"
        j = a + 2 if closing else a + 1
        if j >= n or not ("a" <= low[j] <= "z"):
            if closing:                         # '</>' is nothing; '</ ...' a bogus comment
                e = j if low.startswith(">", j) else low.find(">", j)
                end = e + 1 if e >= 0 else n
                cut(a, end)
                i = end
                if e < 0:
                    break
                continue
            i = a + 1                           # a '<' that opens no tag is text
            continue
        k = j
        while k < n and low[k] not in _TAG_END:
            k += 1
        name = low[j:k]
        gt = _tag_end(low, k)
        if gt < 0:
            cut(a, n)                           # a tag never closed: the rest is in it
            i = n
            break
        if (held["svg"] or held["math"]) and not held[name] and (
                (closing and name in _HTML_END) or (not closing and name in _BREAKOUT)):
            # leaving an svg or math island the page never closed: everything
            # from the innermost svg or math outward is shut, and this tag is
            # read again as HTML
            while stack and (held["svg"] or held["math"]):
                held[stack.pop()] -= 1
            if not stack:
                keep_from = a
            i = a
            continue
        if closing:
            if held[name]:                      # closes it and anything opened inside it
                while stack:
                    top = stack.pop()
                    held[top] -= 1
                    if top == name:
                        break
                if not stack:
                    keep_from = gt + 1
            else:
                keep_tag(a, gt)
            i = gt + 1
            continue
        foreign = bool(held["svg"] or held["math"])
        if name in _HIDDEN:
            if name in _FOREIGN and low[gt - 1] == "/":
                i = gt + 1                      # <svg/> holds nothing
                continue
            if not stack:
                out.append(page[keep_from:a])
            stack.append(name)
            held[name] += 1
            i = gt + 1
            continue
        if foreign:
            i = gt + 1                          # inside svg or math nothing is raw text
            continue
        if name == "plaintext":
            cut(a, n)
            i = n
            break
        if name in _RAW_DROPPED or name == "title":
            body = gt + 1
            close = _script_end(low, body, marks) if name == "script" else _tag_at(low, "/" + name, body)
            end_gt = _tag_end(low, close + 2 + len(name)) if close >= 0 else -1
            after = end_gt + 1 if end_gt >= 0 else n
            text_end = close if close >= 0 else n
            if name == "title":
                if not stack:
                    out.append(page[keep_from:a])
                    out.append(plain_tag(a, gt) + page[body:text_end].replace("<", "&lt;") + "</title>")
                    keep_from = after
            else:
                if name == "script" and not stack and _ld_type(page[k:gt]):
                    blocks.append(page[body:text_end])
                cut(a, after)
            i = after
            if after >= n:
                break
            continue
        keep_tag(a, gt)
        i = gt + 1
    if not stack:
        out.append(page[keep_from:])
    return " ".join(out), blocks


def _cut(page: str) -> str:
    """The page as a reader sees it: no comments, no script, style, textarea
    or template, every title kept (_walk)."""
    return _walk(page or "")[0]


def jobvite_rows(page: str, slug: str) -> list[tuple[str, str, str]]:
    """(url, title, location) per opening on a list page, or AtsError. One
    pass: each row is the text from its name cell to the next </tr>, or to
    the next row's name cell when the page leaves a row unclosed."""
    body, rows, i = _cut(page or ""), [], 0
    marker = '<td class="jv-job-list-name">'
    tr = body.find("</tr>")
    while True:
        i = body.find(marker, i)
        if i < 0:
            break
        # a row runs to its </tr> or to the next row's name cell, whichever
        # comes first, and the next </tr> is looked for again only once passed
        if 0 <= tr < i:
            tr = body.find("</tr>", i)
        nxt = body.find(marker, i + len(marker))
        ends = [x for x in (tr, nxt) if x >= 0]
        cell = body[i:min(ends) if ends else i + 20000]
        i += len(marker)
        name = _JOBVITE_NAME.match(cell)
        if not name or name.group(2).lower() != slug.lower() or not _jv_text(name.group(3)):
            continue                            # another tenant's row, or no title
        # the place: the row's first location cell to its </td>, found with
        # str.find (a regex restarted at every opener of a row of unclosed
        # cells took time with the square of the row: fifth blind review)
        where, lo = "", cell.find(_JV_WHERE)
        if lo >= 0:
            hi = cell.find("</td>", lo + len(_JV_WHERE))
            if hi >= 0:
                where = _jv_text(cell[lo + len(_JV_WHERE):hi])
        rows.append((f"https://{JOBVITE_HOST}" + name.group(1), _jv_text(name.group(3)), where))
    if not rows:
        raise AtsError("jobvite list page carried no openings (an empty board has not "
                       "been seen, so this is not read as a zero)")
    return rows


def jobposting_nodes(page: str) -> list[dict]:
    """Every schema.org JobPosting node on a page, IN PAGE ORDER - in a list,
    in an @graph or alone - from the page's own ld+json blocks as _walk finds
    them: by their <script> tag, never inside a comment or a template
    (blind reviews, 2026-11-22: a commented-out block named the employer for
    the probe; a stray mention of the type hid the next block)."""
    out = []
    for block in _walk(page or "")[1]:
        try:
            obj = json.loads(block)
        except ValueError:
            continue
        stack = [obj]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(reversed(node))      # popped in page order
            elif isinstance(node, dict):
                kind = node.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    out.append(node)
                if isinstance(node.get("@graph"), list):
                    stack.extend(reversed(node["@graph"]))
    return out


def jobvite_posting(url: str, slug: str) -> str:
    return _jobvite_self(url, slug)


def fetch_jobvite(ref) -> list[dict]:
    slug = jobvite_slug(ref)
    _base, page = jobvite_page(slug)
    out = [{"title": title,
            # 'Dallas, Texas', or '2 Locations' where a job is open in several
            "location": re.sub(r"\s*,\s*", ", ", where),
            "url": url} for url, title, where in jobvite_rows(page, slug)]
    if FETCH_DETAILS:
        # the ad and any stated pay are each posting's own schema.org block,
        # read from the posting as itself, never from the employer's page an
        # embedded posting sends a browser to
        def detail(row):
            nodes = jobposting_nodes(jobvite_posting(row["url"], slug))
            # ONLY THE NODE THAT IS THIS POSTING gives the row its ad and its
            # pay. Jobvite's own block names its job by "identifier" (the
            # job's id, measured 2026-11-22 on Gigamon's postings); a node
            # that names another id is another job, even alone on the page
            # (fifth blind review), and of several only one naming this id
            # counts.
            job = row["url"].rstrip("/").rsplit("/", 1)[-1]

            def names(n):
                return [x for x in (n.get("url"), n.get("@id"), n.get("identifier"))
                        if x not in (None, "", [], {})]
            mine = [n for n in nodes if job in json.dumps(names(n))]
            if mine:
                nodes = mine[:1]
            elif len(nodes) == 1 and not names(nodes[0]):
                pass                            # one node that names no job: this page's own
            else:
                nodes = []
            return ((plain_html(nodes[0].get("description") or ""), _schema_comp(nodes[0]))
                    if nodes else ("", None))
        _details(out, detail)
    return out

# ---- jobvitefeed: a Jobvite account's XML feed,
# app.jobvite.com/CompanyJobs/Xml.aspx?c=<company id>
#
# Owner, 2026-11-23, asked whether to read Tyler Technologies' openings
# through the only place they can be read - Tyler's careers pages load a
# Jobvite widget for one account (ci=q6NaVfwI) and link its CompanyJobs
# pages, and name no jobs.jobvite.com career site: "Yes, build it". The ref
# is the account's company id, eight characters, 'q', four, 'fw', one
# (q6NaVfwI; ql9aVfwj, qtA9VfwR, qf79Vfwa, qzDaVfw1 in the research of
# 2026-11-20). app.jobvite.com's robots.txt allows /CompanyJobs/.
#
# ONE REQUEST IS THE WHOLE BOARD. Measured 2026-11-23 on Tyler's: 200, one
# answer of 2,119,266 characters, 266 <job> rows, no paging, no total. A job
# open in several places is a parent row and one row per other place
# (parentId; 139 of the 266, each after its parent, each the parent's title,
# requisition, date, pay and ad, a place of its own, and a detail-url that
# is the parent's job page): 127 jobs, one row each here, the copies' places
# joined to the parent's.
#
# A PERSON'S NAME ON EVERY ROW. hiring_x0020_manager names one on all 266
# (and on every row of Absolute's and Gigamon's feeds, 2026-11-20). So the
# feed is read BY NAME, field by field (_JVX_FIELDS), and the characters of
# every other field are dropped as the parser meets them: no person's name
# is held, let alone put in a row, a note, stderr, the probe's answer or a
# cache (app.jobvite.com is in NEVER_CACHED, and nothing of the feed is
# written anywhere).
#
# PARSED AS XML, AND NOTHING ELSE IS TRUSTED: expat, with a DOCTYPE or an
# ENTITY declaration refused before anything it declares is read (a billion
# laughs, an external entity); read as a stream and refused past
# JOBVITE_FEED_MAX bytes; a feed cut short, one that is not a <result> of
# <job>s, and one that lists no job are errors, never a zero and never part
# of a board. CDATA (detail-url, salarycurrency) is text like any other.
#
# PACED: app.jobvite.com answered 429 (Retry-After 30) to a second feed
# asked a second after the first, so its gate holds requests
# JOBVITE_FEED_PAUSE apart (_GATE_PAUSES). A 429, a 5xx and the network
# failing are raised as every reader raises them - the nightly asks a board
# once more on a 429 or the network, and keeps it Unknown, never "None
# found".
JOBVITE_FEED_HOST = "app.jobvite.com"
JOBVITE_FEED = "https://app.jobvite.com/CompanyJobs/Xml.aspx?c={account}"
# Bytes of a feed read, and more is refused, never read in part. Tyler's 127
# jobs are 2.1 MB (8 KB a row); Absolute's and Gigamon's rows run to 15 KB,
# so this reads a feed of some 2,000 rows - past the owner's giants of about
# 500 openings, so a giant is read and counted rather than refused unseen.
JOBVITE_FEED_MAX = 32_000_000
# Rows that may not read as a job of this account (no id, no title, a job
# page that is another account's) before the read is an error: the feed is
# the board, so more is a feed this reader does not understand.
JOBVITE_FEED_SLACK = 2
JOBVITE_FEED_GIANT = 500     # the owner's line for giants (wire_found.GIANT)
JOBVITE_ACCOUNT = re.compile(r"q[A-Za-z0-9]{4}fw[A-Za-z0-9]")
# a job's id (odUyAfwZ), or a per-location copy's (<parent id>-<its own>)
_JVX_JOB_ID = re.compile(r"[A-Za-z0-9]{5,16}(?:-[A-Za-z0-9]{5,16})?")
# THE ONLY FIELDS EVER READ. Not hiring_x0020_manager, not apply-url,
# briefdescription, jobtype, externalJobPostingId, agency_x0020_access, nor
# any field a feed adds tomorrow: what is not named here is dropped unread.
_JVX_FIELDS = frozenset({"id", "parentId", "title", "requisitionid", "category", "location",
                         "date", "detail-url", "description", "salarymin", "salarymax",
                         "salarycurrency", "salaryfrequency",
                         # (whose board it is, for verify_boards: never a person)
                         "company", "company_x0020_name"})
# what salaryfrequency says, word for word ('Annually', 'Hourly' on Tyler's
# 213 paid rows). _period would read 'Bi-Weekly' as a week and 'Semi-Monthly'
# as a month, so anything else is no period, and no pay published.
_JVX_PERIODS = {"annually": "year", "annual": "year", "yearly": "year", "monthly": "month",
                "weekly": "week", "daily": "day", "hourly": "hour"}
_JVX_DAY = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})")
_JVX_FIGURE = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,4})?")
_JVX_CODE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")


class _JvxRefused(Exception):
    """A feed the parser stops reading at once (a DOCTYPE or ENTITY, a root
    that is not <result>), raised out of an expat handler."""


def jobvitefeed_account(ref) -> str:
    """The Jobvite company id a ref names, or AtsError."""
    account = _text(ref)
    if not JOBVITE_ACCOUNT.fullmatch(account):
        raise AtsError(f"not a jobvite company id: {ref!r}")
    return account


def jobvitefeed_url(ref) -> str:
    return JOBVITE_FEED.format(account=jobvitefeed_account(ref))


def jobvitefeed_page(ref) -> str:
    """Where a person can look at the account's jobs: Jobvite's own careers
    page for it, which robots.txt allows (never the feed: it is for programs,
    and it names a person on every row). Not opened by this reader."""
    return f"https://{JOBVITE_FEED_HOST}/CompanyJobs/Careers.aspx?c={jobvitefeed_account(ref)}"


def _jvx_body(url: str) -> bytes:
    """The feed's bytes, asked once: robots.txt asked (follow), and no
    redirect followed - the feed answers in place, so a hop anywhere is
    refused before it is asked anything: to Jobvite's own pages, where an
    account nobody holds is sent, or back to the feed itself, which follow()
    asked eleven times over, at the host's 30-second pace, before it gave up
    (blind review 12, 2026-11-23). Paced at the host's gate (_one), read as
    a stream, and refused past JOBVITE_FEED_MAX. A 429 is honoured at the
    gate and raised as RateLimited; any other answer but a 200 is an error;
    the network failing on the way, mid-body included, is a network error."""
    hops: list[str] = []

    def stay(u: str):
        hops.append(u)
        if u != url:
            raise AtsError(f"{url} leads off to {str(u)[:120]}, which is not this account's feed "
                           f"(an account that is not there is sent on to Jobvite's own pages), so "
                           f"it is not read as a board with nothing open")
        if len(hops) > 1:
            raise AtsError(f"{url} leads off to itself again: the feed answers in place, so a "
                           f"redirect is never followed (an account that is not there is sent on "
                           f"to Jobvite's own pages), and it is not read as a board with nothing open")
    resp = follow(url, lambda u: _one(u, stream=True), may=stay)
    try:
        status = getattr(resp, "status_code", None)
        if status == 429:
            _back_off(url, resp)
        if status != 200:
            raise AtsError(f"HTTP {status} for {url}")
        raw = bytearray()
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                raw += chunk
                if len(raw) > JOBVITE_FEED_MAX:
                    raise AtsError(f"{url} is more than {JOBVITE_FEED_MAX:,} bytes, this reader's "
                                   f"cap: it is not read, and never read in part")
        except requests.RequestException as exc:
            raise AtsError(f"network error: {exc}") from exc
    finally:
        try:
            resp.close()
        except Exception:                          # noqa: BLE001
            pass
    return bytes(raw)


def jobvitefeed_jobs(raw: bytes, url: str = "the feed") -> list[dict]:
    """Every <job> of a feed, as {field: [its text, once for each time the
    field appears]}, for the fields in _JVX_FIELDS and no others - or
    AtsError for a feed that declares a DOCTYPE or an ENTITY, does not read
    as XML (cut short, a bare '&', an entity XML does not define, junk after
    it), is not a <result> of <job>s, or lists no job.

    The characters of a field not named are never kept: the parser drops
    them as it meets them, hiring_x0020_manager's included. Comments and
    processing instructions are not text; CDATA is."""
    jobs: list[dict] = []
    path: list[str] = []
    here: dict = {"job": None, "field": None}

    def refuse(*_args):
        raise _JvxRefused("declares a DOCTYPE or an ENTITY")

    def start(name, _attrs):
        path.append(name)
        if len(path) == 1 and name != "result":
            raise _JvxRefused(f"is not a Jobvite feed (its root is <{name[:40]}>, not <result>)")
        if len(path) == 2:
            here["job"] = {} if name == "job" else None
        elif len(path) == 3 and here["job"] is not None:
            here["field"] = name if name in _JVX_FIELDS else None
            if here["field"]:
                here["job"].setdefault(name, []).append([])

    def end(name):
        if len(path) == 3 and here["field"]:
            parts = here["job"][here["field"]]
            parts[-1] = "".join(parts[-1])
            here["field"] = None
        elif len(path) == 2 and here["job"] is not None:
            jobs.append(here["job"])
            here["job"] = None
        path.pop()

    def text(data):
        if here["field"]:                     # (a field named, or something inside one)
            here["job"][here["field"]][-1].append(data)

    p = xml.parsers.expat.ParserCreate()
    p.buffer_text = True
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    p.UnparsedEntityDeclHandler = refuse
    p.NotationDeclHandler = refuse
    p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, text
    try:
        p.Parse(raw, True)
    except _JvxRefused as exc:
        raise AtsError(f"{url} {exc}: refused, and nothing it lists is read") from None
    except xml.parsers.expat.ExpatError as exc:
        raise AtsError(f"{url} does not read as XML ({exc}): a feed cut short, or not a feed, "
                       f"and never read in part") from None
    if not jobs:
        raise AtsError(f"{url} lists no job, so it is not read as a board with nothing open "
                       f"(an empty feed has not been seen)")
    return jobs


def _jvx_first(job: dict, field: str) -> str:
    """The first text a job gives a field, one line, or ""."""
    for value in job.get(field) or ():
        t = _text(value)
        if t:
            return t
    return ""


def _jvx_job_url(detail: str, account: str, job: str) -> str | None:
    """The job's own page, https - its detail-url, http in the feed - where
    it is this account's job page for this very job (Job.aspx, c= the
    account, j= the job), written with those two keys and no others; None
    for anything else, which names some other page."""
    try:
        parts = urllib.parse.urlsplit(detail)
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    except ValueError:
        return None
    if (parts.scheme not in ("http", "https") or parts.netloc.lower() != JOBVITE_FEED_HOST
            or parts.path.lower() != "/companyjobs/job.aspx"
            or query.get("c") != [account] or query.get("j") != [job]):
        return None
    return f"https://{JOBVITE_FEED_HOST}/CompanyJobs/Job.aspx?c={account}&j={job}"


def _jvx_day(raw: str) -> str | None:
    """M/D/YYYY ('8/26/2026', all 266 of Tyler's) as YYYY-MM-DD, or None:
    two-digit years and every other form are no date, never a guess."""
    m = _JVX_DAY.fullmatch(_text(raw))
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return None


def _jvx_figure(raw: str) -> tuple[int | float | None, bool]:
    """(the figure, whether it read). Blank or zero is no figure; '22.50'
    stays 22.5 - _money rounds to whole units, which would publish a rate
    the feed never stated."""
    s = re.sub(r"[,\s$]", "", raw or "")
    if not s:
        return None, True
    if not _JVX_FIGURE.fullmatch(s):
        return None, False
    v = float(s)
    if v <= 0:
        return None, True
    return (int(v) if v.is_integer() else v), True


def _jvx_currency(raw: str) -> str | None:
    """The ISO code salarycurrency names ('US Dollar (USD US$)', 'Philippine
    Piso (PHP ₱)', 'Canadian Dollar (CAD $)' on Tyler's), or None where it
    names none, or more than one."""
    t = _text(raw)
    inside = re.findall(r"\(([^()]*)\)", t)
    codes = {c for part in (inside or [t]) for c in _JVX_CODE.findall(part)}
    return codes.pop() if len(codes) == 1 else None


def _jvx_pay(job: dict, abroad: bool = False) -> dict | None:
    """The contract's comp block from the row's own salary fields
    (salarymin, salarymax, salarycurrency, salaryfrequency: 213 of Tyler's
    266 rows), source "ats" as every reader's stated range is - or None
    where the row states no figure, or a figure that does not read (half a
    range is not published as a floor or a ceiling the row never stated). A
    frequency not in _JVX_PERIODS leaves period None, which build_board.
    derived drops, as it drops every reader's pay with no period.

    `abroad` is where the row is (_abroad): a row the board lists outside
    the US whose salarycurrency names no code states no pay published, as a
    bare "$" there names no currency (_text_comp) - a block with none is
    read as US dollars on the site (blind review 12, 2026-11-23: 'Philippine
    Piso' with no code, on a row in the Philippines, was published as
    between $874k and $1116k a year)."""
    lo_raw, hi_raw = _jvx_first(job, "salarymin"), _jvx_first(job, "salarymax")
    (lo, lo_ok), (hi, hi_ok) = _jvx_figure(lo_raw), _jvx_figure(hi_raw)
    if not (lo_ok and hi_ok) or (lo is None and hi is None):
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi, lo_raw, hi_raw = hi, lo, hi_raw, lo_raw
    currency = _jvx_currency(_jvx_first(job, "salarycurrency"))
    if currency is None and abroad:
        return None
    period = _JVX_PERIODS.get(_jvx_first(job, "salaryfrequency").lower())
    return {"min": lo, "max": hi, "currency": currency, "period": period, "source": "ats",
            "raw": _raw_from(lo_raw if lo is not None else None,
                             hi_raw if hi is not None else None, currency, period)}


def jobvitefeed_rows(jobs: list[dict], account: str) -> tuple[list[dict], int]:
    """(one row per job, how many of the feed's rows did not read as a job
    of this account). A job is a parent row and its per-location copies,
    grouped by parentId whatever their order; the parent gives the row its
    title, date, page, category, pay and ad, and every copy's place joins
    its own, in the feed's order, once each, as the readers that read
    several places join them. A row with no id, a group with no title, and
    one whose page is not this account's job page for it are not read."""
    groups: dict[str, list[dict]] = {}
    unread = 0
    for job in jobs:
        jid, parent = _jvx_first(job, "id"), _jvx_first(job, "parentId")
        if not _JVX_JOB_ID.fullmatch(jid) or (parent and not _JVX_JOB_ID.fullmatch(parent)):
            unread += 1
            continue
        groups.setdefault(parent or jid, []).append(job)
    rows = []
    for key, members in groups.items():
        base = next((j for j in members if _jvx_first(j, "id") == key
                     and not _jvx_first(j, "parentId")), members[0])
        title = _jvx_first(base, "title")
        url = _jvx_job_url(_jvx_first(base, "detail-url"), account, key)
        if not title or not url:
            unread += len(members)               # (every row of the job is lost with it)
            continue
        places = dict.fromkeys(p for j in [base] + [m for m in members if m is not base]
                               for p in (_text(v) for v in j.get("location") or ()) if p)
        row = {"title": title,
               "location": "; ".join(places),
               "url": url,
               "posted": _jvx_day(_jvx_first(base, "date")),
               "category": _jvx_first(base, "category") or None}
        # (where the board will list it decides what a currency naming no
        # code is: _jvx_pay)
        row["comp"] = _jvx_pay(base, abroad=_abroad(row))
        # THE AD IS ALWAYS READ: it is in the answer already downloaded, so it
        # costs no request, and "the boards that hand the description over in
        # the list response we already make are always read, flag or no flag"
        # (FETCH_DETAILS above). Behind the flag, as first built, the nightly
        # would have published 41 of Tyler's jobs as "We could not read this
        # posting" with the ad in hand.
        row["jd"] = plain_html(next((v for v in base.get("description") or () if v.strip()), ""))
        rows.append(row)
    return rows, unread


# ONE PLAN ASKS THE FEED ONCE (blind review 12, 2026-11-23). The wiring door
# reads a board (add_company.read_board) and then asks it whose it is
# (verify_boards.board_says), and for this type both are this one feed: one
# plan asked the same address twice, 30 seconds apart, though one answer
# serves both. Inside jobvitefeed_kept() - wire_found.plan, and nothing else
# - a feed read whole is kept in memory under the address it was asked at,
# for as long as the plan runs, and asked again it is that answer. Nothing
# is written anywhere; a read that failed raised, and is not kept; outside
# the block every call asks.
_JVX_KEPT: dict | None = None


@contextlib.contextmanager
def jobvitefeed_kept():
    """Each feed read whole is the answer to every later ask of it in the
    block (see above). Also a decorator: each call is a block of its own."""
    global _JVX_KEPT
    outer = _JVX_KEPT
    _JVX_KEPT = {} if outer is None else outer
    try:
        yield
    finally:
        _JVX_KEPT = outer


def jobvitefeed_read(ref) -> tuple[str, list[dict], list[dict], list[str]]:
    """(the account, the feed's <job>s by field, one row per job, what was
    noticed in words) - one request, or none inside jobvitefeed_kept() where
    the feed was read whole already - or AtsError: more than
    JOBVITE_FEED_SLACK rows that do not read, or none that does, is a feed
    this reader does not understand, never part of a board or a zero."""
    account = jobvitefeed_account(ref)
    url = JOBVITE_FEED.format(account=account)
    kept = _JVX_KEPT
    if kept is not None and url in kept:
        return kept[url]
    jobs = jobvitefeed_jobs(_jvx_body(url), url)
    rows, unread = jobvitefeed_rows(jobs, account)
    if unread > JOBVITE_FEED_SLACK or not rows:
        raise AtsError(f"{url}: {unread} of its {len(jobs)} rows did not read as a job of this "
                       f"account (no id, no title, or a job page that is not this account's), so "
                       f"it is not read in part")
    notes = []
    if unread:
        notes.append(f"{unread} of its {len(jobs)} rows did not read as a job of this account "
                     f"and are left out")
    if len(rows) >= JOBVITE_FEED_GIANT:
        notes.append(f"{len(rows)} openings: a giant by the owner's line (about "
                     f"{JOBVITE_FEED_GIANT})")
    if kept is not None:
        kept[url] = (account, jobs, rows, notes)
    return account, jobs, rows, notes


def fetch_jobvitefeed(ref) -> list[dict]:
    account, _jobs, rows, notes = jobvitefeed_read(ref)
    for line in notes:
        print(f"  jobvitefeed: {account}: {line}", file=sys.stderr)
    return rows

# ---- trinethire: TriNet Hire, app.trinethire.com/companies/<n>-<slug>/jobs/embed
#
# Four SLED vendors hire through TriNet Hire (research of 2026-11-23). Three
# were wired as html pages at /companies/<n>-<slug>/jobs, the CAREER page,
# which said "Currently no openings available." on all four boards read that
# day while /companies/<n>-<slug>/jobs/embed listed 7, 16, 1 and 6 for the
# same numbers: a false zero on every one. So this reads the EMBED - the list
# the employer's own careers page frames - and never the career page, which
# is no evidence of anything.
#
# ONE REQUEST IS THE WHOLE BOARD: server-rendered HTML, never paged (?page=2
# answered Polimorphic's 16 rows again), no JSON form (embed.json: 406). Its
# <title> is 'Open Jobs | {Company}', and its <ul id="jobs-list"> holds one
# <li class="form-group"> a posting: <a href="/companies/<n>-<slug>/jobs/
# <job id>-<slug>">{title}</a> and <p><strong>Location:</strong> {label} -
# {type}</p>. The label is the name the employer gave a location record
# ('Atlanta, GA - Remote', 'Codoxo Headquarters', 'Remote'), so it is the
# row's location as given, never read as a city; the type after the LAST
# ' - ' ('Full-time', 'Contractor') is no field of a row.
#
# THE REF IS '<number>-<slug>' AS FOUND (or the number alone), and the number
# alone selects the board: a stale slug (5502-fraudscope serves Codoxo) and a
# made-up one served the same list. Nothing is read from the slug, a name
# least of all, and every hop is held to this board's own list - the same
# number, on app.trinethire.com, at /companies/<n>[-<slug>]/jobs/embed - as
# its tenants share one host and differ by path (as Jobvite's do). A slug
# left empty ('<n>-': what parameterize leaves of a name with no ASCII letter
# or digit in it) is the number alone, as the finder records it (blind
# review 13's verifier, 2026-11-24: it had been refused, and its address
# filed as one naming no board).
#
# A ZERO ONLY WHERE TRINET SAYS SO, in the shape it was measured in on two
# empty boards (2026-11-24): the 'Open Jobs | ' title, NO <ul id="jobs-list">
# anywhere (TriNet draws the list only when it has rows), and a <div
# id="jobs"> whose heading reads "There are currently no open jobs
# available." with no link, list, table, form, division or control (button,
# input, select) inside it - the words in any other division are no zero -
# and no posting of the board named anywhere in the RAW page (its scripts,
# templates, noscript and comments too, a JSON '\/' read as '/'). An empty
# jobs-list is a shape TriNet does not serve - an empty mount point a script,
# a template or inline JSON would fill - and raises. A page without the
# title (the career page, a challenge page, anything else), a titled page
# with neither shape, a row that does not read as a posting of this board, a
# posting the page names outside its list, a pager (rel=next, a pagination
# class, a link to this board's list or career page with a query, 'View all'
# or 'Load more'), and any answer but a 200 are errors, never a zero and
# never part of a board.
#
# THE BOARD IS ITS NUMBER, compared as a number (a ref written 041234-x is
# board 41234, whose rows TriNet writes 41234), and a slug is anything Rails'
# parameterize leaves - letters, digits, '-' and '_', or nothing after the
# hyphen - none of which changes the path. A posting's address carries the
# company's CURRENT slug, which a rename changes (5502-fraudscope became
# 5502-codoxo), so the board's ids are keyed on the number and the job id
# alone (trinethire_posting_key, build_board.posting_id); the address
# published is the one the list gives.
#
# ONE PLAN ASKS THE LIST ONCE (blind review 13, 2026-11-24): the wiring door
# reads the board and then asks it whose it is, both this one list. Inside
# trinethire_kept() - wire_found.plan, and nothing else - a list read whole
# is kept in memory under the address it was asked at, as jobvitefeed_kept
# keeps a feed; a read that failed raised and is not kept.
#
# THE LIST ONLY: no posting page is asked, flag or no flag. app.trinethire.com's
# robots.txt states Cloudflare content signals (search=yes, ai-train=no,
# ai-input not set) and turns named AI crawlers away, so whether TriNet-hosted
# ad text is read at all is the owner's call, still pending (2026-11-24). The
# board states no pay and no date anywhere, so a row has no jd, no comp and
# no posted (the board keeps its first-seen day, as for every undated reader).
TRINETHIRE_HOST = "app.trinethire.com"
TRINETHIRE_TITLE = "Open Jobs | "
TRINETHIRE_PAGE_MAX = 2_000_000      # characters of a page read; more is refused, never cut
TRINETHIRE_NONE = "There are currently no open jobs available."   # TriNet's own words, measured
_TRINET_REF = re.compile(r"([0-9]{1,12})(?:-([A-Za-z0-9-]{0,200}))?")
# (a slug: what Rails' parameterize leaves - letters, digits, '-' and '_' -
# or nothing at all after the hyphen; none of it changes the path)
_TRINET_EMBED_PATH = re.compile(r"/companies/([0-9]{1,12})(?:-[A-Za-z0-9_-]{0,200})?/jobs/embed/?")
_TRINET_POSTING_PATH = re.compile(r"/companies/([0-9]{1,12})(?:-[A-Za-z0-9_-]{0,200})?/jobs/"
                                  r"([0-9]{1,12})(?:-[A-Za-z0-9_-]{0,300})?/?")
_TRINET_BOARD_PAGE = re.compile(r"/companies/([0-9]{1,12})(?:-[A-Za-z0-9_-]{0,200})?/jobs(?:/embed)?/?")
# a posting of a board, wherever a page names it: an attribute, a script's
# JSON, a template, a comment (the path alone, on any host, its slashes as
# '\/', '\u002f', '&#47;' or '%2F' read as '/')
_TRINET_ANY_POSTING = re.compile(r"/companies/([0-9]{1,12})(?:-[A-Za-z0-9_-]{0,200})?/jobs/"
                                 r"([0-9]{1,12})(?![0-9])")
_TRINET_SLASH = re.compile(r"\\/|\\u002f|&#0*47;|&#x0*2f;|%2f", re.I)
_TRINET_LIST = re.compile(r"<ul\b[^<>]{0,400}?\sid\s*=\s*[\"']jobs-list[\"'][^<>]{0,400}>", re.I)
_TRINET_JOBS_DIV = re.compile(r"<div\b[^<>]{0,400}?\sid\s*=\s*(?:\"jobs\"|'jobs'|jobs(?=[\s>]))[^<>]{0,400}>",
                              re.I)
_TRINET_HEADING = re.compile(r"<h[1-6](?=[\s/>])")
_TRINET_HREF = re.compile(r"""\shref\s*=\s*(?:"([^"<>]{0,800})"|'([^'<>]{0,800})'|([^\s"'<>]{1,800}))""",
                          re.I)
_TRINET_WHERE = re.compile(r"<strong\b[^<>]{0,200}>\s*Location:\s*</strong\s*>", re.I)
# A PAGE OF A LONGER LIST: a pager's rel or class, and words that ask for more
_TRINET_PAGER = re.compile(
    r"""\srel\s*=\s*(?:"[^"<>]{0,200}?\b(?:next|prev)\b|'[^'<>]{0,200}?\b(?:next|prev)\b|(?:next|prev)\b)"""
    r"""|\sclass\s*=\s*["']?[^"'<>]{0,200}?(?:paginat|\bpager\b|\bpage-(?:item|link|numbers)\b"""
    r"""|\bload-more\b|\bshow-more\b)""", re.I)
_TRINET_MORE = re.compile(r">\s*(?:(?:view|see|show|browse)\s+(?:all|more)\b|load\s+more\b"
                          r"|more\s+(?:jobs|openings|positions|roles|results)\b|next\s+page\b)", re.I)
# the employment type TriNet prints after a location's last ' - ' (measured:
# Full-time, Contractor); text after it that is no type is part of the label
_TRINET_KINDS = frozenset({"full-time", "full time", "fulltime", "part-time", "part time", "parttime",
                           "contractor", "contract", "temporary", "temp", "intern", "internship",
                           "seasonal", "per diem", "per-diem", "freelance", "volunteer"})


_TRINET_CAREER_PATH = re.compile(r"/companies/([0-9]{1,12})(?:-[A-Za-z0-9_-]{0,200})?/jobs/?")


def trinethire_career(url) -> str | None:
    """The board number of TriNet Hire's CAREER page at `url` -
    app.trinethire.com/companies/<n>[-<slug>]/jobs, any query - or None for
    any other address (its list, /jobs/embed, and a posting are not it)."""
    try:
        parts = urllib.parse.urlsplit(url if isinstance(url, str) else "")
    except ValueError:
        return None
    m = _TRINET_CAREER_PATH.fullmatch(parts.path or "")
    return m.group(1) if m and (parts.hostname or "").lower() == TRINETHIRE_HOST else None


def trinethire_not_career(url) -> None:
    """NEVER READ TRINET'S CAREER PAGE, by any reader (follow asks this of
    every hop before anything is sent). It said "Currently no openings
    available." on all four boards read on 2026-11-23 while their lists
    carried 30: read as a careers page (type html: three records on file
    point at it) it is a false "None found" every night. Refused, it is
    Unknown - and the render fallbacks, which open the same address through
    ats.resolve, are refused with it (blind review 13, 2026-11-24)."""
    number = trinethire_career(url)
    if number is not None:
        raise AtsError(f"{str(url)[:160]} is TriNet Hire's career page, which says it has no "
                       f"openings whatever its list carries: never read, and never evidence of "
                       f"zero (its list is /companies/{number}/jobs/embed, type trinethire)")


def trinethire_ref(ref) -> tuple[str, str]:
    """(the ref as found, the board's number), or AtsError. '<number>' or
    '<number>-<slug>', the slug letters, digits and hyphens: never an
    address, and never anything that changes the path it is put in. A slug
    left empty, '<number>-', is the number alone ('41234-' is '41234', read
    at /companies/41234/jobs/embed), as find_site_boards records it."""
    s = _text(ref)
    m = _TRINET_REF.fullmatch(s)
    if not m:
        raise AtsError(f"not a trinethire board: {ref!r} (a TriNet Hire board is "
                       f"'<number>-<name>', as app.trinethire.com/companies/<number>-<name> names it)")
    return (s if m.group(2) else m.group(1)), m.group(1)


def trinethire_url(ref) -> str:
    """The board's own list, its embed: what is read, and what a person is
    sent to (build_board.board_url). Never /jobs, the career page, which
    says it has no openings while the list carries them."""
    return f"https://{TRINETHIRE_HOST}/companies/{trinethire_ref(ref)[0]}/jobs/embed"


def _trinet_read(url: str, number: str) -> str:
    """The list page at `url`, every hop held to this board's own embed - on
    app.trinethire.com, at /companies/<number>[-<slug>]/jobs/embed with no
    query - and refused before it is asked anything. robots.txt at every hop
    (_page), the crawler's own name; any answer but a 200 is an error."""
    def stay(u: str):
        parts = urllib.parse.urlsplit(u)
        m = _TRINET_EMBED_PATH.fullmatch(parts.path or "")
        if (parts.scheme != "https" or (parts.netloc or "").lower() != TRINETHIRE_HOST
                or not m or int(m.group(1)) != int(number) or parts.query or parts.fragment):
            raise AtsError(f"{url} leads off to {str(u)[:160]}, which is not this board's own list "
                           f"(/companies/{number}-<name>/jobs/embed), so it is not read as a board "
                           f"with nothing open")
    return _page(url, same_host=True, may=stay).text or ""


def _trinet_posting(href: str, base: str) -> tuple[str, str, str] | None:
    """(the posting's address, its board's number, its job id) for an href
    that is a posting on app.trinethire.com - joined to the list's own
    address (`base`), as a browser joins it, its query and fragment dropped
    (?source=...) - or None."""
    try:
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(base, html_lib.unescape(href or "").strip()))
    except ValueError:
        return None
    m = _TRINET_POSTING_PATH.fullmatch(parts.path or "")
    if parts.scheme not in ("http", "https") or (parts.netloc or "").lower() != TRINETHIRE_HOST or not m:
        return None
    return f"https://{TRINETHIRE_HOST}{parts.path}", m.group(1), m.group(2)


def trinethire_location(said: str) -> str:
    """The label of a row's 'Location:' text, '{label} - {type}': the type
    after the LAST ' - ' is dropped only where it IS an employment type
    (labels hold dashes of their own: 'Atlanta, GA - Remote - Full-time'),
    and a type left blank ('Atlanta, GA - Remote - ') leaves the label whole.
    Text after the last ' - ' that is no type ('Remote - US') is the label's.
    The space put back in front keeps a label left blank (' - Full-time')
    a blank label."""
    s = _text(said)
    if s == "-" or s.endswith(" -"):                  # the type left blank
        return s[:-1].strip()
    label, sep, kind = (" " + s).rpartition(" - ")
    return label.strip() if sep and _text(kind).lower() in _TRINET_KINDS else s


def _trinet_named(raw: str, number: str) -> set:
    """The job id of every posting of board `number` the RAW page names
    anywhere - a script's JSON, a template, a noscript, a comment, an
    attribute, any host - its escaped slashes read as '/'."""
    n, text = int(number), _TRINET_SLASH.sub("/", raw or "")
    return {str(int(m.group(2))) for m in _TRINET_ANY_POSTING.finditer(text) if int(m.group(1)) == n}


def _trinet_none_open(body: str, low: str) -> bool:
    """True where the cut page is TriNet's own word that the board has nothing
    open, as measured on two empty boards (2026-11-24): a <div id="jobs">
    whose first heading reads exactly TRINETHIRE_NONE, holding no link, list,
    table, form, division or control (button, input, select) of its own.
    The words in any other division are not it. Each of these rules is
    pinned by a case of its own (blind review 13's verifier cut the id rule,
    and the barred tags to a, ul and li, and nothing had failed)."""
    m = _TRINET_JOBS_DIV.search(body)
    close = low.find("</div", m.end()) if m else -1
    if close < 0:
        return False
    inner, inner_low = body[m.end():close], low[m.end():close]
    if any(_tag_at(inner_low, t, 0) >= 0 for t in ("a", "ul", "ol", "li", "table", "form", "div",
                                                    "button", "input", "select")):
        return False
    h = _TRINET_HEADING.search(inner_low)
    h_gt = inner_low.find(">", h.end()) if h else -1
    h_end = inner_low.find("</h", h_gt) if h_gt >= 0 else -1
    return h_end >= 0 and _jv_text(inner[h_gt + 1:h_end]).lower() == TRINETHIRE_NONE.lower()


def _trinet_more(body: str, outside: str, number: str, base: str) -> str:
    """Why the cut page shows itself to be one page of a longer list, or "":
    a pager's rel (next, prev) or class (pagination, pager, load-more), a link
    to this board's list or career page with a query (?page=2), or words
    that ask for more ('View all 40 jobs', 'Load more') on the page outside
    its rows (`outside`: a posting may be titled anything)."""
    m = _TRINET_PAGER.search(body)
    if m:
        return f"carries a pager ({m.group(0).strip()[:60]!r})"
    for m in _TRINET_HREF.finditer(body):
        try:
            parts = urllib.parse.urlsplit(urllib.parse.urljoin(
                base, html_lib.unescape(next((g for g in m.groups() if g is not None), "")).strip()))
        except ValueError:
            continue
        page = _TRINET_BOARD_PAGE.fullmatch(parts.path or "")
        if (page and parts.query and (parts.netloc or "").lower() == TRINETHIRE_HOST
                and int(page.group(1)) == int(number)):
            return f"links its own list with a query ({parts.path}?{parts.query[:40]})"
    m = _TRINET_MORE.search(outside)
    if m:
        return f"asks for more ({_text(m.group(0).lstrip('>'))[:40]!r})"
    return ""


def trinethire_rows(page: str, number: str, url: str | None = None) -> tuple[str, list[dict]]:
    """(the name its <title> states after 'Open Jobs | ', "" for none; one row
    a posting) of board `number`'s list page, read from `url` (its hrefs are
    joined to it): rows where the page is the list, none where it is
    TriNet's own word that nothing is open, or AtsError for a page that is
    neither, or whose list does not read whole (see above)."""
    base = url or f"https://{TRINETHIRE_HOST}/companies/{number}/jobs/embed"
    url = url or "the list"
    if len(page or "") > TRINETHIRE_PAGE_MAX:
        raise AtsError(f"{url} is more than {TRINETHIRE_PAGE_MAX:,} characters: not read, and "
                       f"never read in part")
    n = int(number)
    # (the title from the CUT page: a <title> commented out, or an SVG's, is
    # not the document's - seventh blind review's rule, held in _walk)
    body = _cut(page or "")
    low = lower_same(body)
    t0 = _tag_at(low, "title", 0)
    t1 = low.find("</title", t0) if t0 >= 0 else -1
    gt = low.find(">", t0) if t0 >= 0 else -1
    title = _text(html_lib.unescape(body[gt + 1:t1])) if 0 <= gt < t1 else ""
    if not (title == TRINETHIRE_TITLE.strip() or title.startswith(TRINETHIRE_TITLE)):
        raise AtsError(f"{url} is not a TriNet Hire list (its <title> is not 'Open Jobs | '): a "
                       f"career page, a challenge or another page, never a board with nothing open")
    name = title[len(TRINETHIRE_TITLE):].strip() if title.startswith(TRINETHIRE_TITLE) else ""
    # every posting of the board the RAW page names, wherever it names it
    named = _trinet_named(page or "", number)
    ul = _TRINET_LIST.search(body)
    if not ul:
        # NOTHING OPEN, IN TRINET'S OWN WORDS: no jobs-list anywhere in the
        # raw page (a template, a noscript or a script that holds one is a
        # list this reader cannot see), no posting of the board named
        # anywhere, and the 'no open jobs' heading alone in <div id="jobs">
        more = _trinet_more(body, body, number, base)
        if (_trinet_none_open(body, low) and not named and not more
                and "jobs-list" not in lower_same(page or "")):
            return name, []
        raise AtsError(f"{url} is titled 'Open Jobs | ' but is neither its jobs-list nor TriNet's "
                       f"own '{TRINETHIRE_NONE}' alone"
                       + (f" ({more})" if more else f" ({len(named)} posting(s) of board {number} "
                                                       f"named on it)" if named else "")
                       + ": a template this reader does not know, never a board with nothing open")
    close = low.find("</ul", ul.end())
    if close < 0 or _tag_at(low[:close], "ul", ul.end()) >= 0:
        raise AtsError(f"{url}: its jobs-list is cut short or holds a list of its own, so it is "
                       f"not read in part")
    more = _trinet_more(body, body[:ul.end()] + body[close:], number, base)
    if more:
        raise AtsError(f"{url} {more}: one page of a longer list is never read as the whole board")
    inner, inner_low = body[ul.end():close], low[ul.end():close]
    rows, ids, i = [], set(), _tag_at(inner_low, "li", 0)
    while i >= 0:
        nxt = _tag_at(inner_low, "li", i + 3)
        row = inner[i:nxt if nxt >= 0 else len(inner)]
        a = _tag_at(lower_same(row), "a", 0)
        a_end = row.find(">", a) if a >= 0 else -1
        a_close = lower_same(row).find("</a", a_end) if a_end >= 0 else -1
        href = _TRINET_HREF.search(row[a:a_end + 1]) if a_end >= 0 else None
        got = _trinet_posting(next((g for g in href.groups() if g is not None), ""), base) if href else None
        title_ = _jv_text(row[a_end + 1:a_close]) if a_close >= 0 else ""
        if not got or int(got[1]) != n or not title_:
            raise AtsError(f"{url}: a row of its list does not read as a posting of board {number} "
                           f"(its link, its board's number or its title), so the board is not read "
                           f"in part")
        job = str(int(got[2]))
        if job not in ids:                             # one posting listed twice is one posting
            ids.add(job)
            where = _TRINET_WHERE.search(row)
            if where:
                p_end = lower_same(row).find("</p", where.end())
                said = _jv_text(row[where.end():p_end if p_end >= 0 else len(row)])
            else:
                said = ""
            rows.append({"title": title_, "location": trinethire_location(said), "url": got[0]})
        i = nxt
    if not rows:
        # TriNet draws no jobs-list for a board with nothing open (measured
        # 2026-11-24): an empty one is a mount point something else fills
        raise AtsError(f"{url}: its jobs-list holds no posting, which TriNet does not serve (a "
                       f"board with nothing open has no list at all), so it is not read as a zero")
    # ...and every posting of the board the page names - linked, or anywhere
    # in its raw text - is a row of its list: rows in some other markup are
    # a template this reader does not know, never a board read in part
    linked = set(named)
    for m in _TRINET_HREF.finditer(body):
        got = _trinet_posting(next((g for g in m.groups() if g is not None), ""), base)
        if got and int(got[1]) == n:
            linked.add(str(int(got[2])))
    if linked - ids:
        raise AtsError(f"{url} names {len(linked - ids)} posting(s) of board {number} its jobs-list "
                       f"does not carry, so the list is not read as the whole board")
    return name, rows


_TRINET_KEPT: dict | None = None


@contextlib.contextmanager
def trinethire_kept():
    """Each list read whole is the answer to every later ask of it in the
    block (see ONE PLAN ASKS THE LIST ONCE above). Also a decorator: each
    call is a block of its own."""
    global _TRINET_KEPT
    outer = _TRINET_KEPT
    _TRINET_KEPT = {} if outer is None else outer
    try:
        yield
    finally:
        _TRINET_KEPT = outer


def trinethire_board(ref) -> tuple[str, str, list[dict]]:
    """(the list's address, the name its title states or "", its rows): one
    request - none inside trinethire_kept() where the list was read whole
    already - or AtsError."""
    _ref, number = trinethire_ref(ref)
    url = trinethire_url(ref)
    kept = _TRINET_KEPT
    if kept is not None and url in kept:
        name, rows = kept[url]
        return url, name, [dict(r) for r in rows]
    name, rows = trinethire_rows(_trinet_read(url, number), number, url)
    if kept is not None:
        kept[url] = (name, [dict(r) for r in rows])
    return url, name, rows


def trinethire_posting_key(url) -> str | None:
    """The posting a TriNet Hire address names, as the board knows it - its
    company number and its job id, https://app.trinethire.com/companies/<n>/
    jobs/<job id> - or None for any other address. The list's links carry the
    company's CURRENT slug, which a rename changes (5502-fraudscope is now
    5502-codoxo), and the job's slug follows its title: build_board.posting_id
    hashes this key, so a posting keeps its id, its first-seen day and every
    saved link across a rename. It is a key, never an address anybody is
    sent to: the address published is the one the list gives."""
    try:
        parts = urllib.parse.urlsplit(url if isinstance(url, str) else "")
    except ValueError:
        return None
    m = _TRINET_POSTING_PATH.fullmatch(parts.path or "")
    if (parts.netloc or "").lower() != TRINETHIRE_HOST or not m:
        return None
    return f"https://{TRINETHIRE_HOST}/companies/{int(m.group(1))}/jobs/{int(m.group(2))}"


def fetch_trinethire(ref) -> list[dict]:
    return trinethire_board(ref)[2]

# ---- jobscore: JobScore, careers.jobscore.com/careers/<slug>
#
# Two companies on file hire through JobScore (research of 2026-11-23):
# ImageTrend, read as a careers page (type html) at its hosted list - titles
# and links, nothing else - and Finalsite, whose careers page frames
# JobScore's widget (widgets.jobscore.com), which no careers-page reader can
# see: no row at all. Each board publishes one Atom feed, which its own
# hosted page links as rel=alternate and robots.txt allows (it closes
# /apply_flow/ and nothing else):
#
#     GET https://careers.jobscore.com/jobs/<slug>/feed.atom
#
# ONE REQUEST IS THE WHOLE BOARD: no paging offered and none needed (8 of 8
# and 14 of 14 rows against the hosted list, the same addresses, 2026-11-23;
# 8 and 13 on 2026-11-24). Read through ats.follow and _one - robots.txt at
# every hop, the crawler's own name, the host gate - as a stream, and the
# feed answers in place, so any redirect (another host, another path, the
# feed again) is refused before it is asked anything (_jobscore_read). A
# feed past JOBSCORE_FEED_MAX bytes is refused, never read in part, and no
# more of it is taken off the wire than the cap and one chunk (blind review
# of 2026-11-24: read whole first, a 40 MB answer was pulled down to the last
# byte before it was refused). A feed that says it is one page of a longer
# one raises: one page is never the whole board - an Atom link rel first,
# last or current, or any rel naming a next, a previous or an archive page
# (RFC 5005's next-archive and prev-archive; a rel spelled as IANA's URI,
# http://www.iana.org/assignments/relation/next, is the same rel, RFC 4287),
# a self link carrying a query (?page=1), an OpenSearch totalResults above
# the entries it holds or a startIndex past the first; and so does an
# <entry> anywhere but directly under <feed> (wrapped in a vendor's element,
# it would have been skipped while the rest were read).
#
# THE REF IS THE ACCOUNT'S SLUG ('imagetrend', 'finalsite'), AS SPELLED -
# never lowered: JobScore minds the case of the path (asked 2026-11-24,
# /jobs/Finalsite/feed.atom answered 404 and /jobs/finalsite/feed.atom is the
# board), so a slug in other capitals is another address, never a guess at
# this one (find_site_boards.LOWERED and the Lever lesson) - and held to one
# DNS label's shape as Pinpoint's is: never an address, never anything that
# changes the path it is put in.
#
# PARSED AS XML, BY NAME AND BY PLACE, AND NOTHING ELSE IS READ: expat with
# namespaces, a DOCTYPE or an ENTITY declaration refused before anything it
# declares is read, the root Atom's <feed>. Of the feed: its <title>,
# <subtitle> and <link>s. Of each <entry>: its <title>, <link>s,
# <category>s, <content> and the five fields of its <j:publicCompensation>.
# The characters of every other element are dropped as the parser meets
# them, so <j:hiringTeam> - hiring managers and recruiters by name, job
# title, LinkedIn address and photo (23 people on ImageTrend's 8 entries) -
# is never read, nor is <author><name> ('ImageTrend HR'); and jobscore.com
# is in NEVER_CACHED, so the feed is never written to data/http_cache.
#
# A ROW: the title, stripped (they come with trailing spaces, one with a
# leading tab); the url, the entry's ONE rel=alternate link, a posting of
# this very board - https://careers.jobscore.com/careers/<slug>/jobs/
# [<title slug>-]<22-character id> - its tracking query (?ref=rss&sid=68)
# dropped, and NEVER the apply_url link, which points under /apply_flow/,
# which robots.txt closes; the location, the atom#location category's term
# as the employer typed it ('Remote in Eagan, MN', '100% Remote in the U.S',
# 'Remote- LatAm'), no country invented; the ad, the entry's <content
# type="html">; and pay from <j:publicCompensation>, whose salaryMinimum and
# salaryMaximum count CENTS (5000000 is $50,000), published only where the
# block's own <j:formatted> ('From $50,000 to $60,000 per year'), which is
# kept as raw, agrees with all of it: the very figures, no period but the
# block's <j:interval>, and no currency sign or code but one its
# <j:currencyCode> is ('$' for USD, CAD and the other dollars; none named is
# US dollars) - a comp never contradicts its own raw (blind review of
# 2026-11-24). One row a posting id: a posting listed twice is one. An entry
# that does not read as a posting of this board (no title, two titles, or
# not one alternate link to a posting under /careers/<slug>/jobs/) raises: a
# board is never read in part.
#
# NO POSTED DATE. The feed has no <published>; <updated> moves on every edit
# (all 8 of ImageTrend's entries read 2026-11-10 or 2026-11-20), and the day
# inside <id> is the day the job was CREATED (ImageTrend's Account Executive:
# 2026-10-07, posted 2026-10-19). Neither is read, so the board keeps its
# first-seen day. A posting page's JSON-LD states datePosted; it is not read
# (one request a posting, and the page prints the recruiter's name), flag or
# no flag.
#
# NO ZERO, BECAUSE NONE IS MEASURED. No empty JobScore board was at hand
# (2026-11-23 and 11-24), so whether an empty feed keeps its title and
# subtitle is not known: a feed with no <entry> raises - Unknown, never "None
# found" - until one is read. A slug that is not there answers 404 (measured,
# the feed and the page alike), which is gone, never a zero; so is any other
# answer but a 200.
#
# WHOSE IT IS (jobscore_name): the feed's <subtitle>, the bare name, believed
# only where its <title> reads 'Careers at ' and the same name - ImageTrend's
# and Finalsite's, 2026-11-23 and 11-24. Never <author><name>, the slug, or
# any person. And the feed must name this board's own hosted page as its
# alternate link (https://careers.jobscore.com/careers/<slug>): a feed that
# names another is not this board's, and raises.
JOBSCORE_HOST = "careers.jobscore.com"
JOBSCORE_FEED = "https://careers.jobscore.com/jobs/{slug}/feed.atom"
JOBSCORE_PAGE = "https://careers.jobscore.com/careers/{slug}"
JOBSCORE_TITLE = "Careers at "
# Bytes of a feed read; more is refused, never read in part. A posting is 9
# to 12 KB of feed (93 KB for ImageTrend's 8, 125 KB for Finalsite's 14), so
# this is some 1,300 postings - past the owner's giants of about 500.
JOBSCORE_FEED_MAX = 16_000_000
JOBSCORE_LOCATION = "http://schemas.jobscore.com/atom#location"
_JOBSCORE_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_ATOM_NS = "http://www.w3.org/2005/Atom|"
_JOBSCORE_NS = "http://schemas.jobscore.com/atom|"
# OpenSearch's namespaces (1.1's, and RSS 1.0's before it), whose
# totalResults and startIndex say how much of a longer list a page is
_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch"
_OPENSEARCH = ("totalResults", "startIndex", "itemsPerPage")
# a rel written as IANA's registry names it is the short rel (RFC 4287 4.2.7.2)
_IANA_REL = ("http://www.iana.org/assignments/relation/", "https://www.iana.org/assignments/relation/")
# a posting of board <slug>: its 22-character id last, the title's slug and a
# hyphen before it (or nothing: a title with no ASCII letter or digit)
_JOBSCORE_POSTING = re.compile(r"/careers/([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)/jobs/"
                               r"(?:[a-z0-9_-]{0,300}-)?([A-Za-z0-9_-]{22})")
# THE ONLY PAY FIELDS EVER READ, inside <j:publicCompensation>
_JOBSCORE_PAY = ("salaryMinimum", "salaryMaximum", "interval", "currencyCode", "formatted")
# what <j:interval> says, word for word (measured: 'per year' on all nine
# pay blocks); anything else is no period (the figures, where the formatted
# line agrees, are published with none)
_JOBSCORE_PERIODS = {"per year": "year", "per month": "month", "per week": "week",
                     "per day": "day", "per hour": "hour"}
# THE PERIOD A FORMATTED LINE NAMES, word by word: a line naming one its
# block's <j:interval> does not ('From $25 to $30 per hour' over 'per year')
# states no pay. 'odd' is a period none of the five is (every two weeks,
# biweekly, semimonthly), and never agrees with one
_JOBSCORE_PERIOD_WORDS = {
    **dict.fromkeys(("year", "years", "yr", "yrs", "annum", "annual", "annually", "yearly"), "year"),
    **dict.fromkeys(("month", "months", "monthly"), "month"),
    **dict.fromkeys(("week", "weeks", "weekly", "wk", "wks"), "week"),
    **dict.fromkeys(("day", "days", "daily"), "day"),
    **dict.fromkeys(("hour", "hours", "hourly", "hr", "hrs"), "hour")}
_JOBSCORE_ODD_PERIOD = re.compile(
    r"\b(?:bi|semi)[\s-]?(?:weekly|monthly|annual(?:ly)?|week|month)\b|\bfortnight"
    r"|\bevery\s+(?:other|[a-z0-9]+)\s+(?:years?|months?|weeks?|days?|hours?)\b", re.I)
# THE CURRENCIES A SIGN STANDS FOR in a formatted line: a sign not listed
# fits no code, and a block naming no code is US dollars ('$' or nothing)
_JOBSCORE_DOLLARS = frozenset({"USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN", "TWD", "BSD", "BMD",
                               "BBD", "BZD", "JMD", "TTD", "XCD", "FJD", "LRD", "NAD", "SBD", "SRD",
                               "GYD", "KYD", "BND", "CLP", "COP", "ARS", "UYU", "DOP", "CUP"})
_JOBSCORE_SIGNS = {"$": _JOBSCORE_DOLLARS, "€": frozenset({"EUR"}), "£": frozenset({"GBP"}),
                   "₹": frozenset({"INR"}), "¥": frozenset({"JPY", "CNY"}),
                   "₱": frozenset({"PHP"}), "₩": frozenset({"KRW"})}
_JOBSCORE_FIGURE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")
_JOBSCORE_CENTS = re.compile(r"[0-9]{1,13}")
_MARKUP = object()        # a field that held markup of its own: not read


class _JobscoreRefused(Exception):
    """A feed the parser stops reading at once (a DOCTYPE or an ENTITY, a root
    that is not Atom's <feed>), raised out of an expat handler."""


def jobscore_slug(ref) -> str:
    """The JobScore account a ref names - its slug, as spelled: JobScore
    minds its case - or AtsError (for anything else, a number or a list among
    them: a ref is text)."""
    slug = _text(ref) if isinstance(ref, str) else ""
    if not _JOBSCORE_SLUG.fullmatch(slug):
        raise AtsError(f"not a jobscore board: {ref!r} (a JobScore board is its account's slug, "
                       f"as careers.jobscore.com/careers/<slug> names it)")
    return slug


def jobscore_url(ref) -> str:
    """The board's feed: what is read."""
    return JOBSCORE_FEED.format(slug=jobscore_slug(ref))


def jobscore_page(ref) -> str:
    """The board's hosted list, where a person looks (build_board.board_url):
    every opening, each linking its posting. Never read by this reader."""
    return JOBSCORE_PAGE.format(slug=jobscore_slug(ref))


def _jobscore_read(url: str) -> bytes:
    """The feed's bytes, asked once: robots.txt at every hop (follow), the
    crawler's own name and the host gate (_one); no redirect followed - the
    feed answers in place, so a hop anywhere, the feed itself again included,
    is refused before it is asked anything. Read as a stream, as
    _jvx_body reads a Jobvite feed, and refused past JOBSCORE_FEED_MAX
    bytes - never read in part, and never pulled down whole to be refused. A
    429 is honoured at the gate and raised as RateLimited; any other answer
    but a 200 is an error (a slug that is not there answers 404: gone); the
    network failing on the way, mid-body included, is a network error. Never
    the HTTP cache: _one keeps nothing (and jobscore.com is NEVER_CACHED)."""
    hops: list[str] = []

    def stay(u: str):
        hops.append(u)
        if u != url or len(hops) > 1:
            raise AtsError(f"{url} leads off to {str(u)[:160]}, which is not this board's own feed "
                           f"(the feed answers in place), so it is not read as a board with "
                           f"nothing open")
    resp = follow(url, lambda u: _one(u, stream=True), may=stay)
    try:
        status = getattr(resp, "status_code", None)
        if status == 429:
            _back_off(url, resp)
        if status != 200:
            raise AtsError(f"HTTP {status} for {url}")
        raw = bytearray()
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                raw += chunk
                if len(raw) > JOBSCORE_FEED_MAX:
                    raise AtsError(f"{url} is more than {JOBSCORE_FEED_MAX:,} bytes, this reader's "
                                   f"cap: it is not read, and never read in part")
        except requests.RequestException as exc:
            raise AtsError(f"network error: {exc}") from exc
    finally:
        try:
            resp.close()
        except Exception:                          # noqa: BLE001
            pass
    return bytes(raw)


def jobscore_feed(raw: bytes, url: str = "the feed") -> dict:
    """What a feed says in the places this reader reads, and nothing else:
    {"title", "subtitle", "links", "entries"}, each entry {"title", "links",
    "categories", "content", "content_type", "pay", "pay_blocks"}. A text
    field is a list holding what it said each time it appeared (_MARKUP where
    it held markup of its own); a link is (rel, href) - an Atom link with no
    rel is 'alternate' - and a category (scheme, term).

    The characters of every element not named here are never kept: the
    parser drops them as it meets them, the hiring team's and the author's
    included. Also kept: the feed's OpenSearch totalResults, startIndex and
    itemsPerPage, under "opensearch". AtsError for a feed that declares a
    DOCTYPE or an ENTITY, does not read as XML (cut short, a bare '&', an
    entity XML does not define, junk after it), whose root is not Atom's
    <feed>, or that holds an element named entry anywhere but an Atom <entry>
    directly under it (a posting wrapped in another element would be lost
    while the rest were read)."""
    feed: dict = {"title": [], "subtitle": [], "links": [], "entries": [], "opensearch": {}}
    stack: list[str] = []
    here: dict = {"entry": None, "into": None, "key": None, "depth": 0}

    def refuse(*_args):
        raise _JobscoreRefused("declares a DOCTYPE or an ENTITY")

    def begin(into: dict, key: str):
        into.setdefault(key, []).append([])
        here.update(into=into, key=key, depth=len(stack))

    def rel(attrs) -> str:
        said = _text(attrs.get("rel")).lower() or "alternate"
        for lead in _IANA_REL:
            said = said.removeprefix(lead)
        return said

    def start(name, attrs):
        stack.append(name)
        depth = len(stack)
        if depth > 1 and name.rsplit("|", 1)[-1] == "entry" and (depth != 2 or name != _ATOM_NS + "entry"):
            raise _JobscoreRefused(f"holds an <entry> other than directly under its <feed> "
                                   f"(<{name[-60:]}> at depth {depth}), whose posting would be lost")
        if here["into"] is not None:
            # markup inside a field this reads: that field is not read
            here["into"][here["key"]][-1] = _MARKUP
            return
        if depth == 1:
            if name != _ATOM_NS + "feed":
                raise _JobscoreRefused(f"is not an Atom feed (its root is <{name[-60:]}>)")
            return
        parent, entry = stack[-2], here["entry"]
        if depth == 2:
            if name == _ATOM_NS + "entry":
                here["entry"] = {"title": [], "links": [], "categories": [], "content": [],
                                 "content_type": None, "pay": {}, "pay_blocks": 0}
            elif name in (_ATOM_NS + "title", _ATOM_NS + "subtitle"):
                begin(feed, name[len(_ATOM_NS):])
            elif name == _ATOM_NS + "link":
                feed["links"].append((rel(attrs), attrs.get("href") or ""))
            elif name.startswith(_OPENSEARCH_NS) and name.rsplit("|", 1)[-1] in _OPENSEARCH:
                begin(feed["opensearch"], name.rsplit("|", 1)[-1])
        elif depth == 3 and parent == _ATOM_NS + "entry" and entry is not None:
            if name == _ATOM_NS + "title":
                begin(entry, "title")
            elif name == _ATOM_NS + "link":
                entry["links"].append((rel(attrs), attrs.get("href") or ""))
            elif name == _ATOM_NS + "category":
                entry["categories"].append((attrs.get("scheme") or "", attrs.get("term") or ""))
            elif name == _ATOM_NS + "content":
                entry["content_type"] = (_text(attrs.get("type")) or "text").lower()
                begin(entry, "content")
            elif name == _JOBSCORE_NS + "publicCompensation":
                entry["pay_blocks"] += 1
        elif (depth == 4 and parent == _JOBSCORE_NS + "publicCompensation"
              and stack[-3] == _ATOM_NS + "entry" and entry is not None
              and name.startswith(_JOBSCORE_NS) and name[len(_JOBSCORE_NS):] in _JOBSCORE_PAY):
            begin(entry["pay"], name[len(_JOBSCORE_NS):])

    def end(name):
        depth = len(stack)
        if here["into"] is not None and depth == here["depth"]:
            parts = here["into"][here["key"]]
            if isinstance(parts[-1], list):
                parts[-1] = "".join(parts[-1])
            here["into"] = None
        elif depth == 2 and name == _ATOM_NS + "entry" and here["entry"] is not None:
            feed["entries"].append(here["entry"])
            here["entry"] = None
        stack.pop()

    def text(data):
        into = here["into"]
        if into is not None and len(stack) == here["depth"] and isinstance(into[here["key"]][-1], list):
            into[here["key"]][-1].append(data)

    p = xml.parsers.expat.ParserCreate(namespace_separator="|")
    p.buffer_text = True
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    p.UnparsedEntityDeclHandler = refuse
    p.NotationDeclHandler = refuse
    p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, text
    try:
        p.Parse(raw, True)
    except _JobscoreRefused as exc:
        raise AtsError(f"{url} {exc}: refused, and nothing it lists is read") from None
    except xml.parsers.expat.ExpatError as exc:
        raise AtsError(f"{url} does not read as XML ({exc}): a feed cut short, a challenge page or "
                       f"not a feed, and never read in part") from None
    return feed


def _js_one(fields: dict, key: str):
    """The one text a field was given, or None: absent, given more than
    once, or holding markup of its own."""
    got = fields.get(key) or []
    return got[0] if len(got) == 1 and isinstance(got[0], str) else None


def jobscore_name(feed: dict) -> str:
    """The employer a feed states, or "": its <subtitle>, where its <title>
    reads 'Careers at ' and the same name. Both must agree."""
    sub = _text(html_lib.unescape(_js_one(feed, "subtitle") or ""))
    title = _text(html_lib.unescape(_js_one(feed, "title") or ""))
    return sub if sub and title == JOBSCORE_TITLE + sub else ""


def jobscore_posting(href, slug: str | None = None) -> tuple[str, str] | None:
    """(the posting's address, its 22-character id) where `href` is a
    posting on careers.jobscore.com - https, /careers/<slug>/jobs/[<title
    slug>-]<id>, of board `slug` when one is given - its query (?ref=rss&
    sid=68) and fragment dropped; or None for any other address."""
    try:
        parts = urllib.parse.urlsplit(_text(href))
    except ValueError:
        return None
    m = _JOBSCORE_POSTING.fullmatch(parts.path or "")
    if (parts.scheme != "https" or (parts.netloc or "").lower() != JOBSCORE_HOST or not m
            or (slug is not None and m.group(1) != slug)):
        return None
    return f"https://{JOBSCORE_HOST}{parts.path}", m.group(2)


def jobscore_posting_key(url) -> str | None:
    """The address a JobScore posting is known by - the posting's own
    address, query dropped - or None for any other address. One address is
    one posting (its 22-character id names it), so build_board.posting_id
    hashes this alone, never a place beside it: a posting first read through
    the hosted list (type html, which states no place) keeps its id, its
    first-seen day and its saved links when the feed is read instead."""
    got = jobscore_posting(url)
    return got[0] if got else None


def _jobscore_cents(raw) -> tuple[int | float | None, bool]:
    """(whole units from a count of cents, whether it read). Blank or zero
    is no figure; a figure in cents ('5000000') is $50,000, and '2250' stays
    22.5 - nothing is rounded the feed did not round."""
    s = _text(raw)
    if not s:
        return None, True
    if not _JOBSCORE_CENTS.fullmatch(s):
        return None, False
    cents = int(s)
    if cents <= 0:
        return None, True
    units = cents / 100
    return (int(units) if units.is_integer() else round(units, 2)), True


def _jobscore_formatted_agrees(formatted: str, period: str | None, currency: str | None) -> bool:
    """Whether a block's formatted line says nothing its own fields do not:
    no period but `period` (the block's <j:interval>, where it is one of the
    five; where it is none, any the line names stands as raw), and no
    currency sign or code but one `currency` is - a block naming no code is
    US dollars, '$' or nothing. A sign this does not know fits no code."""
    said = {_JOBSCORE_PERIOD_WORDS[w] for w in re.findall(r"[a-z]+", formatted.lower())
            if w in _JOBSCORE_PERIOD_WORDS}
    if _JOBSCORE_ODD_PERIOD.search(formatted):
        said.add("odd")
    if period is not None and said - {period}:
        return False
    want = currency or "USD"
    signs = {ch for ch in formatted if unicodedata.category(ch) == "Sc"}
    codes = set(re.findall(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])", formatted))
    return all(want in _JOBSCORE_SIGNS.get(s, ()) for s in signs) and codes <= {want}


def _jobscore_pay(entry: dict, abroad: bool = False) -> dict | None:
    """The contract's comp block from the entry's own <j:publicCompensation>,
    source "ats" - or None where it has none, has two, or states figures that
    do not read or that its own <j:formatted> does not state: the cents,
    converted, must be the very figures the formatted line prints ('From
    $50,000 to $60,000 per year'), which is kept as raw, and the line may name
    no period but the block's interval and no currency but the block's own
    (_jobscore_formatted_agrees): a comp never contradicts its raw. A row the
    board lists outside the US (`abroad`) whose block names no currency code
    states no pay published (a block with none is read as US dollars on the
    site)."""
    if entry.get("pay_blocks") != 1:
        return None
    pay = entry.get("pay") or {}
    said = {k: _js_one(pay, k) for k in _JOBSCORE_PAY}
    if any(said[k] is None and k in pay for k in _JOBSCORE_PAY):
        return None                                # given twice, or holding markup
    (lo, lo_ok), (hi, hi_ok) = _jobscore_cents(said["salaryMinimum"]), _jobscore_cents(said["salaryMaximum"])
    if not (lo_ok and hi_ok) or (lo is None and hi is None):
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    formatted = _text(said["formatted"])
    printed = {round(float(f.replace(",", "")), 2) for f in _JOBSCORE_FIGURE.findall(formatted)}
    if not formatted or printed != {round(float(x), 2) for x in (lo, hi) if x is not None}:
        return None
    code = _text(said["currencyCode"]).upper()
    currency = code if re.fullmatch(r"[A-Z]{3}", code) else None
    if currency is None and abroad:
        return None
    period = _JOBSCORE_PERIODS.get(_text(said["interval"]).lower())
    if not _jobscore_formatted_agrees(formatted, period, currency):
        return None
    return {"min": lo, "max": hi, "currency": currency, "period": period,
            "source": "ats", "raw": formatted}


def _jobscore_pager(rel: str) -> bool:
    """Whether a feed link's rel (IANA's URI form already folded) points to
    another page of the same list: first, last or current (an archive
    document's), or any rel naming a next, a previous or an archive page -
    RFC 5005's next-archive and prev-archive among them."""
    return rel in ("first", "last", "current") or any(w in rel for w in ("next", "prev", "archive"))


def jobscore_rows(feed: dict, slug: str, url: str = "the feed") -> list[dict]:
    """One row a posting of a board's feed (jobscore_feed), or AtsError for a
    feed that is not this board's whole list (see above)."""
    page = JOBSCORE_PAGE.format(slug=slug)
    alternates = [_text(h).rstrip("/") for rel, h in feed["links"] if rel == "alternate"]
    if alternates != [page]:
        raise AtsError(f"{url} does not name this board's own page ({page}) as its one alternate "
                       f"link ({alternates[:3]}), so it is not read as this board")
    pager = sorted({rel for rel, _h in feed["links"] if _jobscore_pager(rel)})
    if pager:
        raise AtsError(f"{url} links a {pager[0]!r} page of itself: one page of a longer feed is "
                       f"never read as the whole board")
    for rel, href in feed["links"]:
        try:
            query = urllib.parse.urlsplit(_text(href)).query if rel == "self" else ""
        except ValueError:
            query = "unreadable"
        if query:
            raise AtsError(f"{url} calls itself {_text(href)[:160]!r}: a feed that knows itself by a "
                           f"query (?page=) may be one page of a longer one, never read as the whole "
                           f"board")
    if not feed["entries"]:
        raise AtsError(f"{url} lists no posting: an empty JobScore feed has not been measured, so it "
                       f"is never read as a board with nothing open")
    for key, most in (("totalResults", len(feed["entries"])), ("startIndex", 1)):
        if key in feed["opensearch"]:
            n = _text(_js_one(feed["opensearch"], key) or "")
            if not re.fullmatch(r"[0-9]{1,9}", n) or int(n) > most:
                raise AtsError(f"{url} says OpenSearch {key} {n[:20] or 'unreadable'!r} beside "
                               f"{len(feed['entries'])} entries: one page of a longer list is never "
                               f"read as the whole board")
    rows, ids = [], set()
    for entry in feed["entries"]:
        title = _text(_js_one(entry, "title") or "")
        links = [h for rel, h in entry["links"] if rel == "alternate"]
        got = jobscore_posting(links[0], slug) if len(links) == 1 else None
        if not title or got is None:
            raise AtsError(f"{url}: an entry does not read as a posting of this board (a title, and "
                           f"one alternate link to a posting under /careers/{slug}/jobs/), so the "
                           f"board is not read in part")
        address, job = got
        if job in ids:                                  # one posting listed twice is one posting
            continue
        ids.add(job)
        places = dict.fromkeys(p for scheme, term in entry["categories"]
                               if scheme == JOBSCORE_LOCATION for p in (_text(term),) if p)
        row = {"title": title, "location": "; ".join(places), "url": address}
        # (where the board will list it decides what a block naming no
        # currency is: _jobscore_pay)
        row["comp"] = _jobscore_pay(entry, abroad=_abroad(row))
        # THE AD IS ALWAYS READ: it is in the answer already downloaded
        # (FETCH_DETAILS above)
        content = _js_one(entry, "content")
        if not content or entry["content_type"] not in ("html", "text"):
            row["jd"] = ""
        else:
            row["jd"] = plain_html(content) if entry["content_type"] == "html" else content.strip()
        rows.append(row)
    return rows


# ONE PLAN ASKS THE FEED ONCE, as for a Jobvite account's feed and a TriNet
# Hire list: the wiring door reads the board and then asks it whose it is,
# both this one feed. Inside jobscore_kept() - wire_found.plan, and the two
# accept paths of a held board (one_ask_a_board) - a feed read whole is kept
# in memory under the address it was asked at, and asked again it is that
# answer; a read that failed raised and is not kept; nothing is written
# anywhere; outside the block every call asks.
_JOBSCORE_KEPT: dict | None = None


@contextlib.contextmanager
def jobscore_kept():
    """Each feed read whole is the answer to every later ask of it in the
    block. Also a decorator: each call is a block of its own."""
    global _JOBSCORE_KEPT
    outer = _JOBSCORE_KEPT
    _JOBSCORE_KEPT = {} if outer is None else outer
    try:
        yield
    finally:
        _JOBSCORE_KEPT = outer


def _jobscore_copy(rows: list[dict]) -> list[dict]:
    return [dict(r, comp=dict(r["comp"]) if isinstance(r.get("comp"), dict) else r.get("comp"))
            for r in rows]


def jobscore_board(ref) -> tuple[str, str, list[dict]]:
    """(the feed's address, the name it states or "", its rows): one
    request - none inside jobscore_kept() where the feed was read whole
    already - or AtsError."""
    slug = jobscore_slug(ref)
    url = JOBSCORE_FEED.format(slug=slug)
    kept = _JOBSCORE_KEPT
    if kept is not None and url in kept:
        name, rows = kept[url]
        return url, name, _jobscore_copy(rows)
    feed = jobscore_feed(_jobscore_read(url), url)
    rows = jobscore_rows(feed, slug, url)
    name = jobscore_name(feed)
    if kept is not None:
        kept[url] = (name, _jobscore_copy(rows))
    return url, name, rows


def fetch_jobscore(ref) -> list[dict]:
    return jobscore_board(ref)[2]


@contextlib.contextmanager
def one_ask_a_board():
    """jobvitefeed_kept, trinethire_kept and jobscore_kept at once: inside
    it, a board those readers read whole is asked once, however many times it
    is read and then asked whose it is. For the paths that read a board and
    then probe it - accepting a held board at the desk (admin.
    act_board_proposal) and by a ruling (proposal_rulings._accept_board): a
    Jobvite feed and a JobScore feed name people, and the second ask was the
    same answer again (blind review of 2026-11-24). And phenom_kept, defined
    below: a Phenom site's unfiltered page, asked for its size and its name.
    Also a decorator."""
    with jobvitefeed_kept(), trinethire_kept(), jobscore_kept(), phenom_kept():
        yield
# ---- clearcompany: ClearCompany career sites, apply.clearcompany.com/<slug>
#
# Two companies on file hire through ClearCompany (research of 2026-11-23):
# Rehrig Pacific links its hosted site; Clearfield frames a legacy HRM Direct
# board (<label>.hrmdirect.com/employment/, which ClearCompany owns), and that
# address redirects to the hosted site. Both were type html: Rehrig showed 9
# rows, the widget's first page of 10, of 115; Clearfield showed none of 5.
#
# THREE REQUESTS A BOARD, every one through _page (robots.txt asked at every
# hop, the crawler's own name, the host gate):
#   1. the hosted page, https://apply.clearcompany.com/<slug>: a server-drawn
#      shell of about 5 KB with no posting in it, read for its widget's site id
#      (<script src=".../js/v1/career-site.js?hosted=true" data-site-id=GUID>),
#      its <title> ('<brand> - Careers', an en dash) and its footer's
#      a.cc-home link, the employer's own website;
#   2. https://careers-api.clearcompany.com/v1/settings/<site id>: the site's
#      settings, brandFullName among them;
#   3. https://careers-api.clearcompany.com/v1/<site id>?pageIndex=0&
#      pageSize=1000&source=CJB-0: the list, {results, totalCount, ...}, each
#      posting's whole ad in it. pageSize=1000 was honoured (Rehrig's 115 in one
#      answer); the order is NOT stable between two requests, so the list is
#      read whole only when its rows number its own totalCount. Fewer, and the
#      next pages are asked at the same size, deduplicated by id, and the read
#      raises unless they make up exactly totalCount: a partial board is never
#      published as whole.
# Measured 2026-11-23 on both boards (Clearfield 5, Rehrig 115).
#
# THE REF IS THE HOSTED SITE'S SLUG ('rehrig', 'clearfield'), as an address on
# apply.clearcompany.com names it: the finder takes it from a link there or
# from the address a legacy HRM Direct board's redirect lands on, never from
# a guess (the tenant label matched the slug on 2 of 2 sites, which proves
# nothing). Never 'internal', which robots.txt closes. The site id is lifted
# from the hosted page each read and never stored.
#
# NEVER THE TENANT HOST. applyLink and jobPortalUrl point at
# <tenant>.clearcompany.com, whose robots.txt says Disallow: / to everybody: it
# is never asked, never a ref, and never a row's url. A row's url is the
# hosted site's own posting address, https://apply.clearcompany.com/<slug>?
# jobId=<id>, which robots.txt allows. An e-mail-to-apply posting's applyLink
# is an e-mail address, often a person's: it is never read, and nothing from
# careers-api.clearcompany.com is ever written to the HTTP cache (NEVER_CACHED;
# the same host serves a posting's hiring team, names and photos, at
# /v1/insights/team/<id>, which no reader asks - every hop of a read is held
# to the very address it asked).
#
# A ROW: positionTitle (the server leaves stray spaces on it), the place from
# locations[] (clearcompany_place) - never the server's `location` string,
# whose whitespace is mangled ('Erie  PA', 'Hope Mills\t NC'); the ad from the
# list's own description (no second request); a date only where the row
# carries postedDate (both measured sites switch it off, and the feed leaves
# out what a site does not show). No pay field is read: salaryRangeLow,
# salaryRangeHigh and salaryType were on no measured row and the widget prints
# USD and CAD both as '$', so pay comes from the ad's own text through
# plain_rows, as for every reader whose feed states none - and where the row
# is abroad (its office_hint's country, ats._abroad) a bare '$' is no pay.
# departmentName (cost centres: '101004 Kenosha Production') and the
# requisition number are not read.
#
# A PLACE THE BOARD CAN READ (blind review of the reader, 2026-11-24). The
# country is an ISO-2 code, and roles.geography reads a trailing two-letter
# code as a US state: 'Toronto, ON, CA' was a California office and
# 'Calgary, AB, CA' and 'Pune, MH, IN' US ones. So a US place is 'City, ST'
# (or 'Statewide (ST)'); a place abroad is its city alone - its subdivision
# can be a US code too, Tamil Nadu's TN - and its country goes in office_hint
# and nowhere in the text, where it settles is_us and what a '$' means.
# A NATIONWIDE PLACE IS NO WAY OF WORKING. The widget shows one as
# 'Remote (US)', but it is a place an employer picked, not a mode: three of
# Rehrig's five are a forklift job, a yard driver and a warehouse associate
# its own On-Site/Hybrid/Remote field calls On-Site. It is written 'US'
# (abroad: nothing), which geography reads as no mode, and the row's mode is
# only ever the posting's own answer to a field that IS that question
# (clearcompany_mode), through work_mode; the site's other custom fields are
# not read.
#
# A ZERO HAS NOT BEEN SEEN. Neither board was empty, so an empty list raises,
# kept Unknown and never "None found", as an empty Jobvite feed does. Every
# other failure raises too: a slug that is gone (404 on the hosted page), a
# site id that is gone (404 on both API calls), a hosted page with no site id
# or with two, a list in another shape, a count the rows do not make up, a
# redirect off the board's own addresses. A closed posting's address still
# answers 200, titled plain 'Careers', so no posting page is ever read for
# whether it is open.
#
# ONE PLAN ASKS EACH BOARD ONCE: inside clearcompany_kept() - wire_found.plan,
# and nothing else - a board read whole is kept in memory under its hosted
# address, so the wiring door's read and its probe (verify_boards) take one
# answer, as trinethire_kept and jobvitefeed_kept keep theirs.
CLEARCOMPANY_HOST = "apply.clearcompany.com"
CLEARCOMPANY_API_HOST = "careers-api.clearcompany.com"
CLEARCOMPANY_PAGE_SIZE = 1000
# pages of CLEARCOMPANY_PAGE_SIZE asked when the first answer falls short of
# its own count (none was needed on either board measured)
CLEARCOMPANY_MAX_PAGES = 10
CLEARCOMPANY_PAGE_MAX = 2_000_000        # characters of a hosted page read; more is refused
CLEARCOMPANY_TITLE_TAIL = " – Careers"   # an en dash, as the hosted page writes it
CLEARCOMPANY_BOOTSTRAP = "careers-content.clearcompany.com/js/v1/career-site.js"
_CC_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,80})")
_CC_CLOSED = frozenset({"internal"})      # robots.txt: Disallow: /internal/
_CC_GUID = r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}"
_CC_GUID_RX = re.compile(_CC_GUID)
_CC_SCRIPT = re.compile(r"<script\b([^<>]{0,4000})>", re.I)
_CC_A = re.compile(r"<a\b([^<>]{0,4000})>", re.I)
_CC_SITE_ATTR = re.compile(r"""(?:^|\s)data-site-id\s*=\s*(?:"(""" + _CC_GUID + r""")"|'("""
                           + _CC_GUID + r""")'|(""" + _CC_GUID + r""")(?=[\s>/]|$))""", re.I)
_CC_SRC = re.compile(r"""(?:^|\s)src\s*=\s*(?:"([^"<>]{0,2000})"|'([^'<>]{0,2000})'|([^\s"'<>]{1,2000}))""",
                     re.I)
_CC_HREF = re.compile(r"""(?:^|\s)href\s*=\s*(?:"([^"<>]{0,2000})"|'([^'<>]{0,2000})'|([^\s"'<>]{1,2000}))""",
                      re.I)
_CC_CLASS = re.compile(r"""(?:^|\s)class\s*=\s*(?:"([^"<>]{0,400})"|'([^'<>]{0,400})'|([^\s"'<>]{1,400}))""",
                       re.I)


def clearcompany_slug(ref) -> str:
    """The hosted site's slug a ref names, or AtsError: one label of letters,
    digits and hyphens, never 'internal' (robots.txt closes it), never an
    address."""
    s = _text(ref) if isinstance(ref, str) else ""
    if not _CC_SLUG.fullmatch(s) or s.lower() in _CC_CLOSED:
        raise AtsError(f"not a clearcompany board: {ref!r} (a ClearCompany board is the slug "
                       f"of its hosted site, apply.clearcompany.com/<slug>)")
    return s


def clearcompany_url(ref) -> str:
    """The hosted site: what is read first, and what a person is sent to
    (build_board.board_url). Never the tenant host, which robots.txt closes."""
    return f"https://{CLEARCOMPANY_HOST}/{clearcompany_slug(ref)}"


def clearcompany_posting_url(slug: str, job_id: str) -> str:
    """A posting's own address on the hosted site, the id lower-cased: the
    address the server itself gives a posting (its canonical link). Never
    applyLink or jobPortalUrl, which are on the tenant host robots.txt closes."""
    return f"https://{CLEARCOMPANY_HOST}/{slug}?jobId={job_id.lower()}"


def clearcompany_hosted_slug(url) -> str | None:
    """The slug of a hosted site's address - apply.clearcompany.com/<slug>,
    /<slug>/redirect, any query - or None (another host, another path,
    'internal')."""
    try:
        parts = urllib.parse.urlsplit(url if isinstance(url, str) else "")
    except ValueError:
        return None
    m = re.fullmatch(r"/([A-Za-z0-9](?:[A-Za-z0-9-]{0,80}))(?:/redirect)?/?", parts.path or "")
    if (parts.scheme not in ("http", "https") or (parts.hostname or "").lower() != CLEARCOMPANY_HOST
            or not m or m.group(1).lower() in _CC_CLOSED):
        return None
    return m.group(1)


def clearcompany_posting_key(url) -> str | None:
    """The posting a ClearCompany hosted-site address names, as the board
    knows it - https://apply.clearcompany.com/<slug>?jobId=<id>, both lower-
    cased, every other query left out - or None for any other address. The
    html reader that read these boards before (Rehrig's) wrote the same
    posting as ?noheader=False&jobId=<id>; build_board matches the posting
    on this, so a posting keeps its first-seen day whichever way its address
    was written. A key, never an address anybody is sent to."""
    slug = clearcompany_hosted_slug(url)
    if slug is None:
        return None
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return None
    ids = {v.lower() for k, vs in query.items() if k.lower() == "jobid" for v in vs}
    if len(ids) != 1 or not _CC_GUID_RX.fullmatch(next(iter(ids))):
        return None
    return f"https://{CLEARCOMPANY_HOST}/{slug.lower()}?jobId={ids.pop()}"


def clearcompany_posting_forms(url) -> list[str]:
    """Every address the ClearCompany posting `url` names has been written as
    on this board, or [] for an address that names no posting
    (clearcompany_posting_key): this reader's ?jobId=<id>, and the careers
    page's widget's ?noheader=False&jobId=<id> (every one of the 42 Rehrig
    ids the snapshots held by 2026-11-24) and ?noheader=True&jobId=<id> (the
    framed form) - the slug as `url` writes it and lower-cased, the id
    lower-cased (as the list, the widget and the canonical link write it) and
    upper-cased. A snapshot under data/history keeps a posting's id alone,
    hashed from its address as it was written, so build_board.carry_first_seen
    looks an earlier night up under each of these. Keys, never addresses
    anybody is sent to."""
    key = clearcompany_posting_key(url)
    if key is None:
        return []
    slug, job = clearcompany_hosted_slug(url), key.rsplit("jobId=", 1)[1]
    return list(dict.fromkeys(
        f"https://{CLEARCOMPANY_HOST}/{s}?{q}jobId={j}"
        for s in (slug, slug.lower()) for j in (job, job.upper())
        for q in ("", "noheader=False&", "noheader=True&")))


def _cc_attr(rx, attrs: str) -> str:
    m = rx.search(attrs or "")
    return html_lib.unescape(next((g for g in m.groups() if g is not None), "")).strip() if m else ""


def clearcompany_site_id(page: str, url: str = "the hosted page") -> str:
    """The site id the hosted page's widget is drawn for, lower-cased - the
    data-site-id (or ?siteId=) of its career-site.js script - or AtsError for
    a page with none (an offline site, another page) or naming two. Read off
    the page as a browser runs it: a script inside a comment is none."""
    ids = set()
    for m in _CC_SCRIPT.finditer(_COMMENT.sub(" ", page or "")):
        attrs = m.group(1)
        src = _cc_attr(_CC_SRC, attrs)
        try:
            parts = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{CLEARCOMPANY_HOST}/", src))
            query = urllib.parse.parse_qs(parts.query)
        except ValueError:
            continue
        if f"{(parts.hostname or '').lower()}{parts.path.lower()}" != CLEARCOMPANY_BOOTSTRAP:
            continue                       # (the widget's own bootstrap, and nothing else)
        said = _CC_SITE_ATTR.search(attrs)
        if said:
            ids.add(next(g for g in said.groups() if g is not None).lower())
        ids |= {v.lower() for k, vs in query.items() if k.lower() == "siteid"
                for v in vs if _CC_GUID_RX.fullmatch(v.strip())}
    if len(ids) != 1:
        raise AtsError(f"{url} " + ("draws no ClearCompany career site (no career-site.js with a "
                                    "site id): an offline site or another page, never a board "
                                    "with nothing open" if not ids else
                                    f"names {len(ids)} ClearCompany sites, so which is the board "
                                    f"is not known"))
    return ids.pop()


def clearcompany_page_name(page: str) -> str:
    """The name the hosted page's <title> states before ' - Careers' (an en
    dash), unescaped, one line - or "" for any other title (a closed posting's
    and an unknown one's are the bare word 'Careers')."""
    body = _COMMENT.sub(" ", page or "")
    low = lower_same(body)
    t0 = _tag_at(low, "title", 0)
    gt = low.find(">", t0) if t0 >= 0 else -1
    t1 = low.find("</title", gt) if gt >= 0 else -1
    title = _text(html_lib.unescape(body[gt + 1:t1])) if 0 <= gt < t1 else ""
    if not title.endswith(CLEARCOMPANY_TITLE_TAIL):
        return ""
    return title[:-len(CLEARCOMPANY_TITLE_TAIL)].strip()


def clearcompany_home(page: str) -> str | None:
    """The employer's website the hosted page's footer links ('Visit our home
    page', a.cc-home), as the employer's settings wrote it - or None. An
    address, never a name. What it names is verify_boards._site's to judge,
    as for ashby's and jazzhr's: a link written '//www.x/' or 'www.x' still
    names a website, and dropped it let a namesake pass on its name alone
    (blind review of the probe, 2026-11-24). A link with another scheme
    (javascript:, mailto:) or an '@' in it names none."""
    for m in _CC_A.finditer(_COMMENT.sub(" ", page or "")):
        attrs = m.group(1)
        if "cc-home" not in _cc_attr(_CC_CLASS, attrs).lower().split():
            continue
        href = _cc_attr(_CC_HREF, attrs)
        try:
            parts = urllib.parse.urlsplit(href)
        except ValueError:
            return None
        if not href or "@" in href or parts.scheme.lower() not in ("", "http", "https"):
            return None
        return "https:" + href if href.startswith("//") else href
    return None


def _cc_hosted(slug: str) -> tuple[str, str]:
    """(the hosted page, where it landed). Every hop held to this site's own
    addresses - apply.clearcompany.com, /<slug> or /<slug>/redirect, this very
    slug in any case - and refused before it is asked anything."""
    url = f"https://{CLEARCOMPANY_HOST}/{slug}"

    def stay(u: str):
        there = clearcompany_hosted_slug(u)
        if there is None or there.lower() != slug.lower() or not u.startswith("https://"):
            raise AtsError(f"{url} leads off to {str(u)[:160]}, which is not this site's own page, "
                           f"so it is not read as a board with nothing open")
    resp = _page(url, same_host=True, may=stay)
    page = resp.text or ""
    if len(page) > CLEARCOMPANY_PAGE_MAX:
        raise AtsError(f"{url} is more than {CLEARCOMPANY_PAGE_MAX:,} characters: not read, and "
                       f"never read in part")
    return page, str(getattr(resp, "url", "") or url)


def _cc_api(url: str):
    """One answer of ClearCompany's career-site API, read in place: robots.txt
    asked (_page), and any redirect refused before it is asked - the API
    answers where it is asked, and its host also serves a posting's hiring
    team, which nothing here asks."""
    def stay(u: str):
        if u != url:
            raise AtsError(f"{url} leads off to {str(u)[:160]}: the API answers in place, so a "
                           f"redirect is never followed, and it is not read as a board with "
                           f"nothing open")
    return _json(_page(url, same_host=True, may=stay))


def clearcompany_settings(site_id: str) -> dict:
    d = _cc_api(f"https://{CLEARCOMPANY_API_HOST}/v1/settings/{site_id}")
    if not isinstance(d, dict):
        raise AtsError(f"the settings of ClearCompany site {site_id} are not an object")
    return d


def _cc_list_url(site_id: str, index: int) -> str:
    return (f"https://{CLEARCOMPANY_API_HOST}/v1/{site_id}?pageIndex={index}"
            f"&pageSize={CLEARCOMPANY_PAGE_SIZE}&source=CJB-0")


def _cc_page_of(site_id: str, index: int) -> tuple[list, int]:
    url = _cc_list_url(site_id, index)
    d = _cc_api(url)
    results = d.get("results") if isinstance(d, dict) else None
    total = d.get("totalCount") if isinstance(d, dict) else None
    if (not isinstance(results, list) or not all(isinstance(j, dict) for j in results)
            or not isinstance(total, int) or isinstance(total, bool) or total < 0
            or d.get("currentPageIndex") not in (index, None)):
        raise AtsError(f"{url} is not a ClearCompany job list (results, totalCount, its page "
                       f"index), so it is not read")
    return results, total


def clearcompany_jobs(site_id: str) -> list[dict]:
    """Every posting of the site, once each, or AtsError: the first page at
    CLEARCOMPANY_PAGE_SIZE, and where it falls short of its own totalCount
    the next pages at the same size, deduplicated by id. The union must be
    exactly totalCount; anything else is a board that did not read whole."""
    results, total = _cc_page_of(site_id, 0)
    if total == 0 and not results:
        raise AtsError(f"ClearCompany site {site_id} lists no job: an empty ClearCompany board has "
                       f"not been measured, so it is kept Unknown and never read as a zero")
    by_id: dict = {}
    for j in results:
        by_id.setdefault(_text(j.get("id")).lower(), j)
    index = 1
    while len(by_id) < total and index < CLEARCOMPANY_MAX_PAGES \
            and index * CLEARCOMPANY_PAGE_SIZE < total:
        more, again = _cc_page_of(site_id, index)
        if again != total:
            raise AtsError(f"ClearCompany site {site_id} counted {total} and then {again} between "
                           f"two pages, so it is not read in part")
        if not more:
            break
        for j in more:
            by_id.setdefault(_text(j.get("id")).lower(), j)
        index += 1
    # (said for what it is, one answer or several: the owner's queue shows it -
    # blind review of the reader, 2026-11-24)
    n, asked = len(by_id), ("its one answer" if index == 1 else f"its {index} pages")
    if n > total:
        raise AtsError(f"ClearCompany site {site_id} counts {total} postings and {asked} of up to "
                       f"{CLEARCOMPANY_PAGE_SIZE} gave {n} distinct ones, more than its own count, "
                       f"so what it lists is not the board it counts")
    if n < total and index == 1:
        raise AtsError(f"ClearCompany site {site_id} counts {total} postings and its one answer of "
                       f"up to {CLEARCOMPANY_PAGE_SIZE} gave {n} distinct ones, fewer than its own "
                       f"count where one page holds them all, so the board is not read in part")
    if n < total:
        raise AtsError(f"ClearCompany site {site_id} counts {total} postings and {asked} of "
                       f"{CLEARCOMPANY_PAGE_SIZE} gave {n} distinct ones between them: the order is "
                       f"not stable between requests, and a partial board is never published as "
                       f"whole")
    return list(by_id.values())


def clearcompany_place(loc) -> tuple[str, tuple | None]:
    """(a place as the board shows it, what it names as (city, subdivision,
    country) or None) for one of a posting's locations[]. In the US
    'Kenosha, WI', 'Statewide (MN)', and a nationwide place 'US'; abroad the
    city alone, or nothing - never the ISO-2 country or a subdivision code,
    which roles.geography reads as a US state - its country carried in what
    it names (see A PLACE THE BOARD CAN READ above). A country that is not
    stated leaves the place as written ('City, ST')."""
    if not isinstance(loc, dict):
        return "", None
    city, sub, country = (_text(loc.get(k)) for k in ("city", "subdivision", "country"))
    k = country.lower().replace(".", "")
    us, abroad = k in _US_COUNTRY, bool(k) and k not in _US_COUNTRY
    if loc.get("isNationwide") is True:
        return ("US" if us else ""), ((None, None, country) if country else None)
    if loc.get("isStatewide") is True and sub:
        return ("" if abroad else f"Statewide ({sub})"), (None, sub, country or None)
    place = city if abroad else ", ".join(p for p in (city, sub) if p)
    return place, ((city or None, sub or None, country or None) if (city or sub or country) else None)


def clearcompany_mode(job: dict) -> str | None:
    """How the posting is worked, as its own answer says - or None. Only an
    answer to a custom field whose NAME is the question itself, every part of
    it a work_mode word and two or more of them ('On-Site/Hybrid/Remote', as
    Rehrig's site asks it), answered with one of those words, through
    work_mode; two such answers that differ are none. Nothing is inferred
    from a place (a nationwide place is not remote: A NATIONWIDE PLACE IS NO
    WAY OF WORKING above), and no other custom field is read."""
    said = set()
    answers = job.get("customFieldAnswers")
    for a in answers if isinstance(answers, list) else ():
        name = a.get("customFieldName") if isinstance(a, dict) else None
        answer = a.get("customFieldOptionName") if isinstance(a, dict) else None
        if not isinstance(name, str) or not isinstance(answer, str):
            continue
        asks = [work_mode(p) for p in re.split(r"\s*(?:/|,|\|)\s*|\s+or\s+", name.strip(), flags=re.I)]
        if len(asks) < 2 or None in asks or len(set(asks)) != len(asks):
            continue
        got = work_mode(answer)
        if got in asks:
            said.add(got)
    return said.pop() if len(said) == 1 else None


def clearcompany_rows(jobs: list[dict], slug: str, url: str = "the list") -> list[dict]:
    """One row a posting, or AtsError for a row that does not read as one (no
    id in the site's own form, no title): the list is the board, so a row it
    cannot read is never left out of a board published as whole. The office
    hint is the first place that names a city or a subdivision, else the
    first that names a country alone (a nationwide place: is_us and what a
    '$' means settle on it)."""
    rows = []
    for j in jobs:
        jid, title = _text(j.get("id")).lower(), _text(j.get("positionTitle"))
        if not _CC_GUID_RX.fullmatch(jid) or not title:
            raise AtsError(f"{url}: a row does not read as a posting (its id or its title), so the "
                           f"board is not read in part")
        places, named = [], []
        for loc in j.get("locations") if isinstance(j.get("locations"), list) else ():
            place, where = clearcompany_place(loc)
            if place:
                places.append(place)
            if where is not None:
                named.append(where)
        office = next((w for w in named if w[0] or w[1]), None) or next(iter(named), None)
        desc = j.get("description")
        rows.append({"title": title,
                     # (one place a posting names twice is written once)
                     "location": "; ".join(dict.fromkeys(places)),
                     "url": clearcompany_posting_url(slug, jid),
                     "posted": posted_date(j.get("postedDate")) if j.get("postedDate") else None,
                     "mode": clearcompany_mode(j),
                     "office_hint": office_hint(*office) if office else None,
                     # (the ad is in the answer already downloaded: always read)
                     "jd": plain_html(desc) if isinstance(desc, str) else ""})
    return rows


def clearcompany_brands(settings: dict) -> list[str]:
    """The brands the site's own brand filter lists (filterOptions.brand),
    one each, blanks (the 'All Brands' default) left out. A site listing more
    than one names several companies, and verify_boards says so."""
    opts = settings.get("requisitionList") if isinstance(settings, dict) else None
    opts = opts.get("search") if isinstance(opts, dict) else None
    opts = opts.get("filterOptions") if isinstance(opts, dict) else None
    brand = opts.get("brand") if isinstance(opts, dict) else None
    return list(dict.fromkeys(_text(o.get("text")) for o in brand or ()
                              if isinstance(o, dict) and _text(o.get("text")))) \
        if isinstance(brand, list) else []


_CC_KEPT: dict | None = None


@contextlib.contextmanager
def clearcompany_kept():
    """Each board read whole is the answer to every later ask of it in the
    block (see ONE PLAN ASKS EACH BOARD ONCE above). Also a decorator."""
    global _CC_KEPT
    outer = _CC_KEPT
    _CC_KEPT = {} if outer is None else outer
    try:
        yield
    finally:
        _CC_KEPT = outer


def clearcompany_board(ref) -> dict:
    """{slug, url, landed, site_id, page_name, home, brand, brands, rows}: the
    hosted page, the settings and the list, three requests - none inside
    clearcompany_kept() where the board was read whole already - or AtsError.
    `page_name` is what the hosted page's <title> names, `brand` the settings'
    brandFullName, `home` the footer's website: verify_boards weighs them;
    nothing here reads a name for anything else."""
    slug = clearcompany_slug(ref)
    url = f"https://{CLEARCOMPANY_HOST}/{slug}"
    kept = _CC_KEPT
    if kept is not None and url.lower() in kept:
        got = kept[url.lower()]
        return dict(got, rows=[dict(r) for r in got["rows"]])
    page, landed = _cc_hosted(slug)
    site = clearcompany_site_id(page, url)
    settings = clearcompany_settings(site)
    rows = clearcompany_rows(clearcompany_jobs(site), slug, url)
    got = {"slug": slug, "url": url, "landed": landed, "site_id": site,
           "page_name": clearcompany_page_name(page), "home": clearcompany_home(page),
           "brand": _text(settings.get("brandFullName")) if isinstance(settings.get("brandFullName"), str) else "",
           "brands": clearcompany_brands(settings), "rows": rows}
    if kept is not None:
        kept[url.lower()] = dict(got, rows=[dict(r) for r in rows])
    return got


def fetch_clearcompany(ref) -> list[dict]:
    return clearcompany_board(ref)["rows"]

# ---- phenom: Phenom career sites, on the employer's own careers host
#
# Six companies on file have one (research of 2026-11-23; four more pages read
# 2026-11-27): Conduent (430 open by its own count), Fiserv (411), Snowflake
# (381), Adobe (256), Qualtrics (177) and WEX (86). All six were type html and
# read 0 to 10 rows: a Phenom page draws its rows in the browser from JSON the
# page itself carries, and none of a page's ten titles is in its markup.
#
# THE REF IS THE SITE'S OWN baseUrl, https://<host>[/<prefix>]/<cc>/<lang>/
# (https://careers.conduent.com/us/en/, https://www.qualtrics.com/careers/us/
# en/), as the page's inline `var phApp = phApp || {...}` object states it
# (phenom_app, read with json's raw_decode as verify_boards._embedded reads
# inline JSON; phenom_base: a lower-case host, a two-letter country and
# language, the last slash). One /<cc>/<lang>/ is one locale's board, and its
# count is that locale's. Phenom fronts another system - Oracle HCM
# (Conduent), Workday (WEX, Fiserv, Adobe), Greenhouse (Qualtrics), Ashby
# (Snowflake) - and a row's applyUrl is that system's address (a
# myworkdayjobs.com one, Ashby's, ?gh_jid=): never read, never a row's url,
# never followed.
#
# THE ROWS ARE KEYWORD WALKS, fetch_workday's two searches (PHENOM_TERMS):
# <baseUrl>search-results?keywords=<term>, then &from=10, 20, ... (ats._paged,
# PHENOM_PAGE a page), every page read by _ph_read (robots.txt at every hop, as
# a stream capped at PHENOM_PAGE_MAX bytes), answered in place (a redirect is
# not followed) and its phApp naming this very baseUrl. A page's
# phApp.ddo.eagerLoadRefineSearch is {status 200, hits: the rows on THIS page,
# totalHits: the search's, data: {jobs[], keywords: the search, echoed}}. NEVER
# s=1, which silently drops the keyword (Qualtrics ?keywords=sales&from=10&s=1
# answered keywords '' and its whole board), and NEVER the unfiltered list for
# rows: its offset paging reorders between requests (Conduent's ?from=10
# shared 7 of 10 rows with page 1). A page whose echoed keyword is not the
# term, whose totalHits is not the walk's first page's, or that is short
# inside the total raises, and nothing is asked past the total; a search whose
# distinct rows (by jobId) are not its own totalHits raises: a walk that
# comes up short is never published as read. Rows are deduplicated by jobId
# across the terms. WHAT THE COUNT CANNOT SEE (blind review of 2026-11-27): a
# posting that closes while another opens in the seconds a search is walked
# leaves totalHits the same on every page while the offsets shift under it,
# so one open posting can be missed and the closed one kept for that night,
# and the walk still counts whole. The next night's walk reads them right.
# It is the residual risk every offset walk carries, the Workday reader's
# included; nothing here claims more than a walk that matched its own count.
# 'account executive' is no phrase search: WEX answered 65 of its 86 postings
# and Qualtrics 149 of 177 to it (2026-11-27), so a walk is most of a page
# count - see THE REQUESTS A NIGHT in CLAUDE.md.
#
# THE READER READS PART OF THE BOARD BY DESIGN, as fetch_workday does: its rows
# say nothing about the board's size, which phenom_total asks (totalHits on
# the UNFILTERED search page, the whole locale board, one request), and the
# wiring door asks it first (wire_found.PARTIAL).
#
# A SEARCH WITH NOTHING IN IT IS ONE MEASURED SHAPE (2026-11-27, WEX, a term
# nothing matches): status 200, hits 0, totalHits 0, jobs [], the keyword
# echoed. Anything else - no phApp, a phApp naming another site, no
# eagerLoadRefineSearch, another status, a count the rows do not make up, a
# challenge page, a redirect - raises, never a zero.
#
# A ROW: the title; url = baseUrl + 'job/' + jobId + '/' + the title with every
# run of non-alphanumerics a '-' (phenom_posting_url: each of the 60 Conduent
# rows read is an address its sitemap lists, and the address is the posting's
# canonical and og:url on Conduent's and Qualtrics' postings; Snowflake's
# canonical writes its jobSeqNo where this writes the jobId, and the jobId
# address answers the same posting); the place (phenom_place); posted =
# dateCreated, or the earlier of it and postedDate where both are read - never
# postedDate alone (a mass refresh: all 50 Qualtrics rows read
# 2026-11-20T18:08:59) and never a posting's JSON-LD datePosted (rewritten
# daily). descriptionTeaser is an ML-written summary and never the ad, so a
# row's jd is "" - nothing for the pay parser or the classifier to read as the
# employer's words - until FETCH_DETAILS reads the posting's own
# ddo.jobDetail.data.job.description, one request a posting (phenom_ad).
#
# THE PLACE IS THE ONE THE SITE'S OWN CARD SHOWS (blind review of 2026-11-27).
# WEX's every row reads location 'Remote, Maine, United States of America',
# city 'Remote', state 'Maine' and address 'Remote Address, Remote, ME 99999':
# a placeholder, under roles WEX places in Portland, ME and anywhere in the
# US. Its own places are in locationName ('US - Remote', 'Portland, ME |
# Boston, MA'), the field its page's jobwidgetsettings names for its cards;
# Fiserv's names location and multi_location, and the other four sites name
# no card fields (and fill no locationName). So the place is locationName,
# each of its ' | ' parts once, where the page's cards show it - or, on a page
# naming no card fields, where the row carries one - and else location (or
# cityStateCountry) and each multi_location[] once (phenom_place). And a
# state beside a city that names no place, or in a placeholder address, is
# no office's state: the office hint leaves it out.
#
# PEOPLE: Snowflake's rows carry hiringManagerName, and Qualtrics' postings
# recruiterNames, coordinatorDetails and sourcerDetails. None is read, and no
# Phenom page is written to the HTTP cache (_ph_read asks by _one, which keeps
# nothing: the hosts are each employer's own, which NEVER_CACHED cannot list).
# The pages send no ETag or Last-Modified anyway (95 to 240 KB a page, every
# night in full).
#
# ONE PLAN ASKS THE UNFILTERED PAGE ONCE: inside phenom_kept() - wire_found.plan
# and one_ask_a_board - what phenom_site read is kept in memory (its count and
# the two names its <head> states, never the page), so the door's size gate
# (add_company.board_total) and its probe (verify_boards) take one answer.
PHENOM_TERMS = ("account executive", "sales")    # fetch_workday's two searches
PHENOM_PAGE = 10                                 # rows a page; hits=10 on every site
PHENOM_MAX_PAGES = 60                            # 600 rows a search; more is not read
PHENOM_PAGE_MAX = 3_000_000                      # bytes of a page read; more is refused as it arrives
_PH_BASE = re.compile(r"https://((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})"
                      r"((?:/[a-z0-9-]{1,80}){0,4}?)/([a-z]{2})/([a-z]{2})/")
_PH_APP = re.compile(r"\bvar\s+phApp\s*=\s*phApp\s*\|\|\s*")
_PH_DDO = re.compile(r"\bphApp\.ddo\s*=\s*")
_PH_JOB_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,127})")
_PH_POSTING = re.compile(r"(https://[a-z0-9.-]+(?:/[a-z0-9-]{1,80}){0,4}?/[a-z]{2}/[a-z]{2}/)job/"
                         r"([A-Za-z0-9](?:[A-Za-z0-9_-]{0,127}))(?:/[^/?#]*)?/?", re.I)
_PH_RUN = re.compile(r"[^A-Za-z0-9]+")
_PH_META = re.compile(r"<meta\b[^<>]{0,4000}>", re.I)
_PH_ATTR = re.compile(r"""([\w:-]{1,60})\s{0,8}=\s{0,8}(?:"([^"]*)"|'([^']*)')""")
_PH_TITLE = re.compile(r"<title\b[^<>]{0,400}>([^<]{0,400})</title\s*>", re.I)
# a place part that names no place: Snowflake's state 'Not Applicable', and a
# way of working written where a city or state goes (WEX's city 'Remote',
# Conduent's state 'Remote US', Qualtrics' city 'United States (Remote)')
_PH_NO_PLACE = frozenset({"not applicable", "n/a", "na", "none", "null", "-"})
# an address that is a placeholder, not a place: WEX's 'Remote Address,
# Remote, ME 99999, United States of America' on every row
_PH_NO_ADDRESS = re.compile(r"\bremote address\b|\b99999\b", re.I)


def phenom_base(ref) -> str:
    """The site's baseUrl a ref names, exactly as phApp states it, or
    AtsError: https, a lower-case host, at most four lower-case path segments
    before a two-letter country and language, the last slash, nothing else."""
    s = ref if isinstance(ref, str) else ""
    m = _PH_BASE.fullmatch(s)
    if not m or not _HOSTNAME.fullmatch(m.group(1)):
        raise AtsError(f"not a phenom board: {ref!r} (a Phenom board is its site's baseUrl, "
                       f"https://<host>[/<prefix>]/<cc>/<lang>/, lower-case, with its last slash)")
    return s


def phenom_off_site(ref, website) -> str | None:
    """None where the Phenom site `ref` is on the company's own website's
    domain - its host the website's host (a leading www. aside) or a host
    under it (careers.conduent.com under conduent.com; www.qualtrics.com) -
    and else WHICH HALF FAILED, in words (blind review of 2026-11-27: one
    sentence for every failure said a site on a domain of theirs was not).
    Check 1's half for this type (wire_found, at the plan and again at the
    write): a Phenom site sits on its employer's own host, so one on another
    company's domain is not this company's board. A website on file that is
    a page inside a larger site (it has a path, a locale's included) names no
    domain of its own, so nothing is held to it: the owner looks."""
    try:
        host = urllib.parse.urlsplit(phenom_base(ref)).hostname or ""
    except AtsError:
        return f"{str(ref)[:120]!r} is not a Phenom site's baseUrl"
    w = website.strip() if isinstance(website, str) else ""
    if not w:
        return "no website is on file for them, so no domain of theirs is named to hold its host to"
    try:
        parts = urllib.parse.urlsplit(w if "//" in w else "https://" + w)
        site = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        site, parts = "", None
    if parts is None or not _HOSTNAME.fullmatch(site):
        return f"their website on file ({w[:120]}) names no host to hold its host to"
    if parts.path.strip("/") or parts.query:
        return (f"their website on file ({w[:120]}) is a page inside a larger site, not a domain, "
                f"so no domain of theirs is named to hold its host to")
    bare = site.removeprefix("www.")
    if host == bare or host.endswith("." + bare):
        return None
    return (f"it is on {host}, which is neither their website's host ({site}) nor a host under "
            f"it: a Phenom site sits on its employer's own host, so one on another domain is not "
            f"shown to be theirs")


def phenom_on_site(ref, website) -> bool:
    """Is the Phenom site `ref` on the company's own website's domain
    (phenom_off_site says no reason against it)?"""
    return phenom_off_site(ref, website) is None


def phenom_search_url(base: str, term: str | None = None, offset: int = 0) -> str:
    """The site's search page: unfiltered (term None) or one keyword search
    from its `offset`th row - never with s=1, which drops the keyword."""
    url = f"{base}search-results"
    if term is None:
        return url
    url += "?keywords=" + urllib.parse.quote(term, safe="")
    return url + (f"&from={offset}" if offset else "")


def phenom_posting_url(base: str, job_id: str, title: str) -> str:
    """A posting's own address on the site: baseUrl + job/<jobId>/<the title,
    every run of non-alphanumerics a '-'>. Never the row's applyUrl."""
    slug = _PH_RUN.sub("-", title or "").strip("-")
    return f"{base}job/{job_id}/{slug}" if slug else f"{base}job/{job_id}"


def phenom_posting_key(url) -> str | None:
    """The posting a Phenom posting address names - its site's baseUrl and
    jobId, lower-cased, the title's slug left out (it moves with a retitle) -
    or None for any other address. The html reader that read these sites
    before wrote the same posting with no place; build_board matches the
    posting on this, so it keeps its first-seen day when its company moves to
    this reader. A key, never an address anybody is sent to."""
    m = _PH_POSTING.fullmatch(url.strip()) if isinstance(url, str) else None
    if not m:
        return None
    try:
        phenom_base(m.group(1).lower())
    except AtsError:
        return None
    return f"{m.group(1).lower()}job/{m.group(2).lower()}"


def phenom_posting_forms(url) -> list[str]:
    """The addresses a Phenom posting `url` has been written as on this board:
    the address itself, which the html reader and this one both write as the
    site's own link does (every html row on file for Conduent and Adobe is in
    the form phenom_posting_url builds) - or [] for an address that names no
    posting (phenom_posting_key). build_board.carry_first_seen looks an
    earlier night up under it. Keys, never addresses anybody is sent to."""
    return [url.strip()] if phenom_posting_key(url) else []


def phenom_title_cuts(title) -> list[tuple[str, str]]:
    """Every (head, tail) a Phenom posting's own title splits into at a space:
    the html reader that read these sites before often cut a title at a line
    of its card and wrote the rest as the place (Conduent's 'Medical Services
    Specialist with Czech and English Language (100%' in 'Remote)', Adobe's
    'Sr. Full Stack Engineer' in '(Back-end), Agentic Governance'), so the
    snapshot id of such a night is the head as the title and the tail as the
    place (blind review of 2026-11-27). build_board.carry_first_seen looks an
    earlier night up under each, by its exact id - the company, that head
    and the address with that tail. Keys, never a title anybody reads."""
    t = title if isinstance(title, str) else ""
    return [(t[:i], t[i + 1:]) for i, ch in enumerate(t) if ch == " " and t[:i].strip()
            and t[i + 1:].strip()][:40]


def phenom_app(page: str) -> dict | None:
    """The page's own `var phApp = phApp || {...}` object - where the page
    carries exactly one - or None."""
    marks = list(_PH_APP.finditer(page or "")) if isinstance(page, str) else []
    if len(marks) != 1:
        return None
    try:
        got = json.JSONDecoder().raw_decode(page, marks[0].end())[0]
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def _ph_read(url: str) -> str:
    """One page of the site, read in place and as a stream: robots.txt asked
    at every hop (follow), the crawler's own name and the host gate (_one),
    any redirect refused before anything is asked of where it points - a site
    answers its own search and posting pages where they are asked, so a hop
    to itself is refused too - and the page refused past PHENOM_PAGE_MAX
    bytes as they arrive, never pulled down whole to be refused (as a Jobvite
    or a JobScore feed is read: blind review of 2026-11-27). Decoded by the
    one charset rule (best_charset). A 429 is honoured at the gate and raised;
    any other answer but a 200 is an error; the network failing on the way,
    mid-body included, is a network error. Never the HTTP cache: _one keeps
    nothing (PEOPLE above)."""
    hops: list[str] = []

    def stay(u: str):
        hops.append(u)
        if u != url or len(hops) > 1:
            raise AtsError(f"{url} leads off to {str(u)[:160]}: a Phenom page answers where it is "
                           f"asked, so a redirect is not followed, and it is not read as a board "
                           f"with nothing open")
    resp = follow(url, lambda u: _one(u, stream=True), may=stay)
    try:
        status = getattr(resp, "status_code", None)
        if status == 429:
            _back_off(url, resp)
        if status != 200:
            raise AtsError(f"HTTP {status} for {url}")
        raw = bytearray()
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                raw += chunk
                if len(raw) > PHENOM_PAGE_MAX:
                    raise AtsError(f"{url} is more than {PHENOM_PAGE_MAX:,} bytes, this reader's "
                                   f"cap: it is not read, and never read in part")
        except requests.RequestException as exc:
            raise AtsError(f"network error: {exc}") from exc
        ctype = str((getattr(resp, "headers", None) or {}).get("content-type") or "")
        charset = best_charset(bytes(raw), ctype, getattr(resp, "encoding", None))
    finally:
        try:
            resp.close()
        except Exception:                          # noqa: BLE001
            pass
    try:
        return bytes(raw).decode(charset or "utf-8", errors="replace")
    except LookupError:                            # a charset Python does not know
        return bytes(raw).decode("utf-8", errors="replace")


def _ph_ddo(page: str, url: str) -> dict:
    """The page's own phApp.ddo object, where it carries exactly one, or AtsError."""
    marks = list(_PH_DDO.finditer(page))
    if len(marks) != 1:
        raise AtsError(f"{url} carries {len(marks) or 'no'} phApp.ddo block(s), not one: not a "
                       f"Phenom page in the shape measured, so it is not read")
    try:
        got = json.JSONDecoder().raw_decode(page, marks[0].end())[0]
    except ValueError as exc:
        raise AtsError(f"{url}: its phApp.ddo block is not JSON ({exc})") from exc
    if not isinstance(got, dict):
        raise AtsError(f"{url}: its phApp.ddo block is not an object")
    return got


def _ph_count(n) -> bool:
    return type(n) is int and n >= 0


def _ph_shows(ddo: dict) -> str | None:
    """Which place a site's own job cards show, as its page's
    jobwidgetsettings names their fields: 'locationName' (WEX's), 'location'
    for a page naming card fields without it (Fiserv's: location and
    multi_location), or None for a page that names none (the other four)."""
    said = ddo.get("jobwidgetsettings")
    data = said.get("data") if isinstance(said, dict) else None
    fields = data.get("fields") if isinstance(data, dict) else None
    names = {f.get("name") for f in fields if isinstance(f, dict)} if isinstance(fields, list) else set()
    names.discard(None)
    if not names:
        return None
    return "locationName" if "locationName" in names else "location"


def phenom_search(page: str, url: str, base: str,
                  term: str | None) -> tuple[list[dict], int, str | None]:
    """(the rows on one search page, the search's totalHits, which place its
    cards show - _ph_shows), or AtsError for a page that is not this site's
    answer to this search in the shape measured: its phApp naming this
    baseUrl, one ddo, eagerLoadRefineSearch with status 200, hits the rows on
    the page, totalHits, and the keyword it answered echoed as the one asked
    ('' unfiltered). A search with nothing in it is its one measured shape:
    hits 0, totalHits 0, jobs []."""
    app = phenom_app(page)
    if app is None or app.get("baseUrl") != base:
        raise AtsError(f"{url} is not this Phenom site's page: its phApp object is missing or names "
                       f"{str((app or {}).get('baseUrl'))[:120]!r}, not {base}")
    ddo = _ph_ddo(page, url)
    block = ddo.get("eagerLoadRefineSearch")
    data = block.get("data") if isinstance(block, dict) else None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    hits = block.get("hits") if isinstance(block, dict) else None
    total = block.get("totalHits") if isinstance(block, dict) else None
    said = data.get("keywords") if isinstance(data, dict) else None
    if (not isinstance(block, dict) or type(block.get("status")) is not int
            or block.get("status") != 200 or not _ph_count(hits) or not _ph_count(total)
            or not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs)
            or len(jobs) != hits or len(jobs) > total or not isinstance(said, str)):
        raise AtsError(f"{url} is not a Phenom search answer in the shape measured (status 200, "
                       f"hits the rows it carries, totalHits, jobs, keywords), so it is not read")
    if _text(said).casefold() != _text(term or "").casefold():
        raise AtsError(f"{url} answered the search {said[:80]!r}, not {term or ''!r}: a search that "
                       f"dropped its keyword (as s=1 does) is not read as that search")
    return jobs, total, _ph_shows(ddo)


def _ph_id(j: dict, url: str) -> str:
    jid = j.get("jobId")
    if not isinstance(jid, str) or not _PH_JOB_ID.fullmatch(jid):
        raise AtsError(f"{url}: a row has no jobId in the site's own form, so the search is not "
                       f"read in part")
    return jid


def phenom_walk(base: str, term: str) -> tuple[list[dict], str | None]:
    """(every row of one keyword search, once each by jobId, which place its
    cards show), or AtsError: paged by PHENOM_PAGE through ats._paged from
    the first page's totalHits, nothing asked past it, every page stating
    that same total and the same keyword, and the distinct rows exactly the
    total."""
    first = phenom_search_url(base, term)
    jobs, total, shows = phenom_search(_ph_read(first), first, base, term)
    if total > PHENOM_PAGE * PHENOM_MAX_PAGES:
        raise AtsError(f"{first} counts {total} postings, more than the {PHENOM_MAX_PAGES} pages "
                       f"of {PHENOM_PAGE} a search is read to: not read, and never read in part")

    def one(offset: int) -> list[dict]:
        if offset == 0:
            return jobs
        if offset >= total:
            return []                          # (past the total nothing is asked)
        url = phenom_search_url(base, term, offset)
        got, again, _shows = phenom_search(_ph_read(url), url, base, term)
        if again != total:
            raise AtsError(f"the Phenom search {term!r} on {base} counted {total} and then {again} "
                           f"between two pages, so it is not read in part")
        if not got:
            raise AtsError(f"{url} carried no row inside the search's own {total}: read in part, "
                           f"so not read")
        return got
    # (one page past the ceiling, which is never asked - one() answers it []
    # at the total - so a whole read of 591 to 600 rows ends on that empty
    # page and is not logged as cut: blind review of 2026-11-27)
    rows = _paged(one, lambda j: _ph_id(j, first), page=PHENOM_PAGE,
                  max_pages=PHENOM_MAX_PAGES + 1, label=f"{base} {term}")
    if len(rows) != total:
        raise AtsError(f"the Phenom search {term!r} on {base} counts {total} postings and its pages "
                       f"gave {len(rows)} distinct ones: the order is not stable between requests, "
                       f"and a walk that comes up short is never published as read")
    return rows, shows


def _ph_part(raw) -> str:
    t = _text(raw) if isinstance(raw, str) else ""
    return "" if t.lower() in _PH_NO_PLACE or "remote" in t.lower() else t


def phenom_place(j: dict, shows: str | None = None) -> tuple[str, dict | None]:
    """(the place as the site's own card shows it, an office_hint), `shows`
    being what the page's cards show (_ph_shows; see THE PLACE above). Where
    they show locationName - or, on a page naming no card fields, where the
    row carries one - the place is its ' | ' parts, each once (WEX's 'US -
    Remote', 'Portland, ME; Boston, MA'), and nothing else: the row's
    location there is a placeholder. Else it is the row's location (or
    cityStateCountry) and each multi_location[] once, '; ' between. The hint
    is the row's own city, state and country, a part that names no place
    (_PH_NO_PLACE, a way of working) left out, a state beside a city that
    names no place or in a placeholder address (_PH_NO_ADDRESS) left out too,
    and never a country the row does not state (Qualtrics' 'United States
    (Remote)' is a city with no country)."""
    named = _text(j.get("locationName")) if isinstance(j.get("locationName"), str) else ""
    if shows == "locationName" or (shows is None and named):
        place = "; ".join(dict.fromkeys(_text(p) for p in named.split("|") if _text(p)))
    else:
        primary = _text(j.get("location")) if isinstance(j.get("location"), str) else ""
        if not primary and isinstance(j.get("cityStateCountry"), str):
            primary = _text(j.get("cityStateCountry"))
        multi = j.get("multi_location")
        more = [_text(x) for x in multi if isinstance(x, str) and _text(x)] \
            if isinstance(multi, list) else []
        place = "; ".join(dict.fromkeys(([primary] if primary else []) + more))
    city_said = _text(j.get("city")) if isinstance(j.get("city"), str) else ""
    city = _ph_part(city_said)
    address = _text(j.get("address")) if isinstance(j.get("address"), str) else ""
    placeholder = bool(city_said and not city) or bool(_PH_NO_ADDRESS.search(address))
    return place, office_hint(city, "" if placeholder else _ph_part(j.get("state")),
                              _ph_part(j.get("country")))


def phenom_posted(j: dict) -> str | None:
    """dateCreated, or the earlier of it and postedDate where both read -
    never postedDate alone (a mass refresh moves it: see the note above)."""
    made = posted_date(j.get("dateCreated")) if isinstance(j.get("dateCreated"), str) else None
    if not made:
        return None
    said = posted_date(j.get("postedDate")) if isinstance(j.get("postedDate"), str) else None
    return min(made, said) if said else made


def phenom_row(j: dict, base: str, url: str = "the search", shows: str | None = None) -> dict:
    """One row, or AtsError for one that does not read as a posting (no
    jobId in the site's form, no title): a search is never read in part. No
    teaser, no applyUrl, no person: see the notes above."""
    jid, title = _ph_id(j, url), _text(j.get("title")) if isinstance(j.get("title"), str) else ""
    if not title:
        raise AtsError(f"{url}: a row has no title, so the search is not read in part")
    place, hint = phenom_place(j, shows)
    address = phenom_posting_url(base, jid, title)
    return {"title": title, "location": place, "url": address, "posted": phenom_posted(j),
            "office_hint": hint, "jd": "", "_detail_url": address}


def phenom_ad(url: str, base: str) -> str:
    """The employer's own ad, one posting's ddo.jobDetail.data.job.description,
    read only under FETCH_DETAILS - "" where the page gives none - or AtsError
    for a page that is not this posting on this site (_details then keeps the
    row as it is). Nothing else on the page is read: not its companyName, its
    client, its people or its JSON-LD."""
    m = _PH_POSTING.fullmatch(url)
    if not m or m.group(1) != base:
        raise AtsError(f"{url} is not a posting of {base}")
    page = _ph_read(url)
    app = phenom_app(page)
    job = _ph_ddo(page, url).get("jobDetail") if app and app.get("baseUrl") == base else None
    job = ((job.get("data") if isinstance(job, dict) else None) or {}).get("job")
    if not isinstance(job, dict) or job.get("jobId") != m.group(2):
        raise AtsError(f"{url} does not answer as that posting on {base}")
    desc = job.get("description")
    return plain_html(desc) if isinstance(desc, str) else ""


def fetch_phenom(ref) -> list[dict]:
    base = phenom_base(ref)
    rows, seen = [], set()
    for term in PHENOM_TERMS:
        got, shows = phenom_walk(base, term)
        for j in got:
            jid = _ph_id(j, base)
            if jid in seen:                    # the two searches overlap heavily
                continue
            seen.add(jid)
            rows.append(phenom_row(j, base, phenom_search_url(base, term), shows))
    if FETCH_DETAILS:
        _details(rows, lambda r: (phenom_ad(r["_detail_url"], base), None))
    return rows


def phenom_said(page: str) -> tuple[str, list[str]]:
    """(its og:site_name, its <title>s) as the page's <head> states them, its
    scripts, styles and comments left out - "" and [] where it states none.
    Read for verify_boards, which weighs them; nothing here reads a name for
    anything else."""
    low = lower_same(page or "")
    end = low.find("</head")
    head = (page or "")[:end] if end >= 0 else (page or "")
    head = _TAG.sub(" ", _COMMENT.sub(" ", head))
    site = ""
    for tag in _PH_META.findall(head):
        if "og:site_name" not in tag.lower():
            continue
        attrs = {k.lower(): (a or b) for k, a, b in _PH_ATTR.findall(tag)}
        if (attrs.get("property") or attrs.get("name") or "").lower() == "og:site_name":
            site = _text(html_lib.unescape(attrs.get("content") or ""))
            break
    titles = [_text(html_lib.unescape(t)) for t in _PH_TITLE.findall(head)][:4]
    return site, [t for t in titles if t]


_PH_KEPT: dict | None = None


@contextlib.contextmanager
def phenom_kept():
    """Each unfiltered page read is the answer to every later ask of it in the
    block (see ONE PLAN ASKS THE UNFILTERED PAGE ONCE above). Also a decorator."""
    global _PH_KEPT
    outer = _PH_KEPT
    _PH_KEPT = {} if outer is None else outer
    try:
        yield
    finally:
        _PH_KEPT = outer


def phenom_site(ref) -> dict:
    """{base, url, total, site_name, titles}: the site's UNFILTERED search
    page, one request - none inside phenom_kept() where it was read already -
    or AtsError. `total` is its totalHits, the whole locale board's count;
    `site_name` and `titles` what its <head> states (phenom_said)."""
    base = phenom_base(ref)
    url = phenom_search_url(base)
    kept = _PH_KEPT
    if kept is not None and url in kept:
        return dict(kept[url], titles=list(kept[url]["titles"]))
    page = _ph_read(url)
    _rows, total, _shows = phenom_search(page, url, base, None)
    site, titles = phenom_said(page)
    got = {"base": base, "url": url, "total": total, "site_name": site, "titles": titles}
    if kept is not None:
        kept[url] = dict(got, titles=list(titles))
    return got


def phenom_total(ref) -> int:
    """How many postings the WHOLE locale board holds, by its own count: the
    unfiltered search page's totalHits (see THE READER READS PART above)."""
    return phenom_site(ref)["total"]

# ---- successfactors: SAP SuccessFactors' Career Site Builder (CSB), a career
# site on the employer's own careers host (jobs.gainwelltechnologies.com,
# careers.landisgyr.com)
#
# The classic CSB list is a page the server draws, 25 postings to a page:
# <origin>/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow=N.
# Each posting is a <tr class="data-row">, its title link written twice (once
# for a desk, once for a phone) and its place and date in spans; the page's
# pagination label states the board's total. The ref is the careers host's
# https origin. Measured 2026-11-20 on six CSB hosts (Landis+Gyr 63, JENOPTIK
# 175, SPX 237, Indra 623) and 2026-11-22 on Gainwell's (334, 14 pages);
# robots.txt allows /search/, /go/ and /job/ on every one and closes
# /services/. Every read is held to the ref's host (same_host).
#
# THE NEWER "UNIFY" LAYOUT IS NOT READ FROM THIS LIST. It draws its list in
# the browser from POST /services/recruiting/v1/jobs, which robots.txt closes:
# its /search/ page has no rows (Aurigo's, Esri Canada's, Octave's), and its
# board is read from its sitemap instead (successfactors_unify, below). The
# legacy portals, career<N>.successfactors.(com|eu) and career<N>.sapsf.(com|
# eu|cn) ?company=..., say Disallow: / and are never a board; nor is any other
# host of SAP's (rmkcdn., hcm<N>., api<N>.: assets, sign-in, the Unify API).

SF_MAX_PAGES = 80        # 2,000 postings at 25 a page; Indra's 623 is the most read
_SF_SAP = re.compile(r"(?:^|\.)(?:successfactors\.(?:com|eu)|sapsf\.(?:com|eu|cn))$")
_SF_TR = re.compile(r"<tr\b([^<>]*)>", re.I)
_SF_TAG = re.compile(r"<(a|span)\b([^<>]*)>", re.I)
_SF_LABEL = re.compile(r"<span\b([^<>]*)>", re.I)
_SF_BOLD = re.compile(r"<b\b[^<>]*>([^<]{0,40})</b>", re.I)
_SF_LANG = re.compile(r"<html\b[^<>]{0,400}?\slang\s*=\s*[\"']?([A-Za-z]{2})", re.I)
# what only a Unify page carries: its results script, the React root its list
# is drawn into, and the API host its widget settings name (Esri Canada's
# /search/, 2026-11-20)
_SF_UNIFY = re.compile(r"j2w\.searchResultsUnify\b|\bapiURL\s*:\s*[\"']https://api\d+\.sapsf\."
                       r"|<div\b[^<>]{0,200}?\bid\s*=\s*[\"']root[\"']", re.I)
_SF_DESCRIBED = re.compile(r"<([a-z][a-z0-9]{0,15})\b([^<>]*\bitemprop\s*=\s*[\"']?description\b"
                           r"[^<>]*)>", re.I)
_SF_EN_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul",
                                             "aug", "sep", "oct", "nov", "dec"), 1)}
_SF_ES_MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
                 "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12}
_SF_TEXT_MAX = 4000      # of a row's markup one title, place or date is read from


def successfactors_origin(ref) -> str:
    """The https origin of a CSB career site, or AtsError: for anything that is
    not an origin (a path, a query, a port, a name before the host), and for
    SAP's own hosts, none of which is an employer's career site."""
    raw = _text(ref).rstrip("/")
    try:
        parts = urllib.parse.urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:                           # 'https://[x': no address at all
        parts, host = None, ""
    if parts is None or parts.scheme != "https" or not host \
            or (parts.netloc or "").lower() != host or parts.path or parts.query \
            or parts.fragment or not _HOSTNAME.fullmatch(host):
        raise AtsError(f"not a successfactors career site's origin: {ref!r}")
    if _SF_SAP.search(host):
        raise AtsError(f"{host} is one of SAP's own hosts (assets, sign-in, its API, or a "
                       f"legacy portal robots.txt closes), never an employer's career site")
    return f"https://{host}"


def successfactors_list_url(origin: str, row: int) -> str:
    """The classic list, newest first, from its `row`th posting."""
    return (f"{origin}/search/?q=&sortColumn=referencedate&sortDirection=desc"
            f"&startrow={row}")


def _sf_attrs(inner: str) -> dict:
    """A tag's attributes by lower-cased name (the first of each), as written."""
    out: dict = {}
    for m in _ATTR.finditer(inner or ""):
        out.setdefault(m.group(1).lower(), next((g for g in m.group(2, 3, 4) if g is not None), ""))
    return out


def _sf_classes(inner: str) -> set:
    return set(html_lib.unescape(_sf_attrs(inner).get("class") or "").lower().split())


def _sf_place(raw: str) -> str:
    """The row's place, with a placeholder city and postal code dropped.
    MEASURED ON GAINWELL'S BOARD (2026-11-22): a remote role's place is
    written 'Any city, TX, US, 99999' - the state and the country are all it
    says - so it reads 'TX, US'; and one open in more places than one ends in
    the board's own count of the rest ('Any city, AR, US, 99999 +1 more…', 14
    of its 334 rows), which stays: 'AR, US +1 more…'."""
    out = []
    for p in (p.strip() for p in _text(raw).split(",")):
        more = re.fullmatch(r"99999\s+(\+\s*[0-9]+\b.*)", p)
        if more and out:
            out[-1] += " " + more.group(1)
        elif p and p.lower() != "any city" and p != "99999" and not more:
            out.append(p)
    return ", ".join(out)


def _sf_day(raw: str, lang: str) -> str | None:
    """The row's date, in the three forms measured, or None - never a guess.
    'Nov 20, 2026' (en_US) and '21 nov 2026' (es_ES, Indra's) cannot be read
    two ways; '21.11.2026' (de_DE, JENOPTIK's) is read day first only on a page
    that says it is German."""
    t = _text(raw)
    m = re.fullmatch(r"([A-Za-z]{3})\.? ([0-9]{1,2}), ([0-9]{4})", t)
    if m:
        y, mo, d = int(m.group(3)), _SF_EN_MONTHS.get(m.group(1).lower()), int(m.group(2))
    else:
        m = re.fullmatch(r"([0-9]{1,2}) ([A-Za-z]{3,4})\.? ([0-9]{4})", t)
        if m:
            y, mo, d = int(m.group(3)), _SF_ES_MONTHS.get(m.group(2).lower()), int(m.group(1))
        else:
            m = re.fullmatch(r"([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})", t) if lang == "de" else None
            if not m:
                return None
            y, mo, d = int(m.group(3)), int(m.group(2)), int(m.group(1))
    try:
        return dt.date(y, mo, d).isoformat() if mo else None
    except ValueError:
        return None


_SF_DOTS = re.compile(r"\.{1,2}(?:;.*)?", re.S)


def _sf_plain_path(path: str) -> bool:
    """Does this path say plainly where it goes? Not if a segment is, raw or
    once any layer of its escapes is undone, a dot or two (or '..;...'), if a
    segment is empty, or if it holds a backslash: a server that resolves them
    serves another path than the one robots.txt was asked about. robots.txt
    is asked of the address as written, and '/job/%2e%2e/services/...' went
    out as '/job/../services/...' - the path every CSB robots.txt closes
    (seventh blind review, 2026-11-22). Every CSB address read here is plain:
    /search/, /job/<slug>/<id>/."""
    p = path or "/"
    for _ in range(4):
        segs = p.split("/")[1:]
        if segs and segs[-1] == "":
            segs = segs[:-1]                     # a last slash
        if "\\" in p or any(s == "" or _SF_DOTS.fullmatch(s) for s in segs):
            return False
        undone = urllib.parse.unquote(p)
        if undone == p:
            return True
        p = undone
    return False                                 # escapes four deep say nothing plainly


def _sf_posting_url(href: str, origin: str) -> str | None:
    """A row's link as the posting's address on this career site, or None for
    one that leaves it, is not a posting (/job/...), or does not say plainly
    where it goes (_sf_plain_path)."""
    h = html_lib.unescape(href or "").strip()
    if not h:
        return None
    try:
        url = urllib.parse.urljoin(origin + "/", h)
        parts = urllib.parse.urlsplit(url)
    except ValueError:                           # an href that is no address ('http://[')
        return None
    if f"{parts.scheme}://{(parts.netloc or '').lower()}" != origin \
            or not parts.path.startswith("/job/") or not _sf_plain_path(parts.path):
        return None
    return url


class PostingClosed(AtsError):
    """A posting the list carries answered, as the career site, that it has
    closed by sending a reader from its address back to the site's own list
    (/search/). A hop anywhere else off /job/ says nothing of the posting:
    a block page or a challenge can answer so (_sf_hop). The site's own page
    at its address saying so is successfactors_filled."""


def _sf_hop(url: str, offset: int | None = None, posting: bool = False):
    """The rule each hop of one CSB read is held to, asked BEFORE the hop is
    (on top of same_host): a path that says plainly where it goes; for a list
    page past the first, an address that still asks for that page (a hop that
    drops its startrow lands on page 1, and the walk would read page 1 as
    page 2); for a posting, a /job/ address. From a posting, a hop to the
    site's list is the posting closed (PostingClosed); a hop anywhere else
    is not read, and says nothing of the posting - not that it closed
    (eighth blind review, repaired 2026-11-22: any hop off /job/ was read as
    closed, so a challenge page passed a posting over)."""
    def may(u: str):
        parts = urllib.parse.urlsplit(u)
        if not _sf_plain_path(parts.path):
            raise AtsError(f"{url} leads off to {parts.path}, a path that does not say plainly "
                           f"where it goes")
        if offset:
            rows = urllib.parse.parse_qs(parts.query).get("startrow")
            if rows != [str(offset)]:
                raise AtsError(f"{url} leads off to {parts.path}?{parts.query[:80]}, which does "
                               f"not ask for the list from row {offset}")
        if posting and not parts.path.startswith("/job/"):
            if parts.path in ("/search", "/search/"):
                raise PostingClosed(f"the posting {url} answers by sending a reader to the site's "
                                    f"list ({parts.path}): it has closed")
            raise AtsError(f"a posting answers by sending a reader to {parts.path[:60]}, neither a "
                           f"posting nor the site's list (a block or challenge page), so it says "
                           f"nothing of the posting: {url}")
    return may


def _sf_row(cell: str, origin: str, lang: str) -> dict | None:
    """One posting from one data row's markup: its first titled /job/ link, its
    first place and its first date. The phone's copy of each comes later in
    the row and says the same; a row with no titled posting link is none."""
    low = lower_same(cell)
    shut = {"a": low.find("</a>"), "span": low.find("</span>")}
    nxt = -1                                     # the next <a after this one
    title = url = where = when = None
    for m in _SF_TAG.finditer(cell):
        name, said = m.group(1).lower(), m.group(2).lower()
        if "jobtitle-link" not in said and "joblocation" not in said and "jobdate" not in said:
            continue
        if 0 <= shut[name] < m.end():
            shut[name] = low.find(f"</{name}>", m.end())   # forward only: one pass a row
        if shut[name] < 0:
            continue
        cls = _sf_classes(m.group(2))
        if name == "a" and url is None and "jobtitle-link" in cls:
            # (a link's words end where the next link opens, as a browser
            # ends them: a row of links with one </a> is read once)
            if nxt < m.end():
                nxt = _tag_at(low, "a", m.end())
            stop = min(x for x in (shut[name], nxt, m.end() + _SF_TEXT_MAX) if x >= 0)
            text = _jv_text(cell[m.end():stop])
            got = _sf_posting_url(_sf_attrs(m.group(2)).get("href"), origin)
            if got and text:
                url, title = got, text
        elif name == "span" and where is None and "joblocation" in cls:
            where = _jv_text(cell[m.end():min(shut[name], m.end() + _SF_TEXT_MAX)])
        elif name == "span" and when is None and "jobdate" in cls:
            when = _jv_text(cell[m.end():min(shut[name], m.end() + _SF_TEXT_MAX)])
    if not url:
        return None
    return {"title": title, "location": _sf_place(where or ""), "url": url,
            "posted": _sf_day(when or "", lang)}


_SF_NUMBER = r"([0-9]{1,3}(?:[.,\u00a0\u202f]?[0-9]{3})*)"
_SF_RANGE = re.compile(rf"\s*{_SF_NUMBER}\s*[-\u2010-\u2015]\s*{_SF_NUMBER}\s*")
_SF_TOTAL = re.compile(rf"\s*{_SF_NUMBER}\s*")


def _sf_total(bold: list[str], offset: int, n: int) -> int | None:
    """The board's total from its pagination label's bold parts, or None.
    THE LABEL IS A RANGE AND A TOTAL ('Results <b>1 – 25</b> of <b>334</b>',
    'Ergebnisse ... von', 'Resultados ... de'), believed only when the range
    is this page's: it starts at the row after `offset`, it spans the page's
    own `n` rows, and the total reaches its end. Any other shape - 'Page
    <b>1</b> of <b>25</b>', three bold parts, the total written first, a
    total of 0 beside the page's rows - is no total, so a part of a board is
    never read as the whole of it (eighth blind review, 2026-11-22)."""
    if len(bold) != 2:
        return None
    span = _SF_RANGE.fullmatch(html_lib.unescape(bold[0]))
    said = _SF_TOTAL.fullmatch(html_lib.unescape(bold[1]))
    if not span or not said:
        return None
    a, b, total = (int(re.sub(r"[^0-9]", "", x)) for x in (span.group(1), span.group(2), said.group(1)))
    if a != offset + 1 or b - a + 1 != n or total < b or total > 9_999_999:
        return None
    return total


def successfactors_list(page: str, origin: str, offset: int = 0) -> tuple[int, list[dict], int | None]:
    """(how many posting rows one list page carries, the postings on it, the
    total the board states) - read off the page as a reader sees it (_cut): a
    row in a comment, a script or a template is not a row. Each row runs to its
    </tr>, or to the next row where one is left unclosed. `offset` is the row
    the page was asked from (startrow): its label's range must start after it
    (_sf_total)."""
    body = _cut(page or "")
    said = _SF_LANG.search(body[:20000])
    lang = said.group(1).lower() if said else ""
    starts = [m for m in _SF_TR.finditer(body)
              if "data-row" in m.group(1).lower() and "data-row" in _sf_classes(m.group(1))]
    low, rows = lower_same(body), []
    tr = low.find("</tr>")
    for k, m in enumerate(starts):
        if 0 <= tr < m.end():
            tr = low.find("</tr>", m.end())
        nxt = starts[k + 1].start() if k + 1 < len(starts) else len(body)
        row = _sf_row(body[m.end():min(tr, nxt) if tr >= 0 else nxt], origin, lang)
        if row:
            rows.append(row)
    # the total: the pagination label's range and total, read together
    # (_sf_total: 'Results <b>1 – 25</b> of <b>334</b>', in any of its languages)
    total = None
    for m in _SF_LABEL.finditer(body):
        if "paginationlabel" not in m.group(1).lower() \
                or "paginationlabel" not in _sf_classes(m.group(1)):
            continue
        end = low.find("</span>", m.end())
        bold = _SF_BOLD.findall(body[m.end():end if 0 <= end < m.end() + 600 else m.end() + 600])
        total = _sf_total(bold, offset, len(starts))
        break
    return len(starts), rows, total


def successfactors_board(ref) -> tuple[str, str, list[dict], int, int | None]:
    """(the site's origin, its first list page, the postings on it, how many
    rows it carries, the total it states), or AtsError: for a ref that is not
    a CSB site's origin, for a page off its host, and for a first page that
    lists nothing - a Unify site, which says why, or a page nobody has seen a
    CSB board with nothing open draw, which is not read as a zero."""
    origin = successfactors_origin(ref)
    first = successfactors_list_url(origin, 0)
    page = _page(first, same_host=True, may=_sf_hop(first)).text or ""
    n, rows, total = successfactors_list(page, origin)
    if not n:
        if _SF_UNIFY.search(page):
            # (its board is read from its sitemap: successfactors_unify)
            raise SuccessFactorsUnify(f"{origin} is a SuccessFactors 'Unify' career site: it draws "
                                      f"its list in the browser from POST /services/recruiting/v1/"
                                      f"jobs, which its robots.txt closes, so its list is not this "
                                      f"page (and it is not a board with nothing open)", origin)
        raise AtsError(f"{origin}/search/ carried no posting rows and is not a Unify site; a "
                       f"SuccessFactors career site with nothing open has not been seen, so "
                       f"this is not read as a zero")
    if not rows:
        raise AtsError(f"{origin}/search/ carried {n} posting rows and not one titled link "
                       f"to a posting on the site, so it is not read as a zero")
    return origin, page, rows, n, total


# How many rows a read may come up short of what the board's list carries: a
# posting or two that closed while it was read (each closing moves every later
# row up one, past the page boundary), or a row or two that gave no posting.
SF_SLACK = 2


def successfactors_walk(origin: str, first: list[dict], n: int, total: int | None) -> list[dict]:
    """Every posting on the list from its first page on, stepping by that
    page's own row count, each posting once.

    WITH THE BOARD'S OWN TOTAL KNOWN, THE WALK MUST REACH IT (seventh blind
    review, 2026-11-22). A complete read ends at an offset at or past the
    total, which is not asked. A page inside the board that adds no new
    posting - empty, page 1 again, the board's past-the-end list of its
    newest postings served in the middle - means the read went wrong, and
    the rows so far are part of the board, not the board: an AtsError. So is
    a read that reaches the total more than SF_SLACK rows short of it, and so
    is the ceiling with or without a total. With no total, a page that adds
    nothing is the end (past its end Gainwell's list shows its newest
    postings again).

    THE SLACK IS FOR CHURN, AND IT IS A ROW OR TWO (eighth blind review,
    2026-11-22). It was a page: 24 of 25 rows that gave no posting - a title
    link off the site, an empty title, a path that does not say plainly
    where it goes - vanished inside it without a word. Every page's rows
    that gave no posting are counted, and more than SF_SLACK of them in one
    read is an AtsError. The LAST page inside the total may add nothing -
    the postings that closed while the board was read moved it past the
    end, where the list serves its newest again - and the read is still
    held to the total. A total below page 1's own rows is no total: the
    walk never returns fewer rows than page 1 carried."""
    if total is not None and total < n:
        total = None
    rows, seen, unread = [], set(), 0
    for i in range(SF_MAX_PAGES):
        offset = i * n
        if total is not None and offset >= total:
            break
        if i == 0:
            k, got = n, first
        else:
            url = successfactors_list_url(origin, offset)
            k, got, _t = successfactors_list(_page(url, same_host=True, may=_sf_hop(url, offset)).text
                                             or "", origin, offset)
        unread += max(0, k - len(got))
        if unread > SF_SLACK:
            raise AtsError(f"{origin}'s list carried {unread} rows that gave no posting (a title link "
                           f"off the site or to no posting, an empty title, an address that does not "
                           f"say plainly where it goes): read in part, so not read")
        fresh = []
        for r in got:
            if r["url"] not in seen:
                seen.add(r["url"])
                fresh.append(r)
        if not fresh:
            if total is None:
                return rows
            if offset + n < total:
                raise AtsError(f"{origin}'s list page from row {offset + 1} added no posting, and "
                               f"the list states {total}: read in part, so not read")
            break                       # the last page, moved past the end while it was read
        rows.extend(fresh)
    else:
        raise AtsError(f"{origin}'s list did not end within {SF_MAX_PAGES} pages ({len(rows)} postings"
                       f"{f' of {total}' if total is not None else ''}): read in part, so not read")
    if len(rows) < total - SF_SLACK:
        raise AtsError(f"{origin}'s list states {total} postings and its pages carried "
                       f"{len(rows)}: read in part, so not read")
    return rows


def successfactors_posting(url: str, origin: str, landed: list | None = None,
                           known: dict | None = None) -> str:
    """One posting's page, asked only on the career site's own host, at an
    address that says plainly where it goes; a hop to the site's list is the
    posting gone (PostingClosed), and a hop anywhere else off /job/ is not
    read (_sf_hop). `landed`, when given, is handed each address the read
    goes to, in order - the posting's own, then every hop it is let take -
    so its last is where the page came from (a Unify sitemap's address hops
    to /job/<slug>/<N>-<locale>/), or where an answer that was no page came
    from. A HOP BACK TO AN ADDRESS ALREADY ASKED is a loop, not read (blind
    review 10, 2026-11-23: a posting and its hop sending a reader to each
    other were asked eleven times in all before the hop limit ended it).
    `known`, when given, is every address a Unify read has already been to,
    whatever it answered there: a hop to one of them is not asked again
    (_SfKnown)."""
    if _sf_posting_url(url, origin) != url:
        raise AtsError(f"{url} is not a posting of {origin}")
    rule, chain = _sf_hop(url, posting=True), []

    def may(u: str):
        if u in chain:
            raise AtsError(f"{url} sends a reader round in a loop (back to {u[:120]}), so it says "
                           f"nothing of the posting")
        rule(u)
        if known and u != url and u in known:
            raise _SfKnown(u)
        chain.append(u)
        if landed is not None:
            landed.append(u)
    return _page(url, same_host=True, may=may).text or ""


# what only a page showing a posting carries: the posting's own microdata
_SF_SHOWN = re.compile(r"\bitemprop\s*=\s*[\"']?(?:title|description|datePosted|hiringOrganization)\b"
                       r"|\bitemtype\s*=\s*[\"']?https?://schema\.org/JobPosting\b", re.I)
_SF_SAID_IN = re.compile(r"<[a-z][a-z0-9]{0,15}\b[^<>]*\bitemprop\s*=\s*[\"']?(?:title|description)\b"
                         r"[^<>]*>", re.I)
_SF_LANG_ATTR = re.compile(r"(?<![\w:-])lang\s*=\s*[\"']?([A-Za-z]{2,3})\b")


def successfactors_shown(page: str) -> bool:
    """Does this posting page show a posting (its schema.org microdata), as the
    page a reader sees it? A closed one shows none - and neither does a page
    that is not the site's at all (successfactors_filled tells them apart)."""
    return bool(_SF_SHOWN.search(_cut(page or "")))


# WHAT A CLOSED POSTING'S OWN PAGE SAYS IN ITS PLACE. Gainwell's answer 200 at
# the posting's address with the career site's own page (measured 2026-11-22):
# the posting's title kept ('Provider Enrollment Specialist - Jackson, MS
# (Onsite) Job Details | Gainwell Technologies LLC') and, where the posting
# was, "Sorry, this position has been filled." ONLY THE WORDS ARE THE SITE
# SAYING IT CLOSED: every OPEN posting page measured carries that same title
# (Landis+Gyr's, Aurigo's, Gainwell's), so the title says only that the career
# site answered (blind review 9, 2026-11-23: a page in the site's own template,
# titled for the role, with an error where the posting was, read as closed).
_SF_FILLED = re.compile(r"\bthis position has been filled\b", re.I)


def successfactors_filled(page: str) -> bool:
    """Is this page, at a posting's own address, the career site saying that
    the posting has closed: a page that shows no posting and says "this
    position has been filled", as a reader sees it (_cut: never inside a
    comment or a script)? A page that shows no posting and does not say so -
    a block page served 200 (F5's "The requested URL was rejected"), an error
    page, the site's own template titled for the role ('<role> Job Details |
    <site>') with no posting in it - is not the site's word, and says nothing
    of the posting (eighth blind review, repaired 2026-11-22: every such page
    was read as closed, and a board was stated whole on the two of its seven
    postings that answered; blind review 9, 2026-11-23: the posting's title
    alone was still read as the site's word)."""
    body = _cut(page or "")
    return not _SF_SHOWN.search(body) and bool(_SF_FILLED.search(_jv_text(body)))


def successfactors_language(page: str) -> str:
    """The language a posting is written in, as its own title or description
    element says ('en' of lang="en-US", Landis+Gyr's and Aurigo's), or ""."""
    for m in _SF_SAID_IN.finditer(_cut(page or "")):
        said = _SF_LANG_ATTR.search(m.group(0))
        if said:
            return said.group(1).lower()
    return ""


def successfactors_ad(page: str) -> str:
    """The ad on a posting page: EVERY itemprop="description" element of it,
    in page order, each to the close that matches it, as text, one after
    another (the class="jobdescription" span sits inside one on Landis+Gyr's,
    4,939 characters; Esri Canada's postings split theirs into three, the
    pay sentence in the third - blind review 10, 2026-11-23: only the first
    was read, so its pay never was). One inside another is read once, with
    the one it sits in. "" for a page with none, or one that never closes
    one of them: an ad is not read in part."""
    body = _cut(page or "")
    parts, past = [], 0
    for m in _SF_DESCRIBED.finditer(body):
        if m.start() < past:
            continue                     # (inside one read already)
        name = m.group(1).lower()
        if name == "meta":
            parts.append(plain_html(html_lib.unescape(_sf_attrs(m.group(2)).get("content") or "")))
            past = m.end()
            continue
        inner = _sf_inner(body, m.end(), name, len(body))
        if inner is None:
            return ""
        parts.append(plain_html(inner))
        past = m.end() + len(inner)
    return "\n".join(p for p in parts if p)


def successfactors_closed(exc: BaseException) -> bool:
    """Did asking a posting's own page answer that it has closed: a hop to the
    site's list (PostingClosed), or a 404 or 410 at its address? A refusal, a
    429, a server error, the network failing or a hop anywhere else says
    nothing about the posting."""
    return isinstance(exc, PostingClosed) or (
        isinstance(exc, AtsError) and not isinstance(exc, RobotsRefused)
        and bool(re.match(r"HTTP 4(?:04|10)\b", str(exc))))


def fetch_successfactors(ref) -> list[dict]:
    try:
        again = _unify_night_read(successfactors_origin(ref))
    except AtsError:
        again = None                    # (successfactors_board says why)
    if again is not None:
        return again
    try:
        origin, _page1, first, n, total = successfactors_board(ref)
    except SuccessFactorsUnify as unify:
        return successfactors_unify(unify.origin)
    # (a partial read is an error, never the board: successfactors_walk)
    rows = successfactors_walk(origin, first, n, total)
    if FETCH_DETAILS:
        # the ad is on each posting's own page; CSB publishes no pay field, so
        # any figure comes from the ad's text (plain_rows), never from here.
        # A POSTING WHOSE OWN PAGE SAYS IT HAS CLOSED IS NOT PUBLISHED AS OPEN
        # (eighth blind review, 2026-11-22): a 404 or 410, a hop to the site's
        # list, or the site's own page saying so (successfactors_filled:
        # Gainwell's say "Sorry, this position has been filled.") - the row
        # is dropped, and said. Where every posting asked answers so, that is
        # not read as a zero either. A page that shows no posting and is not
        # the site saying so - a block page served 200, an error page (the
        # site's own template titled for the role included: blind review 9),
        # a hop anywhere else - says nothing of the posting, as a refusal or a
        # 429 says nothing: the row stays as the list states it.
        closed = set()

        def read(r):
            try:
                page = successfactors_posting(r["url"], origin)
            except AtsError as exc:
                if successfactors_closed(exc):
                    closed.add(r["url"])
                raise
            if not successfactors_shown(page):
                if not successfactors_filled(page):
                    raise AtsError(f"{r['url']} answered a page that shows no posting and is not "
                                   f"the site saying it has closed")
                closed.add(r["url"])
                return "", None
            return successfactors_ad(page), None
        _details(rows, read)
        if closed:
            if all(r["url"] in closed for r in rows):
                raise AtsError(f"{origin}: every one of the {len(rows)} postings on its list answered "
                               f"as closed on its own page, so it is not read as a board with "
                               f"nothing open")
            print(f"  successfactors: {origin}: {len(closed)} of the {len(rows)} postings on its list "
                  f"answered as closed on their own page (a 404 or 410, a hop to its list, or the "
                  f"site's page saying so) and are not published", file=sys.stderr)
            rows = [r for r in rows if r["url"] not in closed]
    return rows


# ---- successfactors "Unify": the career site's sitemap, then each posting
#
# Owner, 2026-11-23: "yes, build the sitemap reader for those three" (Aurigo,
# Esri Canada and Octave). A Unify site draws its list in the browser from
# POST /services/recruiting/v1/jobs, which its robots.txt closes, so its
# /search/ page lists no rows (successfactors_board says so, as
# SuccessFactorsUnify). What robots.txt leaves open is <origin>/sitemap.xml -
# a <urlset> of <url><loc>https://<host>/job/<slug>/<id>/</loc>, one a
# posting - and each posting's own page. Measured 2026-11-23 on the three
# (robots.txt byte-identical on each, /services/ closed, /sitemap.xml and
# /job/ open): Aurigo 16 locs, Esri Canada 32 (about 16 roles, each once in
# English and once in French), Octave 131. Every <lastmod> read 2026-11-21:
# it is not a posted date, and is never read. A loc carries XML's escapes
# (Esri Canada's /job/Sp%C3%A9cialiste-d&apos;applications%2C-.../), undone
# before it is anything. Each loc answers 302 to /job/<slug>/<N>-<locale>/
# (Aurigo's .../55832644/ to .../2249-en_US/; Octave's page says "Req Id:
# 3378" of its .../3378-en_US/), and a role's English and French copies land
# on the SAME N (Esri Canada's .../663-en_US/ and .../663-fr_CA/): one
# requisition, published once, in English where there is one. The row's url
# is the sitemap's address, never where it landed: Esri Canada's server
# writes a French posting's Location twice encoded, and the page answers it.
#
# A READ costs its /search/ page, its sitemap and two requests a posting (the
# loc and its hop), and robots.txt once: Octave 264, Esri Canada 66, Aurigo
# 34. A NIGHT IS ONE READ (blind review 10, 2026-11-23): the owner's yes was
# to "one or two requests per posting per night", and the nightly job reads
# every wired board twice - refresh.py for its status, then build_board.py
# for its postings, in separate processes - so a night cost two reads, about
# four requests a posting (Octave 530). GOVTECH_DOCK_UNIFY_NIGHT names a
# file the job's two steps share (refresh.yml sets it in the runner's temp
# directory: never committed, gone with the run): a Unify read that comes
# whole is written there, and a later read of that board in the same run,
# within UNIFY_NIGHT_MAX_AGE, is that one, asking nothing. A read that fails
# writes nothing, so the next step asks for itself: refresh asks a 429 once
# more, build_board a 429 or the network, and each read asks its failing
# posting twice, so a failing night costs up to about four reads on a
# lasting 429, three on a lasting network failure, two on a 5xx (blind
# review 11, 2026-11-23). A read made without the ads a caller wants
# (FETCH_DETAILS) is asked again. Other reads on a day - scope_review.py's,
# of a company with openings nobody has ruled on; a person's - are their
# own. The sitemap is held to SF_SITEMAP_MAX addresses
# (a cap on requests, not roles) and a sitemap index to one level of
# SF_SITEMAPS_MAX sitemaps on the site's own origin.
#
# THE POSTING'S OWN PAGE IS THE ROW: there is no list to fall back on. A
# posting the site says has closed (successfactors_closed: a 404 or 410, a
# hop to its list; or its page saying so, successfactors_filled) is left out
# and said - up to SF_CLOSED_SHARE of the sitemap: more is not a day's churn,
# and the read is refused. A FAILURE OF THE DAY on a posting - a 429, a 5xx,
# the network failing or timing out - is asked once more (after the gate's
# backoff for a 429, SF_RETRY_PAUSE for the rest), so one flaky request
# costs one request more, never the board (blind review 10: one 5xx among a
# read's ~265 took the board off for the night). A second one fails the
# whole read, as it came, as any reader's failure of the day does: refresh
# asks the whole board once more on a 429 (not on a 5xx or the network),
# build_board on a 429 or the network (not on a 5xx), and a board that still
# will not read is Unknown for the night, never "None found" - its postings
# are not on the board that night, and the company reads as one we could
# not read. Anything else a posting answers that is not the posting - a page
# showing none that is not the site saying it closed, robots.txt closing it,
# a refusal, a hop off /job/ or off the host, a hop round in a loop - is left
# out and said, up to SF_SLACK postings; more is the board unreadable, never
# part of it.

# Sitemap addresses one read asks, at two requests each: a cap on this
# reader's requests, not on roles - a site listing each role in English and
# in French reaches it at 200 roles (blind review 10, 2026-11-23), short of
# the owner's giants of about 500+ roles.
SF_SITEMAP_MAX = 400
SF_SITEMAPS_MAX = 5      # sitemaps one sitemap index may name, followed one level only
SF_RETRY_PAUSE = 2.0     # seconds before a posting's 5xx or network failure is asked once more
UNIFY_NIGHT = os.environ.get("GOVTECH_DOCK_UNIFY_NIGHT", "")    # the run's shared read (above)
UNIFY_NIGHT_MAX_AGE = dt.timedelta(hours=6)                     # longer than the job may run (5h)
_UNIFY_NIGHT_LOCK = threading.Lock()
# More of a sitemap than this answering closed in one read is not a day's
# postings closing - a site answering 404 for postings it has, a soft block,
# a sitemap long out of date - and the read is refused, never published as
# the board (blind review 10, 2026-11-23: 130 of 131 answering 404 published
# one row). A quarter, build_site.MAX_DROP's line for a board falling
# overnight; never fewer than SF_SLACK.
SF_CLOSED_SHARE = 0.25
# The tokens a posting's place is read from, the first shown with a value
# (Aurigo's 'Posting Location', Octave's 'Job Location (Short)'), else its
# 'Country' (Esri Canada's, which shows no place: its city is only in the ad).
_SF_PLACES = ("posting location", "job location (short)", "job location", "location",
              "work location")
_SM_OPEN = re.compile(r"<(?![?!/])([A-Za-z_][\w.:-]{0,80})")       # an element, not a PI or a close
_SM_LOC = re.compile(r"<loc\b[^<>]*>([^<]*)</loc\s*>")
_SM_LOC_TAG = re.compile(r"<loc\b")
_XML_REF = re.compile(r"&(?:(lt|gt|amp|quot|apos)|#([0-9]{1,7})|#[xX]([0-9A-Fa-f]{1,6}));")
_XML_NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}
_SF_TITLED = re.compile(r"<([a-z][a-z0-9]{0,15})\b([^<>]*\bitemprop\s*=\s*[\"']?title\b[^<>]*)>", re.I)
_SF_META = re.compile(r"<meta\b([^<>]*)>", re.I)
_SF_HTML_LANG = re.compile(r"<html\b[^<>]{0,400}?\slang\s*=\s*[\"']?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8})*)",
                           re.I)
_SF_MDY = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{2})")
_SF_REQUISITION = re.compile(r"([0-9]{1,15})-([A-Za-z]{2,3}(?:_[A-Za-z0-9]{2,8})?)")


class SuccessFactorsUnify(AtsError):
    """successfactors_board's word that a career site's /search/ page is a
    Unify site's, which lists no rows: its board is read from its sitemap
    (successfactors_unify), and its name from its postings' own titles
    (verify_boards._successfactors_unify)."""

    def __init__(self, why: str, origin: str):
        super().__init__(why)
        self.origin = origin


def _xml_text(raw: str) -> str | None:
    """XML character data as the text it stands for: the five entities XML
    defines and character references, undone in one pass ('&amp;apos;' is the
    six characters '&apos;'). None where a '&' begins anything else - an
    entity a DOCTYPE would define, or a bare '&' - which does not read
    plainly."""
    refs = list(_XML_REF.finditer(raw or ""))
    if (raw or "").count("&") != len(refs):
        return None
    out, at = [], 0
    for m in refs:
        n = None if m.group(1) else int(m.group(2) or m.group(3), 10 if m.group(2) else 16)
        if n is not None and not (0 < n <= 0x10FFFF and not 0xD800 <= n <= 0xDFFF):
            return None
        out.append(raw[at:m.start()])
        out.append(_XML_NAMED[m.group(1)] if m.group(1) else chr(n))
        at = m.end()
    out.append(raw[at:])
    return "".join(out)


def _sf_services(path: str) -> bool:
    """Is this path under /services/ (the Unify search API, the RSS), as
    written, as a server serves it (robots.served: a ';...' path parameter
    cut off each segment - '/services;jsessionid=1/...' is /services/...,
    blind review 10 - and a dot segment resolved), or with any layer of its
    escapes undone?"""
    p = path or "/"
    for _ in range(4):
        if any(q.lstrip("/").split("/", 1)[0].lower() == "services" for q in (p, robots.served(p))):
            return True
        undone = urllib.parse.unquote(p)
        if undone == p:
            return False
        p = undone
    return True                                  # escapes four deep say nothing plainly


def _sf_map_hop(url: str):
    """The rule each hop of a sitemap read is held to, asked BEFORE the hop is
    (on top of same_host): a path that says plainly where it goes, and never
    one under /services/ - refused here whatever robots.txt says."""
    def may(u: str):
        path = urllib.parse.urlsplit(u).path or "/"
        if not _sf_plain_path(path) or _sf_services(path):
            raise AtsError(f"{url} leads off to {path[:80]}, which is not a sitemap this reader "
                           f"asks (a path that does not say plainly where it goes, or one under "
                           f"/services/)")
    return may


def _sf_sitemap_page(url: str, origin: str) -> str:
    """One sitemap's text, asked as every page is (robots.txt at every hop,
    the site's own host only, _sf_map_hop), or AtsError. A 404 or 410 says
    only that no sitemap is there: the career site answered its /search/
    page, so its board is unreadable, never gone."""
    try:
        return _page(url, same_host=True, may=_sf_map_hop(url)).text or ""
    except AtsError as exc:
        status = re.match(r"HTTP (4(?:04|10))\b", str(exc))
        if status and not isinstance(exc, RobotsRefused):
            raise AtsError(f"{origin} is a SuccessFactors 'Unify' career site: it draws its list in "
                           f"the browser from POST /services/recruiting/v1/jobs, which its "
                           f"robots.txt closes, and {url} answered HTTP {status.group(1)}: there is "
                           f"no sitemap to read its board from (and it is not a board with "
                           f"nothing open)") from exc
        raise


def _sf_sitemap_read(text: str, url: str) -> tuple[str, list[str]]:
    """(its root element, every <loc>'s text as written) of one sitemap, as an
    XML reader reads it - a commented-out <url> is not listed - or AtsError
    for a page that is not a sitemap, a comment left open, a <loc> holding
    markup (CDATA), or a text that does not end with its root's close (cut
    short): an address that cannot be read plainly, or one the cut took, is
    never a posting silently lost."""
    kept, i = [], 0
    while True:
        j = text.find("<!--", i)
        if j < 0:
            kept.append(text[i:])
            break
        k = text.find("-->", j + 4)
        if k < 0:
            raise AtsError(f"{url} leaves a comment open, so what it lists does not read plainly")
        # (taken out with nothing in its place, as XML takes a comment out of
        # character data: a space put there went into a <loc>'s address -
        # '/job/ Role-5/...' asked and published - blind review 10)
        kept.append(text[i:j])
        i = k + 3
    body = "".join(kept)
    first = _SM_OPEN.search(body)
    root = first.group(1) if first else ""
    if root not in ("urlset", "sitemapindex"):
        raise AtsError(f"{url} is not a sitemap (its first element is "
                       f"{'<' + root[:40] + '>' if root else 'none'})")
    locs = _SM_LOC.findall(body)
    if len(_SM_LOC_TAG.findall(body)) != len(locs):
        raise AtsError(f"{url} lists an address this reader cannot read plainly (markup inside a "
                       f"<loc>), so its board is not read in part")
    # A SITEMAP CUT SHORT IS NOT THE BOARD (blind review 10, 2026-11-23): it
    # states no total to hold a read to, so its root's close is the only sign
    # it came whole. Cut between two <url>s, or in a <lastmod>, the part before
    # the cut read as every posting the board has, and nothing was said - an
    # XML reader refuses that text. So the text must end with the root's
    # close (or the root be empty, '<urlset/>'), only whitespace or a
    # processing instruction after it - and so after every <loc>.
    opened = re.compile(rf"<{root}\b[^<>]*>").search(body, first.start())
    shut = list(re.finditer(rf"</{root}\s*>", body))
    whole = (opened is not None and opened.group(0).endswith("/>") and not locs) or (
        bool(shut) and not re.sub(r"<\?[^<>]*\?>", "", body[shut[-1].end():]).strip())
    if not whole:
        raise AtsError(f"{url} does not close its <{root}> after the last address it lists: it "
                       f"came cut short, so its board is not read in part")
    return root, locs


def _sf_child_sitemap(u: str, origin: str) -> bool:
    """Is this address, named by a sitemap index, a sitemap of this career
    site's own: https on its origin, a plain path, nothing under /services/?"""
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return False
    return (f"{parts.scheme}://{(parts.netloc or '').lower()}" == origin and not _UNPLAIN.search(u)
            and _sf_plain_path(parts.path) and not _sf_services(parts.path))


def _sf_loc(raw: str, url: str) -> str:
    """A <loc>'s address, its XML escapes undone, or AtsError for one that does
    not read as XML (a bare '&', an entity XML does not define) or holds a
    control character: what it names is not known, so no posting it might be
    is silently lost."""
    u = _xml_text(raw.strip())
    if u is None or _UNPLAIN.search(u):
        raise AtsError(f"{url} lists an address that does not read plainly as XML "
                       f"({raw.strip()[:80]!r}), so its board is not read in part")
    return u


def successfactors_sitemap(origin: str) -> tuple[list[str], list[str]]:
    """(every posting of this career site its sitemap lists, in its order and
    each once; what was passed over, in words), or AtsError.

    A loc is a posting where, its XML escapes undone (_sf_loc), it is a /job/
    address on this origin that says plainly where it goes (_sf_posting_url);
    any other address - another host's, a page that is not a posting, a path
    that climbs - is passed over and counted. One that IS such a posting but
    reads otherwise once read so ('&amp;apos;' undone twice, a relative
    address) is an error: the address asked must be the address listed. A
    <sitemapindex> is followed one level, to at most SF_SITEMAPS_MAX sitemaps
    on this origin (another host's is not asked, and is counted); a sitemap
    index inside one is an error. NEVER A ZERO AND NEVER PART OF A BOARD: a
    sitemap robots.txt closes, one that is not there, one that lists no
    posting and one listing more than SF_SITEMAP_MAX addresses (this
    reader's cap on requests, not on roles) are errors, and say why."""
    first = origin + "/sitemap.xml"
    root, locs = _sf_sitemap_read(_sf_sitemap_page(first, origin), first)
    notes: list[str] = []
    if root == "sitemapindex":
        maps, away = [], 0
        for raw in locs:
            u = _sf_loc(raw, first)
            if _sf_child_sitemap(u, origin):
                if u != first and u not in maps:
                    maps.append(u)
            else:
                away += 1
        if len(maps) > SF_SITEMAPS_MAX:
            raise AtsError(f"{first} is a sitemap index naming {len(maps)} sitemaps on {origin}, more "
                           f"than the {SF_SITEMAPS_MAX} this reader follows: not read in part")
        if away:
            notes.append(f"{away} of the sitemaps its index names are not on its own origin (or do "
                         f"not say plainly where they go) and were not asked")
        locs = []
        for u in maps:
            kind, more = _sf_sitemap_read(_sf_sitemap_page(u, origin), u)
            if kind != "urlset":
                raise AtsError(f"{u}, which {first} names, is itself a sitemap index: one level is "
                               f"followed, so its board is not read in part")
            locs.extend((raw, u) for raw in more)
    else:
        locs = [(raw, first) for raw in locs]
    postings, seen, others = [], set(), 0
    for raw, where in locs:
        u = _sf_loc(raw, where)
        got = _sf_posting_url(u, origin)
        if got is None:
            others += 1
        elif got != u:
            raise AtsError(f"{where} lists {u[:120]!r}, a posting of {origin} that reads as "
                           f"{got[:120]!r} once read again: the address asked would not be the "
                           f"address listed, so its board is not read in part")
        elif got not in seen:
            seen.add(got)
            postings.append(got)
    if others:
        notes.append(f"{others} of the {len(locs)} addresses its sitemap lists are not postings of "
                     f"this site (a /job/ page on its host that says plainly where it goes) and "
                     f"were not asked")
    if not postings:
        raise AtsError(f"{origin}'s sitemap lists no posting of this site ({len(locs)} addresses), "
                       f"so it is not read as a board with nothing open")
    if len(postings) > SF_SITEMAP_MAX:
        raise AtsError(f"{origin}'s sitemap lists {len(postings)} posting addresses, more than the "
                       f"{SF_SITEMAP_MAX} this reader asks one by one at two requests each - a cap "
                       f"on its requests, not on roles: a site listing each role in two languages "
                       f"reaches it at {SF_SITEMAP_MAX // 2} - so it is not read")
    return postings, notes


def _sf_of_the_day(exc: BaseException) -> bool:
    """Did asking fail for the day only - a 429, a 5xx, the network failing or
    timing out, a robots.txt that would not answer - saying nothing of the
    posting or the board? A posting is asked once more (_sf_unify_posting);
    failing again, the read fails: refresh asks the board once more on a
    429, build_board on a 429 or the network, and a board that still will
    not read is Unknown for the night, never "None found"."""
    if isinstance(exc, RateLimited):
        return True
    if isinstance(exc, RobotsRefused):
        return getattr(exc, "kind", "disallowed") != "disallowed"
    text = str(exc)
    return bool(re.match(r"HTTP 5[0-9]{2}\b", text)) or text.startswith("network error") \
        or "timed out" in text.lower()


def _sf_inner(body: str, start: int, name: str, stop: int) -> str | None:
    """The markup from `start` (just past an opening <name> tag) to the close
    that matches it, before `stop`; None where it does not close there."""
    depth = 1
    for t in re.compile(rf"<(/?){name}\b[^<>]*>", re.I).finditer(body, start, stop):
        if t.group(1):
            depth -= 1
        elif not t.group(0).endswith("/>"):
            depth += 1
        if depth == 0:
            return body[start:t.start()]
    return None


def _sf_tokens(body: str) -> list[tuple[str, str]]:
    """(label, value) of every labelled token a posting page shows, in page
    order, from the page as a reader sees it (_cut): a <span
    class="joblayouttoken-label">Posting Location:&nbsp;</span>, and the
    value is the span after it, before the next label ('' where there is
    none). The label without its colon."""
    labels = [m for m in _SF_LABEL.finditer(body)
              if "joblayouttoken-label" in m.group(1).lower()
              and "joblayouttoken-label" in _sf_classes(m.group(1))]
    low, out = lower_same(body), []
    for k, m in enumerate(labels):
        stop = min(labels[k + 1].start() if k + 1 < len(labels) else len(body), m.end() + _SF_TEXT_MAX)
        shut = low.find("</span>", m.end(), stop)
        if shut < 0:
            continue
        label = _jv_text(body[m.end():shut]).rstrip(":").strip()
        value, v = "", _SF_LABEL.search(body, shut + 7, stop)
        if v and not v.group(0).endswith("/>"):
            inner = _sf_inner(body, v.end(), "span", stop)
            value = _jv_text(inner) if inner is not None else ""
        out.append((label, value))
    return out


def _sf_unify_title(body: str) -> str:
    """The role a posting page names: its itemprop="title" element's text,
    else its og:title, else ''."""
    m = _SF_TITLED.search(body)
    if m and not m.group(0).endswith("/>"):
        inner = _sf_inner(body, m.end(), m.group(1).lower(), min(len(body), m.end() + _SF_TEXT_MAX))
        if inner is not None and _jv_text(inner):
            return _jv_text(inner)
    for meta in _SF_META.finditer(body):
        attrs = _sf_attrs(meta.group(1))
        if (attrs.get("property") or attrs.get("name") or "").lower() == "og:title":
            return _text(html_lib.unescape(attrs.get("content") or ""))
    return ""


def successfactors_unify_row(page: str, url: str) -> dict | None:
    """One posting from its own page on a Unify site, or None for a page that
    names no role. `url` is the sitemap's address, and the row's.

    THE PLACE is the first place token shown with a value (_SF_PLACES), else
    the 'Country' token: several places in one ('Rio de Janeiro, Brazil |
    São Paulo, Brazil', Octave's) are one each, joined '; ' as the Hirebridge
    and Teamtailor readers join a job's places. A 'Workplace Type' is the
    row's mode, as Ashby's and Pinpoint's workplace types are, and Remote is
    said in the place too, first ('Remote; São Paulo, Brazil') - as the Gem
    and Zoho readers say it - so the place a remote role names is never read
    as its desk. THE DATE is the 'Posting Start Date' on a page whose own
    <html lang> is en-US only ('11/18/26', month first, Aurigo's): another
    English writes the day first (en-GB's 18/11/26), and another language is
    not read either - None, never a guess."""
    body = _cut(page or "")
    title = _sf_unify_title(body)
    if not title:
        return None
    place = country = mode = when = None
    for label, value in _sf_tokens(body):
        key = label.lower()
        if not value:
            continue
        if key in _SF_PLACES and place is None:
            place = value
        elif key == "country" and country is None:
            country = value
        elif key == "workplace type" and mode is None:
            mode = value
        elif key == "posting start date" and when is None:
            when = value
    where = "; ".join(dict.fromkeys(p.strip() for p in (place or country or "").split("|") if p.strip()))
    how = work_mode(mode)
    if how == "remote" and not re.search(r"\bremote\b", where, re.I):
        where = "; ".join(["Remote"] + ([where] if where else []))
    lang = _SF_HTML_LANG.search(body[:20000])
    posted = None
    day = _SF_MDY.fullmatch(_text(when)) if when else None
    if day and lang and lang.group(1).lower() == "en-us":
        try:
            posted = dt.date(2000 + int(day.group(3)), int(day.group(1)), int(day.group(2))).isoformat()
        except ValueError:
            posted = None
    return {"title": title, "location": where, "url": url, "posted": posted, "mode": how}


def _sf_requisition(landed: str) -> tuple[str, str] | None:
    """(the number, the locale) a posting's address names - /job/<slug>/<N>-
    <locale>/, where a Unify loc hops - or None."""
    path = urllib.parse.urlsplit(landed or "").path.rstrip("/")
    m = _SF_REQUISITION.fullmatch(path.rsplit("/", 1)[-1]) if path.startswith("/job/") else None
    return (m.group(1), m.group(2)) if m else None


_SF_LINK = re.compile(r"<link\b([^<>]*)>", re.I)


def _sf_stated_requisition(page: str, origin: str) -> tuple[str, str] | None:
    """(the number, the locale) a posting page states of itself: its
    <link rel="canonical" href="<origin>/job/<slug>/<N>-<locale>/"> (on every
    Unify posting page measured, 2026-11-23: Aurigo's, Esri Canada's and
    Octave's), on this site's origin; None for a page that states none.
    AtsError for one whose canonical is no address at all ('https://[...',
    which the page's own markup can say): the page does not say plainly
    which posting it is, so the posting is not read - never a crash of the
    whole read (blind review 11, 2026-11-23: one such page took a board's
    read down as a ValueError, and the company went Unknown)."""
    for m in _SF_LINK.finditer(_cut(page or "")):
        attrs = _sf_attrs(m.group(1))
        if "canonical" not in (attrs.get("rel") or "").lower().split():
            continue
        href = html_lib.unescape(attrs.get("href") or "").strip()
        try:
            parts = urllib.parse.urlsplit(href)
        except ValueError:                       # an href that is no address ('https://[')
            raise AtsError(f"its canonical link is no address ({href[:60]!r}), so the page does not "
                           f"say plainly which posting it is") from None
        if f"{parts.scheme}://{(parts.netloc or '').lower()}" == origin:
            return _sf_requisition(href)
        return None
    return None


def successfactors_unify_requisition(page: str, landed: str, origin: str) -> tuple[str, str] | None:
    """The requisition one Unify posting page is a copy of: the number and
    locale of the address its loc hopped to (/job/<slug>/<N>-<locale>/), else
    of the address the page states of itself (_sf_stated_requisition) - so a
    copy that answers at its own sitemap address is paired too (blind review
    10, 2026-11-23: an English copy answering in place and its French copy
    hopping were published as two roles). Where the two name different
    numbers, the page does not say plainly which requisition it is, and it
    is none: its copies are not paired on a guess. A page stating its own
    number in another locale (a translation's canonical naming the English
    copy) states the same requisition: the number is the requisition's, the
    locale the copy's, the address it came from. The reader pairs copies by
    this, and the probe asks it of the copies in each language."""
    hopped, stated = _sf_requisition(landed), _sf_stated_requisition(page, origin)
    if hopped and stated and hopped[0] != stated[0]:
        return None
    return hopped or stated


def _sf_code(locale: str) -> str:
    """'fr' of 'fr_CA' or 'fr-CA'."""
    return re.split(r"[-_]", locale or "")[0].lower()


def successfactors_unify_language(page: str, landed: str = "", origin: str = "") -> str:
    """The language a Unify copy is written in, as a bare code ('en', 'fr'):
    the locale of the address its loc hopped to (.../663-fr_CA/, the site's
    own word for the copy, measured on Esri Canada's), else its page's
    <html lang> (lang="en-US" on every posting page measured), else the
    locale its page states of itself (its canonical link, which a
    translation may point at another copy's), else its title or description
    element's own lang (successfactors_language), else ''. The reader keeps
    a requisition's 'en' copy, and the probe asks whether the copies it
    would publish are written in one language
    (verify_boards._successfactors_unify), by this one rule."""
    hopped = _sf_requisition(landed)
    if hopped:
        return _sf_code(hopped[1])
    m = _SF_HTML_LANG.search(_cut(page or "")[:20000])
    if m:
        return _sf_code(m.group(1))
    stated = _sf_stated_requisition(page, origin) if origin else None
    if stated:
        return _sf_code(stated[1])
    return successfactors_language(page)


class _SfKnown(AtsError):
    """A Unify posting's hop lands on an address this read has already been
    to, whatever it answered there (_sf_known): its answer is that one, and
    the address is not asked twice."""

    def __init__(self, address: str):
        super().__init__(f"{address} is read already")
        self.address = address


def _sf_known(known: dict, chain: list, what: str, got):
    """Keep what one posting answered for every address its read went to -
    its loc, each hop it was let take, where it landed - so that no address
    is asked twice in one read, whatever it answered: a posting, a closed
    one, a page showing none, one that did not read (blind review 11,
    2026-11-23: only an open posting was kept, so a closed role listed three
    ways was asked three times and counted three times closed). Each answer
    keeps the requisition it names, by which successfactors_unify knows an
    address leading there as that one posting, or as one of its own. Handed
    back."""
    for u in chain:
        known[u] = (what, got)
    return what, got


def _sf_unify_posting(url: str, origin: str, known: dict | None = None):
    """('row', (row, requisition, where, language)), ('closed', (requisition,
    language)), ('blank', (why, requisition, language)) for a page that shows
    no posting and is not the site saying it closed, or ('unread', (why,
    requisition)), for one posting a Unify sitemap lists, asked once; the
    requisition is the one the copy is of (None where it is not known; for
    one that did not read, the one the address it came to names), the
    language the copy's own (successfactors_unify_language). A FAILURE OF
    THE DAY - a 429, a 5xx, the network failing or timing out
    (_sf_of_the_day) - is asked once more, after the gate's backoff for a
    429 and SF_RETRY_PAUSE for the rest, and failing again is raised as it
    came (blind review 10, 2026-11-23: one 5xx among a read's ~265 requests
    took the whole board off for the night, and nothing asked it again).
    `known` holds what this board's read has already had at each address it
    went to (_sf_known): a hop to one of them is that answer again, not
    asked twice, and is handed back as ('again', (what, got)) - one posting
    with the one before it where that answer names a requisition, a posting
    of its own where it names none (successfactors_unify)."""
    known = {} if known is None else known
    for attempt in (1, 2):
        landed: list = []
        try:
            page = successfactors_posting(url, origin, landed, known)
            break
        except _SfKnown as again:
            # (and each address on the way there leads to that answer too)
            return "again", _sf_known(known, landed, *known[again.address])
        except AtsError as exc:
            # (the address it came to names its requisition: a 404 there, a
            # refusal there)
            hop = landed[-1] if landed else ""
            if successfactors_closed(exc):
                return _sf_known(known, landed, "closed",
                                 (_sf_requisition(hop), successfactors_unify_language("", hop, origin)))
            if not _sf_of_the_day(exc):
                return _sf_known(known, landed, "unread", (f"{url} ({_text(str(exc))[:160]})",
                                                           _sf_requisition(hop)))
            if attempt == 2:
                raise
            if not isinstance(exc, RateLimited):
                time.sleep(SF_RETRY_PAUSE)    # (a 429's wait is the gate's: _back_off)
    where = landed[-1] if landed else url
    try:
        req = successfactors_unify_requisition(page, where, origin)
        lang = successfactors_unify_language(page, where, origin)
    except AtsError as exc:
        # (a page stating its own address as no address at all: which
        # posting it is is not said plainly, so it is not read)
        return _sf_known(known, landed, "unread", (f"{url} ({_text(str(exc))[:160]})",
                                                   _sf_requisition(where)))
    if not successfactors_shown(page):
        if successfactors_filled(page):
            return _sf_known(known, landed, "closed", (req, lang))
        return _sf_known(known, landed, "blank", (f"{url} (a page that shows no posting and is not the "
                                                  f"site saying it has closed)", req, lang))
    row = successfactors_unify_row(page, url)
    if row is None:
        return _sf_known(known, landed, "unread", (f"{url} (a posting page that names no role)", req))
    if FETCH_DETAILS:
        # the ad is on the page already read: no request more
        row["jd"] = successfactors_ad(page)
    return _sf_known(known, landed, "row", (row, req, where, lang))


def _unify_night_read(origin: str) -> list[dict] | None:
    """This run's read of a Unify board, as an earlier step of it wrote it
    (UNIFY_NIGHT), or None: no file named, no entry for the board, one older
    than UNIFY_NIGHT_MAX_AGE (or dated ahead), one made without the ads this
    caller wants, or a file that does not read. Copies, the ads dropped for a
    caller that does not want them; said on stderr."""
    if not UNIFY_NIGHT:
        return None
    try:
        entry = json.loads(pathlib.Path(UNIFY_NIGHT).read_text()).get(origin)
        at = dt.datetime.fromisoformat(entry["read_at"])
        rows = [dict(r) for r in entry["rows"]]
        details = entry.get("details") is True
        age = _utcnow() - at
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if not dt.timedelta(0) <= age <= UNIFY_NIGHT_MAX_AGE or (FETCH_DETAILS and not details) or not rows:
        return None
    if not FETCH_DETAILS:
        for r in rows:
            r.pop("jd", None)
    print(f"  successfactors: {origin}: its {len(rows)} postings are this run's read of "
          f"{entry['read_at'][:16]}Z, asked nothing again", file=sys.stderr)
    return rows


def _unify_night_write(origin: str, rows: list[dict]) -> None:
    """Keep a Unify read that came whole for this run's later steps
    (UNIFY_NIGHT); a file that cannot be written keeps nothing, and the next
    step asks for itself."""
    if not UNIFY_NIGHT:
        return
    path = pathlib.Path(UNIFY_NIGHT)
    with _UNIFY_NIGHT_LOCK:
        try:
            held = json.loads(path.read_text())
            held = held if isinstance(held, dict) else {}
        except (OSError, ValueError):
            held = {}
        held[origin] = {"read_at": _utcnow().isoformat(), "details": bool(FETCH_DETAILS), "rows": rows}
        try:
            fresh = path.with_name(path.name + ".tmp")
            fresh.write_text(json.dumps(held))
            os.replace(fresh, path)
        except OSError:
            pass


def _sf_slug(url: str) -> str:
    """The slug of a /job/<slug>/... address, or ''."""
    segs = urllib.parse.urlsplit(url or "").path.split("/")
    return segs[2] if len(segs) > 2 and segs[1] == "job" else ""


def successfactors_unify(origin: str) -> list[dict]:
    """A Unify career site's board: every posting its sitemap lists
    (successfactors_sitemap), each asked once on its own page and read from
    it (successfactors_unify_row), the site's closed postings left out and
    said, a failure of the day raised as it came, and more than SF_SLACK
    postings that would not read an error; then one row a requisition
    (successfactors_unify_requisition) - a role written in two languages is
    one number twice - in English where there is one, at the address whose
    slug is the one its page answers at where copies in one language
    differ (a stale slug that hops to the posting is not the row's). An
    address the read has already been to is not asked again, whatever it
    answered, and where what it answered names a requisition it is one
    posting, counted once; each address leading to one that names none is a
    posting of its own. Never a zero."""
    urls, notes = successfactors_sitemap(origin)
    closed, unread, read, known, shut, blank = [], [], [], {}, set(), []
    twice, twice_out = 0, 0
    for url in urls:
        if url in known:
            # (the sitemap lists an address another of its postings went to:
            # that answer, had once)
            what, got = "again", known[url]
        else:
            what, got = _sf_unify_posting(url, origin, known)
        if what == "again":
            # AN ADDRESS LANDING WHERE ANOTHER HAD IS THAT ONE POSTING where
            # what it answered there names a requisition - the <N>-<locale>
            # it came to, or the one its page states (blind review 11,
            # 2026-11-23: a closed role listed three ways was asked three
            # times and counted three closed, and 3 of 10 refused the board):
            # a posting read is a row of its requisition, grouped below; one
            # that answered as closed or did not read is counted once, and
            # said. AN ADDRESS NAMING NONE - /job/, /job/expired/ - says
            # nothing of which posting leads there, so each address leading
            # to it is its own posting, that answer had once, not asked again
            # (review 11's repair: 130 of 131 sent to one /job/ answering 404
            # were one closed posting, and one row was published as the board)
            what, got = got
            if got[0] if what == "closed" else got[1]:
                twice += 1
                if what != "row":
                    twice_out += 1
                    continue
            elif what in ("blank", "unread"):
                # (said of this address, as if asked)
                got = (f"{url} (it leads where another address its sitemap lists did: "
                       f"{got[0]})",) + got[1:]
            if what == "row":
                row, req, where, lang = got
                got = (dict(row, url=url), req, where, lang)
        if what == "row":
            read.append(got)
        elif what == "closed":
            closed.append(url)
            if got[0]:
                shut.add(got[0][0])
        elif what == "blank" and got[1] and got[2] != "en":
            # A COPY IN ANOTHER LANGUAGE THAT SHOWS NO POSTING, OF A
            # REQUISITION ANOTHER COPY OF WHICH THE SITE SAYS HAS CLOSED, is
            # that copy closed too: both copies of a closed role stay in the
            # sitemap until it is written again, and the site's words are
            # read in English only (successfactors_filled), so the French one
            # was said "did not read as a posting", and three such made a
            # bilingual board unreadable (blind review 10, 2026-11-23).
            # Weighed once every copy is read, as its other copy may come
            # later; an English copy says it in words read, so one that does
            # not is the page it is, at once.
            blank.append((url, got[0], got[1][0]))
        else:
            unread.append(got[0])
        if len(unread) > SF_SLACK:
            # (no total of postings is known yet - an address still to come
            # may land on a requisition another had, and be that posting
            # again - so the sitemap is counted in addresses, as the refusal
            # after the loop says them beside its postings: one board's shape
            # was '3 of the 5 postings' here and '3 of the 4 postings (5
            # addresses ...)' there, review 11's verifier, 2026-11-23)
            raise AtsError(f"{origin}: {len(unread)} of the postings its sitemap lists ({len(urls)} "
                           f"addresses) did not read as postings ({unread[0]} the first): read in "
                           f"part, so not read")
    # ONE POSTING COUNTS ONCE, closed or open: what is counted below is of the
    # postings the sitemap lists once each, an address landing on a
    # requisition another had being that posting again (blind review 11,
    # 2026-11-23), and one landing where another had on none a posting of
    # its own
    once = len(urls) - twice
    listed = (f"the {len(urls)} postings its sitemap lists" if not twice else
              f"the {once} postings its sitemap lists ({len(urls)} addresses, {twice} of them landing "
              f"on a requisition another had)")
    with_it = [url for url, _why, n in blank if n in shut]
    closed += with_it
    unread += [why for url, why, n in blank if n not in shut]
    if len(unread) > SF_SLACK:
        raise AtsError(f"{origin}: {len(unread)} of {listed} did not read as postings ({unread[0]} the "
                       f"first): read in part, so not read")
    groups: dict = {}
    for row, req, where, lang in read:
        key = ("requisition", req[0]) if req else ("address", row["url"])
        groups.setdefault(key, []).append((row, req, where, lang))

    def rank(copy):
        row, _req, where, lang = copy
        return (lang != "en", _sf_slug(row["url"]) != _sf_slug(where))
    kept, languages, again = [], 0, 0
    for copies in groups.values():
        best = min(copies, key=rank)            # (the first of equals)
        kept.append(best[0])
        for copy in copies:
            if copy is not best:
                if copy[3] == best[3]:
                    again += 1
                else:
                    languages += 1
    if not kept:
        raise AtsError(f"{origin}: none of {listed} answered as an open posting ({len(closed)} closed, "
                       f"{len(unread)} that did not read), so it is not read as a board with nothing "
                       f"open")
    if len(closed) > max(SF_SLACK, SF_CLOSED_SHARE * once):
        raise AtsError(f"{origin}: {len(closed)} of {listed} answered as closed, more than a quarter of "
                       f"it - not a day's postings closing, but a site answering so for postings it "
                       f"has or a sitemap long out of date - so the {len(kept)} left are not read as "
                       f"its board")
    said = list(notes)
    if closed:
        twins_shut = (f"; {len(with_it)} of them a copy that shows no posting, of a requisition "
                      f"another copy of which the site says has closed" if with_it else "")
        said.append(f"{len(closed)} of {listed} answered as closed on their own page (a 404 or 410, a "
                    f"hop to its list, or the site's page saying so{twins_shut}) and are not published")
    if twice_out:
        said.append(f"{twice_out} of the postings its sitemap lists land on a requisition another of "
                    f"them had, which answered as closed or did not read as a posting there, and are "
                    f"counted once, not asked again")
    if unread:
        said.append(f"{len(unread)} of {listed} did not read as postings and are left out: "
                    f"{'; '.join(unread)}")
    if languages:
        said.append(f"{languages} of the postings its sitemap lists are another language's copy of a "
                    f"requisition read (one number) and are published once, in English where there "
                    f"is one")
    if again:
        said.append(f"{again} of the postings its sitemap lists are a requisition read listed again in "
                    f"the same language (one number, one locale) and are published once")
    for line in said:
        print(f"  successfactors: {origin}: {line}", file=sys.stderr)
    # (last, after every refusal above: a read refused for what it found -
    # its closed share, nothing open - keeps nothing for the night, as one
    # that raised keeps nothing; blind review 11 pinned it)
    _unify_night_write(origin, kept)
    return kept


FETCHERS = {
    "ashby": fetch_ashby,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
    "breezy": fetch_breezy,
    "smartrecruiters": fetch_smartrecruiters,
    "bamboohr": fetch_bamboohr,
    "workday": fetch_workday,
    "rippling": fetch_rippling,
    "jazzhr": fetch_jazzhr,
    "icims": fetch_icims,
    "jibe": fetch_jibe,
    "paylocity": fetch_paylocity,
    "oracle": fetch_oracle,
    "html": fetch_html,
    "adp": fetch_adp,
    "gusto": fetch_gusto,
    "gem": fetch_gem,
    "hirebridge": fetch_hirebridge,
    "applicantpro": fetch_applicantpro,
    "teamtailor": fetch_teamtailor,
    "pinpoint": fetch_pinpoint,
    "zohorecruit": fetch_zohorecruit,
    "comeet": fetch_comeet,
    "jobvite": fetch_jobvite,
    "jobvitefeed": fetch_jobvitefeed,
    "trinethire": fetch_trinethire,
    "jobscore": fetch_jobscore,
    "clearcompany": fetch_clearcompany,
    "phenom": fetch_phenom,
    "successfactors": fetch_successfactors,
}


def fetch(ats: dict) -> list[dict]:
    """Dispatch on data/companies.json ats block: {"type": ..., "ref": ...}."""
    kind, ref = ats.get("type"), ats.get("ref")
    if kind == "unknown" or ref is None:
        raise AtsError("no ATS on file - needs discovery")
    fn = FETCHERS.get(kind)
    if fn is None:
        raise AtsError(f"unsupported ats type: {kind}")
    if kind == "workday" and WORKDAY_SNAPSHOT:
        # Rows the Mac stored came out of plain_rows already; they go back as
        # they were stored rather than through it twice.
        rows, why_not = _workday_from_snapshot(ref)
        if rows is not None:
            return rows
        try:
            return plain_rows(fn(ref))
        except RateLimited as exc:
            exc.args = (f"{exc.args[0] if exc.args else exc}; {why_not}",)
            raise
        except AtsError as exc:
            raise AtsError(f"{exc}; {why_not}") from exc
    # One normalisation point for sixteen fetchers. Doing it per fetcher means
    # the next one added quietly skips it, and the escape reaches the id - or,
    # now, the row comes back with no jd/comp keys at all and a consumer that
    # reads them with [] blows up on a board nobody has looked at in months.
    return plain_rows(fn(ref))
