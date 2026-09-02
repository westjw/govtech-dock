#!/usr/bin/env python3
"""Local admin backend: the queues that need a person, in one place.

Everything automated here has a residue. Discovery leaves 884 companies with no
board on file and 16 boards it cannot read. The website guesser leaves 106 names
too generic to guess a domain from, plus short names where the page really does
say the word and only a person can tell a Samsung ETF from a govtech company.
The role classifier leaves 386 titles that name a rank with no function. Slug
mismatches leave an acquisition queue. None of that is a bug to be fixed; it is
the part of the work that needs judgment, and until now it lived in five
different CLI flags and three JSON files nobody opens.

The public site is deliberately static and cannot write. This is the other half:
a stdlib HTTP server bound to loopback that serves admin.html and exposes a small
JSON API over data/. Every write is validated against the same invariants
selftest.py enforces and lands atomically, so a bad edit is refused rather than
half-applied. Nothing here touches data/hiring_history/ - snapshots are the audit
trail and change only through refresh.py.

  python scripts/admin.py [--port 8787]
  then open http://127.0.0.1:8787
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import csv
import datetime as dt
import http.server
import io
import ipaddress
import json
import os
import pathlib
import re
import secrets
import socket
import socketserver
import sys
import tempfile
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ats  # noqa: E402
import add_company    # noqa: E402
import discover_ats   # noqa: E402
import find_websites  # noqa: E402
import roles          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Derived from the fetchers that exist, never hand-listed. The two drifted:
# ats.py grew paylocity and oracle fetchers while this set kept the old
# fourteen, so discovery could find a board the validator then refused. A
# type is legal exactly when something can read it, plus "unknown", which
# means nothing has looked yet.
ATS_TYPES = set(ats.FETCHERS) | {"unknown"}
STATUSES = {"Yes", "Sales (non-AE)", "None found", "Unknown"}

# A company id is a filename in every direction it travels: assets/logos/<id>.png
# is written from it, and the site, the exporter and the logo fetcher all build
# paths out of it. So a path is a legal string but not a legal id, and the file
# is where that has to be said, because an id can enter it from intake, from a
# merge, from a ruling, or from an outside submission. All 2,108 ids on file
# already match; this refuses the ones nobody meant to allow.
#
# \Z rather than $, and the difference is the whole point of the comment above:
# $ also matches immediately before a trailing newline, so "tyler-tech\n" was a
# legal id as far as this said. Nothing reaches it with one today - every
# writer strips - but the invariant is "an id is a safe filename", and a
# trailing newline is not, so the pattern has to actually say it.
ID_OK = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")

# Words that carry no identity, so two records differing only by these are the
# same company: "Miovision" and "Miovision Technologies Inc." are one vendor.
LEGAL = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|group|holdings|"
                   r"technologies|technology|software|systems|solutions|company)\b", re.I)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# "Novotx, An Accela Company" and "TouchNet, A Global Payments Company" are how
# an acquired product gets renamed on its new owner's site. Both are on the
# board twice - once under the plain name, once with the tail - and neither
# pair could reach the duplicates queue, because the tail survives LEGAL and
# makes the two names different strings. The parent's name inside the tail is
# not part of this company's identity; it is a sentence about who owns it.
ACQUIRED_BY = re.compile(
    r",?\s+(?:an?|the)\s+[A-Za-z0-9&.\'-]+(?:\s+[A-Za-z0-9&.\'-]+){0,3}"
    r"\s+(?:company|business|brand)\b.*$", re.I)


def ident(name: str) -> str:
    return norm(LEGAL.sub("", ACQUIRED_BY.sub("", name or "")))


def now() -> str:
    """The stamp every ruling carries, alongside its date.

    A date answers "how many this month" and cannot answer "what did this
    sitting change" - a sitting is not a day, and two of them share one
    often. The date stays because everything already reads it; this is the
    grain the end-of-shift receipt needs, and it can only be recorded at the
    moment of the ruling, never reconstructed afterwards.
    """
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read(name: str, default):
    p = DATA / name
    if not p.exists():
        return default
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return default
    # A present-but-wrong-shaped file once crashed every GET endpoint and the
    # server's own startup: submissions.json as '{}' met subs["items"]. The
    # shape contract belongs here, where every reader inherits it.
    if not isinstance(data, type(default)):
        return default
    if isinstance(default, dict):
        for k, v in default.items():
            if k not in data or not isinstance(data[k], type(v)):
                data[k] = v
    return data


def write_atomic(name: str, payload) -> None:
    """Write via a temp file in the same directory, then replace.

    A partial companies.json is worse than a stale one: the site, the exporter
    and every script read it on the next run, and a truncated write during a
    refresh would take the whole dataset out.
    """
    p = DATA / name
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


# --- every companies.json write keeps a before-image ---------------------
#
# The journal existed for a day before anything called it, and in that day a
# rename bug wrote a marketing tagline over a company's name with no way back.
# So the journal is not an opt-in helper any more: read_companies() remembers
# what it handed out, and save_companies() diffs against that automatically.
# An action cannot forget to record, because it never had to remember.
_LAST_COMPANIES: list | None = None


def read_companies() -> list:
    global _LAST_COMPANIES
    data = read("companies.json", [])
    _LAST_COMPANIES = json.loads(json.dumps(data))   # a copy nobody can mutate
    return data


def save_companies(companies: list, action: str, why: str = "",
                   by: str = "owner", force: bool = False) -> str | None:
    """Journal, then write. Returns a refusal message, or None on success.

    PASS `by`. It defaults to "owner" because most writes are his, and that
    default is a trap for every write that is not: eight actions here called
    this with the action name alone, so an agent's patch, an extension's
    capture and a script's identity ruling were all recorded as rulings the
    owner made. That is not a cosmetic mislabel - the journal is what
    admin_undo reads, what re-attribution works from, and what will one day
    say which labels a classifier can trust. The 86 agent writes in
    companies.json had to be re-attributed by hand for exactly this reason,
    and re-attribution is a thing somebody has to remember to do.
    """
    import journal
    # A SAVE THAT NEVER READ HAS NOTHING TO DIFF AGAINST, and the old fallback
    # made that failure silent in the worst possible way: `before = companies`
    # is the AFTER state, so journal.record() sees no change, writes an entry
    # with an empty diff, and admin_undo.py later restores nothing while
    # reporting success. The write still lands. You would only find out by
    # trying to undo something and watching it not come back.
    #
    # Every caller today reads first. This is here for the seven pipeline
    # scripts that still write companies.json directly and will be converted:
    # the conversion must not be able to half-happen.
    if _LAST_COMPANIES is None:
        return ("refused: save_companies() was called without reading first, so "
                "there is no before-state to journal. Call admin.read_companies() "
                "and modify what it returns.")
    before = _LAST_COMPANIES
    _eid, refusal = journal.record("companies.json", before, companies,
                                   action, by, why, force)
    if refusal:
        return refusal
    write_atomic("companies.json", companies)
    return None


def save_decisions(name: str, after, action: str, why: str = "",
                   by: str = "owner", force: bool = False) -> str | None:
    """Journal a decision-file write, then write. Refusal message, or None.

    THE RULE ONLY EVER COVERED companies.json. journal.py motivates itself
    with "one click on 'All out' writes a ruling for 108 companies and all 108
    pass every check we have" - and that click wrote vendor_scope_decisions
    .json through write_atomic with no journal entry at all. So the exact
    scenario the journal exists for was the one it did not cover: no
    before-image, nothing for --undo, and --reopen with nothing to reopen,
    which matters most here because a scope ruling is never re-asked.

    It also brings BLAST and the runaway guard to these files for the first
    time. A bulk ruling over 25 records now has to be confirmed.

    The before-state is re-read from disk rather than passed in, because every
    caller mutates the dict it read. The write has not happened yet, so disk
    still holds the before-state. journal.snapshot already handles the dict
    shape - "the decision files are already dicts" is its own comment - so
    nothing about the journal needed changing to cover them.
    """
    import journal
    before = read(name, {})
    _eid, refusal = journal.record(name, before, after, action, by, why, force)
    if refusal:
        return refusal
    write_atomic(name, after)
    return None


def validate(companies: list) -> str | None:
    """The invariants selftest.py enforces, checked before a write lands.

    Returning the first failure rather than raising keeps the browser's error
    readable, and refusing the whole write keeps the file consistent.
    """
    schema = read("schema.json", {"sectors": []})
    cats = {s["name"]: set(s["categories"]) for s in schema["sectors"]}
    seen = set()
    for c in companies:
        who = c.get("name", "?")
        for f in ("id", "name", "sector", "category", "ats", "hiring"):
            if c.get(f) in (None, ""):
                return f"{who}: missing {f}"
        if not isinstance(c["id"], str) or not ID_OK.match(c["id"]):
            return f"{who}: id {c['id']!r} is not a slug"
        if c["id"] in seen:
            return f"duplicate id {c['id']}"
        seen.add(c["id"])
        if c["sector"] not in cats:
            return f"{who}: unknown sector {c['sector']}"
        if c["category"] not in cats[c["sector"]]:
            return f"{who}: category {c['category']} is not in {c['sector']}"
        # A vendor can genuinely belong in several places: Tyler sells court
        # case management, municipal ERP and public-safety records, and
        # filing it under one of those hides it from people looking for the
        # other two. sector/category stay the PRIMARY home - the xlsx puts
        # each company on exactly one tab, and a canonical answer to "where
        # does this live" is worth keeping - and `also` carries the rest.
        placements = {(c["sector"], c["category"])}
        for extra in c.get("also") or []:
            s2, c2 = extra.get("sector"), extra.get("category")
            if s2 not in cats:
                return f"{who}: also names unknown sector {s2}"
            if c2 not in cats[s2]:
                return f"{who}: also: {c2} is not a category of {s2}"
            if (s2, c2) in placements:
                return f"{who}: filed twice under {s2} / {c2}"
            placements.add((s2, c2))
        if (c.get("ats") or {}).get("type") not in ATS_TYPES:
            return f"{who}: bad ats type {(c.get('ats') or {}).get('type')}"
        if (c.get("hiring") or {}).get("status") not in STATUSES:
            return f"{who}: bad hiring status"
    return None


def dismissed() -> dict:
    return read("admin_dismissed.json", {})


def dismissal_records():
    """(queue, key, record) for every dismissal, whichever shape wrote it.

    Two shapes have written admin_dismissed.json: dismiss() nests
    {queue: {key: rec}}, and act_place's keep-branch wrote flat
    "miscategorized:<id>" keys at the top level. Every metric consumer read
    ONLY the flat shape - so every judgment recorded through the generic
    dismiss buttons left its queue but never counted as a ruling: absent from
    rulings_by_queue, absent from the day and sitting counters, and its typed
    why invisible to the why-coverage meter, directly contradicting dismiss()'s
    own comment that the meter counts any non-empty why. Care was being taken
    and reported as not taken.

    This is the one reader. A top-level key with a colon is flat legacy; one
    without is a queue holding nested records. All writers now nest.
    """
    for key, rec in read("admin_dismissed.json", {}).items():
        if not isinstance(rec, dict):
            continue
        if ":" in key:
            q, k = key.split(":", 1)
            yield q, k, rec
        else:
            for k, r in rec.items():
                if isinstance(r, dict):
                    yield key, k, r


def dismiss(queue: str, key: str, why: str, by: str = "owner",
            saw: dict | None = None) -> str | None:
    # `why` is null when nobody typed one, never a stand-in. The why-coverage
    # meter counts any non-empty why as a reason, so a placeholder here - the
    # page used to send the literal "dismissed in admin" - reports care that
    # nobody took, which is the one thing that meter exists to make visible.
    d = dismissed()
    rec = {"on": dt.date.today().isoformat(), "at": now(),
           "by": (by or "owner").strip(),
           "why": (why or "").strip() or None}
    if saw is not None:
        rec["saw"] = saw
    d.setdefault(queue, {})[key] = rec
    # Journalled like every other ruling. A dismissal removes a row from a
    # queue and is never re-asked, which is the same permanence a scope
    # ruling has, so it earns the same before-image and the same undo.
    return save_decisions("admin_dismissed.json", d, "dismiss", why=why, by=by)


def is_dismissed(queue: str, key: str) -> bool:
    return key in dismissed().get(queue, {})


# ---------------------------------------------------------------- queues

def q_duplicates(companies, board) -> list:
    """Records that are probably one company.

    Grouped by a normalised name, PLUS a signal that catches what a name
    cannot: two records whose logo file is byte-identical and was fetched from
    one of their own domains. That is not a coincidence to be explained away -
    it means one of these sites served the other's brand asset.

    It found 25 pairs no name test could reach, because the names really are
    different strings: policeapp / policeapp-com, sagitec / sagitec-solutions,
    zoll-medical / zoll-data-systems, revize / revize-government-websites.
    Every one of them is a company counted twice in a total this project
    quotes at strangers.

    Some of them will turn out to be a parent and its division rather than a
    duplicate - infor / infor-public-sector, xylem / xylem-vue - which is
    exactly why they arrive here as a question instead of a merge.
    """
    g = collections.defaultdict(list)
    for c in companies:
        k = ident(c["name"])
        if k:
            g[k].append(c)
    by_id = {c["id"]: c for c in companies}

    # THE SAME WEBSITE. Two records pointing at one domain are one company
    # under two spellings, or a parent and a division - either way somebody
    # has to say which, and neither the name test nor the logo test finds
    # them all. 39 domains on file are shared: revize / revize-government-
    # websites, eagleview / eagleview-technologies, aurigo-software /
    # aurigo-software-technologies-inc. Free to compute and independent of
    # both other signals, which is what makes it worth having.
    dom = collections.defaultdict(list)
    for c in companies:
        w = (c.get("website") or "").strip()
        if not w:
            continue
        d = re.sub(r"^https?://(www\.)?", "", w).rstrip("/").split("/")[0].lower()
        # a shared HOST is only a duplicate signal when it is the company's own
        # site; two firms on one platform are not one firm
        if d and d not in ("sites.google.com", "wixsite.com", "squarespace.com",
                           "godaddysites.com", "linkedin.com", "facebook.com"):
            dom[d].append(c)
    for d, v in dom.items():
        if len(v) < 2:
            continue
        key = "site:" + d
        if not any(ident(m["name"]) and len(g.get(ident(m["name"]), [])) > 1
                   for m in v):
            g[key] = v

    try:
        import acquisitions
        for cid, f in acquisitions._logo_families().items():
            if not f.get("same_company"):
                continue          # a real acquisition, not a duplicate record
            pair = sorted({cid, f["parent"]})
            key = "logo:" + "+".join(pair)
            members = [by_id[i] for i in pair if i in by_id]
            if len(members) == 2 and not any(
                    ident(m["name"]) and len(g.get(ident(m["name"]), [])) > 1
                    for m in members):
                g[key] = members
    except Exception:
        pass          # a signal that fails must not take the queue with it
    out = []
    posts = collections.Counter(p["company_id"] for p in board.get("postings", []))
    for k, v in g.items():
        if len(v) < 2 or is_dismissed("duplicates", k):
            continue
        out.append({"key": k, "members": [{
            "id": c["id"], "name": c["name"], "sector": c["sector"],
            "category": c["category"], "website": c.get("website"),
            "ats": (c.get("ats") or {}).get("type"),
            "postings": posts.get(c["id"], 0),
            "description": c.get("description")} for c in v]})
    # the pair most likely to be a real duplicate is the one where only one side
    # carries data, because merging it loses nothing
    out.sort(key=lambda r: -sum(1 for m in r["members"] if not m["website"]))
    return out


def _unblocks(name: str, companies, board) -> int | None:
    """Decisions in OTHER queues that resolving this one could make moot.

    Only the duplicates queue has this property today: every other queue asks
    about a record on its own terms, but a duplicate pair asks whether one of
    the two records should stop existing, and everything queued against the
    loser is then work nobody needed to do.

    Returns None rather than 0 for queues where the question does not apply,
    so the page can say nothing rather than say "unblocks 0", which reads as
    a claim that this queue holds nothing up.
    """
    if name != "duplicates":
        return None
    try:
        dup = {m["id"] for r in q_duplicates(companies, board)
               for m in r.get("members", [])}
        if not dup:
            return 0
        n = 0
        for other, fn in QUEUES.items():
            if other == "duplicates":
                continue
            try:
                rows = fn(companies, board)
            except Exception:
                continue
            n += sum(1 for r in rows
                     if isinstance(r, dict) and r.get("id") in dup)
        return n
    except Exception:
        return None          # a nicety must never take the queue with it


def q_websites(companies, board) -> list:
    """Companies with no website on file, and which of them are unanswerable here.

    Eleven of the fifty in this queue are one half of a duplicate pair whose
    OTHER half already carries the website: Avolve and Avolve Software, Novotx
    and "Novotx, An Accela Company", Oracle and Oracle Corporation. Researching
    a website for those is work with no possible right answer - the answer is a
    merge, and it lives in a different queue. Left unmarked, this queue quietly
    spends the owner's attention on eleven questions that cannot be settled by
    answering them.

    So the twin is named on the row. Nothing is hidden: a pair can turn out to
    be a parent and its division rather than one company, in which case the
    website question is real again, and that call is not this function's to
    make.
    """
    by_ident = {}
    for c in companies:
        k = ident(c["name"])
        if k:
            by_ident.setdefault(k, []).append(c)

    out = []
    for c in companies:
        if c.get("website") or is_dismissed("websites", c["id"]):
            continue
        twins = [o for o in by_ident.get(ident(c["name"]), [])
                 if o["id"] != c["id"]]
        # only worth flagging when the twin HAS what this row is missing
        has_site = [o for o in twins if (o.get("website") or "").strip()]
        out.append({"id": c["id"], "name": c["name"], "sector": c["sector"],
                    "category": c["category"], "description": c.get("description"),
                    "also_known_as": c.get("also_known_as") or [],
                    "events": _events(c.get("description")),
                    "same_name_as": [{"id": o["id"], "name": o["name"],
                                      "website": o.get("website")}
                                     for o in (has_site or twins)] or None,
                    "tier": 1 if c["sector"] in ("General Gov", "Public Works", "Parks & Rec")
                            else 2})
    return out


# "blocked at" is a PREFIX, not the full literal. discover_ats writes two
# shapes: "blocked at the door (HTTP 403)" for a homepage refusal and
# "blocked at /careers (HTTP 403)" for a refusal mid-crawl. Matching only the
# first filed twenty mid-crawl 403s as "probed, nothing found" - a refusal
# reported as evidence of absence, which is the one mistake this project's
# founding rule exists to prevent. The record's own retry_soon flag is the
# ground truth (discover_ats sets it exactly when the fetch was refused) and
# _probe honours it first.
BLOCKED_MARKERS = ("blocked at", "could not fetch", "gave up after")


def _events(description: str) -> list:
    """The conferences a company exhibited at, from its description note.

    For a company with no findable board this is often the most useful fact
    on the row: it says where a person has stood within arm's reach of them,
    and it is how the owner will actually work the list - by floor, with the
    capture extension, not alphabetically.
    """
    m = re.search(r"exhibited at ([^;.\n]+)", description or "")
    return [t.strip() for t in m.group(1).split(",")] if m else []


def _probe(cid: str) -> dict:
    log = read("discovery_log.json", {})
    e = log.get(cid)
    if not e:
        return {"state": "unprobed", "note": None, "on": None}
    note = e.get("note") or ""
    state = ("blocked" if e.get("retry_soon")
             or any(mk in note for mk in BLOCKED_MARKERS)
             else "none-found")
    return {"state": state, "note": note, "on": e.get("on")}


# A company somebody just worked by hand should not be back at the top of the
# list to work by hand. manual.json already records who was checked and when;
# nothing read it, so the capture worklist re-offered every company the moment
# after it was captured, which is how a list stops feeling worth working.
#
# THIRTY DAYS, and the number is not new: manual.py::STALE_DAYS has meant
# exactly this since the worklist was written - a hand check is good for a
# month and then the jobs have moved on. Reused rather than re-decided, so
# there is one answer to "how long is a check good for" instead of two.
#
# NOT PERMANENT, deliberately. A company that disappears the moment it is
# touched is a company nobody ever revisits, and postings change. This hides
# it for a month; it comes back on its own.
CAPTURE_FRESH_DAYS = 30


def _checked_recently(man: dict | None = None, today=None) -> set:
    """Companies a person looked at by hand inside the window.

    A check with `found: null` does NOT count. That is the shape written when
    somebody looked at the WRONG PAGE - airitcareers.co.uk is a British MSP
    and not Air-Transport IT Services of Orlando - and the record is still
    genuinely unchecked. Treating it as done would be the tool believing its
    own mistake, which is what the null is there to prevent.
    """
    if man is None:
        try:
            man = json.loads((DATA / "manual.json").read_text())
        except Exception:                               # noqa: BLE001
            return set()
    today = today or dt.date.today()
    out = set()
    for cid, chk in (man.get("checks") or {}).items():
        if not isinstance(chk, dict):
            continue
        if chk.get("found", True) is None:
            continue
        on = chk.get("checked_on")
        try:
            age = (today - dt.date.fromisoformat(str(on))).days
        except Exception:                               # noqa: BLE001
            continue
        if 0 <= age < CAPTURE_FRESH_DAYS:
            out.add(cid)
    return out


def _board_rows(companies, board):
    orgs = {o["id"]: o for o in board.get("organizations", [])}
    done = _checked_recently()
    for c in companies:
        if c["id"] in done:
            continue
        o = orgs.get(c["id"], {})
        kind = (c.get("ats") or {}).get("type")
        no_board = kind in (None, "unknown")
        if not (no_board or o.get("unreadable")):
            continue
        if is_dismissed("boards", c["id"]):
            continue
        # A company with a scan lead belongs in Warm leads, not here. Checked
        # when this was written: the two sets are already disjoint, because a
        # scan lead has an html careers page on file and this queue wants
        # companies with no board at all. This is the guard for the day that
        # stops being true, not a fix for an overlap that existed.
        if o.get("scan_lead"):
            continue
        pr = _probe(c["id"])
        yield {"id": c["id"], "name": c["name"], "sector": c["sector"],
               "website": c.get("website"), "ats": kind,
               "why": "board unreadable" if o.get("unreadable")
                      else "no board on file",
               "note": c.get("ats_note"),
               "events": _events(c.get("description")),
               "probe": pr["state"], "probe_note": pr["note"],
               "probed_on": pr["on"],
               "tier": o.get("tier") or 3}


def q_boards(companies, board) -> list:
    """Probed, and nothing found: the capture-extension worklist.

    These are companies whose sites were read and yielded no ATS and no
    careers page a fetcher can use. Most genuinely have no public board -
    they hire on LinkedIn or by email - so the fix is a person pasting an
    address they found by hand, or standing on a conference floor. Blocked
    and never-probed companies live in their own queues; mixing them in here
    made this list look endless and taught nobody to open it.
    """
    out = [r for r in _board_rows(companies, board)
           if r["probe"] != "blocked" and r["website"]]
    # by conference first: this list is worked by floor, with the extension,
    # not alphabetically. Companies seen at more floors sort earlier because
    # the owner is likelier to be within reach of them again.
    out.sort(key=lambda r: (r["tier"], -len(r["events"]),
                            r["events"][0] if r["events"] else "~", r["name"]))
    return out


def q_blocked(companies, board) -> list:
    """Blocked or unreachable when probed: a retry pile, not a worklist.

    A 403 or a timeout is not evidence of anything except that the fetcher
    was turned away. These re-probe themselves after seven days; the button
    here is for retrying one NOW, when you think the block was transient or
    you just fixed the website field.
    """
    out = [r for r in _board_rows(companies, board) if r["probe"] == "blocked"]
    out.sort(key=lambda r: (r["probed_on"] or "", r["name"]))
    return out


def q_placement(companies, board) -> list:
    """Companies whose description disagrees with the sector they are filed in.

    guess_sector is the same routine intake uses, so this asks the question
    intake would have asked if these had come in through the front door.
    """
    out = []
    for c in companies:
        desc = c.get("description")
        if not desc or is_dismissed("placement", c["id"]):
            continue
        try:
            sec, cat, conf, why = add_company.guess_sector(
                f"{c['name']} {desc}".lower())
        except Exception:
            continue
        # Descriptions here are one line, so "high" confidence is close to
        # unreachable and asking for it flagged nothing at all. The sharper
        # question is whether the sector it is filed under scores anywhere: if
        # the description contains no vocabulary from its own sector but plenty
        # from another, the filing is the thing to doubt.
        if not sec or sec == c["sector"] or conf == "low":
            continue
        if any(line.startswith(c["sector"] + " /") for line in (why or [])):
            continue
        out.append({"id": c["id"], "name": c["name"], "description": desc,
                    "current": {"sector": c["sector"], "category": c["category"]},
                    "suggested": {"sector": sec, "category": cat},
                    "why": why})
    return out


def q_unclassified(companies, board) -> list:
    over = read("family_overrides.json", {})
    seen, out = set(), []
    for p in board.get("postings", []):
        if p.get("family") != "other":
            continue
        t = p["title"]
        if t in over or t in seen or is_dismissed("unclassified", t):
            continue
        seen.add(t)
        out.append({"title": t, "company": p["company"], "url": p.get("url"),
                    "location": p.get("location")})
    return out


# Hosts that run job boards for other people. A board on one of these belongs
# to a vendor doing its job, not to a rival whose postings might be mistaken
# for this company's.
ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "bamboohr.com",
    "myworkdayjobs.com", "workday.com", "recruitee.com", "breezy.hr", "gusto.com",
    "smartrecruiters.com", "jazzhr.com", "paylocity.com", "icims.com",
    "rippling.com", "applytojob.com", "taleo.net", "successfactors.com",
    "jobscore.com", "comeet.com", "teamtailor.com", "trinethire.com",
    "applicantpro.com", "hirehive.com", "hrmdirect.com", "ttcportals.com",
    "jobvite.com", "dayforcehcm.com", "adp.com", "ycombinator.com",
    "gnahiring.com", "trakstar.com", "oraclecloud.com", "paycomonline.net",
)


def q_acquisitions(companies, board) -> list:
    """Boards that look like they belong to somebody else.

    Two signals now, because one was missing the plainest case there is.

    The sweep's own suspicions, from ats_suspects.json - a slug that reads but
    does not match the company's name.

    AND: a board hosted on the domain of another company ALREADY ON THIS BOARD.
    That is not a guess about a slug, it is two records this file already holds
    pointing at one place. Twenty-two of them: Cartegraph's board is
    opengov.com, Dedrone's is axon.com, ResourceX's is tylertech.com. Sixteen
    appeared in no queue at all, so nobody was ever asked - and five of them
    were meanwhile telling the public site they were hiring an AE, on the
    strength of a page scan of the parent's careers page.

    An ATS host does not count, or every real board on the site becomes a
    suspect: greenhouse.io is a filing cabinet, not a rival.
    """
    sus = read("ats_suspects.json", {})
    items = sus.get("suspects", sus) if isinstance(sus, dict) else sus
    if isinstance(items, dict):
        items = [{"id": k, **v} for k, v in items.items()]
    items = list(items)
    seen = {i.get("id") for i in items}

    def _root(u):
        h = (urllib.parse.urlparse(u or "").hostname or "").lower()
        h = h.replace("www.", "")
        parts = h.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else h

    sites = {}
    for c in companies:
        r = _root(c.get("website"))
        if r:
            sites.setdefault(r, c["name"])
    for c in companies:
        if c["id"] in seen:
            continue
        ref = (c.get("ats") or {}).get("ref")
        if not isinstance(ref, str) or not ref.startswith("http"):
            continue
        site, host = _root(c.get("website")), _root(ref)
        if not site or not host or site == host or host in ATS_HOSTS:
            continue
        owner = sites.get(host)
        if not owner or owner == c["name"]:
            continue
        items.append({
            "id": c["id"], "ats": c.get("ats"),
            "note": (f"this board is on {host}, which is {owner}'s own domain. "
                     f"Their postings are not necessarily {c['name']}'s"),
            # The strongest signal in this queue, and it needs its own name:
            # `slug` means a string looked odd, this means two records already
            # in this file point at one domain.
            "strength": "domain",
            "on": dt.date.today().isoformat()})
    items = [i for i in items if not is_dismissed("acquisitions", i.get("id", ""))]
    return [_acquisition_row(i, companies) for i in items]


# THE QUEUE WAS RENDERING BLANK, AND NOBODY COULD SEE THAT IT WAS.
#
# admin.html's RENDER.acquisitions reads `name`, `strength`, `says`,
# `postings_on_that_board`, `titles` and `board_calls_itself` - the shape
# scripts/acquisitions.py builds. This function returned `id`, `ats`, `note`
# and `on`. Nothing mapped one to the other, so every one of the 82 rows drew
# with a SLUG as its heading, the fixed band "Only the slug looks odd -
# weakest, most of these are nothing", and an EMPTY evidence line - including
# the 22 rows whose note says outright whose domain the board is on.
#
# A queue that shows a slug and no evidence is a queue nobody can rule, and
# data/acquisition_rulings.json has never been written once. That is the whole
# explanation. It is not that the question is hard; it is that the page was not
# asking it.
#
# Mapped here rather than in admin.html because the renderer's shape is the
# intended one and the other producer already emits it - making the server
# agree is one direction of change, teaching the page a second shape is two.
def _acquisition_row(item: dict, companies: list) -> dict:
    c = next((x for x in companies if x.get("id") == item.get("id")), None)
    note = item.get("note") or ""
    return {**item,
            "name": (c or {}).get("name") or item.get("id"),
            "website": (c or {}).get("website"),
            # `says` is what the renderer prints as the evidence line. The note
            # IS the evidence; it simply had no field to arrive in.
            "says": item.get("says") or note,
            "strength": item.get("strength") or "slug"}


def q_review(companies, board) -> list:
    rev = read("website_review.json", {})
    return [{"id": k, **v} for k, v in rev.items()
            if not is_dismissed("review", k)]


def q_scope(companies, board) -> list:
    """Postings a filter kept but could not confirm belong on this board.

    The live case is federal. A Federal Civilian Sales AE is selling technology
    to government, and it is not state and local, and no regex settles which of
    those this board is about. Rather than let a pattern decide quietly in
    either direction, the posting stays visible and asks.
    """
    decided = read("scope_decisions.json", {})
    out = []
    for p in board.get("postings", []):
        if not p.get("scope_pending") or p["id"] in decided:
            continue
        out.append({"id": p["id"], "title": p["title"], "company": p["company"],
                    "company_id": p["company_id"], "url": p.get("url"),
                    "location": p.get("location"), "family": p.get("family"),
                    "quota": p.get("quota_carrying"),
                    "sector": p.get("sector"), "category": p.get("category")})
    out.sort(key=lambda r: (r["company"], r["title"]))
    return out


def act_scope(body: dict) -> dict:
    """Rule one posting in or out of scope. The ruling beats the pattern.

    Stored by posting id, which is company::title, so a role that reposts under
    the same title keeps the ruling and is never asked about twice.
    """
    pid, keep = body.get("id"), body.get("in_scope")
    if not pid or keep is None:
        return {"error": "need a posting id and a decision"}
    d = read("scope_decisions.json", {})
    d[pid] = {"in_scope": bool(keep), "on": dt.date.today().isoformat(),
              "at": now(),
              "why": (body.get("why") or "").strip() or None}
    bad = save_decisions("scope_decisions.json", d, "scope",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"),
                         force=bool(body.get("force")))
    if bad:
        return {"error": bad}
    return {"ok": True,
            "message": f"{'kept on the board' if keep else 'out of scope'}; "
                       f"takes effect on the next build"}


def act_scope_all(body: dict) -> dict:
    """Rule every pending posting matching a pattern at once.

    Six near-identical 'Federal Civilian Sales' rows is not six decisions, it
    is one decision asked six times.
    """
    keep, pat = body.get("in_scope"), (body.get("match") or "").strip()
    if keep is None or not pat:
        return {"error": "need a decision and something to match on"}
    board = read("board.json", {})
    d = read("scope_decisions.json", {})
    n = 0
    for p in board.get("postings", []):
        if p.get("scope_pending") and p["id"] not in d \
                and re.search(re.escape(pat), p["title"], re.I):
            d[p["id"]] = {"in_scope": bool(keep), "on": dt.date.today().isoformat(),
                          "at": now(),
                          "why": f"bulk ruling on {pat!r}"}
            n += 1
    bad = save_decisions("scope_decisions.json", d, "scope-all",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"),
                         force=bool(body.get("force")))
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{n} posting(s) ruled "
                                   f"{'in' if keep else 'out of'} scope"}


def act_suggest(body: dict) -> dict:
    """Record an argument about the LOGIC, attached to the row that provoked it.

    Every queue shows its reasoning to the person ruling - "the domain matches
    this name", "this is a holding page", "slug does not match". When that
    reasoning is wrong, the owner is the only one who ever sees it, at the one
    moment he has the context to say why, and there has never been anywhere to
    put it. So it goes in a ruling, or it goes nowhere.

    Three of those were wrong in one sitting. The rename panel offered to call
    Conduent Transportation "Conduent" without noticing the name was taken.
    The parked detector convicted wrangler.ai on the words "coming soon". The
    no-board panel said "send it to the acquisitions queue" for a queue no
    button can add to.

    This writes NOTHING to companies.json and nothing to the public board. It
    is a note to whoever fixes the logic, carrying what the panel claimed and
    what the owner says is wrong with it - because "the parked check is too
    eager" a week later is not the same as it with the page in front of you.
    """
    cid = (body.get("id") or "").strip()
    argument = (body.get("argument") or "").strip()
    if not argument:
        return {"error": "nothing to record - say what the logic got wrong"}
    notes = read("logic_notes.json", {"notes": []})
    notes["notes"].append({
        "queue": (body.get("queue") or "").strip() or None,
        "id": cid or None,
        "name": (body.get("name") or "").strip() or None,
        # what the panel actually told him, so the note is readable later
        # without reconstructing the state that produced it
        "saw": (body.get("saw") or "").strip()[:600] or None,
        "argument": argument[:2000],
        "on": dt.date.today().isoformat(),
        "at": now(),
        "by": (body.get("by") or "owner"),
    })
    write_atomic("logic_notes.json", notes)
    n = len(notes["notes"])
    return {"ok": True,
            "message": f"noted \u2014 {n} argument{'s' if n != 1 else ''} on file. "
                       f"Nothing on the board changed."}


def act_posts_at(body: dict) -> dict:
    """Record that a company advertises somewhere we cannot enumerate.

    The no-board queue had two outcomes - paste an ATS address, or dismiss -
    so a company advertising every opening on LinkedIn was recorded exactly
    like one that hires by word of mouth. Those are opposite facts, and
    collapsing them makes the public card imply nobody is hiring at a company
    that is advertising openly. Nothing ever contradicts it, because "we found
    nothing" is indistinguishable from "we did not look in the right place".
    """
    import posts_at as _pa
    cid = (body.get("id") or "").strip()
    where = (body.get("where") or "").strip()
    url = (body.get("url") or "").strip()
    owner = (body.get("owner") or "").strip()
    bad = _pa.check(where, url, owner)
    if bad:
        return {"error": bad}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    c["posts_at"] = _pa.build(where, url, body.get("by") or "owner",
                              body.get("note") or "", owner)
    # board_owner is the field the public card already reads to warn "some of
    # these may not be their roles". posts_at records the ruling; this makes
    # the existing rendering pick it up without a second decision.
    if where == "parent" and owner:
        c["board_owner"] = owner
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "posts-at", why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return {"ok": True,
            "message": f"recorded: {c['name']} advertises on {_pa.label(where)}. "
                       f"Their card now links there instead of saying no board "
                       f"was found."}


def act_identity_ruling(body: dict) -> dict:
    """Record that the identity warning was wrong (or right), and act on it.

    "It literally says the name" is a fact with a structured home. Writing it
    into also_known_as makes identifies() pass for this company from now on,
    so the correction pays for itself immediately instead of only becoming a
    statistic. The label is kept as well, because how often this check is
    wrong is a number nobody currently has.
    """
    import identity_labels
    cid = (body.get("id") or "").strip()
    verdict = (body.get("verdict") or "").strip()
    said = (body.get("said_name") or "").strip()
    if verdict not in ("same", "different"):
        return {"error": "verdict must be same or different"}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}

    added = None
    if verdict == "same":
        # "It is them" should not cost you a transcription. Read the page and
        # work out what it calls itself; only ask when it genuinely does not
        # say, and then offer what was found rather than an empty box.
        if not said:
            try:
                r = add_company.fetch(body.get("url") or c.get("website") or "")
                html = r[0] if isinstance(r, tuple) else r
            except Exception:
                html = ""
            base = ""
            try:
                base = (body.get("url") or "").split("//", 1)[1].split("/")[0] \
                    .replace("www.", "").rsplit(".", 1)[0]
            except (IndexError, AttributeError):
                pass
            cands = find_websites.name_candidates(html, base)
            fresh = [x for x in cands
                     if x["name"].lower() != c["name"].lower()]
            if fresh and fresh[0]["score"] >= 80:
                said = fresh[0]["name"]
            elif fresh:
                return {"ok": True, "ask": True, "candidates": fresh[:4],
                        "message": "Which name does it use?"}
            elif cands:
                return {"ok": True, "already": True,
                        "message": (f"The page calls itself \u201c{cands[0]['name']}\u201d, "
                                    f"which is already the name on file. Nothing "
                                    f"to add - if the check still warns, the page "
                                    f"may genuinely not say it in a place we read.")}
            else:
                return {"error": "The page does not state a name we can read. "
                                 "Type the one it uses."}
        if not find_websites.plausible_name(said):
            return {"error": f"{said[:40]!r} is not a company name - a name is a "
                             f"few words. Paste the name, not the sentence."}
        if said.lower() == c["name"].lower():
            return {"error": "that is already the stored name - if the panel "
                             "still warns, the page may genuinely not say it"}
        aka = set(c.get("also_known_as") or [])
        if said not in aka:
            aka.add(said)
            c["also_known_as"] = sorted(aka)
            added = said
        err = validate(companies)
        if err:
            return {"error": err}
        bad = save_companies(companies, "identity-ruling",
                             why=(body.get("why") or ""),
                             by=(body.get("by") or "owner"))
        if bad:
            return {"error": bad}

    identity_labels.record(cid, c["name"], body.get("page_says") or "",
                           body.get("url") or "", verdict, said,
                           body.get("by") or "owner")
    if verdict == "different":
        return {"ok": True, "message": f"noted: that page is not {c['name']}. "
                                       f"The warning was right, and now we know it."}
    return {"ok": True, "recheck": True,
            "message": (f"recorded: {c['name']} also goes by \u201c{added}\u201d. "
                        f"The check will recognise that from now on.")
                       if added else "already recorded"}


def founded_provenance() -> dict:
    """Which founding years on file were written by something automated.

    On 2026-08-24 a build agent wrote 86 of them into companies.json while
    testing the belt. The owner's call was keep and requeue, not delete: they
    are mostly right - Workday 2005, Tanium 2007, DailyPay 2015, Harris 1976
    and Auror 2012 all check out - and throwing away 86 correct years to
    punish how they arrived would cost the board more than it gained. But a
    year nobody confirmed is exactly the "invented reads as checked" case this
    queue's own docstring refuses, so they cannot simply sit there either.

    Provenance is HARVESTED from the journal and then KEPT here, because
    journal.py is a ring buffer - KEEP=500 - and the day it prunes past these
    entries, an unconfirmed year would quietly become an unremarkable one. A
    fact about where data came from must not have a shorter life than the
    data.
    """
    import journal
    store = read("founded_provenance.json", {})
    grew = False
    for r in journal._entries():
        if r.get("action") not in ("set-founded", "confirm-founded"):
            continue
        if _is_person(r.get("by")):
            continue
        for cid, ch in (r.get("changes") or {}).items():
            year = ((ch.get("after") or {}).get("year_founded"))
            if not year or cid in store:
                continue
            store[cid] = {"year": year, "by": r.get("by"), "at": r.get("at"),
                          "why": r.get("why") or "", "confirmed": None}
            grew = True
    if grew:
        write_atomic("founded_provenance.json", store)
    return store


def _q_board_proposals(companies, board) -> list:
    import board_proposals
    return board_proposals.q_board_proposals(companies, board)


def q_founded(companies, board) -> list:
    """Founding years that need a person: unconfirmed guesses first, then blanks.

    Two different jobs wear one tab, and the order between them is the whole
    argument. A BLANK year is honest - the card simply does not say. A year an
    agent wrote and nobody checked is an assertion on the public card that no
    person has ever stood behind, and a visitor cannot tell the two apart. So
    the unconfirmed ones come first: this queue's own rule is that a wrong year
    is indistinguishable from a right one forever after, and that cuts both
    ways.

    Then the blanks, hiring ones first, for the same reason every other queue
    sorts that way: a blank year on a company with 51 open roles is on screen
    in front of visitors today; a blank year on a dormant one is seen by
    nobody.

    Still deliberately NOT guessed. A founding year scraped from a copyright
    footer is wrong about as often as it is right - "(c) 2019" is when the site
    was built. Blank is honest; invented is not; and invented-then-confirmed is
    the only way one of these becomes a fact.
    """
    dismissed = read("admin_dismissed.json", {})
    hiring = {o["id"]: o.get("open_roles", 0)
              for o in board.get("organizations", [])}
    prov = founded_provenance()

    def links(c, site):
        return {
            # where the answer actually tends to live, so it is one click
            # rather than a search-engine detour
            "about": (site.rstrip("/") + "/about") if site else "",
            "linkedin": ("https://www.linkedin.com/search/results/companies/?keywords="
                         + urllib.parse.quote(c["name"])),
        }

    unconfirmed, blank = [], []
    for c in companies:
        if f"founded:{c['id']}" in dismissed \
                or c["id"] in dismissed.get("founded", {}):
            continue
        site = c.get("website") or ""
        row = {"id": c["id"], "name": c["name"],
               "description": c.get("description"), "website": site,
               "open_roles": hiring.get(c["id"], 0), **links(c, site)}
        p = prov.get(c["id"])
        if c.get("year_founded"):
            # a year on file is only queue work while it is an unconfirmed
            # machine write. Once a person has stood behind it, it is a fact
            # like any other and this queue has nothing left to ask.
            if p and not p.get("confirmed") and p.get("year") == c["year_founded"]:
                unconfirmed.append({**row, "kind": "unconfirmed",
                                    "year": c["year_founded"],
                                    "proposed_by": p.get("by"),
                                    "proposed_at": p.get("at")})
            continue
        blank.append({**row, "kind": "blank"})
    unconfirmed.sort(key=lambda r: (-r["open_roles"], r["name"]))
    blank.sort(key=lambda r: (-r["open_roles"], r["name"]))
    return unconfirmed + blank


def act_set_founded(body: dict) -> dict:
    """Write a founding year somebody read off the company's own page.

    `by` is passed through to the journal rather than defaulting to "owner".
    The 86 agent writes were journalled as the owner's because nothing here
    ever asked; they had to be re-attributed by hand afterwards, and a
    re-attribution is a thing somebody has to remember to do.
    """
    cid = (body.get("id") or "").strip()
    raw = str(body.get("year") or "").strip()
    if not raw.isdigit():
        return {"error": "a four-digit year, or use Not stated"}
    year = int(raw)
    this_year = dt.date.today().year
    # 1800 rather than something tighter: municipal suppliers are genuinely
    # old. Sanborn was founded in 1866 and is on this board.
    if not (1800 <= year <= this_year):
        return {"error": f"{year} is not a plausible founding year "
                         f"(1800-{this_year})"}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    was = c.get("year_founded")
    by = (body.get("by") or "owner").strip()
    c["year_founded"] = year
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "set-founded",
                         f"{c['name']} founded {year}", by=by)
    if bad:
        return {"error": bad}
    # Correcting an unconfirmed machine year IS confirming the record: a
    # person looked, disagreed, and put their name on the answer. Leaving it
    # marked unconfirmed would ask again forever.
    if _is_person(by):
        _record_confirmation(cid, year, by, corrected_from=was)
    return {"ok": True, "message": f"{c['name']}: founded {year}"
                                   + (f" (was {was})" if was and was != year else "")}


def _record_confirmation(cid: str, year: int, by: str,
                         corrected_from=None) -> bool:
    """Put a person's name on a year an agent proposed. Returns False if there
    was nothing to confirm."""
    store = founded_provenance()
    p = store.get(cid)
    if not p:
        return False
    p["confirmed"] = {"by": by, "at": now(), "year": year,
                      "corrected_from": corrected_from
                                        if corrected_from != year else None}
    write_atomic("founded_provenance.json", store)
    return True


def act_confirm_founded(body: dict) -> dict:
    """Confirm the year an agent proposed, as one click.

    Confirming writes nothing to companies.json - the year is already there
    and is unchanged - and everything to the provenance file: who stood behind
    it and when. That is the whole point of the exercise. The record stops
    saying "an agent typed this" and starts saying "a person checked it", and
    the only difference between those two states is a name.
    """
    cid = (body.get("id") or "").strip()
    by = (body.get("by") or "owner").strip()
    if not _is_person(by):
        return {"error": "an agent cannot confirm an agent"}
    companies = read("companies.json", [])
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    if not c.get("year_founded"):
        return {"error": "there is no year on that record to confirm"}
    if not _record_confirmation(cid, c["year_founded"], by):
        return {"error": "nothing on file says that year came from an agent"}
    return {"ok": True,
            "message": f"{c['name']}: {c['year_founded']} confirmed, and the "
                       f"record now says who by"}


def board_health(companies, board) -> dict:
    """A score, and the parts it is made of, because a composite alone hides
    which input moved.

    Weighted the way the owner ranked it: public correctness first, coverage
    second. A row that is wrong in front of visitors today costs more than a
    company we have not finished researching, because the first actively
    misleads somebody and the second only fails to help them.

    Nothing here is a target to be gamed. Every part is a real count on BOTH
    sides of the fraction, and the label says what would move it.

    That last sentence used to be false. The public-correctness part read
    `max(0, 40 - visible_wrong) / 40`, and the 40 counted nothing on file - it
    was a number somebody picked. It scored a board of 2,108 companies against
    an imaginary forty, and because this score gates the CSV export, an
    invented denominator decided when a reward opened. The denominator now is
    the count it was always pretending to be: the companies a visitor can
    actually see, which is the ones with an open posting.

    And a part with nothing in its denominator is reported as UNKNOWN rather
    than as zero. `settled + pending_scope or 1` scored an empty scope queue
    as 0% - "we have not been asked yet" rendered as "you have done none of
    it", which is the same turn-an-unknown-into-a-negative this repo refuses
    everywhere else. Those parts drop out of the weighted average instead of
    dragging it down.
    """
    n = len(companies) or 0
    live = {o["id"]: o.get("open_roles", 0) for o in board.get("organizations", [])}
    # what a visitor sees at all: a company with no open posting is not a row
    # on the public board, so it can be neither right nor wrong in front of
    # anybody
    visible = sum(1 for c in companies if live.get(c["id"], 0) > 0)
    mis = q_miscategorized(companies, board)
    visible_wrong = sum(1 for r in mis if r.get("open_roles"))
    with_site = sum(1 for c in companies if c.get("website"))
    with_year = sum(1 for c in companies if c.get("year_founded"))
    structured = sum(1 for c in companies
                     if (c.get("ats") or {}).get("type") not in
                     ("html", "unknown", None))
    settled = len(read("vendor_scope_decisions.json", {}))
    pending_scope = len(q_vendor_scope(companies, board))

    parts = [
        # (label, got, of, weight, what moves it)
        #
        # "Right bucket" and not "right", because this checks exactly one kind
        # of wrongness - a record whose own vendor_type contradicts the
        # category it is filed under. The note has to say so, or the label
        # claims more than the count does.
        ("Right bucket, public rows", visible - visible_wrong, visible, 3,
         f"{visible_wrong} of the {visible} companies with an open posting are "
         f"filed under a category their own record contradicts. Nothing else "
         f"about a row is checked here"),
        ("Names reachable", with_site, n, 1,
         f"{n - with_site} companies have no website on file"),
        ("Scope settled", settled, settled + pending_scope, 2,
         f"{pending_scope} vendors are waiting on an in/out call"),
        ("Boards found", structured, n, 1,
         f"{structured} companies are on a board we can read reliably"),
        ("Founding years", with_year, n, 1,
         f"{n - with_year} cards show a blank year"),
    ]
    scored = [p for p in parts if p[2]]
    total_w = sum(w for *_, w, _ in scored)
    score = sum((got / of) * w for _, got, of, w, _ in scored)
    return {
        "score": round(100 * score / total_w) if total_w else None,
        "visible": visible,
        "visible_wrong": visible_wrong,
        # pct is None, never 0, when the denominator is empty: no rows to be
        # right about is not the same fact as being right about none of them
        "parts": [{"label": l, "pct": round(100 * got / of) if of else None,
                   "weight": w, "note": note,
                   "of": of}
                  for l, got, of, w, note in parts],
    }


def sessions(limit: int = 30) -> dict:
    """Rulings grouped into SESSIONS, not calendar days.

    The owner's rhythm is bursty - 9 rulings, best day 9, nothing since - and
    a daily streak against that is a machine for feeling bad. A session is a
    run of rulings with no gap longer than four hours, so an evening of work
    counts once whether it happens on a Tuesday or a Sunday.

    THIS COUNTED THE WRONG THING. The docstring said rulings, the output field
    said "rulings", and the body counted every write in the journal - so an
    overnight agent's 86 set-founded writes to companies.json arrived on
    screen as the owner's 86 rulings and a personal best of 84. It now reads
    _ruling_stamps(), which is the same definition the receipt and the
    why-coverage meter use, and which leaves out anything an agent wrote.
    """
    stamps = [t for t, _ in _ruling_stamps()]
    runs, cur = [], []
    for t in stamps:
        if cur and (t - cur[-1]).total_seconds() > SITTING_GAP:
            runs.append(cur); cur = []
        cur.append(t)
    if cur:
        runs.append(cur)

    # A PERSONAL BEST IS NOT A KEYSTROKE COUNT. "41 this sitting, personal
    # best" is exactly the achievement a fast clicker earns, and the raw
    # count is what earns it - forty-one rulings in 2.6 seconds set a record.
    # So the count is still shown, because it is true and it is the owner's
    # own tally, and the BADGE reads the paced subset: rulings far enough
    # apart to have been read. Same READ_SECONDS the agree-rate uses, so
    # there is one definition of "considered" in this file.
    def paced(run: list) -> int:
        n, prev = 0, None
        for t in run:
            if prev is None or (t - prev).total_seconds() >= READ_SECONDS:
                n += 1
            prev = t
        return n

    considered = [paced(r) for r in runs]
    return {
        "sessions": len(runs),
        "best_session": max((len(r) for r in runs), default=0),
        "this_session": len(runs[-1]) if runs else 0,
        "best_considered": max(considered, default=0),
        "this_considered": considered[-1] if considered else 0,
        "rulings": len(stamps),
    }


def reversals(limit: int = 8) -> list:
    """The last few rulings and whether any were taken back.

    Shown rather than hidden, because a queue that only ever displays wins is
    not telling you how you are doing. An undo is not a black mark - it is the
    system working, and the count of them is the only honest read on whether
    the pace is right.
    """
    import journal
    rows = journal._entries()[-40:]
    undone = {r.get("undo_of") for r in rows if r.get("undo_of")}
    out = []
    for r in reversed(rows):
        if r.get("action") in ("undo", "reopen"):
            continue
        # The strip is headed "Last rulings", so it shows the person's. It
        # was showing an overnight agent's 86 set-founded writes under that
        # heading, which is the same misattribution the sitting counter had.
        if not _is_person(r.get("by")):
            continue
        out.append({"id": r["id"], "action": r["action"], "n": r.get("n", 0),
                    "at": r.get("at"), "why": r.get("why", ""),
                    "reversed": r["id"] in undone})
        if len(out) >= limit:
            break
    return out


def act_board_proposal(body: dict) -> dict:
    """Accept or refuse a board that was found inside a careers page.

    Accepting WRITES THE ATS, which is the whole point - these are the boards
    that turn a company producing nothing into one producing postings. So it
    goes through the same gate everything else does: the reference is verified
    with a real fetch first, and the board is asked whose it is, because the
    two failures already met in this data are an acquired company pointing at
    its parent (Prepared at Axon's, 500 postings) and an operating entity
    under another name (Circuit's says TFR Transit Inc).
    """
    import board_proposals, add_company, verify_boards
    cid = (body.get("id") or "").strip()
    accept = bool(body.get("accept"))
    why = (body.get("why") or "").strip()
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}

    if not accept:
        board_proposals.rule(cid, False, why, body.get("by") or "owner")
        return {"ok": True, "message": f"noted: that board is not {c['name']}'s."}

    found = next((f for f in board_proposals._read(board_proposals.FOUND, [])
                  if f["id"] == cid), None)
    if not found:
        return {"error": "that proposal is no longer on file"}
    block = found["found"]
    ok, detail = add_company.verify(block)
    if not ok:
        return {"error": f"the board no longer reads: {detail}"}
    said = verify_boards.board_says(block["type"], block["ref"])
    who = verify_boards.judge(c, said)
    if who["verdict"] == "MISMATCH" and not body.get("force"):
        return {"error": f"{who['why']}. If they were acquired, record the "
                         f"parent first - otherwise this reports somebody "
                         f"else's requisitions as {c['name']}'s."}
    c["ats"] = {"type": block["type"], "ref": block["ref"]}
    if said.get("name") and said["name"].lower() != c["name"].lower():
        c["board_owner"] = said["name"]
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "board-proposal",
                         why or f"accepted the {block['type']} board found in "
                                f"{c['name']}'s careers page",
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    board_proposals.rule(cid, True, why, body.get("by") or "owner")
    return {"ok": True, "message": f"{c['name']} now reads from "
                                   f"{block['type']} \u2014 {detail}."}


def _q_calendar(companies, board) -> list:
    """The conference-date queue, in the shape every other queue here takes.

    It reads data/conferences.json rather than companies or the board, so the
    two arguments are unused - kept so QUEUES stays one uniform table that a
    caller can iterate without special cases.
    """
    import conference_dates as cdates
    return cdates.q_calendar()


def act_conference_date(body: dict) -> dict:
    """Rule on one conference's date.

    THE CALENDAR IS THE ONE PART OF THIS BOARD THAT ROTS ON A CLOCK. A company
    that stops hiring is still a true row; a conference that happened last
    month and still shows a date is a page telling somebody to book a flight
    to an event that is over. State Healthcare IT Connect has been in that
    state for 190 days.

    Four outcomes, and they are four different facts:

      SET          a date, given by a person and parsed before it is stored.
                   conference_dates refuses to read a date off an event page
                   on purpose - measured, the three pages that yielded one all
                   yielded another event's - so a real date can only ever
                   arrive this way.
      UNANNOUNCED  checked, and they have not published one. A real answer,
                   and the reason the calendar can say so instead of leaving a
                   gap a reader has to interpret.
      ENDED        the event does not run any more.
      OK           the date on file is right and the flag was noise. Changes
                   nothing and stops the row being asked about again.

    NOTHING HERE EDITS conferences.json. The ruling is appended to its own
    file and `conference_dates.py --apply` folds it in, which is the same
    division the web admin uses: a bug in the recording half must not be able
    to corrupt the catalogue.
    """
    import conference_dates as cdates
    tag = (body.get("id") or "").strip()
    outcome = (body.get("outcome") or "").strip()
    dates = (body.get("dates") or "").strip()
    why = (body.get("why") or "").strip()
    by = body.get("by") or "owner"
    if not tag:
        return {"error": "which event?"}
    try:
        rec = cdates.rule(tag, outcome, dates, why, by=by)
    except ValueError as e:
        return {"error": str(e)}
    # Journalled through the same path every other decision file uses, so this
    # is undoable like the rest.
    err = save_decisions("conference_date_rulings.json", cdates.rulings(),
                         "conference-date", why or f"{tag}: {outcome}", by=by)
    if err:
        return {"error": err}
    msg = {"set": f"{tag} is now {rec['dates']}",
           "unannounced": f"{tag} has no date announced yet",
           "ended": f"{tag} no longer runs",
           "ok": f"{tag} keeps the date on file"}[outcome]
    return {"ok": True, "message": msg + ". Run `python3 scripts/"
                                         "conference_dates.py --apply` to "
                                         "fold it into the catalogue."}


def act_acquisition_ruling(body: dict) -> dict:
    """Rule on a board that looks like it belongs to a parent company.

    The Acquisitions queue has been READ-ONLY since it was built: 74 rows
    carrying real evidence, scripts/acquisitions.py holding a rule() nothing
    called, and acquisition_rulings.json never once written. A queue you can
    read and not answer only grows, and every sweep re-proposes what you
    already looked at - which is how somebody learns to stop opening a tab.

    Three outcomes, and they are genuinely different decisions, not three
    words for "handled":

      UNWIRE - the board is entirely the parent's and none of it is theirs.
      Prepared's greenhouse board is Axon's: 502 postings, every one stamped
      company_name "Axon". The company goes back to having no board, which is
      honest, and refresh stops reporting a parent's requisitions as theirs.

      KEEP AND LABEL - the board really does carry this company's roles among
      others. Nothing is unwired; board_owner is recorded so the card can say
      "these roles are posted on Axon's board, who acquired them, and some may
      not be Prepared roles". True, and more useful than either silence or
      deletion.

      NOT AN ACQUISITION - the slug is just odd. Vision Government Solutions
      uses "vgsi"; that is their own shorthand and nothing is wrong.

    Recording which one it is stops the next sweep asking, because
    find_embedded_ats and the audit both read the refusals back.
    """
    import acquisitions
    cid = (body.get("id") or "").strip()
    outcome = (body.get("outcome") or "").strip()
    parent = (body.get("parent") or "").strip()
    why = (body.get("why") or "").strip()
    by = (body.get("by") or "owner").strip()
    if outcome not in ("keep", "unwire", "not_acquired"):
        return {"error": "outcome must be keep, unwire or not_acquired"}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}

    # UNWIRE and KEEP both make a claim about who owns the board, and a claim
    # about ownership with no name attached cannot be checked by anybody later.
    if outcome in ("keep", "unwire") and not parent:
        return {"error": "name the company whose board this is - a ruling that "
                         "says 'someone else' cannot be checked later"}

    msg = ""
    if outcome == "unwire":
        was = c.get("ats") or {}
        c["ats"] = {"type": "unknown", "ref": None}
        c["ats_note"] = (f"unwired {dt.date.today().isoformat()}: the "
                         f"{was.get('type')} board {was.get('ref')!r} is "
                         f"{parent}'s, not {c['name']}'s"
                         + (f" - {why}" if why else ""))
        c["board_owner"] = parent
        err = validate(companies)
        if err:
            return {"error": err}
        bad = save_companies(companies, "acquisition-unwire",
                             why or f"{c['name']}'s board is {parent}'s",
                             by=by)
        if bad:
            return {"error": bad}
        msg = (f"{c['name']} no longer reads from {parent}'s board. It has no "
               f"board on file again, which is the honest state.")
    elif outcome == "keep":
        c["board_owner"] = parent
        if not (c.get("parent") or "").strip():
            c["parent"] = parent
        err = validate(companies)
        if err:
            return {"error": err}
        bad = save_companies(companies, "acquisition-keep",
                             why or f"{c['name']}'s roles are on {parent}'s "
                                    f"board among others", by=by)
        if bad:
            return {"error": bad}
        msg = (f"kept. {c['name']}'s card will say its roles are posted on "
               f"{parent}'s board and that some may not be theirs.")
    else:
        msg = f"noted: {c['name']}'s slug is just their own shorthand."

    acquisitions.rule(cid, outcome, parent, why, by)
    return {"ok": True, "message": msg}


def act_vendor_scope_all(body: dict) -> dict:
    """Rule a family of horizontal vendors in one go.

    Takes the NAMES the person was actually shown, not a pattern. A pattern
    would sweep in the ones that look alike and are not: "payments" matches
    Kipu Health and Clio, and ruling those out on a regex is the invisible,
    permanent error this whole repo is built around.
    """
    names, call = body.get("names") or [], body.get("call")
    if isinstance(names, str) or not isinstance(names, list) \
            or not all(isinstance(n, str) and n.strip() for n in names):
        # a bare string iterates per CHARACTER and writes a junk ruling for
        # each - permanent, because rulings are never re-asked
        return {"error": "names must be a list of company names"}
    if not names or call not in ("in", "sled", "out"):
        return {"error": "need names and a call of in, sled or out"}
    d = read("vendor_scope_decisions.json", {})
    # one stamp for the whole bulk, because it WAS one decision - stamping
    # each row at its own microsecond would let a sitting boundary fall in
    # the middle of a single click
    stamp = now()
    n = 0
    for name in names:
        k = _vkey(name)
        if k in d:
            continue
        # why stays null when nobody typed one. It used to be filled in with
        # "bulk ruling on <theme>", which the why-coverage meter then counted
        # as a reason - the craft signal writing its own evidence. What the
        # theme was is already recorded in `saw`, where it belongs: that is
        # what the person was shown, not what they said about it.
        d[k] = {"call": call, "name": name, "on": dt.date.today().isoformat(),
                "at": stamp,
                "by": (body.get("by") or "owner").strip(),
                "why": (body.get("why") or "").strip() or None,
                "bulk": True, "saw": {"theme": body.get("theme")}}
        n += 1
    bad = save_decisions("vendor_scope_decisions.json", d, "vendor-scope-all",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"),
                         force=bool(body.get("force")))
    if bad:
        return {"error": bad}
    said = {"in": "added as full companies",
            "sled": "added, public-sector roles only",
            "out": "left off the board"}[call]
    return {"ok": True, "message": f"{n} vendor(s) {said}"}


END_STATE = {
    "miscategorized": "Clean shelves",
    "vendors": "Scope settled",
    "duplicates": "One record per vendor",
    "boards": "Every door mapped",
    "websites": "Every name reachable",
    "blocked": "Every wall retried",
}

# The queues a belt is allowed to run. Deliberately short.
#
# A belt hands you one item at a time and prepares the next, which is the
# right grip for a question you can answer from what is on the card. It is
# the WRONG grip for the acquisitions queue - where the whole job is deciding
# whether a slug belongs to a parent company, and being handed the next one
# is pressure to stop reading - and for the rows where the guesser has
# nothing to propose, which need the description read rather than a proposal
# checked. Those stay a list, on purpose. A counter there would buy speed
# with accuracy, and accuracy is the only thing this tool sells.
BELT_QUEUES = ("miscategorized", "vendors")

# Queues where the person is shown a machine PROPOSAL rather than a blank
# form. Only these can have an agree-rate, because only these have something
# to agree with.
PROPOSAL_QUEUES = ("miscategorized",)

# WHAT MAKES AN AGREE-RATE A MEASUREMENT.
#
# This used to be one number - `ruled >= 40` - and an adversarial review
# broke it in three and a half seconds: 49 accepts straight down the belt, no
# card read, no reason typed, and the admin reported a 100% agree-rate at
# high, medium AND low confidence, declared itself measured, and opened the
# CSV export. A COUNT CANNOT TELL READING FROM CLICKING. That is not a bug in
# the threshold, it is the wrong kind of evidence, and raising 40 to 400 would
# have made the attack take thirty seconds instead of three.
#
# So a count is now necessary and nowhere near sufficient. Two more conditions
# stand beside it, and neither of them is bought with volume:
#
# PACE. A ruling is CONSIDERED only if at least READ_SECONDS passed since the
# previous one. This is measured from the timestamps the server writes itself,
# not from anything the page reports, so no client can flatter it. A fast
# ruling is not punished and not deleted - it simply is not evidence that the
# card was read, which is the honest treatment of it. Forty-nine rulings in
# 3.5 seconds contribute one considered ruling, not forty-nine.
#
# DISSENT. At least MIN_DISSENT of those considered rulings must have OVERRULED
# the proposal. An unbroken run of agreement is exactly what blind accepting
# looks like, so it cannot be the evidence that the guesser is good; it is a
# record of one behaviour that happens to be consistent with two very
# different explanations. This is a floor on evidence and must never be shown
# as a target - the UI never counts down to it, because "disagree four more
# times" is an instruction to file four companies wrongly.
MEASURED_AT = 40

# Twelve seconds to read a company name, a one-line description, a proposed
# sector and category, and the evidence line under it, then decide. Chosen to
# be beatable by an attentive person on an easy row and not by a person
# holding a key down; it is a floor on plausibility, not a target pace.
READ_SECONDS = 12

# Five, not one: a single overrule can be a mis-key. Five is somebody who has
# been arguing with this guesser.
MIN_DISSENT = 5


# What an absent half of a proposal looks like once something has formatted
# it into a string. Python writes None, JavaScript writes null or undefined,
# and both arrive here as "null / null" - a pair that would otherwise pass a
# naive "/ is present" test and be scored as a proposal the guesser never
# made. This is the same rule as everywhere else in the repo: an unknown is
# reported as an unknown, never converted into a verdict.
_NULLISH = {"", "none", "null", "undefined", "nan"}


def _seen_proposal(rec: dict) -> str | None:
    """The proposal a ruling record says the person was actually shown.

    Returns None for anything that is not a real "Sector / Category" pair.
    A ruling made with no proposal on screen is not evidence about the
    guesser in either direction, and counting it as a disagreement would
    read as the guesser being wrong when it never spoke.
    """
    saw = (rec or {}).get("saw") or {}
    p = saw.get("proposed")
    if not isinstance(p, str) or "/" not in p:
        return None
    left, _, right = p.partition("/")
    if left.strip().lower() in _NULLISH or right.strip().lower() in _NULLISH:
        return None
    return p.strip()


def agree_rate() -> dict:
    """How often the person takes the placement the machine proposed.

    This number does not exist yet, and that is the point. Every wrong-bucket
    row carries a proposal from an earlier AI pass; zero have ever been ruled
    on. So the queue is not "review the AI's work", it is the measurement
    that tells us whether the AI's work was worth reviewing - and it has to
    be visible from the FIRST ruling, next to the confidence label it is
    testing, or the confidence label is just a word.

    Split by the guesser's own confidence, because "72% agreed" hides the
    only interesting question: whether high confidence means anything. And
    MEASURED per band rather than in total, for the same reason: forty medium
    rulings say nothing about what "high" is worth, so they must not be
    allowed to unlock a one-key ruling on a high-confidence card.

    Nothing here is derived from a stored verdict - it is recomputed from what
    the record says the person saw, what they chose, and WHEN the server wrote
    it down, so a client cannot flatter any of it.
    """
    seen = []          # (when|None, confidence, agreed?)

    def tally(prop: str, chose: str, conf: str, at) -> None:
        seen.append((_ts(at), (conf or "unstated"), chose == prop))

    for rec in read("placement_rulings.json", {}).values():
        if not isinstance(rec, dict):
            continue
        prop = _seen_proposal(rec)
        if not prop:
            continue
        tally(prop, f"{rec.get('sector')} / {rec.get('category')}",
              (rec.get("saw") or {}).get("confidence"), rec.get("at"))
    # "Bucket is right" is an OVERRULE, not an absence of a ruling: the
    # proposal said move it, the person looked and said leave it. Dropping
    # those would measure only the cases where the guesser was already
    # trusted, which is how a model grades itself.
    for q, _k, rec in dismissal_records():
        if q != "miscategorized":
            continue
        prop = _seen_proposal(rec)
        was = (rec.get("saw") or {}).get("was")
        if not prop or not isinstance(was, str):
            continue
        tally(prop, was.strip(), (rec.get("saw") or {}).get("confidence"),
              rec.get("at"))

    # PACE, from the server's own clock. Records written before rulings
    # carried `at` have no timestamp: they are counted in the rate and are
    # never counted as considered, because we know they happened and do not
    # know how fast - and guessing would be the invention this file refuses.
    #
    # Walked RECORD by record, not timestamp by timestamp. Marking the
    # timestamps that follow a long-enough gap and then testing membership
    # scores every ruling that shares a second with a considered one as
    # considered too - and `at` has one-second resolution, so 26 rulings
    # hammered out in the same second all inherit the first one's credit.
    # Forty-one blind accepts came back with three "considered" that way.
    ordered = sorted(((t, c, ok) for t, c, ok in seen if t),
                     key=lambda r: r[0])
    ruled = agreed = considered = dissent = 0
    by_conf: dict[str, list] = collections.defaultdict(lambda: [0, 0, 0, 0])
    for t, conf, ok in seen:
        ruled += 1
        agreed += ok
        row = by_conf[conf]
        row[0] += 1
        row[1] += ok
    prev = None
    for t, conf, ok in ordered:
        if prev is None or (t - prev).total_seconds() >= READ_SECONDS:
            considered += 1
            row = by_conf[conf]
            row[2] += 1
            if not ok:
                dissent += 1
                row[3] += 1
        prev = t

    def band(c: str) -> dict:
        n, ag, cons, dis = by_conf[c]
        return {"confidence": c, "ruled": n, "agreed": ag,
                "pct": round(100 * ag / n), "considered": cons,
                "dissent": dis,
                "measured": cons >= MEASURED_AT and dis >= MIN_DISSENT}

    # Why it is not a measurement, in words rather than as a countdown. A
    # number to chase is a target, and the two things being asked for here -
    # slow down, disagree when the guesser is wrong - are exactly the two
    # things nobody should do on purpose to move a bar.
    if considered >= MEASURED_AT and dissent >= MIN_DISSENT:
        why_not = None
    elif not ruled:
        why_not = ("nothing has been ruled on, so the guesser has never been "
                   "checked")
    elif considered < MEASURED_AT:
        why_not = (f"only {considered} of these {ruled} rulings were made far "
                   f"enough apart to be evidence that the card was read; the "
                   f"rest may be right and are not a measurement")
    else:
        why_not = ("not one of these rulings disagreed with the guesser, and "
                   "an unbroken run of agreement is what blind accepting "
                   "looks like too")

    order = ["high", "medium", "low", "unstated"]
    return {
        "ruled": ruled,
        "agreed": agreed,
        "considered": considered,
        "dissent": dissent,
        "pct": round(100 * agreed / ruled) if ruled else None,
        "measured": why_not is None,
        "why_not": why_not,
        "by_confidence": [band(c) for c in order if by_conf.get(c, [0])[0]],
    }


def rulings_by_queue() -> collections.Counter:
    """How many items each queue has already had a person's answer on.

    The counterpart to a queue's remaining length: a bar that fills toward a
    named end state needs both halves, and the done half only exists in the
    decision files.
    """
    done = collections.Counter()
    for fname, queue in (("vendor_scope_decisions.json", "vendors"),
                         ("placement_rulings.json", "miscategorized"),
                         ("scope_decisions.json", "scope")):
        done[queue] += sum(1 for r in read(fname, {}).values()
                           if isinstance(r, dict))
    for q, _k, _rec in dismissal_records():
        done[q] += 1
    # The rulings only the journal remembers. merge, save-website, set-board,
    # retry-board and their kin write through save_companies(), which journals
    # and touches no decision file - so the done half of four END_STATE queues
    # (duplicates, websites, boards, and every blocked retry) sat at zero no
    # matter how many rulings landed. _ruling_stamps counted those same events
    # into the day and sitting counters, and the code around _game insists the
    # two tallies must not be able to disagree; they disagreed from the start.
    # mine_only=False on purpose: this counter measures decisions made, not
    # who made them, same as the decision files above, which carry no filter.
    for _t, q, _eid in _journal_rulings(mine_only=False):
        done[q] += 1
    return done


def queue_state(name: str, left: int, done: collections.Counter | None = None
                ) -> dict | None:
    """The named end state a queue is heading for, and how far along it is.

    Queues do not go to zero, they reach a state with a title. "Clean
    shelves" is a thing a person finishes; "0 remaining" is a thing that
    merely stops.
    """
    label = END_STATE.get(name)
    if not label:
        return None
    d = (done if done is not None else rulings_by_queue()).get(name, 0)
    return {"queue": name, "name": label, "done": d, "left": left,
            "pct": round(100 * d / (d + left)) if (d + left) else 100}


# --- no unlocks, and why --------------------------------------------------
#
# There were two: CSV export and an API key, opened by a board-health
# threshold. An adversarial review defeated every gate protecting them, in
# fifteen distinct ways. The cheapest was one call to the bulk vendor path -
# 243 names, zero seconds, 240 rulings written, health 58 to 83, unlock open.
# Others needed nothing more than the single character "x" as a reason.
#
# The reason they are all defeatable is structural rather than a bug to patch:
# the server gated on facts THE CLIENT SUPPLIED. confidence, proposed and by
# are all posted by whoever is calling, so the confidence band that opened the
# one-key ruling was whatever the caller typed, and excluding agent writes was
# an honour system anybody could opt out of by omitting a field.
#
# That could be fixed - derive every gated fact from the proposal the server
# itself served, never from the reply. It was not fixed, because on a
# two-person team the thing being defended against is the owner, and hardening
# a lock against its own key is an arms race with no winner and a real cost in
# code nobody can follow.
#
# So the unlocks are gone and the capabilities are simply capabilities. What
# survived the review is the honest half, and it is the half CLAUDE.md argued
# for in the first place: named end states, personal bests against your own
# past, and the why-coverage craft signal. None of those hand out a prize, so
# none of them are worth gaming.

def unlocks(health: dict) -> list:
    """Nothing is gated. Kept as a function returning [] so callers and the
    page do not have to change shape, and so the reasoning above stays next to
    the thing it explains."""
    return []


# --- the CSV the unlock hands over ---------------------------------------

# A leading =, +, - or @ makes a spreadsheet treat a cell as a FORMULA, and a
# company name arrives here from an outside submission. Prefixing an
# apostrophe is the one fix that survives a round trip through Excel and
# Sheets both; quoting alone does not.
_FORMULA = re.compile(r"^[=+\-@\t\r]")

CSV_COLUMNS = ["id", "name", "sector", "category", "also", "website",
               "hiring_status", "open_roles", "ats_type", "ats_ref",
               "year_founded", "location", "vendor_type", "checked"]


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if _FORMULA.match(s) else s


def board_csv(companies, board) -> str:
    """The board as a spreadsheet, out of the same records the site reads.

    Every column is a stored fact. Nothing is estimated, inferred or filled
    in: an unknown founding year is an empty cell, not a guess, because a
    blank reads as "we do not know" and a number reads as "we checked".
    """
    live = {o["id"]: o.get("open_roles", 0)
            for o in board.get("organizations", [])}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for c in sorted(companies, key=lambda x: (x.get("name") or "").lower()):
        h = c.get("hiring") or {}
        a = c.get("ats") or {}
        also = "; ".join(f"{x.get('sector')} / {x.get('category')}"
                         for x in (c.get("also") or []))
        w.writerow([_cell(x) for x in (
            c.get("id"), c.get("name"), c.get("sector"), c.get("category"),
            also, c.get("website"), h.get("status"), live.get(c.get("id"), 0),
            a.get("type"), a.get("ref"), c.get("year_founded"),
            c.get("location"), c.get("vendor_type"), h.get("checked"))])
    return buf.getvalue()


# --- the end-of-shift receipt --------------------------------------------

SITTING_GAP = 4 * 3600      # same four hours sessions() uses


def _ts(v) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


# Journal actions that ARE somebody answering a queue and are recorded in no
# decision file, so the journal is the only place they can be counted from.
#
# "place" is deliberately absent: act_place journals under that name AND
# writes placement_rulings.json, so counting it here would report every
# wrong-bucket ruling twice. "move" IS here, because a Sort-board drag is a
# real ruling with no other record - which is exactly why act_move takes the
# journal name as an argument. Everything else the journal holds - patch,
# cleanup, undo, reopen, restore-name, admin - is an edit or housekeeping,
# not an answer to a question somebody was asked.
# The value is the queue the ruling answers, because the receipt draws that
# queue's bar and a ruling filed against the wrong queue would move a bar
# nothing moved. "also" and "move" answer no single queue tab, so they are
# named for the work rather than mapped onto one.
JOURNAL_RULINGS = {
    "set-founded": "founded", "confirm-founded": "founded",
    "identity-ruling": "review", "resolve-submission": "submissions",
    "set-board": "boards", "retry-board": "boards", "posts-at": "boards",
    "save-website": "websites", "merge": "duplicates",
    "also": "placement", "move": "placement",
}


def _is_person(by: str | None) -> bool:
    """Was this written by the person, or by something automated.

    An agent put 86 set-founded writes into the live companies.json in one
    night, and every screen in this admin counted them as the owner's own
    work: 92 "rulings", a best sitting of 84. A count of somebody else's
    typing is not a personal best, and reporting it as one is the same
    dishonesty as an invented denominator wearing a friendlier face.
    """
    return not str(by or "owner").strip().lower().startswith("agent")


def _ruling_stamps(mine_only: bool = True) -> list[tuple[dt.datetime, str]]:
    """(when, which queue) for every ruling that recorded a full timestamp.

    Rulings have always carried a DATE, which is the right grain for "how
    many did you do this month" and the wrong grain for "what did this
    sitting change" - a sitting is not a day and two sittings can share one.
    So rulings now also carry `at`. Records written before that field
    existed have no `at` and are simply not attributed to a sitting, which
    is the honest treatment: we know they happened, we do not know when.

    This is the ONE definition of "a ruling" in this file. sessions(), the
    receipt and the sitting counter all read it, so they cannot disagree
    about what they are counting - which they did: the header said "133 this
    sitting" from the journal while the meter beside it said "23% of your
    rulings say why" from the decision files, two denominators telling one
    story.
    """
    out = []
    for fname, queue in (("vendor_scope_decisions.json", "vendors"),
                         ("placement_rulings.json", "miscategorized"),
                         ("scope_decisions.json", "scope")):
        for rec in read(fname, {}).values():
            if not isinstance(rec, dict):
                continue
            if mine_only and not _is_person(rec.get("by")):
                continue
            t = _ts(rec.get("at"))
            if t:
                out.append((t, queue))
    for q, _k, rec in dismissal_records():
        if mine_only and not _is_person(rec.get("by")):
            continue
        t = _ts(rec.get("at"))
        if t:
            out.append((t, q))
    for t, queue, _eid in _journal_rulings(mine_only):
        out.append((t, queue))
    return sorted(out)


def _journal_rulings(mine_only: bool = True) -> list[tuple[dt.datetime, str, str]]:
    """(when, queue, journal id) for the rulings only the journal remembers."""
    import journal
    out = []
    for r in journal._entries():
        queue = JOURNAL_RULINGS.get(r.get("action"))
        if not queue:
            continue
        if mine_only and not _is_person(r.get("by")):
            continue
        t = _ts(r.get("at"))
        if t:
            out.append((t, queue, r.get("id")))
    return out


def receipt() -> dict:
    """What this sitting actually changed, in the units the work was done in.

    Not a score and not a congratulation. Every line is a difference between
    two states we can both observe, and a line we cannot derive is left out
    rather than approximated - a receipt that rounds is a receipt nobody
    reads twice.

    Two lines used to add up to more than happened. "55 stamped" and "133
    edits to company records" stood one above the other as though they were
    separate piles, but every one of the 55 rulings also wrote a journal
    entry, so the 55 sat INSIDE the 133 and the receipt read as 188. Both
    counts are real; what was wrong was the arrangement. The nesting is now
    stated - `edits` is the total and `stamped` is the part of it that
    carried somebody's judgment - and anything an agent wrote is counted
    separately as exactly that, rather than silently added to the owner's
    evening.
    """
    import journal
    entries = [r for r in journal._entries() if _ts(r.get("at"))]
    stamps = _ruling_stamps()
    times = sorted([_ts(r["at"]) for r in entries] + [t for t, _ in stamps])
    if not times:
        return {"open": False}
    # the last run of activity with no gap longer than four hours. Same
    # boundary sessions() uses, over a wider set of events: a sitting spent
    # ruling vendors leaves nothing in the journal at all.
    start = times[0]
    for i in range(1, len(times)):
        if (times[i] - times[i - 1]).total_seconds() > SITTING_GAP:
            start = times[i]

    sitting = [r for r in entries if _ts(r["at"]) >= start]
    mine = [r for r in sitting if _is_person(r.get("by"))]
    theirs = collections.Counter(str(r.get("by")) for r in sitting
                                 if not _is_person(r.get("by")))
    ruled = [(t, q) for t, q in stamps if t >= start]
    by_queue = collections.Counter(q for _, q in ruled)
    reversed_n = sum(1 for r in mine if r.get("action") in ("undo", "reopen"))

    # Boards that went from "nothing we can read" to a real one, taken from
    # the journal's own before/after images rather than from a count of
    # button presses: a retry that found nothing is not a door.
    opened = []
    for r in mine:
        if r.get("file") != "companies.json":
            continue
        for cid, ch in (r.get("changes") or {}).items():
            b4 = ((ch.get("before") or {}).get("ats") or {}).get("type")
            af = ((ch.get("after") or {}).get("ats") or {}).get("type")
            if af and af != "unknown" and b4 in (None, "unknown") \
                    and cid not in opened:
                opened.append(cid)

    # read(), not read_companies(): read_companies remembers what it handed
    # out so the next write can diff against it, and a read-only report has
    # no business moving that before-image
    companies, board = read("companies.json", []), read("board.json", {})
    done_now = rulings_by_queue()
    lines = []
    for q in ("miscategorized", "vendors"):
        n = by_queue.get(q, 0)
        if not n:
            continue
        left_now = len(QUEUES[q](companies, board))
        now = queue_state(q, left_now, done_now)
        # a ruling moves exactly one row from left to done, so the state at
        # the start of the sitting is this one wound back by n
        before = queue_state(q, left_now + n,
                             collections.Counter({q: done_now.get(q, 0) - n}))
        lines.append({"queue": q, "name": now["name"], "n": n,
                      "from": before["pct"], "to": now["pct"],
                      "left": now["left"]})

    return {
        "open": True,
        "since": start.isoformat(timespec="minutes"),
        # stamped is a SUBSET of edits, and the page says so on one line.
        # They are not two piles to be added.
        "stamped": len(ruled),
        "reversed": reversed_n,
        "edits": len(mine) - reversed_n,
        # not the owner's work, and never folded into it again
        "by_others": [{"by": who, "n": n} for who, n in theirs.most_common()],
        "states": lines,
        "doors": opened,
        "health": board_health(companies, board).get("score"),
    }


def _game(counts: dict) -> dict:
    """The gamification layer, built strictly from ruling records.

    Three mechanics, chosen in CLAUDE.md against the owner's framework:
    quests whose reward is the product working better, personal bests
    against the user's own last 30 days, and a craft signal - why-coverage -
    because the reason on a ruling is what teaches the classifier later, and
    it is a measure of care rather than volume. Volume is deliberately never
    scored on its own: a wrong ruling is invisible and permanent here.

    WHY-COVERAGE HAS ITS OWN DENOMINATOR AND THE PAGE HAS TO SAY SO. It can
    only be computed where a typed reason has somewhere to live - the decision
    files - and that is a smaller set than "your rulings", which also includes
    the ones recorded only in the journal. Rendering "23% of your rulings say
    why" directly above "133 this sitting" told one story with two
    denominators. So this returns `why_of` as well as the percentage, and the
    page names the set it is a percentage of.

    Nothing an agent wrote is in any of these numbers. A personal best is
    personal.
    """
    per_day = collections.Counter()
    with_why = total = 0
    sources = [("vendor_scope_decisions.json", "vendors"),
               ("placement_rulings.json", "miscategorized"),
               ("scope_decisions.json", "scope")]
    # the done-per-queue tally lives in one place, because the sorties, the
    # queue headers and the receipt all draw the same bar and two of them
    # disagreeing would be invisible
    done_by_queue = rulings_by_queue()
    for fname, queue in sources:
        for r in read(fname, {}).values():
            if not isinstance(r, dict) or not _is_person(r.get("by")):
                continue
            total += 1
            if (r.get("why") or "").strip():
                with_why += 1
    for _q, _k, entry in dismissal_records():
        if _is_person(entry.get("by")):
            total += 1
            if (entry.get("why") or "").strip():
                with_why += 1

    # The pace lines read the SAME ruling definition the sitting counter and
    # the receipt read, out of _ruling_stamps(), so "today" and "this sitting"
    # cannot disagree about what a ruling is.
    for t, _q in _ruling_stamps():
        per_day[t.date().isoformat()] += 1

    today = dt.date.today()
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(30)]
    best = max((per_day.get(d, 0) for d in days[1:]), default=0)

    # The tape: what the last few rulings were, newest first. Not a score -
    # a record of consequence, so the work reads as having caused something.
    tape = []
    for fname, verb in (("vendor_scope_decisions.json", "ruled"),
                        ("placement_rulings.json", "refiled")):
        for r in read(fname, {}).values():
            if not isinstance(r, dict) or not r.get("on") \
                    or not _is_person(r.get("by")):
                continue
            what = r.get("name") or r.get("sector")
            if verb == "refiled":
                what = f"{r.get('sector')} / {r.get('category')}"
            tape.append({"on": r["on"], "verb": verb, "what": what,
                         "call": r.get("call"), "why": r.get("why")})
    tape.sort(key=lambda t: t["on"], reverse=True)
    states = [queue_state(q, counts.get(q, 0), done_by_queue)
              for q in END_STATE
              if counts.get(q, 0) or done_by_queue.get(q, 0)]
    return {"today": per_day.get(days[0], 0), "best_30": best,
            "why_coverage": round(100 * with_why / total) if total else None,
            # the set that percentage is OF, so the page never has to guess
            "why_of": total,
            "rulings_total": total, "states": states, "tape": tape[:6]}


def floors(companies, board) -> list:
    """Per-conference progress on the capture worklist.

    The 680-company no-board pile is worked BY FLOOR - you stand on the NACo
    carpet with the extension open, not in an alphabetical list - so the
    progress that matters is per floor: of the companies we know exhibit
    there, how many now have a door we can watch. A floor with every door
    mapped is finished, and stays finished; nothing here decays.
    """
    tally = collections.defaultdict(lambda: {"mapped": 0, "left": 0})
    for c in companies:
        evs = _events(c.get("description"))
        if not evs:
            continue
        has = (c.get("ats") or {}).get("type") not in (None, "unknown")
        for ev in evs:
            tally[ev]["mapped" if has else "left"] += 1
    out = []
    for ev, t in tally.items():
        total = t["mapped"] + t["left"]
        if total < 4:            # too small to be a floor worth pacing
            continue
        out.append({"event": ev, "mapped": t["mapped"], "left": t["left"],
                    "total": total, "pct": round(100 * t["mapped"] / total)})
    # nearly-done floors first: the ones a sitting could actually finish
    out.sort(key=lambda r: (r["left"] == 0, -r["pct"] if r["left"] else 0,
                            r["left"]))
    return out


def triage(companies, board) -> dict:
    """What to work on next, and honestly how much of each pile is workable.

    Eleven tabs and two thousand items is a wall, and a wall is the thing
    people stop opening. Three facts turn it back into work:

    WHAT IT CHANGES. A wrong bucket on a company with open roles is on the
    public page; the same error on a dormant company is seen by nobody. The
    same queue can be urgent and irrelevant at once, so the recommendation
    counts the visible part separately.

    WHAT IS ACTUALLY WORKABLE. 1,033 companies have no readable board, but a
    hundred of them have no website either, so there is nothing for a person
    to open. Presenting those as pending work is how a queue teaches you to
    ignore it.

    WHAT IS ALREADY DONE. Rulings are recorded for the day they become a
    score. Until then they are the only evidence the pile is shrinking.
    """
    hiring = {o["id"]: o.get("open_roles", 0)
              for o in board.get("organizations", [])}
    counts = {k: len(f(companies, board)) for k, f in QUEUES.items()}

    mis = q_miscategorized(companies, board)
    visible = sum(1 for r in mis if r["open_roles"])
    boards = q_boards(companies, board)
    reachable = sum(1 for r in boards if r.get("website"))
    vendors = q_vendor_scope(companies, board)
    families = collections.Counter(v["theme"] for v in vendors)
    rulable = sum(n for t, n in families.items() if t != "Everything else")

    # A sortie is scoped to what a SITTING can finish, not to the whole
    # queue. Three bars at 0-of-240 read as a mountain and kill the appetite
    # they exist to build; the same work framed as "16 of 16" is a thing you
    # close before lunch. The full queue total still shows on its tab.
    recs = []
    if visible:
        recs.append({"queue": "miscategorized", "n": visible,
                     "scope": visible, "done": 0,
                     "goal": "the storefront is right",
                     "headline": f"Fix the {visible} the public can see",
                     "why": f"{visible} miscategorised companies are hiring right "
                            f"now, so they are the top rows of the public "
                            f"Companies tab. The other "
                            f"{counts.get('miscategorized', 0) - visible} are "
                            f"invisible today and can wait."})
    if families:
        top, n_top = families.most_common(1)[0]
        if top == "Everything else" and len(families) > 1:
            top, n_top = families.most_common(2)[1]
        recs.append({"queue": "vendors", "n": n_top,
                     "scope": n_top, "done": 0,
                     "goal": f"{top} is settled",
                     "headline": f"Settle {top} in one decision",
                     "why": f"{n_top} vendors, one call. The largest family on "
                            f"the board, and the whole line leaves the queue "
                            f"with your reason on every one of them."})
    if counts.get("duplicates"):
        recs.append({"queue": "duplicates", "n": counts["duplicates"],
                     "scope": counts["duplicates"], "done": 0,
                     "goal": "one record per vendor",
                     "headline": f"Merge the {counts['duplicates']} duplicate pairs",
                     "why": "the whole queue is small enough to end today, and "
                            "every merge keeps the research from both sides"})
    if counts.get("submissions"):
        recs.append({"queue": "submissions", "n": counts["submissions"],
                     "scope": counts["submissions"], "done": 0,
                     "goal": "nobody is left waiting",
                     "headline": f"Answer the {counts['submissions']} from outside",
                     "why": "a stranger submitted a company and is waiting to "
                            "see whether it landed"})

    rulings = 0
    for f in ("vendor_scope_decisions.json", "placement_rulings.json",
              "scope_decisions.json"):
        rulings += len(read(f, {}))
    return {
        "counts": counts,
        "game": _game(counts),
        "floors": floors(companies, board),
        "recommend": recs,
        "notes": [
            # boards is now website-only by construction, so the honest note is
            # about the pile's NATURE, not a second number: most of it is
            # genuinely boardless and no fetcher will ever fix it.
            f"{counts.get('boards', 0)} companies have a website and no findable "
            f"board. A field audit put 55-63% of that pile as genuinely "
            f"boardless - they hire on LinkedIn or by email - so this is the "
            f"capture extension's work, not a fetcher's.",
            f"{counts.get('blocked', 0)} more were blocked or unreachable when "
            f"probed, which is not evidence of anything; they re-probe "
            f"themselves weekly.",
            f"{counts.get('miscategorized', 0)} are in the wrong bucket; {visible} of "
            f"those are hiring, so the other {counts.get('miscategorized', 0) - visible} "
            f"are invisible to visitors today.",
        ],
        "done": rulings,
    }


def q_miscategorized(companies, board) -> list:
    """Product companies parked in the Suppliers & Services bucket.

    These records contradict themselves: vendor_type "GovTech Product" and
    govtech true, filed under the category reserved for everything that is
    NOT a product, and usually carrying the sector of whichever trade show
    found them rather than what they sell. Workday sits in Transit & Parking
    because it exhibited at APTA.

    They are the most visible rows on the public Companies tab - the ones
    hiring hardest sort to the top - so this is a front-of-house problem, not
    bookkeeping. Each row arrives with a proposed placement from the same
    guesser intake uses, and its evidence, because a proposal a person cannot
    interrogate is just a default with extra steps.
    """
    dismissed = read("admin_dismissed.json", {})
    hiring = {o["id"]: o.get("open_roles", 0)
              for o in board.get("organizations", [])}
    proposals = {}
    pdir = DATA / "conference_intake" / "placements"
    if pdir.exists():
        for f in sorted(pdir.glob("*.json")):
            try:
                for row in json.loads(f.read_text()).get("placements", []):
                    proposals[row["id"]] = row
            except (json.JSONDecodeError, KeyError, OSError):
                continue
    out = []
    for c in companies:
        if c.get("category") != "Suppliers & Services":
            continue
        if not (c.get("govtech") or c.get("vendor_type") == "GovTech Product"):
            continue
        if f"miscategorized:{c['id']}" in dismissed \
                or c["id"] in dismissed.get("miscategorized", {}):
            continue
        blob = f"{c['name']} {c.get('description') or ''}".lower()
        sec, cat, conf, why = add_company.guess_sector(blob)
        # A read of the description beats keyword counting on one-line
        # descriptions: the guesser could place only 92 of 238, because
        # "Endpoint management and security platform" contains none of its
        # vocabulary. Where a proposal exists it wins, and it says so.
        prop = proposals.get(c["id"])
        if prop and prop.get("proposed_sector"):
            sec, cat = prop["proposed_sector"], prop["proposed_category"]
            conf = prop.get("confidence") or "medium"
            why = [prop.get("why") or "proposed from the description"]
        out.append({
            "id": c["id"], "name": c["name"], "sector": c["sector"],
            "category": c["category"], "website": c.get("website"),
            "description": c.get("description"), "open_roles": hiring.get(c["id"], 0),
            "proposed_sector": sec, "proposed_category": cat,
            "confidence": conf, "evidence": why})
    # Hiring first: a wrong bucket on a company with 100 open roles is seen by
    # every visitor, a wrong bucket on a dormant one is seen by nobody.
    out.sort(key=lambda r: (-r["open_roles"], r["name"]))
    return out


# Horizontal vendors arrive in recognisable families, and 42 near-identical
# endpoint-security companies is not 42 decisions. These patterns only GROUP
# the queue so a person can see a family at once; they never rule. Bulk
# rulings act on explicit names the person was shown, never on the pattern,
# because "payments" also catches Kipu Health and Clio, which are arguably
# exactly the govtech this board is for.
VENDOR_THEMES = [
    ("Cybersecurity", r"security|endpoint|threat|siem|firewall|identit(y|ies)|"
                      r"zero.trust|phishing|vulnerab"),
    ("HR and payroll", r"\bhr\b|human resources|payroll|benefits|recruit|"
                       r"workforce management|talent"),
    ("Data and analytics", r"analytics|business intelligence|\bbi\b|data platform|"
                           r"data warehouse|dashboards?"),
    ("Cloud and infrastructure", r"cloud infrastructure|hosting|data cent|compute|"
                                 r"kubernetes|devops|hyperscal|storage"),
    ("Payments and finance ops", r"payment|billing|invoice|expense|treasury|"
                                 r"accounts payable|\berp\b"),
    ("Comms and collaboration", r"collaborat|messaging|video conferenc|"
                                r"work management|project management|\bcrm\b"),
    ("Health and clinical", r"clinical|patient|telehealth|behavioral health|"
                            r"\behr\b|electronic health"),
]


def _theme(text: str) -> str:
    for name, pat in VENDOR_THEMES:
        if re.search(pat, text or "", re.I):
            return name
    return "Everything else"


def q_vendor_scope(companies, board) -> list:
    """Horizontal product companies found on a government exhibit floor.

    Alteryx sells data analytics to everyone, and it took a booth at a
    university-finance show. It is a real product company, it does sell to
    public buyers, and it is not govtech the way Tyler is. The research pass
    refuses to decide these on its own for the same reason the federal roles
    are queued: a pattern that quietly rules either way is worse than a
    question, and a wrong "not govtech" is invisible and permanent.

    Three answers, each recorded with who gave it and why:
      in    a full card, monitored like any other company
      sled  a card flagged sled_only, so only public-sector roles show
      out   not on this board; the name is remembered so it is never re-asked
    """
    q = read("scope_review_queue.json", {"items": []})
    ruled = read("vendor_scope_decisions.json", {})
    out = [{**i, "key": _vkey(i["name"]),
            "theme": _theme(f"{i.get('name','')} {i.get('description','')}")}
           for i in q.get("items", []) if _vkey(i["name"]) not in ruled]
    # families together, and the biggest family first: that is the sitting
    # where one look settles the most rows.
    sizes = collections.Counter(r["theme"] for r in out)
    # biggest family first, because that is where one look settles the most
    # rows - but "Everything else" is not a family and cannot be bulk-ruled,
    # so it goes last however large it is.
    out.sort(key=lambda r: (r["theme"] == "Everything else",
                            -sizes[r["theme"]], r["theme"], r["name"]))
    return out


def slug(name: str) -> str:
    """The one way an id is made here, and never a value taken from a request.

    An id becomes a filename downstream, so a caller-chosen id is a
    caller-chosen path - which is exactly how attacker bytes once landed
    outside assets/logos.
    """
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _vkey(name: str) -> str:
    return slug(name)


def act_also(body: dict) -> dict:
    """Add or drop an extra department for a company.

    Moving is the wrong verb for a vendor that genuinely sells into several:
    Tyler under Courts is not Tyler leaving General Gov. The primary stays
    put and this adds alongside it, so a filter on either finds them.
    """
    cid, sector, category = body.get("id"), body.get("sector"), body.get("category")
    if not cid or not sector or not category:
        return {"error": "need a company, a sector and a category"}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    also = [a for a in (c.get("also") or [])
            if not (a.get("sector") == sector and a.get("category") == category)]
    dropped = len(also) != len(c.get("also") or [])
    if not dropped:
        if (c["sector"], c["category"]) == (sector, category):
            return {"error": "that is already its primary home"}
        also.append({"sector": sector, "category": category})
    c["also"] = also or None
    if c["also"] is None:
        c.pop("also", None)
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "also", why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    verb = "no longer also in" if dropped else "also filed under"
    return {"ok": True, "message": f"{c['name']} {verb} {sector} / {category}",
            "also": c.get("also") or []}


def act_place(body: dict) -> dict:
    """File a miscategorized company where it belongs, or say it is fine.

    Reuses act_move for the write, so placement goes through the one path
    that validates the whole file. Recording who ruled and what they were
    shown is the same bargain as everywhere else: it costs nothing now and
    cannot be added later.

    Two things this function used to get wrong, both of which made the craft
    signal report care that nobody had taken:

    A REASON NOBODY TYPED IS NOT A REASON. The "bucket is right" branch wrote
    the literal string "the bucket is right" whenever the why box was empty,
    and the why-coverage meter counts any non-empty why. So the one metric in
    this admin that measures care was being filled in by the admin itself. An
    untyped reason is now stored as null, exactly as the placement branch has
    always stored it, and the meter counts what a person actually wrote.

    AND A LOW-CONFIDENCE PROPOSAL CANNOT BE ACCEPTED IN SILENCE. Low
    confidence is the guesser saying it does not know; taking that placement
    with nothing typed records the machine's guess as a person's ruling, and
    the belt will hand you the next card in under a second. The owner's call,
    and the reason it is scoped this tightly: it costs nothing when somebody
    is really reading, it stops the blind run completely, and it is NOT asked
    on a high-confidence row a person simply agrees with - demanding an essay
    there would train people to type junk, which is worse than no reason.
    """
    cid = body.get("id")
    if not cid:
        return {"error": "need a company id"}
    why = (body.get("why") or "").strip()
    # What the person was shown, kept identically on both outcomes. "Bucket
    # is right" is not the absence of a ruling, it is the proposal being
    # OVERRULED, and it only counts as evidence about the guesser if the
    # record says what the guesser had proposed at the time.
    saw = {"was": body.get("was"),
           "proposed": body.get("proposed"),
           "confidence": body.get("confidence"),
           "description": body.get("description")}
    if body.get("keep"):
        bad = dismiss("miscategorized", cid, why or "",
                      by=(body.get("by") or "owner"), saw=saw)
        if bad:
            return {"error": bad}
        return {"ok": True, "message": "left where it is"}

    # Accepting = taking the proposal exactly as offered. Filing it somewhere
    # else is an overrule and needs no defence; it already disagrees.
    proposed = _seen_proposal({"saw": saw})
    chose = f"{body.get('sector')} / {body.get('category')}"
    if (body.get("confidence") or "").strip().lower() == "low" \
            and proposed and chose == proposed and not why:
        return {"error": "the guesser rated this one LOW confidence, so "
                         "taking its placement needs a line saying what you "
                         "saw that it did not. Nothing was written."}

    res = act_move({"id": cid, "sector": body.get("sector"),
                    "category": body.get("category")}, action="place")
    if res.get("error"):
        return res
    rulings = read("placement_rulings.json", {})
    rulings[cid] = {"sector": body.get("sector"), "category": body.get("category"),
                    "on": dt.date.today().isoformat(), "at": now(),
                    "by": (body.get("by") or "owner").strip(),
                    "why": why or None,
                    "saw": saw}
    bad = save_decisions("placement_rulings.json", rulings, "place-ruling",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return res


def act_save_website(body: dict) -> dict:
    """Save a website, then spend it: fetch the logo and probe for a board.

    A website is not the point - it is the key. On its own it changes
    nothing a visitor sees; what it unlocks is the company's own mark on
    every card and a shot at finding where they post jobs. Doing all three
    on one paste means the work pays immediately and visibly, instead of
    waiting for whenever the next bulk pass happens to run.

    Each step reports its own outcome and none can undo the one before it.
    The website is written first and stays written even if the logo 404s and
    the board probe finds nothing, because a confirmed website is worth
    keeping on its own.
    """
    cid = body.get("id")
    url, why = outward_url(body.get("url"))
    if not cid or not url:
        return {"error": why or "need a company and a URL"}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}

    c["website"] = url
    name = (body.get("name") or "").strip()
    if name and name.lower() != c["name"].lower():
        aka = set(c.get("also_known_as") or [])
        aka.add(c["name"])
        c["also_known_as"] = sorted(aka)
        c["name"] = name
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "save-website",
                                 why=(body.get("why") or ""),
                                 by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    steps = [f"website saved{' and renamed to ' + name if name else ''}"]

    # 2. the logo, straight away
    got_logo = False
    try:
        import fetch_logos
        _, ext, note = fetch_logos.fetch_one((cid, url))
        if ext:
            got_logo = True
            steps.append("logo found")
        else:
            steps.append("no logo on the site")
    except Exception as exc:  # noqa: BLE001 - a logo is never worth a 500
        steps.append("could not reach the site for a logo")

    # 3. the board, verified before it is written
    got_board = None
    try:
        ats_block, careers, notes = add_company.find_ats(url, paths=["/careers"])
        if ats_block:
            okay, why = add_company.verify(ats_block)
            if okay:
                companies = read_companies()
                c2 = next((x for x in companies if x["id"] == cid), None)
                if c2 is not None:
                    c2["ats"] = ats_block
                    if not validate(companies):
                        bad = save_companies(companies, "save-website",
                                 why=(body.get("why") or ""),
                                 by=(body.get("by") or "owner"))
                        if bad:
                            return {"error": bad}
                        got_board = ats_block["type"]
                        steps.append(f"board found: {got_board}")
            else:
                steps.append(f"board found but unreadable, left unknown")
        else:
            steps.append("no board on the site yet")
    except Exception as exc:  # noqa: BLE001
        steps.append("could not check for a job board just now")

    log = read("discovery_log.json", {})
    log[cid] = {"on": dt.date.today().isoformat(), "found": bool(got_board),
                "note": f"after website saved in admin: {steps[-1][:90]}"}
    write_atomic("discovery_log.json", log)

    return {"ok": True, "message": " \u00b7 ".join(steps),
            "logo": got_logo, "board": got_board}


def act_retry_board(body: dict) -> dict:
    """Re-probe one blocked company right now, instead of waiting a week.

    For when the block looked transient, or the website field was just
    fixed. Same probe, same verification: a slug is written only after a
    real fetch confirmed the board reads.
    """
    cid = body.get("id")
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    if not c.get("website"):
        return {"error": "no website on file - add one first"}
    # the website came off the record rather than out of this request, and a
    # record can be older than any rule about what may go in it
    url, bad = outward_url(c["website"])
    if not url:
        return {"error": f"the website on file is not fetchable: {bad}"}
    # two paths only: this runs inside a single-threaded server, and a
    # deliberate button press may wait seconds, not minutes
    ats_block, careers, notes = add_company.find_ats(url, paths=["/careers"])
    log = read("discovery_log.json", {})
    if ats_block:
        okay, why = add_company.verify(ats_block)
        if okay:
            c["ats"] = ats_block
            err = validate(companies)
            if err:
                return {"error": err}
            bad = save_companies(companies, "retry-board",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
            if bad:
                return {"error": bad}
            log[cid] = {"on": dt.date.today().isoformat(), "found": True,
                        "note": f"retry: {'; '.join(notes)[:120]}"}
            write_atomic("discovery_log.json", log)
            return {"ok": True, "message": f"{c['name']}: board found and "
                                          f"verified ({ats_block['type']})"}
    log[cid] = {"on": dt.date.today().isoformat(), "found": False,
                "note": f"retry: {'; '.join(notes)[:120] or 'still nothing'}"}
    write_atomic("discovery_log.json", log)
    return {"ok": True, "message": f"{c['name']}: still nothing readable - "
                                   f"{'; '.join(notes)[:90] or 'no marker found'}"}


def act_vendor_scope(body: dict) -> dict:
    """Rule one horizontal vendor. Stored by name-key so it is asked once.

    The ruling carries an author and a reason, not because anything reads
    them today, but because a ruling without them cannot be scored, trusted,
    or used to teach the classifier later, and neither can be added after
    the fact.
    """
    name, call = body.get("name"), body.get("call")
    if not name or call not in ("in", "sled", "out"):
        return {"error": "need a name and a call of in, sled or out"}
    d = read("vendor_scope_decisions.json", {})
    d[_vkey(name)] = {"call": call, "name": name,
                      "on": dt.date.today().isoformat(), "at": now(),
                      "by": (body.get("by") or "owner").strip(),
                      "why": (body.get("why") or "").strip() or None,
                      "saw": {"description": body.get("description"),
                              "website": body.get("website"),
                              "source_event": body.get("source_event")}}
    bad = save_decisions("vendor_scope_decisions.json", d, "vendor-scope",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    msg = {"in": "will be added as a full company",
           "sled": "will be added, public-sector roles only",
           "out": "left off the board"}[call]
    return {"ok": True, "message": f"{name}: {msg}"}


def q_submissions(companies, board) -> list:
    """Pending submissions, in the shape admin.html actually renders.

    THE SAME MISMATCH THE ACQUISITIONS QUEUE HAD. submissions.json stores `at`,
    `by` and `context`; RENDER.submissions reads `on`, `submitted_by` and
    `note`. So the row printed "sent undefined", never said who sent it, and
    swallowed the context field entirely - which on the one pending submission
    is a paragraph explaining that civira.com never mentions government
    anywhere and that this is a scope call rather than a regex's.

    The most useful thing on the card was the thing that did not render.

    Mapped here rather than in the page for the reason the acquisitions fix
    gives: the renderer's names are the intended ones, so making the producer
    agree is one direction of change.
    """
    subs = read("submissions.json", {"items": []})
    names = {c["id"]: c["name"] for c in companies}
    out = []
    for i in subs["items"]:
        if i.get("status") != "pending":
            continue
        at = i.get("at") or ""
        out.append({**i,
                    "company": names.get(i.get("company_id")),
                    # a timestamp is not a date, and the card says "sent <x>"
                    "on": i.get("on") or at[:10],
                    "submitted_by": i.get("submitted_by") or i.get("by"),
                    "note": i.get("note") or i.get("context")})
    return out


def q_leads(companies, board) -> list:
    """No board we can read, but a sales title was seen in the page text.

    Split out of the 707-row No-board-found pile because these are not the
    same job. A row here has EVIDENCE: govtech-dock's page scan found a
    quota-carrying title in the text of that careers page and could not
    enumerate the posting behind it. Somebody opening this page will find a
    live role most of the time.

    The other 632 are a person guessing where a company might hide a board.
    Mixing 75 warm doors into 707 cold ones is how the warm ones never get
    opened, and the public card already tells a visitor these are leads - the
    admin was the only place that could not see them.
    """
    orgs = {o["id"]: o for o in board.get("organizations", [])}
    out = []
    for c in companies:
        if is_dismissed("leads", c["id"]):
            continue
        o = orgs.get(c["id"]) or {}
        if not o.get("scan_lead"):
            continue
        h = c.get("hiring") or {}
        out.append({"id": c["id"], "name": c["name"], "sector": c["sector"],
                    "website": c.get("website"),
                    "board": (c.get("ats") or {}).get("ref"),
                    "note": h.get("note") or "",
                    "checked": h.get("checked"),
                    "tier": 1 if c["sector"] in ("General Gov", "Public Safety",
                                                 "Public Works", "Parks & Rec") else 2})
    out.sort(key=lambda r: (r["tier"], r["name"].lower()))
    return out


def q_proposals(companies, board) -> list:
    """What the agents proposed, waiting on a person.

    agents.py has written into data/agent_proposals.json since August and
    admin.py never imported it, so 85 proposals - 24 of them carrying real
    postings read off pages no fetcher can enumerate - have sat pending with
    nowhere to be accepted. The brief-out/proposal-in spine was built and its
    other end was missing.

    Every row shows what the agent SAW, because that is the rule: store the
    input alongside the answer or the ruling teaches the classifier nothing.
    A proposal claiming high confidence with no evidence is not shown as a
    confident one - intake already refuses those, and this queue would be the
    place they arrived if it ever stopped.
    """
    # A DICT keyed "read:<company-id>", not a list. read() enforces the shape
    # against its default and would hand back an empty list for a dict file,
    # silently - which is exactly what it did on the first run of this, and is
    # the shape contract working rather than failing.
    raw = read("agent_proposals.json", {})
    rows = list(raw.values()) if isinstance(raw, dict) else raw
    by_id = {c["id"]: c for c in companies}
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("status") != "pending":
            continue
        if is_dismissed("proposals", r.get("id", "")):
            continue
        c = by_id.get(r.get("id"))
        posts = r.get("postings") or []
        # WHOSE PAGE DID IT READ. The top two proposals in this queue are
        # Aladtec, read off tcpsoftware.com, and Nedap Identification Systems,
        # read off nedap.com's group careers page - the two traps CLAUDE.md
        # names by name. Accepting either would file a parent's requisitions
        # under a subsidiary, which is the false "Yes" this project exists to
        # refuse. The agent cannot know; the queue can say.
        warn = None
        ev, site = r.get("evidence") or "", (c or {}).get("website") or ""
        if ev and site:
            def _root(u):
                h = (urllib.parse.urlparse(u).hostname or "").lower().replace("www.", "")
                bits = h.split(".")
                return ".".join(bits[-2:]) if len(bits) >= 2 else h
            # An ATS host is not another company. greenhouse.io is the filing
            # cabinet; reading a company's own Greenhouse is exactly right, and
            # flagging it would bury the real warnings under noise. Dominion on
            # Paylocity and Fotokite on BambooHR were both flagged before this
            # and both are fine; Brightly on siemens.com is the one that is not.
            # edovo.org against edovo.com is one company with two TLDs, not
            # two companies. Same second-level name means same outfit.
            _name = lambda u: _root(u).rsplit(".", 1)[0]
            if (_root(ev) and _root(site) and _root(ev) != _root(site)
                    and _name(ev) != _name(site)
                    and _root(ev) not in ATS_HOSTS):
                warn = (f"read off {_root(ev)}, which is not {r.get('name')}'s own "
                        f"domain - check whose requisitions these are before "
                        f"accepting")
        out.append({
            "id": r.get("id"), "name": r.get("name") or (c or {}).get("name"),
            "warn": warn,
            "kind": r.get("kind"), "by": r.get("by"), "at": r.get("at"),
            "confidence": r.get("confidence"),
            "evidence": r.get("evidence"),
            "none_found": bool(r.get("none_found")),
            "n": len(posts),
            "postings": posts[:8],
            "saw": r.get("saw"),
            "sector": (c or {}).get("sector"),
        })
    # the ones that found something first: a proposal with postings is a
    # decision worth making today, and "read produced nothing" is a record
    # rather than a task.
    out.sort(key=lambda r: (r["none_found"], not r["warn"], -(r["n"] or 0),
                           r["name"] or ""))
    return out


QUEUES = {"proposals": q_proposals, "leads": q_leads, "boardfound": _q_board_proposals, "founded": q_founded, "miscategorized": q_miscategorized, "vendors": q_vendor_scope, "scope": q_scope, "submissions": q_submissions, "duplicates": q_duplicates, "websites": q_websites, "boards": q_boards, "blocked": q_blocked,
          "placement": q_placement, "unclassified": q_unclassified,
          "acquisitions": q_acquisitions, "review": q_review,
          "calendar": _q_calendar}

LABEL = {"proposals": "Agent proposals", "leads": "Warm leads", "boardfound": "Boards we found", "founded": "Founding year", "miscategorized": "Wrong bucket", "vendors": "Vendor scope", "scope": "Scope review", "submissions": "Submissions", "duplicates": "Duplicates", "websites": "Missing websites",
         "boards": "No board found", "blocked": "Blocked boards", "placement": "Wrong placement",
         "unclassified": "Unclassified roles", "acquisitions": "Acquisitions",
         "review": "Website review", "calendar": "Conference dates"}


# ---------------------------------------------------------------- actions

# Fields that name another company rather than describing this one. They are
# the only fields where inheriting a value can turn a fact into nonsense.
SELF_POINTERS = ("parent", "board_owner")


def _points_at(value, company: dict) -> bool:
    """Does this pointer name the company it would be written onto?"""
    if not isinstance(value, str):
        return False
    v = ident(value)
    return bool(v) and v in {ident(company.get("name") or ""),
                             ident((company.get("id") or "").replace("-", " "))}


def _merge_aliases(keep_aliases, drop_aliases, drop_name: str) -> list:
    """Every name either side answered to, in a stable order, no duplicates."""
    out, seen = [], set()
    for name in list(keep_aliases or []) + list(drop_aliases or []) + [drop_name]:
        if not isinstance(name, str) or not name.strip():
            continue
        key = ident(name) or name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name.strip())
    return out


def _merge_notes(keep_notes, drop_notes) -> list:
    """Both records' notes, deduplicated by their text.

    The same evidence note was written onto four sibling brands at once, so
    concatenating without a dedupe would stack four identical paragraphs on
    the survivor.
    """
    out, seen = [], set()
    for note in list(keep_notes or []) + list(drop_notes or []):
        if not isinstance(note, dict):
            continue
        key = (note.get("text") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(note)
    return out


def act_merge(body: dict) -> dict:
    """Fold one record into another. The survivor keeps every field it has and
    inherits the ones it is missing, so a merge never loses research."""
    if body.get("keep") == body.get("drop"):
        # merging a record into itself no-ops the inheritance loop and then
        # deletes the id from the file - reported as a successful merge. The
        # UI cannot send this; the API refused nothing. Now it does.
        return {"error": "keep and drop are the same company"}
    keep_id, drop_id = body.get("keep"), body.get("drop")
    companies = read_companies()
    keep = next((c for c in companies if c["id"] == keep_id), None)
    drop = next((c for c in companies if c["id"] == drop_id), None)
    if not keep or not drop:
        return {"error": "company not found"}
    filled = []
    for k, v in drop.items():
        if k in ("id", "name"):
            continue
        # A child folded into its own parent carries parent/board_owner
        # pointing AT the survivor. Inheriting one of those makes a company
        # its own parent, which the site renders as "part of itself" and
        # which no later reader can tell apart from a real ownership fact.
        # A pointer at anyone else is still ordinary research and is kept.
        if k in SELF_POINTERS and _points_at(v, keep):
            continue
        if keep.get(k) in (None, "", {}, []) and v not in (None, "", {}, []):
            keep[k] = v
            filled.append(k)
        # an unknown ATS never wins over a discovered one
        if k == "ats" and (keep.get("ats") or {}).get("type") in (None, "unknown") \
                and (v or {}).get("type") not in (None, "unknown"):
            keep["ats"] = v
            filled.append("ats")
    # Two list fields are UNIONED rather than inherited-if-missing, because
    # "the survivor already has some" is not a reason to throw the rest away.
    # also_known_as is the field whose entire job is that a dropped name still
    # finds the record; merging a third brand into a survivor that already had
    # one alias silently dropped the second brand's aliases before this.
    # notes are what a person or an agent actually saw, with a date and an
    # author - the most expensive thing in the record to re-acquire.
    keep["also_known_as"] = _merge_aliases(keep.get("also_known_as"),
                                           drop.get("also_known_as"),
                                           drop["name"])
    notes = _merge_notes(keep.get("notes"), drop.get("notes"))
    if notes:
        keep["notes"] = notes
    # `also` is the third union field, and it carries the drop's PRIMARY
    # placement too. A duplicate pair is often the same vendor filed on two
    # shelves - that is frequently WHY there are two records - and the merge
    # was keeping only the survivor's shelf: drop's sector/category vanished
    # whenever keep already had any `also` list, so a filter on that shelf no
    # longer found the vendor. A placement is research about where a buyer
    # looks for them; a merge never loses research.
    merged_also = list(keep.get("also") or [])
    have = {(a.get("sector"), a.get("category")) for a in merged_also}
    have.add((keep.get("sector"), keep.get("category")))   # never also-yourself
    for a in (drop.get("also") or []) + [{"sector": drop.get("sector"),
                                          "category": drop.get("category")}]:
        pair = (a.get("sector"), a.get("category"))
        if pair in have or not all(pair):
            continue
        have.add(pair)
        merged_also.append({"sector": a["sector"], "category": a["category"]})
        filled.append("also")
    if merged_also:
        keep["also"] = merged_also
    remaining = [c for c in companies if c["id"] != drop_id]
    err = validate(remaining)
    if err:
        return {"error": err}
    bad = save_companies(remaining, "merge", why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"merged {drop['name']} into {keep['name']}"
                                   + (f", inherited {', '.join(sorted(set(filled)))}"
                                      if filled else "")}


def act_patch(body: dict) -> dict:
    """Edit one company's fields. Validation runs on the whole file, so a change
    that breaks a sector/category pairing is refused rather than written."""
    companies = read_companies()
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    if not c:
        return {"error": "company not found"}
    # also_known_as is here for renames: the merge rule holds everywhere -
    # a dropped name is kept as an alias, never discarded
    allowed = {"name", "sector", "category", "website", "description",
               "location", "year_founded", "vendor_type", "parent", "ats_note",
               "also_known_as"}
    fields = body.get("fields") or {}
    # A patch that changes nothing must not report that it changed something.
    # This returned {"ok": True, "message": "updated <name>"} for a body whose
    # fields dict was empty or held only keys outside `allowed` - so a caller
    # that put a field at the top level instead of inside `fields` was told the
    # write landed, and the record was untouched. Silent no-ops are how a
    # correction gets believed and never made.
    touched = [k for k in fields if k in allowed]
    if not touched:
        rejected = sorted(set(fields) - set(allowed))
        return {"error": ("nothing to change - "
                          + (f"{', '.join(rejected)} cannot be patched here"
                             if rejected else "no fields were given")
                          + f". Editable: {', '.join(sorted(allowed))}")}
    # NO ats BACKDOOR. Two lines here used to write fields["ats"] straight onto
    # the company after the allowlist had excluded it - so an ats-ONLY patch
    # was refused with "ats cannot be patched here" while ats smuggled in next
    # to any allowed field landed unverified. That bypasses every gate a board
    # change has: act_set_board's explicit-action check and act_board_proposal's
    # live verify with its MISMATCH refusal. A wrong ref is the worst write
    # this file can make - point "prepared" at greenhouse/axon and the next
    # refresh reports Axon's ~500 requisitions as Prepared's, the exact false
    # Yes the proposal flow exists to refuse. Board changes go through
    # /api/set-board, which verifies, or they do not happen.
    if "ats" in fields:
        return {"error": "ats cannot be patched here, with or without other "
                         "fields - board changes go through set-board, which "
                         "verifies the ref against the live board first. "
                         "Nothing was written."}
    for k in touched:
        c[k] = fields[k]
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "patch", why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"updated {c['name']}"}


# --------------------------------------------------------- reaching outward
#
# Every fetch this server makes is aimed by whoever is talking to it, and the
# reply comes back to them: a page title, a list of anchors. So the target is
# not a detail, it is the whole question, and the answer has two halves.
#
# The first half already exists in this repo. functions/api/submit.js::validUrl
# refuses anything that is not http(s), anything with credentials in it, and
# anything without a dotted host. clean_url() below is that rule, plus the half
# a Cloudflare Worker does not need: a name is free to point at 169.254.169.254,
# so someone has to look at where it actually lands.


# Captured before anything can patch it, so only_public_hosts() has something
# honest to restore and outward_url() has a resolver that answers rather than
# raises.
_real_getaddrinfo = socket.getaddrinfo


def _public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Is this address out on the internet, rather than in here with us.

    is_global leads, and the library owns the answer, because a hand-kept list
    of "the private ranges" is precisely what let 100.64.0.0/10 through. RFC
    6598 carrier-NAT space is not private, not loopback, not link-local and not
    reserved by any flag below - and it routes straight at an ISP's own
    equipment. Python already knows: ip_address("100.64.1.1").is_global is
    False. Ask it instead of extending the list, or the next range nobody has
    heard of gets through the same way.

    The named flags stay underneath it. They are not redundant across versions,
    and quietly losing loopback here would be a different order of mistake than
    being slightly over-strict.

    BUT is_global MOVES BETWEEN PYTHON VERSIONS, and that is not a small
    caveat - it broke the daily refresh for two days. Checked against a real
    3.12 rather than guessed: 2002::/16 is the one that moved. 3.11 calls
    2002:808:808:: global, 3.12 does not, because 3.12 picked up the IANA
    special-purpose registry. Every other address this gate is tested on
    answers the same on both, including 100.64.0.0/10 and the AS112 and AMT
    ranges, which an earlier version of this comment wrongly listed as having
    moved.

    The move is in the SAFE direction, from global to not, so the gate only
    gets stricter as Python updates - which is why leaning on the library is
    still right. What is not safe is a TEST that pins the permissive answer:
    one did, for 6to4, and it passed here and failed on the runner. Anything
    whose classification is contested gets decided below rather than asserted
    above.

    Then the tunnels, which are the other half of the same lesson. An IPv6
    address can carry an IPv4 address as its passenger and answer every
    question above as an ordinary global address: 2002:7f00:1:: IS 127.0.0.1
    once a 6to4 relay unwraps it, and nothing about the outer address says so.
    The library hands the passenger over - .sixtofour, .ipv4_mapped, .teredo -
    so each one is asked the same question as itself. A 6to4 address wrapping a
    real public host still passes, because its passenger does.
    """
    if not ip.is_global:
        return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # 6to4 IS REFUSED OUTRIGHT, whatever its passenger is. This used to
        # depend on the passenger, and that made the answer depend on the
        # PYTHON VERSION: 3.11 says 2002:808:808:: is_global, 3.12 says it is
        # not, because 3.12 picked up the IANA special-purpose registry's view
        # of 2002::/16. A security gate that answers differently on two
        # interpreters is broken in a way no test on one of them can see - and
        # this one broke the daily refresh for two days, on the runner rather
        # than here.
        #
        # Deciding it ourselves, and deciding it the safe way: 6to4 was
        # deprecated by RFC 7526 in 2015. A URL aimed at 2002::/16 in 2026 is
        # far more likely to be someone tunnelling at our loopback than a real
        # host, and the two mistakes do not cost the same. Refusing a genuine
        # 6to4 host costs a link nobody was going to click; accepting a forged
        # one costs the admin.
        if ip.sixtofour is not None:
            return False
        # the rest still hand their passenger over to be asked the same
        # question: each is an IPv4Address or None, so recursion is one deep
        inner = [ip.ipv4_mapped, *(ip.teredo or ())]
        if any(p is not None and not _public(p) for p in inner):
            return False
    return True


def clean_url(raw: str) -> str | None:
    """Shape only: is this a link, and is it aimed outward on its face.

    A URL with no host is not a URL. An empty box used to become 'https://',
    which fetched, returned nothing, and offered to save a page-scan board
    whose ref was literally 'https://'.

    Credentials are refused because 'http://user:pass@evil.com@127.0.0.1/'
    reads as evil.com to a person and resolves to 127.0.0.1 to a fetcher -
    the disagreement IS the trick. A literal private address is refused here
    too, since that needs no lookup to see.

    Deliberately does not resolve the name. This also runs on paths that only
    STORE a url - a submission, a website field - and a company whose DNS is
    down, or a laptop on a plane, must not make a real address unsaveable.
    Resolution belongs to the fetch gate, below.
    """
    u = (raw or "").strip()
    # a newline in the middle would let a caller write a second request line
    # into anything downstream that builds a request by hand
    if not u or re.search(r"[\s\x00-\x1f\x7f]", u):
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "https://" + u
    try:
        parts = urllib.parse.urlsplit(u)
        host = parts.hostname
        creds = parts.username or parts.password
    except ValueError:                    # a malformed ipv6 literal, mostly
        return None
    if parts.scheme not in ("http", "https") or creds or not host:
        return None
    if "." not in host or len(host) < 4:
        return None
    try:
        if not _public(ipaddress.ip_address(host)):
            return None
    except ValueError:
        pass                              # a name, not a literal. see below.
    return u


def outward_url(raw: str) -> tuple[str | None, str | None]:
    """(url, error) for the actions that fetch whatever they are handed.

    Answers in a sentence instead of a stalled connection, which is the only
    reason it looks the name up itself. It is not the enforcement - a name
    can resolve differently here than it does at the socket a moment later -
    and only_public_hosts() is.
    """
    url = clean_url(raw)
    if not url:
        return None, "that does not look like a company website"
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        # the real resolver on purpose: inside only_public_hosts() the patched
        # one raises, and this exists to produce a readable message
        infos = _real_getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None, f"{host} does not resolve"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not _public(ip):
            return None, (f"{host} points at {ip}, which is inside this "
                          f"network - this fetches on your behalf, so it "
                          f"will not go there")
    return url, None


class PrivateAddress(OSError):
    """Raised in place of opening a connection to an address in here."""


def _guarded_getaddrinfo(host, port, *a, **kw):
    infos = _real_getaddrinfo(host, port, *a, **kw)
    for info in infos:
        if info[0] not in (socket.AF_INET, socket.AF_INET6):
            raise PrivateAddress(f"refusing a non-IP connection to {host}")
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not _public(ip):
            raise PrivateAddress(f"{host} resolves to {ip}, which is not public")
    return infos


@contextlib.contextmanager
def only_public_hosts():
    """Refuse, at connect time, any hop that lands inside this network.

    Checking the url a person pasted is not enough on its own, because the
    fetchers follow redirects and then read the target's own hrefs - so the
    person who chose the first hop does not choose the second, and the second
    is where a redirect to 127.0.0.1 goes. Patching the resolver for the
    length of one action puts the check in the only place that sees every
    hop, including the ones inside requests and inside fetch_logos.

    It also closes the gap the pre-check cannot: this is the address the
    socket will actually use, so a name that answers public once and loopback
    a second later is caught on the answer that matters.

    Process-wide, and safe here because the server handles one request at a
    time and no admin action has any business dialling a private address.

    What it does not close, said plainly: it only sees resolution that goes
    through socket.getaddrinfo, so anything reaching the network another way -
    a C-level resolver, an HTTP proxy that resolves for us - is outside it. It
    is installed for the length of a POST action and nothing else; a GET route
    that grew a fetch would need its own. And it refuses a host only when the
    addresses it hands back are private, so a name that answers public here
    and private to somebody else's resolver is somebody else's problem.
    """
    socket.getaddrinfo = _guarded_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = _real_getaddrinfo


def act_verify_website(body: dict) -> dict:
    """Check a URL before writing it. A live page is not evidence - parked
    domains and unrelated businesses all answer on the obvious name - so this
    reports what the page says about itself and lets a person decide."""
    url, why = outward_url(body.get("url"))
    name = body.get("name") or ""
    if not url:
        return {"error": why if body.get("url") else "enter a URL first"}
    try:
        r = add_company.fetch(url)
        html = r[0] if isinstance(r, tuple) else r
    except Exception as exc:
        return {"error": f"could not fetch: {type(exc).__name__}"}
    # Zero bytes back is not "this page says nothing about itself". It is "we
    # learned nothing". airgus.com answers our fetcher with HTTP 202 and an
    # empty body, and the panel duly reported that the page had no title and
    # never named the company - two negative facts invented out of a failed
    # read. That is the asymmetric error the whole repo is built around, so
    # the unreadable case now says so and offers no ruling at all.
    if not (html or "").strip():
        # Said plainly, and NOT as a warning. The first version explained the
        # epistemics - "we have learned nothing about this address, which is
        # different from learning it is wrong" - in red, next to a Save button.
        # True, and it read as "something is wrong here" when the likeliest
        # thing is that the address is perfectly good and their host dislikes
        # robots. The honesty is kept; the philosophy lecture is not.
        return {"ok": True, "unreadable": True, "url": url, "tone": "neutral",
                "message": ("Their site blocks automated readers, so we could "
                            "not open it. That is about our reader, not their "
                            "address \u2014 this link may well be right. Open it "
                            "in a tab: if it is them, hit Save.")}
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", title.group(1)).strip()[:140] if title else ""
    parked = bool(find_websites.PARKED.search(html[:4000]))
    base = url.split("//", 1)[1].split("/")[0].replace("www.", "").rsplit(".", 1)[0]
    aliases = body.get("aliases") or []
    if not aliases and body.get("id"):
        c0 = next((x for x in read_companies()
                   if x["id"] == body["id"]), None)
        aliases = (c0 or {}).get("also_known_as") or []
    note = find_websites.identity_note(html, name, base, aliases)
    return {"ok": True, "title": title, "parked": parked,
            "identifies": note["ok"], "identity": note,
            "url": url}


def act_verify_board(body: dict) -> dict:
    """Detect the ATS behind a careers URL and prove it returns this company's
    jobs. slug_matches is what keeps an off-site careers link from wiring a
    company to somebody else's board, which is how acquisitions surface."""
    url, why = outward_url(body.get("url"))
    if not url:
        return {"error": why if body.get("url") else "enter a careers URL first"}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    try:
        block, note, _ = add_company.find_ats(url)
    except Exception as exc:
        return {"error": f"could not read that page: {type(exc).__name__}"}
    if not block:
        titles = []
        try:
            import ats as ats_mod
            titles = [j.get("title", "") for j in ats_mod.fetch_html_titles(url)][:8]
        except Exception:
            pass
        return {"ok": True, "ats": {"type": "html", "ref": url},
                "note": note or "no known ATS detected; would be stored as a page scan",
                "jobs": len(titles), "slug_ok": None, "titles": titles,
                # A scan that read nothing has not proved the board is empty, it
                # has proved we cannot read it. Storing that as a board makes an
                # unreadable page look like a monitored one.
                "empty_scan": not titles}
    try:
        ok, msg = add_company.verify(block)
    except Exception as exc:
        ok, msg = False, f"{type(exc).__name__}"
    slug_ok = None
    ref = block.get("ref")
    if c and isinstance(ref, str):
        slug_ok = discover_ats.slug_matches(ref, c)
    titles = []
    try:
        import ats as ats_mod
        titles = [j.get("title", "") for j in ats_mod.fetch(block)][:8]
    except Exception:
        pass
    return {"ok": True, "ats": block, "note": msg, "verified": ok,
            "slug_ok": slug_ok, "titles": titles, "jobs": len(titles)}


def act_set_board(body: dict) -> dict:
    companies = read_companies()
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    if not c:
        return {"error": "company not found"}
    block = body.get("ats") or {}
    if block.get("type") not in ATS_TYPES:
        return {"error": f"unknown ats type {block.get('type')}"}
    c["ats"] = {"type": block["type"], "ref": block.get("ref")}
    c["ats_note"] = body.get("note") or "set by hand in admin"
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "set-board",
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{c['name']} now points at {block['type']}"}


def act_set_family(body: dict) -> dict:
    """Assign a role family to one exact title.

    This is data, not a classifier rule. A title like 'Manager' names a rank with
    no function, so there is no pattern to write - the judgment belongs to the
    posting, and roles.py reads these overrides on top of its patterns. A title
    that does suggest a rule still gets one in roles.py with a selftest case,
    per the house rule.
    """
    title, fam = body.get("title"), body.get("family")
    if fam not in roles.LABEL:
        return {"error": f"unknown family {fam}"}
    over = read("family_overrides.json", {})
    over[title] = {"family": fam, "on": dt.date.today().isoformat()}
    write_atomic("family_overrides.json", over)
    return {"ok": True, "message": f"{title} -> {roles.LABEL[fam]}"}


# WHAT DOES NOT READ LIKE A JOB, AND WHY IT IS FLAGGED RATHER THAN DROPPED.
#
# A capture used to arrive only from the bookmarklet, whose href regex filters
# nav chrome on the page before a person ever sees it. The paste form takes
# arbitrary JSON by design - that is its purpose - so the moment anything else
# produces that JSON, the server is the only filter left. `roles.is_junk`
# was supposed to be it and catches none of this: Cookie Preferences,
# CHALLENGES, SOLUTIONS, Privacy Policy and View all jobs all pass it, which
# is the exact list the harvester's first rule was written about.
#
# BUT NOT DROPPED, and this is the whole design decision. ats._TITLEISH is a
# word allowlist, and measured against real titles it rejects Head of Sales,
# Enterprise Sales, Territory Sales, Business Development, VP Marketing and
# SDR - which are not edge cases here, they are the roles this board exists to
# find. Filtering on it would silently delete the most valuable captures to
# avoid a bit of nav chrome, which is the asymmetric error wearing a tidy hat.
#
# So: a title that neither reads like a job NOR carries any sales vocabulary is
# reported back with the count, and the person who pasted it decides. Nothing
# is removed on a guess.
_SALESY = re.compile(r"\b(sales|account|business development|territory|revenue|"
                     r"partnerships?|customer success|sdr|bdr|ae|head of|vp|"
                     r"vice president|chief|director|principal)\b", re.I)


def _reads_like_nav(title: str) -> bool:
    """True when a captured title looks like page furniture rather than a job."""
    return not (ats._TITLEISH.search(title) or _SALESY.search(title))


def act_capture(body: dict) -> dict:
    """Take job titles a person is looking at in their own browser.

    537 careers pages on file have no enumerable job list at all - not a JS
    shell hiding one, genuinely no job anchors - so rendering them recovers
    nothing. A person opening the page sees the jobs anyway, because their
    browser runs the widget, the iframe and the session that ours does not.
    This is the shortest path from "they can see it" to "the board has it".

    The capture is whatever is on screen when a person clicks, one page at a
    time. It does not crawl, paginate, scroll or run on its own, which is the
    line between reading a page you opened and harvesting a site.
    """
    cid = body.get("company_id")
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if not c:
        return {"error": "pick a company to attribute these to"}
    raw = body.get("jobs") or []
    if not raw:
        return {"error": "no job titles in that capture"}
    man = read("manual.json", {"checks": {}, "postings": []})
    today = dt.date.today().isoformat()
    existing = {p["id"] for p in man["postings"]}
    added = 0
    suspect = []
    for j in raw:
        title = (j.get("title") or "").strip()
        if not title or roles.is_junk(title) or roles.is_evergreen(title):
            continue
        if _reads_like_nav(title):
            suspect.append(title)
        pid = f"{cid}::{title}"
        if pid in existing:
            continue
        loc = (j.get("location") or "").strip()
        terr = roles.territory(loc, title)
        # The extension's single-posting mode sends the JD body - the thing the
        # board never has and scoring always wants. Kept on the manual record
        # with provenance; the dock itself only ever renders the title.
        jd = (j.get("jd_text") or "").strip()[:20000]
        man["postings"].append({
            "id": pid, "company": c["name"], "company_id": cid,
            **({"jd_text": jd} if jd else {}),
            "title": title, "family": roles.family(title),
            "quota_carrying": roles.is_quota_carrying(title),
            "seniority": roles.seniority(title),
            "states": terr["states"], "region": terr["region"],
            "work_mode": terr["work_mode"],
            "location": loc, "is_us": roles.is_us(loc, title),
            "url": j.get("url") or body.get("page_url"),
            "sector": c["sector"], "category": c["category"],
            "first_seen": today,
            "captured_from": body.get("page_url"),
        })
        existing.add(pid)
        added += 1
    man["checks"][cid] = {"checked_on": today, "by": "capture",
                          "source": body.get("page_url")}
    write_atomic("manual.json", man)
    # Named, not counted. "2 look like page furniture" tells somebody nothing;
    # seeing "Cookie Preferences" in the message is what makes them go back and
    # look at what they pasted.
    flag = ("" if not suspect else
            f" - but {len(suspect)} do not read like job titles and were kept "
            f"anyway: {', '.join(repr(t) for t in suspect[:4])}"
            + (f" and {len(suspect) - 4} more" if len(suspect) > 4 else "")
            + ". Nothing was dropped; check them on the company card.")
    return {"ok": True, "added": added, "company": c["name"],
            "suspect": suspect,
            "message": f"{added} posting(s) captured for {c['name']}{flag}"
                       + (f", {len(raw) - added} skipped as duplicate or junk"
                          if len(raw) - added else "")}


def act_submit(body: dict) -> dict:
    """Take an outside submission - a company, or a job at a company.

    Submissions are claims, not facts. Nothing here writes to companies.json or
    the board; it lands in a queue with whoever sent it and waits for a person.
    The same rule the fact bank runs on: an outside assertion never becomes
    canon without review, because the cost of a wrong company in a market map
    is paid by everyone reading it.
    """
    kind = body.get("kind")
    if kind not in ("company", "job"):
        return {"error": "kind must be company or job"}
    url = clean_url(body.get("url"))
    if kind == "company" and not url:
        return {"error": "a company submission needs a URL"}
    if kind == "job" and not (body.get("title") or "").strip():
        return {"error": "a job submission needs a title"}
    subs = read("submissions.json", {"items": []})
    sid = f"{kind}-{len(subs['items']) + 1}-{dt.date.today().isoformat()}"
    subs["items"].append({
        "id": sid, "kind": kind, "on": dt.date.today().isoformat(),
        "status": "pending",
        "url": url, "name": (body.get("name") or "").strip(),
        "title": (body.get("title") or "").strip(),
        "company_id": body.get("company_id"),
        "location": (body.get("location") or "").strip(),
        "note": (body.get("note") or "").strip(),
        "submitted_by": (body.get("submitted_by") or "").strip(),
    })
    write_atomic("submissions.json", subs)
    return {"ok": True, "id": sid,
            "message": "submitted for review; nothing is published until a "
                       "person approves it"}


def act_resolve_submission(body: dict) -> dict:
    """Approve or reject one submission.

    Approving a company runs the same intake add_company.py does - identity and
    sector are guessed from the page and shown - but still lands as a proposal
    to check, never a silent write. Approving a job writes it to manual.json,
    where an automated run will not delete it.
    """
    subs = read("submissions.json", {"items": []})
    item = next((i for i in subs["items"] if i["id"] == body.get("id")), None)
    if not item:
        return {"error": "submission not found"}
    action = body.get("action")
    if action == "reject":
        item["status"] = "rejected"
        item["resolved_on"] = dt.date.today().isoformat()
        item["why"] = body.get("why", "")
        write_atomic("submissions.json", subs)
        return {"ok": True, "message": "rejected"}
    if action != "approve":
        return {"error": "action must be approve or reject"}

    if item["kind"] == "job":
        r = act_capture({"company_id": item.get("company_id"),
                         "page_url": item.get("url"),
                         "jobs": [{"title": item["title"], "url": item.get("url"),
                                   "location": item.get("location")}]})
        if r.get("error"):
            return r
        # act_capture returning ok is not the same as act_capture ADDING the
        # posting: its filter drops junk and evergreen titles ("General
        # Application") and duplicates, and reports added: 0. Stamping that
        # "approved" published nothing while telling the submitter and the
        # queue it had - a silent no-op wearing a success message, the same
        # species act_patch was cured of. The reviewer gets the reason and the
        # submission stays pending for a real decision (reject it, or fix the
        # title and approve again).
        if not r.get("added"):
            return {"error": ("approving this added nothing to the board - "
                              + (r.get("message") or "the title was filtered "
                                 "as junk, evergreen, or already present")
                              + ". The submission stays pending; reject it if "
                              "it should not publish.")}
        item["status"] = "approved"
    else:
        fields = body.get("fields") or {}
        companies = read_companies()
        # Always derived, never fields["id"]. The reviewer picks the NAME; the
        # id follows from it. Letting the request name the id let a submission
        # carry "../../.." through approval and out of the repo.
        cid = slug(fields.get("name") or item["name"])
        if not cid:
            return {"error": "that submission has no name to build an id from"}
        if any(c["id"] == cid for c in companies):
            return {"error": f"{cid} is already tracked"}
        companies.append({
            "id": cid, "name": fields.get("name") or item["name"],
            "sector": fields.get("sector"), "category": fields.get("category"),
            "website": item.get("url"),
            "description": fields.get("description") or item.get("note"),
            "govtech": True, "vendor_type": "GovTech Product",
            "ats": {"type": "unknown", "ref": None},
            "hiring": {"roles": [], "status": "Unknown", "checked": None, "note":
                       "added from a submission; board not discovered yet"},
            "source": "submission", "added_on": dt.date.today().isoformat(),
        })
        err = validate(companies)
        if err:
            return {"error": err}
        bad = save_companies(companies, "resolve-submission",
                             why=(body.get("why") or ""),
                             by=(body.get("by") or "owner"))
        if bad:
            return {"error": bad}
        item["status"] = "approved"
        item["company_id"] = cid
    item["resolved_on"] = dt.date.today().isoformat()
    write_atomic("submissions.json", subs)
    return {"ok": True, "message": f"approved {item.get('name') or item.get('title')}"}


def act_inspect_submission(body: dict) -> dict:
    """Guess identity and sector for a submitted company URL, showing evidence.

    Same routine intake uses. Low confidence is reported as low - a submission
    that cannot be placed confidently should be placed by hand, not filed
    wherever one incidental keyword pointed.
    """
    url, why = outward_url(body.get("url"))
    if not url:
        return {"error": why if body.get("url") else "no URL on that submission"}
    try:
        r = add_company.fetch(url)
        html = r[0] if isinstance(r, tuple) else r
        name, desc = add_company.guess_identity(html, url)
    except Exception as exc:
        return {"error": f"could not read that page: {type(exc).__name__}"}
    sec, cat, conf, why = add_company.guess_sector(f"{name} {desc}".lower())
    return {"ok": True, "name": name, "description": desc,
            "sector": sec, "category": cat, "confidence": conf, "why": why}


def act_search_companies(body: dict) -> dict:
    """Name lookup for the capture overlay, which has no company list of its own."""
    q = norm(body.get("q") or "")
    if len(q) < 2:
        return {"results": []}

    # WHAT THE BOARD ALREADY KNOWS, sent back with each hit. The overlay used
    # to get a name and a sector and nothing else, so it could not tell the
    # person that the company they were about to capture is already read every
    # night by the crawler. On 2026-09-02 the very first real capture was
    # BusPatrol - Ashby, thirteen postings already on the board - and the tool
    # said nothing, then filed two of Ashby's own tabs as jobs.
    #
    # None of this is new data. It is three fields already on the record,
    # carried to the one place the decision is actually made.
    live = {}
    try:
        for p in (json.loads((DATA / "board.json").read_text())
                  .get("postings") or []):
            cid = p.get("company_id")
            if cid:
                live[cid] = live.get(cid, 0) + 1
    except Exception:                                   # noqa: BLE001
        live = {}

    out = []
    for c in read_companies():
        n = norm(c["name"])
        if q in n:
            a = c.get("ats") or {}
            out.append({"id": c["id"], "name": c["name"], "sector": c["sector"],
                        "ats_type": a.get("type"),
                        "postings": live.get(c["id"], 0),
                        "hiring_note": (c.get("hiring") or {}).get("note"),
                        "rank": 0 if n.startswith(q) else 1})
    out.sort(key=lambda r: (r["rank"], len(r["name"])))
    return {"results": out[:12]}


def act_worklist(body: dict) -> dict:
    """What to go and look at next, for the capture extension.

    READ-ONLY, and that is what makes it safe to leave open. OPEN_ACTIONS is
    the set the extension may call without the console code, and the line
    those actions already draw is not "no writes" - `capture` writes
    manual.json and `submit` writes submissions.json. The line is that an open
    action may write to a STAGING file and never to the map. This one writes
    nothing at all.

    Four queues, chosen because each is a task that ends with a person on
    somebody's website - which is the only kind the extension can help with:

      boards    685  read and yielded no board a fetcher can use
      founded   619  no founding year on file
      blocked   203  the probe was turned away, so we learned nothing
      websites   40  no website on file at all

    The order inside each is the queue's own and is not re-sorted here.
    q_boards in particular sorts by conference floor, most-exhibited first,
    because that list is worked by floor rather than alphabetically - the
    owner is standing on one.
    """
    which = (body.get("queue") or "boards").strip().lower()
    try:
        limit = max(1, min(50, int(body.get("limit") or 12)))
    except (TypeError, ValueError):
        limit = 12

    builders = {"boards": q_boards, "founded": q_founded,
                "blocked": q_blocked, "websites": q_websites}
    if which not in builders:
        return {"error": f"unknown queue {which!r}",
                "queues": sorted(builders)}

    companies = read_companies()
    board = json.loads((DATA / "board.json").read_text())
    rows = builders[which](companies, board)

    out = []
    for r in rows[:limit]:
        out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "sector": r.get("sector"),
            "website": r.get("website"),
            # The floors this company stands on. The reason the boards queue
            # is worth working in this order, so it travels with the row.
            "events": (r.get("events") or [])[:3],
            "note": r.get("probe_note") or r.get("note") or r.get("why"),
        })
    return {"queue": which, "total": len(rows), "rows": out,
            "counts": {k: len(v(companies, board)) for k, v in builders.items()}}


def act_identify(body: dict) -> dict:
    """Does the page a person is standing on actually belong to this company?

    READ-ONLY, and the reason it exists is a page that did not. The first hour
    of real capturing filed twelve UK IT-support roles from airitcareers.co.uk
    - Air IT Group, "the UK's #1 MSP" - against Air-Transport IT Services of
    Orlando, an airport-technology vendor. Same name, different company. The
    panel had told the person what the board knew about the COMPANY and never
    checked that the PAGE was that company's.

    find_websites.identifies() is the check this project already trusts for
    exactly that question - CLAUDE.md calls it "the only thing standing between
    a squatter and the dataset". It reads a page's own identity fields: title,
    description/og meta, h1. The extension has the live document, so it sends
    those three and this rebuilds the fragment identifies() reads. Nothing is
    fetched; the person is already looking at the page.

    It never loosens the check. A false "not this company" costs a moment's
    doubt; a false "yes" is twelve wrong postings on a public board.
    """
    cid = (body.get("company_id") or "").strip()
    c = next((x for x in read_companies() if x["id"] == cid), None)
    if not c:
        return {"error": f"no company with id {cid!r}"}
    title = (body.get("title") or "").strip()[:300]
    meta = (body.get("meta") or "").strip()[:300]
    h1 = (body.get("h1") or "").strip()[:300]
    if not (title or meta or h1):
        return {"ok": True, "identifies": None,
                "says": "the page carries no title, description or heading to check"}
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;")
                      .replace('"', "&quot;"))
    frag = (f"<title>{esc(title)}</title>"
            f'<meta name="description" content="{esc(meta)}">'
            f"<h1>{esc(h1)}</h1>")
    base = None
    try:
        host = urllib.parse.urlsplit(body.get("page_url") or "").netloc.lower()
        base = host.replace("www.", "").split(".")[0] or None
    except Exception:                                   # noqa: BLE001
        pass
    yes = find_websites.identifies(frag, c["name"], base,
                                   c.get("also_known_as") or [])
    page_says = title or h1 or meta
    return {"ok": True, "identifies": bool(yes), "name": c["name"],
            "page_says": page_says[:120],
            "says": (f"the page identifies as {c['name']}" if yes else
                     f"this page calls itself \"{page_says[:80]}\" - that is not "
                     f"{c['name']}, or not recognisably. Check before sending.")}


def act_task_note(body: dict) -> dict:
    """Record what a person found while working the list. STAGING ONLY.

    This is the half of the loop a capture cannot cover. Standing on a
    company's site, the useful answers are often not "here are their jobs":
    they are "their board is at this address", "they were founded in 2014",
    "they only post on LinkedIn", or "there is nothing here". None of those is
    a posting and all of them are worth keeping.

    WHY IT APPENDS INSTEAD OF WRITING. companies.json is the map, and the map
    changes in Python behind validate() or not at all. This is the same
    division of labour the web admin already runs on: the endpoint appends an
    OPINION and scripts/apply_task_notes.py applies it, so a bug in an
    extension can mis-record a note and cannot corrupt the dataset. It is also
    what lets this action sit in OPEN_ACTIONS without handing the extension
    the console code.

    Nothing here is validated as true - a person typed it. What IS validated
    is the shape, because a note nobody can act on later is not a note.
    """
    kind = (body.get("kind") or "").strip().lower()
    cid = (body.get("company_id") or "").strip()
    value = (body.get("value") or "").strip()

    KINDS = {"board", "founded", "posts-at", "website", "nothing"}
    if kind not in KINDS:
        return {"error": f"kind must be one of {sorted(KINDS)}"}
    if not cid:
        return {"error": "a note has to name the company it is about"}
    if not any(c["id"] == cid for c in read_companies()):
        return {"error": f"no company with id {cid!r}"}
    # "nothing here" is the one kind whose value may be empty: it IS the
    # finding. Everything else without a value is an empty record.
    if kind != "nothing" and not value:
        return {"error": f"a {kind} note needs a value"}
    if kind in ("board", "website") and not value.startswith(("http://", "https://")):
        return {"error": "that does not look like an address"}
    if kind == "founded" and not re.fullmatch(r"(1[89]|20)\d\d", value):
        return {"error": "a founding year is four digits between 1800 and 2099"}

    path = DATA / "task_notes.json"
    try:
        notes = json.loads(path.read_text())
    except Exception:                                   # noqa: BLE001
        notes = []
    notes.append({
        "company_id": cid,
        "kind": kind,
        "value": value or None,
        # WHAT THE PERSON WAS LOOKING AT. Stored with the answer for the same
        # reason every ruling here stores its brief: a note whose context is
        # gone cannot be checked later, and this project treats a label
        # without its input as useless.
        "saw": (body.get("page_url") or "").strip() or None,
        "by": "capture-extension",
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "applied": False,
    })
    # write_atomic takes the OBJECT and serialises it itself. Handing it a
    # pre-serialised string writes a JSON *string* to the file, and the next
    # read comes back as str - which is exactly what the first test of this
    # function did on its second call.
    write_atomic("task_notes.json", notes)
    pending = sum(1 for n in notes if not n.get("applied"))
    return {"ok": True,
            "message": f"noted - {pending} waiting for apply_task_notes.py"}


def act_dismiss(body: dict) -> dict:
    bad = dismiss(body.get("queue", ""), body.get("key", ""),
                  body.get("why", ""), by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return {"ok": True, "message": "dismissed"}


def act_move(body: dict, action: str = "move") -> dict:
    """Move a company to a sector and category in one write.

    Dragging across sectors needs both fields to change together. Setting the
    sector alone would leave the old category behind, which validate() refuses -
    correctly, since 'Police' is not a category of General Gov.

    `action` is what the journal calls it, and it is not decoration. A drag on
    the Sort board is a ruling recorded NOWHERE ELSE; a wrong-bucket ruling
    goes through here too but is also written to placement_rulings.json.
    Counting both journal entries the same way either double-counts every
    placement or drops every drag, and both of those are the kind of quiet
    miscount this whole pass is about. So a placement journals as "place" and
    a drag journals as "move", and the ruling count can tell them apart.
    """
    companies = read_companies()
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    if not c:
        return {"error": "company not found"}
    sec, cat = body.get("sector"), body.get("category")
    schema = read("schema.json", {"sectors": []})
    cats = {x["name"]: x["categories"] for x in schema["sectors"]}
    if sec not in cats:
        return {"error": f"unknown sector {sec}"}
    if not cat:
        # Dropping onto a sector rail says where it belongs, not which shelf.
        # Suppliers & Services is the catch-all when the sector has one.
        cat = "Suppliers & Services" if "Suppliers & Services" in cats[sec] \
              else cats[sec][0]
    if cat not in cats[sec]:
        return {"error": f"{cat} is not a category of {sec}"}
    was = f"{c['sector']} / {c['category']}"
    c["sector"], c["category"] = sec, cat
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, action,
                         why=(body.get("why") or ""),
                         by=(body.get("by") or "owner"))
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{c['name']}: {was} -> {sec} / {cat}",
            "sector": sec, "category": cat}


def sort_companies(sector: str) -> dict:
    companies = read_companies()
    board = read("board.json", {})
    schema = read("schema.json", {"sectors": []})
    posts = collections.Counter(p["company_id"] for p in board.get("postings", []))
    cats = {x["name"]: x["categories"] for x in schema["sectors"]}
    if sector not in cats:
        sector = next(iter(cats), "")
    # The placement queue already asked this question for some of these. Marking
    # them here means the board shows its own suggestions instead of hiding them
    # in another tab.
    flagged = {i["id"]: i["suggested"] for i in q_placement(companies, board)}
    rows = [{"id": c["id"], "name": c["name"], "category": c["category"],
             "description": c.get("description"), "website": c.get("website"),
             "location": c.get("location"),
             "year_founded": c.get("year_founded"),
             "events": _events(c.get("description")),
             "ats": (c.get("ats") or {}).get("type"),
             "postings": posts.get(c["id"], 0),
             "suggested": flagged.get(c["id"])}
            for c in companies if c["sector"] == sector]
    rows.sort(key=lambda r: (-r["postings"], r["name"].lower()))
    return {"sector": sector, "sectors": list(cats), "categories": cats[sector],
            "companies": rows}


def sort_roles() -> dict:
    board = read("board.json", {})
    over = read("family_overrides.json", {})
    seen = {}
    for p in board.get("postings", []):
        if p.get("family") != "other":
            continue
        t = p["title"]
        if t in over or is_dismissed("unclassified", t):
            continue
        e = seen.setdefault(t, {"title": t, "company": p["company"],
                                "url": p.get("url"), "count": 0})
        e["count"] += 1
    rows = sorted(seen.values(), key=lambda r: (-r["count"], r["title"].lower()))
    return {"families": {k: v for k, v in roles.LABEL.items() if k != "other"},
            "titles": rows}


ACTIONS = {"merge": act_merge, "patch": act_patch, "move": act_move,
           "verify-website": act_verify_website, "verify-board": act_verify_board,
           "set-board": act_set_board, "set-family": act_set_family,
           "capture": act_capture, "search-companies": act_search_companies,
           "worklist": act_worklist, "task-note": act_task_note,
           "identify": act_identify,
           "scope": act_scope, "scope-all": act_scope_all,
           "vendor-scope": act_vendor_scope,
           "vendor-scope-all": act_vendor_scope_all,
           "also": act_also, "retry-board": act_retry_board, "save-website": act_save_website, "posts-at": act_posts_at, "suggest": act_suggest, "board-proposal": act_board_proposal, "acquisition-ruling": act_acquisition_ruling, "conference-date": act_conference_date, "set-founded": act_set_founded, "identity-ruling": act_identity_ruling, "place": act_place,
           "submit": act_submit, "resolve-submission": act_resolve_submission,
           "inspect-submission": act_inspect_submission,
           "confirm-founded": act_confirm_founded,
           "dismiss": act_dismiss}


# --- who may write a ruling ----------------------------------------------
#
# THE HOLE THIS CLOSES. On 2026-08-24 an agent made 86 real rulings against
# the owner's running admin. It could not edit a file in the repository, and
# it did not need to: it asked /api/token for a token, attached it, and
# posted. The token was never the wrong idea - it is what keeps a website the
# owner happens to be visiting from driving this server, and it still does -
# but it was doing a second job it cannot do. ANYTHING A LOCAL CALLER CAN ASK
# FOR IS A THING A LOCAL CALLER CAN HAVE. Reading /api/token gets you one;
# so does curling / and grepping the shim out of the page.
#
# So the two jobs are split. The TOKEN says "you are not a web page" and is
# handed out on request, because the capture extension genuinely needs it.
# The CODE says "you are the person sitting at the terminal that started
# this", and the only way to learn it is to look at that terminal: it is
# minted per process, printed once on stdout, and served by no route, in no
# header, and in no page. A caller that can only speak HTTP cannot obtain it,
# which is the whole property being bought.
#
# The owner pays nothing for it in the normal case. main() prints the admin's
# URL with the code already in the fragment, the fragment never travels to
# the server, and the page keeps it. Opening a bookmark instead costs one
# paste. See the report: this is a speed bump for an agent on this machine,
# not a security boundary against one - anything with a shell can read a
# browser profile if it tries hard enough. It stops the accident that
# actually happened.
CODE_HEADER = "X-Admin-Code"

# Actions the code does NOT gate, and why each one is here.
#
# The capture extension is not same-origin, cannot be handed the page's
# fragment, and the owner uses it constantly - so the two calls it makes stay
# open. Both land in reviewed surfaces rather than in companies.json:
# `capture` writes data/manual.json and `submit` writes data/submissions.json,
# which the admin's own docs already call "a claim, not a fact". The other
# three write nothing at all; they fetch a URL and report what they saw.
# READ-ONLY OR STAGING, NEVER THE MAP. That is the line these six - now
# eight - have in common, and it is not "no writes": capture writes
# manual.json and submit writes submissions.json. What none of them touches is
# companies.json, which stays behind the console code.
#   worklist   reads four queues and writes nothing at all
#   task-note  appends to task_notes.json, which apply_task_notes.py then
#              applies in Python behind validate() - the same division of
#              labour the web admin already uses for rulings
# selftest::check_open_actions_never_write_the_map asserts this.
OPEN_ACTIONS = {"capture", "search-companies", "submit",
                "inspect-submission", "verify-website", "verify-board",
                "worklist", "task-note", "identify"}


def _mint_code() -> str:
    """Six characters a person can read off a terminal and retype.

    No 0/O/1/I/L: this gets copied by eye at 1am, and a code that is
    ambiguous to read is a code somebody disables.
    """
    return "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
                   for _ in range(6))


CONSOLE_CODE = _mint_code()


# ---------------------------------------------------------------- server

CAPTURE_PAGE = """<!doctype html><meta charset=utf-8>
<title>SLED JOBS — page capture</title>
<style>
 :root{--bg:#fbfaf8;--panel:#fff;--line:#e5e1db;--ink:#1a1815;--dim:#6a655d;
       --faint:#969086;--accent:#2f6f4f;--chip:#f1eeea}
 @media (prefers-color-scheme:dark){:root{--bg:#121110;--panel:#1a1918;--line:#2f2c28;
   --ink:#eae6e0;--dim:#a09a90;--faint:#726c63;--accent:#6fb98a;--chip:#232120}}
 body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 ui-sans-serif,
      -apple-system,"Segoe UI",Roboto,sans-serif}
 main{max-width:720px;margin:0 auto;padding:34px 22px 70px}
 h1{font-size:21px;margin:0 0 4px;letter-spacing:-.015em}
 h2{font-size:14px;margin:30px 0 8px}
 p{color:var(--dim);font-size:14px}
 .drag{display:inline-block;background:var(--accent);color:#fff;font-weight:600;
   padding:11px 22px;border-radius:9px;text-decoration:none;font-size:14.5px;
   cursor:grab;margin:6px 0}
 ol{color:var(--dim);font-size:14px;padding-left:20px}
 li{margin:7px 0}
 code{background:var(--chip);padding:1px 6px;border-radius:5px;font-size:13px}
 .note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
   border-radius:8px;padding:13px 16px;font-size:13.5px;color:var(--dim);margin:18px 0}
 a{color:var(--accent)}
</style>
<main>
<h1>Page capture</h1>
<p>For the boards the fetcher cannot read. Your browser runs the widgets,
iframes and sessions ours does not, so if you can see the jobs, this can take
them. Two ways in: the extension, which sends straight here, and the
bookmarklet, which goes by the clipboard.</p>

<h2>The extension &mdash; one click, no copying</h2>
<ol>
 <li>Open <code>chrome://extensions</code> and turn on
     <b>Developer mode</b>, top right.</li>
 <li>Click <b>Load unpacked</b> and choose the
     <code>extension/</code> folder in this repository.</li>
 <li><b>SLED JOBS Capture</b> appears in the toolbar. Pin it &mdash; the
     puzzle-piece menu, then the pin beside its name.</li>
 <li>On any careers page, click it. A panel lists the jobs, you pick the
     company, and it lands in <code>data/manual.json</code>.</li>
</ol>
<div class="note">
<b>It keeps your work when this admin is off.</b> A capture made while
<code>admin.py</code> is not running is held in the extension and sent the
moment it can reach here again &mdash; so you can work a list on a train and
start the admin afterwards. The panel tells you how many are waiting.
</div>

<h2>The bookmarklet &mdash; nothing to install</h2>
<p>Drag this to your bookmarks bar:</p>
<a class="drag" href="__LOADER__">SLED JOBS capture</a>
<p style="font-size:13px">If your bookmarks bar is hidden, press
<code>&#8984;&#8679;B</code> first. Right-clicking the button and copying the
link also works &mdash; make a bookmark by hand and paste it as the URL.</p>
<ol>
 <li>Open a careers page in your normal browser.</li>
 <li>Click <b>SLED JOBS capture</b>. A panel lists every job link it can see.</li>
 <li>Uncheck anything that is not a posting, then <b>Copy for admin</b>.</li>
 <li>Paste it into the <a href="/#capture">Capture tab</a> here and pick the
     company.</li>
</ol>
<p style="font-size:13px">The clipboard is the handoff because a bookmarklet
runs as the page, and in Chrome a page served over https cannot reach
<code>127.0.0.1</code>. That is a Chrome behaviour rather than a fact about
browsers &mdash; Firefox and Safari have not implemented it &mdash; but Chrome
is where this runs, and the clipboard needs no permission anywhere.</p>

<h2>What to point it at</h2>
<p>The <a href="/#boards">No board found</a> queue is the worklist. Those are
companies probed and turned away, most of which hire somewhere a fetcher will
never read. It is sorted for this, not alphabetically, and the green chips are
the conference floors each company exhibits on &mdash; the fastest way to work
it is by floor, standing on it.</p>

<div class="note">
Captured postings are never deleted by an automated run. Absence from a refresh
means the fetcher still cannot see that company, not that the role closed, so
only <code>python scripts/manual.py none</code> closes one.
</div>

<h2>What neither of them does</h2>
<p>Both read the page you have open, once, when you click. Neither scrolls,
paginates, follows links, logs in, or runs on its own &mdash; which is the line
between reading a page you opened and harvesting a site. The extension holds
that line by construction: <code>activeTab</code> means it can read nothing at
all until you click, and then only that one tab.</p>

<h2>When it finds nothing</h2>
<p>Usually the board is inside an iframe. Right-click it &rarr; <i>This Frame</i>
&rarr; <i>Show Only This Frame</i>, then click again.</p>

<p style="margin-top:34px;font-size:13px;color:var(--faint)">
The bookmarklet carries all of <code>scripts/capture.js</code> (__LINES__ lines,
__SIZE__) in its own URL, because a page on https cannot load anything from a
loopback server in Chrome. Editing the script means dragging the button again;
the extension just needs reloading on <code>chrome://extensions</code>.
&nbsp;·&nbsp; <a href="/">back to admin</a></p>
</main>"""


# Minted per process and never written down. Any /api/ call has to echo it in
# a header, and a custom header is precisely what a cross-origin caller cannot
# attach without a preflight - which nothing here answers.
#
# That header is the whole fix for the finding that any website the owner
# visited could rewrite companies.json. CORS was never the protection people
# assume: a POST with Content-Type text/plain is a SIMPLE request, so the
# browser sends it with no preflight at all and the write lands whether or not
# the reply can be read. Blocking preflights would have changed nothing. A
# secret the attacker cannot guess does.
TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Admin-Token"

ADMIN_HTML = ROOT / "admin.html"
CAPTURE_JS = pathlib.Path(__file__).resolve().parent / "capture.js"

# Attached to admin.html on the way out rather than written into it, so the
# token lives for one process and never sits in a file - and so the page needs
# no change to cooperate.
TOKEN_SHIM = """<script>
/* Added by scripts/admin.py while serving this page. Every /api/ call needs a
   header a cross-origin caller cannot attach; this attaches it, so the page's
   own fetch() calls stay exactly as they were.

   It also carries the CONSOLE CODE, and the difference between the two is the
   point. The token is in this script because the server put it here, so
   anything that can fetch this page can read it. The code is NOT here and the
   server never sends it anywhere: it arrives in the URL fragment the admin
   printed on its own console, and a fragment is the one part of a URL a
   browser does not transmit. window.gtdCode is where the page reads and
   writes it; nothing else in this file knows it exists. */
(function () {
  var T = "__TOKEN__", H = "__HEADER__", CH = "__CODEHEADER__";
  var KEY = "gtd-admin-code", F = window.fetch;

  /* Out of the fragment on arrival, then out of the address bar - so it does
     not sit in a screenshot, a shared link or the browser's history.

     Also on hashchange, because pasting the printed URL into a tab ALREADY on
     this origin changes only the fragment, and a same-document navigation
     does not re-run this script. Without the listener that paste looks like
     it did nothing, which is the worst kind of nothing. */
  function pocket() {
    var m = /(?:^|[#&])k=([A-Za-z0-9]{4,16})\\b/.exec(location.hash || "");
    if (!m) return false;
    try { localStorage.setItem(KEY, m[1].toUpperCase()); } catch (e) {}
    history.replaceState(null, "", location.pathname + location.search);
    return true;
  }
  pocket();
  window.addEventListener("hashchange", function () {
    if (pocket()) {
      var bar = document.getElementById("codebar");
      if (bar) bar.remove();
    }
  });
  window.gtdCode = {
    get: function () {
      try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
    },
    set: function (v) {
      try { localStorage.setItem(KEY, (v || "").trim().toUpperCase()); }
      catch (e) {}
    }
  };

  window.fetch = function (input, init) {
    var u = typeof input === "string" ? input : (input && input.url) || "";
    var mine = false;
    try {
      var p = new URL(u, location.href);
      mine = p.origin === location.origin && p.pathname.indexOf("/api/") === 0;
    } catch (e) {}
    if (mine) {
      init = Object.assign({}, init || {});
      init.headers = new Headers(
        init.headers ||
        (typeof input === "object" && input && input.headers) || {});
      init.headers.set(H, T);
      var c = window.gtdCode.get();
      if (c) init.headers.set(CH, c);
    }
    return F.call(window, input, init);
  };
})();
</script>"""


class Handler(http.server.BaseHTTPRequestHandler):
    """Loopback, and now also same-origin only.

    Not SimpleHTTPRequestHandler any more. That served the whole repository
    root, so /.git/config, /scripts/admin.py and /data/companies.json all
    answered 200 to anything that asked. ../ traversal was blocked correctly
    the whole time - the directory itself was the exposure - and the fix is to
    stop having a directory. SIX routes are served and everything else is 404
    by construction rather than by check:

        /                    the admin page
        /admin.html          the same page by its own name
        /capture             the bookmarklet install page
        /capture.js          the bookmarklet itself
        /assets/logos/*      company logos
        /assets/mascot/*     the mascot

    This said "three" while serving six, which is the drift selftest exists to
    break, so check_admin_http now asserts the list against a real server:
    /data/companies.json, /.git/config, /scripts/admin.py, /CLAUDE.md and
    /data/admin_journal.jsonl must all 404, / and /admin.html must 200.
    """

    def log_message(self, fmt, *args):        # keep the console readable
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"  {self.command} {self.path}\n")

    # ------------------------------------------------------------ guards

    def _local_host(self) -> bool:
        """Is this request addressed to us by name, or only routed to us.

        A browser fills Host in from the address bar, so this is the one thing
        that still disagrees with a DNS rebinding attack: evil.example can
        resolve to 127.0.0.1, at which point every same-origin protection sides
        with the attacker, but the Host header still reads evil.example.
        """
        try:
            parts = urllib.parse.urlsplit("//" + (self.headers.get("Host") or ""))
            host, port = parts.hostname, parts.port
        except ValueError:
            return False
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        return port in (None, self.server.server_address[1])

    def _authed(self) -> bool:
        # Compared as bytes, because http.server decodes header values as
        # latin-1: one byte over 0x7f arrives here as a non-ASCII character,
        # and secrets.compare_digest raises TypeError on a str like that. It
        # did - `X-Admin-Token: café` reached the except-nothing handler, the
        # connection was dropped mid-request and the caller got no reply at
        # all, which reads as "the admin is down" rather than "wrong token".
        # Bytes compare fine and keep the same constant-time property.
        got = (self.headers.get(TOKEN_HEADER) or "").encode("latin-1", "replace")
        return secrets.compare_digest(got, TOKEN.encode("ascii"))

    def _coded(self) -> bool:
        """Does this caller know what is printed on the admin's own console.

        Same bytes comparison as _authed and for the same reason: a header
        value with a byte over 0x7f arrives as a non-ASCII str and
        compare_digest raises TypeError on one, which drops the connection
        and reads as "the admin is down" rather than "wrong code".
        """
        got = (self.headers.get(CODE_HEADER) or "").strip().upper()
        return secrets.compare_digest(got.encode("latin-1", "replace"),
                                      CONSOLE_CODE.encode("ascii"))

    def _web_origin(self) -> bool:
        """Is this request coming from a page on some website.

        A browser attaches Origin itself and a page cannot forge or drop it,
        so this is the one thing that separates "an ordinary web page is
        asking" from "a tool, or an extension, is asking". Only http(s) counts
        as a web origin: chrome-extension:// is the capture extension, and no
        Origin at all is a plain client or a top-level navigation. Neither of
        those two can be a page reading a reply it should not have.
        """
        origin = (self.headers.get("Origin") or "").strip().lower()
        return origin.startswith("http://") or origin.startswith("https://")

    # ------------------------------------------------------------ writing

    # A page the owner is visiting cannot attach the token header, so its own
    # requests bounce off _authed - but it can put the admin in an iframe,
    # where the framed document is on the admin's own origin, carries the
    # token shim itself, and every button works. Then it only has to be made
    # to look like something else and clicked once. A custom header is no
    # defence against a click on our own UI; refusing to be framed is.
    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # nothing here is cacheable and nothing here should ever be sniffed
        # into a script tag by another page
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # both, on purpose: frame-ancestors is the rule browsers still honour,
        # X-Frame-Options is what an older one reads. Nothing here is ever
        # meant to be embedded, so the answer is none rather than sameorigin.
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(json.dumps(payload).encode(), "application/json", code)

    # -------------------------------------------------------------- GET

    def do_GET(self):
        if not self._local_host():
            return self._json({"error": "not served on that host"}, 421)
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            if path == "/api/token":
                # The one route that hands the token out, and it needs no
                # token itself - it is where the capture extension gets one,
                # since an extension is not same-origin and cannot be handed
                # the shim the admin page gets.
                #
                # Two things keep that from being the hole the token closed.
                # First, a page on any other origin can SEND this request but
                # can read nothing back: no response here carries a CORS
                # header, so the reply is opaque to it, and the JSON content
                # type plus nosniff stops it being pulled in as a script
                # instead. Second, and stricter, a web page's fetch always
                # carries its own Origin and cannot drop it, so a page does not
                # even receive the bytes - only a caller with no web origin at
                # all does, which is the extension, curl, and nothing a website
                # can arrange.
                if self._web_origin():
                    return self._json(
                        {"error": "the token is not handed to a web page"}, 403)
                return self._json({"token": TOKEN, "header": TOKEN_HEADER})
            if not self._authed():
                return self._json({"error": "missing or wrong admin token"}, 403)
            return self._api_get(path)
        if path in ("/", "/admin.html"):
            return self._admin_page()
        if path == "/capture":
            return self._capture_page()
        if path == "/capture.js":
            return self._send(CAPTURE_JS.read_bytes(), "application/javascript")
        if path.startswith("/assets/logos/"):
            return self._logo(path[len("/assets/logos/"):])
        if path.startswith("/assets/mascot/"):
            return self._mascot(path[len("/assets/mascot/"):])
        return self._json({"error": "not found"}, 404)

    # Logos are the one static directory the admin serves. Serving ROOT is what
    # handed out /.git/config and /data/companies.json, so this resolves the
    # file and asserts the logo directory is genuinely its parent rather than
    # trusting the string - "..%2f" and a symlink both die on the resolve.
    LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
                  ".ico": "image/x-icon", ".svg": "image/svg+xml"}

    def _mascot(self, name: str):
        """The penguin. Same resolve-and-contain check as the logos."""
        root = (ROOT / "assets" / "mascot").resolve()
        try:
            f = (root / name).resolve()
        except (OSError, ValueError):
            return self._json({"error": "not found"}, 404)
        if root not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        kind = {".svg": "image/svg+xml", ".png": "image/png"}.get(f.suffix.lower())
        if not kind:
            return self._json({"error": "not found"}, 404)
        return self._send(f.read_bytes(), kind)

    def _logo(self, name: str):
        root = (ROOT / "assets" / "logos").resolve()
        try:
            f = (root / name).resolve()
        except (OSError, ValueError):
            return self._json({"error": "not found"}, 404)
        if root not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        kind = self.LOGO_TYPES.get(f.suffix.lower())
        if not kind:
            return self._json({"error": "not found"}, 404)
        return self._send(f.read_bytes(), kind)

    def _api_get(self, path: str):
        if path == "/api/triage":
            companies, board = read_companies(), read("board.json", {})
            t = triage(companies, board)
            t["health"] = board_health(companies, board)
            t["sessions"] = sessions()
            t["reversals"] = reversals()
            t["unlocks"] = unlocks(t["health"])
            t["agree"] = agree_rate()
            t["receipt"] = receipt()
            return self._json(t)
        if path == "/api/receipt":
            return self._json(receipt())
        if path == "/api/agree":
            # its own route, and cheap: the belt refetches this after every
            # ruling, and rebuilding the whole queue to move one number
            # would make the honest counterweight the slow part of the loop
            return self._json(agree_rate())
        if path == "/api/export.csv":
            # No gate. It used to open at a board-health threshold, and a
            # review took that threshold four different ways - the cheapest
            # being one bulk call that wrote 240 rulings in zero seconds. An
            # export of your own data was never a prize worth defending.
            companies, board = read_companies(), read("board.json", {})
            return self._send(board_csv(companies, board).encode(),
                              "text/csv; charset=utf-8")
        if path == "/api/queues":
            companies, board = read_companies(), read("board.json", {})
            return self._json({"counts": {k: len(f(companies, board))
                                          for k, f in QUEUES.items()},
                               "labels": LABEL,
                               # which companies have a logo, and in what
                               # format. The page needs this to know whether to
                               # ask for an image at all - guessing the
                               # extension would mean 1,800 speculative 404s.
                               "logos": (read("board.json", {}) or {}).get("logos") or {},
                               "companies": len(companies),
                               "postings": len(board.get("postings", [])),
                               "generated": board.get("generated")})
        if path.startswith("/api/queue/"):
            name = path.rsplit("/", 1)[-1]
            if name not in QUEUES:
                return self._json({"error": "no such queue"}, 404)
            companies, board = read_companies(), read("board.json", {})
            items = QUEUES[name](companies, board)
            return self._json({
                "items": items[:400],
                # the page shows 400 at most, and must never round that up
                # into "this is the whole queue"
                "total": len(items),
                "state": queue_state(name, len(items)),
                "belt": name in BELT_QUEUES,
                # only where a machine actually proposed something. A queue
                # with no proposal reporting a 0% agree-rate would read as
                # the guesser being wrong when it never spoke.
                "agree": agree_rate() if name in PROPOSAL_QUEUES else None,
                # How much OTHER work this queue is holding up. 137 decisions
                # across eight queues sit on records that are one half of a
                # duplicate pair; if the pair merges, that research was spent
                # on a record that stops existing. Ordering is the cheapest
                # lever there is, and it is invisible unless somebody counts.
                "unblocks": _unblocks(name, companies, board),
            })
        if path == "/api/sort/companies":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._json(sort_companies((qs.get("sector") or [""])[0]))
        if path == "/api/sort/roles":
            return self._json(sort_roles())
        if path == "/api/schema":
            return self._json(read("schema.json", {}))
        if path == "/api/families":
            return self._json(roles.LABEL)
        return self._json({"error": "not found"}, 404)

    def _admin_page(self):
        try:
            html = ADMIN_HTML.read_text()
        except OSError:
            return self._json({"error": "admin.html is missing"}, 404)
        # The token goes into the page; the console code deliberately does
        # not. If it were substituted here, curl-ing this page would hand it
        # over and the gate would be worth nothing.
        shim = TOKEN_SHIM.replace("__TOKEN__", TOKEN) \
                         .replace("__HEADER__", TOKEN_HEADER) \
                         .replace("__CODEHEADER__", CODE_HEADER)
        # After the charset declaration, which a browser only honours in the
        # first 1024 bytes of the document - a kilobyte of script ahead of it
        # would push it out of range. Still inside <head> and still well ahead
        # of the page's own script, which is all this needs to be.
        m = re.search(r"<meta[^>]+charset[^>]*>", html, re.I)
        if m:
            html = html[:m.end()] + shim + html[m.end():]
        elif "<head>" in html:
            html = html.replace("<head>", "<head>" + shim, 1)
        else:
            html = shim + html
        self._send(html.encode(), "text/html; charset=utf-8")

    def _capture_page(self):
        js = CAPTURE_JS.read_text()
        # Self-contained on purpose. Chrome blocks a page on https from loading
        # anything off http://127.0.0.1 - fetch and script tag alike - so a
        # loader bookmarklet would fail on every real careers site. The whole
        # script rides in the URL instead, which is why editing capture.js means
        # dragging the button again. It hands its result over on the clipboard
        # and never calls this server, which is why nothing here needs a token.
        loader = "javascript:" + urllib.parse.quote(js, safe="")
        html = CAPTURE_PAGE.replace("__LOADER__", loader.replace('"', "&quot;")) \
                           .replace("__LINES__", str(len(js.splitlines()))) \
                           .replace("__SIZE__", f"{len(loader) // 1024} KB")
        self._send(html.encode(), "text/html; charset=utf-8")

    # ------------------------------------------------------------- POST

    def do_POST(self):
        if not self._local_host():
            return self._json({"error": "not served on that host"}, 421)
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        # A JSON content type is not a permission, but demanding it takes the
        # request out of the "simple" class the browser will send cross-origin
        # without asking first. The token is the actual guard; this makes the
        # attempt fail one step earlier.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            return self._json({"error": "send application/json"}, 415)
        if not self._authed():
            return self._json({"error": "missing or wrong admin token"}, 403)
        action = path.rsplit("/", 1)[-1]
        if action not in ACTIONS:
            return self._json({"error": f"unknown action {action}"}, 404)
        # The token got you through the door. Writing a ruling needs the code
        # off the console, which no route hands out - see OPEN_ACTIONS above
        # for the six that do not need it and why.
        if action not in OPEN_ACTIONS and not self._coded():
            return self._json(
                {"error": "this writes a ruling, and a ruling needs the code "
                          "printed on the console where the admin was "
                          "started. Nothing was written.",
                 "code_required": True}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad request body"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "bad request body"}, 400)
        try:
            # Every action, not only the ones that obviously fetch: an action
            # that grows a fetch later inherits the guard instead of having to
            # remember it.
            with only_public_hosts():
                out = ACTIONS[action](body)
        except Exception as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json(out, 400 if out.get("error") else 200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()

    companies, board = read_companies(), read("board.json", {})
    print("SLED JOBS admin\n")
    for k, f in QUEUES.items():
        print(f"  {len(f(companies, board)):>5}  {LABEL[k]}")
    # Loopback only, on purpose. This writes to companies.json with no auth in
    # front of it, so it must not be reachable from the network - and because
    # the browser can reach loopback even when the network cannot, the /api/
    # token above is what keeps a page the owner happens to be visiting from
    # driving it.
    socketserver.TCPServer.allow_reuse_address = True

    # AN OCCUPIED PORT IS ALMOST ALWAYS AN ADMIN YOU ALREADY HAVE OPEN, and
    # this used to answer it with a twenty-line socketserver traceback ending
    # in "OSError: [Errno 48] Address already in use". That is a true statement
    # and a useless one: the actual situation is "your other terminal tab has
    # one running", and the actual fix is to go and look at it.
    #
    # It matters more here than it would elsewhere, because the ruling code
    # lives ONLY in the scrollback of the terminal that started the server. A
    # person who reads that traceback as "it is broken" and kills the process
    # to fix it has just destroyed the one copy of their own code - which has
    # happened, twice, and cost a whole session's rulings each time.
    #
    # So: check first, and if something is already answering on that port, say
    # what it is and where to find it.
    try:
        probe = socket.create_connection(("127.0.0.1", a.port), timeout=0.4)
        probe.close()
    except OSError:
        pass                                  # nothing there, carry on
    else:
        print(f"\nSomething is already listening on port {a.port}.\n")
        print("  That is almost certainly an admin you started earlier, in\n"
              "  another terminal tab or window. Go and find it: the link with\n"
              "  the #k= code is in ITS scrollback, just under the queue\n"
              "  counts, and that code exists nowhere else.\n")
        print("  DO NOT kill it to clear the port unless you mean to lose that\n"
              "  code. Ruling needs it, no route serves it, and it is not in\n"
              "  the page.\n")
        print(f"  If you do want a second one, give it another port:\n"
              f"      python3 scripts/admin.py --port {a.port + 1}\n")
        return 1

    with socketserver.TCPServer(("127.0.0.1", a.port), Handler) as srv:
        # The code rides in the FRAGMENT, which browsers do not send to the
        # server - so opening this URL hands the page its code without the
        # code ever crossing the wire in either direction. Open the link and
        # there is nothing else to do; open a bookmark instead and the page
        # asks for these six characters once.
        print(f"\nhttp://127.0.0.1:{a.port}/#k={CONSOLE_CODE}"
              f"   (loopback only; ctrl-c to stop)")
        print(f"\n  code for this run: {CONSOLE_CODE}")
        print("  Rulings need it. It is printed here and nowhere else - no\n"
              "  route serves it and it is not in the page - so a script that\n"
              "  can only talk HTTP to this port cannot make a ruling.\n")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
