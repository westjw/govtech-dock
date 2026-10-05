#!/usr/bin/env python3
"""Read what each supplier's own site says it sells. Mechanical half only.

    python3 scripts/supplier_pages.py                    # look only: who would be read
    python3 scripts/supplier_pages.py --write            # read every one not read yet
    python3 scripts/supplier_pages.py --write --ids a,b  # just these

WHY. Step 2 of the supplier work puts every supplier into the categories the
govtech companies already use (owner, 2026-10-05: "throw them in the
categories we have for govtech"). The conference a supplier exhibited at
says which part of government it sells to; only its own site says WHAT it
sells, and every category assignment has to quote a sentence from a page we
actually fetched. This fetches those pages and stops. supplier_categories.py
does the sorting.

WHICH SUPPLIERS. Those whose website step 1 found or confirmed
(data/supplier_identity.json: theirs, found, unconfirmed, found_review). A
parked site, a bot wall and no site at all are not read: there is nothing of
theirs to quote, and under the owner's rule they are not published anyway.

WHAT IS KEPT, AND WHERE. The homepage plus up to three pages whose path says
products, solutions, services, industries or about, as text, in
data/supplier_pages/<id>.json - gitignored, because it is other people's
words and the repo is public (the same rule as data/site_pages/). Fetched
through fetch_profiles.grab, so it is paced per host by ats._get and the
crawler identifies itself; a 403 is recorded, never retried as a browser.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse as up

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_profiles as fp  # noqa: E402

DIR = DATA / "supplier_pages"
READ = {"theirs", "found", "unconfirmed", "found_review"}

# A supplier's "what we sell" lives under different paths from a software
# company's: catalogues, capabilities, markets and industries served.
SELLS = re.compile(r"/(products?|solutions?|services?|what-we-do|industries|"
                   r"markets|sectors|capabilities|applications|equipment|"
                   r"catalog|catalogue|shop|government|public-sector|"
                   r"municipal(ities)?|about|about-us|company|who-we-are)(/|$|\?|\.)",
                   re.I)
EXTRA = 3


def site_for(row: dict) -> str | None:
    if row.get("verdict") not in READ:
        return None
    return row.get("url") or row.get("website_was")


def visit(sid: str, site: str) -> dict:
    out = {"id": sid, "website": site, "fetched_on": dt.date.today().isoformat(),
           "pages": [], "unread": None}
    home = fp.grab(site)
    if home.get("unread"):
        out["unread"] = f"homepage: {home['unread']}"
        return out
    out["pages"].append(home)
    try:
        body = fp.ats._get(site).text
    except Exception:                                   # noqa: BLE001
        body = ""
    seen = {(up.urlsplit(site).path or "/").rstrip("/").lower()}
    picked = []
    for u in fp.links(body, site):
        path = up.urlsplit(u).path or "/"
        if not SELLS.search(path) or fp.NOT_A_PAGE.search(path):
            continue
        key = path.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(u)
    # products and solutions before about: an about page tells the story,
    # a products page tells the category
    picked.sort(key=lambda u: 1 if re.search(r"/(about|company|who-we-are)", u, re.I) else 0)
    for u in picked[:EXTRA]:
        out["pages"].append(fp.grab(u))
    return out


def load(sid: str) -> dict | None:
    p = DIR / f"{sid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ids", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--refresh", action="store_true", help="re-read pages already stored")
    a = ap.parse_args()

    rows = json.loads((DATA / "supplier_identity.json").read_text())["rows"]
    want = {s for s in a.ids.split(",") if s} or None
    todo = []
    for sid, row in rows.items():
        if want and sid not in want:
            continue
        site = site_for(row)
        if not site:
            continue
        if not a.refresh and (DIR / f"{sid}.json").exists():
            continue
        todo.append((sid, site))
    print(f"{len(todo)} supplier site(s) to read")
    if not a.write:
        return 0
    DIR.mkdir(exist_ok=True)
    done = unread = 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(visit, sid, site): sid for sid, site in todo}
        for f in cf.as_completed(futs):
            sid = futs[f]
            try:
                rec = f.result()
            except Exception as exc:                    # noqa: BLE001
                rec = {"id": sid, "pages": [], "unread": f"error: {str(exc)[:120]}",
                       "fetched_on": dt.date.today().isoformat()}
            (DIR / f"{sid}.json").write_text(json.dumps(rec) + "\n")
            done += 1
            unread += bool(rec.get("unread"))
            if done % 100 == 0:
                print(f"  {done}/{len(todo)} read, {unread} unread", flush=True)
    print(f"{done} read, {unread} unread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
