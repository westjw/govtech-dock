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
# numbers it followed (`workable_numbers`). 5: what each Paylocity board's
# own page stated when the plan asked it is recorded once, by the board's
# exact spelling (`page_addresses`), and a WIRE row names the addresses of
# boards on file it was compared by (`leans_on`): apply leans on an address
# only where both say it, and the look prints each (eighth blind review,
# 2026-11-22: `record_addresses` was taken on the worklist's word). An older
# worklist is judged by rules that are no longer the door's.
VERSION = 5
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
        # other order fetch_workday takes is pod first, any tenant after it,
        # for myworkdaysite.com (ats.workday_board, since 2026-11-22: before,
        # that tenant had to begin "wd" too, and no real one did).
        if not (ats.WD_POD.fullmatch(ref[1]) or ats.WD_POD.fullmatch(ref[0])):
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
            "unread": cand.get("unread"), "declined": cand.get("declined"),
            # set by addressed_by_page: the /All/ address a Paylocity board's
            # own page states, or what its page said when it stated none
            "page_ref": cand.get("page_ref"), "page_said": cand.get("page_said")})
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
    it is the question already asked, and the two checks may now answer it
    (a Paylocity board only where an id agrees, never by the tenant's name
    alone: consider). A DIFFERENT board is: two finders, two boards, one
    company.
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
                                 # the board as the row spells it, and the /All/
                                 # address its page stated where a door filed it
                                 # (file_found's page_ref): it is met under every
                                 # address it is known by (consider; seventh blind
                                 # review, 2026-11-22: the address was dropped here)
                                 "type": f.get("type"), "ref": f.get("ref"),
                                 "page_ref": board_proposals.paylocity_address(
                                     r.get("page_ref")),
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
                "type": p.get("ats_type"), "ref": p.get("ats_ref"),
                "board": show(p.get("ats_type"), p.get("ats_ref")),
                # the proposal's own words: a question put to the owner, which
                # the two checks do not answer (consider)
                "proposed": {"at": str(p.get("at") or "")[:10] or None, "by": p.get("by"),
                             "why": str(p.get("why") or "").strip()}})
    return out


def waiting_keys(w: dict, pairs: dict | None = None) -> set:
    """Every key a board waiting on a person (a `_waiting` row) is known by:
    the row's own, and each of its addresses' (found_keys: a Workable
    number's slug's too, where `pairs` ties the two). A waiting row is the
    find's own board only where one of these is one of the find's keys,
    asked in this one way by the plan (consider) and again by apply
    (wire_problem) - save a Paylocity row beside a Paylocity find, which is
    that board only by an id that agrees (paylocity_relation and
    paylocity_ids_agree). Apply asks it with the pairs said on file alone:
    the worklist's own workable_numbers, which an edit ties to any slug, waives
    nothing (review 9, 2026-11-23)."""
    return {k for x in board_proposals.board_refs(w.get("type"), w.get("ref"), w.get("page_ref"))
            for k in board_proposals.found_keys(w.get("type"), x, pairs)} | {w.get("key")}


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


def duplicates_dismissed() -> dict:
    """{group key: the record} for every group the owner dismissed in the
    Duplicates queue (admin_dismissed.json, which read_stores has read
    strictly: a file that does not read stops the door first)."""
    got = (admin.dismissed() or {}).get("duplicates")
    return got if isinstance(got, dict) else {}


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


def _one_name(c: dict, other: dict) -> str | None:
    """How two records on file go by one name, in words that name both - one
    name, or an also_known_as either way carrying the other's (names_meet) -
    or None."""
    ours = f"{c.get('name') or c['id']} ({c['id']})"
    theirs = f"{other.get('name') or other['id']} ({other['id']})"
    if names_meet([c.get("name")], [other.get("name")]):
        return f"{theirs} and {ours} have one name"
    hit = next((a for a in _akas(other) if names_meet([a], [c.get("name")])), None)
    if hit:
        return f"the also_known_as of {theirs} carries \"{hit}\", the name of {ours}"
    hit = next((a for a in _akas(c) if names_meet([a], [other.get("name")])), None)
    if hit:
        return f"the also_known_as of {ours} carries \"{hit}\", the name of {theirs}"
    return None


def _site_address(url) -> str:
    """A website as one address - its host without www., lowercased, and its
    path without a last slash - or "" for none."""
    u = str(url or "").strip()
    if not u:
        return ""
    import urllib.parse
    parts = urllib.parse.urlparse(u if "//" in u else "https://" + u)
    host = parts.netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" if host else ""


def _on_website(page, website) -> bool:
    """Is `page` an address under `website` - its host, and at or under its
    path? Stricter than page_belt._same_site, which a sub-host or another
    path of the host passes."""
    p_, w_ = _site_address(page), _site_address(website)
    return bool(p_ and w_) and (p_ == w_ or p_.startswith(w_ + "/"))


def _domain(url) -> str:
    """The host admin.q_duplicates groups a website under."""
    bare = re.sub(r"^https?://(www\.)?", "", str(url or "").strip())
    return bare.rstrip("/").split("/")[0].lower()


def dismissed_pair(c: dict, other: dict, dismissed: dict | None) -> tuple | None:
    """(the group's key, the record) where the Duplicates queue's owner
    dismissed a group holding both records - one name, one website's host or
    one logo, as admin.q_duplicates draws it - or None."""
    d = dismissed or {}
    keys = []
    name_key = admin.ident(c.get("name") or "")
    if name_key and name_key == admin.ident(other.get("name") or ""):
        keys.append(name_key)
    host = _domain(c.get("website"))
    if host and host == _domain(other.get("website")):
        keys.append("site:" + host)
    keys.append("logo:" + "+".join(sorted({c["id"], other["id"]})))
    for k in keys:
        if isinstance(d.get(k), dict):
            return k, d[k]
    return None


def refusal_tie(h: dict, c: dict, by_id: dict, dups: dict | None = None,
                dismissed: dict | None = None) -> tuple[bool, str]:
    """(is a refusal held under ANOTHER record's id - held_boards, code
    `refused` - a refusal of this board for THIS company too?, why, in
    words). The words are said either way: on a SKIP line, the tie the file
    shows; on a row filed for the owner, whose refusal it was and what the
    file does not show.

    A SKIP never reaches him again - the board is not read and nothing is
    filed - so it is taken for this company's ONLY WHERE THE FILE SHOWS IT:

      - another record on file: the two go by one name (one name, or either
        record's also_known_as carrying the other's) AND a second sign agrees
        - one website, the same address, host and path (_site_address), or,
        where a website is missing so that nothing on file tells them apart,
        one group in the Duplicates queue for this very pair (`dups`,
        duplicate_groups) - and the pair was not dismissed there (`dismissed`,
        read in every case: a pair he dismissed is two companies). Two
        websites that differ are two companies, whatever the names say, and
        so are a website and a path or a sub-host of it (a brand at
        bigco.test/brand/ or brand.bigco.test is not on its parent's
        website: sixth blind review, 2026-11-22).
      - a record no longer on file: the refusal recorded a name this company
        goes by (its name or an also_known_as: the desk's merge keeps a
        dropped name in its survivor's also_known_as), nothing on file points
        at another company - no other record goes by that name, and the
        address the refusal was recorded against (`page`) is on no other
        record's website - or that address is on this company's own website,
        at or under its path (_on_website).

    Otherwise it is another company's refusal, and the board is read and
    filed for him with the refusal and these words quoted: one look at a
    board he refused, against a board he would never see again.

    WHY THE ALIAS NEEDS A SECOND SIGN (fifth blind review, 2026-11-22). A
    parent lists an acquired brand in its also_known_as; the owner refuses
    the brand's board for the parent ("it is Brand Co's own board, not Big
    Co's"); the brand's own site embeds it and the board states "Brand Co".
    Taken as one company on the alias alone, the brand's own board was SKIP
    refused, unread and unfiled. RapidSOS goes by IamResponding on file, and
    I Am Responding is a record of its own on another website.

    WHY THE DUPLICATES QUEUE NEEDS A MISSING WEBSITE (the same review). It
    groups every pair of one name, whatever their websites
    (admin.q_duplicates), so for two companies called Concourse on two
    websites a group there is no second sign: a refusal for one SKIPped the
    other's own board. Where a website is missing it is the one thing on file
    that says one company, and the SKIP line says that a dismissal there puts
    the board in front of him.

    WHY A RECORD NO LONGER ON FILE IS HELD TO THE OTHERS (the same review).
    Compared by its name alone, a refusal for the Concourse merged away was
    the other Concourse's too. And A REFUSAL THAT RECORDED NO NAME IS NEVER
    TAKEN FOR THIS COMPANY'S, decided on purpose: the name is the only thing
    that ties a record no longer on file to its survivor, and the address it
    was refused at cannot stand in for it - one website holds a parent and
    its parts (faac.com/milo/: 41 path-bound websites on file, 19 a part of
    a host), so a page on this company's website does not show that the
    refusal was this company's. Every refusal on file today records a name;
    0d2a135e skipped a nameless one, which nothing on file could show was
    right, and 2026-11-21's code filed it saying "this company does not go
    by that name" of a name nobody recorded.

    WHY THE DIFFERENCE AT ALL (blind review, 2026-11-21). The owner refuses
    Sub Co's claim on greenhouse/parentco, "the board is Parent Co's, not
    this company's"; Parent Co's own site links that board and the board
    names Parent Co. Every door held it for Parent Co, the wiring door as
    SKIP refused, and nothing could ever put it in front of him for Parent
    Co. 10 of the 11 board proposals he had rejected name their true owner.
    Discovery and wire_embedded.py, which never wire past him, still hold it
    for every company. AND ONLY ONE COMPANY'S (third blind review,
    2026-11-21): every refusal under a record merged away was taken for this
    company's, so a desk merge of Sub Co put Parent Co's board back out of
    his reach."""
    other = by_id.get(h.get("id"))
    ours = f"{c.get('name') or c['id']} ({c['id']})"
    if other is None:
        whose = [n for n in h.get("whose") or [] if isinstance(n, str) and n.strip()]
        if not whose:
            return False, (f"refused for a record no longer on file ({h.get('id')}), and the "
                           f"refusal recorded no name, so whose it was cannot be shown")
        gone = f"refused for a record since merged away ({whose[0]})"
        if not names_meet(_their_names(c), whose):
            return False, f"{gone}, and this company does not go by that name"
        page = str(h.get("page") or "").strip()
        sites = [o for o in by_id.values() if o.get("id") != c["id"] and page
                 and str(o.get("website") or "").strip()
                 and page_belt._same_site(page, o["website"])]
        if sites:
            o = sites[0]
            return False, (f"{gone}, a name {ours} goes by, but the refusal was recorded "
                           f"against {page}, on the website of {o.get('name') or o['id']} "
                           f"({o['id']}, {short(o['website'])}), so the file does not show "
                           f"it was this company's")
        if page and _on_website(page, c.get("website")):
            return True, (f"the record since merged away ({whose[0]}) went by a name {ours} "
                          f"goes by, and the refusal was recorded against {page}, a page of "
                          f"its own website")
        twins = [o for o in by_id.values() if o.get("id") != c["id"]
                 and names_meet(_their_names(o), whose)]
        if twins:
            o = twins[0]
            return False, (f"{gone}, a name {ours} goes by - and so does "
                           f"{o.get('name') or o['id']} ({o['id']}), so which of them it was "
                           f"merged into cannot be shown")
        return True, (f"the record since merged away ({whose[0]}) went by a name {ours} goes "
                      f"by, and no other record on file does (the desk's merge keeps a dropped "
                      f"name in its survivor's also_known_as)")
    name = other.get("name") or h["id"]
    tie = _one_name(c, other)
    if tie is None:
        return False, f"refused for another company, still on file ({name})"
    held = f"refused for another company, still on file ({name})"
    gone_pair = dismissed_pair(c, other, dismissed)
    if gone_pair:
        k, rec = gone_pair
        return False, (f"{held}: {tie}, but the pair was dismissed in the Duplicates queue "
                       f"({rec.get('on') or '?'}, by {rec.get('by') or '?'}"
                       + (f": {str(rec.get('why')).strip()[:120]}"
                          if str(rec.get("why") or "").strip() else "")
                       + "), so they are two companies")
    site_c, site_o = str(c.get("website") or "").strip(), str(other.get("website") or "").strip()
    if site_c and site_o:
        if _site_address(site_c) == _site_address(site_o):
            return True, f"{tie}, on one website ({short(site_o)})"
        return False, (f"{held}: {tie}, but they are on different websites ({short(site_o)} "
                       f"and {short(site_c)}), so the file does not show they are one company")
    no_site = ("neither has a website on file" if not (site_c or site_o)
               else f"{name} ({other['id']}) has no website on file" if not site_o
               else f"{ours} has no website on file")
    if other.get("id") in (dups or {}).get(c.get("id"), ()):
        return True, (f"{tie}, the Duplicates queue groups them, and nothing on file tells them "
                      f"apart ({no_site}) - if they are two companies, dismissing that pair in "
                      f"the Duplicates queue puts this board in front of the owner for {ours}")
    return False, (f"{held}: {tie}, but {no_site} and the Duplicates queue does not group them "
                   f"(a pair the owner dismissed there is two companies), so the file does not "
                   f"show they are one company")


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


holders_of = board_proposals.holders_of     # {board key: [ids reading it]}, wire_embedded's too


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
            "holders": holders_of(companies, s["found"]),
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
            # which records the Duplicates queue groups, and the groups he
            # dismissed there (refusal_tie)
            "duplicates": duplicate_groups(companies, board),
            "dismissed": duplicates_dismissed(),
            # every record on a Paylocity board, with every address its board
            # is known by (board_proposals.paylocity_records)
            "paylocity_records": board_proposals.paylocity_records(companies, s["found"]),
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


_DIGITS = re.compile(r"[0-9]+")


def account_words(row: dict) -> str:
    """'asks for it as Workable account 710855; its postings open under
    apply.workable.com/archistar' - the number their site asks by, kept on the
    record and on the queue row, so that if the slug ever stops answering the
    file still says which account the site embeds. "" for any other board,
    and for an account that is not a number in digits: every door after reads
    these words back as a number and its slug said together on file
    (board_proposals.workable_pairs), so they are never written of a board of
    another platform (review 9, 2026-11-23: an edited worklist's `account` put
    them on a greenhouse record, and the next door read them as a pair)."""
    account = row.get("account")
    if row.get("type") != "workable" or not isinstance(account, str) \
            or not _DIGITS.fullmatch(account):
        return ""
    return (f"asks for it as Workable account {account}; its postings open "
            f"under apply.workable.com/{row.get('ref')}")


def account_tie(row: dict, numbers: dict | None, pairs: dict | None) -> str | None:
    """How apply knows that a WIRE row's Workable account number (`account`)
    opens under the row's own slug, in the words the look prints - or None.

    A WORKABLE ROW, A NUMBER IN DIGITS, AND A TIE APPLY MAY LEAN ON: the
    plan's own record of the numbers it followed (the worklist's
    `workable_numbers`, the plan-level record a look prints this tie from) or
    a pair said on file (board_proposals.workable_pairs, `pairs`), opening
    that number under this very ref. An account only the row says is never
    leaned on (review 9, 2026-11-23): apply met the company's own unruled row
    for another board under the account's key, so an edited `account` waived
    it - and the row dropped off his queue - and the account's words went into
    the record's note, which every later door reads as a pair on file. This
    tie is what HOLDING needs (the account's key meets the holds, the holders
    and the twins); its words are written only on account_noted, and a row
    waiting under the number is taken for this board only by a pair on file
    (review 10, 2026-11-23)."""
    if not account_words(row):
        return None
    account, ref = row["account"], row.get("ref")
    if (numbers or {}).get(account) == ref:
        return f"the plan followed that number to apply.workable.com/{ref}"
    if (pairs or {}).get(account) == ref:
        return f"the file says that number opens under apply.workable.com/{ref}"
    return None


# the address a site asks Workable's widget (or its API) for an account by,
# as find_site_boards reads it out of a page and quotes it in a sighting
_ASKS_BY_NUMBER = re.compile(
    r"apply\.workable\.com/api/v[13]/(?:widget/)?accounts/([0-9]+)(?![0-9A-Za-z_-])", re.I)


def account_sighted(row: dict) -> str | None:
    """The address in check 1's own sighting (`check_1.saw`, what the finder
    saw on their site) that asks for the board by the row's account number -
    apply.workable.com/api/v1/widget/accounts/<the number> - or None."""
    c1 = row.get("check_1") if isinstance(row.get("check_1"), dict) else {}
    saw, account = c1.get("saw"), row.get("account")
    if not isinstance(saw, str) or not isinstance(account, str):
        return None
    return next((m.group(0) for m in _ASKS_BY_NUMBER.finditer(saw) if m.group(1) == account),
                None)


def account_noted(row: dict, numbers: dict | None, pairs: dict | None) -> str | None:
    """How apply knows a row's Workable account number may be WRITTEN where
    every door after reads it back as a pair on file - the wired record's
    note, a held row's doubt - in words; or None.

    MORE THAN account_tie, which is all the holding needs. A pair said on file
    already ties the number to this very slug; or check 1's own sighting asks
    for the board by that very number (account_sighted: the wire rests on it,
    their site asking for account N and its postings opening under the slug)
    and the plan's record of the numbers it followed opens it under this slug,
    with nothing on file opening it under another. The worklist's
    workable_numbers alone - a number tied to a slug their site was never seen
    asking by - is written nowhere (repair of review 9, 2026-11-23: an edit
    that gave the row that number as its `account` and tied it to the find's
    slug in workable_numbers planted the pair on file for every door after).
    Only ever of the row's OWN board: the slug written is the board the row
    wires or files, which that record or row then holds itself.

    IT CLEARS NOTHING (review 10, 2026-11-23). It used to take a row waiting
    for the company under the number for this very board as well - and
    check 1's sighting is a worklist field like the other two: edited with
    them, the company's own unruled row was waived and dropped off his queue.
    A waiting row is taken for this board under a number only where a pair
    on file says so (waiting_keys, in wire_problem)."""
    if not account_words(row):
        return None
    account, ref = row["account"], row.get("ref")
    on_file = (pairs or {}).get(account)
    if on_file == ref:
        return f"the file says that number opens under apply.workable.com/{ref}"
    asked = account_sighted(row)
    if on_file is None and asked and (numbers or {}).get(account) == ref:
        return (f"the plan followed that number to apply.workable.com/{ref}; check 1's own "
                f"sighting asks by it ({asked})")
    return None


def account_said(row: dict, numbers: dict | None, pairs: dict | None) -> str:
    """What the look prints under a wired row about its account number: what
    ties it to the slug, and whether that is written down (account_noted) or
    only holds."""
    noted = account_noted(row, numbers, pairs)
    if noted:
        return (f"their site asks for it as Workable account {row['account']}, and "
                f"{noted}; the record's note says it, a pair every door after reads (a row "
                f"waiting under the number is taken for this board only by a pair already "
                f"on file)")
    other = (pairs or {}).get(row["account"])
    why = (f"the file opens that number under apply.workable.com/{other}" if other else
           "check 1's own sighting does not ask for the board by that number, and nothing "
           "on file ties the two")
    return (f"for its holds alone, Workable account {row['account']}: "
            f"{account_tie(row, numbers, pairs) or 'nothing ties it to the slug'}, and {why}; "
            f"so no row waiting under the number is taken for this board, and the record's "
            f"note does not say it")


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


# --------------------------------------- a Paylocity board known by two ids

def compared(results: dict, ctx: dict):
    """(company id, record, first board, the spelling quoted) of every
    company whose first board is a Paylocity board consider compares with
    what is on file: a company on file, in scope and waiting for a board,
    whose board consider does not skip unread as refused (refused_first, the
    question consider asks). The one filter the pages the plan asks for a
    find's sake go by - the find's own (addressed_by_page) and a record's
    (records_by_page): a board consider never compares is no reason to ask
    anybody anything (eighth blind review, 2026-11-22: a record's page was
    asked for a company out of scope, on a board, refused, or for a second
    board on its site)."""
    for cid in sorted(results):
        c = ctx["by_id"].get(cid)
        if c is None or c.get("out_of_scope") \
                or (c.get("ats") or {}).get("type") not in WAITING:
            continue
        boards, _unusable = boards_named(results[cid])
        if not boards or boards[0]["type"] != "paylocity":
            continue
        board = boards[0]
        seen, _own = best_seen(board, c.get("website") or "", results[cid].get("pages") or [])
        if refused_first(cid, c, "paylocity", seen["ref"], board_keys(board, ctx), board,
                         ctx)["skip"]:
            continue                    # consider skips it unread: so is it here
        yield cid, c, board, seen


def addressed_by_page(results: dict, ctx: dict) -> None:
    """Give every Paylocity board a site names without its /jobs/All/ id or
    its tenant's name the /jobs/All/ address its own page states (`page_ref`),
    or say what the page said instead (`page_said`: "nothing", or
    "failed: <how>").

    ONE BOARD, TWO IDS, AND ITS ADDRESS CANNOT ALWAYS TIE THEM (fifth blind
    review, 2026-11-22). /jobs/List/<n> and /jobs/All/<uuid> name one board,
    and only the tenant's name after either ties them (also_keys). Celona's
    record on file reads .../All/<uuid> with no name after it, so a
    /List/<n>/Celona-Inc find met nothing of it and was wired to a second
    record, every posting listed twice. The board's own page states its /All/
    address (verify_boards' `address`, og:url), and the identity probe reads
    that page anyway: so it is asked here, BEFORE anything is keyed - after
    the live read, as consider asks them, once per spelling, both kept
    (ctx["read"], ctx["said"]) where consider finds them, and never of a
    board that does not read or is a giant - and a board whose page states
    its address is known by it everywhere after (holders, twins, refusals,
    the owner's holds, waiting rows; `page_ref` on the row for apply, on the
    row it files and in the note of a record it wires). Done to the finder's
    results, as named_by_number is.

    ASKED OF EVERY BOARD CONSIDER WOULD READ, AND OF NO OTHER: a company on
    file, in scope and waiting for a board, whose first board is a Paylocity
    board that consider does not skip unread as refused (refused_first, the
    question consider asks - not "any refusal on file", which lost the tie to
    a board refused at the address its own page states: sixth blind review,
    2026-11-22), named without an /All/ id or without a tenant's name.
    """
    for cid, _c, board, seen in compared(results, ctx):
        refs = board_refs_of(board)
        if any(board_proposals.paylocity_ids(r)[0] for r in refs) \
                and board_proposals.paylocity_tenants(refs):
            continue                    # its /All/ id and its tenant's name are known
        exact = spelling("paylocity", seen["ref"])
        # IN consider's ORDER: the live read first, kept where consider finds
        # it, and a board that does not read or is a giant is not asked whose
        # it is - there or here
        if exact not in ctx["read"]:
            ctx["read"][exact] = (*add_company.read_board({"type": "paylocity",
                                                           "ref": seen["ref"]}), admin.now())
        ok, _detail, read, _at = ctx["read"][exact]
        if not ok or len(read) >= GIANT:
            continue
        if exact not in ctx["said"]:
            ctx["said"][exact] = verify_boards.board_says("paylocity", seen["ref"])
        said = ctx["said"][exact]
        address = board_proposals.paylocity_address(said.get("address"))
        mark = ({"page_ref": address} if address else
                {"page_said": f"failed: {str(said.get('how') or said.get('failed'))}"}
                if said.get("failed") else
                # stated in a form the door does not keep: said so, never
                # "did not state" (seventh blind review, 2026-11-22)
                {"page_said": f"unkept: {str(said.get('address'))[:200]}"}
                if said.get("address") else {"page_said": "nothing"})
        for cand in results[cid].get("candidates") or []:
            if cand.get("type") == "paylocity" and isinstance(cand.get("ref"), str) \
                    and board_key("paylocity", cand["ref"]) == board["key"]:
                cand.update(mark)


def holds_by_page(ctx: dict, finds: list) -> dict:
    """Ask the board of each Paylocity hold of the owner's (held_boards'
    `paylocity`: a refusal, a reader's doubt, a door-filed row, a pending
    proposal) with no /All/ id that a find consider compares cannot be
    compared with, once per spelling, for the /All/ address its own page
    states, and compare by it for the rest of the run: every hold of that
    spelling is known by it, whichever hold it was asked for (`refs`;
    `page_stated`, and `refs_on_file` without it, for the rows that lean on
    it). {exact spelling: what the page said} (page_answer), also kept in
    ctx["hold_said"] for the words.

    ONE HOLD AT A BARE /List/ ADDRESS HELD EVERY BOARD FOUND AT AN /All/ ONE
    (eighth blind review, 2026-11-22): a door-filed row whose page stated
    nothing, refused at the desk, was "cannot be compared" with every /All/
    find of every company, in all three doors, for as long as the refusal
    stood - forever - and nothing asked the held board's own page. It is the
    twin of records_by_page: asked only for a find consider compares, of
    another company than the hold's (a company's own holds are its own), and
    the answer goes to apply as the row's `leans_on`. Where the page states
    nothing the hold stands, and the words say how it clears. A hold is never
    dropped: an /All/ address its page states only shows which board it is.
    (Asked after the pre-pass, so a board the pre-pass read can still meet a
    hold its page shows to be it - and is held or skipped then, never wired.)
    """
    said_of = ctx.setdefault("hold_said", {})
    holds = (ctx.get("held_boards") or {}).get("paylocity") or []
    for h in holds:
        refs = _held_refs(h)
        if not isinstance(h.get("ref"), str) \
                or any(board_proposals.paylocity_ids(r)[0] for r in refs) \
                or not any(board_proposals.paylocity_relation(f_refs, refs) == "unknown"
                           for cid_, f_refs in finds if cid_ != h["id"]):
            continue
        exact = spelling("paylocity", h["ref"])
        # A HOLD OF A COMPANY A PERSON RULED OUT OF SCOPE IS NOT ASKED, as its
        # record is not (records_by_page): such a company is not probed at all,
        # and the hold stands, said as not asked (blind review of the eighth
        # review's fixes, 2026-11-22: the hold's page was asked, its record's not)
        # - and never in place of what that page said when another company's
        # hold of the very same spelling asked it (setdefault)
        if ((ctx.get("by_id") or {}).get(h["id"]) or {}).get("out_of_scope"):
            said_of.setdefault(exact, "out of scope")
            continue
        if exact not in ctx["said"]:
            ctx["said"][exact] = verify_boards.board_says("paylocity", h["ref"])
        said_of[exact] = page_answer(ctx["said"][exact])
    # WHAT A HELD BOARD'S PAGE STATED IS TRUE OF THAT BOARD, WHOSEVER HOLD ASKED
    # IT (review 9, 2026-11-23). The page is asked once per spelling, and every
    # hold of that spelling is known by its answer - a hold of a company ruled
    # out of scope too, which asks nothing itself: the out-of-scope hold of a
    # bare /List/ board another company's hold asked kept holding a find its page
    # shows to be another board, "cannot be shown" though the plan's own read
    # showed it. Added to the addresses it is on file by, never in their place.
    for h in holds:
        if not isinstance(h.get("ref"), str):
            continue
        refs = _held_refs(h)
        exact = spelling("paylocity", h["ref"])
        if said_of.get(exact) != "stated" or h.get("page_stated") \
                or any(board_proposals.paylocity_ids(r)[0] for r in refs):
            continue
        address = board_proposals.paylocity_address(ctx["said"][exact]["address"])
        h.update(refs_on_file=refs, page_stated=address,
                 refs=list(dict.fromkeys([*refs, address])))
    return said_of


def _held_page_words(said) -> str:
    """What a held board's own page said of its /All/ address where this
    plan asked it (holds_by_page), and how the hold stops holding the boards
    it cannot be compared with - in words true of the run."""
    if not said or said == "stated":
        return ""
    if said == "out of scope":
        return ("; the held board's own page was not asked: a person ruled its company out of "
                "the board, and a company ruled out is not probed, so nothing on file compares "
                "the two: his ruling on this row settles this board")
    rest = str(said).split(":", 1)[1].strip() if ":" in str(said) else ""
    what = ("did not state its /jobs/All/ address" if said == "nothing" else
            f"could not be read for its /jobs/All/ address ({rest[:160]})"
            if str(said).startswith(("failed:", "transient:")) else
            f"states its /jobs/All/ address in a form this door does not keep ({rest[:200]})")
    return (f"; the held board's own page, asked by this plan, {what}, so nothing on file "
            f"compares the two: every plan that finds such a board asks that page again, and "
            f"until it states its address his ruling on this row settles this board")


def page_answer(said: dict) -> str:
    """What a Paylocity board's own page said of its /All/ address when the
    plan asked it (verify_boards.board_says), in the words the rows go by:
    "stated"; "transient: <how>", a failure of the day - the find it was asked
    for is SKIPped and asked again, never filed (consider); "failed: <how>";
    "unkept: <address>", stated in a form the door does not keep - never "did
    not state" (eighth blind review, 2026-11-22: a 503 was held and filed,
    and an address with a query after it read as stated, or as nothing);
    or "nothing"."""
    if board_proposals.paylocity_address(said.get("address")):
        return "stated"
    how = str(said.get("how") or said.get("failed") or "the request failed")
    if said.get("transient") is True:
        return f"transient: {how}"
    if said.get("failed"):
        return f"failed: {how}"
    if said.get("address"):
        return f"unkept: {str(said.get('address'))[:200]}"
    return "nothing"


def records_by_page(ctx: dict, finds: list) -> dict:
    """Ask the board of each Paylocity record on file that a find of this run
    cannot be compared with, once, for the /All/ address its own page states,
    and compare by it for the rest of the run. {record id: {ref, address}} for
    the pages that stated one; what each page said is kept in
    ctx["record_said"] for the words. `finds` are [(company id, every
    address)] of the boards consider compares (compared), and only for one of
    them is a page asked: not for a company out of scope or on a board, a
    board skipped unread as refused, or a second board on a site, and never
    the board of a record a person ruled out of scope - such a company is
    not probed at all (consider), and its record is said as not asked
    (eighth blind review, 2026-11-22).

    ONE BARE /List/ RECORD HELD EVERY LATER /All/ FIND (seventh blind review,
    2026-11-22). A record read at a bare /List/<n> address - an accepted row
    whose page stated nothing, a board set by hand - has no /All/ id and no
    tenant's name, so every Paylocity board found at an /All/ address, for any
    company, was "cannot be compared" with it, and nothing the door asked
    could clear it. Its own page states its /All/ address, as a found board's
    does (addressed_by_page), so the plan asks it - one read of a board the
    crawl reads every night, through the probe and its manners, only of a
    record with no /All/ id that some find of this run cannot be compared
    with - and the worklist carries the answer to apply: `page_addresses`
    records what the page stated, and a WIRE row compared by it names it
    (`leans_on`, leans_of); apply leans on it for that row alone, while the
    record still reads that board, and prints it in the look. Where the page
    states nothing, the row names the record and says how he clears it."""
    out, said_of = {}, ctx.setdefault("record_said", {})
    if not finds:
        return out
    recs = []
    for o, o_refs in ctx.get("paylocity_records") or []:
        ref = (o.get("ats") or {}).get("ref")
        if not any(board_proposals.paylocity_ids(r)[0] for r in o_refs) and any(
                board_proposals.paylocity_relation(f_refs, o_refs) == "unknown"
                for cid_, f_refs in finds if cid_ != o["id"]):
            if o.get("out_of_scope"):
                said_of[o["id"]] = "out of scope"       # not probed, as consider
                recs.append((o, o_refs))
                continue
            exact = spelling("paylocity", ref)
            if exact not in ctx["said"]:
                ctx["said"][exact] = verify_boards.board_says("paylocity", ref)
            said = ctx["said"][exact]
            address = board_proposals.paylocity_address(said.get("address"))
            if address:
                out[o["id"]] = {"ref": ref, "address": address}
                o_refs = list(dict.fromkeys([*o_refs, address]))
                said_of[o["id"]] = "stated"
            else:
                said_of[o["id"]] = page_answer(said)
        recs.append((o, o_refs))
    ctx["paylocity_records"] = recs
    return out


def _whole(refs) -> bool:
    """Is a Paylocity board known by an /All/ id and by a tenant's name?"""
    return any(board_proposals.paylocity_ids(r)[0] for r in refs or ()) \
        and bool(board_proposals.paylocity_tenants(refs))


def _clear_words(others: list, record_said: dict | None, refs_of: dict | None = None) -> str:
    """How a record whose Paylocity board nothing compares stops holding
    every later find, and what its own page said where this run asked it.
    A record already known by its whole /All/<uuid>/<Tenant> address is not
    told to be put on file by it (`refs_of`: every address each is known by):
    the board found is the one its addresses leave short."""
    out, short_ = [], False
    for o in others[:3]:
        said = (record_said or {}).get(o["id"])
        asked = ("" if said is None else
                 "; its own page did not state its /jobs/All/ address" if said == "nothing" else
                 f"; its own page could not be read ({said[len('failed:'):].strip()[:120]})"
                 if str(said).startswith("failed:") else
                 f"; its own page could not be read on the day "
                 f"({said[len('transient:'):].strip()[:120]})"
                 if str(said).startswith("transient:") else
                 f"; its own page states its /jobs/All/ address in a form this door does not "
                 f"keep ({said[len('unkept:'):].strip()[:200]})"
                 if str(said).startswith("unkept:") else
                 "; its own page was not asked: a person ruled it out of the board, and a "
                 "company ruled out is not probed" if said == "out of scope" else "")
        out.append(f"{o.get('name') or o['id']}'s board is on file by "
                   f"{short((o.get('ats') or {}).get('ref'))}{asked}")
        short_ = short_ or not _whole((refs_of or {}).get(o["id"])
                                      or [(o.get("ats") or {}).get("ref")])
    return ("; " + "; ".join(out) + (" - put on file by its whole /jobs/All/<uuid>/<Tenant> "
                                     "address, it can be compared with any board found by an "
                                     "/All/ address or by its tenant's name" if short_ else ""))


def _page_words(refs: list, page_said) -> str:
    """What this board's own page said of its /All/ address, where its
    addresses carry no /All/ id: asked and stated nothing, could not be read,
    or was not asked (addressed_by_page's `page_said`). True of the run."""
    if any(board_proposals.paylocity_ids(r)[0] for r in refs):
        return ""
    if page_said == "nothing":
        return ", and this board's own page did not state its /jobs/All/ address"
    if isinstance(page_said, str) and page_said.startswith("unkept:"):
        return (f", and this board's own page states its /jobs/All/ address in a form this "
                f"door does not keep ({page_said[len('unkept:'):].strip()})")
    if isinstance(page_said, str) and page_said.startswith("failed:"):
        return (f", and this board's own page could not be read for its /jobs/All/ address "
                f"({page_said[len('failed:'):].strip()[:160]})")
    return ", and this board's own page was not asked for its /jobs/All/ address"


def relation_words(rl: str, board: str, other: str, refs: list, other_refs: list,
                   page_said=None, held_said=None) -> str:
    """Why a Paylocity board that may be another one on file is held for a
    person, in words true of the two addresses (paylocity_relation's
    "tenant" or "unknown"; `held_said`: what the other board's own page said
    where this plan asked it, holds_by_page)."""
    if rl == "unknown" and held_said:
        return relation_words(rl, board, other, refs, other_refs, page_said) \
            + _held_page_words(held_said)
    if rl == "tenant":
        ts = sorted(board_proposals.paylocity_tenants(refs)
                    & board_proposals.paylocity_tenants(other_refs))
        return (f"{board} and {other} end in one Paylocity tenant's name ({ts[0] if ts else '?'}) "
                f"under two ids of one kind that differ - two boards of one tenant, or one "
                f"the ids do not settle - so whether {board} is that board is not shown")
    if rl == "kinds":
        ts = sorted(board_proposals.paylocity_tenants(refs)
                    & board_proposals.paylocity_tenants(other_refs))
        return (f"{board} and {other} end in one Paylocity tenant's name ({ts[0] if ts else '?'}) "
                f"after ids of the two kinds, /jobs/List/ and /jobs/All/, that nothing on file "
                f"compares{_page_words(refs, page_said)}, so whether {board} is that board - or "
                f"another board of one tenant - is not shown")
    if board_proposals.paylocity_conflict(refs, other_refs):
        return (f"whether {board} is {other} cannot be shown: their ids agree on one kind "
                f"and disagree on the other")
    spelt = board_proposals.paylocity_near_names(refs, other_refs)
    if spelt:
        return (f"whether {board} is {other} cannot be shown: their tenants' names, "
                f"{spelt[0][0]} and {spelt[0][1]}, may be one name written two ways ({SPELT}), "
                + ("under ids of one kind that differ - two boards of one tenant, or one the "
                   "ids do not settle" if board_proposals.paylocity_ids_differ(refs, other_refs)
                   else f"and no id of one kind is on both{_page_words(refs, page_said)}"))
    return (f"whether {board} is {other} cannot be shown: one Paylocity board has a /jobs/List/ "
            f"id and a /jobs/All/ id, tied only by the tenant's name after either, and one of "
            f"the two addresses carries no such name{_page_words(refs, page_said)}")


# how two tenants' names can be one name written two ways (paylocity_near_names)
SPELT = ("the finder keeps a tenant's name only as far as its first character other than a "
         "letter, a digit, _, . or -, so the one may be the other cut short")

# every relation of a Paylocity board to a record on file that holds it for a
# person (record_relation), in the order the words say them - the plan and
# apply hold on the same ones; "same" is said as the board's holder, and only
# "apart" holds nothing
RECORD_HOLDS = ("tenant", "unknown", "spelt", "conflict")


def record_relation(refs: list, o_refs: list) -> str:
    """paylocity_relation of a board and a record on file, with "unknown"
    split: "conflict" where their ids agree on one kind and disagree on the
    other, and "spelt" where no id agrees and the tenants' names may be one
    name written two ways (paylocity_near_names), so the words say that and
    not that nothing compares them."""
    rl = board_proposals.paylocity_relation(refs, o_refs)
    if rl == "unknown" and board_proposals.paylocity_conflict(refs, o_refs):
        return "conflict"
    if rl == "unknown" and board_proposals.paylocity_near_names(refs, o_refs):
        return "spelt"
    return rl


def records_words(rl: str, refs: list, others: list, board: str, page_said=None,
                  record_said: dict | None = None, refs_of: dict | None = None) -> str:
    """What a Paylocity board that records on file may share says, in words
    that name them (`others`: their records; `refs_of`: every address each is
    known by) and, where nothing on file compares the two, say how the owner
    clears it (`record_said`: what each record's own page said where the plan
    asked it, records_by_page)."""
    names = ", ".join(f"{o.get('name') or o['id']} at {short((o.get('ats') or {}).get('ref'))}"
                      for o in others[:3]) + (f" and {len(others) - 3} more"
                                              if len(others) > 3 else "")
    n = f"{many(len(others), 'record')} on file read{'s' if len(others) == 1 else ''}"
    if rl == "conflict":
        return (f"{n} a Paylocity board whose ids agree with {board}'s on one kind and disagree "
                f"on the other ({names}), so whether {board} is already on file cannot be shown")
    if rl == "tenant":
        return (f"{n} a Paylocity board that ends in {board}'s tenant's name under another id "
                f"({names}): two boards of one tenant, or one the ids do not settle, so whether "
                f"{board} is already on file is not shown")
    if rl == "spelt":
        spelt = next((p for o in others for p in board_proposals.paylocity_near_names(
            refs, (refs_of or {}).get(o["id"]) or [])), ("?", "?"))
        return (f"{n} a Paylocity board whose tenant's name may be {board}'s written another way "
                f"({names}): {spelt[0]} and {spelt[1]} ({SPELT}), and no id on both agrees"
                f"{_page_words(refs, page_said)}, so whether {board} is already on file cannot be "
                f"shown{_clear_words(others, record_said, refs_of)}")
    return (f"{n} a Paylocity board that {board} cannot be compared with ({names}): one Paylocity "
            f"board has a /jobs/List/ id and a /jobs/All/ id, tied only by the tenant's name "
            f"after either, one of the two addresses carries no such name"
            f"{_page_words(refs, page_said)}, so whether {board} is already on file cannot be "
            f"shown{_clear_words(others, record_said, refs_of)}")


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
        # a Paylocity board by its tenant's name too (board_proposals.also_keys);
        # where Paylocity boards meet they are compared by every address on
        # both sides (board_refs_of, paylocity_relation), not by these keys
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


def board_refs_of(board: dict) -> list:
    """Every address one found board is known by: each spelling a page of
    theirs gave, and for a Paylocity board the /All/ address its own page
    stated (addressed_by_page's `page_ref`)."""
    seen = board.get("seen") or []
    if board.get("type") != "paylocity":
        return [s.get("ref") for s in seen if s.get("ref") is not None]
    refs = list(dict.fromkeys(s.get("ref") for s in seen if isinstance(s.get("ref"), str)))
    stated = [board_proposals.paylocity_address(s.get("page_ref")) for s in seen]
    return refs + [a for a in dict.fromkeys(stated) if a and a not in refs]


def _held_refs(h: dict) -> list:
    return h.get("refs") or [h.get("ref")]


def refused_first(cid: str, c: dict, kind, ref, keys: list, board: dict, ctx: dict) -> dict:
    """Is the board found a board somebody refused FOR THIS COMPANY - asked
    before anything is read, so a refused board is never read again to be
    proposed again? {skip: the SKIP line or None, refused: the company's own
    refusals, hits: the owner's holds under another company's id that are
    this board, ties: {id(hold): refusal_tie} for the refused ones, loose:
    [(hold, "tenant" | "unknown")] for the Paylocity holds that may be it}.

    consider asks it, and so does addressed_by_page before it reads a board
    to ask its page - so the pre-pass never reads a board consider would skip
    unread, and skips nothing consider would read (sixth blind review,
    2026-11-22: it skipped every company with ANY refusal on file, so a
    board refused for the company itself at the address its own page states
    came back to his card as "an earlier board").

    A PAYLOCITY BOARD IS THE BOARD REFUSED, or the board a hold is about, only
    where every address it is known by says so (paylocity_relation "same"):
    the tenant's name after two ids of one kind that differ makes it a board
    to hold, never "the board refused" (sixth blind review, 2026-11-22: a
    refusal of /All/<uuid1>/Acme-Inc SKIPped /All/<uuid2>/Acme-Inc unread).
    """
    refused = ctx["refusals"].get(cid) or []
    slugs = ctx.get("slugs") or {}
    refs = board_refs_of(board) if board else [ref]
    stated = next((s["page_ref"] for s in (board or {}).get("seen") or []
                   if s.get("page_ref")), None)
    spelled = [x for x in refs if x != stated]
    out = {"skip": None, "refused": refused, "hits": [], "ties": {}, "loose": []}

    def refused_keys(r):
        # a refusal under a Workable NUMBER this run followed is the slug too
        ks = ({r["key"], *_also_keys(r.get("type"), r.get("ref"))} if r.get("key") else set())
        if numbered(r.get("type"), r.get("ref")) and _slug_of(slugs, _number(r["ref"])):
            ks.add(board_key("workable", _slug_of(slugs, _number(r["ref"]))))
        return ks

    def refusal_refs(r):
        return board_proposals.board_refs(r.get("type"), r.get("ref"), r.get("page_ref"))

    def is_it(r, these):
        # A PAYLOCITY BOARD IS THE BOARD REFUSED ONLY WHERE AN ID AGREES: the
        # tenant's name after a /List/ id and after an /All/ id is "same" by
        # paylocity_relation, and is two boards of one tenant as often as one
        # board - SKIPped unread on it, a board he never ruled on never
        # reached him, though by its /All/ address it was held (seventh blind
        # review, 2026-11-22). Such a board is read, its page asked for its
        # /All/ id, and held where that does not settle it (consider).
        if kind == "paylocity" and r.get("type") == "paylocity":
            other = refusal_refs(r)
            return board_proposals.paylocity_relation(these, other) == "same" \
                and board_proposals.paylocity_ids_agree(these, other)
        return bool(refused_keys(r) & set(keys))

    def by_page(r_refs) -> str:
        # said where only the address the board's own page states makes it
        # the board refused: "this board was refused" - its id, where the
        # site's spelling of it agreed on no id (is_it)
        if kind == "paylocity" and stated and not (
                board_proposals.paylocity_relation(spelled, r_refs) == "same"
                and board_proposals.paylocity_ids_agree(spelled, r_refs)):
            return (f"; the board found now, {show(kind, ref)}, states on its own page that "
                    f"its address is {stated}, so this board was refused")
        return ""
    # A NUMBER NOBODY ASKED ABOUT MAY BE THE BOARD THAT WAS REFUSED - EITHER
    # WAY ROUND. A refused company's Workable number is not followed to its
    # slug (the pre-pass asks nobody about a company somebody said no to), so
    # whether it is the Workable board refused under its slug is not known;
    # and a Workable board refused under a number this run did not follow may
    # be any slug found for it now (review of 27eeb628, 2026-11-19: a board refused
    # as workable/710855 was read, probed and filed again as its slug). Each
    # is taken to be the board refused: the answer that proposes nothing again.
    unknown = numbered(kind, ref) and len(keys) == 1
    same = [r for r in refused if is_it(r, refs)]
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
        if same:
            why += by_page(refusal_refs(r))
        elif maybe and numbered("workable", r.get("ref")):
            why += (f". The board refused then was {r['board']}, a Workable account number "
                    f"this run did not follow to its slug, so the Workable board found now, "
                    f"{show(kind, ref)}, is taken to be the board refused - tell the owner")
        elif maybe or declined:
            got = (slugs.get(_number(ref)) or (None,)) if numbered(kind, ref) else (None,)
            why += (f". The board found now is {show(kind, ref)}, a Workable account "
                    f"number, and its slug was not looked for - a company somebody said "
                    f"no to is not asked of anybody"
                    + (f" (the same number was followed for another company in this run: "
                       f"its postings open under apply.workable.com/{got[0]})" if got[0] else "")
                    + (f"; so it is taken to be the board refused, {r['board']}" if maybe
                       else f"; the board refused then was {r['board'] or 'not recorded'}")
                    + " - tell the owner")
        else:
            why += (f". The board ruled on then was {r['board'] or 'not recorded'}, the "
                    f"one found now is {show(kind, ref)}, and a ruled company is never "
                    f"shown in that queue again - tell the owner")
        out["skip"] = why
        return out
    # A BOARD REFUSED FOR ANOTHER COMPANY'S ID is refused here too
    # (held_boards) when the refusal was this company's under another record:
    # merged into its duplicate, a refused company left its refusal under the
    # id that was dropped, the survivor took its name as also_known_as, and
    # the next plan wired the board somebody had said no to (review of
    # 27eeb628, 2026-11-19). A refusal for ANOTHER COMPANY still on file is
    # not this company's answer: it is held below and filed for the owner,
    # the refusal quoted (refusal_tie; blind review, 2026-11-21). Taken for
    # this company's only where the file shows it, and the SKIP line says
    # what shows it (fifth blind review, 2026-11-22).
    owner_holds = ctx.get("held_boards") or {"keys": {}, "named": []}
    if kind == "paylocity":
        hits, loose = [], []
        for h in owner_holds.get("paylocity") or []:
            if h["id"] == cid:
                continue
            rel = board_proposals.paylocity_relation(refs, _held_refs(h))
            if rel == "same" and not board_proposals.paylocity_ids_agree(refs, _held_refs(h)):
                # the tenant's name alone, across the two kinds of id: held,
                # never "the board refused" (is_it, above)
                rel = "kinds"
            if rel == "same":
                hits.append(h)
            elif rel in ("tenant", "unknown", "kinds"):
                loose.append((h, rel))
    else:
        hits = list({id(h): h for k in keys for h in owner_holds["keys"].get(k, [])
                     if h["id"] != cid}.values())
        loose = []
    ties = {id(h): refusal_tie(h, c, ctx["by_id"], ctx.get("duplicates"), ctx.get("dismissed"))
            for h in hits if h["code"] == "refused"}
    out.update(hits=hits, ties=ties, loose=loose)
    no = [h for h in hits if ties.get(id(h), (False,))[0]]
    if no:
        out["skip"] = (f"{no[0]['words']}; the board found now is that board, "
                       f"{show(kind, ref)}, under another record, and the refusal is taken "
                       f"for this company's because {ties[id(no[0])][1]}"
                       f"{by_page(_held_refs(no[0]))} - tell the owner")
    return out


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
    page_ref = next((s["page_ref"] for s in first["seen"] if s.get("page_ref")), None)
    if page_ref:
        base["page_ref"] = page_ref            # the /All/ address its own page states
    elif any(s.get("page_said") for s in first["seen"]):
        # what its page said instead, for the words apply says of it
        base["page_said"] = next(s["page_said"] for s in first["seen"] if s.get("page_said"))
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

    # NEVER RE-PROPOSE WHAT SOMEBODY ALREADY SAID NO TO, and never read a
    # board somebody refused for this company (refused_first, which the
    # Paylocity pre-pass asks too, so the two cannot drift).
    refusal = refused_first(cid, c, kind, ref, keys, first, ctx)
    if refusal["skip"]:
        return _skip(base, "refused", refusal["skip"])
    refused, hits, ties, loose = (refusal["refused"], refusal["hits"], refusal["ties"],
                                  refusal["loose"])
    owner_holds = ctx.get("held_boards") or {"keys": {}, "named": []}

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
            # (true of each tenant form: another account's address, or one
            # job's, whose account the address does not show - never "another
            # account" of a link that may be to this board's own job)
            doubts.append(f"their own site also points at "
                          f"{' and '.join(dict.fromkeys(_fsb.TENANT_FORMS[v] for v in tenants))} "
                          f"somewhere this board's address does not cover ({', '.join(tenants)}): "
                          f"another account, or a job whose account the address does not show, "
                          f"so which board they hire through today is not settled")
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
    refs = board_refs_of(first)
    pl = kind == "paylocity"
    page_said = next((s["page_said"] for s in first["seen"] if s.get("page_said")), None)
    if pl:
        # A PAYLOCITY BOARD MEETS WHAT IS ON FILE BY EVERY ADDRESS IT IS KNOWN
        # BY (board_proposals.paylocity_relation), not by keys: a record at a
        # bare /List/<n> and the same board's /All/<uuid>/<Tenant> share none,
        # and one tenant's name after two ids that differ is not one board
        # (sixth blind review, 2026-11-22)
        rel_records = [(o, record_relation(refs, o_refs))
                       for o, o_refs in ctx.get("paylocity_records") or []
                       if o["id"] != cid and o["id"] in by_id]
        holding = list(dict.fromkeys(o["id"] for o, rl in rel_records if rl == "same"))
    else:
        holding = list(dict.fromkeys(i for k in keys for i in ctx["holders"].get(k, [])
                                     if i != cid and i in by_id))
    held = [by_id[i]["name"] for i in holding]
    if held:
        doubts.append(f"{show(kind, ref)} is already on file as the board of "
                      + " and ".join(held[:4])
                      + (f" (under the account number {account} or its slug)"
                         if account else "")
                      + (f" (its own page states its address is {page_ref})"
                         if page_ref else ""))
    if pl:
        # A RECORD WHOSE BOARD MAY BE THIS ONE holds it for a person: one of the
        # same tenant's name under another id, one nothing on the two
        # addresses compares (fifth and sixth blind reviews, 2026-11-22), or
        # one whose ids agree on one kind and disagree on the other
        refs_of = {o["id"]: o_refs for o, o_refs in ctx.get("paylocity_records") or []}
        for rl in RECORD_HOLDS:
            others = [o for o, r_ in rel_records if r_ == rl]
            if others:
                doubts.append(records_words(rl, refs, others, show(kind, ref), page_said,
                                            ctx.get("record_said"), refs_of))
    if pl:
        rel_finds = [(i, t_refs, board_proposals.paylocity_relation(refs, t_refs))
                     for i, t_refs in ctx.get("paylocity_finds") or [] if i != cid]
        twin_ids = list(dict.fromkeys(i for i, _r, rl in rel_finds if rl == "same"))
    else:
        twin_ids = list(dict.fromkeys(i for k in keys for i in claims.get(k, []) if i != cid))
    twins = [(by_id.get(i) or {}).get("name") or i for i in twin_ids]
    if twins:
        doubts.append(f"the same board was found for {' and '.join(twins[:4])} in this run")
    if pl:
        # ONE BOARD FOUND BY ITS TWO IDS FOR TWO COMPANIES IN ONE RUN (sixth
        # blind review, 2026-11-22: both were WIRE, and apply wired both)
        for rl in ("tenant", "unknown"):
            maybe = [(i, t_refs) for i, t_refs, r_ in rel_finds if r_ == rl]
            if maybe:
                listed = "; ".join(f"{(by_id.get(i) or {}).get('name') or i} at "
                                   f"{short(t_refs[0])}" for i, t_refs in maybe[:4]) \
                    + (f"; and {len(maybe) - 4} more" if len(maybe) > 4 else "")
                one = len(maybe) == 1
                doubts.append(
                    f"{'a Paylocity board was' if one else 'Paylocity boards were'} found in "
                    f"this run for {listed}, and "
                    + relation_words(rl, show(kind, ref), "that board" if one else "each of them",
                                     refs, maybe[0][1], page_said))
    # A BOARD THE OWNER HOLDS FOR ANOTHER COMPANY'S ID is held here too: the
    # board of a standing doubt, or of an unruled row a door filed, under
    # either Workable key (held_boards; this company's own are said below).
    # A record on a Workable number whose slug this is (`number`) is said
    # once, as the board's holder above. A board refused for another company
    # still on file goes to the owner for THIS company, his refusal and whose
    # it was quoted: he said no to it for them, not for this one.
    for words in dict.fromkeys(
            (f"{h.get('refusal') or h['words']} - {ties[id(h)][1]}; whether it is "
             f"{base['name']}'s own board is the owner's to rule on")
            if h["code"] == "refused" else h["words"] for h in hits
            if not (h["code"] == "number" and h["id"] in holding)):
        doubts.append(words)
    # A PAYLOCITY BOARD THE OWNER HOLDS THAT MAY BE THIS ONE, under another
    # company's id - one of its tenant's name under another id, or one its
    # addresses do not compare with - is held, the hold quoted: a hold at a
    # bare /List/2115 and this board at /All/<uuid>/<Tenant> share no key, and
    # the plan wired past an unruled row, his refusal of it and a reader's
    # doubt (sixth blind review, 2026-11-22)
    for h, rl in loose:
        if h["code"] == "refused":
            mine, whose = refusal_tie(h, c, by_id, ctx.get("duplicates"), ctx.get("dismissed"))
            said = (f"{h.get('refusal') or h['words']} - "
                    + (f"taken for this company's, because {whose}," if mine else f"{whose};"))
        else:
            said = f"{h['words']};"
        doubts.append(f"{said} and "
                      + relation_words(rl, show(kind, ref), h["board"], refs, _held_refs(h),
                                       page_said, (ctx.get("hold_said") or {}).get(
                                           spelling("paylocity", h.get("ref")))))
    for r in refused:
        r_refs = board_proposals.board_refs(r.get("type"), r.get("ref"), r.get("page_ref"))
        rl = (board_proposals.paylocity_relation(refs, r_refs)
              if pl and r.get("type") == "paylocity" else None)
        if rl == "same" and not board_proposals.paylocity_ids_agree(refs, r_refs):
            rl = "kinds"                # the tenant's name alone (refused_first)
        doubts.append(f"an earlier board for this company was refused ({r['board']}, "
                      f"{r['by']} on {r['on']}" + (f": {r['why']}" if r["why"] else "") + ")"
                      + (", and " + relation_words(rl, show(kind, ref), r["board"], refs, r_refs,
                                                   page_said)
                         if rl in ("tenant", "unknown", "kinds") else ""))
    for r in ctx["waiting"].get(cid) or []:
        # ONE BOARD UNDER ANY ADDRESS IT IS KNOWN BY, as holders and refusals
        # are met: a Paylocity board by every address on both sides
        # (paylocity_relation, a row's `page_ref` included), any other by its
        # keys, a Workable number by the slug the file ties it to. Compared by
        # its own key alone, a company's own pending proposal for
        # /All/<uuid>/Pl-Co beside a /List/2115/Pl-Co find of the same board
        # was "a different board" (fifth blind review, 2026-11-22).
        r_refs = board_proposals.board_refs(r.get("type"), r.get("ref"), r.get("page_ref"))
        if pl and r.get("type") == "paylocity":
            # THE SAME BOARD ONLY WHERE AN ID AGREES (eighth blind review,
            # 2026-11-22). The tenant's name after a /List/ id and after an
            # /All/ id is two boards of one tenant as often as one board, and
            # taken for "the question already asked" it let the checks answer
            # an unruled row of the company's own: the find was wired and the
            # row dropped off his queue - and the less the door knew, the more
            # it wired (the page stating another /All/ id held it). A SKIP and a
            # waiver both rest on an id that agrees (refused_first's is_it).
            rl = board_proposals.paylocity_relation(refs, r_refs)
            if rl == "same" and not board_proposals.paylocity_ids_agree(refs, r_refs):
                rl = "kinds"
            same_board = rl == "same"
        else:
            rl = None
            same_board = bool(waiting_keys(r, owner_holds.get("pairs")) & set(keys))
        q = r.get("proposed") if isinstance(r.get("proposed"), dict) else None
        asked = ((f", proposed {q.get('at') or '?'} by {q.get('by') or '?'}"
                  + (f": \"{q['why'][:240]}\"" if q.get("why") else "")) if q else "")
        other_spelling = (f" (as {r['board']})" if r.get("board")
                          and r["key"] != board_key(kind, ref) else "")
        if not same_board:
            if rl in ("tenant", "unknown", "kinds"):
                doubts.append(f"{r['board']} is already waiting in {r['store']} for this "
                              f"company{asked}, and "
                              + relation_words(rl, show(kind, ref), r["board"], refs, r_refs,
                                               page_said))
            else:
                doubts.append(f"a different board, {r['board']}, is already waiting in "
                              f"{r['store']} for this company{asked}")
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
            doubts.append(f"this board{other_spelling} is already in the owner's queue "
                          f"({r['store']}), filed "
                          f"{f.get('on') or '?'} by {f.get('by')} with its doubt: "
                          f"\"{(r.get('why') or 'none recorded')[:240]}\"; a doubt filed "
                          f"there is his to settle")
        elif q:
            # A BOARD QUESTION PENDING IN AGENT PROPOSALS IS HIS TOO, whatever
            # board it is about (blind review, 2026-11-21). The same board,
            # proposed and waiting, was "the question already asked" here, so
            # the two checks answered it and the board was wired while the
            # proposal - "claimed by more than one company on the board - an
            # acquisition to settle" - still waited on him, and discovery and
            # wire_embedded.py held the company for it.
            doubts.append(f"this board{other_spelling} is already waiting in Agent proposals "
                          f"for this company{asked}; a board waiting on a person is his to "
                          f"settle")
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
    if pl:
        # A RECORD'S OWN PAGE THAT FAILED ON THE DAY IS NO DOUBT TO FILE (eighth
        # blind review, 2026-11-22). records_by_page asked it because nothing
        # else compares this board with the record, and it answered 503, timed
        # out or said to slow down: held, the find was filed in his queue as
        # "its own page could not be read", and every later plan held it as
        # already there, though the next read would have answered. Listed with
        # the reason, and asked again by the next plan.
        said_of = ctx.get("record_said") or {}
        day = [(f"{o.get('name') or o['id']}'s Paylocity board is on file by "
                f"{short((o.get('ats') or {}).get('ref'))}",
                str(said_of[o["id"]])[len("transient:"):].strip())
               for o, o_refs in ctx.get("paylocity_records") or []
               if o["id"] != cid and o["id"] in by_id
               and str(said_of.get(o["id"]) or "").startswith("transient:")
               and board_proposals.paylocity_relation(refs, o_refs) == "unknown"]
        # ...and so is a HELD board's (holds_by_page)
        held_of = ctx.get("hold_said") or {}
        for h, rl in loose:
            said = str(held_of.get(spelling("paylocity", h.get("ref"))) or "")
            if rl == "unknown" and said.startswith("transient:"):
                day.append((f"the owner holds {h['board']}",
                            said[len("transient:"):].strip()))
        if day:
            whose, how = day[0]
            return _skip(base, "unreadable",
                         f"{whose}, which {show(kind, ref)} cannot be compared with but by the "
                         f"/jobs/All/ address its own page states, and asking that page failed "
                         f"on the day ({how}); it is asked again on the next plan, and a failure "
                         f"on the day is not a doubt to file", error=how, **_carry(doubts))
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
        leans = leans_of(refs, cid, ctx) if pl else {}
        if leans:
            base["leans_on"] = leans    # the boards on file it was compared by
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
                          # the /All/ address the board's own page states, which
                          # every door after meets it by (file_found keeps it)
                          **({"page_ref": page_ref} if page_ref else {}),
                          "saw": seen["saw"], "times": seen["times"],
                          "verified": detail, "board_calls_itself": calls,
                          "identity": "MISMATCH" if mismatch else "unknown",
                          "identity_why": identity_why,
                          "postings": len(titles), "titles": titles[:SAMPLE],
                          "page": seen["page"]}}


def plan(results: dict, companies: list, board: dict, say=print, reviewed=None,
         stores: dict | None = None, numbers: dict | None = None, fresh=(),
         pages: dict | None = None) -> list:
    """Every company in the finder's results, decided. Network happens here.

    Each row is made safe to keep - an address redacted, half a character
    dropped - BEFORE it is printed or kept, so nothing the boards sent is
    ever written or shown in a form the worklist could not hold. `stores` is
    read_stores() as cmd_plan read it (read here when left out). `numbers`,
    when given, is filled with every Workable account number this run
    followed: {number: its slug, or None}. `fresh` are the companies whose
    readers' doubts keep_doubts kept in this very run. `pages`, when given,
    is filled with what each Paylocity board's own page this run asked
    stated (page_addresses_of).
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
    slugs = ctx["held_boards"]["slugs"]     # the file's, and this run's beside it
    for c in companies:
        a = c.get("ats") or {}
        if numbered(a.get("type"), a.get("ref")):
            for slug in slugs.get(_number(a.get("ref"))) or []:
                ctx["holders"][board_key("workable", slug)].append(c["id"])
    # a Paylocity board named by its /List/ address is known by the /All/
    # address its own page states, before anything is keyed (fifth blind
    # review, 2026-11-22)
    addressed_by_page(results, ctx)
    named, claims = {}, collections.defaultdict(list)
    ctx["paylocity_finds"] = []         # [(company id, every address)] in this run
    for cid, entry in results.items():
        named[cid] = boards_named(entry)
        for b in named[cid][0]:
            for k in board_keys(b, ctx):
                claims[k].append(cid)
            if b["type"] == "paylocity":
                ctx["paylocity_finds"].append((cid, board_refs_of(b)))
    # a record on file whose Paylocity board a find of this run cannot be
    # compared with is asked once for the /All/ address its page states
    # (seventh blind review, 2026-11-22) - for a find consider compares, and
    # no other (eighth blind review, 2026-11-22)
    finds = [(cid, board_refs_of(b)) for cid, _c, b, _seen in compared(results, ctx)]
    asked = records_by_page(ctx, finds)
    ctx["records_asked"] = asked
    n_asked = sum(1 for w in ctx["record_said"].values() if w != "out of scope")
    if n_asked:
        say(f"NOTE: asked {many(n_asked, 'record')} on file for the /jobs/All/ "
            f"address its Paylocity board's own page states; "
            f"{len(asked)} stated one")
    # ...and so is a board the owner holds (eighth blind review, 2026-11-22)
    held_asked = {k: w for k, w in holds_by_page(ctx, finds).items() if w != "out of scope"}
    if held_asked:
        say(f"NOTE: asked {many(len(held_asked), 'board')} the owner holds for the /jobs/All/ "
            f"address its own page states; "
            f"{sum(1 for w in held_asked.values() if w == 'stated')} stated one")
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
    if pages is not None:
        pages.update(page_addresses_of(ctx))
    return rows


def page_addresses_of(ctx: dict) -> dict:
    """{exact spelling: the /All/ address its own page stated} for every
    Paylocity board this run asked whose page stated one the door keeps -
    found boards, records', held boards'. The worklist carries it to apply
    (`page_addresses`): the plan's own record of what each page said, which
    a row's `page_ref` and `leans_on` must agree with to be leaned on."""
    return {k: a for k, said in sorted((ctx.get("said") or {}).items())
            if k.startswith("paylocity:")
            and (a := board_proposals.paylocity_address((said or {}).get("address")))}


def leans_of(refs: list, cid: str, ctx: dict) -> dict:
    """{exact spelling: address} of the boards on file this Paylocity board
    was compared by what their own page stated (records_by_page), where its
    addresses alone did not tell them apart: what a WIRE row leans on, named
    on the row. Apply leans on an address only for a row that names it, and
    only where the worklist's page_addresses says it too (eighth blind
    review, 2026-11-22: an address a worklist added cleared a record)."""
    out, asked = {}, ctx.get("records_asked") or {}
    for o, o_refs in ctx.get("paylocity_records") or []:
        got = asked.get(o["id"])
        if not got or o["id"] == cid:
            continue
        before = [r for r in o_refs if r != got["address"]]
        if board_proposals.paylocity_relation(refs, before) != "apart":
            out[spelling("paylocity", got["ref"])] = got["address"]
    # ...and a board the owner holds whose page this plan asked (holds_by_page)
    for h in (ctx.get("held_boards") or {}).get("paylocity") or []:
        if h.get("page_stated") and h["id"] != cid \
                and board_proposals.paylocity_relation(refs, h["refs_on_file"]) != "apart":
            out[spelling("paylocity", h["ref"])] = h["page_stated"]
    return dict(sorted(out.items()))


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


def _line(row: dict, name: str | None = None) -> str:
    """One row of the plan as a person reads it - under `name` where given:
    the look prints a WIRE row under the name of the record it writes
    (review 10, 2026-11-23: a row moved to another company printed the
    worklist's name, and the company wired was never named)."""
    what = show(row["type"], row["ref"]) if row.get("type") else ""
    head = (f"  {row['action']:<5} {str(row['name'] if name is None else name)[:28]:<30} "
            f"{what[:44]:<46}")
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
                note += (f"; NOTE a page off their site points at "
                         f"{' and '.join(dict.fromkeys(_fsb.TENANT_FORMS[v] for v in tenants))} "
                         f"somewhere no board here covers ({', '.join(tenants)})")
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
        pages: dict = {}
        rows = plan(results, companies, board, reviewed=reviewed, stores=stores,
                    numbers=numbers, fresh=fresh, pages=pages)
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
               # what each Paylocity board's own page this plan asked stated,
               # by the board's exact spelling: a row's page_ref and the
               # addresses a WIRE row leans on (leans_on) are leaned on in apply
               # only where this says them too
               "page_addresses": pages,
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
    pages = doc.get("page_addresses")
    if not isinstance(pages, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in pages.items()):
        # what each board's own page stated, which apply compares by: a file
        # that does not say it, or says it in another shape, says nothing
        # apply may lean on
        raise ValueError(f"{path} does not carry what the boards' own pages stated as "
                         f"page_addresses {{board: address}}")
    for row in doc["rows"]:
        leans = row.get("leans_on", {})
        if not isinstance(leans, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in leans.items()):
            raise ValueError(f"{path} has a row whose leans_on is not {{board: address}}")
    return doc, hashlib.sha256(raw).hexdigest()


def row_keys(row: dict) -> list:
    """Every key a planned row's board is known by: its own, and for a
    Workable board its site asks for by number, the number's (board_keys)."""
    keys = {board_key(row.get("type"), row.get("ref")),
            *_also_keys(row.get("type"), row.get("ref"))}
    if row.get("account"):
        keys.add(board_key("workable", row["account"]))
    return sorted(keys)


def row_refs(row: dict) -> list:
    """Every address a planned row's board is known by: its ref, and for a
    Paylocity board the /All/ address its own page stated."""
    return board_proposals.board_refs(row.get("type"), row.get("ref"), row.get("page_ref"))


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
    # AN ADDRESS ONLY THE ROW SAYS IS NEVER LEANED ON (eighth blind review,
    # 2026-11-22): a row's page_ref clears holds and records in apply and goes
    # into the wired record's note, which every door after reads back, so it is
    # leaned on only where the plan recorded that page stating it
    pages = held_for.get("pages") or {}
    if kind == "paylocity" and row.get("page_ref") is not None \
            and pages.get(spelling(kind, ref)) != row.get("page_ref"):
        return "evidence", (f"the row says its own page states its address is "
                            f"{str(row.get('page_ref'))[:160]}, and the plan recorded no such "
                            f"answer from that page (page_addresses): an address only the "
                            f"row says is never leaned on")
    # ...NOR ONE THE PLAN NEVER ASKS (review 10, 2026-11-23): the plan asks the
    # page of a board named without its /All/ id or its tenant's name and of no
    # other (addressed_by_page), so a page_ref on a board known by both is the
    # worklist's alone - edited in with page_addresses, it put a second /All/
    # id on the record for every door after
    if kind == "paylocity" and row.get("page_ref") is not None \
            and _whole(board_proposals.board_refs(kind, ref)):
        return "evidence", (f"the row says its own page states its address is "
                            f"{str(row.get('page_ref'))[:160]}, and {show(kind, ref)} is "
                            f"already named by its /jobs/All/ id and its tenant's name: the "
                            f"plan asks no such board's page, so an address only the worklist "
                            f"says is never leaned on - plan again")
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
    # AN ACCOUNT ONLY THE ROW SAYS IS NEVER LEANED ON (review 9, 2026-11-23).
    # A row's `account` is the Workable number their site asks for the board
    # by: below, the board meets the owner's holds, its holders and the rows
    # waiting for the company under that number's key too, and the record's
    # note says it, which every door after reads back as a pair on file. Taken
    # at the worklist's word, an edited `account` waived the company's own
    # unruled row for another board - and the row dropped off his queue - and
    # put Workable's words on a greenhouse record. So it is leaned on only for
    # a Workable row, in digits, where the plan's own record of the numbers it
    # followed or a pair on file opens it under this very slug (account_tie),
    # and the look prints how. (Listed twice under it, above, a row is still
    # left alone: that only ever holds.) That is enough to HOLD by. The
    # number goes into the note only on more (account_noted), and a row
    # waiting under it is taken for this board only by a pair on file (below):
    # the worklist's workable_numbers alone ties an edited account to any slug.
    if row.get("account") and not account_tie(row, held_for.get("numbers"),
                                              held_for.get("pairs_on_file")):
        acct = str(row.get("account"))[:40]
        return "evidence", (
            f"the row says their site asks for the board as Workable account {acct}, and "
            + (f"{show(kind, ref)} is not a Workable board" if kind != "workable" else
               "that is not an account number as the plan writes one (digits, as text)"
               if not isinstance(row.get("account"), str)
               or not _DIGITS.fullmatch(row["account"]) else
               f"neither the plan's record of the numbers it followed (workable_numbers) nor "
               f"anything on file opens that account under apply.workable.com/{ref}")
            + ": an account only the row says is never leaned on - plan again")
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
    # CHECK 2 IS ASKED AGAIN, OF THE RECORD AS IT READS NOW (review 10,
    # 2026-11-23). The row's `judged` was held to the record, and the board's
    # names to neither: a row moved to another company - its id, its `judged`
    # and its check 1 page set to T Co's - wired X Co's board to T Co though
    # the row's own check 2 said the board names "X Co", and the note and
    # board_owner said "X Co" on T Co's record. The strict judge (no request)
    # is asked again with the names the row says the board states, and the
    # name the note and board_owner quote must be one of them.
    said_names = c2["names"] if isinstance(c2["names"], list) else []
    if not said_names or c2.get("name") not in said_names:
        return "evidence", (f'the row says the board names "{str(c2.get("name"))[:80]}", which '
                            f'is not a name it says the board states: check 2 is not shown '
                            f'for this row - plan again')
    judge = getattr(verify_boards, "proven", None)
    again = judge(c, {"names": [str(x) for x in said_names], "stated": True,
                      "how": c2.get("how")}) if callable(judge) else None
    if not (isinstance(again, dict) and again.get("ok") is True):
        return "evidence", ("check 2 does not prove the board is this company's as the record "
                            "reads now: "
                            + (str(again.get("why") or "the strict judge said no")[:240]
                               if isinstance(again, dict) else
                               "verify_boards.proven is not in this checkout")
                            + " - plan again")
    # NOTHING BUT THE DOOR'S OWN WORDS IS READ BACK FROM THE NOTE (review 10,
    # 2026-11-23). The note quotes the page check 1 names and the name check 2
    # quotes, word for word, and every door after reads "asks for it as
    # Workable account N; its postings open under ..." (workable_pairs) and
    # "its own page states its address is ..." (stated_addresses) back out of
    # any note: an edited check_2.name, or a page on their own host carrying
    # those words, planted a pair on a greenhouse record - and the next plan,
    # unedited, waived another company's own unruled row by it - or a second
    # address on a Paylocity record. So the note is built as it will be
    # written, and those words must be found in it exactly as the door's own
    # part of it says them.
    if note_apart(row, bool(row.get("account")) and bool(account_noted(
            row, held_for.get("numbers"), held_for.get("pairs_on_file")))):
        return "evidence", ("the page check 1 quotes, or the name check 2 quotes, would put "
                            "into the record's note words every door after reads back as a "
                            "Workable account's slug or a Paylocity board's address, which the "
                            "door did not say - plan again")
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
    pl, refs = kind == "paylocity", row_refs(row)
    # what the pages of boards on file stated, for this row alone where it and
    # the worklist both say it (leaned): a record's, a held board's
    lean = leaned(row, pages) if pl else {}
    if pl:
        # A PAYLOCITY BOARD MEETS THE OWNER'S HOLDS BY EVERY ADDRESS ON BOTH
        # SIDES (paylocity_relation): the same board as the plan does, and any
        # it cannot tell apart from one is left alone (sixth blind review,
        # 2026-11-22: a hold at a bare /List/2115 was walked past) - a held
        # board known, for this row, by what its own page stated when the plan
        # asked it (holds_by_page; eighth blind review, 2026-11-22)
        met = []
        for h in owner_holds.get("paylocity") or []:
            if h["id"] == cid:
                continue
            a = lean.get(spelling("paylocity", h.get("ref")))
            h_refs = list(dict.fromkeys([*_held_refs(h), a])) if a else _held_refs(h)
            rl = board_proposals.paylocity_relation(refs, h_refs)
            if rl == "same" and not board_proposals.paylocity_ids_agree(refs, h_refs):
                rl = "kinds"            # the tenant's name alone (refused_first)
            if rl == "same":
                met.append(h)
            elif rl in ("tenant", "unknown", "kinds"):
                return h["code"], (f"{h.get('refusal') or h['words']}; and "
                                   + relation_words(rl, show(kind, ref), h["board"], refs,
                                                    h_refs, row.get("page_said"))
                                   + " - plan again, and the plan holds it for the owner")
    else:
        met = [h for k in keys for h in (owner_holds.get("keys") or {}).get(k) or []]
    for h in met:
        if h["id"] == cid or (h["code"] == "number" and h["id"] in reading):
            continue
        if h["code"] == "refused":
            mine, whose = refusal_tie(h, c, by_id, held_for.get("duplicates"),
                                      held_for.get("dismissed"))
            if not mine:
                # another company's refusal: the plan files it for the owner
                return h["code"], (f"since the plan, {h.get('refusal') or h['words']} - "
                                   f"{whose}: plan again, and the plan holds it for the "
                                   f"owner with the refusal quoted")
            return h["code"], (f"since the plan, {h['words']}; it is taken for this "
                               f"company's because {whose} - tell the owner")
        return h["code"], f"since the plan, {h['words']}"
    # a Workable account held under a number the plan never followed, beside
    # a board that states one of the names it goes by, or found for a company
    # that goes by one (its name or an also_known_as, as on file now: blind
    # review, 2026-11-21)
    for code, words in named_holds(owner_holds, kind, c2.get("names") if c2.get("stated") else [],
                                   cid, _their_names(c)):
        return code, f"{words} - plan again"
    seen = {tuple(x) for x in row.get("waiting_seen") or []
            if isinstance(x, list) and len(x) == 2}
    # THE KEYS A ROW WAITING FOR THE COMPANY MAY BE TAKEN FOR THIS BOARD BY are
    # the board's own, and a waiting Workable number's slug where a pair ON
    # FILE says it (waiting_keys, below) - nothing the worklist alone says.
    # Review 10, 2026-11-23: the repair of review 9 took a row waiting under
    # the row's account where check 1's own sighting asked by that number and
    # the worklist's workable_numbers opened it under this slug - and
    # check_1.saw is a worklist field like the other two: edited with them,
    # the company's own unruled row was waived, it dropped off his queue and
    # the note planted the pair. The account still holds (above).
    clearing = set(row_keys({"type": kind, "ref": ref}))
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
        if pl and w.get("type") == "paylocity":
            # THE PLAN PASSES A WAITING ROW ONLY AS THIS VERY BOARD, BY AN ID
            # THAT AGREES (consider): a worklist saying it saw one tied by less
            # - the tenant's name alone - is not taken at its word (eighth
            # blind review, 2026-11-22). AND BY ADDRESSES ON FILE ALONE (review
            # 10, 2026-11-23): the board as the row spells it, beside the row
            # waiting as the file keeps it (its ref and the page_ref filed with
            # it) - never the /All/ address the worklist says the find's own
            # page states (page_ref, page_addresses: both worklist fields),
            # which an edit set to the waiting row's own address, and the
            # company's own unruled row was waived, dropped off his queue, and
            # the note planted that address for every door after
            w_refs = board_proposals.board_refs("paylocity", w.get("ref"), w.get("page_ref"))
            own = board_proposals.board_refs(kind, ref)
            rl = board_proposals.paylocity_relation(own, w_refs)
            if not (rl == "same" and board_proposals.paylocity_ids_agree(own, w_refs)):
                by_page = board_proposals.paylocity_relation(refs, w_refs) == "same" \
                    and board_proposals.paylocity_ids_agree(refs, w_refs)
                return "waiting", (
                    f"{w['board']} is waiting in {w['store']} for it, and "
                    + (f"only the address the worklist says {show(kind, ref)}'s own page "
                       f"states ({str(row.get('page_ref'))[:160]}) ties the two by an id, "
                       f"and nothing on file says it: the worklist's word takes no row off "
                       f"his queue; a board waiting on a person is his to settle - tell the "
                       f"owner (his ruling on that row settles it)"
                       if by_page else
                       (relation_words("kinds" if rl == "same" else rl, show(kind, ref),
                                       w["board"], own, w_refs, row.get("page_said"))
                        if rl != "apart" else f"it is another board than {show(kind, ref)}")
                       + "; a board waiting on a person is his to settle"))
        elif not waiting_keys(w, held_for.get("pairs_on_file")) & clearing:
            # ANY OTHER WAITING ROW ONLY AS THIS VERY BOARD, under a key it is
            # known by (consider): the plan holds a company beside a different
            # board waiting on a person and records none in waiting_seen, so a
            # worklist saying it saw one - another platform's board, or a row
            # of another platform beside a Paylocity board - is not taken at
            # its word (review of the eighth blind review's fixes, 2026-11-22:
            # re-checked only for a Paylocity row beside a Paylocity board, an
            # edit wired past the company's own unruled row for another board,
            # and the row dropped off his queue). BY KEYS APPLY MAY LEAN ON
            # (review 9, 2026-11-23, its repair, and review 10): the board's
            # own (`clearing`, above), and a waiting Workable number's slug
            # only where a pair on file says it - never the worklist's own
            # workable_numbers, nor the row's `account`, nor check 1's
            # sighting, each of which an edit writes: together they waived the
            # company's own unruled row under that number (they still hold:
            # held_boards and the holders read them)
            asked_by = isinstance(row.get("account"), str) \
                and w.get("key") == board_key("workable", row["account"])
            return "waiting", (
                f"{w['board']} is waiting in {w['store']} for it, and it is not shown to be "
                f"{show(kind, ref)}: "
                + (f"it is the Workable account number the row says their site asks for the "
                   f"board by, and nothing on file opens that number under "
                   f"apply.workable.com/{ref} - the numbers the plan followed and what check "
                   f"1 saw are the worklist's word, which takes no row off his queue; a board "
                   f"waiting on a person is his to settle - tell the owner"
                   if asked_by else
                   f"the two meet under no key apply may know either by (its own, or a "
                   f"Workable number's slug a pair on file says); a board waiting on a person "
                   f"is his to settle - plan again"))
    if pl:
        # a record on file whose Paylocity board is this one, or that its
        # addresses do not tell apart from it (consider; fifth and sixth blind
        # reviews, 2026-11-22) - on file since the plan, or not asked by the
        # plan that made the row
        # what a record's own page stated, for the one row whose plan was
        # compared by it and says so (leans_on), where the worklist's
        # page_addresses says it too, and while the record still reads the
        # board that page is of (eighth blind review, 2026-11-22: taken from
        # the worklist's word alone, an invented address cleared a record)
        on_file = []
        for o, o_refs in (held_for.get("paylocity_records")
                          or board_proposals.paylocity_records(list(by_id.values()))):
            if o["id"] == cid or o["id"] not in by_id:
                continue
            a = lean.get(spelling("paylocity", (o.get("ats") or {}).get("ref")))
            on_file.append((o, list(dict.fromkeys([*o_refs, a])) if a else o_refs))
        recs = [(o, record_relation(refs, o_refs)) for o, o_refs in on_file]
        same = [o for o, rl in recs if rl == "same"]
        if same:
            return "moved", f"{show(kind, ref)} is now on file as the board of {same[0]['name']}"
        for rl in RECORD_HOLDS:
            others = [o for o, r_ in recs if r_ == rl]
            if others:
                words = records_words(rl, refs, others, show(kind, ref), row.get("page_said"),
                                      refs_of={o["id"]: o_refs for o, o_refs in on_file})
                return "moved", f"{words} - plan again"
    else:
        held = [by_id[i]["name"] for i in dict.fromkeys(i for k in keys
                                                         for i in holders.get(k, []))
                if i != cid and i in by_id]
        if held:
            return "moved", f"{show(kind, ref)} is now on file as the board of {held[0]}"
    if n < shown.get(cid, 0):
        return "moved", (f"the public board now shows {many(shown[cid])} for it, more than "
                         f"the {n} this board read - plan again")
    return None


# a Workable account's words as the door writes them (board_proposals.ACCOUNT_WORDS,
# what workable_pairs reads back), with what leads into them on a held row's
# doubt ("...; their site asks for it as ...")
_ACCOUNT_LEAD = re.compile(r"(?:;\s*)?(?:their site\s+)?"
                           + board_proposals.ACCOUNT_WORDS.pattern)


def held_words(row: dict, numbers: dict | None, pairs: dict | None) -> tuple:
    """(a HOLD row's doubt - its queue_row's identity_why - as it may be
    filed, [the words taken out of it]).

    Every door after reads "asks for it as Workable account N; its postings
    open under apply.workable.com/<slug>" in a filed row's doubt as a pair on
    file (board_proposals.workable_pairs), and leans on that pair to take a
    row waiting under the number for the slug's board. So a held row files
    those words only of its own board's number where account_noted shows it
    (the plan writes them there: consider), or of a pair the file already
    says; any other such words in the worklist's doubt are taken out - as a
    page_ref only the row says is (repair of review 9, 2026-11-23: an edit
    appended them to a greenhouse row's doubt, the write filed them, and the
    next plan, unedited, wired past another company's own unruled row by
    that pair)."""
    q = row.get("queue_row") if isinstance(row.get("queue_row"), dict) else {}
    words = q.get("identity_why")
    if not isinstance(words, str):
        return words, []
    own = (row["account"], row.get("ref")) if row.get("account") \
        and account_noted(row, numbers, pairs) else None
    removed = []

    def keep(m):
        if (m.group(1), m.group(2)) == own or (pairs or {}).get(m.group(1)) == m.group(2):
            return m.group(0)
        removed.append(m.group(0).lstrip("; "))
        return ""
    while True:                         # what is left can spell the words again
        left = _ACCOUNT_LEAD.sub(keep, words)
        if left == words:
            return words, removed
        words = left


def queue_row_apart(row: dict) -> str | None:
    """Why a HOLD row's queue row is not the row's own, in words, or None.

    The plan writes the queue row of a held board from the row itself: its
    company, its board as the site spelled it, the page that named it. Filed
    as the worklist says it, an edited queue row put another board on the
    owner's card (and held it for every company after), or one company's
    board and doubt on another's card, while the look printed each row's own
    line (review 10, 2026-11-23). So a queue row is filed only where its
    company, its board and its page are the row's."""
    q = row.get("queue_row")
    if not isinstance(q, dict):
        return None                     # file_found says what is wrong with it
    f = q.get("found") if isinstance(q.get("found"), dict) else {}
    if q.get("id") != row.get("id"):
        return (f"its queue row is filed for {str(q.get('id'))[:60]!r}, not for the row's own "
                f"company - plan again")
    if (f.get("type"), f.get("ref")) != (row.get("type"), row.get("ref")):
        return (f"its queue row files {show(f.get('type'), f.get('ref'))[:120]} where the row "
                f"holds {show(row.get('type'), row.get('ref'))[:120]} - plan again")
    page = (row.get("check_1") or {}).get("page") if isinstance(row.get("check_1"), dict) \
        else None
    if q.get("page") != page:
        return (f"its queue row names the page {str(q.get('page'))[:120]} where check 1 names "
                f"{str(page)[:120]} - plan again")
    return None


def queue_rows(holds: list, standing: dict | None = None, pages: dict | None = None,
               numbers: dict | None = None, pairs: dict | None = None) -> list:
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

    AN ADDRESS ONLY THE ROW SAYS IS NEVER FILED. Every door after meets the
    board by a filed row's `page_ref`, so it goes on the row only where the
    worklist's page_addresses (`pages`) records that page stating it (eighth
    blind review, 2026-11-22). NOR IS AN ACCOUNT ONLY THE ROW SAYS: a
    Workable account's words go into the filed doubt only where held_words
    lets them (`numbers`, the worklist's workable_numbers, and `pairs`, the
    pairs said on file).
    """
    out = []
    for r in holds:
        q = r.get("queue_row")
        if not isinstance(q, dict):
            out.append(q)
            continue
        q = dict(q, read_at=r.get("read_at"))
        f = q.get("found") if isinstance(q.get("found"), dict) else {}
        if q.get("page_ref") is not None and (pages or {}).get(
                spelling(f.get("type"), f.get("ref"))) != q.get("page_ref"):
            q.pop("page_ref")
        words = held_words(r, numbers, pairs)[0]
        if isinstance(words, str):
            q["identity_why"] = words
            more = [d for d in (standing or {}).get(r["id"]) or []
                    if str(d.get("why") or "").strip() not in words]
            if more:
                q["identity_why"] = words + "".join(
                    "; " + doubt_words(d, ON_THIS_ROW) for d in more)
        out.append(q)
    return out


def note_for(row: dict, today: str, noted: bool = False) -> str:
    """Both checks in one line, kept on the record as ats_note - and, for a
    Workable board their site asks for by number, the number: nothing else on
    file would say which account the site embeds if the slug stopped
    answering. Said only where `noted` (account_noted showed it): every door
    after reads those words back as a pair on file, so the worklist's own
    workable_numbers alone never writes them (repair of review 9,
    2026-11-23)."""
    c1, c2 = row["check_1"], row["check_2"]
    read_on = str(row.get("read_at") or "")[:10]
    asks = account_words(row) if noted else ""
    # the /All/ address a Paylocity board's own page states, in the words
    # board_proposals.PAGE_WORDS reads back: a record wired at a bare /List/
    # address is then still compared with the same board's /All/ address,
    # and with every other board's (sixth blind review, 2026-11-22)
    stated = board_proposals.paylocity_address(row.get("page_ref")) \
        if row.get("type") == "paylocity" else None
    return (f"wired {today}: {pointer(c1, full=False)}"
            + (f", and {asks}" if asks else "")
            + (f"; its own page states its address is {stated}" if stated else "")
            + f"; the board names \"{c2['name']}\"; {many(row['postings'])} read"
            + (f" {read_on}" if read_on and read_on != today else ""))


def note_apart(row: dict, noted: bool) -> bool:
    """Would the row's own words - the page check 1 quotes, the name check 2
    quotes - put into the record's note anything every door after reads back
    as a pair or an address on file (board_proposals.ACCOUNT_WORDS,
    PAGE_WORDS) beyond what the door's own part of the note says? The note
    as it will be written, against the same note with those two quoted as
    nothing (review 10, 2026-11-23)."""
    bare = dict(row, check_1=dict(row["check_1"], page="their site"),
                check_2=dict(row["check_2"], name="-"))
    real, own = note_for(row, "0000-00-00", noted), note_for(bare, "0000-00-00", noted)
    return any(rx.findall(real) != rx.findall(own)
               for rx in (board_proposals.ACCOUNT_WORDS, board_proposals.PAGE_WORDS))


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
    holders = holders_of(companies, s["found"])
    # under EVERY slug the number is known by: the worklist's word is added
    # to a pair on file, never put in its place (review 10, 2026-11-23: an
    # edited workable_numbers moved a record on the number off the slug the
    # file named, and a second record was wired to the account)
    for number, ids in numbers.items():
        for slug in board_proposals.number_slugs(pairs, resolved).get(number) or []:
            holders[board_key("workable", slug)] += ids
    board = board if board is not None else read_board()
    shown = shown_now(board)
    wires = [r for r in doc["rows"] if r["action"] == "WIRE"]
    ids = collections.Counter(r["id"] for r in wires)
    keys = collections.Counter(k for r in wires for k in row_keys(r))
    twice = {k for k, n in list(ids.items()) + list(keys.items()) if n > 1}
    # TWO PAYLOCITY ROWS THAT MAY BE ONE BOARD are listed twice too, whatever
    # their keys: one board by its two ids, one with no tenant's name, shares
    # none (sixth blind review, 2026-11-22: two companies naming it by its
    # two ids in one run were both wired)
    pl_rows = [(r["id"], row_refs(r)) for r in wires if r.get("type") == "paylocity"]
    for i, (a_id, a_refs) in enumerate(pl_rows):
        for b_id, b_refs in pl_rows[i + 1:]:
            if board_proposals.paylocity_relation(a_refs, b_refs) != "apart":
                twice |= {a_id, b_id}
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
                "duplicates": duplicate_groups(companies, board),
                "dismissed": duplicates_dismissed(),
                "paylocity_records": board_proposals.paylocity_records(companies, s["found"]),
                # what the plan recorded each board's own page stating: a row's
                # page_ref and what it leans on are leaned on only where this
                # says them too
                "pages": doc.get("page_addresses") or {},
                # what may tie a Workable number to a slug: a waiting row is
                # taken for a board by the pairs said on file alone (review 10,
                # 2026-11-23), and an account is written into a note by them,
                # or by the plan's own record of the numbers it followed for a
                # row's own account that check 1's own sighting asks by - never
                # the worklist's record alone (account_noted; review 9,
                # 2026-11-23, and its repair)
                "numbers": resolved, "pairs_on_file": pairs}

    wire, left = [], []
    for row in wires:
        problem = wire_problem(row, by_id, holders, s["ruled"], s["proposals"], twice, shown,
                               now, held_for=held_for)
        if problem:
            left.append({"row": row, "code": problem[0], "why": problem[1]})
        else:
            wire.append(row)
    holds = [r for r in doc["rows"] if r["action"] == "HOLD"]
    # a queue row that is not its row's own is not filed (queue_row_apart)
    apart = {r["id"]: why for r in holds if (why := queue_row_apart(r))}
    queued = queue_rows([r for r in holds if r["id"] not in apart], standing,
                        held_for["pages"], resolved, pairs)
    # asked of the same door the write goes through, so the look and the
    # write cannot disagree about which rows the queue will take
    filing = board_proposals.file_found(queued, by, write=False, force=True,
                                        companies=companies)
    filing = dict(filing, skipped=[{"id": cid, "why": why} for cid, why in apart.items()]
                  + list(filing["skipped"]))
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
            # every address apply leans on for a row it wires, printed in the
            # look and covered by its code
            "leans": {r["id"]: lean_lines(r, held_for["pages"]) for r in wire},
            # ...and the Workable account number each is asked for by, with
            # what ties it to the slug wired and whether that clears a row and
            # goes into the note (account_said): printed, and covered
            "accounts": {r["id"]: [r["account"], account_said(r, resolved, pairs)]
                         for r in wire if r.get("account")},
            # every row waiting on the owner for a company it wires, each taken
            # for this very board (wire_problem passed it): wired, the company
            # leaves his queue, so the look names each and the code covers it
            # (review 10, 2026-11-23: a waived row was never mentioned)
            "takes": {r["id"]: [[w["store"], w["board"]]
                                for w in held_for["waiting"].get(r["id"]) or []]
                      for r in wire},
            # the wired rows whose note says their account number (account_noted)
            "noted": sorted(r["id"] for r in wire if r.get("account")
                            and account_noted(r, resolved, pairs)),
            # what a held row's doubt files with, and what it is filed without
            # (held_words), as the look prints each
            "held_words": {r["id"]: held_words(r, resolved, pairs) for r in holds},
            "skips": [r for r in doc["rows"] if r["action"] == "SKIP"],
            "standing": standing, "reaches": reaches, "missing": missing,
            "doubts": s["doubts"]}


def _spelled(key) -> str | None:
    """The Paylocity ref a `spelling` key is of, or None for any other key."""
    try:
        ref = json.loads(key[len("paylocity:"):]) if str(key).startswith("paylocity:") else None
    except ValueError:
        return None
    return ref if isinstance(ref, str) else None


def leaned(row: dict, pages: dict | None) -> dict:
    """{exact spelling: address} of what a WIRE row says it was compared by
    (`leans_on`) that the worklist's page_addresses says too: an address only
    one of the two says - a worklist edited to add it - is never leaned on,
    and nor is one under a key that spells no Paylocity board."""
    return {k: a for k, a in (row.get("leans_on") or {}).items()
            if isinstance(a, str) and (pages or {}).get(k) == a and _spelled(k)
            and board_proposals.paylocity_address(a)}


def lean_lines(row: dict, pages: dict | None) -> list:
    """[whose page, address] for every address apply leans on for this row:
    its own page's (page_ref) and the pages of boards on file it was compared
    by (leaned) - each printed in the look, and in the code it prints."""
    out = []
    if row.get("type") == "paylocity" and row.get("page_ref"):
        out.append(["its own page", row["page_ref"]])
    for k, a in sorted(leaned(row, pages).items()):
        out.append([f"the page of {short(_spelled(k))}", a])
    return out


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
        # every address apply leans on, as the look printed each
        "leans": sorted([cid, *x] for cid, xs in d["leans"].items() for x in xs),
        # every Workable account number it leans on, and what ties each
        "accounts": sorted([cid, *x] for cid, x in d["accounts"].items()),
        # every row waiting on the owner that a wire takes off his queue
        "takes": sorted([cid, *x] for cid, xs in d["takes"].items() for x in xs),
        "filed_pages": sorted([q.get("id"), q.get("page_ref")] for q in d["queued"]
                              if isinstance(q, dict) and q.get("id") in filing
                              and q.get("page_ref")),
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
        print(_line(r, name=d["on_file"][r["id"]][0]))
        for whose, address in d["leans"].get(r["id"]) or []:
            print(f"        leans on what the plan was told: {whose} states its address is "
                  f"{address}")
        if r["id"] in d["accounts"]:
            print(f"        leans on its account number: {d['accounts'][r['id']][1]}")
        for store, board in d["takes"].get(r["id"]) or []:
            print(f"        takes {board}, waiting in {store} for it, as this very board: "
                  f"wired, it leaves his queue")
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
        plan_words, removed = d["held_words"].get(cid) or (None, [])
        plan_words = str(plan_words or "")
        now_words = str((queued.get(cid) or {}).get("identity_why") or "")
        # WHAT HIS CARD WILL SAY, as it is filed (review 10, 2026-11-23: the
        # look printed the row's doubts, and an edited queue row filed other
        # words in front of him)
        f = (queued.get(cid) or {}).get("found") or {}
        print(f"        files for him {show(f.get('type'), f.get('ref'))}, in these words: "
              f"\"{now_words}\"")
        if now_words != plan_words and now_words.startswith(plan_words):
            print(f"        and, kept since the plan: {now_words[len(plan_words):].lstrip('; ')}")
        # the Workable accounts its doubt files, which every door after reads
        # as pairs on file, and the words no door files (held_words)
        for number, slug in board_proposals.ACCOUNT_WORDS.findall(now_words):
            print(f"        files with it, as a pair every door after reads: Workable account "
                  f"{number} opens under apply.workable.com/{slug}")
        for words in removed:
            print(f"        files it without what the worklist's doubt says of a Workable "
                  f"account nothing here shows: \"{words[:200]}\"")
        if (queued.get(cid) or {}).get("page_ref"):
            print(f"        files with it what its own page states: its address is "
                  f"{queued[cid]['page_ref']}")
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
            c["ats_note"] = note_for(row, today, noted=row["id"] in d["noted"])
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
