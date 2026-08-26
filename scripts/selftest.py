#!/usr/bin/env python3
"""Offline self-test: validates the data layer and the title classifier without
touching the network. Run after any edit to data/ or scripts/.

  python scripts/selftest.py
"""
from __future__ import annotations

import collections
import csv
import html
import io
import json
import re
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import alert     # noqa: E402
import classify  # noqa: E402
import salary    # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

STATUSES = {"Yes", "Sales (non-AE)", "None found", "Unknown"}
import ats  # noqa: E402

# Derived from the fetchers that exist, never hand-listed. The two drifted:
# ats.py grew paylocity and oracle fetchers while this set kept the old
# fourteen, so discovery could find a board the validator then refused. A
# type is legal exactly when something can read it, plus "unknown", which
# means nothing has looked yet.
ATS_TYPES = set(ats.FETCHERS) | {"unknown"}

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


# A title is not just displayed. It is half the posting id, the key a scope
# ruling is filed under, and what an alert matches on, so an escape that
# survives the fetch spreads. Each of these arrived from a real board.
TITLE_TEXT_CASES = [
    # routeware, via rippling: the page's embedded JSON escapes the ampersand,
    # and the regex fetcher hands back the escape rather than the character
    ("Product Manager \\u0026 Education", "Product Manager & Education"),
    # ...and the same page's anchor text escapes it the other way
    ("Sales &amp; Marketing Lead", "Sales & Marketing Lead"),
    ("Director, People &#39;Ops&#39;", "Director, People 'Ops'"),
    ("A &quot;Named&quot; Account Executive", 'A "Named" Account Executive'),
    ("Account&nbsp;Executive", "Account Executive"),          # entity -> U+00A0
    ("Field \\u0026amp; Ops", "Field & Ops"),                 # escaped twice
    ("Staff  System Software Engineer", "Staff System Software Engineer"),
    # a backslash that is not an escape must leave the title alone rather than
    # take an exception: a wrong title beats a dropped posting
    ("Engineer \\ Architect", "Engineer \\ Architect"),
]


# =========================================================================
# salary.py - pay ranges parsed out of job-description prose
#
# Pay-transparency laws put a range in the posting TEXT even when the ATS
# exposes no compensation field, so a parser is worth having. But this board is
# almost entirely sales roles, whose descriptions are full of big dollar
# figures that are not pay - quotas, deal sizes, books of business, ARR
# targets, funding rounds - and a wrong number here is published as a fact
# about someone else's company. So the negatives below are the point of this
# table, not padding: roughly half the cases must return None, and every trap
# that has ever looked like pay gets one.
#
# expected is (min, max, currency, period), or None for "we refuse to say".
# =========================================================================
SALARY_CASES = [
    # --- the plain forms, exactly as they appear in real postings ----------
    ("The base salary range for this role is $140,000 - $200,000.",
     (140000, 200000, "USD", "year")),
    ("Salary: $140K - $200K", (140000, 200000, "USD", "year")),
    ("Pay range: $140k-$200k per year", (140000, 200000, "USD", "year")),
    ("Compensation: USD 140,000 to 200,000", (140000, 200000, "USD", "year")),
    ("Salary range: 140000-200000 USD", (140000, 200000, "USD", "year")),
    ("We are hiring. Base salary range of $140,000-$200,000.",
     (140000, 200000, "USD", "year")),
    ("The expected pay for this role is $140,000 to $200,000 annually.",
     (140000, 200000, "USD", "year")),
    ("Base salary: between $140,000 and $200,000",
     (140000, 200000, "USD", "year")),
    # all three dashes occur in the wild; so does a section heading two lines up
    ("Salary: $140,000–$200,000", (140000, 200000, "USD", "year")),
    ("The base salary range is $140,000 — $200,000 + equity.",
     (140000, 200000, "USD", "year")),
    ("Compensation\n\n$140,000 - $200,000 per year",
     (140000, 200000, "USD", "year")),
    # --- periods are stored, never converted ------------------------------
    ("The hourly rate for this position is $67.50 - $85.00/hour.",
     (67.5, 85, "USD", "hour")),
    ("The pay scale for this role is $67.50 - $85.00 per hour.",
     (67.5, 85, "USD", "hour")),
    ("Pay: $25.00 - $30.00 an hour", (25, 30, "USD", "hour")),
    ("Wage: $22.50/hr", (22.5, 22.5, "USD", "hour")),
    ("Base pay: $11,667 per month", (11667, 11667, "USD", "month")),
    ("The pay range is $140,000-$200,000/yr.", (140000, 200000, "USD", "year")),
    ("Salary range: $140,000 - $200,000 (annually)",
     (140000, 200000, "USD", "year")),
    ("The annual base salary of $140,000 - $200,000 applies.",
     (140000, 200000, "USD", "year")),
    # a period word welded to the figure is itself pay evidence, so this one
    # needs no label. a lone figure never gets that path - see the negatives.
    ("$67.50 - $85.00/hour", (67.5, 85, "USD", "hour")),
    # --- one-sided bounds --------------------------------------------------
    ("Salary: up to $200,000", (None, 200000, "USD", "year")),
    ("Salary starting at $140,000 per year", (140000, None, "USD", "year")),
    # --- currencies --------------------------------------------------------
    ("The salary range is £70,000 - £90,000.", (70000, 90000, "GBP", "year")),
    ("The salary range is €70,000 - €90,000 per year.",
     (70000, 90000, "EUR", "year")),
    ("Compensation for this role: CAD $120,000 - CAD $150,000 per year",
     (120000, 150000, "CAD", "year")),
    ("Salary: US$140,000 - US$200,000", (140000, 200000, "USD", "year")),
    # --- base wins when base and a bonus are both stated -------------------
    ("Base salary of $140,000 - $200,000 plus an annual bonus of "
     "$10,000 - $20,000.", (140000, 200000, "USD", "year")),
    # a whole posting of the kind this board is made of: every trap above in
    # one blob, and the base range still has to come out clean
    ("Senior Account Executive, SLED\n"
     "We have raised $65M across Series A and B. Our customers save $1.2M a "
     "year, and a typical contract runs $250,000 to $900,000 per year.\n"
     "- Own a $4M book of business and carry a quota of $1.8M in new ARR\n"
     "- Close $500K+ deals and build pipeline of $6M-$9M\n"
     "Benefits: 401(k) match up to 4%, a $1,000 annual learning stipend, "
     "up to $5,000 in tuition reimbursement, equity of 0.05% - 0.25%.\n"
     "Compensation: the base salary range for this position is "
     "$140,000 - $200,000 per year, plus uncapped commission. "
     "On-target earnings are $280,000 - $400,000.",
     (140000, 200000, "USD", "year")),

    # --- NEGATIVES: money in a sales JD that is not pay --------------------
    ("We raised $40M in our Series B round last year.", None),
    ("You will manage a $2M book of business across the Northeast.", None),
    ("You will close $500K+ deals with state agencies.", None),
    ("This role carries a quota of $1.2M in new ARR annually.", None),
    ("You will own a $5M territory and close $500K deals; salary "
     "commensurate with experience.", None),
    ("Our contract with the City of Austin is worth $12M over five years.",
     None),
    ("Our platform saves cities $3M a year in fleet costs.", None),
    # the trailing noun is what disqualifies this one - nothing before it does
    ("The product saves agencies $50,000 - $200,000 in annual savings.", None),
    # --- NEGATIVES: benefits and equity are not pay ------------------------
    ("Benefits include a $500 annual learning stipend.", None),
    ("401k match up to 4% of salary.", None),
    ("Equity grant of 0.1% - 0.5% depending on level.", None),
    ("Tuition reimbursement up to $25,000 per year.", None),
    ("We offer a signing bonus of $10,000 - $20,000.", None),
    # --- NEGATIVES: OTE is deliberately not captured -----------------------
    # the comp shape has no field to mark what kind of number it is, so an OTE
    # range would ship indistinguishable from a base range. see salary.py.
    ("Total compensation range: $180,000 - $260,000.", None),
    ("OTE of $280,000 - $320,000 with uncapped commission.", None),
    ("On-target earnings $250,000.", None),
    # --- NEGATIVES: not enough stated to fill the shape --------------------
    ("Salary range: 140,000 - 200,000", None),          # no currency at all
    ("Salary range $140,000 - £200,000", None),         # two currencies
    ("Base salary $8,000", None),                       # no period, too small
    ("Founded in 2019, we serve 2,000-3,000 agencies.", None),
    # --- NEGATIVES: implausible as pay -------------------------------------
    ("Salary: $200,000 - $140,000", None),              # inverted
    ("Pay: $1,200 - $900,000", None),                   # 750x is two numbers
    ("Hourly pay: $600 - $900 per hour", None),         # over the hourly cap
    ("Salary: $3,000,000 - $4,000,000 per year", None), # over the yearly cap
    # --- NEGATIVES: the posting says two different things ------------------
    # multi-state tiers. picking one publishes a range that may not apply to
    # the reader, so we publish nothing.
    ("The salary range is $140,000-$200,000 in New York and "
     "$120,000-$170,000 in Texas.", None),
    ("The base salary range is $140,000 - $200,000. Salary may go up to "
     "$250,000.", None),
    # --- NEGATIVES: empty and junk input -----------------------------------
    ("", None),
    ("no numbers at all in this description", None),

    # --- found by running the parser over 144 real descriptions ------------
    # Mark43 buries the range inside a sentence that opens with the phrase we
    # refuse. Nearest label wins, so the base range still comes out.
    ("Total compensation for this role is market competitive, including a "
     "target base annual salary range of $80,000 - $110,000, plus bonus "
     "opportunity, company stock options, and a full benefits package.",
     (80000, 110000, "USD", "year")),
    # same posting family, UK req: a space after the symbol, k on both ends,
    # and no symbol on the second figure
    ("a target base annual salary range of £ 30k-80k, plus bonus opportunity",
     (30000, 80000, "GBP", "year")),
    # Accela drops the symbol on the upper bound
    ("The annual base salary range for this full-time position is "
     "$55,000-65,000 (less applicable taxes).", (55000, 65000, "USD", "year")),
    # Swiftly: a symbol plus a spelled-out code is CAD, not a currency clash
    ("Canadian Salary Range: $152,000- $190,000 CAD",
     (152000, 190000, "CAD", "year")),
    # ...and Swiftly's OTE marker sits AFTER the figure, where a lookback
    # cannot see it. This shipped a $74k-$124k OTE as a base range until the
    # tail check learned the word.
    ("US Salary Range: $74,000- $124,000 USD OTE", None),
    # ...and their US/Canada tiers are two different ranges for one job
    ("US Salary Range: $124,000 - $205,000 USD\n"
     "Canadian Salary Range: $152,000- $190,000 CAD", None),
]

# raw is the receipt: a person has to be able to check the number against the
# posting, so it is pinned separately rather than trusted to be non-empty.
SALARY_RAW_CASES = [
    ("The base salary range for this role is $140,000 - $200,000.",
     "$140,000 - $200,000"),
    ("Salary: up to $200,000", "up to $200,000"),
    ("Base salary: between $140,000 and $200,000",
     "between $140,000 and $200,000"),
    # a range split across a line break still has to quote as one readable line
    ("Pay range: $140,000 -\n$200,000 per year", "$140,000 - $200,000 per year"),
]


def check_salary() -> int:
    errors = 0
    for text, expected in SALARY_CASES:
        got = salary.parse(text)
        label = text[:44].replace("\n", " ")
        if expected is None:
            if got is not None:
                errors += fail(f"salary.parse({label!r}...) should refuse, "
                               f"got {got['min']}-{got['max']} from {got['raw']!r}")
            continue
        if got is None:
            errors += fail(f"salary.parse({label!r}...) = None, expected {expected}")
            continue
        actual = (got["min"], got["max"], got["currency"], got["period"])
        if actual != expected:
            errors += fail(f"salary.parse({label!r}...) = {actual}, expected {expected}")
        if got["source"] != "text":
            errors += fail(f"salary.parse({label!r}...) source = {got['source']!r}, "
                           "expected 'text'")
        if not got["raw"]:
            errors += fail(f"salary.parse({label!r}...) has no raw quote")

    for text, expected_raw in SALARY_RAW_CASES:
        got = salary.parse(text)
        if got is None or got["raw"] != expected_raw:
            errors += fail(f"salary.parse({text[:36]!r}...) raw = "
                           f"{None if got is None else got['raw']!r}, "
                           f"expected {expected_raw!r}")

    # A missed range costs a filter hit; a wrong one is published as a fact
    # about somebody else's company. Keep the table honest about that: if the
    # negatives ever thin out, the parser has been tuned for recall.
    negatives = sum(1 for _, e in SALARY_CASES if e is None)
    if negatives * 3 < len(SALARY_CASES):
        errors += fail(f"salary cases: only {negatives} of {len(SALARY_CASES)} are "
                       "negatives; false positives are the expensive error here")
    return errors


def fail(msg):
    print(f"FAIL: {msg}")
    return 1


# Keys that hold a job description, under every name a fetcher or an ATS has
# used for one. None of them may appear on a posting in board.json.
# `jd_text` is the capture extension's name for one, and it was missing from
# this set while build_board popped only `jd` - so a single captured posting
# would have shipped 20,000 characters of another company's job ad into the
# public file. It had never fired only because no single-posting capture had
# run yet.
PROSE_KEYS = {"jd", "jd_text", "description", "descriptionPlain",
              "descriptionHtml",
              "content", "requirements", "jobDescription", "jobAd", "body",
              "text", "_pagetext", "_jd_is_teaser", "_detail_url"}

# Nothing on a posting is prose, so nothing on a posting is long. The longest
# legitimate string on the board today is a 283-character Workday url; a
# description averages ~6,000 characters. Anything past this is text that
# should have been thrown away, whatever key it arrived under.
PROSE_CHARS = 500

# The periods ats.py and salary.py are allowed to state. A period the site does
# not know about is a comparison it cannot make, and comp_floor is meaningless
# without one.
COMP_PERIODS = {"year", "month", "week", "day", "hour"}


def _strings(value, path="") -> list[tuple[str, str]]:
    """Every string anywhere inside a posting, with the key path that holds it."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += _strings(v, f"{path}.{k}" if path else str(k))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out += _strings(v, f"{path}[{i}]")
        return out
    return [(path, value)] if isinstance(value, str) else []


def check_no_jd_text(postings: list[dict]) -> int:
    """The description text must never reach data/board.json.

    build_board.derived() reads each posting's description, keeps the pay range
    and whether it was read at all, and drops the prose. Two reasons that is not
    an optimisation to relax later:

    - size. 4,355 descriptions at ~6,000 characters each is roughly 25MB on top
      of a 5.7MB file, downloaded by every visitor before the board draws.
    - it is not ours. Republishing other companies' job-ad copy wholesale is a
      different product from listing that the job exists and linking to it.

    A leak would not look like an error anywhere - the file would simply get
    large and stay large - so it is checked rather than remembered.
    """
    bad = 0
    for p in postings:
        stray = sorted(PROSE_KEYS & set(p))
        if stray:
            bad += fail(f"posting {p.get('id')!r} carries description key(s) "
                        f"{stray} - board.json must ship derived facts only, "
                        "never the job-ad text (see build_board.derived)")
            break
    for p in postings:
        long_ = [(k, len(v)) for k, v in _strings(p) if len(v) > PROSE_CHARS]
        if long_:
            k, n = long_[0]
            bad += fail(f"posting {p.get('id')!r} field {k!r} is {n} characters; "
                        f"nothing on a posting is prose, so nothing should pass "
                        f"{PROSE_CHARS} (see build_board.derived)")
            break
    return bad


def check_comp(postings: list[dict]) -> int:
    """Pay fields, where a board built by the current builder carries them.

    Skipped per-posting rather than demanded, because data/board.json is
    rebuilt by a long network run and a stale file must not fail the build. It
    retires itself the first time the board is rebuilt.

    build_board.derived() is pinned directly in check_derived() below, which is
    what actually holds the rules; this is the same rules re-checked against
    whatever is on disk, so a hand-edit or a half-finished run cannot slip past.
    """
    bad = 0
    for p in postings:
        comp = p.get("comp")
        if comp is None:
            # Not stated, or never read. Never zero, and never a reason to drop
            # the posting - which is why there is nothing to check here.
            if p.get("comp_floor") is not None or p.get("comp_period") is not None:
                bad += fail(f"posting {p['id']!r} has no comp but carries "
                            "comp_floor/comp_period")
                break
            continue
        lo, hi = comp.get("min"), comp.get("max")
        if lo is None and hi is None:
            bad += fail(f"posting {p['id']!r} has a comp block with no figure in "
                        "it - that is 'not stated', which is comp: null")
            break
        if lo is not None and hi is not None and lo > hi:
            bad += fail(f"posting {p['id']!r} comp min {lo} > max {hi}")
            break
        if comp.get("period") not in COMP_PERIODS:
            bad += fail(f"posting {p['id']!r} comp period "
                        f"{comp.get('period')!r} is not one of "
                        f"{sorted(COMP_PERIODS)}")
            break
        if not comp.get("raw"):
            bad += fail(f"posting {p['id']!r} comp has no raw quote - the quote "
                        "is how a person checks the number against the posting")
            break
        # comp_floor is the low bound and never the high one: "up to $200,000"
        # states a ceiling, and publishing it as a floor advertises a minimum
        # nobody offered.
        if p.get("comp_floor") != lo or p.get("comp_period") != comp.get("period"):
            bad += fail(f"posting {p['id']!r} comp_floor/comp_period "
                        f"({p.get('comp_floor')}, {p.get('comp_period')!r}) "
                        f"drifted from comp ({lo}, {comp.get('period')!r})")
            break
    return bad


def check_safe_url() -> int:
    """Every posting url must be one a browser will follow.

    Two on the board carried a literal space - PowerSchool's in a query value
    ("location=CA--Remote - CAN"), Survalent's in a filename. A space is not
    legal in a url and a browser will not follow it, so both were dead links
    on a board whose whole value is that the link works. Two out of 4,369:
    invisible in any summary, and the only person who ever sees it is the one
    who clicks it.
    """
    import build_board
    errors = 0
    CASES = [
        ("https://x.com/job?location=CA--Remote - CAN",
         "https://x.com/job?location=CA--Remote%20-%20CAN"),
        ("https://x.com/Job_Posting_Account Development Rep.pdf",
         "https://x.com/Job_Posting_Account%20Development%20Rep.pdf"),
        # an escape that is already an escape stays one - running this twice
        # must not turn %20 into %2520
        ("https://x.com/already%20encoded", "https://x.com/already%20encoded"),
        ("https://job-boards.greenhouse.io/metropolis/jobs/7810050003",
         "https://job-boards.greenhouse.io/metropolis/jobs/7810050003"),
    ]
    for raw, want in CASES:
        got = build_board.safe_url(raw)
        if got != want:
            errors += fail(f"safe_url({raw!r}) = {got!r}, expected {want!r}")
        if build_board.safe_url(got) != got:
            errors += fail(f"safe_url is not idempotent on {raw!r}")

    # and nothing already shipped may carry one
    board = json.load(open(DATA / "board.json"))
    bad = [p for p in board.get("postings", [])
           if isinstance(p.get("url"), str) and " " in p["url"]]
    for p in bad[:5]:
        errors += fail(f"board.json: {p['company']} has a space in its url - "
                       f"a browser will not follow {p['url'][:70]!r}")
    return errors


def check_derived() -> int:
    """build_board.derived(): what survives a description, and what does not."""
    import build_board
    bad = 0
    year = {"min": 140000, "max": 200000, "currency": "USD", "period": "year",
            "source": "text", "raw": "$140,000 - $200,000"}
    hour = {"min": 67.5, "max": 85, "currency": "USD", "period": "hour",
            "source": "text", "raw": "$67.50 - $85.00/hour"}
    cap = {"min": None, "max": 200000, "currency": "USD", "period": "year",
           "source": "text", "raw": "up to $200,000"}

    cases = [
        # (row, expected subset of the derived fields)
        # read the posting, no pay stated. NOT a posting that pays nothing.
        ({"jd": "We are hiring a seller.", "comp": None},
         {"jd_seen": True, "comp": None}),
        # never read it. Looks nothing like the row above, and must not.
        ({"jd": "", "comp": None}, {"jd_seen": False, "comp": None}),
        # whitespace is not a description
        ({"jd": "   \n ", "comp": None}, {"jd_seen": False, "comp": None}),
        # read it, and it stated a yearly range
        ({"jd": "Base salary range: $140,000 - $200,000", "comp": year},
         {"jd_seen": True, "comp": year, "comp_floor": 140000,
          "comp_period": "year"}),
        # hourly stays hourly. Annualising it would mean inventing hours.
        ({"jd": "text", "comp": hour},
         {"jd_seen": True, "comp_floor": 67.5, "comp_period": "hour"}),
        # a stated ceiling is not a floor
        ({"jd": "text", "comp": cap},
         {"jd_seen": True, "comp_floor": None, "comp_period": "year"}),
        # Breezy: pay in the list response, no description anywhere. Pay stated
        # and the posting never read are independent facts, not a sequence.
        ({"jd": "", "comp": year},
         {"jd_seen": False, "comp": year, "comp_floor": 140000}),
        # A CAPTURED row: the description arrives under jd_text, and nothing
        # upstream has parsed it, because it never went through ats.py. These
        # are the postings with the most riding on it - a row is captured by
        # hand precisely because no fetcher can enumerate that company, so this
        # text holds the only pay range anybody will ever get for it.
        ({"jd_text": "The base salary range for this role is "
                     "$140,000 - $200,000 per year.", "comp": None},
         {"jd_seen": True, "comp_floor": 140000, "comp_period": "year"}),
        # and it counts as having read the posting, which "" for `jd` does not
        ({"jd_text": "We are hiring a seller.", "comp": None},
         {"jd_seen": True, "comp": None}),
        # a comp already on the row wins: the board's own field is a stronger
        # claim than anything parsed out of prose, and must not be overwritten
        ({"jd_text": "Base salary range: $10 - $20 per hour", "comp": year},
         {"jd_seen": True, "comp": year, "comp_floor": 140000}),
        # whitespace is not a description under this key either
        ({"jd_text": "  \n ", "comp": None},
         {"jd_seen": False, "comp": None}),
        # a fetcher handing back something malformed costs the pay range, not
        # the board: this whole script is one process writing one file.
        ({"jd": "text", "comp": "$140k"}, {"jd_seen": True, "comp": None}),
        ({"jd": {"html": "..."}, "comp": None}, {"jd_seen": False, "comp": None}),
        ({}, {"jd_seen": False, "comp": None}),
    ]
    for row, want in cases:
        got = build_board.derived(dict(row))
        for k, v in want.items():
            if got.get(k) != v:
                bad += fail(f"derived({row!r})[{k!r}] = {got.get(k)!r}, "
                            f"expected {v!r}")
        # the whole point: the text is the input and never the output
        for k in got:
            if k in PROSE_KEYS:
                bad += fail(f"derived({row!r}) returned {k!r} - the description "
                            "text must not survive into a posting")
    # a row with a comp must never lose it, and a row without one must not
    # invent the keys that would make it filterable as a low payer
    plain = build_board.derived({"jd": "x", "comp": None})
    if "comp_floor" in plain or "comp_period" in plain:
        bad += fail("derived() gave a pay-less posting comp_floor/comp_period; "
                    "a posting with no stated pay is not a posting paying zero")
    return bad


def check_board() -> int:
    """Invariants on the built board that a person cannot eyeball at 4,242 rows.

    The site resolves a role with `D.postings.find(x => x.id === id)`, so a
    repeated id is not a cosmetic problem: every row after the first opens
    somebody else's job, and a saved role reopens as a different city.
    """
    path = DATA / "board.json"
    if not path.exists():
        print("note: no data/board.json yet, skipping board checks")
        return 0
    board = json.load(open(path))
    postings = board.get("postings", [])
    bad = 0

    dupes = [i for i, n in collections.Counter(p["id"] for p in postings).items()
             if n > 1]
    if dupes:
        bad += fail(f"{len(dupes)} posting id(s) are used by more than one row, "
                    f"e.g. {sorted(dupes)[:3]}")

    for p in postings:
        want = f"{p['company_id']}::{p['title']}"
        if p.get("opening_id") != want:
            bad += fail(f"opening_id {p.get('opening_id')!r} is not "
                        f"company::title for {p['id']!r}")
            break
        # Old shared links and saved roles carry the pre-discriminator id, and
        # index.html resolves them by prefix-matching `oldid::`. That fallback
        # only works while the opening id stays the head of the posting id.
        if not p["id"].startswith(p["opening_id"] + "::"):
            bad += fail(f"posting id {p['id']!r} does not start with its "
                        f"opening_id, breaking stale-link recovery")
            break

    for p in postings:
        title = p.get("title") or ""
        if html.unescape(title) != title or re.search(r"\\u[0-9a-fA-F]{4}", title):
            bad += fail(f"title still carries an escape: {title!r} "
                        f"({p['company_id']}) - see ats.plain()")
            break

    # Openings, not rows. Recomputed here from the postings themselves so a
    # headline number can never drift from the rows it claims to summarise.
    groups = collections.defaultdict(list)
    for p in postings:
        groups[p["opening_id"]].append(p)
    t = board.get("totals", {})
    for name, got, want in [
        ("postings", t.get("postings"), len(postings)),
        ("openings", t.get("openings"), len(groups)),
        ("quota_carrying", t.get("quota_carrying"),
         sum(1 for rows in groups.values()
             if any(r.get("quota_carrying") for r in rows))),
        ("quota_carrying_postings", t.get("quota_carrying_postings"),
         sum(1 for p in postings if p.get("quota_carrying"))),
    ]:
        if got != want:
            bad += fail(f"totals.{name} = {got}, but the postings say {want}")

    for rows in groups.values():
        if rows[0].get("opening_postings") != len(rows):
            bad += fail(f"{rows[0]['opening_id']!r} says it is advertised "
                        f"{rows[0].get('opening_postings')} times, but "
                        f"{len(rows)} rows carry it")
            break

    per_co = collections.Counter(rows[0]["company_id"] for rows in groups.values())
    for o in board.get("organizations", []):
        if o.get("open_roles", 0) != per_co.get(o["id"], 0):
            bad += fail(f"{o['name']}: open_roles = {o.get('open_roles')}, but "
                        f"{per_co.get(o['id'], 0)} openings are filed under it")
            break

    bad += check_no_jd_text(postings)
    bad += check_comp(postings)
    if postings and not any("jd_seen" in p for p in postings):
        print("note: data/board.json predates the pay fields; rebuild it with "
              "build_board.py to get comp/jd_seen coverage")
    else:
        stated = sum(1 for p in postings if p.get("comp"))
        unread = sum(1 for p in postings if not p.get("jd_seen"))
        print(f"note: {stated} posting(s) state pay, {unread} were never read "
              "(the two are separate facts, not a partition)")
    return bad


def check_brand() -> int:
    """functions/_brand.js must say the same thing as data/brand.json.

    A Worker cannot read the JSON at runtime, so the domain is written down
    twice. The day the domain changes, missing one of the two means alert links
    and confirmation emails point at a name that no longer resolves - and
    nothing would report it, because both files are individually valid.
    """
    js = ROOT / "functions" / "_brand.js"
    if not js.exists():
        return 0
    import brand
    text = js.read_text()
    bad = 0
    for const, want in (("SITE", brand.SITE), ("DOMAIN", brand.DOMAIN),
                        ("NAME", brand.NAME), ("FROM", brand.FROM)):
        m = re.search(rf'export const {const} = "([^"]*)"', text)
        if not m:
            bad += fail(f"_brand.js: no {const}")
        elif m.group(1) != want:
            bad += fail(f"_brand.js {const} is {m.group(1)!r}, "
                        f"data/brand.json says {want!r}")
    return bad


def check_admin_game() -> int:
    """The admin's scoring layer must never turn an unknown into a number.

    Three invariants, and all three are the same rule wearing different
    clothes - absence of evidence is reported as absence of evidence:

    - The AGREE-RATE is unmeasured until somebody rules. A ruling made with
      no proposal on screen says nothing about the guesser in either
      direction, so counting it would manufacture an accuracy figure out of
      records that never tested one.
    - The BELT only runs where the answer is on the card. Acquisitions is
      explicitly excluded: deciding whether a slug belongs to a parent
      company needs slow reading, and a counter beside it would buy speed
      with accuracy.
    - The CSV export is a copy of stored facts and nothing else. Every
      column must exist on the records, and a cell that could be read as a
      spreadsheet FORMULA has to be neutralised, because company names
      arrive here from outside submissions.
    """
    import admin
    bad = 0

    if admin.agree_rate.__module__ != "admin":
        return fail("admin.agree_rate went missing")
    empty = {"saw": {"proposed": None}}
    if admin._seen_proposal(empty) is not None:
        bad += fail("agree_rate would score a ruling that saw no proposal")
    # "null / null" is what a browser writes when there was no proposal, and
    # it has a slash in it, so a naive check scores it as a real one
    for shape in ({}, {"saw": {}}, {"saw": {"proposed": "None / None"}},
                  {"saw": {"proposed": "null / null"}},
                  {"saw": {"proposed": "undefined / undefined"}},
                  {"saw": {"proposed": " / Fire"}},
                  {"saw": {"proposed": "not a pair"}}):
        if admin._seen_proposal(shape) is not None:
            bad += fail(f"_seen_proposal({shape}) invented a proposal")
    if admin._seen_proposal({"saw": {"proposed": "Public Safety / Fire"}}) \
            != "Public Safety / Fire":
        bad += fail("_seen_proposal dropped a real proposal")

    for q in admin.BELT_QUEUES + admin.PROPOSAL_QUEUES:
        if q not in admin.QUEUES:
            bad += fail(f"belt/proposal queue {q!r} is not a queue")
    if "acquisitions" in admin.BELT_QUEUES:
        bad += fail("acquisitions must never ride the belt - it needs slow "
                    "reading, and a counter there trades accuracy for speed")
    for q in admin.END_STATE:
        if q not in admin.QUEUES:
            bad += fail(f"END_STATE names {q!r}, which is not a queue")

    # a formula-shaped cell must be neutralised, and an ordinary one left alone
    for raw in ("=1+1", "+cmd", "-2", "@x"):
        if not admin._cell(raw).startswith("'"):
            bad += fail(f"CSV cell {raw!r} would run as a spreadsheet formula")
    if admin._cell("Tyler Technologies") != "Tyler Technologies":
        bad += fail("CSV cell mangled an ordinary value")
    companies = json.load(open(DATA / "companies.json"))
    rows = list(csv.DictReader(io.StringIO(
        admin.board_csv(companies, json.load(open(DATA / "board.json"))))))
    if len(rows) != len(companies):
        bad += fail(f"CSV has {len(rows)} rows for {len(companies)} companies")
    elif {r["id"] for r in rows} != {c["id"] for c in companies}:
        bad += fail("CSV rows do not cover exactly the companies on file")
    return bad


def _sandbox_admin(files: dict):
    """Point admin AND journal at a throwaway data directory.

    Every check below writes rulings, and a test that writes a ruling into
    data/ is exactly the accident this repo spent a night recovering from.
    journal has its own DATA, so both get moved or the before-images land in
    the real audit trail.
    """
    import contextlib
    import tempfile

    import admin
    import journal

    @contextlib.contextmanager
    def swap():
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gtd-selftest-"))
        for name, payload in files.items():
            (tmp / name).write_text(json.dumps(payload))
        keep = (admin.DATA, journal.DATA, journal.LOG)
        admin.DATA, journal.DATA, journal.LOG = tmp, tmp, tmp / "admin_journal.jsonl"
        try:
            yield tmp
        finally:
            admin.DATA, journal.DATA, journal.LOG = keep
    return swap()


def check_admin_gates() -> int:
    """The gates that decide when a reward opens and when a keystroke commits.

    An adversarial review emptied all of them in three and a half seconds - 49
    accepts down the belt, no card read, no reason typed - and its sharpest
    observation was not any single number but that NOTHING TESTED THEM: "a
    refactor could open the CSV gate and nothing would notice". So each gate
    is asserted here, against the functions and against a real server, in the
    same spirit as check_admin_http.

    - NO INVENTED DENOMINATOR. board_health's public-correctness part read
      max(0, 40 - wrong) / 40, and the 40 counted nothing on file. Every part
      must divide by a number that is a count of records.
    - AN EMPTY DENOMINATOR IS NOT A ZERO. A queue nobody has been asked about
      yet must report unknown, not 0%.
    - THE CSV GATE IS ENFORCED ON THE SERVER, not only on the button, and it
      is closed while any public row contradicts itself.
    - A COUNT OF RULINGS IS NOT A MEASUREMENT. A burst cannot make the
      agree-rate measured, and neither can an unbroken run of agreement.
    - A REASON NOBODY TYPED IS NOT A REASON, and a low-confidence proposal
      cannot be accepted in silence.
    - THE CONSOLE CODE IS IN NOTHING THE SERVER SENDS. That is the whole
      property that keeps a script off the ruling endpoints; if it ever ships
      in the page, the gate is decoration.
    """
    import datetime as dt

    import admin
    bad = 0

    # --- board_health: real counts on both sides of every fraction --------
    companies = json.load(open(DATA / "companies.json"))
    board = json.load(open(DATA / "board.json"))
    h = admin.board_health(companies, board)
    live = {o["id"]: o.get("open_roles", 0) for o in board.get("organizations", [])}
    visible = sum(1 for c in companies if live.get(c["id"], 0) > 0)
    if h.get("visible") != visible:
        bad += fail(f"board_health says {h.get('visible')} public rows, the "
                    f"board says {visible}")
    part = next((p for p in h["parts"] if "public" in p["label"].lower()), None)
    if not part:
        bad += fail("board_health has no part about the public rows")
    elif part["of"] != visible:
        bad += fail(f"the public-correctness part divides by {part['of']}, "
                    f"which is not the {visible} companies with open roles - "
                    f"a denominator that counts nothing on file is the bug "
                    f"this check exists for")
    for p in h["parts"]:
        if p["of"] == 0 and p["pct"] is not None:
            bad += fail(f"{p['label']!r} has an empty denominator and still "
                        f"reports {p['pct']}% - an unknown scored as a zero")

    # --- the CSV gate, as a function and on the wire ----------------------
    # The unlocks are gone and this asserts they stay gone. A review defeated
    # every gate protecting them fifteen ways, the cheapest being one bulk
    # call that wrote 240 rulings in zero seconds - so the capabilities are
    # simply capabilities now. If somebody reintroduces a reward gated on an
    # activity number, this fails and they have to read why first.
    if getattr(admin, "UNLOCKS", None):
        errors += fail("admin.UNLOCKS is back - rewards gated on activity "
                       "were removed deliberately; see the note above unlocks()")
    if admin.unlocks({}) != []:
        errors += fail("unlocks() should hand out nothing")
    if hasattr(admin, "csv_gate"):
        errors += fail("csv_gate is back - the export is not gated")

    # --- what makes an agree-rate a measurement ---------------------------
    t0 = dt.datetime(2026, 8, 24, 10, 0, 0)

    def ruling(i, secs, agrees=True):
        return {"sector": "Public Safety",
                "category": "Fire" if agrees else "Police",
                "at": (t0 + dt.timedelta(seconds=secs)).isoformat(),
                "on": "2026-08-24", "by": "owner", "why": None,
                "saw": {"proposed": "Public Safety / Fire",
                        "confidence": "high"}}

    n = admin.MEASURED_AT + 20
    cases = [
        ("a burst of rulings in one second",
         {f"c{i}": ruling(i, 0) for i in range(n)}, False),
        ("an unbroken run of agreement",
         {f"c{i}": ruling(i, i * (admin.READ_SECONDS + 5)) for i in range(n)},
         False),
        ("paced rulings that argued with the guesser",
         {f"c{i}": ruling(i, i * (admin.READ_SECONDS + 5),
                          agrees=i >= admin.MIN_DISSENT) for i in range(n)},
         True),
    ]
    for label, recs, want in cases:
        with _sandbox_admin({"placement_rulings.json": recs}):
            got = admin.agree_rate()
        if got["measured"] != want:
            bad += fail(f"{label}: agree-rate measured={got['measured']}, "
                        f"expected {want} "
                        f"(ruled {got['ruled']}, considered {got['considered']},"
                        f" dissent {got['dissent']})")
        if not want and not got.get("why_not"):
            bad += fail(f"{label}: unmeasured with no reason given")

    # --- act_place: no substituted reason, no silent low-confidence accept -
    # A company that is NOT already where these cases file it. companies[0]
    # is Seneca, which is Public Safety / Fire already, so "moving" it there
    # is a no-op, the journal correctly records nothing, and the
    # counted-once check below reads that as a missing entry.
    cid = next(c["id"] for c in companies
               if (c["sector"], c["category"]) != ("Public Safety", "Fire"))
    shown = {"was": "General Gov / Suppliers & Services",
             "proposed": "Public Safety / Fire", "confidence": "low"}
    with _sandbox_admin({"companies.json": companies,
                         "schema.json": json.load(open(DATA / "schema.json"))}):
        r = admin.act_place({"id": cid, "sector": "Public Safety",
                             "category": "Fire", "why": "", **shown})
        if not r.get("error"):
            bad += fail("a LOW-confidence proposal was accepted with no reason "
                        "typed - that is the blind path the belt gate closes")
        r = admin.act_place({"id": cid, "keep": True, "why": "", **shown})
        if r.get("error"):
            bad += fail(f"'bucket is right' was refused: {r['error']}")
        kept = admin.read("admin_dismissed.json", {}).get(f"miscategorized:{cid}")
        if (kept or {}).get("why"):
            bad += fail(f"a dismissal nobody explained stored the reason "
                        f"{kept['why']!r} - the craft meter counts any "
                        f"non-empty why, so a stand-in inflates it")
    with _sandbox_admin({"companies.json": companies,
                         "schema.json": json.load(open(DATA / "schema.json"))}):
        # the same ruling, at high confidence, must NOT be asked for an essay
        r = admin.act_place({"id": cid, "sector": "Public Safety",
                             "category": "Fire", "why": "",
                             **{**shown, "confidence": "high"}})
        if r.get("error"):
            bad += fail(f"a high-confidence agreement was made to justify "
                        f"itself: {r['error']} - that trains junk reasons")

    # --- rulings are rulings, and an agent's writes are not yours ---------
    agent_journal = "\n".join(json.dumps({
        "id": f"2026-08-24#{i}", "at": (t0 + dt.timedelta(seconds=i)).isoformat(),
        "file": "companies.json", "action": "set-founded",
        "by": "agent:overnight-build", "why": "x", "n": 1,
        "changes": {f"c{i}": {"before": {"year_founded": None},
                              "after": {"year_founded": 1999}}}})
        for i in range(30))
    with _sandbox_admin({}) as tmp:
        (tmp / "admin_journal.jsonl").write_text(agent_journal + "\n")
        s = admin.sessions()
        if s["rulings"]:
            bad += fail(f"sessions() counted {s['rulings']} rulings out of 30 "
                        f"journal writes by an agent - a personal best made "
                        f"of somebody else's typing")
        rc = admin.receipt()
        if rc.get("stamped"):
            bad += fail(f"the receipt stamped {rc['stamped']} of an agent's writes")
        if not rc.get("by_others"):
            bad += fail("30 agent writes this sitting and the receipt does "
                        "not say whose they were")

    # --- one ruling is counted once --------------------------------------
    #
    # A wrong-bucket ruling writes BOTH a decision record and a journal entry;
    # a Sort-board drag writes only a journal entry. They used to journal
    # under the same name, so any rule that counted journal writes either
    # doubled every placement or dropped every drag. The names are what keeps
    # them apart, so the names are asserted.
    if "place" in admin.JOURNAL_RULINGS:
        bad += fail("a placement is journalled AND recorded in "
                    "placement_rulings.json - counting both doubles it")
    if "move" not in admin.JOURNAL_RULINGS:
        bad += fail("a Sort-board drag is a ruling with no other record and "
                    "is not being counted")
    with _sandbox_admin({"companies.json": companies,
                         "schema.json": json.load(open(DATA / "schema.json"))}):
        r = admin.act_place({"id": cid, "sector": "Public Safety",
                             "category": "Fire", "why": "checked the site",
                             "was": "General Gov / Suppliers & Services",
                             "proposed": "Public Safety / Fire",
                             "confidence": "low"})
        if r.get("error"):
            bad += fail(f"a low-confidence accept WITH a reason was refused: "
                        f"{r['error']}")
        import journal
        actions = [e.get("action") for e in journal._entries()]
        if "place" not in actions:
            bad += fail(f"act_place journalled as {actions!r}, not 'place' - "
                        f"the ruling count cannot tell it from a drag")
        stamps = admin._ruling_stamps()
        if len(stamps) != 1:
            bad += fail(f"one ruling was counted {len(stamps)} times")

    # --- the console code never leaves the process ------------------------
    if not admin.OPEN_ACTIONS <= set(admin.ACTIONS):
        bad += fail("OPEN_ACTIONS names something that is not an action")
    for a in ("place", "vendor-scope", "vendor-scope-all", "move", "merge",
              "patch", "set-founded", "confirm-founded", "dismiss"):
        if a in admin.OPEN_ACTIONS:
            bad += fail(f"{a!r} writes a ruling and is exempt from the code")
    return bad


def check_admin_guards() -> int:
    """Two invariants the admin states in comments, said here as tests.

    Both were true of the words and not of the code, which is the only reason
    this function exists: an invariant nothing checks is a comment.

    - WHAT COUNTS AS INSIDE. The admin fetches whatever a person pastes and
      hands the answer back, so the address is the whole question. The four
      shapes at the bottom of INSIDE are the ones a hand-written list of
      private ranges misses - carrier NAT is not private by any flag, and a
      6to4 address is a global address with a loopback passenger - and all
      four were fetched. They are here rather than in the guard's comment
      because the next person to simplify _public() will run this.

    - WHAT COUNTS AS AN ID. A company id is a filename everywhere it travels.
      The pattern ended in $, which also matches before a trailing newline, so
      "tyler-tech\\n" was a legal id as far as validate() could tell.
    """
    import socket
    import ipaddress
    import admin
    bad = 0

    # A PATCH THAT CHANGES NOTHING MUST SAY SO. act_patch reads body["fields"],
    # and for a body with no usable fields it fell through the loop and still
    # returned {"ok": True, "message": "updated <name>"}. So a caller that put
    # the field at the top level - which is how every other action in this file
    # takes its arguments - was told the correction landed while the record was
    # untouched. That is the worst shape a bug can take here: the person moves
    # on believing a wrong fact is fixed, and nothing ever contradicts them.
    # A REAL id, or every case below returns "company not found" and the test
    # passes without ever reaching the logic it claims to pin. None of these
    # four can write: each is rejected before the loop that assigns fields.
    real = json.load(open(DATA / "companies.json"))[0]["id"]
    for body, why in (
            ({"id": real}, "no fields at all"),
            ({"id": real, "website": "https://a.example"}, "field at top level"),
            ({"id": real, "fields": {}}, "empty fields dict"),
            ({"id": real, "fields": {"govtech": True}}, "only un-patchable keys")):
        got = admin.act_patch(dict(body))
        if got.get("ok"):
            bad += fail(f"act_patch: reported success for {why} - {got}")
    # NOT satisfied by refusing everything - but proved WITHOUT a write. The
    # first version of this asserted the positive case by actually patching a
    # real company, which mutated companies.json on every selftest run and
    # journalled it as the owner. A test that edits the live dataset to prove
    # editing works is the same mistake as the build agent that put 86 writes
    # into companies.json while testing the scoring belt.
    #
    # So: assert the refusal came from the FIELDS check rather than from the
    # id lookup. Only the new path names what is editable, so a regression that
    # reverts to a silent no-op cannot pass this, and neither can one that
    # rejects every patch outright.
    got = admin.act_patch({"id": real, "fields": {}})
    if "Editable:" not in (got.get("error") or ""):
        bad += fail("act_patch: an empty patch must be refused by the fields "
                    f"check and say what is editable - got {got}")
    missing = admin.act_patch({"id": "no-such-company-here", "fields": {}})
    if "Editable:" in (missing.get("error") or ""):
        bad += fail("act_patch: a missing company must fail the id lookup "
                    "first, or the fields check is being reached with no "
                    f"record to patch - got {missing}")

    INSIDE = [
        ("127.0.0.1", "loopback"),
        ("10.0.0.1", "private"),
        ("192.168.1.1", "private"),
        ("172.16.0.1", "private"),
        ("169.254.169.254", "the cloud metadata address, over link-local"),
        ("0.0.0.0", "unspecified"),
        ("::1", "loopback"),
        ("fd00::1", "unique local"),
        ("fe80::1", "link local"),
        ("::ffff:127.0.0.1", "v4-mapped loopback"),
        # the four that got through
        ("100.64.1.1", "RFC 6598 carrier NAT"),
        ("100.127.255.254", "RFC 6598 carrier NAT, far end"),
        ("2002:7f00:1::", "6to4-encoded 127.0.0.1"),
        ("2002:a00:1::", "6to4-encoded 10.0.0.1"),
    ]
    # the other half of the rule: this is a guard, not a ban on the internet
    OUTSIDE = [
        ("8.8.8.8", "an ordinary public address"),
        ("2606:4700::1111", "an ordinary public v6 address"),
        ("2002:808:808::", "6to4 wrapping public 8.8.8.8, which is a real tunnel"),
    ]
    for text, why in INSIDE:
        if admin._public(ipaddress.ip_address(text)):
            bad += fail(f"admin would fetch {text} - {why}")
    for text, why in OUTSIDE:
        if not admin._public(ipaddress.ip_address(text)):
            bad += fail(f"admin refuses {text} - {why}, and it is on the internet")

    # A predicate nothing calls is not a guard, so both gates that use it get
    # asked directly. Every host here is a literal, so nothing resolves and
    # this stays offline.
    for text, why in INSIDE:
        if ":" in text:
            # clean_url wants a dotted host and a bare v6 literal has no dot,
            # which is why the original report spelled 6to4 with a trailing
            # 0.0.0.0. Use that spelling, so the dotted-host rule is not what
            # does the work here and _public is genuinely the gate under test.
            host = text + "0.0.0.0" if text.endswith("::") else text
            url = f"http://[{host}]/"
        else:
            url = f"http://{text}/"
        if admin.clean_url(url) is not None:
            bad += fail(f"clean_url accepted {url} - {why}")
        try:
            with admin.only_public_hosts():
                socket.getaddrinfo(text, 80, proto=socket.IPPROTO_TCP)
            bad += fail(f"the connect gate would dial {text} - {why}")
        except admin.PrivateAddress:
            pass
        except socket.gaierror:
            bad += fail(f"the connect gate could not read {text} as an address")

    for good in ("tyler-tech", "3di", "a", "motorola-solutions"):
        if not admin.ID_OK.match(good):
            bad += fail(f"ID_OK refuses {good!r}, which is an ordinary id")
    for evil in ("abc\n", "abc\n\n", "abc\r", "-abc", "ABC", "a b", "a/b",
                 "../etc", "a.b", "", "abc\nx"):
        if admin.ID_OK.match(evil):
            bad += fail(f"ID_OK accepts {evil!r} as a company id")
    # and the pattern still has to admit every id actually on file, or the
    # next write to any of them is refused
    for c in json.load(open(DATA / "companies.json")):
        if not admin.ID_OK.match(c["id"]):
            bad += fail(f"ID_OK refuses {c['id']!r}, which is on file")
            break
    return bad


def check_redirect_hop() -> int:
    """The guard has to see the SECOND hop, not only the url someone typed.

    This is the whole reason only_public_hosts() patches the resolver instead
    of checking the string: the fetchers follow redirects, so the person who
    chose the first hop did not choose the second, and the second is where a
    redirect into this network goes. That argument was written down and never
    tested, and it is the half a url-shaped check cannot do at all.

    Nothing leaves this machine. One thing is staged, and it has to be: hop 1
    has to live on loopback, because loopback is the only address this machine
    can serve - and loopback is exactly what the guard refuses. So _public is
    wrapped to answer True for 127.0.0.1 and to hand every other address to
    the real rule. The refusal below therefore comes from the real rule,
    firing on a redirect target nobody typed.
    """
    import http.server
    import threading

    import requests

    import admin

    target = "http://169.254.169.254/latest/meta-data/"   # cloud metadata

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    # port 0: the OS picks a free one, so two runs at once cannot collide
    srv = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    real_public, asked = admin._public, []

    def hop_one_is_public(ip):
        asked.append(str(ip))
        return True if str(ip) == "127.0.0.1" else real_public(ip)

    session = requests.Session()
    # an http_proxy in the environment would resolve the second hop for us and
    # this would pass without the guard doing anything, which is the one way
    # this test could lie
    session.trust_env = False

    admin._public = hop_one_is_public
    bad = 0
    try:
        with admin.only_public_hosts():
            r = session.get(f"http://127.0.0.1:{port}/careers", timeout=5)
        bad += fail(f"the guard followed a redirect all the way to {r.url}")
    except requests.exceptions.ConnectionError as exc:
        if "169.254.169.254" not in str(exc):
            bad += fail(f"the redirect died, but not on the guard: {exc}")
    except Exception as exc:                       # noqa: BLE001 - report it
        bad += fail(f"the redirect check broke: {type(exc).__name__}: {exc}")
    finally:
        admin._public = real_public
        session.close()
        srv.shutdown()
        srv.server_close()

    # and it was genuinely asked, rather than the fetch failing for its own
    # reasons somewhere before the second hop
    if "169.254.169.254" not in asked:
        bad += fail("the guard was never asked about the redirect target - it "
                    f"only ever saw {asked}")
    return bad


def check_admin_http() -> int:
    """What the admin says on the wire, asked of a real server on loopback.

    Three of these were true in a comment and false in the code, which is the
    pattern this whole file exists to break. They are checked here rather than
    by reading admin.py for a string, because the thing that matters is the
    reply a browser gets, and a header can be set on a path nothing takes.

    - A WRITE NEEDS THE TOKEN. That secret is the entire answer to "any site
      the owner visits could drive the admin": a cross-origin page can send a
      request but cannot attach a custom header without a preflight, and this
      server answers none.
    - A NON-ASCII TOKEN IS AN ANSWER, NOT A DROPPED CONNECTION. compare_digest
      raises TypeError on a str with a byte over 0x7f, and http.server decodes
      headers as latin-1, so `X-Admin-Token: cafe\\xe9` used to kill the
      request mid-flight and read as "the admin is down".
    - IT REFUSES TO BE FRAMED. The token does not help against a click on our
      own UI: a framed admin document is on the admin's own origin and carries
      the token itself. Refusing the frame is the only thing that does.
    - AND THE TOKEN IS NOT HANDED TO A WEB PAGE. /api/token needs no token of
      its own, so the Origin a browser attaches and cannot drop is what keeps
      it to the capture extension and off every website.

    Loopback and nothing else - no network, no data written, no port picked by
    hand.
    """
    import http.client
    import http.server
    import threading
    import urllib.error
    import urllib.request

    import admin

    class Quiet(admin.Handler):
        def log_message(self, *a):                 # keep selftest output clean
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bad = 0

    def ask(path, headers=None, method="GET", body=None):
        """(status, headers, body), with status None for no reply at all.

        The dropped connection is a result here rather than an exception on
        purpose: it is the exact symptom of the non-ASCII bug below, and a
        traceback out of selftest would report it as "selftest is broken"
        instead of "the admin drops the request".
        """
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            return None, {}, f"no reply: {type(e).__name__}: {e}".encode()

    try:
        code, _, _ = ask("/api/queues")
        if code != 403:
            bad += fail(f"a /api/ read with no token answered {code}, not 403")

        # the byte over 0x7f is the whole case, and a dropped connection is
        # the failure being watched for, not a 200
        code, _, body = ask("/api/queues", {"X-Admin-Token": "café"})
        if code is None:
            bad += fail(f"a non-ASCII token killed the request: {body.decode()}")
        elif code != 403:
            bad += fail(f"a non-ASCII token answered {code}, not 403")
        elif b"token" not in body:
            bad += fail(f"a non-ASCII token got no reason back: {body[:80]!r}")

        # deliberately the cheapest authed route rather than /api/queues:
        # building all seven queues over 2,108 companies took two seconds and
        # this is testing the lock, not what is behind it
        code, _, _ = ask("/api/agree", {"X-Admin-Token": admin.TOKEN})
        if code != 200:
            bad += fail(f"the real token was refused: {code}")

        for path in ("/", "/api/queues"):
            _, head, _ = ask(path)
            if head.get("X-Frame-Options") != "DENY":
                bad += fail(f"{path} can be framed: X-Frame-Options "
                            f"{head.get('X-Frame-Options')!r}")
            if "frame-ancestors 'none'" not in (head.get("Content-Security-Policy") or ""):
                bad += fail(f"{path} has no frame-ancestors rule")

        code, _, _ = ask("/api/token")
        if code != 200:
            bad += fail(f"the capture extension cannot get a token: {code}")
        # a browser attaches this itself and a page cannot drop it
        code, _, _ = ask("/api/token", {"Origin": "https://evil.example"})
        if code != 403:
            bad += fail(f"/api/token answered a web page with {code}, not 403")

        # a write, in the shape a page can send with no preflight at all
        code, _, _ = ask("/api/patch", {"Content-Type": "text/plain"},
                         "POST", b'{"id":"tyler-technologies","name":"owned"}')
        if code != 415:
            bad += fail(f"a text/plain write answered {code}, not 415")

        # THE RULING GATE. A local script can get a token by asking for one -
        # that route exists for the capture extension and is not the hole.
        # The hole was that the token was also permission to RULE, and an
        # agent made 86 rulings with one. A ruling now needs the code printed
        # on the console, which nothing serves.
        #
        # The body is empty on BOTH probes, and that is not laziness. An
        # earlier draft sent a real ruling here - keep:true on a real company
        # - and relied on the gate to refuse it. The moment the gate was
        # broken to check that this test notices, the test itself wrote a
        # dismissal into the owner's live admin_dismissed.json. A test whose
        # safety depends on the thing it is testing is the wrong shape: what
        # is being asserted is the status code, so the body must be one that
        # writes nothing even if it gets all the way through.
        authed = {"X-Admin-Token": admin.TOKEN,
                  "Content-Type": "application/json"}
        code, _, body = ask("/api/place", authed, "POST", b'{}')
        if code != 403:
            bad += fail(f"a ruling with a token and no console code answered "
                        f"{code}, not 403 - this is the 86-ruling hole")
        elif b"code_required" not in body:
            bad += fail("the ruling refusal does not say a code is needed")
        # And the code, supplied, gets THROUGH the gate: act_place refuses it
        # on its own terms, which is proof the gate opened and proof nothing
        # was written.
        code, _, body = ask("/api/place", {**authed,
                                           "X-Admin-Code": admin.CONSOLE_CODE},
                            "POST", b'{}')
        if code == 403 and b"code_required" in body:
            bad += fail("the real console code was refused by its own gate")
        elif b"need a company id" not in body:
            bad += fail(f"a coded ruling did not reach the action: {body[:90]!r}")
        # the extension's one write must NOT have been locked out with it
        if "capture" not in admin.OPEN_ACTIONS:
            bad += fail("the capture extension can no longer write")

        # THE CODE IS IN NOTHING THE SERVER SENDS. Not in the page, not in the
        # shim, not on /api/token. If it ever ships, a script reads it out of
        # curl and the gate is decoration.
        # /api/schema and /api/agree, not /api/queues: building all thirteen
        # queues over 2,108 companies is slow AND has a side effect - the
        # founding-year queue harvests provenance out of the journal and
        # writes it - and selftest must not write to data/ at all.
        secret = admin.CONSOLE_CODE.encode()
        for path, hdrs in (("/", None), ("/api/token", None),
                           ("/api/schema", {"X-Admin-Token": admin.TOKEN}),
                           ("/api/agree", {"X-Admin-Token": admin.TOKEN})):
            _, _, body = ask(path, hdrs)
            if secret in body:
                bad += fail(f"{path} hands out the console code - anything "
                            f"that can fetch it can now rule")

        # The CSV is no longer gated on anything but the token. It used to open
        # at a board-health threshold, and a review took that threshold four
        # ways - the cheapest being one bulk call writing 240 rulings in zero
        # seconds. An export of your own data was never a prize worth guarding.
        code, _, body = ask("/api/export.csv", {"X-Admin-Token": admin.TOKEN})
        if code != 200:
            bad += fail(f"/api/export.csv answered {code} for an authenticated "
                        f"caller - it is not gated any more")
        code2, _, _ = ask("/api/export.csv", {})
        if code2 == 200:
            bad += fail("/api/export.csv answered an unauthenticated caller")
    finally:
        srv.shutdown()
        srv.server_close()
    return bad


def check_url_sinks() -> int:
    """Every href built from data goes through safeUrl, on both shipped pages.

    esc() cannot save an href. "javascript:alert(1)" contains not one character
    esc() touches - the scheme IS the payload - and the link runs on our own
    origin the moment somebody clicks it. Only an allowlist of schemes closes
    it, which is what safeUrl is.

    This was live on alerts.html, and that is the worst page to have it on: a
    saved role's url starts on somebody else's ATS, goes up into a subscription
    record and comes back down into a link, on the one page that holds the
    settings token in memory. The token is the whole identity there, so one
    click would have handed over the subscription.

    Checked as a shape rather than by running the page, because there is no
    JS engine here and the shape is the part that regresses: someone adds a
    link, reaches for esc() because every other interpolation uses it, and the
    hole is back with no visible difference.
    """
    bad = 0
    for name in ("index.html", "alerts.html"):
        src = (ROOT / name).read_text()
        if "function safeUrl(" not in src:
            bad += fail(f"{name} has no safeUrl(), so nothing filters a scheme")
            continue
        # an allowlist of two, never a blocklist of schemes: a blocklist has to
        # know about javascript:, data:, vbscript:, blob: and whatever is next
        if ('p.protocol==="http:"' not in src
                or 'p.protocol==="https:"' not in src):
            bad += fail(f"{name}'s safeUrl no longer allowlists http and https")
        # names that hold an already-filtered url, so `href="${esc(purl)}"`
        # counts as safe without repeating the call at the sink
        safe = set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*safeUrl\(", src))
        sinks = re.findall(r'href="\$\{([^}]*)\}', src)
        if not sinks:
            bad += fail(f"{name} builds no href from data any more - if that is "
                        f"deliberate, this check has outlived the page")
        for expr in sinks:
            if "safeUrl(" in expr:
                continue
            m = re.fullmatch(r"esc\((\w+)\)", expr.strip())
            if m and m.group(1) in safe:
                continue
            bad += fail(f"{name} builds an href from {expr!r} with no safeUrl "
                        f"behind it - esc() does not stop a javascript: scheme")
    return bad


def check_merged_names_stay_merged() -> int:
    """A record a merge deleted must not be live again.

    A merge folds one company into another and deletes the dropped id. The
    journal records that as a change with a `before` and a null `after`, which
    makes "every id a merge has ever removed" an exact set - not a guess.

    This exists because one came back. merge_families folded the NRPA 2026
    booth row "Xplor Recreation I Vermont Systems - RecDesk - NextRec - ePACT"
    into Xplor Recreation at 11:35 - correctly, it is four brands in one
    exhibitor cell rather than a company - and within the hour an intake path
    had re-created it from the conference source as a fresh unresearched
    record. Nothing errored. The journal held one entry for the merge and none
    for the resurrection, because seven pipeline scripts write companies.json
    directly instead of through save_companies, and only admin.py was ever
    covered by the rule that every write keeps a before-image.

    Repairing those seven is the real fix and this is not it. This is the
    backstop that makes the failure LOUD wherever it comes from, so a restored
    duplicate fails the next build instead of surviving because the only person
    who would recognise it is the one who did the merge.

    Keyed on the ID a merge deleted, NOT on `also_known_as`. The first version
    of this check used the alias list and flagged EagleView and Concourse,
    which are not resurrections: two live records that legitimately carry each
    other's name while somebody decides whether they are one company. That is
    the Duplicates queue's job, and a build failure is the wrong severity for
    a question nobody has answered yet.
    """
    errors = 0
    companies = json.load(open(DATA / "companies.json"))
    live = {c["id"] for c in companies}
    jpath = DATA / "admin_journal.jsonl"
    if not jpath.exists():
        return 0
    dropped = {}
    for line in jpath.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        for cid, ch in (d.get("changes") or {}).items():
            if not isinstance(ch, dict):
                continue
            if "after" in ch and ch["after"] is None and ch.get("before"):
                dropped[cid] = d          # last word wins: a later re-add
            elif cid in dropped and ch.get("after"):
                dropped.pop(cid, None)    # deliberately restored, journalled
    for cid, entry in sorted(dropped.items()):
        if cid in live:
            errors += fail(
                f"merge: {cid!r} was deleted by a {entry.get('action')} on "
                f"{entry.get('at', '')[:10]} ({entry.get('by')}) and is live "
                f"again with nothing in the journal restoring it. Something "
                f"wrote companies.json outside save_companies. Merge it again, "
                f"then find the writer.")
    return errors


def check_writes_name_their_author() -> int:
    """No admin write may fall back to the default author.

    save_companies() defaults `by` to "owner" because most writes are his.
    That default is a trap for every write that is not: eight actions called
    it with the action name alone, so an agent's patch, an extension's capture
    and a script's identity ruling were all journalled as rulings the owner
    made. It is not a cosmetic mislabel - the journal is what admin_undo reads,
    what re-attribution works from, and what will one day decide which labels
    a classifier can trust. 86 agent writes already had to be re-attributed by
    hand, and re-attribution is a thing somebody has to remember to do.

    Source-level, because the failure is a missing argument: no call runs in a
    test, so nothing at runtime can catch the one somebody forgets next.
    """
    src = (ROOT / "scripts" / "admin.py").read_text()
    bad = 0
    # Paren-BALANCED. The first version matched [^)]* and stopped at the first
    # ")", so every call whose arguments contain parens - which is all of them,
    # they read (body.get("why") or "") - was truncated before `by=` and
    # reported as a failure. A check that cries wolf on 12 correct calls is one
    # somebody turns off.
    for m in re.finditer(r"\bsave_companies\(", src):
        i, depth = m.end(), 1
        while i < len(src) and depth:
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        args = src[m.end():i - 1]
        if "by=" in args or args.lstrip().startswith("companies: list"):
            continue        # the definition itself is not a call
        if not args.strip():
            continue        # "save_companies()" written in prose, not called
        line = src[:m.start()].count("\n") + 1
        snippet = " ".join(args.split())[:70]
        bad += fail(f"admin.py:{line}: save_companies({snippet}) does not pass "
                    f"`by`, so this write will be journalled as the owner's "
                    f"whoever actually made it")
    return bad


def check_header_shared() -> int:
    """index.html and alerts.html must wear the same header.

    There is no build step here on purpose, so the band is restated in each
    file rather than templated. Restated duplication is the thing that rots:
    somebody changes the brand colour in one file, the other keeps the old
    one, and a reader crossing between them sees two different products. It
    is the same failure the brand.json / _brand.js guard and the alerts
    vocabulary guard exist for, so it gets the same treatment.

    Checked: the four header tokens hold identical values, and both pages
    point the mark at the same asset. NOT checked: layout, which is allowed
    to differ - alerts.html has no tab strip and should not grow one.
    """
    errors = 0
    pages = {}
    for name in ("index.html", "alerts.html"):
        src = (ROOT / name).read_text()
        toks = dict(re.findall(r"(--hdr-[a-z]+):\s*(#[0-9A-Fa-f]{3,8})", src))
        mark = re.search(r'class="mark"\s+src="([^"]+)"', src)
        pages[name] = (toks, mark.group(1) if mark else None)

    want = {"--hdr-bg", "--hdr-ink", "--hdr-mute", "--hdr-line"}
    for name, (toks, mark) in pages.items():
        missing = want - set(toks)
        if missing:
            errors += fail(f"{name}: header tokens missing {sorted(missing)} - "
                           f"the shared header band is not defined there")
        if not mark:
            errors += fail(f"{name}: no <img class=\"mark\"> - the mascot is "
                           f"the way home from every page")

    a, b = pages["index.html"], pages["alerts.html"]
    for tok in sorted(want):
        va, vb = a[0].get(tok), b[0].get(tok)
        if va and vb and va.lower() != vb.lower():
            errors += fail(f"header drift: {tok} is {va} in index.html and "
                           f"{vb} in alerts.html. One header, one value.")
    if a[1] and b[1] and a[1] != b[1]:
        errors += fail(f"header drift: the mark is {a[1]} in index.html and "
                       f"{b[1]} in alerts.html")
    return errors


def check_identity_guard() -> int:
    """identifies() is what stands between a squatter and the dataset.

    Both of its failures are here because both were real, and they fail in
    opposite directions:

    A FALSE PARKED VERDICT on a real page. `sedo` sat in PARKED as a bare
    unanchored alternative, so it matched inside "onmou-SEDO-wn" - and a
    WordPress lazy-load listener puts "mousedown" in the first 4KB of a great
    many company homepages. bentley.com and soilflo.com were both rejected on
    that basis. It fails in the honest direction, reporting "no website
    found", and it is still wrong: the page is real and names the company in
    its own title.

    A FALSE PASS on a squatter. identifies() read only html[:4000], and a
    DomainMarket listing for vocaltechnologies.com carries "Technology Domains
    for Sale" IN ITS TITLE - pushed to byte 4162 by inline tracking script. It
    missed by 162 bytes, and then satisfied the identity check outright,
    because a for-sale headline containing both of the company's name tokens
    is the entire business model of a domain squatter.
    """
    import find_websites as fw
    errors = 0

    REAL = [
        # (html, name, base) - a real page that must NOT read as parked
        ('<title>Bentley Systems | Infrastructure Engineering Software</title>'
         '<script>el.addEventListener("mousedown",f)</script>',
         "Bentley Systems", "bentley"),
        ('<title>SoilFLO | Soil Tracking Software</title>'
         '<script>window.onmousedown=null</script>', "SoilFLO", "soilflo"),
        # ordinary words that contain a vendor name as a substring
        ('<title>Acme Closedown Services</title><p>used online by cities</p>',
         "Acme Closedown Services", "acmeclosedown"),
    ]
    for html, name, base in REAL:
        if fw._parked(html):
            errors += fail(f"identity: {name!r} page read as PARKED - a real "
                           f"page rejected because a guard matched inside an "
                           f"ordinary word")
        if not fw.identifies(html, name, base):
            errors += fail(f"identity: {name!r} not identified on its own page")

    PARKED_PAGES = [
        # the tell is past the 4KB window but sits in the title
        ("<script>" + "x" * 4200 + "</script><title>VocalTechnologies.com - "
         "Technology Domains for Sale - Buy Premium Tech Domain Names</title>",
         "VOCAL Technologies Inc.", "vocaltechnologies"),
        ("<title>This domain is listed at Sedo</title>", "Anything", "anything"),
        ("<title>HugeDomains.com - Shop for over 300,000 Premium Domains</title>",
         "Liberty Mobility Now", "libertymobilitynow"),
    ]
    for html, name, base in PARKED_PAGES:
        if not fw._parked(html):
            errors += fail(f"identity: a for-sale page for {name!r} was not "
                           f"recognised as parked")
        if fw.identifies(html, name, base):
            errors += fail(f"identity: identifies() PASSED a squatter page for "
                           f"{name!r} - this is the one thing it exists to stop")
    return errors


def check_beak_is_never_text() -> int:
    """Beak may colour a border or a background. Never text.

    data/brand.json says it in one line - "Beak is never text" - and CLAUDE.md
    restates it. A header rebuild broke it in seven places at once, because
    Beak is the obvious colour to reach for on a Penguin band and the contrast
    ratio it produces (7.5:1) passes every automated check there is.

    That is exactly why this exists. The rule is not "keep it readable", it is
    a decision the kit already made about what this colour is FOR, and a
    passing contrast ratio is not permission to overrule it. Nothing at
    runtime can catch a colour that looks fine.
    """
    import re
    errors = 0
    # `color:` but not `border-color:` / `background-color:` / `outline-color:`
    bad = re.compile(r"(?<![-\w])color\s*:\s*var\(\s*--beak\s*\)", re.I)
    for name in ("index.html", "alerts.html"):
        src = (ROOT / name).read_text()
        for m in bad.finditer(src):
            line = src[:m.start()].count("\n") + 1
            ctx = src[max(0, m.start() - 60):m.start()].splitlines()[-1:]
            errors += fail(f"{name}:{line}: --beak used as a TEXT colour"
                           f"{' in ' + ctx[0].strip() if ctx else ''} - "
                           f"brand.json says Beak is never text. Use it on a "
                           f"border or a background instead.")
    return errors


def check_journal_matches_reality() -> int:
    """What the journal last recorded is what the file should still say.

    check_merged_names_stay_merged catches a DELETED RECORD coming back. It
    does not catch a FIELD being reverted, and the same unjournalled write did
    both: merge_families gave Xplor Recreation four researched brands at
    2026-08-24#93, and by #97 they were gone with nothing in between. One
    stale snapshot of companies.json, two symptoms, and only the resurrected
    record was noticed at the time. The brands were recoverable only because
    the journal still held #93's after-image.

    So: replay the journal, take the last after-image per company, and compare
    the fields ADMIN OWNS. Anything that differs was changed by something that
    did not journal itself - which is seven pipeline scripts writing
    companies.json directly, the hole CLAUDE.md now names.

    Deliberately NOT every field. `hiring` is refresh.py's and changes every
    run by design; flagging it would bury the real signal in noise, and a
    check that cries wolf is one somebody turns off.
    """
    OWNED = ("brands", "also_known_as", "parent", "board_owner", "year_founded",
             "location", "website", "sector", "category", "description")
    jpath = DATA / "admin_journal.jsonl"
    if not jpath.exists():
        return 0
    last = {}
    for line in jpath.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        for cid, ch in (d.get("changes") or {}).items():
            if isinstance(ch, dict) and ch.get("after"):
                last[cid] = (d["id"], ch["after"])

    live = {c["id"]: c for c in json.load(open(DATA / "companies.json"))}
    errors = 0
    for cid, (eid, after) in sorted(last.items()):
        cur = live.get(cid)
        if cur is None:
            continue          # deletion is the other check's job
        for f in OWNED:
            if f not in after:
                continue
            if after[f] != cur.get(f):
                was = "set" if after[f] else "empty"
                now = "set" if cur.get(f) else "EMPTY"
                errors += fail(
                    f"journal: {cid}.{f} was {was} by {eid} and is {now} now, "
                    f"with nothing in the journal changing it. Something wrote "
                    f"companies.json outside save_companies. Recover the value "
                    f"from {eid}'s after-image.")
    return errors


def check_rating_scale() -> int:
    """index.html and functions/api/rate.js must agree on the scale.

    The page draws the buttons and the Worker validates the score, and they
    are different files in different languages - the same shape as the alerts
    vocabulary, which is guarded here for the same reason. Drift is silent and
    total: the page offers a 1-10 button, the endpoint rejects anything over
    5, and every vote past halfway fails with an error nobody sees coming.

    Also pins MIN_SHOWN, because it is a promise about honesty rather than a
    preference. Below it the API returns no average, and the page says how
    many more it needs. Somebody lowering it to 1 turns a single anonymous
    rating into a published "9.0/10", which is the first thing astroturf
    reaches for.
    """
    import re
    errors = 0
    api = (ROOT / "functions" / "api" / "rate.js").read_text()
    page = (ROOT / "index.html").read_text()

    def const(name, src):
        m = re.search(rf"^const {name}\s*=\s*(\d+);", src, re.M)
        return int(m.group(1)) if m else None

    lo, hi, floor = const("MIN", api), const("MAX", api), const("MIN_SHOWN", api)
    if lo is None or hi is None or floor is None:
        errors += fail("rate.js: MIN / MAX / MIN_SHOWN must each be a plain "
                       "`const NAME = <int>;` so this guard can read them")
        return errors

    # the page builds exactly `hi` buttons and labels the scale `/hi`
    n = re.search(r"Array\.from\(\{length:(\d+)\}", page)
    if not n or int(n.group(1)) != hi:
        errors += fail(f"rating scale: rate.js accepts {lo}-{hi} but index.html "
                       f"draws {n.group(1) if n else 'an unknown number of'} "
                       f"buttons. A vote the page offers and the endpoint "
                       f"refuses fails with an error nobody sees coming.")
    if f"/{hi}</small>" not in page:
        errors += fail(f"rating scale: index.html does not print '/{hi}' beside "
                       f"the average, so the page and the endpoint disagree "
                       f"about what the number means")
    if floor < 3:
        errors += fail(f"rating floor: MIN_SHOWN is {floor}. Under 3, one "
                       f"anonymous rating publishes as an average - a badge, "
                       f"not a measurement, and the shape astroturf takes "
                       f"first.")
    return errors


def check_every_company_says_what_it_sells() -> int:
    """A company on the public map must say what it sells.

    validate() has never required a description, and every one of the 2,103
    companies has one anyway - the convention is 100% honoured and 0% enforced,
    which is the state a rule is in right before it breaks.

    It nearly did. A conference sweep produced 95 companies worth intaking and
    only 39 carried a line about what they sell; the other 56 would have
    entered as structurally valid records that render on the public Companies
    tab as a name and nothing else. Nothing would have errored.

    CLAUDE.md's rule is one line, what they sell and to whom. This does not
    police the prose - it only refuses the empty.
    """
    errors = 0
    companies = json.load(open(DATA / "companies.json"))
    blank = [c["id"] for c in companies if not (c.get("description") or "").strip()]
    for cid in blank[:12]:
        errors += fail(f"company {cid!r} has no description. It would render on "
                       f"the public Companies tab as a name and nothing else.")
    if len(blank) > 12:
        errors += fail(f"... and {len(blank) - 12} more companies with no "
                       f"description")
    return errors


def check_unreachable_names_the_failure() -> int:
    """A broken certificate chain must not be recorded as a dead site.

    kunzleigh.com sells state WIC management systems and Medicaid
    third-party-liability modules - exactly what this board exists to find.
    add_company.fetch() returned 0 bytes on https, on www and on http, and
    curl gets HTTP 200 with 275KB. Their server sends the leaf certificate and
    omits the intermediate; curl fetches the missing link itself over AIA and
    `requests` does not.

    The note said "unreachable: SSLError", the honest-failure path filed it as
    no website found, and a live company disappeared. That is the "blocked is
    not a zero" rule in a new place: a transport failure that LOOKS like
    absence, and the one shape of it that a person can fix in a minute.

    So the four failures are named separately. A DNS miss means the host does
    not exist. A timeout means it did not answer. A broken chain means it
    answered perfectly and we refused to trust it.
    """
    import add_company
    import ssl
    errors = 0
    cases = [
        (ssl.SSLCertVerificationError(
            "certificate verify failed: unable to get local issuer certificate"),
         "tls_chain", "an incomplete chain is recoverable and must say so"),
        (TimeoutError("connection timed out"), "timeout",
         "a host that did not answer is not a host that does not exist"),
        (Exception("Name or service not known"), "dns",
         "a host that does not resolve is a different fact"),
        (ValueError("something else entirely"), "unreachable",
         "anything unrecognised keeps the old, honest label"),
    ]
    for exc, want, why in cases:
        got = add_company._why_unreachable(exc)
        if not got.startswith(want):
            errors += fail(f"fetch: {type(exc).__name__} -> {got[:40]!r}, "
                           f"expected it to start {want!r}. {why}")
    return errors


def check_search_routes_are_live() -> int:
    """Every search phrase must land on a sector and category that exist.

    "hhs" used to route to two places: the Health & Human Services sector and
    a Health & Human Services category inside General Gov, because the dataset
    filed the same thing twice. When the 38 companies moved out and the
    duplicate category was deleted, that second route still sat in
    semantic.py and in the copy of it inside index.html.

    A dead route is the quietest kind of wrong. It throws nothing and shows
    nothing - a reader types the word, the filter matches no company, and the
    board says there is no work here. That is the asymmetric error this
    project is built to refuse, arriving through the search box instead of
    the crawler.

    So the routes are checked against schema.json, both copies, every run.
    """
    import json
    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    cats = {s["name"]: set(s["categories"]) for s in schema["sectors"]}
    errors = 0

    import semantic
    routes = [(e["say"][0], s, c)
              for e in semantic.CONCEPTS for s, c in e["go"]]
    if not routes:
        return fail("semantic.CONCEPTS is empty; the search map did not load")

    for phrase, sector, category in routes:
        if sector not in cats:
            errors += fail(f"search {phrase!r} routes to sector {sector!r}, "
                           f"which is not in schema.json")
        elif category is not None and category not in cats[sector]:
            errors += fail(f"search {phrase!r} routes to {sector} / "
                           f"{category!r}, a category that sector does not "
                           f"have. A reader typing that word sees an empty "
                           f"board and reads it as no jobs")

    # index.html carries its own copy of the map, and both copies get edited by
    # hand when a sector changes. Substring probing was too weak to prove they
    # agree - it could only say a route was ABSENT, never that one copy had
    # gained something the other did not. So the page's map is parsed and
    # compared entry for entry, in order.
    import re as _re
    html = (ROOT / "index.html").read_text()
    m = _re.search(r"/\* CONCEPT-MAP \*/(.*?)/\* /CONCEPT-MAP \*/", html, _re.S)
    if not m:
        return errors + fail("index.html no longer carries a CONCEPT-MAP block; "
                             "the search map cannot be checked against "
                             "semantic.py")
    try:
        in_page = json.loads(m.group(1).rstrip().rstrip(";").rstrip())
    except json.JSONDecodeError as exc:
        return errors + fail(f"the CONCEPT-MAP inside index.html is not valid "
                             f"JSON ({exc}); search would break at runtime")

    def shape(e):
        return (tuple(e["say"]), tuple(tuple(g) for g in e["go"]))
    py_set = {shape(e) for e in semantic.CONCEPTS}
    html_set = {shape(e) for e in in_page}
    for e in semantic.CONCEPTS:
        if shape(e) not in html_set:
            errors += fail(f"search phrase {e['say'][0]!r} is in semantic.py "
                           f"but not in index.html. The two copies of the map "
                           f"have drifted, and only the page's copy is what a "
                           f"reader actually searches")
    for e in in_page:
        if shape(e) not in py_set:
            errors += fail(f"search phrase {e['say'][0]!r} is in index.html "
                           f"but not in semantic.py, so nothing server-side "
                           f"knows the route exists")
    return errors



def check_calendar_dates_survive_the_round_trip() -> int:
    """The .ics a reader downloads must hold the week the conference runs.

    This one is executed, not read. A text assertion would have passed every
    version of the bug that matters: DTEND on an all-day VEVENT is EXCLUSIVE,
    so a conference ending the 21st needs DTEND 22 or every calendar app
    silently drops the last day. Nothing about the source looks wrong when it
    is wrong.

    The parser's job is also to REFUSE. "Conference: November 17-19, 2026;
    Expo: November 18-19, 2026" is two ranges in one field and there is no
    honest way to pick one, so that row gets no button at all. That is the
    same asymmetry the crawler runs on: a missing button is visible, a
    plausible wrong week is not.

    node is not a project dependency. If it is absent the check says so and
    passes - a missing tool is not a broken board.
    """
    import shutil, subprocess, json as _json
    if not shutil.which("node"):
        note("node not installed; the .ics parser was not executed this run")
        return 0

    html = (ROOT / "index.html").read_text()
    a = html.find("const CAL_MONTHS=")
    b = html.find("function downloadIcs(")     # needs a DOM, stays out
    if a < 0 or b < 0 or b <= a:
        return fail("index.html: could not find the .ics parser to test it")

    cases = [
        # dates,                                    start,      end (exclusive)
        ["October 17-21, 2026",                    "20261017", "20261022"],
        ["September 30 - October 3, 2026",         "20260930", "20261004"],
        ["June 26 - July 1, 2027",                 "20270626", "20270702"],
        ["August 7 - 11, 2027",                    "20270807", "20270812"],
        ["March 1-4, 2027",                        "20270301", "20270305"],
        ["December 8-10, 2026",                    "20261208", "20261211"],
    ]
    refuse = [
        "Conference: November 17-19, 2026; Expo: November 18-19, 2026",
        "February 30, 2026",          # Date() would roll this to March 2
        "October 21-17, 2026",        # runs backwards
        "December 30 - January 2, 2027",   # year rollover is ambiguous
        "Spring 2027",
        "",
    ]
    script = html[a:b] + """
const CASES = %s, REFUSE = %s;
const out = {ranges: [], refused: [], loc: null};
for (const [s] of CASES) {
  const ics = icsFor({name: "T", dates: s, city: "Washington, DC",
                      url: "https://x.test/", tag: "t"});
  const g = t => { const m = ics && ics.match(new RegExp(t + ":(\\\\d{8})")); return m ? m[1] : null; };
  out.ranges.push([g("DTSTART;VALUE=DATE"), g("DTEND;VALUE=DATE")]);
}
for (const s of REFUSE) out.refused.push(calRange(s) === null);
/* isPast decides whether a row is labelled a past edition. Anchored to fixed
   dates either side of a known point rather than to "now", so the check does
   not start failing on its own the day the catalogue rolls over. */
/* withinDays backs the "how soon" filter. The case that matters is the one
   that looks like a no-op: a row we cannot date must be KEPT. */
out.within = {
  undatedKept:  withinDays("", 90) && withinDays("Spring 2027", 90),
  noFilterKeepsAll: withinDays("July 25-28, 2099", 0),
  farFutureExcluded: !withinDays("July 25-28, 2099", 90),
  longPastExcluded:  !withinDays("November 19-21, 2025", 90),
};
/* isNow marks an event running TODAY. Anchored to a range that always spans
   the current date rather than to fixed dates, because "today" is the whole
   point and a hard-coded case would rot the day after it was written. */
out.now = (() => {
  const d = new Date(); d.setHours(0,0,0,0);
  const MON = ["January","February","March","April","May","June","July",
               "August","September","October","November","December"];
  const s = new Date(d), e = new Date(d);
  s.setDate(s.getDate() - 1); e.setDate(e.getDate() + 1);
  const span = s.getMonth() === e.getMonth()
    ? `${MON[s.getMonth()]} ${s.getDate()}-${e.getDate()}, ${e.getFullYear()}`
    : `${MON[s.getMonth()]} ${s.getDate()} - ${MON[e.getMonth()]} ${e.getDate()}, ${e.getFullYear()}`;
  return {
    spansToday:   isNow(span),
    spanUsed:     span,
    longPast:     isNow("November 19-21, 2025"),
    farFuture:    isNow("July 25-28, 2099"),
    undated:      isNow(""),
    /* an event on today is NOT a past edition - the two markers must never
       both appear on one row */
    notAlsoPast:  !isPast(span),
  };
})();
out.past = {
  clearlyOver:   isPast("November 19-21, 2025"),
  clearlyAhead:  isPast("July 25-28, 2099"),
  unparseable:   isPast("Conference: November 17-19, 2026; Expo: November 18-19, 2026"),
  noDates:       isPast(""),
};
const one = icsFor({name: "A, B; C", dates: "March 1-4, 2027",
                    city: "Washington, DC", tag: "t"});
out.loc = /LOCATION:Washington\\\\, DC/.test(one) && /SUMMARY:A\\\\, B\\\\; C/.test(one);
out.crlf = one.includes("\\r\\n") && !/[^\\r]\\n/.test(one);
console.log(JSON.stringify(out));
""" % (_json.dumps(cases), _json.dumps(refuse))

    try:
        r = subprocess.run(["node", "--input-type=module", "-e", script],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return fail(f".ics parser could not be executed: {exc}")
    if r.returncode != 0:
        return fail(f".ics parser threw: {r.stderr.strip()[:200]}")
    got = _json.loads(r.stdout)

    errors = 0
    for (dates, want_s, want_e), (gs, ge) in zip(cases, got["ranges"]):
        if gs != want_s:
            errors += fail(f"ics {dates!r}: DTSTART {gs}, expected {want_s}")
        if ge != want_e:
            errors += fail(f"ics {dates!r}: DTEND {ge}, expected {want_e}. "
                           f"DTEND is exclusive - a calendar shows one day "
                           f"less than the conference actually runs")
    for s, refused in zip(refuse, got["refused"]):
        if not refused:
            errors += fail(f"ics: {s!r} was parsed into a date. It is not "
                           f"unambiguous, and a wrong week in somebody's "
                           f"calendar is the error nobody notices")
    if not got["loc"]:
        errors += fail("ics: a comma or semicolon in a city or event name is "
                       "not escaped, so the field splits into two")
    if not got.get("crlf"):
        errors += fail("ics: lines must end CRLF per RFC 5545")

    # The past-edition marker. Five of these rows exist deliberately - they
    # were added to be mined for exhibitor lists - so the date is correct and
    # the row was still misleading until it said which edition it is.
    within = got.get("within") or {}
    if not within.get("undatedKept"):
        errors += fail("a conference we cannot date is dropped by the 'how "
                       "soon' filter. That answers 'is this happening soon?' "
                       "with 'no' on evidence we do not have")
    if not within.get("noFilterKeepsAll"):
        errors += fail("the 'how soon' filter drops events when it is set to "
                       "Any time")
    if not within.get("farFutureExcluded"):
        errors += fail("'in the next 3 months' is showing an event years away")
    if not within.get("longPastExcluded"):
        errors += fail("'in the next 3 months' is showing an event that "
                       "already happened")

    now = got.get("now") or {}
    if not now.get("spansToday"):
        errors += fail(f"isNow says a conference running today "
                       f"({now.get('spanUsed')}) is not on now")
    for key, why in (("longPast", "an event from 2025"),
                     ("farFuture", "an event in 2099"),
                     ("undated", "a row with no dates")):
        if now.get(key):
            errors += fail(f"isNow marks {why} as running today")
    if not now.get("notAlsoPast"):
        errors += fail("a conference running today is ALSO marked a past "
                       "edition; one row would carry both markers")

    past = got.get("past") or {}
    if not past.get("clearlyOver"):
        errors += fail("a conference that ended in 2025 is not marked a past "
                       "edition, so it reads as one you can still fly to")
    if past.get("clearlyAhead"):
        errors += fail("an upcoming conference is marked as a past edition")
    for key, why in (("unparseable", "dates we cannot read"),
                     ("noDates", "a row with no dates")):
        if past.get(key):
            errors += fail(f"{why} must not be called a past edition - we do "
                           f"not know when it is, and a guess either way is "
                           f"a claim we cannot support")

    # Anything in the live catalogue that the parser refuses must be a row we
    # know about, not a shape that quietly lost its button.
    known_unparseable = {"Conference: November 17-19, 2026; Expo: November 18-19, 2026"}
    cat = _json.loads((ROOT / "data" / "conferences.json").read_text())["conferences"]
    dated = [c for c in cat if c.get("dates")]
    probe = html[a:b] + (
        "\nconsole.log(JSON.stringify(%s.map(d => calRange(d) !== null)));"
        % _json.dumps([c["dates"] for c in dated]))
    r2 = subprocess.run(["node", "--input-type=module", "-e", probe],
                        capture_output=True, text=True, timeout=30)
    if r2.returncode == 0:
        for c, okd in zip(dated, _json.loads(r2.stdout)):
            if not okd and c["dates"] not in known_unparseable:
                errors += fail(f"{c.get('conference')}: dates "
                               f"{c['dates']!r} get no calendar button and "
                               f"are not a known-ambiguous shape")
    return errors


def check_acquired_names_still_match_themselves() -> int:
    """"X, An Acme Company" and "X" are one company filed twice.

    That is how an acquired product gets renamed on its new owner's site, and
    the board carries both spellings for Novotx and TouchNet. The tail survives
    the legal-suffix strip, so the two names stay different strings and the
    pair never reaches the duplicates queue - a company counted twice in a
    total this project quotes at strangers, and no signal able to say so.

    The opposite mistake is worse and cheaper to make: a rule hungry enough to
    eat "The Active Network" or to collapse two unrelated firms would merge
    companies that are not the same. So the negatives are pinned here as hard
    as the positives.

    Known and deliberately not fixed: LEGAL strips its words anywhere in a
    name, not only at the end, so a company literally called "Company Nurse"
    would collapse into "Nurse". Nothing on the board is affected, and
    narrowing LEGAL to a suffix rule would re-key every existing merge - a
    change with a blast radius nothing currently needs. Left here so the next
    person meets it as a note rather than as a surprise.
    """
    import admin
    errors = 0
    same = [
        ("Novotx, An Accela Company", "Novotx"),
        ("TouchNet, A Global Payments Company", "TouchNet"),
        ("Collins Aerospace, An RTX Business", "Collins Aerospace"),
        ("Foo, a Bar Holdings Company", "Foo"),
    ]
    for a, b in same:
        if admin.ident(a) != admin.ident(b):
            errors += fail(f"{a!r} and {b!r} do not resolve to one identity, "
                           f"so the pair never reaches the duplicates queue")
    differ = [
        # a real name that merely contains the trigger words
        ("The Active Network", "Active"),
        ("American Water Works Company", "American"),
        # the tail names the PARENT; stripping it must not leave the parent
        ("Novotx, An Accela Company", "Accela"),
        # two different firms must never collapse into each other
        ("Motorola Solutions", "Tyler Technologies"),
        ("Accela", "Novotx"),
    ]
    for a, b in differ:
        if admin.ident(a) == admin.ident(b):
            errors += fail(f"{a!r} and {b!r} now resolve to the SAME identity. "
                           f"A name rule that over-reaches merges companies "
                           f"that are not the same company")
    return errors



def check_websites_queue_names_its_twins() -> int:
    """A row that cannot be answered by answering it must say so.

    Eleven of the fifty companies in the missing-websites queue are one half of
    a duplicate pair whose OTHER half already carries the website - Avolve and
    Avolve Software, Novotx and "Novotx, An Accela Company", Oracle and Oracle
    Corporation. Researching a website for those is attention spent on a
    question with no right answer; the answer is a merge, in a different queue.

    The row is marked rather than hidden, because a pair can turn out to be a
    parent and its division, and then the website question is real again.

    Pinned in both halves: admin.py emits `same_name_as`, admin.html reads it.
    A rename on one side alone silently drops the warning, and the queue goes
    back to quietly wasting eleven decisions.
    """
    import admin
    errors = 0
    companies = admin.read("companies.json", [])
    rows = admin.q_websites(companies, admin.read("board.json", {}))
    if not rows:
        note("no companies are missing a website; nothing to check here")
        return 0

    flagged = [r for r in rows if r.get("same_name_as")]
    for r in flagged:
        for twin in r["same_name_as"]:
            if twin["id"] == r["id"]:
                errors += fail(f"{r['name']}: listed as its own twin")
    # a row whose name matches another company's must be flagged
    idents = {}
    for c in companies:
        k = admin.ident(c["name"])
        if k:
            idents.setdefault(k, []).append(c["id"])
    for r in rows:
        k = admin.ident(r["name"])
        if len(idents.get(k, [])) > 1 and not r.get("same_name_as"):
            errors += fail(f"{r['name']} shares a name with another record but "
                           f"the websites queue does not say so, so it reads "
                           f"as a research job when it is a merge")

    # The UI half is a text check and cannot prove the branch runs. It counts
    # occurrences rather than testing for one, because the guard and the use are
    # separate lines: disabling the guard drops the count even though the string
    # survives further down. That catches the realistic failure - someone edits
    # the condition - without pretending to be an execution test.
    html = (ROOT / "admin.html").read_text()
    seen = html.count("same_name_as")
    if seen < 2:
        errors += fail(f"admin.html references same_name_as {seen} time(s); the "
                       f"websites queue computes the duplicate warning and then "
                       f"throws it away, so eleven rows read as research jobs "
                       f"when they are merges")
    return errors


def check_alert_vocabulary() -> int:
    """functions/api/alerts.js must accept exactly what roles.py can assign."""
    js = (ROOT / "functions" / "api" / "alerts.js")
    if not js.exists():
        return 0
    text = js.read_text()
    bad = 0
    import roles
    expected = {
        "FAMILIES": set(roles.FAMILIES),
        "SENIORITY": {"junior", "mid", "senior", "leadership"},
        "MODES": {"remote", "hybrid", "onsite"},
    }
    for name, want in expected.items():
        m = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", text, re.S)
        if not m:
            bad += fail(f"alerts.js: no {name} set found")
            continue
        got = set(re.findall(r'"([^"]+)"', m.group(1)))
        if got != want:
            bad += fail(f"alerts.js {name} drifted from roles.py: "
                        f"only in js {sorted(got - want)}, "
                        f"only in python {sorted(want - got)}")
    return bad


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
        # extra placements, for vendors that genuinely sell into several
        # departments. The primary stays single so the xlsx has one tab per
        # company and "where does this live" has one answer.
        placed = {(c["sector"], c["category"])}
        for extra in c.get("also") or []:
            s2, c2 = extra.get("sector"), extra.get("category")
            if s2 not in sector_cats:
                errors += fail(f"{where}: also names unknown sector {s2}")
            elif c2 not in sector_cats[s2]:
                errors += fail(f"{where}: also: {c2} not a category of {s2}")
            elif (s2, c2) in placed:
                errors += fail(f"{where}: filed twice under {s2} / {c2}")
            placed.add((s2, c2))
        if c["ats"]["type"] not in ATS_TYPES:
            errors += fail(f"{where}: bad ats type {c['ats']['type']}")
        if c["hiring"]["status"] not in STATUSES:
            errors += fail(f"{where}: bad status {c['hiring']['status']}")
        y = c.get("year_founded")
        if y is not None and (not isinstance(y, int) or not (1800 <= y <= 2100)):
            errors += fail(f"{where}: suspicious year {y}")

    # The exporter must honor exactly the guarantees the loop above makes:
    # year_founded, location and description are optional, and an entry
    # missing them still gets a row. A KeyError here once crashed the 6am run
    # after the 40-minute fetch and lost the day's uncommitted snapshots.
    # Conference event tags end up inside company descriptions forever, so
    # they are assigned in conferences.json rather than by whatever agent
    # scraped the floor. Acronyms collide: three associations are called APPA
    # and two are called ASBO, and a bare "APPA 2026" cannot be read back six
    # months later. Every tag must be unique, and every tag written into a
    # description must be one the catalog actually issued.
    conf_p = DATA / "conferences.json"
    if conf_p.exists():
        confs = json.load(open(conf_p))["conferences"]
        tags = [c.get("event_tag") for c in confs if c.get("event_tag")]
        dupes = {t for t in tags if tags.count(t) > 1}
        if dupes:
            errors += fail(f"conference event_tag is not unique: {sorted(dupes)}")
        for c in confs:
            t = c.get("event_tag") or ""
            if t and not re.match(r"^[\w &.'/-]+ 20\d{2}$", t):
                errors += fail(f"{c['conference']}: event_tag {t!r} is not "
                               f"'<name> <year>'")
        issued = set(tags) | {t for c in confs
                              for t in (c.get('prior_tags') or [])}
        seen = set()
        for row in companies + json.load(open(DATA / "suppliers.json")):
            m = re.search(r"exhibited at ([^;.\n]+)", row.get("description") or "")
            if m:
                seen.update(x.strip() for x in m.group(1).split(","))
        stray = {t for t in seen if t not in issued}
        if stray:
            errors += fail("descriptions carry event tags the catalog never "
                           f"issued: {sorted(stray)[:6]}")

    # The intake guesser proposes a sector and category; every pair it can
    # propose must be one the validator would accept. They drifted apart once
    # already: the schema gained categories and renamed others while
    # SECTOR_HINTS kept the old names, so intake proposed placements that
    # could never be written.
    import add_company as _addco
    for sector, category, _pat in _addco.SECTOR_HINTS:
        if sector not in sector_cats:
            errors += fail(f"SECTOR_HINTS names unknown sector {sector!r}")
        elif category not in sector_cats[sector]:
            errors += fail(f"SECTOR_HINTS: {category!r} is not a category of "
                           f"{sector!r} in schema.json")

    import export_xlsx as _xlsx
    for c in companies:
        try:
            _xlsx.row_values(c)
        except Exception as e:  # noqa: BLE001 - any crash is the failure
            errors += fail(f"{c.get('name', '???')}: export_xlsx.row_values "
                           f"crashed: {type(e).__name__}: {e}")
            break

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

    # Territory, office and work mode are three separate facts, each honest
    # about absence. Every case here is a conflation that once happened or
    # plausibly would: title-states filed as a desk, a bare city read as
    # proof of onsite, "Remote - NY" read as a New York office.
    GEOGRAPHY_CASES = [
        # (location, title) -> (territory_states, territory_stated,
        #                       office_state, work_mode)
        (("Denver, CO", "Enterprise Account Executive - NY, MA, VT, NH"),
         (["MA", "NH", "NY", "VT"], True, "CO", "not stated")),
        (("New York, NY", "Account Executive"),
         ([], False, "NY", "not stated")),
        (("Remote - NY, NJ, CT", "Account Executive"),
         ([], False, None, "remote")),
        (("", "Territory Manager, Pacific Northwest"),
         ([], True, None, "not stated")),        # region stated, no states
        (("TX, OK", "Territory Manager"),
         (["OK", "TX"], True, None, "not stated")),
        (("Chicago, IL (Hybrid)", "Account Executive"),
         ([], False, "IL", "hybrid")),
        (("San Antonio, TX", "On-site Account Executive"),
         ([], False, "TX", "onsite")),
        (("Remote", "Account Executive"),
         ([], False, None, "remote")),
        (("", "Account Executive"),
         ([], False, None, "not stated")),
        (("Manchester, United Kingdom", "Account Executive"),
         ([], False, None, "not stated")),       # no US state = no office claim
        # A CITY SPELLED THE LONG WAY IS STILL A CITY. The office parser read
        # only "City, ST", so 507 postings naming a real place resolved to no
        # desk at all - and a distance filter built on that would have
        # answered "nothing near you" across most of the board.
        (("San Francisco, California", "Account Executive"),
         ([], False, "CA", "not stated")),
        (("BOSTON, MASSACHUSETTS, UNITED STATES", "Account Executive"),
         ([], False, "MA", "not stated")),
        (("New York, New York, United States", "Account Executive"),
         ([], False, "NY", "not stated")),
        (("San Mateo, CA United States", "Account Executive"),
         ([], False, "CA", "not stated")),
        # Washington DC is the seat of a great many of these roles and is
        # spelled a way neither pattern catches: not two bare letters, and not
        # in STATE_NAMES.
        (("Washington, D.C.", "Account Executive"),
         ([], False, "DC", "not stated")),
        # Georgia is also a country. Reading this as an Atlanta desk is the
        # trap AMBIGUOUS_STATE_NAMES exists for, and the long-form pattern
        # must honour it too.
        (("Tbilisi, Georgia", "Account Executive"),
         ([], False, None, "not stated")),
        # Remote still wins over an address: "Remote - San Francisco,
        # California" is eligibility, not a desk.
        (("Remote - San Francisco, California", "Account Executive"),
         ([], False, None, "remote")),
    ]
    # The CITY STRING, not just its state. Boards shout, and "BOSTON" and
    # "Boston" are one desk - if they survive as two, they are two rows in a
    # city picker, two cities to geocode, and two distances from the same
    # place. Title-casing must not flatten a name somebody capitalised on
    # purpose, which is why McLean is here.
    #
    # "boston, ma" in lower case is NOT here. It was, and it failed, and the
    # fix was to delete the case rather than loosen the pattern: nothing in
    # the data is spelled that way (checked - zero all-lowercase location
    # strings across 4,353 postings), and widening a guard to satisfy an
    # invented input is how a pattern starts matching prose.
    CITY_CASES = [
        ("BOSTON, MASSACHUSETTS, UNITED STATES", "Boston"),
        ("Boston, MA", "Boston"),
        ("McLean, VA", "McLean"),
        ("DeKalb, IL", "DeKalb"),
        ("Washington, D.C.", "Washington"),
        # A LOCATION FIELD THAT SAYS MORE THAN AN ADDRESS. The city group
        # starts at the first capital before the comma, so these produced
        # cities called "in-office preferred in San Mateo" and "United States
        # - San Francisco" - five of them on the board, each becoming its own
        # row in a city list and its own geocoder lookup.
        ("in-office preferred in San Mateo, CA", "San Mateo"),
        ("Production AMP - Commerce City, CO", "Commerce City"),
        ("United States - San Francisco, CA", "San Francisco"),
        # and a real three-word city must survive the same trimming
        ("Salt Lake City, UT", "Salt Lake City"),
    ]
    # TWO CAPITALS ARE NOT A US STATE. The office pattern matched any [A-Z]{2},
    # so London UK, Cambridge UK, Montreal QB, Noida UP, Pune MH and even
    # "California, US" were filed as US desks - 24 postings at 16 places that
    # do not exist, each of which would have answered a search for offices near
    # a US city. None of these may produce an office.
    #
    # "Berlin, DE" is NOT in this list, though it was. DE is Delaware, and the
    # board carries Dover, Newark and Wilmington under it - refusing DE to
    # catch a German city nobody has posted would drop three real US ones. The
    # data decided it: "Berlin, DE" appears nowhere, and the three spellings
    # that do appear are "Berlin", "Berlin, Germany" and "Berlin, Berlin,
    # Deutschland", none of which this pattern touches.
    for loc in ("London, UK", "Cambridge, UK", "Montreal, QB", "Noida, UP",
                "Pune, MH", "Toronto, ON"):
        got = _roles.geography(loc, "Account Executive")["office"]
        if got:
            errors += fail(f"geography({loc!r}) claimed a US office {got} - "
                           f"{loc.split(',')[-1].strip()} is not a US state")
    # "California, US" is a state with no city, which is a different and
    # correct answer: a bare state still pins the seat to a state, and the
    # city must be None rather than the word "California".
    ca = _roles.geography("California, US", "Account Executive")["office"]
    if not ca or ca.get("state") != "CA" or ca.get("city") is not None:
        errors += fail(f"geography('California, US') office = {ca}, expected "
                       f"state CA with no city")
    for loc, want_city in CITY_CASES:
        got = _roles.geography(loc, "Account Executive")["office"]
        got_city = got["city"] if got else None
        if got_city != want_city:
            errors += fail(f"geography({loc!r}) city = {got_city!r}, "
                           f"expected {want_city!r}")

    for (loc, title), (t_states, t_stated, o_state, mode) in GEOGRAPHY_CASES:
        g = _roles.geography(loc, title)
        got = (g["territory"]["states"], g["territory"]["stated"],
               g["office"]["state"] if g["office"] else None, g["work_mode"])
        if got != (t_states, t_stated, o_state, mode):
            errors += fail(f"geography({loc!r}, {title!r}) = {got}, "
                           f"expected {(t_states, t_stated, o_state, mode)}")

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

    # The alerts Worker re-states roles.py's vocabulary because a Worker cannot
    # import Python. If the two drift, a subscriber picks a value the Worker
    # happily stores, no posting ever carries it, and their alert silently
    # never arrives - no error anywhere. So the duplication is checked here.
    errors += check_alert_vocabulary()
    errors += check_merged_names_stay_merged()
    errors += check_journal_matches_reality()
    errors += check_writes_name_their_author()
    errors += check_header_shared()
    errors += check_identity_guard()
    errors += check_unreachable_names_the_failure()
    errors += check_search_routes_are_live()
    errors += check_calendar_dates_survive_the_round_trip()
    errors += check_acquired_names_still_match_themselves()
    errors += check_websites_queue_names_its_twins()
    errors += check_beak_is_never_text()
    errors += check_rating_scale()
    errors += check_every_company_says_what_it_sells()
    errors += check_brand()
    errors += check_admin_game()
    errors += check_admin_gates()
    errors += check_admin_guards()
    errors += check_redirect_hop()
    errors += check_admin_http()
    errors += check_url_sinks()

    for raw, expected in TITLE_TEXT_CASES:
        got = ats.plain(raw)
        if got != expected:
            errors += fail(f"ats.plain({raw!r}) = {got!r}, expected {expected!r}")
    errors += check_board()
    errors += check_salary()
    # What survives a job description, and what must not. Checked against the
    # function rather than the file, so the rule holds even when board.json on
    # disk was written before it existed.
    errors += check_derived()
    errors += check_safe_url()

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
          f"{len(PAGESCAN_CASES)} page-scan, {len(TITLE_TEXT_CASES)} title-text")
    if errors:
        print(f"\n{errors} problem(s) found")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
