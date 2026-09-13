#!/usr/bin/env python3
"""Send the day's alert digests. Runs in CI, after the refresh has committed.

Subscribers live in Cloudflare KV, never in this repository - the repository
is public. This reads them over the KV REST API, asks digest.py what each one
would receive today, and sends only where the answer is "something worth an
email".

Three rules that keep this from becoming a spam cannon:

  1. NOTHING IS SENT WITHOUT --send. A dry run is the default, prints what
     would go out, and touches nobody's inbox. The one irreversible thing this
     repository can do should not be the thing that happens when you run the
     file to see what it does.
  2. UNCONFIRMED ADDRESSES ARE SKIPPED, always, no flag to override.
  3. last_sent ADVANCES ONLY ON A SUCCESSFUL SEND. If the mail API is down,
     those roles ride along to the next digest instead of vanishing into a
     window nobody received.
  4. AND THE THIRD STATE, which rule 3 alone could not represent: the mail
     LEFT and last_sent could not be written. Rule 3 guards one direction -
     send fails, nothing is recorded, roles carry - and this is the inverse.
     It happened on 2026-09-10: a transient 401 from Cloudflare KV raised out
     of the loop and took the whole run down at the last step. Two harms, both
     invisible. Every subscriber after that one got nothing and was never
     told. And last_sent stayed stale, so the next digest clearing their
     volume floor repeats a window they already received. No duplicate
     actually went out, and only because the digest did not clear the floor on
     either following day - luck, not a guard.

     So: the KV write RETRIES, it never raises, one subscriber's failure never
     ends the loop, and a send we could not record is named on stderr and
     fails the step AFTER everybody has been served. There is nowhere safe to
     write it down - CI is ephemeral, and no address or token may enter a
     tracked file - so saying it plainly is the honest limit.

No address is ever printed in full: this runs in CI and CI logs are forever.

  python scripts/send_digests.py            # dry run, prints a plan
  python scripts/send_digests.py --send     # actually mails
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import brand   # noqa: E402
import digest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CF = "https://api.cloudflare.com/client/v4"
FROM = brand.FROM


def mask(email: str) -> str:
    """j****@gmail.com - enough to tell two subscribers apart in a log,
    not enough to be a mailing list if the log leaks."""
    name, _, host = email.partition("@")
    return f"{name[:1]}****@{host}"


class KV:
    def __init__(self, account: str, namespace: str, token: str):
        self.base = f"{CF}/accounts/{account}/storage/kv/namespaces/{namespace}"
        self.h = {"authorization": f"Bearer {token}"}

    def keys(self, prefix: str) -> list[str]:
        out, cursor = [], None
        while True:
            p = {"prefix": prefix, "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            r = requests.get(f"{self.base}/keys", headers=self.h, params=p, timeout=30)
            r.raise_for_status()
            body = r.json()
            out += [k["name"] for k in body.get("result", [])]
            cursor = (body.get("result_info") or {}).get("cursor")
            if not cursor:
                return out

    def get(self, key: str) -> dict | None:
        r = requests.get(f"{self.base}/values/{key}", headers=self.h, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return None

    def put(self, key: str, value: dict) -> bool:
        """Write a subscription back. True on success; NEVER raises.

        IT RETURNS A VERDICT BECAUSE THE CALLER HAS ALREADY SENT THE MAIL.
        `send_mail` has retried and returned True by the time this runs, so an
        exception out of here is not "the write failed" - it is "a person has
        the email and we cannot record that they do". Raising took the whole
        run down mid-loop on 2026-09-10, which is two separate harms: every
        subscriber after that one in the list got nothing and was never told,
        and `last_sent` stayed stale so the same window would send again.

        RETRIED ON 401, WHICH LOOKS WRONG AND IS NOT. A 401 is normally a real
        auth failure and retrying it is pointless. The one this exists for was
        transient: the same CF_API_TOKEN worked on 09-09 and again on 09-11,
        and failed once in between. Two more attempts cost two seconds and
        would have saved that run. A token that is genuinely revoked still
        fails all three and is reported.
        """
        for attempt in range(3):
            try:
                r = requests.put(f"{self.base}/values/{key}", headers=self.h,
                                 timeout=30,
                                 files={"value": (None, json.dumps(value)),
                                        "metadata": (None, "{}")})
                if r.ok:
                    return True
                if r.status_code in (401, 429) or r.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                print(f"    KV refused the write: {r.status_code}", flush=True)
                return False
            except requests.RequestException as exc:
                print(f"    KV {type(exc).__name__}", flush=True)
                time.sleep(2 ** attempt)
        return False

    def get_or_none(self, key: str) -> tuple[dict | None, bool]:
        """(subscription, could_we_read_it). A read we could not make is not
        an empty subscription: returning None for both would silently skip a
        real subscriber and count them as "nothing to send"."""
        for attempt in range(3):
            try:
                return self.get(key), True
            except requests.RequestException:
                time.sleep(2 ** attempt)
            except Exception:
                time.sleep(2 ** attempt)
        return None, False


def send_mail(key: str, to: str, subject: str, text: str, html: str) -> bool:
    for attempt in range(3):
        try:
            r = requests.post("https://api.resend.com/emails", timeout=30,
                              headers={"authorization": f"Bearer {key}"},
                              json={"from": FROM, "to": [to], "subject": subject,
                                    "text": text, "html": html,
                                    # One-click unsubscribe. Without it Gmail
                                    # treats a bulk sender as suspect, and a
                                    # person who wants out should not have to
                                    # hunt for a link at the bottom.
                                    "headers": {
                                        "List-Unsubscribe-Post":
                                            "List-Unsubscribe=One-Click"}})
            if r.ok:
                return True
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            print(f"    refused: {r.status_code}", flush=True)
            return False
        except requests.RequestException as exc:
            print(f"    {type(exc).__name__}", flush=True)
            time.sleep(2 ** attempt)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="actually send. Without it this is a dry run.")
    ap.add_argument("--today", help="pretend today is this date")
    a = ap.parse_args()

    need = ["CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN"]
    missing = [n for n in need if not os.environ.get(n)]
    if missing:
        print(f"alerts not configured (missing {', '.join(missing)}) - nothing to do")
        return 0
    resend = os.environ.get("RESEND_KEY")
    if a.send and not resend:
        print("RESEND_KEY missing; refusing to run a send")
        return 1

    kv = KV(os.environ["CF_ACCOUNT_ID"], os.environ["CF_KV_NAMESPACE_ID"],
            os.environ["CF_API_TOKEN"])
    board = json.loads((ROOT / "data" / "board.json").read_text())
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()

    tokens = kv.keys("sub:")
    print(f"{len(tokens)} subscriptions, {today} ({today.strftime('%A')})"
          + ("" if a.send else "  [DRY RUN - no mail will be sent]"), flush=True)

    sent = skipped = failed = 0
    unreadable = 0
    # SENT, BUT NOT RECORDED. The one state the old code could not represent:
    # the mail left and `last_sent` could not be written. Those subscribers may
    # receive the same window again, and the run must say so by name.
    at_risk: list[str] = []
    for key in tokens:
        sub, could_read = kv.get_or_none(key)
        if not could_read:
            # A SUBSCRIBER WE COULD NOT READ IS NOT A SUBSCRIBER WITH NOTHING
            # TO SEND. Skipping is the safe side - no mail goes out on prefs
            # and a last_sent we do not have - but it is a skip somebody has to
            # know happened, not a silent `continue`.
            unreadable += 1
            print(f"  [unreadable subscription - skipped, not 'nothing to send']",
                  flush=True)
            continue
        if not sub or not sub.get("email"):
            continue
        who = mask(sub["email"])
        if not sub.get("confirmed"):
            skipped += 1
            continue
        s = dict(sub.get("prefs") or {})
        s["last_sent"] = sub.get("last_sent")
        s["token"] = key[4:]
        d = digest.build(board, s, today)
        if not d["send"]:
            print(f"  {who}: {d['why']}", flush=True)
            skipped += 1
            continue
        subject, text, html = digest.render(d, s, board)
        print(f"  {who}: {subject}", flush=True)
        if not a.send:
            continue
        if send_mail(resend, sub["email"], subject, text, html):
            sent += 1
            sub["last_sent"] = today.isoformat()
            # ONLY AFTER THE MAIL ACTUALLY LEFT - and the count above is
            # incremented BEFORE this, because it counts mail that left, which
            # is now true whatever the write does next.
            if not kv.put(key, sub):
                at_risk.append(who)
        else:
            failed += 1

    print(f"\n{sent} sent, {skipped} skipped, {failed} failed"
          + (f", {unreadable} unreadable" if unreadable else "")
          + ("" if a.send else "  (dry run)"))
    if at_risk:
        # NAMED, LOUD, AND THE RUN FAILS - after everybody has been served.
        # These people have the email; we could not record that they do, so
        # the next digest that clears their volume floor will send the same
        # window again. There is nowhere safe to write this down: CI is
        # ephemeral, and no address or token may enter a tracked file. Saying
        # it plainly and failing the step is the honest limit.
        print(f"\n{len(at_risk)} subscriber(s) WERE SENT MAIL that could not be "
              f"recorded: {', '.join(at_risk)}", file=sys.stderr)
        print(f"  Their last_sent is stale, so the next digest clearing their "
              f"floor repeats this window. Re-running this script does NOT fix "
              f"it - it would send again. Advance last_sent in KV by hand, or "
              f"accept the repeat.", file=sys.stderr)
    if unreadable:
        print(f"\n{unreadable} subscription(s) could not be READ and got "
              f"nothing. That is a fact about the KV API today, not about "
              f"whether they had roles waiting.", file=sys.stderr)
    # A FAILED SEND IS STILL NOT A BROKEN BUILD - the roles carry to the next
    # digest, which is what `failed` counts and why it is not here. A send we
    # could not RECORD is different: nothing carries it, and a person has to
    # know. Same for a subscriber we could not read at all.
    return 1 if (at_risk or unreadable) else 0


if __name__ == "__main__":
    raise SystemExit(main())
