#!/usr/bin/env python3
"""Build the alert digest for one subscription, from the board's own history.

An alert is only worth having if "new" is true. Most job alerts re-send the
same listings because they diff a search result against nothing; this diffs
against the snapshot history that already exists, so a role appears in
exactly one digest - the first one after it showed up.

Two knobs, both the owner's:

  THRESHOLD is two things, because both matter and they are not the same.
  A role bar decides what is worth telling you about at all (quota-carrying,
  seniority, family, geography - the same vocabulary the board's own search
  speaks). A volume floor decides whether an email is worth sending: under
  it, the digest is skipped entirely rather than arriving to say "1 new
  role", which is how a daily alert teaches someone to filter it to trash.

  CADENCE decides which days run. A weekly digest looks back seven days, a
  Tuesday/Thursday one looks back to the previous send, so nothing is missed
  and nothing is repeated regardless of which cadence somebody picks.

Nothing here sends anything. It returns what WOULD be sent, so it can be
tested against real data without a mail account, and so a send can be
reviewed before it goes out.

  python scripts/digest.py --preview --quota --since 2026-08-22
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import brand  # noqa: E402

SITE = brand.SITE
NAME = brand.NAME

CADENCE_DAYS = {
    # weekday numbers as date.weekday(): Monday is 0
    "daily": {0, 1, 2, 3, 4},          # weekday mornings; a Sunday alert helps nobody
    "twice": {1, 3},                   # Tuesday and Thursday
    "weekly": {2},                     # Wednesday
}


def cadence_runs_today(cadence: str, today: dt.date) -> bool:
    return today.weekday() in CADENCE_DAYS.get(cadence, CADENCE_DAYS["weekly"])


def lookback_start(cadence: str, today: dt.date, last_sent: str | None) -> dt.date:
    """Where this digest should start looking.

    From the last send when we know it, so a missed run does not silently
    drop the roles it would have carried. Otherwise from the cadence's own
    natural window.
    """
    if last_sent:
        try:
            # the DAY AFTER the last send: roles first seen on that date were
            # in that digest, and a role must appear in exactly one email
            return dt.date.fromisoformat(last_sent) + dt.timedelta(days=1)
        except ValueError:
            pass
    return today - dt.timedelta(days={"daily": 1, "twice": 3, "weekly": 7}
                                .get(cadence, 7))


def matches(p: dict, sub: dict) -> bool:
    """The role bar: does this posting clear what the subscriber asked for?"""
    if sub.get("quota_only") and not p.get("quota_carrying"):
        return False
    fam = sub.get("family")
    if fam and p.get("family") != fam:
        return False
    sen = sub.get("seniority")
    if sen and p.get("seniority") != sen:
        return False
    sec = sub.get("sector")
    if sec and p.get("sector") != sec and not any(
            a.get("sector") == sec for a in (p.get("also") or [])):
        return False
    # Exactly what the board's own mode filter does (index.html), so a
    # preview of these settings on the board and the email that follows can
    # never disagree.
    if sub.get("work_mode") and p.get("work_mode") != sub["work_mode"]:
        return False
    states = set(sub.get("states") or [])
    if states:
        here = set(p.get("states") or [])
        if p.get("office", {}) and (p.get("office") or {}).get("state"):
            here.add(p["office"]["state"])
        if not (here & states):
            return False
    if sub.get("us_only") and p.get("is_us") is False:
        return False
    return True


def build(board: dict, sub: dict, today: dt.date | None = None) -> dict:
    """What this subscription would receive today."""
    today = today or dt.date.today()
    cadence = sub.get("cadence", "weekly")
    if not cadence_runs_today(cadence, today):
        return {"send": False, "why": f"{cadence} does not run on "
                                      f"{today.strftime('%A').lower()}"}
    start = lookback_start(cadence, today, sub.get("last_sent"))
    fresh = [p for p in board.get("postings", [])
             if p.get("first_seen") and dt.date.fromisoformat(p["first_seen"]) >= start]
    hits = [p for p in fresh if matches(p, sub)]
    floor = int(sub.get("min_count") or 1)
    if len(hits) < floor:
        return {"send": False, "roles": hits, "since": start.isoformat(),
                "why": f"{len(hits)} new, under the floor of {floor}"}
    return {"send": True, "roles": interleave(hits), "since": start.isoformat(),
            "why": f"{len(hits)} new since {start.isoformat()}"}


def interleave(hits: list[dict]) -> list[dict]:
    """Order so no company appears twice before every other has appeared once.

    Sorting by relevance alone put all 47 of one company's reqs at the top and
    the other 23 companies below the fold. A digest is a scan, not a ranking:
    the reader wants to see the SPREAD of who is hiring, and can open the board
    for the depth. Within each company, quota-carrying roles come first.
    """
    by_co: dict[str, list[dict]] = {}
    for p in hits:
        by_co.setdefault(p["company"], []).append(p)
    for roles in by_co.values():
        roles.sort(key=lambda p: (not p.get("quota_carrying"), p["title"]))
    # companies carrying a number lead, then the deepest benches
    order = sorted(by_co, key=lambda c: (not any(p.get("quota_carrying")
                                                 for p in by_co[c]),
                                         -len(by_co[c]), c))
    out, round_ = [], 0
    while len(out) < len(hits):
        for c in order:
            if round_ < len(by_co[c]):
                out.append(by_co[c][round_])
        round_ += 1
    return out


def _where(p: dict) -> str:
    bits = []
    t = p.get("territory") or {}
    if t.get("stated"):
        bits.append("territory: " + (", ".join(t.get("states") or [])
                                     or t.get("region") or ""))
    if p.get("work_mode") == "remote":
        bits.append("remote")
    elif p.get("office"):
        o = p["office"]
        bits.append("in office - " + ((o.get("city") + ", ") if o.get("city") else "")
                    + (o.get("state") or ""))
    elif p.get("work_mode") in ("onsite", "hybrid"):
        bits.append(p["work_mode"])
    elif not bits:
        bits.append("location not stated")
    else:
        bits.append("no office stated")
    return " · ".join(b for b in bits if b)


# --- the shared email shell ------------------------------------------------
#
# THE SAME SHELL AS functions/api/alerts.js, restated in Python for the same
# reason functions/_brand.js restates brand.json: a Cloudflare Worker cannot
# import this file and this script cannot import that one. The duplication is
# real and therefore guarded - selftest.py::check_mail_shell fails the build
# when the two drift, because drift here is silent: the confirmation email and
# the digest simply stop looking like the same product and nothing errors.
#
# Everything that NAMES the product is colour and type, never a pixel: the
# Penguin band, the wordmark as live text, the Beak rule as a table cell, the
# Belly plate as a hard 52px cell with a background. Only the mascot's face is
# an image. Outlook and much of Gmail block images by default, so the design
# is built around the blocked state and the face is the one optional thing.
#
# The MSO conditional is not decoration. Word does not walk a font stack: it
# takes the first family, fails to find Archivo, and renders the whole email
# in Times New Roman. The selector list must include div and p.
MAIL_FONT = "Archivo,'Helvetica Neue',Helvetica,Arial,sans-serif"
MASCOT = f"{SITE}/assets/mascot/png/head-on-the-hunt.png"


def button(href: str, label: str) -> str:
    """A call to action. Word drops padding on an inline anchor, so the button
    is a table cell with a bgcolor and the anchor inside it."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        ' style="border-collapse:collapse"><tr>'
        '<td bgcolor="#0B57C4" style="background-color:#0B57C4;padding:13px 22px">'
        f'<a href="{href}" style="display:inline-block;color:#FAF7F0;'
        f'text-decoration:none;font-weight:700;font-size:15px;'
        f'font-family:{MAIL_FONT}">{label}</a>'
        '</td></tr></table>')


def shell(preheader: str, body: str, links: list) -> str:
    foot = " &nbsp;&middot;&nbsp; ".join(
        f'<a href="{u}" style="color:#556F82;text-decoration:underline">{t}</a>'
        for t, u in (links or []))
    return f"""<!doctype html>
<html lang="en" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{NAME}</title>
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
<!--[if mso]><style type="text/css">body,table,td,a,span,div,p{{font-family:'Segoe UI',Arial,sans-serif !important}}</style><![endif]-->
<style>
 :root{{color-scheme:light only;supported-color-schemes:light only}}
 @media (prefers-color-scheme:dark){{
  .ice{{background-color:#E8F1F7!important}}.belly{{background-color:#FAF7F0!important}}
  .band{{background-color:#1F2536!important}}.plate{{background-color:#FAF7F0!important}}
  .beak{{background-color:#F5A623!important}}.ink{{color:#1F2536!important}}
  .mute,.mute a{{color:#556F82!important}}.faint{{color:#7C97AA!important}}
  .onband,.onband a{{color:#E8F1F7!important}}.onbandmute{{color:#9FB3C4!important}}}}
 [data-ogsc] .ice{{background-color:#E8F1F7!important}}
 [data-ogsc] .belly{{background-color:#FAF7F0!important}}
 [data-ogsc] .band{{background-color:#1F2536!important}}
 [data-ogsc] .plate{{background-color:#FAF7F0!important}}
 [data-ogsc] .ink{{color:#1F2536!important}}
 [data-ogsc] .mute{{color:#556F82!important}}
 [data-ogsc] .onband{{color:#E8F1F7!important}}
 [data-ogsc] .onbandmute{{color:#9FB3C4!important}}
 @media only screen and (max-width:620px){{
  .pad{{padding-left:20px!important;padding-right:20px!important}}
  .wm{{font-size:22px!important}}
  .kicker{{font-size:10px!important;letter-spacing:.05em!important}}}}
</style>
</head>
<body class="ice" bgcolor="#E8F1F7" style="margin:0;padding:0;width:100%;
 background-color:#E8F1F7;-webkit-text-size-adjust:100%">
<div class="faint" style="display:none;max-height:0;max-width:0;overflow:hidden;
 mso-hide:all;font-size:1px;line-height:1px;opacity:0;color:#E8F1F7">{preheader}</div>
<table role="presentation" class="ice" bgcolor="#E8F1F7" width="100%" cellpadding="0"
 cellspacing="0" border="0" style="width:100%;background-color:#E8F1F7;border-collapse:collapse">
<tr><td align="center" valign="top" bgcolor="#E8F1F7" style="background-color:#E8F1F7;padding:24px 0">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" align="center"
 style="width:100%;max-width:600px;border-collapse:collapse">
 <tr><td class="band pad" bgcolor="#1F2536" style="background-color:#1F2536;padding:18px 24px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
   style="border-collapse:collapse"><tr>
   <td class="plate" bgcolor="#FAF7F0" width="52" height="52" valign="middle" align="center"
    style="background-color:#FAF7F0;width:52px;height:52px;font-size:0;line-height:0;
    mso-line-height-rule:exactly"><a href="{SITE}" style="text-decoration:none"><img
    src="{MASCOT}" width="46" height="46" alt="" style="display:block;width:46px;
    height:46px;border:0;outline:none;text-decoration:none"></a></td>
   <td width="16" style="width:16px;font-size:0;line-height:0">&nbsp;</td>
   <td align="left" valign="middle" style="background-color:#1F2536">
    <div class="wm onband" style="font-family:{MAIL_FONT};font-size:25px;font-weight:800;
     letter-spacing:.02em;line-height:1.1;color:#E8F1F7"><a href="{SITE}"
     style="color:#E8F1F7;text-decoration:none">{NAME}</a></div>
    <div class="kicker onbandmute" style="padding-top:6px;font-family:{MAIL_FONT};font-size:12px;
     font-weight:600;letter-spacing:.08em;line-height:1.4;text-transform:uppercase;
     color:#9FB3C4">State &amp; local govtech sales roles</div>
   </td></tr></table>
 </td></tr>
 <tr><td class="beak" height="3" bgcolor="#F5A623" style="background-color:#F5A623;
  height:3px;line-height:3px;font-size:3px;mso-line-height-rule:exactly">&nbsp;</td></tr>
 <tr><td class="belly ink pad" align="left" valign="top" bgcolor="#FAF7F0"
  style="background-color:#FAF7F0;padding:28px 24px;font-family:{MAIL_FONT};font-size:15px;
  line-height:1.55;color:#1F2536">{body}</td></tr>
 <tr><td class="belly pad" bgcolor="#FAF7F0" style="background-color:#FAF7F0;
  padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  border="0" style="border-collapse:collapse"><tr><td height="1" bgcolor="#C9DCE8"
  style="height:1px;line-height:1px;font-size:0;background-color:#C9DCE8">&nbsp;</td>
  </tr></table></td></tr>
 <tr><td class="belly mute pad" align="left" bgcolor="#FAF7F0" style="background-color:#FAF7F0;
  padding:14px 24px 26px;font-family:{MAIL_FONT};font-size:12px;line-height:1.7;color:#556F82">
  <a href="{SITE}" style="color:#0B57C4;text-decoration:none;font-weight:700">{NAME}</a>
  &mdash; every open sales role at state and local government technology companies.{
  ("<br>" + foot) if foot else ""}<br>
  <span class="faint" style="color:#7C97AA">It&rsquo;s tough SLEDing out there.</span>
 </td></tr>
</table></td></tr></table></body></html>"""


def render(digest: dict, sub: dict, board: dict) -> tuple[str, str, str]:
    """Return (subject, text, html). Plain text is written first and is not a
    fallback: plenty of people read mail as text, and a digest that only makes
    sense in HTML is a digest that fails silently for them."""
    roles = digest["roles"]
    n = len(roles)
    quota = sum(1 for p in roles if p.get("quota_carrying"))
    subject = (f"{n} new govtech role{'s' if n != 1 else ''}"
               + (f", {quota} carrying a number" if quota else ""))
    lines = [subject, "=" * len(subject), ""]
    for p in roles[:40]:
        lines.append(f"{p['title']}")
        lines.append(f"  {p['company']} · {p.get('sector','')} · {_where(p)}")
        lines.append("  " + SITE + "/?role="
                     + urllib.parse.quote(str(p["id"]), safe=""))
        lines.append("")
    if n > 40:
        lines.append(f"...and {n - 40} more on the board.")
        lines.append("")
    lines.append(f"Since {digest['since']}. Every role here appeared on the board "
                 f"after that date, so nothing repeats between digests.")
    lines.append(f"Change what you get or stop these: {SITE}/alerts?t={sub.get('token','')}")
    text = "\n".join(lines)

    esc = (lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;"))
    PILL = ('<span style="background:#e9f7ef;color:#0B57C4;font-size:11px;'
            'padding:2px 8px;border-radius:12px;margin-left:8px">'
            'carries a number</span>')

    def card(p: dict) -> str:
        href = SITE + "/?role=" + urllib.parse.quote(str(p["id"]), safe="")
        pill = PILL if p.get("quota_carrying") else ""
        return (
            '<tr><td style="padding:14px 0;border-bottom:1px solid #C9DCE8">'
            f'<a href="{href}" style="color:#1F2536;text-decoration:none;'
            f'font-weight:600;font-size:15px">{esc(p["title"])}</a>{pill}'
            '<div style="color:#556F82;font-size:13px;margin-top:3px">'
            f'{esc(p["company"])} · {esc(p.get("sector", ""))} · {esc(_where(p))}'
            '</div></td></tr>')

    cards = "".join(card(p) for p in roles[:40])
    body = (
        f'<div style="font-size:20px;font-weight:800;letter-spacing:-.015em;'
        f'line-height:1.3">{esc(subject)}</div>'
        f'<div style="color:#556F82;font-size:13px;padding-top:4px">On the board '
        f"since {esc(digest['since'])}. Nothing here has been sent to you before."
        '</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        f' border="0" style="border-collapse:collapse;margin-top:10px">{cards}</table>'
        + (f'<div style="color:#556F82;font-size:13px;padding-top:12px">and {n-40} '
           f'more on the board</div>' if n > 40 else ''))

    tok = esc(sub.get("token", ""))
    html = shell(
        f"{n} new role(s) on the board since {esc(digest['since'])}.",
        body,
        [("Change what you get", f"{SITE}/alerts?t={tok}"),
         ("Stop these emails", f"{SITE}/alerts?t={tok}&amp;stop=1")])

    return subject, text, html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--cadence", default="daily", choices=list(CADENCE_DAYS))
    ap.add_argument("--quota", action="store_true", help="quota-carrying only")
    ap.add_argument("--family")
    ap.add_argument("--seniority")
    ap.add_argument("--sector")
    ap.add_argument("--states", help="comma separated, e.g. NY,NJ,CT")
    ap.add_argument("--work-mode", choices=["remote", "onsite"])
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--since", metavar="LAST_SENT",
                    help="date of the previous digest; roles newer than it")
    ap.add_argument("--today", help="pretend today is this date")
    a = ap.parse_args()

    board = json.loads((DATA / "board.json").read_text())
    sub = {"cadence": a.cadence, "quota_only": a.quota, "family": a.family,
           "seniority": a.seniority, "sector": a.sector,
           "states": [s.strip().upper() for s in (a.states or "").split(",") if s.strip()],
           "work_mode": a.work_mode, "min_count": a.min_count,
           "last_sent": a.since, "token": "PREVIEW"}
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    d = build(board, sub, today)
    if not d["send"]:
        print(f"nothing would be sent: {d['why']}")
        return 0
    subject, text, _html = render(d, sub, board)
    print(f"SUBJECT: {subject}\n")
    print(text if a.preview else f"{len(d['roles'])} roles ({d['why']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
