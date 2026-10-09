#!/usr/bin/env python3
"""Redraw data/board.json from the inputs on disk, without a crawl.

build_board.py is the crawler. It re-reads a few hundred third-party job
boards to redraw anything, so a description fixed in the admin, a company
moved to the right sector or a write-up landed reached the site the next
morning - or cost a 13-20 minute crawl of other people's servers to show
today. CLAUDE.md records four such crawls in one day for metadata-only edits.

This draws the board again from what is already here:

  data/companies.json   every company, as it is now
  data/board.json       the last crawl: its postings and what it read
  side files            news.json, manual.json, claims.json,
                        scope_decisions.json, discovery_log.json,
                        cities.json, conferences.json, assets/logos/

Every organization goes through build_board.org_record() - the builder the
crawl itself uses - so a redraw cannot describe a company differently from
the way a crawl would. The same goes for the manual merge, first_seen, the
narrowing filters, the counts, the totals, the conference rows and the
data/detail/ split: all of it is build_board's own code, called from here.

WHAT IS CARRIED FROM THE CRAWL, never re-derived:
  - the postings it read, and build_board.CRAWL_FIELDS on every organization
    (unreadable, roles_from_storage, enumerable, offtopic_dropped,
    federal_dropped, scan_lead) - they are what the board answered;
  - `generated`, which is the crawl date the site ages every posting by. A
    redraw that moved it would make week-old postings read as fresh;
  - the crawl's own counts: boards_read, unreadable, rendered.

WHAT CHANGES on the crawled rows: a company no longer on file loses its
rows; company, sector, category and also are re-stamped from the record;
the narrowing filters (federal, sled_only, a person's scope ruling) run
again and what they drop is added to that company's count. manual.json is
merged again exactly the way a crawl merges it, first_seen carried.

A SHARED BOARD WHOSE HOLDER LEFT THE FILE passes on, rows and all, the way
the next crawl passes it (see takeover()). The company that now holds it
alone gets the holder's crawled rows re-keyed under its own id by
build_board's opening_id()/posting_id(), and a company still following it
names whoever holds it now. No organization on a redrawn board names, in
shares_board_with, a company that is not on it.

WHAT WAITS FOR THE NIGHTLY CRAWL is named in the report and never guessed:
  - an ats type or board address that changed. The company keeps the board
    the crawl read - its rows, CRAWL_FIELDS and BOARD_FIELDS below - until
    a crawl reads the new one. Drawing the new board unread would tell a
    visitor "nothing open that we can see" about a board nobody opened.
  - a widening: sled_only turned off on a company that published its own
    rows, a shared board whose attribution moved between companies still on
    file, or a board taken over from a holder whose filters dropped roles
    the new holder's may keep. The rows those would bring back were never
    stored.
  - a company new since the crawl with a board on file - including one
    that points at a board another company already holds. It is LEFT OFF
    the redrawn board (no card, no rows, not in any count or attribution,
    its captures held back) until a crawl reads it, which is what the board
    did before redraws existed. A new company with no board on file is
    drawn: there is nothing of its to read.

Two edits cannot be seen from here and simply land with the next crawl: a
scope ruling that brings a DROPPED role back in (dropped rows are not
stored), and a same-type ref change on the boards whose address board.json
does not record (workday, gusto, adp and gem refs: board_url falls back to
the website there, so the old ref is nowhere to compare against).

IT NEVER WRITES data/history/, removed.json, render_attempts.json or any
crawl stat, and it never touches the network (see no_network). It writes
board.json - with a `redrawn` marker naming when, and a hash of the inputs
it was drawn from - and the data/detail/ files that changed.

  python3 scripts/quick_rebuild.py           dry run: which orgs would change, and why
  python3 scripts/quick_rebuild.py --write   redraw board.json and data/detail/
  python3 scripts/quick_rebuild.py --check   exit 1 unless board.json is drawn
                                             from the inputs on disk
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import hashlib
import json
import os
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import build_board as bb  # noqa: E402

# How an organization describes the BOARD the crawl read. Carried, with its
# rows and CRAWL_FIELDS, for a company whose board waits for the crawl: the
# old board is still what its rows came from, and re-deriving these from
# the new ats would describe a board nobody has read. Keeping them is also
# what keeps the company waiting on the next redraw, instead of the change
# looking settled the moment one redraw has run.
BOARD_FIELDS = ("ats", "ats_ranks", "board_url", "no_board_on_file", "probe",
                "sled_only", "shares_board_with", "board_owner_unverified")

# The two BOARD_FIELDS that say who holds a shared board. Never carried from
# a holder that has left the file: they are today's attribution.
ATTRIBUTION = ("shares_board_with", "board_owner_unverified")

# What a crawled row takes from its company record, re-stamped on a redraw.
STAMPED = ("company", "sector", "category", "also")

# The side files a redraw reads, hashed into the `redrawn` marker.
HASHED = ("news.json", "manual.json", "claims.json", "scope_decisions.json",
          "discovery_log.json", "cities.json", "conferences.json",
          "schema.json", "hq_jobs.json", "link_health.json")


@contextlib.contextmanager
def no_network():
    """Refuse every way out of this process for as long as a redraw runs.

    NO NETWORK IS A PROPERTY OF THE RUN, NOT A PROMISE IN A DOCSTRING.
    build_board imports ats and ats imports requests, so the fetchers are
    loaded in this process whether a redraw wants them or not. Inside this
    block every socket connect and every fetcher raises instead, so a later
    edit that reaches for one fails loudly here rather than quietly
    crawling somebody's board.
    """
    def refuse(*_a, **_k):
        raise RuntimeError("quick_rebuild does not touch the network - "
                           "reading boards is build_board.py's job")

    shut = [(socket.socket, "connect"), (socket.socket, "connect_ex"),
            (socket, "create_connection"), (socket, "getaddrinfo"),
            (bb.ats, "fetch"), (bb.ats, "fetch_html_titles"),
            (bb.ats, "_get"), (bb.ats, "_post_json")]
    kept = [(obj, name, getattr(obj, name)) for obj, name in shut]
    for obj, name, _ in kept:
        setattr(obj, name, refuse)
    try:
        yield
    finally:
        for obj, name, orig in kept:
            setattr(obj, name, orig)


def inputs_hash(companies: list) -> str:
    """sha256 of every input a redraw reads, less what only a crawl honours.

    `hiring` and `ats` are left out of each company: hiring is refresh.py's
    crawl output, and a changed ats waits for the crawl. Hashing either would
    call a board stale for an edit no redraw can draw.
    """
    h = hashlib.sha256()
    kept = [{k: v for k, v in c.items() if k not in ("hiring", "ats")}
            for c in companies]
    h.update(b"companies.json\n")
    h.update(json.dumps(kept, sort_keys=True, ensure_ascii=False).encode())
    for name in HASHED:
        p = bb.DATA / name
        h.update(f"\n{name}\n".encode())
        h.update(p.read_bytes() if p.exists() else b"(absent)")
    h.update(b"\nassets/logos\n")
    h.update(json.dumps(bb.logos_manifest()).encode())
    return h.hexdigest()


def never_crawled(was: dict | None) -> bool:
    """No crawl has drawn this organization: it is new, or a redraw drew it.

    A crawl always writes `enumerable` as True or False; only NO_CRAWL leaves
    it None. So a company a redraw added keeps saying so on every later
    redraw, until a crawl reads it.
    """
    return was is None or was.get("enumerable") is None


def has_board(c: dict) -> bool:
    """A board on file: what build_board.read_board() would go and read."""
    a = c.get("ats") or {}
    return a.get("type") not in (None, "unknown") and a.get("ref") is not None


def left_off(c: dict, was: dict | None) -> str | None:
    """Why this company stays OFF the redrawn board until a crawl reads it, or None.

    A company no crawl has drawn, with a board on file: new since the crawl,
    or new and pointing at a board another company already holds. Nobody has
    read that board under its name. Drawn anyway, it would carry zero roles
    and `enumerable` None, which index.html and build_site.py both read as a
    board that answered - "they have nothing open that we can see", about a
    board nobody opened. So it gets no card at all until the nightly crawl
    draws one, which is what happened before redraws existed. A new company
    with NO board on file is drawn: it has nothing to read.
    """
    if not never_crawled(was) or not has_board(c):
        return None
    return (f"new since the crawl, and its {c['ats']['type']} board has not "
            f"been read - left off the board until a crawl reads it")


def waits_for_crawl(c: dict, was: dict | None, ctx: dict, on_file: set) -> str | None:
    """Why this company cannot be redrawn without reading its board, or None.

    `on_file` is every company id in companies.json. A company no crawl has
    drawn gets here only with no board on file (left_off() takes the rest),
    and has nothing to wait for.
    """
    kind = (c.get("ats") or {}).get("type")
    no_board = not has_board(c)
    if never_crawled(was):
        return None
    if was.get("ats") != kind:
        return f"ats changed from {was.get('ats')} to {kind}"
    if bool(was.get("no_board_on_file")) != no_board:
        return "a board went on or off file"
    url = bb.board_url(c)
    # A board_url that is only the website on both sides is a website edit,
    # not a new board: board_url() falls back to it for refs it cannot spell.
    if was.get("board_url") != url and not (
            was.get("board_url") == was.get("website") and url == c.get("website")):
        return f"board address changed from {was.get('board_url')} to {url}"
    held_by = was.get("shares_board_with")
    # A company that followed somebody else's board published no rows of its
    # own, so turning its filter off brings nothing back.
    if was.get("sled_only") and not c.get("sled_only") and not held_by:
        return "sled_only turned off: the roles it dropped were never stored"
    if held_by and held_by not in on_file:
        # ITS HOLDER HAS LEFT THE FILE. Holding the board alone now, it takes
        # the holder's rows (takeover(), which says when that cannot be
        # exact); still following, it names whoever holds the board now. A
        # follower's record is the same whoever that is - no rows, and crawl
        # fields no fetch decides - so neither waits on this alone.
        return None
    if (held_by != ctx["owns"].get(c["id"])
            or bool(was.get("board_owner_unverified")) != (c["id"] in ctx["unowned"])):
        return "the shared-board attribution moved"
    return None


def takeover(c: dict, gone: dict, rows: list, scope: dict,
             generated: str) -> tuple[list, dict | None, str | None]:
    """The board `c` holds alone now that `gone`, its holder at the crawl, left the file.

    What the next crawl does: it reads that board under c's id, so the rows
    the holder published come back as c's - the same requisitions, keyed by
    build_board's own opening_id() and posting_id() for c, put through c's
    own narrowing filters, and dated `generated`, because to
    carry_first_seen() a new id is a new row and the crawl dates it the day
    it reads it. `rows` are the holder's crawled rows (manual rows are not
    among them; they belong to their own company id).

    Returns (rows, crawl, why). `crawl` is the CRAWL_FIELDS a crawl would
    write for c, or None where nothing of the holder's read can be handed
    over. `why` names what a redraw cannot know, or is None when this IS the
    crawl's drawing.
    """
    cid, gid = c["id"], gone["id"]
    if gone.get("roles_from_storage"):
        # Its rows were ITS OWN stored roles, which the ownership guard passed
        # for the holder's name and domain. The board gave nothing; a crawl
        # would promote c's own stored roles, or none.
        return [], None, (f"took over {gone.get('name')}'s board, whose roles "
                          f"came from {gone.get('name')}'s own stored roles, "
                          f"not the board")
    crawl = {k: gone.get(k) for k in bb.CRAWL_FIELDS}
    answered = bool(rows or gone.get("offtopic_dropped") or gone.get("federal_dropped"))
    if not answered:
        # The board published nothing: it answered empty, failed, or held only
        # junk titles. A crawl then reaches for c's OWN stored roles and scan
        # lead, which a redraw can only match when c has none to reach for.
        if bb._stored_roles_as_jobs(c):
            return [], None, (f"took over {gone.get('name')}'s board, which "
                              f"published nothing; a crawl would publish "
                              f"{c['name']}'s own stored roles")
        lead = bb._scan_lead(c)
        emptied = bool(gone.get("unreadable") or gone.get("scan_lead")
                       or (gone.get("ats") == "html" and gone.get("enumerable") is False))
        if lead and not emptied:
            return [], None, (f"took over {gone.get('name')}'s board, which "
                              f"published nothing; whether {c['name']}'s scan "
                              f"lead shows turns on what the board answered")
        crawl["scan_lead"] = lead
        return [], crawl, None

    sled_only = bool(c.get("sled_only"))
    fallback = gone.get("board_url")
    moved, drop, why = [], collections.Counter(), None
    for p in rows:
        q = dict(p)
        title, loc = q["title"], q.get("location") or ""
        oid = bb.opening_id(cid, title)
        rid = bb.posting_id(cid, title, q.get("url"), loc)
        was_oid = bb.opening_id(gid, title)
        if (bb.posting_id(gid, title, q.get("url"), loc) != q["id"]
                and q["id"].startswith(was_oid + "::")):
            # safe_url() rewrote this url after the crawl hashed it (a space,
            # a quote). posting_id() hashes the url and the location only, so
            # the crawl's own hash stands and only the company half moves.
            rid = oid + q["id"][len(was_oid):]
        if fallback and q.get("url") == bb.safe_url(fallback) and bb.board_url(c) != fallback:
            # A row with no url of its own took the holder's board_url, which
            # is its WEBSITE for the refs board_url() cannot spell. A crawl
            # gives it c's.
            why = (f"took over {gone.get('name')}'s board, and its rows with no "
                   f"url of their own point at {fallback}")
        q.update(id=rid, opening_id=oid, company_id=cid, first_seen=generated)
        if bb.roles.not_a_listing(title, c["name"]):
            continue                    # not an opening; the crawl counts none
        v = bb.out_of_scope(title, rid, oid, scope, sled_only)
        if v:
            drop[v] += 1
        else:
            moved.append(q)
    crawl["federal_dropped"] = ((crawl["federal_dropped"] or 0) + drop["federal"]) or None
    crawl["offtopic_dropped"] = ((crawl["offtopic_dropped"] or 0) + drop["offtopic"]) or None

    # WHAT THE HOLDER'S FILTERS DROPPED WAS NEVER STORED. c's filters only
    # drop at least as much when both carry sled_only and no ruling of the
    # holder's vetoed a role; a ruling keyed to c's ids could let one back.
    vetoed = any(k.startswith(gid + "::") and not (v or {}).get("in_scope")
                 for k, v in scope.items())
    ruled = any(k.startswith(cid + "::") for k in scope)
    if (gone.get("offtopic_dropped")
            and not (sled_only and gone.get("sled_only") and not vetoed)) \
            or ((gone.get("offtopic_dropped") or gone.get("federal_dropped")) and ruled):
        why = (f"took over {gone.get('name')}'s board: roles its filters "
               f"dropped may be {c['name']}'s to keep, and were never stored")
    return moved, crawl, why


def redraw() -> dict:
    """Everything a redraw would write, and what it did to get there. Writes nothing."""
    companies = json.loads((bb.DATA / "companies.json").read_text())
    prev = json.loads((bb.DATA / "board.json").read_text())
    scope_path = bb.DATA / "scope_decisions.json"
    scope = json.loads(scope_path.read_text()) if scope_path.exists() else {}
    man = bb.load_manual()
    # The same people's rulings a crawl honours (build_board.ruled_out): a
    # company ruled out of scope is not on file as far as the board knows.
    companies, man, ruled = bb.ruled_out(companies, man)
    prev_orgs = {o["id"]: o for o in prev.get("organizations", [])}
    on_file = {c["id"] for c in companies}

    # LEFT OFF until a crawl reads them: see left_off(). Taken out before
    # anything is attributed, so no company on the board names one of these
    # as the holder of a board it shares, and their captures wait with them.
    off = {c["id"]: why for c in companies
           if (why := left_off(c, prev_orgs.get(c["id"])))}
    drawn = [c for c in companies if c["id"] not in off]
    drawn_ids = {c["id"] for c in drawn}
    if man and off:
        man = {**man, "postings": [mp for mp in man.get("postings", [])
                                   if mp.get("company_id") not in off]}
    ctx = bb.run_context(drawn, man)

    # The crawl's rows by company, in board order. Manual and SLED HQ rows are
    # not here: they are merged again from manual.json and hq_jobs.json below,
    # the way a crawl does it, so each has one path in and one set of filters.
    crawled: dict[str, list] = collections.defaultdict(list)
    for p in prev.get("postings", []):
        if p.get("source") not in ("manual", "hq"):
            crawled[p.get("company_id")].append(p)

    waits: dict[str, str] = {}
    narrowed: dict[str, int] = {}
    handed: dict[str, dict] = {}
    restamped = 0
    postings: list[dict] = []
    orgs: list[dict] = []
    for c in drawn:
        cid = c["id"]
        was = prev_orgs.get(cid)
        why = waits_for_crawl(c, was, ctx, on_file)
        # A company new since the crawl has no rows: nobody has read its board.
        rows = [] if was is None else [dict(p) for p in crawled.get(cid, [])]
        crawl = (dict(bb.NO_CRAWL) if was is None
                 else {k: was.get(k) for k in bb.CRAWL_FIELDS})
        # THE BOARD OF A HOLDER THAT LEFT THE FILE, now held by this company
        # alone: its rows and its read pass here, as they do on the next crawl.
        took = None
        gid = (was or {}).get("shares_board_with")
        if (not why and gid and gid not in on_file and gid in prev_orgs
                and ctx["owns"].get(cid) is None):
            moved, got, why = takeover(c, prev_orgs[gid], crawled.get(gid, []),
                                       scope, prev["generated"])
            handed[cid] = {"from": gid, "rows": len(crawled.get(gid, [])),
                           "kept": len(moved), "passed": got is not None}
            if got is not None:
                rows, crawl, took = moved, got, prev_orgs[gid]
                if len(moved) < len(crawled.get(gid, [])):
                    narrowed[cid] = len(crawled.get(gid, [])) - len(moved)
        for p in rows:
            before = [p.get(k) for k in STAMPED]
            p["company"] = c["name"]
            p["sector"], p["category"] = c["sector"], c["category"]
            p["also"] = c.get("also") or None
            restamped += before != [p.get(k) for k in STAMPED]
        # NOT AN OPENING AT ALL (roles.not_a_listing), for every company -
        # including one waiting for the crawl, whose rows otherwise pass
        # through untouched (second review, 2026-10-08). The crawl drops
        # these before counting anything, so they join no count here.
        listed = [p for p in rows if not bb.roles.not_a_listing(p["title"], c["name"])]
        if len(listed) < len(rows):
            narrowed[cid] = narrowed.get(cid, 0) + len(rows) - len(listed)
        rows = listed
        if why:
            waits[cid] = why
        elif rows and took is None:
            # THE NARROWING FILTERS, AGAIN, on rows the crawl already kept.
            # Only ever narrower: a row they drop now is counted with the
            # ones the crawl dropped, and a row they would let back in was
            # never stored - that is a widening, and it waits.
            sled_only = bool(c.get("sled_only"))
            kept, drop = [], collections.Counter()
            for p in rows:
                v = bb.out_of_scope(p["title"], p["id"], p["opening_id"], scope, sled_only)
                if v:
                    drop[v] += 1
                else:
                    kept.append(p)
            if drop:
                crawl["federal_dropped"] = ((crawl["federal_dropped"] or 0)
                                            + drop["federal"]) or None
                crawl["offtopic_dropped"] = ((crawl["offtopic_dropped"] or 0)
                                             + drop["offtopic"]) or None
                narrowed[cid] = narrowed.get(cid, 0) + sum(drop.values())
            rows = kept
        postings.extend(rows)
        o = bb.org_record(c, drawn, ctx, crawl)
        if why and not never_crawled(was):
            # The board the crawl read: this company's own, or - taken over -
            # the holder's, read under the holder's filters. Who holds it is
            # today's attribution either way; the holder is gone.
            for k in BOARD_FIELDS:
                if took is None or k not in ATTRIBUTION:
                    o[k] = (took or was).get(k)
            if o.get("shares_board_with") and o["shares_board_with"] not in drawn_ids:
                # NEVER NAME A COMPANY THAT IS NOT ON THIS BOARD. The one the
                # crawl named has left the file; say who holds it now.
                o["shares_board_with"] = ctx["owns"].get(cid)
                o["board_owner_unverified"] = (cid in ctx["unowned"]) or None
        orgs.append(o)
    passed = {h["from"] for h in handed.values() if h["passed"]}
    gone_rows = {i: len(r) for i, r in crawled.items()
                 if i not in on_file and i not in passed}

    manual_count, manual_dupes = bb.merge_manual(postings, man, orgs)
    bb.merge_hq(postings, bb.load_hq(), companies)
    nights, read_from = bb.history_first_seen()
    bb.carry_first_seen(postings, prev.get("postings", []), nights)
    bb.fill_geography(postings)
    unique = bb.drop_identical(postings)
    identical = len(postings) - len(unique)
    postings = unique
    bb.mark_read_since(orgs, postings, read_from)
    bb.mark_seller_debut(orgs, postings, prev["generated"],
                         bb.sellers_before(prev["generated"]))
    groups = bb.count_openings(postings, orgs)
    # `generated` and the crawl's counts are the crawl's, never this run's.
    # The companies left off are left out of every count too: the board is
    # drawn as if they were not on file yet, which is what it said before.
    payload = bb.board_payload(prev["generated"], orgs, postings, groups, drawn,
                               {"unreadable": prev.get("unreadable"),
                                "rendered": prev.get("rendered")},
                               manual_count, manual_dupes)
    payload["coverage"] = bb.coverage_split(drawn, orgs, ctx["discovery"])
    payload["boards_read"] = prev.get("boards_read")
    bodies = bb.detail_bodies(orgs)
    bb.strip_details(orgs)

    changed, new, removed = detail_changes(bodies)
    same = (_without_mark(payload) == _without_mark(prev))
    return {"payload": payload, "bodies": bodies, "prev": prev, "ruled": ruled,
            "companies": companies, "waits": waits, "narrowed": narrowed,
            "off": off, "handed": handed,
            "restamped": restamped, "gone_rows": gone_rows,
            "identical_dropped": identical,
            "detail": {"changed": changed, "new": new, "removed": removed},
            "inputs": inputs_hash(companies),
            "drawn": same and not (changed or new or removed)}


def _without_mark(board: dict) -> dict:
    return {k: v for k, v in board.items() if k != "redrawn"}


def detail_changes(bodies: dict[str, str]) -> tuple[list, list, list]:
    """(changed, new, removed) company ids in data/detail/ against `bodies`."""
    d = bb.DATA / "detail"
    on_disk = {f.stem: f for f in d.glob("*.json")} if d.exists() else {}
    changed = [i for i, b in bodies.items()
               if i in on_disk and on_disk[i].read_text() != b]
    new = [i for i in bodies if i not in on_disk]
    removed = sorted(i for i in on_disk if i not in bodies)
    return changed, new, removed


def _names(ids: list, names: dict, most: int = 6) -> str:
    shown = [names.get(i, i) for i in ids[:most]]
    more = len(ids) - len(shown)
    return ", ".join(shown) + (f" +{more} more" if more > 0 else "")


def report(plan: dict) -> list[str]:
    """What a redraw changes, grouped by field, and what waits for the crawl."""
    payload, prev = plan["payload"], plan["prev"]
    names = {c["id"]: c["name"] for c in plan["companies"]}
    names.update({o["id"]: o["name"] for o in prev.get("organizations", [])
                  if o["id"] not in names})
    old = {o["id"]: o for o in prev.get("organizations", [])}
    out = [f"board.json: crawled {prev.get('generated')}, "
           f"{len(prev.get('organizations', []))} organizations, "
           f"{len(prev.get('postings', []))} postings"]
    mark = prev.get("redrawn")
    if isinstance(mark, dict):
        same = "the inputs on disk" if mark.get("inputs") == plan["inputs"] \
            else "inputs that have changed since"
        out.append(f"  last redrawn {mark.get('at')} from {same}")

    by_field: dict[str, list] = collections.defaultdict(list)
    new_ids = []
    for o in payload["organizations"]:
        p = old.get(o["id"])
        if p is None:
            new_ids.append(o["id"])
            continue
        if json.dumps(o) == json.dumps(p):
            continue
        fields = sorted(k for k in set(o) | set(p) if o.get(k) != p.get(k))
        for k in fields or ["(key order only)"]:
            by_field[k].append(o["id"])
    touched = {i for ids in by_field.values() for i in ids}
    on_file = {c["id"] for c in plan["companies"]}
    # A COMPANY A PERSON RULED OUT IS STILL ON FILE. It is said as a ruling,
    # not as a record somebody deleted (review, 2026-10-07: Concourse read as
    # "no longer on file").
    ruled = set(plan.get("ruled") or [])
    gone = [i for i in old if i not in on_file and i not in ruled]
    out.append(f"  {len(touched)} organization(s) would change"
               + (":" if touched else ""))
    for k in sorted(by_field, key=lambda k: (-len(by_field[k]), k)):
        out.append(f"    {k:<24} {len(by_field[k]):>4}  {_names(by_field[k], names)}")
    if new_ids:
        out.append(f"  {len(new_ids)} new since the crawl with no board on file, "
                   f"drawn with no crawl and no roles: {_names(new_ids, names)}")
    if ruled & set(old):
        out.append(f"  {len(ruled & set(old))} ruled out of scope by a person, not "
                   f"drawn: {_names(sorted(ruled & set(old)), names)}")
    if gone:
        out.append(f"  {len(gone)} no longer on file, dropped with their rows "
                   f"unless another company took over their board: "
                   f"{_names(gone, names)}")
    if plan["handed"]:
        out.append(f"  {len(plan['handed'])} took over a shared board whose holder "
                   f"is no longer on file:")
        for cid, h in plan["handed"].items():
            got = (f"its {h['rows']} crawled row(s) re-keyed under {cid}, "
                   f"{h['kept']} kept by its filters" if h["passed"]
                   else "nothing handed over (see waits)")
            out.append(f"    {names.get(cid, cid)} ({cid}) from "
                       f"{names.get(h['from'], h['from'])}: {got}")

    before, after = len(prev.get("postings", [])), len(payload["postings"])
    bits = []
    ruled_rows = sum(n for c, n in plan["gone_rows"].items() if c in ruled)
    if plan["gone_rows"] and sum(plan["gone_rows"].values()) > ruled_rows:
        bits.append(f"{sum(plan['gone_rows'].values()) - ruled_rows} of companies "
                    f"no longer on file")
    if ruled_rows:
        bits.append(f"{ruled_rows} of companies ruled out of scope")
    if plan["narrowed"]:
        bits.append(f"{sum(plan['narrowed'].values())} now out of scope or not "
                    f"an opening at "
                    f"{_names(sorted(plan['narrowed']), names)}")
    if plan["identical_dropped"]:
        bits.append(f"{plan['identical_dropped']} byte-identical duplicates")
    out.append(f"  postings {before} -> {after}"
               + (f" (dropped: {'; '.join(bits)})" if bits else "")
               + (f"; {plan['restamped']} re-stamped with their company's "
                  f"current name or placement" if plan["restamped"] else ""))
    d = plan["detail"]
    if d["changed"] or d["new"] or d["removed"]:
        out.append(f"  data/detail/: {len(d['changed'])} changed, {len(d['new'])} "
                   f"new, {len(d['removed'])} removed")
    top = []
    for k in payload:
        if k in ("organizations", "postings"):
            continue
        if payload[k] != prev.get(k):
            top.append(k)
        elif json.dumps(payload[k]) != json.dumps(prev.get(k)):
            top.append(f"{k} (order only)")
    if top:
        out.append(f"  top level: {', '.join(top)}")

    if plan["waits"]:
        out.append(f"  waits for the nightly crawl ({len(plan['waits'])}) - its "
                   f"board is drawn as the last crawl left it, rows and all:")
        for cid, why in plan["waits"].items():
            out.append(f"    {names.get(cid, cid)} ({cid}): {why}")
    if plan["off"]:
        out.append(f"  left off the board, waiting for the nightly crawl "
                   f"({len(plan['off'])}) - no card, no rows, no count:")
        for cid, why in plan["off"].items():
            out.append(f"    {names.get(cid, cid)} ({cid}): {why}")
    out.append("  board.json is drawn from the current inputs" if plan["drawn"]
               else "  board.json is NOT drawn from the current inputs")
    return out


def write(plan: dict) -> None:
    """board.json in build_board's own serialization, plus the changed detail files."""
    payload, prev = plan["payload"], plan["prev"]
    mark = prev.get("redrawn")
    unchanged = _without_mark(payload) == _without_mark(prev)
    if not (unchanged and isinstance(mark, dict) and mark.get("inputs") == plan["inputs"]):
        # A NEW STAMP ONLY WHEN SOMETHING MOVED. Rewriting `at` on a redraw
        # that changed nothing would put a diff in git for every run.
        mark = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "inputs": plan["inputs"]}
    payload["redrawn"] = mark
    bb.write_details(plan["bodies"], bb.DATA / "detail")
    path = bb.DATA / "board.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1) + "\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Redraw data/board.json from the inputs on disk, with no crawl.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="write board.json and the data/detail/ files that changed")
    mode.add_argument("--check", action="store_true",
                      help="exit 1 unless board.json is drawn from the current inputs")
    a = ap.parse_args(argv)

    if not (bb.DATA / "board.json").exists():
        print("no data/board.json to redraw: a redraw carries a crawl's postings, "
              "so the first board has to come from build_board.py")
        return 1
    with no_network():
        plan = redraw()
        for line in report(plan):
            print(line)
        if a.check:
            return 0 if plan["drawn"] else 1
        if not a.write:
            print("\n(dry run, nothing written: --write redraws board.json "
                  "and data/detail/)")
            return 0
        write(plan)
    print(f"\nwrote data/board.json (generated {plan['payload']['generated']} "
          f"kept) and {len(plan['detail']['changed']) + len(plan['detail']['new'])} "
          f"data/detail/ file(s), removed {len(plan['detail']['removed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
