#!/usr/bin/env python3
"""Free-text notes on a company, and the structured facts they hint at.

WHY A NOTE FIELD AT ALL, WHEN THERE ARE ALREADY DROPDOWNS

Because the truth is messier than any menu. The case that prompted this:

    "madison ai advertises on linkedin but used a job service that is on a
     board with multiple sites"

Every dropdown in the admin gets that wrong. "Posts elsewhere -> LinkedIn" is
true and incomplete. "Paste the board address" would file a MULTI-TENANT board
against one company, so every other tenant's postings would be reported as
Madison AI's - the same failure as pointing a subsidiary at its parent's
Workday, which this repo already refuses to do. And "no public board" is
flatly false.

There is no menu that holds that thought. So: a sentence, kept.

WHY THE NOTES ARE READ, NOT JUST STORED

A note nobody reads again is a diary. These are read twice. A person opening
the row tomorrow sees the reasoning instead of re-deriving it, and the
detectors below look for the handful of situations that have a STRUCTURED
home somewhere else in the dataset, and offer to file it there.

They only ever SUGGEST. Every one of these detectors is a regex over prose
somebody typed quickly, which is exactly the kind of evidence this repo
refuses to act on by itself - "a submission is a claim, not a fact", and a
note is a submission from your past self. The suggestion is shown with the
words that triggered it, so it can be judged rather than trusted.

The most important detector is the shared-board one, because that mistake is
invisible: the board fills with real postings that belong to somebody else,
every count looks healthy, and nothing ever contradicts it.
"""
from __future__ import annotations

import datetime as dt
import re

MAX_NOTE = 2000

# Each concept: the pattern, what it means in plain words, and which
# structured field it would go to if the person agrees. `field` None means
# there is nowhere to file it - it stays prose, which is a fine outcome.
CONCEPTS: list[tuple[str, str, str, str | None]] = [
    ("shared_board",
     r"multi[- ]?(tenant|site|company)|multiple (sites|companies|employers|"
     r"clients)|shared (board|site)|job (service|aggregator|portal) (that |which )?"
     r"(hosts|lists|covers)|hosts (several|multiple|other)",
     "This board carries more than one company's jobs, so postings read off it "
     "may belong to somebody else.",
     "shares_board_with"),
    ("acquired",
     r"\bacquired by\b|\bbought by\b|\bnow part of\b|\bmerged (in)?to\b|"
     r"\bsubsidiary of\b|\bowned by\b",
     "They were acquired, so their careers page may redirect to the parent's "
     "board - which must never be recorded as theirs.",
     "parent"),
    ("renamed",
     r"\bformerly\b|\brenamed\b|\bused to be (called|known)\b|\bnow called\b|"
     r"\brebranded\b|\bdba\b",
     "They trade under a different name now; the old one is worth keeping as "
     "an alias so a search still finds them.",
     "also_known_as"),
    ("posts_elsewhere",
     r"\blinkedin\b|\bindeed\b|\bglassdoor\b|\bziprecruiter\b|\bbuilt ?in\b|"
     r"governmentjobs|neogov|\bposts? (on|via|through)\b",
     "They advertise somewhere we cannot count, so the card should link there "
     "rather than say no board was found.",
     "posts_at"),
    ("defunct",
     r"\bshut down\b|\bout of business\b|\bdefunct\b|\bwound down\b|\bceased\b|"
     r"\bno longer (exists|operating|trading)\b",
     "They may no longer exist, which is a removal decision rather than a "
     "board one.",
     None),
    ("not_govtech",
     r"\bnot govtech\b|\bhorizontal\b|\bsells to everyone\b|\bgeneral purpose\b|"
     r"\bnot (specific|specifically) (to )?gov",
     "This reads like a horizontal vendor rather than a govtech product, which "
     "is the Vendor scope question.",
     "vendor_scope"),
    ("hiring_freeze",
     r"\bhiring freeze\b|\bfroze hiring\b|\blayoff|\blaid off\b|\bRIF\b",
     "Hiring is paused, which explains an empty board without meaning the "
     "board is unreadable.",
     None),
]

_COMPILED = [(k, re.compile(p, re.I), meaning, field)
             for k, p, meaning, field in CONCEPTS]


def read_note(text: str) -> list[dict]:
    """What a note appears to be saying. Suggestions only, with the evidence.

    Returns the matched words so a person can see WHY it fired. A detector
    that says "shared board" without showing which phrase triggered it is
    asking to be trusted, and none of these have earned that.
    """
    out = []
    for key, pattern, meaning, field in _COMPILED:
        m = pattern.search(text or "")
        if m:
            out.append({"concept": key, "means": meaning, "field": field,
                        "matched": m.group(0)})
    return out


def clean(text: str) -> str:
    """Notes are rendered in the admin and can be read by an agent later."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "",
                  str(text or "")).strip()[:MAX_NOTE]


def add(company: dict, text: str, by: str = "owner") -> dict:
    """Append a note. Append, never replace: an earlier reading stays visible
    even when a later one contradicts it, because which of the two was right
    is exactly the thing you want to see later."""
    body = clean(text)
    if not body:
        raise ValueError("a note needs some text")
    note = {"text": body, "by": by or "owner",
            "on": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "reads": read_note(body)}
    company.setdefault("notes", []).append(note)
    return note


def search(companies: list, query: str) -> list[dict]:
    """Find companies by what was written about them.

    Words, not substrings: searching "ai" should not return every company
    whose note contains "available".
    """
    terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 1]
    if not terms:
        return []
    hits = []
    for c in companies:
        for n in c.get("notes") or []:
            words = set(re.split(r"\W+", n["text"].lower()))
            if all(t in words for t in terms):
                hits.append({"id": c["id"], "name": c["name"], "note": n})
                break
    return hits
