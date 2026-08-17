"""Classify job titles into AE / non-AE sales / not-sales, and roll up a
company hiring status.

Statuses (must stay in sync with site + xlsx exporter):
  "Yes"            - at least one quota-carrying field sales role is open
  "Sales (non-AE)" - sales org is hiring, but only SDR/BDR/CS/leadership/etc.
  "None found"     - board was read successfully, no sales roles
  "Unknown"        - board could not be read (JS wall, bad slug, network)
"""
from __future__ import annotations

import re

# Quota-carrying field sales titles ("AE-equivalent").
AE_PAT = re.compile(
    r"account executive|account exec\b|sales executive|sales rep(resentative)?\b"
    r"|territory (sales )?manager|regional sales (manager|director)"
    r"|area sales manager|named account manager|enterprise sales\b"
    r"|strategic account (executive|manager)|regional account (executive|manager)"
    r"|account manager|business development manager|sales director",
    re.I,
)

# Sales-org roles that are definitively not AE reqs. A match here always means
# sales_other, and it beats an AE-looking fragment in the same title
# ("Inside Sales Account Manager" is not an AE req).
SALES_ORG_NON_AE_PAT = re.compile(
    r"\bsdr\b|\bbdr\b|sales development|business development (rep|representative|associate)"
    r"|inside sales|customer success|client success|account management associate"
    r"|solutions? (engineer|consultant|architect)|sales engineer|presales|pre-sales"
    r"|revenue operations|rev ?ops|sales operations|sales enablement|deal desk"
    r"|renewals?\b|channel|alliances|partner(ships?)? manager",
    re.I,
)

# Roles that only count as sales when the title says so ("VP, Sales" yes;
# "VP, Engineering" no). Leadership isn't an AE req either way.
AMBIGUOUS_PAT = re.compile(
    r"vp\b|vice president|head of|chief|\bcro\b|marketing|demand gen|growth\b",
    re.I,
)

# Anything that signals the sales org at all (for the ambiguous check).
SALESY_PAT = re.compile(r"sales|account|business development|revenue|go.to.market|gtm", re.I)

# Finance roles. SALESY_PAT matches the bare substring "account", so without
# this guard "Senior Accountant" and "Accounting Manager, Lease & Fixed Assets"
# both land in Sales (non-AE). Checked after AE_PAT, so a genuine
# "Account Executive, Accounting Software" still classifies as an AE req.
FINANCE_NOT_SALES_PAT = re.compile(
    r"\baccountant\b|\baccounting\b|accounts (payable|receivable)"
    r"|\bbookkeep|\bcontroller\b|\bauditor\b",
    re.I,
)


def classify_title(title: str) -> str:
    """Return 'ae' | 'sales_other' | 'none' for one job title."""
    t = title.strip()
    if not t:
        return "none"
    if SALES_ORG_NON_AE_PAT.search(t):
        return "sales_other"
    if AMBIGUOUS_PAT.search(t):
        # leadership/marketing beats the AE match ("VP, Sales" is not an AE req)
        return "sales_other" if SALESY_PAT.search(t) else "none"
    if AE_PAT.search(t):
        return "ae"
    if FINANCE_NOT_SALES_PAT.search(t):
        return "none"
    if SALESY_PAT.search(t):
        return "sales_other"
    return "none"


# A board that explicitly says it has nothing open. This is the only way a page
# scan can *establish* absence - otherwise an empty scan is indistinguishable
# from a JS shell whose listings never rendered.
# \b0\b matters: without it "Sales 130 jobs" matches "0 jobs" and a busy board
# reads as an empty one.
NO_OPENINGS_PAT = re.compile(
    r"no (current(ly)? )?(open |available )?(openings|positions|jobs|roles|vacancies)"
    r"|there are (currently )?no open|no results found|\b0 (open )?(jobs|positions)\b",
    re.I,
)


# Page scans only. SALES_ORG_NON_AE_PAT is tuned for discrete job titles, where
# every string it sees is already known to be a title. Loose on free page text:
# "Customer Success" and "Channel" are nav sections on half the marketing sites
# in this list. Require the role noun too, so we match reqs and not navigation.
PAGESCAN_SALES_ROLE_PAT = re.compile(
    r"\bsdr\b|\bbdr\b"
    r"|sales development (rep\b|representative|manager)"
    r"|business development (rep\b|representative|associate)"
    r"|inside sales (rep\b|representative|associate|manager)"
    r"|(customer|client) success (manager|associate|specialist|lead|director)"
    r"|sales (engineer|operations manager|enablement manager|trainer)"
    r"|solutions? (engineer|consultant|architect)"
    r"|renewals? (manager|specialist)"
    r"|(channel|partnerships?) (manager|director)",
    re.I,
)


def scan_pagetext(text: str) -> str:
    """For 'html'-type boards we only have page text, not discrete titles.

    Returns 'ae' | 'sales_other' | 'none' | 'unreadable'. A page scan can only
    ever prove *presence*: finding "Account Executive" means a live posting,
    but finding nothing means either an empty board or - far more often - a
    JS shell whose listings never loaded. So we assert a status only on
    concrete evidence, and call everything else unreadable rather than
    reporting a false "None found" that would hide a warm door.

    Concrete evidence is checked before any "no openings" claim: such text is
    often an inert JS template branch ("There are no open positions matching
    your filter selection") sitting on a page that is in fact full of roles.
    """
    if AE_PAT.search(text):
        return "ae"
    if PAGESCAN_SALES_ROLE_PAT.search(text):
        return "sales_other"                # a concrete non-AE sales title rendered
    if NO_OPENINGS_PAT.search(text):
        return "none"                       # board rendered; it is genuinely empty
    return "unreadable"


def rollup(jobs: list[dict]) -> tuple[str, str, list[dict]]:
    """Roll a fetched job list up to (status, note, ae_roles)."""
    ae_roles, sales_other = [], []
    unreadable = False
    for j in jobs:
        if "_pagetext" in j:
            verdict = scan_pagetext(j["_pagetext"])
            if verdict == "ae":
                ae_roles.append({"title": "AE-type role (page scan)", "location": "",
                                 "url": j.get("url", "")})
            elif verdict == "sales_other":
                sales_other.append(j)
            elif verdict == "unreadable":
                unreadable = True
            continue
        verdict = classify_title(j.get("title", ""))
        if verdict == "ae":
            ae_roles.append({k: j.get(k, "") for k in ("title", "location", "url")})
        elif verdict == "sales_other":
            sales_other.append(j)

    if ae_roles:
        first = ae_roles[0]
        note = first["title"][:34] + (f" ({first['location'][:18]})" if first["location"] else "")
        return "Yes", note.strip(), ae_roles
    if sales_other:
        titles = [j.get("title", "") for j in sales_other if j.get("title")]
        note = (titles[0][:34] + " only") if titles else "sales roles, none AE"
        return "Sales (non-AE)", note, []
    if unreadable:
        return "Unknown", "page scan found no listings", []
    return "None found", "", []
