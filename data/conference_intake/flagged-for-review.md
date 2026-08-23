# Flagged by the sweep, for a person to check

Every line here is something a scraping agent was NOT sure about, or
actively caught being wrong, and said so. None of it blocks the data:
these rows are catalogued and none has entered companies.json. Work
this list when you next review the queues.

### 3CMA 2026  (35 captured, 23 govtech)
- Source: https://3cma.org/401/Conference-Sponsors, fetched cleanly with WebFetch and re-verified against raw curl HTML (no 403, no pagination, single page, all tiers inline).
- Borderline TRUE calls worth a second look: Tightrope Media Systems and CASTUS (gov video playout/streaming -- software-centric but shipped partly as appliances), Telephone Town Hall Meeting (tech plus managed serv

### NASCIO 2026  (76 captured, 41 govtech)
- Guard against a summarizer bug: WebFetch reported bogus totals ('98 companies', 'Gold 33') while its own name list held 76 -- I enumerated headings and logo alts directly from the DOM to confirm exactly 8 tiers and 76 names, matching WebFetch name-for-name.

### NAFA I&E 2026  (23 captured, 9 govtech, INCOMPLETE)
- Now on the walled list: https://www.nafainstitute.org/exhibitors-sponsors/
- MISSING: the full 230+ exhibitor directory.

### NACM 2026  (9 captured, 7 govtech)
- DATA-QUALITY CATCH: WebFetch hallucinated the last sponsor as 'Linebarger Gidel Strunk & Associates (LGBS)'.
- The logo image has no alt text, so that name was invented.

### APHSA 2026  (59 captured, 30 govtech)
- The raw-HTML extraction matched the rendered read exactly, so nothing was truncated despite the discovery note's warning that the list runs past the sample.

### IEDC 2026  (8 captured, 4 govtech)
- DATA QUALITY WARNING: the Data-Axle logo on the IEDC page is hyperlinked to https://www.cleco.com/ (Cleco, a Louisiana utility), which is almost certainly a CMS error;
- Worth re-verifying whether that booth is Data-Axle or Cleco.
- Per the discovery notes this list will grow before the Oct 25-28 event - complete=true reflects everything published as of today (2026-08-23), and this one should be re-swept closer to the date.

### NAHRO 2026  (72 captured, 24 govtech)
- Lower-confidence calls worth a second look: 'Tikler' (tikler.io) and 'Kanso Software', 'SACS Software', 'Zagaran Software' had blank profile descriptions and were judged true from name/domain plus known PHA-software reputation;

### EDUCAUSE 2026  (321 captured, 209 govtech)
- 6 exhibitors have no website link on their card (1Kosmos, Box, Catchbox, Equinix, TeamDynamix, Xfinity Communities) - left empty rather than guessed.
