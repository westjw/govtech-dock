#!/usr/bin/env python3
"""The queue for boards found hiding inside a careers page.

find_embedded_ats.py reads a careers page nothing could enumerate and pulls
out the ATS reference the page itself names - Circuit's had a Paylocity board
sitting in its HTML three times over, worth 8 readable postings, after months
of "page scan found no listings".

None of it is written to companies.json, and this is the file that explains
why. Two failure modes, both already met in the real data:

  THE PARENT'S BOARD. Prepared - assistive AI for 911 centres - links to
  Axon's greenhouse board. 500 postings, every one stamped company_name
  "Axon", because Axon acquired them. Wiring that up reports a parent's
  requisitions as the subsidiary's, which is the one thing CLAUDE.md forbids
  by name.

  THE OPERATING ENTITY. Circuit's own board calls itself "TFR Transit Inc".
  Probably right, and "probably" is how a corporate-finance Concourse ended
  up with seven postings on a govtech board.

So each proposal arrives with the evidence a person needs to rule in one
look: what the board calls itself, how many postings it holds, and a sample
of titles. The rule is the same one this repo applies to a stranger's
submission - a claim, not a fact.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FOUND = DATA / "embedded_ats.json"
RULED = DATA / "board_proposal_rulings.json"

# A company is offered a board only while it has none: `html` is a careers
# page nothing can enumerate, `unknown` is no board on file.
WAITING = ("html", "unknown")
# What a row FILED here may say about whose board it is. Never "matches": a
# row is filed because something was in doubt, and wire_embedded.py reads
# "matches" in this file as leave to wire it.
HELD_AS = ("unknown", "MISMATCH")
FILE_ACTION = "boards-found"
# A FILED ROW SAYS "this board reads N postings, here are eight of them", and
# that is only true for as long as the read behind it is live. A worklist
# found a month later would put September's count and September's titles in
# front of the owner under today's date. So a row carries the time its board
# was read, and is filed only while that read is this fresh - the same limit
# wire_found.py holds the boards it wires to.
MAX_AGE_HOURS = 24


def _read(p: pathlib.Path, default):
    return json.loads(p.read_text()) if p.exists() else default


# --- what the owner holds, read once for every door that could wire past him
#
# THE ONE READER. A reader's doubt, a row a door filed for him, and a refusal
# in either store each hold a company's board until he says otherwise - and
# for a while only scripts/wire_found.py knew it. The second blind review of
# that door (2026-11-19) showed the weekly discovery job probing a doubted
# company and writing the very board the readers doubted, and wire_embedded.py
# wiring an older finder's row for a company whose door-held row the queue
# could not take. So the question "is this company the owner's right now" is
# asked here, of the files that answer it, by every door that writes a board.
DOUBTS = "board_doubts.json"
PROPOSALS = "agent_proposals.json"
SUSPECTS = "ats_suspects.json"
# the journal action of a reader's doubt put on a row already waiting here
DOUBT_ADDED = "boards-found-doubt"


class Unreadable(ValueError):
    """A file that holds the owner's answers is on disk and cannot be read.

    Never read as empty. A refusal, a doubt or a row waiting on him that
    cannot be read is one nothing stands behind, and a door that took the
    file for empty would wire the board somebody said no to.
    """


def _dir(data) -> pathlib.Path:
    return pathlib.Path(data) if data is not None else DATA


def _rows_shape(got) -> str | None:
    if isinstance(got, list) and all(isinstance(r, dict) and isinstance(r.get("id"), str)
                                     and r["id"].strip() for r in got):
        return None
    return "a list of rows, each with its company's id"


def _map_shape(got) -> str | None:
    if isinstance(got, dict) and all(isinstance(k, str) and isinstance(v, dict)
                                     for k, v in got.items()):
        return None
    return "{key: its record}"


def _object_shape(got) -> str | None:
    return None if isinstance(got, dict) else "one JSON object"


def _list_of_records(got) -> str | None:
    if isinstance(got, list) and all(isinstance(r, dict) for r in got):
        return None
    return "a list of records"


def _link_health_shape(got) -> str | None:
    links = got.get("links", {}) if isinstance(got, dict) else None
    if isinstance(links, dict) and all(
            isinstance(v, list) and all(isinstance(r, dict) for r in v) for v in links.values()):
        return None
    return "{links: {company id: [its link records]}}"


def _site_identity_shape(got) -> str | None:
    rows = got.get("rows", []) if isinstance(got, dict) else None
    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
        return None
    return "{rows: [records]}"


def _suspects_shape(got) -> str | None:
    # what discovery writes ({company id: {ats, note, on}}), or the same
    # under "suspects", or a list of rows carrying their id
    items = got.get("suspects", got) if isinstance(got, dict) else None
    if isinstance(items, dict) and all(isinstance(k, str) and isinstance(v, dict)
                                       for k, v in items.items()):
        return None
    if isinstance(items, list) and all(isinstance(r, dict) and isinstance(r.get("id"), str)
                                       for r in items):
        return None
    return "{company id: discovery's note}"


def doubt_id(key: str, rec: dict) -> str:
    """The company a record in board_doubts.json is about. The first shape
    keyed one record per company by its id; a record kept since carries its
    `id`, under the key "<company id>@<when>"."""
    cid = rec.get("id") if isinstance(rec, dict) else None
    if isinstance(cid, str) and cid.strip():
        return cid
    return key.split("@", 1)[0] if "@" in key else key


def _doubts_shape(got) -> str | None:
    # A RECORD WITHOUT WORDS HOLDS NOTHING, so it is not read as a doubt: one
    # written {"why": ""} by hand, or {}, planned WIRE in the plan and was
    # wired by apply (second review, 2026-11-19). It stops the door instead.
    # A record that SETTLES doubts (wire_found.py settle, on the owner's word)
    # carries his words and the keys it settles.
    if not isinstance(got, dict) or not all(isinstance(k, str) and isinstance(v, dict)
                                            for k, v in got.items()):
        return "{key: a reader's doubt}"
    for k, v in got.items():
        if not isinstance(v.get("why"), str) or not v["why"].strip():
            return f"{{key: a reader's doubt}}: {k!r} says no doubt in words"
        if "id" in v and not (isinstance(v["id"], str) and v["id"].strip()):
            return f"{{key: a reader's doubt}}: {k!r} names no company"
        if "settles" in v and not (isinstance(v["settles"], list) and v["settles"] and all(
                isinstance(x, str) and x.strip() for x in v["settles"])):
            return f"{{key: a reader's doubt}}: {k!r} settles no doubt by its key"
    return None


# name -> (what a missing file is, what a file that reads must look like)
SHAPES = {"embedded_ats.json": (list, _rows_shape),
          "board_proposal_rulings.json": (dict, _map_shape),
          PROPOSALS: (dict, _map_shape),
          SUSPECTS: (dict, _suspects_shape),
          DOUBTS: (dict, _doubts_shape)}
# THE FILES THE ACQUISITIONS QUEUE IS BUILT FROM. wire_found.py holds every
# company in that queue (both builders are asked: admin.q_acquisitions and
# acquisitions.q_acquisitions), and the builders read these files leniently or
# not at all: with merge-conflict markers in acquisitions_research.json, a
# company whose parent outside research named fell out of the queue, planned
# WIRE and was written (blind review of 27eeb628, 2026-11-19). So the door reads each
# strictly, in the shape its builder walks, before it asks anything or writes
# anything. A file that is not there is empty.
ACQUISITION_FILES = {"acquisitions_research.json": (dict, _map_shape),
                     "admin_dismissed.json": (dict, _object_shape),
                     "acquisition_rulings.json": (dict, _object_shape),
                     "board_audit.json": (list, _list_of_records),
                     "logo_log.json": (dict, _map_shape),
                     "link_health.json": (dict, _link_health_shape),
                     "site_identity.json": (dict, _site_identity_shape)}
SHAPES.update(ACQUISITION_FILES)


def strict(name: str, data=None):
    """data/<name>, parsed and of its shape. A file that is not there is
    empty; one that is there and does not read, or reads as something else,
    raises Unreadable - unlike admin.read, which hands back its default."""
    empty, shape = SHAPES[name]
    p = _dir(data) / name
    if not p.exists():
        return empty()
    try:
        got = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise Unreadable(f"data/{name} does not read "
                         f"({' '.join(str(exc).split())[:120]})") from None
    problem = shape(got)
    if problem:
        raise Unreadable(f"data/{name} is not {problem}")
    return got


# --- the readers' doubts: one record per doubt, grouped by company ---------
#
# ONE RECORD PER DOUBT, AND A NEW DOUBT IS ALWAYS AN ADDITION. The file first
# held one record per company, and a second doubt rewrote it with the words
# joined by " | " - so a reader's sentence that contained " | " was never
# found again and was appended on every apply, and once the file held sixty
# doubts the journal's runaway rule refused any batch that rewrote a third of
# them, force or not (second review, 2026-11-19). A doubt kept since is its
# own record under "<company id>@<when>", carrying its `id`; the first shape
# is still read. Whole records are compared, never pieces of a reader's words.
#
# A DOUBT IS SETTLED BY THE OWNER, AND BY NOTHING THE DOOR KEEPS. Any ruling of
# his on the company in Boards we found that is later than the doubt settles
# it, whichever row he ruled from. 27eeb628's rule - only a ruling on a
# row that CARRIED the doubt settles it - left doubts no ruling could ever
# settle (JustFOIA's record is not carried by its row, so after his ruling on
# its card the doubt would have stood for good) and a desk that refused merges
# with advice he could not follow (decided 2026-11-19: the owner's desk is
# never blocked by the door's bookkeeping). Where no ruling can reach him - an
# older ruling, a company on a board, merged away or refused - the doubt stands
# and he is TOLD; on his word, `wire_found.py settle` writes a record that
# settles it (`settles`: the keys of the doubts it settles, his words, who ran
# it). Every reader of this file goes through read_doubts and standing_doubts
# below, so every door honours a settled doubt the same way.

def doubt_records(raw: dict) -> dict:
    """{company id: [its doubts, oldest first]} from board_doubts.json as it
    is on disk; each record a copy carrying its `key` and its `id`, and
    `settled` (who, when, his words) where a settle record names its key. A
    settle record is not a doubt and is not listed."""
    settles: dict = {}
    marks = sorted(((k, v) for k, v in (raw or {}).items()
                    if isinstance(v, dict) and isinstance(v.get("settles"), list)),
                   key=lambda kv: (str(kv[1].get("at") or kv[1].get("on") or ""), kv[0]))
    for k, v in marks:
        for dk in v["settles"]:
            settles.setdefault(dk, {"key": k, "by": v.get("by"), "on": v.get("on"),
                                    "at": v.get("at"), "why": v.get("why")})
    out: dict = {}
    for k, v in (raw or {}).items():
        if isinstance(v, dict) and "settles" not in v:
            cid = doubt_id(k, v)
            rec = dict(v, key=k, id=cid)
            if k in settles:
                rec["settled"] = settles[k]
            out.setdefault(cid, []).append(rec)
    for recs in out.values():
        recs.sort(key=lambda r: (str(r.get("at") or r.get("on") or ""), r["key"]))
    return out


def read_doubts(data=None) -> dict:
    """data/board_doubts.json, grouped by company (doubt_records). A file
    that is on disk and does not read, or holds a record with no words,
    raises Unreadable."""
    return doubt_records(strict(DOUBTS, data))


def _later(a, b) -> bool:
    """Is the moment `a` ({at, on}) shown to be at or after `b`? By the
    stamps where both carry one, else only by a LATER date: the same day with
    nothing to order them by is not shown, and that answer wires less."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    ta, tb = when(a.get("at")), when(b.get("at"))
    if ta and tb:
        return ta >= tb
    oa, ob = str(a.get("on") or "")[:10], str(b.get("on") or "")[:10]
    return bool(oa and ob and oa > ob)


def ruled_since(ruling, doubt: dict) -> bool:
    """Has the owner ruled on this company in Boards we found since the
    doubt was recorded? Only a ruling shown to be later counts (_later). A
    ruling from before the doubt, or from the same day with no stamps to tell
    them apart, leaves the doubt standing - that is the answer that wires
    less."""
    return isinstance(ruling, dict) and _later(ruling, doubt)


def rows_by_id(found) -> dict:
    """{company id: [its rows in Boards we found]}."""
    out: dict = {}
    for r in found or []:
        if isinstance(r, dict):
            out.setdefault(r.get("id"), []).append(r)
    return out


def standing_doubts(grouped: dict, ruled: dict, found=None) -> dict:
    """{company id: [its standing doubts]} - the doubts in `grouped`
    (read_doubts) the owner has not settled: neither a ruling of his on the
    company in Boards we found later than the doubt (`ruled`,
    board_proposal_rulings.json; ruled_since), nor his word through
    `wire_found.py settle` (`settled`). `found` (the queue file) is taken and
    not needed any more: which row he ruled from does not matter."""
    out = {}
    for cid, recs in (grouped or {}).items():
        left = [d for d in recs or [] if not d.get("settled")
                and not ruled_since((ruled or {}).get(cid), d)]
        if left:
            out[cid] = left
    return out


def held_for_the_owner(data=None) -> dict:
    """{company id: why it is the owner's} for every company a door must not
    wire a board for: a reader's doubt that stands, a row a door filed in
    Boards we found that he has not ruled on, a board refused in either
    store (Boards we found, Agent proposals), or a board proposal of its
    still pending in Agent proposals.

    PENDING IS HIS TOO (blind review, 2026-11-21). The wiring door holds a
    company whose board question is waiting in Agent proposals, and the
    weekly discovery sweep probed such a company and wrote the board it
    found: Pend Co's "claimed by more than one company - an acquisition to
    settle". On file that day: board:icsolutions, pending since 2026-09-02,
    with ICSolutions `unknown` and a website, so the sweep would have probed
    it when it came due.

    `data` is the directory the CALLER writes - discovery passes its own, so
    the files it reads are the files beside the companies.json it writes. A
    file that is on disk and does not read raises Unreadable, and the caller
    stops before its first request.
    """
    found = strict("embedded_ats.json", data)
    ruled = strict("board_proposal_rulings.json", data)
    props = strict(PROPOSALS, data)
    doubts = read_doubts(data)
    out: dict = {}
    for cid in sorted(standing_doubts(doubts, ruled)):
        out.setdefault(cid, "a reader's doubt about its board stands")
    for r in found:
        if r.get("filed") and r["id"] not in ruled:
            out.setdefault(r["id"], "a board a door filed for the owner waits in Boards we found")
    for cid, v in sorted(ruled.items()):
        if not v.get("accept"):
            out.setdefault(cid, "a board was refused for it in Boards we found")
    for key, p in sorted(props.items()):
        if key.startswith("board:") and p.get("status") == "rejected":
            out.setdefault(p.get("id") or key.split(":", 1)[1],
                           "a board was refused for it in Agent proposals")
        elif key.startswith("board:") and p.get("status") == "pending":
            out.setdefault(p.get("id") or key.split(":", 1)[1],
                           "a board proposal for it waits in Agent proposals")
    return out


# --- the boards the owner holds, under any company's id: the one reader ----
#
# THREE DOORS WRITE A BOARD, AND ALL THREE ASK THIS. A company merged into its
# duplicate at the desk leaves its doubts, its door-filed row and its
# refusals under the id that was dropped, and nothing moves them; the desk's
# merge is never refused for it (decided after the blind review of 27eeb628,
# 2026-11-19). So a board is held BY BOARD as well as by company - for every
# company whose site names it, under any id, current or merged away - and
# wire_found.py, discover_ats.py and wire_embedded.py all read the holds
# here (held_boards; held_boards_for_the_owner for the two that hold no
# stores of their own), so the three cannot drift. A board is one LOOSE key
# (board_key), and a Workable account is one board under two keys, its
# number and its slug, wherever the two are known to belong together.

# a Paylocity board is the identifier after /jobs/All/ (a uuid) or /jobs/List/
# (a number), with or without the tenant's name after it: find_site_boards
# writes /List/<n> both ways (FAAC's shape, 2026-11-18), and keyed on the
# whole address a hold under one spelling missed the board found under the
# other (blind review, 2026-11-21)
_PAYLOCITY = re.compile(r"/jobs/(?:all|list)/([^/?#]+)")
_ICIMS = re.compile(r"([a-z0-9-]+)\.icims\.com(?:[/?]|$)")
# a Zoho Recruit career site is its host and its page's name: a posting's
# address sits under it (.../jobs/<PageName>/<id>-<title>)
_ZOHO = re.compile(r"([^/?#]+\.zohorecruit\.[a-z]+)/jobs/([^/?#]+)")
# a Comeet board is its company uid, whichever of its two refs names it -
# "<UID>|<TOKEN>" or the hosted board comeet.com/jobs/<slug>/<UID> - and a
# posting under the hosted board is the same board
_COMEET = re.compile(r"(?:^|comeet\.com/jobs/[^/?#]+/)([0-9a-f]{2}\.[0-9a-f]{3})(?=$|[|/?#])")
# the platforms whose board is a whole host: any address on it is the board
HOST_BOARDS = ("jibe", "oracle", "applicantpro", "teamtailor", "successfactors")
# the words wire_found.py writes on a wired record's note and a held row's
# doubt when a site asks for a Workable board by its account number (its
# account_words): the one place the number and the slug are said together
ACCOUNT_WORDS = re.compile(r"asks for it as Workable account (\d+); its postings open under "
                           r"apply\.workable\.com/([A-Za-z0-9][A-Za-z0-9-]*)")


def board_key(kind, ref) -> str:
    """One LOOSE spelling per board, for one question: is this board that one?

    It is asked to notice a collision - the same board found for two
    companies, already held by another, refused before, already waiting - and
    a collision only ever sends a board to a person. So it leans toward
    calling two addresses one board. Case is ignored (SmartRecruiters refs
    are case-sensitive, so two different boards can share a key). An address
    is the same board with or without its scheme, its www. or a last slash.
    And some shapes are read for the part that identifies the board, because
    this repo's own data writes each of them more than one way: a Paylocity address is
    the id after /jobs/All/ or /jobs/List/, with or without the tenant's name
    after it (keyed on the whole address, a board already on file as
    .../<uuid>/<Tenant> was not seen to be the board found as .../<uuid>, and
    would have been wired to a second company); an iCIMS portal is its slug,
    bare or inside its address; a Jibe, Oracle, ApplicantPro, Teamtailor or
    SuccessFactors board is its host; a Zoho Recruit career site is its host and its page's
    name (a posting's address is under it); a Comeet board is its company
    uid, whether the ref is "<UID>|<TOKEN>" or the hosted board's address.
    (The last four were read since 2026-11-19 and keyed on the whole ref, so
    a hold or a refusal written in one spelling missed the other: test
    review of the door, 2026-11-21. Hirebridge's number and Pinpoint's slug
    are one spelling already.)

    IT IS NEVER WHAT GETS READ. Reading goes by the ref exactly as the site
    gave it (wire_found.spelling): on a platform that minds case the loose
    form is a different board, or none.
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
    elif kind in HOST_BOARDS:
        r = r.split("/", 1)[0]
    elif kind == "zohorecruit":
        m = _ZOHO.match(r)
        r = f"{m.group(1)}/jobs/{m.group(2)}" if m else r
    elif kind == "comeet":
        m = _COMEET.search(r)
        r = m.group(1) if m else r
    return f"{kind}:{json.dumps(r)}"


# the tenant's name a Paylocity address ends in, after its /All/ or /List/ id
_PAYLOCITY_TENANT = re.compile(r"/jobs/(?:all|list)/[^/?#]+/([^/?#]+)")


def also_keys(kind, ref) -> list:
    """The other keys one board is known by, beside board_key's: a Paylocity
    board by the tenant's name its address ends in.

    ONE BOARD, TWO IDS (third blind review, 2026-11-21). FAAC's page frames
    /List/2115/FAAC-Incorporated, and the frame lands on
    /All/<uuid>/FAAC-Incorporated: one board (find_site_boards folds the two
    only when both are on one page). board_key keys them 2115 and <uuid>, and
    every Paylocity ref on file is /All/, the refusals included - so a read
    that saw only the /List/ frame planned WIRE past a refusal of the very
    board. Both shapes end in the tenant's name; a collision of names only
    ever sends a board to a person."""
    if kind == "paylocity" and isinstance(ref, str):
        m = _PAYLOCITY_TENANT.search(ref.strip().lower())
        if m:
            return [f"{kind}:{json.dumps('tenant/' + m.group(1))}"]
    return []


_PAYLOCITY_IDS = re.compile(r"/jobs/(all|list)/([^/?#]+)(?:/([^/?#]+))?")


def paylocity_ids(ref) -> tuple:
    """(the /jobs/All/ id, the /jobs/List/ id, the tenant's name) a Paylocity
    address carries, each lowercased, None where it carries none."""
    m = _PAYLOCITY_IDS.search(ref.strip().lower()) if isinstance(ref, str) else None
    if not m:
        return None, None, None
    return (m.group(2) if m.group(1) == "all" else None,
            m.group(2) if m.group(1) == "list" else None, m.group(3) or None)


# A Paylocity board's own /jobs/All/<uuid> address, as its page states it
# (verify_boards' `address`) and as it is read on file: on a filed row
# (`page_ref`) and in a record's note (PAGE_WORDS) - which the wiring door
# no longer writes (review 10's repair, 2026-11-23: the address reached its
# write only through the worklist, an edit planted one, and every door after
# read it as on file; what is read is what a person put there). The SAME
# grammar as the probe's: the tenant's name after the id may carry , ; ( ),
# and a door that refused what the probe stated filed the row as "its own
# page did not state" an address it had (seventh blind review, 2026-11-22).
# In a note the address ends at "; " or the end - no address holds a space.
_PAYLOCITY_ALL = re.compile(r"https://recruiting\.paylocity\.com/recruiting/jobs/all/"
                            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                            r"(?:/[^/?#\s\"'<>]{1,200})?/?", re.I)
PAGE_WORDS = re.compile(r"its own page states its address is (https://\S+?)(?=; |\s|$)")


def paylocity_address(value) -> str | None:
    """`value` when it is a Paylocity board's /jobs/All/<uuid> address, as a
    board's own page states one (no query, no fragment), trimmed; else None."""
    v = value.strip().rstrip("/") if isinstance(value, str) else ""
    return v if v and _PAYLOCITY_ALL.fullmatch(v) else None


def board_refs(kind, ref, *stated) -> list:
    """Every address one board is known by: its ref, and for a Paylocity
    board each /All/ address its own page was found to state."""
    if kind != "paylocity":
        return [ref]
    return [ref, *dict.fromkeys(a for a in (paylocity_address(x) for x in stated) if a)]


def _paylocity_sides(refs) -> tuple:
    alls, lists, tenants = set(), set(), set()
    for r in refs or ():
        a, n, t = paylocity_ids(r)
        alls |= {a} - {None}
        lists |= {n} - {None}
        tenants |= {t} - {None}
    return alls, lists, tenants


def paylocity_relation(a_refs, b_refs) -> str:
    """How two Paylocity boards, each given as every address it is known by
    (board_refs), stand - "same", "tenant", "unknown" or "apart".

    One board, two ids (third blind review, 2026-11-21): /jobs/List/<n> and
    /jobs/All/<uuid> name the same board, and only the tenant's name after
    either ties them. So:

      same     an id of one kind agrees and none disagrees; or neither side
               has an id of the other's kind and the tenant's names agree
               (FAAC's /List/2115/FAAC-Incorporated and its /All/<uuid>/...).
      tenant   ids of one kind DISAGREE under one tenant's name: two boards
               of one tenant, or one the ids do not settle. It holds the board
               for a person and is never "the same board" (sixth blind review,
               2026-11-22: a refusal of /All/<uuid1>/Acme-Inc SKIPped the
               board found at /All/<uuid2>/Acme-Inc, unread).
      unknown  nothing on the addresses compares them: no id of a kind both
               have, and a tenant's name missing on one side (Celona's record
               at a bare .../All/<uuid>, a bare /List/<n> find: fifth blind
               review, 2026-11-22) - or the ids agree on one kind and disagree
               on another - or no id agrees and the tenants' names are not one
               spelling yet may be one name written two ways
               (paylocity_near_names: alike in the finder's form, or the one
               the start of the other). It holds the board for a person.
      apart    ids of one kind disagree under no shared tenant's name, or the
               tenants' names disagree where no id compares - and are not one
               name written two ways: two boards.

    ONE NAME, TWO SPELLINGS (eighth blind review, 2026-11-22). The door keeps
    a tenant's name as the board's own page states it, "," ";" "(" ")"
    included, and find_site_boards writes it only as far as its first other
    character (paylocity_tenant_form): one board's link
    /List/2140/Acme-(US),-Inc is the finder's /List/2140/Acme-, and its page's
    /All/<uuid>/Acme-(US),-Inc. Compared as two strings they were "apart",
    and the door wired past a filed row and a record of the very board.
    """
    a, b = _paylocity_sides(a_refs), _paylocity_sides(b_refs)
    agree = disagree = False
    for kind in (0, 1):
        if a[kind] and b[kind]:
            if a[kind] & b[kind]:
                agree = True
            else:
                disagree = True
    if agree:
        return "unknown" if disagree else "same"
    near = bool(paylocity_near_names(a_refs, b_refs))
    if disagree:
        return "tenant" if a[2] & b[2] else "unknown" if near else "apart"
    if a[2] and b[2]:
        return "same" if a[2] & b[2] else "unknown" if near else "apart"
    return "unknown"


# the characters find_site_boards keeps of the name after a Paylocity id (its
# _paylocity pattern): it cuts the name at the first other one, and strips a
# last "."
_PAYLOCITY_FORM = re.compile(r"[a-z0-9_.-]*")


def paylocity_tenant_form(name) -> str:
    """A Paylocity tenant's name in the finder's form, the one form two names
    are compared in: lowercased, cut at the first character other than a
    letter, a digit, "_", "." or "-", a last "." stripped
    (find_site_boards._paylocity). "Acme-(US),-Inc" is "acme-"."""
    return _PAYLOCITY_FORM.match(str(name or "").lower()).group(0).rstrip(".")


def paylocity_near_names(a_refs, b_refs) -> list:
    """[(a's name, b's name)], sorted: the tenants' names after the two
    sides' ids that are not one spelling and may still be one name - alike
    in the finder's form (paylocity_tenant_form), or the one's form the start
    of the other's (a name cut short). Empty where the two sides share a name,
    or one side has none. A name with no finder's form at all ("(US)-Acme":
    the finder writes no name for it) is the start of nothing - else one such
    record would hold every board found."""
    a, b = _paylocity_sides(a_refs)[2], _paylocity_sides(b_refs)[2]
    if a & b:
        return []
    out = []
    for x in sorted(a):
        for y in sorted(b):
            fx, fy = paylocity_tenant_form(x), paylocity_tenant_form(y)
            if fx and fy and (fx.startswith(fy) or fy.startswith(fx)):
                out.append((x, y))
    return out


def paylocity_ids_differ(a_refs, b_refs) -> bool:
    """Do two Paylocity boards have ids of one kind that share none?"""
    a, b = _paylocity_sides(a_refs), _paylocity_sides(b_refs)
    return any(a[k] and b[k] and not a[k] & b[k] for k in (0, 1))


def paylocity_ids_agree(a_refs, b_refs) -> bool:
    """Do two Paylocity boards share an id of one kind, with none of a kind
    both have disagreeing? The only "same" a SKIP may rest on: the tenant's
    name after a /List/ id and after an /All/ id is "same" by
    paylocity_relation, and those two ids can still be two boards of one
    tenant (seventh blind review, 2026-11-22)."""
    a, b = _paylocity_sides(a_refs), _paylocity_sides(b_refs)
    met = [bool(a[k] & b[k]) for k in (0, 1) if a[k] and b[k]]
    return True in met and False not in met


def paylocity_tenants(refs) -> set:
    """The tenants' names these Paylocity addresses end in, lowercased."""
    return _paylocity_sides(refs)[2]


# A PAYLOCITY POSTING IS ONE JOB, NOT A BOARD (owner, 2026-11-23).
# find_site_boards writes a link to one - .../recruiting/jobs/Details/<id>... -
# as a candidate of its own type, and only the wiring door asks the posting's
# page which board it names (wire_found.resolved_by_posting) and holds that
# board to both checks. No door writes, files or wires a posting as a board:
# discovery does not follow one (it read the board off the posting's page and
# held it to a tenant's name alone), wire_embedded leaves a row of one in no
# pile, and the queue's own door files none.
POSTING_TYPE = "paylocity-posting"
_PAYLOCITY_POSTING = re.compile(r"(?<![a-z0-9.-])recruiting\.paylocity\.com/recruiting/jobs/details/",
                                re.I)


def paylocity_posting(kind, ref) -> bool:
    """Is (type, ref) a Paylocity posting - the finder's posting type, or a
    Paylocity ref on one job's address - and so no board to wire, file or
    read as one?"""
    if kind == POSTING_TYPE:
        return True
    return kind in ("paylocity", None) and isinstance(ref, str) \
        and bool(_PAYLOCITY_POSTING.search(ref.strip()))


def paylocity_conflict(a_refs, b_refs) -> bool:
    """Do two Paylocity boards' ids agree on one kind and disagree on the
    other (paylocity_relation calls that "unknown" too)?"""
    a, b = _paylocity_sides(a_refs), _paylocity_sides(b_refs)
    met = [bool(a[k] & b[k]) for k in (0, 1) if a[k] and b[k]]
    return True in met and False in met


def has_paylocity_id(refs) -> bool:
    """Do these addresses carry a /jobs/All/ or /jobs/List/ id at all? A ref
    with none (a posting's .../jobs/Details/<n>/...) is no board address to
    compare, and is left to the keys."""
    sides = _paylocity_sides(refs)
    return bool(sides[0] or sides[1])


def stated_addresses(companies: list | None, found: list | None) -> dict:
    """{company id: [the /All/ addresses its Paylocity board's own page was
    found to state]}, from what is on file: a record's note (PAGE_WORDS), and
    the `page_ref` of the company's row in Boards we found whose board is the
    board the record reads (a row the owner accepted at the desk is wired as
    it was filed, its /List/ spelling included). No request: what is on file.
    The wiring door writes neither (review 10's repair, 2026-11-23): the
    address reached its write through the worklist alone, and an edit that
    planted one was read here by every door after, which then never asked
    the board's own page and wired the very board to a second company."""
    out = collections.defaultdict(list)
    by_id = {c.get("id"): c for c in companies or [] if isinstance(c, dict)}
    for cid, c in by_id.items():
        a = c.get("ats") or {}
        if a.get("type") != "paylocity":
            continue
        for x in PAGE_WORDS.findall(str(c.get("ats_note") or "")):
            if paylocity_address(x):
                out[cid].append(paylocity_address(x))
    for r in found or []:
        if not isinstance(r, dict):
            continue
        c = by_id.get(r.get("id")) or {}
        a, f = c.get("ats") or {}, r.get("found") if isinstance(r.get("found"), dict) else {}
        stated = paylocity_address(r.get("page_ref"))
        if stated and a.get("type") == "paylocity" == f.get("type") \
                and board_key("paylocity", a.get("ref")) == board_key("paylocity", f.get("ref")):
            out[r["id"]].append(stated)
    return {cid: list(dict.fromkeys(v)) for cid, v in out.items()}


def holders_of(companies: list, found: list | None = None) -> dict:
    """{board key: [ids of the companies already reading from that board]},
    a Paylocity board under every address it is known by - its ref and the
    /All/ address its own page was found to state (stated_addresses). The
    one reader the wiring door and wire_embedded.py ask."""
    out = collections.defaultdict(list)
    stated = stated_addresses(companies, found)
    for c in companies or []:
        a = (c.get("ats") or {}) if isinstance(c, dict) else {}
        if a.get("type") in (None, "unknown", "html") or a.get("ref") is None:
            continue
        refs = board_refs(a["type"], a["ref"], *stated.get(c["id"], ()))
        for k in dict.fromkeys(k for r in refs for k in (board_key(a["type"], r),
                                                         *also_keys(a["type"], r))):
            out[k].append(c["id"])
    return out


def paylocity_records(companies: list | None, found: list | None = None) -> list:
    """[(record, every address its Paylocity board is known by)] for every
    record on file that reads a Paylocity board by an address with an id."""
    stated = stated_addresses(companies, found)
    out = []
    for c in companies or []:
        a = (c.get("ats") or {}) if isinstance(c, dict) else {}
        if a.get("type") == "paylocity" and isinstance(a.get("ref"), str):
            refs = board_refs("paylocity", a["ref"], *stated.get(c.get("id"), ()))
            if has_paylocity_id(refs):
                out.append((c, refs))
    return out


def show(kind, ref) -> str:
    """greenhouse/acme - how the journal and the desk already write a board."""
    if isinstance(ref, list):
        return f"{kind}/{'/'.join(str(x) for x in ref)}"
    return f"{kind}/{ref}"


def workable_number(ref) -> str:
    """A Workable ref as the reader asks it: no space, no last slash."""
    return ref.strip().rstrip("/") if isinstance(ref, str) else ""


def numbered(kind, ref) -> bool:
    """A Workable board written as its account number, not its slug
    ("710855/" is the same number). See wire_found.numbered."""
    return kind == "workable" and isinstance(ref, str) and workable_number(ref).isdigit()


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


def _address(value) -> str | None:
    """`value` when it is a web address, trimmed; else None."""
    v = value.strip() if isinstance(value, str) else ""
    return v if v.lower().startswith(("http://", "https://")) else None


def refusals(stores: dict) -> dict:
    """{company id: [earlier refusals]} from the two stores that keep them
    (`stores`: {"ruled", "proposals", "found"}, each read strictly).

    The stores do not talk to each other, so both are read. A refusal in
    board_proposal_rulings.json carries no board; the row it ruled on is
    still in embedded_ats.json, which is where the board is read from.

    Each refusal also carries `page`: the address it was recorded against,
    where the store kept one - the page a door filed the row from
    (`filed.page`), an Agent proposal's `evidence` - or None. The wiring
    door holds it to the websites on file when the record refused is no
    longer on file (wire_found.refusal_tie); it can be the board's own
    address rather than a page of the company's site, so it is read only
    for whose website it is on.
    """
    out = collections.defaultdict(list)
    listed = {r.get("id"): r for r in stores["found"] if isinstance(r, dict)}
    for cid, v in stores["ruled"].items():
        if not isinstance(v, dict) or v.get("accept"):
            continue
        row = listed.get(cid) or {}
        f = row.get("found") if isinstance(row.get("found"), dict) else {}
        filed = row.get("filed") if isinstance(row.get("filed"), dict) else {}
        out[cid].append({"store": "Boards we found",
                         "key": board_key(f.get("type"), f.get("ref")) if f else None,
                         "board": show(f.get("type"), f.get("ref")) if f else None,
                         "type": f.get("type"), "ref": f.get("ref"),
                         # the names the refused board went by: what it stated, and
                         # whose row it was (held_boards, for a Workable number)
                         "names": [n for n in (row.get("board_calls_itself"), row.get("name"))
                                   if isinstance(n, str) and n.strip()],
                         # whose refusal it was, by the name the row recorded: what a
                         # record merged away since is known by (wire_found.refusal_tie).
                         # NEVER the name the board called itself: that is the
                         # board's claim, and 10 of the 11 board proposals he had
                         # rejected name their true owner (blind review, 2026-11-21)
                         "whose": [n for n in (row.get("name"),)
                                   if isinstance(n, str) and n.strip()],
                         "page": _address(filed.get("page")),
                         # the /All/ address the board's own page stated, where the
                         # door that filed the row asked it (wire_found)
                         "page_ref": paylocity_address(row.get("page_ref")),
                         "by": v.get("by") or "?", "on": v.get("on") or "?",
                         "why": str(v.get("why") or "").strip()})
    for key, p in stores["proposals"].items():
        if not (isinstance(p, dict) and key.startswith("board:")
                and p.get("status") == "rejected"):
            continue
        cid = p.get("id") or key.split(":", 1)[1]
        out[cid].append({"store": "Agent proposals",
                         "key": board_key(p.get("ats_type"), p.get("ats_ref")),
                         "board": show(p.get("ats_type"), p.get("ats_ref")),
                         "type": p.get("ats_type"), "ref": p.get("ats_ref"),
                         "names": [n for n in (p.get("name"),)
                                   if isinstance(n, str) and n.strip()],
                         "whose": [n for n in (p.get("name"),)
                                   if isinstance(n, str) and n.strip()],
                         "page": _address(p.get("evidence")),
                         "by": p.get("ruled_by") or "?", "on": p.get("ruled_on") or "?",
                         "why": str(p.get("ruled_why") or "").strip()})
    return out


def strict_names(*names) -> dict:
    """{a name as verify_boards.strict leaves it: the name as written} for
    every name given that is one. The comparison the by-name hold makes."""
    import verify_boards
    out = {}
    for n in names:
        if isinstance(n, str) and n.strip():
            key = verify_boards.strict(n)[0]
            if key:
                out.setdefault(key, n.strip())
    return out


def their_names(c: dict | None) -> list:
    """A record's name and every other name on file for it."""
    if not isinstance(c, dict):
        return []
    return [c.get("name"), *sorted(str(a) for a in (c.get("also_known_as") or [])
                                   if str(a).strip())]


def slug_of(resolved: dict, number: str) -> str | None:
    """The slug a Workable account number is known to open under, or None.
    `resolved` is a plan's ctx['slugs'] ({number: (slug, how, kind)}), a
    worklist's workable_numbers ({number: slug or null}) or workable_pairs."""
    got = (resolved or {}).get(number)
    slug = got[0] if isinstance(got, (list, tuple)) and got else got
    return slug if isinstance(slug, str) and slug else None


def workable_pairs(companies: list | None, found: list | None) -> dict:
    """{Workable account number: its slug} for every pair this repo has said
    belong together, in wire_found's own words (ACCOUNT_WORDS) on a record's
    note or a queue row's doubt. No request: what is on file."""
    out = {}
    texts = [c.get("ats_note") for c in companies or [] if isinstance(c, dict)]
    texts += [r.get("identity_why") for r in found or [] if isinstance(r, dict)]
    for t in texts:
        for number, slug in ACCOUNT_WORDS.findall(str(t or "")):
            out.setdefault(number, slug)
    return out


def number_slugs(pairs: dict | None, resolved: dict | None = None) -> dict:
    """{Workable account number: [every slug it is known to open under]}: the
    one a pair on file says first (workable_pairs, `pairs`), and beside it the
    one a run followed it to (`resolved`: a plan's ctx['slugs'] or a
    worklist's workable_numbers) where that is another. A run's word is ADDED
    to a pair on file, never put in its place: a hold or a holder under the
    number is one under each slug (review 10, 2026-11-23: apply let the
    worklist's workable_numbers replace a pair on file, and an entry edited to
    open the number under some other slug moved the owner's queued row, his
    refusal and a record holding the number off the slug the file named, and
    a row flipped to WIRE beside them was wired)."""
    out: dict = {}
    for n, s in (pairs or {}).items():
        if isinstance(s, str) and s:
            out.setdefault(n, []).append(s)
    for n in (resolved or {}):
        s = slug_of(resolved, n)
        if s and s not in out.get(n, []):
            out.setdefault(n, []).append(s)
    return out


def numbers_on_file(companies: list) -> dict:
    """{Workable account number: [ids of the records that hold it]}."""
    out = collections.defaultdict(list)
    for c in companies or []:
        a = c.get("ats") or {}
        if numbered(a.get("type"), a.get("ref")):
            out[workable_number(a["ref"])].append(c["id"])
    return out


def held_boards(standing: dict, stores: dict, by_id: dict, resolved: dict | None = None,
                companies: list | None = None, refusals_: dict | None = None) -> dict:
    """Every board the owner holds, under WHATEVER company it is held for:
    {"keys": {board key: [hold]}, "named": [hold], "pairs": {number: slug},
    "slugs": {number: [slug]}, "paylocity": [hold]}, a hold being {id, code,
    board, words, names}. Codes: `standing` (the board
    of a reader's doubt that stands), `queued` (the board of a row a door
    filed in Boards we found that he has not ruled on), `refused` (a board
    refused in either store: a Boards-we-found refusal by the board on its
    row, an Agent-proposals rejection by its ats_type/ats_ref), `waiting` (a
    board proposal still pending in Agent proposals), and `number`
    (a record on a Workable account number: by name where nobody followed
    the number to a slug, under the slug's key where the two are known to
    belong together).

    A WORKABLE BOARD HELD UNDER ITS ACCOUNT NUMBER is one board under two
    keys. Where the number is known to open under a slug - followed by this
    run (`resolved`), or said together on file (workable_pairs) - the hold is
    under the slug's key as well: under EACH, where the two say different
    slugs (number_slugs: a run's word is added to a pair on file, never put in
    its place; `pairs` names the file's slug first). Where it is not, no request ties the number
    to any slug found, and holding every Workable board for it held boards
    that could not be the same account. So the hold goes in `named`, with the
    names it goes by - what that board stated, whose record or row it is -
    and a Workable board going by one of them (verify_boards.strict) is held
    beside it (named_holds). A record on file holding a number nobody
    followed is held the same way, by its names; one holding a number whose
    slug is known is held under the slug's key.

    A PAYLOCITY BOARD is held under every address it is known by - its ref,
    and the /All/ address its own page was found to state where the hold
    carries it (a filed row's `page_ref`, and a refusal of one; the wiring
    door files none since review 10's repair, 2026-11-23) - and
    every Paylocity hold is listed again in `paylocity`, with `type`, `ref`
    and `refs`, for the comparison keys cannot make (paylocity_relation): a
    bare /List/<n> hold and the same board's /All/<uuid>/<Tenant> share no
    key, and the wiring door holds any board it cannot tell apart from one
    (sixth blind review, 2026-11-22).
    """
    keys, named, paylocity = collections.defaultdict(list), [], []
    # A PAIR ON FILE IS NEVER REPLACED BY A RUN'S WORD (review 10, 2026-11-23):
    # a number is held under the slug the file says AND under the one the run
    # followed it to (number_slugs); `pairs` names one slug per number, the
    # file's first, for the questions that take one (found_keys, waiting_keys)
    slugs = number_slugs(workable_pairs(companies, stores.get("found")), resolved)
    pairs = {n: v[0] for n, v in slugs.items()}

    def name_of(cid, *also):
        # the record's name, or - merged away - the name its row, refusal or
        # doubt went by
        return (by_id.get(cid) or {}).get("name") or next(
            (n for n in also if isinstance(n, str) and n.strip()), cid)

    def hold(cid, code, kind, ref, words, *names, page_ref=None, **more):
        h = {"id": cid, "code": code, "board": show(kind, ref), "words": words,
             "names": strict_names(*names, *their_names(by_id.get(cid))), **more}
        refs = board_refs(kind, ref, page_ref)
        for k in dict.fromkeys(k for r in refs for k in (board_key(kind, r),
                                                         *also_keys(kind, r))):
            keys[k].append(h)
        if kind == "paylocity":
            h.update(type=kind, ref=ref, refs=refs)
            paylocity.append(h)
        if numbered(kind, ref):
            known = slugs.get(workable_number(ref)) or []
            for slug in known:
                keys[board_key("workable", slug)].append(h)
            if not known and h["names"]:
                named.append(dict(h, number=workable_number(ref)))

    for cid, recs in (standing or {}).items():
        for d in recs:
            kind, ref = doubt_board(d)
            if kind:
                hold(cid, "standing", kind, ref, (
                    f"{show(kind, ref)} is the board of a reader's doubt that stands about "
                    f"{name_of(cid, d.get('name'))}, recorded {d.get('on') or '?'} by "
                    f"{d.get('by') or '?'}: {str(d.get('why') or '').strip()}"), d.get("name"))
    ruled = stores["ruled"]
    for r in stores["found"]:
        f = r.get("found") if isinstance(r.get("found"), dict) else None
        if f and isinstance(r.get("filed"), dict) and r["id"] not in ruled:
            hold(r["id"], "queued", f.get("type"), f.get("ref"), (
                f"{show(f.get('type'), f.get('ref'))} is in the owner's queue (Boards we found) "
                f"for {name_of(r['id'], r.get('name'))}, filed {r['filed'].get('on') or '?'} by "
                f"{r['filed'].get('by') or '?'} with its doubt: "
                f"\"{str(r.get('identity_why') or 'none recorded')[:240]}\"; a board filed "
                f"there is his to settle"), r.get("board_calls_itself"), r.get("name"),
                page_ref=r.get("page_ref"))
    # a board proposal still pending in Agent proposals is his to settle, for
    # whatever company's site names the board (blind review, 2026-11-21)
    for key, p in sorted((stores.get("proposals") or {}).items()):
        if isinstance(p, dict) and key.startswith("board:") and p.get("status") == "pending" \
                and p.get("ats_type") and p.get("ats_ref") is not None:
            cid = p.get("id") or key.split(":", 1)[1]
            hold(cid, "waiting", p["ats_type"], p["ats_ref"], (
                f"{show(p['ats_type'], p['ats_ref'])} is waiting in Agent proposals for "
                f"{name_of(cid, p.get('name'))}, proposed {str(p.get('at') or '?')[:10]} by "
                f"{p.get('by') or '?'}" + (f" (\"{str(p.get('why')).strip()[:200]}\")"
                                           if str(p.get("why") or "").strip() else "")
                + "; a board waiting on a person is his to settle"), p.get("name"))
    for cid, refs in sorted((refusals_ if refusals_ is not None else refusals(stores)).items()):
        for x in refs:
            if x.get("key") and x.get("type"):
                # `refusal` is the refusal alone, in words: the wiring door
                # quotes it on a row it files for the owner, where "not
                # proposed again" would not be true
                said = (f"{x['board']} was refused for "
                        f"{name_of(cid, *(x.get('names') or [])[::-1])} in {x['store']} "
                        f"({x['by']} on {x['on']}" + (f": {x['why']}" if x["why"] else "") + ")")
                hold(cid, "refused", x["type"], x["ref"],
                     f"{said}, and a board somebody said no to is not proposed again",
                     *(x.get("names") or []), refusal=said, whose=list(x.get("whose") or []),
                     page=x.get("page"), page_ref=x.get("page_ref"))
    for number, ids in sorted(numbers_on_file(companies or []).items()):
        known = slugs.get(number) or []
        for cid in ids:
            names = strict_names(*their_names(by_id.get(cid)))
            if known:
                # A RECORD ON A NUMBER WHOSE SLUG IS KNOWN HOLDS THE SLUG (blind
                # review, 2026-11-21). It was skipped as "the slug's holder,
                # under its own key", and nothing put it there for a pair said
                # on file: Archi Co on workable/710855, a note saying that
                # account opens under apply.workable.com/archico, and a
                # duplicate record whose site links workable/archico was wired
                # to the same account - where without the note it was held.
                # (The wiring door also counts such a record among a board's
                # holders, and says it in those words.) Under EVERY slug the
                # number is known by, the file's and a run's (review 10).
                for slug in known:
                    keys[board_key("workable", slug)].append({
                        "id": cid, "code": "number", "board": f"workable/{number}",
                        "names": names, "words": (
                            f"a record on file holds Workable account {number} "
                            f"({name_of(cid)}), whose postings open under "
                            f"apply.workable.com/{slug}: workable/{slug} is that record's "
                            f"board")})
                continue
            if names:
                named.append({"id": cid, "code": "number", "number": number, "names": names,
                              "board": f"workable/{number}", "words": (
                                  f"a record holds Workable account {number} "
                                  f"({name_of(cid)}), and whether it is this board was not "
                                  f"asked")})
    return {"keys": keys, "named": named, "pairs": pairs, "slugs": slugs,
            "paylocity": paylocity}


def named_holds(held: dict, kind, names, cid, ours=()) -> list:
    """(code, words) for each hold a Workable board going by `names` meets by
    name (held_boards' `named`), other than `cid`'s own - which hold it by
    company. `names` are the names the board itself states; `ours` the names
    the company it was found for goes by (their_names: its name and every
    also_known_as), compared the same way. Only ever more holding: a name is
    compared with the names the held account goes by, strictly.

    THE COMPANY'S OWN NAMES TOO (blind review, 2026-11-21). The wiring door
    asked only the board's stated names, while discovery and wire_embedded.py
    ask the company's: a doubt kept under Workable account 710855 about
    "Archi", Archi merged into "Archistar" (also_known_as Archi), and the
    survivor's own board, which states "Archistar", was held by the other two
    doors and wired by this one."""
    if kind != "workable":
        return []
    stated = strict_names(*(names or []))
    theirs = strict_names(*(ours or []))
    out = []
    for h in (held or {}).get("named") or []:
        if h["id"] == cid:
            continue
        hit = sorted(set(stated) & set(h["names"]))
        how = f"this board states \"{stated[hit[0]]}\"" if hit else ""
        if not hit:
            hit = sorted(set(theirs) & set(h["names"]))
            how = f"the company it was found for goes by \"{theirs[hit[0]]}\"" if hit else ""
        if hit:
            # a refusal in its own words: the row this goes on is filed for
            # the owner, so it is not "not proposed again" (third blind review,
            # 2026-11-21)
            said = (h.get("refusal") or h["words"]) if h.get("code") == "refused" else h["words"]
            out.append((h["code"], (
                f"{said}; it is held under Workable account {h['number']}, which was not "
                f"followed to a slug, and {how}, a name that account goes by "
                f"({h['names'][hit[0]]}) - whether it is the same Workable account was not "
                f"asked")))
    return out


def found_keys(kind, ref, pairs: dict | None = None) -> list:
    """Every key a found board is known by: its own, and for a Workable board
    found by its account number, its slug's where the two are known to belong
    together (`pairs`, as held_boards gives them). (A board found by its slug
    needs no other key: a hold under the number is held under the slug too.)"""
    keys = {board_key(kind, ref), *also_keys(kind, ref)}
    if numbered(kind, ref) and (pairs or {}).get(workable_number(ref)):
        keys.add(board_key("workable", pairs[workable_number(ref)]))
    return sorted(keys)


def held_boards_for_the_owner(data=None, companies: list | None = None) -> dict:
    """held_boards, read for a door that keeps no stores of its own
    (discover_ats.py, wire_embedded.py): the queue, the rulings, Agent
    proposals and the readers' doubts read STRICTLY from `data` (the
    directory the caller writes), its standing doubts, and `companies` as the
    caller read them. Raises Unreadable for a store that is on disk and does
    not read - the caller stops before its first probe."""
    stores = {"found": strict(FOUND.name, data), "ruled": strict(RULED.name, data),
              "proposals": strict(PROPOSALS, data)}
    standing = standing_doubts(read_doubts(data), stores["ruled"])
    by_id = {c.get("id"): c for c in companies or [] if isinstance(c, dict)}
    return held_boards(standing, stores, by_id, None, companies)


def held_board(held: dict, kind, ref, cid=None, names=(), refs=None) -> tuple | None:
    """(code, words) of the first hold the owner has on this board, or None:
    under any company's id but `cid`'s own (held by company already), under
    any key it is known by (found_keys), for a Paylocity board by every
    address on both sides (`refs`, else its ref: paylocity_relation, held on
    anything but "apart", as the wiring door holds it), and - for a Workable
    board - by a name it goes by (`names`) that a held account nobody
    followed goes by too (named_holds).

    THE PAYLOCITY HALF (seventh blind review, 2026-11-22). Keys alone never
    meet a hold at a bare /List/<n> beside the same board's /All/<uuid>/<Tenant>,
    nor a bare /All/<uuid> beside /List/<n>/<Tenant>: the wiring door held such
    a pair as "cannot be shown", and wire_embedded --write wired it."""
    for k in found_keys(kind, ref, (held or {}).get("pairs")):
        for h in (held or {}).get("keys", {}).get(k) or []:
            if h["id"] != cid:
                return h["code"], h["words"]
    if kind == "paylocity":
        mine = refs or board_refs(kind, ref)
        for h in (held or {}).get("paylocity") or []:
            if h["id"] == cid:
                continue
            rl = paylocity_relation(mine, h.get("refs") or [h.get("ref")])
            if rl != "apart":
                return h["code"], (h["words"] if rl == "same" else
                                   f"{h['words']}; and whether {show(kind, ref)} is that board "
                                   f"cannot be told from the two addresses ({rl})")
    hits = named_holds(held, kind, names, cid)
    return hits[0] if hits else None


def when(stamp):
    """The moment an ISO stamp names, with a timezone, or None."""
    import datetime as dt
    try:
        t = dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


def stale_read(stamp, now=None) -> str | None:
    """Why a live read stamped `stamp` is too old to act on, in words, or None.

    Undated is stale: a read nobody can date is not one anybody can call
    live. So is one dated more than an hour ahead - a clock that wrong says
    nothing about how old the read is.
    """
    import datetime as dt
    read = when(stamp)
    if read is None:
        return "undated"
    age = ((now or dt.datetime.now().astimezone()) - read).total_seconds() / 3600
    if age < -1:
        return "dated in the future"
    return f"{age:.0f} hours old" if age > MAX_AGE_HOURS else None


def q_board_proposals(companies, board) -> list:
    """Boards found inside a careers page, awaiting a yes or no.

    Ordered by how much is riding on the answer: a board with 100 postings
    puts 100 rows in front of visitors the moment it is accepted, and a wrong
    one puts 100 of somebody else's.
    """
    found = _read(FOUND, [])
    ruled = _read(RULED, {})
    by_id = {c["id"]: c for c in companies}
    out = []
    for f in found:
        if f["id"] in ruled:
            continue
        c = by_id.get(f["id"])
        if not c:
            continue
        # a board that already produces postings does not need this
        if (c.get("ats") or {}).get("type") not in ("html", "unknown"):
            continue
        n = f.get("postings") or 0
        out.append({
            "id": f["id"], "name": f["name"],
            "description": c.get("description"),
            "website": c.get("website"),
            "sector": c.get("sector"), "category": c.get("category"),
            "found": f["found"],
            "saw": f.get("saw"),
            "times": f.get("times", 1),
            "board_calls_itself": f.get("board_calls_itself"),
            "identity": f.get("identity", "unknown"),
            "identity_why": f.get("identity_why", ""),
            "postings": n,
            "verified": f.get("verified"),
            "titles": f.get("titles") or [],
            # who filed the row and the page that pointed at the board, when
            # a door held it for a person (file_found); the page then prints
            # identity_why, which on those rows is the doubt in words
            "filed": f.get("filed"),
            # and a row that was given a reader's doubt since (add_doubt),
            # whoever left it: the page prints its identity_why too
            "updated": f.get("updated"),
            # the two things that should make somebody slow down
            "warn_parent": f.get("identity") == "MISMATCH",
            "warn_scale": n >= 150,
        })
    out.sort(key=lambda r: (r["warn_parent"], -r["postings"]))
    return out


def _row_problem(r) -> str | None:
    """Why this row cannot be put in front of a person, or None.

    The queue draws what the row carries and nothing else, so a row that
    arrives without its evidence draws a blank where the evidence goes - and a
    blank looks exactly like weak evidence. Refused here, by name.
    """
    if not isinstance(r, dict):
        return "not a row"
    if not isinstance(r.get("id"), str) or not r["id"].strip():
        return "no company id"
    found = r.get("found")
    if not isinstance(found, dict):
        return "no found {type, ref}"
    import ats
    kind, ref = found.get("type"), found.get("ref")
    if kind not in ats.FETCHERS or kind == "html":
        return (f"{kind!r} is not a board the crawl can read (html is another "
                f"page we cannot enumerate, not a board found)")
    if paylocity_posting(kind, ref):
        return ("a Paylocity posting is one job, not a board: the wiring door asks its page "
                "which board it names, and files that board")
    ref_ok = (isinstance(ref, str) and ref.strip()) or (
        isinstance(ref, list) and ref
        and all(isinstance(x, str) and x.strip() for x in ref))
    if not ref_ok:
        return "the found board has no ref"
    if r.get("identity") not in HELD_AS:
        return (f"identity must be one of {', '.join(HELD_AS)} - a row is filed "
                f"because something is in doubt, got {r.get('identity')!r}")
    if not isinstance(r.get("identity_why"), str) or not r["identity_why"].strip():
        return "no identity_why: a held board must say its doubt in words"
    name = r.get("board_calls_itself")
    if name is not None and not isinstance(name, str):
        return "board_calls_itself must be the name the board states, or null"
    if r["identity"] == "MISMATCH" and not (name or "").strip():
        return "a MISMATCH must quote the name the board states"
    if not isinstance(r.get("verified"), str) or not r["verified"].strip():
        return ("no `verified`: only a board that was read live is filed - one "
                "that would not read is not a wrong board, and not a found one")
    n = r.get("postings")
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return "postings must be the count the live read returned"
    titles = r.get("titles")
    if not isinstance(titles, list) or not all(isinstance(t, str) for t in titles):
        return "titles must be a list of the titles read (it may be empty)"
    times = r.get("times")
    if not isinstance(times, int) or isinstance(times, bool) or times < 1:
        return "times must say how often their page named the board"
    if not isinstance(r.get("saw"), str):
        return "saw must be the text their page named the board with"
    if when(r.get("read_at")) is None:
        return ("no read_at: a row says when its board was read live, so an old "
                "read cannot be filed as today's")
    return None


def file_found(rows: list, by: str, write: bool = True, force: bool = False,
               why: str = "", companies: list | None = None) -> dict:
    """Append boards a door could not prove to "Boards we found".

    Returns {"added": [ids], "skipped": [{"id", "why"}], "error": str | None}.
    With write=False nothing is written and `added` is what a write would add.

    WHY THIS EXISTS. The owner's ruling of 2026-11-18: a found board is wired
    only when the company's own site points to it AND the board itself names
    the company; a parent's board, a name mismatch and anything doubtful go to
    his "Boards we found" queue. That queue reads this one file, and until now
    its only writer was find_embedded_ats.py, which REPLACES the whole file
    with one run's hits - so there was no way to hand him a single doubt
    without throwing away the rows already waiting on him.

    WHAT IT WILL NOT DO, each for a reason this file has already met:
      - replace the list. It reads what is on disk and writes that plus the
        new rows, through admin.save_decisions, so the append is journalled
        with the author and admin_undo.py can take it back.
      - add an id that is already in the list. The journal keys this file by
        id; two rows under one id are one record to it, and an undo would
        restore whichever won.
      - add an id that already has a ruling. The queue hides a ruled id, so
        the row would be written and never shown - and a refusal is kept so
        the next sweep stops proposing what somebody already said no to.
      - offer a board to a company that already reads from one.
      - file a row whose live read is more than MAX_AGE_HOURS old, or
        undated: the count and the titles on it are as old as the read.
      - file for a person. The author is agent:<label>. The owner does not
        file boards for himself to rule on, and a row stamped 'owner' would
        say he had - on the row and in the journal.
      - force. Over the journal's 25-record limit the caller shows the count
        first and passes force=True itself.

    A row is {id, found {type, ref}, saw, times, verified, board_calls_itself,
    identity ("unknown" or "MISMATCH"), identity_why, postings, titles,
    read_at} and may carry `page`, the page that pointed to the board - on
    the company's own site unless the doubt on the row says it is not.
    read_at is the ISO time of the live read the count and the titles came
    from. A Paylocity row may carry `page_ref`, the /All/ address the board's
    own page states (kept next to `found`, which stays the board as the site
    spelled it): every door after meets the board under it too, and the
    wiring door reads it back for a record wired from the row - so the wiring
    door files none (its worklist would be all that says it: wire_found.
    queue_rows, review 10's repair, 2026-11-23). identity_why
    is the doubt in words: on a MISMATCH the desk prints
    it in red, on every other filed row it prints it as the reason the board
    was not wired. `name` and `was` are taken from the company record, not
    from the caller, and `filed` records who filed it, when, and when the
    board was read.
    """
    import datetime as dt

    import admin
    import journal
    import privacy

    out = {"added": [], "skipped": [], "error": None}
    by = (by or "").strip()
    if not by.startswith("agent:") or len(by) <= len("agent:"):
        out["error"] = ("a filing names the agent that made it, as agent:<label>. "
                        "A person does not file here: he rules, in the admin")
        return out
    if "@" in by:
        out["error"] = "the author is a handle, never an address (the repo is public)"
        return out

    # THE FILE THE JOURNAL WILL REPLACE IS THE FILE THIS READS. Both paths
    # are fixed at import and a sandbox has to move both; a test that moved
    # one once wrote a fixture ruling into the owner's real file (2026-10-24).
    # If they ever disagree the append would graft one directory's rows onto
    # the other's file, so it stops instead.
    path = admin.DATA / FOUND.name
    if path.resolve() != FOUND.resolve():
        out["error"] = (f"board_proposals reads {FOUND} and admin would write "
                        f"{path}: two data directories. Nothing was written")
        return out
    current = []
    if path.exists():
        try:
            current = json.loads(path.read_text())
        except ValueError:
            current = None
        if not isinstance(current, list):
            # an unreadable queue is not an empty one: appending to "nothing"
            # would write the new rows OVER the rows waiting on the owner
            out["error"] = (f"{FOUND.name} is on disk and does not read as a "
                            f"list. Nothing was written")
            return out
    ruled = admin.read(RULED.name, {})
    if companies is None:
        companies = admin.read("companies.json", [])
    by_id = {c.get("id"): c for c in companies if isinstance(c, dict)}
    listed = {r.get("id") for r in current if isinstance(r, dict)}

    today, stamp = dt.date.today().isoformat(), admin.now()
    new = []
    for r in rows or []:
        cid = r.get("id") if isinstance(r, dict) else None
        problem = _row_problem(r)
        if problem:
            out["skipped"].append({"id": cid, "why": problem})
            continue
        old = stale_read(r["read_at"])
        if old:
            out["skipped"].append({"id": cid, "why": (
                f"the live read behind this row is {old}; a board is read again "
                f"before it is put in front of a person - plan again")})
            continue
        if cid in listed:
            out["skipped"].append({"id": cid, "why": "already in the list"})
            continue
        if cid in ruled:
            v = ruled[cid] if isinstance(ruled[cid], dict) else {}
            said = "accepted" if v.get("accept") else "refused"
            out["skipped"].append({"id": cid, "why": (
                f"already ruled: a board was {said} by {v.get('by') or '?'} on "
                f"{v.get('on') or '?'}, and the queue never shows a ruled company again")})
            continue
        c = by_id.get(cid)
        if c is None:
            out["skipped"].append({"id": cid, "why": "no such company on file"})
            continue
        kind = (c.get("ats") or {}).get("type")
        if kind not in WAITING:
            out["skipped"].append({"id": cid, "why": (
                f"{c.get('name')} already reads from {kind}; a board is offered "
                f"only to a company that has none")})
            continue
        filed = {"by": by, "on": today, "at": stamp, "read_at": r["read_at"]}
        # an address, or nothing: a page is something a person will open
        page = r.get("page").strip() if isinstance(r.get("page"), str) else ""
        if page.lower().startswith(("http://", "https://")):
            filed["page"] = page
        stated = (paylocity_address(r.get("page_ref"))
                  if r["found"]["type"] == "paylocity" else None)
        new.append(privacy.redact_deep({
            "id": cid, "name": c.get("name"), "was": kind,
            "found": {"type": r["found"]["type"], "ref": r["found"]["ref"]},
            **({"page_ref": stated} if stated else {}),
            "saw": r["saw"][:160], "times": r["times"],
            "verified": r["verified"].strip(),
            "board_calls_itself": (r.get("board_calls_itself") or "").strip() or None,
            "identity": r["identity"],
            "identity_why": r["identity_why"].strip(),
            "postings": r["postings"], "titles": r["titles"][:8],
            "filed": filed}))
        listed.add(cid)
    out["added"] = [r["id"] for r in new]
    if not new:
        return out

    after = current + new
    why = why or (f"{len(new)} found board(s) that could not be proven the "
                  f"company's own, filed for a person to rule")
    # asked before the write, so a look says what the write would be told
    _changes, refusal = journal.check(FOUND.name, current, after, force)
    if not refusal and write:
        refusal = admin.save_decisions(FOUND.name, after, FILE_ACTION,
                                       why=why, by=by, force=force)
    if refusal:
        out["error"] = refusal
        if write:
            out["added"] = []
    return out


def doubt_line(d: dict, row: dict | None = None) -> str:
    """A reader's doubt as it goes onto a row in this queue. The caller that
    knows boards (wire_found) passes its own `line`, which names the board the
    doubt is about and says when it is not the board on the card."""
    return (f"a reader's doubt" + (f" about {d['board']}" if d.get("board") else "")
            + f", kept {d.get('on') or '?'} by {d.get('by') or '?'}: "
            + str(d.get("why") or "").strip())


def runaway_share(total: int) -> int:
    """The most existing records one journal entry may rewrite in a file of
    `total` records (journal.RUNAWAY, from journal.RUNAWAY_FLOOR records up):
    a write over that share is refused, force or not."""
    import journal
    if total < journal.RUNAWAY_FLOOR:
        return max(total, 1)
    k = int(journal.RUNAWAY * total)
    while k > 1 and k / total > journal.RUNAWAY:
        k -= 1
    return max(k, 1)


def add_doubt(doubts: dict, by: str, write: bool = True, force: bool = False,
              why: str = "", line=None) -> dict:
    """Put readers' doubts on the rows already waiting for the owner here.

    `doubts` is {company id: [doubt records]} as read_doubts gives them;
    `line(record, row)` is how a doubt reads on a row (doubt_line by default).
    Returns {"added": {id: n}, "stamped": [ids], "already": [ids],
    "skipped": [{"id", "why"}], "error": str | None, "entries": n,
    "per_entry": n}; with write=False nothing is written and `added` is what a
    write would add.

    WHY THIS EXISTS. The queue holds one row per company. When a reader
    doubted a company that already had a row waiting - an older finder's, or
    an earlier batch's filed on another doubt - the door's held row was "already
    in the list", and the reader's words went nowhere the owner looks: his
    card showed the older row's words (second review, 2026-11-19). Here each
    doubt whose words the row does not already have is added to the row's
    identity_why, naming the board it is about, and the row is stamped
    `updated`: the card prints identity_why for a row carrying `filed` or
    `updated`. A row that already has the words and neither stamp is stamped,
    so the card prints them. Any ruling of his on the company from then on
    settles the doubt (standing_doubts) - as one did before the words were
    there; the words are so that he sees what he is ruling on.

    IN ENTRIES THE JOURNAL TAKES. Putting words on a row REWRITES it, and the
    journal refuses one entry that rewrites more than a third of a file of 60
    rows or more, force or not: a batch of doubts about 62 of 180 waiting
    companies stopped every plan for good (review of 27eeb628, 2026-11-19). So the
    rows go in entries of at most runaway_share(rows on file) each.

    WHAT IT WILL NOT DO: touch a company the owner has ruled on (the queue
    never shows a ruled company again, so a doubt about one cannot reach him
    here - the caller says so); add a row; write for a person (agent:<label>
    only); replace the file (journalled writes of the file plus the words,
    through admin.save_decisions); force past the journal's 25-record rule
    unless the caller has printed the count and passes force=True.
    """
    import datetime as dt

    import admin
    import journal
    import privacy

    out = {"added": {}, "stamped": [], "already": [], "skipped": [], "error": None,
           "entries": 0, "per_entry": 0}
    by = (by or "").strip()
    if not by.startswith("agent:") or len(by) <= len("agent:") or "@" in by:
        out["error"] = ("a reader's doubt is added by the agent that carries it, as "
                        "agent:<label> - a handle, never an address")
        return out
    path = admin.DATA / FOUND.name
    if path.resolve() != FOUND.resolve():
        out["error"] = (f"board_proposals reads {FOUND} and admin would write {path}: two "
                        f"data directories. Nothing was written")
        return out
    try:
        current = strict(FOUND.name, admin.DATA)
        ruled = strict(RULED.name, admin.DATA)
    except Unreadable as exc:
        out["error"] = f"{exc}. Nothing was written"
        return out
    line = line or doubt_line
    after = json.loads(json.dumps(current))
    rows = rows_by_id(after)
    today, stamp = dt.date.today().isoformat(), admin.now()
    changed = []
    for cid, recs in sorted((doubts or {}).items()):
        mine = rows.get(cid) or []
        if not mine:
            out["skipped"].append({"id": cid, "why": "no row for it is in the list"})
            continue
        if cid in ruled:
            v = ruled[cid]
            out["skipped"].append({"id": cid, "why": (
                f"already ruled: a board was {'accepted' if v.get('accept') else 'refused'} "
                f"by {v.get('by') or '?'} on {v.get('on') or '?'}, and the queue never shows "
                f"a ruled company again")})
            continue
        n, stamped = 0, False
        for r in mine:
            words = str(r.get("identity_why") or "").strip()
            more = [d for d in recs or [] if str(d.get("why") or "").strip()
                    and str(d["why"]).strip() not in words]
            if more:
                said = "; ".join(line(d, r) for d in more)
                r["identity_why"] = privacy.redact(f"{words}; {said}" if words else said)
                n += len(more)
            elif r.get("filed") or r.get("updated"):
                continue                # the card already prints the words it has
            else:
                stamped = True
            r["updated"] = {"by": by, "on": today, "at": stamp,
                            "why": "a reader's doubt put on the row"}
        if n:
            out["added"][cid] = n
        elif stamped:
            out["stamped"].append(cid)
        else:
            out["already"].append(cid)
        if n or stamped:
            changed.append(cid)
    if not changed:
        return out
    why = why or (f"{sum(out['added'].values())} reader's doubt(s) put on the rows already "
                  f"waiting for the owner, so his card shows them")
    # one entry per share of the file the journal lets one entry rewrite; each
    # entry is judged against the file as the one before it left it
    total = len(journal.snapshot(current, FOUND.name))
    size = runaway_share(total)
    parts = [changed[i:i + size] for i in range(0, len(changed), size)]
    out["per_entry"], state = size, current
    for i, part in enumerate(parts, 1):
        mine = set(part)
        after_i = [new if (isinstance(new, dict) and new.get("id") in mine) else old
                   for old, new in zip(state, after)]
        said = why if len(parts) == 1 else f"{why} (entry {i} of {len(parts)})"
        _changes, refusal = journal.check(FOUND.name, state, after_i, force)
        if not refusal and write:
            refusal = admin.save_decisions(FOUND.name, after_i, DOUBT_ADDED, why=said, by=by,
                                           force=force)
        if refusal:
            out["error"] = refusal
            if write:
                done = {c for p in parts[:i - 1] for c in p}
                out["added"] = {c: n for c, n in out["added"].items() if c in done}
                out["stamped"] = [c for c in out["stamped"] if c in done]
            return out
        out["entries"] += 1
        state = after_i
    return out


def rule(company_id: str, accept: bool, why: str = "", by: str = "owner") -> dict:
    """Record a decision. Accepting does NOT write the ats - the admin does
    that through its own validated path, so this file only ever holds an
    opinion."""
    import datetime as dt
    ruled = _read(RULED, {})
    ruled[company_id] = {"accept": bool(accept), "why": why.strip(),
                         "by": by, "on": dt.date.today().isoformat()}
    RULED.write_text(json.dumps(ruled, indent=1, sort_keys=True) + "\n")
    return ruled[company_id]


def main() -> int:
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    rows = q_board_proposals(companies, board)
    if not rows:
        # NOT "run find_embedded_ats.py": run bare, that script REPLACES this
        # queue's file with one run's hits, and a queue with nothing waiting
        # is not a file with nothing in it. The rows already ruled on are
        # still there, and a refusal's board is read from its row - the
        # ruling itself records no board (wire_found._refusals).
        print("no board proposals waiting. scripts/wire_found.py files the boards "
              "it cannot prove here; scripts/find_embedded_ats.py needs its own "
              "--out file.")
        return 0
    parent = [r for r in rows if r["warn_parent"]]
    scale = [r for r in rows if r["warn_scale"] and not r["warn_parent"]]
    clean = [r for r in rows if not r["warn_parent"] and not r["warn_scale"]]
    print(f"{len(rows)} boards found inside a careers page\n")
    if parent:
        print(f"REFUSE unless you know better ({len(parent)}) - "
              f"the board says it belongs to somebody else:")
        for r in parent:
            print(f"  {r['name'][:26]:28} board says \"{r['board_calls_itself']}\" "
                  f"· {r['postings']} postings")
    if scale:
        print(f"\nLOOK FIRST ({len(scale)}) - more postings than this company "
              f"plausibly has:")
        for r in scale:
            print(f"  {r['name'][:26]:28} {r['postings']} postings via "
                  f"{r['found']['type']}")
    print(f"\nREADY TO RULE ({len(clean)}):")
    for r in clean[:30]:
        who = f' — board says "{r["board_calls_itself"]}"' if r.get("board_calls_itself") else ""
        print(f"  {r['name'][:26]:28} {r['found']['type']:12} "
              f"{r['postings']:>4} postings{who}")
    if len(clean) > 30:
        print(f"  ... and {len(clean) - 30} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
