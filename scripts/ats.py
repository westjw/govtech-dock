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

  one extra request per posting (behind FETCH_DETAILS, off by default)
    bamboohr     /careers/<id>/detail  -> description + a compensation field
    smartrecruiters  /postings/<id>    -> jobAd.sections
    workday      /wday/cxs/<t>/<site><externalPath> -> jobPostingInfo.jobDescription
    breezy       the posting page      -> schema.org JobPosting
    jazzhr       the posting page      -> schema.org JobPosting
    icims        the posting page      -> schema.org JobPosting
    rippling     the posting page      -> __NEXT_DATA__ apiData.jobPost

  nothing published
    oracle       no company on file uses it; unverified either way
"""
from __future__ import annotations

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
import urllib.parse
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
            r["comp"] = _text_comp(r["jd"])
    return rows


def _text_comp(jd: str) -> dict | None:
    """Pay parsed out of the description, when the API stated none itself.

    Wrapped because a parser bug must not cost us a posting: the description is
    a nice-to-have and the job is the product. Falling back to None also keeps
    the honest reading - no salary found means not stated, never zero.
    """
    if salary is None:
        return None
    try:
        got = salary.parse(jd)
    except Exception:                     # noqa: BLE001 - see docstring
        return None
    if got:
        got.setdefault("source", "text")
    return got or None


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
# its hosts, that is still not a reason to keep a copy of it.
NEVER_CACHED = frozenset({"hirebridge.com"})


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
_SHARED_GATES = ((re.compile(r"(^|\.)(myworkdayjobs|myworkdaysite)\.com$"), "workday"),)
# A 429 that names no Retry-After still backs its gate off this long: a
# server that says only "too many" has said to wait, and asking again at once
# is the burst that earned the 429.
RATE_LIMIT_PAUSE = float(os.environ.get("GOVTECH_DOCK_429_PAUSE", "15"))


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
    if not host or HOST_PAUSE <= 0:
        return
    with _HOST_TABLE:                       # only guards the dicts, never a sleep
        lock = _HOST_LOCKS.setdefault(host, threading.Lock())
    with lock:
        now = time.monotonic()
        last = _HOST_LAST.get(host)
        wait = HOST_PAUSE - (now - last) if last is not None else 0.0
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
        url = urllib.parse.urljoin(url, loc)
    raise AtsError(f"more than {hops} redirects from {url}")


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


def _page(url: str, *, same_host: bool = False, **kw):
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
        if there != home:
            raise AtsError(f"{url} leads off to {there}, which is not the board")
    return follow(url, lambda u: _get(u, allow_redirects=False, **kw),
                  may=stay if same_host else None)


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


def fetch_workday(ref: list) -> list[dict]:
    """Workday, on either of its two board hosts.

    myworkdayjobs.com is tenant-first: acme.wd12.myworkdayjobs.com/Careers.
    myworkdaysite.com is pod-first and carries the tenant in the path:
    wd3.myworkdaysite.com/recruiting/modaxo/Tadera. The pod ("wd3", "wd12",
    "wd502") is never guessable - it has to come out of the URL that was found.
    """
    tenant, host, site = ref
    if str(host).startswith("wd") and str(tenant).startswith("wd"):
        # ("wd3", "modaxo", "Tadera") - pod, tenant, site
        pod, tenant, site = tenant, host, site
        base = f"https://{pod}.myworkdaysite.com/recruiting/{tenant}/{site}"
        api = f"https://{pod}.myworkdaysite.com/wday/cxs/{tenant}/{site}/jobs"
        return _workday_jobs(api, base, site)
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
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
                        "url": f"{base}/en-US/{site}{path}",
                        "_detail_url": _workday_detail_url(api, path)})
    return _workday_details(out)


def _workday_jobs(api: str, base: str, site: str) -> list[dict]:
    out, seen = [], set()
    for term in ("account executive", "sales"):
        rows = _paged(
            lambda off, t=term: _post_json(
                api, {"appliedFacets": {}, "limit": WD_PAGE, "offset": off,
                      "searchText": t}
            ).get("jobPostings", []),
            lambda j: j.get("externalPath", j.get("title", "")),
            page=WD_PAGE, max_pages=WD_MAX_PAGES, label=f"{site}/{term}")
        for j in rows:
            k = j.get("externalPath", j.get("title", ""))
            if k in seen:
                continue
            seen.add(k)
            path = j.get("externalPath", "") or ""
            out.append({"title": j.get("title", ""),
                        "location": j.get("locationsText", "") or "",
                        "url": base + path,
                        "_detail_url": _workday_detail_url(api, path)})
    return _workday_details(out)


def _workday_detail_url(api: str, path: str) -> str:
    """The jobs endpoint minus "/jobs", plus the posting's externalPath.

    Both Workday hosts answer the ad on the same /wday/cxs/<tenant>/<site>
    prefix they answer the search on, which is why this is derived from `api`
    rather than rebuilt from the pod-and-tenant guessing above.
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

    One request, in the shape and at the address fetch_workday already uses.
    Raises AtsError when the board will not answer or states no total: "the
    size is unknown" is the caller's answer to give, not a zero made up here.
    """
    tenant, host, site = ref
    body = {"limit": WD_PAGE, "offset": 0, "searchText": ""}
    if str(host).startswith("wd") and str(tenant).startswith("wd"):
        pod, tenant = tenant, host
        api = f"https://{pod}.myworkdaysite.com/wday/cxs/{tenant}/{site}/jobs"
        body["appliedFacets"] = {}
    else:
        api = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    data = _post_json(api, body)
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
# cell. The ref is the slug. An embedded board's list page sends a browser on
# to the employer's own careers page (OverDrive's, 2026-11-19); asked with
# nl=1&fr=false it answers itself. A dead slug is sent to search.jobvite.com
# (?invalid=1), a host that is not the board, so it is an error and never a
# zero - and a list with no rows is an error too, until a real empty board
# has been read. The XML feed beside it (app.jobvite.com) is not read: it
# names each job's hiring manager, and its host answers 429 to a second ask.
# Measured 2026-11-19 and 11-21: Gigamon 22, Absolute 18, Uplight 5,
# OverDrive 5.

_JOBVITE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")
# one row per opening; a layout may put cells between the name and the place
# (OverDrive's has the job's type there)
_JOBVITE_TR = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S)
_JOBVITE_NAME = re.compile(
    r'<td class="jv-job-list-name">\s*<a href="(/[A-Za-z0-9_-]+/job/[A-Za-z0-9]{5,16})"[^>]*>'
    r'(.*?)</a>', re.S)
_JOBVITE_WHERE = re.compile(r'<td class="jv-job-list-location">(.*?)</td>', re.S)


def jobvite_page(ref) -> tuple[str, str]:
    """(the list's address, the list page), or AtsError."""
    slug = _text(ref)
    if not _JOBVITE_SLUG.fullmatch(slug):
        raise AtsError(f"not a jobvite career site: {ref!r}")
    base = f"https://jobs.jobvite.com/{slug}/jobs"
    try:
        return base, _page(base, same_host=True).text or ""
    except AtsError as exc:
        if "leads off" not in str(exc):
            raise
    # an embedded board's list, asked for itself; a dead slug leaves the host
    # this way too, and is an error
    return base, _page(base + "?nl=1&fr=false", same_host=True).text or ""


def _jv_text(raw: str) -> str:
    return _text(html_lib.unescape(re.sub(r"<[^>]+>", " ", raw or "")))


def jobvite_rows(page: str) -> list[tuple[str, str, str]]:
    """(url, title, location) per opening on a list page, or AtsError."""
    rows = []
    for tr in _JOBVITE_TR.findall(page or ""):
        name, where = _JOBVITE_NAME.search(tr), _JOBVITE_WHERE.search(tr)
        if name and _jv_text(name.group(2)):
            rows.append(("https://jobs.jobvite.com" + name.group(1), _jv_text(name.group(2)),
                         _jv_text(where.group(1)) if where else ""))
    if not rows:
        raise AtsError("jobvite list page carried no openings (an empty board has not "
                       "been seen, so this is not read as a zero)")
    return rows


def fetch_jobvite(ref) -> list[dict]:
    _base, page = jobvite_page(ref)
    out = [{"title": title,
            # 'Dallas, Texas', or '2 Locations' where a job is open in several
            "location": re.sub(r"\s*,\s*", ", ", where),
            "url": url} for url, title, where in jobvite_rows(page)]
    if FETCH_DETAILS:
        # the ad and any stated pay are each posting's schema.org block
        _details(out, lambda r: _schema_posting(r["url"]))
    return out

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
