# Two findings logged, not applied

Both were researched on 2026-09-09 and recorded through the admin's
suggest door into `data/logic_notes.json`. **Nothing on the board was
changed.** Each needs a ruling before anything moves.

---

## NEOGOV & PowerDMS by NEOGOV

- **record id** `neogov-powerdms-by-neogov`
- **queue** missing-websites
- **logged** 2026-09-09T14:50 by owner

**What the card showed**

Missing-websites card: Parks & Rec / Suppliers & Services, no website,
description says HR, policy and training software for government - exhibited
at NRPA 2026

**Finding**

NOT A COMPANY - a trade-show booth name. Two brands shared a stand at NRPA
2026 and the exhibitor sweep filed the booth as one entity. Verified from
their own site: governmentjobs.com/careers/neogovpe carries a privacy notice
naming the legal entity as Governmentjobs.com, Inc. (DBA NEOGOV), with related
brands NEOGOV.com, NEOED.com, PowerDMS.com, Governmentjobs.com and
Schooljobs.com. So PowerDMS is a BRAND of NEOGOV, not a subsidiary and not a
peer. Three records exist today: neogov (the real parent, neogov.com, founded
2000, General Gov / HR & Workforce), power-dms-by-neogov (powerdms.com, Public
Safety / Suppliers & Services), and this booth record with no website at all.
ONE SHARED BOARD: powerdms.com/careers links to
governmentjobs.com/careers/neogovpe, which is NEOGOV hiring on its own
product. It is JS-rendered - 0 job links in the HTML, 20 after Chromium
renders it - so ats.py cannot read it as html. governmentjobs.com is not in
ATS_MARKERS and no other company on this board uses it, so it is not worth an
integration. Proposed, NOT APPLIED, owner asked to log only: merge this booth
record away, keep neogov as parent with the neogovpe board, keep power-dms-by-
neogov as a brand pointing at the same board, and record the parent link.

---

## iSport360

- **record id** `isport360`
- **queue** acquisitions
- **logged** 2026-09-09T14:59 by owner

**What the card showed**

Parks & Rec / Youth Sports & Leagues, isport360.com, founded 2016, ats
unknown, source: competitor sweep. Their About page banner reads: iSport360
was recently acquired by Signature Media!

**Finding**

ACQUIRED, and the acquirer is not a govtech vendor - this is a scope question,
not a merge. Their own announcement: Signature Athletics has officially
acquired iSport360 ... This acquisition marks the official launch of Signature
Media, the content-to-commerce division of Signature Athletics. So the
acquirer is Signature Athletics (signaturelocker.com), whose own meta
description reads: Signature Athletics offers custom, on-demand uniforms and
sideline swag for Soccer, Field Hockey and Lacrosse programs. That is DTC
apparel and team stores, not software sold to a rec department. Signature
Media is a division launched by the deal, not the buyer - the homepage banner
naming it as the acquirer is loose. Neither Signature entity is on this board.
iSport360 has no careers page (404 on /careers and /about/careers) and its ats
is unknown; signaturelocker.com/pages/careers answers 200, so any hiring is
likely to move there. iSport360 is NOT in the acquisitions queue today because
nothing recorded the deal. Proposed, NOT APPLIED, owner asked to log only. TWO
QUESTIONS FOR THE OWNER, both scope calls: (1) does a govtech product acquired
by a consumer apparel company stay on this board, given the announcement
repositions it as content-to-commerce rather than software a Parks and Rec
department buys; (2) if it stays, is Signature Athletics added as its parent
even though the parent is not govtech and would never qualify on its own.

---

## 3. City Detect — and Gem is a readable ATS nobody knew about

- **company** city-detect (on file, sector General Gov / Permitting & Licensing)
- **queue** boards / coverage
- **logged** 2026-09-09 by owner
- **status** RESEARCHED, NOTHING APPLIED

### Two corrections to what I told you earlier

**I said City Detect had no openings. It has two, and they were already on the
public board** before I looked — Account Executive and Business Development
Representative, both live rows with working links. The board was right and my
log was wrong.

**I said `jobs.gusto.com` was a 403 Cloudflare wall. It is not.** All three
Gusto URLs answer 200 (board index 14 KB, both postings ~17 KB) with a browser
user-agent. The 403 was user-agent dependent, not a wall. That is the same
shape as the APWA/GMIS finding already in CLAUDE.md, and I should have
retried before recording a refusal.

### What is actually true

City Detect runs **two live boards in parallel**:

| board | roles | readable? |
|---|---|---|
| Gusto `city-detect-6e1a4cdf…` | Account Executive, BDR | yes, plain HTML, with salary |
| Gem `jobs.gem.com/citydetect` | Account Executive, BDR, Full Stack Product Engineer | yes, via a JSON API |

The careers page embeds the **Gem** board in an iframe; the Gusto board is
reachable directly and still current. The two sales roles appear on both — the
same requisitions, advertised twice. The third Gem role is engineering, so it
is not quota-carrying and its absence costs the board nothing.

### The finding worth more than the company: Gem is enumerable

Gem renders nothing to a plain fetch — every page, board index and individual
job alike, is the same 4 KB JavaScript shell with **zero** JobPosting JSON-LD.
That is why a page scan finds nothing. But the shell fetches its data from one
public endpoint, and that endpoint needs no auth at all:

```
POST https://jobs.gem.com/api/public/graphql/batch
Content-Type: application/json
```

No API key, no cookie, no token — only a `Referer`. It returns 1.6 KB of
structured JSON: title, all locations, department, employment type and a
remote flag per posting, plus the board's display name.

**It also distinguishes absence from emptiness**, which is the property this
project requires before it will assert a status:

- a real board with nothing open (`token-transit`) → board object, `0` postings
- a slug that does not exist → `board: null`

So a Gem fetcher can honestly say `None found` where a page scan could only
ever say `Unknown`. That is the difference between a finished state and a gap.

### What this unlocks, and what it does not

`ats.py` has never heard of Gem — no fetcher, not in `coverage.STRUCTURED`.
One company, **token-transit**, is filed as `ats.type: html` pointing at
`jobs.gem.com/token-transit`, so every refresh page-scans a JS shell and
learns nothing. Its board is genuinely empty right now, but we cannot
currently tell that from an unreadable one.

Only 2 companies are known to be on Gem today, so adding the fetcher buys
little immediately. Its value is that Gem stops being a dead end, and any
company found on it later is readable for free. Whether more of the 828
page-only companies sit on Gem is unknown — finding out means probing Gem
with ~800 slugs, which is a lot of traffic at a third party for an unknown
return. Not done, and not recommended without a reason to think the hit rate
is worth it.

### Defects on the record, none corrected

1. `ats.ref` and `posts_at.url` both point at the **Gusto** board. That is a
   real live board, so this is not wrong — but the careers page a visitor
   actually opens shows **Gem**, and nothing on the record says Gem exists.
2. `hiring.roles` carries a synthetic placeholder, `"AE-type role (page
   scan)"` with `synthetic: true`, linking to the Gusto board index. **This
   does not reach the public board** — `build_board` filters synthetic rows
   and 0 of 6,707 postings carry the flag — so no reader ever sees it. It is
   stale state on the record, not a public defect. 100 companies carry one.
3. **The salary is published and we are not showing it.** The Gusto posting
   states `$85,000 - $95,000 per year` for the Account Executive. Our row has
   `comp: null` and `jd_seen: false`, because both rows came in through the
   manual capture path (`source: manual`, `first_seen: 2026-09-09`), which
   records the title and never opens the JD. Wiring the Gusto board as a real
   `ats` entry would let `salary.py` read that range on the next refresh.

### If you want it applied

The smallest correct change is (a) add a `gem` fetcher to `ats.py` and to
`coverage.STRUCTURED`, (b) repoint token-transit from `html` to `gem`, and
(c) leave City Detect's `ats` on Gusto but record Gem as a second board so
the two sales rows are not counted twice — `opening_id` already collapses
them by title, but two sources would produce two rows for one advertisement,
which is the Xplor shape CLAUDE.md warns about.

Say the word and I will build it behind a selftest case and mutation-test the
guard. Nothing above has been applied.
