#!/usr/bin/env python3
"""Fold a supplier row into the board company it already is. --write to land.

    python3 scripts/merge_supplier_twins.py                  # what would merge
    python3 scripts/merge_supplier_twins.py --write
    python3 scripts/merge_supplier_twins.py --pair firstnet=firstnet-built-with-at-t --write

WHY THEY EXIST. conference_intake matches a floor's names against the board
and the suppliers, and files a name that is neither, and that the reader did
not call govtech, as a supplier. When the company reaches the board later - by
hand, off a card, from another floor - or gains the alias that matches it
later, the supplier row stays behind carrying the floors the company was seen
on. On 2026-10-18 there were 18. Eight board companies were missing 18
exhibited-at tags that only their supplier twins carried, and three twins were
in the SLED HQ export as live suppliers (Notenetic, Intrado, Regroup).

A TWIN is a supplier row whose name or alias, by intake's own norm(), is a
board company's name or alias. That is the match intake would have made had
the company been on file first, so it is the right test, and the only one: a
shared word is not a twin.

THREE KINDS ARE NOT MERGED ON THE NAME ALONE, and are named instead:
  - a row somebody researched (`researched` true, or `govtech` true): a person
    or an agent looked at it as an organisation of its own;
  - a row whose website is on a different site from the company's, because a
    same-named business in another line of work is exactly that shape;
  - a name that matches more than one board company.
`--pair SUPPLIER_ID=COMPANY_ID` merges one of those once somebody has looked.

A MERGE: the company gains every exhibited-at tag the twin carries, written as
intake writes them; the twin leaves suppliers.json through the journal; and
its row in supplier_categories.json, which SLED HQ reads, says `merged_into`
the company - the export's own word for a supplier that is a board company.
Nothing else about the company changes: the board record's name, placement
and website were set with evidence, and a supplier row has less of each.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse

sys.path.insert(0, __import__("os").path.dirname(__file__))
import admin                                                    # noqa: E402
import build_board                                              # noqa: E402
import conference_intake as ci                                  # noqa: E402

EXPORT = "supplier_categories.json"


def site(url: str | None) -> str:
    """The site an address is on, as its last two host labels ('' for none).

    Naive about two-part suffixes (co.uk), which only ever makes two sites
    look different - the direction that sends a pair to a person."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return ".".join(host.split(".")[-2:]) if host else ""


def _names(row: dict) -> list[str]:
    return [k for k in (ci.norm(n) for n in [row.get("name") or "",
                                             *(row.get("also_known_as") or [])]) if k]


def tags_of(row: dict, issued: set) -> list[str]:
    """Every catalogue tag a row carries, in `source` or its exhibited-at note."""
    out = list(build_board._event_tags(row.get("source"), issued))
    m = re.search(r"exhibited at ([^;.\n]+)", row.get("description") or "")
    for t in (x.strip() for x in m.group(1).split(",")) if m else ():
        if t in issued and t not in out:
            out.append(t)
    return out


def twins(companies: list, suppliers: list, pairs: dict | None = None) -> list[dict]:
    """[{supplier, company, refused}] for every supplier row that is a twin.

    `refused` is None for a merge, else the reason a person must look first.
    `pairs` ({supplier_id: company_id}) are pairs somebody has looked at."""
    pairs = pairs or {}
    by_key: dict[str, list] = {}
    for c in companies:
        for k in _names(c):
            if c not in by_key.setdefault(k, []):
                by_key[k].append(c)
    by_id = {c["id"]: c for c in companies}
    out = []
    for s in suppliers:
        if s.get("id") in pairs:
            c = by_id.get(pairs[s["id"]])
            out.append({"supplier": s, "company": c,
                        "refused": None if c else f"no company {pairs[s['id']]!r} on file"})
            continue
        hits = []
        for k in _names(s):
            for c in by_key.get(k, []):
                if c not in hits:
                    hits.append(c)
        if not hits:
            continue
        c = hits[0]
        why = None
        if len(hits) > 1:
            why = "matches " + ", ".join(h["id"] for h in hits)
        elif s.get("researched") or s.get("govtech") is True:
            why = "somebody researched this row as its own organisation"
        elif s.get("website") and c.get("website") and site(s["website"]) != site(c["website"]):
            why = f"its website is on {site(s['website'])}, the company's on {site(c['website'])}"
        out.append({"supplier": s, "company": c, "refused": why})
    return out


def merge(plan: list[dict], by: str, write: bool) -> dict:
    """Land every unrefused twin. Returns what moved, or what refused."""
    cat = json.loads((admin.DATA / "conferences.json").read_text())["conferences"]
    issued = build_board.issued_tags(cat)
    go = [p for p in plan if not p["refused"]]
    companies = admin.read_companies()
    by_id = {c["id"]: c for c in companies}
    moved = []
    for p in go:
        c = by_id[p["company"]["id"]]
        have = set(build_board._event_tags(c.get("source"), issued))
        for t in tags_of(p["supplier"], issued):
            if t not in have:
                c["description"] = ci.add_event(c.get("description"), t)
                c["source"] = ci.add_source_tag(c.get("source"), t)
                have.add(t)
                moved.append((c["name"], t))
    gone = {p["supplier"]["id"]: p["company"]["id"] for p in go}
    rep = {"merged": len(go), "tags": moved, "refused": [p for p in plan if p["refused"]]}
    if not write or not go:
        return rep
    names = "; ".join(f"{p['supplier']['name']} -> {p['company']['name']}" for p in go)
    if moved:
        err = admin.validate(companies)
        if err:
            return {**rep, "error": err}
        bad = admin.save_companies(companies, "merge-supplier-twin",
                                   f"{len(moved)} exhibited-at tags from supplier twins: {names}",
                                   by=by)
        if bad:
            return {**rep, "error": bad}
    suppliers = json.loads((admin.DATA / "suppliers.json").read_text())
    keep = [s for s in suppliers if s.get("id") not in gone]
    bad = admin.save_decisions("suppliers.json", keep, "merge-supplier-twin",
                               why=f"each row is a board company already: {names}", by=by)
    if bad:
        return {**rep, "error": bad}
    path = admin.DATA / EXPORT
    if path.exists():
        cats = json.loads(path.read_text())
        for row in cats.get("suppliers") or []:
            if row.get("supplier_id") in gone:
                cid = gone[row["supplier_id"]]
                for k in list(row):
                    if k not in ("supplier_id", "name"):
                        del row[k]
                row["status"] = {"merged_into": {"kind": "company", "id": cid}}
        cats["assignments"] = [a for a in cats.get("assignments") or []
                               if a.get("supplier_id") not in gone]
        admin.write_atomic(EXPORT, cats)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--pair", action="append", default=[], metavar="SUPPLIER_ID=COMPANY_ID",
                    help="merge a pair the name rule leaves to a person, once somebody has looked")
    ap.add_argument("--by", default="agent:merge-supplier-twins")
    a = ap.parse_args()
    pairs = dict(x.split("=", 1) for x in a.pair)
    companies = admin.read_companies()
    suppliers = json.loads((admin.DATA / "suppliers.json").read_text())
    plan = twins(companies, suppliers, pairs)
    for p in plan:
        mark = "NEEDS A PERSON" if p["refused"] else "merge"
        print(f"  {mark:14} {p['supplier']['id']:38} -> {(p['company'] or {}).get('id', '?')}"
              + (f"   ({p['refused']})" if p["refused"] else ""))
    rep = merge(plan, a.by, a.write)
    for name, t in rep["tags"]:
        print(f"    + {name}: {t}")
    print(f"\n{rep['merged']} twin(s) to merge, {len(rep['tags'])} tag(s) to move, "
          f"{len(rep['refused'])} for a person")
    if rep.get("error"):
        print(f"refused: {rep['error']}", file=sys.stderr)
        return 1
    if not a.write:
        print("LOOKED ONLY. --write to land.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
