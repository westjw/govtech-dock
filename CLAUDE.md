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
knows what this is and tells a stranger nothing.

**Home is a front page (owner, 2026-10-25: "r2 3 please", option 3 of round 2
of the home page canvas).** It replaced the rotating banner and its
`buildSlides()`. Everything on it is BUILT FROM THE BOARD by `home()`:
- **The headline** dates the read ("The board, read 23 October 2026") and
  says how many sales roles reached the board this run, the count spelled as
  a word up to 99 (`numWords`). A role is new when its EARLIEST posting on
  the board was first seen on the crawl: one that only gained a city has a
  new posting, not a new role. A quiet run drops the claim rather than
  printing a zero, and the headline says the board's size instead. One line
  under the search says the counts are the board's default (quota-carrying,
  not placed outside the US); never "in the United States".
- **The cards** are openings (`groupOpenings`, shared with the jobs tab), new
  this run first. The lead is the best-paid new role by a yearly US-dollar
  figure every posting agrees on (`groupYearly`); then one card a company.
  Pay is the card's rule (`groupPay`, shared with `jobCardHTML`). The link
  opens `?since=0`, which counts openings with ANY posting new tonight, so it
  reads "All N new this run" when the two agree and "All N with new postings
  this run" when they do not. A run with fewer than five new roles shows all
  of them and tops up with older ones, still one a company, and the section
  is headed "Fresh badges / First seen ..." only when every card is new
  (round 5, below).
- **Hiring hardest** is `quota_roles` on boards we read, in the Companies
  spotlight's own order, ties included. **On the floor** is what is on today
  (the reader's today, as the Conferences tab reads it), the biggest floor
  first, then what comes next. **Pay, where posted** is the highest yearly
  US-dollar ranges stated on every posting of a role, one company each, and
  says how many postings state pay. Logos are `aria-hidden` beside the name.
- **The departments** are counted in openings with `inSector()`, the
  predicate the board filters by. The footnote names the widest requisition
  and carries the one link to How a listing gets here.
- **Round 5 of the canvas (owner, 2026-10-27: dark is the default, then "love
  the new home page, push please").** The cards are BADGES: a Badge band
  ("HELLO, WE'RE HIRING", aria-hidden, the lanyard slot punched through to
  `--bg`), headed "Fresh badges / First seen <when>." only when every badge
  is new this run (then no badge repeats its age and the lead says "1 of N
  new"), else "Newest badges / The newest roles on the board." with each
  badge's age. The departments are six CARDS, then the rest as chips: never
  called halls, no description above them, the band on the header's tokens,
  "Hiring most" naming the three companies holding the most of the card's
  openings (ties by name), and the button reading the department's name.
  The **registration desk** is the last section: alerts.html's three days
  (`DESK_CADENCE`, one pressed, `deskCadence` in ACTS) set the alert link's
  `?cadence=`, which alerts.html starts a new sign-up on; "Save a search"
  opens the jobs tab, where saving is. The penguin stands on an arch of
  `--hdr-tile`. `check_the_registration_desk_carries_its_days` and the
  Hiring-most cases in `check_the_front_page_counts_by_opening`.
- **The Map tab is gone (owner, 2026-10-27: "kill the maps tab, all of it")**:
  the view, its nav entry, its action, its share card and description, its
  sitemap line and its checks. `D.cities` stays: the Near-a-city filter
  measures on it and credits OpenStreetMap where it does. An old `?tab=map`
  link opens the jobs board, as any unknown tab does (render()).
- **Every door is a real link** (`?where=remote`, `?pay=stated`, `?sec=`,
  `?since=0`, `?role=`, `?co=`, `?e=`, `?tab=`), taken over on a plain click
  and opened through `goTab` with the link's own address (`?tab=` read by
  `tabFromUrl`): one history entry, the top of the page, and Back comes
  home. A modified click does what the link would. An event's door replaces
  the tab's entry with `?e=` and opens the panel as if from the address.
- `check_the_front_page_counts_what_its_links_open` runs `home()` on the real
  board, on the same board a day later (a quiet run) and with a seller debut
  (jobcard_harness.mjs), and holds every number to what its link opens,
  computed independently. The harness also presses every door through
  home()'s own wiring, plainly and with modifiers, and types into the
  search. `check_the_front_page_counts_by_opening` holds the department
  index (its counts and the address each row opens), the widest requisition,
  the debut line and the hidden logos. Blind review (3 lenses, a skeptic
  each): 18 confirmed, all fixed but one, the white search glyph and the
  card shadow, which stay literals copied from .cfs-go and .cocard.

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

## One search bar, and companies as cards (owner, 2026-10-19)

The owner found the jobs filters "too messy", liked the Conferences tab, and
picked option A from a design canvas plus company cards ("build option A
with the company cards").
- **Jobs and Companies search is the Conferences bar** (`.cfsearch`, scoped
  `.jbar`): labelled fields in one rounded bar, then a quiet row (`.jrow`).
  - Jobs: What / Where / Role / Sells to / Pay.
  - Where is a popover holding Remote/Hybrid/Onsite and Near a city
    (`jWireWhere`, `paintWhere`).
  - Quota-carrying, First seen, More filters, Sort and "Copy link to this
    view" sit in the quiet row.
- **Every control kept its id and its action.** The address, the chips and
  the outside callers reach the same filters: home's sector buttons, the
  alerts preview and digest links.
- **The country's default is said once**, as "not placed outside the US" in
  the count line, never as a chip. Never "in the United States": the default
  keeps unplaced roles.
  - Clearing the country goes back to the default.
  - "Anywhere in the world" (`#j-us` empty) is written to the address as
    `us=`.
  - `check_the_jobs_bar_says_the_default_once` holds this.
- **Near a city is a filter like the rest:** `near=`/`mi=` in the address,
  a chip, and Clear all. Remote ignores a typed city, as the Where field says.
- **The Companies list is cards** (`coCard`, a real link). The spotlight
  shows the three hiring the most sellers among what the filters kept, on
  boards we read only (a card saying "board not readable" cannot head it).
  The first card is dark, and none of them is repeated below.
  - Browsing (no search, sector or sub-sector): every sector, six cards
    each, "Show all N in X" to open one. A company sits on every shelf it
    is filed under (owner, 2026-10-20, for the Housing shelf): a second
    listing's card names its primary sector, and the shelf says "N also
    listed here", so the groups add up to more than the count.
    `check_a_second_listing_sits_on_its_shelf`.
  - **"Show all N in X" opens X's own page** (owner, 2026-10-27: "it should
    take you to the public works page"): a real link to
    `?tab=companies&csec=X` in the Showing chosen, through `toCompanies`, at
    the top of the page. N counts the whole sector, the spotlight's
    companies included, so it is the page's count. A chosen sector draws
    `coSectorPage` above the list: its sub-industries (the dropdown's own
    list, each count the list its link opens) and its conferences
    ("the subindustries and conferences that fall into it"), from the
    `sector` build_board stamps on every conference row off
    `scripts/conference_sectors.py`, the one crosswalk supplier_categories
    also files suppliers by. `check_show_all_opens_the_department_page`.
  - Searching or filtering: one grid in rank order, 60 at a time.
  - "Showing" is two buttons, Hiring now and Everyone, each with
    aria-pressed. The tab opens on Everyone (owner, 2026-10-20); Hiring now
    is `chire=1` in the address, and an old `call=1` link means Everyone.
  - A card says "on N exhibitor lists", never "seen at": some lists come
    out months before their event.
- **The card's count corner keeps the list row's honesty order:** no board
  on file, then board not readable, then sellers, then open, then "nothing
  open" (only for a board we read).
  - `check_a_company_card_keeps_the_rows_honesty` runs `coCard` over every
    company through jobcard_harness.mjs.
- **The company page's hiring-phase cell is gone** (owner, 2026-10-19: "kill
  this", over "Regional push · 73 roles opened in 60 days · 22 regions"). The
  stat strip is three cells, in co() and on the static /c/ page; selftest
  fails if either draws one again. The board-level phase (o.phase, "selling:
  a go-to-market push") is a different measurement and stays.
- **Round corners are the conferences design's exception to --radius.** They
  stay inside the bar, its popover and the cards; toggles, selects and chips
  outside them stay square. The home front page carries the exception with
  its design (2026-10-25): its search pill, its three chips, its cards and
  its logo tiles.

## Flag for Job Hunter (owner, 2026-10-20)

"I want to be able to connect wyeth to the job board to flag jobs." Job
Hunter (the private repo, live at hunter.<domain> behind its own Access
application) is the other end.
- **A link, never a fetch.** `jhFlagLink(p)` opens
  `https://hunter.<host>/app?flag=<base64url JSON {i, c, t, u, l}>` in a new
  tab: the posting's id, company, title, address and place as this board
  shows them, nothing else. Job Hunter matches the role by address, then
  company and title, and files it on the signed-in person's own board.
- **Only an owner or a Job Hunter beta user sees it.** whoAmI puts `jh` on
  the root when /admin/api/whoami answers with `hunter_app`, which whoami.js
  returns only for those roles and builds from `_brand.js` DOMAIN - never
  from the address bar, which on solesourcejobs.com named a host Job Hunter
  does not answer. Links drawn before that answer are repointed. `.jhflag`
  is `display:none` otherwise. The account menu's Job Hunter door goes to
  the same address, and /admin/hunter/ links it.
- **Job Hunter confirms every flag**, so the link needs no referrer
  (`rel="noopener noreferrer"`): a company's careers link on the board could
  redirect into a flag carrying this board's referrer, so arriving is never
  consent (review, 2026-10-20).
- On a card it sits under the dates (`.jc-flag`), never in the bookmark's
  corner, so a title keeps its width on a 320px phone; the role page and
  the Saved tab put it beside the bookmark.
- `check_a_role_can_be_flagged_for_job_hunter` decodes what the page's own
  jhFlagHref sends (jobcard_harness) and holds the rest by source.

**Back and forth with Job Hunter (owner, 2026-10-25: "I want to go back and
forth", then "there should be like a popout", and of three, a Job Hunter
panel on a role).**
- **The tab row ends in "Job Hunter ↗"** for an owner or beta user, added by
  whoAmI after start() wired the tabs, so goTab never takes it over. Job
  Hunter links back from its sidebar, from "See it on SLED JOBS" on every role
  it took off this board (it keeps the posting each opening leads with,
  `signals.board_id`), and from the company's name (`?co=`).
- **A role page's "Job Hunter ▸"** (beside Flag and Save, one `.roleacts`
  group that takes its own line on a phone) opens a popout at the page's edge:
  the fit, the application, the resume and the flag as the person's own Job
  Hunter holds them, and "Open in Job Hunter". **An open role Job Hunter has
  not scored, with no failing gate and no score on its way, offers "Score it
  in Job Hunter"** (owner, 2026-10-29: "the job hunter for that role, like you
  click a button and it charges you"): the Score is pressed on Job Hunter's
  role page, where the credit is spent, and the panel says what it costs and
  what they hold, as Job Hunter's header says it (Job Hunter's board-role
  answer carries `credits` and `scoring`). No price for an account that
  never pays; "Being scored in Job Hunter now" while one is on its way. It FETCHES Job Hunter's
  `/api/board-role?id=<posting id>` with the person's own sign-in
  (`credentials: include`, `redirect: manual`, nothing sent but the id).
  Job Hunter answers that one route to this board's address and no other,
  so the button shows only on the board's own host (`:root.jhp`, set by
  whoAmI when Job Hunter is hunter.<this host>): on solesourcejobs.com or the
  pages.dev alias it would ask and never hear. The Flag and the tab are links
  and work anywhere. Each silence says which it is (`jhAnswer`): Job Hunter's
  own 401 "no_account" is a person signed in with no account, its other
  refusals ask for a sign-in, and a reply from Access itself (an expired
  sign-in's redirect) carries no CORS header, so the browser refuses it and
  it reads with a request that never answered (the Mac asleep): "did not
  answer", whose words say a sign-in may need renewing. A failing gate prints above the fit
  and a score the person rejected says so, as on Job Hunter's own board.
  Everything Job Hunter says is printed as text.
- **It is a dialog** (review, 2026-10-25): a backdrop that closes it, Tab kept
  inside, Escape, focus given back to its button. Opening it pushes a history
  entry, so Back closes the panel and not the role; the × or Escape pops that
  entry (`JH_PANEL_POP`, swallowed by the panel's popstate listener, which is
  registered before boot's). Several steps back from the history menu land
  somewhere other than the role (`JH_PANEL_HREF`), and that page redraws.
  A score with no ceiling prints the score alone. `render()` and `co()` close it first with
  `closeJhPanel(true)`, which pops nothing. A role page left by a link while
  the panel was open keeps that entry: Back from the next page shows the role
  twice. Known, small, and not worth a router.
- **`connect-src` on the app page is `'self' https://hunter.<domain>`**
  (csp.app_connect, from brand.json); every other kind of page stays on
  `'self'`, and the policy guard holds both. It names the whole host: the one
  route is Job Hunter's rule, not this one.
- `check_the_job_hunter_panel_says_what_job_hunter_said` runs the page's own
  jhPanelHTML (jobcard_harness) on each silence, on roles in every state Job
  Hunter answers with (crm.LABEL's words), and on one whose every string is
  markup, and its jhAnswer on each reply a fetch can hand it; the fetch and
  the dialog are held by source. The flag check reads whoAmI's role block to
  its closing brace. Mutation-tested 50 of 50 after a second blind review
  (2026-10-26), the policy 3 of 3.

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
company (`discover_ats.slug_matches`) and the judge does not say MISMATCH. `unknown`
is NOT a pass. Since 2026-11-18 the judge reads a name on Ashby, Lever,
BambooHR, Breezy, Recruitee, SmartRecruiters and six more, and there it says
`matches` only when the name IS theirs (the strict test
`verify_boards.proven` applies); a name that merely contains theirs is still
`unknown`. A careers page must sit on their
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
- **No e-mail address in a tracked file (2026-10-10).** `scripts/privacy.py`
  says what an address is (not RFC 2606 placeholders, not our brand's, not a
  machine's, not an `@2x` image name); proposal ingest and the news sweep
  redact before writing; the write-up door normalises a page through the
  same redaction so a stored quote still matches. `check_no_person_in_the_repo`
  fails on any address in any tracked text file. It used to allow
  "business-contact files" a counted number each, and 73 people's work
  mailboxes sat in `data/suppliers.json` under that allowance - exhibitor
  contacts off sheriffs'-association floors, filed as company names. They
  are out of the files and still in git history; rewriting a public repo's
  history is the owner's call.
- **The crawler honours robots.txt, and says who it is (owner, 2026-10-09).**
  Every PAGE read asks first (`scripts/robots.py`), AT EVERY REDIRECT HOP:
  through `ats._page`, or `ats.follow(url, one)` where `one` makes a single
  request with `allow_redirects=False`. A library left to follow redirects
  asks only the first host, so a careers page that 301s to a disallowed iCIMS
  portal was read on the company's permission. Browsers (`render_fetch`,
  `discover_js`) walk the address's redirects first with `ats.resolve`, abort
  refused navigations in a `page.route` handler, and keep nothing from a page
  whose final URL is refused; Playwright's router never sees a server
  redirect's second hop (measured). Covered: careers pages, newsrooms,
  rendered pages, sites read for a write-up or a submission, and the sweeps a
  person runs by hand (exhibitor floors, directories, website, board and logo
  finders). A new page fetcher that calls `ats._get`, or `requests` with
  redirects on, reads past it. Exempt: the boards' documented JSON feeds
  (Greenhouse, Lever, Ashby and the rest, and `verify_boards`), which are
  interfaces published for programs; `link_check` and `redirect_sweep`, which
  read where an address lands and no content; `logos`, which fetches the
  address a claimant gave us.
  `verify_boards` reads feeds through `ats._get` and a board's own page hop
  by hop through `ats.follow`; its selftest holds it to the user agent, the
  host gate and no retries.
  The rules are RFC 9309's and `robots.py` reads them itself: the stdlib
  `urllib.robotparser` takes the first matching rule instead of the longest,
  ignores `*` and `$`, ends a group at a blank line, and wrongly refused Rain
  Bird. **The path asked is the path served** (eighth blind review,
  2026-11-22): the library resolves a raw '..' before it sends it, and a
  browser reads a backslash in a path as a slash, so '/jobs/../private/x' and
  '/jobs\..\private/x' were asked as written and served as /private/x. An
  http(s) page is read only when its path as written and as served
  (`robots.served`: a backslash, '%2f' and '%5c' read as '/', '%2e' as '.', dot
  segments resolved) are both allowed; `ats.follow` reads a backslash in a
  path as a slash before it asks or sends anything, and `ats.plain_address`
  refuses a path with a '.' or '..' segment in any of those readings before
  anything is asked (`robots.climbs`) - '..;' and '.;x' too, which Tomcat,
  Jetty and Spring cut to a dot segment and serve from elsewhere ('/en/..;/
  careers/' is /careers/; blind review of the eighth review's fixes).
  **They cut a path parameter off EVERY segment** (blind review 9,
  2026-11-23): ';...' to the next '/', before they decode or resolve
  anything, so '/careers;jsessionid=1/private/x' is served as
  /careers/private/x and was asked only as written. `robots.served` cuts it
  off every segment first (after reading a backslash as a slash, as a browser
  does), and a segment that is nothing but a parameter goes with it, as
  Tomcat merges the two slashes it leaves ('/a/;x/b' is /a/b). An empty
  segment WRITTEN as such ('/a//b') is still asked as written and resolved
  as RFC 3986 reads it, not merged as Tomcat and nginx merge it - not
  changed here, and not yet reviewed. `robots.decide`
  asks the path as written, as served so, and as served with the parameter
  kept (`served(..., params=False)`, the earlier reading - a server that
  knows no path parameter), and a page is read only when all three are
  allowed; an escaped ';' ('%3B') is a character of its segment, not a
  parameter.
  A 4xx means no rules; a 5xx keeps out (cached an hour); NO ANSWER is a
  network error, raised as `AtsError("network error: ...")` and never cached,
  so it is retried and never reads as a refusal (`add_company` gives it the
  same tls/dns/timeout diagnosis as the page). A refusal is never "no board":
  discovery skips that page and tries the next, and a site closed at the door
  is filed "could not fetch", which the admin reads as blocked.
  Every user agent names `govtech-dock/1.0` and the repo; none passes as plain
  Chrome. Cost, measured with this matcher on 2026-10-09: 303 postings at 4
  companies (Bruker Detection 213 and BigBear.ai 64 on iCIMS portals saying
  `Disallow: /`, CORE Business Technologies 20 and Fortem Technologies 6 on
  LinkedIn), and 5 boards that had none. A person may still capture a role by
  hand: that is a reader, not a crawler. `check_the_crawler_honours_robots_txt`
  holds it (36/36 mutations). The suite stubs `robots._rules` at the top of
  selftest.py so no check asks a real site; a check that wants rules passes
  its own `fetch`.

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

`python3 scripts/admin.py`, then <http://127.0.0.1:8787>. It opens on the
front door the desk toggle picked (below, "The five front doors"), the
shift among them. Twenty-four queues,
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

**Since 2026-10-22 the FRONT PAGE is public and the boards are signed-in.**
Owner: "we need a better landing page, accessing the job, company, and
conference boards should be sign in", "we want people to log in and
subscribe", and anyone may sign in, free (email and one-time code).
- `landing.html` (option 3 of the "SLED JOBS landing page options" canvas,
  no counts sold on it, no script) is what `_gate.js` serves at `/` to a
  visitor with no verified sign-in, from `env.ASSETS` at `/landing`,
  `no-store`, `Vary: Cookie`, indexable. `/` with only tracking params is the
  front door; any other query (`?tab=`, `?role=`) is the board, deep-linked.
  The files it loads are `PUBLIC` (mascot kit, `og/home.png`, robots,
  sitemap), matched on the raw path with a closed character set. Company
  logos stay gated. A missing landing file falls back to the sign-in page.
- The sign-in page every other path gets is option 1 of that canvas: it names
  nothing about the page asked for. Two actions on the landing: Sign in
  (`to=/`) and Subscribe to job alerts (`to=/alerts`).
- **The `/admin` door checks a ROLE** (`functions/_roles.js` `whoIs`, owner or
  admin on the Users board). A verified sign-in no longer means the owner
  listed you; before this, any signed-in person could read every queue, the
  rulings with his reasons and the Users board.
- **A subscriber's own link passes with no sign-in** (review, 2026-10-22):
  `/alerts?t=<token>`, `GET /api/alerts?t=`, and a JSON POST to
  `/api/alerts` whose body is confirm, update, sync or stop with a
  well-formed token (read off a clone, so alerts.js still gets the body).
  Gated, the stop link in every alert email led to a sign-in, which the law on
  commercial email does not allow. A NEW subscription still needs a sign-in.
  `/data/sectors.json` is public for the alerts page; company names are not.
- The Job Hunter beta page is `/beta/` (it was `/admin/hunter/`, which now
  forwards): any signed-in member reaches it, and it holds no data. Every
  `/admin` path needs Access and the role.
- **Revoking an admin** is the Users board plus a deploy, or the Access
  policy (which lists only the owner and his admins again since 2026-11-13).
- While the boards are signed-in, `sitemap.xml` lists `/` alone and
  `robots.txt` allows `/` and the art (`build_site.boards_signed_in` reads
  `GATED` off `_gate.js`; `/?utm_` is allowed too, so a shared link unfurls).
  `csp.py` has a `landing` kind with no script; the landing served at `/` gets
  the holding policy, which allows no script.
- Guards: `check_the_front_page_is_whole_and_public` (shipped, no script, no
  counts, no placeholder, every file it loads in `PUBLIC`), the front-door and
  subscriber cases in `check_the_site_is_signed_in_only_until_launch`, and the
  role cases in `check_the_admin_door_is_verified_on_every_hostname`. Blind
  review (3 lenses, a skeptic each): 4 confirmed and 9 smaller, all fixed;
  22 of 22 breakages caught.
- **Members sign in with the SITE'S OWN SIGN-IN, not Access (owner,
  2026-11-13: "anyone can sign in", then, told every Access sign-in takes one
  of the Zero Trust free plan's 50 seats and the 51st person is turned away:
  "build").** `functions/_session.js` (the core), `functions/signin/`
  (the pages and doors), `functions/_signin_page.js` (the card), and
  `functions/api/whoami.js` (outside `/admin`; `/admin/api/whoami`
  re-exports it).
  - `/signin`: Continue with Google (when `GOOGLE` is true), or an address.
    The email (Resend, `alerts@`, click tracking OFF) carries a link and a
    10-character Crockford code (50 bits) that signs in only the device
    holding the pending cookie it was derived from. Both live 15 minutes.
    **The link asks before it signs in**: GET draws "Sign in as <address>"
    and a button that POSTs the token back, so a mail scanner that opens
    every link is handed nothing and somebody else's link cannot silently
    replace your sign-in. Google is OpenID Connect's code flow with PKCE against our own
    OAuth client (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`), scope
    `openid email`, `email_verified` required.
  - **Nothing is stored.** A sign-in is `__Host-sj_session` (30 days,
    HttpOnly, Secure, Lax, no Domain), an HMAC-SHA256 under
    `SESSION_SECRET` over the address and method; every sealed value names
    its purpose (session, link, pending, oauth) inside the MAC. No secret, or
    one under 32 characters, and nothing verifies. One person cannot be
    signed out alone; changing the secret signs out everyone.
  - Email sign-ins are capped (KV counters, 26 h): 20 a day per network (IPv6
    by /48; offices and conference Wi-Fi share one address), 4 per INBOX (`mailbox()`: +tags off, Gmail dots folded), 60 in
    all (`SIGNIN_MAILS_PER_DAY`), because the free Resend allowance (100 a
    day) is the digests' too. ALL are read before ANY is written, so a
    refusal writes nothing (KV's 1,000 writes a day are alerts' and claims'
    too). KV has no atomic increment, so the SHARED key is written first
    with the request's own mark and read back: a refused write, or a mark
    overwritten by a simultaneous request (same or higher count, other mark),
    refuses; a stale read never does. Bursts of 13 from 13 networks send one
    each (final review: 104 emails got past a cap of 60 before). Across
    Cloudflare's locations the count stays approximate; Resend's 100 a day is
    the backstop, a Durable Object the firm fix. Every POST that
    sends mail or sets a cookie must come from our own page (`fromOurPage`:
    Origin, else Sec-Fetch-Site). Refusals mention Google only when it is on.
  - Our cookies are read by `_session.ours()`: ASCII-trimmed, and a name that
    arrives twice is no cookie (a sibling host cannot set `__Host-`, so a
    duplicate is a plant). `/api/whoami` prefers a verified Access sign-in
    over ours when both are held. `/signin` refuses to be framed.
  - **Light and dark (owner, 2026-11-14: "I want white/dark options on all
    of them").** The sign-in pages, the gate's card, `/beta/` and `/alerts`
    are dark by default like the board and light when chosen. The
    `sled-theme` cookie DECIDES (every switch writes it: the board's
    `toggleTheme` via `themeCookie`, and `/signin/theme` on the no-script
    pages), localStorage only where it is missing; each page reads it in its
    `<head>` before painting, the sign-in pages server-side
    (`_middleware.js themeSignin`, `_signin_page.themed`). Colours and the
    card live once in `functions/_card.js`.
  - **The gate's card carries the address box** on the site's own host
    (owner: "do it please!"), and, with a sign-in pending on the device, the
    code first; elsewhere it links to the site's sign-in. It refuses to be
    framed wherever it is served (`secure`: holding and not 200).
  - The sign-in pages and the gate's card are the `signin` and `holding`
    CSP kinds: no script, and the ONLY kinds whose `form-action` is
    `'self'` (`csp.FORMS`). **Their Referrer-Policy is
    `same-origin`, NEVER `no-referrer`**: under no-referrer a browser sends
    every POST with `Origin: null`, and the first version refused every
    Chrome sign-in that way (runtime review, 2026-11-13; the harness's fake
    browser had always sent a real Origin, so the suite enforced the bug).
    `fromOurPage` takes `Origin: null` only with `Sec-Fetch-Site:
    same-origin`. Asking for an email answers 303 to `/signin/check` (a GET
    drawn from the pending cookie), so a reload never re-sends the email.
    Google's request carries a nonce sealed in the state, checked in the ID
    token. The GET pages answer HEAD; `/signin/` with a trailing slash passes
    the gate. `/signin` with a sign-in pending goes to the code box (the
    email says to type the code there); the POST doors opened as pages go
    back to it; Back after signing in is not "run out"; a `to` over 1,024
    characters is "/" (`_to.js` MAX_TO), since a sealed one must fit a
    cookie. A real-browser run (Chromium, the real middleware and CSP)
    signed in by code and by link, and out, with a real Origin on every
    POST.
  - **The gate takes ours or Access's**; `/admin` takes Access alone (the
    owner's Access sign-in opens the boards too). `access_harness` proves a
    real session on the owner's own address is refused at `/admin`.
  - The `to` of every door is `_to.js`'s, held again by each door that
    redirects, even from a sealed value.
  - Guard: `check_our_own_sign_in_proves_the_address_and_opens_only_the_boards`
    (`scripts/signin_harness.mjs`, signing in with the link and code out of
    the mail its stub received). Blind security review (2026-11-13): no way
    to sign in as somebody else or reach /admin; six smaller findings, all
    fixed.
- The owner's dashboard steps are DEPLOY.md §3a: `SESSION_SECRET` (and the
  two Google secrets) as Pages secrets, a redeploy, then Path `admin` back
  on the EXISTING Access application with its policy left as the owner and
  his admins (NOT Everyone), Fail closed kept. No second Access application:
  the 2026-11-02 "SLED JOBS Google sign-in" one is not needed.
- **"Continue with Google" is built and OFF** (owner, 2026-11-02: "Can I
  have the cloudflare sign in and future google sign in on the landing
  page"); since 2026-11-13 it is the site's own (above), and until the
  client exists `/signin/google` sends a person to the email sign-in.
  `landing.html` and the sign-in page carry both versions of every changed
  line between `<!--google-on-->` / `<!--google-off-->` markers, and
  `_gate.js` `signinBlocks()` keeps the one `GOOGLE` says; `build_site`
  `sign_in_version()` makes the same choice when it ships landing.html, since
  `/landing` is served as-is to a signed-in person (review: both versions
  showed there at once). Flip `GOOGLE` only after DEPLOY.md §3a step 3 is
  done and the owner's private-window run through Google lands on the board.
  Guard: `check_continue_with_google_waits_for_its_own_door`.

**Until 2026-10-22 the WHOLE site was signed-in only (owner, 2026-10-05).**
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

### SLED roles only, read one by one (owner, 2026-10-27)

"Review the roles open, make sure they are just sled jobs not b2b." All 694
openings on the board's default were read (four readers, a skeptic each,
B2B only on words a reader could quote): 200 SLED, 186 B2B, 11 federal, 106
selling to both, 191 that title and page did not settle. The owner's
rulings on those counts:
- **B2B and federal off**: 197 per-opening rulings in
  `scope_decisions.json` (journal 2026-10-27#1, by
  `agent:sled-review-2026-10-27`, each with the reader's reason), 694
  openings to 497 before the second read.
- **Roles that sell to both stay.**
- **The unclear are read further**, from the full ad on the company's own
  board feed (`ats.fetch` with details on, for those titles only), not
  dropped. The second read (157 of 191 ads found; a reader and a skeptic)
  ruled 95 more B2B (ZOLL's 45 LifeVest and hospital territories among
  them) and 16 more out of scope (US federal, or a non-US role selling to
  another country's government: Thales, Mitel and Xylem in Europe, Harris
  in England). 22 sell to both, 17 are SLED, 41 stay unclear and stay
  listed. 308 rulings in all; the board's default went from 694 openings
  to 386.
- **No title rule.** Asked whether the 75 business-first vendors should
  carry `sled_only`, he said "read the roles and see if that role does": a
  role is judged by its ad, because a title rule drops real SLED roles whose
  titles do not say so (Bentley "DOT", Hyland "Education", ZOLL "EMS").
- **The daily review (owner: "yes set up the daily review").**
  `scripts/scope_review.py brief` lists the openings on the board's default
  that no ruling and no earlier read covers, each with its full ad where the
  company's own feed publishes one, and the reading `rules`; a reader and a
  skeptic return verdicts; `apply --write --by agent:...` turns B2B and
  FEDERAL into scope rulings (journalled; a B2B call with no quote is
  refused by name; a ruling on file is never overwritten) and records every
  verdict in `data/scope_reviews.json` so a role is read once (seeded with
  the 694 of 10-27). `unclear` lists what no reader could call, for the
  owner. A scheduled task on the owner's Mac runs it daily in its own
  worktree (~/govtech-dock-scope), redraws, runs selftest and pushes.
  `check_the_daily_scope_review_reads_once_and_rules_only_on_quotes`.
- **A capture answers to the scope rulings too.** `merge_manual()` never
  asked `out_of_scope()`, so two hand-captured B2B roles (SAP Concur, Nearmap)
  stayed listed after their ruling. Both builds pass `scope` now;
  `check_a_capture_answers_to_the_scope_rulings`.

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

## Conference data quality, 2026-10-09: what the review found public

The owner's ask: "review the conferences for data quality; make sure we are
scrubbing the expo halls." His rulings are in memory and in
`~/.claude/plans/conference-scrub-2026-10-09.md`: scope is the 138 live
conferences AND the staged registry; scrubbing must produce staged lists, tags
on companies on file, AND new companies through the card door; one pass now,
the weekly cadence later; Claude reads the floors with a skeptic behind and the
owner on a 5% sample; dates may be proposed by reader + skeptic and published
only when the organisation's own page states them.

**Stage 1 (landed): the defects visible on the live site.**

- **A label with a year in it is not an event.** `_event_tags` counted
  anything digit-bearing, so "research pass: HHS conference exhibitor research
  2026-08-25" sat on ten company pages as a conference they exhibited at and
  the tab offered it as an event nobody runs. It now reads only tags the
  catalogue issued (`issued_tags(cat)`: event_tag and prior_tags), carried in
  `run_context` as `issued_tags`, and both call sites pass it. A source label
  outside the vocabulary is provenance, never an event.
- **508 supplier rows were an association's navigation.** `conference_intake`
  filed every name off a page as a supplier before the `menu` grade gated it,
  so "Member Login", "Board of Trustees", "Keynote Speakers", people's names,
  bare domains and 71 addresses or copyright lines were supplier records. The
  purge used PROVENANCE, not word patterns (a pattern caught Driver Training
  Solutions): rows with no website whose every source tag is a menu-graded
  floor, judged by two readers and a skeptic (`purge-navigation` in the
  journal, 2026-10-10). Real exhibitors among them stayed: Anvil Press, Reach
  Alert, AM Quartex, Arcadia Publishing. The link profile ("sponsors link out,
  menus link in") separates them on 3CMA and APPA and fails on NC League
  (sponsors link to internal profile pages) and NCSEA (hundreds of .gov
  members on the same page) - the reader is the tool, as the 2026-09-02 note
  on that signal already said.
- **The MESC "ended" ruling (2026-09-03) was recorded and never applied.**
  `conference_dates --apply` wrote the catalogue with `\u` escapes against a
  file committed as raw UTF-8, which made a one-line ruling a ten-line diff; it
  writes `ensure_ascii=False` now, like `apply_conference_reads`.
- **Three things about the staged floors, for stage 2.** `swept` is set by hand
  on 12 rows and by no script; the admin's floor queue and the tab both count
  from staged files and companies instead, so the flag is furniture. Every row
  with an `exhibitor_url` already has a staged file and nearly every
  names-bearing file is intaken: the gaps are the 58 rows with no directory
  address, the 21 menu floors, 11 floor-plan apps that need a browser, and
  4,023 names the classifier left unjudged. Three "good" or staged pages are
  not floors at all on reading: ACCG's is a job board, APPA Public Power's and
  Animal Care Expo's are navigation.
**Stage 2 (landed): the floors are read before they are filed.**

- **`scripts/read_floor.py` is the reader's deterministic half.** `brief`
  lists every staged name nobody has judged (not on the board, not a
  candidate, not a supplier a research pass stamped); `apply` stamps a
  reader's verdict beside each name - `vendor` (is_govtech true, a research
  candidate), `supplier` (false), `not_a_company` (leaves the floor, and the
  supplier row filed off it leaves through the journal: only a row with no
  website that THIS floor filed alone, `purge_suppliers`; a TRB and an FDIC
  reader once deleted BuyBoard and Eastern Kentucky University, filed off NSBA,
  NRPA and IAFC FRI, 2026-10-26, undone), `unsure` (stays
  null, held). A floor whose every name is judged is graded `read`, which
  intake accepts where it refused `menu`; a floor whose every name was the
  site's own text becomes `found: false` with the reason, so the queue shows
  a wrong address, not a swept floor. Verdicts are kept in
  `data/conference_intake/reads/`.
- **Two readers and a skeptic, blind to each other.** 4,023 names in 15
  batches; the skeptic tried to refute every `vendor` call and a sample of
  the deletions. Deletions held (0 disputed in 230 sampled); `vendor` was
  over-called by 15-30% a batch, mostly passive RF component makers,
  consultancies and benefits programmes. A dispute resolves the conservative
  way: `supplier` when the skeptic says so (visible, promotable), `unsure`
  when the readers split, with `disputed` kept on the row so the research
  stage can pick disputed vendors up after the clean ones.
- **Intake holds back the unread.** `is_govtech: null` is no longer a
  supplier; it is held, counted, and named (`--allow-unread` to insist).
  Intake refuses `found: false` and writes the journalled companies file
  before the bare suppliers file. `classify_exhibitors` never overwrites a
  reader's verdict.
- **The link profile was measured again and is still not a detector** (it
  separates sponsors from navigation on 3CMA and APPA, fails on NC League and
  NCSEA); the reader is the tool.
- **`scripts/read_page.py`** prints a page's text the way the crawler reads it
  (robots.txt at every hop, our name on the request) so research agents never
  fetch with their own user agent. **`scripts/promote_cards.py`** is the card
  door: `--verify` looks for each card's evidence quote on the company's own
  site, `--second-read` records a blind second reader, `--gate` prints what a
  person reads (refusals, low confidence, evidence not on the page, split
  verdicts, a 5% sample), `--land` lands only high-confidence cards whose
  quote is on the site and whose second reader agreed, and only after the
  gate has printed. A govtech card whose only match is an unresearched
  supplier row moves that row out through the journal rather than adding a
  twin. Two words the door misread (card round, 2026-10-26): the country "US"
  in a description is not the pronoun (the write-up door already knew), and
  a legal form (Corporation, Limited, Ltd, GmbH, PLC) is not a distinctive
  word, so two "... Corporation"s are not held as twins. `--land --keys
  FILE` lands only the cards a round researched itself, which is how an
  unattended run (the weekly conference scrub) must land. `--land` with no
  filter is refused since 2026-10-28; `--all-gated` lands every pending card
  that passes, the owner's refused pile included: a round lands its own keys. **Dates:** `conference_dates.confirm` also reads `dates_source`;
  `apply_conference_reads` takes an `unannounced` read as a dated note.

**Stage 3 (2026-10-15): the registry's promotion door, tightened from use.**

- **A matcher fix does not reach the rows it already wrote.** Stage 1 resolves
  only rows with no `org_url`, so Kansas kept the Arkansas Municipal League and
  the Association of Arkansas Counties, and West Virginia the Virginia
  Municipal League, six weeks after both matcher bugs were fixed. Re-resolved
  (lkm.org and kansascounties.org answer 403 to an identified crawler, so
  `org_unreachable`). `check_chapter_listings_are_looked_up` now refuses any
  row whose observed organisation names another state.
- **A table that says it is the floor is read** (`table_of_exhibitors`): a
  header naming a company column AND a booth column. Illinois APCO's 80 vendors
  were invisible because 23 menu anchors passed MIN_NAMES and the cell
  fallback never ran. It is ADDED to the harvest, never instead: measured on
  the 75 live floors, 8 carry such a table, and replacing the harvest dropped
  four sponsors linked outside it on CCM's page (Tyler among them). A table
  with NO header counts when 80% of its rows carry a "Booth #N" cell, and only
  those rows are read (NACE's 48-row list was read as 56 menu items and was
  one stamp from being filed "not a floor at all"). With both forms, 16 of the
  live floors carry such a table; four held 20 names nobody had staged
  (Smart Cities Connect's 12 among them).
- **A booth number is not part of a name** (`strip_booths`): floor apps print
  "1035 PCTEL, Inc." in one cell. APCO's browser read carried one on 246 of
  253 names, ICC's 112 of 119, NAPT's 76 of 87, and no such name matches a
  company on file, so known exhibitors went untagged and every name went to
  the reader as new. Taken off only where 70% of a floor carries one (3M,
  2J Antennas and 360 Photo Booth are names); `--restage` applies it offline.
- **A browser read ADDS to a staged floor, never replaces it**
  (`render_floor.add_to`). `merge_into` only stops a refusal overwriting a
  find, so the first `render_floor --all --write` put a 22-item menu over
  WEFTEC's 1,142 read names and IAFC FRI's 302 with 201 (restored the same
  day, before anything was intaken from them).
- **The platform names its own field** (`named_fields`, 2026-10-16): a2z
  prints `<a class="exhibitorName">AccuTemp Products</a>` with no href, so
  SNA's 2027 floor of 289 read as its 20 menu links; CEC's as 25 of 94. A
  class naming the exhibitor's or company's name is read on top of the rest
  (not a bare `class="name"`). It added 278 names to SNA, 48 to NLC City
  Summit, 10 to Smart Cities Connect.
- **A logo wall is read by its alt text, and only where a reader said
  "image"** (`logo_names`, `sweep_exhibitors --images`). Linked logos off the
  page's own host, alt text filtered of file names and social icons. On any
  other page a logo strip is as often the association's year-round partners,
  which is not this event's list. NASS, NCSHA, PTI, NACVSO staged this way.
- **A directory reader's "found" is confirmed only when the crawler reads at
  least half the names the reader saw**; below that the list draws in a
  browser and the row is marked js-walled for render_floor.
- **`clean()` decodes until stable** (`&amp;amp;` off an alt text) **and takes
  a leading call to action off** ("CONTACT BOTCOPY"), never "Contact Us".
- **render_floor on ExpoCAD reads the category tree, not the exhibitors**
  (AB Show: "Bleachers/Seating Athletic Business"); the reader deletes them.
  A click into ExpoCAD's exhibitor view is not built.
- **A site builder's credit is not an exhibitor** (`credited()`): an anchor
  after "powered by", "websites by", "site by", "designed by" is cut. Illinois
  APCO's footer tagged CivicPlus with a 911 conference, unread, because the
  floor reader skips names already on the board. Measured on the live floors
  it cuts only credits: A2Z Events on seven, GrowthZone, Visual Composer and
  two web agencies. "Sponsored by" names a sponsor and is kept.
- **Promotion re-judges the directory page** with `judge()` before it writes.
  The status alone was trusted, and a reader's attribution set Indiana Parks'
  page to `directory_found`: its sponsors are logo images, its text is the
  menu, and it was promoted and withdrawn the same day. A reader's lead is
  judged like any candidate, never landed as a verdict.
- **The name that goes public is read, and cleaned only of what is not the
  name**: a bare state ("Illinois", APCO's link text) is not an organisation;
  CivicPlus's "Official Website of the" is the platform's; `event_name_from`
  decodes entities and takes a list's caption off ("IPSTA Conference Vendors-
  as of 10/12/2026" is "IPSTA Conference"). Five stored names corrected.

- **A "show more" that added nothing is not pressed again**
  (`render_floor.expand`, 2026-10-25). MapYourShow puts a "Show All" letter
  filter above "Load More Results", both read as "show more", and the first
  was pressed every round: FDIC's 924 exhibitors read as 198, DISTRIBUTECH's
  stopped at E. A press spends its control only when the page has not grown
  within GROW_WAIT_MS (one reading at 1.5s spent a 2-second Load More), a
  control is the same control by its text and address (Playwright hands back
  new element objects every query), a link to another page ("See All
  Speakers") is never pressed, and a button that navigates is gone back from;
  if the page cannot come back, render() reads nothing. The test's fake is
  browser-shaped in each of those ways (second review, 2026-10-26).
- **A sponsorship tier is not part of a name** (`clean()`): Sched writes
  "Esri - Diamond Sponsor", "VertiGIS & YP Meet-Up Sponsor" and "Terracon Data
  Solutions - Workshop & GeoPoker/Milwaukee Landmark Crawl Sponsor". After a
  dash or bar the whole label goes (`SPONSOR_LABEL`); without one only
  sponsorship words go (`SPONSOR_WORDS`), and an "&" is a separator only when
  every word after it is one: "Black & Veatch Gold Sponsor" is Black & Veatch,
  which the first version cut to "Black".
- **A logo wall is read without the organiser** (`logo_names`): a logo linking
  a host the page's header or nav links is the site's own, wherever it sits
  (Humane World for Animals was staged as a sponsor of its own Animal Care
  Expo, from its brand in the header and footer). An alt ending in "Logo" names
  what stands before it ("GotchaCot Logo" was dropped whole), and a sponsor's
  logo no rule can name is listed on the staged floor (`logos_unread`) rather
  than lost from a floor graded as read.
- **A row never swept files nobody, at every door** (2026-10-26).
  `sweep_exhibitors.never_swept`: ruled out of scope (`sled: false`, off the
  tab too) or listed and never swept (`harvest: false`: an ADJACENT, PROVIDER
  or INDUSTRY event, or a training one, which stage_promote now writes onto
  the row from the registry). render_floor held the first and the sweep held
  neither, so TechConnect 2027 (sled: false) was swept from a reader's address
  and intaken: one company tag, four new suppliers, six supplier tags.
  Reverted the same night (journal 2026-10-26#4). The sweep, the browser read
  and conference_intake (under the event's tag, stripped, or a prior one) ask
  the one question. `check_a_non_sled_event_is_listed_never_swept`.
- **The supplier rulings file holds every batch.** merge_supplier_records
  passes over a ruling already on file (`landed`), so a second batch joins
  the first; a twin with no website at all is folded on its name and shows,
  and two different websites still refuse.
- **`check_a_redraw_is_the_crawl_without_the_fetch` flaked once** during this
  work: 21 organisations differed after its randomised edits (everbridge,
  urban-sdk, placer-ai first), none with a source the tag change can touch,
  and it passed on three reruns. Not understood; written down so the next
  failure is read as the second, not the first.

## For exhibitors: what a seller needs to get on a floor (owner, 2026-10-27)

"What other info can we take and include on the page? including links to
their stuff? costs?" - and, offered four kinds, all four: COSTS (attendee
registration, booth, sponsorship), EXHIBITOR LINKS (prospectus, booth
sign-up, floor plan, exhibit sales contact, registration), DEADLINES and WHO
ATTENDS (stated attendance, audience, whether a vendor may come without a
booth, the organiser).
- **Read off the organiser's own pages, never guessed.**
  `scripts/conference_facts.py brief` lists the conferences with no facts,
  facts for an edition before the next one on file, or facts older than
  120 days, with the reading `rules`; a reader collects, a second reader
  re-opens every source and sets `confirmed`; `apply --in reads.json --write
  --by agent:...` is the door. It refuses, item by item: an unconfirmed read,
  an older edition than the one on file, a cost whose figure is not in its
  quote or carries no currency, a deadline that is not YYYY-MM-DD, an unknown
  link kind, anything not http(s). A read replaces a conference's facts
  whole, so last year's price never sits beside this year's.
- `data/conference_facts.json` keeps the quotes; build_board ships each
  conference's facts on its row WITHOUT them (`load_conference_facts`), and
  the page links the source beside each figure instead.
- The app's conference panel (`cfFactsHTML`) and the /e/ page
  (`_conference_facts_section`) draw "For exhibitors" under "the <edition>
  edition, read <date>": links, costs, deadlines still ahead, who attends.
  A refused link draws no door; a passed deadline is not listed.
- The weekly conference scrub (Sundays) reads them as it goes.
  `check_a_conference_fact_is_read_never_guessed` (5 of 5 breakages caught).

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

## The five front doors, and the batch engine (owner, 2026-11-01)

"so read 58 per batch instead of 5%? also build all 5 and just have a toggle
where I can pick which one to use. make sure all of them are wired together."
The five are round 2 of the admin canvas (claude.ai artifact
CUsqeCcCC1E1fmePcWK4bm): 1 Waiting on you, 2 Morning brief, 3 Batches,
4 Health map, 5 Ask the desk. The finding they answer, measured that day: the
shift had never been run, the owner's rulings came in bursts (mostly in chat
with Claude), and about 9,000 rows waited in 24 queues.
- **The toggle** (`#dk-toggle`, under the header) picks the door the desk
  opens on, remembered in this browser (`localStorage desk.view`, Waiting on
  you until another is picked); the Shift is the sixth door. The nav's Desk
  tab goes to the picked one.
- **One core, `scripts/desk.py`, read by all five** through `/api/desk/*`
  (questions, batches, batch, piles, health, brief, ledger) and the actions
  batch-mark, batch-rule, batch-undo, desk-answer and desk-ask (read-only,
  open). Every view draws the same question card, ledger, pile list and batch
  screen, and opens queues through `goQueue` with the department, so a ruling
  in one is the truth in the other four.
- **Questions only the owner can answer** are computed (the launch, asked of
  the site itself signed out, in the background at most every half hour, and
  an alarm if the boards answer without a sign-in; submissions) or filed in
  `data/owner_questions.json` by whoever found them, each answer naming what
  happens next and the write it makes (`apply`): `not_a_supplier`, or
  `ask_claude` (one job a question in Claude's inbox). An answer that writes
  needs the owner's own why. Answering is journalled; Undo takes back the
  answer and its write, not an inbox job.
- **The health map** counts by `page_belt.checklist`, the page sweep's own
  definition. A company where discovery found no board counts as WITHOUT one
  (it waits in No board found, and a scan never proves absence); the number
  is shown beside the column. A square's levers are only the piles that move
  THAT square (`closes`).
- **The batch engine** (direction 3). A pile is one machine answer already
  stored on a queue's rows, where the owner's rules allow ruling fast
  (`PILES`): empty page reads, Wrong bucket at high and medium (a proposal
  its button can take: `_placeable`), researched founding years with a quote,
  and Website review and Wrong placement read whole. Every other queue is in
  `NEVER` or `NEEDS_AGENT` with its reason, and
  `check_every_queue_is_a_pile_or_says_why` makes a new queue choose. Not
  batched, by rule: **Write-ups** (the gate: every refused, medium, low or
  unsure write-up is read by a person), **Supplier websites** (a held
  supplier is released one look each, 2026-10-05), No board found and Scrub
  (a scan never proves absence), Acquisitions, Vendor scope, Duplicates,
  Boards we found, people. The first blind review found the write-up and
  supplier piles broke those rules; the owner can reopen either.
- **The sample is sized by what it proves, not by a percentage**: zero-defect
  acceptance sampling at 95% (hypergeometric). A pile that reaches the public
  board reads about 58 cards with none wrong (under 5% wrong); a record-only
  pile about 29 (under 10%); 20 rows or fewer are read whole, and accepting a
  read-all pile takes only the cards marked right.
- **Marks** (`data/batch_marks.json`, gitignored, not journalled: up to 58 a
  pile would push real rulings out of the 500-entry live journal) are kept
  per PILE, not per department view, and only a person makes one. A mark
  holds only while the card shows what it showed (`_seen`, a fingerprint of
  what the ruling would write). A card marked wrong stands for the round,
  wherever it was seen and wherever its row has gone since; withdrawing one
  is kept and the ruling says so. Every ruling starts a new round with a new
  draw (the rank is a hash of pile, round and key). The ruling copies the
  round's marks, with who made them, into the journalled
  `data/batch_audits.json`.
- **Accept runs every row through the applier its own button uses**
  (`APPLIERS`), with saves held in memory (`held_writes`: admin.read,
  read_companies, save_*, write_atomic and agents.load, which reads the
  proposals file off disk and would otherwise keep only the last row), then a
  `landed` test per row, then ONE journal entry per file (chunked under
  RUNAWAY), force only because `expect_n` matches the count shown. A row its
  own applier refuses is held out of the pile, named, and the person looks
  again (`held_out`). Every ruling record it wrote says `via: batch:<id>`;
  `_ruling_stamps` counts a batch once, the receipt winds back by the rows it
  landed, and the agree-rate, the why meter and the shift tally skip `via`
  records (a batch must not buy those numbers with volume).
- **Batch undo is field by field** (`act_batch_undo`): the nightly rewrites
  hiring on every company, so act_undo's whole-record test refused every
  batch by morning. A field the batch wrote and something changed since is
  named and nothing is written; a dismissal made later in a queue the batch
  created stays (`CONTAINERS`); records the batch added are removed under
  `journal.undoing_adds()`, which lifts RUNAWAY for exactly those keys and
  cannot leak. A batch any of whose entries the journal has archived is
  refused whole (`batch_live`); the ledger offers no Undo for it.
- **The ledger filters on the server** (`who=mine|batch`, before the limit)
  and says how far back the live journal goes. A person's entry without the
  desk mark from before 2026-10-24T15:23 is "yours"; after, "yours, in chat".
- Guards: `check_a_sample_proves_what_it_says`,
  `check_every_queue_is_a_pile_or_says_why`,
  `check_a_batch_is_ruled_on_its_sample_and_undone_whole` (the real appliers
  and a test pile, in a sandbox) and
  `check_the_batch_screen_runs_right_under_its_own_script` (harness cases
  14-19). Blind review (3 lenses, a skeptic each): 35 confirmed, all fixed or
  ruled out by taking the two piles away.

## The shift: the desk admin's front door until 2026-11-01 (owner, 2026-10-23)

"build option 5 for the admin": the fifth option of the admin canvas, "free
rein: a shift on the show floor". The admin opened on it until the five front
doors (above); it is the toggle's sixth door now (`showShiftHome`);
Start here is a button on it and a tab, and every queue is one click away
under "Or work one queue".
- **A shift is a box the person picks** (10, 20 or 30 minutes) **and a hand
  the server deals** (`admin.shift_hand`, `/api/shift`): people first
  (Submissions), then the rows that move board health most, at most a
  quarter of the hand from one queue, one card a minute. The deal is a deck
  size, never a pace. Acquisitions is never dealt (`SHIFT_QUEUES`).
- **A card's points are board_health's own step** (`per_item`: one settled
  row's move of the score). A wrong-bucket row with no open posting moves
  nothing, and a founding year moves only when the card showed a blank; a
  card that moves nothing says why (`no_points`). Health moved during a
  shift is the BOARD's, said "with anybody's write", and a department's
  health says its Scope settled part is the whole board's.
- **The deal ranks by points except Founding year**, whose unconfirmed
  years (an agent's year on a public card nobody has stood behind) rank
  with the blanks, in q_founded's own order (`_shift_rank`): by points
  alone no unconfirmed year was ever dealt. "Waiting" is the queue's rows
  in the department, and the hand counts the rows it never deals (nothing
  for the guesser to go on).
- **The keyed cards are the belt's card for a belt of one.** Wrong bucket
  and Vendor scope are drawn by `beltCard()` and ruled by its own
  choose/commit, so two acts, the LOW-confidence why and the per-band
  agree-rate hold unchanged; `advance()` and `renderBelt()` hand the card
  back to the shift. Every other queue is its own `RENDER[queue]` card,
  untouched, with a Skip; the renderer removing its card after a write is
  the "ruled" signal.
- **Every card is re-asked of its queue before it is shown**
  (`/api/shift-card`); one ruled elsewhere since the deal is not shown and
  is counted as gone.
- **Undo [U] takes back the last ruling: every journal entry its writes
  made, all or none** (`act_undo`, `/api/undo`). `do_POST` hands back the
  ids a request journalled (`journal.WRITTEN`). It refuses an entry that is
  not a person's, an undo, one already undone, and the whole undo when any
  record has changed since (`plan_undo`), before writing anything; a
  multi-entry ruling counts as one undo (`undo_batch`). An undone ruling is
  not a ruling: `_journal_rulings` leaves it out, as the decision files
  already did by deleting it. `admin_undo.py` stays the tool for the rest.
- **Every count is the server's.** The meters and the summary read
  `shift_summary` (`/api/shift-progress?since=` the deal's own time): rulings
  from `_ruling_stamps`, proposals taken as proposed, how many were "far
  enough apart to count" (READ_SECONDS after the ruling before, of any
  queue, walked record by record so a burst inside one second is not
  credited), undos. The page counts only which card is on screen. The
  timer counts down to a sentence and ends nothing.
- **One desk request is one ruling.** While do_POST runs, `journal.DESK`
  marks every entry `desk` and every entry after the first
  `same_ruling_as` the first, and `_journal_rulings` counts a group once:
  approving a submission (company + submission) or correcting a year
  (set-founded + confirm-founded) counted two. A write-up landed at the desk
  (`promote-profiles`, desk only: the CLI's gated landings never counted)
  and a write-up rejected are Write-ups rulings (`_ruling_queue`). An undo
  that was itself undone cancels nothing. A Boards-we-found ruling is
  written through `save_decisions` (`_rule_board_proposal`), so undoing an
  accepted board takes the ruling back with the wiring; a job approval's
  capture is the person's, so its Undo is not refused. The receipt and the
  Last rulings strip read the same way: one row and one reversal per
  request, and a board an undo took away is not a door opened
  (`_undone_ids`, shared).
- **The page holds a card's writes to that card.** `api()` counts a run's
  POSTs in flight with the seq of the card on screen when sent
  (`shiftWatch`/`shiftHeard`); Skip and Undo wait while one is on its way,
  and Undo also while a landed write's card has not moved on (Undo during a
  write once undid the card before and credited the wrong one). A POST that
  writes nothing (`READ_ONLY_POSTS`, sent with the queue counts: a Check, a
  page read) holds nothing back. A write that lands after its card left goes
  to that card, a Skip after a landed write is a ruling, and `shiftResolve`
  resolves only the card the deal showed (`onScreen`, seq). A card the server cannot be asked
  about stays in the hand with Try again; a deal that fails gives the tabs
  back. A refused code is forgotten so the home draws a box for the new one;
  the code bar no longer opens on load (the home has the box), a code typed
  there goes with a person who leaves by another door, and a refusal with
  no code stored asks for one rather than calling it stale.
- **A mutation run once wrote a test ruling into the owner's
  board_proposal_rulings.json**: its copy linked to the real data/, and
  board_proposals fixes its paths at import, outside `_sandbox_admin`. The
  sandbox now moves board_proposals too, mutation copies hold their own
  data/, and the suite fails when a file it touched gained a value starting
  "selftest". Restored from git before anything was committed.
- **End shift** appends the server's record to `data/admin_shifts.jsonl`
  (gitignored, a plain append like Claude's inbox: the desk's own sittings,
  not rulings). The home lists the last six.
- During a shift the tabs and the department picker are hidden and
  `refreshCounts()` is skipped (`/api/queues` takes 2s on a single-threaded
  server, between a ruling and the next card).
- Built on this sheet's square system, not the canvas's rounded cards.
- `check_a_shift_deals_rules_and_undoes_what_it_says` (the deal on real
  data, the writes in a sandbox and through a real server),
  `check_a_shift_counts_each_ruling_once` (the review's server findings),
  `check_the_shift_screen_keeps_the_belts_rules` (source, the helpers under
  node, and `node --check` over the whole admin script, which the suite
  never had) and `check_the_shift_runs_right_under_its_own_script`:
  `scripts/admin_shift_harness.mjs` runs admin.html's own script in a node
  vm against a server that answers one request at a time, as admin.py does,
  and presses keys through the page's listeners. Two blind reviews (3
  lenses then 2, a skeptic each) confirmed 18 and 6 defects, all fixed; the
  reviewers' reproductions became the harness.

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
- **Double opt-in, no override, with ONE proven exception (owner,
  2026-11-14).** `send_digests.py` skips unconfirmed addresses and has no
  flag to stop skipping them. The exception: a signed-in person subscribing
  the very address they signed in with (email link or code, Google with
  `email_verified`, or Access) has proved that mailbox, so a NEW subscription
  starts confirmed (`confirmed_by: "sign-in"`), from our own page as JSON only
  (`fromOurPage`). An existing confirmed one never hands its token to the
  page (it is permanent and carries the saved roles): its settings link is
  mailed, as before. A pending one is replaced, so the mailed token opens
  nothing. Every other address keeps the confirmation email.
- **Nothing sends without `--send`.** The dry run is the default.
- **`last_sent` advances only after a successful send**, so a mail outage
  delays roles rather than swallowing the window they were in.
- **Subscribe answers identically** whether the address is new, pending or
  already subscribed. Varying it makes the endpoint an oracle for "does this
  person have an account on a job board", which is exactly the question this
  site must never answer about somebody. The answer differs only toward the
  person who proved that mailbox by signing in (above): never about anybody
  else. The alerts page fills in the signed-in address from `/api/me`
  (gated; the caller's own address only).
- **Unsubscribe deletes.** No suppression list — that is still a record of who
  wanted out of their job.
- **Saved roles follow the sign-in (owner, 2026-11-18: "saves should follow my
  sign-in").** He signed in from a private window, found none of his saved
  roles and asked whether he had made a second account; there are no accounts,
  so the signed-in address is what the list now hangs from.
  `functions/api/saved.js` (GET; POST merge-and-answer; DELETE) keeps it in
  **D1 bound as `DB`**, NOT the ALERTS KV: the free plan's KV allows 1,000
  writes a day for the whole account and every sign-in email spends three; D1
  allows 100,000 rows a day and a merge writes only changed rows. Rows are
  keyed by a hash of the inbox (`inboxOf`: lower-cased, and only Gmail's dots
  and +tags folded - elsewhere a +tag is a different address, so a catch-all
  box cannot read Alice's list), never the address; one row per (owner,
  role), saved iff `saved_at` > `removed_at`, the later of each wins.
  - **One writer at a time per person:** the merge reads the version and the
    rows in ONE batch (one snapshot), and its write batch starts with a
    compare-and-set: `INSERT INTO saved_cas (ok) VALUES (CASE WHEN version
    still = read THEN 1 END)` against a NOT NULL column, so a stale writer's
    batch fails whole and reads again; nothing is claimed or cleaned up, so
    nothing can be slipped into or left to jam later writes (a first lock
    did both: second review, 2026-11-18; the harness pauses a writer between
    read and write with AsyncLocalStorage while two others commit). Caps: 500
    saved (past it the newest that fit land and `capped` counts every role
    held back, including past-500 uploads), 600 KB body,
    `SAVED_WRITES_PER_DAY` (3,000) rows a person a day, `fromOurPage` on
    POST, tombstones pruned at 90 days, stamps or dates that are not ISO or
    are over a day ahead dropped (`_saved.stamp`). No DELETE: a one-shot
    delete does not stick while any browser holds a copy.
  - **A saved role keeps the posting's shapes** (`_saved.cleanSaved`):
    territory `{stated, states, region}` and office `{city, state}` are
    objects on every posting; links keep 1,000 characters.
  - **Whose copy is this - settled when a copy CHANGES HANDS, i.e. when the
    next person loads the board, never at Sign out** (third review,
    2026-11-18: setting things aside at Sign out hid them from the same
    person coming back, and ran before the person had confirmed). The page
    gets a hint, the `__Host-sj_owner` cookie from `functions/_middleware.js`
    on every page answer (200, the bare 304 a cached board comes back as,
    the gate's own pages; private, never weaker than the gate's no-store);
    the account `/api/saved` answers for is the authority. `claimAtLoad()`:
    the cookie names the copy's owner - drawn; names somebody else - the copy
    is set aside under its owner's tag (`park`, never deleted) and the
    cookie's person's own set-aside copy comes back (`unpark`); no cookie - a
    tagged copy is set aside until the answer (`WAITING_FOR`) and saves made
    meanwhile are tagged `pending`. On EVERY OK answer `adopt(owner)` sets
    aside a copy that is somebody else's, brings back the owner's set-aside
    copy if any (then sends it up), and puts this page's own changes
    (`JOURNAL`) on top. `park` and `unpark` MERGE (later-wins saves, both
    sets of applied marks and searches, the alert token), so a second
    set-aside never replaces the first. Sign out (`signOutHere`) only sends
    what is not up yet (no keepalive: bodies over 64 KB fail) and goes on; a
    plain link when there is nothing to send. An untagged copy, from before
    saves followed the sign-in, belongs to whoever signs in first. Clean
    means the server holds every role here.
  - **Nothing is lost in flight:** every change bumps `SAVE_GEN`; an answer
    that comes back after the page moved on sends again; a run of 409s backs
    off after 3.
  - **The alert subscription** is folded in ONCE per browser, and only when
    its `owner_tag` (now in the alerts settings read) is this account's - a
    token left by the last person on a shared browser is theirs. Stop on the
    alerts page deletes the subscription only (it is reached from every
    digest's one-click link); the page says the sign-in's list stays.
  - **The server's clock:** answers carry `now`; the board keeps the offset
    and stamps on it (`nowISO`).
  No `DB` bound: 501, and the board falls back to the alert sync exactly as
  before. `check_saved_roles_follow_the_sign_in` runs `scripts/
  saved_harness.mjs` (the real module on a D1 that IS SQLite, `node:sqlite`,
  sign-ins minted by `_session.js`) and `scripts/saved_board_harness.mjs`
  (the board's own code in a vm); the alerts check runs Stop against D1.
  Reviewed blind three times (three readers, then two, then two,
  2026-11-18); every finding fixed and given the reviewer's own scenario as
  a case; mutations 16/16, 16/17 (a duplicate guard) and 8/8. Setup:
  DEPLOY.md §3e.
- **Before the sign-in carried them, saved roles stayed local by default.**
  The alert sync below still carries them where no `DB` is bound. It is off
  until someone turns it on
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

## Which board a company's own site points to: check one of two (owner, 2026-11-18)

Owner, 2026-11-18: a program may wire a found board only when TWO separate
checks agree - the company's own website points to the board, AND the board
itself names that company as the employer. Parent boards, name mismatches
and anything doubtful go to "Boards we found". `scripts/find_site_boards.py`
is the FIRST check, with its evidence written down. It asks no board
anything, wires nothing and never writes `data/`: it writes one results
file, wherever `--out` says (an `--out` inside `data/` is refused), and the
wiring door reads it. The older finders (`find_embedded_ats`, `find_boards`,
`discover_ats`) could not be this check: they read whatever page was on
file, let the library follow redirects and threw the landing address away,
and never said where in a page a reference sat.

- **Run it.** `python3 scripts/find_site_boards.py --out /tmp/boards.json
  --limit 40` (plain requests); `--render` for one Chromium, two pages at a
  time (needs Playwright, imported only there); `--only id,id --show` for a
  person's look. The sweep of every html/unknown company (1,968 today) is
  `.github/workflows/board-sweep.yml`: by hand only, `contents: read`, its
  own concurrency group, no commit. Each machine's results leave as an
  artifact (kept a week; an artifact of a public repo is not private) and
  enter `data/` only through the door. `--shard K/N` splits by the SITE a
  website is on, so two records of one site go to one machine. The log
  carries counts only, a dead browser included (exit 3, no traceback);
  `--show` is for a terminal.
- **The results file** is the contract the door reads: `{generated, mode,
  companies: {id: {name, website, was, pages[], candidates[], off_site[],
  elsewhere[], left_site[], job_like_links, says_no_openings, error}}}`. A
  reference row is `{type, ref, element, saw, times, page, hop}`: `ref` is
  exactly what that type's fetcher takes (Workday `[tenant, pod, site]`),
  `element` is where it sat (href | iframe | script | text | request | frame
  | redirect), `saw` is at most 160 characters with any e-mail address
  taken out, `page` is where the read LANDED. Every address a record keeps
  (`saw`, pages, hops, elsewhere pages) has a person's name taken out of its
  query - a recruiter's, a contact's, a first, last or full name, Jobvite's
  sd= and __jvsd=, raw or percent-encoded inside another address - to the
  value's end: across spaces, entity spaces and an apostrophe between
  letters ('Pat O'Brien', eighth blind review) or beside a letter's
  percent-escape ("D'%C3%81vila", as the library sends D'Ávila), straight or
  curly, raw or as an entity (&#39;, &#x27;, &apos;, &rsquo;, &#8217;,
  &#x2019;) - and in every other spelling of one (blind review 9,
  2026-11-23): the left curly quote (&lsquo;, &#8216;, &#x2018;), the
  modifier letter apostrophe (&#700;, &#x2bc;), HTML5's &CloseCurlyQuote;,
  &OpenCurlyQuote; and &rsquor;, any of the numeric references without its
  ';' before a letter or a letter's escape ('&#39Brien', '&#x27Brien'), and each form
  escaped twice (&amp;#39;, &amp;#x27;, &amp;apos;, &amp;rsquo;,
  &amp;#8217;). A bare &amp; (and &#38;, &#x26;) is none of these and still
  ends the value - it is the next key's separator, so the account or ref
  after it survives - and a quote that ends an attribute still ends it.
  **Two shapes are left on purpose**: an apostrophe followed by a raw space
  ("O' Brien") and a value that opens with an apostrophe (last_name='Brien)
  each read the same as a quote that ends the attribute the address sits in
  ("...?last_name=Pat' title=..."), so the scrub stops there and the part
  after it stays. `generated` is the date of the
  file's OLDEST record: a run that picks a file up again keeps it.
- **A candidate** was seen on a page that landed on the company's own site,
  or is a redirect from an address of theirs that went straight to the
  board. Every other reference to a board is `off_site`: for a person, never
  for a program. A candidate is a claim about the PAGE, not the board:
  Prepared's own careers page links Axon's board.
- **"Their own site" is narrower than the host** (`Whose.of`: own | shared |
  part | other; only "own" makes a candidate). The host rule is still
  `page_belt._same_site`, and it decides which links the walk may follow.
  On top of it: a website on file with a path beyond a locale
  (faac.com/milo/) or a label in front of its site (security.gallagher.com)
  names a PART of a host, and a page outside that part is the larger
  site's; a page another record on file has the better claim to is that
  record's; a website two records give ("shared") is read, and its
  references go to off_site for both. Such a page is read once, filed under
  off_site with the reason in its note, and not walked on from. Today: 41
  path-bound companies, 19 that are a part of a host, 12 that share a
  website. This errs toward the queue on purpose: about.acme.com with its
  careers on acme.com is not told apart from product.parent.com.
- **It never says "no board".** A page robots.txt closes, a bot wall (shown
  to the plain request or to the browser), an error and a site that left
  are each written down as that; a company with no page of status `read`
  was not looked at. A refusal is not asked twice: not by the walk, not by
  the browser after the plain request, not by a resumed run. A resumed run
  looks again only where the look failed on OUR side (`unfinished`: it
  crashed, or did not finish).
- **Redirects.** Every address is walked one hop at a time through
  `ats.follow` (robots.txt at each), and a hop whose answer points at a job
  platform stops the walk: where it pointed is the finding, and the
  platform is not asked. It is the company's pointer only when every
  address on the way was theirs, or on the host it ended at (a Workday
  tenant's front door). Own site -> the parent's site -> the parent's board
  is off_site, recorded against the parent's address.
- **In a browser the same rules hold**, and the browser is the only mode the
  workflow runs. One route for the whole context (`_Session.rule`): every
  address the main frame goes to after its first, and every frame at one of
  the company's own addresses, is walked first exactly as above, so a
  script's redirect to a board is recorded without the board being opened;
  a second window is refused, nothing is submitted, pictures, video and
  fonts are not loaded, and a page may have eight addresses walked for it.
  Where the browser, and each frame, ENDED is asked of robots.txt before
  anything is kept. What a document that is not the company's own did
  inside the page (a frame inside another site's frame, what that frame
  asked for) is off_site, against that document's address. The limit that
  remains is render_fetch's: Playwright's router never sees a server
  redirect's second hop, so somebody else's frame that redirects to a
  closed host is loaded by the browser - and nothing of it is kept.
- **The clock.** One company gets four pages and 75 seconds (three and 150
  in a browser). `requests`' timeout is per socket read and bounds nothing,
  so each read is made in a daemon thread and `land()` is back by its
  deadline whatever the server does, hanging up on what was still arriving.
  Never `asyncio.to_thread` for anything that can wait on a server with no
  clock of its own: a pool's stuck thread is waited for when the run ends.
- **Patterns** are `find_boards.PATTERNS` and the slug entries of
  `discover_ats.MARKERS` by reference, plus what no table read right. A
  slug is spelt as the page spelt it wherever the platform tells spellings
  apart: SmartRecruiters does, and SO DOES LEVER (asked 2026-11-18:
  `Wayground` answers, `wayground` is a 404). "Lever slugs are lowercase",
  under Common tasks and in find_boards.py, is wrong for two of the 33 on
  file; whoever verifies a Lever ref may try the lowered form second, and
  must compare Lever refs without regard to case when asking whether a
  board is already held. Boards in a vendor's European region
  (job-boards.eu.greenhouse.io, jobs.eu.lever.co) are tallied under
  `elsewhere` as greenhouse-eu / lever-eu: the readers ask the American API
  only. A reference inside an HTML comment is not on the page, whatever
  kind of element was commented out. A SuccessFactors Career Site Builder
  board names no vendor in its address: it is known by its page's own markup,
  or by a link of a CSB shape on their own domain (see "SAP SuccessFactors
  career sites" below).
- **What the door must still do, because this file cannot:** make the
  second check (ask the board whose it is); refuse a (type, ref) another
  record already holds, or that two records' candidates share (two brands
  whose separate sites link one group board each get it here as a
  candidate); skip a company with a parent, an acquisition or a suspect on
  file, and the giants - the worklist does not, so that what their sites
  say is still there for a person; treat any `off_site` row, or two
  different boards among one company's candidates, as doubt.
- `find_embedded_ats.py` builds a Workday ref as [tenant, pod, site] (it
  had [tenant, SITE, pod], and found none for three months), reads the
  address a page's script asks (/wday/cxs/<tenant>/<site>), and no longer
  takes "wday" or a bare locale for a site. It can therefore put a Workday
  list ref into its output file for the first time: nothing in this work
  writes `data/embedded_ats.json`, and the desk's `boardUrlFor` has not
  been tried on one.
- Guards: `check_a_board_counts_only_when_their_own_site_points_to_it`
  (patterns, whose page, the plain walk, the list, a run),
  `check_the_board_finder_keeps_its_clock_and_its_manners` (the request,
  the deadline, the caps, redaction),
  `check_the_board_finder_in_a_browser_points_only_where_the_page_does`
  (the real `read_rendered` and the real sweep, driven by a stand-in for
  the part of Playwright they use; no browser runs in the suite), and
  `check_a_board_named_in_a_page_is_read_as_its_reader_reads_it`. Blind
  review of b646e97: 10 findings, all reproduced, all fixed (df53403); the
  first check alone let 21 of 43 small breakages through, the four now
  catch 81 of 81. Also run by hand in a real Chromium against a server on
  the same machine (nothing left it); one question was asked of the outside
  world, two requests to Lever's public feed.

## A found board is proven by its own stated name (owner, 2026-11-18)

The owner's rule for wiring a found board without him has two checks, and both must agree: the company's own website points to the board, and the board itself names that company as the employer. `scripts/verify_boards.py` is the second check and only the second. It wires nothing and writes nothing. (Its `main()` is the older audit and still writes `data/board_identity.json`; never run it for this.)

**`board_says(kind, ref)` asks the board whose it is.** Twenty-three types state their employer (`NAMES_ITS_EMPLOYER`). Fifteen from 2026-11-18: greenhouse, workable, jibe, breezy, recruitee, smartrecruiters and gem in the feed the reader already reads; ashby, lever, bamboohr, rippling, jazzhr, gusto, paylocity and icims on the board's own hosted page. Eight more since: hirebridge, applicantpro, teamtailor, pinpoint, zohorecruit, comeet and jobvite (2026-11-21), and successfactors (2026-11-22: the site's own title and description, and its postings' hiringOrganization; on a Unify site, since 2026-11-23, its postings' own titles; see "SAP SuccessFactors career sites" below). adp, workday, oracle and html have no probe, answer "no identity probe written for <kind>", and can never pass. It never raises. Beside `name`, `hosts`, `n` and `how` it returns:

- `names`: every distinct employer name the board states. `stated`: True only when the employer said it through the board itself AND over the whole board. A jibe site longer than the one page read keeps its names and is not `stated`.
- `site`: the employer's website where the board gives one (ashby, jazzhr).
- `failed`: None, or why the board was not read. `refused` (robots.txt, 401, 403, 429) is never asked again in the same run. `gone` is 404 or 410. `unreadable` is anything else, and `how` says what. `how` gives the status and the address in words.
- `n_is_partial`: True on icims, where `n` is the first page only. Size a board from the reader (`add_company.verify`), never from `n`.
- `kind`: the type that was asked.
- `address` (paylocity only, where its page gives it): the board's own /jobs/All/<uuid> address, from og:url, stated only when the page's LeadJoinUrl, if it gives one, is exactly /Recruiting/PublicLeads/New/<that uuid> (a LeadJoinUrl in another shape, another uuid with a digit more included, is disagreement: test review, 2026-11-22). An address, never a name; the wiring door keys a board found by /List/ by it (fifth blind review, 2026-11-22).

Never read as a name, because each sits beside the name and is something else: the slug, a Paylocity address tail, Breezy's friendly_id, SmartRecruiters' identifier, a board's free-text title, a vendor's own page title. No probe looks at the ref for a name, so none can echo what it was asked.

**`proven(company, said)` is the only test a board may be wired on unattended.** It returns `{"ok", "why", "name"}`. ok only when `stated` is True, there is at least one name, and EVERY name equals the company's name or an `also_known_as` once these are set aside: case, punctuation, a leading "the", a legal form at the end (inc, incorporated, llc, ltd, limited, corp, corporation, co, company, gmbh, plc, lp, llp, pty, sa, ag, bv) and one trailing country (US, USA, United States, North America, Americas, Canada, UK). Never a substring, prefix, slug or host. parent, acquired, brands and board_owner are not read. Four refusals go beyond the name, each from the blind review of 2026-11-18:

- A country on OUR name must be on the board's too. "Esri Canada" is not proven by a board calling itself "Esri": that is the group's board. The other way round passes ("Acusensus United States" for Acusensus). All 17 names on file that end in a country passed on the bare name before this.
- The board's own names may not carry two different countries. "Acme US" and "Acme Canada" on one board is a group's board.
- "US" is a country only in capitals. "Unite Us" is a company.
- Where the board gives a website and one is on file, they must be the same site (the same host, or one under the other). Two companies are called Concourse; the name cannot tell them apart and the website can. A refusal only: a matching website never passes a name that is not theirs.

A refusal means "a person has to look", and `why` quotes both names (and both websites where it turned on them). When the owner says a refused name is theirs, it goes in `also_known_as` and passes from then on. Expect roughly a fifth of right boards to be refused on the name alone: Flock Safety's board says "Flock", Circuit's says "Circuit Transit".

**`judge()` is the desk's older test and is not the gate.** On greenhouse, workable and jibe it is as forgiving as it always was: one name inside the other, no floor. On the twelve types it could not read a name on until 2026-11-18 it says `matches` only when the strict test passes, `CHECK` when the name is theirs and the board gives another website, `unknown` when the names only share ground, and MISMATCH when they share nothing. So the page belt's Add board, `find_embedded_ats` and `wire_embedded.triage` are no looser than they were. The first version let the substring test loose on the new names, and the belt wired a Lever board calling itself "Jacobs" to "StreetLight Data (Jacobs)".

KNOWN, AND LEFT AS FOUND: on greenhouse, workable and jibe the substring test still passes a parent. "Jacobs" is `matches` there and was before; 4 of the 65 stored audit matches are containment only. Closing it is one condition in `judge()`, and it changes what the owner's doors do on types this work did not touch, so it is his to rule.

**The audit** (`verify_boards.audit`, which `main()` loops over) still asks an ashby or lever board whose name matches what its job ads talk about (LOOK), unless the board gives the website on file. That look is the only thing that ever noticed Concourse.

**Manners, pinned by selftest on every read.**
- A feed goes through `ats._get`. A page goes through `_board_page`: `ats.follow`, robots.txt at every hop.
- A hop that leaves the board's HOST ends the read. The same host, not the same domain: BambooHR's marketing site is www.bamboohr.com.
- Lever's page is read as a stream for its first 64 KB only. Any other page is cut at 2 MB before a pattern runs.
- Nothing is asked twice.
- A full-address iCIMS ref must be on icims.com.
- A pattern over a page reads it once (`_inside`, and `[^<>]` rather than `[^>]`). The first versions took 77 seconds on 300 KB of unclosed `<h1>`.

Selftests: `check_a_board_is_proven_only_by_its_own_stated_name` (the probes, the manners, the gate) and `check_a_stated_name_does_not_loosen_the_older_doors` (judge, the belt, triage, the audit). A new ATS type gets a probe measured on live boards and a row in the first check's BOARDS before any company may be proven on it.

## Wiring a found board: two checks agree, or it goes to the owner (owner, 2026-11-18)

Owner, on coverage (the crawl read postings from 563 of 2,442 companies): Claude
may wire a found board ONLY when two separate checks agree. CHECK 1: the
company's own website points to the board. CHECK 2: the board itself names that
company as the employer. A parent's board, a name mismatch and anything doubtful
go to his "Boards we found" queue. Each verified batch is written without waiting
for him and he is told after. Giants (about 500+ open roles) are skipped this
round.

`scripts/wire_found.py` is that door, and the only one a batch goes through:

    python3 scripts/wire_found.py plan --results <finder results>.json --out <scratch>/worklist.json
    python3 scripts/wire_found.py plan --results ... --doubts doubts.json --by agent:<label> --out <scratch>/worklist-2.json
    python3 scripts/wire_found.py apply <scratch>/worklist.json --by agent:<label>            # a look
    python3 scripts/wire_found.py apply <scratch>/worklist.json --by agent:<label> --write --expect <code>
    python3 scripts/wire_found.py settle --id <company> --board <type>/<ref> --why "<the owner's words>" --by agent:<label>   # only on his word; a look
    python3 scripts/wire_found.py settle --id <company> --board <type>/<ref> [--key <doubt key> ...] --why "..." --by agent:<label> --write --expect <code>

- **`plan` is the only step on the network, and it freezes a worklist.** It
  reads `find_site_boards.py` results (several files are pooled: a plain read
  and a rendered one of the same sites) and asks each board at most three
  things, once per spelling and never again after a refusal: its own total
  where the reader does not read the whole board (`add_company.board_total`),
  the live read (`add_company.read_board`), and who it says it is
  (`verify_boards.board_says`, judged by `verify_boards.proven`; with no
  `proven` in the checkout it refuses to start). The worklist's name is taken
  before the first request, never inside data/, written as ASCII, and never
  overwritten. A company a person already answered about (out of scope, a
  refusal, a board already set) is not asked of anybody.
- **WIRE needs all of it.** The reference sits on a page of the website on
  file NOW, as a link, embed, script or request, or as a redirect the finder
  followed from an address on their site to the board (the asked address is
  what is quoted). `proven` says ok AND the board stated the name itself: a
  pass with no stated name is held, which is how Concourse got in. The live
  read has postings on it, at least as many as the public board already shows
  for them. And nothing on file argues.
- **`html` does not mean unread.** On 2026-11-18, 114 of those pages put
  1,357 postings on the board every night. A board that reads nothing, or
  fewer than `open_postings` in board.json, is held: wiring it takes roles
  off, and an empty account left behind still states the right name.
- **HOLD is every other board that reads, with its doubt in words:** the page
  is not on their website; the site only names the board in plain text; two
  boards on one site, or one board spelled two ways; a reference beside it
  that no reader takes; a job platform nothing here reads named on their own
  site (City Detect's shape); a parent, an acquisition, a board owner or an
  `ats_note` on file (wiring would write over the note); a discovery suspect
  or an Acquisitions row (both builders are asked); another company already
  holding the board, or the same board found for two companies in one run; a
  different board refused earlier or already waiting (a proposal's own words
  quoted); this very board
  pending in Agent proposals for the company itself, its words quoted (it was
  "the question already asked", and the two checks answered it while the
  owner's question stood: blind review, 2026-11-21) - a waiting row is met
  under every key the board is known by, as holders and refusals are (its
  Paylocity tenant's name, the /All/ address its page states, a Workable
  number the file ties to its slug), and one met under the other address is
  said as "this board (as <its address there>)", never "a different board"
  (fifth blind review, 2026-11-22); this board refused for
  another company still on file (the refusal and whose it was quoted, below);
  a board that names somebody else (MISMATCH, both names quoted) or nobody.
- **An advertisement is not a second board, and a question that did not get
  through is not a doubt** (the first real run, 2026-11-18: 38 companies, 9
  to wire, 15 held, and three of the 15 were neither). A LINK to (or a
  mention of) the company's Indeed or LinkedIn page (`ADVERTISED_ON`, seen as
  `href` or `text`), and a LINK to the own site of the very platform the
  board is on (`VENDOR_SITES`, seen as `href`: "Powered by JazzHR" under an
  embedded JazzHR board, filed as `other:jazzhr.com`), are kept on the row
  as `advertised_on` and hold nothing: Anaplan's and Freebee's boards were
  held on exactly those. Everything else holds (second blind review,
  2026-11-19): an aggregator the page embeds, scripts, requests or redirects
  to (it shows where they hire today); Y Combinator and Wellfound (hiring
  channels, not copies); a vendor host seen as a frame (a Workable posting
  framed by short code is any account's); `other:<any other site>` (a
  careers link to a host nothing knows is what a parent's careers site
  looks like). Each is said for what it is, on their own site (a doubt) and
  on a page off it (a note on a WIRE line), with the forms read from
  `find_site_boards` (`_NO_BOARD_IN_IT`, `TENANT_FORMS`), never listed here:
  a form of a platform we read that names no board ("loads jobvite in a form
  that names no board"), a tenant of one this door cannot name, or a
  platform the crawl cannot read - the WIRE line's note said that last of
  every one, Jobvite's own form included, and the finder's run summary
  counted them all under "no reader for" (third review's Jobvite reviewer,
  2026-11-21); the summary now counts "forms of platforms we read" apart.
  `VENDOR_SITES` lists only vendors whose boards sit under a
  path on their board host - never a domain that gives each tenant a careers
  site of its own (jibeapply.com, applytojob.com, bamboohr.com, breezy.hr,
  recruitee.com, icims.com, workable.com: a link there can be another
  tenant's, a parent's) - and none for adp, oracle or workday. A board that
  read and whose identity probe failed ON THE DAY is SKIP `unreadable` and
  asked again by the next plan - decided BY CAUSE, by
  `verify_boards.board_says`' `transient` (True only for a network error, a
  timeout, an HTTP 5xx, an HTTP 429, or a robots.txt that did not answer),
  never by `failed`'s label. A page that leads off to another host, an
  answer in a shape nobody knows, a non-JSON feed, another 4xx, a page that
  refused or is gone is held for the owner with the reason in words: it
  comes back every day, and skipped it never reached him.
- **A Workable board a site asks for by number is wired by its slug, or not
  wired.** A page that embeds Workable's widget asks for the board by the
  account's NUMBER (`.../widget/accounts/710855`), and a rendered read sees
  nothing else. The number reads (it is the address `fetch_workable` asks)
  and passes both checks, and it does not open:
  `apply.workable.com/710855` is Workable's error page, and
  `build_board.board_url` builds the public "go and look" link from the ref.
  The first real batch carried Archistar that way and a blind reviewer
  opened the link before it was written (2026-11-19). `named_by_number`
  follows ONE posting to where it lands (`add_company.lands_at`: robots.txt
  at each hop, no page read, only a 200 is a landing, a 429 honoured at the
  host gate), reads the slug off the landing address, and holds the slug to
  the number: the board read under the slug must list exactly the postings
  the number listed. A number is tested as the reader reads it (`710855/` is
  a number). It asks nothing about a company somebody already answered
  about - out of scope, on a board, or with a refusal in EITHER store - and
  stops at a number whose own board holds 500 rows (no follow, no slug
  read; SKIP giant). A resolved board is ONE board under TWO keys, the
  slug's and the number's (`board_keys`, `row_keys`): holders, twins,
  refusals and waiting rows meet it under either, in the plan and in
  `apply`; a company the pre-pass skips keeps the number's key (and gains
  the slug's when the same number was resolved for another company), and a
  record on file holding a number resolved in the run holds the slug too
  (in `apply` through the worklist's own `workable_numbers`), as does one
  whose number and slug are said together on file (`workable_pairs`, below):
  the door counts it among the slug's holders, in the plan and in apply,
  and `held_boards` holds the slug under its key (code `number`) for
  discovery and wire_embedded. Skipped as "the slug's holder" with nothing
  putting it there, it held nothing, and a duplicate record linking the slug
  was wired to the same account (blind review, 2026-11-21). A board the
  owner holds under a number (a doubt, a door-filed row, a refusal) is held
  under the slug's key too where the run resolved the number. A run's slug
  is ADDED to a pair said on file, never put in its place
  (`board_proposals.number_slugs`): where the two say different slugs, the
  holds and the holders under the number are held under each, in the plan
  and in apply (review 10, 2026-11-23: apply took the worklist's
  `workable_numbers` first, so an entry edited to open 7300 under another
  slug moved the owner's queued row, his refusal and a record on the number
  off the slug the file pairs it with, and a row flipped to WIRE beside them
  was wired, the look a plain WIRE line). Where the run did
  not resolve the number - and for a record on file holding a number nobody in the run
  followed - a Workable board is held when it STATES one of the names that
  account goes by (the held row's `board_calls_itself` or company, the
  doubt's company, the record's name or also_known_as; `verify_boards.strict`),
  or is found for a company that GOES BY one of them (its name or an
  also_known_as, the names discovery and wire_embedded ask by: a doubt about
  "Archi", merged into Archistar, held the survivor's own board in those two
  doors and not in this one, blind review of 2026-11-21), in the plan and in
  apply: "a record holds Workable account N (X), and whether it is this board
  was not asked ... this board states "X"" (or "the company it was found for
  goes by "X""). No
  longer every Workable board (decided after the review of 27eeb628). A refused
  company's number is not followed (marked `declined`) and the company is
  SKIP refused, whatever it refused - "its slug was not looked for ... tell
  the owner" - with nothing read, probed or filed under the number; and a
  board refused under a number this run did not follow is taken to be any
  Workable board found for that company now (SKIP refused, nothing read).
  "Could not be found from it" is said only of a follow that was made and
  answered; a giant's number and one that did not read say why nothing was
  followed. A follow, or a read under
  the slug, that failed ON THE DAY is SKIP `unreadable` and asked again; a
  robots.txt disallow, a 404, a landing on no board and the rest are held
  with the reason and never wired, by the plan or by `apply`. The wired
  record's `ats_note`, and a held row's `identity_why`, say "asks for it as
  Workable account N; its postings open under apply.workable.com/<slug>" -
  only of a Workable board asked for by a number in digits
  (`wire_found.account_words`): every door reads them back as a pair on
  file, and an edited worklist once put them on a greenhouse record (review
  9, 2026-11-23). `apply` leans on a WIRE row's `account` only where
  `wire_found.account_tie` shows it - a Workable row, the number in digits,
  opened under that very slug by the plan's own `workable_numbers` or by a
  pair on file - and prints it under the row ("leans on its account number:
  ..."); any other `account` leaves the row alone (`evidence`). That is
  enough to HOLD by (holders, holds and twins meet the board under the
  number too). The account goes into the record's note only where
  `wire_found.account_noted` shows more: a pair on file already opening
  that number under this slug, or check 1's own sighting asking for the
  board by that very number (`check_1.saw` carries
  apply.workable.com/api/v1/widget/accounts/N, `account_sighted`: the wire
  rests on it) with the plan's `workable_numbers` opening it under this slug
  and nothing on file opening it under another - always of the row's own
  board, which the record then holds. The account CLEARS NOTHING: a row
  waiting for the company under the number is taken for this very board
  only where a pair on file opens that number under this slug, never on the
  worklist's `workable_numbers`, the row's `account` or check 1's sighting,
  each of which an edit writes (review 10, 2026-11-23: the repair of review
  9 took such a row on the three, and with `check_1.saw` edited too the
  company's own unruled row was waived, dropped off his queue, and the note
  planted the pair). So the plan's own follow passes the company's own
  unruled row under the number and apply leaves the WIRE row alone ("... is
  the Workable account number the row says their site asks for the board
  by, and nothing on file opens that number under ...; ... his to settle -
  tell the owner"). The look says which, printing the sighting and that the
  note says it, or "for its holds alone" (repair of review 9, 2026-11-23: an
  edit giving the row the waiting row's number as its `account` and tying it
  to the find's slug in `workable_numbers` waived the company's own unruled
  row - it dropped off his queue - and the note planted the pair); and under
  each WIRE row it names every row waiting on the owner the wire takes off
  his queue ("takes <board>, waiting in <store> for it, as this very board"),
  which the code covers. A held row's doubt is filed with those words only
  of its own board's number where `account_noted` shows it, or of a pair the file
  already says (`wire_found.held_words`); any other such words in the
  worklist's doubt are taken out (and taken out again where taking them out
  spells them anew), and the look prints each pair a held row files with
  ("files with it, as a pair every door after reads: ...") and the words it
  is filed without (the same repair: an edit appended them to a greenhouse
  row's doubt, the write filed them, and the next plan, unedited, wired past
  another company's own unruled row under the number by that pair).
  `board_url` and the desk's `boardUrlFor` link the filed page or the
  company's website for a number, never `apply.workable.com/<number>`; the
  desk's Accept and its Set board both refuse a numbered Workable ref
  (`710855/` and ` 710855 ` included: its slug is needed).
- **A board is sized by its openings, not its rows.** `fetch_rippling`
  returns the logo's file name as a row on every board, and a talent pool or
  an unfilled template is a row too. The door counts what
  `roles.not_a_listing` lets through - the question every path onto the
  public board asks - so an empty Rippling board does not read as one
  posting and walk past the hold for a board nobody is hiring through. In
  `apply` a WIRE row's count is held to its titles: the first eight of its
  openings, each one an opening, or the row is left alone (`evidence`).
- **A plan is read before it is written, and a reader's doubt holds the
  board.** Independent readers try to show each WIRE row is somebody else's
  board (two lenses: whose board is it, and does their own site point to
  it); 20 rows or fewer are read whole, a larger batch by the owner's
  zero-defect draw of 58 (2026-11-01). A WRONG row sends the batch back and
  the door is fixed first. A row a reader could not settle goes to the
  owner in the reader's words: `plan --doubts doubts.json --by
  agent:<label>` ({company id: the doubt}) holds it however well it passes
  both checks, and files it with the other held rows. A doubts file that
  names a company the results do not hold, or is not that shape, or comes
  without a `--by` that is an agent's handle, stops the plan before its
  first read. The first batch: JustFOIA's own board, whose ads open
  "JustFOIA, an MCCi brand" - whether a parent's brand is wired on its own
  board is his call.
  **And it stays his, from the moment it is given, until he settles it.**
  `plan --doubts` keeps every doubt it is given - HOLD, SKIP or no board -
  in `data/board_doubts.json` BEFORE its first request, under the desk lock:
  one journalled ADDITION under the `--by` label, words redacted and whole,
  one record per doubt (`{"<company id>@<when>": {id, name, board, type,
  ref, why, by, on, at}}`; the first shape `{company id: {...}}` is still
  read). The same words about the same board for the same company are never
  written twice, ruled on since or not (the board by its key; a Workable
  number and a slug taken for one account only where the file ties the two,
  `workable_pairs` - the doubts are kept before the first request, so
  nothing the run follows can. Any number beside any slug was once taken
  for one account: a doubt about another account in the words of one he
  had settled was not kept, the plan said it was settled on his word, and
  the next plan wired the board; blind review, 2026-11-21). Where the company has a row
  waiting in Boards we found, the words go onto it (`board_proposals.add_doubt`,
  stamped `updated`, which makes the card print `identity_why`), naming the
  board - "a reader's doubt about greenhouse/xco, not the board on this card
  (lever/xold)" - in entries of at most a third of the file
  (`runaway_share`), a count over 25 printed BEFORE the write that passes
  force; a refused row write stops the plan before its first request. Apply
  writes no doubt, meets every doubt kept since its plan as `standing` (the
  old code no longer opens the write), and STOPS a worklist whose reader
  doubts are no longer on file. A doubts file that does not read, or holds a
  record without words, stops plan and apply.
  **The owner settles a doubt, and nothing the door keeps does** (decided
  after the review of 27eeb628: his desk is never blocked by the door's
  bookkeeping). ANY ruling of his on the company in Boards we found later
  than the doubt settles it (by the stamps, else a strictly later date),
  whichever row or card he ruled from, as on main; refused is then SKIP
  refused, accepted means the checks decide. A doubt no ruling can reach - a
  company he ruled on before, one on a board, one not on file (it holds its
  board for any company), one refused in Agent proposals for that board, one
  with no row yet - stands, and the plan, the look and the journal say so
  and that he must be told (`reach`); only a row on his desk is promised his
  ruling, and a row filed after a doubt says "his ruling on this row in
  Boards we found settles it" of itself. "Wired before the reader's doubt
  was kept" is said only of a doubt the same run kept. On his word,
  `settle` adds one journalled record (`{"<id>@<when>~settled": {id, board,
  settles: [keys], why, by, on, at}}`) that settles the company's standing
  doubts ABOUT THE ONE BOARD his words are about (`--board`, as the plan
  prints it, compared by `same_board`, a Workable number and the slug the
  file ties it to being one; `-` names the doubts that name no board) for
  every reader (plan, apply, `held_for_the_owner`); admin_undo.py takes it
  back. Where more than one doubt stands about that board, each one his
  words answer is named by `--key` (the look lists every doubt's key), and
  one he was not asked about is never settled by his answer to another.
  Without `--write` it is a look: it lists the doubts it would settle and
  every other standing doubt of the company it leaves standing, and prints a
  code, which `--write --expect` must quote, as in apply (the code covers
  the company, the board, his words and exactly the doubts it settles).
  It refuses a doubt whose company has its row on his desk (reach `row`):
  his own ruling on that card settles it. (Blind review, 2026-11-21: `settle
  --id` settled every standing doubt of the company - one about a board
  nobody had shown him, which the next plan wired, and one behind the card
  he settles it from. The third, the same day: it settled every doubt about
  the board, a later reader's in other words included, and a doubt about a
  Workable number the file ties to the slug named was "about another
  board".) A settled doubt given again is not kept again, never called
  standing, and tells nobody anything. A board a door FILED in his queue
  (`filed`) is held by every later plan too, with its doubt; a row an older
  finder left may still be answered by the two checks. A SKIP made after
  the doubts are gathered carries them. No sentence says "asked again on the
  next plan" unless it is; the plan's last lines say nothing is wired or
  filed until the worklist is applied with --write.
- **A board the owner holds is held under any company's id** - current or
  merged away - whatever it is held by: the board of a standing doubt, of
  an unruled row a door filed in Boards we found, or of a board proposal
  still pending in Agent proposals (`waiting`, blind review of 2026-11-21),
  is HOLD with the words quoted, and a board refused in either store (a
  Boards-we-found refusal by the board on its row, an Agent-proposals
  rejection by its ats_type/ats_ref)
  for THIS company under another record is SKIP refused, "tell the owner",
  with nothing read - for whatever company's site names it, under either
  Workable key, in the plan and in apply (`held_boards`,
  `wire_found.refusal_tie`; apply's codes `standing`, `queued`,
  `refused`). A SKIP never reaches him again, so a refusal is this
  company's ONLY WHERE THE FILE SHOWS IT, and the SKIP line says what shows
  it ("the refusal is taken for this company's because ..."): a record on
  file that goes by one name with this one (one name, or an also_known_as
  either way carrying the other's) AND a second sign - one website, the
  same address, host and path (a brand at bigco.test/brand/ or
  brand.bigco.test is on a website of its own: sixth blind review,
  2026-11-22), or, where a website is missing so nothing on file tells them
  apart, one group in the Duplicates queue for this very pair
  (`wire_found.duplicate_groups`; the line says that dismissing the pair
  there puts the board in front of him) - and never a pair he DISMISSED
  there, whatever their websites (`wire_found.dismissed_pair`, read in every
  case: the dismissal is said on the row filed for him); or
  a record no longer on file whose refusal recorded a name this company
  goes by (its survivor: the desk's merge puts the dropped name in
  also_known_as), with no other record on file going by it and the address
  the refusal was recorded against (`page`: the row's `filed.page`, the
  proposal's `evidence`) on no other record's website - or at or under this
  company's own. Two websites that differ are two companies whatever the
  names say: an alias alone made a parent's refusal of its brand's board
  SKIP the brand's own board unread (RapidSOS goes by IamResponding, and
  I Am Responding is a record of its own), and the Duplicates queue groups
  every pair of one name, so it is no second sign for two Concourses on two
  websites (fifth blind review, 2026-11-22). A refusal that recorded NO
  NAME is never taken for this company's - decided on purpose: one website
  holds a parent and its parts, so a page on this company's site does not
  show whose refusal it was; every refusal on file today records one.
  Names are compared as
  `verify_boards.strict` leaves them, country tag included
  (`wire_found.names_meet`): "Esri" is not "Esri Canada". Refused for any
  OTHER company, on file or merged away, or for one the file does not show
  to be this one, it is not this one's answer: the board is read and HELD,
  and filed in Boards we found for
  the company whose own site and board both name it, the refusal and whose
  it was quoted in words that name both records and what ties or parts
  them ("... was refused for Sub Co in Agent proposals (...) -
  refused for another company, still on file (Sub Co)", "... (Big Co): the
  also_known_as of Big Co (big-co) carries "Brand Co", the name of Brand Co
  (brand-co), but they are on different websites (...)", "- refused for a
  record since merged away (Sub Co), and this company does not go by that
  name", "- refused for a record no longer on file (acme-old), and the
  refusal recorded no name, so whose it was cannot be shown"; "whether it is
  Parent Co's own board is the owner's to rule on"),
  the refusal in its own words - a row that proposes the board never says
  it "is not proposed again", whether it is held by key or by a name a
  Workable account goes by; apply leaves such a WIRE row alone and says to
  plan again. A desk refusal's `whose` is the row's own name, never the
  name the board called itself (that is the board's claim, often its true
  owner's). (Blind review,
  2026-11-21: Sub Co's refusal, "the board is Parent Co's", held Parent Co's
  own board in all three doors, and nothing could put it in front of him for
  Parent Co; 10 of the 11 board proposals he had rejected name their true
  owner. The third blind review, the same day: every refusal under a record
  merged away was still taken for this company's, so a desk merge of Sub Co
  put Parent Co's board out of his reach again; and two records both named
  Acme Co were "another company", so his answer for one came back on his
  desk for the other. Discovery and wire_embedded.py, which never wire past
  him, still hold it.) A
  doubted or refused company merged into its duplicate left its hold under
  the dropped id, and the survivor's plan wired the board (third review and
  review of 27eeb628, 2026-11-19). The desk's merge (`admin.act_merge`) is NOT refused for it,
  as on main: the door holds by board instead, and so do discovery and
  wire_embedded.py (below), through the same reader
  (`board_proposals.held_boards`), so the three doors cannot drift. A
  Workable number and slug are one board wherever the two are known to
  belong together: followed by the run, or said on file in the door's own
  words ("asks for it as Workable account N; its postings open under
  apply.workable.com/<slug>", `board_proposals.workable_pairs`).
- **SKIP is listed, never silent:** a giant; `size unknown`; a board that did
  not read (kept with its error, not retried); a site that named only a
  reference no reader takes (a Workday ref that is not `[tenant, wdN, site]`
  is refused before any request); the same board refused before, or any
  refusal in the desk's own store ("tell the owner": that queue never shows a
  ruled company again). Only "no board seen on their site" is counted
  without being listed.
- **A reader that filters cannot show a board is small.** The Workday reader
  asks two sales searches and the Oracle reader takes one reply
  (`wire_found.PARTIAL`). Measured 2026-11-18: Motorola Solutions' board holds
  868 postings by its own count on a day the reader returned 385 rows, and no
  Workday board on file reads as 500. So for those the board's own total is
  asked FIRST, in one request (`ats.workday_total`: `total` on the first reply
  to an empty search), a giant is never read at all, and a board whose total
  cannot be had is skipped with the giants. Oracle has no way to ask, so every
  Oracle find is skipped. A count from the identity probe skips a board when
  it is 500 or more and is never read as "small".
- **`board_key` is loose and `spelling` is exact.** The key only notices
  collisions, and a collision only ever sends a board to a person: case is
  ignored, an address matches with or without scheme, `www.` or a last slash,
  a Paylocity board is the id after `/jobs/All/` or `/jobs/List/`, with or
  without the tenant's name after it (a /List/ board had two keys, so a hold
  under one spelling missed the other: blind review, 2026-11-21), and is
  known by the tenant's name both shapes end in as well
  (`board_proposals.also_keys`, asked wherever boards meet: FAAC's frame is
  /List/2115/FAAC-Incorporated and lands on /All/<uuid>/FAAC-Incorporated,
  one board under two ids; every Paylocity ref on file is /All/, and a /List/
  find walked past a refusal of the board - third blind review). The tenant's
  name is not always there: Celona's record reads .../All/<uuid> bare (1 of
  the 41 on file) and find_site_boards writes /List/<n> bare too, so a
  /List/<n>/Celona-Inc find met nothing of it and was wired to a second
  record (fifth blind review, 2026-11-22). So `plan` first asks a board
  their site names without its /All/ id or its tenant's name for the /All/
  address its own page states (`wire_found.addressed_by_page`: the identity
  probe's `address`, asked once, after the board's live read as consider
  asks them, and kept for both; asked of every board consider would read
  and no other - never of a board that does not read or is a giant, of a
  company out of scope or on a board, or of a board consider skips unread
  as refused (`wire_found.refused_first`, the one question both ask: it
  skipped every company with ANY refusal, and a board refused for the
  company itself at the address its own page states came back to his card;
  now it is SKIP refused, "this board was refused") - and `page_said` says
  what a page that stated nothing said). The board is known by that address
  everywhere after, and so is what the door WRITES of it: `page_ref` on the
  row for apply, on the row it files in Boards we found next to `found`
  (file_found keeps it; refusals, held_boards and the waiting rows read it),
  and in the note of a record it wires ("its own page states its address is
  ...", read back by `board_proposals.stated_addresses`, as is the
  `page_ref` of a row the owner accepted at the desk) - a record wired at a
  bare /List/ address was compared with nothing, and every later /All/ find
  of any board was held (sixth blind review, 2026-11-22). Paylocity boards
  meet BY EVERY ADDRESS ON BOTH SIDES, not by keys
  (`board_proposals.paylocity_relation`: same, tenant, unknown, apart) -
  records on file, the owner's holds under any id, this company's own
  refusals, its waiting rows, this run's other finds and, in apply, the
  WIRE rows among themselves (a bare /List/2115 hold and the same board's
  /All/<uuid>/<Tenant> share no key, and the plan wired past an unruled
  row, his refusal of it and a doubt; two companies naming one board by its
  two ids were both wired). "Same" is the same board; "unknown" (no id of
  one kind on both, a tenant's name missing) and "tenant" (one tenant's
  name after two ids of one kind that differ - two boards of one tenant,
  never "the board refused": sixth blind review, 2026-11-22) hold the board
  for him, with both addresses in the words; a record whose ref carries no
  /All/ or /List/ id is no address to compare. A SKIP rests only on an id
  that agrees (`board_proposals.paylocity_ids_agree`): the tenant's name
  after a /List/ id and after an /All/ id is "same" for holding and never
  "the board refused", so such a board is read, its page asked for its /All/
  id, and SKIPped only where that id is the refused one ("this board was
  refused"), held otherwise (`kinds` in the words) - by /List/ it was
  SKIPped unread where by its /All/ address it was held (seventh blind
  review, 2026-11-22). So does a waiver: a row waiting for the company
  itself (an older finder's row, a door-filed row, its own pending
  proposal) is "this board, the question already asked" only where an id
  agrees; tied by the tenant's name alone it is held, the name said as all
  that ties them - in the plan, and in apply whatever `waiting_seen` says
  (eighth blind review, 2026-11-22: the find was wired and his unruled row
  dropped off the queue). A record with no /All/ id that a find of the run
  cannot be compared with - a bare /List/ board accepted at the desk or set
  by hand held every later /All/ find, for every company - has its own
  board's page asked once for its /All/ address (`wire_found.records_by_page`,
  through the probe and its manners, once per spelling) - for a find consider
  compares and no other (`wire_found.compared`, the pre-pass's own filter: a
  company in scope, waiting, its first board, not skipped as refused), and
  never the board of a record ruled out of scope, which is said as not asked
  (eighth blind review, 2026-11-22: a record's page was asked for finds
  nothing compared) - and the worklist carries the answer to
  apply: `page_addresses` records what each page the plan asked stated, by
  the board's exact spelling, and a WIRE row compared by a record's names it
  (`leans_on`); apply leans on such an address only for the row that names
  it, only where both say it, and while the record still reads that board,
  and the look prints every address it leans on, the row's own `page_ref`
  included - a `page_ref` the plan did not record leaves the row alone
  (`evidence`) and is never filed on a held row (eighth blind review,
  2026-11-22: `record_addresses` and a row's `page_ref` were taken on the
  worklist's word, and an added address cleared a record and a proposal);
  where that page states nothing, the row names the record and says that
  putting its board on file by its whole /jobs/All/<uuid>/<Tenant> address
  clears it. What the page said is kept as `wire_found.page_answer` words: a
  page that fails ON THE DAY (a 5xx, a 429, a timeout) SKIPs the find only it
  could compare - asked again by the next plan, never filed (eighth blind
  review, 2026-11-22: a 503 was filed in his queue and held every later
  plan) - and one that cannot be read, or states its address in a form the
  door does not keep, is said as that, never "did not state", and the
  latter's address is never carried. A HOLD of the owner's with no /All/
  id (a refusal, a reader's doubt, a door-filed row or a pending proposal at
  a bare /List/ address) is asked the same way (`wire_found.holds_by_page`:
  once per spelling, for a find consider compares, never for the hold's own
  company's find, and never the hold of a company a person ruled out of
  scope, which is not probed at all, as its record is not - the row says
  the held board's page was not asked) - one refused at the desk held every /All/ board found,
  for every company, in all three doors, forever (eighth blind review,
  2026-11-22). What the page states, asked for one hold, is taken for every
  hold of that spelling, a ruled-out company's included (that asks nothing
  more), and a ruled-out hold met after it never writes "not asked" over it
  (review 9, 2026-11-23: the ruled-out company's hold of the very board kept
  holding a find the page showed to be another board, "cannot be shown").
  Its page's address shows which board the hold is, and the
  hold is never dropped: a find is compared by it (the WIRE row names it in
  `leans_on`, so apply compares by it too), a page that fails on the day
  SKIPs the find, and where the page states nothing the row says so and
  how the hold clears (every plan that finds such a board asks again; his
  ruling on the row settles that board). Discovery and wire_embedded ask no
  page: `held_board` holds such a board there as before. The probe and the door keep one grammar for a stated /All/
  address (a tenant's name may carry , ; ( ): the door refused what the
  probe stated, and the row said the page "did not state" it), a note's
  address ends at "; ", and a filed row's `page_ref` reaches its waiting
  row too. Ids that agree on one kind and differ on the other hold the
  board, said as that for a hold and for a record on file alike
  (`wire_found.record_relation`). Two tenants' names are compared in one
  form, the finder's (`board_proposals.paylocity_tenant_form`: cut at the
  first character other than a letter, a digit, _, . or -, a last "."
  stripped): names that are not one spelling but alike in that form, or the
  one the start of the other, are "unknown" and hold the board, never
  "apart" (`paylocity_near_names`; "spelt" beside a record; a name the
  finder cannot write at all, "(US)-Acme", is the start of nothing, or one
  such record would hold every board) - the finder's
  /List/2140/Acme- and a page's /All/<uuid>/Acme-(US),-Inc were two
  strings, and the door wired past a filed row and a record of the board
  (eighth blind review, 2026-11-22); a record known by its whole address is
  never told to be put on file by it. The key of
  an iCIMS portal is its slug, of a Jibe, Oracle, ApplicantPro, Teamtailor or
  SuccessFactors board its host, of a Zoho Recruit career site its host and page name (a
  posting's address is under it), and of a Comeet board its company uid,
  whether written
  `<UID>|<TOKEN>` or as the hosted board's address (those four were keyed on
  the whole ref until the test review of 2026-11-21; Hirebridge's number and
  Pinpoint's slug need nothing). What is READ is the ref as the quoted page
  spelled it; SmartRecruiters minds case, and `caseco` answering empty says
  nothing about `CaseCo`.
- **`apply` reads the frozen file and makes no request, but it asks the data
  again.** A write needs `--expect` with the code the look printed; the code
  covers the file's bytes, the author, and what the look would do, including
  what each record to be wired reads as (name, other names, website). Left
  alone, by name: a row that no longer carries both checks, what they were
  judged against, a dated read with postings and a size under the line; a
  read older than 24 hours (`board_proposals.MAX_AGE_HOURS`), undated or
  dated ahead; a company or board listed twice (under either key); a
  company its own worklist's readers doubted (`doubted`, however the row
  came to say WIRE); and anything that MOVED since the plan: the board on
  file, the website (check 1 is asked again), the name or also_known_as
  (check 2 was judged against them), out_of_scope, a parent, a note, a
  refusal, a discovery suspect or an Acquisitions row recorded since
  (`moved`), a reader's doubt kept since (`standing`), the board held for the
  owner under another company's id (`standing`, `queued`, `refused`), a
  Workable account held under a number the plan never followed whose name
  the board states or the company goes by (`number`, or the hold's own
  code), a door-filed
  row in Boards we found for any board (`queued`), a waiting row the plan did
  not record in the row's `waiting_seen`, or one it records that is not this
  very board - another board under every key it is known by
  (`wire_found.waiting_keys`, the plan's own test, asked by keys apply may
  lean on: the row's own board, and a waiting Workable number's slug only
  where a pair on file says it - never the worklist's `workable_numbers`,
  the row's `account` or check 1's sighting - review 9, 2026-11-23: an
  edited `account`, or a number tied to the find's slug in
  `workable_numbers`, waived the company's own unruled row and it dropped
  off his queue; its repair: the two edited together, which `account_tie`
  takes on the worklist's own record, waived it too; and review 10: so did
  the two with `check_1.saw` edited to ask by the number), or a
  Paylocity board tied
  by less than an id that agrees: the plan holds beside either and records
  neither, so a worklist saying it saw one is not taken at its word (review
  of the eighth blind review's fixes, 2026-11-22: an edited `waiting_seen`
  naming the company's own unruled row for another board wired past it, and
  the row dropped off his queue) - or a board proposal pending for the
  company in Agent proposals whether the plan recorded it or not (`waiting`),
  another holder (under either key), more postings shown than the board
  read. A write whose `--expect` is not the code of the data as it is now is
  REFUSED, even when nothing would be written. A worklist older than version
  5, or without `reader_doubts` (or with a blank one), `workable_numbers` or
  `page_addresses`, is refused: version 3 kept the readers' doubts only when
  it was written, and version 4 carried a record's page's address apply took
  on its word. The look prints, under each row it wires, every /All/ address
  it leans on ("leans on what the plan was told: ...") and the Workable
  account number it is asked for by, with what ties it to the slug and
  whether that goes into the note ("leans on its account number: ...", or
  "... for its holds alone ..."), and every row waiting on the owner that
  the wire takes off his queue ("takes ..."), and under each held row it
  files the pairs its doubt files with and the account words it is filed
  without; and the code covers them.
- **Every store is read strictly.** board_proposal_rulings.json,
  agent_proposals.json, embedded_ats.json, ats_suspects.json,
  board_doubts.json and the files the Acquisitions queue is built from
  (`board_proposals.ACQUISITION_FILES`: acquisitions_research.json,
  admin_dismissed.json, acquisition_rulings.json, board_audit.json,
  logo_log.json, link_health.json, site_identity.json) (`read_stores`,
  `board_proposals.strict`), and companies.json and board.json
  (`read_companies`, `read_board`): a file that is on disk and does not
  read, or reads as another shape, stops `plan` before it keeps a doubt or
  asks a board and `apply` before anything is written. Read through
  admin.read, a rulings file with merge-conflict markers in it lost the
  owner's refusal and the refused board was wired, and a broken research
  file dropped a company from the Acquisitions queue (third review; review of 27eeb628).
- **What a write is.** The wired boards are ONE journalled batch (action
  `wire-found`, the agent's own label, never `owner`; force only for the
  25-record rule, after the count is printed): `ats`, an `ats_note` saying
  both checks, and `board_owner` where the board's stated name differs in
  form. The held boards are one journalled APPEND to data/embedded_ats.json
  (its journal entry says "the board did not name the company, or something
  on file, or a reader, argues with it"). The readers' doubts are the
  plan's write, not apply's: one journalled addition to
  data/board_doubts.json (and entries to data/embedded_ats.json where a
  doubt went onto a waiting row), made before the plan's first request - the
  plan prints "Commit them together" for those at once; `settle` is one
  journalled addition to data/board_doubts.json. The worklist is redacted
  as it is read, whoever wrote it. Commit data/companies.json,
  data/embedded_ats.json, data/board_doubts.json and both journal files
  together. Postings appear after the nightly crawl, not before.
- **The queue's own door is `board_proposals.file_found`.** It appends and
  never replaces; takes `agent:<label>` only; never files `matches` (that
  word in this file is leave for `wire_embedded.py` to wire), a company
  already listed, ruled, or on a board, or a row whose read is not dated and
  fresh. A filed row carries `filed {by, on, at, read_at, page}`;
  `wire_embedded.py` leaves filed rows alone, and every row of a company the
  owner holds (below). The card prints the doubt
  ("Not wired because:"), the read date, and links both the board and the
  page that named it; `boardUrlFor` has a line for every type in
  `ats.FETCHERS`.
- **No other door wires a company the owner holds.**
  `board_proposals.held_for_the_owner()` is the one reader: a reader's doubt
  that stands (`data/board_doubts.json`), a row a door filed in Boards we
  found that he has not ruled on, a board refused in either store (Boards
  we found, Agent proposals), or a board proposal still pending in Agent
  proposals (the sweep wrote a pending company's board, where the wiring
  door held it: blind review, 2026-11-21; on file then, ICSolutions,
  pending since 2026-09-02). `discover_ats.py` asks it before its first
  probe - the weekly sweep and an `--only` list alike - and prints only a
  count (the workflow's log is public); `wire_embedded.py` asks it before it
  sorts, wires or queues anything, and leaves every row of such a company
  alone, whichever finder left it. AND BOTH HOLD BY BOARD
  (`board_proposals.held_boards_for_the_owner`, `held_board`): discovery does
  not write a board it found - in the sweep, an `--only` list or `--confirm`
  - when the owner holds that board under ANY record's id (a doubt about a
  company merged away, its door-filed row, a refusal under another id in
  either store, a board pending in Agent proposals for another record
  (`waiting`), a Workable account under either key where known, or one held
  under a number nobody followed whose name the company goes by); it logs
  "a board was found and is held for the owner under another record, so it
  was not written" and prints only a count. Nor does it keep such a board as
  a near miss (one that read and did not match the name) in
  ats_suspects.json, which feeds the Acquisitions queue and `--review`'s
  "accept" line, or print it: it is counted, and logged as "not kept for
  review" (a board refused for another record went back in front of him
  that way: blind review, 2026-11-21). wire_embedded.triage leaves such
  a row in no pile, and the run counts it with the rows it left for the
  owner; and it asks the door's other questions of a board on file
  (`wire_embedded.board_on_file`): a board another record already reads,
  under any key, or a Paylocity board a record's address cannot be told
  apart from, is an ownership question, never the clean pile, and one board
  under two spellings is one board claimed twice (claimants grouped by
  `board_proposals.found_keys` and `paylocity_relation`: sixth blind review,
  2026-11-22). Its words are decided over every address the record is known
  by, the whole /All/<uuid>/<Tenant> address its note keeps included, as the
  wiring door decides them: such a record is never told to be put on file
  by it, and ids that agree on one kind and disagree on the other, or a
  tenant's name that may be the row's written another way, are said as that
  (the eighth review's door verifier, 2026-11-22: only the record's ref was
  read, and --propose put advice that could not help into Agent proposals).
  And the hold reader both doors share
  (`board_proposals.held_board`) compares a Paylocity board with the owner's
  holds by every address on both sides, holding on anything but "apart", as
  the wiring door does: by keys alone a hold at a bare /List/ address never
  met the same board's /All/ address, and wire_embedded --write wired a board
  the wiring door held (seventh blind review, 2026-11-22). A store that is
  on disk and does not read stops either run before
  it starts: read as empty it would hold nobody. The second blind review
  (2026-11-19) had the unattended discovery sweep write a doubted company's
  board. Guard: `check_no_door_wires_a_company_the_owner_holds`.
- **`find_embedded_ats.py` needs `--out`.** It replaces its output file with
  one run's hits and its default is that queue file, so while the file holds
  anything a bare run is refused before the first request.
- Guards: `check_a_found_board_is_wired_only_on_two_checks`,
  `check_a_found_board_is_held_for_every_doubt`,
  `check_an_advertisement_is_not_a_second_board`,
  `check_a_board_named_by_a_number_is_wired_by_its_slug`,
  `check_a_found_board_is_sized_by_its_openings`,
  `check_a_readers_doubt_holds_the_board`,
  `check_a_readers_doubt_stands_until_the_owner_rules`,
  `check_a_readers_doubt_is_kept_once_and_reaches_the_owner`,
  `check_a_readers_doubt_is_settled_only_by_the_owner`,
  `check_the_doubts_reach_the_rows_in_entries_the_journal_takes`,
  `check_a_board_the_owner_holds_is_held_under_any_id`,
  `check_a_workable_account_is_held_under_either_key`,
  `check_the_wiring_door_reads_every_store_strictly`,
  `check_a_worklist_row_is_asked_again_at_the_write`,
  `check_a_found_board_is_not_wired_past_what_moved`,
  `check_the_boards_found_card_opens_what_it_names` (executed under node),
  `check_a_finder_cannot_replace_the_owners_queue`,
  `check_no_door_wires_a_company_the_owner_holds`. They share `_WiringBox`,
  which owns its data and shuts every way out of ats.py: a fixture that
  stubbed only the seams it knew about once let a new seam ask a real host.
  Blind review of 7742d06: 10 findings, all real, all fixed in 1a89cdf; 124
  mutations of the door, 124 noticed (the first check alone had missed 26 of
  41). Second blind review (2026-11-19, of the four refinements): 26
  findings, all reproduced, all fixed together with the checks above. Third
  blind review (of 8b926b9a): 25 findings, all reproduced, fixed on branch
  door-fixes-3 (the discovery guard first, on its own). Fourth (of
  27eeb628): 30 findings; two of its mechanisms left the owner's desk with
  dead ends, so the design was made simpler (any later ruling settles a
  doubt, `settle` on his word, holds by board instead of a merge refusal).
  Fifth (of ba3111cd, 2026-11-22): nine door findings, fixed on branch
  door-fixes-6 - a refusal under another record is this company's only
  where the file shows it (an alias or one name needs a second sign; a
  nameless merged-away refusal never is), a Paylocity board is known by the
  /All/ address its page states and held where its addresses cannot be
  compared, a waiting row is met under every key, and settle's printed
  command and its queue-row pairs are pinned. Sixth (of 3ce8f7d1,
  2026-11-22): eight door findings and ten unpinned rules, fixed and pinned
  on the same branch - Paylocity boards meet by every address on both
  sides (holds, twins, apply), what the door writes carries the address the
  page stated, the pre-pass asks what consider would read, one website is
  one address, a dismissed Duplicates pair is two companies, and
  wire_embedded's triage asks the door's holders questions. Seventh (of
  460b9171, 2026-11-22): six door findings fixed and pinned on the same
  branch - held_board's Paylocity half, a SKIP only on an id that agrees,
  the waiting rows' page_ref, a bare /List/ record's page asked once, one
  address grammar - and the triage grouping pinned; with them the tests
  reviewer's unpinned rules (Paylocity holds in discovery and --write, the
  tenant and Workable-pair rules of triage, a row about another board
  lending a record nothing, apply's tenant cases, the Duplicates dismissal
  keys, one website however written, a sibling path, conflicting ids, and
  the words of a failed page, an own waiting row and a desk-accepted row),
  and a record whose ids agree on one kind and differ on the other is said
  as that, not as "nothing compares them".
- **The owner's to settle:** whether a job platform nothing reads, named on
  their own site, should hold a board (it does now; a link to an aggregator
  and a link to the board's own vendor do not); whether a board named
  only in plain text should (it does now); whether Oracle finds are
  worth a way to ask their size; and what becomes of a board held under a
  Workable NUMBER whose slug could not be found: the desk will not accept a
  number, and while that door-filed row is unruled a board under the same
  account is held wherever a run follows the number to it or the board
  states a name the row goes by, so it waits for him to refuse it or for a
  person to put the slug on file. Also his: a doubt no ruling of his
  can reach (an older ruling, a company on a board, one merged away) holds
  its board until he is told and answers - `settle` writes his answer, about
  the one board he answered about; and a
  board refused for one company is SKIP refused only where the file shows
  the refusal was this company's - its survivor (the name the refusal
  recorded, no other record going by it, nothing it was recorded against on
  another record's website), or another record of the same company (one
  name or an also_known_as either way, AND one website - one address, host
  and path - or a Duplicates group where a website is missing, and the pair
  not dismissed there) - and for every other company that names it, a
  nameless refusal's survivor, an alias or a name on another website or a
  path or sub-host of it, and a pair he dismissed included, is filed in
  Boards we found with the refusal quoted, the group's own record included,
  for him to rule on there (discovery and wire_embedded hold it for every
  company). Whether a Duplicates group of one name with a website missing
  should be enough is his: a dismissal there frees the board. So is whether
  one Paylocity tenant's name ever carries two boards: the door holds such
  a board as maybe another (`tenant`), and never takes it for the one
  refused.
  Giants on readers that do read the whole board are still read in full
  before they are skipped.

## SAP SuccessFactors career sites: the reader (2026-11-22)

Owner, 2026-11-22: "can we get this board to be scrubbed for gainwell, if
not, why not?" (jobs.gainwelltechnologies.com/go/View-All-Jobs/8808000/). Until
then nothing read SAP SuccessFactors' Career Site Builder (CSB), Gainwell's
record pointed at its marketing site, and its 100 postings on the board were a
one-time capture of 2026-09-08. `ats.fetch_successfactors` reads a CSB site
now. Gainwell's record is NOT rewired by this work: that waits for a blind
review and the owner's word, through the wiring door.

- **The ref is the career site's https origin** (`https://jobs.gainwelltechnologies.com`),
  on the employer's own domain, and nothing more. `ats.successfactors_origin`
  refuses a path, a query, a port, a name before the host, and every host of
  SAP's own (successfactors.com/.eu, sapsf.com/.eu/.cn: rmkcdn. assets,
  hcm<N>. and performancemanager<N>. scripts, api<N>. the Unify API, career<N>.
  the legacy portals). `board_url` and the desk's `boardUrlFor` open
  `<origin>/search/`; `board_key` is its host.
- **The list** is GET `<origin>/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow=N`,
  drawn by the server, 25 rows a page; every hop is held to the ref's host
  (`same_host`) and asked of robots.txt, which allows /search/, /go/ and /job/
  on every CSB host read and closes /services/. It is paged by page 1's own
  row count (`ats.successfactors_walk`), and an offset at or past the total
  its pagination label states is not asked. **The label is a range and a
  total** ("Results <b>1 – 25</b> of <b>334</b>", in any of its languages),
  believed only when it is exactly those two bold parts and the range is the
  page's own: it starts at the row after the page's startrow, spans the
  page's rows, and the total reaches its end (`ats._sf_total`, eighth blind
  review). Any other shape - 'Page 1 of 25', three bold parts, the total
  first, a total of 0 beside the page's rows - is no total, so part of a
  board is never read or stated as the whole of it; a total below page 1's
  rows is none either, and the walk never returns fewer rows than page 1
  carried. **A read short of that total is an error, never the board**
  (seventh blind review, 2026-11-22): a page inside it that adds no posting
  (empty, page 1 again, the past-the-end list served in the middle), a read
  that reaches the total more than `SF_SLACK` (2) rows short of it, more than
  2 rows in one read that gave no posting (a title link off the site, an
  empty title, an address that does not say plainly where it goes - counted
  on every page, eighth blind review: the slack was a page, and 24 of 25 such
  rows vanished inside it), and the 80-page ceiling with or without a total;
  a hop from page N that drops or changes its startrow is refused before it
  is asked. Past its end the list says "no open positions matching" and
  shows the board's 10 most recent postings again (Gainwell, 2026-11-22), so
  a board with no label stops on the page that adds nothing. A posting or
  two closing mid-read cost a row each, as offset paging always has, and may
  move the last page past the end: that page adding nothing is not an error,
  and the read is still held to the total.
- **A row** is a `<tr class="data-row">` the page shows (read through
  `ats._cut`: a commented-out or script-held row is none): its first titled
  `jobTitle-link` to a /job/ page on the site - written twice, for a desk and
  for a phone, one posting - and its first `jobLocation` and `jobDate`. Measured
  on Gainwell's board: a remote role's place is 'Any city, TX, US, 99999', and
  the placeholder city and postal code are dropped ('TX, US'); a role open in
  more places ends in the board's own count of the rest, which stays ('Any
  city, AR, US, 99999 +1 more…' reads 'AR, US +1 more…'). A date is read
  in three forms only: 'Nov 20, 2026' (en_US), '21 nov 2026' (es_ES, Indra's),
  and '21.11.2026' (de_DE, JENOPTIK's) only on a page whose `<html lang>` is
  German. Anything else is no date, never a guess. **No address is read
  that does not say plainly where it goes**: a posting link or a hop whose
  path, raw or with any layer of its escapes undone, has a '.', '..' or
  '..;' segment, an empty one or a backslash (robots.txt is asked of the
  address as written, and '/job/%2e%2e/services/...' went out as
  '/job/../services/...').
- **Behind FETCH_DETAILS** the ad is each posting's `itemprop="description"`
  element, to the close that matches it. CSB has no pay field; a figure in the
  ad's text is plain_rows' text parse, as for every reader. **A posting whose
  own page says it has closed is not published as open** (eighth blind
  review): a 404 or 410, a hop to the site's list (/search/ or /search, with
  or without a query; not followed), or the site's own words at its address
  (`ats.successfactors_filled`: a 200 page showing no posting that says "this
  position has been filled", as a reader sees it - never inside a comment or
  a script; Gainwell's closed page, measured 2026-11-22, says "Sorry, this
  position has been filled." under the posting's own title) drops the row,
  and the reader says how many on stderr. **The posting's title alone is not
  the site saying so** ('<role> Job Details | <site>'): every OPEN posting
  page measured carries it too (blind review 9, 2026-11-23: a page in the
  site's own template, titled for the role, with an error where the posting
  was, was read as closed by its title, and five open postings of seven
  answering so were dropped). A refusal, a 429, a 5xx, the network failing,
  a hop anywhere else off /job/ (not followed) and a page that shows no
  posting and is not the site saying so - a block page served 200 (F5's
  "The requested URL was rejected"), an error page, the site's template
  titled for the role without those words - say nothing about the posting,
  and the row stays as the list states it (repaired 2026-11-22: every page
  showing no posting was read as closed, and five open postings of seven
  answering a block page were dropped). A list every one of whose postings
  answers closed is an error, never a zero.
- **Never a zero.** A first page with no rows is an error: a "Unify" site
  (Aurigo's, Esri Canada's: its list is POST /services/recruiting/v1/jobs,
  which robots.txt closes) is read from its sitemap instead (below); a
  classic page with no rows says a CSB board with nothing open has not been
  seen; rows with no titled posting link say so.
- **The probe** (`verify_boards._successfactors`). The site's name is the
  board's own configuration, on the /search/ page: its `<title>` and meta
  description, one locale's pair ('X Jobs' / 'Find at X'; 'X - Stellen' /
  'Bei X nach suchen'; 'X ofertas de trabajo' / 'Buscar en X'), believed only
  when the two name one name, and always one of the names. Then the postings'
  `hiringOrganization` metas: every one asked must carry one, not empty and
  not in a comment (a posting naming nobody states nothing), and another
  employer is a second name, so not proven. **The whole board or nothing**
  (the owner's rule, seventh blind review, 2026-11-22): a board is stated
  only when its label states a total of `SF_POSTINGS_ASKED` (40) or fewer and
  every posting on its list was asked, its list read to the end (a list
  whose pages carry more postings than the 40 asked is not stated either);
  a longer board, or one with no total, is asked about its first
  `SF_POSTINGS_SAMPLED` (5) for the desk and states nothing. So Gainwell's
  334 goes to the owner's queue, never wired unattended. `n` is the label's
  total (`n_is_partial` when a page has none). **A posting that has closed
  proves nothing either way** - a 404 or 410, a hop to the site's list (not
  asked), or the site's own words at its address saying so
  (`ats.successfactors_filled`, the reader's rule above, never the posting's
  title alone): Gainwell's closed ones say only "Sorry, this position has
  been filled.", measured 2026-11-22. It names nobody for the board and nobody against it: it is
  passed over, and on a longer board the next posting on page 1 is asked in
  its place, `SF_POSTINGS_ASKED` (40) in all at most. It is not a failure of
  the day - it was, and a list that keeps a filled posting answered so every
  plan, so the board never reached a person (eighth blind review). Where
  every posting asked answered as closed, the board states nothing and goes
  to the owner, saying so. **A board is stated whole only with at most
  `SF_SLACK` (2) of its postings closed** - the churn the walk allows; more
  is a list carrying postings that name nobody, and the names of the rest
  are kept for the desk, not stated. **A page that shows no posting and is
  not the site saying so is not a closed posting** (repaired 2026-11-22: a
  block page served 200 at five of seven postings was passed over, and the
  board stated whole and proven on the other two): a block or error page -
  the site's own template titled for the role, showing no posting and not
  saying it was filled, among them (blind review 9) - or a hop anywhere but
  the list (not asked), is the board unreadable for a person - `failed`
  "unreadable", not a failure of the day, no name - so the door holds it
  with the reason. A posting answering 401 or 403 is the board
  refusing, and 429 a failure of the day: neither is a closed posting.
  Postings written in more than one language (their title's own lang) are a
  board listing a role once per language (JENOPTIK's twins), and are not
  stated. A placeholder name ('our team' on SPX's board, the tenant id
  'esricanada' on Esri Canada's) is stated as it is, and proven() refuses it
  by name.
- **The finder** (`find_site_boards`). A page whose OWN markup - its scripts
  and other tags' attributes, never its links or frames, never what a browser
  saw it ask for - loads CSB's machinery is a board at THAT page's origin:
  rmkcdn.successfactors.com/<8 hex>/ assets, a `j2w.init({... "ssoCompanyId"
  ...})` block, jQuery from (hcm|performancemanager)<N>.(sapsf|successfactors).(com|eu)/verp/vmod_v1/ui/extlib/,
  or its own /platform/js/j2w/ scripts. A link to `<host>/go/<Name>/<digits>/`
  or `<host>/search/?...sortColumn=...` on a host of the company's own (the
  registrable domain of its website, never SAP's) is a board at that origin.
  A legacy portal's address is filed `successfactors-legacy`, a platform
  nothing reads, as it was before CSB had a reader (renamed only because
  ELSEWHERE never files a type the crawl reads); every other SAP address is
  `successfactors-other`, a form of a platform we read that names no board.
  On the board's own pages both are its furniture - its j2w.init ssoUrl
  names its tenant's legacy portal, and Indra's front page links it to sign
  in - but a legacy portal only where it names that board's own tenant (its
  company= is the page's ssoCompanyId) or none: another tenant's portal
  beside the board is another account, and stays (seventh blind review).
- **Unify sites: the sitemap, then each posting** (owner, 2026-11-23: "yes,
  build the sitemap reader for those three" - Aurigo, Esri Canada, Octave).
  `successfactors_board` says a Unify /search/ page is one
  (`ats.SuccessFactorsUnify`) and `fetch_successfactors` reads the board
  from `<origin>/sitemap.xml` (`ats.successfactors_unify`): one `<url><loc>`
  a posting at /job/<slug>/<id>/ (measured 2026-11-23: Aurigo 16, Esri
  Canada 32 - about 16 roles, each in English and in French - Octave 131;
  every `<lastmod>` one day, never read). **The sitemap**
  (`ats.successfactors_sitemap`) is asked as every page is (robots.txt at
  every hop, its own host, and nothing under /services/ whatever robots.txt
  says) and read as an XML reader reads it: a commented-out `<url>` is not
  listed, and a loc's escapes are undone ('&apos;' Esri Canada's, '&amp;'
  Octave's). A /job/ address on the origin that says plainly where it goes
  is a posting; anything else is passed over and said; one that is a
  posting but does not read plainly (a bare '&', an entity XML does not
  define, '&amp;apos;' undone twice, a relative address, CDATA, a control
  character) fails the read, so a posting is never silently lost. A
  `<sitemapindex>` is followed one level, to at most `SF_SITEMAPS_MAX` (5)
  sitemaps on its own origin; another host's is not asked. **Never a zero,
  never a giant**: a sitemap robots.txt closes, one not there (404 or 410:
  the board unreadable, never gone), one listing no posting and one listing
  more than `SF_SITEMAP_MAX` (400; the owner skips giants of about 500+) are
  errors that say why. **Each posting is asked once**
  (`successfactors_posting`: its loc and the 302 to
  /job/<slug>/<N>-<locale>/), two requests a posting beside the /search/
  page and the sitemap - Octave 264 a night, Esri Canada 66, Aurigo 34, and
  robots.txt: the cost the owner accepted. **A row is the posting's own
  page** (`ats.successfactors_unify_row`): the role (its itemprop="title"
  text, else og:title); the first of the tenant's place tokens shown with a
  value (Posting Location, Job Location (Short), Job Location, Location,
  Work Location), else its Country, a token naming several places split at
  '|' and joined '; '; Workplace Type as the row's mode, and Remote said
  first in the place ('Remote; São Paulo, Brazil') so the place a remote
  role names is not its desk; the Posting Start Date only on a page whose
  `<html lang>` is en-US (M/D/YY: en-GB writes the day first, so no other
  page is dated); the url the sitemap's address, never where it landed
  (Esri Canada writes a French posting's Location twice encoded); the ad
  (`successfactors_ad`) behind FETCH_DETAILS, for no request more. **One
  requisition is one row**: copies that land on one N (Esri Canada's
  .../663-en_US/ and .../663-fr_CA/) are published once, the en_* copy where
  there is one, and said. **What a posting answers**: the site saying it
  closed (`successfactors_closed`, `successfactors_filled`) leaves it out,
  said on stderr; a 429, a 5xx or the network fails the whole read at once,
  raised as it came, so refresh and build_board ask a 429 and the network
  once more and a board that still will not read keeps its state (Unknown,
  never "None found") - one flaky request never empties a board; anything
  else (a page showing no posting that is not the site saying so,
  robots.txt closing it, a 401 or 403, a hop off /job/ or off the host, a
  page naming no role) is left out and said up to `SF_SLACK` (2) postings,
  and more is the board unreadable; every posting closed is an error.
  **The probe** (`verify_boards._successfactors_unify`): the site's name is
  each posting page's `<title>` after its last ' | ' ('<role> Job Details |
  <site>', in French '<rôle> Détails de l'emploi | <site>'), stated whole
  only by the classic rule - the sitemap lists `SF_POSTINGS_ASKED` (40) or
  fewer, every one asked, at most `SF_SLACK` closed; a longer one is asked
  about its first `SF_POSTINGS_SAMPLED` (5) that answer, for the desk; a
  block page or any other failure is the board not read. The classic
  probe's language rule is not asked: the reader pairs the copies. The
  names measured: 'Aurigo Software Technologies', 'esricanada', 'Octave'.
  proven() is unchanged: 'esricanada' proves nobody (a country on our name
  must be on the board's), and 'Aurigo Software Technologies' does not
  prove the bare 'Aurigo Software' - Aurigo's record passes only through
  its also_known_as 'Aurigo Software Technologies Inc.' (asked offline of
  the record on file). **Read offline** over the responses saved
  2026-11-23, the postings with no saved page served a saved page of their
  host retitled (no live read yet): Aurigo 16 rows in 34 requests, stated
  whole and proven for its record by that alias; Esri Canada 16 rows from
  32 locs, 16 copies paired, in 66, stated whole as 'esricanada' and
  proven for nobody; Octave 131 rows in 264, five postings asked, not
  stated.
  **Known**: the ad is the FIRST itemprop="description" element only
  (`successfactors_ad`, shared with the classic reader), and Esri Canada's
  postings split theirs into three, the pay sentence in the third; Esri
  Canada's Salary Min/Max tokens are not read. Wiring the three waits for
  the door. Guard: `check_successfactors_unify_reads_its_sitemap` (77
  one-line breakages of the reader and the probe, each alone in a private
  copy, each caught).
- **Measured** on six CSB hosts by the research of 2026-11-20 (Landis+Gyr 63,
  JENOPTIK 175, SPX 237 and Indra 623 classic; Aurigo and Esri Canada Unify)
  and on Gainwell's of 2026-11-22. **Read live once** the same day (details
  off, no cache): 334 rows, every one of the 334 its label states, from 14
  list pages and one robots.txt on its own host, all dated (2026-10-25 to
  2026-11-22); the probe asked page 1 and five postings and stated
  'Gainwell Technologies LLC', proven for the record - under the first
  probe. Under the whole-board rule since, the same answer is not stated
  (334 postings, five asked), so Gainwell goes to the owner. Every placeholder place
  read clean (80 rows read 'TX, US') except 14 rows open in more places than
  one, and the rule that keeps their '+1 more…' was written after that read,
  from one read of the list sorted by place.
- **Not built**: a multi-brand tenant's /<Brand>/search/; a unit
  filter for a group's board (SPX's 237 rows are 12 units, named only in a
  per-site Facility column). And a board on file that lists each role once
  per language publishes each copy: the list marks no row's language (two
  JENOPTIK twins share a title and differ by id and by the language of their
  place), so the reader cannot pair them; the probe holds such a board, so
  it is never wired unattended, and asking /search/ with a locale was not
  measured.
- Guard: `check_successfactors_reads_its_career_site`, beside a row in
  `check_a_board_is_proven_only_by_its_own_stated_name`'s BOARDS and two forms
  in SITE_BOARD_FORMS. First round: 64 breaks of the reader, the probe, the
  finder and the tables, one at a time in a private copy, 64 noticed (three
  only after the check was tightened). Seventh blind review (2026-11-22):
  nine findings and the tests reviewer's unpinned breakages (E-01..E-14,
  E-22), each fixed or pinned; then 99 breaks of the code as it stands, the
  first round's included where the code moved, 99 noticed. Eighth blind
  review (2026-11-22): the label read as a range and a total, rows that gave
  no posting counted, the last page's churn, closed postings (passed over in
  the probe, dropped behind FETCH_DETAILS), and the tests reviewer's
  unpinned hop targets, startrow, 403/429, no-total, comment and four-deep
  cases - each fixed and pinned, Gainwell's measured shape (334 postings, 14
  pages of 25) among the cases; each fix reverted in a private copy fails
  the check. Its repair pass (2026-11-22): a closed posting is only the site
  saying so (a block page served 200, or a hop anywhere but the list, is the
  board unreadable and the row kept), a board is stated whole with at most
  `SF_SLACK` closed and every listed posting asked - thirteen breaks of the
  repair, one at a time in a private copy, each noticed. Blind review 9
  (2026-11-23): the posting's title alone is no longer the site saying it
  closed - only its words, a 404 or 410, or a hop to the list (the eighth
  review's pin that read a title-only page as closed is turned round, so the
  title rule and its unescape are gone); a hop to '/search' without its
  slash, and to '/search?x', is pinned as closed, and the probe's words when
  more than `SF_SLACK` closed are pinned whole. Each fix reverted in a
  private copy fails the check.

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

### The claim moves to HQ on one switch (2026-10-22)

Owner's list, item 29: "when HQ deploys, set `SLEDJOBS_FEED_TOKEN`, then move
the claim link to HQ." Built ahead of the deploy and dormant until then.
- `scripts/hq_employers.py` reads HQ's verified-employers feed (HQ's
  `docs/feeds/sledjobs-employers.md`, v1) nightly into `data/hq_employers.json`,
  through `hq_jobs.read_feed` and on the same rules: any answer but 200 keeps
  the list on file, a 200 is the whole list, and no token asks nothing.
  Refused one row at a time, each with its reason: a company the board lacks,
  one with no website, a date that is not YYYY-MM-DD or is in the future, and
  a repeat. Only `company_id` and `verified_since` are kept.
- **A list read more than a week ago marks nobody** (`STALE_DAYS`): a
  revocation reaches the board only on a good read, and HQ's contract asks
  for this.
- A verified company's org row carries `hq_verified` (the date) and only that
  company's, and only while it has a website (re-checked at build, like an
  HQ job). Its page and the app say "A verified recruiter on SLED HQ":
  somebody at the domain proved an address and a person here checked it, so
  they can post jobs and hire there, nothing more. Never "hiring": the feed
  does not know that. They offer "Post a job" (`board.json` `hq_post_at`),
  and once the claim lives on HQ, "Work there too? Claim this page" (HQ
  verifies up to three people a company). The board build prints a line
  when the list on file is too old to mark anybody.
- **`hq_employers.CLAIMS_ON_HQ` is the switch**, False today. On, every "Claim
  this page" opens `https://hq.<domain>/claim?company=<id>` (HQ's own
  parameter): `build_site.claim_href` on the static pages and `board.json`
  `claim_at` in the app. **build_site stamps `claim_at` into the board.json
  it ships** (`stamp_claims`): the nightly board build alone left the app a
  day behind the pages after a flip (review, 2026-10-22). No claim was ever made on this site's own form
  (`data/claims.json` is empty), so nothing is stranded.
- **The day HQ is live**, in order: the owner sets `SLEDJOBS_FEED_TOKEN` (the
  same value on both sides), the nightly run reads both feeds, then
  `CLAIMS_ON_HQ = True`. HQ's company-profiles feed (a company's own words,
  logo and links) is a third feed, not read yet.
- `check_the_claim_moves_to_hq_on_one_switch` runs the app's company view in
  `jobcard_harness.mjs` in four states (switch off and on, verified or not)
  and compares its words and links with the static page's.

### Co-bidders from SLED HQ's bid records (2026-10-23)

Owner: "yes" to "SLED JOBS could turn co-bidders into competitor suggestions
you rule on". HQ's team opens a Texas government's own bid document (a
tabulation, a score sheet, an award, a pre-bid sign-in) and records which
companies it names and in what role; HQ's feed (`/api/feed/bid-records`, its
`docs/feeds/sledjobs-bid-records.md`, v1) carries only those person-confirmed
readings, and only HQ's own team's, never a customer's.
- `scripts/hq_bid_records.py` reads it nightly in `refresh.yml` into
  `data/hq_bid_records.json`, through `hq_jobs.read_feed` and on its rules:
  any answer but 200 keeps the records on file, a 200 is the whole list, no
  token asks nothing. Records are kept from the contract's twelve-field
  allow-list and refused one at a time (id, place, address, role, numbers, a
  future date, an email or phone in a name, a repeat).
- **HQ's `company_id` is checked, never taken**: it links only when that
  board company's name or an alias folds (case, accents, punctuation, a
  legal suffix) to the record's company. Otherwise the record is kept,
  unlinked, with why.
- **Two board companies that both BID on one solicitation** (submitted,
  finalist, recommended, awarded) become one `cobid` proposal in Agent
  proposals: the pair and every solicitation, each with the government's
  document. A pre-bid sign-in alone pairs nobody: it is interest, not a bid.
  One solicitation is its place, its printed number (or the document, when
  none), and its year and title wherever printed, so "Bid 1" in two years is
  two. The proposal cites the document naming both companies where there is
  one, else each company's own, and shows each name as HQ's person confirmed
  it and as the document prints it. A pair already listing each other is not
  proposed; a pair a person ruled on is never asked again; a pending pair is
  `withdrawn` with the true reason (its records left HQ's feed, it no longer
  links to a board company, or the two now list each other) and asked again
  if its records come back; a read refusing more rows than a real feed
  explains withdraws nothing; a list read more than `STALE_DAYS` (7) ago
  proposes nothing; a night with the same records writes nothing. A blind
  review found 9 defects in the first version, each now a case.
- **Accepting** (`proposal_rulings._accept_cobid`) writes each onto the
  other's competitors in one journal entry: why "Both bid on <solicitation>
  for <government>", source the document, by "SLED HQ bid records". An edge
  already there stays as it is; a company at the cap (8) is a refusal, never
  a truncation.
- Dormant until HQ deploys and `SLEDJOBS_FEED_TOKEN` is set (HQ had no
  records on 2026-10-23). `check_co_bidders_from_hq_become_suggestions_a_person_rules_on`
  (mutation-tested with the supplier work, 46 of 46).

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
  re-wired Concourse after the owner ruled it out. Discovery also skips every
  company the owner holds for a board (`board_proposals.held_for_the_owner`:
  a reader's doubt, a door-filed row in Boards we found, a refusal), and
  stops before its first probe if one of those files does not read.
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
  - **It shipped reporting** (`CSP_MODE = "report"`, Report-Only header) and
    was flipped to `"enforce"` on 2026-10-14 after a live check of the
    five pages (DEPLOY.md §3 says how it was checked). The gate's holding
    page was enforced from the start. selftest refuses `GATED = false`
    while it only reports. Cloudflare's Web Analytics beacon runs on the
    nonce the edge copies onto it, not on a host entry.
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
- **A second launch audit (2026-10-09)** found 56 things; the first batch is
  fixed here, each with a check broken on purpose:
  - **A company alert is about that company.** Every company page links to
    `/alerts?company=<id>`, and nothing read it: the reader signed up for
    every role on the board. alerts.html now names the companies, `cleanPrefs`
    keeps up to 20 lowercase ids, and `digest.matches()` refuses a posting
    from any other company. `check_a_company_alert_is_about_that_company`.
  - **Every control answers the keyboard.** A row the app draws as a div or
    span goes through `reachable()` (tabindex 0, role button), and one keydown
    listener turns Enter and Space into its click. The Companies tab, saved
    rows, sibling roles and "Show more" could not be opened without a mouse.
    The selects under More filters carry aria-labels.
    `check_every_control_answers_the_keyboard`.
  - **The app's company view counts openings,** as its stat strip and the
    static page already did: one row per opening ("and 222 other
    locations"), and pay prints a figure only when every posting states the
    same one (`coGroupPay`, `_group_pay_cell`).
    `check_the_app_company_view_counts_openings`.
  - **A link goes where it says.** The state page opens `/?off=` (office
    state; `st=` is territory and dropped MN and SD). A parent or brand links
    only to a record we hold, else plain text. The claim mail sends "their
    page" to /c/ only when one is built, and a mismatched domain opens the
    Add-a-company form, not a `tab=submit` nothing has.
    `check_every_link_on_a_page_leads_where_it_says`.
  - **Every CSS variable is defined** (`.hot` used an undefined
    `--penguin`), and `:root[data-theme=dark]` repeats the dark tokens so the
    theme switch works on a light system. `check_every_css_variable_is_defined`.
  - **"Hiring a seller" means a quota-carrying role**, on conference rosters
    and in the company link preview, never "any open role".
  The second batch:
  - **"Hiring hard" ignores a night we started reading more** (momentum
    rule 6). On 09-02 the crawler began reading whole boards and Motorola
    went from 33 openings to 357 overnight; its badge said 23 quota-carrying
    roles became 62 while they had fallen from 69. A night that adds 15+
    openings of any family and half again moves that company's baseline to
    it, and the badge names that night. `check_a_coverage_jump_is_not_a_surge`.
  - **A posting keeps the night a snapshot first held it.** carry_first_seen
    read only the previous board, so postings hidden by sled_only or behind a
    failed read were dated the night they came back: Granicus said "first
    read here 1 day ago" about boards read since August. Exact ids from
    data/history now count too (1,171 postings moved), and `read_since` on an
    org is the first night we held any of its postings, where that is
    earlier than its open ones. `check_a_posting_keeps_the_night_we_first_saw_it`.
  - **Ownership prints the ruling's sentence, never a stored year beside it.**
    The year was often another deal's (DTN "acquired 2025" was its own
    purchase), and the label said "their own announcement" over research.
    `check_ownership_says_what_the_record_holds`.
  - **The role page's pay sentence counts with payBit**: silence only where
    we read the posting, unread ones said as unread, and a $ range only from
    yearly USD (Mueller's "$80k" was CAD). `check_the_pay_sentence_counts_what_it_read`.
  - **Every view has an address.** Tabs go through `goTab()`, the Companies
    list keeps its sector, sub-sector and "everyone" in `csec`, `ccat` and
    `call`, and "Browse all N in X" opens the list on X. The middleware
    titles ?e= as its /e/ page (meta-events.json) and a filtered board as the
    jobs tab, not as the home page. `check_every_view_has_its_own_address`.
  - **Fog never colours reading text** (49 rules and inline styles moved to
    `--dim` / `--c-ink2`); counts, the add dialog and the alerts page speak
    to a screen reader; the conference panel keeps and returns focus; the
    banner pauses while a keyboard is in it. Element `onfocusin` does not
    exist in any browser: use addEventListener.
    `check_reading_text_is_never_set_in_fog`, `check_assistive_tech_hears_what_changed`.
  - **A blind review of that batch** (3 lenses, each finding challenged)
    confirmed 11, all fixed and each guard broken on purpose: a sub-sector
    is matched in the same filing as its sector (`coFilings`, `coCatOk(o,
    cat, sec)`, `category_counts`), so "Browse all N" opens N; `goTab` owns
    PENDING_URL and `applyCoUrl` clears an earlier query's pill release; one
    click is one history entry; the panel's Tab trap is on the document
    (focus falls to <body> when a vote disables the button); an empty live
    region stays displayed (display:none takes it out of the tree); and
    `history_first_seen()` reads HISTORY when called, not at import.
  The low findings, first half (claims and links):
  - **A namesake city with a US state code is the US one** (`roles._us_namesake`,
    `US_PREFIX`): OCLC's eight "Dublin, OH", "Manchester, NH", Badger Meter's
    "US - FL - Melbourne" and "(New England)" were placed outside the US.
    Fixed at the next crawl; a redraw does not recompute `is_us`.
  - **The front page counts openings**: the sector buttons, a measured
    widest requisition instead of a frozen "93 cities", and "Hiring hardest"
    by the `quota_roles` the Companies tab shows. **A debut is one on any
    record** (`sellers_before` reads data/history and data/hiring_history;
    `seller_debut` on the org), never "not on today's board".
  - **feed.xml has no silent cap** and links at the canonical's encoding;
    the no-JS page counts quota-carrying openings in the US.
  - **A deal sentence cut at the research's 400 characters prints to its
    last whole sentence** (`deal_text`), without "(Correction: ...)" notes;
    Run by counts `published_count`, not staged registry rows.
  - **A board nobody read gets no phase** (rows, intel, role page, story),
    says which of the three no-board states it is in, and is dated by
    `last_looked()` (the later of the probe and a person's capture); "none
    added in 30 days" only when somebody looked within 30. Dates format in UTC.
  The low findings, second half (accessibility):
  - **Text takes a text token**: `--accent-text` (never `--accent`, 4.16:1
    on the dark panel), `--warn-text`, and `--dim-chip` for secondary text on
    a `--chip` (`--dim` there is 4.26:1). The static pages' links take
    `--link-text`. Derived values are in brand.json `derived`.
    `check_text_on_tags_and_dark_cards_is_readable` measures them per theme.
  - **A screen reader is told where it is**: the nav marks the view with
    `aria-current="page"` (aria-selected means nothing on a button);
    `viewTitle()` titles every view, role and company; opening a role or a
    company moves focus to its h1 (`focusView`), and a redraw of the same
    company gives it back; each filter chip names its filter and keeps
    focus; the map canvas was named and had a list of states doing its one
    action (the Map tab is gone since 2026-10-27); views go h1, h2, h3; no nested <main>; the claim page's fields
    have names and its messages are live. `check_every_view_is_announced_and_named`.
  - **The blind review of the low findings** confirmed 12 (0 refuted), all
    fixed and broken on purpose: Run by counts catalogue rows only (a
    promoted chapter row is the same event); a namesake keeps its foreign
    home when the code is its own country's ("Vancouver, CA", "Berlin, DE");
    the map's state buttons count what they open and keep focus; a company
    card marks Companies current and any open the reader makes is a new
    visit; a conference panel titles the page; `shares_board_with` now
    ships (intel counts boards); e/ pages define --space-8, and
    `check_every_css_variable_is_defined` now scans the built pages' CSS.
- **A third launch audit (2026-10-09)**: the mail a subscriber gets, what
  search engines and link previews see, and what a reader sees when things
  fail. 18 found, 0 refuted, all fixed and broken on purpose (27 of 27):
  - **The digest counts openings** (`digest.openings`, "N locations" on the
    row) and keeps to the board's default: roles placed outside the US only
    for a subscriber who ticks `worldwide`, and then labelled with where they
    are. "And N more" links the board with the subscriber's filters
    (`board_link`). `check_the_digest_counts_openings_and_keeps_to_the_boards_default`.
  - **A pending signup sends at most three confirmations** (`MAX_CONFIRMS`)
    and lapses a week after the first; a saved company syncs its `kind`.
    `check_a_stranger_cannot_mail_somebody_without_end`.
  - **Claim mails and the portal link /c/ only where it is built**
    (`verify_claims.page_url`, the claim GET's `page`), and the portal tells
    our failure from an expired link. `check_claim_links_open_pages_that_exist`.
  - **The middleware describes only "/"** (claim?co= kept its own head); ?co=
    previews are clipped at a word (`clip_desc`) with one stop; a company's
    first sentence does not stop at "Inc."; an ended floor nobody read is not
    in the sitemap and no empty floor says "who is hiring"; feed dates are
    RFC 822 and a sitemap lastmod is a role's first_seen or nothing; the
    share cards carry no counts (nothing redraws them).
    `check_search_and_previews_see_what_is_there`.
  - **"Today" is the reader's** (`when()` against `todayISO()`); a role gone
    from a board we cannot read says we cannot tell (`goneWhy`); the alerts
    and claim pages tell our failures from a bad link (`GONE`); a blocked
    site store says so instead of failing silently (`storageWorks`).
    `check_the_reader_is_told_the_truth_when_things_fail`.
  - **The blind review of those fixes** confirmed 13 (0 refuted), all fixed
    and broken on purpose: a company alert includes the company's roles
    abroad unless its subscriber said not (`worldwide` None means "follow the
    company page"); the "and N more" link carries the email's window as
    dates (`from`/`to`, read by the board and shown as a chip), never a
    relative horizon; a row says places and postings apart; the confirmation
    budget is per address (`cb:` key, 30 days, cleared on confirm); "who is
    hiring" needs a seller hiring; `todayISO()` is a New York date, the
    calendar the board is stamped in; the age filter says "of the last
    check"; a role at a company with no board says so.
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

## APA's 48 chapters, and the company page's Conferences box (2026-10-28)

Owner, 2026-10-28: "do we have all the apa chapters?" - no: 20 of APA's 48
(its own list, planning.org/chapters, read in a browser because planning.org
now serves crawlers `Disallow: /` half the time) and none on the tab. Then
"yes add all 48", and "remember to update the conferences box on each company
page that highlights what they have gone to and are going to".

- **Registry.** The 28 missing chapters are rows in state_events.json (merged,
  never register_state_events, which replaces the file); Arizona and Florida
  had stale addresses; New York's row is the Metro chapter, Upstate its own.
  `find_event_directories --directories --only CODES` reads only named rows.
- **Read by a reader and a skeptic** (four batches): each chapter's own name,
  its conference, the edition, and THIS edition's sponsor/exhibitor list - in
  text, alt text, or pictures opened and read (scratch helpers fetch an image
  through ats._page, robots asked at every hop). Year-round chapter sponsors,
  older editions and sign-up pages were refused as lists.
- **The listed route** in stage_promote: a row carrying a confirmed
  `conference_read` is listed with NO exhibitor_url if the reader's quoted
  words naming the event and its year are still on the page today (every
  piece of a "..."-joined quote, matched on _flat text), the page is the
  chapter's (owns()), and the read is confirmed. A joint conference is listed
  once, named for the event (`joint_event` on the host row, `covered_by` on
  the others: Quad State = IA/KS/MO, Deep South = AL/MS, Montana & Idaho).
  fetchability is `picture` or `reader` where the readers' names are staged,
  so no sweep reads the page and puts its menu over them (a find replaced a
  find until Wave 0, below). check_a_chapter_listed_without_a_floor_is_held_to_the_page: 7 of 7.
- **37 rows listed for 41 chapters**; 23 floors staged from the names BOTH
  readers saw (532), read by read_floor (16 vendor, 425 supplier, 6 held),
  intaken under Housing & Community Dev; dates and cities landed through
  apply_conference_reads. SNEAPA (CT/RI/MA) failed owns() - its site names no
  state often enough - and the owner ruled it: "add sneapa under connecticut".
  The ruling is `ownership_ruling` on the row ({by: "owner", url}), accepted
  in place of owns() for THAT page only and only from a person; listed as
  SNEAPA 2026 with Rhode Island and Massachusetts covered by it, no floor (its
  only sponsor list is 2025's). Not listed, each for its reason:
  National Capital Area and Northern New England (planning.org subdomains with
  a regional geo: owns() has no state to find, and _names_the_org's two-word
  bar would pass APA's own pages), New Jersey and Western Central (robots.txt).
- **Tags.** `_tag` drops a year the name already carries ("2026 APA Quad State
  Conference" was "... 2026 2026") and shortens "American Planning
  Association" to APA before cutting a long name; GENERIC_TITLE knows
  "Homepage". A name that is only a year and conference words ("2026 Annual
  Conference") falls back to the organisation.
- **The Conferences box** (index.html coConfGroups/coConfs, build_site
  co_conferences, one rule): On now / Going to / Date not announced / Went to,
  from o.conferences joined to board.conferences, which now ships `prior_tags`.
  A TAG NAMES ITS EDITION BY ITS YEAR: a row that rolled to next year's dates
  kept its old tag, so the first build put Accela and Esri "Going to" ICMA
  2027 off their ICMA 2026 tag. A tag older than its row's dates is Went to,
  newer is Date not announced. It is rebuilt from board.json, so a scrub that
  tags companies AND dates the event (then quick_rebuild) updates every box.
  check_a_company_page_says_which_conferences_it_went_to_and_is_going_to runs
  the app's own function through jobcard_harness and the static port on one
  fixture: 8 of 8 breakages caught.

## Wave 0 of the 2026-10-28 review: what Sunday's scrub would have broken

Owner, 2026-10-28: "go, start wave 0", after a 72-finding system review (63
confirmed by a skeptic each). The fixes, each with a guard broken to prove it:
- **A re-sweep adds, never replaces** (`sweep_exhibitors.merge_into` and
  `add_new`, which render_floor's `add_to` now calls). The scrub's
  `sweep_exhibitors --all --write` would have put ICC 2026's 11 shell-page
  names over 107 a browser read and sent 65 reader-graded floors (8,381
  names) back to unread. Staged rows keep their names, verdicts and grade; a
  name the candidate door calls the same company is not new; a name a reader
  dropped (conference_intake/reads/) stays dropped and one a reader judged
  comes back judged. A reader's "not a floor" rules on its NAMES, not the
  address: the first version froze the address, and Government Fleet Expo's
  /exhibitors, 21 menu items when read, carried 18 GFX 2027 exhibitors two
  weeks later (blind review). To start a floor over, delete its file.
  check_sweep_keeps_its_own_work: 11 of 11.
- **`conference_dates --json` keeps the confirmations** the admin's Conference
  dates queue reads "not found" from, while each is still about the date on
  file; a --limit run replaces only what it re-read. A confirmation is about
  the date it checked (`held`): ICMA's "not found" about 2026-10-17 sat
  against September 2027 after the row rolled, so q_calendar and the carry
  both drop one about another date (the queue went 20 to 12).
  check_the_date_report_keeps_its_confirmations: 6 of 6.
- **`held_dates`**: the dates of the edition a floor was read at, on a row
  with no dates (a chapter row is promoted undated; an "unannounced" read
  answers the NEXT edition, and an owner's "unannounced" ruling erased them).
  apply_conference_reads takes them with a source, the read's own
  held_confidence high or medium, written "Month D-D, YYYY" with the month
  in full (the app's calRange reads nothing else; "Aug. 30" split the two
  boxes), in the tag's year. The ruling keeps them ONLY when the dates are
  the tag's own year: a rolled row's dates are the NEXT edition's, and kept
  as held they put ICMA 2026's exhibitors under Going to 2027 (blind
  review). Both boxes ignore held dates of another year; build_board ships
  them; check_calendar_writers_agree holds the two parsers to them too.
  Maryland Municipal League 2026's (June 14-17, off its exhibits page) moved
  18 companies from "Date not announced" to Went to.
  check_an_undated_row_keeps_the_edition_its_floor_was_read_at: 8 of 8.
- **The box, in order.** The app keyed a prior edition by year x 1e13
  against milliseconds, so every rolled tag sat above every dated past
  edition and the six shown differed from the static page for 12 companies.
  One key now: newest first by start day, a year-only edition as its year's
  FIRST day (below the dated ones of that year), dated first on a tie. An
  undated tag from a year already over is Went to; one this year or later
  says "the YYYY edition, dates not on file". "Date not announced" sorts by
  name in code-point order on both (localeCompare put NACo before NACVSO).
  The check compares the order.
  scripts/show_conferences_box.py prints a company's box off board.json.
- **Every alert issue is assigned** (watchdog, claim gate):
  check_every_alert_issue_is_assigned_to_the_owner reads every `gh issue
  create` (and `gh api` POST to /issues) in the workflows, across
  continuations, either spelling of the owner. 4 of 4.
- **/api/submit is capped**: 5 a caller a day, 40 for everyone, JSON by its
  media type ("text/plain;application/json" is text/plain), refused with no
  KV to count in. A full day is read first and writes nothing, and says it
  is the form's day that is full. scripts/submit_harness.mjs imports it, and
  the check now runs tests/test_submit.mjs too (its URL, honeypot, @mention
  and leak cases broke unseen when the caps landed).
  check_a_submission_cannot_flood_the_public_repo: 8 of 8.
- **Gate stamps are per checkout**: data/.cards_read, .rivals_read,
  .profiles_read (and .buyer_read) are untracked and gitignored, and a bare
  `promote_cards --land` is refused: `--keys FILE` or `--all-gated`. The
  stamp holds 12 hours (`GATE_HOURS`): the weekly scrub reuses its worktree,
  so last Sunday's untracked stamp would pass this Sunday's --land.
  check_a_gate_stamp_belongs_to_one_checkout: 4 of 4.
- **An agency is none, at the reader**: read_floor's fifth verdict `agency`
  (a city, county, state department, regional council, port or transit
  authority) is filed by intake with `not_a_supplier`. "an agency" had been
  in the supplier verdict's own definition since 2026-10-10, so the APA
  floors filed 35 government bodies as suppliers and every floor read since
  had filed more. 156 are pulled (supplier_record_rulings.json, journalled,
  by agent:wave0-2026-10-28): the 35, then 121 found by sweeping all 494
  supplier rows whose name or reader's reason suggested a public body, a
  reader and a skeptic agreeing on each, none disputed. 14 of them were
  published to HQ (WSSC Water, Pace, Metra, Houston Public Works, Lehigh
  Valley Planning Commission, CCHCS, four public libraries among them).
  Sellers stay suppliers: HGACBuy, Maryland Correctional Enterprises, the
  university programmes. Miami-Dade Sheriff's Training Center is held for
  the owner (the skeptic's pull was low confidence: it may sell courses).
  Intake now marks a supplier already on file when a reader calls it an
  agency, and says so when a later reader disagrees.
- **The scheduled tasks run one command at a time.** The daily role review's
  first run hung on `cd ~/govtech-dock-launch && git fetch` (a compound in the
  live checkout); the task files now use `git -C <worktree>`, full script
  paths, a fixed scratch directory and no `$(...)`. The worktrees
  ~/govtech-dock-scope and ~/govtech-dock-confscrub were made by hand. The
  end-of-day inbox reads ~/govtech-dock-launch (it read the stale checkout
  for 23 days); the four Sep-30 items were carried over.

## Logos: the nightly finds them, the desk checks the guesses (owner, 2026-11-01)

Owner, on a job card full of letter tiles: "logos?", then "yes, add it to the
nightly". 38 hiring companies had none: 24 never tried, because
`fetch_logos.py` only ever ran by hand, and 14 that failed once. A hand run
found 29, and three of those were wrong (IMPLAN's was a customer's logo off
its case studies, Oracle's two were social-share photos, Merit's was Aspire's
off the website on file). All three came from the page's markup; none from
an icon the site declares.

- **The nightly runs `fetch_logos.py --limit 80` before the build** (its
  step in refresh.yml, `continue-on-error`), so a logo found is on the cards
  the same morning. Never `--retry` there: a site that said 403 yesterday
  mostly says it again; retrying failures stays a person's call.
- **What goes live on its own** is `fetch_logos.LIVE_VIA`: an apple-touch
  icon, a declared icon, a manifest icon - decided by WHERE `candidates()`
  found it, never by how the URL is spelled - and only when it is on the
  company's own host (or a known CDN) and under `LIVE_MAX_BYTES` (100KB, the
  selftest's live ceiling; CI cannot shrink one). Anything else (a markup
  logo, og:image, a guessed path) is parked in `assets/logos/review/`,
  which the build never reads. An SVG that can run (`svg_runs`, the one
  rule selftest's scan also uses) is never written at all.
- **The desk's "Logos to check"** (admin queue `logos`) shows a parked logo
  on both plates. "Use this logo" / "Not theirs" only RECORD a ruling in
  `data/logo_rulings.json`, keyed by the image's sha256 and journaled like
  any ruling (so the publisher carries it and Undo works). The desk never
  moves a logo file: `fetch_logos.apply_rulings()`, at the start of every
  run on main, moves an accepted one up a level and deletes a refused one.
  Keyed by image, so two refusals for one company both stand; a refused
  image is never landed again for anyone, and the next run tries the
  site's next candidate once (`fetch_logos.due`).
- **The desk does not fetch logos.** Its website save called `fetch_one`
  with a 2-tuple and unpacked a dict, so it had been saying "could not reach
  the site" every time; fixed, it would have written files the nightly also
  writes. `due()` looks again when a company's website changes, so the
  nightly does it. The desk serves a parked SVG sandboxed, and GIFs.
- **A hand run pushed during the nightly** no longer stops its push:
  `merge_decisions --resolve` keeps upstream's copy of a logo file both
  sides wrote, and merges `logo_log.json` by company, upstream's entry where
  both looked. The run writes its log every ten companies, and one site's
  exception is one miss, not the end of the run.
- **Refused by content in code** (`NOT_THEIRS`) are the images ruled by eye
  before the desk queue existed.
- Guards: `check_the_nightly_parks_a_guessed_logo_and_lands_a_declared_one`,
  `check_the_desk_rules_on_a_parked_logo`,
  `check_a_logo_both_sides_wrote_resolves_to_upstream`; the SVG scan reads
  the subfolders too, since `build_site` copies them into public/. Blind
  review of d969c7c (2 reviewers, a skeptic per finding): 14 real, all
  fixed; 41 of 42 breakages caught across both commits.

## Conventions

- Company `id` = kebab-case name (parenthetical suffixes dropped).
- `ats.type` ∈ ashby | greenhouse | lever | workable | recruitee | breezy |
  smartrecruiters | bamboohr | workday | rippling | jazzhr | icims | paylocity
  | oracle | jibe | adp | gusto | gem | hirebridge | applicantpro | teamtailor
  | pinpoint | zohorecruit | comeet | jobvite | successfactors | html | unknown
  (`ats.FETCHERS` is the list; the seven before successfactors were read from
  2026-11-21 and had not been added here).
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
- The export is written for SLED HQ, and **HQ does not read it yet** (checked
  in HQ's tree on 2026-10-22, every branch). The contract, the 10-05 taxonomy
  change and the 10-19 rebuild are in `docs/hq-supplier-export.md`. It has
  fixed sector keys, sector-qualified category slugs, and a status per
  supplier. It carries no addresses: 67 supplier "names" are email
  addresses, and `clean_name` plus a scrub keep them out.
- **The export follows every intake** (owner's list, 2026-10-22, item 28).
  Built 10-05, it still listed 1,248 removed suppliers on 10-19 and had
  never placed 502 new ones; rebuilt 10-19, it was 511 behind three days
  later. `scripts/supplier_refresh.py --write` runs the three steps in order
  and stops at the first failure: `supplier_identity.py --ids` for the
  suppliers never judged, `supplier_pages.py --write` for trusted sites with
  no page yet, then `supplier_categories.py --write`. **Steps 2 and 3 run on
  every --write**, new ids or not: a floor whose exhibitors are all on file
  only adds tags, and a tag is a sector assignment (14 of 54 intakes in the
  journal filed no supplier and tagged up to 1,073). A rebuild keeps a
  `merged_into` row whose supplier has left the file (`carry_merges`); the
  first refresh dropped five and they were restored. Step 1 also takes a
  supplier judged under a website that has changed since (`website_was`): a
  merge's survivor inherits its twin's address, and Hill's Pet Nutrition and
  NACA stayed held as "not found" with their own website on file until it
  did (2026-10-26). First run 2026-10-22:
  511 placed, 3,235 publish, 4,260 held, 110 merged, 77 not a company. `conference_intake.py`
  runs it after every write (`--no-hq-export` skips it; a failed rebuild is
  reported, never a failed intake). selftest prints a note while the export
  is behind, counting a `merged_into` row as placed, not gone
  (`supplier_categories.drift`).
- **The page cache is one per machine**, not per checkout:
  `~/Library/Application Support/sled-jobs/supplier_pages`
  (`supplier_pages.cache_dir`; `SUPPLIER_PAGES` names another; a machine
  without it, CI, uses the checkout's gitignored `data/supplier_pages/`).
  A fresh worktree used to lack the 107 MB cache, and only one checkout
  could rebuild. Copied there 2026-10-22 from both old caches; with it,
  today's code reproduced 7,165 of the 10-19 export's 7,166 old rows exactly
  (the other was released by a person since).
  `--write` still refuses when the cache is missing, or when a supplier the
  last export published has no page:
  `check_the_hq_export_never_loses_a_supplier_to_a_missing_cache`.
  `check_the_supplier_export_follows_every_intake` holds the rest.
- **What HQ's door refused is fixed here, not there** (2026-10-23). HQ's
  first read (main 126d1cf) refused three rows, and its count of 76
  published suppliers with no placement was a bug of ours:
  - A link wrapper is the address it opens (`unwrap`: Outlook safelinks,
    Google's `/url`). A website on a shared host or someone else's site is
    held: `the website on file is not a domain of their own` (`not_own`,
    with HQ's `websiteDomain()` lists). A bare host with no scheme is still
    a domain, and a mistyped one is unread, not someone else's.
  - An own-site quote is never a sentence carrying a phone number (read the
    way HQ's `contactSpans` reads one) or an `@`: HQ calls "Sparks@Play"
    an address. `build()` writes any phone left in a quote or a name as
    `[phone]`.
  - A category the schema does not hold is now said on stderr, never
    dropped in silence: VOCAB split "Park Assets, Forestry & Visitor Data"
    into two names and lost 76 suppliers' only placement.
  - After a change here, run HQ's own `parseSupplierExport` over the file
    (read only, no database) before telling HQ it will pass.
  - A published website is always a lower-case http(s) address (a bare
    host gets `https://`, as supplier_pages reads it; `&amp;` reads as `&`); one that is not a web
    address at all is `website unread (the address on file is not a web
    address)`, so it stays in the admin's Supplier websites queue.
  - A shelter is a park's unless an animal's ("animal shelters",
    "shelters and rescues").
  - These are cases in `check_suppliers_land_in_the_govtech_categories`.
    A blind review (3 lenses, a skeptic each) found 13 more ways to publish
    a row HQ refuses, each now a case; mutation-tested 33 of 33.
- **One company is one supplier, and an agency is none** (owner, 2026-10-23:
  "yes" to merging the duplicates and pulling the agencies). 62 websites
  were shared by 134 published suppliers, and HQ's claim keys on a domain.
  - Each group was read by an agent and challenged by a skeptic told to
    refute every merge and pull. The skeptic overturned 2: Sodexo Campus is
    Sodexo's higher-education division, and Bremerton Housing Authority/CMS
    sells a service. The rulings, with reasons, are
    `data/supplier_record_rulings.json`; `scripts/merge_supplier_records.py`
    applies them.
  - A merge moves the twin's shows and names onto the survivor, and every
    field the survivor lacks (website, the description's words, location,
    year, the govtech/research/vendor-type stamps): the first version kept
    the emptier row and six card-ready groups lost their only website. The
    twin leaves suppliers.json through the journal; the export says
    `merged_into` the survivor (49 folded into 45). The floor reader
    (`read_floor.settled`) and the card door (`_accept_card`) match a
    supplier's aliases too, or a merged name came back to both.
  - Brands or divisions of one parent on its site (Flygt and Sanitaire on
    xylem.com) are not duplicates and stay.
  - A government body that BUYS - a department, a federal agency, a utility,
    a school district - stays on file with `not_a_supplier`, so intake still
    knows the name, and the export calls it not_a_company (12). A public
    body that sells to government (a degree programme, Connecticut
    Correctional Enterprises) stays a supplier.
  - `check_one_supplier_is_one_record_and_an_agency_is_none`.
- **A bare host is read over https** (2026-10-23). 23 suppliers' websites
  were printed with no scheme ("www.cleanharbors.com"): the identity check
  retried them, but supplier_pages handed them over bare and the fetcher
  refused every one, so they sat as "website unread" for three weeks.
  `supplier_pages.site_for` and `supplier_identity.check_own` add `https://`;
  re-read, 18 had pages and 13 now publish. The other 5 are robots.txt bot
  walls or SSL failures, honestly unread.
- **A corrected website is read again** (2026-11-16). The second
  shared-website audit (reader + skeptic, every page through read_page.py)
  folded 17 twins (`supplier_record_rulings.json`, decided_on 2026-11-16),
  gave 5 suppliers their own site in place of another company's (Cunningham
  Recreation off gametime.com, Transit Marketing Group off a Tucson
  consultancy's transitmarketing.com, two IAFC divisions off iafc.org), and
  put 2 unread leads in the Supplier websites queue (Butterball Foodservice,
  the Canadian fire chiefs). supplier_pages passed over the corrected ones
  because a page file existed, and the export quoted the OLD site as their
  own: HQ's parseSupplierExport refused 3 placements. `supplier_pages.stale`
  re-reads pages that came off another host, and `own_text` never quotes
  them (held `website unread (pages read from ...)` until read).
  `check_a_corrected_website_is_read_again_before_it_is_quoted`. Kept apart
  on purpose: brands and divisions on a parent's site (xylem, sodexo,
  hubbell/Beckwith, EXL Health, Komline, GEO Care, Nationwide, PPG, Progress,
  CGI, Chart) and Fusion Learning Partners' two programmes on fusionlp.org.

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

**The refresh self-tests the board its own crawl built (owner, 2026-10-25:
"add the news check to the refresh").** Its first self-test reads last
night's board, so a defect tonight's crawl introduced went live unchecked: on
10-23 a Recruitee range with no period shipped and three news runs failed on
it before anyone knew (10-25's two failures were the debut check's comma
split). The second run is the LAST step, after the data commit and the
digests, `if: !cancelled() && steps.build.outcome == 'success'`: it only
shouts, like the collapse alarm, because refusing the commit would throw away
a night's crawl. `check_the_refresh_tests_the_board_it_built` (5 of 5).

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

**The render queue: recent yielders first, then the rotation (2026-11-19).**
After the parallel fetch, the html boards whose plain read failed are handed
to one browser until `--render-budget` (900s in the nightly) is spent:
about 140 a night out of ~920, so each got a turn every four to seven
nights, least recently attempted first (`render_attempts.json`, stamped on
the attempt, never by a dry run). A page only a browser can read publishes
its roles on its turn and nothing between: over 11-03..11-19, 53 of the 152
html companies that showed postings did so on some nights only (Caselle
11-07, 11-14, 11-17; Conduent 11-11, 11-16; UKG 11-13, 11-17), 39 of them
showing nothing on the last night. So `render_order` puts first every board
whose company had a posting on the board in the last `YIELD_DAYS` (21)
snapshots before tonight (`recent_yielders`, read off data/history like
history_first_seen), least recently attempted first among them, then
everything else as the rotation always ran. A page that stops yielding
leaves the front by itself after 21 nights. The cost is a slower turn for
the ~870 pages that have not yielded, where 95% of renders return nothing.
`check_render_rotation` holds the order, the window and the wiring.

**Workday is one service, paced as one** (2026-11-16). Each Workday tenant is
a host of its own, so the per-host gate (`ats._host_gate`) never paced
Workday: its 42 boards went out together from ten workers, and Workday
answered 39 of them `429 rate limited` in both crawls of 2026-11-16. Neither
the build nor the hiring refresh asked again, so 24% of the board read as
companies with no openings and the sanity gate refused to publish it twice;
read alone, the same tenants answered at once. `ats._gate_key` gives every
`*.myworkdayjobs.com` / `*.myworkdaysite.com` tenant one gate and one backoff,
a 429 with no Retry-After still backs its gate off `RATE_LIMIT_PAUSE` (15s),
and `build_board.read_board` and `refresh.check_company` ask once more after
a 429. All 42 read through the shared gate from a laptop: 305 requests, 156s,
0 refused. `check_workday_is_paced_as_one_service_and_a_429_is_asked_again`.

**Workday is read on the owner's Mac** (owner, 2026-11-17: "Read Workday from
my Mac"). The pacing above was not the CI cause: on the 2026-11-17 re-run,
with the shared gate live, Workday still answered 429 to 39 of 42 tenants -
from the FIRST request of the hiring step (Motorola, no description reads; CI
never passes `--details`) - while the same code read all 42 from the Mac that
hour. GitHub's runner addresses are shared with every scraper on Azure; no
crawl code changed between the last good run (11-15) and the first failure.
- `scripts/workday_snapshot.py --push` runs on the Mac (launch agent
  `deploy/launchd/com.sledjobs.workday.plist`, 23:40 and 07:10 ET, in its own
  worktree `~/govtech-dock-workday`, log `~/Library/Logs/sledjobs-workday.log`):
  it reads every Workday ref on file with the same crawler rules and commits
  `data/workday_snapshot.json` alone. `--push` refuses a checkout with any
  other change and stands on `origin/main` before reading.
- `refresh.yml` sets `GOVTECH_DOCK_WORKDAY_SNAPSHOT=1` for the whole job, so
  `ats.fetch` takes a Workday board from the file when the Mac read it in the
  last 48h (`WORKDAY_SNAPSHOT_MAX_AGE`). A board the Mac could not read raises
  the Mac's error, said as the Mac's; one it has not read in 48h, or never
  (a Workday company added today), is asked live, and that failure names the
  snapshot's age. Off by default: the desk and the Mac's own read are live.
- A bad night on the Mac hides nothing: a refusal for now (429, timeout, a
  5xx or a maintenance page - Workday DOWN is not an answer) keeps that
  board's last good rows with THEIR date, so they still age out; a real error
  (404, robots) is recorded; and when most boards were not read fresh tonight
  in any way, nothing is written, so the file's age stays honest. Known
  property: a kept board's rows (under 48h) reach the crawl like a fresh read.
- The watchdog judges every board's own `read_at`, never `generated`, and
  faults when the median board is 20h old (`watchdog.workday_snapshot_faults`):
  it runs once a day at 15:00 UTC and the crawl after it can read Workday ~23h
  later, so 20h warns a day before the 48h cutoff (36h, as first written,
  warned after the loss). One board stuck 36h+ is a note.
- First version reviewed blind by three readers (2026-11-17); all three found
  the same two defects: a night that read nothing still wrote a fresh
  `generated` (the guard counted only transient errors, and every board is
  "kept" from night two), and a Workday outage replaced good rows with errors.
- The longer-term options put to the owner the same day: carry a refused
  board's last-good postings forward so one source never freezes the deploy,
  and move this job to an always-on machine on a home connection.
`check_workday_is_read_from_the_owners_mac` (16/17 mutations; the 17th is equivalent).

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
- **Read a floor before filing it:** `python3 scripts/read_floor.py brief --out brief.json`
  → agents judge (vendor / supplier / agency / not_a_company / unsure: a
  government body that buys is `agency`, one that sells to government is a
  supplier) → `python3 scripts/read_floor.py apply verdicts.json --write` →
  `conference_intake.py`.
- **Fold supplier rows that are board companies:** `python3 scripts/merge_supplier_twins.py`
  lists twins (a supplier whose name or alias, by intake's norm(), is a board
  company's) and `--write` moves their exhibited-at tags onto the company,
  takes the row out through the journal and marks the HQ export
  `merged_into`. A researched row, one on another website or one matching two
  companies waits for a person; `--pair SUPPLIER_ID=COMPANY_ID` once somebody
  has looked. Selftest prints a note while any twin is on file.
- **Research cards through the door:** `agents.py brief --kind card` → research
  (fetch with `python3 scripts/read_page.py URL`) → `agents.py ingest --kind card`
  → `promote_cards.py --verify`, `--second-read reads.json`, `--gate` (it holds
  12 hours), `--land --keys keys.json --by owner` (the round's own cards;
  `--all-gated` lands every card that passes, the held pile included).
- **Discover an ATS (JS-walled board):** `python3 scripts/discover_js.py noats`
  renders each Unknown in headless Chromium and prints the ATS endpoint its
  board actually calls. Needs `pip install playwright` + `python -m playwright
  install chromium`, which is **why it's a separate script**: the browser is a
  one-off discovery tool, its conclusions get written into `companies.json` as
  normal `ats` entries, and `refresh.py`/CI stay stdlib-only. Always verify a
  slug with a real fetch before writing it — most Lever slugs are lowercase
  but Lever minds case (`Wayground` answers, `wayground` is a 404), and an
  off-site careers link occasionally lands on another company's board.
- **Wire the boards a finder found:** `wire_found.py plan` to a scratch
  folder, read the plan, `apply` for the look, have the WIRE rows read
  blind (a doubt goes back in with `plan --doubts <file> --by
  agent:<label>`, which keeps it in data/board_doubts.json at once), then
  `apply --write --expect <code>` of the doubted plan's worklist; commit
  the data files and the journal, and
  tell the owner what was wired, what is in Boards we found, and every SKIP
  the look listed. Never `discover_ats.py --confirm`, `act_set_board` or a
  bare `find_embedded_ats.py` for this.
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
