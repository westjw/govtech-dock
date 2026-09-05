#!/usr/bin/env python3
"""Write company descriptions overnight, without anybody in the room.

    python3 scripts/write_profiles.py --category Police --limit 20 --dry-run
    python3 scripts/write_profiles.py --category Police --limit 20
    python3 scripts/write_profiles.py --retry-refused --limit 10

101 of 2,054 company pages carry a description. The other 1,953 are the
product's first impression and they say nothing. Every write-up that exists
was produced inside a session somebody was watching, which is why the number
is 101 and not 2,054.

DESIGNED BY A COUNCIL, ADOPTED BLIND. Three agents designed this
independently - one from cost, one from unattended failure, one from output
quality - and three more ranked the three without knowing who wrote which.
Quality won 9-6-3. Two of its findings were things nobody had looked for, and
one of them had been sitting in the refusal log for a week: see
PROFILE_RULES["quote_source"].

WHAT IT DOES NOT CHANGE: the door. agents.check_profile rules on what comes
back exactly as it rules on a write-up typed in a session, and everything -
passes and refusals both - goes through agents.ingest. Nothing here writes to
companies.json; promote_profiles lands, a person gates.

FIVE THINGS THAT ONLY GO WRONG WHEN NOBODY IS WATCHING, each of which the
council named and each of which has a line of code here:

  NO KEY MUST COST NOTHING. The check comes before the fetch, not after. A
  run that cheerfully fetches 100 companies' websites and then discovers it
  cannot ask anything has spent somebody's afternoon of goodwill for zero.

  THE BODIES ARE NOT IN THE REPOSITORY. data/site_pages/ is gitignored, so a
  fresh runner has none and brief_profile quietly yields nothing. The job goes
  green having done nothing, every night, and the only symptom is a number
  that never moves. So the fetch is part of the run and the summary says how
  many bodies it actually had.

  A DEGRADED NIGHT MUST NOT WRITE 'UNREAD' ACROSS A BATCH. CLAUDE.md carries
  this exact scar: two bad runs wrote 55 false "gave up after 75s" notes and
  42 of 53 answered fine on a retry. A company whose index entry says readable
  and whose fetch fails tonight is SKIPPED, never re-marked.

  A SYSTEMATIC REFUSAL BURNS COMPANIES PERMANENTLY. brief_profile skips any
  company already in the store, refused included, so a broken prompt spends
  the night refusing a hundred companies that are then never asked again. In
  a session a person sees three and stops. So: a circuit breaker.

  AN INGEST PER COMPANY EVICTS THE JOURNAL. journal.KEEP is 500 and every
  write prunes to the last 500 entries, so a hundred entries a night erases
  the before-images that make every admin write reversible - in five nights.
  Proposals land in batches of 40.

ONE COMPANY PER REQUEST, and not for the reason it looks like. A write-up is
about 800 output tokens against a cap of 8,000, so size is not the constraint.
Blast radius is: llm._json_from deliberately refuses to repair a truncated
object, so one batched request that hits the cap loses every company in it -
and a per-company refusal cannot be handed back inside a batch, which is the
whole repair loop below.

THE REPAIR LOOP IS THE CHEAPEST QUALITY THERE IS. The door runs HERE, against
the same corpus it will use at intake, before anything is stored. A refusal is
handed straight back to the model in its own words - "'Britain' is not on any
of their pages" - and it gets exactly one attempt to fix it. Two calls beat a
queue row a person has to read, and the door never moved an inch.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402
import fetch_profiles as fp                                     # noqa: E402
import llm                                                      # noqa: E402

INGEST_BATCH = 40      # journal.KEEP is 500 and every write prunes to it
BREAKER = 8            # consecutive refusals before a run stops asking

TASK = """Write a description of this company using ONLY the pages given.

Every sentence carries the url it came from and a verbatim quote from that
page. Obey `rules` exactly - they are the door's rules, and a write-up that
breaks one is refused rather than published.

"unsure" is a real and useful answer. If these pages will not support two or
three paragraphs about what the company sells and to whom, say so and write
nothing: a thin page is a fact about the page, and an invented sentence about
a real company is published on a public board under their name.

Return ONE JSON object:
{"id": <the exact id>, "confidence": "high"|"medium"|"low"|"unsure",
 "why": <one sentence on what the pages did and did not support>,
 "evidence": <the url that carried the most>,
 "paragraphs": [[{"text": ..., "url": ..., "quote": ...}]],
 "quote": {"text": <optional pull quote>, "url": ...}}"""

REPAIR = """That write-up was refused by the door. Here is exactly why:

    {why}

Fix ONLY what the refusal names and return the same JSON shape. If the pages
genuinely do not support the sentence it objects to, delete that sentence or
answer unsure - do not argue with the door and do not invent a replacement.
If it names something as "not on any of their pages", that thing has to go."""


def tonight(category: str | None, ids: list[str], limit: int,
            retry_refused: bool) -> list[dict]:
    """The companies to ask about, and nothing already answered."""
    companies = admin.read_companies()
    if retry_refused:
        # THE NINE. Refusals stay in the store so the gate review can read
        # them, which also means brief_profile will never offer them again.
        # After a BRIEF bug is fixed - and one was - they are re-askable, and
        # this is the only path that says so out loud.
        store = agents.load()
        ids = [p["id"] for p in store.values()
               if isinstance(p, dict) and p.get("kind") == "profile"
               and p.get("status") == "refused" and p.get("id")][:limit]
        if not ids:
            return []
        for cid in ids:
            store.pop(f"profile:{cid}", None)
        bad = agents.save(store, "profile-reopen",
                          why=f"re-asking {len(ids)} refused write-up(s) after "
                              f"a brief fix", by="write-profiles",
                          force=len(ids) > 25)
        if bad:
            print(f"REFUSED by the journal: {bad}", file=sys.stderr)
            return []
        print(f"  reopened {len(ids)} refused write-up(s) to be re-asked")
    # ALREADY ANSWERED IS FILTERED BEFORE THE FETCH, not after. brief_profile
    # skips anything in the store, so without this a run happily fetches
    # twenty websites and then builds zero briefs - a green night that did
    # nothing, which is the failure this file exists to make impossible.
    done = agents.load()
    want = [c for c in companies
            if (not ids or c.get("id") in ids)
            and (not category or c.get("category") == category)
            and f"profile:{c.get('id')}" not in done
            and not (isinstance(c.get("profile"), dict)
                     and c["profile"].get("paragraphs"))]
    return want[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--retry-refused", action="store_true",
                    help="re-ask write-ups the door refused, after a brief fix")
    ap.add_argument("--no-repair", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # BEFORE THE FETCH. See the docstring: a run that fetches a hundred sites
    # and then finds it cannot ask anything has spent goodwill for nothing.
    if not llm.key() and not a.dry_run:
        print("no ANTHROPIC_API_KEY and no key file: nothing fetched, nothing "
              "asked, nothing spent.")
        return 0

    rows = tonight(a.category, a.id, a.limit, a.retry_refused)
    if not rows:
        print("nothing to write tonight")
        return 0
    print(f"{len(rows)} compan(y/ies) for tonight"
          + (f", category {a.category!r}" if a.category else ""))

    idx = fp.index()
    fetched, skipped = [], []
    for c in rows:
        cid = c["id"]
        was_readable = bool(idx.get(cid)) and not (idx.get(cid) or {}).get("unread")
        if a.dry_run:
            if fp.load(cid):
                fetched.append(c)
            continue
        rec = fp.visit(c, news_depth=0)
        if rec.get("unread") and was_readable:
            # A DEGRADED NIGHT IS NOT A FINDING ABOUT THE COMPANY. CLAUDE.md's
            # scar: two bad runs wrote 55 false "gave up after 75s" notes and
            # 42 of 53 answered fine a week later. Skip; never re-mark.
            skipped.append((c.get("name"), rec["unread"]))
            continue
        fp.save(rec)
        idx[cid] = fp.index_entry(rec)
        if not rec.get("unread"):
            fetched.append(c)
    if not a.dry_run:
        fp.save_index(idx)
    print(f"  {len(fetched)} readable, {len(skipped)} skipped "
          f"(a failed fetch tonight is not a fact about the company)")
    if not fetched:
        print("  nothing readable; the bodies are gitignored, so a fresh "
              "runner starts with none and this is what that looks like.")
        return 0

    briefs = agents.brief_profile(ids=[c["id"] for c in fetched], limit=a.limit)
    print(f"  {len(briefs)} brief(s) built")
    if a.dry_run:
        if briefs:
            b = dict(briefs[0])
            b["pages"] = [{"url": p["url"], "lines": p["lines"][:6]} for p in b["pages"]]
            print(f"\n--- task ---\n{TASK}")
            print(f"\n--- one brief ---\n{json.dumps(b, indent=1)[:1500]}\n...")
        print(f"\ndry run: {len(briefs)} request(s) would be sent, nothing spent")
        return 0

    kept, refused_run, in_row = [], 0, 0
    for i, b in enumerate(briefs, 1):
        try:
            got = llm.ask(TASK, json.dumps(b, indent=1), "profile",
                          model=a.model, max_tokens=llm.MAX_OUTPUT)
        except llm.Refused as e:
            print(f"  stopping at {i}: {e}", file=sys.stderr)
            break
        if got is None:
            continue
        got.setdefault("id", b["id"])
        # THE DOOR, HERE, against the corpus it will use at intake.
        why = agents.check_profile(got, agents._profile_texts(got))
        if why and not a.no_repair:
            got2 = llm.ask(TASK + "\n\n" + REPAIR.format(why=why),
                           json.dumps(b, indent=1), "profile-repair",
                           model=a.model, max_tokens=llm.MAX_OUTPUT)
            if got2:
                got2.setdefault("id", b["id"])
                if agents.check_profile(got2, agents._profile_texts(got2)) is None:
                    got, why = got2, None
        kept.append(dict(got, kind="profile", key=f"profile:{b['id']}",
                         name=b.get("name"), sector=b.get("sector"),
                         category=b.get("category"),
                         also_known_as=b.get("also_known_as") or [],
                         saw={"pages": [{"url": p["url"], "sha": p.get("sha")}
                                        for p in b["pages"]]}))
        in_row = in_row + 1 if why else 0
        refused_run += bool(why)
        print(f"  {i}/{len(briefs)}: {'REFUSED - ' + why[:60] if why else 'passed the door'}")
        if in_row >= BREAKER:
            # A BROKEN PROMPT MUST NOT BURN A HUNDRED COMPANIES. They would be
            # in the store as refusals and brief_profile never offers those
            # again.
            print(f"\n  STOPPING: {BREAKER} refusals in a row. Something is "
                  f"wrong with the prompt or the door, not with these "
                  f"companies - and every one asked is one the brief will "
                  f"never offer again.", file=sys.stderr)
            break

    calls, usd = llm.spent()
    print(f"\n{len(kept)} answer(s), {refused_run} refused, "
          f"{calls} request(s), ${usd:.2f}")
    for i in range(0, len(kept), INGEST_BATCH):
        rep = agents.ingest("profile", kept[i:i + INGEST_BATCH],
                            model=f"{a.model}:write-profiles")
        print(f"  ingest: {rep['kept']} through, {len(rep['refused'])} refused")
    print(f"\nPending in the admin. Gate and land the usual way:\n"
          f"  python3 scripts/promote_profiles.py --gate "
          f"{a.category or '<category>'!r} --self")
    return 0


if __name__ == "__main__":
    sys.exit(main())
