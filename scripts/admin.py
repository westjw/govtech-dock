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
import datetime as dt
import http.server
import json
import os
import pathlib
import re
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

    recs = []
    if visible:
        recs.append({"queue": "miscategorized", "n": visible,
                     "headline": f"{visible} miscategorised companies are hiring right now",
                     "why": "they are the top rows of the public Companies tab, "
                            "filed in the bucket meant for things that are not products"})
    if rulable:
        recs.append({"queue": "vendors", "n": rulable,
                     "headline": f"{rulable} horizontal vendors sit in {len(families) - 1} families",
                     "why": "each family takes one decision, so this clears far "
                            "faster than its count suggests"})
    if counts.get("duplicates"):
        recs.append({"queue": "duplicates", "n": counts["duplicates"],
                     "headline": f"{counts['duplicates']} duplicate pairs",
                     "why": "small, and every merge keeps the research from both sides"})
    if counts.get("submissions"):
        recs.append({"queue": "submissions", "n": counts["submissions"],
                     "headline": f"{counts['submissions']} waiting from outside",
                     "why": "someone is waiting on an answer"})

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


def _vkey(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def act_also(body: dict) -> dict:
    """Add or drop an extra department for a company.

    Moving is the wrong verb for a vendor that genuinely sells into several:
    Tyler under Courts is not Tyler leaving General Gov. The primary stays
    put and this adds alongside it, so a filter on either finds them.
    """
    cid, sector, category = body.get("id"), body.get("sector"), body.get("category")
    if not cid or not sector or not category:
        return {"error": "need a company, a sector and a category"}
    companies = read("companies.json", [])
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
    write_atomic("companies.json", companies)
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


def act_retry_board(body: dict) -> dict:
    """Re-probe one blocked company right now, instead of waiting a week.

    For when the block looked transient, or the website field was just
    fixed. Same probe, same verification: a slug is written only after a
    real fetch confirmed the board reads.
    """
    cid = body.get("id")
    companies = read("companies.json", [])
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    if not c.get("website"):
        return {"error": "no website on file - add one first"}
    # two paths only: this runs inside a single-threaded server, and a
    # deliberate button press may wait seconds, not minutes
    ats_block, careers, notes = add_company.find_ats(c["website"],
                                                     paths=["/careers"])
    log = read("discovery_log.json", {})
    if ats_block:
        okay, why = discover_ats.verify(ats_block)
        if okay:
            c["ats"] = ats_block
            err = validate(companies)
            if err:
                return {"error": err}
            write_atomic("companies.json", companies)
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


QUEUES = {"miscategorized": q_miscategorized, "vendors": q_vendor_scope, "scope": q_scope, "submissions": q_submissions, "duplicates": q_duplicates, "websites": q_websites, "boards": q_boards, "blocked": q_blocked,
          "placement": q_placement, "unclassified": q_unclassified,
          "acquisitions": q_acquisitions, "review": q_review}

LABEL = {"miscategorized": "Wrong bucket", "vendors": "Vendor scope", "scope": "Scope review", "submissions": "Submissions", "duplicates": "Duplicates", "websites": "Missing websites",
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
    companies = read("companies.json", [])
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
    write_atomic("companies.json", remaining)
    return {"ok": True, "message": f"merged {drop['name']} into {keep['name']}"
                                   + (f", inherited {', '.join(sorted(set(filled)))}"
                                      if filled else "")}


def act_patch(body: dict) -> dict:
    """Edit one company's fields. Validation runs on the whole file, so a change
    that breaks a sector/category pairing is refused rather than written."""
    companies = read("companies.json", [])
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
    write_atomic("companies.json", companies)
    return {"ok": True, "message": f"updated {c['name']}"}


def clean_url(raw: str) -> str | None:
    """A URL with no host is not a URL. An empty box used to become 'https://',
    which fetched, returned nothing, and offered to save a page-scan board whose
    ref was literally 'https://'."""
    u = (raw or "").strip()
    if not u:
        return None
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    host = u.split("//", 1)[1].split("/")[0]
    return u if "." in host and len(host) > 3 else None


def act_verify_website(body: dict) -> dict:
    """Check a URL before writing it. A live page is not evidence - parked
    domains and unrelated businesses all answer on the obvious name - so this
    reports what the page says about itself and lets a person decide."""
    url, name = clean_url(body.get("url")), body.get("name") or ""
    if not url:
        return {"error": "enter a URL first"}
    try:
        r = add_company.fetch(url)
        html = r[0] if isinstance(r, tuple) else r
    except Exception as exc:
        return {"error": f"could not fetch: {type(exc).__name__}"}
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", title.group(1)).strip()[:140] if title else ""
    parked = bool(find_websites.PARKED.search(html[:4000]))
    base = url.split("//", 1)[1].split("/")[0].replace("www.", "").rsplit(".", 1)[0]
    return {"ok": True, "title": title, "parked": parked,
            "identifies": find_websites.identifies(html, name, base),
            "url": url}


def act_verify_board(body: dict) -> dict:
    """Detect the ATS behind a careers URL and prove it returns this company's
    jobs. slug_matches is what keeps an off-site careers link from wiring a
    company to somebody else's board, which is how acquisitions surface."""
    url = clean_url(body.get("url"))
    if not url:
        return {"error": "enter a careers URL first"}
    companies = read("companies.json", [])
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
    companies = read("companies.json", [])
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
    write_atomic("companies.json", companies)
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
    companies = read("companies.json", [])
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
        companies = read("companies.json", [])
        cid = fields.get("id") or re.sub(r"[^a-z0-9]+", "-",
                                         (fields.get("name") or item["name"]).lower()).strip("-")
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
        write_atomic("companies.json", companies)
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
    url = clean_url(body.get("url"))
    if not url:
        return {"error": "no URL on that submission"}
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
    for c in read("companies.json", []):
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
    companies = read("companies.json", [])
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
    write_atomic("companies.json", companies)
    return {"ok": True, "message": f"{c['name']}: {was} -> {sec} / {cat}",
            "sector": sec, "category": cat}


def sort_companies(sector: str) -> dict:
    companies = read("companies.json", [])
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
           "also": act_also, "retry-board": act_retry_board, "place": act_place,
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


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):        # keep the console readable
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"  {self.command} {self.path}\n")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        # Chrome's Private Network Access blocks a public page from reaching a
        # loopback server unless the server opts in. Without this the capture
        # bookmarklet fails with a bare "Failed to fetch" on every https site.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.path = "/admin.html"
            return super().do_GET()
        if path == "/api/triage":
            companies, board = read("companies.json", []), read("board.json", {})
            return self._json(triage(companies, board))
        if path == "/api/queues":
            companies, board = read("companies.json", []), read("board.json", {})
            return self._json({"counts": {k: len(f(companies, board))
                                          for k, f in QUEUES.items()},
                               "labels": LABEL,
                               "companies": len(companies),
                               "postings": len(board.get("postings", [])),
                               "generated": board.get("generated")})
        if path.startswith("/api/queue/"):
            name = path.rsplit("/", 1)[-1]
            if name not in QUEUES:
                return self._json({"error": "no such queue"}, 404)
            companies, board = read("companies.json", []), read("board.json", {})
            return self._json({"items": QUEUES[name](companies, board)[:400]})
        if path == "/capture":
            return self._capture_page()
        if path == "/capture.js":
            js = (pathlib.Path(__file__).parent / "capture.js").read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(js)))
            self.end_headers()
            return self.wfile.write(js)
        if path == "/api/sort/companies":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._json(sort_companies((qs.get("sector") or [""])[0]))
        if path == "/api/sort/roles":
            return self._json(sort_roles())
        if path == "/api/schema":
            return self._json(read("schema.json", {}))
        if path == "/api/families":
            return self._json(roles.LABEL)
        return super().do_GET()

    def _capture_page(self):
        js = (pathlib.Path(__file__).parent / "capture.js").read_text()
        # A bookmarklet has to be one URI-encoded line. Loading the real file
        # from the local server instead of inlining it means editing capture.js
        # takes effect on the next click, with no reinstall.
        # Self-contained on purpose. Chrome blocks a page on https from loading
        # anything off http://127.0.0.1 - fetch and script tag alike - so a
        # loader bookmarklet would fail on every real careers site. The whole
        # script rides in the URL instead, which is why editing capture.js means
        # dragging the button again.
        loader = "javascript:" + urllib.parse.quote(js, safe="")
        html = CAPTURE_PAGE.replace("__LOADER__", loader.replace('"', "&quot;")) \
                           .replace("__LINES__", str(len(js.splitlines()))) \
                           .replace("__SIZE__", f"{len(loader) // 1024} KB")
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        action = path.rsplit("/", 1)[-1]
        if action not in ACTIONS:
            return self._json({"error": f"unknown action {action}"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad request body"}, 400)
        try:
            out = ACTIONS[action](body)
        except Exception as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json(out, 400 if out.get("error") else 200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()

    companies, board = read("companies.json", []), read("board.json", {})
    print("GovTech Dock admin\n")
    for k, f in QUEUES.items():
        print(f"  {len(f(companies, board)):>5}  {LABEL[k]}")
    # Loopback only, on purpose. This writes to companies.json with no auth in
    # front of it, so it must not be reachable from the network.
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
