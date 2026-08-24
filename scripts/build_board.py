#!/usr/bin/env python3
"""Build the SLED job board and market intelligence dataset.

refresh.py answers one question per company: is anyone hiring an AE? That is the
right question for a prospecting tracker and the wrong one for a job board,
because it discards every non-sales opening before anything is stored.

This keeps the whole board. For each company it fetches every posting, tags it
with a role family and a US determination, and writes:

  data/board.json     every open posting, plus per-company aggregates
  data/history/*.json dated snapshot of posting ids, for repost detection

The market-intelligence signal is the family mix. A company hiring twelve
engineers and no sellers is in a different phase than one hiring eight AEs, and
that difference is invisible if you only ever counted AEs.

The fetchers also hand back each posting's description text and, where a board
publishes one, a pay range. The pay range is kept. **The description text is
not**: see derived() for the two facts that survive it and why the prose itself
never reaches data/board.json.

Two units, kept apart everywhere: a POSTING is one advertisement and a row on
the site, an OPENING is one company advertising one title and what every count
here reports. Xplor's single Account Executive opening is 93 postings. See
opening_id() for why the counts use the second one.

  python scripts/build_board.py [--limit N] [--company id] [--dry-run]
                               [--details]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats            # noqa: E402
import roles          # noqa: E402

try:
    import render_fetch                            # noqa: E402  optional
except ImportError:                                # pragma: no cover
    render_fetch = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "history"

# sector -> buyer motion tier. 1 = municipal SaaS full-cycle, 2 = adjacent.
TIER = {"General Gov": 1, "Public Works": 1, "Parks & Rec": 1,
        "Public Safety": 2, "Transit & Parking": 2, "K-12 Schools": 2,
        # Municipal utilities and airports are public-sector buyers with the same
        # procurement motion, one step further from the city-hall relationship.
        "Utilities & Energy": 2, "Airports & Aviation": 2}

# Which applicant-tracking systems actually rank applicants. A resume seeded with
# exact keywords helps on the first two and does close to nothing on the third.
# Kept in lockstep with job-hunter's core/parsers.py RANKS_* sets: a company
# scored "hard" in one repo and "soft" in the other would condition a resume
# score on a reader the dock says does not exist. oracle/paylocity/rippling
# were added when discovery learned those ATS families.
RANKS_HARD = {"workday", "icims", "taleo", "successfactors", "adp", "paycom",
              "oracle"}
RANKS_SOFT = {"greenhouse", "lever", "ashby", "smartrecruiters", "jazzhr",
              "workable", "recruitee", "jobvite", "bamboohr", "breezy",
              "rippling", "paylocity"}


def ats_tier(kind: str | None) -> str:
    k = (kind or "").lower()
    return "hard" if k in RANKS_HARD else "soft" if k in RANKS_SOFT else "none"


def board_url(c: dict) -> str | None:
    """Where a person can go look themselves. Matters most where extraction fails."""
    a = c.get("ats") or {}
    kind, ref = a.get("type"), a.get("ref")
    if isinstance(ref, str) and ref.startswith("http"):
        return ref
    pat = {"greenhouse": "https://job-boards.greenhouse.io/{r}",
           "lever": "https://jobs.lever.co/{r}",
           "ashby": "https://jobs.ashbyhq.com/{r}",
           "breezy": "https://{r}.breezy.hr",
           "recruitee": "https://{r}.recruitee.com",
           "workable": "https://apply.workable.com/{r}",
           "jazzhr": "https://{r}.applytojob.com/apply",
           "bamboohr": "https://{r}.bamboohr.com/careers",
           "rippling": "https://ats.rippling.com/{r}/jobs",
           "smartrecruiters": "https://careers.smartrecruiters.com/{r}",
           "icims": "https://{r}.icims.com/jobs/search?ss=1"}
    if kind in pat and isinstance(ref, str):
        return pat[kind].format(r=ref)
    return c.get("website")


# Some companies belong here for a slice of what they do. Anthropic and OpenAI
# sell into state and local government, but their boards are 500 and 750
# postings of research and infrastructure, and importing all of it would bury
# the market this board is about under a horizontal company's headcount.
#
# This is NOT the same as Palantir or Verkada carrying engineering reqs. Those
# ARE govtech companies, and what they are building is real signal about where
# they are. A horizontal vendor's ML researcher is not.
#
# So: `sled_only: true` on a company keeps the roles that name the public sector
# and drops the rest. The company still appears with an honest count.
SLED_ROLE = re.compile(
    r"public[- ]sector|state (and|&) local|SLED|gov(ernment|tech)?|"
    r"civic|municipal|federal|public safety|K-?12|higher[- ]ed", re.I)

# ...but not another country's public sector. "Account Executive - Public
# Sector (ASEAN)" and "Account Director, Public Sector - Tokyo" both matched,
# and neither is this market. This board is US state and local.
# Kept by the filter, but not confidently in scope. These go to the admin's
# Scope review queue rather than being decided by a regex.
AMBIGUOUS_SCOPE = re.compile(
    r"federal|national security|intelligence community|\bDoD\b|"
    r"civilian agenc|\bCONUS\b|department of defen[cs]e", re.I)

NOT_OUR_GOV = re.compile(
    r"\bASEAN\b|\bEMEA\b|\bAPAC\b|\bLATAM\b|\bUK\b|Tokyo|Japan|Singapore|"
    r"Korea|India|Australia|Canada|Germany|France|Netherlands|Nordics|Ireland|"
    r"Brazil|Mexico|Dubai|\bUAE\b|Israel", re.I)


def opening_id(company_id: str, title: str) -> str:
    """The unit the board COUNTS: one company advertising one job title.

    Xplor Recreation posts "Account Executive" to 93 service areas across 15
    states while its whole board holds 7 other roles. Those are 93 real
    SmartRecruiters requisitions and they are not 93 jobs, and reporting them
    as 93 put a single advertisement third on a leaderboard of the biggest
    go-to-market pushes in the market. Civica goes further and lists ONE
    Workable requisition - same url - under six Australian cities.

    So a headline number counts openings and carries the spread next to it:
    "470 sellers wanted, advertised in 607 postings" is a sentence someone can
    check by hand. The per-location rows all stay - a reader who wants the
    Tucson requisition still gets exactly it - only the counting changes.

    This is also the id every shared link and saved role carried before
    posting_id() below started disambiguating them, which is why it stays the
    prefix of the new one.
    """
    return f"{company_id}::{title}"


def posting_id(company_id: str, title: str, url: str | None, location: str) -> str:
    """One id per requisition, stable across runs.

    company::title alone was the id and it is not unique: the 93 Xplor rows
    shared one, and the site resolves a role by `find(x => x.id === id)`, so
    92 of 93 readers who clicked a city opened a different city's job, and
    saving Goodyear and reopening it showed Tucson.

    url plus location is what actually separates two rows the board gave us.
    url alone does not: Workable answers six cities with one requisition url.

    Hashed rather than spliced in whole so the id stays short and there is no
    per-ATS requisition-id parsing to get wrong. Hashed from the posting's own
    content rather than from its position in the list, because an id that
    churns when a board reorders breaks every saved role and every shared link
    on every refresh, and turns the daily history diff into noise.
    """
    key = f"{url or ''}\n{location or ''}"
    disc = hashlib.blake2s(key.encode("utf-8"), digest_size=4).hexdigest()
    return f"{opening_id(company_id, title)}::{disc}"


def derived(row: dict) -> dict:
    """The facts we keep off a fetched row's description, and the text we drop.

    ats.py hands every row a `jd` (the description as plain text) and a `comp`
    (a pay range, from the board's own field or parsed out of that text). The
    jd is the input to this function and goes no further: 4,355 descriptions
    average ~6,000 characters, so shipping them would put roughly 25MB into a
    5.7MB file, and it would republish other companies' job-ad copy wholesale,
    which is not this project's to do. Facts out, prose in the bin.

    Two facts come back, in four keys.

    **comp** is the contract block verbatim - min, max, currency, period,
    source, raw. `raw` is the quote the figures came from and is what makes the
    number checkable by a person, so it is never dropped or rewritten.

    **comp_floor / comp_period** are the cheap filter, and they travel
    together on purpose. The tempting field is one annualised number so the
    site can sort every posting against every other, and it cannot be built
    honestly: turning $67.50/hour into a yearly figure means choosing hours per
    week and weeks per year, and the board would then be publishing a salary
    the employer never stated. So the number keeps the units it was stated in,
    and two rows only compare when their comp_period matches.

    comp_floor is `min` and never `max`. "Up to $200,000" states a ceiling and
    says nothing at all about the floor; reading 200000 as the floor would
    advertise a minimum nobody offered. A posting that stated only an upper
    bound therefore has a comp and a null comp_floor - which is the point of
    deriving this once here rather than letting each consumer re-derive it and
    get that case wrong.

    **jd_seen** is whether a description was actually read. "This employer did
    not state pay" and "we never read this posting" are different facts and a
    reader has to be able to tell them apart; without this field they look
    identical, and a board that silently reported the second as the first would
    be doing exactly what a false "None found" does to a warm door. The two are
    independent, not a sequence: Breezy publishes a pay range in the list
    response and no description at all, so polco's row is jd_seen false with a
    real comp on it.
    """
    # Both fields are type-checked rather than trusted. A fetcher bug must not
    # cost the whole board: everything below the fetch is one process building
    # one file, so an AttributeError on a malformed comp or jd would end the run
    # with no board written at all - which the site reads as nobody hiring
    # anywhere. A bad row costs its own pay range and nothing else.
    comp = row.get("comp")
    if not isinstance(comp, dict):
        comp = None
    jd = row.get("jd")
    out = {"jd_seen": bool(jd.strip()) if isinstance(jd, str) else False,
           "comp": comp}
    if comp:
        out["comp_floor"] = comp.get("min")
        out["comp_period"] = comp.get("period")
    return out


def phase(families: dict) -> str:
    """What the family mix says about where a company is."""
    total = sum(families.values())
    if total < 4:
        return "too few openings to read"
    gtm = families.get("gtm", 0)
    build = families.get("engineering", 0) + families.get("product", 0) + \
        families.get("data", 0)
    absorb = families.get("cs", 0) + families.get("field", 0)
    if build > gtm * 1.5:
        return "building: hiring mostly engineers and product"
    if gtm > build * 1.5:
        return "selling: a go-to-market push"
    if absorb > gtm:
        return "absorbing: delivery and support for customers already won"
    return "mixed: building and selling at once"


def count_openings(postings: list[dict], orgs: list[dict]) -> dict[str, list[dict]]:
    """Group rows into openings, stamp the spread on each row, count the orgs.

    Runs once, after dedup and the manual merge, over the final posting list -
    which is the only place all three sources are present at the same time.

    Returns opening_id -> its rows, so the caller can build totals from the
    same grouping the per-company numbers came from and the two cannot drift.
    """
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for p in postings:
        groups[p["opening_id"]].append(p)

    for rows in groups.values():
        # How widely this one opening is advertised. The site renders it as
        # "also in 92 other locations" next to a row, so a reader can see that
        # the Tucson requisition is one of 93 and not miss the other 92.
        # Both numbers, because a board that states no location at all gives
        # rows a spread with zero distinct locations in it.
        locations = len({(p.get("location") or "").strip() for p in rows
                         if (p.get("location") or "").strip()})
        for p in rows:
            p["opening_postings"] = len(rows)
            p["opening_locations"] = locations

    per_org: dict[str, dict] = {}
    for rows in groups.values():
        e = per_org.setdefault(rows[0]["company_id"],
                               {"open": 0, "postings": 0, "quota": 0,
                                "quota_postings": 0,
                                "families": collections.Counter()})
        e["open"] += 1
        e["postings"] += len(rows)
        # family and quota are read off the title, and an opening is one title,
        # so every row of a group agrees and the first row speaks for all.
        e["families"][rows[0].get("family") or "other"] += 1
        if rows[0].get("quota_carrying"):
            e["quota"] += 1
            e["quota_postings"] += len(rows)

    for o in orgs:
        e = per_org.get(o["id"])
        if e is None:                 # a company with nothing open right now
            continue
        o["open_roles"] = e["open"]
        o["open_postings"] = e["postings"]
        o["quota_roles"] = e["quota"]
        o["quota_postings"] = e["quota_postings"]
        o["families"] = dict(e["families"])
        o["phase"] = phase(e["families"])
    return groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--company")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-render", action="store_true",
                    help="skip the browser fallback even if Playwright is installed")
    ap.add_argument("--details", action="store_true",
                    help="read the posting page on the seven boards that only "
                         "publish the description there (~971 extra requests at "
                         "today's counts). Off by default: the daily run must "
                         "not get slower, and the boards that hand the "
                         "description over in the list response are read either "
                         "way. GOVTECH_DOCK_JD_DETAILS=1 does the same thing.")
    ap.add_argument("--write-partial", action="store_true",
                    help="allow a --limit/--company run to overwrite the full board")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--render-budget", type=float, default=900,
                    help="seconds to spend on the browser fallback in total "
                         "(default 900). Rendering is sequential and costs "
                         "~27s a page, so an uncapped render phase can run "
                         "longer than the entire parallel fetch.")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()

    # Only ever turned ON here. ats.py reads GOVTECH_DOCK_JD_DETAILS at import,
    # so writing False on the no-flag path would quietly override the env var
    # the owner set, and the flag he passed last would lose to the flag he
    # did not pass.
    if a.details:
        ats.FETCH_DETAILS = True

    companies = json.loads((DATA / "companies.json").read_text())
    # A person's rulings on whether a posting belongs on this board at all.
    # Keyed by posting id (company::title), so a role that reposts under the
    # same title keeps its ruling and is not asked about twice.
    scope_path = DATA / "scope_decisions.json"
    scope = json.loads(scope_path.read_text()) if scope_path.exists() else {}
    if a.company:
        companies = [c for c in companies if c["id"] == a.company]
    if a.limit:
        companies = companies[:a.limit]

    today = dt.date.today().isoformat()
    postings, orgs, unreadable, rendered = [], [], 0, 0

    # Fetch in parallel. Sequentially, 362 boards plus renders took 71 minutes,
    # which does not fit a daily job. Most of that is waiting on sockets, so
    # concurrency is the whole fix. Rendering stays sequential afterwards: it is
    # heavy, and only a handful of pages need it.
    def read_board(c):
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        if kind in (None, "unknown") or ref is None:
            return c, [], None, True, False       # nothing on file to read
        try:
            if kind == "html" and isinstance(ref, str):
                return c, ats.fetch_html_titles(ref), None, True, False
            return c, ats.fetch(c["ats"]), None, True, False
        except Exception as exc:
            return c, [], str(exc)[:60], True, kind == "html"

    render_started = time.monotonic()
    # Two companies pointing at ONE board is not two boards. It happens after an
    # acquisition: both the product and its acquirer end up with the parent's
    # careers URL, and the same postings get counted under both names. Twenty
    # refs were shared this way, double-counting 112 of 704 quota-carrying
    # roles - a 16% inflation of the single number this board exists to report.
    #
    # The board belongs to whoever the slug names. Everyone else sharing it is
    # marked and contributes nothing, rather than silently doubling the total.
    shared: dict[tuple, list] = collections.defaultdict(list)
    for c in companies:
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        if kind in (None, "unknown") or ref is None:
            continue
        shared[(kind, json.dumps(ref, sort_keys=True))].append(c)

    owns: dict[str, str] = {}          # company id -> id of the board's owner
    unowned: set[str] = set()          # holders the slug does not name
    for (kind, ref_json), group in shared.items():
        if len(group) < 2:
            continue
        ref = json.loads(ref_json)
        slug = ref if isinstance(ref, str) else " ".join(str(x) for x in ref)
        norm = lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())
        ns = norm(slug)

        def closeness(c):
            """How much of this company's name the slug accounts for.

            Taking the first name that merely CONTAINS the slug gave the Xplor
            board to "PerfectMind (Xplor Recreation)" over "Xplor Recreation",
            which is the company the slug is actually named after. The slug
            covering more of the name is the better claim.
            """
            n = norm(c["name"])
            if not n or not ns:
                return 0.0
            if n in ns or ns in n:
                return len(ns) / max(len(n), len(ns))
            return 0.0

        holder = max(group, key=closeness)
        unverified = closeness(holder) == 0
        if unverified:
            # Nobody is named by the slug. Three Catalis brands share
            # catalisgov.com and none of them IS Catalis, so whoever holds it
            # holds it arbitrarily. Keep the pick stable and say so, rather
            # than letting an arbitrary attribution look decided.
            holder = group[0]
            unowned.add(holder["id"])
        for c in group:
            if c["id"] != holder["id"]:
                owns[c["id"]] = holder["id"]

    fetched = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(read_board, companies), 1):
            fetched.append(res)
            if i % 250 == 0:
                # flush: redirected stdout is block-buffered, so an unflushed
                # progress line is invisible for the whole run. A 57-minute
                # rebuild looked hung when it was working fine.
                print(f"  fetched {i}/{len(companies)}...", flush=True)

    for c, jobs, err, enumerable, may_render in fetched:
        if c["id"] in owns:
            # Somebody else's board. Keep the company on the list with its real
            # state, and no postings: a shared board is one board.
            jobs, err = [], None
        kind = (c.get("ats") or {}).get("type")
        ref = (c.get("ats") or {}).get("ref")
        no_board = kind in (None, "unknown") or ref is None

        if err and may_render and not a.no_render and render_fetch is not None \
                and render_fetch.available() \
                and time.monotonic() - render_started < a.render_budget:
            try:
                # render_fetch reads the rendered DOM, not ats.fetch(), so its
                # titles have not been through plain() yet.
                jobs = ats.plain_rows(render_fetch.fetch_rendered(ref))
                err = None
                rendered += 1
            except Exception:
                jobs = []
            if rendered and rendered % 10 == 0:
                print(f"  rendered {rendered} board(s), "
                      f"{time.monotonic() - render_started:.0f}s spent", flush=True)
        if err and not jobs:
            if kind == "html":
                enumerable = False
                err = None
            else:
                unreadable += 1
        if not jobs and kind == "html" and not err:
            enumerable = False

        sled_only = bool(c.get("sled_only"))
        dropped_offtopic = 0
        pending = 0
        for j in jobs:
            title = (j.get("title") or "").strip()
            if roles.is_junk(title) or roles.is_evergreen(title):
                continue
            loc = j.get("location") or ""
            url = j.get("url") or board_url(c)
            oid = opening_id(c["id"], title)
            rid = posting_id(c["id"], title, url, loc)
            # Scope rulings are looked up by both ids, most specific first.
            # admin.py keys a new ruling by the posting id it saw on the board,
            # which is now per-requisition; every ruling made before that is
            # keyed company::title and is a judgement about the ROLE, so it
            # still applies to all 93 rows and nobody is asked 93 times.
            ruling = scope.get(rid)
            if ruling is None:
                ruling = scope.get(oid)
            scope_pending = False
            if sled_only or ruling:
                if ruling is not None:
                    # A person has already decided. Their ruling beats the
                    # pattern in both directions.
                    if not ruling.get("in_scope"):
                        dropped_offtopic += 1
                        continue
                elif not SLED_ROLE.search(title) or NOT_OUR_GOV.search(title):
                    dropped_offtopic += 1
                    continue
                elif AMBIGUOUS_SCOPE.search(title):
                    # Kept by the pattern, but the pattern is not sure. Federal
                    # is the live case: it is selling tech to government and it
                    # is not state and local, and only a person settles which
                    # of those this board is about.
                    pending += 1
                    scope_pending = True
            fam = roles.family(title)
            geo = roles.geography(loc, title)
            postings.append({
                "scope_pending": scope_pending or None,
                "id": rid,
                # what this row is one advertisement OF. Rows sharing it are
                # one opening; the site groups on this to count and to say
                # "also advertised in N other locations".
                "opening_id": oid,
                "company": c["name"], "company_id": c["id"],
                "title": title, "family": fam,
                "quota_carrying": roles.is_quota_carrying(title),
                "seniority": roles.seniority(title),
                # territory (what the role covers) and office (where the job
                # sits) are separate, filterable facts; states/region repeat
                # the territory half for older consumers.
                "territory": geo["territory"], "office": geo["office"],
                "states": geo["territory"]["states"],
                "region": geo["territory"]["region"],
                "work_mode": geo["work_mode"],
                "location": loc, "is_us": roles.is_us(loc, title),
                # pay and jd_seen, derived off j["jd"]. The text itself is not
                # copied into this dict and does not leave this loop.
                **derived(j),
                "url": url,
                "sector": c["sector"], "category": c["category"],
                # extra departments this vendor also sells into, so a
                # filter on Courts finds Tyler even though its primary
                # home is General Gov
                "also": c.get("also") or None,
                "first_seen": today, "source": "ats",
            })

        orgs.append({
            "id": c["id"], "name": c["name"], "sector": c["sector"],
            "category": c["category"], "also": c.get("also") or None,
            "location": c.get("location"),
            "year_founded": c.get("year_founded"), "description": c.get("description"),
            "website": c.get("website"), "board_url": board_url(c),
            "ats": kind, "ats_ranks": ats_tier(kind),
            "tier": TIER.get(c["sector"]),
            "vendor_type": c.get("vendor_type"), "govtech": c.get("govtech"),
            "parent": c.get("parent"), "ats_note": c.get("ats_note"),
            # Filled in by count_openings() once every posting exists. Counting
            # here counted rows, missed the manual merge below, and rescanned
            # the whole posting list once per company.
            "open_roles": 0, "open_postings": 0,
            "quota_roles": 0, "quota_postings": 0,
            "families": {}, "phase": phase({}),
            "unreadable": err,
            "sled_only": sled_only or None,
            "offtopic_dropped": dropped_offtopic or None,
            "shares_board_with": owns.get(c["id"]),
            "board_owner_unverified": c["id"] in unowned or None,
            # A company with nothing on file has not failed; it has never been
            # tried. Counting 4,137 of those as "unreadable" made a discovery
            # backlog look like a systemic fetch failure.
            "no_board_on_file": no_board,
            "enumerable": enumerable,
        })

    # Merge hand-checked findings. These come from companies the fetchers cannot
    # read at all, so an automated run must never delete them: absence from this
    # run means the fetcher still cannot see the company, not that the role closed.
    # Only `manual.py none` closes a manual posting.
    manual_path = DATA / "manual.json"
    manual_count = 0
    if manual_path.exists():
        man = json.loads(manual_path.read_text())
        checks = man.get("checks", {})
        for mp in man.get("postings", []):
            # manual.py keys a hand-captured row company::title, which names
            # the opening rather than the requisition. Re-key it the same way
            # a fetched row is keyed, so "one id, one row" holds across both
            # sources. No org counting here: count_openings() below sees these
            # rows too, and doing it twice double-counted them.
            # A captured row is a title read off a page a fetcher cannot
            # enumerate, so there is no description behind it and derived()
            # reads that correctly as jd_seen false. The dict is copied whole
            # from manual.json, so it is also the one path by which a `jd` key
            # could ever ride into the public file - derived() rebuilds the
            # pay fields from scratch and the pop removes the text itself.
            row = {**mp, "source": "manual", **derived(mp)}
            row.pop("jd", None)
            row["title"] = ats.plain(mp.get("title") or "")
            row["opening_id"] = opening_id(mp["company_id"], row["title"])
            row["id"] = posting_id(mp["company_id"], row["title"],
                                   mp.get("url"), mp.get("location") or "")
            postings.append(row)
            manual_count += 1
        for org in orgs:
            chk = checks.get(org["id"])
            if chk:
                org["checked_by_hand"] = chk.get("checked_on")

    # carry first_seen forward so a posting keeps its original date
    prev_path = DATA / "board.json"
    if prev_path.exists():
        prev, legacy = {}, {}
        for p in json.loads(prev_path.read_text()).get("postings", []):
            seen = p.get("first_seen")
            if not seen:
                continue
            if p["id"] not in prev or seen < prev[p["id"]]:
                prev[p["id"]] = seen
            # Rows written before ids carried a requisition discriminator have
            # id == company::title exactly. Without this fallback the very
            # first run under the new scheme matches nothing, resets every
            # first_seen to today, and the site reports 4,242 roles as posted
            # this morning. It retires itself: once a board has been written
            # with discriminated ids, no row takes this branch again.
            # The oldest date wins: the old id was shared by up to 93 rows, and
            # the opening has been open since the earliest of them appeared.
            if p["id"] == opening_id(p.get("company_id"), p.get("title")):
                if p["id"] not in legacy or seen < legacy[p["id"]]:
                    legacy[p["id"]] = seen
        for p in postings:
            was = prev.get(p["id"]) or legacy.get(p.get("opening_id"))
            if was:
                p["first_seen"] = was

    for mp in postings:
        if "territory" not in mp:            # manual entries predate these fields
            g = roles.geography(mp.get("location", ""), mp.get("title", ""))
            mp.update(seniority=roles.seniority(mp.get("title", "")),
                      territory=g["territory"], office=g["office"],
                      states=g["territory"]["states"],
                      region=g["territory"]["region"], work_mode=g["work_mode"])

    # Byte-identical duplicate rows are a fetcher stutter, not two jobs.
    # Rows that DIFFER - one title across 93 locations - are real and stay;
    # each now carries its own id, and posting_id() is derived from the url and
    # location, so two rows that would collide on an id are identical in every
    # other field too and one of them is dropped right here.
    unique, seen_rows = [], set()
    for mp in postings:
        key = json.dumps(mp, sort_keys=True)
        if key in seen_rows:
            continue
        seen_rows.add(key)
        unique.append(mp)
    if len(unique) != len(postings):
        print(f"  dropped {len(postings) - len(unique)} byte-identical duplicate posting rows")
    postings = unique

    groups = count_openings(postings, orgs)

    # Everything below counts OPENINGS - see opening_id(). The row counts are
    # still here, named *_postings, because "advertised in 607 postings" is the
    # sentence that makes 470 checkable.
    fam_totals = collections.Counter(rows[0].get("family") or "other"
                                     for rows in groups.values())
    sector_totals = collections.Counter(rows[0].get("sector")
                                        for rows in groups.values())
    # Where the pay numbers came from. "ats" is the board's own field, "text" is
    # salary.py reading it out of the description - a weaker claim, and the site
    # should be able to say which it is showing rather than presenting both as
    # equally settled.
    pay_source = collections.Counter(p["comp"]["source"] for p in postings
                                     if p.get("comp"))
    # which companies have a logo on file, and in what format. The page
    # needs the extension to build the src, and a manifest is cheaper than
    # 2,100 speculative requests that mostly 404.
    logos = {}
    ldir = ROOT / "assets" / "logos"
    if ldir.exists():
        for f in ldir.glob("*.*"):
            logos[f.stem] = f.suffix.lstrip(".")

    payload = {
        "generated": today,
        "logos": logos,
        "companies_read": len(companies), "unreadable": unreadable,
        "rendered": rendered,
        "no_board_on_file": sum(1 for o in orgs if o.get("no_board_on_file")),
        "manual_postings": manual_count,
        "totals": {
            # rows: one per advertisement, which is what the board lists
            "postings": len(postings),
            "quota_carrying_postings": sum(1 for p in postings if p["quota_carrying"]),
            # openings: one per (company, title), which is what it counts.
            # us/non_us count an opening if ANY of its rows says so, so an
            # opening advertised on both sides of a border appears in both.
            "openings": len(groups),
            "quota_carrying": sum(1 for rows in groups.values()
                                  if any(p["quota_carrying"] for p in rows)),
            "us": sum(1 for rows in groups.values()
                      if any(p["is_us"] is True for p in rows)),
            "non_us": sum(1 for rows in groups.values()
                          if any(p["is_us"] is False for p in rows)),
            "families": dict(fam_totals), "sectors": dict(sector_totals),
            # What the site can honestly say on screen about pay coverage.
            #
            # These four do NOT partition the board and must not be presented as
            # if they do. A posting can state pay without our ever having read a
            # description (Breezy publishes the range in the list response and no
            # description at all), and a posting we read in full very often
            # states no pay. The only safe readings are the direct ones: this
            # many rows carry a figure, this many rows we never read. Everything
            # else - above all "the rest pay nothing" - is invented.
            "pay_stated": sum(1 for rows in groups.values()
                              if any(p.get("comp") for p in rows)),
            "pay_stated_postings": sum(1 for p in postings if p.get("comp")),
            "pay_source": dict(pay_source),
            "jd_read_postings": sum(1 for p in postings if p.get("jd_seen")),
            "jd_unread_postings": sum(1 for p in postings if not p.get("jd_seen")),
        },
        "organizations": orgs,
        "postings": postings,
    }

    if owns:
        by_holder = collections.Counter(owns.values())
        print(f"{len(owns)} company(ies) share a board with another and were not "
              f"counted twice:")
        names = {c["id"]: c["name"] for c in companies}
        for follower, holder in sorted(owns.items())[:8]:
            print(f"   {names.get(follower, follower)[:30]:<30} -> "
                  f"{names.get(holder, holder)[:30]}")
        if len(owns) > 8:
            print(f"   ... and {len(owns) - 8} more")
        if unowned:
            print(f"   ({len(unowned)} of those boards are held arbitrarily: the slug "
                  f"names none of the companies sharing it)")

    filtered = [o for o in orgs if o.get("offtopic_dropped")]
    if filtered:
        tot = sum(o["offtopic_dropped"] for o in filtered)
        print(f"{tot} off-topic posting(s) dropped from {len(filtered)} horizontal "
              f"vendor(s) marked sled_only:")
        for o in sorted(filtered, key=lambda x: -x["offtopic_dropped"])[:6]:
            # rows against rows: offtopic_dropped counts postings thrown away,
            # so the kept side has to be postings too or the pair reads wrong.
            print(f"   {o['name'][:26]:<26} kept {o['open_postings']:>3}, "
                  f"dropped {o['offtopic_dropped']}")

    no_board = sum(1 for o in orgs if o.get("no_board_on_file"))
    print(f"{len(companies)} companies: {len(companies) - no_board} with a board on "
          f"file, {no_board} awaiting discovery")
    print(f"  {unreadable} boards unreadable, {rendered} recovered by rendering")
    t = payload["totals"]
    print(f"{t['openings']} open roles, advertised in {t['postings']} postings")
    print(f"  {t['quota_carrying']} quota-carrying, "
          f"advertised in {t['quota_carrying_postings']} postings")
    src = ", ".join(f"{n} {k}" for k, n in sorted(pay_source.items())) or "none"
    print(f"  {t['pay_stated']} state pay ({t['pay_stated_postings']} postings: {src})")
    # Said as two separate facts on purpose. Reading these as one number - "the
    # rest pay nothing" - is the failure this field exists to prevent.
    print(f"  {t['jd_read_postings']} descriptions read, "
          f"{t['jd_unread_postings']} postings not read"
          f"{'' if ats.FETCH_DETAILS else ' (--details reads ~971 more)'}")
    for f, n in fam_totals.most_common():
        print(f"  {n:>4}  {roles.LABEL.get(f, f)}")
    if a.dry_run:
        print("\n(dry run, nothing written)")
        return 0

    # A partial run must never overwrite the full board. --limit and --company are
    # for testing a fetcher, and writing 3 companies over 137 silently destroys the
    # dataset the site reads. Learned the hard way.
    if (a.limit or a.company) and not a.write_partial:
        print("\npartial run, not written. Pass --write-partial to overwrite the "
              "full board on purpose.")
        return 0

    DATA.mkdir(exist_ok=True)
    HISTORY.mkdir(exist_ok=True)
    prev_path.write_text(json.dumps(payload, indent=1) + "\n")
    # snapshot only the ids: enough for repost detection, small enough to keep
    (HISTORY / f"{today}.json").write_text(json.dumps(
        {"date": today, "ids": sorted(p["id"] for p in postings)}, indent=1) + "\n")
    print(f"\nwrote data/board.json and data/history/{today}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
