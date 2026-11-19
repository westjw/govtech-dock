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


def _read(p: pathlib.Path, default):
    return json.loads(p.read_text()) if p.exists() else default


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
      - force. Over the journal's 25-record limit the caller shows the count
        first and passes force=True itself.

    A row is {id, found {type, ref}, saw, times, verified, board_calls_itself,
    identity ("unknown" or "MISMATCH"), identity_why, postings, titles} and
    may carry `page`, the page on the company's own site that pointed to the
    board. identity_why is the doubt in words: on a MISMATCH the desk prints
    it in red, on every other filed row it prints it as the reason the board
    was not wired. `name` and `was` are taken from the company record, not
    from the caller, and `filed` records who filed it and when.
    """
    import datetime as dt

    import admin
    import journal
    import privacy

    out = {"added": [], "skipped": [], "error": None}
    by = (by or "").strip()
    if not by:
        out["error"] = "a filing needs an author: agent:<label>"
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
        filed = {"by": by, "on": today, "at": stamp}
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
        print("no board proposals waiting."
              " Run scripts/find_embedded_ats.py to make some.")
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
