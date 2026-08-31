#!/usr/bin/env python3
"""What govtech sales roles actually state they pay, from the postings themselves.

    python3 scripts/pay_report.py
    python3 scripts/pay_report.py --json

Nobody publishes this. Comp surveys for state-and-local software sales either
do not exist or cost money and sample nobody you have heard of. This board
reads every job board it holds an address for, every night, and
pay-transparency law obliges a growing share of employers to publish a range,
so the numbers are sitting in the postings already.

    (That sentence used to say "reads 2,113 companies' own job boards every
    night". It was len(companies) wearing a "boards read" label - the same
    overclaim the site's own coverage card made twice - and stale besides:
    there are 2,058 records, 918 of them carry no board address at all, and
    the last run actually read 337 boards. `python3 scripts/coverage.py` and
    board.json's `boards_read` are the live figures.)

THE HEADLINE CAVEAT COMES FIRST, NOT IN A FOOTNOTE. Only a minority of
quota-carrying postings state a figure at all. Every number below describes
THOSE, and a reader who takes it as the market rate is being misled by an
omission rather than by an error. The report says the ratio before it says
anything else, and repeats it in the JSON.

WHAT IT REFUSES TO DO, and each refusal is a number it could have printed:

  - No period conversion. An hourly rate multiplied by 2,080 invents a
    full-time year nobody offered. Hourly and monthly postings are counted and
    excluded, and the count is printed.
  - No currency mixing. Two currencies in one median is not a number.
  - No band under MIN_N. A median of four postings is two employers' opinions
    wearing a statistic's authority. Bands below the floor are counted and
    named as "not enough data", which is a real answer here and stays the
    answer until it is not.
  - Every cut says WHAT SHARE OF THE SAMPLE IT COVERS, because a band passing
    the floor is not the same as a cut being answerable. Office state is the
    case that forced it: 75 of the 147 postings carry a parsed state, spread
    across 21 of them, so only CA and NY clear MIN_N at 11 and 9. The two rows
    are true and the table is a map with two pins on it, which a reader will
    take for a national picture unless the coverage line is right there.

    (Twice while writing this the prose and the code disagreed. The docstring
    first said "NO GEOGRAPHY" while the function printed a geography table, and
    then said 20 postings carried a state when the number is 75 - the coverage
    line I had just added is what caught the second one. A comment that
    disagrees with its own function is the same defect as a page that disagrees
    with its own data, and it is just as invisible from the inside.)
  - No averages. One $600k enterprise req should not move a line somebody is
    about to negotiate against.

AND IT SEPARATES THE TWO KINDS OF FIGURE. A range an employer published in
structured form on their own ATS is a stronger claim than one salary.py pulled
out of prose. Both are reported, and the split is printed, because a reader
deciding whether to trust this deserves to know which they are looking at.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Below this a median is not a market rate. Eight is deliberately low for a
# board this size and still excludes most geography cuts, which is the point:
# the floor is what stops the report inventing precision it does not have.
MIN_N = 8


def load(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _rank(v: list, p: float) -> int:
    """Nearest-rank percentile: the smallest value at or above p of the set.

    `ceil(p*n) - 1` on a 0-indexed list. Written out because the obvious
    `v[n // 4]` is an UNDECREMENTED RANK used as an index, and it is right for
    three n in four - which is why it survived. At n = 28 it read v[7] and
    v[21] where the quartiles are v[6] and v[20], and the remote band printed
    a $145k upper quartile against a true $130k. A $15k error on the figure
    somebody quotes back across a table.
    """
    n = len(v)
    i = -((-int(round(p * n * 1000))) // 1000)       # ceil(p*n), no float slop
    return v[min(max(i - 1, 0), n - 1)]


def _band(vals: list[int]) -> dict:
    """Median and the middle half. A single number hides the spread a reader
    is actually negotiating inside."""
    v = sorted(vals)
    return {"n": len(v),
            "median": int(statistics.median(v)),
            "p25": int(_rank(v, 0.25)),
            "p75": int(_rank(v, 0.75))}


def report() -> dict:
    board = load(DATA / "board.json", {}) or {}
    ps = board.get("postings") or []
    quota = [p for p in ps if p.get("quota_carrying")]

    stated, other_period, other_ccy = [], 0, 0
    for p in quota:
        c = p.get("comp") or {}
        v = c.get("min")
        # ANY NUMBER, so the exclusion counters can see what they exclude.
        # This tested `isinstance(v, int)` FIRST, and an hourly rate is
        # fractional - so two of the three hourly quota postings were floats,
        # were dropped before the period test ever ran, and the report said
        # "1 states an hourly or monthly rate" where three do. A counter
        # downstream of a filter that eats its own subject counts survivors.
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if c.get("period") != "year":
            other_period += 1
            continue
        if c.get("currency") != "USD":
            other_ccy += 1
            continue
        stated.append(p)

    out = {
        "generated": board.get("generated"),
        "quota_postings": len(quota),
        "stated_annual_usd": len(stated),
        "excluded_other_period": other_period,
        "excluded_other_currency": other_ccy,
        # The ratio that governs how the whole report should be read.
        "share_stating_pay": (round(len(stated) / len(quota), 3)
                              if quota else None),
        "by_source": dict(collections.Counter(
            (p["comp"].get("source") or "?") for p in stated)),
        "bands": {},
        "not_enough_data": {},
    }
    if not stated:
        return out

    def cut(name, key):
        groups: dict = collections.defaultdict(list)
        for p in stated:
            k = key(p)
            if k:
                groups[str(k)].append(int(p["comp"]["min"]))
        good, thin = {}, {}
        for k, vals in groups.items():
            (good if len(vals) >= MIN_N else thin)[k] = (
                _band(vals) if len(vals) >= MIN_N else len(vals))
        out["bands"][name] = dict(sorted(good.items(),
                                         key=lambda kv: -kv[1]["n"]))
        # NAMED, NOT DROPPED. A cut that vanishes silently reads as a cut that
        # does not exist; one reported as too thin tells a reader what the
        # board cannot yet answer, which is the more useful fact.
        out["not_enough_data"][name] = {
            "bands": len(thin), "postings": sum(thin.values())}
        # WHAT SHARE OF THE SAMPLE THIS CUT CAN EVEN SPEAK FOR. A band over the
        # floor still says nothing about the postings the cut cannot classify:
        # office_state reports CA and NY honestly and covers 14% of the sample,
        # and a reader who does not see that reads a national picture off two
        # states.
        out.setdefault("coverage", {})[name] = {
            "classified": sum(len(v) for v in groups.values()),
            "of": len(stated)}

    cut("seniority", lambda p: p.get("seniority"))
    cut("sector", lambda p: p.get("sector"))
    # "not stated" IS ROLES.PY'S ABSENCE SENTINEL, not a work mode. It is a
    # truthy string, so `if k:` filed all 118 of them as classified and the
    # report printed "146 of 146 postings carry a work mode" - clearing the
    # 90% threshold that would otherwise have attached a coverage caveat.
    # CLAUDE.md: work_mode is `not stated` on 79% of postings because most
    # boards never say it.
    cut("work_mode",
        lambda p: (p.get("work_mode")
                   if p.get("work_mode") != "not stated" else None))
    cut("office_state", lambda p: (p.get("office") or {}).get("state"))
    return out


def k(n) -> str:
    return f"${n / 1000:.0f}k"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = report()
    if a.json:
        print(json.dumps(r, indent=1))
        return 0

    if not r["stated_annual_usd"]:
        print("no quota-carrying posting on this board states an annual "
              "US-dollar figure, so there is nothing to report.")
        return 0

    share = r["share_stating_pay"]
    print(f"What govtech sales roles say they pay  ({r['generated']})\n")
    print(f"  {r['stated_annual_usd']} of {r['quota_postings']} quota-carrying "
          f"postings state an annual US-dollar range: {share:.0%}.")
    print(f"  EVERYTHING BELOW DESCRIBES THOSE {r['stated_annual_usd']}. The "
          f"other {r['quota_postings'] - r['stated_annual_usd']} say nothing "
          f"about pay,\n  and nothing here is an estimate of what they offer.")
    src = r["by_source"]
    if src:
        pub = src.get("ats", 0)
        print(f"\n  {pub} published a structured range on their own job board; "
              f"{src.get('text', 0)} were read\n  out of the posting's prose. "
              f"The first is a stronger claim than the second.")
    ex = []
    if r["excluded_other_period"]:
        ex.append(f"{r['excluded_other_period']} state an hourly or monthly "
                  f"rate (never multiplied into a year)")
    if r["excluded_other_currency"]:
        ex.append(f"{r['excluded_other_currency']} are in another currency")
    if ex:
        print(f"  Excluded: {'; '.join(ex)}.")

    for name in ("seniority", "sector", "work_mode", "office_state"):
        bands = r["bands"].get(name) or {}
        thin = r["not_enough_data"].get(name) or {}
        print(f"\n  by {name.replace('_', ' ')}")
        if not bands:
            print(f"    not enough data in any band. "
                  f"{thin.get('postings', 0)} posting(s) across "
                  f"{thin.get('bands', 0)} band(s), all under {MIN_N}.")
            if name == "office_state":
                print("    This one is worth saying out loud: the board cannot "
                      "tell you what pay\n    looks like by geography, because "
                      "most seller postings that state a\n    figure never say "
                      "where the desk is.")
            continue
        cvg = (r.get("coverage") or {}).get(name) or {}
        if cvg.get("of"):
            pct = cvg["classified"] / cvg["of"]
            note = ("" if pct >= 0.9 else
                    f"   <- speaks for {pct:.0%} of the sample"
                    if pct >= 0.5 else
                    f"   <- speaks for only {pct:.0%} of the sample")
            label = name.replace("_", " ")
            article = "an" if label[0] in "aeiou" else "a"
            print(f"    ({cvg['classified']} of {cvg['of']} postings carry "
                  f"{article} {label}{note})")
        for band, b in bands.items():
            print(f"    {band[:24]:26} n={b['n']:3}   "
                  f"{k(b['p25'])} .. {k(b['median'])} .. {k(b['p75'])}")
        if thin.get("bands"):
            print(f"    ({thin['bands']} more band(s), {thin['postings']} "
                  f"posting(s), under the {MIN_N} floor and not reported)")
    print(f"\n  Figures are the stated FLOOR of each range: 25th percentile, "
          f"median, 75th.\n  Re-run this rather than quoting it; it moves every "
          f"night.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
