#!/usr/bin/env python3
"""Assemble the public site into public/, by allowlist.

The repo is private and data/ is full of working files: discovery logs with
per-company failure notes, the acquisition review queue, the 2,777-company
supplier list, submissions, website probe logs. A static host pointed at the
repo root would serve every one of them. So this ships an ALLOWLIST - the two
files the site actually reads - rather than excluding things one at a time and
hoping the list stays complete.

It also sanitises board.json. The site renders a "board could not be read"
chip, and the underlying string is a raw fetch error carrying the ATS API URL
and the company's slug ("HTTP 404 for https://api.lever.co/v0/postings/apptegy").
That is a debugging detail, not something to publish, and it hands a reader the
exact endpoint we probe. The public build replaces it with the fact, and keeps
the detail in the private repo where it is useful.

  python scripts/build_site.py [--out public]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Everything the public site is allowed to serve. Adding a file here is a
# deliberate act; nothing is included by walking a directory.
SHIP = ["index.html", "alerts.html"]


# Queue-row fields the admin page never reads. "why" and "evidence" are the
# reviewer's own prose about a company - 477 internal notes that were being
# published to make a page that does not render them. Dropping them costs
# nothing and removes the largest and most sensitive part of the payload.
#
# source_event, game and floors are NOT dropped: the page does render them
# (the conference a company was mined from is a feature the owner asked for,
# and the counters are the point of the landing screen). They stay behind the
# SHIP_ADMIN gate rather than being stripped into uselessness.
DROP_QUEUE = {"why", "evidence", "ats_note", "notes"}


def _public_row(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in DROP_QUEUE}


def build_admin_bundle(out: "pathlib.Path") -> None:
    """The web admin: the judgment queues, precomputed at build time.

    NOT SHIPPED UNLESS SHIP_ADMIN=1 IS SET IN THE BUILD ENVIRONMENT.

    That default is the correction to a real leak. The reasoning used to be
    that /admin is safe to publish because the Cloudflare Access application
    covers it - but Access was never created, so for as long as this shipped,
    https://solesourcejobs.com/admin/data.json returned 245KB to anyone who
    asked: 243 internal review notes, 234 pieces of unmade-ruling reasoning,
    which conference exhibitor lists are being mined and how far along each
    is, and the owner's personal work record.

    The old docstring claimed the page "shows company names and public
    postings data only, the same facts the public board already serves".
    That was wrong, and being written down made it harder to notice.

    Two changes, and the ORDER matters. The gate below means a build with no
    Access application publishes no admin at all - misconfiguration now means
    "nothing is there", which is what the ruling endpoint already assumed.
    And the payload is stripped of internal reasoning either way, so that if
    Access is ever misconfigured the blast radius is queue contents rather
    than a research file.
    """
    if os.environ.get("SHIP_ADMIN") != "1":
        print("  admin bundle: NOT shipped (set SHIP_ADMIN=1 once the "
              "Cloudflare Access application covers /admin)")
        return
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    import admin as _admin
    companies = json.loads((ROOT / "data" / "companies.json").read_text())
    board = json.loads((ROOT / "data" / "board.json").read_text())
    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    tri = _admin.triage(companies, board)
    # The company's own mark, so a row on a phone is recognisable before it is
    # read. The owner asked for logos on the admin pages; the desktop admin got
    # them and this one did not, which is the kind of gap that survives because
    # nobody looks at the same screen twice.
    #
    # A MANIFEST, not the images: {id: extension}. The page builds the src from
    # it, which is 2 KB instead of 2,103 speculative requests that mostly 404.
    logo_manifest = {}
    _ldir = ROOT / "assets" / "logos"
    if _ldir.exists():
        for f in _ldir.glob("*.*"):
            logo_manifest[f.stem] = f.suffix.lstrip(".")

    payload = {
        "generated": board.get("generated"),
        "logos": logo_manifest,
        "companies": len(companies),
        "postings": len(board.get("postings", [])),
        "game": tri.get("game"),
        "floors": tri.get("floors"),
        "visible_now": next((r["n"] for r in tri.get("recommend", [])
                             if r["queue"] == "miscategorized"), 0),
        "schema": {x["name"]: [c for c in x["categories"]
                               if c != "Suppliers & Services"]
                   for x in schema["sectors"]},
        # `id` rides along so the page can build a logo src. It is the
        # company's own kebab id, already public on the board, and the row
        # carried only `key` before - a hash of the name, which no asset is
        # filed under.
        "vendors": [_public_row(v) for v in _admin.q_vendor_scope(companies, board)],
        "miscategorized": [_public_row(v)
                           for v in _admin.q_miscategorized(companies, board)],
    }
    admin_dir = out / "admin"
    admin_dir.mkdir(parents=True, exist_ok=True)
    (admin_dir / "index.html").write_text((ROOT / "admin-web.html").read_text())
    (admin_dir / "data.json").write_text(json.dumps(payload))
    print(f"  admin bundle: {len(payload['vendors'])} vendors, "
          f"{len(payload['miscategorized'])} wrong-bucket")

# Organization fields the site never reads. Dropping them is not security -
# the data is public job postings - it is not publishing internal bookkeeping
# under a domain that looks authoritative.
# no_board_on_file stays: index.html renders an honest "N companies produced
# no readable board" count from it, and stripping it silently turned 969
# into 16 on the public page - the field the comment called never-read was
# read every day. ats_note stays stripped (internal review notes); the site
# degrades gracefully without it.
DROP_ORG = {"vendor_type", "govtech"}


def sanitize(board: dict) -> dict:
    """Strip debugging detail out of the copy that goes public."""
    stripped = 0
    for o in board.get("organizations", []):
        for k in DROP_ORG:
            o.pop(k, None)
        if o.get("unreadable"):
            # Keep the FACT (the site renders a chip from it) and drop the
            # endpoint. "HTTP 404 for https://api.lever.co/v0/postings/<slug>"
            # tells a reader which API we hit and under what name.
            raw = str(o["unreadable"])
            m = re.match(r"HTTP (\d{3})", raw)
            o["unreadable"] = (f"the board returned HTTP {m.group(1)}" if m
                               else "the board could not be read automatically")
            stripped += 1
        if o.get("ats_note"):
            # Internal review notes, e.g. "cleared on audit: quorum.com sells
            # disaster recovery, not government affairs software".
            o.pop("ats_note", None)
    board["_public"] = True
    return board, stripped


# A refresh that breaks quietly is the failure mode a daily unattended deploy
# invents. Every fetcher this repo has broken in some new way, and the symptom
# is always the same shape: the board still builds, it is just suddenly much
# smaller. Publishing that at 06:00 replaces a good board with a bad one and
# nobody finds out until they look.
#
# So the gate refuses to build on a sharp DROP, and only a drop: growth is
# never suspicious here, and a threshold that fires on growth would have
# blocked every real improvement this month (2,273 -> 4,033 -> 4,199).
MAX_DROP = 0.25          # postings, day over day
MAX_HIRING_DROP = 0.40   # companies showing at least one opening
MAX_UNREADABLE_LOSS = 0.05  # postings lost to boards that would not read


class StaleData(Exception):
    pass


def previous_snapshot() -> tuple[str, int] | None:
    """The strongest recent snapshot BEFORE today's, as (date, count).

    Comparing against yesterday alone let the gate disarm itself: a broken
    fetcher's collapsed snapshot still landed in history, so day two compared
    broken-with-broken and published the broken board. Comparing against the
    BEST of the last week means a collapse stays blocked until the numbers
    actually recover or a person looks and forces it - which is the entire
    point of having a gate.
    """
    snaps = sorted((ROOT / "data" / "history").glob("*.json"))
    if len(snaps) < 2:
        return None
    best = None
    for sp in snaps[-8:-1]:
        d = json.loads(sp.read_text())
        n = len(d.get("ids", []))
        if best is None or n > best[1]:
            best = (d.get("date", sp.stem), n)
    return best


def previous_hiring() -> int | None:
    """Most companies-with-an-opening in the last week, before today.

    Same best-of-week rule as previous_snapshot() and for the same reason: a
    broken run's collapsed snapshot lands in history too, so comparing against
    yesterday alone lets one bad day disarm the gate for the next.

    Returns None while no earlier snapshot carries the field. Snapshots written
    before 2026-08-29 have no `hiring` key, so this leg stays inert for about a
    week and then arms itself. That is honest: a gate with no baseline cannot
    tell a collapse from a first run, and inventing a baseline would be the
    kind of made-up number this project refuses.
    """
    snaps = sorted((ROOT / "data" / "history").glob("*.json"))
    if len(snaps) < 2:
        return None
    best = None
    for sp in snaps[-8:-1]:
        try:
            n = json.loads(sp.read_text()).get("hiring")
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(n, int) and (best is None or n > best):
            best = n
    return best


def sanity_check(board: dict) -> list[str]:
    """Reasons this board should not be published. Empty means go."""
    bad = []
    postings = len(board.get("postings", []))
    hiring = sum(1 for o in board.get("organizations", []) if o.get("open_roles"))

    if postings == 0:
        bad.append("the board has no postings at all")
    if hiring == 0:
        bad.append("no company shows a single opening")

    prev = previous_snapshot()
    if prev is None:
        # Nothing to compare against is not a failure, it is a first run.
        return bad
    prev_date, prev_n = prev
    if prev_n and postings < prev_n * (1 - MAX_DROP):
        drop = (1 - postings / prev_n) * 100
        bad.append(f"postings fell {drop:.0f}% since {prev_date} "
                   f"({prev_n} -> {postings}), past the {MAX_DROP:.0%} limit")

    # THE CLIFF THE PERCENTAGE CANNOT SEE. On 2026-08-26 the board fell 13.3%
    # - 4,334 postings to 3,757 - and this gate published it, because 13.3% is
    # under the 25% limit. 524 of those postings belonged to 33 companies whose
    # boards had gone UNREADABLE, and three of the biggest (Civica 89, Career
    # TEAM 64, BibliU 51) read perfectly when retried by hand minutes later.
    # A transient fetch failure had zeroed them for the day.
    #
    # A whole-board percentage cannot see that: a few hundred postings spread
    # across thousands is noise at the aggregate and a cliff for the company it
    # happens to. The discriminator is not "fell to zero" - companies do close
    # every role, and the history shows 31 doing it in one ordinary day. It is
    # "fell to zero AND the board would not read". A company that genuinely
    # emptied its board returns an empty list; a broken fetch returns nothing
    # at all, and the difference is recorded.
    unreadable_ids = {o["id"] for o in board.get("organizations", [])
                      if o.get("unreadable")}
    if unreadable_ids and prev_n:
        # The BEST count each company has shown in the last week, not an
        # average - the same reason previous_snapshot() takes the best rather
        # than yesterday. An average lets a run that already broke drag the
        # baseline down and disarm the gate on the next one.
        snaps = sorted((ROOT / "data" / "history").glob("*.json"))
        was: dict[str, int] = {}
        for sp in snaps[-8:-1]:
            seen: dict[str, int] = {}
            for pid in json.loads(sp.read_text()).get("ids", []):
                cid = pid.split("::")[0]
                seen[cid] = seen.get(cid, 0) + 1
            for cid, n in seen.items():
                was[cid] = max(was.get(cid, 0), n)
        now_by: dict[str, int] = {}
        for post in board.get("postings", []):
            now_by[post["company_id"]] = now_by.get(post["company_id"], 0) + 1
        lost = sum(max(0, was.get(i, 0) - now_by.get(i, 0)) for i in unreadable_ids)
        if lost > prev_n * MAX_UNREADABLE_LOSS:
            bad.append(f"{len(unreadable_ids)} board(s) would not read this run "
                       f"and about {lost} posting(s) went with them, "
                       f"{lost / prev_n:.0%} of the board. A board that will not "
                       f"answer is not a company with no jobs - retry before "
                       f"publishing a zero for each of them")

    # A big fall in companies-with-openings usually means a fetcher broke
    # rather than a market that emptied overnight. It is a separate question
    # from the posting count above: one large board growing can hold the total
    # up while a fetcher quietly drops fifty companies to zero.
    was = previous_hiring()
    if was and hiring < was * (1 - MAX_HIRING_DROP):
        bad.append(f"companies with an opening fell from {was} to {hiring}, "
                   f"past the {MAX_HIRING_DROP:.0%} limit. A fetcher breaking "
                   f"looks exactly like this; a market emptying overnight does "
                   f"not")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--force", action="store_true",
                    help="publish even if the sanity gate objects. For when you "
                         "have looked and the drop is real.")
    ap.add_argument("--check-only", action="store_true",
                    help="run the gate and exit; write nothing")
    a = ap.parse_args()

    board_src = json.loads((ROOT / "data" / "board.json").read_text())
    objections = sanity_check(board_src)
    if objections:
        print("the sanity gate is refusing to publish this board:", file=sys.stderr)
        for o in objections:
            print(f"  - {o}", file=sys.stderr)
        if not a.force:
            print("\nA board that shrinks overnight is usually a broken fetcher, not a\n"
                  "market that emptied. Look at the run first. If the drop is real,\n"
                  "re-run with --force.", file=sys.stderr)
            return 1
        print("  (--force given, publishing anyway)", file=sys.stderr)
    else:
        prev = previous_snapshot()
        if prev:
            print(f"sanity gate: {len(board_src['postings'])} postings against "
                  f"{prev[1]} on {prev[0]}, within limits")
    if a.check_only:
        return 0

    out = ROOT / a.out
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    for name in SHIP:
        shutil.copy2(ROOT / name, out / name)

    # the mascot: favicon, hero and the four expression heads
    mascot = ROOT / "assets" / "mascot"
    if mascot.exists():
        shutil.copytree(mascot, out / "assets" / "mascot")

    # logos are public by nature - they are the companies' own marks, served
    # from our origin so no visitor is reported to a logo service
    logos = ROOT / "assets" / "logos"
    if logos.exists():
        shutil.copytree(logos, out / "assets" / "logos")

    build_admin_bundle(out)

    board, stripped = sanitize(board_src)
    # separators: the site is served gzipped, but 300KB of whitespace is still
    # 300KB the browser has to parse.
    (out / "data" / "board.json").write_text(
        json.dumps(board, separators=(",", ":")))

    # The alerts page needs the sector names and nothing else. Without this it
    # would pull board.json - 4.7MB - to fill one dropdown on a settings page.
    # the name, tagline and palette, so a page never hardcodes them either
    shutil.copy2(ROOT / "data" / "brand.json", out / "data" / "brand.json")

    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    (out / "data" / "sectors.json").write_text(
        json.dumps([x["name"] for x in schema["sectors"]], separators=(",", ":")))

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {a.out}/: {len(SHIP)} page(s) + data/board.json")
    print(f"  {len(board['postings'])} postings, "
          f"{len(board['organizations'])} organizations")
    print(f"  {stripped} internal error string(s) replaced with the plain fact")
    print(f"  {size / 1e6:.2f} MB on disk, roughly {size / 1e6 * 0.1:.2f} MB over the wire")

    # Say what was deliberately left behind, so the omission is visible rather
    # than assumed.
    left = sorted(p.name for p in (ROOT / "data").iterdir()
                  if p.name != "board.json")
    print(f"\nnot shipped: {', '.join(left)}")
    print("also not shipped: scripts/, .github/, CLAUDE.md, admin.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
