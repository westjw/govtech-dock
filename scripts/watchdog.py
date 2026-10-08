#!/usr/bin/env python3
"""Is the board still being built, and does it still say true things?

    python3 scripts/watchdog.py            # exit 1 if something is wrong
    python3 scripts/watchdog.py --json

The nightly job can stop in a way nobody notices. A failed workflow sends
GitHub's own email; a workflow that succeeds while doing nothing sends
NOTHING, and the site keeps serving yesterday's board with today's confidence.
So does a cron that silently stopped firing, a step whose `continue-on-error`
swallowed the reason, and a token that expired.

The public page carries the build date, which is the honest thing to do and is
also the thing nobody re-reads. This is the second reading: a separate job, on
its own schedule, that asks whether the first one actually ran - and it is a
SEPARATE JOB on purpose, because a check that lives inside the pipeline it
watches goes quiet at exactly the moment it is needed.

WHAT IT CHECKS, and each one is a way this has actually gone wrong or could:

  - the board is not stale. Two missed nights is a fault, not a blip.
  - the snapshot for the last run exists. build_board writing board.json and
    failing before the history snapshot would leave the audit trail with a
    hole nobody would find later.
  - the board is not suspiciously empty. A crawl that answers for every
    company and reads nothing still writes a valid file, and every card on
    the public site would say "no roles" - the false absence this project
    exists to refuse, published at once about two thousand companies.
  - coverage has not collapsed. If `structured` halves overnight, something
    broke in discovery rather than 140 companies deleting their job boards.
  - meta.json's dates agree with the history directory.
  - the last deploy went out. Everything above reads data/ in the repo, so a
    Cloudflare build that failed left an old site live with a good board
    sitting unpublished - and GitHub emails nobody about a failed check from
    an app (launch audit, 2026-10-06). See deploy_faults().

It asserts nothing about whether the numbers are GOOD. A quiet week is a quiet
week. It only reports the shapes that mean the machinery stopped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Two nights. One missed run is a slow crawl, a GitHub incident, or a rerun
# that took longer than a day - reporting that as a fault trains somebody to
# ignore this. Two is the machinery.
STALE_DAYS = 2

# A board that has lost more than this share of its postings in one run did not
# have a quiet day. The largest real single-day fall on file is 149 of ~4,450,
# about 3%; 40% is far outside anything a market does and well inside what a
# broken fetcher does.
CLIFF = 0.40


# The check run Cloudflare Pages posts on every commit it builds.
DEPLOY_CHECK = "Cloudflare Pages"
# A build normally takes a few minutes. One still running after this is stuck.
DEPLOY_GRACE = dt.timedelta(hours=2)
# How far back to look for the newest commit Pages has answered for.
DEPLOY_LOOKBACK = 10


def _github(token: str):
    def fetch(path: str):
        req = urllib.request.Request(
            "https://api.github.com" + path,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "govtech-dock-watchdog"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    return fetch


def _when(stamp: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def deploy_faults(env=None, fetch=None, now=None) -> list[tuple[str, str, bool]]:
    """Did the newest deploy Cloudflare Pages attempted actually go out?

    Walks main's newest commits for the newest one carrying a Cloudflare
    Pages check run. Success is fine. Failed, cancelled or timed out is a
    HARD fault: the site is serving an older build than the repo holds, and
    the staleness banner will not say so for two days. Still running past
    DEPLOY_GRACE is hard too. Still running inside it is a build in
    progress, so the commit before it is judged instead.

    Only in GitHub Actions (GITHUB_TOKEN and GITHUB_REPOSITORY set); a
    local run has nothing to ask. A failed question - the API down, a token
    without `checks: read` - is SOFT: a watchdog that opens an issue because
    GitHub hiccuped is a watchdog people learn to ignore.
    """
    env = os.environ if env is None else env
    token, repo = env.get("GITHUB_TOKEN"), env.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return []
    fetch = fetch or _github(token)
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        commits = fetch(f"/repos/{repo}/commits?sha=main&per_page={DEPLOY_LOOKBACK}")
        for i, c in enumerate(commits):
            sha = c["sha"]
            runs = fetch(f"/repos/{repo}/commits/{sha}/check-runs"
                         f"?check_name={urllib.request.quote(DEPLOY_CHECK)}")
            runs = sorted(runs.get("check_runs") or [],
                          key=lambda r: r.get("started_at") or "", reverse=True)
            if not runs:
                # MAIN'S TIP IS ALWAYS THE TIP OF A PUSH, and Pages builds every
                # push's tip within a minute (400 commits checked, 2026-10-08).
                # Only the middle commits of a multi-commit push go unbuilt. So a
                # tip with no run, past the grace, means Pages stopped picking
                # main up - an app that lost the repo, deploys switched off - and
                # skipping it would read an older success as a live site
                # (third review, 2026-10-08).
                pushed = _when((c.get("commit") or {}).get("committer", {}).get("date"))
                if i == 0 and pushed and now - pushed > DEPLOY_GRACE:
                    return [(f"Cloudflare Pages never picked up main's newest commit "
                             f"{sha[:7]}",
                             f"pushed {pushed:%Y-%m-%d %H:%M} UTC and no Pages build "
                             f"exists for it, so the public site is an older build than "
                             f"the repo holds", True)]
                continue
            run = runs[0]
            if run.get("status") != "completed":
                started = _when(run.get("started_at"))
                if started and now - started > DEPLOY_GRACE:
                    return [(f"the deploy of {sha[:7]} has been running since "
                             f"{run.get('started_at')}",
                             "a Pages build takes minutes; this one is stuck, and "
                             "the site is still serving the build before it", True)]
                continue
            if run.get("conclusion") == "success":
                return []
            return [(f"the last deploy failed ({run.get('conclusion')}) on {sha[:7]}",
                     f"Cloudflare Pages did not publish main as of "
                     f"{run.get('completed_at')}, so the public site is an older "
                     f"build than the repo holds. {run.get('html_url') or ''}".strip(),
                     True)]
        return [(f"no Cloudflare Pages result on the last {DEPLOY_LOOKBACK} commits",
                 "either builds are being skipped or the Pages app stopped "
                 "reporting to GitHub", False)]
    except Exception as e:                       # noqa: BLE001 - see docstring
        return [("could not ask GitHub whether the last deploy went out",
                 f"{type(e).__name__}: {e}", False)]


def load(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def today() -> dt.date:
    return dt.date.today()


def check() -> list[dict]:
    """Every fault found, worst first. Empty list means the machinery is fine."""
    out: list[dict] = []

    def bad(what: str, why: str, hard: bool = True):
        out.append({"what": what, "why": why, "hard": hard})

    board = load(DATA / "board.json")
    if board is None:
        bad("board.json is missing or unreadable",
            "the public site reads this file directly - if it is gone, so is "
            "the board")
        return out

    gen = (board.get("generated") or "")[:10]
    try:
        age = (today() - dt.date.fromisoformat(gen)).days
    except ValueError:
        bad("board.json has no readable build date",
            f"generated is {board.get('generated')!r}, and the page prints "
            f"this to a visitor as the freshness claim")
        age = None

    if age is not None and age > STALE_DAYS:
        bad(f"the board is {age} days old",
            f"built {gen}; the nightly job has not produced a board in "
            f"{age} days and the site is serving it as current")

    snaps = sorted(p.stem for p in (DATA / "history").glob("*.json"))
    if gen and gen not in snaps:
        bad(f"no history snapshot for {gen}",
            "build_board wrote board.json and did not write the day's "
            "snapshot - the audit trail has a hole in it, and a hole nobody "
            "notices today cannot be filled in later")

    postings = board.get("postings") or []
    orgs = board.get("organizations") or []
    if not postings:
        bad("the board has no postings at all",
            f"{len(orgs):,} companies and zero roles - a crawl that answers "
            f"for everybody and reads nothing writes a perfectly valid file, "
            f"and every card on the site would say no roles")
    elif len(snaps) >= 2:
        prev = load(DATA / "history" / f"{snaps[-2]}.json", {})
        before = len(prev.get("ids") or [])
        if before and len(postings) < before * (1 - CLIFF):
            lost = before - len(postings)
            bad(f"{lost:,} postings vanished in one run",
                f"{before:,} on {snaps[-2]} and {len(postings):,} now, a fall "
                f"of {lost / before:.0%}. The market does not do that; a "
                f"fetcher that stopped answering does")

    cov = board.get("coverage") or {}
    if cov:
        s = cov.get("structured")
        if isinstance(s, int) and s < 100:
            bad(f"only {s} companies are on a structured API",
                "that number has been near 280 - a collapse here means "
                "discovery broke, not that companies deleted their boards")

    meta = load(DATA / "meta.json", {})
    last = meta.get("last_run")
    if last and snaps and last > snaps[-1]:
        bad(f"meta.json says the last run was {last}",
            f"but the newest snapshot is {snaps[-1]} - the run recorded "
            f"itself and did not leave the data behind", hard=False)

    for what, why, hard in deploy_faults():
        bad(what, why, hard)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    faults = check()
    if a.json:
        print(json.dumps({"ok": not any(f["hard"] for f in faults),
                          "faults": faults}, indent=1))
    elif not faults:
        b = load(DATA / "board.json", {})
        print(f"ok - board built {(b.get('generated') or '?')[:10]}, "
              f"{len(b.get('postings') or []):,} postings, "
              f"{len(b.get('organizations') or []):,} companies")
    else:
        for f in faults:
            print(f"{'FAULT' if f['hard'] else 'note '}: {f['what']}")
            print(f"       {f['why']}")
    # A soft fault is worth saying out loud and is not worth waking somebody
    # for; only a hard one fails the job.
    return 1 if any(f["hard"] for f in faults) else 0


if __name__ == "__main__":
    sys.exit(main())
