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
import digest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CF = "https://api.cloudflare.com/client/v4"
FROM = "SoleSource <alerts@solesourcejobs.com>"


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

    def put(self, key: str, value: dict) -> None:
        r = requests.put(f"{self.base}/values/{key}", headers=self.h, timeout=30,
                         files={"value": (None, json.dumps(value)),
                                "metadata": (None, "{}")})
        r.raise_for_status()


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
    for key in tokens:
        sub = kv.get(key)
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
            sub["last_sent"] = today.isoformat()
            kv.put(key, sub)          # only after the mail actually left
            sent += 1
        else:
            failed += 1

    print(f"\n{sent} sent, {skipped} skipped, {failed} failed"
          + ("" if a.send else "  (dry run)"))
    # A failed send is not a broken build - the roles carry to the next digest.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
