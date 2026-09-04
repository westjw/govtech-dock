#!/usr/bin/env python3
"""Check every refusal the write-up door has made, against the pages.

    python3 scripts/audit_refusals.py              # the token check, no cost
    python3 scripts/audit_refusals.py --review     # ask a model to read them
    python3 scripts/audit_refusals.py --review --limit 40

WHY THIS EXISTS. The door has refused something TRUE ten times: a plural, a
trademark sign, a page read with the wrong character set, a parenthetical
inside a name, "of" held to a stricter test than a comma, a product name
containing a word off the marketing list, "leading" used as a verb, a
sentence-opening capital swallowed into a run, the same again in the marketing
rule, and a pronoun that was half a company's name. Every one was found by
asking whether the thing the door NAMED is actually absent from their pages -
never by reading the code, and never by anybody noticing on their own.

That is a check, not an anecdote, so it is a script. Run it after every batch.

TWO PASSES, AND THE FIRST ONE IS FREE. The token check below is mechanical: it
pulls the token out of the refusal sentence and looks for it, whole-word, in
the raw page text. That is exactly the question that found all ten, and it
costs nothing. It is the default.

WHAT IT CANNOT SEE is the refusal whose sentence names no token - "a sentence
runs over 45 words", "the quote is not on that page", "'US' is not on any of
their pages" where the pages say "U.S." - and the ones where the token really
is absent but the RULE is wrong. Those are a reading job. `--review` is that
reading: the refused sentence, the rule that refused it, and the page text
around it, handed to a model that answers whether the door was right and what
rule would fix it.

IT REPORTS. IT NEVER RULES. Two of the first 39 flagged rows came back
"suspect" and both were correct on a closer read - a version number that is a
prefix of a longer one, and a marketing word that really was lower case. A
tool that decided for itself would have reversed both. So the model's verdict
is printed next to the door's, a person compares them, and a rule changes only
when somebody edits agents.py by hand.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                                   # noqa: E402
import fetch_profiles as fp                                     # noqa: E402
import llm                                                      # noqa: E402

BATCH = 12

REVIEW = """A provenance door refused a sentence somebody wrote about a
company, using only that company's own web pages as evidence. Your job is to
say whether the DOOR was right.

The door's rules, in short: every sentence must quote the page it cites,
verbatim; every capitalised name, every capitalised run and every number in
the prose must appear somewhere on the company's own pages; no first person;
no em-dashes; no marketing adjectives.

The door has been wrong ten times, and every one was the same shape: the thing
it named IS on the page, in a form the matcher did not recognise. A plural
("FaceVectors" for "FaceVector"). A trademark sign normalising to "TM". A page
decoded as Latin-1 so curly quotes became three glued characters. A
parenthetical inside a name ("Rocket City (Huntsville) HQ"). A name bridged by
"of" ("Greg Champagne of St. Charles Parish"). A product whose name contains a
marketing word ("Graykey Premier"). "leading" used as a verb ("leading to the
arrest"). A sentence-opening capital swallowed into a name run ("Adding
Mastery Connect"). A pronoun that is half a company name ("Unite Us").

So look for that shape first. But a refusal is usually CORRECT, and saying so
is the useful answer most of the time - the door exists because a model
writing about somebody else's company invents customers, numbers and dates.

For each item return:
{"reviews": [{"key": <the exact key>, "verdict": "door_was_right" |
 "door_was_wrong" | "unsure",
 "found_as": <only when door_was_wrong: how the thing DOES appear on the page,
   quoted exactly>,
 "rule": <only when door_was_wrong: one sentence naming what the matcher would
   have to tolerate>,
 "why": <one sentence>}]}

"unsure" is a real answer. A wrong "door_was_wrong" argues for loosening a
door that publishes facts about real companies, which is the expensive
direction."""


def refusals() -> list:
    store = agents.load()
    return [dict(v, _key=k) for k, v in store.items()
            if isinstance(v, dict) and v.get("kind") == "profile"
            and v.get("status") == "refused"]


def raw_pages(cid: str) -> str:
    rec = fp.load(cid) or {}
    return " ".join(x.get("text", "") for x in
                    (rec.get("about") or []) + (rec.get("news") or []))


def token_check(rows: list) -> tuple[list, list]:
    """The free pass: is the token the door named actually on the page?"""
    suspect, sound = [], []
    for p in rows:
        why = p.get("refused_why") or ""
        m = re.search(r"'([^']+)'", why)
        tok = m.group(1) if m else ""
        if not tok:
            sound.append((p, why, ""))
            continue
        raw = raw_pages(p["id"])
        hit = re.search(r"(?<!\w)" + re.escape(tok) + r"(?!\w)", raw, re.I)
        (suspect if hit else sound).append((p, why, tok))
    return suspect, sound


def _context(p: dict, tok: str, width: int = 700) -> str:
    """The part of the pages worth reading, not all of them.

    A REFUSAL IS ABOUT ONE TOKEN, and sending four pages to ask about one word
    is how a batch of twelve becomes an unaffordable request. When the token
    appears, its neighbourhood is the evidence; when it does not, the opening
    of the pages is the best available context for judging whether the rule
    itself is wrong.
    """
    raw = raw_pages(p["id"])
    if tok:
        m = re.search(r"(?<!\w)" + re.escape(tok[:40]) + r"(?!\w)", raw, re.I)
        if m:
            a = max(0, m.start() - width // 2)
            return raw[a:a + width]
    return raw[:width]


def review(rows: list, model: str, batch: int, dry: bool) -> list:
    """Ask a model whether each refusal was right. Returns its answers."""
    items = []
    for p, why, tok in rows:
        prop = p.get("proposal") or {}
        # the refused sentence itself, when the rule named one
        text = ""
        for para in (prop.get("paragraphs") or []):
            for s in (para if isinstance(para, list) else []):
                if isinstance(s, dict) and tok and tok.lower() in (s.get("text") or "").lower():
                    text = s["text"]
                    break
        items.append({"key": p["_key"], "company": p.get("name"),
                      "refused_because": why, "token_named": tok,
                      "the_sentence": text or None,
                      "page_context": _context(p, tok)})
    lots = [items[i:i + batch] for i in range(0, len(items), batch)]
    if dry:
        print(f"\n--- system ---\n{REVIEW}")
        print(f"\n--- one item ---\n{json.dumps(items[0], indent=1)[:1200] if items else ''}")
        print(f"\ndry run: {len(lots)} request(s) would be sent, nothing spent")
        return []
    out = []
    for i, lot in enumerate(lots, 1):
        try:
            got = llm.ask(REVIEW, json.dumps({"items": lot}, indent=1),
                          "refusal-audit", model=model, max_tokens=llm.MAX_OUTPUT)
        except llm.Refused as e:
            print(f"  stopping at request {i}: {e}", file=sys.stderr)
            break
        if got is None:
            print(f"  request {i}/{len(lots)}: nothing usable came back")
            continue
        out.extend((got or {}).get("reviews") or [])
        print(f"  request {i}/{len(lots)}: {len(out)} review(s) so far")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true",
                    help="ask a model to read the refusals the token check clears")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = refusals()
    print(f"{len(rows)} refusal(s) on file\n")
    suspect, sound = token_check(rows)

    print(f"LOOK AT THESE: {len(suspect)} name something that IS on the page")
    for p, why, tok in suspect:
        print(f"  {p['id'][:26]:28} {why[:78]}")
    print(f"\nsound on their face: {len(sound)}")
    for p, why, tok in sound[:10]:
        print(f"  {p['id'][:26]:28} {why[:78]}")

    if not a.review:
        print("\n(--review reads the sound ones with a model; the pass above "
              "is free and is what found all ten false refusals so far)")
        return 0

    # THE TOKEN CHECK HAS ALREADY ANSWERED THE SUSPECT ONES. Paying to re-ask
    # a question a regex settled is how these runs get expensive for nothing.
    todo = sound[:a.limit] if a.limit else sound
    if not todo:
        print("\nnothing for the second reader")
        return 0
    print(f"\nreading {len(todo)} refusal(s) the token check cleared")
    got = review(todo, a.model, a.batch, a.dry_run)
    if a.dry_run:
        return 0

    wrong = [r for r in got if r.get("verdict") == "door_was_wrong"]
    unsure = [r for r in got if r.get("verdict") == "unsure"]
    calls, usd = llm.spent()
    print(f"\n{len(got)} reviewed, {calls} request(s), ${usd:.2f} estimated")
    print(f"  door was right : {len(got) - len(wrong) - len(unsure)}")
    print(f"  door was wrong : {len(wrong)}")
    print(f"  unsure         : {len(unsure)}")
    for r in wrong:
        print(f"\n  {r.get('key')}")
        print(f"    found as : {str(r.get('found_as'))[:110]}")
        print(f"    rule     : {str(r.get('rule'))[:110]}")
        print(f"    why      : {str(r.get('why'))[:110]}")
    if wrong:
        print("\nNOTHING HAS BEEN CHANGED. Each line above is a claim about a "
              "rule in agents.py, to be checked against the page by hand - two "
              "of the first 39 flagged rows turned out to be correct refusals "
              "on a closer read, and a tool that decided for itself would have "
              "reversed both.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
