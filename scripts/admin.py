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
import datetime as dt
import http.server
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
ID_OK = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Words that carry no identity, so two records differing only by these are the
# same company: "Miovision" and "Miovision Technologies Inc." are one vendor.
LEGAL = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|group|holdings|"
                   r"technologies|technology|software|systems|solutions|company)\b", re.I)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def ident(name: str) -> str:
    return norm(LEGAL.sub("", name or ""))


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
    """Journal, then write. Returns a refusal message, or None on success."""
    import journal
    before = _LAST_COMPANIES if _LAST_COMPANIES is not None else companies
    _eid, refusal = journal.record("companies.json", before, companies,
                                   action, by, why, force)
    if refusal:
        return refusal
    write_atomic("companies.json", companies)
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


def dismiss(queue: str, key: str, why: str) -> None:
    d = dismissed()
    d.setdefault(queue, {})[key] = {"on": dt.date.today().isoformat(), "why": why}
    write_atomic("admin_dismissed.json", d)


def is_dismissed(queue: str, key: str) -> bool:
    return key in dismissed().get(queue, {})


# ---------------------------------------------------------------- queues

def q_duplicates(companies, board) -> list:
    g = collections.defaultdict(list)
    for c in companies:
        k = ident(c["name"])
        if k:
            g[k].append(c)
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


def q_websites(companies, board) -> list:
    return [{"id": c["id"], "name": c["name"], "sector": c["sector"],
             "category": c["category"], "description": c.get("description"),
             "also_known_as": c.get("also_known_as") or [],
             "events": _events(c.get("description")),
             "tier": 1 if c["sector"] in ("General Gov", "Public Works", "Parks & Rec")
                     else 2}
            for c in companies
            if not c.get("website") and not is_dismissed("websites", c["id"])]


BLOCKED_MARKERS = ("blocked at the door", "could not fetch", "gave up after")


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
    state = ("blocked" if any(mk in note for mk in BLOCKED_MARKERS)
             else "none-found")
    return {"state": state, "note": note, "on": e.get("on")}


def _board_rows(companies, board):
    orgs = {o["id"]: o for o in board.get("organizations", [])}
    for c in companies:
        o = orgs.get(c["id"], {})
        kind = (c.get("ats") or {}).get("type")
        no_board = kind in (None, "unknown")
        if not (no_board or o.get("unreadable")):
            continue
        if is_dismissed("boards", c["id"]):
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


def q_acquisitions(companies, board) -> list:
    sus = read("ats_suspects.json", {})
    items = sus.get("suspects", sus) if isinstance(sus, dict) else sus
    if isinstance(items, dict):
        items = [{"id": k, **v} for k, v in items.items()]
    return [i for i in items if not is_dismissed("acquisitions", i.get("id", ""))]


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
              "why": (body.get("why") or "").strip() or None}
    write_atomic("scope_decisions.json", d)
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
                          "why": f"bulk ruling on {pat!r}"}
            n += 1
    write_atomic("scope_decisions.json", d)
    return {"ok": True, "message": f"{n} posting(s) ruled "
                                   f"{'in' if keep else 'out of'} scope"}


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
    bad = _pa.check(where, url)
    if bad:
        return {"error": bad}
    companies = read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    c["posts_at"] = _pa.build(where, url, body.get("by") or "owner",
                              body.get("note") or "")
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "posts-at")
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
        bad = save_companies(companies, "identity-ruling")
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


def q_founded(companies, board) -> list:
    """Companies with no founding year, hiring ones first.

    645 of 2,108 have none. It is the one field on the public card that is
    simply blank, and unlike a sector or a board it cannot be derived from
    anything - somebody has to read it off the company's own about page. So
    this queue is a typing job, and it is built to be typed: the year is the
    only input, and the two places the answer usually lives are one click away.

    Hiring first, for the same reason every other queue sorts that way: a blank
    year on a company with 51 open roles is on screen in front of visitors
    today; a blank year on a dormant one is seen by nobody.

    Deliberately NOT guessed. A founding year scraped from a copyright footer
    is wrong about as often as it is right - "(c) 2019" is when the site was
    built - and a wrong year is indistinguishable from a right one forever
    after. Blank is honest; invented is not.
    """
    dismissed = read("admin_dismissed.json", {})
    hiring = {o["id"]: o.get("open_roles", 0)
              for o in board.get("organizations", [])}
    out = []
    for c in companies:
        if c.get("year_founded"):
            continue
        if f"founded:{c['id']}" in dismissed \
                or c["id"] in dismissed.get("founded", {}):
            continue
        site = c.get("website") or ""
        out.append({
            "id": c["id"], "name": c["name"],
            "description": c.get("description"),
            "website": site,
            "open_roles": hiring.get(c["id"], 0),
            # where the answer actually tends to live, so it is one click
            # rather than a search-engine detour
            "about": (site.rstrip("/") + "/about") if site else "",
            "linkedin": ("https://www.linkedin.com/search/results/companies/?keywords="
                         + urllib.parse.quote(c["name"])),
        })
    out.sort(key=lambda r: (-r["open_roles"], r["name"]))
    return out


def act_set_founded(body: dict) -> dict:
    """Write a founding year somebody read off the company's own page."""
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
    c["year_founded"] = year
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "set-founded", f"{c['name']} founded {year}")
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{c['name']}: founded {year}"}


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
    n = 0
    for name in names:
        k = _vkey(name)
        if k in d:
            continue
        d[k] = {"call": call, "name": name, "on": dt.date.today().isoformat(),
                "by": (body.get("by") or "owner").strip(),
                "why": (body.get("why") or "").strip()
                       or f"bulk ruling on {body.get('theme') or 'a group'}",
                "bulk": True, "saw": {"theme": body.get("theme")}}
        n += 1
    write_atomic("vendor_scope_decisions.json", d)
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


def _game(counts: dict) -> dict:
    """The gamification layer, built strictly from ruling records.

    Three mechanics, chosen in CLAUDE.md against the owner's framework:
    quests whose reward is the product working better, personal bests
    against the user's own last 30 days, and a craft signal - why-coverage -
    because the reason on a ruling is what teaches the classifier later, and
    it is a measure of care rather than volume. Volume is deliberately never
    scored on its own: a wrong ruling is invisible and permanent here.
    """
    per_day = collections.Counter()
    with_why = total = 0
    sources = [("vendor_scope_decisions.json", "vendors"),
               ("placement_rulings.json", "miscategorized"),
               ("scope_decisions.json", "scope")]
    done_by_queue = collections.Counter()
    for fname, queue in sources:
        for r in read(fname, {}).values():
            if not isinstance(r, dict):
                continue
            total += 1
            done_by_queue[queue] += 1
            per_day[r.get("on") or "?"] += 1
            if (r.get("why") or "").strip():
                with_why += 1
    for key, entry in read("admin_dismissed.json", {}).items():
        if isinstance(entry, dict) and isinstance(key, str) and ":" in key:
            total += 1
            done_by_queue[key.split(":", 1)[0]] += 1
            per_day[entry.get("on") or "?"] += 1
            if (entry.get("why") or "").strip():
                with_why += 1

    today = dt.date.today()
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(30)]
    best = max((per_day.get(d, 0) for d in days[1:]), default=0)

    # The tape: what the last few rulings were, newest first. Not a score -
    # a record of consequence, so the work reads as having caused something.
    tape = []
    for fname, verb in (("vendor_scope_decisions.json", "ruled"),
                        ("placement_rulings.json", "refiled")):
        for r in read(fname, {}).values():
            if not isinstance(r, dict) or not r.get("on"):
                continue
            what = r.get("name") or r.get("sector")
            if verb == "refiled":
                what = f"{r.get('sector')} / {r.get('category')}"
            tape.append({"on": r["on"], "verb": verb, "what": what,
                         "call": r.get("call"), "why": r.get("why")})
    tape.sort(key=lambda t: t["on"], reverse=True)
    states = []
    for q, name in END_STATE.items():
        left = counts.get(q, 0)
        done = done_by_queue.get(q, 0)
        if left or done:
            states.append({"queue": q, "name": name, "done": done,
                           "left": left,
                           "pct": round(100 * done / (done + left))
                                  if (done + left) else 100})
    return {"today": per_day.get(days[0], 0), "best_30": best,
            "why_coverage": round(100 * with_why / total) if total else None,
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
    bad = save_companies(companies, "also")
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
    """
    cid = body.get("id")
    if not cid:
        return {"error": "need a company id"}
    if body.get("keep"):
        d = read("admin_dismissed.json", {})
        d[f"miscategorized:{cid}"] = {
            "on": dt.date.today().isoformat(),
            "by": (body.get("by") or "owner").strip(),
            "why": (body.get("why") or "").strip() or "the bucket is right"}
        write_atomic("admin_dismissed.json", d)
        return {"ok": True, "message": "left where it is"}

    res = act_move({"id": cid, "sector": body.get("sector"),
                    "category": body.get("category")})
    if res.get("error"):
        return res
    rulings = read("placement_rulings.json", {})
    rulings[cid] = {"sector": body.get("sector"), "category": body.get("category"),
                    "on": dt.date.today().isoformat(),
                    "by": (body.get("by") or "owner").strip(),
                    "why": (body.get("why") or "").strip() or None,
                    "saw": {"was": body.get("was"),
                            "proposed": body.get("proposed"),
                            "description": body.get("description")}}
    write_atomic("placement_rulings.json", rulings)
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
    bad = save_companies(companies, "save-website")
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
                        bad = save_companies(companies, "save-website")
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
            bad = save_companies(companies, "retry-board")
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
                      "on": dt.date.today().isoformat(),
                      "by": (body.get("by") or "owner").strip(),
                      "why": (body.get("why") or "").strip() or None,
                      "saw": {"description": body.get("description"),
                              "website": body.get("website"),
                              "source_event": body.get("source_event")}}
    write_atomic("vendor_scope_decisions.json", d)
    msg = {"in": "will be added as a full company",
           "sled": "will be added, public-sector roles only",
           "out": "left off the board"}[call]
    return {"ok": True, "message": f"{name}: {msg}"}


def q_submissions(companies, board) -> list:
    subs = read("submissions.json", {"items": []})
    names = {c["id"]: c["name"] for c in companies}
    return [{**i, "company": names.get(i.get("company_id"))}
            for i in subs["items"] if i.get("status") == "pending"]


QUEUES = {"founded": q_founded, "miscategorized": q_miscategorized, "vendors": q_vendor_scope, "scope": q_scope, "submissions": q_submissions, "duplicates": q_duplicates, "websites": q_websites, "boards": q_boards, "blocked": q_blocked,
          "placement": q_placement, "unclassified": q_unclassified,
          "acquisitions": q_acquisitions, "review": q_review}

LABEL = {"founded": "Founding year", "miscategorized": "Wrong bucket", "vendors": "Vendor scope", "scope": "Scope review", "submissions": "Submissions", "duplicates": "Duplicates", "websites": "Missing websites",
         "boards": "No board found", "blocked": "Blocked boards", "placement": "Wrong placement",
         "unclassified": "Unclassified roles", "acquisitions": "Acquisitions",
         "review": "Website review"}


# ---------------------------------------------------------------- actions

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
        if keep.get(k) in (None, "", {}, []) and v not in (None, "", {}, []):
            keep[k] = v
            filled.append(k)
        # an unknown ATS never wins over a discovered one
        if k == "ats" and (keep.get("ats") or {}).get("type") in (None, "unknown") \
                and (v or {}).get("type") not in (None, "unknown"):
            keep["ats"] = v
            filled.append("ats")
    keep.setdefault("also_known_as", [])
    if drop["name"] not in keep["also_known_as"]:
        keep["also_known_as"].append(drop["name"])
    remaining = [c for c in companies if c["id"] != drop_id]
    err = validate(remaining)
    if err:
        return {"error": err}
    bad = save_companies(remaining, "merge")
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
    for k, v in (body.get("fields") or {}).items():
        if k in allowed:
            c[k] = v
    if "ats" in (body.get("fields") or {}):
        c["ats"] = body["fields"]["ats"]
    err = validate(companies)
    if err:
        return {"error": err}
    bad = save_companies(companies, "patch")
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
    """Is this address out on the internet, rather than in here with us."""
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


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
        return {"ok": True, "unreadable": True, "url": url,
                "message": ("Their server answered but sent nothing back - most "
                            "likely a bot wall. We have learned nothing about "
                            "this address, which is different from learning it "
                            "is wrong. Open it yourself and use Save if it is "
                            "them.")}
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
    bad = save_companies(companies, "set-board")
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
    for j in raw:
        title = (j.get("title") or "").strip()
        if not title or roles.is_junk(title) or roles.is_evergreen(title):
            continue
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
    return {"ok": True, "added": added, "company": c["name"],
            "message": f"{added} posting(s) captured for {c['name']}"
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
        bad = save_companies(companies, "resolve-submission")
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
    out = []
    for c in read_companies():
        n = norm(c["name"])
        if q in n:
            out.append({"id": c["id"], "name": c["name"], "sector": c["sector"],
                        "rank": 0 if n.startswith(q) else 1})
    out.sort(key=lambda r: (r["rank"], len(r["name"])))
    return {"results": out[:12]}


def act_dismiss(body: dict) -> dict:
    dismiss(body.get("queue", ""), body.get("key", ""), body.get("why", ""))
    return {"ok": True, "message": "dismissed"}


def act_move(body: dict) -> dict:
    """Move a company to a sector and category in one write.

    Dragging across sectors needs both fields to change together. Setting the
    sector alone would leave the old category behind, which validate() refuses -
    correctly, since 'Police' is not a category of General Gov.
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
    bad = save_companies(companies, "move")
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
           "scope": act_scope, "scope-all": act_scope_all,
           "vendor-scope": act_vendor_scope,
           "vendor-scope-all": act_vendor_scope_all,
           "also": act_also, "retry-board": act_retry_board, "save-website": act_save_website, "posts-at": act_posts_at, "set-founded": act_set_founded, "identity-ruling": act_identity_ruling, "place": act_place,
           "submit": act_submit, "resolve-submission": act_resolve_submission,
           "inspect-submission": act_inspect_submission,
           "dismiss": act_dismiss}


# ---------------------------------------------------------------- server

CAPTURE_PAGE = """<!doctype html><meta charset=utf-8>
<title>GovTech Dock — page capture</title>
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
<p>For the boards the fetcher cannot read. Your browser runs the widgets, iframes
and sessions ours does not, so if you can see the jobs, this can take them.</p>

<h2>Install</h2>
<p>Drag this to your bookmarks bar:</p>
<a class="drag" href="__LOADER__">GTD capture</a>
<p style="font-size:13px">If your bookmarks bar is hidden, press
<code>&#8984;&#8679;B</code> first. Right-clicking the button and copying the link
also works — make a bookmark by hand and paste it as the URL.</p>

<h2>Use</h2>
<ol>
 <li>Open a careers page, or a LinkedIn jobs list, in your normal browser.</li>
 <li>Click <b>GTD capture</b>. A panel lists every job link it can see.</li>
 <li>Uncheck anything that is not a posting, then <b>Copy for admin</b>.</li>
 <li>Paste it into the <a href="/#capture">Capture tab</a> here and pick the
     company. It lands in <code>data/manual.json</code>.</li>
</ol>
<p style="font-size:13px">The clipboard is the handoff because the page cannot
reach this server: Chrome will not let an https site talk to
<code>127.0.0.1</code>. Copying works everywhere and needs no permissions.</p>

<div class="note">
Captured postings are never deleted by an automated run. Absence from a refresh
means the fetcher still cannot see that company, not that the role closed, so
only <code>python scripts/manual.py none</code> closes one.
</div>

<h2>What it does not do</h2>
<p>It reads the page you have open, once, when you click. It does not scroll,
paginate, follow links, log in, or run on its own — which is the line between
reading a page you opened and harvesting a site. On LinkedIn that matters: use it
on a list you are already looking at, not as a crawler.</p>

<h2>When it finds nothing</h2>
<p>Usually the board is inside an iframe. Right-click it &rarr; <i>This Frame</i>
&rarr; <i>Show Only This Frame</i>, then click the bookmarklet again.</p>

<p style="margin-top:34px;font-size:13px;color:var(--faint)">
The button carries all of <code>scripts/capture.js</code> (__LINES__ lines,
__SIZE__) in its own URL, because a page on https cannot load anything from a
loopback server. Editing the script means dragging the button again.
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
   own fetch() calls stay exactly as they were. */
(function () {
  var T = "__TOKEN__", H = "__HEADER__", F = window.fetch;
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
    stop having a directory. Three routes are served, listed below, and
    everything else is 404 by construction rather than by check.
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
        return secrets.compare_digest(self.headers.get(TOKEN_HEADER) or "", TOKEN)

    # ------------------------------------------------------------ writing

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # nothing here is cacheable and nothing here should ever be sniffed
        # into a script tag by another page
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
                # token itself. The admin page is same-origin so it can read
                # this; the capture extension holds a host permission for this
                # server so it can too; a page on any other origin can send the
                # request but cannot read the reply, because no response here
                # carries a CORS header any more.
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
        return self._json({"error": "not found"}, 404)

    # Logos are the one static directory the admin serves. Serving ROOT is what
    # handed out /.git/config and /data/companies.json, so this resolves the
    # file and asserts the logo directory is genuinely its parent rather than
    # trusting the string - "..%2f" and a symlink both die on the resolve.
    LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
                  ".ico": "image/x-icon", ".svg": "image/svg+xml"}

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
            return self._json(triage(companies, board))
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
            return self._json({"items": QUEUES[name](companies, board)[:400]})
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
        shim = TOKEN_SHIM.replace("__TOKEN__", TOKEN) \
                         .replace("__HEADER__", TOKEN_HEADER)
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
    print("GovTech Dock admin\n")
    for k, f in QUEUES.items():
        print(f"  {len(f(companies, board)):>5}  {LABEL[k]}")
    # Loopback only, on purpose. This writes to companies.json with no auth in
    # front of it, so it must not be reachable from the network - and because
    # the browser can reach loopback even when the network cannot, the /api/
    # token above is what keeps a page the owner happens to be visiting from
    # driving it.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port), Handler) as srv:
        print(f"\nhttp://127.0.0.1:{a.port}   (loopback only; ctrl-c to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
