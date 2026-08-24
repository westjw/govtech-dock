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

import json
import pathlib
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
    r"submit your (resume|cv)|no (matching )?role|\(evergreen\)|\bevergreen\b|spontaneous application|interested in joining|"
    r"can.?t find|didn.?t find|general interest|future consideration|"
    r"create your own role", re.I)


def is_junk(title: str) -> bool:
    t = (title or "").strip()
    return not t or len(t) < 3 or bool(JUNK.search(t))


def is_evergreen(title: str) -> bool:
    return bool(EVERGREEN.search(title or ""))


RULES: list[tuple[str, re.Pattern]] = [
    # "Chief Services and Delivery Officer" has three words between chief and
    # officer, so a single \w+ never matched it.
    ("exec", re.compile(r"\b(chief[\w\s,&/-]{0,40}officer|chief of staff|"
                        r"\bc[efimoprt]o\b|founder|president\b|general manager)\b", re.I)),
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
                       r"strategy (principal|associate|manager|lead)|"
                       # "People/Talent Operations Manager" is HR, not business ops,
                       # and ops is tested before ga so it would win by position
                       r"deal operations|(?<!people )(?<!talent )operations "
                       r"(manager|lead|coordinator)|"
                       r"change manager|service delivery)\b", re.I)),
    # Bid and proposal work is how public-sector deals are actually won.
    ("gtm", re.compile(r"\b(proposals?\b|\bbids?\b|tenders?|\brfp\b|capture manager|"
                       r"account management|social media|brand marketing|"
                       r"strategic accounts?|account director|lead generation|"
                       r"partner (development|success)|alliances|enablement|"
                       r"campaign operations|commercial (director|development)|"
                       r"field marketer)\b", re.I)),
    ("gtm", re.compile(r"\b(account executive|account manager|sales|seller|"
                       r"paid media|website|seo\b|digital marketing|field marketing|"
                       r"product marketing|business development|\bbdr\b|\bsdr\b|"
                       r"territory|quota|marketing|demand gen\w*|growth|partnerships?|"
                       r"channel|revenue|go.to.market|\bgtm\b|brand|communications?|"
                       r"content|community manager|events?|account development|"
                       r"commercial (lead|manager|director|executive)|"
                       r"country (director|manager)|\bbd manager)\b", re.I)),
    ("cs", re.compile(r"\b(customer success|client success|customer experience|"
                      r"customer (engagement|service|trust|care)|"
                      r"support (analyst|lead|desk)|application support|"
                      r"technical success|technical solutions|"
                      r"customer delivery|client advocate|customer advocate|"
                      r"client services|account management associate|"
                      r"support (engineer|specialist|representative|manager|agent)|"
                      r"technical support|helpdesk|help desk|onboarding specialist|"
                      r"renewals?|call cent(er|re)|application specialist)\b", re.I)),
    ("field", re.compile(r"\b(implementation|deployment|professional services|"
                         r"training\b|curriculum|consultant\b|dispatcher|maintenance|"
                         r"mechanic|road supervisor|protection agent|fleet technician|"
                         r"service (supervisor|manager|technician)|utility worker|"
                         r"operator\b|driver\b|construction|superintendent|"
                         r"project manager|project engineer|"
                         r"safety (manager|coordinator|specialist)|"
                         r"solutions? delivery|field (service|technician|operations)|"
                         r"installation|trainer|technical program manager|"
                         r"assembler|material handler|machinist|fabrication|"
                         r"manufacturing (supervisor|technician|associate)|"
                         r"installer)\b", re.I)),
    ("data", re.compile(r"\b(data (scientist|engineer|analyst|architect|annotator)|"
                        r"business analyst|applied ai|analytics|"
                        r"scientist|research (scientist|engineer|lead)|machine learning|"
                        r"\bml\b|business intelligence|statistician)\b", re.I)),
    ("engineering", re.compile(r"\b(engineer|engineering|developer|programmer|software|"
                               r"member of technical staff|technical lead|"
                               r"site reliability|"
                               r"firmware|hardware|devops|\bsre\b|platform|"
                               r"infrastructure|security engineer|qa\b|test engineer|"
                               r"architect|technician|robotics|mechanical|electrical)\b",
                               re.I)),
    ("product", re.compile(r"\b(product manag\w*|product owner|vp,? (of )?product|"
                           r"product (specialist|lead)|director,? (of )?product|"
                           r"head of product|product design|technical writer|"
                           r"creative director|"
                           r"\bux\b|\bui\b|designer|design lead|user research)\b", re.I)),
    ("ga", re.compile(r"\b(recruit\w*|talent acquisition|people (ops|operations|"
                      r"relations|experience)|human resources|\bhr\b|fp&a|"
                      r"financial (analyst|planning)|collections|compensation|"
                      r"strategic sourcing|procurement|purchasing|vendor management|"
                      r"warehouse|facilities|finance|accountant|accounting|"
                      r"accounts (payable|receivable)|controller|payroll|compliance|"
                      r"executive assistant|office manager|administrative business "
                      r"partner|contracts? specialist|commercial contracts|"
                      r"\btax\b|pensions?|benefits? (analyst|specialist)|"
                      r"pricing|security governance|shipping|logistics|"
                      r"enrollment agent|clerk|"
                  r"administrative (assistant|manager|specialist|coordinator))\b", re.I)),
]


# Hand assignments from the admin queue, keyed by exact title. Some titles name
# a rank with no function - "Manager", "Executive", "Commercial Development" -
# and the family lives in the JD body, which the board does not fetch. There is
# no pattern to write for those, so the judgment is stored as data. A title that
# does suggest a rule still gets one below, with a selftest case.
_OVERRIDES: dict | None = None


def overrides() -> dict:
    global _OVERRIDES
    if _OVERRIDES is None:
        p = pathlib.Path(__file__).resolve().parent.parent / "data" / "family_overrides.json"
        try:
            raw = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            raw = {}
        _OVERRIDES = {k: v["family"] if isinstance(v, dict) else v
                      for k, v in raw.items()}
    return _OVERRIDES


def family(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "other"
    o = overrides().get(t)
    if o:
        return o
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
    r"Kenya|Cairo|Egypt|"
    # Added after four "Pakistan - Remote" AE roles reached a New York
    # shortlist banded "strong": the list knew a lot of Europe and nothing of
    # South Asia beyond India, so is_us returned None and the non-US branch of
    # the prescreen never fired.
    r"Pakistan|Islamabad|Lahore|Karachi|Bangladesh|Dhaka|Sri Lanka|Colombo|"
    r"Nepal|Kathmandu|Malaysia|Kuala Lumpur|Taiwan|Taipei|Bucharest|Romania|"
    r"Sofia|Bulgaria|Belgrade|Serbia|Zagreb|Croatia|Kyiv|Kiev|Ukraine|"
    r"Riga|Latvia|Vilnius|Lithuania|Tallinn|Estonia|Bratislava|Slovakia|"
    r"Ljubljana|Slovenia|Reykjav[ií]k|Iceland|Luxembourg|Malta|Cyprus|"
    r"Casablanca|Morocco|Tunis|Tunisia|Lagos|Nigeria|Accra|Ghana|"
    r"Abu Dhabi|\bUAE\b|Qatar|Doha|Riyadh|Saudi|Kuwait|Bahrain|Amman|Jordan|"
    r"Lima|Peru|Bogot[aá]|Quito|Ecuador|Montevideo|Uruguay|Asunci[oó]n|"
    r"San Jos[eé], Costa Rica|Costa Rica|Panama|Guatemala|Santo Domingo|"
    r"Dominican Republic|Guadalajara|Monterrey|Mexico City|CDMX)\b", re.I)
# "U.S." is deliberately outside the trailing \b: a word boundary after a
# period needs a word character next, so "U.S. (Remote)" failed its own hint.
US_HINT = re.compile(r"\b(United States|USA?\b|remote.{0,12}\bus\b|"
                     r"\bus\b.{0,12}remote|nationwide)|U\.S\.", re.I)
# Cities that are unambiguously US and routinely appear without a state.
US_CITY = re.compile(r"\b(NYC|New York City|Los Angeles|Chicago|Boston|Seattle|"
                     r"Atlanta|Denver|Austin|Dallas|Houston|Phoenix|Philadelphia|"
                     r"San Francisco|Bay Area|Silicon Valley|Washington,? D\.?C\.?|"
                     r"Minneapolis|Detroit|Pittsburgh|Baltimore|Nashville|"
                     r"Charlotte|Portland, Oregon|Salt Lake City|Kansas City|"
                     r"St\.? Louis|San Diego|Sacramento|Tampa|Orlando|Miami)\b", re.I)
STATE = re.compile(r",\s*(A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|"
                   r"N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY]|DC)\b")
# Spelled-out state names, minus the ones that are also common words or places
# elsewhere: Georgia is a country, Washington needs no help, and "Indiana"
# style names are safe. Built below, once STATE_NAMES exists.


AMBIGUOUS_STATE_NAMES = {"georgia"}      # also a country


# ---------------------------------------------------------------- territory
STATE_CODES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC"}
STATE_NAMES = {
    "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA",
    "colorado":"CO","connecticut":"CT","delaware":"DE","florida":"FL","georgia":"GA",
    "hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA",
    "kansas":"KS","kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
    "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
    "missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV",
    "new hampshire":"NH","new jersey":"NJ","new mexico":"NM","new york":"NY",
    "north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
    "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
    "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT",
    "virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI",
    "wyoming":"WY","district of columbia":"DC"}

# Word-bounded spelled-out state names, for is_us. Georgia is excluded: it is
# also a country, and NON_US is checked first only for cities inside it.
STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in sorted(STATE_NAMES)
                       if n not in AMBIGUOUS_STATE_NAMES) + r")\b", re.I)

REGIONS = {
    "Northeast": {"NY","NJ","CT","MA","PA","RI","VT","NH","ME","MD","DE","DC"},
    "Southeast": {"FL","GA","NC","SC","VA","WV","TN","KY","AL","MS","AR","LA"},
    "Midwest": {"OH","MI","IN","IL","WI","MN","IA","MO","ND","SD","NE","KS"},
    "Southwest": {"TX","OK","NM","AZ"},
    "West": {"CA","OR","WA","NV","ID","UT","CO","MT","WY","AK","HI"},
}
REGION_WORDS = {
    "northeast":"Northeast","north east":"Northeast","new england":"Northeast",
    "mid-atlantic":"Northeast","mid atlantic":"Northeast","tri-state":"Northeast",
    "east coast":"Northeast","eastern":"Northeast",
    "southeast":"Southeast","south east":"Southeast","gulf coast":"Southeast",
    "midwest":"Midwest","mid-west":"Midwest","great lakes":"Midwest",
    "southwest":"Southwest","south west":"Southwest",
    "west coast":"West","western":"West","pacific northwest":"West","pnw":"West",
    "mountain west":"West",
}

REMOTE_RE = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b|\banywhere\b", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
ONSITE_RE = re.compile(r"\bon-?site\b|\bin-?office\b|\bin-?person\b", re.I)


def _region_of(codes: set, text_low: str) -> str | None:
    for word, reg in REGION_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", text_low):
            return reg
    if codes:
        for reg, members in REGIONS.items():
            if codes & members and codes <= members:
                return reg
    return None


def _codes_in(text: str) -> set:
    codes = {c for c in re.findall(r"\b([A-Z]{2})\b", text or "") if c in STATE_CODES}
    low = (text or "").lower()
    for name, code in STATE_NAMES.items():
        if name not in AMBIGUOUS_STATE_NAMES and \
                re.search(r"\b" + re.escape(name) + r"\b", low):
            codes.add(code)
    return codes


# "City, ST" - the shape a location field uses for an office. The city part
# excludes digits so "9-5, TX" style noise cannot match.
_CITY_ST = re.compile(r"([A-Z][A-Za-z .'-]{1,40}?),\s*([A-Z]{2})\b")

# "City, Full State Name", with an optional country after it. This is the same
# fact in a different dress and the office parser used to see only the first
# form, which is why 917 postings naming a real city resolved to no desk at
# all: "San Francisco, California", "New York, New York, United States" and
# "BOSTON, MASSACHUSETTS, UNITED STATES" are every bit as much an address as
# "San Mateo, CA". Measured before this existed: 961 of 4,353 postings had a
# city. A distance filter built on that would have been answering "nothing
# near you" for most of the board.
#
# Georgia stays out via AMBIGUOUS_STATE_NAMES for the same reason it is
# excluded everywhere else here - it is also a country, and "Tbilisi, Georgia"
# is not an office in Atlanta.
_CITY_STATENAME = re.compile(
    r"([A-Z][A-Za-z .'-]{1,40}?),\s*(" +
    "|".join(re.escape(n) for n in sorted(STATE_NAMES, key=len, reverse=True)
             if n not in AMBIGUOUS_STATE_NAMES) +
    r")\b", re.I)

# The two-letter group above matches ANY two capitals, which is not the same
# thing as a US state. "London, UK" was being filed as a US office in a state
# called UK, and so were Cambridge UK, Montreal QB, Noida UP, Pune MH and
# "California, US" - 24 postings at 16 places that do not exist, every one of
# which would have turned up in a search for desks near a US city. A code is a
# state only if it is one.
US_CODES = frozenset(STATE_NAMES.values())

# "Washington, D.C." and "Washington DC" - the seat of a great many govtech
# roles, spelled a way neither pattern above catches because D.C. is not two
# bare letters and is not in STATE_NAMES.
_DC = re.compile(r"\bwashington,?\s*d\.?\s*c\.?", re.I)


def _office_from(loc: str):
    """A city and state out of a location field, or None.

    Tries the strictest reading first. Returns the city in Title Case because
    boards shout: "BOSTON, MASSACHUSETTS, UNITED STATES" is the same desk as
    "Boston, MA" and must not sort or de-duplicate as a different one.
    """
    if not loc:
        return None
    if _DC.search(loc):
        return {"city": "Washington", "state": "DC"}
    m = _CITY_ST.search(loc)
    if m and m.group(2) in US_CODES:
        c = _clean_city(m.group(1).strip())
        return {"city": _title(c), "state": m.group(2)} if c else None
    m = _CITY_STATENAME.search(loc)
    if m:
        c = _clean_city(m.group(1).strip())
        return ({"city": _title(c), "state": STATE_NAMES[m.group(2).lower()]}
                if c else None)
    return None


def _clean_city(raw: str) -> str | None:
    """The city out of a phrase that merely ends in one.

    The pattern's city group starts at the first capital before the comma, so
    a location field that says more than an address hands back the whole
    sentence: "in-office preferred in San Mateo, CA" produced a city called
    "in-office preferred in San Mateo", which then became its own row in a
    city list and its own geocoder lookup. Five of those were on the board.

    A city is the TRAILING run of capitalised words, so this walks back from
    the end and stops at the first word that is not part of a name - the
    lowercase connector in "preferred in San Mateo", the dash in "Production
    AMP - Commerce City", the country in "United States - San Francisco".
    Capped at four words: "Salt Lake City" is three and nothing real is more.

    Returns None rather than a guess when nothing survives.
    """
    words = raw.replace("\u2013", "-").split()
    out = []
    for w in reversed(words):
        if len(out) == 4:
            break
        # a name word starts with a capital; a particle is only kept between
        # two of them, never as the first word of the result
        if w[:1].isupper() and w not in ("-",):
            out.append(w)
        elif out and w.lower() in ("of", "de", "la", "le", "el") :
            out.append(w)
        else:
            break
    if not out:
        return None
    return " ".join(reversed(out)).strip(" -,")


def _title(city: str) -> str:
    """Title Case that leaves already-mixed names alone.

    "BOSTON" -> "Boston", but "McLean" and "DeKalb" are not "Mclean" and
    "Dekalb": a name a person capitalised deliberately is not ours to flatten.
    """
    if city.isupper() or city.islower():
        return " ".join(w[:1].upper() + w[1:].lower() for w in city.split())
    return city


def geography(location_text: str, title: str = "") -> dict:
    """Three separate facts about a posting, each honest about absence.

      territory  what the ROLE COVERS. Territory sales put the geography in
                 the TITLE and the company HQ in the location field:
                 "Enterprise AE - NY, MA, VT, NH" listed as "Denver, CO".
                 Reading only the location field files a Northeast territory
                 under Colorado, so the title wins. stated=False is a real,
                 renderable state: "territory not stated", never invented.
      office     where the JOB SITS, read only from the location field, and
                 only when it names one place. A location listing several
                 states is a coverage or hiring-eligibility list, not a desk.
      work_mode  remote / hybrid / onsite exactly as STATED, else
                 "not stated". A bare city is an office, not proof of onsite:
                 conflating them is how a field role gets filed as desk-bound.
    """
    title = title or ""
    loc = location_text or ""
    blob = f"{title} {loc}"

    # ---- territory: the title's geography, then an explicit multi-state list
    t_codes = _codes_in(title)
    t_region = _region_of(t_codes, title.lower())
    loc_codes = _codes_in(loc)
    if not t_codes and not t_region and len(loc_codes) > 1 \
            and re.search(r"\b(territory|regional)\b", title, re.I):
        t_codes = loc_codes            # "Territory Manager" over "TX, OK"
    if not t_region:
        t_region = _region_of(t_codes, "")
    territory = {"states": sorted(t_codes), "region": t_region,
                 "stated": bool(t_codes or t_region)}

    # ---- office: one nameable place in the location field, or nothing
    office = None
    found = _office_from(loc)
    if found and len(loc_codes) <= 1:
        office = found
    elif len(loc_codes) == 1 and not REMOTE_RE.search(loc):
        # a bare "Texas" or "TX" with no city still pins the seat to a state
        office = {"city": None, "state": next(iter(loc_codes))}

    # ---- work mode: only what the posting says
    if REMOTE_RE.search(blob) and not ONSITE_RE.search(blob):
        mode = "remote"
        office = None                  # "Remote - NY" is eligibility, not a desk
    elif HYBRID_RE.search(blob):
        mode = "hybrid"
    elif ONSITE_RE.search(blob):
        mode = "onsite"
    else:
        mode = "not stated"

    return {"territory": territory, "office": office, "work_mode": mode}


def territory(location_text: str, title: str = "") -> dict:
    """The flat legacy shape some consumers still read; geography() is the
    source of truth. states/region here are TERRITORY facts only."""
    g = geography(location_text, title)
    return {"states": g["territory"]["states"], "region": g["territory"]["region"],
            "work_mode": g["work_mode"]}


# ---------------------------------------------------------------- seniority
# A proxy for the years a JD will ask for, derivable from the title alone. Actual
# years live in the JD body, which is not fetched for every posting.
_LEAD = re.compile(r"\b(chief|\bc[efimoprt]o\b|founder|president|vp\b|"
                   r"vice president|head of|general manager)\b", re.I)
_SENIOR = re.compile(r"\b(senior|sr\.?|principal|staff|lead\b|director|"
                     r"strategic|major|enterprise)\b", re.I)
_JUNIOR = re.compile(r"\b(junior|jr\.?|associate|entry|intern|apprentice|"
                     r"assistant|graduate|trainee|\bsdr\b|\bbdr\b|"
                     r"sales development (rep|representative)|"
                     r"business development (rep|representative))\b", re.I)


def seniority(title: str) -> str:
    t = title or ""
    if _LEAD.search(t):
        return "leadership"
    if _SENIOR.search(t):
        return "senior"
    if _JUNIOR.search(t):
        return "junior"
    return "mid"


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
    if US_HINT.search(blob) or STATE.search(blob) or US_CITY.search(blob):
        return True
    # STATE only matches a comma-prefixed two-letter code, so "Texas Remote
    # Work" and "Arizona Remote Work" read as undeterminable. The spelled-out
    # names were already on file for territory() and simply never consulted
    # here. Georgia is omitted on purpose - it is also a country.
    if STATE_NAME_RE.search(blob):
        return True
    if re.search(r"\bremote\b", blob, re.I):
        return None
    return None
