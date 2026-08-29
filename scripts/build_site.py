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
import os
import pathlib
import re
import shutil
import sys
import urllib.parse

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
            r["d"] = p_.get("first_seen") or ""
            if off.get("city"):
                r["ci"] = off["city"]
            if off.get("state"):
                r["st"] = off["state"]
        roles[p_["id"]] = r
    for o in board.get("organizations", []):
        cos[o["id"]] = {"n": o.get("name") or "", "s": o.get("sector") or "",
                        "d": (o.get("description") or "")[:180],
                        "r": o.get("open_roles") or 0}
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


def _page(title: str, desc: str, canonical: str, body: str, brand: dict,
          og: str = "home") -> str:
    """One no-JS page, in the brand's own tokens.

    Deliberately not a copy of index.html: this is what a crawler, a
    link-unfurler and a reader with JavaScript off actually get, so it carries
    the facts in the HTML rather than fetching them. It links INTO the app for
    anyone who wants filters.
    """
    p_ = brand["palette"]
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
<style>
 :root{{--bg:{p_['ice']['hex']};--panel:{p_['belly']['hex']};--ink:{p_['penguin']['hex']};
   --line:{p_['frost']['hex']};--dim:{brand['derived']['deep_fog']['hex']};
   --link:{p_['badge']['hex']};--beak:{p_['beak']['hex']}}}
 @media (prefers-color-scheme:dark){{:root{{--bg:{p_['penguin']['hex']};--panel:#28304a;
   --ink:{p_['ice']['hex']};--line:#39445e;--dim:#93a9ba;
   --link:{brand['derived']['dark']['badge']['hex']}}}}}
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
</style></head><body>
<div class="band"><a href="/">{html.escape(brand['name'])}</a></div>
<main>{body}</main>
</body></html>
"""


def write_company_pages(out: pathlib.Path, board: dict, brand: dict) -> int:
    """A real page per company that is hiring.

    Head tags fix how a link UNFURLS. They do not fix crawling: Bing,
    LinkedIn's fetcher and most AI crawlers do not run JavaScript, so they saw
    an empty shell where a company's facts should be. These carry the facts in
    the HTML.

    Only companies with something open. A page saying "nothing open right now"
    is true, useful in the app where you arrived deliberately, and worthless as
    1,810 near-identical documents in an index.
    """
    site = brand["site"].rstrip("/")
    d = out / "c"
    d.mkdir(parents=True, exist_ok=True)
    by_co = {}
    for p_ in board.get("postings", []):
        by_co.setdefault(p_["company_id"], []).append(p_)
    n = 0
    for o in board.get("organizations", []):
        if not o.get("open_roles"):
            continue
        # Sales first, then the rest, then capped. This site is about sales
        # roles, so a page opening with forty engineering titles buries the
        # thing somebody came for - and Verkada alone carries 247 openings,
        # which is 38KB of list nobody reads to the end of. The count above is
        # the true total either way; what is capped is the listing, and the
        # page says so rather than letting the two disagree.
        SALES = {"gtm", "field"}
        roles = sorted(by_co.get(o["id"], []),
                       key=lambda r: (r.get("family") not in SALES,
                                      not r.get("quota_carrying"),
                                      r.get("title") or ""))
        shown, hidden = roles[:40], max(0, len(roles) - 40)
        bits = [x for x in (o.get("sector"), o.get("category")) if x]
        facts = " &middot; ".join(html.escape(x) for x in (
            [" / ".join(bits)] if bits else []) + [
            html.escape(o["location"]) for _ in [1] if o.get("location")] + [
            "founded " + html.escape(str(o["year_founded"])) for _ in [1] if o.get("year_founded")])
        items = ""
        for r in shown:
            loc = r.get("location") or ""
            quota = ' <span class="meta">quota-carrying</span>' if r.get("quota_carrying") else ""
            items += (f'<li><div class="role">{html.escape(r.get("title") or "")}'
                      f'{quota}</div>'
                      f'<div class="meta">{html.escape(loc)}</div></li>')
        desc = (o.get("description") or
                f"{o['name']} sells into {o.get('sector') or 'state and local government'}.")
        nq = o.get("quota_roles") or 0
        line = (f"{o['open_roles']} open role{'s' if o['open_roles'] != 1 else ''}"
                + (f", {nq} of them quota-carrying" if nq else ""))
        board_link = ""
        if o.get("board_url"):
            board_link = (f'<p><a class="cta" href="{html.escape(o["board_url"])}" '
                          f'rel="nofollow noopener" target="_blank">'
                          f'Open their hiring board &rarr;</a></p>')
        body = (f'<h1>{html.escape(o["name"])}</h1>'
                f'<p class="kv">{facts}</p>'
                f'<p>{html.escape(desc)}</p>'
                + (f'<p class="kv"><a href="{html.escape(o["website"])}" '
                   f'rel="nofollow noopener">{html.escape(o["website"])}</a></p>'
                   if o.get("website") else "")
                + f'<h2>{line}</h2><ul>{items}</ul>'
                + (f'<p class="kv">Showing the first {len(shown)}, sales roles '
                   f'first. {hidden} more are on the board.</p>' if hidden else "")
                + board_link
                + f'<div class="note">Listed on {html.escape(brand["name"])}, which '
                  f'tracks sales roles at state and local government technology '
                  f'companies. <a href="/?co={urllib.parse.quote(o["id"])}">See this '
                  f'company in the board</a>, where the roles are filterable and '
                  f'kept current.</div>')
        (d / f"{o['id']}.html").write_text(_page(
            f"{o['name']} is hiring &middot; {brand['name']}".replace("&middot;", "·"),
            f"{desc} {line}.", f"{site}/c/{o['id']}.html", body, brand, "companies"))
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
        quota = sum(1 for p_ in ps if p_.get("quota_carrying"))
        items = ""
        for co, rs in sorted(cos.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            cid = rs[0]["company_id"]
            titles = ", ".join(sorted({r.get("title") or "" for r in rs})[:4])
            more = f" and {len(rs) - 4} more" if len(rs) > 4 else ""
            items += (f'<li><div class="role"><a href="/c/{urllib.parse.quote(cid)}.html">'
                      f'{html.escape(co)}</a></div>'
                      f'<div class="meta">{html.escape(titles)}{more}</div></li>')
        line = (f"{len(ps)} open role{'s' if len(ps) != 1 else ''} across "
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
            f"{site}/s/{st.lower()}.html", body, brand, "jobs"))
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
                      f"DESCRIPTION:{_ics_esc(str(c.get('approx_count') or 0))} "
                      f"exhibitors tracked. {site}/?tab=conferences",
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
    hiring = [o for o in board.get("organizations", []) if o.get("open_roles")]
    # /c/<id>.html, not ?co=. Both addresses show the same company, so one of
    # them has to be the canonical or they compete with each other; the static
    # page is the one with the facts in its HTML, which is what a crawler that
    # never runs JavaScript can actually read.
    for o in sorted(hiring, key=lambda x: -(x.get("open_roles") or 0)):
        urls.append((f"{site}/c/{urllib.parse.quote(o['id'])}.html", "weekly", "0.6"))
    for st in sorted({(p_.get("office") or {}).get("state")
                      for p_ in board.get("postings", [])
                      if p_.get("family") in ("gtm", "field")} - {None, ""}):
        urls.append((f"{site}/s/{st.lower()}.html", "weekly", "0.6"))
    for c in board.get("conferences", []) or []:
        tag = c.get("event_tag") or c.get("conference")
        if tag:
            urls.append((f"{site}/?tab=conferences&ev={urllib.parse.quote(tag)}",
                         "monthly", "0.5"))
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

    brand = json.loads((ROOT / "data" / "brand.json").read_text())
    crawl = write_crawl_files(out, board, brand)
    meta_idx = write_meta_index(out, board)
    n_co = write_company_pages(out, board, brand)
    n_st = write_state_pages(out, board, brand)
    feeds = write_feeds(out, board, brand)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {a.out}/: {len(SHIP)} page(s) + data/board.json")
    print(f"  {len(board['postings'])} postings, "
          f"{len(board['organizations'])} organizations")
    print(f"  {stripped} internal error string(s) replaced with the plain fact")
    print(f"  sitemap.xml: {crawl['urls']} urls ({crawl['companies']} companies "
          f"with an opening), robots.txt, 404.html")
    print(f"  c/: {n_co} prerendered company pages")
    print(f"  s/: {n_st} state pages")
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
