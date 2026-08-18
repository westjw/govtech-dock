#!/usr/bin/env python3
"""Manual capture for companies the fetchers cannot read.

Some companies have no job board, or one behind a JS wall or a bot check, and a
few post only to LinkedIn. Those are invisible to build_board.py and always will
be: scraping LinkedIn is excluded by design, and driving an authenticated session
risks the account whose network you are job searching on.

So they get checked by hand. The point of this script is that hand-checking is
only useful if it is remembered. Each check records what was found AND when it
was looked at, so "nothing open" is a real answer with a date on it rather than
a gap indistinguishable from never having looked.

  python scripts/manual.py worklist            # what to check, prioritised
  python scripts/manual.py add <id> "Title" [--location L] [--url U] [--family F]
  python scripts/manual.py none <id>           # checked, nothing open
  python scripts/manual.py show [<id>]

Findings land in data/manual.json and are merged into the board by
build_board.py, tagged so a manual posting is never mistaken for a fetched one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import roles          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANUAL = DATA / "manual.json"

# How long a hand check stays trustworthy. A month is a compromise: shorter and
# the worklist becomes a chore nobody does, longer and "nothing open" stops
# meaning anything.
STALE_DAYS = 30
# Cap the worklist. A list of 43 companies gets ignored; a list of 8 gets done.
WORKLIST_CAP = 8


def load() -> dict:
    if MANUAL.exists():
        return json.loads(MANUAL.read_text())
    return {"checks": {}, "postings": []}


def save(d: dict) -> None:
    MANUAL.write_text(json.dumps(d, indent=1) + "\n")


def companies() -> list[dict]:
    return json.loads((DATA / "companies.json").read_text())


def board() -> dict:
    p = DATA / "board.json"
    return json.loads(p.read_text()) if p.exists() else {"organizations": []}


def linkedin_url(name: str) -> str:
    """A direct search rather than a guessed company slug, which is often wrong."""
    return ("https://www.linkedin.com/jobs/search/?keywords="
            + urllib.parse.quote(name) + "&f_TPR=r604800")


def days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (dt.date.today() - dt.date.fromisoformat(iso)).days
    except ValueError:
        return None


def needs_check(org: dict, checks: dict) -> tuple[bool, str]:
    """Only companies the fetchers genuinely cannot read. Everything else is
    already covered weekly and should not waste a manual slot."""
    unreadable = bool(org.get("unreadable"))
    no_board = org.get("ats") in (None, "unknown")
    if not (unreadable or no_board):
        return False, ""
    age = days_since((checks.get(org["id"]) or {}).get("checked_on"))
    if age is None:
        return True, "never checked"
    if age >= STALE_DAYS:
        return True, f"last checked {age} days ago"
    return False, f"checked {age} days ago"


def cmd_worklist(a) -> int:
    d = load()
    orgs = {o["id"]: o for o in board().get("organizations", [])}
    rows = []
    for c in companies():
        o = orgs.get(c["id"], {})
        o = {**c, **o}
        due, why = needs_check(o, d["checks"])
        if not due:
            continue
        # Tier 1 municipal SaaS first, then never-checked before stale.
        tier = o.get("tier") or 3
        rows.append((tier, 0 if why == "never checked" else 1, o["name"], o, why))
    rows.sort()
    total = len(rows)
    rows = rows[:a.limit]
    if not rows:
        print("nothing due. every unreadable company has been checked within "
              f"{STALE_DAYS} days.")
        return 0
    print(f"{total} companies cannot be read automatically; here are {len(rows)} "
          f"worth {len(rows) * 2} minutes:\n")
    for tier, _, name, o, why in rows:
        print(f"  {name}  (tier {tier}, {o.get('sector','?')}, {why})")
        print(f"    LinkedIn:  {linkedin_url(name)}")
        if o.get("website"):
            print(f"    Site:      {o['website']}")
        print(f"    Record:    python3 scripts/manual.py add {o['id']} \"Title\" "
              f"--location \"...\" --url \"...\"")
        print(f"    Or empty:  python3 scripts/manual.py none {o['id']}")
        print()
    return 0


def cmd_add(a) -> int:
    d = load()
    comp = {c["id"]: c for c in companies()}.get(a.company_id)
    if comp is None:
        print(f"no company with id {a.company_id!r}", file=sys.stderr)
        return 1
    today = dt.date.today().isoformat()
    fam = a.family or roles.family(a.title)
    pid = f"{a.company_id}::{a.title}"
    d["postings"] = [p for p in d["postings"] if p["id"] != pid]
    d["postings"].append({
        "id": pid, "company": comp["name"], "company_id": a.company_id,
        "title": a.title, "family": fam,
        "quota_carrying": roles.is_quota_carrying(a.title),
        "location": a.location or "", "is_us": roles.is_us(a.location or "", a.title),
        "url": a.url, "sector": comp["sector"], "category": comp["category"],
        "first_seen": today, "source": "manual",
    })
    d["checks"][a.company_id] = {"checked_on": today, "found": True}
    save(d)
    print(f"recorded: {comp['name']} - {a.title} [{roles.LABEL.get(fam, fam)}]")
    print(f"  {sum(1 for p in d['postings'] if p['company_id']==a.company_id)} "
          f"manual posting(s) on file for this company")
    return 0


def cmd_none(a) -> int:
    d = load()
    comp = {c["id"]: c for c in companies()}.get(a.company_id)
    if comp is None:
        print(f"no company with id {a.company_id!r}", file=sys.stderr)
        return 1
    today = dt.date.today().isoformat()
    # Drop stale manual postings for this company: they were open last time and
    # are not now, which is exactly what a check is for.
    before = len(d["postings"])
    d["postings"] = [p for p in d["postings"] if p["company_id"] != a.company_id]
    d["checks"][a.company_id] = {"checked_on": today, "found": False}
    save(d)
    closed = before - len(d["postings"])
    print(f"{comp['name']}: checked {today}, nothing open"
          + (f" ({closed} previous posting(s) closed out)" if closed else ""))
    return 0


def cmd_show(a) -> int:
    d = load()
    ps = [p for p in d["postings"]
          if not a.company_id or p["company_id"] == a.company_id]
    if ps:
        print(f"{len(ps)} manual posting(s):\n")
        for p in sorted(ps, key=lambda x: x["company"]):
            q = " [quota]" if p["quota_carrying"] else ""
            print(f"  {p['company']}: {p['title']}{q}")
            print(f"    {p.get('location') or 'location not stated'} "
                  f"| seen {p['first_seen']} | {p.get('url') or 'no url'}")
    else:
        print("no manual postings on file")
    checked = d["checks"]
    if checked:
        fresh = sum(1 for v in checked.values()
                    if (days_since(v.get("checked_on")) or 999) < STALE_DAYS)
        print(f"\n{len(checked)} company check(s) recorded, {fresh} still fresh "
              f"(under {STALE_DAYS} days)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="manual.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worklist"); w.add_argument("--limit", type=int, default=WORKLIST_CAP)
    ad = sub.add_parser("add")
    ad.add_argument("company_id"); ad.add_argument("title")
    ad.add_argument("--location"); ad.add_argument("--url"); ad.add_argument("--family")
    nn = sub.add_parser("none"); nn.add_argument("company_id")
    sh = sub.add_parser("show"); sh.add_argument("company_id", nargs="?")
    a = p.parse_args()
    return {"worklist": cmd_worklist, "add": cmd_add,
            "none": cmd_none, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
