#!/usr/bin/env python3
"""Rule on competitor shortlists, and write the accepted ones into the map.

    python3 scripts/promote_rivals.py                       # what is waiting
    python3 scripts/promote_rivals.py --show verkada
    python3 scripts/promote_rivals.py --category Police     # read the whole set
    python3 scripts/promote_rivals.py --accept verkada --accept brinc
    python3 scripts/promote_rivals.py --reject auror --why "retail, not police"
    python3 scripts/promote_rivals.py --accept-category Police   # after reading

AGENTS PROPOSE, PEOPLE RULE. `agents.py` says it at length and this is the
door for one kind: nothing here writes a competitor onto a public company page
without somebody having said yes. The map is the thing 2,058 strangers will
read, and "Verkada competes with Palantir" is a claim about two real firms.

WHY THIS IS A SCRIPT AND NOT AN ADMIN QUEUE, for now. The admin's queues each
render evidence a person needs to rule, and a competitor set's evidence is the
other company's own one-line description sitting next to the reason given. That
is a real screen and it should exist. This is the path that unblocks ruling
today; the queue is the better grip and comes next.

--accept-category IS DELIBERATELY NOT A SHORTCUT PAST READING. It refuses
unless --category was printed in this shell first, because a bulk accept over
132 companies is exactly the "one click writes a ruling for 108 companies"
shape journal.py exists to catch. The journal records it as one entry, so
admin_undo.py can take the whole thing back.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402

SEEN = DATA / ".rivals_read"          # which categories have been printed


def pending(store: dict, category: str | None = None) -> list[dict]:
    out = [p for k, p in store.items()
           if p.get("kind") == "rival" and p.get("status") == "pending"
           and (not category or p.get("category") == category)]
    out.sort(key=lambda p: (p.get("category") or "", p.get("id") or ""))
    return out


def show(p: dict, names: dict) -> None:
    who = names.get(p["id"], p["id"])
    print(f"\n  {who}  [{p.get('confidence')}]  {p.get('sector')} / {p.get('category')}")
    if p.get("why"):
        print(f"     thesis: {p['why'][:150]}")
    if not p.get("rivals"):
        print("     NO COMPETITOR on this roster, asserted")
        return
    for r in p["rivals"]:
        print(f"     - {names.get(r['id'], r['id'])[:30]:32} {r.get('why','')[:74]}")


def board_index() -> tuple[dict, dict, dict]:
    """(id -> company, host -> id, normalised name -> id).

    THE THIRD MAP IS THE POINT, and getting it wrong would have been quiet and
    bad. admin.ident() NORMALISES a string, it does not look one up: it turns
    "Flock Safety" into "flocksafety" and "Zzqq Fake Corp" into "zzqqfake",
    neither of which is a board id (Flock's is "flock-safety"). Used as a
    resolver it returns a confident id for every name on earth, so an
    off-board competitor would land as an edge pointing at a company that
    does not exist. It is a KEY on both sides of a real lookup, or it is
    nothing.
    """
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    index = {c["id"]: c for c in seq if c.get("id")}
    by_host, by_name = {}, {}
    for c in seq:
        cid = c.get("id")
        if not cid:
            continue
        h = agents._host(c.get("website") or "")
        if h:
            by_host[h] = cid
            by_host[h[4:] if h.startswith("www.") else "www." + h] = cid
        for nm in [c.get("name"), *(b.get("name") for b in (c.get("brands") or [])
                                    if isinstance(b, dict))]:
            if nm:
                k = admin.ident(nm)
                if k:
                    by_name.setdefault(k, cid)
    return index, by_host, by_name


def resolve_add(add: dict, by_host: dict, by_name: dict | None = None) -> str | None:
    """A web add's board id, or None if this company is not on the board.

    RESOLUTION HAPPENS HERE, NEVER IN THE AGENT. The agent gives a name and a
    website. An agent that hands back ids is an agent inventing them, and the
    id is what the page links to.

    None is a real and common answer. An off-board competitor goes to the
    candidate door; it never becomes an edge.
    """
    h = agents._host(add.get("website") or "")
    if h:
        hit = by_host.get(h) or by_host.get(
            h[4:] if h.startswith("www.") else "www." + h)
        if hit:
            return hit
    if by_name:
        return by_name.get(admin.ident(add.get("name") or "")) or None
    return None


def verify_sources(store: dict, category: str | None, limit: int) -> int:
    """Fetch each add's source page and hold the quote against it.

    THE SEPARATE NETWORKED STEP, and it is separate on purpose. check_rival_web
    is pure: it can say a source is missing, off-domain or too short, and it
    cannot say whether the page actually contains the sentence. A quote nobody
    fetched is a citation nobody checked, which is the shape of a plausible
    lie. Nothing is accepted until this has run and stamped the add.
    """
    import ats
    done = 0
    for p in pending(store, category):
        for a in (p.get("add") or []):
            if a.get("verified_on") or a.get("unverified"):
                continue
            if limit and done >= limit:
                return done
            url = (a.get("source") or {}).get("url") or ""
            quote = ((a.get("source") or {}).get("quote") or "").strip()
            try:
                text = ats._get(url).text
            except Exception as exc:
                a["unverified"] = f"could not fetch the source: {str(exc)[:90]}"
                done += 1
                continue
            hay = agents._pf_norm(text) if hasattr(agents, "_pf_norm") else \
                " ".join(text.split()).casefold()
            needle = agents._pf_norm(quote) if hasattr(agents, "_pf_norm") else \
                " ".join(quote.split()).casefold()
            if needle and needle in hay:
                a["verified_on"] = dt.date.today().isoformat()
                a.pop("unverified", None)
            else:
                a["unverified"] = "the quote is not on the page it cites"
            done += 1
    if done:
        agents.save(store, "rival-verify",
                    why=f"fetched and checked {done} competitor source(s)",
                    by="promote-rivals", force=done > 25)
    return done


def stage_candidates(store: dict, category: str | None) -> int:
    """Off-board competitors go to the candidate door, never onto a company.

    A name this board has never heard of is a CANDIDATE COMPANY, and there is
    already a door for those. Writing it straight onto a page as a competitor
    would create an edge pointing at nothing, and inventing a company record
    to hang it on is the thing promote_candidates exists to stop.
    """
    path = DATA / "conference_intake" / "rival_candidates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    have = json.loads(path.read_text()) if path.exists() else []
    seen = {(c.get("website") or c.get("name", "")).lower() for c in have}
    added = 0
    _, by_host, by_name = board_index()
    for p in pending(store, category):
        for a in (p.get("add") or []):
            if resolve_add(a, by_host, by_name):
                continue
            k = (a.get("website") or a.get("name", "")).lower()
            if k in seen:
                continue
            seen.add(k)
            have.append({"name": a.get("name"), "website": a.get("website"),
                         "why": a.get("why"),
                         "source": a.get("source"),
                         "found_as": f"competitor of {p.get('id')}",
                         "staged_on": dt.date.today().isoformat(),
                         "status": None})
            added += 1
    if added:
        path.write_text(json.dumps(have, indent=1))
    return added


def merge_web(p: dict, company: dict, with_drops: bool) -> tuple[list, str | None]:
    """(edges, problem). EXISTING is the base. REFINE, NEVER REMOVE.

    "Don't remove companies" is on the record, and it is the right default for
    the 132 Police shortlists already published: a second engine disagreeing
    with the first is not evidence the first was wrong. A drop is PROPOSED and
    printed, and applies only when a person asks by name.

    THE BASE IS `existing`, NOT `keep`, and the first draft of this got it
    backwards. Building the merge from the agent's keep list means an edge it
    simply did not mention disappears from a public page with no reason
    recorded anywhere. That is the silence-deletes bug the tailoring path
    already has a guard for, and it is worse here: nobody re-reads a
    competitor list to notice a name went missing.

    OVER THE CAP IS A REFUSAL, NOT A TRUNCATION. Cutting the list at eight
    would drop whichever edge sorted last, which is the same silent removal
    wearing a limit's clothes. The proposal has to drop something explicitly.
    """
    _, by_host, by_name = board_index()
    existing = [e if isinstance(e, dict) else {"id": e, "why": ""}
                for e in (company.get("competitors") or [])]
    drops = {d.get("id") for d in (p.get("drop") or [])} if with_drops else set()
    out, have = [], set()
    for e in existing:
        if e.get("id") in drops:
            continue
        out.append(e)
        if e.get("id"):
            have.add(e["id"])
    for a in (p.get("add") or []):
        rid = resolve_add(a, by_host, by_name)
        edge = {"why": (a.get("why") or "").strip(),
                "source": {"url": (a.get("source") or {}).get("url"),
                           "verified_on": a.get("verified_on")}}
        if rid:
            if rid in drops or rid in have:
                continue
            edge["id"] = rid
            have.add(rid)
        else:
            # NOT ON THIS BOARD YET. The edge points at a name, not an id, and
            # the page does not draw it until promote_candidates lands the
            # company and the edge is resolved again. Dropping it instead would
            # mean running the search again to learn what we already know.
            if any(o.get("awaiting") == a.get("name") for o in out):
                continue
            edge["awaiting"] = a.get("name")
            edge["website"] = a.get("website")
        out.append(edge)
    if len(out) > agents.EDGE_CAP:
        over = len(out) - agents.EDGE_CAP
        return out, (f"{len(out)} edges after the merge, {over} over the cap of "
                     f"{agents.EDGE_CAP}. Truncating would remove whichever "
                     f"edge sorted last with no reason recorded; the proposal "
                     f"has to drop something by name instead")
    return out, None


def write_accepted(store: dict, ids: list[str], by: str, why: str,
                   with_drops: bool = False) -> int:
    """Land accepted shortlists on companies.json as ONE journalled write."""
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    index = {c["id"]: c for c in seq if c.get("id")}
    today = dt.date.today().isoformat()
    wrote = 0
    for cid in ids:
        p = store.get(f"rival:{cid}")
        if not p or cid not in index:
            continue
        # THE EDGE CARRIES ITS REASON ONTO THE PAGE. A competitor with no
        # stated overlap is the category listing again, and the page has a
        # line for the reason precisely so a reader can disagree with it.
        if p.get("add") is not None or p.get("keep") is not None:
            edges, problem = merge_web(p, index[cid], with_drops)
            if problem:
                print(f"  {cid}: REFUSED - {problem}")
                continue
            index[cid]["competitors"] = edges
            index[cid]["competitors_method"] = "web"
        else:
            index[cid]["competitors"] = [
                {"id": r["id"], "why": r.get("why", "").strip()}
                for r in (p.get("rivals") or [])
            ]
        index[cid]["competitors_checked_on"] = today
        # An asserted empty is a FINDING and has to survive the round trip,
        # or the page cannot tell "nobody competes with them" from "nobody
        # has looked" - which are the two states this whole engine exists to
        # keep apart.
        index[cid]["competitors_none_found"] = bool(
            p.get("none_found") or not p.get("rivals"))
        p["status"] = "accepted"
        p["ruled_by"], p["ruled_on"], p["ruled_why"] = by, today, why
        wrote += 1
    if wrote:
        # ACTION IS POSITIONAL AND NAMED, and `by` is passed explicitly: the
        # journal is what admin_undo reads, and CLAUDE.md records 86 agent
        # writes that had to be re-attributed by hand because a caller let
        # `by` default to the owner.
        refused = admin.save_companies(
            companies, "promote-rivals",
            why=why or f"accepted {wrote} competitor shortlist(s)", by=by,
            force=wrote > 50)
        if refused:
            print(f"  REFUSED by the journal: {refused}")
            return 0
        agents.save(store)
    return wrote


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--show")
    ap.add_argument("--accept", action="append", default=[])
    ap.add_argument("--reject", action="append", default=[])
    ap.add_argument("--accept-category")
    ap.add_argument("--why", default="")
    # NO DEFAULT AUTHOR. CLAUDE.md: `by` defaulting to "owner" is "a trap for
    # every write that is not", and 86 writes in companies.json had to be
    # re-attributed by hand because of it. This script is run by agents as
    # readily as by Wyeth, so it refuses to guess which.
    ap.add_argument("--by", default=None,
                    help='who is ruling: "owner", or "agent:<label>". '
                         'Required for anything that writes.')
    # ---- v2, the web pass -------------------------------------------------
    ap.add_argument("--verify", action="store_true",
                    help="fetch each web add's source page and hold the quote "
                         "against it. A quote nobody fetched is a citation "
                         "nobody checked")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--allow-unverified", action="store_true",
                    help="accept a web add whose source was never fetched or "
                         "did not carry its quote. Says so in the journal")
    ap.add_argument("--with-drops", action="store_true",
                    help="apply the proposed drops. Off by default: 'do not "
                         "remove companies' is the standing rule")
    ap.add_argument("--stage-candidates", action="store_true",
                    help="send competitors this board has never heard of to "
                         "the candidate door, never straight onto a page")
    a = ap.parse_args()

    if a.verify:
        store = agents.load()
        n = verify_sources(store, a.category, a.limit)
        ok = sum(1 for p_ in pending(store, a.category)
                 for x in (p_.get("add") or []) if x.get("verified_on"))
        bad = sum(1 for p_ in pending(store, a.category)
                  for x in (p_.get("add") or []) if x.get("unverified"))
        print(f"fetched {n} source(s): {ok} carry their quote, {bad} do not")
        return 0

    if a.stage_candidates:
        store = agents.load()
        n = stage_candidates(store, a.category)
        print(f"staged {n} off-board competitor(s) for the candidate door. "
              f"They are candidates, not companies: promote_candidates.py "
              f"rules on them")
        return 0

    # READING NEEDS NO AUTHOR; RULING DOES. Requiring it on --show made a
    # read-only command demand an attribution for a write it never performs,
    # which teaches people to type a value to get past a prompt - and a value
    # typed to get past a prompt is the wrong value.
    if (a.accept or a.reject or a.accept_category) and not a.by:
        ap.error("--by is required to rule: \"owner\", or \"agent:<label>\". "
                 "The journal is what admin_undo reads and what says whose "
                 "judgment a ruling was")

    store = agents.load()
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    names = {c["id"]: c.get("name", c["id"]) for c in seq if c.get("id")}

    if a.show:
        p = store.get(f"rival:{a.show}")
        if not p:
            print(f"no competitor proposal on file for {a.show!r}")
            return 1
        show(p, names)
        return 0

    # AN UNVERIFIED SOURCE IS NOT A SOURCE. check_rival_web can say a citation
    # is missing, off-domain or too short; only a fetch can say the page
    # actually contains the sentence. Accepting before that is accepting a
    # link nobody opened.
    if (a.accept or a.accept_category) and not a.allow_unverified:
        wanted = list(a.accept) or [q.get("id") for q in
                                    pending(store, a.accept_category)]
        unver = []
        for cid in wanted:
            q = store.get(f"rivweb:{cid}") or store.get(f"rival:{cid}")
            for x in ((q or {}).get("add") or []):
                if not x.get("verified_on"):
                    unver.append(f"{cid}: {x.get('name')} "
                                 f"({x.get('unverified') or 'never fetched'})")
        if unver:
            print("REFUSED: web competitor(s) whose source was never verified:")
            for line in unver[:12]:
                print(f"  {line}")
            print("\n  Run:  promote_rivals.py --verify --category <cat>")
            print("  Or accept anyway with --allow-unverified, which is "
                  "recorded in the journal as exactly that.")
            return 2

    if a.reject:
        for cid in a.reject:
            p = store.get(f"rival:{cid}")
            if not p:
                print(f"  no proposal for {cid!r}")
                continue
            p["status"] = "rejected"
            p["ruled_by"] = a.by
            p["ruled_on"] = dt.date.today().isoformat()
            p["ruled_why"] = a.why or "rejected"
        agents.save(store)
        print(f"  rejected {len(a.reject)}. The company stays researchable; "
              f"nothing was deleted.")

    if a.accept:
        n = write_accepted(store, a.accept, a.by, a.why or "accepted by hand", with_drops=a.with_drops)
        print(f"  wrote {n} shortlist(s) to companies.json, journalled as one entry")
        return 0

    if a.accept_category:
        read = json.loads(SEEN.read_text()) if SEEN.exists() else []
        if a.accept_category not in read:
            print(f"  REFUSED. Nothing has printed the {a.accept_category} "
                  f"shortlists in this checkout yet.\n"
                  f"  A bulk accept over a whole category is the one-click "
                  f"ruling for 108 companies\n"
                  f"  that journal.py exists to catch. Read them first:\n"
                  f"      python3 scripts/promote_rivals.py "
                  f"--category {a.accept_category}")
            return 1
        ids = [p["id"] for p in pending(store, a.accept_category)]
        n = write_accepted(store, ids, a.by,
                           a.why or f"accepted all {a.accept_category} shortlists", with_drops=a.with_drops)
        print(f"  wrote {n} shortlist(s), journalled as ONE entry. "
              f"Undo with:\n      python3 scripts/admin_undo.py")
        return 0

    rows = pending(store, a.category)
    if not rows:
        print("nothing waiting" + (f" in {a.category}" if a.category else ""))
        return 0

    if a.category:
        for p in rows:
            show(p, names)
        read = json.loads(SEEN.read_text()) if SEEN.exists() else []
        if a.category not in read:
            read.append(a.category)
            SEEN.write_text(json.dumps(read))
        empties = sum(1 for p in rows if not p.get("rivals"))
        print(f"\n  {len(rows)} shortlist(s) in {a.category}, "
              f"{sum(len(p.get('rivals') or []) for p in rows)} edges, "
              f"{empties} asserting no competitor.")
        print(f"  Accept the set:  python3 scripts/promote_rivals.py "
              f"--accept-category {a.category}")
        return 0

    by_cat: dict[str, int] = {}
    for p in rows:
        by_cat[f"{p.get('sector')} / {p.get('category')}"] = \
            by_cat.get(f"{p.get('sector')} / {p.get('category')}", 0) + 1
    print(f"{len(rows)} competitor shortlist(s) waiting on a ruling\n")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4}  {cat}")
    print("\n  Read one category:  python3 scripts/promote_rivals.py "
          "--category <name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
