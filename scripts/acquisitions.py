#!/usr/bin/env python3
"""Companies whose job board belongs to somebody else, and probably to a parent.

WHY THIS NEEDED MORE THAN IT HAD

The Acquisitions queue already held 59 rows, every one saying the same thing:
"the slug does not match the name; likely someone else's board". That is a
suspicion, and a queue of 59 identical suspicions with no evidence attached is
a queue nobody works.

There are two much stronger signals available, and both are cheap:

  WHAT THE BOARD CALLS ITSELF. Greenhouse stamps company_name on every job.
  Prepared's board says "Axon" - because Axon acquired them - and ParkHub's
  says "JustPark". That is not a suspicion, it is the employer's own claim
  about who they are.

  WHERE THE CAREERS PAGE ENDS UP. Simpleview's redirects to granicus.com.
  An acquired company's careers page stops being theirs, and the final host
  after redirects says so without anybody having to read anything.

THE DECISION THIS QUEUE IS FOR, WHICH IS NOT "delete the board"

CLAUDE.md's rule is never to point a company at its parent's board, because
doing so reports the parent's requisitions as the subsidiary's. But the answer
is not always to unwire it. Three outcomes, and they are genuinely different:

  KEEP AND LABEL - the board really does carry this company's roles among
  others. The card then says "these roles are posted on Axon's board, who
  acquired them, and some may not be Prepared roles", which is true and
  useful.
  UNWIRE - the board is entirely the parent's and none of it is theirs. The
  company goes back to having no board, which is honest.
  NOT AN ACQUISITION - the slug is just odd. Vision Government Solutions
  uses "vgsi"; that is their own shorthand and nothing is wrong.

Recording which of those it is means the next sweep stops asking.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RULED = DATA / "acquisition_rulings.json"


def _read(p: pathlib.Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def evidence_for(cid: str) -> dict:
    """Everything already gathered about this company's board, in one place.

    Read from files rather than fetched, because a queue that makes 60 network
    calls to render is a queue that times out. The sweeps write these; this
    only collects them.
    """
    out = {}
    # THE LOGO FETCHER IS AN ACQUISITION DETECTOR AND NOBODY WAS READING IT.
    # It refuses a logo when a company's own domain redirects somewhere that
    # serves a different brand - "redirects to www.tylertech.com - a different
    # brand, not taken" - because taking that image would put Tyler's mark on
    # VendEngine's card. That refusal is the same fact this queue exists for,
    # arrived at from the other direction: a company whose website is now
    # somebody else's website has usually been bought. Eight were sitting in
    # logo_log.json unread, including four whose parent is not in the dataset
    # at all.
    for lid, e in (_read(DATA / "logo_log.json", {}) or {}).items():
        if lid != cid or not isinstance(e, dict):
            continue
        note = e.get("note") or ""
        if "different brand" in note:
            m = re.search(r"redirects to (\S+)", note)
            if m:
                out.setdefault("redirect", {"to": m.group(1).rstrip(" -"),
                                            "from": "their own website"})
    for row in _read(DATA / "board_audit.json", []):
        if row.get("id") == cid:
            out["identity"] = row.get("identity")
            out["identity_why"] = row.get("identity_why")
            out["board_calls_itself"] = row.get("board_calls_itself")
            out["redirect"] = row.get("redirect")
            out["slug_tell"] = row.get("slug_tell")
            out["postings"] = row.get("postings")
    for row in _read(DATA / "embedded_ats.json", []):
        if row.get("id") == cid and row.get("identity") == "MISMATCH":
            out.setdefault("board_calls_itself", row.get("board_calls_itself"))
            out.setdefault("identity_why", row.get("identity_why"))
            out.setdefault("postings", row.get("postings"))
            out["titles"] = row.get("titles") or []
    return out


def q_acquisitions(companies, board) -> list:
    """Boards that look like they belong to a parent, strongest evidence first.

    Ordered so the ones with a name to point at come before the ones with only
    a strange slug: "the board says Axon" is a different quality of fact from
    "the slug is not the name", and a person should meet them in that order.
    """
    ruled = _read(RULED, {})
    dismissed = _read(DATA / "admin_dismissed.json", {})
    live = {o["id"]: o for o in board.get("organizations", [])}
    by = {c["id"]: c for c in companies}

    seen, out = set(), []
    def add(cid, source):
        if cid in seen or cid in ruled:
            return
        if f"acquisitions:{cid}" in dismissed \
                or cid in (dismissed.get("acquisitions") or {}):
            return
        c = by.get(cid)
        if not c:
            return
        seen.add(cid)
        ev = evidence_for(cid)
        o = live.get(cid, {})
        # how sure we are, and it is worth being explicit rather than sorting
        # by a number nobody can interpret
        if ev.get("board_calls_itself"):
            strength, says = "named", (
                f'their board calls itself "{ev["board_calls_itself"]}"')
        elif ev.get("redirect"):
            # name the page that actually redirected. A careers page pointing
            # at a parent is ordinary - lots of groups run one board - but the
            # company's OWN homepage serving somebody else's brand is a much
            # stronger claim, and calling both "their careers page" would flatten
            # the difference and mislabel the evidence a person is ruling on.
            whose = ev["redirect"].get("from") or "their careers page"
            strength, says = "redirect", (
                f'{whose} ends up on {ev["redirect"]["to"]}')
        else:
            strength, says = "slug", (
                f'the board slug is "{(c.get("ats") or {}).get("ref")}", '
                f'which is not their name')
        out.append({
            "id": cid, "name": c["name"], "source": source,
            "description": c.get("description"), "website": c.get("website"),
            "ats": c.get("ats"), "parent": c.get("parent"),
            "board_owner": c.get("board_owner"),
            "open_roles": o.get("open_roles", 0),
            "strength": strength, "says": says,
            "titles": (ev.get("titles") or [])[:5],
            "postings_on_that_board": ev.get("postings"),
        })

    for row in _read(DATA / "board_audit.json", []):
        add(row.get("id"), "audit")
    for row in _read(DATA / "embedded_ats.json", []):
        if row.get("identity") == "MISMATCH":
            add(row.get("id"), "embedded")
    for lid, e in (_read(DATA / "logo_log.json", {}) or {}).items():
        if isinstance(e, dict) and "different brand" in (e.get("note") or ""):
            add(lid, "website-redirect")
    sus = _read(DATA / "ats_suspects.json", {})
    items = sus.get("suspects", sus) if isinstance(sus, dict) else sus
    if isinstance(items, dict):
        items = [{"id": k, **v} for k, v in items.items()]
    for i in items or []:
        add(i.get("id"), "slug")

    rank = {"named": 0, "redirect": 1, "slug": 2}
    out.sort(key=lambda r: (rank[r["strength"]], -(r["open_roles"] or 0)))
    return out


def rule(company_id: str, outcome: str, parent: str = "", why: str = "",
         by: str = "owner") -> dict:
    """outcome is keep | unwire | not_acquired."""
    if outcome not in ("keep", "unwire", "not_acquired"):
        raise ValueError("outcome must be keep, unwire or not_acquired")
    ruled = _read(RULED, {})
    ruled[company_id] = {"outcome": outcome, "parent": parent.strip(),
                         "why": why.strip(), "by": by,
                         "on": dt.date.today().isoformat()}
    RULED.write_text(json.dumps(ruled, indent=1, sort_keys=True) + "\n")
    return ruled[company_id]


def main() -> int:
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    rows = q_acquisitions(companies, board)
    named = [r for r in rows if r["strength"] == "named"]
    red = [r for r in rows if r["strength"] == "redirect"]
    slug = [r for r in rows if r["strength"] == "slug"]
    print(f"{len(rows)} boards that may belong to a parent\n")
    if named:
        print(f"THE BOARD NAMES SOMEBODY ELSE ({len(named)}) — the employer's "
              f"own claim, not our guess:")
        for r in named:
            print(f"  {r['name'][:26]:28} {r['says']}"
                  f"   [{r['open_roles']} roles on our board]")
    if red:
        print(f"\nTHEIR PAGE GOES SOMEWHERE ELSE ({len(red)}) - the line says which page:")
        for r in red:
            print(f"  {r['name'][:26]:28} {r['says']}")
    print(f"\nONLY A STRANGE SLUG ({len(slug)}) — weakest, and most of these "
          f"will be nothing:")
    for r in slug[:12]:
        print(f"  {r['name'][:26]:28} {r['says'][:70]}")
    if len(slug) > 12:
        print(f"  ... and {len(slug) - 12} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
