#!/usr/bin/env python3
"""The gate review for company write-ups, and the door that lands them.

    python3 scripts/promote_profiles.py                      # the funnel
    python3 scripts/promote_profiles.py --gate Police        # what a person reads
    python3 scripts/promote_profiles.py --show brinc         # one, sentence by source
    python3 scripts/promote_profiles.py --land Police --by owner
    python3 scripts/promote_profiles.py --reject brinc --why "..." --by owner
    python3 scripts/promote_profiles.py --hide brinc --why "..." --by owner

"DOOR ONLY, ADD SOME GATE REVIEWS" is the owner's ruling on how 2,024
write-ups reach public pages, and this is that shape. The door is
agents.check_profile: every sentence quotes the company's own site, every
named thing appears on it, no marketing adjectives, no first person. What
passes the door is landed by --land, a category at a time. What a person
reads before landing is --gate, and it is exception-based, not exhaustive:

  1. every proposal the door REFUSED, with the rule that refused it and the
     token it named - so a rule that is too tight is visible
  2. every proposal at medium, low or unsure confidence
  3. a 5% random sample of what passed, sentence beside quote beside URL

--land refuses until --gate has printed that category in this checkout, the
same way promote_rivals refuses a bulk accept nobody has read. Each landed
batch is ONE journal entry, so admin_undo takes a whole batch back. And
--hide is the kill switch: a person who sees a wrong write-up on the public
page throws profile_hidden on that company, journalled, and the page shows
the one-line record again by the next build.

WRITES LAND IN CHUNKS OF 500. journal.RUNAWAY refuses any save that
rewrites more than a third of the records even with force, and 2,024 of
2,063 is 98%. That guard is right and this script works within it.
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
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402

READ = DATA / ".profiles_read"       # categories --gate has printed
CHUNK = 500
SAMPLE = 0.05


def _by_category(store: dict, companies: list) -> dict:
    cat = {c["id"]: (c.get("category") or "?") for c in companies if c.get("id")}
    out: dict[str, list] = {}
    for k, p in store.items():
        if not isinstance(p, dict) or p.get("kind") != "profile":
            continue
        out.setdefault(cat.get(p.get("id"), "?"), []).append((k, p))
    return out


def show(p: dict, name: str) -> None:
    """One write-up, every sentence with its quote and URL beneath it.

    The raw text beside the reading. A person ruling on a sentence about a
    real company's customers should see the words on the page it came from
    without opening anything.
    """
    print(f"\n  {name}  [{p.get('confidence')}]  {p.get('status')}"
          + (f"  REFUSED: {p['refused_why']}" if p.get("status") == "refused" else ""))
    if p.get("why"):
        print(f"     thesis: {p['why'][:160]}")
    paras = p.get("paragraphs") or (p.get("proposal") or {}).get("paragraphs") or []
    for i, para in enumerate(paras):
        for j, s in enumerate(para if isinstance(para, list) else []):
            print(f"     [{i}.{j}] {s.get('text', '')}")
            print(f"           \"{(s.get('quote') or '')[:110]}\"")
            print(f"           {s.get('url', '')}")
    q = p.get("quote") or (p.get("proposal") or {}).get("quote")
    if isinstance(q, dict) and q.get("text"):
        print(f"     pull quote: \"{q['text'][:120]}\"  {q.get('url', '')}")


def gate(store: dict, companies: list, category: str, seed: int = 0) -> dict:
    """What a person reads for one category. Returns the three lists."""
    names = {c["id"]: c.get("name", c["id"]) for c in companies if c.get("id")}
    rows = _by_category(store, companies).get(category, [])
    refused = [(k, p) for k, p in rows if p.get("status") == "refused"]
    pending = [(k, p) for k, p in rows if p.get("status") == "pending"]
    low = [(k, p) for k, p in pending
           if (p.get("confidence") or "unsure") in ("medium", "low", "unsure")]
    high = [(k, p) for k, p in pending if (k, p) not in low]
    rng = random.Random(seed or dt.date.today().toordinal())
    sample = rng.sample(high, max(1, int(len(high) * SAMPLE))) if high else []

    print(f"GATE REVIEW: {category}  ({len(rows)} write-ups on file)")
    print(f"\n== 1. Refused by the door: {len(refused)} ==")
    by_rule: dict[str, int] = {}
    for _, p in refused:
        rule = (p.get("refused_why") or "?").split(".")[0][:60]
        by_rule[rule] = by_rule.get(rule, 0) + 1
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print(f"   {n:4}  {rule}")
    for k, p in refused[:12]:
        print(f"   - {names.get(p.get('id'), p.get('id'))}: {p.get('refused_why', '')[:120]}")
    print(f"\n== 2. Medium / low / unsure confidence: {len(low)} ==")
    for k, p in low:
        show(p, names.get(p.get("id"), p.get("id")))
    print(f"\n== 3. Sample of what passed: {len(sample)} of {len(high)} ==")
    for k, p in sample:
        show(p, names.get(p.get("id"), p.get("id")))
    read = json.loads(READ.read_text()) if READ.exists() else []
    if category not in read:
        read.append(category)
        READ.write_text(json.dumps(read))
    print(f"\n  Land the {len(pending)} pending:  python3 scripts/promote_profiles.py "
          f"--land {category!r} --by owner")
    return {"refused": refused, "low": low, "sample": sample, "pending": pending}


def _record(p: dict, by: str, today: str) -> dict:
    """The written shape: paragraphs as text, provenance beside them."""
    paras, prov = [], []
    for i, para in enumerate(p.get("paragraphs") or []):
        sents = para if isinstance(para, list) else []
        paras.append(" ".join((s.get("text") or "").strip() for s in sents).strip())
        for j, s in enumerate(sents):
            prov.append({"p": i, "s": j, "url": s.get("url"), "quote": s.get("quote")})
    seen = (p.get("saw") or {}).get("pages") or []
    return {"paragraphs": paras,
            "quote": p.get("quote") if isinstance(p.get("quote"), dict) else None,
            "provenance": prov,
            "sources": [{"url": pg.get("url"), "sha": pg.get("sha"),
                         "fetched_on": pg.get("fetched_on")} for pg in seen if pg.get("url")],
            "written_on": today, "by": p.get("by") or "agent",
            "ruled_by": by, "ruled_on": today}


def land(store: dict, companies: list, keys: list[str], by: str, why: str) -> int:
    """Write accepted write-ups onto companies.json, at most CHUNK per journal entry."""
    seq = companies if isinstance(companies, list) else list(companies.values())
    index = {c["id"]: c for c in seq if c.get("id")}
    today = dt.date.today().isoformat()
    wrote = skipped = 0
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start:start + CHUNK]
        n = 0
        for k in chunk:
            p = store.get(k)
            if not p or p.get("status") != "pending" or p.get("id") not in index:
                continue
            if not _has_text(p):
                # AN UNSURE ANSWER HAS NOTHING TO LAND. It passed the door
                # because unsure is a valid answer; landing it would write a
                # profile with no paragraphs, and the page would say "written
                # from their site" above nothing. It stays pending and is
                # counted so the person landing a category sees it.
                skipped += 1
                continue
            index[p["id"]]["profile"] = _record(p, by, today)
            index[p["id"]].pop("profile_hidden", None)
            p["status"] = "accepted"
            p["ruled_by"], p["ruled_on"], p["ruled_why"] = by, today, why
            n += 1
        if not n:
            continue
        bad = admin.save_companies(companies, "promote-profiles",
                                   why=why or f"landed {n} write-up(s)", by=by,
                                   force=n > 25)
        if bad:
            print(f"  REFUSED by the journal: {bad}")
            return wrote
        bad = agents.save(store, "promote-profiles", why=why, by=by)
        if bad:
            print(f"  REFUSED by the journal (store): {bad}")
            return wrote
        wrote += n
        print(f"  landed {n} (journal entry {start // CHUNK + 1})")
    if skipped:
        print(f"  left {skipped} pending: unsure answers with no paragraphs, nothing to land")
    return wrote


def _has_text(p: dict) -> bool:
    return any(isinstance(para, list) and any((s.get("text") or "").strip() for s in para
                                              if isinstance(s, dict))
               for para in (p.get("paragraphs") or []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", metavar="CATEGORY")
    ap.add_argument("--land", metavar="CATEGORY")
    ap.add_argument("--show", metavar="ID")
    ap.add_argument("--reject", action="append", default=[], metavar="ID")
    ap.add_argument("--hide", action="append", default=[], metavar="ID")
    ap.add_argument("--unhide", action="append", default=[], metavar="ID")
    ap.add_argument("--why", default="")
    ap.add_argument("--by", default=None,
                    help='who is ruling: "owner", or "agent:<label>". Required to write.')
    a = ap.parse_args()

    if (a.land or a.reject or a.hide or a.unhide) and not a.by:
        ap.error("--by is required to rule: \"owner\", or \"agent:<label>\"")

    store = agents.load()
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    names = {c["id"]: c.get("name", c["id"]) for c in seq if c.get("id")}

    if a.show:
        p = store.get(f"profile:{a.show}")
        if not p:
            print(f"no write-up proposal on file for {a.show!r}")
            return 1
        show(p, names.get(a.show, a.show))
        return 0

    if a.gate:
        gate(store, seq, a.gate)
        return 0

    if a.reject:
        for cid in a.reject:
            p = store.get(f"profile:{cid}")
            if not p:
                print(f"  no proposal for {cid!r}")
                continue
            p["status"] = "rejected"
            p["ruled_by"], p["ruled_on"] = a.by, dt.date.today().isoformat()
            p["ruled_why"] = a.why or "rejected"
        bad = agents.save(store, "profile-reject", why=a.why, by=a.by)
        print(bad or f"  rejected {len(a.reject)}; the company stays proposable")
        return 1 if bad else 0

    if a.hide or a.unhide:
        index = {c["id"]: c for c in seq if c.get("id")}
        for cid in a.hide:
            if cid in index:
                index[cid]["profile_hidden"] = True
        for cid in a.unhide:
            if cid in index:
                index[cid].pop("profile_hidden", None)
        bad = admin.save_companies(companies, "profile-hide",
                                   why=a.why or "kill switch", by=a.by)
        print(bad or f"  hidden {len(a.hide)}, unhidden {len(a.unhide)}; the page "
                     f"shows the one-line record by the next build")
        return 1 if bad else 0

    if a.land:
        read = json.loads(READ.read_text()) if READ.exists() else []
        if a.land not in read:
            print(f"  REFUSED. --gate {a.land!r} has not printed in this checkout.\n"
                  f"  The gate review is the part a person reads; landing a "
                  f"category nobody\n  has looked at is the door alone, and the "
                  f"owner asked for the door plus a look.")
            return 1
        keys = [k for k, p in _by_category(store, seq).get(a.land, [])
                if p.get("status") == "pending"]
        n = land(store, seq, keys, a.by, a.why or f"landed {a.land} after gate review")
        print(f"  {n} write-up(s) on the map. Undo a batch: python3 scripts/admin_undo.py")
        return 0

    # the funnel, re-derived every time
    rows = [p for p in store.values() if isinstance(p, dict) and p.get("kind") == "profile"]
    st = {}
    for p in rows:
        st[p.get("status", "?")] = st.get(p.get("status", "?"), 0) + 1
    on_map = sum(1 for c in seq if isinstance(c.get("profile"), dict)
                 and c["profile"].get("paragraphs"))
    hidden = sum(1 for c in seq if c.get("profile_hidden"))
    try:
        import fetch_profiles as fp
        idx = fp.index()
        fetched, readable = len(idx), sum(1 for e in idx.values() if not e.get("unread"))
    except Exception:
        fetched = readable = 0
    print(f"companies {len(seq)} · sites fetched {fetched} · readable {readable}")
    print(f"proposed {len(rows)}: " + ", ".join(f"{v} {k}" for k, v in sorted(st.items())))
    print(f"on the public page {on_map} · hidden by kill switch {hidden}")
    print(f"still saying 'not on file': {len(seq) - on_map}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
