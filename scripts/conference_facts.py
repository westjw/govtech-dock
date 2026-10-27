#!/usr/bin/env python3
"""What a seller needs to get on a conference floor, read off the organiser's pages.

    python3 scripts/conference_facts.py brief --out brief.json    # what needs reading
    python3 scripts/conference_facts.py apply --in reads.json     # look only
    python3 scripts/conference_facts.py apply --in reads.json --write --by agent:conf-facts-2026-10-27

Owner, 2026-10-27: "what other info can we take and include on the page?
including links to their stuff? costs?" - and, of four kinds offered, all
four: COSTS (attendee registration, booth and sponsorship prices), EXHIBITOR
LINKS (prospectus, booth sign-up, floor plan, exhibit sales contact),
DEADLINES (early-bird, exhibitor, speakers, hotel), and WHO ATTENDS (stated
attendance and audience, whether a vendor may come without a booth, the
organiser). Collected by the weekly conference scrub.

EVERY FACT IS A READ, NEVER A GUESS, and carries the page it was read off
and the words that say it. A price changes every year, so every read carries
the EDITION it is for and the day it was read, and the page prints both.

THE DOOR REFUSES, item by item, saying why:
  - a source that is not an http(s) address;
  - a fact with no quote, or a cost whose figure is not in its quote: a
    number nobody can find on the page is a number we made;
  - a cost with no currency marker ("$", "USD", "EUR", a pound or euro sign);
  - a deadline that is not a real YYYY-MM-DD date;
  - a link kind we do not know, or a link that is not http(s);
  - a whole read the second reader did not confirm (`confirmed` true);
  - a read for an older edition than the one on file.
A read REPLACES the conference's facts as a whole when it lands, so a price
from last year's prospectus never sits beside this year's.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
DATA = ROOT / "data"
FILE = "conference_facts.json"
LINK_KINDS = {
    "prospectus": "Exhibitor prospectus",
    "booth": "Book a booth",
    "floor_plan": "Floor plan",
    "exhibit_contact": "Exhibit sales contact",
    "registration": "Register to attend",
}
CURRENCY = re.compile(r"\$|\bUSD\b|\bEUR\b|€|£|\bGBP\b|\bCAD\b", re.I)
STALE_DAYS = 120

RULES = """You are reading a government-sector conference's OWN website for what a seller
needs to get on its floor. Read only the organiser's pages (the event site, its
exhibitor/sponsor pages, its registration page, a prospectus PDF it links), using
`python3 scripts/read_page.py <url>` from the repo (it honours robots.txt; at most 12
pages per conference; never any other way of fetching). For each conference return:
  {"tag", "edition": "<the year the facts are for>",
   "costs": [{"what": "Attendee registration, member, early-bird", "amount": "$595",
              "quote": "<the words on the page that state it>", "source": "<url>"}],
   "links": [{"kind": "prospectus|booth|floor_plan|exhibit_contact|registration",
              "url": "<url>", "source": "<page that links it>"}],
   "deadlines": [{"what": "Early-bird registration ends", "date": "YYYY-MM-DD",
                  "quote": "...", "source": "<url>"}],
   "audience": {"attendance": "<as stated, e.g. 6,000+ attendees>",
                "who": "<who attends, in the organiser's words>",
                "vendors": "<whether a vendor may attend without exhibiting, as stated>",
                "organiser": "<the organising association>",
                "quote": "...", "source": "<url>"},
   "unread": "<why, if the site would not answer or says nothing>"}
Rules: copy amounts and dates exactly as the page states them; a figure must appear in
its quote; no estimates, no ranges you built, no prices from a third-party site; if the
page gives no year, the edition is the conference's next date on file. Leave a field
out rather than guess. A second reader then checks every item against the page and
marks the read "confirmed": true only if all of it holds."""


def http(url) -> bool:
    try:
        p = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def digits(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")


def check_read(r: dict, on_file: dict | None) -> tuple[dict | None, list]:
    """(the facts to store, or None) and the refusals, item by item.

    THE REPOSITORY IS PUBLIC: every text field passes privacy.redact before
    it is kept, as proposal ingest does, so an exhibit-sales mailbox read off
    a page never lands in a tracked file (and a mailto: link is refused as
    not http(s)). An address is unescaped once: a page's "&amp;" is "&"."""
    import privacy
    r = privacy.redact_deep(r)
    for group in ("costs", "links", "deadlines"):
        for item in r.get(group) or []:
            for k in ("url", "source"):
                if item.get(k):
                    item[k] = html.unescape(str(item[k]))
    if (r.get("audience") or {}).get("source"):
        r["audience"]["source"] = html.unescape(str(r["audience"]["source"]))
    bad = []
    tag = r.get("tag")
    if not r.get("confirmed"):
        return None, [f"{tag}: the second reader did not confirm this read"]
    edition = str(r.get("edition") or "").strip()
    if not re.fullmatch(r"20\d\d", edition):
        return None, [f"{tag}: edition {edition!r} is not a year"]
    if on_file and str(on_file.get("edition") or "") > edition:
        return None, [f"{tag}: a {edition} read is older than the {on_file['edition']} on file"]
    out = {"edition": edition, "costs": [], "links": [], "deadlines": [], "audience": None}
    for c in r.get("costs") or []:
        what, amount, quote, src = (str(c.get(k) or "").strip() for k in ("what", "amount", "quote", "source"))
        if not (what and amount and quote):
            bad.append(f"{tag}: a cost needs what, amount and quote: {c}"); continue
        if not http(src):
            bad.append(f"{tag}: cost {what!r} has no http(s) source"); continue
        if not CURRENCY.search(amount) or not digits(amount):
            bad.append(f"{tag}: {amount!r} carries no currency and figure"); continue
        if digits(amount) not in digits(quote):
            bad.append(f"{tag}: {amount!r} is not in its quote"); continue
        out["costs"].append({"what": what[:120], "amount": amount[:60], "quote": quote[:300], "source": src})
    seen = set()
    for ln in r.get("links") or []:
        kind, url, src = (str(ln.get(k) or "").strip() for k in ("kind", "url", "source"))
        if kind not in LINK_KINDS:
            bad.append(f"{tag}: link kind {kind!r} is not one of {', '.join(LINK_KINDS)}"); continue
        if not http(url) or (src and not http(src)):
            bad.append(f"{tag}: the {kind} link is not an http(s) address"); continue
        if (kind, url) in seen:
            continue
        seen.add((kind, url))
        out["links"].append({"kind": kind, "url": url, "source": src or url})
    for d in r.get("deadlines") or []:
        what, date, quote, src = (str(d.get(k) or "").strip() for k in ("what", "date", "quote", "source"))
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            bad.append(f"{tag}: deadline {what!r} date {date!r} is not YYYY-MM-DD"); continue
        if not (what and quote) or not http(src):
            bad.append(f"{tag}: deadline {what!r} needs what, quote and an http(s) source"); continue
        out["deadlines"].append({"what": what[:120], "date": date, "quote": quote[:300], "source": src})
    out["deadlines"].sort(key=lambda d: d["date"])
    a = r.get("audience") or {}
    fields = {k: str(a.get(k) or "").strip()[:300] for k in ("attendance", "who", "vendors", "organiser")}
    if any(fields.values()):
        if not str(a.get("quote") or "").strip() or not http(a.get("source")):
            bad.append(f"{tag}: who attends needs a quote and an http(s) source")
        else:
            out["audience"] = {**{k: v for k, v in fields.items() if v},
                               "quote": str(a["quote"]).strip()[:300], "source": a["source"]}
    if not (out["costs"] or out["links"] or out["deadlines"] or out["audience"]):
        return None, bad + [f"{tag}: nothing in this read survived the door"]
    return out, bad


def read_json(name: str, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def catalogue() -> list:
    """data/conferences.json's rows ({"note", "conferences": [...]})."""
    cat = read_json("conferences.json", {})
    return cat.get("conferences", []) if isinstance(cat, dict) else cat


def apply(reads: list, write: bool, by: str) -> int:
    import admin
    tags = {c.get("event_tag") for c in catalogue()}
    with admin.desk_lock():
        facts = read_json(FILE, {})
        landed, refused = 0, []
        today = dt.date.today().isoformat()
        for r in reads:
            if r.get("tag") not in tags:
                refused.append(f"{r.get('tag')!r}: not a conference in the catalogue"); continue
            got, bad = check_read(r, facts.get(r["tag"]))
            refused += bad
            if got:
                facts[r["tag"]] = {**got, "read_on": today, "by": by}
                landed += 1
        print(f"{landed} conference(s) would land" if not write else f"{landed} conference(s) landed")
        for b in refused:
            print(f"  refused {b}")
        if write and landed:
            err = admin.save_decisions(FILE, facts, "conference-facts",
                                       why=f"conference facts read off organisers' pages ({landed}): "
                                           f"costs, exhibitor links, deadlines, who attends; owner, 2026-10-27",
                                       by=by, force=True)
            if err:
                print(f"refused by the journal: {err}")
                return 2
    return 0


def brief(limit: int | None = None) -> dict:
    """Conferences whose facts are missing, stale, or for an edition before
    the next one on file - the next edition first, the flagship floors first."""
    facts = read_json(FILE, {})
    today = dt.date.today()
    todo = []
    for c in catalogue():
        if c.get("sled") is False or not http(c.get("url")):
            continue
        tag, f = c.get("event_tag"), facts.get(c.get("event_tag")) or {}
        nxt = str(c.get("next_edition") or c.get("event_year") or "")
        stale = (not f or (nxt and str(f.get("edition") or "") < nxt)
                 or (today - dt.date.fromisoformat(f.get("read_on") or "2000-01-01")).days > STALE_DAYS)
        if stale:
            todo.append({"tag": tag, "name": c.get("conference"), "url": c.get("url"),
                         "exhibitor_url": c.get("exhibitor_url"), "dates": c.get("dates"),
                         "next_edition": nxt or None, "flagship": bool(c.get("flagship")),
                         "on_file": f or None})
    todo.sort(key=lambda x: (not x["flagship"], x["name"] or ""))
    return {"rules": RULES, "conferences": todo[:limit] if limit else todo}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--out")
    b.add_argument("--limit", type=int)
    a = sub.add_parser("apply")
    a.add_argument("--in", dest="src", required=True)
    a.add_argument("--write", action="store_true")
    a.add_argument("--by", default=f"agent:conf-facts-{dt.date.today().isoformat()}")
    args = ap.parse_args(argv)
    if args.cmd == "brief":
        out = brief(args.limit)
        text = json.dumps(out, indent=1, ensure_ascii=False)
        if args.out:
            pathlib.Path(args.out).write_text(text)
            print(f"{len(out['conferences'])} conference(s) to read: {args.out}")
        else:
            print(text)
        return 0
    if not args.by.startswith("agent:"):
        print("--by names the agent that read them (agent:...)")
        return 2
    return apply(json.loads(pathlib.Path(args.src).read_text()), args.write, args.by)


if __name__ == "__main__":
    raise SystemExit(main())
