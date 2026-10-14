#!/usr/bin/env python3
"""Print a page's visible text, the way the crawler reads it.

    python3 scripts/read_page.py https://example.org/exhibitors
    python3 scripts/read_page.py https://example.org/ --links      # its links too
    python3 scripts/read_page.py https://example.org/ --max 20000

FOR THE RESEARCH AGENTS, so a research pass reads the web the way the nightly
crawl does: through ats._page, which asks robots.txt at every redirect hop and
names this crawler (owner's ruling, 2026-10-09). The card research of
2026-08-23 used curl with a Chrome user agent, which the identity rule now
forbids, and WebFetch, which is somebody else's crawler. An agent that needs a
page runs this instead. It writes nothing but to stdout.

A page the crawler may not read says so and exits 3; a page that did not
answer says why and exits 2. Both are facts to report, not to work around.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                                      # noqa: E402
import fetch_profiles as fp                                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--links", action="store_true", help="list the page's links after the text")
    ap.add_argument("--max", type=int, default=12000, help="characters of text to print")
    a = ap.parse_args()
    try:
        resp = ats._page(a.url)
    except ats.RobotsRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:                                    # noqa: BLE001
        print(f"UNREAD: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2
    body = getattr(resp, "text", "") or ""
    final = getattr(resp, "url", "") or a.url
    text = fp.text_of(body)
    # THE TITLE, which text_of drops with the rest of <head>. It is often the
    # one place a site spells its own name out ("AAAE - American Association
    # of Airport Executives"), and the identity rule turns on that.
    import html as html_lib
    import re
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = re.sub(r"\s+", " ", html_lib.unescape(m.group(1))).strip()[:200] if m else ""
    print(f"URL: {final}\nTITLE: {title}\nCHARS: {len(text)}\n")
    print(text[:a.max])
    if len(text) > a.max:
        print(f"\n[... {len(text) - a.max} more characters]")
    if a.links:
        # EVERY LINK, OFF-SITE FIRST. fetch_profiles.links keeps same-site
        # links only, which is right for a company profile and wrong here:
        # an exhibitor list sits on a2z, MapYourShow or the show's own domain,
        # and the pilot read of ten conference sites saw the button text
        # ("LIVE FLOOR PLAN AND EXHIBITOR LIST") and never its address.
        import html as html_lib
        import re
        import urllib.parse as up
        host = up.urlsplit(final).netloc.lower()
        seen, offsite, onsite = set(), [], []
        for m in re.finditer(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', body, re.I | re.S):
            href = up.urljoin(final, html_lib.unescape(m.group(1)).strip())
            if not href.startswith("http") or href in seen:
                continue
            seen.add(href)
            text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()[:80]
            (onsite if up.urlsplit(href).netloc.lower() == host else offsite).append(f"  {href}  [{text}]")
        print(f"\nLINKS OFF THIS SITE ({len(offsite)}):")
        print("\n".join(offsite[:200]))
        print(f"\nLINKS ON THIS SITE ({len(onsite)}):")
        print("\n".join(onsite[:300]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
