#!/usr/bin/env python3
"""The spine every pipeline agent shares: briefs out, proposals in.

WHY AGENTS DO NOT WRITE

This repo already decided that a submission from a stranger is a claim, not a
fact, and that nothing reaches companies.json without a person approving it.
An agent is a stranger who types faster. It gets exactly the same deal.

So an agent never edits the dataset. It reads a BRIEF (assembled here,
deterministically, from what we already know) and returns a PROPOSAL, which
lands in data/agent_proposals.json and shows up in the admin queue next to the
evidence it was based on. A person accepts or rejects. That keeps three things
true at once:

  - refresh.py and CI stay deterministic and stdlib-only, as CLAUDE.md requires
  - a bad model run costs a queue full of rejects, never a corrupted map
  - the accept/reject becomes labelled training data, because the brief the
    agent saw is stored alongside the answer it gave

WHY THE BRIEF IS BUILT HERE AND NOT BY THE AGENT

If the agent gathers its own context it will gather different context every
run, and two proposals that disagree will be impossible to compare. Building
briefs deterministically means a re-run is a re-run.

THE ASYMMETRIC ERROR STILL RULES

An agent that answers everything is worse than one that answers less. Placing
a company wrongly puts it in front of visitors under a heading that is untrue;
saying "unsure" costs one human glance. Every agent here must be able to
return unsure, and the intake below REFUSES a proposal that claims high
confidence without evidence, because that is the shape a guess takes when a
model is trying to be helpful.

Agents planned on this spine, all the same shape:
  bucket   - a product company filed under Suppliers & Services: where does it
             really belong                                      (built)
  read     - the careers pages a person can read and no fetcher can: open one
             and read the jobs off it                           (built)
  card     - a new company arrives from a submission or a conference list:
             research the card                                  (next)
  board    - find the ATS behind a page, so `read` never has to run on it
             again  - and the n=60 trial says this one is worth
             more than `read` is                                (next)
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORE = DATA / "agent_proposals.json"

KINDS = ("bucket", "read", "card", "board")
CONFIDENCE = ("high", "medium", "low", "unsure")


def _schema() -> dict:
    s = json.loads((DATA / "schema.json").read_text())
    return {x["name"]: [c for c in x["categories"] if c != "Suppliers & Services"]
            for x in s["sectors"]}


def load() -> dict:
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def save(store: dict) -> None:
    STORE.write_text(json.dumps(store, indent=1, sort_keys=True) + "\n")


# ---------------------------------------------------------------- briefs

def brief_bucket(limit: int | None = None) -> list[dict]:
    """One brief per company sitting in the wrong bucket.

    The deterministic guesser already runs on these and places 92 of 238; it
    fails on the rest because it counts keywords and the descriptions are one
    line long - "Endpoint management and security platform" contains none of
    its vocabulary. So the brief carries the guesser's answer as a STARTING
    POINT clearly labelled as such, not as ground truth: an agent told "the
    regex said Public Safety" and left to agree is a more expensive regex.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin

    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    rows = admin.q_miscategorized(companies, board)
    done = load()
    out = []
    for r in rows:
        if f"bucket:{r['id']}" in done:
            continue          # already proposed; a re-run does not re-ask
        out.append({
            "kind": "bucket",
            "key": f"bucket:{r['id']}",
            "id": r["id"],
            "name": r["name"],
            "description": r.get("description") or "",
            "website": r.get("website") or "",
            "open_roles": r.get("open_roles", 0),
            "filed_now": f"{r['sector']} / {r['category']}",
            "regex_guess": (f"{r['proposed_sector']} / {r['proposed_category']}"
                            if r.get("proposed_sector") else None),
            "regex_confidence": r.get("confidence"),
        })
    return out[:limit] if limit else out


def brief_read(limit: int | None = None) -> list[dict]:
    """One brief per careers page a person can read and a fetcher cannot.

    806 companies - 42% of the map - are in this state (run coverage.py for
    the live figure; do not quote this one), and it is the single biggest hole
    on the board. They are third-party widgets in iframes, session-gated
    boards, and pages that only draw a list after somebody clicks something,
    which is exactly why the capture bookmarklet exists - a person looking at
    the page sees the jobs anyway.

    MEASURED TWICE. n=25 on 2026-08-24 (8 yielded), then n=60 drawn at random
    from this worklist with seed 20260824 (16 yielded, 88 rows). Pooled that
    is 24 of 85 = 28% [95% CI 20-39%]. The numbers that decide whether to run
    this at scale are in the n=60 block below; read it before quoting 28% at
    anybody, because the hit rate is not the number that matters.

    The older note here - and in CLAUDE.md - said rendering a sample of 25 in
    headless Chromium recovered ZERO. That is not what happens. Three things
    separate the two results, and all three are about the reader, not the
    browser:

      - READ THE CHILD FRAMES. The finding that these are widgets in iframes
        is correct, and reading only the top document is why a render comes
        back empty from a page that is visibly full of jobs. Autura's board is
        a Greenhouse job_board in an iframe; the frame had the list in it.
      - THE TITLE DECIDES, NOT THE LINK. Requiring the job-link shape before
        looking at a row is right on a job board and wrong here, where rows
        are divs with an onclick or links to /roles/x. It threw away 10 real
        reqs on Nearmap and 34 on Nedap, both rendered in plain sight.
      - WAIT. networkidle plus a few seconds; these lists draw late.

    THE n=60 RESULT, AND WHY THE HIT RATE IS THE WRONG NUMBER. 16 of 60
    yielded 88 rows. But run classify.py over those 88 and only 14 are `ae`
    and 14 `sales_other`; 60 are neither. Per company that is 5 of 60 gaining
    an AE row [CI 4-18%] and 2 more gaining Sales (non-AE). So the honest
    headline is not "27% of pages read" - it is THIS TOOL'S OWN STATUS CHANGES
    ON 7 COMPANIES IN 60, and the other 9 yields are welders, GIS operators
    and kernel engineers that this board does not rank on. Extrapolating the
    AE-row count to 806 gives ~188 rows with a bootstrap 95% interval of
    27-403, which is another way of saying the sample cannot size this.

    THE CHEAP PATH DID MOST OF THE WORK. 9 of the 16 yields, and 57 of the 88
    rows, came from a plain stdlib-shaped GET with no browser at all - the
    list was in the served HTML and the earlier trials never looked. Rendering
    the other 51 pages cost 12.9s each against 1.2s, needed Playwright, and
    bought 7 more companies. Try the fetch first, always.

    Traps beyond the nav chrome, all of which produce strings with the exact
    shape of a job title and are not reqs. NAV_CHROME below catches none of
    them, and a reader here must:

      - TESTIMONIAL AND LEADERSHIP BYLINES, far and away the commonest false
        positive - it accounted for role-shaped strings on 9 of the 60. A name
        above a title is an employee, not an opening. OnSolve's is a rotating
        carousel, so two reads of the same page return different "jobs".
      - FILTER CHIPS. "All / Support / Sales / Developer / Marketing" above a
        list, or "Filter by department" above nothing at all.
      - A GROUP BOARD UNDER ONE BRAND'S NAME. Justice Systems by LONG's URL
        renders 80 reqs and not one is Justice Systems' - they are the
        parent's HVAC technicians. Filing those would put a false number on
        the board. 7 of 60 careers URLs pointed at a parent's hub.
      - AN ld+json JobPosting THAT IS NOT A JOB. Exactly one of the 60 pages
        carried machine-readable ld+json, and its title was "Multiple
        Positions | See Open Roles" - recruitment marketing shaped as schema.
        Do not build an extractor that trusts ld+json on these pages.
      - A COUNT WITHOUT A TITLE. Lucy Zodion's site says ALL JOBS (1) and
        renders no row. A count is not a posting; propose nothing.

    And the thing worth more than the read, now measured: 21 of the 60 pages
    exposed an ATS vendor host - in the served HTML, or in what the page
    called at render time - against 16 that gave up any rows at all. FINDING
    THE BOARD BEHIND THE PAGE HITS MORE OFTEN THAN READING THE PAGE, and it is
    permanent where a read is a snapshot somebody must re-take by hand. When a
    read turns up an ATS host that is the finding, and it belongs to the
    `board` agent. Verify any slug with a real fetch before writing it.

    So this agent does what the bookmarklet does: opens the page, reads what is
    actually on screen, and hands the rows over. Two rules the harvester
    learned the hard way apply to the agent verbatim, and the intake below
    enforces both:

      - a job link is the job SEGMENT PLUS SOMETHING AFTER IT. Matching
        "/careers" alone returned CHALLENGES, SOLUTIONS and Cookie
        Preferences - the same nav chrome that fools page scans.
      - POSITION FIRST, PATTERN SECOND. Take the first non-chip line as the
        title, then look for a location among the lines after it. Testing the
        location pattern first steals the title whenever one looks like a
        place.

    And the boundary the bookmarklet holds, this agent holds too: read the
    page you were pointed at, once. Do not crawl the site, do not paginate,
    do not follow links, do not sign in. That line is what makes this reading
    a public page rather than harvesting a site.
    """
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    live = {o["id"]: o.get("open_roles", 0) for o in board.get("organizations", [])}
    done = load()
    out = []
    for c in companies:
        ats = c.get("ats") or {}
        url = ats.get("ref") or ats.get("url") or c.get("careers_url")
        # a page on file, of the kind nothing can enumerate, producing nothing
        if ats.get("type") != "html" or not url:
            continue
        if live.get(c["id"]):
            continue          # it already yields postings; leave it alone
        if f"read:{c['id']}" in done:
            continue
        out.append({
            "kind": "read",
            "key": f"read:{c['id']}",
            "id": c["id"],
            "name": c["name"],
            "url": url,
            "website": c.get("website") or "",
            "sector": c.get("sector"),
            "why_here": "a careers page is on file and no fetcher can enumerate it",
        })
    out.sort(key=lambda r: r["name"])
    return out[:limit] if limit else out


NAV_CHROME = {"careers", "jobs", "open positions", "all jobs", "search",
              "solutions", "challenges", "cookie preferences", "privacy",
              "about", "contact", "home", "apply", "apply now", "learn more",
              "view all", "see all openings", "benefits", "culture", "life at",
              "our team", "join us", "talent community", "sign in", "log in"}


def check_read(p: dict) -> str | None:
    """Refuse a read that looks like it scraped the navigation."""
    rows = p.get("postings")
    if p.get("confidence") == "unsure" or p.get("none_found"):
        # "I opened it and there are no jobs" is a real answer, but it is NOT
        # evidence of absence - the page may be a widget that failed to draw.
        # It is recorded as "read produced nothing", never as "not hiring".
        return None
    if not isinstance(rows, list) or not rows:
        return "either return postings, or say none_found with a reason"
    seen = set()
    for r in rows:
        if not isinstance(r, dict):
            return "each posting must be an object"
        title = (r.get("title") or "").strip()
        if len(title) < 3:
            return "a posting needs a title"
        if title.lower() in NAV_CHROME:
            return f"{title!r} is navigation, not a job"
        if title.lower() in seen:
            return f"{title!r} appears twice - that is the page repeating itself"
        seen.add(title.lower())
        url = (r.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            return f"posting url must be absolute, got {url!r}"
    if len(rows) > 400:
        return "over 400 rows from one page reads like a crawl, not a read"
    return None


# ---------------------------------------------------------------- intake

def check_bucket(p: dict, schema: dict) -> str | None:
    """Refuse a proposal before it can waste a person's attention."""
    sec, cat = p.get("sector"), p.get("category")
    conf = p.get("confidence")
    if conf not in CONFIDENCE:
        return f"confidence must be one of {CONFIDENCE}"
    why = (p.get("why") or "").strip()
    if conf == "unsure":
        # an unsure proposal is a legitimate and useful answer, but it must
        # not smuggle in a placement
        return None if not sec else "an unsure proposal must not name a sector"
    if not sec or not cat:
        return "a confident proposal must name a sector and a category"
    if sec not in schema:
        return f"unknown sector {sec!r}"
    if cat not in schema[sec]:
        return f"{cat!r} is not a category of {sec!r}"
    if cat == "Suppliers & Services":
        return "that is the bucket these are being moved OUT of"
    if len(why) < 25:
        return "say why, in a sentence a person can check"
    # The tell of a helpful guess: maximum confidence, minimum evidence.
    if conf == "high" and not (p.get("evidence") or "").strip():
        return "high confidence needs a quote from the description or site"
    return None


def ingest(kind: str, proposals: list[dict], model: str = "") -> dict:
    """Store proposals, refusing the malformed. Returns a small report."""
    if kind not in KINDS:
        raise ValueError(f"unknown agent kind {kind}")
    schema = _schema()
    store = load()
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    kept, refused = 0, []
    for p in proposals:
        key = p.get("key") or f"{kind}:{p.get('id')}"
        bad = (check_bucket(p, schema) if kind == "bucket"
               else check_read(p) if kind == "read" else None)
        if bad:
            refused.append({"key": key, "why": bad})
            continue
        store[key] = {
            "kind": kind,
            "id": p.get("id"),
            "name": p.get("name"),
            "sector": p.get("sector"),
            "category": p.get("category"),
            "confidence": p.get("confidence"),
            "why": (p.get("why") or "").strip(),
            "evidence": (p.get("evidence") or "").strip(),
            # the brief is stored WITH the answer: a label is useless for
            # teaching anything later unless you know what was in front of
            # the agent when it answered
            "postings": p.get("postings") or None,
            "none_found": bool(p.get("none_found")),
            "saw": p.get("saw") or {},
            "by": model or "agent",
            "at": now,
            "status": "pending",
        }
        kept += 1
    save(store)
    return {"kept": kept, "refused": refused, "total": len(store)}


def summary() -> dict:
    store = load()
    out = {"pending": 0, "accepted": 0, "rejected": 0, "unsure": 0, "total": len(store)}
    for p in store.values():
        out[p.get("status", "pending")] = out.get(p.get("status", "pending"), 0) + 1
        if p.get("confidence") == "unsure":
            out["unsure"] += 1
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["brief", "status", "show"])
    ap.add_argument("--kind", default="bucket", choices=KINDS)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    if a.command == "brief":
        briefs = {"bucket": brief_bucket, "read": brief_read}[a.kind](a.limit)
        print(json.dumps(briefs, indent=1))
        return 0
    if a.command == "status":
        s = summary()
        print(f"{s['total']} proposal(s): {s['pending']} pending, "
              f"{s['accepted']} accepted, {s['rejected']} rejected "
              f"({s['unsure']} said unsure)")
        return 0
    for key, p in sorted(load().items()):
        mark = {"pending": "?", "accepted": "+", "rejected": "-"}.get(p["status"], "?")
        print(f"{mark} {p['name']}  ->  {p.get('sector') or 'UNSURE'}"
              f"{' / ' + p['category'] if p.get('category') else ''}"
              f"  [{p['confidence']}]")
        if p.get("why"):
            print(f"    {p['why'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
