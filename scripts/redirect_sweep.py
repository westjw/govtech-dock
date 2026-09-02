#!/usr/bin/env python3
"""Follow each unread company's HOMEPAGE and record where it actually lands.

    python3 scripts/redirect_sweep.py            # look
    python3 scripts/redirect_sweep.py --write    # record into board_audit.json

WHY. acquisitions.py already treats "a page that redirects" as its second
strongest evidence - but it only ever saw redirects from a CAREERS page, read
during discovery. A company whose probe was BLOCKED never got that far, so a
blocked domain could not reveal an acquisition. ALC Schools sat in the blocked
queue for two weeks as "we learned nothing" while alcschools.com 301'd straight
to everdriven.com; a person opening it in a browser saw it in one second.

Re-probing 202 blocked companies on 2026-09-02 found nine redirects, three of
them onto companies already on the board (EverDriven, Ativion, Granicus) and
one onto a domain-for-sale page. Six of the nine were only found on a second
pass, because the first checked the HTTP status BEFORE the redirect and threw
away anything that redirected and then 403'd - which is exactly what ALC does.
A redirect is a fact even when the destination refuses; where it went is the
finding, what the destination thought of us is a separate one.

WHERE IT WRITES. board_audit.json, as a row carrying `redirect: {from, to,
why}`. q_acquisitions reads that field off any row and ranks it "redirect" -
so this needs no change to the queue, it just feeds it. A row is written only
for a redirect that leaves the company's own registrable domain; a www-to-apex
hop or a .net-to-.com move is the same company and is not an acquisition.

It probes the HOMEPAGE, paced by ats.HOST_PAUSE, one request per company.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse as up

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                            # noqa: E402
import ats                                              # noqa: E402
from acquisitions import _registrable                   # noqa: E402


def landing(url: str) -> tuple[str | None, int | None]:
    """(final host, status). The host is the fact; the status rides along."""
    ats._host_gate(url)
    try:
        r = requests.get(url, headers=ats.UA, timeout=(5, 12),
                         allow_redirects=True, stream=True)
        r.close()
    except requests.RequestException:
        return None, None
    return up.urlsplit(r.url).netloc.lower(), r.status_code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="every company with no board, not only the blocked queue")
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    rows = admin.q_blocked(companies, board)
    if a.all:
        rows = rows + [r for r in admin.q_boards(companies, board)
                       if r["id"] not in {x["id"] for x in rows}]
    rows = [r for r in rows if r.get("website")]

    by_domain = {}
    for c in companies:
        d = _registrable(up.urlsplit(c.get("website") or "").netloc.lower())
        if d:
            by_domain.setdefault(d, c)

    print(f"probing {len(rows)} homepage(s) for off-domain redirects\n")
    found = []
    for i, r in enumerate(rows):
        host, code = landing(r["website"])
        if not host:
            continue
        a_dom = _registrable(up.urlsplit(r["website"]).netloc.lower())
        b_dom = _registrable(host)
        if not b_dom or b_dom == a_dom:
            continue
        owner = by_domain.get(b_dom)
        found.append({"id": r["id"], "name": r["name"], "from": r["website"],
                      "to": host, "status": code,
                      "owner": owner["name"] if owner else None,
                      "owner_id": owner["id"] if owner else None,
                      "parked": "forsale" in host or "sedo" in host
                                or "parking" in host})
        if i and i % 60 == 0:
            print(f"  …{i}/{len(rows)}", flush=True)

    print(f"\n{len(found)} redirect(s) off their own domain:")
    for f in found:
        tag = (f"= {f['owner']} (ON THE BOARD)" if f["owner"]
               else "domain for sale" if f["parked"] else "not on the board")
        print(f"  {f['name'][:26]:28} -> {f['to'][:26]:28} [{f['status']}] {tag}")

    if not a.write:
        print("\n  LOOKED ONLY. Re-run with --write to record them for the "
              "Acquisitions queue.")
        return 0

    path = DATA / "board_audit.json"
    audit = json.loads(path.read_text()) if path.exists() else []
    have = {row.get("id") for row in audit}
    added = 0
    for f in found:
        if f["parked"]:
            continue                    # a dead domain is not an acquisition
        why = (f"their homepage {f['from']} ends up on {f['to']}"
               + (f", which is {f['owner']}'s domain" if f["owner"] else "")
               + (f" (the destination answered HTTP {f['status']})"
                  if f["status"] and f["status"] >= 400 else ""))
        rec = {"redirect": {"from": f["from"], "to": f["to"], "why": why,
                            "seen": admin.today() if hasattr(admin, "today") else None}}
        if f["id"] in have:
            for row in audit:
                if row.get("id") == f["id"]:
                    row.update(rec)
        else:
            c = next(x for x in companies if x["id"] == f["id"])
            audit.append({"id": f["id"], "name": f["name"],
                          "type": (c.get("ats") or {}).get("type"),
                          "ref": (c.get("ats") or {}).get("ref"),
                          "identity": None, "identity_why": None,
                          "slug_tell": None, "postings": 0, **rec})
        added += 1
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(audit, indent=1) + "\n")
    json.loads(tmp.read_text())
    tmp.replace(path)
    print(f"\n  recorded {added} redirect(s) in board_audit.json; the Acquisitions "
          f"queue will show them next time it is opened")
    return 0


if __name__ == "__main__":
    sys.exit(main())
