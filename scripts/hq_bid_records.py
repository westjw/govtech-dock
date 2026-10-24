#!/usr/bin/env python3
"""Read who took part in which solicitation, as SLED HQ's team read it off a
government's own document, and propose co-bidders as competitors for a person
to rule on.

    python3 scripts/hq_bid_records.py            # read the feed and report; nothing written
    python3 scripts/hq_bid_records.py --write    # data/hq_bid_records.json + proposals

THE OWNER'S RULING, 2026-10-23: "yes" to "SLED JOBS could turn co-bidders into
competitor suggestions you rule on". HQ publishes the feed
(docs/feeds/sledjobs-bid-records.md in SLED HQ, v1): one record per company's
part on one government document - a pre-bid sign-in, a bid tabulation, a
score sheet, an award - and only readings a person on HQ's team confirmed by
opening the document. The feed says who took part, never who competes with
whom; that judgement is a person's here.

WHAT AN ERROR MEANS, as for HQ's other feeds (hq_jobs.read_feed): any answer
but 200 keeps the records on file. A 200 is the whole list, so a record gone
from it is a reading HQ no longer stands behind (rejected, voided, its place
withheld), and a suggestion built only on it is withdrawn. A list nobody has
read for STALE_DAYS proposes nothing new.

A RECORD IS KEPT ONLY AS SENT, from an allow-list of the contract's twelve
fields, and refused one at a time with its reason: a malformed id, place,
address, role, number or date, a name carrying an email address or a phone
number, an id listed twice. Nothing about a person is in the feed by HQ's
contract, and none is kept here.

HQ'S company_id IS A MATCH BY NAME, made against HQ's copy of
data/companies.json, and the contract says to check it. A record links to a
board company only when that company is on file now AND its name or an alias
folds to the record's company name; otherwise it is kept, unlinked, with why.

A SUGGESTION is a `cobid` proposal (agents.py) for one PAIR of board
companies that both BID on one solicitation - submitted, finalist,
recommended or awarded. Attending a pre-bid meeting is interest, not a bid
(HQ's words), so a sign-in alone pairs nobody. A pair already listing each
other is not proposed, and a pair a person ruled on is never asked again.
Accepting writes each onto the other's competitors, with the solicitation as
the reason and the government's document as the source
(proposal_rulings._accept_cobid).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import brand  # noqa: E402
import hq_jobs  # noqa: E402

FEED = f"https://hq.{brand.DOMAIN}/api/feed/bid-records"
OUT = DATA / "hq_bid_records.json"
STALE_DAYS = 7
ROLES = ("attended_prebid", "submitted", "finalist", "recommended", "awarded")
BIDDERS = ("submitted", "finalist", "recommended", "awarded")
FIELDS = ("id", "government", "document_url", "solicitation", "company", "printed_as",
          "company_id", "supplier_id", "role", "amount", "score", "decided_on")
BY = "hq:bid-records"
ID = re.compile(r"[0-9a-f]{64}")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")
EMAIL = re.compile(r"[\w.+-]+\s*(?:@|\[\s*at\s*\]|\(\s*at\s*\))\s*[\w-]+\.[A-Za-z]{2,}", re.I)
LEGAL = re.compile(r"\s+(incorporated|inc|llc|l l c|ltd|limited|corporation|corp|co|company|"
                   r"lp|llp|plc|pbc|pc|pllc|gmbh|sa|ag|nv|bv)$")


def fold(name: str) -> str:
    """A company name for an EQUALITY test: case, accents, punctuation and a
    legal suffix ("Inc.") ignored - the fold HQ's contract describes."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    while True:
        t = LEGAL.sub("", s).strip()
        if t == s:
            return s
        s = t


def _str_or_none(v) -> bool:
    return v is None or (isinstance(v, str) and len(v) <= 300)


def _number(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def refuse_reason(row, today: dt.date, seen: set) -> str | None:
    if not isinstance(row, dict):
        return "not an object"
    if not isinstance(row.get("id"), str) or not ID.fullmatch(row["id"]):
        return "id is not 64 lower-case hex characters"
    if row["id"] in seen:
        return "listed twice"
    g = row.get("government")
    if not isinstance(g, dict) or g.get("kind") not in ("city", "county") \
            or not isinstance(g.get("slug"), str) or not SLUG.fullmatch(g["slug"]) \
            or not isinstance(g.get("name"), str) or not g["name"].strip():
        return "government is not a city or county with a slug and a name"
    url = row.get("document_url")
    if not isinstance(url, str) or not re.match(r"https?://[^\s/]+", url):
        return "document_url is not an http(s) address"
    s = row.get("solicitation")
    if not isinstance(s, dict) or not _str_or_none(s.get("number")) \
            or not _str_or_none(s.get("title")) \
            or not (s.get("year") is None or (isinstance(s.get("year"), int)
                                             and not isinstance(s.get("year"), bool))):
        return "solicitation is not {number, title, year}"
    company = row.get("company")
    if not isinstance(company, str) or not company.strip() or len(company) > 300:
        return "company is not a name"
    if not _str_or_none(row.get("printed_as")):
        return "printed_as is not a name or null"
    import supplier_categories
    for v in (company, row.get("printed_as"), s.get("number"), s.get("title"), g["name"]):
        if v and (EMAIL.search(v) or supplier_categories.phone_spans(v)):
            return "a name or a title carries an email address or a phone number"
    if EMAIL.search(url) or "@" in url:
        return "document_url carries an address"
    for k in ("company_id", "supplier_id"):
        if not (row.get(k) is None or (isinstance(row[k], str) and SLUG.fullmatch(row[k]))):
            return f"{k} is not an id or null"
    if row.get("role") not in ROLES:
        return "role is not one of the contract's five"
    if not _number(row.get("amount")) or not _number(row.get("score")):
        return "amount or score is not a number or null"
    day = row.get("decided_on")
    if not isinstance(day, str) or not hq_jobs.DATE.match(day):
        return "decided_on is not YYYY-MM-DD"
    try:
        when = dt.date.fromisoformat(day)
    except ValueError:
        return "decided_on is not YYYY-MM-DD"
    if when > today:
        return "decided_on is in the future"
    return None


def link(row: dict, companies: dict) -> tuple[str | None, str | None]:
    """(board company id, why not). HQ's match is checked, never taken."""
    cid = row.get("company_id")
    if not cid:
        return None, "HQ matched no company"
    c = companies.get(cid)
    if not c:
        return None, f"{cid} is not on this board"
    names = {fold(n) for n in [c.get("name"), *(c.get("also_known_as") or [])] if n}
    if fold(row["company"]) not in names:
        return None, f"the record names {row['company']!r}, which is not {c.get('name')!r}"
    return cid, None


def sort_feed(body: dict, companies: dict, today: dt.date) -> tuple[list, list]:
    took, refused, seen = [], [], set()
    for row in body.get("records") or []:
        why = refuse_reason(row, today, seen)
        if why:
            # ONLY A WELL-FORMED ID IS KEPT OR PRINTED: a refused row's id is
            # whatever HQ sent, and the file and the Actions log are public
            rid = row.get("id") if isinstance(row, dict) else None
            refused.append({"id": rid if isinstance(rid, str) and ID.fullmatch(rid) else None,
                            "why": why})
            continue
        seen.add(row["id"])
        rec = {k: row.get(k) for k in FIELDS}
        rec["government"] = {k: row["government"].get(k) for k in ("kind", "slug", "name")}
        rec["solicitation"] = {k: row["solicitation"].get(k) for k in ("number", "title", "year")}
        rec["board_id"], why_not = link(row, companies)
        if why_not:
            rec["unlinked"] = why_not
        took.append(rec)
    took.sort(key=lambda r: (r["government"]["slug"], r["document_url"], r["company"]))
    return took, refused


def fresh(doc: dict | None, now: dt.datetime | None = None) -> bool:
    if not doc or not doc.get("read_at"):
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        read = dt.datetime.fromisoformat(doc["read_at"])
    except ValueError:
        return False
    return read.tzinfo is not None and now - read <= dt.timedelta(days=STALE_DAYS)


def solicitation_key(r: dict) -> tuple:
    """One solicitation: its government and the number the document prints,
    or the document itself when it prints none (HQ's own grouping) - and the
    year and title wherever printed, so "Bid 1" (Mowing, 2023) and "Bid 1"
    (Fleet Fuel, 2025), or two awards on one memo, are never one solicitation.
    Splitting too finely misses a pair; joining too coarsely invents one."""
    s = r["solicitation"]
    n = (s.get("number") or "").strip()
    return (r["government"]["slug"], fold(n) if n else "doc:" + r["document_url"],
            s.get("year"), fold(s.get("title") or ""))


def label(r: dict) -> str:
    s = r["solicitation"]
    what = " ".join(x for x in (s.get("number"), f"({s['title']})" if s.get("title") else None) if x)
    return (f"{what or 'a solicitation'} for {r['government']['name']}"
            + (f", {s['year']}" if s.get("year") else ""))


ROLE_RANK = {r: i for i, r in enumerate(BIDDERS)}


def pairs_of(records: list, key: str = "board_id") -> dict:
    """{(a, b): [solicitation]} for every pair of companies that both bid on
    one solicitation, by our board link (or by HQ's own company_id, to tell
    why a pair went). Each solicitation names, per company, the document(s)
    that show its part, and the one that shows both when there is one."""
    by_sol: dict = {}
    for r in records:
        if r.get(key) and r["role"] in BIDDERS:
            by_sol.setdefault(solicitation_key(r), []).append(r)
    pairs: dict = {}
    for _key, rows in by_sol.items():
        ids = sorted({r[key] for r in rows})
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                mine = {x: [r for r in rows if r[key] == x] for x in (a, b)}
                docs = {x: sorted({r["document_url"] for r in mine[x]}) for x in (a, b)}
                both = sorted(set(docs[a]) & set(docs[b]))
                # the furthest a company got on it: awarded over submitted
                top = {x: max(mine[x], key=lambda r: ROLE_RANK[r["role"]]) for x in (a, b)}
                pairs.setdefault((a, b), []).append({
                    "solicitation": label(top[a]),
                    "document_url": both[0] if both else None,
                    "documents": {x: docs[x] for x in (a, b)},
                    "decided_on": max(r["decided_on"] for x in (a, b) for r in mine[x]),
                    "roles": {x: top[x]["role"] for x in (a, b)},
                    # the name as HQ's person confirmed it and as the document
                    # prints it: HQ's id is a name match, and this is what the
                    # person ruling checks the match against
                    "as_named": {x: {"company": top[x]["company"], "printed_as": top[x]["printed_as"]}
                                 for x in (a, b)},
                    "records": sorted({r["id"] for x in (a, b) for r in mine[x]})})
    return pairs


def listed(companies: dict, a: str, b: str) -> bool:
    has = lambda x, y: any((e.get("id") if isinstance(e, dict) else e) == y
                           for e in (companies[x].get("competitors") or []))
    return has(a, b) and has(b, a)


def propose(records: list, companies: dict) -> list[dict]:
    """One cobid proposal per pair of board companies that both bid on a
    solicitation, unless each already lists the other."""
    out = []
    for (a, b), sols in sorted(pairs_of(records).items()):
        ca, cb = companies[a], companies[b]
        if listed(companies, a, b):
            continue
        sols.sort(key=lambda s: s["decided_on"], reverse=True)
        out.append({
            "kind": "cobid", "key": f"cobid:{a}:{b}", "id": a,
            "name": f"{ca['name']} and {cb['name']}",
            "sector": ca.get("sector"), "category": ca.get("category"),
            "confidence": "medium",
            "why": (f"Both bid on {len(sols)} solicitation{'s' if len(sols) != 1 else ''}, "
                    f"as read by a person on SLED HQ's team: {sols[0]['solicitation']}"
                    + (f" and {len(sols) - 1} more" if len(sols) > 1 else "") + "."),
            "evidence": sols[0]["document_url"] or sols[0]["documents"][a][0],
            "cobid": {"pair": [a, b], "names": {a: ca["name"], b: cb["name"]},
                      "solicitations": sols[:12]},
        })
    return out


GONE_FROM_HQ = ("no record in SLED HQ's feed supports it any more: rejected, voided "
                "or withheld there")
UNLINKED = "a company in it no longer matches a company on this board by name"
LISTED = "they already list each other on the board"


def settle(doc: dict, companies: dict, store: dict) -> tuple[list, dict]:
    """(proposals to ingest, {pending key: why it is withdrawn}). Only a good,
    fresh read proposes or withdraws. A row whose evidence is unchanged is left
    alone, so a quiet night writes nothing; a WITHDRAWN row is asked again
    when its records come back. A read that refused more rows than a real feed
    explains (a format change on HQ's side) withdraws nothing."""
    if not fresh(doc):
        return [], {}
    records = doc.get("records") or []
    props = propose(records, companies)
    new = [p for p in props
           if (store.get(p["key"]) or {}).get("status") not in ("accepted", "rejected")
           and ((store.get(p["key"]) or {}).get("status") == "withdrawn"
                or (store.get(p["key"]) or {}).get("cobid") != p["cobid"])]
    n_refused = len(doc.get("refused") or [])
    if n_refused > max(3, (len(records) + n_refused) // 10):
        return new, {}
    ours = pairs_of(records)
    hqs = pairs_of(records, "company_id")
    gone = {}
    for k, v in store.items():
        if not (isinstance(v, dict) and v.get("kind") == "cobid" and v.get("status") == "pending"):
            continue
        pair = tuple((v.get("cobid") or {}).get("pair") or ())
        if pair in ours:
            if all(x in companies for x in pair) and listed(companies, *pair):
                gone[k] = LISTED
        else:
            gone[k] = UNLINKED if pair in hqs else GONE_FROM_HQ
    return new, gone


def withdraw(store: dict, gone: dict) -> None:
    today = dt.date.today().isoformat()
    for k, why in gone.items():
        store[k]["status"] = "withdrawn"
        store[k]["ruled_by"], store[k]["ruled_on"] = BY, today
        store[k]["ruled_why"] = why


def main(argv: list | None = None, get=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("SLEDJOBS_FEED_TOKEN", "").strip()
    on_file = json.loads(OUT.read_text()) if OUT.exists() else None
    kept = len((on_file or {}).get("records") or [])
    if not token:
        print(f"SLED HQ bid-records feed not configured (no SLEDJOBS_FEED_TOKEN); "
              f"{kept} record(s) on file kept")
        return 0
    status, body, note = hq_jobs.read_feed(token, get, url=FEED, key="records")
    if body is None:
        print(f"SLED HQ bid-records feed {note}; {kept} record(s) on file kept "
              f"- an error is never 'nobody bid on anything'")
        return 0
    companies = {c["id"]: c for c in json.loads((DATA / "companies.json").read_text())}
    took, refused = sort_feed(body, companies, dt.datetime.now(dt.timezone.utc).date())
    linked = sum(1 for r in took if r.get("board_id"))
    print(f"SLED HQ bid-records feed answered 200: {len(took)} record(s), {linked} on a "
          f"board company, {len(refused)} refused")
    for r in refused[:20]:
        print(f"  refused {r['id'] or '(a row with no usable id)'}: {r['why']}")
    doc = {"note": "Who took part in which solicitation, as SLED HQ's team read it off the "
                   "government's own document and its feed sent it, less the refused. Written "
                   "by scripts/hq_bid_records.py; co-bidders become cobid proposals.",
           "read_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "generated_at": body.get("generated_at"), "records": took, "refused": refused}
    import agents
    store = agents.load()
    new, gone = settle(doc, companies, store)
    print(f"  {len(new)} co-bidder suggestion(s) to ask, {len(gone)} to withdraw")
    if not a.write:
        return 0
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    if gone:
        import journal
        withdraw(store, gone)
        # THE COUNT IS PRINTED ABOVE, THEN THE WRITE IS FORCED, as ingest
        # does: one voided bid tab withdraws every pair on it at once, and a
        # refusal here stopped the nightly run.
        bad = agents.save(store, "cobid-withdraw", why=f"{len(gone)} co-bidder suggestion(s) "
                          f"no longer supported", by=BY, force=len(gone) > journal.BLAST)
        if bad:
            # said, never fatal: the board build after this must still run
            print(f"  refused: {bad}", file=sys.stderr)
            return 0
    if new:
        rep = agents.ingest("cobid", new, model=BY)
        print(f"  ingested {rep['kept']}, refused {len(rep['refused'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
