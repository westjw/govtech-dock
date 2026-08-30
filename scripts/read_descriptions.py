#!/usr/bin/env python3
"""Read the job descriptions the daily crawl deliberately leaves unread.

    python3 scripts/read_descriptions.py --limit 200
    python3 scripts/read_descriptions.py --limit 50 --company verkada
    python3 scripts/read_descriptions.py --dry-run

1,742 of the 4,452 postings on this board have never had their description
read, and the fix has existed the whole time: seven boards publish the ad text
on the posting page rather than in the list response, `ats.FETCH_DETAILS` turns
that on, and it is off because switching it on would add roughly nine hundred
requests to a daily refresh that already takes twenty minutes against other
people's servers.

So this is that work, taken OUT of the refresh: a bounded number of postings a
night, resumable, rotating so nothing waits forever, and safe to kill at any
moment. Nothing here is on the critical path - if it never runs again the board
is exactly as correct as it is today, only quieter about pay.

WHAT READING ONE BUYS. Three things, and they compound:

  - A stated salary. salary.py finds a range in roughly three descriptions out
    of four that state one, and pay is the filter people actually use.
  - Google for Jobs. functions/_middleware.js emits a JobPosting block ONLY for
    postings whose description was read, deliberately - structured data about a
    posting we never opened is a claim we cannot support. Every description
    read here makes one more posting eligible for the one channel that sends
    high-intent traffic to a board this size.
  - The difference between "they did not state pay" and "we never looked",
    which the board already reports honestly and which this shrinks.

WHY A CACHE FILE AND NOT A WRITE INTO board.json. board.json is rebuilt from
scratch by every crawl, so anything written into it directly is destroyed on
the next run - and hand-editing it would be the same class of mistake as
hand-editing a history snapshot. The descriptions land in data/jd_cache.json
keyed by the posting's own url, and build_board reads that cache for any row
whose fetcher returned no description. A cache entry is therefore always a
FALLBACK: a fresher reading from the board itself always wins.

WHAT IT WILL NOT DO. It does not invent, and it does not retry forever. A
posting whose page will not load is recorded as attempted with no text, which
is a fact about the fetch and not about the job, and it goes to the back of the
rotation rather than being asked again tomorrow. The pay is not parsed here at
all - salary.py runs at build time so that a description read in August picks
up a parser fix made in October, which is the same reason captured rows are
parsed at build rather than at capture.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ats                                                  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "jd_cache.json"

# One request per posting on somebody else's API. ats.DETAIL_PAUSE is 0.2s
# between calls inside a single company's fetch; this is the pause between
# POSTINGS here, deliberately slower, because this script exists to be run
# unattended and a script nobody is watching should be the politest thing on
# the network.
PAUSE = 0.6

# Boards that publish the description only on the posting page, AND give that
# posting a page of its own. Everything else either hands the description over
# in the list response we already make - those are free and already read - or
# has nothing a second request would find.
#
# RIPPLING IS THE INSTRUCTIVE EXCLUSION, and it is why this list is not just
# "the seven types ats.py has a detail reader for". Its postings all carry the
# BOARD's url - 65 postings across 13 urls, `ats.rippling.com/<slug>/jobs` - so
# there is no per-posting page to open. Fetching it 65 times would download the
# same JS shell 65 times, find no JobPosting block in any of them, and record
# 65 postings as read-and-empty, which is a false "they stated no pay" written
# 65 times over. Caught by a real run returning 1 of 12; the eleven were all
# Rippling asking the same url.
#
# The rule this encodes: a type belongs here only if its postings have distinct
# urls. check_jd_backfill_targets_real_pages in selftest re-derives that from
# the board rather than trusting this set.
DETAIL_TYPES = {"bamboohr", "smartrecruiters", "workday", "icims",
                "breezy", "jazzhr"}


def load(p: pathlib.Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_atomic(p: pathlib.Path, obj) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    tmp.replace(p)


def worklist(board: dict, companies: list, only: str | None) -> list[dict]:
    """Postings with no description, on a board that has one to give.

    ROTATION, NOT A QUEUE. Ordered by when we last TRIED - never attempted
    first, then oldest attempt - so a posting whose page fails every night
    cannot sit at the head of the list starving everything behind it. Same
    shape as the render rotation in build_board, and for the same reason: the
    failure mode of a plain queue is that the first hundred broken things
    become the only hundred things you ever try.
    """
    cache = load(CACHE, {})
    by_id = {c.get("id"): c for c in companies}
    out = []
    for p in board.get("postings", []):
        if p.get("jd_seen"):
            continue
        if only and p.get("company_id") != only:
            continue
        url = p.get("url")
        if not url:
            continue
        c = by_id.get(p.get("company_id")) or {}
        atype = ((c.get("ats") or {}).get("type") or "").lower()
        # `html` boards are read by fetch_html_titles, which already picks up
        # any JobPosting block in the page it downloaded. There is no second
        # request that would find more, so asking again would be 759 requests
        # for nothing.
        if atype not in DETAIL_TYPES:
            continue
        prev = cache.get(url) or {}
        out.append({"url": url, "title": p.get("title"), "company": p.get("company"),
                    "company_id": p.get("company_id"), "ats": atype,
                    "tried": prev.get("tried") or ""})
    out.sort(key=lambda r: (r["tried"] or "", r["url"]))
    return interleave(out)


def interleave(rows: list[dict]) -> list[dict]:
    """Round-robin by company, keeping the rotation order between companies.

    Sorted by attempt date alone, a run of 150 lands 150 requests on ONE
    company's API in a row - Adtran had 8 unread postings at the head of the
    list and a bigger board would have had all 150. The pause between calls is
    what makes this polite in aggregate; hitting one host 150 times in ninety
    seconds is not polite however long the pause is.

    So companies take turns, and the order companies take their first turn in
    is still the rotation order - a company nothing has ever been read from
    still goes before one read last week.
    """
    order: list[str] = []
    by_co: dict[str, list[dict]] = {}
    for r in rows:
        cid = r["company_id"] or ""
        if cid not in by_co:
            by_co[cid] = []
            order.append(cid)
        by_co[cid].append(r)
    out: list[dict] = []
    while order:
        for cid in list(order):
            out.append(by_co[cid].pop(0))
            if not by_co[cid]:
                order.remove(cid)
    return out


def read_one(row: dict) -> str:
    """The description behind one posting url, or "".

    Every failure is swallowed and reported as no text. This runs unattended
    over thousands of third-party pages; one 500 must not end the run, and a
    page that did not load is recorded as a page that did not load rather than
    as a posting with no description.
    """
    url = row["url"]
    try:
        if row["ats"] == "bamboohr":
            jd, _ = ats._bamboohr_detail(url.rstrip("/") + "/detail")
            return jd or ""
        # Everything else publishes an ld+json JobPosting on the posting page,
        # which is the same block fetch_html_titles already knows how to read.
        # Using that rather than seven bespoke detail parsers means this script
        # gains nothing to drift out of step with.
        resp = ats._get(url)
        found = ats._page_postings(resp.text)
        if not found:
            return ""
        want = ats.plain(str(row.get("title") or ""))
        jd, _ = found.get(want, ("", None))
        if jd:
            return jd
        # ONE posting on the page and one job we asked about: that is the job.
        # More than one and we do not guess - a page listing several roles is a
        # section, and filing one job's description under another's title is
        # worse than having no description at all.
        if len(found) == 1:
            return next(iter(found.values()))[0]
        return ""
    except Exception:                     # noqa: BLE001 - see docstring
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150,
                    help="how many postings to read this run (default 150)")
    ap.add_argument("--company", help="only this company id")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be read, fetch nothing, write nothing")
    a = ap.parse_args()

    board = load(DATA / "board.json", {})
    companies = load(DATA / "companies.json", [])
    if isinstance(companies, dict):
        companies = companies.get("companies", [])
    if not board.get("postings"):
        print("no board.json to work from - run build_board.py first",
              file=sys.stderr)
        return 1

    work = worklist(board, companies, a.company)
    total_unread = sum(1 for p in board["postings"] if not p.get("jd_seen"))
    print(f"{total_unread:,} postings have no description; {len(work):,} of them "
          f"are on a board that publishes one")
    if not work:
        return 0

    todo = work[:max(0, a.limit)]
    never = sum(1 for r in todo if not r["tried"])
    print(f"reading {len(todo):,} this run "
          f"({never} never attempted, oldest attempt "
          f"{todo[0]['tried'] or 'never'})")
    if a.dry_run:
        for r in todo[:10]:
            print(f"  would read  {r['company']} - {r['title']}")
        if len(todo) > 10:
            print(f"  ... and {len(todo) - 10:,} more")
        return 0

    cache = load(CACHE, {})
    today = dt.date.today().isoformat()
    got = 0
    started = time.time()
    try:
        for i, r in enumerate(todo):
            if i:
                time.sleep(PAUSE)
            jd = read_one(r)
            # STAMPED WHETHER OR NOT IT WORKED, and stamped even when it did
            # not, because that is what moves a failing posting to the back of
            # the rotation. Recording only successes is how a queue turns into
            # a hundred broken urls retried forever.
            entry = {"tried": today}
            if jd:
                entry["jd"] = jd
                entry["read_on"] = today
                got += 1
            elif (cache.get(r["url"]) or {}).get("jd"):
                # a previous run read it; keep that text and only restamp
                entry = dict(cache[r["url"]], tried=today)
            cache[r["url"]] = entry
            if i and i % 25 == 0:
                write_atomic(CACHE, cache)
                print(f"  {i}/{len(todo)} - {got} description(s) so far")
    except KeyboardInterrupt:
        print("\n  stopped - keeping what was read so far")
    finally:
        # ALWAYS. This script is meant to be killed mid-run, and a run that
        # threw away an hour of reading because it was interrupted would teach
        # everyone to never interrupt it.
        write_atomic(CACHE, cache)

    took = time.time() - started
    print(f"read {got:,} of {len(todo):,} attempted in {took/60:.1f} min "
          f"({got/len(todo)*100:.0f}%)")
    print(f"data/jd_cache.json now holds "
          f"{sum(1 for v in cache.values() if v.get('jd')):,} description(s)")
    print("run build_board.py to fold them into the board")
    return 0


if __name__ == "__main__":
    sys.exit(main())
