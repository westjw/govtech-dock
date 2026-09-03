#!/usr/bin/env python3
"""Offline self-test: validates the data layer and the title classifier without
touching the network. Run after any edit to data/ or scripts/.

  python scripts/selftest.py
"""
from __future__ import annotations

import collections
import contextlib
import csv
import datetime as dt
import html
import io
import inspect
import json
import gzip
import math
import re
import shutil
import tempfile
import threading
import time
import pathlib
import sys
import urllib.parse as up

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


# A CARD IS SEVERAL LINES AND THE EXTRACTOR USED TO PUBLISH THEM AS ONE.
# fetch_html_titles flattened everything inside a job link into a single string,
# filed it as the title, and hard-coded "location": "" - for all 681 rows it
# produced, not a subset. So the board carried "Full Stack Engineer New York, NY
# $120k - 145k" as a job name, with no desk to put on the map and the pay in a
# field salary.py never reads.
#
# Every string below is real. The `flat` column is verbatim from data/board.json
# as published, and the `lines` column is what the live markup actually renders
# as, block by block. Invented inputs would let a pattern be tuned to a shape no
# board uses.
#
# THE LAST THREE ARE THE POINT OF THE TABLE. Territory in a title is legitimate
# and roles.geography() deliberately keeps it separate from the office, so
# mark43's "(CO, NM, UT)" and peregrine's ", California" must come back exactly
# as they went in. And ease-health renders a whole card inside one block, where
# there is no line boundary to read: that one keeps its long flattened title
# rather than getting a guess. A slightly long title beats a truncated one.
#
# WHAT COUNTS AS A LINE, tested on the markup itself rather than on a list
# somebody typed. CARD_CASES below starts from lines and cannot see this half,
# so a rule about tags needs its own table or it has no test at all - which is
# exactly what happened: disabling the span rule left every check passing.
#
# Each fragment is copied verbatim out of the live page.
CARD_LINE_CASES = [
    # BLOCK TAGS. Doorman's card is three <p>s in three <div>s, which is the
    # whole reason the title, the city and the pay could be told apart.
    ('<p class="framer-text framer-styles-preset-1mhwl21" data-styles-preset='
     '"Hs6m4oJU2"><strong class="framer-text">Full Stack Engineer</strong></p>'
     '</div><div class="framer-m3527p" data-framer-component-type='
     '"RichTextContainer" style="--framer-link-text-color:rgb(0, 153, 255);'
     '--framer-link-text-decoration:underline;opacity:0.7;transform:none">'
     '<p class="framer-text framer-styles-preset-1mhwl21" data-styles-preset='
     '"Hs6m4oJU2">New York, NY</p>',
     ["Full Stack Engineer", "New York, NY"]),
    # THE MARKUP'S OWN NEWLINES, which is how a formatted page has always come
    # apart here. Dossier writes its location and employment type as sibling
    # spans on separate source lines, and they arrive as separate lines.
    ('<span>Cork, Ireland</span>\n                <span>Full Time</span>',
     ["Cork, Ireland", "Full Time"]),
    # THE SAME STACK, MINIFIED. ease-health emits the identical structure with
    # no newline and no space between the tags, and without the span rule the
    # three chips arrive as one string - which is how eight postings came to be
    # called "Engineering Software Engineer Remote, U.S. - Full-time".
    ('<span class="text-sm uppercase tracking-wide text-ease-forest/70">'
     'Engineering</span><span class="font-serif text-2xl text-ease-forest '
     'md:text-3xl">Software Engineer</span><span class="text-base '
     'text-ease-forest/80">Remote, U.S.<!-- --> \u00b7 <!-- -->Full-time</span>',
     ["Engineering", "Software Engineer", "Remote, U.S. \u00b7 Full-time"]),
    # AND A PLAIN SPACE IS STILL A SPACE. No board in the corpus writes a title
    # this way, so this case is the guard's only evidence and is honest about
    # that: it is here because splitting "Senior Engineer" into "Senior" and
    # "Engineer" would rename the job, not because the data has done it.
    ("<span>Senior</span> <span>Engineer</span>", ["Senior Engineer"]),
]


# (lines, flattened, title, location, pay raw or None)
CARD_CASES = [
    (["Full Stack Engineer", "New York, NY", "$120k - 145k", "Apply"],
     "Full Stack Engineer New York, NY $120k - 145k",
     "Full Stack Engineer", "New York, NY", "$120k - 145k"),
    (["Network Engineer, Axon 911", "New York, New York, United States"],
     "Network Engineer, Axon 911 New York, New York, United States",
     "Network Engineer, Axon 911", "New York, New York, United States", None),
    # a DEPARTMENT above the title. Taking line one here would name eleven
    # Dossier postings "Development & Product Management" - which _TITLEISH
    # rejects, so the fix would have deleted the roles rather than renamed them.
    (["Development & Product Management", "Software Architect",
      "Limerick, Ireland", "Full Time"],
     "Development & Product Management Software Architect Limerick, Ireland Full Time",
     "Software Architect", "Limerick, Ireland", None),
    # "Sales" is a department, "Full Time" is an employment type, "Apply now" is
    # a button: none of the three may be read as the desk.
    (["Business Development Manager (Remote)", "Sales", "Sydney, Australia",
      "Apply now"],
     "Business Development Manager (Remote) Sales Sydney, Australia",
     "Business Development Manager (Remote)", "Sydney, Australia", None),
    # SAMSARA HAD THE TITLE RIGHT AND THE DESK MISSING. Its cards give the title
    # its own heading, so the old code got the name right and then threw the
    # next line away - 120 postings whose location field said "Remote - US" and
    # arrived empty.
    (["Sr. Manager, Business Operations", "Remote - US"],
     "Sr. Manager, Business Operations",
     "Sr. Manager, Business Operations", "Remote - US", None),
    # SPANS STACKED WITH NO SPACE BETWEEN THEM. ease-health builds its card out
    # of four touching <span>s rather than blocks, so the department, the title,
    # the location and the employment type arrived as one name. The department
    # is skipped for the same reason Dossier's is - "Engineering" carries no job
    # word, and _TITLEISH would have rejected it as a title anyway.
    (["Engineering", "Software Development Engineer in Test",
      "Remote, U.S. \u00b7 Full-time", "View role"],
     "Engineering Software Development Engineer in Test Remote, U.S. \u00b7 "
     "Full-time View role",
     "Software Development Engineer in Test", "Remote, U.S. \u00b7 Full-time", None),
    # THREE LINES OF FURNITURE AHEAD OF THE TITLE, and the job word is what
    # walks past all three. "Full-time" and the separator are not places and are
    # not taken; "Remote" is, and it is the only line above the title that says
    # anything about where the job sits.
    (["Full-time", "\u00b7", "Remote", "Enterprise Account Executive",
      "View role"],
     "Full-time \u00b7 Remote Enterprise Account Executive View role",
     "Enterprise Account Executive", "Remote", None),
    # one block, nothing to split on: unchanged, not guessed at. A slightly long
    # title beats a truncated one.
    (["Application Engineer Tokyo, Japan",
      "Engineering \u00b7 Full-time \u00b7 Entry-level"],
     "Application Engineer Tokyo, Japan Engineering \u00b7 Full-time \u00b7 Entry-level",
     "Application Engineer Tokyo, Japan", "", None),
    # THE PLACE ABOVE THE ROLE. ZeroEyes and Leo Technologies stack the location
    # chip first, so the lines after the title hold nothing and the field stayed
    # empty. It is read now - but only after the lines below have come up empty,
    # and only ever into the location. The title is settled before this runs, by
    # position and by carrying a job word, so the failure CLAUDE.md records here
    # cannot recur: what went wrong there was letting the location pattern pick
    # WHICH LINE THE TITLE WAS, and "Database Administrator, Infrastructure -
    # UK" came back as a job called Manchester.
    #
    # This row keeps its title and gains "Remote / Hybrid / Conshohocken, PA".
    # Note what geography() then does with it: REMOTE_RE reads the line as
    # eligibility, so the answer is a remote posting and NOT a desk in
    # Conshohocken. Recovering the string and claiming an office are two
    # different things and only the first one happens here.
    (["Remote / Hybrid / Conshohocken, PA",
      "Principal Engineer, DevOps & Infrastructure", "Apply"],
     "Principal Engineer, DevOps & Infrastructure",
     "Principal Engineer, DevOps & Infrastructure",
     "Remote / Hybrid / Conshohocken, PA", None),

    # TERRITORY IN A TITLE IS NOT A DEFECT AND MUST NOT BE "FIXED"
    (["Strategic Account Executive (CO, NM, UT)"],
     "Strategic Account Executive (CO, NM, UT)",
     "Strategic Account Executive (CO, NM, UT)", "", None),
    (["Strategic Growth Account Executive, California"],
     "Strategic Growth Account Executive, California",
     "Strategic Growth Account Executive, California", "", None),
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



def check_rival_door_refuses_a_category() -> int:
    """The competitor door must refuse a category wearing a better word.

    THE BUG THIS REPLACES IS ON THE PUBLIC PAGE RIGHT NOW. The company rail
    reads "Others in Police" and lists Verkada, Palantir, Peregrine, Robin
    Radar and Brinc by open-role count: cameras, a data platform, data
    integration, drone detection, and drones. Five companies, no two of which
    compete. A category is the room they are standing in; a competitor is
    someone a buyer would put on the same shortlist.

    An agent asked "who competes with Verkada" and handed a 132-company roster
    will, if it wants to be helpful, hand most of it back - which is the same
    rail with a new heading. So the door caps a shortlist, and the cap is the
    guard. Everything else here refuses a claim that cannot be checked: a name
    that was never on the roster is the agent answering from memory, and an
    edge with no reason is a listing again.

    An empty answer must survive, because "nobody on this roster competes with
    them" is true of a great many of these companies and is the honest thing
    to publish. It must be ASSERTED though: an empty list that did not set
    none_found is a field that failed to fill, and the two are different
    findings wearing one shape.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import agents

    roster = [{"id": f"c{i}", "name": f"C{i}"} for i in range(33)]

    def prop(**kw):
        d = {"id": "c0", "roster": roster, "confidence": "medium",
             "rivals": [{"id": "c1", "why": "both sell RMS to the same police buyer"}]}
        d.update(kw)
        return d

    ok_cases = [
        ("a two-name shortlist", prop()),
        ("an asserted empty answer", prop(rivals=[], none_found=True)),
        ("high confidence with evidence",
         prop(confidence="high", evidence="named together in one RFP shortlist")),
    ]
    # A SMALL ROSTER, so the proportion cap binds BEFORE the absolute one.
    # The first version of this check used a 33-company roster throughout, on
    # which max(2, 33//3) is 11 and EDGE_CAP of 8 always fired first - so the
    # proportion rule was never exercised and deleting it changed nothing here.
    small = [{"id": f"s{i}", "name": f"S{i}"} for i in range(9)]

    refuse_cases = [
        ("four rivals out of a nine-company category",
         {"id": "s0", "roster": small, "confidence": "medium",
          "rivals": [{"id": f"s{i}", "why": "a reason long enough to pass"}
                     for i in range(1, 5)]}),
        ("most of the category restated",
         prop(rivals=[{"id": f"c{i}", "why": "also sells to police departments"}
                      for i in range(1, 20)])),
        ("one over the shortlist cap",
         prop(rivals=[{"id": f"c{i}", "why": "a reason long enough to pass"}
                      for i in range(1, 10)])),
        ("a company that was never on the roster",
         prop(rivals=[{"id": "axon", "why": "they both sell body cameras"}])),
        ("itself as a competitor",
         prop(rivals=[{"id": "c0", "why": "a reason long enough to pass"}])),
        ("an edge carrying no reason", prop(rivals=[{"id": "c1", "why": "same"}])),
        ("the same rival twice",
         prop(rivals=[{"id": "c1", "why": "both sell RMS to police"}] * 2)),
        ("an empty list nobody asserted", prop(rivals=[])),
        ("no rivals field at all", prop(rivals=None)),
        ("high confidence resting on nothing", prop(confidence="high")),
    ]

    errors = 0
    for name, pr in ok_cases:
        got = agents.check_rival(pr)
        if got is not None:
            errors += fail(f"the rival door refused {name}: {got}")
    for name, pr in refuse_cases:
        if agents.check_rival(pr) is None:
            errors += fail(f"the rival door ACCEPTED {name}. That is the "
                           f"'Others in Police' rail arriving through a door "
                           f"built to stop it")
    return errors


def check_every_queue_has_a_renderer() -> int:
    """A queue the admin lists must be a queue the admin can draw.

    admin.html builds one tab per key in META.labels and dispatches clicks
    through RENDER[key](it). For a month two of the seventeen - Agent
    proposals (131 rows) and Warm leads (72) - had a tab, a count, and no
    RENDER entry, so clicking either threw TypeError inside the forEach and
    203 rows were unreachable from the only UI that holds thirteen of the
    queues. queue_stats never saw it: the crash was client-side. This is the
    guard that was missing, and it reads the source because the failure is a
    missing assignment that no request ever exercises.
    """
    import re
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin
    html = (ROOT / "admin.html").read_text()
    drawn = set(re.findall(r"^RENDER\.([a-z_]+)\s*=", html, re.M))
    errors = 0
    for key in admin.QUEUES:
        if key not in drawn:
            errors += fail(f"admin.QUEUES lists {key!r} and admin.html has no "
                           f"RENDER.{key}. The tab renders with a count and "
                           f"throws on click; every row in it is unreachable")
    for key in drawn - set(admin.QUEUES):
        note(f"admin.html draws RENDER.{key}, which is not a queue (fine if it "
             f"is a sub-view)")
    return errors


def check_proposal_rulings_cover_every_kind() -> int:
    """Every kind in agents.KINDS either lands through the one door or refuses
    by name - and never raises inside a request handler.

    The spine's other end. agents.KINDS declares kinds ahead of their appliers
    so the queue can show them; proposal_rulings.rule must therefore answer
    for EVERY kind, including the ones with nothing behind them, with an
    explicit refusal rather than a KeyError that takes the whole action down.
    Executed in a sandbox with the store, the journal and the companies file
    all pointed at a temp dir - a check that rules on a real proposal is the
    accident this repo spent a night recovering from.
    """
    import json as _json
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin, agents, proposal_rulings

    companies = [{"id": "acme", "name": "Acme", "sector": "Public Safety",
                  "category": "Police", "description": "CAD for police",
                  "website": "https://acme.example", "ats": {"type": "unknown", "ref": None},
                  "hiring": "Unknown", "govtech": True, "vendor_type": "product"}]
    store = {}
    for k in agents.KINDS:
        store[f"{k}:acme"] = {"kind": k, "id": "acme", "name": "Acme",
                              "status": "pending", "confidence": "medium",
                              "why": "fixture", "evidence": "https://acme.example/careers",
                              "postings": [] , "none_found": True,
                              "rivals": [], "sector": "Public Safety", "category": "Police"}
    store["read:acme"]["evidence"] = "https://other.example/jobs"   # off-domain
    # A KIND NOBODY DECLARED. The dispatcher must refuse it in words, not
    # raise: a request handler that raises is a tab that crashes, which is
    # the exact defect this queue had.
    store["bogus:acme"] = dict(store["board:acme"], kind="bogus")
    files = {"companies.json": companies, "agent_proposals.json": store,
             "manual.json": {"checks": {}, "postings": []},
             "admin_dismissed.json": {}, "placement_rulings.json": {}}
    errors = 0
    with _sandbox_admin(files) as tmp:
        keep = agents.STORE
        agents.STORE = tmp / "agent_proposals.json"
        try:
            st = agents.load()
            # 1. no author, no ruling. Asserted on the REJECT path, which has
            # no other gate: the first version asserted it on an off-domain
            # read, which is refused for a different reason, so a dispatcher
            # that quietly defaulted `by` to "owner" walked past.
            r = proposal_rulings.rule(st, "board:acme", False, why="x", by="")
            if not r.get("error") or "author" not in r["error"]:
                errors += fail(f"a reject with no author was not refused for "
                               f"want of one: {r}. `by` defaulting to owner is "
                               f"the attribution trap CLAUDE.md records 86 "
                               f"writes paying for")
            if agents.load()["board:acme"].get("status") != "pending":
                errors += fail("a reject with no author still changed the row")
            # 1b. an undeclared kind refuses in words
            r = proposal_rulings.rule(agents.load(), "bogus:acme", True,
                                      why="x", by="owner")
            if not r.get("error") or "bogus" not in r["error"]:
                errors += fail(f"an undeclared kind was not refused by name: {r}")
            # 2. every kind answers; the unbuilt ones refuse BY NAME
            for k in agents.KINDS:
                try:
                    r = proposal_rulings.rule(agents.load(), f"{k}:acme", True,
                                              why="fixture", by="owner")
                except Exception as exc:
                    errors += fail(f"accepting a {k} proposal RAISED "
                                   f"{type(exc).__name__}: {exc}. A request "
                                   f"handler that raises is a tab that crashes")
                    continue
                if k in proposal_rulings.NO_APPLIER:
                    # THE refusal, not any refusal. "unknown proposal kind
                    # 'profile'" also contains the word profile; the first
                    # version accepted it and a dispatcher that dropped the
                    # no-applier branch walked past.
                    if not r.get("error") or "no applier" not in r["error"] \
                            or k not in r["error"]:
                        errors += fail(f"a {k} proposal was not refused as "
                                       f"'no applier for a {k}'; got {r}. A "
                                       f"kind with no applier must say so, "
                                       f"not pretend to land or fall through")
                elif k == "read":
                    # off-domain read refuses without force, lands with it
                    if not r.get("error"):
                        errors += fail("an off-domain read was accepted without "
                                       "force. That files a parent's "
                                       "requisitions under a subsidiary")
                    r2 = proposal_rulings.rule(agents.load(), "read:acme", True,
                                               why="fixture", by="owner", force=True)
                    if r2.get("error"):
                        errors += fail(f"a forced none_found read did not close: {r2}")
                    st2 = agents.load()
                    if st2["read:acme"].get("status") != "accepted" \
                            or st2["read:acme"].get("ruled_by") != "owner":
                        errors += fail("an accepted read was not stamped with "
                                       "status and author in the store")
            # 3. a reject stamps and keeps the row
            r = proposal_rulings.rule(agents.load(), "board:acme", False,
                                      why="not theirs", by="owner")
            st3 = agents.load()
            if r.get("error") or st3["board:acme"].get("status") != "rejected" \
                    or st3["board:acme"].get("ruled_why") != "not theirs":
                errors += fail(f"a reject did not stamp the row: {r} / "
                               f"{st3['board:acme']}")
            if "board:acme" not in st3:
                errors += fail("a rejected proposal was deleted. Rejecting "
                               "records; it never removes")
            # 4. the store write was journalled - THIS ruling's entry, found by
            # its action. The first version looked for the filename anywhere
            # in the log, and the rival path's own save had already put it
            # there, so a dispatcher writing the store directly walked past.
            log = tmp / "admin_journal.jsonl"
            acts = []
            if log.exists():
                for line in log.read_text().splitlines():
                    try:
                        acts.append(_json.loads(line).get("action"))
                    except ValueError:
                        pass
            if "proposal-reject" not in acts or "proposal-accept" not in acts:
                errors += fail(f"proposal rulings were not journalled under "
                               f"their own action (saw {sorted(set(acts))}). "
                               f"admin_undo cannot take one back")
            # 5. the action is registered and goes through the same door
            if admin.ACTIONS.get("proposal-ruling") is not admin.act_proposal_ruling:
                errors += fail("ACTIONS has no proposal-ruling entry; the queue's "
                               "buttons post into nothing")
        finally:
            agents.STORE = keep
    return errors


def check_ingest_keeps_refusals() -> int:
    """What the door refuses is KEPT, with the rule that refused it.

    The owner's ruling on write-ups is "door only, add some gate reviews",
    and the gate review's first list is every proposal the door refused, by
    rule, so a rule that is too tight is visible. A refusal that vanished at
    intake could never be reviewed, and a door nobody can see being wrong is
    a door nobody fixes. Asserted in a sandbox against a rival proposal that
    the existing door refuses (a name not on the roster), then that the
    admin queue does NOT show it as pending and the profile gate DOES list
    it as refused.
    """
    import json as _json
    sys.path.insert(0, str(ROOT / "scripts"))
    import admin, agents, promote_profiles
    roster = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    bad_prop = {"id": "a", "name": "A", "roster": roster, "confidence": "medium",
                "rivals": [{"id": "not-on-roster", "why": "a reason long enough to pass"}]}
    companies = [{"id": "a", "name": "A", "sector": "S", "category": "Police",
                  "description": "d", "ats": {"type": "unknown", "ref": None},
                  "hiring": "Unknown", "govtech": True, "vendor_type": "product"}]
    errors = 0
    with _sandbox_admin({"companies.json": companies, "agent_proposals.json": {},
                         "admin_dismissed.json": {}}) as tmp:
        keep = agents.STORE
        agents.STORE = tmp / "agent_proposals.json"
        try:
            rep = agents.ingest("rival", [bad_prop], model="agent:test")
            st = agents.load()
            row = st.get("rival:a")
            if rep["kept"] != 0 or not rep["refused"]:
                errors += fail(f"the door did not refuse the fixture: {rep}")
            if not row or row.get("status") != "refused" or not row.get("refused_why"):
                errors += fail(f"a refused proposal was not kept in the store "
                               f"with its reason: {row}. The gate review has "
                               f"nothing to show")
            if row and "not on the roster" not in row.get("refused_why", ""):
                errors += fail("the kept refusal does not carry the door's own "
                               "sentence, so a person cannot tell which rule fired")
            # the admin queue shows PENDING rows only
            shown = admin.q_proposals(companies, {"organizations": []})
            if any(r.get("id") == "a" for r in shown):
                errors += fail("a refused proposal appeared in the admin's "
                               "pending queue")
            # the profile gate lists a refused profile row under its category
            st["profile:a"] = {"kind": "profile", "id": "a", "name": "A",
                               "status": "refused", "refused_why": "4. quote not on page",
                               "confidence": "high"}
            g = promote_profiles._by_category(st, companies)
            if not any(k == "profile:a" for k, _ in g.get("Police", [])):
                errors += fail("promote_profiles does not list a refused write-up "
                               "under its category, so --gate cannot show it")
        finally:
            agents.STORE = keep
    return errors


def check_company_page_profile_states() -> int:
    """coAbout renders the new profile shape, and NEVER the legacy one.

    Two companies (granicus, everdriven) carry a `profile` whose text is a
    reviewer's working notes: "their Greenhouse board carried 12 postings...
    the SLED sales roles this board exists to list". Under the company's own
    name on a public page that reads as the company describing itself. So the
    renderer keys on the SHAPE - `paragraphs`, with provenance - and the
    legacy shape gets the stub, and this check hands it the legacy text and
    asserts the text does not come out. Executed under node, the way coPhase
    is, because a template branch cannot be driven and this is the one line
    on the page most worth driving.
    """
    import shutil, subprocess, json as _json
    html = (ROOT / "index.html").read_text()
    ca = html.find("function co(id,fromUrl){")
    cb = html.find("function toggleSaveCompany(")
    body = html[ca:cb] if ca > 0 and cb > ca else ""
    errors = 0
    if "coAbout(" not in body:
        errors += fail("co() no longer calls coAbout, so the About section is "
                       "being written inline again and the legacy-shape guard "
                       "does not apply to it")
    if not shutil.which("node"):
        note("node not installed; coAbout was not executed this run")
        return errors

    def slice_fn(name):
        # to the function's OWN closing brace at column 0, not to the next
        # `function` keyword: the first version swallowed 4KB of unrelated
        # module state after safeUrl, which happened to parse and would not
        # have the day someone put a `let` there that needed the DOM.
        i = html.find(f"function {name}(")
        j = html.find("\n}\n", i + 1)
        return html[i:j + 2] if i >= 0 and j > i else ""

    def slice_const(name):
        # `esc` is a const arrow, not a declaration, and the first version of
        # this slicer looked only for `function esc(` - so coAbout threw
        # ReferenceError under node on correct code. A statement ends at the
        # first `;` followed by a newline after its start.
        i = html.find(f"const {name}=")
        if i < 0:
            return ""
        j = html.find(";\n", i)
        return html[i:j + 1] if j > i else ""
    src = "\n".join([slice_const("esc"), slice_fn("safeUrl"), slice_fn("coAbout")])
    if "function coAbout(" not in src:
        return errors + fail("index.html: coAbout is gone")

    new = {"paragraphs": ["Brinc builds drones for police departments.",
                          "The Lemur opens locked doors."],
           "quote": {"text": "built for public safety", "url": "https://b.example/about"},
           "sources": [{"url": "https://b.example/", "fetched_on": "2026-09-04"},
                       {"url": "https://b.example/about", "fetched_on": "2026-09-04"}],
           "paragraph_sources": [["https://b.example/"], ["https://b.example/about"]],
           "written_on": "2026-09-05", "by_kind": "site"}
    legacy = {"description": "INTERNAL their Greenhouse board carried 12 postings",
              "sources": ["https://g.example"]}
    # THE PAGE RUNS IN A BROWSER, so it has `location`; node does not, and
    # safeUrl resolves every href against location.href. Without the stub
    # every URL came back "" and the check failed on correct code - the
    # exact lesson the .ics harness above already carries.
    script = """
const location = {href: "https://sledjobs.com/"};
%s
const base = {description: "One line.", website: "https://b.example", researched: true};
const out = {
  site:    coAbout({...base, profile: %s}, "b.example"),
  claimed: coAbout({...base, profile: {...%s, by_kind: "company"}}, "b.example"),
  none:    coAbout(base, "b.example"),
  legacy:  coAbout({...base, profile: %s}, "g.example"),
};
console.log(JSON.stringify(out));
""" % (src, _json.dumps(new), _json.dumps(new), _json.dumps(legacy))
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return errors + fail(f"coAbout threw under node: {r.stderr.strip()[:200]}")
    got = _json.loads(r.stdout)
    s = got["site"]
    for want in ("Brinc builds drones", "Lemur opens", "built for public safety",
                 "https://b.example/about", "written from their site",
                 "Every sentence traces"):
        if want not in s:
            errors += fail(f"coAbout with a full profile does not render {want!r}")
    if "<sup>1</sup>" not in s or "<sup>2</sup>" not in s:
        errors += fail("paragraphs do not carry their source numbers; a reader "
                       "cannot check a sentence against the page it came from")
    if "in their own words" not in got["claimed"]:
        errors += fail("a claimed-company profile is not marked as the company's "
                       "own words")
    if "not on file for this company yet" not in got["none"]:
        errors += fail("a company with no profile does not get the honest stub")
    if "INTERNAL" in got["legacy"] or "Greenhouse board carried" in got["legacy"]:
        errors += fail("coAbout rendered a LEGACY profile's internal notes on the "
                       "public page. The renderer must key on the paragraphs "
                       "shape, never on the presence of a `profile` key")
    if "not on file for this company yet" not in got["legacy"]:
        errors += fail("a legacy-shape profile did not fall back to the stub")
    return errors


def check_dechrome_keeps_sentences_and_drops_chrome() -> int:
    """The brief a judge writes from must be the company's sentences, not its
    menu - and must not lose the sentences while dropping the menu.

    Measured on a 25-site sample before this shipped (median 64% of text
    kept), and two of the sample's failures are the cases here. A site whose
    homepage and /about/ were the same document kept 6%: every line
    "repeated" and the repeat rule ate the whole site. And a cookie banner on
    a one-page site survived, because it is three words or more and on one
    page nothing repeats. Each case is one rule; deleting a rule turns one
    case red.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_profiles as fp

    body = ("We use analytics cookies to understand how visitors use this site.\n"
            "Products\n"
            "Brinc builds drones that police departments stage on station rooftops.\n"
            "Request a Demo\n"
            "The company was founded in 2017 and is based in Seattle.\n")
    home = {"url": "https://x/", "text": body}
    about = {"url": "https://x/about/", "text": body}      # the same document
    other = {"url": "https://x/products/",
             "text": "Products\nRequest a Demo\nThe Lemur drone opens locked doors.\n"
                     "Brinc builds drones that police departments stage on station rooftops.\n"}
    errors = 0

    one = fp.dechrome([home])[0]["text"]
    if "Brinc builds drones" not in one or "founded in 2017" not in one:
        errors += fail("dechrome dropped a real sentence from a one-page site")
    if "analytics cookies" in one:
        errors += fail("dechrome kept a cookie banner. On a one-page site "
                       "nothing repeats, so the repeat rule cannot catch it; "
                       "the anchored chrome pattern has to")
    if "Products" in one.split("\n"):
        errors += fail("dechrome kept a one-word menu label as a line")
    # "Request a Demo" is THREE words and SURVIVES on a one-page site, by
    # design: the rule is "under three words", and a three-word line is a
    # sentence often enough that dropping it costs real text. What catches a
    # three-word CTA is the repeat rule, once a second page carries it - the
    # case asserted below. The first version of this check asserted the CTA
    # was dropped here and failed on correct code.
    if "Request a Demo" not in one.split("\n"):
        note("dechrome dropped a three-word line on a one-page site; the rule "
             "says under three words, so this is stricter than documented")

    dup = fp.dechrome([home, about])
    if len(dup) != 1:
        errors += fail(f"two copies of one document became {len(dup)} pages. "
                       f"status-solutions-network kept 6% of its text this "
                       f"way: every line repeated, so every line was chrome")
    if "Brinc builds drones" not in dup[0]["text"]:
        errors += fail("the same page under two urls lost its own sentences")

    two = fp.dechrome([home, other])
    texts = [pg["text"] for pg in two]
    if any("Brinc builds drones" in tx for tx in texts):
        errors += fail("a sentence repeated verbatim on two DIFFERENT pages "
                       "was kept. That is the shape of a tagline in the "
                       "footer, and a brief that leads with it writes "
                       "marketing")
    if any("Request a Demo" in tx.split("\n") for tx in texts):
        errors += fail("a three-word CTA repeated on two pages was kept. The "
                       "repeat rule is what catches chrome the length rule "
                       "cannot")
    if not any("Lemur drone" in tx for tx in texts):
        errors += fail("dechrome dropped a sentence that appears on one page only")
    if not any("founded in 2017" in tx for tx in texts):
        errors += fail("dechrome dropped a one-page sentence when a second page was present")
    return errors


def check_site_pages_stay_out_of_git() -> int:
    """Other people's page text never reaches the repository.

    fetch_profiles.py reads what a company's own site says about it and
    keeps the text so a later claim can be checked against the bytes. The
    first version kept all of it in one committed JSON, which at 2,024
    companies is ~100MB of somebody else's words in the history of a repo
    that is going public - the exact thing .gitignore already refuses for
    http_cache. Three things hold the line now, and each is asserted: the
    bodies directory is ignored, the old single file is gone from the tree,
    and the committed index carries which pages were read and their shas
    but never a character of what they said.
    """
    import subprocess
    errors = 0
    ignore = (ROOT / ".gitignore").read_text()
    for path in ("/data/site_pages/", "/data/briefs/", "/data/proposals_in/"):
        if path not in ignore:
            errors += fail(f".gitignore no longer lists {path}. That directory "
                           f"holds other people's page text (or briefs made "
                           f"from it), and one commit puts 100MB of it in the "
                           f"public history for good")
    tracked = subprocess.run(["git", "ls-files", "data/site_pages.json",
                              "data/site_pages"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    if tracked:
        errors += fail(f"page text is tracked by git: {tracked[:3]}. The "
                       f"bodies live in data/site_pages/ and only the index "
                       f"is committed")
    idx = ROOT / "data" / "site_pages_index.json"
    if idx.exists():
        raw = idx.read_text()
        import json as _json
        d = _json.loads(raw)
        for cid, e in list(d.items())[:50]:
            for bucket in ("about", "news"):
                for pg in e.get(bucket) or []:
                    if "text" in pg or "html" in pg:
                        errors += fail(f"site_pages_index.json carries page "
                                       f"text for {cid}. The index is a list "
                                       f"of what was read, never what it said")
                        break
        # the guard has to read the real shape, not a hopeful one
        sys.path.insert(0, str(ROOT / "scripts"))
        import fetch_profiles as fp
        probe = fp.index_entry({"id": "x", "fetched_on": "2026-01-01",
                                "about": [{"url": "https://x/", "text": "SECRET",
                                           "chars": 6, "sha": "abc"}], "news": []})
        if "SECRET" in _json.dumps(probe):
            errors += fail("fetch_profiles.index_entry leaks page text into the "
                           "committed index")
    return errors


def check_profile_fetch_stays_first_party() -> int:
    """Site pages must come from the company's own domain, and nowhere else.

    THE ENGINES READING THIS STORE ARE TOLD EVERY URL IS FIRST-PARTY, and two
    of them publish what they find on 2,058 public company pages. A company's
    press page routinely links out to the trade outlet that covered them; one
    followed link and a magazine's words are filed as the company's own
    description of itself, under that company's name, with a citation that
    looks first-party because the store said it was.

    The dedupe is here for a smaller reason that costs somebody else money:
    www.brincdrones.com/about/ and brincdrones.com/about/ are one page, and
    the first pass fetched both - a wasted request at their expense, and the
    same words stored twice, which would later read as two sources for one
    claim.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_profiles as fp

    body = """
      <a href="/about">about</a>
      <a href="https://www.acme.com/customers/">customers</a>
      <a href="https://acme.com/about/">about again, other spelling</a>
      <a href="https://press.acme.com/news">own subdomain</a>
      <a href="https://govtechtoday.example/acme-raises-50m">the outlet</a>
      <a href="https://linkedin.com/company/acme">their profile elsewhere</a>
      <a href="mailto:hi@acme.com">mail</a>
      <a href="javascript:void(0)">script</a>
    """
    got = fp.links(body, "https://www.acme.com")
    hosts = {up.urlsplit(u).hostname for u in got}
    errors = 0
    for bad in ("govtechtoday.example", "linkedin.com"):
        if bad in hosts:
            errors += fail(f"fetch_profiles followed a link to {bad}. The store "
                           f"promises first-party text; one outside page and "
                           f"somebody else's words are published as a "
                           f"company's own account of itself")
    if "press.acme.com" not in hosts:
        errors += fail("fetch_profiles dropped the company's own subdomain. "
                       "press.acme.com is acme.com, and it is usually where "
                       "the news actually lives")
    paths = [up.urlsplit(u).path.rstrip("/").lower() for u in got]
    if paths.count("/about") > 1:
        errors += fail("fetch_profiles kept www and non-www as two pages. One "
                       "page fetched twice spends somebody else's server and "
                       "stores one claim as two sources")
    for scheme_bad in got:
        if scheme_bad.lower().startswith(("mailto:", "javascript:")):
            errors += fail(f"fetch_profiles returned {scheme_bad!r} as a page")
    return errors


def check_rival_brief_never_cuts_the_roster() -> int:
    """A sliced assignment must still carry every candidate.

    Police holds 132 companies and no agent judges 132 at once, so the work is
    sliced. THE ROSTER IS NOT. An agent shown half the category cannot propose
    the edge that crosses the cut, and that missing edge is invisible forever -
    the false absence this project refuses everywhere else, from `scan_pagetext`
    to the near-a-city filter. Slice the assignment, never the candidates.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import agents

    briefs = agents.brief_rival(sector="Public Safety", category="Police")
    if not briefs:
        note("no Police briefs to check (all proposed already?)")
        return 0
    errors = 0
    # MEASURE THE LIST, DO NOT READ THE FIELD. The first version compared
    # roster_size, which brief_rival computes separately from the roster it
    # ships - so a mutation that sliced the actual candidate list left the
    # reported number at 132 and walked straight past. A guard that trusts a
    # count over the thing counted is not checking anything.
    for b in briefs:
        if len(b["roster"]) != b["roster_size"]:
            errors += fail(f"slice {b['key']} reports a roster of "
                           f"{b['roster_size']} and ships {len(b['roster'])}. "
                           f"The number is not the thing")
    sizes = {len(b["roster"]) for b in briefs}
    if len(sizes) != 1:
        errors += fail(f"slices of one category saw different rosters {sizes}. "
                       f"Two agents judging the same category against "
                       f"different candidate sets cannot be compared, and the "
                       f"smaller one cannot find what it was not shown")
    for b in briefs:
        ids = {r["id"] for r in b["roster"]}
        missing = [a["id"] for a in b["assigned"] if a["id"] not in ids]
        if missing:
            errors += fail(f"slice {b['key']} was assigned {missing[:3]} and "
                           f"they are not on its own roster")
        if len(b["assigned"]) > agents.SLICE:
            errors += fail(f"slice {b['key']} carries {len(b['assigned'])} "
                           f"assignments over a slice of {agents.SLICE}")
    covered = [a["id"] for b in briefs for a in b["assigned"]]
    if len(covered) != len(set(covered)):
        errors += fail("a company is assigned to two slices, so it would be "
                       "judged twice and the two answers would disagree on "
                       "one page")
    if len(set(covered)) != briefs[0]["roster_size"]:
        errors += fail(f"{len(set(covered))} companies assigned out of a "
                       f"{briefs[0]['roster_size']}-company category. The ones "
                       f"left out get no shortlist and nothing says why")
    return errors


def check_company_counts_are_roles_not_postings():
    """The company page's counters must count openings, never postings.

    ONE OPENING ADVERTISED IN SIX CITIES IS SIX POSTINGS AND ONE JOB. The stat
    strip's headline is o.open_roles, which the builder dedupes by opening_id.
    The first render of the turn-6 layout put a posting count beside it and
    Motorola Solutions read:

        357  OPEN ROLES
        +393 in the last 30 days

    which is not a rounding difference, it is an impossibility on its face. A
    reader can only conclude one of the two numbers is wrong, and has no way
    to tell which.

    THIS CHECK DRIVES THE CALLER AS WELL AS THE HELPER, because the first
    version of it did not and three of seven mutations walked straight past.
    A perfect coRoles is worth nothing if co() counts the delta inline again,
    and that is the exact shape of the bug this file has now caught four
    separate times: a guard that proves a helper works while the call site
    that uses it quietly stops calling it. So co()'s body must not name
    first_seen at all - every date-based count in it goes through coRoles or
    it does not happen.

    THE FIXTURES ARE BUILT TO SEPARATE THE TWO UNITS, not to look plausible.
    Nine postings over three openings: a posting counter answers 9 and an
    opening counter answers 3. Then a second fixture where the two units
    disagree about the ANSWER and not just the number - five quota postings
    over one quota opening, which reads as a sales floor being built if you
    count postings and as ordinary backfill if you count jobs. The first
    version asserted on a substring and the fixture's own region count
    supplied the digit it was looking for, so the assertions here are exact.

    node is not a project dependency. If it is absent the check says so and
    passes: a missing tool is not a broken board.
    """
    import shutil, subprocess, json as _json

    html = (ROOT / "index.html").read_text()
    errors = 0

    # ---- the caller ---------------------------------------------------
    ca = html.find("function co(id,fromUrl){")
    cb = html.find("function toggleSaveCompany(")
    if ca < 0 or cb < 0 or cb <= ca:
        return fail("index.html: could not find co() to check what it counts")
    import re as _re
    body = html[ca:cb]
    # first_seen may be READ (coAge stamps one posting with its own age); it
    # may not be COUNTED. So every occurrence has to be an argument to coAge,
    # and any counting spelling - a filter, a reduce, a hand-rolled loop -
    # necessarily reads it somewhere else.
    import re as _re
    note_call = _re.search(r"coOpenNote\(([^)]*)\)", body)
    if note_call is not None and note_call.group(1).count(",") < 2:
        errors += fail(
            f"co() calls coOpenNote({note_call.group(1)}) with too few "
            f"arguments. It needs the postings, the count AND whether the "
            f"board could be read; without the last one an unreadable zero "
            f"comes back as 'none seen recently', which files our failed "
            f"fetch as a fact about their hiring")
    # A SELECTOR THAT MATCHES NOTHING FAILS SILENTLY, WHICH IS WHY IT SURVIVED
    # A DESKTOP SCREENSHOT. The stat strip's cells are <dl> elements and every
    # rule for them named div: `.costrip>div` for the padding, `.costrip>div+div`
    # for the separators, and both responsive rules. None of it applied. The
    # strip was held together by inline flex values alone, so it looked right
    # at 1440px and stayed four columns wide at 375px, where each cell gets
    # eighty pixels and one of them carries a sentence. Nothing errors, nothing
    # logs; the page just quietly ignores its own stylesheet.
    # from the OPENING TAG, so the depth counter starts outside the strip and
    # its cells land at depth 1. Slicing from the attribute made every cell
    # look like a top-level element and the check read the wrong tier.
    strip_at = body.find('<div class="costrip"')
    if strip_at < 0:
        errors += fail("index.html: the company page's stat strip is gone")
    else:
        strip = body[strip_at:body.find("`;", strip_at)]
        # DIRECT children, computed, not "does this tag appear anywhere".
        # The first version asked whether <div> was present, and it is - the
        # big number inside each cell is a div - so a rule targeting
        # .costrip>div passed while matching nothing. The whole bug is about
        # the > combinator, so the check has to honour it.
        kids, depth = set(), 0
        for m in _re.finditer(r"<(/?)([a-z0-9]+)[^>]*?(/?)>",
                              _re.sub(r"\$\{[^}]*\}", "", strip)):
            closing, name, selfclose = m.group(1), m.group(2), m.group(3)
            if closing:
                depth -= 1
                continue
            if depth == 1:
                kids.add(name)
            if not selfclose and name not in ("br", "img", "hr", "input"):
                depth += 1
        want = set(_re.findall(r"\.costrip\s*>\s*([a-z]+)", html))
        for el in sorted(want):
            if el not in kids:
                errors += fail(
                    f"the stylesheet has .costrip>{el} rules but the "
                    f"strip's direct children are {sorted(kids) or 'none'}. "
                    f"A selector that matches "
                    f"nothing throws no error and logs nothing - it just "
                    f"drops the padding, the separators and every breakpoint, "
                    f"and the page looks correct on a desktop screenshot "
                    f"while collapsing on a phone")
        # AND THE OTHER DIRECTION. Asking only "is every styled element
        # present" lets one cell change tag and go unstyled while its three
        # siblings keep the selector alive. Every direct child has to be
        # covered by a rule, or the strip has a cell nothing applies to.
        for el in sorted(kids - want):
            errors += fail(
                f"the stat strip has a <{el}> child and no .costrip>{el} "
                f"rule, so that cell gets none of the padding, the "
                f"separator or the breakpoints its siblings get")
        if 'style="flex:' in strip:
            errors += fail(
                "the stat strip carries inline flex values. They outrank the "
                "responsive rules and, worse, they held the layout up while "
                "every .costrip selector was matching nothing - which is how "
                "a stylesheet that applied to no element went unnoticed. "
                "Put the widths in classes")

    # THE FIELDS ARE OBJECTS, AND THE HELPERS THAT KNOW THAT ALREADY EXIST.
    # The company page's role rows shipped reading p.location and p.comp
    # directly: territory is an object, so every Granicus row rendered
    # "[object Object]", and comp is an object whose absence carries WHICH
    # silence it is. locCell and payCell had been getting both right for the
    # jobs table since the beginning. Reinventing a formatter is how a page
    # loses a distinction the rest of the board is careful about.
    rows_at = body.find('class="corow"')
    if rows_at < 0:
        errors += fail("index.html: the company page's role rows are gone")
    else:
        rows = body[rows_at:rows_at + 1200]
        if "locCell(" not in rows:
            errors += fail("the company page's role rows do not call locCell. "
                           "p.territory is an object and renders as "
                           "'[object Object]'; p.location is empty on most "
                           "postings, so a row that reads it directly says "
                           "nothing about where the job is")
        if "payCell(" not in rows:
            errors += fail("the company page's role rows do not call payCell. "
                           "p.comp is an object, and the dash payCell prints "
                           "carries which silence it is in its title - a row "
                           "that prints its own dash throws that away")
    if "coOpenNote(" not in body:
        errors += fail("co() no longer calls coOpenNote, so the open-roles "
                       "evidence line is being written inline again. Every "
                       "rule about what that number may claim lives in "
                       "coOpenNote and is worth nothing if the page stops "
                       "asking it. This repo has now shipped the same shape "
                       "of bug five times: a guard that proves a helper "
                       "correct while the call site quietly drops it")
    # NOT JUST THAT THEY ARE CALLED - THAT THEY ARE TOLD WHETHER THE BOARD
    # WAS READ. coPhase's refusal is driven by that second argument, and a call
    # site that stops passing it leaves the helper perfect and the page wrong,
    # which is this repo's most-repeated bug and the reason both call-site
    # rules here are written against the arguments and not the name.
    call = _re.search(r"coPhase\(([^)]*)\)", body)
    if call is None:
        errors += fail("co() no longer calls coPhase, so the hiring-phase "
                       "cell is deciding for itself what a board's history "
                       "supports")
    elif "," not in call.group(1):
        errors += fail(f"co() calls coPhase({call.group(1)}) without telling "
                       f"it whether the board could be read. Undefined is not "
                       f"false, so an unreadable board goes back to being "
                       f"described as quiet")
    i = body.find("first_seen")
    while i >= 0:
        if "coAge(" not in body[max(0, i - 24):i]:
            near = body[max(0, i - 60):i + 30].strip().replace("\n", " ")
            errors += fail(
                f"co() reads first_seen outside coAge: ...{near}... Every "
                f"date-based COUNT on the company page goes through coRoles, "
                f"which deduplicates by opening_id; counting inline is how the "
                f"stat strip came to print '357 open roles, +393 in the last "
                f"30 days'. Call coRoles(mine, cutoff).size")
            break
        i = body.find("first_seen", i + 1)

    # ---- the helpers, executed ----------------------------------------
    if not shutil.which("node"):
        note("node not installed; the company-page counters were not executed "
             "this run (co() was still checked)")
        return errors

    a = html.find("function coRoles(")
    b = html.find("function coAge(")
    if a < 0 or b < 0 or b <= a:
        return errors + fail(
            "index.html: coRoles/coPhase are gone, so the company page's "
            "counters could not be executed. If they were renamed, re-point "
            "this check; do not delete it")
    src = html[a:b]

    # 9 postings, 3 openings, 3 cities.
    spread = [{"id": f"p{i}", "opening_id": oid, "first_seen": "TODAY",
               "quota_carrying": False, "location": city}
              for i, (oid, city) in enumerate(
                  [(o, c) for o in ("op-a", "op-b", "op-c")
                   for c in ("Austin", "Denver", "Reno")])]

    # A WINDOW LONGER THAN THE RECORD MEASURES US, NOT THEM, so coPhase
    # refuses to read a 60-day phase off a board we have watched for less
    # than 60 days. Every phase fixture therefore carries one posting from
    # 200 days ago to establish that the record is old enough - it sits
    # outside the 60-day window and so changes no count, only the span.
    OLD = {"id": "anchor", "opening_id": "op-old", "first_seen": "OLD",
           "quota_carrying": False, "location": "Austin"}

    mid_fixture = spread + [{"id": "m", "opening_id": "op-m",
                             "first_seen": "MID", "quota_carrying": False,
                             "location": "Austin"}]

    # 8 postings, 4 openings, ONE city. Quota: 5 postings, 1 opening.
    # postings -> 5/8 and 5 >= 3   -> "Building a sales floor"
    # openings -> 1/4 and 1  < 3   -> "Steady backfill"
    quota = ([{"id": f"q{i}", "opening_id": "op-a", "first_seen": "TODAY",
               "quota_carrying": True, "location": "Austin"} for i in range(5)]
             + [{"id": f"n{i}", "opening_id": f"op-{c}", "first_seen": "TODAY",
                 "quota_carrying": False, "location": "Austin"}
                for i, c in enumerate("bcd")])

    script = """
%s
const iso = t => new Date(t).toISOString().slice(0,10);
const today = iso(Date.now()), old = iso(Date.now() - 200*864e5);
const mid = iso(Date.now() - 20*864e5);
const stamp = a => a.map(p => ({...p,
  first_seen: p.first_seen === "OLD" ? old
            : p.first_seen === "MID" ? mid
            : p.first_seen === "FORTYFIVE" ? iso(Date.now() - 45*864e5)
            : p.first_seen === "FIFTYNINE" ? iso(Date.now() - 59*864e5)
            : today}));
const spread = stamp(%s), quota = stamp(%s), anchor = stamp([%s]);
const thin = stamp([{id:"a",opening_id:"o1"},{id:"b",opening_id:"o1"},
                    {id:"c",opening_id:"o1"},{id:"d",opening_id:"o1"}]);
console.log(JSON.stringify({
  all:    coRoles(spread).size,
  since:  coRoles(spread, Date.now() - 30*864e5).size,
  before: coRoles(spread, Date.now() + 864e5).size,
  noId:   coRoles([{id:"solo", first_seen: today}]).size,
  note:   coPhase(spread.concat(anchor)).note,
  label:  coPhase(quota.concat(anchor)).value,
  thin:   coPhase(thin.concat(anchor)).value,
  // the same postings with NO old anchor: 60 days read off a record that
  // starts today is the crawl's start date wearing the company's name
  young:  coPhase(spread).value,
  span:   coRecordDays(spread.concat(anchor)),
  undated: coRecordDays([{id:"x", opening_id:"o"}]),
  // a 20-day record is older than a week and younger than the window, which
  // is the case a shrunken threshold would wave through
  midling: coPhase(stamp(%s)).value,
  // 45 days: past a 30-day threshold, still short of the 60-day window this
  // phase is read over. Only a record between the two catches a threshold
  // quietly relaxed to half the window it is supposed to protect.
  phase45: coPhase(stamp(%s)).value,
  // 59 days: one day short of the window. Probing 45 caught a threshold
  // relaxed to 30 but not one relaxed to 50, and chasing each value is a
  // losing game - a record one day inside the window closes all of them.
  phase59: coPhase(stamp(%s)).value,
  // the evidence line under the open-roles number, over every kind of zero
  // and over a record too young to carry a delta
  noteYoung: coOpenNote(spread, 3, true),
  noteAged:  coOpenNote(spread.concat(anchor), 4, true),
  // 20 days: older than a week, younger than the window. A threshold quietly
  // shrunk to something the data always clears is the same bug as no
  // threshold at all, and only a record between the two sizes shows it.
  noteMid:   coOpenNote(stamp(%s), 4, true),
  noteUnread: coOpenNote([], 0, false),
  noteQuiet:  coOpenNote([], 0, true),
  // an unreadable board, which has no phase rather than a quiet one
  phaseUnread: coPhase(spread.concat(anchor), false).value,
  phaseRead:   coPhase(spread.concat(anchor), true).value,
  // place lives in office and territory on this board, not in p.location,
  // which is empty on most postings. Three openings across an office state,
  // a two-state territory and a named region are four places.
  regions: coPhase(stamp(%s).concat(anchor), true).note,
}));
""" % (src, _json.dumps(spread), _json.dumps(quota), _json.dumps(OLD),
       # POSITIONAL, AND THE ORDER IS THE SCRIPT'S: midling, phase45, noteMid.
       # Inserting a fixture in the middle once shifted every argument after
       # it and the failure blamed the wrong assertion.
       _json.dumps(mid_fixture),
       _json.dumps(spread + [{"id": "f", "opening_id": "op-f",
                              "first_seen": "FORTYFIVE", "quota_carrying": False,
                              "location": "Austin"}]),
       _json.dumps(spread + [{"id": "n", "opening_id": "op-n",
                              "first_seen": "FIFTYNINE", "quota_carrying": False,
                              "location": "Austin"}]),
       _json.dumps(mid_fixture),
       _json.dumps([
           {"id": "r1", "opening_id": "op-r1", "first_seen": "TODAY",
            "quota_carrying": False, "office": {"state": "CA"}},
           {"id": "r2", "opening_id": "op-r2", "first_seen": "TODAY",
            "quota_carrying": False,
            "territory": {"states": ["TX", "OK"], "stated": True}},
           {"id": "r3", "opening_id": "op-r3", "first_seen": "TODAY",
            "quota_carrying": False,
            "territory": {"states": [], "region": "Midwest", "stated": True}},
           # a territory the board does NOT vouch for. build_board sets
           # stated:false when it inferred states rather than reading them,
           # and an inference is not a place the company said it hires in.
           {"id": "r4", "opening_id": "op-r4", "first_seen": "TODAY",
            "quota_carrying": False,
            "territory": {"states": ["FL"], "stated": False}},
       ]))

    try:
        r = subprocess.run(["node", "--input-type=module", "-e", script],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return errors + fail(f"the company-page counters could not run: {exc}")
    if r.returncode != 0:
        return errors + fail(f"the company-page counters threw: "
                             f"{r.stderr.strip()[:200]}")
    got = _json.loads(r.stdout)

    if got["all"] != 3:
        errors += fail(f"coRoles counted {got['all']} over 9 postings carrying "
                       f"3 opening_ids. It is counting postings, so the stat "
                       f"strip prints a 30-day delta larger than the number of "
                       f"roles it sits under")
    if got["since"] != 3:
        errors += fail(f"coRoles with a since-date counted {got['since']}, "
                       f"expected 3 - the '+N in the last 30 days' line is "
                       f"back in postings")
    if got["before"] != 0:
        errors += fail(f"coRoles counted {got['before']} postings first seen "
                       f"after the cutoff; the date filter is not applied")
    if got["noId"] != 1:
        errors += fail("a posting with no opening_id was dropped rather than "
                       "counted once. Absence of an id is not absence of a job")
    if not (got["note"] or "").startswith("3 roles opened in 60 days"):
        errors += fail(f"the hiring-phase evidence reads {got['note']!r}. Over "
                       f"9 postings carrying 3 openings it must open with "
                       f"'3 roles opened in 60 days'. Counting postings there, "
                       f"or calling them reqs, lets a posting count wear a "
                       f"job's name in the one line a reader checks the label "
                       f"against")
    if got["label"] != "Steady backfill":
        errors += fail(f"a company with 5 postings of ONE quota opening and 3 "
                       f"other openings was called {got['label']!r}. Counted "
                       f"as jobs it is 1 quota role in 4, which is backfill; "
                       f"only a posting count makes it a sales floor being "
                       f"built. The page would name a hiring phase that is "
                       f"not happening")
    if got["young"] != "Not enough history to read":
        errors += fail(
            f"a board first read TODAY was given the hiring phase "
            f"{got['young']!r} off a 60-day window. Motorola Solutions went "
            f"from 38 readable postings to 393 in one night because the crawl "
            f"widened, not because they opened 355 jobs; read over a record "
            f"that does not span the window, that is our start date wearing "
            f"their hiring's name. The phase must refuse until the record is "
            f"as old as the window")
    if got["span"] != 200:
        errors += fail(f"coRecordDays answered {got['span']} for a board whose "
                       f"oldest sighting is 200 days old. The span is what "
                       f"decides whether a window can be read at all")
    if got["undated"] is not None:
        errors += fail(f"coRecordDays answered {got['undated']!r} for postings "
                       f"carrying no date. No record is not a record of zero "
                       f"length; null is the honest answer and the callers "
                       f"branch on it")
    if got["midling"] != "Not enough history to read":
        errors += fail(
            f"a board on file for 20 days was given the hiring phase "
            f"{got['midling']!r} off a 60-day window. The threshold is the "
            f"window: a record shorter than what is being measured over "
            f"cannot produce the measurement, and 20 days is not 60")
    if got["noteYoung"] != "first read here 0 days ago" \
            and not got["noteYoung"].startswith("first read here"):
        errors += fail(
            f"the open-roles evidence over a record that starts today reads "
            f"{got['noteYoung']!r}. It must say how long we have been reading "
            f"the board. Motorola Solutions went from 38 readable postings to "
            f"393 in one night because the crawl widened; printed against a "
            f"fixed 30-day window that became '+357 in the last 30 days', "
            f"which is our start date wearing their hiring's name")
    if got["noteAged"] != "+3 first read in the last 30 days":
        errors += fail(
            f"the open-roles evidence over a 200-day record reads "
            f"{got['noteAged']!r}, expected '+3 first read in the last 30 "
            f"days'. Three things have to hold at once: the delta counts "
            f"openings not postings, it only appears once the record spans "
            f"the window, and it says FIRST READ - we are not told when a job "
            f"was posted, only when we saw it")
    if got["phase45"] != "Not enough history to read":
        errors += fail(
            f"a board on file for 45 days was given the hiring phase "
            f"{got['phase45']!r} off a 60-day window. The threshold has to BE "
            f"the window. Relaxed to half of it, the phase reads 60 days of "
            f"hiring off 45 days of watching and the missing fortnight is "
            f"invented as quiet")
    if got["regions"] != "4 roles opened in 60 days \u00b7 4 regions":
        errors += fail(
            f"the hiring-phase evidence over three openings across an office "
            f"state, a two-state territory and a named region reads "
            f"{got['regions']!r}, expected '4 roles opened in 60 days \u00b7 "
            f"4 regions' - the fourth opening carries an UNSTATED territory, "
            f"which build_board marks that way because it inferred the states "
            f"rather than reading them, and an inference is not a place the "
            f"company said it hires in. Place lives in office and territory; "
            f"p.location is empty on most postings, so counting it alone "
            f"reports one region for a company hiring across nine states - "
            f"and the region count is what decides 'Regional push'")
    if got["phase59"] != "Not enough history to read":
        errors += fail(
            f"a board on file for 59 days was given the hiring phase "
            f"{got['phase59']!r}. The threshold is the window, exactly: one "
            f"day short of 60 is short of 60, and every relaxed threshold "
            f"reads days nobody watched as days nothing happened")
    if got["noteMid"] != "first read here 20 days ago":
        errors += fail(
            f"the open-roles evidence over a 20-day record reads "
            f"{got['noteMid']!r}, expected 'first read here 20 days ago'. "
            f"20 days is older than a week and younger than the 30-day "
            f"window: a threshold shrunk to something the data always clears "
            f"is the same bug as no threshold at all")
    if got["noteUnread"] != "not measured":
        errors += fail(
            f"a zero from a board that could not be read reads "
            f"{got['noteUnread']!r}. Nobody looked, so nothing was seen, and "
            f"saying 'none seen recently' files our failure to fetch as a "
            f"fact about their hiring")
    if got["noteQuiet"] != "none seen recently":
        errors += fail(
            f"a zero from a board we DID read reads {got['noteQuiet']!r}. "
            f"A read board with no roles is a real measurement and should "
            f"say so; collapsing it into 'not measured' throws away the one "
            f"case where the zero means something")
    if got["phaseUnread"] != "Not measured":
        errors += fail(
            f"a company whose board could not be read was given the hiring "
            f"phase {got['phaseUnread']!r}. Any phrasing about openings "
            f"asserts that openings were counted, and nobody could count "
            f"them. Indigov shipped with 'Too few openings to read' one "
            f"column from a Source cell reading 'Board unreadable' - the "
            f"strip contradicting itself in four inches")
    if got["phaseRead"] == "Not measured":
        errors += fail(
            "a readable board was also called 'Not measured', so the phase "
            "cell has stopped reading anything. Refusing every board is not "
            "caution, it is the same failure in the other direction")
    if got["thin"] != "Too few openings to read":
        errors += fail(f"four postings of a SINGLE opening were read as a "
                       f"hiring phase ({got['thin']!r}). That is one job. The "
                       f"floor is 3 roles and it has to be measured in roles, "
                       f"or one job posted in four cities reads as a trend")
    return errors


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
    # EVERY WRITER, NOT ONLY admin.py. The rule used to cover one file, and
    # promote_rivals.py shipped with `by` defaulting to owner and journalled
    # an agent's 132-company write as the owner's ruling - re-attributed by
    # hand the same day. Any script that can call save_companies or
    # save_decisions is in scope, by glob, so the next promote_*.py is
    # covered the day it is written.
    files = [ROOT / "scripts" / "admin.py"] + sorted(
        (ROOT / "scripts").glob("promote_*.py")) + [
        ROOT / "scripts" / n for n in ("proposal_rulings.py", "apply_web_rulings.py",
                                       "agents.py", "conference_intake.py",
                                       "wire_embedded.py", "apply_task_notes.py")
        if (ROOT / "scripts" / n).exists()]
    src = "\n".join(f"# FILE {f.name}\n" + f.read_text() for f in files)
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
        fstart = src.rfind("# FILE ", 0, m.start())
        fname = src[fstart + 7:src.find("\n", fstart)] if fstart >= 0 else "admin.py"
        # the line a person can open: counted from the file's own marker,
        # not from the top of the concatenation
        line = src.count("\n", fstart, m.start()) if fstart >= 0 else line
        bad += fail(f"{fname}:{line}: save_companies({snippet}) does not pass "
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
             # EVERY POSTING CARRIES BOTH IDS, because every real one does -
             # 0 of 4,439 on the live board lack opening_id. This fixture
             # omitted it, and when the company page started grouping by
             # opening (so a requisition in forty cities stops printing forty
             # times) this check raised KeyError instead of failing.
             #
             # The fix is the fixture, not a .get() fallback in build_site: a
             # fallback would make a board that somehow lost the field degrade
             # silently back to counting rows, which is the defect being
             # closed. A fixture that does not look like the data cannot test
             # the code that reads the data.
             "postings": [
                 {"id": "1", "opening_id": "seller::Account Executive",
                  "company_id": "seller", "company": "Seller Co",
                  "title": "Account Executive", "family": "gtm",
                  "quota_carrying": True, "office": {"state": "CA"}},
                 {"id": "2", "opening_id": "seller::Backend Software Engineer",
                  "company_id": "seller", "company": "Seller Co",
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
    """datePosted may only ever be the EMPLOYER'S date, never ours.

    THIS CHECK CHANGED ON 2026-09-01 AND ITS REASON DID NOT. It used to forbid
    datePosted outright, and that was correct while the only date this repo
    held was our own crawl date. ats.py now reads a publish date from all
    seven structured boards, so a true value exists for the rows whose board
    publishes one - and Google lists datePosted as required, so withholding it
    made every one of those blocks ineligible.

    What is still forbidden is the ONE thing that was ever wrong: filling it
    from first_seen. The guard moved from "no date" to "not that date", which
    is a narrower claim and the one the reasoning below actually supports.

    It emitted `first_seen`, the day THIS BOARD first saw the row. 2,183 of
    3,524 structured blocks claimed 2026-08-18 or 2026-08-19 - our first two
    crawls - as the day the employer posted. A role advertised since spring
    read as posted the morning we started looking.

    index.html already refuses to tell a HUMAN that, in as many words: "Saying
    'appeared' would file our crawl date as a fact about somebody's hiring,
    which is the same species of claim as reporting a page we could not read as
    'no jobs here'." The page told the truth to a reader and told Google the
    other thing.

    Where a board publishes no date the field is still withheld entirely, like
    validThrough and baseSalary. Optional in the spec; a wrong one is not.
    """
    bad = 0
    mw = (ROOT / "functions" / "_middleware.js").read_text()
    code = re.sub(r"//.*$", "", mw, flags=re.M)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    flat = re.sub(r"\s+", "", code)
    # IT MAY READ `pd` AND NOTHING ELSE. `pd` is the employer's own publish
    # date; `d` was first_seen, and 2,183 of 3,524 blocks once claimed one of
    # our first two crawl days as the employer's posting date.
    if "datePosted" in code and "o.datePosted=r.pd" not in flat:
        bad += fail("the middleware sets datePosted from something other than "
                    "r.pd, the employer's own publish date. The only other "
                    "date on a role is first_seen, which is when WE saw it - "
                    "our crawler's history dressed as their hiring")
    if re.search(r"datePosted\s*=\s*r\.d\b", flat.replace("o.datePosted=r.pd", "")):
        bad += fail("the middleware fills datePosted from r.d, which is "
                    "first_seen - the exact defect this check was written for")
    # AND IT MUST BE CONDITIONAL. Emitting it unconditionally publishes
    # `undefined` as a date on every row whose board gave none.
    if "datePosted" in code and "if(r.pd)o.datePosted=r.pd" not in flat:
        bad += fail("the middleware emits datePosted without checking that the "
                    "employer actually published one, so rows from boards that "
                    "give no date would carry an empty or undefined value")
    # AND THE PRODUCER, because moving the fallback one file upstream is the
    # same defect with a different address - and it is the mutation the
    # middleware half cannot see. build_site must write `pd` from the
    # employer's `posted` and from nothing else.
    bs = re.sub(r"#.*$", "", (ROOT / "scripts" / "build_site.py").read_text(),
                flags=re.M)
    bsflat = re.sub(r"\s+", "", bs)
    if 'r["pd"]=p_["posted"]' not in bsflat:
        bad += fail("build_site no longer writes pd from the employer's own "
                    "posted date. Any other source is our crawl date, and "
                    "2,183 of 3,524 blocks once published one of our first two "
                    "crawl days as the day the employer posted the job")
    if 'if p_.get("posted"):' not in bs:
        bad += fail("build_site writes pd without checking the employer "
                    "published a date at all")
    # LINE-WISE, not against the flattened source. The first version of this
    # searched `\["pd"\]=[^\n]*first_seen` in text that had already had its
    # newlines stripped, so `[^\n]*` spanned the whole file and it fired on
    # correct code - a check that reads its own input wrong, which is the
    # shape this file exists to catch.
    for line in bs.splitlines():
        if '["pd"]' in line and "first_seen" in line:
            bad += fail("build_site falls back to first_seen for pd - that is "
                        "the crawl-date defect exactly, one file upstream of "
                        "where it was caught last time")
            break
    _b = built()
    if _b is None:
        return fail(f"selftest cannot build the shipped artifacts "
                    f"({_BUILT.get('why')}), so the structured-data checks "
                    f"cannot run. Silence here reads as a pass.")
    meta = _b / "meta-roles.json"
    if meta.exists():
        roles = json.loads(meta.read_text())
        roles = roles.get("roles", roles)
        dated = sum(1 for v in roles.values()
                    if isinstance(v, dict) and v.get("ld") and v.get("d"))
        if dated:
            bad += fail(f"{dated:,} structured roles still carry a date field "
                        f"for the middleware to publish as datePosted")
    src = (ROOT / "scripts" / "build_site.py").read_text()
    # THE WHOLE FUNCTION, NOT A SLICE. This read from `r["ld"] = 1` to
    # `roles[p_[`, so writing the crawl date one line lower - after the dict is
    # stored - fell outside the window and passed. Same boundary bug as
    # act_capture's split("\n")[1:3][0], and in a check written the same night
    # that one was fixed.
    blk = src[src.index("def write_meta_index("):]
    blk = blk[:blk.index("\ndef ")]
    if re.search(r'r\["d"\]\s*=|\["d"\]\s*=\s*p_\.get\("first_seen"', blk):
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
    # THE EXPRESSION, NOT THE WORD. `else if (false) o.jobLocationType =
    # "TELECOMMUTE";` leaves the string verbatim and emits nothing - the same
    # disabled-in-place mutation that beat the csvCell check three commits ago.
    if 'r.tc)o.jobLocationType="TELECOMMUTE"' not in re.sub(r"\s+", "", mwcode):
        bad += fail("the middleware no longer emits jobLocationType gated on "
                    "r.tc, so 533 remote postings ship a JobPosting with no "
                    "location statement at all")
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


# THE SHIPPED ARTIFACTS, BUILT HERE RATHER THAN READ FROM public/.
#
# Five checks read ROOT/"public" - the sitemap, meta-roles, the shipped board,
# _headers. public/ is in .gitignore, and refresh.yml runs selftest as its
# FIRST step, before build_board and before build_site. So in CI those files do
# not exist, every one of those checks takes its `if ... .exists()` escape, and
# the suite reports all checks passed. Six mutations to real fixes - shipping
# invalid placeless JobPosting blocks, putting .html back in the sitemap,
# restoring the companies_read overclaim - were all green.
#
# Locally the files DO exist, which is worse: they are whatever the last build
# produced, so a check can pass against an artifact built before the edit it is
# meant to be judging.
#
# So the suite builds its own, once, from the live data, into a temp directory.
# Deterministic, present in CI, and always matching the source in the tree.
_BUILT: dict = {}


def built() -> "pathlib.Path | None":
    """A freshly built public/ for this run, or None if it cannot be built."""
    if "path" in _BUILT:
        return _BUILT["path"]
    import tempfile
    import build_site
    try:
        out = pathlib.Path(tempfile.mkdtemp(prefix="selftest-public-"))
        board = json.loads((DATA / "board.json").read_text())
        brand = json.loads((DATA / "brand.json").read_text())
        (out / "data").mkdir(parents=True, exist_ok=True)
        build_site.write_meta_index(out, board)
        build_site.write_crawl_files(out, board, brand)
        build_site.write_headers(out)
        # THE PAGES WITH NUMBERS ON THEM. Added after an audit found /s/mi
        # shipping "12 open roles ... 9 quota-carrying" against 4 openings of
        # which 1 carried a quota, and /c/xplor-recreation heading a
        # forty-row list with "17 open roles" and closing "60 more are on the
        # board". Neither had ever been built by this suite.
        build_site.write_company_pages(out, board, brand)
        build_site.write_state_pages(out, board, brand)
        _BUILT["path"] = out
    except Exception as e:                        # noqa: BLE001
        # A build that cannot run must SAY so rather than let five checks
        # quietly pass. The caller fails loudly on None.
        _BUILT["path"] = None
        _BUILT["why"] = f"{type(e).__name__}: {e}"
    return _BUILT["path"]


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


def check_every_check_is_actually_run() -> int:
    """Every `def check_*` in this file must be called by main().

    Checks here are registered by a hand-written `errors += check_x()` line in
    main, which is fine until somebody adds the function and forgets the line.
    Then the file contains a check that has never run once. That happened to
    check_ship_path_attaches_active within a minute of it being written - the
    suite printed "all checks passed" with a brand-new guard sitting inert two
    hundred lines above the call list, and the mutation it was written to
    catch went green.

    This file's whole job is catching checks that prove nothing. A check that
    is never called is the limit case of that, and it is the one shape no
    amount of care inside a check can catch from the inside.
    """
    src = pathlib.Path(__file__).read_text()
    defined = set(re.findall(r"^def (check_[A-Za-z0-9_]+)\(", src, re.M))
    called = set(re.findall(r"(?<!def )\b(check_[A-Za-z0-9_]+)\(", src))
    orphans = sorted(defined - called - {"check_every_check_is_actually_run"})
    bad = 0
    for name in orphans:
        bad += fail(f"{name} is defined in this file and never called, so it "
                    f"has never run and cannot have caught anything")
    # AND NO CHECK MAY BE DEFINED TWICE. Python keeps the LAST definition
    # silently, so a second copy of a check does not error - it replaces the
    # first, and every assertion the earlier one carried simply stops
    # existing. That happened here: a second check_posts_at_vocabulary was
    # added by somebody who had not found the one already in the file. It sat
    # 1,300 lines above the original, called a helper that was never defined,
    # and could not have raised NameError because it never ran. The suite
    # printed "all checks passed" throughout, and the mutation written to
    # prove the new assertion went green.
    #
    # `defined` above is a set, so it cannot see this. Count the defs.
    for name in sorted(set(re.findall(r"^def (check_[A-Za-z0-9_]+)\(", src, re.M))):
        n = len(re.findall(rf"^def {name}\(", src, re.M))
        if n > 1:
            bad += fail(f"{name} is defined {n} times in this file. Python "
                        f"keeps the last one, so the assertions in the others "
                        f"have silently stopped running")
    return bad


def check_ship_path_attaches_active() -> int:
    """build_site.main must actually call attach_active.

    THE COST OF EXTRACTING A FUNCTION TO TEST IT. attach_active was inline in
    main(); moving it out let the badge check call it directly, and that check
    now passes whether or not the ship path calls it at all. Replacing the
    call in main() with `board["active"] = []` mutates the site to ship no
    badge ever, and every other check stayed green.

    Source-level, because main() is a full site build and no test runs it -
    the same reason check_writes_name_their_author reads source rather than
    calling the admin's actions.
    """
    src = (ROOT / "scripts" / "build_site.py").read_text()
    i = src.find("\ndef main(")
    if i < 0:
        return fail("build_site has no main() - this guard cannot find the "
                    "ship path it is supposed to be checking")
    if "attach_active(" not in src[i:]:
        return fail("build_site.main never calls attach_active, so nothing "
                    "puts the hiring-hard list on the board and the badge "
                    "cannot appear on the site - the badge check passes "
                    "anyway, because it calls attach_active itself")
    return 0


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
    # COMPUTED HERE, not read from a file, and the route to that took two
    # wrong turns worth keeping. First this read data/board.json, which never
    # carries `active` at all (build_site attaches it on the way out), so the
    # check found nothing and returned 0. Then it read the SHIPPED board under
    # public/ - correct artifact, but public/ is gitignored and absent when
    # this suite runs in CI, so it took its own exists() escape and passed
    # while the badge could be deleted from the page outright.
    #
    # So build_site.attach_active was extracted from main() and is called
    # here. That closes the CI hole and opens a smaller one: calling the
    # function directly cannot see whether the SHIP PATH still calls it, which
    # is what check_ship_path_attaches_active above is for. Extracting code to
    # make it testable moves it out of the path you were testing.
    import build_site as _bs
    board = _bs.attach_active(
        json.loads((DATA / "board.json").read_text()))
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
    # THE CALL SITE. `function hotChip(` contains "hotChip(", so deleting the
    # only invocation left this green while the badge vanished from the page.
    if "${hotChip(" not in src:
        bad += fail("index.html defines hotChip but never calls it - the badge "
                    "is gone from the page and nothing here notices")
    blk = src[src.index("function hotChip("):]
    blk = blk[:blk.index("\nfunction ")]
    if "if(!a) return" not in re.sub(r"\s+", "", blk).replace("if(!a)return", "if(!a) return"):
        bad += fail("hotChip does not return empty for a company that is not "
                    "in the list - a badge on everything means nothing")
    if "title=" not in blk:
        bad += fail("the active badge carries no tooltip saying what the "
                    "number is, so the claim cannot be checked by the reader")
    return bad


# Every wire format the seven structured boards actually send, observed live
# on 2026-09-01 rather than remembered. The last two are the ones that matter:
# a parser that guesses turns 01/02 into January the second on one board and
# the first of February on the next.
POSTED_CASES = [
    ("2026-08-26T16:02:03-04:00", "2026-08-26"),   # greenhouse first_published
    ("2025-10-19T18:01:17.960+00:00", "2025-10-19"),  # ashby publishedAt
    (1784620514622, "2026-07-21"),                 # lever createdAt, epoch ms
    ("2026-09-01T07:29:04.571Z", "2026-09-01"),    # smartrecruiters releasedDate
    ("2026-08-27 19:42:19 UTC", "2026-08-27"),     # recruitee published_at
    ("2026-07-04T03:01:28.931Z", "2026-07-04"),    # breezy published_date
    ("2026-08-14", "2026-08-14"),                  # workable published_on
    ("", None), (None, None), ("not a date", None),
    ("01/02/2026", None),        # ambiguous by design - must refuse, not guess
]


def _import_ats():
    sys.path.insert(0, str(ROOT / "scripts"))
    import ats
    return ats


# Markup fragments taken off REAL careers pages on 2026-09-01, with the slug
# each one names. The first three are why this table exists: the first draft of
# find_boards.py was written from memory and found NOTHING on all three, while
# every one of them carries its board in the served HTML.
FIND_BOARD_CASES = [
    # Autura. The `/js` is the part a remembered pattern drops.
    ('<div id="grnhse_app"></div><script src="https://boards.greenhouse.io'
     '/embed/job_board/js?for=autura"></script>', ("greenhouse", "autura")),
    # Nallian names its board only in a mailto on the page.
    ('send your resume to <a href="mailto:nallian@jobs.workablemail.com">',
     ("workable", "nallian")),
    ('<iframe src="https://boards.greenhouse.io/debtbook"></iframe>',
     ("greenhouse", "debtbook")),
    ('<a href="https://jobs.lever.co/everbridge">Careers</a>',
     ("lever", "everbridge")),
    ('<a href="https://jobs.ashbyhq.com/seneca/04229ae5">Open roles</a>',
     ("ashby", "seneca")),
    ('<script src="https://truleo.breezy.hr/embed"></script>',
     ("breezy", "truleo")),
    # NOT a slug: a build-tool filename that merely contains the ATS name.
    ('<link href="/module_Career_-_Greenhouse.min.css">', None),
    # NOT a slug: the vendor's own marketing site.
    ('<a href="https://www.greenhouse.io/customers">Greenhouse</a>', None),
]


def _import_find_boards():
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_boards
    return find_boards


# Company name against the LinkedIn slug its own careers page names, every
# pair observed on a real page 2026-09-01. The False rows are the point: two
# of them are a PARENT's page sitting in a subsidiary's footer.
LINKEDIN_CASES = [
    ("Kahua", "kahua", True),
    ("Palo Alto Networks", "palo-alto-networks", True),
    ("VR Systems", "vr-systems-inc", True),
    ("24/7 Software", "247software", True),
    ("AffordableHousing.com", "affordablehousingdotcom", True),
    ("Schneider Geospatial", "schneider-geospatial", True),
    # Gordian's footer names Fortive, its parent. A seeker sent there lands on
    # 4,000 Fortive employees and cannot tell which three are Gordian's - the
    # same false Yes as pointing a company at a parent's job board.
    ("Gordian", "fortive", False),
    ("SITA Information Networking", "axa", False),
    # A rename that is indistinguishable from a mistake from here. Eccovia
    # really was CaseWorthy; deciding that is judgement, so it waits.
    ("Eccovia", "caseworthyinc", False),
    # The opaque numeric form is a valid LinkedIn address carrying no name at
    # all, so nothing can confirm it is theirs.
    ("Saltus Technologies", "819952", False),
]


def _import_find_linkedin():
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_linkedin
    return find_linkedin


# Every value the four boards actually send, observed live 2026-09-01. The
# False/None rows are the point: two of these boards say "not remote" and
# neither of them says "onsite".
WORK_MODE_CASES = [
    ("OnSite", "onsite"), ("Remote", "remote"), ("Hybrid", "hybrid"),   # ashby
    ("remote", "remote"), ("hybrid", "hybrid"), ("onsite", "onsite"),   # lever
    ("on-site", "onsite"),
    (False, None), (True, None),      # a boolean is not a word for a mode
    ("Flexible", None), ("", None), (None, None),
]

OFFICE_HINT_CASES = [
    # ashby, a real US address: the region arrives as a full name
    (dict(city="Sausalito", region="California", country="United States"),
     {"city": "Sausalito", "state": "CA", "country": "United States"}),
    # workable, a real UK address in the SAME SHAPE. "England" must not
    # become a state - this is the London/Montreal trap in a structured field
    (dict(city="London", region="England", country="United Kingdom"),
     {"city": "London", "state": None, "country": "United Kingdom"}),
    (dict(city="Greater Montreal", country="Canada"),
     {"city": "Greater Montreal", "state": None, "country": "Canada"}),
    # lever sends a country and nothing else
    (dict(country="US"), {"city": None, "state": None, "country": "US"}),
    (dict(city="Austin", region="TX", country="USA"),
     {"city": "Austin", "state": "TX", "country": "USA"}),
    # THE PAIR THAT MAKES THE COUNTRY CHECK LOAD-BEARING. roles.STATE_NAMES
    # returns GA for "georgia" and cannot tell the country from the state -
    # CLAUDE.md already lists georgia in AMBIGUOUS_STATE_NAMES for exactly
    # this. Only the country separates Tbilisi from Atlanta, and without
    # these two rows the England case passes on the name lookup alone and the
    # country check can be deleted with the suite still green.
    (dict(city="Tbilisi", region="Georgia", country="Georgia"),
     {"city": "Tbilisi", "state": None, "country": "Georgia"}),
    (dict(city="Atlanta", region="Georgia", country="United States"),
     {"city": "Atlanta", "state": "GA", "country": "United States"}),
    (dict(), None),
]


# Date strings as the catalogue and real conference pages actually write them.
# Every one observed, not composed.
CONF_DATE_CASES = [
    ("October 17-21, 2026", "2026-10-17"),
    ("August 30 - September 2, 2026", "2026-08-30"),
    ("June 26 - July 1, 2027", "2027-06-26"),
    ("Feb. 21-24, 2026", "2026-02-21"),
    ("October 25\u201328, 2026", "2026-10-25"),      # en dash
    ("May 6, 2027", "2027-05-06"),                    # a single day
    ("", None),
    ("Conference: November 17-19, 2026; Expo: November 18-19, 2026",
     "2026-11-17"),        # two ranges: the FIRST start, and the calendar
                           # still refuses to place it - see cfCalendarHTML
    ("Sometime in the spring", None),
]


def _import_conference_dates():
    sys.path.insert(0, str(ROOT / "scripts"))
    import conference_dates
    return conference_dates


def check_conference_dates_engine() -> int:
    """The calendar's maintenance engine confirms dates and never invents one.

    THE OBVIOUS ENGINE WOULD HAVE MADE THE DATA WORSE. Reading each event's
    own page and extracting the date was measured on eight events before any
    of it was written: three pages yielded a date at all and all three were
    wrong. NACo's page offers "Dec. 3-5, 2026" and "Feb. 11-15 2028" - two
    OTHER NACo events - against the July 2027 annual we hold. An association
    runs several events on one site, and picking which is "the" date is
    judgement. 111 of 126 dates are already high confidence, so a scraper
    would mostly be replacing good data with bad.

    So the engine makes the weakest claim that is still useful - does the page
    still carry what we already say - and the half that needs no network at
    all: an event whose date has passed is stale by arithmetic, with no false
    positives possible. That is what actually rots a calendar.

    This check pins the parser and the never-writes rule. A date parser that
    guesses is how 01/02 becomes two different days.
    """
    cd = _import_conference_dates()
    bad = 0
    for raw, want in CONF_DATE_CASES:
        got = cd.parsed(raw)
        got = got.isoformat() if got else None
        if got != want:
            bad += fail(f"conference_dates.parsed({raw!r}) = {got!r}, "
                        f"expected {want!r}")
    # IT MUST NOT WRITE THE CATALOGUE. The whole design rests on proposing to
    # a person rather than editing, and one open() in write mode would undo it.
    src = re.sub(r"#.*$", "", (ROOT / "scripts" / "conference_dates.py").read_text(),
                 flags=re.M)
    src = re.sub(r'""".*?"""', "", src, flags=re.S)
    # Every write target this module has, resolved through the variable it
    # was assigned to rather than matched on one line - `out.write_text(...)`
    # names its path two lines up, which a line-wise check cannot see.
    import ast as _ast
    targets = set()
    _tree = _ast.parse((ROOT / "scripts" / "conference_dates.py").read_text())
    _paths = {}
    for _n in _ast.walk(_tree):
        if isinstance(_n, _ast.Assign) and len(_n.targets) == 1 \
                and isinstance(_n.targets[0], _ast.Name):
            _paths[_n.targets[0].id] = _ast.unparse(_n.value)
        if isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Attribute) \
                and _n.func.attr in ("write_text", "write_bytes", "open"):
            tgt = _ast.unparse(_n.func.value)
            targets.add(_paths.get(tgt, tgt))
    # THIS CHECK NARROWED WHEN THE QUEUE LANDED, and its reason did not.
    # It used to forbid writing the catalogue at all, which was right while
    # nothing could produce a date a person had approved. The admin's
    # Conference dates queue now does, so --apply folds those rulings in.
    #
    # What is still forbidden is the thing that was ever wrong: a date read
    # off an event's page reaching the catalogue. confirm() must not produce
    # one, and the write must be gated on the ruling path.
    csrc = (ROOT / "scripts" / "conference_dates.py").read_text()
    conf_fn = csrc[csrc.index("def confirm("):]
    conf_fn = conf_fn[:conf_fn.index("\ndef ")]
    # COMMENTS AND THE DOCSTRING STRIPPED FIRST. The comment inside confirm()
    # explains how ARMA "writes" its dates, and a raw search for that word
    # failed the suite on correct code - the third time today a check here has
    # matched prose instead of what runs.
    conf_code = re.sub(r"#.*$", "", conf_fn, flags=re.M)
    conf_code = re.sub(r'""".*?"""', "", conf_code, flags=re.S)
    if "write" in conf_code:
        bad += fail("conference_dates.confirm writes something. It reads an "
                    "event page, and every page measured that offered a date "
                    "offered another event's - NACo's site gave two other "
                    "NACo meetings against the annual we hold")
    if "dates_confidence" in conf_code:
        bad += fail("conference_dates.confirm proposes a date. It answers one "
                    "question - is what we hold still on their page - and "
                    "reading a new one off a page is what the measurement "
                    "showed produces other events' dates")
    apply_at = csrc.find("if a.apply:")
    write_at = csrc.find("conferences.json.tmp")
    if write_at > 0 and not (0 < apply_at < write_at):
        bad += fail("the catalogue write is not inside the --apply branch, so "
                    "a plain run could edit dates nobody ruled on")
    if "def confirm(" not in src or "not found" not in src:
        bad += fail("conference_dates no longer distinguishes a date it could "
                    "not find from one that changed. They are different facts "
                    "and only one of them is about the conference")
    return bad


def check_acquisition_bands_cover_every_strength() -> int:
    """Every strength the queue can emit must have a heading on the card.

    THIRTY-TWO ROWS DREW THE WORD "undefined". admin.html's band map had
    named, redirect, domain and slug and no `logo` - and logo-family is the
    second strongest signal in the queue, 32 of its rows. Every one of them
    headed its card with a JavaScript mistake, under an evidence line that
    was perfectly good.

    This is the same failure the queue already has a long comment about: it
    rendered blank for 82 rows because the server's shape and the client's
    reader had drifted, and acquisition_rulings.json has never been written
    once. A band nobody can read is a row nobody rules.

    Source-level on both sides, because a browser cannot import Python and the
    two lists are necessarily separate - the same reason
    check_alert_vocabulary exists.
    """
    acq = (ROOT / "scripts" / "acquisitions.py").read_text()
    emits = set(re.findall(r'strength,\s*says\s*=\s*"([a-z_]+)"', acq))
    page = (ROOT / "admin.html").read_text()
    m = re.search(r"const head = \{(.*?)\}\[strength\]", page, re.S)
    if not m:
        return fail("admin.html no longer defines the acquisition band map, "
                    "so every row heads its card with nothing")
    named = set(re.findall(r"(\w+)\s*:\s*['\"]", m.group(1)))
    bad = 0
    for k in sorted(emits - named):
        bad += fail(f"acquisitions.py can emit strength {k!r} and admin.html "
                    f"has no band for it, so those rows head their card with "
                    f"the word 'undefined'")
    # AND A FALLBACK, because the next strength added will be missing too and
    # the guard only fires after somebody runs it.
    # UP TO THE END OF THE STATEMENT, not a fixed window. The first version
    # searched the next 200 characters for "||" and found `s.says || ''` on
    # the line after next - an unrelated expression - so removing the fallback
    # left the suite green. A check that passes on adjacent text is not a
    # check, which is the shape this whole file exists to catch.
    stmt = page[m.end():page.find(";", m.end()) + 1] if ";" in page[m.end():] else ""
    if "||" not in stmt:
        bad += fail("the band map has no fallback for an unknown strength. "
                    "The guard above catches a new one only when somebody runs "
                    "the suite; the fallback catches it on the first render")
    return bad


def check_board_stated_mode_and_office() -> int:
    """What a board states about mode and place beats what we read off prose.

    work_mode reads "not stated" on 3,525 of 4,450 postings and office parses
    on 1,627 - and both numbers are about OUR regex, not about what employers
    published. Two primary controls sit over that field: the
    Remote/Hybrid/Onsite pills reach 117 and 24 rows respectively. Ashby,
    Lever, Workable and Recruitee all state the mode outright and hand back a
    structured address in responses this project already downloads.

    TWO TRAPS, AND THE CASES ABOVE ARE BOTH OF THEM.

    A NEGATIVE IS NOT A MODE. Workable sends `telecommuting: false` and Ashby
    sends `isRemote: false`. Neither means onsite - it means NOT REMOTE, which
    is onsite or hybrid and the board did not say which. Reading either as
    onsite publishes a claim about somebody's job that their own posting does
    not make. On Civica's board that is 69 of 81 rows.

    A FULL REGION NAME IS NOT A STATE CODE. These boards send "California" and
    "England" in the identical field. The board's state pages and map are US
    two-letter codes, so a region becomes a state only where the country reads
    as the United States and the name resolves - which is the structured-field
    version of the trap CITY_CASES already pins, where "London, UK" and
    "Montreal, QB" filed 24 postings in states that do not exist.
    """
    ats = _import_ats()
    bad = 0
    for raw, want in WORK_MODE_CASES:
        got = ats.work_mode(raw)
        if got != want:
            bad += fail(f"ats.work_mode({raw!r}) = {got!r}, expected {want!r}"
                        + ("  - a board that says 'not remote' has not said "
                           "onsite" if want is None and isinstance(raw, bool)
                           else ""))
    for kw, want in OFFICE_HINT_CASES:
        got = ats.office_hint(**kw)
        if got != want:
            bad += fail(f"ats.office_hint({kw}) = {got}, expected {want}"
                        + ("  - a region outside the US must never become a "
                           "state code" if want and want.get("state") is None
                           else ""))
    # A COUNTRY ALONE MUST NOT BECOME AN OFFICE. build_board only promotes a
    # hint to `office` when it has a city or a state, because an office of
    # {city: None, state: None} is truthy: it passes "has a desk", is excluded
    # by the "no office stated" filter, and is silently skipped by the map,
    # which needs both.
    src = re.sub(r"#.*$", "", (ROOT / "scripts" / "build_board.py").read_text(),
                 flags=re.M)
    flat = re.sub(r"\s+", "", src)
    if 'ifhint.get("city")orhint.get("state"):' not in flat:
        bad += fail("build_board promotes a board's office hint without "
                    "checking it names a city or a state, so a country-only "
                    "hint becomes a truthy office with no place in it")
    # AND IT ONLY EVER FILLS A BLANK.
    if 'ifnotgeo["work_mode"]orgeo["work_mode"]=="notstated":' not in flat:
        bad += fail("build_board overwrites a work mode that geography() read "
                    "off the location text, so two sources can disagree with "
                    "no way to tell which won")
    if 'ifhintandnotgeo.get("office"):' not in flat:
        bad += fail("build_board overwrites a parsed office with the board's "
                    "hint instead of only filling a blank")

    # BAMBOOHR KEEPS THE ADDRESS IN atsLocation, and `location` is
    # {city: null, state: null} on every row of every board checked. Reading
    # the obvious field returned nothing and looked like the board giving
    # nothing: 48 of 266 live postings carried a completely empty location
    # string and 166 had no parsed office, while "Denver, Colorado, United
    # States" sat one key over.
    asrc = re.sub(r"#.*$", "", (ROOT / "scripts" / "ats.py").read_text(),
                  flags=re.M)
    aflat = re.sub(r"\s+", "", asrc)
    if 'loc=j.get("atsLocation")orj.get("location")or{}' not in aflat:
        bad += fail("fetch_bamboohr reads `location` before `atsLocation`. "
                    "That field is {city: null, state: null} on every row, so "
                    "the postings come back with no place at all and it looks "
                    "like the board never said")
    # SMARTRECRUITERS' remote/hybrid FLAGS ARE NOT READ, ON PURPOSE. Xplor's
    # board sets remote true on 92 of 100 postings that each carry a real city
    # - "Phoenix, AZ, United States". Whatever the flag means there, it is not
    # what work_mode means here, and publishing it would put 92 false "remote"
    # labels on one company.
    i = aflat.find("api.smartrecruiters.com/v1/companies/")
    if i > 0 and 'loc.get("remote")' in aflat[i:i + 2000]:
        bad += fail("fetch_smartrecruiters now reads that board's `remote` "
                    "flag as a work mode. It is true on 92 of 100 Xplor "
                    "postings that all name a real city, so it does not mean "
                    "what work_mode means here")
    return bad


def check_alert_preview_matches_the_digest() -> int:
    """The preview and the email it previews must ask the same question.

    alerts.html's "preview" is not a mock - it sends the reader to the board
    carrying their own filters, and its comment says why: "a preview that can
    disagree with the real thing is worse than no preview, so this reuses the
    board's own URL keys rather than re-implementing the matching here."

    The intent was right and the board had no key that meant what the digest
    means. digest.py matches a subscriber's state when the TERRITORY covers it
    OR the OFFICE is in it. The board had `st` (territory alone, 96 of 4,450
    postings) and `off` (office alone), and the preview picked `st` - then sent
    only states[0]. A subscriber choosing NY, NJ, CT previewed 10 postings
    against the 326 their email would carry. Thirty-three times, on the one
    screen that asks for an email address.

    Guarded on BOTH sides because it is the same shape as
    check_alert_vocabulary: a Worker cannot import Python, a browser cannot
    either, and the drift is silent - the preview simply shows a smaller
    number and nobody can tell it is the wrong number.
    """
    bad = 0
    al = (ROOT / "alerts.html").read_text()
    flat_al = re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", al, flags=re.S))
    if 'u.set("anyst",p.states.join(","))' not in flat_al:
        bad += fail("the alerts preview no longer sends every chosen state to "
                    "the board's territory-or-office filter. `st` is territory "
                    "alone and reaches 96 of 4,450 postings; states[0] throws "
                    "away every choice after the first")
    if 'u.set("st",p.states[0])' in flat_al:
        bad += fail("the alerts preview is back on `st` with states[0] - the "
                    "exact pair that showed a subscriber a thirty-third of "
                    "their own alert")

    page = (ROOT / "index.html").read_text()
    flat_pg = re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", page, flags=re.S))
    # The board's side of the same question. Both halves of the union, or the
    # preview quietly starts disagreeing again in the other direction.
    if "anyst.some(x=>(p.states||[]).includes(x)" not in flat_pg:
        bad += fail("index.html's anyst filter no longer matches on the "
                    "role's territory states, so a preview would drop every "
                    "territory role the digest would send")
    if "p.office&&p.office.state===x" not in flat_pg:
        bad += fail("index.html's anyst filter no longer matches on the "
                    "office state, so a preview would drop every desk-in-state "
                    "role the digest would send - which is most of them")

    # AND digest.py must still be asking that question. If it narrows to one
    # of the two, the board is now the one overstating.
    dg = re.sub(r"#.*$", "", (ROOT / "scripts" / "digest.py").read_text(),
                flags=re.M)
    flat_dg = re.sub(r"\s+", "", dg)
    if 'here.add(p["office"]["state"])' not in flat_dg:
        bad += fail("digest.py no longer folds the office state into its "
                    "state match, so the board's anyst filter now shows a "
                    "subscriber more than their email will carry")
    return bad


def check_linkedin_is_the_companys_own() -> int:
    """A LinkedIn address on a card must be that company's, not a parent's.

    find_linkedin reads the slug off the vendor's own careers page - it never
    touches linkedin.com - and a footer carries the parent's address about as
    often as its own. Measured on a random 25 of the page-only pile: 23 named
    a LinkedIn company URL and 5 of those were not the company. Storing all 23
    would have been 22% wrong, and wrong in the direction CLAUDE.md names
    explicitly: never point a company at its parent.

    So the name check is the whole guard, and it is deliberately crude. It is
    not deciding whether a company was renamed - it cannot, and one of the
    five is a real rename. It separates "obviously theirs" from "needs a
    person", and only the first pile is ever written.
    """
    fl = _import_find_linkedin()
    bad = 0
    for name, slug, want in LINKEDIN_CASES:
        got = fl.resembles(name, slug)
        if got != want:
            bad += fail(
                f"find_linkedin.resembles({name!r}, {slug!r}) = {got}, "
                f"expected {want}"
                + ("  - that slug is not this company and storing it points a "
                   "reader at somebody else's page, usually a parent's"
                   if want is False else
                   "  - a real match was refused, so the company keeps no "
                   "LinkedIn at all and the card has nothing to offer"))
    # AND A PERSONAL PROFILE IS NEVER A COMPANY. /in/ is somebody's profile.
    for markup in ('<a href="https://www.linkedin.com/in/wyethwest">me</a>',
                   '<a href="https://www.linkedin.com/">LinkedIn</a>'):
        if fl.candidates(markup):
            bad += fail(f"find_linkedin read a company slug out of {markup!r}, "
                        f"which names a person or nothing at all")
    return bad


def check_find_boards_reads_real_pages() -> int:
    """find_boards must recognise the forms careers pages actually use.

    THE FIRST DRAFT OF THIS FILE FOUND NOTHING, on the exact three pages the
    read trial named as its whole justification - Autura, Nallian and
    DebtBook. All three carry their board in the SERVED html; the patterns
    were written from memory and every one was slightly wrong. Autura serves
    `/embed/job_board/js?for=` and the pattern expected `/embed/job_board?for=`;
    Nallian names its board only in a mailto at
    `nallian@jobs.workablemail.com`.

    An extractor that finds nothing does not fail loudly - it reports "no ATS
    named on the page" for all 781 companies and reads exactly like an honest
    negative. That is the shape this table exists to catch, so every fragment
    here was copied off a live page rather than composed.

    The two negatives matter as much: a CSS filename containing "Greenhouse"
    and a link to the vendor's own site are both things a loosened pattern
    would file as a company's board.
    """
    fb = _import_find_boards()
    bad = 0
    for markup, want in FIND_BOARD_CASES:
        got = fb.candidates(markup)
        if want is None:
            if got:
                bad += fail(f"find_boards read {got} out of markup that names "
                            f"no board: {markup[:60]!r}. A pattern loose "
                            f"enough to match a filename will propose one")
        elif want not in got:
            bad += fail(f"find_boards did not find {want} in {markup[:70]!r} "
                        f"- got {got or 'nothing'}. An extractor that misses "
                        f"reports a clean negative for every page it cannot "
                        f"read, which is indistinguishable from there being "
                        f"no board")
    # ITS ORDERING KEY MUST ACTUALLY DISCRIMINATE. The first version sorted
    # the worklist on whether a company currently shows a quota-carrying role
    # - but the worklist only holds companies showing NO roles at all, so the
    # key was False for all 781 and the sort silently collapsed to
    # alphabetical. A run with --limit 30 worked the A's, and every later run
    # would have worked the A's again.
    #
    # A dead sort key does not fail. It produces a plausible-looking run over
    # the wrong slice, forever, and the only tell is that the names are in
    # alphabetical order.
    try:
        rows = fb.worklist()
    except Exception as e:                       # noqa: BLE001
        return bad + fail(f"find_boards.worklist raised {type(e).__name__}")
    if rows and not any(r.get("ever") for r in rows):
        bad += fail(
            f"find_boards orders its {len(rows)} companies on a key that is "
            f"false for every one of them, so a limited run works the same "
            f"alphabetical prefix every time and never reaches the rest")
    # EVERY TYPE IT CAN PROPOSE MUST BE ONE refresh.py FETCHES. Proposing a
    # host nothing can enumerate would wire a company to a new kind of silence.
    ats = _import_ats()
    for kind, _rx in fb.PATTERNS:
        if kind not in ats.FETCHERS:
            bad += fail(f"find_boards can propose {kind!r}, which is not in "
                        f"ats.FETCHERS - refresh.py could never read it")
    return bad


def check_posted_date_is_the_employers() -> int:
    """"Posted" must be the employer's date, and absent when they gave none.

    Every posting on this board carries first_seen, and that is OUR date - the
    day this crawler first saw the row. On 2026-08-31 all 4,442 of them fell
    inside thirteen days, so a Workable req live since 2022-10-27 and one
    opened yesterday were indistinguishable to a reader. Freshness is the
    second question anybody asks after relevance and the board could not
    answer it.

    THE FAILURE THIS GUARDS IS A FALLBACK, not a parse. Filling `posted` from
    first_seen when a board gives no date would look completely reasonable in
    a diff, would make every row appear to have an employer date, and would be
    invisible forever after - our crawler's history published as the
    employer's claim. It is the same shape as reporting "no jobs" for a page
    we could not read.

    ONE FIELD PER BOARD AND NO CHAINS. Greenhouse's updated_at moves whenever
    anything is edited and Recruitee ships created_at, published_at and
    updated_at side by side. "Posted" and "last touched" are different claims,
    and a fallback chain would silently prefer whichever the board happened to
    fill.
    """
    ats = _import_ats()
    bad = 0
    for raw, want in POSTED_CASES:
        got = ats.posted_date(raw)
        if got != want:
            bad += fail(f"ats.posted_date({raw!r}) = {got!r}, expected {want!r}"
                        + ("  - a date it cannot read with certainty must be "
                           "None, never a guess" if want is None else ""))
    src = (ROOT / "scripts" / "ats.py").read_text()
    code = re.sub(r"#.*$", "", src, flags=re.M)
    flat = re.sub(r"\s+", "", code)
    # SEVEN BOARDS, SEVEN CALLS. A fetcher that stops reading its date does not
    # error - it just publishes rows with no employer date, and the column
    # quietly empties for that whole ATS.
    n = flat.count('"posted":posted_date(')
    if n < 7:
        bad += fail(f"only {n} of the 7 structured fetchers read an employer "
                    f"publish date. A fetcher that stops reading one does not "
                    f"error, it just empties the column for that whole board")
    # THE FIELDS THEMSELVES, by name, because reading the wrong one is the
    # defect that looks correct.
    for want, why in (('posted_date(j.get("first_published"))',
                       "greenhouse: updated_at moves on any edit"),
                      ('posted_date(j.get("publishedAt"))', "ashby"),
                      ('posted_date(j.get("createdAt"))', "lever, epoch ms"),
                      ('posted_date(j.get("releasedDate"))', "smartrecruiters"),
                      ('posted_date(j.get("published_on"))', "workable"),
                      ('posted_date(j.get("published_at"))',
                       "recruitee: it also ships created_at and updated_at"),
                      ('posted_date(j.get("published_date"))', "breezy")):
        if re.sub(r"\s+", "", want) not in flat:
            bad += fail(f"a fetcher no longer reads its own publish field "
                        f"({why}) - {want}")
    if "updated_at" in flat.replace('j.get("first_published")', ""):
        bad += fail("something in ats.py reads updated_at as a publish date. "
                    "It moves whenever a posting is edited, so every edited "
                    "req would read as newly posted")
    # AND NO FALLBACK TO OUR OWN DATE, anywhere in the pipeline.
    bsrc = re.sub(r"#.*$", "", (ROOT / "scripts" / "build_board.py").read_text(),
                  flags=re.M)
    bflat = re.sub(r"\s+", "", bsrc)
    for pat in ('"posted"]=row.get("first_seen")', '"posted"]=first_seen',
                'out["posted"]=row.get("posted")orfirst_seen',
                'out["posted"]=row.get("posted")orrow.get("first_seen")'):
        if pat in bflat:
            bad += fail("build_board falls back to first_seen for the posted "
                        "date. That publishes our crawler's history as the "
                        "employer's claim, on every row, invisibly")
    return bad


def check_boards_read_agrees_with_coverage() -> int:
    """"Boards read this run" must be counted from the run, not from the map.

    THIS CARD HAS BEEN WRONG TWICE, and the second wrong answer was written
    while fixing the first.

    It printed len(companies) - every company on file - directly above the
    coverage table saying most of them are blocked, absent or never probed.
    The fix derived it from the split instead, as structured + page only, and
    that is better arithmetic and the same false claim: CLAUDE.md says in as
    many words not to add those two together, because `page only` is a
    worklist for capture, not coverage. 866 of the 1,163 it reported are pages
    a fetcher mostly cannot enumerate. The card called all of them read.

    Both versions failed the same way - describing the MAP and labelling it
    the RUN. So the number is now a counter incremented in build_board's own
    summary loop, where the error off each fetch is already in hand.

    Guarded at source AND in data, because they catch different things and
    only one of them is current. data/board.json changes only after a full
    crawl, and refresh.yml runs this suite BEFORE build_board - so a source
    regression ships that night and the data check fires the next morning
    against damage already done. The data half still earns its place: it is
    the only one that can see the counter drift away from the coverage table
    it sits above.
    """
    bad = 0
    src = (ROOT / "scripts" / "build_board.py").read_text()
    code = re.sub(r"#.*$", "", src, flags=re.M)
    flat = re.sub(r"\s+", "", code)

    if 'payload["boards_read"]=boards_read' not in flat:
        bad += fail("build_board no longer ships boards_read as the counter it "
                    "keeps during the fetch summary. Any expression derived "
                    "from the coverage split describes the map rather than the "
                    "run, which is the mistake this card has already made "
                    "twice")
    # THE TWO SHAPES THAT WERE WRONG BEFORE, refused by name so neither can
    # come back as a tidy-looking one-liner.
    if re.search(r'boards_read"\]?\s*[:=]\s*len\(', code):
        bad += fail("build_board sets boards_read to a len() again - that is "
                    "the original overclaim: every company on file, printed "
                    "under the label 'boards read this run'")
    if re.search(r'boards_read"?\]?\s*=\s*\(?\s*split\.get', code):
        bad += fail("build_board derives boards_read from the coverage split "
                    "again. structured + page only is what we HAVE AN ADDRESS "
                    "FOR, not what answered us, and CLAUDE.md forbids adding "
                    "those two together in a status report")
    if "boards_read+=1" not in flat:
        bad += fail("nothing in build_board increments boards_read, so the "
                    "card would report zero boards read on a run that read "
                    "hundreds")
    # AND THE INCREMENT MUST BE CONDITIONAL. A counter bumped once per company
    # regardless of outcome is len(companies) wearing a loop.
    want_cond = ("ifnotno_boardandnotsharedand(notfetch_errorrendered_ok):"
                 "boards_read+=1")
    if want_cond not in flat:
        bad += fail("boards_read's increment is no longer guarded on all three "
                    "of: we had an address, the board is not somebody else's, "
                    "and either the fetch or the render answered. A counter "
                    "that always increments is the len() overclaim again, one "
                    "loop further in")

    # IT MUST READ THE WIRE, NOT `err`. This is the defect the first version
    # shipped with, and it is invisible from the increment alone: three lines
    # above it set `err = None`, and only the render one means a board
    # answered. The shared-board rule reports one board as two reads; the
    # stored-role promotion reports LAST run's roles as a read this run, which
    # is the same overclaim the card was rewritten to stop making.
    if "fetch_err=err" not in flat:
        bad += fail("build_board no longer captures the fetch error before the "
                    "loop rewrites it, so boards_read counts companies whose "
                    "`err` was cleared by the shared-board rule or by "
                    "promoting roles stored on a previous run")
    # ORDER, NOT JUST PRESENCE. Capturing fetch_err below the shared-board
    # rule records the rewrite instead of the answer, and the file would still
    # contain both lines.
    i_capture = flat.find("fetch_err=err")
    i_zero = flat.find("jobs,err=[],None")
    if i_capture == -1 or (i_zero != -1 and i_capture > i_zero):
        bad += fail("build_board captures fetch_err at or after the point the "
                    "shared-board rule zeroes err, so the capture records the "
                    "rewrite rather than the answer and every shared board "
                    "counts as a board we read")

    board = json.loads((DATA / "board.json").read_text())
    cov = board.get("coverage") or {}
    read = board.get("boards_read")
    if not cov or read is None:
        # PRE-DATES THE COUNTER and cannot be faked. The page renders no card
        # at all in this state rather than a zero, which is the same rule as
        # every other absence here.
        return bad
    orgs = len(board.get("organizations") or [])
    if read == orgs:
        bad += fail(f"boards_read is {read:,}, which is every organization on "
                    f"the board. The coverage split says "
                    f"{cov.get('blocked',0)+cov.get('absent',0)+cov.get('unchecked',0):,} "
                    f"of them are blocked, absent or never probed - the card "
                    f"and the table beneath it cannot both be true")
    have = cov.get("structured", 0) + cov.get("page only", 0)
    if read > have:
        bad += fail(f"boards_read is {read:,} but only {have:,} companies have "
                    f"a board on file at all. We cannot have read more boards "
                    f"than we hold addresses for")
    return bad


def check_stored_roles_are_labelled_as_stale() -> int:
    """A role republished from storage must not look like one read today.

    build_board promotes roles it has on file when a board fails to enumerate,
    which is right - a fetch that failed is not evidence the job is gone, and
    that is this project's founding rule. But promoting them CLEARS `err`, and
    the org record reads `unreadable` from `err` 143 lines further down, so
    the company whose roles are genuinely stale is the one no staleness
    warning can reach. Absence of evidence, reported as evidence of currency.

    So the promotion records its own fact and the card says so. Source-level
    on both halves: the promotion branch must set it, and index.html must
    render it. No test runs a full crawl, and the branch does not fire on
    every run - it fired zero times the night this was written, which is
    exactly why a data-only check would have been green and useless.
    """
    bad = 0
    src = (ROOT / "scripts" / "build_board.py").read_text()
    flat = re.sub(r"\s+", "", re.sub(r"#.*$", "", src, flags=re.M))
    if "from_storage=True" not in flat:
        bad += fail("build_board's stored-role promotion no longer records "
                    "that it fired, so a role republished from a previous run "
                    "is indistinguishable from one read today")
    if '"roles_from_storage":from_storage' not in flat:
        bad += fail("the org record no longer carries roles_from_storage, so "
                    "nothing downstream can tell a reader that a company's "
                    "roles were not confirmed on this run")
    page = (ROOT / "index.html").read_text()
    if "o.roles_from_storage?" not in re.sub(r"\s+", "", page):
        bad += fail("index.html never reads roles_from_storage - the board "
                    "records that a company's roles are stale and the page "
                    "shows them as though they were read today")
    return bad


def check_pay_report_arithmetic() -> int:
    """The pay report publishes figures somebody negotiates against.

    Three defects, all found by audit rather than here, and all the same
    shape: a number that is right most of the time, or a sentinel counted as
    a fact.

    1. _band's percentiles were `v[n // 4]` and `v[(3*n) // 4]` - an
       undecremented rank used as a 0-indexed index. Correct for three n in
       four, which is exactly why it survived. At n = 28 the remote band
       printed a $145k upper quartile where the true one is $130k.
    2. work_mode's cut counted roles.py's "not stated" sentinel as a
       classification. It is a truthy string, so the coverage line read
       "146 of 146 postings carry a work mode" and cleared the 90% threshold
       that attaches the caveat - while 118 of the 146 state no mode at all,
       and a phantom "not stated" band was printed as if it were one.
    3. The exclusion counters sat downstream of `isinstance(min, int)`, and
       an hourly rate is fractional - so two of three hourly postings were
       dropped before the period test and the report said 1 where 3 is true.

    Checked as arithmetic against a known answer, not by reading source.
    """
    pay = _import_pay_report()
    bad = 0
    # NEAREST RANK, against a hand-worked case. 1..28 has its 25th percentile
    # at the 7th value and its 75th at the 21st.
    v = list(range(1, 29))
    b = pay._band(v)
    if (b["p25"], b["p75"]) != (7, 21):
        bad += fail(f"_band on 1..28 gives p25={b['p25']} p75={b['p75']}; "
                    f"nearest-rank quartiles are 7 and 21. An undecremented "
                    f"rank used as an index is right for three n in four and "
                    f"wrong on the fourth")
    for n in range(1, 60):
        vv = list(range(1, n + 1))
        bb = pay._band(vv)
        for label, q, got in (("p25", 0.25, bb["p25"]), ("p75", 0.75, bb["p75"])):
            want = vv[min(max(-((-int(round(q * n * 1000))) // 1000) - 1, 0), n - 1)]
            if got != want:
                bad += fail(f"_band {label} at n={n} is {got}, nearest rank "
                            f"is {want}")
                return bad
    r = pay.report()
    # NO SENTINEL MAY BE A BAND. "not stated" is an absence; printing it as a
    # work mode gives a band of 118 postings the authority of a measurement.
    for cut, bands in (r.get("bands") or {}).items():
        for name in bands:
            if name.strip().lower() in ("not stated", "unknown", "none", "n/a", ""):
                bad += fail(f"pay_report prints a '{name}' band under {cut}. "
                            f"That is an absence sentinel, and a band built "
                            f"from it reports 'we do not know' as a category")
    # AND THE COVERAGE LINE MUST COUNT THE SAME WAY THE BANDS DO.
    for cut, cvg in (r.get("coverage") or {}).items():
        got = (sum(b["n"] for b in (r["bands"].get(cut) or {}).values())
               + (r["not_enough_data"].get(cut) or {}).get("postings", 0))
        if cvg.get("classified") != got:
            bad += fail(f"pay_report says {cvg['classified']} postings carry a "
                        f"{cut} but its bands account for {got} - the coverage "
                        f"line and the table beneath it are counting "
                        f"different sets")
    return bad


def check_built_pages_count_openings_not_rows() -> int:
    """A heading that says N must sit over a listing of N things.

    THE XPLOR CASE, ON TWO SHIPPED PAGE TYPES, FOUND BY AUDIT RATHER THAN BY
    THIS FILE. /c/xplor-recreation.html was headed "17 open roles, 1 of them
    quota-carrying" over forty identical "Account Executive" rows - one
    requisition in forty cities - and closed "60 more are on the board", so a
    reader was told a hundred jobs sat behind a heading that said seventeen.
    /s/mi.html said "12 open roles across 3 companies, 9 quota-carrying"; the
    real Michigan figures are 4 openings and 1 quota-carrying, and all nine of
    those "quota-carrying roles" were the same Xplor req.

    CLAUDE.md states the rule in one line - "The headline counts openings, not
    rows" - and it held on the home banner and the company card while these
    two page types quietly counted rows. The state sentence is also the page's
    meta description, so the inflated number is what a search result renders.

    Checked against the BUILT pages: parse the heading number out and count
    the list items under it. An arithmetic guard on the source would have to
    be rewritten every time the sentence is reworded; this one reads what
    ships.
    """
    out = built()
    if out is None:
        return fail(f"could not build the site to check it: {_BUILT.get('why')}")
    bad = 0
    # WHAT EACH PAGE TYPE LISTS, which is not the same thing on both and is
    # why the first version of this check failed 40 state pages that were
    # correct. A company page lists one item per OPENING, so the "N open
    # roles" heading is the number to match. A state page lists one item per
    # COMPANY, so the number to match is "across N companies" - its "open
    # roles" figure is a total across all of them and is checked separately
    # below, against the openings actually on the page.
    KINDS = (("c", r"(\d+) open role", "openings"),
             ("s", r"across (\d+) compan", "companies"))
    for kind, pat, unit in KINDS:
        d = out / kind
        if not d.exists():
            bad += fail(f"build_site wrote no /{kind}/ pages, so nothing on "
                        f"this page type is being checked at all")
            continue
        for f in sorted(d.glob("*.html")):
            src = f.read_text()
            m = re.search(pat, src)
            if not m:
                continue
            said, listed = int(m.group(1)), src.count("<li>")
            capped = "more are on the board" in src or "more compan" in src
            if not capped and listed and said != listed:
                bad += fail(
                    f"/{kind}/{f.stem} says {said} {unit} over a list of "
                    f"{listed} items. On this page type the list is one item "
                    f"per {unit[:-3] if unit.endswith('ies') else unit[:-1]}"
                    f"y, so the heading is counting posting rows - one "
                    f"requisition in several cities reads as several jobs")
                if bad > 4:
                    return bad          # the shape is established

    # AND THE STATE TOTAL AGAINST THE BOARD ITSELF. /s/mi said "12 open roles
    # ... 9 quota-carrying" where Michigan holds 4 openings and 1 of them
    # carries a quota. Re-derived here from the same board the page was built
    # from, because that sentence is the page's meta description and is what
    # a search result renders.
    SALES = {"gtm", "field"}
    board = json.loads((DATA / "board.json").read_text())
    per: dict = {}
    for p_ in board.get("postings") or []:
        st = (p_.get("office") or {}).get("state")
        if st and p_.get("family") in SALES:
            per.setdefault(st, []).append(p_)
    for st, ps in sorted(per.items()):
        f = out / "s" / f"{st.lower()}.html"
        if not f.exists():
            continue
        src = f.read_text()
        m = re.search(r"(\d+) open role", src)
        if not m:
            continue
        want = len({p_["opening_id"] for p_ in ps})
        if int(m.group(1)) != want:
            bad += fail(f"/s/{st.lower()} says {m.group(1)} open roles; "
                        f"{st} holds {want} distinct openings across "
                        f"{len(ps)} posting rows")
            return bad
        mq = re.search(r"(\d+) quota-carrying", src)
        wantq = len({p_["opening_id"] for p_ in ps if p_.get("quota_carrying")})
        if mq and int(mq.group(1)) != wantq:
            bad += fail(f"/s/{st.lower()} says {mq.group(1)} quota-carrying; "
                        f"{st} holds {wantq} distinct quota-carrying openings")
            return bad
    return bad


def check_momentum_counts_openings_not_rows() -> int:
    """One requisition relisted in a second city is not a company hiring harder.

    THIS SHIPPED A FALSE BADGE ON A REAL COMPANY. Mueller Water Products went
    from 4 quota-carrying posting ROWS to 6 and lit the "hiring hard" chip on
    the public Companies tab. In openings - which is the unit CLAUDE.md says
    the headline counts, and the unit the Xplor case exists to defend - it
    went from 4 to 5. One new requisition, under MIN_ADDED. The sixth row was
    an existing job posted to another city.

    A badge is a leaderboard with one row on it, so it inherits the leaderboard
    rule. Exercised behaviourally against a fixture rather than by matching
    source, because the defect is arithmetic: the same title under two hashes
    must count once.
    """
    mom = _import_momentum()
    ids = {
        # one company, ONE opening, advertised in three cities
        "acme::Account Executive::h1",
        "acme::Account Executive::h2",
        "acme::Account Executive::h3",
        # and a genuinely different seller req
        "acme::Enterprise Account Executive::h9",
    }
    got = mom.per_company(ids, quota_only=True).get("acme")
    if got != 2:
        return fail(f"momentum.per_company reports {got} quota-carrying "
                    f"openings for a company advertising two requisitions, one "
                    f"of them in three cities. Counting rows is what put a "
                    f"'hiring hard' badge on Mueller Water Products for "
                    f"relisting a job it already had open")
    return 0


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

    Exercised against synthetic snapshots so the rule can be driven at will,
    NOT because the live data qualifies nobody. This paragraph used to say it
    did, and that was false and load-bearing: three companies qualified while
    it said so, and one of them - Mueller Water Products - was a false
    positive from counting rows instead of openings. A maintainer reading
    "the rule currently fires on nobody" has no reason to go and look at what
    it is firing on, which is how the false badge stayed up.

    Re-derive the live output rather than trusting any sentence here about
    it: `python3 scripts/momentum.py`.
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
    _b = built()
    if _b is None:
        return fail(f"selftest cannot build the shipped artifacts "
                    f"({_BUILT.get('why')}), so the sitemap checks cannot "
                    f"run")
    sm = _b / "sitemap.xml"
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
        meta_r = _b / "meta-roles.json"
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
            # THIS USED TO ASSERT ZERO IDENTICAL RENDERINGS, and that
            # assertion is what pushed me into a dedupe that deleted 232 real
            # job pages, 193 of them pointing at a different apply url than the
            # row that survived. Two Accela Account Executive reqs at $70-85k
            # and $100-120k, and only the cheaper one reached the sitemap.
            #
            # All 29 identical-rendering groups have distinct apply urls: they
            # are separate requisitions a company posted, not a job we listed
            # twice. The invariant worth holding is the opposite one - every
            # posting has a page and every page is reachable. A wrong check is
            # worse than none, because it argues for the damage.
            board_j = json.loads((DATA / "board.json").read_text())
            want = {p["id"] for p in board_j.get("postings", []) if p.get("id")}
            # UNESCAPE THE XML FIRST. The sitemap runs every loc through
            # html.escape, so an id containing an apostrophe is written
            # &#x27; and comes back out of the regex literally. Six French and
            # possessive titles looked missing when they were present - the
            # check was wrong, not the sitemap, which is worth pausing on given
            # what the last wrong check in this function cost.
            import html as _h
            import urllib.parse as _up
            have = {_up.unquote(_h.unescape(u).split("?role=")[1])
                    for u in urls if "?role=" in u}
            missing = want - have
            if missing:
                bad += fail(f"{len(missing):,} postings have a page a reader "
                            f"can reach and no url in the sitemap. Each is a "
                            f"distinct advertisement with its own apply link, "
                            f"and omitting one is a false absence we made")

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
    _bh = built()
    hdr = (_bh / "_headers") if _bh else pathlib.Path("/nonexistent")
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


# What capture.js must and must not treat as a job link. Every FALSE here is
# a real thing that came back as a job title before the rule was tightened,
# and the two rules CLAUDE.md records are both in this table: a job link is
# the job SEGMENT PLUS SOMETHING AFTER IT (bare /careers matched every nav
# link and returned CHALLENGES, SOLUTIONS and Cookie Preferences), and the
# ATS hosts that put the id straight after the company slug need their own
# arm because no job word appears in the path at all.
CAPTURE_HREF_CASES = [
    # Wellfound, the startup default, added when Spout turned up on it. Its
    # company page is per-company so the link really is only their openings -
    # but the domain sits behind a Cloudflare bot check that 403s every
    # fetcher, which is why capture is the only way these get counted.
    ("https://wellfound.com/jobs/3994536-founding-full-stack-engineer", True),
    ("https://wellfound.com/company/spout-1/jobs/3145678-account-exec", True),
    ("https://wellfound.com/company/spout-1/jobs", False),   # the board itself
    ("https://wellfound.com/company/spout-1", False),        # the profile
    ("https://wellfound.com/discover/startups", False),
    # The structured ATSes, every url copied off data/board.json so no case
    # here rests on a remembered format.
    ("https://job-boards.greenhouse.io/frontlinewildfire/jobs/4384362009", True),
    ("https://jobs.lever.co/everbridge/09236dde-4a28-4f44-8423-dd583b48fe9f", True),
    ("https://jobs.ashbyhq.com/seneca/04229ae5-c83b-410d-ac40-ea6b87f92b74", True),
    # THE REAL SHAPE, taken off our own board rather than guessed. The first
    # version of this case invented apply.workable.com/<company>/j/<code>,
    # which fails the rule and would have been "read" as a capture.js bug.
    # Workable's job urls carry no company segment at all.
    ("https://apply.workable.com/j/C004CE0A35", True),
    ("https://truleo.breezy.hr/p/f352600c2f30-sales-development-representative", True),
    ("https://amilia.recruitee.com/o/sales-development-representative-16", True),
    ("https://acme.com/careers?gh_jid=4012345", True),
    # NAVIGATION, not postings. The whole reason the rule is segment+id.
    ("https://acme.com/careers", False),
    ("https://acme.com/careers/", False),
    ("https://acme.com/jobs", False),
    ("https://acme.com/about", False),
    ("https://acme.com/solutions/public-safety", False),
]


def check_capture_link_rule() -> int:
    """capture.js decides what a job link is, and nothing tested it.

    887 companies have a careers page that produces nothing for a fetcher, and
    capture is how a person turns one of those into counted roles - so this
    regex is the difference between a real posting and a nav item filed as a
    job. CLAUDE.md records two rules it had to learn, and neither was pinned
    anywhere until Wellfound made the file matter again.

    The pattern is READ OUT OF capture.js rather than restated here. A copy
    would be a second source of truth for the one rule this file exists to
    protect, and it would rot exactly the way the alerts vocabulary would
    without check_alert_vocabulary. capture.js is a browser file with no test
    runner, so the pattern is lifted from its own source and compiled with
    Python's re - the constructs in it are common to both engines, and if that
    ever stops being true this check FAILS rather than skipping, because a
    guard that quietly opts out is the thing tonight was spent removing.
    """
    js = (ROOT / "scripts" / "capture.js").read_text()
    i = js.find("const HREF_RE")
    if i < 0:
        return fail("capture.js no longer defines HREF_RE - the harvester has "
                    "no rule for what a job link is")
    seg = js[i:js.index(");", i)]
    parts = re.findall(r"'((?:[^'\\]|\\.)*)'", seg)
    if len(parts) < 2:
        return fail("could not read HREF_RE's pattern out of capture.js; the "
                    "guard cannot check a rule it cannot find")
    pattern = "".join(parts[:-1]) if parts[-1] == "i" else "".join(parts)
    pattern = pattern.replace("\\\\", "\\")
    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        return fail(f"capture.js's HREF_RE no longer compiles under Python re "
                    f"({e}), so this guard can no longer read it. Either the "
                    f"pattern uses a JavaScript-only construct or it is "
                    f"malformed - do not delete this check to make it pass")
    bad = 0
    for url, want in CAPTURE_HREF_CASES:
        got = bool(rx.search(url))
        if got != want:
            bad += fail(
                f"capture.js would {'take' if got else 'skip'} {url} and it "
                f"should {'take' if want else 'skip'} it - "
                + ("a nav link captured as a job posting"
                   if got else "a real posting the harvester would not see"))
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
    # THE LABELS, not only the keys. Matching keys spelled two different ways
    # is the same drift one layer down, and it is sneakier than a missing key:
    # a stored record carries posts_at.py's spelling in rec["label"] and the
    # page falls back to its own map only when that is absent, so one service
    # shows under two names depending on which path rendered the card.
    for k in sorted(in_py & in_page):
        want = posts_at.WHERE[k][0]
        got = re.search(rf'\b{k}\s*:\s*"([^"]*)"', m.group(1))
        if got and got.group(1) != want:
            errors += fail(f"posts_at calls {k!r} {want!r} and index.html "
                           f"calls it {got.group(1)!r} - one service, two names")
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
    # THE PAGE RUNS IN A BROWSER, so it has `location`; node does not. icsFor
    # reads location.hostname for the UID and the description, which is the
    # point - the domain moved on 2026-09-02 and a literal would not have
    # followed it. The harness models the real runtime by supplying the one
    # global a browser always has, BEFORE the extracted code, because the
    # first call to icsFor is forty lines above where a stub would naturally
    # go and it threw there.
    script = ('globalThis.location = { hostname: "sledjobs.test" };\n'
              + html[a:b]) + """
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
/* THE PAGE RUNS IN A BROWSER, so it has `location`. node does not, and
   icsFor reads location.hostname for the UID and the description - which is
   the point: the domain moved on 2026-09-02 and a literal would not have
   followed. The harness has to model the real runtime, so it supplies the
   one global the browser always has. */
globalThis.location = { hostname: "sledjobs.test" };
const one = icsFor({name: "A, B; C", dates: "March 1-4, 2027",
                    city: "Washington, DC", tag: "t"});
out.uidHost = /UID:t@sledjobs\\.test/.test(one);
out.descHost = one.includes("sledjobs.test");
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
    if not got.get("uidHost") or not got.get("descHost"):
        errors += fail("the .ics UID or description does not carry the host the "
                       "page is served from - a literal domain is back, and it "
                       "will not follow the next move")
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

    # THE RULE OUTLIVED TWO ANCHORS, WHICH IS THE ARGUMENT FOR EXECUTING IT.
    # This scanned markup for a counter that the turn-6 rebuild turned into a
    # stat cell, then scanned the stat cell for a branch that moved into
    # coOpenNote. Both times the rule was right and the anchor was stale. The
    # rule itself has never changed: a zero from a board nobody could read
    # must not carry evidence claiming somebody looked. It is asserted against
    # the real function now, in check_company_counts_are_roles_not_postings,
    # which runs coOpenNote under node over both kinds of zero.
    if html.find("function coOpenNote(") < 0:
        errors += fail("index.html: coOpenNote is gone. It is the only place "
                       "that decides what the open-roles count is allowed to "
                       "claim; if it was renamed, re-point the checks rather "
                       "than dropping the rule")
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
    # THE WHOLE FUNCTION, not a fixed byte window. This read html[i:i+2600]
    # of an 8,280-character function, so it was checking the first third and
    # passing on where the branch happened to sit. Adding a comment above that
    # branch failed the suite while the branch was still there, which is the
    # tell: it measured layout, not behaviour.
    j = html.find("\nfunction ", i + 10)
    body = html[i:j if j > 0 else len(html)]
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


def check_workday_pages_to_the_end() -> int:
    """Workday hands back 20 rows a page and WRAPS past the end, not an empty page.

    Both Workday fetchers used to post {"limit": 20, "offset": 0} once per
    search term and read the reply, so every tenant was cut to the first 20
    hits of each query however many it advertised. Motorola's own response
    reported total=161 for "account executive" and 362 for "sales"; the board
    published 38 postings for the company. Paging the same queries properly
    returns 396, including "Channel Sales Executive (Chicago, Western
    Suburbs)" and a dozen more Channel Account Managers - quota-carrying GTM
    roles, which is the product.

    THE TRAP, and the reason this check exists rather than a comment. Asking
    for an offset past the end does not return nothing. Verified live on
    2026-09-01: offsets 180, 200 and 300 each returned 20 rows byte-identical
    to offset 0. A paginator written the obvious way - "stop on an empty
    page" - therefore never stops, and re-requests page one against somebody
    else's server in a loop. The stop condition has to be a page that adds no
    path already seen.

    `total` cannot be the bound either: the same walk saw total=161 at offset
    0, then total=0 at every offset from 20 to 160 while returning 20 real
    rows each time, then 161 again once it wrapped.

    Driven offline against a fixture that reproduces the wrap, plus a source
    check for the literal that was the bug, because the regression is someone
    "simplifying" the loop back to a single request.
    """
    errors = 0
    key = lambda j: j["externalPath"]

    def board(total):
        def page(off):
            if off >= total:          # what Workday actually does past the end
                off = 0
            return [{"externalPath": f"/job/{i}"}
                    for i in range(off, min(off + ats.WD_PAGE, total))]
        return page

    for total in (0, 1, 20, 21, 161, 400):
        seen_offsets = []
        raw = board(total)

        def counted(off, _raw=raw, _log=seen_offsets):
            _log.append(off)
            return _raw(off)

        rows = ats._paged(counted, key)
        if len(rows) != total:
            print(f"  FAIL: a {total}-posting Workday board yielded "
                  f"{len(rows)} rows")
            errors += 1
        if len({key(r) for r in rows}) != total:
            print(f"  FAIL: duplicate rows survived on a {total}-row board")
            errors += 1
        # THE ROW COUNT ALONE DOES NOT PROVE THE STOP CONDITION. Delete the
        # wrap-around break and every extra page returns rows already seen, so
        # the totals still come out right - while the walk makes sixty requests
        # instead of ten against somebody else's server and then reports itself
        # TRUNCATED because the loop ran to its ceiling. Traffic is the thing
        # being guarded here, so traffic is what gets asserted.
        want = 1 if not total else math.ceil(total / ats.WD_PAGE) + 1
        if len(seen_offsets) != want:
            print(f"  FAIL: a {total}-posting Workday board took "
                  f"{len(seen_offsets)} requests, expected {want} - the "
                  f"wrap-around stop is not firing")
            errors += 1

    # The wrap must not become an infinite loop, and the ceiling must announce
    # itself rather than quietly returning a truncated list.
    calls = []

    def never_repeats(off):
        calls.append(off)
        return [{"externalPath": f"/job/{off}/{i}"} for i in range(ats.WD_PAGE)]

    rows = ats._paged(never_repeats, key, label="selftest")
    if len(calls) != ats.WD_MAX_PAGES:
        print(f"  FAIL: a board that never repeats made {len(calls)} requests, "
              f"ceiling is {ats.WD_MAX_PAGES}")
        errors += 1

    # The bug, as a source shape. Either Workday function posting a literal
    # zero offset is the defect coming back.
    src = (ROOT / "scripts" / "ats.py").read_text()
    for name in ("def fetch_workday(", "def _workday_jobs("):
        i = src.find(name)
        if i < 0:
            print(f"  FAIL: {name} is gone from ats.py")
            errors += 1
            continue
        body = src[i:i + 1800]
        if '"offset": 0' in body:
            print(f"  FAIL: {name} posts a literal offset of 0 again - "
                  f"that is the un-paged fetch this check exists to stop")
            errors += 1
        if "_paged(" not in body:
            print(f"  FAIL: {name} no longer pages through _paged()")
            errors += 1
    return errors


MAIL_MARKS = [
    ("Outlook DPI",          "o:PixelsPerInch"),
    ("mso font guard",       "body,table,td,a,span,div,p"),
    ("Segoe fallback",       "'Segoe UI',Arial,sans-serif !important"),
    ("iOS reformat guard",   "x-apple-disable-message-reformatting"),
    ("Outlook.com dark",     "[data-ogsc]"),
    ("dark-mode media",      "prefers-color-scheme:dark"),
    ("preheader hidden",     "mso-hide:all"),
    ("Penguin band",         'bgcolor="#1F2536"'),
    ("Belly plate 52px",     'bgcolor="#FAF7F0" width="52" height="52"'),
    ("Beak rule",            'bgcolor="#F5A623"'),
    ("Frost rule",           "#C9DCE8"),
    ("band mute token",      "#9FB3C4"),
    ("Badge button",         'bgcolor="#0B57C4"'),
    ("the mascot",           "head-on-the-hunt.png"),
    ("live-text wordmark",   'letter-spacing:.02em'),
    ("mobile kicker rule",   ".kicker{font-size:10px"),
]


def check_mail_shell() -> int:
    """The email shell exists twice, in two languages, and will therefore rot.

    functions/api/alerts.js sends the confirmation and the settings email from
    a Cloudflare Worker; scripts/digest.py sends the recurring digest from CI.
    A Worker cannot import Python and the script cannot import the Worker, so
    the shell - the Penguin band, the Belly plate, the Beak rule, the Outlook
    guards - is written out in both. That is the same forced duplication as
    functions/_brand.js and gets the same treatment: a guard, because the
    failure is SILENT. Nothing errors when they drift. The confirmation email
    and the digest simply stop looking like the same product, and the person
    who changed one of them never sees the other.

    THE MARKS BELOW ARE NOT DECORATION, which is why drift matters:

    - `o:PixelsPerInch` - without it Outlook 2016 on a 120/144 DPI display
      rescales every px dimension and the mascot no longer fits its plate.
    - The mso font block - Word does not walk a font stack. It takes the first
      family, fails to find Archivo, and renders the whole email in Times New
      Roman. The selector list must include div and p, because the type here
      is set on divs.
    - `x-apple-disable-message-reformatting` - stops iOS Mail's own scaling.
    - `[data-ogsc]` - Outlook.com's dark mode ignores prefers-color-scheme and
      needs its own overrides or the ink inverts and the ground does not.
    - The bgcolor attributes - a client that reads no CSS at all still gets
      the palette, which is the whole images-off strategy.

    Also asserted: no SVG (Gmail strips it entirely) and no flex or grid
    (Word drops both), in either file.
    """
    errors = 0
    js = (ROOT / "functions" / "api" / "alerts.js").read_text()
    # digest.py builds its shell in an f-string, where a literal CSS brace has
    # to be written doubled. Comparing raw source would therefore report every
    # CSS rule as drift, which is the guard crying wolf rather than a finding -
    # so the doubling is undone before the two are held against each other.
    # This compares what each file WILL EMIT, which is the thing that has to
    # match; it is not a claim that the two files are typed identically.
    py = ((ROOT / "scripts" / "digest.py").read_text()
          .replace("{{", "{").replace("}}", "}"))

    marks = MAIL_MARKS
    for name, mark in marks:
        in_js, in_py = mark in js, mark in py
        if not (in_js and in_py):
            where = "alerts.js" if in_py else "digest.py"
            print(f"  FAIL: the email shell's {name} ({mark!r}) is missing from "
                  f"{where} - the two mail shells have drifted")
            errors += 1

    for label, src in (("alerts.js", js), ("digest.py", py)):
        # Only the mail shell is being judged here, so look at the part of the
        # file that builds email rather than the whole module.
        i = src.find("the shared email shell")
        if i < 0:
            print(f"  FAIL: {label} no longer carries the shared email shell")
            errors += 1
            continue
        shell_src = src[i:]
        if ".svg" in shell_src:
            print(f"  FAIL: {label}'s email shell references an SVG - Gmail "
                  f"strips SVG entirely and the mark would simply not appear")
            errors += 1
        for css in ("display:flex", "display:grid", "grid-template"):
            if css in shell_src:
                print(f"  FAIL: {label}'s email shell uses {css} - Outlook "
                      f"renders through Word and drops it")
                errors += 1
    return errors


def check_fetchers_page_and_do_not_fake_zeros() -> int:
    """Three fetchers took the first page of a board and called it the board.

    Measured live on 2026-09-01, all three against real employers:

      SmartRecruiters  asked limit=100 once. Xplor answers totalFound=251 to
                       that same request. 100 published, 251 available.
      iCIMS            read three pages on a comment claiming "portals page
                       at 50". Bruker's serves NINETEEN, over eleven pages.
                       54 published, 204 available. It also deduped on the
                       title after stripping the req id - the exact rule
                       fetch_html_titles documents as the one never to use -
                       collapsing 204 links to 177 names.
      JazzHR           capped the title at 90 characters of UNSTRIPPED anchor
                       text, and JazzHR indents its markup by 77. Thirteen
                       characters for a job title. Across twelve boards: 51
                       real postings, 2 published. VGSI advertises 31 and
                       showed none.

    THE JAZZHR HALF IS THE ONE THAT MATTERS MOST, and it is not about counts.
    That fetcher returned [] whatever happened, so a board it could not read
    published as a company with "0 open roles" - a false absence, the one
    error this project says never corrects itself. Three of those boards do
    say they have nothing open, and that is a different fact from silence.
    Driven here against synthetic pages, because the difference between "they
    have no openings" and "we could not read it" is the whole point.
    """
    errors = 0

    # --- JazzHR, driven for real against stubbed pages --------------------
    PAD = " " * 77
    def page(anchors, extra=""):
        rows = "".join(
            f'<a class="x" href="https://acme.applytojob.com/apply/{i}">'
            f'{PAD}{a}{PAD}</a>' for i, a in enumerate(anchors))
        return f"<html><body>{rows}{extra}</body></html>"

    class Stub:
        def __init__(self, text): self.text = text
    real_get = ats._get
    try:
        cases = [
            ("a long title survives the padding",
             page(["Senior Account Executive, State and Local Government"]), 1, False),
            ("a short title still survives",
             page(["Sales Manager"]), 1, False),
            ("a genuinely empty board reports zero",
             page([], "<p>There are no open positions at this time.</p>"), 0, False),
            ("an unreadable board RAISES rather than reporting zero",
             "<html><body><p>Something else entirely</p></body></html>", 0, True),
        ]
        for label, html, want, should_raise in cases:
            ats._get = lambda *_a, **_k: Stub(html)
            try:
                got = len(ats.fetch_jazzhr("acme"))
                if should_raise:
                    print(f"  FAIL: jazzhr - {label}: returned {got} instead of "
                          f"raising. An unread board published as a zero is the "
                          f"false absence this check exists to stop")
                    errors += 1
                elif got != want:
                    print(f"  FAIL: jazzhr - {label}: got {got}, expected {want}")
                    errors += 1
            except ats.AtsError:
                if not should_raise:
                    print(f"  FAIL: jazzhr - {label}: raised, expected {want} row(s)")
                    errors += 1
    finally:
        ats._get = real_get

    # --- the paging fixes, as source shapes -------------------------------
    src = (ROOT / "scripts" / "ats.py").read_text()

    def body(name, n=2600):
        """The function's CODE, with its comments removed.

        Not a nicety. The first version of this check searched raw source and
        failed instantly on fetch_jazzhr - because that function's comment
        explains the bug by quoting the old pattern, and the guard matched the
        explanation. This file has caught the same shape three separate times
        (a `||` fallback found in a string, an ARMA "writes" comment, an
        orphan check matching a module's own name in prose), so a source-level
        assertion here reads code or it reads nothing.
        """
        i = src.find(name)
        if i < 0:
            return ""
        window = src[i:i + n]
        return "\n".join(re.sub(r"#.*$", "", ln) for ln in window.splitlines())

    sr = body("def fetch_smartrecruiters(")
    if not sr:
        print("  FAIL: fetch_smartrecruiters is gone from ats.py"); errors += 1
    else:
        if "_paged(" not in sr:
            print("  FAIL: fetch_smartrecruiters no longer pages - it is back to "
                  "taking SmartRecruiters' maximum page size as the board size")
            errors += 1
        if "postings?limit=100\"" in sr:
            print("  FAIL: fetch_smartrecruiters posts a bare limit=100 with no "
                  "offset again")
            errors += 1

    ic = body("def fetch_icims(")
    if not ic:
        print("  FAIL: fetch_icims is gone from ats.py"); errors += 1
    else:
        if "range(3)" in ic:
            print("  FAIL: fetch_icims is back to a three-page cap - Bruker's "
                  "portal is eleven pages and serves 19 a page, not 50")
            errors += 1
        if "_paged(" not in ic:
            print("  FAIL: fetch_icims no longer pages through _paged()")
            errors += 1
        if "if title and title not in seen" in ic:
            print("  FAIL: fetch_icims dedups on the title again - that deletes "
                  "27 distinct requisitions on Bruker alone")
            errors += 1

    jz = body("def fetch_jazzhr(")
    if "{4,90}" in jz:
        print("  FAIL: fetch_jazzhr measures the length inside the pattern "
              "again, which counts JazzHR's 77 characters of padding")
        errors += 1
    if "raise AtsError" not in jz:
        print("  FAIL: fetch_jazzhr no longer raises on an unreadable board, so "
              "it will publish a false 'no open roles'")
        errors += 1
    return errors


def check_the_crawler_paces_itself() -> int:
    """1,570 requests at servers nobody here owns, and nothing paced them.

    build_board runs a ThreadPoolExecutor over 1,140 boards, and since the
    paging fixes a single Workday tenant serves ten pages and an iCIMS portal
    eleven. Before HOST_PAUSE none of that was spaced at all - it went out as
    fast as the network allowed. 79 companies already sit behind a bot wall
    that refuses identified crawlers on the first request; those were never
    earned, and arriving in a burst is how more get earned.

    THE LOCK MUST BE HELD ACROSS THE SLEEP. That is the whole design and it is
    the thing worth a test: release before sleeping and two threads both read a
    stale timestamp, both decide they may go, and both hit the host together -
    the exact burst this exists to prevent, and invisible in any single-threaded
    check. So this drives it with real threads.

    Per HOST, not globally, or the gate would serialise 866 unrelated company
    websites behind each other and turn a 15-minute crawl into hours.
    """
    errors = 0
    pause, real = 0.05, ats.HOST_PAUSE
    ats.HOST_PAUSE = pause
    try:
        # Same host from eight threads must come out spaced, not in a burst.
        ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear()
        stamps = []
        lock = threading.Lock()

        def hit(u):
            ats._host_gate(u)
            with lock:
                stamps.append(time.monotonic())

        threads = [threading.Thread(target=hit, args=("https://api.example.com/x",))
                   for _ in range(8)]
        for th in threads: th.start()
        for th in threads: th.join()
        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        if gaps and min(gaps) < pause * 0.8:
            print(f"  FAIL: eight concurrent requests to one host came "
                  f"{min(gaps):.3f}s apart, pause is {pause}s - the gate is not "
                  f"holding its lock across the sleep and bursts get through")
            errors += 1

        # Different hosts must NOT queue behind each other.
        #
        # COMPARED AGAINST THE SAME-HOST CASE, not against a wall clock. The
        # first version asserted eight different hosts finish inside one
        # pause, which is true on an idle machine and false on a busy one:
        # it failed on 2026-09-02 with a load average of 7.7 while six
        # research agents ran, and passed four times out of four the moment
        # they stopped. A guard that fails under load is a guard somebody
        # turns off. The RATIO is what the design actually claims - per-host
        # gating means eight hosts cost about one wait and eight same-host
        # calls cost about seven - and it holds however slow the machine is.
        ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear()
        t0 = time.monotonic()
        threads = [threading.Thread(target=ats._host_gate,
                                    args=(f"https://h{i}.example.com/x",))
                   for i in range(8)]
        for th in threads: th.start()
        for th in threads: th.join()
        spread = time.monotonic() - t0
        same = stamps[-1] - stamps[0] if len(stamps) > 1 else pause * 7
        if spread > same / 2:
            print(f"  FAIL: eight DIFFERENT hosts took {spread:.3f}s against "
                  f"{same:.3f}s for eight calls to ONE host - the gate is "
                  f"global rather than per-host, which would turn the crawl "
                  f"into hours")
            errors += 1
    finally:
        ats.HOST_PAUSE = real
        ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear()

    # --- a 429 is an instruction, not a refusal ---------------------------
    #
    # _get used to turn "slow down" into an AtsError spelled exactly like a
    # 404, so the discovery log recorded a cooperative server as blocked and
    # the 7-day requeue asked again at the same speed, having learned nothing.
    # Now it backs the host off by what the server asked for - and per host,
    # or one rude server would slow down 1,139 innocent ones.
    class _Resp:
        def __init__(self, code, headers=None):
            self.status_code, self.headers, self.text = code, headers or {}, ""

    real_get, real_cache, real_pause = ats.requests.get, ats.HTTP_CACHE, ats.HOST_PAUSE
    ats.HOST_PAUSE, ats.HTTP_CACHE = 0.02, None
    ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear()
    try:
        ats.requests.get = lambda *a, **k: _Resp(429, {"Retry-After": "1"})
        try:
            ats._get("https://limited.test/a")
            print("  FAIL: a 429 did not raise at all")
            errors += 1
        except ats.RateLimited:
            pass
        except ats.AtsError:
            print("  FAIL: a 429 raises a plain AtsError, so a server saying "
                  "'slow down' is recorded identically to a page that is gone")
            errors += 1

        ats.requests.get = lambda *a, **k: _Resp(200)
        t0 = time.monotonic(); ats._get("https://limited.test/b")
        if time.monotonic() - t0 < 0.6:
            print("  FAIL: the next request to a host that just returned 429 "
                  "went straight back in - Retry-After was read and ignored")
            errors += 1
        t0 = time.monotonic(); ats._get("https://unrelated.test/a")
        if time.monotonic() - t0 > 0.5:
            print("  FAIL: one host's 429 backed off an unrelated host - the "
                  "backoff is global rather than per-host")
            errors += 1

        ats.requests.get = lambda *a, **k: _Resp(404)
        try:
            ats._get("https://gone.test/a")
        except ats.RateLimited:
            print("  FAIL: a 404 is being reported as a rate limit")
            errors += 1
        except ats.AtsError:
            pass
    finally:
        ats.requests.get, ats.HTTP_CACHE, ats.HOST_PAUSE = real_get, real_cache, real_pause
        ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear()

    # Both spellings of Retry-After. A parser that reads only the integer form
    # treats an HTTP-date as no header at all, silently ignoring the server.
    from email.utils import format_datetime
    later = format_datetime(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60))
    if ats._retry_after("30") != 30:
        print("  FAIL: Retry-After in seconds is not being read")
        errors += 1
    if not (50 <= (ats._retry_after(later) or 0) <= 61):
        print("  FAIL: Retry-After as an HTTP-date is not being read, so a "
              "server using that spelling is silently ignored")
        errors += 1
    for junk in ("banana", "", None):
        if ats._retry_after(junk) is not None:
            print(f"  FAIL: Retry-After {junk!r} should read as absent")
            errors += 1

    return errors


def check_embedded_wiring() -> int:
    """The three ways wiring a discovered board publishes somebody else's jobs.

    `data/embedded_ats.json` held 82 boards found behind careers pages, 79 of
    them never connected. Connecting them is the cheapest coverage this project
    will ever get - no new requests, and every one moves a company off a
    fragile HTML fetch onto an API no bot wall refuses. It is also the single
    easiest place to publish a false "Yes", which is the error CLAUDE.md says
    never corrects itself.

    Three gates, and each one was earned by a real entry in that file:

    IDENTITY MISMATCH. Prepared's careers page names greenhouse/axon - 502
    postings. Wiring that record publishes Axon's entire requisition list under
    Prepared's name.

    ONE BOARD, SEVERAL CLAIMANTS. ashby/opengov is named by both Cartegraph and
    OpenGov; one Paylocity board by Catalis, Matterhorn, QScend AND nCourt.
    Wiring them all publishes one company's jobs four times and invents a
    leader on the board. These are acquisitions, and ownership is a person's
    call.

    A SLUG THAT IS NOT THE COMPANY'S NAME. 65 of the 82 carry identity
    "unknown", meaning the board never said whose it was. Without this gate the
    first draft offered to wire ICSolutions to careers-tkcholdings (its parent,
    310 postings), Veovo to gentrack (which acquired it), Sparkrock to
    Ionicpartners and Careers In Government to skagit-911.

    The gate must not be so strict it refuses a company its own board:
    stripping the ATS's own prefix is what lets careers-viapath match ViaPath.
    Both directions are asserted, because a guard that refuses everything is
    not a guard.
    """
    errors = 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import wire_embedded as w
    PAY = "https://recruiting.paylocity.com/recruiting/jobs/All/0f0f0f0f-0000-0000-0000-000000000000/"

    for name, ref, want in [
        ("ViaPath Technologies", "careers-viapath", True),
        ("JAGGAER", "careers-jaggaer", True),
        ("EBSCO Information Services", "careers-ebscoind", True),
        ("Brinc", "brinc", True),
        ("ICSolutions", "careers-tkcholdings", False),
        ("Veovo", "gentrack", False),
        ("Sparkrock", "Ionicpartners", False),
        ("Careers In Government", "skagit-911", False),
        # AN ADDRESS IS A SLUG TOO. Paylocity's ref is the whole recruiting
        # URL and it ends in the tenant's registered name. The first version
        # returned True for any http ref, and the board carried AEM's 29
        # requisitions as Earth Networks' and Liberty Vote's as Dominion's.
        ("Earth Networks", PAY + "AEM", False),
        ("Dominion Voting Systems", PAY + "Liberty-Vote-USA-Inc", False),
        ("American Legal Publishing", PAY + "General-Code", False),
        ("ICC Innovation", PAY + "INTERNATIONAL-CODE-COUNCIL-INC", False),
        # and the legal form is not a name - these are their own boards
        ("ES&S (Election Systems & Software)", PAY + "Election-Systems-Software-LLC", True),
        ("Bigbelly", PAY + "Big-Belly-Solar-LLC", True),
        ("Edlio", PAY + "Edlio-LLC", True),
        ("Brinc", "brinc", True),               # "inc" inside a name is not a suffix
    ]:
        if w.resembles(name, ref) != want:
            verb = "refused" if want else "accepted"
            print(f"  FAIL: the slug check {verb} {name!r} -> {ref!r}. "
                  f"{'A company must not lose its own board to an ATS prefix' if want else 'That is a parent board being published as this company'}")
            errors += 1

    if not w.resembles("DZS", PAY + "Zhone-Technologies-Inc", ["Zhone Technologies"]):
        print("  FAIL: a former name a person already recorded in also_known_as "
              "is being doubted again")
        errors += 1
    src_w = (ROOT / "scripts" / "wire_embedded.py").read_text()
    main_src = src_w[src_w.find("def main("):]
    if "save_companies(" not in main_src or 'with_suffix(".tmp")' in main_src:
        print("  FAIL: wire_embedded writes companies.json directly again - no "
              "before-image, nothing for admin_undo.py to take back")
        errors += 1

    # The triage itself, on synthetic entries shaped like the real file.
    entries = [
        {"id": "a", "name": "Acme", "identity": "MISMATCH",
         "found": {"type": "greenhouse", "ref": "othercorp"}},
        {"id": "b", "name": "Beta", "identity": "unknown",
         "found": {"type": "ashby", "ref": "shared"}},
        {"id": "c", "name": "Gamma", "identity": "unknown",
         "found": {"type": "ashby", "ref": "shared"}},
        {"id": "d", "name": "Delta", "identity": "unknown",
         "found": {"type": "ashby", "ref": "delta"}},
        # ALONE ON ITS BOARD, AND THE SLUG IS SOMEBODY ELSE'S NAME. Without
        # this entry the fixture does not exercise the slug gate at all: every
        # other candidate here resembles its own company, so deleting the gate
        # changed nothing and the mutation passed. This is the ICSolutions ->
        # careers-tkcholdings shape, which is how a parent's board gets
        # published as a subsidiary's.
        {"id": "e", "name": "Epsilon", "identity": "unknown",
         "found": {"type": "ashby", "ref": "someparentco"}},
        # THE SAME SHAPE AS AN ADDRESS. Without this entry the slug gate was
        # never exercised on a URL ref, which is the 35 Paylocity entries.
        {"id": "f", "name": "Zeta", "identity": "unknown",
         "found": {"type": "paylocity", "ref": PAY + "Some-Parent-Co-LLC"}},
    ]
    by_id = {e["id"]: {"id": e["id"], "ats": {"type": "html"}} for e in entries}
    clean, mismatch, refused = w.triage(entries, by_id)
    got = {e["id"] for e in clean}
    if got != {"d"}:
        print(f"  FAIL: triage would wire {sorted(got)}; only 'd' is safe - 'a' "
              f"names another company and 'b'/'c' claim one board between them")
        errors += 1
    if len(mismatch) != 1:
        print(f"  FAIL: triage found {len(mismatch)} mismatch(es), expected 1")
        errors += 1
    if {e["id"] for e in refused} != {"b", "c", "e", "f"}:
        print(f"  FAIL: refused should be both claimants of the shared board "
              f"AND the two whose slug names another company - one a slug, "
              f"one an address - got {sorted(e['id'] for e in refused)}")
        errors += 1

    # A company that already has a real board is never overruled by this.
    by_id["d"]["ats"] = {"type": "greenhouse", "ref": "delta"}
    clean, _, _ = w.triage(entries, by_id)
    if any(e["id"] == "d" for e in clean):
        print("  FAIL: triage would rewrite a company that already has a "
              "structured board - this script connects findings, it does not "
              "overrule a person who already answered")
        errors += 1
    return errors


def check_capture_parity() -> int:
    """The capture extractor exists twice and had drifted BOTH ways.

    `extension/capture.js` and `scripts/capture.js` are copy-pasted, because a
    bookmarklet has to be one self-contained string and an extension has to be
    files. That is forced duplication, and this repo already guards two others
    for the same reason - check_brand holds _brand.js against brand.json,
    check_alert_vocabulary holds alerts.js against roles.py. This one was
    missing, and both copies had quietly gained things the other lacked:

      the extension had  |currentJobId in HREF_RE and |dismiss|report in
                         NOT_RE, and the whole single-posting JD mode
      the bookmarklet had the sibling-cell location fallback, which had NEVER
                         existed in the extension - so the extension silently
                         dropped the location on every board that puts it in a
                         cell beside the anchor rather than inside it

    Quote style is not drift: a bookmarklet lives inside a javascript: URL and
    uses single quotes. So the patterns are compared with quotes, whitespace
    and grouping parens removed - what is compared is what the regex MEANS.

    THE ANCHOR IS CHECKED SEPARATELY AND THAT IS NOT PEDANTRY. Merging
    |dismiss|report into the bookmarklet put it outside the anchored group:
    `^(apply|load more)$|dismiss|report` matches any title CONTAINING
    "report", which would have silently deleted every Reporting Analyst and
    Report Developer on every board captured. A title filter that is not
    anchored is a title deleter.
    """
    errors = 0
    ext = (ROOT / "extension" / "capture.js").read_text()
    bm = (ROOT / "scripts" / "capture.js").read_text()

    def pattern(src, name):
        m = re.search(rf"const {name}\s*=\s*(.*?);\n", src, re.S)
        if not m:
            return None
        s = re.sub(r"new RegExp\(|,\s*[\"']i[\"']|//[^\n]*", "", m.group(1))
        return re.sub(r"[\s\"'+()]", "", s)

    for name in ("HREF_RE", "NOT_RE", "CHIP_RE", "LOC_RE"):
        a, b = pattern(ext, name), pattern(bm, name)
        if a is None or b is None:
            print(f"  FAIL: {name} is missing from "
                  f"{'extension' if a is None else 'scripts'}/capture.js")
            errors += 1
        elif a != b:
            print(f"  FAIL: {name} has drifted between the two capture copies. "
                  f"One of them is now finding jobs the other cannot.")
            errors += 1

    for label, needle, why in [
        ("the link dedup", "seen.indexOf(href)",
         "keying on the title deletes the second of two same-named reqs"),
        ("the sibling-cell location fallback", "closest(",
         "without it the location is dropped on every board that puts it "
         "beside the anchor rather than inside it"),
        ("the textContent fallback", "textContent",
         "a CSS-collapsed anchor has no innerText and its job is lost"),
    ]:
        for name, src in (("extension/capture.js", ext), ("scripts/capture.js", bm)):
            if needle not in src:
                print(f"  FAIL: {name} lost {label} - {why}")
                errors += 1

    # An unanchored title filter is a title deleter, so prove it stays anchored
    # AS ONE GROUP. The first version of this stripped the parens before it
    # looked, so `^(apply|load more)$|dismiss|report` read as ^...$ and passed
    # - while matching any title CONTAINING "report". Grouping is kept here,
    # and the group that opens after ^ has to be the one that closes before $.
    def shape(src, name):
        m = re.search(rf"const {name}\s*=\s*(.*?);\n", src, re.S)
        if not m:
            return ""
        raw = m.group(1).strip()
        called = raw.startswith("new RegExp(")
        s = re.sub(r"new RegExp\(|,\s*[\"']i[\"']|//[^\n]*", "", raw)
        s = re.sub(r"[\s\"'+]", "", s)
        if called and s.endswith(")"):
            s = s[:-1]                      # the RegExp call's own paren
        elif s.startswith("/"):
            s = re.sub(r"^/|/[a-z]*$", "", s)   # a /literal/i
        return s

    def one_anchored_group(pat):
        if not (pat.startswith("^(") and pat.endswith(")$")):
            return False
        depth, i, end = 0, 1, len(pat) - 2
        while i < end:
            ch = pat[i]
            if ch == "\\":                    # an escaped char is not grouping
                i += 3 if pat[i + 1:i + 2] == "\\" else 2
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return False            # the top group closed early
            i += 1
        return depth == 1

    # The test itself, against the shape that got through and a nested one.
    if one_anchored_group("^(apply|loadmore)$|dismiss|report") or \
            one_anchored_group("^(apply)$|(report)$") or \
            not one_anchored_group("^(apply|join(us|ourteam)|\\(x\\))$"):
        print("  FAIL: the anchor test cannot tell one anchored group from a "
              "trailing alternative - it is the guard that is broken")
        errors += 1
    for name, src in (("extension/capture.js", ext), ("scripts/capture.js", bm)):
        if not one_anchored_group(shape(src, "NOT_RE")):
            print(f"  FAIL: {name}'s NOT_RE is not one group anchored end to end "
                  f"- an alternative outside the group matches any title "
                  f"CONTAINING that word, and 'report' alone deletes every "
                  f"Reporting Analyst on a board")
            errors += 1

    # Two shadow-stylesheet declarations were invalid CSS and silently dropped:
    # `inherit` is a CSS-wide keyword and cannot sit inside a shorthand.
    css_i = ext.find("css.textContent")
    css = ext[css_i:ext.find("`;", css_i)] if css_i > 0 else ""
    if re.search(r"\bfont:\s*[^;]*\binherit\b", css):
        print("  FAIL: extension/capture.js sets `font: ... inherit` - that "
              "shorthand is invalid CSS and the whole declaration is dropped")
        errors += 1
    if 'kind === "html" && c.postings > 0' not in ext:
        print("  FAIL: verdictOf() no longer distinguishes an html board the "
              "crawler already reads from one it cannot - it will tell the "
              "person to capture what is already on the board")
        errors += 1
    return errors


def check_extension_icons() -> int:
    """A manifest naming an icon that is not there stops Chrome loading at all.

    Most manifest mistakes degrade; this one refuses. "Load unpacked" fails
    outright with a file-not-found, and the moment that happens is the moment
    somebody is standing at a conference trying to install this to capture a
    floor. So the files are checked against the manifest rather than assumed.

    BOTH KEYS ARE REQUIRED AND THEY ARE NOT THE SAME THING. `icons` dresses
    the extensions page, the menu and the store listing. `action.default_icon`
    is the toolbar button. A manifest carrying only the first looks correct
    everywhere except the one place the owner actually clicks - the toolbar
    still shows a grey square - which is the whole failure this replaced.

    The declared size is checked against the file's REAL pixel width, not its
    name. A 512px image called icon-16.png loads happily and renders as mush,
    and nothing anywhere would say so.
    """
    import struct

    errors = 0
    ext = ROOT / "extension"
    try:
        man = json.loads((ext / "manifest.json").read_text())
    except Exception as exc:                            # noqa: BLE001
        print(f"  FAIL: extension/manifest.json does not parse ({exc})")
        return 1

    def png_width(path):
        """Width from the IHDR chunk. No dependency, and it reads the FILE."""
        with path.open("rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">I", head[16:20])[0]

    for key in ("icons", "action.default_icon"):
        block = man.get("icons") if key == "icons" else (man.get("action") or {}).get("default_icon")
        if not block:
            where = ("the extensions page and menu" if key == "icons"
                     else "the TOOLBAR BUTTON, which is the one the owner clicks")
            print(f"  FAIL: extension/manifest.json declares no {key} - "
                  f"Chrome will draw a grey puzzle piece on {where}")
            errors += 1
            continue
        for size, rel in block.items():
            f = ext / rel
            if not f.exists():
                print(f"  FAIL: {key} names {rel}, which is not in extension/ - "
                      f"Chrome refuses to load an unpacked extension whose "
                      f"manifest points at a missing file")
                errors += 1
                continue
            got = png_width(f)
            if got is None:
                print(f"  FAIL: {rel} is not a PNG")
                errors += 1
            elif got != int(size):
                print(f"  FAIL: {rel} is declared as {size}px and is actually "
                      f"{got}px - it loads without complaint and renders blurry")
                errors += 1
    return errors


def check_open_actions_never_write_the_map() -> int:
    """OPEN_ACTIONS may write to a staging file. None of them may touch the map.

    OPEN_ACTIONS is the set the capture extension can call without the console
    code that is printed on the admin's own terminal. The line those actions
    draw is easy to state wrong: it is NOT "no writes". `capture` writes
    manual.json and `submit` writes submissions.json, both by design, because
    a staging file is reviewed before anything reaches the board.

    The line is that an open action never writes COMPANIES.JSON. That file is
    the map, it changes in Python behind validate() through save_companies,
    and everything that edits it sits behind the code.

    This matters more now than it did. Two actions were added for the capture
    worklist - `worklist`, which only reads, and `task-note`, which appends to
    task_notes.json for scripts/apply_task_notes.py to apply later. Both are
    open. If either ever grew a save_companies call, the extension would
    silently gain the power to rewrite the dataset from any page it is clicked
    on, and nothing else in this file would notice.

    Checked on the CODE, with docstrings and comments stripped. This project
    has caught a check reading its own explanatory prose four times; a guard
    that matches the words "save_companies" inside a docstring explaining that
    it must not call save_companies would be the fifth.
    """
    errors = 0
    src = (ROOT / "scripts" / "admin.py").read_text()

    m = re.search(r"OPEN_ACTIONS\s*=\s*\{(.*?)\}", src, re.S)
    if not m:
        print("  FAIL: OPEN_ACTIONS is gone from admin.py")
        return 1
    open_names = set(re.findall(r'"([a-z-]+)"', m.group(1)))
    if not open_names:
        print("  FAIL: OPEN_ACTIONS parsed as empty")
        return 1

    handlers = dict(re.findall(r'"([a-z-]+)"\s*:\s*(act_[a-z_]+)', src))

    # CODE ONLY, then every def in the file, so a call can be followed. The
    # first version read the handler's own body and nothing else - so an open
    # action that called act_set_founded({...}), whose body carries the
    # save_companies, passed. A writer is a writer however many hops away.
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    code = "\n".join(re.sub(r"#.*$", "", ln) for ln in code.splitlines())
    defs = {}
    for m in re.finditer(r"^def (\w+)\(", code, re.M):
        end = code.find("\ndef ", m.end())
        defs[m.group(1)] = code[m.start():end if end > 0 else len(code)]
    WRITES = ("save_companies(", 'write_atomic("companies.json"',
              "write_atomic('companies.json'", '"companies.json").write',
              '"companies.json").open')

    def reaches_a_writer(fname, seen):
        body = defs.get(fname, "")
        if any(w in body for w in WRITES):
            return [fname]
        # ANY MENTION, not just a call. act_worklist dispatches through a
        # dict - builders[which](companies, board) - so the callee's name
        # never sits before a paren. A name reached is a name that can run.
        for callee in sorted(set(re.findall(r"\b([A-Za-z_]\w*)\b", body))):
            if callee in defs and callee != fname and callee not in seen:
                seen.add(callee)
                path = reaches_a_writer(callee, seen)
                if path:
                    return [fname] + path
        return None

    for name in sorted(open_names):
        fname = handlers.get(name)
        if not fname:
            print(f"  FAIL: {name!r} is in OPEN_ACTIONS with no handler in the "
                  f"dispatch table - it can be called and does not exist")
            errors += 1
            continue
        if fname not in defs:
            print(f"  FAIL: {fname} is dispatched for {name!r} but not defined")
            errors += 1
            continue
        if "companies.json" in defs[fname]:
            print(f"  FAIL: {fname} is an OPEN action and names companies.json "
                  f"in its code. An action the extension can call without the "
                  f"console code must never touch the map - stage it and let a "
                  f"script in Python apply it")
            errors += 1
        path = reaches_a_writer(fname, {fname})
        if path:
            print(f"  FAIL: {fname} is an OPEN action and reaches a writer of "
                  f"the map: {' -> '.join(path)}. One hop through a helper is "
                  f"still the extension rewriting the dataset from any page")
            errors += 1
    return errors


def check_worklist_drops_what_was_worked() -> int:
    """A company you just captured must not be back at the top of the list.

    The capture worklist re-offered every company the moment after it was
    captured, because nothing read manual.json's `checks`. A list that does
    not shrink as you work it is a list you stop working, and this one is 685
    deep.

    THIRTY DAYS AND NOT FOREVER. manual.py::STALE_DAYS has meant exactly this
    since the worklist was written - a hand check is good for a month and then
    the postings have moved on - so it is reused rather than re-decided. A
    company that vanished permanently the moment it was touched would never be
    revisited, and jobs change.

    A CHECK WITH `found: null` DOES NOT COUNT, which is the case worth having
    a test for. That shape is written when somebody looked at the WRONG PAGE:
    airitcareers.co.uk is Air IT Group, a British MSP, and the board's AirIT
    is Air-Transport IT Services of Orlando. Twelve UK IT-support roles were
    filed against a Florida airport vendor before anyone noticed. The record
    is still genuinely unchecked, and hiding it would be the tool believing
    its own mistake.
    """
    import admin as _admin

    errors = 0
    today = dt.date(2026, 9, 2)
    man = {"checks": {
        "fresh":    {"checked_on": "2026-09-01", "by": "capture"},
        "edge":     {"checked_on": "2026-08-04"},          # 29 days - still fresh
        "stale":    {"checked_on": "2026-07-01"},          # 63 days - back on the list
        "wrongpage": {"checked_on": "2026-09-02", "found": None},
        "nothing":  {"checked_on": "2026-09-01", "found": False},
        "junk":     {"checked_on": "not a date"},
        "notadict": "whatever",
    }}
    got = _admin._checked_recently(man, today)
    for cid, want, why in [
        ("fresh", True, "a capture yesterday must hide it"),
        ("edge", True, "29 days is inside the 30-day window"),
        ("stale", False, "63 days old - the postings have moved on"),
        ("wrongpage", False,
         "found:null means somebody looked at the WRONG COMPANY; that record "
         "has still never been checked"),
        ("nothing", True,
         "found:false is a real answer - a person looked and there was nothing"),
        ("junk", False, "an unparseable date is not a check"),
        ("notadict", False, "a malformed entry is not a check"),
    ]:
        if (cid in got) != want:
            print(f"  FAIL: {cid!r} {'should' if want else 'should not'} be "
                  f"hidden from the worklist - {why}")
            errors += 1

    # AND THE QUEUE MUST ACTUALLY USE IT. The first version of this check
    # tested _checked_recently() alone, so deleting the one line in
    # _board_rows that calls it left the function perfect and the worklist
    # broken - the mutation passed. That is the fifth time in this project a
    # check has measured a helper instead of the wiring, so the queue is now
    # driven end to end against a throwaway data directory.
    with _sandbox_admin({
        "companies.json": [
            {"id": "worked", "name": "Worked", "website": "https://a.test",
             "sector": "General Gov", "category": "Suppliers & Services",
             "description": "x", "year_founded": None, "location": None,
             "ats": {"type": "unknown", "ref": None}, "govtech": True,
             "vendor_type": "GovTech Product",
             "hiring": {"status": "Unknown", "note": "", "roles": []}},
            {"id": "untouched", "name": "Untouched", "website": "https://b.test",
             "sector": "General Gov", "category": "Suppliers & Services",
             "description": "x", "year_founded": None, "location": None,
             "ats": {"type": "unknown", "ref": None}, "govtech": True,
             "vendor_type": "GovTech Product",
             "hiring": {"status": "Unknown", "note": "", "roles": []}},
        ],
        "manual.json": {"checks": {
            "worked": {"checked_on": dt.date.today().isoformat(), "by": "capture"}},
            "postings": []},
        "admin_dismissed.json": {},
        "discovery_log.json": [],
    }):
        rows = _admin.q_boards(_admin.read_companies(), {"organizations": []})
        ids = {r["id"] for r in rows}
        if "worked" in ids:
            print("  FAIL: q_boards still offers a company captured today - "
                  "_board_rows is not consulting the manual checks, so the "
                  "worklist never shrinks as it is worked")
            errors += 1
        if "untouched" not in ids:
            print("  FAIL: q_boards dropped a company nobody has checked - the "
                  "filter is hiding more than it should")
            errors += 1

    import manual as _manual
    if _admin.CAPTURE_FRESH_DAYS != _manual.STALE_DAYS:
        print(f"  FAIL: CAPTURE_FRESH_DAYS is {_admin.CAPTURE_FRESH_DAYS} and "
              f"manual.py::STALE_DAYS is {_manual.STALE_DAYS}. Two answers to "
              f"'how long is a hand check good for' is one too many")
        errors += 1
    return errors


def check_identify_catches_a_namesake() -> int:
    """The panel must notice when the page is a different company with the same name.

    Twelve UK IT-support roles from airitcareers.co.uk - Air IT Group, "the
    UK's #1 MSP" - were filed against Air-Transport IT Services of Orlando on
    the first day of real capturing. The verdict line had said what the board
    knew about the company; nothing had checked that the page was that
    company's. act_identify is that check, and it reuses identifies() rather
    than inventing a looser one.

    Driven with the actual title the wrong page carried, and with a title the
    right page would. Read-only-ness is asserted separately by
    check_open_actions_never_write_the_map, since identify is OPEN.
    """
    errors = 0
    import admin as _admin
    with _sandbox_admin({
        "companies.json": [{
            "id": "airit", "name": "AirIT (Air-Transport IT Services)",
            "website": "https://www.airit.com", "location": "Orlando, Florida",
            "sector": "Airports & Aviation", "category": "Terminal & Passenger Experience",
            "description": "x", "year_founded": None, "ats": {"type": "unknown", "ref": None},
            "govtech": True, "vendor_type": "GovTech Product",
            "hiring": {"status": "Unknown", "note": "", "roles": []}}],
    }):
        wrong = _admin.act_identify({
            "company_id": "airit", "page_url": "https://airitcareers.co.uk/",
            "title": "Air IT Careers | Jobs in IT",
            "meta": "Be part of the UK's leading managed service provider",
            "h1": "JOIN THE UK'S #1 MSP"})
        if wrong.get("identifies") is not False:
            print(f"  FAIL: identify said {wrong.get('identifies')!r} for Air IT Group's "
                  f"page against Air-Transport IT Services - the namesake that filed "
                  f"twelve wrong postings would get through again")
            errors += 1
        right = _admin.act_identify({
            "company_id": "airit", "page_url": "https://www.airit.com/careers",
            "title": "Careers - Air-Transport IT Services",
            "meta": "AirIT airport passenger processing", "h1": "AirIT Careers"})
        if right.get("identifies") is not True:
            print(f"  FAIL: identify said {right.get('identifies')!r} for the company's "
                  f"own careers page - a check that refuses the real page is one "
                  f"somebody switches off")
            errors += 1
        empty = _admin.act_identify({"company_id": "airit", "title": "", "meta": "", "h1": ""})
        if empty.get("identifies") is not None:
            print("  FAIL: identify must answer None, not a verdict, when the page "
                  "carries no identity fields at all")
            errors += 1
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



_ACME = {"id": "acme", "name": "Acme", "website": "https://acme.test",
         "sector": "General Gov", "category": "Suppliers & Services",
         "description": "x", "year_founded": None, "location": None,
         "ats": {"type": "unknown", "ref": None}, "govtech": True,
         "vendor_type": "GovTech Product",
         "hiring": {"status": "Unknown", "note": "", "roles": []}}


class _Resp:
    """Enough of a requests.Response for _get and _post_json."""

    def __init__(self, code, headers=None, text="", payload=None, url=""):
        self.status_code, self.headers, self.text = code, headers or {}, text
        self._payload, self.url = payload if payload is not None else {}, url

    def json(self):
        return self._payload


def check_fetchers_page_through_their_callers() -> int:
    """The paging helper is tested; this is whether the fetchers USE it.

    check_workday_pages_to_the_end drives _paged() with a fixture and proves
    the helper stops on the wrap. It proves nothing about fetch_workday,
    which could take one page, never call _paged, and leave that check green
    - the sixth time in this project a guard measured a helper instead of
    the wiring. So each paged fetcher is driven here through a stubbed
    transport serving MORE than one page, and the assertion is on what came
    back and how many requests it took to get it.
    """
    errors = 0
    keep = (ats._post_json, ats._get, ats._workday_details, ats.FETCH_DETAILS)
    try:
        ats.FETCH_DETAILS = False
        ats._workday_details = lambda out: out

        # Workday: 45 postings, and past the end it wraps to page one.
        TOTAL = 45
        posts = []

        def post(url, body):
            posts.append(body)
            off = body.get("offset", 0)
            if off >= TOTAL:
                off = 0
            return {"jobPostings": [
                {"externalPath": f"/job/{i}", "title": f"Role {i}",
                 "locationsText": "Austin, TX"}
                for i in range(off, min(off + ats.WD_PAGE, TOTAL))]}

        ats._post_json = post
        want_calls = 2 * (math.ceil(TOTAL / ats.WD_PAGE) + 1)     # two search terms
        for label, call in (
                ("fetch_workday", lambda: ats.fetch_workday(["acme", "wd5", "Careers"])),
                ("_workday_jobs", lambda: ats._workday_jobs(
                    "https://wd1.myworkdaysite.com/wday/cxs/acme/Careers/jobs",
                    "https://wd1.myworkdaysite.com/recruiting/acme/Careers", "Careers"))):
            posts.clear()
            rows = call()
            if len(rows) != TOTAL:
                print(f"  FAIL: {label} returned {len(rows)} of {TOTAL} postings "
                      f"on a {TOTAL}-posting tenant - it is not paging to the end")
                errors += 1
            if len(posts) < want_calls:
                print(f"  FAIL: {label} made {len(posts)} requests for two "
                      f"search terms over {TOTAL} postings; paging to the end "
                      f"takes {want_calls}. The helper is fine and the caller "
                      f"is not using it")
                errors += 1

        # SmartRecruiters: 250 postings at the 100 the API caps a page to.
        SR = 250
        gets = []

        def get(url, **kw):
            gets.append(url)
            m = re.search(r"offset=(\d+)", url)
            off = int(m.group(1)) if m else 0
            return _Resp(200, payload={"content": [
                {"id": f"id{i}", "name": f"Role {i}",
                 "location": {"city": "Austin", "region": "TX", "country": "us"}}
                for i in range(off, min(off + ats.SR_PAGE, SR))]})

        ats._get = get
        rows = ats.fetch_smartrecruiters("acme")
        if len(rows) != SR or len(gets) < 3:
            print(f"  FAIL: fetch_smartrecruiters returned {len(rows)} of {SR} "
                  f"in {len(gets)} request(s) - Xplor advertises 251 and the "
                  f"board published 100")
            errors += 1

        # iCIMS: nineteen a page, dedup on the href, three pages.
        IC, PER = 57, 19
        gets.clear()

        def get2(url, **kw):
            gets.append(url)
            m = re.search(r"pr=(\d+)", url)
            start = (int(m.group(1)) if m else 0) * PER
            anchors = "".join(
                f'<a href="https://acme.icims.com/jobs/{i}/x" class="iCIMS_Anchor" '
                f'title="{i} - Role {i}">x</a>'
                for i in range(start, min(start + PER, IC)))
            return _Resp(200, text=f"<html>{anchors}</html>")

        ats._get = get2
        rows = ats.fetch_icims("acme")
        if len(rows) != IC or len(gets) < 3:
            print(f"  FAIL: fetch_icims returned {len(rows)} of {IC} in "
                  f"{len(gets)} request(s) - Bruker's portal is eleven pages")
            errors += 1
    finally:
        ats._post_json, ats._get, ats._workday_details, ats.FETCH_DETAILS = keep
    return errors


def check_the_gate_is_where_the_requests_are() -> int:
    """The host gate is tested on its own; this is whether requests pass it.

    Three things, each driven rather than read off the source:

    THE GATE IS WIRED. _get and _post_json both call it. The pacing check
    drives _host_gate directly, so either transport could drop the call and
    that check would stay green - and _post_json is where Workday's ten
    pages a tenant actually go.

    A 429 SURVIVES A SLEEPER. The first backoff wrote a future stamp into
    _HOST_LAST from outside the lock. A same-host worker already asleep in
    the gate then woke, stamped "now" over it, and the server's Retry-After
    was gone before anyone honoured it. Reproduced here with a real thread.

    A 304 WITHOUT A BODY RE-ASKS PLAINLY, THROUGH THE GATE. That used to be
    asserted by counting the string 'requests.get' in the source, which a
    re-fetch that re-sent the validators - and so got another 304 - would
    satisfy. Now the second request's headers are what is checked.
    """
    errors = 0
    keep = (ats._host_gate, ats.requests.get, ats.requests.post,
            ats.HTTP_CACHE, ats.HOST_PAUSE)
    gated = []
    try:
        ats.HTTP_CACHE = None
        ats._host_gate = lambda url: gated.append(url)
        ats.requests.get = lambda *a, **k: _Resp(200)
        ats.requests.post = lambda *a, **k: _Resp(200, payload={})
        ats._get("https://a.test/x")
        ats._post_json("https://a.test/y", {})
        if gated != ["https://a.test/x", "https://a.test/y"]:
            print(f"  FAIL: the host gate saw {gated}; _get and _post_json must "
                  f"both pass through it, or Workday's paging goes out in a burst")
            errors += 1
        ats.requests.post = lambda *a, **k: _Resp(429, {"Retry-After": "1"})
        try:
            ats._post_json("https://limited.test/p", {})
            print("  FAIL: a 429 on a POST did not raise")
            errors += 1
        except ats.RateLimited:
            pass
        except ats.AtsError:
            print("  FAIL: a 429 on a Workday POST raises a plain AtsError - "
                  "'slow down' recorded exactly like 'gone'")
            errors += 1
    finally:
        (ats._host_gate, ats.requests.get, ats.requests.post,
         ats.HTTP_CACHE, ats.HOST_PAUSE) = keep

    # The race, with a real thread asleep in a real gate.
    ats.HOST_PAUSE = 0.25
    ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear(); ats._HOST_NOT_BEFORE.clear()
    try:
        host = "https://sleepy.test/x"
        ats._host_gate(host)                          # stamps now
        th = threading.Thread(target=ats._host_gate, args=(host,))
        th.start()                                    # asleep for ~0.25s, lock held
        time.sleep(0.05)
        try:
            ats._back_off(host, _Resp(429, {"Retry-After": "1"}))
        except ats.RateLimited:
            pass
        th.join()
        t0 = time.monotonic()
        ats._host_gate(host)
        if time.monotonic() - t0 < 0.6:
            print("  FAIL: a worker asleep in the gate woke and overwrote the "
                  "429 backoff - the next request went straight back in")
            errors += 1
    finally:
        ats.HOST_PAUSE = keep[4]
        ats._HOST_LAST.clear(); ats._HOST_LOCKS.clear(); ats._HOST_NOT_BEFORE.clear()

    # The 304 path, driven.
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gtd-cache-"))
    gated = []
    try:
        ats.HTTP_CACHE, ats.HOST_PAUSE = tmp, 0
        ats._host_gate = lambda url: gated.append(url)
        url = "https://cached.test/board"
        meta_p, body_p = ats._cache_paths(url)
        meta_p.write_text(json.dumps({"url": url, "etag": "abc", "last_modified": None}))
        calls = []

        def get(u, headers=None, **kw):
            calls.append(dict(headers or {}))
            return _Resp(304) if len(calls) == 1 else _Resp(200, text="fresh")

        ats.requests.get = get
        r = ats._get(url)
        if r.text != "fresh" or len(calls) != 2:
            print(f"  FAIL: a 304 with no cached body came back as "
                  f"{r.text!r} after {len(calls)} request(s) - that is a cache "
                  f"miss reported as an unreadable board")
            errors += 1
        elif "If-None-Match" in calls[1] or "If-Modified-Since" in calls[1]:
            print("  FAIL: the re-fetch after a bodiless 304 re-sent the "
                  "validators, so the second reply is another 304")
            errors += 1
        if len(gated) != 2:
            print(f"  FAIL: the 304 re-fetch skipped the host gate ({len(gated)} "
                  f"gate call(s) for two requests at one host)")
            errors += 1
        body_p.write_bytes(gzip.compress(b"cached body"))
        calls.clear()
        r = ats._get(url)
        if not getattr(r, "from_cache", False) or getattr(r, "url", None) != url:
            print("  FAIL: a cached 304 reply carries no .url - find_websites."
                  "probe reads it off every _get result and crashes the run")
            errors += 1
    finally:
        (ats._host_gate, ats.requests.get, ats.requests.post,
         ats.HTTP_CACHE, ats.HOST_PAUSE) = keep
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_mail_is_built_from_the_shell() -> int:
    """check_mail_shell holds two source files against each other. This sends.

    That check compares text and proves drift detection; it does not prove
    an email is ever BUILT from the shell, so every caller could stop using
    shell() and it would stay green. And it reads alerts.js as text, which
    is how the Worker shipped for a day using NAME without importing it:
    every mail path threw ReferenceError, and nothing in this file ran a
    line of it. So the Python half is rendered and the JavaScript half is
    executed under node, and what is asserted is the OUTPUT - the shell's
    marks present, no template brace, no `undefined` where a value belonged.
    """
    errors = 0
    import digest
    roles = [{"id": "acme::Account Executive", "title": "Account Executive",
              "company": "Acme", "sector": "General Gov", "quota_carrying": True,
              "location": "Austin, TX", "states": ["TX"], "work_mode": "not stated",
              "office": {"city": "Austin", "state": "TX"}}]
    subject, text, html_out = digest.render(
        {"roles": roles, "since": "2026-09-01"}, {"token": "t" * 48}, {})
    # The digest's links are text links by design; the Badge button is a
    # call-to-action the confirmation carries and the digest does not.
    shell_marks = [(n, m) for n, m in MAIL_MARKS if n != "Badge button"]
    for name, mark in shell_marks:
        if mark not in html_out:
            print(f"  FAIL: digest.render() emitted no {name} ({mark!r}) - the "
                  f"digest is not being built from the shell")
            errors += 1
            break
    # `}}` is legitimate CSS (a rule closing inside a media block); `{{` never
    # is. It is what an f-string that lost its f would emit.
    if "{{" in html_out:
        print("  FAIL: a doubled open brace reached the digest - a shell "
              "f-string that is no longer one")
        errors += 1
    if "Account Executive" not in html_out or "Account Executive" not in text:
        print("  FAIL: the role did not reach the rendered digest")
        errors += 1

    if not shutil.which("node"):
        print("  SKIP: node is not installed here, so functions/api/alerts.js was "
              "NOT executed - the Worker's mail path is unverified on this machine")
        return errors
    # THE REAL IMPORT, resolved to the real _brand.js. The first version of
    # this replaced the import line with its own constants - so the one bug
    # it exists to catch, NAME used and never imported, could not reproduce:
    # the harness had defined NAME itself. Only the specifier is rewritten;
    # the names the file asks for are the names it gets.
    src = (ROOT / "functions" / "api" / "alerts.js").read_text()
    brand = (ROOT / "functions" / "_brand.js").resolve().as_uri()
    src, n = re.subn(r'from\s*"\.\./_brand\.js"', f'from "{brand}"', src, count=1)
    if n != 1:
        print("  FAIL: alerts.js no longer imports from ../_brand.js")
        return errors + 1
    src += ('\nconsole.log(JSON.stringify({confirm: confirmMail("t".repeat(48), '
            '{cadence: "weekly"}), settings: shell("pre", "<div>x</div>", '
            '[["Change", "https://x/1"]]), button: button("https://x/2", "Go")}));')
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        import subprocess
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    if r.returncode:
        print(f"  FAIL: alerts.js threw when its mail builders ran: "
              f"{r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else 'no output'}")
        return errors + 1
    out = json.loads(r.stdout)
    subj, _txt, confirm_html = out["confirm"]
    if "SLED JOBS" not in subj:
        print(f"  FAIL: the confirmation subject is {subj!r} - NAME did not reach it")
        errors += 1
    if 'bgcolor="#0B57C4"' not in confirm_html:
        print("  FAIL: the confirmation email carries no Badge button - the "
              "reader has nothing to click")
        errors += 1
    for label, page in (("confirmMail", confirm_html), ("the settings shell", out["settings"])):
        for name, mark in shell_marks:
            if mark not in page:
                print(f"  FAIL: {label} emitted no {name} ({mark!r})")
                errors += 1
                break
        for junk in ("${", "{{", "undefined", "[object"):
            if junk in page:
                print(f"  FAIL: {label} emitted {junk!r} - a template brace or "
                      f"a missing value reached a reader")
                errors += 1
    if 'href="https://x/2"' not in out["button"] or ">Go<" not in out["button"]:
        print("  FAIL: button() did not carry its link and label")
        errors += 1
    return errors


def check_capture_keeps_two_reqs_with_one_title() -> int:
    """An Account Executive in Austin and one in Denver are two postings.

    act_capture keyed on company::title and kept the first, while build_board
    re-keys every manual row by (company, title, url, location) and the
    extension dedups on the link. The second requisition was lost between
    them. Driven end to end in a sandbox: two same-titled jobs with different
    links land as two rows, and sending them again lands nothing.
    """
    import admin as _admin
    errors = 0
    jobs = [{"title": "Account Executive", "url": "https://acme.test/j/1",
             "location": "Austin, TX"},
            {"title": "Account Executive", "url": "https://acme.test/j/2",
             "location": "Denver, CO"}]
    with _sandbox_admin({"companies.json": [_ACME],
                         "manual.json": {"checks": {}, "postings": []}}) as tmp:
        r = _admin.act_capture({"company_id": "acme",
                                "page_url": "https://acme.test/careers", "jobs": jobs})
        if r.get("added") != 2:
            print(f"  FAIL: two requisitions with one title captured as "
                  f"{r.get('added')} - the second is lost ({r.get('error') or r.get('message')})")
            errors += 1
        r = _admin.act_capture({"company_id": "acme",
                                "page_url": "https://acme.test/careers", "jobs": jobs})
        if r.get("added") != 0:
            print(f"  FAIL: re-sending the same capture added {r.get('added')} row(s)")
            errors += 1
        man = json.loads((tmp / "manual.json").read_text())
        ids = [p["id"] for p in man["postings"]]
        if len(ids) != 2 or len(set(ids)) != 2:
            print(f"  FAIL: manual.json holds ids {ids}; two distinct rows expected")
            errors += 1
        # A row already on file under the old plain key is matched by what it
        # is, not by its id - so nothing captured before this is doubled.
        r = _admin.act_capture({"company_id": "acme",
                                "page_url": "https://acme.test/careers",
                                "jobs": [dict(jobs[0], url=None)]})   # same posting, link off the page
        if r.get("added") != 0 and len(json.loads((tmp / "manual.json").read_text())["postings"]) > 3:
            print("  FAIL: a re-capture with the page url doubled a row")
            errors += 1
    return errors


def check_task_notes_land_honestly() -> int:
    """Five note kinds, and the four ways one used to land wrong.

    founded  was written as a string; validate() let it through and the next
             selftest failed on a file every write had approved.
    board    an address find_ats could not read was filed as an html board
             and became the public card's link.
    posts-at bypassed posts_at.check(), so a LinkedIn brochure went on the
             card as "where they post".
    nothing  set a note nothing read; the worklist re-offered the company.
    And the endpoint accepted 2027-2099, which the founded action refuses.
    """
    import admin as _admin
    import apply_task_notes as atn
    errors = 0

    c = {"id": "acme", "name": "Acme", "ats": {"type": "greenhouse", "ref": "acme"},
         "year_founded": None}
    ok, _ = atn.apply_one({"kind": "founded", "value": "1999"}, c)
    if not ok or c["year_founded"] != 1999 or isinstance(c["year_founded"], str):
        print(f"  FAIL: a founded note landed as {c['year_founded']!r}; the map "
              f"holds years as integers")
        errors += 1
    ok, _ = atn.apply_one({"kind": "founded", "value": "2099"}, c)
    if ok:
        print("  FAIL: a founding year in the future was applied")
        errors += 1
    err = _admin.validate([dict(_ACME, year_founded="1999")])
    if not err or "year_founded" not in err:
        print("  FAIL: validate() accepts a founding year stored as a string")
        errors += 1
    err = _admin.validate([dict(_ACME, year_founded=1999)])
    if err and "year_founded" in err:
        print(f"  FAIL: validate() refuses a plain integer year: {err}")
        errors += 1

    keep = atn.add_company.find_ats
    try:
        atn.add_company.find_ats = lambda url, paths=None: (
            None, None, ["no careers page or ATS marker found"])
        c2 = {"id": "b", "name": "B", "ats": {"type": "unknown", "ref": None}}
        ok, why = atn.apply_one({"kind": "board", "value": "https://b.test/careers"}, c2)
        if ok or (c2["ats"] or {}).get("type") == "html":
            print("  FAIL: an address find_ats could not read was filed as an "
                  "html board - the public card would link a page nobody read")
            errors += 1
        atn.add_company.find_ats = lambda url, paths=None: (
            {"type": "html", "ref": url}, url, [])
        ok, why = atn.apply_one({"kind": "board", "value": "https://acme.test/careers"}, c)
        if ok or c["ats"]["type"] != "greenhouse":
            print("  FAIL: a page scan replaced a structured greenhouse board")
            errors += 1
    finally:
        atn.add_company.find_ats = keep

    ok, why = atn.apply_one({"kind": "posts-at",
                             "value": "https://www.linkedin.com/company/acme"}, c)
    if ok:
        print("  FAIL: a LinkedIn brochure page was recorded as where they post")
        errors += 1
    ok, why = atn.apply_one({"kind": "posts-at",
                             "value": "https://www.linkedin.com/company/acme/jobs"}, c)
    if not ok or (c.get("posts_at") or {}).get("where") != "linkedin":
        print(f"  FAIL: a LinkedIn jobs page did not land as posts_at/linkedin ({why})")
        errors += 1

    n = {"kind": "nothing", "at": "2026-09-02T10:00:00-04:00", "saw": "https://x"}
    ok, _ = atn.apply_one(n, c)
    chk = n.get("_check") or {}
    if not ok or chk.get("found") is not False or chk.get("checked_on") != "2026-09-02":
        print("  FAIL: a 'nothing here' note does not become a manual check, so "
              "the worklist keeps re-offering a company somebody already stood on")
        errors += 1

    with _sandbox_admin({"companies.json": [_ACME], "task_notes.json": []}):
        r = _admin.act_task_note({"kind": "founded", "company_id": "acme", "value": "2099"})
        if "error" not in r:
            print("  FAIL: the task-note endpoint accepts 2099 as a founding year, "
                  "which validate() and the founded action both refuse")
            errors += 1
        r = _admin.act_task_note({"kind": "founded", "company_id": "acme", "value": "1999"})
        if "error" in r:
            print(f"  FAIL: the task-note endpoint refused 1999: {r['error']}")
            errors += 1
    return errors


def check_sweep_keeps_its_own_work() -> int:
    """Three ways the exhibitor pipeline lost or invented names.

    A trailing chevron beat both the dedupe key and the anchored nav filter,
    so GFOA staged "CONTACT US ›" and 11 menu items twice. A re-sweep rebuilt
    the file from bare names and dropped every is_govtech flag classify had
    written, and a directory that refused today overwrote the good list from
    last week. classify counted "not already on file" against the labelled
    set, so 555 names the board already held read as new. And the state-event
    duplicate guard swallowed its own read error and switched itself off.
    """
    import sweep_exhibitors as sw
    import classify_exhibitors as cx
    import register_state_events as rse
    errors = 0
    if sw.clean("Exhibitors ›") != "Exhibitors":
        print("  FAIL: clean() leaves a trailing chevron on a name")
        errors += 1
    if sw.looks_like_a_name(sw.clean("CONTACT US ›")):
        print("  FAIL: 'CONTACT US ›' is being staged as an exhibitor")
        errors += 1
    for n in ("Careers at GFOA", "GFOA's Research & Consulting Center", "Bylaws",
              "Privacy Policy", "Empty cart Cart", "Registration for 2027 Opens Fall 2026"):
        if sw.looks_like_a_name(n, "GFOA"):
            print(f"  FAIL: {n!r} passes as an exhibitor name")
            errors += 1
    for n in ("Illinois GFOA", "Government Window", "Baker Tilly", "Carr, Riggs & Ingram"):
        if not sw.looks_like_a_name(n, "GFOA"):
            print(f"  FAIL: {n!r} - a real exhibitor - is being filtered out")
            errors += 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gtd-sweep-"))
    try:
        f = tmp / "exhibitors_X.json"
        f.write_text(json.dumps({"found": True, "exhibitors": [
            {"name": "Acme", "website": None, "is_govtech": True, "is_govtech_why": "ruled"}]}))
        fresh = {"found": True, "exhibitors": [{"name": "Acme", "website": "https://acme.test"}]}
        if not sw.merge_into(fresh, f) or fresh["exhibitors"][0].get("is_govtech") is not True:
            print("  FAIL: a re-sweep drops the is_govtech flag classify wrote")
            errors += 1
        if sw.merge_into({"found": False, "why": "HTTP 403", "exhibitors": []}, f):
            print("  FAIL: a refused read would overwrite a good staged file")
            errors += 1

        (tmp / "companies.json").write_text(json.dumps(
            [{"name": "Acme", "also_known_as": ["Acme Corp"]}]))
        (tmp / "suppliers.json").write_text(json.dumps([{"name": "Bob's Catering"}]))
        keep = cx.DATA
        cx.DATA = tmp
        try:
            got = cx.on_file()
        finally:
            cx.DATA = keep
        if "bobscatering" not in got or "acmecorp" not in got:
            print("  FAIL: classify's 'on file' set misses an unstamped supplier "
                  "or an also_known_as - those names would be counted as new")
            errors += 1

        keep = rse.DATA
        rse.DATA = tmp / "nowhere"
        try:
            rse.existing_tags()
            print("  FAIL: existing_tags() answered an empty set for a missing "
                  "conferences.json - the duplicate guard switched itself off")
            errors += 1
        except SystemExit:
            pass
        finally:
            rse.DATA = keep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errors


def check_intake_writes_only_issued_tags() -> int:
    """The string intake writes into descriptions is the catalog's tag, ever.

    On 2026-09-02 conference_intake fell back to staged["conference"] -
    "AWWA ACE", "3CMA" - and wrote it into 988 descriptions in one afternoon.
    The catalog guard above caught it after the fact; this refuses it before
    the write. Driven, not read: a staged file with no tag, a tag the catalog
    never issued, and a real one.
    """
    import conference_intake as ci
    errors = 0
    real = sorted(ci.issued_tags())
    if not real:
        print("  FAIL: conferences.json issues no tags at all")
        return 1
    tag, why = ci.resolve_tag({"event_tag": real[0], "conference": "Whatever"}, None)
    if tag != real[0] or why:
        print(f"  FAIL: a catalog tag was not resolved: {why}")
        errors += 1
    tag, why = ci.resolve_tag({"conference": "AWWA ACE"}, None)
    if tag or not why:
        print("  FAIL: intake fell back to the conference NAME again - that is "
              "the string that landed in 988 descriptions")
        errors += 1
    tag, why = ci.resolve_tag({"event_tag": real[0]}, "Made Up 2026")
    if tag or not why:
        print("  FAIL: an --event-tag the catalog never issued was accepted")
        errors += 1
    src = (ROOT / "scripts" / "conference_intake.py").read_text()
    body = src[src.find("def main("):]
    if "resolve_tag(" not in body or 'staged["conference"]' in body.split("resolve_tag(")[0]:
        print("  FAIL: main() no longer resolves the tag through resolve_tag()")
        errors += 1
    return errors


def check_extension_holds_and_refuses() -> int:
    """The service worker, executed, with a fake chrome and a fake admin.

    Four facts about the capture queue, each once wrong or unproven:
    a capture with the admin off is HELD, and says so in words; a capture the
    admin saw and REFUSED is set aside with the admin's reason and never sent
    again - it used to go back on the queue and re-send on every flush,
    forever, silently; a connection failure mid-queue keeps everything
    behind it in order; and the queue goes when the admin answers.
    scripts/worker_harness.js is the fake chrome.
    """
    if not shutil.which("node"):
        print("  SKIP: node is not installed here, so extension/background.js "
              "was NOT executed - the capture queue is unverified on this machine")
        return 0
    import subprocess
    r = subprocess.run(["node", str(ROOT / "scripts" / "worker_harness.js"),
                        str(ROOT / "extension" / "background.js")],
                       capture_output=True, text=True, timeout=30)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:                                   # noqa: BLE001
        print(f"  FAIL: the worker harness produced no result: "
              f"{(r.stderr or r.stdout).strip()[:200]}")
        return 1
    if out.get("crash"):
        print(f"  FAIL: background.js crashed under the harness: {out['crash'][:200]}")
        return 1
    errors = 0
    held = out.get("held", {})
    if not held.get("queued") or held.get("pending") != 1 or "not running" not in str(held.get("error")):
        print(f"  FAIL: a capture with the admin off was not held and explained: {held}")
        errors += 1
    if "not running" not in str(out.get("off_search")):
        print(f"  FAIL: a search with the admin off shows {out.get('off_search')!r} "
              f"instead of telling the person to start it")
        errors += 1
    ref = out.get("refused", {})
    if ref.get("refused") != 1 or ref.get("pending") != 0 or ref.get("set_aside") != 1 \
            or ref.get("why") != "no such company":
        print(f"  FAIL: a capture the admin refused was not set aside with its "
              f"reason: {ref}")
        errors += 1
    if out.get("again", {}).get("captures") != 1:
        print(f"  FAIL: a refused capture was sent again on the next flush "
              f"({out.get('again')}) - that is the forever loop")
        errors += 1
    if out.get("kept") != ["a", "b"]:
        print(f"  FAIL: two captures held while the admin was off came back as "
              f"{out.get('kept')}")
        errors += 1
    if out.get("sent", {}).get("flushed") != 2 or out.get("sent", {}).get("pending") != 0:
        print(f"  FAIL: held captures did not go when the admin answered: {out.get('sent')}")
        errors += 1
    return errors



def check_a_menu_is_not_a_directory() -> int:
    """Seven chapter menus were filed as exhibitor directories on 2026-09-02.

    find_event_directories accepted any page whose harvest was not
    `suspicious` and graded good OR mixed. A chapter's sponsorship page grades
    "good" on navigation alone, because association menus say Group, Services,
    Resources and Partners as readily as vendors do. 146 names came off APA
    Florida's page and the companies among them numbered zero: "Knowledge
    Center", "Sections Overview", "Back to Main Menu", "Atlantic Coast
    Section". Promoting those seven would have published seven conference
    pages whose exhibitors were menu items.

    The detector was MEASURED before it was used, against the 26 staged floors
    on disk that produced real companies. Worst real floor: nav 0.05, tree
    0.053. The offenders: 0.315, 0.247, 0.237, 0.234. Threshold 0.10.

    So this check drives it three ways: the shapes it must catch, the real
    floors it must not, and - the part a helper test would miss - that
    find_event_directories still CALLS it.
    """
    errors = 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import sweep_exhibitors as sw

    menus = [
        ("APA Florida's sponsorship page",
         ["Knowledge Center", "Conferences & Events", "Sections", "Sections Overview",
          "Back to Main Menu", "Atlantic Coast Section", "Broward Section",
          "Capital Area Section", "Professional Growth", "Professional Growth Overview",
          "Policy and Advocacy", "Policy and Advocacy Overview", "Career Center",
          "Community Outreach", "Community Outreach Overview", "Back to Sections"]),
        ("a menu that nests without saying Overview",
         ["Membership", "Membership Benefits", "Membership Directory", "Events",
          "Events Calendar", "Events Archive", "Resources", "Resources Library",
          "About", "About Our Board", "News", "News Releases"]),
    ]
    for label, names in menus:
        rows = [{"name": n} for n in names]
        if not sw.reads_as_a_menu(rows):
            nav, tree = sw.navish(rows)
            print(f"  FAIL: {label} is not being read as a menu "
                  f"(nav={nav:.2f} tree={tree:.2f}) - a page like it was filed "
                  f"as an exhibitor directory for seven APA chapters")
            errors += 1

    # AND THE REAL FLOORS MUST SURVIVE. A detector that refuses everything is
    # not a detector; these are the lists that produced actual companies.
    staged = sorted((DATA).glob("exhibitors_*.json"))
    if not staged:
        print("  FAIL: no staged exhibitor files to measure the detector against")
        return errors + 1
    for f in staged:
        d = json.loads(f.read_text())
        if not d.get("found") or len(d.get("exhibitors") or []) < 10:
            continue
        why = sw.reads_as_a_menu(d["exhibitors"])
        if why:
            print(f"  FAIL: {d.get('event_tag')} - a floor that produced real "
                  f"companies is being refused as a menu: {why[:80]}")
            errors += 1

    # THE CALLER, not just the helper. judge() can stop consulting it and
    # every assertion above stays green while the gate is wide open again.
    # The fixture has to GRADE WELL, or judge() would refuse it on the grade
    # and this would pass with the menu rule deleted - which is what the
    # first version of this check did. Association menus say Services,
    # Solutions, Technology and Group as readily as vendors do; that is
    # exactly why the grade cannot carry this on its own.
    import find_event_directories as fed
    menu_names = ["Member Services", "Member Services Overview", "Technology Group",
                  "Technology Group Overview", "Business Solutions",
                  "Business Solutions Overview", "Consulting Partners",
                  "Consulting Partners Overview", "Engineering Systems",
                  "Engineering Systems Overview", "Back to Main Menu",
                  "Back to Member Services", "Awards Overview", "Sections Overview"]
    if sw.quality([{"name": n} for n in menu_names])[0] != "good":
        print("  FAIL: the menu fixture no longer grades 'good', so this check "
              "would pass with the menu rule deleted")
        errors += 1
    menu_page = "".join(f'<a href="/p{i}">{n}</a>' for i, n in enumerate(menu_names))
    if fed.judge(menu_page, "https://florida.example.org/sponsorship",
                 "APA", "Florida")[0] == "directory":
        print("  FAIL: judge() accepts a page of pure navigation as a directory - "
              "it is no longer consulting reads_as_a_menu()")
        errors += 1
    return errors


def check_a_chapter_directory_is_not_its_parents() -> int:
    """A state chapter's exhibitor list must belong to that chapter.

    North Carolina Police Chiefs' organisation url was myiacp.org/NC__Login -
    IACP's own login page, matched on the two letters in its path - and
    walking its links reached a real exhibitor list of 36 companies. It was
    IACP's NATIONAL 2026 Technology Conference; "north carolina" appeared on
    it zero times. Accepting it would have tagged three dozen companies with a
    North Carolina conference they never attended, which is CLAUDE.md's
    never-point-a-company-at-its-parent's-board rule wearing a conference.

    Two guards, and both were earned by that one row: a login page is not a
    chapter site, and a two-letter state code is not evidence - not after a
    dot (NIGP's Alberta chapter, nigpabchapter.ca, was filed as California on
    the .ca), and not on the parent's own server.
    """
    errors = 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_event_directories as fed
    IACP, APA = "https://www.theiacp.org/", "https://www.planning.org/"
    for page, url, geo, org, parent, want, why in [
        ("<title>Exhibitors</title> 2026 Tech Conference",
         "https://events.rdmobile.com/Exhibitors/Index/20070", "North Carolina",
         "https://www.myiacp.org/NC__Login", IACP, False,
         "THE TRAP: off-site, names the state nowhere"),
        ("<title>Sponsors</title>", "http://www.nyplanning.org/about/sponsors",
         "New York", "http://www.nyplanning.org/", APA, True,
         "the chapter's own domain settles it"),
        ("<title>Exhibitors</title>", "https://www.myiacp.org/NC/exhibitors",
         "North Carolina", "https://www.myiacp.org/x", "https://www.myiacp.org/", False,
         "on the parent's server the abbreviation is not evidence"),
        ("<title>North Carolina Chiefs</title>", "https://www.myiacp.org/NC/exhibitors",
         "North Carolina", "https://www.myiacp.org/x", "https://www.myiacp.org/", True,
         "on the parent's server, but it names the state"),
        ("<title>Exhibitors</title>", "https://northcarolina.planning.org/x",
         "North Carolina", None, None, True, "the state closed up in the host"),
        ("<title>Exhibitors</title>", "https://x.test/e", "North Carolina", None, None,
         False, "names the state nowhere at all"),
        # A SUBDOMAIN OF THE PARENT IS THE PARENT. ace.awwa.org is AWWA's own
        # national ACE conference and stage 1 had handed it to the
        # California-Nevada Section as that section's site; comparing hosts
        # exactly let it through, because ace.awwa.org is not awwa.org.
        ("<title>Become an Exhibitor - American Water Works Association</title>",
         "https://ace.awwa.org/exhibitors-sponsors/", "California/Nevada",
         "https://ace.awwa.org/", "https://www.awwa.org/", False,
         "the parent's own subdomain is the parent"),
        ("<title>Sponsors</title>", "https://ca-nv-awwa.org/sponsors", "California/Nevada",
         "https://ca-nv-awwa.org/", "https://www.awwa.org/", True,
         "the section's own domain, which is not under the parent's"),
        # EIGHT ROWS NAME NO SINGLE STATE - Multi-state, WA/OR/ID, TN/KY,
        # NC/SC. Returning True there switched the guard off exactly where a
        # regional body is most likely to be handed its parent's event.
        ("<title>Exhibitors</title>", "https://events.example.com/list", "Multi-state",
         "https://region.example.org/", "https://parent.example.org/", False,
         "no state to check and the list is off the body's own site"),
        ("<title>Exhibitors</title>", "https://region.example.org/list", "Multi-state",
         "https://region.example.org/", "https://parent.example.org/", True,
         "no state to check, but it is on the body's own site"),
    ]:
        got = fed.owns(page, url, geo, org, parent)
        if got != want:
            print(f"  FAIL: owns({url[:44]!r}, {geo!r}) = {got}, expected {want} - {why}")
            errors += 1

    for url, parent, want in [
        ("https://ace.awwa.org/x", "https://www.awwa.org/", True),
        ("https://awwa.org/y", "https://www.awwa.org/", True),
        ("https://ca-nv-awwa.org/", "https://www.awwa.org/", False),
        ("https://notawwa.org/", "https://www.awwa.org/", False)]:
        if fed.on_parent_host(url, parent) != want:
            print(f"  FAIL: on_parent_host({url!r}) should be {want} - a "
                  f"subdomain of the national body is the national body")
            errors += 1

    # The words as they are actually written in these listings: NC__Login,
    # /s/Sign_In, /OnlineJoinMain.aspx. A "/login" pattern matched none of
    # them, including the one it was written for.
    for path, want in [("/NC__Login", True), ("/s/Sign_In", True),
                       ("/OnlineJoinMain.aspx", True), ("/individual-online-join", True),
                       ("/registered-members", False), ("/portalside-park", False),
                       ("/joinville-parks", False), ("/chapters/north-carolina", False)]:
        if fed.is_sign_in(path) != want:
            print(f"  FAIL: is_sign_in({path!r}) = {not want}; a sign-in page "
                  f"filed as a chapter site is where the parent's-event trap began")
            errors += 1

    # DRIVEN, NOT READ. The first version asserted the word "login" appeared
    # in stage_parents' source - and it still did after the skip was deleted,
    # because a second skip list further down also says it. So the stage runs
    # end to end against a stubbed fetch and a throwaway file, and what is
    # asserted is the url it CHOSE.
    import tempfile
    listing = ('<a href="https://www.myiacp.org/NC__Login">North Carolina</a>'
               '<a href="https://ncchiefs.example.org/">North Carolina Chiefs</a>'
               '<a href="http://nigpabchapter.ca">nigpabchapter.ca</a>')
    events = {"note": "", "events": [
        {"org_code": "T_NC", "geo": "North Carolina", "parent_national": "IACP",
         "org_url": None, "status": "needs_url"},
        {"org_code": "T_CA", "geo": "California", "parent_national": "IACP",
         "org_url": None, "status": "needs_url"}]}
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gtd-events-")) / "state_events.json"
    tmp.write_text(json.dumps(events))
    keep_events, keep_fetch = fed.EVENTS, fed.fetch
    keep_listings = dict(fed.PARENT_LISTINGS)
    out = io.StringIO()
    try:
        fed.EVENTS = tmp
        fed.fetch = lambda url: listing if "listing.test" in url else None
        fed.PARENT_LISTINGS["IACP"] = ("https://listing.test/chapters", "reads in raw html")
        with contextlib.redirect_stdout(out):
            fed.stage_parents(True)
        rows = {r["org_code"]: r for r in json.loads(tmp.read_text())["events"]}
    finally:
        fed.EVENTS, fed.fetch = keep_events, keep_fetch
        fed.PARENT_LISTINGS.clear(); fed.PARENT_LISTINGS.update(keep_listings)
    nc = rows["T_NC"].get("org_url")
    if nc and "login" in nc.lower():
        print(f"  FAIL: stage_parents chose {nc} as North Carolina's chapter site. "
              f"A login page on the parent's own domain is not a chapter site - "
              f"that is the row every other trap here grew out of")
        errors += 1
    elif nc != "https://ncchiefs.example.org/":
        print(f"  FAIL: stage_parents chose {nc!r} for North Carolina, expected "
              f"the chapter's own site")
        errors += 1
    ca = rows["T_CA"].get("org_url")
    if ca:
        print(f"  FAIL: stage_parents matched California to {ca} - a .ca domain "
              f"is a country, not a state. That filed NIGP's ALBERTA chapter as "
              f"California")
        errors += 1

    # judge() must CONSULT owns(). The helper can be perfect and unused, which
    # is how the seven menus got in. This fixture is a real exhibitor list -
    # it grades "good" and passes every other test - so the only thing that
    # can refuse it is ownership.
    vendors = ["Acme Technologies Inc", "Beta Solutions LLC", "Gamma Systems Inc",
               "Delta Consulting Group", "Epsilon Software Corp", "Zeta Services Ltd",
               "Eta Engineering LLC", "Theta Analytics Inc", "Iota Data Systems",
               "Kappa Networks Inc", "Lambda Cloud Solutions", "Mu Platform Group"]
    good_page = "".join(f'<a href="/x{i}">{n}</a>' for i, n in enumerate(vendors))
    if fed.judge(good_page, "https://events.example.com/list", "IACP", None)[0] != "directory":
        print("  FAIL: the ownership fixture is not accepted even for a non-state "
              "event, so this check would pass with owns() deleted")
        errors += 1
    if fed.judge(good_page, "https://events.example.com/list", "IACP", "North Carolina",
                 "https://www.myiacp.org/NC__Login", IACP)[0] == "directory":
        print("  FAIL: judge() accepts an off-site list that names no state - it "
              "is no longer consulting owns()")
        errors += 1
    return errors


def check_chapter_listings_are_looked_up() -> int:
    """The chapter listing page per parent is a table, not a first-match guess.

    Stage 1 used to fetch each parent's home page and walk the FIRST link
    whose text matched /chapter|affiliate|section/. That found
    apcointl.org/technology/spectrum for APCO, awwa.org/careercenter for AWWA,
    apha.org/membership for APHA, and nothing at all for the ten parents whose
    home page carries no such link. 26 parents, 338 events, 0 resolved.

    Researched per association and looked up, exactly as PARENT_SITES already
    is, because the wording differs per body: AWWA has SECTIONS, WEF has
    MEMBER ASSOCIATIONS, NLC has STATE MUNICIPAL LEAGUES, NACo has STATE
    ASSOCIATIONS. Every parent in the events file must be accounted for -
    with a listing, or with a recorded reason there is none.
    """
    errors = 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_event_directories as fed
    ev = json.loads((DATA / "state_events.json").read_text())["events"]
    parents = {e.get("parent_national") for e in ev if e.get("parent_national")}
    known = set(fed.PARENT_LISTINGS) | set(fed.NO_CHAPTERS) | set(fed.NO_LISTING_PUBLISHED)
    unaccounted = sorted(parents - known)
    if unaccounted:
        print(f"  FAIL: no chapter listing and no recorded reason for "
              f"{unaccounted} - those events cannot be resolved and nothing "
              f"says why")
        errors += 1
    for code, (url, _note) in fed.PARENT_LISTINGS.items():
        if not url.startswith("http"):
            print(f"  FAIL: {code}'s listing url is not an address: {url!r}")
            errors += 1
    overlap = set(fed.PARENT_LISTINGS) & (set(fed.NO_CHAPTERS) | set(fed.NO_LISTING_PUBLISHED))
    if overlap:
        print(f"  FAIL: {sorted(overlap)} both has a listing and is recorded as "
              f"having none")
        errors += 1

    # THE HREF IS UNESCAPED BEFORE THE FRAGMENT IS CUT. NLC writes its 49
    # chapter links entity-encoded - href="http&#x3A;&#x2F;&#x2F;www.akml.org"
    # - and a pattern that excluded "#" to skip fragments matched "http&" and
    # stopped. 19 links read off a page carrying 49; every NLC event lost.
    got = dict(fed.links(
        '<a href="http&#x3A;&#x2F;&#x2F;www.akml.org">Alaska Municipal League</a>'
        '<a href="#top">Skip</a><a href="/x#frag">Deep</a>', "https://www.nlc.org/p/"))
    if got.get("Alaska Municipal League") != "http://www.akml.org":
        print(f"  FAIL: an entity-encoded href is not being decoded before the "
              f"fragment is cut - got {got.get('Alaska Municipal League')!r}. "
              f"That is all 49 NLC chapters")
        errors += 1
    if "Skip" in got:
        print("  FAIL: a bare fragment link is being treated as a chapter link")
        errors += 1

    # Proximity, bounded. Half the listings put the state in a heading and
    # label the link "Web Site"; a state mentioned in prose far above must
    # not claim an unrelated link.
    page = ('<h3>California</h3><a href="https://www.calsheriffs.org/">Web Site</a>'
            '<p>Ohio appears here in prose.</p>' + "x" * 900 +
            '<a href="https://unrelated.test/">Far away</a>')
    if fed.link_near_state(page, "https://x/", "california", lambda h: False) \
            != "https://www.calsheriffs.org/":
        print("  FAIL: the link following a state heading is not being matched")
        errors += 1
    if fed.link_near_state(page, "https://x/", "ohio", lambda h: False) is not None:
        print(f"  FAIL: a state named in prose claimed a link {fed.NEAR}+ "
              f"characters away")
        errors += 1
    return errors



def check_promotion_refuses_a_generated_name() -> int:
    """conferences.json is PUBLIC - a page per event - so what it holds is a claim.

    130 of the 359 staged chapter events carry a name the GENERATOR made by
    filling a state into a template, and register_state_events says so in the
    file itself: "a row is promoted only once it has a directory that answers
    and - if generated - a confirmation that the organisation exists under
    that name". Nothing enforced that, because nothing promoted anything at
    all: `promoted` was written by one script and read by none.

    Now something does, so the rule needs a guard. Four facts are required
    and every one is observed rather than built: a directory a fetch read as
    companies, an organisation name the PARENT'S OWN LISTING confirms, an
    event name the directory page states, and a year. This drives the whole
    stage against a throwaway catalog and asserts what it refused.
    """
    errors = 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import find_event_directories as fed

    for row, want, why in [
        ({"org_name_observed": "Alaska Municipal League", "name_confidence": "pattern"},
         "Alaska Municipal League", "the parent's listing named it"),
        ({"org_name_observed": "(the link following 'california' on the listing)",
          "name_confidence": "pattern"}, None,
         "matched by position, so the NAME is still unconfirmed"),
        ({"org_name": "APWA California Chapter", "name_confidence": "pattern"}, None,
         "a template with a state filled in is not a confirmed organisation"),
        ({"org_name": "Real Association", "name_confidence": "named"},
         "Real Association", "observed when the catalogue was built"),
    ]:
        got = fed.confirmed_name(row)
        if got != want:
            print(f"  FAIL: confirmed_name = {got!r}, expected {want!r} - {why}")
            errors += 1

    # THE SECOND WAY TO CONFIRM A NAME, and it must not be a looser way. A row
    # matched by POSITION on the parent's listing has no name from it, which
    # left three sheriffs' associations carrying 117-135 exhibitors stuck. The
    # organisation's own site can answer - but only if it NAMES THE STATE, or
    # a generic "Home" or another body's page answers for it.
    pages = {
        "https://ncsheriffs.org/": "<title>North Carolina Sheriffs' Association</title>",
        "https://generic.test/": "<title>Home</title><h1>Welcome</h1>",
        "https://other.test/": "<title>Association of Somewhere Else</title>",
        "https://suffix.test/": "<title>Michigan Sheriffs Association | Home</title>",
    }
    keep_fetch = fed.fetch
    try:
        fed.fetch = lambda u: pages.get(u)
        for url, geo, want in [
            ("https://ncsheriffs.org/", "North Carolina", "North Carolina Sheriffs' Association"),
            ("https://suffix.test/", "Michigan", "Michigan Sheriffs Association"),
            ("https://generic.test/", "Florida", None),
            ("https://other.test/", "Florida", None),
        ]:
            got = fed.name_from_own_site({"org_url": url, "geo": geo})
            if got != want:
                print(f"  FAIL: name_from_own_site({url}) = {got!r}, expected "
                      f"{want!r} - a site that does not name its own state has "
                      f"not confirmed anything")
                errors += 1
    finally:
        fed.fetch = keep_fetch

    # Every tag must satisfy the catalog's own pattern, which selftest enforces
    # a few hundred lines up. Chapter names carry parentheses, en dashes and
    # apostrophes; a tag that cannot be written is a promotion that cannot land.
    for base in ["League of California Cities (Cal Cities)",
                 "ACCG – Advancing Georgia's Counties", "Texas Municipal League",
                 "Water Environment Association of Texas / WEAT"]:
        tag = fed._tag(base, "2026", set())
        if not re.match(r"^[\w &.'/-]+ 20\d{2}$", tag):
            print(f"  FAIL: _tag({base!r}) = {tag!r}, which conferences.json's own "
                  f"tag rule would refuse")
            errors += 1
    a = fed._tag("Same Name", "2026", set(), "Texas")
    b = fed._tag("Same Name", "2026", {a}, "Ohio")
    c = fed._tag("Same Name", "2026", {a, b}, None)
    if len({a, b, c}) != 3 or not all(
            re.match(r"^[\w &.'/-]+ 20\d{2}$", x) for x in (a, b, c)):
        print(f"  FAIL: colliding names produced {a!r}, {b!r}, {c!r} - tags must "
              f"be unique AND match the catalog's own charset, which forbids the "
              f"parentheses the first version reached for")
        errors += 1

    # Every catalog place must already exist in conferences.json. A block or
    # department the site does not know files the event nowhere.
    cat = json.loads((DATA / "conferences.json").read_text())["conferences"]
    pairs = {(c["block"], c["department"]) for c in cat}
    for dept, place in fed.CATALOG_PLACE.items():
        if tuple(place) not in pairs:
            print(f"  FAIL: {dept!r} maps to {place}, which is not a "
                  f"(block, department) pair conferences.json already uses")
            errors += 1
    staged_depts = {e.get("department") for e in
                    json.loads((DATA / "state_events.json").read_text())["events"]}
    unmapped = sorted(d for d in staged_depts if d and d not in fed.CATALOG_PLACE)
    if unmapped:
        print(f"  FAIL: staged events use departments with no catalog place: {unmapped}")
        errors += 1

    # THE STAGE ITSELF, driven. The helpers can all be right and unused.
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gtd-promote-"))
    (tmp / "conferences.json").write_text(json.dumps(
        {"note": "", "conferences": [
            {"block": "Executive / administration", "department": "Cities (elected)",
             "conference": "Existing", "event_tag": "Existing 2026",
             "exhibitor_url": "https://taken.test/list"}]}))
    events = {"note": "", "events": [
        {"org_code": "GOOD", "geo": "Texas", "department": "Municipal Government",
         "name_confidence": "pattern", "org_name": "Texas Municipal League",
         "org_name_observed": "Texas Municipal League", "org_url": "https://tml.test/",
         "org_url_source": "https://www.nlc.org/list", "directory_url": "https://tml.test/exhibitors",
         "directory_note": "40 names, good", "status": "directory_found", "promoted": False},
        {"org_code": "GUESS", "geo": "Ohio", "department": "Municipal Government",
         "name_confidence": "pattern", "org_name": "APWA Ohio Chapter",
         "org_url": "https://oh.test/", "directory_url": "https://oh.test/exhibitors",
         "status": "directory_found", "promoted": False},
        {"org_code": "NOYEAR", "geo": "Iowa", "department": "Public Works",
         "name_confidence": "named", "org_name": "Iowa Public Works",
         "org_url": "https://ia.test/", "directory_url": "https://ia.test/exhibitors",
         "status": "directory_found", "promoted": False},
        {"org_code": "DUPE", "geo": "Utah", "department": "Municipal Government",
         "name_confidence": "named", "org_name": "Utah League",
         "org_url": "https://ut.test/", "directory_url": "https://taken.test/list",
         "status": "directory_found", "promoted": False},
    ]}
    (tmp / "state_events.json").write_text(json.dumps(events))
    pages = {
        "https://tml.test/exhibitors": "<title>2026 TML Annual Conference Exhibitors</title>",
        "https://oh.test/exhibitors": "<title>2026 Annual Conference Sponsors</title>",
        "https://ia.test/exhibitors": "<title>Exhibitors</title><p>no year anywhere</p>",
        "https://taken.test/list": "<title>2026 Annual Conference</title>",
    }
    keep = (fed.EVENTS, fed.DATA, fed.fetch)
    out = io.StringIO()
    try:
        fed.EVENTS, fed.DATA = tmp / "state_events.json", tmp
        fed.fetch = lambda u: pages.get(u)
        with contextlib.redirect_stdout(out):
            fed.stage_promote(True)
        got = json.loads((tmp / "conferences.json").read_text())["conferences"]
        rows = {r["org_code"]: r for r in json.loads((tmp / "state_events.json").read_text())["events"]}
    finally:
        fed.EVENTS, fed.DATA, fed.fetch = keep
    promoted = {c.get("state_event", {}).get("org_code") for c in got if c.get("state_event")}
    if promoted != {"GOOD"}:
        print(f"  FAIL: promoted {sorted(promoted)}; only GOOD is confirmed. "
              f"GUESS carries a generated name, NOYEAR states no year, and DUPE "
              f"points at a url already in the catalog")
        errors += 1
    if rows["GUESS"].get("promoted") or rows["NOYEAR"].get("promoted"):
        print("  FAIL: a refused row was marked promoted")
        errors += 1
    good = next((c for c in got if c.get("state_event", {}).get("org_code") == "GOOD"), None)
    if good:
        if good["conference"] != "2026 TML Annual Conference Exhibitors":
            print(f"  FAIL: the conference name is {good['conference']!r}; the page "
                  f"states one and it must be read, not built")
            errors += 1
        if good["block"] != "Executive / administration" or good["department"] != "Cities (elected)":
            print(f"  FAIL: GOOD was filed at {good['block']}/{good['department']}")
            errors += 1
        if not re.match(r"^[\w &.'/-]+ 20\d{2}$", good.get("event_tag", "")):
            print(f"  FAIL: promoted tag {good.get('event_tag')!r} is not legal")
            errors += 1
        if good.get("swept") is not False or good.get("flagship") is not False:
            print("  FAIL: a freshly promoted event is neither swept nor flagship")
            errors += 1
    return errors



def check_the_domain_lives_in_one_place() -> int:
    """No shipped file may carry the domain as a string literal.

    data/brand.json says a rebrand is "an edit to this file rather than a hunt
    through five languages", and check_brand holds functions/_brand.js against
    it. Neither noticed that index.html had the domain typed into it twice -
    the iCalendar UID and the description on every conference download.

    That came due on 2026-09-02, when the board moved to sledjobs.com and
    solesourcejobs.com was set aside for a separate FEDERAL board. Two strings
    on the site would have gone on stamping calendar entries with a domain
    about to belong to a different product, and nothing would have said so.

    A literal in a COMMENT is fine and this file is full of them - the rule is
    about strings the program emits. So the search is for the domain inside
    quotes or backticks, which is where an emitted one lives.
    """
    import brand as _brand
    errors = 0
    # Both names: the one in use, and the one being handed to another product.
    domains = {_brand.DOMAIN, "solesourcejobs.com"}
    allowed = {"data/brand.json", "functions/_brand.js"}
    pat = re.compile(r"""["'`][^"'`\n]*\b(%s)\b""" %
                     "|".join(re.escape(d) for d in domains))
    for rel in ["index.html", "alerts.html", "admin-web.html"]:
        f = ROOT / rel
        if f.exists() and pat.search(f.read_text()):
            m = pat.search(f.read_text())
            print(f"  FAIL: {rel} carries a domain as a string literal near "
                  f"{m.group(0)[:60]!r}. The domain lives in data/brand.json; a "
                  f"page reads location.hostname or is given the value")
            errors += 1
    for f in sorted((ROOT / "scripts").glob("*.py")) + \
             sorted((ROOT / "functions").rglob("*.js")):
        rel = str(f.relative_to(ROOT))
        if rel in allowed or f.name == "selftest.py":
            continue
        src = f.read_text()
        if f.suffix == ".py":
            src = re.sub(r'"""[\s\S]*?"""', "", src)
            src = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
        else:
            src = re.sub(r"/\*[\s\S]*?\*/", "", src)
            src = "\n".join(re.sub(r"//.*$", "", ln) for ln in src.splitlines())
        m = pat.search(src)
        if m:
            print(f"  FAIL: {rel} carries a domain as a string literal near "
                  f"{m.group(0)[:60]!r} - import brand (Python) or _brand.js "
                  f"(Worker) instead")
            errors += 1

    # And the sending address must stay somewhere Resend has verified. Moving
    # it to a domain Resend has never seen makes every alert fail to send with
    # the endpoint still answering 200 - the failure nobody sees.
    if not _brand.FROM.endswith("@solesourcejobs.com>"):
        who = _brand.FROM.split("@")[-1].rstrip(">")
        print(f"  NOTE: the sending address moved to {who}. That is correct "
              f"ONLY if Resend shows {who} verified with its DNS published; "
              f"if it does not, alerts fail silently. Delete this check's "
              f"note once the move is confirmed.")
    return errors


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
        # THE SEAT OF GOVERNMENT, SPELLED THE WAY BOARDS ACTUALLY SPELL IT.
        # Only "Washington, D.C." resolved. The bare-DC forms did not, because
        # the word Washington counted as the STATE as well as the city, and
        # geography() reads two states as a coverage list and names no office at
        # all. Sixteen postings in DC, on a board about government technology,
        # had no desk on the map or the /s/dc page. Kansas City lost two the
        # same way, its name carrying Kansas alongside the Missouri it sits in.
        ("Washington, DC", "Washington"),
        ("Washington DC", "Washington"),
        ("Washington, DC, United States", "Washington"),
        ("Washington, District of Columbia, United States", "Washington"),
        ("Hybrid - Kansas City, MO", "Kansas City"),
        ("Kansas City, Missouri", "Kansas City"),
        # DESKS THE HTML EXTRACTOR USED TO SWALLOW. Both of these were inside a
        # title and nowhere else - "Network Engineer, Axon 911 Scottsdale,
        # Arizona, United States" and uveye's "Supply Chain Analyst Teaneck, NJ
        # Full-time More Details Less Details" - so the postings had no office,
        # appeared on no map and on no /s/<state> page. They are locations now,
        # and a location is only worth recovering if it resolves.
        ("Scottsdale, Arizona, United States", "Scottsdale"),
        ("Teaneck, NJ", "Teaneck"),
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
    # A LIST OF STATES IS STILL A LIST OF STATES. Reading "Washington" out of
    # "Washington, DC" as the city rather than a second state must not soften
    # the rule it lives inside: a location naming several places is a coverage
    # or eligibility list, not a desk, and none of these may produce an office.
    # Every one is on the board today.
    for loc in ("Ohio, Michigan", "Indiana, Kentucky, Tennessee", "CO, NM, UT",
                "San Francisco, CA | Washington, DC",
                "Addison, TX (Hybrid); Bellevue, WA (Hybrid); Durham, NC "
                "(Hybrid); Emeryville, CA (Hybrid)"):
        got = _roles.geography(loc, "Account Executive")["office"]
        if got:
            errors += fail(f"geography({loc!r}) named an office {got} - a "
                           f"location listing several places is not a desk")
    # ...and a city whose name CONTAINS a state name, with no other state to
    # fall back on, still pins the seat to that state. Reading "New York City"
    # as carrying no state at all cost forty postings their only location -
    # more than the eighteen the DC fix recovered.
    for loc, want in (("New York City", "NY"), ("Kansas City", "KS"),
                      ("New York City; San Francisco", "NY")):
        got = _roles.geography(loc, "Account Executive")["office"]
        if not got or got.get("state") != want or got.get("city") is not None:
            errors += fail(f"geography({loc!r}) office = {got}, expected a bare "
                           f"state {want} - the city-name rule emptied it")
    # "Socorro, New Mexico" is in the United States. The country's name is
    # inside the state's, and NON_US matched the wrong one.
    if _roles.is_us("Socorro, New Mexico", "Assistant Store Manager") is not True:
        errors += fail("is_us('Socorro, New Mexico') is not True - New Mexico "
                       "is a state, not the country inside its name")

    # AND A TWO-LETTER CODE THAT *IS* A US STATE, ATTACHED TO A CITY THAT IS
    # NOT IN IT. The list above works because UK, QB, UP and MH are not states;
    # IL and IN are, so a foreign address written with an ISO COUNTRY code walks
    # straight through every test and files a desk six thousand miles from the
    # job. "Hyderabad, Telengana, IN" was on the board as an Indiana office,
    # seven times, and reading Doorman-style card locations out of the html
    # extractor was about to add "Tel-Aviv, IL" as an Illinois one four more.
    #
    # NEITHER HALF ALONE IS ENOUGH. Refusing the CITY and then handing back a
    # bare "state IL" moves the wrong answer rather than removing it: the state
    # page is where it would show up either way. And is_us has to agree, or the
    # posting is still counted as American.
    for loc in ("Tel-Aviv, IL", "Hyderabad, Telengana, IN"):
        got = _roles.geography(loc, "Account Executive")["office"]
        if got:
            errors += fail(f"geography({loc!r}) claimed a US office {got} - the "
                           f"two letters are a country code, not the state")
        if _roles.is_us(loc, "Account Executive") is not False:
            errors += fail(f"is_us({loc!r}) is not False - a foreign address "
                           f"written with a state-shaped country code")
    # ...and the states themselves must survive being guarded against. Every one
    # of these is a real US desk on the board today.
    for loc, want in (("Chicago, IL", "Chicago"), ("Champaign, IL", "Champaign"),
                      ("Fort Wayne, IN", "Fort Wayne")):
        got = _roles.geography(loc, "Account Executive")["office"]
        if not got or got.get("city") != want or not _roles.is_us(loc):
            errors += fail(f"geography({loc!r}) office = {got}, expected city "
                           f"{want!r} - the guard took a real US desk with it")
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

    # A DIRECTION IN FRONT OF A CONTINENT IS NOT A US REGION. Four live
    # quota-carrying postings asserted one they do not have - MSAB's "Account
    # Executive Eastern Europe" rendered on the public jobs tab as "Northeast
    # (territory)", and Via, Versaterm and Dataminr did the same. The four US
    # cases are here because the obvious fix - dropping "eastern"/"western"
    # from REGION_WORDS - silently breaks every real territory title.
    REGION_CASES = [
        ("Account Executive Eastern Europe", None),
        ("Account Executive, Western Europe", None),
        ("Account Executive, UK & Western Europe", None),
        ("Account Executive, Public Sector - Central and Eastern Europe", None),
        ("Sales Manager, Western Canada", None),
        ("Enterprise Account Executive, EMEA - Northern Europe", None),
        ("Account Executive, Northeast", "Northeast"),
        ("Regional Sales Manager - West Coast", "West"),
        ("Enterprise AE - Eastern Territory", "Northeast"),
        ("AE - Western Region", "West"),
    ]
    for title, want in REGION_CASES:
        got = _roles.geography("", title)["territory"]["region"]
        if got != want:
            errors += fail(
                f"geography('', {title!r}) region = {got!r}, expected {want!r}"
                + ("  - a role outside the United States is being filed under "
                   "a US region" if want is None else
                   "  - a real US territory title stopped being read"))

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
    errors += check_every_check_is_actually_run()
    errors += check_ship_path_attaches_active()
    errors += check_stored_roles_are_labelled_as_stale()
    errors += check_pay_report_arithmetic()
    errors += check_built_pages_count_openings_not_rows()
    errors += check_momentum_counts_openings_not_rows()
    errors += check_active_badge_is_shipped_honestly()
    errors += check_conference_dates_engine()
    errors += check_acquisition_bands_cover_every_strength()
    errors += check_board_stated_mode_and_office()
    errors += check_alert_preview_matches_the_digest()
    errors += check_linkedin_is_the_companys_own()
    errors += check_find_boards_reads_real_pages()
    errors += check_posted_date_is_the_employers()
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
    errors += check_capture_link_rule()
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
    errors += check_workday_pages_to_the_end()
    errors += check_mail_shell()
    errors += check_fetchers_page_and_do_not_fake_zeros()
    errors += check_the_crawler_paces_itself()
    errors += check_embedded_wiring()
    errors += check_capture_parity()
    errors += check_extension_icons()
    errors += check_open_actions_never_write_the_map()
    errors += check_worklist_drops_what_was_worked()
    errors += check_identify_catches_a_namesake()
    errors += check_fetchers_page_through_their_callers()
    errors += check_the_gate_is_where_the_requests_are()
    errors += check_mail_is_built_from_the_shell()
    errors += check_capture_keeps_two_reqs_with_one_title()
    errors += check_task_notes_land_honestly()
    errors += check_sweep_keeps_its_own_work()
    errors += check_extension_holds_and_refuses()
    errors += check_intake_writes_only_issued_tags()
    errors += check_a_menu_is_not_a_directory()
    errors += check_a_chapter_directory_is_not_its_parents()
    errors += check_chapter_listings_are_looked_up()
    errors += check_promotion_refuses_a_generated_name()
    errors += check_the_domain_lives_in_one_place()

    for raw, expected in TITLE_TEXT_CASES:
        got = ats.plain(raw)
        if got != expected:
            errors += fail(f"ats.plain({raw!r}) = {got!r}, expected {expected!r}")
    for raw, expected in CTA_CASES:
        got = ats.strip_cta(raw)
        if got != expected:
            errors += fail(f"ats.strip_cta({raw!r}) = {got!r}, expected {expected!r}")
    for inner, want_lines in CARD_LINE_CASES:
        got_lines = ats._card_lines(inner)
        if got_lines != want_lines:
            errors += fail(f"ats._card_lines({inner[:48]!r}...) = {got_lines!r}, "
                           f"expected {want_lines!r}")
    for lines, flat, w_title, w_loc, w_pay in CARD_CASES:
        title, loc, comp = ats.card_fields(lines, flat)
        pay = (comp or {}).get("raw")
        if (title, loc, pay) != (w_title, w_loc, w_pay):
            errors += fail(
                f"ats.card_fields({lines!r}) = {(title, loc, pay)!r}, "
                f"expected {(w_title, w_loc, w_pay)!r}")
    errors += check_board()
    errors += check_rival_door_refuses_a_category()
    errors += check_every_queue_has_a_renderer()
    errors += check_proposal_rulings_cover_every_kind()
    errors += check_ingest_keeps_refusals()
    errors += check_company_page_profile_states()
    errors += check_dechrome_keeps_sentences_and_drops_chrome()
    errors += check_site_pages_stay_out_of_git()
    errors += check_profile_fetch_stays_first_party()
    errors += check_rival_brief_never_cuts_the_roster()
    errors += check_company_counts_are_roles_not_postings()
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
          f"{len(CTA_CASES)} button-label, {len(CARD_CASES)} card-split, "
          f"{len(CARD_LINE_CASES)} card-line")
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
