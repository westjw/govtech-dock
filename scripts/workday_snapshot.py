#!/usr/bin/env python3
"""Read every Workday board on file from THIS machine and write the snapshot
the GitHub crawl reads instead of asking Workday itself.

    python3 scripts/workday_snapshot.py            # read, write data/workday_snapshot.json
    python3 scripts/workday_snapshot.py --push     # the nightly job: update, read, commit, push

WHY THIS EXISTS (owner, 2026-11-17: "Read Workday from my Mac"). From
2026-11-16 Workday answered 429 to GitHub's runners from the first request of
a run - 39 of 42 tenants - while this same code read all 42 from the owner's
Mac the same hour. Three nightly crawls in a row lost ~2,400 postings (24% of
the board) and the sanity gate rightly refused to publish any of them. GitHub's
addresses are shared with every scraper on Azure; the Mac's are not. ats.py
(WORKDAY_SNAPSHOT) is the reading half; this is the writing half.

Same crawler, same rules: robots.txt at every hop, our own name on every
request, Workday paced as one service (ats._gate_key). Nothing here reads a
page any differently from the nightly crawl; it reads from a different place.

WHAT A BAD NIGHT DOES. A board that refuses for a moment (429, timeout, a
connection that dropped) keeps the rows it had at its last good read, with
that read's date, so ats.WORKDAY_SNAPSHOT_MAX_AGE still ages it out and a
board that stays down becomes the crawl's live failure, named as such. Workday
being down (a 5xx, a maintenance page) counts as a refusal for now. A board
that answers with a real error (404, a changed site) records the error: the
crawl reports exactly what the Mac saw. And if most boards were not read
tonight, in any way - the Mac is offline, Workday refuses or is down for
everyone - nothing is written at all, so the file's age stays honest.

--push runs only in a checkout whose ONLY change is the snapshot (a dedicated
worktree, ~/govtech-dock-workday), moves it to origin/main, reads, commits
data/workday_snapshot.json alone and pushes, rebasing on a race. The file is
written by nothing else, so a rebase cannot conflict.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ats  # noqa: E402

SNAP = ROOT / "data" / "workday_snapshot.json"
REL = "data/workday_snapshot.json"
WORKERS = 4                 # Workday is one gate (ats._gate_key); threads only overlap latency
MOSTLY_FAILED = 0.5         # more than this share NOT read tonight: write nothing
FETCH_TRIES, FETCH_WAIT = 10, 30   # a Mac that just woke may not have its network yet


def workday_refs(companies: list[dict]) -> dict[str, list]:
    """Every distinct Workday ref on file, by snapshot key."""
    out = {}
    for c in companies:
        a = c.get("ats") or {}
        if a.get("type") == "workday" and a.get("ref"):
            out[ats.workday_key(a["ref"])] = a["ref"]
    return out


_SERVER_DOWN = re.compile(r"\bhttp 5\d\d\b|non-json response")


def transient(exc: Exception) -> bool:
    """A refusal for now, not an answer: worth keeping yesterday's rows over.

    A 5xx, or a 200 that is an HTML maintenance page ("non-JSON response"), is
    Workday being down, not the board having changed - the 2026-11-17 review
    found a Workday-wide outage would otherwise replace 42 boards' good rows
    with 42 errors and publish them. A 404 or a robots refusal is an answer."""
    s = str(exc).lower()
    return (isinstance(exc, ats.RateLimited) or "network error" in s
            or "timed out" in s or "timeout" in s or bool(_SERVER_DOWN.search(s)))


def read_one(ref, fetch=None) -> tuple[list[dict] | None, Exception | None]:
    fetch = fetch or ats.fetch
    for attempt in (1, 2):
        try:
            return fetch({"type": "workday", "ref": ref}), None
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, none is fatal
            if attempt == 1 and transient(exc):
                continue        # the shared gate already holds Workday after a 429
            return None, exc
    return None, None           # unreachable


def build(refs: dict[str, list], old: dict, now: str, fetch=None) -> tuple[dict, dict]:
    """(new snapshot, counts). Pure apart from `fetch`, so selftest can drive it."""
    boards, counts = {}, {"read": 0, "kept": 0, "error": 0, "postings": 0}
    old_boards = (old or {}).get("boards") or {}
    with cf.ThreadPoolExecutor(WORKERS) as pool:
        futs = {k: pool.submit(read_one, ref, fetch) for k, ref in refs.items()}
        for k in sorted(futs):
            rows, exc = futs[k].result()
            if exc is None:
                boards[k] = {"read_at": now, "rows": rows}
                counts["read"] += 1
                counts["postings"] += len(rows)
            elif transient(exc) and (old_boards.get(k) or {}).get("rows") is not None \
                    and not old_boards[k].get("error"):
                boards[k] = old_boards[k]           # its own old date: it still ages out
                counts["kept"] += 1
            else:
                boards[k] = {"read_at": now, "error": " ".join(str(exc).split())[:300],
                             "transient": transient(exc)}
                counts["error"] += 1
    snap = {"_comment": "Written by scripts/workday_snapshot.py on the owner's machine; "
                        "read by ats.fetch when GOVTECH_DOCK_WORKDAY_SNAPSHOT=1 (refresh.yml). "
                        "Never edit by hand.",
            "generated": now, "boards": boards}
    return snap, counts


def mostly_failed(snap: dict, n: int) -> bool:
    """Most boards were NOT read tonight - kept from an older read, or failed
    in any way. Counting only transient errors (as first written) missed the
    normal case: from the second night on every board has rows, so a night
    when Workday refused the Mac everything was all "kept", wrote a fresh
    `generated`, pushed, and kept the watchdog quiet while the rows aged out
    (2026-11-17 review, three reviewers)."""
    fresh = sum(1 for b in snap["boards"].values()
                if b.get("read_at") == snap["generated"] and not b.get("error"))
    return n > 0 and (n - fresh) / n > MOSTLY_FAILED


def write(snap: dict) -> None:
    tmp = SNAP.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=1, ensure_ascii=True, sort_keys=True) + "\n")
    tmp.replace(SNAP)


def git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], text=True,
                          capture_output=True, check=check)


def cleanup_plan(porcelain: str) -> tuple[list[str], str | None]:
    """(other changed paths, what to do with the snapshot: None, "restore" or "delete").

    An UNTRACKED snapshot (a run that died before `git add`, or a plain run in
    this worktree before main carried the file) cannot be `git checkout --`'d;
    trying wedged every later run (2026-11-17 review)."""
    others, snap = [], None
    for ln in porcelain.splitlines():
        if not ln.strip():
            continue
        status, path = ln[:2], ln[3:]
        if path == REL:
            snap = "delete" if status == "??" else "restore"
        else:
            others.append(path)
    return others, snap


def prepare_checkout(sleep=time.sleep) -> None:
    """Refuse unless the only change here is the snapshot, then stand on origin/main."""
    others, snap = cleanup_plan(git("status", "--porcelain").stdout)
    if others:
        raise SystemExit(f"refusing --push: this checkout has other changes ({others[:5]}); "
                         f"run it in its own worktree (~/govtech-dock-workday)")
    if snap == "restore":
        git("checkout", "--", REL)
    elif snap == "delete":
        SNAP.unlink(missing_ok=True)
    for i in range(FETCH_TRIES):
        r = git("fetch", "-q", "origin", "main", check=False)
        if r.returncode == 0:
            break
        print(f"git fetch failed (try {i + 1} of {FETCH_TRIES}): {r.stderr.strip()[:120]}")
        if i + 1 == FETCH_TRIES:
            raise SystemExit("no network to reach GitHub; the next scheduled run will try again")
        sleep(FETCH_WAIT)
    git("checkout", "-q", "--detach", "origin/main")


def push(counts: dict) -> None:
    git("add", REL)
    if not git("diff", "--cached", "--quiet", check=False).returncode:
        print("nothing changed; nothing to push")
        return
    msg = (f"workday: {counts['read']} board(s) read on the owner's machine, "
           f"{counts['postings']:,} posting(s); {counts['kept']} kept from the last read, "
           f"{counts['error']} error(s)")
    git("commit", "-q", "-m", msg)
    for _ in range(3):
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            print(f"pushed: {msg}")
            return
        git("fetch", "-q", "origin", "main")
        git("rebase", "-q", "origin/main")
    raise SystemExit("could not push the snapshot after three tries")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="the nightly job: update, read, commit, push")
    ap.add_argument("--updated", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.push and not a.updated:
        # Move to origin/main, then start again: this process imported
        # yesterday's ats.py, and tonight's read should be main's.
        prepare_checkout()
        os.execv(sys.executable, [sys.executable, str(pathlib.Path(__file__).resolve()),
                                  "--push", "--updated"])
    ats.WORKDAY_SNAPSHOT = False            # this IS the live read
    companies = json.loads((ROOT / "data" / "companies.json").read_text())
    refs = workday_refs(companies)
    old = json.loads(SNAP.read_text()) if SNAP.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    snap, counts = build(refs, old, now)
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M} {len(refs)} Workday board(s): {counts}")
    for k, b in sorted(snap["boards"].items()):
        if b.get("error"):
            print(f"  {k}: {b['error'][:120]}")
    if mostly_failed(snap, len(refs)):
        # Nothing written, so `generated` and every board's read_at keep
        # their real age and the watchdog sees the Mac has stopped reading.
        print("most boards were not read tonight (offline? Workday refusing or down?) - "
              "the snapshot is left as it was")
        return 1
    write(snap)
    if a.push:
        push(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
