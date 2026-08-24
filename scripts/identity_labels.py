#!/usr/bin/env python3
"""What a person said when the identity check got it wrong, kept as labels.

WHY THIS EXISTS

The website panel says "this page never names this company". Sometimes it is
right and the domain belongs to somebody else. Sometimes it is obviously wrong
- eagleview.com, EagleView's logo on it, and the panel is warning you off it.
Until now, overruling that warning meant clicking Save and the disagreement
vanished. The check learned nothing and there was no way to know how often it
was wrong.

So a correction is recorded, and it does two things at once.

IMMEDIATELY, it fixes this company. "It says Eagleview" is a fact with a
structured home: also_known_as. Once written, identifies() checks the alias
too and the panel goes green - for this company, for good. That is the part
that pays for itself on the first use.

OVER TIME, the labels measure the check. Each one stores the stored name, what
the page actually said, and the verdict - which is what CLAUDE.md means by
storing the INPUT the person saw alongside their answer. With enough of them
you can say "the check is wrong 1 in 6 times, and 80% of those are a corporate
suffix the site drops". That is a measurement nobody currently has, and the
gamification design explicitly asks for it.

WHAT IT DELIBERATELY DOES NOT DO

It does not adjust identifies() on its own. A rule loosened by aggregate
statistics is a rule nobody decided, and this particular rule is the only
thing standing between a squatter and the dataset - a false yes here puts
somebody else's company on the board under a name that is not theirs.
propose() below reports what the labels suggest, with the evidence, and stops
there.

Negative labels matter as much as positive ones. "No, that really is a
different company" is the rarer and more valuable answer, because it is the
one that would otherwise never be written down anywhere.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "identity_labels.jsonl"

# Words a company puts on its incorporation papers and leaves off its website.
# Used ONLY to describe a correction after the fact, never to accept one.
SUFFIXES = {"technologies", "technology", "tech", "inc", "incorporated", "llc",
            "ltd", "limited", "corp", "corporation", "company", "co", "group",
            "holdings", "solutions", "systems", "software", "labs", "labs.",
            "international", "global", "worldwide", "partners", "ventures"}


def record(company_id: str, stored_name: str, page_says: str, url: str,
           verdict: str, said_name: str = "", by: str = "owner") -> dict:
    """verdict is "same" (the page is this company) or "different"."""
    if verdict not in ("same", "different"):
        raise ValueError("verdict must be 'same' or 'different'")
    row = {
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "by": by or "owner",
        "company_id": company_id,
        "stored_name": stored_name,
        # the page's own words, which is the input the check was looking at
        "page_says": (page_says or "")[:300],
        "url": url,
        "verdict": verdict,
        "said_name": (said_name or "").strip()[:120],
    }
    with LOG.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def load() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _words(s: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


def propose() -> dict:
    """What the labels say about the check. A report, not a change."""
    rows = load()
    same = [r for r in rows if r["verdict"] == "same"]
    diff = [r for r in rows if r["verdict"] == "different"]
    # of the corrections, how many were simply a corporate suffix the site drops
    suffix_only, other = [], []
    for r in same:
        stored = _words(r["stored_name"])
        said = _words(r.get("said_name") or "")
        if not said:
            other.append(r)
            continue
        dropped = [w for w in stored if w not in said]
        if dropped and all(w in SUFFIXES for w in dropped):
            suffix_only.append((r, dropped))
        else:
            other.append(r)
    dropped_counts = collections.Counter(
        w for _r, dropped in suffix_only for w in dropped)
    n = len(rows)
    return {
        "labels": n,
        "wrongly_warned": len(same),
        "correctly_warned": len(diff),
        # the honest headline: how often the warning was worth heeding
        "warning_precision": (round(len(diff) / n, 3) if n else None),
        "explained_by_a_dropped_suffix": len(suffix_only),
        "suffixes_seen": dropped_counts.most_common(8),
        "not_explained": len(other),
        "enough_to_act_on": n >= 25,
        "note": ("Under 25 labels this is an anecdote, not a measurement. "
                 "Nothing here changes identifies(); it is a report."),
    }


def main() -> int:
    p = propose()
    if not p["labels"]:
        print("no identity corrections recorded yet.")
        print("They accumulate as you overrule the website panel.")
        return 0
    print(f"{p['labels']} correction(s) recorded")
    print(f"  the warning was right   : {p['correctly_warned']}")
    print(f"  the warning was wrong   : {p['wrongly_warned']}")
    if p["warning_precision"] is not None:
        print(f"  so it is worth heeding  : {p['warning_precision']:.0%} of the time")
    print(f"  wrong purely because the site drops a corporate suffix: "
          f"{p['explained_by_a_dropped_suffix']}")
    if p["suffixes_seen"]:
        print(f"    {', '.join(f'{w} x{n}' for w, n in p['suffixes_seen'])}")
    print(f"  needing a different explanation: {p['not_explained']}")
    print(f"\n{p['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
