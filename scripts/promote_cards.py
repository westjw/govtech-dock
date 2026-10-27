#!/usr/bin/env python3
"""The door that lands researched candidates as companies, and the gate a
person reads before it does.

    python3 scripts/promote_cards.py --verify              # evidence against the site
    python3 scripts/promote_cards.py --second-read reads.json
    python3 scripts/promote_cards.py --gate                # what a person reads
    python3 scripts/promote_cards.py --land --by owner     # land what passed

THE OWNER'S RULING (2026-09-09, restated 2026-10-09): a high-confidence row
publishes without waiting for him. The gate is the door, not his inbox; he
reads the exceptions. promote_profiles.py is this shape for write-ups and
landed 545 of them that way. This is the same shape for the research cards
`agents.py brief --kind card` asks for and `agents.py ingest --kind card`
takes in.

WHAT THE DOOR DEMANDS BEFORE A CARD LANDS, each a fact observed and recorded
on the proposal, none a model's say-so:

  1. check_card at ingest: a verdict, a sector and category the schema holds,
     a one-line description in their words not ours, a why, and evidence.
  2. --verify: the evidence is ON THE COMPANY'S OWN SITE. The quote the
     researcher gave is looked for in the page the crawler reads at the
     card's website (robots.txt honoured, our name on the request), through
     the same normalisation the write-up door uses. A quote that is not there
     is a card that does not land; the proposal says so and waits for a
     person.
  3. --second-read: a second reader, blind to the first, agreed with the
     verdict (the council rule; disagreement is the finding and goes to the
     person).
  4. high confidence. Medium, low and unsure go to the person, by the
     researcher's own account.

--land refuses until --gate has printed in this checkout, the way
promote_profiles does: what passed is landed by a door a person has looked
at, exception-first - the refusals with the rule that refused them, the
low-confidence rows, the evidence that was not on the page, the split
verdicts, and a 5% sample of what is about to land.

A GOVTECH VERDICT ON A NAME THE SUPPLIER FILE HOLDS IS A MOVE, NOT A TWIN.
intake filed hundreds of real vendors as suppliers before anybody read the
floors; _accept_card refuses "already on file" against suppliers too, so a
researched vendor could never leave that pile. When the only match is a
supplier row nobody researched, --land takes that row out through the
journal and lets the card in. A supplier a research pass stamped stays, and
the card is refused, because that is a disagreement between two readings.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
READ = DATA / ".cards_read"           # when --gate last printed, in this checkout
SAMPLE = 0.05
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402
import fetch_profiles as fp                                     # noqa: E402
import promote_candidates as pc                                 # noqa: E402
import proposal_rulings                                         # noqa: E402

MIN_QUOTE = 25


def cards(store: dict) -> list[tuple[str, dict]]:
    return [(k, p) for k, p in store.items()
            if isinstance(p, dict) and p.get("kind") == "card"]


def pending(store: dict) -> list[tuple[str, dict]]:
    return [(k, p) for k, p in cards(store) if p.get("status") == "pending"]


# ------------------------------------------------------------- verify --

def page_text(url: str, fetch=None) -> tuple[str | None, str | None]:
    """(normalised text, why not). `fetch(url)` stands in for the crawler."""
    if not url:
        return None, "the card names no website"
    got = (fetch or fp.grab)(url)
    if got.get("unread"):
        return None, f"the site did not read: {got['unread']}"
    return agents._pf_norm(got.get("text") or ""), None


def evidence_on_page(quote: str, text: str) -> bool:
    """The quote, normalised like the page, is in the page."""
    q = agents._pf_norm(quote or "").strip(" .\"'")
    if len(q) < MIN_QUOTE:
        return False
    return q in text


def verify(store: dict, limit: int | None = None, fetch=None, by: str = "agent:card-door") -> dict:
    """Stamp `evidence_verified` on every pending card not yet checked."""
    on = dt.date.today().isoformat()
    done = {"verified": 0, "not_on_page": 0, "unread": 0}
    todo = [(k, p) for k, p in pending(store) if "evidence_verified" not in p]
    for k, p in todo[:limit] if limit else todo:
        # THE PAGE THE RESEARCHER CITED FIRST, then the website. A state-and-
        # local page two clicks in is where a vendor says who it sells to;
        # looking only at the homepage called verbatim quotes "not on the page"
        # (batch 003 of the 2026-10-10 research, Argyle and Armis).
        pages = [u for u in (p.get("evidence_url"), (p.get("saw") or {}).get("url"), p.get("website")) if u]
        pages = list(dict.fromkeys(pages))
        text, why, found, where = None, "the card names no page", False, None
        for u in pages:
            text, why = page_text(u, fetch)
            if text is not None and evidence_on_page(p.get("evidence") or "", text):
                found, where = True, u
                break
        if found:
            p["evidence_verified"], p["evidence_why"] = True, f"the quote is on {where}"
            done["verified"] += 1
        elif text is None:
            p["evidence_verified"], p["evidence_why"] = False, why
            done["unread"] += 1
        else:
            p["evidence_verified"] = False
            p["evidence_why"] = "the quote is not on the page the crawler reads at the cited page or the website"
            done["not_on_page"] += 1
        p["evidence_checked_on"] = on
    if any(done.values()):
        # the counts above are what journal.BLAST asks for before a write
        # touching more than 25 records; they are printed by the caller
        bad = agents.save(store, "card-evidence", why=json.dumps(done), by=by,
                          force=sum(done.values()) > 25)
        if bad:
            raise SystemExit(f"refused: {bad}")
    return done


# -------------------------------------------------------- second read --

def second_read(store: dict, reads: list[dict], by: str) -> dict:
    """Record a blind second reader's verdict beside each card.

    `reads`: [{key, verdict, why}]. Agreement is the same verdict; anything
    else is a split the person sees in the gate.
    """
    out = {"agree": 0, "split": 0, "unknown": 0}
    for r in reads:
        p = store.get(r.get("key"))
        if not isinstance(p, dict) or p.get("kind") != "card":
            out["unknown"] += 1
            continue
        agree = (r.get("verdict") == p.get("verdict"))
        p["second_read"] = {"verdict": r.get("verdict"), "why": (r.get("why") or "")[:200],
                            "by": by, "agree": agree}
        out["agree" if agree else "split"] += 1
    bad = agents.save(store, "card-second-read", why=json.dumps(out), by=by,
                      force=(out["agree"] + out["split"]) > 25)
    if bad:
        raise SystemExit(f"refused: {bad}")
    return out


# ---------------------------------------------------------------- gate --

def passes(p: dict) -> bool:
    return (p.get("status") == "pending" and p.get("confidence") == "high"
            and p.get("evidence_verified") is True
            and (p.get("second_read") or {}).get("agree") is True)


def gate(store: dict, seed: int = 0) -> dict:
    rows = cards(store)
    refused = [(k, p) for k, p in rows if p.get("status") == "refused"]
    pend = pending(store)
    low = [(k, p) for k, p in pend if (p.get("confidence") or "unsure") != "high"]
    unverified = [(k, p) for k, p in pend if p.get("confidence") == "high"
                  and p.get("evidence_verified") is not True]
    split = [(k, p) for k, p in pend if p.get("confidence") == "high"
             and p.get("evidence_verified") is True
             and (p.get("second_read") or {}).get("agree") is not True]
    landing = [(k, p) for k, p in pend if passes(p)]
    rng = random.Random(seed or dt.date.today().toordinal())
    sample = rng.sample(landing, max(1, int(len(landing) * SAMPLE))) if landing else []

    def show(p):
        print(f"   - {p.get('name')}: {p.get('verdict')} / {p.get('confidence')} "
              f"-> {p.get('sector')} / {p.get('category')}")
        print(f"       {(p.get('description') or '')[:140]}")
        print(f"       why: {(p.get('why') or '')[:140]}")
        print(f"       evidence: \"{(p.get('evidence') or '')[:140]}\"  {p.get('website') or ''}")
        sr = p.get("second_read") or {}
        if sr:
            print(f"       second reader: {sr.get('verdict')} - {(sr.get('why') or '')[:100]}")

    print(f"GATE REVIEW: research cards  ({len(rows)} on file, {len(pend)} pending)")
    print(f"\n== 1. Refused by the door: {len(refused)} ==")
    by_rule: dict[str, int] = {}
    for _, p in refused:
        rule = (p.get("refused_why") or "?").split(".")[0][:70]
        by_rule[rule] = by_rule.get(rule, 0) + 1
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print(f"   {n:4}  {rule}")
    print(f"\n== 2. Medium / low / unsure confidence: {len(low)} ==")
    for _, p in low:
        show(p)
    print(f"\n== 3. High confidence, evidence NOT found on the site: {len(unverified)} ==")
    for _, p in unverified:
        print(f"   - {p.get('name')}: {p.get('evidence_why') or 'not checked yet (--verify)'}")
    print(f"\n== 4. Two readers split: {len(split)} ==")
    for _, p in split:
        show(p)
    print(f"\n== 5. Sample of what will land: {len(sample)} of {len(landing)} ==")
    for _, p in sample:
        show(p)
    READ.write_text(json.dumps({"on": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                "landing": len(landing)}))
    print(f"\n  Land the {len(landing)}:  python3 scripts/promote_cards.py --land --by owner")
    return {"refused": refused, "low": low, "unverified": unverified, "split": split,
            "landing": landing, "sample": sample}


# ---------------------------------------------------------------- land --

def _site(url) -> str:
    """A website as the company it names: host, lower case, no www."""
    import urllib.parse as up
    u = str(url or "").strip()
    if not u:
        return ""
    h = (up.urlsplit(u if "//" in u else "//" + u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _supplier_only_match(name: str, website: str | None = None) -> dict | None:
    """The one supplier row this card already is, when nothing on the board
    matches and nobody researched that row. None otherwise.

    THE SAME COMPANY, NOT A NAME INSIDE A NAME. This used same_company, the
    candidate door's containment match, which strips "Group, Inc." and finds
    "clarity" inside "Clarity Movement": moving a row DELETES it, and on
    2026-10-20 it deleted the unrelated suppliers "Clarity Group, Inc." and
    "Public Safety Group" to make way for two cards. A row is moved only when
    its normalised name is the card's, or its website is the card's.
    """
    companies = admin.read_companies()
    key = pc.norm(name)
    if pc.same_company(key, {pc.norm(c["name"]): c["name"] for c in companies}):
        return None
    site = _site(website)
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    hits = [s for s in suppliers
            if pc.norm(s["name"]) == key or (site and _site(s.get("website")) == site)]
    if len(hits) != 1:
        return None
    s = hits[0]
    if s.get("researched") or s.get("govtech") is not None:
        return None
    return s


def land(store: dict, by: str, limit: int | None = None, keys: set | None = None) -> dict:
    """Land every pending card that passes - or, with `keys`, only those.

    A ROUND LANDS ITS OWN KEYS. --land with no filter takes every pending
    card that passes, the owner's held pile included (CLAUDE.md). The weekly
    conference scrub (owner, 2026-10-27: "Weekly, Sundays") runs unattended,
    so it hands over the keys of the cards it researched itself, and nothing
    else can land on its run.
    """
    if not READ.exists():
        raise SystemExit("refusing: --gate has not printed in this checkout. A door a "
                         "person has not looked at is not a door.")
    out = {"landed": 0, "suppliers_moved": 0, "refused": []}
    todo = [(k, p) for k, p in pending(store) if passes(p) and (keys is None or k in keys)]
    for k, p in todo[:limit] if limit else todo:
        moved, before = None, None
        if p.get("verdict") == "govtech":
            s = _supplier_only_match(p.get("name") or "", p.get("website"))
            if s:
                before = json.loads((DATA / "suppliers.json").read_text())
                keep = [r for r in before if r["id"] != s["id"]]
                bad = admin.save_decisions("suppliers.json", keep, "promote-supplier-to-card",
                                           why=f"{s['name']}: researched as a govtech vendor; "
                                               f"the card replaces the unresearched supplier row",
                                           by=by)
                if bad:
                    out["refused"].append((k, bad))
                    continue
                moved = s
        res = proposal_rulings.rule(store, k, True, why="passed the card door: high confidence, "
                                    "evidence on the site, second reader agreed", by=by)
        if res.get("error"):
            # A REFUSED CARD GIVES THE ROW BACK. The row was taken out so the
            # card could land in its place; when the card does not land, the
            # supplier is put back as it was, through the journal - or a
            # refusal deletes a company (2026-10-20, two suppliers lost).
            if moved:
                bad = admin.save_decisions("suppliers.json", before, "restore-supplier",
                                           why=f"{moved['name']}: the card meant to replace it was "
                                               f"refused ({str(res['error'])[:120]})", by=by)
                if bad:
                    res = {"error": f"{res['error']}; AND the supplier row could not be put back: {bad}"}
            out["refused"].append((k, res["error"]))
        else:
            out["landed"] += 1
            if moved:
                out["suppliers_moved"] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--second-read", metavar="FILE")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--land", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--by", default="agent:card-door")
    ap.add_argument("--keys", metavar="FILE",
                    help="a JSON list of proposal keys: --land takes only these (a round's own cards)")
    a = ap.parse_args()
    store = agents.load()
    if a.verify:
        done = verify(store, a.limit, by=a.by)
        print(f"evidence checked: {done}")
    if a.second_read:
        reads = json.loads(pathlib.Path(a.second_read).read_text())
        print(f"second reads recorded: {second_read(store, reads, a.by)}")
    if a.gate:
        gate(store)
    if a.land:
        keys = set(json.loads(pathlib.Path(a.keys).read_text())) if a.keys else None
        res = land(store, a.by, a.limit, keys)
        print(f"landed {res['landed']} card(s); {res['suppliers_moved']} supplier row(s) moved")
        for k, why in res["refused"]:
            print(f"   refused {k}: {why}")
    if not (a.verify or a.second_read or a.gate or a.land):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
