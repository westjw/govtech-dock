#!/usr/bin/env python3
"""Wire a found board to its company - only when two separate checks agree.

    python3 scripts/wire_found.py plan --results site_boards.json --out worklist.json
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
        the owner's queue through board_proposals.file_found.
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
are one journalled append to data/embedded_ats.json.

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
VERSION = 2                 # 2: rows carry what they were judged against
WAITING = board_proposals.WAITING

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
            "mode": cand.get("mode")})
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


def also_seen(entry: dict, key: str, website: str) -> dict:
    """What else the finder saw: {elsewhere, off_site, elsewhere_off_site}.

    `elsewhere` is a job platform this repo has no reader for, named on the
    company's OWN site. That one is a doubt (see consider): it is a second
    place they may be hiring through, and City Detect is the case on file -
    its site embedded a platform nothing here read while its old board still
    answered and still named it. A platform recorded with no page is counted
    as theirs, because nothing shows it was not.

    The other two were seen on pages that are NOT theirs - a different board,
    or such a platform. Neither is one of the checks and how noisy either
    list is has not been measured, so they hold nothing back; they are kept
    on the row and printed for whoever reads the plan.
    """
    out = {}
    other = sorted({show(o.get("type"), o.get("ref")) for o in entry.get("off_site") or []
                    if board_key(o.get("type"), o.get("ref")) != key})
    if other:
        out["off_site"] = other[:6]
    own, away = set(), set()
    for e in entry.get("elsewhere") or []:
        vendor = str(e.get("vendor") or "").strip()
        page = str(e.get("page") or "").strip()
        if vendor:
            (own if not page or page_belt._same_site(page, website or "") else away).add(vendor)
    if own:
        out["elsewhere"] = sorted(own)[:6]
    if away - own:
        out["elsewhere_off_site"] = sorted(away - own)[:6]
    return out


# ------------------------------------------------- what is on file, no network

def _suspects() -> dict:
    """{company id: discovery's own note} from data/ats_suspects.json."""
    sus = admin.read("ats_suspects.json", {})
    items = sus.get("suspects", sus) if isinstance(sus, dict) else {}
    if isinstance(items, list):
        items = {i.get("id"): i for i in items if isinstance(i, dict)}
    return {k: str((v or {}).get("note") or "its slug did not match the company's name")
            for k, v in (items or {}).items() if isinstance(v, dict)}


def _refusals() -> dict:
    """{company id: [earlier refusals]} from the two stores that keep them.

    The stores do not talk to each other, so both are read. A refusal in
    board_proposal_rulings.json carries no board; the row it ruled on is
    still in embedded_ats.json, which is where the board is read from.
    """
    out = collections.defaultdict(list)
    listed = {r.get("id"): r for r in admin.read(board_proposals.FOUND.name, [])
              if isinstance(r, dict)}
    for cid, v in admin.read(board_proposals.RULED.name, {}).items():
        if not isinstance(v, dict) or v.get("accept"):
            continue
        f = (listed.get(cid) or {}).get("found") or {}
        out[cid].append({"store": "Boards we found",
                         "key": board_key(f.get("type"), f.get("ref")) if f else None,
                         "board": show(f.get("type"), f.get("ref")) if f else None,
                         "by": v.get("by") or "?", "on": v.get("on") or "?",
                         "why": str(v.get("why") or "").strip()})
    for key, p in admin.read("agent_proposals.json", {}).items():
        if not (isinstance(p, dict) and key.startswith("board:")
                and p.get("status") == "rejected"):
            continue
        cid = p.get("id") or key.split(":", 1)[1]
        out[cid].append({"store": "Agent proposals",
                         "key": board_key(p.get("ats_type"), p.get("ats_ref")),
                         "board": show(p.get("ats_type"), p.get("ats_ref")),
                         "by": p.get("ruled_by") or "?", "on": p.get("ruled_on") or "?",
                         "why": str(p.get("ruled_why") or "").strip()})
    return out


def _waiting() -> dict:
    """{company id: [boards already waiting on a person for it]}.

    A row in Boards we found that nobody has ruled, or a board proposal still
    pending in Agent proposals. The SAME board found again is no new doubt -
    it is the question already asked, and the two checks may now answer it.
    A DIFFERENT board is: two finders, two boards, one company.
    """
    out = collections.defaultdict(list)
    ruled = admin.read(board_proposals.RULED.name, {})
    for r in admin.read(board_proposals.FOUND.name, []):
        f = r.get("found") if isinstance(r, dict) else None
        if isinstance(f, dict) and r.get("id") not in ruled:
            out[r["id"]].append({"store": "Boards we found",
                                 "key": board_key(f.get("type"), f.get("ref")),
                                 "board": show(f.get("type"), f.get("ref"))})
    for key, p in admin.read("agent_proposals.json", {}).items():
        if isinstance(p, dict) and key.startswith("board:") and p.get("status") == "pending":
            out[p.get("id") or key.split(":", 1)[1]].append({
                "store": "Agent proposals",
                "key": board_key(p.get("ats_type"), p.get("ats_ref")),
                "board": show(p.get("ats_type"), p.get("ats_ref"))})
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


def standing(companies: list, board: dict) -> dict:
    """Everything a plan holds a company against, read once, plus the three
    places the plan remembers what a board answered (`total`, `read`, `said`),
    each filed under the board's exact spelling."""
    return {"by_id": {c["id"]: c for c in companies},
            "holders": holders_of(companies),
            "suspects": _suspects(),
            "refusals": _refusals(),
            "waiting": _waiting(),
            "acquisitions": acquisition_doubts(companies, board),
            "shown": shown_now(board),
            "crawled": str((board or {}).get("generated") or "")[:10],
            "total": {}, "read": {}, "said": {}}


# ------------------------------------------------------------------ the plan

def _skip(base: dict, kind: str, why: str, **more) -> dict:
    return {"action": "SKIP", **base, "kind": kind, "why": why, **more}


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
    same = [r for r in refused if r["key"] == key]
    desk = [r for r in refused if r["store"] == "Boards we found"]
    if same or desk:
        r = (same or desk)[0]
        why = (f"refused earlier in {r['store']}: {r['by']} on {r['on']}"
               + (f" - {r['why']}" if r["why"] else ""))
        if not same:
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
    if first.get("spellings"):
        doubts.append(f"their site spells this board more than one way "
                      f"({' and '.join(first['spellings'][:4])}); where case matters those "
                      f"are different boards, and only {show(kind, ref)} was read")
    if unusable:
        doubts.append("their site also names a board this door cannot use ("
                      + "; ".join(unusable[:3]) + ")")
    extra = also_seen(entry, key, website)
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
    held = [by_id[i]["name"] for i in ctx["holders"].get(key, []) if i != cid and i in by_id]
    if held:
        doubts.append(f"{show(kind, ref)} is already on file as the board of "
                      + " and ".join(held[:4]))
    twins = [(by_id.get(i) or {}).get("name") or i for i in claims.get(key, []) if i != cid]
    if twins:
        doubts.append(f"the same board was found for {' and '.join(twins[:4])} in this run")
    for r in refused:
        doubts.append(f"an earlier board for this company was refused ({r['board']}, "
                      f"{r['by']} on {r['on']}" + (f": {r['why']}" if r["why"] else "") + ")")
    for r in ctx["waiting"].get(cid) or []:
        if r["key"] != key:
            doubts.append(f"a different board, {r['board']}, is already waiting in "
                          f"{r['store']} for this company")

    # --- the network: three questions, each asked once per spelling --------
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
                         f"{GIANT} postings is skipped with the giants")
        base["board_total"] = total
        if total >= GIANT:
            return _skip(base, "giant", f"{GIANT_WHY} (the board holds {total} postings)")

    # The live read. Two companies whose sites name one board are both held,
    # and neither needs the board asked twice to say so.
    if exact not in ctx["read"]:
        ctx["read"][exact] = (*add_company.read_board(block), admin.now())
    ok, detail, read, base["read_at"] = ctx["read"][exact]
    if not ok:
        # kept with its error and never retried: a refusal is an answer for
        # today, and a board that will not read is not a board that is wrong
        return _skip(base, "unreadable", f"the board did not read: {detail}", error=detail)
    titles = [str(r.get("title") or "").strip() for r in read]
    titles = [t for t in titles if t]
    if kind in PARTIAL:
        # said where the count is said: "9 postings" from a reader that
        # filters is not the size of the board, and whoever rules on the row
        # should not have to know which readers those are
        detail = f"{detail} - {PARTIAL[kind]}, and the whole board holds {total}"
    shown = ctx["shown"].get(cid, 0)
    base.update({"postings": len(titles), "verified": detail, "titles": titles[:SAMPLE],
                 "shown_before": shown})
    if len(titles) >= GIANT:
        return _skip(base, "giant", f"{GIANT_WHY} ({len(titles)} postings read)")

    if exact not in ctx["said"]:
        ctx["said"][exact] = verify_boards.board_says(kind, ref)
    said = ctx["said"][exact]
    stated_n = _count(said.get("n"))
    if stated_n is not None and stated_n >= GIANT:
        # whatever a probe counts, it does not count more than is there: a
        # board that states 500 holds 500. (It may hold more than it states,
        # which is why a SMALL count from a probe is never read as small.)
        return _skip(base, "giant", f"{GIANT_WHY} (the board states {stated_n} postings)")
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

    if passed and not doubts:
        return {"action": "WIRE", **base}

    mismatch = not passed and stated and bool(names)
    if not passed:
        doubts.insert(0, why_2)
    lead = f'the board names "{calls}", which is this company, but ' if passed else ""
    identity_why = f"{lead}{'; '.join(doubts)}; {pointer(seen)}"
    return {"action": "HOLD", **base, "doubts": doubts,
            "queue_row": {"id": cid, "name": base["name"], "was": was.get("type"),
                          "found": {"type": kind, "ref": ref},
                          "saw": seen["saw"], "times": seen["times"],
                          "verified": detail, "board_calls_itself": calls,
                          "identity": "MISMATCH" if mismatch else "unknown",
                          "identity_why": identity_why,
                          "postings": len(titles), "titles": titles[:SAMPLE],
                          "page": seen["page"]}}


def plan(results: dict, companies: list, board: dict, say=print) -> list:
    """Every company in the finder's results, decided. Network happens here.

    Each row is made safe to keep - an address redacted, half a character
    dropped - BEFORE it is printed or kept, so nothing the boards sent is
    ever written or shown in a form the worklist could not hold.
    """
    ctx = standing(companies, board)
    named, claims = {}, collections.defaultdict(list)
    for cid, entry in results.items():
        named[cid] = boards_named(entry)
        for b in named[cid][0]:
            claims[b["key"]].append(cid)
    rows = []
    for cid in sorted(results):
        boards, unusable = named[cid]
        row = privacy.redact_deep(_encodable(
            consider(cid, results[cid], boards, unusable, claims, ctx)))
        rows.append(row)
        if row["action"] == "SKIP" and row["kind"] == "no-candidate":
            continue                    # most of the file; counted, not listed
        say(_line(row))
    return rows


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
        return (f"{head} {many(row['postings'])}; board names "
                f"\"{row['check_2']['name']}\"; {pointer(row['check_1'], full=False)}{note}")
    if row["action"] == "HOLD":
        return f"{head} {many(row['postings'])}; {'; '.join(row['doubts'])}"
    return f"{head} {row['why']}"


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
        rows = plan(results, companies, board)
        n = counts(rows)
        doc = {"made": MADE, "version": VERSION, "generated": admin.now(),
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
    return doc, hashlib.sha256(raw).hexdigest()


def wire_problem(row: dict, by_id: dict, holders: dict, ruled: dict,
                 proposals: dict, twice: set, shown: dict,
                 now: dt.datetime) -> tuple[str, str] | None:
    """(code, words) for a WIRE row that must be left alone, or None.

    Not a second plan: nothing is fetched and no board is asked again. Two
    kinds of question. First, does the row carry what the plan only ever
    writes on a WIRE row - both checks, a dated read with postings on it, a
    size under the giants' line - because a row that cannot show them is not
    one this door wires, however it came to say WIRE. Second, everything
    that can have MOVED between the plan and this moment: a person wired the
    company by hand, corrected its website or its name, recorded a parent, a
    note or a refusal, or gave the same board to somebody else.
    """
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
    total = _count(row.get("board_total"))
    if n >= GIANT or (total or 0) >= GIANT:
        return "giant", GIANT_WHY
    if kind in PARTIAL and total is None:
        return "size", (f"{PARTIAL[kind]}, and the row carries no total for the board, so "
                        f"it was never shown to be under {GIANT} postings")
    key = board_key(kind, ref)
    if cid in twice or key in twice:
        return "twice", "the worklist lists this company or this board more than once"
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
    held = [by_id[i]["name"] for i in holders.get(key, []) if i != cid and i in by_id]
    if held:
        return "moved", f"{show(kind, ref)} is now on file as the board of {held[0]}"
    if n < shown.get(cid, 0):
        return "moved", (f"the public board now shows {many(shown[cid])} for it, more than "
                         f"the {n} this board read - plan again")
    return None


def queue_rows(holds: list) -> list:
    """What to file for these held boards, each stamped with ITS row's read.

    One stamp per row. The queue row carries no time of its own, so there is
    nothing to drift from the one this door judges the row by: a worklist
    whose reads are a month old cannot file them as today's.
    """
    return [dict(r["queue_row"], read_at=r.get("read_at"))
            if isinstance(r.get("queue_row"), dict) else r.get("queue_row") for r in holds]


def note_for(row: dict, today: str) -> str:
    """Both checks in one line, kept on the record as ats_note."""
    c1, c2 = row["check_1"], row["check_2"]
    read_on = str(row.get("read_at") or "")[:10]
    return (f"wired {today}: {pointer(c1, full=False)}; the board names "
            f"\"{c2['name']}\"; {many(row['postings'])} read"
            + (f" {read_on}" if read_on and read_on != today else ""))


def decide(doc: dict, companies: list, by: str, now: dt.datetime) -> dict:
    """What applying this worklist would do against the data as it is now."""
    by_id = {c["id"]: c for c in companies}
    holders = holders_of(companies)
    ruled = admin.read(board_proposals.RULED.name, {})
    proposals = admin.read("agent_proposals.json", {})
    shown = shown_now(admin.read("board.json", {}))
    wires = [r for r in doc["rows"] if r["action"] == "WIRE"]
    ids = collections.Counter(r["id"] for r in wires)
    keys = collections.Counter(board_key(r.get("type"), r.get("ref")) for r in wires)
    twice = {k for k, n in list(ids.items()) + list(keys.items()) if n > 1}

    wire, left = [], []
    for row in wires:
        problem = wire_problem(row, by_id, holders, ruled, proposals, twice, shown, now)
        if problem:
            left.append({"row": row, "code": problem[0], "why": problem[1]})
        else:
            wire.append(row)
    holds = [r for r in doc["rows"] if r["action"] == "HOLD"]
    # asked of the same door the write goes through, so the look and the
    # write cannot disagree about which rows the queue will take
    filing = board_proposals.file_found(queue_rows(holds), by, write=False, force=True,
                                        companies=companies)
    return {"wire": wire, "left": left, "holds": holds, "filing": filing,
            # what each company to be wired reads as at this moment: the
            # code covers it, so a record corrected after the look is caught
            # even where the correction would still pass
            "on_file": {r["id"]: [by_id[r["id"]].get("name"), by_id[r["id"]].get("website"),
                                  _akas(by_id[r["id"]])] for r in wire},
            "skips": [r for r in doc["rows"] if r["action"] == "SKIP"]}


def expect_code(sha: str, d: dict, by: str) -> str:
    """The worklist's own hash, and a hash of what the look said it would do.

    Two halves so a refusal can say WHICH moved: the file, or the data under
    it. Reasons go in as short codes, not sentences - "30 hours old" becomes
    "31 hours old" on its own and must not read as the plan changing.
    """
    doing = json.dumps({
        "by": by,
        "wire": [[r["id"], spelling(r["type"], r["ref"]), *d["on_file"][r["id"]]]
                 for r in d["wire"]],
        "left": [[x["row"]["id"], x["code"]] for x in d["left"]],
        "file": d["filing"]["added"],
        "not_filed": sorted(str(s.get("id")) for s in d["filing"]["skipped"]),
        "filing_error": bool(d["filing"]["error"])}, sort_keys=True)
    return f"{sha[:16]}-{hashlib.sha256(doing.encode()).hexdigest()[:16]}"


def report(doc: dict, d: dict) -> None:
    by_row = {r["id"]: r for r in d["holds"]}
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

    with admin.desk_lock():
        companies = admin.read_companies()
        before = json.loads(json.dumps(companies))
        d = decide(doc, companies, by, dt.datetime.now().astimezone())
        code = expect_code(sha, d, by)
        n = counts(doc["rows"])
        print(f"worklist {pathlib.Path(a.worklist).name}, planned {doc.get('generated')}: "
              f"{len(doc['rows'])} rows\n  sha256 {sha}\n")
        report(doc, d)
        n_wire, n_hold = len(d["wire"]), len(d["filing"]["added"])
        tail = f"{skipped_words(n)}" + (f"; left alone {len(d['left'])}" if d["left"] else "") \
            + (f"; not filed {len(d['filing']['skipped'])}" if d["filing"]["skipped"] else "")

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
        if not n_wire and not n_hold:
            print(f"\nnothing to write: wired 0, held 0, {tail}")
            return 0
        if not a.write:
            print(f"\nwould wire {n_wire}, would hold {n_hold}, {tail}")
            print("LOOKED ONLY. Nothing was written. To write exactly this:")
            print(f"  python3 scripts/wire_found.py apply {a.worklist} --by '{by}' "
                  f"--write --expect {code}")
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
                print(f"\nSTOPPED, and nothing was written: {bad}")
                return 1
        if n_hold:
            if n_hold > journal.BLAST:
                print(f"\n{n_hold} held rows in one entry is over the journal's "
                      f"{journal.BLAST}-record limit; the count and every name are "
                      f"printed above, so the write is confirmed")
            rep = board_proposals.file_found(
                queue_rows(d["holds"]), by, write=True,
                force=n_hold > journal.BLAST, companies=companies,
                why=(f"{many(n_hold, 'found board')} held for the owner: the board did "
                     f"not name the company, or something on file argues with it; each "
                     f"row says which (worklist {short_sha})"))
            if rep["error"] or rep["added"] != d["filing"]["added"]:
                what = (f"the held rows were NOT filed: {rep['error']}" if rep["error"]
                        else f"the queue took {len(rep['added'])} held row(s) where "
                             f"the look listed {n_hold}")
                print(f"\nSTOPPED after the wiring: {many(n_wire, 'board')} "
                      f"{'is' if n_wire == 1 else 'are'} wired, and {what}. "
                      f"Run the look again; it will list what is left.")
                return 1
    print(f"\nwired {n_wire}, held {n_hold}, {tail}")
    changed = ["data/companies.json"] * bool(n_wire) + ["data/embedded_ats.json"] * bool(n_hold)
    print(f"written by {by}: {', '.join(changed)} and the journal (data/admin_journal.jsonl, "
          f"with data/admin_journal.archive.jsonl when it rolls). Commit them together.")
    print("Postings appear after the nightly crawl reads the new boards, not before.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="read the boards live and freeze a worklist")
    p.add_argument("--results", action="append", required=True,
                   help="a results file find_site_boards.py wrote (repeatable)")
    p.add_argument("--out", required=True, help="where the frozen worklist goes")
    w = sub.add_parser("apply", help="apply a frozen worklist; a look unless --write")
    w.add_argument("worklist")
    w.add_argument("--by", required=True, help="agent:<label>, the author on the journal")
    w.add_argument("--write", action="store_true")
    w.add_argument("--expect", help="the code the look printed; required with --write")
    a = ap.parse_args(argv)
    return cmd_plan(a) if a.cmd == "plan" else cmd_apply(a)


if __name__ == "__main__":
    sys.exit(main())
