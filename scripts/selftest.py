#!/usr/bin/env python3
"""Offline self-test: validates the data layer and the title classifier without
touching the network. Run after any edit to data/ or scripts/.

  python scripts/selftest.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import alert     # noqa: E402
import classify  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

STATUSES = {"Yes", "Sales (non-AE)", "None found", "Unknown"}
ATS_TYPES = {"ashby", "greenhouse", "lever", "workable", "recruitee", "breezy",
             "smartrecruiters", "bamboohr", "workday", "rippling", "jazzhr",
             "icims", "html", "unknown"}

CLASSIFIER_CASES = [
    ("Account Executive", "ae"),
    ("Senior Account Executive, SLED - NYC Metro", "ae"),
    ("Enterprise Account Executive", "ae"),
    ("Sales Executive - Tolling", "ae"),
    ("Territory Manager, Pacific Northwest", "ae"),
    ("Regional Sales Manager - West", "ae"),
    ("Municipal Account Manager", "ae"),
    ("Named Account Manager, SLED", "ae"),
    ("Sales Development Representative", "sales_other"),
    ("Business Development Representative", "sales_other"),
    ("BDR", "sales_other"),
    ("VP, Sales", "sales_other"),
    ("Head of Sales", "sales_other"),
    ("Customer Success Manager", "sales_other"),
    ("Sales Engineer", "sales_other"),
    ("Solutions Consultant", "sales_other"),
    ("Inside Sales Account Manager", "sales_other"),
    ("Channel Partner Manager", "sales_other"),
    ("Revenue Operations Analyst", "sales_other"),
    ("Senior Full Stack Engineer", "none"),
    ("Product Manager", "none"),
    ("Firmware Engineer", "none"),
    ("Marketing Coordinator", "none"),  # marketing alone is not a sales-org signal
    # finance roles: "account" is a substring of "accountant"/"accounting"
    ("Senior Accountant", "none"),
    ("Accounting Manager, Lease & Fixed Assets", "none"),
    ("Accounts Payable Specialist", "none"),
    ("Corporate Controller", "none"),
    # ...but a real AE req that happens to mention accounting still counts
    ("Account Executive, Accounting Software", "ae"),
]

# 'html'-type boards give us page text, not titles. A scan can prove presence
# but never absence, so anything without concrete evidence is 'unreadable'
# (-> status Unknown) rather than a false 'none' (-> status None found).
PAGESCAN_CASES = [
    ("Open Roles Senior Account Executive, SLED Apply Now", "ae"),
    ("Careers We are hiring a Sales Development Representative", "sales_other"),
    ("Careers at Acme. There are currently no open positions.", "none"),
    ("Careers Search Jobs No results found", "none"),
    # nav chrome from a JS shell - the real Granicus/Polimorphic failure mode
    ("Who we serve Local Government State government Federal government "
     "Education Special districts Tourism Solutions Pricing About", "unreadable"),
    # product names containing sales-ish words must not fake a listing
    ("Enterprise Asset Manager Revenue Management Suite Permitting", "unreadable"),
    # a busy board must not read as empty via the "0 jobs" substring of "130 jobs"
    ("Engineering 12 jobs Marketing 13 jobs Sales 130 jobs Support 11 jobs", "unreadable"),
    # inert JS template branch on a page that is actually full of roles
    ("There are no open positions matching your filter selection. "
     "Autonomy Engineer Deep Learning. Enterprise Account Executive, SLED", "ae"),
    # nav sections named after sales functions must not read as open reqs
    ("Platform Solutions Customer Success Channel Partners Pricing Contact", "unreadable"),
    ("Why Us Partnerships Customer Success Stories Resources Blog", "unreadable"),
    # ...but the same words with a role noun are real postings
    ("Open Roles: Customer Success Manager, Remote. Apply", "sales_other"),
    ("Careers Channel Manager - Northeast", "sales_other"),
]


# Role-family cases. Most of these were 'Unclassified' on a live board, and each
# was a specific bug rather than an ambiguous title: \brecruit\b cannot match
# "Recruiter", "people ops" cannot match "People Operations", and
# chief\s+\w+\s+officer cannot match "Chief Services and Delivery Officer".
FAMILY_CASES = [
    ("Recruiter", "ga"),
    ("Recruiting Coordinator", "ga"),
    ("People Operations Coordinator", "ga"),
    ("Chief Services and Delivery Officer", "exec"),
    ("Customer Support Analyst - Level 2", "cs"),
    ("Customer Engagement Manager", "cs"),
    ("Accounts Payable Specialist", "ga"),
    ("Pensions Calculation Analyst", "ga"),
    ("Office Manager", "ga"),
    ("Enrollment Agent I", "ga"),
    ("Director of Strategic Accounts", "gtm"),
    ("Lead Generation Manager", "gtm"),
    ("Partner Development Director", "gtm"),
    ("Field Marketer, Customer", "gtm"),
    ("Road Supervisor", "field"),
    ("Mechanic", "field"),
    ("Data Annotator", "data"),
    ("Business Analyst", "data"),
    ("Technical Writer", "product"),
    ("Member of Technical Staff", "engineering"),
    ("Deal Operations Administrator", "ops"),
    ("Account Development Representative", "gtm"),
    ("Commercial Manager", "gtm"),
    ("Administrative Assistant", "ga"),
    ("Call Center Agent", "cs"),
    ("Assembler", "field"),
    # the decisions that must not regress
    ("Account Executive, SLED", "gtm"),
    ("Sales Engineer", "gtm"),
    ("Associate General Counsel, Revenue", "ga"),
    ("Senior Software Engineer", "engineering"),
]


def fail(msg):
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    errors = 0

    companies = json.load(open(DATA / "companies.json"))
    schema = json.load(open(DATA / "schema.json"))
    sector_cats = {s["name"]: set(s["categories"]) for s in schema["sectors"]}

    ids = [c["id"] for c in companies]
    if len(ids) != len(set(ids)):
        errors += fail("duplicate company ids")
    for c in companies:
        where = f"{c.get('name', '???')}"
        # Required to function: an entry without these cannot be keyed, placed in
        # the market map, or monitored. Everything else is research that may not
        # exist yet for a company somebody just added, and demanding it would mean
        # refusing to track a real company for want of a founding year.
        for field in ("id", "name", "sector", "category", "ats", "hiring"):
            if c.get(field) in (None, ""):
                errors += fail(f"{where}: missing {field}")
        if c["sector"] not in sector_cats:
            errors += fail(f"{where}: unknown sector {c['sector']}")
        elif c["category"] not in sector_cats[c["sector"]]:
            errors += fail(f"{where}: category {c['category']} not in {c['sector']}")
        if c["ats"]["type"] not in ATS_TYPES:
            errors += fail(f"{where}: bad ats type {c['ats']['type']}")
        if c["hiring"]["status"] not in STATUSES:
            errors += fail(f"{where}: bad status {c['hiring']['status']}")
        y = c.get("year_founded")
        if y is not None and (not isinstance(y, int) or not (1800 <= y <= 2100)):
            errors += fail(f"{where}: suspicious year {y}")

    import roles as _roles
    for title, expected in FAMILY_CASES:
        got = _roles.family(title)
        if got != expected:
            errors += fail(f"family({title!r}) = {got}, expected {expected}")
    # is_us: a country the list does not know returns None, and None does not
    # trip the non-US branch downstream. Four "Pakistan - Remote" AE roles
    # reached a New York shortlist banded "strong" that way.
    for loc, want in [("Pakistan - Remote", False), ("Karachi", False),
                      ("Bucharest, Romania", False), ("Guadalajara, Mexico", False),
                      ("United States - Remote", True), ("New York, NY", True),
                      ("San Jose, CA", True), ("Remote", None),
                      # STATE wanted a comma-prefixed code, so spelled-out
                      # names read as undeterminable and NYC-shaped locations
                      # never resolved at all
                      ("New York City", True), ("NYC Headquarters", True),
                      ("Texas Remote Work", True), ("U.S. (Remote)", True),
                      ("London, England", False), ("Toronto", False),
                      ("2 Locations", None)]:
        got = _roles.is_us(loc, "Account Executive")
        if got != want:
            errors += fail(f"is_us({loc!r}) = {got!r}, expected {want!r}")

    for t in ("Spontaneous Application", "Interested in joining our team?"):
        if not _roles.is_evergreen(t):
            errors += fail(f"{t!r} should be treated as an evergreen posting")

    for title, expected in CLASSIFIER_CASES:
        got = classify.classify_title(title)
        if got != expected:
            errors += fail(f"classify({title!r}) = {got}, expected {expected}")

    status, note, roles = classify.rollup([
        {"title": "Account Executive, SLED", "location": "New York, NY", "url": "x"},
        {"title": "SDR", "location": "", "url": "y"},
    ])
    if status != "Yes" or not roles:
        errors += fail("rollup: AE + SDR should be Yes")
    status, _, _ = classify.rollup([{"title": "SDR", "location": "", "url": "y"}])
    if status != "Sales (non-AE)":
        errors += fail("rollup: SDR only should be Sales (non-AE)")
    status, _, _ = classify.rollup([{"title": "Engineer", "location": "", "url": "y"}])
    if status != "None found":
        errors += fail("rollup: engineer only should be None found")

    for text, expected in PAGESCAN_CASES:
        got = classify.scan_pagetext(text)
        if got != expected:
            errors += fail(f"scan_pagetext({text[:32]!r}...) = {got}, expected {expected}")

    # an unreadable page scan must surface as Unknown, never as None found
    status, note, _ = classify.rollup([{"url": "x", "_pagetext": "Solutions Pricing About Us"}])
    if status != "Unknown":
        errors += fail(f"rollup: unreadable page scan should be Unknown, got {status}")
    status, _, _ = classify.rollup(
        [{"url": "x", "_pagetext": "Careers. There are currently no open positions."}])
    if status != "None found":
        errors += fail(f"rollup: explicit empty board should be None found, got {status}")

    # alerting: only transitions *into* Yes, and never on a first snapshot
    changes = [{"company": "A", "id": "a", "from": "Unknown", "to": "Yes"},
               {"company": "B", "id": "b", "from": "None found", "to": "Yes"},
               {"company": "C", "id": "c", "from": "Yes", "to": "None found"},
               {"company": "D", "id": "d", "from": "Yes", "to": "Yes"},
               {"company": "E", "id": "e", "from": "None found", "to": "Sales (non-AE)"}]
    hits = [h["id"] for h in alert.new_ae_openings({"previous": "2026-01-01",
                                                    "changes": changes})]
    if hits != ["a", "b"]:
        errors += fail(f"alert: expected new-Yes ['a','b'], got {hits}")
    if alert.new_ae_openings({"previous": None, "changes": changes}):
        errors += fail("alert: first snapshot should never alert")

    hist = sorted((DATA / "hiring_history").glob("*.json"))
    if not hist:
        errors += fail("no hiring_history snapshots")

    n_api = sum(1 for c in companies if c["ats"]["type"] not in ("html", "unknown"))
    thin = sum(1 for c in companies if not c.get("location") or not c.get("year_founded"))
    if thin:
        print(f"note: {thin} companies are missing optional research "
              f"(location or founding year)")
    print(f"{len(companies)} companies | {n_api} on structured ATS APIs | "
          f"{len(hist)} snapshot(s) | classifier cases: {len(CLASSIFIER_CASES)} title, "
          f"{len(PAGESCAN_CASES)} page-scan")
    if errors:
        print(f"\n{errors} problem(s) found")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
