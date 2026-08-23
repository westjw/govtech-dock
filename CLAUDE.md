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

## The admin queues are meant to become a game (owner, 2026-08-23)

Not to build now. But every queue built from here should be built so this is
possible later, because retrofitting it means re-recording history.

The owner's reasoning, in his words: gamifying admin work would "help train
the AI, speed up backend manual work, and enhance the product if someone
else were to do it." Three payoffs, and the third is the one with teeth: a
queue a stranger would willingly work is a different product from a queue
only the owner tolerates.

What that implies for anything built today, at no cost today:

- **Every ruling gets an author, a timestamp and a reason.** Already true of
  `admin_dismissed.json` and `scope_decisions.json`. Keep it true everywhere.
  A ruling without an author cannot be scored, trusted, or learned from, and
  you cannot add the author afterwards.
- **A ruling is training data.** Each one is a labeled example: this title is
  that family, this vendor belongs in that sector, this federal role is in
  scope. Store the INPUT the person saw alongside their answer, not just the
  answer, or the label is useless for teaching the classifier later.
- **Keep the confident cases out of the queue.** The fun in a queue is that
  every item needed a person. Padding it with items a rule could have settled
  is what makes admin work feel like a chore.
- **Never let a score reward volume over correctness.** The asymmetric error
  rule holds: a wrong "not a govtech company" is invisible and permanent. Any
  future points system must make care pay better than speed, or it will
  manufacture exactly the errors this repo is built to avoid.

## Coverage: read `scripts/coverage.py`, not the raw fraction

`python scripts/coverage.py [--by-sector]`

"839 of 1,722 monitored" was wrong in both directions and it drove bad
decisions for a while. It counted a careers page nothing can enumerate the
same as a Greenhouse API, and it counted companies that have **no job board at
all** as a gap to be closed. The real split:

```
structured   213   a real API. Titles, locations, links. THIS is the number to move.
page only    629   a page a person can read and a fetcher mostly cannot.
blocked       98   a bot wall or transport error. We learned nothing. NOT a zero.
absent       652   checked, no public board exists. A finished state, not a gap.
unchecked    130
```

A 15-agent field audit (n=90 random re-probe, plus 24 investigated by hand)
found **55-63% of the "no board found" pile is genuinely boardless** - small
SLED vendors hiring on LinkedIn or by email. Rebuilding discovery on that
audit's findings and A/B-ing it on a fresh 70-company sample recovered
**1-2 structured boards per 70**, against a projection of ~10. Measure, then
report the measurement; the projections in that audit were drawn from a sample
selected for being interesting.

So: **`page only` is a worklist for the capture bookmarklet, not coverage.**
Converting those to `structured` is mostly impossible - there is no ATS behind
them to find. Do not add the two together in a status report.

## Build order (owner, 2026-08-23)

This repo comes first, and inside it: front end, back end, data, admin. The
job-hunter repo waits. The owner's framing is that the board is the product
being launched, so anything that makes the board better for a stranger
outranks anything that makes his own search easier.

Practical reading of that when choosing what to do next:

- A gap a visitor would notice beats a gap only the owner would notice.
- Data completeness beats new features: 4,733 catalogued suppliers and 670
  researched candidates are worth more than another filter.
- Admin work is product work here, because the queues are what keep the data
  honest, and one day they are meant to be playable.

## Portfolio Dashboard Sync

This project is tracked in a personal portfolio dashboard. Maintain
`portfolio-status.json` in the project root throughout every session.

**Project ID:** govtech_dock
**Repo:** westjw/govtech-dock (private)

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
shippable is not the project being 90% finished.

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
