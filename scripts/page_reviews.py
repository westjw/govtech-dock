#!/usr/bin/env python3
""""I looked at this company's page and it is good." One record per company.

    python3 scripts/page_reviews.py                          # what has been signed off
    python3 scripts/page_reviews.py --category "Recreation Management"

WHY A STORED RULING AND NOT A DERIVED STATE. Everything else on the sweep is
derived - a write-up either exists or it does not, and asking the file is
always right and never goes stale. Whether a person has READ the page cannot
be derived from anything: it is a fact about the owner, not about the record,
and the only place it can live is where he put it.

IT IS A RULING, SO IT CARRIES WHAT EVERY RULING HERE CARRIES: an author, a
date and room for a reason. CLAUDE.md's rule for the queues - "every ruling
gets an author, a timestamp and a reason, and none can be added afterwards".

THE SIGN-OFF REMEMBERS WHAT WAS TRUE WHEN IT WAS MADE, which is the part that
stops this rotting. A page signed off today whose write-up is retracted next
month is not still reviewed - it is a page somebody read in a state it is no
longer in. So the gaps at the moment of signing are stored alongside, and
`stale_for()` compares them with the gaps now. The same reasoning as
`.profiles_read` recording WHEN a category was gated rather than merely THAT
it was: a marker that cannot say what it covered certifies work nobody did.

It never blocks anything. A stale sign-off re-opens a row on the sweep and
says what changed; it does not un-publish a page or touch the map.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FILE = "page_reviews.json"


def load() -> dict:
    import admin
    d = admin.read(FILE, {})
    return d if isinstance(d, dict) else {}


def save(rows: dict, action: str, why: str, by: str) -> str | None:
    """Through save_decisions, so the journal keeps a before-image."""
    import admin
    return admin.save_decisions(FILE, rows, action, why=why, by=by,
                                force=len(rows) > 25)


def record(company_id: str, by: str, gaps: list, note: str = "") -> dict:
    """Sign one page off. `gaps` is what was outstanding at that moment.

    An EMPTY gaps list is the ordinary case and is stored as one: it is the
    difference between "nothing was missing when I looked" and "I did not
    record what was missing", and only the first is evidence.
    """
    if not company_id:
        raise ValueError("a review names the company it is about")
    if not by or "@" in by:
        raise ValueError("a review names its author as a handle, never an "
                         "address - check_no_person_in_the_repo refuses one")
    if not isinstance(gaps, list):
        raise ValueError("gaps must be a list, empty when nothing was missing")
    return {"by": by, "on": dt.date.today().isoformat(),
            "note": (note or "").strip()[:400],
            "gaps_then": sorted(str(g) for g in gaps)}


def stale_for(review: dict | None, gaps_now: list) -> list:
    """What has changed since this page was signed off. [] when nothing has.

    Reports BOTH directions. A gap that appeared is the obvious one - the
    write-up was retracted, the board stopped reading. A gap that CLOSED
    matters too: the page he approved is not the page a visitor sees, and he
    may want to look at what landed.
    """
    if not review:
        return []
    then = set(review.get("gaps_then") or [])
    now = set(str(g) for g in gaps_now)
    out = [f"+{g}" for g in sorted(now - then)]
    out += [f"-{g}" for g in sorted(then - now)]
    return out


def main() -> int:
    import admin
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--category")
    a = ap.parse_args()
    rows = load()
    cos = {c["id"]: c for c in admin.read_companies()}
    if a.category:
        keep = {i for i, c in cos.items() if c.get("category") == a.category}
        rows = {k: v for k, v in rows.items() if k in keep}
        total = len(keep)
        print(f"{a.category}: {len(rows)} of {total} pages signed off")
    else:
        print(f"{len(rows)} page(s) signed off, of {len(cos):,} companies")
    for cid, r in sorted(rows.items()):
        nm = cos.get(cid, {}).get("name", cid)
        print(f"  {nm[:30]:32} {r.get('on')}  by {r.get('by')}"
              + (f"  ({r['note'][:40]})" if r.get("note") else "")
              + (f"  gaps then: {', '.join(r['gaps_then'])}"
                 if r.get("gaps_then") else "  nothing missing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
