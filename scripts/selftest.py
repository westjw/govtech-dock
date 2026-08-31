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
import inspect
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

# A card's button label glued to the front of the title. Adobe's careers page
# put "Apply Now" inside the anchor and gave the card no heading, so nine
# postings reached the public board named "Apply Now Account Manager, Channel
# Sales" - and a wrong title is not only wrong on screen: it is the posting id,
# the alert match, and the key a scope ruling is filed under.
#
# The three that must NOT change are the point of the list. "Apply" starts real
# words, and a substring rule here would rename "Applied Scientist" to
# "ed Scientist" - a fix that quietly corrupts more titles than the bug did.
CTA_CASES = [
    ("Apply Now Account Manager, Channel Sales", "Account Manager, Channel Sales"),
    ("Apply now: Account Executive", "Account Executive"),
    ("View Job Sales Director", "Sales Director"),
    ("Learn More - Regional Sales Manager", "Regional Sales Manager"),
    ("Applied Scientist", "Applied Scientist"),
    ("Application Engineer", "Application Engineer"),
    ("Senior Apply Engineer", "Senior Apply Engineer"),
    # the whole text is the button: hand it back rather than return "", or a
    # link we should never have taken becomes a job with no name
    ("Apply Now", "Apply Now"),
    ("More Details", "More Details"),

    # AND THE OTHER END, which turned out to be the more common one. Eleven
    # postings reached the public board reading like these.
    ("Account Executive, Fire Read More", "Account Executive, Fire"),
    ("Business Development Manager (Remote) Sales Sydney, Australia Apply now",
     "Business Development Manager (Remote) Sales Sydney, Australia"),
    # two labels on one flattened card - the uveye case, by name
    ("Supply Chain Analyst Teaneck, NJ Full-time More Details Less Details",
     "Supply Chain Analyst Teaneck, NJ Full-time"),
    # the tail rule is deliberately stricter than the head: a title can
    # plausibly END in "details" or "more" and cannot plausibly BEGIN with
    # "apply now", so only whole phrases are taken off the back
    ("Director of More Markets", "Director of More Markets"),
    ("Analyst, Business Details", "Analyst, Business Details"),

    # A KNOWN, ACCEPTED FALSE POSITIVE, recorded rather than hidden. The head
    # rule takes "Read More" off the front of anything, so a genuine title
    # beginning with those words loses them. This board carries 53 library
    # companies and one of them could conceivably post it. It is kept because
    # the alternative - dropping the head rule - readmits nine Adobe titles
    # and every flattened card like them, and because a title beginning
    # "Read More" is vanishingly rarer than a card beginning with the button.
    # If it ever happens for real, the fix is a stored exception, not a
    # loosened anchor.
    ("Read More Books Coordinator", "Books Coordinator"),
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


def note(msg):
    """Something a run could not test, said out loud rather than passed over.

    Three checks called this before it existed. Every one of them called it
    only on a SKIP path - node missing, no IPv6, an empty queue - so the
    NameError could not fire on a machine where the thing being skipped was
    present. It would have fired on a CI runner and nowhere else, which is the
    worst place for a latent crash and the hardest to reproduce.
    """
    print(f"note: {msg}")


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
    # `bad`, not `errors`: this function's accumulator is `bad`, and all three
    # of these said `errors +=`. Every one of them would have raised NameError
    # instead of failing - so the explanation they exist to print ("rewards
    # gated on activity were removed deliberately") never reached anyone, and
    # every check after this one was skipped. A guard that crashes still stops
    # the build, which is why it survived; it just stops it uselessly.
    if getattr(admin, "UNLOCKS", None):
        bad += fail("admin.UNLOCKS is back - rewards gated on activity "
                    "were removed deliberately; see the note above unlocks()")
    if admin.unlocks({}) != []:
        bad += fail("unlocks() should hand out nothing")
    if hasattr(admin, "csv_gate"):
        bad += fail("csv_gate is back - the export is not gated")

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


def check_role_promotion() -> int:
    """A stored role may only be published when it is that company's own.

    build_board promotes a role the refresh pass stored when the board itself
    will not enumerate. That is how Zencity and First Arriving got a posting a
    visitor can click. It is also one wrong containment test away from
    republishing every parent-board false Yes this project has removed, so the
    dangerous direction is asserted here explicitly.

    The rule is asymmetric on purpose. A slug that EXTENDS the company name is
    theirs - kpaonline for KPA. A slug the name extends is the PARENT - xylem
    for Xylem Vue, whose sixteen roles are pump sales; zoll for ZOLL Data
    Systems, whose fifteen include a cardiac device AE.
    """
    import build_board as bb
    errors = 0

    # The slug test itself, in both directions.
    if not bb._slug_names("kpaonline", ["KPA"]):
        errors += fail("_slug_names refused 'kpaonline' for KPA - a slug that "
                       "extends the name is theirs")
    if not bb._slug_names("d-fendsolutions", ["D-Fend Solutions"]):
        errors += fail("_slug_names refused 'd-fendsolutions' for D-Fend Solutions")
    for slug, name in (("xylem", "Xylem Vue"), ("zoll", "ZOLL Data Systems"),
                       ("merative", "Cúram by Merative"), ("harriscomputer", "Harris")):
        if slug != "harriscomputer" and bb._slug_names(slug, [name]):
            errors += fail(f"_slug_names accepted '{slug}' for {name} - the slug is "
                           f"SHORTER than the name, which means the parent's board")

    # The whole promotion path, on records shaped like the real failures.
    theirs = {"name": "Zencity", "website": "https://zencity.io",
              "hiring": {"roles": [{"title": "Enterprise Account Manager",
                                    "url": "https://zencity.io/careers/x"}]}}
    if len(bb._stored_roles_as_jobs(theirs)) != 1:
        errors += fail("a role on the company's OWN domain was not promoted")

    parent = {"name": "Cartegraph", "website": "https://cartegraph.com",
              "hiring": {"roles": [{"title": "Account Executive",
                                    "url": "https://opengov.com/careers/x"}]}}
    if bb._stored_roles_as_jobs(parent):
        errors += fail("promoted a role read off the PARENT's domain "
                       "(Cartegraph off opengov.com) - this is the false Yes")

    # A NON-ATS DOMAIN IS REFUSED EVEN WHEN THE PATH NAMES THE COMPANY. The
    # ATS carve-out exists only because greenhouse.io is a filing cabinet, not
    # a company. centegix.com IS a company - Ident-A-Kid's acquirer - and a
    # path segment on it naming Ident-A-Kid does not make the requisition
    # theirs. Without this case the ATS restriction can be deleted outright
    # and every other assertion here still passes.
    acquirer_path = {"name": "Ident-A-Kid", "website": "https://identakid.com",
                     "hiring": {"roles": [{"title": "Account Executive",
                                           "url": "https://centegix.com/identakid/jobs/1"}]}}
    if bb._stored_roles_as_jobs(acquirer_path):
        errors += fail("promoted a role hosted on the ACQUIRER's own domain "
                       "(centegix.com) because a path segment named the "
                       "acquired brand - only ATS hosts get the slug carve-out")

    drawer = {"name": "Xylem Vue", "website": "https://xylem.com/vue",
              "hiring": {"roles": [{"title": "Rental Sales Representative",
                                    "url": "https://xylem.wd5.myworkdayjobs.com/en-US/x/job/1"}]}}
    if bb._stored_roles_as_jobs(drawer):
        errors += fail("promoted a role from the parent's ATS drawer "
                       "(Xylem Vue off xylem's Workday)")

    synthetic = {"name": "Skydio", "website": "https://skydio.com",
                 "hiring": {"roles": [{"title": "AE-type role (page scan)",
                                       "url": "https://skydio.com/careers",
                                       "synthetic": True}]}}
    if bb._stored_roles_as_jobs(synthetic):
        errors += fail("promoted the page-scan MARKER as a posting - it has no "
                       "title and no url of its own; publishing it invents a job")
    # and again for records stored before `synthetic` existed
    old_synthetic = {"name": "Skydio", "website": "https://skydio.com",
                     "hiring": {"roles": [{"title": "AE-type role (page scan)",
                                           "url": "https://skydio.com/careers"}]}}
    if bb._stored_roles_as_jobs(old_synthetic):
        errors += fail("promoted a page-scan marker stored before the "
                       "`synthetic` flag existed")

    evergreen = {"name": "SchoolStatus", "website": "https://schoolstatus.com",
                 "hiring": {"roles": [{"title": "Account Executive (Future Opportunities)",
                                       "url": "https://schoolstatus.com/careers/x"}]}}
    if bb._stored_roles_as_jobs(evergreen):
        errors += fail("promoted a talent-pool posting as an opening")

    # The classifier must keep flagging its marker, or the guard above loses
    # its strongest signal and falls back to matching a title string.
    import classify
    _, _, ae = classify.rollup([{"_pagetext": "We are hiring an Account Executive",
                                 "url": "https://example.com/careers"}])
    if not ae or not ae[0].get("synthetic"):
        errors += fail("classify.rollup no longer marks its page-scan role "
                       "`synthetic` - build_board can no longer tell a marker "
                       "from a real posting")
    return errors


def check_render_rotation() -> int:
    """The render queue must rotate, and a dry run must not move it.

    Rendering used to happen inline in fetch order, so the budget cut off at
    the same place every run: the same boards were rendered daily and the 564
    past the cut-off were never tried once. That is not a backlog, it is a
    permanent blind spot, and raising the budget only moves the cliff. Two
    properties keep it honest, and neither is visible by reading the output.
    """
    import build_board as bb
    import inspect
    errors = 0

    # A board never tried sorts ahead of every dated one; among dated ones the
    # oldest goes first. This is the sort the pre-pass runs.
    attempts = {"b": "2026-08-27", "c": "2026-08-20", "e": "2026-08-28"}
    cos = [{"id": x} for x in ["a", "b", "c", "d", "e"]]
    cos.sort(key=lambda c: (attempts.get(c["id"], ""), c["id"]))
    order = [c["id"] for c in cos]
    if order[:2] != ["a", "d"]:
        errors += fail(f"render queue put {order[:2]} first; boards never tried "
                       f"(a, d) must lead or the tail is never reached")
    if order[2] != "c":
        errors += fail(f"render queue took {order[2]} before c, which was tried "
                       f"longest ago")
    if order[-1] != "e":
        errors += fail("render queue did not put today's attempt last")

    src = inspect.getsource(bb)

    # Stamped BEFORE the attempt, or a board that crashes the renderer keeps
    # its place at the front and blocks everything behind it, every run.
    i_stamp = src.find('attempts[c["id"]] = today')
    i_try = src.find("render_fetch.fetch_rendered(ref)", i_stamp if i_stamp > 0 else 0)
    if i_stamp < 0:
        errors += fail("build_board no longer records a render attempt - the "
                       "queue cannot rotate without it")
    elif i_try < 0 or i_stamp > i_try:
        errors += fail("the render attempt is stamped AFTER the render, so a "
                       "board that reliably crashes the browser never yields "
                       "its place")

    # And the budget clock must start after the fetch, not before it. It used
    # to be set above a forty-minute parallel fetch, so the render budget was
    # mostly spent fetching: a run reported "784s spent" having rendered for
    # about eighty of them.
    i_clock = src.find("render_started = time.monotonic()")
    i_fetch = src.find("ThreadPoolExecutor")
    if i_clock < 0 or i_fetch < 0:
        errors += fail("cannot locate the render clock or the fetch pool")
    elif i_clock < i_fetch:
        errors += fail("the render budget clock starts BEFORE the parallel "
                       "fetch, so the fetch spends the render budget")

    if "if not a.dry_run:" not in src:
        errors += fail("a dry run can now write render_attempts.json, which "
                       "would push boards to the back of the queue and hide "
                       "them from the next real build")
    return errors


def check_refresh_render_ration() -> int:
    """refresh.py renders on a ration, and the ration rotates.

    _try_render was added on 2026-08-28 with no bound at all: every html board
    that failed to read got a browser, every run. 649 boards want one, at
    roughly 7-27s each - one to five hours added to a CI job with a six-hour
    ceiling, and always the same boards first. Same pair of bugs build_board
    had, fixed the same way.

    Writes nothing: the attempts path is redirected to a temp file, because a
    selftest that stamped the real queue would push boards to the back of it
    every time it ran.
    """
    import refresh as R
    import tempfile, pathlib as _pl
    errors = 0
    saved_path, saved_attempts = R.RENDER_ATTEMPTS, dict(R._render_attempts)
    R.RENDER_ATTEMPTS = _pl.Path(tempfile.mkdtemp()) / "attempts.json"
    try:
        cos = [{"id": f"c{i:02d}", "ats": {"type": "html"}} for i in range(20)]
        cos.append({"id": "api1", "ats": {"type": "greenhouse"}})

        R._plan_renders(cos, 120)                      # 120 // 12 = 10
        run1 = set(R._RENDER_ALLOW)
        if len(run1) != 10:
            errors += fail(f"a 120s render budget queued {len(run1)} boards, "
                           f"expected 10 - the ration is not being applied")
        if "api1" in run1:
            errors += fail("a non-html board was queued for rendering; only "
                           "page-scanned html boards can be helped by a browser")

        R._render_attempts = {i: "2026-08-28" for i in run1}
        R._save_render_attempts()
        R._plan_renders(cos, 120)
        if run1 & set(R._RENDER_ALLOW):
            errors += fail("refresh render queue does not rotate - the same "
                           "boards come up every run and the tail is never "
                           "rendered once")

        R._plan_renders(cos, 0)
        if R._RENDER_ALLOW:
            errors += fail("--render-budget 0 did not disable rendering")

        R._plan_renders(cos, 120)
        R._render_skipped = 0
        outside = next(c["id"] for c in cos if c["id"] not in R._RENDER_ALLOW)
        if R._try_render("html", "https://example.com/x", outside) is not None:
            errors += fail("_try_render rendered a board that was not this "
                           "run's turn")
        if R._render_skipped != 1:
            errors += fail("a board refused for being out of turn was not "
                           "counted - the run would report it as a zero")

        src = __import__("inspect").getsource(R)
        i_stamp = src.find("_render_attempts[cid] =")
        i_try = src.find("render_fetch.fetch_rendered(ref)")
        if i_stamp < 0:
            errors += fail("refresh no longer records a render attempt - the "
                           "queue cannot rotate without it")
        elif i_try > 0 and i_stamp > i_try:
            errors += fail("refresh stamps the attempt AFTER the render, so a "
                           "board that crashes the browser never yields its place")
    finally:
        R.RENDER_ATTEMPTS, R._render_attempts = saved_path, saved_attempts
    return errors


def check_save_needs_read() -> int:
    """save_companies() must refuse when nothing was read first.

    The old fallback was `before = _LAST_COMPANIES if ... else companies` -
    the AFTER state. journal.record() then saw no change, wrote an entry with
    an empty diff, and admin_undo.py would later restore nothing while
    reporting success. The write itself still landed. The only way to discover
    it was to undo something and watch it not come back.

    THIS CHECK CANNOT BE ALLOWED TO WRITE, and the first version of it could.
    save_companies() does not call validate(), so when the guard was mutated
    away to confirm this check fires, the probe write went through and
    replaced all 2,103 companies with a single {"id": "x"} record. Restored
    from git, but the lesson is the test's, not the guard's: a selftest that
    is capable of writing WILL write, on exactly the run where the thing it
    guards is broken - which is the one run where the damage is worst.

    So write_atomic is stubbed for the duration. Now a mutation that defeats
    the guard is caught twice: the refusal is missing, and the stub records a
    write that should never have been attempted.
    """
    import admin
    errors = 0
    saved_last, saved_write = admin._LAST_COMPANIES, admin.write_atomic
    wrote = []
    try:
        admin.write_atomic = lambda name, data: wrote.append(name)
        admin._LAST_COMPANIES = None
        r = admin.save_companies([{"id": "x"}], "selftest-probe", by="claude")
        if not r:
            errors += fail("save_companies() accepted a write with no prior "
                           "read - the journal entry would carry an empty diff "
                           "and undo would silently restore nothing")
        elif "without reading first" not in r:
            errors += fail(f"save_companies() refused a read-less write but the "
                           f"reason does not say why: {r[:60]}")
        if wrote:
            errors += fail(f"save_companies() wrote {wrote} despite having no "
                           f"before-state to journal against")
    finally:
        admin._LAST_COMPANIES, admin.write_atomic = saved_last, saved_write
    return errors



def check_review_findings() -> int:
    """The 2026-08-28 admin review's confirmed findings, held closed.

    Twelve findings survived adversarial verification; these are the ones a
    test can hold. Every check stubs write_atomic - a selftest that can write
    WILL write, on exactly the run where the thing it guards is broken.
    """
    import admin
    import build_board as bb
    errors = 0

    # 1. A refusal is never evidence of absence. discover_ats writes two note
    # shapes ("blocked at the door", "blocked at /careers (HTTP 403)") and
    # marks both retry_soon; matching only the first filed twenty mid-crawl
    # 403s as "probed, nothing found". Forty more "found X but unreadable"
    # records - a board FOUND and unreadable, reported as nothing found - are
    # caught by honouring the record's own retry_soon flag.
    saved_read = admin.read
    probe_log = {"a": {"note": "blocked at /careers (HTTP 403)", "retry_soon": True},
                 "b": {"note": "blocked at the door (HTTP 403)", "retry_soon": True},
                 "c": {"note": "greenhouse:x found but unreadable (HTTP 404)",
                       "retry_soon": True},
                 "d": {"note": "no board found", "retry_soon": False}}
    admin.read = lambda name, default=None: (probe_log if name == "discovery_log.json"
                                             else saved_read(name, default))
    try:
        for cid, want in (("a", "blocked"), ("b", "blocked"),
                          ("c", "blocked"), ("d", "none-found")):
            got = admin._probe(cid)["state"]
            if got != want:
                errors += fail(f"_probe filed {probe_log[cid]['note']!r} as "
                               f"{got}, not {want} - a refusal reported as "
                               f"evidence of absence")
    finally:
        admin.read = saved_read
    bb._DISCOVERY_LOG = probe_log
    try:
        if bb._probe_state("a") != "blocked" or bb._probe_state("c") != "blocked":
            errors += fail("build_board._probe_state files a refusal as "
                           "none-found - the public card side of the same bug")
    finally:
        bb._DISCOVERY_LOG = None

    # 2. act_patch has no ats backdoor. An ats smuggled in beside an allowed
    # field used to land unverified, bypassing set-board's live verify - the
    # write that points "prepared" at greenhouse/axon and reports Axon's ~500
    # requisitions as Prepared's.
    saved_write = admin.write_atomic
    admin.write_atomic = lambda name, data: (_ for _ in ()).throw(
        AssertionError(f"act_patch wrote {name} on a refused ats patch"))
    # save_companies journals BEFORE it writes, through journal.record's own
    # io - stubbing write_atomic alone let the mutation run put a phantom
    # entry in the real admin_journal.jsonl: a journalled change whose write
    # was intercepted, which the coverage check then reported as corruption.
    # The journal is stubbed for the same reason the writer is.
    import journal as _journal
    saved_record = _journal.record
    _journal.record = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("act_patch journalled on a refused ats patch"))
    try:
        r = admin.act_patch({"id": "prepared",
                             "fields": {"description": "x",
                                        "ats": {"type": "greenhouse", "ref": "axon"}}})
        if not (r.get("error") and "set-board" in r["error"]):
            errors += fail("act_patch accepted ats smuggled beside an allowed "
                           "field - the backdoor around board verification is "
                           "open again")
    except AssertionError as e:
        errors += fail(str(e))
    finally:
        admin.write_atomic = saved_write
        _journal.record = saved_record

    # 3. A dismissal is a ruling everywhere, not only off its queue. dismiss()
    # nests {queue: {key: rec}}; every metric consumer read only flat
    # "queue:key" keys, so every judgment through the generic dismiss buttons
    # vanished from done-counts, day/sitting counters and the why-coverage
    # meter.
    store = {}
    admin.read = lambda name, default=None: (store.get(name, {})
                                             if name == "admin_dismissed.json"
                                             else saved_read(name, default))
    admin.write_atomic = lambda name, data: store.__setitem__(name, data)
    # AND THE JOURNAL, for the same reason as check_save_needs_read. dismiss()
    # goes through save_decisions() now, and journal.record writes through its
    # own io - so stubbing write_atomic alone let this check append a real
    # entry to the real admin_journal.jsonl on EVERY run. Six of them landed
    # before this was caught. A selftest that writes to the thing it is
    # checking is the failure this file exists to prevent, and it caught me
    # twice in one day with the same mechanism.
    import journal as _journal
    saved_record = _journal.record
    _journal.record = lambda *a, **k: ("selftest", None)
    try:
        admin.dismiss("duplicates", "site:x.com", "checked both, not duplicates")
        if not admin.is_dismissed("duplicates", "site:x.com"):
            errors += fail("dismiss() no longer registers with is_dismissed")
        if admin.rulings_by_queue().get("duplicates", 0) < 1:
            errors += fail("a dismissal does not count in rulings_by_queue - "
                           "the judgment leaves the queue and every done-meter "
                           "says nothing happened")
        if "duplicates" not in [q for _t, q in admin._ruling_stamps()]:
            errors += fail("a dismissal is missing from _ruling_stamps - the "
                           "day and sitting counters cannot see it")
        # and the flat legacy shape still counts
        store["admin_dismissed.json"]["miscategorized:oldco"] = {
            "on": "2026-08-01", "at": "2026-08-01T12:00:00", "why": "legacy"}
        if admin.rulings_by_queue().get("miscategorized", 0) < 1:
            errors += fail("the flat legacy dismissal shape stopped counting")
    finally:
        admin.read, admin.write_atomic = saved_read, saved_write
        _journal.record = saved_record

    # 4. A merge unions `also` and folds in the drop's primary placement. A
    # duplicate pair is often the same vendor filed on two shelves - that is
    # why there are two records - and the merge kept only the survivor's.
    captured = {}
    admin.write_atomic = lambda name, data: captured.__setitem__(name, data)
    saved_save = admin.save_companies
    saved_last = admin._LAST_COMPANIES
    try:
        cos = [{"id": "keep1", "name": "Keep", "sector": "Public Safety",
                "category": "Police",
                "also": [{"sector": "Courts & Justice",
                          "category": "Courts & Case Management"}]},
               {"id": "drop1", "name": "Drop", "sector": "General Gov",
                "category": "Permitting & Licensing",
                "also": [{"sector": "Public Works", "category": "Streets"}]}]
        admin.read_companies = lambda: cos
        admin.save_companies = lambda *a, **k: None
        admin.validate = lambda c: None
        r = admin.act_merge({"keep": "keep1", "drop": "drop1", "why": "test"})
        keep = cos[0]
        pairs = {(a["sector"], a["category"]) for a in keep.get("also", [])}
        if ("Public Works", "Streets") not in pairs:
            errors += fail("act_merge dropped the drop record's `also` "
                           "placements when the survivor already had some")
        if ("General Gov", "Permitting & Licensing") not in pairs:
            errors += fail("act_merge lost the drop record's PRIMARY placement "
                           "- the shelf it was actually filed on")
    finally:
        import importlib
        importlib.reload(admin)
    return errors


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
        # 6to4 wrapping a genuinely PUBLIC host, and still refused. This one
        # sat in OUTSIDE until 2026-08-26, asserting that we must be willing to
        # fetch it - and that assertion broke the daily refresh for two days,
        # because the answer came from ip.is_global and Python 3.12 disagrees
        # with 3.11 about whether 2002::/16 is globally reachable. The gate now
        # decides for itself and decides against: 6to4 was deprecated by RFC
        # 7526 in 2015, and refusing a real one costs a link nobody was going
        # to click.
        ("2002:808:808::", "6to4 wrapping public 8.8.8.8 - deprecated tunnel, "
                           "and the outer address says nothing about the passenger"),
    ]
    # the other half of the rule: this is a guard, not a ban on the internet
    OUTSIDE = [
        ("8.8.8.8", "an ordinary public address"),
        ("2606:4700::1111", "an ordinary public v6 address"),
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
    v6_unsupported: list[str] = []
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
            # A host with no IPv6 stack cannot form an IPv6 address at all, and
            # getaddrinfo says so with the same gaierror a bad hostname gets.
            # GitHub's ubuntu runners are such a host. Reporting that as "the
            # connect gate could not read ::1" states a failure of the guard
            # when the truth is a fact about the machine - the exact confusion
            # this whole board exists to refuse, in its own test suite.
            #
            # The guard is NOT going untested. _public() was asked about this
            # same address directly a few lines above, with no network
            # involved, and that is the part that decides. This call only
            # confirms the gate is wired into getaddrinfo, and on a machine
            # that cannot express the address there is nothing to wire.
            if ":" in text:
                v6_unsupported.append(text)
            else:
                bad += fail(f"the connect gate could not read {text} as an "
                            f"address, and this host can express it")

    if v6_unsupported:
        note(f"no IPv6 on this host, so {len(v6_unsupported)} address(es) could "
             f"not be handed to getaddrinfo ({', '.join(v6_unsupported[:3])}"
             f"{'...' if len(v6_unsupported) > 3 else ''}). _public() was asked "
             f"about each of them directly and answered; only the getaddrinfo "
             f"wiring went untested for those.")

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

        # THE STATIC ROUTE ALLOWLIST. CLAUDE.md credits this function with
        # asserting it and it did not: serving the repository root is what
        # handed out /.git/config, /scripts/admin.py and /data/companies.json,
        # and "everything else is 404 by construction" was a claim in a
        # docstring with no test under it. Six routes are served; these five
        # are the ones whose exposure was the original bug.
        for path in ("/data/companies.json", "/.git/config", "/scripts/admin.py",
                     "/CLAUDE.md", "/data/admin_journal.jsonl"):
            code, _h, _b = ask(path, {"X-Admin-Token": admin.TOKEN})
            if code != 404:
                bad += fail(f"{path} answered {code}, not 404 - the admin is "
                            f"serving files off disk again, which is exactly "
                            f"the exposure the route allowlist replaced")
        for path in ("/", "/admin.html"):
            code, _h, _b = ask(path, {"X-Admin-Token": admin.TOKEN})
            if code != 200:
                bad += fail(f"{path} answered {code}, not 200 - the admin page "
                            f"itself stopped being served")

        # THE HOST CHECK, the other thing CLAUDE.md credits here. DNS
        # rebinding beats every same-origin protection because evil.example
        # can resolve to 127.0.0.1, but the browser still fills Host in from
        # the address bar, so a request addressed to anything but us is
        # refused with 421.
        code, _h, _b = ask("/api/queues", {"X-Admin-Token": admin.TOKEN,
                                           "Host": "evil.example"})
        if code != 421:
            bad += fail(f"a request addressed to evil.example answered {code}, "
                        f"not 421 - DNS rebinding is not being refused")

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


def check_publish_gate_legs() -> int:
    """Both legs of the publish gate must be able to fire.

    The companies-with-an-opening leg read `companies_hiring` out of
    meta.json and NOTHING has ever written that key, so `if was and ...` was
    permanently false: the gate protecting the public site had been running on
    one leg. A gate with a dead leg is not a gate, and this is the quiet kind
    of wrong - it throws nothing and publishes.

    The baseline now comes from the history snapshots, best of the last week,
    for the same reason previous_snapshot() does: a broken run writes a
    collapsed snapshot too, and comparing against yesterday alone lets one bad
    day disarm the gate on the next.
    """
    import build_site, json as _json, tempfile, shutil
    import pathlib as _pl
    errors = 0
    real = build_site.ROOT
    tmp = _pl.Path(tempfile.mkdtemp())
    (tmp / "data" / "history").mkdir(parents=True)
    build_site.ROOT = tmp
    try:
        for d, h in [("2026-08-22", 300), ("2026-08-23", 305), ("2026-08-24", 302),
                     ("2026-08-25", 310), ("2026-08-26", 308), ("2026-08-27", 301),
                     ("2026-08-28", 304)]:
            (tmp / "data" / "history" / f"{d}.json").write_text(_json.dumps(
                {"date": d, "ids": [f"p{i}" for i in range(4000)], "hiring": h}))
        if build_site.previous_hiring() != 310:
            errors += fail(f"previous_hiring() returned "
                           f"{build_site.previous_hiring()}, not the week's best "
                           f"(310) - comparing against yesterday lets a broken "
                           f"run disarm the gate")
        board = {"postings": [{"id": f"p{i}"} for i in range(4000)],
                 "organizations": [{"id": f"o{i}", "open_roles": 1} for i in range(150)]}
        if not any("companies with an opening" in b
                   for b in build_site.sanity_check(board)):
            errors += fail("the publish gate did not object to companies-with-an-"
                           "opening halving (310 to 150) - that leg is dead again "
                           "and a broken fetcher will publish")
        board["organizations"] = [{"id": f"o{i}", "open_roles": 1} for i in range(300)]
        if any("companies with an opening" in b
               for b in build_site.sanity_check(board)):
            errors += fail("the publish gate objected to an ordinary 310 to 300 "
                           "move - a gate that cries wolf gets forced past")
        # No baseline is not a failure, and must not be treated as a collapse.
        for d in ("2026-08-22", "2026-08-23"):
            (tmp / "data" / "history" / f"{d}.json").write_text(
                _json.dumps({"date": d, "ids": []}))
        for f_ in sorted((tmp / "data" / "history").glob("*.json"))[2:]:
            f_.unlink()
        if build_site.previous_hiring() is not None:
            errors += fail("previous_hiring() invented a baseline from snapshots "
                           "that carry no hiring count")
        # And the writer, because the reader alone cannot tell you the field
        # stopped being written: the leg would just go quiet again a week
        # later, which is exactly how it died the first time. Source-level,
        # since the write happens at the end of a 20-minute crawl.
        import inspect
        import build_board as _bb
        src = "\n".join(l.split("#")[0] for l in inspect.getsource(_bb).splitlines())
        if '"hiring": sum(' not in src:
            errors += fail("build_board no longer records `hiring` in the history "
                           "snapshot, so the publish gate's second leg loses its "
                           "baseline and goes inert within a week")
    finally:
        build_site.ROOT = real
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def _journal_fingerprint() -> tuple:
    """(lines, bytes) of the live journal, or (0, 0) if there is none."""
    p = DATA / "admin_journal.jsonl"
    try:
        raw = p.read_bytes()
    except OSError:
        return (0, 0)
    return (raw.count(b"\n"), len(raw))


def check_checks_can_fail() -> int:
    """No check may crash where it means to fail.

    check_admin_gates accumulated into `bad` and three of its assertions said
    `errors +=`. Python raises NameError there, so all three CSV-gate guards
    would have died mid-suite instead of failing: the explanation they exist
    to print never reached anyone, and every check after them was skipped. It
    survived because a crash also stops the build - it just stops it
    uselessly, and a stack trace is not the sentence somebody needs to read.

    This is the class, not the instance: every check_* function is parsed and
    any accumulator it augments without ever assigning is reported. Static,
    because these paths only execute when something else is already broken,
    which is exactly when nobody wants a second bug.
    """
    import ast
    errors = 0
    tree = ast.parse(pathlib.Path(__file__).read_text())
    for fn in tree.body:
        if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("check_")):
            continue
        augmented = {n.target.id for n in ast.walk(fn)
                     if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)}
        assigned = {t.id for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.For))
                    for t in ast.walk(n.targets[0] if isinstance(n, ast.Assign) else n.target)
                    if isinstance(t, ast.Name)}
        args = {a.arg for a in fn.args.args}
        undefined = augmented - assigned - args
        if undefined:
            errors += fail(f"{fn.name}() augments {sorted(undefined)} without "
                           f"ever assigning it - that path raises NameError "
                           f"instead of failing, and skips every check after it")
    return errors


def check_decision_files_are_journalled() -> int:
    """A ruling in a decision file must be as reversible as one in companies.json.

    journal.py motivates itself with "one click on 'All out' writes a ruling
    for 108 companies and all 108 pass every check we have" - and that click
    wrote vendor_scope_decisions.json through write_atomic with no journal
    entry. The exact scenario the journal exists for was the one it did not
    cover: no before-image, nothing for --undo, and nothing for --reopen,
    which matters most here because a scope ruling is never re-asked.

    Runs against a temp DATA dir. Writing to the owner's real files from a
    test is the thing CLAUDE.md forbids in capitals.
    """
    import admin as _a, journal as _j, tempfile, shutil, json as _json
    import pathlib as _pl
    errors = 0
    tmp = _pl.Path(tempfile.mkdtemp())
    ra, rj, rl = _a.DATA, _j.DATA, _j.LOG
    _a.DATA, _j.DATA, _j.LOG = tmp, tmp, tmp / "admin_journal.jsonl"
    try:
        names = [f"Vendor {i}" for i in range(108)]
        r = _a.act_vendor_scope_all({"names": names, "call": "out", "by": "wyeth"})
        if not r.get("error") or "limit of" not in r["error"]:
            errors += fail("a 108-vendor bulk ruling was not stopped for "
                           "confirmation - BLAST does not reach the decision "
                           "files, which is the click journal.py was written for")
        if (tmp / "vendor_scope_decisions.json").exists():
            errors += fail("a refused bulk ruling still wrote the file")

        r = _a.act_vendor_scope_all({"names": names, "call": "out", "force": True,
                                     "by": "wyeth", "why": "not govtech"})
        if r.get("error"):
            errors += fail(f"a confirmed bulk ruling was refused: {r['error'][:70]}")
        log = tmp / "admin_journal.jsonl"
        if not log.exists():
            errors += fail("a decision-file ruling wrote nothing to the journal, "
                           "so --undo and --reopen have nothing to act on")
        else:
            e = [_json.loads(l) for l in log.read_text().strip().split("\n")][-1]
            if e["file"] != "vendor_scope_decisions.json":
                errors += fail(f"journal entry names {e['file']}, not the "
                               f"decision file that was written")
            if len(e["changes"]) != 108:
                errors += fail(f"the journal recorded {len(e['changes'])} of 108 "
                               f"records - a bulk ruling must be ONE entry "
                               f"covering all of it, or undo restores half")
            if e.get("why") != "not govtech":
                errors += fail("the ruling's reason did not reach the journal")

        # A pure addition is not a rewrite. The runaway guard measures records
        # that already existed, or the first bulk ruling into an empty file is
        # 100% of it and force cannot help - force only lifts BLAST.
        _c, refusal = _j.check("vendor_scope_decisions.json", {},
                               {f"v{i}": {} for i in range(108)}, force=True)
        if refusal:
            errors += fail(f"the runaway guard refused 108 pure additions to an "
                           f"empty file: {refusal[:60]}")
        # and it still refuses a real rewrite
        b = [{"id": f"c{i}", "s": "A"} for i in range(2113)]
        a = [{"id": f"c{i}", "s": "B" if i < 800 else "A"} for i in range(2113)]
        _c, refusal = _j.check("companies.json", b, a, force=True)
        if not refusal:
            errors += fail("the runaway guard no longer refuses rewriting 800 of "
                           "2,113 existing companies - loosening it for additions "
                           "must not loosen it for destruction")
    finally:
        _a.DATA, _j.DATA, _j.LOG = ra, rj, rl
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_share_cards() -> int:
    """Every og:image the middleware can name must exist, and ship.

    A link that unfurls with a broken picture is worse than the naked url it
    replaced, and the two halves live in different languages: the middleware
    picks a filename, make_og_cards.py writes one, and nothing connected them.
    Source-level on the middleware because a Worker cannot run here.
    """
    import re
    errors = 0
    mw = ROOT / "functions" / "_middleware.js"
    if not mw.exists():
        return fail("functions/_middleware.js is gone - every url shares one "
                    "title again and nothing unfurls")
    src = mw.read_text()
    named = set(re.findall(r"/assets/og/([a-z]+)\.png", src))
    named |= {t for t in re.findall(r"^\s+(\w+): \[\"", src, re.M) if t != "saved"}
    have = {p.stem for p in (ROOT / "assets" / "og").glob("*.png")}
    for miss in sorted(named - have):
        errors += fail(f"the middleware points at /assets/og/{miss}.png and no "
                       f"such card exists - run scripts/make_og_cards.py")
    ship = (ROOT / "scripts" / "build_site.py").read_text()
    if '"og"' not in ship:
        errors += fail("build_site does not copy assets/og into the published "
                       "tree, so every og:image is a 404 on the live site")
    # and the middleware must read the brand rather than restating it
    if 'from "./_brand.js"' not in src:
        errors += fail("_middleware.js hardcodes the site name or domain "
                       "instead of importing _brand.js")
    return errors


def check_prerendered_pages() -> int:
    """The static pages must say what they promise, and only that.

    Head tags fix how a link unfurls; they do not fix crawling. Bing,
    LinkedIn's fetcher and most AI crawlers never run JavaScript, so they saw
    an empty shell where 2,113 company records should be.

    The trap these caught in the writing: a page headed "Govtech sales jobs in
    California" listed Backend Software Engineer and Administrative
    Coordinator, because the board carries every posting on purpose. A page
    that promises sales and delivers engineering is the same defect as a
    filter option matching nothing, and only visible by looking at it.
    """
    import build_site, tempfile, shutil
    import pathlib as _pl
    errors = 0
    brand = {"site": "https://example.test", "name": "SLED JOBS",
             "palette": {k: {"hex": "#000000"} for k in
                         ("ice", "belly", "penguin", "frost", "badge", "beak")},
             "derived": {"deep_fog": {"hex": "#556F82"},
                         "dark": {"badge": {"hex": "#478EF5"}}}}
    board = {"organizations": [
                 {"id": "seller", "name": "Seller Co", "open_roles": 2,
                  "quota_roles": 1, "sector": "General Gov",
                  "description": "Sells things to cities"},
                 {"id": "quiet", "name": "Quiet Co", "open_roles": 0}],
             "postings": [
                 {"id": "1", "company_id": "seller", "company": "Seller Co",
                  "title": "Account Executive", "family": "gtm",
                  "quota_carrying": True, "office": {"state": "CA"}},
                 {"id": "2", "company_id": "seller", "company": "Seller Co",
                  "title": "Backend Software Engineer", "family": "engineering",
                  "office": {"state": "CA"}}]}
    tmp = _pl.Path(tempfile.mkdtemp())
    try:
        n_co = build_site.write_company_pages(tmp, board, brand)
        if n_co != 1:
            errors += fail(f"wrote {n_co} company pages for one hiring company - "
                           f"a company with nothing open must not get a page, or "
                           f"the index fills with near-identical empty documents")
        if (tmp / "c" / "quiet.html").exists():
            errors += fail("a company with nothing open got a prerendered page")
        co = (tmp / "c" / "seller.html").read_text()
        if "Account Executive" not in co:
            errors += fail("a company page does not list the company's roles")
        if 'canonical" href="https://example.test/c/seller"' not in co:
            errors += fail("a company page is not canonical to itself, so it "
                           "competes with the app's ?co= view for the same company")

        # Conference pages: the roster is what WE track, never the show's own
        # exhibitor list, and every page has to say so. "52 exhibitors" read as
        # a claim about the floor rather than about us is the quiet kind of
        # overstatement this project refuses - we hold 35 of 93 tags and swept
        # eleven floors.
        board["conferences"] = [{"tag": "GOOD 2026", "name": "Good Conf",
                                 "dates": "July 25-28, 2027", "city": "Anaheim, CA",
                                 "department": "Police", "url": "https://good.test"}]
        board["organizations"][0]["conference"] = "GOOD 2026"
        board["organizations"][1]["conference"] = "GOOD 2026"
        n_ev = build_site.write_conference_pages(tmp, board, brand)
        if n_ev != 1:
            errors += fail(f"wrote {n_ev} conference pages, expected 1")
        ev = (tmp / "e" / "good-2026.html").read_text()
        if "1 of the 2 exhibitors we track here are hiring" not in ev:
            errors += fail("the conference page does not count hiring exhibitors "
                           "against the roster we actually hold")
        if "not the show" not in ev:
            errors += fail("a conference page presents our partial roster as the "
                           "show's exhibitor list - a short list must read as us "
                           "knowing less, never as the floor being small")
        if "/c/seller.html" not in ev:
            errors += fail("a hiring exhibitor is not linked to its own page")

        build_site.write_state_pages(tmp, board, brand)
        st = (tmp / "s" / "ca.html").read_text()
        if "Account Executive" not in st:
            errors += fail("a state page omits a sales role sitting in that state")
        if "Backend Software Engineer" in st:
            errors += fail("a page headed 'Govtech sales jobs' lists an "
                           "engineering role - it promises sales and delivers "
                           "something else")
        if "Quiet Co" in st:
            errors += fail("a state page lists a company with nothing open")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_feeds_and_structured_data() -> int:
    """Feeds say only what the data supports, and markup only where we read.

    A calendar entry on the wrong day is worse than none, because somebody
    books travel around it - so an unparseable date omits the event rather
    than guessing. JobPosting markup is emitted only for the 2,711 postings
    whose description was actually read, never carries a validThrough we do
    not know, and never claims a baseSalary: the board holds a stated range
    for some postings, but a range read out of prose is not the same claim as
    an employer's structured salary and must not be dressed as one.
    """
    import build_site, tempfile, shutil, xml.dom.minidom
    import pathlib as _pl
    errors = 0
    brand = {"site": "https://example.test", "name": "SLED JOBS",
             "domain": "example.test"}
    board = {"generated": "2026-08-29",
             "postings": [
                 {"id": "a", "company": "A Co", "title": "AE", "family": "gtm",
                  "quota_carrying": True, "first_seen": "2026-08-29"},
                 {"id": "b", "company": "B Co", "title": "Old AE", "family": "gtm",
                  "quota_carrying": True, "first_seen": "2026-08-01"}],
             "conferences": [
                 {"name": "Good Conf", "tag": "GOOD 2026", "dates": "July 25-28, 2027",
                  "city": "Anaheim, CA", "block": "Public safety"},
                 {"name": "Undated Conf", "tag": "UND 2026", "dates": "TBA",
                  "block": "Public safety"}]}
    tmp = _pl.Path(tempfile.mkdtemp())
    try:
        out = build_site.write_feeds(tmp, board, brand)
        rss = (tmp / "feed.xml").read_text()
        xml.dom.minidom.parseString(rss)
        if "AE at A Co" not in rss:
            errors += fail("a role first seen on this run is missing from feed.xml")
        if "Old AE" in rss:
            errors += fail("feed.xml carries a role from an earlier run - 'new' "
                           "must mean new, or the feed repeats itself forever")
        cal = (tmp / "conferences.ics").read_text()
        if "DTSTART;VALUE=DATE:20270725" not in cal:
            errors += fail("the calendar lost a dated conference")
        if "Undated Conf" in cal:
            errors += fail("a conference with an unparseable date reached the "
                           "calendar - a wrong date is worse than no entry, "
                           "because somebody books travel around it")
        if " " in [l for l in cal.splitlines() if l.startswith("UID:")][0]:
            errors += fail("an iCalendar UID contains a space; some clients "
                           "drop the event")
        if out["events"] != 1:
            errors += fail(f"reported {out['events']} dated events, expected 1")

        # the middleware's structured data, source-level. COMMENTS STRIPPED
        # FIRST: the words this looks for are exactly the words the file uses
        # to explain why it omits them, and a check that trips on the prose
        # defending it is the third time that has happened in this repo.
        import re as _re
        raw = (ROOT / "functions" / "_middleware.js").read_text()
        mw = _re.sub(r"/\*.*?\*/", "", raw, flags=_re.S)
        mw = "\n".join(l.split("//")[0] for l in mw.splitlines())
        if "validThrough" in mw:
            errors += fail("the JobPosting markup emits validThrough - we do not "
                           "know when a posting expires and inventing an expiry "
                           "is how a board advertises dead roles")
        if "baseSalary" in mw:
            errors += fail("the JobPosting markup claims a baseSalary; a range "
                           "read out of prose is not an employer's structured claim")
        if "r.ld ?" not in mw:
            errors += fail("structured data is no longer gated on the description "
                           "having been read, so it asserts a completeness the "
                           "board does not have for 1,492 postings")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_map_says_what_it_omits() -> int:
    """The map plots placed desks and must admit the ones it could not place.

    A city we hold no coordinate for is left out of board.json entirely - "a
    city at no coordinate is not a city at 0,0" - so the map is always a
    subset. A map that silently drops what it cannot place is a false "nothing
    near you", the same failure as a page scan reporting no listings when it
    could not read.

    Source-level on index.html: the drawing needs a canvas and a browser, but
    the sentence that keeps it honest is text and can be checked here.
    """
    errors = 0
    src = (ROOT / "index.html").read_text()
    if "function mapView()" not in src:
        return fail("the map view is gone - the project calls itself a map")
    i = src.index("function mapView()")
    body = src[i:i + 4000]
    for phrase, why in (
        ("could not place", "the map must say how many cities it could not place"),
        ("no city at all", "the map must say how many postings name no city"),
        ("neither is a zero", "the map must say that an unplaced desk is not an "
                              "absent one, which is this project's whole rule")):
        if phrase not in body:
            errors += fail(f"the map view no longer says {phrase!r}: {why}")
    if "D.cities" not in src:
        errors += fail("the map is not reading the geocoded cities the board "
                       "ships, so it is deriving coordinates from something else")
    return errors


def check_weekly_report_is_honest() -> int:
    """The week's report must not assemble growth out of a schema change.

    On 2026-08-23 posting ids gained a url+location hash, so the 08-22 and
    08-23 snapshots share not one id out of three thousand. Comparing across
    that boundary counts every posting as new: the first run of report.py said
    "580 new quota-carrying roles" and "3,332 came off the board", which is a
    growth story made entirely of an id change. Real adjacent days overlap by
    thousands, so a near-empty intersection means the two are not comparable.
    """
    import report
    errors = 0
    real = report.DATA
    import tempfile, shutil, json as _json
    import pathlib as _pl
    tmp = _pl.Path(tempfile.mkdtemp())
    (tmp / "history").mkdir()
    report.DATA = tmp
    try:
        # old scheme, then a hard break, then two comparable days
        (tmp / "history" / "2026-08-01.json").write_text(_json.dumps(
            {"date": "2026-08-01", "ids": [f"old{i}" for i in range(500)]}))
        (tmp / "history" / "2026-08-05.json").write_text(_json.dumps(
            {"date": "2026-08-05", "ids": [f"new{i}" for i in range(500)]}))
        (tmp / "history" / "2026-08-08.json").write_text(_json.dumps(
            {"date": "2026-08-08", "ids": [f"new{i}" for i in range(520)]}))
        (tmp / "board.json").write_text(_json.dumps(
            {"postings": [{"id": f"new{i}", "quota_carrying": i < 5,
                           "sector": "General Gov", "company": "C",
                           "company_id": "c"} for i in range(520)],
             "organizations": [{"id": "c", "open_roles": 1}]}))
        r = report.week(None)
        if r.get("error"):
            errors += fail(f"the report refused a comparable week: {r['error']}")
        elif r["from"] != "2026-08-05":
            errors += fail(f"the report compared against {r['from']}, crossing the "
                           f"id-scheme break at 2026-08-05 - every posting reads "
                           f"as new and the post invents a growth story")
        elif not r.get("note"):
            errors += fail("the report silently shortened its own span without "
                           "saying why")
        elif r["new"] != 20:
            errors += fail(f"reported {r['new']} new postings across a comparable "
                           f"pair, expected 20")
    finally:
        report.DATA = real
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_coverage_and_removed() -> int:
    """The board must publish what it could not read, and what left.

    "839 of 1,722 monitored" was wrong in both directions for months, because
    it counted a careers page nothing can enumerate the same as a Greenhouse
    API and counted companies with no board at all as a gap to be closed. The
    five-way split is the honest shape and it lived only in a script nobody
    ran.

    removed.json is the other half of the same honesty: the board could say
    what arrived and never what left. It must refuse to compute across an id
    change, where every posting looks removed - that would publish thousands
    of phantom departures.
    """
    import build_board as bb
    errors = 0
    src = "\n".join(l.split("#")[0] for l in
                    pathlib.Path(bb.__file__).read_text().splitlines())
    for needle, why in (
        ('payload["coverage"]', "the five-way coverage split is not written to "
                              "board.json, so the site cannot say what it "
                              "could not read"),
        ('"removed.json"', "removed.json is not written, so a role a reader "
                           "saw yesterday just vanishes"),
        ('"board_checked_on"', "the date we last looked for a board is not "
                               "carried across, so 'no public board' reads as "
                               "permanent rather than as of a day"),
        ('overlap >= 0.2', "removed.json does not check that the two snapshots "
                           "are comparable - across an id change every posting "
                           "reads as removed")):
        if needle not in src:
            errors += fail(why)

    # the split itself must total the company count and name all five states
    import coverage as cov, json as _json
    companies = _json.loads((DATA / "companies.json").read_text())
    log = _json.loads((DATA / "discovery_log.json").read_text())
    board = _json.loads((DATA / "board.json").read_text())
    orgs = {o["id"]: o for o in board.get("organizations", [])}
    split = {}
    for c in companies:
        st = cov.state(c, log.get(c["id"]), orgs.get(c["id"]))
        split[st] = split.get(st, 0) + 1
    if sum(split.values()) != len(companies):
        errors += fail(f"the coverage split totals {sum(split.values())} of "
                       f"{len(companies)} companies - some company is in no "
                       f"state at all")
    missing = {"structured", "page only", "blocked", "absent", "unchecked"} - set(split)
    if missing:
        errors += fail(f"the coverage split has no {sorted(missing)} bucket; a "
                       f"missing state is how 'page only' got added to "
                       f"'structured' and called coverage")
    return errors


def check_busy_port_does_not_traceback() -> int:
    """Starting a second admin must explain itself, not raise.

    An occupied port is almost always an admin the owner already has open in
    another terminal tab, and this used to answer with twenty lines of
    socketserver internals ending in "OSError: [Errno 48] Address already in
    use". True, and useless: the situation is "your other tab has one" and the
    fix is to go and look at it.

    It matters more here than it would anywhere else. The ruling code lives
    ONLY in the scrollback of the terminal that started the server - no route
    serves it and it is not in the page, deliberately. Somebody who reads that
    traceback as "it is broken" and kills the process to clear the port has
    destroyed the one copy of their own code. That has happened twice and cost
    a session's rulings each time.

    Tested by actually holding a port, because the failure is an exception and
    a source scan cannot see whether it is raised.
    """
    import socket as _s
    admin = _admin()
    bad = 0
    held = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    held.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    # fail() PRINTS, so it must not be inside the redirect. The first version
    # of this check wrapped the whole thing, the mutation test removed the
    # guard, admin.main() raised exactly as it should have - and the FAIL line
    # went into the StringIO instead of to the terminal. A check whose failure
    # message is captured by the check is a check that cannot report.
    argv, out = sys.argv, io.StringIO()
    err = None
    try:
        sys.argv = ["admin.py", "--port", str(port)]
        import contextlib
        with contextlib.redirect_stdout(out):
            try:
                rc = admin.main()
            except OSError as e:
                err, rc = e, None
    finally:
        sys.argv = argv
        held.close()
    if err is not None:
        bad += fail(f"admin.py raised {err!r} on a busy port instead of "
                    f"explaining it. The person reading that traceback kills "
                    f"the admin that holds their ruling code")
    if rc is not None:
        said = out.getvalue()
        if rc == 0:
            bad += fail("admin.py exited 0 on a busy port - it did not start a "
                        "server, so it must not report success")
        for want in ("already listening", "another terminal", "--port"):
            if want not in said:
                bad += fail(f"the busy-port message does not mention {want!r}; "
                            f"it has to say WHERE the running one is and how "
                            f"to start a second without killing it")
        if "DO NOT kill it" not in said:
            bad += fail("the busy-port message no longer warns against killing "
                        "the running admin - that is the action it exists to "
                        "prevent, and it costs the console code every time")
    return bad


def check_structured_data_claims_no_posting_date() -> int:
    """JobPosting must not carry a datePosted, because we do not have one.

    It emitted `first_seen`, the day THIS BOARD first saw the row. 2,183 of
    3,524 structured blocks claimed 2026-08-18 or 2026-08-19 - our first two
    crawls - as the day the employer posted. A role advertised since spring
    read as posted the morning we started looking.

    index.html already refuses to tell a HUMAN that, in as many words: "Saying
    'appeared' would file our crawl date as a fact about somebody's hiring,
    which is the same species of claim as reporting a page we could not read as
    'no jobs here'." The page told the truth to a reader and told Google the
    other thing.

    Nothing in this repository reads a posted date from any board, so there is
    no true value being passed over - the field was manufactured. It is
    withheld entirely, like validThrough and baseSalary before it. Optional in
    the spec; a wrong one is not.
    """
    bad = 0
    mw = (ROOT / "functions" / "_middleware.js").read_text()
    code = re.sub(r"//.*$", "", mw, flags=re.M)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    if "datePosted" in code:
        bad += fail("the middleware emits datePosted again. The only date this "
                    "board holds is when WE first saw a row, and publishing it "
                    "as the employer's posting date is our crawler's history "
                    "dressed as their hiring")
    meta = ROOT / "public" / "meta-roles.json"
    if meta.exists():
        roles = json.loads(meta.read_text())
        roles = roles.get("roles", roles)
        dated = sum(1 for v in roles.values()
                    if isinstance(v, dict) and v.get("ld") and v.get("d"))
        if dated:
            bad += fail(f"{dated:,} structured roles still carry a date field "
                        f"for the middleware to publish as datePosted")
    src = (ROOT / "scripts" / "build_site.py").read_text()
    blk = src[src.index('r["ld"] = 1'):]
    blk = blk[:blk.index("roles[p_[")]
    if re.search(r'r\["d"\]\s*=', blk):
        bad += fail("build_site writes a date onto the structured-data record "
                    "again - the only one available is our crawl date")

    # AND EVERY BLOCK MUST BE ABLE TO SAY WHERE THE JOB IS.
    #
    # jobLocation is not optional. 2,083 of 3,524 blocks carried neither a city
    # nor a state, so 59% of this board's structured data was a claim no
    # aggregator can accept. 533 of those say `remote` on the posting itself
    # and get jobLocationType TELECOMMUTE; the rest get no block at all. The
    # raw location string is NOT an acceptable substitute - CITY_CASES exists
    # because "Montreal, QB" and "Australia - Remote" would be stamped
    # addressCountry US.
    if meta.exists():
        roles = json.loads(meta.read_text())
        roles = roles.get("roles", roles)
        blocks = [v for v in roles.values()
                  if isinstance(v, dict) and v.get("ld")]
        placeless = [v for v in blocks
                     if not v.get("ci") and not v.get("st") and not v.get("tc")]
        if placeless:
            bad += fail(f"{len(placeless):,} JobPosting blocks state no city, "
                        f"no state and no TELECOMMUTE. jobLocation is required "
                        f"and those blocks are invalid - a job claim we cannot "
                        f"complete is worse than no claim")
    mwcode = re.sub(r"//.*$", "", mw, flags=re.M)
    if "TELECOMMUTE" not in mwcode:
        bad += fail("the middleware no longer emits jobLocationType for a "
                    "remote posting, so 533 valid blocks lose the only "
                    "location statement they can make")
    return bad


def check_middleware_separates_unreadable_from_gone() -> int:
    """One failed fetch of our own index must not noindex the whole board.

    index() returns null when /meta-roles.json answers non-200 or unparseable
    JSON: a deploy mid-flight, a WAF page, a 503. The gone-role branch tests
    `!r`, and with a null index EVERY role is falsy - so a single bad fetch
    would serve "That role is no longer listed" with noindex on all 4,439 role
    pages at once, days after submitting every one to Google.

    That is the asymmetric error at the largest scale available on this site,
    and the same shape as reporting a page we could not read as a company with
    no jobs.

    AND THE PROTOTYPE CHAIN. `idx.roles[role]` walks it, so ?role=constructor
    returns a truthy function, passes the `!r` guard, and renders
    "undefined at undefined - SLED JOBS" self-canonical with no noindex: an
    indexable page asserting a company that does not exist, reachable from the
    address bar.

    Source-checked, because there is no JS engine here and both failures are a
    missing guard rather than wrong output.
    """
    bad = 0
    mw = (ROOT / "functions" / "_middleware.js").read_text()
    code = re.sub(r"//.*$", "", mw, flags=re.M)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    flat = re.sub(r"\s+", "", code)

    if "if(!idx||!idx.roles)returnnull" not in flat:
        bad += fail("the middleware does not bail out before the gone-role "
                    "branch when the index is unreadable. A 503 on "
                    "/meta-roles.json would mark all 4,439 role pages noindex "
                    "and tell every crawler the jobs no longer exist")
    if "Object.hasOwn" not in code:
        bad += fail("the middleware looks roles or companies up without an "
                    "own-property check, so ?role=constructor and ?co=toString "
                    "render an indexable page about a company that does not "
                    "exist")
    # the own() helper has to actually be used, not merely defined
    if flat.count("own(") < 2:
        bad += fail("the own() guard is defined but not used on both the role "
                    "and company lookups")
    return bad


TRIPLE_D = chr(34) * 3
TRIPLE_S = chr(39) * 3


def check_pay_report_states_what_it_omits() -> int:
    """A pay report is a number somebody negotiates against. It must not lie.

    147 of 618 quota-carrying postings state an annual US-dollar figure. Every
    band describes those 147, and a reader who takes them for the market rate
    is misled by an omission rather than an error - so the ratio is printed
    before anything else, not in a footnote.

    The checks below are the refusals, each of which is a number the report
    could have printed and does not:
      - hourly and monthly are counted and EXCLUDED, never multiplied by 2,080
      - one currency only
      - no band under MIN_N, and the thin ones are named rather than dropped
      - every cut says what share of the sample it can speak for: office_state
        clears the floor on CA and NY while covering half the sample across 21
        states, and two pins is not a map
    """
    bad = 0
    pr = _import_pay_report()
    r = pr.report()
    if not r.get("stated_annual_usd"):
        return 0
    # THE FLOOR'S VALUE, not just consistency with itself. Comparing each band
    # against pr.MIN_N is circular: setting MIN_N = 1 publishes a band of one
    # posting and passes, because the check moves with the constant. Five is
    # the point below which a median stops being a summary and starts being a
    # list of specific employers' offers.
    if pr.MIN_N < 5:
        bad += fail(f"pay_report.MIN_N is {pr.MIN_N}. Below five a median is "
                    f"not a market rate, it is a handful of specific offers "
                    f"reprinted with a statistic's authority")
    for name, bands in (r.get("bands") or {}).items():
        for label, b in bands.items():
            if not isinstance(b, dict):
                bad += fail(f"pay_report published a {name} band {label!r} "
                            f"that is not a band: {b!r}")
                continue
            if b["n"] < pr.MIN_N:
                bad += fail(f"pay_report published a {name} band {label!r} with "
                            f"n={b['n']}, under its own floor of {pr.MIN_N} - a "
                            f"median of a handful is two employers' opinions "
                            f"wearing a statistic's authority")
            if not (b["p25"] <= b["median"] <= b["p75"]):
                bad += fail(f"{name}/{label} percentiles are out of order: "
                            f"{b['p25']} / {b['median']} / {b['p75']}")
    if r.get("share_stating_pay") is None:
        bad += fail("pay_report does not state what share of quota postings "
                    "carry a figure, which is the caveat that governs how every "
                    "number in it should be read")
    if not r.get("coverage"):
        bad += fail("pay_report does not say what share of the sample each cut "
                    "can speak for - office_state clears the floor on two "
                    "states and a reader takes that for a national picture")
    # DOCSTRINGS AND COMMENTS OFF FIRST. The module's own prose explains
    # why it does NOT multiply by 2,080, and the first version of this
    # check read that sentence and reported the crime it describes.
    # Fifth time today a source scan in this file has tripped on its own
    # explanation.
    src = inspect.getsource(pr)
    code = re.sub(re.escape(TRIPLE_D) + r"(?:.|\n)*?" + re.escape(TRIPLE_D),
                  "", src)
    code = re.sub(re.escape(TRIPLE_S) + r"(?:.|\n)*?" + re.escape(TRIPLE_S),
                  "", code)
    code = re.sub(r"#.*$", "", code, flags=re.M)
    if re.search(r"2080|2,080|\*\s*52\b", code):
        bad += fail("pay_report converts an hourly rate into a year. The board "
                    "stores periods rather than converting them on purpose: "
                    "2,080 x an hourly rate invents a full-time year nobody "
                    "stated")
    if 'c.get("period") != "year"' not in code:
        bad += fail("pay_report no longer restricts to annual figures")
    if 'c.get("currency") != "USD"' not in code:
        bad += fail("pay_report no longer pins the currency; two currencies in "
                    "one median is not a number")
    return bad


def _import_pay_report():
    import pay_report
    return pay_report


def check_active_badge_is_shipped_honestly() -> int:
    """The badge on the page must be the momentum rules, not a looser copy.

    A badge saying "hiring hard" is a claim about an employer. It is derived
    from our own daily snapshots rather than from anybody's clicks, and
    momentum.py refuses far more than it reports - comparable baseline, same
    classifier on both sides, and a company with nothing at the baseline
    excluded because "they had none" and "we could not see them" are
    indistinguishable from a snapshot.

    The risk in shipping it is that the page grows its own looser version. So:
    the list on the board must be exactly what momentum returns, an empty list
    must render no badge, and the tooltip must say what the number is - a badge
    whose basis nobody can see is a claim nobody can check.
    """
    bad = 0
    # THE SHIPPED COPY, not data/board.json. build_site writes `active` onto
    # the sanitized board on its way out, so data/board.json never has it - and
    # the first version of this check read that file, found nothing, and
    # returned 0. A check pointed at the wrong artifact tests nothing, which is
    # the tenth time today that shape has turned up and the first time in a
    # check I wrote while fixing the other nine.
    shipped = ROOT / "public" / "data" / "board.json"
    if not shipped.exists():
        return 0                      # nothing built yet
    board = json.loads(shipped.read_text())
    active = board.get("active")
    if active is None:
        bad += fail("the shipped board carries no `active` list at all, so "
                    "build_site is not computing it and the badge can never "
                    "appear")
        return bad
    mom = _import_momentum()
    want = {c["id"] for c in (mom.surge().get("companies") or [])}
    got = {a["id"] for a in active}
    if got != want:
        bad += fail(f"the shipped active list {sorted(got)} is not what "
                    f"momentum returns {sorted(want)} - the page has its own "
                    f"rules for who counts as hiring hard")
    for a in active:
        if not (isinstance(a.get("was"), int) and isinstance(a.get("now"), int)):
            bad += fail(f"active entry {a.get('id')} carries no before/after "
                        f"counts, so the badge cannot say what it is claiming")
        elif a["now"] <= a["was"]:
            bad += fail(f"{a.get('id')} is marked active with {a['was']} -> "
                        f"{a['now']}, which is not an increase")
    src = (ROOT / "index.html").read_text()
    if "hotChip(" not in src:
        bad += fail("index.html no longer renders the active badge")
    blk = src[src.index("function hotChip("):]
    blk = blk[:blk.index("\nfunction ")]
    if "if(!a) return" not in re.sub(r"\s+", "", blk).replace("if(!a)return", "if(!a) return"):
        bad += fail("hotChip does not return empty for a company that is not "
                    "in the list - a badge on everything means nothing")
    if "title=" not in blk:
        bad += fail("the active badge carries no tooltip saying what the "
                    "number is, so the claim cannot be checked by the reader")
    return bad


def check_boards_read_agrees_with_coverage() -> int:
    """The card must not contradict the table three inches below it.

    "boards read this run" printed len(companies) - every company on file,
    2,113 - directly above the coverage table that says 950 of them are
    blocked, absent or never probed, in that table's own words: "We learned
    nothing about these" and "never probed".

    A company whose board we could not open was being counted as a board we
    read. That is the "839 of 1,722 monitored" overclaim coverage.py exists to
    kill, reappearing one card above the split that kills it.

    Checked as arithmetic against the shipped board, because the failure is a
    number that looks reasonable on its own and is only wrong beside another
    one.
    """
    bad = 0
    board = json.loads((DATA / "board.json").read_text())
    cov = board.get("coverage") or {}
    read = board.get("companies_read")
    if not cov or read is None:
        return 0                       # pre-dates the split; next build fixes it
    orgs = len(board.get("organizations") or [])
    if read == orgs:
        bad += fail(f"companies_read is {read:,}, which is every organization "
                    f"on the board. The coverage split says "
                    f"{cov.get('blocked',0)+cov.get('absent',0)+cov.get('unchecked',0):,} "
                    f"of them are blocked, absent or never probed - the card "
                    f"and the table beneath it cannot both be true")
    want = cov.get("structured", 0) + cov.get("page only", 0)
    if read != want:
        bad += fail(f"companies_read is {read:,} but the coverage split's "
                    f"structured + page only is {want:,}. They are two ways of "
                    f"stating one fact and they have drifted, which is exactly "
                    f"what deriving one from the other was meant to prevent")
    src = (ROOT / "scripts" / "build_board.py").read_text()
    if 'payload["companies_read"]' not in src:
        bad += fail("build_board no longer derives companies_read from the "
                    "coverage split, so the card can drift from the table again")
    return bad


def check_active_badge_measures_them_not_us() -> int:
    """"Active" must be a fact about the employer, not about our crawler.

    A click counter measures OUR traffic. This measures THEIR hiring, off the
    daily snapshots, and no visitor is tracked to produce it. Which makes the
    failure mode subtle, and it is the one this check is for.

    Granicus reads 0 to 53 quota roles over the last week and Adobe 0 to 9.
    Neither started hiring last week: we started READING them. Publishing that
    as their momentum prints our own crawler's history as somebody else's news,
    which is the same error as reporting a page we could not read as a company
    with no jobs, pointed the other way. From a snapshot alone "they had none"
    and "we could not see them" are indistinguishable, so a company with no
    postings at the baseline is excluded outright.

    Exercised against synthetic snapshots, because the live data currently
    qualifies NOBODY - a rule that never fires is indistinguishable from a
    broken one, and the honest empty output is itself worth pinning.
    """
    import shutil as _sh
    import tempfile as _tf
    mom = _import_momentum()
    bad = 0
    with _tf.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _sh.copytree(ROOT / "data", root / "data")
        real_data = mom.DATA
        mom.DATA = root / "data"
        try:
            picked = mom.surge().get("since")
            if not picked:
                return 0                       # no comparable history to test
            basef = root / "data" / "history" / f"{picked}.json"
            nowf = sorted((root / "data" / "history").glob("*.json"))[-1]
            base = json.loads(basef.read_text())
            now = json.loads(nowf.read_text())
            board = json.loads((root / "data" / "board.json").read_text())

            def mk(co, n, t):
                # A TITLE THE CLASSIFIER READS AS QUOTA-CARRYING. "Role a1" is
                # not one, and momentum now asks roles.is_quota_carrying() of
                # both snapshots - a fixture of unclassifiable titles would
                # count zero on both sides and pass whatever the code did.
                return [f"{co}::Account Executive {t}{i}::h{i}" for i in range(n)]

            # EVERY FIXTURE ID HAD A QUOTA-CARRYING TITLE, which is why this
            # check could not see the bug it was written beside. `was` counted
            # ALL postings and `now` counted quota-carrying ones; with every
            # injected title a seller title, both sides landed on the same
            # basis and the mismatch was invisible. On the real board it made
            # `added` negative for 125 companies and the function returned
            # nobody - an empty output that got reported as honest.
            #
            # So the fixture now carries NON-seller titles too. If the two
            # sides are ever counted differently again, `noise` moves one side
            # and not the other.
            base["ids"] += mk("smallco", 2, "a")
            now["ids"] += mk("smallco", 5, "a")        # real surge
            base["ids"] += [f"smallco::Staff Software Engineer {i}::n{i}"
                            for i in range(9)]
            now["ids"] += [f"smallco::Staff Software Engineer {i}::n{i}"
                           for i in range(9)]          # unchanged, non-seller
            base["ids"] += mk("bigco", 200, "b")
            now["ids"] += mk("bigco", 203, "b")        # +3 on 200
            now["ids"] += mk("newlyread", 40, "c")     # we started reading them
            base["ids"] += mk("oneup", 4, "d")
            now["ids"] += mk("oneup", 5, "d")          # +1 on 4: fails growth
            # ISOLATES THE COUNT FLOOR. 1 -> 2 is 100% growth, so MIN_GROWTH
            # waves it through and only MIN_ADDED stops it. Without this case
            # the count floor could be deleted and nothing would notice: the
            # `oneup` company above is 4 -> 5, which the growth rule already
            # rejects, so it was masking the very rule it looked like it tested.
            base["ids"] += mk("tinyco", 1, "e")
            now["ids"] += mk("tinyco", 2, "e")
            basef.write_text(json.dumps(base))
            nowf.write_text(json.dumps(now))
            board["postings"] += [
                {"id": i, "quota_carrying": True}
                for i in mk("smallco", 5, "a") + mk("bigco", 203, "b")
                + mk("newlyread", 40, "c") + mk("oneup", 5, "d")
                + mk("tinyco", 2, "e")]
            board["organizations"] += [
                {"id": k, "name": k}
                for k in ("smallco", "bigco", "newlyread", "oneup", "tinyco")]
            (root / "data" / "board.json").write_text(json.dumps(board))

            res = mom.surge()
            got = {c["id"] for c in res.get("companies", [])}
            row = next((c for c in res.get("companies", []) if c["id"] == "smallco"), None)
            if row and (row["was"] != 2 or row["now"] != 5):
                bad += fail(f"momentum counted smallco {row['was']} -> "
                            f"{row['now']}, not 2 -> 5. Nine non-seller titles "
                            f"were added to BOTH snapshots and changed the "
                            f"answer, so the two sides are being counted on "
                            f"different bases - which on the live board made "
                            f"`added` negative for 125 companies")
            if "smallco" not in got:
                bad += fail("momentum does not report a real surge (2 sellers "
                            "to 5) - a badge that never fires is the same as "
                            "no badge, and nobody would notice")
            if "newlyread" in got:
                bad += fail("momentum called a company active on the strength "
                            "of going 0 to 40. A company we could not read "
                            "before is not a company that was not hiring; that "
                            "publishes our crawler's history as their news")
            if "bigco" in got:
                bad += fail("momentum called +3 roles on a base of 200 a "
                            "surge - the floor is on the CHANGE relative to "
                            "the company, or every large employer is permanently"
                            " 'active'")
            for one in ("oneup", "tinyco"):
                if one in got:
                    bad += fail(f"momentum called a single added role a surge "
                                f"({one}); every company posts one sometimes")
        finally:
            mom.DATA = real_data
    return bad


def _import_momentum():
    import momentum
    return momentum


def check_sitemap_offers_the_job_pages() -> int:
    """The sitemap must list the role pages, and gone roles must say noindex.

    THESE TWO ARE ONE CHANGE AND HAVE TO STAY TOGETHER.

    functions/_middleware.js emits a JobPosting block on every ?role= whose
    description was actually read - 3,524 of 4,439 today - and Google for Jobs
    is the one channel that sends high-intent traffic to a board this size. The
    sitemap listed 468 addresses and NOT ONE job page. The markup was correct,
    live, and undiscoverable: the only route in was to crawl a company page and
    follow a link out of a single-page app.

    Adding them creates the second problem immediately. Postings leave this
    board by the hundred - 141 in one run last week - and a gone role answers
    200 with the app's generic title, which is a soft 404. Pointing a crawler
    at 4,439 of those without a noindex would earn the domain a penalty within
    days of the first crawl.

    So: roles are in the sitemap, AND the middleware marks a role it cannot
    find as noindex. Removing either one alone is worse than having neither.
    """
    bad = 0
    src = (ROOT / "scripts" / "build_site.py").read_text()
    body = src[src.index("def write_crawl_files("):]
    body = body[:body.index("\ndef ")]
    code = re.sub(r"#.*$", "", body, flags=re.M)
    if "?role=" not in code:
        bad += fail("the sitemap no longer lists role pages - the JobPosting "
                    "markup on 3,524 postings is live and nothing points a "
                    "crawler at it")
    sm = ROOT / "public" / "sitemap.xml"
    if sm.exists():
        urls = re.findall(r"<loc>(.*?)</loc>", sm.read_text())
        roles = sum(1 for u in urls if "?role=" in u)
        if not roles:
            bad += fail("public/sitemap.xml carries no role urls")
        if len(urls) > 50000:
            bad += fail(f"the sitemap has {len(urls):,} urls, over the 50,000 "
                        f"limit - it must be split into an index")

        # NO TWO SUBMITTED URLS MAY RENDER THE SAME PAGE. 400 role urls once
        # rendered identically - same title, company and place - because one
        # opening was read twice at a location, or had no location at all. 62
        # of those carried a structured block, and Google's guidance is
        # explicit about not submitting the same job twice. This is the same
        # defect the jobs list fixed by collapsing to one row per opening,
        # reintroduced by the sitemap in a different shape.
        #
        # NOT one url per opening: Xplor's 82 cities are 82 real pages with 82
        # real locations, and collapsing them would hide places somebody might
        # search for. Keyed on what the page shows.
        meta_r = ROOT / "public" / "meta-roles.json"
        if meta_r.exists():
            import collections as _c
            import urllib.parse as _up
            rr = json.loads(meta_r.read_text())
            rr = rr.get("roles", rr)
            sig = _c.Counter()
            for u in urls:
                if "?role=" not in u:
                    continue
                v = rr.get(_up.unquote(u.split("?role=")[1])) or {}
                if not v.get("ld"):
                    continue
                sig[(v.get("t"), v.get("c"), v.get("w"),
                     v.get("ci"), v.get("st"))] += 1
            # ONE URL FORM, AND IT MUST BE THE ONE THE SERVER SERVES.
            # Cloudflare 308s /c/verkada.html to /c/verkada, and the target
            # then declared the .html form as its canonical - a canonical
            # pointing at a redirect back to the page declaring it, on all 462
            # company, state and conference urls.
            if any(u.endswith(".html") for u in urls):
                bad += fail("the sitemap submits .html urls, which Cloudflare "
                            "308s to the extensionless form - every one tells "
                            "a crawler the address it was sent to is not the "
                            "real one")
            # AND THE SAME ESCAPING AS THE CANONICAL. urllib.parse.quote
            # escapes ! ~ * ' ( ) and encodeURIComponent does not; 539 posting
            # ids contain one, so the submitted url and the canonical the page
            # declares were different strings for 12% of the role urls.
            over = [u for u in urls
                    if any(e in u for e in ("%28", "%29", "%27", "%21", "%2A", "%7E"))]
            if over:
                bad += fail(f"{len(over):,} sitemap urls are escaped more "
                            f"tightly than encodeURIComponent, so they do not "
                            f"match the canonical the page declares")
            dupes = sum(n for n in sig.values() if n > 1)
            if dupes:
                bad += fail(f"{dupes:,} sitemap urls carry a JobPosting block "
                            f"identical to another submitted url - the same job "
                            f"offered to Google more than once")

    mw = (ROOT / "functions" / "_middleware.js").read_text()
    mwc = re.sub(r"//.*$", "", mw, flags=re.M)
    mwc = re.sub(r"/\*.*?\*/", "", mwc, flags=re.S)
    # THE TAG AND THE FLAG THAT GATES IT, not the word. The first version
    # looked for "noindex" anywhere in the file, and a mutation setting
    # `noindex: false` left it passing - the word was still there in the very
    # line that had been disabled. Fourth time today a check has been written
    # against a string when it needed to be written against a behaviour.
    flat = re.sub(r"\s+", "", mwc)
    if 'content="noindex' not in flat:
        bad += fail("the middleware emits no robots noindex tag. With 4,439 "
                    "role urls in the sitemap, every posting that comes off "
                    "the board becomes a soft 404 a crawler was sent to on "
                    "purpose")
    if "m.noindex?" not in flat:
        bad += fail("the noindex tag is no longer gated on m.noindex, so it is "
                    "either never emitted or emitted on every page - one of "
                    "which un-indexes the whole board")
    if "noindex:true" not in flat:
        bad += fail("no branch sets noindex:true, so an unknown ?role= is "
                    "served as an ordinary page and indexed as a soft 404")
    return bad


def check_alerts_page_cannot_be_framed() -> int:
    """The one page here with a token and a delete button must refuse framing.

    /alerts holds a subscription token in memory and carries a one-click
    "Delete this subscription and everything stored with it" behind it. A
    framed copy is the textbook clickjacking target: the victim is already
    authenticated by the link in their own email, and one disguised click
    destroys their subscription.

    This lived in vercel.json for months and applied on nothing. Cloudflare
    Pages does not read that file, so the three headers it declared were
    decoration - verified against the live site, where two of them were sent
    anyway because Cloudflare sets them itself, and frame protection was not
    sent at all. The file is gone and build_site.py writes public/_headers,
    which Cloudflare does read.

    SCOPED, NOT SITE-WIDE, and the check enforces that too. The board is
    public, read-only, sessionless; somebody embedding a job list is a use
    rather than an attack, and a blanket deny would be cargo cult. More to the
    point, a Content-Security-Policy over the board would have to permit the
    inline script and style the single-file app is built from - a policy that
    allows exactly what it exists to stop.
    """
    bad = 0
    src = (ROOT / "scripts" / "build_site.py").read_text()
    if "def write_headers(" not in src:
        return fail("build_site.py no longer writes public/_headers - the "
                    "alerts page can be framed, and it has a one-click delete "
                    "behind a token")
    body = src[src.index("def write_headers("):]
    body = body[:body.index("\ndef ")]
    code = re.sub(r'""".*?"""', "", body, flags=re.S)
    for want in ("X-Frame-Options: DENY", "frame-ancestors"):
        if want not in code:
            bad += fail(f"public/_headers no longer sets {want!r} for /alerts")
    if "/alerts" not in code:
        bad += fail("the _headers block is not scoped to /alerts any more")
    # and it must NOT have grown into a site-wide policy that would break the
    # single-file app
    if re.search(r"^\s*\"/\\n", code, re.M) or '"/*' in code:
        bad += fail("public/_headers now applies to the whole site - a CSP over "
                    "the board would have to permit the inline script and style "
                    "it is built from, which permits what it is meant to stop")
    # AND THAT IT IS CALLED. The check read write_headers' source and never
    # that main() invokes it. Commenting out the call left the suite green -
    # and because public/ is gitignored and Cloudflare rebuilds it from
    # build_site.py on every push, the deploy would ship NO _headers file at
    # all. /alerts becomes framable, with its token in memory and its one-click
    # "delete this subscription and everything stored with it" behind it.
    if not re.search(r"^\s*write_headers\(", src, re.M):
        bad += fail("build_site.main never calls write_headers, so no _headers "
                    "file is written and the deploy ships none. /alerts becomes "
                    "framable - the clickjacking case this exists for")
    hdr = ROOT / "public" / "_headers"
    if hdr.exists():
        h = hdr.read_text()
        if "/alerts" not in h or "X-Frame-Options" not in h:
            bad += fail("public/_headers does not protect /alerts")
    if (ROOT / "vercel.json").exists():
        bad += fail("vercel.json is back. Cloudflare Pages does not read it, so "
                    "any header in it applies on no path that exists - it is a "
                    "second deploy door that does not open")
    return bad


def check_watchdog_is_independent() -> int:
    """The watchdog must not depend on the thing it watches.

    A failed refresh sends GitHub's own email. A refresh that SUCCEEDS while
    doing nothing sends nothing at all - and neither does a cron that quietly
    stopped firing, a step whose continue-on-error swallowed the reason, or an
    expired token. All of those leave the site serving yesterday's board with
    today's confidence, which is a stale claim presented as a current one.

    So the properties that make this worth having, each of which is one edit
    from being lost:

    ITS OWN WORKFLOW AND ITS OWN CRON. Folded into refresh.yml it would go
    quiet at exactly the moment it is needed, because the thing that stopped is
    the thing that would have run it.

    STDLIB ONLY. A watchdog that can fail because pip did not install is a
    watchdog that reports the machinery as broken when only the watchdog is.

    AND IT MUST STILL BE ABLE TO SPEAK. `gh issue create --label` fails outright
    when the label does not exist, which would mean silence on the one night
    there was something to say.
    """
    bad = 0
    wf = ROOT / ".github/workflows/watchdog.yml"
    if not wf.exists():
        return fail("no .github/workflows/watchdog.yml - nothing notices when "
                    "the nightly run stops succeeding quietly")
    # COMMENTS OFF FIRST. This file has tripped on its own prose five times
    # now - the last was this very check, failing on the line that explains
    # why there is no pip install in it.
    y = re.sub(r"^\s*#.*$", "", wf.read_text(), flags=re.M)
    if "cron:" not in y:
        bad += fail("the watchdog has no schedule of its own, so it only runs "
                    "when somebody remembers to run it")
    if "pip install" in y:
        bad += fail("the watchdog installs a dependency - it must be stdlib "
                    "only, or it can fail for a reason that has nothing to do "
                    "with the board")
    if "gh label create" not in y:
        bad += fail("the watchdog does not ensure its label exists; "
                    "`gh issue create --label` fails when it does not, and the "
                    "watchdog goes silent on the night it matters")
    if "issue comment" not in y or "issue close" not in y:
        bad += fail("the watchdog does not keep ONE rolling issue - a fresh "
                    "ticket every night for the same stuck cron is how "
                    "somebody learns to close them unread")
    # the refresh workflow must NOT be where this lives
    r = (ROOT / ".github/workflows/refresh.yml").read_text()
    if "watchdog.py" in r:
        bad += fail("refresh.yml runs the watchdog - a check inside the "
                    "pipeline it watches cannot report that the pipeline did "
                    "not run")
    src = (ROOT / "scripts" / "watchdog.py").read_text()
    for mod in ("requests", "openpyxl", "playwright"):
        if re.search(rf"^import {mod}\b|^from {mod}\b", src, re.M):
            bad += fail(f"watchdog.py imports {mod} - stdlib only, see above")
    return bad


def check_queue_history_is_append_only() -> int:
    """A queue count nobody wrote down is gone, and history cannot be backfilled.

    CLAUDE.md picked personal bests - the user against their own last 30 days -
    as one of the three mechanics the admin grows into, and rejected every
    leaderboard. A personal best needs a history to be personal about, and a
    count that was true in August and never recorded cannot be recovered in
    October. That is why this had to start before the ruling did.

    Two properties, both easy to lose to a well-meaning tidy-up:

    A QUEUE THAT RAISES IS RECORDED AS RAISING, never as zero. A zero would
    read as "somebody cleared it", which is the flattering version of exactly
    the failure this project refuses - an absence of information published as
    an absence of work.

    AND PAST LINES ARE NEVER REWRITTEN. Same rule as data/hiring_history: today
    may be replaced (a later count on the same day is the truer one), yesterday
    may not.
    """
    bad = 0
    qs = _import_queue_stats()
    src = inspect.getsource(qs)
    if "out[key] = None" not in src:
        bad += fail("queue_stats no longer records a raising queue as None - "
                    "if it writes 0 instead, a broken queue reads as a cleared "
                    "one forever after")
    # the writer must keep every line whose date is not today
    w = inspect.getsource(qs.record)
    if 'r.get("on") != today' not in w:
        bad += fail("queue_stats.record no longer preserves lines from other "
                    "days - this file is an audit trail and a rewritten past "
                    "is not one")
    if "queue_stats.py" not in (ROOT / ".github/workflows/refresh.yml").read_text():
        bad += fail("the nightly workflow does not run queue_stats.py, so the "
                    "history it exists to keep will have holes in it")
    return bad


def _import_queue_stats():
    import queue_stats
    return queue_stats


def check_capture_flags_nav_without_dropping_sellers() -> int:
    """A pasted capture must name page furniture and must never drop a seller.

    Captures used to arrive only from the bookmarklet, whose href regex filters
    nav chrome before a person sees it. The paste form takes arbitrary JSON by
    design, so the server is the only filter left - and roles.is_junk, which was
    supposed to be it, passes Cookie Preferences, CHALLENGES, SOLUTIONS,
    Privacy Policy and View all jobs. That is the exact list the harvester's
    first rule was written about.

    BOTH HALVES ARE THE TEST. Flagging is easy; the trap is fixing it with
    ats._TITLEISH, which is a word allowlist and rejects Head of Sales,
    Enterprise Sales, Territory Sales, Business Development, VP Marketing and
    SDR. Those are not edge cases on this board, they are the roles it exists
    to find, and dropping them to tidy away some nav is the asymmetric error in
    a clean shirt.

    So: nav is NAMED and everything is KEPT.
    """
    bad = 0
    admin = _admin()
    if not hasattr(admin, "_reads_like_nav"):
        return fail("admin has no _reads_like_nav - a pasted capture can put "
                    "'Cookie Preferences' on the public board as a job")
    nav = ["Cookie Preferences", "SOLUTIONS", "CHALLENGES", "Privacy Policy",
           "View all jobs", "Contact Us", "Our Team"]
    sellers = ["Head of Sales", "Enterprise Sales", "Territory Sales",
               "Business Development", "VP Marketing", "SDR", "BDR",
               "Account Executive", "Regional Sales Manager",
               "Customer Success Manager", "Director of Partnerships",
               "Chief Revenue Officer", "Solutions Engineer"]
    for t in nav:
        if not admin._reads_like_nav(t):
            bad += fail(f"a captured row titled {t!r} is not flagged as page "
                        f"furniture - it reaches the public board as a job")
    for t in sellers:
        if admin._reads_like_nav(t):
            bad += fail(f"{t!r} is flagged as page furniture, and it is a real "
                        f"seller title - the flag must never be used to drop, "
                        f"and it must not cry wolf on the roles this board is "
                        f"for")
    # and the flag must not have become a filter
    # THE WHOLE BLOCK, NOT THE NEXT LINE. This read
    # `src.split("_reads_like_nav")[1].split("\n")[1:3][0]` - the single line
    # after the `if`. Putting the drop one line lower:
    #     if _reads_like_nav(title):
    #         suspect.append(title)
    #         continue          <- invisible
    # left the suite green, and the check's whole stated purpose is that nav is
    # NAMED and everything is KEPT.
    src = inspect.getsource(admin.act_capture)
    after = src.split("_reads_like_nav")[1]
    # everything up to the next line at the same indent as the `if`, i.e. the
    # body of that branch
    branch = []
    for line in after.splitlines()[1:]:
        if line.strip() and not line.startswith(" " * 12):
            break
        branch.append(line)
    if any(w in "\n".join(branch) for w in ("continue", "return")):
        bad += fail("act_capture now SKIPS a row that reads like nav. It must "
                    "flag and keep: ats._TITLEISH rejects Head of Sales and "
                    "SDR, so a filter here deletes warm doors to tidy away "
                    "some chrome")
    return bad


def check_worklist_leads_with_evidence() -> int:
    """The capture worklist must offer the companies we know something about.

    It sorted on (tier, never-checked, NAME), which inside tier 1 is the
    alphabet wearing a ranking's clothes - the first three it ever offered were
    "'with' Community Calendar", ACI Worldwide and ADP.

    Worse, needs_check EXCLUDED the best targets. 116 companies have a careers
    page a scan read a quota-carrying title off and could not enumerate; their
    status is "Yes", so they failed both the `unreadable` and `no_board` tests
    and were filtered out as already covered. They are not covered: the public
    card carries a SYNTHETIC row titled "AE-type role (page scan)" with no
    location and no link. It is a marker meaning somebody should look, and the
    worklist was the one thing that would have sent them.

    So: a company whose every role is synthetic is unread whatever its status
    says, and it sorts ahead of a company we have no evidence about at all.
    """
    bad = 0
    m = _import_manual()
    checks: dict = {}
    scan_only = {"id": "x", "ats": {"type": "html", "ref": "https://x/careers"},
                 "hiring": {"status": "Yes", "roles": [
                     {"title": "AE-type role (page scan)", "synthetic": True}]}}
    due, why = m.needs_check(scan_only, checks)
    if not due:
        bad += fail("manual.needs_check skips a company whose only role is a "
                    "synthetic page-scan marker - those are the 116 we have "
                    "the best evidence about and the worklist would never "
                    "send anybody to them")
    elif "scan" not in why:
        bad += fail(f"needs_check returns {why!r} for a scan-only company - "
                    f"the reason is what tells a person this one is worth "
                    f"opening first")
    # a company with a REAL posting is covered weekly and must stay out
    real = {"id": "y", "ats": {"type": "greenhouse", "ref": "https://x"},
            "hiring": {"status": "Yes", "roles": [
                {"title": "Account Executive", "url": "https://x/1"}]}}
    if m.needs_check(real, checks)[0]:
        bad += fail("manual.needs_check now claims a company with a real "
                    "fetched posting needs a manual check - that spends a "
                    "person's evening on a board refresh.py already reads")
    src = (ROOT / "scripts" / "manual.py").read_text()
    body = src[src.index("def cmd_worklist("):]
    if "synthetic" not in body:
        bad += fail("cmd_worklist no longer ranks on the page-scan signal, so "
                    "it is back to sorting tier-1 companies alphabetically")
    if "Careers:" not in body:
        bad += fail("cmd_worklist no longer prints the careers URL - that is "
                    "the page a person opens, and without it they hunt for the "
                    "careers link before they can start")
    return bad


def _import_manual():
    import manual
    return manual


def check_every_title_extractor_strips_buttons() -> int:
    """Both extractors, not one. The CTA rule was added to one and shipped.

    THIS CHECK EXISTS BECAUSE THE OBVIOUS ONE WAS NOT ENOUGH. CTA_CASES tests
    ats.strip_cta as a function, it passed, its mutations fired - and the very
    next rebuild published all nine of Adobe's "Apply Now Account Manager"
    titles unchanged. strip_cta had been wired into fetch_html_titles alone,
    and Adobe's board is JavaScript, so it comes through render_fetch.py's
    separate extractor, which had never heard of the rule.

    A function test proves the rule is CORRECT. It cannot prove the rule is
    REACHED. render_fetch.py already carries a comment saying "a rule that only
    one of two extractors knows is a rule with a hole in it" - written there
    after the same mistake with the job-count rule - and the hole was reopened
    one file over anyway.

    So this asserts REACHABILITY, at source, for every path that produces a job
    title. If a third extractor appears it has to be added here, which is the
    point: the list of extractors is the thing that must not drift.
    """
    bad = 0
    extractors = [
        ("scripts/ats.py", "fetch_html_titles",
         "the requests path, for server-rendered careers pages"),
        ("scripts/render_fetch.py", "fetch_rendered",
         "the headless-browser path, for JavaScript boards - this is the one "
         "Adobe comes through"),
    ]
    for path, fn, what in extractors:
        src = (ROOT / path).read_text()
        if f"def {fn}(" not in src:
            bad += fail(f"{path} has no {fn}() - this check names the title "
                        f"extractors and one of them moved; find it and fix "
                        f"the list, do not delete the entry")
            continue
        body = src[src.index(f"def {fn}("):]
        nxt = body.find("\ndef ", 1)
        body = body[:nxt] if nxt > 0 else body
        # comments strip first: this file has tripped on its own prose four
        # times, and the paragraph above mentions strip_cta repeatedly
        code = re.sub(r"#.*$", "", body, flags=re.M)
        code = re.sub(r'""".*?"""', "", code, flags=re.S)
        # THE RESULT MUST BE USED, not merely computed. `_cta = strip_cta(raw)`
        # calls it and throws the answer away; so does moving the call after
        # split_location, which is a natural refactor and re-breaks the tail
        # rule the comment above it warns about. Both left the suite green.
        if not re.search(r"\b(text|raw|title)\s*=\s*strip_cta\(", code):
            bad += fail(f"{path}::{fn} calls strip_cta and discards the "
                        f"result - the button label is computed and then the "
                        f"original string is used anyway")
        if "strip_cta(" not in code:
            bad += fail(f"{path}::{fn} never calls strip_cta - {what}. A job "
                        f"title beginning 'Apply Now' reaches the public board "
                        f"from here, and it is also the posting id, the alert "
                        f"match and the scope-ruling key")
    return bad


def check_queue_rows_carry_what_the_page_renders() -> int:
    """EVERY queue, not the one whose breakage somebody happened to notice.

    admin.html's RENDER.<queue> reads fields off a row; admin.py's q_<queue>
    builds it. Nothing connects the two, and when they disagree the row draws
    with a blank exactly where its evidence should be - which looks identical
    to a row whose evidence is weak, so it survives indefinitely.

    Two were found the day this check was written, and finding the second is
    why it sweeps all of them:

      acquisitions - 82 rows drew with a SLUG for a heading, the fixed band
        "Only the slug looks odd - weakest, most of these are nothing", and an
        EMPTY evidence line, including the 22 whose note names the parent's
        domain outright. data/acquisition_rulings.json has never been written
        once, and that is the whole explanation.
      submissions - the card printed "sent undefined", never said who sent it,
        and swallowed `context`, which on the one pending row is the paragraph
        explaining why it is a scope call rather than a regex's.

    UNGUARDED READS ONLY. A field the page reads behind `if (s.x)` or
    `s.x || ...` is optional by construction and degrades correctly - six of
    the acquisitions fields are exactly that and are not defects. A BARE read
    is a promise the row is expected to keep, and this fails when it does not.
    """
    bad = 0
    page = (ROOT / "admin.html").read_text()
    admin = _admin()
    companies = json.loads((ROOT / "data" / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = companies.get("companies", [])
    board = json.loads((ROOT / "data" / "board.json").read_text())

    for m in re.finditer(r"RENDER\.(\w+)\s*=\s*s\s*=>\s*\{", page):
        name = m.group(1)
        fn = getattr(admin, "QUEUES", {}).get(name)
        if not fn:
            continue
        nxt = page.find("\nRENDER.", m.end())
        body = page[m.end(): nxt if nxt > 0 else m.end() + 6000]
        try:
            rows = fn(companies, board)
        except Exception as e:                    # noqa: BLE001
            bad += fail(f"q_{name} raised {type(e).__name__}: {e}")
            continue
        if not rows:
            continue

        # WHICH READS ARE ALLOWED TO MISS, and getting this wrong once made
        # the check unable to catch its own founding case.
        #
        # The first version treated any `s.x || …` as guarded. But the
        # acquisitions bug WAS `s.says || ''` - a fallback to the empty string,
        # which is exactly the blank line that started all this. A rule that
        # excuses it is a rule that would have shipped the bug.
        #
        # So the test is not "is there a fallback" but "is the fallback worth
        # anything":
        #   if (s.x)              - the element is omitted. Fine.
        #   s.x && …  /  || s.x   - x is an alternative, not the subject. Fine.
        #   s.x || s.y  /  || 'a real string'  - degrades to something. Fine.
        #   s.x || ''   /  s.x || ""           - renders BLANK. A defect.
        #   bare s.x                            - a defect, unless the line is a
        #     ternary keyed on a DIFFERENT field, which means the author already
        #     branched: `s.kind === 'company' ? (s.name || s.url) : s.title`
        #     legitimately has no title on a company submission.
        #
        # SPLIT ON STATEMENTS, NOT LINES. The submissions heading is
        #     el('h3', null, s.kind === 'company'
        #       ? (s.name || s.url) : s.title)
        # and line-by-line the condition and its branches land in different
        # chunks, so the ternary is invisible and `s.title` reads as bare. A
        # company submission legitimately has no title.
        guarded = set()
        for line in re.split(r";", body):
            fields = set(re.findall(r"\bs\.(\w+)", line))
            # a ternary whose condition names another field branches for us
            cond = re.match(r"[^?]*", line).group(0)
            branching = "?" in line and any(
                f in fields and re.search(rf"\bs\.{f}\b", cond) for f in fields)
            for f in fields:
                if re.search(rf"if\s*\(\s*s\.{f}\b", line) \
                        or re.search(rf"s\.{f}\s*&&", line) \
                        or re.search(rf"\|\|\s*s\.{f}\b", line) \
                        or branching:
                    guarded.add(f); continue
                m = re.search(rf"s\.{f}\s*\|\|\s*(\S+)", line)
                # a fallback that is an empty literal is not a fallback
                if m and m.group(1).rstrip(");,") not in ("''", '""', "``"):
                    guarded.add(f)
        reads = set(re.findall(r"\bs\.(\w+)", body)) - guarded
        for f in sorted(reads):
            if not any(f in r for r in rows):
                bad += fail(f"admin.html's {name} card reads s.{f} unguarded "
                            f"and not one of its {len(rows)} rows carries it - "
                            f"that draws a blank where the evidence goes, which "
                            f"is indistinguishable from weak evidence")
    return bad


def check_queue_strengths_have_a_band() -> int:
    """A strength the server emits and the page has no band for renders as
    `undefined` where the row's headline goes."""
    page = (ROOT / "admin.html").read_text()
    if "RENDER.acquisitions" not in page:
        return 0
    i = page.index("RENDER.acquisitions")
    body = page[i: page.find("\nRENDER.", i)]
    bands = set(re.findall(r"^\s*(\w+): '", body, re.M))
    admin = _admin()
    companies = json.loads((ROOT / "data" / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = companies.get("companies", [])
    board = json.loads((ROOT / "data" / "board.json").read_text())
    bad = 0
    for st in sorted({r.get("strength")
                      for r in admin.q_acquisitions(companies, board)
                      if r.get("strength")}):
        if st not in bands:
            bad += fail(f"the server emits strength {st!r} and admin.html has "
                        f"no band for it - the row's headline renders as "
                        f"undefined")
    return bad


def _admin():
    import admin
    return admin


def check_structured_matches_the_fetchers() -> int:
    """coverage.py's STRUCTURED is ats.FETCHERS minus `html`. Keep it so.

    STRUCTURED decides what counts as a real API, which is the numerator of the
    one number this project says is worth moving. It is hand-listed, and
    CLAUDE.md's conventions section says a new ATS type must be added in both
    places - a sentence that has never once stopped anybody from forgetting.

    The drift is silent in the worst direction: add a fetcher and leave
    STRUCTURED alone, and every company on that new ATS is counted as `page
    only` forever. The coverage report understates the thing it exists to
    measure, and nothing looks wrong - the number is simply smaller than the
    truth, which is exactly the shape "839 of 1,722" had.

    `html` is excluded on purpose and only `html`: it is a page scan, which can
    prove a role is there and never that one is not.
    """
    structured = _import_coverage().STRUCTURED
    want = set(ats.FETCHERS) - {"html"}
    missing = sorted(want - structured)
    extra = sorted(structured - want)
    bad = 0
    if missing:
        bad += fail(f"ats.py fetches {', '.join(missing)} but coverage.py's "
                    f"STRUCTURED does not list them - every company on those "
                    f"boards is counted as 'page only' and the one number "
                    f"worth moving reads lower than it is")
    if extra:
        bad += fail(f"coverage.py counts {', '.join(extra)} as a structured API "
                    f"and ats.py has no fetcher for it - those companies are "
                    f"counted as monitored and are not being read")
    return bad


def _import_coverage():
    import coverage
    return coverage


def check_ats_advice_covers_the_board() -> int:
    """Every ATS a company here actually uses needs its own "before you apply".

    The section used to print one sentence for every board in the `hard`
    bucket - a sentence true of eighteen different systems and therefore advice
    about none of them. It is per-ATS now, which introduces the failure this
    check exists for: a company moves onto an ATS with no entry, the section
    silently falls back to the old generic line, and nothing looks wrong.

    Same shape and same reason as check_alert_vocabulary. `html` and `unknown`
    are excluded on purpose - neither names a product, and falling back is the
    correct behaviour for both.

    It does NOT check the wording. Whether Workday asks you to retype your
    resume is a fact about Workday that no test here can verify; what a test
    can hold is that every board somebody might apply through is covered, and
    that the entries stay about the SOFTWARE rather than drifting into claims
    about how a company screens.
    """
    bad = 0
    src = (ROOT / "index.html").read_text()
    if "const ATSHOW=" not in src:
        return 0
    block = src[src.index("const ATSHOW="):]
    block = block[:block.index("\n};")]
    have = set(re.findall(r"^\s{2}(\w+):\{", block, re.M))
    companies = json.loads((ROOT / "data" / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = companies.get("companies", [])
    used: dict[str, int] = {}
    for c in companies:
        t = ((c.get("ats") or {}).get("type") or "").lower()
        if t and t not in ("html", "unknown"):
            used[t] = used.get(t, 0) + 1
    for t, n in sorted(used.items(), key=lambda kv: -kv[1]):
        if t not in have:
            bad += fail(f"{n} compan{'y' if n == 1 else 'ies'} post on {t!r} and "
                        f"index.html's ATSHOW has no entry for it - the role "
                        f"page falls back to the generic sentence and nobody "
                        f"can tell that it did")
    # and the entries must stay about the product. A line naming a company is
    # a claim about that employer's screening, which this board cannot make.
    # ...and NOT the vendor whose product the entry is about. Workday is on
    # this board as a company AND is the name of the ATS its entry describes;
    # so is Oracle. Naming the product is the entry's whole job. Every OTHER
    # company name in the block would be a claim about that employer.
    vendors = {v.lower() for v in have} | {"oracle recruiting"}
    names = {(c.get("name") or "") for c in companies if len(c.get("name") or "") > 6}
    for nm in sorted(names):
        if nm.lower() in vendors:
            continue
        if re.search(rf"\b{re.escape(nm)}\b", block):
            bad += fail(f"ATSHOW mentions {nm!r} - these entries describe how "
                        f"the software behaves, never how a named employer "
                        f"screens, and this board knows nothing about the "
                        f"second")
            break
    return bad


def check_jd_backfill_targets_real_pages() -> int:
    """read_descriptions must only ask boards that have a page to ask for.

    A type belongs in DETAIL_TYPES only if its postings have distinct urls.
    Rippling's do not: 65 postings across 13 urls, because every posting on a
    Rippling board carries the BOARD's address. Asking it 65 times downloads
    the same JS shell 65 times, finds no JobPosting block in any of them, and
    stamps 65 postings as read-and-empty - which the board then publishes as
    "we read this posting and it gave no figure", 65 times, about postings
    nobody ever opened. That is a false absence manufactured at scale, which is
    the one failure this project is built to refuse.

    Re-derived FROM THE BOARD rather than trusting the constant, so a company
    moving onto a shared-url ATS is caught before its postings are stamped.
    """
    bad = 0
    import read_descriptions as rd
    board = json.loads((DATA / "board.json").read_text())
    companies = json.loads((DATA / "companies.json").read_text())
    if isinstance(companies, dict):
        companies = companies.get("companies", [])
    by_id = {c.get("id"): c for c in companies}
    counts: dict[str, list] = {}
    for p in board.get("postings", []):
        if not p.get("url"):
            continue
        t = (((by_id.get(p.get("company_id")) or {}).get("ats") or {})
             .get("type") or "").lower()
        if t not in rd.DETAIL_TYPES:
            continue
        counts.setdefault(t, [0, set()])
        counts[t][0] += 1
        counts[t][1].add(p["url"])
    for t, (n, urls) in sorted(counts.items()):
        # A handful of genuine duplicate urls is normal - the same requisition
        # cross-posted. One url standing in for many postings is not.
        if n >= 10 and len(urls) < n * 0.8:
            bad += fail(f"read_descriptions would fetch {n} {t} postings across "
                        f"only {len(urls)} distinct urls - those postings share "
                        f"a board address, so reading it would stamp them all "
                        f"as read-and-empty and publish a pay silence nobody "
                        f"checked. Drop {t!r} from DETAIL_TYPES.")
    return bad


def check_public_csv_neutralises_formulas() -> int:
    """The public export must not hand somebody a spreadsheet that runs code.

    A leading =, +, - or @ makes Excel and Sheets treat a cell as a FORMULA,
    and the company names in this file arrive from outside submissions. admin.py
    has carried this guard since its queues got a CSV; the public export needs
    it for the same reason and it is one careless edit from being lost - a CSV
    with the apostrophe missing looks completely correct in every other way.

    Also checked: the export writes what REACHED US rather than a status, so a
    company whose board we could not open never leaves here as a bare 0 in an
    "open roles" column. That is the asymmetric error rule in a file that
    outlives every caveat printed beside it.
    """
    bad = 0
    src = (ROOT / "index.html").read_text()
    if "function csvCell(" not in src:
        return 0
    body = src[src.index("function csvCell("):]
    body = body[:body.index("\n/* WHAT GOES IN IT")]
    # AND THAT IT IS APPLIED TO EVERY CELL. The first version asserted the
    # guard's characters were in csvCell's source, which two mutations walked
    # straight past: replacing `.map(csvCell)` with `.map(JSON.stringify)` left
    # the guard intact and never applied to a single value, and wrapping the
    # test in `false && ...` left both matched strings verbatim. A guard that
    # is present and unreached is not a guard.
    exp0 = src[src.index("function exportCompanies("):]
    exp0 = exp0[:exp0.index("\nfunction ")]
    flatx = re.sub(r"\s+", "", exp0)
    # The DATA rows are the security-critical half: company names arrive from
    # outside submissions, headers are ours. So this pins the row expression
    # specifically rather than counting occurrences, which the header's
    # `csvCell(c[1])` form would have satisfied on its own.
    if "line(o).map(csvCell)" not in flatx:
        bad += fail("exportCompanies no longer puts every data cell through "
                    "csvCell - a company name from an outside submission would "
                    "reach the file unneutralised and run as a formula when "
                    "somebody opens it in Excel")
    if "csvCell(c[1])" not in flatx:
        bad += fail("the export's header row bypasses csvCell")
    # THE REGEX MUST BE THE CONDITION ITSELF. `(false&&/^[=+\-@\t\r]/.test(s)`
    # leaves every string this check looks for verbatim while neutralising
    # nothing - a mutation that disabled the guard in place and stayed green.
    # Pinning the flattened expression closes it without needing a JS engine.
    flatc = re.sub(r"\s+", "", body)
    if '+(/^[=+' not in flatc.replace("'", ""):
        bad += fail("csvCell's formula test is no longer the condition of its "
                    "own ternary - something has been put in front of it, and "
                    "a guard that never evaluates neutralises nothing")
    if "[=+" not in body:
        bad += fail("index.html's csvCell no longer neutralises a leading "
                    "formula character - a company name from a submission "
                    "would run as a formula when somebody opens the export")
    if '"\'"' not in body and "\"'\"" not in body:
        bad += fail("index.html's csvCell no longer prefixes an apostrophe, "
                    "which is the only neutralisation that survives a round "
                    "trip through both Excel and Sheets")
    exp = src[src.index("function exportCompanies("):]
    exp = exp[:exp.index("\nfunction ")]
    if "could not be read" not in re.sub(r"\s+", " ", exp):
        bad += fail("the company export no longer says which rows we could not "
                    "read, so a company behind a bot wall leaves here as a 0")
    return bad


def check_pay_band_is_not_an_estimate() -> int:
    """The comparable-pay block must stay a count, never a valuation.

    Three quarters of the postings here state no salary, and the standing
    temptation is to fill that with a modelled number. CLAUDE.md's first rule
    forbids it - no estimated salary, ever - because a guessed range published
    under somebody else's job is a fact about their company nobody there said.

    So this is a SOURCE check on the four properties that keep the block a
    report rather than a guess, all of which are one careless edit from being
    lost, and none of which any runtime test would catch on a page nothing here
    can render:

      - a floor on the sample. Below five, a median is one or two employers'
        opinions wearing the authority of a statistic.
      - no `other`. That family is labelled Unclassified; a band built from it
        compares a job against our own failure to read its title.
      - annual, single currency. The board stores periods rather than
        converting them on purpose: 2,080 x an hourly rate invents a full-time
        year nobody stated.
      - the disclaimer sentence, in the page, in words a reader sees.
    """
    bad = 0
    src = (ROOT / "index.html").read_text()
    if "function payBand(" not in src:
        return 0                       # the block is optional; lying is not
    body = src[src.index("function payBand("):]
    body = body[:body.index("\nfunction ")]
    # A source scan that trips on its own explanatory comments has happened
    # four times in this file. Strip them once, up front.
    nocomments = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    nocomments = re.sub(r"^\s*//.*$", "", nocomments, flags=re.M)
    if "peers.length<5" not in body.replace(" ", ""):
        bad += fail("payBand no longer requires at least five comparable "
                    "postings - a median of two is an opinion, not a band")
    # THE GUARD EXPRESSION, not the word. Checking for '"other"' anywhere in
    # the body passed on the COMMENT explaining the exclusion, so the check
    # could not fail - caught by mutating the guard and watching nothing
    # happen, which is the only way this class of check is ever caught.
    if 'p.family==="other"' not in re.sub(r"\s+", "", nocomments):
        bad += fail("payBand no longer excludes the 'other' family, which is "
                    "labelled Unclassified - it would compare a job against "
                    "the titles we could not read")
    if 'period==="year"' not in body.replace(" ", "").replace('period=="year"', 'period==="year"'):
        bad += fail("payBand no longer restricts to annual pay - mixing an "
                    "hourly rate into a yearly median invents a full-time "
                    "year nobody stated")
    if 'currency==="USD"' not in body.replace(" ", ""):
        bad += fail("payBand no longer pins the currency - two currencies in "
                    "one median is not a number")
    if "x.id!==p.id" not in body.replace(" ", ""):
        bad += fail("payBand no longer excludes the posting itself, so a role "
                    "helps set the band it is being compared against")
    # the sentence is wrapped across source lines in a template literal, so
    # it is matched against the collapsed text a reader would see
    if "not an estimate of what this job pays" not in re.sub(r"\s+", " ", body):
        bad += fail("payBand no longer tells the reader it is a count rather "
                    "than an estimate - that sentence is the whole licence "
                    "for showing a number next to a job that stated none")
    return bad


def check_shared_board_links_open_the_board() -> int:
    """A filter link the site itself produces must reopen on the job board.

    writeUrl omits `tab` on the jobs tab - correctly, "?tab=jobs&fam=gtm" says
    the same thing twice - but boot resolved a missing tab to `home`. So the
    exact string the address bar shows while somebody filters the board,
    `?fam=gtm&st=TX`, reopened on the home tab with the filters applied and
    invisible. Every shared board link, every reload, every history entry.

    It is a shape check for the same reason check_safe_urls is: there is no JS
    engine here, and the regression is a second copy of the rule appearing at
    one of the three sites that resolve a tab. So: one resolver, and nobody
    reads `tab` off the url except it.
    """
    bad = 0
    src = (ROOT / "index.html").read_text()
    if "function tabFromUrl(" not in src:
        return fail("index.html has no tabFromUrl() - a url with filters and "
                    "no ?tab= will resolve to the home tab and the filters "
                    "will be invisible")
    # every read of ?tab= must be inside the resolver
    body = src.split("function tabFromUrl(", 1)[1]
    body = body[:body.index("\nlet URL_LOADING")] if "\nlet URL_LOADING" in body else body[:600]
    outside = [m for m in re.findall(r'[^\n]*\bget\("tab"\)[^\n]*', src)
               if m.strip() not in body]
    for line in outside:
        bad += fail(f"index.html reads ?tab= outside tabFromUrl: {line.strip()!r} "
                    f"- a second copy of this rule is how it came to be wrong "
                    f"in one place and right in the others")
    # and the resolver must actually know about the filter keys, or it is a
    # rename away from being the old behaviour with a new name
    if "URLKEYS" not in body:
        bad += fail("tabFromUrl does not consult URLKEYS, so it cannot tell a "
                    "filtered board url from a bare one")
    # AND WHAT IT RETURNS. The check asserted the resolver existed, that no
    # ?tab= was read outside it, and that URLKEYS was mentioned - never what
    # came back. Changing the filter branch's `return "jobs"` to
    # `return fallback` left the suite green and restored the original bug
    # verbatim: ?fam=gtm&st=TX, the exact string the address bar shows while
    # somebody filters, reopening on the home tab with the filters invisible.
    flatb = re.sub(r"\s+", "", body)
    if 'some(k=>u.has(k)))return"jobs"' not in flatb:
        bad += fail("tabFromUrl's filter branch no longer returns \"jobs\". A "
                    "url carrying board filters and no ?tab= resolves to the "
                    "home tab again, with the filters applied and invisible - "
                    "every shared board link, reload and back button")
    return bad


def check_posts_at_vocabulary() -> int:
    """index.html's copy of the posts_at labels must match posts_at.py.

    The page's own comment says it: "IS DUPLICATED AND THEREFORE IT WILL ROT -
    see the report for the selftest case that should guard it." No such case
    existed. Drift here is silent and total, exactly like the alerts
    vocabulary it was modelled on: a ruling stores a `where` the page has no
    label for, and the card falls back to "another site" forever.

    It also checks that build_board carries the field at all. The renderers,
    the admin action and the vocabulary were all built in August and the value
    never crossed into board.json, so the whole feature was invisible.
    """
    import re
    import posts_at
    errors = 0
    page = (ROOT / "index.html").read_text()
    m = re.search(r"const POSTS_AT_LABEL\s*=\s*\{(.*?)\}", page, re.S)
    if not m:
        return fail("index.html has no POSTS_AT_LABEL map - the card cannot say "
                    "where a company posts")
    in_page = set(re.findall(r"(\w+)\s*:", m.group(1)))
    in_py = set(posts_at.WHERE)
    for k in sorted(in_py - in_page):
        errors += fail(f"posts_at.py can store where={k!r} and index.html has no "
                       f"label for it - the card would say 'another site' for a "
                       f"ruling somebody actually made")
    for k in sorted(in_page - in_py):
        errors += fail(f"index.html carries a posts_at label {k!r} that "
                       f"posts_at.py cannot store")
    src = "\n".join(l.split("#")[0] for l in
                    (ROOT / "scripts" / "build_board.py").read_text().splitlines())
    if '"posts_at"' not in src:
        errors += fail("build_board does not carry posts_at onto the org record, "
                       "so every ruling made in the admin stays invisible on the "
                       "public card")
    return errors


def check_crawl_files() -> int:
    """A single-page app cannot be indexed by luck.

    /robots.txt and /sitemap.xml both answered with the app's own HTML and a
    200, so there were no crawl directives at all and every mistyped path was
    a soft-404 teaching crawlers the domain is duplicate content. 2,113
    company records are the largest body of unique writing here and none of
    them had an address until ?co= existed.

    The sitemap must list only addresses that resolve to something, and must
    NOT list companies with nothing open: a sitemap is a claim that a url is
    worth crawling, and 1,800 near-identical no-openings pages is how a site
    teaches a crawler to stop believing it.
    """
    import build_site, tempfile, shutil, json as _json
    import pathlib as _pl
    errors = 0
    board = {"postings": [], "conferences": [{"event_tag": "APCO 2026"}],
             "organizations": [{"id": "hiring-co", "open_roles": 3},
                               {"id": "quiet-co", "open_roles": 0}]}
    brand = {"site": "https://example.test", "name": "SLED JOBS"}
    tmp = _pl.Path(tempfile.mkdtemp())
    try:
        build_site.write_crawl_files(tmp, board, brand)
        for f in ("robots.txt", "sitemap.xml", "404.html"):
            if not (tmp / f).exists():
                errors += fail(f"build_site did not write {f}")
        sm = (tmp / "sitemap.xml").read_text()
        # /c/<id>, not ?co=. Both show the same company, so one has to be
        # canonical or they compete; the static page is the one a crawler that
        # never runs JavaScript can actually read.
        #
        # EXTENSIONLESS. Cloudflare 308s /c/<id>.html to /c/<id>, so submitting
        # the .html form pointed every crawler at a redirect, and the target
        # then named the .html form as its canonical - back at the redirect.
        if "/c/hiring-co" not in sm:
            errors += fail("a company with an opening is missing from the "
                           "sitemap, or the sitemap still points at the app "
                           "view rather than the prerendered page")
        if "quiet-co" in sm:
            errors += fail("a company with nothing open is in the sitemap - a "
                           "sitemap full of near-identical empty pages teaches "
                           "a crawler to stop believing this one")
        # /e/<slug>.html, matching the prerendered conference page, not the
        # tab query it used to point at.
        if "/e/apco-2026" not in sm:
            errors += fail("conferences are missing from the sitemap, or it "
                           "still points at the tab query rather than the "
                           "prerendered conference page")
        rob = (tmp / "robots.txt").read_text()
        if "Sitemap: https://example.test/sitemap.xml" not in rob:
            errors += fail("robots.txt does not name the sitemap")
        if "Disallow: /data/" not in rob:
            errors += fail("robots.txt invites crawlers into the 6MB data feed")
        if "example.test" not in rob or "example.test" not in sm:
            errors += fail("the crawl files hardcode a domain instead of reading "
                           "brand.json - a rebrand would leave them pointing at "
                           "the old one")
        four = (tmp / "404.html").read_text()
        if "noindex" not in four:
            errors += fail("404.html is missing noindex")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_semantic_map() -> int:
    """semantic.py's own full check, which nothing was running.

    check_search_routes_are_live catches a DEAD route: a phrase pointing at a
    sector/category the schema no longer holds. semantic.check() catches that
    and the mirror image, which is the one that shipped: a category the schema
    DOES hold that no phrase can reach. On 2026-08-29 Health & Human Services
    / Case Management & Social Care was in that state with eight real
    companies in it - Unite Us, Findhelp, Bitfocus and five more - unfindable
    by concept search. `semantic.py --check` had been failing for days and
    nobody ran it.

    An unreachable category is the search box telling a reader there is no
    work here, which is the same asymmetric error as a crawler reporting no
    jobs when it could not read the page. It also verifies that index.html's
    copy of the map has not drifted, and that no phrase sits in two concepts.

    check() takes quiet=True specifically so this could call it. That
    argument has existed the whole time.
    """
    import semantic
    n = semantic.check(quiet=True)
    if n:
        return fail(f"semantic.py --check reports {n} problem(s). Run "
                    f"`python3 scripts/semantic.py --check` for the list; a "
                    f"category no phrase reaches is invisible to search, and "
                    f"a drifted index.html copy is a search box that "
                    f"disagrees with itself.")
    return 0


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



def check_headline_counts_openings() -> int:
    """The number the board quotes at strangers must be roles, not rows.

    CLAUDE.md rules on this outright - "the headline counts openings, not
    rows" - and the jobs page was breaking it, reading D.postings.length and
    calling 4,334 advertisements "open roles" when there were 3,693 roles.

    The reason is Xplor. They advertised ONE Account Executive requisition in
    93 cities. Counting rows put a single advertisement third on a leaderboard
    of the biggest go-to-market pushes in the market - a claim about the market
    that was really a claim about one company's posting habits.

    Checked as a shape, because there is no JS engine here: any element
    labelled "open roles" must be fed from totals.openings, and postings may
    only appear beside a word that says what it is - "postings", "rows",
    "advertised in".
    """
    raw = (ROOT / "index.html").read_text()
    # Comments are stripped first. The comment ABOVE the fix quotes the bug it
    # describes - "called 4,334 advertisements open roles" - and tripped this
    # check on its own explanation. A checker that cannot tell code from prose
    # about the code fails the first time somebody writes the prose.
    html = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    errors = 0

    # the jobs headline
    i = html.find("open roles")
    while i != -1:
        line_start = html.rfind("\n", 0, i)
        window = html[max(0, i - 260):i]
        if "D.postings.length" in window and "totals" not in window:
            errors += fail("index.html labels D.postings.length as \"open "
                           "roles\". That is one row per advertisement, not "
                           "one per role - CLAUDE.md: the headline counts "
                           "openings, not rows")
            break
        i = html.find("open roles", i + 1)

    # The home card had the same bug in a different shape: q.length is a row
    # count, and it sat under the heading "Sales roles" three elements below a
    # banner slide that says "we count openings, not rows". Any count fed
    # straight from a filtered posting list must not be labelled as roles.
    for m in re.finditer(r"\$\{q\.length[^}]*\}\s*</span>\s*<h3>([^<]*)</h3>", html):
        if re.search(r"\broles?\b", m.group(1), re.I):
            errors += fail(f"index.html labels a posting-row count as "
                           f"{m.group(1)!r}. Rows are advertisements; roles "
                           f"are openings, and the home banner on the same "
                           f"screen says so")

    # and totals.postings must never be labelled as roles anywhere
    for m in re.finditer(r"totals\.postings", html):
        after = html[m.end():m.end() + 90]
        if re.search(r"\bopen roles\b", after) and not re.search(
                r"\b(posting|postings|rows|advertised)\b", after):
            errors += fail("index.html labels totals.postings as open roles")
    return errors



def check_admin_blurbs_have_no_typed_counts() -> int:
    """A number written into prose is true the day it is typed and wrong after.

    The admin's queue descriptions carried two: "102 companies are here" when
    the queue held 50, and "537 careers pages on file" when there were 887.
    Neither is visible as a bug - the sentence still reads perfectly - and both
    were telling the owner the size of his own backlog incorrectly, on the page
    where he decides what to work on.

    HTTP status codes and year-like numbers are allowed: "a 403, a timeout" is
    naming a thing, not counting one.
    """
    html = (ROOT / "admin.html").read_text()
    i = html.find("const INTRO = {")
    if i < 0:
        return fail("admin.html: the queue blurbs (INTRO) are gone")
    j = html.find("\n};", i)
    block = html[i:j if j > 0 else len(html)]

    ALLOWED = {"200", "301", "302", "403", "404", "429", "500", "503"}
    errors = 0
    for m in re.finditer(r"\b\d[\d,]{1,6}\b", block):
        n = m.group(0)
        if n in ALLOWED or re.fullmatch(r"(19|20)\d\d", n):
            continue
        line = block[block.rfind("\n", 0, m.start()) + 1:
                     block.find("\n", m.end())].strip()
        errors += fail(f"admin.html queue blurb has the count {n!r} typed into "
                       f"its prose: {line[:70]!r}. Counts go stale silently - "
                       f"the blurb is given a live one at render time")
    return errors



def check_queues_do_not_propose_deleted_categories() -> int:
    """A proposal aimed at a category that no longer exists is unacceptable.

    Deleting a category is a normal thing to do - two went on 2026-08-25, when
    Health & Human Services and Courts & Justice stopped being categories under
    General Gov and became only sectors. Four separate things reference a
    category by name, and only three were already guarded:

      companies         validate() refuses an unknown sector/category
      `also` placements validate() reads those too
      search routes     check_search_routes_are_live
      QUEUE PROPOSALS   nothing

    A queue row proposing "move this to General Gov / Courts & Justice" would
    be offered to the owner, accepted, and then refused by validate() at the
    write - or worse, silently written by a path that does not validate. The
    row looks completely normal either way.
    """
    import admin
    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    cats = {s["name"]: set(s["categories"]) for s in schema["sectors"]}
    companies = admin.read("companies.json", [])
    board = admin.read("board.json", {})
    errors = 0
    for name, fn in admin.QUEUES.items():
        try:
            rows = fn(companies, board)
        except Exception:
            continue           # a queue that cannot build is another check's job
        for r in rows:
            if not isinstance(r, dict):
                continue
            s, c = r.get("proposed_sector"), r.get("proposed_category")
            if s is None and c is None:
                continue
            if s not in cats or c not in cats.get(s, ()):
                errors += fail(f"the {name!r} queue proposes moving "
                               f"{r.get('name', r.get('id', '?'))!r} to "
                               f"{s} / {c}, which is not in schema.json. The "
                               f"row reads normally and the write would be "
                               f"refused after the ruling was made")
                break          # one per queue is enough to say it
    return errors



def check_a_count_is_never_a_job_title() -> int:
    """"Engineer jobs 555,845 open jobs" was a posting on the public board.

    LinkedIn's company jobs page carries a browse-by-title rail, and every link
    in it passes the html enumerator's tests honestly: the href is a real
    /jobs/ URL and the text reads like a title, because it starts with one.
    Fourteen of CORE Business Technologies' twenty postings were that rail.
    _NAV could not catch them - it is anchored, so it matches a link reading
    "Jobs" and nothing longer.

    Two of the eighteen were "Business Development Representative jobs 52,084
    open jobs" and "Sales Technician jobs 78,850 open jobs". Neither reached
    the quota-carrying count, which is luck rather than design: the family
    classifier declined them. Three did reach the go-to-market family on the
    market-intel page.

    The negatives are pinned as hard as the positives, because a rule that
    ate "Analyst 3 - Data Systems" or a title with a salary in it would delete
    real jobs silently.
    """
    import ats
    errors = 0
    reject = [
        "Engineer jobs 555,845 open jobs",
        "Business Development Representative jobs 52,084 open jobs",
        "Manager jobs 1,880,925 open jobs",
        "See all 12 open positions",
        "Browse 40+ open roles",
    ]
    keep = [
        "Account Executive",
        "Sales Account Executive - GovTech ($65K-$85K+)",
        "Analyst 3 - Data Systems",
        "Engineer II, Platform",
        "Director of Accounts",
        "Senior RF Engineer",
        "Account Executive, SLED West",
    ]
    for s in reject:
        if not ats._JOB_COUNT.search(s):
            errors += fail(f"{s!r} is a count, not a job, and would be listed "
                           f"on the board as somebody's opening")
    for s in keep:
        if ats._JOB_COUNT.search(s):
            errors += fail(f"{s!r} is a real job title and the count filter "
                           f"would delete it")

    # The rule must be WIRED IN, not merely present. Deleting the call site in
    # fetch_html_titles left every assertion above passing, because they
    # exercise the regex and not the enumerator. So the enumerator is run:
    # a page carrying the rail must come back without it.
    page = ("<html><body>"
            "<a href='/jobs/view/account-executive-123'>Account Executive</a>"
            "<a href='/jobs/view/engineer-ii-456'>Engineer II, Platform</a>"
            "<a href='/jobs/engineer-jobs'>Engineer jobs 555,845 open jobs</a>"
            "<a href='/jobs/bdr-jobs'>Business Development Representative jobs "
            "52,084 open jobs</a>"
            "</body></html>")

    class _Resp:
        text = page

    real_get = ats._get
    try:
        ats._get = lambda *a, **k: _Resp()
        titles = [r["title"] for r in ats.fetch_html_titles("https://x.test/jobs")]
    except Exception as exc:
        titles = None
        errors += fail(f"the html enumerator threw on a normal page: {exc}")
    finally:
        ats._get = real_get

    if titles is not None:
        leaked = [s for s in titles if ats._JOB_COUNT.search(s)]
        if leaked:
            errors += fail(f"fetch_html_titles returned {leaked[0]!r}. The count "
                           f"rule exists but is not applied in the enumerator, "
                           f"so the rail reaches the board anyway")
        if "Account Executive" not in titles:
            errors += fail("fetch_html_titles dropped a real job title while "
                           "filtering the rail")

    # BOTH EXTRACTORS MUST KNOW THE RULE. render_fetch.py has its own NAV
    # filter and its own loop, and it never learned this one - so Nutanix came
    # back from the renderer as hiring "102 open vacancies in Sales", the same
    # shape ats.py has refused since the LinkedIn rail. A rule only one of two
    # extractors knows is a rule with a hole in it, and the hole is invisible
    # because each extractor looks correct on its own.
    rf = (ROOT / "scripts" / "render_fetch.py").read_text()
    if "_JOB_COUNT" not in rf:
        errors += fail("render_fetch.py does not apply the count rule, so a "
                       "rendered page can still report '102 open vacancies in "
                       "Sales' as somebody's job title")
    elif "re.compile" in rf.split("_JOB_COUNT")[0][-200:]:
        errors += fail("render_fetch.py defines its own copy of the count rule "
                       "instead of importing ats._JOB_COUNT; two copies drift")

    # and nothing currently on the board may trip it except the known rail
    board = json.loads((ROOT / "data" / "board.json").read_text())
    hit = [p for p in board.get("postings", [])
           if ats._JOB_COUNT.search(p.get("title", ""))]
    stale = [p for p in hit if p.get("company") != "CORE Business Technologies"]
    if stale:
        errors += fail(f"{len(stale)} posting(s) on the board look like a count "
                       f"rather than a job, and are not the known LinkedIn "
                       f"rail: {stale[0].get('title')!r} at "
                       f"{stale[0].get('company')!r}")
    return errors



def check_an_unread_board_is_not_a_zero() -> int:
    """A board we could not READ must not render as a company with no jobs.

    The board already refuses to show a zero for a company whose board was
    never FOUND - noBoardNote says so on the card, and the counters say "not
    known". A board we HAVE, that failed to fetch this run, is the same fact
    wearing different clothes, and it said nothing at all: 64 companies on the
    2026-08-26 build, 524 postings between them, every card reading "0 open
    roles" with no explanation.

    Market Intel discloses the count - "N boards could not be read this run, so
    those companies show zero openings rather than none existing" - in one
    sentence at the bottom of a different tab. That is not where somebody
    decides whether to apply.

    Checked as a shape: both branches must read o.unreadable, because the
    obvious regression is someone simplifying the condition back to
    no_board_on_file alone.
    """
    html = (ROOT / "index.html").read_text()
    errors = 0
    i = html.find("function noBoardNote(")
    if i < 0:
        return fail("index.html: noBoardNote is gone; nothing tells a reader "
                    "why a company shows no roles")
    body = html[i:i + 2000]
    if "o.unreadable" not in body:
        errors += fail("noBoardNote ignores o.unreadable, so a company whose "
                       "board failed to fetch shows a bare zero with no "
                       "explanation - the same silence-as-fact this board "
                       "refuses everywhere else")

    j = html.find('<div class="lbl">open roles</div>')
    if j < 0:
        errors += fail("index.html: the open-roles counter is gone")
    else:
        window = html[max(0, j - 700):j]
        if "unreadable" not in window:
            errors += fail("the open-roles counter shows a number without "
                           "checking o.unreadable, so an unread board reports "
                           "0 open roles as if that were measured")
    return errors



def check_the_gate_sees_an_unreadable_cliff() -> int:
    """A percentage cannot see a few hundred postings fall off a cliff.

    On 2026-08-26 the board fell 13.3% and the publish gate would have shipped
    it, because the limit is 25%. 524 of the missing postings belonged to 33
    companies whose boards had gone unreadable - and Civica (89), Career TEAM
    (64) and BibliU (51) all read perfectly when retried by hand minutes later.
    A transient fetch failure, published as 33 companies with no jobs.

    The discriminator is not "fell to zero". The history shows 31 companies
    doing that in one ordinary day; companies really do close every role. It is
    "fell to zero AND the board would not read" - an emptied board returns an
    empty list, a broken fetch returns nothing at all, and the run records
    which happened.

    Pinned in both directions, because a gate that refuses every build is
    turned off within a week.
    """
    import build_site
    errors = 0

    # THE FIXTURE HAS TO BE HEALTHY ON EVERY LEG, not just the one under test.
    #
    # This used to be ONE organization with 4,000 postings, and it passed only
    # because the gate's other leg - companies with an opening - had a low
    # enough baseline to tolerate it. The nightly run of 2026-08-30 moved that
    # baseline to 299 and the fixture started failing: 1 against 299 is a 99.7%
    # fall, so the gate objected, correctly, to a board that was never meant to
    # look like that.
    #
    # A test that breaks when the real data moves is testing the data. The
    # organization count is derived from the same baseline the gate compares
    # against, so this stays a test of "a healthy board passes" rather than a
    # test of what last night happened to produce.
    n_orgs = max(1, build_site.previous_hiring() or 1)
    good = {"postings": [{"company_id": f"c{i % n_orgs}"} for i in range(4000)],
            "organizations": [{"id": f"c{i}", "open_roles": 4000 // n_orgs + 1}
                              for i in range(n_orgs)]}
    objection = build_site.sanity_check(good)
    if objection:
        errors += fail(f"the publish gate objects to a healthy board; a gate "
                       f"that cries wolf gets forced past and stops working. "
                       f"It said: {objection}")

    # The synthetic company has to be one HISTORY KNOWS, or the gate has no
    # baseline for it and correctly says nothing. A made-up id proves only that
    # the test was made up: the first version of this check used one and failed
    # against working code.
    hist = sorted((ROOT / "data" / "history").glob("*.json"))
    if len(hist) >= 2 and build_site.previous_snapshot():
        counts: dict[str, int] = {}
        for pid in json.loads(hist[-2].read_text()).get("ids", []):
            cid = pid.split("::")[0]
            counts[cid] = counts.get(cid, 0) + 1
        big = max(counts, key=counts.get) if counts else None
        if big and counts[big] >= 10:
            cliff = {"postings": [{"company_id": "a"} for _ in range(3000)],
                     "organizations": [{"id": "a", "open_roles": 3000},
                                       {"id": big, "open_roles": 0,
                                        "unreadable": "HTTP 404"}]}
            if not any("would not read" in o
                       for o in build_site.sanity_check(cliff)):
                errors += fail(f"the publish gate does not notice postings "
                               f"disappearing with boards that would not read: "
                               f"{big!r} held {counts[big]} postings and its "
                               f"board failing raised no objection. A 13% fall "
                               f"of exactly that shape passed it once")
    if build_site.MAX_UNREADABLE_LOSS > 0.15:
        errors += fail(f"MAX_UNREADABLE_LOSS is {build_site.MAX_UNREADABLE_LOSS:.0%}; "
                       f"the failure it exists to catch was 13% of the board")
    return errors



def check_a_transport_failure_gets_a_second_chance() -> int:
    """A socket that dropped is not a company with no jobs.

    On 2026-08-26 a build marked 64 boards unreadable against 18 the day
    before. 47 were "network error" - no answer at all - and Civica (89
    postings), Career TEAM (64) and BibliU (51) read perfectly when retried by
    hand minutes later. Without a retry, one dropped socket publishes a company
    as not hiring for a whole day.

    The other 16 were HTTP 404, and those must NOT be retried. A 404 is the
    board answering: the slug is gone. Asking twice spends somebody's request
    to learn the same thing, and it would hide a real finding more slowly.

    Checked as a shape, since running it would mean fetching hundreds of live
    boards: the retry must exist, must be conditioned on the transport case,
    and must not fire on everything.
    """
    src = (ROOT / "scripts" / "build_board.py").read_text()
    i = src.find("def read_board(")
    if i < 0:
        return fail("build_board.py: read_board is gone")
    raw = src[i:i + 2600]
    # Comments are stripped first. The comment explaining the retry contains
    # the words "retry" and "network error", so deleting the CODE left this
    # check reading its own explanation and passing. Second time today that
    # exact trap has fired; a checker that cannot tell code from prose about
    # the code is testing the prose.
    body = "\n".join(ln.split("#")[0] for ln in raw.split("\n"))
    errors = 0
    if "network error" not in body:
        errors += fail("read_board does not distinguish a transport failure "
                       "from an answer, so a dropped socket and a dead board "
                       "are recorded the same way")
    # Count CALLS, not the definition: "def once():" contains the same
    # substring, so a naive count never falls below 2 and the check passed
    # against code with the retry deleted.
    calls = len(re.findall(r"(?<!def )\bonce\(\)", body))
    if calls < 2:
        errors += fail("read_board never retries. One dropped socket publishes "
                       "a company as not hiring for a day - it cost 524 "
                       "postings across 33 companies once")
    # the retry must be conditional; retrying a 404 is a different bug
    if "transient" not in body:
        errors += fail("read_board's retry is not conditioned on the failure "
                       "being transient. A 404 retried is a request spent to "
                       "learn the slug is still gone")
    return errors



def check_a_refusal_is_not_a_search() -> int:
    """"We could not find a board" and "they turned us away" are not the same.

    1,688 companies have been probed for a job board. 1,507 were read and
    genuinely yielded nothing - for those, "we could not find a public job
    board" is exactly right. 181 were refused at the door: a 403, a bot wall,
    a fetch that gave up. We never got far enough to look, and every one of
    their cards said we had looked and found nothing.

    One of those sentences is a statement about the company. The other is a
    statement about us, and saying it the wrong way round is how a company that
    may well be hiring ends up described as one nobody can find work at.

    The admin has always had this right - the blocked queue says "not evidence
    of anything except that the fetcher was refused" - but that sentence lives
    behind Access and the public card never saw it.

    Both halves are pinned: build_board must emit the probe state, and the card
    must branch on it. Either alone is silent.
    """
    errors = 0
    src = (ROOT / "scripts" / "build_board.py").read_text()
    if '"probe":' not in src:
        errors += fail("build_board no longer emits a probe state, so the card "
                       "cannot tell a refusal from a search that came up empty")

    # the two marker lists must agree, or the queue and the card describe the
    # same company differently
    import admin
    import build_board
    if set(admin.BLOCKED_MARKERS) != set(build_board._BLOCKED_MARKERS):
        errors += fail(f"admin and build_board disagree about what counts as "
                       f"blocked: {sorted(set(admin.BLOCKED_MARKERS) ^ set(build_board._BLOCKED_MARKERS))}. "
                       f"The queue and the public card would describe the same "
                       f"company differently")

    html = (ROOT / "index.html").read_text()
    i = html.find("function noBoardNote(")
    if i < 0:
        return errors + fail("index.html: noBoardNote is gone")
    body = html[i:i + 2600]
    if 'probe==="blocked"' not in body.replace(" ", ""):
        errors += fail("noBoardNote does not branch on a blocked probe, so a "
                       "company whose site refused our reader is told to a "
                       "visitor as a company with no job board")
    return errors



def check_coming_soon_is_not_a_parked_domain() -> int:
    """A company that has not launched is not a domain for sale.

    wrangler.ai puts "Wrangler.ai - the agentic Chief of Staff for field
    operations. Coming soon." in its meta description, sets og:site_name to its
    own name, and signs its footer "(c) 2026 Wrangler Technologies, Inc." The
    PARKED pattern listed "coming soon" beside "buy this domain", so a real
    company was read as a squatter on two words.

    The owner saw both conclusions at once. One line said "the page calls
    itself Wrangler.ai"; the next said "it never names this company - the
    domain is parked or up for sale". Two code paths disagreeing in front of
    the person being asked to rule.

    So the weak signals are separated and corroborated: "coming soon" and
    "under construction" count only where the page names NOBODY. A squatter's
    holding page says coming soon and identifies no one; a pre-launch company
    says coming soon and says who it is. A STRONG signal still wins outright -
    a page can name itself and still be a for-sale listing, which is precisely
    the DomainMarket business model.
    """
    import find_websites as fw
    errors = 0
    named = ('<html><head><meta property="og:site_name" content="Wrangler.ai">'
             '<meta name="description" content="Coming soon."></head>'
             '<body>&copy; 2026 Wrangler Technologies, Inc.</body></html>')
    cases = [
        ("<title>Coming soon</title><body>Coming soon</body>", True,
         "a holding page that names nobody"),
        ("<title>Under Construction</title><body>under construction</body>", True,
         "the classic holding page"),
        (named, False,
         "a pre-launch company that names itself"),
        ("<title>vocaltechnologies.com - Technology Domains for Sale</title>", True,
         "a strong signal in the title"),
        ("<body>buy this domain</body>", True, "a strong signal"),
        ('<html><head><meta property="og:site_name" content="Squatter Co"></head>'
         "<body>buy this domain</body></html>", True,
         "a strong signal beats naming itself"),
    ]
    for html, want, what in cases:
        got = fw._parked(html)
        if got != want:
            errors += fail(f"_parked says {got} for {what}; expected {want}")
    if not fw.identifies(named, "Wrangler.ai"):
        errors += fail("identifies() rejects a pre-launch company that names "
                       "itself in og:site_name and its copyright line")
    # and the weak terms must not have been left in the strong pattern
    for weak in ("coming soon", "under construction"):
        if fw.PARKED.search(weak):
            errors += fail(f"{weak!r} is still in the STRONG parked pattern, so "
                           f"it convicts a page on its own")
    return errors



def check_a_parent_board_ruling_names_the_parent() -> int:
    """"Somewhere else" was the only answer for "their parent's board".

    Nine places a company could advertise - LinkedIn, Indeed, Glassdoor,
    ZipRecruiter, Built In, a gov portal, a recruiter, email, somewhere else -
    and not one of them was the commonest answer in a consolidated market. SAP
    Concur's roles are on jobs.sap.com. Conduent Transportation's are on
    careers.conduent.com. RecDesk's and Vermont Systems' are Xplor
    Recreation's. Each had to be filed as "somewhere else", which reads like an
    obscure job site rather than the truth.

    The name is mandatory, and that is the whole point of the option. Sending
    somebody to a parent's board without saying whose it is is the sharpest
    form of the mistake this file exists to prevent: they land on four thousand
    SAP openings and cannot tell which three are Concur's. A card that cannot
    name the parent cannot warn them.
    """
    import posts_at as pa
    errors = 0
    if "parent" not in pa.WHERE:
        return fail("posts_at has no 'parent' option, so a company whose roles "
                    "are on its parent's board can only be filed as "
                    "'somewhere else'")

    URL = "https://jobs.sap.com/search/"
    if not pa.check("parent", URL):
        errors += fail("a parent ruling was accepted without naming whose "
                       "board it is; the card cannot warn without the name")
    if pa.check("parent", URL, "SAP"):
        errors += fail(f"a parent ruling naming SAP was refused: "
                       f"{pa.check('parent', URL, 'SAP')}")

    rec = pa.build("parent", URL, "owner", "", "SAP")
    if rec.get("board_owner") != "SAP":
        errors += fail("the parent's name is not carried on the record, so "
                       "nothing downstream can render it")
    said = pa.sentence(rec)
    if "SAP" not in said:
        errors += fail(f"the card does not name the parent: {said[:80]!r}")
    if "not be theirs" not in said and "not their" not in said:
        errors += fail(f"the card does not warn that most roles on that board "
                       f"are somebody else's: {said[:110]!r}")

    # the other places must not have grown a requirement they do not need
    for w in ("linkedin", "indeed", "govportal"):
        if pa.check(w, "https://www.linkedin.com/company/x/jobs"
                    if w == "linkedin" else f"https://{w}.example.com/jobs"):
            continue        # a wrong-host complaint is fine here
    if pa.check("email", ""):
        errors += fail("'by email only' now demands a link it never needed")
    return errors


def check_the_owner_can_argue_with_the_logic() -> int:
    """Every queue shows its reasoning; there was nowhere to say it is wrong.

    The panels explain themselves while asking for a ruling - "the domain
    matches this name", "this is a holding page", "slug does not match". Three
    of those were wrong in a single sitting: a rename offered onto a name
    already taken, a live company convicted of being a parked domain on the
    words "coming soon", and "send it to the acquisitions queue" for a queue
    no button can add to.

    The owner is the only person who ever sees those, at the one moment the
    context is in front of him. Without somewhere to put it the choice is rule
    anyway or lose it, and losing it is what had been happening.

    A note must never be able to change the board - it is an argument about
    the logic, not a ruling on a company.
    """
    import admin
    errors = 0
    if "suggest" not in admin.ACTIONS:
        return fail("there is no way to record an argument about the logic")
    if "suggest" in admin.OPEN_ACTIONS:
        errors += fail("the suggest action is ungated; it writes a file and "
                       "should need the console code like every other write")

    # The action WRITES, so the live file is put back exactly as found. A
    # check that leaves a note behind every run fills the owner's own file
    # with test data - which is the mistake a probe already made once against
    # companies.json, and the reason that rule is in CLAUDE.md.
    notes_path = ROOT / "data" / "logic_notes.json"
    saved = notes_path.read_text() if notes_path.exists() else None
    before = json.loads((ROOT / "data" / "companies.json").read_text())
    try:
        out = admin.ACTIONS["suggest"]({"queue": "websites", "id": "x",
                                        "name": "X", "saw": "panel said a thing",
                                        "argument": "and the thing was wrong"})
        if not out.get("ok"):
            errors += fail(f"recording an argument failed: {out}")
        after = json.loads((ROOT / "data" / "companies.json").read_text())
        if before != after:
            errors += fail("recording an argument about the logic CHANGED "
                           "companies.json. It must never touch the map")
        if not admin.ACTIONS["suggest"]({"argument": "   "}).get("error"):
            errors += fail("an empty argument was recorded as if it said "
                           "something")
    finally:
        if saved is None:
            notes_path.unlink(missing_ok=True)
        else:
            notes_path.write_text(saved)
    return errors



def check_a_card_link_yields_the_title_not_the_whole_card() -> int:
    """"Supply Chain Analyst Teaneck, NJ Full-time More Details Less Details".

    uveye.com wraps the title, the location, the employment type and a
    More/Less toggle in ONE anchor, so flattening the link text put all of it
    in the title field - sixteen postings on the public board reading like
    that. The title was sitting in its own <h3> the whole time.

    Only when there is exactly one heading in the link. Two headings mean the
    anchor is a section rather than a card, and picking one is the sort of
    cleverness that files a location as a job title.

    THE DEDUP MOVED WITH IT, and that is the half worth testing. It keyed on
    the title, which only worked because the titles were dirty: Samsara's rows
    carried their locations, so two postings of one role were two strings.
    Cleaning the titles collapsed 241 rows to 192 - a tidy-up that silently
    deleted forty-nine advertisements. The url is what distinguishes two
    postings, CLAUDE.md says the per-location rows all stay, and opening_id
    already collapses them for the headline.
    """
    import ats
    errors = 0
    CARD = ("<html><body>"
            "<a href='/job/supply-chain-analyst'>"
            "<div><h3>Supply Chain Analyst</h3>"
            "<div><span>Teaneck, NJ</span><span>Full-time</span></div>"
            "<div><span>More Details</span><span>Less Details</span></div>"
            "</div></a>"
            "<a href='/job/ops-manager-us'><div><h3>Ops Manager</h3>"
            "<span>Remote - US</span></div></a>"
            "<a href='/job/ops-manager-ca'><div><h3>Ops Manager</h3>"
            "<span>Remote - Canada</span></div></a>"
            "</body></html>")

    class _R:
        text = CARD

    real = ats._get
    try:
        ats._get = lambda *a, **k: _R()
        rows = ats.fetch_html_titles("https://x.test/careers")
    except Exception as exc:
        ats._get = real
        return fail(f"the enumerator threw on a card-style page: {exc}")
    finally:
        ats._get = real

    titles = [r["title"] for r in rows]
    if "Supply Chain Analyst" not in titles:
        errors += fail(f"the title was not read out of its heading; got "
                       f"{titles[:2]!r}")
    for t_ in titles:
        for junk in ("More Details", "Full-time", "Teaneck"):
            if junk in t_:
                errors += fail(f"the card's {junk!r} ended up in the title "
                               f"{t_!r}")
    # two links, same role, different places: BOTH rows survive
    if sum(1 for t_ in titles if t_ == "Ops Manager") != 2:
        errors += fail("two postings of one role were deduped into one. The "
                       "rows are per-advertisement; opening_id does the "
                       "collapsing for the headline, not the fetcher")
    return errors



def check_refresh_renders_a_shell_before_saying_unknown() -> int:
    """The board listed an AE role while the company's own card said Unknown.

    build_board.py has always fallen back to a real browser when a careers page
    turns out to be a JavaScript shell. refresh.py did not. So the two
    pipelines disagreed in public: eleven AE roles sat on the board under cards
    that would not admit the company was hiring - Frontline Education's
    Strategic Account Executive among them.

    Two things are pinned. The fallback must cover BOTH failure paths: a rollup
    that comes back Unknown, and an AtsError raised before any rollup happens.
    The first version of the fix only covered the rollup, and missed "page too
    small - likely JS-rendered" - the single most render-appropriate failure
    there is.

    And it must stay OPTIONAL. Playwright is a 150MB browser that selftest, a
    local run and a fresh clone must not need. With it absent the fallback
    returns None and the honest Unknown stands.
    """
    import refresh
    errors = 0
    if not hasattr(refresh, "_try_render"):
        return fail("refresh.py no longer renders a shell before calling it "
                    "Unknown; the board and the cards will disagree again")

    # absent Playwright must be a no-op, never a crash
    import builtins
    real = builtins.__import__

    def no_playwright(name, *a, **k):
        if name == "render_fetch":
            raise ImportError("render_fetch unavailable")
        return real(name, *a, **k)

    builtins.__import__ = no_playwright
    try:
        got = refresh._try_render("html", "https://x.test/careers")
    except Exception as exc:
        got = "CRASHED"
        errors += fail(f"the render fallback crashed when Playwright is "
                       f"absent: {exc}")
    finally:
        builtins.__import__ = real
    if got not in (None, "CRASHED"):
        errors += fail(f"with no renderer available the fallback returned "
                       f"{got!r}; it must return None and leave the Unknown")

    # It must never be reached for a structured board. Calling it and checking
    # the return proves nothing - a greenhouse slug is not a renderable url, so
    # it comes back None whether the guard is there or not. The guard itself is
    # the thing, so the guard itself is what is checked, and this is a shape
    # check rather than a behavioural one on purpose.
    guard = inspect.getsource(refresh._try_render)
    if 'kind != "html"' not in guard:
        errors += fail("the render fallback no longer refuses non-html boards. "
                       "A greenhouse or workday board has a real API; firing a "
                       "browser at its slug spends 30 seconds to learn nothing")
    if refresh._try_render("html", None) is not None:
        errors += fail("the render fallback fired with no url to render")

    # both failure paths must reach it
    src = (ROOT / "scripts" / "refresh.py").read_text()
    body = src[src.find("def check_company"):]
    body = body[:body.find("\ndef ", 1)]
    if body.count("_try_render") < 2:
        errors += fail("check_company calls the render fallback on only one "
                       "failure path. An AtsError returns before the rollup, "
                       "and 'page too small - likely JS-rendered' arrives that "
                       "way - the case a browser most obviously fixes")
    return errors



def check_a_page_scan_of_the_parents_site_is_not_evidence() -> int:
    """Five cards said "Yes - AE-type role" off somebody else's careers page.

    Cartegraph's board is opengov.com and its own description reads "part of
    OpenGov". ACTIVE's is activenetwork.com. Aladtec's is tcpsoftware.com.
    Ident-A-Kid's is centegix.com. SRS Computing's is tributetech.com. Every
    one of them showed ZERO postings, because build_board already refuses to
    count a shared board twice - so a visitor saw "Yes, hiring an AE" with
    nothing to click and no way to check the claim.

    A page scan is the weakest evidence this project accepts: it proves some
    AE-ish words appeared somewhere on a page. Those words on the PARENT's page
    say nothing about the subsidiary, and CLAUDE.md rules that reporting a
    parent's requisition as theirs is a false Yes - the thing this repo exists
    to refuse, arrived at from the opposite direction to the usual.

    Downgraded to Unknown and NOT to "None found", because we still have not
    read their board. Saying we found nothing there would be the other false
    claim.

    An ATS host is not a company. greenhouse.io and bamboohr.com are filing
    cabinets, and treating them as foreign domains would delete every real
    board on the site.
    """
    import refresh
    errors = 0
    OTHER = [
        ("https://cartegraph.com", "https://opengov.com/careers/", "OpenGov"),
        ("https://activenetwork.com/x", "https://careers.activenetwork.com", "Active"),
        ("https://aladtec.com", "https://tcpsoftware.com/careers", "TCP"),
    ]
    for site, ref, who in OTHER:
        if site.split("//")[1].split("/")[0].replace("www.", "") in ref:
            continue                       # same domain, not a case
        if not refresh._someone_elses_site(site, ref):
            errors += fail(f"a board on {who}'s domain is not being treated as "
                           f"another company's, so a page scan of their careers "
                           f"page can still produce a Yes for somebody else")
    SAME_OR_ATS = [
        ("https://fotokite.com", "https://fotokite.bamboohr.com/careers"),
        ("https://acme.com", "https://boards.greenhouse.io/acme"),
        ("https://acme.com", "https://acme.com/careers"),
        ("https://acme.com", "https://jobs.lever.co/acme"),
        ("https://acme.com", None),
        ("https://acme.com", "someslug"),
    ]
    for site, ref in SAME_OR_ATS:
        if refresh._someone_elses_site(site, ref):
            errors += fail(f"{ref!r} was treated as another company's site. "
                           f"An ATS host is a filing cabinet, not a rival - "
                           f"this would delete real boards wholesale")
    # and the downgrade must be Unknown, never "None found"
    # Comments stripped first. This tripped on the comment that explains why
    # the downgrade is NOT "None found" - the second time today a check has
    # convicted its own prose. A checker that cannot tell code from writing
    # about the code fails the moment somebody writes the explanation.
    src = re.sub(r"#[^\n]*", "", inspect.getsource(refresh.check_company))
    if "_someone_elses_site" not in src:
        errors += fail("check_company no longer consults _someone_elses_site, "
                       "so a page scan of a parent's page can be reported as "
                       "this company hiring")
    elif '"None found"' in src.split("_someone_elses_site")[1][:400]:
        errors += fail("a board on another company's domain is being recorded "
                       "as 'None found'. We have not read THEIR board; saying "
                       "we found nothing is the opposite false claim")
    return errors



def check_a_board_on_another_members_domain_is_surfaced() -> int:
    """Twenty-two companies pointed at another company's board and no queue asked.

    Cartegraph's board is opengov.com. Dedrone's is axon.com. ResourceX's is
    tylertech.com. Sixteen of the twenty-two appeared in NO queue at all, so
    nobody was ever asked about them - while five were meanwhile telling the
    public site they were hiring an AE, on the strength of a page scan of the
    parent's careers page.

    This is not a guess about a slug, which is what the acquisitions queue was
    built on. It is two records this file already holds pointing at one place,
    and it costs nothing to notice.

    An ATS host must never count. greenhouse.io is a filing cabinet, and
    treating it as a rival's domain would put every real board on the site into
    the acquisitions queue.
    """
    import admin
    errors = 0
    companies = admin.read("companies.json", [])
    board = admin.read("board.json", {})
    rows = admin.QUEUES["acquisitions"](companies, board)
    ids = {r.get("id") for r in rows}

    by_name = {c["name"]: c for c in companies}
    for sub, parent in (("Cartegraph", "OpenGov"), ("ResourceX", "Tyler Technologies")):
        c = by_name.get(sub)
        if not c:
            continue
        ref = (c.get("ats") or {}).get("ref") or ""
        if not isinstance(ref, str) or not ref.startswith("http"):
            continue          # somebody re-wired it; the case no longer applies
        if c["id"] not in ids:
            errors += fail(f"{sub}'s board is on {parent}'s domain and the "
                           f"acquisitions queue does not raise it, so nobody is "
                           f"asked whether those postings are theirs")

    # No company on a normal ATS may be dragged in. This holds today for a
    # second reason as well as the ATS_HOSTS list: the owner lookup only
    # matches a company's own WEBSITE, and no company's website is
    # greenhouse.io. Removing ATS_HOSTS therefore changes nothing right now,
    # which I found by mutating it and watching this check pass - so the list
    # is defence in depth rather than the thing doing the work, and saying
    # otherwise here would overstate what is tested.
    #
    # It still earns its place: the day somebody records a recruiting vendor as
    # a company, ATS_HOSTS is what stops every board on the site becoming an
    # acquisition suspect.
    dragged = []
    for r in rows:
        if "own domain" not in (r.get("note") or ""):
            continue
        ref = ((r.get("ats") or {}).get("ref") or "")
        if any(h in ref for h in ("greenhouse.io", "lever.co", "bamboohr.com",
                                  "ashbyhq.com", "myworkdayjobs.com")):
            dragged.append(r.get("id"))
    if dragged:
        errors += fail(f"{len(dragged)} companies on a normal ATS were flagged "
                       f"as sitting on another company's domain "
                       f"(e.g. {dragged[0]}). An ATS host is not a rival")
    missing = [h for h in ("greenhouse.io", "lever.co", "bamboohr.com",
                           "ashbyhq.com", "myworkdayjobs.com")
               if h not in admin.ATS_HOSTS]
    if missing:
        errors += fail(f"ATS_HOSTS no longer lists {missing}. The day a "
                       f"recruiting vendor is recorded as a company, that list "
                       f"is the only thing stopping every board on the site "
                       f"becoming an acquisition suspect")
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
    # THE SUITE MUST NOT WRITE TO WHAT IT CHECKS. Two checks stub write_atomic
    # to keep their probes off disk, and journal.record writes through its own
    # io - so both of them appended a real entry to the real journal on every
    # run. Nineteen landed before anyone noticed, each one a "dismiss" ruling
    # attributed to the owner that he never made. Stubbing is per-check and
    # easy to forget; this notices when somebody forgets.
    _journal_before = _journal_fingerprint()

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
    errors += check_checks_can_fail()
    errors += check_decision_files_are_journalled()
    errors += check_crawl_files()
    errors += check_busy_port_does_not_traceback()
    errors += check_structured_data_claims_no_posting_date()
    errors += check_middleware_separates_unreadable_from_gone()
    errors += check_pay_report_states_what_it_omits()
    errors += check_active_badge_is_shipped_honestly()
    errors += check_boards_read_agrees_with_coverage()
    errors += check_active_badge_measures_them_not_us()
    errors += check_sitemap_offers_the_job_pages()
    errors += check_alerts_page_cannot_be_framed()
    errors += check_watchdog_is_independent()
    errors += check_queue_history_is_append_only()
    errors += check_capture_flags_nav_without_dropping_sellers()
    errors += check_worklist_leads_with_evidence()
    errors += check_every_title_extractor_strips_buttons()
    errors += check_queue_rows_carry_what_the_page_renders()
    errors += check_queue_strengths_have_a_band()
    errors += check_structured_matches_the_fetchers()
    errors += check_ats_advice_covers_the_board()
    errors += check_jd_backfill_targets_real_pages()
    errors += check_public_csv_neutralises_formulas()
    errors += check_pay_band_is_not_an_estimate()
    errors += check_shared_board_links_open_the_board()
    errors += check_posts_at_vocabulary()
    errors += check_coverage_and_removed()
    errors += check_weekly_report_is_honest()
    errors += check_map_says_what_it_omits()
    errors += check_prerendered_pages()
    errors += check_feeds_and_structured_data()
    errors += check_share_cards()
    errors += check_semantic_map()
    errors += check_publish_gate_legs()
    errors += check_calendar_dates_survive_the_round_trip()
    errors += check_acquired_names_still_match_themselves()
    errors += check_websites_queue_names_its_twins()
    errors += check_headline_counts_openings()
    errors += check_admin_blurbs_have_no_typed_counts()
    errors += check_queues_do_not_propose_deleted_categories()
    errors += check_a_count_is_never_a_job_title()
    errors += check_a_card_link_yields_the_title_not_the_whole_card()
    errors += check_refresh_renders_a_shell_before_saying_unknown()
    errors += check_a_page_scan_of_the_parents_site_is_not_evidence()
    errors += check_a_board_on_another_members_domain_is_surfaced()
    errors += check_coming_soon_is_not_a_parked_domain()
    errors += check_a_parent_board_ruling_names_the_parent()
    errors += check_the_owner_can_argue_with_the_logic()
    errors += check_an_unread_board_is_not_a_zero()
    errors += check_the_gate_sees_an_unreadable_cliff()
    errors += check_a_transport_failure_gets_a_second_chance()
    errors += check_a_refusal_is_not_a_search()
    errors += check_beak_is_never_text()
    errors += check_rating_scale()
    errors += check_every_company_says_what_it_sells()
    errors += check_brand()
    errors += check_admin_game()
    errors += check_admin_gates()
    errors += check_admin_guards()
    errors += check_save_needs_read()
    errors += check_role_promotion()
    errors += check_render_rotation()
    errors += check_refresh_render_ration()
    errors += check_review_findings()
    errors += check_redirect_hop()
    errors += check_admin_http()
    errors += check_url_sinks()

    for raw, expected in TITLE_TEXT_CASES:
        got = ats.plain(raw)
        if got != expected:
            errors += fail(f"ats.plain({raw!r}) = {got!r}, expected {expected!r}")
    for raw, expected in CTA_CASES:
        got = ats.strip_cta(raw)
        if got != expected:
            errors += fail(f"ats.strip_cta({raw!r}) = {got!r}, expected {expected!r}")
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
          f"{len(PAGESCAN_CASES)} page-scan, {len(TITLE_TEXT_CASES)} title-text, "
          f"{len(CTA_CASES)} button-label")
    after = _journal_fingerprint()
    if after != _journal_before:
        errors += fail(
            f"the selftest wrote to data/admin_journal.jsonl "
            f"({_journal_before[0]} lines -> {after[0]}). A check that stubs "
            f"write_atomic must stub journal.record too: it writes through its "
            f"own io, so the probe lands in the real journal as a ruling "
            f"nobody made. Remove the entries and stub it.")

    if errors:
        print(f"\n{errors} problem(s) found")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
