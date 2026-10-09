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
    return;
  }}
  /* A CODE, FOR SOMEBODY THE OWNER HAS MET. Access plus the Users board is
     how the owner and anyone he has enrolled get in; a code is how everyone
     else does, without being added to an Access policy first.

     IT GATES THIS PAGE AND NOTHING ELSE. Nobody's resume, fact bank or
     employment history sits behind it - job-hunter's server binds 127.0.0.1
     deliberately - so the worst a forged localStorage flag buys is a look at
     this paragraph. The day there is data behind it, this is NOT what should
     be standing in front. */
  const KEY = "sledjobs.beta.v1";
  let held = null;
  try {{ held = JSON.parse(localStorage.getItem(KEY) || "null"); }} catch (e) {{}}
  const inBeta = (rec) => {{
    body.innerHTML = '<div class="in"><b>You are on the beta list.</b> '
      + 'Redeemed ' + esc(rec.on || "") + '.<br><br>' + esc(rec.consent_text || "")
      + '</div>';
  }};
  const esc = (t) => String(t).replace(/[&<>"]/g, c =>
    ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }}[c]));
  if (held && held.code) return inBeta(held);

  body.innerHTML =
    '<div class="in">The beta is closed. If the owner gave you a code, put it in.</div>'
    + '<p><label for="bc">Beta code</label><br>'
    + '<input id="bc" placeholder="JH-XXXX-XXXX" autocomplete="off" spellcheck="false"'
    + ' style="font:inherit;padding:8px;width:220px;text-transform:uppercase">'
    + ' <button id="bgo" style="font:inherit;padding:8px 14px">Redeem</button></p>'
    + '<p class="kv" id="bmsg"></p>';
  const go = document.getElementById("bgo"), inp = document.getElementById("bc"),
        msg = document.getElementById("bmsg");
  go.onclick = async () => {{
    const code = (inp.value || "").trim().toUpperCase();
    if (!code) return;
    go.disabled = true; msg.textContent = "Checking\u2026";
    let r = {{}};
    try {{
      const res = await fetch("/api/beta", {{ method: "POST",
        headers: {{ "content-type": "application/json" }},
        body: JSON.stringify({{ code }}) }});
      r = await res.json();
    }} catch (e) {{ r = {{ error: "unreachable" }}; }}
    go.disabled = false;
    if (r.ok) {{
      const rec = {{ code, on: r.on, consent_text: (r.consent || {{}}).text }};
      try {{ localStorage.setItem(KEY, JSON.stringify(rec)); }} catch (e) {{}}
      return inBeta(rec);
    }}
    /* EVERY REFUSAL SAYS WHICH ONE IT IS. "That did not work" makes somebody
       retype a code that is spent, and makes a real typo indistinguishable
       from a code the owner revoked. */
    msg.textContent =
      r.why === "already_redeemed" ? "That code has already been used"
        + (r.on ? " (on " + r.on + ")" : "") + ". Codes are one per person \u2014 "
        + "ask the owner for another."
      : r.why === "not_a_code" ? "That is not a code we minted, or it was revoked."
      : r.error === "bad_code" ? "That does not look like a code. They read JH-XXXX-XXXX."
      : r.error === "too_many" ? "Too many tries today. Try again tomorrow."
      : r.error === "not_configured" ? "Redeeming is not switched on yet."
      : "Could not reach the server. Nothing was sent.";
  }};
  inp.addEventListener("keydown", e => {{ if (e.key === "Enter") go.click(); }});
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
    # WHAT HAS ALREADY BEEN RULED, shipped behind the same Access door.
    #
    # admin-web read these four files from raw.githubusercontent.com, which
    # works only while the repository is PUBLIC: on a private repo every fetch
    # 404s, the page's own .catch() turns that into {}, and every row a person
    # already ruled comes back on the next reload with nothing to say it had
    # been answered. That made repo visibility a load-bearing part of the
    # admin, which it should never have been.
    #
    # They ship here rather than to /data/ because /admin/ is behind Access
    # and /data/ is not: a scope ruling is a judgement about a company, and
    # judgements belong on the side of the door where people sign in.
    # AND THE DISMISSALS. "Bucket is right", "Not duplicates" and "Cannot find
    # it" all write admin_dismissed.json, which was not shipped, so a
    # dismissed row could never be hidden by what the page knew - it left
    # only when the queue itself recomputed at the next build.
    rulings = {}
    for name in ("vendor_scope_decisions", "placement_rulings",
                 "web_merge_rulings", "web_founded_rulings", "admin_dismissed"):
        f = ROOT / "data" / f"{name}.json"
        try:
            rulings[name] = json.loads(f.read_text()) if f.exists() else {}
        except (json.JSONDecodeError, OSError):
            rulings[name] = {}
    # WHO HAS ACCESS, for the phone's Users tab: handles and roles only. The
    # hash lives in /admin/users.json for whoami.js; the page has no use for it.
    payload["users"] = [{"handle": h, "roles": list(u.get("roles") or []),
                         "label": u.get("label") or "", "revoked_on": u.get("revoked_on")}
                        for h, u in (users or {}).items() if isinstance(u, dict)]
    (admin_dir / "rulings.json").write_text(json.dumps(rulings))
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

# KEYS index.html NEVER READS, measured 2026-09-18 against the shipped
# public/data/board.json. 760,538 B raw, and only 20.8 KB gzipped - they are
# mostly repeated "key":null, so this is a PARSE cost on the main thread
# rather than a transfer cost, which is the honest way to describe it.
#
# TWO KEYS WERE ON THIS LIST AND ARE NOT NOW, and the reason is the whole
# risk of the change. sanitize() mutates board_src IN PLACE (no deep copy),
# and write_company_pages runs AFTER it, so anything stripped here is also
# gone from the 812 static /c/ pages built from the same object:
#   `claimed` is read at index.html:5372 (`const claimed=o.claimed`) and at
#     build_site.py:1646 - it renders "this page is claimed by the company".
#   `acquired` is read by _co_acquired() at build_site.py:1318, reached from
#     company_page_html - it renders "Part of X, acquired YYYY".
# Dropping either would have blanked a real sentence on a public page with
# nothing erroring. Anything added here must be checked against BOTH readers.
# offtopic_dropped and federal_dropped SHIP, since 2026-10-07. Stripped, a
# company whose every posting this board leaves out (Granicus: 2 federal and
# 52 off-topic; OpenGov: 71 off-topic) printed "Their board is one we read
# every night and it is empty right now" - a false empty about a board with
# dozens of jobs on it. Two small ints on the companies that have them.
# checked_by_hand SHIPS, since 2026-10-08: a captured posting's page says
# when a person last re-read the page it came from (index.html byHandText),
# and without it "not re-checked since" stays after a re-read.
DROP_ORG_DEAD = {
    "board_owner_unverified", "shares_board_with",
    "board_owner", "quota_postings", "open_postings",
    "sled_only", "linkedin", "ats_note",
}
DROP_POSTING = {"opening_locations", "opening_postings", "captured_from"}


def sanitize(board: dict) -> dict:
    """Strip debugging detail out of the copy that goes public."""
    stripped = 0
    for o in board.get("organizations", []):
        for k in DROP_ORG:
            o.pop(k, None)
        for k in DROP_ORG_DEAD:
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
        # ats_note (internal review notes, e.g. "cleared on audit: quorum.com
        # sells disaster recovery, not government affairs software") is popped
        # by DROP_ORG_DEAD above. It used to be popped INSIDE `if
        # o.get("ats_note")`, so a row whose note was null or "" kept the key
        # and 2,019 of 2,044 organizations shipped "ats_note":null.
    for pg in board.get("postings", []):
        for k in DROP_POSTING:
            pg.pop(k, None)
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


# --- the company detail, read back for the STATIC pages ----------------------
#
# build_board splits `news` and `profile` out of board.json into
# data/detail/<id>.json, because together they are 57% of a payload every
# visitor downloads and are read only when somebody opens ONE company. The
# app re-joins them with a second fetch. THE STATIC PAGES HAVE TO RE-JOIN
# THEM TOO, and for three weeks they did not: has_static_page and _co_about
# both read o["profile"], the split emptied it, and every prerendered page
# said the write-up was "not on file for this company yet" while 657 of them
# sat in data/detail/. The app looked right, so nothing contradicted it -
# and the prerendered pages are the ones a crawler reads.
_DETAIL_CACHE: dict = {}


def detail_for(cid: str) -> dict:
    """{news, profile} for one company, or {} - cached, read once each."""
    if cid in _DETAIL_CACHE:
        return _DETAIL_CACHE[cid]
    f = ROOT / "data" / "detail" / f"{cid}.json"
    got = {}
    if f.exists():
        try:
            got = json.loads(f.read_text()) or {}
        except (ValueError, OSError):
            got = {}
    _DETAIL_CACHE[cid] = got
    return got


def with_detail(o: dict) -> dict:
    """The org as the app sees it once its detail has loaded.

    A shallow copy: the board that SHIPS must stay split, so nothing here
    may write news or profile back onto the organization.
    """
    d = detail_for(o.get("id") or "")
    if not d:
        return o
    merged = dict(o)
    for k in ("news", "profile"):
        if d.get(k) is not None and merged.get(k) is None:
            merged[k] = d[k]
    return merged


def has_static_page(o: dict) -> bool:
    """One answer, used by the page writer, the meta index and the sitemap.

    Three places used to ask "open_roles?" separately; the day one of them
    grew a second condition the other two would have gone stale, and a
    sitemap listing a page that was never written is a 404 submitted to
    Google as canonical. A company gets a page when it has something a
    crawler cannot get from the app: open roles, a write-up (sourced by the
    agent door, or written by hand by the owner), or a researched shortlist.
    """
    o = with_detail(o)
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
            # A CITY IS NOT A COUNTRY. The note above states the rule this
            # code did not keep: assert addressCountry "US" only where a real
            # US STATE parsed. A city alone was enough to emit the block, and
            # the Worker stamps US onto whatever it gets - so Leon (Spain),
            # Kitchener (Ontario), London (England) and Buenos Aires went to
            # Google as United States jobs. 291 of 309 city-only blocks were
            # flagged is_us false by this board's own record while its
            # structured data said otherwise.
            #
            # Google for Jobs is, in _middleware's own words, "a channel that
            # punishes a lie". A city with no state is a place we could not
            # put in a country, and the honest block is no block: the posting
            # keeps its title, description and canonical, and falls through to
            # TELECOMMUTE below if the employer said remote.
            if off.get("state"):
                r["st"] = off["state"]
                if off.get("city"):
                    r["ci"] = off["city"]
            if not (r.get("ci") or r.get("st")):
                # REMOTE, AND FROM WHERE. Google requires a remote posting to
                # name at least one country an applicant may work from; the
                # Worker states US, so only a posting this board's own record
                # calls US (is_us True) may carry it. 283 remote blocks were
                # "Remote - UK" or a bare "Remote" (2026-10-08).
                if p_.get("work_mode") == "remote" and p_.get("is_us") is True:
                    r["tc"] = 1          # jobLocationType: TELECOMMUTE, in the US
                else:
                    r.pop("ld", None)    # no location we can state: no block
            # THE EMPLOYER'S OWN DATE, now that the board has one. Shipped
            # only when the board we read published it - `posted` is absent on
            # every row where it did not, and there is no fallback. See the
            # note above for why first_seen can never fill this.
            if p_.get("posted"):
                r["pd"] = p_["posted"]
            else:
                # datePosted IS REQUIRED (Google's JobPosting docs, checked
                # 2026-10-08), and the only other date is our crawl date. No
                # employer date, no block - 174 were going out without one.
                r.pop("ld", None)
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
                        # q: quota-carrying roles, so a link preview can say
                        # "sales roles" only about sales roles. It said "N open
                        # sales roles" with N = every open role (Aurelian: 8,
                        # all engineering and product; launch audit 2).
                        **({"q": o["quota_roles"]} if o.get("quota_roles") else {}),
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
    # WHICH CLAIMS THE OWNER LET THROUGH, so the claim endpoint can tell a
    # verified claimant their edit goes live instead of promising them a
    # review that no longer happens. TAILS ONLY - six characters, which is
    # what sync_claims already writes into this public repo beside every
    # proposal. The full token is the whole of a claimant's identity here and
    # is never published, never logged and never leaves KV.
    #
    # THIS FILE AUTHORISES NOTHING. It is a message: whether an edit may
    # actually land unreviewed is decided in sync_claims, from the employer
    # log, on the repo's own side. Published so a claimant sees the truth
    # about what will happen, not so anything downstream can skip the check.
    try:
        import employer_log
        ok = employer_log.verified_claims()
    except Exception as e:                                  # noqa: BLE001
        # NOT SILENT. Falling back to {} tells every verified company that a
        # person reviews their edits, which stopped being true when the owner
        # verified them - so the failure has to be visible in the build log
        # rather than showing up as claimants waiting for a review that is
        # not coming. The build still completes: a missing badge is a smaller
        # loss than no site.
        print(f"  meta-claims: could not read the employer log ({e}); every "
              f"claimant will be told a person reviews their edits",
              file=sys.stderr)
        ok = {}
    (out / "meta-claims.json").write_text(
        json.dumps({"generated": gen, "verified": ok}, separators=(",", ":")))
    return {"roles": len(roles), "companies": len(cos), "verified": len(ok)}


def _default_css(brand: dict) -> str:
    """The stylesheet of the state and conference pages, in the brand's own
    tokens. The company page brings its own (COPAGE_CSS)."""
    p_ = brand["palette"]
    return f"""
 :root{{--bg:{p_['ice']['hex']};--panel:{p_['belly']['hex']};--ink:{p_['penguin']['hex']};
   --line:{p_['frost']['hex']};--dim:{brand['derived']['deep_fog']['hex']};
   --link:{p_['badge']['hex']};--beak:{p_['beak']['hex']};
   --on-link:{p_['ice']['hex']}}}
 @media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:{p_['penguin']['hex']};--panel:#262E42;
   --ink:{p_['ice']['hex']};--line:#39435C;--dim:#A8BCCA;
   --link:{brand['derived']['dark']['badge']['hex']};
   --on-link:{p_['penguin']['hex']}}}}}
 :root[data-theme=dark]{{--bg:{p_['penguin']['hex']};--panel:#262E42;
   --ink:{p_['ice']['hex']};--line:#39435C;--dim:#A8BCCA;
   --link:{brand['derived']['dark']['badge']['hex']};
   --on-link:{p_['penguin']['hex']}}}
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
# The exhibitor roster on an /e/ page. IACP alone carries 164.
CO_ROSTER_CAP = 250
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
# THE STATIC CONFERENCE PAGE IS THE APP'S CONFERENCE PANEL, for the same
# reason the company page is the app's company page: a conference read as two
# different products depending on whether somebody clicked a row or followed a
# link is two things to keep in step, and they do not stay in step. What
# follows is cfPanelHTML() ported - the same sections in the same order, the
# same class names, the same copy per state. The panel's own stylesheet is
# carried whole below, minus the two rules that only make sense as an overlay:
# the fixed wrapper and the close button.
#
# The app's spacing tokens are not defined on these pages, so they are
# declared on :root here. --beak comes from the site sheet above.
CFPAGE_CSS = """
  /* ── turn 3: the conference page proper ─────────────────────────────────
     THREE STATES, ONE PAGE. The design file draws them as 3g (swept, people
     hiring), 3e (dates known, floor unread - "the 800-page case") and 3h (no
     dates announced). They are not three templates: the header, the facts
     table, the doors, "Run by" and "Where this came from" are identical, and
     what changes is the one section in the middle that answers "who is on
     this floor". Sections with no data are DROPPED rather than rendered
     empty, which is what keeps state 3 from reading as a broken state 1.

     Every colour is a token from the kit, so both themes come along. */
  .cfx{max-width:900px;margin:0 auto;padding:0 var(--space-4) 64px}
  .cfx-crumb{font-size:13px;color:var(--dim);margin:var(--space-6) 0 var(--space-4)}
  .cfx-crumb a{color:var(--link);text-decoration:none}
  .cfx-crumb a:hover{text-decoration:underline}
  .cfx-eyebrow{display:flex;align-items:center;gap:var(--space-3);
    font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
    color:var(--faint)}
  .cfx-flag{color:var(--ink);border:1px solid var(--line);padding:2px 7px}
  .cfx h1{font-family:var(--font-heading);font-weight:800;font-size:31px;
    letter-spacing:-.02em;line-height:1.12;margin:var(--space-2) 0 var(--space-3);
    text-wrap:balance}
  .cfx-state{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;
    font-weight:600;border:1px solid var(--line);padding:4px 10px;
    color:var(--dim);margin-bottom:var(--space-4)}
  .cfx-state i{width:8px;height:8px;background:var(--beak);flex:none}
  .cfx-state.read i{background:var(--link)}
  .cfx-lede{font-size:16px;line-height:1.55;margin:0 0 var(--space-6);
    color:var(--ink)}
  .cfx-lede b{font-weight:700}
  /* the facts table - a label column and a value column, nothing else */
  .cfx-facts{display:grid;grid-template-columns:150px 1fr;gap:2px var(--space-4);
    margin:0 0 var(--space-6);font-size:14.5px}
  .cfx-facts dt{font-size:11px;font-weight:700;letter-spacing:.07em;
    text-transform:uppercase;color:var(--faint);padding-top:5px}
  .cfx-facts dd{margin:0;padding:3px 0;border-bottom:1px solid var(--line)}
  .cfx-doors{display:flex;flex-wrap:wrap;gap:var(--space-2);margin:0 0 var(--space-8)}
  .cfx-door{display:inline-block;padding:11px 18px;font-size:14px;font-weight:600;
    text-decoration:none;border:1px solid var(--line);color:var(--ink)}
  .cfx-door.lead{background:var(--link);border-color:var(--link);
    color:var(--on-link)}
  .cfx-door:hover{border-color:var(--link);color:var(--link)}
  .cfx-door.lead:hover{color:var(--on-link);opacity:.92}
  .cfx-sec{margin:0 0 var(--space-8)}
  .cfx-sec h2{font-size:11px;font-weight:700;letter-spacing:.08em;
    text-transform:uppercase;color:var(--faint);margin:0 0 var(--space-3);
    padding-bottom:6px;border-bottom:1px solid var(--line);
    display:flex;justify-content:space-between;align-items:baseline;gap:var(--space-3)}
  .cfx-sec h2 span{font-weight:600;letter-spacing:0;text-transform:none;font-size:13px}
  /* THE HONEST PANEL. State 3's whole job, and it is prose rather than a
     table because it is a statement about us. */
  .cfx-note{border-left:3px solid var(--beak);padding:2px 0 2px var(--space-4);
    font-size:14.5px;line-height:1.6;color:var(--ink)}
  .cfx-note p{margin:0 0 var(--space-3)}
  .cfx-note p:last-child{margin:0}
  .cfx-roster{width:100%;border-collapse:collapse;font-size:14px}
  .cfx-roster th{text-align:left;font-size:11px;font-weight:700;
    letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
    padding:0 var(--space-3) 7px 0;border-bottom:1px solid var(--line)}
  .cfx-roster td{padding:9px var(--space-3) 9px 0;border-bottom:1px solid var(--line);
    vertical-align:top}
  .cfx-roster td.n{text-align:right;font-variant-numeric:tabular-nums;font-weight:700}
  .cfx-roster a{color:var(--link);text-decoration:none}
  .cfx-roster a:hover{text-decoration:underline}
  .cfx-more{font-size:13px;color:var(--dim);margin-top:var(--space-3)}
  .cfx-org{font-size:14.5px;line-height:1.5}
  .cfx-org a{font-weight:700;color:var(--link);text-decoration:none;font-size:16px}
  .cfx-org .meta{color:var(--dim);font-size:13px;margin-top:3px}
  .cfx-next{display:grid;gap:var(--space-2)}
  .cfx-next a{display:flex;justify-content:space-between;gap:var(--space-4);
    text-decoration:none;color:var(--ink);border-bottom:1px solid var(--line);
    padding:9px 0;font-size:14px}
  .cfx-next a:hover .nm{color:var(--link)}
  .cfx-next .when{color:var(--dim);font-size:13px;flex:none;
    font-variant-numeric:tabular-nums}
  .cfx-src{font-size:13px;color:var(--dim);line-height:1.6}
  @media (max-width:640px){
    .cfx h1{font-size:25px}
    .cfx-facts{grid-template-columns:1fr;gap:0}
    .cfx-facts dt{padding-top:var(--space-3)}
    .cfx-door{width:100%;text-align:center}
  }
 :root{--space-2:8px;--space-3:12px;--space-4:16px;--space-6:24px;
   --accent:var(--link);--faint:var(--dim)}
  /* the panel */
  .cfp-wrap{position:fixed;inset:0;z-index:60;background:rgba(15,22,36,.45);
    display:flex;justify-content:flex-end;overflow-y:auto}
  body.cfp-lock{overflow:hidden}
  .cfp-card{position:relative;background:var(--bg);color:var(--ink);
    width:min(560px,100%);min-height:100%;padding:var(--space-6);
    display:flex;flex-direction:column;gap:var(--space-3)}
  .cfp-x{position:absolute;top:var(--space-4);right:var(--space-4);width:36px;
    height:36px;border-radius:50%;border:1px solid var(--line);
    background:var(--panel);color:var(--ink);font-size:18px;cursor:pointer}
  .cfp-name{font-family:var(--font-heading);font-weight:800;font-size:26px;
    margin:0;padding-right:44px;display:flex;align-items:center;gap:8px;
    flex-wrap:wrap}
  .cfp-org,.cfp-when{margin:0;font-size:14px;color:var(--dim)}
  .cfp-acts{display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap;
    margin:var(--space-2) 0}
  .cfp-go{background:var(--accent);color:var(--on-link);text-decoration:none;
    font-weight:700;font-size:14px;padding:10px 18px;border-radius:999px}
  .cfp-alt{font:inherit;font-size:14px;font-weight:600;padding:10px 18px;
    border-radius:999px;border:1px solid var(--line);background:var(--panel);
    color:var(--ink);cursor:pointer}
  .cfp-src{margin-left:auto;font-size:12px;color:var(--dim);text-align:right}
  .cfp-chips{display:flex;gap:8px;flex-wrap:wrap}
  .cfchip{font-size:12.5px;background:var(--panel);border:1px solid var(--line);
    border-radius:999px;padding:6px 12px;color:var(--dim)}
  .cfchip b{color:var(--ink)}
  .cfp-sec{background:var(--panel);border:1px solid var(--line);
    border-radius:14px;padding:var(--space-4)}
  .cfp-sec h4{margin:0 0 var(--space-3);font-size:11px;letter-spacing:.09em;
    text-transform:uppercase;color:var(--faint);display:flex;
    justify-content:space-between;align-items:center}
  .cfp-all{font-size:12px;text-transform:none;letter-spacing:0;font-weight:700}
  .cfp-roster{list-style:none;margin:0;padding:0;display:grid;gap:10px;
    grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
  .cfp-roster li{display:flex;align-items:center;gap:8px;font-size:14px}
  .cfp-roster em{font-style:normal;font-size:11px;color:var(--accent);
    margin-left:auto}
  .cfp-note{margin:var(--space-3) 0 0;font-size:12.5px;color:var(--dim)}
  .cfp-rate .cfrate{border:0;padding:0}

  .cfflag{font-size:11px;letter-spacing:.08em;text-transform:uppercase;
    font-weight:700;color:var(--bg);background:var(--dim);padding:1px 5px}
  .cfnow{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
    font-weight:700;color:var(--ink);background:transparent;padding:1px 5px;
    border:2px solid var(--beak);white-space:nowrap}
  .cfpast{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
    font-weight:700;color:var(--panel);background:var(--dim);padding:1px 5px;
    white-space:nowrap}
  .cfface{display:grid;place-items:center;width:26px;height:26px;
    border-radius:50%;background:var(--ink);color:var(--panel);
    font-size:9.5px;font-weight:700;margin-left:-7px;
    border:2px solid var(--panel);flex:0 0 auto}
  .cfzero{color:var(--dim)}
 /* -- the two differences, stated rather than edited into the rules above --
    The panel's stylesheet is carried WHOLE and on purpose: an edited copy is
    a copy that drifts. What a page is and a drawer is not goes here.

    The card is not an overlay. It has no fixed wrapper to sit in, so it
    takes a measure and centres, and it stops being full height. .cfp-wrap,
    body.cfp-lock and .cfp-x have no element on this page at all - left in
    place rather than deleted so a diff against the app's sheet stays
    readable. */
 .cfp-card{width:auto;max-width:660px;margin:0 auto;min-height:0;
   padding:28px 22px 70px}
 .cfp-name{font-size:30px;padding-right:0}
 .cfp-sec h2{margin:0 0 var(--space-3);font-size:11px;letter-spacing:.09em;
   text-transform:uppercase;color:var(--faint)}
 .cfp-roster a{color:var(--link)}
 .cfp-card .note{margin-top:var(--space-4)}
"""



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
 .paynone{color:var(--dim)}
 .copage{--c-bg:#FAF7F0;--c-rule:#C9DCE8;--c-stroke:#1F2536;--c-ink:#1F2536;--c-ink2:#556F82;--c-ink3:#7C97AA;--c-accent:#C1341F;--c-accent-text:#C1341F;--c-link:#0B57C4}
 @media (prefers-color-scheme:dark){:root:not([data-theme=light]) .copage{--c-bg:#151B29;--c-rule:#2E3A50;--c-stroke:#4A5C71;--c-ink:#E8F1F7;--c-ink2:#A9C3D4;--c-ink3:#7C97AA;--c-accent:#C1341F;--c-accent-text:#E4634A;--c-link:#9CC3FF}}
 :root[data-theme=dark] .copage{--c-bg:#151B29;--c-rule:#2E3A50;--c-stroke:#4A5C71;--c-ink:#E8F1F7;--c-ink2:#A9C3D4;--c-ink3:#7C97AA;--c-accent:#C1341F;--c-accent-text:#E4634A;--c-link:#9CC3FF}
 .copage{background:var(--c-bg);color:var(--c-ink);margin:0;padding:0 20px 48px;min-height:70vh}
 .copage a{color:var(--c-link)}
 .cowrap{max-width:1000px;margin:0 auto}
 .cocrumb{font-size:11px;line-height:1.5;color:var(--c-ink2);padding:14px 0 10px;font-variant-numeric:tabular-nums}
 .cocrumb a{color:var(--c-ink2);text-decoration:none}
 .cocrumb a:hover{text-decoration:underline}
 .cocrumb .sep{padding:0 7px;color:var(--c-rule)}
 .coid{display:flex;gap:16px;align-items:flex-start;padding:2px 0 18px}
 .coid .logo{width:56px;height:56px;flex:none;display:grid;place-items:center;font:800 22px/1 var(--font-heading);background:#E8F1F7;color:#1F2536;position:relative;overflow:hidden}
 .coid .logo img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#fff;padding:4px}
 .coid .logo img.ondark{background:var(--hdr-bg)}
 .coid h1{font-size:30px;line-height:1;letter-spacing:-.025em;margin:0 0 8px}
 .cometa{font-size:11px;line-height:1.5;color:var(--c-ink2);font-variant-numeric:tabular-nums}
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
 .costrip .v.dim{color:var(--c-ink2)}
 .costrip .v.acc{color:var(--c-accent-text)}
 .costrip dt{font:800 9.5px/1 var(--font-heading);letter-spacing:.16em;text-transform:uppercase;color:var(--c-ink3);margin:10px 0 7px}
 .costrip dd{margin:0;font-size:11px;line-height:1.5;color:var(--c-ink2);font-variant-numeric:tabular-nums}
 .cobody{display:flex;gap:40px;align-items:flex-start}
 .cocol{flex:1;min-width:0}
 .corail{width:262px;flex:none}
 .cosec{margin:0 0 30px}
 .cosec>h2{font:800 15px/1 var(--font-heading);display:inline}
 .cosec .smeta{font-size:11px;color:var(--c-ink2);padding-left:10px}
 .cosechd{display:flex;align-items:baseline;gap:0;border-bottom:1px solid var(--c-rule);padding-bottom:9px;margin-bottom:14px}
 .coabout p{font-size:13px;line-height:1.65;max-width:560px;margin:0 0 13px;color:var(--c-ink)}
 .coabout .more{color:var(--c-ink2)}
 .conews{max-height:262px;overflow:auto;position:relative}
 .conewsw{position:relative}
 .conewsw .fade{position:absolute;left:0;right:0;bottom:0;height:54px;pointer-events:none;background:linear-gradient(transparent,var(--c-bg))}
 .cochip{font:800 9.5px/1 var(--font-heading);letter-spacing:.1em;text-transform:uppercase;padding:6px 9px;border:1px solid var(--c-rule);color:var(--c-ink3);display:inline-block}
 .conews .row{display:flex;gap:0;padding:11px 0;border-bottom:1px solid var(--c-rule);align-items:baseline}
 .conews .d{width:62px;flex:none;font-size:11px;color:var(--c-ink2);font-variant-numeric:tabular-nums}
 .conews .k{width:84px;flex:none;font:800 9.5px/1 var(--font-heading);letter-spacing:.16em;text-transform:uppercase;color:var(--c-ink2)}
 .conews .k.contract{color:var(--c-accent-text)}
 .conews .t{flex:1;min-width:0;font-size:12.5px;line-height:1.5}
 .cogrp{margin:0 0 14px}
 .cogrph{display:flex;align-items:baseline;gap:10px;padding:9px 0;border-bottom:1px solid var(--c-rule)}
 .cogrph h3{font:800 13px/1 var(--font-heading)}
 .cogrph .n{font-size:11px;color:var(--c-ink2);margin-left:auto}
 .corow{display:flex;align-items:baseline;padding:9px 0;border-bottom:1px solid var(--c-rule);font-size:12.5px;line-height:1.5}
 .corow .ti{flex:1;min-width:0;font-weight:800}
 .corow .lo{width:120px;flex:none;color:var(--c-ink2);font-size:11px}
 .corow .pa{width:118px;flex:none;color:var(--c-ink2);font-size:11px;font-variant-numeric:tabular-nums}
 .corow .ag{width:52px;flex:none;color:var(--c-ink2);font-size:11px;font-variant-numeric:tabular-nums}
 .corow .sv{width:46px;flex:none;text-align:right}
 .comore{font-size:11px;padding:9px 0;display:inline-block}
 .coempty{display:flex;gap:20px;align-items:flex-start;padding:22px 0 4px}
 .coempty img{width:96px;flex:none;opacity:.85}
 .coempty h3{font:800 15px/1.2 var(--font-heading);margin:0 0 8px}
 .coempty p{font-size:12.5px;line-height:1.6;color:var(--c-ink2);max-width:52ch;margin:0 0 14px}
 .corail section{margin:0 0 26px}
 .corail h2{font:800 9.5px/1.35 var(--font-heading);letter-spacing:.16em;text-transform:uppercase;color:var(--c-ink3);border-bottom:1px solid var(--c-rule);padding-bottom:8px;margin-bottom:4px}
 .corail .r{display:flex;align-items:baseline;gap:8px;padding:9px 0;border-bottom:1px solid var(--c-rule);font-size:12.5px;line-height:1.45}
 .corail .r .d{display:block;font-size:11px;color:var(--c-ink2);margin-top:3px}
 .corail .r .n{margin-left:auto;font-size:11px;color:var(--c-ink2);font-variant-numeric:tabular-nums;flex:none}
 .corail .tag{margin-left:auto;font:800 9.5px/1 var(--font-heading);letter-spacing:.14em;text-transform:uppercase;color:var(--c-accent-text);flex:none}
 .coacq .acqhead{font-size:14px;font-weight:700;margin:0 0 6px}
 .coacq p{font-size:12.5px;line-height:1.6;color:var(--c-ink2);margin:0 0 6px}
 .corail .b{padding:11px 0;border-bottom:1px solid var(--c-rule)}
 .corail .b:last-of-type{border-bottom:0}
 .corail .bh{display:flex;align-items:baseline;gap:8px;font-size:12.5px;font-weight:600}
 .corail .bh .n{margin-left:auto;font-size:11px;color:var(--c-ink2)}
 .corail .does,.corail .deal{font-size:11px;line-height:1.55;color:var(--c-ink2);
   margin:5px 0 0}
 .corail .deal{color:var(--c-ink2)}
 .corail .src-l{font-size:11px;margin:5px 0 0}
 .corail .note{font-size:11px;line-height:1.5;color:var(--c-ink2);padding-top:9px}
 .corail .all{font-size:11px;padding-top:9px;display:inline-block}
 .coprov{font-size:10.5px;line-height:1.5;color:var(--c-ink2)}
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


def _co_record_days(mine: list, now: dt.date,
                    since: "str | None" = None) -> "int | None":
    """How far back the record goes for this company, in days, or None when
    nothing here carries a date. A window longer than the record measures
    us, not them - see coRecordDays in index.html. `since` is the org's
    `read_since`: our snapshots can go back further than its open postings."""
    first = _co_date(since) if since else None
    for p in mine:
        t = _co_date(p.get("first_seen"))
        if t and (first is None or t < first):
            first = t
    return None if first is None else (now - first).days


def board_state(o: dict) -> str:
    """'read', 'unread' or 'none': what a count of 0 on this company means.

    ONLY 'read' MAKES A ZERO A FACT. Until 2026-10-06 the company page asked
    only "enumerable is not False", which is True for the 862 companies with
    no board on file (refresh skips ats=unknown), so each of them printed a
    large "0 open roles", "none seen recently" and "Their board is one we read
    every night and it is empty right now" - about a board that does not
    exist. The 754 unreadable boards printed the same 0 over "not measured".
    That is the false "None found" this repository refuses everywhere else.
    index.html's boardState() is the same rule, and selftest holds the two."""
    if o.get("no_board_on_file"):
        return "none"
    if o.get("enumerable") is False or o.get("unreadable"):
        return "unread"
    return "read"


def _posts_at_phrase(pa: dict) -> str:
    """Where a company with no board says it hires, as a clause. Mirrors the
    cases index.html's postsAtSentence() separates."""
    where = pa.get("where")
    if where == "email":
        return "openings go out by email"
    if where == "recruiter":
        return "they hire through an outside recruiter"
    return f"they post on {html.escape(pa.get('label') or 'another site')}"


def scope_note(o: dict) -> str:
    """Why a board we read shows fewer roles than it lists, or "". The board
    is not empty; this board leaves some of it out, and says how many and why.
    index.html's scopeNote() is the same sentence."""
    fed = o.get("federal_dropped") or 0
    off = o.get("offtopic_dropped") or 0
    if not (fed or off):
        return ""
    # WHAT THE FILTER DID, NOT WHERE THE BOARD'S SCOPE ENDS. This said the
    # roles were outside this board's scope, which was false for OpenGov's and
    # Granicus's account executives: they are dropped by the sled_only title
    # filter, which is misapplied on 159 companies pending the owner's ruling
    # (review, 2026-10-07). Postings, because offtopic_dropped counts rows.
    bits = []
    if off:
        bits.append(f"{off} posting{'' if off == 1 else 's'} our public-sector "
                    f"title filter does not keep")
    if fed:
        bits.append(f"{fed} federal posting{'' if fed == 1 else 's'}, which "
                    f"belong{'s' if fed == 1 else ''} on a federal board")
    return "Of what their board lists, this board leaves out " + " and ".join(bits) + "."


def no_board_note(o: dict) -> str:
    """Why a company has no board on file, in the source cell's few words.

    THREE DIFFERENT FACTS, and only one is about them. Discovery found nothing
    ("none-found"), their site turned our reader away ("blocked" - 179
    companies, which said "none found when we looked" about a site that never
    let us look), or nobody has looked yet (no probe). index.html's
    noBoardText() is the same rule (review, 2026-10-07)."""
    on = o.get("board_checked_on")
    probe = o.get("probe")
    if probe == "blocked":
        return "their site turned our reader away" + (f" on {on}" if on else "")
    if not probe:
        return "not looked for yet"
    return "none found when we looked" + (f" on {on}" if on else "")


def open_count(o: dict) -> str:
    """The open-roles figure as printed: a number, or an em dash when nobody
    could count. Roles captured by hand are a floor and still print."""
    n = o.get("open_roles") or 0
    return str(n) if n or board_state(o) == "read" else "&mdash;"


def last_looked(o: dict) -> "str | None":
    """The last day anybody looked at this company's board: the crawl's probe
    or a person's capture, whichever is later. Gainwell's page said "a person
    checks it - last 2026-08-19" over 97 roles a person captured on 09-08
    (launch audit 2, 2026-10-09). index.html lastLooked() is the same rule."""
    days = [str(d) for d in (o.get("board_checked_on"), o.get("checked_by_hand")) if d]
    return max(days) if days else None


def _co_open_note(mine: list, open_: int, readable: bool, now: dt.date,
                  state: str = "", since: "str | None" = None,
                  last: "str | None" = None) -> str:
    if not open_:
        if state == "none":
            return "no board on file to count"
        return "none seen recently" if readable else "not measured"
    span = _co_record_days(mine, now, since)
    if span is not None and span < 30:
        when = "today" if span == 0 else f"{span} day{'' if span == 1 else 's'} ago"
        return f"first read here {when}"
    n = len(_co_roles(mine, now - dt.timedelta(days=30)))
    if n:
        return f"+{n} first read in the last 30 days"
    # a board only a person reads: "none added in 30 days" is a claim about a
    # month nobody may have looked at
    if state == "unread":
        lt = _co_date(last)
        if lt is None or (now - lt).days > 30:
            return f"not re-checked since {last}" if last else "not re-checked in 30 days"
    return "none added in 30 days"


def _co_phase(mine: list, readable: bool, now: dt.date,
              since: "str | None" = None) -> dict:
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
    span = _co_record_days(mine, now, since)
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


def _group_pay_cell(grp: list) -> str:
    """Pay for one opening advertised in several places, said the way the job
    card says it: a figure only when every posting states the same one. The
    row printed the FIRST posting's pay for all of them - one city's band, or
    "no salary stated", for postings that state other figures (launch audit
    2, 2026-10-09). index.html's coGroupPay is the same rule."""
    if len(grp) == 1:
        return _pay_cell(grp[0])
    bits = [_pay_bit(g) for g in grp]
    figs = {b[1] for b in bits if b and b[0] == "pay"}
    stated = sum(1 for b in bits if b and b[0] == "pay")
    if len(figs) > 1:
        return '<span class="paynone" title="the postings state different figures">varies</span>'
    if figs:
        fig = html.escape(next(iter(figs)))
        return (f'<span class="pay">{fig}</span>'
                + (f' <span class="paynone">on {stated} of {len(grp)}</span>' if stated < len(grp) else ""))
    return '<span class="paynone" title="no pay figure on any of these postings">&mdash;</span>'


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


# apply_run_facts.py and apply_transit_run.py stored research notes as a[:400]
DEAL_CEILING = 400


def deal_text(raw) -> str:
    """A stored deal sentence as it may be printed: whole sentences only.

    The research that wrote these cut them at 400 characters, so seven pages
    ended mid-word - Galaxy Digital's on "WAS ACQUIRED by Bette", which cut
    off the very acquisition the section is about - and OpenCounter's
    printed an internal "(Correction: ...)" note (launch audit 2,
    2026-10-09). Nothing is composed: a parenthetical correction note is
    dropped, and a text that stops mid-sentence ends at its last full one.
    """
    cut = len(str(raw or "")) >= DEAL_CEILING
    t = re.sub(r"\s*\(Correction:[^)]*\)", "", str(raw or "")).strip()
    # only a text that reached the research's ceiling was cut; a short one
    # with no closing stop ("Acquired by Poppulo; announced 2025-03-04") is whole
    if not t or not cut or re.search(r"[.!?][\"'\u201d)]*$", t):
        return t
    ends = list(re.finditer(r"[.!?][\"'\u201d)]*(?=\s)", t))
    return t[:ends[-1].end()] if ends else ""


def _co_acquired(o: dict, by_id: dict) -> str:
    """"Part of X" as a block a reader can actually read.

    The parent was already on the page - four words of grey meta type beside
    the founding year. That is the right size for a fact nobody asked about
    and the wrong size for the answer to "who owns this company", which is
    the question a seller asks second. The year and the sentence the ruling
    was made from are both stored; neither was being shown anywhere.
    """
    esc = html.escape
    acq = o.get("acquired") if isinstance(o.get("acquired"), dict) else None
    parent = (acq or {}).get("parent") or o.get("parent")
    if not parent:
        return ""
    who = esc(str(parent))
    hit = next((cid for cid, x in by_id.items()
                if (x.get("name") or "").strip().lower() == str(parent).strip().lower()), None)
    if hit:
        who = f'<a href="{_co_href(hit, by_id)}">{who}</a>'
    # NO YEAR IN THE HEADLINE. The stored `year` is often another deal's:
    # DTN read "acquired 2025" (its own purchase of Tandem Concepts) over a
    # sentence saying TBG AG bought it in 2017, and Sterling Volunteers
    # "acquired 2026" over "No year is given" (launch audit 2, 2026-10-09).
    # The sentence the ruling was made from carries any year it has.
    head = f"Part of {who}"
    said = deal_text((acq or {}).get("deal"))
    deal = f'<p>{esc(said)}</p>' if said else ""
    src = (acq or {}).get("source")
    link = (f'<p class="coprov">{_ext_link(src, "Read the announcement")}</p>'
            if isinstance(src, str) and src.startswith("http") else "")
    if not deal:
        return ""            # a bare parent name is what the meta line already says
    # who ruled, and from what: "their own announcement" sat over SubItUp's
    # "SubItUp's own site makes no mention of it"
    by = str((acq or {}).get("ruled_by") or "")
    on = (acq or {}).get("on")
    said = ("from research, not yet checked by a person"
            if not by or by.startswith("agent")
            else "a person's ruling" + (f" on {esc(str(on))}" if on else "")
            + ", from research")
    return (f'<section class="cosec coacq"><div class="cosechd"><h2>Ownership</h2>'
            f'<span class="smeta">{said}</span></div>'
            f'<p class="acqhead">{head}</p>{deal}{link}</section>')


def _co_about(o: dict, dom: str) -> str:
    """The About section - coAbout, ported. Keys on the SHAPE of profile,
    never on the key: a legacy profile is a reviewer's notes and renders as
    the one-line record, not as a write-up."""
    esc = html.escape
    o = with_detail(o)
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
    # THE WRITE-UP IS THE TEXT, NOTHING HUNG ON IT (owner, 2026-09-28). The
    # quote block, the numbered source links and the "every sentence traces"
    # line came off the page: he did not want them, and he writes the rest
    # himself. The sources stay in the data - the door that checks an agent's
    # draft still reads them - they are simply not printed.
    paras = "".join(f"<p>{esc(str(txt))}</p>" for txt in pr["paragraphs"])
    by = ("written by SLED JOBS" if pr.get("hand_written")
          else "in their own words, claimed page" if pr.get("by_kind") == "company"
          else "written from their site")
    return (f'<section class="cosec coabout">'
            f'<div class="cosechd"><h2>About</h2><span class="smeta">{by}</span></div>'
            f'{lede}{paras}</section>')


NEWSKIND = {"contract": "contract", "funding": "funding", "leadership": "people",
            "product": "product", "press": "press"}


def _co_news(o: dict, dom: str, now: dt.date | None = None) -> str:
    """The News section - coNews, ported. This page used to print "No news
    items have been recorded" on every company, 1,189 of them, while 810
    companies had dated news on file; the app showed it and the page a
    search engine indexes said the opposite. Same states, same words."""
    esc = html.escape
    o = with_detail(o)
    items = o.get("news") if isinstance(o.get("news"), list) else []
    st = o.get("news_state")
    # a newsroom hidden on purpose has no section, as in the app
    if st == "hidden" and not items:
        return ""
    when = f" &middot; read {esc(str(o['news_checked_on']))}" if o.get("news_checked_on") else ""
    # old news says so, up front, as in the app (coNews)
    dates = sorted(str(i.get("date") or "")[:10] for i in items if isinstance(i, dict) and i.get("date"))
    stale = ""
    if dates and now:
        try:
            if (now - dt.date.fromisoformat(dates[-1])).days > 730:
                stale = f" &middot; nothing newer than {esc(dates[-1][:7])}"
        except ValueError:
            stale = ""
    meta = (f"{len(items)} item{'' if len(items) == 1 else 's'}"
            f"{' from ' + esc(dom) if dom else ''}{stale}{when}" if items
            else f"their news page listed nothing dated{when}" if st == "none_found"
            else "no news page found on their site" if st == "no_news_page"
            else "their site could not be read" if st == "unread"
            else f"the newsroom on their site is {esc(str(o.get('news_by') or dom or 'another organisation'))}'s"
            if st == "hosts_news"
            else "not checked yet")
    head = f'<div class="cosechd"><h2>News</h2><span class="smeta">{meta}</span></div>'
    if not items:
        say = ("Their newsroom is on file and nothing on it carried a date we could read. "
               "An item without a date is not published here." if st == "none_found"
               else "Nothing on their site links to a newsroom, a blog or a press page."
               if st == "no_news_page"
               else "Their site did not answer when we last asked. That is a fact about "
                    "the fetch, not about the company." if st == "unread"
               else (f"Their website is a page on {esc(dom or 'another site')}, so the news there "
                     f"is {esc(str(o.get('news_by') or dom or 'that site'))}'s, not theirs, and it "
                     f"is not shown here as theirs.") if st == "hosts_news"
               else "Nobody has looked yet.")
        return (f'<section class="cosec">{head}<p style="font-size:12.5px;line-height:1.6;'
                f'color:var(--c-ink2);margin:0">{say}</p></section>')
    rows = ""
    for i in items:
        if not isinstance(i, dict):
            continue
        kind = str(i.get("kind") or "")
        title = esc(str(i.get("headline") or ""))
        link = _ext_link(i.get("url"), title) if _safe_url(i.get("url")) else title
        rows += (f'<div class="row"><span class="d">{esc(str(i.get("date") or "")[:10])}</span>'
                 f'<span class="k {esc(kind)}">{esc(NEWSKIND.get(kind, kind))}</span>'
                 f'<span class="t">{link}</span></div>')
    # the kind chips when more than one kind is present, and the fade that
    # says the list scrolls - at 375 wide it stopped after three of twelve
    # items with nothing to say there were more
    kinds = list(dict.fromkeys(str(i.get("kind")) for i in items
                               if isinstance(i, dict) and i.get("kind")))
    chips = ('<div style="margin:0 0 6px">' + " ".join(
        f'<span class="cochip">{esc(NEWSKIND.get(k, k))}</span>' for k in kinds)
        + '</div>') if len(kinds) > 1 else ""
    return (f'<section class="cosec">{head}{chips}<div class="conewsw">'
            f'<div class="conews">{rows}</div><span class="fade"></span></div></section>')


def category_counts(orgs: list) -> dict:
    """{(sector, category): companies filed there}, for "Browse all N".

    Every filing counts, a company's own and each `also`, once per company,
    because that is what the list the link opens matches (index.html
    coCatOk). Counting the primary alone printed "Browse all 23" over a list
    of 24."""
    cat_n: dict = {}
    for x in orgs:
        filed = {(x.get("sector"), x.get("category"))} | {
            (a.get("sector"), a.get("category")) for a in (x.get("also") or [])
            if isinstance(a, dict)}
        for f in filed:
            cat_n[f] = cat_n.get(f, 0) + 1
    return cat_n


def companies_list_href(sector: str = "", category: str = "") -> str:
    """The app's Companies list, opened on a sector or sub-sector.

    csec/ccat are the list's own keys (index.html COKEYS), and call=1 shows
    everyone we track, which is the number "Browse all N" printed. Both links
    were bare /?tab=companies and opened all 1,200 (launch audit 2)."""
    q = [("tab", "companies")]
    if sector:
        q.append(("csec", sector))
    if category:
        q.append(("ccat", category))
    if sector or category:
        q.append(("call", "1"))
    return "/?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def _co_rivals(o: dict, n_in_cat: int, by_id: dict) -> str:
    """The Competitors rail block - coRivals, ported. Three states, three
    different facts: a researched shortlist, a researched EMPTY, and not
    researched yet. The third offers the category as navigation, labelled as
    navigation, and never as the shortlist."""
    esc = html.escape
    href = esc(companies_list_href(o.get("sector") or "", o.get("category") or ""))
    link = (f'<a class="all" href="{href}">Browse all {n_in_cat:,} in '
            f'{esc(o.get("category") or "")} &rarr;</a>' if n_in_cat > 1 else "")
    checked = o.get("competitors_checked_on")
    rivals = o.get("competitors") or []
    if rivals:
        rows = ""
        for r in rivals:
            if not isinstance(r, dict):
                continue
            x = by_id.get(r.get("id")) or {}
            # A competitor whose board nobody read is a dash, not a 0: 319 of
            # 458 rows printed 0 for an unread or absent board (2026-10-06).
            n = (f'<span class="n">{open_count(x)}</span>'
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
        if board_state(o) == "none":
            pa = o.get("posts_at") or {}
            probe = o.get("probe")
            why = (("Their site turned our reader away, so we never got far enough "
                    "to see whether they have a job board. That is a fact about our "
                    "reader, not about their hiring." if probe == "blocked" else
                    "We have not looked for a job board for them yet." if not probe else
                    "We have not found a public job board for them, so we cannot "
                    "say whether they are hiring.")
                   + (f" {_posts_at_phrase(pa)[:1].upper()}{_posts_at_phrase(pa)[1:]}, "
                      f"which we do not read automatically." if pa else ""))
        elif readable and scope_note(o):
            why = ("Their board is one we read every night, and it lists roles this "
                   "board does not show. " + scope_note(o))
        elif readable:
            why = "Their board is one we read every night and it is empty right now."
        else:
            last = (f" &mdash; last on {esc(last_looked(o))}"
                    if last_looked(o) else "")
            why = ("Their board is live but built in a way we cannot read automatically, "
                   f"so this list may be incomplete. A person checks it{last}.")
        board = (_ext_link(o["board_url"], "Open their hiring board &#8599;", "cobtn") + " "
                 if o.get("board_url") and _safe_url(o["board_url"])
                 and board_state(o) != "none" else "")
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
                     f'<span class="pa">{_group_pay_cell(grp)}</span>'
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
    state = board_state(o)
    readable = state == "read"
    phase = _co_phase(mine, readable, now, o.get("read_since"))
    if state == "none":
        phase = {"value": "Not measured", "tone": "dim",
                 "note": "no job board on file, so there is nothing here to "
                         "read a phase from"}
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
    # THE LOGO, which this template drew as a letter for as long as it has
    # existed. 1,916 logo files sit in assets/logos/ and ship to public/, and
    # the only thing that ever rendered one was a list row - the app's
    # tileHTML. The company page, the page a search result lands on, showed
    # an initial for every one of them.
    _ext = (board.get("logos") or {}).get(o.get("id"))
    logo_img = (f'<img src="/assets/logos/{urllib.parse.quote(str(o.get("id")), safe="")}'
                f'.{urllib.parse.quote(str(_ext), safe="")}" alt=""'
                f' decoding="async">' if _ext else "")
    sep = '<span class="sep">&middot;</span>'
    ident = (f'<div class="coid">'
             f'<div class="logo" aria-hidden="true">{esc(initial)}{logo_img}</div>'
             f'<div style="flex:1;min-width:0"><h1>{esc(o["name"])}</h1>'
             f'<div class="cometa">{sep.join(bits)}</div></div>'
             f'<div class="coacts">'
             f'<a class="cobtn fill" href="/?co={q_id}">Open in the board</a>'
             f'<a class="cobtn" href="/alerts?company={q_id}">Alert on new roles</a>'
             f'</div></div>')

    # --- stat strip, four cells, always ------------------------------------
    ats = o.get("ats") or ""
    if state == "none":
        pa = o.get("posts_at") or {}
        src_val = "No board on file"
        src_note = _posts_at_phrase(pa) if pa else esc(no_board_note(o))
    elif readable:
        src_val = ats[:1].upper() + ats[1:] if ats else "Not on file"
        src_note = (f"read nightly{f' · all {open_} readable' if open_ else ''}"
                    if ats and ats not in ("html", "unknown")
                    else "a page we scan, not a board we can enumerate")
    else:
        src_val = "Board unreadable"
        src_note = (("custom HTML" if ats == "html" else "their board")
                    + " · a person checks it"
                    + (f" · last {esc(last_looked(o))}" if last_looked(o) else ""))
    # a board we read that lists roles, none in scope, is not "none seen"
    open_note = ("none shown here, see below" if readable and not open_ and scope_note(o)
                 else _co_open_note(mine, open_, readable, now, state,
                                    o.get("read_since"), last_looked(o)))
    pct = f"{int(math.floor(quota / open_ * 100 + 0.5))}% of open roles" if quota and open_ else "nothing to count"
    strip = (f'<div class="costrip">'
             f'<dl><div class="v{"" if open_ else " dim"}">{open_count(o)}</div><dt>open roles</dt>'
             f'<dd>{esc(open_note)}</dd></dl>'
             f'<dl><div class="v{"" if quota else " dim"}">{quota or "&mdash;"}</div><dt>quota-carrying</dt>'
             f'<dd>{pct}</dd></dl>'
             f'<dl class="wide"><div class="v txt {phase["tone"]}">{esc(phase["value"])}</div>'
             f'<dt>hiring phase</dt><dd>{esc(phase["note"])}</dd></dl>'
             f'<dl class="src"><div class="v txt{"" if readable else " acc"}">{esc(src_val)}</div>'
             f'<dt>source</dt><dd>{src_note}</dd></dl>'
             f'</div>')

    # --- reading column -----------------------------------------------------
    about = _co_about(o, dom)
    acq = _co_acquired(o, by_id)
    news = _co_news(o, dom, now)
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
    # No board on file means no hiring board to link: board_url on those
    # records is their homepage, already linked above as the website.
    if o.get("board_url") and _safe_url(o["board_url"]) and state != "none":
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
            n = f'<span class="n">{b["openRoles"]}</span>' if b.get("openRoles") is not None else ""
            # THE PARAGRAPH IS THE POINT. A list of names answers "who do they
            # own" and stops there; the reader's next question is always what
            # that was and when it changed hands. Both are stored - the
            # company's own one-liner, and the sentence the acquisition was
            # ruled from - so neither is composed here.
            does = (f'<p class="does">{esc(str(b["does"]))}</p>'
                    if b.get("does") else "")
            said = deal_text(b.get("deal"))
            deal = f'<p class="deal">{esc(said)}</p>' if said else ""
            src = (f'<p class="src-l">{_ext_link(b["source"], "the announcement")}</p>'
                   if b.get("source") else "")
            # no "acquired YYYY": the stored year can be another deal's (see
            # _co_acquired); the deal sentence carries the one it has
            rows += (f'<div class="b"><div class="bh"><span>{nm}</span>{n}</div>'
                     f'{does}{deal}{src}</div>')
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
             f'{" " + esc(last_looked(o)) if last_looked(o) else ""}'
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
            f'<a href="{esc(companies_list_href(o.get("sector") or ""))}">'
            f'{esc(o.get("sector") or "")}</a>'
            f'<span class="sep">/</span>{esc(o.get("category") or "")}</nav>'
            f'{ident}{strip}'
            f'<div class="cobody"><main class="cocol">{about}{acq}{news}{roles}</main>'
            f'<aside class="corail">{rail}</aside></div>'
            f'{foot}</div></div>')

    # --- head: unchanged from the page this replaces ------------------------
    prof = with_detail(o).get("profile")
    prof = prof if isinstance(prof, dict) else None
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
    if logo_img:
        body += _lightmark_script()
    return _page(title, meta, f"{site}/c/{cid}", body, brand, "companies",
                 css=COPAGE_CSS, wrap=False)


def _lightmark_script() -> str:
    """index.html's lightMark(), run over this page's logo once it loads.

    THE ONE COPY LIVES IN index.html, between its /*lightMark:start*/ and
    /*lightMark:end*/ markers, and is cut out here rather than written twice:
    a white logo on the company page's white plate was an empty box (Axon's,
    among ten), the same defect the app's tiles had. Run at the end of the
    page, so the image may already be loaded - complete, or wait for it."""
    src = (ROOT / "index.html").read_text()
    fn = src[src.index("/*lightMark:start*/"):src.index("/*lightMark:end*/")]
    return ("<script>" + fn
            + 'document.querySelectorAll(".coid .logo img").forEach(function(i){'
            + 'if(i.complete&&i.naturalWidth)lightMark(i);'
            + 'else i.addEventListener("load",function(){lightMark(i)})});'
            + "</script>")


def write_company_pages(out: pathlib.Path, board: dict, brand: dict) -> int:
    """A real page per company that has something to say.

    Head tags fix how a link UNFURLS. They do not fix crawling: Bing,
    LinkedIn's fetcher and most AI crawlers do not run JavaScript, so they saw
    an empty shell where a company's facts should be. These carry the facts in
    the HTML.

    A COMPANY WITH SOMETHING TO SAY GETS A PAGE. This used to be "only
    companies with something open", on the argument that 1,810 pages reading
    "nothing open right now" are worthless in an index. That was right when
    the page had nothing else. Once a company carries a write-up or a
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
    # company itself included, the way the app counts it. Every filing counts,
    # its own and each `also`, because that is what the list it opens matches
    # (index.html coCatOk); counting the primary alone printed 23 over 24.
    cat_n = category_counts(orgs)
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
                # THE OFFICE FILTER, which is what this page counts by. It
                # linked st=, the board's TERRITORY filter: /s/tx's 101 roles
                # opened as 10, and MN and SD - no territory option at all -
                # opened the whole national board (launch audit 2).
                f'<div class="note"><a href="/?off={urllib.parse.quote(st)}">'
                f'See offices in {html.escape(name)} on the live board</a>, kept '
                f'current. It opens on the quota-carrying roles; &ldquo;Any role&rdquo; '
                f'there shows the rest.</div>')
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
    # NO SILENT CAP. It stopped at 60, sorted by company name, so on 10-09
    # Urban SDK, Versaterm, Workday and Xylem - companies late in the
    # alphabet - never reached a subscriber and nothing said so (launch
    # audit 2). A ceiling only for a run that adds hundreds, and the channel
    # then says how many it left out.
    CAP = 500
    items = ""
    for p_ in fresh[:CAP]:
        # the canonical's encoding (encodeURIComponent), as the sitemap has it
        link = f"{site}/?role={urllib.parse.quote(p_['id'], safe=_JS_SAFE)}"
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
        f"companies, first seen on the most recent run"
        + (f": the first {CAP} of {len(fresh):,} by company name; the rest are "
           f"on the board" if len(fresh) > CAP else "") + ".</description>\n"
        f"  <lastBuildDate>{gen}</lastBuildDate>\n"
        + items + "</channel></rss>\n")

    # Calendars. One for everything, one per department block, so somebody who
    # only sells into public safety is not subscribed to library conferences.
    confs = [c for c in (board.get("conferences") or []) if c.get("dates")]
    # DTSTAMP for every event in this build. Taken from the board's own
    # `generated` rather than the wall clock, so rebuilding an unchanged board
    # produces identical bytes and the deploy diff stays honest.
    stamp = (re.sub(r"[-:]", "", str(gen).split(".")[0]).replace(" ", "T")
             or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S"))
    if not stamp.endswith("Z"):
        stamp = stamp.rstrip("Z") + "Z"
    if "T" not in stamp:                 # a bare date needs a time to be valid
        stamp = stamp[:8] + "T000000Z"

    def ics(rows, name):
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
                 f"PRODID:-//{brand['name']}//conferences//EN",
                 "CALSCALE:GREGORIAN",
                 # ESCAPED LIKE EVERY OTHER TEXT VALUE HERE. A comma is the
                 # value separator inside an iCalendar TEXT (RFC 5545 3.3.11),
                 # and 4 of the 12 block names carry one, so "Clerk, records,
                 # elections, legal" subscribed as a calendar named "SLED
                 # JOBS: Clerk". SUMMARY, LOCATION, DESCRIPTION and URL all
                 # went through _ics_esc; this was the one that did not.
                 f"X-WR-CALNAME:{_ics_esc(name)}"]
        n = 0
        for c in rows:
            span = _ics_range(c.get("dates"))
            if not span:
                continue          # a date we could not parse is not invented
            first, last = span
            n += 1
            lines += ["BEGIN:VEVENT",
                      # DTSTAMP is REQUIRED by RFC 5545 3.6.1 and no event in
                      # this feed carried one. Built from the run's own
                      # timestamp rather than the wall clock, so two builds of
                      # the same board produce the same bytes.
                      f"DTSTAMP:{stamp}",
                      # slugified: a UID with a space in it is not a valid
                      # iCalendar identifier and some clients drop the event
                      f"UID:{_slugify(c.get('tag') or c.get('name') or 'event')}"
                      f"@{brand['domain']}",
                      f"DTSTART;VALUE=DATE:{first.strftime('%Y%m%d')}",
                      # DTEND ON AN ALL-DAY EVENT IS EXCLUSIVE. A conference
                      # ending the 15th needs DTEND 16 or the calendar drops
                      # the last day - the same sentence icsFor() carries.
                      f"DTEND;VALUE=DATE:"
                      f"{(last + dt.timedelta(days=1)).strftime('%Y%m%d')}",
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
                      "TRANSP:TRANSPARENT",
                      "END:VEVENT"]
            # The event's own page, so a subscriber can get from a calendar
            # entry back to the roster. icsFor() carries it and this did not.
            if c.get("url") and re.match(r"^https?://", str(c["url"])):
                lines.insert(len(lines) - 2,
                             f"URL:{_ics_esc(str(c['url']))}")
        lines.append("END:VCALENDAR")
        return "\r\n".join(_ics_fold(x) for x in lines) + "\r\n", n

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


_ICS_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}


def _ics_month(word: str) -> int | None:
    """A month from its name or any unambiguous prefix ('Sept', 'Aug')."""
    w = str(word or "").lower().rstrip(".")
    if w in _ICS_MONTHS:
        return _ICS_MONTHS[w]
    hits = [v for k, v in _ICS_MONTHS.items() if len(w) >= 3 and k.startswith(w)]
    return hits[0] if len(hits) == 1 else None


def _ics_range(dates: str) -> tuple[dt.date, dt.date] | None:
    """(first day, last day INCLUSIVE) from the catalogue's date string.

    A PORT OF calRange() IN index.html, AND IT HAS TO STAY ONE. Those two are
    the only writers of this file format and they disagreed about every event
    in it: the button on a card wrote DTSTART and DTEND, this wrote DTSTART
    alone, so a reader who clicked "+ calendar" got Fire-Rescue International
    as August 12-15 and a reader who SUBSCRIBED to the same feed got it as
    August 12. 118 of 118 events in conferences.ics were one day long.

    The start day disagreed too, and more quietly. The old parser searched for
    the first `20\\d{2}` anywhere in the string and matched a month name at the
    front with `re.match`, so a row reading "Sponsorship opens May 1;
    conference August 12-15, 2026" yielded May 1 2026 - a date nobody stated,
    for a conference in August. calRange refuses any string carrying ';' or
    ':' for exactly that reason, and so does this.

    Returns None rather than guessing. A calendar entry on the wrong day is
    worse than no calendar entry, because somebody books travel around it.
    """
    txt = str(dates or "").strip()
    if not txt or ";" in txt or ":" in txt:
        return None

    def mk(y1, m1, d1, y2, m2, d2):
        # date() rejects February 30 where a naive constructor would roll it
        # into March. mkRange() reads its days back off the Date for the same
        # reason; here the exception IS the check.
        try:
            s, e = dt.date(y1, m1, d1), dt.date(y2, m2, d2)
        except ValueError:
            return None
        return (s, e) if e >= s else None

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", txt)
    if m:
        return mk(*(int(x) for x in m.groups()), *(int(x) for x in m.groups()))

    M, D, Y = r"([A-Za-z]+\.?)", r"(\d{1,2})", r"(\d{4})"
    DASH = r"\s*[-–—]\s*"

    # September 30 - October 3, 2026
    m = re.fullmatch(rf"{M}\s+{D}{DASH}{M}\s+{D},\s*{Y}", txt)
    if m:
        a, b = _ics_month(m.group(1)), _ics_month(m.group(3))
        # A range running backwards through the months means the year rolls
        # over, and the trailing year could mean either end of it. Nothing in
        # the catalogue does this today; when something does it gets a person.
        if a is None or b is None or b < a:
            return None
        return mk(int(m.group(5)), a, int(m.group(2)),
                  int(m.group(5)), b, int(m.group(4)))

    # October 17-21, 2026
    m = re.fullmatch(rf"{M}\s+{D}{DASH}{D},\s*{Y}", txt)
    if m:
        a = _ics_month(m.group(1))
        if a is None or int(m.group(3)) < int(m.group(2)):
            return None
        return mk(int(m.group(4)), a, int(m.group(2)),
                  int(m.group(4)), a, int(m.group(3)))

    # October 17, 2026
    m = re.fullmatch(rf"{M}\s+{D},\s*{Y}", txt)
    if m:
        a = _ics_month(m.group(1))
        if a is None:
            return None
        return mk(int(m.group(3)), a, int(m.group(2)),
                  int(m.group(3)), a, int(m.group(2)))
    return None


def _ics_date(dates: str) -> str | None:
    """YYYYMMDD for the first day, or None."""
    r = _ics_range(dates)
    return r[0].strftime("%Y%m%d") if r else None


def _ics_fold(line: str) -> str:
    """RFC 5545 caps a content line at 75 OCTETS, not characters.

    Unfolded lines are what a strict parser truncates, and the longest line in
    the shipped feed was 82. Folding is counted in bytes because a city like
    'Montréal' is more bytes than characters, and a split landing inside a
    multi-byte character produces mojibake in somebody's calendar.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur = [], bytearray()
    for ch in line:
        b = ch.encode("utf-8")
        # 74 leaves room for the leading space every continuation line carries
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(bytes(cur).decode("utf-8"))
            cur = bytearray()
        cur += b
    out.append(bytes(cur).decode("utf-8"))
    return "\r\n ".join(out)



def _ics_desc(c: dict, site: str) -> str:
    """The calendar description for one conference: a count only when we have
    one, and the link either way."""
    n = c.get("companies") or 0
    head = (f"{n} govtech exhibitor{'s' if n != 1 else ''} on file. " if n else "")
    return _ics_esc(f"{head}{site}/?tab=conferences")



def conference_gets_a_page(c: dict, by_tag: dict) -> bool:
    """One rule, asked in both places.

    The sitemap listed EVERY conference row while write_conference_pages
    skipped any row with no roster and no dates, so 17 of the 138 addresses
    it advertised were 404s. Google was being handed a canonical URL for a
    page that had never been written. With ~310 events staged behind this,
    that gap is the difference between a sitemap and a list of broken links.
    """
    tag = c.get("tag") or c.get("event_tag")
    if not tag:
        return False
    # A TAG IS ENOUGH NOW, because state 4 exists. This required a roster OR
    # dates and the comment said an undated, unswept row had "nothing to say
    # that the catalogue tab does not say" - true of the old panel port, false
    # of turn 3's page, which names the organisation, its other events, the
    # department, the event site, and says out loud that no dates are on file
    # and we are not guessing. Four rows were getting no page while the design
    # drew one specifically for them. The sitemap reads this same function, so
    # the two cannot drift apart.
    return True


def conference_is_worth_crawling(c: dict, by_tag: dict) -> bool:
    """Should the SITEMAP advertise this conference's page?

    TWO DIFFERENT QUESTIONS, and collapsing them was wrong in both
    directions. `conference_gets_a_page` asks whether there is a page - and
    since turn 3 there always is, because state 4 is drawn for precisely the
    undated, unswept row. A sitemap entry is a different claim: that the url
    is worth a crawler's time. This file's own company rule says the same
    thing more sharply - it refuses to list 1,800 near-identical no-openings
    pages, because that is how a site teaches a crawler to stop believing it.

    So a conference we can say something specific about - a roster, or a date
    somebody could plan around - is advertised. Four rows with neither still
    get a page, reachable from the tab and from any company that carries the
    tag, and are simply not pushed at Google. The invariant that actually
    matters is that everything advertised RESOLVES, and it does: this is a
    subset of what the page writer writes, which selftest asserts both ways.
    """
    tag = c.get("tag") or c.get("event_tag")
    if not tag or not conference_gets_a_page(c, by_tag):
        return False
    return bool(by_tag.get(tag) or c.get("dates"))


def conference_rosters(board: dict) -> dict:
    """tag -> the organisations that carry it, from EVERY tag they carry."""
    out: dict = {}
    for o in board.get("organizations", []):
        tags = o.get("conferences") or ([o["conference"]] if o.get("conference") else [])
        for t in tags:
            out.setdefault(t, []).append(o)
    return out


def _cf_place(c: dict) -> str | None:
    """The venue, or the state we know it is in, or nothing.

    A city is a venue and is said plainly. A state is NOT a venue, so it is
    said as what it is - "across North Carolina" - because printing "North
    Carolina" where every other row prints "Denver, CO" reads as a place the
    event is held rather than the only thing we know about where it is.
    """
    city = (c.get("city") or "").strip()
    if city:
        return city
    state = (c.get("state") or "").strip()
    return f"across {state}" if state else None


def _cf_initials(name: str) -> str:
    w = [x for x in re.sub(r"[^A-Za-z0-9 ]", " ", name or "").split() if x]
    return ((w[0][0] if w else "?") + (w[1][0] if len(w) > 1 else "")).upper()


def _cf_provenance(c: dict) -> str:
    """The same five sentences the panel prints. Kept beside it deliberately -
    two surfaces grading the same date differently is the drift this whole
    arrangement exists to prevent."""
    return {"high": "dates read off the event's own page",
            "medium": "dates from a secondary source",
            "owner": "dates confirmed by the organisation",
            "unannounced": "the next edition has not been announced",
            "unreachable": "their site did not answer when we last looked",
            }.get((c.get("dates_confidence") or "").lower(), "dates unconfirmed")


def _conference_body(c: dict, tag: str, roster: list, hiring: list,
                     org: dict | None = None, same_dept: list | None = None) -> str:
    """Turn 3's conference page: states 3g, 3e and 3h, drawn from one shape.

    The design file draws three. They are not three templates - the header,
    the facts table, the doors, "Run by" and "Where this came from" are
    identical in all three, and the only thing that changes is the section
    that answers WHO IS ON THIS FLOOR:

      state 1 (3g) swept        -> the roster, hiring first, and what the read found
      state 3 (3e) dates, unread-> "we have not read this floor", and the door we hold
      state 4 (3h) no dates     -> the same, plus "no dates on file and we are not
                                   guessing"

    TWO BLOCKS THE DESIGN ASKS FOR ARE NOT HERE, and their absence is the
    honest half of this page. "From last year's read" and "Past editions" want
    a previous edition of the same event; the catalogue holds 138 events and
    138 distinct editions, no conference has a sibling year, and no company on
    the board carries a tag from 2024 or earlier. There is no last year to read
    from. Rendering those sections empty would turn the best idea in the design
    into furniture, and filling them would be an invented fact. They land the
    day an event rolls over and we keep the old tag beside the new one.

    "What it is" is missing for the same reason: there is no conference
    description anywhere in the data, and writing one here would be this file
    asserting something no page said.
    """
    esc = html.escape
    name = c.get("name") or tag
    swept = bool(roster)
    has_dates = bool(c.get("dates"))
    dept = c.get("department") or ""
    block = c.get("block") or ""
    n_hire = len(hiring)
    open_roles = sum(o.get("open_roles") or 0 for o in roster)

    # ── header ────────────────────────────────────────────────────────────
    crumb = ['<a href="/?tab=conferences">Conferences</a>']
    if block:
        crumb.append(esc(block))
    if dept and dept != block:
        crumb.append(esc(dept))
    eyebrow = [f'<span>{esc(tag)}</span>']
    if c.get("flagship"):
        eyebrow.append('<span class="cfx-flag">Flagship</span>')
    # AN EDITION THAT HAS ENDED SAYS SO. 29 of 138 pages were for events
    # already over (3CMA, EDUCAUSE) and still offered "Event site &
    # registration" and "Add to calendar"; the .cfpast style shipped on every
    # page and nothing used it (launch audit, 2026-10-06). Same "today" as the
    # same-department list below, which already drops past events.
    span = _ics_range(c.get("dates"))
    ended = bool(span) and span[1] < dt.date.today()
    if ended:
        eyebrow.append('<span class="cfpast">Ended</span>')

    if swept:
        state = ('<span class="cfx-state read"><i></i>Floor read</span>')
        where = f"{esc(block)} &rarr; {esc(dept)}. " if block and dept else ""
        # NO SUM OF open_roles HERE, AND THAT IS THE POINT. The design reads
        # "9 hiring right now across 20 open sales roles", which assumes the
        # third figure belongs to this floor. It does not: open_roles is a
        # company's whole board, so APCO's 32 exhibitors summed to 358 because
        # Motorola Solutions carries 352 reqs worldwide - "Channel Account
        # Executive - ITALY" among them - and the sentence then reads as a
        # claim about a dispatch show in Anaheim. The per-company counts are
        # in the roster table below, where each number sits beside the company
        # it belongs to and cannot be mistaken for the floor's total.
        lede = (("This edition has ended. " if ended else "")
                + f'{where}We read this floor: <b>{len(roster)}</b> '
                f'{"company" if len(roster) == 1 else "companies"} on file'
                + (f', <b>{n_hire}</b> of them hiring a seller today'
                   if n_hire else
                   ', and none of them is hiring a seller today')
                + '.')
    else:
        state = '<span class="cfx-state"><i></i>Floor not read yet</span>'
        where = f"{esc(block)} &rarr; {esc(dept)}. " if block and dept else ""
        lede = (("This edition has ended. " if ended else "")
                + f'{where}The tag <b>{esc(tag)}</b> is what company pages cite '
                f'when we know a company exhibited here.')

    # ── the facts table: only rows we hold ────────────────────────────────
    facts = []
    facts.append(("Dates", esc(c.get("dates") or _cf_no_dates(c))
                  + (" &middot; ended" if ended else "")))
    if _cf_place(c):
        facts.append(("City", esc(_cf_place(c))))
    if dept:
        facts.append(("Department",
                      esc(f"{block} → {dept}" if block and block != dept else dept)))
    facts.append(("Event tag", esc(tag)))
    facts_html = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)

    # ── the doors ─────────────────────────────────────────────────────────
    doors = []
    # Every outbound address here passes _safe_url, as the company pages'
    # do: conferences.json and organisations.json are research output, and a
    # javascript: value there would ship as a live link on our origin.
    if _safe_url(c.get("url")):
        doors.append(f'<a class="cfx-door lead" href="{esc(_safe_url(c["url"]))}" '
                     f'rel="nofollow noopener">'
                     + ("Event site" if ended else "Event site &amp; registration")
                     + ' &nearr;</a>')
    # THE DIRECTORY, WHICH REACHED NO READER UNTIL TODAY. 61 rows carry one and
    # the conference row shape did not pass it through, so every unmined page
    # said "we have not read this floor" and offered nothing to do about it.
    if _safe_url(c.get("exhibitor_url")):
        doors.append(f'<a class="cfx-door" href="{esc(_safe_url(c["exhibitor_url"]))}" '
                     f'rel="nofollow noopener">Exhibitor directory &nearr;</a>')
    # ONLY WHEN THE FILE EXISTS. _ics_range refuses a date string it cannot
    # parse, so `has_dates` is not the same question as "is there a calendar":
    # five rows carry prose a person can read and the parser will not guess at.
    if span and not ended:
        doors.append(f'<a class="cfx-door" href="/e/{_slugify(tag)}.ics">'
                     f'Add to calendar (.ics)</a>')

    secs = []

    # ── the middle: what we know about this floor ────────────────────────
    if swept:
        rows = []
        for o in roster[:10]:
            # the company's quota-carrying roles, as its own page counts them:
            # the column printed every open role, so Motorola's 359 sat under
            # a "sales roles" heading beside a company page saying 62
            n = o.get("quota_roles") or 0
            place = " / ".join(x for x in (o.get("sector"), o.get("category")) if x)
            # LINK ONLY WHERE A PAGE WAS WRITTEN. has_static_page is the one
            # gate on whether /c/<id>.html exists, and this roster linked every
            # exhibitor regardless: 147 links across the conference pages went
            # to a page that was never built. A visitor clicked IBM on a
            # conference floor and got a 404.
            #
            # It is the same defect has_static_page's own docstring describes
            # for the sitemap - "a sitemap listing a page that was never
            # written is a 404 submitted to Google as canonical" - and this
            # roster was never brought onto the gate. A company with nothing
            # to say still BELONGS on the floor; it just is not a link.
            cell = (f'<a href="/c/{esc(o.get("id") or "")}.html">'
                    f'{esc(o.get("name") or "")}</a>'
                    if has_static_page(o) else esc(o.get("name") or ""))
            rows.append(
                f'<tr><td>{cell}</td>'
                f'<td>{esc(place)}</td>'
                f'<td class="n">{n or "&mdash;"}</td></tr>')
        more = ""
        if len(roster) > 10:
            more = (f'<p class="cfx-more">Showing 10 of {len(roster)}, the ones '
                    f'hiring first.</p>')
        # TWO DIFFERENT TRUNCATIONS, and only one of them is about the show.
        # `more` above is our display cap. This is the other gap: the catalogue
        # records roughly how big the floor was, and it is usually far larger
        # than the number of exhibitors we follow - APCO lists about 250 and we
        # track 32. Printing 32 under a heading about the floor, with nothing
        # said about the other 218, reads as a claim that the show was small.
        # The app refuses to make that claim and so does this.
        gap = ""
        approx = c.get("approx_count") or 0
        if approx > len(roster):
            gap = (f'<div class="cfx-note"><p>About <b>{approx}</b> exhibitors '
                   f'were on this floor and we follow <b>{len(roster)}</b> of '
                   f'them. The rest are companies we do not follow &mdash; '
                   f'not companies that were absent.</p></div>')
        secs.append(
            f'<section class="cfx-sec"><h2>Who exhibits here &middot; hiring first'
            f'<span>{len(roster)} '
            f'{"company" if len(roster) == 1 else "companies"}'
            f'{f" &middot; {n_hire} hiring a seller" if n_hire else ""}</span></h2>'
            f'<table class="cfx-roster"><thead><tr><th>Company</th>'
            f'<th>On the board</th><th class="n">Quota-carrying roles</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>{more}'
            f'<p class="cfx-more">These are the exhibitors <em>we</em> track from '
            f'this show, not the show&rsquo;s own list. A short roster here means '
            f'we know less about this floor, never that the floor was small.</p>'
            f'{gap}</section>')
        # What the read found - only where a sweep record exists to say it.
        sw = c.get("sweep") or {}
        if sw.get("swept_on") or sw.get("captured"):
            found = []
            if sw.get("swept_on"):
                found.append(f'Read on <b>{esc(str(sw["swept_on"]))}</b>')
            if sw.get("captured"):
                found.append(f'<b>{sw["captured"]}</b> names captured')
            if sw.get("govtech"):
                found.append(f'<b>{sw["govtech"]}</b> read as govtech')
            secs.append(f'<section class="cfx-sec"><h2>What the read found</h2>'
                        f'<p class="cfx-org">{" &middot; ".join(found)}</p>'
                        f'</section>')
    else:
        # THE 800-PAGE CASE. An empty count is a fact about us and this says so
        # in the same words the catalogue tab uses, then hands over the only
        # useful thing we have: the organiser's own list.
        door = ""
        if _safe_url(c.get("exhibitor_url")):
            door = (f'<p>They publish an exhibitor directory, so when it is read '
                    f'this page fills in on its own. '
                    f'<a href="{esc(_safe_url(c["exhibitor_url"]))}" rel="nofollow noopener">'
                    f'Open their exhibitor directory &nearr;</a></p>')
        else:
            door = ('<p>We have not found an exhibitor list for this event at '
                    'all. If you know where one is, that is one page of ours '
                    'made correct.</p>')
        no_dates = ""
        if not has_dates:
            no_dates = ('<p><b>No dates on file, and we are not guessing.</b> '
                        'The organiser has not published the next edition where '
                        'we can read it; this page updates itself the day they '
                        'do.</p>')
        secs.append(
            f'<section class="cfx-sec"><h2>The floor here</h2>'
            f'<div class="cfx-note">'
            # THE PHRASE STAYS ON ONE LINE. selftest greps this module's own
            # source for it, so splitting it across two f-string fragments
            # renders correctly and fails the check - which is fair, because a
            # phrase nobody can find by searching is a phrase that drifts.
            f'<p>We have not read this floor yet &mdash;'
            f' that is a fact about us, not about the conference.</p>'
            f'{no_dates}{door}</div></section>')

    # ── run by ────────────────────────────────────────────────────────────
    if org and org.get("name"):
        meta = []
        # PUBLISHED events, the ones on the Conferences tab. event_count also
        # counts staged registry rows - "Chapter conferences (~30)", "e.g.
        # Connecticut NAHRO" - so NAHRO read "16 events in the catalogue"
        # over a catalogue holding one (launch audit 2, 2026-10-09)
        n_pub = org.get("published_count")
        if n_pub:
            meta.append(f'{n_pub} {"event" if n_pub == 1 else "events"} '
                        f'in the catalogue')
        if org.get("swept_count"):
            meta.append(f'{org["swept_count"]} '
                        f'{"floor" if org["swept_count"] == 1 else "floors"} read')
        host = ""
        if _safe_url(org.get("url")):
            host = urllib.parse.urlparse(org["url"]).netloc.replace("www.", "")
        link = (f'<a href="{esc(_safe_url(org["url"]))}" rel="nofollow noopener">'
                f'{esc(org["name"])}</a>' if _safe_url(org.get("url"))
                else f'<b>{esc(org["name"])}</b>')
        secs.append(f'<section class="cfx-sec"><h2>Run by</h2>'
                    f'<div class="cfx-org">{link}'
                    f'<div class="meta">'
                    f'{" &middot; ".join([esc(host)] + meta) if host else " &middot; ".join(meta)}'
                    f'</div></div></section>')

    # ── same department, next up ───────────────────────────────────────────
    if same_dept:
        rows = []
        for x in same_dept[:4]:
            tail = (f'{x["companies"]} exhibitors' if x.get("companies")
                    else "floor not read yet")
            rows.append(
                f'<a href="/e/{_slugify(x["tag"])}.html">'
                f'<span class="nm">{esc(x.get("name") or x["tag"])}</span>'
                f'<span class="when">{esc(x.get("dates") or _cf_no_dates(x))}'
                f' &middot; {tail}</span></a>')
        secs.append(f'<section class="cfx-sec">'
                    f'<h2>Same department, next up</h2>'
                    f'<div class="cfx-next">{"".join(rows)}</div></section>')

    # ── where this came from ──────────────────────────────────────────────
    secs.append(f'<section class="cfx-sec"><h2>Where this came from</h2>'
                f'<p class="cfx-src">{esc(_cf_provenance(c))}</p></section>')

    return f'''<div class="cfx">
  <nav class="cfx-crumb">{" &rsaquo; ".join(crumb)}</nav>
  <div class="cfx-eyebrow">{"".join(eyebrow)}</div>
  <h1>{esc(name)}</h1>
  {state}
  <p class="cfx-lede">{lede}</p>
  <dl class="cfx-facts">{facts_html}</dl>
  <div class="cfx-doors">{"".join(doors)}</div>
  {"".join(secs)}
</div>'''


def _cf_no_dates(c: dict) -> str:
    return {"unannounced": "next edition not announced yet",
            "unreachable": "their site did not answer when we last looked",
            }.get((c.get("dates_confidence") or "").lower(), "dates unconfirmed")


def _ics_stamp(board: dict) -> str:
    """DTSTAMP for this build, from the board's own `generated`.

    Shared by the feed and the per-event files so two writers of one format
    cannot disagree about when the build happened, and so an unchanged board
    rebuilds to identical bytes.
    """
    gen = board.get("generated") or dt.date.today().isoformat()
    stamp = re.sub(r"[-:]", "", str(gen).split(".")[0]).replace(" ", "T")
    stamp = stamp.rstrip("Z")
    if "T" not in stamp:
        stamp = stamp[:8] + "T000000"
    return stamp + "Z"


def _one_event_ics(c: dict, tag: str, brand: dict, site: str,
                   stamp: str) -> str | None:
    """One conference as a subscribable .ics, or None if it has no date.

    THE BUTTON HAS TO LEAD SOMEWHERE. The panel this page replaced carried no
    calendar download and said why: "a page with dead buttons is worse than a
    page without them". Turn 3 draws "Add to calendar (.ics)", so the file has
    to exist - 128 pages shipped a link to a path nothing wrote before this.

    Same writer discipline as the feed: DTSTAMP and an EXCLUSIVE DTEND, every
    TEXT value escaped, every line folded at 75 octets.
    """
    span = _ics_range(c.get("dates"))
    if not span:
        return None
    first, last = span
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             f"PRODID:-//{brand['name']}//conferences//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_ics_esc(c.get('name') or tag)}",
             "BEGIN:VEVENT",
             f"DTSTAMP:{stamp}",
             # DERIVED WHEN IT IS NOT GIVEN. brand.json carries `domain`, but
             # two selftest harnesses build a partial brand and this crashed
             # on the second one - and the value is already implied by `site`,
             # so asking for it twice was the mistake.
             f"UID:{_slugify(tag)}@"
             f"{brand.get('domain') or urllib.parse.urlparse(site).netloc}",
             f"DTSTART;VALUE=DATE:{first.strftime('%Y%m%d')}",
             f"DTEND;VALUE=DATE:"
             f"{(last + dt.timedelta(days=1)).strftime('%Y%m%d')}",
             f"SUMMARY:{_ics_esc(c.get('name') or tag)}",
             f"LOCATION:{_ics_esc(c.get('city') or '')}",
             f"DESCRIPTION:{_ics_desc(c, site)}",
             f"URL:{_ics_esc(f'{site}/e/{_slugify(tag)}')}",
             "TRANSP:TRANSPARENT", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_ics_fold(x) for x in lines) + "\r\n"


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
    # EVERY TAG A COMPANY CARRIES, not just the first.
    #
    # This read o["conference"] - the legacy single field build_board keeps
    # beside the list - so a company at two shows appeared only on the first
    # one's page. Ten events understated their own roster against the count
    # the Conferences tab prints from the same data: ICMA 2026 named 11 of 40,
    # NSA 2026 named 60 of 88, and SoundThinking ("IACP 2026; NSA 2026")
    # appeared on neither, filed under IACP and then cut by the roster cap.
    by_tag = conference_rosters(board)

    # ONE READ, NOT 138. "Run by" needs the organisation behind each event and
    # organisations.json already holds the counts the design asks for -
    # event_count and swept_count - so the page states a number it did not
    # compute here. All 138 conferences resolve to a real body and none is an
    # is_a_class grouping, so the section never renders a placeholder.
    org_by_tag: dict = {}
    _orgp = ROOT / "data" / "organisations.json"
    if _orgp.exists():
        _orgs = json.loads(_orgp.read_text())
        _orgs = _orgs.get("organisations", _orgs) if isinstance(_orgs, dict) else _orgs
        for _o in _orgs:
            for _e in (_o.get("events") or []):
                if _e.get("tag"):
                    org_by_tag[_e["tag"]] = _o

    # "Same department, next up" - the soonest OTHER events in this department,
    # by the same parser the calendar and the .ics use, and only ones that get
    # a page of their own. 66 of 138 share a department with at least one other
    # event; the rest drop the section rather than showing a lonely link.
    dept_index: dict = {}
    for _c in board.get("conferences") or []:
        if _c.get("department"):
            dept_index.setdefault(_c["department"], []).append(_c)

    n = 0
    # tag -> the page this event got, for the middleware: a shared ?e= link
    # was titled and canonicalised as the home page (launch audit 2)
    events: dict = {}
    for c in board.get("conferences", []) or []:
        # THE SAME ACCESSOR THE PREDICATE USES. This read only `tag` while
        # conference_gets_a_page reads `tag or event_tag`, so the two disagreed
        # about what identifies a conference. It works in production only
        # because build_board writes `tag` - a row in conferences.json shape
        # was judged page-worthy and then silently skipped by the writer, which
        # is precisely the sitemap-advertises-a-404 failure this pair exists to
        # prevent, arriving from the other side.
        tag = c.get("tag") or c.get("event_tag")
        if not tag:
            continue
        # sellers first: a quota-carrying role, then any open role at all
        roster = sorted(by_tag.get(tag, []),
                        key=lambda o: (-(o.get("quota_roles") or 0), -(o.get("open_roles") or 0),
                                       o.get("name") or ""))
        if not conference_gets_a_page(c, by_tag):
            continue      # nothing to say that the catalogue tab does not say
        # HIRING A SELLER means a quota-carrying role. This took any open role:
        # APCO's page said 9 of 32 were "hiring a seller today" where 5 were,
        # counting Aurelian's eight engineering and product openings among
        # them (launch audit 2, 2026-10-09).
        hiring = [o for o in roster if o.get("quota_roles")]
        # The <meta description> is what a search result and a link unfurl
        # show, so it says the one thing this page can answer that nothing
        # else can - and says it about US, never about the show's floor.
        line = (f"{len(hiring)} of the {len(roster)} exhibitors we track here "
                f"are hiring a seller" if roster else "No exhibitors tracked here yet")
        span = _ics_range(c.get("dates"))
        peers = [x for x in dept_index.get(c.get("department") or "", [])
                 if x.get("tag") != tag and conference_gets_a_page(x, by_tag)]
        # soonest first, and an event already past is not "next up"
        peers = sorted(
            (x for x in peers if _ics_range(x.get("dates"))),
            key=lambda x: _ics_range(x["dates"])[0])
        peers = [x for x in peers
                 if _ics_range(x["dates"])[1] >= dt.date.today()]
        body = _conference_body(c, tag, roster, hiring,
                               org=org_by_tag.get(tag), same_dept=peers)
        cal = _one_event_ics(c, tag, brand, site, _ics_stamp(board))
        if cal:
            (d / f"{_slugify(tag)}.ics").write_text(cal)
        (d / f"{_slugify(tag)}.html").write_text(_page(
            f"{c.get('name') or tag}: who is hiring · {brand['name']}",
            f"{line}. " + (f"{c.get('dates')}, {c.get('city')}. " if c.get("dates") else "")
            + "Sales roles at the govtech companies on this floor.",
            f"{site}/e/{_slugify(tag)}", body, brand, "conferences",
            css=_default_css(brand) + CFPAGE_CSS, wrap=False))
        events[tag] = {"n": c.get("name") or tag, "p": _slugify(tag), "l": line}
        n += 1
    (out / "meta-events.json").write_text(
        json.dumps({"generated": board.get("generated"), "events": events},
                   separators=(",", ":")))
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
        # `since` per company: rule 6 measures a company whose coverage
        # jumped from the night it jumped, not from the window's start
        board["active"] = ([{"id": c["id"], "was": c["was"], "now": c["now"],
                             "since": c["since"]}
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
    # MATCHED BY SHAPE, NOT BY BYTES. An exact string broke the day #view
    # gained tabindex="-1" for the skip link: the fallback silently stopped
    # shipping and the build only printed a note (third review, 2026-10-08).
    m = re.search(r'<main><div id="stale"></div><div class="panel" id="view"[^>]*>'
                  r'(?:<p class="empty boot"[^>]*>[^<]*</p>)?</div></main>',
                  html_txt)
    if not m:
        # The body changed shape. A missing fallback looks identical to a
        # working one, so this stops the build rather than printing a note.
        raise SystemExit("noscript: could not find the main element in index.html; "
                         "the no-JavaScript page would not ship")
    anchor = m.group(0)

    name = brand.get("name") or "SLED JOBS"
    # COUNTED AS THE BOARD OPENS: quota-carrying roles not placed outside the
    # US, by opening (index.html boardDefault). It printed every opening of
    # every family, 6,656 under "every open sales role" beside a home card
    # saying 552 (launch audit 2, 2026-10-09).
    sell = [p_ for p_ in board.get("postings") or []
            if p_.get("quota_carrying") and p_.get("is_us") is not False]
    n_roles = len({p_.get("opening_id") or p_.get("id") for p_ in sell})
    n_cos = len({p_.get("company_id") for p_ in sell})
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
        f'companies. <strong>{n_roles:,} open sales roles</strong> at '
        f'{n_cos:,} companies, out of '
        f'{len(board.get("organizations") or []):,} we track, rebuilt every '
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
    frame-ancestors is the one that is actually specified. The PAGE policy is
    not set here (2026-10-09): functions/_csp.js sets one on every HTML
    response from the manifest scripts/csp.py builds, allowing each inline
    script by its hash rather than by 'unsafe-inline' - so the single-file app
    runs and an injected handler, javascript: url or script does not. This
    file once said such a policy would have to permit what it is meant to
    prevent; that was true of 'unsafe-inline' and is not true of hashes.
    """
    # EVERY SPELLING OF BOTH PAGES, EACH WITH ITS OWN HEADERS. "/alerts.html"
    # sat here with no header lines under it, and claim mail links to /claim,
    # which this file did not name (launch audit, 2026-10-06). Cloudflare also
    # does not apply _headers to a response a Pages Function produced, and the
    # root middleware sees every request - so functions/_csp.js sets the same
    # two headers itself (NO_FRAME, and /admin). This file is the second layer.
    # A claim page carries a token in the URL and answers only to somebody
    # holding one, which is why it is here at all.
    rules = "".join(f"{path}\n  X-Frame-Options: DENY\n"
                    f"  Content-Security-Policy: frame-ancestors 'none'\n"
                    for path in ("/alerts", "/alerts.html", "/claim", "/claim.html"))
    (out / "_headers").write_text(
        "# Generated by build_site.py - see write_headers() for why this is\n"
        "# scoped to the token pages and not applied site-wide.\n" + rules)
    print("  _headers: frame protection on /alerts and /claim")


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
    # The app's own tabs (index.html TABS). "market" and "alerts" were listed
    # here and neither is a tab: both opened the job list (2026-10-06).
    for tab in ("jobs", "companies", "conferences", "map", "intel"):
        urls.append((f"{site}/?tab={tab}", "daily", "0.8"))
    # every company that HAS a page, not every company that is hiring - the
    # 1,810-near-identical-documents argument dies once a page carries a
    # write-up. Same gate as the writer, by construction.
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
    # A SUBSET OF WHAT THE PAGE WRITER WRITES. Listing a row that never got a
    # page hands Google a canonical address that 404s; 17 of 138 once did.
    # Every page exists now, so the question here is the narrower one -
    # whether the url is worth crawling. See conference_is_worth_crawling.
    _rosters = conference_rosters(board)
    for c in board.get("conferences", []) or []:
        if conference_is_worth_crawling(c, _rosters):
            tag = c.get("tag") or c.get("event_tag")
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
        "# data/ is NOT disallowed: the board draws every role page from\n"
        "# data/board.json, and a crawler that may not fetch it renders an\n"
        "# error message instead of the role.\n"
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

    # THE COMPANY DETAIL, one small file each. build_board splits news and the
    # write-up out of board.json because they are 57% of a payload every
    # visitor downloads and are read only when somebody opens ONE company.
    # They have to SHIP, or coAbout and coNews get a 404 and every company
    # page says the write-up could not be loaded - which is exactly the state
    # this build would have deployed: the files existed in data/ and this
    # function copies named files, not the tree.
    src_detail = ROOT / "data" / "detail"
    if src_detail.exists():
        dst = out / "data" / "detail"
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src_detail.glob("*.json"):
            shutil.copy2(f, dst / f.name)
            n += 1
        print(f"  shipped {n} company detail file(s)")
    else:
        print("  NO data/detail/ - run build_board first, or every company "
              "page will say its write-up could not be loaded")

    # THE BUILD REFUSES TO SHIP A BOARD WITH ITS DETAIL MISSING.
    #
    # A selftest cannot check this honestly: the failure is a copy that does
    # not happen, and every string-level version of the check passed while
    # the mutation that broke it sat in a comment one line away. So the
    # assertion lives HERE, where the real files are, and runs on the deploy
    # itself rather than on a reading of the source.
    #
    # The pairing is what matters. build_board strips news and profile out of
    # the board because they are 57% of a payload every visitor downloads;
    # coAbout and coNews then fetch data/detail/<id>.json. Ship one without
    # the other and every company page 404s and says its write-up could not
    # be loaded - on a build that is otherwise perfectly correct.
    split = any("news" not in o and "profile" not in o
                for o in board.get("organizations", [])[:50])
    shipped = len(list((out / "data" / "detail").glob("*.json"))) \
        if (out / "data" / "detail").exists() else 0
    if split and not shipped:
        raise SystemExit(
            "refusing to ship: board.json has been split (news and profile "
            "are not on the organizations) and no data/detail/ went with it. "
            "Every company page would fetch a 404 and report that its "
            "write-up could not be loaded.")

    schema = json.loads((ROOT / "data" / "schema.json").read_text())
    (out / "data" / "sectors.json").write_text(
        json.dumps([x["name"] for x in schema["sectors"]], separators=(",", ":")))
    # THE NAMES, FOR AN ALERT ABOUT ONE COMPANY. /alerts?company=<id> names
    # the company it is limited to; the alerts page reads this (about 60 KB)
    # rather than the whole board to say it in words.
    (out / "data" / "company-names.json").write_text(
        json.dumps({o["id"]: o.get("name") or o["id"] for o in board.get("organizations") or []},
                   separators=(",", ":"), sort_keys=True))

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
    # LAST, after every page exists as it will ship: the content security
    # policy, every inline script hashed from the page itself, and a refusal
    # (SystemExit) for any script, handler or javascript: url this build did
    # not write. See scripts/csp.py; functions/_csp.js serves it.
    import csp
    csp_m = csp.write(out, ROOT, brand, _hunter_page(brand), _lightmark_script())

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"wrote {a.out}/: {len(SHIP)} page(s) + data/board.json")
    print(f"  {len(board['postings'])} postings, "
          f"{len(board['organizations'])} organizations")
    print(f"  {stripped} internal error string(s) replaced with the plain fact")
    print(f"  sitemap.xml: {crawl['urls']} urls ({crawl['companies']} companies "
          f"with an opening), robots.txt, 404.html")
    print(f"  noscript: a real page for a reader without JavaScript, "
          f"linking {n_ns} state page(s)")
    print(f"  meta-csp.json: a policy for {len(csp_m['policies'])} kinds of page, "
          f"{sum(csp_m['scripts'].values())} inline scripts hashed")
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
