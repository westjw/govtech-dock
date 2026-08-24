#!/usr/bin/env python3
"""Where a company posts jobs when it has no board we can read.

WHY THIS IS NOT "no public board"

The no-board queue offered two outcomes: paste an ATS address, or dismiss the
row. So a company that advertises every opening on LinkedIn got recorded
exactly the same as one that hires by word of mouth - dismissed, and gone.

Those are opposite facts. One is "there is nothing to find". The other is
"there is plenty to find, we simply cannot enumerate it from here" - and a
person looking for a job can go and look at it in one click.

Collapsing them is the asymmetric error this whole repo is built around,
wearing a different hat: the public card ends up implying a company is not
hiring when it is advertising openly, and nothing ever contradicts it, because
"we found nothing" is indistinguishable from "we did not look in the right
place".

WHY IT IS NOT AN `ats` TYPE EITHER

`ats` means "monitored": refresh.py fetches it, counts postings, and the board
reports the number. A LinkedIn jobs page is not monitorable - scraping it
would breach their terms and the capture bookmarklet exists precisely because
we do not do that. Filing LinkedIn under `ats` would make refresh try, fail,
and record a zero, which is the false negative again.

So this is its own field, and it means exactly one thing: THIS COMPANY POSTS
PUBLICLY, HERE, AND WE ARE NOT COUNTING IT. The card says so in those words,
links out, and never claims a number.
"""
from __future__ import annotations

import datetime as dt
import re

# The places a small govtech vendor actually advertises, in the order they
# come up. "email" and "word of mouth" are deliberately present: they are real
# answers, and recording them is what stops the row being asked again forever.
WHERE = {
    "linkedin":  ("LinkedIn", r"linkedin\.com"),
    "indeed":    ("Indeed", r"indeed\.com"),
    "glassdoor": ("Glassdoor", r"glassdoor\."),
    "ziprecruiter": ("ZipRecruiter", r"ziprecruiter\.com"),
    "builtin":   ("Built In", r"builtin\."),
    "govportal": ("a government jobs portal", r"governmentjobs\.com|neogov|"
                                              r"\.gov/|usajobs\.gov"),
    "recruiter": ("an outside recruiter", None),
    "email":     ("by email only", None),
    "other":     ("somewhere else", None),
}

# A jobs page, not a company profile. linkedin.com/company/acme is a brochure;
# linkedin.com/company/acme/jobs is the thing a job seeker wants. Sending
# somebody to the brochure and calling it "where they post" is a small lie
# that costs them a click and some faith.
JOBSY = re.compile(r"/jobs?(/|$|\?)|/careers?(/|$|\?)|/openings|/positions|"
                   r"/vacanc|/search", re.I)


def guess_where(url: str) -> str | None:
    """Which service an address belongs to, from the address alone."""
    u = (url or "").lower()
    for key, (_label, pattern) in WHERE.items():
        if pattern and re.search(pattern, u):
            return key
    return None


def label(where: str) -> str:
    return WHERE.get(where, WHERE["other"])[0]


def check(where: str, url: str) -> str | None:
    """Refuse a record that would mislead somebody. Returns a problem or None."""
    if where not in WHERE:
        return f"unknown place {where!r}"
    if where in ("email", "recruiter"):
        return None                       # no link is expected for these
    if not url:
        return f"a link is needed to say they post on {label(where)}"
    if not url.startswith(("http://", "https://")):
        return "the link needs to start with https://"
    guessed = guess_where(url)
    if guessed and guessed != where:
        return (f"that link is {label(guessed)}, not {label(where)} - "
                f"pick the one that matches, or fix the link")
    if where == "linkedin" and not JOBSY.search(url):
        return ("that is their LinkedIn page, not their jobs page. Add /jobs "
                "to the end so the link lands on the openings.")
    return None


def build(where: str, url: str, by: str = "owner", note: str = "") -> dict:
    """The record written onto the company."""
    return {
        "where": where,
        "label": label(where),
        "url": (url or "").strip() or None,
        "note": (note or "").strip() or None,
        "on": dt.date.today().isoformat(),
        "by": by or "owner",
    }


def sentence(rec: dict) -> str:
    """How the public card says it. Plain, and honest about the limit."""
    if not rec:
        return ""
    where = rec.get("where")
    if where == "email":
        return ("They do not run a public job board - openings go out by email. "
                "There may well be roles open that this page cannot show.")
    if where == "recruiter":
        return ("They hire through an outside recruiter rather than a public "
                "board, so their openings are not listed here.")
    return (f"They advertise their openings on {rec.get('label') or 'another site'} "
            f"rather than a job board we can read, so the roles are not counted "
            f"here - the link goes straight to their listings.")
