# The supplier export, for SLED HQ

SLED JOBS sorts the conference suppliers it holds into the govtech sectors and
categories its companies already use, and publishes the result as one file,
`data/supplier_categories.json`, for SLED HQ to read. This document is the
contract between the two. HQ reads it the way it already reads
`conferences.json` and the exhibitor files: from this public repository at a
commit it pins. HQ never writes this repository.

The code that produces it:

- `scripts/supplier_identity.py` (who each supplier is, and whether the
  website on file is theirs) writes `data/supplier_identity.json`;
- `scripts/supplier_pages.py` reads each trusted website into a page cache
  that is never committed;
- `scripts/supplier_categories.py` writes the export;
- `scripts/supplier_refresh.py` runs the three in order. Since 2026-10-22 it
  runs after every conference intake, so the file follows the suppliers on
  file instead of going stale. The guard is
  `check_the_supplier_export_follows_every_intake` in `scripts/selftest.py`.

As of HQ's tree on 2026-10-22, nothing in SLED HQ reads this file yet, on any
branch.

## What changed (2026-10-05 and 2026-10-19)

- **The taxonomy is the board's own** (owner, 2026-10-05: "throw them in the
  categories we have for govtech"). Suppliers sit in the 12 sectors of
  `data/schema.json` and their categories. There is no separate industry tree.
- **The file was rebuilt on 2026-10-19.** The 10-05 build still listed 1,248
  suppliers that had since left the file (163 of them published, 72 of those
  now board companies) and had never placed 502 new ones. After the
  rebuild: 3,048 publish, 3,941 held, 106 merged and 77 not a company.
- **It now follows every intake** (2026-10-22, above). The first refresh
  placed the 511 suppliers filed since 10-19: 3,235 publish, 4,260 held,
  110 merged and 77 not a company.
- **What HQ's door refused at 126d1cf is fixed at the source** (2026-10-23).
  HQ refused two published websites with no domain of their own and one
  placement whose quote held a phone number; its count of 76 published
  suppliers with no placement was ours too. Now:
  - a link wrapper (Outlook safelinks, Google's `/url`) is the address it
    opens: `www-youngwilliams-com` publishes `http://www.youngwilliams.com/`,
    and its own-site quote cites that page;
  - a website on a shared host or someone else's site (a site builder's
    subdomain, a preview build, `github.com/acme`, a mail filter's redirect)
    is held, with the new reason `the website on file is not a domain of
    their own`. The lists are the ones in HQ's `websiteDomain()`;
  - a published website is always a lower-case `http://` or `https://`
    address with `&amp;` read as `&` (two published addresses carried it);
    an address that is not a web address at all is held as
    `website unread (the address on file is not a web address)`;
  - an own-site quote is never a sentence with a phone number or an `@` in
    it, and any phone that reaches a quote or a name is written `[phone]`;
  - the playground and site-furnishing makers are filed under
    `parks-rec/park-assets-forestry-and-visitor-data`, which the sorter
    had been naming as two categories the schema does not hold (80 new
    placements). An animal shelter is not a park shelter, so two suppliers
    whose only parks words were about animal shelters are now held.
  HQ's own parser (`parseSupplierExport`) over the rebuilt file: 3,232
  published suppliers usable, 3,429 placements, none refused, and no
  published supplier without a placement.

## The file

```json
{
  "v": 1,
  "generated": "2026-10-19",
  "note": "...",
  "sectors":     [{"key": "public-safety", "name": "Public Safety"}],
  "industries":  [{"slug": "public-safety/fire", "name": "Fire", "sector": "public-safety"}],
  "suppliers":   [{"supplier_id": "1-800-sweeper", "name": "1-800-SWEEPER",
                   "status": "publish", "website": "https://www.1800sweeper.com"}],
  "assignments": [{"supplier_id": "1-800-sweeper", "industry": "public-works/streets",
                   "basis": "own_site",
                   "source": {"url": "https://www.1800sweeper.com",
                              "quote": "Experience the Difference Choose 1-800-SWEEPER"}}]
}
```

- `v` goes up when a field changes meaning or is removed. Adding a field does
  not change it, so ignore fields you do not know.
- `generated` is the date of the build on the clock of the machine that ran
  it (New York time for a build run on the owner's machine), not a UTC date.
- The file is **the whole list** every time. It is not a diff.

### Sectors and industries

`sectors[].key` is fixed: `public-safety`, `public-works`, `general-gov`,
`parks-rec`, `k12-schools`, `transit-parking`, `utilities-energy`,
`airports-aviation`, `courts-justice`, `health-human-services`,
`higher-education`, `housing-community-dev`. An industry's `slug` is
`<sector key>/<category slug>`, so one category name under two sectors is two
industries. Every sector has a `<key>/suppliers-and-services` industry for a
supplier whose sector is known and whose category is not.

### A supplier

| Field | Notes |
| --- | --- |
| `supplier_id` | SLED JOBS's id for the supplier. Stable. |
| `name` | As the exhibitor list printed it, with any email address and a trailing contact name taken out (`clean_name`). No name in the file carries an address. |
| `status` | `"publish"`, `"held"`, `"not_a_company"`, or `{"merged_into": {"kind": "supplier" or "company", "id": ...}}`. |
| `website` | Present exactly when `status` is `"publish"`. |
| `website_by` | `"person"` when a person at SLED JOBS released the website by hand; absent when the sorter confirmed it (every published row on 2026-10-22). |
| `held` | Present exactly when `status` is `"held"`: the reason, in words (below). |

**Publish only what says `publish`** (owner, 2026-10-04: nothing is
published without a confirmed website). A website counts only when its own
text sells into the supplier's sector. A held supplier may well be a real
company. The reasons, as written (counts on 2026-10-22; the last row 2026-10-23):

| `held` | Count | Means |
| --- | --- | --- |
| `no website` | 2,502 | none on file and none found |
| `website not confirmed by what it sells` | 1,269 | its own text does not sell into its sector |
| `website unread (<detail>)` | 486 | the crawler could not read it, e.g. `(not read)`, `(homepage: only 0 chars of text)`: the crawler's limit, not a fact about them |
| `the website on file is a domain for sale` | 3 | |
| `the website on file is not a domain of their own` | 4 | a page on a shared host or someone else's site, or a link wrapper naming no address (2026-10-23) |
| `a person said the website on file is not theirs` | 0 | a person's ruling |

Match on the start of the string for `website unread`, and treat any reason
you do not know as held. `merged_into` names the record the supplier is the
same company as; `kind: "company"` is a SLED JOBS board company, the same id
HQ's `employers` import uses. A merged row stays in the file after its
supplier has left `suppliers.json` (`carry_merges`), so the word on where it
went is never lost.

**A supplier absent from the file is no longer a supplier on SLED JOBS.** It
may have become a board company (check the employers import) or been removed.

### An assignment

One supplier can have several, one per industry. `basis` is `conference` (the
show it exhibited at says where it sells; the source is the exhibitor list
and the quote is its name as listed) or `own_site` (its own website names the
category in two distinct terms; the quote is the sentence, at most 300
characters, with any address scrubbed, and never a sentence carrying a phone
number or an `@`). Every assignment carries its source; an own-site source
is the page the words were read on, a link wrapper opened to the address it
names.

## How good it is

Measured blind, never on the sample that set the rules:

- 2026-10-05, a fresh 200-supplier holdout: of the published, 93% had the
  right website, 96% the right sector and 76% the exact category.
- 2026-10-19, 60 published suppliers after the rebuild: the website was
  right 56 times and the placement 39 times. The four wrong websites were
  corrected at the source. Placement misses are at the category level.

Known gaps, for the owner to rule on: 211 websites a reader found and a person
has not looked at publish without one; some buyers (universities, transit
agencies, associations) are filed as suppliers.

## Asked of HQ (2026-10-22)

SLED JOBS's competitor lists want outside evidence: a government document
naming two companies on the same solicitation. HQ's bid-record research
(`bid_record_documents`, `bid_record_claims`, and a person's reading in
`bid_record_decisions`) is exactly that, and agenda titles are not (measured
2026-10-22 on HQ's 124,267 agenda items: 202 name two SLED JOBS companies, 12
of those share a category, and on reading almost none were rivals; titles
never list the losing bidders). If HQ publishes a feed of claims a person has
confirmed (the document's address, the company's SLED JOBS id or name as
printed, the role, the solicitation), SLED JOBS would pair co-bidders into
sourced competitor proposals through its own door (`agents.ingest --kind
rival`), and a person there still rules on each.
