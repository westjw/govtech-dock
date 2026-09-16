#!/usr/bin/env python3
"""The owner's gate on a claim, and the only abuse control the free tier has.

    python3 scripts/verify_claims.py                      # who is waiting
    python3 scripts/verify_claims.py --company tyler-technologies
    python3 scripts/verify_claims.py --verify tyler-technologies --tail a1b2c3 --write
    python3 scripts/verify_claims.py --refuse  acme --tail a1b2c3 --why "..." --write

WHY THIS EXISTS AT ALL. Posting a job here is free. A posting fee was the
other way to make abuse expensive, and it was rejected: it taxes the 99% of
companies who are exactly who they say they are in order to inconvenience the
1% who are not. So the cost is paid once, here, by a person looking - and
everything downstream of this gate is self-serve precisely BECAUSE this gate
is not. If this step ever becomes a rubber stamp, the free tier has no abuse
control at all, and nothing else in the pipeline is watching for one.

WHAT A CONFIRMED CLAIM ACTUALLY PROVES, which is less than it looks like.
`claim_confirmed` means somebody read mail at the company's own domain. It
does not mean the company wants them speaking for it, that the domain belongs
to who we think, or that the company is what our record says. An intern, a
departing employee and a contractor with a mailbox all clear that bar. This
step is where a person asks the question a domain check cannot.

THE ADDRESS NEVER LANDS. It lives in Cloudflare KV because mail has to be sent
to it; this reads it, sends one message, and writes the DOMAIN and the DATE to
the log. Printed masked, always - `check_no_person_in_the_repo` is the
standing guard and a terminal is a place people paste from.

THE DECISION IS THE LOG ENTRY, NOT A FIELD IN KV. `claim_verified` in
data/employer_events.jsonl is append-only, in git, and written only from here.
That matters downstream: sync_claims decides whether a correction may land
unreviewed by asking THIS log, never by trusting a `verified` flag on the KV
record. The Worker writes KV. If the Worker's word were enough, a bug in the
claim endpoint would hand out self-serve access to the map, which is the one
thing the whole division of labour exists to make impossible.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import brand                                                    # noqa: E402
import digest                                                   # noqa: E402
import employer_log                                             # noqa: E402
import sync_claims                                              # noqa: E402

SITE = brand.SITE


def mask(addr: str) -> str:
    """Enough to tell two people at one company apart, and nothing more."""
    u, _, d = str(addr or "").partition("@")
    if not u or not d:
        return "(no address on the record)"
    head = u[0] if len(u) <= 2 else u[:2]
    return f"{head}{'*' * min(6, max(1, len(u) - len(head)))}@{d}"


def waiting(kv) -> list[dict]:
    """Confirmed claims the owner has not yet ruled on, worst wait first.

    Reads the CLAIM records rather than the claims file, because the thing
    being ruled on is a person's claim and two people at one company are two
    decisions. `claims.json` collapses them to a company.
    """
    out = []
    for key in kv.keys("claim:"):
        k = key.split("/")[-1] if "/" in key else key
        rec = kv.get(k) or {}
        if not isinstance(rec, dict) or not rec.get("company_id"):
            continue
        if not rec.get("confirmed"):
            continue          # not a claim yet; the funnel counts it, not this
        cid, tail = rec["company_id"], k.split(":", 1)[-1][-6:]
        st = employer_log.state(cid)
        if rec.get("domain") in st["verified_domains"]:
            continue
        if _ruled(cid, tail):
            continue
        out.append({"company_id": cid, "name": rec.get("name") or cid,
                    "domain": rec.get("domain") or "", "email": rec.get("email") or "",
                    "tail": tail, "confirmed_at": rec.get("confirmed_at") or "",
                    "created": rec.get("created") or "",
                    "proposals": rec.get("proposals") or 0})
    return sorted(out, key=lambda r: r["confirmed_at"] or r["created"])


def _ruled(cid: str, tail: str) -> str:
    """'verified', 'refused' or '' for this one claim.

    KEYED ON THE TAIL, NOT THE COMPANY. Two people at one company are two
    rulings; keying on the company would let the first person through the gate
    carry the second, which is the gate not being a gate.
    """
    for ev in employer_log.events(company_id=cid):
        if ev.get("claim_tail") != tail:
            continue
        if ev["kind"] == "claim_verified":
            return "verified"
        if ev["kind"] == "claim_refused":
            return "refused"
    return ""


def evidence(row: dict, companies: list) -> list[str]:
    """What a person needs on screen to answer 'is this really them'.

    RE-CHECKED HERE, NOT TRUSTED FROM THE ENDPOINT. claim.js already refused a
    domain that does not match the website on file, but a guard that only runs
    in the caller proves nothing about this path: sync_claims replays KV
    records, and a record written before that rule existed - or by a Worker
    with a bug in it - would reach this queue unchallenged.
    """
    c = next((x for x in companies if x.get("id") == row["company_id"]), None)
    site = (c or {}).get("website") or ""
    host = sync_claims_registrable(site)
    lines = [
        f"company    {row['name']}  ({row['company_id']})",
        f"website    {site or '(none on file)'}",
        f"claimed by {mask(row['email'])}   domain {row['domain'] or '(none)'}",
        f"confirmed  {(row['confirmed_at'] or row['created'])[:19] or '(unknown)'}"
        f"   proposals sent so far: {row['proposals']}",
        f"page       {SITE}/c/{row['company_id']}",
    ]
    if not c:
        lines.append("!! NO SUCH COMPANY on file. Refuse: there is no page to hold.")
    elif not host:
        lines.append("!! no website on file, so the domain rests on nothing here.")
    elif host != row["domain"]:
        lines.append(f"!! DOMAIN DOES NOT MATCH the website on file ({host}). "
                     f"claim.js should have refused this. Do not verify it "
                     f"until you know why it is here.")
    else:
        lines.append(f"ok         domain matches the website on file ({host})")
    return lines


def sync_claims_registrable(url: str) -> str:
    """The registrable host of a website we published ourselves."""
    h = str(url or "").split("//")[-1].split("/")[0].lower()
    h = h.replace("www.", "", 1) if h.startswith("www.") else h
    if not h or "." not in h:
        return ""
    p = h.split(".")
    if len(p) <= 2:
        return h
    if len(p[-2]) <= 3 and len(p[-1]) == 2:      # co.uk, com.au
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def welcome(row: dict) -> tuple[str, str, str]:
    """(subject, text, html) for the one mail this gate sends.

    IT SAYS WHAT CHANGED AND WHAT DID NOT. Before this mail every correction
    waited on a person; after it, their description, their logo and their
    roles go live unreviewed. A welcome that only says "you're in" leaves them
    guessing which, and the three things still refused are exactly the three
    somebody will otherwise try once and be silently confused by.
    """
    page = f"{SITE}/c/{row['company_id']}"
    sub = f"You can edit {row['name']} on {brand.NAME}"
    text = (
        f"{row['name']} is verified on {brand.NAME}.\n\n"
        f"From now on your description, your logo and your open roles go "
        f"straight onto your page - no review, no waiting on us. Posting "
        f"roles is free and stays free.\n\n"
        f"Three things stay ours, and they are worth saying out loud:\n"
        f"  - your competitors. Who a buyer shortlists you against is not "
        f"yours to edit, and a market map where vendors curate their own "
        f"rivals is worth nothing to the people reading it.\n"
        f"  - your category. You can ask; a person decides. A company filing "
        f"itself onto a busier shelf is the oldest trick in directory "
        f"listings.\n"
        f"  - anything about another company.\n\n"
        f"Your page: {page}\n"
        f"You can hand the claim back at any time, and nothing you sent is "
        f"held hostage if you do.\n")
    html = digest.shell(
        f"{row['name']} is verified on {brand.NAME}",
        f"""<p style="margin:0 0 14px"><strong>{row['name']}</strong> is verified.
        We looked, and you are who you said you were.</p>
        <p style="margin:0 0 14px">From now on your description, your logo and your
        open roles go straight onto your page &mdash; no review, no waiting on us.
        Posting roles is free and stays free.</p>
        <p style="margin:0 0 8px">Three things stay ours, and they are worth saying
        out loud:</p>
        <ul style="margin:0 0 18px;padding-left:20px">
          <li style="margin:0 0 6px"><strong>Your competitors.</strong> Who a buyer
          shortlists you against is not yours to edit &mdash; a market map where
          vendors curate their own rivals is worth nothing to the people reading
          it.</li>
          <li style="margin:0 0 6px"><strong>Your category.</strong> You can ask; a
          person decides.</li>
          <li style="margin:0 0 6px"><strong>Anything about another company.</strong></li>
        </ul>
        {digest.button(page, "Open your page")}
        <p style="margin:18px 0 0;font-size:13px;color:#556F82">You can hand the
        claim back at any time, and nothing you sent is held hostage if you do.</p>""",
        [["Their page", page], [brand.NAME, SITE]])
    return sub, text, html


def _send(row: dict) -> tuple[bool, str]:
    """Send the welcome. Returns (sent, why not)."""
    key = os.environ.get("RESEND_KEY")
    if not key:
        return False, "RESEND_KEY is not set, so no welcome could be sent"
    if not row.get("email"):
        return False, "the KV record carries no address"
    import send_digests
    sub, text, html = welcome(row)
    ok = send_digests.send_mail(key, row["email"], sub, text, html)
    return bool(ok), "" if ok else "Resend refused the message"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--company", help="show one company's waiting claims")
    ap.add_argument("--verify", metavar="COMPANY")
    ap.add_argument("--refuse", metavar="COMPANY")
    ap.add_argument("--tail", help="the six-character claim tail from the queue")
    ap.add_argument("--why", default="", help="required to refuse")
    ap.add_argument("--no-mail", action="store_true",
                    help="verify without sending the welcome (it can be sent later)")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    kv = sync_claims._kv()
    if kv is None:
        print("no CF_* secrets set; claiming is not configured, so nothing has "
              "been claimed and there is nothing to verify.")
        return 0

    import admin
    companies = admin.read_companies()
    rows = waiting(kv)

    if a.verify or a.refuse:
        cid = a.verify or a.refuse
        if not a.tail:
            print("--tail is required: two people at one company are two "
                  "decisions, and a company id does not say which.", file=sys.stderr)
            return 2
        row = next((r for r in rows if r["company_id"] == cid
                    and r["tail"] == a.tail), None)
        if not row:
            was = _ruled(cid, a.tail)
            print(f"no claim waiting for {cid} with tail {a.tail}"
                  + (f" - it was already {was}." if was else "."), file=sys.stderr)
            return 2
        for line in evidence(row, companies):
            print("  " + line)
        if a.refuse and not a.why:
            print("\n--why is required to refuse. A gate that refuses without a "
                  "reason cannot be audited.", file=sys.stderr)
            return 2
        if not a.write:
            what = "VERIFY" if a.verify else "REFUSE"
            print(f"\ndry run. Re-run with --write to {what} this claim.")
            return 0

        if a.verify:
            sent, why_not = (False, "--no-mail") if a.no_mail else _send(row)
            ev = employer_log.record_once(
                "claim_verified", cid, by="owner",
                source_key=f"claim:{cid}:{row['tail']}",
                domain=row["domain"], claim_tail=row["tail"],
                welcomed=sent)
            if ev is None:
                print("already verified; nothing written.")
                return 0
            print(f"\nverified {cid} ({row['domain']}).")
            print("  welcome sent." if sent else
                  f"  NO WELCOME SENT: {why_not}. They are verified either way; "
                  f"re-send by hand or they will find out by editing.")
            return 0

        employer_log.record_once(
            "claim_refused", cid, by="owner",
            source_key=f"claim:{cid}:{row['tail']}",
            domain=row["domain"], claim_tail=row["tail"], why=a.why)
        print(f"\nrefused {cid} ({row['domain']}): {a.why}")
        print("  NOTHING WAS SENT TO THEM. A refusal reason is for the log and "
              "for you; deciding whether this person hears about it is a "
              "judgement, so it stays yours.")
        return 0

    if a.company:
        rows = [r for r in rows if r["company_id"] == a.company]
    if not rows:
        print("Nothing is waiting on you. That is a real answer: every "
              "confirmed claim has been ruled, or none has been made.")
        return 0
    print(f"{len(rows)} claim(s) waiting on you, longest first:\n")
    for r in rows:
        for line in evidence(r, companies):
            print("  " + line)
        print(f"  ->  python3 scripts/verify_claims.py --verify "
              f"{r['company_id']} --tail {r['tail']} --write")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
