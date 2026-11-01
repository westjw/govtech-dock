"""The desk's five front doors, and the one core they share (owner, 2026-11-01).

"build all 5 and just have a toggle where I can pick which one to use. make
sure all of them are wired together." The five are the round-2 admin canvas:
Waiting on you, Morning brief, Batches, Health map, Ask the desk. They are
five ways of LOOKING at the same work, so none of them owns any of it: every
number comes from here, every write goes through the action the queue's own
button already uses, and a ruling made in one view is the same ruling in the
other four.

The finding they all answer, measured on 2026-11-01: the shift was never run,
the owner's rulings come in bursts (mostly in chat with Claude), and about
9,000 rows wait in 24 queues for one person.

THE BATCH ENGINE is the new part, and it is the part that can go wrong in a
way nothing else here can: one press rules a whole pile. So it keeps four
promises, each one a guard in selftest.

1. A pile is ONE machine answer over rows that already carry it. Nothing here
   proposes anything: a pile exists only where an agent, a door or a guesser
   has stored an answer on the row, and only where the owner's own rules allow
   ruling it fast. Everything else is listed with the reason it is not a
   batch (NEVER, NEEDS_AGENT), because "not batchable" is a finding too.
2. The sample is sized by what it must prove, not by a percentage. Zero-defect
   acceptance sampling at 95%: read n cards, and if none is wrong the pile is
   under the tolerance. A pile that publishes something takes 5% (about 58
   cards however big it is), a pile that only writes a record takes 10%
   (about 29), and a pile of 20 or fewer is read whole. The first wrong card
   ends it: the pile goes back. (A flat 5% proved nothing on small piles: 4
   clean cards out of 68 still allowed half of them to be wrong.)
3. Accept runs every row through the SAME applier its own button runs, with
   the saves held in memory and written once per file: one journal entry per
   file (chunked under RUNAWAY), force only because the count was shown
   (expect_n must match), and every record it wrote says `via: batch:<id>`.
   A batch is ONE ruling to every counter, and the agree-rate never reads a
   batch-ruled record (check_admin_game): the sample marks are the judgments.
4. Undo takes back exactly what the batch wrote, field by field: a field the
   nightly refresh or a later ruling has changed since is named and nothing
   is written. Records the batch itself added may be removed again without
   tripping RUNAWAY (journal.UNDOING_ADDS), which would otherwise refuse
   deleting them.
"""
from __future__ import annotations

import collections
import contextlib
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import subprocess
import time

import admin

# admin.DATA is read at every use, never copied here: the selftest sandbox
# moves it, and a copy taken at import would read and write the real data/.
AUDITS = "batch_audits.json"
# THE MARKS ARE NOT JOURNALLED, and that is the one write here that is not.
# A mark is a reading of one sample card, not a ruling on any record, and a
# pile takes up to 58 of them: journalled, five piles' marks would push 290
# real rulings out of the 500-entry live journal, where Undo can no longer
# reach them. The ruling that rests on the marks copies them into AUDITS,
# which IS journalled, so the evidence travels with the ruling. Gitignored,
# like admin_shifts.jsonl: the desk's own reading, not the map.
MARKS = "batch_marks.json"
QUESTIONS = "owner_questions.json"

CONFIDENCE = 0.95
TOLERANCE = {"public": 0.05, "record": 0.10}
READ_ALL_AT = 20


# ------------------------------------------------------------------ sampling

def sample_size(n: int, reach: str) -> int:
    """How many cards a person reads so that none wrong proves the pile is
    under its tolerance, at 95% confidence. Hypergeometric, so a pile of 66
    needs 34 and not 58; N <= READ_ALL_AT is read whole."""
    if n <= READ_ALL_AT:
        return n
    tol = TOLERANCE.get(reach, TOLERANCE["public"])
    bad = max(1, math.ceil(tol * n))
    p_clean = 1.0
    for k in range(1, n + 1):
        # chance that k cards drawn at random from n contain none of `bad`
        if n - bad - (k - 1) <= 0:
            return k
        p_clean *= (n - bad - (k - 1)) / (n - (k - 1))
        if p_clean <= 1 - CONFIDENCE:
            return k
    return n


def _rank(bid: str, key: str) -> str:
    """A row's place in the draw. Hashing the batch with the key gives an
    order nobody chose that does not move when other rows come and go, so a
    card read yesterday is still in the sample today unless its row left."""
    return hashlib.sha256(f"{bid}|{key}".encode()).hexdigest()


def draw(bid: str, keys: list[str], n: int) -> list[str]:
    return sorted(keys, key=lambda k: _rank(bid, k))[:n]


# --------------------------------------------------------------------- piles

WHY_BATCH = "ruled as a batch after reading a sample"


def _pr_accept(r):
    return {"key": r["key"], "accept": True}


def _pr_reject(r):
    return {"key": r["key"], "accept": False}


def _apply_review(body: dict) -> dict:
    """Website review's Yes is two requests on the page (patch, then
    dismiss 'confirmed'); one already applied is only dismissed."""
    cos = admin.read_companies()
    c = next((x for x in cos if x["id"] == body["id"]), None)
    if c is None:
        return {"error": "no such company"}
    if (c.get("website") or "").rstrip("/") != body["url"].rstrip("/"):
        res = admin.act_patch({"id": body["id"], "fields": {"website": body["url"]},
                               "by": body.get("by"), "why": body.get("why")})
        if res.get("error"):
            return res
    return admin.act_dismiss({"queue": "review", "key": body["id"],
                              "why": "confirmed", "by": body.get("by")})


def _apply_placement(body: dict) -> dict:
    return admin.act_patch({"id": body["id"], "fields": {
        "sector": body["sector"], "category": body["category"]},
        "by": body.get("by"), "why": body.get("why")})


def _supplier_body(verdict):
    return lambda r: {"id": r["id"], "verdict": verdict,
                      "saw_url": r.get("url"), "says": r.get("says")}


def _place_body(r):
    return {"id": r["id"], "sector": r["proposed_sector"],
            "category": r["proposed_category"],
            "was": f"{r.get('sector')} / {r.get('category')}",
            "proposed": f"{r['proposed_sector']} / {r['proposed_category']}",
            "confidence": r.get("confidence"), "description": r.get("description")}


def _founded_rows(rows):
    """Researched years only, high confidence, with the quote beside them."""
    try:
        res = {x["id"]: x for x in json.loads((admin.DATA / "proposed_founded.json").read_text())
               if isinstance(x, dict) and x.get("id")}
    except (OSError, json.JSONDecodeError):
        res = {}
    out = []
    for r in rows:
        x = res.get(r["id"])
        if (r.get("kind") == "unconfirmed" and r.get("proposed_by") == "agent:founded-research"
                and x and x.get("confidence") == "high" and x.get("year") == r.get("year")
                and x.get("quote")):
            out.append({**r, "quote": x["quote"], "source": x.get("source_url"),
                        "research_why": x.get("why")})
    return out


# Every pile: which queue, which rows, what the answer is, how far it reaches,
# the applier its own button uses, the body that button sends, what a person
# checks on a sample card, and how to tell afterwards that the row landed.
# `card` names the row fields a sample card shows.
PILES = [
    {"queue": "profiles", "pile": "accept", "reach": "public", "closes": "writeup",
     "label": "Put {n} write-ups on their company pages",
     "answer": "On the page",
     "select": lambda r: r.get("status") == "pending" and r.get("paragraphs")
                         and r.get("confidence") == "medium",
     "apply": "proposal-ruling", "body": _pr_accept,
     "check": "Read the write-up beside the company's website. Wrong if a "
              "sentence says more than the site does, or it describes someone else.",
     "landed": lambda st, r: (st["proposals"].get(r["key"]) or {}).get("status") == "accepted"},
    {"queue": "profiles", "pile": "dismiss-refused", "reach": "record",
     "label": "Dismiss {n} write-ups the door refused",
     "answer": "Dismissed: the door's refusal stands",
     "select": lambda r: r.get("status") == "refused",
     "apply": "dismiss", "body": lambda r: {"queue": "profiles", "key": r["key"]},
     "check": "Read the door's reason. Wrong if the refusal was the door's mistake.",
     "landed": lambda st, r: r["key"] in st["dismissed"].get("profiles", {})},
    {"queue": "profiles", "pile": "reject-unsure", "reach": "record",
     "label": "Reject {n} write-ups the agent declined to write",
     "answer": "Rejected: nothing to put on the page",
     "select": lambda r: r.get("status") == "pending" and r.get("confidence") == "unsure"
                         and not r.get("paragraphs"),
     "apply": "proposal-ruling", "body": _pr_reject,
     "check": "Open the company's site. Wrong if it says enough to write from.",
     "landed": lambda st, r: (st["proposals"].get(r["key"]) or {}).get("status") == "rejected"},
    {"queue": "suppliers", "pile": "theirs", "reach": "public",
     "label": "Rule {n} supplier websites theirs (step 1 said theirs)",
     "answer": "Their website",
     "select": lambda r: r.get("step1") == "theirs" and r.get("url"),
     "apply": "supplier-site", "body": _supplier_body("theirs"),
     "check": "Open the site. Wrong if it is not this supplier's own site, or "
              "it does not sell into the department it is filed under.",
     "landed": lambda st, r: (st["supplier_rulings"].get(r["id"]) or {}).get("verdict") == "theirs"},
    {"queue": "suppliers", "pile": "found", "reach": "public",
     "label": "Rule {n} supplier websites theirs (step 1 found one)",
     "answer": "Their website",
     "select": lambda r: r.get("step1") == "found" and r.get("url"),
     "apply": "supplier-site", "body": _supplier_body("theirs"),
     "check": "Open the site. Wrong if it is not this supplier's own site, or "
              "it does not sell into the department it is filed under.",
     "landed": lambda st, r: (st["supplier_rulings"].get(r["id"]) or {}).get("verdict") == "theirs"},
    {"queue": "suppliers", "pile": "found-review", "reach": "public",
     "label": "Rule {n} supplier websites theirs (step 1 found one, wanted a look)",
     "answer": "Their website",
     "select": lambda r: r.get("step1") == "found_review" and r.get("url"),
     "apply": "supplier-site", "body": _supplier_body("theirs"),
     "check": "Open the site. Wrong if it is not this supplier's own site.",
     "landed": lambda st, r: (st["supplier_rulings"].get(r["id"]) or {}).get("verdict") == "theirs"},
    {"queue": "suppliers", "pile": "unconfirmed", "reach": "public",
     "label": "Rule {n} supplier websites theirs (step 1 could not confirm)",
     "answer": "Their website",
     "select": lambda r: r.get("step1") == "unconfirmed" and r.get("url"),
     "apply": "supplier-site", "body": _supplier_body("theirs"),
     "check": "Open the site. Wrong if it is not this supplier's own site.",
     "landed": lambda st, r: (st["supplier_rulings"].get(r["id"]) or {}).get("verdict") == "theirs"},
    {"queue": "suppliers", "pile": "not-theirs", "reach": "record",
     "label": "Rule {n} supplier websites not theirs (a listing site or a parked domain)",
     "answer": "Not their website",
     "select": lambda r: r.get("step1") in ("listing_site", "parked"),
     "apply": "supplier-site", "body": _supplier_body("not_theirs"),
     "check": "Open the site. Wrong if it IS the supplier's own site.",
     "landed": lambda st, r: (st["supplier_rulings"].get(r["id"]) or {}).get("verdict") == "not_theirs"},
    {"queue": "proposals", "pile": "reads-empty", "reach": "record",
     "label": "Close {n} page reads that found nothing",
     "answer": "Recorded: the read found nothing",
     "select": lambda r: r.get("kind") == "read" and r.get("none_found") and not r.get("warn"),
     "apply": "proposal-ruling", "body": _pr_accept,
     "check": "Open the careers page. Wrong if it lists openings the read missed.",
     "landed": lambda st, r: (st["proposals"].get(r["key"]) or {}).get("status") == "accepted"},
    {"queue": "miscategorized", "pile": "high", "reach": "public",
     "label": "File {n} companies where the guesser said, high confidence",
     "answer": "Filed where the guesser said",
     "select": lambda r: r.get("confidence") == "high" and r.get("proposed_sector"),
     "apply": "place", "body": _place_body,
     "check": "Read the description. Wrong if a buyer would not look for it there.",
     "landed": lambda st, r: (st["companies"].get(r["id"]) or {}).get("category") == r["proposed_category"]},
    {"queue": "miscategorized", "pile": "medium", "reach": "public",
     "label": "File {n} companies where the guesser said, medium confidence",
     "answer": "Filed where the guesser said",
     "select": lambda r: r.get("confidence") == "medium" and r.get("proposed_sector"),
     "apply": "place", "body": _place_body,
     "check": "Read the description. Wrong if a buyer would not look for it there.",
     "landed": lambda st, r: (st["companies"].get(r["id"]) or {}).get("category") == r["proposed_category"]},
    {"queue": "founded", "pile": "researched", "reach": "public", "closes": "founded",
     "label": "Stand behind {n} founding years an agent read off the company's own site",
     "answer": "Stood behind, on a sample",
     "rows": _founded_rows,
     "select": lambda r: True,
     "apply": "confirm-founded", "body": lambda r: {"id": r["id"]},
     "check": "Open the source. Wrong if the quote is not on the page, or the "
              "year is not this company's founding.",
     "landed": lambda st, r: bool(((st["founded"].get(r["id"]) or {}).get("confirmed") or {}).get("by"))},
    {"queue": "review", "pile": "all", "reach": "public",
     "label": "Change {n} websites to the address the researcher found",
     "answer": "Website changed",
     "select": lambda r: bool(r.get("url")),
     "apply": "_review", "body": lambda r: {"id": r["id"], "url": r["url"]},
     "check": "Open the new address. Wrong if it is not this company's site.",
     "landed": lambda st, r: r["id"] in st["dismissed"].get("review", {})},
    {"queue": "placement", "pile": "all", "reach": "public",
     "label": "Move {n} companies to the department the guesser suggests",
     "answer": "Moved where the guesser said",
     "select": lambda r: bool((r.get("suggested") or {}).get("sector")),
     "apply": "_placement",
     "body": lambda r: {"id": r["id"], "sector": r["suggested"]["sector"],
                        "category": r["suggested"]["category"]},
     "check": "Read the description. Wrong if it belongs where it is.",
     "landed": lambda st, r: (st["companies"].get(r["id"]) or {}).get("category")
                             == r["suggested"]["category"]},
]

# Not batches, with the rule that says so. Read by the Batches view, the
# backlog and Ask the desk, so the three cannot disagree about why.
NEVER = {
    "boards": "A page scan never proves absence. A wrong 'nothing public' buries a warm door.",
    "scrub": "Every row is a page the machine could not read. 'No openings today' is a person's word.",
    "acquisitions": "Your rule: whether a board is a parent's needs slow reading. Never dealt, never batched.",
    "vendors": "No stored answer, and your SLED-only ruling: no title rule, read the roles.",
    "duplicates": "A merge is the ruling this map can least afford to get quietly wrong.",
    "boardfound": "No board on the row is a machine 'yes'; unknown is not a pass.",
    "submissions": "People first, one at a time.",
    "users": "People first, one at a time.",
    "blocked": "Nothing to rule: nobody could fetch the page. It is retried.",
    "signedoff": "Each row points into another queue; it clears when that fact turns green.",
    "calendar": "A date is published only off the organisation's own page, typed by a person.",
    "scope": "Empty for good since federal went out of scope.",
    "logos": "Guessed logos: you look at each one.",
    "websites": "Four rows, each a finding in prose, and a dismissal is never re-asked.",
}
NEEDS_AGENT = {
    "unclassified": "The family agent (agents.brief_family) has never run: 0 proposals on file.",
    "leads": "A reader that opens each careers page and proposes where they post.",
    "sweeps": "A reader that confirms a floor's address, and an action to accept it.",
}


ONE = {"write-ups": "write-up", "websites": "website", "companies": "company",
       "years": "year", "reads": "read"}


def _label(spec: dict, n: int) -> str:
    out = spec["label"].format(n=f"{n:,}")
    if n == 1:
        for many, one in ONE.items():
            out = re.sub(rf"\b{many}\b", one, out, count=1)
    return out


def _pile_id(queue: str, pile: str, sector: str | None) -> str:
    return f"{queue}:{pile}:{sector or 'all'}"


_SECTORS: dict = {}


def _sector_of(r: dict) -> str | None:
    """A row's department. Some queues' rows carry none (Founding year,
    Website review), so the company's own is looked up."""
    return (r.get("sector") or (r.get("current") or {}).get("sector")
            or _SECTORS.get(r.get("id")))


def _in_sector(r: dict, sector: str | None) -> bool:
    return not sector or _sector_of(r) == sector


def _load_sectors(companies) -> None:
    _SECTORS.clear()
    _SECTORS.update({c.get("id"): c.get("sector") for c in companies if isinstance(c, dict)})


def _rows_for(spec: dict, queue_rows: list, sector: str | None) -> list:
    rows = spec["rows"](queue_rows) if spec.get("rows") else queue_rows
    return [r for r in rows if _in_sector(r, sector) and spec["select"](r)]


def _row_key(r: dict) -> str:
    return str(r.get("key") or r.get("id"))


def _audits() -> dict:
    return admin.read(AUDITS, {})


def _marks() -> dict:
    return admin.read(MARKS, {})


def _queue_rows(queue: str, companies, board) -> list:
    rows = admin.QUEUES[queue](companies, board)
    return [r for r in rows if isinstance(r, dict)]


def _view(spec: dict, rows: list, sector: str | None, audits: dict) -> dict:
    bid = _pile_id(spec["queue"], spec["pile"], sector)
    keys = [_row_key(r) for r in rows]
    n = sample_size(len(keys), spec["reach"])
    sample = draw(bid, keys, n)
    marks = {k: m for k, m in (_marks().get(bid) or {}).items()
             if k in set(sample) and m.get("answer") == spec["answer"]}
    wrong = [k for k, m in marks.items() if m.get("mark") == "wrong"]
    return {"id": bid, "queue": spec["queue"], "pile": spec["pile"],
            "sector": sector or None, "label": _label(spec, len(keys)),
            "closes": spec.get("closes"),
            "answer": spec["answer"], "reach": spec["reach"],
            "tolerance": TOLERANCE[spec["reach"]], "n": len(keys),
            "sample_n": n, "read_all": len(keys) <= READ_ALL_AT,
            "read": len(marks), "wrong": len(wrong), "check": spec["check"],
            "state": ("empty" if not keys else
                      "sent back" if wrong and len(keys) > READ_ALL_AT else
                      "ready" if len(marks) >= n else
                      "reading" if marks else "unread"),
            "last": ((audits.get(bid) or {}).get("rulings") or [None])[-1]}


def batches(companies=None, board=None, sector: str | None = None) -> dict:
    """Every queue: its piles, what is left for a person, or why it is not a
    batch. One read of each queue, so the counts are the tabs' counts."""
    companies = companies if companies is not None else admin.read("companies.json", [])
    board = board if board is not None else admin.read("board.json", {})
    _load_sectors(companies)
    audits = _audits()
    out = []
    by_queue = collections.defaultdict(list)
    for spec in PILES:
        by_queue[spec["queue"]].append(spec)
    for q in admin.QUEUES:
        if q in by_queue:
            rows = [r for r in _queue_rows(q, companies, board) if _in_sector(r, sector)]
            piles, covered = [], set()
            for spec in by_queue[q]:
                prow = _rows_for(spec, rows, None)
                covered |= {_row_key(r) for r in prow}
                piles.append(_view(spec, prow, sector, audits))
            out.append({"queue": q, "label": admin.LABEL.get(q, q), "n": len(rows),
                        "piles": piles, "left": len(rows) - len(covered),
                        "kind": "batch"})
        else:
            n = None
            try:
                n = len([r for r in _queue_rows(q, companies, board) if _in_sector(r, sector)])
            except Exception:                       # a queue that cannot be read
                n = None                            # says so; it is not zero
            out.append({"queue": q, "label": admin.LABEL.get(q, q), "n": n,
                        "piles": [], "left": n,
                        "kind": "agent" if q in NEEDS_AGENT else "never",
                        "why": NEEDS_AGENT.get(q) or NEVER.get(q) or ""})
    for b in out:
        b["who"], b["note"] = WHO.get(b["queue"], ("you", ""))
    out.sort(key=lambda b: -(b["n"] or 0))
    return {"queues": out, "sector": sector or None,
            "rule": {"confidence": CONFIDENCE, "tolerance": TOLERANCE,
                     "read_all_at": READ_ALL_AT}}


def _spec(queue: str, pile: str) -> dict | None:
    return next((s for s in PILES if s["queue"] == queue and s["pile"] == pile), None)


def _parse_id(bid: str) -> tuple[str, str, str | None]:
    q, p, s = (bid.split(":", 2) + ["", "", ""])[:3]
    return q, p, (None if s in ("", "all") else s)


def _card(spec: dict, r: dict) -> dict:
    """What a sample card shows: the row as the queue serves it, plus the one
    answer the pile would write, so a person judges the answer and not the
    row. Long text is kept whole; the page decides what to fold."""
    return {"key": _row_key(r), "row": r, "answer": spec["answer"]}


def batch(bid: str, companies=None, board=None) -> dict:
    q, p, sector = _parse_id(bid)
    spec = _spec(q, p)
    if spec is None:
        return {"error": f"no batch {bid!r}"}
    companies = companies if companies is not None else admin.read("companies.json", [])
    board = board if board is not None else admin.read("board.json", {})
    _load_sectors(companies)
    rows = _rows_for(spec, _queue_rows(q, companies, board), sector)
    audits = _audits()
    v = _view(spec, rows, sector, audits)
    by_key = {_row_key(r): r for r in rows}
    sample = draw(v["id"], list(by_key), v["sample_n"])
    marks = _marks().get(v["id"]) or {}
    v["sample"] = [{**_card(spec, by_key[k]), "mark": (marks.get(k) or {}).get("mark")
                    if (marks.get(k) or {}).get("answer") == spec["answer"] else None,
                    "mark_why": (marks.get(k) or {}).get("why")} for k in sample]
    v["rulings"] = (audits.get(v["id"]) or {}).get("rulings") or []
    v["queue_label"] = admin.LABEL.get(q, q)
    return v


def act_batch_mark(body: dict) -> dict:
    """A person's read of one sample card: right or wrong, and why if wrong.
    A mark is not a ruling on the row - nothing about the company changes -
    it is the measurement the batch is ruled on, so it keeps what was on
    the card (the answer) and is journalled like every judgment here."""
    bid, key, mark = body.get("batch") or "", body.get("key") or "", body.get("mark")
    q, p, sector = _parse_id(bid)
    spec = _spec(q, p)
    if spec is None:
        return {"error": f"no batch {bid!r}"}
    if mark not in ("right", "wrong", None):
        return {"error": "a mark is right, wrong, or cleared"}
    why = (body.get("why") or "").strip()
    if mark == "wrong" and not why:
        return {"error": "say in one line what was wrong; it goes back with the batch"}
    v = batch(bid)
    if key not in {c["key"] for c in v.get("sample", [])}:
        return {"error": "that card is not in this batch's sample any more"}
    allm = _marks()
    mine = allm.setdefault(bid, {})
    if mark is None:
        mine.pop(key, None)
    else:
        mine[key] = {"mark": mark, "why": why or None, "answer": spec["answer"],
                     "on": dt.date.today().isoformat(), "at": admin.now(),
                     "by": (body.get("by") or "owner")}
    admin.write_atomic(MARKS, allm)
    return {"ok": True, "message": "marked" if mark else "cleared"}


# ------------------------------------------------- holding a batch's writes

APPLIERS = {
    "proposal-ruling": lambda b: admin.act_proposal_ruling(b),
    "dismiss": lambda b: admin.act_dismiss(b),
    "supplier-site": lambda b: admin.act_supplier_site(b),
    "place": lambda b: admin.act_place(b),
    "confirm-founded": lambda b: admin.act_confirm_founded(b),
    "_review": _apply_review,
    "_placement": _apply_placement,
}


@contextlib.contextmanager
def held_writes():
    """Run appliers with every save kept in memory, then hand back
    {file: {"before", "after"}}. The appliers are the desk's own, untouched;
    only where they read from and write to moves. Reads of a file the batch
    has already written see the batch's copy, so row two builds on row one -
    including agents.load(), which reads the proposals file straight off
    disk and would otherwise hand every row the file as it was before the
    batch and keep only the last row's change."""
    import agents
    files: dict = {}
    orig = {"read": admin.read, "read_companies": admin.read_companies,
            "save_companies": admin.save_companies, "save_decisions": admin.save_decisions,
            "write_atomic": admin.write_atomic, "load": agents.load}

    def _keep(name, after, default):
        if name not in files:
            files[name] = {"before": orig["read"](name, default), "after": after}
        else:
            files[name]["after"] = after

    def read(name, default):
        if name in files:
            return files[name]["after"]
        return orig["read"](name, default)

    def read_companies():
        if "companies.json" in files:
            return files["companies.json"]["after"]
        return orig["read"]("companies.json", [])

    def save_companies(companies, action, why="", by="owner", force=False, extra=None):
        _keep("companies.json", companies, [])
        return None

    def save_decisions(name, after, action, why="", by="owner", force=False, extra=None):
        _keep(name, after, [] if isinstance(after, list) else {})
        return None

    def write_atomic(name, payload):
        _keep(name, payload, [] if isinstance(payload, list) else {})

    def load():
        if "agent_proposals.json" in files:
            return files["agent_proposals.json"]["after"]
        return orig["load"]()

    admin.read, admin.read_companies = read, read_companies
    admin.save_companies, admin.save_decisions = save_companies, save_decisions
    admin.write_atomic, agents.load = write_atomic, load
    try:
        yield files
    finally:
        admin.read, admin.read_companies = orig["read"], orig["read_companies"]
        admin.save_companies, admin.save_decisions = orig["save_companies"], orig["save_decisions"]
        admin.write_atomic, agents.load = orig["write_atomic"], orig["load"]


def _stamp_via(files: dict, via: str) -> None:
    """Every ruling record the batch wrote says so. companies.json holds
    facts, not rulings, so it is left alone; the journal entry carries the
    batch for it."""
    import journal
    for name, f in files.items():
        if name == "companies.json":
            continue
        b = journal.snapshot(f["before"], name)
        a = journal.snapshot(f["after"], name)
        for k, rec in a.items():
            if b.get(k) == rec or not isinstance(rec, dict):
                continue
            if name == "admin_dismissed.json":
                # keyed by queue: the records are one level down
                old = b.get(k) or {}
                for kk, inner in rec.items():
                    if isinstance(inner, dict) and old.get(kk) != inner:
                        inner["via"] = via
            elif name == "founded_provenance.json" and isinstance(rec.get("confirmed"), dict):
                rec["confirmed"]["via"] = via
            else:
                rec["via"] = via


def _chunks(name: str, before, after) -> list:
    """The payloads to write in order, each under RUNAWAY. A rewrite of more
    than a third of a big file is refused even with force, so a pile that
    big lands in steps, each its own journal entry under one batch."""
    import journal
    b, a = journal.snapshot(before, name), journal.snapshot(after, name)
    changed = sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))
    total = max(len(b), 1)
    cap = len(changed) if total < journal.RUNAWAY_FLOOR else max(1, int(journal.RUNAWAY * total))
    rewrites = [k for k in changed if b.get(k) is not None]
    adds = [k for k in changed if b.get(k) is None]
    steps, cur = [], before
    groups = [rewrites[i:i + cap] for i in range(0, len(rewrites), cap)] or [[]]
    groups[0] = groups[0] + adds
    for i, g in enumerate(groups):
        if i == len(groups) - 1:
            payload = after
        else:
            restored = dict(journal.snapshot(cur, name))
            for k in g:
                if a.get(k) is None:
                    restored.pop(k, None)
                else:
                    restored[k] = a[k]
            payload = journal.as_payload(cur, restored, name)
        steps.append((cur, payload))
        cur = payload
    return steps


def act_batch_rule(body: dict) -> dict:
    """Rule a whole pile: accept it, or send it back.

    Accept refuses unless the sample is read with none wrong, and unless the
    pile is the size the person was shown (expect_n). A read-all pile
    accepts only the rows marked right; its wrong ones stay in the queue.
    Send back writes nothing but the record: the pile stays in its queue,
    the marked cards and the why go to whoever re-splits it."""
    import journal
    bid = body.get("batch") or ""
    ruling = body.get("ruling")
    by = (body.get("by") or "owner").strip()
    why = (body.get("why") or "").strip()
    q, p, sector = _parse_id(bid)
    spec = _spec(q, p)
    if spec is None:
        return {"error": f"no batch {bid!r}"}
    if ruling not in ("accept", "send_back"):
        return {"error": "a batch is accepted or sent back"}
    if not admin._is_person(by):
        return {"error": "a batch is a person's ruling; an agent cannot rule one"}
    companies = admin.read("companies.json", [])
    board = admin.read("board.json", {})
    _load_sectors(companies)
    rows = _rows_for(spec, _queue_rows(q, companies, board), sector)
    v = batch(bid, companies, board)
    try:
        expect = int(body.get("expect_n"))
    except (TypeError, ValueError):
        expect = -1
    if expect != v["n"]:
        return {"error": f"the batch changed since you looked: it held {expect}, it "
                         f"holds {v['n']} now. Look again; nothing was written.",
                "stale": True}
    marks = {c["key"]: c["mark"] for c in v["sample"]}
    unread = [k for k, m in marks.items() if not m]
    wrong = [k for k, m in marks.items() if m == "wrong"]
    audits = _audits()
    rec = audits.setdefault(bid, {"queue": q, "pile": p, "sector": sector,
                                  "rulings": []})
    rid = f"{bid}#{len(rec['rulings']) + 1}"
    stamp = {"id": rid, "ruling": ruling, "n": v["n"], "sample_n": v["sample_n"],
             "read": len(marks) - len(unread), "wrong": len(wrong),
             "wrong_keys": wrong, "why": why or None, "by": by,
             "marks": {c["key"]: {"mark": c["mark"], "why": c.get("mark_why")}
                       for c in v["sample"] if c["mark"]},
             "on": dt.date.today().isoformat(), "at": admin.now()}

    if ruling == "send_back":
        if not wrong and not why:
            return {"error": "say why it goes back, or mark the card that was wrong"}
        rec["rulings"].append({**stamp, "landed": 0})
        bad = admin.save_decisions(AUDITS, audits, "batch-send-back", why=why, by=by)
        if bad:
            return {"error": bad}
        return {"ok": True, "message": f"sent back: {v['n']:,} rows stay in "
                                       f"{admin.LABEL.get(q, q)}, nothing else was written"}

    if unread:
        return {"error": f"read the sample first: {len(unread)} of {v['sample_n']} "
                         f"cards are not marked. Nothing was written."}
    if wrong and not v["read_all"]:
        return {"error": f"{len(wrong)} sample card(s) marked wrong, so this pile "
                         f"cannot be accepted. Send it back. Nothing was written."}
    take = [r for r in rows if not (v["read_all"] and _row_key(r) in wrong)]
    if not take:
        return {"error": "nothing to accept"}

    via = f"batch:{rid}"
    sample_note = (f"read all {v['n']}" if v["read_all"] else
                   f"sample {v['sample_n']} of {v['n']}, none wrong")
    jwhy = f"{why + ' - ' if why else ''}{WHY_BATCH} ({sample_note})"
    apply = APPLIERS[spec["apply"]]
    import io
    with held_writes() as files, contextlib.redirect_stdout(io.StringIO()):
        for r in take:
            out = apply({**spec["body"](r), "by": by, "why": jwhy})
            if not isinstance(out, dict) or out.get("error"):
                return {"error": f"{r.get('name') or _row_key(r)}: "
                                 f"{(out or {}).get('error')}. The batch stopped and "
                                 f"nothing was written."}
    state = _state(files)
    missed = [r.get("name") or _row_key(r) for r in take if not spec["landed"](state, r)]
    if missed:
        return {"error": f"{len(missed)} row(s) did not land ({', '.join(missed[:5])}), so "
                         f"nothing was written. This is a bug in the batch, not in the rows."}
    _stamp_via(files, via)
    if "companies.json" in files:
        err = admin.validate(files["companies.json"]["after"])
        if err:
            return {"error": f"refused: {err}. Nothing was written."}
    plan = []
    for name, f in files.items():
        for before, after in _chunks(name, f["before"], f["after"]):
            _ch, refusal = journal.check(name, before, after, force=True)
            if refusal:
                return {"error": refusal}
            plan.append((name, before, after))
    extra = {"batch": bid, "batch_ruling": rid, "sample": sample_note}
    for name, before, after in plan:
        _eid, refusal = journal.record(name, before, after, f"batch-{q}", by, jwhy,
                                       True, extra)
        if refusal:                        # check() passed above; this is a bug
            return {"error": f"{refusal} (after part of the batch was written: "
                             f"see the journal for {rid})"}
        admin.write_atomic(name, after)
    audits = _audits()
    rec = audits.setdefault(bid, {"queue": q, "pile": p, "sector": sector,
                                  "rulings": []})
    rec.setdefault("rulings", []).append({**stamp, "landed": len(take),
                                          "files": sorted(files)})
    bad = admin.save_decisions(AUDITS, audits, "batch-accept", why=jwhy, by=by)
    if bad:
        return {"error": f"the batch landed but its record did not: {bad}"}
    return {"ok": True, "batch_ruling": rid, "landed": len(take),
            "message": f"{len(take):,} ruled: {spec['answer'].lower()}"}


def _state(files: dict) -> dict:
    """What the landed() checks read: the held copy where the batch wrote,
    the disk where it did not."""
    def f(name, default):
        return files[name]["after"] if name in files else admin.read(name, default)
    import agents
    props = files["agent_proposals.json"]["after"] if "agent_proposals.json" in files \
        else agents.load()
    return {"proposals": props,
            "dismissed": f("admin_dismissed.json", {}),
            "supplier_rulings": f("supplier_rulings.json", {}),
            "founded": f("founded_provenance.json", {}),
            "companies": {c["id"]: c for c in f("companies.json", []) if isinstance(c, dict)}}


# -------------------------------------------------------------- batch undo

def _field_undo(entry: dict, current) -> tuple[dict, list[str], set]:
    """Reverse one batch entry field by field. Returns (restored map,
    conflicts, keys the entry added that this removes)."""
    import journal
    name = entry.get("file") or ""
    cur = journal.snapshot(current, name)
    restored = dict(cur)
    conflicts, removed = [], set()
    for key, ch in (entry.get("changes") or {}).items():
        b, a, c = ch.get("before"), ch.get("after"), cur.get(key)
        if b is None:                                   # the batch added it
            if c is None:
                continue
            if c != a:
                conflicts.append(f"{key} (added, and changed since)")
                continue
            restored.pop(key, None)
            removed.add(key)
        elif a is None:                                 # the batch removed it
            if c is not None:
                conflicts.append(f"{key} (removed, and back since)")
                continue
            restored[key] = b
        elif isinstance(b, dict) and isinstance(a, dict) and isinstance(c, dict):
            new = dict(c)
            for fld in set(b) | set(a):
                if b.get(fld) == a.get(fld):
                    continue
                if c.get(fld) != a.get(fld):
                    conflicts.append(f"{key}.{fld}")
                    continue
                if fld in b:
                    new[fld] = b[fld]
                else:
                    new.pop(fld, None)
            restored[key] = new
        else:
            if c != a:
                conflicts.append(str(key))
                continue
            restored[key] = b
    return restored, conflicts, removed


def act_batch_undo(body: dict) -> dict:
    """Take a batch ruling back: every entry it wrote, newest first, field by
    field, all or nothing. The nightly refresh rewrites hiring on every
    company, so a whole-record comparison (act_undo's) refuses every batch
    that touched companies.json by morning; this compares only what the
    batch wrote."""
    import journal
    rid = (body.get("batch_ruling") or "").strip()
    by = (body.get("by") or "owner").strip()
    if not rid:
        return {"error": "name the batch ruling to undo"}
    rows = journal._entries()
    mine = [e for e in rows if (e.get("batch_ruling") == rid
                                and str(e.get("action", "")).startswith("batch-")
                                and e.get("action") not in ("batch-mark",))]
    if not mine:
        return {"error": f"no journal entries for {rid} in the live journal; an "
                         f"archived batch is undone with scripts/admin_undo.py"}
    if any(journal.undone(e["id"]) for e in mine):
        return {"error": f"{rid} is already undone"}
    state = {}
    plans = []
    for e in reversed(mine):
        name = e["file"]
        if name not in state:
            state[name] = admin.read(name, [] if name in ("companies.json", "suppliers.json")
                                     else {})
        restored, conflicts, removed = _field_undo(e, state[name])
        if conflicts:
            return {"error": f"{len(conflicts)} change(s) moved on since the batch: "
                             f"{', '.join(conflicts[:6])}. Nothing was undone.",
                    "conflicts": conflicts}
        payload = journal.as_payload(state[name], restored, name)
        plans.append((e, name, state[name], payload, removed))
        state[name] = payload
    if "companies.json" in state:
        err = admin.validate(state["companies.json"])
        if err:
            return {"error": f"refused: {err}. Nothing was undone."}
    for e, name, before, after, removed in plans:
        journal.UNDOING_ADDS = set(removed)
        try:
            _ch, refusal = journal.check(name, before, after, force=True)
        finally:
            journal.UNDOING_ADDS = set()
        if refusal:
            return {"error": refusal}
    first = plans[0][0]["id"]
    for e, name, before, after, removed in plans:
        journal.UNDOING_ADDS = set(removed)
        try:
            _eid, refusal = journal.record(name, before, after, "undo", by,
                                           f"undid {rid}", True,
                                           {"undo_of": e["id"], "undo_batch": first,
                                            "batch_ruling": rid})
        finally:
            journal.UNDOING_ADDS = set()
        if refusal:
            return {"error": f"{refusal} (part of the undo was written)"}
        admin.write_atomic(name, after)
    audits = _audits()
    bid = rid.rsplit("#", 1)[0]
    for r in (audits.get(bid) or {}).get("rulings") or []:
        if r.get("id") == rid:
            r["undone"] = {"by": by, "at": admin.now()}
    admin.save_decisions(AUDITS, audits, "batch-undo", why=f"undid {rid}", by=by)
    return {"ok": True, "message": f"undid {rid}"}


def batch_rulings() -> list[dict]:
    """Every batch ruling that landed and stands, for the counters: ONE
    ruling each, whatever it touched."""
    out = []
    for bid, rec in _audits().items():
        if not isinstance(rec, dict):
            continue
        for r in rec.get("rulings") or []:
            if r.get("ruling") == "accept" and not r.get("undone"):
                out.append({**r, "queue": rec.get("queue"), "batch": bid})
    return out


# ---------------------------------------------------- who works each queue

# The backlog's owner column (direction 1). "batch" queues are worked by a
# person on a sample; the rest say who or what is on them, in one line.
WHO = {
    "profiles": ("batch", "The write-up door held these. Rule them by pile, on a sample."),
    "suppliers": ("batch", "Step 1 put a verdict on each. Rule them by verdict, on a sample."),
    "proposals": ("you", "Cards, boards and facts one by one; empty reads as a pile."),
    "miscategorized": ("batch", "The guesser's placement is on each row. High and medium are piles."),
    "founded": ("batch", "Researched years with a quote are a pile; blanks wait for an agent."),
    "review": ("batch", "Read all twelve, then rule them together."),
    "placement": ("batch", "Read all sixteen, then rule them together."),
    "blocked": ("nightly", "Asked again by the crawl. One that answers leaves."),
    "logos": ("nightly", "The nightly parks a guessed logo here for a look."),
    "calendar": ("scrub", "The Sunday conference scrub reads new dates."),
    "sweeps": ("scrub", "The Sunday conference scrub reads floors."),
    "unclassified": ("agent", "Needs the family agent first."),
    "leads": ("agent", "Needs a reader for each careers page first."),
    "boards": ("you", "A warm door: a person reads each one."),
    "scrub": ("you", "Pages only a person can read, with the capture extension."),
    "acquisitions": ("you", "Slow reading, never dealt or batched."),
    "vendors": ("you", "Is this a SLED vendor? One at a time."),
    "duplicates": ("you", "Merge, or not duplicates."),
    "boardfound": ("you", "Wire it, or not theirs."),
    "websites": ("you", "Four findings to read."),
    "signedoff": ("you", "Clears when the fact behind it turns green."),
    "submissions": ("you", "Someone sent this. People first."),
    "users": ("nobody", "Who has access; nothing waits here."),
    "scope": ("nobody", "Empty for good."),
}


# ------------------------------------------------------- questions for you

_LAUNCH: dict = {}


def _launch_state(force: bool = False) -> dict:
    """Is the public site open yet? Asked of the site itself, signed out, at
    most every half hour: the landing page should answer 200 and the boards
    should still ask for a sign-in. A network failure is said as one, never
    read as either answer."""
    now = time.time()
    if not force and _LAUNCH.get("at") and now - _LAUNCH["at"] < 1800:
        return _LAUNCH
    import requests
    import brand
    site = f"https://{brand.DOMAIN}/"
    ua = {"User-Agent": "govtech-dock/1.0 (+https://github.com/westjw/govtech-dock) desk launch check"}
    out = {"at": now, "checked": admin.now(), "site": site}
    try:
        home = requests.get(site, headers=ua, timeout=6, allow_redirects=False)
        jobs = requests.get(site + "?tab=jobs", headers=ua, timeout=6, allow_redirects=False)
        out["home"], out["jobs"] = home.status_code, jobs.status_code
        loc = home.headers.get("Location") or ""
        out["public"] = home.status_code == 200 and "cloudflareaccess" not in loc
        out["boards_gated"] = jobs.status_code in (302, 303, 401, 403)
        out["access_whole_host"] = "cloudflareaccess" in loc
    except Exception as exc:                  # no answer is not an answer
        out["error"] = f"{type(exc).__name__}: the site did not answer"
        out["public"] = None
    _LAUNCH.clear()
    _LAUNCH.update(out)
    return _LAUNCH


def _launch_host() -> str:
    import brand
    return brand.DOMAIN


def questions(check_launch: bool = True) -> dict:
    """The decisions only the owner can make, as cards every view draws the
    same way. Each answer names what happens next and the write it makes."""
    out = []
    ls = _launch_state() if check_launch else dict(_LAUNCH)
    if ls.get("public") is not True:
        out.append({
            "id": "launch", "kind": "launch", "tag": "Launch · Cloudflare dashboard",
            "title": f"Open {_launch_host()} to the public?",
            "body": "The landing page is built and waiting. Three steps in the "
                    "Cloudflare dashboard, and only you can do them.",
            "steps": ["Put the admin path back on the Access app.",
                      "Let anyone sign in, with Google or a one-time code.",
                      "Keep Fail closed."],
            "state": ("The site did not answer just now." if ls.get("error") else
                      "Access still covers the whole site." if ls.get("access_whole_host") else
                      f"The home page answered {ls.get('home')}." if ls.get("home") else
                      "Not checked yet."),
            "checked": ls.get("checked"),
            "answers": [{"key": "done", "label": "I did the 3 steps",
                         "next": [f"The desk asks {_launch_host()} again, signed out.",
                                  "It should show the landing page, and the boards should still ask for a sign-in."]}],
            "doc": "DEPLOY.md, section 3a"})
    for qid, q in admin.read(QUESTIONS, {}).items():
        if not isinstance(q, dict) or q.get("answered"):
            continue
        out.append({**{k: v for k, v in q.items() if k != "apply"}, "id": qid})
    for r in _safe_rows("submissions"):
        out.append({"id": f"submission:{r.get('id')}", "kind": "submission",
                    "tag": "From people · Add a company",
                    "title": "Someone sent a company: " + (r.get("name") or r.get("company")
                                                            or re.sub(r"^https?://(www\.)?", "",
                                                                      r.get("url") or "?").rstrip("/")),
                    "facts": [["Sent", r.get("on") or "?"], ["By", r.get("submitted_by") or "?"],
                              ["Their note", (r.get("note") or "")[:400]]],
                    "body": "A submission is a claim, not a fact. Nothing reaches the "
                            "board until you approve it.",
                    "since": r.get("on"), "queue": "submissions", "row": r,
                    "answers": [{"key": "open", "label": "Open it",
                                 "next": ["Shows the guess and its evidence first."]}]})
    watch = []
    logos = _safe_rows("logos")
    watch.append({"queue": "logos", "n": len(logos),
                  "text": (f"{len(logos)} guessed logo(s) parked for a look." if logos
                           else "Logos to check: none. The nightly parks a guessed logo here for a look.")})
    return {"questions": out, "watch": watch, "launch": {k: v for k, v in ls.items() if k != "at"}}


def _safe_rows(queue: str) -> list:
    try:
        return [r for r in admin.QUEUES[queue](admin.read("companies.json", []),
                                               admin.read("board.json", {}))
                if isinstance(r, dict)]
    except Exception:
        return []


def act_desk_answer(body: dict) -> dict:
    """Answer one question. The launch check writes nothing: it asks the site.
    A question on file records the answer (journalled, so Undo takes it back)
    and makes the write the answer names, through the same helpers the
    queues use."""
    qid = (body.get("id") or "").strip()
    ans = (body.get("answer") or "").strip()
    by = (body.get("by") or "owner").strip()
    why = (body.get("why") or "").strip()
    if qid == "launch":
        ls = _launch_state(force=True)
        if ls.get("public"):
            return {"ok": True, "message": f"{_launch_host()} shows the landing page to a "
                    "stranger" + (", and the boards still ask for a sign-in."
                                  if ls.get("boards_gated") else
                                  ". THE BOARDS DID NOT ASK FOR A SIGN-IN: check the gate.")}
        return {"ok": True, "message": "Not yet: " + (ls.get("error") or (
            "Access still answers for the whole site." if ls.get("access_whole_host")
            else f"the home page answered {ls.get('home')}.")), "still": True}
    qs = admin.read(QUESTIONS, {})
    q = qs.get(qid)
    if not isinstance(q, dict):
        return {"error": f"no question {qid!r}"}
    if q.get("answered"):
        return {"error": "already answered"}
    a = next((x for x in q.get("answers") or [] if x.get("key") == ans), None)
    if a is None:
        return {"error": f"{ans!r} is not one of this question's answers"}
    step = (q.get("apply") or {}).get(ans) or {}
    if step.get("do") == "not_a_supplier":
        sup = admin.read("suppliers.json", [])
        row = next((s for s in sup if isinstance(s, dict) and s.get("id") == step["id"]), None)
        if row is None:
            return {"error": f"no supplier {step['id']!r}"}
        row["not_a_supplier"] = (f"{why or step.get('why') or 'a government body'} "
                                 f"(ruled by {by}, {dt.date.today().isoformat()})")
        bad = admin.save_decisions("suppliers.json", sup, "not-a-supplier", why=why, by=by)
        if bad:
            return {"error": bad}
    elif step.get("do") == "ask_claude":
        # The one channel for work that needs reading, gitignored on purpose
        # (CLAUDE.md "Claude's inbox"): an agent looks, a person confirms.
        p = admin.DATA / "claude_inbox.jsonl"
        with p.open("a") as fh:
            fh.write(json.dumps({"id": f"desk-{qid}-{int(time.time())}", "at": admin.now(),
                                 "from": "desk question", "company": step.get("company"),
                                 "item": step.get("item") or qid,
                                 "text": step.get("ask") or q.get("title"),
                                 "saw": {"question": q.get("title"), "answer": a.get("label")},
                                 "status": "open"}) + "\n")
    q["answered"] = {"answer": ans, "label": a.get("label"), "by": by,
                     "why": why or None, "on": dt.date.today().isoformat(), "at": admin.now()}
    bad = admin.save_decisions(QUESTIONS, qs, "question-answer", why=why or a.get("label"), by=by)
    if bad:
        return {"error": bad}
    return {"ok": True, "message": a.get("done") or "answered"}


# ------------------------------------------------------------- health map

MEASURES = [("logo", "Logo"), ("website", "Website"), ("board", "Readable job board"),
            ("writeup", "Write-up"), ("founded", "Founding year")]
CLOSES = {"logo": "logos", "website": "websites", "board": "boards",
          "writeup": "profiles", "founded": "founded"}


def health() -> dict:
    """Department by measure, each square the share of a department's
    companies that have it, by the page sweep's own checklist (one
    definition: page_belt.checklist), the logo files, and the board's open
    roles. A company with no public board at all is FINISHED, not missing:
    discovery checked and found none (coverage.state 'absent'), so it is
    counted apart and never as a gap."""
    import page_belt, coverage
    companies = admin.read("companies.json", [])
    board = admin.read("board.json", {})
    ctx = page_belt.context(companies, board)
    logos = {p.stem for p in (admin.ROOT / "assets" / "logos").glob("*.*") if p.is_file()}
    orgs = {o.get("id"): o for o in board.get("organizations", []) if isinstance(o, dict)}
    log = admin.read("discovery_log.json", {})
    rows = collections.OrderedDict()
    whole = collections.Counter()
    for c in companies:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        items = {i["key"]: i["green"] for i in page_belt.checklist(c, ctx)}
        sec = c.get("sector") or "?"
        a = rows.setdefault(sec, collections.Counter())
        hiring = (orgs.get(c["id"]) or {}).get("open_roles") or 0
        have = {"logo": c["id"] in logos, "website": bool(items.get("website")),
                "board": bool(items.get("board")), "writeup": bool(items.get("writeup")),
                "founded": bool(items.get("founded"))}
        absent = not have["board"] and coverage.state(c, log.get(c["id"]), orgs.get(c["id"])) == "absent"
        for tgt in (a, whole):
            tgt["n"] += 1
            tgt["hiring"] += bool(hiring)
            tgt["board_absent"] += absent
            for k, v in have.items():
                tgt[k] += v
                if not v and hiring and not (k == "board" and absent):
                    tgt[f"{k}_hiring_without"] += 1

    def cell(a, k):
        n = a["n"] - (a["board_absent"] if k == "board" else 0)
        have = a[k]
        return {"have": have, "of": n, "share": round(100 * have / n) if n else None,
                "without": n - have, "hiring_without": a[f"{k}_hiring_without"]}

    def line(name, a):
        return {"sector": name, "n": a["n"], "hiring": a["hiring"],
                "hiring_share": round(100 * a["hiring"] / a["n"]) if a["n"] else None,
                "board_absent": a["board_absent"],
                "cells": {k: cell(a, k) for k, _ in MEASURES}}

    deps = sorted((line(s, a) for s, a in rows.items()), key=lambda r: -r["n"])
    gaps = []
    for d in deps:
        k = min((k for k, _ in MEASURES), key=lambda k: (d["cells"][k]["share"] or 0))
        gaps.append({"sector": d["sector"], "measure": k,
                     "label": dict(MEASURES)[k], **d["cells"][k],
                     "closes": CLOSES[k], "queue_label": admin.LABEL.get(CLOSES[k], CLOSES[k])})
    gaps.sort(key=lambda g: (g["share"] or 0, -g["hiring_without"]))
    return {"departments": deps, "whole": line("Whole board", whole),
            "measures": [{"key": k, "label": l, "closes": CLOSES[k],
                          "queue_label": admin.LABEL.get(CLOSES[k], CLOSES[k])}
                         for k, l in MEASURES],
            "gaps": gaps[:5]}


def pile_counts(queue: str, sector: str | None) -> list:
    """The piles one queue would offer for one department, without reading
    every other queue (the health map asks this per gap)."""
    companies = admin.read("companies.json", [])
    board = admin.read("board.json", {})
    _load_sectors(companies)
    if queue not in admin.QUEUES:
        return []
    rows = [r for r in _queue_rows(queue, companies, board) if _in_sector(r, sector)]
    audits = _audits()
    return [_view(s, _rows_for(s, rows, None), sector, audits)
            for s in PILES if s["queue"] == queue]


# ------------------------------------------------------------------ ledger

# the batch engine's own records, which ride with the batch they belong to
AUDIT_ACTIONS = ("batch-accept", "batch-undo", "batch-send-back", "batch-mark")

PHRASE = {
    "set-founded": "set a founding year", "confirm-founded": "confirmed a founding year",
    "dismiss": "dismissed a row", "place": "filed a company", "move": "moved a company",
    "place-ruling": "filed a company", "patch": "edited a company",
    "merge": "merged duplicates", "save-website": "saved a website",
    "set-board": "wired a board", "retry-board": "retried a board",
    "posts-at": "said where they post", "proposal-accept": "accepted a proposal",
    "proposal-reject": "rejected a proposal", "board-proposal": "ruled a found board",
    "vendor-scope": "ruled vendor scope", "vendor-scope-all": "ruled vendor scope",
    "supplier-site": "ruled a supplier website", "promote-rivals": "promoted competitor lists",
    "promote-profiles": "landed write-ups", "add-company": "added a company",
    "undo": "took a ruling back", "question-answer": "answered a question",
    "not-a-supplier": "took a supplier off the list", "scope": "ruled a role's scope",
    "logo-accept": "accepted a logo", "logo-refuse": "refused a logo",
    "resolve-submission": "answered a submission", "identity-ruling": "ruled a website",
    "page-competitors": "set a page's competitors", "page-approve": "approved a page",
    "merge-supplier-records": "merged supplier records", "set-family": "filed a job title",
    "acquisition-ruling": "ruled an acquisition", "conference-date": "ruled a conference date",
    "capture": "captured jobs", "page-board": "added a board from the belt",
    "page-ownership": "answered ownership", "reopen": "reopened a ruling",
}


def _who(by: str | None, desk: bool) -> str:
    if admin._is_person(by):
        return "yours" if desk else "yours, written in chat"
    b = str(by or "")
    if b == "web":
        return "phone"
    return "doors"


def ledger(limit: int = 60, days: int | None = None) -> dict:
    """The journal as decisions, newest first: one row per desk request or
    batch (journal.DESK's same_ruling_as, desk's batch_ruling), saying who,
    what, how many, and whether Undo can take it back."""
    import journal
    rows = journal._entries()
    undone = admin._undone_ids(rows)
    groups: dict = collections.OrderedDict()
    for e in rows:
        if e.get("action") in ("batch-mark",):
            continue
        # one desk request is one row: journal.DESK links every entry after
        # the first to it, which is also how a batch's files and its audit
        # record arrive together
        lead = e.get("same_ruling_as") or e.get("id")
        g = groups.get(lead)
        if g is None:
            g = groups[lead] = {"lead": lead, "at": e.get("at"), "by": e.get("by"),
                                "desk": bool(e.get("desk")), "actions": [], "files": [],
                                "n": 0, "ids": [], "why": e.get("why") or "",
                                "batch_ruling": None, "undo_of": e.get("undo_of")}
        g["actions"].append(e.get("action"))
        g["files"].append(e.get("file"))
        g["n"] += int(e.get("n") or 0)
        g["ids"].append(e.get("id"))
        if e.get("batch_ruling"):
            g["batch_ruling"] = e["batch_ruling"]
            g["batch_undo"] = g.get("batch_undo") or e.get("action") == "undo"
    out = []
    audits = _audits()
    labels = {}
    for bid, rec in audits.items():
        for r in (rec or {}).get("rulings") or []:
            labels[r.get("id")] = (bid, r)
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat() if days else None
    for g in reversed(groups.values()):
        if cutoff and str(g["at"])[:10] < cutoff:
            break
        named = [a for a in g["actions"] if a not in AUDIT_ACTIONS] or g["actions"]
        first = named[0]
        if g["batch_ruling"]:
            bid, r = labels.get(g["batch_ruling"], (None, {}))
            spec = _spec(*_parse_id(bid)[:2]) if bid else None
            label = _label(spec, (r or {}).get("landed") or g["n"]) if spec \
                else g["batch_ruling"]
            if (r or {}).get("landed") is not None:
                g["n"] = r["landed"]
            if g.get("batch_undo"):
                what, undo = f"Took back a batch: {label}", None
            else:
                what = f"Batch: {label}"
                undo = None if (r or {}).get("undone") else {"batch_ruling": g["batch_ruling"]}
        elif first == "batch-send-back":
            what, undo = "Sent a pile back", None
        else:
            what = PHRASE.get(first, first.replace("-", " "))
            ok = (admin._is_person(g["by"]) and first not in ("undo", "reopen")
                  and not any(i in undone for i in g["ids"]) and len(g["ids"]) <= 10)
            undo = {"entries": g["ids"]} if ok else None
        out.append({"at": g["at"], "day": str(g["at"])[:10], "who": _who(g["by"], g["desk"]),
                    "by": g["by"], "what": what, "n": g["n"],
                    "files": sorted(set(g["files"])), "why": (g["why"] or "")[:300],
                    "undone": any(i in undone for i in g["ids"]),
                    "undo": undo, "ids": g["ids"]})
        if len(out) >= limit:
            break
    return {"rows": out}


# ------------------------------------------------------------------- brief

_GH: dict = {}


def _runs() -> dict:
    """The nightly's last runs, from GitHub, if this machine's gh can say.
    Cached ten minutes; anything else is 'not known here', never 'fine'."""
    now = time.time()
    if _GH.get("at") and now - _GH["at"] < 600:
        return _GH
    out = {"at": now}
    try:
        p = subprocess.run(["gh", "run", "list", "-R", "westjw/govtech-dock",
                            "--workflow", "refresh.yml", "-L", "4", "--json",
                            "conclusion,createdAt,status,url"],
                           capture_output=True, text=True, timeout=10)
        out["runs"] = json.loads(p.stdout) if p.returncode == 0 else None
        if p.returncode != 0:
            out["error"] = (p.stderr or "gh failed").strip()[:200]
    except Exception as exc:
        out["runs"], out["error"] = None, f"{type(exc).__name__}"
    _GH.clear()
    _GH.update(out)
    return _GH


_LOGOS: dict = {}


def _logos_added_today() -> int | None:
    """Logos the history says arrived today, or None where there is no
    history to ask (a copy of the checkout): not known is not zero."""
    now = time.time()
    if _LOGOS.get("at") and now - _LOGOS["at"] < 600:
        return _LOGOS.get("n")
    n = None
    try:
        p = subprocess.run(["git", "-C", str(admin.ROOT), "log", "--since=midnight",
                            "--diff-filter=A", "--format=", "--name-only", "--",
                            "assets/logos/"], capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            n = sum(1 for line in p.stdout.splitlines()
                    if line.strip() and line.count("/") == 2)
    except Exception:
        n = None
    _LOGOS.update({"at": now, "n": n})
    return n


def brief() -> dict:
    """The morning brief: what ran overnight, what needs you, your rulings
    lately, what the doors did, and the backlog. Every line from a file or
    the journal; what this machine cannot know is said so."""
    import journal
    today = dt.date.today().isoformat()
    meta = admin.read("meta.json", {})
    diff = admin.read("latest_diff.json", {})
    board = admin.read("board.json", {})
    # A LOGO FOUND TODAY is one a commit added today. A file's age on disk
    # says when this checkout wrote it (a fresh worktree makes all 2,000 new),
    # and the log's `on` is newer than most of its entries, so git is asked.
    found_today = _logos_added_today()
    parked_today = sum(1 for p in (admin.ROOT / "assets" / "logos" / "review").glob("*.*")
                       if p.is_file())
    orgs = [o for o in board.get("organizations", []) if isinstance(o, dict)]
    hiring = [o for o in orgs if (o.get("open_roles") or 0) > 0]
    have_logo = {p.stem for p in (admin.ROOT / "assets" / "logos").glob("*.*") if p.is_file()}
    tot = board.get("totals") or {}
    led = ledger(limit=400, days=30)["rows"]
    mine = [r for r in led if r["who"].startswith("yours")]
    by_day = collections.OrderedDict()
    for r in mine:
        by_day.setdefault(r["day"], []).append(r)
    doors = collections.Counter()
    door_n = collections.Counter()
    for r in led:
        if r["who"] == "doors":
            doors[str(r["by"])] += 1
            door_n[str(r["by"])] += r["n"]
    q = questions()
    return {"date": today, "last_run": meta.get("last_run"),
            "board": {"open_roles": tot.get("openings") or tot.get("open_roles"),
                      "postings": len(board.get("postings") or []),
                      "companies": len(orgs),
                      "changes": len(diff.get("changes") or []) if diff.get("date") == today else None},
            "logos": {"found_today": found_today, "parked_today": parked_today,
                      "hiring": len(hiring),
                      "hiring_with_logo": sum(1 for o in hiring if o.get("id") in have_logo)},
            "runs": {k: v for k, v in _runs().items() if k != "at"},
            "questions": q, "rulings": [{"day": d, "rows": rs[:12], "n": len(rs)}
                                        for d, rs in list(by_day.items())[:7]],
            "doors": [{"by": k, "entries": v, "records": door_n[k]}
                      for k, v in doors.most_common(8)],
            "publish": admin.publish_status()}


# --------------------------------------------------------------------- ask

VERBS = [("accept", ("accept", "approve", "land", "take", "rule", "yes to")),
         ("send_back", ("send back", "reject", "refuse")),
         ("undo", ("undo", "take back", "reverse")),
         ("show", ("show", "open", "list", "find", "see", "look at", "go to"))]
QUEUE_WORDS = {
    "profiles": ("write-ups", "write ups", "writeups", "write-up", "profiles", "descriptions"),
    "proposals": ("agent proposals", "proposals", "empty reads", "reads"),
    "suppliers": ("supplier websites", "suppliers", "supplier sites"),
    "miscategorized": ("wrong bucket", "buckets", "miscategorized", "categories"),
    "founded": ("founding years", "founding year", "founded", "years"),
    "review": ("website review",), "placement": ("wrong placement", "placements"),
    "boards": ("no board found", "no board"), "blocked": ("blocked boards", "blocked"),
    "scrub": ("scrub",), "vendors": ("vendor scope", "vendors"),
    "duplicates": ("duplicates", "dupes"), "acquisitions": ("acquisitions",),
    "unclassified": ("unclassified roles", "unclassified"),
    "leads": ("warm leads", "leads"), "boardfound": ("boards we found", "found boards"),
    "calendar": ("conference dates", "dates"), "sweeps": ("conference floors", "floors"),
    "submissions": ("submissions", "submission"), "logos": ("logos to check", "logos"),
    "websites": ("missing websites",), "signedoff": ("open on approved pages", "approved pages"),
    "users": ("users",), "scope": ("scope review",),
}
VIEW_WORDS = {"ledger": ("ledger", "my rulings", "what did i"), "health": ("health map", "health"),
              "brief": ("brief", "morning"), "batches": ("batches", "piles"),
              "waiting": ("waiting on me", "what needs me", "decisions")}


def ask(text: str) -> dict:
    """Read a typed decision into a verb and what it is about, and say what
    it would do. Writes nothing: an accept opens the pile, where the sample
    and the button with the number are; a show opens the queue. Every part
    of the reading is shown, so a wrong reading is seen before anything."""
    t = " " + re.sub(r"\s+", " ", (text or "").lower()).strip() + " "
    if not t.strip():
        return {"error": "type what to do"}
    verb = next((v for v, ws in VERBS if any(f" {w} " in t or t.startswith(f" {w}") for w in ws)), None)
    schema = admin.read("schema.json", {"sectors": []})
    sectors = [s["name"] for s in schema.get("sectors", [])]
    sector = next((s for s in sorted(sectors, key=len, reverse=True)
                   if s.lower() in t or s.lower().replace(" & ", " and ") in t), None)
    queue = None
    for q, ws in QUEUE_WORDS.items():
        for w in sorted(ws, key=len, reverse=True):
            if f" {w} " in t or f" {w}" in t.rstrip():
                queue = queue or q
    view = next((v for v, ws in VIEW_WORDS.items() if any(w in t for w in ws)), None)
    company = None
    if not queue and not view and " launch" not in t:
        cos = admin.read("companies.json", [])
        words = t.strip()
        best = None
        for c in cos:
            n = (c.get("name") or "").lower()
            if len(n) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", words):
                if best is None or len(n) > len(best["name"]):
                    best = c
        company = best and {"id": best["id"], "name": best["name"], "sector": best.get("sector")}
    read = {"verb": verb, "queue": queue, "queue_label": admin.LABEL.get(queue) if queue else None,
            "sector": sector, "company": company, "view": view}
    import brand
    if brand.DOMAIN in t or " launch" in t or " go public" in t or " the site to the public" in t:
        return {"read": {**read, "about": "launch"},
                "say": f"Opening {brand.DOMAIN} to the public is yours: three steps in Cloudflare.",
                "go": {"view": "question", "id": "launch"}}
    if verb == "undo" or view == "ledger":
        return {"read": read, "say": "The ledger lists every ruling with its Undo.",
                "go": {"view": "ledger"}}
    if company and not queue:
        return {"read": read, "say": f"{company['name']}: opens its page on the belt.",
                "go": {"view": "company", "id": company["id"], "sector": company.get("sector")}}
    if queue and verb in ("accept", "send_back"):
        piles = pile_counts(queue, sector)
        live = [p for p in piles if p["n"]]
        if not piles:
            why = NEVER.get(queue) or NEEDS_AGENT.get(queue) or "It has no pile."
            return {"read": read, "say": f"{admin.LABEL.get(queue, queue)} is not ruled as a "
                                         f"batch. {why}",
                    "go": {"view": "queue", "queue": queue, "sector": sector}}
        if not live:
            return {"read": read, "say": "Nothing waits in its piles"
                                         + (f" for {sector}" if sector else "") + ".",
                    "piles": piles}
        return {"read": read, "piles": live,
                "say": ("Accepting a pile needs its sample read first. "
                        if verb == "accept" else "Sending a pile back writes only the record. ")
                       + "Pick the pile:",
                "go": {"view": "batch", "batch": live[0]["id"]} if len(live) == 1 else None}
    if queue:
        return {"read": read, "say": f"Opens {admin.LABEL.get(queue, queue)}"
                                     + (f" for {sector}" if sector else "") + ".",
                "go": {"view": "queue", "queue": queue, "sector": sector}}
    if view:
        return {"read": read, "say": f"Opens the {view} view.", "go": {"view": view}}
    if sector:
        return {"read": read, "say": f"Opens the health map at {sector}.",
                "go": {"view": "health", "sector": sector}}
    return {"read": read, "say": "Not sure what that names. Start with what to do, then "
                                 "a queue, a department or a company: \"accept the "
                                 "write-ups for Public Safety\", \"show me the duplicates\"."}
