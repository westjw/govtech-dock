#!/usr/bin/env python3
"""Apply rulings recorded by the web admin to the dataset.

The web admin only APPENDS opinions - a placement ruling says where a
company belongs, it does not move it. This script does the moving, here in
Python where validate() lives, so a web bug can mis-record an opinion but
can never corrupt the map. Runs in the daily workflow after checkout and
before the board build; idempotent, so a ruling applied yesterday is a
no-op today.

Vendor-scope rulings need no applying: the queues read the decision file
directly. Placement rulings carry applied:false until this runs.

  python scripts/apply_web_rulings.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import admin  # noqa: E402  (validate + write_atomic live there)


# A GOOD PHONE SESSION MUST NOT TAKE THE NIGHTLY RUN DOWN. save_companies goes
# through journal.check, which refuses more than BLAST (25) changes in one
# write unless told the count was seen; 26 wrong-bucket rulings on a Sunday
# therefore made this exit 1 on Monday, before the crawl and the board build,
# and again every night after, because the rulings stayed pending. Each web
# ruling is a person's individual decision, so the count IS seen - here - and
# the writes are chunked to BLAST so every journal entry stays undoable in one
# piece. A refusal is printed and the rulings stay pending; it never fails the
# run that carries the crawl.
CHUNK = 25


def _chunks(items: list) -> list:
    return [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)]


def _pending(name: str) -> dict:
    """Opinions from the web that the daily run has not applied yet."""
    path = DATA / name
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: r for k, r in rows.items()
            if isinstance(r, dict) and r.get("applied") is False}


def apply_founded(dry: bool) -> int:
    """A confirmed founding year, from the web.

    One field, and the year was already on the card, so the only thing that
    can go wrong is a company that has since been merged away. That fails
    loudly and stays pending rather than being dropped.
    """
    pending = _pending("web_founded_rulings.json")
    if not pending:
        return 0
    rows = json.loads((DATA / "web_founded_rulings.json").read_text())
    done, failed = [], []
    for chunk in _chunks(list(pending.items())):
        companies = admin.read_companies()
        by_id = {c["id"]: c for c in companies}
        landed = []
        for cid, r in chunk:
            c = by_id.get(cid)
            if c is None:
                failed.append((cid, "no such company - merged away since the ruling"))
                continue
            was = c.get("year_founded")
            c["year_founded"] = int(r["year"])   # an int, like every other year on the map
            err = admin.validate(companies)
            if err:
                c["year_founded"] = was
                failed.append((cid, err))
                continue
            landed.append((cid, was, r["year"]))
        if dry or not landed:
            done.extend(landed)
            continue
        print(f"  writing {len(landed)} founding year(s) (count seen; chunk of at most {CHUNK})")
        bad = admin.save_companies(companies, "apply-web-founded",
                                   f"{len(landed)} founding year(s) from the web admin",
                                   by="web", force=True)
        if bad:
            print(f"refused: {bad}; these stay pending")
            failed.extend((cid, bad) for cid, _, _ in landed)
            continue
        for cid, _, _ in landed:
            rows[cid]["applied"] = True
        done.extend(landed)
    print(f"founding years: {len(done)} applied, {len(failed)} left pending")
    for cid, was, now in done[:8]:
        print(f"  {cid}: {was or 'unknown'} -> {now}")
    for cid, why in failed[:5]:
        print(f"  PENDING {cid}: {why}")
    if not dry and done:
        admin.write_atomic("web_founded_rulings.json", rows)
    return 0


def apply_vendor_scope(dry: bool) -> int:
    """A vendor ruled IN or SLED-only goes back to the candidate queue.

    57 vendor-scope calls sat recorded and unacted on: 'out' worked by
    accident (hiding IS the outcome) while 'in' and 'sled' had no landing at
    all, and both doors said "will be added as a full company". A scope-review
    item carries no sector or category, and validate() refuses a company
    without one, so this cannot write companies.json. What it can do is hand
    the vendor back to the path that already lands candidates WITH a sector:
    govtech_candidates.json is what the card agent reads and
    promote_candidates.py lands, behind the owner's gate. `sled_only` rides
    along so a 'sled' call becomes the flag on the landed company.

    The decision is stamped `landed` with where it went, so this is
    idempotent and the phone can say where the vendor actually is.
    """
    import datetime as dt
    path = DATA / "vendor_scope_decisions.json"
    if not path.exists():
        return 0
    decisions = json.loads(path.read_text())
    todo = {k: d for k, d in decisions.items()
            if isinstance(d, dict) and d.get("call") in ("in", "sled") and not d.get("landed")}
    if not todo:
        return 0
    review_p = DATA / "scope_review_queue.json"
    review = json.loads(review_p.read_text()) if review_p.exists() else {"items": []}
    cand_p = DATA / "conference_intake" / "govtech_candidates.json"
    cands = json.loads(cand_p.read_text()) if cand_p.exists() else []
    norm = lambda n: "".join(ch for ch in (n or "").lower() if ch.isalnum())  # noqa: E731
    have = {norm(c.get("name")) for c in cands}
    by_name = {norm(i.get("name")): i for i in review.get("items", [])}
    moved, missing = [], []
    for key, d in todo.items():
        item = by_name.get(norm(d.get("name")))
        if item is None:
            missing.append((d.get("name"), "not in the scope review queue any more"))
            continue
        if norm(item["name"]) not in have:
            cands.append({"name": item["name"], "website": item.get("website"),
                          "vertical": item.get("why"), "description": item.get("description"),
                          "source_event": item.get("source_event"),
                          "sled_only": d["call"] == "sled",
                          "from_scope_ruling": {"call": d["call"], "on": d.get("on"),
                                                "by": d.get("by")}})
            have.add(norm(item["name"]))
        moved.append((key, item["name"], d["call"]))
        d["landed"] = {"to": "candidates", "on": dt.date.today().isoformat()}
        review["items"] = [i for i in review["items"] if norm(i.get("name")) != norm(item["name"])]
    print(f"vendor scope: {len(moved)} in/sled ruling(s) handed to the candidate queue, "
          f"{len(missing)} could not be")
    for _, name, call in moved[:8]:
        print(f"  {name}: {call} -> govtech_candidates.json")
    for name, why in missing[:5]:
        print(f"  PENDING {name}: {why}")
    if dry or not moved:
        return 0
    cand_p.write_text(json.dumps(cands, indent=1, ensure_ascii=False) + "\n")
    review_p.write_text(json.dumps(review, indent=1, ensure_ascii=False) + "\n")
    admin.write_atomic("vendor_scope_decisions.json", decisions)
    return 0


def apply_users(dry: bool) -> int:
    """A grant recorded on the web lands in users.json, through act_user_grant.

    The record carries the hash rule.js made and never an address; the desk
    door accepts a pre-hashed key for exactly this caller. The owner's own
    roles are refused there, and so is a hash already on file under another
    handle - both stay pending and say why.
    """
    pending = _pending("web_user_rulings.json")
    if not pending:
        return 0
    rows = json.loads((DATA / "web_user_rulings.json").read_text())
    done, failed = [], []
    for handle, r in pending.items():
        if dry:
            done.append((handle, r.get("roles")))
            continue
        out = admin.act_user_grant({"handle": handle, "email_sha256": r.get("email_sha256"),
                                    "roles": r.get("roles") or [], "label": r.get("label") or "",
                                    "by": f"web:{r.get('by') or 'unknown'}"})
        if out.get("error"):
            failed.append((handle, out["error"]))
            continue
        rows[handle]["applied"] = True
        done.append((handle, r.get("roles")))
    print(f"users: {len(done)} grant(s) applied, {len(failed)} left pending")
    for handle, roles in done[:8]:
        print(f"  {handle}: {', '.join(roles or [])}")
    for handle, why in failed[:5]:
        print(f"  PENDING {handle}: {why}")
    if not dry and done:
        admin.write_atomic("web_user_rulings.json", rows)
    return 0


def apply_merges(dry: bool) -> int:
    """A merge ruled from the web, applied here through act_merge.

    NOT reimplemented: act_merge is where "a merge never loses research"
    actually lives - the survivor inherits what it lacks, a discovered ATS
    beats an unknown one, the dropped name becomes an alias, and `also`
    placements union. A second copy of that logic in this file would drift
    from the first one, and the merge is the ruling this project can least
    afford to get quietly wrong.
    """
    pending = _pending("web_merge_rulings.json")
    if not pending:
        return 0
    rows = json.loads((DATA / "web_merge_rulings.json").read_text())
    done, failed = [], []
    if dry:
        # A DRY RUN THAT MERGES IS NOT A DRY RUN. act_merge writes
        # companies.json itself, so calling it here and skipping only the
        # bookkeeping at the end left --dry-run performing every merge for
        # real - caught when the second pass reported "company not found" for
        # a record the first pass had already folded away.
        by_id = {c["id"] for c in admin.read_companies()}
        for key, r in pending.items():
            missing = [x for x in (r["keep"], r["drop"]) if x not in by_id]
            if missing:
                failed.append((key, f"not on file: {', '.join(missing)}"))
            else:
                done.append((key, f"would merge {r['drop']} into {r['keep']}"))
        print(f"merges: {len(done)} would apply, {len(failed)} would stay pending")
        for _k, msg in done[:8]:
            print(f"  {msg}")
        for k, why in failed[:5]:
            print(f"  PENDING {k}: {why}")
        return 0
    for key, r in pending.items():
        # act_merge reads and writes companies.json itself, one merge at a
        # time, journalled. Slower than batching and correct: a refused merge
        # leaves the others alone instead of taking the batch down with it.
        out = admin.act_merge({"keep": r["keep"], "drop": r["drop"],
                               "why": r.get("why") or "",
                               # the handle rule.js recorded, never the owner by default
                               "by": f"web:{r.get('by') or 'unknown'}"})
        if out.get("error"):
            failed.append((key, out["error"]))
            continue
        done.append((key, out.get("message", "")))
        rows[key]["applied"] = True
    print(f"merges: {len(done)} applied, {len(failed)} left pending")
    for key, msg in done[:8]:
        print(f"  {msg}")
    for key, why in failed[:5]:
        print(f"  PENDING {key}: {why}")
    if not done:
        return 0
    admin.write_atomic("web_merge_rulings.json", rows)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # EVERY APPLIER RUNS. `apply_founded(...) or apply_merges(...)` skipped the
    # merges whenever founded returned non-zero.
    rc = 0
    for step in (apply_founded, apply_merges, apply_vendor_scope, apply_users):
        rc = step(a.dry_run) or rc

    path = DATA / "placement_rulings.json"
    if not path.exists():
        print("no placement rulings on file")
        return rc
    rulings = json.loads(path.read_text())
    pending = {cid: r for cid, r in rulings.items()
               if isinstance(r, dict) and r.get("applied") is False}
    if not pending:
        print("no placement rulings pending")
        return rc

    # through read_companies so save_companies has a real before-image to
    # diff against - reading the file directly leaves the journal comparing
    # against whatever the last caller happened to load. One read per chunk,
    # so each write diffs against what the previous chunk left.
    moved, failed = [], []
    for chunk in _chunks(list(pending.items())):
        companies = admin.read_companies()
        by_id = {c["id"]: c for c in companies}
        landed = []
        for cid, r in chunk:
            c = by_id.get(cid)
            if c is None:
                failed.append((cid, "no such company"))
                continue
            was = (c["sector"], c["category"])
            c["sector"], c["category"] = r["sector"], r["category"]
            err = admin.validate(companies)
            if err:
                # revert just this one and keep going; the ruling stays pending
                c["sector"], c["category"] = was
                failed.append((cid, err))
                continue
            landed.append((cid, was, (r["sector"], r["category"]), r))
        if a.dry_run or not landed:
            moved.extend(landed)
            continue
        # THROUGH save_companies, NOT write_atomic: these are the rulings a
        # person is most likely to want back - made on a small screen, away
        # from the evidence, applied hours later by a cron nobody watches - so
        # every one gets a before-image and an undo.
        print(f"  writing {len(landed)} placement(s) (count seen; chunk of at most {CHUNK})")
        bad = admin.save_companies(
            companies, "apply-web-rulings",
            f"{len(landed)} placement ruling(s) from the web admin", by="web", force=True)
        if bad:
            print(f"refused: {bad}; these stay pending")
            failed.extend((cid, bad) for cid, _, _, _ in landed)
            continue
        for _, _, _, r in landed:
            r["applied"] = True
        moved.extend(landed)

    print(f"{len(moved)} applied, {len(failed)} left pending")
    for cid, was, now, _ in moved[:10]:
        print(f"  {cid}: {was[0]}/{was[1]} -> {now[0]}/{now[1]}")
    for cid, why in failed[:5]:
        print(f"  PENDING {cid}: {why}")
    if not a.dry_run and moved:
        admin.write_atomic("placement_rulings.json", rulings)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
