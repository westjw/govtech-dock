#!/usr/bin/env python3
"""The company's own LinkedIn page, read off the careers page they publish.

    python3 scripts/find_linkedin.py --limit 50          # look only
    python3 scripts/find_linkedin.py --limit 50 --write  # store the confident ones

WHY A COMPANY NEEDS ONE ON THIS BOARD. 781 companies hold a careers page that
will not enumerate and show zero jobs, and 918 have no findable board at all.
For a seeker on one of those cards the page currently ends at "we could not
read their board". Their LinkedIn is the honest next step - it is where a
company that posts nowhere else posts - and the company itself publishes the
address in its own page footer. Measured on a random 25 of the page-only pile:
23 named one.

THIS TOUCHES LINKEDIN ZERO TIMES. It reads the vendor's own careers page,
which refresh.py already fetches every night, and takes the address out of the
markup. Nothing here opens linkedin.com, signs in, or reads a posting. What
lands is a LINK, never a job: the card says "they post here and we are not
counting it", which is the rule posts_at.py already states.

THE PARENT TRAP, WHICH IS THE WHOLE REASON FOR THE NAME CHECK. A careers page
footer carries the parent's LinkedIn as often as its own. In the sample of 23,
five slugs were not the company: Gordian's page names `fortive` (its parent),
SITA's names `axa`, Eccovia's names `caseworthyinc`, MindMixer's names
`socialassurance`, and Saltus's is the opaque numeric form `819952`. That is
22% wrong, and CLAUDE.md's rule about never pointing a company at its parent's
board applies exactly as much to pointing it at a parent's LinkedIn: a seeker
lands on 4,000 Fortive employees and cannot tell which three are Gordian's.

So a slug is stored only when it RESEMBLES THE COMPANY NAME. Everything else
is printed for a person and written nowhere. An acquisition or a rename can
look identical to a mistake from here - Eccovia really did used to be
CaseWorthy - and telling them apart is judgement, not string distance.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                          # noqa: E402

# linkedin.com/company/<slug>. `/jobs` and `/about` are sub-pages of the same
# company, and the trailing segment is dropped so both land on one address.
LI = re.compile(
    r"linkedin\.com/(?:[a-z]{2}/)?company/([A-Za-z0-9._%-]{2,80})", re.I)

# Slugs that are LinkedIn's own furniture or somebody's personal profile
# rather than a company page.
NOT_A_COMPANY = {"company", "companies", "school", "showcase", "jobs", "feed",
                 "in", "pub", "profile", "shareArticle", "sharing"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resembles(name: str, slug: str) -> bool:
    """Is this slug plausibly THIS company's, on the name alone?

    Deliberately crude and deliberately strict. It is not trying to decide
    whether a company was renamed or acquired - it cannot, and the five
    mismatches in the sample include at least one real rename. It is only
    separating "obviously theirs" from "needs a person", and everything in the
    second pile is printed rather than written.
    """
    n, s = norm(name), norm(slug)
    if not n or not s:
        return False
    if s.isdigit():
        # linkedin.com/company/819952 is a valid address and carries no name
        # at all, so nothing here can confirm it belongs to this company.
        return False
    return n.startswith(s[:6]) or s.startswith(n[:6]) or s in n or n in s


def candidates(html: str) -> list[str]:
    out, seen = [], set()
    for m in LI.finditer(html or ""):
        slug = m.group(1).strip("/.")
        if not slug or slug in NOT_A_COMPANY or slug.lower() in seen:
            continue
        seen.add(slug.lower())
        out.append(slug)
    return out


def worklist(companies: list, limit: int | None = None) -> list[dict]:
    """Companies with a page to read and no LinkedIn on file yet."""
    out = []
    for c in companies:
        if c.get("linkedin"):
            continue
        a = c.get("ats") or {}
        url = a.get("ref") or c.get("careers_url") or c.get("website")
        if not url or not str(url).startswith(("http://", "https://")):
            continue
        out.append({"id": c["id"], "name": c["name"], "url": url})
    out.sort(key=lambda r: r["name"])
    return out[:limit] if limit else out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    path = DATA / "companies.json"
    try:
        companies = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"companies.json does not parse ({e}). Resolve it first.",
              file=sys.stderr)
        return 1

    rows = worklist(companies, a.limit)
    by_id = {c["id"]: c for c in companies}
    print(f"{len(rows)} page(s) to read "
          f"(of {len(worklist(companies))} with no LinkedIn on file)\n")

    stored, unsure, none = 0, [], 0
    for r in rows:
        try:
            html = ats._get(r["url"]).text
        except Exception:                          # noqa: BLE001
            continue
        cands = candidates(html)
        if not cands:
            none += 1
            continue
        good = [s for s in cands if resembles(r["name"], s)]
        if good:
            slug = good[0]
            print(f"  {r['name'][:28]:30} linkedin.com/company/{slug}")
            if a.write:
                by_id[r["id"]]["linkedin"] = \
                    f"https://www.linkedin.com/company/{slug}"
            stored += 1
        else:
            unsure.append((r["name"], cands[0]))

    print(f"\n  {stored} confident, {len(unsure)} need a person, "
          f"{none} named none")
    if unsure:
        print("\n  NOT STORED - the slug is not this company's name, which is "
              "how a parent's page gets filed as a subsidiary's:")
        for n, s in unsure:
            print(f"    {n[:30]:32} -> linkedin.com/company/{s}")
        print("  Some of these are real renames or acquisitions. Telling those "
              "from a footer's parent link is judgement, so they wait.")

    if a.write and stored:
        # Same discipline as the other pipeline writers: validate the whole
        # file, then write atomically. A bad edit is refused, never half
        # applied.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(companies, indent=1) + "\n")
        try:
            json.loads(tmp.read_text())
        except json.JSONDecodeError:
            tmp.unlink(missing_ok=True)
            print("refused: the result did not parse", file=sys.stderr)
            return 1
        tmp.replace(path)
        print(f"\n  wrote {stored} linkedin address(es) to companies.json")
    elif stored:
        print("\n  LOOKED ONLY. Re-run with --write to store them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
