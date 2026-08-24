#!/usr/bin/env python3
"""Fold a brand family into one company record that still lists its brands.

  python3 scripts/merge_families.py                 what it would do (dry run)
  python3 scripts/merge_families.py --apply         do it, through save_companies
  python3 scripts/merge_families.py --show          what merged, and how to undo
  python3 scripts/merge_families.py --data DIR      run against a copy of data/

WHY THIS EXISTS AND NOT JUST act_merge

act_merge is right and is reused verbatim for the fold itself: survivor keeps
what it has, inherits what it lacks, dropped name goes into also_known_as. Two
things it cannot know sit either side of it.

BEFORE - a merge can orphan live postings, and nothing downstream would say so.
build_board.py keys every posting on the COMPANY id and fetches the board from
that company's single `ats` block. So the postings of a family follow the
records that hold the boards, not the family. Xplor's family holds TWO live
boards - smartrecruiters/Xplor (100 postings) and rippling/
clubessential-holdings-cjb (5) - and a company record has room for one. Folding
every brand into one record would delete the second board's only holders and
five real roles would vanish from the next build with no error anywhere. So
preflight() below simulates build_board's own attribution over the POST-merge
file and refuses any fold that would leave a board with postings unreferenced.
A refusal names the board and stops that member; it does not stop the family.

AFTER - three kinds of research live in the DROPPED record's shape rather than
in its fields, and inheritance cannot carry them:
  * its market placement. Simpleview is filed Parks & Rec / Events & Tourism
    and Granicus is General Gov / Citizen Services. Fold one into the other and
    the tourism placement is simply gone, so a filter on Events & Tourism stops
    finding 53 real postings. `also` is where a second placement belongs, and
    carrying one is a judgement (a conference row's guessed category is not a
    researched placement), so it is declared per family here, never automatic.
  * the brand itself. The owner's requirement is that the parent's page still
    lists its sub-companies with a website and a real description, which is
    what the `brands` array is.
  * findability. also_known_as is not in the site's search haystack -
    index.html builds it from name + description + location + sector +
    category. So the brand names go into the survivor's one-line description
    too, which is what actually makes searching "recdesk" find Xplor today.

EVERY WRITE HERE GOES THROUGH admin.save_companies(), so admin_undo.py can take
any of it back. Each family writes at most three kinds of journal entry -
merge-prep, one merge per folded brand, merge-brands - and undoing them newest
first restores the family exactly.
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import admin        # noqa: E402
import build_board  # noqa: E402
import journal      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- the families
#
# Only `survivor`, `members` and the prep facts live here. Which members are
# actually safe to fold is decided by preflight() against the live board, not
# by this table, so a board that moves changes the answer without an edit.
#
# Every string in `prep` is either already in the record or is quoted from the
# family's own site in data/brand_research*.json. Nothing here is inferred.

FAMILIES = [
    {
        "key": "xplor",
        "survivor": "xplor-recreation",
        # rule: the record that already carries the working board and the
        # roles. xplor-recreation holds smartrecruiters/Xplor and 100 postings;
        # perfectmind names the same board and build_board already gives it to
        # xplor-recreation, so folding the other way would move 100 rows for
        # nothing.
        "why_survivor": "holds smartrecruiters/Xplor and its 100 live postings",
        "research": "brand_research.json",
        "members": [
            {"id": "perfectmind", "brand": "NextRec (formerly PerfectMind)",
             "research": "perfectmind"},
            {"id": "epact", "brand": "ePACT", "research": "epact"},
            {"id": "epact-network", "brand": "ePACT", "research": "epact",
             "same_brand_as": "epact"},
            {"id": "recdesk", "brand": "RecDesk", "research": "recdesk"},
            {"id": "vermont-systems", "brand": "Vermont Systems",
             "research": "vermont-systems"},
            {"id": "xplor-recreation-i-vermont-systems-recdesk-nextrec-epact",
             "conference_row": True, "research":
                 "xplor-recreation-i-vermont-systems-recdesk-nextrec-epact"},
        ],
        "prep": {
            # brand names are in the description because that, not
            # also_known_as, is what index.html searches
            "description": "Parks and recreation management software; brands "
                           "NextRec (formerly PerfectMind), Vermont Systems, "
                           "RecDesk and ePACT - exhibited at AB Show 2026, "
                           "NRPA 2026",
            # nextrec.com: "Copyright 2026 Xplor Technologies"; the PerfectMind
            # page: "Xplor Technologies is the parent company of PerfectMind".
            "parent": "Xplor Technologies",
            "researched": True,
            # ePACT Network was filed here and its own site sells to camps,
            # YMCAs, JCCs and sports associations, so the placement is carried
            # rather than dropped. The conference row's Suppliers & Services is
            # NOT carried: an exhibitor row's guessed category is not a
            # researched placement, and asserting it would file a software
            # vendor among suppliers.
            "also": [{"sector": "Parks & Rec",
                      "category": "Youth Sports & Leagues"}],
            "source_add": ["NRPA 2026"],
            "note": "Merged brand family. The NRPA 2026 exhibitor row named "
                    "Xplor Recreation, Vermont Systems, RecDesk, NextRec and "
                    "ePACT in one booth; its attribution is folded here and "
                    "the row is dropped. The two rosters disagree and both are "
                    "the family's own material: xplor.com/industries/"
                    "xplor-recreation/ lists Vermont Systems, RecDesk, NextRec "
                    "and CampBrain and omits ePACT, while the NRPA booth lists "
                    "ePACT and omits CampBrain. CampBrain is not in this "
                    "dataset and no record was invented for it.",
        },
    },
    {
        "key": "granicus",
        "survivor": "granicus",
        "why_survivor": "the parent; simpleviewinc.com/careers redirects to "
                        "granicus.com/careers and both sites say so",
        "research": "brand_research_granicus.json",
        "members": [
            {"id": "simpleview", "brand": "Simpleview (Granicus Destinations)",
             "research": "simpleview"},
        ],
        "prep": {
            "description": "Resident communications (govDelivery), meeting and "
                           "agenda management, digital services; destination "
                           "marketing as Simpleview, now Granicus Destinations "
                           "- exhibited at 3CMA 2026, GMIS International 2026, "
                           "APHSA 2026",
            "researched": True,
            # Granicus is General Gov / Citizen Services and Simpleview is
            # Parks & Rec / Events & Tourism. Without this the fold would take
            # 53 postings out of every tourism filter on the site.
            "also": [{"sector": "Parks & Rec", "category": "Events & Tourism"}],
            "note": "Merged brand family. Simpleview was acquired by Granicus "
                    "(press release 2026-09-05 on granicus.com) and Granicus's "
                    "destinations FAQ answers \"Is Simpleview now part of "
                    "Granicus?\" with \"Yes\". The solutions site stays at "
                    "simpleviewinc.com for now - \"The two websites will be "
                    "combined in the near future\" - so the brand keeps its "
                    "own website here.",
        },
    },
]

PREP_ACTION, BRANDS_ACTION = "merge-prep", "merge-brands"


# ------------------------------------------------------------------- plumbing

def use_data_dir(path: str | None) -> None:
    """Point admin AND journal at another data/ directory.

    journal has its own DATA, so both move or the before-images of a test run
    land in the real audit trail - the exact accident this repo spent a night
    recovering from.
    """
    if not path:
        return
    d = pathlib.Path(path).resolve()
    if not (d / "companies.json").exists():
        raise SystemExit(f"{d} has no companies.json")
    admin.DATA = d
    journal.DATA = d
    journal.LOG = d / "admin_journal.jsonl"


def by_id(companies: list) -> dict:
    return {c["id"]: c for c in companies}


def ats_key(c: dict) -> tuple | None:
    """The board a company points at, as build_board groups them."""
    a = c.get("ats") or {}
    kind, ref = a.get("type"), a.get("ref")
    if kind in (None, "unknown") or ref is None:
        return None
    return (kind, json.dumps(ref, sort_keys=True))


def load_board() -> dict:
    p = admin.DATA / "board.json"
    return json.loads(p.read_text()) if p.exists() else {"postings": []}


def postings_by_company(board: dict) -> dict:
    out: dict[str, int] = {}
    for p in board.get("postings", []):
        out[p.get("company_id")] = out.get(p.get("company_id"), 0) + 1
    return out


def research_for(fam: dict) -> dict:
    p = admin.DATA / fam["research"]
    if not p.exists():
        raise SystemExit(f"missing {p} - the brand research is the input here")
    return json.loads(p.read_text())


# ------------------------------------------------------------------ preflight

def preflight(fam: dict, companies: list, board: dict) -> tuple[list, list]:
    """Which members can be folded without orphaning a live posting.

    Simulates the part of build_board.py that decides which company a board's
    postings are filed under, over the file as it would look AFTER the fold.
    A board that still has a holder keeps its postings (they re-key onto the
    holder). A board with postings and no holder left is an orphan, and every
    member that held it is refused.

    Returns (folding, refused). refused rows carry the reason verbatim.
    """
    idx = by_id(companies)
    survivor = idx.get(fam["survivor"])
    if survivor is None:
        return [], [{"id": fam["survivor"], "why": "survivor is not in the file"}]

    counts = postings_by_company(board)
    # every board that currently produces postings, and who points at it
    live: dict[tuple, dict] = {}
    for c in companies:
        k = ats_key(c)
        if k is None:
            continue
        row = live.setdefault(k, {"holders": [], "postings": 0})
        row["holders"].append(c["id"])
        row["postings"] += counts.get(c["id"], 0)

    wanted = [m for m in fam["members"] if m["id"] in idx]
    missing = [{"id": m["id"], "why": "not in the file (already merged?)"}
               for m in fam["members"] if m["id"] not in idx]

    # what the survivor's board will be after prep + fold. The one case where
    # the survivor's own ats block changes is a same-board promotion, decided
    # below, so resolve that first.
    promote = same_board_promotion(survivor, wanted, idx, counts)
    survivor_after = copy.deepcopy(survivor)
    if promote:
        survivor_after["ats"] = copy.deepcopy(idx[promote["from"]]["ats"])

    dropping = {m["id"] for m in wanted}
    refused, blocked_boards = list(missing), {}
    for k, row in live.items():
        if row["postings"] == 0:
            continue
        left = [h for h in row["holders"]
                if h not in dropping or h == fam["survivor"]]
        if ats_key(survivor_after) == k and fam["survivor"] not in left:
            left.append(fam["survivor"])
        if left:
            continue
        blocked_boards[k] = row

    for k, row in blocked_boards.items():
        kind, ref = k[0], json.loads(k[1])
        url = build_board.board_url({"ats": {"type": kind, "ref": ref}})
        for h in row["holders"]:
            refused.append({
                "id": h,
                "why": f"folding it would leave {kind} board {ref!r} ({url}) "
                       f"with no company pointing at it, and build_board keys "
                       f"postings on the company id - {row['postings']} live "
                       f"posting(s) would vanish from the next build. A "
                       f"company record holds one ats block and this family "
                       f"has two boards.",
                "board": url, "postings": row["postings"],
            })

    refused_ids = {r["id"] for r in refused}
    folding = [m for m in wanted if m["id"] not in refused_ids]
    return folding, refused


def same_board_promotion(survivor: dict, members: list, idx: dict,
                         counts: dict) -> dict | None:
    """When a member reads the SAME board the survivor already names, better.

    Granicus is on file as `html` pointing at
    https://careers-granicus.icims.com/jobs/search?ss=1 and reads zero. Its
    Simpleview record is on file as `icims` ref careers-granicus, which
    build_board.board_url() resolves to that identical URL, and reads 53. That
    is not two boards, it is one board and a working fetcher for it, so the
    fetcher is promoted onto the survivor before the fold. Refusing to promote
    would orphan 53 real postings; promoting blind would be a guess. The gate
    is that both sides resolve to the same URL and only one side reads.
    """
    su = build_board.board_url(survivor)
    if su is None:
        return None
    for m in members:
        c = idx.get(m["id"])
        if c is None or ats_key(c) is None or ats_key(c) == ats_key(survivor):
            continue
        if build_board.board_url(c) != su:
            continue
        if counts.get(c["id"], 0) > 0 and counts.get(survivor["id"], 0) == 0:
            return {"from": c["id"], "url": su,
                    "was": survivor.get("ats"), "now": c.get("ats"),
                    "postings": counts[c["id"]],
                    # The hiring block is that board's last reading, and it
                    # comes with the board. Leaving the survivor's own -
                    # "Unknown, page scan found no listings" - on a record that
                    # now points at a board with 53 live roles would be a
                    # record contradicting itself until the next refresh, and
                    # thirteen role titles and urls would be thrown away for
                    # nothing. refresh.py overwrites this on its next run.
                    "hiring": c.get("hiring")}
    return None


# ----------------------------------------------------------------- the writes

def prep_survivor(fam: dict, promote: dict | None, folding: list,
                  apply: bool) -> list[str]:
    """One journalled write for everything the survivor gains around the fold.

    Kept separate from the merges so `admin_undo.py --undo` on it reverses the
    description, the placements and the ats promotion without disturbing the
    folds, and vice versa.
    """
    companies = admin.read_companies()
    idx = by_id(companies)
    s = idx[fam["survivor"]]
    prep, did = fam["prep"], []

    if promote:
        s["ats"] = copy.deepcopy(idx[promote["from"]]["ats"])
        did.append(f"ats {promote['was']} -> {promote['now']} "
                   f"(same board, {promote['postings']} postings)")
        h = promote.get("hiring")
        if h and (h.get("status") in admin.STATUSES):
            was = (s.get("hiring") or {}).get("status")
            s["hiring"] = copy.deepcopy(h)
            did.append(f"hiring {was!r} -> {h['status']!r} with "
                       f"{len(h.get('roles') or [])} role(s), the reading of "
                       f"that same board")
    for field in ("description", "parent", "researched"):
        if field in prep and s.get(field) != prep[field]:
            did.append(f"{field}: {s.get(field)!r} -> {prep[field]!r}")
            s[field] = prep[field]
    if prep.get("also"):
        placed = {(s["sector"], s["category"])}
        placed |= {(a.get("sector"), a.get("category")) for a in s.get("also") or []}
        keep = list(s.get("also") or [])
        for extra in prep["also"]:
            if (extra["sector"], extra["category"]) not in placed:
                keep.append(dict(extra))
                placed.add((extra["sector"], extra["category"]))
                did.append(f"also += {extra['sector']} / {extra['category']}")
        if keep:
            s["also"] = keep
    for extra in prep.get("source_add") or []:
        parts = [t.strip() for t in (s.get("source") or "").split(";") if t.strip()]
        if extra not in parts:
            parts.append(extra)
            s["source"] = "; ".join(parts)
            did.append(f"source += {extra}")
    if prep.get("note"):
        note = {"text": prep["note"], "by": "agent:merge_families",
                "on": admin.now()}
        notes = list(s.get("notes") or [])
        if not any((n.get("text") or "") == prep["note"] for n in notes):
            notes.append(note)
            s["notes"] = notes
            did.append("note recorded")

    if not did or not apply:
        return did
    err = admin.validate(companies)
    if err:
        raise SystemExit(f"refusing prep: {err}")
    bad = admin.save_companies(
        companies, PREP_ACTION, by="agent:merge_families",
        why=f"{fam['key']} family: survivor {fam['survivor']} gains what the "
            f"fold cannot inherit - " + "; ".join(did))
    if bad:
        raise SystemExit(f"refusing prep: {bad}")
    return did


def fold(fam: dict, member: dict, apply: bool) -> str:
    """The fold itself, through admin.act_merge - the one merge in the repo."""
    if not apply:
        return f"would merge {member['id']} into {fam['survivor']}"
    why = (f"{fam['key']} family: {member['id']} is a brand of "
           f"{fam['survivor']}, kept in its brands array with its own website "
           f"and description")
    if member.get("conference_row"):
        why = (f"{fam['key']} family: {member['id']} is a conference exhibitor "
               f"row naming several brands at once, not a company. Its "
               f"source_event attribution is folded into {fam['survivor']}.")
    res = admin.act_merge({"keep": fam["survivor"], "drop": member["id"],
                           "by": "agent:merge_families", "why": why})
    if res.get("error"):
        raise SystemExit(f"merge {member['id']} refused: {res['error']}")
    return res.get("message", "merged")


def was_facts(c: dict | None) -> dict | None:
    """The dropped record's own scalar facts, kept beside its brand entry."""
    if not c:
        return None
    out = {k: c.get(k) for k in
           ("year_founded", "location", "sector", "category", "source",
            "website", "vendor_type")
           if c.get(k) not in (None, "")}
    ats = c.get("ats") or {}
    if ats.get("type") not in (None, "unknown"):
        out["ats"] = ats
    return out or None


def write_brands(fam: dict, refused: list, before: dict, apply: bool) -> list:
    """The brands array: the family roster on the survivor's own record.

    A brand that was folded says so and names the id it used to have, which is
    what lets a reader walk back to the journal entry that folded it. A brand
    that is STILL its own record says that too, and names it - a roster that
    quietly omitted RecDesk because its board could not be folded would be a
    worse answer than one that says where RecDesk actually is.

    `before` is the members' records as they were BEFORE the fold, because by
    the time this runs the folded ones are no longer in the file.
    """
    research = research_for(fam)
    companies = admin.read_companies()
    idx = {**before, **by_id(companies)}
    s = by_id(companies)[fam["survivor"]]
    refused_ids = {r["id"] for r in refused}

    brands, seen = [], {}
    for m in fam["members"]:
        if m.get("conference_row"):
            continue          # not a company; rule 3
        key = m.get("same_brand_as") or m["id"]
        r = research.get(m["research"]) or {}
        if key in seen:
            was = was_facts(idx.get(m["id"]))
            if was:
                seen[key].setdefault("was_also_facts", {})[m["id"]] = was
            # two ids, one brand: epact and epact-network are one company on
            # one site. Record the second id rather than losing it.
            seen[key].setdefault("was_also", []).append(m["id"])
            continue
        entry = {
            "name": m["brand"],
            "website": r.get("website") or (idx.get(m["id"]) or {}).get("website"),
            "description": r.get("description"),
            "status": "separate record" if m["id"] in refused_ids else "folded",
            "was_id": m["id"],
            "words": r.get("words"),
            "confidence": r.get("confidence"),
            "sources": r.get("sources") or [],
            # an unread site is unreachable, not evidence of anything, and the
            # short description above says so in its own first sentence
            "unread": r.get("unread") or [],
            # A brand's own founding year, home town, market placement and how
            # it entered the dataset are facts about the BRAND, and a survivor
            # has room for exactly one of each. Inheritance keeps whichever the
            # survivor happened to be missing and silently drops the rest -
            # Xplor Recreation is on file as founded 1998, so PerfectMind's
            # 2000, ePACT's 2012 and Vermont Systems' 1985 all had nowhere to
            # go. They go here.
            "was": was_facts(idx.get(m["id"])),
        }
        if m["id"] in refused_ids:
            entry["record"] = m["id"]
            entry["why_separate"] = next(
                x["why"] for x in refused if x["id"] == m["id"])
            entry.pop("was_id")
        seen[key] = entry
        brands.append(entry)

    thin = [b["name"] for b in brands
            if not b["description"] or (b.get("words") or 0) < 250]
    if s.get("brands") == brands:
        return brands
    if not apply:
        return brands

    s["brands"] = brands
    # the parent's own long-form profile, from the same research
    own = research.get(fam["survivor"])
    if own and own.get("description"):
        s["profile"] = {"description": own["description"],
                        "sources": own.get("sources") or [],
                        "unread": own.get("unread") or [],
                        "confidence": own.get("confidence"),
                        "words": own.get("words")}
    # every brand name answers to this record from now on
    aliases = list(s.get("also_known_as") or [])
    for b in brands:
        if not any(admin.ident(b["name"]) == admin.ident(a) for a in aliases):
            aliases.append(b["name"])
    s["also_known_as"] = aliases

    err = admin.validate(companies)
    if err:
        raise SystemExit(f"refusing brands: {err}")
    bad = admin.save_companies(
        companies, BRANDS_ACTION, by="agent:merge_families",
        why=f"{fam['key']} family: {len(brands)} brand(s) recorded on "
            f"{fam['survivor']} with website and description"
            + (f"; SHORT: {', '.join(thin)}" if thin else ""))
    if bad:
        raise SystemExit(f"refusing brands: {bad}")
    return brands


# --------------------------------------------------------------------- report

def run(apply: bool, only: str | None) -> int:
    board = load_board()
    counts = postings_by_company(board)
    total_moved = 0
    for fam in FAMILIES:
        if only and fam["key"] != only:
            continue
        companies = admin.read_companies()
        idx = by_id(companies)
        print(f"\n=== {fam['key']}  survivor: {fam['survivor']} "
              f"({fam['why_survivor']})")
        if fam["survivor"] not in idx:
            print("  survivor is not in the file; skipping family")
            continue
        folding, refused = preflight(fam, companies, board)
        promote = same_board_promotion(
            idx[fam["survivor"]], folding, idx, counts)
        # the members exactly as they are now, so their own facts survive the
        # fold that removes them from the file
        before = {m["id"]: copy.deepcopy(idx[m["id"]])
                  for m in fam["members"] if m["id"] in idx}

        for r in refused:
            print(f"  REFUSED {r['id']}: {r['why']}")
        if promote:
            print(f"  promote ats on {fam['survivor']}: {promote['was']} -> "
                  f"{promote['now']}  (same board {promote['url']}, "
                  f"{promote['postings']} postings)")

        did = prep_survivor(fam, promote, folding, apply)
        for line in did:
            print(f"  prep: {line}")

        for m in folding:
            n = counts.get(m["id"], 0)
            total_moved += n
            tail = (f"   [{n} posting(s) re-key onto {fam['survivor']}]"
                    if n else "")
            print(f"  {fold(fam, m, apply)}{tail}")

        brands = write_brands(fam, refused, before, apply)
        for b in brands:
            print(f"  brand: {b['name']}  [{b['status']}]  "
                  f"{b.get('words')} words  {b['website']}")
    print(f"\n{'applied' if apply else 'dry run'}; "
          f"{total_moved} posting(s) change company id on the next build.")
    if not apply:
        print("nothing was written. Re-run with --apply.")
    else:
        print("undo any of it with:  python3 scripts/merge_families.py --show")
    return 0


def show() -> int:
    """What merged, and the exact command that takes each piece back."""
    rows = [r for r in journal.recent(200)
            if r.get("action") in ("merge", PREP_ACTION, BRANDS_ACTION)]
    if not rows:
        print("no family merges in the journal.")
        return 0
    print(f"{len(rows)} family-merge write(s), oldest first. Undo newest "
          f"first, or the older one will refuse as changed-since.\n")
    for r in rows:
        mark = "  (already undone)" if journal.undone(r["id"]) else ""
        print(f"{r['id']}  {r['at']}  {r['action']}  by {r['by']}  "
              f"{r['n']} record(s){mark}")
        for key, ch in (r.get("changes") or {}).items():
            if ch.get("after") is None:
                print(f"    - {key}   dropped")
            elif ch.get("before") is None:
                print(f"    + {key}   created")
            else:
                print(f"    ~ {key}   changed")
        if r.get("why"):
            print(f"    why: {r['why']}")
        print(f"    undo: python3 scripts/admin_undo.py --undo {r['id']}")
        print()
    print("A merge undo puts the dropped record back and reverses the "
          "survivor. Undo the newest entry first.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write it; without this the run is a dry run")
    ap.add_argument("--show", action="store_true",
                    help="what merged and how to take it back")
    ap.add_argument("--only", metavar="KEY",
                    help="one family: " + ", ".join(f["key"] for f in FAMILIES))
    ap.add_argument("--data", metavar="DIR",
                    help="run against a copy of data/ instead of the real one")
    a = ap.parse_args()
    use_data_dir(a.data)
    if a.show:
        return show()
    return run(a.apply, a.only)


if __name__ == "__main__":
    raise SystemExit(main())
