# GovTech Dock — project guide for Claude Code

## What this is

A tracker of state & local govtech companies with live "who's hiring AEs"
status. The owner is a SLED sales professional using it to map employers and
prospect timing. It began as a Cowork spreadsheet; this repo is the app
version. Three parts:

1. **Data** — `data/companies.json` (the database), `data/schema.json`
   (sectors/categories), `data/hiring_history/*.json` (append-only snapshots),
   `data/meta.json` + `data/latest_diff.json` (run state).
2. **Engine** — `scripts/refresh.py` (deterministic ATS checks; no AI),
   `scripts/ats.py` (per-ATS fetchers), `scripts/classify.py` (title rules),
   `scripts/export_xlsx.py` (spreadsheet), `scripts/selftest.py` (offline QA).
3. **Site** — `index.html`, a single-file static app reading `data/` at runtime.

## House rules

- **Never hand-edit `data/hiring_history/*.json`** — snapshots are the audit
  trail. Hiring state changes only through `refresh.py`.
- **`data/schema.json` is the source of truth** for sector/category names and
  xlsx tab colors. To add a sector/category: edit schema.json, then move/add
  companies, then run `python scripts/selftest.py` (it enforces consistency).
- Keep `refresh.py` deterministic. AI-judgment work (finding a new company's
  ATS, deciding whether an odd title is an AE req) happens interactively in
  Claude Code sessions, and its *conclusions* get written into
  `data/companies.json` (an `ats` entry, a classifier rule) — not into the run.
- New classifier edge cases go into `classify.py` **with a matching case in
  `selftest.py`'s CLASSIFIER_CASES** (title rules) or **PAGESCAN_CASES**
  (`html` page-text rules).
- **A page scan never proves absence.** `scan_pagetext` may return `unreadable`
  → status `Unknown`. Don't "simplify" that back into `None found`: a false
  `None found` silently deletes a warm door, which is the one failure this
  tool cannot afford. Assert a status only on concrete evidence in the text.
- After any data or script change: `python scripts/selftest.py` must pass.
- The site is deliberately dependency-free (no build step, no framework). Keep
  it a single `index.html` unless the owner asks to graduate it.
- Statuses are exactly: `Yes`, `Sales (non-AE)`, `None found`, `Unknown` —
  renderer, exporter, and selftest all assume this set.

## The admin backend

`python scripts/admin.py` then <http://127.0.0.1:8787>. Loopback only, on
purpose: it writes `companies.json` with no auth in front of it.

It is where the residue of every automated pass goes - the parts that need
judgment rather than a better regex. Seven queues: duplicates, missing
websites, missing boards, wrong placement, unclassified roles, acquisitions,
website review. Rules that hold there:

- **Every write is validated against the same invariants `selftest.py`
  enforces**, on the whole file, then lands atomically. A bad edit is refused,
  never half-applied.
- **A merge never loses research.** The survivor keeps what it has and inherits
  what it lacks; a discovered ATS always beats an `unknown` one; the dropped
  name is kept in `also_known_as`.
- **Evidence before the write.** Pasting a URL shows the page title, whether it
  is parked, whether it identifies the company, which ATS is behind it and
  whether the slug matches - then a person decides. A slug mismatch says so in
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
rail sets sector *and* category together - setting the sector alone would strand
the old category and `validate()` would refuse the write, correctly.

## Capture, and why it is a bookmarklet

`scripts/capture.js`, installed from <http://127.0.0.1:8787/capture>.

537 careers pages on file have a board recorded and produce nothing. They are
not JS shells hiding a list - rendering a sample of 25 in headless Chromium
recovered **zero**. They are third-party widgets in iframes, session-gated
boards, and pages that only draw a list after an interaction. No fetcher we
write will read them. A person looking at the page sees the jobs anyway.

So capture reads what is already on screen and hands it over. Three things
about it are load-bearing:

- **The handoff is the clipboard, not a request.** Chrome blocks a page on
  https from reaching `http://127.0.0.1` - both `fetch` and a `<script>` tag,
  even with CORS and `Access-Control-Allow-Private-Network` set. Verified, not
  assumed. Copy-and-paste is the only channel that works on every site.
- **The bookmarklet is self-contained** for the same reason, so editing
  `capture.js` means dragging the button again.
- **It runs once, on click, over the current document.** It does not scroll,
  paginate, follow links, log in, or run on a timer. That is the line between
  reading a page you opened and harvesting a site, and it is why this is
  usable on LinkedIn when server-side scraping is not.

Two rules the harvester learned the hard way, both worth keeping:

- A job link is the job **segment plus something after it**. Matching `/careers`
  alone returned CHALLENGES, SOLUTIONS and Cookie Preferences - the same nav
  chrome that fools page scans.
- **Position first, pattern second.** Take the first non-chip line as the title,
  then look for a location among the lines *after* it. Testing the location
  pattern first stole the title whenever one looked like a place: "Database
  Administrator, Infrastructure - UK" matched, and the row came back with
  Manchester as the job.

Captured postings live in `data/manual.json` and an automated run never deletes
them - absence from a refresh means the fetcher still cannot see that company.

## Submissions

`data/submissions.json`, reviewed in the admin Submissions queue. Outside
parties can send a company or a job. **A submission is a claim, not a fact**:
nothing reaches `companies.json` or the board without a person approving it,
for the same reason the fact bank refuses unverified records. Approving a
company runs the same identity and sector guess as intake and shows the
evidence, and reports low confidence as low rather than filing on one
incidental keyword. Approving a job writes it through the capture path.

## Conventions

- Company `id` = kebab-case name (parenthetical suffixes dropped).
- `ats.type` ∈ ashby | greenhouse | lever | workable | recruitee | breezy |
  smartrecruiters | bamboohr | workday | rippling | jazzhr | icims | html |
  unknown.
  Prefer structured API types; `html` is a last resort; `unknown` means
  "needs discovery" and is skipped by refresh.
- **Never point a company at its parent's job board.** Several here were
  acquired (Rave → Motorola Solutions, RoadBotics → Michelin) and their
  careers pages redirect to the parent's Workday. Wiring that up would report
  a parent-company AE req as the subsidiary's, which is a false "Yes". Leave
  them `unknown` unless the board can be scoped to the product line.
- Descriptions: one line, what they sell + to whom, no marketing fluff.
- Python: stdlib + requests + openpyxl only. Match existing style (typed,
  small functions, no classes where a function does).

## Common tasks

- **Refresh everything:** `python scripts/refresh.py` (add `--dry-run` to
  preview, `--company <id>` for one). Summarize the diff for the owner
  afterward — new "Yes" companies are the headline, in prospecting terms.
- **Add a company:** research name/HQ/founding year/what they do (verify on
  the company site or funding press, not aggregators), pick sector+category
  from schema.json, find their ATS (try the API URL patterns in
  `scripts/ats.py` docstrings), append to companies.json, run selftest, run
  `refresh.py --company <id>`, then `export_xlsx.py`.
- **Discover an ATS (JS-walled board):** `python scripts/discover_js.py noats`
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
  style probes with obvious slugs.
- **The owner says "run":** that means refresh + summarize changes + regenerate
  the xlsx. Same contract as the original Cowork workflow.

## Roadmap ideas the owner has floated or would plausibly want

- Close out the ~11 JS-walled Unknowns (Playwright fallback fetcher, or manual)
- Columns for funding/stage and careers-page links
- New sectors: Courts & Justice, Utilities & Energy
- Alerting: open a GitHub issue (or email) when a watched company flips to Yes
- Per-company notes field for outreach tracking (who he contacted, when)
