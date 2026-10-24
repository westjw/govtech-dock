#!/usr/bin/env python3
"""Fold a supplier into the supplier it is, and take a government body off the
supplier list. Reads data/supplier_record_rulings.json; --write to land.

    python3 scripts/merge_supplier_records.py                 # what would change
    python3 scripts/merge_supplier_records.py --write --by owner

THE OWNER'S RULING, 2026-10-23: "yes" to "merge the duplicates and pull the
agencies". 62 websites were shared by 134 published suppliers in the export
SLED HQ reads, and HQ's claim flow keys on a website's domain, so two records
of one company were two claimable pages. Each group was read by an agent off
the names, descriptions, shows and the supplier's own homepage text, and
challenged by a skeptic told to refute every merge and every pull; what
survived is in the rulings file, with its reason. merge_supplier_twins folds
a supplier into the BOARD company it is; this folds one into another
SUPPLIER, and the two never overlap.

A MERGE NEVER LOSES RESEARCH (CLAUDE.md's rule for companies, and the
review of 2026-10-23 found the first version breaking it: the survivor was
often the emptier row, and six card-ready groups lost their only website and
description). The survivor gains every show the merged row was seen at
(written as intake writes them, so the export places it by all of them), the
merged row's name and aliases as `also_known_as` (so the next intake, the
floor reader and the card door all match it instead of filing the name
again), and every field it lacks that the twin has - the website, the words
of the description, the location, the founding year, the govtech, research
and vendor-type stamps - and a `merged_from` note; the merged row
leaves suppliers.json through the journal, and its row in the export says
`merged_into` the survivor, which supplier_categories carries forward.

NOT DUPLICATES, and never merged here: brands or divisions of one parent on
the parent's site (Flygt and Sanitaire on xylem.com, Sodexo Campus), and
different firms sharing a host. A wrong merge hides a real company from HQ,
so the rulings file lists only what both readers let stand.

A PULL: a government body filed as a supplier - a department, a federal
agency, a city utility, a school district - BUYS from suppliers. It stays in
suppliers.json, so intake still recognises its name, with `not_a_supplier`
saying why; the export calls it not_a_company and HQ stores nothing for it.
A public body that SELLS to government (a university's degree programme,
Connecticut Correctional Enterprises) is a supplier and is not pulled.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse

sys.path.insert(0, __import__("os").path.dirname(__file__))
import admin                                                    # noqa: E402
import conference_intake as ci                                  # noqa: E402
import journal                                                  # noqa: E402
import supplier_categories as SC                                # noqa: E402

RULINGS = "supplier_record_rulings.json"
EXPORT = "supplier_categories.json"


def _host(url: str | None) -> str:
    """The site an address opens, as the export reads it (a wrapper opened)."""
    u = SC.unwrap(url) or url or ""
    h = (urllib.parse.urlsplit(SC.web_address(u)).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def plan(rulings: dict, suppliers: list, ident: dict) -> tuple[list, list, list]:
    """(merges, pulls, refusals). Every id must be a supplier on file; a
    survivor is never itself merged; an id is merged or pulled once; the
    survivor and what folds into it share a website, which is the evidence
    the reading started from."""
    by_id = {s["id"]: s for s in suppliers}
    site = lambda sid: _host((ident.get(sid) or {}).get("url")
                             or (ident.get(sid) or {}).get("website_was")
                             or (by_id.get(sid) or {}).get("website"))
    merges, pulls, refused, used = [], [], [], set()
    for m in rulings.get("merge") or []:
        into, ids, why = m.get("into"), list(m.get("ids") or []), (m.get("why") or "").strip()
        bad = (None if into in by_id else f"no supplier {into!r} on file")
        bad = bad or next((f"no supplier {i!r} on file" for i in ids if i not in by_id), None)
        bad = bad or ("no reason given" if len(why) < 15 else None)
        bad = bad or next((f"{i!r} is ruled twice" for i in [into, *ids] if i in used), None)
        bad = bad or next((f"{i!r} is not on {site(into) or 'the survivor’s site'} but {site(i) or 'no site'}"
                           for i in ids if site(i) != site(into)), None)
        if bad:
            refused.append({"into": into, "ids": ids, "why": bad})
            continue
        used.update([into, *ids])
        merges.append({"into": into, "ids": ids, "why": why})
    for p in rulings.get("pull") or []:
        sid, why = p.get("id"), (p.get("why") or "").strip()
        bad = (f"no supplier {sid!r} on file" if sid not in by_id
               else "no reason given" if len(why) < 15
               else f"{sid!r} is ruled twice" if sid in used else None)
        if bad:
            refused.append({"id": sid, "why": bad})
            continue
        used.add(sid)
        pulls.append({"id": sid, "why": why})
    return merges, pulls, refused


INHERITED = ("website", "location", "year_founded", "govtech", "researched", "vendor_type")


def _what(desc: str | None) -> str:
    """The words of a description before its "exhibited at" list."""
    d = desc or ""
    m = re.search(r"\s*-?\s*exhibited at [^;.\n]+", d)
    return (d[:m.start()] if m else d).strip(" -")


def apply(merges: list, pulls: list, suppliers: list) -> tuple[list, list]:
    """(suppliers after, moved tags as (survivor, tag)). Pure: no write."""
    rows = [dict(s) for s in suppliers]
    by_id = {s["id"]: s for s in rows}
    gone, moved = set(), []
    for m in merges:
        s = by_id[m["into"]]
        akas = list(s.get("also_known_as") or [])
        for i in m["ids"]:
            t = by_id[i]
            for k in INHERITED:
                if s.get(k) in (None, "") and t.get(k) not in (None, ""):
                    s[k] = t[k]
            if not _what(s.get("description")) and _what(t.get("description")):
                tags = SC.tags_of(s)
                s["description"] = _what(t["description"])
                for tag in tags:
                    s["description"] = ci.add_event(s["description"], tag)
            for tag in SC.tags_of(t):
                if tag not in SC.tags_of(s):
                    s["description"] = ci.add_event(s.get("description"), tag)
                    s["source"] = ci.add_source_tag(s.get("source"), tag)
                    moved.append((m["into"], tag))
            for n in [t.get("name"), *(t.get("also_known_as") or [])]:
                if n and n != s.get("name") and n not in akas:
                    akas.append(n)
            s.setdefault("merged_from", []).append({"id": i, "name": t.get("name"), "why": m["why"]})
            gone.add(i)
        if akas:
            s["also_known_as"] = akas
    for p in pulls:
        by_id[p["id"]]["not_a_supplier"] = p["why"]
    return [s for s in rows if s["id"] not in gone], moved


def mark_export(merges: list, pulls: list) -> None:
    """The export's own word for each, before the rebuild carries them."""
    path = admin.DATA / EXPORT
    if not path.exists():
        return
    cats = json.loads(path.read_text())
    into = {i: m["into"] for m in merges for i in m["ids"]}
    pulled = {p["id"] for p in pulls}
    for row in cats.get("suppliers") or []:
        sid = row.get("supplier_id")
        if sid in into or sid in pulled:
            for k in list(row):
                if k not in ("supplier_id", "name"):
                    del row[k]
            row["status"] = ({"merged_into": {"kind": "supplier", "id": into[sid]}}
                             if sid in into else "not_a_company")
    cats["assignments"] = [a for a in cats.get("assignments") or []
                           if a.get("supplier_id") not in into and a.get("supplier_id") not in pulled]
    admin.write_atomic(EXPORT, cats)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--by", default="owner")
    a = ap.parse_args()
    rulings = admin.read(RULINGS, {})
    suppliers = admin.read("suppliers.json", [])
    ident = (admin.read("supplier_identity.json", {}) or {}).get("rows") or {}
    merges, pulls, refused = plan(rulings, suppliers, ident)
    for m in merges:
        print(f"  merge  {', '.join(m['ids'])} -> {m['into']}")
    for p in pulls:
        print(f"  pull   {p['id']}   ({p['why'][:80]})")
    for r in refused:
        print(f"  REFUSED {r}")
    after, moved = apply(merges, pulls, suppliers)
    n = sum(len(m["ids"]) for m in merges)
    print(f"\n{n} supplier(s) folded into {len(merges)}, {len(moved)} show(s) moved, "
          f"{len(pulls)} government bod(ies) pulled, {len(refused)} refused")
    if refused:
        return 1
    if not a.write:
        print("LOOKED ONLY. --write to land.")
        return 0
    # THE COUNT IS PRINTED ABOVE, THEN THE WRITE IS FORCED: journal.py asks a
    # caller over its blast limit to have shown a person the count first.
    changed = n + len(merges) + len(pulls)
    bad = admin.save_decisions(
        "suppliers.json", after, "merge-supplier-records",
        why=(f"owner, 2026-10-23: merge the duplicates and pull the agencies. {n} folded into "
             f"{len(merges)} survivors, {len(pulls)} government bodies marked not_a_supplier "
             f"(rulings in data/{RULINGS})"),
        by=a.by, force=changed > journal.BLAST)
    if bad:
        print(f"refused: {bad}", file=sys.stderr)
        return 1
    mark_export(merges, pulls)
    print(f"wrote data/suppliers.json and marked data/{EXPORT}; rebuild with "
          f"supplier_categories.py --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
