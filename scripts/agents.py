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
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import roles                                                    # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORE = DATA / "agent_proposals.json"

# profile, news and claim are declared ahead of their appliers so the queue
# can show them; proposal_rulings refuses to land one until the applier
# exists, by name, rather than raising inside a request handler.
KINDS = ("bucket", "read", "card", "board", "rival", "profile", "news",
         "claim", "family", "fact", "where", "buyer")
CONFIDENCE = ("high", "medium", "low", "unsure")


def _schema() -> dict:
    s = json.loads((DATA / "schema.json").read_text())
    return {x["name"]: [c for c in x["categories"] if c != "Suppliers & Services"]
            for x in s["sectors"]}


def load() -> dict:
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def save(store: dict, action: str = "agent-proposals", why: str = "",
         by: str = "agent", force: bool = False) -> str | None:
    """Write the store THROUGH THE JOURNAL. Returns a refusal or None.

    This used to be a bare write_text. CLAUDE.md heads a section "Every admin
    write is reversible" and this file was one of the writers that made that
    false: a ruling stamped onto a proposal here had no before-image and no
    --undo. save_decisions journals it like every other decision file, and
    brings BLAST and the runaway guard to it for the first time.
    """
    import admin
    return admin.save_decisions("agent_proposals.json", store, action,
                                why=why, by=by, force=force)


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


CANDIDATES = "conference_intake/govtech_candidates.json"


def brief_card(limit: int | None = None) -> list[dict]:
    """One brief per researched candidate nobody has ruled on.

    772 names came off conference floors with a website and a one-line
    vertical, and `card` has been a declared kind with no applier since it was
    written - so every one of them has sat undecided. They are not the
    supplier pile: CLAUDE.md already ruled that 2,776 of the 4,745 suppliers
    are ANSWERS rather than backlog, and a filter run over the undecided 1,969
    found no product vendor misfiled among them. This is the 772 that were
    researched FOR this board and never let in.

    THE BRIEF IS THIN ON PURPOSE AND SAYS SO. Every one of these carries a
    null description - the vertical line is all the research produced. A model
    handed a name, a website and eight words cannot honestly write the
    one-line description `promote_candidates` needs, so the brief tells it to
    answer unsure rather than fill the field, and check_card refuses a govtech
    verdict whose description is missing or too short. scout.py fetches the
    site for the ones worth the request; this brief does not, because 772
    fetches to answer a question most of these settle without one is the
    wrong order.
    """
    path = DATA / CANDIDATES
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    if isinstance(rows, dict):
        rows = list(rows.values())
    done = load()
    known = {(c.get("website") or "").lower() for c in
             json.loads((DATA / "companies.json").read_text())}
    out = []
    for r in rows:
        if not isinstance(r, dict) or not (r.get("name") or "").strip():
            continue
        key = f"card:{_pf_key(r['name']) or r['name']}"
        if key in done:
            continue
        # ALREADY ON THE BOARD IS NOT A QUESTION, and asking it wastes a row.
        if (r.get("website") or "").lower() in known and r.get("website"):
            continue
        out.append({
            "kind": "card", "key": key, "id": r["name"], "name": r["name"],
            "website": r.get("website") or "",
            "vertical": r.get("vertical") or "",
            "description": r.get("description") or None,
            "source_event": r.get("source_event") or "",
            "verdicts": {
                "govtech": "sells a product to state or local government; "
                           "needs a sector, a category and a one-line "
                           "description or it cannot be let in",
                "supplier": "sells to government but is not a technology "
                            "product vendor - services, hardware, consulting",
                "not_this_board": "not a government seller at all",
            },
            "note": "the description is null for every one of these. If the "
                    "name, the website and the vertical do not settle it, "
                    "answer unsure - do not write a description you cannot "
                    "read off something.",
        })
        if limit and len(out) >= limit:
            break
    return out


def brief_fact(limit: int | None = None, field: str | None = None) -> list[dict]:
    """One brief per company missing a founding year or a location.

    447 rows have no founding year and 323 have no location, and the pages
    that would state them are ALREADY ON DISK - the profile crawl fetched
    every company's homepage and about pages months of work ago. Nothing has
    read them for this. That is the cheapest unopened box in the repository.

    ONE FIELD PER BRIEF, not both, because they refuse differently: a year is
    a number that must appear in the quote, a location is a city that must.
    Answering two questions in one proposal means one refusal takes down a
    correct answer with it.
    """
    import fetch_profiles as fp
    companies = json.loads((DATA / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = list(companies.values())
    idx = fp.index()
    done = load()
    fields = [field] if field else list(FACT_FIELDS)
    out = []
    for c in companies:
        cid = c.get("id")
        if not cid:
            continue
        e = idx.get(cid)
        if not e or e.get("unread"):
            continue                      # nothing to read from, honestly
        for f in fields:
            if c.get(f):
                continue                  # already on file
            key = f"fact:{f}:{cid}"
            if key in done:
                continue
            rec = fp.load(cid)
            if not rec:
                continue
            pages = [pg for pg in (rec.get("about") or []) if pg.get("text")][:PROFILE_PAGES]
            if not pages:
                continue
            out.append({
                "kind": "fact", "key": key, "id": cid, "name": c.get("name"),
                "field": f, "asking": FACT_FIELDS[f],
                "website": c.get("website") or "",
                "pages": [{"url": pg["url"], "sha": pg.get("sha"),
                           "text": pg["text"][:PROFILE_PAGE_CHARS]}
                          for pg in fp.dechrome(pages)],
                "rules": {
                    "value": ("a four-digit year" if f == "year_founded"
                              else "'City, ST' for the US, else 'City, Country'"),
                    "quote": "verbatim from one of these pages, 20+ characters, "
                             "and it must CONTAIN the value itself",
                    "unsure": "the right answer when the pages do not state it",
                },
            })
            if limit and len(out) >= limit:
                return out
    return out


BOARD_PILES = {
    "unread": "a careers page is wired as `html` and reads zero titles",
    "unknown": "no board has ever been found, and none has been ruled absent",
}


def board_brief(company: dict, page: dict, pile: str) -> dict:
    """One brief for 'where does this company actually post?', from a fetched page.

    PURE, AND THE FETCH IS THE CALLER'S. scout.py does the IO and hands the
    page in, so this can be driven under fixtures and so the same brief shape
    covers both piles. CLAUDE.md's reason for building briefs here rather than
    letting an agent gather its own holds either way: an agent that fetches
    its own context fetches different context every run, and two proposals
    then cannot be compared.

    THE MODEL IS NOT ALLOWED TO CLOSE THIS QUESTION and the brief says so.
    check_board demands a row count and sample titles from a real fetch, which
    no reading of a careers page can produce - so the answer here is a
    CANDIDATE address, and scout verifies it before it is ever a proposal. And
    "no board exists" is not on the menu at all: a false absence is invisible
    and permanent, which is the one error this project refuses everywhere.
    """
    return {
        "kind": "board", "key": f"board:{company['id']}",
        "id": company["id"], "name": company.get("name"),
        "website": company.get("website") or "",
        "sector": company.get("sector"),
        "why_here": BOARD_PILES.get(pile, pile),
        "read_on": page.get("url"),
        # the sha travels so a ruling months later knows WHICH bytes were
        # read, without the bodies being in the repo - the same deal
        # brief_profile's `saw` strikes
        "page_sha": page.get("sha"),
        "page_text": (page.get("text") or "")[:PROFILE_PAGE_CHARS],
        "answers": {
            "board": "an ATS this project can fetch is named or linked on the "
                     "page. Give its type and the slug or url. Do NOT claim a "
                     "posting count - it will be fetched and checked.",
            "posts_at": "the openings are somewhere we cannot count - "
                        "LinkedIn, a parent's board, their own page. Name the "
                        "place and the link.",
            "unsure": "the page does not say. Always available, always cheap.",
        },
        "never": "Do not answer that no board exists. Nothing on one page "
                 "proves that, and a wrong 'none' hides a company for ever.",
    }


def brief_family(limit: int | None = None) -> list[dict]:
    """One brief per job title `roles.py` could not put in a family.

    756 distinct titles sit in `other`, which is the queue the admin calls
    Unclassified, and every one of them is a title the pattern rules already
    read and could not place. So the brief carries no guess: there is nothing
    to anchor to, which is the opposite of the bucket brief's problem.

    WHAT IT DOES CARRY IS THE COMPANY, and that is the whole reason this is
    judgment rather than a longer regex. "Implementation Consultant" is field
    work at a public-safety vendor and professional services at a consultancy;
    "Solutions Architect" is presales at one company and engineering at
    another. The title alone genuinely does not say, and a rule that guessed
    from the title alone would be wrong in both directions at once.

    HOW MANY POSTINGS WEAR THE TITLE is in the brief too, because it decides
    what the answer is worth. A title on 40 postings across 12 companies is
    not an override waiting to be typed - it is a rule `roles.py` is missing,
    and `judge.py` prints those clusters rather than filing 40 overrides that
    hide the gap.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin

    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    rows = admin.q_unclassified(companies, board)
    cos = {c.get("id"): c for c in companies if c.get("id")}
    wear: dict = {}
    for post in board.get("postings", []):
        if post.get("family") == "other":
            wear.setdefault(post["title"], []).append(post)
    done = load()
    out = []
    for r in rows:
        title = r["title"]
        if f"family:{title}" in done:
            continue          # already proposed; a re-run does not re-ask
        posts = wear.get(title, [])
        firms = []
        for post in posts:
            c = cos.get(post.get("company_id")) or {}
            label = c.get("name") or post.get("company") or ""
            sector = f"{c.get('sector')} / {c.get('category')}" if c.get("sector") else ""
            if label and (label, sector) not in firms:
                firms.append((label, sector))
        out.append({
            "kind": "family",
            "key": f"family:{title}",
            "id": title,
            "name": title,
            "title": title,
            "postings": len(posts),
            "at_companies": [{"name": n, "filed": f} for n, f in firms[:6]],
            "sample_location": r.get("location") or "",
            "url": r.get("url") or "",
            "families": {k: v for k, v in roles.LABEL.items() if k != "other"},
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


# ------------------------------------------------------------ rival v2 ----
# ONE COMPANY PER BRIEF, and only one that has an accepted write-up. v1 briefs
# a slice of a category and asks "who competes with these"; that is the right
# question when the roster is all you have. Once a company has a write-up
# built from its own pages, the better question is "who competes with THIS",
# with the description in front of the agent, and the answer is allowed to
# name companies this board has never heard of.
RIVAL_WEB_RULES = {
    "unit": "one company. Every add is a competitor of THIS company",
    "sources": "every add carries {url, quote} from a page that is not the "
               "company's own site and not LinkedIn. The quote is checked "
               "against the fetched page before anything is accepted",
    "searches": "record every search you run, with its query and its hits. An "
                "add with no search behind it is refused: the pipeline has no "
                "web, so an unsourced name is something you remembered",
    "keep": "a subset of `existing`. Not a second way to add",
    "drop": "an existing edge you think is wrong, with a reason. Proposed "
            "only; drops are not applied unless a person asks for them",
    "cap": "keep plus add may not exceed the edge cap (8)",
    "resolution": "give the name and the website. Do NOT give an id: the door "
                  "resolves a website to a board id, and an agent that "
                  "resolves ids invents them",
    "empty": "no competitors found is a real answer: add nothing and say so",
}
RIVAL_WEB_QUERIES = ("{name} competitors", "{name} vs", "{name} alternatives",
                     "{what} vendors for {buyer}")


def brief_rival_web(limit: int | None = None, category: str | None = None,
                    ids: list | None = None) -> list[dict]:
    """One brief per company that has an accepted write-up and no web pass yet.

    THE WRITE-UP IS THE POINT. Wyeth's rule, on the record: understand the
    company FIRST, and let that knowledge fuel the search. A brief that says
    only "Verkada, Public Safety/Police" produces a search for the category;
    one that carries three sourced paragraphs about what they sell and who
    buys it produces a search for the company.
    """
    companies = json.loads((DATA / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = list(companies.values())
    by_cat: dict = {}
    for c in companies:
        by_cat.setdefault((c.get("sector") or "?", c.get("category") or "?"),
                          []).append(c)
    done = load()
    out = []
    for c in sorted(companies, key=lambda x: (x.get("name") or "").lower()):
        cid = c.get("id")
        if not cid or (ids and cid not in ids):
            continue
        if category and c.get("category") != category:
            continue
        prof = c.get("profile")
        if not (isinstance(prof, dict) and prof.get("paragraphs")):
            continue                      # no write-up: nothing to search FROM
        key = f"rivweb:{cid}"
        if key in done:
            continue
        pool = by_cat.get((c.get("sector") or "?", c.get("category") or "?"), [])
        roster = [{"id": r["id"], "name": r["name"],
                   "sells": (r.get("description") or "").strip()}
                  for r in pool if r.get("id") and r.get("id") != cid][:120]
        existing = [e.get("id") if isinstance(e, dict) else e
                    for e in (c.get("competitors") or [])]
        what = (c.get("description") or "").strip()
        buyer = c.get("category") or "government agencies"
        out.append({
            "kind": "rival", "key": key, "id": cid, "name": c["name"],
            "website": c.get("website"),
            "sector": c.get("sector"), "category": c.get("category"),
            "write_up": prof.get("paragraphs"),
            "existing": [e for e in existing if e],
            "roster": roster,
            "web": {"max_searches": 6,
                    "queries": [q.format(name=c["name"], what=what[:60],
                                         buyer=buyer)
                                for q in RIVAL_WEB_QUERIES],
                    "record_every_search": True},
            "rules": RIVAL_WEB_RULES,
        })
        if limit and len(out) >= limit:
            break
    return out


# -------------------------------------------------------------- profile ----
# THE BRIEF IS THE COMPANY'S OWN PAGES AND NOTHING ELSE. 2,061 of 2,063
# company pages say the write-up is not on file, and the way to fill that
# with prose about a real firm's real customers is the exact failure this
# repo is built around: a model completing a pattern from what it remembers
# about a name. So the brief carries the text their own site says, cut to
# what a judge can hold, and a rules block the judge reads. Every sentence
# it returns has to quote a page in this brief, and check_profile holds the
# quote against the FULL stored text, so nothing here is invented and
# nothing is cut so tight that a true sentence cannot verify.
PROFILE_PAGE_CHARS = 7000
PROFILE_PAGES = 4            # homepage + up to three about-class pages
PROFILE_RULES = {
    "paragraphs": "2 to 3",
    "sentences_min": 3,
    "words": "80 to 240 in total",
    "sentence_max_words": 45,
    "provenance": "every sentence: {url, quote} where quote is verbatim from that url",
    # A QUOTE IS AN UNBROKEN RUN FROM ONE LINE, and this is the rule that
    # nine refused write-ups were never told. `dechrome` drops lines under
    # three words, so the brief shows two page lines as adjacent when the page
    # has a dropped line between them: aspiraconnect's brief read "Campground
    # and Lodging Reservations / Day Use and Parking Fulfillment" while the
    # page has "Customizable Websites" in the middle. The model copied what it
    # was given, and the door - checking the FULL page, correctly - refused a
    # true quote. 9 of 12 rule-4 refusals are that, and every one is our bug,
    # not the model's and not the door's.
    "quote_source": "a quote must be an unbroken run from ONE entry of a "
                    "page's `lines`. Never join two lines: lines that look "
                    "adjacent here may not be adjacent on the page.",
    "quote_min_chars": 20,
    "pull_quote": "optional, verbatim, at most 40 words, the company's own words",
    "forbidden": "first person; em-dashes; marketing adjectives; any customer, "
                 "number, date or product not on these pages",
    # BUILT FROM THE DOOR'S OWN LIST, never restated. A word added to
    # agents.MARKETING would otherwise start refusing write-ups written under
    # rules that never mentioned it - the alerts-vocabulary drift again.
    "marketing_words_refused": None,
    "absence": "answer confidence 'unsure' with no paragraphs if the pages do not "
               "say what they sell and to whom. That is a real answer.",
}


def _brief_pages(pages: list) -> list:
    """The page shape every brief written from a company's own site uses.

    LINES, NOT ONE STRING. See PROFILE_RULES["quote_source"]: a joined string
    invites a quote that spans a line dechrome removed, which the door then
    refuses although it is true - 9 of 12 rule-4 refusals were exactly that.
    It lives in one function because the scope brief is checked by a door with
    the same quote rule, and a shape written twice is two shapes that drift.
    """
    import fetch_profiles as fp
    return [{"url": pg["url"], "sha": pg.get("sha"),
             "lines": [ln for ln in
                       pg["text"][:PROFILE_PAGE_CHARS].split("\n")
                       if ln.strip()]}
            for pg in fp.dechrome(pages)]


def brief_profile(ids: list[str] | None = None, sector: str | None = None,
                  category: str | None = None, limit: int | None = None,
                  hiring_first: bool = True) -> list[dict]:
    """One brief per company with a readable site record and no profile yet."""
    import fetch_profiles as fp
    companies = json.loads((DATA / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = list(companies.values())
    try:
        board = json.loads((DATA / "board.json").read_text())
        open_by = {o["id"]: o.get("open_roles", 0) for o in board.get("organizations", [])}
    except Exception:
        open_by = {}
    idx = fp.index()
    done = load()
    want = set(ids or [])
    rows = []
    for c in companies:
        cid = c.get("id")
        if not cid or (want and cid not in want):
            continue
        if sector and c.get("sector") != sector:
            continue
        if category and c.get("category") != category:
            continue
        e = idx.get(cid)
        if not e or e.get("unread"):
            continue                      # nothing to write from, honestly
        if f"profile:{cid}" in done:
            continue                      # already proposed; a re-run does not re-ask
        prof = c.get("profile")
        if isinstance(prof, dict) and prof.get("paragraphs"):
            continue                      # already on file in the new shape
        rows.append(c)
    if hiring_first:
        rows.sort(key=lambda c: (-(open_by.get(c["id"], 0)), c["name"].lower()))
    out = []
    for c in rows:
        rec = fp.load(c["id"])
        if not rec:
            continue
        pages = [pg for pg in (rec.get("about") or []) if pg.get("text")][:PROFILE_PAGES]
        if not pages:
            continue
        out.append({
            "kind": "profile", "key": f"profile:{c['id']}",
            "id": c["id"], "name": c["name"], "website": c.get("website"),
            "sector": c.get("sector"), "category": c.get("category"),
            "description": (c.get("description") or "").strip(),
            "also_known_as": c.get("also_known_as") or [],
            "fetched_on": rec.get("fetched_on"),
            "pages": _brief_pages(pages),
            "rules": PROFILE_RULES,
        })
        if limit and len(out) >= limit:
            break
    return out


def brief_buyer(ids: list[str] | None = None, sector: str | None = None,
                category: str | None = None, limit: int | None = None,
                hiring_first: bool = True, include_answered: bool = False) -> list[dict]:
    """One brief per company whose buyer nobody has read off their own site.

    OFFERED INDEPENDENTLY OF THE WRITE-UP, which is the whole reason this is
    its own kind. 505 companies carry a landed write-up and no scope answer:
    their pages were fetched, read, quoted and published from, and the second
    question was never put. brief_profile can never offer them again because
    their profile proposal is accepted. This offers exactly them, and the
    1,173 nobody has written up either, off the same cached pages.

    ALREADY ANSWERED IS NOT RE-ASKED. A company carrying `sells_to_gov` was
    answered by the 2026-09-11 scope pass or by a person, and paying to
    reproduce an answer we are holding is the thing --recheck exists to avoid.
    --include-answered is there for the day a rule changes and the old answers
    need re-reading; it is never the default.
    """
    import fetch_profiles as fp
    companies = json.loads((DATA / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = list(companies.values())
    try:
        board = json.loads((DATA / "board.json").read_text())
        open_by = {o["id"]: o.get("open_roles", 0) for o in board.get("organizations", [])}
    except Exception:
        open_by = {}
    idx = fp.index()
    done = load()
    want = set(ids or [])
    rows = []
    for c in companies:
        cid = c.get("id")
        if not cid or (want and cid not in want):
            continue
        if sector and c.get("sector") != sector:
            continue
        if category and c.get("category") != category:
            continue
        e = idx.get(cid)
        if not e or e.get("unread"):
            continue                      # nothing to read from, honestly
        if f"buyer:{cid}" in done:
            continue                      # already proposed; a re-run does not re-ask
        if c.get("sells_to_gov") and not include_answered:
            continue                      # somebody already answered this one
        rows.append(c)
    if hiring_first:
        # HIRING FIRST IS NOT DECORATION HERE. A scope answer on a company
        # with no postings changes a recorded fact; on one with postings it is
        # what decides whether 548 live rows belong on this board.
        rows.sort(key=lambda c: (-(open_by.get(c["id"], 0)), c["name"].lower()))
    out = []
    for c in rows:
        rec = fp.load(c["id"])
        if not rec:
            continue
        pages = [pg for pg in (rec.get("about") or []) if pg.get("text")][:PROFILE_PAGES]
        if not pages:
            continue
        out.append({
            "kind": "buyer", "key": f"buyer:{c['id']}",
            "id": c["id"], "name": c["name"], "website": c.get("website"),
            "sector": c.get("sector"), "category": c.get("category"),
            "description": (c.get("description") or "").strip(),
            "also_known_as": c.get("also_known_as") or [],
            "fetched_on": rec.get("fetched_on"),
            "pages": _brief_pages(pages),
            "rules": BUYER_RULES,
        })
        if limit and len(out) >= limit:
            break
    return out


# ============================================================== profile ====
# WRITTEN BY A COUNCIL, ADOPTED BLIND. CLAUDE.md requires it for anything
# load-bearing, and this is the most load-bearing door in the repo: it decides
# what 2,000 public pages say about other people's companies. Two agents drafted
# it independently from one spec and a third ran both against a 34-case battery
# without knowing which was which. A scored 34/34, B 33/34.
#
# B'S ONE FAILURE IS THE REASON THE PROCEDURE EXISTS. Given the invented
# customer "New York Police Department", B trimmed the allowlisted words off
# the run - New, York, Police are a state and category words - and was left
# checking the lone token "Department", which really was on page two. It
# accepted a customer nobody has. A checks the run as a phrase and refuses it,
# naming it. One draft iterated would very likely have shipped B's version.
#
# Grafted from B: the refusal names the bare token, so a possessive reads
# "'Dallas' is not on any of their pages" rather than "'Dallas's'".
# Fixed in review: _PF_POINT was an empty string in A as delivered, so the
# decimal sentinel replaced nothing; it passed only because needle and corpus
# were transformed identically. Pinned to an explicit escape.

# ------------------------------------------------------------ profile door
#
# EVERY SENTENCE OF A PROFILE IS A CLAIM ABOUT SOMEBODY ELSE'S COMPANY on a
# public page, and the house rule is the whole design: never invent a fact to
# fill a field. So each sentence must point at a page we fetched and quote it,
# and every name and number in the prose must appear somewhere on the
# company's own pages. A sentence that says LESS than the site passes; a
# sentence that says one thing MORE than the site is refused, naming the thing.
# Marketing adjectives, first person and em-dashes are refused because they are
# what pasted copy looks like, and pasted copy is where invented facts hide.

MARKETING = ("leading", "industry-leading", "innovative", "cutting-edge",
             "world-class", "best-in-class", "state-of-the-art",
             "next-generation", "revolutionary", "seamless", "robust",
             "trusted", "premier", "unparalleled", "award-winning",
             "game-changing")

PROFILE_RULES["marketing_words_refused"] = list(MARKETING)

try:
    _PF_CONFIDENCE = CONFIDENCE          # the module's own vocabulary
except NameError:                        # this block imported on its own
    _PF_CONFIDENCE = ("high", "medium", "low", "unsure")

# Names that are never refused under rule 5. States and months are places and
# times, not facts about the company; the country words are how a US-market
# profile is written. Multi-word entries are matched as whole phrases only:
# 'New' and 'York' alone are not a state.
_PF_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming")
_PF_STATE_CODES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME "
                   "MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
                   "RI SC SD TN TX UT VT VA WA WV WI WY").split()
_PF_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December",
              "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept",
              "Oct", "Nov", "Dec")
# GEOGRAPHY IS CONTEXT, NOT A CLAIM, and the list was US-only. 59 of the 72
# refused write-ups died on rule 5 naming a bare token out of a country name:
# 'United', 'Kingdom', 'European', 'British'. A vendor selling into UK police
# forces cannot be described without those words, and refusing them is the
# door being too tight rather than the write-up being wrong.
#
# THESE GO IN AS PHRASES, NOT TOKENS, which is the whole point of the split.
# "United Kingdom" being allowlisted does not allowlist "United", so an
# invented customer "United Airlines" is still refused by name. That is B's
# one council failure, and it is why this list may only ever grow by phrase.
#
# WHAT STAYS REFUSED, deliberately: 'SOC' (SOC 2), 'Stock' (London Stock
# Exchange), and every other certification, standard or listing. Those are
# claims about the company that a reader would act on, and a compliance claim
# their own site does not make is exactly what this door is for.
_PF_COUNTRY = ("United States", "US", "U.S.", "USA", "U.S.A.", "American",
               "United Kingdom", "UK", "U.K.", "Britain", "British",
               "England", "Scotland", "Wales", "Northern Ireland", "Ireland",
               "Irish", "Europe", "European", "European Union", "EU",
               "Canada", "Canadian", "Australia", "Australian",
               "New Zealand", "Mexico", "Mexican", "Germany", "German",
               "France", "French", "Spain", "Spanish", "Netherlands", "Dutch",
               "India", "Indian", "Israel", "Israeli", "Japan", "Japanese",
               "Singapore", "North America", "North American",
               "Latin America", "EMEA", "APAC", "LATAM")

# Characters that make a true quote fail a naive `in`: a page's curly quote
# against the agent's straight one, a soft hyphen the browser never showed,
# a zero-width joiner from a CMS. Folded on BOTH sides before comparing.
_PF_INVISIBLE = "­​‌‍⁠﻿‎‏"
# A TRADEMARK SYMBOL IS NOT A LETTER, and NFKC disagrees: it maps ™ to the
# letters TM and ℠ to SM, so a page writing "SafeZone™" normalises to the
# single token "safezonetm" and a true sentence naming SafeZone is refused as
# an invented product. Dropped before NFKC on both sides, so needle and
# corpus agree.
_PF_MARKS = "™®℠©"
_PF_TRANS = str.maketrans({
    **{c: "'" for c in "‘’‚‛′ʼ"},         # ‘ ’ ‚ ‛ ′ ʼ
    **{c: '"' for c in "“”„‟″«»"},   # “ ” „ ‟ ″ « »
    **{c: "-" for c in "‐‑‒–—―⁃−"
                       "﹘﹣－"},       # ‐ ‑ ‒ – — ― ⁃ − small fullwidth
    **{c: None for c in _PF_INVISIBLE},
})
_PF_EDGE_PUNCT = "\"'()[]{}<>,;:!?.…*"
_PF_CLOSERS = ",;:!?)]}\"'’”…"
_PF_OPENERS = "\"'([{‘“"
# A period ends a name run ('drones. Dallas') unless it belongs to one of
# these ('Inc. Dallas' is still one name) or the token has inner periods.
_PF_ABBREV = {"inc", "co", "corp", "ltd", "llc", "plc", "st", "mt", "ft",
              "dept", "dr", "mr", "mrs", "ms", "jr", "sr", "no", "vs"}
_PF_BRIDGE = {"of", "&"}                 # 'State of Texas', 'Johnson & Johnson'
# A sentence-start word that never begins a name, so 'The Lemur weighs' is
# checked as Lemur while 'Dallas Police fly' is still checked as Dallas Police.
_PF_OPENING_WORDS = set("""
    the a an this that these those its their his her our your my some any each
    every all both no most many several few other another such one two three
    in on at by for from with without to of as into through across over under
    after before since until during within among between beyond near around
    along about against alongside like unlike per via and but or so yet if
    when while where although though because today now here there then also
    still once it they he she we you who what which founded based headquartered
    """.split())
_PF_POINT = "\ue000"               # private-use stand-in that keeps 2.5 a number
_PF_FIRST_PERSON = re.compile(r"\b(we|our|us|ours|we're|we've)\b", re.I)
_PF_MARKETING_RE = re.compile(
    r"\b(?:" + "|".join(r"[-\s]+".join(re.escape(part) for part in w.split("-"))
                        for w in MARKETING) + r")\b", re.I)
_PF_NUMBER = re.compile(r"\d+(?:\.\d+)?")


# A PAGE DECODED WITH THE WRONG CHARACTER SET is still that page. Eleven of
# the first 157 sites served UTF-8 without saying so, requests read them as
# Latin-1, and every curly apostrophe on them became three characters
# (a-circumflex and two control bytes) glued to the word before it, so the
# door refused "Dearborn Heights PD" as a customer not on the page. Runs
# shaped like that are re-read as the UTF-8 they were; nothing else moves.
_PF_MOJIBAKE = re.compile("[\u00c2\u00c3\u00e2][\u0080-\u00bf]{1,3}")


def _pf_demojibake(s: str) -> str:
    if "\u00e2" not in s and "\u00c3" not in s and "\u00c2" not in s:
        return s

    def fix(m: re.Match) -> str:
        try:
            return m.group(0).encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m.group(0)
    return _PF_MOJIBAKE.sub(fix, s)


def _pf_norm(s: str) -> str:
    """Casefold; fold curly quotes and every dash to ASCII; drop soft hyphens
    and zero-width characters; NFKC; one space per whitespace run.

    Folded before AND after NFKC: NFKC turns a small em-dash into an em-dash
    and a double prime into two primes, so a single pass in either order
    leaves one variant behind."""
    if not isinstance(s, str):
        return ""
    s = _pf_demojibake(s)
    s = s.translate(_PF_TRANS)
    for mark in _PF_MARKS:
        s = s.replace(mark, "")
    s = unicodedata.normalize("NFKC", s).translate(_PF_TRANS)
    return " ".join(s.casefold().split())


def _pf_loose(s: str) -> str:
    """Punctuation-blind view of normalised text for the rule-5 search:
    'Solutions, Inc.' and 'Solutions Inc' are one name and '1,200' is 1200.
    Apostrophes, hyphens, ampersands and decimal points stay because names
    and numbers carry them (Brinc's, Wi-Fi, AT&T, 2.5)."""
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    s = re.sub(r"(?<=\d)\.(?=\d)", _PF_POINT, s)
    s = re.sub(r"[^\w\s'&\-" + _PF_POINT + "]", " ", s)
    return " ".join(s.replace(_PF_POINT, ".").split())


def _pf_key(raw: str) -> str:
    """One token made comparable: normalised, edge punctuation off,
    possessive off, so Brinc's, "Brinc" and Brinc, all read brinc."""
    k = _pf_norm(raw).strip(_PF_EDGE_PUNCT + "-&/")
    return k[:-2] if k.endswith("'s") else k


def _pf_page(url: object, texts: dict[str, str]) -> str | None:
    """The stored page a url names, tolerating only a trailing slash."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    for cand in (u, u.rstrip("/"), u + "/"):
        if cand and cand in texts:
            return cand
    return None


def _pf_has(corpus: str, needle: str, numeric: bool = False) -> bool:
    """Whole-word presence in the loose corpus. A bare `in` would find Ion
    inside solution and Fort inside effort; a number must not sit inside a
    longer one (125 is not on a page that says 1250 or 125.5)."""
    needle = _pf_loose(_pf_norm(needle))
    if not needle:
        return True
    if numeric:
        # A NUMBER MUST NOT SIT INSIDE A LONGER ONE. 125 is not on a page that
        # says 1250 or 125.5, and "3.5" is not on a page that says "3.5.0" -
        # that last refusal is correct and stays: a version is not its prefix.
        pat = r"(?<!\d)(?<!\d\.)" + re.escape(needle) + r"(?!\d)(?!\.\d)"
    else:
        # A PLURAL IS THE SAME NAME, in either direction. FaceTec's page says
        # "3D FaceVectors" and a sentence naming FaceVector was refused as an
        # invented product; the reverse happens too when a page names one
        # thing and the prose names several. Only a trailing s or es, so the
        # needle still has to be present in full - this widens nothing else.
        stem = re.escape(needle)
        if needle.endswith("es") and len(needle) > 4:
            stem = re.escape(needle[:-2]) + r"(?:es)?"
        elif needle.endswith("s") and len(needle) > 3:
            stem = re.escape(needle[:-1]) + r"s?"
        else:
            stem = stem + r"(?:e?s)?"
        pat = r"(?<!\w)" + stem + r"(?!\w)"
    return re.search(pat, corpus) is not None


def _pf_allow(company: dict) -> tuple[set[str], set[tuple[str, ...]]]:
    """The rule-5 allowlist as single-token keys and whole-phrase key tuples.
    Name, aliases, sector and category are allowed whole and by token; the
    fixed lists are allowed whole only."""
    aka = company.get("also_known_as") or []
    if isinstance(aka, str):
        aka = [aka]
    own = [company.get("name") or "", company.get("sector") or "",
           company.get("category") or "", *aka]
    tokens: set[str] = set()
    phrases: set[tuple[str, ...]] = set()
    for by_token, items in ((True, own), (False, [*_PF_STATES, *_PF_STATE_CODES,
                                                  *_PF_MONTHS, *_PF_COUNTRY])):
        for item in items:
            keys = tuple(k for k in map(_pf_key, str(item).split()) if k)
            if not keys:
                continue
            if len(keys) == 1:
                tokens.add(keys[0])
            else:
                phrases.add(keys)
                if by_token:
                    tokens.update(keys)
    return tokens, phrases


def _pf_breaks(raw: str) -> bool:
    """Does this token close a name run? 'Dallas,' does; 'Inc.' does not."""
    tail = raw[-1:]
    if tail in _PF_CLOSERS:
        return True
    if tail == ".":
        core = raw.rstrip(".").casefold()
        return not ("." in core or core in _PF_ABBREV)
    return False


_PF_PAREN = re.compile(r"\([^()]{0,80}\)")
_PF_NEAR = 120     # chars; two names the page puts this close are one claim


def _pf_aside_free(pages_norm: dict) -> str:
    """The loose corpus with parentheticals removed. "Rocket City (Huntsville)
    HQ" carries the run "Rocket City HQ" with an aside in the middle, and a
    sentence naming Rocket City HQ was refused. Used for run checks only:
    the single-token check still sees Huntsville."""
    return "\n".join(_pf_loose(_PF_PAREN.sub(" ", t)) for t in pages_norm.values())


def _pf_near_bridged(tokens: list[str], corpus: str) -> bool:
    """A run bridged over 'of' or '&' whose whole phrase is not on the page
    still holds when the page puts its parts next to each other. The page
    says "Sheriff Greg Champagne, St. Charles Parish Sheriff" and the writer
    said "Greg Champagne of St. Charles Parish": a comma would have split
    the run and passed, so the bridge must not be a stricter test than a
    comma. Each segment must be on the page AND some occurrence of each
    must sit within _PF_NEAR chars of the next, so "Greg Champagne of
    Dallas County" is still refused on a page that names both, far apart."""
    segs, cur = [], []
    for tok in tokens:
        if tok.casefold() in _PF_BRIDGE:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    if len(segs) < 2:
        return False
    spots = []
    for seg in segs:
        needle = _pf_loose(_pf_norm(" ".join(seg)))
        if not needle:
            return False
        pat = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
        pos = [m.start() for m in re.finditer(pat, corpus)]
        if not pos:
            return False
        spots.append(pos[:200])
    for a, b in zip(spots, spots[1:]):
        if not any(abs(x - y) <= _PF_NEAR for x in a for y in b):
            return False
    return True


_PF_VERBAL = {"leading": ("to", "up")}   # 'leading to the arrest' is a verb


def _pf_marketing_ok(text: str, m: "re.Match", corpus: str, cased: str = "") -> bool:
    """Is this listed word innocent HERE? Two ways, both narrow.

    A NAME. "Autokey is included with Graykey Premier online licenses" was
    refused for 'Premier', which is half of a product Magnet Forensics sells
    and which their own page prints. A listed word capitalised mid-sentence
    and sitting in a capitalised run that the company's own pages carry is a
    name, not a claim. The run must be on the page: 'Premier Platform' with
    no such product is still refused.

    A VERB. "credits CellHawk analysis with leading to the arrest of a murder
    suspect" was refused for 'leading'. 'leading to' is a verb; the adjective
    the rule exists to stop is 'a leading provider'.
    """
    word = m.group(0)
    after = text[m.end():].split()
    key = word.casefold()
    if key in _PF_VERBAL and after and after[0].strip(_PF_EDGE_PUNCT).casefold() in _PF_VERBAL[key]:
        return True
    if not word[:1].isupper() or m.start() == 0:
        return False
    # the capitalised run this word belongs to, and whether the page has it
    raws = text.split()
    at = len(text[:m.start()].split())
    if at >= len(raws):
        return False
    i = j = at
    cap = lambda k: bool(raws[k][:1].isupper()) if 0 <= k < len(raws) else False
    while i > 0 and cap(i - 1):
        i -= 1
    while j + 1 < len(raws) and cap(j + 1):
        j += 1
    if j == i:
        return False                      # a lone capitalised adjective is not a name
    run = " ".join(raws[i:j + 1]).strip(_PF_EDGE_PUNCT)
    if _pf_has(corpus, run):
        return True
    # THE SAME GRAMMAR CAPITAL, in a second rule. "Adding Talon Premier
    # licences" walks back from Premier to a run that starts at the sentence's
    # first word, and "Adding Talon Premier" is on nobody's page while "Talon
    # Premier" is. Set the opener aside only when their own pages write it in
    # lower case, which is the same evidence rule 5 uses.
    if i == 0 and j - i >= 1 and cased:
        opener = _pf_key(raws[0])
        if opener and re.search(r"(?<!\w)" + re.escape(opener) + r"(?!\w)", cased):
            return _pf_has(corpus, " ".join(raws[1:j + 1]).strip(_PF_EDGE_PUNCT))
    return False


def _pf_entities(text: str, allow_tokens: set[str], allow_phrases: set[tuple[str, ...]],
                 corpus: str, corpus_aside_free: str = "", cased: str = "") -> str | None:
    """Rule 5: the first name or number in `text` that no page carries.

    (a) a capitalised token off the sentence start; (b) a run of capitalised
    tokens, checked as a phrase because 'New York Police' is a customer even
    when every word of it is innocent on its own; (c) a number. A run is
    exempt only when it IS an allowlisted phrase, never because its tokens
    are each allowed. The sentence-start token is exempt from (a) as ordinary
    capitalisation unless it is shouty (NYPD, McKinsey), which prose is not,
    and it opens a run unless it is a determiner-class word ('The Lemur')."""
    raws = text.split()
    keys = [_pf_key(r) for r in raws]
    caps = [k[:1].isalpha() and len(k) >= 2 and any(ch.isupper() for ch in r)
            for r, k in zip(raws, keys)]
    covered: set[int] = set()            # positions inside an allowed phrase
    for ph in allow_phrases:
        for i in range(len(keys) - len(ph) + 1):
            if tuple(keys[i:i + len(ph)]) == ph:
                covered.update(range(i, i + len(ph)))

    # (a) single tokens, by hyphen/slash part so 'Dallas-based' names Dallas
    for i, raw in enumerate(raws):
        if not caps[i] or i in covered:
            continue
        core = raw.strip(_PF_EDGE_PUNCT)
        if i == 0 and not any(ch.isupper() for ch in core[1:]):
            continue
        for part in re.split(r"[-/]", core):
            k = _pf_key(part)
            if (k[:1].isalpha() and len(k) >= 2 and any(ch.isupper() for ch in part)
                    and k not in allow_tokens and not _pf_has(corpus, k)):
                return re.sub(r"['’]s$", "", part.strip(_PF_EDGE_PUNCT))

    # (b) runs, bridged over 'of' and '&' so 'State of Texas' is one claim
    n, i = len(raws), 0
    while i < n:
        if not caps[i] or (i == 0 and keys[0] in _PF_OPENING_WORDS):
            i += 1
            continue
        j = i
        while not _pf_breaks(raws[j]):
            nxt = j + 1
            if (nxt + 1 < n and raws[nxt].casefold() in _PF_BRIDGE
                    and caps[nxt + 1] and raws[nxt + 1][:1] not in _PF_OPENERS):
                nxt += 1
            if nxt < n and caps[nxt] and raws[nxt][:1] not in _PF_OPENERS:
                j = nxt
            else:
                break
        j += 1
        if j - i >= 2:
            ks = tuple(k for k in keys[i:j] if k)
            span = " ".join(raws[i:j])
            # A SENTENCE'S FIRST WORD IS CAPITALISED BY GRAMMAR. "Adding
            # Mastery Connect puts assessment inside the courses" was refused
            # because the run read as "Adding Mastery Connect", and only
            # "Mastery Connect" is on Instructure's pages. Dropping the first
            # word wholesale would be worse - it would let "Dallas Police fly
            # the Lemur" through on the strength of "Police" - so the opening
            # word is set aside ONLY when the company's own pages use it in
            # lower case. That is evidence, not a word list: their pages say
            # "adding" as ordinary prose, and no page writes "dallas" that
            # way. The rest of the run is then checked on its own.
            if i == 0 and j - i >= 3:
                opener = _pf_key(raws[0])
                lower_on_page = bool(opener) and bool(re.search(
                    r"(?<!\w)" + re.escape(opener) + r"(?!\w)", cased))
                if lower_on_page:
                    # BELT AND BRACES, and worth saying which. An invented
                    # word inside the run is already caught by the
                    # single-token pass above, which runs first - so this
                    # cannot be the only thing standing between a guess and
                    # the page, and a mutation removing it changes no
                    # outcome the fixtures can reach. It is here for the run
                    # whose every word is separately allowlisted while the
                    # phrase itself is on nobody's page.
                    rest = " ".join(raws[1:j])
                    if _pf_has(corpus, rest) or (corpus_aside_free and
                                                 _pf_has(corpus_aside_free, rest)):
                        i = j
                        continue
            if (ks not in allow_phrases and not _pf_has(corpus, span)
                    and not _pf_has(corpus, " ".join(ks))
                    and not (corpus_aside_free and _pf_has(corpus_aside_free, span))
                    and not _pf_near_bridged(raws[i:j], corpus)):
                return span.strip(_PF_EDGE_PUNCT)
        i = j

    # (c) numbers: commas, $ and % off, unit letters off, so $125M asks for 125
    for num in _PF_NUMBER.findall(re.sub(r"(?<=\d),(?=\d)", "", text)):
        if not _pf_has(corpus, num, numeric=True):
            return num
    return None


def _pf_pull_quote(p: dict, texts: dict[str, str], pages: dict[str, str]) -> str | None:
    """Rules 2 and 9 for the optional pull quote."""
    q = p.get("quote")
    if not q:
        return None
    if not isinstance(q, dict) or not isinstance(q.get("text"), str) or not q["text"].strip():
        return "9. a pull quote must be an object with a text and a url."
    url = _pf_page(q.get("url"), texts)
    if url is None:
        return f"2. url {q.get('url')!r} is not one of this company's pages."
    qt = q["text"].strip()
    if len(qt.split()) > 40:
        return f"9. the pull quote runs over 40 words: {qt[:40]!r}."
    if _pf_norm(qt) not in pages[url]:
        return f"9. pull quote {qt[:40]!r} is not on {url}."
    return None


def check_profile(p: dict, texts: dict[str, str]) -> str | None:
    """Refuse a profile unless the company's own pages back every sentence:
    each sentence quotes the page it cites, and every name and number in the
    prose appears on those pages. Returns a numbered sentence a person can
    read, or None to accept.

    This is the door between an AI-written description and a public page,
    and the rule behind every check is the house rule: never invent a fact
    to fill a field. A wrong sentence about a real company's real customers
    is a defamation-shaped problem, so the door refuses by name and a person
    can see exactly what the model added."""
    if not isinstance(p, dict):
        return "1. a profile proposal must be an object."
    texts = {u: t for u, t in (texts or {}).items() if isinstance(u, str) and isinstance(t, str)}
    pages = {u: _pf_norm(t) for u, t in texts.items()}

    # 10. the confidence vocabulary, first because rule 1 branches on it
    conf = p.get("confidence")
    if conf not in _PF_CONFIDENCE:
        return f"10. confidence must be one of {_PF_CONFIDENCE}, got {conf!r}."

    # 1. an id, and unsure means NO paragraphs: that is a complete answer, and
    #    the only thing it could still carry to a page is a pull quote
    pid = p.get("id")
    if not isinstance(pid, str) or not pid.strip():
        return "1. a profile proposal must carry the id of the company it is about."
    paras = p.get("paragraphs") or []
    if conf == "unsure":
        if paras:
            return "1. an unsure proposal must not carry paragraphs; unsure is the whole answer."
        return _pf_pull_quote(p, texts, pages)
    if not isinstance(paras, list) or not paras:
        return ("1. a confident proposal must carry paragraphs; if the pages do "
                "not support a write-up, answer unsure.")

    # 3. shape: 2 to 3 paragraphs, 3+ sentences, 80 to 240 words, none over 45
    if not 2 <= len(paras) <= 3:
        return f"3. a profile is 2 to 3 paragraphs, got {len(paras)}."
    sentences: list[dict] = []
    for para in paras:
        if not isinstance(para, list) or not para:
            return "3. each paragraph must be a non-empty list of sentences."
        for s in para:
            if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip():
                return "3. each sentence must be an object with a text."
            sentences.append(s)
    if len(sentences) < 3:
        return f"3. a profile needs at least 3 sentences, got {len(sentences)}."
    counts = [len(s["text"].split()) for s in sentences]
    for s, w in zip(sentences, counts):
        if w > 45:
            return f"3. a sentence runs over 45 words ({w}): {s['text'].strip()[:40]!r}."
    if not 80 <= sum(counts) <= 240:
        return f"3. a profile is 80 to 240 words in total, got {sum(counts)}."

    company = p.get("company") if isinstance(p.get("company"), dict) else {}
    company = {"name": p.get("name"), "sector": p.get("sector"),
               "category": p.get("category"),
               "also_known_as": p.get("also_known_as"), **company}
    allow_tokens, allow_phrases = _pf_allow(company)
    # Rule 5 reads ALL pages, not the cited one: a customer named on the
    # about page may be cited from the homepage, and that is still their claim.
    corpus = "\n".join(_pf_loose(t) for t in pages.values())
    corpus_aside_free = _pf_aside_free(pages)
    # CASE SURVIVES HERE AND NOWHERE ELSE. `corpus` is casefolded, so it
    # cannot answer "do their pages write this word in lower case?" - which
    # is the evidence that a sentence-opening capital is grammar rather than
    # a name.
    cased = "\n".join(texts.values())

    for s in sentences:
        text = s["text"].strip()
        head = text[:40]
        # 2. only pages we fetched exist; anything else is a url from memory
        url = _pf_page(s.get("url"), texts)
        if url is None:
            return f"2. url {s.get('url')!r} is not one of this company's pages."
        # 6. house style, and the punctuation pasted copy arrives in; NFKC so
        #    a small em-dash cannot slip past as a different code point
        if "—" in unicodedata.normalize("NFKC", text) or "―" in text:
            return f"6. no em-dashes in a sentence (house style): {head!r}."
        # 7. first person is the company talking, not us describing it;
        #    an uppercase US is the country - AND A PRONOUN CAN BE SOMEBODY'S
        #    NAME. "Unite Us sells software" was refused for 'Us', which is
        #    half of the company being described, and "One franchise operator,
        #    We Rock the Spectrum" for 'We', which is the franchise's name.
        #    A company called Unite Us could never have had a write-up at all.
        #    The same evidence test the other rules use: capitalised, inside a
        #    capitalised run their own pages carry. A lower-case "we" is still
        #    the company talking.
        hit = next((m for m in _PF_FIRST_PERSON.finditer(text)
                    if m.group(0) != "US"
                    and not _pf_marketing_ok(text, m, corpus, cased)), None)
        if hit:
            return f"7. pasted marketing: first person {hit.group(0)!r} in {head!r}."
        # 8. adjectives nobody can check - but a WORD IS NOT AN ADJECTIVE
        #    JUST BECAUSE IT IS ON A LIST. "Graykey Premier" is a product
        #    Magnet Forensics sells and "leading to the arrest" is a verb;
        #    both were refused as marketing on their first real batch.
        m = next((m for m in _PF_MARKETING_RE.finditer(text)
                  if not _pf_marketing_ok(text, m, corpus, cased)), None)
        if m:
            return f"8. marketing word {m.group(0)!r} in {head!r}."
        # 4. the quote is the provenance; it must be on the page it cites
        quote = s.get("quote")
        if not isinstance(quote, str) or len(quote.strip()) < 20:
            return f"4. every sentence needs a quote of at least 20 characters from its page: {head!r}."
        if _pf_norm(quote) not in pages[url]:
            return f"4. quote {quote.strip()[:40]!r} is not on {url}."
        # 5. every name and number in the prose is on their pages, by name
        bad = _pf_entities(text, allow_tokens, allow_phrases, corpus,
                           corpus_aside_free, cased)
        if bad is not None:
            return f"5. {bad!r} is not on any of their pages."

    # 9. the pull quote is verbatim and short
    err = _pf_pull_quote(p, texts, pages)
    if err:
        return err

    # 10. THE EVIDENCE FIELD IS NOT REQUIRED HERE, and the reason is worth
    #     writing down rather than leaving as an absence.
    #
    #     It exists to catch "the tell of a helpful guess: maximum confidence,
    #     minimum evidence", which is real for a kind carrying ONE claim - a
    #     board, a read. A profile carries a claim per sentence, and rule 1
    #     has already refused a confident proposal with no paragraphs, rule 2
    #     every url that is not a page of theirs, and rule 4 every sentence
    #     whose quote is not verbatim on the page it cites. By the time
    #     execution reaches here, every sentence names a page and quotes it.
    #     A summary pointer adds nothing those three have not already proved,
    #     and requiring it refused 54 sound write-ups in one batch, none of
    #     which failed any other rule.
    #
    #     Written as a comment rather than an `if` that can never fire: dead
    #     code shaped like a guard is worse than no guard, because the next
    #     reader counts it as protection.
    return None


# ================================================================ scope ====
# TWO QUESTIONS, NOT ONE, AND THE SECOND ONE WAS NEVER ASKED. Every write-up
# answers "what does this company sell". 1,678 of 2,044 companies have no
# answer at all to "and who buys it" - 172 of them are on the board right now
# with 548 live postings. The pages that would settle it are already on disk:
# every one of those 1,678 has a cached site record, fetched for the write-up
# and read for one question when it could have been read for two.
#
# WHY A KIND OF ITS OWN AND NOT A FIELD ON A PROFILE. Because the two answers
# fail separately. A write-up refused at rule 5 for naming a customer the
# pages do not carry can still have read the buyer correctly off the same
# page, and a sound write-up can be paired with a scope answer that rests on
# nothing. One status for both would throw away whichever half was good. And
# 505 companies already HAVE a landed write-up and no scope answer - under a
# field-on-a-profile design they are unreachable for ever, because their
# profile proposal is already accepted and brief_profile will never offer
# them again.
#
# WHAT THE MODEL IS NOT ASKED. It is never asked for `sled_only`. That flag
# makes build_board drop every posting whose title does not name the public
# sector, so a wrong one deletes real jobs off a public board and leaves no
# mark. What the model answers is two verdicts with a quote behind each;
# buyer_sled_eligible() derives eligibility from them using the rule the
# 2026-09-11 scope pass measured, and a person still has to ask for it.

BUYER_RULES = {
    "question": "who buys this company's product, and is a government one of "
                "them? Answer from these pages only.",
    "sells_to_gov": "'yes' ONLY where a page names a government buyer: a city, "
                    "county, state, school district, transit agency, utility "
                    "district, police or fire department, or the public sector "
                    "named as a market they sell to. 'no' where the pages name "
                    "who buys and none of them is a government. 'unclear' where "
                    "the pages do not say who buys.",
    "buyer": "one sentence under 300 characters naming who these pages say "
             "buys, in the pages' own terms. Never a buyer the pages do not name.",
    "buyer_evidence": "a 'yes' needs buyer_url and buyer_quote: an unbroken "
                      "verbatim run of at least 20 characters from that page "
                      "naming the government buyer. A quote is the only thing "
                      "that can carry a 'yes'.",
    "names_other_buyers": "'yes' where a page names a buyer who is NOT a "
                          "government - and then other_url and other_quote must "
                          "prove it the same way. 'no' means exactly this: I "
                          "read these pages and not one of them names a "
                          "non-government buyer. 'unclear' where you cannot tell.",
    # THE RULE THE WHOLE ANSWER TURNS ON, and it is the house rule wearing a
    # different hat. A page that does not mention hospitals is not evidence
    # that they do not sell to hospitals.
    "absence": "these pages are not the whole company. Not finding a buyer is "
               "a fact about the pages, and 'unclear' is the answer for it. "
               "Never write 'no' to mean 'I did not see one'.",
    # STATED BECAUSE THE DOOR ENFORCES IT. Rule 7 refuses an 'unclear' verdict
    # at high confidence and nothing here said so until a drift guard was
    # written - which is the exact defect the profile rules already paid for:
    # an answer can obey every stated rule and still be refused, and the
    # refusal then reads as the model's fault.
    "confidence": "about the verdict, not about the reading. An 'unclear' "
                  "verdict cannot be high confidence - the verdict there is "
                  "that there is no verdict. Answer medium or low.",
    "quote_source": "a quote must be an unbroken run from ONE entry of a "
                    "page's `lines`. Never join two lines: lines that look "
                    "adjacent here may not be adjacent on the page.",
    "no_flags": "do not propose sled_only, or any flag, or any consequence. "
                "You answer two questions and quote the pages for each. What "
                "follows from the answers is not yours to decide.",
}

_BUYER_VERDICT = ("yes", "unclear", "no")
BUYER_MAX = 300


def buyer_sled_eligible(p: dict) -> bool:
    """Does this scope answer clear the bar sled_only was measured at?

    THE RULE IS apply_scope_pass's, NOT A NEW ONE. Ten agents read 76 sites on
    2026-09-11 and sled_only landed only where the verdict was yes, the site
    named no non-government buyer at all, and the agent said high. Anything
    softer was left for a person with the evidence in front of them. Restated
    here in one place rather than re-derived, because a rule written twice is
    two rules that drift - and this one silently removes postings.

    ELIGIBLE IS NOT LANDED. promote_profiles.land_buyer writes the flag only
    when a person passes --with-sled, having read the gate.
    """
    return (isinstance(p, dict)
            and p.get("sells_to_gov") == "yes"
            and p.get("names_other_buyers") == "no"
            and p.get("confidence") == "high")


def check_buyer(p: dict, texts: dict[str, str]) -> str | None:
    """Refuse a scope answer unless the company's own pages carry the verdict.

    The profile door's shape, for a claim that is one line instead of three
    paragraphs: a positive verdict has to quote the page that states it, and
    the quote has to be on that page. What is different is the treatment of
    absence, because the two verdicts are not symmetrical:

      'yes'     a claim about what a page says   -> a verbatim quote, required
      'no'      a claim about what these pages
                do NOT say                       -> a quote is welcome and
                                                    never required; it cannot
                                                    be proved by one
      'unclear' the honest answer when the pages
                do not say                       -> no quote at all

    A page scan never proves absence, so nothing here lets a 'no' assert one:
    it is recorded, it is shown in the gate, and it removes nothing. The one
    place an absence is load-bearing - names_other_buyers 'no', which is what
    sled_only rests on - is a claim about THESE PAGES, which the model read in
    full, and it still takes a person to act on.
    """
    if not isinstance(p, dict):
        return "1. a scope proposal must be an object."
    texts = {u: t for u, t in (texts or {}).items()
             if isinstance(u, str) and isinstance(t, str)}
    pages = {u: _pf_norm(t) for u, t in texts.items()}

    pid = p.get("id")
    if not isinstance(pid, str) or not pid.strip():
        return "1. a scope proposal must carry the id of the company it is about."

    conf = p.get("confidence")
    if conf not in _PF_CONFIDENCE:
        return f"2. confidence must be one of {_PF_CONFIDENCE}, got {conf!r}."

    verdict = p.get("sells_to_gov")
    if verdict not in _BUYER_VERDICT:
        return (f"3. sells_to_gov must be one of {_BUYER_VERDICT}, "
                f"got {verdict!r}.")
    other = p.get("names_other_buyers")
    if other not in _BUYER_VERDICT:
        return (f"3. names_other_buyers must be one of {_BUYER_VERDICT}, "
                f"got {other!r}.")

    buyer = p.get("buyer")
    if not isinstance(buyer, str) or len(buyer.strip()) < 10:
        return ("4. buyer must be one sentence saying who these pages say "
                "buys; if they do not say, that sentence says so.")
    if len(buyer.strip()) > BUYER_MAX:
        return (f"4. buyer is one sentence, at most {BUYER_MAX} characters, "
                f"got {len(buyer.strip())}.")

    # 5/6. A QUOTE OFFERED IS A QUOTE CHECKED, whatever the verdict. Requiring
    #      one only for 'yes' would leave a 'no' free to carry an invented
    #      sentence that nobody ever verified and a person later reads as
    #      evidence.
    def quoted(url_key: str, quote_key: str, rule: str,
               required: bool, what: str) -> str | None:
        q, u = p.get(quote_key), p.get(url_key)
        has = isinstance(q, str) and q.strip()
        if not has:
            if required:
                return (f"{rule}. {what} needs {quote_key}: a verbatim run of "
                        f"at least 20 characters from one of their pages.")
            return None
        if len(q.strip()) < 20:
            return (f"{rule}. {quote_key} must be at least 20 characters, "
                    f"got {len(q.strip())}: {q.strip()[:40]!r}.")
        page = _pf_page(u, texts)
        if page is None:
            return (f"{rule}. {url_key} {u!r} is not one of this company's pages.")
        if _pf_norm(q) not in pages[page]:
            return f"{rule}. {quote_key} {q.strip()[:40]!r} is not on {page}."
        return None

    err = quoted("buyer_url", "buyer_quote", "5", verdict == "yes",
                 "a 'yes' on sells_to_gov")
    if err:
        return err
    err = quoted("other_url", "other_quote", "6", other == "yes",
                 "a 'yes' on names_other_buyers")
    if err:
        return err

    # 7. AN ANSWER CANNOT BE BOTH SURE AND UNSURE. "high confidence that the
    #    pages do not say" is the tell of a guess dressed up: the confidence
    #    field is about the verdict, and the verdict here is that there is no
    #    verdict.
    if verdict == "unclear" and conf == "high":
        return ("7. an 'unclear' verdict cannot be high confidence; "
                "answer medium or low, or say what the pages do state.")

    # 8. THE MODEL DOES NOT PROPOSE CONSEQUENCES. sled_only is derived by
    #    buyer_sled_eligible from the two verdicts above and landed only by a
    #    person. A value arriving here would be dead on the floor - and a dead
    #    field shaped like a decision is what the next reader wires up.
    for flag in ("sled_only", "sled_only_why", "drop_postings"):
        if p.get(flag) is not None:
            return (f"8. a scope proposal does not carry {flag!r}. Answer the "
                    f"two questions and quote the pages; what follows from "
                    f"the answers is not yours to decide.")
    return None


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

CARD_VERDICTS = ("govtech", "supplier", "not_this_board")


def check_card(p: dict, schema: dict) -> str | None:
    """Refuse a candidate ruling that would add a wrong company, or lose a right one.

    THE TWO ERRORS ARE NOT SYMMETRIC AND THE DOOR IS NOT EITHER. A wrong
    "govtech" puts a company on a public board where a person can see it and
    say so. A wrong "not_this_board" hides a warm door for ever: the record
    stops appearing, nothing errors, no count looks odd, and nothing ever
    contradicts it. That is the same asymmetric error `scan_pagetext` refuses
    to make, and it is why a NEGATIVE verdict here is held to the same
    evidence bar as a positive one rather than being the cheap default.

    `unsure` is the cheap default, deliberately, and it costs one queue row.

    A govtech verdict must also arrive with the sector, the category and a
    one-line description, because `promote_candidates` needs all three and
    CLAUDE.md's rule for that path is explicit: it refuses to invent. A
    verdict that names none of them is not a ruling, it is a vote.
    """
    conf = p.get("confidence")
    if conf not in CONFIDENCE:
        return f"confidence must be one of {CONFIDENCE}"
    if not (p.get("name") or "").strip():
        return "a card proposal must name the candidate"
    verdict = p.get("verdict")
    why = (p.get("why") or "").strip()
    if conf == "unsure":
        return (None if not verdict else
                "an unsure proposal must not also carry a verdict")
    if verdict not in CARD_VERDICTS:
        return f"verdict must be one of {CARD_VERDICTS}"
    if len(why) < 25:
        return "say why, in a sentence a person can check"
    # BOTH DIRECTIONS NEED EVIDENCE. See the docstring: the negative is the
    # invisible one, so it does not get to be the lazy answer.
    if not (p.get("evidence") or "").strip():
        return ("every verdict needs evidence - the words that decided it. A "
                "wrong 'not this board' hides a company for ever and nothing "
                "ever contradicts it")
    if verdict != "govtech":
        return None
    sec, cat = p.get("sector"), p.get("category")
    if not sec or not cat:
        return "a govtech verdict must name a sector and a category"
    if sec not in schema:
        return f"unknown sector {sec!r}"
    if cat not in schema[sec]:
        return f"{cat!r} is not a category of {sec!r}"
    desc = (p.get("description") or "").strip()
    if len(desc) < 20:
        return ("a govtech verdict must carry a one-line description: what "
                "they sell and to whom")
    if len(desc) > 200:
        return f"the description is one line, got {len(desc)} characters"
    if _PF_FIRST_PERSON.search(desc):
        return "the description is ours, not theirs; no 'we' or 'our'"
    m = _PF_MARKETING_RE.search(desc)
    if m:
        return f"{m.group(0)!r} is marketing copy, not what they sell"
    return None


FACT_FIELDS = {"year_founded": "the year the company was founded",
               "location": "where the company is headquartered"}
_FACT_YEAR = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")


def check_fact(p: dict, texts: dict) -> str | None:
    """Refuse a founding year or a location that the company's own pages do not state.

    THE SAME DOOR AS check_profile, NARROWED TO ONE VALUE. A founding year is
    a published fact about somebody else's company, and CLAUDE.md's rule is
    absolute: never invent a fact to fill a field, no guessed founding year.
    A year that is merely PLAUSIBLE is exactly what a model produces when it
    wants to be helpful, and nothing downstream would ever contradict it -
    the field simply fills in and looks researched.

    So the value must sit inside a verbatim quote from a page we fetched:
    not near it, not implied by it, IN it. That single rule is what makes the
    difference between reading and remembering.
    """
    conf = p.get("confidence")
    if conf not in CONFIDENCE:
        return f"confidence must be one of {CONFIDENCE}"
    field = p.get("field")
    if field not in FACT_FIELDS:
        return f"field must be one of {tuple(FACT_FIELDS)}"
    if conf == "unsure":
        return (None if p.get("value") in (None, "") else
                "an unsure proposal must not also carry a value")
    value, url = p.get("value"), (p.get("url") or "").strip()
    quote = (p.get("quote") or "").strip()
    if value in (None, ""):
        return "a confident proposal must carry the value"
    texts = {u: t for u, t in (texts or {}).items()
             if isinstance(u, str) and isinstance(t, str)}
    if url not in texts:
        return (f"{url!r} is not a page we fetched for this company; a fact "
                f"has to come off a page on file")
    if len(quote) < 20:
        return "the quote must be at least 20 characters of their own words"
    if _pf_norm(quote) not in _pf_norm(texts[url]):
        return f"the quote is not on {url}"
    if field == "year_founded":
        try:
            year = int(str(value).strip())
        except (TypeError, ValueError):
            return f"a founding year is a number, got {value!r}"
        this_year = dt.date.today().year
        if not 1800 <= year <= this_year:
            return f"{year} is not a founding year between 1800 and {this_year}"
        # THE VALUE MUST BE IN THE QUOTE, not merely on the page. A quote that
        # supports a nearby year supports any year on that page.
        if str(year) not in _FACT_YEAR.findall(quote):
            return (f"{year} is not in the quote. The quote has to be the "
                    f"sentence that states the year, not one beside it")
        return None
    loc = str(value).strip()
    if "," not in loc or len(loc) < 4:
        return "a location reads 'City, ST' or 'City, Country'"
    city = loc.split(",")[0].strip()
    if not city:
        return "a location needs a city"
    if not _pf_has(_pf_norm(quote), city):
        return (f"{city!r} is not in the quote. The quote has to be the "
                f"sentence that states where they are")
    return None


def check_where(p: dict) -> str | None:
    """Refuse a 'they post here' record that would mislead a job seeker.

    `posts_at` is not an `ats` entry and the difference is load-bearing: `ats`
    means MONITORED, and filing LinkedIn there would make refresh try, fail,
    and record a zero. This says "the openings are here and we are not
    counting them", which is a different and honest claim.

    It is also the one board-shaped answer a model may give directly, because
    the dangerous direction is not available to it. A posts_at record claims
    no number and asserts no absence; the worst it can do is send somebody to
    the wrong page, which `posts_at.check` already refuses on its own terms.
    """
    import posts_at

    conf = p.get("confidence")
    if conf not in CONFIDENCE:
        return f"confidence must be one of {CONFIDENCE}"
    if conf == "unsure":
        return (None if not p.get("where") else
                "an unsure proposal must not also name a place")
    where = p.get("where")
    if not where:
        return "a confident proposal must name where they post"
    bad = posts_at.check(where, (p.get("url") or "").strip(),
                         (p.get("board_owner") or "").strip())
    if bad:
        return bad
    if len((p.get("why") or "").strip()) < 25:
        return "say why, in a sentence a person can check"
    if not (p.get("evidence") or "").strip().startswith(("http://", "https://")):
        return "evidence must be the page this was read on"
    # A BOARD WE COULD FETCH IS NOT A posts_at. Filing a Greenhouse address
    # here would record "the openings are there and we are not counting them"
    # about a board refresh.py could read nightly. The host list is admin's,
    # not restated: a second copy is the alerts-vocabulary drift, which is
    # silent and total.
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin

    url = (p.get("url") or "").lower()
    if any(h in url for h in admin.ATS_HOSTS):
        return ("that is a board a fetcher can read; propose it as a board so "
                "refresh.py counts it, not as a place we do not count")
    return None


def unclassified_titles() -> set:
    """The titles the queue is actually asking about. Read fresh, not cached.

    THE DOOR NEEDS THIS AND THE BRIEF IS NOT ENOUGH. `family_overrides.json`
    is keyed by exact title and `roles.family()` reads it ON TOP of the
    pattern rules, so an override for a title the patterns already place
    SILENTLY BEATS a working rule. Every other kind here proposes against a
    company that exists; this one can propose against a title nobody asked
    about, and the damage would be invisible - a correctly-classified title
    quietly reassigned, no error, no count out of place.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin

    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    return {r["title"] for r in admin.q_unclassified(companies, board)}


def check_family(p: dict, asked: set) -> str | None:
    """Refuse a family proposal before it can overwrite a working rule."""
    title = p.get("title") or p.get("id")
    fam = p.get("family")
    conf = p.get("confidence")
    if conf not in CONFIDENCE:
        return f"confidence must be one of {CONFIDENCE}"
    if not title:
        return "a family proposal names the exact title it is about"
    if title not in asked:
        # the dangerous direction, and the only one that is silent
        return (f"{title!r} is not in the unclassified queue; an override for "
                f"a title the rules already place would beat that rule")
    why = (p.get("why") or "").strip()
    if conf == "unsure":
        return None if not fam else "an unsure proposal must not name a family"
    if not fam:
        return "a confident proposal must name a family"
    if fam not in roles.LABEL:
        return f"unknown family {fam!r}"
    if fam == "other":
        # `other` is what the title already is. Restating it teaches nothing
        # and would write an override asserting the unknown as a decision.
        return "'other' is where it already sits; answer unsure instead"
    if len(why) < 25:
        return "say why, in a sentence a person can check"
    if conf == "high" and not (p.get("evidence") or "").strip():
        return "high confidence needs the words in the title or the posting"
    return None


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


# ============================================================ rival v2 ====
# V1 CHOOSES FROM A ROSTER. That is what makes it safe: an id that is not on
# the board cannot be invented, because the door only accepts ids it handed
# over. It is also what makes it thin - Verkada's real competitors are not
# confined to the companies this board happens to list, and a shortlist that
# silently means "of the ones we track" is a different claim than the page
# makes.
#
# V2 LETS AN AGENT LOOK OUTWARD, and every rule below exists because that
# removes the roster guard. What replaces it:
#
#   A NAME NEEDS A SOURCE. An `add` carries {url, quote} from a page that is
#   not the company's own careers site and not LinkedIn, and the quote is
#   verified against the fetched page by a SEPARATE networked step before
#   anything is accepted. Memory looks exactly like research until you ask it
#   for the link.
#
#   AN ADD WITHOUT A RECORDED SEARCH IS MEMORY. The routine has the web; the
#   pipeline does not. If a proposal names a competitor and cannot say which
#   search surfaced it, the model is completing a pattern about a company
#   name, which is the one failure this whole repo is built around.
#
#   RESOLUTION HAPPENS HERE, NEVER IN THE AGENT. The agent gives a name and a
#   website; the door turns that into a board id or marks it offboard. An
#   agent that resolves ids invents them.
#
#   THE CAP BINDS ON THE MERGED LIST. keep + add, not add alone, or the cap
#   is bypassed by keeping seven and adding seven.
LINKEDIN = ("linkedin.com", "www.linkedin.com")
SOURCE_QUOTE_MIN = 20


def _host(url: str) -> str:
    try:
        import urllib.parse as _up
        return (_up.urlsplit(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def check_rival_any(p: dict) -> str | None:
    """One kind, two engines, DISPATCHED rather than chained.

    v1 answers with a `rivals` list chosen off a roster; v2 answers with
    `keep`, `add` and `drop` and no roster at all. Running both doors over
    either proposal refuses the other engine's shape: v1 demands a rivals
    list, which a v2 answer correctly does not have.

    The shape decides. A proposal carrying any of keep/add/drop is v2.
    """
    if any(p.get(k) is not None for k in ("add", "keep", "drop")):
        return check_rival_web(p, _own_site(p))
    return check_rival(p)


def _own_site(p: dict) -> str | None:
    """The company's own website, so the door can refuse it as a source.

    A company saying who it competes with is marketing, not an independent
    reading, and the proposal itself carries the site the brief handed over.
    """
    site = p.get("website")
    if site:
        return site
    try:
        rows = json.loads((DATA / "companies.json").read_text())
        rows = rows if isinstance(rows, list) else list(rows.values())
        for c in rows:
            if c.get("id") == p.get("id"):
                return c.get("website")
    except Exception:
        pass
    return None


def check_rival_web(p: dict, own_site: str | None = None) -> str | None:
    """The v2 half: web-sourced adds and the drops beside them.

    Returns None when there is nothing v2 about the proposal, so a v1 answer
    passes through untouched and one door serves both.
    """
    adds = p.get("add")
    drops = p.get("drop")
    keep = p.get("keep")
    if adds is None and drops is None and keep is None:
        return None                       # a v1 proposal; nothing to add here

    searches = p.get("searches")
    if adds and not searches:
        return ("this proposal adds a competitor and records no search. The "
                "routine has the web and the pipeline does not, so an add with "
                "no search behind it is the model remembering a company name, "
                "which is the one thing this door exists to stop")
    if searches is not None and not isinstance(searches, list):
        return "searches must be a list of {q, hits}"
    for srch in (searches or []):
        if not isinstance(srch, dict) or not (srch.get("q") or "").strip():
            return "every recorded search must carry the query that was run"

    existing = {e for e in (p.get("existing") or [])}
    for k in (keep or []):
        if existing and k not in existing:
            return (f"{k!r} is kept but was not an existing edge. `keep` is a "
                    f"subset of what is already on file, not a second way to "
                    f"add")
    for d in (drops or []):
        if not isinstance(d, dict):
            return "each drop must be an object with an id and a why"
        if existing and d.get("id") not in existing:
            return (f"{d.get('id')!r} is dropped but is not an existing edge")
        if len((d.get("why") or "").strip()) < 15:
            return (f"the drop of {d.get('id')!r} carries no reason. Removing a "
                    f"name from a public page is the one edit nobody can see "
                    f"happen")

    seen_names = set()
    for a in (adds or []):
        if not isinstance(a, dict):
            return "each add must be an object"
        name = (a.get("name") or "").strip()
        if not name:
            return "an add with no name cannot be resolved to anything"
        if name.lower() in seen_names:
            return f"{name!r} is added twice"
        seen_names.add(name.lower())
        site = (a.get("website") or "").strip()
        if not site or _host(site) == "":
            return (f"the add {name!r} carries no website. A name without one "
                    f"cannot be resolved to a company or checked by a person")
        if len((a.get("why") or "").strip()) < 15:
            return (f"the edge to {name!r} carries no reason")
        src = a.get("source")
        if not isinstance(src, dict):
            return (f"{name!r} has no source. A competitor named with no page "
                    f"behind it is memory wearing research's clothes")
        url = (src.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return f"the source for {name!r} is not a fetchable http(s) url"
        host = _host(url)
        if host in LINKEDIN or host.endswith(".linkedin.com"):
            return (f"the source for {name!r} is LinkedIn, which is not a "
                    f"public page this can fetch and check")
        if own_site and host and host == _host(own_site):
            return (f"the source for {name!r} is the company's own site. A "
                    f"company saying who it competes with is a marketing "
                    f"claim, not an independent one")
        if len((src.get("quote") or "").strip()) < SOURCE_QUOTE_MIN:
            return (f"the source for {name!r} carries no quote, or one too "
                    f"short to verify against the page")

    total = len(keep or []) + len(adds or [])
    if total > EDGE_CAP:
        return (f"{total} edges after the merge is a category, not a "
                f"shortlist; the cap is {EDGE_CAP} and it binds on keep plus "
                f"add, not on add alone")
    return None


def _profile_texts(p: dict) -> dict:
    """{url: full page text} for the company a profile proposal is about."""
    try:
        import fetch_profiles as fp
        rec = fp.load(p.get("id") or "")
    except Exception:
        rec = None
    if not rec:
        return {}
    out = {}
    for pg in (rec.get("about") or []) + (rec.get("news") or []):
        if pg.get("url") and pg.get("text"):
            out[pg["url"]] = pg["text"]
    return out


def ingest(kind: str, proposals: list[dict], model: str = "") -> dict:
    """Store proposals, refusing the malformed. Returns a small report."""
    if kind not in KINDS:
        raise ValueError(f"unknown agent kind {kind}")
    schema = _schema()
    # read ONCE per ingest, not per proposal: a 120-title batch would
    # otherwise rebuild the queue 120 times off board.json
    asked = unclassified_titles() if kind == "family" else set()
    store = load()
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    kept, refused = 0, []
    for p in proposals:
        key = p.get("key") or f"{kind}:{p.get('id')}"
        bad = (check_bucket(p, schema) if kind == "bucket"
               else check_read(p) if kind == "read"
               else check_board(p) if kind == "board"
               else check_rival_any(p) if kind == "rival"
               # THE FULL STORED TEXT, not the trimmed brief: a quote from a
               # region dechrome cut must still verify, or the door refuses
               # true sentences and the gate review fills with false refusals.
               else check_profile(p, _profile_texts(p)) if kind == "profile"
               else check_family(p, asked) if kind == "family"
               else check_card(p, schema) if kind == "card"
               # THE SAME PAGES A PROFILE IS CHECKED AGAINST. A founding year
               # and a description are the same class of claim about somebody
               # else's company, so they are held to the same evidence.
               else check_fact(p, _profile_texts(p)) if kind == "fact"
               else check_where(p) if kind == "where"
               # THE SAME PAGES AGAIN, AND THE SAME REASON. A buyer sentence
               # is a claim about somebody else's company read off their own
               # site, so it is checked against the FULL stored text, not the
               # trimmed brief - a quote from a region dechrome cut is still
               # true and must still verify.
               else check_buyer(p, _profile_texts(p)) if kind == "buyer"
               else None)
        if bad:
            refused.append({"key": key, "why": bad})
            # A REFUSAL IS KEPT, NOT DROPPED. The gate review the owner asked
            # for ("door only, add some gate reviews") reads what the door
            # refused, by rule, so a rule that is too tight is visible. A
            # refusal that vanished at intake could never be reviewed, and a
            # door nobody can see being wrong is a door nobody fixes.
            store[key] = {"kind": kind, "id": p.get("id"), "name": p.get("name"),
                          "status": "refused", "refused_why": bad,
                          "confidence": p.get("confidence"),
                          "why": (p.get("why") or "").strip(),
                          "proposal": {k: v for k, v in p.items()
                                       if k not in ("roster", "pages")},
                          "by": model or "agent", "at": now}
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
            # a profile proposal is its paragraphs (each sentence with url and
            # quote) and its pull quote; the pages it saw travel as shas so a
            # ruling six months on knows which bytes the prose was checked
            # against without the bodies being in the repo
            "paragraphs": p.get("paragraphs"),
            # ONE STORED SHAPE FOR A QUOTE, {text, url}, whatever the kind
            # sent. A profile's pull quote already arrives as that dict; a
            # fact's arrives as a bare string beside its url, and the
            # dict-only rule that used to be here would have stored None -
            # silently dropping the ONE piece of evidence the whole fact
            # rests on, while the proposal still read as accepted.
            "quote": (p.get("quote") if isinstance(p.get("quote"), dict)
                      else {"text": p["quote"], "url": p.get("url")}
                      if isinstance(p.get("quote"), str) and p["quote"].strip()
                      else None),
            "saw": p.get("saw") or {},
            # a family proposal's whole answer: one title, one family
            "title": p.get("title"),
            "family": p.get("family"),
            # a card proposal: which of the three doors this candidate goes
            # through, and the one-line description a govtech verdict owes
            "verdict": p.get("verdict"),
            "description": p.get("description"),
            "website": p.get("website"),
            # a fact proposal: one field, one value, and the sentence on their
            # own page that states it
            "field": p.get("field"),
            "value": p.get("value"),
            "url": p.get("url"),
            # a where proposal: the place they post that we are not counting
            "where": p.get("where"),
            "board_owner": p.get("board_owner"),
            # a scope proposal: two verdicts, each with the page and the
            # sentence on it that carries the verdict. `buyer` is prose a
            # person reads; the two verdicts are what anything acts on.
            "buyer": (p.get("buyer") or "").strip() or None,
            "sells_to_gov": p.get("sells_to_gov"),
            "names_other_buyers": p.get("names_other_buyers"),
            "buyer_quote": p.get("buyer_quote"),
            "buyer_url": p.get("buyer_url"),
            "other_quote": p.get("other_quote"),
            "other_url": p.get("other_url"),
            # DERIVED HERE, ASSERTED NOWHERE. The model is refused if it sends
            # a flag (rule 8); this is the repo computing what the two verdicts
            # mean under the rule the scope pass measured. Stored so the gate
            # can show it and land_buyer does not re-derive it out of sight -
            # and it is still only eligibility, not the flag.
            "sled_eligible": (buyer_sled_eligible(p) if kind == "buyer"
                              else None),
            "by": model or "agent",
            "at": now,
            "status": "pending",
        }
        kept += 1
    # THE COUNT IS SHOWN, THEN THE WRITE IS FORCED, which is exactly the deal
    # journal.py asks for: it refuses a bulk write "unless the caller passes
    # force=True having shown a person the count". A 109-company category is
    # one ingest and trips the 25-record blast limit by construction, so
    # without this the whole batch is refused and the door's real findings -
    # four invented product names and a quote that is not on the page - are
    # thrown away with them. The count goes to stdout before the write, and
    # the journal still records it as one reversible entry.
    n = kept + len(refused)
    if n > 25:
        print(f"  ingesting {n} {kind} proposal(s) ({kept} through the door, "
              f"{len(refused)} refused) as one journal entry", file=sys.stderr)
    bad = save(store, "agent-ingest",
               why=f"{kept} {kind} proposal(s) from {model or 'agent'}, "
                   f"{len(refused)} refused at the door",
               by=model or "agent", force=n > 25)
    if bad:
        return {"kept": 0, "refused": refused + [{"key": "*", "why": bad}],
                "total": len(store)}
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
    ap.add_argument("command", choices=["brief", "ingest", "status", "show"])
    ap.add_argument("--kind", default="bucket", choices=KINDS)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sector")
    ap.add_argument("--category")
    ap.add_argument("--file", help="ingest: a JSON file of proposals")
    ap.add_argument("--model", default="", help="ingest: who produced them")
    a = ap.parse_args()

    if a.command == "brief":
        # THE DISPATCH USED TO BE A TWO-ENTRY DICT and `--kind rival` raised
        # KeyError, so the one kind that had actually run was the one the
        # CLI could not brief. Every kind with a brief is listed; a kind
        # without one says so instead of crashing.
        if a.kind == "bucket":
            briefs = brief_bucket(a.limit)
        elif a.kind == "read":
            briefs = brief_read(a.limit)
        elif a.kind == "rival":
            briefs = brief_rival(a.sector, a.category, a.limit)
        elif a.kind == "profile":
            briefs = brief_profile(sector=a.sector, category=a.category, limit=a.limit)
        elif a.kind == "family":
            briefs = brief_family(a.limit)
        elif a.kind == "card":
            briefs = brief_card(a.limit)
        elif a.kind == "fact":
            briefs = brief_fact(a.limit)
        elif a.kind == "buyer":
            briefs = brief_buyer(sector=a.sector, category=a.category, limit=a.limit)
        else:
            print(f"no brief builder for {a.kind!r} yet", file=sys.stderr)
            return 2
        print(json.dumps(briefs, indent=1))
        return 0
    if a.command == "ingest":
        if not a.file:
            ap.error("ingest needs --file")
        raw = json.loads(pathlib.Path(a.file).read_text())
        props = raw.get("proposals") if isinstance(raw, dict) else raw
        rep = ingest(a.kind, props or [], model=a.model)
        print(f"kept {rep['kept']}, refused {len(rep['refused'])}, "
              f"store holds {rep['total']}")
        for r in rep["refused"][:12]:
            print(f"  REFUSED {r['key']}: {r['why'][:100]}")
        return 0 if rep["kept"] or not props else 1
    if a.command == "status":
        s = summary()
        print(f"{s['total']} proposal(s): {s['pending']} pending, "
              f"{s['accepted']} accepted, {s['rejected']} rejected "
              f"({s['unsure']} said unsure)")
        return 0
    for key, p in sorted(load().items()):
        mark = {"pending": "?", "accepted": "+", "rejected": "-"}.get(p["status"], "?")
        print(f"{mark} {p.get('name')}  ->  {p.get('sector') or 'UNSURE'}"
              f"{' / ' + p['category'] if p.get('category') else ''}"
              f"  [{p.get('confidence')}]")
        if p.get("why"):
            print(f"    {p['why'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
