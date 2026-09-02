#!/usr/bin/env python3
"""Sort staged exhibitor names into "worth researching" and "supplier".

    python3 scripts/classify_exhibitors.py --measure       # accuracy, first
    python3 scripts/classify_exhibitors.py --apply         # look only
    python3 scripts/classify_exhibitors.py --apply --write

WHY THIS EXISTS. sweep_exhibitors stages names off a conference floor and
conference_intake routes each one: a name already on file gets the event added
to its page, a name flagged `is_govtech` becomes a CANDIDATE for research, and
everything else becomes a supplier. The sweeper cannot set that flag - it read
a name off a page and nothing more - so all 667 new names from the national
sweep would land as suppliers. suppliers.json already holds 4,785 records with
2,009 undecided, and burying a real vendor in that pile is how a warm door
gets lost.

WHAT THE FLAG ACTUALLY MEANS HERE, and why setting it is not a claim. A
candidate is not a company: promote_candidates.py researches it and a person
rules before anything reaches the board. So `is_govtech: true` here means
"worth a person's time", not "this is a govtech company". That is a much
weaker claim and it is one a name can support.

MEASURED, NOT ASSERTED. This project has 2,058 companies marked govtech and
2,776 suppliers explicitly stamped false - nearly five thousand labelled
examples. So the classifier is scored against them before it is trusted with
anything, and --measure prints that score. A rule nobody measured is a guess
with formatting.

WHICH WAY IT ERRS, DELIBERATELY. Toward supplier. A vendor wrongly filed as a
supplier stays visible, keeps its record, and can be promoted the moment
anyone looks - the supplier file is a queue, not a bin. A supplier wrongly
sent to candidates costs a person a research pass on a catering firm. The
asymmetric error rule points the other way from usual here, because neither
outcome deletes anything: nothing here can produce a false "no jobs".
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# WHAT A PRODUCT VENDOR CALLS ITSELF. Not proof - "Data" appears in plenty of
# staffing firms - which is why the whole thing is scored before use.
TECH = re.compile(
    r"\b(software|technolog|systems?|solutions?|platform|digital|cloud|data|"
    r"analytics?|cyber|informatics|geospatial|gis|saas|app|apps|mobile|web|"
    r"network|telecom|wireless|sensor|iot|ai|automation|robotic|drone|"
    r"intelligence|dashboard|portal|records?|permitting|licensing|"
    r"dispatch|cad|erp|crm|payments?|billing|utility|metering|fleet|"
    r"asset|inspection|compliance|e-?gov|govtech|civic|municipal)\b", re.I)

# WHAT IS PLAINLY NOT ONE. These are the trades that stand at every floor:
# food, furniture, uniforms, insurance, banks, builders, staffing.
NOT_TECH = re.compile(
    r"\b(staffing|recruit|temp|personnel|insurance|assurance|underwrit|"
    r"bank|credit union|capital|financial group|wealth|investment|"
    r"bond|actuar|law|legal|attorney|counsel|llp|cpa|accounting|audit|"
    r"construction|contractor|builders?|paving|concrete|asphalt|roofing|"
    r"plumbing|electric(al)? (co|contract)|hvac|landscap|nursery|"
    r"uniform|apparel|clothing|footwear|catering|food|beverage|coffee|"
    r"furniture|seating|playground|turf|fencing|signage|awning|"
    r"pest|janitor|cleaning|laundry|waste hauling|towing|"
    r"university|college|school district|academy|institute of|"
    r"association|society|foundation|council|coalition|chamber of|"
    r"hotel|resort|convention|travel|tourism|airlines?)\b", re.I)


# THE FLOOR PREDICTS BETTER THAN THE NAME, and both were measured before
# either was used. Across 7,694 exhibitor rows this board has already ruled
# on, 20% turned out to be govtech - but the spread by event is enormous:
# ICMA and EDUCAUSE ran 46%, NACo 40%, GFOA 33%, while Animal Care Expo ran
# 3% and SNA 5%. A name at ICMA is fifteen times more likely to be a vendor
# than the same name at an animal shelter expo.
#
# So the rule is a union, not an AND: a tech word in the name, OR a floor
# that has historically produced vendors. Neither is strong enough alone to
# CALL something govtech - and neither has to be, because this flag routes a
# name to a research queue that a person then rules on. It is a reading
# order, not a verdict.
RICH_FLOOR = 0.28


def floor_rates() -> dict:
    """Each event's measured govtech share, from what the board already holds."""
    import collections
    got = collections.defaultdict(lambda: [0, 0])
    def events(text):
        return re.findall(
            r"(?:conference sweep:\s*)?([A-Z][A-Za-z0-9&.\- ]{2,34}\s20\d\d)",
            str(text or ""))
    comps = json.loads((DATA / "companies.json").read_text())
    sup = json.loads((DATA / "suppliers.json").read_text())
    sup = sup if isinstance(sup, list) else sup.get("suppliers", [])
    for c in comps:
        for e in set(events(c.get("source")) + events(c.get("description"))):
            got[e.strip()][0] += 1
    for s in sup:
        for e in set(events(s.get("description")) + events(s.get("source"))):
            got[e.strip()][1] += 1
    # Only events with enough history to mean anything. A floor with four
    # rows on it has no rate, it has an anecdote.
    return {e: g / (g + b) for e, (g, b) in got.items() if g + b >= 12}


def score(name: str, floor: float | None = None) -> tuple[bool, str]:
    """True when a name is worth a person's research pass.

    `floor` is the event's measured govtech share where one is known. A trade
    this board has never once filed as govtech - catering, uniforms, paving -
    still loses whatever floor it stood on, because the floor shifts a prior
    and does not overturn an answer.
    """
    n = name or ""
    if NOT_TECH.search(n):
        return False, "names a trade that is not a product vendor"
    if TECH.search(n):
        return True, "names software, a platform or a govtech function"
    if floor is not None and floor >= RICH_FLOOR:
        return True, (f"nothing in the name, but {floor:.0%} of this floor's "
                      f"exhibitors have turned out to be govtech")
    return False, "nothing in the name either way"

# NOT USED AS A VERDICT, AND HERE IS WHY. The floor rate is the better signal
# and it cannot be honestly scored: it is computed FROM the same rulings any
# test would score against, so measuring the combined rule on this board's own
# labels grades it on its own answer key. Passing a single rate to every name
# in a test - which is what a first attempt did - fires the rule on everything
# and reports 100% recall, which is not a result, it is the rule saying yes.
#
# So apply() runs on the NAME alone, which was measured on 4,645 real rulings:
# 55% precision, 14% recall. Weak, honestly weak, and still worth running,
# because the alternative is 667 names going to suppliers unread.
#
# The rates floor_rates() computes are reported instead, as a reading order
# for whoever works the queue: ICMA 46%, EDUCAUSE 46%, NACo 40%, GFOA 33%
# against Animal Care Expo 3% and SNA 5%. Where to spend an hour is a
# judgement a person can make from that; which company is govtech is not.


def labelled() -> list[tuple[str, bool]]:
    """Every name this project has already ruled on, with its answer."""
    out = []
    for c in json.loads((DATA / "companies.json").read_text()):
        if c.get("govtech") is not False and c.get("category") != "Suppliers & Services":
            out.append((c["name"], True))
    sup = json.loads((DATA / "suppliers.json").read_text())
    rows = sup if isinstance(sup, list) else sup.get("suppliers", [])
    for s in rows:
        if s.get("govtech") is False:
            out.append((s["name"], False))
    return out


def measure() -> int:
    """Score the rules against what the board already decided.

    Reported as two numbers rather than one, because they are not
    interchangeable. PRECISION is what a person's research time buys: of the
    names sent for research, how many were really vendors. RECALL is what the
    supplier pile hides: of the real vendors, how many were sent. A single
    accuracy figure over an unbalanced set would flatter a rule that simply
    said "supplier" to everything.
    """
    data = labelled()
    tp = fp = tn = fn = 0
    for name, truth in data:
        guess, _ = score(name)
        if guess and truth: tp += 1
        elif guess and not truth: fp += 1
        elif not guess and not truth: tn += 1
        else: fn += 1
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    print(f"scored against {len(data)} names this board has already ruled on")
    print(f"  {tp + fn} known govtech, {tn + fp} known suppliers\n")
    print(f"  PRECISION {prec:.0%}  - of the names it sends for research, "
          f"{prec:.0%} really are vendors")
    print(f"  RECALL    {rec:.0%}  - of the real vendors, it catches {rec:.0%}; "
          f"the rest land in suppliers, visible and promotable")
    print(f"\n  sent for research: {tp + fp}   held as supplier: {tn + fn}")
    print("\n  A NAME IS A WEAK SIGNAL AND THIS IS WHAT THAT LOOKS LIKE. The "
          "point is not a good classifier;\n  it is a filter that costs less "
          "than reading 667 names by hand, erring toward supplier because\n  "
          "a supplier stays visible and a candidate costs someone an hour.")
    rnd = random.Random(11)
    wrong = [(n, t) for n, t in data if score(n)[0] != t]
    print(f"\n  {len(wrong)} disagreements. A sample, both directions:")
    for n, t in rnd.sample(wrong, min(10, len(wrong))):
        print(f"    {'vendor called supplier' if t else 'supplier sent to research'}: {n[:44]}")
    return 0


def apply(write: bool) -> int:
    files = sorted(DATA.glob("exhibitors_*.json"))
    known = {re.sub(r"[^a-z0-9]", "", n.lower()) for n, _ in labelled()}
    touched = tally = Counter()
    tally = Counter()
    changed = 0
    for f in files:
        d = json.loads(f.read_text())
        if not d.get("found"):
            continue
        n = 0
        for ex in d.get("exhibitors") or []:
            key = re.sub(r"[^a-z0-9]", "", (ex.get("name") or "").lower())
            if key in known:
                continue                       # already ruled on; intake tags it
            flag, why = score(ex["name"])
            if ex.get("is_govtech") != flag:
                n += 1
            ex["is_govtech"] = flag
            ex["is_govtech_why"] = why
            tally[flag] += 1
        if n and write:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1) + "\n")
            try:
                json.loads(tmp.read_text())
            except json.JSONDecodeError:
                tmp.unlink(missing_ok=True)
                print(f"  refused: {f.name} did not parse", file=sys.stderr)
                continue
            tmp.replace(f)
        changed += n
    print(f"{len(files)} staged file(s); {sum(tally.values())} name(s) not already on file")
    print(f"  {tally[True]} would go to CANDIDATES for research")
    print(f"  {tally[False]} would go to suppliers")
    print(f"\n  {'flagged ' + str(changed) + ' name(s)' if write else 'LOOKED ONLY. Re-run with --write.'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.measure or not a.apply:
        return measure()
    return apply(a.write)


if __name__ == "__main__":
    sys.exit(main())
