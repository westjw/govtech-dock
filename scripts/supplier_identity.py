#!/usr/bin/env python3
"""Confirm who each supplier is - step 1 of moving the suppliers to SLED HQ.

The owner (2026-10-02): move the suppliers onto SLED HQ and sort them in
detail. Sorting needs to know WHO each one is first, and the 7,919 records
mostly cannot say: 5,474 have no website, nearly every description is one line
off a conference listing, and two listings can be one company. So this answers
three questions per supplier, and only these three:

  1. Is it a DUPLICATE - of another supplier, or of a company already on the
     board? Same website host, or the same name once legal suffixes go.
  2. Where none is on file, can a website be FOUND - a domain guessed from
     the name and accepted only when the page's own identity fields name the
     company (find_websites.identifies, the same strict door the board uses;
     a live page proves only that a domain resolves).
  3. Where one is on file, is it THEIRS - does the page still name them, has
     it gone to a for-sale page, does it now name somebody else?

Every answer is said as what we managed to read, never as what the company
is: "no candidate domain named them" is not "they have no website", and a site
that would not answer is "could not read", not "not theirs".

IT WRITES ONE FILE, data/supplier_identity.json - a staged finding with its
evidence. It never writes suppliers.json: landing a website on a record is a
separate step a person rules on (agents propose, people rule).

    python3 scripts/supplier_identity.py --sample 200      # measure first
    python3 scripts/supplier_identity.py --write            # everything
    python3 scripts/supplier_identity.py --stats            # what the file says
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import random
import re
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import find_websites as fw  # noqa: E402

OUT = "supplier_identity.json"
RETRY_DAYS = 60

# What each verdict means, in the words the report and SLED HQ will use. A
# verdict is about what we READ, never a claim about the company.
VERDICTS = {
    "theirs": "the website on file names them",
    "found": "no website was on file; a guessed domain names them",
    "found_review": "a guessed domain names them, but the name is too short to be sure",
    "names_other": "the website on file now names somebody else",
    "parked": "the website on file is a for-sale or holding page",
    "unreadable": "the website on file did not answer, so nothing is known",
    "not_found": "no website on file, and no guessed domain named them",
    "duplicate": "the same company as another record (see duplicate_of)",
    "not_a_company": "the name is not a company: an address, a number or site navigation",
    "listing_site": "the website on file is the association or event page that listed "
                    "them, and no site of their own was found",
}


def host(url: str | None) -> str:
    if not url:
        return ""
    u = url if "//" in url else "https://" + url
    h = urllib.parse.urlsplit(u).netloc.lower().split(":")[0]
    return h.removeprefix("www.")


def root(h: str) -> str:
    """The registrable part, roughly: the last two labels, three for a
    country second level (co.uk). Good enough to say two records share a
    site; never used to build a URL."""
    parts = [p for p in h.split(".") if p]
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac") \
            and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# Sites many unrelated listings point at - a parent's portal, a marketplace,
# a social profile. Two suppliers sharing one of these share nothing.
SHARED_HOSTS = {"linkedin.com", "facebook.com", "google.com", "sites.google.com",
                "wixsite.com", "squarespace.com", "godaddysites.com",
                "business.site", "amazon.com", "youtube.com", "instagram.com",
                "x.com", "twitter.com", "yelp.com", "bbb.org", "github.io"}


# WHAT A HARVESTED DIRECTORY LEAVES BEHIND. Association sites were swept as
# exhibitor lists and their menus came along: every one of the 57 records on
# ccm-ct.org and the 31 on ncsea.org is a menu item ("Advocacy", "Career
# Center", "2026 CCM Convention"). Only EXACT shapes are refused here - the
# exhibitor sweep's anchored patterns, an address, a bare number. Its broader
# rules (a name starting with a verb or "The", one containing " / ") are
# right for a page being harvested and wrong for a record already kept: they
# would refuse "The Home Depot" and "Neptune Benson / Xylem".
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
NUMBER = re.compile(r"^\s*(booth\s*#?\s*)?[#\d][\d\s#-]*$", re.I)


def junk_reason(name: str) -> str | None:
    import sweep_exhibitors as sw
    n = sw.clean(name or "")
    if not n:
        return "the record has no name"
    if ADDRESS.search(n):
        return "the name is an email address"
    if NUMBER.match(n):
        return "the name is a number or booth number"
    for rx in (sw.NAV, sw.TIER, sw.SECTIONS, sw.JUNK):
        if rx.search(n):
            return "the name is site navigation"
    return None


def listing_hosts(suppliers: list) -> set:
    """Hosts that are a LISTING, not a company's site: shared by three or more
    records, most of whose names do not carry the host's own name. misheriff.org
    holds 68 records and names none of them; cgi.com holds three and they are
    all CGI - those are one company listed three times, a duplicate, not a
    listing."""
    by: dict = {}
    for s in suppliers:
        r = root(host(s.get("website")))
        if r:
            by.setdefault(r, []).append(s)
    out = set()
    for r, rows in by.items():
        if len(rows) < 3:
            continue
        label = re.sub(r"[^a-z0-9]", "", r.split(".")[0])
        carry = sum(1 for s in rows if label and label in name_key(s.get("name")))
        if carry * 2 < len(rows):
            out.add(r)
    return out


def name_key(name: str) -> str:
    """A name with legal suffixes and punctuation gone. "Acme, Inc." and
    "ACME Inc" are one key; "Acme Systems" is another (STOP words stay -
    they are what separates two real companies)."""
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
             if w and w not in fw.LEGAL]
    return "".join(words)


def duplicates(suppliers: list, companies: list, listing: set | None = None) -> dict:
    """{supplier id: {"of": id, "on_board": bool, "why": str}}.

    The first record of a group (by id) stands; the rest point at it. A
    supplier that matches a company on the board points at the company -
    the board's record is the one with research behind it."""
    out: dict = {}
    by_host: dict = {}
    by_name: dict = {}
    skip = SHARED_HOSTS | (listing if listing is not None else listing_hosts(suppliers))
    for c in companies:
        r = root(host(c.get("website")))
        if r and r not in skip:
            by_host.setdefault(r, ("board", c["id"]))
        k = name_key(c.get("name"))
        if len(k) >= 4:
            by_name.setdefault(k, ("board", c["id"]))
    for s in sorted(suppliers, key=lambda x: x["id"]):
        r = root(host(s.get("website")))
        k = name_key(s.get("name"))
        hit, why = None, ""
        if junk_reason(s.get("name")):
            continue                      # not a company: nothing to be a copy of
        if r and r not in skip and r in by_host:
            hit, why = by_host[r], f"same website ({r})"
        elif len(k) >= 4 and k in by_name:
            hit, why = by_name[k], "same name"
        if hit:
            out[s["id"]] = {"of": hit[1], "on_board": hit[0] == "board", "why": why}
            continue
        if r and r not in skip:
            by_host.setdefault(r, ("supplier", s["id"]))
        if len(k) >= 4:
            by_name.setdefault(k, ("supplier", s["id"]))
    return out


def fetch(url: str):
    import ats
    return ats._get(url)


def check_own(s: dict, get=fetch) -> dict:
    """Is the website on file theirs?"""
    url = s["website"]
    try:
        r = get(url)
    except Exception as exc:                            # noqa: BLE001
        return {"verdict": "unreadable", "url": url,
                "why": f"{type(exc).__name__}: {str(exc)[:120]}"}
    status = getattr(r, "status_code", 200)
    html = getattr(r, "text", "") or ""
    final = str(getattr(r, "url", url) or url).rstrip("/")
    if status and status >= 400:
        return {"verdict": "unreadable", "url": url, "final": final,
                "why": f"HTTP {status}"}
    if fw._parked(html):
        return {"verdict": "parked", "url": url, "final": final,
                "why": "for-sale or holding page"}
    base = host(final).rsplit(".", 1)[0]
    note = fw.identity_note(html, s.get("name", ""), base, s.get("also_known_as"))
    ev = {"url": url, "final": final, "says": note["says"],
          "present": note["present"], "missing": note["missing"]}
    if host(final) and root(host(final)) != root(host(url)):
        ev["moved_to"] = host(final)
    if note["ok"]:
        return dict(ev, verdict="theirs")
    # The page names nobody we can match. A brand shorter than the listing
    # ("Eagleview" for "EagleView Technologies") and a different business on
    # the domain look alike here, so it is said with what matched and what
    # did not - a person decides.
    names = [n["name"] for n in fw.name_candidates(html, base)[:2]]
    return dict(ev, verdict="names_other", page_names=names)


def find_site(s: dict, get=fetch) -> dict:
    """No website on file: guess domains, accept only one that names them."""
    name = s.get("name", "")
    tried = 0
    for url in fw.candidates(name):
        base = url.split("//", 1)[1].rsplit(".", 1)[0]
        tried += 1
        try:
            r = get(url)
        except Exception:                               # noqa: BLE001
            continue
        if getattr(r, "status_code", 200) >= 400:
            continue
        html = getattr(r, "text", "") or ""
        if fw.identifies(html, name, base, s.get("also_known_as")):
            note = fw.identity_note(html, name, base)
            return {"verdict": "found_review" if fw.short_name(name) else "found",
                    "url": str(getattr(r, "url", url) or url).rstrip("/"),
                    "says": note["says"], "tried": tried}
    return {"verdict": "not_found", "tried": tried,
            "why": "no guessed domain named them" if tried else "the name gives nothing to guess"}


def check(s: dict, get=fetch, listing: set = frozenset()) -> dict:
    r = root(host(s.get("website")))
    if s.get("website") and r in listing:
        # The site on file is where they were LISTED. Search for their own,
        # but only on a name with two or more distinctive words: a menu item
        # off that site ("Employment", "Awards") is one word, and guessing a
        # domain for one word finds whoever owns the word.
        if len(fw.tokens(s.get("name", ""))) >= 2:
            row = find_site(s, get)
            if row["verdict"] == "not_found":
                row = dict(row, verdict="listing_site")
            elif row["verdict"] == "found":
                # these names came off a site whose menus were swept with its
                # exhibitors; a two-word menu item ("Career Center") can own
                # a domain too, so a person confirms
                row = dict(row, verdict="found_review",
                           why="the name came off an association site that also yielded menu items")
        else:
            row = {"verdict": "listing_site", "tried": 0,
                   "why": "one-word name off an association site; not searched"}
        row["listing_host"] = r
    else:
        row = check_own(s, get) if s.get("website") else find_site(s, get)
    row["checked_on"] = dt.date.today().isoformat()
    return row


def run(suppliers: list, companies: list, prior: dict, *, workers: int = 12,
        get=fetch, progress=None, all_suppliers: list | None = None) -> dict:
    """{id: row} for every supplier given. Duplicates are settled offline
    first and never fetched; a supplier checked within RETRY_DAYS keeps its
    row."""
    listing = listing_hosts(all_suppliers if all_suppliers is not None else suppliers)
    dups = duplicates(all_suppliers if all_suppliers is not None else suppliers,
                      companies, listing)
    today = dt.date.today()
    rows: dict = {}
    todo = []
    for s in suppliers:
        jr = junk_reason(s.get("name"))
        if jr:
            rows[s["id"]] = {"verdict": "not_a_company", "why": jr,
                             "checked_on": today.isoformat()}
            continue
        if s["id"] in dups:
            rows[s["id"]] = {"verdict": "duplicate", "duplicate_of": dups[s["id"]]["of"],
                             "on_board": dups[s["id"]]["on_board"],
                             "why": dups[s["id"]]["why"],
                             "checked_on": today.isoformat()}
            continue
        old = prior.get(s["id"])
        try:
            fresh = old and (today - dt.date.fromisoformat(old["checked_on"])).days < RETRY_DAYS \
                and old.get("verdict") not in ("duplicate", "not_a_company") and \
                old.get("website_was") == s.get("website")
        except (KeyError, ValueError, TypeError):
            fresh = False
        if fresh:
            rows[s["id"]] = old
        else:
            todo.append(s)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check, s, get, listing): s for s in todo}
        for i, f in enumerate(cf.as_completed(futs), 1):
            s = futs[f]
            try:
                row = f.result()
            except Exception as exc:                    # noqa: BLE001
                row = {"verdict": "unreadable", "why": f"crashed: {type(exc).__name__}",
                       "checked_on": today.isoformat()}
            row["website_was"] = s.get("website")
            rows[s["id"]] = row
            if progress and i % 100 == 0:
                progress(i, len(todo))
    for s in suppliers:
        rows[s["id"]].setdefault("website_was", s.get("website"))
    return rows


def summary(rows: dict) -> collections.Counter:
    return collections.Counter(r.get("verdict") for r in rows.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", type=int, help="check a random sample of this many")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--write", action="store_true", help="write data/supplier_identity.json")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    import admin
    stored = admin.read(OUT, {})
    prior = stored.get("rows") if isinstance(stored.get("rows"), dict) else {}
    if a.stats:
        c = summary(prior)
        print(f"{len(prior)} suppliers checked (as of {stored.get('generated')})")
        for k, n in c.most_common():
            print(f"  {n:>5}  {k:13} {VERDICTS.get(k, '')}")
        return 0
    suppliers = admin.read("suppliers.json", [])
    companies = admin.read_companies()
    pick = suppliers
    if a.sample:
        rnd = random.Random(a.seed)
        pick = rnd.sample(suppliers, min(a.sample, len(suppliers)))
    t0 = time.time()
    rows = run(pick, companies, {} if a.sample else prior, workers=a.workers,
               all_suppliers=suppliers,
               progress=lambda i, n: print(f"  {i}/{n} checked, {time.time() - t0:.0f}s",
                                           flush=True))
    took = time.time() - t0
    c = summary(rows)
    print(f"\n{len(rows)} suppliers in {took:.0f}s")
    for k, n in c.most_common():
        print(f"  {n:>5}  {n * 100 // max(len(rows), 1):>3}%  {k:13} {VERDICTS.get(k, '')}")
    if a.sample:
        with_site = sum(1 for s in pick if s.get("website"))
        print(f"\nsample: {with_site} had a website on file, {len(pick) - with_site} did not")
        print(f"projected for all {len(suppliers)}: about {took * len(suppliers) / max(len(pick), 1) / 60:.0f} min")
    if not a.write:
        print("\n(nothing written: --write records data/supplier_identity.json)")
        return 0
    if a.sample:
        print("\n--write records the full run only; a sample is a measurement")
        return 1
    admin.write_atomic(OUT, {"generated": dt.datetime.now().astimezone().isoformat(
        timespec="seconds"), "verdicts": VERDICTS, "rows": rows})
    print(f"\nwrote data/{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
