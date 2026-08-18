"""Classify any job title into a role family, and decide if a posting is US-based.

The tracker originally asked one question: is this an AE req? Everything else was
discarded before storage, so a company hiring twelve engineers and no sellers
looked identical to a company hiring nobody. For a job board that is fatal, and
for market intelligence it throws away the most useful signal there is: WHAT KIND
of hiring a company is doing tells you where it is in its life. Heavy engineering
means building. Heavy GTM means a go-to-market push. Heavy CS and field means
absorbing customers it already won.

Ordering carries the judgments. "Sales Engineer" is GTM, not engineering.
"Revenue Operations" is ops, not sales. "Associate General Counsel, Revenue" is a
lawyer, not a seller. Each is a specific decision and selftest.py pins them.
"""
from __future__ import annotations

import re

FAMILIES = ("gtm", "cs", "ops", "engineering", "product", "data", "policy",
            "ga", "exec", "field", "other")

LABEL = {
    "gtm": "GTM (sales, marketing, BD)",
    "cs": "Customer Success and Support",
    "ops": "Operations and RevOps",
    "engineering": "Engineering",
    "product": "Product and Design",
    "data": "Data, Analytics and Research",
    "policy": "Policy and Government Affairs",
    "ga": "G&A (finance, legal, people)",
    "exec": "Executive",
    "field": "Field, implementation and services",
    "other": "Unclassified",
}

# Extraction artifacts. Some careers pages yield image filenames and stray markup;
# "Xplor_Logo_withTag_Color.png" is not an opening and must never reach the board.
JUNK = re.compile(r"\.(png|jpe?g|svg|gif|webp|pdf|css|js)\b|^\s*[\W_]+\s*$|"
                  r"^(logo|image|icon|banner|photo)\b", re.I)

# Talent-pool pages that never close. Counting them inflates every hiring number.
EVERGREEN = re.compile(
    r"general application|don.?t see (a|the) (job|role|position)|"
    r"talent (pool|community|network)|future opportunit|open application|"
    r"speculative|join our (talent|network)|other opportunit|"
    r"submit your (resume|cv)|no (matching )?role|\(evergreen\)|\bevergreen\b", re.I)


def is_junk(title: str) -> bool:
    t = (title or "").strip()
    return not t or len(t) < 3 or bool(JUNK.search(t))


def is_evergreen(title: str) -> bool:
    return bool(EVERGREEN.search(title or ""))


RULES: list[tuple[str, re.Pattern]] = [
    ("exec", re.compile(r"\b(chief\s+\w+\s+officer|\bc[efimoprt]o\b|founder|"
                        r"president\b|general manager)\b", re.I)),
    # Legal and admin before GTM: "Associate General Counsel, Revenue" matched the
    # GTM rule on the word "revenue" and read as a seller. It is a lawyer.
    ("ga", re.compile(r"\b(general counsel|counsel\b|paralegal|attorney|legal\b|"
                      r"office administrator|sales administrator)", re.I)),
    # Sales engineering is GTM even though "engineer" appears. Precedes engineering.
    ("gtm", re.compile(r"\b(sales engineer|solutions? (engineer|consultant|architect)|"
                       r"pre.?sales|technical account manager)\b", re.I)),
    # Policy is a real govtech lane, not a subspecies of marketing.
    ("policy", re.compile(r"\b(government affairs|public affairs|public policy|"
                          r"policy (manager|lead|advisor|analyst|director|counsel)|"
                          r"regulatory affairs|legislative|lobby|\w+ affairs\b|"
                          r"community engagement|civic engagement)\b", re.I)),
    ("ops", re.compile(r"\b(revenue operations|rev\s?ops|sales operations|"
                       r"business operations|biz\s?ops|deal desk|sales enablement|"
                       r"strategy (and|&) operations|strategy & ops|"
                       r"program manager|programme manager|transformation|"
                       r"strategy (principal|associate|manager|lead))\b", re.I)),
    # Bid and proposal work is how public-sector deals are actually won.
    ("gtm", re.compile(r"\b(proposals?\b|\bbids?\b|tenders?|\brfp\b|capture manager|"
                       r"account management|social media|brand marketing)\b", re.I)),
    ("gtm", re.compile(r"\b(account executive|account manager|sales|seller|"
                       r"paid media|website|seo\b|digital marketing|field marketing|"
                       r"product marketing|business development|\bbdr\b|\bsdr\b|"
                       r"territory|quota|marketing|demand gen\w*|growth|partnerships?|"
                       r"channel|revenue|go.to.market|\bgtm\b|brand|communications?|"
                       r"content|community manager|events?)\b", re.I)),
    ("cs", re.compile(r"\b(customer success|client success|customer experience|"
                      r"technical success|support lead|technical solutions|"
                      r"customer delivery|client advocate|customer advocate|"
                      r"client services|account management associate|"
                      r"support (engineer|specialist|representative|manager|agent)|"
                      r"technical support|helpdesk|help desk|onboarding specialist|"
                      r"renewals?)\b", re.I)),
    ("field", re.compile(r"\b(implementation|deployment|professional services|"
                         r"training\b|curriculum|consultant\b|dispatcher|maintenance|"
                         r"service (supervisor|manager|technician)|utility worker|"
                         r"operator\b|driver\b|construction|superintendent|"
                         r"project manager|project engineer|"
                         r"safety (manager|coordinator|specialist)|"
                         r"solutions? delivery|field (service|technician|operations)|"
                         r"installation|trainer|technical program manager)\b", re.I)),
    ("data", re.compile(r"\b(data (scientist|engineer|analyst|architect)|analytics|"
                        r"scientist|research (scientist|engineer|lead)|machine learning|"
                        r"\bml\b|business intelligence|statistician)\b", re.I)),
    ("engineering", re.compile(r"\b(engineer|engineering|developer|programmer|software|"
                               r"firmware|hardware|devops|\bsre\b|platform|"
                               r"infrastructure|security engineer|qa\b|test engineer|"
                               r"architect|technician|robotics|mechanical|electrical)\b",
                               re.I)),
    ("product", re.compile(r"\b(product manag\w*|product owner|vp,? (of )?product|"
                           r"product specialist|head of product|product design|"
                           r"\bux\b|\bui\b|designer|design lead|user research)\b", re.I)),
    ("ga", re.compile(r"\b(recruit|talent|people ops|human resources|\bhr\b|fp&a|"
                      r"financial analyst|collections|compensation|strategic sourcing|"
                      r"procurement|vendor management|warehouse|facilities|finance|"
                      r"accountant|accounting|controller|payroll|compliance|"
                      r"executive assistant)\b", re.I)),
]


def family(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "other"
    for fam, pat in RULES:
        if pat.search(t):
            return fam
    return "other"


# GTM titles that do not carry a number: a TAM is post-sales, an SE supports a
# quota rather than owning one.
NOT_QUOTA = re.compile(r"technical account manager|sales engineer|solutions? "
                       r"(engineer|consultant|architect)|pre.?sales|"
                       r"account management associate", re.I)
QUOTA = re.compile(r"\b(account executive|account manager|sales (executive|"
                   r"representative|manager|director)|territory manager|"
                   r"regional sales|enterprise sales|named account|seller)\b", re.I)


def is_quota_carrying(title: str) -> bool:
    if family(title) != "gtm" or NOT_QUOTA.search(title or ""):
        return False
    return bool(QUOTA.search(title or ""))


NON_US = re.compile(
    r"\b(EMEA|APAC|LATAM|United Kingdom|\bUK\b|England|Scotland|Wales|Ireland|"
    r"London|Manchester|Dublin|Limerick|Cork|Paris|France|Berlin|Munich|Germany|"
    r"Madrid|Barcelona|Spain|Amsterdam|Netherlands|Brussels|Belgium|Zurich|"
    r"Switzerland|Stockholm|Sweden|Oslo|Norway|Copenhagen|Denmark|Helsinki|"
    r"Warsaw|Poland|Prague|Budapest|Hungary|Vienna|Austria|Lisbon|Portugal|"
    r"Milan|Rome|Italy|Athens|Greece|Istanbul|Turkey|Tel Aviv|Israel|Dubai|"
    r"Toronto|Vancouver|Montreal|Ottawa|Canada|Mexico|Brazil|S[aã]o Paulo|"
    r"Argentina|Chile|Colombia|Sydney|Melbourne|Australia|Auckland|New Zealand|"
    r"Singapore|Tokyo|Japan|Seoul|Korea|Hong Kong|Shanghai|Beijing|China|"
    r"Bangalore|Bengaluru|Mumbai|Delhi|India|Manila|Philippines|Jakarta|"
    r"Indonesia|Bangkok|Thailand|Vietnam|Johannesburg|South Africa|Nairobi|"
    r"Kenya|Cairo|Egypt)\b", re.I)
US_HINT = re.compile(r"\b(United States|\bUSA?\b|U\.S\.|remote.{0,12}\bus\b|"
                     r"\bus\b.{0,12}remote|nationwide)\b", re.I)
STATE = re.compile(r",\s*(A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|"
                   r"N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY]|DC)\b")


def is_us(location_text: str, title: str = "") -> bool | None:
    """True, False, or None when undeterminable.

    None matters: a posting with no location should be visibly unknown rather
    than silently dropped from a US view. Guessing 'not US' hides real openings.
    """
    blob = f"{location_text or ''} {title or ''}"
    if not blob.strip():
        return None
    if NON_US.search(blob):
        return False
    if US_HINT.search(blob) or STATE.search(blob):
        return True
    if re.search(r"\bremote\b", blob, re.I):
        return None
    return None
