#!/usr/bin/env python3
"""What a person actually has to look at, and what the engine can check itself.

    python3 scripts/gate.py                       # where a person is needed
    python3 scripts/gate.py --kind fact           # the exception review, free
    python3 scripts/gate.py --kind fact --self    # + a blind second read

3,762 proposals are pending across five kinds and nobody is going to read them
one at a time. The alternative is not "the machine decides" - that is how a
wrong answer about somebody else's company reaches a public page and nothing
ever contradicts it. It is a GATE that works out WHICH ones a person needs.

`promote_profiles --gate` already does this for write-ups, on the owner's own
ruling ("door only, add some gate reviews"), and the shape is exception-based
rather than exhaustive:

  1. every proposal the door REFUSED, with the rule that refused it, so a rule
     that is too tight is visible - the write-up door has refused something
     true ten times and every one was found by looking at this list
  2. everything below high confidence, because that is the model saying so
  3. a random sample of what passed, so a rule that is too LOOSE is visible

This is that, for every kind, plus the part the owner asked for by name.

THE SELF REVIEW, AND WHY IT IS BLIND. `--self` takes a sample of what passed
at high confidence, rebuilds the question from what the first agent SAW, and
asks again with no knowledge of the first answer. Agreement is silent.
DISAGREEMENT IS THE FINDING, and it is the only thing that reaches a person.

The blinding is the whole mechanism, and CLAUDE.md says why: an attempt known
to be the main attempt gets defended, and a reviewer who can see whose work
they are holding grades the author. Strip the label and the only thing left to
judge is the answer. A second reader shown the first answer would agree with
it almost always, which would feel like verification and be worth nothing.

IT CANNOT RULE AND IT CANNOT WRITE. It prints. A person accepts or rejects in
the admin, exactly as before - the gate changes WHAT they read, never who
decides. Two of the first 39 flagged write-up refusals turned out to be
correct on a closer read, and a tool that acted on its own would have reversed
both.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                                   # noqa: E402
import llm                                                      # noqa: E402

SAMPLE = 0.05
SEED = 20260903          # a sample nobody can re-roll until it looks good

# WHAT COUNTS AS THE SAME ANSWER, per kind. Deliberately coarse: two readers
# writing a different `why` for the same verdict agree, and pretending
# otherwise would bury a person in prose disagreements that change nothing.
AGREE = {
    "family": lambda a, b: a.get("family") == b.get("family"),
    "bucket": lambda a, b: (a.get("sector"), a.get("category"))
                           == (b.get("sector"), b.get("category")),
    "card":   lambda a, b: a.get("verdict") == b.get("verdict"),
    "fact":   lambda a, b: str(a.get("value") or "").strip().lower()
                           == str(b.get("value") or "").strip().lower(),
    "board":  lambda a, b: ((a.get("ats_type"), (a.get("ats_ref") or "").lower())
                            == (b.get("ats_type"), (b.get("ats_ref") or "").lower())),
    "where":  lambda a, b: a.get("where") == b.get("where"),
    "profile": lambda a, b: a.get("confidence") == b.get("confidence"),
}

# WHAT THE ANSWER IS, in one line, for printing side by side.
SAYS = {
    "family": lambda p: p.get("family") or "-",
    "bucket": lambda p: f"{p.get('sector')} / {p.get('category')}",
    "card":   lambda p: p.get("verdict") or "-",
    "fact":   lambda p: f"{p.get('field')} = {p.get('value')}",
    "board":  lambda p: f"{p.get('ats_type')}:{p.get('ats_ref')}",
    "where":  lambda p: p.get("where") or "-",
    "profile": lambda p: f"{len(p.get('paragraphs') or [])} paragraph(s)",
}

SECOND_READ = """You are the SECOND reader of a question another reader has
already answered. You are not told what they said, and you must not try to
guess - answer the question yourself, from the evidence in front of you, as if
nobody had.

Your answer will be compared to theirs. Agreement means nobody has to look at
it. Disagreement means a person does. So an answer you are not sure of is
worth more as "unsure" than as a guess that happens to match.

Every rule the first reader worked under still applies to you: answer only
from the evidence given, never invent a fact to fill a field, and "unsure" is
a real answer."""


def pending(kind: str) -> list:
    return [dict(p, _key=k) for k, p in agents.load().items()
            if isinstance(p, dict) and p.get("kind") == kind
            and p.get("status") == "pending"]


def refused(kind: str) -> list:
    return [dict(p, _key=k) for k, p in agents.load().items()
            if isinstance(p, dict) and p.get("kind") == kind
            and p.get("status") == "refused"]


def split(rows: list) -> tuple[list, list]:
    """(needs a person by confidence, passed at high)."""
    low = [p for p in rows if p.get("confidence") != "high"]
    high = [p for p in rows if p.get("confidence") == "high"]
    return low, high


def sample(rows: list, frac: float = SAMPLE, n: int | None = None) -> list:
    """A stable random sample. Seeded, so nobody re-rolls it until it looks good."""
    if not rows:
        return []
    want = n if n is not None else max(1, round(len(rows) * frac))
    rnd = random.Random(SEED)
    return rnd.sample(rows, min(want, len(rows)))


def overview() -> int:
    """Where a person is needed, and where they are not."""
    store = agents.load()
    kinds = sorted({p.get("kind") for p in store.values()
                    if isinstance(p, dict) and p.get("kind")})
    print(f"{'kind':10} {'pending':>8} {'refused':>8} {'not high':>9} "
          f"{'sample':>7}   what a person reads")
    tot = 0
    for k in kinds:
        pend, ref = pending(k), refused(k)
        low, high = split(pend)
        smp = len(sample(high))
        need = len(ref) + len(low) + smp
        tot += need
        print(f"{k:10} {len(pend):8} {len(ref):8} {len(low):9} {smp:7}   {need}")
    print(f"\n{tot} item(s) need a person, out of "
          f"{sum(1 for p in store.values() if isinstance(p, dict))} on file.")
    print("Everything else passed a door at high confidence and is not "
          "hidden - it is in the admin queue like always. The gate decides "
          "what is worth READING, never what is true.")
    print("\n  python3 scripts/gate.py --kind <kind>          the review, free")
    print("  python3 scripts/gate.py --kind <kind> --self   + a blind re-read")
    return 0


def review(kind: str, limit: int | None) -> tuple[list, list, list]:
    ref = refused(kind)
    low, high = split(pending(kind))
    smp = sample(high, n=limit)
    say = SAYS.get(kind, lambda p: p.get("confidence") or "?")

    print(f"\n=== {kind}: {len(ref)} refused, {len(low)} below high, "
          f"{len(high)} passed at high ===")

    if ref:
        print(f"\n1. REFUSED BY THE DOOR ({len(ref)}). A rule that is too "
              f"tight shows up here, and nowhere else.")
        by_rule = collections.Counter(
            (p.get("refused_why") or "?").split(".")[0] for p in ref)
        for rule, n in by_rule.most_common():
            print(f"   {n:4} × rule {rule}")
        for p in ref[:12]:
            print(f"     {str(p.get('id'))[:24]:26} {(p.get('refused_why') or '')[:76]}")
        if len(ref) > 12:
            print(f"     ... {len(ref) - 12} more")

    if low:
        print(f"\n2. BELOW HIGH CONFIDENCE ({len(low)}). The model saying it "
              f"is not sure, which is the answer it is meant to give.")
        for p in low[:12]:
            print(f"   {str(p.get('confidence') or '?'):7} "
                  f"{str(p.get('id'))[:24]:26} {say(p)[:40]:42} "
                  f"{(p.get('why') or '')[:44]}")
        if len(low) > 12:
            print(f"   ... {len(low) - 12} more")

    if smp:
        print(f"\n3. A SAMPLE OF WHAT PASSED ({len(smp)} of {len(high)}). "
              f"A rule that is too LOOSE shows up here.")
        for p in smp:
            print(f"   {str(p.get('id'))[:24]:26} {say(p)[:40]:42} "
                  f"{(p.get('why') or '')[:44]}")
            ev = (p.get("evidence") or "")
            if ev:
                print(f"   {'':26} evidence: {ev[:76]}")
    return ref, low, smp


def second_read(kind: str, rows: list, model: str, dry: bool) -> list:
    """Ask the question again, blind. Returns [(proposal, their answer)]."""
    import judge
    import scout
    task = (judge.TASKS.get(kind) if kind in judge.TASKS
            else scout.RULES if kind in ("board", "where") else None)
    if task is None:
        print(f"\nno second reader for {kind!r} yet")
        return []
    items = []
    for p in rows:
        saw = p.get("saw") or {}
        if not saw:
            continue
        items.append(dict(saw, id=p.get("id"), name=p.get("name")))
    if not items:
        print("\nnothing carries what its agent saw, so nothing can be "
              "re-asked. A ruling nobody can re-read is not a ruling.")
        return []
    sysm = f"{SECOND_READ}\n\n{task}"
    user = json.dumps({"items": items}, indent=1)
    if dry:
        print(f"\n--- second-read system ---\n{sysm[:900]}\n...")
        print(f"\n{len(items)} item(s) would be re-asked. "
              f"dry run: nothing spent")
        return []
    try:
        got = llm.ask(sysm, user, f"gate-{kind}", model=model,
                      max_tokens=llm.MAX_OUTPUT)
    except llm.Refused as e:
        print(f"  {e}", file=sys.stderr)
        return []
    if got is None:
        print("  the second reader answered nothing usable")
        return []
    by_id = {p.get("id"): p for p in rows}
    out = []
    for ans in (got or {}).get("answers") or []:
        ident = ans.get("id") or ans.get("title") or ans.get("name")
        p = by_id.get(ident)
        if p:
            out.append((p, ans))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=tuple(agents.KINDS))
    ap.add_argument("--self", dest="self_read", action="store_true",
                    help="a blind second read of the sample; disagreement is "
                         "the only thing it reports")
    ap.add_argument("--limit", type=int,
                    help="how many to sample (default 5%% of what passed)")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.kind:
        return overview()
    ref, low, smp = review(a.kind, a.limit)
    if not a.self_read:
        print(f"\n(--self re-asks the sample blind and reports only where the "
              f"two readers disagree)")
        return 0
    if not smp:
        print("\nnothing passed at high confidence to re-read")
        return 0

    print(f"\n4. THE SECOND READER, blind, on those {len(smp)}.")
    pairs = second_read(a.kind, smp, a.model, a.dry_run)
    if a.dry_run or not pairs:
        return 0
    same = AGREE.get(a.kind, lambda x, y: True)
    say = SAYS.get(a.kind, lambda p: "?")
    disagree = []
    for p, ans in pairs:
        if ans.get("confidence") == "unsure":
            disagree.append((p, ans, "the second reader was unsure"))
        elif not same(p, ans):
            disagree.append((p, ans, "they answered differently"))
    calls, usd = llm.spent()
    print(f"\n   {len(pairs)} re-read, {len(pairs) - len(disagree)} agreed, "
          f"{len(disagree)} did not · {calls} request(s), ${usd:.2f}")
    for p, ans, note in disagree:
        print(f"\n   {str(p.get('id'))[:34]:36} {note}")
        print(f"     first  : {say(p)[:60]}")
        print(f"              {(p.get('why') or '')[:70]}")
        print(f"     second : {say(ans)[:60]}")
        print(f"              {(ans.get('why') or '')[:70]}")
    if disagree:
        print(f"\nTHOSE {len(disagree)} ARE THE LIST. Two readers who cannot "
              f"agree from the same evidence is exactly the case a person "
              f"should spend attention on, and it is a small fraction of "
              f"{len(pending(a.kind))} pending. Nothing has been changed.")
    else:
        print("\nBoth readers agreed on every one. That is evidence the door "
              "and the prompt are working on this kind - it is not proof, and "
              "the sample is 5%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
