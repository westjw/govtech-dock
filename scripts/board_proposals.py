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

import json
import pathlib
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


def _doubts_shape(got) -> str | None:
    if isinstance(got, dict) and all(isinstance(k, str) and isinstance(v, dict)
                                     for k, v in got.items()):
        return None
    return "{company id: the doubt}"


# name -> (what a missing file is, what a file that reads must look like)
SHAPES = {"embedded_ats.json": (list, _rows_shape),
          "board_proposal_rulings.json": (dict, _map_shape),
          PROPOSALS: (dict, _map_shape),
          DOUBTS: (dict, _doubts_shape)}


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


def ruled_since(ruling, doubt: dict) -> bool:
    """Has the owner ruled on this company in Boards we found since the
    doubt was recorded? Only a ruling shown to be later counts: by the stamp
    where both carry one, else by a later date. A ruling from before the
    doubt, or from the same day with no stamps to tell them apart, leaves the
    doubt standing - that is the answer that wires less."""
    if not isinstance(ruling, dict):
        return False
    ra, da = when(ruling.get("at")), when(doubt.get("at"))
    if ra and da:
        return ra >= da
    ro, do = str(ruling.get("on") or ""), str(doubt.get("on") or "")
    return bool(ro and do and ro > do)


def standing_doubts(store: dict, ruled: dict) -> dict:
    """{company id: its standing doubt} - the doubts in `store`
    (board_doubts.json) the owner has not ruled on since (`ruled`,
    board_proposal_rulings.json)."""
    return {cid: d for cid, d in (store or {}).items()
            if isinstance(d, dict) and not ruled_since((ruled or {}).get(cid), d)}


def held_for_the_owner(data=None) -> dict:
    """{company id: why it is the owner's} for every company a door must not
    wire a board for: a reader's doubt that stands, a row a door filed in
    Boards we found that he has not ruled on, or a board refused in either
    store (Boards we found, Agent proposals).

    `data` is the directory the CALLER writes - discovery passes its own, so
    the files it reads are the files beside the companies.json it writes. A
    file that is on disk and does not read raises Unreadable, and the caller
    stops before its first request.
    """
    found = strict("embedded_ats.json", data)
    ruled = strict("board_proposal_rulings.json", data)
    props = strict(PROPOSALS, data)
    doubts = strict(DOUBTS, data)
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
    return out


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
    from. identity_why is the doubt in words: on a MISMATCH the desk prints
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
        new.append(privacy.redact_deep({
            "id": cid, "name": c.get("name"), "was": kind,
            "found": {"type": r["found"]["type"], "ref": r["found"]["ref"]},
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
