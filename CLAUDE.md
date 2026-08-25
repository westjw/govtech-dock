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

- **The domain is `solesourcejobs.com`** and changes when the owner buys the
  new one. Editing that string and pointing the Pages custom domain at the new
  name is the whole move: alert links, digest footers, the confirmation email
  and the submission form all read it from brand.json.
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

`python3 scripts/admin.py`, then <http://127.0.0.1:8787>. Fourteen queues:
boards we found, founding year, wrong bucket, vendor scope, scope review,
submissions, duplicates, missing websites, no board found, blocked boards,
wrong placement, unclassified roles, acquisitions, website review.

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
  and `/data/companies.json` all answered 200 to anything that asked. Six
  routes are served — `/`, `/admin.html`, `/capture`, `/capture.js`,
  `/assets/logos/*`, `/assets/mascot/*` — and everything else is 404 by
  construction rather than by check.
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
- **THE RULE ONLY EVER COVERED `admin.py`.** Seven pipeline scripts write
  `companies.json` directly — `add_company`, `discover_ats`,
  `conference_intake`, `find_websites`, `refresh`, `merge_companies`,
  `promote_candidates`. That is how a merged-away record came back an hour
  after `merge_families` folded it, with one journal entry for the merge and
  none for the resurrection. `selftest::check_merged_names_stay_merged` is the
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
`scripts/apply_web_rulings.py`) is the judgment half — vendor scope and wrong
bucket — workable from a phone. The division of labour is the design: the
Worker only *appends an opinion* to a ruling file; the daily run applies it to
`companies.json` in Python, where `validate()` lives. A bug in the web half can
mis-record an opinion and cannot corrupt the map. Auth is Cloudflare Access,
and the endpoint refuses to write when the Access headers are absent, so the
failure mode of misconfiguration is "nothing works", never "everyone can write".

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

Four agents on one spine: `bucket` and `read` are built; `card` (research a new
company) and `board` (find the ATS behind a page) are next.

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

## Capture: the bookmarklet and the extension

`scripts/capture.js`, installed from <http://127.0.0.1:8787/capture>, plus a
Chrome extension in `extension/`.

**887 companies have a careers page on file that produces nothing** (counted
2026-08-24; `coverage.py` prints the live figure) — a person
looking at the page sees the jobs anyway. That is the worklist both of these
serve, and it is the single biggest hole on the board.

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
Every one is optional — with none set, the endpoint reports "not configured"
and the CI step prints that and exits 0. A refresh must never fail because
nobody set up email.

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
all** as a gap to be closed. The honest split, re-derived 2026-08-24 across
2,103 companies:

```
structured   269  12.8%  a real API. Titles, locations, links. THIS is the number to move.
page only    887  42.2%  a page a person can read and a fetcher mostly cannot.
blocked      238  11.3%  a bot wall or transport error. We learned nothing. NOT a zero.
absent       568  27.0%  checked, no public board exists. A finished state, not a gap.
unchecked    141   6.7%  never probed, or probed before the current rules existed.
```

285 companies currently show at least one open posting.

**Run the script; do not quote this block.** These moved by one while this
section was being written, because a discovery pass was running in another
session. A number copied out of a document is how the old "839 of 1,722" got
believed for months.

The two ratios the script prints mean different things and neither is
"coverage":

- **1,156/2,103 = 55%** — we have *some* board on file, against every company.
- **1,156/1,535 = 75%** — the same numerator against companies that have a
  board to find (total minus `absent`). This is the denominator that can be
  worked. It is **not** 75% readable: 887 of that 1,156 is the `page only`
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
- Data completeness beats new features: `data/suppliers.json` holds 4,745
  catalogued suppliers and `data/conference_intake/govtech_candidates.json`
  holds 670 researched candidates, none of them on the board yet. That is
  worth more than another filter. (Counted 2026-08-24 — re-derive, don't
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
shippable is not the project being 90% finished — the paid half currently has
no pricing, no billing, no accounts and no employer side, and the only paid
intent on record is "alerts, paid eventually" in the owner's spec. A number
that quietly means "the free board" is the kind of stale fact this file exists
to prevent.

## Conventions

- Company `id` = kebab-case name (parenthetical suffixes dropped).
- `ats.type` ∈ ashby | greenhouse | lever | workable | recruitee | breezy |
  smartrecruiters | bamboohr | workday | rippling | jazzhr | icims | paylocity
  | oracle | html | unknown.
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
  **Still outstanding: General Gov / Courts & Justice holds 21 while the
  Courts & Justice sector holds 14.** Same defect, same ruling would fix it,
  not yet made. General Gov also still holds Libraries (53), Cemetery
  Management (23) and Animal Services (2), which are specific, not general.
- Descriptions: one line, what they sell + to whom, no marketing fluff.
- Python: stdlib + requests + openpyxl only. Match existing style (typed,
  small functions, no classes where a function does). Comments explain WHY.

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
