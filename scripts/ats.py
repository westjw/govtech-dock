"""ATS fetchers.

Each fetcher returns a list of {"title": str, "location": str, "url": str} for a
company's open roles, or raises AtsError when the board can't be read (network
error, 404 slug, JS-walled page). Callers treat AtsError as status "Unknown".

Supported types (data/companies.json -> ats.type):
  ashby, greenhouse, lever, workable, recruitee, breezy, smartrecruiters,
  bamboohr, workday, rippling, jazzhr, icims
                               -> structured JSON APIs / server-rendered boards
  html                         -> fetch page, strip tags, scan visible text (weak signal)
  unknown                      -> not fetchable; needs ATS discovery (ask Claude Code)
"""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse

import requests

# Conventional bot identification: Mozilla/5.0 prefix, a name, and a contact URL.
# This is the standard well-behaved-crawler format, not a browser impersonation.
# Many WAFs reject anything without the Mozilla prefix outright, which is what was
# turning Pavilion into a 405. Where a site blocks this too (Daxko returns 429),
# that is a deliberate refusal and it goes to the manual worklist rather than
# getting a spoofed Chrome string.
UA = {"User-Agent": "Mozilla/5.0 (compatible; govtech-dock/1.0; "
                    "+https://github.com/westjw/govtech-dock)"}
TIMEOUT = 20


class AtsError(Exception):
    pass


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


# ---------------------------------------------------------------- structured APIs

def fetch_ashby(slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}"
    data = _json(_get(url))
    jobs = data.get("jobs", [])
    return [{"title": j.get("title", ""), "location": j.get("location", "") or "",
             "url": j.get("jobUrl", "") or j.get("applyUrl", "")} for j in jobs]


def fetch_greenhouse(slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    data = _json(_get(url))
    return [{"title": j.get("title", ""),
             "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", "")} for j in data.get("jobs", [])]


def fetch_lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _json(_get(url))
    if not isinstance(data, list):
        raise AtsError("unexpected lever payload")
    return [{"title": j.get("text", ""),
             "location": (j.get("categories") or {}).get("location", "") or "",
             "url": j.get("hostedUrl", "")} for j in data]


def fetch_workable(slug: str) -> list[dict]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=false"
    data = _json(_get(url))
    return [{"title": j.get("title", ""), "location": j.get("city", "") or "",
             "url": j.get("url", "")} for j in data.get("jobs", [])]


def fetch_recruitee(slug: str) -> list[dict]:
    url = f"https://{slug}.recruitee.com/api/offers/"
    data = _json(_get(url))
    return [{"title": j.get("title", ""), "location": j.get("location", "") or "",
             "url": j.get("careers_url", "")} for j in data.get("offers", [])]


def fetch_breezy(slug: str) -> list[dict]:
    url = f"https://{slug}.breezy.hr/json"
    data = _json(_get(url))
    if not isinstance(data, list):
        raise AtsError("unexpected breezy payload")
    return [{"title": j.get("name", ""),
             "location": (j.get("location") or {}).get("name", "") or "",
             "url": j.get("url", "")} for j in data]


def fetch_bamboohr(slug: str) -> list[dict]:
    """https://<slug>.bamboohr.com/careers/list -> {"result": [...]}.
    Location arrives as parts (city/state/country), not one string."""
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
                    "url": f"https://{slug}.bamboohr.com/careers/{j.get('id', '')}"})
    return out


def fetch_smartrecruiters(slug: str) -> list[dict]:
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    data = _json(_get(url))
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append({"title": j.get("name", ""),
                    "location": ", ".join(x for x in [loc.get("city"), loc.get("region")] if x),
                    "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}"})
    return out


def fetch_workday(ref: list) -> list[dict]:
    tenant, host, site = ref
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
                        "url": f"{base}/en-US/{site}{path}"})
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


def fetch_rippling(slug: str) -> list[dict]:
    # Rippling boards are server-rendered; job titles appear in page text and in
    # embedded JSON. Try embedded JSON first, fall back to text scan.
    url = f"https://ats.rippling.com/{slug}/jobs"
    resp = _get(url)
    titles = re.findall(r'"name"\s*:\s*"([^"]{4,90})"\s*,\s*"[^"]*url', resp.text)
    if titles:
        return [{"title": t, "location": "", "url": url} for t in titles]
    return [{"title": "", "location": "", "url": url, "_pagetext": _strip(resp.text)}]


def fetch_jazzhr(slug: str) -> list[dict]:
    # JazzHR hosted boards are server-rendered lists of <a> job links.
    url = f"https://{slug}.applytojob.com/apply/"
    resp = _get(url)
    links = re.findall(r'<a[^>]+href="(https?://[^"]*applytojob\.com/apply/[^"]+)"[^>]*>([^<]{4,90})</a>',
                       resp.text)
    return [{"title": html_lib.unescape(t).strip(), "location": "", "url": u} for u, t in links]


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
_NAV = re.compile(r"^(apply|apply now|learn more|read more|view (all|jobs|openings)|"
                  r"see (all|more)|careers?|jobs?|open (roles|positions)|back|next|"
                  r"previous|home|about|contact|search|filter|all departments?|"
                  r"privacy|terms|cookie)s?$", re.I)


def fetch_html_titles(url: str) -> list[dict]:
    """Enumerate job titles from a server-rendered careers page.

    Deliberately conservative: a link counts only if its href looks like a job
    URL and its text reads like a job title. Requires at least two hits, because
    one match is far more likely to be a stray link than a real board.
    """
    resp = _get(url)
    seen, out = set(), []
    for href, inner in _ANCHOR.findall(resp.text):
        text = re.sub(r"\s+", " ", _ANYTAG.sub(" ", inner)).strip()
        text = html_lib.unescape(text)
        if not (6 <= len(text) <= 90) or _NAV.match(text):
            continue
        if not (_JOB_HREF.search(href) and _TITLEISH.search(text)):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": text, "location": "",
                    "url": urllib.parse.urljoin(url, html_lib.unescape(href))})
    if len(out) < 2:
        raise AtsError("no enumerable job links on the page")
    return out


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
    return fn(ref)
