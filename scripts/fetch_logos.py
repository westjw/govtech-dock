#!/usr/bin/env python3
"""Fetch a logo for each company, once, into assets/logos/.

Hotlinking a logo service would be easier and is the wrong trade: every
visitor's browsing would be reported to that service, on a board people use
to look for a job quietly from their current desk. So the images are fetched
here, committed, and served from our own origin. No runtime third party, no
key, and the board still renders with the network off.

Order of attempts per company, stopping at the first that yields a real
image: the site's own apple-touch-icon (biggest and cleanest), its declared
icon link, then /favicon.ico. Nothing is invented - a company with no
reachable icon simply keeps its initials tile, which is a fine fallback and
is already what the board draws.

  python scripts/fetch_logos.py [--limit 400] [--all] [--stats]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "assets" / "logos"
LOG = DATA / "logo_log.json"
MIN_BYTES = 120          # anything smaller is a 1x1 or an error page
MAX_BYTES = 220_000      # a logo, not a hero image

ICON_RE = re.compile(
    r'<link[^>]+rel=["\'][^"\']*(?:apple-touch-icon|shortcut icon|icon)[^"\']*["\'][^>]*>',
    re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
SIZES_RE = re.compile(r'sizes=["\'](\d+)', re.I)


def looks_like_image(blob: bytes) -> str | None:
    """Return an extension when the bytes really are an image."""
    if len(blob) < MIN_BYTES or len(blob) > MAX_BYTES:
        return None
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if blob[:3] == b"\xff\xd8\xff":
        return "jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"
    if blob[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if blob.lstrip()[:5].lower() == b"<?xml" or b"<svg" in blob[:400].lower():
        return "svg"
    return None


OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)[^"\']*["\'][^>]*>', re.I)
CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)
# Paths a site serves an icon from even when it declares none. Cheap to try
# and they account for most of the "no readable icon" pile: a site with no
# <link rel=icon> very often still answers on the conventional filename.
BLIND_PATHS = ["/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
               "/favicon.ico", "/favicon.png", "/static/favicon.ico",
               "/assets/favicon.ico", "/images/favicon.ico", "/logo.png",
               "/assets/logo.png", "/images/logo.png", "/static/logo.png"]


def candidates(site: str, html: str) -> list[str]:
    """Icon URLs to try, best first."""
    out: list[tuple[int, str]] = []
    for tag in ICON_RE.findall(html or ""):
        m = HREF_RE.search(tag)
        if not m:
            continue
        href = m.group(1).strip()
        if href.startswith("data:"):
            continue
        size = int(SIZES_RE.search(tag).group(1)) if SIZES_RE.search(tag) else 0
        if "apple-touch" in tag.lower():
            size = max(size, 180)
        out.append((size, urllib.parse.urljoin(site, href)))
    # og:image is the card image a site shows when shared - very often the
    # logo, and present on sites that declare no icon at all
    for tag in OG_RE.findall(html or ""):
        m = CONTENT_RE.search(tag)
        if m and not m.group(1).startswith("data:"):
            out.append((60, urllib.parse.urljoin(site, m.group(1).strip())))
    out.sort(reverse=True)
    urls = [u for _, u in out]
    urls += [urllib.parse.urljoin(site, path) for path in BLIND_PATHS]
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:12]


def logo_path(cid: str, ext: str) -> pathlib.Path:
    """Where this logo is allowed to land, or nowhere.

    OUT / f"{cid}.{ext}" only lands in assets/logos when cid is a bare slug,
    and an id reaches here from wherever companies.json got it - including an
    outside submission. Resolving the joined path and insisting the parent is
    still OUT is the layer that holds without trusting anything upstream,
    which is the point of having it as well as the checks upstream.
    """
    dest = (OUT / f"{cid}.{ext}").resolve()
    if dest.parent != OUT.resolve():
        raise ValueError(f"{cid!r} is not a company id, it is a path")
    return dest


def fetch_one(row: tuple[str, str]) -> tuple[str, str | None, str]:
    cid, site = row
    import requests
    sys.path.insert(0, str(ROOT / "scripts"))
    import ats
    html, base = "", site
    for attempt in (site, site.replace("://www.", "://") if "://www." in site
                    else site.replace("://", "://www.")):
        try:
            r = requests.get(attempt, headers=ats.UA, timeout=20,
                             allow_redirects=True)
            html = r.text if r.ok else ""
            base = r.url or attempt
            break
        except Exception as exc:
            # a certificate that fails on www often passes on the apex (and
            # the other way round); try the sibling before giving up
            last = f"site unreachable ({type(exc).__name__})"
    else:
        return cid, None, last
    for url in candidates(base, html):
        try:
            ir = requests.get(url, headers=ats.UA, timeout=12)
            if not ir.ok:
                continue
            ext = looks_like_image(ir.content)
            if not ext:
                continue
            try:
                dest = logo_path(cid, ext)
            except ValueError as exc:
                return cid, None, str(exc)
            dest.write_bytes(ir.content)
            return cid, ext, url
        except Exception:
            continue
    return cid, None, "no readable icon on the site"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--all", action="store_true",
                    help="every company, not just the ones with open roles")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--retry", action="store_true",
                    help="re-attempt companies that failed before. A failure "
                         "is a fact about one attempt, not about the company.")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    hiring = {o["id"]: o.get("open_roles", 0)
              for o in board.get("organizations", [])}
    log = json.loads(LOG.read_text()) if LOG.exists() else {}
    have = {f.stem for f in OUT.glob("*.*")}

    if a.stats:
        print(f"{len(have)} logos on file, {len(log)} companies attempted")
        print(f"  of the {sum(1 for c in companies if hiring.get(c['id'])) } hiring, "
              f"{sum(1 for c in companies if hiring.get(c['id']) and c['id'] in have)} "
              f"have one")
        return 0

    # Companies with open roles first: they are the ones actually on screen.
    todo = [c for c in companies
            if c.get("website") and c["id"] not in have
            and (a.retry or c["id"] not in log)]
    todo.sort(key=lambda c: -hiring.get(c["id"], 0))
    if not a.all:
        todo = [c for c in todo if hiring.get(c["id"], 0) > 0]
    todo = todo[:a.limit]
    if not todo:
        print("nothing to fetch")
        return 0

    print(f"fetching {len(todo)} logos with {a.workers} workers...", flush=True)
    got = 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        rows = [(c["id"], c["website"]) for c in todo]
        for i, (cid, ext, note) in enumerate(ex.map(fetch_one, rows), 1):
            log[cid] = {"ok": bool(ext), "note": note[:120]}
            if ext:
                got += 1
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}, {got} found", flush=True)
    LOG.write_text(json.dumps(log, indent=1))
    total = sum(f.stat().st_size for f in OUT.glob("*.*"))
    print(f"{got} of {len(todo)} found · {len(list(OUT.glob('*.*')))} on file · "
          f"{total/1024:.0f} KB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
