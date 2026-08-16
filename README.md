# GovTech Dock

A running map of state & local government technology companies — who they are,
what they sell, and **who's hiring Account Executives right now**. Started as a
spreadsheet built in Claude Cowork; this repo is the "formal app" version:
a JSON database, a static website, and a deterministic refresh engine.

**137 companies · 6 sectors** (Public Safety, Public Works, General Gov,
Parks & Rec, K-12 Schools, Transit & Parking), each broken into categories
(Fire/Police/EMS, Waste/Streets/Water, and so on).

## Quickstart

```bash
pip install -r requirements.txt

python scripts/selftest.py      # offline sanity check (data + classifier)
python -m http.server 8000      # then open http://localhost:8000  <- the site
python scripts/refresh.py       # re-check every company's job board (~2 min)
python scripts/export_xlsx.py   # regenerate the classic 6-tab spreadsheet
```

The site is 100% static — it reads `data/*.json` client-side. Serve the repo
root (any static server works); opening `index.html` via `file://` won't load
data because of browser fetch rules.

## How the refresh works

`scripts/refresh.py` is **deterministic — no AI calls**. For each company,
`data/companies.json` records which applicant-tracking system (ATS) it uses:

| ats.type | How it's read |
|---|---|
| `ashby`, `greenhouse`, `lever`, `workable`, `recruitee`, `breezy`, `smartrecruiters`, `workday` | official public JSON APIs |
| `rippling`, `jazzhr` | server-rendered boards, parsed |
| `html` | careers page fetched and text-scanned (weakest signal — a `Yes` gets a `[page scan - verify]` note) |
| `unknown` | skipped; listed at the end of each run as "needs ATS discovery" |

Titles are classified by `scripts/classify.py` into **Yes** (quota-carrying
field sales: AE, Sales Executive, Territory Manager, Regional Sales Manager…),
**Sales (non-AE)** (SDR/BDR/CS/leadership only), **None found**, or **Unknown**
(board unreadable). Every run writes:

- `data/hiring_history/YYYY-MM-DD.json` — dated snapshot (never edit these)
- `data/latest_diff.json` — what changed vs. the previous snapshot
- `data/companies.json` — updated `hiring` block per company
- `data/meta.json` — run metadata

The site's "Changes since last run" panel and the diff printed at the end of
each run are the real signal: *a company that just opened AE reqs is a warm
door.*

## Putting it on GitHub (site + weekly auto-refresh)

1. Create a new GitHub repo and push this folder to `main`.
2. **Settings → Pages** → Source: *Deploy from a branch* → `main` / `/ (root)`.
   Your site appears at `https://<you>.github.io/<repo>/`.
3. **Settings → Actions → General → Workflow permissions** → select
   *Read and write permissions* (the weekly job commits refreshed data).
4. Done. `.github/workflows/refresh.yml` runs every Monday morning (and on
   demand via the Actions tab → *weekly-refresh* → *Run workflow*), refreshes
   hiring data, regenerates the spreadsheet, and commits the diff.

## Working on this repo with Claude Code

Open the folder and run `claude`. `CLAUDE.md` gives it the house rules, and
three slash commands are included:

- `/refresh` — run the refresh, then summarize what changed in plain English
- `/add-company <name or URL>` — research a company, verify facts, add it to
  the right sector/category, find its ATS
- `/discover-ats` — work through the companies marked `unknown`/`html` and
  upgrade them to structured ATS entries

## Data model (`data/companies.json`)

```jsonc
{
  "id": "flock-safety",            // stable slug
  "name": "Flock Safety",
  "website": "https://www.flocksafety.com",
  "location": "Atlanta, GA",
  "year_founded": 2017,
  "sector": "Public Safety",        // must exist in data/schema.json
  "category": "Police",             // must be a category of that sector
  "description": "License plate readers, gunshot detection, ...",
  "ats": { "type": "ashby", "ref": "flock safety" },
  "hiring": { "status": "Yes", "note": "...", "roles": [...], "checked": "2026-08-16" }
}
```

`data/schema.json` is the single source of truth for sector/category names and
the spreadsheet tab colors. Add a sector or category there first; `selftest.py`
enforces consistency.

## Caveats

- Founding years for the youngest startups are best-effort from press coverage.
- `html`-type checks are text scans, not structured data — treat their "Yes"
  as a lead, not a fact, until upgraded via `/discover-ats`.
- A page scan can only prove *presence*. Finding no AE role on an `html` page
  means "we couldn't read this board", not "this board is empty", so it is
  reported as **Unknown**, never "None found". "None found" from a page scan
  requires the page to say so outright ("no open positions"). This is why
  Unknown is large (55) — those are boards to fix, not companies to skip.
- A handful of boards (BurnBot, Vantiq, NEOGOV, Paymentus…) are JS-walled with
  no public API found yet; they stay "Unknown" until someone finds their ATS.
- Snapshots are keyed by date, so a second run on the same day would overwrite
  the first. `refresh.py` refuses to do that unless you pass `--force`, in
  which case the earlier run becomes the diff baseline.
