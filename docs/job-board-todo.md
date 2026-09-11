# What the job board needs

Written 2026-09-11, against the board as it stands that morning: **2,044 companies,
6,405 postings, 358 companies actually posting.**

Two rulings from the owner govern everything below.

1. **Scope is all of SLED** — state agencies, counties, cities, towns, special districts,
   K-12 districts, universities and colleges, and transit / airport / water / port
   authorities. Federal-only, defence and commercial come off.
2. **Mixed vendors get read per role.** A company that sells to government *and* elsewhere
   does not get a blanket answer; the job description decides.

---

## Where the board actually is

| | |
|---|---|
| companies | 2,044 |
| companies with a live posting | 358 |
| postings | 6,405 |
| …of which are known non-US | 1,379 |
| …of which state no location at all | 1,869 |
| companies with a researched buyer | 87 of 358 |
| companies marked `sled_only` | 128 |
| job descriptions cached | 918 — **none of them for a posting currently live** |

That last row is the one to sit with. `read_descriptions.py` exists and works, but its
cache is from August and every posting it read has since rolled off the board. For the
purpose of deciding scope, **we currently hold zero job descriptions.**

### The three populations of postings

| | postings | what decides them |
|---|---|---|
| at a `sled_only` company | 573 | nothing — post everything |
| at a researched **mixed** company | 2,086 | the JD, per role |
| at a company we have never researched | **3,746** | unknown until the company is read |

**58% of the board sits in the third row.** No amount of per-posting cleverness helps
there, because we do not yet know what those companies sell. The company pass has to come
first.

---

## 1. Finish the company pass — the thing everything else waits on

**271 of the 358 posting companies have never been researched.** The transit run (200
companies) and the scope pass (76) between them settled 87. The method is proven: read the
company's own site, write one sentence naming who buys, record the page it came from.

- **Do it in batches of ~75**, ordered by posting count. The top 40 companies carry over
  half the board's postings, so the first batch buys the most.
- **Output is three fields, and they already exist:** `buyer` (a sentence), `sells_to_gov`
  (yes / unclear / no), and `sled_only` — set only where the site names no non-government
  buyer at all, with the sentence that justified it stored beside it.
- **`unclear` is a real answer.** A site that names no public buyer is unclear, not no.
- **Watch for two traps the pass has already hit.** A stated "Enterprise" or "Mid-Market"
  segment says nothing about whether a buyer is public — a big city is enterprise SLED and
  a small town is SMB SLED. And campuses, universities, school districts, libraries and
  airport authorities are **public**; an early cut of the rule treated "also campuses" as
  disqualifying, which would have thrown out the E in SLED.

Roughly four runs of the shape already built. Each produced usable corrections beyond
scope — the transit run found nine records that should not have been standalone, and the
scope pass found that Xplor now brands itself nextRec and that Bruker's URL points at the
parent corporation rather than the detection division.

## 2. Read the JDs — the ruling, and the expensive part

Only for **mixed** companies. A `sled_only` company posts everything; an unresearched
company is not ready for this step.

**Build it as its own bounded job, modelled on `read_descriptions.py`.** Never inside the
daily refresh — that crawl already takes twenty minutes against other people's servers,
and `ats.FETCH_DETAILS` is off for exactly this reason.

Requirements, in order of how badly they bite:

- **Three answers, never two: in scope, out of scope, and could not tell.** A JD that 403s,
  renders empty, or simply never says must land in the third bucket and stay visible. A
  fetch failure becoming "not SLED" deletes a real job and nobody ever sees it. This is the
  same rule that governs every fetcher here.
- **Cache the verdict against the posting's identity**, so a posting is read once and only
  re-read when it changes. The current `jd_cache` is keyed in a way that did not survive
  postings rolling over — fix that before building on it.
- **Record the sentence the verdict came from**, the way `sled_only_why` does. A scope call
  with no quotable evidence cannot be argued with or corrected.
- **Cost**: ~2,086 postings at mixed companies today, growing as the company pass settles
  more. One fetch and one call each, incremental after the first pass.

## 3. The deterministic drops — free, and already available

**1,510 postings can come off today with no AI at all**, because they are labels the
employer chose:

| | |
|---|---|
| known non-US | 1,379 |
| federal or defence only in the title | 65 |
| a market that is not government (commercial, retail, banking, automotive) | 58 |
| a region outside the US (EMEA, DACH, APAC…) | 8 |

Geography is already shipped — the board defaults to "United States" as of 2026-09-10, and
keeps the 1,869 unplaced rather than deleting them. **The other 131 are not shipped**,
because they are a scope claim rather than a geography fact, and now have a ruling behind
them.

Two rules the filter must carry:

- **"Federal" cannot be a blind drop.** *"Applied AI Architect, Public Sector (National
  Security)"* names both; anything naming state, local or public sector alongside federal
  stays.
- **Deal size and sales motion are not markets.** Enterprise, Mid-Market, SMB, Strategic
  Accounts, Channel, Partner, Inside Sales — 522 postings carry one of those and every one
  of them can be a SLED job.

## 4. Fix the `jd_cache` key

918 descriptions, zero matching a live posting. Whatever it is keyed on does not survive a
posting rolling off and coming back. Worth an hour before item 2 is built on top of it,
because item 2's whole economy depends on reading each posting once.

## 5. The company-level corrections the passes keep finding

Every research run turns up records that are wrong in a way scope does not capture. These
are cheap and they compound:

- **Xplor Recreation** now brands itself **nextRec**; the record carries the old name.
- **Bruker Detection**'s URL points at the parent scientific-instruments corporation, whose
  homepage sells to academia and biopharma and never mentions detection.
- **Omnitracs** posts into Solera's global Workday board — 188 jobs across Audatex,
  Identifix and the rest. Deliberately unwired; it needs a brand filter or a person.
- **UVeye, KPA, Banyan Water** came back selling to nobody public. Awaiting a ruling.
- **21 companies** sit at `sells_to_gov: unclear` and want a second look.

---

## The conference side, which feeds the board

48 of 139 floors read. **91 in the admin queue**, now filterable by kind because they are
four different jobs:

| | | |
|---|---|---|
| **ready** | 2 | has a directory URL, unswept |
| **wrong url** | 20 | the URL we hold is the association's own site |
| **blocked** | 6 | somebody tried; the reason is on the row |
| **no directory** | 63 | no exhibitor list has ever been found |

**The 63 are the real work now.** The sweeping is nearly done; the *finding* is not, and it
is the part that needs judgement rather than a fetcher.

**The 20 wrong-url rows are a warning worth keeping.** Those URLs pointed at association
navigation, and five were swept on 2026-09-11 producing an office address, "Cats", a staff
member's name, Facebook and Instagram, and a **login page**. All were graded `doubtful` by
the old rule — which nothing enforced — so all 233 names would have been written. Both
`conference_intake` and `classify_exhibitors` now refuse `doubtful` as well as `menu`.

### Downstream backlog

**924 research candidates and 7,868 suppliers.** A candidate is not a company: it needs
research and a person's ruling before it reaches the board. The classifier that sorts them
scores itself at **55% precision, 14% recall** — that is what a company name is worth as a
signal — and errs toward supplier on purpose, because a supplier stays visible and
promotable while a false candidate costs an hour. So expect real vendors sitting in the
7,868, and expect half the 924 to be nothing.

---

## Order, and why

1. **Finish the company pass** (271 companies). Everything else is blocked on it —
   3,746 postings, 58% of the board, sit at companies nobody has read.
2. **Ship the 131 deterministic drops.** Free, ruled on, ten minutes.
3. **Fix the `jd_cache` key.** An hour, and item 4 cannot be economical without it.
4. **Build the JD scope reader** for mixed companies, with its three-way verdict.
5. **The 63 conferences with no directory.** Independent of the above; do it whenever.

Items 1 and 5 are the two that genuinely need judgement at scale. Items 2, 3 and 4 are
engineering with a clear spec.

---

## What this deliberately does not do

- **Does not touch the 1,869 postings with no location.** They are visibly unknown, not
  claimed as American. Bruker Detection has 16 postings, 0 confirmed US and 10 unplaced —
  a strict `is_us === true` default would show a reader nothing where ten plausible
  American roles sit.
- **Does not remove a company on a machine's say-so.** UVeye, KPA and Banyan Water have the
  evidence on the record and are waiting for a person.
- **Does not put a JD reader in the daily refresh.** It is a separate, resumable,
  killable job, and the board is exactly as correct on a day it does not run.
