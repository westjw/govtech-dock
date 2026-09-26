#!/usr/bin/env python3
"""Resolve a rebase conflict in a decision file by keeping BOTH sides' keys.

    python3 scripts/merge_decisions.py --resolve      # inside a stopped rebase

WHY A RULING FROM A PHONE COULD VANISH WITH NO ERROR ANYWHERE. rule.js
commits a ruling to main the moment it is made. The nightly refresh runs for
minutes, and its push step said `git pull --rebase -X theirs`: on a conflict
the bot's version of the file wins wholesale. The bot's run touches the same
decision files (apply_web_rulings flips `applied` flags in placement_rulings
and web_founded_rulings), so a ruling committed while the bot was running
conflicted with the bot's copy of that file and was dropped - the phone had
said "1 ruling saved" and the commit existed, and the next build shipped a
rulings.json without it.

These files are dicts keyed by id. Two writers appending different keys is
not a conflict in any sense but git's, so this merges them the way the data
means: start from the side that includes the other person's writes (during a
rebase that is "ours", the upstream), overlay the keys the bot's commit
changed (its `applied` flags), and stage the result. Anything that is not a
JSON dict is left for a person - this refuses rather than guesses.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The files two writers may both touch. companies.json is NOT here: it is
# written by the bot only, and a conflict in it is a real one.
DECISION_FILES = {
    "data/placement_rulings.json", "data/vendor_scope_decisions.json",
    "data/web_merge_rulings.json", "data/web_founded_rulings.json",
    "data/web_user_rulings.json", "data/admin_dismissed.json",
}


def _show(stage: int, path: str) -> dict | None:
    r = subprocess.run(["git", "show", f":{stage}:{path}"], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def union(base: dict | None, upstream: dict, replayed: dict) -> dict:
    """upstream's view, plus every key replayed CHANGED against the base.

    A key replayed did not touch keeps upstream's value (that is the other
    writer's ruling, kept). A key replayed changed - an `applied` flag, a
    `landed` stamp - takes replayed's value. A key only replayed has is added.
    Nested one level, because admin_dismissed is {queue: {key: rec}}.
    """
    out = dict(upstream)
    base = base or {}
    for k, v in replayed.items():
        if k not in out:
            out[k] = v
        elif isinstance(v, dict) and isinstance(out.get(k), dict) \
                and isinstance(base.get(k), dict) and not _is_record(v):
            out[k] = union(base.get(k), out[k], v)
        elif v != base.get(k):
            out[k] = v
    return out


def _is_record(d: dict) -> bool:
    """A ruling record, as opposed to a queue of them."""
    return any(k in d for k in ("on", "call", "applied", "year", "keep", "roles"))


def resolve() -> int:
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                       capture_output=True, text=True, cwd=ROOT)
    conflicted = [p for p in r.stdout.split() if p]
    if not conflicted:
        print("nothing is conflicted")
        return 0
    rc = 0
    for path in conflicted:
        if path not in DECISION_FILES:
            print(f"  {path}: not a decision file, left for a person", file=sys.stderr)
            rc = 1
            continue
        base, ours, theirs = _show(1, path), _show(2, path), _show(3, path)
        if ours is None or theirs is None:
            print(f"  {path}: a side is not a JSON dict, left for a person", file=sys.stderr)
            rc = 1
            continue
        merged = union(base, ours, theirs)
        (ROOT / path).write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n")
        subprocess.run(["git", "add", path], cwd=ROOT, check=True)
        print(f"  {path}: merged {len(ours)} + {len(theirs)} keys -> {len(merged)}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true")
    a = ap.parse_args()
    if not a.resolve:
        ap.error("--resolve, inside a stopped rebase")
    return resolve()


if __name__ == "__main__":
    sys.exit(main())
