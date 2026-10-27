#!/usr/bin/env python3
"""The daily read of new roles: is each one a SLED job, or B2B?

    python3 scripts/scope_review.py brief --out brief.json   # what needs reading
    python3 scripts/scope_review.py apply verdicts.json      # look only
    python3 scripts/scope_review.py apply verdicts.json --write --by agent:scope-review-2026-10-28
    python3 scripts/scope_review.py unclear                  # what a person should look at

WHY. Owner, 2026-10-27: "review the roles open, make sure they are just sled
jobs not b2b". Every one of the 694 openings on the board's default was read
that day (two reads, a skeptic behind each) and 308 B2B and federal roles were
ruled off. Asked about a title rule (`sled_only` on the business-first
vendors) he said "read the roles and see if that role does", and to the
daily review "yes set up the daily review": each night's new roles get the
same read, the clear B2B ones come off, and the unclear are left for him.

THE SPLIT, as read_floor.py splits it: this script is the deterministic half.
`brief` lists the openings on the board's default (quota-carrying, not placed
outside the US) that no scope ruling and no earlier review covers, each with
its company and, where the company's own board feed publishes it, the full
ad. The reading is agent work against the `rules` the brief carries. `apply`
takes the verdicts:
  B2B, FEDERAL   a ruling out of scope in scope_decisions.json, through the
                 admin's journalled door (save_decisions, under desk_lock),
                 with the reader's reason and the words it quoted
  SLED, MIXED    stay on the board
  UNCLEAR        stays on the board, and is listed by `unclear` for a person
Every verdict is kept in data/scope_reviews.json, so a role is read once.

A PERSON'S RULING IS NEVER OVERWRITTEN, either way: an opening already in
scope_decisions.json is skipped by `brief` and refused by `apply`. A B2B or
FEDERAL verdict with no quote is refused by name: a wrong B2B call takes a
real SLED job off a public board and nothing looks wrong afterwards.

`apply --write` changes the decision files only; the board is redrawn by
quick_rebuild.py --write, as for any scope ruling.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
DATA = ROOT / "data"
REVIEWS = "scope_reviews.json"
VERDICTS = ("SLED", "B2B", "MIXED", "FEDERAL", "UNCLEAR")
OFF = ("B2B", "FEDERAL")

RULES = """SLED JOBS is a public job board for SALES roles that sell to US STATE AND LOCAL
GOVERNMENT and education (SLED). The owner's rule (2026-10-27): only SLED jobs, not
B2B; roles that sell to BOTH public and private buyers stay; a role is judged by
READING it, never by a title rule.

One verdict per opening:
- SLED: sells to US state or local government buyers: cities, counties, states, K-12
  districts, public colleges and universities, police, fire, EMS and 911 agencies,
  public utilities, transit and airport authorities, courts, libraries, special
  districts. A vendor whose customers are overwhelmingly those buyers makes even an
  untitled "Account Executive" SLED.
- B2B: sells to private businesses (commercial, enterprise, mid-market, SMB, private
  hospitals and health systems, physicians and clinics, insurers, banks,
  manufacturers, energy companies, law firms), or to consumers.
- MIXED: sells to both public and private buyers in one territory.
- FEDERAL: sells to the US federal government, or is a role abroad selling to another
  country's government (both out of this board's scope).
- UNCLEAR: the ad, the title and the company do not settle it.

THE ASYMMETRIC RULE: a wrong B2B call takes a real SLED job off a public board and
nothing looks wrong afterwards. B2B and FEDERAL need a "quote": the ad's own words (or
the title's) that name the buyers. At a vendor that sells to businesses and
government alike, a generic title with no ad is UNCLEAR, never B2B.

Return [{"opening_id", "verdict", "why", "quote"}] for EVERY opening in the brief.
A second reader (a skeptic) then tries to refute each B2B, FEDERAL and SLED call;
the verdict applied is the one that survives."""


def board_default(board: dict) -> dict:
    """{opening_id: [postings]} on the board's default: quota-carrying, not placed
    outside the US - the set the Jobs tab and the home page count."""
    ops = collections.defaultdict(list)
    for p in board.get("postings") or []:
        if p.get("quota_carrying") and p.get("is_us") is not False:
            ops[p.get("opening_id") or p["id"]].append(p)
    return ops


def read_json(name: str, default):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else default


def pending(board: dict, scope: dict, reviews: dict) -> dict:
    """The openings nobody has ruled on or read yet."""
    return {k: rs for k, rs in board_default(board).items()
            if k not in scope and k not in reviews}


def ads_for(want: dict, companies: dict) -> dict:
    """{opening_id: ad text} from each company's own board feed, asking for the
    detail of the wanted titles only. A board that will not answer gives no ad,
    and no ad is said, never guessed."""
    import ats
    by_co = collections.defaultdict(set)
    for k, rs in want.items():
        by_co[rs[0]["company_id"]].add(rs[0]["title"])
    out, real, was = {}, ats._details, ats.FETCH_DETAILS
    ats.FETCH_DETAILS = True
    try:
        for cid, titles in by_co.items():
            def only(rows, read, titles=titles):
                real([r for r in rows if r.get("title") in titles], read)
                return rows
            ats._details = only
            try:
                rows = ats.fetch((companies.get(cid) or {}).get("ats") or {})
            except Exception:  # noqa: BLE001 - an unread board is no ad, not an error
                rows = []
            for r in rows:
                if r.get("title") in titles and r.get("jd"):
                    out.setdefault(f"{cid}::{r['title']}", r["jd"][:6000])
    finally:
        ats._details, ats.FETCH_DETAILS = real, was
    return out


def brief(board: dict, companies: list, scope: dict, reviews: dict, fetch: bool = True) -> dict:
    cos = {c["id"]: c for c in companies}
    want = pending(board, scope, reviews)
    ads = ads_for(want, cos) if (fetch and want) else {}
    items = []
    for k, rs in sorted(want.items()):
        p, c = rs[0], cos.get(rs[0]["company_id"]) or {}
        items.append({"opening_id": k, "title": p["title"], "company": p.get("company"),
                      "company_description": c.get("description"), "sector": c.get("sector"),
                      "places": sorted({p_.get("location") or "" for p_ in rs})[:4],
                      "url": p.get("url"), "first_seen": p.get("first_seen"),
                      "ad": ads.get(k)})
    return {"rules": RULES, "openings": items}


def plan(verdicts: list, board: dict, scope: dict, reviews: dict) -> tuple[list, list]:
    """(accepted, refused) - each accepted row says what it will do."""
    on_board = board_default(board)
    ok, bad, seen = [], [], set()
    for v in verdicts:
        k, verdict = v.get("opening_id"), str(v.get("verdict") or "").upper()
        why, quote = str(v.get("why") or "").strip(), str(v.get("quote") or "").strip()
        if not k or k in seen:
            bad.append((k, "no opening id, or the same one twice")); continue
        seen.add(k)
        if verdict not in VERDICTS:
            bad.append((k, f"verdict {verdict!r} is not one of {', '.join(VERDICTS)}")); continue
        if k in scope:
            bad.append((k, "a ruling is already on file; a ruling is never overwritten")); continue
        if k not in on_board:
            bad.append((k, "not an opening on the board's default")); continue
        if not why:
            bad.append((k, "no reason given")); continue
        if verdict in OFF and not quote:
            bad.append((k, f"{verdict} with no quote: a role comes off only on words a reader can show")); continue
        ok.append({"opening_id": k, "verdict": verdict, "why": why, "quote": quote,
                   "title": on_board[k][0]["title"], "company": on_board[k][0].get("company")})
    return ok, bad


def apply(verdicts: list, write: bool, by: str) -> int:
    import admin
    board = read_json("board.json", {})
    with admin.desk_lock():
        scope = read_json("scope_decisions.json", {})
        reviews = read_json(REVIEWS, {})
        ok, bad = plan(verdicts, board, scope, reviews)
        off = [r for r in ok if r["verdict"] in OFF]
        counts = collections.Counter(r["verdict"] for r in ok)
        print(f"read {len(ok)}: " + ", ".join(f"{n} {v}" for v, n in sorted(counts.items()))
              + f"; {len(off)} come off the board")
        for k, why in bad:
            print(f"  refused {k}: {why}")
        for r in off:
            print(f"  off  {r['verdict']:<7} {r['company']}: {r['title']}")
        if not write:
            print("(look only: --write records them)")
            return 1 if bad else 0
        today, stamp = dt.date.today().isoformat(), admin.now()
        for r in off:
            label = "B2B" if r["verdict"] == "B2B" else "out of scope (federal or another country's government)"
            scope[r["opening_id"]] = {
                "in_scope": False, "on": today, "at": stamp, "by": by,
                "why": f"{label}: {r['why']}"[:600],
                "saw": {"title": r["title"], "company": r["company"], "description": r["quote"][:400]}}
        for r in ok:
            reviews[r["opening_id"]] = {"verdict": r["verdict"], "why": r["why"][:600],
                                        "quote": r["quote"][:400] or None, "on": today, "by": by}
        why = (f"daily scope review, {today}: {len(ok)} new roles read (a reader and a skeptic); "
               f"{len(off)} B2B or federal off, per the owner's 2026-10-27 ruling")
        # force: the count was printed above and the run's summary names
        # every role that came off; BLAST is for a person's bulk click
        if off:
            refused = admin.save_decisions("scope_decisions.json", scope, "scope",
                                           why=why, by=by, force=True)
            if refused:
                print(f"refused by the journal: {refused}")
                return 2
        refused = admin.save_decisions(REVIEWS, reviews, "scope-review", why=why, by=by,
                                       force=True)
        if refused:
            print(f"refused by the journal: {refused}")
            return 2
    print("written: now python3 scripts/quick_rebuild.py --write")
    return 1 if bad else 0


def unclear() -> int:
    board = read_json("board.json", {})
    on_board = board_default(board)
    rows = [(k, v) for k, v in read_json(REVIEWS, {}).items()
            if v.get("verdict") == "UNCLEAR" and k in on_board]
    print(f"{len(rows)} role(s) on the board that no reader could call:")
    for k, v in sorted(rows):
        p = on_board[k][0]
        print(f"  {p.get('company')}: {p['title']}\n      {p.get('url')}\n      {v.get('why')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--out")
    b.add_argument("--no-ads", action="store_true", help="do not read the boards' feeds")
    a = sub.add_parser("apply")
    a.add_argument("verdicts")
    a.add_argument("--write", action="store_true")
    a.add_argument("--by", default=f"agent:scope-review-{dt.date.today().isoformat()}")
    sub.add_parser("unclear")
    args = ap.parse_args(argv)
    if args.cmd == "brief":
        out = brief(read_json("board.json", {}), read_json("companies.json", []),
                    read_json("scope_decisions.json", {}), read_json(REVIEWS, {}),
                    fetch=not args.no_ads)
        text = json.dumps(out, indent=1, ensure_ascii=False)
        if args.out:
            pathlib.Path(args.out).write_text(text)
            with_ad = sum(1 for o in out["openings"] if o["ad"])
            print(f"{len(out['openings'])} opening(s) to read ({with_ad} with their ad): {args.out}")
        else:
            print(text)
        return 0
    if args.cmd == "apply":
        if not args.by.startswith("agent:"):
            print("--by names the agent that read them (agent:...); a person rules in the admin")
            return 2
        return apply(json.loads(pathlib.Path(args.verdicts).read_text()), args.write, args.by)
    return unclear()


if __name__ == "__main__":
    raise SystemExit(main())
