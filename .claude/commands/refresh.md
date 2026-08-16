---
description: Refresh AE hiring status for every company, then summarize the diff
---

Run the hiring refresh and report what changed:

1. `python scripts/refresh.py`
2. Read the diff it prints (also written to `data/latest_diff.json`).
3. `python scripts/export_xlsx.py` so the spreadsheet matches.
4. Summarize for the owner in prospecting terms: which companies flipped to
   "Yes" (these are the headline — name the role and territory from the note),
   which went quiet, and anything now Unknown that was readable before
   (a possible slug change worth re-discovering).
5. If any companies were listed as "needs ATS discovery", mention the count and
   offer to run /discover-ats.

If the run errors on a specific fetcher, fix scripts/ats.py, add a selftest
case if it was a classification issue, and re-run before summarizing.
