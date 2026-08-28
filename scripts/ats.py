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
import os
import re
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


def _get(url: str, **kw) -> requests.Response:
    try:
        resp = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise AtsError(f"network error: {exc}") from exc
    if resp.status_code != 200:
        raise AtsError(f"HTTP {resp.status_code} for {url}")
    return resp


def _post_json(url: str, body: dict) -> dict:
    try:
        resp = requests.post(url, json=body, headers={**UA, "Content-Type": "application/json"},
                             timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise AtsError(f"network error: {exc}") from exc
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
             "comp": _ashby_comp(j)} for j in jobs]


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
        loc = j.get("location") or {}
        parts = [loc.get(k) for k in ("city", "state", "country")]
        out.append({"title": j.get("jobOpeningName", ""),
                    "location": ", ".join(p for p in parts if p),
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
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    data = _json(_get(url))
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append({"title": j.get("name", ""),
                    "location": ", ".join(x for x in [loc.get("city"), loc.get("region")] if x),
                    "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}",
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
    # Workday search is paged; two targeted queries beat crawling everything.
    for term in ("account executive", "sales"):
        data = _post_json(api, {"limit": 20, "offset": 0, "searchText": term})
        for j in data.get("jobPostings", []):
            key = j.get("externalPath", j.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
            path = j.get("externalPath", "") or ""
            out.append({"title": j.get("title", ""),
                        "location": j.get("locationsText", "") or "",
                        "url": f"{base}/en-US/{site}{path}",
                        "_detail_url": _workday_detail_url(api, path)})
    return _workday_details(out)


def _workday_jobs(api: str, base: str, site: str) -> list[dict]:
    out, seen = [], set()
    for term in ("account executive", "sales"):
        data = _post_json(api, {"appliedFacets": {}, "limit": 20, "offset": 0,
                                "searchText": term})
        for j in data.get("jobPostings", []):
            key = j.get("externalPath", j.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
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
    links = re.findall(r'<a[^>]+href="(https?://[^"]*applytojob\.com/apply/[^"]+)"[^>]*>([^<]{4,90})</a>',
                       resp.text)
    out = [{"title": html_lib.unescape(t).strip(), "location": "", "url": u}
           for u, t in links]
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
    out, seen = [], set()
    for page in range(3):                     # portals page at 50; 150 is plenty
        url = f"{base}{joiner}in_iframe=1&pr={page}"
        html = _get(url).text
        found = re.findall(
            r'<a\s+href="([^"]+)"[^>]*class="iCIMS_Anchor"[^>]*title="([^"]+)"', html)
        new = 0
        for href, raw in found:
            title = re.sub(r"^\d+\s*-\s*", "", html_lib.unescape(raw)).strip()
            if title and title not in seen:
                seen.add(title)
                new += 1
                out.append({"title": title, "location": "",
                            "url": html_lib.unescape(href)})
        if not new:
            break
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
        text = html_lib.unescape(text)
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
        out.append({"title": text, "location": "",
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
