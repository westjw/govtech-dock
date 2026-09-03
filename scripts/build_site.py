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
import datetime as dt
import html
import json
import math
import os
import pathlib
import re
import shutil
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Everything the public site is allowed to serve. Adding a file here is a
# deliberate act; nothing is included by walking a directory.
SHIP = ["index.html", "alerts.html", "claim.html"]


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


def _hunter_page(brand: dict) -> str:
    """The closed-beta holding page under /admin/hunter/. Access signs the
    person in; whoami says whether the owner granted them the beta."""
    name = html.escape(brand.get("name") or "SLED JOBS")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Job Hunter beta</title>
<style>body{{margin:0;font-family:Archivo,"Helvetica Neue",Arial,sans-serif;background:#E8F1F7;color:#1F2536}}
.band{{background:#1F2536;color:#E8F1F7;padding:12px 20px;font-weight:700}}.band a{{color:inherit;text-decoration:none}}
main{{max-width:640px;margin:40px auto;padding:0 20px;line-height:1.5}}h1{{font-size:28px;margin:0 0 8px}}
.kv{{color:#4B5A6B}}.in{{border-left:3px solid #F5A623;padding:10px 14px;background:#FAF7F0;margin:18px 0}}
a{{color:#0B57C4}}</style></head><body>
<div class="band"><a href="/">{name}</a> &middot; Job Hunter, closed beta</div>
<main><h1>Job Hunter</h1><p class="kv" id="who">Checking who you are&hellip;</p><div id="body"></div>
<p><a href="/">Back to the board</a> &middot; <a href="/admin/">Admin</a></p></main>
<script>
(async () => {{
  const who = document.getElementById("who"), body = document.getElementById("body");
  let me = {{signed_in: false}};
  try {{ const r = await fetch("/admin/api/whoami", {{credentials: "include"}}); if (r.ok) me = await r.json(); }} catch (e) {{}}
  if (!me.signed_in) {{ who.textContent = "You are not signed in."; return; }}
  const roles = me.roles || [];
  who.textContent = me.handle ? "Signed in as " + me.handle + "." : "Signed in, but not on the list yet.";
  if (roles.includes("hunter") || roles.includes("owner")) {{
    body.innerHTML = '<div class="in"><b>You are in the beta.</b> Job Hunter reads the roles on this board, scores them against what you have told it about yourself, and drafts nothing on its own: every application is yours to send. The tool runs on the desk today; this page is where the browser version will live, and you will hear from the owner when it does.</div>';
  }} else {{
    body.innerHTML = '<div class="in">The beta is closed for now. The owner grants access from the Users board; ask and this page will change.</div>';
  }}
}})();
</script></body></html>"""


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

    VERIFIED LIVE 2026-09-01. SHIP_ADMIN=1 is now set on the Pages project and
    the Access application exists: /admin, /admin/, /admin/index.html and
    /admin/data.json all answer 302 to a cloudflareaccess.com sign-in with
    auth_status NONE, unauthenticated. Recorded because the variable being set
    LOOKS like the old leak and reads as alarming on the settings page. Do not
    conclude the gate is defeated from the variable alone - re-run the probe.
    Note the account-wide "Access policy across your Workers" toggle is a
    DIFFERENT feature and is off; the per-application policy is what protects
    this, so that toggle's state proves nothing either way.
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
        # TWO MORE QUEUES, chosen because the answer is on the card and the
        # cost of being wrong is recoverable. Duplicates first: 70 pairs
        # holding up 136 rulings in other queues, and a merge keeps every field
        # the survivor has, so a wrong merge is undoable where a wrong scope
        # ruling is invisible. Founding year is one tap against a year that is
        # already on the row.
        #
        # Acquisitions is deliberately NOT here, and not on the phone either:
        # deciding whether a slug belongs to a parent needs slow reading, and a
        # fast grip buys speed with accuracy. CLAUDE.md excludes it from the
        # belt for the same reason.
        "duplicates": [_public_row(v)
                       for v in _admin.QUEUES["duplicates"](companies, board)],
        "founded": [_public_row(v)
                    for v in _admin.QUEUES["founded"](companies, board)][:400],
    }
    admin_dir = out / "admin"
    admin_dir.mkdir(parents=True, exist_ok=True)
    (admin_dir / "index.html").write_text((ROOT / "admin-web.html").read_text())
    # WHO MAY REACH WHAT, for the login endpoint. data/users.json carries
    # handles, roles and a hash of each address - never an address - and it
    # is served only behind the Access application like everything else
    # under /admin. functions/admin/api/whoami.js reads it.
    users_p = ROOT / "data" / "users.json"
    users = json.loads(users_p.read_text()) if users_p.exists() else {}
    for h, u in list(users.items()):
        if not isinstance(u, dict) or any("@" in str(v) for v in u.values()):
            raise SystemExit(f"users.json row {h!r} carries an address; refusing to ship it")
    (admin_dir / "users.json").write_text(json.dumps(users, indent=1))
    # THE CLOSED JOB HUNTER BETA, behind the same door. A holding page for
    # now: it says who is signed in and whether the owner has let them in.
    # The tool itself runs on the desk today; when a browser version exists
    # this is where it lives, and the Users board already decides who sees it.
    hunter = admin_dir / "hunter"
    hunter.mkdir(parents=True, exist_ok=True)
    brand = json.loads((ROOT / "data" / "brand.json").read_text())
    (hunter / "index.html").write_text(_hunter_page(brand))
    (admin_dir / "data.json").write_text(json.dumps(payload))
    print(f"  admin bundle: {len(payload['vendors'])} vendors, "
          f"{len(payload['miscategorized'])} wrong-bucket, "
          f"{len(payload['duplicates'])} duplicate pairs, "
          f"{len(payload['founded'])} founding years")

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


def _reg_host(url: str | None) -> str:
    """example.com from https://www.example.com/x - two labels, lowercased."""
    h = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    h = h[4:] if h.startswith("www.") else h
    bits = h.split(".")
    return ".".join(bits[-2:]) if len(bits) >= 2 else h


def has_static_page(o: dict) -> bool:
    """One answer, used by the page writer, the meta index and the sitemap.

    Three places used to ask "open_roles?" separately; the day one of them
    grew a second condition the other two would have gone stale, and a
    sitemap listing a page that was never written is a 404 submitted to
    Google as canonical. A company gets a page when it has something a
    crawler cannot get from the app: open roles, a sourced write-up, or a
    researched shortlist.
    """
    prof = o.get("profile") if isinstance(o.get("profile"), dict) else None
    return bool(o.get("open_roles") or (prof and prof.get("paragraphs"))
                or o.get("competitors"))


def write_meta_index(out: pathlib.Path, board: dict) -> dict:
    """The small file the middleware reads to title a page.

    A Worker cannot parse a 6MB board on every request, so this is the
    smallest thing that answers "what is at this address": id to title,
    company, place and count. Roles and companies only, because those are the
    two addresses that name one specific thing.

    Nothing here is invented. A posting with no office prints no place, and a
    company with no opening prints no count, because the middleware's job is
    to describe the page and a description that guesses is worse than a
    generic one.
    """
    roles, cos = {}, {}
    for p_ in board.get("postings", []):
        off = p_.get("office") or {}
        where = off.get("city") or off.get("state") or ""
        r = {"t": p_.get("title") or "", "c": p_.get("company") or "", "w": where}
        # Structured data ONLY where the description was actually read. 2,711
        # of 4,203 postings were; the rest are a title and a link, and
        # publishing JobPosting markup for those would assert to Google a
        # completeness we do not have. city/state ride along separately
        # because JobPosting wants them apart, and validThrough is never
        # emitted: we do not know when a posting expires and inventing an
        # expiry is how a board ends up advertising dead roles.
        if p_.get("jd_seen"):
            r["ld"] = 1
            # datePosted IS THE EMPLOYER'S DATE OR IT IS ABSENT.
            #
            # UPDATED 2026-09-01: ats.py now reads a publish date from all
            # seven structured boards - first_published, publishedAt,
            # createdAt, releasedDate, published_on, published_at,
            # published_date - and it rides through as `posted`. Where a board
            # publishes one, `pd` below carries it and the Worker emits
            # datePosted. Where it does not, the field is still withheld
            # entirely. The paragraphs below are why it can never be filled
            # from our own date, and they still hold.
            #
            # This emitted first_seen, which is the day THIS BOARD first saw
            # the row. 2,183 of 3,524 structured blocks claimed 2026-08-18 or
            # 2026-08-19 - the first two crawls - as the day the employer
            # posted the job. A role advertised since spring read as posted the
            # morning we started looking.
            #
            # index.html already refuses to make this exact claim to a human,
            # in these words: "first_seen is our crawl date... Saying
            # 'appeared' would file our crawl date as a fact about somebody's
            # hiring, which is the same species of claim as reporting a page we
            # could not read as 'no jobs here'." The page told the truth to a
            # reader and told Google the other thing.
            #
            # Before that change nothing read a posted date at all, so the
            # only date on hand was ours. Where a board still gives none the
            # field is withheld entirely
            # does with validThrough and baseSalary for the same reason.
            # datePosted is optional in Google's JobPosting spec; a wrong one
            # is not.
            #
            # AND THE SAME RULE FOR LOCATION, which is NOT optional.
            #
            # 2,083 of 3,524 blocks carried neither a city nor a state, because
            # roles.geography() could not put the posting anywhere. A
            # JobPosting without jobLocation and without jobLocationType is
            # invalid, so 59% of this board's structured data was being
            # published as a claim no aggregator can accept.
            #
            # 533 of those are work_mode `remote`, read verbatim off the
            # posting, and the spec has a field for exactly that. The remaining
            # 1,550 say nothing we can express: not stated, or hybrid with no
            # parsed office.
            #
            # The tempting fix is to feed the raw `location` string in. It
            # would be wrong for the reason CLAUDE.md's CITY_CASES exists: the
            # 1,441 blocks that DO carry a location assert
            # addressCountry: "US" only where a real US state parsed, and
            # pushing raw text through would stamp US onto "Montreal",
            # "Newcastle upon Tyne" and "Australia - Remote". Two capitals are
            # not a US state.
            #
            # So: TELECOMMUTE where the posting says remote, and no structured
            # block at all for the rest. They keep their title, description and
            # canonical - a page a person can read and a crawler can index -
            # and simply stop making a job claim we cannot complete.
            if off.get("city"):
                r["ci"] = off["city"]
            if off.get("state"):
                r["st"] = off["state"]
            if not (r.get("ci") or r.get("st")):
                if p_.get("work_mode") == "remote":
                    r["tc"] = 1          # jobLocationType: TELECOMMUTE
                else:
                    r.pop("ld", None)    # no location we can state: no block
            # THE EMPLOYER'S OWN DATE, now that the board has one. Shipped
            # only when the board we read published it - `posted` is absent on
            # every row where it did not, and there is no fallback. See the
            # note above for why first_seen can never fill this.
            if p_.get("posted"):
                r["pd"] = p_["posted"]
        roles[p_["id"]] = r
    for o in board.get("organizations", []):
        cos[o["id"]] = {"n": o.get("name") or "", "s": o.get("sector") or "",
                        "d": (o.get("description") or "")[:180],
                        "r": o.get("open_roles") or 0,
                        # p: a static /c/ page exists, so the canonical may
                        # point there. w: the registrable website host, which
                        # the claim Function needs and cannot read from
                        # companies.json. Both omitted when false/absent, so
                        # the file does not grow a byte for the 1,700 that
                        # carry neither.
                        **({"p": 1} if has_static_page(o) else {}),
                        **({"w": _reg_host(o.get("website"))} if _reg_host(o.get("website")) else {})}
    # TWO FILES, not one. A role page has no use for 2,113 company records and
    # a company page has none for 4,475 postings; one combined index made the
    # Worker pull 923KB to write a single <title>. Split, each request fetches
    # only the half it can use, and the Worker caches it at the edge so the
    # cost is one fetch per edge per deploy rather than one per visitor.
    gen = board.get("generated")
    (out / "meta-roles.json").write_text(
        json.dumps({"generated": gen, "roles": roles}, separators=(",", ":")))
    (out / "meta-companies.json").write_text(
        json.dumps({"generated": gen, "companies": cos}, separators=(",", ":")))
    return {"roles": len(roles), "companies": len(cos)}


def _default_css(brand: dict) -> str:
    """The stylesheet of the state and conference pages, in the brand's own
    tokens. The company page brings its own (COPAGE_CSS)."""
    p_ = brand["palette"]
    return f"""
 :root{{--bg:{p_['ice']['hex']};--panel:{p_['belly']['hex']};--ink:{p_['penguin']['hex']};
   --line:{p_['frost']['hex']};--dim:{brand['derived']['deep_fog']['hex']};
   --link:{p_['badge']['hex']};--beak:{p_['beak']['hex']}}}
 @media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:{p_['penguin']['hex']};--panel:#262E42;
   --ink:{p_['ice']['hex']};--line:#39435C;--dim:#A8BCCA;
   --link:{brand['derived']['dark']['badge']['hex']}}}}}
 :root[data-theme=dark]{{--bg:{p_['penguin']['hex']};--panel:#262E42;
   --ink:{p_['ice']['hex']};--line:#39435C;--dim:#A8BCCA;
   --link:{brand['derived']['dark']['badge']['hex']}}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--ink);
   font:16px/1.6 Archivo,system-ui,sans-serif}}
 .band{{background:{p_['penguin']['hex']};color:{p_['ice']['hex']};
   border-bottom:3px solid var(--beak);padding:14px 22px}}
 .band a{{color:{p_['ice']['hex']};text-decoration:none;font-weight:800;
   letter-spacing:.01em}}
 main{{max-width:74ch;margin:0 auto;padding:28px 22px 70px}}
 h1{{font-size:30px;font-weight:800;letter-spacing:-.02em;margin:0 0 6px;
   text-wrap:balance}}
 h2{{font-size:18px;font-weight:800;margin:30px 0 8px}}
 .kv{{color:var(--dim);font-size:14px;margin:0 0 18px}}
 p{{margin:0 0 14px}}
 ul{{padding-left:0;list-style:none;margin:0}}
 li{{border-bottom:1px solid var(--line);padding:10px 0}}
 li:last-child{{border-bottom:0}}
 .role{{font-weight:600}}
 .meta{{color:var(--dim);font-size:13.5px}}
 a{{color:var(--link)}}
 .note{{background:var(--panel);border:1px solid var(--line);padding:12px 14px;
   font-size:14px;color:var(--dim);margin:18px 0}}
 .cta{{display:inline-block;margin-top:8px;font-weight:600}}
"""


def _page(title: str, desc: str, canonical: str, body: str, brand: dict,
          og: str = "home", css: "str | None" = None, wrap: bool = True) -> str:
    """One no-JS page, in the brand's own tokens.

    Deliberately not a copy of index.html: this is what a crawler, a
    link-unfurler and a reader with JavaScript off actually get, so it carries
    the facts in the HTML rather than fetching them. It links INTO the app for
    anyone who wants filters.

    `css` is the page's whole stylesheet and `wrap` says whether the body goes
    inside the default <main> measure. The company page brings its own of
    both: it is the app's .copage layout, which lays out its own columns and
    must not sit inside a 74ch column.
    """
    css = _default_css(brand) if css is None else css
    main = f"<main>{body}</main>" if wrap else body
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{html.escape(canonical)}">
<link rel="icon" href="/assets/mascot/svg/favicon.svg" type="image/svg+xml">
<meta property="og:type" content="website">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:url" content="{html.escape(canonical)}">
<meta property="og:image" content="{brand['site']}/assets/og/{og}.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&display=swap">
<style>{css}</style></head><body>
<div class="band"><a href="/">{html.escape(brand['name'])}</a></div>
{main}
</body></html>
"""


# ------------------------------------------------------------ company page
# THE STATIC COMPANY PAGE IS THE APP'S COMPANY PAGE. A visitor from a search
# result lands on /c/<id>.html; a visitor inside the app opens ?co=<id> and
# gets co() in index.html. For a while those were two different pages - the
# static one a 74ch column of lists written before the redesign - and the
# same company read as two different products depending on the door. What
# follows is co() ported line for line: the same sections in the same order,
# the same class names, the same copy per state, the same breakpoints, the
# same dark override. Anything the app does on the client - save, expand a
# group, push history - becomes a plain link into the app or is left out.
#
# EVERYTHING HERE IS COMPUTED FROM THE RECORD AND THE POSTINGS, never typed
# in. The phase cell in particular: the app reads it from this company's
# postings against today's date, and the honest answer for every company on
# a board whose history began 2026-08-18 is "not enough history to read".
# That is what this prints, by running the same arithmetic against the
# board's own generated date, and not by hard-coding the sentence - the day
# the record is 60 days deep the page starts saying something else on its own.

FAM = {"gtm": "GTM", "cs": "Customer Success", "ops": "Ops / RevOps",
       "engineering": "Engineering", "product": "Product & Design",
       "data": "Data & Research", "policy": "Policy & Gov Affairs",
       "ga": "G&A", "exec": "Executive", "field": "Field & implementation",
       "other": "Unclassified"}
SALES_FAMILIES = {"gtm", "field"}
# Verkada alone carries 247 openings and Motorola 354, which is more list
# than anybody reads to the end of. The cut is by OPENING, and the page says
# where the rest are.
CO_ROLE_CAP = 40
PAY_PERIOD = {"year": "", "month": "a month", "week": "a week",
              "day": "a day", "hour": "an hour"}

# The app's .copage stylesheet (index.html), carried whole so the page stands
# alone. One edit: the app's negative margins cancel its own <main> padding;
# there is no such padding here, so the block's margin is 0. The tokens the
# page consumes from the site sheet (--accent for .pay, --faint for .paynone,
# --beak for the band, the fonts, --radius) are defined on :root above it.
# Both dark forms are defined - the media query for a reader whose system is
# dark, and [data-theme=dark] for an explicit choice - because a token with
# one definition is a token that is wrong in one of the two themes.
COPAGE_CSS = """
 :root{--font-heading:"Archivo",system-ui,sans-serif;--font-body:"Archivo",system-ui,sans-serif;--radius:0px}
 :root{--bg:#E8F1F7;--panel:#FAF7F0;--line:#C9DCE8;--ink:#1F2536;--dim:#556F82;
   --faint:#7C97AA;--accent:#0B57C4;--warn:#C1341F;--bad:#C1341F;--beak:#F5A623;--chip:#DCE9F1}
 @media (prefers-color-scheme:dark){:root:not([data-theme=light]){
   --bg:#1F2536;--panel:#262E42;--line:#39435C;--ink:#E8F1F7;--dim:#A8BCCA;
   --faint:#7C97AA;--accent:#478EF5;--warn:#E46855;--bad:#E46855;--beak:#F5A623;--chip:#2E3852}}
 :root[data-theme=dark]{
   --bg:#1F2536;--panel:#262E42;--line:#39435C;--ink:#E8F1F7;--dim:#A8BCCA;
   --faint:#7C97AA;--accent:#478EF5;--warn:#E46855;--bad:#E46855;--beak:#F5A623;--chip:#2E3852}
 :root{--hdr-bg:#1F2536;--hdr-ink:#E8F1F7;--hdr-mute:#9FB3C4;--hdr-line:#39435C}
 *{box-sizing:border-box}
 button,input,select,textarea,dialog,img,code{border-radius:var(--radius)}
 body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 var(--font-body)}
 h1,h2,h3,h4{font-family:var(--font-heading);font-weight:800;letter-spacing:-.02em;line-height:1.12;margin:0}
 a{color:var(--accent);text-underline-offset:2px}
 .band{background:var(--hdr-bg);color:var(--hdr-ink);border-bottom:3px solid var(--beak);padding:14px 22px}
 .band a{color:var(--hdr-ink);text-decoration:none;font-weight:800;letter-spacing:.01em}
 .pay{font-variant-numeric:tabular-nums}
 .pay{color:var(--accent);font-weight:600;white-space:nowrap}
 .paynone{color:var(--faint)}
 .copage{--c-bg:#FAF7F0;--c-rule:#C9DCE8;--c-stroke:#1F2536;--c-ink:#1F2536;--c-ink2:#556F82;--c-ink3:#7C97AA;--c-accent:#C1341F;--c-accent-text:#C1341F;--c-link:#0B57C4}
 @media (prefers-color-scheme:dark){:root:not([data-theme=light]) .copage{--c-bg:#151B29;--c-rule:#2E3A50;--c-stroke:#4A5C71;--c-ink:#E8F1F7;--c-ink2:#A9C3D4;--c-ink3:#7C97AA;--c-accent:#C1341F;--c-accent-text:#E4634A;--c-link:#9CC3FF}}
 :root[data-theme=dark] .copage{--c-bg:#151B29;--c-rule:#2E3A50;--c-stroke:#4A5C71;--c-ink:#E8F1F7;--c-ink2:#A9C3D4;--c-ink3:#7C97AA;--c-accent:#C1341F;--c-accent-text:#E4634A;--c-link:#9CC3FF}
 .copage{background:var(--c-bg);color:var(--c-ink);margin:0;padding:0 20px 48px;min-height:70vh}
 .copage a{color:var(--c-link)}
 .cowrap{max-width:1000px;margin:0 auto}
 .cocrumb{font-size:11px;line-height:1.5;color:var(--c-ink3);padding:14px 0 10px;font-variant-numeric:tabular-nums}
 .cocrumb a{color:var(--c-ink2);text-decoration:none}
 .cocrumb a:hover{text-decoration:underline}
 .cocrumb .sep{padding:0 7px;color:var(--c-rule)}
 .coid{display:flex;gap:16px;align-items:flex-start;padding:2px 0 18px}
 .coid .logo{width:56px;height:56px;flex:none;display:grid;place-items:center;font:800 22px/1 var(--font-heading);background:#E8F1F7;color:#1F2536}
 .coid h1{font-size:30px;line-height:1;letter-spacing:-.025em;margin:0 0 8px}
 .cometa{font-size:11px;line-height:1.5;color:var(--c-ink3);font-variant-numeric:tabular-nums}
 .cometa .sep{padding:0 8px;color:var(--c-rule)}
 .cometa a{color:var(--c-ink2)}
 .coacts{margin-left:auto;display:flex;gap:8px;flex:none}
 .cobtn{font:800 11px/1 var(--font-heading);padding:10px 14px;cursor:pointer;border:1px solid var(--c-stroke);background:none;color:var(--c-ink);text-decoration:none;display:inline-block}
 .cobtn.fill{background:var(--c-accent);border-color:var(--c-accent);color:#FAF7F0}
 .cobtn.on{background:none;border-color:var(--c-stroke);color:var(--c-ink2)}
 .cobtn:focus-visible{outline:2px solid var(--c-accent-text);outline-offset:2px}
 .costrip{display:flex;border-top:2px solid var(--c-rule);border-bottom:2px solid var(--c-rule);margin:0 0 30px}
 .costrip>dl{padding:18px 0 16px;padding-right:22px;margin:0;flex:1 1 0}
 .costrip>dl+dl{border-left:1.5px solid var(--c-rule);padding-left:22px}
 .costrip>dl.wide{flex:1.3 1 0}
 .costrip>dl.src{flex:1.1 1 0}
 .costrip .v{font:800 34px/1 var(--font-heading);letter-spacing:-.02em;font-variant-numeric:tabular-nums}
 .costrip .v.txt{font-size:17px;line-height:1.2;letter-spacing:-.01em}
 .costrip .v.dim{color:var(--c-ink3)}
 .costrip .v.acc{color:var(--c-accent-text)}
 .costrip dt{font:800 9.5px/1 var(--font-heading);letter-spacing:.16em;text-transform:uppercase;color:var(--c-ink3);margin:10px 0 7px}
 .costrip dd{margin:0;font-size:11px;line-height:1.5;color:var(--c-ink2);font-variant-numeric:tabular-nums}
 .cobody{display:flex;gap:40px;align-items:flex-start}
 .cocol{flex:1;min-width:0}
 .corail{width:262px;flex:none}
 .cosec{margin:0 0 30px}
 .cosec>h2{font:800 15px/1 var(--font-heading);display:inline}
 .cosec .smeta{font-size:11px;color:var(--c-ink3);padding-left:10px}
 .cosechd{display:flex;align-items:baseline;gap:0;border-bottom:1px solid var(--c-rule);padding-bottom:9px;margin-bottom:14px}
 .coabout p{font-size:13px;line-height:1.65;max-width:560px;margin:0 0 13px;color:var(--c-ink)}
 .coabout .more{color:var(--c-ink3)}
 .coquote{border-left:1.5px solid var(--c-rule);padding-left:14px;margin:16px 0 0;font-style:italic;font-size:13px;line-height:1.6;color:var(--c-ink2);max-width:560px}
 .coquote .src{font-style:normal;font-size:11px;color:var(--c-ink3)}
 .cogrp{margin:0 0 14px}
 .cogrph{display:flex;align-items:baseline;gap:10px;padding:9px 0;border-bottom:1px solid var(--c-rule)}
 .cogrph h3{font:800 13px/1 var(--font-heading)}
 .cogrph .n{font-size:11px;color:var(--c-ink3);margin-left:auto}
 .corow{display:flex;align-items:baseline;padding:9px 0;border-bottom:1px solid var(--c-rule);font-size:12.5px;line-height:1.5}
 .corow .ti{flex:1;min-width:0;font-weight:800}
 .corow .lo{width:120px;flex:none;color:var(--c-ink2);font-size:11px}
 .corow .pa{width:118px;flex:none;color:var(--c-ink2);font-size:11px;font-variant-numeric:tabular-nums}
 .corow .ag{width:52px;flex:none;color:var(--c-ink3);font-size:11px;font-variant-numeric:tabular-nums}
 .corow .sv{width:46px;flex:none;text-align:right}
 .comore{font-size:11px;padding:9px 0;display:inline-block}
 .coempty{display:flex;gap:20px;align-items:flex-start;padding:22px 0 4px}
 .coempty img{width:96px;flex:none;opacity:.85}
 .coempty h3{font:800 15px/1.2 var(--font-heading);margin:0 0 8px}
 .coempty p{font-size:12.5px;line-height:1.6;color:var(--c-ink2);max-width:52ch;margin:0 0 14px}
 .corail section{margin:0 0 26px}
 .corail h2{font:800 9.5px/1.35 var(--font-heading);letter-spacing:.16em;text-transform:uppercase;color:var(--c-ink3);border-bottom:1px solid var(--c-rule);padding-bottom:8px;margin-bottom:4px}
 .corail .r{display:flex;align-items:baseline;gap:8px;padding:9px 0;border-bottom:1px solid var(--c-rule);font-size:12.5px;line-height:1.45}
 .corail .r .d{display:block;font-size:11px;color:var(--c-ink3);margin-top:3px}
 .corail .r .n{margin-left:auto;font-size:11px;color:var(--c-ink2);font-variant-numeric:tabular-nums;flex:none}
 .corail .tag{margin-left:auto;font:800 9.5px/1 var(--font-heading);letter-spacing:.14em;text-transform:uppercase;color:var(--c-accent-text);flex:none}
 .corail .note{font-size:11px;line-height:1.5;color:var(--c-ink3);padding-top:9px}
 .corail .all{font-size:11px;padding-top:9px;display:inline-block}
 .coprov{font-size:10.5px;line-height:1.5;color:var(--c-ink3)}
 .cofoot{padding-top:10px;border-top:1px solid var(--c-rule)}
 @media (max-width:1080px){.corail{width:220px}}
 @media (max-width:900px){
   .cobody{display:block}
   .corail{width:auto;margin-top:34px}
   .costrip{flex-wrap:wrap}
   .costrip>dl{flex:1 1 46%!important}
   .costrip>dl:nth-child(3){border-left:0;padding-left:0}
   .corow{flex-wrap:wrap}
   .corow .ti{flex:1 1 100%}
   .corow .lo{width:auto;order:3;flex:1 1 60%}
   .coid{flex-wrap:wrap}
   .coid>div[style]{flex:1 1 60%}
   .coacts{margin-left:0;flex:1 1 100%;order:3;padding-top:4px}
   .coacts .cobtn{flex:1 1 0;min-height:44px;display:flex;align-items:center;justify-content:center}
 }
 @media (max-width:620px){
   .costrip>dl{flex:1 1 46%!important;padding-right:14px}
   .costrip>dl:nth-child(3),.costrip>dl:nth-child(4){flex:1 1 100%!important;border-left:0;padding-left:0}
   .costrip>dl:nth-child(3){border-top:1.5px solid var(--c-rule);margin-top:2px;padding-top:16px}
   .coid .logo{width:44px;height:44px}
   .coid h1{font-size:24px}
 }
"""


def _co_now(board: dict) -> dt.date:
    """The day the board was generated, standing in for the app's Date.now().
    The build runs nightly, so the two agree to within the day; and a page
    dated by its own data is reproducible, which a page dated by the clock
    of whichever machine built it is not."""
    try:
        return dt.date.fromisoformat(str(board.get("generated") or "")[:10])
    except ValueError:
        return dt.date.today()


def _co_date(iso) -> "dt.date | None":
    try:
        return dt.date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return None


def _co_roles(posts: list, since: "dt.date | None" = None) -> set:
    """Distinct openings, optionally only those first read on or after
    `since`. Openings, not rows: a requisition in forty cities is one."""
    out = set()
    for p in posts:
        if since is not None:
            t = _co_date(p.get("first_seen"))
            if not t or t < since:
                continue
        out.add(p.get("opening_id") or p.get("id"))
    return out


def _co_record_days(mine: list, now: dt.date) -> "int | None":
    """How far back the record goes for this company, in days, or None when
    nothing here carries a date. A window longer than the record measures
    us, not them - see coRecordDays in index.html."""
    first = None
    for p in mine:
        t = _co_date(p.get("first_seen"))
        if t and (first is None or t < first):
            first = t
    return None if first is None else (now - first).days


def _co_open_note(mine: list, open_: int, readable: bool, now: dt.date) -> str:
    if not open_:
        return "none seen recently" if readable else "not measured"
    span = _co_record_days(mine, now)
    if span is not None and span < 30:
        when = "today" if span == 0 else f"{span} day{'' if span == 1 else 's'} ago"
        return f"first read here {when}"
    n = len(_co_roles(mine, now - dt.timedelta(days=30)))
    return f"+{n} first read in the last 30 days" if n else "none added in 30 days"


def _co_phase(mine: list, readable: bool, now: dt.date) -> dict:
    """{value, tone, note} for the hiring-phase cell - coPhase, ported.

    A board nobody could read has no phase, not a quiet one. And the record
    has to span the window before a trend read over it means anything: 60
    days of postings out of 15 days of watching is our start date wearing
    their hiring's name."""
    if not readable:
        return {"value": "Not measured", "tone": "dim",
                "note": "their board could not be read, so there is nothing "
                        "here to read a phase from"}
    d60 = now - dt.timedelta(days=60)
    fresh = []
    for p in mine:
        t = _co_date(p.get("first_seen"))
        if t and t >= d60:
            fresh.append(p)
    roles = len(_co_roles(fresh))
    span = _co_record_days(mine, now)
    if span is not None and span < 60:
        return {"value": "Not enough history to read", "tone": "dim",
                "note": f"we have only been reading this board for {span} "
                        f"day{'' if span == 1 else 's'}, and a 60-day phase "
                        f"needs 60"}
    if roles < 3:
        return {"value": "Too few openings to read", "tone": "dim",
                "note": "we need 3+ roles over 60 days to call it"}
    q = len(_co_roles([p for p in fresh if p.get("quota_carrying")]))
    places = set()
    for p in fresh:
        off = p.get("office")
        if isinstance(off, dict) and off.get("state"):
            places.add(off["state"])
            continue
        ter = p.get("territory")
        if isinstance(ter, dict) and ter.get("stated"):
            if ter.get("states"):
                places.update(ter["states"])
            elif ter.get("region"):
                places.add(ter["region"])
            continue
        if p.get("location"):
            places.add(p["location"])
    ev = (f"{roles} role{'' if roles == 1 else 's'} opened in 60 days"
          + (f" · {len(places)} regions" if len(places) > 1 else ""))
    if q >= 3 and q / roles >= .5:
        return {"value": "Building a sales floor", "tone": "acc", "note": ev}
    if len(places) >= 3:
        return {"value": "Regional push", "tone": "acc", "note": ev}
    return {"value": "Steady backfill", "tone": "acc", "note": ev}


def _co_age(iso, now: dt.date) -> str:
    t = _co_date(iso)
    if not t:
        return ""
    d = (now - t).days
    if d < 1:
        return "today"
    if d < 30:
        return f"{d}d"
    return f"{t:%b} {t.day}"


def _loc_cell(p: dict) -> str:
    """Where a posting is, in the board's own vocabulary - locCell, ported.
    One divergence: an office with a city and no state prints the city. The
    app concatenates the null and prints "London, null"; 341 postings on the
    board are shaped that way."""
    if p.get("work_mode") == "remote":
        return "remote"
    off = p.get("office")
    if isinstance(off, dict) and (off.get("city") or off.get("state")):
        parts = [x for x in (off.get("city"), off.get("state")) if x]
        return ", ".join(parts) + (" (hybrid)" if p.get("work_mode") == "hybrid" else "")
    ter = p.get("territory")
    if isinstance(ter, dict) and ter.get("stated"):
        states = ter.get("states") or []
        if states:
            head = ", ".join(states[:3]) + (f" +{len(states) - 3}" if len(states) > 3 else "")
        else:
            head = ter.get("region") or ""
        return f"{head} (territory)"
    if p.get("location"):
        return str(p["location"])
    return "not stated"


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _money(n, cur, cents: bool, use_k: bool) -> str:
    if cents:
        s = f"{n:.2f}"
    elif use_k and n >= 1000 and n % 1000 == 0:
        s = f"{int(n // 1000)}k"
    else:
        s = f"{int(math.floor(n + 0.5)):,}"
    return "$" + s if cur == "USD" else s


def _pay_text(c) -> str:
    """$140k, $142,500, $67.50 - how a person writes it, and never a number
    the posting did not state. payText, ported."""
    if not isinstance(c, dict):
        return ""
    lo = c.get("min") if _is_num(c.get("min")) else None
    hi = c.get("max") if _is_num(c.get("max")) else None
    if lo is None and hi is None:
        return ""
    cents = any(v is not None and round(v) != v for v in (lo, hi))
    use_k = c.get("period") == "year" or not c.get("period")
    cur = c.get("currency")
    code = f"{cur} " if cur and cur != "USD" else ""
    f = lambda v: _money(v, cur, cents, use_k)  # noqa: E731
    per = PAY_PERIOD.get(c.get("period") or "", "")
    if lo is not None and hi is not None and lo != hi:
        core = f"{code}{f(lo)} – {f(hi)}"
    elif lo is not None and hi is not None:
        core = f"{code}{f(lo)}"
    elif lo is not None:
        core = f"from {code}{f(lo)}"
    else:
        core = f"up to {code}{f(hi)}"
    return core + (f" {per}" if per else "")


def _pay_bit(p: dict) -> "tuple | None":
    """(class, text), or None when this build recorded nothing either way.
    The two silences are worded differently on purpose."""
    if "comp" not in p:
        return None
    t = _pay_text(p.get("comp"))
    if t:
        return ("pay", t)
    if p.get("jd_seen"):
        return ("paynone", "no salary stated")
    return ("paynone", "no description to read" if p.get("source") == "manual"
            else "we could not read this posting")


def _pay_cell(p: dict) -> str:
    b = _pay_bit(p)
    if b and b[0] == "pay":
        return f'<span class="pay">{html.escape(b[1])}</span>'
    why = b[1] if b else "this build of the board recorded no pay either way"
    return f'<span class="paynone" title="{html.escape(why)}">&mdash;</span>'


def _safe_url(u) -> str:
    """http(s) and nothing else. A url out of an ATS is a url out of a
    stranger, and this page is served to strangers."""
    s = str(u or "").strip()
    if not s:
        return ""
    try:
        parts = urllib.parse.urlsplit(s)
    except ValueError:
        return ""
    return s if parts.scheme in ("http", "https") and parts.netloc else ""


def _path_of(u) -> str:
    """"/about" for https://x.test/about/, "/" for the homepage, the raw
    string for anything that is not a url."""
    try:
        parts = urllib.parse.urlsplit(str(u))
    except ValueError:
        return str(u)
    if not (parts.scheme and parts.netloc):
        return str(u)
    pth = "" if parts.path == "/" else parts.path
    return pth.rstrip("/") or "/"


def _ext_link(url, text: str, cls: str = "") -> str:
    """An outbound link that opens in a new tab, or the plain text when the
    url is not one we would send a reader to."""
    safe = _safe_url(url)
    if not safe:
        return text
    c = f' class="{cls}"' if cls else ""
    return (f'<a{c} href="{html.escape(safe)}" target="_blank" '
            f'rel="nofollow noopener">{text}</a>')


def _co_href(target_id, by_id: dict) -> str:
    """Where a company link goes: its own static page when it has one, the
    app otherwise. The same rule the middleware uses for canonical, so a
    link on a static page never advertises a 404."""
    tid = str(target_id or "")
    if tid and tid in by_id and has_static_page(by_id[tid]):
        return f"/c/{urllib.parse.quote(tid, safe='')}.html"
    return f"/?co={urllib.parse.quote(tid, safe='')}"


def _co_about(o: dict, dom: str) -> str:
    """The About section - coAbout, ported. Keys on the SHAPE of profile,
    never on the key: a legacy profile is a reviewer's notes and renders as
    the one-line record, not as a write-up."""
    esc = html.escape
    pr = o.get("profile") if isinstance(o.get("profile"), dict) else None
    ready = bool(pr and isinstance(pr.get("paragraphs"), list) and pr["paragraphs"])
    desc = o.get("description") or ""
    lede = f"<p>{esc(desc)}{'.' if desc and desc[-1] not in '.!?' else ''}</p>"
    if not ready:
        return (f'<section class="cosec coabout">'
                f'<div class="cosechd"><h2>About</h2><span class="smeta">'
                f'{"researched by SLED JOBS" if o.get("researched") else "from the record"}'
                f'{" &middot; checked against " + esc(dom) if dom else ""}</span></div>'
                f'{lede}'
                f'<p class="more">This is the one-line record. The longer write-up the page is '
                f'built for &mdash; what they sell, who buys it, named customers &mdash; is '
                f'not on file for this company yet, so nothing stands in for it.</p>'
                f'</section>')
    srcs = pr.get("sources") if isinstance(pr.get("sources"), list) else []

    def num(u):
        for i, s in enumerate(srcs):
            if isinstance(s, dict) and s.get("url") == u:
                return f"<sup>{i + 1}</sup>"
        return ""
    psrc = pr.get("paragraph_sources") if isinstance(pr.get("paragraph_sources"), list) else []
    paras = ""
    for i, txt in enumerate(pr["paragraphs"]):
        us = list(dict.fromkeys(psrc[i] if i < len(psrc) and isinstance(psrc[i], list) else []))
        paras += f"<p>{esc(str(txt))}{''.join(num(u) for u in us)}</p>"
    q = pr.get("quote") if isinstance(pr.get("quote"), dict) else None
    quote = ""
    if q and q.get("text"):
        src = (f'<span class="src">{_ext_link(q["url"], esc(_path_of(q["url"])))}</span>'
               if q.get("url") else "")
        quote = f'<blockquote class="coquote">{esc(str(q["text"]))}{src}</blockquote>'
    by = ("in their own words, claimed page" if pr.get("by_kind") == "company"
          else "written from their site")
    prov = ""
    if srcs:
        first = srcs[0] if isinstance(srcs[0], dict) else {}
        when = f", read {esc(str(first['fetched_on']))}" if first.get("fetched_on") else ""
        links = ", ".join(
            _ext_link((s if isinstance(s, dict) else {}).get("url"),
                      f"{i + 1}&nbsp;{esc(_path_of((s if isinstance(s, dict) else {}).get('url') or ''))}")
            for i, s in enumerate(srcs))
        prov = (f'<p class="coprov">Written from {len(srcs)} page{"" if len(srcs) == 1 else "s"} '
                f'on {esc(dom or "their site")}{when}: {links}. Every sentence traces to '
                f'one of them.</p>')
    return (f'<section class="cosec coabout">'
            f'<div class="cosechd"><h2>About</h2><span class="smeta">{by}</span></div>'
            f'{lede}{paras}{quote}{prov}</section>')


def _co_rivals(o: dict, n_in_cat: int, by_id: dict) -> str:
    """The Competitors rail block - coRivals, ported. Three states, three
    different facts: a researched shortlist, a researched EMPTY, and not
    researched yet. The third offers the category as navigation, labelled as
    navigation, and never as the shortlist."""
    esc = html.escape
    link = (f'<a class="all" href="/?tab=companies">Browse all {n_in_cat:,} in '
            f'{esc(o.get("category") or "")} &rarr;</a>' if n_in_cat > 1 else "")
    checked = o.get("competitors_checked_on")
    rivals = o.get("competitors") or []
    if rivals:
        rows = ""
        for r in rivals:
            if not isinstance(r, dict):
                continue
            x = by_id.get(r.get("id")) or {}
            n = (f'<span class="n">{x["open_roles"]}</span>'
                 if x.get("open_roles") is not None else "")
            why = f'<span class="d">{esc(r["why"])}</span>' if r.get("why") else ""
            rows += (f'<div class="r"><span><a href="{_co_href(r.get("id"), by_id)}">'
                     f'{esc(x.get("name") or r.get("id") or "")}</a>{why}</span>{n}</div>')
        return (f'<section><h2>Competitors</h2>{rows}'
                f'<p class="note">Who a buyer would shortlist against them'
                f'{", checked " + esc(str(checked)) if checked else ""}.</p>{link}</section>')
    if o.get("competitors_none_found"):
        return (f'<section><h2>Competitors</h2><p class="note">Nobody else on this board '
                f'sells what they sell into this market. That is a finding, checked'
                f'{" " + esc(str(checked)) if checked else ""}, not an empty field.</p>'
                f'{link}</section>')
    return (f'<section><h2>Competitors</h2><p class="note">Not researched yet. The '
            f'companies below share this category, which is the room they are all '
            f'standing in, not the shortlist a buyer would build.</p>{link}</section>')


def _co_roles_html(o: dict, mine: list, readable: bool, now: dt.date) -> str:
    """The Open roles section body. ONE ROW PER OPENING, grouped by family
    the way the app groups them, sales families first the way this page
    always led, and cut at CO_ROLE_CAP with the rest pointed at the board.

    The app's rows are postings, and its "N roles" per group counts them.
    Here the rows are openings and the counts are openings: Xplor advertised
    one Account Executive requisition in forty cities, and a static page that
    printed the title forty times under a strip saying seventeen was the
    disagreement CLAUDE.md's counting rule exists to stop. The strip, the
    section label and the group labels all count the same thing."""
    esc = html.escape
    cid = o["id"]
    alert = f'/alerts?company={urllib.parse.quote(cid, safe="")}'
    if not mine:
        if readable:
            why = "Their board is one we read every night and it is empty right now."
        else:
            last = (f" &mdash; last on {esc(str(o['board_checked_on']))}"
                    if o.get("board_checked_on") else "")
            why = ("Their board is live but built in a way we cannot read automatically, "
                   f"so this list may be incomplete. A person checks it{last}.")
        board = (_ext_link(o["board_url"], "Open their hiring board &#8599;", "cobtn") + " "
                 if o.get("board_url") and _safe_url(o["board_url"]) else "")
        return (f'<div class="coempty">'
                f'<img src="/assets/mascot/svg/head-ghosted.svg" alt="" width="88" height="88">'
                f'<div><h3>No open roles we can see.</h3><p>{why}</p>'
                f'{board}<a class="cobtn" href="{alert}">Alert me when they post</a>'
                f'</div></div>')
    by_fam: dict = {}
    for p in mine:
        by_fam.setdefault(p.get("family") or "other", []).append(p)
    fams = []
    for fam, rows in by_fam.items():
        groups: dict = {}
        for r in rows:
            groups.setdefault(r["opening_id"], []).append(r)
        openings = sorted(groups.values(),
                          key=lambda g: (not g[0].get("quota_carrying"),
                                         g[0].get("title") or ""))
        fams.append((fam, openings))
    fams.sort(key=lambda fo: (fo[0] not in SALES_FAMILIES, -len(fo[1])))
    left = CO_ROLE_CAP
    out = ""
    for fam, openings in fams:
        n = len(openings)
        q = sum(1 for g in openings if g[0].get("quota_carrying"))
        shown = openings[:max(0, left)]
        left -= len(shown)
        rows = ""
        for grp in shown:
            rep = grp[0]
            places = {_loc_cell(g) for g in grp}
            loc = _loc_cell(rep)
            if len(places) > 1:
                loc += f" and {len(places) - 1} other location{'s' if len(places) > 2 else ''}"
            seen = [t for t in (_co_date(g.get("first_seen")) for g in grp) if t]
            age = _co_age(min(seen).isoformat(), now) if seen else ""
            rows += (f'<div class="corow">'
                     f'<span class="ti"><a href="/?role={urllib.parse.quote(str(rep.get("id") or ""), safe="")}">'
                     f'{esc(rep.get("title") or "")}</a></span>'
                     f'<span class="lo">{esc(loc)}</span>'
                     f'<span class="pa">{_pay_cell(rep)}</span>'
                     f'<span class="ag">{esc(age)}</span>'
                     f'<span class="sv"></span></div>')
        hidden = n - len(shown)
        more = (f'<a class="comore" href="/?co={urllib.parse.quote(cid, safe="")}">'
                f'Show {hidden} more in the board</a>' if hidden else "")
        out += (f'<div class="cogrp"><div class="cogrph"><h3>{esc(FAM.get(fam, fam))}</h3>'
                f'<span class="n">{n} role{"" if n == 1 else "s"}'
                f'{f" &middot; {q} quota-carrying" if q else ""}</span></div>'
                f'{rows}{more}</div>')
    return out


def company_page_html(o: dict, mine: list, board: dict, brand: dict,
                      by_id: dict, by_name: dict, in_cat: int) -> str:
    """The whole static company page for one organization - co(), ported.
    Everything on it is read from `o` and `mine`; nothing is typed in."""
    esc = html.escape
    site = brand["site"].rstrip("/")
    now = _co_now(board)
    cid = o["id"]
    open_ = o.get("open_roles") or 0
    quota = o.get("quota_roles") or 0
    readable = o.get("enumerable") is not False and not o.get("unreadable")
    phase = _co_phase(mine, readable, now)
    dom = re.sub(r"/.*$", "", re.sub(r"^https?://", "", o.get("website") or ""))
    q_id = urllib.parse.quote(cid, safe="")

    # --- identity ---------------------------------------------------------
    bits = [esc(x) for x in (o.get("category"), o.get("location")) if x]
    if o.get("year_founded"):
        bits.append(f"founded {esc(str(o['year_founded']))}")
    if o.get("tier"):
        bits.append(f"tier {esc(str(o['tier']))}")
    # claim state is not on file for any company, so the page says nothing
    # rather than asserting "unclaimed" about two thousand firms
    brands = o.get("brands") or []
    if brands:
        bits.append(f"owns {len(brands)} brand{'' if len(brands) == 1 else 's'}")
    if o.get("parent"):
        # `parent` is a NAME, not an id. Resolved here; a name nobody on the
        # board answers to is printed as the name and not as a dead link.
        par = by_name.get(str(o["parent"]).strip().lower())
        bits.append(f'part of <a href="{_co_href(par["id"], by_id)}">{esc(o["parent"])}</a>'
                    if par else f"part of {esc(o['parent'])}")
    initial = (o.get("name") or "?").strip()[:1] or "?"
    sep = '<span class="sep">&middot;</span>'
    ident = (f'<div class="coid">'
             f'<div class="logo" aria-hidden="true">{esc(initial)}</div>'
             f'<div style="flex:1;min-width:0"><h1>{esc(o["name"])}</h1>'
             f'<div class="cometa">{sep.join(bits)}</div></div>'
             f'<div class="coacts">'
             f'<a class="cobtn fill" href="/?co={q_id}">Open in the board</a>'
             f'<a class="cobtn" href="/alerts?company={q_id}">Alert on new roles</a>'
             f'</div></div>')

    # --- stat strip, four cells, always ------------------------------------
    ats = o.get("ats") or ""
    if readable:
        src_val = ats[:1].upper() + ats[1:] if ats else "Not on file"
        src_note = (f"read nightly{f' · all {open_} readable' if open_ else ''}"
                    if ats and ats not in ("html", "unknown")
                    else "a page we scan, not a board we can enumerate")
    else:
        src_val = "Board unreadable"
        src_note = (("custom HTML" if ats == "html" else "their board")
                    + " · a person checks it"
                    + (f" · last {esc(str(o['board_checked_on']))}" if o.get("board_checked_on") else ""))
    pct = f"{int(math.floor(quota / open_ * 100 + 0.5))}% of open roles" if quota and open_ else "nothing to count"
    strip = (f'<div class="costrip">'
             f'<dl><div class="v{"" if open_ else " dim"}">{open_}</div><dt>open roles</dt>'
             f'<dd>{esc(_co_open_note(mine, open_, readable, now))}</dd></dl>'
             f'<dl><div class="v{"" if quota else " dim"}">{quota or "&mdash;"}</div><dt>quota-carrying</dt>'
             f'<dd>{pct}</dd></dl>'
             f'<dl class="wide"><div class="v txt {phase["tone"]}">{esc(phase["value"])}</div>'
             f'<dt>hiring phase</dt><dd>{esc(phase["note"])}</dd></dl>'
             f'<dl class="src"><div class="v txt{"" if readable else " acc"}">{esc(src_val)}</div>'
             f'<dt>source</dt><dd>{src_note}</dd></dl>'
             f'</div>')

    # --- reading column -----------------------------------------------------
    about = _co_about(o, dom)
    news = ('<section class="cosec"><div class="cosechd"><h2>News</h2>'
            '<span class="smeta">none on file</span></div>'
            '<p style="font-size:12.5px;line-height:1.6;color:var(--c-ink2);margin:0">'
            'No news items have been recorded for this company.</p></section>')
    incomplete = ('<p class="coprov" style="padding-top:10px">This list may be incomplete: '
                  'their board is not one we can read in full.</p>' if mine and not readable else "")
    roles = (f'<section class="cosec"><div class="cosechd"><h2>Open roles</h2>'
             f'<span class="smeta">{f"{open_} on file" if mine else "none on file"}</span></div>'
             f'{_co_roles_html(o, mine, readable, now)}{incomplete}</section>')

    # --- reference rail -----------------------------------------------------
    links = ""
    unreadable_tag = '<span class="tag">unreadable</span>'
    if o.get("website") and _safe_url(o["website"]):
        links += f'<div class="r">{_ext_link(o["website"], esc(dom))}</div>'
    if o.get("board_url") and _safe_url(o["board_url"]):
        links += (f'<div class="r">{_ext_link(o["board_url"], "Their hiring board")}'
                  f'{"" if readable else unreadable_tag}</div>')
    rail = f"<section><h2>Links</h2>{links}</section>"
    if brands:
        rows = ""
        for b in brands:
            b = b if isinstance(b, dict) else {"name": str(b)}
            nm = esc(str(b.get("name") or ""))
            # a brand record carries no id of its own; it links to the folded
            # company's page only where that company still exists on the
            # board, and to its own site where the record names one
            was = b.get("was_id")
            if was and was in by_id:
                nm = f'<a href="{_co_href(was, by_id)}">{nm}</a>'
            elif b.get("website"):
                nm = _ext_link(b["website"], nm)
            d = f'<span class="d">{esc(str(b["descriptor"]))}</span>' if b.get("descriptor") else ""
            n = f'<span class="n">{b["openRoles"]}</span>' if b.get("openRoles") is not None else ""
            rows += f'<div class="r"><span>{nm}{d}</span>{n}</div>'
        rail += (f'<section><h2>Brands they own</h2>{rows}'
                 f'<p class="note">Counts are not rolled up: roles above are each '
                 f"company's own.</p></section>")
    if o.get("also"):
        rows = "".join(f'<div class="r"><span>{esc(str(a.get("sector") or ""))} / '
                       f'{esc(str(a.get("category") or ""))}</span></div>'
                       for a in o["also"] if isinstance(a, dict))
        rail += f"<section><h2>Also filed under</h2>{rows}</section>"
    rail += _co_rivals(o, in_cat, by_id)
    # WHO SAYS SO, on the page a stranger from search actually lands on. A
    # description we wrote from their site and one the company sent are
    # different claims about the world, and the badge is the only thing that
    # tells them apart. The wording never says "verified" about anything but
    # the address: we know somebody could read mail at that domain, not that
    # they speak for the company.
    claimed = o.get("claimed") if isinstance(o.get("claimed"), dict) else None
    if claimed:
        when = esc(str(claimed.get("on") or ""))
        rail += ('<section class="coclaim"><h3>Claimed by the company</h3>'
                 f'<p>Somebody at {esc(dom or o["name"])} confirmed an address on '
                 f'that domain{f" in {when}" if when else ""}. Corrections from them '
                 f'are reviewed here like any other, and they cannot edit who their '
                 f'competitors are.</p></section>')
    else:
        rail += ('<section class="coclaim">'
                 f'<h3>Work at {esc(o["name"])}?</h3>'
                 '<p>Claim this page and you can correct what it says, post your '
                 'open roles, and tell us if we have filed you in the wrong place. '
                 'It takes an email at your own domain, and no password.</p>'
                 f'<a class="cobtn" href="/claim?co={urllib.parse.quote(o["id"])}">'
                 'Claim this page</a></section>')
    # "Roles read from unknown nightly" is what the app prints for a readable
    # board whose ATS is recorded as "unknown". That is a sentence about a
    # system that does not exist; the strip one screen up already says the
    # page is scanned rather than enumerated, and this line agrees with it.
    nightly = readable and ats and ats not in ("html", "unknown")
    rail += (f'<section><p class="coprov">Record last verified'
             f'{" " + esc(str(o["board_checked_on"])) if o.get("board_checked_on") else ""}'
             f'{" by hand" if o.get("researched") else ""}. '
             f'{f"Roles read from {esc(ats)} nightly." if nightly else "Roles are checked by hand."}'
             f'</p></section>')

    foot = (f'<p class="coprov cofoot">Listed on {esc(brand["name"])}, which tracks sales '
            f'roles at state and local government technology companies. '
            f'<a href="/?co={q_id}">See this company in the board</a>, where the roles '
            f'are filterable and kept current.</p>')

    body = (f'<div class="copage"><div class="cowrap">'
            f'<nav class="cocrumb" aria-label="Breadcrumb">'
            f'<a href="/?tab=companies">Companies</a><span class="sep">/</span>'
            f'<a href="/?tab=companies">{esc(o.get("sector") or "")}</a>'
            f'<span class="sep">/</span>{esc(o.get("category") or "")}</nav>'
            f'{ident}{strip}'
            f'<div class="cobody"><main class="cocol">{about}{news}{roles}</main>'
            f'<aside class="corail">{rail}</aside></div>'
            f'{foot}</div></div>')

    # --- head: unchanged from the page this replaces ------------------------
    prof = o.get("profile") if isinstance(o.get("profile"), dict) else None
    desc = (o.get("description") or
            f"{o['name']} sells into {o.get('sector') or 'state and local government'}.")
    # THE DESCRIPTION ENDS ITS OWN SENTENCE before anything is appended to
    # it. "…for law enforcement 27 open roles, 3 of them quota-carrying."
    # shipped in the meta description of every page whose one-liner had no
    # terminal punctuation.
    if desc and desc[-1] not in ".!?":
        desc += "."
    line = ((f"{open_} open role{'s' if open_ != 1 else ''}"
             + (f", {quota} of them quota-carrying" if quota else "")) if open_ else "")
    first = ""
    if prof and prof.get("paragraphs"):
        first = str(prof["paragraphs"][0]).split(". ")[0].strip()
        if first and first[-1] not in ".!?":
            first += "."
    meta = " ".join(x for x in (first or desc, f"{line}." if line else "") if x)
    title = (f"{o['name']} is hiring · {brand['name']}" if line
             else f"{o['name']} · {brand['name']}")
    return _page(title, meta, f"{site}/c/{cid}", body, brand, "companies",
                 css=COPAGE_CSS, wrap=False)


def write_company_pages(out: pathlib.Path, board: dict, brand: dict) -> int:
    """A real page per company that has something to say.

    Head tags fix how a link UNFURLS. They do not fix crawling: Bing,
    LinkedIn's fetcher and most AI crawlers do not run JavaScript, so they saw
    an empty shell where a company's facts should be. These carry the facts in
    the HTML.

    A COMPANY WITH SOMETHING TO SAY GETS A PAGE. This used to be "only
    companies with something open", on the argument that 1,810 pages reading
    "nothing open right now" are worthless in an index. That was right when
    the page had nothing else. Once a company carries a sourced write-up or a
    researched shortlist, its page carries facts a crawler cannot get from
    the app, and the argument inverts. has_static_page is the one gate.

    The page itself is the app's company page, ported: see company_page_html.
    """
    d = out / "c"
    d.mkdir(parents=True, exist_ok=True)
    orgs = board.get("organizations", [])
    by_co: dict = {}
    for p_ in board.get("postings", []):
        by_co.setdefault(p_["company_id"], []).append(p_)
    by_id = {x["id"]: x for x in orgs if x.get("id")}
    by_name = {str(x["name"]).strip().lower(): x for x in orgs if x.get("name")}
    # the whole category, counted once, for the "Browse all N" link - the
    # company itself included, the way the app counts it
    cat_n: dict = {}
    for x in orgs:
        cat_n[(x.get("sector"), x.get("category"))] = cat_n.get((x.get("sector"), x.get("category")), 0) + 1
    n = 0
    for o in orgs:
        if not has_static_page(o):
            continue
        in_cat = cat_n.get((o.get("sector"), o.get("category")), 0)
        (d / f"{o['id']}.html").write_text(company_page_html(
            o, by_co.get(o["id"], []), board, brand, by_id, by_name, in_cat))
        n += 1
    return n


def write_state_pages(out: pathlib.Path, board: dict, brand: dict) -> int:
    """One no-JS page per state that has an office posting.

    "Govtech sales jobs in California" is the highest-intent question this
    dataset can answer and no competitor answers it, but a JavaScript-rendered
    single page can never rank for any of the forty-two.

    Built from `office`, not `work_mode`: a bare city is an office and 79% of
    postings never state a mode, so gating on the words "hybrid" or "onsite"
    would reach a fraction of them. That is the same distinction the near-a-city
    filter makes, for the same reason.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import roles as role_lib
    code_to_name = {v: k.title() for k, v in role_lib.STATE_NAMES.items()}
    site = brand["site"].rstrip("/")
    d = out / "s"
    d.mkdir(parents=True, exist_ok=True)

    # SALES ROLES, because that is what the page says it is. The board carries
    # every posting on purpose - knowing what KIND of hiring a company is doing
    # is the point of the market view - but a page headed "Govtech sales jobs in
    # California" that lists Backend Software Engineer and Administrative
    # Coordinator is promising one thing and delivering another. gtm is sales,
    # marketing and BD; field is implementation and services, which is the
    # nearest neighbour a seller actually looks at.
    SALES = {"gtm", "field"}
    by_state: dict = {}
    for p_ in board.get("postings", []):
        st = (p_.get("office") or {}).get("state")
        if st and p_.get("family") in SALES:
            by_state.setdefault(st, []).append(p_)
    n = 0
    for st, ps in sorted(by_state.items()):
        name = code_to_name.get(st, st)
        cos = {}
        for p_ in ps:
            cos.setdefault(p_["company"], []).append(p_)
        # OPENINGS, NOT ROWS, and this page shipped rows for months. One
        # requisition advertised in nine Michigan cities is nine rows, and
        # /s/mi said "12 open roles ... 9 quota-carrying" against 4 openings
        # of which 1 carried a quota - all nine of those rows were the single
        # Xplor Account Executive req that opening_id was invented for. The
        # sentence is also this page's <meta description>, so the inflated
        # number is the one Google renders under the result.
        opens = {p_["opening_id"] for p_ in ps}
        quota = len({p_["opening_id"] for p_ in ps if p_.get("quota_carrying")})
        items = ""
        for co, rs in sorted(cos.items(),
                             key=lambda kv: (-len({r["opening_id"] for r in kv[1]}),
                                             kv[0])):
            cid = rs[0]["company_id"]
            names = sorted({r.get("title") or "" for r in rs})
            titles = ", ".join(names[:4])
            # "and N more" sits after a list of TITLES, so N must be titles.
            # It counted rows, so a company with one job in six cities read
            # "Account Executive and 5 more".
            more = f" and {len(names) - 4} more" if len(names) > 4 else ""
            items += (f'<li><div class="role"><a href="/c/{urllib.parse.quote(cid)}.html">'
                      f'{html.escape(co)}</a></div>'
                      f'<div class="meta">{html.escape(titles)}{more}</div></li>')
        line = (f"{len(opens)} open role{'s' if len(opens) != 1 else ''} across "
                f"{len(cos)} compan{'ies' if len(cos) != 1 else 'y'}"
                + (f", {quota} quota-carrying" if quota else ""))
        body = (f'<h1>Govtech sales jobs in {html.escape(name)}</h1>'
                f'<p class="kv">{line}</p>'
                f'<p>Sales, business development and field roles at companies '
                f'selling technology to state and local government, with someone '
                f'sitting in {html.escape(name)}. Roles that never state a '
                f'location are not counted here, and neither are the engineering '
                f'and back-office openings these companies also carry, so this is '
                f'a floor rather than a total.</p>'
                f'<ul>{items}</ul>'
                f'<div class="note"><a href="/?tab=jobs&amp;st={urllib.parse.quote(st)}">'
                f'Filter the live board to {html.escape(name)}</a>, where these are '
                f'sortable and kept current.</div>')
        (d / f"{st.lower()}.html").write_text(_page(
            f"Govtech sales jobs in {name} · {brand['name']}",
            f"{line}. Sales roles at state and local government technology "
            f"companies with a desk in {name}.",
            f"{site}/s/{st.lower()}", body, brand, "jobs"))
        n += 1
    return n


def write_feeds(out: pathlib.Path, board: dict, brand: dict) -> dict:
    """An RSS feed of the new quota roles, and calendar feeds for the floors.

    A subscribed feed is a foothold in somebody's week that renews itself. A
    one-off .ics download is forgotten by Friday, and a board with no feed is a
    board you have to remember to visit.

    The RSS carries roles first seen on THIS build, which is what "new" means
    here and the only definition the data supports. When a run adds nothing the
    feed is empty rather than padded with yesterday's - an empty feed is a true
    statement about a quiet day.
    """
    site = brand["site"].rstrip("/")
    gen = board.get("generated") or dt.date.today().isoformat()
    fresh = [p_ for p_ in board.get("postings", [])
             if p_.get("first_seen") == gen and p_.get("quota_carrying")]
    fresh.sort(key=lambda p_: (p_.get("company") or "", p_.get("title") or ""))
    items = ""
    for p_ in fresh[:60]:
        link = f"{site}/?role={urllib.parse.quote(p_['id'])}"
        where = (p_.get("location") or "").strip()
        desc = (f"{p_.get('company','')} is hiring a {p_.get('title','')}"
                + (f" in {where}" if where else "") + ".")
        items += (f"  <item>\n"
                  f"    <title>{html.escape(p_.get('title') or '')} at "
                  f"{html.escape(p_.get('company') or '')}</title>\n"
                  f"    <link>{html.escape(link)}</link>\n"
                  f"    <guid isPermaLink=\"false\">{html.escape(p_['id'])}</guid>\n"
                  f"    <description>{html.escape(desc)}</description>\n"
                  f"  </item>\n")
    (out / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>\n'
        f"  <title>{html.escape(brand['name'])}: new quota-carrying roles</title>\n"
        f"  <link>{site}/</link>\n"
        f"  <description>Sales roles at state and local government technology "
        f"companies, first seen on the most recent run.</description>\n"
        f"  <lastBuildDate>{gen}</lastBuildDate>\n"
        + items + "</channel></rss>\n")

    # Calendars. One for everything, one per department block, so somebody who
    # only sells into public safety is not subscribed to library conferences.
    confs = [c for c in (board.get("conferences") or []) if c.get("dates")]
    def ics(rows, name):
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
                 f"PRODID:-//{brand['name']}//conferences//EN",
                 "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{name}"]
        n = 0
        for c in rows:
            start = _ics_date(c.get("dates"))
            if not start:
                continue          # a date we could not parse is not invented
            n += 1
            lines += ["BEGIN:VEVENT",
                      # slugified: a UID with a space in it is not a valid
                      # iCalendar identifier and some clients drop the event
                      f"UID:{_slugify(c.get('tag') or c.get('name') or 'event')}"
                      f"@{brand['domain']}",
                      f"DTSTART;VALUE=DATE:{start}",
                      f"SUMMARY:{_ics_esc(c.get('name') or '')}",
                      f"LOCATION:{_ics_esc(c.get('city') or '')}",
                      # WHAT WE HOLD, NOT WHAT THE SHOW CLAIMS, and silence
                      # at zero. This printed `approx_count`, which is the
                      # catalogue's estimate of the show's SIZE: APCO 2026 has
                      # approx_count 250 and companies 0, so its calendar entry
                      # read "250 exhibitors tracked" over a floor nobody has
                      # swept. Unswept events read "0 exhibitors tracked",
                      # which is the "we looked and found none" claim the UI
                      # refuses everywhere else. icsFor() in index.html has
                      # always used the real count and omitted the line at
                      # zero; two writers of one file now follow one rule.
                      f"DESCRIPTION:{_ics_desc(c, site)}",
                      "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n", n

    cal = out / "cal"
    cal.mkdir(parents=True, exist_ok=True)
    body, n_all = ics(confs, f"{brand['name']}: govtech conferences")
    (out / "conferences.ics").write_text(body)
    blocks = sorted({c.get("block") for c in confs if c.get("block")})
    for b in blocks:
        body, _ = ics([c for c in confs if c.get("block") == b], f"{brand['name']}: {b}")
        (cal / f"{_slugify(b)}.ics").write_text(body)
    return {"rss": len(fresh), "events": n_all, "calendars": 1 + len(blocks)}


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "block"


def _ics_esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", " ")


def _ics_date(dates: str) -> str | None:
    """YYYYMMDD from the catalogue's date string, or None.

    Returns None rather than guessing. A calendar entry on the wrong day is
    worse than no calendar entry, because somebody books travel around it.
    """
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(dates or ""))
    if m:
        return m.group(1) + m.group(2) + m.group(3)
    m = re.match(r"\s*([A-Za-z]+)\s+(\d{1,2})", str(dates or ""))
    if not m:
        return None
    months = {mn.lower(): i for i, mn in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], 1)}
    mon = months.get(m.group(1).lower()[:3] and
                     next((k for k in months if k.startswith(m.group(1).lower()[:3])), ""))
    yr = re.search(r"(20\d{2})", str(dates or ""))
    if not mon or not yr:
        return None
    return f"{yr.group(1)}{mon:02d}{int(m.group(2)):02d}"



def _ics_desc(c: dict, site: str) -> str:
    """The calendar description for one conference: a count only when we have
    one, and the link either way."""
    n = c.get("companies") or 0
    head = (f"{n} govtech exhibitor{'s' if n != 1 else ''} on file. " if n else "")
    return _ics_esc(f"{head}{site}/?tab=conferences")



def write_conference_pages(out: pathlib.Path, board: dict, brand: dict) -> int:
    """A page per conference, with the exhibitors we track and who is hiring.

    "Which exhibitors at this show are hiring salespeople" is a question no
    other site on the internet can answer, and it is the most linkable thing
    this dataset produces. The link already exists in the data: 1,139
    organizations carry the conference tag they were found at.

    WHAT THE ROSTER IS NOT. It is the exhibitors WE TRACK, never the show's
    exhibitor list - we hold 35 of the 93 tags in the catalogue and swept only
    eleven floors. Every page says so, because "52 exhibitors" read as a claim
    about the show rather than about us is the kind of quiet overstatement this
    project refuses.
    """
    site = brand["site"].rstrip("/")
    d = out / "e"
    d.mkdir(parents=True, exist_ok=True)
    by_tag: dict = {}
    for o in board.get("organizations", []):
        if o.get("conference"):
            by_tag.setdefault(o["conference"], []).append(o)

    n = 0
    for c in board.get("conferences", []) or []:
        tag = c.get("tag")
        if not tag:
            continue
        roster = sorted(by_tag.get(tag, []),
                        key=lambda o: (-(o.get("open_roles") or 0), o.get("name") or ""))
        if not roster and not c.get("dates"):
            continue      # nothing to say that the catalogue tab does not say
        hiring = [o for o in roster if o.get("open_roles")]
        where = " &middot; ".join(html.escape(x) for x in
                                 (c.get("dates"), c.get("city"), c.get("department"))
                                 if x)
        items = ""
        for o in roster[:60]:
            n_open = o.get("open_roles") or 0
            link = (f'<a href="/c/{urllib.parse.quote(o["id"])}.html">'
                    f'{html.escape(o["name"])}</a>' if n_open
                    else html.escape(o["name"]))
            note = (f'<div class="meta">{n_open} open role'
                    f'{"s" if n_open != 1 else ""}</div>' if n_open else
                    '<div class="meta">nothing open that we can see</div>')
            items += f'<li><div class="role">{link}</div>{note}</li>'
        more = (f'<p class="kv">Showing {min(len(roster), 60)} of '
                f'{len(roster)}, the ones hiring first.</p>'
                if len(roster) > 60 else "")
        line = (f"{len(hiring)} of the {len(roster)} exhibitors we track here "
                f"are hiring" if roster else "No exhibitors tracked here yet")
        body = (f'<h1>{html.escape(c.get("name") or tag)}</h1>'
                + (f'<p class="kv">{where}</p>' if where else "")
                + f'<h2>{line}</h2>'
                + (f'<ul>{items}</ul>{more}' if roster else "")
                + f'<div class="note">These are the exhibitors <em>we</em> track '
                  f'from this show, not the show\'s exhibitor list. We hold a '
                  f'roster for some conferences and not others, so a short list '
                  f'here means we know less about this floor, never that the '
                  f'floor was small. '
                  + (f'<a href="{html.escape(c["url"])}" rel="nofollow noopener">'
                     f'The event\'s own site</a> has the real one. ' if c.get("url") else "")
                  + f'<a href="/?tab=conferences">All conferences</a>.</div>')
        (d / f"{_slugify(tag)}.html").write_text(_page(
            f"{c.get('name') or tag}: who is hiring · {brand['name']}",
            f"{line}. " + (f"{c.get('dates')}, {c.get('city')}. " if c.get("dates") else "")
            + "Sales roles at the govtech companies on this floor.",
            f"{site}/e/{_slugify(tag)}", body, brand, "conferences"))
        n += 1
    return n


def attach_active(board: dict) -> dict:
    """Put the "hiring hard" list on the board. Returns it too, for callers.

    EXTRACTED SO THE SUITE CAN CALL IT. This was inline in main(), which meant
    the only way to test it was to read public/data/board.json - a gitignored
    file that does not exist when selftest runs in CI, so the check took its
    exists() escape and passed while the badge could be deleted outright.

    scripts/momentum.py derives it from our own daily snapshots: their hiring,
    not our traffic, so no visitor is counted to produce it. Computed at ship
    time rather than in build_board because it needs the day's history snapshot
    that build_board writes on its way out, and a signal this cheap should not
    cost a twenty-minute crawl to refresh.

    A LIST, NEVER A FLAG PER COMPANY. Nothing qualifying means an empty list
    and no badge at all, which is the same rule the home banner follows when a
    run was quiet: a badge on everything means nothing, and a badge on nothing
    is the honest output of a week where nobody surged.
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import momentum as _mom
        surge = _mom.surge()
        board["active"] = ([{"id": c["id"], "was": c["was"], "now": c["now"]}
                            for c in surge.get("companies", [])]
                           if surge.get("ready") else [])
        board["active_since"] = surge.get("since")
    except Exception as e:                       # noqa: BLE001
        # A signal that cannot be computed must not cost the build. An empty
        # list renders nothing, which is what a reader should see when we do
        # not know - never a badge on a guess.
        board["active"], board["active_since"] = [], None
        board["active_error"] = f"{type(e).__name__}: {e}"
    return board


def write_noscript(out: pathlib.Path, board: dict, brand: dict) -> int:
    """Put something real in the shipped index.html for a reader without JS.

    THE PAGE IS TWO EMPTY DIVS. Everything is drawn in the browser from
    board.json, so with scripts off the live site yields 385 characters and
    all of them are furniture - "Saved on this device", "Switch theme", "Add a
    company". No job, no company, no sentence saying what the site is. There
    was no <noscript> anywhere.

    AND THE FALLBACK WAS ALREADY BUILT. 296 company pages, 38 state pages and
    120 conference pages ship every night as plain HTML that needs no
    JavaScript at all - written for exactly this reader - and NOTHING linked
    to them. Zero occurrences of /c/, /s/, /e/ or feed.xml in the app. They
    were reachable only by knowing a url or parsing sitemap.xml, which is a
    page search engines discount and a person cannot use.

    INJECTED AT SHIP TIME rather than written into the source, because the
    numbers and the state list have to be true on the day. A hand-maintained
    list of 38 states in a single-file app is a list that goes stale the first
    time a state gains or loses its last posting, and a hardcoded count is the
    kind of frozen figure this project keeps finding and removing.
    """
    src = out / "index.html"
    if not src.exists():
        return 0
    html_txt = src.read_text()
    anchor = '<main><div id="stale"></div><div class="panel" id="view"></div></main>'
    if anchor not in html_txt:
        # The body changed shape. Say so rather than silently shipping no
        # fallback - a missing noscript looks identical to a working one.
        print("  noscript: could not find the main element; NOT injected")
        return 0

    t = board.get("totals") or {}
    name = brand.get("name") or "SLED JOBS"
    # The states that actually have a page, read off what write_state_pages
    # wrote rather than guessed at.
    sdir = out / "s"
    codes = sorted(f.stem for f in sdir.glob("*.html")) if sdir.exists() else []
    # The same map write_state_pages uses, so the two pages name a state
    # identically rather than one saying "Texas" and the other "TX".
    import roles as role_lib
    code_to_name = {v: k.title() for k, v in role_lib.STATE_NAMES.items()}
    links = "".join(
        f'<li><a href="/s/{c}">{html.escape(code_to_name.get(c.upper(), c.upper()))}</a></li>'
        for c in codes)

    block = (
        '<noscript><div class="nojs">'
        f'<h1>{html.escape(name)}</h1>'
        f'<p>Every open sales role at state and local government technology '
        f'companies. <strong>{t.get("openings", 0):,} roles</strong> at '
        f'{len(board.get("organizations") or []):,} companies, rebuilt every '
        f'night.</p>'
        '<p>This page normally assembles itself in your browser. With '
        'JavaScript off, these pages need none:</p>'
        f'<h2>Sales roles by state</h2><ul class="nojs-cols">{links}</ul>'
        '<h2>Everything else</h2><ul>'
        '<li><a href="/feed.xml">The newest roles, as a feed</a></li>'
        '<li><a href="/sitemap.xml">Every company, state and conference page</a></li>'
        '<li><a href="/alerts">Email alerts</a></li>'
        '</ul></div></noscript>')
    src.write_text(html_txt.replace(anchor, block + anchor, 1))
    return len(codes)


def write_headers(out: pathlib.Path) -> None:
    """public/_headers — the one response header this site actually needs.

    THIS REPLACES A FILE THAT DID NOTHING. vercel.json sat in the repo root
    declaring three security headers, and Cloudflare Pages does not read
    vercel.json, so none of them applied on any path that exists. Verified
    2026-08-30 against the live site: two of the three - nosniff and
    referrer-policy - are sent anyway, because Cloudflare Pages sets them
    itself. The third, frame protection, was not sent at all.

    AND IT IS SCOPED TO ONE PAGE, on purpose.

    The board is public, read-only, has no session and no state-changing
    action; framing it is harmless and somebody embedding a job list in their
    own site is a use, not an attack. Blanket-denying it would be cargo cult.

    /alerts is different and is the reason this file exists. It holds a
    subscription token in memory, and it carries a one-click "Delete this
    subscription and everything stored with it" behind that token. A framed
    copy of that page is the textbook clickjacking target: the victim is
    already authenticated by the link in their own email, and one disguised
    click destroys their subscription. That is worth a header.

    Both forms, because X-Frame-Options is the one older browsers honour and
    frame-ancestors is the one that is actually specified. Nothing else is set
    here - a Content-Security-Policy on the board itself would have to allow
    the inline script and style the single-file app is built from, which is a
    policy that permits what it is meant to prevent.
    """
    (out / "_headers").write_text(
        "# Generated by build_site.py - see write_headers() for why this is\n"
        "# scoped to /alerts and not applied site-wide.\n"
        "/alerts\n"
        "  X-Frame-Options: DENY\n"
        "  Content-Security-Policy: frame-ancestors 'none'\n"
        "/alerts.html\n"
        # A CLAIM PAGE IS NOT A LANDING PAGE. It carries a token in the URL
        # and answers only to somebody holding one; indexing it would put
        # dead token links in a search engine.
        "/claim.html\n"
        "  X-Frame-Options: DENY\n"
        "  Content-Security-Policy: frame-ancestors 'none'\n")
    print("  _headers: frame protection on /alerts")


# The characters encodeURIComponent leaves alone. urllib.parse.quote
# escapes ! ~ * ' ( ) and encodeURIComponent does not, so a sitemap built
# with the default `safe` submits %28Controls%29 while the page it reaches
# declares (Controls) as canonical. A module constant because an f-string
# cannot carry these quotes inline before Python 3.12.
_JS_SAFE = "!~*'()"


# CLOUDFLARE SERVES THE EXTENSIONLESS FORM AND 308s THE OTHER.
#
# /c/verkada.html answers 308 to /c/verkada, and /c/verkada then declared its
# canonical as /c/verkada.html - a canonical pointing at a redirect away from
# the page declaring it. The sitemap submitted the .html form for all 462
# company, state and conference pages, so every one told a crawler "the URL you
# were sent to is not the real one, the real one is this URL that bounces you
# back here".
#
# One form everywhere: the one the server actually serves.
def write_crawl_files(out: pathlib.Path, board: dict, brand: dict) -> dict:
    """robots.txt, sitemap.xml and a real 404, none of which existed.

    /robots.txt and /sitemap.xml both answered with the app's own HTML and a
    200, so there were no crawl directives at all and every mistyped or stale
    path was a soft-404 teaching crawlers the whole domain is duplicate
    content. A single-page app cannot be indexed by luck.

    The sitemap lists only addresses that RESOLVE TO SOMETHING: the six tabs,
    every company showing an opening, and every conference. A company with
    nothing open is deliberately left out - it is a real page, but a sitemap
    is a claim that a url is worth crawling, and 1,800 near-identical
    no-openings pages is how a site teaches a crawler to stop believing it.
    """
    site = brand["site"].rstrip("/")
    today = dt.date.today().isoformat()
    urls = [(f"{site}/", "daily", "1.0")]
    for tab in ("jobs", "companies", "conferences", "market", "alerts"):
        urls.append((f"{site}/?tab={tab}", "daily", "0.8"))
    # every company that HAS a page, not every company that is hiring - the
    # 1,810-near-identical-documents argument dies once a page carries a
    # sourced write-up. Same gate as the writer, by construction.
    hiring = [o for o in board.get("organizations", []) if has_static_page(o)]
    # /c/<id>.html, not ?co=. Both addresses show the same company, so one of
    # them has to be the canonical or they compete with each other; the static
    # page is the one with the facts in its HTML, which is what a crawler that
    # never runs JavaScript can actually read.
    for o in sorted(hiring, key=lambda x: -(x.get("open_roles") or 0)):
        urls.append((f"{site}/c/{urllib.parse.quote(o['id'])}", "weekly", "0.6"))
    for st in sorted({(p_.get("office") or {}).get("state")
                      for p_ in board.get("postings", [])
                      if p_.get("family") in ("gtm", "field")} - {None, ""}):
        urls.append((f"{site}/s/{st.lower()}", "weekly", "0.6"))
    for c in board.get("conferences", []) or []:
        tag = c.get("tag") or c.get("event_tag") or c.get("conference")
        if tag:
            urls.append((f"{site}/e/{_slugify(tag)}", "monthly", "0.5"))

    # THE ROLE PAGES, WHICH WERE NOT IN HERE AT ALL.
    #
    # The sitemap listed 468 addresses - companies, states, conferences, tabs -
    # and zero job pages. Meanwhile functions/_middleware.js emits a JobPosting
    # block on every ?role= whose description we actually read, 3,524 of them
    # today, and Google for Jobs is the one channel that sends high-intent
    # traffic to a board this size.
    #
    # So the structured data was correct, live, and undiscoverable. The only
    # route to a job page was to crawl a company page and follow a link out of
    # a single-page app. The markup was doing its job and nothing pointed at
    # it.
    #
    # DAILY, and priced just under the home page, because a job posting is the
    # most perishable thing here and the most valuable while fresh. The sitemap
    # is rebuilt from the live board every run, so a role that came off the
    # board leaves the sitemap without anything having to remember to remove
    # it.
    #
    # THE ONES WE READ COME FIRST. A posting with no description still gets a
    # page and still belongs here, since somebody may search its exact title,
    # but the ones carrying a JobPosting block are the ones an aggregator can
    # act on, so they lead and are priced higher.
    # EVERY POSTING GETS ITS URL. THE DEDUPE HERE WAS A MISTAKE AND IS GONE.
    #
    # It keyed on (opening_id, city, state, work_mode) to collapse role pages
    # that "render identically". But `office` parses for only 35% of the board,
    # so for the other 65% the key was (opening_id, None, None, work_mode) and
    # every distinct requisition under one title became a single entry. It
    # dropped 232 postings, 193 of which point at a DIFFERENT APPLY URL than
    # the one that survived - two separate Accela Account Executive reqs at
    # $70-85k and $100-120k, and only the cheaper one reached the sitemap.
    #
    # The 62 it was aimed at turned out not to be duplicates either: all 29
    # groups have distinct apply urls. A company posting two identical-looking
    # requisitions is the employer doing that, not us duplicating anything, and
    # Google's "do not submit the same job twice" is about the same job.
    #
    # CLAUDE.md is explicit that the per-location rows all stay and only the
    # COUNTING changes. The jobs list collapses by opening because a list is
    # read; a sitemap enumerates pages, and each posting has its own page with
    # its own apply link. Dropping one is a page a reader can reach and a
    # crawler cannot - a false absence, made by us, at scale.
    read, unread = [], []
    for p_ in board.get("postings", []):
        pid = p_.get("id")
        if not pid:
            continue
        # SAME ENCODING AS THE CANONICAL. urllib.parse.quote escapes ! ' ( )
        # and * ; encodeURIComponent, which _middleware.js uses to build the
        # canonical, does not. 539 posting ids contain one of those, so the
        # sitemap submitted %28Controls%29 while the page it reached declared
        # (Controls) as canonical - Google files that as "alternate page with
        # proper canonical tag" and the submitted address is not the indexed
        # one, for 12% of the role urls.
        u = f"{site}/?role={urllib.parse.quote(pid, safe=_JS_SAFE)}"
        (read if p_.get("jd_seen") else unread).append(u)
    for u in read:
        urls.append((u, "daily", "0.9"))
    for u in unread:
        urls.append((u, "daily", "0.7"))
    body = "\n".join(
        f'  <url><loc>{html.escape(u)}</loc><lastmod>{today}</lastmod>'
        f'<changefreq>{f}</changefreq><priority>{pr}</priority></url>'
        for u, f, pr in urls)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + body + "\n</urlset>\n")

    (out / "robots.txt").write_text(
        "# Every page here is public and meant to be found.\n"
        "User-agent: *\n"
        "Allow: /\n"
        "# data/board.json is 6MB of JSON the pages read at runtime. It is not\n"
        "# secret - it is simply not a page, and crawling it helps nobody.\n"
        "Disallow: /data/\n"
        f"\nSitemap: {site}/sitemap.xml\n")

    # A real 404 body. Cloudflare Pages serves /404.html for an unmatched path,
    # which turns every soft-404 into an honest one.
    (out / "404.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Not found &middot; {html.escape(brand['name'])}</title>
<link rel="icon" href="/assets/mascot/svg/favicon.svg" type="image/svg+xml">
<style>
 body{{margin:0;background:#E8F1F7;color:#1F2536;
   font:16px/1.6 Archivo,system-ui,sans-serif;display:grid;place-items:center;
   min-height:100vh;padding:24px}}
 @media (prefers-color-scheme:dark){{body{{background:#161B29;color:#E8F1F7}}}}
 .b{{max-width:52ch;text-align:center}}
 img{{width:96px;height:auto;margin:0 0 18px}}
 h1{{font-size:26px;font-weight:800;margin:0 0 8px;letter-spacing:-.02em}}
 p{{margin:0 0 18px;color:#556F82}}
 @media (prefers-color-scheme:dark){{p{{color:#93A9BA}}}}
 a{{color:#0B57C4;font-weight:600}}
 @media (prefers-color-scheme:dark){{a{{color:#478EF5}}}}
</style></head>
<body><div class="b">
<img src="/assets/mascot/svg/head-ghosted.svg" alt="">
<h1>Nothing at this address</h1>
<p>The page you asked for is not here. A role that has come off the board, or a
company record that was merged into another, both end up looking like this.</p>
<p><a href="/">Go to the board</a></p>
</div></body></html>
""")
    return {"urls": len(urls), "companies": len(hiring)}


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

    # the share cards the head-tag middleware points at. Without these the
    # og:image tags name a 404 and a link unfurls with a broken picture, which
    # is worse than the naked url it replaced.
    og = ROOT / "assets" / "og"
    if og.exists():
        shutil.copytree(og, out / "assets" / "og")

    # logos are public by nature - they are the companies' own marks, served
    # from our origin so no visitor is reported to a logo service
    logos = ROOT / "assets" / "logos"
    if logos.exists():
        shutil.copytree(logos, out / "assets" / "logos")

    build_admin_bundle(out)

    board, stripped = sanitize(board_src)

    attach_active(board)
    print(f"  active: {len(board['active'])} company(ies) hiring harder "
          f"since {board.get('active_since') or 'n/a'}")
    # 300KB the browser has to parse.
    (out / "data" / "board.json").write_text(
        json.dumps(board, separators=(",", ":")))

    # The alerts page needs the sector names and nothing else. Without this it
    # would pull board.json - 4.7MB - to fill one dropdown on a settings page.
    # the name, tagline and palette, so a page never hardcodes them either
    shutil.copy2(ROOT / "data" / "brand.json", out / "data" / "brand.json")

    # What came off the board, and what changed. Both are computed every run
    # and were never published, so the site could say what arrived and never
    # what left - a role a reader saw yesterday simply vanished.
    for name in ("removed.json", "latest_diff.json"):
        src = ROOT / "data" / name
        if src.exists():
            shutil.copy2(src, out / "data" / name)

    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    (out / "data" / "sectors.json").write_text(
        json.dumps([x["name"] for x in schema["sectors"]], separators=(",", ":")))

    brand = json.loads((ROOT / "data" / "brand.json").read_text())
    write_headers(out)
    crawl = write_crawl_files(out, board, brand)
    meta_idx = write_meta_index(out, board)
    n_co = write_company_pages(out, board, brand)
    n_st = write_state_pages(out, board, brand)
    feeds = write_feeds(out, board, brand)
    n_ev = write_conference_pages(out, board, brand)
    # AFTER the pages it links to exist, so it can only ever name a page that
    # was actually written. A fallback advertising a 404 is worse than none.
    n_ns = write_noscript(out, board, brand)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {a.out}/: {len(SHIP)} page(s) + data/board.json")
    print(f"  {len(board['postings'])} postings, "
          f"{len(board['organizations'])} organizations")
    print(f"  {stripped} internal error string(s) replaced with the plain fact")
    print(f"  sitemap.xml: {crawl['urls']} urls ({crawl['companies']} companies "
          f"with an opening), robots.txt, 404.html")
    print(f"  noscript: a real page for a reader without JavaScript, "
          f"linking {n_ns} state page(s)")
    print(f"  c/: {n_co} prerendered company pages")
    print(f"  s/: {n_st} state pages")
    print(f"  e/: {n_ev} conference pages")
    print(f"  feed.xml: {feeds['rss']} new quota role(s) &middot; "
          f"{feeds['calendars']} calendar(s), {feeds['events']} dated event(s)"
          .replace("&middot;", "·"))
    print(f"  meta-index.json: {meta_idx['roles']} roles, "
          f"{meta_idx['companies']} companies for the head-tag worker")
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
