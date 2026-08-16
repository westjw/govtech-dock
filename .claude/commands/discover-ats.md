---
description: Upgrade companies with weak or missing ATS entries to structured API checks
---

Work through the data-quality backlog:

1. List companies where `ats.type` is `unknown`, plus any `html` entries whose
   hiring note says `[page scan - verify]`.
2. For each, find the real ATS: fetch the careers page and search its HTML for
   greenhouse.io, lever.co, ashbyhq.com, myworkdayjobs.com, workable.com,
   recruitee.com, breezy.hr, smartrecruiters.com, rippling.com, applytojob.com.
   Probe the matching public API pattern from `scripts/ats.py` to confirm it
   returns jobs.
3. Update the company's `ats` block in `data/companies.json`. If a board is
   genuinely JS-walled with no API (some ADP/iCIMS/custom portals), leave a
   comment-style note in the `ats.ref` URL choice and move on — do not guess.
4. `python scripts/selftest.py`, then `python scripts/refresh.py --company <id>`
   for each upgraded company.
5. Report the before/after count of structured-API companies.
