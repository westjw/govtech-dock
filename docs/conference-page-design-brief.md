# Design brief — conference pages and organisation pages

For SLED JOBS (sledjobs.com). Two new page types. Everything below is a field
that exists in the data today; where a field is missing that is called out,
because designing around a field we do not have is how a page ends up showing
a placeholder forever.

Match the existing company page: Archivo, headings 800, `--radius: 0` on every
control, the 4px spacing scale, `tabular-nums` on every column of figures, and
the Penguin header band that does not flip with dark mode. Palette from
`data/brand.json` only — Ice is the ground and the only white, Belly is cards,
Frost is rules, Fog is tiny uppercase labels ONLY (it is 2.7:1), `--dim` is the
readable secondary tone, Beak is a highlight and is never text.

---

## Page 1 — the conference page (`/e/<slug>`)

One event, one edition. There are 139 published today and ~880 more staged.

### It has to work in five states, not one

This is the whole design problem. Most pages will be state 3 for a long time.

| # | state | how many today | what the page must say |
|---|---|---|---|
| 1 | **swept, exhibitors hiring** | 12 events | the roster, hiring first |
| 2 | **swept, nobody hiring** | some of the 12 | the roster, plainly, no apology |
| 3 | **not scanned yet** | the large majority | "we have not read this floor" — a fact about US |
| 4 | **no dates announced** | 17 published | the event without a date, not a blank |
| 5 | **past edition** | several 2025 tags | still useful: this is who exhibits in that room |

The board's own sentence, already on the Conferences tab, is the tone to hold:
*"An event with no companies beside it is one we have not mined yet — that is a
fact about us, not about the conference."* A page that looks broken in state 3
makes 800 pages look broken.

**A "scanned / not scanned" marker is wanted explicitly**, and it should be
easy to remove later — as floors get read, the not-scanned state disappears and
the marker stops earning its space.

### Fields available now

| field | coverage (of 139) | notes |
|---|---|---|
| `conference` — display name | 139 | |
| `event_tag` — e.g. "IACP 2026" | 139 | permanent, used in company descriptions |
| `url` — the event's own site | 139 | **the ticket/registration link, effectively** |
| `dates` | 120 | a string: "September 9-11, 2026" |
| `dates_confidence` | 139 | `high` 111 · `unannounced` 17 · `medium` 6 · `owner` 3 · `unreachable` 2 |
| `city` | 121 | |
| `block` / `department` | 139 | the two grouping levels the tab uses |
| `flagship` | 59 true | the big ones |
| `swept` | 12 true | has the floor been read |
| `exhibitor_url` | **58** | the floor list itself — **currently shown nowhere** |
| `companies` / `hiring` / `open_roles` | computed | the roster and its hiring count |

**Not on file, do not design around:** logo, attendee count, venue, floor plan,
exhibitor tiers (gold/silver), a "who should attend" paragraph, a price.

### What it must feature

1. **Name, dates, city, and the honest date state.** When dates are missing the
   page says which kind of missing: *"next edition not announced yet"* /
   *"dates unconfirmed — their site would not load"*. Never a blank.
2. **Scanned or not**, prominently, as its own state.
3. **The exhibitor roster** — company name, open-role count, link to the company
   page where they are hiring. Ranked hiring-first. Up to 250; say
   *"showing 250 of 400, the ones hiring first"* when it truncates.
4. **Two outbound links, both real:** the event's own site (registration and
   tickets), and the **exhibitor directory** where we have one. The second is
   the most useful link on the page for a seller and is currently unused.
5. **Add to calendar** — the `.ics` already exists per event and per block.
6. **Past editions** — a small table of prior years with links. The data holds
   `prior_tags` and successive tags like "ICMA 2025" / "ICMA 2026".
7. **A link up to the organisation** that runs it (page 2).

### The two audiences, one page

Owner's ruling: this is the one-stop shop for SLED govcon conferences, so it
serves both.

- **A job seeker** wants: which exhibitors are hiring, right now, with a way in.
- **A seller** wants: who is in that room, is it worth going, when and where,
  and how to get on the floor.

Both need the roster; they read it differently. Do not split the page — one
roster, with the hiring signal clear enough that a job seeker can scan it and
quiet enough that a seller is not reading a job board.

*(Grouping the roster by sector was considered and is deliberately deferred.)*

---

## Page 2 — the organisation page (`/o/<code>`) — NEW

The gap this fills: the catalogue is flat. A reader on "NRPA Directors School"
cannot get to "NRPA Annual Conference", and nobody can ask what an association
runs.

**901 organisations, 232 of them running more than one event.**

Real examples:

- **NRPA** — 9 events: the Annual Conference (published, floor read), plus
  Directors School, Revenue Development School, Green School, Innovation Labs,
  a Legislative Forum and four certification programmes.
- **APA** — 8 events: National Planning Conference, NPC Online, a Policy &
  Advocacy Conference, 47 chapter conferences, division events, webcasts.
- **NAHRO** — 16. **IAFC** — 11. **APPA** — 12.

### Fields available

`code` · `name` · `url` · `event_count` · `published_count` · `swept_count` ·
`harvest_count` · `blocks` · `departments` · `scopes` · `is_a_class` · and the
full `events` list, each carrying its source (`catalogue` / `staged` /
`chapter`), name, status, and whether it is published.

### What it must feature

1. **The organisation, its site, and what it runs** — every event, grouped by
   what it is: conferences with a floor, schools and training, policy meetings,
   chapter events.
2. **Which of those we cover**, honestly: published vs staged vs not read.
3. **A link down to each conference page.**
4. **Chapter events, where they exist**, with their state.

### One state to design for explicitly

**`is_a_class: true`** — ten codes are a CLASS of body, not a body: "State
associations", "State affiliates", "Various". The events under them are real and
belong in the catalogue; the organisation is not one organisation. That page
must not present a name, a website and a history as though it were.

---

## Honesty rules that constrain both pages

These are house rules, not preferences.

1. **A count of zero is never presented as a finding** unless we actually read
   the floor. "Not scanned yet" and "we read it and nobody is hiring" are
   different sentences and must look different.
2. **Never invent a fact to fill a field.** No estimated attendance, no guessed
   date, no placeholder logo. If we do not know, the page says so.
3. **`est_exhibitors` is an estimate and is never a count** — it is not rendered
   anywhere today and should stay that way.
4. **Every page must work at 375px** with no horizontal scroll, and in dark mode.
5. **The static page is what a crawler reads.** Whatever is essential must be in
   the HTML, not drawn by JavaScript afterwards.

---

## What I need back

Artboards in the Design 6 style (PDF + screenshot) for:

1. Conference page — **state 1** (swept, exhibitors hiring)
2. Conference page — **state 3** (not scanned yet, dates known) — *the common one*
3. Conference page — **state 4** (no dates announced)
4. Organisation page — a multi-event body (NRPA or APA)
5. Mobile at 375px for the conference page

If only two are possible, make them **state 3 and the organisation page** —
state 1 is the easy one and state 3 is 800 pages.
