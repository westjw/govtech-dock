#!/usr/bin/env python3
"""The gate review for company write-ups, and the door that lands them.

    python3 scripts/promote_profiles.py                      # the funnel
    python3 scripts/promote_profiles.py --gate               # every category waiting
    python3 scripts/promote_profiles.py --land-all --by owner
    python3 scripts/promote_profiles.py --gate Police        # what a person reads
    python3 scripts/promote_profiles.py --show brinc         # one, sentence by source
    python3 scripts/promote_profiles.py --land Police --by owner
    python3 scripts/promote_profiles.py --reject brinc --why "..." --by owner
    python3 scripts/promote_profiles.py --hide brinc --why "..." --by owner
    python3 scripts/promote_profiles.py --gate-buyer Police     # who buys, reviewed
    python3 scripts/promote_profiles.py --land-buyer Police --by owner

"DOOR ONLY, ADD SOME GATE REVIEWS" is the owner's ruling on how 2,024
write-ups reach public pages, and this is that shape. The door is
agents.check_profile: every sentence quotes the company's own site, every
named thing appears on it, no marketing adjectives, no first person. What
passes the door is landed by --land, a category at a time. What a person
reads before landing is --gate, and it is exception-based, not exhaustive:

  1. every proposal the door REFUSED, with the rule that refused it and the
     token it named - so a rule that is too tight is visible
  2. every proposal at medium, low or unsure confidence
  3. a 5% random sample of what passed, sentence beside quote beside URL

--land refuses until --gate has printed that category in this checkout, the
same way promote_rivals refuses a bulk accept nobody has read. Each landed
batch is ONE journal entry, so admin_undo takes a whole batch back. And
--hide is the kill switch: a person who sees a wrong write-up on the public
page throws profile_hidden on that company, journalled, and the page shows
the one-line record again by the next build.

WRITES LAND IN CHUNKS OF 500. journal.RUNAWAY refuses any save that
rewrites more than a third of the records even with force, and 2,024 of
2,063 is 98%. That guard is right and this script works within it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import agents                                                   # noqa: E402

READ = DATA / ".profiles_read"       # categories --gate has printed, and WHEN

# THE MARKER USED TO BE A LIST OF NAMES AND THAT WAS NOT ENOUGH. "Somebody
# gated Police" says nothing about WHICH write-ups they read. A category gated
# last week and written to again tonight carries the same mark, so tonight's
# arrivals land behind a review that never saw them - and the more write-ups
# arrive, the more the mark certifies work nobody did. Found the day a bulk
# --land-all was built on top of it: 69 new proposals sat in categories all
# marked read, and the command would have published every one.
#
# So it records a TIME per category, and a proposal written after that time is
# held back and named. The legacy list is migrated to the moment before the
# run that produced everything then on file, because that is what those gate
# reviews actually covered - and not one second later.
LEGACY_GATE = "2026-09-03T23:59:59"
# A SEPARATE FILE, AND NOT FOR TIDINESS. Reading a category's write-ups tells
# a person nothing about whether its buyer answers are sound - different
# claims, different evidence, different failure. One file would let a gate
# review of the prose unlock the landing of 500 scope verdicts nobody read.
BUYER_READ = DATA / ".buyer_read"
# TWO, MEASURED. Six was a guess and four of five requests came back cut off
# at max_tokens - $1.38 for two usable answers. A write-up is two or three
# paragraphs with a verbatim quote per sentence, roughly 1,200 output tokens,
# and adaptive thinking is billed out of the same 8,000. Two fits with room.
# ONE. Measured three times, and each measurement was paid for. Six: eleven
# of thirteen requests cut off at max_tokens. Two, with thinking switched
# off: still cut off - because Sonnet 5 emits a thinking block whether or not
# one is asked for, so `thinking=False` does not buy the budget back. A single
# write-up completes in about 2,200 output tokens with room to spare, and one
# per request is the only size that has never truncated.
SELF_BATCH = 1

SELF_RULES = """You are writing a description of a company from its own web
pages. Another reader has answered this question before you, and you are not
told what they said - answer it yourself, from the pages in front of you, as
if nobody had.

Your answer will be compared to theirs. Agreement means nobody has to look at
it; disagreement means a person does. So an answer you are not sure of is
worth more as "unsure" than as a guess that happens to match.

Never write a fact the pages do not state. If the pages will not support two
or three paragraphs, answer unsure - that is a complete and useful answer."""
CHUNK = 500
SAMPLE = 0.05


def _read_gates() -> dict:
    """{category: when it was gated}. Tolerates the old list-of-names shape."""
    if not READ.exists():
        return {}
    try:
        raw = json.loads(READ.read_text())
    except Exception:                                           # noqa: BLE001
        return {}
    if isinstance(raw, list):
        return {c: LEGACY_GATE for c in raw}
    return raw if isinstance(raw, dict) else {}


def _mark_gated(category: str) -> None:
    gates = _read_gates()
    gates[category] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    READ.write_text(json.dumps(gates, indent=1))


def _after_gate(p: dict, when: str | None) -> bool:
    """Was this write-up created after the gate review that covers it?

    A PROPOSAL WITH NO TIMESTAMP IS TREATED AS UNREAD. It cannot prove it was
    on screen, and the asymmetric rule applies: publishing a description of
    somebody else's company that nobody reviewed is the expensive mistake,
    holding one back for a second look is the cheap one.
    """
    if not when:
        return True
    at = p.get("at")
    return not isinstance(at, str) or at > when


def _by_category(store: dict, companies: list, kind: str = "profile") -> dict:
    cat = {c["id"]: (c.get("category") or "?") for c in companies if c.get("id")}
    out: dict[str, list] = {}
    for k, p in store.items():
        if not isinstance(p, dict) or p.get("kind") != kind:
            continue
        out.setdefault(cat.get(p.get("id"), "?"), []).append((k, p))
    return out


def show(p: dict, name: str) -> None:
    """One write-up, every sentence with its quote and URL beneath it.

    The raw text beside the reading. A person ruling on a sentence about a
    real company's customers should see the words on the page it came from
    without opening anything.
    """
    print(f"\n  {name}  [{p.get('confidence')}]  {p.get('status')}"
          + (f"  REFUSED: {p['refused_why']}" if p.get("status") == "refused" else ""))
    if p.get("why"):
        print(f"     thesis: {p['why'][:160]}")
    paras = p.get("paragraphs") or (p.get("proposal") or {}).get("paragraphs") or []
    for i, para in enumerate(paras):
        for j, s in enumerate(para if isinstance(para, list) else []):
            print(f"     [{i}.{j}] {s.get('text', '')}")
            print(f"           \"{(s.get('quote') or '')[:110]}\"")
            print(f"           {s.get('url', '')}")
    q = p.get("quote") or (p.get("proposal") or {}).get("quote")
    if isinstance(q, dict) and q.get("text"):
        print(f"     pull quote: \"{q['text'][:120]}\"  {q.get('url', '')}")


def gate(store: dict, companies: list, category: str, seed: int = 0) -> dict:
    """What a person reads for one category. Returns the three lists."""
    names = {c["id"]: c.get("name", c["id"]) for c in companies if c.get("id")}
    rows = _by_category(store, companies).get(category, [])
    refused = [(k, p) for k, p in rows if p.get("status") == "refused"]
    pending = [(k, p) for k, p in rows if p.get("status") == "pending"]
    low = [(k, p) for k, p in pending
           if (p.get("confidence") or "unsure") in ("medium", "low", "unsure")]
    high = [(k, p) for k, p in pending if (k, p) not in low]
    rng = random.Random(seed or dt.date.today().toordinal())
    sample = rng.sample(high, max(1, int(len(high) * SAMPLE))) if high else []

    print(f"GATE REVIEW: {category}  ({len(rows)} write-ups on file)")
    print(f"\n== 1. Refused by the door: {len(refused)} ==")
    by_rule: dict[str, int] = {}
    for _, p in refused:
        rule = (p.get("refused_why") or "?").split(".")[0][:60]
        by_rule[rule] = by_rule.get(rule, 0) + 1
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print(f"   {n:4}  {rule}")
    for k, p in refused[:12]:
        print(f"   - {names.get(p.get('id'), p.get('id'))}: {p.get('refused_why', '')[:120]}")
    print(f"\n== 2. Medium / low / unsure confidence: {len(low)} ==")
    for k, p in low:
        show(p, names.get(p.get("id"), p.get("id")))
    print(f"\n== 3. Sample of what passed: {len(sample)} of {len(high)} ==")
    for k, p in sample:
        show(p, names.get(p.get("id"), p.get("id")))
    _mark_gated(category)
    print(f"\n  Land the {len(pending)} pending:  python3 scripts/promote_profiles.py "
          f"--land {category!r} --by owner")
    return {"refused": refused, "low": low, "sample": sample, "pending": pending}


def show_buyer(p: dict, name: str) -> None:
    """One buyer answer, with the sentence off their page under each verdict."""
    print(f"\n  {name}  [{p.get('confidence')}]  {p.get('status')}"
          + ("  SLED-ELIGIBLE" if p.get("sled_eligible") else "")
          + (f"  REFUSED: {p['refused_why']}" if p.get("status") == "refused" else ""))
    src = p if p.get("sells_to_gov") else (p.get("proposal") or {})
    print(f"     sells to government: {src.get('sells_to_gov')}"
          f"   names other buyers: {src.get('names_other_buyers')}")
    print(f"     buyer: {(src.get('buyer') or '')[:170]}")
    for label, q, u in (("gov", src.get("buyer_quote"), src.get("buyer_url")),
                        ("other", src.get("other_quote"), src.get("other_url"))):
        if q:
            print(f"       {label}: \"{str(q)[:110]}\"")
            print(f"            {u or ''}")
    if src.get("why"):
        print(f"     why: {str(src['why'])[:160]}")


def gate_buyer(store: dict, companies: list, category: str, seed: int = 0) -> dict:
    """What a person reads before any buyer answer in a category lands.

    THE EXCEPTION LIST IS NOT THE WRITE-UPS' EXCEPTION LIST, because the two
    answers go wrong differently. A write-up goes wrong by asserting a fact
    the pages do not carry, which the door catches by name. A buyer answer
    goes wrong in two ways the door cannot see:

      A 'NO' IS A SCOPE RULING AND IT BELONGS TO A PERSON. The 2026-09-11
      pass said so in its own docstring and left three companies untouched
      for exactly this reason. It is shown in full, every one, never sampled.

      SLED-ELIGIBLE IS THE ONE THAT DELETES THINGS. build_board drops every
      posting whose title does not name the public sector on a company
      carrying sled_only, and a wrong flag takes real jobs off a public board
      with no mark left behind. Every eligible row is shown in full, and
      landing the flag takes a second, separate word on the command line.
    """
    names = {c["id"]: c.get("name", c["id"]) for c in companies if c.get("id")}
    posts = _postings_by_company(companies)
    rows = _by_category(store, companies, "buyer").get(category, [])
    refused = [(k, p) for k, p in rows if p.get("status") == "refused"]
    pending = [(k, p) for k, p in rows if p.get("status") == "pending"]
    says_no = [(k, p) for k, p in pending if p.get("sells_to_gov") == "no"]
    sled = [(k, p) for k, p in pending if p.get("sled_eligible")]
    low = [(k, p) for k, p in pending
           if (p.get("confidence") or "unsure") in ("medium", "low", "unsure")
           and (k, p) not in says_no and (k, p) not in sled]
    rest = [(k, p) for k, p in pending
            if (k, p) not in says_no and (k, p) not in sled and (k, p) not in low]
    rng = random.Random(seed or dt.date.today().toordinal())
    sample = rng.sample(rest, max(1, int(len(rest) * SAMPLE))) if rest else []

    print(f"SCOPE GATE REVIEW: {category}  ({len(rows)} buyer answer(s) on file)")
    print(f"\n== 1. Refused by the door: {len(refused)} ==")
    by_rule: dict[str, int] = {}
    for _, p in refused:
        rule = (p.get("refused_why") or "?").split(".")[0][:60]
        by_rule[rule] = by_rule.get(rule, 0) + 1
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        print(f"   {n:4}  {rule}")
    for k, p in refused[:12]:
        print(f"   - {names.get(p.get('id'), p.get('id'))}: {p.get('refused_why', '')[:120]}")

    print(f"\n== 2. Read as NOT selling to government: {len(says_no)} ==")
    print("   A scope ruling is a person's. Landing these records the answer "
          "and removes nothing.")
    for k, p in says_no:
        show_buyer(p, names.get(p.get("id"), p.get("id")))

    print(f"\n== 3. Eligible for sled_only: {len(sled)} ==")
    if sled:
        n_posts = sum(posts.get(p.get("id"), 0) for _, p in sled)
        # THE COST, SAID PLAINLY AND BEFORE THE FACT. scope_sweep prints the
        # same number for the same reason: a flag whose price is invisible
        # until after it is paid is not a decision anybody made.
        print(f"   These carry {n_posts} posting(s) between them. Landing the "
              f"flag keeps only the roles whose titles name the public "
              f"sector; the rest stop appearing.")
    for k, p in sled:
        show_buyer(p, names.get(p.get("id"), p.get("id")))

    print(f"\n== 4. Medium / low / unsure confidence: {len(low)} ==")
    for k, p in low:
        show_buyer(p, names.get(p.get("id"), p.get("id")))
    print(f"\n== 5. Sample of the rest: {len(sample)} of {len(rest)} ==")
    for k, p in sample:
        show_buyer(p, names.get(p.get("id"), p.get("id")))

    read = json.loads(BUYER_READ.read_text()) if BUYER_READ.exists() else []
    if category not in read:
        read.append(category)
        BUYER_READ.write_text(json.dumps(read))
    print(f"\n  Land the {len(pending)} pending:  python3 scripts/promote_profiles.py "
          f"--land-buyer {category!r} --by owner")
    if sled:
        print(f"  The {len(sled)} sled_only flag(s) need a second word: "
              f"add --with-sled")
    return {"refused": refused, "no": says_no, "sled": sled,
            "low": low, "sample": sample, "pending": pending}


def _postings_by_company(companies: list) -> dict:
    """How many live postings each company carries, or {} if no board is built."""
    try:
        board = json.loads((DATA / "board.json").read_text())
    except Exception:
        return {}
    out: dict = {}
    for p in board.get("postings", []):
        cid = p.get("company_id")
        if cid:
            out[cid] = out.get(cid, 0) + 1
    return out


def land_buyer(store: dict, companies: list, keys: list[str], by: str,
               why: str, with_sled: bool = False) -> dict:
    """Write accepted buyer answers onto companies.json.

    THE SAME FIELDS THE 2026-09-11 PASS WROTE, deliberately, because a second
    shape for the same fact is a second thing every reader has to learn:
    `buyer`, `sells_to_gov`, `buyer_source`, `buyer_checked_on`.

    IT NEVER OVERWRITES AN ANSWER ALREADY ON FILE. brief_buyer does not offer
    a company that carries `sells_to_gov`, so a row reaching here with one is
    a company somebody answered BETWEEN the ask and the landing - by hand, or
    by the scope pass. The newer answer is not automatically the better one
    and this is not the place to decide; it is skipped, counted and NAMED,
    never silently dropped.

    SLED_ONLY IS SEPARATE AND OPT-IN. It is the only field here that changes
    what the public board shows, and what it does is subtract. So it needs
    `with_sled`, it needs the row to have cleared buyer_sled_eligible at the
    door, and it is never removed here - taking a flag off is its own
    decision with its own evidence.
    """
    seq = companies if isinstance(companies, list) else list(companies.values())
    index = {c["id"]: c for c in seq if c.get("id")}
    today = dt.date.today().isoformat()
    wrote = sled = 0
    held: list = []
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start:start + CHUNK]
        n = 0
        for k in chunk:
            p = store.get(k)
            if not p or p.get("status") != "pending":
                continue
            if p.get("id") not in index:
                # NEVER SILENTLY SKIP. A row whose id names no company on file
                # is the mis-keyed shape ingest now refuses at the door; one
                # already on file must still be counted and named rather than
                # vanishing out of a landing that reports success.
                held.append((p.get("name") or p.get("id"),
                             f"id {p.get('id')!r} is not a company on file"))
                continue
            c = index[p["id"]]
            if not p.get("sells_to_gov") or not p.get("buyer"):
                # A REFUSED ANSWER STORES NO VERDICT, and a pending row with
                # none is a shape nothing here can land. Counted, not skipped.
                held.append((c.get("name") or p["id"], "carries no verdict"))
                continue
            if c.get("sells_to_gov"):
                held.append((c.get("name") or p["id"],
                             f"already answered {c['sells_to_gov']!r} on "
                             f"{c.get('buyer_checked_on') or 'an earlier pass'}"))
                continue
            c["buyer"] = p["buyer"]
            c["sells_to_gov"] = p["sells_to_gov"]
            c["buyer_source"] = p.get("buyer_url") or None
            c["buyer_checked_on"] = today
            if (p.get("why") or "").strip():
                c["scope_note"] = str(p["why"]).strip()[:600]
            if with_sled and p.get("sled_eligible") and not c.get("sled_only"):
                c["sled_only"] = True
                c["sled_only_why"] = (
                    f"scope read {today}: their own pages name no "
                    f"non-government buyer. {str(p['buyer'])[:180]}")
                sled += 1
            p["status"] = "accepted"
            p["ruled_by"], p["ruled_on"], p["ruled_why"] = by, today, why
            n += 1
        if not n:
            continue
        bad = admin.save_companies(companies, "promote-buyer",
                                   why=why or f"landed {n} buyer answer(s)",
                                   by=by, force=n > 25)
        if bad:
            print(f"  REFUSED by the journal: {bad}")
            return {"wrote": wrote, "sled": sled, "held": held}
        # ONE LANDING, ONE DECISION ABOUT ITS SIZE - the scar `land` carries
        # two functions up: save_companies took 99 rows onto the public file
        # and journal.BLAST then refused to stamp them accepted, leaving the
        # two halves disagreeing about what happened.
        bad = agents.save(store, "promote-buyer", why=why, by=by, force=n > 25)
        if bad:
            print(f"  REFUSED by the journal (store): {bad}")
            return {"wrote": wrote, "sled": sled, "held": held}
        wrote += n
        print(f"  landed {n} (journal entry {start // CHUNK + 1})")
    if held:
        print(f"  held back {len(held)}, each named:")
        for name, reason in held[:20]:
            print(f"     {str(name)[:36]:38} {reason}")
        if len(held) > 20:
            print(f"     ... and {len(held) - 20} more")
    return {"wrote": wrote, "sled": sled, "held": held}


def gate_map(store: dict, companies: list) -> dict:
    """Every category with write-ups waiting, and what each one would cost.

    THE FRICTION THIS REMOVES IS REAL AND IT WAS MINE. 291 landable write-ups
    sit across 55 categories, and the only way to reach them was --gate with a
    category name you had to already know. Nothing listed them. So the queue
    that a person is meant to work through was addressable only by guessing
    its addresses, which is the 131-unreachable-rows shape wearing a
    friendlier face.

    IT MARKS NOTHING AS READ, deliberately. --land refuses until --gate has
    PRINTED that category's exceptions, because the owner's ruling was "door
    only, add some gate reviews" and the review is the part a person does.
    A map of the work is not the work. Printing 291 write-ups in one wall of
    text and stamping all 55 read would manufacture the appearance of a review
    and destroy the only thing the marker is for.
    """
    names = {c["id"]: c.get("name", c["id"]) for c in companies if c.get("id")}
    read = json.loads(READ.read_text()) if READ.exists() else []
    by = _by_category(store, companies)
    rows = []
    for cat, rs in by.items():
        pend = [(k, p) for k, p in rs if p.get("status") == "pending"]
        land = [(k, p) for k, p in pend if _has_text(p)]
        if not pend and not any(p.get("status") == "refused" for _, p in rs):
            continue
        rows.append({
            "category": cat,
            "landable": len(land),
            "high": sum(1 for _, p in land if p.get("confidence") == "high"),
            "read_first": sum(1 for _, p in land
                              if (p.get("confidence") or "unsure") != "high"),
            "unsure": len(pend) - len(land),
            "refused": sum(1 for _, p in rs if p.get("status") == "refused"),
            "gated": cat in read,
        })
    rows.sort(key=lambda r: -r["landable"])
    tot = {k: sum(r[k] for r in rows)
           for k in ("landable", "high", "read_first", "unsure", "refused")}
    print(f"WRITE-UPS WAITING: {tot['landable']} landable across "
          f"{len(rows)} categor(y/ies)\n")
    print(f"  {'category':34} {'land':>5} {'high':>5} {'read':>5} "
          f"{'unsure':>7} {'refused':>8}  gated")
    for r in rows:
        print(f"  {r['category'][:34]:34} {r['landable']:5} {r['high']:5} "
              f"{r['read_first']:5} {r['unsure']:7} {r['refused']:8}"
              f"  {'yes' if r['gated'] else '-'}")
    print(f"  {'TOTAL':34} {tot['landable']:5} {tot['high']:5} "
          f"{tot['read_first']:5} {tot['unsure']:7} {tot['refused']:8}")
    print(f"\n  land   = carries paragraphs, could go on a public page")
    print(f"  high   = the door passed it cleanly; --gate samples these")
    print(f"  read   = medium/low; --gate shows EVERY one, in full")
    print(f"  unsure = a complete answer with nothing to land")
    ungated = [r for r in rows if not r["gated"] and r["landable"]]
    print(f"\n  {len(ungated)} categor(y/ies) have never been gated. Read one:")
    for r in ungated[:3]:
        print(f"     python3 scripts/promote_profiles.py --gate {r['category']!r}")
    print(f"\n  Then land everything gated, in one journalled batch each:")
    print(f"     python3 scripts/promote_profiles.py --land-all --by owner")
    print(f"     ... add --high-only to leave medium/low/unsure for a person")
    return {"rows": rows, "totals": tot}


def _claims(text: str) -> set:
    """The named things and numbers a write-up asserts.

    THE DOOR ALREADY PROVED EACH TOKEN IS ON A PAGE. What it cannot see is a
    true token inside a false claim - a partner described as a customer, a
    number attached to the wrong product, two real names joined by a
    relationship the page never states. Every token passes rule 5 and the
    sentence is still wrong.

    So this is not a second provenance check. It is the set of things the
    write-up NAMES, so it can be compared against what an independent reader
    of the same pages thought worth naming.
    """
    import agents
    out = set()
    cased = text or ""
    for m in agents._PF_NUMBER.finditer(cased):
        out.add(m.group(0))
    def named(w: str) -> bool:
        """A LONE CAPITALISED WORD IS USUALLY NOT A NAME. "Nothing", "Water",
        "Municipal" all start sentences or clauses and reporting them as
        claims buries the two that matter. A single word counts only when it
        LOOKS like a name - internal capitals or a digit, which is what
        EcoVadis, DR4900 and InfoSense have and what an ordinary word does
        not. Multi-word runs always count: "Dallas Police", "Amazon Web
        Services" are exactly the claims a second reader should have made
        too."""
        return any(c.isdigit() for c in w) or any(c.isupper() for c in w[1:])

    run: list[str] = []
    for raw in cased.split():
        bare = raw.strip(agents._PF_EDGE_PUNCT)
        # A SENTENCE-OPENING CAPITAL IS NOT A NAME, and the first version of
        # this reported "only the first reader names Another, Nothing, One,
        # Buyers" - every one of them the first word of a sentence. The door
        # solved this already; _PF_OPENING_WORDS is its list, reused rather
        # than restated.
        if (bare[:1].isupper() and len(bare) > 2
                and bare.lower() not in agents._PF_OPENING_WORDS):
            run.append(bare)
        else:
            if len(run) >= 2 or (len(run) == 1 and named(run[0])):
                out.add(" ".join(run))
            run = []
    if len(run) >= 2 or (len(run) == 1 and named(run[0])):
        out.add(" ".join(run))
    return {x for x in out if len(x) > 2}


def self_read(store: dict, companies: list, category: str, model: str,
              limit: int | None, dry: bool) -> list:
    """A blind second reader on every pending write-up. Returns disagreements.

    THE SAMPLE WAS A COMPROMISE WITH COST AND IT NO LONGER HAS TO BE. Reading
    5% and hoping was right when a second read meant a person; at roughly
    four tenths of a cent each, all 545 outstanding write-ups can be re-read
    for about two dollars. So every one gets a second reader and a person
    reads only where the two disagree.

    BLIND, and that is the whole mechanism. The second reader is handed the
    same pages and asked the same question. It never sees the first write-up,
    its confidence, or that a first write-up exists. CLAUDE.md's reason: an
    attempt known to be the main attempt gets defended, and a reviewer who can
    see whose work they are holding grades the author. A second reader shown
    the first answer agrees with it almost always, which looks exactly like
    verification and is worth nothing.

    WHAT COUNTS AS DISAGREEMENT. Not the prose - two honest readers write
    different sentences from the same page and both are right. Two things:
    the second reader declining to write at all where the first was confident,
    and a NAMED THING the first asserts that the second, reading the same
    pages, never mentioned. The door already proved every token sits on a
    page; what it cannot catch is a true token inside a false claim, and that
    is exactly the shape a lone confident answer takes.
    """
    import agents
    import llm

    names = {c["id"]: c.get("name", c["id"]) for c in companies if c.get("id")}
    rows = [(k, p) for k, p in _by_category(store, companies).get(category, [])
            if p.get("status") == "pending" and _has_text(p)]
    if limit:
        rows = rows[:limit]
    if not rows:
        print(f"\nno pending write-ups in {category!r} to re-read")
        return []

    briefs = []
    for k, p in rows:
        saw = p.get("saw") or {}
        pages = saw.get("pages") or []
        texts = agents._profile_texts(p)
        # the SAME pages, by url, read fresh off disk - never the first
        # write-up, and never anything derived from it
        keep = [{"url": u, "text": t[:agents.PROFILE_PAGE_CHARS]}
                for u, t in texts.items()
                if not pages or any(pg.get("url") == u for pg in pages)]
        if not keep:
            continue
        briefs.append({"key": k, "id": p.get("id"), "name": names.get(p.get("id")),
                       "sector": p.get("sector"), "category": p.get("category"),
                       "pages": keep, "rules": agents.PROFILE_RULES})
    if not briefs:
        print("\nnothing carries the pages it was written from")
        return []

    sysm = (f"{SELF_RULES}\n\nWrite a description of each company below using "
            f"ONLY the pages given for it, obeying `rules` exactly.\n\n"
            f'Return: {{"answers": [{{"id": <the exact id>, "confidence": '
            f'"high"|"medium"|"low"|"unsure", "paragraphs": [[{{"text": ..., '
            f'"url": ..., "quote": ...}}]], "why": <one sentence>}}]}}')
    if dry:
        print(f"\n--- second-read system ---\n{sysm[:700]}\n...")
        print(f"\n{len(briefs)} write-up(s) would be re-read blind, "
              f"in {-(-len(briefs) // SELF_BATCH)} request(s). Nothing spent.")
        return []

    seconds: dict = {}
    lots = [briefs[i:i + SELF_BATCH] for i in range(0, len(briefs), SELF_BATCH)]
    for i, lot in enumerate(lots, 1):
        try:
            # THINKING OFF, MEASURED. With adaptive thinking on, 11 of 13
            # requests were cut off at max_tokens even at two write-ups each -
            # two descriptions are ~2,400 output tokens and the other 5,600
            # went to thinking. This task is writing prose from pages that are
            # already in front of it, not reasoning, and $3.74 was spent
            # finding that out.
            got = llm.ask(sysm, json.dumps({"items": lot}, indent=1),
                          "profile-second-read", model=model,
                          max_tokens=llm.MAX_OUTPUT, thinking=False)
        except llm.Refused as e:
            print(f"  stopping at request {i}: {e}", file=sys.stderr)
            break
        for ans in ((got or {}).get("answers") or []):
            if ans.get("id"):
                seconds[ans["id"]] = ans
        if got is None and llm.LAST_STOP == "max_tokens":
            print(f"  request {i}/{len(lots)}: CUT OFF at max_tokens and paid "
                  f"for - lower SELF_BATCH", file=sys.stderr)
        print(f"  request {i}/{len(lots)}: {len(seconds)} re-read so far")

    out, unread = [], []
    for k, p in rows:
        second = seconds.get(p.get("id"))
        if not second:
            # A WRITE-UP NOBODY RE-READ IS NOT ONE TWO READERS AGREED ON, and
            # the first version of this counted it as agreement: 22 of 26 were
            # never answered and the summary said "everything else was written
            # twice, independently, and the two agreed". That is the exact
            # false reassurance this whole engine is shaped to refuse, and it
            # cost nothing to say because nobody had checked.
            unread.append((k, p))
            continue
        first_conf = p.get("confidence") or "unsure"
        second_conf = second.get("confidence") or "unsure"
        # 1. the second reader would not write at all
        if second_conf in ("unsure", "low") and first_conf == "high":
            out.append((k, p, second, f"the second reader answered "
                                      f"{second_conf} from the same pages"))
            continue
        # 2. a named thing only the first reader mentions
        f_text = " ".join(sent.get("text", "") for para in (p.get("paragraphs") or [])
                          for sent in (para if isinstance(para, list) else []))
        s_text = " ".join(sent.get("text", "") for para in (second.get("paragraphs") or [])
                          for sent in (para if isinstance(para, list) else []))
        only_first = _claims(f_text) - _claims(s_text)
        # THE COMPANY'S OWN NAME IS NOT A CLAIM. "only the first reader names
        # Badger Meter" is noise: both write-ups are about Badger Meter.
        own = {w for w in (p.get("name") or "").split() if len(w) > 2}
        only_first -= own
        only_first = {c for c in only_first
                      if not any(w in c.split() for w in own)}
        # AND A DIGIT GROUP IS NOT A NUMBER. "150,000" splits into "150" and
        # "000", and "000" reported as a fact the second reader missed is
        # noise that buries the one that matters.
        only_first = {c for c in only_first if c != "000" and not (
            c.isdigit() and any(c in d for d in _claims(s_text) if d.isdigit()))}
        # a name the second reader used INSIDE a longer run still counts as
        # mentioned; compare on the raw text, not just the extracted set
        only_first = {c for c in only_first if c.lower() not in s_text.lower()}
        if only_first:
            out.append((k, p, second, "only the first reader names "
                                      + ", ".join(sorted(only_first)[:4])))
    if unread:
        print(f"\n   {len(unread)} of {len(rows)} were NOT re-read - the "
              f"request they were in returned nothing usable. They are not "
              f"agreed, they are unchecked:", file=sys.stderr)
        for k, p in unread[:8]:
            print(f"     {str(p.get('name') or p.get('id'))[:44]}", file=sys.stderr)
    return out, unread


def _record(p: dict, by: str, today: str) -> dict:
    """The written shape: paragraphs as text, provenance beside them."""
    paras, prov = [], []
    for i, para in enumerate(p.get("paragraphs") or []):
        sents = para if isinstance(para, list) else []
        paras.append(" ".join((s.get("text") or "").strip() for s in sents).strip())
        for j, s in enumerate(sents):
            prov.append({"p": i, "s": j, "url": s.get("url"), "quote": s.get("quote")})
    seen = (p.get("saw") or {}).get("pages") or []
    return {"paragraphs": paras,
            "quote": p.get("quote") if isinstance(p.get("quote"), dict) else None,
            "provenance": prov,
            "sources": [{"url": pg.get("url"), "sha": pg.get("sha"),
                         "fetched_on": pg.get("fetched_on")} for pg in seen if pg.get("url")],
            "written_on": today, "by": p.get("by") or "agent",
            "ruled_by": by, "ruled_on": today}


def record_from_company(paragraphs: list, domain: str, ruled_by: str,
                        today: str | None = None) -> dict:
    """The same written shape, for a write-up the COMPANY sent us itself.

    IT LIVES HERE RATHER THAN IN THE CLAIM DOOR because a shape written in two
    places is two shapes that drift, and this one is read by build_board,
    build_site and two selftest fixtures. The claim door supplies the words;
    this decides what the record looks like.

    THE PROVENANCE IS EMPTY, AND THAT IS THE HONEST ANSWER, not a gap to fill.
    An agent's write-up carries a url and a quote per sentence because it read
    them off a page and a reader has to be able to check it. A company writing
    about itself IS the source: there is no page we fetched, no sentence we
    quoted, and manufacturing a citation that points at their homepage would
    dress an assertion up as a verified reading. `by` starts with "claim:" so
    build_board stamps by_kind="company" and the page says "in their own
    words, claimed page" instead of "written from their site" - which is the
    whole of what a reader needs to weigh it.
    """
    today = today or dt.date.today().isoformat()
    paras = [str(s).strip() for s in (paragraphs or []) if str(s).strip()]
    if not paras:
        raise ValueError("a write-up needs at least one paragraph")
    return {"paragraphs": paras, "quote": None, "provenance": [], "sources": [],
            "written_on": today, "by": f"claim:{domain}",
            "ruled_by": ruled_by, "ruled_on": today}


def land(store: dict, companies: list, keys: list[str], by: str, why: str) -> int:
    """Write accepted write-ups onto companies.json, at most CHUNK per journal entry."""
    seq = companies if isinstance(companies, list) else list(companies.values())
    index = {c["id"]: c for c in seq if c.get("id")}
    today = dt.date.today().isoformat()
    wrote = skipped = 0
    for start in range(0, len(keys), CHUNK):
        chunk = keys[start:start + CHUNK]
        n = 0
        for k in chunk:
            p = store.get(k)
            if not p or p.get("status") != "pending" or p.get("id") not in index:
                continue
            if not _has_text(p):
                # AN UNSURE ANSWER HAS NOTHING TO LAND. It passed the door
                # because unsure is a valid answer; landing it would write a
                # profile with no paragraphs, and the page would say "written
                # from their site" above nothing. It stays pending and is
                # counted so the person landing a category sees it.
                skipped += 1
                continue
            index[p["id"]]["profile"] = _record(p, by, today)
            index[p["id"]].pop("profile_hidden", None)
            p["status"] = "accepted"
            p["ruled_by"], p["ruled_on"], p["ruled_why"] = by, today, why
            n += 1
        if not n:
            continue
        bad = admin.save_companies(companies, "promote-profiles",
                                   why=why or f"landed {n} write-up(s)", by=by,
                                   force=n > 25)
        if bad:
            print(f"  REFUSED by the journal: {bad}")
            return wrote
        # THE COUNT IS SHOWN, THEN THE STORE WRITE IS FORCED, on the same
        # terms as the companies write above it. Without this the two halves
        # of one landing disagree: save_companies took 99 write-ups onto the
        # public file and journal.BLAST then refused to stamp them accepted,
        # leaving companies.json holding profiles the store still called
        # pending. One landing, one decision about its size.
        bad = agents.save(store, "promote-profiles", why=why, by=by, force=n > 25)
        if bad:
            print(f"  REFUSED by the journal (store): {bad}")
            return wrote
        wrote += n
        print(f"  landed {n} (journal entry {start // CHUNK + 1})")
    if skipped:
        print(f"  left {skipped} pending: unsure answers with no paragraphs, nothing to land")
    return wrote


def _has_text(p: dict) -> bool:
    return any(isinstance(para, list) and any((s.get("text") or "").strip() for s in para
                                              if isinstance(s, dict))
               for para in (p.get("paragraphs") or []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", metavar="CATEGORY", nargs="?", const="",
                    help="with a category, the exception review for it. With "
                         "NO category, a map of every category waiting - which "
                         "marks nothing as read, because a map is not a review")
    ap.add_argument("--land", metavar="CATEGORY")
    ap.add_argument("--land-all", action="store_true",
                    help="land every category a --gate has printed in this "
                         "checkout. Categories nobody has gated are refused "
                         "and named, never quietly skipped")
    ap.add_argument("--gate-buyer", metavar="CATEGORY",
                    help="what a person reads before any buyer answer lands")
    ap.add_argument("--land-buyer", metavar="CATEGORY",
                    help="land the buyer answers a --gate-buyer has printed")
    ap.add_argument("--with-sled", action="store_true",
                    help="with --land-buyer: ALSO set sled_only on the rows "
                         "the door found eligible. This subtracts postings "
                         "from the public board, so it is its own word")
    ap.add_argument("--show", metavar="ID")
    ap.add_argument("--reject", action="append", default=[], metavar="ID")
    ap.add_argument("--hide", action="append", default=[], metavar="ID")
    ap.add_argument("--unhide", action="append", default=[], metavar="ID")
    ap.add_argument("--why", default="")
    ap.add_argument("--by", default=None,
                    help='who is ruling: "owner", or "agent:<label>". Required to write.')
    ap.add_argument("--self", dest="self_read", action="store_true",
                    help="with --gate: a blind second reader on every pending "
                         "write-up; you read only the disagreements")
    ap.add_argument("--limit", type=int, help="with --self: how many to re-read")
    ap.add_argument("--model", default=None)
    ap.add_argument("--high-only", action="store_true",
                    help="with --land: land ONLY the high-confidence write-ups, "
                         "leaving medium/low/unsure pending for a person")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if (a.land or a.land_all or a.land_buyer or a.reject or a.hide
            or a.unhide) and not a.by:
        ap.error("--by is required to rule: \"owner\", or \"agent:<label>\"")

    store = agents.load()
    companies = admin.read_companies()
    seq = companies if isinstance(companies, list) else list(companies.values())
    names = {c["id"]: c.get("name", c["id"]) for c in seq if c.get("id")}

    if a.show:
        p = store.get(f"profile:{a.show}")
        if not p:
            print(f"no write-up proposal on file for {a.show!r}")
            return 1
        show(p, names.get(a.show, a.show))
        return 0

    if a.gate == "":
        # --gate WITH NO CATEGORY. `nargs="?"` makes the bare flag land here
        # as an empty string rather than None, which is how this stays one
        # flag instead of two that a person has to choose between.
        gate_map(store, seq)
        return 0

    if a.land_all:
        gates = _read_gates()
        by = _by_category(store, seq)
        done, skipped, total, unread, batch = [], [], 0, [], []
        for cat in sorted(by):
            rows = [(k, p) for k, p in by[cat]
                    if p.get("status") == "pending"
                    and (not a.high_only
                         or (p.get("confidence") or "unsure") == "high")]
            when = gates.get(cat)
            newer = [(k, p) for k, p in rows if _after_gate(p, when)]
            keys = [k for k, p in rows if not _after_gate(p, when)]
            if newer:
                unread.append((cat, len(newer)))
            if not rows:
                continue
            if cat not in gates:
                # NEVER QUIETLY SKIPPED. A category nobody gated is the whole
                # reason --land refuses, and a bulk landing that silently
                # passed over it would be the door alone wearing the gate's
                # name.
                skipped.append((cat, len(rows)))
                continue
            if keys:
                done.append((cat, len(keys)))
                batch.extend(keys)
        # ONE LANDING, ONE JOURNAL ENTRY, and not for tidiness. `land` saves
        # the WHOLE companies file on every call, and this loop mutates one
        # in-memory list across categories - so calling it per category makes
        # each save's diff the RUNNING TOTAL. journal.BLAST refuses anything
        # over 25 records without force, and `land` forces on that category's
        # own count, which stops matching the sixth time round: five categories
        # landed and then sixteen were refused at 26, 27, 28... records. The
        # guard was right every time. Landing once makes the count the caller
        # forces on the count the journal sees, and it is what the rule asks
        # for anyway - "a bulk action recorded as ONE entry so undoing restores
        # all of it or none".
        total = land(store, seq, batch, a.by,
                     a.why or "landed after gate review") if batch else 0
        for cat, n in done:
            print(f"  {cat}: {n}")
        print(f"\n  {total} write-up(s) on the map, across {len(done)} "
              f"categor(y/ies)")
        if skipped:
            print(f"\n  REFUSED, never gated - {sum(n for _, n in skipped)} "
                  f"write-up(s) in {len(skipped)} categor(y/ies):")
            for cat, n in skipped[:12]:
                print(f"     {n:4}  {cat}")
            if len(skipped) > 12:
                print(f"     ... and {len(skipped) - 12} more")
            print(f"  Read one, then re-run:  python3 "
                  f"scripts/promote_profiles.py --gate {skipped[0][0]!r}")
        if unread:
            # WRITTEN SINCE SOMEBODY LOOKED. The category was gated; these
            # arrived afterwards, so the mark on it certifies a review that
            # never saw them.
            print(f"\n  HELD BACK, written after their category's gate review "
                  f"- {sum(n for _, n in unread)} write-up(s) in "
                  f"{len(unread)} categor(y/ies):")
            for cat, n in unread[:12]:
                print(f"     {n:4}  {cat}  (gated {str(gates.get(cat))[:16]})")
            if len(unread) > 12:
                print(f"     ... and {len(unread) - 12} more")
        print(f"  Undo a batch: python3 scripts/admin_undo.py")
        return 0

    if a.gate:
        gate(store, seq, a.gate)
        if a.self_read:
            import llm
            print(f"\n== 4. A SECOND READER, blind, on every pending "
                  f"write-up in {a.gate!r} ==")
            got_self = self_read(store, companies, a.gate,
                                 a.model or llm.DEFAULT_MODEL, a.limit,
                                 a.dry_run)
            bad, unread = got_self if isinstance(got_self, tuple) else (got_self, [])
            if not a.dry_run:
                calls, usd = llm.spent()
                print(f"\n   {calls} request(s), ${usd:.2f}")
                if unread:
                    print(f"   {len(unread)} write-up(s) were NOT re-read at "
                          f"all - unchecked, not agreed.")
                if not bad and not unread:
                    print("   Both readers agreed on every one. That is "
                          "evidence the door and the prompt are working here; "
                          "it is not proof.")
                elif not bad:
                    print("   The ones that WERE re-read all agreed.")
                for k, first, second, why in bad:
                    nm = next((c.get("name") for c in companies
                               if c.get("id") == first.get("id")), first.get("id"))
                    print(f"\n   {str(nm)[:40]:42} {why}")
                    print(f"     first  ({first.get('confidence')}): "
                          f"{(first.get('why') or '')[:80]}")
                    print(f"     second ({second.get('confidence')}): "
                          f"{(second.get('why') or '')[:80]}")
                if bad:
                    n_ok = len(bad) + len(unread)
                    print(f"\n   THOSE {len(bad)} ARE THE LIST. The rest that "
                          f"were RE-READ agreed; {len(unread)} were never "
                          f"answered and are unchecked. Nothing has been "
                          f"changed - land or reject in the usual way.")
        return 0

    if a.reject:
        for cid in a.reject:
            p = store.get(f"profile:{cid}")
            if not p:
                print(f"  no proposal for {cid!r}")
                continue
            p["status"] = "rejected"
            p["ruled_by"], p["ruled_on"] = a.by, dt.date.today().isoformat()
            p["ruled_why"] = a.why or "rejected"
        bad = agents.save(store, "profile-reject", why=a.why, by=a.by)
        print(bad or f"  rejected {len(a.reject)}; the company stays proposable")
        return 1 if bad else 0

    if a.hide or a.unhide:
        index = {c["id"]: c for c in seq if c.get("id")}
        for cid in a.hide:
            if cid in index:
                index[cid]["profile_hidden"] = True
        for cid in a.unhide:
            if cid in index:
                index[cid].pop("profile_hidden", None)
        bad = admin.save_companies(companies, "profile-hide",
                                   why=a.why or "kill switch", by=a.by)
        print(bad or f"  hidden {len(a.hide)}, unhidden {len(a.unhide)}; the page "
                     f"shows the one-line record by the next build")
        return 1 if bad else 0

    if a.gate_buyer:
        gate_buyer(store, seq, a.gate_buyer)
        return 0

    if a.land_buyer:
        read = json.loads(BUYER_READ.read_text()) if BUYER_READ.exists() else []
        if a.land_buyer not in read:
            print(f"  REFUSED. --gate-buyer {a.land_buyer!r} has not printed in "
                  f"this checkout.\n  A buyer answer decides whether a company "
                  f"belongs on a board about government;\n  landing a "
                  f"category's worth on the door alone is not the deal.")
            return 1
        keys = [k for k, p in _by_category(store, seq, "buyer").get(a.land_buyer, [])
                if p.get("status") == "pending"]
        rep = land_buyer(store, seq, keys, a.by,
                         a.why or f"landed {a.land_buyer} buyer answers after "
                                  f"the scope gate review", a.with_sled)
        print(f"  {rep['wrote']} buyer answer(s) recorded"
              + (f", {rep['sled']} sled_only flag(s) set" if rep["sled"]
                 else ", no sled_only flags set"))
        if not a.with_sled:
            eligible = sum(1 for k in keys
                           if (store.get(k) or {}).get("sled_eligible"))
            if eligible:
                print(f"  {eligible} row(s) were eligible for sled_only and did "
                      f"NOT get it. Add --with-sled to set them.")
        print(f"  Undo a batch: python3 scripts/admin_undo.py")
        return 0

    if a.land:
        gates = _read_gates()
        if a.land not in gates:
            print(f"  REFUSED. --gate {a.land!r} has not printed in this checkout.\n"
                  f"  The gate review is the part a person reads; landing a "
                  f"category nobody\n  has looked at is the door alone, and the "
                  f"owner asked for the door plus a look.")
            return 1
        # WHICH PENDING ONES. --land takes every pending write-up in the
        # category, which is right after a gate review that read the whole
        # exception list. It is NOT right when the instruction was "accept the
        # high-confidence ones": those are the 553 the door passed cleanly and
        # a person samples, while medium/low/unsure are the 283 the gate exists
        # to put in front of somebody. Landing both under one word would
        # publish the unreviewed half silently.
        rows = [(k, p) for k, p in _by_category(store, seq).get(a.land, [])
                if p.get("status") == "pending"
                and (not a.high_only
                     or (p.get("confidence") or "unsure") == "high")]
        when = gates.get(a.land)
        fresh = [(k, p) for k, p in rows if _after_gate(p, when)]
        keys = [k for k, p in rows if not _after_gate(p, when)]
        if fresh:
            print(f"  {len(fresh)} write-up(s) were created AFTER the gate "
                  f"review of {a.land!r} on {str(when)[:16]}, so nobody has "
                  f"read them. Held back:")
            for k, p in fresh[:8]:
                print(f"     {str(p.get('name') or p.get('id'))[:38]:40} "
                      f"written {str(p.get('at'))[:16]}")
            if len(fresh) > 8:
                print(f"     ... and {len(fresh) - 8} more")
            print(f"  Re-run --gate {a.land!r} to read them, then land again.")
        n = land(store, seq, keys, a.by, a.why or f"landed {a.land} after gate review")
        print(f"  {n} write-up(s) on the map. Undo a batch: python3 scripts/admin_undo.py")
        return 0

    # the funnel, re-derived every time
    rows = [p for p in store.values() if isinstance(p, dict) and p.get("kind") == "profile"]
    st = {}
    for p in rows:
        st[p.get("status", "?")] = st.get(p.get("status", "?"), 0) + 1
    on_map = sum(1 for c in seq if isinstance(c.get("profile"), dict)
                 and c["profile"].get("paragraphs"))
    hidden = sum(1 for c in seq if c.get("profile_hidden"))
    try:
        import fetch_profiles as fp
        idx = fp.index()
        fetched, readable = len(idx), sum(1 for e in idx.values() if not e.get("unread"))
    except Exception:
        fetched = readable = 0
    print(f"companies {len(seq)} · sites fetched {fetched} · readable {readable}")
    print(f"proposed {len(rows)}: " + ", ".join(f"{v} {k}" for k, v in sorted(st.items())))
    print(f"on the public page {on_map} · hidden by kill switch {hidden}")
    print(f"still saying 'not on file': {len(seq) - on_map}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
