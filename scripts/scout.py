#!/usr/bin/env python3
"""Where does this company actually post? Read the page, then PROVE the answer.

    python3 scripts/scout.py --pile unread  --limit 40 --dry-run
    python3 scripts/scout.py --pile unread  --limit 40
    python3 scripts/scout.py --pile unknown --limit 40

THE BIGGEST HOLE ON THE BOARD, and it is one job done 1,647 times. 746 of the
820 companies wired as an `html` careers page read ZERO titles, and 901 more
have no board on file at all. Rain Bird, AMCS and BigBear.ai were each found
the same way: the owner opened the page, saw the jobs, and pasted the real
address. Rain Bird had ten roles behind a search page. AMCS had eleven.
BigBear.ai had eighty-six.

THE DETERMINISTIC PASS HAS ALREADY RUN ON EVERY ONE OF THESE. That is what
put them in these piles - `discover_ats.probe` read the site, matched its
patterns against the markup and found nothing it could name. So this is not a
longer regex; it is the case the regex is structurally bad at: an iframe whose
src carries a slug, a careers link two hops away, a widget named in prose, a
board that says "powered by" in a footer.

THE MODEL DOES NOT GET TO CLOSE THE QUESTION, and the shape of this file is
that sentence. check_board demands the row count a slug ACTUALLY returned and
sample titles from it, because CLAUDE.md is explicit that a slug is verified
with a real fetch before it is written - slugs that look right land on other
companies' boards. No reading of a careers page can produce those. So:

    the model proposes an ADDRESS -> this file fetches it -> only what
    answered becomes a proposal.

An address that will not fetch is reported and dropped. An address that
fetches somebody else's board is reported and dropped, because
`discover_ats.slug_matches` is the same ownership test the admin runs before a
person is allowed to wire one by hand.

AND "NO BOARD EXISTS" IS NOT ON THE MENU. It is the one answer that would be
worth having and the one this must never accept, because a false absence is
invisible and permanent: the company stops being asked about, nothing errors,
no count looks odd, and nothing ever contradicts it. The brief says so, the
door has no field for it, and a run that finds nothing reports "the model saw
no board on N pages" - which is a fact about the reading, not about the
company. That is the same distinction `scan_pagetext` holds between
`None found` and `Unknown`, and it is the rule this project turns on.
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
import ats                                                      # noqa: E402
import discover_ats                                             # noqa: E402
import fetch_profiles as fp                                     # noqa: E402
import llm                                                      # noqa: E402

BATCH = 8              # careers pages per request; the page text sets this
MAX_ROWS = 2000        # over this it is a parent's hub, and check_board says so

# THE TWO SENTENCES A NULL RESULT IS ALLOWED TO SAY, as values rather than as
# prose inside a print. What this run reports when it finds nothing IS the
# invariant - a false absence is the one error this engine exists to refuse -
# so it has to be something a guard can read. As f-string fragments inside
# main() they were split across lines and no substring check could see them.
SAW_NOTHING = ("the model saw no board it could name on {n} page(s). That is "
               "what one reading of one page showed, and it is not evidence "
               "that those companies have no board.")
UNREAD_NOTE = ("{n} page(s) would not load at all - also not evidence of "
               "anything about the company.")

RULES = """You are reading one company's careers page and answering one
question: where do they actually advertise jobs?

Everything you write is a PROPOSAL. An address you give will be FETCHED and
checked before anything is written, and an address that does not answer, or
that answers with somebody else's postings, is thrown away. So a guess costs
nothing and buys nothing - say unsure instead.

- Answer only from the page text in the brief.
- NEVER answer that no job board exists. One page cannot show that, and a
  wrong "none" hides a hiring company for ever.
- "unsure" is a real answer, expected often, and always cheap.

Return ONE JSON object:
{"answers": [{"id": <the exact id from the brief>,
  "answer": "board" | "posts_at" | "unsure",
  "ats_type": <only for board: greenhouse, lever, ashby, workable, recruitee,
     breezy, smartrecruiters, bamboohr, workday, rippling, jazzhr, icims,
     jibe, paylocity, oracle>,
  "ats_ref": <only for board: the slug or the full board url as it appears>,
  "where": <only for posts_at: linkedin, indeed, glassdoor, ziprecruiter,
     builtin, wellfound, govportal, recruiter, email, parent, own, other>,
  "url": <only for posts_at: the link>,
  "board_owner": <only for posts_at where=parent: whose board it is>,
  "confidence": "high"|"medium"|"low"|"unsure",
  "why": <one sentence a person can check against the page>,
  "evidence": <the words on the page that decided it>}]}"""


def careers_url(c: dict, pile: str) -> str:
    """The page to read, or "" when there is nothing to read yet."""
    if pile == "unread":
        return ((c.get("ats") or {}).get("ref") or "").strip()
    return (c.get("website") or "").strip()


def pile_rows(pile: str, limit: int | None) -> list:
    """The companies this pile is about, hiring-shaped ones first.

    A COMPANY WITH A CONFERENCE TAG OR A DESCRIPTION IS WORTH THE REQUEST
    FIRST, because those are the ones a reader is most likely to look up. The
    order is a preference, not a filter: everything in the pile is eventually
    asked about.
    """
    companies = admin.read_companies()
    board = json.loads((DATA / "board.json").read_text())
    posts = {}
    for p in board.get("postings", []):
        posts[p["company_id"]] = posts.get(p["company_id"], 0) + 1
    done = agents.load()
    out = []
    for c in companies:
        cid = c.get("id")
        kind = (c.get("ats") or {}).get("type")
        if not cid or c.get("posts_at"):
            continue
        if f"board:{cid}" in done or f"where:{cid}" in done:
            continue          # already proposed; a re-run does not re-ask
        if pile == "unread":
            if kind != "html" or posts.get(cid):
                continue
        elif pile == "unknown":
            if kind not in (None, "unknown"):
                continue
        if not careers_url(c, pile):
            continue
        out.append(c)
    out.sort(key=lambda c: (not c.get("researched"), (c.get("name") or "").lower()))
    return out[:limit] if limit else out


def verify(company: dict, a: dict) -> tuple[dict | None, str]:
    """Fetch the address the model named. Returns (proposal, note).

    THIS IS THE HALF THAT MAKES THE OTHER HALF SAFE. Everything check_board
    insists on - a type refresh.py can fetch, a slug that answered, the count
    it returned, the titles it returned - is produced here by actually asking
    the board, never by the model asserting it.
    """
    kind = (a.get("ats_type") or "").strip().lower()
    ref = (a.get("ats_ref") or "").strip()
    if kind not in ats.FETCHERS:
        return None, f"{kind!r} is not a type refresh.py can fetch"
    # `html` IS A FETCHER AND IS NOT AN ANSWER HERE. Every company in the
    # `unread` pile is ALREADY wired as html and reads zero titles - that is
    # what put it in the pile - so proposing html is proposing the status quo,
    # and check_board refuses it by name downstream. Refusing it here costs a
    # request and a queue row less. Found by a guard, after the first version
    # of that guard masked it with a stub that raised.
    if kind == "html":
        return None, ("html is another page we cannot enumerate, not a board "
                      "found - and it is what this company is already wired as")
    if not ref:
        return None, "no slug or url given"
    try:
        rows = ats.fetch({"type": kind, "ref": ref})
    except Exception as exc:                       # ats raises its own type
        return None, f"{kind}:{ref} did not answer ({str(exc)[:60]})"
    real = [r for r in rows if (r.get("title") or "").strip()]
    if not real:
        # NOT "they have nothing open". A slug that fetches and returns
        # nothing is a slug we cannot tell apart from a wrong one, and wiring
        # it would record a zero every night for ever.
        return None, f"{kind}:{ref} answered with no titles"
    if len(real) > MAX_ROWS:
        return None, f"{kind}:{ref} returned {len(real)} rows - a parent's hub"
    # THE OWNERSHIP TEST THE ADMIN ALREADY RUNS. CLAUDE.md: never point a
    # company at its parent's board. A mismatch is not refused here, it is
    # CARRIED, because a rename looks exactly like a mistake and only a person
    # can tell them apart - and check_board's whole design is to hand a person
    # the evidence rather than guess.
    owns = discover_ats.slug_matches(ref, company)
    sample = [r["title"] for r in real[:8]]
    return {
        "kind": "board", "key": f"board:{company['id']}",
        "id": company["id"], "name": company.get("name"),
        "ats_type": kind, "ats_ref": ref,
        "rows": len(real), "sample": sample,
        "confidence": a.get("confidence") or "medium",
        "why": (a.get("why") or "").strip(),
        "evidence": a.get("evidence_url") or a.get("read_on") or "",
        "slug_matches": bool(owns),
        # WHAT THE AGENT SAW, stored beside its answer. CLAUDE.md: store the
        # input the person saw alongside their answer, or the label is
        # useless for teaching anything later - and a ruling nobody can
        # re-read is not a ruling. Without this a board proposal could be
        # gated but never SECOND-READ, because the question it answered was
        # gone. The page text is referenced by sha, not carried: the bodies
        # are gitignored and this store is committed.
        "saw": {"read_on": a.get("read_on"), "pile": a.get("pile"),
                "page_sha": a.get("page_sha"),
                "evidence": (a.get("evidence") or "").strip()},
    }, ("" if owns else
        f"SLUG MISMATCH: {ref!r} does not read as {company.get('name')!r} - "
        f"a person rules whether that is a rename or a parent's board")


def as_where(company: dict, a: dict) -> dict:
    return {
        "kind": "where", "key": f"where:{company['id']}",
        "id": company["id"], "name": company.get("name"),
        "where": a.get("where"), "url": (a.get("url") or "").strip(),
        "board_owner": (a.get("board_owner") or "").strip(),
        "confidence": a.get("confidence") or "medium",
        "why": (a.get("why") or "").strip(),
        "evidence": a.get("evidence_url") or a.get("read_on") or "",
        "saw": {"read_on": a.get("read_on"), "pile": a.get("pile"),
                "page_sha": a.get("page_sha"),
                "evidence": (a.get("evidence") or "").strip()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pile", choices=tuple(agents.BOARD_PILES), required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = pile_rows(a.pile, a.limit)
    if not rows:
        print(f"nothing unanswered in the {a.pile} pile")
        return 0
    print(f"{len(rows)} compan(y/ies) in the {a.pile} pile: "
          f"{agents.BOARD_PILES[a.pile]}")

    # THE FETCH IS FIRST AND IT IS DETERMINISTIC. A page that will not load is
    # a fact about the fetch, never about the company, and it costs no request.
    briefs, unread = [], []
    for c in rows:
        page = fp.grab(careers_url(c, a.pile))
        if page.get("unread"):
            unread.append((c.get("name"), page["unread"]))
            continue
        briefs.append((c, agents.board_brief(c, page, a.pile)))
    print(f"  {len(briefs)} page(s) read, {len(unread)} would not load")

    if a.dry_run:
        if briefs:
            print(f"\n--- system ---\n{RULES}")
            print(f"\n--- one brief ---\n"
                  f"{json.dumps(briefs[0][1], indent=1)[:1400]}\n...")
        print("\ndry run: nothing asked, nothing fetched for verification, "
              "nothing spent")
        return 0

    lots = [briefs[i:i + a.batch] for i in range(0, len(briefs), a.batch)]
    boards, wheres, notes, saw_nothing = [], [], [], 0
    for i, lot in enumerate(lots, 1):
        by_id = {c["id"]: (c, b) for c, b in lot}
        user = json.dumps({"items": [b for _, b in lot]}, indent=1)
        try:
            got = llm.ask(RULES, user, "board", model=a.model,
                          max_tokens=llm.MAX_OUTPUT)
        except llm.Refused as e:
            print(f"  stopping at request {i}: {e}", file=sys.stderr)
            break
        if got is None:
            print(f"  request {i}/{len(lots)}: nothing usable came back")
            continue
        for ans in (got or {}).get("answers") or []:
            pair = by_id.get(ans.get("id"))
            if not pair:
                continue                      # an answer for a brief nobody sent
            c, b = pair
            ans["read_on"] = b.get("read_on")
            ans["pile"] = a.pile
            ans["page_sha"] = b.get("page_sha")
            kind = ans.get("answer")
            if kind == "board":
                prop, note = verify(c, ans)
                if note:
                    notes.append(f"{c['name']}: {note}")
                if prop:
                    boards.append(prop)
            elif kind == "posts_at":
                wheres.append(as_where(c, ans))
            else:
                saw_nothing += 1
        print(f"  request {i}/{len(lots)}: {len(boards)} verified board(s), "
              f"{len(wheres)} posts-at so far")

    for kind, rows_ in (("board", boards), ("where", wheres)):
        if not rows_:
            continue
        rep = agents.ingest(kind, rows_, model=f"{a.model}:scout-{a.pile}")
        print(f"\n{kind}: {rep['kept']} through the door, "
              f"{len(rep['refused'])} refused")
        for r in rep["refused"][:6]:
            print(f"    REFUSED {r['key']}: {r['why'][:100]}")

    calls, usd = llm.spent()
    print(f"\n{calls} request(s), ${usd:.2f} estimated")
    if notes:
        print(f"\n{len(notes)} address(es) did not survive verification:")
        for n in notes[:15]:
            print(f"  {n}")
    # SAID AS A FACT ABOUT THE READING, never about the companies.
    print("\n" + SAW_NOTHING.format(n=saw_nothing))
    if unread:
        print(UNREAD_NOTE.format(n=len(unread)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
