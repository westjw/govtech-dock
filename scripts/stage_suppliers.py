#!/usr/bin/env python3
"""Move suppliers onto the board, in slices, with a tag and an author.

    python3 scripts/stage_suppliers.py                      # the triage
    python3 scripts/stage_suppliers.py --sector "K-12 Schools"
    python3 scripts/stage_suppliers.py --sector "K-12 Schools" --land --by owner

THE OWNER'S RULING (2026-09-17): "we are going to have to move all suppliers
over onto the system... if you want to sell to government, this is the place."
That changes what this board IS - not a govtech-product list, a map of
everyone who sells to government - and it is why `tags` exists: the sells
dimension is what keeps a janitorial contractor and a permitting platform
legible on one page.

"ALL SUPPLIERS" IS 1,578, NOT 5,143, AND THAT IS THE FIRST THING THIS SAYS.
Measured 2026-09-18 over the 5,143 undecided records:

    2,569  a NAME on an exhibitor list - no description, no website
    1,578  a description AND a website  -> a real card today
      500  a website, no description
      496  a description, no website

Half the file is a name somebody read off a floor. Publishing those would put
2,569 empty cards on a public board, which is the same lie as a page scan
reporting "no listings" when it could not read: it looks like coverage and is
absence wearing coverage's clothes. They are not thrown away - they stay in
suppliers.json, which is what that file is for - and they come back the day a
sweep or a person gives them a description.

VENDOR_TYPE IS THE POINT, AND IT IS READ OFF THEIR OWN WORDS. tags.tags_for
derives every tag and stores none; with no vendor_type a merged supplier
carries `tags: []`, which is exactly the outcome the tag system was built to
avoid. So each record is classified from its OWN description, on an anchored
phrase, and the matched phrase travels with the proposal - CLAUDE.md's rule
that the sentence we read belongs next to the value we produced. A record
whose description carries no anchor is `unsure`, and unsure does not land.
Nothing here guesses: an unclassifiable supplier is a supplier we have not
classified, said out loud.

vendor_type is "GovTech Product" on all 2,044 companies today, so the sells
tag is a tautology on the board as it stands. It stops being one here.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import tags as tagmod                                           # noqa: E402

SUPPLIERS = DATA / "suppliers.json"

# ANCHORED, ORDERED, AND EVERY ONE MEASURED AGAINST THE REAL CORPUS. The order
# matters because descriptions stack words: "Course materials distribution" is
# a distributor before it is anything else, and "higher education consulting
# services" is consulting before it is services. A phrase earns its place here
# only if it is what the company SELLS - "solutions", "group", "partners" and
# "inc" say nothing and are deliberately absent.
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Association / Gov / Media", (
        "association", "nonprofit", "non-profit", "foundation", "institute",
        "chamber of commerce", "publisher", "publishing", "magazine",
        "trade group", "membership organization", "society of")),
    ("Distributor / Reseller / Co-op", (
        "distributor", "distribution", "reseller", "dealer", "wholesale",
        "cooperative purchasing", "purchasing cooperative", "co-op",
        "bookstore", "supplier of", "supplies to")),
    ("Engineering / Consulting", (
        "consulting", "consultancy", "advisory", "engineering services",
        "architecture", "architects", "design services", "actuarial",
        "accounting and", "audit services", "legal services", "law firm")),
    ("Services (non-tech)", (
        "janitorial", "custodial", "landscaping", "staffing", "dining",
        "food service", "catering", "security guard", "guard services",
        "transportation services", "waste collection", "laundry",
        "facility services", "facilities management", "maintenance services",
        "print and mail", "shredding", "temporary staffing")),
    ("GovTech Product", (
        "software", "platform", "saas", "mobile app", "web application",
        "management system", "data analytics")),
    ("Equipment / Components", (
        "equipment", "hardware", "vehicles", "apparatus", "furniture",
        "pipe", "castings", "valves", "pumps", "sensors", "meters",
        "uniforms", "tools", "machinery", "components", "fixtures")),
]


def own_words(rec: dict) -> str:
    """The description with the exhibited-at tag taken off.

    conference_intake appends "- exhibited at <event>" to a description, so
    half these records read as a sentence about a trade show rather than about
    a company. Classifying on that text would file every exhibitor at one
    event as the same kind of business.
    """
    d = re.sub(r"\s*-?\s*exhibited at [^.;]*", "", rec.get("description") or "")
    return d.strip(" -,")


def classify(rec: dict) -> tuple[str | None, str]:
    """(vendor_type, the phrase it was read from). (None, "") when unsure."""
    text = own_words(rec).lower()
    if not text:
        return None, ""
    for vendor_type, phrases in RULES:
        for p in phrases:
            if p in text:
                return vendor_type, p
    return None, ""


def load() -> list[dict]:
    raw = json.loads(SUPPLIERS.read_text())
    return raw if isinstance(raw, list) else list(raw.values())


def triage(rows: list[dict]) -> dict:
    """The four populations, which is the answer to 'move all suppliers over'."""
    out = collections.defaultdict(list)
    for r in rows:
        if r.get("govtech") is not None:
            continue                      # already ruled: not a backlog
        d, w = own_words(r), (r.get("website") or "").strip()
        out["ready" if (d and w) else
            "no_website" if d else
            "no_description" if w else "name_only"].append(r)
    return dict(out)


def proposals(rows: list[dict], sector: str | None = None,
              limit: int = 0) -> tuple[list[dict], list[dict]]:
    """(what would land, what is held back and why). Nothing is written."""
    ready = triage(rows).get("ready", [])
    if sector:
        ready = [r for r in ready if r.get("sector") == sector]
    on_board = {c["id"] for c in admin.read_companies() if c.get("id")}
    vocab = tagmod.vocabulary()
    land, held = [], []
    for r in ready:
        if r.get("id") in on_board:
            held.append({**r, "why": "already on the board"})
            continue
        vt, phrase = classify(r)
        if not vt:
            held.append({**r, "why": "no anchored phrase in their own words"})
            continue
        rec = {k: v for k, v in r.items() if k != "govtech"}
        rec["vendor_type"] = vt
        rec["description"] = own_words(r)
        # THE EVENT MOVES TO `source`, WHERE THE CONFERENCES TAB COUNTS IT.
        # build_board._event_tags reads `source`; intake wrote the tag into
        # the description. A supplier landed with the tag only in its prose
        # would exhibit at a show the tab says we never mined.
        events = re.findall(r"exhibited at ([^.;]+)", r.get("description") or "")
        if events:
            parts = [e.strip() for ev in events for e in ev.split(",")
                     if re.search(r"(19|20)\d{2}", e)]
            old = (r.get("source") or "").strip()
            merged = [p for p in parts if p not in old]
            if merged:
                rec["source"] = "; ".join(([old] if old else []) + merged)
        rec["tags_would_be"] = tagmod.tags_for(rec, vocab)
        rec["read_from"] = phrase
        land.append(rec)
        if limit and len(land) >= limit:
            break
    return land, held


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sector")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--land", action="store_true")
    ap.add_argument("--by", help='"owner", or "agent:<label>"')
    ap.add_argument("--show", type=int, default=12)
    a = ap.parse_args()

    rows = load()
    pops = triage(rows)
    if not a.sector and not a.land:
        n = sum(len(v) for v in pops.values())
        print(f"\n{n} supplier(s) nobody has ruled on.\n")
        for k, label in (("name_only", "a NAME on an exhibitor list - not a card"),
                         ("ready", "a description AND a website - a real card"),
                         ("no_description", "a website, no description"),
                         ("no_website", "a description, no website")):
            print(f"  {len(pops.get(k, [])):5}  {label}")
        print("\n  'all suppliers' is the second line, not the first. The "
              "others stay in suppliers.json\n  until a sweep or a person "
              "gives them what a card needs.\n")
        by_sec = collections.Counter(r.get("sector") for r in pops.get("ready", []))
        for sec, c in by_sec.most_common():
            print(f"    {c:5}  {sec}")
        biggest = by_sec.most_common(1)[0][0] if by_sec else 'General Gov'
        print(f"\n  Read one:  python3 scripts/stage_suppliers.py --sector "
              f"{biggest!r}")
        return 0

    land, held = proposals(rows, a.sector, a.limit)
    if not land and not held:
        print(f"nothing ready in {a.sector!r}")
        return 0
    print(f"\n{len(land)} would land"
          + (f" in {a.sector}" if a.sector else "")
          + f", {len(held)} held back\n")
    for r in land[:a.show]:
        print(f"  {r['name'][:32]:34} {r['vendor_type'][:26]:28} "
              f"{','.join(r['tags_would_be']) or '(no tag)'}")
        print(f"      read from {r['read_from']!r}: {r['description'][:64]}")
    if len(land) > a.show:
        print(f"  ... and {len(land) - a.show} more")
    why = collections.Counter(h["why"] for h in held)
    for w, c in why.most_common():
        print(f"  HELD {c:5}  {w}")
    untagged = [r for r in land if not r["tags_would_be"]]
    if untagged:
        print(f"\n  {len(untagged)} would land with NO tag; that is the thing "
              f"this exists to prevent.")

    if not a.land:
        print("\ndry run: nothing written. Add --land --by owner to move them.")
        return 0
    if not a.by:
        print("\n--by is required to land: \"owner\", or \"agent:<label>\". The "
              "journal is what\nadmin_undo reads and what says whose judgment "
              "this was.")
        return 1
    companies = admin.read_companies()
    for r in land:
        companies.append({k: v for k, v in r.items()
                          if k not in ("tags_would_be", "read_from")})
    bad = admin.save_companies(
        companies, "stage-suppliers",
        why=f"moved {len(land)} supplier(s)"
            + (f" in {a.sector}" if a.sector else "") + " onto the board",
        by=a.by, force=len(land) > 25)
    if bad:
        print(f"REFUSED: {bad}", file=sys.stderr)
        return 1
    print(f"\nmoved {len(land)} onto the board, journalled as ONE entry.\n"
          f"Undo with:  python3 scripts/admin_undo.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
