#!/usr/bin/env python3
"""Wire a found board to its company - only when two separate checks agree.

    python3 scripts/wire_found.py plan --results site_boards.json --out worklist.json
    python3 scripts/wire_found.py plan --results ... --doubts doubts.json --by agent:<label> --out worklist-2.json
    python3 scripts/wire_found.py apply worklist.json --by agent:<label>            # look only
    python3 scripts/wire_found.py apply worklist.json --by agent:<label> --write --expect <code>
    python3 scripts/wire_found.py settle --id <company> --board <type>/<ref> --why "<his words>" --by agent:<label>  # a look
    python3 scripts/wire_found.py settle ... --write --expect <code>

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
        that company, and its board under any company's id, until he rules
        on the company in Boards we found after the doubt was kept, or it is
        settled on his word (`settle`). Where no ruling of his can reach it,
        the door says so and that he must be told.
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
request (one journalled addition to data/board_doubts.json, and their words
onto the rows already waiting, in entries the journal's runaway rule takes),
so no worklist, earlier or later, and no failure of this plan can wire past
one. `settle` writes the owner's word that settles a company's doubts about
the one board his words were about - only on his word, and a look first.

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
import shlex
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
# {"<company id>@<when>": {id, name, board, type, ref, why, by, on, at}} (the
# first shape, {company id: {board, why, by, on, at}}, is still read), written
# only by `plan --doubts` before its first request, through the journal, with
# the agent's own label - an ADDITION every time, never a rewrite. Every later
# plan holds such a company, and every apply of any worklist leaves its WIRE
# row alone, until the owner rules on the company in Boards we found after the
# doubt was kept, or it is settled on his word: `settle` adds a record
# {"<company id>@<when>~settled": {id, board, settles: [keys], why, by, on,
# at}}, settling only the doubts about the board his words were about
# (board_proposals.standing_doubts). No file is no doubts.
DOUBTS = board_proposals.DOUBTS
DOUBT_ACTION = "reader-doubt"
SETTLE_ACTION = "reader-doubt-settled"
# What a held row filed in Boards we found says of a standing doubt it carries:
# the row it is written on is the one his ruling settles it by.
ON_THIS_ROW = "his ruling on this row in Boards we found settles it"

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
    "hirebridge": {"hirebridge.com"},
    "jazzhr": {"jazzhr.com"}, "lever": {"lever.co"}, "paylocity": {"paylocity.com"},
    "rippling": {"rippling.com"}, "smartrecruiters": {"smartrecruiters.com"}}
VENDOR_AS = frozenset({"href"})
# THE BOARD'S OWN PLATFORM IN A FORM THAT NAMES NO BOARD, linked or named in
# text: the "Powered by ApplicantPro" link, a Teamtailor footer. The finder
# files every address on these platforms that names a board AS a board, so
# what is left under these names names none, and as a link it is the vendor
# doing its job (blind review, 2026-11-21: it held boards that passed both
# checks, on a doubt that said "a platform the crawl cannot read"). Embedded,
# scripted or requested, it is still a doubt: a widget of another account's
# names no board either.
OWN_FORMS = {"applicantpro": {"applicantpro-other"}, "teamtailor": {"teamtailor-other"},
             "pinpoint": {"pinpoint-other"}, "zohorecruit": {"zohorecruit-other"},
             "comeet": {"comeet-other"}, "jobvite": {"jobvite-other"}}
SKIP_WORDS = (("giant", "giants"), ("unreadable", "unreadable"),
              ("size-unknown", "size unknown"),
              ("no-candidate", "no board seen on their site"),
              ("unusable", "named only a board this door cannot use"),
              ("refused", "refused earlier"),
              ("not-waiting", "already on a board"),
              ("out-of-scope", "ruled out of scope"),
              ("gone", "no longer on file"))

# A Workable posting's short address, and the address it opens at. The second
# carries the board's slug, which the first does not.
_WORKABLE_SHORT = re.compile(r"https://apply\.workable\.com/j/([A-Za-z0-9]{6,24})")
_WORKABLE_PAGE = re.compile(
    r"https://apply\.workable\.com/([a-z0-9][a-z0-9-]{1,59})/j/([A-Za-z0-9]{6,24})", re.I)
_POD = re.compile(r"wd\d+")             # a Workday pod: wd1, wd5, wd108, wd503


# ---------------------------------------------------------------- small words

board_key = board_proposals.board_key       # one LOOSE key per board (the shared reader)
_also_keys = board_proposals.also_keys      # and the others it is known by (a Paylocity tenant)


def spelling(kind, ref) -> str:
    """The board exactly as it was spelled - what a read is filed under.

    Two spellings that share a `board_key` are read separately and never
    stand in for each other: 'caseco' answering empty says nothing about
    'CaseCo'.
    """
    if isinstance(ref, str):
        ref = ref.strip().rstrip("/")
    return f"{kind}:{json.dumps(ref, sort_keys=True)}"


show = board_proposals.show


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
            # why the board's own address could not be found from it, or why
            # it was not looked for (a giant, a board that did not read, a
            # company somebody refused)
            "account": cand.get("account"), "no_address": cand.get("no_address"),
            "follow_failed": cand.get("follow_failed"), "too_big": cand.get("too_big"),
            "unread": cand.get("unread"), "declined": cand.get("declined")})
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
    if vendor in OWN_FORMS.get(kind, ()):
        return how in ADVERTISED_AS
    return vendor.startswith("other:") and how in VENDOR_AS and \
        vendor.split(":", 1)[1].strip().lower() in VENDOR_SITES.get(kind, ())


def also_seen(entry: dict, key, website: str, kind=None) -> dict:
    """What else the finder saw: {elsewhere, advertised_on, off_site,
    elsewhere_off_site}.

    `elsewhere` is a job platform this repo has no reader for, or a form of a
    platform it does read that names no board or names a tenant this door
    cannot name (find_site_boards._NO_BOARD_IN_IT, TENANT_FORMS; each is said
    for what it is), named on the company's OWN site. That one is a doubt (see
    consider): it is a second
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

    THE ACQUISITIONS QUEUE'S FILES TOO (board_proposals.ACQUISITION_FILES).
    The door holds every company in that queue, and its builders read their
    files leniently: with merge-conflict markers in acquisitions_research.json
    a company held on outside research alone planned WIRE and was written
    (review of 27eeb628, 2026-11-19). They are read here only to be shown to read.
    """
    d = admin.DATA
    for name in board_proposals.ACQUISITION_FILES:
        board_proposals.strict(name, d)
    return {"ruled": board_proposals.strict(board_proposals.RULED.name, d),
            "proposals": board_proposals.strict(board_proposals.PROPOSALS, d),
            "found": board_proposals.strict(board_proposals.FOUND.name, d),
            "suspects": board_proposals.strict(board_proposals.SUSPECTS, d),
            "doubts": board_proposals.read_doubts(d)}


def read_companies() -> list:
    """data/companies.json, read STRICTLY: a file that is on disk and does not
    read as a list of records, each with its id, raises Unreadable. Read
    through admin.read it was an empty list, and `plan --doubts` journalled
    every doubt as naming "no company on file" and put none on the rows
    waiting for the owner (review of 27eeb628, 2026-11-19)."""
    p = admin.DATA / "companies.json"
    if not p.exists():
        return []
    try:
        got = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise board_proposals.Unreadable(
            f"data/companies.json does not read ({' '.join(str(exc).split())[:120]})") from None
    if not (isinstance(got, list) and all(isinstance(c, dict) and isinstance(c.get("id"), str)
                                          and c["id"].strip() for c in got)):
        raise board_proposals.Unreadable("data/companies.json is not a list of records, each "
                                         "with its id")
    return got


def read_board() -> dict:
    """data/board.json, which every plan and apply compares a board's count
    with: a file that does not read, or carries no list of organizations,
    raises Unreadable (read as empty, the hold for a board that reads fewer
    postings than the public board shows would hold nothing)."""
    p = admin.DATA / "board.json"
    try:
        got = json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError) as exc:
        raise board_proposals.Unreadable(
            f"data/board.json does not read ({' '.join(str(exc).split())[:120]})") from None
    if not (isinstance(got, dict) and isinstance(got.get("organizations"), list)):
        raise board_proposals.Unreadable(
            "data/board.json has no list of organizations, so what each company already "
            "shows on the public board cannot be compared with the board found for it")
    return got


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
    """{company id: [earlier refusals]} from the two stores that keep them
    (board_proposals.refusals, the reader all three doors share)."""
    return board_proposals.refusals(_stores(stores))


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
    owner has not settled: by a ruling on the company in Boards we found
    later than the doubt, or on his word (`settle`)
    (board_proposals.standing_doubts, the one rule every door holds a company
    to)."""
    s = _stores(stores)
    return board_proposals.standing_doubts(grouped, s["ruled"])


def settled_words(recs: list, ruling) -> str:
    """How the doubts on file about a company were settled, said plainly."""
    hows = []
    for d in recs or []:
        s = d.get("settled")
        if isinstance(s, dict):
            hows.append(f"settled on the owner's word ({s.get('by') or '?'} on "
                        f"{s.get('on') or '?'}: {str(s.get('why') or '').strip()[:160]})")
        elif isinstance(ruling, dict):
            hows.append(f"settled: the owner ruled on this company in Boards we found on "
                        f"{ruling.get('on') or '?'} (a board "
                        f"{'accepted' if ruling.get('accept') else 'refused'}), after it was "
                        f"kept")
    return "; ".join(dict.fromkeys(hows or ["settled"])) + "; it holds nothing now"


def reach(c: dict | None, rows: list, ruling, standing=None, on_file=None, refusals=None,
          fresh: bool = False) -> tuple[str, str]:
    """(code, words): what a reader's doubt about this company can reach,
    said plainly. Codes: settled, gone, wired, ruled, row, refused, none.
    `standing` are the company's standing doubts and `on_file` all of its
    doubts on file (one on file that does not stand was settled); `refusals`
    its refusals (_refusals); `fresh` that this very run kept the doubts.

    NOTHING IS PROMISED A RULING HE CANNOT MAKE, AND NOTHING SETTLED IS SAID TO
    STAND. Any ruling of his on the company in Boards we found after the doubt
    settles it, so a doubt about a company with a row on his desk (`row`) is
    his to rule on. Every other standing doubt cannot reach him - an older
    ruling (that queue never shows a ruled company again), a company already
    on a board, one merged away, one refused in Agent proposals, one with no
    row yet - so it says so, and that he must be told; on his word, `settle`
    takes it back. "Wired before the reader's doubt was kept" is said only of
    a doubt this run kept, when the board was there first (review of 27eeb628,
    2026-11-19).
    """
    standing = list(standing or [])
    if on_file and not standing:
        return "settled", settled_words(on_file, ruling)
    if c is None:
        boards = sorted({d["board"] for d in standing if d.get("board")})
        if not boards:
            return "gone", ("no company with this id is on file, and the doubt names no board, "
                            "so it holds nothing - tell the owner")
        return "gone", (f"no company with this id is on file (merged away or taken off), so no "
                        f"card of his can show it: it holds {' and '.join(boards[:3])} for any "
                        f"company whose site names it - tell the owner")
    a = c.get("ats") or {}
    if a.get("type") not in WAITING:
        return "wired", (f"it already reads from {show(a.get('type'), a.get('ref'))}"
                         + (": wired before the reader's doubt was kept" if fresh
                            else ", and a doubt does not take a board off")
                         + " - tell the owner")
    if isinstance(ruling, dict):
        return "ruled", (f"it cannot reach the owner's queue: he ruled on this company in "
                         f"Boards we found on {ruling.get('on') or '?'} (a board "
                         f"{'accepted' if ruling.get('accept') else 'refused'}), which is not "
                         f"shown to be after the doubt was kept, and that queue never shows a "
                         f"ruled company again - tell the owner")
    live = [r for r in rows or [] if isinstance(r, dict) and isinstance(r.get("found"), dict)]
    if live:
        f = live[0]["found"]
        return "row", (f"it is on its row in Boards we found ({show(f.get('type'), f.get('ref'))}), "
                       f"and any ruling of his on that row settles it")
    # refused in Agent proposals FOR THE BOARD THE DOUBT IS ABOUT: every plan
    # that finds that board skips it as refused, so no row ever carries it
    doubted = {board_key(*doubt_board(d)) for d in standing if doubt_board(d)[0]}
    rejected = [r for r in refusals or [] if r.get("store") == "Agent proposals"
                and r.get("key") in doubted]
    if rejected:
        r = rejected[0]
        return "refused", (f"the board it is about was refused for it in Agent proposals "
                           f"({r['board']}, {r['by']} on {r['on']}), so no plan files a row for "
                           f"it in Boards we found: it holds its board - tell the owner")
    return "none", ("it has no row in Boards we found, so no ruling of his can settle it yet: "
                    "it holds the board, and the next held row filed for it carries it - tell "
                    "the owner")


def doubt_words(d: dict, holds: str | None = None) -> str:
    """A standing doubt, as a held row says it - the reader's words whole,
    and, where given, what settles it (`holds`)."""
    return (f"a reader's doubt about this company stands, recorded "
            f"{d.get('on') or '?'} by {d.get('by') or '?'}"
            + (f" about {d['board']}" if d.get("board") else "")
            + f": {str(d.get('why') or '(no words recorded)').strip()}"
            + (f"; {holds}" if holds else ""))


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
                "board": show(p.get("ats_type"), p.get("ats_ref")),
                # the proposal's own words: a question put to the owner, which
                # the two checks do not answer (consider)
                "proposed": {"at": str(p.get("at") or "")[:10] or None, "by": p.get("by"),
                             "why": str(p.get("why") or "").strip()}})
    return out


doubt_board = board_proposals.doubt_board


_strict_names = board_proposals.strict_names


_their_names = board_proposals.their_names


_slug_of = board_proposals.slug_of


held_boards = board_proposals.held_boards   # under any id, by key and by name


named_holds = board_proposals.named_holds


numbers_on_file = board_proposals.numbers_on_file


def names_meet(ours, theirs) -> bool:
    """Do two lists of names share one? Compared as verify_boards.strict
    leaves a name, ITS COUNTRY TAG INCLUDED: "Esri" is not "Esri Canada"
    (strict_names keeps the name part only, and on file those two are one
    name - a distributor's refusal would read as Esri's own)."""
    def keys(names):
        return {k for k in (verify_boards.strict(n) for n in names or ()
                            if isinstance(n, str) and n.strip()) if k[0]}
    return bool(keys(ours) & keys(theirs))


def duplicate_groups(companies: list, board: dict) -> dict:
    """{company id: the ids the Duplicates queue groups it with}, as the desk
    draws that queue (admin.q_duplicates: one name, one website, one logo; a
    group the owner dismissed there is not one)."""
    out = collections.defaultdict(set)
    for g in admin.q_duplicates(companies, board):
        ids = {m.get("id") for m in g.get("members") or [] if isinstance(m, dict)}
        for i in ids:
            out[i] |= ids - {i}
    return out


def refused_for_whom(h: dict, by_id: dict) -> str:
    """Whose refusal a refusal under another record's id was, for a row it is
    quoted on: another company still on file, or a record since merged away
    whose name this company does not go by."""
    other = by_id.get(h.get("id"))
    if other is not None:
        return f"refused for another company, still on file ({other.get('name') or h['id']})"
    whose = next((n for n in h.get("whose") or [] if str(n).strip()), h.get("id"))
    return (f"refused for a record since merged away ({whose}), and this company does not go "
            f"by that name")


def refused_for_it(h: dict, c: dict, by_id: dict, dups: dict | None = None) -> bool:
    """Is a refusal held under ANOTHER record's id (held_boards, code
    `refused`) a refusal of this board for THIS company too? Only when the
    two are one company, as far as the file shows (names_meet):

      - the record is no longer on file, and this company goes by the name
        the refusal recorded for it (its name or an also_known_as - the
        desk's merge puts the dropped name there): it is the survivor;
      - either record's also_known_as carries the other's name;
      - the two have one name, and one website or one group in the
        Duplicates queue (`dups`, duplicate_groups; two companies are called
        Concourse, and a website tells them apart).

    Otherwise it is another company's refusal.

    WHY THE DIFFERENCE (blind review, 2026-11-21). The owner refuses Sub Co's
    claim on greenhouse/parentco, "the board is Parent Co's, not this
    company's"; Parent Co's own site links that board and the board names
    Parent Co. Every door held it for Parent Co, the wiring door as SKIP
    refused, and nothing could ever put it in front of him for Parent Co.
    10 of the 11 board proposals he had rejected name their true owner. So
    the wiring door files such a board for him, the refusal quoted and whose
    it was, and he rules on it; discovery and wire_embedded.py, which never
    wire past him, still hold it.

    AND ONLY ONE COMPANY'S (third blind review, 2026-11-21). Every refusal
    under a record merged away was taken for this company's, so a desk
    merge of Sub Co put Parent Co's board back out of his reach; and two
    records both named Acme Co, or one whose also_known_as carries the
    other's name, were "another company", so a board he had refused for
    Acme Co went back on his desk for Acme Co."""
    other = by_id.get(h.get("id"))
    if other is None:
        return names_meet(_their_names(c), h.get("whose") or [])
    if names_meet(_akas(c), [other.get("name")]) or names_meet(_akas(other), [c.get("name")]):
        return True
    if not names_meet([c.get("name")], [other.get("name")]):
        return False
    return page_belt._same_site(c.get("website") or "", other.get("website") or "") \
        or other.get("id") in (dups or {}).get(c.get("id"), ())


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
        for k in dict.fromkeys([board_key(a["type"], a["ref"]), *_also_keys(a["type"], a["ref"])]):
            out[k].append(c["id"])
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
    doubts = standing_doubts(s["doubts"], s)
    refusals = _refusals(s)
    return {"by_id": by_id,
            "companies": companies,
            "stores": s,
            "holders": holders_of(companies),
            "suspects": _suspects(s),
            "refusals": refusals,
            "waiting": _waiting(s),
            "doubts": doubts,
            # what each company's doubt can reach (reach)
            "on_file": s["doubts"],
            "rows": board_proposals.rows_by_id(s["found"]),
            "ruled": s["ruled"],
            # every board the owner holds under any company's id: set by
            # plan() once the pre-pass has followed this run's Workable numbers
            "held_boards": held_boards(doubts, s, by_id, {}, companies, refusals),
            "acquisitions": acquisition_doubts(companies, board),
            # which records the Duplicates queue groups (refused_for_it)
            "duplicates": duplicate_groups(companies, board),
            "shown": shown_now(board),
            "crawled": str((board or {}).get("generated") or "")[:10],
            "total": {}, "read": {}, "said": {}}


def reach_of(cid: str, ctx: dict) -> tuple[str, str]:
    """reach() for one company, as the plan's data reads (`fresh`: its doubt
    was kept by this very plan)."""
    return reach(ctx["by_id"].get(cid), (ctx.get("rows") or {}).get(cid) or [],
                 (ctx.get("ruled") or {}).get(cid), (ctx.get("doubts") or {}).get(cid),
                 (ctx.get("on_file") or {}).get(cid), (ctx.get("refusals") or {}).get(cid),
                 cid in (ctx.get("fresh") or ()))


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
    return board_proposals.numbered(kind, ref)


_number = board_proposals.workable_number   # no space, no last slash


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
    marked with why: `no_address` (a follow made and answered: consider holds
    it), `follow_failed` (failed on the day: consider skips it and the next
    plan asks again), `too_big` (a giant: nothing was followed, and consider
    skips it) or `unread` (the board did not read under its number, so
    nothing was followed: consider skips it with that read's error). "Could
    not be found from it" is said only of the first.
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
                cand["too_big"] = how
            elif kind == "unread":
                cand["unread"] = how
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
        # a Paylocity board by its tenant's name too (board_proposals.also_keys)
        keys.update(_also_keys(board["type"], s.get("ref")))
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
# can reach is said in reach's words. A doubt given again in the words of
# one the owner has settled is not kept again (D2), and is never said to
# stand: SETTLED.
UNFILED = ("a reader's doubt reaches no new row: its row is skipped ({kind}); the doubt "
           "is kept standing (data/board_doubts.json), and {reach}")
UNQUEUED = ("a reader's doubt reaches no new row: the queue cannot take the row ({why}); "
            "the doubt is kept standing (data/board_doubts.json), and {reach}")
SETTLED = ("a reader's doubt given in the words of one already on file ({reach}): it was not "
           "kept again and holds no later plan - to hold the board again, give the doubt in "
           "new words")


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
    slugs = ctx.get("slugs") or {}

    def refused_keys(r):
        # a refusal under a Workable NUMBER this run followed is the slug too
        ks = ({r["key"], *_also_keys(r.get("type"), r.get("ref"))} if r.get("key") else set())
        if numbered(r.get("type"), r.get("ref")) and _slug_of(slugs, _number(r["ref"])):
            ks.add(board_key("workable", _slug_of(slugs, _number(r["ref"]))))
        return ks
    # A NUMBER NOBODY ASKED ABOUT MAY BE THE BOARD THAT WAS REFUSED - EITHER
    # WAY ROUND. A refused company's Workable number is not followed to its
    # slug (the pre-pass asks nobody about a company somebody said no to), so
    # whether it is the Workable board refused under its slug is not known;
    # and a Workable board refused under a number this run did not follow may
    # be any slug found for it now (review of 27eeb628, 2026-11-19: a board refused
    # as workable/710855 was read, probed and filed again as its slug). Each
    # is taken to be the board refused: the answer that proposes nothing again.
    unknown = numbered(kind, ref) and len(keys) == 1
    same = [r for r in refused if refused_keys(r) & set(keys)]
    maybe = [r for r in refused if r not in same and r.get("type") == "workable"
             and kind == "workable"
             and ((unknown and not numbered("workable", r.get("ref")))
                  or (numbered("workable", r.get("ref"))
                      and not _slug_of(slugs, _number(r["ref"]))))]
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
        if not same and maybe and numbered("workable", r.get("ref")):
            why += (f". The board refused then was {r['board']}, a Workable account number "
                    f"this run did not follow to its slug, so the Workable board found now, "
                    f"{show(kind, ref)}, is taken to be the board refused - tell the owner")
        elif not same and (maybe or declined):
            got = (slugs.get(_number(ref)) or (None,)) if numbered(kind, ref) else (None,)
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
    # A BOARD REFUSED FOR ANOTHER COMPANY'S ID is refused here too
    # (held_boards) when the refusal was this company's under another record:
    # merged into its duplicate, a refused company left its refusal under the
    # id that was dropped, the survivor took its name as also_known_as, and
    # the next plan wired the board somebody had said no to (review of
    # 27eeb628, 2026-11-19). A refusal for ANOTHER COMPANY still on file is
    # not this company's answer: it is held below and filed for the owner,
    # the refusal quoted (refused_for_it; blind review, 2026-11-21).
    owner_holds = ctx.get("held_boards") or {"keys": {}, "named": []}
    hits = list({id(h): h for k in keys for h in owner_holds["keys"].get(k, [])
                 if h["id"] != cid}.values())
    no = [h for h in hits if h["code"] == "refused"
          and refused_for_it(h, c, ctx["by_id"], ctx.get("duplicates"))]
    if no:
        return _skip(base, "refused", f"{no[0]['words']}; the board found now is that board, "
                                      f"{show(kind, ref)}, for another record - tell the owner")

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
        # says why - "could not be found" is a claim about an attempt.
        doubts.append(f"their site asks for this board by its Workable account number "
                      f"({ref}), and the board's own address could not be found from it "
                      f"({seen['no_address']}): apply.workable.com/{_number(ref)} is "
                      f"Workable's error page, so the board is wired by its slug or not at "
                      f"all, and its slug has to be found first (the public site and the "
                      f"desk link their own site for a number, never that address)")
    elif numbered(kind, ref) and not seen.get("follow_failed"):
        # a giant's number is read once and nothing is followed; a number that
        # did not read cannot be followed either (review of 27eeb628, 2026-11-19)
        not_why = (f" ({seen['too_big']})" if seen.get("too_big") else
                   f" (the board did not read under its number: {seen['unread']})"
                   if seen.get("unread") else "")
        doubts.append(f"their site asks for this board by its Workable account number "
                      f"({ref}), and its slug was not looked for{not_why}: "
                      f"apply.workable.com/{_number(ref)} is Workable's error page, so the "
                      f"board is wired by its slug or not at all")
    if first.get("spellings"):
        doubts.append(f"their site spells this board more than one way "
                      f"({' and '.join(first['spellings'][:4])}); where case matters those "
                      f"are different boards, and only {show(kind, ref)} was read")
    if unusable:
        doubts.append("their site also names a board this door cannot use ("
                      + "; ".join(unusable[:3]) + ")")
    extra = also_seen(entry, keys, website, kind)
    if extra.get("elsewhere"):
        # A PLATFORM WE READ, in a form that names no board (a widget, a
        # script, a short link), is not "a platform the crawl cannot read":
        # it is said for what it is (blind review, 2026-11-21: Powerfleet's
        # card said so of Teamtailor's own pictures).
        import find_site_boards as _fsb
        forms = [v for v in extra["elsewhere"] if v in _fsb._NO_BOARD_IN_IT]
        tenants = [v for v in extra["elsewhere"] if v in _fsb.TENANT_FORMS]
        unread = [v for v in extra["elsewhere"] if v not in forms and v not in tenants]
        if tenants:
            doubts.append(f"their own site also names another "
                          f"{' and '.join(_fsb.TENANT_FORMS[v] for v in tenants)} account than "
                          f"this board ({', '.join(tenants)}), a tenant whose board this door "
                          f"cannot name, so which board they hire through today is not settled")
        if unread:
            doubts.append(f"their own site also names {' and '.join(unread)}, a job "
                          f"platform the crawl cannot read, so which board they hire through "
                          f"today is not settled")
        if forms:
            doubts.append(f"their own site also loads "
                          f"{' and '.join(_fsb._NO_BOARD_IN_IT[v] for v in forms)} in a form "
                          f"that names no board ({', '.join(forms)}): a widget or a link of "
                          f"another account names none either, so which board they hire "
                          f"through today is not settled")
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
    # either Workable key (held_boards; this company's own are said below).
    # A record on a Workable number whose slug this is (`number`) is said
    # once, as the board's holder above. A board refused for another company
    # still on file goes to the owner for THIS company, his refusal and whose
    # it was quoted: he said no to it for them, not for this one.
    for words in dict.fromkeys(
            (f"{h.get('refusal') or h['words']} - {refused_for_whom(h, by_id)}; whether it is "
             f"{base['name']}'s own board is the owner's to rule on")
            if h["code"] == "refused" else h["words"] for h in hits
            if not (h["code"] == "number" and h["id"] in holding)):
        doubts.append(words)
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
        elif isinstance(r.get("proposed"), dict):
            # A BOARD QUESTION PENDING IN AGENT PROPOSALS IS HIS TOO, whatever
            # board it is about (blind review, 2026-11-21). The same board,
            # proposed and waiting, was "the question already asked" here, so
            # the two checks answered it and the board was wired while the
            # proposal - "claimed by more than one company on the board - an
            # acquisition to settle" - still waited on him, and discovery and
            # wire_embedded.py held the company for it.
            q = r["proposed"]
            doubts.append(f"this board is already waiting in Agent proposals for this company, "
                          f"proposed {q.get('at') or '?'} by {q.get('by') or '?'}"
                          + (f": \"{q['why'][:240]}\"" if q.get("why") else "")
                          + "; a board waiting on a person is his to settle")
    reviewed = str((ctx.get("reviewed") or {}).get(cid) or "").strip()
    stands = (ctx.get("doubts") or {}).get(cid) or []
    reach_code, holds = reach_of(cid, ctx)
    if reach_code == "none":
        # the row this plan holds is the row that carries it to him: said of
        # that row, never "no row for it is in Boards we found" on the row
        # itself (review of 27eeb628, 2026-11-19)
        holds = ON_THIS_ROW
    for d in stands:
        # A READER'S DOUBT STANDS UNTIL THE OWNER SETTLES IT. It used to hold
        # the board for the one plan that was given it: JustFOIA was filed on
        # two readers' doubt and the next plan had it WIRE, and a doubted
        # board that ended SKIP that day was filed nowhere at all. `plan
        # --doubts` keeps every doubt it is given (data/board_doubts.json)
        # before it asks anything, each its own record, and every one holds
        # the company here - in the words, and saying what settles it
        # (reach). The words this plan was given are said once, below.
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
        return _skip(base, "giant", f"{GIANT_WHY} ({seen['too_big']})", **_carry(doubts))
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
    # A WORKABLE ACCOUNT HELD UNDER A NUMBER THIS RUN DID NOT FOLLOW - a
    # reader's doubt, a door-filed row or a refusal under workable/710855, or
    # a record on file set to it - may be this board under its slug: held
    # beside a board that STATES one of the names that account goes by
    # (held_boards, named_holds). No longer every Workable board, which held
    # boards that state other names entirely (decided after the review of
    # 27eeb628, 2026-11-19). AND beside a board found for a company that GOES
    # BY one of them (its name or an also_known_as), as discovery and
    # wire_embedded.py hold it: a doubt about "Archi", merged into Archistar,
    # held the survivor's own board in those two doors and not in this one
    # (blind review, 2026-11-21).
    doubts += [w for _c, w in named_holds(owner_holds, kind, names if stated else [], cid,
                                          _their_names(c))]

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
         stores: dict | None = None, numbers: dict | None = None, fresh=()) -> list:
    """Every company in the finder's results, decided. Network happens here.

    Each row is made safe to keep - an address redacted, half a character
    dropped - BEFORE it is printed or kept, so nothing the boards sent is
    ever written or shown in a form the worklist could not hold. `stores` is
    read_stores() as cmd_plan read it (read here when left out). `numbers`,
    when given, is filled with every Workable account number this run
    followed: {number: its slug, or None}. `fresh` are the companies whose
    readers' doubts keep_doubts kept in this very run.
    """
    ctx = standing(companies, board, stores)
    ctx["reviewed"] = {cid: reader_words(words) for cid, words in (reviewed or {}).items()}
    ctx["fresh"] = set(fresh or ())         # the doubts keep_doubts kept in this run
    named_by_number(results, ctx)
    if numbers is not None:
        numbers.update({n: got[0] for n, got in sorted(ctx["slugs"].items())})
    # what the owner holds by board, now that this run's Workable numbers are
    # followed: a held number the run resolved is held under its slug too
    ctx["held_boards"] = held_boards(ctx["doubts"], ctx["stores"], ctx["by_id"], ctx["slugs"],
                                     companies, ctx["refusals"])
    # a record on file holding a Workable NUMBER is the same board as the
    # slug that number was followed to in this run - or that the two are said
    # together on file in the door's own words (board_proposals.workable_pairs:
    # a note or a queue row). Followed only, a duplicate record linking the
    # slug was wired to the same account whenever the note was all that tied
    # them (blind review, 2026-11-21).
    pairs = ctx["held_boards"]["pairs"]
    for c in companies:
        a = c.get("ats") or {}
        slug = _slug_of(pairs, _number(a.get("ref"))) if numbered(a.get("type"), a.get("ref")) \
            else None
        if slug:
            ctx["holders"][board_key("workable", slug)].append(c["id"])
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
    """{company id: reach's (code, words)} for every row, as the plan's data
    reads."""
    return {r["id"]: reach_of(r["id"], ctx) for r in rows}


def unfiled(rows: list, doubted, why_not: dict | None = None,
            reaches: dict | None = None) -> list:
    """A line for every company a reader doubted whose doubt reaches no new
    queue row: its row was skipped, or (the look's `why_not`) the queue
    cannot take it. The doubt is not lost - the plan kept it before it asked
    anything - and it is never dropped without a word, nor said to wait for
    a ruling it cannot get, nor said to stand once it was settled
    (`reaches`: reach's (code, words) for each company)."""
    by_id = {r["id"]: r for r in rows}
    out = []
    for cid in sorted(doubted or ()):
        r = by_id.get(cid)
        if r is None:
            continue
        code, where = (reaches or {}).get(cid) or (None, None)
        if code == "settled":
            if r["action"] == "HOLD" and cid not in (why_not or {}):
                continue
            line = SETTLED.format(reach=where)
        elif r["action"] != "HOLD":
            line = UNFILED.format(kind=r.get("kind") or r["action"].lower(),
                                  reach=where or "what it can reach was not asked")
        elif cid in (why_not or {}):
            line = UNQUEUED.format(why=why_not[cid], reach=where or "what it can reach was not "
                                                               "asked")
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
            # said for what each is, as consider() says it of their own site:
            # a tenant of a platform we read, a read platform's form that names
            # no board, or a platform the crawl cannot read (third review's
            # Jobvite reviewer, 2026-11-21: the note called Jobvite's own
            # no-board form unreadable)
            import find_site_boards as _fsb
            away = extra["elsewhere_off_site"]
            tenants = [v for v in away if v in _fsb.TENANT_FORMS]
            forms = [v for v in away if v in _fsb._NO_BOARD_IN_IT and v not in tenants]
            unread = [v for v in away if v not in tenants and v not in forms]
            if unread:
                note += (f"; NOTE a page off their site names {', '.join(unread)}, which the "
                         f"crawl cannot read")
            if tenants:
                note += (f"; NOTE a page off their site names a "
                         f"{' and '.join(_fsb.TENANT_FORMS[v] for v in tenants)} tenant "
                         f"({', '.join(tenants)}) whose board this door cannot name")
            if forms:
                note += (f"; NOTE a page off their site loads "
                         f"{' and '.join(_fsb._NO_BOARD_IN_IT[v] for v in forms)} in a form that "
                         f"names no board ({', '.join(forms)})")
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


def same_board(a: tuple, b: tuple, pairs: dict | None = None) -> bool:
    """Are the boards (type, ref) `a` and `b` one board, for one question:
    is a reader's doubt already on file about it? By board_key, never by
    spelling - "greenhouse/AcmeCo" and "greenhouse/acmeco" are one board, and
    the same words about it were written twice after the owner's ruling and
    stood again (review of 27eeb628, 2026-11-19). No board and no board are
    the same.

    A WORKABLE ACCOUNT NUMBER AND A SLUG ARE ONE BOARD ONLY WHERE A PAIR SAYS
    SO (`pairs`: {number: slug}, board_proposals.workable_pairs - what is said
    on file; the doubts are kept before the plan's first request, so nothing
    this run follows can say it). Any number beside any slug was taken for one
    account: a doubt about workable/othergroup, in the words of one the owner
    had settled about workable/710855, was dropped as "already on file", the
    plan said it was settled on his word, and the next plan wired a board he
    never saw (blind review, 2026-11-21)."""
    (ka, ra), (kb, rb) = a, b
    if not ka or not kb:
        return not ka and not kb
    if board_key(ka, ra) == board_key(kb, rb):
        return True
    if not (ka == kb == "workable" and numbered(ka, ra) != numbered(kb, rb)):
        return False
    number, slug = (ra, rb) if numbered(ka, ra) else (rb, ra)
    known = _slug_of(pairs, _number(number))
    return bool(known) and board_key("workable", known) == board_key("workable", slug)


def new_doubts(reviewed: dict, results: dict, by_id: dict, grouped: dict, taken,
               by: str, pairs: dict | None = None) -> dict:
    """{record key: record} for every reader's doubt that is not already on
    file in the same words about the same board for the same company -
    WHETHER OR NOT the owner has settled it since. A doubt he settled, given
    again by a re-plan with the same --doubts file, came back as new, with a
    stamp later than his ruling, and stood again (second review,
    2026-11-19). Each new doubt is a record of its own, under a key nothing
    else holds: keeping one is an addition to the file, never a rewrite. It
    carries the company's name as it read then: a doubt held by board under
    a Workable number is compared with a board's stated name by it, after the
    record is merged away. `pairs` are the Workable numbers and slugs said
    together on file (same_board)."""
    on, at = dt.date.today().isoformat(), admin.now()
    taken = set(taken)
    out = {}
    for cid, words in sorted(reviewed.items()):
        board, kind, ref = board_named(results.get(cid), by_id.get(cid))
        if any(str(d.get("why") or "").strip() == words
               and same_board(doubt_board(d), (kind, ref), pairs)
               for d in grouped.get(cid) or []):
            continue
        key, n = f"{cid}@{at}", 2
        while key in taken:
            key, n = f"{cid}@{at}~{n}", n + 1
        taken.add(key)
        name = (by_id.get(cid) or {}).get("name") or (results.get(cid) or {}).get("name")
        out[key] = {"id": cid, **({"name": str(name)} if name else {}), "board": board,
                    "type": kind, "ref": ref, "why": words, "by": by, "on": on, "at": at}
    return out


def doubt_line(d: dict, row: dict | None) -> str:
    """A reader's doubt as it goes onto a row already waiting for the owner:
    naming the board it is about, and saying so when that is not the board
    on his card. A doubt about greenhouse/xco went onto lever/xold's card
    with no sign it was about another board (review of 27eeb628, 2026-11-19)."""
    kind, ref = doubt_board(d)
    f = (row or {}).get("found") if isinstance((row or {}).get("found"), dict) else {}
    on_card = show(f.get("type"), f.get("ref")) if f else None
    if not kind:
        about = "this company"
    elif f and board_key(kind, ref) == board_key(f.get("type"), f.get("ref")):
        about = f"this board ({show(kind, ref)})"
    elif f and kind == f.get("type") == "workable" \
            and numbered(kind, ref) != numbered("workable", f.get("ref")):
        # a number beside a slug: said as what may be, whatever is on file
        about = (f"{show(kind, ref)} (a Workable account number; this card's board, "
                 f"{on_card}, may be the same account)")
    else:
        about = f"{show(kind, ref)}, not the board on this card" + (
            f" ({on_card})" if on_card else "")
    return (f"a reader's doubt about {about}, kept {d.get('on') or '?'} by "
            f"{d.get('by') or '?'}: {str(d.get('why') or '').strip()}")


def _without_tail(words: str) -> str:
    """A refusal without the journal's closing "Nothing was written.": said
    after the readers' doubts WERE written, it was false (review of 27eeb628)."""
    return re.sub(r"\s*Nothing was written\.?\s*$", "", str(words or "")).rstrip(". ")


def keep_doubts(reviewed: dict, results: dict, by: str, worklist: str, say=print) -> tuple:
    """Keep the readers' doubts NOW, before this plan asks any board.

    A DOUBT STANDS FROM THE MOMENT IT IS GIVEN. It used to stand from the
    moment the doubted plan's worklist was WRITTEN, so the readers' own
    worklist, planned before they spoke and fresh for a day, still wired the
    board both of them doubted, with the code its own look printed - and a
    doubted plan that was never written kept nothing (second review,
    2026-11-19). So: one journalled ADDITION to data/board_doubts.json under
    the agent's label, before the first request; then the words onto the row
    already waiting for the owner in Boards we found, where there is one
    (board_proposals.add_doubt, in entries the journal's runaway rule takes),
    so his card shows them. Under the desk lock, so a concurrent write is not
    lost. A count over the journal's 25-record limit is printed (`say`)
    BEFORE the write it confirms, never after.

    Returns (why it stopped or None, the files written, the companies whose
    doubts it kept).
    """
    wrote = []
    with admin.desk_lock():
        try:
            raw = board_proposals.strict(DOUBTS, admin.DATA)
            s = read_stores()
            companies = read_companies()
        except board_proposals.Unreadable as exc:
            return f"{exc}; no reader's doubt was kept", wrote, set()
        by_id = {c["id"]: c for c in companies}
        refusals = _refusals(s)
        rows = board_proposals.rows_by_id(s["found"])
        # a Workable number and slug are one board only where the file says so
        pairs = board_proposals.workable_pairs(companies, s["found"])
        new = new_doubts(reviewed, results, by_id, s["doubts"], raw, by, pairs)
        fresh = {r["id"] for r in new.values()}
        if new:
            by_code = collections.defaultdict(list)
            for cid in sorted(fresh):
                recs = [v for v in new.values() if v["id"] == cid]
                by_code[reach(by_id.get(cid), rows.get(cid) or [], s["ruled"].get(cid), recs,
                              recs, refusals.get(cid), fresh=True)[0]].append(cid)

            def group(code, words):
                ids = by_code[code]
                return f"{len(ids)} {words} ({', '.join(ids[:12])})" if ids else None
            parts = [f"{many(len(new), 'reader doubt')} kept, given to the wiring door's plan "
                     f"before it asked any board (worklist {worklist})"] + [p for p in (
                group("row", "hold their company's board in every later plan and every "
                             "apply, until the owner rules on the company's row in Boards we "
                             "found, which shows them"),
                group("none", "have no row in Boards we found yet: each holds its board, and "
                              "the next held row filed for it carries it - tell the owner"),
                group("ruled", "cannot reach the owner's queue - he ruled on the company in "
                               "Boards we found before, and that queue never shows it again - "
                               "tell the owner"),
                group("refused", "are about a company refused in Agent proposals, which no "
                                 "plan files a row for - tell the owner"),
                group("wired", "are about a company already on a board: wired before the "
                               "reader's doubt was kept - tell the owner"),
                group("gone", "name no company on file: each holds its board for any company "
                              "whose site names it - tell the owner")) if p]
            if len(new) > journal.BLAST:
                say(f"{len(new)} reader doubts in one journal entry is over its "
                    f"{journal.BLAST}-record limit. The count and every company, before the "
                    f"write: {', '.join(sorted(fresh))}. So the write is confirmed.")
            bad = admin.save_decisions(DOUBTS, dict(raw, **new), DOUBT_ACTION,
                                       why="; ".join(parts), by=by,
                                       force=len(new) > journal.BLAST)
            if bad:
                return (f"the doubts file would refuse the readers' doubts: "
                        f"{_without_tail(bad)}; no reader's doubt was kept"), wrote, set()
            wrote.append(f"data/{DOUBTS}")
        # THE WORDS GO WHERE THE OWNER LOOKS: onto the row already waiting for
        # him, every standing doubt of each doubted company whose words it
        # does not have, naming the board each is about
        grouped = board_proposals.read_doubts(admin.DATA)
        standing = board_proposals.standing_doubts(grouped, s["ruled"])
        carry = {cid: standing[cid] for cid in sorted(reviewed) if cid in standing
                 and reach(by_id.get(cid), rows.get(cid) or [], s["ruled"].get(cid),
                           standing[cid], grouped.get(cid), refusals.get(cid))[0] == "row"}
        rep = board_proposals.add_doubt(carry, by, write=False, force=True, line=doubt_line)
        changed = sorted(rep["added"]) + sorted(rep["stamped"])
        if not rep["error"] and changed:
            n, size = len(changed), rep["per_entry"]
            entries = -(-n // size)
            force = min(n, size) > journal.BLAST
            if force or entries > 1:
                say(f"{n} rows in Boards we found get a reader's doubt's words"
                    + (f", in {entries} journal entries of at most {size} rows each (the "
                       f"journal refuses one entry that rewrites more than a third of the file)"
                       if entries > 1 else "")
                    + (f"; over the journal's {journal.BLAST}-record limit in one entry, so "
                       f"the count and every company, before the write: " if force else
                       ": ") + ", ".join(changed) + (". So the write is confirmed." if force
                                                     else "."))
            rep = board_proposals.add_doubt(
                carry, by, write=True, force=force, line=doubt_line,
                why=(f"{many(sum(rep['added'].values()), 'reader doubt')} put on the rows "
                     f"already waiting for the owner, so his card shows them, naming the "
                     f"board each is about: the queue takes one row per company (worklist "
                     f"{worklist})"))
            if rep["entries"]:
                wrote.append(f"data/{board_proposals.FOUND.name}")
        if rep["error"]:
            done = (f"; {rep['entries']} of its entries were written first" if rep["entries"]
                    else "; the queue file was not changed")
            return ((f"the readers' doubts are kept in data/{DOUBTS}, and " if wrote else "")
                    + f"their words could not be put on the rows already waiting for the "
                      f"owner{done}: {_without_tail(rep['error'])}"), wrote, fresh
        # what each doubt can reach, said of the files as they are now
        try:
            s = read_stores()
        except board_proposals.Unreadable as exc:
            return f"{exc}, after the readers' doubts were kept", wrote, fresh
        grouped = s["doubts"]
        standing = board_proposals.standing_doubts(grouped, s["ruled"])
        rows = board_proposals.rows_by_id(s["found"])
        refusals = _refusals(s)
    say(f"READERS' DOUBTS {len(reviewed)} - kept before any board is asked "
        f"({len(fresh)} new, {len(reviewed) - len(fresh)} already on file in the same words):")
    for cid in sorted(reviewed):
        name = (by_id.get(cid) or {}).get("name") or (results.get(cid) or {}).get("name") or cid
        board, kind, ref = board_named(results.get(cid), by_id.get(cid))
        mine = [d for d in grouped.get(cid) or [] if str(d.get("why") or "").strip()
                == reviewed[cid] and same_board(doubt_board(d), (kind, ref), pairs)]
        if cid not in fresh and mine and not any(d in (standing.get(cid) or []) for d in mine):
            words = settled_words(mine, s["ruled"].get(cid))
        else:
            words = reach(by_id.get(cid), rows.get(cid) or [], s["ruled"].get(cid),
                          standing.get(cid), grouped.get(cid), refusals.get(cid),
                          fresh=cid in fresh)[1]
        say(f"  {'KEPT' if cid in fresh else 'HAD':<5} {str(name)[:28]:<30} "
            f"{str(board or '-')[:44]:<46} {words}")
    return None, wrote, fresh


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
        print(f"{exc}. A refusal, a reader's doubt, a row waiting on the owner or an "
              f"Acquisitions row that cannot be read cannot hold its board, so nothing may "
              f"be planned. Nothing was read and nothing was written.")
        return 2
    # the records the checks are judged against, and what the public board
    # shows, read as strictly: read as empty, every doubt was journalled as
    # naming "no company on file" and none reached the rows waiting for him
    try:
        companies = read_companies()
        board = read_board()
    except board_proposals.Unreadable as exc:
        print(f"{exc}. Nothing was read and nothing was written.")
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
    done, kept_new, fresh = False, False, set()
    try:
        if reviewed:
            stop, wrote, fresh = keep_doubts(reviewed, results, by, out.name)
            kept_new = f"data/{DOUBTS}" in wrote
            if wrote:
                print(f"written by {by}: {', '.join(wrote)} and the journal "
                      f"(data/admin_journal.jsonl, with data/admin_journal.archive.jsonl "
                      f"when it rolls). Commit them together: the doubts stand from now, "
                      f"whatever becomes of this plan.")
            if stop:
                print(f"\nSTOPPED before the first request: {stop}. Nothing was read.")
                return 1
            # read again: the plan judges every row against the files as the
            # doubts just left them (and the words just put on the rows)
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
                    numbers=numbers, fresh=fresh)
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
          + ("; the readers' new doubts were kept before the first request, and stand "
             "whatever becomes of it" if kept_new else
             "; no reader's doubt was new - each was already on file in the same words, as "
             "READERS' DOUBTS says" if reviewed else ""))
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
    keys = {board_key(row.get("type"), row.get("ref")),
            *_also_keys(row.get("type"), row.get("ref"))}
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
            doubt_words(d, ((held_for.get("reaches") or {}).get(cid) or (None, None))[1])
            for d in stands)
    # the board held for the owner under ANY company's id (held_boards): a
    # refused one, a doubted one, a door-filed one - under either Workable key.
    # A record on file on a Workable number whose slug this is (`number`) is
    # the board's holder, and is said as one below.
    owner_holds = held_for.get("boards") or {}
    reading = {i for k in keys for i in holders.get(k, [])}
    for k in keys:
        for h in (owner_holds.get("keys") or {}).get(k) or []:
            if h["id"] == cid or (h["code"] == "number" and h["id"] in reading):
                continue
            if h["code"] == "refused" and not refused_for_it(h, c, by_id,
                                                             held_for.get("duplicates")):
                # another company's refusal: the plan files it for the owner
                return h["code"], (f"since the plan, {h.get('refusal') or h['words']} - "
                                   f"{refused_for_whom(h, by_id)}: plan again, and the plan "
                                   f"holds it for the owner with the refusal quoted")
            return h["code"], (f"since the plan, {h['words']}"
                               + (" - tell the owner" if h["code"] == "refused" else ""))
    # a Workable account held under a number the plan never followed, beside
    # a board that states one of the names it goes by, or found for a company
    # that goes by one (its name or an also_known_as, as on file now: blind
    # review, 2026-11-21)
    for code, words in named_holds(owner_holds, kind, c2.get("names") if c2.get("stated") else [],
                                   cid, _their_names(c)):
        return code, f"{words} - plan again"
    seen = {tuple(x) for x in row.get("waiting_seen") or []
            if isinstance(x, list) and len(x) == 2}
    for w in (held_for.get("waiting") or {}).get(cid) or []:
        if w.get("filed"):
            # filed by a door, on a doubt: his until he rules, whatever board
            f = w["filed"]
            return "queued", (f"{w['board']} is in the owner's queue ({w['store']}), filed "
                              f"{f.get('on') or '?'} by {f.get('by') or '?'} with its doubt, "
                              f"and a board filed there is his to settle")
        if isinstance(w.get("proposed"), dict) and (w.get("store"), w.get("key")) in seen:
            # pending in Agent proposals when the plan was made, and passed by
            # it (consider held it only after the blind review of 2026-11-21):
            # his to settle, whatever the plan saw
            return "waiting", (f"{w['board']} is waiting in Agent proposals for it"
                               + (f" (\"{w['proposed']['why'][:160]}\")"
                                  if w["proposed"].get("why") else "")
                               + "; the plan saw it and did not hold it, and a board waiting "
                                 "on a person is his to settle")
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


def queue_rows(holds: list, standing: dict | None = None) -> list:
    """What to file for these held boards, each stamped with ITS row's read,
    and carrying every reader's doubt that stands for its company NOW.

    One stamp per row. The queue row carries no time of its own, so there is
    nothing to drift from the one this door judges the row by: a worklist
    whose reads are a month old cannot file them as today's.

    A DOUBT KEPT SINCE THE PLAN GOES ON THE ROW. His ruling on a row filed
    after a doubt was kept settles that doubt, so the row has to show him its
    words: a worklist planned before the readers spoke files its rows with
    the words of that day. They are said of THIS row (ON_THIS_ROW), never in
    words about a row that is not there yet.
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
                    "; " + doubt_words(d, ON_THIS_ROW) for d in more)
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
           stores: dict | None = None, board: dict | None = None) -> dict:
    """What applying this worklist would do against the data as it is now.
    `stores` is read_stores() and `board` read_board() as cmd_apply read
    them; left out, each is read here, and a file that cannot be used raises
    rather than hold nothing."""
    s = _stores(stores)
    by_id = {c["id"]: c for c in companies}
    # a record holding a Workable NUMBER the plan followed holds the slug too,
    # and so does one whose number and slug are said together on file
    # (workable_pairs; blind review, 2026-11-21)
    resolved = doc.get("workable_numbers") or {}
    pairs = board_proposals.workable_pairs(companies, s["found"])
    numbers = numbers_on_file(companies)
    holders = holders_of(companies)
    for number, ids in numbers.items():
        slug = resolved.get(number) or pairs.get(number)
        if slug:
            holders[board_key("workable", slug)] += ids
    board = board if board is not None else read_board()
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
    refusals = _refusals(s)
    reaches = {cid: reach(by_id.get(cid), rows_on_file.get(cid) or [], s["ruled"].get(cid),
                          standing.get(cid), s["doubts"].get(cid), refusals.get(cid))
               for cid in set(standing) | set(doc.get("reader_doubts") or {})}
    held_for = {"doubted": doc.get("reader_doubts") or {},
                "standing": standing, "reaches": reaches, "waiting": _waiting(s),
                "boards": held_boards(standing, s, by_id, resolved, companies, refusals),
                "suspects": _suspects(s),
                "acquisitions": acquisition_doubts(companies, board),
                "duplicates": duplicate_groups(companies, board)}

    wire, left = [], []
    for row in wires:
        problem = wire_problem(row, by_id, holders, s["ruled"], s["proposals"], twice, shown,
                               now, held_for=held_for)
        if problem:
            left.append({"row": row, "code": problem[0], "why": problem[1]})
        else:
            wire.append(row)
    holds = [r for r in doc["rows"] if r["action"] == "HOLD"]
    queued = queue_rows(holds, standing)
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
        print(f"\nREADERS' DOUBTS {len(doubted)} - on file in data/{DOUBTS}, where the plan "
              f"kept them before it asked any board; nothing here writes them:")
        for cid in sorted(doubted):
            recs = [x for x in d["doubts"].get(cid) or []
                    if str(x.get("why") or "").strip() == str(doubted[cid]).strip()]
            board = (recs[-1].get("board") if recs else None) or "-"
            print(f"  {str(names.get(cid) or cid)[:28]:<30} {str(board)[:44]:<46} "
                  f"{(d['reaches'].get(cid) or (None, ''))[1]}")
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
            read_companies()            # shown to read, before admin reads it
            board = read_board()
        except board_proposals.Unreadable as exc:
            print(f"{exc}. A refusal, a reader's doubt, a row waiting on the owner, an "
                  f"Acquisitions row or a record that cannot be read cannot hold its board, "
                  f"so nothing may be wired or filed. Nothing was written.")
            return 2
        companies = admin.read_companies()
        before = json.loads(json.dumps(companies))
        d = decide(doc, companies, by, dt.datetime.now().astimezone(), stores=stores,
                   board=board)
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


def settle_board(given: str) -> tuple | None:
    """(type, ref) of the board `settle --board` names, as the door writes a
    board ("greenhouse/acme", "workday/<tenant>/<pod>/<site>"; doubt_board's
    reading), or (None, None) for "-": the doubts that name no board. None
    when it is neither."""
    given = str(given or "").strip()
    if given == "-":
        return None, None
    kind, ref = doubt_board({"board": given})
    return (kind, ref) if kind else None


def _doubt_line(d: dict) -> str:
    return (f"  {str(d.get('board') or '-')[:44]:<46} kept {d.get('on') or '?'} by "
            f"{d.get('by') or '?'} (--key {d.get('key')}): {str(d.get('why') or '').strip()}")


def cmd_settle(a) -> int:
    """Settle the standing readers' doubts about ONE board of a company, on
    the owner's word.

    ON HIS WORD, AND ONLY THEN. A doubt no ruling of his in Boards we found
    can reach - an older ruling, a company already on a board, one merged
    away, one refused in Agent proposals, one with no row - holds its board
    until he is told and answers. This writes his answer down: one
    journalled ADDITION to data/board_doubts.json, {"<id>@<when>~settled":
    {id, board, settles: [the keys of the doubts it settles], why: his words,
    by, on, at}}, which every reader of the file honours
    (board_proposals.standing_doubts) - this door, discovery and
    wire_embedded.py alike. It rewrites no doubt; admin_undo.py takes it
    back, and the doubts then stand again.

    AND ONLY WHAT HIS WORDS WERE ABOUT (blind review, 2026-11-21). It settled
    every standing doubt of the company: a doubt about greenhouse/settleco,
    which he had answered, and one a later plan kept about lever/settlecob,
    which nobody had shown him - and the next plan wired lever/settlecob. And
    it settled a doubt whose row is on his desk, behind the card his own
    ruling settles it from. So `--board` names the board his words are
    about, as the plan and the look print it (`-`: the doubts that name no
    board), and only doubts about that board (board_key) are settled; a run
    without --write is a look that lists them, and everything left standing,
    and prints the code; --write needs --expect with that code, as apply's
    does; and a doubt whose company has its row in Boards we found (reach
    'row') is refused: his ruling there settles it.
    """
    by = (a.by or "").strip()
    if not by.startswith("agent:") or len(by) <= len("agent:") or "@" in by:
        print("--by names the agent that writes the owner's word down, as agent:<label> (a "
              "handle, never an address): he does not run this himself. Nothing was written.")
        return 2
    cid, words = (a.id or "").strip(), reader_words(a.why)
    if not cid or not words:
        print("settle needs --id (the company) and --why (the owner's words, as he gave "
              "them). Nothing was written.")
        return 2
    board = settle_board(a.board)
    if board is None:
        print("--board is the board the owner's words are about, as the plan prints it: "
              "<type>/<ref> (greenhouse/acme; workday/<tenant>/<pod>/<site>), or - for the "
              "doubts that name no board. Nothing was written.")
        return 2
    kind, ref = board
    key_of = board_key(kind, ref) if kind else None
    named = show(kind, ref) if kind else "no board"
    with admin.desk_lock():
        try:
            raw = board_proposals.strict(DOUBTS, admin.DATA)
            stores = read_stores()
            by_id = {c["id"]: c for c in read_companies()}
        except board_proposals.Unreadable as exc:
            print(f"{exc}. Nothing was written.")
            return 2
        on_file = stores["doubts"].get(cid) or []
        ruling = stores["ruled"].get(cid)
        if not on_file:
            print(f"no reader's doubt about {cid} is on file in data/{DOUBTS} - check the id. "
                  f"Nothing was written.")
            return 1

        # ABOUT THE BOARD as same_board says it, a Workable account number and
        # the slug the file ties it to being one (third blind review,
        # 2026-11-21: a doubt about workable/710855 was "about another board"
        # beside --board workable/archico, the file saying they are one)
        pairs = board_proposals.workable_pairs(list(by_id.values()), stores["found"])

        def about(d):
            return same_board(doubt_board(d), (kind, ref), pairs)
        mine = [d for d in on_file if about(d)]
        everything = board_proposals.standing_doubts({cid: on_file}, stores["ruled"]).get(cid) \
            or []
        candidates = [d for d in everything if about(d)]
        named_keys = list(dict.fromkeys(k.strip() for k in a.key or [] if k and k.strip()))
        standing = [d for d in candidates if not named_keys or d["key"] in named_keys]
        others = [d for d in everything if d not in standing]
        if not mine:
            print(f"no reader's doubt about {cid} is on file about {named}; those on file are "
                  f"about {', '.join(sorted({str(d.get('board') or '-') for d in on_file}))} - "
                  f"give --board as the plan printed it. Nothing was written.")
            return 1
        if not candidates:
            print(f"no reader's doubt about {cid} stands about {named} "
                  f"({settled_words(mine, ruling)})"
                  + (f"; {many(len(others), 'other doubt')} about another board still "
                     f"stand{'s' if len(others) == 1 else ''}" if others else "")
                  + ". Nothing was written.")
            return 0
        code_r, where = reach(by_id.get(cid), board_proposals.rows_by_id(stores["found"]).get(cid)
                              or [], ruling, everything, on_file, _refusals(stores).get(cid))
        if code_r == "row":
            print(f"REFUSED, and nothing was written: {where}. `settle` writes the owner's word "
                  f"only where no ruling of his can reach a doubt; this one is on his desk, and "
                  f"his own card settles it.")
            return 1
        # ONE DOUBT AT A TIME WHERE THERE ARE MORE (third blind review,
        # 2026-11-21). Every standing doubt about the board was settled: the
        # owner's words about one ("it is their own board, not the
        # parent's") settled a later reader's about the same board in other
        # words ("every posting on it is a reseller's"), and the next plan
        # wired it. So where more than one stands about the board, each one
        # his words answer is named by its key.
        unknown = [k for k in named_keys if k not in {d["key"] for d in candidates}]
        if unknown:
            print(f"--key {', '.join(unknown)} is not a standing reader's doubt about {cid} about "
                  f"{named}; those that stand about it are listed with their keys:")
            for d in candidates:
                print(_doubt_line(d))
            print("Nothing was written.")
            return 1
        if len(candidates) > 1 and not named_keys:
            print(f"{many(len(candidates), 'reader doubt')} about {cid} stand about {named}, in "
                  f"words of their own: give --key for each one the owner's words answer (a "
                  f"doubt he was not asked about is not settled by his answer to another):")
            for d in candidates:
                print(_doubt_line(d))
            print("Nothing was written.")
            return 1
        code = hashlib.sha256(json.dumps({
            "id": cid, "board": key_of, "why": words, "by": by,
            "settles": [[d["key"], str(d.get("why") or "")] for d in standing]},
            sort_keys=True).encode()).hexdigest()[:16]
        print(f"{'SETTLE' if a.write else 'WOULD SETTLE'} {many(len(standing), 'reader doubt')} "
              f"about {cid} ({named}), on the owner's word: {words}")
        for d in standing:
            print(_doubt_line(d))
        if others:
            print(f"LEFT STANDING {len(others)} - about another board, or not named by --key, "
                  f"which these words do not settle; each holds its board until the owner "
                  f"answers about it too:")
            for d in others:
                print(_doubt_line(d))
        if not a.write:
            print("LOOKED ONLY. Nothing was written. To write exactly this:")
            print(f"  python3 scripts/wire_found.py settle --id {shlex.quote(cid)} --board "
                  f"{shlex.quote(str(a.board).strip())}"
                  + "".join(f" --key {shlex.quote(k)}" for k in named_keys)
                  + f" --why {shlex.quote(words)} --by {shlex.quote(by)} --write --expect {code}")
            return 0
        if (a.expect or "").strip() != code:
            why = ("--write needs --expect with the code the look printed" if not
                   (a.expect or "").strip() else
                   "the doubts it would settle, or the words, are not the ones the look listed")
            print(f"REFUSED, and nothing was written: {why}. Run the look again (without "
                  f"--write), read it, and use the code it prints.")
            return 1
        at = admin.now()
        key, n = f"{cid}@{at}~settled", 2
        while key in raw:
            key, n = f"{cid}@{at}~settled{n}", n + 1
        rec = {"id": cid, "board": show(kind, ref) if kind else None,
               "settles": [d["key"] for d in standing], "why": words, "by": by,
               "on": dt.date.today().isoformat(), "at": at}
        bad = admin.save_decisions(
            DOUBTS, dict(raw, **{key: rec}), SETTLE_ACTION, by=by,
            why=(f"on the owner's word, {many(len(standing), 'reader doubt')} about {cid} "
                 f"({named}) settled: {words}"))
        if bad:
            print(f"the doubts file would refuse it: {bad}")
            return 1
    print(f"SETTLED {many(len(standing), 'reader doubt')} about {cid} ({named}), on the owner's "
          f"word: {words}")
    print(f"written by {by}: data/{DOUBTS} and the journal (data/admin_journal.jsonl, with "
          f"data/admin_journal.archive.jsonl when it rolls). Commit them together; "
          f"admin_undo.py takes it back.")
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
    st = sub.add_parser("settle", help="settle a company's standing readers' doubts about one "
                                       "board - only on the owner's word; a look unless --write")
    st.add_argument("--id", required=True, help="the company the doubts are about")
    st.add_argument("--board", required=True,
                    help="the board his words are about, as the plan prints it (greenhouse/acme; "
                         "- for the doubts that name no board): only doubts about it are settled")
    st.add_argument("--key", action="append",
                    help="a doubt his words answer, by the key the look lists (repeatable); "
                         "needed where more than one doubt stands about the board")
    st.add_argument("--why", required=True, help="the owner's words, as he gave them")
    st.add_argument("--by", required=True, help="agent:<label>, the author on the journal")
    st.add_argument("--write", action="store_true")
    st.add_argument("--expect", help="the code the look printed; required with --write")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "apply": cmd_apply, "settle": cmd_settle}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
