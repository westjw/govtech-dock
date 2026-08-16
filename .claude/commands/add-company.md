---
description: Research a govtech company and add it to the dock
argument-hint: <company name or URL>
---

Add $ARGUMENTS to the dock:

1. Research the company — its own site plus funding press. Verify: official
   name, HQ city/state, founding year (press releases beat aggregator profiles;
   note best-effort years honestly), one-line description (what they sell + to
   whom), website.
2. Pick sector + category from `data/schema.json`. If nothing fits, STOP and
   ask the owner before inventing a new sector or category — house rule.
3. Find their ATS: check the careers page source for greenhouse/lever/ashby/
   workday/workable/etc. URLs, then confirm the public API endpoint works
   (patterns documented in `scripts/ats.py`). Fall back to `html` with the
   careers URL, or `unknown` if the page is JS-walled with no API.
4. Append the entry to `data/companies.json` (id = kebab-case name; hiring
   block starts as Unknown/unchecked).
5. `python scripts/selftest.py`, then `python scripts/refresh.py --company <id>`,
   then `python scripts/export_xlsx.py`.
6. Report back: what the company does, the verified facts, its hiring status,
   and anything relevant to a SLED sales job hunt (fresh funding, NYC/Northeast
   presence, GTM team building out).
