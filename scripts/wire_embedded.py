#!/usr/bin/env python3
"""Wire up the boards discovery already found and never connected.

    python3 scripts/wire_embedded.py              # look only, change nothing
    python3 scripts/wire_embedded.py --write      # wire the clean ones
    python3 scripts/wire_embedded.py --write --propose   # ...and queue the rest

WHAT THIS IS. `data/embedded_ats.json` holds boards found INSIDE careers pages
- the page named its own widget - each verified at discovery time by a real
fetch that read real postings. 79 of the 82 are still filed `html` or
`unknown`. The finding happened; nobody connected it. This connects it.

WHY IT IS THE BEST AVAILABLE MOVE. It makes no new requests to anybody beyond
re-verifying what it is about to write, and every company it moves from `html`
to a structured API stops being an HTML fetch a bot wall can refuse and becomes
a JSON call no bot wall touches. CLAUDE.md puts it plainly: "Reading a page is
a snapshot somebody has to re-take by hand; finding the board behind it is
permanent and refresh.py keeps it current."

THE HEADLINE NUMBER IS A LIE AND THIS SCRIPT REFUSES TO REPEAT IT. The file
sums to 1,427 postings. Two deductions this project's own rules require bring
it to roughly 714:

  IDENTITY MISMATCH. Five entries name somebody else's board. The worst is
  Prepared -> greenhouse/axon, 500 postings: wiring that record publishes
  Axon's whole requisition list as Prepared's. That is the false "Yes"
  CLAUDE.md's never-point-a-company-at-its-parent's-board rule exists to stop,
  and no fetch can settle it, because the question is ownership rather than
  readability.

  ONE BOARD, SEVERAL CLAIMANTS. Four boards are claimed by more than one
  company - ashby/opengov by both Cartegraph and OpenGov; one Paylocity board
  by Catalis, Matterhorn, QScend AND nCourt. Wiring all of them would publish
  one company's 33 jobs four times and put a fake leader on the board. Every
  one of these is an acquisition already sitting in the Acquisitions queue,
  which is where the answer belongs.

Neither refusal is a rejection of the finding. Both go to a person, with the
evidence, through the same proposal path every other agent here uses. The
script's rule is the project's rule: an agent proposes, a person rules.

RE-VERIFIED BEFORE WRITING, ALWAYS. The `verified` string in the file can be
weeks old, and a board that has since gone is not a board. Nothing is written
on the strength of a stored count. `ats.HOST_PAUSE` paces the re-check for
free.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import agents                                          # noqa: E402
import ats                                             # noqa: E402


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resembles(name: str, ref: str) -> bool:
    """Is this slug plausibly THIS company's, on the name alone?

    Lifted from find_linkedin.py, for the same reason and with the same
    bluntness. A careers page names its parent's board as often as its own,
    and `identity: unknown` - which is 65 of these - means the board never
    said whose it was, not that it is ours.

    Without this the dry run happily offered to wire ICSolutions to
    icims/careers-tkcholdings (TKC Holdings, its parent, 310 postings), Veovo
    to bamboohr/gentrack (Gentrack acquired Veovo), Sparkrock to
    lever/Ionicpartners and Careers In Government to breezy/skagit-911. Each
    would have published another company's requisitions under this one's name,
    which is the false "Yes" CLAUDE.md calls the error this tool cannot afford.

    Deliberately crude and deliberately strict. It cannot tell a rename from a
    mistake - mdf commerce really did become Sovra - so everything it doubts
    is PRINTED AND QUEUED, never dropped. Telling those apart is judgement.
    """
    # ATS FURNITURE IS NOT IDENTITY. iCIMS names tenants `careers-<company>`,
    # and comparing that raw refused ViaPath its own board at careers-viapath,
    # JAGGAER at careers-jaggaer and EBSCO at careers-ebscoind. Stripping the
    # vendor's prefix compares the company half, and it does not weaken the
    # guard: careers-tkcholdings still fails against ICSolutions, because what
    # is left is a different company's name rather than a missing prefix.
    ref = re.sub(r"^(worldwide|us|global|internal|jobs|careers)?[-_]?careers?[-_]",
                 "", str(ref).strip(), flags=re.I)
    n, s = norm(name), norm(ref)
    if not n or not s:
        return False
    # A paylocity ref is a full recruiting URL rather than a slug, so the
    # company name is not in it to find. Those are judged on identity alone.
    if s.startswith("https") or s.startswith("http"):
        return True
    return n.startswith(s[:6]) or s.startswith(n[:6]) or s in n or n in s


def load(name: str):
    return json.loads((DATA / name).read_text())


def unwired(entry: dict, by_id: dict) -> bool:
    """Is this company still waiting for a board?

    A company that has since been given a real ATS by hand is left alone. This
    script connects findings; it does not overrule a person who already
    answered the same question.
    """
    c = by_id.get(entry.get("id"))
    if not c:
        return False
    kind = (c.get("ats") or {}).get("type")
    return kind in (None, "unknown", "html")


def triage(rows: list, by_id: dict) -> tuple[list, list, list]:
    """Split the pile into wire / refuse-mismatch / refuse-shared.

    The shared-board pass runs over the candidates ONLY. A board shared with a
    company that already has its own ATS wired is not a collision - that
    company is not going to be written here.
    """
    live = [e for e in rows if unwired(e, by_id)]

    mismatch = [e for e in live if e.get("identity") == "MISMATCH"]
    rest = [e for e in live if e.get("identity") != "MISMATCH"]

    claims = defaultdict(list)
    for e in rest:
        f = e.get("found") or {}
        claims[(f.get("type"), str(f.get("ref")))].append(e)

    shared = [e for k, v in claims.items() if len(v) > 1 for e in v]
    alone = [e for k, v in claims.items() if len(v) == 1 for e in v]

    # THE LAST GATE, and the one the first draft of this script was missing.
    # `identity: unknown` means the board never said whose it was. A slug that
    # does not resemble the company is then the only signal left, and it is the
    # one that separates a board from a parent's board.
    clean, unsure = [], []
    for e in alone:
        f = e.get("found") or {}
        if e.get("identity") == "matches" or resembles(e.get("name"), str(f.get("ref"))):
            clean.append(e)
        else:
            unsure.append(e)
    return clean, mismatch, shared + unsure


def recheck(entry: dict) -> dict:
    """Ask the board itself, now, whether it is still there.

    Returns what a person or a write needs: the row count and sample titles.
    A fetcher that raises is reported rather than swallowed - an unreadable
    board is a fact worth printing, and it is emphatically not a zero.
    """
    f = entry.get("found") or {}
    kind, ref = f.get("type"), f.get("ref")
    fetch = ats.FETCHERS.get(kind)
    if not fetch:
        return {"ok": False, "why": f"{kind!r} is not a type refresh.py fetches"}
    try:
        rows = fetch(ref)
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "why": f"{type(exc).__name__}: {str(exc)[:70]}"}
    titles = [str(r.get("title", "")).strip() for r in rows if r.get("title")]
    return {"ok": True, "rows": len(rows), "titles": titles[:8]}


def proposal(entry: dict, seen: dict, why: str) -> dict:
    """A refused finding, shaped for the queue a person rules from.

    Everything `check_board` demands is carried: the type, the slug, the row
    count a real fetch returned, sample titles, and the page the board was
    named on. Those titles are the evidence ownership is judged on, which is
    the whole question for both refusal kinds.
    """
    f = entry.get("found") or {}
    saw = str(entry.get("saw") or "")
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "ats_type": f.get("type"),
        "ats_ref": f.get("ref"),
        "rows": seen.get("rows"),
        "sample": seen.get("titles"),
        "evidence": saw if saw.startswith("http") else f"https://{saw}",
        "why": why,
        "saw": {"identity": entry.get("identity"),
                "identity_why": entry.get("identity_why"),
                "stored_verified": entry.get("verified")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="wire the clean findings into companies.json")
    ap.add_argument("--propose", action="store_true",
                    help="queue the refused findings for a person to rule")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    rows = load("embedded_ats.json")
    companies = load("companies.json")
    by_id = {c["id"]: c for c in companies}

    clean, mismatch, shared = triage(rows, by_id)
    if a.limit:
        clean = clean[:a.limit]

    print(f"{len(rows)} discovered board(s) on file")
    print(f"  {len(clean)} to wire   {len(mismatch)} name somebody else   "
          f"{len(shared)} claimed by more than one company\n")

    # --- the refusals, printed before anything is written ------------------
    if mismatch:
        print("REFUSED - the board names another company. Wiring one of these "
              "publishes somebody else's requisitions as this company's:")
        for e in mismatch:
            f = e.get("found") or {}
            print(f"  {str(e.get('name'))[:28]:30} -> {f.get('type')}/{f.get('ref')}"
                  f"  ({e.get('postings')} postings)")
        print()
    if shared:
        by_board = defaultdict(list)
        for e in shared:
            f = e.get("found") or {}
            by_board[(f.get("type"), str(f.get("ref")))].append(e.get("name"))
        print("REFUSED - the slug is not this company's name, or the board is "
              "claimed by several companies. Both are ownership questions:")
        for (kind, ref), names in by_board.items():
            print(f"  {kind}/{str(ref)[:40]:42} {', '.join(str(n) for n in names)}")
        print()

    # --- re-verify, then write --------------------------------------------
    print(f"re-checking {len(clean)} board(s) live before writing "
          f"(stored counts can be weeks old)\n")
    wired, gone, proposals = 0, [], []
    for e in clean:
        seen = recheck(e)
        f = e.get("found") or {}
        name = str(e.get("name"))[:26]
        if not seen["ok"] or not seen["rows"]:
            why = seen.get("why") or "the board answered with no postings"
            print(f"  skip  {name:28} {f.get('type')}/{str(f.get('ref'))[:22]:24} {why}")
            gone.append((e.get("name"), why))
            continue
        print(f"  ok    {name:28} {f.get('type')}/{str(f.get('ref'))[:22]:24} "
              f"{seen['rows']} posting(s)")
        if a.write:
            by_id[e["id"]]["ats"] = {"type": f.get("type"), "ref": f.get("ref")}
        wired += 1

    if a.propose:
        for e in mismatch:
            proposals.append(proposal(e, recheck(e),
                                      "the board names another company; who owns it "
                                      "is a judgement a fetch cannot make"))
        for e in shared:
            proposals.append(proposal(e, recheck(e),
                                      "this board is claimed by more than one company "
                                      "on the board - an acquisition to settle"))

    print(f"\n  {wired} wireable, {len(gone)} no longer readable")

    if a.write and wired:
        # Same discipline as every other writer here: validate the whole file,
        # then write atomically. A bad edit is refused, never half applied.
        path = DATA / "companies.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(companies, indent=1) + "\n")
        try:
            json.loads(tmp.read_text())
        except json.JSONDecodeError:
            tmp.unlink(missing_ok=True)
            print("refused: the result did not parse", file=sys.stderr)
            return 1
        tmp.replace(path)
        print(f"  wrote {wired} board(s) into companies.json")
    elif wired:
        print("  LOOKED ONLY. Nothing was written. Re-run with --write.")

    if proposals:
        rep = agents.ingest("board", proposals, model="wire_embedded.py")
        print(f"  queued {rep['kept']} for a person; {len(rep['refused'])} refused")
        for r in rep["refused"]:
            print(f"    {r['key']}: {r['why'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
