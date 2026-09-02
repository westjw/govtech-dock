#!/usr/bin/env python3
"""Find the exhibitor directories behind the state and local chapter events.

    python3 scripts/find_event_directories.py --parents        # stage 1, look
    python3 scripts/find_event_directories.py --parents --write
    python3 scripts/find_event_directories.py --directories --write   # stage 2

THE PROBLEM. data/state_events.json holds 359 chapter conferences, every one
with an empty directory_url - and a conference with no exhibitor directory
cannot produce a single company. 130 of the 359 also carry a GENERATED
organisation name ("APWA {state} Chapter"), which the generator itself calls
scaffolding. So two things have to be found per event, and neither may be
guessed: where the organisation actually lives, and where its exhibitor list is.

WHERE THE URLS COME FROM, AND WHY NOT A SEARCH ENGINE. Every event carries a
parent_national - NLC, NRPA, APWA, NACo - and 24 of the 26 parents are already
on file in conferences.json with a URL. A national association LISTS ITS OWN
CHAPTERS: NLC keeps a state-league directory, APWA a chapter map, NRPA an
affiliate list. Reading that page hands over the chapter's real name and real
site, published by the body the chapter belongs to. That is a fact with a
source, which a domain guessed from a name is not.

STAGE 1 (--parents): for each parent, fetch its site, find the chapters /
affiliates / state-leagues page, and match each outbound link to an event by
state name. Writes org_name_confirmed and org_url onto the event.

STAGE 2 (--directories): for each event with an org_url, fetch it, find the
exhibit / sponsor / expo page one link away, fetch THAT, and ask
sweep_exhibitors' own harvest() and quality() whether it is a list of
companies. Writes directory_url and status.

WHAT IT RECORDS WHEN IT FAILS. `status` is one of needs_url, org_found,
directory_found, no_chapter_page, no_directory_link, not_a_directory. Each is
a different fact and none is written as another. An event this cannot resolve
is left exactly where it was, for a person.

Paced by ats.HOST_PAUSE. Stage 1 is ~50 requests; stage 2 is up to two per
event.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import pathlib
import re
import sys
import urllib.parse as up

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                              # noqa: E402
import sweep_exhibitors as sweep                        # noqa: E402

EVENTS = DATA / "state_events.json"

CHAPTER_LINK = re.compile(
    r"chapter|affiliate|state league|state municipal|state association|"
    r"section|regional|find your|our members|member (leagues|associations)|"
    r"state (partners|societies|organi[sz]ations)", re.I)
DIRECTORY_LINK = re.compile(
    r"exhibit|sponsor|expo|trade ?show|vendor|marketplace|solution center|"
    r"partner (directory|list)|who.?s exhibiting", re.I)

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}


def links(page: str, base: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        href = up.urljoin(base, html_lib.unescape(m.group(1)))
        if href.startswith("http") and text:
            out.append((text, href))
    return out


def fetch(url: str) -> str | None:
    try:
        return ats._get(url).text
    except Exception:                                   # noqa: BLE001
        return None


# THE ASSOCIATION'S OWN SITE, which is not its conference's site. NRPA's
# conference is at conference.nrpa.org and its affiliate list is at nrpa.org;
# IACP's conference has a domain of its own (theiacpconference.org) that says
# nothing about chapters. A first pass derived the root from the conference
# url and found no chapters page on 15 of 23 parents for exactly this reason
# - and matched "NSA" to nasact.org, because NSA is a substring of NASACT.
#
# So the parents are a table. These are the well-known domains of national
# associations, one line each, and they are looked up rather than inferred.
# A code not in the table is skipped and says so; it is not guessed.
PARENT_SITES = {
    "NLC": "https://www.nlc.org/", "NACO": "https://www.naco.org/",
    "NRPA": "https://www.nrpa.org/", "APWA": "https://www.apwa.org/",
    "AWWA": "https://www.awwa.org/", "WEF": "https://www.wef.org/",
    "SWANA": "https://swana.org/", "APTA": "https://www.apta.com/",
    "GFOA": "https://www.gfoa.org/", "NIGP": "https://www.nigp.org/",
    "GMIS": "https://www.gmis.org/", "IACP": "https://www.theiacp.org/",
    "NSA": "https://www.sheriffs.org/", "IAFC": "https://www.iafc.org/",
    "APCO": "https://www.apcointl.org/", "IAEM": "https://www.iaem.org/",
    "IIMC": "https://www.iimc.com/", "IAAO": "https://www.iaao.org/",
    "ICC": "https://www.iccsafe.org/", "APA": "https://www.planning.org/",
    "ASBO": "https://asbointl.org/", "NSBA": "https://www.nsba.org/",
    "COSN": "https://www.cosn.org/", "APHA": "https://www.apha.org/",
    "APHSA": "https://aphsa.org/", "EREPUBLIC": "https://www.govtech.com/",
}


def parent_sites() -> dict:
    events = json.loads(EVENTS.read_text())["events"]
    codes = sorted({e["parent_national"] for e in events if e.get("parent_national")})
    missing = [c for c in codes if c not in PARENT_SITES]
    if missing:
        print(f"  no site on file for parent(s) {missing}; skipped, not guessed")
    return {c: PARENT_SITES[c] for c in codes if c in PARENT_SITES}


def stage_parents(write: bool) -> int:
    doc = json.loads(EVENTS.read_text())
    events = doc["events"]
    sites = parent_sites()
    print(f"{len(sites)} parent(s) with a site on file\n")
    found = 0
    for code, root in sites.items():
        mine = [e for e in events if e.get("parent_national") == code and not e.get("org_url")]
        if not mine:
            continue
        home = fetch(root)
        if not home:
            print(f"  {code:10} {root[:40]:42} site did not answer"); continue
        cands = [(t, h) for t, h in links(home, root) if CHAPTER_LINK.search(t) or CHAPTER_LINK.search(h)]
        if not cands:
            print(f"  {code:10} {root[:40]:42} no chapters/affiliates link on the home page"); continue
        listing = cands[0][1]
        page = fetch(listing) or ""
        home_host = up.urlsplit(root).netloc.lower().replace("www.", "")
        list_path = up.urlsplit(listing).path.rstrip("/")
        outbound = []
        for t, h in links(page, listing):
            host = up.urlsplit(h).netloc.lower().replace("www.", "")
            path = up.urlsplit(h).path
            # another domain, or a deeper page under the listing itself
            if host != home_host or (list_path and path.startswith(list_path + "/")):
                if h.rstrip("/") != listing.rstrip("/"):
                    outbound.append((t, h))
        hit = 0
        for e in mine:
            geo = (e.get("geo") or "").lower()
            state = next((s for s in STATES if re.search(rf"\b{s}\b", geo)), None)
            if not state:
                continue
            ab = STATES[state]
            for t, h in outbound:
                blob = f"{t} {up.urlsplit(h).path}".lower()
                if (re.search(rf"\b{state}\b", blob)
                        or re.search(rf"(^|[^a-z]){ab.lower()}([^a-z]|$)", blob)):
                    e["org_url"] = h
                    e["org_url_source"] = listing
                    e["status"] = "org_found"
                    hit += 1
                    break
        found += hit
        print(f"  {code:10} {cands[0][1][:44]:46} {len(outbound):3} outbound, matched {hit}/{len(mine)}")
    print(f"\n  {found} event(s) now have an organisation url from their parent's own listing")
    if write:
        _save(doc)
    else:
        print("  LOOKED ONLY. Re-run with --write.")
    return 0


def stage_directories(write: bool, limit: int | None) -> int:
    doc = json.loads(EVENTS.read_text())
    todo = [e for e in doc["events"] if e.get("org_url") and not e.get("directory_url")]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} event(s) with an org url and no directory yet\n")
    got = 0
    for e in todo:
        page = fetch(e["org_url"])
        if not page:
            e["status"] = "org_unreachable"; continue
        cands = [(t, h) for t, h in links(page, e["org_url"]) if DIRECTORY_LINK.search(t) or DIRECTORY_LINK.search(h)]
        if not cands:
            e["status"] = "no_directory_link"; continue
        best = None
        for t, h in cands[:3]:
            d = fetch(h)
            if not d:
                continue
            names = sweep.harvest(d)
            grade, note = sweep.quality(names)
            if not sweep.suspicious(names) and grade in ("good", "mixed"):
                best = (h, len(names), grade, note); break
        if not best:
            e["status"] = "not_a_directory"
            print(f"  --  {e['event_name'][:40]:42} links found, none read as a list")
            continue
        e["directory_url"], e["status"] = best[0], "directory_found"
        e["directory_note"] = f"{best[1]} names, {best[2]} ({best[3]})"
        got += 1
        print(f"  ok  {e['event_name'][:40]:42} {best[1]:4} names  {best[0][:40]}")
    print(f"\n  {got} directory url(s) found")
    if write:
        _save(doc)
    else:
        print("  LOOKED ONLY. Re-run with --write.")
    return 0


def _save(doc: dict) -> None:
    tmp = EVENTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n")
    json.loads(tmp.read_text())
    tmp.replace(EVENTS)
    print("  saved")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parents", action="store_true")
    ap.add_argument("--directories", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.parents:
        return stage_parents(a.write)
    if a.directories:
        return stage_directories(a.write, a.limit)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
