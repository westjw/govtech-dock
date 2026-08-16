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
    if SALESY_PAT.search(t):
        return "sales_other"
    return "none"


def scan_pagetext(text: str) -> str:
    """For 'html'-type boards we only have page text, not discrete titles.
    Look for AE-ish phrases in visible text. Conservative: page text mentioning
    'account executive' etc. usually means a live posting on that page."""
    if AE_PAT.search(text) and not re.search(r"no (current )?open(ings| positions)", text, re.I):
        return "ae"
    if re.search(r"sales", text, re.I):
        return "sales_other"
    return "none"


def rollup(jobs: list[dict]) -> tuple[str, str, list[dict]]:
    """Roll a fetched job list up to (status, note, ae_roles)."""
    ae_roles, sales_other = [], []
    for j in jobs:
        if "_pagetext" in j:
            verdict = scan_pagetext(j["_pagetext"])
            if verdict == "ae":
                ae_roles.append({"title": "AE-type role (page scan)", "location": "",
                                 "url": j.get("url", "")})
            elif verdict == "sales_other":
                sales_other.append(j)
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
    return "None found", "", []
