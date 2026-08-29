#!/usr/bin/env python3
"""The week in one post, from the board's own numbers.

    python3 scripts/report.py --week
    python3 scripts/report.py --week --since 2026-08-22

A board with no audience needs something to say out loud every week, and the
only honest source for that is what actually changed. This writes it in a fixed
format so it can be posted without editing, and so two weeks running are
comparable rather than two different essays.

WHAT IT WILL NOT SAY. No growth framing on a number that fell. No total dressed
as an increase. When the week was quiet it says the week was quiet, because a
weekly post that always sounds like a good week teaches people to stop reading
it, and this project's whole discipline is that an absence gets reported as an
absence.

The numbers come from data/history/*.json (posting ids per day) and
data/board.json. Nothing here is stored: run it again and it recomputes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load(p: pathlib.Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def snapshots() -> list[tuple[str, set]]:
    """(date, posting ids) oldest first, for every day we hold."""
    out = []
    for f in sorted((DATA / "history").glob("*.json")):
        d = _load(f)
        if d and isinstance(d.get("ids"), list):
            out.append((d.get("date") or f.stem, set(d["ids"])))
    return out


def week(since: str | None) -> dict:
    board = _load(DATA / "board.json") or {}
    snaps = snapshots()
    if len(snaps) < 2:
        return {"error": "need at least two daily snapshots to compare a week"}

    today_date, today_ids = snaps[-1]
    if since:
        base = next(((d, s) for d, s in snaps if d >= since), snaps[0])
    else:
        # a week back if we have it, else the oldest we hold - and the report
        # says which, because "this week" over four days is a different claim
        want = (dt.date.fromisoformat(today_date) - dt.timedelta(days=7)).isoformat()
        base = next(((d, s) for d, s in snaps if d >= want), snaps[0])
    base_date, base_ids = base

    # A COMPARISON ACROSS AN ID CHANGE IS NOT A WEEK, IT IS AN ARTEFACT. On
    # 2026-08-23 posting ids gained a url+location hash, so the 08-22 and 08-23
    # snapshots share not one id out of three thousand. Comparing across that
    # boundary reports every posting as new and every earlier one as gone: the
    # first run of this said "580 new quota-carrying roles" and "3,332 came
    # off", which is a growth story assembled out of a schema change.
    #
    # Every genuine adjacent pair overlaps by thousands, so a near-empty
    # intersection means the two snapshots are not comparable. Walk forward to
    # the oldest one that IS, and say the span shrank and why.
    note = None
    if base_ids and today_ids:
        overlap = len(base_ids & today_ids) / min(len(base_ids), len(today_ids))
        if overlap < 0.2:
            for d, ids in snaps:
                if d <= base_date or not ids:
                    continue
                if len(ids & today_ids) / min(len(ids), len(today_ids)) >= 0.2:
                    note = (f"history before {d} uses a different posting id and "
                            f"cannot be compared with today, so this covers "
                            f"{d} onward instead")
                    base_date, base_ids = d, ids
                    break
            else:
                return {"error": "no snapshot in the history is comparable with "
                                 "today - the posting id scheme changed and "
                                 "nothing since is old enough to compare"}

    posts = {p["id"]: p for p in board.get("postings", [])}
    new_ids = today_ids - base_ids
    gone_ids = base_ids - today_ids
    new = [posts[i] for i in new_ids if i in posts]
    quota_new = [p for p in new if p.get("quota_carrying")]

    orgs = {o["id"]: o for o in board.get("organizations", [])}
    debut = sorted({p.get("company") for p in quota_new
                    if p.get("company_id") and not any(
                        q.get("company_id") == p.get("company_id")
                        for q in posts.values()
                        if q["id"] in base_ids)} - {None})

    by_sector: dict = {}
    for p in quota_new:
        by_sector[p.get("sector") or "unfiled"] = by_sector.get(p.get("sector") or "unfiled", 0) + 1
    by_state: dict = {}
    for p in quota_new:
        st = (p.get("office") or {}).get("state")
        if st:
            by_state[st] = by_state.get(st, 0) + 1

    return {
        "note": note,
        "from": base_date, "to": today_date,
        "days": (dt.date.fromisoformat(today_date) - dt.date.fromisoformat(base_date)).days,
        "asked_for_a_week": since is None,
        "new": len(new), "new_quota": len(quota_new), "gone": len(gone_ids),
        "debut_companies": debut,
        "sectors": sorted(by_sector.items(), key=lambda kv: -kv[1])[:5],
        "states": sorted(by_state.items(), key=lambda kv: -kv[1])[:5],
        "total_postings": len(board.get("postings", [])),
        "total_hiring": sum(1 for o in orgs.values() if o.get("open_roles")),
        "total_companies": len(orgs),
    }


def render(r: dict, brand: dict) -> str:
    if r.get("error"):
        return r["error"]
    site = brand["site"].rstrip("/")
    L = []
    span = f"{r['from']} to {r['to']}"
    if r["days"] != 7 and r["asked_for_a_week"]:
        span += f" ({r['days']} days, which is all the history we hold)"
    L.append(f"{brand['name']}: {span}")
    if r.get("note"):
        L.append(f"({r['note']})")
    L.append("")

    if not r["new_quota"]:
        # The honest version of a quiet week. A weekly post that always sounds
        # like a good week is one people stop reading.
        L.append("No new quota-carrying roles appeared this week. The board is "
                 f"steady at {r['total_hiring']} companies hiring out of "
                 f"{r['total_companies']:,} tracked.")
    else:
        L.append(f"{r['new_quota']} new quota-carrying role"
                 f"{'s' if r['new_quota'] != 1 else ''} appeared, out of "
                 f"{r['new']} new postings overall.")
        if r["sectors"]:
            L.append("Where: " + ", ".join(f"{k} {v}" for k, v in r["sectors"]) + ".")
        if r["states"]:
            L.append("Desks in: " + ", ".join(f"{k} {v}" for k, v in r["states"]) + ".")
        if r["debut_companies"]:
            names = r["debut_companies"][:5]
            more = (f" and {len(r['debut_companies']) - 5} more"
                    if len(r["debut_companies"]) > 5 else "")
            L.append("First seller req we have ever recorded at: "
                     + ", ".join(names) + more + ".")
    L.append("")
    L.append(f"{r['gone']} posting{'s' if r['gone'] != 1 else ''} came off the "
             f"board. A posting leaving is not always a role filled: a board "
             f"that stops answering looks the same from here, which is why "
             f"that number is reported rather than read as hiring slowing.")
    L.append("")
    L.append(f"Standing: {r['total_postings']:,} postings at "
             f"{r['total_hiring']} companies, out of {r['total_companies']:,} "
             f"govtech companies tracked.")
    L.append(site + "/")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", action="store_true", help="the weekly post")
    ap.add_argument("--since", help="compare against this date instead of a week back")
    ap.add_argument("--json", action="store_true", help="the numbers, unformatted")
    a = ap.parse_args()
    if not (a.week or a.json):
        ap.print_help()
        return 1
    brand = _load(DATA / "brand.json") or {"name": "SLED JOBS", "site": ""}
    r = week(a.since)
    print(json.dumps(r, indent=1) if a.json else render(r, brand))
    return 0


if __name__ == "__main__":
    sys.exit(main())
