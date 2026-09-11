"""Apply a research run's FACTS to companies already on the board. --write to land it.

    python3 scripts/apply_run_facts.py stormcon [--write]
    python3 scripts/apply_run_facts.py govit    [--write]

WHAT THIS DOES NOT DO, AND WHY THAT IS THE POINT. It never adds a company.
A research run is a list of names somebody read off an expo page; sector,
category, ATS and a description need research before a row is valid, and
`validate()` would refuse a hollow one anyway. New names go to
conference_intake, which stages them as suppliers or research candidates and
lets a person rule. That is the same boundary promote_candidates holds.

THE DEFECT THIS FIXES. apply_transit_run.py matched each row and did
`if not c: continue` - a silent skip. On the transit run that was 18 of 200
and nobody saw it. On the StormCon and Gov IT runs it would be 95 of 109,
because these lists are mostly names the board has never held. A row that
went nowhere is now COUNTED AND NAMED, and the run refuses to look like it
did more than it did.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import admin                                                    # noqa: E402

# Each run's workbook says the same things under different headings, so the
# columns are named here rather than counted - a positional index is how the
# transit applier would have read "Buyer" out of the Gov IT sheet's "Buyer"
# column only by luck.
RUNS = {
    "stormcon": {
        "xlsx": "/Users/wyethwest/Downloads/SLED-stormcon-74.xlsx",
        "label": "StormCon Expo run 2026-09-10",
        "name": "Name", "website": "Website", "buyer": "Buyer",
        "sells": "Sells to government", "founded": "Founded",
        "fconf": "Founded confidence", "fsrc": "Founded source",
        "acq": "Acquisitions",
    },
    "govit": {
        "xlsx": "/Users/wyethwest/Downloads/SLED-gov-it-cyber-35.xlsx",
        "label": "Gov IT & Cyber run 2026-09-11",
        "name": "Name (as on site)", "website": "Website", "buyer": "Buyer",
        "sells": "Sells to gov", "founded": "Founded",
        "fconf": "Founded confidence", "fsrc": "Founded source",
        "acq": "Acquisitions",
    },
}

# A buyer sentence naming any of these names a NON-government buyer, so the
# sled_only flag stays off. Deal size and sales motion are deliberately absent:
# enterprise, mid-market and SMB are all shapes SLED comes in - a big city is
# enterprise SLED and a small town is SMB SLED - and an earlier cut of this
# rule counted 522 postings out on that mistake.
PRIVATE = re.compile(
    r"\b(commercial|private|enterprise customers|retail|industrial|corporate|"
    r"HOAs?|homeowners?|property (managers?|owners?)|contractors?|"
    r"distributors?|wholesalers?|developers?|banks?|credit unions?|"
    r"insurance|lenders?|consumer|golf courses?|hotels?|casinos?|"
    r"manufacturers?|OEMs?|MSPs?|businesses|SMB|media)\b", re.I)

# A BUYER SENTENCE THAT DENIES A PRIVATE BUYER IS EVIDENCE FOR sled_only, NOT
# AGAINST IT. Madison AI's reads "Public sector only; no private-sector line was
# seen" - and a bare word match found "private" there and withheld the flag on
# the strength of a sentence asserting the opposite. Same shape as every other
# absence bug in this repo: the word is present, the fact is not. So a hit
# preceded by a negation inside the same clause does not count, and a sentence
# whose every hit is negated names no private buyer at all.
NEGATED = re.compile(r"\b(no|not|never|zero|without|nothing)\b[^.;:]{0,40}$", re.I)


def names_a_private_buyer(buyer: str) -> bool:
    """True when the sentence names a buyer who is not a government."""
    hits = list(PRIVATE.finditer(buyer or ""))
    if not hits:
        return False
    return any(not NEGATED.search(buyer[:h.start()]) for h in hits)


# The cases that drove the rule, checked on import so a later tightening of
# PRIVATE cannot quietly re-break them. A wrong sled_only is invisible on the
# public board, which is why these run every time rather than in a suite
# somebody remembers to call.
for _sentence, _expect in [
    ("Public sector only; no private-sector line was seen.", False),
    ("It also serves law enforcement, K-12, higher ed and commercial clients.", True),
    ("Private sector is primary: sold to corporate IT teams and MSPs.", True),
    ("Municipal sewer, wastewater, and stormwater utilities.", False),
    ("Cities and counties; no commercial customers named anywhere.", False),
    # Enterprise/mid-market/SMB are SLED shapes, not private buyers - except
    # where the sentence says "enterprise customers", which names a buyer.
    ("Enterprise and mid-market local government agencies.", False),
]:
    if names_a_private_buyer(_sentence) is not _expect:
        raise SystemExit(f"PRIVATE rule broke on: {_sentence!r}")


def nd(s: str) -> str:
    s = re.sub(r"https?://", "", str(s or "").lower()).strip().strip("/")
    return re.sub(r"^www\.", "", s).split("/")[0]


def nm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in RUNS:
        raise SystemExit(f"usage: apply_run_facts.py {'|'.join(RUNS)} [--write]")
    key = sys.argv[1]
    spec = RUNS[key]
    write = "--write" in sys.argv
    today = dt.date.today().isoformat()

    co = admin.read_companies()
    rows = co if isinstance(co, list) else co["companies"]
    by_dom = {nd(c.get("website")): c for c in rows if c.get("website")}
    by_nm = {nm(c.get("name")): c for c in rows}
    for c in rows:
        for a in c.get("also_known_as") or []:
            by_nm.setdefault(nm(a), c)

    ws = openpyxl.load_workbook(spec["xlsx"], read_only=True)["Companies"]
    hdr = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    H = {h: i for i, h in enumerate(hdr) if h}
    for field in ("name", "website", "buyer", "sells"):
        if spec[field] not in H:
            raise SystemExit(f"{spec['xlsx']} has no {spec[field]!r} column")

    def cell(r, field):
        i = H.get(spec.get(field))
        return r[i] if i is not None else None

    touched = sled = years = acqs = none_found = 0
    unmatched: list[tuple[str, str]] = []

    for r in ws.iter_rows(min_row=2, values_only=True):
        name, site = cell(r, "name"), cell(r, "website")
        c = by_dom.get(nd(site)) or by_nm.get(nm(name))
        if not c:
            # NOT A SKIP. The run researched this company and the board does
            # not hold it; that is a finding, and it is what the summary
            # reports at the end so nobody reads this run as complete.
            unmatched.append((str(name), str(site)))
            continue
        touched += 1
        buyer, sells = cell(r, "buyer"), cell(r, "sells")
        if buyer and str(buyer).strip():
            c["buyer"] = str(buyer).strip()[:400]
            c["buyer_source"] = spec["label"]
            c["buyer_checked_on"] = today
        if sells and str(sells).strip() in ("yes", "no", "unclear"):
            c["sells_to_gov"] = str(sells).strip()
        # THE FLAG COMES FROM A STATED BUYER, never from the category, and the
        # sentence that justified it is stored beside it so the call can be
        # argued with. "unclear" never sets it: a site that names no public
        # buyer is unclear, which is not the same as government-only.
        if (str(sells).strip() == "yes" and buyer
                and not names_a_private_buyer(str(buyer))):
            c["sled_only"] = True
            c["sled_only_why"] = (f"{spec['label']}: buyer stated as "
                                  f"{str(buyer).strip()[:200]}")
            sled += 1

        founded, fconf = cell(r, "founded"), cell(r, "fconf")
        if (c.get("year_founded") in (None, "") and founded
                and str(fconf or "").lower().startswith("conf")):
            try:
                y = int(str(founded)[:4])
                if 1800 <= y <= dt.date.today().year:
                    c["year_founded"] = y
                    c["founded_source"] = str(cell(r, "fsrc") or "")[:300] or None
                    years += 1
            except (TypeError, ValueError):
                pass

        a = str(cell(r, "acq") or "").strip()
        if a.lower().startswith("none found"):
            c["acquisitions_checked_on"] = today
            c["acquisitions_none_found"] = True
            none_found += 1
        elif a and not c.get("acquired") and not c.get("parent"):
            c["acquisition_note"] = a[:400]
            c["acquisitions_checked_on"] = today
            acqs += 1

    total = touched + len(unmatched)
    print(f"{spec['label']}: {total} researched rows")
    print(f"  {touched} already on the board and updated")
    print(f"     sled_only set on {sled}, each with the sentence behind it")
    print(f"     {years} founding years (confirmed only), {acqs} acquisitions, "
          f"{none_found} checked-and-none-found")
    print(f"  {len(unmatched)} NOT on the board - nothing was written for these.")
    print(f"     They are names, not records. Stage them through "
          f"conference_intake so they land as suppliers or research "
          f"candidates and a person rules:")
    for n, s in unmatched[:8]:
        print(f"       {n[:44]:44} {nd(s)}")
    if len(unmatched) > 8:
        print(f"       ... and {len(unmatched) - 8} more")

    if not write:
        print("\n  LOOKED ONLY. --write to land it.")
        return 0

    out = rows if isinstance(co, list) else {**co, "companies": rows}
    bad = admin.save_companies(
        out, f"{key}-run-facts",
        why=(f"{spec['label']}: buyer and sells_to_gov onto {touched} companies "
             f"already on the board, sled_only on {sled} from a stated buyer, "
             f"{years} founding years, {acqs} acquisitions. "
             f"{len(unmatched)} researched names are NOT on the board and "
             f"nothing was written for them."),
        by=f"{spec['label']}, applied by Claude", force=True)
    if bad:
        print("  REFUSED:", bad)
        return 1
    print(f"\n  written through the journal; {touched} records updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
