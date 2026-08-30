#!/usr/bin/env python3
"""Every queue count, once a day, appended and never rewritten.

    python3 scripts/queue_stats.py            # record today
    python3 scripts/queue_stats.py --show     # what has moved
    python3 scripts/queue_stats.py --dry-run

There are 2,960 open rows across sixteen queues and no record anywhere of what
that number was last week. So there is no way to answer the only question that
matters about ruling work - is it going down - and no way to tell a queue that
somebody is steadily clearing from one that is quietly refilling faster than it
empties.

Both of those look identical from a single day's count, which is the same shape
as every other failure this repo is built around: the absence of a second
reading is indistinguishable from no change at all.

WHY IT MATTERS BEYOND CURIOSITY. CLAUDE.md picked personal bests - the user
against their own last 30 days - as one of the three mechanics the admin should
grow into, and rejected every leaderboard. A personal best needs a history to
be personal about, and history cannot be backfilled: a count nobody wrote down
in August is gone. This is the cheapest possible way to stop losing it, and it
had to start before the ruling did, not after.

APPEND-ONLY, ONE LINE PER DAY. data/queue_history.jsonl is an audit trail in
the same sense data/hiring_history is, so nothing here rewrites a past line.
Running twice in a day REPLACES today's line only - the counts are a snapshot
of a moment and the later one is the truer one - and a second run is reported
rather than silently swallowed.

It counts. It never rules, never dismisses, never writes a company.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import admin                                                # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "queue_history.jsonl"


def counts() -> dict:
    """Every queue, measured against the live files. Read-only throughout."""
    companies = admin.read_companies()
    board = json.loads((DATA / "board.json").read_text())
    out = {}
    for key, fn in admin.QUEUES.items():
        try:
            out[key] = len(fn(companies, board))
        except Exception as e:                     # noqa: BLE001
            # A QUEUE THAT RAISES IS RECORDED AS RAISING, not as zero. A zero
            # here would read as "somebody cleared it", which is the flattering
            # version of the same failure a false "no jobs found" is.
            out[key] = None
            print(f"  {key}: raised {type(e).__name__}: {e}", file=sys.stderr)
    return out


def history() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def record(today: str, now: dict) -> str:
    rows = history()
    replaced = any(r.get("on") == today for r in rows)
    rows = [r for r in rows if r.get("on") != today]
    rows.append({"on": today, "queues": now,
                 "total": sum(v for v in now.values() if isinstance(v, int))})
    rows.sort(key=lambda r: r.get("on") or "")
    LOG.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return "replaced today's line" if replaced else "appended"


def show() -> int:
    rows = history()
    if not rows:
        print("no history yet - run this once with no arguments")
        return 0
    now = rows[-1]
    print(f"{now['total']:,} open rows across {len(now['queues'])} queues "
          f"on {now['on']}")
    if len(rows) < 2:
        print("\nOne reading. A single count cannot say whether anything is "
              "moving,\nwhich is the whole reason this file exists - come back "
              "tomorrow.")
        return 0

    # The oldest reading we hold that is at most 30 days back, so "since" names
    # a real date rather than implying a month we do not have.
    horizon = (dt.date.fromisoformat(now["on"]) - dt.timedelta(days=30)).isoformat()
    base = next((r for r in rows if r["on"] >= horizon), rows[0])
    if base["on"] == now["on"]:
        base = rows[-2]
    print(f"against {base['on']}:\n")
    moved = []
    for k in sorted(now["queues"]):
        a, b = base["queues"].get(k), now["queues"].get(k)
        if not isinstance(a, int) or not isinstance(b, int):
            continue
        moved.append((b - a, k, a, b))
    for d, k, a, b in sorted(moved):
        if d == 0:
            continue
        # A QUEUE GOING UP IS NOT A FAILURE AND IS NOT DRESSED AS ONE. Blocked
        # boards rose from 150 to 210 the day 60 refusals stopped being filed
        # as absences - the number got worse and the data got better.
        arrow = "↓" if d < 0 else "↑"
        print(f"  {arrow} {k:16} {a:>5} → {b:<5} ({d:+})")
    if not any(d for d, *_ in moved):
        print("  nothing moved.")
    tot = now["total"] - base["total"]
    print(f"\n  total {base['total']:,} → {now['total']:,} ({tot:+})")
    if tot > 0:
        print("  The pile grew. That is not automatically bad: a sweep that "
              "finds\n  real work adds rows, and so does a refusal correctly "
              "re-filed as a\n  refusal rather than as an absence.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="what has moved")
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    a = ap.parse_args()
    if a.show:
        return show()
    now = counts()
    today = dt.date.today().isoformat()
    total = sum(v for v in now.values() if isinstance(v, int))
    for k in sorted(now, key=lambda k: -(now[k] or 0)):
        print(f"  {k:16} {now[k] if now[k] is not None else 'raised'}")
    print(f"  {'TOTAL':16} {total:,}")
    if a.dry_run:
        print("\ndry run - nothing written")
        return 0
    what = record(today, now)
    print(f"\n{what} in data/queue_history.jsonl ({len(history())} day(s) on file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
