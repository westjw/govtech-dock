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

import requests

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


class AtsError(Exception):
    pass


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


class _Cached:
    """Enough of a requests.Response for the five things anybody reads.

    `.url` is one of them. find_websites.probe reads it off every _get result
    to record which candidate domain identified the company, and a cached 304
    that lacked it crashed the whole run on the first company whose homepage
    had not changed since the last probe.
    """

    def __init__(self, text: str, url: str = ""):
        self.text = text
        self.url = url
        self.status_code = 200
        self.from_cache = True
        self.headers: dict = {}

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


def _cache_write(url: str, resp) -> None:
    etag = resp.headers.get("ETag")
    lastmod = resp.headers.get("Last-Modified")
    if not etag and not lastmod:
        return                                 # nothing to revalidate with
    try:
        HTTP_CACHE.mkdir(parents=True, exist_ok=True)
        meta_p, body_p = _cache_paths(url)
        body_p.write_bytes(gzip.compress(resp.text.encode("utf-8"), 6))
        meta_p.write_text(json.dumps(
            {"url": url, "etag": etag, "last_modified": lastmod}))
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


def _host_gate(url: str) -> None:
    host = urllib.parse.urlsplit(url).netloc.lower()
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
    if wait:
        host = urllib.parse.urlsplit(url).netloc.lower()
        with _HOST_TABLE:
            _HOST_LOCKS.setdefault(host, threading.Lock())
            _HOST_NOT_BEFORE[host] = max(_HOST_NOT_BEFORE.get(host, 0.0),
                                         time.monotonic() + min(wait, 300))
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


def _get(url: str, **kw):
    _host_gate(url)
    headers = dict(UA)
    meta = _cache_read(url) if HTTP_CACHE else None
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
            return _Cached(body, url)
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
    if resp.status_code != 200:
        raise AtsError(f"HTTP {resp.status_code} for {url}")
    _fix_encoding(resp)
    if HTTP_CACHE:
        _cache_write(url, resp)
    return resp


def _fix_encoding(resp) -> None:
    """A text/* body with no charset in its header is UTF-8 if it decodes as
    UTF-8. requests follows the 1999 RFC and calls it Latin-1, which turns
    every curly apostrophe on such a page into three characters glued to
    the word before it. Eleven of the first 157 company sites shipped that
    way, and the write-up door refused true customer names on them. Fixed
    here so the cache and everything downstream see the page as served."""
    ctype = (resp.headers or {}).get("content-type", "") if hasattr(resp, "headers") else ""
    if "charset" in ctype.lower():
        return
    try:
        resp.content.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return
    resp.encoding = "utf-8"


def _post_json(url: str, body: dict) -> dict:
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
        raw = _get(url).text
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
    return [{"title": j.get("title", ""), "location": j.get("city", "") or "",
             "url": j.get("url", ""),
             "posted": posted_date(j.get("published_on")),
             # telecommuting True means remote. FALSE MEANS NOT REMOTE, which
             # is onsite or hybrid and this board did not say which, so it
             # asserts nothing.
             "mode": "remote" if j.get("telecommuting") is True else None,
             "office_hint": office_hint(j.get("city"), j.get("state"),
                                        j.get("country")),
             "jd": plain_html(j.get("description") or "")}
            for j in data.get("jobs", [])]


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


def fetch_paylocity(ref: str) -> list[dict]:
    """Paylocity publishes no JSON API; the board ships its data in the page.

    The list lives in a window.pageData assignment, so the titles are exact
    rather than scraped out of markup.
    """
    resp = _get(ref)
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
    resp = _get(url)
    text = _TAG.sub(" ", resp.text)
    text = _ANYTAG.sub(" ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text)


_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_rippling(slug: str) -> list[dict]:
    # Rippling boards are server-rendered; job titles appear in page text and in
    # embedded JSON. Try embedded JSON first, fall back to text scan.
    url = f"https://ats.rippling.com/{slug}/jobs"
    resp = _get(url)
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
    m = _NEXT_DATA.search(_get(url).text)
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
    resp = _get(url)

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
            _get(f"{base}{joiner}in_iframe=1&pr={pr}").text),
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
    idx = next((i for i, line in enumerate(lines) if _TITLEISH.search(line)), None)
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
    resp = _get(url)
    seen, out = set(), []
    for href, inner in _ANCHOR.findall(resp.text):
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
        if not (6 <= len(text) <= 90) or _NAV.match(text):
            continue
        if _JOB_COUNT.search(text):
            continue          # "Engineer jobs 555,845 open jobs" is a rail, not a role
        if not (_JOB_HREF.search(href) and _TITLEISH.search(text)):
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
    "paylocity": fetch_paylocity,
    "oracle": fetch_oracle,
    "html": fetch_html,
    "adp": fetch_adp,
}


def fetch(ats: dict) -> list[dict]:
    """Dispatch on data/companies.json ats block: {"type": ..., "ref": ...}."""
    kind, ref = ats.get("type"), ats.get("ref")
    if kind == "unknown" or ref is None:
        raise AtsError("no ATS on file - needs discovery")
    fn = FETCHERS.get(kind)
    if fn is None:
        raise AtsError(f"unsupported ats type: {kind}")
    # One normalisation point for sixteen fetchers. Doing it per fetcher means
    # the next one added quietly skips it, and the escape reaches the id - or,
    # now, the row comes back with no jd/comp keys at all and a consumer that
    # reads them with [] blows up on a board nobody has looked at in months.
    return plain_rows(fn(ref))
