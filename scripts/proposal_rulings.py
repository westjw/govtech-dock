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
import employer_log                                             # noqa: E402
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
# CLAIM LEFT THIS LIST ON 2026-09-10. It had been "genuinely unbuilt" since
# the claim endpoint shipped, which meant a company could verify its own
# domain, send a correction or post a role, be told "a person reviews every
# change before it appears" - and no person could land it however much they
# agreed. That is the 131-unreachable-rows shape one layer down, and worse,
# because the promise was made to somebody outside.
ELSEWHERE = {"profile": "one at a time here, or a category at a time: promote_profiles.py --gate <category>",
             "buyer": "one at a time here, or a category at a time: promote_profiles.py --gate-buyer <category>"}
NO_APPLIER = ("news",)

# WHO LANDS A VERIFIED CLAIMANT'S OWN EDIT. Named once and shared, because two
# places have to agree on it: sync_claims passes it as the ruling's author,
# and _log_employer_ruling reads it to decide the log says `self_served`
# rather than `accepted`. A literal in both is a literal that drifts, and the
# drift would be silent and would only show up as an accept rate that is
# quietly wrong.
SELF_SERVE_BY = "script:claim-self-serve"

# WHAT A VERIFIED CLAIMANT MAY LAND WITHOUT A PERSON. Mirrors SELF_SERVE in
# functions/api/claim.js, and check_the_two_self_serve_lists_agree holds them
# together for the reason the two applier lists are held together: a kind that
# is self-serve at the endpoint and queued here promises a claimant something
# the repo will not do.
SELF_SERVE_KINDS = ("description", "profile", "logo", "job")


def _log_employer_ruling(p: dict, key: str, accepted: bool, by: str,
                         why: str) -> None:
    """A ruling on a CLAIMANT's proposal is an event in the employer trail.

    Only `claim` proposals: the rest come from agents and belong to the map's
    own history, not to a company's relationship with this board. Recording
    them all here would make the accept rate a statement about our agents
    wearing the label of a statement about our employers.

    The store key carries the company and the moment the claimant sent it, so
    a ruling joins back to its `proposal_sent` on those two facts. Failing to
    log must never fail the ruling - the person's decision has already landed
    in a journalled file, and losing the audit line is the smaller loss of the
    two - so this reports and returns.
    """
    if p.get("kind") != "claim":
        return
    cid = p.get("id")
    if not cid:
        return
    edit = p.get("edit") or {}
    kind = edit.get("kind") or "unknown"
    # AN UNREVIEWED EDIT IS NOT AN ACCEPTED ONE. `proposal_accepted` says a
    # person read the proposal and applied it; a verified claimant's own
    # correction lands with nobody reading it, and filing that under
    # "accepted" would make every accept rate this log reports a claim about
    # how much of the map a person has seen that is simply false. The gate
    # moved to the claim, once - it did not stop existing.
    if accepted and by == SELF_SERVE_BY:
        try:
            employer_log.record_once(
                "proposal_self_served", cid, by=by, source_key=key,
                proposal_kind=kind, domain=edit.get("by_domain") or "",
                proposal_key=key)
        except Exception as e:                              # noqa: BLE001
            print(f"employer_log: could not record {key}: {e}", file=sys.stderr)
        return
    try:
        employer_log.record_once(
            "proposal_accepted" if accepted else "proposal_rejected",
            cid, by=by, source_key=key, proposal_kind=kind,
            why=why or ("accepted" if accepted else "no reason given"),
            proposal_key=key)
    except Exception as e:                                  # noqa: BLE001
        print(f"employer_log: could not record {key}: {e}", file=sys.stderr)


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


def _accept_claim(p: dict, by: str, why: str, force: bool) -> dict:
    """Land a correction the company itself sent, through the door that owns it.

    THE ONE PLACE THE COMPANY IS THE SOURCE. Every other kind here is an agent
    reading somebody else's page, so the rules are all about evidence: a fact
    needs its quote, a board needs a verifying fetch, a card needs a duplicate
    check. A claimant is the firm the record is about, mailing us from its own
    domain - which changes what counts as evidence and changes nothing about
    who decides. A person still rules, for the reason claim.js states: a
    company that could rewrite its own entry unreviewed could rewrite its
    competitors' context.

    NOTHING IS WRITTEN HERE. Each kind is handed to the door that already owns
    that write, with the gates that door holds - act_capture's junk filter and
    posting key for a job, promote_profiles' record shape for a write-up,
    notes.add's append-never-replace for a message. A gate reimplemented in two
    places is two gates that drift, and these four are all reachable.

    A DESCRIPTION IS THE ONE OVERWRITE, and it is deliberate. `_accept_fact`
    refuses a field already on file, correctly - a proposal made weeks ago must
    not overwrite an answer a person gave since. That rule cannot apply here:
    all 2,053 companies already carry a description, so refusing an occupied
    field would refuse every correction, and a correction to what we wrote is
    the entire point of claiming. The safety net is the one this repo already
    relies on: save_companies journals the before-image, so admin_undo can take
    it back, and the why names the domain it came from.

    A CATEGORY IS REFUSED BY NAME. claim.js already tells the claimant it is a
    request rather than an edit; landing it here would quietly make it an edit,
    which is the oldest trick in directory listings and the reason that rule
    exists. It goes to a person on the wrong-bucket queue like any other
    placement question.
    """
    import notes as _notes
    import promote_profiles as _pp

    edit = p.get("edit") if isinstance(p.get("edit"), dict) else {}
    kind = edit.get("kind")
    cid = p.get("id")
    domain = (edit.get("by_domain") or "").strip()
    if not cid:
        return {"error": "this claim proposal names no company"}
    if not domain:
        # An unattributable correction is worse than none: it reads as the
        # company's own words and nothing can say whose they were.
        return {"error": "no claimant domain survived intake, so nothing here "
                         "can be attributed to the company. Refuse it."}

    if kind == "category":
        return {"error": "a category is a REQUEST, not an edit - claim.js says "
                         "so to the claimant's face, and landing it here would "
                         f"make it one. Move {p.get('name') or cid} on the "
                         "wrong-bucket queue if the request is right."}
    if kind == "competitors":
        return {"error": "a company does not edit who it is shortlisted "
                         "against. This should never have reached the queue."}

    companies = admin.read_companies()
    c = next((x for x in companies if x.get("id") == cid), None)
    if c is None:
        return {"error": f"no company {cid!r}"}
    note = f"correction from {domain}" + (f": {why}" if why else "")

    if kind == "description":
        text = (edit.get("description") or "").strip()
        if len(text) < 20:
            return {"error": "a description that short is not a description"}
        if text == (c.get("description") or "").strip():
            # A WRITE THAT CHANGES NOTHING MUST SAY SO - act_patch reported
            # "updated" over an untouched record and a caller believed it.
            return {"error": f"{c.get('name')} already reads exactly that; "
                             f"nothing to write"}
        c["description"] = text
        bad = admin.save_companies(companies, "claim-description", why=note, by=by)
        return ({"error": bad} if bad else
                {"ok": True, "message": f"{c.get('name')} describes itself: "
                                        f"{text[:80]}"})

    if kind == "profile":
        paras = edit.get("paragraphs")
        if not isinstance(paras, list) or not any(str(s).strip() for s in paras):
            return {"error": "a write-up with no paragraphs has nothing to land"}
        try:
            c["profile"] = _pp.record_from_company(paras, domain, by)
        except ValueError as e:
            return {"error": str(e)}
        # An accepted write-up must be visible; a company that had been hidden
        # and has now written its own is no longer hidden.
        c.pop("profile_hidden", None)
        bad = admin.save_companies(companies, "claim-profile", why=note, by=by)
        return ({"error": bad} if bad else
                {"ok": True, "message": f"{c.get('name')} write-up, in their "
                                        f"own words ({len(c['profile']['paragraphs'])} para)"})

    if kind == "job":
        title = (edit.get("title") or "").strip()
        url = (edit.get("url") or "").strip()
        if not title or not url:
            return {"error": "a posting needs a title and a link"}
        # THROUGH act_capture, not around it. It holds the junk and evergreen
        # filters, the nav-lookalike warning, and the posting key build_board
        # re-derives - company::title::hash(url+location) - so two reqs with
        # one title stay two rows. Keying a claimant's job any other way is
        # the Xplor trap in reverse.
        res = admin.act_capture({"company_id": cid, "page_url": url,
                                 "jobs": [{"title": title, "url": url,
                                           "location": edit.get("location") or ""}]})
        if res.get("error"):
            return res
        return {"ok": True, "message": f"{c.get('name')}: {title} - "
                                       f"{res.get('message') or 'captured'}"}

    if kind == "logo":
        # THROUGH logos.install, not around it. That door holds the https
        # rule, the same-domain rule, the size ceiling, the magic-byte sniff
        # and the one-file-per-company sweep - and it is the only writer of
        # assets/logos, so a second caller reimplementing any of them is a
        # second set of rules. Not journalled, because the journal covers data
        # files and this is a file in git: `git log assets/logos/<id>.*` is
        # the before-image, and reverting is a checkout.
        import logos as _logos
        res = _logos.install(cid, (edit.get("url") or "").strip(), domain,
                             by=by, write=True)
        if res.get("error"):
            return res
        return {"ok": True, "message": f"{c.get('name')}: {res['message']}"}

    if kind == "contact":
        text = (edit.get("note") or "").strip()
        if not text:
            return {"error": "nothing was written"}
        try:
            _notes.add(c, text, by=f"claim:{domain}")
        except ValueError as e:
            return {"error": str(e)}
        bad = admin.save_companies(companies, "claim-note", why=note, by=by)
        return ({"error": bad} if bad else
                {"ok": True, "message": f"noted against {c.get('name')}"})

    return {"error": f"a claim of kind {kind!r} has no door here. The kinds "
                     f"claim.js sends are description, profile, logo, job, "
                     f"category and contact"}


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
    # A SCOPE RULING RIDES ON THE CANDIDATE ROW, NEVER ON THE PROPOSAL. The
    # model is never asked for sled_only (agents.py rule 8 refuses a proposal
    # carrying it); a person's 'sled' call from the vendor-scope door is
    # written onto the candidate row by apply_web_rulings, and read back here
    # from that row, by name, when the card lands.
    sled = _candidate_flag(name, "sled_only")
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
        "researched": True,
        **({"sled_only": True} if sled else {})})
    bad = admin.save_companies(companies, "add-company",
                               why=(why or p.get("why") or "")[:300], by=by)
    return ({"error": bad} if bad else
            {"ok": True, "message": f"{name} added as {cid} ({sector} / {cat})"
                                    + (", SLED-only per the owner's scope call" if sled else "")})


def _candidate_flag(name: str, flag: str):
    """A flag a PERSON put on the candidate row (apply_web_rulings writes
    sled_only from a vendor-scope call). Looked up by normalised name; a row
    that is not there, or a file that is not, is simply no flag."""
    import promote_candidates as pc
    path = admin.DATA / "conference_intake" / "govtech_candidates.json"
    try:
        rows = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(rows, dict):
        rows = list(rows.values())
    key = pc.norm(name)
    for r in rows:
        if isinstance(r, dict) and pc.norm(r.get("name") or "") == key:
            return r.get(flag)
    return None


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


def _accept_buyer(p: dict, by: str, why: str, force: bool, store: dict,
                  key: str) -> dict:
    """Land ONE buyer answer through promote_profiles.land_buyer, the same
    function that lands a category, so a single ruling and a batch write the
    same fields with the same provenance.

    IT NEVER SETS sled_only FROM HERE, and that is not an oversight. The flag
    makes build_board drop every posting whose title does not name the public
    sector, so accepting one row in a queue would silently subtract jobs from
    a public board. land_buyer takes `with_sled` and only the CLI passes it,
    behind a gate review that prints how many postings are at stake first.
    """
    import promote_profiles
    if not p.get("sells_to_gov") or not p.get("buyer"):
        return {"error": "nothing to land: this row carries no verdict. A "
                         "refused answer stays where it is, for reading"}
    companies = admin.read_companies()
    rep = promote_profiles.land_buyer(
        store, companies, [key], by,
        why or f"landed {p.get('id')} from the proposals queue",
        with_sled=False)
    if not rep.get("wrote"):
        held = "; ".join(f"{n}: {r}" for n, r in (rep.get("held") or [])[:2])
        return {"error": f"the buyer answer could not be landed"
                         + (f" - {held}" if held else "; see the journal")}
    tail = ("  It is eligible for sled_only and did NOT get it: that flag "
            "removes postings and is set from the CLI behind a gate review."
            if p.get("sled_eligible") else "")
    return {"ok": True, "message": f"buyer recorded for "
                                   f"{p.get('name') or p.get('id')}.{tail}"}


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
        _log_employer_ruling(p, key, False, by, why)
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
    elif kind == "claim":
        res = _accept_claim(p, by, why, force)
    elif kind == "rival":
        res = _accept_rival(p, by, why, force, store)
    elif kind == "profile":
        res = _accept_profile(p, by, why, force, store, key)
    elif kind == "buyer":
        res = _accept_buyer(p, by, why, force, store, key)
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
    _log_employer_ruling(p, key, True, by, why)
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
