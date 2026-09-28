#!/usr/bin/env python3
"""The page belt: one company page at a time, a checklist that fixes itself.

Built to the owner's drawing (2026-09-28): pick a sector and, optionally, a
subsector; the belt brings its companies up alphabetically, one at a time.
The left of the screen is the company page as it will publish; the right is
a checklist of nine things a good page needs, each green or red, and EVERY
item - green or red - opens an editor on the same screen. Submit signs the
page off even with red items; the red ones keep showing in their own admin
queues (and in "Open items on approved pages"), and a page that later LOSES
something shows up there too - never back on the belt.

This module owns three things and nothing else:
  checklist(c, ctx)  - the nine items, one definition, computed server-side
  belt list / card   - what the belt shows
  the page-* actions - the edits, each through the house doors
                       (save_companies / save_decisions, journalled, `by`
                       always passed, never in OPEN_ACTIONS)

WHAT GREEN MEANS, per item, is written next to each predicate. The rule for
all of them: green is a fact the file can show, never "a value exists". A
description is on all 2,041 records and 870 of them are conference stubs; a
vendor_type is on all 2,041 and says the same thing on every one. Presence
would paint the whole board green and teach the owner to stop reading it.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                     # noqa: E402

# THE NINE KEYS ARE STORED in every sign-off (page_reviews.json), so they are
# permanent: renaming one later would mark every approved page as changed.
ITEMS = ("website", "board", "writeup", "competitors", "news", "supplier",
         "buyer", "founded", "ownership")
LABEL = {"website": "Website", "board": "Job board",
         "writeup": "Description / write-up", "competitors": "Competitors",
         "news": "News", "supplier": "Supplier vs govtech",
         "buyer": "Who buys it", "founded": "Founding year",
         "ownership": "Ownership"}
# where a red item lives when it is not being fixed on the belt
QUEUE = {"website": "websites", "board": "boards", "writeup": "profiles",
         "competitors": "proposals", "news": "signedoff",
         "supplier": "miscategorized", "buyer": "signedoff",
         "founded": "founded", "ownership": "acquisitions"}

ATS_NAME = {"ashby": "Ashby", "greenhouse": "Greenhouse", "lever": "Lever",
            "workable": "Workable", "recruitee": "Recruitee",
            "breezy": "Breezy", "smartrecruiters": "SmartRecruiters",
            "bamboohr": "BambooHR", "workday": "Workday",
            "rippling": "Rippling", "jazzhr": "JazzHR", "icims": "iCIMS",
            "paylocity": "Paylocity", "oracle": "Oracle", "jibe": "Jibe",
            "adp": "ADP", "gusto": "Gusto", "gem": "Gem",
            "html": "Careers page", "unknown": "No board on file"}

# the owner's three answers to "who buys it". sells_to_gov is what tags.py
# and the rest of the pipeline read; buyer_mix is the finer fact he asked
# for. NEITHER touches sled_only: that is a posting filter that deletes
# every title not naming the public sector, and a checklist toggle must
# never be able to delete jobs.
BUYER_MIX = {"gov_only": ("yes", "Government only"),
             "gov_and_business": ("yes", "Government and businesses"),
             "business_only": ("no", "Businesses, not government")}

INBOX_NAME = "claude_inbox.jsonl"         # gitignored: private words, public repo


def inbox_path() -> pathlib.Path:
    """Resolved at call time against admin.DATA, so a sandboxed test writes
    its sandbox and never the real inbox."""
    return admin.DATA / INBOX_NAME
SCRUB = "scrub.json"                      # the manual sweep's send list
SCRUB_DAYS = 14                           # the owner's cadence (2026-09-28)


def _today() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- context

def context(companies: list, board: dict) -> dict:
    """Everything the checklist reads, loaded once per request.

    founded_provenance.json is READ directly, never through
    admin.founded_provenance(): that function rewrites the file whenever the
    journal has grown, and a checklist must not write on read.
    """
    store = admin.read("agent_proposals.json", {})
    pending: dict = {}
    for k, v in (store.items() if isinstance(store, dict) else []):
        if isinstance(v, dict) and v.get("status") == "pending" and v.get("id"):
            pending.setdefault(v["id"], []).append(dict(v, key=k))
    man = admin.read("manual.json", {})
    captured: dict = {}
    for p in man.get("postings") or []:
        if isinstance(p, dict) and p.get("company_id"):
            d = p.get("first_seen") or ""
            captured[p["company_id"]] = max(captured.get(p["company_id"], ""), d)
    si = admin.read("site_identity.json", {})
    ident = {r.get("id"): r for r in (si.get("rows") or [])
             if isinstance(r, dict)}
    dup_ids: dict = {}
    try:
        for g in admin.q_duplicates(companies, board):
            ids = [m.get("id") for m in g.get("members") or []]
            for i in ids:
                dup_ids[i] = [x for x in ids if x != i]
    except Exception:                                   # noqa: BLE001
        pass
    acq_ids: dict = {}
    try:
        for r in admin.q_acquisitions(companies, board):
            acq_ids[r.get("id")] = {"strength": r.get("strength"),
                                    "says": r.get("says") or r.get("note") or "",
                                    "parent_claim": r.get("parent_claim")}
    except Exception:                                   # noqa: BLE001
        pass
    def ids_in(q):
        try:
            return {r.get("id") for r in q(companies, board) if isinstance(r, dict)}
        except Exception:                               # noqa: BLE001
            return set()
    boards_q = ids_in(admin.q_boards)
    # the queues a red item may link to, by who they really list: a pending
    # write-up draft is in "Write-ups to check" only when refused or below
    # high confidence, and a rival or buyer answer is in Agent proposals
    profiles_q = ids_in(admin.q_profiles)
    proposals_q = ids_in(admin.q_proposals)
    dismissed = admin.dismissed()
    return {
        "boards_q": boards_q, "profiles_q": profiles_q, "proposals_q": proposals_q,
        "news": admin.read("news.json", {}),
        "pending": pending,
        "checks": man.get("checks") or {},
        "captured": captured,
        "ident": ident,
        "provenance": admin.read("founded_provenance.json", {}),
        "dups": dup_ids,
        "acq": acq_ids,
        "founded_dismissed": (dismissed.get("founded") or {}),
        "orgs": {o.get("id"): o for o in board.get("organizations", [])},
        "names": {c["id"]: c.get("name") for c in companies},
        "scrub": admin.read(SCRUB, {}),
        "reviews": admin.read("page_reviews.json", {}),
    }


def last_swept(cid: str, ctx: dict) -> str | None:
    """When a person last swept this company's openings by hand. The owner's
    cadence question - "the last time I swept it manually" - answered from
    the three places a sweep leaves a mark."""
    marks = [ctx["captured"].get(cid) or ""]
    chk = ctx["checks"].get(cid)
    if isinstance(chk, dict):
        marks.append(chk.get("checked_on") or "")
    s = (ctx["scrub"] or {}).get(cid)
    if isinstance(s, dict):
        marks.append(s.get("swept_on") or "")
    best = max(marks)
    return best or None


def cadence(cid: str, ctx: dict, today: dt.date | None = None) -> dict:
    """The 14-day sweep cadence for one company, in ONE place: the checklist,
    the jobs box and the Scrub tab all read it, so none of them can disagree
    about the day a sweep comes due. A sweep is due ON day 14 - "comes back
    when its last sweep is 14 days old" - never a day later on one screen."""
    today = today or dt.date.today()
    swept = last_swept(cid, ctx)
    age = (today - dt.date.fromisoformat(swept[:10])).days if swept else None
    due = age is None or age >= SCRUB_DAYS
    return {"last_swept": swept, "days_since": age, "due": due,
            "due_on": (None if due else
                       (dt.date.fromisoformat(swept[:10])
                        + dt.timedelta(days=SCRUB_DAYS)).isoformat())}


def board_verdict(c: dict, org: dict | None) -> tuple[bool, str]:
    """Can the nightly crawl read this board? THE BOARD'S OWN VERDICT - the
    same test the public page prints (build_site: enumerable is not False
    and nothing unreadable). refresh's hiring.status is a different
    question: 'Yes' there can be a keyword hit with no title behind it, and
    192 of 808 careers pages disagreed with the page a visitor sees."""
    kind = (c.get("ats") or {}).get("type") or "unknown"
    h = c.get("hiring") or {}
    if kind not in admin.STRUCTURED_ATS and kind != "html":
        return False, "no board on file"
    if not org:
        return False, "not crawled yet"
    if org.get("unreadable"):
        return False, str(org["unreadable"])[:160]
    if org.get("enumerable") is False:
        return False, (h.get("note") or "the page scan found no listings")[:160]
    return True, ("read every night" if kind in admin.STRUCTURED_ATS
                  else "a page scan reads it")


def on_scrub(c: dict, ctx: dict) -> bool:
    """Is this company on the Scrub tab? One predicate for the tab and the
    jobs box: a careers page the crawl cannot read, a place they post that a
    person can capture, or a company sent from the belt. A structured board
    is read every night and is never on it."""
    import posts_at as _pa
    kind = (c.get("ats") or {}).get("type")
    if kind in admin.STRUCTURED_ATS:
        return False
    if admin.is_dismissed("scrub", c["id"]):
        return False
    pa = c.get("posts_at")
    unread_page = kind == "html" and not board_verdict(c, ctx["orgs"].get(c["id"]))[0]
    return bool(unread_page or (pa and _pa.reach(pa) == "capture")
                or c["id"] in (ctx["scrub"] or {}))


# --------------------------------------------------------------- checklist

def _item(key, green, state, detail="", queue=None, **extra) -> dict:
    """One checklist row. `queue` is set only where that queue really lists
    this company - a link to a queue that does not hold it is a promise the
    screen cannot keep."""
    return {"key": key, "label": LABEL[key], "green": bool(green),
            "state": state, "detail": detail, "queue": queue, **extra}


def jobs_box(c: dict, ctx: dict) -> dict:
    """The drawing's JOBS box: the board by name, whether we can read it,
    and when a person last swept it."""
    import posts_at as _pa
    ats = c.get("ats") or {}
    kind = ats.get("type") or "unknown"
    ref = ats.get("ref")
    h = c.get("hiring") or {}
    org = ctx["orgs"].get(c["id"]) or {}
    url = None
    if kind == "html" and isinstance(ref, str):
        url = ref
    elif org.get("board_url"):
        url = org.get("board_url")
    readable, why = board_verdict(c, ctx["orgs"].get(c["id"]))
    pa = c.get("posts_at")
    cad = cadence(c["id"], ctx)
    return {
        "type": kind, "name": ATS_NAME.get(kind, kind), "url": url,
        "readable": bool(readable), "why": why,
        "open_roles": org.get("open_roles", 0) or 0,
        "checked": h.get("checked"),
        "posts_at": ({"where": pa.get("where"), "label": _pa.label(pa.get("where")),
                      "url": pa.get("url"), "reach": _pa.reach(pa)}
                     if isinstance(pa, dict) else None),
        "last_swept": cad["last_swept"], "due": cad["due"],
        "due_on": cad["due_on"],
        # on the Scrub tab by the tab's own rule, and separately whether it
        # was sent from the belt (which puts it at the top)
        "on_scrub": on_scrub(c, ctx),
        "sent_from_belt": c["id"] in (ctx["scrub"] or {}),
    }


def checklist(c: dict, ctx: dict) -> list:
    """The nine items for one company, in the owner's order."""
    import posts_at as _pa
    cid = c["id"]
    out = []

    # WEBSITE. Green: an address on file that is not a hijacked domain.
    # site_identity's 'spam' rows are sites now serving casino pages; a
    # website_removed record is one somebody already pulled for that reason.
    ident = ctx["ident"].get(cid) or {}
    site = (c.get("website") or "").strip()
    if not site:
        out.append(_item("website", False, "no website on file",
                         queue=QUEUE["website"]))
    elif c.get("website_removed") or ident.get("kind") == "spam":
        out.append(_item("website", False, "the site on file is not theirs",
                         ident.get("kind") or "removed", queue=QUEUE["website"],
                         url=site))
    else:
        out.append(_item("website", True, site.replace("https://", "")
                         .replace("http://", "").rstrip("/"), url=site))

    # JOB BOARD. The jobs box's own verdict: a structured board read nightly,
    # a careers page whose scan produced a verdict, a place they post that
    # has nothing to capture (an outside recruiter, email), or a capture
    # swept within the cadence.
    jb = jobs_box(c, ctx)
    pa = c.get("posts_at")
    reach = _pa.reach(pa) if pa else None
    swept = jb["last_swept"]
    scrubq = "scrub" if jb["on_scrub"] else None
    if jb["type"] in admin.STRUCTURED_ATS or (jb["type"] == "html" and jb["readable"]):
        out.append(_item("board", jb["readable"],
                         f"{jb['name']}{'' if jb['readable'] else ' - not reading'}",
                         jb["why"]))
    elif reach == "none":
        out.append(_item("board", True, f"posts {jb['posts_at']['label']}",
                         "nothing to capture"))
    elif reach == "capture" or jb["type"] == "html":
        # `cadence` marks a red that is ONLY the sweep coming due: nothing was
        # taken off the page, so an approved page is not reported as having
        # LOST its board every 14 days
        out.append(_item("board", not jb["due"],
                         (f"swept {swept}" + (" - sweep due" if jb["due"] else "")
                          if swept else "never swept by hand"),
                         jb["why"] if jb["type"] == "html"
                         else f"posts on {jb['posts_at']['label']}",
                         queue=scrubq, cadence=bool(swept and jb["due"])))
    else:
        out.append(_item("board", False, "no board on file", jb["why"],
                         queue=scrubq or ("boards" if cid in ctx["boards_q"] else None)))

    # DESCRIPTION / WRITE-UP, one item to the owner. Green: a write-up is on
    # the page and not hidden. The one-line description is on every record,
    # so it cannot make this green; a conference stub is said out loud.
    prof = c.get("profile") if isinstance(c.get("profile"), dict) else None
    paras = (prof or {}).get("paragraphs") or []
    stub = bool(re.search(r"exhibited at ", c.get("description") or ""))
    if paras and not c.get("profile_hidden"):
        out.append(_item("writeup", True, f"{len(paras)} paragraph(s)",
                         "written by the owner" if (prof or {}).get("hand_written")
                         else "", stub_description=stub))
    else:
        drafted = any(p.get("kind") == "profile" for p in ctx["pending"].get(cid, []))
        out.append(_item("writeup", False,
                         ("a draft is waiting" if drafted else "no write-up")
                         + ("; the description is a conference stub" if stub else ""),
                         queue="profiles" if cid in ctx["profiles_q"] else None,
                         stub_description=stub))

    # COMPETITORS. Green: a shortlist, or a person's "none found" - an
    # answer, distinct from never having looked.
    comps = c.get("competitors") or []
    if comps:
        names = [ctx["names"].get(r.get("id"), r.get("id")) for r in comps
                 if isinstance(r, dict)]
        out.append(_item("competitors", True, ", ".join(n for n in names if n)[:120]))
    elif c.get("competitors_none_found"):
        out.append(_item("competitors", True, "none found"))
    else:
        has_prop = any(p.get("kind") in ("rival", "rivweb")
                       for p in ctx["pending"].get(cid, []))
        out.append(_item("competitors", False,
                         "a shortlist is waiting" if has_prop else "none on file",
                         queue="proposals" if cid in ctx["proposals_q"] else None))

    # NEWS. Green: the news sweep read their newsroom and got items, or read
    # it and found none. 'unread' is NOT checked - 71 companies sat green on
    # the old sweep because the only test was "in news.json".
    n = ctx["news"].get(cid) or {}
    state = n.get("state") or ("items" if n.get("items") else None)
    if state == "items" or n.get("items"):
        items = n.get("items") or []
        top = items[0] if items else {}
        out.append(_item("news", True, f"{len(items)} item(s)",
                         f"{top.get('date', '')} {top.get('headline', '')}".strip()[:140]))
    elif state == "none_found":
        out.append(_item("news", True, "checked, none found",
                         f"checked {n.get('checked_on') or '?'}"))
    else:
        out.append(_item("news", False,
                         "could not read their news" if state == "unread"
                         else "never checked"))

    # SUPPLIER VS GOVTECH. Every record on the map says 'GovTech Product',
    # so the value proves nothing. Red is the one live contradiction: a
    # govtech record filed under Suppliers & Services.
    if c.get("govtech") is False or c.get("category") == "Suppliers & Services":
        out.append(_item("supplier", False, "filed under Suppliers & Services",
                         "is this a govtech product or a supplier?"))
    else:
        out.append(_item("supplier", True, "GovTech product",
                         f"{c.get('sector')} / {c.get('category')}"))

    # WHO BUYS IT. Green: a person or the buyer door answered yes or no.
    # 'unclear' is not an answer. The mix (government only vs government and
    # businesses) is shown when recorded.
    # The question the owner drew is the MIX - government only, or government
    # and businesses. "Sells to government" alone does not answer it, so it
    # is red until the mix is recorded; "does not sell to government" is an
    # answer on its own.
    stg = c.get("sells_to_gov")
    mix = c.get("buyer_mix")
    has_prop = any(p.get("kind") == "buyer" for p in ctx["pending"].get(cid, []))
    if mix in BUYER_MIX:
        out.append(_item("buyer", True, BUYER_MIX[mix][1],
                         (c.get("buyer") or "")[:160]))
    elif stg == "no":
        out.append(_item("buyer", True, "does not sell to government",
                         (c.get("buyer") or "")[:160]))
    elif stg == "yes":
        out.append(_item("buyer", False,
                         "sells to government; only government, or businesses too?",
                         (c.get("buyer") or "")[:160]))
    else:
        out.append(_item("buyer", False,
                         "unclear" if stg == "unclear"
                         else ("an answer is waiting" if has_prop else "never read"),
                         queue="proposals" if cid in ctx["proposals_q"] else None))

    # FOUNDING YEAR. Green: a year nobody has reason to doubt - set by a
    # person, confirmed, or from the original research. Red: blank, or a
    # machine-written year nobody has confirmed. A blank a person answered
    # "not stated anywhere" is an answer, and green.
    y = c.get("year_founded")
    prov = ctx["provenance"].get(cid) if isinstance(ctx["provenance"], dict) else None
    unconfirmed = (isinstance(prov, dict) and not prov.get("confirmed")
                   and not admin._is_person(prov.get("by")))
    if y and not unconfirmed:
        out.append(_item("founded", True, str(y),
                         c.get("founded_source") or ""))
    elif y:
        out.append(_item("founded", False, f"{y} - written by a machine, unconfirmed",
                         queue=None if cid in ctx["founded_dismissed"] else QUEUE["founded"]))
    elif cid in ctx["founded_dismissed"]:
        out.append(_item("founded", True, "not stated anywhere",
                         str((ctx["founded_dismissed"][cid] or {}).get("why") or "")
                         if isinstance(ctx["founded_dismissed"][cid], dict) else ""))
    else:
        out.append(_item("founded", False, "blank", queue=QUEUE["founded"]))

    # OWNERSHIP. Green: somebody looked (a parent recorded, or checked and
    # independent) and nothing open says otherwise - no duplicate pair and
    # no board that reads like someone else's.
    # An ANSWER is a parent, an acquisition, or "independent". A check that
    # found something and never recorded it (acquisitions_checked_on alone)
    # is not an answer - 64 records sat green as "never checked" that way,
    # 20 of them naming their owner in their own acquisition_note.
    parent = (c.get("parent") or "").strip()
    answered = bool(parent or c.get("acquired") or c.get("acquisitions_none_found"))
    # admin._is_person(None) is True - a missing author reads as the owner -
    # so the field must be PRESENT before it can say a person answered
    by_person = bool(c.get("ownership_by")) and admin._is_person(c.get("ownership_by"))
    flags, queue = [], None
    if cid in ctx["dups"]:
        flags.append("possible duplicate of "
                     + ", ".join(ctx["names"].get(i, i) for i in ctx["dups"][cid]))
        queue = "duplicates"
    acq = ctx["acq"].get(cid)
    if isinstance(acq, dict):
        claim = (acq.get("parent_claim") or "").casefold()
        settled = acq.get("strength") == "research" and (
            by_person or (parent and claim and (parent.casefold() in claim
                                                or claim in parent.casefold())))
        if not settled:
            flags.append(acq.get("says") or "the acquisitions queue has a question")
            queue = queue or "acquisitions"
    if ident.get("kind") == "names_another_company" and not by_person:
        # only names that are companies on the map, and not the parent on file:
        # "Line", "Number" and "Chlorine" are words, and a site that talks
        # about its recorded parent is saying what we already know
        on_map = {(n or "").casefold() for n in ctx["names"].values()}
        others = [n for n, _ in (ident.get("names_instead") or [])
                  if (n or "").casefold() in on_map
                  and (n or "").casefold() != parent.casefold()]
        if others:
            flags.append("their site talks mostly about " + ", ".join(others[:2]))
    if parent:
        state_txt = f"part of {parent}"
    elif c.get("acquired"):
        state_txt = "acquired"
    elif c.get("acquisitions_none_found"):
        state_txt = "independent"
    elif c.get("acquisitions_checked_on"):
        state_txt = "checked, not answered"
        flags.append((c.get("acquisition_note") or "a check ran and recorded no answer")[:160])
    else:
        state_txt = "never checked"
    out.append(_item("ownership", answered and not flags, state_txt,
                     "; ".join(flags), queue=queue,
                     brands=[b.get("name") for b in c.get("brands") or []
                             if isinstance(b, dict)]))
    return out


def reds(c: dict, ctx: dict) -> list:
    """Red keys for the LOSS test on approved pages. A board whose only red
    is the sweep coming due is left out: the cadence belongs to the Scrub
    tab, and nothing was taken off the page."""
    return [i["key"] for i in checklist(c, ctx)
            if not i["green"] and not i.get("cadence")]


# ------------------------------------------------------------- belt + card

def belt(companies: list, board: dict, sector: str, category: str | None) -> dict:
    """The belt for one sector (and optional subsector): unsigned pages,
    alphabetical. A signed-off page never comes back here - whatever it
    later loses shows up in its own queue instead."""
    reviews = admin.read("page_reviews.json", {})
    rows = [c for c in companies if c.get("sector") == sector
            and (not category or c.get("category") == category)]
    rows.sort(key=lambda c: (c.get("name") or "").casefold())
    todo = [c for c in rows if c["id"] not in reviews]
    return {"sector": sector, "category": category or None,
            "total": len(rows), "signed_off": len(rows) - len(todo),
            "items": [{"id": c["id"], "name": c.get("name"),
                       "category": c.get("category")} for c in todo]}


def card(companies: list, board: dict, cid: str) -> dict:
    c = next((x for x in companies if x.get("id") == cid), None)
    if c is None:
        return {"error": "company not found"}
    ctx = context(companies, board)
    rows = checklist(c, ctx)
    pend = [{"key": p["key"], "kind": p.get("kind"),
             "confidence": p.get("confidence"), "why": p.get("why"),
             "value": p.get("value"), "field": p.get("field"),
             "rivals": [dict(r, name=ctx["names"].get(r.get("id"), r.get("id")))
                        for r in (p.get("rivals") or []) if isinstance(r, dict)],
             "paragraphs": p.get("paragraphs"), "verdict": p.get("verdict"),
             "sells_to_gov": p.get("sells_to_gov"), "buyer": p.get("buyer"),
             "names_other_buyers": p.get("names_other_buyers")}
            for p in ctx["pending"].get(cid, [])]
    same_cat = sorted(({"id": x["id"], "name": x.get("name")} for x in companies
                       if x.get("category") == c.get("category")
                       and x.get("sector") == c.get("sector") and x["id"] != cid),
                      key=lambda r: (r["name"] or "").casefold())
    import brand as _brand
    return {"company": {k: c.get(k) for k in (
                "id", "name", "sector", "category", "website", "description",
                "year_founded", "location", "parent", "also_known_as",
                "competitors", "competitors_none_found", "sells_to_gov",
                "buyer", "buyer_mix", "brands", "acquired", "profile",
                "profile_hidden", "posts_at", "ats")},
            "checklist": rows, "jobs": jobs_box(c, ctx), "pending": pend,
            "news": (ctx["news"].get(cid) or {}),
            "news_job": news_job(cid),
            # the saved shortlist WITH names: a rival from outside this
            # category is not in same_category and drew as its raw id
            "competitor_names": {r.get("id"): ctx["names"].get(r.get("id"), r.get("id"))
                                 for r in (c.get("competitors") or [])
                                 if isinstance(r, dict)},
            "counts": _counts(companies, c),
            "reviewed": ctx["reviews"].get(cid),
            "same_category": same_cat,
            "live": f"{_brand.SITE}/c/{cid}"}


# ---------------------------------------------------------------- actions

def _find(companies, cid):
    return next((x for x in companies if x.get("id") == cid), None)


def _by(body):
    return (body.get("by") or "owner").strip()


def _save(companies, action, why, body) -> dict | None:
    err = admin.validate(companies)
    if err:
        return {"error": err}
    bad = admin.save_companies(companies, action, why=why, by=_by(body))
    return {"error": bad} if bad else None


def act_title(body: dict) -> dict:
    """Rename a company. The old name is kept as an alias - a dropped name
    stays findable - and a name another company already carries is refused,
    because two records answering to one name is how a merge goes wrong."""
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()
    if not name:
        return {"error": "a company needs a name"}
    if name == c["name"]:
        return {"ok": True, "message": "no change"}
    key = name.casefold()
    for x in companies:
        if x is c:
            continue
        if (x.get("name") or "").casefold() == key or key in {
                a.casefold() for a in x.get("also_known_as") or []}:
            return {"error": f"{x['name']} already answers to {name!r}. If they "
                             f"are one company, merge them in Duplicates"}
    aka = set(c.get("also_known_as") or [])
    aka.add(c["name"])
    aka.discard(name)
    was = c["name"]
    c["also_known_as"] = sorted(aka)
    c["name"] = name
    bad = _save(companies, "page-title", f"renamed {was} to {name}", body)
    return bad or {"ok": True, "message": f"renamed to {name}; {was} kept as an alias"}


def act_website(body: dict) -> dict:
    """Save a website WITHOUT touching the board. The older save-website also
    probes /careers and overwrites the ats with whatever it finds, with no
    ownership check; on a page that already has a correct board that is how
    a pasted website rewires it to a parent's. The job board has its own
    item here."""
    url, why = admin.outward_url(body.get("url"))
    if not url:
        return {"error": why or "paste the company's website"}
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    was = c.get("website")
    c["website"] = url
    c.pop("website_removed", None)
    bad = _save(companies, "page-website", f"website {was or '(none)'} -> {url}", body)
    return bad or {"ok": True, "message": f"website saved: {url}"}


def _host(url: str) -> str:
    import urllib.parse
    h = urllib.parse.urlparse(url if "//" in (url or "") else "https://" + (url or "")).netloc
    return h.lower().split(":")[0].removeprefix("www.")


def _same_site(page: str, website: str) -> bool:
    """The page's host is the website's host, or one sits under the other
    (careers.acme.com and acme.com)."""
    a, b = _host(page), _host(website)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def _block_from_url(url: str) -> dict | None:
    """A board named by the pasted address itself. find_ats reads the PAGE
    for markers, and Greenhouse's own board page does not contain its own
    board address - so a pasted boards.greenhouse.io link read as nothing."""
    import add_company
    for kind, pat in add_company.ATS_MARKERS:
        m = re.search(pat, url, re.I)
        if m:
            slug = next((g for g in m.groups() if g), "")
            if slug and slug.lower() not in ("www", "jobs", "careers", "api", "embed"):
                return {"type": kind, "ref": slug}
    return None


# The per-company address shapes of the places a company posts. A LinkedIn
# link to somebody else's company page is somebody else's openings.
POSTS_SLUG = re.compile(r"linkedin\.com/company/([^/?#]+)|indeed\.com/cmp/([^/?#]+)|"
                        r"wellfound\.com/company/([^/?#]+)|builtin\.com/company/([^/?#]+)",
                        re.I)


def _stamp_sweep(cid: str, how: str, by: str) -> str | None:
    """A sweep outcome that is not a capture - 'posts somewhere else',
    'real board found' - still counts toward the 14-day cadence."""
    rows = admin.read(SCRUB, {})
    row = dict(rows.get(cid) or {})
    row.update({"swept_on": _today(), "swept_how": how, "swept_by": by})
    rows[cid] = row
    return admin.save_decisions(SCRUB, rows, "scrub-swept",
                                why=f"{cid}: {how}", by=by)


def act_board(body: dict) -> dict:
    """Take a pasted link and put it where it belongs.

    OWNERSHIP IS PROVEN, NOT ASSUMED. A board is wired only when something
    positive says it is this company's: the board names them (the judge's
    'matches'), or its slug shares ground with their name or website
    (discover_ats.slug_matches), or - for a careers page - it sits on their
    own website's host. The judge cannot see a name on Ashby, Lever,
    BambooHR, Breezy, Recruitee or SmartRecruiters, so 'unknown' there is
    NOT a pass. Anything unproven is refused WITH its evidence, and the belt
    offers "wire anyway", which sends force and is journalled as such.

    A LinkedIn, Indeed or government-portal link is recorded as where they
    post. A page that reads nothing is not wired at all: it goes to the
    scrub list, which is what a page only a person can read is for.
    """
    import add_company
    import discover_ats
    import posts_at as _pa
    import verify_boards
    url, why = admin.outward_url(body.get("url"))
    if not url:
        return {"error": why or "paste a link to their job board"}
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    force = bool(body.get("force"))
    by = _by(body)
    anyway = " (wired anyway by a person)" if force else ""
    where = _pa.guess_where(url)
    if where and where not in ("own",):
        bad = _pa.check(where, url, "")
        if bad:
            return {"error": bad}
        m = POSTS_SLUG.search(url)
        slug = next((g for g in (m.groups() if m else ()) if g), "")
        if slug and not discover_ats.slug_matches(slug, c) and not force:
            return {"error": f"that {_pa.label(where)} page is {slug!r}, which does "
                             f"not look like {c['name']}. Nothing was written",
                    "mismatch": True, "evidence": {"slug": slug, "where": where}}
        c["posts_at"] = _pa.build(where, url, by, "", "")
        bad = _save(companies, "page-board",
                    f"{c['name']} posts on {where}{anyway}", body)
        if bad:
            return bad
        swept = _stamp_sweep(c["id"], "posts somewhere else", by)
        return {"ok": True, "outcome": "posts_at", "scrub_swept": swept is None,
                "message": f"recorded: they post on {_pa.label(where)}"
                           + ("" if swept is None else f" (sweep not stamped: {swept})")}
    block = _block_from_url(url)
    if block is None:
        try:
            block, note, _ = add_company.find_ats(url)
        except Exception as exc:                        # noqa: BLE001
            return {"error": f"could not read that page: {type(exc).__name__}"}
    if block and block.get("type") not in (None, "html", "unknown"):
        ok, detail = add_company.verify(block)
        if not ok:
            return {"error": f"that board does not read right now: {detail}. "
                             f"Nothing was written"}
        said = verify_boards.board_says(block["type"], block["ref"])
        who = verify_boards.judge(c, said)
        slug_ok = discover_ats.slug_matches(block["ref"], c)
        proven = who["verdict"] == "matches" or (slug_ok and who["verdict"] != "MISMATCH")
        if not proven and not force:
            if who["verdict"] == "MISMATCH":
                msg = (f"{who['why']}. That is another company's board, so it is not "
                       f"wired - it would publish their jobs as {c['name']}'s. If "
                       f"{c['name']} belongs to them, record the parent under "
                       f"Ownership, and if they hire through the parent's board, "
                       f"paste it as where they post")
            else:
                msg = (f"nothing on that board says it is {c['name']}'s: the slug "
                       f"{block['ref']!r} does not match their name or website, and "
                       f"{who['why'] or 'the board states no employer name'}. "
                       f"Nothing was written")
            return {"error": msg, "mismatch": True,
                    "evidence": {"type": block["type"], "slug": block["ref"],
                                 "slug_matches": slug_ok, "verdict": who["verdict"],
                                 "board_says": said.get("name"), "postings": detail}}
        c["ats"] = block
        if said.get("name") and said["name"].lower() != c["name"].lower():
            c["board_owner"] = said["name"]
        bad = _save(companies, "page-board",
                    f"{c['name']} reads from {block['type']}: {detail}{anyway}", body)
        if bad:
            return bad
        swept = _stamp_sweep(c["id"], "real board found", by)
        return {"ok": True, "outcome": "wired", "scrub_swept": swept is None,
                "message": f"wired to {ATS_NAME.get(block['type'], block['type'])}"
                           f" ({detail}); roles appear after the next crawl"}
    page = (block or {}).get("ref") if (block or {}).get("type") == "html" else url
    page = page if isinstance(page, str) and page.startswith("http") else url
    if not _same_site(page, c.get("website") or "") and not force:
        return {"error": f"that page is on {_host(page)}, not on their website "
                         f"({_host(c.get('website') or '') or 'none on file'}). "
                         f"Nothing was written",
                "mismatch": True, "evidence": {"host": _host(page),
                                               "website": c.get("website")}}
    titles = []
    try:
        import ats as ats_mod
        titles = [j.get("title", "") for j in ats_mod.fetch_html_titles(page)][:8]
    except Exception:                                   # noqa: BLE001
        pass
    if not titles:
        return {"ok": True, "outcome": "empty", "wrote": False,
                "message": "that page reads no job titles, so nothing was wired. "
                           "Send it to the scrub list and sweep it with the "
                           "extension"}
    c["ats"] = {"type": "html", "ref": page}
    bad = _save(companies, "page-board", f"{c['name']} careers page {page}{anyway}", body)
    if bad:
        return bad
    swept = _stamp_sweep(c["id"], "real board found", by)
    return {"ok": True, "outcome": "page", "titles": titles,
            "scrub_swept": swept is None,
            "message": f"stored as a careers page ({len(titles)} titles read)"}


def act_scrub(body: dict) -> dict:
    """"Submit to workflow": put this company on the manual scrub list."""
    cid = (body.get("id") or "").strip()
    companies = admin.read_companies()
    c = _find(companies, cid)
    if not c:
        return {"error": "company not found"}
    rows = admin.read(SCRUB, {})
    row = dict(rows.get(cid) or {})
    row.update({"sent_on": _today(), "sent_by": _by(body), "from": "page belt"})
    rows[cid] = row
    bad = admin.save_decisions(SCRUB, rows, "page-scrub",
                               why=f"{c['name']} sent to the scrub list", by=_by(body))
    return {"error": bad} if bad else {
        "ok": True, "message": f"{c['name']} is on the scrub list"}


def act_description(body: dict) -> dict:
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()
    if not text:
        return {"error": "a description cannot be empty"}
    c["description"] = text[:400]
    bad = _save(companies, "page-description", f"description for {c['name']}", body)
    return bad or {"ok": True, "message": "description saved"}


def act_writeup(body: dict) -> dict:
    """The owner's own write-up, published as he wrote it (2026-09-28).

    He is the authority on his board and does not want the quotes and source
    links; the agent's sourced door still guards what agents write. Marked
    hand_written, which the landing door respects: a pending agent write-up
    for this company is rejected here, and promote_profiles never lands over
    a hand-written one.
    """
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    raw = body.get("paragraphs")
    if isinstance(raw, str):
        raw = re.split(r"\n\s*\n", raw)
    paras = [re.sub(r"\s+", " ", str(p)).strip() for p in (raw or [])]
    paras = [p for p in paras if p]
    by = _by(body)
    if not paras:
        return {"error": "write at least one paragraph (use Hide to take a "
                         "write-up off the page)"}
    # THE AGENT'S PENDING DRAFT IS TURNED DOWN FIRST, or a later --land-all
    # would put it over his words. Done before the write, so a refusal here
    # leaves nothing half-done.
    import proposal_rulings
    # read through admin, like every other file here: agents.load() reads a
    # path fixed at import, which a sandboxed test cannot move
    store = admin.read("agent_proposals.json", {})
    for k, v in list(store.items()):
        if (isinstance(v, dict) and v.get("id") == c["id"]
                and v.get("kind") == "profile" and v.get("status") == "pending"):
            res = proposal_rulings.rule(store, k, False,
                                        why="the owner wrote this write-up by hand",
                                        by=by)
            if res.get("error"):
                return res
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    today = _today()
    c["profile"] = {"paragraphs": paras, "quote": None, "provenance": [],
                    "sources": [], "written_on": today, "by": by,
                    "ruled_by": by, "ruled_on": today, "hand_written": True}
    c.pop("profile_hidden", None)
    bad = _save(companies, "page-writeup", f"{c['name']}: write-up by {by}", body)
    return bad or {"ok": True, "message": f"write-up saved ({len(paras)} paragraph(s))"}


def act_competitors(body: dict) -> dict:
    """Pick the shortlist by hand, or say there are none."""
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    ids = [str(i) for i in (body.get("rivals") or []) if str(i).strip()]
    known = {x["id"] for x in companies}
    bad_ids = [i for i in ids if i not in known or i == c["id"]]
    if bad_ids:
        return {"error": f"not companies on the map: {', '.join(bad_ids)}"}
    today = _today()
    if body.get("none_found"):
        c["competitors"] = []
        c["competitors_none_found"] = True
    else:
        if not ids:
            return {"error": "pick at least one competitor, or say none found"}
        c["competitors"] = [{"id": i, "why": "chosen by the owner"}
                            for i in dict.fromkeys(ids)]
        c.pop("competitors_none_found", None)
    c["competitors_checked_on"] = today
    bad = _save(companies, "page-competitors", f"{c['name']}: competitors", body)
    return bad or {"ok": True, "message": ("none found, recorded"
                                           if body.get("none_found")
                                           else f"{len(ids)} competitor(s) saved")}


NEWS_JOBS: dict = {}          # cid -> {"started", "done", "message"|"error"}
_NEWS_LOCK = __import__("threading").Lock()


def _news_worker(cid: str) -> None:
    py = sys.executable
    res = {"message": None, "error": None}
    try:
        # a FULL visit, not the --news watch: the watch only re-reads
        # newsrooms already on file, and a company never checked has none
        for cmd in ([py, "scripts/fetch_profiles.py", "--refetch",
                     "--id", cid, "--write"],
                    [py, "scripts/news.py", "--write", "--id", cid]):
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=150)
            if r.returncode != 0:
                res["error"] = f"{cmd[1]} failed: {(r.stderr or r.stdout)[-300:]}"
                break
        else:
            n = admin.read("news.json", {}).get(cid) or {}
            res["message"] = (f"{len(n.get('items') or [])} news item(s)"
                              if n.get("items") else
                              f"news: {n.get('state') or 'nothing found'}")
    except Exception as exc:                            # noqa: BLE001
        res["error"] = f"the news check stopped: {type(exc).__name__}"
    with _NEWS_LOCK:
        NEWS_JOBS[cid] = dict(NEWS_JOBS.get(cid) or {}, done=admin.now(), **res)


def news_job(cid: str) -> dict | None:
    with _NEWS_LOCK:
        j = NEWS_JOBS.get(cid)
        return dict(j) if j else None


def act_news_check(body: dict) -> dict:
    """Re-read this one company's newsroom now, through the same two steps
    the news sweep runs.

    IN THE BACKGROUND. The admin is a single-threaded server, and two
    network steps inline froze every other request for minutes. The check
    runs in a thread; the card reports "checking" and then the result
    (page-card carries news_job). The write is a CRAWL write to news.json,
    exactly what news.yml does four times a day - not a ruling, so it is not
    journalled.
    """
    import threading
    cid = (body.get("id") or "").strip()
    if not admin.ID_OK.match(cid or ""):
        return {"error": "which company?"}
    if not _find(admin.read_companies(), cid):
        return {"error": "company not found"}
    with _NEWS_LOCK:
        j = NEWS_JOBS.get(cid)
        if j and not j.get("done"):
            return {"ok": True, "running": True,
                    "message": "already checking their news"}
        NEWS_JOBS[cid] = {"started": admin.now(), "done": None}
    threading.Thread(target=_news_worker, args=(cid,), daemon=True).start()
    return {"ok": True, "running": True,
            "message": "checking their news - the result shows here in a minute or two"}


def act_supplier(body: dict) -> dict:
    """GovTech product (optionally re-filed into a real category), or a
    supplier - which takes the company off the map and into the supplier
    registry, where it is not a public company page."""
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    choice = body.get("choice")
    if choice == "govtech":
        sec, cat = body.get("sector") or c["sector"], body.get("category") or c["category"]
        if cat == "Suppliers & Services":
            return {"error": "pick the category the product belongs in"}
        return admin.act_place({"id": c["id"], "sector": sec, "category": cat,
                                "why": body.get("why") or "owner, page belt",
                                "was": {"sector": c["sector"], "category": c["category"]},
                                "confidence": "high", "by": _by(body)})
    if choice != "supplier":
        return {"error": "choose govtech or supplier"}
    if not body.get("confirm"):
        return {"error": "moving a company off the map needs confirm: true",
                "confirm_needed": True}
    import journal
    by = _by(body)
    rec = dict(c)
    rec["govtech"] = False
    rec["demoted"] = {"on": _today(), "by": by,
                      "why": (body.get("why") or "")[:200]}
    sup = admin.read("suppliers.json", [])
    if not isinstance(sup, list):
        return {"error": "suppliers.json is not a list; nothing was written"}
    already = any(isinstance(s, dict) and s.get("id") == c["id"] for s in sup)
    # A demoted company leaves every competitor list with it, in the same
    # write: a public page would otherwise print its bare slug as a rival
    # and the checklist would stay green on a company that is gone.
    cid = c["id"]
    touched = []
    for x in companies:
        comps = x.get("competitors") or []
        keep = [r for r in comps if not (isinstance(r, dict) and r.get("id") == cid)]
        if len(keep) != len(comps):
            x["competitors"] = keep
            touched.append(x["name"])
    companies.remove(c)
    err = admin.validate(companies)
    if err:
        return {"error": err}
    why_c = (f"{c['name']} moved off the map to the supplier registry"
             + (f"; dropped from {len(touched)} competitor list(s)" if touched else ""))
    # BOTH HALVES ARE CHECKED BEFORE EITHER IS WRITTEN. suppliers.json used
    # to be written first, so a refusal on the companies half left a
    # supplier record for a company still on the map, and every retry then
    # failed on "already exists". A supplier record already there (from such
    # a half-done try) is reused, not refused.
    if not already:
        sup.append(rec)
        _, refusal = journal.check("suppliers.json", admin.read("suppliers.json", []),
                                   sup, force=False)
        if refusal:
            return {"error": refusal}
    _, refusal = journal.check("companies.json", admin.read_companies(), companies,
                               force=False)
    if refusal:
        return {"error": refusal}
    if not already:
        bad = admin.save_decisions("suppliers.json", sup, "page-demote",
                                   why=f"{c['name']} is a supplier, not govtech",
                                   by=by)
        if bad:
            return {"error": bad}
    bad = _save(companies, "page-demote", why_c, body)
    return bad or {"ok": True, "removed": True,
                   "message": f"{c['name']} moved to the supplier registry"
                              + (f"; taken off {len(touched)} competitor list(s)"
                                 if touched else "")}


def act_buyer(body: dict) -> dict:
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    mix = body.get("mix")
    if mix not in BUYER_MIX:
        return {"error": "choose government only, government and businesses, "
                         "or businesses only"}
    c["sells_to_gov"] = BUYER_MIX[mix][0]
    c["buyer_mix"] = mix
    text = re.sub(r"\s+", " ", str(body.get("buyer") or "")).strip()
    if text:
        c["buyer"] = text[:300]
    c["buyer_source"] = f"owner ({_by(body)})"
    c["buyer_checked_on"] = _today()
    bad = _save(companies, "page-buyer", f"{c['name']}: {BUYER_MIX[mix][1]}", body)
    return bad or {"ok": True, "message": BUYER_MIX[mix][1]}


def act_founded(body: dict) -> dict:
    """A year he read somewhere, or 'not stated anywhere' - an answered blank.

    'Not stated anywhere' with a year on file means the year is wrong: it is
    CLEARED through a journalled write first, then the blank is recorded as
    answered. Recording only the dismissal left the year on the page and the
    item red - 166 companies carry an unconfirmed machine year."""
    if body.get("unknown"):
        companies = admin.read_companies()
        c = _find(companies, body.get("id"))
        if not c:
            return {"error": "company not found"}
        was = c.get("year_founded")
        if was:
            c["year_founded"] = None
            bad = _save(companies, "clear-founded",
                        f"{c['name']}: founding year {was} removed, not stated "
                        f"anywhere", body)
            if bad:
                return bad
        r = admin.act_dismiss({"queue": "founded", "key": c["id"],
                               "why": "not stated anywhere (page belt)",
                               "by": _by(body)})
        if r.get("error"):
            return r
        return {"ok": True, "message": "recorded: no founding year stated anywhere"
                                       + (f"; {was} removed from the page" if was else "")}
    r = admin.act_set_founded({"id": body.get("id"), "year": body.get("year"),
                               "by": _by(body)})
    if r.get("error"):
        return r
    src = re.sub(r"\s+", " ", str(body.get("source") or "")).strip()
    if src:
        companies = admin.read_companies()
        c = _find(companies, body.get("id"))
        c["founded_source"] = src[:300]
        bad = _save(companies, "page-founded", f"{c['name']}: founded source", body)
        if bad:
            return bad
    return r


def act_ownership(body: dict) -> dict:
    """Part of another company, or independent. A parent is named as a
    company on the map when it is one, so the page can link to it."""
    companies = admin.read_companies()
    c = _find(companies, body.get("id"))
    if not c:
        return {"error": "company not found"}
    today = _today()
    if body.get("independent"):
        c["parent"] = None
        c.pop("parent", None)
        c["acquisitions_none_found"] = True
        c["acquisitions_checked_on"] = today
        why = f"{c['name']} is independent"
    else:
        parent = re.sub(r"\s+", " ", str(body.get("parent") or "")).strip()
        if not parent:
            return {"error": "name the parent company, or mark it independent"}
        p = _find(companies, parent) or next(
            (x for x in companies if (x.get("name") or "").casefold()
             == parent.casefold()), None)
        if p is c:
            return {"error": "a company cannot be its own parent"}
        c["parent"] = p["name"] if p else parent
        c.pop("acquisitions_none_found", None)
        c["acquisitions_checked_on"] = today
        why = f"{c['name']} is part of {c['parent']}"
    # WHO answered: a person's answer settles the research claim and the
    # "their site talks about another company" hint on the checklist
    c["ownership_by"] = _by(body)
    bad = _save(companies, "page-ownership", why, body)
    if bad:
        return bad
    # the research row in Acquisitions asked exactly this question; a
    # person answered it, so it leaves that queue with the answer as its why
    if any(r.get("id") == c["id"] and r.get("strength") == "research"
           for r in admin.q_acquisitions(companies, admin.read("board.json", {}))):
        admin.act_dismiss({"queue": "acquisitions", "key": c["id"],
                           "why": f"answered on the page belt: {why}",
                           "by": _by(body)})
    return {"ok": True, "message": why}


def act_explain(body: dict) -> dict:
    """Words for Claude, about one checklist item.

    PRIVATE BY CONSTRUCTION. The repo is public: logic_notes.json and the
    journal are tracked, and save_decisions copies before/after images and
    the why into the journal. So this appends to a gitignored inbox with a
    plain write, never through the journal. The end-of-day digest reads it.
    """
    cid = (body.get("id") or "").strip()
    item = body.get("item")
    text = str(body.get("text") or "").strip()
    if item not in ITEMS and item not in ("title", "page"):
        return {"error": "which checklist item is this about?"}
    if not text:
        return {"error": "say what is wrong or what the system should know"}
    companies = admin.read_companies()
    c = _find(companies, cid)
    if not c:
        return {"error": "company not found"}
    # WHAT THE SYSTEM SHOWED when he wrote it - the input beside the answer,
    # or the end-of-day workshop is guessing what he was looking at
    saw = None
    if item in ITEMS:
        try:
            ctx = context(companies, admin.read("board.json", {}))
            it = next(i for i in checklist(c, ctx) if i["key"] == item)
            saw = {"green": it["green"], "state": it["state"], "detail": it["detail"]}
        except Exception:                               # noqa: BLE001
            saw = None
    row = {"id": f"{cid}:{item}:{admin.now()}", "at": admin.now(),
           "source": "owner-explain", "company_id": cid,
           "name": c.get("name"), "item": item, "saw": saw, "text": text[:4000],
           "by": _by(body), "status": "open"}
    path = inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    return {"ok": True, "message": "sent to Claude's inbox for the end-of-day review"}


def act_submit(body: dict) -> dict:
    """Sign the page off. Red items are allowed and recorded - server-side,
    never taken from the client - and keep showing in their queues."""
    import page_reviews
    cid = (body.get("id") or "").strip()
    companies = admin.read_companies()
    c = _find(companies, cid)
    if not c:
        return {"error": "company not found"}
    board = admin.read("board.json", {})
    ctx = context(companies, board)
    items = checklist(c, ctx)
    by = _by(body)
    if not by or "@" in by:
        return {"error": "a sign-off names its author as a handle"}
    rows = page_reviews.load()
    red = [i["key"] for i in items if not i["green"]]
    rows[cid] = {"by": by, "on": _today(), "at": admin.now(),
                 "note": (body.get("note") or "").strip()[:400],
                 "gaps_then": sorted(red),
                 "checklist": {i["key"]: bool(i["green"]) for i in items}}
    bad = page_reviews.save(rows, "page-review",
                            why=f"{cid} approved on the page belt"
                                + (f", {len(red)} open" if red else ", nothing open"),
                            by=by)
    if bad:
        return {"error": bad}
    return {"ok": True, "red": red, "counts": _counts(companies, c),
            "message": f"{c['name']} approved" + (
                f"; {len(red)} item(s) stay open under Open on approved pages"
                if red else "")}


def _counts(companies: list, c: dict) -> dict:
    """The belt's own numbers for this company's sector and subsector, from
    page_reviews.json, so the screen never counts by adding one."""
    reviews = admin.read("page_reviews.json", {})
    sec = [x for x in companies if x.get("sector") == c.get("sector")]
    cat = [x for x in sec if x.get("category") == c.get("category")]
    return {"sector": c.get("sector"), "category": c.get("category"),
            "sector_total": len(sec),
            "sector_signed": sum(1 for x in sec if x["id"] in reviews),
            "category_total": len(cat),
            "category_signed": sum(1 for x in cat if x["id"] in reviews)}


def q_signedoff(companies, board) -> list:
    """Open items on approved pages: every red item on a signed-off page, and
    whether it was already red when he approved it or was LOST since. This is
    where a signed-off page's problems show up - never back on the belt."""
    reviews = admin.read("page_reviews.json", {})
    if not reviews:
        return []
    ctx = context(companies, board)
    out = []
    for c in companies:
        rev = reviews.get(c["id"])
        if not rev:
            continue
        then = rev.get("checklist") or {}
        for it in checklist(c, ctx):
            if it["green"]:
                continue
            out.append({"id": c["id"], "name": c.get("name"),
                        "sector": c.get("sector"), "category": c.get("category"),
                        "item": it["key"], "label": it["label"],
                        "state": it["state"], "detail": it["detail"],
                        # a sweep coming due is the cadence, not a loss
                        "lost": bool(then.get(it["key"])) and not it.get("cadence"),
                        "sweep_due": bool(it.get("cadence")),
                        "approved_on": rev.get("on")})
    out.sort(key=lambda r: (not r["lost"], r["item"], (r["name"] or "").casefold()))
    return out


ACTIONS = {"page-title": act_title, "page-website": act_website,
           "page-board": act_board, "page-scrub": act_scrub,
           "page-description": act_description, "page-writeup": act_writeup,
           "page-competitors": act_competitors, "page-news-check": act_news_check,
           "page-supplier": act_supplier, "page-buyer": act_buyer,
           "page-founded": act_founded, "page-ownership": act_ownership,
           "page-explain": act_explain, "page-submit": act_submit}


# ------------------------------------------------------------- live preview

RECORD_FIELDS = ("name", "sector", "category", "location", "year_founded",
                 "description", "website", "linkedin", "vendor_type",
                 "govtech", "parent", "ats_note", "acquired", "also_known_as",
                 "competitors", "competitors_none_found",
                 "competitors_checked_on", "posts_at", "researched",
                 "sled_only", "board_owner")


def preview_org(companies: list, board: dict, c: dict) -> dict:
    """The board's organization for this company, redrawn from the record AS
    IT IS NOW. The crawl-owned fields (open roles, readability) stay as the
    last crawl left them - a new board produces roles only after a crawl -
    and everything a person edits on the belt is taken from companies.json,
    so the preview shows the edit made a second ago.

    profile and news are always set, never None: build_site.with_detail
    refills a None from a cache that lives as long as the admin process, so
    a write-up he just hid would otherwise keep showing.
    """
    import build_board as bb
    import tags
    base = next((o for o in board.get("organizations", [])
                 if o.get("id") == c["id"]), None)
    o = dict(base) if base else {"id": c["id"], "open_roles": 0,
                                 "open_postings": 0, "quota_roles": 0,
                                 "quota_postings": 0, "families": {},
                                 "no_board_on_file": True}
    for k in RECORD_FIELDS:
        o[k] = c.get(k)
    o["also"] = c.get("also") or None
    o["board_url"] = bb.board_url(c)
    o["tags"] = tags.tags_for(c, tags.vocabulary())
    o["brands"] = bb._brands_with_history(c, companies)
    o["profile"] = bb.profile_for_board(c) or {}
    items, state, on = bb.news_for_board(c, bb._news_store())
    o["news"] = items or []
    o["news_state"], o["news_checked_on"] = state, on
    return o


def preview_html(companies: list, board: dict, cid: str) -> str | None:
    import build_site as bs
    c = _find(companies, cid)
    if c is None:
        return None
    o = preview_org(companies, board, c)
    bs._DETAIL_CACHE.pop(cid, None)
    orgs = [x for x in board.get("organizations", []) if x.get("id") != cid] + [o]
    by_id = {x["id"]: x for x in orgs if x.get("id")}
    by_name = {str(x["name"]).strip().lower(): x for x in orgs if x.get("name")}
    in_cat = sum(1 for x in orgs if (x.get("sector"), x.get("category"))
                 == (o.get("sector"), o.get("category")))
    mine = [p for p in board.get("postings", []) if p.get("company_id") == cid]
    return bs.company_page_html(o, mine, board, admin.read("brand.json", {}),
                                by_id, by_name, in_cat)


# ------------------------------------------------------------- the scrub tab

def q_scrub(companies, board) -> list:
    """The manual scrub list: every board only a person can read, with when a
    person last swept it (owner, 2026-09-28: "what I really need to know is
    the last time I swept it manually so I can keep up a cadence so we don't
    miss jobs").

    Who is on it: careers pages on file that read nothing, companies whose
    openings sit on a page a person can open (posts_at that reaches a
    capture), and anything sent from the page belt with "Submit to workflow".
    A company on a structured board is read every night and is never here.

    Order: sent from the belt and due first, then never swept, then the
    longest since a sweep; a company swept inside the cadence sits at the
    bottom with the day it comes due, so the tab shows the whole standing
    job and not only today's slice.
    """
    import posts_at as _pa
    ctx = context(companies, board)
    sent = ctx["scrub"] or {}
    out = []
    for c in companies:
        if not on_scrub(c, ctx):
            continue
        kind = (c.get("ats") or {}).get("type")
        pa = c.get("posts_at")
        on_page = kind == "html" and not board_verdict(c, ctx["orgs"].get(c["id"]))[0]
        reach = _pa.reach(pa) if pa else None
        cad = cadence(c["id"], ctx)
        swept, age, due = cad["last_swept"], cad["days_since"], cad["due"]
        url = (pa.get("url") if isinstance(pa, dict) and reach == "capture"
               else None) or ((c.get("ats") or {}).get("ref")
                              if kind == "html" else None) or c.get("website")
        s = sent.get(c["id"]) or {}
        out.append({
            "id": c["id"], "name": c.get("name"), "sector": c.get("sector"),
            "category": c.get("category"), "website": c.get("website"),
            "url": url,
            "where": _pa.label(pa.get("where")) if isinstance(pa, dict) else None,
            "why": (board_verdict(c, ctx["orgs"].get(c["id"]))[1]
                    if on_page else (f"posts on {_pa.label(pa.get('where'))}"
                                     if isinstance(pa, dict) else "sent from the page belt")),
            "last_swept": swept, "days_since": age, "due": due,
            "due_on": cad["due_on"],
            "swept_how": (sent.get(c["id"]) or {}).get("swept_how"),
            "sent_on": s.get("sent_on"),
            "open_roles": (ctx["orgs"].get(c["id"]) or {}).get("open_roles", 0) or 0,
        })
    out.sort(key=lambda r: (not r["due"], not r["sent_on"],
                            r["last_swept"] is not None, r["last_swept"] or "",
                            (r["name"] or "").casefold()))
    return out
