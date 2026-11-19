#!/usr/bin/env python3
"""Wire a found board to its company - only when two separate checks agree.

    python3 scripts/wire_found.py plan --results site_boards.json --out worklist.json
    python3 scripts/wire_found.py plan --results ... --doubts doubts.json --by agent:<label> --out worklist-2.json
    python3 scripts/wire_found.py apply worklist.json --by agent:<label>            # look only
    python3 scripts/wire_found.py apply worklist.json --by agent:<label> --write --expect <code>

WHY THIS EXISTS. On 2026-11-18 the crawl read postings from about a quarter of
the companies on the board. Of the rest, roughly a thousand have a careers
page nothing can enumerate and over nine hundred have no board on file at
all. Many of those sites name the real board behind them - an iframe, a
script, a link - and wiring that board is the cheapest coverage this project
gets: a JSON feed the nightly crawl reads from then on.

It is also the easiest place to publish somebody else's jobs under this
company's name, and that has happened three ways already. Concourse: a board
found by search whose slug matched the name, belonging to a different company
called Concourse. Prepared: its own careers page links greenhouse/axon, 500
postings, every one stamped "Axon". Circuit: its board calls itself "TFR
Transit Inc". A name-shaped guess passed all three.

THE OWNER'S RULING, 2026-11-18. Claude may wire a found board ONLY when two
separate checks agree:

  CHECK 1  the company's own website points to the board. Evidence comes from
           find_site_boards.py, which records a board only when it was seen
           on a page whose final address is on the company's own site.
  CHECK 2  the board itself names that company as the employer
           (verify_boards.board_says, then verify_boards.proven). A slug, a
           tenant id or a host that looks like the name is not the board
           naming anybody.

A parent's board, a name mismatch and anything doubtful go to his "Boards we
found" queue instead. Each verified batch is written without waiting for him
and he is told after. Giants (about 500+ open roles) are skipped this round.

THREE OUTCOMES, AND NONE OF THEM IS SILENCE.
  WIRE  both checks pass, the board was read live a moment ago and has
        postings on it - at least as many as the public board already shows
        for the company - and nothing on file argues with it: no parent, no
        note about its board, no earlier refusal, no other company already
        holding the same board.
  HOLD  the board reads, and something is in doubt. The doubt is written in
        words - both names quoted, or the parent named - and the row goes to
        the owner's queue through board_proposals.file_found. (Where the
        roles are merely advertised - a link to the company's Indeed or
        LinkedIn page, the vendor's own "powered by" link - is a note on the
        row, not a doubt; and a board whose identity probe failed ON THE DAY
        - the network, a 5xx, a 429 - is listed under SKIP and asked again,
        not filed.) A READER'S DOUBT STANDS FROM THE MOMENT IT IS GIVEN:
        `plan --doubts` keeps every one in data/board_doubts.json before it
        asks any board, puts its words on a row already waiting for the
        owner, and every later plan and every apply - of any worklist - holds
        that company until he rules on a row that carried the doubt.
  SKIP  a giant; a board nobody could show to be smaller than one; a board
        that did not read (kept with its error: a board that will not read is
        not a wrong board); a company whose site named no board, or only one
        this door cannot use; a company a person already answered about.

THE PLAN IS FROZEN, AND APPLY READS NOTHING ELSE. `plan` is the only step that
touches the network. It writes one worklist with the whole evidence for every
row. `apply` reads that file and makes no request. The failure this is built
against is a landing script that reads its inputs again at write time: they
have grown since the dry run, and rows nobody looked at go into the write. So
a write here needs --expect with the code the look printed, and that code
covers the worklist's bytes AND what the look said it would do. If either
moved, nothing is written and the look is run again.

APPLY DOES ASK THE DATA AGAIN, because the data is the one thing that moves
under a frozen file. Both checks were judged against a record: check 1
against its website, check 2 against its name. A person who corrects either
between the plan and the write has changed what the checks were about, so
the row is left alone and the plan is run again - as it is when a parent, a
refusal, a note or another holder of the board was recorded in between.

WHAT APPLY WRITES. The wired boards are ONE journalled batch through
admin.read_companies() / validate() / save_companies(), with the agent's own
label as the author - never the default 'owner'. One entry, because the live
journal is at its 500-entry cap and every extra entry pushes one of the
owner's own out of reach of admin_undo.py. force is passed only for the
journal's 25-record rule and only after the count has been printed; its
34%-of-the-file rule and validate() are never argued with. The held boards
are one journalled append to data/embedded_ats.json. The readers' doubts
are not apply's to write: `plan --doubts` kept them before its first
request (one journalled addition to data/board_doubts.json, and one to the
rows already waiting), so no worklist, earlier or later, and no failure of
this plan can wire past one.

Postings do not appear when this runs. The nightly crawl reads the new boards
and the site follows it.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import add_company                                     # noqa: E402
import admin                                           # noqa: E402
import ats                                             # noqa: E402
import board_proposals                                 # noqa: E402
import journal                                         # noqa: E402
import page_belt                                       # noqa: E402
import privacy                                         # noqa: E402
import roles                                           # noqa: E402
import verify_boards                                   # noqa: E402

# The owner's line for this round: AWS / Oracle / Salesforce / IBM size.
GIANT = 500
GIANT_WHY = "giant - skipped this round on the owner's ruling of 2026-11-18"
# A READER THAT DOES NOT READ THE WHOLE BOARD CANNOT SHOW A BOARD IS SMALL.
# The Workday reader asks two sales searches and the Oracle reader takes one
# reply, so 180 rows from either is as likely a company with 4,000 openings
# as one with 200 - and the first draft of this door wired exactly that, on
# the strength of a count that was never the board's. For these the board's
# OWN total is asked first (add_company.board_total, one request, and a giant
# is then never read at all). Where no total can be had the size is unknown,
# and unknown is skipped with the giants: "probably small" is not this door's
# to decide, and a giant in the owner's queue is what his ruling ruled out.
PARTIAL = {"workday": "the Workday reader asks only two sales searches",
           "oracle": "the Oracle reader takes one reply and never pages"}
SAMPLE = 8                  # titles kept per board: what ownership is judged on
ACTION = "wire-found"
MADE = "wire_found.py plan"
# 2: rows carry what they were judged against. 3: a row's postings are its
# OPENINGS, not its rows; what may be WIRE changed (an aggregator, a vendor's
# site, a failure of the day); the readers' doubts travel with their words
# (`reader_doubts`); and a WIRE row records the waiting rows it was planned
# beside. 4: the readers' doubts were KEPT by the plan before it asked any
# board (apply writes none), and the plan records which Workable account
# numbers it followed (`workable_numbers`). An older worklist is judged by
# rules that are no longer the door's.
VERSION = 4
WAITING = board_proposals.WAITING
# A READER'S DOUBT STANDS FROM THE MOMENT IT IS GIVEN. One record per doubt,
# {"<company id>@<when>": {id, board, type, ref, why, by, on, at}} (the first
# shape, {company id: {board, why, by, on, at}}, is still read), written only
# by `plan --doubts` before its first request, through the journal, with the
# agent's own label - an ADDITION every time, never a rewrite. Every later
# plan holds such a company, and every apply of any worklist leaves its WIRE
# row alone, until the owner rules on a row in Boards we found that carried
# the doubt (board_proposals.standing_doubts). No file is no doubts.
DOUBTS = board_proposals.DOUBTS
DOUBT_ACTION = "reader-doubt"

# How a page points at a board, in the words the note on the record uses.
POINTS = {"href": "links to the board", "iframe": "embeds the board",
          "frame": "embeds the board", "script": "loads the board",
          "request": "loads its jobs from the board",
          "redirect": "redirects to the board", "text": "names the board"}
# Which evidence is quoted when a board was seen more than once. An embed, a
# request the page itself made or a redirect is the site USING the board; a
# link is the site sending people to it; bare text is the weakest - one
# footer line, a partner logo or a blog post can leave a slug on a page - and
# on its own it is a doubt, not a pointer (see consider).
STRENGTH = {"iframe": 0, "frame": 0, "script": 0, "request": 0, "redirect": 0,
            "href": 1, "text": 2}
# What the finder says about a page it asked for. A redirect only counts as
# theirs when the address was actually followed to where it ends: a page
# robots.txt refused, a block or an error has no known end.
FOLLOWED = ("read", "left-site")
# WHERE A COMPANY ALSO ADVERTISES IS NOT A SECOND BOARD. The finder files a
# link to a company's Indeed or LinkedIn page under `elsewhere`, beside the
# job platforms nothing here reads, and the first real run of this door
# (2026-11-18, 38 companies) held Anaplan's Greenhouse board on one: 128
# postings, loaded by Anaplan's own careers page, the board naming Anaplan,
# and held because that page also links the company's Indeed profile. An
# aggregator carries copies of the roles a company posts on its board. It is
# not somewhere the company keeps one, so it says nothing about which board
# they hire through. It stays on the row as a note and is never a doubt -
# but ONLY AS A LINK OR A MENTION (ADVERTISED_AS). A careers page that embeds
# its LinkedIn job list, or loads it by script, is showing where it hires
# today, beside whatever old board it still links: City Detect's shape,
# which is why a second platform holds a board at all. Y Combinator's Work
# at a Startup and Wellfound are not copies of anything: companies post and
# take applications there, so they are hiring channels, and a doubt.
ADVERTISED_ON = frozenset({"linkedin", "indeed"})
ADVERTISED_AS = frozenset({"href", "text"})
# THE PLATFORM'S OWN SITE, LINKED BY ITS OWN WIDGET. "Powered by JazzHR" under
# an embedded JazzHR board is a link to info.jazzhr.com; the finder files a
# careers-looking link to a host it does not know as other:<its site>, and
# the same run held Freebee's board on that one link. ONLY the site of the
# very platform the board is on, and ONLY AS A LINK (VENDOR_AS): the finder
# also files a frame from a vendor host that names no board as other:<that
# site> - a Workable posting embedded by short code (any account's), a
# Greenhouse form by token - and that is a second reference on the platform
# whose board is unknown. other:<anything else> is a careers link that
# leaves for a host nothing here knows - which is exactly what a parent's
# careers site looks like - and it still holds the board. Not listed, for
# the same reason: a domain where every tenant gets a careers site of its
# own on a host of its own (<tenant>.jibeapply.com, .applytojob.com,
# .bamboohr.com, .breezy.hr, .recruitee.com, .icims.com, .workable.com) - a
# link there can be ANOTHER tenant's careers site, a parent's for one. Kept:
# a vendor whose boards sit under a path on its board host. And only the
# platforms whose boards can be proven here: adp.com, oracle.com and
# workday.com are also the careers sites of three very large employers, and
# no board on those three is wired without a person anyway.
VENDOR_SITES = {
    "ashby": {"ashbyhq.com"}, "gem": {"gem.com"},
    "greenhouse": {"greenhouse.io", "greenhouse.com"}, "gusto": {"gusto.com"},
    "jazzhr": {"jazzhr.com"}, "lever": {"lever.co"}, "paylocity": {"paylocity.com"},
    "rippling": {"rippling.com"}, "smartrecruiters": {"smartrecruiters.com"}}
VENDOR_AS = frozenset({"href"})
SKIP_WORDS = (("giant", "giants"), ("unreadable", "unreadable"),
              ("size-unknown", "size unknown"),
              ("no-candidate", "no board seen on their site"),
              ("unusable", "named only a board this door cannot use"),
              ("refused", "refused earlier"),
              ("not-waiting", "already on a board"),
              ("out-of-scope", "ruled out of scope"),
              ("gone", "no longer on file"))

# the part of a Paylocity address that IS the board: the id after /jobs/all/
# (a uuid on every board seen), whatever follows it
_PAYLOCITY = re.compile(r"/jobs/all/([^/?#]+)")
# A Workable posting's short address, and the address it opens at. The second
# carries the board's slug, which the first does not.
_WORKABLE_SHORT = re.compile(r"https://apply\.workable\.com/j/([A-Za-z0-9]{6,24})")
_WORKABLE_PAGE = re.compile(
    r"https://apply\.workable\.com/([a-z0-9][a-z0-9-]{1,59})/j/([A-Za-z0-9]{6,24})", re.I)
_ICIMS = re.compile(r"([a-z0-9-]+)\.icims\.com(?:[/?]|$)")
_POD = re.compile(r"wd\d+")             # a Workday pod: wd1, wd5, wd108, wd503


# ---------------------------------------------------------------- small words

def board_key(kind, ref) -> str:
    """One LOOSE spelling per board, for one question: is this board that one?

    It is asked to notice a collision - the same board found for two
    companies, already held by another, refused before, already waiting - and
    a collision only ever sends a board to a person. So it leans toward
    calling two addresses one board. Case is ignored (SmartRecruiters refs
    are case-sensitive, so two different boards can share a key). An address
    is the same board with or without its scheme, its www. or a last slash.
    And three shapes are read for the part that identifies the board, because
    this repo's own data writes each of them two ways: a Paylocity address is
    the id after /jobs/All/, with or without the tenant's name after it
    (keyed on the whole address, a board already on file as
    .../<uuid>/<Tenant> was not seen to be the board found as .../<uuid>, and
    would have been wired to a second company); an iCIMS portal is its slug,
    bare or inside its address; a Jibe or Oracle board is its host.

    IT IS NEVER WHAT GETS READ. Reading goes by `spelling`, the ref exactly
    as the site gave it: on a platform that minds case the loose form is a
    different board, or none.
    """
    if not isinstance(ref, str):
        return f"{kind}:{json.dumps(ref, sort_keys=True).lower()}"
    r = ref.strip().lower()
    for lead in ("https://", "http://"):
        r = r.removeprefix(lead)
    r = r.removeprefix("www.").rstrip("/")
    if kind == "paylocity":
        m = _PAYLOCITY.search(r)
        r = m.group(1) if m else r
    elif kind == "icims":
        m = _ICIMS.match(r)
        r = m.group(1) if m else r
    elif kind in ("jibe", "oracle"):
        r = r.split("/", 1)[0]
    return f"{kind}:{json.dumps(r)}"


def spelling(kind, ref) -> str:
    """The board exactly as it was spelled - what a read is filed under.

    Two spellings that share a `board_key` are read separately and never
    stand in for each other: 'caseco' answering empty says nothing about
    'CaseCo'.
    """
    if isinstance(ref, str):
        ref = ref.strip().rstrip("/")
    return f"{kind}:{json.dumps(ref, sort_keys=True)}"


def show(kind, ref) -> str:
    """greenhouse/acme - how the journal and the desk already write a board."""
    if isinstance(ref, list):
        return f"{kind}/{'/'.join(str(x) for x in ref)}"
    return f"{kind}/{ref}"


def ref_problem(kind, ref) -> str | None:
    """Why this (type, ref) cannot be handed to a fetcher, or None."""
    if kind not in ats.FETCHERS or kind == "html":
        return f"{kind!r} is not a board the crawl reads"
    if kind == "workday":
        if not (isinstance(ref, list) and len(ref) == 3
                and all(isinstance(x, str) and x.strip() for x in ref)):
            return "a Workday board is the list [tenant, pod, site]"
        # THE POD IS THE PART THAT GETS OUT OF ORDER. find_embedded_ats.py
        # wrote [tenant, site, pod] for months, and that asks
        # tenant.<site>.myworkdayjobs.com - nobody's host. Every one of the 72
        # Workday refs on file on 2026-11-18 has "wd" and digits second; the
        # other order fetch_workday takes is pod first, then a "wd.." tenant.
        if not (_POD.fullmatch(ref[1])
                or (_POD.fullmatch(ref[0]) and ref[1].startswith("wd"))):
            return ("a Workday board is the list [tenant, pod, site], and the pod "
                    "reads 'wd' and digits")
        return None
    if not isinstance(ref, str) or not ref.strip():
        return "the board has no ref"
    return None


def short(url: str) -> str:
    """acme.com/careers, for the one-line note on the record."""
    u = str(url or "").strip()
    for lead in ("https://", "http://"):
        if u.lower().startswith(lead):
            u = u[len(lead):]
    return u.removeprefix("www.").rstrip("/")


def many(n: int, word: str = "posting") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def pointer(seen: dict, full: bool = True) -> str:
    """'https://acme.com/careers embeds the board (seen 3 times)'."""
    page = seen.get("page") or "their site"
    how = POINTS.get(seen.get("element"), "names the board")
    times = seen.get("times") or 1
    tail = f" (seen {times} times)" if times > 1 else ""
    return f"{page if full else short(page)} {how}{tail}"


_when = board_proposals.when


def _count(n) -> int | None:
    """`n` when it is a real count, else None. True is not one posting."""
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def _akas(c: dict) -> list:
    """The other names on file for a company, in one order."""
    return sorted(str(a) for a in (c.get("also_known_as") or []) if str(a).strip())


def _encodable(obj):
    """`obj` with every string made writable: half a character is dropped.

    A feed that cuts a title in the middle of an emoji hands back half of
    one, and no file can hold that. It stopped a whole plan at its last line,
    after every board had been read, and left an empty worklist behind.
    """
    if isinstance(obj, str):
        return obj.encode("utf-8", "replace").decode("utf-8")
    if isinstance(obj, list):
        return [_encodable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _encodable(v) for k, v in obj.items()}
    return obj


# ------------------------------------------------------- the finder's results

def load_results(paths: list) -> tuple[dict, list]:
    """(what the finder saw per company, which files said so).

    Several files are one run read two ways - a plain fetch and a rendered
    one - so a company's references are pooled. If the two disagree about
    which board the site points to, that is two boards on one site, and the
    company is held like any other with two.
    """
    merged: dict = {}
    files = []
    for p in paths:
        raw = pathlib.Path(p).read_bytes()
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get("companies"), dict):
            raise ValueError(f"{p} is not a finder results file: no 'companies' map")
        mode = doc.get("mode")
        files.append({"file": pathlib.Path(p).name,
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "generated": doc.get("generated"), "mode": mode,
                      "companies": len(doc["companies"])})
        for cid, entry in doc["companies"].items():
            if not isinstance(entry, dict):
                continue
            m = merged.setdefault(cid, {"name": entry.get("name"),
                                        "candidates": [], "pages": [],
                                        "off_site": [], "elsewhere": [],
                                        "errors": [], "read_as": []})
            for cand in entry.get("candidates") or []:
                if isinstance(cand, dict):
                    m["candidates"].append({**cand, "mode": mode})
            for field in ("pages", "off_site", "elsewhere"):
                m[field] += [x for x in entry.get(field) or [] if isinstance(x, dict)]
            if entry.get("error"):
                m["errors"].append(str(entry["error"]))
            # the website the FINDER read them at. It decides nothing - the
            # one on file now does - and it is quoted when the two differ
            site = str(entry.get("website") or "").strip()
            if site and site not in m["read_as"]:
                m["read_as"].append(site)
    return merged, files


def boards_named(entry: dict) -> tuple[list, list]:
    """(the distinct boards a company's own site named, references unusable).

    One record per board, with every place it was seen. Most-mentioned first:
    when a site names two boards the company is held either way, and the one
    it leans on hardest is the one offered to the person who rules.

    A board is one `board_key`, and that is loose on purpose. So each place
    it was seen keeps the ref AS IT WAS SPELLED THERE, and when a site spells
    one board two ways the record says so (`spellings`): the first draft kept
    whichever spelling came first, read 'caseco', found it empty, and never
    asked for the 'CaseCo' the page actually embeds.
    """
    boards: dict = {}
    unusable = []
    for cand in entry.get("candidates") or []:
        kind, ref = cand.get("type"), cand.get("ref")
        problem = ref_problem(kind, ref)
        if problem:
            unusable.append(f"{show(kind, ref)}: {problem}")
            continue
        if isinstance(ref, str):
            ref = ref.strip()
        key = board_key(kind, ref)
        b = boards.setdefault(key, {"type": kind, "key": key, "seen": []})
        times = cand.get("times")
        hop = cand.get("hop")
        b["seen"].append({
            "ref": ref,
            "page": str(cand.get("page") or "").strip(),
            "element": str(cand.get("element") or "").strip(),
            "saw": str(cand.get("saw") or "")[:160],
            "times": times if isinstance(times, int) and not isinstance(times, bool)
            and times > 0 else 1,
            "hop": hop if isinstance(hop, int) and not isinstance(hop, bool) else 0,
            "mode": cand.get("mode"),
            # set by named_by_number: the account number the site used, or
            # why the board's own address could not be found from it, or that
            # it was not looked for (a company somebody refused)
            "account": cand.get("account"), "no_address": cand.get("no_address"),
            "follow_failed": cand.get("follow_failed"), "too_big": cand.get("too_big"),
            "declined": cand.get("declined")})
    for b in boards.values():
        b["times"] = sum(s["times"] for s in b["seen"])
        spelled = {spelling(b["type"], s["ref"]): show(b["type"], s["ref"]) for s in b["seen"]}
        if len(spelled) > 1:
            b["spellings"] = sorted(spelled.values())
    return sorted(boards.values(), key=lambda b: (-b["times"], b["key"])), unusable


def their_page(seen: dict, website: str, pages: list) -> str:
    """The address on the company's OWN site behind this reference, or "".

    CHECK 1, asked again. The finder already promises it: a reference is a
    candidate only when the page finally served is on the company's own host.
    This door asks once more, against the website on file NOW, because it is
    the last gate and the two files can drift - a website corrected after the
    finder ran makes yesterday's "own site" somebody else's.

    A redirect is theirs when an address ASKED FOR on their site is what
    ended up at the board, and that asked address is the one to quote: the
    address it landed on is the board, which proves nothing about who sent
    you there. The record has to show where the asked address ENDED - a
    blank is not an address, and two blanks once compared equal, so a careers
    page robots.txt had refused was written down as "redirects to the board"
    - and that the finder really followed it there.
    """
    page = str(seen.get("page") or "").strip()
    if page_belt._same_site(page, website or ""):
        return page
    if seen.get("element") == "redirect":
        for p in pages:
            asked = str(p.get("asked") or "").strip()
            final = str(p.get("final") or "").strip()
            if final and p.get("status") in FOLLOWED \
                    and page_belt._same_site(asked, website or "") \
                    and final.rstrip("/") == page.rstrip("/"):
                return asked
    return ""


def best_seen(board: dict, website: str, pages: list) -> tuple[dict, bool]:
    """(the evidence to quote for this board, whether it is on their site).

    The page on the evidence is the company's own address wherever one was
    shown; `landed` keeps where a redirect ended up. The evidence carries
    the ref as that page spelled it, and that spelling is the one read.
    """
    def rank(s):
        return (not their_page(s, website, pages),
                STRENGTH.get(s["element"], 3), -s["times"], s["hop"])
    best = dict(sorted(board["seen"], key=rank)[0])
    own = their_page(best, website, pages)
    if own and own != best["page"]:
        best["landed"], best["page"] = best["page"], own
    return best, bool(own)


def advertising(vendor: str, kind, element=None) -> bool:
    """Is this `elsewhere` entry somewhere the roles are only advertised?

    True for an aggregator (ADVERTISED_ON) seen as a link or in text, and for
    the own site of the very platform the board is on (VENDOR_SITES[kind],
    which the finder writes as other:<site>) seen as a link. Neither is a
    second place the company keeps a board. Seen any other way - embedded,
    loaded by script or request, redirected to, or with no element recorded
    - it is how the page shows its jobs, and that is a doubt.
    """
    how = str(element or "").strip().lower()
    if vendor in ADVERTISED_ON:
        return how in ADVERTISED_AS
    return vendor.startswith("other:") and how in VENDOR_AS and \
        vendor.split(":", 1)[1].strip().lower() in VENDOR_SITES.get(kind, ())


def also_seen(entry: dict, key, website: str, kind=None) -> dict:
    """What else the finder saw: {elsewhere, advertised_on, off_site,
    elsewhere_off_site}.

    `elsewhere` is a job platform this repo has no reader for, named on the
    company's OWN site. That one is a doubt (see consider): it is a second
    place they may be hiring through, and City Detect is the case on file -
    its site embedded a platform nothing here read while its old board still
    answered and still named it. A platform recorded with no page is counted
    as theirs, because nothing shows it was not.

    `advertised_on` is what the finder files in the same list and is NOT such
    a platform (see `advertising`): a link to the company's Indeed or
    LinkedIn page, or the board's own vendor linked from its own widget. A
    note, not a doubt.

    The other two were seen on pages that are NOT theirs - a different board,
    or such a platform. Neither is one of the checks and how noisy either
    list is has not been measured, so they hold nothing back; they are kept
    on the row and printed for whoever reads the plan.

    `key` is the board's key, or every key it is known by (board_keys).
    """
    keys = {key} if isinstance(key, str) else set(key or ())
    out = {}
    other = sorted({show(o.get("type"), o.get("ref")) for o in entry.get("off_site") or []
                    if board_key(o.get("type"), o.get("ref")) not in keys})
    if other:
        out["off_site"] = other[:6]
    own, away, ads = set(), set(), set()
    for e in entry.get("elsewhere") or []:
        vendor = str(e.get("vendor") or "").strip()
        page = str(e.get("page") or "").strip()
        if not vendor:
            continue
        if advertising(vendor, kind, e.get("element")):
            ads.add(vendor)
        else:
            (own if not page or page_belt._same_site(page, website or "") else away).add(vendor)
    if own:
        out["elsewhere"] = sorted(own)[:6]
    if ads - own:
        # (a vendor seen both ways is the doubt above, not a note as well)
        out["advertised_on"] = sorted(ads - own)[:6]
    if away - own:
        out["elsewhere_off_site"] = sorted(away - own)[:6]
    return out


# ------------------------------------------------- what is on file, no network

def read_stores() -> dict:
    """Every file this door holds a board against, read STRICTLY, once.

    NOT admin.read, which hands back its default for a file that does not
    parse or has another shape. A rulings file with merge-conflict markers
    in it read as no rulings, and the board the owner had refused planned
    WIRE and was written; a truncated agent_proposals.json lost its
    rejections the same way (second review, 2026-11-19). So a file that is
    on disk and does not read as its shape stops `plan` before its first
    request and `apply` before anything is written. Raises
    board_proposals.Unreadable; a file that is not there is empty.
    """
    d = admin.DATA
    return {"ruled": board_proposals.strict(board_proposals.RULED.name, d),
            "proposals": board_proposals.strict(board_proposals.PROPOSALS, d),
            "found": board_proposals.strict(board_proposals.FOUND.name, d),
            "suspects": board_proposals.strict(board_proposals.SUSPECTS, d),
            "doubts": board_proposals.read_doubts(d)}


def _stores(stores: dict | None) -> dict:
    return stores if stores is not None else read_stores()


def _suspects(stores: dict | None = None) -> dict:
    """{company id: discovery's own note} from data/ats_suspects.json."""
    sus = _stores(stores)["suspects"]
    items = sus.get("suspects", sus) if isinstance(sus, dict) else {}
    if isinstance(items, list):
        items = {i.get("id"): i for i in items if isinstance(i, dict)}
    return {k: str((v or {}).get("note") or "its slug did not match the company's name")
            for k, v in (items or {}).items() if isinstance(v, dict)}


def _refusals(stores: dict | None = None) -> dict:
    """{company id: [earlier refusals]} from the two stores that keep them.

    The stores do not talk to each other, so both are read. A refusal in
    board_proposal_rulings.json carries no board; the row it ruled on is
    still in embedded_ats.json, which is where the board is read from.
    """
    s = _stores(stores)
    out = collections.defaultdict(list)
    listed = {r.get("id"): r for r in s["found"] if isinstance(r, dict)}
    for cid, v in s["ruled"].items():
        if not isinstance(v, dict) or v.get("accept"):
            continue
        f = (listed.get(cid) or {}).get("found") or {}
        out[cid].append({"store": "Boards we found",
                         "key": board_key(f.get("type"), f.get("ref")) if f else None,
                         "board": show(f.get("type"), f.get("ref")) if f else None,
                         "type": f.get("type"), "ref": f.get("ref"),
                         "by": v.get("by") or "?", "on": v.get("on") or "?",
                         "why": str(v.get("why") or "").strip()})
    for key, p in s["proposals"].items():
        if not (isinstance(p, dict) and key.startswith("board:")
                and p.get("status") == "rejected"):
            continue
        cid = p.get("id") or key.split(":", 1)[1]
        out[cid].append({"store": "Agent proposals",
                         "key": board_key(p.get("ats_type"), p.get("ats_ref")),
                         "board": show(p.get("ats_type"), p.get("ats_ref")),
                         "type": p.get("ats_type"), "ref": p.get("ats_ref"),
                         "by": p.get("ruled_by") or "?", "on": p.get("ruled_on") or "?",
                         "why": str(p.get("ruled_why") or "").strip()})
    return out


def read_doubts() -> tuple[dict, str | None]:
    """(data/board_doubts.json grouped by company - {id: [its doubts]} - or
    {} with why it cannot be used).

    NOT admin.read, which hands back its default for a file that does not
    parse: a doubts file that cannot be read would then hold nothing, and
    every board a reader doubted would plan as WIRE. A file that is there and
    does not read as records with words in them stops the door instead. The
    reader is board_proposals.read_doubts, the one discovery and
    wire_embedded.py use too.
    """
    try:
        return board_proposals.read_doubts(admin.DATA), None
    except board_proposals.Unreadable as exc:
        return {}, str(exc)


_ruled_since = board_proposals.ruled_since


def standing_doubts(grouped: dict, stores: dict | None = None) -> dict:
    """{company id: [its standing doubts]} - the doubts in `grouped` the
    owner has not settled by ruling on a row that carried them
    (board_proposals.standing_doubts, the one rule every door holds a company
    to). A refused company is SKIP refused anyway; an accepted one is on a
    board."""
    s = _stores(stores)
    return board_proposals.standing_doubts(grouped, s["ruled"], s["found"])


def reach(c: dict | None, rows: list, ruling, doubts=None) -> tuple[str, str]:
    """(code, words): whether a reader's doubt about this company can reach
    the owner, said plainly. Codes: gone, wired, ruled, unseen, row, none.
    `doubts` are the company's standing doubts, when the words are about them.

    A DOUBT THAT CANNOT REACH HIM IS NOT "HIS TO RULE ON". The queue never
    shows a ruled company again, so a doubt about a company he ruled on
    before holds its board for good and has to be TOLD to him; a company
    already on a board was wired before the doubt was kept. Neither is
    promised a ruling in Boards we found (second review, 2026-11-19). Nor is
    a doubt his row does not carry (`unseen`: JustFOIA's second doubt was kept
    after its row was filed, in other words) - his ruling there would not
    settle it, and `plan --doubts` naming the company puts it on the row.
    """
    if c is None:
        return "gone", "no company with this id is on file, so it holds nothing"
    a = c.get("ats") or {}
    if a.get("type") not in WAITING:
        return "wired", (f"it already reads from {show(a.get('type'), a.get('ref'))}: wired "
                         f"before the reader's doubt was kept - tell the owner")
    if isinstance(ruling, dict):
        return "ruled", (f"it cannot reach the owner's queue: he ruled on this company in "
                         f"Boards we found on {ruling.get('on') or '?'} (a board "
                         f"{'accepted' if ruling.get('accept') else 'refused'}), and that "
                         f"queue never shows a ruled company again - tell the owner")
    if rows:
        f = rows[0].get("found") or {}
        if doubts and not all(board_proposals.carried(rows, d) for d in doubts):
            return "unseen", (f"its row in Boards we found ({show(f.get('type'), f.get('ref'))}) "
                              f"does not carry it, so his ruling there would not settle it: "
                              f"give it again with plan --doubts, which puts it on that row, or "
                              f"tell the owner")
        return "row", (f"it is on its row in Boards we found ({show(f.get('type'), f.get('ref'))}), "
                       f"and holds the board until the owner rules on that row")
    return "none", ("no row for it is in Boards we found yet: it holds the board until a "
                    "plan files one and the owner rules on it")


def doubt_words(d: dict, holds: str | None = None) -> str:
    """A standing doubt, as a held row says it - the reader's words whole,
    and what it holds until (`holds`: reach's words for the company)."""
    return (f"a reader's doubt about this company stands, recorded "
            f"{d.get('on') or '?'} by {d.get('by') or '?'}"
            + (f" about {d['board']}" if d.get("board") else "")
            + f": {str(d.get('why') or '(no words recorded)').strip()}; "
            + (holds or "it holds the board until the owner rules on it in Boards we found"))


def _waiting(stores: dict | None = None) -> dict:
    """{company id: [boards already waiting on a person for it]}.

    A row in Boards we found that nobody has ruled, or a board proposal still
    pending in Agent proposals. The SAME board found again is no new doubt -
    it is the question already asked, and the two checks may now answer it.
    A DIFFERENT board is: two finders, two boards, one company.
    """
    s = _stores(stores)
    out = collections.defaultdict(list)
    ruled = s["ruled"]
    for r in s["found"]:
        f = r.get("found") if isinstance(r, dict) else None
        if isinstance(f, dict) and r.get("id") not in ruled:
            filed = r.get("filed") if isinstance(r.get("filed"), dict) else None
            out[r["id"]].append({"store": "Boards we found",
                                 "key": board_key(f.get("type"), f.get("ref")),
                                 "board": show(f.get("type"), f.get("ref")),
                                 # a row a door FILED (only board_proposals.file_found
                                 # writes `filed`), with the doubt it filed it on
                                 "filed": filed,
                                 "why": str(r.get("identity_why") or "").strip()})
    for key, p in s["proposals"].items():
        if isinstance(p, dict) and key.startswith("board:") and p.get("status") == "pending":
            out[p.get("id") or key.split(":", 1)[1]].append({
                "store": "Agent proposals",
                "key": board_key(p.get("ats_type"), p.get("ats_ref")),
                "board": show(p.get("ats_type"), p.get("ats_ref"))})
    return out


def doubt_board(d: dict) -> tuple:
    """(type, ref) of the board a doubt is about, or (None, None). A record
    kept since carries both; the first shape wrote only "greenhouse/acme"
    (`show`), and a Workday board as "workday/<tenant>/<pod>/<site>"."""
    if d.get("type"):
        return d["type"], d.get("ref")
    kind, _, rest = str(d.get("board") or "").partition("/")
    if not kind or not rest:
        return None, None
    return kind, (rest.split("/") if kind == "workday" else rest)


def held_boards(standing: dict, stores: dict, names: dict) -> dict:
    """{board key: [{id, code, words}]} - every board the
    owner holds, under WHATEVER company it is held for: the board of a
    reader's doubt that stands, and the board of a row a door filed in
    Boards we found that he has not ruled on.

    HELD BY BOARD AS WELL AS BY COMPANY. A doubted company merged into its
    duplicate at the desk left its doubt, and its door-filed row, under the
    id that was dropped; the survivor's plan saw neither and the door wired
    the board both readers doubted (second review, 2026-11-19). A board held
    for one company is held for every company whose site names it.
    """
    out = collections.defaultdict(list)
    for cid, recs in (standing or {}).items():
        for d in recs:
            kind, ref = doubt_board(d)
            if kind:
                out[board_key(kind, ref)].append({"id": cid, "code": "standing", "words": (
                    f"{show(kind, ref)} is the board of a reader's doubt that stands about "
                    f"{names.get(cid) or cid}, recorded {d.get('on') or '?'} by "
                    f"{d.get('by') or '?'}: {str(d.get('why') or '').strip()}")})
    ruled = stores["ruled"]
    for r in stores["found"]:
        f = r.get("found") if isinstance(r.get("found"), dict) else None
        if f and isinstance(r.get("filed"), dict) and r["id"] not in ruled:
            out[board_key(f.get("type"), f.get("ref"))].append({"id": r["id"], "code": "queued", "words": (
                f"{show(f.get('type'), f.get('ref'))} is in the owner's queue (Boards we found) "
                f"for {names.get(r['id']) or r['id']}, filed {r['filed'].get('on') or '?'} by "
                f"{r['filed'].get('by') or '?'} with its doubt: "
                f"\"{str(r.get('identity_why') or 'none recorded')[:240]}\"; a board filed "
                f"there is his to settle")})
    return out


def numbers_on_file(companies: list) -> dict:
    """{Workable account number: [ids of the records that hold it]}."""
    out = collections.defaultdict(list)
    for c in companies:
        a = c.get("ats") or {}
        if numbered(a.get("type"), a.get("ref")):
            out[_number(a["ref"])].append(c["id"])
    return out


def unasked_numbers(on_file: dict, resolved: dict, names: dict, ignore=()) -> list:
    """The words for every Workable account number a record holds that this
    run did not resolve to a slug: whether one of them is the board found
    now was not asked, so no Workable board is wired beside them."""
    out = []
    for number in sorted(on_file):
        got = resolved.get(number)
        slug = got[0] if isinstance(got, (list, tuple)) and got else got
        if slug or number in ignore:
            continue
        who = " and ".join(names.get(i) or i for i in on_file[number][:3])
        out.append(f"a record holds Workable account {number} ({who}), and whether it is "
                   f"this board was not asked")
    return out


def acquisition_doubts(companies: list, board: dict) -> dict:
    """{company id: what the Acquisitions queue says about it}.

    Both builders are asked, because they see different things and either one
    is a reason to stop. admin.q_acquisitions is the queue the desk draws:
    discovery's slug suspects, a careers page sitting on another member's
    domain, and a parent named by outside research. acquisitions.py adds a
    website that now serves another brand, a shared logo file and a board
    that calls itself somebody else. A company in either is the owner's.
    Neither is wrapped in a try: if the queue cannot be built, "not in the
    Acquisitions queue" cannot be shown, and nothing may be wired on it.
    """
    import acquisitions
    says: dict = {}
    for r in admin.q_acquisitions(companies, board):
        if r.get("id"):
            says.setdefault(r["id"], str(r.get("says") or r.get("note") or "").strip())
    for r in acquisitions.q_acquisitions(companies, board):
        says.setdefault(r["id"], str(r.get("says") or "").strip())
    return says


def holders_of(companies: list) -> dict:
    """{board key: [ids of the companies already reading from that board]}."""
    out = collections.defaultdict(list)
    for c in companies:
        a = c.get("ats") or {}
        if a.get("type") in (None, "unknown", "html") or a.get("ref") is None:
            continue
        out[board_key(a["type"], a["ref"])].append(c["id"])
    return out


def shown_now(board: dict) -> dict:
    """{company id: how many postings the public board shows for it today}.

    NOT EVERY WAITING COMPANY SHOWS NOTHING. `html` means a careers page and
    not a feed, and it does not mean unread: on 2026-11-18, 114 of those
    pages were read every night and put 1,357 postings on the board. The
    first draft of this door took the whole type for empty, and would have
    swapped a page that reads eleven roles for a board that reads none.
    """
    out = {}
    for o in (board.get("organizations") or []) if isinstance(board, dict) else []:
        if isinstance(o, dict) and isinstance(o.get("id"), str):
            out[o["id"]] = _count(o.get("open_postings")) or 0
    return out


def ownership_on_file(c: dict) -> list:
    """What the record already says about who owns this company or its board.

    Any of these is the owner's by his ruling: a company with a parent may
    well hire through the parent's board, and the board its site points to is
    then exactly the one that must not be wired without a person.
    """
    name = c.get("name") or c.get("id")
    out = []
    parent = str(c.get("parent") or "").strip()
    acq = c.get("acquired") or c.get("acquired_by")
    if acq:
        who = str((acq.get("parent") if isinstance(acq, dict) else acq) or "").strip()
        year = acq.get("year") if isinstance(acq, dict) else None
        out.append(f"{name} is on file as acquired"
                   + (f' by "{who}"' if who else "") + (f" in {year}" if year else ""))
        if parent and parent == who:
            parent = ""                     # one fact, said once
    if parent:
        out.append(f'"{parent}" is on file as the parent of {name}')
    owner = str(c.get("board_owner") or "").strip()
    if owner:
        out.append(f'a board owner is already on file for {name}: "{owner}"')
    return out


def standing(companies: list, board: dict, stores: dict | None = None) -> dict:
    """Everything a plan holds a company against, read once, plus the three
    places the plan remembers what a board answered (`total`, `read`, `said`),
    each filed under the board's exact spelling. `stores` is read_stores()
    as cmd_plan read it; left out, it is read here, and a file that cannot be
    used raises rather than hold nothing."""
    s = _stores(stores)
    by_id = {c["id"]: c for c in companies}
    names = {cid: c.get("name") for cid, c in by_id.items()}
    doubts = standing_doubts(s["doubts"], s)
    return {"by_id": by_id,
            "holders": holders_of(companies),
            "suspects": _suspects(s),
            "refusals": _refusals(s),
            "waiting": _waiting(s),
            "doubts": doubts,
            # what each company's doubt can reach, and every board the owner
            # holds under any company's id
            "rows": board_proposals.rows_by_id(s["found"]),
            "ruled": s["ruled"],
            "held_boards": held_boards(doubts, s, names),
            # Workable account numbers on file, by number: one this run does
            # not resolve may be any Workable board found
            "numbers": numbers_on_file(companies),
            "acquisitions": acquisition_doubts(companies, board),
            "shown": shown_now(board),
            "crawled": str((board or {}).get("generated") or "")[:10],
            "total": {}, "read": {}, "said": {}}


# ------------------------------------------------- a board named by a number

def numbered(kind, ref) -> bool:
    """A Workable board written as its account number, not its slug.

    A page that embeds Workable's widget asks for the board by the account's
    NUMBER (apply.workable.com/api/v1/widget/accounts/710855), and a rendered
    read of that page sees nothing else. The number reads - it is the address
    the crawl's own reader asks - and it is not an address a person can open:
    apply.workable.com/710855 is Workable's error page, and the public site
    builds its "go and look" link from the ref (build_board.board_url). The
    first real run of this door would have published exactly that dead link
    for Archistar; a blind reviewer opened it (2026-11-19). Asked of the ref
    as the reader reads it: "710855/" is the same number.
    """
    return kind == "workable" and isinstance(ref, str) and _number(ref).isdigit()


def _number(ref) -> str:
    """A Workable ref as the reader asks it: no space, no last slash."""
    return ref.strip().rstrip("/") if isinstance(ref, str) else ""


def account_words(row: dict) -> str:
    """'asks for it as Workable account 710855; its postings open under
    apply.workable.com/archistar' - the number their site asks by, kept on the
    record and on the queue row, so that if the slug ever stops answering the
    file still says which account the site embeds. "" for any other board."""
    if not row.get("account"):
        return ""
    return (f"asks for it as Workable account {row['account']}; its postings open "
            f"under apply.workable.com/{row.get('ref')}")


def workable_slug(number: str, ctx: dict) -> tuple[str | None, str, str]:
    """(the slug of the Workable board with this account number, how it is
    known, "found") - or (None, why it could not be found, what kind of
    "not found" it is):

      "hold"       an answer: no posting to follow, a robots.txt that
                   disallows the follow, a landing on no board, on another
                   posting or back under the number, a board under the slug
                   that lists other postings, or a failure that will come
                   back (a 404, a 400). The board goes to a person.
      "transient"  the follow, or the read under the slug, failed ON THE
                   DAY (verify_boards.transient: the network, a 5xx, a 429,
                   a robots.txt that did not answer). Not an answer: the row
                   is skipped and the next plan asks again - filing it would
                   park the number in the owner's queue, where a door-filed
                   row then holds the slug from being wired.
      "giant"      the number's own board holds GIANT rows or more. Nothing
                   is followed and the slug is not read: it is skipped with
                   the giants whatever its slug is.
      "unread"     the board did not read under its number; consider skips
                   it with that error, from the same read.

    The feed carries no slug. Each posting on it has a short address
    (apply.workable.com/j/<code>) that opens at the board's own
    (apply.workable.com/<slug>/j/<code>), so ONE posting is followed to where
    it lands - robots.txt asked at each hop, no page read, and only a 200 is
    a landing (add_company.lands_at) - and the slug is read off the landing
    address. Then THE SLUG IS HELD TO THE NUMBER: the board is read once under
    the slug and must list exactly the postings the number listed. An address
    that merely looks like a slug is how a board gets wired to the wrong
    company, so anything short of the same board is "could not be found", and
    the board goes to a person.

    Both reads are kept in ctx["read"] under their own spellings, so the plan
    does not ask the board again.
    """
    def read(ref):
        exact = spelling("workable", ref)
        if exact not in ctx["read"]:
            ctx["read"][exact] = (*add_company.read_board({"type": "workable", "ref": ref}),
                                  admin.now())
        return ctx["read"][exact]

    ok, detail, rows, _at = read(number)
    if not ok:
        return None, f"the board did not read under its number: {detail}", "unread"
    if len(rows) >= GIANT:
        return None, (f"it holds {len(rows)} postings under the number, so it is skipped "
                      f"with the giants and no posting was followed"), "giant"
    urls = sorted(str(r.get("url") or "").strip() for r in rows)
    short = next((u for u in urls if _WORKABLE_SHORT.fullmatch(u)), None)
    if not short:
        return None, ("it lists no posting whose address could be followed to the board's "
                      "own"), "hold"
    code = _WORKABLE_SHORT.fullmatch(short).group(1)
    try:
        landed = str(add_company.lands_at(short) or "").strip()
    except Exception as exc:                    # noqa: BLE001 - a refusal is an answer
        words = privacy.redact(" ".join(str(exc).split()))[:120]
        if verify_boards.transient(exc):
            return None, (f"following one of its postings ({short}) to the board's own "
                          f"address failed on the day: {words}"), "transient"
        return None, f"following one of its postings ({short}) to the board failed: {words}", \
            "hold"
    m = _WORKABLE_PAGE.fullmatch(landed.split("?", 1)[0].split("#", 1)[0].rstrip("/"))
    slug = m.group(1) if m else ""
    if not m or m.group(2) != code or slug.isdigit():
        return None, (f"one of its postings opens at {landed[:120] or 'no address'}, which "
                      f"names no board"), "hold"
    ok, detail, under_slug, _at = read(slug)
    if not ok:
        if verify_boards.said_transient(detail):
            return None, (f"workable/{slug}, where its postings open, did not read on the "
                          f"day: {detail}"), "transient"
        return None, f"workable/{slug}, where its postings open, did not read: {detail}", "hold"
    if urls != sorted(str(r.get("url") or "").strip() for r in under_slug):
        return None, (f"workable/{slug}, where one of its postings opens, does not list the "
                      f"same postings as account {number}"), "hold"
    return slug, f"its postings open under apply.workable.com/{slug}", "found"


def named_by_number(results: dict, ctx: dict) -> None:
    """Give every Workable board the finder wrote as a number its slug.

    Done to the finder's results BEFORE anything is keyed, so that the board
    has its slug's key everywhere after; a resolved board also keeps its
    number (`account`), and board_keys() compares it under both, because a
    record, a refusal or a waiting row may hold the same account by number.
    A company a person already answered about is not asked: out of scope, a
    board already set, or ANY refusal on file in either store (the same
    cheap questions `consider` asks first - never read a board to propose it
    again); a refused company's numbers are marked `declined`, and consider
    skips it as refused. Where the slug cannot be found the number stays,
    marked with why: `no_address` (consider holds it), `follow_failed`
    (failed on the day: consider skips it and the next plan asks again) or
    `too_big` (a giant: consider skips it).
    """
    found = ctx.setdefault("slugs", {})
    for cid in sorted(results):
        c = ctx["by_id"].get(cid)
        if c is None or c.get("out_of_scope") \
                or (c.get("ats") or {}).get("type") not in WAITING:
            continue
        if ctx["refusals"].get(cid):
            # NOT ASKED, AND SAID SO. consider skips such a company as
            # refused: nothing is read, probed or filed under a number nobody
            # followed, and no row says "could not be found" of a follow that
            # was never made (second review, 2026-11-19)
            for cand in results[cid].get("candidates") or []:
                if numbered(cand.get("type"), cand.get("ref")):
                    cand["declined"] = "refused"
            continue
        for cand in results[cid].get("candidates") or []:
            if not numbered(cand.get("type"), cand.get("ref")):
                continue
            number = _number(cand["ref"])
            if number not in found:
                found[number] = workable_slug(number, ctx)
            slug, how, kind = found[number]
            if slug:
                cand["ref"], cand["account"] = slug, number
            elif kind == "transient":
                cand["follow_failed"] = how
            elif kind == "giant":
                cand["too_big"], cand["no_address"] = True, how
            else:
                cand["no_address"] = how


def board_keys(board: dict, ctx: dict) -> list:
    """Every key one board is known by, for the questions where boards meet:
    a holder on file, a twin in this run, a refusal, a waiting row.

    Its own key, and for a Workable board its site asks for by NUMBER, the
    number's key as well as the slug's: board_key('workable', '710855') is
    not board_key('workable', 'archistar'), and a record or a refusal that
    holds the account by number has to be seen to be the same board. A
    number the pre-pass did not resolve for this company (a company it does
    not ask about) keeps the number's key, and gains the slug's when the same
    number was resolved for another company in this run - no extra request.
    """
    keys = {board["key"]}
    slugs = ctx.get("slugs") or {}
    for s in board.get("seen") or []:
        if s.get("account"):
            keys.add(board_key("workable", s["account"]))
        elif numbered(board["type"], s.get("ref")):
            got = slugs.get(_number(s["ref"]))
            if got and got[0]:
                keys.add(board_key("workable", got[0]))
    return sorted(keys)


# ------------------------------------------------------------------ the plan

def _skip(base: dict, kind: str, why: str, **more) -> dict:
    return {"action": "SKIP", **base, "kind": kind, "why": why, **more}


def _carry(doubts: list) -> dict:
    """A SKIP made after the doubts were gathered carries them: a reader's
    words, a parent on file, the board's own count. Skipped is not cleared,
    and a skipped row that dropped them would show nobody what was in doubt."""
    return {"doubts": list(doubts)} if doubts else {}


# What the plan and the look say of a company a reader doubted whose doubt
# reaches no new queue row. The doubt itself was kept by `plan --doubts`
# before it asked anything, so "kept" is true when this is printed; what it
# can reach is said in reach's words.
UNFILED = ("a reader's doubt reaches no new row: its row is skipped ({kind}); the doubt "
           "is kept standing (data/board_doubts.json), and {reach}")
UNQUEUED = ("a reader's doubt reaches no new row: the queue cannot take the row ({why}); "
            "the doubt is kept standing (data/board_doubts.json), and {reach}")


def _pages_note(entry: dict) -> str:
    """A page we could not read is not a page without a board; say which."""
    status = collections.Counter(str(p.get("status") or "?") for p in entry.get("pages") or [])
    if status.get("read"):
        return f"{many(status['read'], 'page')} read, none named a board"
    if status:
        return ("no page could be read (" + ", ".join(
            f"{n} {s}" for s, n in sorted(status.items())) + "), so this is not an answer")
    return "no page was read" + (f": {entry['errors'][0][:120]}" if entry.get("errors") else "")


def consider(cid: str, entry: dict, boards: list, unusable: list,
             claims: dict, ctx: dict) -> dict:
    """One company: WIRE, HOLD or SKIP, with everything a later reader needs.

    The cheap questions come first and the network last, so a company a
    person already answered about is never asked of anybody: one ruled out of
    scope is not probed at all, and a board somebody refused is not read
    again to be proposed again.
    """
    c = ctx["by_id"].get(cid)
    base = {"id": cid, "name": (c or {}).get("name") or entry.get("name") or cid}
    if c is None:
        return _skip(base, "gone", "no company with this id is on file")
    if not boards:
        if unusable:
            # THEIR SITE DID NAME A BOARD, and the finder wrote it down in a
            # shape no reader takes. That is not "no board seen": it is
            # listed under its own name so somebody looks at the reference
            return _skip(base, "unusable",
                         "their site named a board, and the finder's reference cannot be "
                         "used: " + "; ".join(unusable[:3]))
        return _skip(base, "no-candidate", "the finder saw no board on their own site: "
                     + _pages_note(entry))

    first = boards[0]
    kind, key = first["type"], first["key"]
    # the board under every key it is known by: a Workable board asked for by
    # number meets a holder, a twin, a refusal or a waiting row under either
    keys = board_keys(first, ctx)
    was = c.get("ats") or {}
    website = c.get("website") or ""
    seen, own = best_seen(first, website, entry.get("pages") or [])
    ref = seen["ref"]                   # as the quoted page spelled it
    base.update({"type": kind, "ref": ref,
                 "was": {"type": was.get("type"), "ref": was.get("ref")}})
    check_1 = {"page": seen["page"], "element": seen["element"], "saw": seen["saw"],
               "times": seen["times"], "hop": seen["hop"], "on_their_site": own}
    if seen.get("landed"):
        check_1["landed"] = seen["landed"]
    base["check_1"] = check_1
    account = seen.get("account") or next(
        (s["account"] for s in first["seen"] if s.get("account")), None)
    if account:
        base["account"] = account              # the number their site asks for it by
    # WHAT THE CHECKS ARE JUDGED AGAINST, kept on the row. Check 1 is about a
    # website and check 2 about a name, and both are the record's as it reads
    # at this moment. apply compares them with the record as it reads THEN.
    base["judged"] = {"name": c.get("name"), "also_known_as": _akas(c), "website": website}

    scope = c.get("out_of_scope")
    if scope:
        on = scope.get("on") if isinstance(scope, dict) else None
        return _skip(base, "out-of-scope",
                     f"a person ruled {base['name']} out of the board"
                     + (f" on {on}" if on else "") + "; it is not probed or proposed")
    if was.get("type") not in WAITING:
        return _skip(base, "not-waiting",
                     f"already reads from {show(was.get('type'), was.get('ref'))}; "
                     f"this door never overrules a board somebody already set")

    # NEVER RE-PROPOSE WHAT SOMEBODY ALREADY SAID NO TO. The same board
    # refused is an answer. A refusal in the desk's own store also hides the
    # company from that queue for good, so even a different board could not
    # be shown there: it is said out loud here for the owner to be told.
    refused = ctx["refusals"].get(cid) or []
    # A NUMBER NOBODY ASKED ABOUT MAY BE THE BOARD THAT WAS REFUSED. A
    # refused company's Workable number is not followed to its slug (the
    # pre-pass asks nobody about a company somebody said no to), so whether
    # it is the Workable board refused under its slug is not known - and it
    # is taken to be: the answer that proposes nothing again.
    unknown = numbered(kind, ref) and len(keys) == 1
    same = [r for r in refused if r["key"] in keys]
    maybe = [r for r in refused if unknown and r not in same and r.get("type") == "workable"
             and not numbered("workable", r.get("ref"))]
    desk = [r for r in refused if r["store"] == "Boards we found"]
    # A REFUSED COMPANY'S NUMBER WAS NOT FOLLOWED (named_by_number marks it
    # `declined`), so whatever it refused, nothing is read, probed or filed
    # under that number: filed, the row said "the board's own address could
    # not be found from it" of a follow nobody made, and the desk cannot
    # accept a number (second review, 2026-11-19).
    declined = refused and numbered(kind, ref)
    if same or maybe or desk or declined:
        r = (same or maybe or desk or refused)[0]
        why = (f"refused earlier in {r['store']}: {r['by']} on {r['on']}"
               + (f" - {r['why']}" if r["why"] else ""))
        if not same and (maybe or declined):
            got = (ctx.get("slugs") or {}).get(_number(ref)) or (None,)
            why += (f". The board found now is {show(kind, ref)}, a Workable account "
                    f"number, and its slug was not looked for - a company somebody said "
                    f"no to is not asked of anybody"
                    + (f" (the same number was followed for another company in this run: "
                       f"its postings open under apply.workable.com/{got[0]})" if got[0] else "")
                    + (f"; so it is taken to be the board refused, {r['board']}" if maybe
                       else f"; the board refused then was {r['board'] or 'not recorded'}")
                    + " - tell the owner")
        elif not same:
            why += (f". The board ruled on then was {r['board'] or 'not recorded'}, the "
                    f"one found now is {show(kind, ref)}, and a ruled company is never "
                    f"shown in that queue again - tell the owner")
        return _skip(base, "refused", why)

    # --- everything on file that argues with wiring it, in words -----------
    doubts = []
    if not own:
        read_as = [s for s in entry.get("read_as") or []
                   if not page_belt._same_site(s, website)]
        doubts.append(f"the page that named the board, {seen['page'] or '(none recorded)'}, "
                      f"is not on their website ({website or 'none on file'})"
                      + (f"; the finder read them at {read_as[0]}" if read_as else ""))
    elif seen["element"] == "text":
        # CHECK 1 IS THE SITE POINTING AT THE BOARD. A slug in a sentence is
        # the site mentioning one, and that is how an old board stays on a
        # page: City Detect's left answering, and still naming them, after
        # the site had moved to another platform
        doubts.append("their site names this board only in plain text - no link, embed, "
                      "script or redirect - and a slug left in a sentence can be an old board")
    elif seen["element"] not in STRENGTH:
        doubts.append(f"the finder recorded a kind of reference this door does not know "
                      f"({seen['element'] or 'none'!r}), so how their site points at the "
                      f"board is not known")
    if len(boards) > 1:
        doubts.append("their site points to more than one board: " + " and ".join(
            f"{show(b['type'], ref if b is first else b['seen'][0]['ref'])} "
            f"(seen {many(b['times'], 'time')})" for b in boards[:4]))
    if numbered(kind, ref) and seen.get("no_address"):
        # ONLY WHEN A FOLLOW WAS MADE AND ANSWERED. A follow that failed on
        # the day is skipped below and asked again; a number nobody followed
        # says so - "could not be found" is a claim about an attempt.
        doubts.append(f"their site asks for this board by its Workable account number "
                      f"({ref}), and the board's own address could not be found from it "
                      f"({seen['no_address']}): apply.workable.com/{_number(ref)} is "
                      f"Workable's error page, so the board is wired by its slug or not at "
                      f"all, and its slug has to be found first (the public site and the "
                      f"desk link their own site for a number, never that address)")
    elif numbered(kind, ref) and not seen.get("follow_failed"):
        doubts.append(f"their site asks for this board by its Workable account number "
                      f"({ref}), and its slug was not looked for: apply.workable.com/"
                      f"{_number(ref)} is Workable's error page, so the board is wired by "
                      f"its slug or not at all")
    if first.get("spellings"):
        doubts.append(f"their site spells this board more than one way "
                      f"({' and '.join(first['spellings'][:4])}); where case matters those "
                      f"are different boards, and only {show(kind, ref)} was read")
    if unusable:
        doubts.append("their site also names a board this door cannot use ("
                      + "; ".join(unusable[:3]) + ")")
    extra = also_seen(entry, keys, website, kind)
    if extra.get("elsewhere"):
        doubts.append(f"their own site also names {' and '.join(extra['elsewhere'])}, a job "
                      f"platform the crawl cannot read, so which board they hire through "
                      f"today is not settled")
    doubts += ownership_on_file(c)
    note = str(c.get("ats_note") or "").strip()
    if note:
        # a note on a company with no board is somebody saying something
        # about its board - "left unwired on purpose", "their own /careers
        # page reads 11" - and wiring would write over it
        doubts.append(f'a note about its board is already on file, and wiring would '
                      f'replace it: "{note[:220]}"')
    if cid in ctx["suspects"]:
        doubts.append(f"discovery already doubted a board for this company "
                      f"({ctx['suspects'][cid]})")
    if cid in ctx["acquisitions"]:
        # the queue's line for a slug suspect IS discovery's note, just said
        said_q = ctx["acquisitions"][cid]
        fresh = said_q and said_q != ctx["suspects"].get(cid)
        doubts.append("it is in the Acquisitions queue"
                      + (f": {said_q[:220]}" if fresh else ""))
    by_id = ctx["by_id"]
    holding = list(dict.fromkeys(i for k in keys for i in ctx["holders"].get(k, [])
                                 if i != cid and i in by_id))
    held = [by_id[i]["name"] for i in holding]
    if held:
        doubts.append(f"{show(kind, ref)} is already on file as the board of "
                      + " and ".join(held[:4])
                      + (f" (under the account number {account} or its slug)"
                         if account else ""))
    twins = [(by_id.get(i) or {}).get("name") or i
             for i in dict.fromkeys(i for k in keys for i in claims.get(k, [])) if i != cid]
    if twins:
        doubts.append(f"the same board was found for {' and '.join(twins[:4])} in this run")
    # A BOARD THE OWNER HOLDS FOR ANOTHER COMPANY'S ID is held here too: the
    # board of a standing doubt, or of an unruled row a door filed, under
    # either Workable key (held_boards; this company's own are said below)
    for words in dict.fromkeys(h["words"] for k in keys for h in ctx["held_boards"].get(k, [])
                               if h["id"] != cid):
        doubts.append(words)
    # A WORKABLE NUMBER ON FILE THAT NOBODY IN THIS RUN FOLLOWED may be this
    # very board under its other key: a record set to workable/710855 by hand
    # and a site linking workable/archico would be two records on one account
    if kind == "workable":
        doubts += unasked_numbers(ctx["numbers"], ctx.get("slugs") or {},
                                  {i: c.get("name") for i, c in by_id.items()},
                                  ignore={_number(ref)} if numbered(kind, ref) else ())
    for r in refused:
        doubts.append(f"an earlier board for this company was refused ({r['board']}, "
                      f"{r['by']} on {r['on']}" + (f": {r['why']}" if r["why"] else "") + ")")
    for r in ctx["waiting"].get(cid) or []:
        if r["key"] not in keys:
            doubts.append(f"a different board, {r['board']}, is already waiting in "
                          f"{r['store']} for this company")
        elif r.get("filed"):
            # A DOUBT THIS DOOR FILED IS THE OWNER'S UNTIL HE RULES. The same
            # board found again used to be "the question already asked, which
            # the two checks may now answer" - and for a row filed on a doubt
            # the checks do not see, that answer overrules the doubt. JustFOIA
            # was filed on two readers' doubt ("an MCCi brand") on 2026-11-19
            # with both checks passing; the next plan, run on the sweep, had it
            # WIRE. A row an older finder left, with no doubt recorded by a
            # door, may still be answered by the checks.
            f = r["filed"]
            doubts.append(f"this board is already in the owner's queue ({r['store']}), filed "
                          f"{f.get('on') or '?'} by {f.get('by')} with its doubt: "
                          f"\"{(r.get('why') or 'none recorded')[:240]}\"; a doubt filed "
                          f"there is his to settle")
    reviewed = str((ctx.get("reviewed") or {}).get(cid) or "").strip()
    stands = (ctx.get("doubts") or {}).get(cid) or []
    holds = reach(c, (ctx.get("rows") or {}).get(cid) or [],
                  (ctx.get("ruled") or {}).get(cid), stands)[1]
    for d in stands:
        # A READER'S DOUBT STANDS UNTIL THE OWNER RULES ON A ROW THAT CARRIED
        # IT. It used to hold the board for the one plan that was given it:
        # JustFOIA was filed on two readers' doubt and the next plan had it
        # WIRE, and a doubted board that ended SKIP that day was filed
        # nowhere at all. `plan --doubts` keeps every doubt it is given
        # (data/board_doubts.json) before it asks anything, each its own
        # record, and every one holds the company here - in the words, and
        # saying what it holds until (reach). The words this plan was given
        # are said once, below.
        if str(d.get("why") or "").strip() != reviewed:
            doubts.append(doubt_words(d, holds))
    if reviewed:
        # A PLAN IS READ BEFORE IT IS WRITTEN, by readers who try to show each
        # board is somebody else's. What one of them could not settle is a
        # doubt like any other: it holds the board and goes to the owner in
        # the reader's words, through the same door and the same journal
        # entry as every other held row (plan --doubts).
        doubts.append(f"a reader checking this plan doubted it: {reviewed}")

    # --- the network: three questions, each asked once per spelling --------
    # From here on every SKIP carries the doubts gathered above (_carry).
    if seen.get("follow_failed"):
        # THE SLUG'S QUESTION DID NOT GET THROUGH, WHICH IS NOT AN ANSWER.
        # Held, the number went to the owner's queue - where a door-filed row
        # then holds the slug from being wired by any later plan - on a
        # timeout. Listed with the reason, and asked again by the next plan.
        return _skip(base, "unreadable",
                     f"their site asks for this board by its Workable account number "
                     f"({ref}), and {seen['follow_failed']}; it is asked again on the next "
                     f"plan, and a failure on the day is not a doubt to file",
                     error=seen["follow_failed"], **_carry(doubts))
    block = {"type": kind, "ref": ref}
    exact = spelling(kind, ref)
    # HOW BIG, BEFORE ANYTHING ELSE, where the read cannot say. One request
    # instead of the hundred and twenty a full Workday read of a giant took
    # before it was skipped anyway.
    if kind in PARTIAL:
        if exact not in ctx["total"]:
            ctx["total"][exact] = add_company.board_total(block)
        total, how = ctx["total"][exact]
        total = _count(total)
        if total is None:
            return _skip(base, "size-unknown",
                         f"size unknown - {PARTIAL[kind]}, and the board's own total could "
                         f"not be had ({how}). A board nobody can show to hold fewer than "
                         f"{GIANT} postings is skipped with the giants", **_carry(doubts))
        base["board_total"] = total
        if total >= GIANT:
            return _skip(base, "giant", f"{GIANT_WHY} (the board holds {total} postings)",
                         **_carry(doubts))

    # The live read. Two companies whose sites name one board are both held,
    # and neither needs the board asked twice to say so.
    if exact not in ctx["read"]:
        ctx["read"][exact] = (*add_company.read_board(block), admin.now())
    ok, detail, read, base["read_at"] = ctx["read"][exact]
    if not ok:
        # kept with its error and never retried: a refusal is an answer for
        # today, and a board that will not read is not a board that is wrong
        return _skip(base, "unreadable", f"the board did not read: {detail}", error=detail,
                     **_carry(doubts))
    if seen.get("too_big"):
        # read once, under the number, and found to be a giant before any
        # posting was followed: it is not asked whose it is either
        return _skip(base, "giant", f"{GIANT_WHY} ({seen.get('no_address')})",
                     **_carry(doubts))
    rows_read = [str(r.get("title") or "").strip() for r in read]
    rows_read = [t for t in rows_read if t]
    # A BOARD IS SIZED BY ITS OPENINGS, NOT BY ITS ROWS. The Rippling reader
    # hands back the company logo's file name as a row on every board
    # ("logo-justfoia-dots-3000px.png"); a talent-pool page and a template
    # left unfilled are rows too. The public board drops all of them through
    # one question, roles.not_a_listing, and this door asks the same one: the
    # first real batch said "3 postings" of a board with 2, and an EMPTY
    # Rippling board would have read as one posting and walked past the line
    # below that holds a board nobody is hiring through.
    titles = [t for t in rows_read if not roles.not_a_listing(t, c.get("name"), *_akas(c))]
    if len(titles) != len(rows_read):
        detail = (f"{detail}; {many(len(titles), 'opening')} among them (the rest are not "
                  f"openings: a logo's file name, a talent pool, a template)")
    if kind in PARTIAL:
        # said where the count is said: "9 postings" from a reader that
        # filters is not the size of the board, and whoever rules on the row
        # should not have to know which readers those are
        detail = f"{detail} - {PARTIAL[kind]}, and the whole board holds {total}"
    shown = ctx["shown"].get(cid, 0)
    base.update({"postings": len(titles), "verified": detail, "titles": titles[:SAMPLE],
                 "shown_before": shown})
    if len(titles) >= GIANT:
        return _skip(base, "giant", f"{GIANT_WHY} ({len(titles)} postings read)",
                     **_carry(doubts))

    if exact not in ctx["said"]:
        ctx["said"][exact] = verify_boards.board_says(kind, ref)
    said = ctx["said"][exact]
    stated_n = _count(said.get("n"))
    if stated_n is not None and stated_n >= GIANT:
        # whatever a probe counts, it does not count more than is there: a
        # board that states 500 holds 500. (It may hold more than it states,
        # which is why a SMALL count from a probe is never read as small.)
        return _skip(base, "giant", f"{GIANT_WHY} (the board states {stated_n} postings)",
                     **_carry(doubts))
    how = str(said.get("how") or "the identity probe failed").strip()
    if said.get("transient") is True:
        # THE QUESTION DID NOT GET THROUGH, WHICH IS NOT AN ANSWER. The board
        # read; asking it whose it is timed out, or the server erred or said
        # to slow down. The first real run filed exactly that in the owner's
        # queue as "the board states no employer name" (Schneider Geospatial,
        # 2026-11-18): a row for him to rule on that the next plan would have
        # answered by itself. So it is listed here with its error and asked
        # again next time. BY CAUSE, NOT BY `failed`'s LABEL: verify_boards
        # files a page that leads off to another host, an answer in a shape
        # nobody knows and a 400 as "unreadable" too, and those come back
        # every day - skipped on that label, they never reached a person.
        # They, a page that refused and a page that is gone are held below
        # like any other board that names nobody, with the reason.
        return _skip(base, "unreadable",
                     f"the board read, and asking it whose it is failed on the day "
                     f"({how}); it is asked again on the next plan, and a failure on the "
                     f"day is not a doubt to file", error=how, **_carry(doubts))
    verdict = verify_boards.proven(c, said)
    names = [str(n).strip() for n in (said.get("names") or []) if str(n).strip()]
    stated = said.get("stated") is True
    # BOTH LOCKS, NOT ONE. proven() is asked, and its answer is held to its
    # own contract here: a pass on a board that stated no name itself is a
    # name-shaped guess wearing a verdict, which is how Concourse got in.
    passed = bool(verdict.get("ok")) and stated and bool(names)
    why_2 = str(verdict.get("why") or "").strip()
    if verdict.get("ok") and not passed:
        why_2 = ("the identity check passed a board that states no employer name "
                 "of its own, and a name read off a slug or an address is not proof")
    if not why_2:
        why_2 = ("the board states no employer name" if not (stated and names)
                 else "the board's stated name was not shown to be this company's")
    calls = (None if not (stated and names) else names[0] if len(names) == 1
             else " / ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else ""))
    # THE NAME KEPT IS ONE THE BOARD STATED, in the board's own spelling: it
    # goes on the record as what the board calls itself, and "Acme, Inc." is
    # only worth recording because it is not how we wrote it.
    judged = str(verdict.get("name") or "").strip()
    if passed and judged not in names:
        judged = names[0]
    base["check_2"] = {"ok": passed,
                       "name": judged or (names[0] if names else None),
                       "names": names, "stated": stated,
                       "how": said.get("how"), "why": why_2}
    if extra:
        base["also_seen"] = extra

    # WHAT THE READ ITSELF ARGUES. A board with nothing on it shows nothing
    # about where they hire today - an account left behind answers empty and
    # still states the name - and wiring one publishes "no openings" for a
    # company that may have eleven on a page nothing reads. A board that
    # reads fewer than the public board already shows for them takes roles
    # off it. Neither is this door's to decide.
    if not titles:
        doubts.append("the board read no postings, so nothing shows it is the board they "
                      "hire through today")
    elif len(titles) < shown:
        doubts.append(f"the public board already shows {many(shown)} for this company "
                      f"(crawl of {ctx['crawled'] or 'an unknown date'}) and this board read "
                      f"{many(len(titles))}; wiring it would take roles off the board")
    if said.get("failed") and how not in why_2:
        # a failure that is not of the day is a reason, and it is said
        doubts.append(f"asking the board whose it is failed in a way that does not pass "
                      f"by itself ({how}), so a person has to look")

    if passed and not doubts:
        # what was already waiting on a person for this company when it was
        # planned (only the same board, from a row no door filed, lets a
        # board through): apply leaves the row alone if anything else is
        # waiting by then
        base["waiting_seen"] = sorted([r["store"], r["key"]]
                                      for r in ctx["waiting"].get(cid) or [])
        return {"action": "WIRE", **base}

    mismatch = not passed and stated and bool(names)
    if not passed:
        doubts.insert(0, why_2)
    lead = f'the board names "{calls}", which is this company, but ' if passed else ""
    asks = account_words(base)
    identity_why = (f"{lead}{'; '.join(doubts)}; {pointer(seen)}"
                    + (f"; their site {asks}" if asks else ""))
    return {"action": "HOLD", **base, "doubts": doubts,
            "queue_row": {"id": cid, "name": base["name"], "was": was.get("type"),
                          "found": {"type": kind, "ref": ref},
                          "saw": seen["saw"], "times": seen["times"],
                          "verified": detail, "board_calls_itself": calls,
                          "identity": "MISMATCH" if mismatch else "unknown",
                          "identity_why": identity_why,
                          "postings": len(titles), "titles": titles[:SAMPLE],
                          "page": seen["page"]}}


def plan(results: dict, companies: list, board: dict, say=print, reviewed=None,
         stores: dict | None = None, numbers: dict | None = None) -> list:
    """Every company in the finder's results, decided. Network happens here.

    Each row is made safe to keep - an address redacted, half a character
    dropped - BEFORE it is printed or kept, so nothing the boards sent is
    ever written or shown in a form the worklist could not hold. `stores` is
    read_stores() as cmd_plan read it (read here when left out). `numbers`,
    when given, is filled with every Workable account number this run
    followed: {number: its slug, or None}.
    """
    ctx = standing(companies, board, stores)
    ctx["reviewed"] = {cid: reader_words(words) for cid, words in (reviewed or {}).items()}
    named_by_number(results, ctx)
    if numbers is not None:
        numbers.update({n: got[0] for n, got in sorted(ctx["slugs"].items())})
    # a record on file holding a Workable NUMBER is the same board as the
    # slug that number was followed to in this run
    for c in companies:
        a = c.get("ats") or {}
        got = ctx["slugs"].get(_number(a.get("ref"))) if numbered(a.get("type"), a.get("ref")) \
            else None
        if got and got[0]:
            ctx["holders"][board_key("workable", got[0])].append(c["id"])
    named, claims = {}, collections.defaultdict(list)
    for cid, entry in results.items():
        named[cid] = boards_named(entry)
        for b in named[cid][0]:
            for k in board_keys(b, ctx):
                claims[k].append(cid)
    rows = []
    for cid in sorted(results):
        boards, unusable = named[cid]
        row = privacy.redact_deep(_encodable(
            consider(cid, results[cid], boards, unusable, claims, ctx)))
        rows.append(row)
        if row["action"] == "SKIP" and row["kind"] == "no-candidate":
            continue                    # most of the file; counted, not listed
        say(_line(row))
    for line in unfiled(rows, ctx["reviewed"], reaches=reaches_of(rows, ctx)):
        say(line)
    return rows


def reader_words(words) -> str:
    """A reader's doubt as it is kept and compared: whole, trimmed, written
    safely, any address taken out. One spelling for the record, the worklist
    and the row, so a doubt is found again by its words."""
    return privacy.redact(_encodable(str(words or "").strip()))


def reaches_of(rows: list, ctx: dict) -> dict:
    """{company id: reach's words} for every row, as the plan's data reads."""
    return {r["id"]: reach(ctx["by_id"].get(r["id"]), (ctx.get("rows") or {}).get(r["id"]) or [],
                           (ctx.get("ruled") or {}).get(r["id"]),
                           (ctx.get("doubts") or {}).get(r["id"]))[1] for r in rows}


def unfiled(rows: list, doubted, why_not: dict | None = None,
            reaches: dict | None = None) -> list:
    """A line for every company a reader doubted whose doubt reaches no new
    queue row: its row was skipped, or (the look's `why_not`) the queue
    cannot take it. The doubt is not lost - the plan kept it before it asked
    anything - and it is never dropped without a word, nor said to wait for
    a ruling it cannot get (`reaches`)."""
    by_id = {r["id"]: r for r in rows}
    out = []
    for cid in sorted(doubted or ()):
        r = by_id.get(cid)
        if r is None:
            continue
        where = (reaches or {}).get(cid) or "it holds the board in every later plan"
        if r["action"] != "HOLD":
            line = UNFILED.format(kind=r.get("kind") or r["action"].lower(), reach=where)
        elif cid in (why_not or {}):
            line = UNQUEUED.format(why=why_not[cid], reach=where)
        else:
            continue
        out.append(f"  NOTE  {str(r['name'])[:28]:<30} {line}")
    return out


def _line(row: dict) -> str:
    """One row of the plan as a person reads it."""
    what = show(row["type"], row["ref"]) if row.get("type") else ""
    head = f"  {row['action']:<5} {str(row['name'])[:28]:<30} {what[:44]:<46}"
    if row["action"] == "WIRE":
        extra, note = row.get("also_seen") or {}, ""
        if extra.get("off_site"):
            note += f"; NOTE a page off their site names {', '.join(extra['off_site'])}"
        if extra.get("elsewhere_off_site"):
            note += (f"; NOTE a page off their site names "
                     f"{', '.join(extra['elsewhere_off_site'])}, which the crawl cannot read")
        if extra.get("advertised_on"):
            note += f"; NOTE their site also links {', '.join(extra['advertised_on'])}"
        return (f"{head} {many(row['postings'])}; board names "
                f"\"{row['check_2']['name']}\"; {pointer(row['check_1'], full=False)}{note}")
    if row["action"] == "HOLD":
        return f"{head} {many(row['postings'])}; {'; '.join(row['doubts'])}"
    return f"{head} {row['why']}" + (f"; in doubt as well: {'; '.join(row['doubts'])}"
                                     if row.get("doubts") else "")


def counts(rows: list) -> dict:
    kinds = collections.Counter(r.get("kind") for r in rows if r["action"] == "SKIP")
    return {"wire": sum(1 for r in rows if r["action"] == "WIRE"),
            "hold": sum(1 for r in rows if r["action"] == "HOLD"),
            "skip": sum(kinds.values()), "skip_kinds": dict(sorted(kinds.items()))}


def skipped_words(n: dict) -> str:
    """'skipped 9 (giants 1, unreadable 2, no board seen on their site 6)'."""
    kinds = n.get("skip_kinds") or {}
    parts = [f"giants {kinds.get('giant', 0)}", f"unreadable {kinds.get('unreadable', 0)}"]
    parts += [f"{words} {kinds[k]}" for k, words in SKIP_WORDS[2:] if kinds.get(k)]
    return f"skipped {n.get('skip', 0)} ({', '.join(parts)})"


def board_named(entry: dict | None, c: dict | None) -> tuple:
    """(show, type, ref) of the board a plan quotes first for this company,
    from the finder's results alone - no request - or (None, None, None). A
    Workable board asked for by number is as the site wrote it: the plan
    resolves it later, and a doubt meets it under either key."""
    boards, _unusable = boards_named(entry or {})
    if not boards:
        return None, None, None
    first = boards[0]
    seen, _own = best_seen(first, (c or {}).get("website") or "",
                           (entry or {}).get("pages") or [])
    return show(first["type"], seen["ref"]), first["type"], seen["ref"]


def new_doubts(reviewed: dict, results: dict, by_id: dict, grouped: dict, taken,
               by: str) -> dict:
    """{record key: record} for every reader's doubt that is not already on
    file in the same words about the same board for the same company -
    WHETHER OR NOT the owner has settled it since. A doubt he settled, given
    again by a re-plan with the same --doubts file, came back as new, with a
    stamp later than his ruling, and stood again (second review,
    2026-11-19). Each new doubt is a record of its own, under a key nothing
    else holds: keeping one is an addition to the file, never a rewrite."""
    on, at = dt.date.today().isoformat(), admin.now()
    taken = set(taken)
    out = {}
    for cid, words in sorted(reviewed.items()):
        board, kind, ref = board_named(results.get(cid), by_id.get(cid))
        if any(str(d.get("why") or "").strip() == words and d.get("board") == board
               for d in grouped.get(cid) or []):
            continue
        key, n = f"{cid}@{at}", 2
        while key in taken:
            key, n = f"{cid}@{at}~{n}", n + 1
        taken.add(key)
        out[key] = {"id": cid, "board": board, "type": kind, "ref": ref,
                    "why": words, "by": by, "on": on, "at": at}
    return out


def keep_doubts(reviewed: dict, results: dict, by: str, worklist: str) -> tuple:
    """Keep the readers' doubts NOW, before this plan asks any board.

    A DOUBT STANDS FROM THE MOMENT IT IS GIVEN. It used to stand from the
    moment the doubted plan's worklist was WRITTEN, so the readers' own
    worklist, planned before they spoke and fresh for a day, still wired the
    board both of them doubted, with the code its own look printed - and a
    doubted plan that was never written kept nothing (second review,
    2026-11-19). So: one journalled ADDITION to data/board_doubts.json under
    the agent's label, before the first request; then the words onto the row
    already waiting for the owner in Boards we found, where there is one
    (board_proposals.add_doubt), so his card shows them. Under the desk lock,
    so a concurrent write is not lost.

    Returns (lines to print, why it stopped or None, the files written).
    """
    lines, wrote = [], []
    with admin.desk_lock():
        try:
            raw = board_proposals.strict(DOUBTS, admin.DATA)
            s = read_stores()
        except board_proposals.Unreadable as exc:
            return lines, f"{exc}; no reader's doubt was kept", wrote
        by_id = {c["id"]: c for c in admin.read("companies.json", [])}
        new = new_doubts(reviewed, results, by_id, s["doubts"], raw, by)
        rows = board_proposals.rows_by_id(s["found"])
        reaches = {cid: reach(by_id.get(cid), rows.get(cid) or [], s["ruled"].get(cid))
                   for cid in reviewed}
        fresh = {r["id"] for r in new.values()}
        if new:
            if len(new) > journal.BLAST:
                lines.append(f"{len(new)} reader doubts in one entry is over the journal's "
                             f"{journal.BLAST}-record limit; the count and every company are "
                             f"printed below, so the write is confirmed")
            by_code = collections.defaultdict(list)
            for cid in sorted(fresh):
                by_code[reaches[cid][0]].append(cid)
            parts = [f"{many(len(new), 'reader doubt')} kept, given to the wiring door's plan "
                     f"before it asked any board (worklist {worklist})"]
            if by_code["row"] or by_code["none"]:
                ids = by_code["row"] + by_code["none"]
                parts.append(f"{len(ids)} hold their company's board in every later plan and "
                             f"every apply, until the owner rules on a row in Boards we found "
                             f"that carried the doubt ({', '.join(ids[:12])})")
            if by_code["ruled"]:
                parts.append(f"{len(by_code['ruled'])} cannot reach the owner's queue - he "
                             f"ruled on the company in Boards we found before, and that queue "
                             f"never shows it again - tell the owner "
                             f"({', '.join(by_code['ruled'][:12])})")
            if by_code["wired"]:
                parts.append(f"{len(by_code['wired'])} are about a company already on a "
                             f"board: wired before the reader's doubt was kept - tell the "
                             f"owner ({', '.join(by_code['wired'][:12])})")
            if by_code["gone"]:
                parts.append(f"{len(by_code['gone'])} name no company on file "
                             f"({', '.join(by_code['gone'][:12])})")
            bad = admin.save_decisions(DOUBTS, dict(raw, **new), DOUBT_ACTION,
                                       why="; ".join(parts), by=by,
                                       force=len(new) > journal.BLAST)
            if bad:
                return lines, (f"the doubts file would refuse the readers' doubts: {bad}; no "
                               f"reader's doubt was kept"), wrote
            wrote.append(f"data/{DOUBTS}")
        # THE WORDS GO WHERE THE OWNER LOOKS: onto the row already waiting for
        # him, every standing doubt of each doubted company it does not carry
        grouped = board_proposals.read_doubts(admin.DATA)
        standing = board_proposals.standing_doubts(grouped, s["ruled"], s["found"])
        carry = {cid: standing[cid] for cid in sorted(reviewed)
                 if cid in standing and reaches[cid][0] in ("row", "unseen")}
        rep = board_proposals.add_doubt(carry, by, write=False, force=True)
        if not rep["error"] and rep["added"]:
            n = len(rep["added"])
            if n > journal.BLAST:
                lines.append(f"{n} rows in Boards we found given a reader's doubt in one "
                             f"entry is over the journal's {journal.BLAST}-record limit; the "
                             f"count and every company are printed below, so the write is "
                             f"confirmed")
            rep = board_proposals.add_doubt(
                carry, by, write=True, force=n > journal.BLAST,
                why=(f"{many(sum(rep['added'].values()), 'reader doubt')} put on the rows "
                     f"already waiting for the owner, so his card shows them: the queue "
                     f"takes one row per company (worklist {worklist})"))
            if not rep["error"]:
                wrote.append(f"data/{board_proposals.FOUND.name}")
        if rep["error"]:
            return lines, (f"the readers' doubts are kept in data/{DOUBTS}, and their words "
                           f"could not be put on the rows already waiting for the owner: "
                           f"{rep['error']}"), wrote
        # what each doubt can reach, said of the files as they are now
        try:
            s = read_stores()
        except board_proposals.Unreadable as exc:
            return lines, f"{exc}, after the readers' doubts were kept", wrote
        standing = board_proposals.standing_doubts(s["doubts"], s["ruled"], s["found"])
        rows = board_proposals.rows_by_id(s["found"])
        reaches = {cid: reach(by_id.get(cid), rows.get(cid) or [], s["ruled"].get(cid),
                              standing.get(cid)) for cid in reviewed}
    names = {cid: (by_id.get(cid) or {}).get("name") or (results.get(cid) or {}).get("name")
             or cid for cid in reviewed}
    lines.append(f"READERS' DOUBTS {len(reviewed)} - kept before any board is asked "
                 f"({len(fresh)} new, {len(reviewed) - len(fresh)} already on file in the same "
                 f"words):")
    for cid in sorted(reviewed):
        board = board_named(results.get(cid), by_id.get(cid))[0]
        lines.append(f"  {'KEPT' if cid in fresh else 'HAD':<5} {str(names[cid])[:28]:<30} "
                     f"{str(board or '-')[:44]:<46} {reaches[cid][1]}")
    return lines, None, wrote


def cmd_plan(a) -> int:
    if not callable(getattr(verify_boards, "proven", None)):
        print("verify_boards.proven is not in this checkout, so CHECK 2 (the board "
              "itself names the company) cannot be run and nothing may be planned "
              "for wiring. Nothing was read and nothing was written.")
        return 2
    out = pathlib.Path(a.out)
    if out.exists():
        print(f"{out} already exists. A worklist is frozen: a look may already have "
              f"been read against it. Give the new plan a new name.")
        return 2
    try:
        inside = out.resolve().is_relative_to(admin.DATA.resolve())
    except OSError:
        inside = False
    if inside:
        print("a worklist does not belong in data/: the nightly job commits "
              "everything there. Write it to a scratch folder.")
        return 2
    try:
        results, files = load_results(a.results)
    except (OSError, ValueError) as exc:
        print(f"could not read the finder's results: {exc}")
        return 2

    by = (a.by or "").strip()
    if (a.doubts or by) and (not by.startswith("agent:") or len(by) <= len("agent:")
                             or "@" in by):
        print("--doubts keeps each doubt the moment it is given, through the journal, "
              "under the agent's own label: give --by agent:<label> (a handle, never an "
              "address). Nothing was read and nothing was written.")
        return 2
    reviewed = {}
    if a.doubts:
        try:
            reviewed = json.loads(pathlib.Path(a.doubts).read_text())
        except (OSError, ValueError) as exc:
            print(f"could not read --doubts: {exc}. Nothing was read and nothing was written.")
            return 2
        if not isinstance(reviewed, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v.strip()
                for k, v in reviewed.items()):
            print("--doubts is one JSON object: a company's id, then the doubt in words. "
                  "Nothing was read and nothing was written.")
            return 2
        unknown = sorted(set(reviewed) - set(results))
        if unknown:
            # a doubt about a company this plan will not look at would be
            # dropped without a word, and the board it was about wired later
            print(f"--doubts names {', '.join(unknown[:6])}, which the finder's results do "
                  f"not hold. Nothing was read and nothing was written.")
            return 2
        reviewed = {cid: reader_words(words) for cid, words in reviewed.items()}
    # EVERY STORE A BOARD IS HELD AGAINST, read before anything else: a file
    # that cannot be read would hold nothing, and every board a refusal, a
    # doubt or a row waiting on the owner stood against would plan as WIRE
    try:
        stores = read_stores()
    except board_proposals.Unreadable as exc:
        print(f"{exc}. A refusal, a reader's doubt or a row waiting on the owner that "
              f"cannot be read cannot hold its board, so nothing may be planned. Nothing "
              f"was read and nothing was written.")
        return 2

    companies = admin.read("companies.json", [])
    board = admin.read("board.json", {})
    if not isinstance(board.get("organizations"), list):
        print("data/board.json has no list of organizations, so what each company "
              "already shows on the public board cannot be compared with the board "
              "found for it. Nothing was read and nothing was written.")
        return 2

    # THE WORKLIST'S NAME IS TAKEN BEFORE THE FIRST REQUEST. The file is
    # written at the very end, after every board has been read; a folder that
    # is not there, or a name somebody else took meanwhile, used to be found
    # out at that last line and threw every read away. Opening it for
    # exclusive creation now proves the folder, the permission and the name
    # in one step, and what is left behind if the plan dies is taken back.
    try:
        with open(out, "x"):
            pass
    except FileExistsError:
        print(f"{out} already exists. Give the new plan a new name.")
        return 2
    except OSError as exc:
        print(f"the worklist cannot be written to {out} ({exc.strerror or exc}). "
              f"Nothing was read. Make the folder first, or name another.")
        return 2
    part = out.with_name(out.name + ".part")
    done = False
    try:
        if reviewed:
            lines, stop, wrote = keep_doubts(reviewed, results, by, out.name)
            for line in lines:
                print(line)
            if wrote:
                print(f"written by {by}: {', '.join(wrote)} and the journal "
                      f"(data/admin_journal.jsonl, with data/admin_journal.archive.jsonl "
                      f"when it rolls). Commit them together: the doubts stand from now, "
                      f"whatever becomes of this plan.")
            if stop:
                print(f"\nSTOPPED before the first request: {stop}. Nothing was read.")
                return 1
            try:
                stores = read_stores()
            except board_proposals.Unreadable as exc:
                print(f"\nSTOPPED before the first request: {exc}. Nothing was read.")
                return 1
            print()
        have = sum(1 for e in results.values() if e.get("candidates"))
        print(f"{len(results)} companies in the finder's results, {have} with a board "
              f"named on their own site")
        for f in files:
            made = _when(f.get("generated"))
            days = (dt.datetime.now().astimezone() - made).days if made else None
            if days is None or days > 3:
                # the boards are read again here; the pages are not. CHECK 1
                # is as old as the finder's run, and a careers page can change
                print(f"NOTE: {f['file']} was written "
                      f"{'at an unknown time' if days is None else f'{days} days ago'}; "
                      f"check 1 is only as fresh as the finder's run")
        print()
        numbers: dict = {}
        rows = plan(results, companies, board, reviewed=reviewed, stores=stores,
                    numbers=numbers)
        n = counts(rows)
        doc = {"made": MADE, "version": VERSION, "generated": admin.now(),
               "doubted_by_a_reader": sorted(reviewed),
               # the readers' own words, redacted like every row; the plan kept
               # each one in data/board_doubts.json before it asked anything
               "reader_doubts": {cid: words for cid, words in sorted(reviewed.items())},
               "doubts_by": by or None,
               # every Workable account number this run followed, and the slug
               # it found (or null): apply holds a Workable board beside any
               # number on file that is not here
               "workable_numbers": numbers,
               "ruling": ("owner, 2026-11-18: wire only when the company's own website "
                          "points to the board AND the board itself names the company; "
                          "anything doubtful goes to Boards we found; giants skipped"),
               "results": files, "counts": n, "rows": rows}
        # ASCII, like every data file here (admin.write_atomic): it cannot
        # fail to encode on any machine, and every character comes back.
        # Written whole under another name and then moved, so a worklist is
        # either all there or not there.
        part.write_bytes((json.dumps(doc, indent=1) + "\n").encode("ascii"))
        os.replace(part, out)
        done = True
    finally:
        if not done:
            part.unlink(missing_ok=True)
            try:
                if out.stat().st_size == 0:
                    out.unlink()        # only ever our own empty placeholder
            except OSError:
                pass
    print(f"\nplan: wire {n['wire']}, hold {n['hold']}, {skipped_words(n)}")
    print(f"frozen in {out}")
    # what the plan wrote is the readers' doubts (above) and nothing else: the
    # rest WILL BE written, and only by the write the look prints
    print("nothing is wired or filed yet: the WIRE rows above will be wired, and the HOLD "
          "rows filed for the owner, when this worklist is applied with --write"
          + ("; the readers' doubts were kept before the first request, and stand "
             "whatever becomes of it" if reviewed else ""))
    print(f"next: python3 scripts/wire_found.py apply {out} --by agent:<label>")
    return 0


# ----------------------------------------------------------------- the apply

def load_worklist(path: str) -> tuple[dict, str]:
    """(the worklist, the sha256 of its bytes). Refuses anything else."""
    raw = pathlib.Path(path).read_bytes()
    doc = json.loads(raw)
    if not (isinstance(doc, dict) and doc.get("made") == MADE
            and doc.get("version") == VERSION and isinstance(doc.get("rows"), list)):
        raise ValueError(f"{path} is not a worklist this `wire_found.py plan` wrote")
    for row in doc["rows"]:
        if not isinstance(row, dict) or row.get("action") not in ("WIRE", "HOLD", "SKIP") \
                or not isinstance(row.get("id"), str):
            raise ValueError(f"{path} has a row that is not WIRE, HOLD or SKIP")
    words = doc.get("reader_doubts")
    if not isinstance(words, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in words.items()):
        # every plan writes it, empty when nobody doubted anything: a file
        # without it cannot say which of its rows a reader stood against
        raise ValueError(f"{path} does not carry its readers' doubts (reader_doubts)")
    if any(not v.strip() for v in words.values()):
        # a doubt with no words holds nothing, and would be read as none
        raise ValueError(f"{path} carries a reader's doubt with no words in it")
    numbers = doc.get("workable_numbers")
    if not isinstance(numbers, dict) or not all(
            isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in numbers.items()):
        # apply holds a Workable board beside any number on file this plan did
        # not follow, and a file that does not say which it followed cannot
        raise ValueError(f"{path} does not say which Workable account numbers its plan "
                         f"followed (workable_numbers)")
    return doc, hashlib.sha256(raw).hexdigest()


def row_keys(row: dict) -> list:
    """Every key a planned row's board is known by: its own, and for a
    Workable board its site asks for by number, the number's (board_keys)."""
    keys = {board_key(row.get("type"), row.get("ref"))}
    if row.get("account"):
        keys.add(board_key("workable", row["account"]))
    return sorted(keys)


def wire_problem(row: dict, by_id: dict, holders: dict, ruled: dict,
                 proposals: dict, twice: set, shown: dict,
                 now: dt.datetime, held_for: dict | None = None) -> tuple[str, str] | None:
    """(code, words) for a WIRE row that must be left alone, or None.

    Not a second plan: nothing is fetched and no board is asked again. Two
    kinds of question. First, does the row carry what the plan only ever
    writes on a WIRE row - both checks, a dated read with postings on it, a
    size under the giants' line - and does nothing in the worklist itself
    stand against it (a reader's doubt), because a row that cannot show them
    is not one this door wires, however it came to say WIRE. Second,
    everything that can have MOVED between the plan and this moment: a
    person wired the company by hand, corrected its website or its name,
    recorded a parent, a note or a refusal, or gave the same board to
    somebody else; a reader's doubt was kept since; or the company was put
    in front of a person since (`held_for`: the worklist's reader doubts, the
    standing doubts and what is waiting, each as it reads NOW).
    """
    held_for = held_for or {}
    cid = row["id"]
    kind, ref = row.get("type"), row.get("ref")
    c1, c2 = row.get("check_1") or {}, row.get("check_2") or {}
    judged = row.get("judged") if isinstance(row.get("judged"), dict) else {}
    n = _count(row.get("postings"))
    if ref_problem(kind, ref) or not c1.get("page") or c1.get("on_their_site") is not True \
            or STRENGTH.get(c1.get("element"), 2) >= 2 \
            or c2.get("ok") is not True or not c2.get("names") or not c2.get("stated") \
            or not n or not row.get("read_at") \
            or not isinstance(row.get("titles"), list) \
            or not isinstance(judged.get("name"), str) \
            or not isinstance(judged.get("also_known_as"), list):
        return "evidence", ("the row does not carry both checks, what they were judged "
                            "against and a live read with postings on it")
    if numbered(kind, ref):
        return "evidence", ("the board is written as a Workable account number, which "
                            "reads and does not open; a board is wired by its slug")
    total = _count(row.get("board_total"))
    if n >= GIANT or (total or 0) >= GIANT:
        return "giant", GIANT_WHY
    if kind in PARTIAL and total is None:
        return "size", (f"{PARTIAL[kind]}, and the row carries no total for the board, so "
                        f"it was never shown to be under {GIANT} postings")
    # THE COUNT IS HELD TO THE TITLES IT CAME WITH. A plan keeps the first
    # SAMPLE of the openings it read, and only openings: a row edited to
    # "postings 1" with no titles, or with a logo's file name for its one
    # title, was wired as "1 posting read" (second review, 2026-11-19).
    titles = row["titles"]
    if len(titles) != min(n, SAMPLE) or any(
            not isinstance(t, str) or not t.strip()
            or roles.not_a_listing(t, judged["name"],
                                   *[str(x) for x in judged["also_known_as"]])
            for t in titles):
        return "evidence", (f"the row's titles do not bear out its {many(n, 'opening')}: a "
                            f"plan keeps the first {SAMPLE} of the openings it read, each "
                            f"one an opening - not a logo's file name, a talent pool or a "
                            f"template")
    keys = row_keys(row)
    if cid in twice or any(k in twice for k in keys):
        return "twice", "the worklist lists this company or this board more than once"
    # A READER'S DOUBT IN THIS WORKLIST IS NEVER WIRED. A doubted row carries
    # every piece of evidence a WIRE row does - both checks passed, which is
    # why it took a reader to doubt it - so a worklist edited from HOLD to
    # WIRE for it used to be wired, with the doubt still in its own header.
    doubted = held_for.get("doubted") or {}
    if cid in doubted:
        return "doubted", (f"a reader checking the plan doubted it: "
                           f"{str(doubted[cid] or '').strip()[:200] or '(no words)'}; a "
                           f"doubted board is the owner's to rule on")
    # "Verify with a real fetch immediately before writing; stored counts go
    # stale." A worklist is planned and applied in one sitting. One left over
    # from another day would wire boards on the strength of a read nobody
    # would now call live, so its rows are left alone and the plan is run
    # again. The limit is the queue door's own (board_proposals.MAX_AGE_HOURS):
    # a board is wired, and a board is filed, on reads of the same freshness.
    old = board_proposals.stale_read(row["read_at"], now)
    if old:
        return "stale", (f"the live read is {old}; a board is read again before "
                         f"it is wired - plan again")
    c = by_id.get(cid)
    if c is None:
        return "gone", "the company is no longer on file"
    now_ats = c.get("ats") or {}
    was = row.get("was") or {}
    if now_ats.get("type") == kind and now_ats.get("ref") == ref:
        return "done", "already reads from this board"
    if now_ats.get("type") not in WAITING or \
            {"type": now_ats.get("type"), "ref": now_ats.get("ref")} != \
            {"type": was.get("type"), "ref": was.get("ref")}:
        return "moved", (f"its board on file changed since the plan: was "
                         f"{show(was.get('type'), was.get('ref'))}, now "
                         f"{show(now_ats.get('type'), now_ats.get('ref'))}")
    if c.get("out_of_scope"):
        return "moved", "it was ruled out of scope since the plan"
    # THE TWO CHECKS THEMSELVES, against the record as it reads now. A
    # website on file that turned out to be another company's of the same
    # name, or a record renamed, used to be wired all the same - with a note
    # quoting the old site as "their page".
    was_aka = sorted(str(x) for x in judged["also_known_as"])
    if c.get("name") != judged["name"] or _akas(c) != was_aka:
        return "moved", (f'its name on file changed since the plan: the board\'s name was '
                         f'judged against "{judged["name"]}"'
                         + (f' (also {", ".join(was_aka)})' if was_aka else "")
                         + f', and the record now reads "{c.get("name")}"'
                         + (f' (also {", ".join(_akas(c))})' if _akas(c) else "")
                         + " - plan again")
    if not page_belt._same_site(c1["page"], c.get("website") or ""):
        return "moved", (f"the page that pointed at the board, {c1['page']}, is not on the "
                         f"website now on file ({c.get('website') or 'none'}) - plan again")
    owned = ownership_on_file(c)
    if owned:
        return "moved", f"since the plan, {owned[0]}"
    if str(c.get("ats_note") or "").strip():
        return "moved", "a note about its board was put on file since the plan"
    v = ruled.get(cid)
    p = proposals.get(f"board:{cid}")
    if (isinstance(v, dict) and not v.get("accept")) or \
            (isinstance(p, dict) and p.get("status") == "rejected"):
        return "moved", "a board was refused for it since the plan"
    # DISCOVERY'S DOUBT AND THE ACQUISITIONS QUEUE, asked again: the readers
    # read a plan for hours, and a slug suspect or an Acquisitions row
    # recorded in that window used to be wired past (second review,
    # 2026-11-19). A WIRE row was planned only for a company in neither.
    sus = (held_for.get("suspects") or {}).get(cid)
    if sus:
        return "moved", f"discovery doubted a board for it since the plan ({sus[:160]})"
    if cid in (held_for.get("acquisitions") or {}):
        said_q = held_for["acquisitions"][cid]
        return "moved", ("it entered the Acquisitions queue since the plan"
                         + (f": {said_q[:160]}" if said_q else ""))
    # A DOUBT KEPT SINCE THE PLAN, and a queue row put in front of a person
    # since. A worklist planned before the readers' doubt stays fresh for a
    # day, and so does the code its look printed: applied after the doubted
    # plan, it wired the board both readers doubted, and the row filed for the
    # owner dropped off his desk (the queue hides a company on a board). The
    # doubt is kept when it is GIVEN (plan --doubts), so every worklist meets
    # it here, whichever was planned first.
    stands = (held_for.get("standing") or {}).get(cid) or []
    if stands:
        return "standing", "since the plan, " + "; and ".join(
            doubt_words(d, (held_for.get("reaches") or {}).get(cid)) for d in stands)
    # the board held for the owner under ANY company's id (held_boards)
    for k in keys:
        for h in (held_for.get("boards") or {}).get(k) or []:
            if h["id"] != cid:
                return h["code"], f"since the plan, {h['words']}"
    # a Workable account number on file that the plan never followed may be
    # this very board under its other key
    if kind == "workable":
        unasked = unasked_numbers(held_for.get("numbers") or {}, held_for.get("resolved") or {},
                                  {i: x.get("name") for i, x in by_id.items()})
        if unasked:
            return "number", f"{unasked[0]} - plan again"
    seen = {tuple(x) for x in row.get("waiting_seen") or []
            if isinstance(x, list) and len(x) == 2}
    for w in (held_for.get("waiting") or {}).get(cid) or []:
        if w.get("filed"):
            # filed by a door, on a doubt: his until he rules, whatever board
            f = w["filed"]
            return "queued", (f"{w['board']} is in the owner's queue ({w['store']}), filed "
                              f"{f.get('on') or '?'} by {f.get('by') or '?'} with its doubt, "
                              f"and a board filed there is his to settle")
        if (w.get("store"), w.get("key")) not in seen:
            return "waiting", (f"{w['board']} was put in front of a person in {w['store']} "
                               f"since the plan, and is his to settle")
    held = [by_id[i]["name"] for i in dict.fromkeys(i for k in keys for i in holders.get(k, []))
            if i != cid and i in by_id]
    if held:
        return "moved", f"{show(kind, ref)} is now on file as the board of {held[0]}"
    if n < shown.get(cid, 0):
        return "moved", (f"the public board now shows {many(shown[cid])} for it, more than "
                         f"the {n} this board read - plan again")
    return None


def queue_rows(holds: list, standing: dict | None = None, reaches: dict | None = None) -> list:
    """What to file for these held boards, each stamped with ITS row's read,
    and carrying every reader's doubt that stands for its company NOW.

    One stamp per row. The queue row carries no time of its own, so there is
    nothing to drift from the one this door judges the row by: a worklist
    whose reads are a month old cannot file them as today's.

    A DOUBT KEPT SINCE THE PLAN GOES ON THE ROW. A row filed after a doubt was
    kept is a row the owner's ruling settles that doubt by (it is filed at or
    after it), so it has to carry the doubt's words: a worklist planned before
    the readers spoke files its rows with the words of that day.
    """
    out = []
    for r in holds:
        q = r.get("queue_row")
        if not isinstance(q, dict):
            out.append(q)
            continue
        q = dict(q, read_at=r.get("read_at"))
        words = q.get("identity_why")
        if isinstance(words, str):
            more = [d for d in (standing or {}).get(r["id"]) or []
                    if str(d.get("why") or "").strip() not in words]
            if more:
                q["identity_why"] = words + "".join(
                    "; " + doubt_words(d, (reaches or {}).get(r["id"])) for d in more)
        out.append(q)
    return out


def note_for(row: dict, today: str) -> str:
    """Both checks in one line, kept on the record as ats_note - and, for a
    Workable board their site asks for by number, the number: nothing else on
    file would say which account the site embeds if the slug stopped
    answering."""
    c1, c2 = row["check_1"], row["check_2"]
    read_on = str(row.get("read_at") or "")[:10]
    asks = account_words(row)
    return (f"wired {today}: {pointer(c1, full=False)}"
            + (f", and {asks}" if asks else "")
            + f"; the board names \"{c2['name']}\"; {many(row['postings'])} read"
            + (f" {read_on}" if read_on and read_on != today else ""))


def decide(doc: dict, companies: list, by: str, now: dt.datetime,
           stores: dict | None = None) -> dict:
    """What applying this worklist would do against the data as it is now.
    `stores` is read_stores() as cmd_apply read it; left out, it is read
    here, and a file that cannot be used raises rather than hold nothing."""
    s = _stores(stores)
    by_id = {c["id"]: c for c in companies}
    names = {cid: c.get("name") for cid, c in by_id.items()}
    # a record holding a Workable NUMBER the plan followed holds the slug too
    resolved = doc.get("workable_numbers") or {}
    numbers = numbers_on_file(companies)
    holders = holders_of(companies)
    for number, ids in numbers.items():
        if resolved.get(number):
            holders[board_key("workable", resolved[number])] += ids
    board = admin.read("board.json", {})
    shown = shown_now(board)
    wires = [r for r in doc["rows"] if r["action"] == "WIRE"]
    ids = collections.Counter(r["id"] for r in wires)
    keys = collections.Counter(k for r in wires for k in row_keys(r))
    twice = {k for k, n in list(ids.items()) + list(keys.items()) if n > 1}
    # what stands against a WIRE row now: this worklist's own reader doubts,
    # a doubt kept since, a board held for the owner under any id, a number
    # nobody followed, whatever is waiting on a person for it, and what
    # discovery and the Acquisitions queue say of it
    standing = standing_doubts(s["doubts"], s)
    rows_on_file = board_proposals.rows_by_id(s["found"])
    reaches = {cid: reach(by_id.get(cid), rows_on_file.get(cid) or [], s["ruled"].get(cid),
                          standing.get(cid))[1]
               for cid in set(standing) | set(doc.get("reader_doubts") or {})}
    held_for = {"doubted": doc.get("reader_doubts") or {},
                "standing": standing, "reaches": reaches, "waiting": _waiting(s),
                "boards": held_boards(standing, s, names),
                "numbers": numbers, "resolved": resolved,
                "suspects": _suspects(s),
                "acquisitions": acquisition_doubts(companies, board)}

    wire, left = [], []
    for row in wires:
        problem = wire_problem(row, by_id, holders, s["ruled"], s["proposals"], twice, shown,
                               now, held_for=held_for)
        if problem:
            left.append({"row": row, "code": problem[0], "why": problem[1]})
        else:
            wire.append(row)
    holds = [r for r in doc["rows"] if r["action"] == "HOLD"]
    queued = queue_rows(holds, standing, reaches)
    # asked of the same door the write goes through, so the look and the
    # write cannot disagree about which rows the queue will take
    filing = board_proposals.file_found(queued, by, write=False, force=True,
                                        companies=companies)
    # THE READERS' DOUBTS THIS WORKLIST CARRIES ARE ON FILE: the plan kept each
    # before it asked anything, in these words. One that is not (taken back,
    # or lost in a merge) is never dropped by a write.
    missing = sorted(cid for cid, words in (doc.get("reader_doubts") or {}).items()
                     if not any(str(d.get("why") or "").strip() == str(words).strip()
                                for d in s["doubts"].get(cid) or []))
    return {"wire": wire, "left": left, "holds": holds, "queued": queued, "filing": filing,
            # what each company to be wired reads as at this moment: the
            # code covers it, so a record corrected after the look is caught
            # even where the correction would still pass
            "on_file": {r["id"]: [by_id[r["id"]].get("name"), by_id[r["id"]].get("website"),
                                  _akas(by_id[r["id"]])] for r in wire},
            "skips": [r for r in doc["rows"] if r["action"] == "SKIP"],
            "standing": standing, "reaches": reaches, "missing": missing,
            "doubts": s["doubts"]}


def expect_code(sha: str, d: dict, by: str) -> str:
    """The worklist's own hash, and a hash of what the look said it would do.

    Two halves so a refusal can say WHICH moved: the file, or the data under
    it. Reasons go in as short codes, not sentences - "30 hours old" becomes
    "31 hours old" on its own and must not read as the plan changing.
    """
    filing = set(d["filing"]["added"])
    doing = json.dumps({
        "by": by,
        "wire": [[r["id"], spelling(r["type"], r["ref"]), *d["on_file"][r["id"]]]
                 for r in d["wire"]],
        "left": [[x["row"]["id"], x["code"]] for x in d["left"]],
        "file": d["filing"]["added"],
        # what each held row will say in front of the owner: a reader's doubt
        # kept between the look and the write is on it, and opens no old code
        "file_words": sorted([q.get("id"), q.get("identity_why")] for q in d["queued"]
                             if isinstance(q, dict) and q.get("id") in filing),
        "not_filed": sorted(str(s.get("id")) for s in d["filing"]["skipped"]),
        "filing_error": bool(d["filing"]["error"])},
        sort_keys=True)
    return f"{sha[:16]}-{hashlib.sha256(doing.encode()).hexdigest()[:16]}"


def report(doc: dict, d: dict) -> None:
    by_row = {r["id"]: r for r in d["holds"]}
    queued = {q.get("id"): q for q in d["queued"] if isinstance(q, dict)}
    print(f"WIRE {len(d['wire'])} - their own site points to the board, and the "
          f"board names them:")
    for r in d["wire"]:
        print(_line(r))
    if d["left"]:
        print(f"\nLEFT ALONE {len(d['left'])} - planned to wire, and not wired by this run:")
        for x in d["left"]:
            r = x["row"]
            print(f"  {str(r.get('name'))[:28]:<30} "
                  f"{show(r.get('type'), r.get('ref'))[:44]:<46} {x['why']}")
    print(f"\nHOLD {len(d['filing']['added'])} - to the owner's \"Boards we found\", "
          f"each with its doubt:")
    for cid in d["filing"]["added"]:
        print(_line(by_row[cid]))
        plan_words = str((by_row[cid].get("queue_row") or {}).get("identity_why") or "")
        now_words = str((queued.get(cid) or {}).get("identity_why") or "")
        if now_words != plan_words and now_words.startswith(plan_words):
            print(f"        and, kept since the plan: {now_words[len(plan_words):].lstrip('; ')}")
    if d["filing"]["skipped"]:
        print(f"\nNOT FILED {len(d['filing']['skipped'])} - held, and the queue "
              f"cannot take the row:")
        for s in d["filing"]["skipped"]:
            r = by_row.get(s.get("id")) or {}
            print(f"  {str(r.get('name') or s.get('id'))[:28]:<30} {s['why']}")
    listed = [r for r in d["skips"] if r.get("kind") != "no-candidate"]
    n = counts(doc["rows"])
    print(f"\nSKIPPED {n['skip']}" + (":" if listed else ""))
    for r in listed:
        print(_line(r))
    doubted = doc.get("reader_doubts") or {}
    if doubted:
        # NOT A WRITE: the plan kept these before it asked any board
        names = {r["id"]: r.get("name") for r in doc["rows"]}
        print(f"\nREADERS' DOUBTS {len(doubted)} - kept by the plan before it asked any "
              f"board (data/{DOUBTS}); nothing here writes them:")
        for cid in sorted(doubted):
            recs = [x for x in d["doubts"].get(cid) or []
                    if str(x.get("why") or "").strip() == str(doubted[cid]).strip()]
            board = (recs[-1].get("board") if recs else None) or "-"
            print(f"  {str(names.get(cid) or cid)[:28]:<30} {str(board)[:44]:<46} "
                  f"{(d['reaches'].get(cid) or '')}")
    why_not = {s.get("id"): s.get("why") for s in d["filing"]["skipped"]}
    notes = unfiled(doc["rows"], doubted, why_not, d["reaches"])
    if notes:
        print()
        for line in notes:
            print(line)


def cmd_apply(a) -> int:
    by = (a.by or "").strip()
    if not by.startswith("agent:") or len(by) <= len("agent:") or "@" in by:
        print("--by names the agent that planned this, as agent:<label> (a handle, "
              "never an address). A person rules in the admin; this door never "
              "writes as 'owner'.")
        return 2
    try:
        doc, sha = load_worklist(a.worklist)
    except (OSError, ValueError) as exc:
        print(f"could not read the worklist: {exc}")
        return 2
    # REDACTED AS IT IS READ, whoever wrote it. The plan redacts every row,
    # and this door still does not take a file's word for that: what a row
    # says goes into companies.json (the note, the board's stated name) and
    # into the journal, and both are public. The hash above is of the file's
    # own bytes, so the code a look prints still names the file it read.
    doc = privacy.redact_deep(doc)
    kept_by_plan = (f", with data/{DOUBTS} (and data/{board_proposals.FOUND.name} where it "
                    f"put a doubt on a row) that the plan wrote when it kept the readers' "
                    f"doubts" if doc.get("reader_doubts") else "")

    with admin.desk_lock():
        # every store a board is held against, read first and strictly: a
        # file that cannot be read cannot say which boards somebody stood
        # against, and read as empty it holds nothing
        try:
            stores = read_stores()
        except board_proposals.Unreadable as exc:
            print(f"{exc}. A refusal, a reader's doubt or a row waiting on the owner that "
                  f"cannot be read cannot hold its board, so nothing may be wired or filed. "
                  f"Nothing was written.")
            return 2
        companies = admin.read_companies()
        before = json.loads(json.dumps(companies))
        d = decide(doc, companies, by, dt.datetime.now().astimezone(), stores=stores)
        code = expect_code(sha, d, by)
        n = counts(doc["rows"])
        print(f"worklist {pathlib.Path(a.worklist).name}, planned {doc.get('generated')}: "
              f"{len(doc['rows'])} rows\n  sha256 {sha}\n")
        report(doc, d)
        n_wire, n_hold = len(d["wire"]), len(d["filing"]["added"])
        tail = f"{skipped_words(n)}" + (f"; left alone {len(d['left'])}" if d["left"] else "") \
            + (f"; not filed {len(d['filing']['skipped'])}" if d["filing"]["skipped"] else "")
        if d["missing"]:
            print(f"\nSTOPPED, and nothing was written: the plan kept "
                  f"{many(len(d['missing']), 'reader doubt')} that "
                  f"{'is' if len(d['missing']) == 1 else 'are'} no longer on file in "
                  f"data/{DOUBTS} ({', '.join(d['missing'][:6])}). A reader's doubt is never "
                  f"dropped by a write: plan again with the same --doubts file, which keeps "
                  f"them again.")
            return 1

        # --- the write, built and asked about before anything lands --------
        today = dt.date.today().isoformat()
        by_id = {c["id"]: c for c in companies}
        for row in d["wire"]:
            c = by_id[row["id"]]
            c["ats"] = {"type": row["type"], "ref": row["ref"]}
            c["ats_note"] = note_for(row, today)
            # as act_board_proposal does: the same company under its legal
            # form ("Acme, Inc.") is recorded, so the name the board really
            # uses is on file and is not doubted a second time
            stated = str(row["check_2"].get("name") or "").strip()
            if stated and stated.lower() != str(c.get("name") or "").lower():
                c["board_owner"] = stated
        force = n_wire > journal.BLAST
        stop = None
        if n_wire:
            stop = admin.validate(companies)
            if not stop:
                _changes, stop = journal.check("companies.json", before, companies, force)
        if not stop and d["filing"]["error"]:
            stop = f"the queue file would refuse the held rows: {d['filing']['error']}"
        if stop:
            print(f"\nSTOPPED, and nothing was written: {stop}")
            return 1
        # A WRITE GOES BY THE CODE ITS LOOK PRINTED, even when nothing would be
        # written now: a doubt kept since the look has changed what the write
        # is, and the old code is refused, not quietly turned into "nothing"
        if not a.write and not n_wire and not n_hold:
            print(f"\nnothing to write: wired 0, held 0, {tail}"
                  + ("; the readers' doubts were kept by the plan" if doc.get("reader_doubts")
                     else ""))
            return 0
        if not a.write:
            print(f"\nwould wire {n_wire}, would hold {n_hold}, {tail}")
            print("LOOKED ONLY. Nothing was written. To write exactly this:")
            print(f"  python3 scripts/wire_found.py apply {a.worklist} --by '{by}' "
                  f"--write --expect {code}")
            return 0
        if not n_wire and not n_hold and (a.expect or "").strip() in ("", code):
            print(f"\nnothing to write: wired 0, held 0, {tail}"
                  + ("; the readers' doubts were kept by the plan" if doc.get("reader_doubts")
                     else ""))
            return 0
        if (a.expect or "").strip() != code:
            given = (a.expect or "").strip()
            if not given:
                why = "--write needs --expect with the code the look printed"
            elif given.split("-")[0] != code.split("-")[0]:
                why = "the worklist file is not the one the look read"
            else:
                why = ("the worklist is the same and the data under it moved since "
                       "the look, so the write would not be what was listed")
            print(f"\nREFUSED, and nothing was written: {why}. Run the look again "
                  f"(without --write), read it, and use the code it prints.")
            return 1

        short_sha = sha[:12]
        if n_wire:
            if force:
                print(f"\n{n_wire} records in one entry is over the journal's "
                      f"{journal.BLAST}-record limit; the count and every name are "
                      f"printed above, so the write is confirmed")
            bad = admin.save_companies(
                companies, ACTION,
                f"{many(n_wire, 'board')} wired on two checks each: the company's own "
                f"website points to the board, and the board itself names the company "
                f"as the employer; each read live first (worklist {short_sha}, planned "
                f"{str(doc.get('generated'))[:16]}). Owner's ruling of 2026-11-18: "
                f"verified batches are written and he is told after",
                by=by, force=force)
            if bad:
                print(f"\nSTOPPED: {bad}. Nothing was wired or filed by this write"
                      + (f"; the readers' doubts the plan kept stay on file (data/{DOUBTS})"
                         if doc.get("reader_doubts") else "") + ".")
                return 1
        if n_hold:
            if n_hold > journal.BLAST:
                print(f"\n{n_hold} held rows in one entry is over the journal's "
                      f"{journal.BLAST}-record limit; the count and every name are "
                      f"printed above, so the write is confirmed")
            rep = board_proposals.file_found(
                d["queued"], by, write=True,
                force=n_hold > journal.BLAST, companies=companies,
                why=(f"{many(n_hold, 'found board')} held for the owner: the board did "
                     f"not name the company, or something on file, or a reader, argues "
                     f"with it; each row says which (worklist {short_sha})"))
            if rep["error"] or rep["added"] != d["filing"]["added"]:
                what = (f"the held rows were NOT filed: {rep['error']}" if rep["error"]
                        else f"the queue took {len(rep['added'])} held row(s) where "
                             f"the look listed {n_hold}")
                print(f"\nSTOPPED after the wiring: {many(n_wire, 'board')} "
                      f"{'is' if n_wire == 1 else 'are'} wired, and {what}. "
                      f"Commit data/companies.json and the journal together{kept_by_plan}; "
                      f"then run the look again: it will list what is left.")
                return 1
    print(f"\nwired {n_wire}, held {n_hold}, {tail}")
    changed = ["data/companies.json"] * bool(n_wire) + ["data/embedded_ats.json"] * bool(n_hold)
    print(f"written by {by}: {', '.join(changed)} and the journal (data/admin_journal.jsonl, "
          f"with data/admin_journal.archive.jsonl when it rolls). Commit them together"
          f"{kept_by_plan}.")
    print("Postings appear after the nightly crawl reads the new boards, not before.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="read the boards live and freeze a worklist")
    p.add_argument("--results", action="append", required=True,
                   help="a results file find_site_boards.py wrote (repeatable)")
    p.add_argument("--out", required=True, help="where the frozen worklist goes")
    p.add_argument("--doubts", help="a JSON object {company id: the doubt in words} from "
                                    "the readers who checked an earlier plan; each one is "
                                    "kept in data/board_doubts.json before the plan asks "
                                    "any board, and holds its board for the owner (needs "
                                    "--by)")
    p.add_argument("--by", help="agent:<label>, the author the readers' doubts are kept "
                                "under (required with --doubts)")
    w = sub.add_parser("apply", help="apply a frozen worklist; a look unless --write")
    w.add_argument("worklist")
    w.add_argument("--by", required=True, help="agent:<label>, the author on the journal")
    w.add_argument("--write", action="store_true")
    w.add_argument("--expect", help="the code the look printed; required with --write")
    a = ap.parse_args(argv)
    return cmd_plan(a) if a.cmd == "plan" else cmd_apply(a)


if __name__ == "__main__":
    sys.exit(main())
