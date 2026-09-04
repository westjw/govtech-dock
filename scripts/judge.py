#!/usr/bin/env python3
"""Ask a model the two queue questions that are pure judgment on text on disk.

    python3 scripts/judge.py --kind family --limit 300 --dry-run
    python3 scripts/judge.py --kind family --limit 300
    python3 scripts/judge.py --kind bucket --limit 200

TWO QUEUES, ONE SHAPE. Unclassified roles (756 titles) and Wrong bucket (188
companies) are the two piles here where every fact needed to answer is already
in `board.json` and `companies.json`. Nothing has to be fetched, nothing has to
be searched, nobody has to open a page. They are the queues a person clears by
reading a line and knowing what it means, which is exactly the work a model
does cheaply and a regex has already failed at - `roles.py` and the bucket
guesser both ran on these and left what they could not place.

MY CODE OWNS THE LOOP. The model gets a batch of briefs built here,
deterministically, and answers in one request. It does not choose what to look
at, it does not decide when it is finished, and it cannot ask for more. That is
the same deal `agents.py` has always described, and the only new thing is that
nobody has to be in the room.

BATCHES ARE AN OUTPUT CAP, NOT A DESIGN. One request for 756 titles would need
roughly 38,000 output tokens and `llm.MAX_OUTPUT` is 8,000, so the work goes
out in batches - six or seven requests, not 756. Each batch is INGESTED THE
MOMENT IT LANDS rather than at the end, so a crash, a rate limit or a Ctrl-C
keeps everything already paid for.

THE CLUSTER REPORT IS THE POINT OF RUNNING THIS AT ALL. CLAUDE.md is explicit
that hand family assignments are DATA and a title that suggests a rule gets a
rule with a selftest case. Filing 40 overrides for 40 titles that all say
"Solutions Consultant" would clear the queue and hide the gap in `roles.py`
that put them there, and next month's board would refill it. So after every
run this prints the phrases that several titles share and several answers
agreed on, and those are rules to write, not overrides to accept.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                                   # noqa: E402
import llm                                                      # noqa: E402

# WHAT SETS EACH NUMBER, because none of them is a preference. family and
# bucket are capped by OUTPUT tokens - 756 titles at ~50 tokens each is five
# times llm.MAX_OUTPUT. fact is capped by INPUT: each brief carries up to four
# pages of somebody's website, median 6,312 characters, so 20 is ~126,000 of
# llm.MAX_INPUT_CHARS' 240,000. card is small at both ends.
BATCH = {"family": 60, "bucket": 25, "card": 40, "fact": 20}

# THE HOUSE RULES, IN THE PROMPT. Every one of these is a rule this project
# already enforces at the door, restated where the answer is written: a door
# that refuses is cheaper than a door that refuses a hundred times.
RULES = """You are answering a queue in a public job board's admin. Everything
you write is a PROPOSAL. A person rules on it beside the evidence, and a door
refuses it before they ever see it if it breaks a rule below.

- Answer only from what is in the brief. Never invent a fact to fill a field.
- "unsure" is a real answer and a useful one. A wrong confident answer is worse
  than an honest unsure, because a wrong answer here is invisible: nothing
  errors, no count looks odd, and nothing ever contradicts it.
- A confident answer carries a `why` of at least 25 characters that a person
  can check against the brief.
- "high" confidence additionally carries `evidence`: the words you read.
- Return ONE JSON object and nothing else."""

FAMILY_TASK = """Each item is a job title that the board's pattern rules could
not put in a role family, with the companies advertising it and what those
companies sell.

Decide the family from the list in `families`. The company matters: an
"Implementation Consultant" is field work at a software vendor and something
else at a consultancy. Do NOT answer "other" - that is where the title already
sits; answer "unsure" instead.

Return: {"answers": [{"title": <the exact title from the brief>,
"family": <key from families, or omit when unsure>, "confidence":
"high"|"medium"|"low"|"unsure", "why": <one sentence>, "evidence": <the words in
the title or company that decided it>}]}"""

BUCKET_TASK = """Each item is a company filed under a sector and category that
look wrong, with its own one-line description and what a keyword guesser
proposed. The guesser counts words and is often wrong - treat `regex_guess` as
a starting point to disagree with, not an answer to confirm.

Choose from the sectors and categories in `schema`. Never propose "Suppliers &
Services": that is the bucket these are being moved out of.

Return: {"answers": [{"id": <the exact id from the brief>, "sector": ...,
"category": ..., "confidence": "high"|"medium"|"low"|"unsure", "why": <one
sentence>, "evidence": <the words in the description that decided it>}]}"""


CARD_TASK = """Each item is a company somebody researched off a conference floor
and nobody has ruled on. It is NOT on this board yet.

Answer one of three verdicts:
 - "govtech": they sell a technology product to state or local government. This
   one is expensive to be wrong about in both directions, so it also needs a
   sector and category from `schema`, and a one-line description of what they
   sell and to whom.
 - "supplier": they sell to government but are not a technology product vendor
   - services, hardware, consulting, staffing, financial advice.
 - "not_this_board": not a government seller at all.

BOTH DIRECTIONS NEED EVIDENCE, and the negative needs it most. A wrong
"govtech" is visible on a public page and somebody will say so. A wrong "not
this board" hides a real company for ever: it stops appearing, nothing errors,
and nothing ever contradicts it.

The description field is null for every one of these - the research produced a
name, a website and a one-line vertical and no more. If those do not settle it,
answer "unsure". Do not write a description you cannot read off something.

Return: {"answers": [{"name": <the exact name from the brief>, "verdict": ...,
"confidence": "high"|"medium"|"low"|"unsure", "why": <one sentence>,
"evidence": <the words that decided it>, "sector": ..., "category": ...,
"description": <one line, only for govtech>}]}"""

FACT_TASK = """Each item is a company and the pages from its own website that we
have on file, with one or two facts missing: a founding year, a location, or
both.

Answer ONLY from these pages. The value must appear inside a verbatim quote
from one of them - not near it, not implied by it, IN it. A year that is merely
plausible is exactly what gets written when nobody can check, and nothing
downstream will ever contradict it.

If the pages do not state it, answer "unsure". That is the expected answer
often, and it costs nothing.

Return: {"answers": [{"id": <the exact id>, "field": "year_founded"|"location",
"value": <a four-digit year, or "City, ST">, "confidence":
"high"|"medium"|"low"|"unsure", "url": <which page>, "quote": <verbatim, 20+
characters, containing the value>, "why": <one sentence>}]}"""


TASKS = {"family": FAMILY_TASK, "bucket": BUCKET_TASK,
         "card": CARD_TASK, "fact": FACT_TASK}


def _prompt(kind: str, briefs: list) -> tuple[str, str]:
    payload: dict = {"items": briefs}
    if kind in ("bucket", "card"):
        payload["schema"] = agents._schema()
    if kind == "fact":
        # ONE COPY OF THE PAGES per company, however many fields it is short.
        by_id: dict = {}
        for b in briefs:
            row = by_id.setdefault(b["id"], {
                "id": b["id"], "name": b["name"], "website": b.get("website"),
                "pages": b["pages"], "asking": {}, "rules": b["rules"]})
            row["asking"][b["field"]] = b["asking"]
        payload = {"items": list(by_id.values())}
    return f"{RULES}\n\n{TASKS[kind]}", json.dumps(payload, indent=1)


def _as_proposals(kind: str, answers: list, briefs: list) -> list:
    """Attach each answer to the brief it belongs to, and refuse an orphan.

    A MODEL HANDED SIXTY ITEMS SOMETIMES RETURNS SIXTY-ONE, and the extra one
    is invented. Keying every answer back to a brief that was actually sent is
    what stops an invented title reaching the door at all - the door would
    catch it too, but a refusal costs a queue row and this costs nothing.
    """
    # a fact answer is keyed by BOTH the company and the field: one company
    # can be short of two, and they refuse independently
    by_key = ({(b["id"], b["field"]): b for b in briefs} if kind == "fact"
              else {b["id"]: b for b in briefs})
    out = []
    for a in answers or []:
        ident = (a.get("title") if kind == "family"
                 else a.get("name") if kind == "card"
                 else (a.get("id"), a.get("field")) if kind == "fact"
                 else a.get("id"))
        b = by_key.get(ident)
        if not b:
            continue
        row = dict(a)
        row.update({"kind": kind, "key": b["key"], "id": b["id"],
                    "name": b["name"],
                    "saw": {k: v for k, v in b.items()
                            if k not in ("kind", "key", "families")}})
        if kind == "family":
            row["title"] = b["title"]
        if kind == "fact":
            row["field"] = b["field"]
        out.append(row)
    return out


WORD = re.compile(r"[A-Za-z][A-Za-z&/-]+")


def clusters(rows: list, floor: int = 4) -> list:
    """Phrases several titles share where the answers also agreed.

    Two or three words, because one word is "Manager" and says nothing. The
    floor is what separates a rule from a coincidence, and it is deliberately
    low: a phrase on four titles is worth a person LOOKING at, not a rule
    written automatically. Nothing here writes anything.
    """
    seen: dict = collections.defaultdict(set)
    for r in rows:
        fam = r.get("family")
        if not fam or r.get("confidence") == "unsure":
            continue
        w = [x.lower() for x in WORD.findall(r.get("title") or "")]
        for n in (2, 3):
            for i in range(len(w) - n + 1):
                seen[(" ".join(w[i:i + n]), fam)].add(r["title"])
    out = [{"phrase": k[0], "family": k[1], "titles": sorted(v)}
           for k, v in seen.items() if len(v) >= floor]
    # a trigram and the bigram inside it are one finding; keep the longer
    out.sort(key=lambda r: (-len(r["titles"]), -len(r["phrase"])))
    kept: list = []
    for r in out:
        if any(r["phrase"] in k["phrase"] and set(r["titles"]) <= set(k["titles"])
               for k in kept):
            continue
        kept.append(r)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=("family", "bucket", "card", "fact"),
                    required=True)
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--batch", type=int)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the request; ask nothing, spend nothing")
    a = ap.parse_args()

    briefs = (agents.brief_family(a.limit) if a.kind == "family"
              else agents.brief_bucket(a.limit) if a.kind == "bucket"
              else agents.brief_card(a.limit) if a.kind == "card"
              else agents.brief_fact(a.limit))
    if a.kind == "fact":
        # SAME COMPANY, SAME BATCH. 13 of 47 companies in the queue are missing
        # both a founding year and a location, and their briefs carry the same
        # four pages twice. Sorting by id puts them in one request, where
        # _prompt sends those pages once - about a quarter of the input tokens
        # for this queue, for one sort.
        briefs.sort(key=lambda b: (b["id"], b["field"]))
    if not briefs:
        print(f"nothing unanswered in the {a.kind} queue")
        return 0
    size = a.batch or BATCH[a.kind]
    lots = [briefs[i:i + size] for i in range(0, len(briefs), size)]
    print(f"{len(briefs)} {a.kind} brief(s) in {len(lots)} request(s) "
          f"of up to {size}, model {a.model}")

    if a.dry_run:
        sysm, user = _prompt(a.kind, lots[0])
        print(f"\n--- system ({len(sysm)} chars) ---\n{sysm}")
        print(f"\n--- user, request 1 of {len(lots)} "
              f"({len(user)} chars) ---\n{user[:2500]}\n...")
        print("\ndry run: nothing asked, nothing spent")
        return 0

    landed: list = []
    for i, lot in enumerate(lots, 1):
        sysm, user = _prompt(a.kind, lot)
        try:
            got = llm.ask(sysm, user, a.kind, model=a.model, max_tokens=llm.MAX_OUTPUT)
        except llm.Refused as e:
            print(f"  stopping at request {i}: {e}", file=sys.stderr)
            break
        if got is None:
            print(f"  request {i}/{len(lots)}: nothing usable came back")
            continue
        rows = _as_proposals(a.kind, (got or {}).get("answers"), lot)
        if not rows:
            print(f"  request {i}/{len(lots)}: no answer matched a brief")
            continue
        # INGESTED PER BATCH, not at the end: what is paid for is kept.
        rep = agents.ingest(a.kind, rows, model=f"{a.model}:judge-{a.kind}")
        landed.extend(rows)
        print(f"  request {i}/{len(lots)}: {rep['kept']} through the door, "
              f"{len(rep['refused'])} refused")
        for r in rep["refused"][:4]:
            print(f"      REFUSED {r['key']}: {r['why'][:90]}")

    calls, usd = llm.spent()
    print(f"\n{len(landed)} answer(s) in, {calls} request(s), "
          f"${usd:.2f} estimated")

    if a.kind == "family":
        cl = clusters(landed)
        if cl:
            print("\nTHESE ARE RULES, NOT OVERRIDES. Each phrase below is on "
                  "several titles that all read the same way, which is a gap "
                  "in roles.py - write the rule with a selftest case and the "
                  "titles leave the queue together:")
            for c in cl[:12]:
                print(f"  {len(c['titles']):3} × {c['phrase']!r} -> {c['family']}")
                for t in c["titles"][:3]:
                    print(f"        {t[:70]}")
    print(f"\nThey are pending in the admin's Agent proposals tab. "
          f"Nothing has been written to the map.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
