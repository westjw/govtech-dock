#!/usr/bin/env python3
"""The reader between a staged exhibitor floor and intake.

    python3 scripts/read_floor.py brief                   # what needs reading, as a brief
    python3 scripts/read_floor.py brief --out brief.json
    python3 scripts/read_floor.py apply verdicts.json     # look only
    python3 scripts/read_floor.py apply verdicts.json --write

WHY A READER. sweep_exhibitors reads the names off a page; classify_exhibitors
sets `is_govtech` from the name alone and leaves "nothing in the name either
way" as null; conference_intake filed every null as a supplier. So 4,024 names
sat unjudged across 54 floors, and the floors graded `menu` - real sponsors
buried in an association's own navigation (Deloitte and Conduent on NCSEA's
partner page, Granicus and CivicPlus on 3CMA's) - were refused whole. The
owner's ruling, 2026-10-09: Claude reads the floors, a skeptic reads behind,
and a person reads a 5% sample. This script is the deterministic half of that:
it assembles the brief and applies the verdicts. The reading itself is agent
work, and the agent never edits the dataset (CLAUDE.md, "briefs out, proposals
in").

WHAT A VERDICT IS. One of four words per name:
  vendor         sells a technology product to government - worth a research
                 card (is_govtech true; conference_intake makes it a candidate)
  supplier       exhibits to government but is not a product vendor - services,
                 hardware, consulting, an agency, a university (is_govtech false)
  not_a_company  the association's own navigation, a programme item, a person,
                 a bare domain: it leaves the staged file
  unsure         stays null; intake holds it back rather than filing it
A floor whose every remaining name carries a verdict is graded `read`, which
intake accepts where it refused `menu`. A floor with NOTHING real on it - ACCG's
"good"-graded page was a job board, APPA Public Power's and Animal Care Expo's
were navigation - becomes `found: false` with the reason, the same honest
answer the sweeper gives a page it could not read, so the admin's floor queue
shows a wrong address rather than a swept floor. A supplier row that was filed
off one of these names (no website, same name) leaves with it, through the
journal. The verdicts are kept beside the floor in
data/conference_intake/reads/ so the reading can be read back.

The brief leaves out what is already settled: a name on the board, a name
already queued as a candidate, a supplier a research pass already stamped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
READS = DATA / "conference_intake" / "reads"
sys.path.insert(0, str(ROOT / "scripts"))

import conference_intake as ci                                   # noqa: E402

VERDICTS = ("vendor", "supplier", "not_a_company", "unsure")


def staged_files() -> list[pathlib.Path]:
    return sorted(pathlib.Path(p) for p in glob.glob(str(DATA / "exhibitors_*.json")))


def settled() -> tuple[set, set, set]:
    """Names that need no reader: on the board, already a candidate, or a
    supplier a research pass has already judged."""
    companies = json.loads((DATA / "companies.json").read_text())
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    cands = json.loads((DATA / "conference_intake" / "govtech_candidates.json").read_text())
    board = {ci.norm(c["name"]) for c in companies}
    board |= {ci.norm(a) for c in companies for a in (c.get("also_known_as") or [])}
    researched = {ci.norm(s["name"]) for s in suppliers
                  if s.get("researched") or s.get("govtech") is not None}
    queued = {ci.norm(c["name"]) for c in cands if isinstance(c, dict)}
    return board, queued, researched


def brief(files: list[pathlib.Path] | None = None) -> list[dict]:
    board, queued, researched = settled()
    out = []
    for f in files or staged_files():
        d = json.loads(f.read_text())
        if not d.get("found"):
            continue
        for e in d.get("exhibitors") or []:
            k = ci.norm(e.get("name"))
            if not k or k in board or k in queued or k in researched:
                continue
            if e.get("is_govtech") is True or (e.get("read") or {}).get("verdict"):
                continue
            out.append({"file": f.name, "event": d.get("event_tag"),
                        "page": d.get("source_url"), "grade": d.get("quality"),
                        "name": e["name"], "website": e.get("website"),
                        "classifier": e.get("is_govtech"),
                        "classifier_why": e.get("is_govtech_why")})
    return out


def purge_suppliers(names: list[str], by: str, why: str) -> tuple[int, str | None]:
    """Remove supplier rows filed off names a reader judged not a company.

    Only a row with NO website: a supplier with a site is a record somebody
    checked, whatever its name looks like. Through admin.save_decisions, so
    the journal keeps the before-image and the blast rule sees the count.
    """
    import admin
    rows = json.loads((DATA / "suppliers.json").read_text())
    gone = {ci.norm(n) for n in names}
    keep = [r for r in rows if not (ci.norm(r["name"]) in gone and not r.get("website"))]
    n = len(rows) - len(keep)
    if not n:
        return 0, None
    bad = admin.save_decisions("suppliers.json", keep, "purge-navigation",
                               why=f"{n} confirmed: {why}"[:300], by=by, force=n > 25)
    return n, bad


def apply(verdicts: list[dict], by: str, write: bool) -> dict:
    """Stamp verdicts into the staged files. Returns a report."""
    by_file: dict[str, dict[str, dict]] = {}
    for v in verdicts:
        if v.get("verdict") not in VERDICTS:
            raise SystemExit(f"{v.get('name')!r}: verdict must be one of {VERDICTS}, "
                             f"got {v.get('verdict')!r}")
        if v["verdict"] != "unsure" and len((v.get("why") or "").strip()) < 4:
            raise SystemExit(f"{v.get('name')!r}: say why, in a word or more a person can check")
        by_file.setdefault(v["file"], {})[ci.norm(v["name"])] = v
    on = dt.date.today().isoformat()
    report = {"files": 0, "vendor": 0, "supplier": 0, "dropped": 0, "unsure": 0,
              "now_read": [], "dropped_names": [], "not_a_floor": [],
              "suppliers_purged": 0, "purge_refused": None}
    # A NAME THE BOARD ALREADY HOLDS IS JUDGED. The brief leaves it out, so no
    # verdict arrives for it; counting it unread kept 3CMA graded `menu` with
    # Granicus, CivicPlus and Catalis on it, and intake never tagged them.
    board, queued, researched = settled()
    def judged(e: dict) -> bool:
        k = ci.norm(e.get("name"))
        return (bool((e.get("read") or {}).get("verdict")) or e.get("is_govtech") is not None
                or k in board or k in queued or k in researched)
    for fname, verd in sorted(by_file.items()):
        path = DATA / fname
        d = json.loads(path.read_text())
        kept, dropped, unread = [], [], 0
        for e in d.get("exhibitors") or []:
            v = verd.get(ci.norm(e.get("name")))
            if not v:
                if not judged(e):
                    unread += 1
                kept.append(e)
                continue
            if v["verdict"] == "not_a_company":
                dropped.append(e["name"]); report["dropped"] += 1
                continue
            if v["verdict"] == "unsure":
                # a reading, recorded as one: the name stays null and intake
                # holds it, but the floor is not unread because of it
                e["read"] = {"verdict": "unsure", "why": (v.get("why") or "").strip(), "by": by, "on": on}
                # the name rule's earlier False would file it as a supplier;
                # a reader's "unsure" outranks that and holds the name
                e["is_govtech"] = None
                report["unsure"] += 1
                kept.append(e)
                continue
            e["is_govtech"] = v["verdict"] == "vendor"
            e["is_govtech_why"] = f"reader: {v['why'].strip()}"
            e["read"] = {"verdict": v["verdict"], "why": v["why"].strip(), "by": by, "on": on}
            report[v["verdict"]] += 1
            kept.append(e)
        d["exhibitors"] = kept
        if dropped:
            d["read_dropped"] = int(d.get("read_dropped") or 0) + len(dropped)
            report["dropped_names"] += [(fname, n) for n in dropped]
        if dropped and not kept:
            # NOTHING REAL WAS ON IT. Not a swept floor with zero exhibitors -
            # a page that was never an exhibitor list, said the way the
            # sweeper says it, so the queue shows the address is wrong.
            d["found"] = False
            d["why"] = (f"reader: every one of the {len(dropped)} names was the "
                        f"site's own text, not an exhibitor; this page is not a floor")
            d.pop("quality", None)
            report["not_a_floor"].append(fname)
        # EVERY NAME LEFT HAS BEEN JUDGED, so the floor is one a reader
        # separated from the chrome around it, whatever the grade rule said
        elif kept and unread == 0 and all(judged(e) for e in kept):
            if d.get("quality") != "read":
                d["quality_was"] = d.get("quality")
                d["quality"] = "read"
                report["now_read"].append(fname)
        report["files"] += 1
        if write:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
            json.loads(tmp.read_text())
            tmp.replace(path)
            READS.mkdir(parents=True, exist_ok=True)
            rec = READS / fname.replace("exhibitors_", "")
            prior = json.loads(rec.read_text()) if rec.exists() else []
            # one record per name, the latest reading winning, so a re-run
            # does not double the trail
            latest = {ci.norm(r.get("name")): r for r in prior}
            latest.update({k: {**v, "by": by, "on": on} for k, v in verd.items()})
            rec.write_text(json.dumps(list(latest.values()), indent=1, ensure_ascii=False) + "\n")
    if write and report["dropped_names"]:
        n, bad = purge_suppliers([nm for _, nm in report["dropped_names"]], by,
                                 "filed off a floor as an exhibitor; a reader judged "
                                 "the name the site's own text, not a company")
        report["suppliers_purged"], report["purge_refused"] = n, bad
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--out")
    b.add_argument("--files", nargs="*")
    a = sub.add_parser("apply")
    a.add_argument("verdicts")
    a.add_argument("--by", default="agent:reader")
    a.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if args.cmd == "brief":
        files = [pathlib.Path(f) for f in args.files] if args.files else None
        rows = brief(files)
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
        print(f"{len(rows)} name(s) to read across "
              f"{len({r['file'] for r in rows})} floor(s)"
              + (f" -> {args.out}" if args.out else ""))
        return 0
    verdicts = json.loads(pathlib.Path(args.verdicts).read_text())
    rep = apply(verdicts, args.by, args.write)
    print(f"{rep['files']} floor(s): {rep['vendor']} vendor, {rep['supplier']} supplier, "
          f"{rep['dropped']} not a company (dropped), {rep['unsure']} unsure (held)")
    if rep["now_read"]:
        print(f"  graded `read`: {', '.join(rep['now_read'])}")
    if rep["not_a_floor"]:
        print(f"  not a floor at all (now found: false): {', '.join(rep['not_a_floor'])}")
    if rep["suppliers_purged"] or rep["purge_refused"]:
        print(f"  supplier rows filed off dropped names: {rep['suppliers_purged']} removed"
              + (f"; REFUSED: {rep['purge_refused']}" if rep["purge_refused"] else ""))
    if not args.write:
        print("  looked only. --write to stamp the floors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
