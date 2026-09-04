#!/usr/bin/env python3
"""The one door every agent proposal passes to become a fact on the map.

    python3 scripts/proposal_rulings.py                          # what waits
    python3 scripts/proposal_rulings.py --show read:aladtec
    python3 scripts/proposal_rulings.py --accept read:aladtec --by owner
    python3 scripts/proposal_rulings.py --reject board:x --why "parent's" --by owner

THE OTHER END OF THE SPINE. agents.py has written proposals into
data/agent_proposals.json since August, and the admin grew a queue tab for
them with no renderer behind it and no action to accept one - 131 rows a
person could count and never rule. promote_rivals.py was written as a CLI
for one kind because there was nowhere else to put it. This is the applier
for EVERY kind, in one place, used by the admin action, the web admin's
apply step, and the CLI alike, so the rules hold identically whoever is at
the keyboard.

ACCEPTING IS DISPATCHED BY KIND, AND EACH KIND KEEPS ITS OWN GATE. A read
proposal lands through act_capture, which dedupes on the board's key and
names the titles that do not read like jobs. A board proposal is verified
with a real fetch and asked whose it is, and a MISMATCH refuses without
force - the two failures already met here are a subsidiary pointing at its
parent's Workday and an operating entity under another name. A bucket
proposal lands through act_place, which refuses a low-confidence placement
taken in silence. A rival proposal lands through promote_rivals, cap and
all. Nothing here reimplements a gate that exists; a gate reimplemented in
two places is two gates that drift.

A KIND WITH NO APPLIER SAYS SO. profile, news and claim are declared in
agents.KINDS ahead of their appliers so the queue can show them; accepting
one returns an explicit refusal naming the kind. It does not pretend to
land it, and it does not raise KeyError inside a request handler.

REJECTING DELETES NOTHING. The row is marked rejected with who, when and
why, and the company stays proposable. A ruling is training data, and a
reject that vanished would teach the next run nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402
import roles                                                    # noqa: E402

# Kinds this door cannot land, and WHY each one, because "no applier yet" is
# two different facts. `profile` has an applier - promote_profiles.py - but it
# lands a whole category at once behind a gate review, which is the owner's
# ruling on how 2,000 write-ups reach public pages; ruling one here would walk
# past the gate. `news` is a parser with no proposals at all. `claim` and
# `card` are genuinely unbuilt.
# A PROFILE HAS TWO DOORS AND ONE APPLIER. A category lands in one batch
# through promote_profiles.py --land after --gate has printed its exceptions;
# a single write-up lands here, from the admin's Write-ups tab, where the
# person ruling it has that write-up open sentence by sentence - which is
# the gate review for one row. Both paths call promote_profiles.land, so the
# written shape, the journal entry and the author are identical.
ELSEWHERE = {"profile": "one at a time here, or a category at a time: promote_profiles.py --gate <category>"}
NO_APPLIER = ("news", "claim")


def _stamp(p: dict, status: str, by: str, why: str) -> None:
    p["status"] = status
    p["ruled_by"] = by
    p["ruled_on"] = dt.date.today().isoformat()
    p["ruled_why"] = (why or "").strip() or None


def _save_store(store: dict, action: str, why: str, by: str) -> str | None:
    """The proposal store goes through the journal like every decision file."""
    return admin.save_decisions("agent_proposals.json", store, action,
                                why=why or "", by=by)


def _accept_read(p: dict, by: str, why: str, force: bool) -> dict:
    companies = admin.read_companies()
    c = next((x for x in companies if x["id"] == p.get("id")), None)
    warn = admin.proposal_warn(p, c)
    if warn and not force:
        return {"error": f"{warn}. Pass force to accept anyway."}
    if not p.get("postings"):
        # A "read produced nothing" is a RECORD, not a task: accepting it
        # writes no posting and only closes the row.
        return {"ok": True, "message": "recorded: the read found nothing; "
                                       "no posting written"}
    return admin.act_capture({"company_id": p["id"], "jobs": p["postings"],
                              "page_url": p.get("evidence") or "", "by": by})


def _accept_board(p: dict, by: str, why: str, force: bool) -> dict:
    import add_company, verify_boards
    cid = p.get("id")
    kind, ref = (p.get("ats_type") or "").strip(), (p.get("ats_ref") or "").strip()
    if not kind or not ref:
        return {"error": "this board proposal names no ats type or ref"}
    companies = admin.read_companies()
    c = next((x for x in companies if x["id"] == cid), None)
    if c is None:
        return {"error": "no such company"}
    block = {"type": kind, "ref": ref}
    ok, detail = add_company.verify(block)
    if not ok:
        return {"error": f"the board does not read right now: {detail}. "
                         f"Nothing was written; a slug is never wired on a "
                         f"proposal alone"}
    said = verify_boards.board_says(kind, ref)
    who = verify_boards.judge(c, said)
    if who["verdict"] == "MISMATCH" and not force:
        return {"error": f"{who['why']}. If they were acquired, record the "
                         f"parent first - otherwise this reports somebody "
                         f"else's requisitions as {c['name']}'s"}
    c["ats"] = block
    if said.get("name") and said["name"].lower() != c["name"].lower():
        c["board_owner"] = said["name"]
    err = admin.validate(companies)
    if err:
        return {"error": err}
    bad = admin.save_companies(companies, "proposal-board",
                               why or f"accepted the {kind} board an agent found "
                                      f"for {c['name']}", by=by)
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{c['name']} now reads from {kind}: {detail}"}


def _accept_bucket(p: dict, by: str, why: str, force: bool) -> dict:
    saw = p.get("saw") or {}
    return admin.act_place({"id": p["id"], "sector": p.get("sector"),
                            "category": p.get("category"), "why": why,
                            "was": saw.get("filed_now"),
                            "proposed": f"{p.get('sector')} / {p.get('category')}",
                            "confidence": p.get("confidence"),
                            "description": saw.get("description"), "by": by})


FACT_PROVENANCE = "fact_provenance.json"


def _accept_fact(p: dict, by: str, why: str, force: bool) -> dict:
    """Write one founding year or one location, with the sentence that states it.

    THE PROVENANCE IS NOT OPTIONAL AND IT DOES NOT LIVE ON THE COMPANY. A year
    is a published fact about somebody else's firm, and CLAUDE.md's rule is
    that we never invent one - so the quote that justified it has to survive
    the write, or six months from now nobody can tell a researched year from a
    remembered one. `founded_provenance.json` is the older, hand-ruled version
    of the same idea; this file is keyed `<id>:<field>` so a location gets the
    same treatment, and it goes through save_decisions like every other
    decision file rather than the bare write_atomic that one still uses.
    """
    field, value = p.get("field"), p.get("value")
    if field not in agents.FACT_FIELDS:
        return {"error": f"unknown field {field!r}"}
    quote = p.get("quote") or {}
    text = quote.get("text") if isinstance(quote, dict) else quote
    url = (quote.get("url") if isinstance(quote, dict) else None) or p.get("url")
    if not text or not url:
        return {"error": "the quote and its url did not survive intake; "
                         "nothing may be written without them"}
    companies = admin.read_companies()
    c = next((x for x in companies if x.get("id") == p.get("id")), None)
    if c is None:
        return {"error": f"no company {p.get('id')!r}"}
    # ALREADY ON FILE IS NOT A CORRECTION. A proposal made weeks ago must not
    # overwrite an answer a person has given since.
    if c.get(field):
        return {"error": f"{c.get('name')} already reads {field}="
                         f"{c[field]!r}; change it in the admin, not here"}
    c[field] = int(value) if field == "year_founded" else str(value).strip()
    bad = admin.save_companies(companies, f"set-{field}",
                               why=(why or p.get("why") or "")[:300], by=by)
    if bad:
        return {"error": bad}
    prov = admin.read(FACT_PROVENANCE, {})
    prov[f"{p['id']}:{field}"] = {
        "value": c[field], "url": url, "quote": text[:400],
        "on": dt.date.today().isoformat(), "by": by}
    bad = admin.save_decisions(FACT_PROVENANCE, prov, f"{field}-provenance",
                               why=f"{p['id']} {field}", by=by)
    if bad:
        # THE COMPANY WRITE ALREADY LANDED. Say so rather than reporting a
        # clean failure - admin_undo can take it back and the caller has to
        # know there is something to take back.
        return {"error": f"{field} was written but its provenance was refused: "
                         f"{bad}. Undo the {field} write if that matters."}
    return {"ok": True, "message": f"{c.get('name')} {field} = {c[field]}"}


def _accept_where(p: dict, by: str, why: str, force: bool) -> dict:
    """Record that a company advertises somewhere we cannot count.

    Deliberately NOT an `ats` entry: `ats` means monitored, and filing
    LinkedIn there would make refresh try, fail, and write a zero that reads
    as "nobody is hiring here". This says the opposite and honest thing.
    """
    import posts_at as _pa
    where, url = p.get("where"), (p.get("url") or "").strip()
    owner = (p.get("board_owner") or "").strip()
    bad = _pa.check(where, url, owner)
    if bad:
        return {"error": bad}
    companies = admin.read_companies()
    c = next((x for x in companies if x.get("id") == p.get("id")), None)
    if c is None:
        return {"error": f"no company {p.get('id')!r}"}
    if c.get("posts_at"):
        return {"error": f"{c.get('name')} already records where they post"}
    c["posts_at"] = _pa.build(where, url, by, (why or p.get("why") or "")[:200],
                              owner)
    bad = admin.save_companies(companies, "set-posts-at",
                               why=(why or p.get("why") or "")[:300], by=by)
    return ({"error": bad} if bad else
            {"ok": True, "message": f"{c.get('name')} posts at {_pa.label(where)}"})


def _accept_card(p: dict, by: str, why: str, force: bool) -> dict:
    """Let a researched candidate onto the board, or file it as a supplier.

    THE INSERT IS promote_candidates', NOT A SECOND COPY OF IT. That script
    holds three guards this must not lose: an id that collides gets a suffix,
    a name sharing a distinctive word with a company already on file is a
    possible duplicate and goes to a person rather than becoming a card, and
    `source_event` is only written as "exhibited at X" when the catalogue
    actually issued that tag - a research pass called itself an event once and
    put ten companies on the Conferences tab claiming they attended it.

    `not_this_board` writes NOTHING, and that is the whole answer. The
    proposal is stamped, the brief will not re-ask, and no record is created
    for a company this board does not carry - which is also what makes the
    ruling reversible: there is nothing to take back.
    """
    import promote_candidates as pc

    verdict = p.get("verdict")
    name = (p.get("name") or "").strip()
    if verdict not in agents.CARD_VERDICTS:
        return {"error": f"unknown verdict {verdict!r}"}
    if verdict == "not_this_board":
        return {"ok": True, "message": f"{name}: not this board. Nothing was "
                                       f"written, and it will not be re-asked"}
    companies = admin.read_companies()
    suppliers = json.loads((admin.DATA / "suppliers.json").read_text())
    key = pc.norm(name)
    known = {pc.norm(c["name"]): c["name"] for c in companies}
    known.update({pc.norm(s["name"]): s["name"] for s in suppliers})
    match = pc.same_company(key, known)
    if match:
        return {"error": f"{name} is already on file as {match!r}"}

    if verdict == "supplier":
        suppliers.append({
            "id": pc.kebab(name), "name": name, "website": p.get("website"),
            "location": None, "year_founded": None,
            "sector": "General Gov", "category": "Suppliers & Services",
            "description": (p.get("description") or "").strip(),
            "ats": {"type": "unknown", "ref": None},
            "hiring": {"status": "Unknown", "note": "not researched",
                       "roles": [], "checked": None}})
        bad = admin.save_decisions("suppliers.json", suppliers, "add-supplier",
                                   why=(why or p.get("why") or "")[:300], by=by)
        return ({"error": bad} if bad else
                {"ok": True, "message": f"{name} filed as a supplier"})

    sector, cat = p.get("sector"), p.get("category")
    schema = agents._schema()
    if sector not in schema or cat not in schema.get(sector, []):
        return {"error": f"sector/category not in schema: {sector} / {cat}"}
    desc = (p.get("description") or "").strip().rstrip(".")
    if not desc:
        return {"error": "a govtech card needs a one-line description"}
    by_word: dict = {}
    for real in list(known.values()):
        for w in pc._words(real):
            by_word.setdefault(w, real)
    near = pc.shares_a_name(name, by_word)
    if near and not force:
        return {"error": f"{name} shares a distinctive word with {near!r}. "
                         f"That is a merge or two companies, and only a person "
                         f"can say which - rule it again with force to insist"}
    taken = {c["id"] for c in companies} | {s["id"] for s in suppliers}
    cid, n = pc.kebab(name), 2
    while cid in taken:
        cid = f"{pc.kebab(name)}-{n}"
        n += 1
    saw = p.get("saw") or {}
    ev = saw.get("source_event")
    event = ev if ev in pc._issued_event_tags() else None
    companies.append({
        "id": cid, "name": name, "website": p.get("website"),
        "location": None, "year_founded": None,
        "sector": sector, "category": cat,
        "description": f"{desc} - exhibited at {event}" if event else desc,
        "ats": {"type": "unknown", "ref": None},
        "hiring": {"status": "Unknown", "note": "board not discovered yet",
                   "roles": [], "checked": None},
        "govtech": True, "vendor_type": "GovTech Product",
        "source": (f"conference sweep: {ev}" if event
                   else (f"research pass: {ev}" if ev else "agent card")),
        "researched": True})
    bad = admin.save_companies(companies, "add-company",
                               why=(why or p.get("why") or "")[:300], by=by)
    return ({"error": bad} if bad else
            {"ok": True, "message": f"{name} added as {cid} "
                                    f"({sector} / {cat})"})


def _accept_family(p: dict, by: str, why: str, force: bool) -> dict:
    """File one title under one family, in family_overrides.json.

    NOT THROUGH admin.act_set_family, which writes the file with a bare
    write_atomic and journals nothing. CLAUDE.md heads a section "Every admin
    write is reversible" and that call is one of six that make it false; a
    ruling arriving from a nightly model run is the last write that should be
    the unrecoverable one, so this goes through save_decisions like every
    other decision file. The owner's own click still takes the old path -
    fixing that is P4.1 and belongs with the other five, not smuggled in here.

    THE TITLE IS RE-CHECKED AGAINST THE QUEUE at the moment of the write. The
    door checked it at ingest, which can be weeks earlier: a rule added to
    roles.py in between would place the title correctly, and this override
    would silently beat it. That is the one failure this file cannot see
    afterwards.
    """
    title = p.get("title") or p.get("id")
    fam = p.get("family")
    if fam not in roles.LABEL or fam == "other":
        return {"error": f"unknown family {fam!r}"}
    if title not in agents.unclassified_titles():
        return {"error": f"{title!r} is no longer unclassified - a rule places "
                         f"it now, and an override would beat that rule"}
    over = admin.read("family_overrides.json", {})
    if title in over:
        return {"error": f"{title!r} already reads "
                         f"{over[title].get('family')}; change it in the admin"}
    over[title] = {"family": fam, "on": dt.date.today().isoformat(), "by": by}
    bad = admin.save_decisions("family_overrides.json", over, "set-family",
                               why=(why or p.get("why") or "")[:300], by=by)
    if bad:
        return {"error": bad}
    return {"ok": True, "message": f"{title} -> {roles.LABEL[fam]}"}


def _retract(p: dict, by: str, why: str) -> str:
    """Take a rejected write-up off the public file, if it is on it.

    Returns "nothing" when there was nothing published, "pulled" when it
    came down, and a message starting REFUSED when the journal said no.
    """
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    c = next((x for x in seq if x.get("id") == p.get("id")), None)
    prof = (c or {}).get("profile")
    if not isinstance(prof, dict) or not prof.get("paragraphs"):
        return "nothing"
    c.pop("profile", None)
    c.pop("profile_hidden", None)
    bad = admin.save_companies(companies, "profile-retract",
                               why=f"rejected: {why}"[:300], by=by)
    return f"REFUSED by the journal: {bad}" if bad else "pulled"


def _accept_profile(p: dict, by: str, why: str, force: bool, store: dict,
                    key: str) -> dict:
    """Land ONE write-up onto its company through promote_profiles.land, the
    same function that lands a category, so a single ruling and a batch
    write the same record with the same provenance. land() refuses anything
    not pending, which is what keeps a door-refused write-up off the page
    even from here: the door's word stands, a person can only dismiss it."""
    import promote_profiles
    if not promote_profiles._has_text(p):
        return {"error": "nothing to land: this write-up carries no paragraphs. "
                         "An unsure answer is a valid answer and stays pending"}
    companies = admin.read_companies()
    n = promote_profiles.land(store, companies, [key], by,
                              why or f"landed {p.get('id')} from the write-ups tab")
    if not n:
        return {"error": "the write-up could not be landed; see the journal"}
    return {"ok": True, "message": f"write-up on the page for {p.get('name') or p.get('id')}"}


def _accept_rival(p: dict, by: str, why: str, force: bool, store: dict) -> dict:
    import promote_rivals
    n = promote_rivals.write_accepted(store, [p["id"]], by, why)
    if not n:
        return {"error": "the shortlist could not be written; see the journal"}
    return {"ok": True, "message": f"shortlist written for {p.get('name') or p['id']}"}


def rule(store: dict, key: str, accept: bool, why: str = "", by: str = "",
         force: bool = False) -> dict:
    """Accept or reject one proposal. Returns {ok, message} or {error}."""
    if not by:
        return {"error": "a ruling needs an author: owner, or agent:<label>"}
    p = store.get(key)
    if not isinstance(p, dict):
        return {"error": f"no proposal on file under {key!r}"}
    kind = p.get("kind")
    # A WRITE-UP ON THE PAGE CAN ALWAYS BE TAKEN BACK. Every other ruling is
    # once-only, and rightly: re-accepting a board or a read twice does work
    # twice. But a description of a real company that turns out to be wrong
    # has to come off on sight, and refusing to rule it again is refusing to
    # correct it. Five landed Police write-ups could not be retracted through
    # this door for exactly that reason - two of them describing the company
    # that had bought the one they were filed under.
    retracting = (kind == "profile" and not accept and p.get("status") == "accepted")
    if p.get("status") != "pending" and not retracting:
        return {"error": f"{key} was already ruled {p.get('status')} by "
                         f"{p.get('ruled_by')} on {p.get('ruled_on')}"}

    if not accept:
        _stamp(p, "rejected", by, why)
        bad = _save_store(store, "proposal-reject", why, by)
        if bad:
            return {"error": bad}
        # A REJECTION AFTER THE WRITE-UP HAS LANDED MUST TAKE IT OFF THE PAGE.
        # Rejecting only stamped the proposal, so five Police write-ups the
        # second reader threw out stayed on the public pages for 40 minutes -
        # two of them describing Versaterm under the name of a company it had
        # bought. The proposal store is not the public file; saying no in one
        # has to reach the other.
        # EXPLICIT OUTCOMES, not a truthy string: _retract returned "" on a
        # successful pull, which is falsy, so the branch reporting the
        # retraction never ran and the caller was told nothing came down.
        pulled = _retract(p, by, why) if kind == "profile" else "nothing"
        if pulled.startswith("REFUSED"):
            return {"error": pulled}
        if pulled == "pulled":
            return {"ok": True, "message": f"rejected {key} AND took the "
                                           f"published write-up off the page"}
        return {"ok": True, "message": f"rejected {key}; the company stays "
                                       f"proposable, nothing was deleted"}

    if kind in NO_APPLIER:
        where = ELSEWHERE.get(kind)
        return {"error": (f"a {kind} proposal is not ruled here: {where}"
                          if where else
                          f"no applier for a {kind} proposal yet. The queue can "
                          f"show it; nothing can land it. That is a refusal, "
                          f"not a landing")}
    if kind == "read":
        res = _accept_read(p, by, why, force)
    elif kind == "board":
        res = _accept_board(p, by, why, force)
    elif kind == "bucket":
        res = _accept_bucket(p, by, why, force)
    elif kind == "family":
        res = _accept_family(p, by, why, force)
    elif kind == "fact":
        res = _accept_fact(p, by, why, force)
    elif kind == "where":
        res = _accept_where(p, by, why, force)
    elif kind == "card":
        res = _accept_card(p, by, why, force)
    elif kind == "rival":
        res = _accept_rival(p, by, why, force, store)
    elif kind == "profile":
        res = _accept_profile(p, by, why, force, store, key)
    else:
        return {"error": f"unknown proposal kind {kind!r}"}
    if res.get("error"):
        return res
    # promote_rivals and promote_profiles stamp and save their own rows;
    # every other kind is stamped here, and the store is written through
    # the journal either way
    if kind not in ("rival", "profile"):
        _stamp(p, "accepted", by, why)
    bad = _save_store(store, "proposal-accept", why, by)
    if bad:
        return {"error": bad}
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show")
    ap.add_argument("--accept", action="append", default=[])
    ap.add_argument("--reject", action="append", default=[])
    ap.add_argument("--why", default="")
    ap.add_argument("--by", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    store = agents.load()

    if a.show:
        p = store.get(a.show)
        print(json.dumps(p, indent=1) if p else f"nothing under {a.show!r}")
        return 0 if p else 1

    if a.accept or a.reject:
        if not a.by:
            ap.error("--by is required to rule: \"owner\", or \"agent:<label>\"")
        for key in a.accept:
            res = rule(store, key, True, a.why, a.by, a.force)
            print(f"  {key}: {res.get('message') or 'REFUSED: ' + res.get('error', '')}")
        for key in a.reject:
            res = rule(store, key, False, a.why, a.by, a.force)
            print(f"  {key}: {res.get('message') or 'REFUSED: ' + res.get('error', '')}")
        return 0

    pending = [(k, p) for k, p in store.items()
               if isinstance(p, dict) and p.get("status") == "pending"]
    by_kind: dict[str, int] = {}
    for _, p in pending:
        by_kind[p.get("kind", "?")] = by_kind.get(p.get("kind", "?"), 0) + 1
    print(f"{len(pending)} proposal(s) waiting on a ruling")
    for k, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        tail = (f"   ({ELSEWHERE[k]})" if k in ELSEWHERE
                else "   (no applier yet)" if k in NO_APPLIER else "")
        print(f"  {n:4}  {k}{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
