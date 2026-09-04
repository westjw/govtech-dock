#!/usr/bin/env python3
"""The one place this repository calls a model.

    python3 scripts/llm.py --check       # is a key configured, and what is capped
    python3 scripts/llm.py --spend       # what has been spent, per day and per kind

UNTIL TODAY THIS REPO MADE ZERO API CALLS, and that was not an oversight. Every
proposal in `agent_proposals.json` was written inside a session, by an agent
somebody was watching. The map filled at the speed of a person being at the
keyboard, which is why 2,061 companies still had no description in September.

WHAT CHANGES IS THE SOURCE OF A PROPOSAL AND NOTHING ELSE. A proposal from this
module lands in the same store, behind the same `check_*` door, in the same
admin queue, waiting on the same person. The doors do not know whether anybody
was watching when the answer was written, and they must never be able to tell:
the day a door starts trusting a caller is the day the door stops working.
So this module returns DATA. It does not import admin, it cannot write
companies.json, and every caller hands what it gets to `agents.ingest`.

RAW HTTP, NOT THE SDK. CLAUDE.md's dependency rule is absolute - stdlib plus
requests plus openpyxl, no new dependencies, ever - and the official SDK would
be one. `requests` is already here and the Messages API is one POST. If that
rule is ever relaxed, the SDK is the better call and this module is where the
swap happens; nothing above it would change.

FOUR THINGS THIS REFUSES TO DO, each of them a failure this project has already
had in a different costume:

  NO KEY IS NOT AN ERROR. `send_digests` established the rule and it is the
  same rule: a nightly run must never fail because nobody set up a secret. With
  no ANTHROPIC_API_KEY, `ask()` returns None and says so once. The Action goes
  green having done nothing, which is honest.

  A BAD ANSWER IS NOT AN EXCEPTION. A model that returns prose where JSON was
  asked for, or a truncated object, is a refused call - logged, counted, None
  returned. A crash in a nightly job is a silence nobody reads; a refusal is a
  number in the spend log.

  A LOOP CANNOT SPEND THE MONTH. MAX_CALLS and MAX_SPEND are per process and
  checked before the request, not after. The failure mode here is not a wrong
  answer, it is a bug that asks four thousand times, and the caps exist because
  by the time anybody notices, the bill has already happened.

  THE LOG HOLDS NO PROMPT AND NO ANSWER. `data/llm_log.jsonl` records what it
  cost and what it was for: model, kind, tokens, dollars, ok. The prompts carry
  other people's page text and this repository is about to be public - the same
  reason `site_pages/` is gitignored and `sync_claims` scrubs an address by
  value. A spend log is a bill, not a transcript.

PRICES GO STALE. The table below is what this cost when it was written, in
dollars per million tokens, and it is used for one thing: telling the owner
roughly what a run cost before the invoice does. It is never a budget the API
enforces. If the number here and the number on the bill disagree, the bill is
right and this table needs an edit.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "llm_log.jsonl"

API = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"

# THE KEY FILE LIVES OUTSIDE THIS REPOSITORY, deliberately. govtech-dock is
# about to be public, and a secret inside the working tree is one `git add -A`
# away from being in the history for ever - the same reasoning that put
# site_pages/ outside git and that makes sync_claims scrub an address by
# value. Nothing here can commit what it cannot reach.
KEY_FILE = pathlib.Path.home() / ".config" / "sledjobs" / "anthropic_key"

# The owner's recorded decision (plan O36): sonnet for the volume work, cost is
# his. A caller wanting a harder read passes model= and the log records which
# one answered, so a batch judged by a different model is visible afterwards.
DEFAULT_MODEL = "claude-sonnet-5"

# dollars per million tokens, input / output. See the docstring: an estimate.
PRICES = {
    "claude-sonnet-5":  (3.0, 15.0),
    "claude-opus-5":    (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

MAX_CALLS = 400          # per process
MAX_SPEND = 25.00        # per process, estimated dollars
MAX_OUTPUT = 8000        # tokens; above this the request must stream, and
                         # nothing here needs to, so it is refused instead
MAX_INPUT_CHARS = 240_000
RETRIES = 3
BACKOFF = 4.0            # seconds, doubled per retry

_spent = 0.0
_calls = 0
_said_no_key = False
# WHY THE LAST CALL RETURNED NOTHING, for the caller to read. A truncated
# answer and a refused one are different problems with different fixes, and a
# caller that cannot tell them apart prints "0 came back" and says nothing
# about the $1.38 it just spent finding out.
LAST_STOP = ""


class Refused(Exception):
    """A cap was hit. Distinct from a bad answer, which returns None."""


def key() -> str:
    """The key, from the environment first and then from the key file.

    THE ENVIRONMENT WINS so a one-off run can override what is stored, and CI
    can set a repository secret without any file existing at all.
    """
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        return env
    try:
        return KEY_FILE.read_text().strip()
    except OSError:
        return ""


def key_source() -> str:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "the environment"
    return str(KEY_FILE) if KEY_FILE.exists() else "nowhere"


def diagnose(k: str) -> str:
    """Why a key will not work, in a sentence, or "" when its shape is right.

    NOTHING HERE PRINTS THE KEY. A length, the prefix every Anthropic key
    shares, and whether the string carries the ellipsis the console puts in a
    key it is only DISPLAYING.
    """
    if not k:
        return "nothing is set"
    if "..." in k or "\u2026" in k:
        return ("that is the DISPLAY version, not the key. The console shows a "
                "key in full exactly once, in the dialog when you create it; "
                "the list afterwards abbreviates it with an ellipsis and there "
                "is no way to reveal the rest. Create a new key.")
    if k != k.strip():
        return "it has leading or trailing whitespace, which is enough on its own"
    if k[:1] in "\"'":
        return "it is wrapped in quotes; copy the key without them"
    if k.startswith("sk-ant-oat"):
        return ("that is a Claude Code OAuth token, not an API key. The "
                "Messages API does not take it.")
    if k.startswith(("python", "read ", "export ", "curl ", "/", "-")):
        return (f"that is a shell command, not a key ({k[:22]!r}...). An "
                f"interactive prompt swallowed the next thing typed at it.")
    if not k.startswith("sk-ant-api"):
        return "an API key starts 'sk-ant-api'; this is some other credential"
    if len(k) < 90:
        return f"the prefix is right and it is only {len(k)} characters, so the copy was cut"
    return ""


def set_key_from_clipboard() -> int:
    """Take the key off the clipboard and store it, 0600, outside the repo.

    THE CLIPBOARD, NOT A PROMPT, AND THAT IS THE WHOLE POINT. `read -s` waits
    on stdin, and in a terminal driven by a Run button the next thing to
    arrive on stdin is THE NEXT COMMAND - which it silently accepts as the
    key. That happened three times here and produced three 401s whose only
    clue was a length. Reading from /dev/tty would not have helped: the
    injected keystrokes arrive on the same terminal either way.

    Nothing waits, so nothing can be swallowed. And the key never appears in a
    command, in shell history, or in anything written down.
    """
    import subprocess
    tool = ("pbpaste" if sys.platform == "darwin"
            else "wl-paste" if shutil.which("wl-paste") else "xclip")
    if not shutil.which(tool):
        print(f"no clipboard tool ({tool}) on this machine. Write the key to "
              f"{KEY_FILE} yourself, one line, no quotes.", file=sys.stderr)
        return 1
    try:
        args = [tool] if tool != "xclip" else [tool, "-o", "-selection", "clipboard"]
        got = subprocess.run(args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"could not read the clipboard: {e}", file=sys.stderr)
        return 1
    k = (got.stdout or "").strip()
    if not k:
        print("the clipboard is empty. Copy the key, then run this again.",
              file=sys.stderr)
        return 1
    bad = diagnose(k)
    if bad:
        # REFUSED BEFORE IT IS STORED. Writing a key that cannot work, and
        # then reading it back for weeks, is worse than not having one.
        print(f"NOT STORED - {bad}", file=sys.stderr)
        return 1
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    KEY_FILE.write_text(k + "\n")
    KEY_FILE.chmod(0o600)
    print(f"stored in {KEY_FILE} (owner-read-only, outside the repository)")
    print("the clipboard still holds it - clear it when you are done.\n")
    got = ask("Reply with the single word: ok", 'Return JSON: {"ok": true}',
              "ping", max_tokens=64, thinking=False)
    if got is None:
        print("\nStored, but it did not authenticate. The line above is what "
              "the API said.", file=sys.stderr)
        return 1
    calls, usd = spent()
    print(f"and it works. {calls} request, ${usd:.4f}")
    return 0


def price(model: str, tin: int, tout: int) -> float:
    """Dollars, or 0.0 for a model this table has never heard of.

    A ZERO IS NOT A FREE CALL and the log says which model it was, so an
    unpriced model shows up as a row costing nothing next to a real token
    count - visible, rather than quietly folded into the total.
    """
    p = PRICES.get(model)
    if not p:
        return 0.0
    return tin / 1e6 * p[0] + tout / 1e6 * p[1]


def _log(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _json_from(text: str) -> dict | list | None:
    """The answer, or None if there is not one in there.

    A MODEL ASKED FOR JSON SOMETIMES WRITES A SENTENCE FIRST, and sometimes
    fences it. Neither is a reason to lose a paid answer, so this takes the
    outermost brace- or bracket-delimited run and tries that. What it will not
    do is repair: a truncated object is a call that hit max_tokens, and
    guessing the missing half is exactly the invention this project forbids.
    """
    t = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None


def ask(system: str, user: str, kind: str, *, model: str = DEFAULT_MODEL,
        max_tokens: int = 4000, thinking: bool = True,
        tools: list | None = None) -> dict | list | None:
    """One request. Returns the parsed JSON answer, or None.

    `kind` is what the call was FOR - it is the only description of the work
    that reaches the spend log, so make it the proposal kind ("profile",
    "family") and not a sentence.
    """
    global _spent, _calls, _said_no_key, LAST_STOP
    if not key():
        if not _said_no_key:
            print("no ANTHROPIC_API_KEY set; nothing will be asked.",
                  file=sys.stderr)
            _said_no_key = True
        return None
    if max_tokens > MAX_OUTPUT:
        raise Refused(f"max_tokens {max_tokens} over {MAX_OUTPUT}; stream instead")
    if len(system) + len(user) > MAX_INPUT_CHARS:
        raise Refused(f"prompt is {len(system) + len(user)} chars, over "
                      f"{MAX_INPUT_CHARS}; send a smaller batch")
    # CHECKED BEFORE THE REQUEST, not after. A cap that stops the NEXT call has
    # already paid for the one that broke it.
    if _calls >= MAX_CALLS:
        raise Refused(f"{_calls} calls this run, at the cap of {MAX_CALLS}")
    if _spent >= MAX_SPEND:
        raise Refused(f"${_spent:.2f} spent this run, at the cap of ${MAX_SPEND:.2f}")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if thinking:
        # Adaptive only. budget_tokens is REJECTED on these models, and a 400
        # from a nightly run reads as "the engine is broken".
        body["thinking"] = {"type": "adaptive"}
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": key(), "anthropic-version": VERSION,
               "content-type": "application/json"}

    wait, last = BACKOFF, ""
    for attempt in range(RETRIES):
        try:
            r = requests.post(API, headers=headers, json=body, timeout=600)
        except requests.RequestException as e:
            last = f"transport: {e}"
            time.sleep(wait); wait *= 2
            continue
        if r.status_code in (429, 500, 502, 503, 529):
            # Their own Retry-After beats our guess when they send one.
            hold = r.headers.get("retry-after")
            time.sleep(float(hold) if (hold or "").isdigit() else wait)
            wait *= 2
            last = f"http {r.status_code}"
            continue
        if r.status_code != 200:
            last = f"http {r.status_code}: {r.text[:200]}"
            break
        payload = r.json()
        u = payload.get("usage") or {}
        tin = int(u.get("input_tokens") or 0)
        tout = int(u.get("output_tokens") or 0)
        cost = price(model, tin, tout)
        _spent += cost
        _calls += 1
        # A thinking response puts thinking blocks first. The answer is the
        # text, and only the text.
        text = "".join(b.get("text") or "" for b in payload.get("content", [])
                       if b.get("type") == "text")
        out = _json_from(text)
        LAST_STOP = payload.get("stop_reason") or ""
        _log({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "kind": kind, "model": model, "in": tin, "out": tout,
              "usd": round(cost, 4), "ok": out is not None,
              "stop": payload.get("stop_reason")})
        if out is None:
            why = ("the answer was CUT OFF at max_tokens - send fewer items "
                   "per request" if LAST_STOP == "max_tokens"
                   else f"the answer was not JSON (stop_reason {LAST_STOP})")
            print(f"  {kind}: {why}", file=sys.stderr)
        return out
    _log({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
          "kind": kind, "model": model, "in": 0, "out": 0, "usd": 0.0,
          "ok": False, "stop": f"failed: {last}"})
    print(f"  {kind}: {last}", file=sys.stderr)
    return None


def spent() -> tuple[int, float]:
    """Calls and estimated dollars, this process."""
    return _calls, _spent


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--set-key", dest="set_key", action="store_true",
                    help="take the key off the clipboard, store it 0600 "
                         "outside the repo, and prove it works")
    ap.add_argument("--forget-key", dest="forget_key", action="store_true")
    ap.add_argument("--ping", action="store_true",
                    help="one 4-token request, to prove the key authenticates")
    ap.add_argument("--spend", action="store_true")
    a = ap.parse_args()
    if a.set_key:
        return set_key_from_clipboard()
    if a.forget_key:
        if KEY_FILE.exists():
            KEY_FILE.unlink()
            print(f"deleted {KEY_FILE}")
        else:
            print("no key file to delete")
        return 0
    if a.check:
        # WHAT IS WRONG WITH THE KEY, WITHOUT PRINTING IT. Three 401s in a row
        # cost more attention than they should have, because "set, 47 chars"
        # was the only fact available and it does not say WHICH 47. Everything
        # below is safe to read aloud: a length, the prefix every Anthropic
        # key shares, and whether the string carries the ellipsis the console
        # puts in a key it is only DISPLAYING.
        k = key()
        print(f"default model : {DEFAULT_MODEL}")
        print(f"caps per run  : {MAX_CALLS} calls, ${MAX_SPEND:.2f}, "
              f"{MAX_OUTPUT} output tokens, {MAX_INPUT_CHARS:,} prompt chars")
        print(f"key found in  : {key_source()}\n")
        if not k:
            print("No key anywhere. Copy it to the clipboard, then:\n"
                  f"  python3 {pathlib.Path(__file__).resolve()} --set-key\n\n"
                  "In CI: a repository secret named ANTHROPIC_API_KEY.")
            return 0
        raw = os.environ.get("ANTHROPIC_API_KEY", "") or k
        print(f"length        : {len(k)}   (a real key is about 108)")
        print(f"prefix        : {k[:11]!r}")
        print(f"ellipsis      : {'YES' if ('...' in k or chr(8230) in k) else 'no'}")
        print(f"whitespace    : {'YES' if raw != raw.strip() else 'no'}\n")
        bad = diagnose(k)
        if bad:
            print(f"THAT WILL NOT WORK - {bad}")
            print(f"\nCopy the real key to the clipboard, then:\n"
                  f"  python3 {pathlib.Path(__file__).resolve()} --set-key")
        else:
            print(f"The shape is right. Prove it authenticates:\n"
                  f"  python3 {pathlib.Path(__file__).resolve()} --ping")
        return 0
    if a.ping:
        # THE CHEAPEST POSSIBLE REAL REQUEST. --check only says whether a
        # string is set, and a key that is present and wrong fails later,
        # deep inside a batch, as an http 401 buried in a run summary. This
        # asks the API and reports what it said. Four output tokens, no
        # thinking: a fraction of a cent.
        if not key():
            print("no ANTHROPIC_API_KEY set")
            return 1
        got = ask("Reply with the single word: ok",
                  'Return JSON: {"ok": true}', "ping",
                  max_tokens=64, thinking=False)
        if got is None:
            print("\nThe key did not work, or the reply was not JSON. The line "
                  "above is what the API said - a 401 means the key is wrong "
                  "or truncated, a 429 means slow down.")
            return 1
        calls, usd = spent()
        print(f"the key works. {calls} request, ${usd:.4f}")
        return 0
    if a.spend:
        rows = read_log()
        if not rows:
            print("nothing asked yet")
            return 0
        by_day: dict = collections.defaultdict(lambda: [0, 0.0, 0])
        by_kind: dict = collections.defaultdict(lambda: [0, 0.0, 0])
        for r in rows:
            for b, k in ((by_day, r["at"][:10]), (by_kind, r.get("kind", "?"))):
                b[k][0] += 1
                b[k][1] += r.get("usd", 0.0)
                b[k][2] += 0 if r.get("ok") else 1
        print(f"{len(rows)} call(s), ${sum(r.get('usd', 0.0) for r in rows):.2f} "
              f"estimated, {sum(1 for r in rows if not r.get('ok'))} that "
              f"answered nothing usable\n")
        for title, b in (("by day", by_day), ("by kind", by_kind)):
            print(title)
            for k in sorted(b):
                n, usd, bad = b[k]
                print(f"  {k:28} {n:5} call(s)  ${usd:7.2f}"
                      + (f"  {bad} unusable" if bad else ""))
            print()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
