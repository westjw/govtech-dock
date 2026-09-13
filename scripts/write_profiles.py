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

TASK = """Answer TWO questions about this company using ONLY the pages given.

WHAT THEY SELL. Every sentence carries the url it came from and a verbatim
quote from that page. Obey `rules` exactly - they are the door's rules, and a
write-up that breaks one is refused rather than published.

"unsure" is a real and useful answer. If these pages will not support two or
three paragraphs about what the company sells and to whom, say so and write
nothing: a thin page is a fact about the page, and an invented sentence about
a real company is published on a public board under their name.

WHO BUYS IT. A separate answer, judged separately, in `buyer`. Obey
`buyer_rules`. It is not a summary of the write-up and it does not stand or
fall with it: a write-up refused for one sentence can carry a sound buyer
answer read off the same page, and a good write-up can be paired with a buyer
answer that rests on nothing. Answer it as if nothing else were being asked.

Return ONE JSON object:
{"id": <the exact id>, "confidence": "high"|"medium"|"low"|"unsure",
 "why": <one sentence on what the pages did and did not support>,
 "evidence": <the url that carried the most>,
 "paragraphs": [[{"text": ..., "url": ..., "quote": ...}]],
 "quote": {"text": <optional pull quote>, "url": ...},
 "buyer": {"confidence": "high"|"medium"|"low"|"unsure",
           "why": <one sentence on what the pages did and did not settle>,
           "sells_to_gov": "yes"|"unclear"|"no",
           "buyer": <one sentence naming who these pages say buys>,
           "buyer_url": <the page that names a government buyer>,
           "buyer_quote": <verbatim from that page; required for "yes">,
           "names_other_buyers": "yes"|"unclear"|"no",
           "other_url": <the page that names a non-government buyer>,
           "other_quote": <verbatim from that page; required for "yes">}}"""

BUYER_TASK = """Answer ONE question about this company using ONLY the pages
given: who buys what they sell, and is a government one of them?

Obey `rules` exactly - they are the door's rules, and an answer that breaks
one is refused rather than recorded.

These pages are not the whole company. "unclear" is a real and complete
answer, and it is the right one whenever the pages do not say who buys.
Never write "no" to mean "I did not see one".

Return ONE JSON object:
{"id": <the exact id>, "confidence": "high"|"medium"|"low"|"unsure",
 "why": <one sentence on what the pages did and did not settle>,
 "sells_to_gov": "yes"|"unclear"|"no",
 "buyer": <one sentence naming who these pages say buys>,
 "buyer_url": <the page that names a government buyer>,
 "buyer_quote": <verbatim from that page; required for "yes">,
 "names_other_buyers": "yes"|"unclear"|"no",
 "other_url": <the page that names a non-government buyer>,
 "other_quote": <verbatim from that page; required for "yes">}"""

REPAIR = """That answer was refused at the door. Here is exactly why:

    {why}

Fix ONLY what the refusal names and return the same JSON shape. If the pages
genuinely do not support the sentence it objects to, delete that sentence or
answer unsure - do not argue with the door and do not invent a replacement.
If it names something as "not on any of their pages", that thing has to go."""


def split_answer(got: dict, bid: str, buyer_only: bool = False) -> tuple[dict, dict]:
    """One reply, two proposals: the write-up and the buyer answer.

    THEY TRAVEL TOGETHER AND THEY ARE JUDGED APART, which is the whole point
    of asking both in one request. The pages are fetched once, read once and
    paid for once; the two claims they carry then go through two doors, get
    two statuses and land through two commands. A single status would throw
    away whichever half was sound.

    The buyer block carries its OWN confidence and why. Nothing is defaulted
    in from the write-up's: "high confidence that the pages support three
    paragraphs" is not "high confidence about who buys", and copying one into
    the other would manufacture a certainty nobody stated.

    TWO TASKS, TWO SHAPES, AND THIS FUNCTION HAS TO KNOW WHICH. TASK asks for
    the buyer answer NESTED under `buyer` beside the write-up; BUYER_TASK asks
    for it FLAT, because it is the whole of what was asked. The first version
    looked for the nested key in both modes, so every --buyer-only reply was
    silently emptied and refused at rule 2 for carrying no confidence - the
    model had answered correctly and the splitter threw it away. Found on the
    first live call, for three cents, which is what that call was for.
    """
    if buyer_only:
        sc = dict(got)
        sc["id"] = sc.get("id") or bid
        return {}, sc
    prof = {k: v for k, v in got.items() if k != "buyer"}
    prof.setdefault("id", bid)
    raw = got.get("buyer")
    sc = dict(raw) if isinstance(raw, dict) else {}
    sc["id"] = prof["id"]
    return prof, sc


def recheck_refused(limit: int, apply: bool) -> int:
    """Re-run the door over write-ups it already refused. No model call.

    A refusal is one of two things and they need opposite treatment. Either
    the write-up claimed something the company's pages do not say, which is
    the door working and the answer stays no; or the DOOR was wrong, and the
    write-up on file was fine all along. Re-asking costs money to reproduce
    prose we are already holding, and the second answer will not be the same
    prose, so the refusal that was our fault is not even the thing reviewed.

    Measured on the 72 refusals on file: widening rule 5 from a US-only
    geography list to one that includes the United Kingdom and Europe cleared
    14 of them, for nothing. What stayed refused is what the door is for -
    'Chief Financial Officer Adam Jones', 'Los Angeles County', 'Amazon Web
    Services', '30000' - none of which appear on the pages they were written
    from.

    Reopened proposals go back to `pending`, which puts them in the gate
    review where a person reads them. Nothing here lands anything.
    """
    store = agents.load()
    refused = [(k, v) for k, v in store.items()
               if isinstance(v, dict) and v.get("kind") == "profile"
               and v.get("status") == "refused" and v.get("id")]
    now_ok, still, unread = [], 0, 0
    for key, prop in refused:
        rec = fp.load(prop["id"])
        if not rec:
            unread += 1
            continue
        texts = {pg["url"]: pg.get("text") or ""
                 for pg in (rec.get("about") or []) if pg.get("url")}
        if agents.check_profile(prop.get("proposal") or {}, texts) is None:
            now_ok.append(key)
        else:
            still += 1
    print(f"  {len(refused)} refused write-up(s) on file")
    print(f"  {len(now_ok)} now pass the door, {still} still refused"
          + (f", {unread} whose pages are no longer cached" if unread else ""))
    if not now_ok:
        return 0
    if not apply:
        print("  nothing changed: add --write to reopen them for the gate")
        return 0
    for key in now_ok[:limit]:
        store[key]["status"] = "pending"
        store[key]["reopened_why"] = ("the door refused this and now accepts "
                                      "it unchanged; the refusal was ours")
    bad = agents.save(store, "profile-recheck",
                      why=f"{len(now_ok[:limit])} write-up(s) the door now "
                          f"accepts unchanged, moved to pending for review",
                      by="write-profiles", force=len(now_ok[:limit]) > 25)
    if bad:
        print(f"REFUSED by the journal: {bad}", file=sys.stderr)
        return 1
    print(f"  reopened {len(now_ok[:limit])} for the gate review. They are "
          f"PENDING, not landed: promote_profiles.py --gate reads them")
    return 0


def tonight(category: str | None, ids: list[str], limit: int,
            retry_refused: bool, buyer_only: bool = False) -> list[dict]:
    """The companies to ask about, and nothing already answered."""
    companies = admin.read_companies()
    if buyer_only:
        # A DIFFERENT QUESTION HAS A DIFFERENT ALREADY-ANSWERED. The 505
        # companies this mode exists for all carry a landed write-up, so the
        # profile filter below would skip every one of them. brief_buyer holds
        # the rule that matters - no scope proposal on file, no sells_to_gov
        # on the company - and it is the same rule the brief will apply in a
        # moment, so asking it here is not a second opinion about who is due.
        want = {r["id"] for r in agents.brief_buyer(ids=ids or None,
                                                    category=category)}
        return [c for c in companies if c.get("id") in want][:limit]
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


def _buyer_verdict(sc: dict, got: dict, buyer_only: bool) -> str | None:
    """The scope door, plus the one refusal the door itself cannot phrase.

    A reply with no `scope` key at all is not a malformed scope answer, it is
    a question that went unanswered, and check_buyer would report it as a
    missing confidence field - true, unhelpful, and the kind of refusal a
    person reads in the gate and cannot act on. It gets its own sentence.
    """
    if not buyer_only and not isinstance(got.get("buyer"), dict):
        return ("0. the reply carried no `buyer` object at all; the buyer "
                "question was not answered.")
    return agents.check_buyer(sc, agents._profile_texts(sc))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--write", action="store_true",
                    help="with --recheck-refused, actually reopen them")
    ap.add_argument("--recheck-refused", action="store_true",
                    help="re-run the DOOR over write-ups it already refused, "
                         "with no model call. A refusal the door itself was "
                         "wrong about does not need new prose")
    ap.add_argument("--retry-refused", action="store_true",
                    help="re-ask write-ups the door refused, after a brief fix")
    ap.add_argument("--buyer-only", action="store_true",
                    help="ask ONLY who buys, for companies whose write-up is "
                         "already landed and whose buyer nobody has read")
    ap.add_argument("--no-fetch", action="store_true",
                    help="read the cached site record instead of re-fetching. "
                         "ON by default with --buyer-only: those pages are "
                         "already on disk and re-crawling 500 sites to ask a "
                         "question of bytes we hold is traffic nobody owes us")
    ap.add_argument("--fetch", dest="force_fetch", action="store_true",
                    help="with --buyer-only: re-fetch anyway, for a company "
                         "whose cached pages are stale enough to matter")
    ap.add_argument("--no-repair", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.recheck_refused:
        return recheck_refused(a.limit or 10 ** 6, a.write)

    # BEFORE THE FETCH. See the docstring: a run that fetches a hundred sites
    # and then finds it cannot ask anything has spent goodwill for nothing.
    if not llm.key() and not a.dry_run:
        print("no ANTHROPIC_API_KEY and no key file: nothing fetched, nothing "
              "asked, nothing spent.")
        return 0

    rows = tonight(a.category, a.id, a.limit, a.retry_refused, a.buyer_only)
    if not rows:
        print("nothing to write tonight")
        return 0
    print(f"{len(rows)} compan(y/ies) for tonight"
          + (f", category {a.category!r}" if a.category else ""))

    idx = fp.index()
    # CACHED BY DEFAULT FOR THE BUYER QUESTION. fp.visit always goes to the
    # network; the 1,447 companies brief_buyer can offer all have a site
    # record on disk already, fetched for a write-up and read for one question
    # when it could have been read for two. Re-crawling them to re-ask is a
    # cost paid by other people's servers for nothing.
    no_fetch = (a.no_fetch or a.buyer_only) and not a.force_fetch
    fetched, skipped = [], []
    for c in rows:
        cid = c["id"]
        was_readable = bool(idx.get(cid)) and not (idx.get(cid) or {}).get("unread")
        if a.dry_run or no_fetch:
            rec = fp.load(cid)
            if rec and not rec.get("unread"):
                fetched.append(c)
            elif not a.dry_run:
                # NOT A FACT ABOUT THE COMPANY. Nothing on disk means nothing
                # was ever fetched, or the fetch that ran failed; either way
                # this run learned nothing about them and says so.
                skipped.append((c.get("name"), "no readable pages on disk"))
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
    if not a.dry_run and not no_fetch:
        fp.save_index(idx)
    print(f"  {len(fetched)} readable, {len(skipped)} skipped "
          f"(a failed fetch tonight is not a fact about the company)"
          + ("  [read off disk, nothing fetched]" if no_fetch else ""))
    if not fetched:
        print("  nothing readable; the bodies are gitignored, so a fresh "
              "runner starts with none and this is what that looks like.")
        return 0

    # BOTH QUESTIONS OFF ONE SET OF PAGES, or the buyer question alone for the
    # 505 companies whose write-up is already landed and whose buyer nobody
    # ever read. Same doors either way.
    if a.buyer_only:
        briefs = agents.brief_buyer(ids=[c["id"] for c in fetched], limit=a.limit)
        task = BUYER_TASK
    else:
        briefs = [dict(b, buyer_rules=agents.BUYER_RULES)
                  for b in agents.brief_profile(ids=[c["id"] for c in fetched],
                                                limit=a.limit)]
        task = TASK
    print(f"  {len(briefs)} brief(s) built")
    if a.dry_run:
        if briefs:
            b = dict(briefs[0])
            b["pages"] = [{"url": p["url"], "lines": p["lines"][:6]} for p in b["pages"]]
            print(f"\n--- task ---\n{task}")
            print(f"\n--- one brief ---\n{json.dumps(b, indent=1)[:1800]}\n...")
        print(f"\ndry run: {len(briefs)} request(s) would be sent, nothing spent")
        return 0

    kept, scoped = [], []
    refused_run, buyer_refused_run = 0, 0
    in_row, buyer_in_row, cut_off = 0, 0, 0
    for i, b in enumerate(briefs, 1):
        try:
            # THINKING OFF, AND THIS IS THE SECOND TIME. llm.ask defaults
            # `thinking` to True, which sends {"type": "adaptive"}, and the
            # tailoring path already learned what that costs: the whole
            # 8,000-token output budget spent on a thinking block that
            # produced zero characters of text, $3.40 and two wrong diagnoses
            # before anyone looked at the flag. Everything an answer needs is
            # in the brief - the company's own pages and the door's rules -
            # and this is the OVERNIGHT path, which spends unattended.
            got = llm.ask(task, json.dumps(b, indent=1),
                          "buyer" if a.buyer_only else "profile",
                          model=a.model, max_tokens=llm.MAX_OUTPUT,
                          thinking=False)
        except llm.Refused as e:
            print(f"  stopping at {i}: {e}", file=sys.stderr)
            break
        if got is None:
            # A TRUNCATED ANSWER IS PAID FOR AND SILENT. llm._json_from
            # refuses to repair one, so it arrives here as None and the loop
            # used to move on saying nothing. The buyer question adds a field
            # to a reply that already measured at ~2,200 of 8,000 output
            # tokens, which should be nowhere near the cap - and "should be"
            # is exactly the kind of claim this file is supposed to measure
            # rather than assume. Counted and named at the end.
            cut_off += llm.LAST_STOP == "max_tokens"
            continue
        prof, sc = split_answer(got, b["id"], a.buyer_only)

        # THE DOORS, HERE, against the corpus they will use at intake.
        # A scope-only run stores no write-up, so there is none to refuse -
        # and running check_profile over an absent one would manufacture a
        # refusal about a claim nobody made.
        why = None
        if not a.buyer_only:
            why = agents.check_profile(prof, agents._profile_texts(prof))
        sc_why = _buyer_verdict(sc, got, a.buyer_only)

        if (why or sc_why) and not a.no_repair:
            # ONE REPAIR CALL FOR BOTH, naming both refusals. A second request
            # per half would double the bill to fix two things the model can
            # see at once.
            told = "\n    ".join(x for x in (why, sc_why) if x)
            got2 = llm.ask(task + "\n\n" + REPAIR.format(why=told),
                           json.dumps(b, indent=1),
                           "buyer-repair" if a.buyer_only else "profile-repair",
                           model=a.model, max_tokens=llm.MAX_OUTPUT,
                           thinking=False)
            if got2:
                prof2, sc2 = split_answer(got2, b["id"], a.buyer_only)
                # EACH HALF IS TAKEN ON ITS OWN MERITS, and mixing them is
                # honest precisely because they are independent claims: both
                # answers were written from the same pages and each half kept
                # here passed the same door against the same bytes. Taking the
                # repair wholesale would throw away a sound write-up to rescue
                # a buyer sentence, or the reverse.
                if why is not None and agents.check_profile(
                        prof2, agents._profile_texts(prof2)) is None:
                    prof, why = prof2, None
                if sc_why is not None:
                    sc_why2 = _buyer_verdict(sc2, got2, a.buyer_only)
                    if sc_why2 is None:
                        sc, sc_why = sc2, None

        common = dict(name=b.get("name"), sector=b.get("sector"),
                      category=b.get("category"),
                      saw={"pages": [{"url": p["url"], "sha": p.get("sha")}
                                     for p in b["pages"]]})
        if not a.buyer_only:
            kept.append(dict(prof, kind="profile", key=f"profile:{b['id']}",
                             also_known_as=b.get("also_known_as") or [],
                             **common))
            in_row = in_row + 1 if why else 0
            refused_run += bool(why)
        # A SCOPE ANSWER IS STORED EVEN WHEN THE DOOR REFUSED IT, the same way
        # a refused write-up is: the gate review reads refusals by rule, and a
        # door nobody can see being wrong is a door nobody fixes.
        scoped.append(dict(sc, kind="buyer", key=f"buyer:{b['id']}", **common))
        buyer_in_row = buyer_in_row + 1 if sc_why else 0
        buyer_refused_run += bool(sc_why)

        line = ("REFUSED - " + why[:52] if why else "passed") if not a.buyer_only else None
        sline = "REFUSED - " + sc_why[:52] if sc_why else "passed"
        print(f"  {i}/{len(briefs)}: "
              + (f"write-up {line}, " if line else "")
              + f"buyer {sline}")

        # A BROKEN PROMPT MUST NOT BURN A HUNDRED COMPANIES. They would be in
        # the store as refusals and neither brief offers those again. TWO
        # counters, because the two halves break independently: a scope rule
        # that refuses everything would otherwise run all night behind a
        # write-up prompt that is working perfectly.
        if in_row >= BREAKER or buyer_in_row >= BREAKER:
            which = "write-up" if in_row >= BREAKER else "buyer"
            print(f"\n  STOPPING: {BREAKER} {which} refusals in a row. "
                  f"Something is wrong with the prompt or the door, not with "
                  f"these companies - and every one asked is one the brief "
                  f"will never offer again.", file=sys.stderr)
            break

    calls, usd = llm.spent()
    print(f"\n{len(scoped)} buyer answer(s), {buyer_refused_run} refused"
          + (f"; {len(kept)} write-up(s), {refused_run} refused" if kept else "")
          + f"; {calls} request(s), ${usd:.2f}")
    if cut_off:
        print(f"  {cut_off} reply/replies were CUT OFF at max_tokens and paid "
              f"for. Both halves of those are lost - shorten the ask or raise "
              f"the cap before the next run.", file=sys.stderr)
    for i in range(0, len(kept), INGEST_BATCH):
        rep = agents.ingest("profile", kept[i:i + INGEST_BATCH],
                            model=f"{a.model}:write-profiles")
        print(f"  write-up ingest: {rep['kept']} through, {len(rep['refused'])} refused")
    for i in range(0, len(scoped), INGEST_BATCH):
        rep = agents.ingest("buyer", scoped[i:i + INGEST_BATCH],
                            model=f"{a.model}:write-profiles")
        print(f"  buyer ingest: {rep['kept']} through, {len(rep['refused'])} refused")
    cat = a.category or "<category>"
    print(f"\nPending in the admin. Gate and land each half separately:")
    if kept:
        print(f"  python3 scripts/promote_profiles.py --gate {cat!r} --self")
    print(f"  python3 scripts/promote_profiles.py --gate-buyer {cat!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
