"""Redraw the Conferences tab from the catalogue, without re-crawling. --write.

    python3 scripts/refresh_conference_rows.py [--write]

WHY THIS IS NOT build_board.py. That script re-fetches every job board it has
a ref for and takes 13-20 minutes against a few hundred other people's
servers. The conference rows depend on NONE of that: the catalogue file, the
companies' own source tags, and open-role counts already sitting in
board.json. CLAUDE.md records four full rebuilds run in one day for
metadata-only edits, which is a lot of traffic aimed at strangers to redraw a
tab.

WHAT IT DOES NOT TOUCH, and this is the whole safety argument. It replaces
exactly one key - `conferences` - and leaves postings, organizations, totals
and `generated` byte-for-byte alone. The postings keep the provenance of the
crawl that produced them, so this cannot make the board claim a freshness it
does not have. That is the failure the missing --reuse-postings flag was
always going to risk: a board reporting a fresh `generated` date over
week-old postings is the same lie as reporting "no jobs here" when nobody
looked.

It calls build_board.conference_rows() rather than reimplementing it, because
a second writer of the same rows is the bug this repo keeps finding - most
recently in the calendar feed, where two writers disagreed about the date of
every event for 118 events running.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import build_board                                              # noqa: E402


def main() -> int:
    write = "--write" in sys.argv
    bpath = DATA / "board.json"
    board = json.loads(bpath.read_text())
    cat = json.loads((DATA / "conferences.json").read_text()).get("conferences", [])
    companies = admin.read_companies()

    before = board.get("conferences") or []
    after = build_board.conference_rows(cat, board.get("organizations") or [],
                                        companies)

    was = {r["tag"]: r for r in before}
    now = {r["tag"]: r for r in after}
    changed = []
    for tag, r in now.items():
        o = was.get(tag)
        if not o:
            changed.append((tag, "NEW ROW", ""))
            continue
        for k in ("dates", "city", "companies", "hiring", "open_roles",
                  "swept", "approx_count"):
            if o.get(k) != r.get(k):
                changed.append((tag, k, f"{o.get(k)!r} -> {r.get(k)!r}"))
    gone = [t for t in was if t not in now]

    print(f"conference rows: {len(before)} -> {len(after)}")
    print(f"  {len(changed)} field change(s), {len(gone)} row(s) gone")
    for tag, k, d in changed[:30]:
        print(f"    {tag[:42]:42} {k:12} {d}")
    if len(changed) > 30:
        print(f"    ... and {len(changed)-30} more")
    for t in gone:
        print(f"    GONE: {t}")

    # POSTINGS ARE NOT TOUCHED, and the run says so out loud rather than
    # leaving it to be inferred from a diff nobody reads.
    print(f"\n  postings: {len(board.get('postings') or [])} left exactly as "
          f"crawled; `generated` stays {board.get('generated')!r}")

    if not write:
        print("\n  LOOKED ONLY. --write to land it.")
        return 0

    board["conferences"] = after
    tmp = bpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(board, ensure_ascii=False, separators=(",", ":")))
    reread = json.loads(tmp.read_text())          # it parses or nothing moves
    if len(reread.get("postings") or []) != len(board.get("postings") or []):
        print("  REFUSED: postings count changed", file=sys.stderr)
        tmp.unlink()
        return 1
    tmp.replace(bpath)
    print("  written to board.json (conferences only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
