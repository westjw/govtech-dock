#!/usr/bin/env python3
"""The Claude inbox: what the page belt could not settle on its own.

    python3 scripts/inbox.py                  # everything still open
    python3 scripts/inbox.py --all            # open and settled
    python3 scripts/inbox.py --resolve <id> --status fixed --note "rule added"

One gitignored file, data/claude_inbox.jsonl, one JSON object per line. The
owner writes to it from any checklist item's "explain" box; other parts of
the system may add rows with their own `source` (the publisher when a push
is refused, for instance). Once a day, at the end of the day, a Claude
session reads the open rows and WORKSHOPS them with him - so the system
learns what it got wrong (owner, 2026-09-28) - and marks each one settled
here with what was done about it.

PRIVATE BY CONSTRUCTION. The repository is public; this file is ignored by
git and never goes through the journal, which is tracked.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INBOX = ROOT / "data" / "claude_inbox.jsonl"
STATUSES = ("open", "workshopped", "fixed", "wontfix")


def load() -> list[dict]:
    if not INBOX.exists():
        return []
    rows = []
    for line in INBOX.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue                      # a torn line is not a reason to lose the rest
    return rows


def save(rows: list[dict]) -> None:
    tmp = INBOX.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(INBOX)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--resolve", metavar="ID")
    ap.add_argument("--status", choices=STATUSES[1:], default="workshopped")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    rows = load()
    if a.resolve:
        hit = [r for r in rows if r.get("id") == a.resolve]
        if not hit:
            print(f"no inbox item {a.resolve!r}", file=sys.stderr)
            return 1
        for r in hit:
            r["status"] = a.status
            r["resolved_on"] = dt.date.today().isoformat()
            r["resolution"] = a.note[:1000]
        save(rows)
        print(f"{a.resolve}: {a.status}")
        return 0
    show = rows if a.all else [r for r in rows if r.get("status", "open") == "open"]
    if not show:
        print("EMPTY")
        return 0
    by_co: dict = {}
    for r in show:
        # the publisher's items are about a publish, not a company
        by_co.setdefault((r.get("name") or r.get("company_id")
                          or ("the desk publisher" if r.get("source") == "publisher" else "?")),
                         []).append(r)
    n = 0
    for name, items in sorted(by_co.items(), key=lambda kv: kv[0].casefold()):
        print(f"\n{name}")
        for r in items:
            n += 1
            saw = r.get("saw") or {}
            if "state" in saw:
                shown = (f"the page showed {'green' if saw.get('green') else 'red'}: "
                         f"{saw.get('state')}"
                         + (f" ({saw.get('detail')})" if saw.get("detail") else ""))
            elif saw.get("files"):
                shown = "files: " + ", ".join(saw["files"][:8])
            else:
                shown = "-"
            print(f"  {n}. [{r.get('item')}] {shown}")
            print(f"     {r.get('by', 'owner')} wrote: {r.get('text')}")
            print(f"     id {r.get('id')} · {r.get('at', '')[:16]} · {r.get('status', 'open')}")
    print(f"\n{n} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
