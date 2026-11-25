#!/usr/bin/env python3
"""The desk publisher: what the owner does in the desk admin reaches the
public site on its own, frugally.

THE OWNER'S RULES (2026-09-28). "Push every 15 min + quick rebuild", then,
because Cloudflare Pages is on the Free plan (500 builds a month, one at a
time), "Free: be frugal". So a cycle pushes only when something changed,
never within 30 minutes of the last push, and only once the desk has been
quiet for 5 minutes - a sitting on the belt publishes once, after it, not
once per click. launchd runs a cycle every 5 minutes; most cycles look,
find nothing to do, and exit in well under a second.

WHAT A CYCLE PUBLISHES is DERIVED, never typed: the data files named by
admin journal entries that are not yet on main (every admin write goes
through save_decisions/save_companies, which journal the file name), the
journal itself - the live file and every new or changed part of its archive,
data/journal_archive/, carried onto main's row by row - and the four files
the desk writes as a crawl does (news.json and site_pages_index.json from
"check their news now", identity_labels.jsonl, discovery_log.json from a
website saved or a board tried again). Anything else modified in the checkout - a research
session's exhibitors file, a board.json a local build drew - is left out and
named in the status. board.json and data/detail/ are never taken from the
desk: the quick rebuild redraws them on top of main.

HOW, IN ORDER, and why each step exists:

  1. The checkout must be quiet: no rebase or merge in progress, and no
     local commit that main does not have (the three-way base would be
     ambiguous; that is a person's call, and an inbox item says so).
  2. `git fetch`. Main moves under us all day - the nightly crawl, news four
     times a day, phone rulings.
  3. Gates: something to publish; 5 quiet minutes; 30 minutes since the last
     push; no data workflow queued or running on GitHub (they push the same
     files; their resolver now merges a conflict record by record, but not
     racing them is cheaper than resolving).
  4. Snapshot the desk's files UNDER THE ADMIN LOCK (data/.admin.lock, which
     the admin holds around every action), so a multi-file action is never
     caught half written.
  5. Merge each file three ways on PARSED JSON (merge_data.merge_text): base
     = the checkout's HEAD, ours = the desk, theirs = main. Any field both
     sides changed differently is a CONFLICT: nothing is pushed, an inbox
     item names it. A company both sides changed in different fields is
     journalled as merged, by the publisher (_journal_the_merge), so the
     journal's last after-image is the record main now holds.
  6. In a throwaway worktree at main - main's CODE runs, never the
     checkout's uncommitted code - write the merged files, run the quick
     rebuild, stage explicit paths (never -A, never -f), and run the checks:
     the rebuild's own --check, every staged JSON parses, the full selftest
     (which includes check_no_person_in_the_repo over the staged files).
  7. Commit as westjw (noreply) and push, NEVER forced. A rejected push
     means main moved; the next cycle starts over.
  8. Bring the checkout up to main - under the lock, only if the desk's files
     are byte-identical to the snapshot and nothing else dirty would be
     overwritten - by restoring the published paths and fast-forwarding.
  9. On a later cycle, read Cloudflare's check run for the pushed commit and
     record "live" or "the build failed" (the failure goes to the inbox).

A REPAIR. When main moved (a nightly crawl redraws board.json from the
companies.json it checked out, which can predate a publish), a cycle with
nothing of its own to publish asks the quick rebuild whether main's board
still shows every desk edit, and pushes a redraw if it does not. News alone
never triggers one: news reaches the site with the nightly, as before.

    python3 scripts/publish.py               one cycle (what launchd runs)
    python3 scripts/publish.py --dry-run     what a cycle would do; writes nothing
    python3 scripts/publish.py --status      the whole state file; its "last" is the last cycle
    python3 scripts/publish.py --install     write and load the launchd job
    python3 scripts/publish.py --uninstall   unload and remove it
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import journal  # noqa: E402
import merge_data  # noqa: E402

QUIET_S = 5 * 60                 # the desk has been idle this long
GAP_S = 30 * 60                  # at most one push per this long
REMOTE, BRANCH = "origin", "main"
REPO_SLUG = "westjw/govtech-dock"
AUTHOR = ("westjw", "67345260+westjw@users.noreply.github.com")
# The workflows in the govtech-dock-data concurrency group: each commits data
# files this publisher also commits.
DATA_WORKFLOWS = ("daily-refresh", "discovery", "news", "write-profiles", "links")
BUSY = ("queued", "in_progress", "waiting", "pending", "requested")
# Written by the desk the way a crawl writes, so not journalled - and merged
# by record (merge_data: the later read of a company stands). discovery_log
# .json is the desk's record of having looked for a board (saving a website,
# "try again"), and main's weekly discovery sweep writes the same file: left
# out of this list, a website saved at the desk kept the checkout off main
# for good - every cycle "skipped: would overwrite data/discovery_log.json"
# (2026-11-24 second review).
CRAWL_WRITES = ("data/news.json", "data/site_pages_index.json",
                "data/identity_labels.jsonl", "data/discovery_log.json")
# THE JOURNAL: the live file, and the archive of what it pruned - one file
# per month under data/journal_archive/ (journal.py, 2026-11-24), every new
# or changed part published. The old single file is named only to be folded:
# a desk still running the old code appends to it until it is restarted, and
# its rows reach main filed into the month files, never as that file.
LIVE_JOURNAL = "data/admin_journal.jsonl"
ARCHIVE_DIR = f"data/{journal.ARCHIVE_DIR}"
LEGACY_ARCHIVE = f"data/{journal.LEGACY_ARCHIVE}"
FOLDING = f"data/{journal.FOLDING}"
JOURNALS = (LIVE_JOURNAL, ARCHIVE_DIR + "/", LEGACY_ARCHIVE, FOLDING)
COMPANIES = "data/companies.json"
LABEL = "com.sledjobs.publish"


def is_archive(path: str) -> bool:
    return journal.is_archive_path(path)


def _read(p: str):
    """A checkout file as the cycle snapshots it: bytes for the archive (a
    sealed part is gzip), text for everything else; None when absent."""
    f = ROOT / p
    if not f.is_file():
        return None
    return f.read_bytes() if is_archive(p) else f.read_text()


def _write(f: pathlib.Path, v) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(v, bytes):
        f.write_bytes(v)
    else:
        f.write_text(v)


def _archive_order(p: str) -> tuple:
    """The old single file first (a desk on the old code wrote it before any
    month file) - a copy of it a fold moved aside before it - then the parts
    in reading order."""
    if p == FOLDING:
        return (0, "", 0, False)
    if p == LEGACY_ARCHIVE:
        return (0, "~", 0, False)
    k = journal.parse_part(p.rsplit("/", 1)[-1])
    return (1, *k) if k else (2, p, 0, False)


def state_path() -> pathlib.Path:
    return ROOT / "data" / "publish_state.json"


def lock_path() -> pathlib.Path:
    return ROOT / "data" / ".admin.lock"


def base_dir() -> pathlib.Path:
    """The desk files a push published while the checkout could not follow.

    THE BASE OF A THREE-WAY MERGE IS WHAT THE DESK LAST PUBLISHED. Normally
    that is the checkout's HEAD, because a push brings the checkout up to
    main. When a keystroke lands during a cycle the sync stands aside, HEAD
    stays behind, and HEAD is then the wrong base both ways: a field the desk
    changed again after the push reads as a conflict with its own published
    value, and a field main merged in (the crawl's hiring) reads as the desk
    changing it BACK. The published snapshot is the right base until the
    checkout catches up."""
    return ROOT / "data" / ".publish_base"


@contextlib.contextmanager
def admin_lock(path: pathlib.Path | None = None):
    """The lock the desk admin holds around every action (admin.py)."""
    p = path or lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def now_iso(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(t if t is not None else time.time()) \
        .astimezone().isoformat(timespec="seconds")


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    p = state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    os.replace(tmp, p)


def git(*args, cwd: pathlib.Path | None = None, check: bool = True,
        text: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True,
                       text=text)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def show(rev: str, path: str, cwd: pathlib.Path | None = None) -> str | None:
    r = git("show", f"{rev}:{path}", cwd=cwd, check=False)
    return r.stdout if r.returncode == 0 else None


def show_bytes(rev: str, path: str, cwd: pathlib.Path | None = None) -> bytes | None:
    """show(), undecoded: a sealed archive part is gzip."""
    r = git("show", f"{rev}:{path}", cwd=cwd, check=False, text=False)
    return r.stdout if r.returncode == 0 else None


def _at(rev: str, path: str) -> bool:
    return git("cat-file", "-e", f"{rev}:{path}", check=False).returncode == 0


def inbox(kind: str, text: str, st: dict, detail: dict | None = None) -> None:
    """Tell the owner's end-of-day inbox, once per distinct problem."""
    key = hashlib.sha1(f"{kind}|{text}".encode()).hexdigest()[:12]
    if st.get("last_inbox") == key:
        return
    st["last_inbox"] = key
    import page_belt
    row = {"id": f"publisher:{kind}:{now_iso()}", "at": now_iso(),
           "source": "publisher", "company_id": None, "name": None,
           "item": "page", "saw": detail, "text": text[:4000],
           "by": "publisher", "status": "open"}
    p = page_belt.inbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps(row) + "\n")


# ------------------------------------------------------------------ what

def _journal_lines(path: str, raw: bytes | None) -> set:
    try:
        return set(journal.lines_of(path, raw))
    except (OSError, EOFError, ValueError, zlib.error) as exc:   # a torn gzip names nothing
        print(f"note: {path} could not be read: {exc}", file=sys.stderr)
        return set()


def journal_files(head: str = "HEAD", dirty: dict | None = None) -> set:
    """Data files named by journal entries the desk wrote since `head`.

    An entry is new when its line is in none of the journal files at `head`:
    the live file, and every archive path that changed (a row moves from the
    live file into the archive, from an open part into a sealed one, and
    from the old single file into the month files, and a moved row is not a
    new one). An archive part the checkout did not touch holds the same rows
    on both sides, so it is not read."""
    dirty = dirty_paths() if dirty is None else dirty
    paths = [LIVE_JOURNAL] + sorted(p for p in dirty if is_archive(p))
    local, past = set(), set()
    for j in paths:
        f = ROOT / j
        local |= _journal_lines(j, f.read_bytes() if f.is_file() else None)
        past |= _journal_lines(j, show_bytes(head, j))
    names = set()
    for ln in local - past:
        try:
            f = json.loads(ln).get("file")
        except (ValueError, AttributeError):
            continue
        if isinstance(f, str) and f and "/" not in f and not f.startswith("."):
            names.add(f"data/{f}")
    return names


def dirty_paths() -> dict:
    """{path: status} for everything git sees changed in the checkout."""
    r = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    out = {}
    for rec in r.stdout.split("\0"):
        if len(rec) > 3:
            out[rec[3:]] = rec[:2]
    return out


def publishable() -> tuple[list, list]:
    """(what the desk changed and a cycle publishes, what is left out).
    Every changed archive path is taken - a new part, an appended one, a
    month sealed (its open parts gone), the old single file."""
    dirty = dirty_paths()
    named = journal_files(dirty=dirty) | {LIVE_JOURNAL} | set(CRAWL_WRITES)
    take = sorted(p for p in dirty if (p in named or is_archive(p))
                  and not merge_data.is_generated(p))
    left = sorted(p for p in dirty if p not in take and p.startswith("data/")
                  and not merge_data.is_generated(p) and p != "data/publish_state.json")
    return take, left


def newest_mtime(paths: list) -> float:
    return max(((ROOT / p).stat().st_mtime for p in paths if (ROOT / p).exists()),
               default=0.0)


def workflows_busy() -> list | None:
    """Data workflows queued or running on GitHub; None when gh cannot say."""
    try:
        r = subprocess.run(["gh", "run", "list", "-R", REPO_SLUG, "--limit", "30",
                            "--json", "workflowName,status"], capture_output=True,
                           text=True, timeout=30)
        if r.returncode != 0:
            return None
        runs = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return [x["workflowName"] for x in runs
            if x.get("workflowName") in DATA_WORKFLOWS and x.get("status") in BUSY]


def pages_check(sha: str) -> str | None:
    """'success', 'failure', ..., or None while Cloudflare has not finished."""
    try:
        r = subprocess.run(["gh", "api", f"repos/{REPO_SLUG}/commits/{sha}/check-runs",
                            "--jq", '.check_runs[] | select(.name=="Cloudflare Pages") '
                                    '| [.status, .conclusion] | @tsv'],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (r.stdout or "").strip().splitlines()[:1]
    if not line:
        return None
    status, _, conclusion = line[0].partition("\t")
    return conclusion if status == "completed" else None


# ------------------------------------------------------------------ build

def run_py(args: list, cwd: pathlib.Path, timeout: int = 900) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout, env=env)


def unparsed(wt: pathlib.Path, staged: list) -> str | None:
    """The first staged JSON file that does not parse, or None. A JSON-lines
    file is read line by line, and the line is named.

    THE JOURNAL'S ARCHIVE IS HELD TO ITS OWN RULES (journal.archive_problems)
    the moment any part of it is staged - a sealed part read through gzip, so
    a torn one is named. A torn copy of a row held whole is no failure there
    (the seal leaves it out, the full pass takes it out), and this parse
    refused every desk publish over one on main (2026-11-24 second review);
    any other line that is no JSON is, named by file and line."""
    archive = False
    for p in staged:
        f = wt / p
        if is_archive(p) or p.startswith(ARCHIVE_DIR + "/"):
            archive = True
            continue
        if not f.exists() or not p.endswith((".json", ".jsonl")):
            continue
        try:
            if p.endswith(".jsonl"):
                for i, ln in enumerate(f.read_text().splitlines(), 1):
                    if ln.strip():
                        try:
                            json.loads(ln)
                        except ValueError as exc:
                            return f"{p} line {i} does not parse: {exc}"
            else:
                json.loads(f.read_text())
        except (ValueError, OSError) as exc:
            return f"{p} does not parse: {exc}"
    if archive:
        bad, _look = journal.archive_problems(wt / "data")
        if bad:
            return ("the journal archive does not parse by its rules (python3 scripts/journal.py "
                    "--check): " + "; ".join(bad[:3]))[:1500]
    return None


def checks(wt: pathlib.Path, staged: list) -> str | None:
    """Everything that must hold before a push. None, or what failed."""
    r = run_py(["scripts/quick_rebuild.py", "--check"], wt, 300)
    if r.returncode != 0:
        return "the redrawn board does not match its inputs:\n" + r.stdout[-800:]
    bad = unparsed(wt, staged)
    if bad:
        return bad
    r = run_py(["scripts/selftest.py"], wt, 1200)
    if r.returncode != 0 or "all checks passed" not in r.stdout:
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL")][:8]
        return "the selftest failed:\n" + "\n".join(fails or [r.stdout[-800:]])
    return None


def desk_drift(wt: pathlib.Path) -> int:
    """How many organizations main's board shows differently from what its
    own inputs say - news set aside, which reaches the site nightly."""
    code = r"""
import json, sys
sys.path.insert(0, "scripts")
import quick_rebuild as q
plan = q.redraw()
prev = {o["id"]: o for o in plan["prev"].get("organizations", [])}
skip = {"news_state", "news_checked_on"}
n = 0
for o in plan["payload"]["organizations"]:
    p = prev.get(o["id"])
    if p is None or {k: v for k, v in o.items() if k not in skip} != \
            {k: v for k, v in p.items() if k not in skip}:
        n += 1
n += sum(1 for p in prev if p not in {o["id"] for o in plan["payload"]["organizations"]})
det = q.bb.DATA / "detail"
for i, body in plan["bodies"].items():
    f = det / f"{i}.json"
    old = json.loads(f.read_text()).get("profile") if f.exists() else None
    if json.loads(body).get("profile") != old:
        n += 1
print(n)
"""
    r = run_py(["-c", code], wt, 300)
    try:
        return int(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


@contextlib.contextmanager
def worktree(rev: str):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sledjobs-publish-"))
    wt = tmp / "wt"
    git("worktree", "add", "--detach", str(wt), rev)
    try:
        yield wt
    finally:
        git("worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ cycle

def rebuild(wt: pathlib.Path) -> str | None:
    """The quick rebuild, in the worktree. None, or why it failed."""
    r = run_py(["scripts/quick_rebuild.py", "--write"], wt, 600)
    return None if r.returncode == 0 else (r.stdout + r.stderr)[-1500:]


def cycle(dry_run: bool = False, *, now: float | None = None,
          busy=workflows_busy, run_checks=checks, drift=desk_drift,
          redraw=rebuild, pages=pages_check) -> dict:
    """One publish cycle. Returns the outcome it recorded."""
    now = time.time() if now is None else now
    st = load_state()
    out = {"at": now_iso(now)}

    def done(outcome: str, **kw) -> dict:
        out.update(outcome=outcome, **kw)
        if not dry_run:
            st["last"] = out
            save_state(st)
        return out

    # Cloudflare's word on the last push, once it has one
    if st.get("pending_sha") and not dry_run:
        c = pages(st["pending_sha"])
        if c:
            st["live"] = {"sha": st["pending_sha"], "conclusion": c, "read": now_iso(now)}
            if c != "success":
                inbox("pages", f"Cloudflare's build of {st['pending_sha'][:7]} ended "
                               f"'{c}'. The site still shows the build before it.", st)
            st.pop("pending_sha", None)

    gitdir = pathlib.Path(git("rev-parse", "--git-dir").stdout.strip())
    gitdir = gitdir if gitdir.is_absolute() else ROOT / gitdir
    if any((gitdir / x).exists() for x in ("rebase-merge", "rebase-apply", "MERGE_HEAD")):
        return done("paused", why="a rebase or merge is in progress in the checkout")
    try:
        git("fetch", "-q", REMOTE, BRANCH)
    except RuntimeError as exc:
        return done("paused", why=f"could not reach GitHub: {exc}")
    theirs_rev = f"{REMOTE}/{BRANCH}"
    main_sha = git("rev-parse", theirs_rev).stdout.strip()
    ahead = git("rev-list", f"{theirs_rev}..HEAD").stdout.split()
    if ahead:
        inbox("ahead", f"The checkout has {len(ahead)} commit(s) main does not "
                       f"({ahead[0][:7]}...). Desk edits are not published until "
                       f"they are pushed or dropped - a person's call.", st)
        return done("held", why="the checkout has commits main does not have")

    take, left = publishable()
    out["left_out"] = left[:20]
    repair = False
    if not take:
        # NOTHING OF THE DESK'S TO PUBLISH, AND MAIN MOVED: the checkout
        # follows main (the nightly's hiring, news, new code), under the same
        # rules as after a push - so the desk never works on yesterday's file.
        if git("rev-list", f"HEAD..{theirs_rev}").stdout.split() and not dry_run:
            _sync({}, theirs_rev, st, out)
        elif not dry_run:
            out["sync"] = f"checkout at {main_sha[:7]}, which is main" + _scripts_note()
        if st.get("drift_checked") == main_sha:
            return done("idle", why="nothing to publish")
        if now - st.get("last_push_ts", 0) < GAP_S:
            return done("waiting", why="main moved; checking it waits for the "
                                       "30-minute gap")
        with worktree(theirs_rev) as wt:
            n = drift(wt)
        st["drift_checked"] = main_sha
        if not n:
            return done("idle", why="nothing to publish; main's board shows every edit")
        repair = True
        out["repair_orgs"] = n
    else:
        quiet = now - newest_mtime(take)
        if quiet < QUIET_S:
            return done("waiting", why=f"the desk was active {int(quiet)}s ago; "
                                       f"publishing after {QUIET_S // 60} quiet minutes",
                        pending=take)
        since = now - st.get("last_push_ts", 0)
        if since < GAP_S:
            return done("waiting", why=f"the last push was {int(since // 60)} min ago; "
                                       f"at most one every {GAP_S // 60}", pending=take)
    b = busy()
    if b is None:
        return done("paused", why="cannot see GitHub Actions (gh)")
    if b:
        return done("paused", why="a data workflow is running: " + ", ".join(sorted(set(b))))
    if dry_run:
        return done("would-publish", files=take, repair=repair)

    # ---- snapshot, merge
    with admin_lock(), journal.journal_lock(ROOT / "data"):
        snap = {p: _read(p) for p in take}
    head_sha = git("rev-parse", "HEAD").stdout.strip()
    held = st.get("base_files") if st.get("base_head") == head_sha else None

    def base_of(p):
        if held is not None and p in held:
            f = base_dir() / p
            return f.read_text() if held[p] and f.exists() else None
        return show("HEAD", p)
    merged, conflicts = {}, []
    # THE ARCHIVE IS MERGED BY ROW, NOT BY FILE (below, in the worktree): a
    # month the desk sealed while main appended to it is "deleted on one side
    # and changed on the other" file by file, and is no conflict row by row.
    arch = [p for p in take if is_archive(p)]
    for p in take:
        if p in arch:
            continue
        text, c = merge_data.merge_text(p, base_of(p), snap[p], show(theirs_rev, p))
        merged[p] = text
        conflicts += c
    if conflicts:
        inbox("conflict", "The desk and main changed the same thing differently; "
                          "nothing was published:\n" + "\n".join(conflicts[:20]), st,
              {"files": take})
        return done("conflict", conflicts=conflicts[:20])

    # ---- build, check, commit, push
    with worktree(theirs_rev) as wt:
        for p, text in merged.items():
            f = wt / p
            if text is None:
                f.unlink(missing_ok=True)
            else:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text)
        if COMPANIES in merged and _journal_the_merge(
                wt, base_of(COMPANIES), snap.get(COMPANIES), show(theirs_rev, COMPANIES),
                merged[COMPANIES], now, [snap[p] for p in arch if snap.get(p) is not None
                                         and not p.endswith(".gz")]):
            merged[LIVE_JOURNAL] = (wt / LIVE_JOURNAL).read_text()   # staged, and trimmed
        if arch or LIVE_JOURNAL in merged:
            odd = _file_archive(wt, arch, snap)
            if odd:
                bad = ("the desk's journal archive holds " + str(len(odd)) + " line(s) that are "
                       "not JSON, and no row held whole starts with them - what an append cut "
                       "off part way left of a row no file holds whole now: "
                       + "; ".join(odd[:3]) + ". Nothing was published. A person reads each "
                       "where it is (python3 scripts/journal.py --check in the checkout names "
                       "them too); once he has, it is removed from that file, a copy kept "
                       "outside the repository.")
                inbox("checks", "A publish was refused; nothing was pushed:\n" + bad, st,
                      {"files": take})
                return done("refused", why=bad[:1500])
        why = redraw(wt)
        if why:
            inbox("rebuild", "The quick rebuild failed; nothing was published:\n" + why, st)
            return done("failed", why="quick rebuild failed")
        tracked = set(git("ls-files", "--", *merged, "data/board.json", "data/detail",
                          cwd=wt).stdout.split())
        paths = [p for p in list(merged) + ["data/board.json", "data/detail"]
                 if (wt / p).exists() or p in tracked
                 or any(t.startswith(p + "/") for t in tracked)]
        git("add", "--", *paths, cwd=wt)
        _stage_archive(wt)
        staged = git("diff", "--cached", "--name-only", cwd=wt).stdout.split()
        if not staged:
            st["drift_checked"] = main_sha
            if not repair:
                _sync(snap, theirs_rev, st, out)
            return done("idle", why="the merged files equal main already")
        bad = run_checks(wt, staged)
        if bad:
            inbox("checks", "A publish was refused by its checks; nothing was "
                            "pushed:\n" + bad, st, {"files": take})
            return done("refused", why=bad[:1500])
        n_desk = len([p for p in staged if p in merged or is_archive(p)])
        msg = (f"desk: publish {n_desk} file(s) the admin changed"
               if not repair else "desk: redraw the board from main's own inputs")
        body = "\n".join(f"  {p}" for p in staged[:40]) + (
            f"\n  ... and {len(staged) - 40} more" if len(staged) > 40 else "")
        git("-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}",
            "commit", "-q", "-m", msg, "-m", body, cwd=wt)
        sha = git("rev-parse", "HEAD", cwd=wt).stdout.strip()
        r = git("push", REMOTE, f"HEAD:refs/heads/{BRANCH}", cwd=wt, check=False)
        if r.returncode != 0:
            return done("waiting", why="main moved while this cycle built; the next "
                                       "cycle starts over", push=r.stderr.strip()[-300:])
    st["last_push_ts"] = now
    st["last_push"] = {"sha": sha, "at": now_iso(now), "files": staged[:60],
                       "repair": repair}
    st["pending_sha"] = sha
    st["drift_checked"] = sha
    git("fetch", "-q", REMOTE, BRANCH, check=False)
    if not repair:
        _sync(snap, sha, st, out)
    return done("pushed", sha=sha, files=staged[:60], repair=repair)


def _file_archive(wt: pathlib.Path, arch: list, snap: dict) -> None:
    """Carry the desk's archive onto main's, row by row, in the worktree.

    Every line of every archive path the desk changed - the old single file
    first - that main's archive lacks is filed by journal.py's own rules
    (its month; a new part after a sealed one; the cap), and main's archive
    is then brought to those rules as any write would (the old file folded,
    a row a merge left in another month's file moved home, a finished month
    sealed by main's merged live journal). Main's layout
    stands: a sealed file main has is never replaced, so two seals that came
    out differently cannot conflict - the second one's rows are recognised
    by line and nothing is filed twice.

    AND THE LIVE JOURNAL, merged by line (every line either side has), is
    brought back to its rules there (journal trim_live): a row the archive
    now holds leaves it - the desk pruned it, main had not, and the union
    brought it back: 257 rows in both places, filed twice by main's next
    write (2026-11-24 review) - and it keeps its last KEEP entries. A torn
    copy in the desk's archive (an append cut off part way) is told by the
    desk's live journal, which still holds that row whole, and is not
    carried; a row the desk's OLD code glued onto a fragment is split and
    carried (journal.unglue).

    Returns what it could not carry: a line of the desk's archive that is no
    JSON and no torn copy of a row held whole - what a cut-off append left of
    a row no file holds whole - named by the desk's file and line number.
    The cycle is refused over it: carried, it would refuse every publish
    after from main's side, and a person must read it where it is."""
    lines, where = [], {}
    for p in sorted(arch, key=_archive_order):
        if snap.get(p) is not None:
            for i, ln in journal.numbered_lines(p, snap[p]):
                lines.append(ln)
                for x in journal.unglue(ln):
                    where.setdefault(x, (p, i))
    known = journal.lines_of(LIVE_JOURNAL, (snap.get(LIVE_JOURNAL) or "").encode())
    did = journal.normalize(wt / "data", lines=lines, known=known, full=True, trim=True)
    return [f"{where[x][0]} line {where[x][1]} ({len(x):,} bytes, '{journal._head(x)}')"
            for x in did["odd"] if x in where]


def _journal_the_merge(wt: pathlib.Path, base: str | None, ours: str | None,
                       theirs: str | None, merged: str | None, now: float,
                       archived=()) -> dict | None:
    """A journal entry, by the publisher, for each company the desk and main
    BOTH changed while apart: merged field by field (merge_data), the record
    on main is neither side's, and no entry's after-image holds it - so the
    journal said one thing and companies.json another, and main's own
    selftest refused the publish (check_journal_matches_reality, whose
    advice - "recover the value from <id>'s after-image" - would have
    reverted one side's ruling; 2026-11-24 second review). The entry's
    before is main's record, its after the merged one: what the publish does
    to main, undoable like any other write. A company whose last live entry
    already says what the merge made (the crawl's hiring and ats aside, which
    nothing journals) gets none. Appended to the worktree's live journal,
    before the carry trims it; returns the entry, or None."""
    if merged is None or ours is None or theirs is None:
        return None
    name = COMPANIES.rsplit("/", 1)[-1]
    try:
        B = journal.snapshot(json.loads(base) if base else [], name)
        O, T = journal.snapshot(json.loads(ours), name), journal.snapshot(json.loads(theirs), name)
        X = journal.snapshot(json.loads(merged), name)
    except ValueError:
        return None
    live = wt / LIVE_JOURNAL
    text = live.read_text() if live.is_file() else ""
    rows, last = [], {}
    for ln in text.splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        rows.append(r)
        if isinstance(r, dict) and r.get("file") == name:
            for k, ch in (r.get("changes") or {}).items():
                if isinstance(ch, dict) and ch.get("after"):
                    last[k] = ch["after"]

    def own(rec):
        return {f: v for f, v in (rec or {}).items() if f not in merge_data.UPSTREAM_ONLY}
    changes = {}
    for k, x in X.items():
        o, t, b = O.get(k), T.get(k), B.get(k)
        if None in (o, t, b) or o == b or t == b or x in (o, t):
            continue                    # one side's change: that side's entry holds it
        if k in last and own(last[k]) == own(x):
            continue
        changes[k] = {"before": t, "after": x}
    if not changes:
        return None
    entry = {"id": journal.next_id(rows, archive=wt / "data",
                                   lines=[x if isinstance(x, bytes) else x.encode()
                                          for x in archived]),
             "at": now_iso(now), "file": name, "action": "publish-merge", "by": "publisher",
             "why": (f"the desk and main each changed {', '.join(sorted(changes)[:8])}"
                     f"{f' and {len(changes) - 8} more' if len(changes) > 8 else ''} while "
                     f"apart; the publish of {now_iso(now)} merged them field by field, so no "
                     f"entry before this one holds the record as it now stands (before: "
                     f"main's; after: the merge)"),
             "n": len(changes), "changes": changes}
    with open(live, "a") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.write(json.dumps(entry) + "\n")
    return entry


def _stage_archive(wt: pathlib.Path) -> None:
    """Stage the archive as it now stands in the worktree: new parts, parts
    appended to, open parts a seal removed, and the old single file gone -
    and the live journal the carry trimmed with it (staged even when the
    desk's own live journal was not taken: the two go to main together)."""
    for p in (ARCHIVE_DIR, LEGACY_ARCHIVE):
        if (wt / p).exists() or git("ls-files", "--", p, cwd=wt).stdout.strip():
            git("add", "-A", "--", p, cwd=wt)
    if (wt / LIVE_JOURNAL).exists():
        git("add", "--", LIVE_JOURNAL, cwd=wt)


def _hold_base(snap: dict, st: dict) -> None:
    """Keep what was published as the next cycle's base (see base_dir)."""
    d = base_dir()
    held = dict(st.get("base_files") or {}) if st.get("base_head") == \
        git("rev-parse", "HEAD").stdout.strip() else {}
    for p, text in snap.items():
        if is_archive(p):
            continue        # merged by row, never against a base (_file_archive)
        f = d / p
        if text is None:
            f.unlink(missing_ok=True)
        else:
            _write(f, text)
        held[p] = text is not None
    st["base_head"] = git("rev-parse", "HEAD").stdout.strip()
    st["base_files"] = held


def _drop_base(st: dict) -> None:
    shutil.rmtree(base_dir(), ignore_errors=True)
    st.pop("base_head", None)
    st.pop("base_files", None)


def _sync(snap: dict, rev: str, st: dict, out: dict) -> None:
    """Bring the checkout up to `rev` without losing a keystroke: under the
    admin lock, only when every published file is still byte-identical to
    what was snapshotted, and nothing else dirty is in the way. When it
    stands aside, what was published becomes the next cycle's base."""
    ok = _sync_inner(snap, rev, st, out)
    if ok:
        _drop_base(st)
    else:
        _hold_base(snap, st)


def _matches(rev: str, p: str) -> bool:
    """Is the checkout's file exactly what main holds at `rev`, or held at
    any commit between the checkout's HEAD and `rev` (both absent counts)?
    Such a file is not in a fast-forward's way: nothing in it is the desk's
    own. The owner's step fetches main's scripts by hand to bring this
    publisher up (CLAUDE.md, "The journal archive"); a script main changed
    again before a cycle ran matched neither HEAD nor main, and every cycle
    after stood aside for it - "skipped: would overwrite scripts/..." for
    good (2026-11-24 review)."""
    f = ROOT / p
    here = f.read_bytes() if f.is_file() else None
    if here == show_bytes(rev, p):
        return True
    r = git("log", "--format=", "--raw", "--no-abbrev", "--no-renames", f"HEAD..{rev}",
            "--", p, check=False)
    blobs = {ln.split("\t", 1)[0].split()[3] for ln in r.stdout.splitlines()
             if ln.startswith(":") and len(ln.split("\t", 1)[0].split()) >= 4}
    if here is None:
        return "0" * 40 in blobs              # main deleted it on the way
    h = git("hash-object", "--", p, check=False).stdout.strip()
    return bool(h) and h in blobs


def _sync_inner(snap: dict, rev: str, st: dict, out: dict) -> bool:
    with admin_lock(), journal.journal_lock(ROOT / "data"):
        for p, text in snap.items():
            if _read(p) != text:
                out["sync"] = f"skipped: {p} changed since the snapshot; the next cycle carries it"
                return False
        moving = set(git("diff", "--name-only", "HEAD", rev).stdout.split())
        dirty = dirty_paths()
        others = [p for p in dirty if p in moving and p not in snap]
        same = sorted(p for p in others if _matches(rev, p))
        blocking = sorted(p for p in others if p not in same)
        if blocking:
            out["sync"] = "skipped: would overwrite " + ", ".join(blocking[:6])
            inbox("sync", "Published, but the checkout could not be brought up to "
                          "main without overwriting: " + ", ".join(blocking[:12]), st)
            return False
        # AND A DIRTY PATH MAIN DOES NOT MOVE, holding a copy main held on the
        # way: a script main changed after the hand fetch and then put back as
        # the checkout's commit has it, or added and then removed. It is in
        # no fast-forward's way, so it was left as it was - main's passing copy,
        # still staged, run by the desk - under a sync that said "checkout at
        # <sha>, which is main" (2026-11-24 second review). Restored with the
        # rest; a path that is the desk's own (no copy main held) is left.
        stale = sorted(p for p in dirty if p not in moving and p not in snap
                       and _matches(rev, p))
        same += stale
        kept_same = {p: ((ROOT / p).read_bytes() if (ROOT / p).is_file() else None)
                     for p in same}
        restore = list(snap) + same
        tracked = [p for p in restore if _at("HEAD", p)]
        if tracked:
            git("checkout", "HEAD", "--", *tracked)
        for p in restore:
            if p not in tracked:
                git("rm", "-q", "-f", "--cached", "--ignore-unmatch", "--", p, check=False)
                (ROOT / p).unlink(missing_ok=True)
        r = git("merge", "--ff-only", "-q", rev, check=False)
        if r.returncode != 0:
            for p, text in snap.items():           # put the desk's files back
                if text is not None:
                    _write(ROOT / p, text)
                elif _at("HEAD", p):
                    (ROOT / p).unlink(missing_ok=True)
            for p, raw in kept_same.items():
                if raw is not None:
                    _write(ROOT / p, raw)
            out["sync"] = "failed: " + (r.stderr or r.stdout).strip()[-300:]
            inbox("sync", "Published, but the checkout's fast-forward failed; the "
                          "desk's files were put back as they were.\n" + out["sync"], st)
            return False
        out["sync"] = f"checkout at {git('rev-parse', '--short=7', 'HEAD').stdout.strip()}" \
            + _scripts_note()
        return True


def _scripts_note() -> str:
    """'' when the checkout's scripts are main's - at HEAD, nothing changed -
    else what a person must know before calling the checkout main: which
    scripts differ (the desk would run them)."""
    r = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "scripts",
            check=False)
    differ = sorted({rec[3:] for rec in r.stdout.split("\0") if len(rec) > 3})
    if not differ:
        return ""
    return (f", but {len(differ)} script(s) differ from main's: {', '.join(differ[:6])}"
            + (" ..." if len(differ) > 6 else ""))


# ------------------------------------------------------------------ launchd

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-u</string>
    <string>{script}</string>
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>{path}</string>
    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
  </dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def install() -> int:
    """Written from THIS checkout's paths at install time, so no path of the
    owner's machine is ever committed to the public repo."""
    home = pathlib.Path.home()
    dirs = []
    for tool in ("git", "gh", "node"):
        w = shutil.which(tool)
        if w:
            dirs.append(str(pathlib.Path(w).parent))
    path = ":".join(dict.fromkeys(dirs + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    log = home / "Library" / "Logs" / "sledjobs-publish.log"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(PLIST.format(label=LABEL, python=sys.executable,
                                  script=ROOT / "scripts" / "publish.py", root=ROOT,
                                  path=path, log=log))
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    r = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
    print(f"wrote {plist}\nlog: {log}\n" + (r.stderr or "loaded"))
    return r.returncode


def uninstall() -> int:
    plist = pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    plist.unlink(missing_ok=True)
    print(f"removed {plist}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--status", action="store_true")
    m.add_argument("--install", action="store_true")
    m.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.status:
        print(json.dumps(load_state(), indent=1))
        return 0
    try:
        out = cycle(dry_run=a.dry_run)
    except Exception as exc:                            # noqa: BLE001
        st = load_state()
        st["last"] = {"at": now_iso(), "outcome": "crashed",
                      "why": f"{type(exc).__name__}: {exc}"[:800]}
        with contextlib.suppress(Exception):
            inbox("crash", f"The publisher crashed: {type(exc).__name__}: {exc}", st)
        save_state(st)
        print(json.dumps(st["last"]))
        return 1
    print(json.dumps(out))
    return 0 if out.get("outcome") not in ("conflict", "failed", "refused") else 1


if __name__ == "__main__":
    sys.exit(main())
