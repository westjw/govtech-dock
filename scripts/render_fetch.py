"""Enumerate job titles from a JS-rendered careers page, using a real browser.

50 companies have a live board whose roles cannot be read server-side, because
the page is a shell and the listings arrive from JavaScript. That is precisely
why they were tagged html-type. Requests cannot see them; a browser can.

Kept as an OPTIONAL fallback, imported lazily and never required. build_board.py
tries requests first and only reaches for this when a page turns out to be a
shell, so the stdlib path still works end to end with Playwright absent. That
matters: selftest.py, a local run, and anyone cloning the repo should not need a
150MB browser to use it.

  pip install playwright && python -m playwright install chromium

Two rules that keep this honest rather than merely powerful:

It reads the rendered DOM and nothing else. No authenticated sessions, no
LinkedIn, no bot-check circumvention. A page that blocks a browser is a
deliberate refusal and goes to the manual worklist.

It returns nothing rather than something dubious. Under two plausible titles it
raises, because one match on a careers page is far more often a stray link than
a real listing.
"""
from __future__ import annotations

import re
import sys
import urllib.parse

# The same shape tests the requests path uses, so a title has to look like a job
# whichever way the page was read.
JOB_HREF = re.compile(r"/(job|jobs|career|careers|position|opening|vacanc|apply|"
                      r"posting|req)[/\-_?=]|jobId|requisition|gh_jid|lever\.co|"
                      r"ashbyhq|greenhouse", re.I)
TITLEISH = re.compile(r"\b(engineer|developer|manager|director|analyst|specialist|"
                      r"executive|representative|coordinator|associate|lead|architect|"
                      r"designer|scientist|consultant|administrator|technician|"
                      r"supervisor|officer|president|counsel|accountant|recruiter|"
                      r"marketer|strategist|advocate|partner|intern|apprentice|"
                      r"operator|driver|installer|trainer|writer|editor|controller|"
                      r"planner|advisor|agent|success|support|sales)\b", re.I)
NAV = re.compile(r"^(apply|apply now|learn more|read more|view (all|jobs|openings)|"
                 r"see (all|more)|careers?|jobs?|open (roles|positions)|back|next|"
                 r"previous|home|about|contact|search|filter|all departments?|"
                 r"privacy|terms|cookie|sign in|log in|menu)s?$", re.I)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# An anchor's innerText includes its children, so a card that renders the title
# and the location as siblings arrives as one string: "Senior Revenue Accountant
# BOSTON, MASSACHUSETTS, UNITED STATES". Split the trailing location off rather
# than letting it pollute every title.
# Splitting title from location is done by scanning candidate positions rather
# than with one regex. A regex matches leftmost-first, so a pattern permissive
# enough to catch "BOSTON, MASSACHUSETTS, UNITED STATES" also swallowed the
# capitalised title words in front of it: "Senior Revenue Accountant BOSTON, ..."
# split to a title of "Senior".
_DANGLING = re.compile(r"\b(to|for|of|and|in|at|the|a|an|with|on|from)$", re.I)


def _looks_like_location(tail: str) -> bool:
    """A location tail is capitalised throughout and either has a comma or
    starts with Remote. Requiring that keeps ordinary title words out."""
    t = tail.strip()
    if not t:
        return False
    if re.match(r"^remote\b", t, re.I):
        return True
    if "," not in t:
        return False
    # A location never contains a job word. Without this, "Regional Sales Manager
    # Des Moines, IA" split at "Sales" and filed "Manager Des Moines, IA" as the
    # location.
    if TITLEISH.search(t):
        return False
    return all(w[:1].isupper() or not w[:1].isalpha() for w in t.split())


def split_location(text: str) -> tuple[str, str]:
    """Separate a trailing location from a concatenated anchor label.

    Scans split points left to right across the final words, taking the first
    that leaves a head still reading as a job title. Returns the text unchanged
    when no split qualifies: a slightly long title beats a truncated one.
    """
    words = text.split()
    if len(words) < 3:
        return text.strip(), ""
    for i in range(max(1, len(words) - 8), len(words)):
        head, tail = " ".join(words[:i]), " ".join(words[i:])
        if len(head) < 6 or not TITLEISH.search(head) or _DANGLING.search(head):
            continue
        if _looks_like_location(tail):
            return head.strip(" ,-|"), tail.strip()
    return text.strip(), ""


class RenderUnavailable(RuntimeError):
    """Playwright is not installed. Callers fall back to the requests path."""


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def fetch_rendered(url: str, *, timeout_ms: int = 25000,
                   settle_ms: int = 2200) -> list[dict]:
    """Return [{title, location, url}] from a rendered careers page."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                    # pragma: no cover
        raise RenderUnavailable("playwright is not installed") from exc

    out, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA,
                                  viewport={"width": 1280, "height": 1000})
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass                              # some boards never go idle
            page.wait_for_timeout(settle_ms)
            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => [e.getAttribute('href') || '', "
                "(e.innerText || '').trim()])")
        finally:
            browser.close()

    # THE COUNT RULE LIVES IN ats.py AND IS IMPORTED, NOT COPIED. This
    # extractor has its own NAV filter and never learned that a count is not a
    # job title, so Nutanix came back as hiring "102 open vacancies in Sales".
    # ats.fetch_html_titles has refused that shape since the LinkedIn browse
    # rail put "Engineer jobs 555,845 open jobs" on the public board - and a
    # rule that only one of two extractors knows is a rule with a hole in it.
    #
    # THAT SENTENCE WAS WRITTEN HERE AND THEN IGNORED, one file over. strip_cta
    # was added to fetch_html_titles alone to take Adobe's "Apply Now" off the
    # front of nine job titles, and the very next rebuild published all nine
    # unchanged - because Adobe's board is JavaScript and comes through THIS
    # extractor, not that one. The commit message said they would correct on
    # the next run. They did not.
    from ats import _JOB_COUNT, strip_cta

    for href, raw in links:
        raw = re.sub(r"\s+", " ", raw or "").strip()
        if not (6 <= len(raw) <= 160) or NAV.match(raw):
            continue
        if _JOB_COUNT.search(raw):
            continue
        if not (JOB_HREF.search(href or "") and TITLEISH.search(raw)):
            continue
        # Before the split, not after: "Apply Now" sits in front of the title
        # and would otherwise be what split_location reads as the head.
        raw = strip_cta(raw)
        title, loc = split_location(raw)
        if not TITLEISH.search(title):
            title, loc = raw, ""      # never lose a role to an over-eager split
        if not (5 <= len(title) <= 120):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "location": loc,
                    "url": urllib.parse.urljoin(url, href)})
    if len(out) < 2:
        raise ValueError("no enumerable job links in the rendered page")
    return out


if __name__ == "__main__":
    if not available():
        print("playwright not installed", file=sys.stderr)
        raise SystemExit(2)
    for u in sys.argv[1:]:
        try:
            jobs = fetch_rendered(u)
            print(f"{u}: {len(jobs)} titles")
            for j in jobs[:6]:
                print(f"   {j['title'][:70]}")
        except Exception as exc:
            print(f"{u}: {exc}")
