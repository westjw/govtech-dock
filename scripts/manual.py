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
    already covered weekly and should not waste a manual slot.

    A PAGE SCAN THAT SAW A SALES TITLE IS THE STRONGEST REASON TO CHECK, and
    this used to be the one case it excluded.

    116 companies have a careers page that a scan read a quota-carrying title
    off and could not enumerate. Their status is "Yes" - so `unreadable` was
    false, `no_board` was false, and every one of them was filtered out as
    "already covered weekly". They are not covered: what is on the public card
    is a SYNTHETIC row titled "AE-type role (page scan)" with no location, no
    link and no company behind it. It is a marker meaning "somebody should
    look", and nobody was ever sent.

    So the worklist was 966 companies about which we have no evidence at all,
    with the 116 we have the best evidence about excluded by construction -
    the manual-checking equivalent of reading everything except the pages that
    said something.

    A company whose every role is synthetic is unread, whatever its status
    says.
    """
    unreadable = bool(org.get("unreadable"))
    no_board = org.get("ats") in (None, "unknown")
    roles = (org.get("hiring") or {}).get("roles") or []
    scan_only = bool(roles) and all(r.get("synthetic") for r in roles)
    if not (unreadable or no_board or scan_only):
        return False, ""
    age = days_since((checks.get(org["id"]) or {}).get("checked_on"))
    if age is None:
        return True, ("a scan saw a role here, nobody has read it"
                      if scan_only else "never checked")
    if age >= STALE_DAYS:
        return True, f"last checked {age} days ago"
    return False, f"checked {age} days ago"


def cmd_worklist(a) -> int:
    d = load()
    orgs = {o["id"]: o for o in board().get("organizations", [])}
    rows = []
    for c in companies():
        o = orgs.get(c["id"], {})
        # THE BOARD ORG'S `ats` IS A STRING AND THE COMPANY'S IS A DICT, and
        # this merge puts the org's on top - so o["ats"] is "html", not
        # {"type": "html", "ref": ...}. Reading `.get("ref")` off it raises.
        # The careers URL therefore comes from the company record by name.
        o = {**c, **o}
        careers = (c.get("ats") or {}).get("ref")
        due, why = needs_check(o, d["checks"])
        if not due:
            continue
        # EVIDENCE FIRST, THEN TIER, AND NAME LAST OF ALL.
        #
        # This used to sort on (tier, never-checked, NAME), which inside tier 1
        # is the alphabet wearing a ranking's clothes. The first three it
        # offered were "'with' Community Calendar", ACI Worldwide and ADP -
        # a payroll giant whose board is enormous and mostly not govtech
        # sales - while the companies where a page scan ALREADY SAW a
        # quota-carrying title sat unreached further down.
        #
        # That distinction is the whole difference between a worklist and a
        # list. 116 of the page-only companies have a scan that read an AE-type
        # title off the careers page and could not enumerate the board: we know
        # there is something there and only a person can get it. Those are
        # worth an evening. A company nobody has any evidence about is worth
        # one when the evidenced ones run out.
        h = o.get("hiring") or {}
        _r = h.get("roles") or []
        seen = 1 if _r and all(x.get("synthetic") for x in _r) else 2
        tier = o.get("tier") or 3
        rows.append((seen, tier, 0 if why == "never checked" else 1,
                     o["name"], o, why, careers))
    rows.sort()
    total = len(rows)
    rows = rows[:a.limit]
    if not rows:
        print("nothing due. every unreadable company has been checked within "
              f"{STALE_DAYS} days.")
        return 0
    print(f"{total} companies cannot be read automatically; here are {len(rows)} "
          f"worth {len(rows) * 2} minutes:\n")
    for seen, tier, _, name, o, why, careers in rows:
        h = o.get("hiring") or {}
        mark = ("  <- a page scan already read a sales title here"
                if seen == 1 else "")
        print(f"  {name}  (tier {tier}, {o.get('sector','?')}, {why}){mark}")
        if seen == 1 and h.get("note"):
            print(f"    Scan saw:  {h['note']}")
        # THE CAREERS PAGE, which is the thing you actually open. It lives in
        # ats.ref for every one of these - `html` means "a page, not an API" -
        # and printing only the marketing site made a person hunt for the
        # careers link before they could start.
        if isinstance(careers, str) and careers.startswith("http"):
            print(f"    Careers:   {careers}")
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
