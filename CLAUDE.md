# SLED JOBS — project guide for Claude Code

## What this is

A public job board for sales roles at state & local government technology
companies, and the map of companies behind it. The owner is a SLED sales
professional; it began as a Cowork spreadsheet, and the board is now the
product being launched. Three parts:

1. **Data** — `data/companies.json` (the company map), `data/board.json` (what
   the site actually reads), `data/schema.json` (sectors/categories),
   `data/hiring_history/*.json` (append-only snapshots), `data/meta.json` +
   `data/latest_diff.json` (run state).
2. **Engine** — `scripts/refresh.py` (deterministic ATS checks; no AI),
   `scripts/ats.py` (per-ATS fetchers), `scripts/classify.py` (title rules),
   `scripts/roles.py` (family / seniority / work mode), `scripts/salary.py`
   (pay out of prose), `scripts/build_board.py` (postings → board.json),
   `scripts/build_site.py` (what ships to `public/`),
   `scripts/export_xlsx.py`, `scripts/selftest.py` (offline QA).
3. **Site** — `index.html`, a single-file static app reading `data/` at
   runtime, plus `alerts.html` and a few Cloudflare Pages Functions under
   `functions/`.

**Every page wears the same header**, and it is a BAND, not a line of text:
Penguin ground, the mascot's face at 46px in a Belly circle, a Beak rule
underneath, three round buttons at the right. The four `--hdr-*` tokens are
deliberately NOT theme-swapped — everything else flips with
`prefers-color-scheme` and the header stays Penguin in both, so it is the one
fixed thing across every page and either theme. The mark goes home from
anywhere, which is the habit every visitor already has.

There is no build step, so `alerts.html` restates the band rather than
templating it, and `selftest::check_header_shared` fails the build if the
tokens or the mark drift. Same pattern, same reason, as the brand.json guard.

**`home` is the first tab and the default.** It used to be `tab="jobs"` — the
site opened into a filtered list, which is right for somebody who already
knows what this is and tells a stranger nothing. The home banner's slides are
BUILT FROM THE BOARD by `buildSlides()`, never written: a quiet run drops the
"new this run" slide rather than printing a zero, and if no slide can be built
there is no banner. It auto-advances every 6s, pauses on hover, and never
starts at all for a reader whose OS asks for reduced motion.

**The product is called SLED JOBS.** It was GovTech Dock. The git remote is
still `westjw/govtech-dock` and the portfolio `project_id` is still
`govtech_dock` — those are identifiers, leave them. Anywhere else the old name
appears in prose or UI, it is stale.

## The name, the palette, and the one place they live

`data/brand.json` is the single source for the name, the tagline, the domain
and the eight-colour palette. Nothing may hardcode any of them.

- **The domain is `sledjobs.com`** (bought 2026-09-02).
  **`solesourcejobs.com` is being handed to a separate FEDERAL hiring board**,
  so nothing in this project may keep using it. Editing brand.json and
  `functions/_brand.js`, then pointing the Pages custom domain at the new
  name, is the whole move: alert links, digest footers, the confirmation email
  and the submission form all read it from brand.json.
- **The sending address moved to `alerts@sledjobs.com` late on 2026-09-02**
  (2026-09-03 UTC, commit 6168048), once Resend showed sledjobs.com verified
  (SPF and DKIM in Cloudflare DNS).
  `data/brand.json` and `functions/_brand.js` moved together and
  `selftest::check_brand` refuses the pair disagreeing. This bullet said the
  address was DELIBERATELY still on solesourcejobs.com for three weeks after
  it was not; a reader following it would have reverted a live, correct
  configuration. Keep the secret rule from that episode: never rename a
  Pages secret in place, delete and re-add.
- **Two strings on the site would not have followed the move**: the iCalendar
  UID and the description on every conference download had the domain typed
  into them. They read `location.hostname` now, and
  `selftest::check_the_domain_lives_in_one_place` refuses a domain written as
  a string literal in any shipped file. brand.json's claim that a rebrand is
  "an edit to this file rather than a hunt through five languages" is true
  again.
- **Old links keep working until the federal board takes the domain.** Any
  alert already sent carries `solesourcejobs.com/alerts?t=<token>`; point both
  domains at this Pages project so those resolve, and expect them to break the
  day solesourcejobs.com moves. One confirmation email has ever been sent, so
  the blast radius is one address.
- **`functions/_brand.js` restates four values** (`SITE`, `DOMAIN`, `NAME`,
  `FROM`) because a Pages Function cannot read a repo file at runtime. That
  duplication is the thing that will rot, so `selftest.py::check_brand` fails
  the build if the two disagree. Change the domain in both files or selftest
  will tell you which one you forgot. Same pattern, same reason, as the alerts
  vocabulary guard.
- **The palette is CSS tokens, never a hex literal in a rule.** Ice `--bg` is
  the ground and the only white, Belly `--panel` is cards, Frost `--line` is
  rules. Fog `--faint` is 2.7:1 and is for tiny uppercase labels only; `--dim`
  is Fog's own hue darkened to 4.6:1 and is the readable secondary tone; Beak
  `--beak` is a highlight and is never text. The two derived tokens are marked
  as derived in brand.json so nobody later mistakes them for the kit.
- **The rest of the system is asserted once**, at the top of `index.html`:
  Archivo, headings 800, a 4px spacing scale, `--radius:0` applied to every
  control in one rule, `tabular-nums` on every column of figures. A single
  rounded corner anywhere is what breaks it. Match the system; do not
  re-approximate it from a screenshot.
- The mascot is in `assets/mascot/svg/`: `head-ghosted` for an empty state,
  `head-offer-in` for success, `head-competitive-pay` for a refusal.

## Where a job is, and the three ways we can fail to say

`roles.geography()` returns three separate facts and each is honest about
absence: **territory** (what the role covers, read from the title),
**office** (where the job sits, read from the location field), **work_mode**
(remote/hybrid/onsite exactly as stated, else `not stated`).

`work_mode` is **`not stated` on 79% of postings**, because most boards never
say it. So anything gated on the words "hybrid" or "onsite" reaches 139
postings out of 4,369. The field that means "this job has a place" is
**`office`**, not `work_mode` — a bare city is an office, not proof of onsite,
and 917 postings that never said a mode still name a real city.

Two traps the office parser has already fallen into, both now pinned by cases
in `selftest.py::CITY_CASES`:

- **Two capitals are not a US state.** `[A-Z]{2}` matched `London, UK`,
  `Montreal, QB`, `Noida, UP` and `California, US` — 24 postings filed at
  places that do not exist. A code is a state only if it is in `US_CODES`.
  Delaware stays valid: the board carries Dover, Newark and Wilmington.
- **A city is the trailing run of capitalised words.** The pattern starts at
  the first capital before the comma, so `in-office preferred in San Mateo, CA`
  produced a city named "in-office preferred in San Mateo".

**Coordinates are asked for, never derived.** `scripts/geocode_cities.py`
queries Nominatim at one request a second with an identifying User-Agent (that
is their usage policy — run it less often rather than faster) and writes
`data/cities.json` with the query and what it matched, so every coordinate is
auditable. A city it cannot resolve is stored with `lat: null` and is **left
out of `board.json` entirely**: a city at no coordinate is not a city at 0,0.

The "near a city" filter therefore has three different silences and says all
three out loud rather than returning zero — a city we do not hold, a name in
several states, and desks we could not place. A distance search that quietly
drops what it cannot map is a false "nothing near you", which is the same
failure as a page scan reporting "no listings" when it could not read.

## The job list is cards (owner, 2026-09-27)

The jobs tab was a dense table from 2026-08-23. The owner looked at a federal
board laid out as cards (govconcareershub.com, a Jobboardly site) and asked for
that here, on the web and on phones. `jobCardHTML` in index.html builds every
card at every width; the phone layout is CSS alone, so one function decides
what a card says. The trade is known: a card fits two or three roles on a
laptop screen where the table fitted five or six.

What a card may say, each one a rule the table used to keep by position:

- **Silence is written out and names which silence**: "no salary stated" (we
  read the description and it named none), "pay unknown: we could not read this
  posting", "pay not recorded" (this build has no pay field). "location not
  stated" only when nothing names a place. Silence is `--dim`, not `--faint`:
  on a card it is reading copy, and `--faint` is 2.86:1 on `--panel`.
- **The work-mode line appears only when the posting states a mode.** An office
  is a place, never proof of onsite; a territory reads "covers TX, OK +2". A
  remote role that names no place gets no place line at all.
- **A group is one advertisement in several places, and it only speaks for
  itself where its postings agree.** Places are counted as distinct places, not
  postings ("212 locations · 217 postings"; three postings naming nothing read
  "location not stated · 3 postings"). Pay is one figure only if every posting
  states it; "$X on 1 of 2 postings" when some are silent; "pay varies by
  location" only when two figures differ. The employer's posted date shows only
  when every posting gives the same one.
- **A silence is not a difference.** One posting saying "remote" and another
  saying nothing is "Remote on 1 of 2 postings"; "varies by location" needs two
  different stated modes. Workday's "3 Locations" is a count, not a place:
  "listed as 3 locations, not named here", and it is never counted as one.
- **Both dates carry their labels**: "first seen today" (this board's crawl)
  and "employer posted 2026-03-13" (their board's own date). "today" loses its
  accent when the employer's date is more than 14 days older, and a group whose
  newest posting is later than its first says "latest posting ...", because
  Newest first sorts a group by its newest posting.
- **Every save button for one role repaints together** (`data-id`). A
  multi-posting card holds the lead posting's button twice, and repainting only
  the clicked one made the next click undo the save it appeared to make. The
  corner button on a grouped card saves ONE posting, and its label names where.
- **An office with a city and no state** printed "Itasca, null" on cards, role
  pages, the Saved tab and in search (416 postings). `officeText()` is the one
  way to print an office.

`check_the_job_card_says_what_the_posting_says` RUNS the card:
`scripts/jobcard_harness.mjs` loads index.html's own script into a node vm with
a DOM that absorbs everything and calls `jobCardHTML` on fixtures. It was built
because the suite had never executed a line of the public page's script.

## The page belt, the Scrub tab and Claude's inbox (owner, 2026-09-28)

The owner's drawing: company pages reviewed as a BELT, one company at a time,
alphabetical within a department (sector) and optional subsector. Left: the
public company page as it will publish, redrawn from the record after every
edit (`page_belt.preview_html`). Right: the title (editable; the old name is
kept as an alias) and a nine-item checklist, then the Jobs box. Skip (J) and
Submit (S). Code: `scripts/page_belt.py`, admin tab "Page sweep".

**The nine items, in his order:** website, job board, write-up (the
description and the write-up are one item to him), competitors, news,
supplier vs govtech, who buys it, founding year, ownership. Every item,
green or red, opens its editor on the belt. Green is a FACT the file shows,
computed on the server by `page_belt.checklist` - one definition, which
Submit records. Rules that were each wrong once (review, 2026-09-28):

- **Readable is the board's own verdict** (`board_verdict`: enumerable is not
  False and nothing unreadable), the same test the public page prints.
  refresh's `hiring.status` is a different question - "Yes" can be a keyword
  hit with no title - and 192 of 808 careers pages disagreed with it.
- **Who buys it is the MIX** - government only, or government and businesses.
  "Sells to government" alone is red; "does not sell to government" is an
  answer. The select pre-selects nothing.
- **Ownership needs an answer**: a parent, acquired, or independent. A check
  that recorded nothing (`acquisitions_checked_on` alone) is red "checked, not
  answered". A research claim that names the parent on file settles itself;
  a person's answer (`ownership_by`) settles it and dismisses the research row
  in Acquisitions. `admin._is_person(None)` is True, so test the field is
  PRESENT before asking whether a person wrote it.
- **"Not stated anywhere" with a year on file REMOVES the year** (journalled),
  then records the answered blank.
- **A red item links a queue only when that queue lists the company.**

**Add board proves ownership, it does not assume it.** A structured board is
wired when the judge says `matches`, or its slug shares ground with the
company (`discover_ats.slug_matches`) and the judge does not say MISMATCH. The
judge sees no name on Ashby, Lever, BambooHR, Breezy, Recruitee or
SmartRecruiters, so `unknown` is NOT a pass. A careers page must sit on their
own website's host. A LinkedIn/Indeed/Wellfound/Built In link must carry their
slug. Anything unproven is refused with its evidence; "Wire it anyway" sends
`force` and the journal says "wired anyway by a person". A pasted board
address is read as an address first (Greenhouse's page does not contain its
own URL).

**Submit approves even with red items.** An approved page never returns to
the belt. Its red items show in "Open on approved pages" (`q_signedoff`) and in
their own queue where one lists them - a company approved with everything but
a founding year still shows in Founding year. A page that LOSES something after
approval (green then, red now) is flagged "lost" there, never back on the
belt. A sweep coming due is the cadence, not a loss (`cadence` on the item;
`reds()` leaves it out).

**Write-ups.** "I don't like the quotes and links in the description. Use what
we have if we have it, I will add all of them manually." The public page
prints paragraphs only - no numbered sources, no quote. His write-up is saved
with `hand_written: True`, labelled "written by SLED JOBS", published as
written; the agent's pending draft for that company is rejected first, a
landing (`promote_profiles.land`) holds back rather than overwrite it and says
so, and a queue Accept is refused with that reason. The agent door still
guards what agents write.

**The Scrub tab** (`q_scrub`) is every board only a person can read: a careers
page the crawl cannot read, a place they post that a person can capture, or a
company sent from the belt ("Submit to workflow"). A structured board is read
nightly and never on it. Cadence: **14 days**, due ON day 14, one helper
(`cadence`) for the tab, the jobs box and the checklist. A sweep is any of:
jobs captured, no openings today, posts somewhere else, real board found - the
last two are stamped by Add board into `scrub.json` (`swept_on`, `swept_how`).
The badge counts DUE rows. The extension's worklist "scrub list (due)" steps
through it; a row click stores `ss-target` with the URL it opened, and the
panel pre-picks that company ONLY on that site (and on shared sites like
LinkedIn only under that company's own path); picking another company,
closing the panel or recording the sweep forgets it.

**Claude's inbox.** Every checklist item has an "explain" box for anything
that needs semantic explanation. It appends to `data/claude_inbox.jsonl` -
GITIGNORED, written with a plain append and never through the journal,
because the repo is public and the journal copies the why. Each row keeps what
the checklist showed when he wrote it (`saw`). Once a day, at the end of the
day, the open items are workshopped with him so the system understands what
is going on; `python3 scripts/inbox.py` lists them and `--resolve ID --status
workshopped|fixed|wontfix --note ...` settles one.

**The screen acts on what it shows.** Skip and Submit act on the card on
screen (`pbOnScreen`), never on an index, and do nothing while a card loads;
every card load carries a sequence number and a stale one is dropped. Submit
shows the prefetched next card at once and approves in the background; a
refusal brings the page back as the next card. The preview iframe is
`sandbox="allow-scripts"` for its one scroll-keeper script and must NEVER
gain `allow-same-origin` - that would hand a company page the console token.
The news check runs in a background thread (the admin is single-threaded)
and the card polls for the result.

## The desk publisher (owner, 2026-09-28)

What the owner does in the desk admin reaches the public site on its own:
`scripts/publish.py`, run by launchd every 5 minutes from his checkout
(`python3 scripts/publish.py --install` writes the plist from the checkout's
own paths - no path of his machine is committed - and `--uninstall` removes
it; `--status` and `--dry-run` for a look). **Cloudflare Pages is on the Free
plan ("be frugal")**: a cycle pushes only when something changed, never within
30 minutes of the last push, and only after 5 quiet minutes on the desk.

- **What it publishes is derived, not typed**: data files named by admin
  journal entries not yet on main, the two journals, and news.json /
  site_pages_index.json / identity_labels.jsonl (written by the desk the way a
  crawl writes). Anything else modified in the checkout is left out and named
  in the status. board.json and data/detail/ are never taken from the desk.
- **Merged on parsed JSON, three ways** (`merge_data.merge_text`, base = the
  checkout's HEAD, ours = the desk, theirs = main): companies.json field by
  field, a record the desk merged away is deleted when main changed only
  hiring/ats, news records by the later read, journals by line, decision
  files by key. A field both sides changed differently is a CONFLICT: nothing
  is pushed and Claude's inbox says which.
- **Built and checked in a throwaway worktree at main** (main's code runs,
  never the checkout's uncommitted code): quick rebuild, its --check, every
  staged JSON parses, the full selftest. Commit as westjw noreply, push never
  forced. The admin holds `data/.admin.lock` around every action (`desk_lock`)
  and the publisher takes it to snapshot and to sync.
- **The checkout comes up to main** only when every published file is
  byte-identical to the snapshot and nothing else dirty is in the way;
  otherwise the next cycle carries it. A local commit main does not have
  holds every cycle (inbox) - the base would be ambiguous.
- **Paused** while a data workflow (daily-refresh, discovery, news,
  write-profiles) is queued or running. Those workflows' push steps now
  resolve a rebase conflict record by record through the same merge rule
  (`merge_decisions.py --resolve`), taking the bot's freshly built board.json
  and data/detail/; the publisher's next cycle redraws the board if a desk
  edit is missing from it (news alone never triggers a redraw).
- The belt says when an edit goes live (admin meta `publish`).

## House rules

- **A page scan never proves absence.** `scan_pagetext` may return
  `unreadable` → status `Unknown`. Don't "simplify" that back into
  `None found`: a false `None found` silently deletes a warm door, which is the
  one failure this tool cannot afford. Assert a status only on concrete
  evidence in the text.
- **Never invent a fact to fill a field.** No estimated salary, no guessed
  founding year, no magic constant standing in for a count. If we do not know,
  the UI says we do not know. Every module here is tuned toward silence for
  the same reason (`salary.py` documents the trade-off at length).
- **Never hand-edit `data/hiring_history/*.json`** — snapshots are the audit
  trail. Hiring state changes only through `refresh.py`.
- **`data/schema.json` is the source of truth** for sector/category names and
  xlsx tab colors. To add a sector/category: edit schema.json, then move/add
  companies, then run `python3 scripts/selftest.py` (it enforces consistency).
- Keep `refresh.py` deterministic. AI-judgment work (finding a new company's
  ATS, deciding whether an odd title is an AE req) happens interactively, and
  its *conclusions* get written into `data/companies.json` (an `ats` entry, a
  classifier rule) — not into the run.
- New classifier edge cases go into `classify.py`/`roles.py` **with a matching
  case in `selftest.py`**: `CLASSIFIER_CASES` (title rules), `PAGESCAN_CASES`
  (`html` page-text rules), `TITLE_TEXT_CASES`, `FAMILY_CASES`,
  `SALARY_CASES`. A new invariant means a new case there.
- After any data or script change: `python3 scripts/selftest.py` must print
  **all checks passed**.
- **Never let a score reward volume over correctness.** The owner's rule, and
  the admin's scoring layer has broken it before. `check_admin_game` in
  selftest is what holds the line.
- Python: stdlib + `requests` + `openpyxl`. **No new dependencies, ever.**
  The one exception is `discover_js.py`, which is a separate script precisely
  so Playwright never enters the run path.
- The site is deliberately dependency-free — no build step, no framework. Keep
  it a single `index.html` unless the owner asks to graduate it.
- Statuses are exactly: `Yes`, `Sales (non-AE)`, `None found`, `Unknown` —
  renderer, exporter, and selftest all assume this set.
- **Do not write to the owner's live admin.** If you need admin state to test
  against, run your own instance on a port above 8700 pointed at a *copy* of
  `data/` under `/tmp`. On 2026-08-24 a build agent testing the scoring belt
  put 86 `set-founded` writes into the real `companies.json`. They are still
  there. They survive only because `journal.py` kept a before-image of each
  one, and they are now attributed in `data/admin_journal.jsonl` to
  `agent:overnight-build` with a `why` that says they are not human rulings —
  so `admin_undo.py` can take any of them back. That recovery is the safety
  net working, not permission to use it.

## Rows, openings, and the ids that hold them together

`build_board.py` gives every posting two ids, and the difference between them
is the difference between an honest headline and a flattering one.

- `opening_id` = `company::title`. One advertisement, however many places it
  was posted to.
- `posting_id` = `company::title::hash(url + location)`. One row as the board
  handed it to us.

Both must stay stable across runs. The hash is taken from the posting's own
content, never from its position in a list, because an id that churns when a
board reorders breaks every saved role and every shared link on every refresh
and turns the daily diff into noise. `opening_id` stays the prefix because it
*was* the id every shared link carried before disambiguation existed.

**The headline counts openings, not rows.** Xplor advertised one Account
Executive requisition in 93 cities; counting rows put a single advertisement
third on a leaderboard of the biggest go-to-market pushes in the market.
Today's numbers, from `data/board.json` totals: **617 quota-carrying rows
against 479 distinct openings** (4,360 rows, 3,711 openings overall). Re-derive
rather than quoting these — they move every refresh. Say it the way a reader
can check: "479 sellers wanted, advertised in 617 postings." The per-location
rows all stay; only the counting changes.

## The admin backend

`python3 scripts/admin.py`, then <http://127.0.0.1:8787>. Twenty-three queues,
and `LABEL` in admin.py is the list (this paragraph said fourteen for a month,
then twenty for another): Users, Write-ups to check, Agent proposals, Warm
leads, Boards we found, Founding year, Wrong bucket, Vendor scope, Scope
review, Submissions, Duplicates, Missing websites, No board found, Blocked
boards, Wrong placement, Unclassified roles, Acquisitions, Website review,
Conference dates, Conference floors, Open on approved pages, Scrub, Supplier
websites (a person releases a held supplier's website, 2026-10-05). Every key in `QUEUES` has a `RENDER.<key>` in admin.html and
`check_every_queue_has_a_renderer` keeps QUEUES and RENDER.* honest, and
`check_the_two_applier_lists_agree` keeps the two NO_APPLIER lists agreeing.

**No board found carries two kinds of row since 2026-09-26.** A company with
no board on file, and a company with a careers page ON FILE whose scan reads
nothing ("page scan found no listings", a JS shell, a 404 on the address).
The second kind - 589 companies, the page-only pile - was in no queue at all:
the queue took `ats.type == unknown` or the org's `unreadable` flag, and
build_board never sets `unreadable` on an html board (it sets `enumerable:
False`, because `unreadable` drives the public "we could not read them"
messages). The row says which kind it is and carries refresh's note. A fetch
that was TURNED AWAY (403, 429) goes to Blocked boards instead; a page-scan
board that produced a verdict is a readable board and is offered nowhere, and
the page sweep no longer prints "no readable board" on it.
`check_a_page_that_reads_nothing_is_offered_to_a_person` drives both. Work
the queue one sector at a time (`?sector=`).

The two newest are both about boards that may not belong to the company they
are filed under, and they are different questions. **Boards we found** holds a
board discovered INSIDE a careers page - the page named its own widget - and
asks whether to wire it up. **Acquisitions** holds a board already wired that
looks like it belongs to a parent, and asks whether to keep it, label it, or
unwire it. Both refuse to write on their own; both keep the refusals so the
next sweep stops proposing what somebody already said no to.

Acquisitions was READ-ONLY until 2026-08-24 — 74 rows, a `rule()` in
`scripts/acquisitions.py` that nothing called, and `acquisition_rulings.json`
never written once. It now takes the three outcomes that file describes
(**unwire** / **keep and label** / **not an acquisition**), and both ownership
outcomes refuse without a named parent, because a ruling that says "somebody
else" cannot be checked later.

Its evidence now arrives from four directions, ranked by how much they claim:
the board naming somebody else, a page that redirects, **a logo file shared
byte-for-byte with another company and fetched from that company's domain**,
and a slug that is merely odd. The logo signal finds acquisitions AND
duplicate records, which are different problems — `_same_company()` decides
which, and duplicates go to the Duplicates queue where a merge is the answer.

It is where the residue of every automated pass goes — the parts that need
judgment rather than a better regex.

### What guards it

This section used to say the admin writes companies.json "with no auth in front
of it". That has been false since the token landed. Loopback binding is not the
protection people assume, because a browser can reach loopback even when the
network cannot: any site the owner happened to visit could once have driven
this server. CORS was never the answer either — a
POST with `Content-Type: text/plain` is a *simple* request, so the browser
sends it with no preflight at all and the write lands whether or not the reply
can be read. What is actually there now:

- **A per-process token.** Minted at startup, never written to a file, echoed
  in an `X-Admin-Token` header on every `/api/` call. A cross-origin page
  cannot attach a custom header without a preflight, and this server answers
  none. `admin.py` injects a shim into `admin.html` on the way out, so the
  page's own `fetch` calls did not have to change.
- **A `Host` check.** DNS rebinding beats every same-origin protection —
  evil.example can resolve to 127.0.0.1 — but the browser fills `Host` in from
  the address bar, so it still reads evil.example. Anything not addressed to
  `127.0.0.1` / `localhost` / `::1` on our own port gets 421.
- **A static route allowlist.** This is not `SimpleHTTPRequestHandler` any
  more. That served the repository root, so `/.git/config`, `/scripts/admin.py`
  and `/data/companies.json` all answered 200 to anything that asked. Seven
  routes are served — `/`, `/admin.html`, `/capture`, `/capture.js`,
  `/assets/logos/*`, `/assets/mascot/*`, and `/preview/c/*.html` (the
  approval screen's iframe; the one route answering `frame-ancestors 'self'`
  instead of `'none'`, and the one non-/api/ route that renders an
  unpublished company on demand) — and everything else is 404 by construction
  rather than by check.
- **`/api/token` is refused to any web origin.** It needs no token of its own
  (it is where the capture extension gets one), so the `Origin` header a
  browser attaches and a page cannot drop is what keeps it to the extension,
  curl, and nothing a website can arrange.
- **It refuses to be framed** — `frame-ancestors 'none'` and
  `X-Frame-Options: DENY`. A token is no defence against a click on our own
  UI: a framed admin document is on the admin's own origin and carries the
  shim.
- **POST demands `application/json`**, which takes the request out of the
  simple class entirely.

`selftest.py::check_admin_http` asserts all of that against a real server on
loopback, because three of these were once true in a comment and false in the
code, which is the pattern that file exists to break.

### Rules that hold inside it

- **Every write is validated against the same invariants `selftest.py`
  enforces**, on the whole file, then lands atomically. A bad edit is refused,
  never half-applied.
- **Every write goes through `read_companies()` / `save_companies()`.** Never
  `write_atomic("companies.json", ...)` directly. `save_companies` journals
  the before-image and then writes, so an action *cannot forget* to record
  itself — which is the only reason the 86 agent writes above are recoverable.
- **PASS `by`.** It defaults to `"owner"` because most writes are his, and
  that default is a trap for every write that is not: nine actions once called
  `save_companies` with the action name alone, so an agent's patch, an
  extension's capture and a script's ruling were all journalled as the owner's
  rulings. `selftest::check_writes_name_their_author` is a SOURCE-level guard,
  because the failure is a missing argument and no call runs in a test.
- **A write that changes nothing must say so.** `act_patch` reads
  `body["fields"]` and used to return `{"ok": True, "message": "updated X"}`
  for a body with no usable fields — so a caller passing the field at the top
  level, which is how every other action here takes its arguments, was told the
  correction landed while the record was untouched.
- **THE RULE ONLY EVER COVERED `admin.py`.** Six pipeline scripts still
  write `companies.json` directly — `add_company`, `discover_ats`,
  `find_websites`, `refresh`, `merge_companies`, `promote_candidates`. That
  is how a merged-away record came back an hour after `merge_families`
  folded it, with one journal entry for the merge and none for the
  resurrection. (`conference_intake` and `wire_embedded` were moved onto
  `read_companies()`/`save_companies()` on 2026-09-02: intake tags hundreds
  of descriptions in one run, which is exactly the write the journal guard
  flags when it is not journalled.) `selftest::check_merged_names_stay_merged` is the
  backstop, keyed on the ids a merge actually deleted rather than on
  `also_known_as` — the alias version flagged EagleView and Concourse, which
  are two live records legitimately carrying each other's name while somebody
  decides whether they are one company.
- **A merge never loses research.** The survivor keeps what it has and inherits
  what it lacks; a discovered ATS always beats an `unknown` one; the dropped
  name is kept in `also_known_as`.
- **Evidence before the write.** Pasting a URL shows the page title, whether it
  is parked, whether it identifies the company, which ATS is behind it and
  whether the slug matches — then a person decides. A slug mismatch says so in
  red, because saving it would record a parent's postings as the subsidiary's.
- **An empty page scan is not a board.** Reading zero titles means the page is
  unreadable, not that the board is empty; the UI says so and relabels the
  button "Save anyway".
- **Dismissals are recorded** in `data/admin_dismissed.json` with a reason, so a
  queue shrinks when a person says "this is fine" and does not re-ask forever.
- **Hand family assignments are data, not rules.** `data/family_overrides.json`
  is keyed by exact title and read by `roles.family()`. Use it for titles with
  no pattern to write ("Manager", "Commercial Development"). A title that *does*
  suggest a rule still gets one in `roles.py` with a `selftest.py` case.
- Nothing in admin touches `data/hiring_history/`.

**The Sort board** is the same writes with a different grip. The queue tabs ask
one question at a time, which is right when the answer needs evidence; sorting
is a comparison job, and the fastest way to see a vendor is in the wrong bucket
is to see the bucket. Companies mode gives category columns for one sector plus
a rail of every other sector as a drop target; job-families mode drags
unclassified titles into a family. Cards carry their open-posting count, because
a company with a live board is the one worth getting right. Dropping onto the
rail sets sector *and* category together — setting the sector alone would strand
the old category and `validate()` would refuse the write, correctly.

**The web admin** (`admin-web.html`, `functions/admin/api/rule.js`,
`scripts/apply_web_rulings.py`) is the judgment half, workable from a phone:
five tabs as of 2026-09-25 — Vendor scope, Wrong bucket, Duplicates, Founding
year, and Users (a GRANT only; a revoke that waits a night is not a revoke,
so the instant one is the Access policy). The division of labour is the
design: the Worker only *appends an opinion* to a ruling file (committed to
the repo through the GitHub Contents API, keyed by id); the daily run applies
it in Python, where `validate()` lives — founding years and placements in
chunks of `journal.BLAST` with the count seen, merges through `act_merge`,
an `in`/`sled` vendor call back into the candidate queue so the card agent
gives it a sector, a grant through `act_user_grant` with the hash rule.js
made. A bug in the web half can mis-record an opinion and cannot corrupt the
map. The page keeps a session overlay over the build-time `rulings.json`
(which ships dismissals too), so a ruling stays ruled until the next deploy
catches up.

**The door on `/admin` is `functions/admin/_middleware.js`, on every
hostname, and it VERIFIES the Access JWT.** On 2026-09-25 the whole bundle —
every queue, every ruling with the owner's reasons, the owner's email hash —
answered 200 to anyone on the project's `*.pages.dev` alias: Access covered
the custom domain and not the alias, and `rule.js`/`whoami.js` trusted a
header's presence. The middleware checks signature, audience, issuer and
expiry against the team's keys; no token is 403, keys that will not answer
are 503, and `whoami`/`login` stay open because they hold nothing. Team and
AUD are constants with `ACCESS_TEAM_DOMAIN`/`ACCESS_AUD` overrides — a wrong
value is a 403 naming the variable, never an open door.

**Until launch the WHOLE site is signed-in only (owner, 2026-10-05).**
`functions/_gate.js` runs first in `functions/_middleware.js` on every page,
data file and endpoint, on every hostname. It checks the same Access sign-in
as the `/admin` door, and the verifier both doors share lives in
`functions/_access.js`.
- Launch is `GATED = false` in `_gate.js`, plus narrowing the Access
  application back to `/admin` (DEPLOY.md §3).
- Both doors hold only while the Function runs. On the Free plan the Pages
  project must be set to **Fail closed**, or a spent daily allowance serves
  every static file with no gate.
- Our own static files are read through `env.ASSETS`, never `fetch()` of our
  own url. That fetch carries no sign-in, so the gate refuses it.
- **One request passes without a sign-in: a mail client's one-click
  unsubscribe** (RFC 8058) - a POST to `/api/alerts` with a well-formed `?t=`
  and a form body (`isOneClickUnsubscribe`). Digests go out while the site is
  private and the provider's POST can carry no sign-in. alerts.js then
  requires `List-Unsubscribe=One-Click` and answers the same for any token.
  **This only helps while Access covers just `/admin`.** Once Access covers
  the whole host (DEPLOY.md §3 step 3), Access answers at the edge before any
  Function runs, so a mail client's button cannot work until launch. Making
  it work would take a second Access application with a Bypass on
  `api/alerts`, against the one-application rule; with one subscriber it is
  not worth it. The digest's own "Stop these emails" link works for a
  signed-in reader throughout.

**A phone ruling used to vanish in the nightly push.** `refresh.yml` said
`git pull --rebase -X theirs`; the bot's copy of a decision file won
wholesale over a ruling committed while it ran. `scripts/merge_decisions.py
--resolve` unions the keys now — both writers' rows kept, the bot's
`applied` flags overlaid — and anything else that conflicts stops for a
person.

## Every admin write is reversible

`scripts/journal.py` (the before-images) and `scripts/admin_undo.py` (the tool).

`write_atomic()` already guarantees a write is never *partial* and `validate()`
guarantees the file is never *structurally invalid*. Neither is the failure
this repo fears. The failure is a write that is complete, valid, and wrong —
one click on "All out" writes a ruling for 108 companies and all 108 pass every
check we have. A wrong "out of scope" is invisible: the company stops
appearing, nothing errors, no count looks odd, and nothing ever contradicts it.

So: a diff of exactly the records an action touched, with who, when and why;
a bulk action recorded as **one** entry so undoing restores all of it or none;
a refusal above `BLAST` records unless the caller passes `force=True` having
shown a person the count; and a refusal to undo a record something else has
changed since, naming the conflict rather than saying "no".

```
python3 scripts/admin_undo.py                    what changed recently
python3 scripts/admin_undo.py --show 2026-08-24#4
python3 scripts/admin_undo.py --undo 2026-08-24#4
python3 scripts/admin_undo.py --reopen 2026-08-24#4
```

`--reopen` is the one that matters for scope rulings. A ruling is never
re-asked, which is right for a correct answer and permanent for a wrong one.
Reopening deletes the ruling instead of reversing it, so the company returns to
the queue with fresh eyes. Use it when you are not sure you were right, which
is a different thing from being sure you were wrong.

## Facts that no dropdown could hold

Four small modules exist because forcing a messy truth into a menu produced a
confident falsehood. All four store what a person actually saw.

- **`scripts/notes.py`** — free text on a company, plus detectors that *suggest*
  a structured home for what the sentence hints at. The case that prompted it:
  "madison ai advertises on linkedin but used a job service that is on a board
  with multiple sites." Every dropdown gets that wrong, and "paste the board
  address" would file a **multi-tenant** board against one company, reporting
  every other tenant's postings as theirs. Detectors only suggest, and show the
  words that triggered them, because a note is a submission from your past self.
- **`scripts/posts_at.py`** — where a company posts when we cannot read a board.
  "Advertises every opening on LinkedIn" and "hires by word of mouth" used to be
  recorded identically, as a dismissal. They are opposite facts. This is its own
  field, not an `ats` type: `ats` means *monitored*, and filing LinkedIn there
  would make refresh try, fail, and record a zero. The card says "they post here
  and we are not counting it", links out, and never claims a number.
- **`scripts/identity_labels.py`** — what a person said when the website
  identity check got it wrong. Immediately it fixes the company (the correction
  lands in `also_known_as`, `identifies()` reads it, the panel goes green). Over
  time the labels *measure the check* — stored name, what the page said, the
  verdict — which is the "store the input alongside the answer" rule made real.
  It never loosens `identifies()` on its own: that rule is the only thing
  standing between a squatter and the dataset.
- **`scripts/salary.py`** — a stated range pulled out of description prose,
  which is worth doing only because pay-transparency laws oblige employers to
  publish one. A missed salary costs a filter hit. A wrong salary is published
  on a public board as a fact about somebody else's company. So it is tuned hard
  toward silence: no OTE, no M/B multipliers, no figure without a currency
  marker, no posting with two different ranges, sanity bounds per period, and
  periods stored rather than converted. If you are here because "it missed one",
  the fix is a new *anchored* form with a test case — never a loosened anchor.

## Pipeline agents: briefs out, proposals in

`scripts/agents.py`. An agent is a stranger who types faster, so it gets the
same deal a submission gets: **it never edits the dataset.** It reads a brief
assembled here, deterministically, and returns a proposal into
`data/agent_proposals.json`, which appears in the admin queue next to the
evidence. A person accepts or rejects. That keeps refresh and CI deterministic,
makes a bad model run cost a queue full of rejects rather than a corrupted map,
and turns each accept/reject into labelled training data.

Briefs are built here rather than by the agent because an agent that gathers
its own context gathers different context every run, and two proposals that
disagree then cannot be compared. Every agent must be able to answer *unsure*,
and intake refuses a proposal claiming high confidence without evidence,
because that is the shape a guess takes when a model is trying to be helpful.

Kinds on the spine, 2026-09-25: twelve in `agents.KINDS` - `bucket`, `read`,
`card`, `board`, `rival`, `profile`, `news`, `claim`, `family`, `fact`, `where`,
`buyer`. Eleven have an applier in `proposal_rulings.rule()`; `news` is the one
declared ahead of a producer, an applier and a renderer, deliberately, so the
queue can show it the day it exists (`NO_APPLIER = ("news",)`). `card`,
`family` and `where` render from raw fields in the proposals tab rather than
from a drawn card, which is why the one pending `card` reads as JSON. **The other end of the spine exists
now**: `scripts/proposal_rulings.py` is the one applier for every kind (the
admin action, the CLI and the web admin's apply step all call it), and the
admin's Agent proposals tab renders every kind from one table - it had a tab, a
count and no renderer for a month, and 131 rows were unreachable. A kind with
no applier refuses BY NAME rather than raising. `agents.save()` goes through
the journal. `ingest` KEEPS what a door refuses, with the rule that refused it,
because the profile gate review reads refusals and a door nobody can see being
wrong is a door nobody fixes. Every `promote_*.py` is covered by
`check_writes_name_their_author` by glob.

### Federal is out of scope (owner, 2026-09-12)

This file used to say federal was "the live case: it is selling tech to
government and it is not state and local, and only a person settles which of
those this board is about." **It is settled: federal is out.**
`solesourcejobs.com` is becoming a separate federal hiring board and those
roles belong there.

**`AMBIGUOUS_SCOPE` is now `FEDERAL_ROLE`**, because the old name said "we
have not decided" and we have. The old name is kept as an alias.

Three things changed in `build_board`, and the second is the one that mattered:

1. A federal role is **dropped**, not routed to a person. Nobody had ever
   ruled on the 7 rows the Scope review queue held - `scope_decisions.json`
   (the file admin.py actually reads and writes) is empty.
2. **It drops on EVERY company, not only `sled_only` ones.** 27 of the 40
   federal roles sat at companies carrying no flag at all - Motorola's eight,
   Workday's five, Palantir's two - where the whole board loads and nothing
   ever looked at a title. A federal account executive is federal whoever
   employs them.
3. **A person's ruling still wins in both directions.** The drop reads
   `ruling is None` first, so anyone who looks at a federal role and says it
   belongs is not overruled by a regex.

**`scope_pending` is gone from the posting shape.** It meant "kept by the
pattern, but the pattern is not sure" and federal was the only case it ever
held; a field that can only ever be None is dead code shaped like a
measurement. **The admin's Scope review queue is therefore permanently
empty** - decide whether to drop the tab. The mechanism comes back the day
there is a second genuinely ambiguous case, with whatever sets it written at
the same time.

**"FEDERAL WAY" IS A CITY OF 100,000 IN WASHINGTON STATE.** Federal Heights is
one in Colorado, Federal Hill a Baltimore neighbourhood - all of them exactly
the state-and-local buyers this board exists for, and `\bfederal\b` matches
every one. Same shape as the two capitals that were not a US state and the
city called "in-office preferred in San Mateo". None is on the board today;
`FEDERAL_CASES` in selftest holds them so that the day one is, a state-and-
local role is not deleted as a federal one.

**What it removes, measured before it ran: 40 of 5,582 postings (0.7%) across
14 companies, 38 distinct openings, and ZERO quota-carrying sales roles** - so
the "sellers wanted" headline does not move. Every drop is counted per company
as `federal_dropped` and printed in the build summary, because a wrong "out of
scope" is the one mistake this board cannot see: the company stops appearing,
nothing errors and no count looks odd.

**One company loses every posting: Granicus.** Both its roles are federal
account executives, so it renders as having none. That is a fact about this
board's scope and not about their hiring, and the build says so by name.

### Two questions off one set of pages: the `buyer` kind, added 2026-09-12

Every write-up answered *what does this company sell*. Nothing ever asked
*and who buys it*. **1,678 of 2,044 companies carry no answer** — 172 of them
with 548 live postings on the board right now. The pages that settle it are
already on disk: all 1,678 have a cached site record, fetched for a write-up
and read for one question when it could have been read for two.

`buyer` is a kind of its own, not a field on a profile, and the reason is that
**the two answers fail separately**. A write-up refused at rule 5 for naming a
customer the pages do not carry can still have read the buyer correctly off
the same page; a sound write-up can be paired with a buyer answer resting on
nothing. One status for both throws away whichever half was good. And **505
companies already have a landed write-up and no buyer answer** — under a
field-on-a-profile design they are unreachable for ever, because their profile
proposal is accepted and `brief_profile` never offers them again.

```
python3 scripts/write_profiles.py --category Police --limit 20   # both questions
python3 scripts/write_profiles.py --buyer-only --limit 20        # the 505
python3 scripts/promote_profiles.py --gate-buyer Police
python3 scripts/promote_profiles.py --land-buyer Police --by owner
```

**`--buyer-only` reads off disk and fetches nothing.** `fp.visit` always goes
to the network, so a buyer pass over 505 companies whose pages we already hold
would re-crawl 505 third-party sites to re-ask a question of bytes in
`data/site_pages/`. `--no-fetch` is the default in that mode; `--fetch`
overrides it for a company whose cache is stale enough to matter.

**The two verdicts are not symmetrical, and `check_buyer` is built around
that.** A `yes` is a claim about what a page says and needs a verbatim quote
from a page we fetched. A `no` is a claim about what these pages do *not* say,
which no quote can prove — so a quote is never required for one, and a quote
that IS offered is checked anyway, because a person reading the gate sees
something in quotation marks and takes it for evidence. `unclear` is the
answer for "the pages do not say", and it cannot be high confidence: the
verdict there is that there is no verdict.

**RETIRED 2026-10-08: no buyer verdict sets `sled_only`** - the rule below
put the filter on government-only vendors, the opposite of what it is for, and
hid 151 of 152 quota-carrying roles at 159 of them (see "Launch fixes"). The
paragraph is kept for the history.

**The model is never asked for `sled_only`, and rule 8 refuses it by name.**
That flag makes `build_board` drop every posting whose title does not name the
public sector, so a wrong one deletes real jobs off a public board and leaves
no mark. `buyer_sled_eligible()` *derives* eligibility from the two verdicts
using the rule the 2026-09-11 scope pass measured — verdict `yes`, no
non-government buyer named, high confidence — and it still takes a person:
`--land-buyer` records the buyer, and only `--with-sled` sets the flag, after
a gate review that prints how many postings are at stake. The admin's
single-row Accept never sets it and the card says so on its face.

**`--gate-buyer` has its own marker file.** Reading a category's write-ups
tells a person nothing about whether its buyer verdicts are sound — different
claims, different evidence, different way of being wrong — so `.buyer_read` is
separate from `.profiles_read` and `--gate` does not unlock `--land-buyer`.
`check_landing_refuses_a_category_nobody_gated` drives both through `main()`;
nothing had ever checked either refusal.

**`land_buyer` never overwrites an answer already on file** and never silently
skips: a company answered between the ask and the landing is held back,
counted and NAMED.

**The trap that cost the profile door nine refusals is clean here, measured.**
Both briefs build pages through one `_brief_pages()`, so a quote taken off the
brief's `lines` verifies against the full stored text the door checks —
15,480 of 15,480 quotable lines across 150 companies, zero false refusals. A
sample of that runs in selftest.

**IT IS CALLED `buyer` AND NOT `scope` ON PURPOSE.** The admin already has
*Scope review* (does this POSTING belong on the board) and *Vendor scope*
(does this COMPANY), so a third sense would be one word carrying three
meanings in one product — the defect this file names under "a sector is never
also a category". Free to fix the day it landed, because zero rows were stored
under the old key, and never free again. Buyer rows are also kept OUT of the
Agent proposals tab for the reason 104 Police write-ups were: 1,447 of them
would bury every read and board proposal in a queue nobody clicks through one
by one.

**Six guards, mutation-tested: 29 unique mutations, 28 caught** (plus 11
re-runs after the rename, confirming no guard was blinded by it). Both misses
are worth keeping:

- A guard asserted `sled_only_why` was non-empty, and a mutation that gutted
  the sentence left a truthy stub — "their own pages name no " — behind it.
  *A presence check is not an evidence check.* It now asserts the buyer
  sentence itself travels with the flag, and its re-run caught.
- **The standing one: nothing guards buyer rows staying out of the Agent
  proposals tab.** It is a queue-quality property rather than a correctness
  one, so it is named here instead of covered by a thin guard — if that
  exclusion is ever dropped, 1,447 rows bury every read and board proposal
  and no check will say so.

**THE FIRST LIVE CALL PAID FOR ITSELF, at three cents.** `split_answer` read a
flat `--buyer-only` reply under the combined ask's nested key and emptied it,
so a correct answer was refused at rule 2 for carrying no confidence — a
systematic refusal that read as the model's fault, on a path whose breaker
would have burned eight companies before stopping. Both reply shapes now go
through the real splitter and the real door in selftest.

**What the corpus can actually support, scanned before committing to a run:**
920 of 1,446 cached page sets (63%) carry government-buyer language at all, so
a `yes` has a sentence available; the other 526 will honestly answer
`unclear`, as Adobe did on the first real call — its homepage, Firefly and
catalogue pages name students, teachers and businesses and no buyer
organisation, so the answer was `unclear`/`unclear` at low confidence and NOT
sled-eligible. That is the house rule working: an `unclear` we can show the
pages for is a real answer, and it is one we do not have today.

### The read trial, measured 2026-08-24, n=25

**This corrects what this file used to say.** The old text asserted that
rendering a sample of 25 page-only boards in headless Chromium "recovered
zero". That is not what happens. On a fresh sample of 25 drawn at random from
the read worklist, a render recovered postings from **8 of the 25**, 26 rows in
total; 17 came back empty and are recorded as *"read produced nothing"*, never
as "not hiring". Three things separate that from the earlier zero, and all
three are about the reader, not the browser:

- **Read the child frames.** The finding that these are widgets in iframes is
  correct — which is exactly why reading only the top document comes back empty
  from a page visibly full of jobs.
- **The title decides, not the link.** Requiring the job-link shape before
  looking at a row is right on a job board and wrong here, where rows are divs
  with an `onclick`. It threw away 10 real reqs on Nearmap and 34 on Nedap.
- **Wait.** `networkidle` plus a few seconds; these lists draw late.

**A small sample is a small sample.** 25 of 806 is 3%, drawn once, and every
one of those 25 proposals is still `pending` — nobody has accepted any of them,
so none of it has reached the dataset. Do not extrapolate 32% recovery across
the worklist from this; re-measure on a bigger sample before anyone plans
around it.

The finding worth more than the rows: some of these pages have an **enumerable
ATS one link away**. Three of the 25 named one outright — Autura's iframe URL
hands over a Greenhouse slug, Nallian's page exposes a Workable address,
Dominion's careers link is already a Paylocity board. Reading a page is a
snapshot somebody has to re-take by hand; finding the board behind it is
permanent and `refresh.py` keeps it current. When a read turns up an ATS host,
*that* is the finding, and it belongs to the `board` agent.

Read `brief_read`'s docstring before running one — it carries the traps that
produce strings shaped exactly like job titles (testimonial bylines, filter
chips) and the method notes in full. And the group-careers trap is live here:
Nedap's page lists 34 reqs across five business units and only 9 belong to the
company on file. The boundary the bookmarklet holds, the agent holds too: read
the page you were pointed at, once.

### Council review: independent attempts, then a blind read (adopted 2026-09-01)

Taken from `karpathy/llm-council`, and it is a rule about **how things get
built here**, not a feature to add to the site. Its three stages: several
models answer the same question independently; each then ranks the others'
answers labelled "Response A, B, C", never learning who wrote which; a chairman
synthesises from the answers and the rankings together. Names are attached back
on only at display time, so a reader can follow along.

**The blinding is the part that does the work.** An attempt known to be the
main attempt gets defended. A reviewer who can see whose work they are holding
grades the author. Strip the label and the only thing left to judge is the
answer, which is the thing we wanted judged.

So, for anything load-bearing here — a parser that will publish facts about
somebody else's company, a scoring rule, a schema change, a guard:

- **Two or more independent attempts before one is chosen.** Not one attempt
  iterated. Iterating polishes the first idea, and the first idea is usually
  the most obvious one; this file is largely a list of obvious things that
  turned out to be wrong.
- **Review them without knowing which is which** — including mine, especially
  mine. An agent checking my work while knowing it is mine is the same broken
  measurement as ruling on a proposal you have already read.
- **Disagreement is the finding, not a problem to resolve.** Two readers
  splitting on one company is worth more than either one's confidence label.
  `agents.py` already refuses high confidence without evidence for the same
  reason: a lone confident answer is the shape a guess takes.
- **Synthesise from the winner, but graft what the losers got right.** The
  runner-up is usually right about one thing the winner missed. Discarding it
  wholesale turns a review into a vote.

This is the instinct behind two habits already here: `check_admin_game`, and
verifying a guard by breaking the production code and confirming it fires. An
answer nobody tried to break is not evidence.

**The corollary at display time: show the raw text beside our reading of it.**
llm-council prints each model's evaluation in full and puts the parsed ranking
directly underneath, so a person can catch the parser being wrong rather than
trusting it. Everywhere this project turns prose into a fact — `salary.py` on a
stated range, `roles.geography()` on a location string, `scan_pagetext` on a
careers page — the sentence it read belongs next to the value it produced. A
wrong parse is published on a public board as a fact about somebody else's
company, and it is invisible unless the source sits beside it.

**One place the same shape is live in the product** (noted 2026-09-01, not
ruled): the `miscategorized` card draws the proposed placement, then the
confidence, then the agree-rate, then the evidence — the answer before the
reasons. So the agree-rate it carefully collects measures agreement, and
anchoring produces agreement for free. Reordering it would cost the owner
ruling speed, so it is his call; it is written down here so the next person
notices rather than trusting the number.

## The 2026-09-02 review, and what it found about the guards

A 12-agent adversarial pass over the two days of work before it: 38 confirmed
findings, 5 refuted. Four were HIGH and all four are fixed; the shapes are
worth more than the list.

**An address was a free pass.** `wire_embedded.resembles()` returned True for
any `http` ref on the grounds that "the company name is not in it to find".
It is: Paylocity's ref is a URL ending in the tenant's registered name. That
one clause let 35 entries past the slug gate, and the board carried AEM's 29
requisitions as Earth Networks', General Code's as American Legal
Publishing's, Liberty Vote's as Dominion's — since discovery pass 2 in
August, not since the wire script. `tenant()` now reads the name out of the
address and it is judged like any slug; the 11 the corrected gate refused
were unwired through the journal and queued in Boards we found with live
sample titles. Five of them are probably renames (DZS/Zhone,
Momentus/Ungerboeck) — the script cannot tell a rename from a mistake and
must not guess, so the owner re-wires those with one click.

**The Worker used a name it never imported.** `alerts.js` used `NAME` five
times with `import { SITE, FROM }`. Every signup after that deploy 500'd,
and selftest read the file as text. It is now EXECUTED under node, against
the real `_brand.js` import — the first harness defined `NAME` itself and so
could never reproduce the bug it existed to catch. A guard that supplies
what it is testing for is not a guard.

**Six guards measured helpers, not wiring.** The paging check drove `_paged`
and would have stayed green with `fetch_workday` taking one page; the pacing
check drove `_host_gate` while `_post_json` could drop the call; the
open-actions check read each handler's own body while `act_worklist`
dispatches through a dict and a writer sat two hops down. Each now drives the
CALLER: the fetchers through a stubbed transport serving more than one page,
`_get`/`_post_json` through the gate with a real thread asleep in it,
`act_capture` in a sandbox, `background.js` under node with a fake `chrome`
(`scripts/worker_harness.js`). Every one was then verified by breaking the
production code — 27 mutations, 27 caught. **When a guard walks a call graph,
match names, not `name(`**: a dict-dispatched callee never sits before a
paren.

**Smaller, all fixed:** a 429 backoff written outside the host lock and
overwritten by a worker already asleep in the gate (now a separate
`_HOST_NOT_BEFORE`, read by the gate, written under the table lock);
`apply_task_notes` and `apply_web_rulings` writing `year_founded` as a
string (`validate()` now refuses anything but an int in 1800..this year);
the extension re-sending a refused capture forever (set aside with the
admin's reason, never re-sent); `act_capture` keying on `company::title`
while build_board keys on link+location, so two same-titled reqs collapsed
to one; a trailing chevron that beat both the sweep's dedupe and its anchored
nav filter (GFOA staged "CONTACT US ›" and 11 menu items twice); a re-sweep
that rebuilt staged files from bare names and dropped every `is_govtech`
flag; and `classify` counting "not already on file" against the labelled set
(1,282 new names where 686 were).

**Still open from the review, deliberately:** the sweep's "starts with a
preposition" rule drops "The Hartford" and "The Phia Group" — the docstring
chose that side and it is the owner's call to loosen; `BOARD_HEADING`'s
end-anchor still loses a real title that ENDS in a board word (none seen);
`_paged` dedupes within a page now, but no iCIMS or SmartRecruiters page in
the cache actually repeats a key, so that one is unmeasured.

## State chapter conferences: the 338, and the seven that were never directories

`data/state_events.json` (359 chapter events), `scripts/find_event_directories.py`.
A conference with no exhibitor directory cannot produce a single company, and
on 2026-09-02 exactly 7 of the 359 had one. All seven were wrong.

**THE SEVEN WERE MENUS.** APA Florida's "directory" yielded 146 names of which
the companies numbered zero: "Knowledge Center", "Sections Overview", "Back to
Main Menu", "Atlantic Coast Section". Every one of the seven was a chapter's
*sponsorship program* page - how to sponsor us - and the harvester was reading
the site's own navigation. Promoting them would have published seven conference
pages whose exhibitors were menu items. The gate accepted them because it took
any page that was not `suspicious` and graded good OR mixed, and association
menus say Group, Services, Resources and Partners as readily as vendors do.

**What the detector is, and what it is not.** Three signals were measured
against the 26 staged floors that produced real companies:

| signal | worst real floor | the seven |
| --- | --- | --- |
| names ending "Overview" / starting "Back to" | 0.05 | 0.315, 0.247, 0.237, 0.234 |
| a name that is another name plus a word | 0.053 | 0.247, 0.219, 0.172, 0.152 |

Threshold 0.10, zero false positives across all 52 staged files. **Two other
signals were measured and DO NOT WORK** - written down so nobody spends that
afternoon again. *The link profile*: "a real list links out, a menu links back"
is false, AWWA ACE is 97% same-host and NAHRO 98%, because real directories
link each exhibitor to a detail page on the association's own site. *Overlap
with the home page*: "navigation repeats on every page, so subtract the home
page" is elegant and confounded - AIRA, NAMPI, NASWA, NHCAA and NAFA all score
1.0 because their exhibitor list IS the home page.

**A `mixed` grade is now a judgement, not a finding.** Every real floor of 30+
names graded `good`; `mixed` covers both real small floors (3CMA, CoSN, PRIMA)
and menus. So `mixed` writes `needs_person` with the candidate url and no
`directory_url`. Agents propose, people rule.

**THE PARENT'S EVENT, which is the same error as the parent's job board.**
North Carolina Police Chiefs' organisation url was `myiacp.org/NC__Login` -
IACP's own login page, matched on the two letters in its path - and walking it
reached a real exhibitor list of 36 companies. It was IACP's NATIONAL 2026
Technology Conference; "north carolina" appears on it zero times. Accepting it
would have tagged three dozen companies with a conference they never attended.
`owns()` now requires the chapter's own domain, or the state named in the
address, the title or three times in the body. **On the parent's own server a
two-letter code is not evidence** - and never after a dot: `nigpabchapter.ca`
filed NIGP's ALBERTA chapter as California, which is the `[A-Z]{2}` bug that
once put postings in London, UK wearing a different hat.

**Stage 1 is a lookup table now, and that is why it works.** It used to fetch
each parent's home page and walk the FIRST link matching
/chapter|affiliate|section/. That found `apcointl.org/technology/spectrum`,
`awwa.org/careercenter`, `apha.org/membership`, and nothing at all for the ten
parents whose home page carries no such link: **26 parents, 338 events, 0
resolved.** `PARENT_LISTINGS` is researched per association, the same rule
`PARENT_SITES` already follows, because the wording differs per body - AWWA has
SECTIONS, WEF has MEMBER ASSOCIATIONS, NLC has STATE MUNICIPAL LEAGUES, NACo
has STATE ASSOCIATIONS. **0 → 185 organisation urls.**

Three bugs found on the way there, all now guarded:

- **Unescape the href BEFORE cutting the fragment.** NLC writes its links
  entity-encoded (`href="http&#x3A;&#x2F;&#x2F;www.akml.org"`) and a pattern
  excluding `#` to skip fragments matched `http&` and stopped. 19 links read
  off a page carrying 49. That one ordering is all 49 NLC chapters.
- **Half the listings put the state in a heading, not the link.** NSA writes
  the url as the link text under a state heading; WEF labels every one "Web
  Site". Neither carries the state where a text match can see it, and neither
  can be guessed from the domain - `calsheriffs.org` is California's and
  `flsheriffs` is Florida's. Position is the evidence instead, bounded to 700
  characters so a state named in prose cannot claim an unrelated link.
- **A sign-in page is not a chapter site, and the word is never tidy.** The url
  this rule exists for is `NC__Login`; NSA writes `/s/Sign_In` and
  `/OnlineJoinMain.aspx`. A `/login` prefix match caught none of them,
  including its own founding example. The path is split on punctuation AND
  camelCase and the tokens are checked, which keeps "registered", "portalside"
  and "Joinville" out of it.

**Statuses are facts and none is written as another**: `org_found`,
`directory_found`, `needs_person`, `list_is_an_image` (APA Washington's sponsor
list is a single file called `Thank You Sponsors.png`, North Carolina's is a
JPEG - a person can read those and a fetcher never will, which is the capture
extension's job), `parents_event`, `not_a_directory`, `no_directory_link`,
`no_chapter_listing`, `org_unreachable`.

**Five parents are recorded as answered rather than missing.** APTA and IAEM
have no state chapters at all. IACP, IAFC and NSBA have them and publish no
list. e.Republic runs its state summits itself - matching states against its
webinar calendar produced a link to "Responsible AI in Government" filed as
Indiana's summit, so it needs its own matcher against the event calendar.
`NO_CHAPTERS` and `NO_LISTING_PUBLISHED` hold those reasons so the search is
not run again.

**THE FIRST STATE FLOORS ARE IN (2026-09-02).** Stage 2 read 183 chapter
sites and found 16 real exhibitor lists; a re-verify with the corrected
ownership check dropped one and kept 15. 13 promoted, swept and intaken:
**177 companies already on the board gained a state event they exhibited at,
893 suppliers were filed, and 82 candidates went to the research queue.**
Maryland Municipal League alone lists 222 exhibitors, Vermont 123, Michigan
Sheriffs 117.

Two refusals worth keeping: Wisconsin Counties' page states no year, and a
tag without one cannot be read back six months later; Florida Sheriffs'
own site does not name Florida in its title, so nothing has confirmed the
organisation and the row waits.

`--promote` is the link that did not exist. `promoted` had been written by
register_state_events and read by nothing, so a directory found here could
never become a company. It requires four observed facts and refuses on any
one: a directory a fetch read as companies, an organisation name confirmed
by the parent's own listing OR by the site's own title (which must name the
state), an event name the page states, and a year.

**The subdomain hole, found in the re-verify.** `ace.awwa.org` is AWWA's own
national conference and stage 1 had handed it to the California-Nevada
Section as that section's site. `owns()` compared hosts exactly, so
ace.awwa.org was not awwa.org and the same-host rule passed it. A subdomain
of the parent IS the parent. Found the same day: a geo naming no single
state ("Multi-state", "WA/OR/ID", "NC/SC") switched the guard off entirely,
which is precisely where a regional body is most likely to be handed its
parent's event; those now require the body's own site or go to a person.

**APWA and GMIS answer 403 to an identified crawler.** That is their policy and
it was not evaded; a browser user-agent would have got the page. 33 events sit
behind it, honestly.

## Capture: the bookmarklet and the extension

`scripts/capture.js`, installed from <http://127.0.0.1:8787/capture>, plus a
Chrome extension in `extension/`.

**887 companies have a careers page on file that produces nothing** (counted
2026-08-24; `coverage.py` prints the live figure) — a person
looking at the page sees the jobs anyway. That is the worklist both of these
serve, and it is the single biggest hole on the board. Since 2026-09-26 it is
offered in the admin as the "careers page reads nothing" rows of **No board
found**; before that it was in no queue, which is one reason it stayed a hole.

Three things about capture are load-bearing:

- **It runs once, on click, over the current document.** It does not scroll,
  paginate, follow links, log in, or run on a timer. That is the line between
  reading a page you opened and harvesting a site, and it is why this is usable
  on LinkedIn when server-side scraping is not. The extension holds the same
  line by construction: `activeTab` + on-click injection means it can read
  nothing until you click it, and then only that tab.
- **The bookmarklet hands its result over on the clipboard**, and is
  self-contained for the same reason, which is why editing `capture.js` means
  dragging the button again.
- **The extension exists because a service worker with a host permission can
  reach the admin directly**, where a page cannot. It fetches `/api/token`
  itself (it is not same-origin, so it does not get the shim), retries once on
  a 403 because a token dies with the admin process, and stores nothing.

**A correction on the browser claim.** This file used to assert, as settled
fact, that a page on https cannot reach `http://127.0.0.1`. The observation in
Chrome was real; the generalisation was not. **Private Network Access is a
Chrome behaviour and Firefox and Safari have not implemented it**, so do not
repeat it as a fact about browsers or design around it as one. What the design
actually rests on: Chrome is the browser this runs in, the extension's host
permission is the exemption being relied on there, and the clipboard path works
everywhere and needs no permission at all.

Two rules the harvester learned the hard way, both worth keeping:

- A job link is the job **segment plus something after it**. Matching `/careers`
  alone returned CHALLENGES, SOLUTIONS and Cookie Preferences — the same nav
  chrome that fools page scans.
- **Position first, pattern second.** Take the first non-chip line as the title,
  then look for a location among the lines *after* it. Testing the location
  pattern first stole the title whenever one looked like a place: "Database
  Administrator, Infrastructure - UK" matched, and the row came back with
  Manchester as the job.

Captured postings live in `data/manual.json` and an automated run never deletes
them — absence from a refresh means the fetcher still cannot see that company.

## Submissions

`data/submissions.json`, reviewed in the admin Submissions queue. Outside
parties can send a company or a job. **A submission is a claim, not a fact**:
nothing reaches `companies.json` or the board without a person approving it,
for the same reason the fact bank refuses unverified records. Approving a
company runs the same identity and sector guess as intake and shows the
evidence, and reports low confidence as low rather than filing on one
incidental keyword. Approving a job writes it through the capture path.

## The admin queues are meant to become a game (owner, 2026-08-23)

Not to build all at once. But every queue built from here should make this
possible, because retrofitting means re-recording history.

The owner's reasoning, in his words: gamifying admin work would "help train
the AI, speed up backend manual work, and enhance the product if someone
else were to do it." The game is for him and possibly one employee.

### The mechanics, chosen against a 10-mechanic framework (owner, 2026-08-23)

The owner supplied a framework of ten gamification mechanics — eight safe,
two aggressive — and the product decides which fit. This product is a
two-person, correctness-critical review tool where every ruling becomes
training data and the asymmetric error rule holds: a wrong "not govtech" is
invisible and permanent, so care must always pay better than speed.

**Built into Start here (the core three plus framing):**

1. *Quests with a payoff you'd want anyway* (#4). Every recommendation
   names what the work buys: "rule the 16 hiring miscategorised companies
   -> the public Companies tab is correct today." The reward is the product
   working better, never a badge.
2. *Personal bests* (#5). The user against their own last 30 days of
   rulings. Zero social risk, works for one person, never demotivates.
3. *Visible craft signals* (#6). The why-coverage meter: what fraction of
   rulings carry a reason. The why is what teaches the classifier later,
   so its absence is the sloppiness worth making visible — and it is a
   CARE metric, which volume metrics are not.
4. *Named end states* (#1, as framing). Queues do not go to zero, they
   reach a state with a title: Wrong bucket -> "Clean shelves", Vendor
   scope -> "Scope settled". People finish things that have a name.

**Deferred until there are two players:** streaks on the smallest real
action (#2 — and the action is RULING, never opening the app), team-level
cooperative goals (#7 — one shared board-health bar, no individual
ranking), unlockable depth (#3 — only meaningful for strangers), surprise
recognition (#8 — sparingly, or it reads as spam).

**Rejected, with the framework's own caveats as the reason:**

- *Public leaderboard with relegation* (#9): works only where the metric is
  fully within the person's control and the population is competitive by
  self-selection. A two-person team reviewing ambiguous companies is
  neither, and ranking would reward exactly the speed the asymmetric rule
  forbids.
- *Loss-framed status decay* (#10): loss aversion is ~2x as motivating and
  reads as manipulation the moment it is noticed. Churning your one
  employee out of resentment is fatal, not a conversion cost.
- The caveat that governs both, kept verbatim: "both convert intrinsic
  motivation into extrinsic, and that trade is hard to reverse. Once people
  work for the points, removing the points removes the work."

### What that implies for everything built today (unchanged)

- **Every ruling gets an author, a timestamp and a reason.** A ruling
  without them cannot be scored, trusted, or learned from, and none can be
  added afterwards.
- **A ruling is training data.** Store the INPUT the person saw alongside
  their answer, or the label is useless for teaching the classifier later.
- **Keep the confident cases out of the queue.** Padding it with items a
  rule could settle is what makes admin work feel like a chore.
- **Never let a score reward volume over correctness.** `check_admin_game`
  in selftest enforces the three ways this has already been broken, and all
  three are the same rule: absence of evidence is reported as absence of
  evidence. The agree-rate is unmeasured until somebody rules against a
  proposal that was actually on screen. The belt only runs where the answer
  is on the card — Acquisitions is excluded, because deciding whether a slug
  belongs to a parent needs slow reading and a counter beside it would buy
  speed with accuracy. And the CSV export copies stored facts only, with
  anything a spreadsheet could read as a formula neutralised, because
  company names arrive here from outside submissions.

## Alerts and saved-role sync

`functions/api/alerts.js` (the endpoint), `alerts.html` (signup + settings),
`scripts/digest.py` (what an email would contain), `scripts/send_digests.py`
(the CI sender). Subscribers live in a **Cloudflare KV namespace bound as
ALERTS** — never in this repository, which is going public.

Owner's spec (2026-08-23): both audiences, cadence of every weekday morning /
Tuesday+Thursday / Wednesday weekly, delivered by email, paid eventually, with
"two customizations: cadence and threshold".

**Threshold is two things and both are built**, because they answer different
questions and only having one makes the feature worse:

- a **role bar** — which roles are worth telling you about (quota, family,
  seniority, sector, work mode, states), and
- a **volume floor** — don't email at all under N new roles. A daily alert
  that arrives saying "1 new role" is how a person learns to filter it. Roles
  under the floor are not dropped; they ride into the next email that clears
  it.

Rules that hold here:

- **No accounts, no passwords.** A subscription IS the identity: one long
  random token, mailed to the address, which also carries the saved-role sync.
  Nothing in this project should ever grow a password store.
- **Double opt-in, no override.** `send_digests.py` skips unconfirmed
  addresses and has no flag to stop skipping them.
- **Nothing sends without `--send`.** The dry run is the default.
- **`last_sent` advances only after a successful send**, so a mail outage
  delays roles rather than swallowing the window they were in.
- **Subscribe answers identically** whether the address is new, pending or
  already subscribed. Varying it makes the endpoint an oracle for "does this
  person have an account on a job board", which is exactly the question this
  site must never answer about somebody.
- **Unsubscribe deletes.** No suppression list — that is still a record of who
  wanted out of their job.
- **Saved roles stay local by default.** Sync is off until someone turns it on
  for one browser, and an unlinked visitor makes zero API calls. Sync carries
  **tombstones**: a union merge would let a stale phone resurrect a role you
  unsaved, forever. Tombstones compare against `saved_at` (a timestamp), not
  `saved_on` (a date), or re-saving something the same day loses to its own
  tombstone.
- **The vocabulary is duplicated and therefore guarded.** `alerts.js` restates
  roles.py's FAMILIES/SENIORITY/MODES because a Worker cannot import Python.
  `selftest.py::check_alert_vocabulary` fails if they drift — drift here is
  silent and total: the subscriber picks a value the endpoint happily stores,
  no posting ever carries it, and their alert simply never arrives.

Setup the owner does once, in this order: create the KV namespace and bind it
as `ALERTS`; add `RESEND_KEY` to the Pages project; add `CF_ACCOUNT_ID`,
`CF_KV_NAMESPACE_ID`, `CF_API_TOKEN` and `RESEND_KEY` as repository secrets.
A fifth repository secret, `ANTHROPIC_API_KEY`, is read by `write-profiles.yml`
only; that workflow is `workflow_dispatch` and its cron line is a comment
until the owner decides the nightly spend. (The four alerts secrets are
confirmed present by the 2026-09-17 refresh log; whether `ANTHROPIC_API_KEY`
is a repository secret cannot be established from this repo - it is OWNER-TODO
T1 in job-hunter, unticked.)
Every one is optional — with none set, the endpoint reports "not configured"
and the CI step prints that and exits 0. A refresh must never fail because
nobody set up email.
**THE FIRST EMAIL SENT ON 2026-09-01.** The KV namespace was already bound;
the mail half went in that night. `solesourcejobs.com` is a verified Resend
sending domain (SPF and DKIM written into Cloudflare DNS by Resend's own
auto-configure; DMARC deliberately not added yet), the API key is scoped to
sending from that domain alone, and a real signup produced a real confirmation
in a real inbox from `alerts@solesourcejobs.com`.

**THE CI HALF IS DONE TOO, and this section used to say it was not.** All four
repository secrets are set — the 2026-09-17 refresh log shows `CF_ACCOUNT_ID`,
`CF_KV_NAMESPACE_ID`, `CF_API_TOKEN` and `RESEND_KEY` all present — and a real
digest went out that morning: "1 subscriptions ... 159 new govtech roles".
**`sync_claims.py --write` has run nightly in `refresh.yml` since 2026-09-18**,
before build_board reads companies.json, and `claim_alert.py` in the same
workflow opens one GitHub issue when a claim waits on the hand gate (counts and
wait-age only; the repo is public). It has carried no real claim yet:
`data/claims.json` is `{}` and `data/employer_events.jsonl` does not exist,
because nobody has been verified. (This paragraph said "no workflow calls it"
for a week after one did.) And that same run **failed on exit 1** because the digest sent and the `last_sent`
write-back to KV did not — `advance last_sent in KV by hand, or accept the
repeat`, because re-running sends again.

**The hour this cost, so nobody spends it twice.** The secret was first saved
as `Resend_Key`, and the name was then corrected to `RESEND_KEY` by editing
the field in place. The row rendered perfectly afterwards — right name, "Value
encrypted" — and the Function still could not see the variable across two full
redeploys. Cloudflare appears to key the stored ciphertext to the ORIGINAL
name, so an in-place rename leaves a row that looks correct with nothing
behind it. DELETE THE ROW AND ADD IT AGAIN; never rename a Pages secret. The
dashboard cannot tell you this — the settings page and both deploy logs looked
healthy the entire time.

Diagnosing it took two requests and sent no mail, which is the method worth
keeping. `GET /api/alerts` with no token answers 400 `bad_token` when ALERTS
is bound and 501 when it is not. `POST` with a deliberately invalid address
answers 400 from `validEmail`, which sits immediately above the `RESEND_KEY`
check and below the `ALERTS` one. Those two replies bracket the failure to a
single binding without putting anything in anybody's inbox.

To see what an email would say, without any of that:

```
python3 scripts/digest.py --preview --quota --since 2026-08-22 --today 2026-08-24
```

## Coverage: read `scripts/coverage.py`, not the raw fraction

```
python3 scripts/coverage.py [--by-sector]
```

"839 of 1,722 monitored" was wrong in both directions and it drove bad
decisions for a while. It counted a careers page nothing can enumerate the
same as a Greenhouse API, and it counted companies that have **no job board at
all** as a gap to be closed. The honest split, re-derived 2026-09-25 across
2,045 companies:

```
structured   356  17.4%  a real API. Titles, locations, links. THIS is the number to move.
page only    808  39.5%  a page a person can read and a fetcher mostly cannot.
blocked      169   8.3%  a bot wall or transport error. We learned nothing. NOT a zero.
absent       591  28.9%  checked, no public board exists. A finished state, not a gap.
unchecked    121   5.9%  never probed, or probed before the current rules existed.
```

327 companies currently show at least one open posting.

**Run the script; do not quote this block.** These moved by one while this
section was being written, because a discovery pass was running in another
session. A number copied out of a document is how the old "839 of 1,722" got
believed for months.

The two ratios the script prints mean different things and neither is
"coverage":

- **1,164/2,045 = 57%** — we have *some* board on file, against every company.
- **1,164/1,454 = 80%** — the same numerator against companies that have a
  board to find (total minus `absent`). This is the denominator that can be
  worked. It is **not** 80% readable: 808 of that 1,164 is the `page only`
  pile, which is mostly not enumerable at all.

A 15-agent field audit (n=90 random re-probe, plus 24 investigated by hand)
found **55-63% of the "no board found" pile is genuinely boardless** — small
SLED vendors hiring on LinkedIn or by email. Rebuilding discovery on that
audit's findings and A/B-ing it on a fresh 70-company sample recovered
**1-2 structured boards per 70**, against a projection of ~10. Measure, then
report the measurement; the projections in that audit were drawn from a sample
selected for being interesting.

So: **`page only` is a worklist for capture and the `read` agent, not
coverage.** Converting those to `structured` is mostly impossible — there is
often no ATS behind them to find. Do not add the two together in a status
report. And `blocked` is not a zero: those probes learned nothing and requeue
in 7 days.

## Build order (owner, 2026-08-23)

This repo comes first, and inside it: front end, back end, data, admin. The
job-hunter repo waits. The owner's framing is that the board is the product
being launched, so anything that makes the board better for a stranger
outranks anything that makes his own search easier.

Practical reading of that when choosing what to do next:

- A gap a visitor would notice beats a gap only the owner would notice.
- Data completeness beats new features: `data/suppliers.json` holds 7,919
  catalogued suppliers (5,143 unruled, 1,578 of them real cards with a
  description and a website; `stage_suppliers.py` lands them per sector and
  has never been run with `--land`) and
  `data/conference_intake/govtech_candidates.json`
  holds 928 researched candidates, none of them on the board yet. That is
  worth more than another filter. (Counted 2026-09-25 — re-derive, don't
  quote.)
- Admin work is product work here, because the queues are what keep the data
  honest, and one day they are meant to be playable.

## Queued: an iOS app, and it is an ADMIN app

**Expo / React Native** (owner, 2026-08-25), chosen over a PWA and over native
Swift for push notifications, which iOS does not give a PWA reliably.

**It is for RULING, not for browsing**, and that was the owner's correction to
a worse plan. The first version of this note argued the app should wait
because "a phone app over a map with 2,769 unmade rulings ships the same
mistakes to a smaller screen." That argument dies the moment the app is the
thing that REDUCES the 2,769. It also has the better justification on its own
terms: the public board already works on a phone (verified at 375px), so a
reader app duplicates something that is not broken, while the ruling half is
where the actual bottleneck is.

The web admin already does two queues from a phone, behind Cloudflare Access.
What a native app adds over that, and the only reasons worth a second
codebase:

- **Rule offline.** A subway, a plane, a conference floor. Decisions queue
  locally and sync when there is signal. The web admin needs a live Worker
  for every single ruling.
- **A gesture instead of a tap.** Vendor scope and wrong bucket are one
  question with three answers; a swipe rules faster than a button, and the
  queue is 480 items deep.
- **Push when something is wrong on the public site**, which is the one
  notification that has ever mattered here: 16 miscategorised companies are
  visible to strangers right now.

**Which queues belong on a phone, and which must not:**

| Queue | Phone | Why |
| --- | --- | --- |
| Wrong bucket (237) | yes | The answer is on the card |
| Vendor scope (243) | yes | Same |
| Founding year confirms (177) | yes | One tap, the year is already there |
| Duplicates (69) | maybe | Two records side by side needs width |
| Board proposals (82) | maybe | Wants the board opened in a tab |
| **Acquisitions (59)** | **NO** | CLAUDE.md already excludes it from the belt for the same reason: deciding whether a slug belongs to a parent needs slow reading, and a fast grip buys speed with accuracy |

Everything the Worker rule holds still holds: the app **appends an opinion**,
and `apply_web_rulings.py` applies it in Python behind `validate()`. A bug in
a phone app must not be able to corrupt the map.

## Portfolio Dashboard Sync

This project is tracked in a personal portfolio dashboard. Maintain
`portfolio-status.json` in the project root throughout every session.

**Project ID:** govtech_dock
**Repo:** westjw/govtech-dock (private until the owner flips it — see DEPLOY.md)

### Rules
- READ portfolio-status.json at the start of every session
- UPDATE it whenever a significant feature is completed, a section of the
  market map is published, or the public board changes state
- ALWAYS update it at the end of every session before wrapping up
- Commit it with: `git add portfolio-status.json && git commit -m "chore: portfolio sync"`

### Format - keep every field current
```json
{
  "project_id": "govtech_dock",
  "repo": "westjw/govtech-dock",
  "last_updated": "[ISO timestamp]",
  "status": "Active Build",
  "progress_pct": 0,
  "progress_note": "~0% - [what just changed in one line]",
  "next_move": "The single most important next action right now",
  "session_summary": "2-3 sentences on what was built or published this session",
  "recent_work": [
    "Specific thing completed"
  ],
  "blockers": []
}
```

Do not change project_id or repo. All other fields should reflect reality
after every session.

**On progress_pct honestly.** The scope the owner set is "free as a board,
paid as a product". Report against BOTH halves, not just the half that is
nearly done, and say which frame the number uses. The free board being
shippable is not the project being 90% finished — the paid half has a model
now (below) but still no pricing, no billing and no accounts, and the employer
side is built and dark. A number that quietly means "the free board" is the
kind of stale fact this file exists to prevent.

## The business model (settled 2026-09-16, by the owner)

Written down because it was tribal knowledge and it decides what gets built.

| | who pays | what they get |
|---|---|---|
| **free** | govtech companies | claim their page, write their own description, change their own logo, **post jobs — free** |
| **paid** | *the same companies* | SLED HQ: council agendas, leadership, procurement vehicles |
| **paid** | job seekers | the job reviewer — job-hunter productized |

**Employers and sellers are one audience.** A company selling to cities is the
same company hiring for it, and the board is the top of the funnel for the
customer SLED HQ is sold to. Do not build two audiences, two logins or two
funnels; a feature that only makes sense for "employers" as a separate market
is a feature aimed at somebody who is not here.

**Posting jobs is free, and abuse is handled by the verification gate rather
than by a price.** A posting fee was considered and rejected: the owner
verifies every claim by hand before a company can self-serve, so the cost of
abuse is paid at the door once, not metered forever. This is why the gate is
load-bearing and why nothing may route around it.

**The claim ladder, in the owner's order:**

1. Somebody at the company asks to claim the page.
2. They confirm from an address at the company's own domain.
3. **The owner verifies them by hand.** This is a person, not a rule, and it
   is the only abuse control the free tier has.
4. They get a welcome mail.
5. From then on it is self-serve: their description, their logo, their job
   posts, without the owner in the loop each time.

Still refused after verification, for the reason `claim.js` already states:
**competitors** (who a buyer shortlists you against is not yours to edit),
**category** (a request, never an edit), and **any other company's record**.

**Where the ladder lives, so nobody rebuilds it.** `functions/api/claim.js`
takes the claim and the confirm and writes only to KV.
`scripts/verify_claims.py` is the owner's gate: it lists what is waiting,
re-checks the domain against the website on file, sends the one welcome mail
and appends `claim_verified` (or `claim_refused`, which needs a `why`) to
`data/employer_events.jsonl`. `scripts/sync_claims.py --write` pulls KV,
ingests corrections and then lands the ones a verified claimant sent, through
`proposal_rulings.rule()` — the same door the owner clicks.
`scripts/logos.py` is the only writer of `assets/logos/`.

**THE AUTHORISATION IS READ FROM THE REPO, NEVER FROM KV.** Whether an edit
may land unreviewed is answered by `employer_log.verified_claims()`, replaying
an append-only file in git that only the owner's gate writes. The claim
endpoint stamps `self_serve_expected` on the proposal and publishes verified
token tails in `meta-claims.json`, but both exist only so the claimant is told
the truth about what happens next; nothing downstream believes either. If a
KV flag decided who may write, "a bug in the claim endpoint cannot corrupt the
board" would stop being true. `check_kv_cannot_certify_its_own_claimant` is
the standing guard, and verification is per claim tail, not per company —
two people at one company are two decisions.

**Handing a claim back leaves a tombstone.** `release()` deletes the KV
record, and sync_claims projects the log from the records that still exist —
so a release produced no event at all and `verified_claims()` kept the tail
forever, landing edits for somebody who had walked away. `claimrel:<id>:<tail>`
is written *before* the delete and is what makes the release an event.

**An unreviewed edit is logged as `proposal_self_served`, never
`proposal_accepted`.** The second kind means a person read it. Folding
self-serve into it would make every accept rate the employer log reports a
false claim about how much of the map a person has actually seen.

**The no-password-store rule survives on the free tier.** "The subscription
token IS the identity, and this project should never grow a password store"
still governs employers. If the paid tiers need real accounts, that is a
decision to take deliberately for those tiers — it is not licence to put a
password behind a company page.

## Every published link is re-asked weekly (2026-10-05)

A website is judged once, at discovery, and `site_identity.py` re-reads only
pages cached back then, so nothing went back to look.
- gwfathom.com's careers link stayed on Fathom's public page for three weeks
  after the website on the same domain was taken down for casino spam.
- `scripts/link_check.py` (`links.yml`, Wednesdays) fetches every website,
  careers page and posts-at link live, and writes `data/link_health.json`.
- `build_board` does not publish a link found serving spam or a for-sale
  page. The record is untouched; a person fixes it.
- "Moved" (usually an acquisition) and "unread" are shown, never hidden.

**The first full run flagged 33 links as spam and 31 were vendors.** BondLink
says "slot" seven times (time slots), Trimble "spin" ten times (a spinner).
- The real hijacks use five or more different gambling words.
- So spam needs three different ones, or an unmistakable word in the title.
- A for-sale page is judged by the words a reader sees. A registrar's name in
  the markup is not enough: legacymark.com loads a GoDaddy script.
- Mutation-tested 13 of 13. Read the flagged list before trusting it.

**"No answer" says which silence (2026-10-08).** The 10-07 run listed 61
websites with no status and no reason. Re-asked, 23 answered over plain http,
and the rest were five different failures: domains gone from DNS, expired
certificates, certificates for another address, timeouts and dropped
connections.
- `why` on each such row names the failure. Only "no such domain" says the
  address itself is dead.
- `plain_http` is where the same address goes over http. It is evidence only:
  the published https link stays unread, because that is what a visitor gets.
  thecitybase.com's certificate expired and over http it lands on Euna, so
  `moved_to()` hands that to the Acquisitions queue.
- A domain that is for sale over plain http is hidden like one over https.

## Jobs posted on SLED HQ reach the board through a feed (2026-10-05)

The recruiter side lives in SLED HQ (owner, 2026-10-01), and HQ never writes
this repo.
- `scripts/hq_jobs.py` reads HQ's feed nightly in `refresh.yml`, before the
  board is built, into `data/hq_jobs.json`.
- `build_board.merge_hq` lists those jobs the way `merge_manual` lists
  captures, and `quick_rebuild` does the same.
- Without the `SLEDJOBS_FEED_TOKEN` secret it does nothing. HQ is not
  deployed yet.

The rules follow the contract agreed with the HQ session on 2026-10-04:
- Any answer but 200 keeps the jobs on file, because an error is never
  "no jobs".
- A 200 carries the whole list.
- Bad jobs are refused one at a time, each with its reason: an unknown
  company, a company with no website, an apply url off `hq.<domain>`,
  vocabulary outside roles.py, or a closing date already past.
- No description is stored, and no token or body is ever logged.
- At build time a job is dropped again if its date passed or its company lost
  its website.
- An HQ row is keyed `company::title::hash` like every row, with `hq_id` beside
  it, so it groups with the same title off the company's own board.
- Its pay is labelled "stated by the employer on SLED HQ".
- `check_sled_hq_jobs_reach_the_board` covers this. Mutation-tested 15 of 15.

## Launch fixes, 2026-10-06: what each guard holds

A launch audit (the checklist lives in a claude.ai artifact, not the repo)
found these. Each rule has a check that was broken on purpose to prove it
fires.
- **A 0 is printed only for a board we read.** `build_site.board_state()` and
  index.html's `boardState()` split read / unread / none. A company with no
  board on file printed "0 open roles" and "a board we read every night".
  `check_a_zero_is_only_printed_when_a_board_was_read` runs both languages
  over every organization.
- **A person's `out_of_scope` ruling on a company keeps it off the board.**
  `build_board.ruled_out()` serves main() and quick_rebuild, and discover_ats
  and refresh skip the company. Nothing read the field before, so discovery
  re-wired Concourse after the owner ruled it out.
- **Alerts:** an unconfirmed signup expires in a week (`putSub`). Every
  mail-sending endpoint has an hourly cooldown per address and a daily
  allowance per caller (`_mail.underDailyCap`), counted before the address is
  looked up so it cannot become an oracle. `alerts_harness.mjs` runs the
  handlers.
- **The shared KV store:** ratings reads come from the edge cache, the vote
  cap is per IP (not IP plus user-agent), and beta guesses are capped per
  caller rather than by one global counter.
- **The Actions logs are public.** The digest names a subscriber by four
  characters of its KV key, never by any part of the address.
- **robots.txt does not block /data/.** The app draws every page from it.
- **Share cards record the domain they were drawn for**
  (`assets/og/rendered.json`), because the domain guard cannot read a PNG.
- **`selftest.yml` runs the suite on every human push.** It reports; it cannot
  hold a Pages deploy back. DEPLOY.md §3 has the way back to private.
- **SLED_ROLE held BACKSPACE characters for seven weeks** (2026-08-21 to
  10-07): its `\b` word boundaries were written as 0x08, so "SLED", "gov",
  "government", "govtech", "K-12" and "state and local" never matched. Fixed;
  `SLED_ROLE_CASES` drive out_of_scope() and
  `check_no_control_characters_in_source` refuses any control character in a
  tracked source file.
- **`sled_only` has two meanings in this repo, and they point in opposite
  directions.** build_board reads it as "drop every title that does not name
  the public sector", which was built for horizontal vendors (Anthropic,
  OpenAI). The 2026-09-10/11 runs set it on 159 companies whose sites name
  only government buyers, reading it as "sells only to government". Measured
  live on 64 of those boards: 2,683 postings listed, 22 shown, and 1 of 152
  quota-carrying roles shown (OpenGov, Granicus, Tyler, Mark43...).
  **Owner, 2026-10-08: cleared on the 159 (Anthropic and OpenAI keep it),
  and the rule retired.** `agents.buyer_sled_eligible` returns False,
  `promote_profiles --with-sled` refuses by name, the three one-off appliers
  no longer write it, and `check_nothing_derives_sled_only` holds the line:
  the flag is a person's Vendor scope ruling ("sled") and nothing else.
- **The repo keeps no job ads (2026-10-08).** `data/jd_cache.json` held 816
  whole descriptions; it, the hand captures in `manual.json` and their
  journal images now keep only the characters `salary.parse` reads around
  each money figure (`salary.pay_excerpt`: 120 before, 35 after; 1.9% of
  the text, parse-identical on all 816) plus a read marker (`read_on` /
  `jd_read`).
  The whole text goes to `data/jd_local.json`, gitignored, for job-hunter.
  `check_the_repo_keeps_no_job_ads`. The old text is still in git history;
  rewriting that is the owner's call.
- **Nothing listed is a non-job (2026-10-08).** `roles.not_a_listing` -
  junk, talent pools, expressions of interest, internal-only reqs, unfilled
  placeholders, the employer's own name - is asked at capture, at crawl, in
  the manual merge (captures made under an older rule) and on a redraw. 22
  rows came off. `check_no_listing_is_a_non_job` pins real jobs that share
  the words ("Internal Audit Manager") and reads the published board.
- **A news label is a claim (2026-10-08).** CONTRACT needs a contract word
  beside its verb, a third-person adoption verb, or a public body partnering;
  FUNDING needs money; a blog-shaped headline gets neither (`news.NEWS_RULES`,
  `BLOG_SHAPE`). `build_board.news_for_board` re-reads every stored item's
  label from its headline, so a rule fix reaches all of news.json at the
  next build. `check_news_labels_make_no_false_claims`.
- **A company page never shows another company's newsroom as its own
  (2026-10-09).** Some websites on file are a page on someone else's site:
  InitLive's is bloomerang.com/volunteer, DaySmart Recreation's is
  daysmart.com/recreation, TSO Mobile's is zonar.com/acquisitions/tsomobile.
  The sweep followed each site to its newsroom, so those pages printed
  Bloomerang's, DaySmart's and Zonar's news as their own.
  - `build_board.newsroom_owner()` decides. The site's newsroom belongs to
    someone else when the site's brand is the parent's on file, or is in
    none of the company's own names.
  - It does not count a language path (`/us-en`) or a whole site of the
    company's own, even a renamed one (cardyai.com for Cardinality.ai).
  - The news is withheld with state `hosts_news` and `news_by`, which is
    set only where it applies (a key on every organization would add 37 KB
    to the first load).
  - Both renderers say whose newsroom it is.
  - `check_a_company_page_never_shows_another_companys_newsroom`.
- **Browsers keep images a day and board data five minutes** (root
  middleware `CACHE_RULES`, private, GET 200 only) - every request is a
  Function call against the free plan's daily allowance.
- **Job markup for Google is OFF (owner, 2026-10-08).** `JOB_MARKUP = false`
  in functions/_middleware.js: Google requires the full job description and
  the repo keeps no ad text. jobLd() and its guards stay so turning it back on
  is one line; gate_harness proves a role page attaches no JobPosting block.
- **The nightly is scheduled 05:23 UTC** because GitHub starts it 4-9 hours
  late; the watchdog also asks GitHub whether the last Cloudflare deploy went
  out (`watchdog.deploy_faults`).
- **A company with no board says which kind**: none found, turned away
  (`probe == "blocked"`), or not looked for yet. `no_board_note()` and
  `noBoardText()`. A board we read whose every role this board leaves out
  says so with the counts (`scope_note`), never "empty right now".
- **jobcard_harness runs the app's company view, `co()`,** on fixtures and
  captures what it writes to #view. The helpers agreeing proved nothing about
  the view.
- **No markup carries code (2026-10-09).** index.html drew 33 inline
  handlers, five as `onclick="openRole('${esc(p.id)}')"`. That was never
  escaped: the HTML parser decodes `&#39;` back into a quote before the
  handler compiles, so five French titles with an apostrophe threw a
  SyntaxError when clicked, and a title written to close the string would
  have run as script on the site.
  - A control names its action in `data-click`, `data-input` or
    `data-change`, and the value it needs in `data-arg`. `dispatch()` looks
    the name up in the frozen `ACTS` table with an own-key test.
  - An action takes an id or a name, never a url and never HTML.
  - Logos carry `data-mark`. `load` and `error` do not bubble, so one
    capture-phase listener on the document handles them. A row built
    off-page must be appended in the same synchronous pass.
  - `check_no_markup_carries_code` scans the hand-written pages, builds the
    site and parses every page, holds ACTS against the names used, and drives
    `dispatch()` with an id carrying `'`, `"` and `\`.
  - The same inventory found six places putting outside text in raw
    (conference links with no scheme check, the logo path, claim.html's
    address, the beta page's date, the issue number, the web admin's links).
    All fixed; `check_outside_text_never_becomes_markup`.
  - This is step 0 of a content security policy, which needs no inline
    handlers to be worth having.
- **A content security policy on every page, built from the pages
  (2026-10-09).** The second wall behind escaping: if an escape is ever
  missed, the browser still refuses an injected handler, `javascript:` url,
  script or eval.
  - **Nobody types a hash.** `build_site.main()` calls `csp.write()` LAST.
    It reads the pages as they will ship, hashes every inline script, and
    writes `public/meta-csp.json` with one policy per kind of page (app,
    company, static, alerts, claim, admin, none, holding).
    `functions/_csp.js` reads it once per isolate and sets the header, so an
    edit changes the page and its hashes in the same deploy.
  - **The build refuses what it did not write.** Each kind of page may carry
    only the scripts cut from its sources. Anything else stops the build,
    naming the file and leaving the last deploy live: an inline handler, a
    `javascript:` url, an external script, a nonce, a `<meta>` policy,
    `<base>`, `<object>`, `<embed>` or `<iframe>`, or a page of unknown kind.
    So an escaping bug cannot get itself hashed and blessed. A new kind of
    page needs a line in `csp.kind_of()` and `csp.ROUTES`.
  - **The nonce is minted per response and never written into our HTML.** It
    exists for the Bot Fight Mode snippet Cloudflare injects (seen live).
    The policy allows only that snippet's `/cdn-cgi/challenge-platform/`
    path, not `'self'`.
  - Styles stay `'unsafe-inline'`: the pages carry thousands of style
    attributes, and a style cannot run anything. A hash or nonce in
    style-src would switch it off.
  - **It ships reporting** (`CSP_MODE = "report"`, Report-Only header). The
    gate's holding page is enforced from the start. Flip to `"enforce"`
    after a console check on the live site (DEPLOY.md §3). selftest refuses
    `GATED = false` while it still reports.
  - **It never costs a page.** A missing, garbled or unreadable manifest
    sends the page untouched, keeping only the frame headers. Frame headers
    are on the token pages and, since this change, on `/admin` too, which
    had none.
  - `check_every_page_carries_a_policy_built_from_itself` covers it:
    - plants nine things the build must refuse;
    - re-hashes every built page with a second reader;
    - drives every spelling of every kind through the real middleware with
      the real manifest;
    - checks that a broken manifest costs no page.
  - Tested in a browser with the policy ENFORCED on a local build. Every
    kind of page ran. An injected onerror, a `javascript:` link, an injected
    script and eval were all refused.
  - A blind review (4 reviewers, 4 skeptics) confirmed 12 findings, all
    fixed. The ones worth knowing:
    - **A 304 carries no policy header.** The browser copies a 304's
      policy over the page it stored. A frame-only header (ours, or the
      `_headers` line Pages adds to its bare 304) would have left a cached
      /alerts with no script policy in enforce mode.
    - **The build reads raw text too.** html.parser and browsers disagree on
      `<!-->`, `--!>`, `<svg><style>`, a `</noscript>` inside an attribute,
      and duplicated attributes (browsers keep the first). So after cutting
      out the scripts the build wrote, `csp.raw_problems()` refuses any
      handler, `<script>` or `javascript:` attribute left in the text.
      `is_js_url()` strips tabs, newlines and leading controls the way a
      browser does.
    - **SVGs are served sandboxed.** The logos come from company websites.
  - **An `/admin` spelled another way is refused** (pre-existing, found by
    the same review). The Functions router matches the admin door against
    the RAW path and the asset server decodes it. So `/%61dmin/data.json`
    and `//admin/data.json` were served the admin's queue data past the
    door. A second review found dot segments behind encoded slashes
    (`/x%2F..%2Fadmin/data.json`) did the same. So `_middleware.disguisedPath()`
    refuses any path that encodes a slash, backslash or dot (no real address
    here does), and any other spelling that resolves to `/admin`.
    `check_admin_has_one_spelling`.
- **"Show N more" on a company page shows the rest** (2026-10-09). From the
  turn-6 rebuild it toggled a set nothing read for the first two groups,
  so it redrew the same three roles. The set is cleared when `co()` opens
  a different company, or one company's expanded group would follow the
  reader everywhere.
- **The /admin handlers read the person from the verified token**, which the
  door passes on as `context.data.access`; whoami verifies its own.
- **Pending alert signups carry one absolute `expires`** for both keys, and a
  caller is its IPv6 /64.
- **SLED_ROLE takes the plural and the abbreviation**: "Governments", "Govt",
  "State, Local". The restored pattern missed all three (GovWell's "Small &
  Medium Governments").
- **A filtered board's sentence says what the FILTER did**, never that the
  roles are outside this board's scope, because while sled_only is misapplied
  that is false for OpenGov's account executives. The counts are postings.
- **Every front-page number a click turns into the jobs tab counts that tab's
  default** (`boardDefault()`: quota-carrying, not placed outside the US);
  the banner and the card disagreed with the page they open.
  `check_the_front_page_counts_what_its_links_open` runs the real
  buildSlides() and home().
- **Emailed links wait for a click** (alerts confirm/stop, claim confirm), and
  digests carry both RFC 8058 headers, answered by alerts.js's form POST.
- **Three blind review rounds** (8, 8 and 4 agents, half of them skeptics):
  11, 21 and 7 confirmed findings, all fixed; most were tests that could not
  fail.
  **Run a check's clean baseline before mutating it**: one "caught" mutation
  in round one was the baseline failing, not the guard.
- The board's offtopic counts were computed under the broken pattern and
  correct themselves at the first crawl after the fix ships.

## Conventions

- Company `id` = kebab-case name (parenthetical suffixes dropped).
- `ats.type` ∈ ashby | greenhouse | lever | workable | recruitee | breezy |
  smartrecruiters | bamboohr | workday | rippling | jazzhr | icims | paylocity
  | oracle | jibe | adp | gusto | gem | html | unknown.
  Prefer structured API types; `html` is a last resort; `unknown` means
  "needs discovery" and is skipped by refresh. `coverage.py::STRUCTURED` is the
  list that decides what counts as a real API — add a new type in both places.
- **Never point a company at its parent's job board.** Several here were
  acquired (Rave → Motorola Solutions, RoadBotics → Michelin) and their
  careers pages redirect to the parent's Workday. Wiring that up would report
  a parent-company AE req as the subsidiary's, which is a false "Yes". Leave
  them `unknown` unless the board can be scoped to the product line. The same
  rule catches group careers pages (Nedap) and multi-tenant boards.
- **A sector is never also a category (owner, 2026-08-25).** The dataset filed
  Health & Human Services twice: as a category inside General Gov holding 38
  companies, and as its own sector holding 70. Owner ruled HHS gets its own
  tab and General Gov "should be pretty general stuff". The 38 moved, the
  duplicate category is gone, and HHS is 108. A word that names a sector must
  not also name a category under a different sector - it splits one industry
  across two tabs, and a reader who picks the wrong tab sees a short list and
  believes it. `selftest.check_search_routes_are_live` now refuses any search
  phrase pointing at a sector/category pair the schema does not hold, in
  semantic.py AND in the copy of the map inside index.html.
  **Courts & Justice had the same defect and was fixed the same day**: the
  category held 21 while the sector it duplicated held 14. Those 21 moved
  (16 to Courts & Case Management, 5 to Prosecution & Defense, following
  LegalEdge as the precedent for prosecutor and defender tooling), the
  category is gone, and Tyler's secondary `also` placement was repointed
  rather than dropped - Tyler does sell court systems, so the placement was
  right and only its address was stale. **Check `also` after any category
  deletion**; validate() reads it and nothing else would have caught it.
  General Gov still holds Libraries (53), Cemetery Management (23) and
  Animal Services (2). Those are specific rather than general, but none of
  them is ALSO a sector, so they are not this defect - leave them unless the
  owner says otherwise.
- Descriptions: one line, what they sell + to whom, no marketing fluff.
- Python: stdlib + requests + openpyxl only. Match existing style (typed,
  small functions, no classes where a function does). Comments explain WHY.

## The supplier move to SLED HQ: who each one is, and where it sells (2026-10-03..05)

The owner wants the 7,918 suppliers on SLED HQ, sorted carefully (2026-10-01).
Two steps are done. Neither rewrites `suppliers.json`: each writes its own file.

- **Step 1, `scripts/supplier_identity.py` → `data/supplier_identity.json`.**
  This records a verdict per supplier on who it is: the website is theirs,
  found, unconfirmed, parked, unreadable, not found, a duplicate, a related
  record, not a company, or a menu item off an association's site. A 214-record
  audit set each rule, and a fresh 233-record audit measured the result.
  "Not found" and "unreadable" are limits of the crawler, not facts about the
  company.
- **Step 2, `scripts/supplier_pages.py` + `scripts/supplier_categories.py` →
  `data/supplier_categories.json`.** Owner: "throw them in the categories we
  have for govtech", so suppliers sit in schema.json's sectors and categories.
  - The conference a supplier exhibited at gives the sector (`CROSSWALK`).
  - Its own site, read into the gitignored `data/supplier_pages/`, adds ONE
    category when the site names it in two distinct terms, quoting the
    sentence.
  - When every show was a generalist one, the site picks the sector.
  - When nothing specific turns up, the supplier goes in that sector's
    Suppliers & Services.
- **Nothing publishes without a website** (owner, 2026-10-04). A website
  counts only when its own text sells into that sector. Step 1's "theirs"
  is held to the same test, because a same-named business passed it.
  - A blind, fresh 200-supplier holdout measured the published suppliers at
    93% right website, 96% right sector and 76% exact category.
  - 40 of 87 HELD suppliers had a real website, so the held pile is partly
    a review queue.
- The export is what SLED HQ reads. It has fixed sector keys, sector-qualified
  category slugs, and a status per supplier. It carries no addresses:
  67 supplier "names" are email addresses, and `clean_name` plus a scrub keep
  them out.

## The supplier backlog is smaller than it looks (checked 2026-08-25)

**Re-measured 2026-09-18: 7,919 records, 5,143 unruled, 1,578 card-ready
(description and website), 2,569 bare names off an exhibitor floor.** The
figures below are the 2026-08-25 measurement and are kept for the reasoning,
not the counts.

The audit line "promote 670 candidates + 4,745 suppliers, none on the board"
reads as 4,745 companies waiting to be let in. Checked, and it is not that.

**2,776 of the 4,745 are already decided** - stamped `govtech: false`. They are
not a backlog, they are answers. The undecided set is **1,969**, all of which
carry a description.

Those 1,969 were then filtered three ways to find any govtech PRODUCT vendor
misfiled as a supplier, which is the thing worth finding: a missed vendor is a
warm door hidden, the same asymmetric error as a false "no jobs here".

  1,969 -> 156 whose description mentions software or a platform and nothing
           physical
    156 -> 10 that also name a government buyer and carry no nonprofit,
           consumer or campus signal

Those 10 read as correctly-filed suppliers: DLT is a reseller, Kurita sells
water-treatment hardware, Allied Universal is security guards, and the rest
are consultancies or course providers. Cross-checked a different way - the 144
undecided suppliers that came off a CORE govtech floor (GFOA, NASCIO, IACP,
NACo) are accounting firms, credit-rating agencies, financial advisors and
trade associations. Not one product company among them.

**So: nobody needs to spend days triaging suppliers.** The classification was
right; those records simply never got an explicit `false` stamp. They were NOT
bulk-stamped here, because a filter saying "probably a supplier" is evidence,
not proof, and 1,969 assertions nobody checked one by one is exactly the kind
of bulk claim this project does not make.

One name is worth a look if the scope question ever reopens: **Nava PBC**, a
digital government services firm, currently filed as a supplier because it
sells services rather than a product. That is consistent with the existing
rule, not a mistake - just the closest call in the pile.

## A bad probe run poisons the record for weeks (found 2026-08-26)

`discovery_log.json` remembers why a company has no job board, and the blocked
queue re-probes on a 7-day cycle. That is fine when the note is true. When a
whole RUN is degraded, it writes dozens of false notes at once and they all
persist together.

55 companies carried "gave up after 75s". Retried by hand with a 90s budget,
**42 of 53 answered - median 0.8 seconds, 41 of them under five.** 3AM
Innovations, recorded as a site that would not answer in 75 seconds, returns
525KB in 0.8s. These are not slow sites.

The tell is the probe DATE. 40 of the 55 came from one run on 2026-08-20 and
14 from 2026-08-23; only one is from any other day. Two degraded runs, roughly
a 75% false-negative rate between them, and the record then reads as 55
separate companies with unreachable websites.

**Group them by `on`, then RETRY A SAMPLE.** Clustering alone proves nothing -
it only says they were swept together, which is what a sweep does. The 403s in
this log cluster on exactly the same two dates as the timeouts, 37 and 22 of
them, and only 2 of 71 answer on a retry: those sites genuinely refuse
identified crawlers, and the shared date is just the sweep's date.

The retry is what separates the two. A timeout that answers in 0.8 seconds is
the run's fault; a 403 that is still a 403 a week later is the site's policy
and belongs on the manual worklist. Clustering tells you where to look;
retrying tells you what you found.

The same shape appeared the same day in build_board: 47 boards "network error"
in one build, and Civica, Career TEAM and BibliU all read perfectly minutes
later.

## Two scheduled sweeps that had stopped, and the bound each needed (found 2026-09-26)

**news.yml was cancelled at its 90-minute timeout on 55 of 58 runs between
2026-09-13 and 09-26.** The re-read step was bounded by `--limit 600`, which
is the wrong unit: 600 newsrooms took 76, 85 and 48 minutes on the last three
runs that finished, and the job times out on the clock. Once one run timed
out, every later run was colder than the last, because `actions/cache` saves
the ETag cache only when a job completes - so each run restored the cache the
09-12 run saved, sent two-week-old validators, and paid full fetches plus
article hops for every index that had moved. Four runs a day, two weeks, no
extract, no commit. `fetch_profiles.py --budget-seconds` now stops handing out
newsrooms when the budget is spent (pool.map submitted every row up front and
no deadline could reach it); what was read is saved, what was not sorts first
next run because the worklist rotates on `fetched_on`. news.yml passes 3600s
of its 5400s. `check_the_news_sweep_stops_before_the_job_does` drives the
loop with a stubbed reader and holds the number under the timeout.

**discovery.yml lost the 2026-09-20 sweep** to `git pull --rebase: You have
unstaged changes`. discover_ats.py writes three files and the workflow staged
two; the third, `ats_suspects.json`, is written only when a probe finds a
readable board whose slug names another company, which the 09-13 run did not
and the 09-20 run did. 195 lines of probe results died in the runner.
`check_discovery_stages_every_file_it_writes` reads the script's write
targets off the code and holds the workflow's `git add` against them.

Both are the same lesson as the render budget and the journal's BLAST: a
bound has to be in the unit the failure is measured in, and a workflow's
commit step has to be derived from what the script writes, not typed in.

**The budget did not hold either: one run finished between 2026-09-20 and
10-05.** The September fix was right but incomplete. The budget stopped
HANDING OUT companies, and a company already handed out ran to its end.
- **One company could be hundreds of fetches.** `index_entry`'s `slim()`
  dropped `from_index`, so every article a sweep ever read was committed as a
  newsroom. A cold CI run works from that index, since it holds no bodies.
  It re-read them all, then hopped twelve articles from each.
  - The index held 33,743 "newsroom" pages across 1,321 companies, and 1,215
    of them were Thomson Reuters'.
  - On the same first 16 sites, the old code was still on Thomson Reuters 6½
    minutes into a 2-minute budget. The fix read all 16 in 25 seconds.
- **The log said nothing**, because stdout was buffered.

Four rules now hold:
- The index keeps the flag.
- `revisit_news` re-reads at most `MAX_PAGES` newsrooms. Legacy unflagged
  pages are ranked newsroom-shaped first, never excluded, because
  `/newsroom/press-releases` is shaped like an article.
- `grab()` refuses to START a fetch once the run's budget is spent
  (`_RUN_DEADLINE`) or the company has used its `VISIT_CAP`. A company the
  deadline cut is not saved, so it goes first next run.
- news.yml sets `PYTHONUNBUFFERED`.

`check_a_slow_site_cannot_outlast_the_sweep` drives the real `revisit_news`
with a slow fake fetch.

## build_board.py is the crawler, not a formatter (noted 2026-08-25)

`python3 scripts/build_board.py` re-fetches every job board it has a ref for.
It is the refresh job wearing a build job's name, and it takes 13-20 minutes.

That matters because a change touching NO postings - moving a sector, filling
in conference dates, adding a field to the conference rows - used to cost a
full crawl of a few hundred third-party boards before the site showed it. Four
such rebuilds ran on 2026-08-25 for metadata-only edits, which is a lot of
traffic aimed at other people's servers to redraw a tab. Those edits are
`quick_rebuild.py`'s job now (below).

**Waiting for it: never `pgrep -f build_board.py`.** A shell running
`until ! pgrep -f build_board.py; do sleep; done` has that string in its OWN
command line, so pgrep matches the waiter and it waits for itself forever. On
2026-08-25 six of those stacked up and reported "still building" for two hours
while nothing was building. Wait on the PID instead - `while kill -0 <pid>;
do sleep 15; done` - or bracket the pattern, `pgrep -f "[b]uild_board.py"`.

`--limit` and `--company` skip work but refuse to write the full board
(correctly - a partial run overwriting the full board destroyed the dataset
once).

**The offline mode is `scripts/quick_rebuild.py` (2026-09-28).** This section
said it was deliberately not built because the postings flow through the fetch
loop that builds `orgs`. That loop no longer builds them: every organization
is `build_board.org_record()`, shared-board attribution is `board_owners()`,
and the manual merge, first_seen, the narrowing filters (`out_of_scope()`),
totals, conference rows and the data/detail split are functions main() calls.
quick_rebuild calls the same ones, so a redraw cannot describe a company
differently from a crawl. It reads no board: every socket and every fetcher is
refused while it runs.

- **It carries what only a crawl knows**: the postings, the six
  `CRAWL_FIELDS`, `boards_read`/`unreadable`/`rendered`, and `generated` - the
  crawl date the site ages postings by, which a redraw never moves. Its board
  says it was redrawn: `redrawn: {at, inputs}`, the second a sha256 of the
  inputs less `hiring` and `ats`.
- **What needs a crawl waits for one, by name**, under "waits for the nightly
  crawl": a changed ats type or board address, sled_only turned off on a
  company that published its own rows, a shared board whose attribution moved
  between companies still on file. Each is drawn exactly as the last crawl
  left it - rows, crawl fields and board fields - and stays named on every
  redraw until a crawl reads it.
- **A company new since the crawl with a board on file is LEFT OFF the
  board** - including one that points at a board another company already
  holds. No card, no rows, no place in any count or in shared-board
  attribution, its captures held back, and the report names it "waiting for
  the nightly crawl" until a crawl reads it: what the board did before
  redraws existed. Drawn, it would carry zero roles and `enumerable: None`,
  which index.html and build_site.py read as a board that answered - "they
  have nothing open that we can see" about a board nobody opened. A new
  company with NO board on file is drawn; it has nothing to read.
- **A shared board whose holder left the file passes on as the crawl passes
  it** (`quick_rebuild.takeover()`): the company now holding it alone takes
  the holder's crawled rows, re-keyed under its own id by `opening_id()` and
  `posting_id()`, dated `generated`, through its own filters; one still
  following names the new holder. Where the holder's read cannot pass exactly
  the new holder waits by name instead. Its sled_only dropped roles the new
  holder would keep, or its rows took its website as their url: the rows pass
  with the holder's sled_only or board_url carried, so the wait is named on
  every redraw. Its scope rulings dropped roles, its roles came from its own
  stored roles, or it published nothing while the new holder has stored roles
  or a scan lead of its own: named on the redraw that makes the change only
  (nothing on the board can carry it), the first with the rows passed, the
  others drawn as the follower they were. No `shares_board_with` on a redrawn
  board names a company that is not on it.
- Two edits it cannot see land with the next crawl unannounced: a scope
  ruling that brings a dropped role back in (dropped rows are not stored), and
  a same-type ref change on a workday/gusto/adp/gem board (board.json holds no
  ref for those).
- `python3 scripts/quick_rebuild.py` is a dry run listing what would change,
  grouped by field; `--write` redraws; `--check` exits 1 unless board.json is
  drawn from the inputs on disk.

`selftest::check_a_redraw_is_the_crawl_without_the_fetch` runs main() itself
OFFLINE over the committed inputs - fetchers stubbed from the committed board,
sockets refused - and requires the redraw to match it byte for byte, then again
after nine real edits, two of them a shared board's holder taken off the map.
It is deliberately NOT compared with the committed board.json: that goes stale
the first time news.yml commits after a crawl, and a check against it would
fail every workflow's self-test from then until the next nightly, the
nightly's own included. `check_an_ats_change_waits_for_the_crawl` holds every
wait above, the takeover and the companies left off, across two redraws.

## Common tasks

- **Refresh everything:** `python3 scripts/refresh.py` (add `--dry-run` to
  preview, `--company <id>` for one). Summarize the diff for the owner
  afterward — new "Yes" companies are the headline, in prospecting terms.
- **Add a company:** research name/HQ/founding year/what they do (verify on
  the company site or funding press, not aggregators), pick sector+category
  from schema.json, find their ATS (try the API URL patterns in
  `scripts/ats.py` docstrings), append to companies.json, run selftest, run
  `refresh.py --company <id>`, then `export_xlsx.py`.
- **Discover an ATS (JS-walled board):** `python3 scripts/discover_js.py noats`
  renders each Unknown in headless Chromium and prints the ATS endpoint its
  board actually calls. Needs `pip install playwright` + `python -m playwright
  install chromium`, which is **why it's a separate script**: the browser is a
  one-off discovery tool, its conclusions get written into `companies.json` as
  normal `ats` entries, and `refresh.py`/CI stay stdlib-only. Always verify a
  slug with a real fetch before writing it — Lever slugs are lowercase, and an
  off-site careers link occasionally lands on another company's board.
- **Discover an ATS (plain):** careers page source usually reveals it — look for
  greenhouse.io / lever.co / ashbyhq.com / myworkdayjobs.com / workable.com
  URLs in the HTML, or try `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs`
  style probes with obvious slugs. **Read the child frames** — the board is
  often in an iframe and the frame URL carries the slug.
- **The owner says "run":** that means refresh + summarize changes + regenerate
  the xlsx. Same contract as the original Cowork workflow.
- **Verify a UI change in a browser with measurements.** Build to `/tmp/<name>`,
  serve above port 8700, and confirm `innerWidth` is not 0 and is what you
  expect before trusting any pixel number — a collapsed grid reports numbers
  that look real. One session reported a 923px hero and four screens of
  scrolling; measured properly it was 993px to the first job. Kill every server
  you start.
