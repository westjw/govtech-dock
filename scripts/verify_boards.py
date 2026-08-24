#!/usr/bin/env python3
"""Does this job board actually belong to the company we filed it against?

WHY THIS EXISTS

The owner opened a "Founding Account Executive" posting on this board and asked
whether it had anything to do with government. It did not: Concourse, an AI
company for corporate finance teams, backed by a16z. Seven of its postings were
on the public board and one was counted in the headline as a seller wanted.

Somebody found "Concourse" on the NIGP 2026 exhibitor list, searched for an
ATS, found jobs.ashbyhq.com/concourse and wired it. discover_ats.slug_matches
returned TRUE, because the slug does match the name - two entirely different
companies are called Concourse. A name-based check cannot tell them apart, and
that is the failure CLAUDE.md warns about by name: never point a company at
another company's board.

WHAT THIS CHECKS INSTEAD, AND WHY NOT THE OBVIOUS THING

The obvious test - "do the postings mention government?" - is a bad one. Plenty
of genuine govtech companies post "Senior Backend Engineer" with no public
sector vocabulary anywhere in it, so that test would flag hundreds of correct
boards and teach whoever reads the report to ignore it. A check that cries wolf
is worse than no check.

So this asks the boards to identify THEMSELVES. Most ATS APIs state the
employer's own name somewhere in the response - greenhouse puts company_name on
every job, ashby names the board, lever and workable carry the hosted URL - and
that name is written by the employer rather than inferred by us. Comparing it
to what we have on file is a direct identity claim rather than a guess.

Where an ATS states no name, this falls back to the domain the apply links
point at, which is weaker and is reported as weaker. Anything it cannot judge
comes back as "unknown", never as "fine": a check that cannot see is not a
check that passed.

  python scripts/verify_boards.py [--limit 40] [--type greenhouse] [--all]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ats  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Words a company name carries and a board never bothers to repeat. Stripped
# before comparison so "Tyler Technologies" still matches a board that calls
# itself "Tyler".
NOISE = {"inc", "inc.", "llc", "ltd", "limited", "corp", "corporation", "co",
         "company", "technologies", "technology", "tech", "software",
         "solutions", "systems", "group", "holdings", "labs", "the", "a"}


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def core(name: str) -> str:
    """The distinctive part of a name, with the corporate furniture removed."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w]
    keep = [w for w in words if w.lower() not in NOISE]
    return squash("".join(keep or words))


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


def board_says(kind: str, ref) -> dict:
    """Ask the board who it belongs to. Returns {name, hosts, n, how}."""
    out = {"name": None, "hosts": set(), "n": 0, "how": None}
    try:
        if kind == "greenhouse":
            d = ats._json(ats._get(
                f"https://boards-api.greenhouse.io/v1/boards/{ref}/jobs"))
            jobs = d.get("jobs") or []
            out["n"] = len(jobs)
            names = {j.get("company_name") for j in jobs if j.get("company_name")}
            if names:
                out["name"] = sorted(names)[0]
                out["how"] = "greenhouse company_name, stated by the employer"
            out["hosts"] = {host_of(j.get("absolute_url", "")) for j in jobs[:8]}
        elif kind == "ashby":
            d = ats._json(ats._get(
                f"https://api.ashbyhq.com/posting-api/job-board/{ref}"))
            jobs = d.get("jobs") or []
            out["n"] = len(jobs)
            # ashby states no employer name, so the apply host is all there is
            out["hosts"] = {host_of(j.get("applyUrl") or j.get("jobUrl") or "")
                            for j in jobs[:8]}
            out["how"] = "ashby apply hosts (this ATS states no employer name)"
        elif kind == "lever":
            d = ats._json(ats._get(
                f"https://api.lever.co/v0/postings/{ref}?mode=json"))
            jobs = d if isinstance(d, list) else []
            out["n"] = len(jobs)
            out["hosts"] = {host_of(j.get("applyUrl") or j.get("hostedUrl") or "")
                            for j in jobs[:8]}
            out["how"] = "lever apply hosts"
        elif kind == "workable":
            d = ats._json(ats._get(
                f"https://apply.workable.com/api/v1/widget/accounts/{ref}?details=true"))
            out["n"] = len(d.get("jobs") or [])
            nm = (d.get("name") or (d.get("account") or {}).get("name"))
            if nm:
                out["name"] = nm
                out["how"] = "workable account name, stated by the employer"
        else:
            out["how"] = f"no identity probe written for {kind}"
    except Exception as exc:
        out["how"] = f"could not read the board ({type(exc).__name__})"
    out["hosts"] = {h for h in out["hosts"] if h}
    return out


def judge(company: dict, said: dict) -> dict:
    """Compare what the board says about itself with what we have on file."""
    ours = core(company.get("name", ""))
    aliases = [core(a) for a in (company.get("also_known_as") or [])]
    site = host_of(company.get("website") or "").replace("www.", "")
    verdict, why = "unknown", said.get("how") or ""

    if said.get("name"):
        theirs = core(said["name"])
        if theirs and (theirs == ours or theirs in aliases
                       or theirs in ours or ours in theirs):
            verdict = "matches"
            why = f'board calls itself "{said["name"]}"'
        else:
            verdict = "MISMATCH"
            why = (f'board calls itself "{said["name"]}", '
                   f'we have it as "{company.get("name")}"')
    elif said.get("hosts"):
        # weaker: an apply host on the company's own domain is corroboration,
        # an ATS-owned host (ashby, lever) tells us nothing either way
        own = [h for h in said["hosts"] if site and site.split(".")[0] in h]
        generic = all(re.search(r"(ashbyhq|lever|greenhouse|workable|myworkday)",
                                h) for h in said["hosts"])
        if own:
            verdict, why = "matches", f"apply links point at {sorted(own)[0]}"
        elif generic:
            verdict = "unknown"
            why = ("every apply link is on the ATS's own domain, so the board "
                   "never says whose it is")
        else:
            verdict = "CHECK"
            why = f"apply links point at {sorted(said['hosts'])[:2]}"
    return {"verdict": verdict, "why": why, "postings": said.get("n", 0)}



# Government vocabulary, used ONLY as a reason to look - never as a verdict.
#
# Measured before trusting it: Concourse, the corporate-finance company that
# started all this, scores 1 hit in 37,540 characters of job descriptions.
# But Seneca - genuinely govtech, autonomous wildfire-suppression drones -
# scores ZERO in 23,540, because a drone engineer's job ad talks about drones.
#
# So a low score is not evidence a board is wrong. It is evidence nobody has
# checked. That distinction is the whole point: this produces a queue for a
# person, and the asymmetric error rule says a wrong removal is invisible and
# permanent while a wrong flag costs somebody two seconds.
GOV = re.compile(r"\b(government|public sector|municipal|municipalit\w*|county|"
                 r"citywide|state and local|SLED|agenc(y|ies)|constituent|"
                 r"resident|taxpayer|public safety|school district|K-12|"
                 r"procurement|permitting|civic|federal)\b", re.I)


def vocabulary(kind: str, ref) -> dict | None:
    """How often a board's own job descriptions talk about government.

    Only worth asking where the ATS hands descriptions over for free in the
    same call. Anything needing a request per posting is not worth 271 boards
    of somebody else's bandwidth for a signal this soft.
    """
    try:
        if kind == "ashby":
            d = ats._json(ats._get(
                f"https://api.ashbyhq.com/posting-api/job-board/{ref}"))
            texts = [j.get("descriptionPlain") or "" for j in d.get("jobs") or []]
        elif kind == "greenhouse":
            d = ats._json(ats._get(
                f"https://boards-api.greenhouse.io/v1/boards/{ref}/jobs?content=true"))
            texts = [ats.plain(j.get("content") or "") for j in d.get("jobs") or []]
        elif kind == "lever":
            d = ats._json(ats._get(
                f"https://api.lever.co/v0/postings/{ref}?mode=json"))
            texts = [j.get("descriptionPlain") or "" for j in (d if isinstance(d, list) else [])]
        else:
            return None
    except Exception:
        return None
    blob = " ".join(texts)
    if len(blob) < 2000:          # too little text to conclude anything from
        return None
    hits = len(GOV.findall(blob))
    return {"chars": len(blob), "hits": hits,
            "per1k": round(1000 * hits / len(blob), 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--type", help="only this ats type")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pause", type=float, default=0.4)
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    todo = [c for c in companies
            if (c.get("ats") or {}).get("type") in
            ("greenhouse", "ashby", "lever", "workable")]
    if a.type:
        todo = [c for c in todo if c["ats"]["type"] == a.type]
    if not a.all:
        todo = todo[:a.limit]

    print(f"checking {len(todo)} boards\n")
    rows, counts = [], {"matches": 0, "MISMATCH": 0, "CHECK": 0, "unknown": 0}
    for i, c in enumerate(todo, 1):
        said = board_says(c["ats"]["type"], c["ats"].get("ref"))
        r = judge(c, said)
        # where the board named nobody, ask what it TALKS about instead
        if r["verdict"] == "unknown":
            v = vocabulary(c["ats"]["type"], c["ats"].get("ref"))
            if v:
                r["vocab"] = v
                if v["per1k"] < 0.05:
                    r["verdict"] = "LOOK"
                    r["why"] = (f'{v["hits"]} government words in {v["chars"]:,} '
                                f'characters of its own job ads - not proof it is '
                                f'the wrong board, but nothing corroborates it either')
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        rows.append({"id": c["id"], "name": c["name"],
                     "type": c["ats"]["type"], "ref": c["ats"].get("ref"), **r})
        if r["verdict"] in ("MISMATCH", "CHECK", "LOOK"):
            print(f"  {r['verdict']:9} {c['name'][:30]:32} {r['why'][:82]}")
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)}", flush=True)
        time.sleep(a.pause)

    out = DATA / "board_identity.json"
    out.write_text(json.dumps(rows, indent=1) + "\n")
    print(f"\n{counts}")
    print(f"written to {out.relative_to(ROOT)}")
    print("\n'unknown' is not 'fine' - it means the board never said whose it "
          "is, which is most of ashby and lever.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
