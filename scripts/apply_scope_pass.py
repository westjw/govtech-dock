#!/usr/bin/env python3
"""Land the once-per-company scope pass. --write to apply.

WHAT THIS IS. Ten agents read 76 companies' OWN sites on 2026-09-11 and
answered one question each: who buys this? The companies are the ones whose
own hiring could not settle it - between 1% and 33% of their postings named
somewhere abroad or a non-government market, which is the band where a pure
govtech vendor with a stray title looks exactly like a horizontal vendor with
a government slice.

WHAT IT SETS, and what it deliberately does not. `buyer` and `sells_to_gov`
land on every company researched: they are the researcher's words with a page
behind them. `sled_only` lands ONLY where the site named no non-government
buyer at all AND the agent said high confidence - anything softer is a
judgement somebody should make with the evidence in front of them, and the
evidence is recorded either way.

NOTHING IS REMOVED HERE. Three companies came back as not selling to
government at all; that is a scope ruling and it belongs to a person.
"""
import json, pathlib, sys, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import admin

WRITE = "--write" in sys.argv
today = dt.date.today().isoformat()
found = json.loads((ROOT / "data" / "scope_pass_2026-09-11.json").read_text())

co = admin.read_companies()
rows = co if isinstance(co, list) else co["companies"]
byid = {c["id"]: c for c in rows}

# THE AGENT CORRECTED ITS OWN FIELD IN PROSE, and the prose is right: it
# answered "no" for Lightspeed Systems and then wrote "sells_to_gov should
# read yes - school districts are public buyers under the house rules". A
# field and a note that disagree is a reason to read the note.
OVERRIDE = {"lightspeed-systems": "yes"}

n = sled = skipped = 0
not_gov = []
for r in found:
    c = byid.get(r["id"])
    if not c:
        skipped += 1
        continue
    verdict = OVERRIDE.get(r["id"], r["sells_to_gov"])
    if r.get("buyer"):
        c["buyer"] = r["buyer"]
    c["sells_to_gov"] = verdict
    c["buyer_source"] = r.get("evidence_url") or None
    c["buyer_checked_on"] = today
    if r.get("note"):
        c["scope_note"] = r["note"][:600]
    n += 1
    if verdict == "no" or (verdict == "unclear" and r["sells_outside_government"] == "yes"):
        not_gov.append((r["id"], r["name"], r["confidence"]))
    if (verdict == "yes" and r["sells_outside_government"] == "no"
            and r["confidence"] == "high"):
        c["sled_only"] = True
        c["sled_only_why"] = (f"scope pass {today}: their own site names no "
                              f"non-government buyer. {r['buyer'][:180]}")
        sled += 1

print(f"{n} companies updated, {skipped} no longer on file")
print(f"  sled_only set on {sled} more, each from a site that named no other buyer")
print(f"\n{len(not_gov)} came back as not selling to government - NOT touched, "
      f"a scope ruling is a person's:")
for cid, name, conf in not_gov:
    print(f"     {name[:30]:32} ({conf} confidence)")

if WRITE:
    bad = admin.save_companies(
        co, "scope-pass-2026-09-11",
        why=(f"Once-per-company scope pass: 76 companies' own sites read by "
             f"ten agents. buyer and sells_to_gov on {n}, sled_only on {sled} "
             f"where the site named no non-government buyer at high "
             f"confidence. No removals."),
        by="scope pass 2026-09-11, applied by Claude on the owner's request",
        force=True)
    if bad:
        print("  REFUSED:", bad); sys.exit(1)
    print("\n  written through the journal")
else:
    print("\n  LOOKED ONLY. --write to land it.")
