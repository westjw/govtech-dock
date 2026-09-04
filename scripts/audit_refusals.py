#!/usr/bin/env python3
"""Check every refusal the write-up door has made, against the pages.

    python3 scripts/audit_refusals.py

WHY THIS EXISTS. The door has refused something true ten times: a plural, a
trademark sign, a page read with the wrong character set, a parenthetical
inside a name, "of" held to a stricter test than a comma, a product name
containing a word off the marketing list, "leading" used as a verb, a
sentence-opening capital swallowed into a run, the same again in the
marketing rule, and a pronoun that was half a company's name. Every one was
found by asking whether the thing the door NAMED is actually absent from
their pages - never by reading the code.

That is a check, not an anecdote, so it is a script. Run it after every
batch. It prints the refusals whose named token IS on the page, which is the
shape a false refusal takes; everything else it leaves alone.

It reports rather than rules. Two of the first 39 came back flagged and both
were correct on a closer read - a version number that is a prefix of a
longer one, and a marketing word that really was lower case. A tool that
decided for itself would have reversed both.
"""
import sys, json, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import agents, fetch_profiles as fp
store = agents.load()
rows = [v for v in store.values() if isinstance(v, dict)
        and v.get("kind") == "profile" and v.get("status") == "refused"]
print(f"{len(rows)} refusals on file\n")
suspect, sound = [], []
for p in rows:
    why = p.get("refused_why") or ""
    m = re.search(r"'([^']+)'", why)
    tok = m.group(1) if m else ""
    rec = fp.load(p["id"]) or {}
    raw = " ".join(x.get("text", "") for x in (rec.get("about") or []) + (rec.get("news") or []))
    if not tok:
        sound.append((p["id"], why, "")); continue
    # the exact token, whole-word, in the RAW page text
    hit = re.search(r"(?<!\w)" + re.escape(tok) + r"(?!\w)", raw, re.I)
    (suspect if hit else sound).append((p["id"], why, tok))
print(f"LOOK AT THESE: {len(suspect)} name something that IS on the page")
for cid, why, tok in suspect:
    print(f"  {cid[:26]:28} {why[:78]}")
print(f"\nsound on their face: {len(sound)}")
for cid, why, tok in sound[:10]:
    print(f"  {cid[:26]:28} {why[:78]}")
