#!/usr/bin/env python3
"""Turn the latest refresh diff into an alert about companies that just opened
AE reqs - the warm doors. Written for the weekly Action, but runs anywhere.

  python scripts/alert.py /tmp/body.md   # writes body, prints "alert" or "none"

Prints "alert" and writes a markdown body when there is something worth an
issue; prints "none" and writes nothing when there isn't. Always exits 0, so
the caller branches on the printed word rather than on an exit code.

Deliberately quiet: only transitions *into* "Yes" are alert-worthy. A company
going Yes -> None found is a door closing, which is visible in the diff on the
site and doesn't need to interrupt anyone's Monday.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))


def new_ae_openings(diff: dict) -> list[dict]:
    """Companies whose status became "Yes" since the previous snapshot."""
    if not diff.get("previous"):
        # First snapshot: everything looks new. Alerting here would mean a
        # 50-line issue that says nothing about what changed.
        return []
    return [c for c in diff.get("changes", [])
            if c.get("to") == "Yes" and c.get("from") != "Yes"]


def render(hits: list[dict], companies: list[dict], date: str, previous: str) -> str:
    by_id = {c["id"]: c for c in companies}
    lines = [f"Since the {previous} run, **{len(hits)} "
             f"{'company' if len(hits) == 1 else 'companies'}** opened AE reqs.",
             ""]
    for h in hits:
        comp = by_id.get(h["id"], {})
        hiring = comp.get("hiring", {})
        where = " · ".join(x for x in (comp.get("location"), comp.get("sector"),
                                       comp.get("category")) if x)
        lines.append(f"### {h['company']}")
        if where:
            lines.append(f"*{where}*")
        if comp.get("description"):
            lines.append(f"\n{comp['description']}")
        lines.append(f"\n- was **{h['from']}**, now **Yes**")
        for role in hiring.get("roles", [])[:5]:
            title = role.get("title", "").strip() or "(untitled role)"
            loc = f" — {role['location']}" if role.get("location") else ""
            url = role.get("url", "")
            lines.append(f"- [{title}{loc}]({url})" if url else f"- {title}{loc}")
        if "[page scan - verify]" in (hiring.get("note") or ""):
            lines.append("- ⚠️ found by page scan, not a structured job board — "
                         "**verify before acting on this one**")
        if comp.get("website"):
            lines.append(f"- {comp['website']}")
        lines.append("")
    lines.append("---")
    lines.append(f"From the {date} refresh. Close this once you've worked the list.")
    return "\n".join(lines)


# ------------------------------------------------------------------ email --
#
# ONE EMAIL, IN THE BRAND. This went out as a GitHub issue assigned to the
# owner, and GitHub mails twice for that - once for the issue, once for the
# assignment - in its own plain layout (owner, 2026-10-20: "why 2 emails?
# logos and color?"). The email is built from digest.shell(), the same shell
# the subscriber digests use, so it is checked by the same mail guards.

# Gmail clips a message over 102KB behind "[Message clipped]", and 59
# companies in full cards is over it. So the first FULL_CARDS get a card and
# the rest a line each; the whole list is always in the plain text.
FULL_CARDS = 30
MAIL_LIMIT = 95_000
# what a mail client can draw: SVG is stripped by Gmail and ICO by most
LOGO_EXTS = ("png", "jpg", "jpeg", "gif", "webp")


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def logo_url(cid: str) -> str | None:
    """The company's logo where a mail client can load it, or None."""
    import digest
    for ext in LOGO_EXTS:
        if (ROOT / "assets" / "logos" / f"{cid}.{ext}").exists():
            return f"{digest.MAIL_ASSETS}/assets/logos/{cid}.{ext}"
    return None


# A WHITE LOGO ON THE WHITE PLATE IS AN EMPTY BOX. The site measures each
# logo in the browser (index.html lightMark) and gives a light one a dark
# plate; a mail client runs no script, and the refresh carries no imaging
# library. The fetcher kept where each logo came from, and a logo published
# as white says so in its own file name ("CrowdStrike-logo-primary-white.png").
# Read narrowly: "light" is NOT a white logo ("favicon-light" is the dark mark
# for light backgrounds), and "white-bg" is a white BACKGROUND.
_WHITE_MARK = re.compile(r"(?i)(?<![a-z])(white|reverse[d]?|knockout|negative|inverse)"
                         r"(?![a-z])(?![-_ ]?(?:bg|background|bkgrnd))")
_LOGO_LOG = None


def light_logo(cid: str) -> bool:
    """The logo was published as a white or reversed mark."""
    global _LOGO_LOG
    if _LOGO_LOG is None:
        try:
            _LOGO_LOG = json.loads((DATA / "logo_log.json").read_text())
        except (OSError, json.JSONDecodeError):
            _LOGO_LOG = {}
    rec = _LOGO_LOG.get(cid) or {}
    src = str(rec.get("url") or rec.get("note") or "")
    name = src.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return bool(_WHITE_MARK.search(name))


def _tile(name: str, cid: str) -> str:
    """40px logo plate, or the company's initials on Penguin when there is no
    logo a mail client can draw - the same fallback the site's tiles use."""
    url = logo_url(cid)
    if url:
        plate = "#1F2536" if light_logo(cid) else "#FFFFFF"
        return (f'<img src="{_esc(url)}" width="40" height="40" alt="" '
                'style="display:block;width:40px;height:40px;border:0;'
                f'border-radius:8px;object-fit:contain;background:{plate}" bgcolor="{plate}">')
    ini = "".join(w[0] for w in str(name).split()[:2] if w[:1].isalnum()).upper() or "?"
    return ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse"><tr><td width="40" height="40" align="center" '
            'valign="middle" bgcolor="#1F2536" style="width:40px;height:40px;'
            'background-color:#1F2536;border-radius:8px;color:#E8F1F7;font-size:14px;'
            f'font-weight:800;line-height:40px">{_esc(ini[:2])}</td></tr></table>')


# what the status a company held before reads as, in words
WAS = {"None found": "wasn't hiring", "Unknown": "new to the board",
       "Sales (non-AE)": "only non-AE sales roles"}


def _day(iso: str) -> str:
    """'2026-10-20' -> 'Oct 20'; anything else as it came."""
    import datetime as dt
    try:
        d = dt.date.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{d:%b} {d.day}"


def _scanned(comp: dict) -> bool:
    return "[page scan - verify]" in ((comp.get("hiring") or {}).get("note") or "")


def render_email(hits: list[dict], companies: list[dict], date: str,
                 previous: str) -> tuple[str, str, str]:
    """(subject, text, html) for the owner's AE alert."""
    import brand
    import digest
    site = brand.SITE
    by_id = {c["id"]: c for c in companies}
    n = len(hits)
    who = "company" if n == 1 else "companies"
    subject = f"{n} {who} opened AE roles \u00b7 {_day(date)}"
    # WARMEST FIRST: openings read off a real job board before the ones a
    # page scan guessed at, and more roles before fewer. The plain text keeps
    # the refresh's own order, which is the issue's.
    nroles = lambda h: len([r for r in ((by_id.get(h["id"]) or {}).get("hiring") or {}).get("roles", [])
                            if r.get("title")])
    hits = sorted(hits, key=lambda h: (_scanned(by_id.get(h["id"]) or {}), -nroles(h)))

    text = render(hits, companies, date, previous).replace(
        "Close this once you've worked the list.", f"Board: {site}")

    def roles_of(comp):
        return [r for r in (comp.get("hiring") or {}).get("roles", []) if r.get("title")]

    def card(h) -> str:
        comp = by_id.get(h["id"], {})
        where = " \u00b7 ".join(x for x in (comp.get("location"), comp.get("sector"),
                                             comp.get("category")) if x)
        page = f"{site}/?co={urllib.parse.quote(h['id'], safe='')}"
        roles = roles_of(comp)
        links = "".join(
            '<div style="padding-top:5px;font-size:14px;line-height:1.4">'
            f'<a href="{_esc(r.get("url") or page)}" style="color:#0B57C4;text-decoration:none;'
            f'font-weight:600">{_esc(r["title"].strip())}</a>'
            + (f'<span style="color:#556F82"> \u2014 {_esc(r["location"])}</span>'
               if r.get("location") else "") + '</div>' for r in roles[:3])
        more = (f'<div style="padding-top:5px;font-size:13px"><a href="{_esc(page)}" '
                f'style="color:#556F82">+{len(roles) - 3} more on the board</a></div>'
                if len(roles) > 3 else "")
        warn = ('<div style="padding-top:6px;font-size:12px;color:#C1341F;font-weight:700">'
                'Found by a page scan, not a job board \u2014 check before you act</div>'
                if _scanned(comp) else "")
        return (
            '<tr><td style="padding:16px 0;border-bottom:1px solid #C9DCE8">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse"><tr>'
            f'<td width="40" valign="top" style="width:40px">{_tile(h["company"], h["id"])}</td>'
            '<td width="14" style="width:14px;font-size:0">&nbsp;</td><td valign="top">'
            f'<a href="{_esc(page)}" style="color:#1F2536;text-decoration:none;font-weight:800;'
            f'font-size:16px">{_esc(h["company"])}</a>'
            + (f'<div style="color:#556F82;font-size:12px;padding-top:2px">{_esc(where)}</div>'
               if where else "")
            + '<div style="padding-top:6px"><span style="background-color:#FDF1D6;color:#1F2536;'
              'font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px">'
            f'{_esc(WAS.get(h.get("from"), "was " + str(h.get("from"))))} \u2192 now hiring AEs</span></div>'
            + links + more + warn + '</td></tr></table></td></tr>')

    def line(h) -> str:
        comp = by_id.get(h["id"], {})
        page = f"{site}/?co={urllib.parse.quote(h['id'], safe='')}"
        k = len(roles_of(comp))
        return ('<tr><td style="padding:7px 0;border-bottom:1px solid #C9DCE8;font-size:14px">'
                f'<a href="{_esc(page)}" style="color:#1F2536;font-weight:700;text-decoration:none">'
                f'{_esc(h["company"])}</a><span style="color:#556F82"> \u00b7 '
                f'{_esc(comp.get("sector") or "")} \u00b7 {k} role{"s" if k != 1 else ""}</span>'
                '</td></tr>')

    def build(full: int) -> str:
        cards = "".join(card(h) for h in hits[:full])
        rest = hits[full:]
        tail = ("" if not rest else
                '<div style="font-size:12px;font-weight:700;letter-spacing:.06em;'
                'text-transform:uppercase;color:#556F82;padding-top:22px">'
                f'And {len(rest)} more</div><table role="presentation" width="100%" '
                'cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">'
                + "".join(line(h) for h in rest) + "</table>")
        body = (
            f'<div style="font-size:22px;font-weight:800;letter-spacing:-.015em;line-height:1.25">'
            f'{n} {who} opened AE roles</div>'
            f'<div style="color:#556F82;font-size:13px;padding-top:4px">Since {_esc(_day(previous))} '
            f'\u00b7 {_esc(_day(date))} refresh. Warm doors: none of these was hiring '
            'account executives last time, and each is now.</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;margin-top:8px">{cards}</table>{tail}'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;margin-top:24px"><tr><td bgcolor="#0B57C4" '
            'style="background-color:#0B57C4;border-radius:8px;padding:11px 20px">'
            f'<a href="{_esc(site)}/?tab=companies" style="color:#FFFFFF;font-weight:700;'
            'font-size:14px;text-decoration:none">Open the board</a></td></tr></table>')
        return digest.shell(f"{n} {who} opened AE roles since {_day(previous)}.", body,
                            [("The board", site)])

    full = min(FULL_CARDS, n)
    html = build(full)
    while len(html.encode()) > MAIL_LIMIT and full > 5:
        full -= 5
        html = build(full)
    return subject, text, html


def send_email() -> int:
    """Mail tonight's alert to the owner. 0 sent, 1 failed, 2 not set up.

    The address is a GitHub secret (ALERT_TO), never a tracked file: the
    repository is public. Not set up, or a failed send, and the workflow
    falls back to the issue - an alert is never simply lost."""
    import os
    key, to = os.environ.get("RESEND_KEY"), os.environ.get("ALERT_TO")
    if not key or not to:
        print("email not set up (RESEND_KEY and ALERT_TO)")
        return 2
    diff = json.loads((DATA / "latest_diff.json").read_text())
    hits = new_ae_openings(diff)
    if not hits:
        print("none")
        return 0
    companies = json.loads((DATA / "companies.json").read_text())
    subject, text, html = render_email(hits, companies, diff.get("date", "?"),
                                       diff.get("previous", "?"))
    import send_digests
    ok = send_digests.send_mail(key, to, subject, text, html)
    print("emailed" if ok else "send failed")
    return 0 if ok else 1


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Tonight's new AE openings, as an issue body or an email.")
    ap.add_argument("out", nargs="?", help="write the markdown body here")
    ap.add_argument("--email", action="store_true",
                    help="mail the owner (RESEND_KEY + ALERT_TO); 0 sent, 1 failed, 2 not set up")
    ap.add_argument("--preview", metavar="HTML", help="write tonight's email to this file")
    a = ap.parse_args()
    if a.email:
        return send_email()
    if a.preview:
        # the email as it would go out tonight, written to a file to open
        out = pathlib.Path(a.preview)
        diff = json.loads((DATA / "latest_diff.json").read_text())
        hits = new_ae_openings(diff)
        companies = json.loads((DATA / "companies.json").read_text())
        subject, _, html = render_email(hits, companies, diff.get("date", "?"),
                                        diff.get("previous", "?"))
        out.write_text(html)
        print(f"{subject} | {len(html.encode()):,} bytes | {len(hits)} companies")
        return 0
    out = pathlib.Path(a.out) if a.out else None
    diff = json.loads((DATA / "latest_diff.json").read_text())
    hits = new_ae_openings(diff)
    if not hits:
        print("none")
        return 0
    companies = json.loads((DATA / "companies.json").read_text())
    body = render(hits, companies, diff.get("date", "?"), diff.get("previous", "?"))
    if out:
        out.write_text(body)
    else:
        print(body, file=sys.stderr)
    print("alert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
