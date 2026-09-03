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
  rival    - who a buyer would put on the same shortlist        (built)
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORE = DATA / "agent_proposals.json"

KINDS = ("bucket", "read", "card", "board", "rival")
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



# ---------------------------------------------------------------- rival ----
# A SHORTLIST IS NOT A CATEGORY. The company page shipped a rail headed
# "Others in Police" listing Verkada, Palantir, Peregrine, Robin Radar and
# Brinc by open-role count. Every one of those sells to a police department
# and no two of them compete: cameras, a data platform, data integration,
# drone detection, drones. A category is the room they are all standing in.
# A competitor is someone a buyer would put on the same shortlist, and that
# is a product judgment no keyword count makes.
#
# THE UNIT IS THE CATEGORY, NOT THE COMPANY, for a reason that is worth
# stating: competition is symmetric. Judging one company at a time produces
# A-competes-with-B without B-competes-with-A, and the two answers disagree
# on the same page. One agent holding the whole roster can be checked for
# symmetry mechanically, and check_rival does.
#
# A LARGE CATEGORY IS SLICED BY ASSIGNMENT, NEVER BY ROSTER. Police holds 132
# companies. An agent assigned twenty of them still receives all 132 as
# candidates, because a roster cut in half cannot propose the edge that
# crosses the cut, and that missing edge is exactly the invisible false
# absence this project refuses everywhere else.
SLICE = 20


def brief_rival(sector: str | None = None, category: str | None = None,
                limit: int | None = None) -> list[dict]:
    """One brief per slice of a category. Roster whole, assignment partial."""
    companies = json.loads((DATA / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = list(companies.values())
    board = json.loads((DATA / "board.json").read_text())
    open_by = {o["id"]: o.get("open_roles", 0)
               for o in board.get("organizations", [])}

    pools: dict[tuple, list] = {}
    for c in companies:
        if not c.get("id") or not c.get("name"):
            continue
        pools.setdefault((c.get("sector") or "?", c.get("category") or "?"),
                         []).append(c)

    done = load()
    out = []
    for (sec, cat), pool in sorted(pools.items()):
        if sector and sec != sector:
            continue
        if category and cat != category:
            continue
        if len(pool) < 2:
            continue          # nobody to compete with is a real answer
        pool.sort(key=lambda c: c["name"].lower())
        # THE ROSTER EVERY SLICE SEES. One line each: what a shortlist is
        # decided on is what the company sells and who buys it.
        roster = [{"id": c["id"], "name": c["name"],
                   "sells": (c.get("description") or "").strip(),
                   "brands": [b.get("name") for b in (c.get("brands") or [])
                              if isinstance(b, dict) and b.get("name")] or None,
                   "open_roles": open_by.get(c["id"], 0)}
                  for c in pool]
        for i in range(0, len(pool), SLICE):
            chunk = pool[i:i + SLICE]
            key = f"rival:{sec}/{cat}#{i // SLICE}"
            if key in done:
                continue
            out.append({
                "kind": "rival", "key": key,
                "sector": sec, "category": cat,
                "assigned": [{"id": c["id"], "name": c["name"]} for c in chunk],
                "roster": roster,
                "roster_size": len(roster),
            })
    return out[:limit] if limit else out

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


def check_board(p: dict) -> str | None:
    """Refuse a board proposal that cannot be ruled on, or that is a guess.

    THE ONE THING THIS AGENT CAN GET CATASTROPHICALLY WRONG is naming a
    parent's board as a subsidiary's. CLAUDE.md: "Never point a company at its
    parent's job board. Several here were acquired and their careers pages
    redirect to the parent's Workday. Wiring that up would report a
    parent-company AE req as the subsidiary's, which is a false 'Yes'." That
    is not a judgement any fetch can make, so nothing here tries - the check
    instead refuses any proposal that does not carry the evidence a PERSON
    needs to make it.

    So a proposal must arrive with: the ats type this project can actually
    fetch, a slug, the row count that slug returned, and sample titles. A
    proposal with no row count was not verified, and an unverified slug is the
    exact thing CLAUDE.md says to never write - "always verify a slug with a
    real fetch before writing it".
    """
    kind, ref = (p.get("ats_type") or "").strip(), (p.get("ats_ref") or "").strip()
    if not kind or not ref:
        return "a board proposal must name an ats type and a ref"
    import ats as _ats
    if kind not in _ats.FETCHERS:
        return (f"{kind!r} is not a type refresh.py can fetch, so wiring it "
                f"would leave the company unreadable under a new name")
    if kind == "html":
        return "html is another page we cannot enumerate, not a board found"
    rows = p.get("rows")
    if not isinstance(rows, int) or rows < 1:
        return ("a board proposal must carry the row count its slug actually "
                "returned - an unverified slug is a guess, and slugs that "
                "look right land on other companies' boards")
    sample = p.get("sample")
    if not isinstance(sample, list) or not sample:
        return ("send sample titles from that board: they are the evidence a "
                "person rules ownership on, and ownership is the whole "
                "question here")
    if not (p.get("evidence") or "").strip().startswith(("http://", "https://")):
        return "evidence must be the careers page url the board was named on"
    if rows > 2000:
        return (f"{rows} postings is a parent's hub, not this company's board "
                f"- that is the false-Yes this check exists for")
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



# THE CAP IS THE WHOLE GUARD. An agent asked "who competes with Verkada" and
# handed 132 companies will, if it wants to be helpful, hand back most of the
# category - and that is precisely the rail this replaces, wearing a better
# word. A shortlist a buyer actually builds is two to six names. So a proposal
# naming more than EDGE_CAP rivals, or more than a third of the roster, is
# refused as a category listing rather than stored as a judgment.
#
# SYMMETRY IS RECORDED, NOT REQUIRED. An earlier draft of this door enforced
# it. That was wrong: Peregrine competes with Palantir and Palantir does not
# think about Peregrine, and forcing the reciprocal edge would have written a
# claim nobody made. Direction is kept, one-way edges are surfaced in the
# queue as information, and a person rules on them.
EDGE_CAP = 8


def check_rival(p: dict) -> str | None:
    """Refuse a shortlist that is really a category, or an invented name."""
    roster = {r.get("id") for r in (p.get("roster") or []) if r.get("id")}
    me = (p.get("id") or "").strip()
    if not me:
        return "a rival proposal must name the company it is about"
    if roster and me not in roster:
        return f"{me!r} is not in the roster this brief carried"

    rivals = p.get("rivals")
    if rivals is None:
        return ("a rival proposal must carry a rivals list, empty if none. "
                "Absence of the field is not the same answer as an empty one, "
                "and only one of them is a finding")
    if not isinstance(rivals, list):
        return "rivals must be a list"

    # AN EMPTY LIST IS A REAL ANSWER and must survive the door, but only when
    # it is asserted rather than defaulted to.
    if not rivals:
        if not p.get("none_found"):
            return ("an empty rivals list must set none_found, so that 'nobody "
                    "here competes with them' is a claim somebody made and not "
                    "a field that failed to fill")
        return None

    if len(rivals) > EDGE_CAP:
        return (f"{len(rivals)} rivals is a category, not a shortlist. A buyer "
                f"evaluating this company would not carry {len(rivals)} names "
                f"into the room; the cap is {EDGE_CAP}")
    if roster and len(rivals) > max(2, len(roster) // 3):
        return (f"{len(rivals)} rivals out of a {len(roster)}-company roster is "
                f"most of the category restated. That is the listing this "
                f"agent exists to replace")

    seen = set()
    for r in rivals:
        if not isinstance(r, dict):
            return "each rival must be an object with an id and a why"
        rid = (r.get("id") or "").strip()
        if not rid:
            return "a rival with no id cannot be linked to anything"
        if rid == me:
            return f"{me!r} is listed as its own competitor"
        if roster and rid not in roster:
            return (f"{rid!r} is not on the roster. An agent may only choose "
                    f"from the companies it was shown; a name from memory is "
                    f"the one thing this door exists to stop")
        if rid in seen:
            return f"{rid!r} is listed twice"
        seen.add(rid)
        why = (r.get("why") or "").strip()
        if len(why) < 15:
            return (f"the edge to {rid!r} carries no reason. An edge without "
                    f"one is a category listing with a better heading, which "
                    f"is the exact thing being replaced")

    if p.get("confidence") == "high" and not (p.get("evidence") or "").strip():
        return "a high-confidence shortlist must say what it rests on"
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
               else check_read(p) if kind == "read"
               else check_board(p) if kind == "board"
               else check_rival(p) if kind == "rival" else None)
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
            # A BOARD PROPOSAL'S WHOLE CASE. Stored beside the answer for the
            # same reason the brief is: a ruling that cannot be re-read is not
            # training data, and ownership is judged on these titles.
            "ats_type": p.get("ats_type"),
            "ats_ref": p.get("ats_ref"),
            "rows": p.get("rows"),
            "quota": p.get("quota"),
            "sample": p.get("sample") or None,
            "none_found": bool(p.get("none_found")),
            # a shortlist and the roster it was chosen from, stored together:
            # a ruling nobody can re-read is not a ruling
            "rivals": p.get("rivals"),
            "roster_size": p.get("roster_size"),
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
