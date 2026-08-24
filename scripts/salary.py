"""Pull a stated pay range out of job-description prose.

This exists because pay-transparency laws (New York, California, Colorado,
Washington, Illinois and more) require a range in the posting text, so a
meaningful share of descriptions state one even when the ATS exposes no
compensation field. Parsing prose is only worth doing for that reason: we are
reading a number the employer was legally obliged to publish, not guessing one.

THE TRADE-OFF, AND WHY YOU SHOULD NOT "IMPROVE" THE RECALL
----------------------------------------------------------
A missed salary costs a filter hit: the posting stays on the board with no pay
shown, which is the honest state anyway. A WRONG salary is published on a
public job board as a fact about somebody else's company, and a job seeker may
turn down an interview over it. The two errors are not comparable, so this
module is tuned hard toward silence. Every rule below prefers None to a guess,
and the population makes that necessary: this board is almost entirely SALES
roles, whose descriptions are full of large dollar figures that are not pay -
quotas, deal sizes, books of business, ARR targets, funding rounds. A parser
built for recall would report a $2M book of business as a $2M salary.

If you are here because "it missed one", the fix is a new *anchored* form with
a test case, never a loosened anchor. Removing the anchor requirement or the
sanity bounds will produce wrong pay on a live board within one refresh.

WHAT IT REFUSES ON PURPOSE
--------------------------
- OTE / total compensation / on-target earnings. NOT captured, deliberately.
  The storage contract has one comp shape with no field to mark what kind of
  number it is, so an OTE range would land in board.json indistinguishable from
  a base range - one company's $140-200k base would look identical to another's
  $140-200k OTE. When a posting states both, the base is taken and the OTE is
  ignored. Capturing OTE needs a contract change first, not a regex change.
- Bonus, commission, equity, stipend, reimbursement and 401(k) figures.
- Any figure written with an M / MM / B multiplier. "$1.2M" is deal, quota and
  funding language; no employer writes a base salary that way. Refusing the
  suffix outright is cheaper and safer than trying to tell them apart.
- Percentages, because they carry no currency and are usually equity or a 401k
  match ("0.1% - 0.5% equity", "match up to 4%").
- A figure with no currency marker at all. We cannot fill the currency field
  without inventing it, so "salary range: 140,000 - 200,000" is refused.
- Two different ranges in one posting (multi-state tiers). Picking one would
  publish a range that may not apply to the reader's location.
- A bare "$500K+" with no bound word. "up to" and "starting at" state a bound;
  a trailing plus sign in a sales JD is a deal-size idiom.

SANITY BOUNDS (per period, inclusive). Outside these we return None rather than
report an implausible figure:
    year   10,000 - 2,000,000        week    200 - 15,000
    month   1,000 -    60,000        day      50 -  5,000
    hour        5 -       500
Plus: min <= max, and max <= 5 * min when both bounds are stated. A range
wider than 5x is not how pay is posted; it is two unrelated numbers that
happened to sit next to a dash.

PERIODS ARE STORED, NEVER CONVERTED. Normalising hourly to yearly would mean
assuming hours per year, which is inventing a number.

    salary.parse(text) -> {"min": int, "max": int, "currency": "USD",
                           "period": "year", "source": "text",
                           "raw": "$140,000 - $200,000 per year"} | None
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- vocabulary

# Currency. Longer forms first so "CA$" is not eaten as "C" + "A$", and the
# code-plus-symbol spellings ("CAD $120,000") before the bare codes.
_CUR_ALT = (r"USD\s?\$|CAD\s?\$|AUD\s?\$|NZD\s?\$|US\$|CA\$|AU\$|NZ\$|C\$|A\$"
            r"|USD|CAD|AUD|NZD|GBP|EUR|[$£€]")
_CUR = {
    "$": "USD", "US$": "USD", "USD": "USD", "USD$": "USD",
    "£": "GBP", "GBP": "GBP",
    "€": "EUR", "EUR": "EUR",
    "C$": "CAD", "CA$": "CAD", "CAD$": "CAD", "CAD": "CAD",
    "A$": "AUD", "AU$": "AUD", "AUD$": "AUD", "AUD": "AUD",
    "NZ$": "NZD", "NZD": "NZD",
}

# None means "a dollar sign, currency not stated" - compatible with any of the
# dollar codes, contradicted only by a non-dollar one.
_SYMBOL_CODE = {"$": None, "£": "GBP", "€": "EUR"}
_DOLLAR_CODES = {"USD", "CAD", "AUD", "NZD"}

# A number with optional thousands separators and at most two decimal places.
# The grouped form must come first or "140,000" matches as bare "140".
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"


def _money(tag: str) -> str:
    """One money token, with its groups suffixed by `tag` so two can co-exist.

    The multiplier has to be glued to the digits and must not be the first
    letter of the next word, or "$200,000 Kansas" parses as two hundred million.
    """
    return (rf"(?:(?P<c{tag}>{_CUR_ALT})\s{{0,2}})?"
            rf"(?P<n{tag}>{_NUM})"
            rf"(?:(?P<m{tag}>[KkMmBb]{{1,2}})(?![A-Za-z0-9]))?")


_SEP = r"-|‐|‑|‒|–|—|―|to|through|and"

RANGE_RE = re.compile(
    rf"(?P<between>\bbetween\s+)?{_money('1')}"
    rf"\s{{0,4}}(?P<sep>{_SEP})\s{{0,4}}"
    rf"{_money('2')}"
    rf"(?:\s{{0,2}}(?P<c3>USD|CAD|AUD|NZD|GBP|EUR)\b)?",
    re.I,
)

# Bound words that turn a single figure into a real one-sided range. Longest
# first: "starting from" must win over "from".
_MIN_WORDS = ("starting at", "starting from", "beginning at", "as low as",
              "no less than", "a minimum of", "minimum of", "at least", "from")
_MAX_WORDS = ("up to", "as much as", "no more than", "a maximum of",
              "maximum of")
_BOUND_ALT = "|".join(re.escape(w) for w in (_MAX_WORDS + _MIN_WORDS))

SINGLE_RE = re.compile(
    rf"(?:(?P<bound>{_BOUND_ALT})\s+)?{_money('1')}"
    rf"(?:\s{{0,2}}(?P<c3>USD|CAD|AUD|NZD|GBP|EUR)\b)?",
    re.I,
)

# Words that mean "the number next to me is pay". One of these must be the
# nearest label before the figure, otherwise the figure is not pay to us.
ACCEPT_RE = re.compile(
    r"base salary|salary range|salary band|salary|base pay|pay range|pay band|"
    r"pay rate|pay scale|rate of pay|hourly rate|hourly pay|annual rate|"
    r"base compensation|compensation range|compensation|remuneration|"
    r"base range|expected pay|starting pay|starting salary|\bwages?\b|"
    r"pay for this (role|position|job)|range for this (role|position|job)",
    re.I,
)

# Words that mean "the number next to me is NOT base pay". These beat an accept
# word when they are nearer to the figure, or when they contain it - which is
# how "total compensation" avoids being read as "compensation".
REJECT_RE = re.compile(
    r"total (cash |target )?compensation|total target earnings|total rewards|"
    r"\bote\b|on.target (earnings|compensation)|\bquota\b|book of business|"
    r"\bdeals?\b|\bacv\b|\barr\b|\bbookings?\b|\bpipeline\b|\brevenues?\b|"
    r"\bfunding\b|\braised\b|series [a-e]\b|valuation|\bbudget\b|"
    r"\bcontracts?\b|\bstipend\b|reimburs|401\s?\(?k\)?|\bbonus(es)?\b|"
    r"\bcommissions?\b|\bequity\b|\bgrants?\b|\bsav(e|es|ed|ing|ings)\b|"
    r"\bsells?\b|\bsold\b|\bportfolio\b|per diem|\bfees?\b|\binvest",
    re.I,
)

# Checked in the few characters AFTER the figure, for the sales idioms that put
# their noun on the right: "$500K in new business", "$50k-$200k in savings".
# Deliberately excludes equity/bonus/commission, because "base range $140,000 -
# $200,000 + equity" is a real and correct base range.
TAIL_REJECT_RE = re.compile(
    r"\bsav(e|es|ed|ing|ings)\b|\bin (new )?(business|revenue|arr|acv|bookings)\b|"
    r"book of business|\bdeals?\b|\bquotas?\b|\bcontracts?\b|\bpipeline\b|"
    r"\bbudgets?\b|\brevenues?\b|\bvaluation\b|\bfunding\b|\bstipend\b|"
    # Swiftly writes "US Salary Range: $74,000- $124,000 USD OTE". The label
    # before the figure says salary; the word that makes it an OTE sits after
    # it. Found in live data, so the OTE terms are checked on both sides.
    r"\bote\b|on.target (earnings|compensation)|total compensation",
    re.I,
)

PERIOD_RE = re.compile(
    r"/\s?(?P<slash>hrs?|hours?|yrs?|years?|mos?|months?|wks?|weeks?|days?)\b|"
    r"\bper\s+(?P<per>hour|year|annum|month|week|day)\b|"
    r"\b(?P<adv>hourly|yearly|annually|annualized|annualised|annual|monthly|"
    r"weekly|daily)\b|"
    r"\ban?\s+(?P<art>hour|year|month|week|day)\b",
    re.I,
)

_GAP_RE = re.compile(r"[\s/,;:.\-–—()\[\]]*")

_PERIOD_OF = {
    "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour",
    "hourly": "hour",
    "yr": "year", "yrs": "year", "year": "year", "years": "year",
    "annum": "year", "yearly": "year", "annually": "year", "annual": "year",
    "annualized": "year", "annualised": "year",
    "mo": "month", "mos": "month", "month": "month", "months": "month",
    "monthly": "month",
    "wk": "week", "wks": "week", "week": "week", "weeks": "week",
    "weekly": "week",
    "day": "day", "days": "day", "daily": "day",
}

BOUNDS = {
    "year": (10_000, 2_000_000),
    "month": (1_000, 60_000),
    "week": (200, 15_000),
    "day": (50, 5_000),
    "hour": (5, 500),
}

# With no period word anywhere, "annual" is the only reading a US posting can
# expect a reader to take - but only once the figure is big enough that nothing
# else fits. Below this we bail: at $8,000 the number could as easily be a
# month, a semester, or a signing bonus, and choosing is inventing.
NO_PERIOD_FLOOR = 20_000

# How far back the nearest-label search looks. Long enough to reach a section
# heading two lines up ("Compensation\n\n$140,000 - $200,000"), short enough
# that a label from an unrelated paragraph does not govern.
LOOKBACK = 120
TAIL = 35
LEAD_PERIOD = 40   # a period word before the figure ("annual base salary of")
TRAIL_PERIOD = 15  # ...and how close after it must sit to count as attached


# ------------------------------------------------------------------- helpers

def _amount(num: str, mult: str | None):
    """Digits -> a number in whole currency units, or None if we refuse it."""
    if mult:
        # m/mm/b never denominate pay. See the module docstring.
        if mult.lower() != "k":
            return None
        value = float(num.replace(",", "")) * 1000
    else:
        value = float(num.replace(",", ""))
    # The contract wants integers in whole units, and everything but an hourly
    # rate with cents is one. We do NOT round $67.50 to $68: a published pay
    # figure that is fifty cents wrong is still wrong, and rounding is exactly
    # the kind of invented number this project refuses.
    return int(value) if value == int(value) else value


def _currency(*raw: str | None) -> str | None:
    """One currency, or None for 'none stated' and for a mixed-currency range.

    A spelled-out code beats a bare symbol, because the symbol is the weaker
    evidence: Swiftly writes "$152,000 - $190,000 CAD", and reading that as a
    dollar/CAD conflict threw away a real range. A symbol only contradicts a
    code when it cannot mean it - "$" alongside GBP.
    """
    codes, symbols = set(), set()
    for v in raw:
        if not v:
            continue
        # spaces stripped so "CAD $" and "CAD$" are the same marker
        key = v.upper().replace(" ", "")
        if key in _SYMBOL_CODE:
            symbols.add(key)
        elif key in _CUR:
            codes.add(_CUR[key])
    if len(codes) > 1:
        return None
    if codes:
        code = codes.pop()
        for s in symbols:
            fixed = _SYMBOL_CODE[s]
            if fixed is None and code not in _DOLLAR_CODES:
                return None
            if fixed is not None and fixed != code:
                return None
        return code
    if len(symbols) != 1:
        return None
    # A bare "$" is read as USD. This is a US state & local govtech board, and
    # a posting that means Canadian dollars says CAD. It is an assumption about
    # notation rather than an invented number, but it is the one assumption in
    # this module, so it is written down here.
    return _SYMBOL_CODE[symbols.pop()] or "USD"


def _label(text: str, start: int) -> str:
    """'accept' | 'reject' | 'none' for the nearest pay label before `start`.

    Nearest wins, so "base salary $140k-$200k plus an annual bonus of $10,000 -
    $20,000" reads the first range as pay and the second as a bonus. A reject
    that OVERLAPS the accept also wins, which is how "total compensation" is
    stopped from matching as "compensation" and shipping an OTE as a base.
    """
    window = text[max(0, start - LOOKBACK):start]
    acc = None
    for m in ACCEPT_RE.finditer(window):
        acc = m
    rej = None
    for m in REJECT_RE.finditer(window):
        rej = m
    if rej is None:
        return "accept" if acc else "none"
    if acc is None:
        return "reject"
    if rej.end() >= acc.end() or (rej.start() < acc.end() and rej.end() > acc.start()):
        return "reject"
    return "accept"


def _period(text: str, start: int, end: int) -> tuple[str | None, bool, int]:
    """(period, attached_after, end offset to quote up to).

    `attached_after` is what unlocks an unlabelled range: "$67.50 - $85.00/hour"
    is pay on its own, while a period word sitting BEFORE the figure only tells
    us the period of something we already decided was pay.
    """
    tail = text[end:end + TRAIL_PERIOD + 12]
    m = PERIOD_RE.search(tail)
    # "attached" has to mean attached: only whitespace and punctuation may sit
    # between the figure and the period word. Allowing a couple of words lets
    # the next clause donate one - "$140,000 - $200,000 plus an annual bonus"
    # read as an annual range, and quoted "plus an annual" back as evidence.
    if m and m.start() <= TRAIL_PERIOD and _GAP_RE.fullmatch(tail[:m.start()]):
        word = next(g for g in m.groupdict().values() if g)
        return _PERIOD_OF.get(word.lower()), True, end + m.end()

    # Look back for "annual base salary of ...". Cut at the previous sentence so
    # a stray "a year" in the sentence before does not set the period here.
    lead = text[max(0, start - LEAD_PERIOD):start]
    cut = max(lead.rfind("."), lead.rfind(";"), lead.rfind("\n"))
    lead = lead[cut + 1:]
    found = None
    for m in PERIOD_RE.finditer(lead):
        found = m
    if found:
        word = next(g for g in found.groupdict().values() if g)
        return _PERIOD_OF.get(word.lower()), False, end
    return None, False, end


def _plausible(lo, hi, period: str) -> bool:
    low, high = BOUNDS[period]
    for v in (lo, hi):
        if v is not None and not (low <= v <= high):
            return False
    if lo is not None and hi is not None:
        if lo > hi:
            return False
        # a >5x spread is two unrelated numbers that happen to share a dash
        if hi > 5 * lo:
            return False
    return True


def _quote(text: str, start: int, end: int) -> str:
    """The exact substring the figures came from, so a person can check it.

    Whitespace runs are collapsed - a range can straddle a line break, and a
    quote with a newline in it is unreadable wherever it is eventually shown.
    Nothing else is touched, except that a bracket the quote opened gets closed:
    "$140,000 - $200,000 (annually" is a quote a reader would distrust.
    """
    if (text[end:end + 1] == ")"
            and text.count("(", start, end) > text.count(")", start, end)):
        end += 1
    return re.sub(r"\s+", " ", text[start:end]).strip()


def _finish(text: str, lo, hi, cur, m_start: int, m_end: int, quote_from: int):
    """Apply the label, tail, period and plausibility rules to one candidate."""
    label = _label(text, m_start)
    if label == "reject":
        return None
    if TAIL_REJECT_RE.search(text[m_end:m_end + TAIL]):
        return None
    period, attached, quote_to = _period(text, m_start, m_end)
    if label != "accept" and not attached:
        return None
    if period is None:
        stated = [v for v in (lo, hi) if v is not None]
        if min(stated) < NO_PERIOD_FLOOR:
            return None
        period = "year"
    if not _plausible(lo, hi, period):
        return None
    return {"min": lo, "max": hi, "currency": cur, "period": period,
            "source": "text", "raw": _quote(text, quote_from, quote_to)}


def _candidates(text: str) -> list[dict]:
    out, spans = [], []
    for m in RANGE_RE.finditer(text):
        spans.append((m.start(), m.end()))
        g = m.groupdict()
        # "and" only joins a range after the word "between". Without that guard
        # "a $5,000 bonus and $140,000 base" reads as a range.
        if g["sep"].lower() == "and" and not g["between"]:
            continue
        lo = _amount(g["n1"], g["m1"])
        hi = _amount(g["n2"], g["m2"])
        cur = _currency(g["c1"], g["c2"], g["c3"])
        if lo is None or hi is None or cur is None:
            continue
        start = m.start("c1") if g["c1"] else m.start("n1")
        # quote from "between" when it is there, so raw stays a faithful quote
        quote_from = m.start("between") if g["between"] else start
        cand = _finish(text, lo, hi, cur, start, m.end(), quote_from)
        if cand:
            out.append(cand)

    for m in SINGLE_RE.finditer(text):
        # a figure already read as half of a range is not also a single figure,
        # and a range we REFUSED stays refused rather than coming back as two.
        if any(s <= m.start("n1") < e for s, e in spans):
            continue
        g = m.groupdict()
        value = _amount(g["n1"], g["m1"])
        cur = _currency(g["c1"], g["c3"])
        if value is None or cur is None:
            continue
        bound = (g["bound"] or "").lower()
        if bound in _MAX_WORDS:
            lo, hi = None, value
        elif bound in _MIN_WORDS:
            lo, hi = value, None
        else:
            lo = hi = value
        start = m.start("c1") if g["c1"] else m.start("n1")
        # A lone figure gets no unlabelled path: "close $500K annually" has the
        # shape of pay and is a quota. It must be labelled as pay to count.
        if _label(text, start) != "accept":
            continue
        quote_from = m.start("bound") if g["bound"] else start
        cand = _finish(text, lo, hi, cur, start, m.end(), quote_from)
        if cand:
            out.append(cand)
    return out


def _key(c: dict) -> tuple:
    return (c["min"], c["max"], c["currency"], c["period"])


def parse(text: str) -> dict | None:
    """Return the comp shape for a pay range stated in `text`, or None.

    None means "this posting does not state a pay range we can stand behind",
    which is not the same as "this posting pays nothing" and must never be
    rendered as a zero or filtered as a low number.
    """
    if not text or not isinstance(text, str):
        return None
    # 1:1 replacement, so every offset below still indexes the original text.
    hay = text.replace(" ", " ")
    cands = _candidates(hay)
    if not cands:
        return None

    two_sided = [c for c in cands if c["min"] is not None and c["max"] is not None]
    if two_sided:
        # Two different ranges is a multi-state posting. Reporting either one
        # publishes a range that may not apply where the reader lives.
        if len({_key(c) for c in two_sided}) > 1:
            return None
        win = two_sided[0]
        # A one-sided figure elsewhere is normally the same range restated
        # ("starting at $140,000"). If it contradicts, the posting says two
        # things and we say nothing.
        for c in cands:
            if c["min"] is not None and c["max"] is not None:
                continue
            if c["currency"] != win["currency"] or c["period"] != win["period"]:
                return None
            v = c["min"] if c["min"] is not None else c["max"]
            if not (win["min"] <= v <= win["max"]):
                return None
        return win

    if len({_key(c) for c in cands}) > 1:
        return None
    return cands[0]
