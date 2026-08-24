#!/usr/bin/env python3
"""Fetch a logo for each company, once, into assets/logos/.

Hotlinking a logo service would be easier and is the wrong trade: every
visitor's browsing would be reported to that service, on a board people use
to look for a job quietly from their current desk. So the images are fetched
here, committed, and served from our own origin. No runtime third party, no
key, and the board still renders with the network off.

Order of attempts per company, stopping at the first that yields a real
image: the site's own apple-touch-icon (biggest and cleanest), its declared
icon link, the icons its web app manifest names, an image the markup itself
calls a logo, og:image, then a short list of conventional paths. Nothing is
invented - a company with no reachable icon simply keeps its initials tile,
which is a fine fallback and is already what the board draws.

  python scripts/fetch_logos.py [--limit 400] [--all] [--stats]

Two rules this file exists to hold:

- **Only bytes that really are an image land on disk.** An error page saved
  as company.png renders as a broken square forever, and nobody goes back to
  check. `sniff()` reads magic bytes and refuses anything served as HTML.
- **Only the company's own origin.** Not a logo API, and not a picture of
  someone else's brand scraped off a customer wall - see `markup_logos`.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import html as htmllib
import json
import pathlib
import re
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "assets" / "logos"
# A company whose website is still only a PROPOSAL gets its logo parked here
# instead. build_board.py globs assets/logos/*.* and never descends, so a file
# in this folder is not live: it goes live when the owner accepts the website
# and the file is moved up one level. Binding a logo to an unconfirmed website
# is exactly how the wrong company's mark would end up on a card permanently.
PENDING = OUT / "pending"
LOG = DATA / "logo_log.json"
PROPOSED_SITES = DATA / "proposed_websites.json"
MIN_BYTES = 120          # anything smaller is a 1x1 or an error page
MAX_BYTES = 220_000      # a logo, not a hero image

ICON_RE = re.compile(
    r'<link[^>]+rel=["\'][^"\']*(?:apple-touch-icon|shortcut icon|icon)[^"\']*["\'][^>]*>',
    re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
SIZES_RE = re.compile(r'sizes=["\'](\d+)', re.I)
MANIFEST_RE = re.compile(
    r'<link[^>]+rel=["\'][^"\']*manifest[^"\']*["\'][^>]*>', re.I)

# Leading noise an SVG file is allowed to carry before its root element: the
# XML declaration, a doctype, comments, whitespace. Stripping these is what
# lets us insist the FIRST tag is <svg, rather than searching for "<svg"
# anywhere in the blob - which would happily accept an HTML error page that
# has an inline icon in its header.
SVG_LEAD_RE = re.compile(rb"^(?:\s+|<\?xml[^>]*\?>|<!DOCTYPE[^>]*>|<!--.*?-->)+",
                         re.I | re.S)


def sniff(blob: bytes, ctype: str = "") -> tuple[str | None, str]:
    """Return (extension, reason) - the extension only when the bytes really
    are an image. The reason is for the log, so a refusal is a recorded fact
    rather than a silent skip."""
    if ctype and "html" in ctype.split(";")[0].lower():
        return None, "served as html"
    if len(blob) < MIN_BYTES:
        return None, f"too small ({len(blob)}b)"
    if len(blob) > MAX_BYTES:
        return None, f"too big ({len(blob)//1024}KB)"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "png"
    if blob[:3] == b"\xff\xd8\xff":
        return "jpg", "jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp", "webp"
    if blob[:4] == b"\x00\x00\x01\x00":
        return "ico", "ico"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "gif"
    body = SVG_LEAD_RE.sub(b"", blob, count=1).lstrip()
    if body[:4].lower() == b"<svg":
        return "svg", "svg"
    return None, "not an image"


def looks_like_image(blob: bytes) -> str | None:
    """Back-compatible shim: the ext, or None. Kept because it is the name
    the rule is known by."""
    return sniff(blob)[0]


OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)[^"\']*["\'][^>]*>', re.I)
CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)
# Paths a site serves an icon from even when it declares none. Cheap to try
# and they account for most of the "no readable icon" pile: a site with no
# <link rel=icon> very often still answers on the conventional filename.
BLIND_PATHS = ["/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
               "/favicon.ico", "/favicon.png", "/favicon.svg",
               "/favicon-32x32.png", "/android-chrome-192x192.png",
               "/static/favicon.ico", "/assets/favicon.ico",
               "/images/favicon.ico", "/logo.png", "/logo.svg",
               "/assets/logo.png", "/assets/logo.svg", "/images/logo.png",
               "/images/logo.svg", "/static/logo.png",
               "/assets/images/logo.png", "/img/logo.png", "/img/logo.svg"]

# Any quoted or url()-wrapped reference to an image file. One regex covers
# src=, href=, content=, data-src=, srcset= and CSS url(), which is the point:
# the logo is referenced by a different attribute on every site.
ASSET_RE = re.compile(
    r'''["'(]\s*((?:https?:)?/{0,2}[^"'()\s,]+?\.(?:svg|png|webp|jpe?g))'''
    r'''(?:\?[^"'()\s,]*)?\s*["')]''', re.I)

# A page full of logos is usually a page full of OTHER PEOPLE'S logos - the
# customer wall, the integration grid, the award badges. Picking one of those
# would put a stranger's brand on a company's card and look entirely correct
# while doing it, which is the failure this list exists to prevent.
NOT_OURS = ("client", "customer", "partner", "sponsor", "award", "badge",
            "press", "media-kit", "mediakit", "testimonial", "review",
            "capterra", "g2crowd", "gartner", "forrester", "trustpilot",
            "app-store", "appstore", "google-play", "googleplay", "app_store",
            "linkedin", "twitter", "facebook", "instagram", "youtube", "x-logo",
            "placeholder", "logo-cloud", "logocloud", "logo-strip",
            "logo-carousel", "logo-slider", "trusted-by", "trustedby",
            "integration", "certification", "soc2", "hipaa", "sprite",
            "microsoft", "salesforce", "aws", "azure", "google-cloud")

WORD_RE = re.compile(r"[a-z0-9]+")

# Enough of the suffix space to find the brand label in a hostname. A real
# public-suffix list is a dependency, and this only has to be right enough to
# tell "geocomm.com" from "eunasolutions.com".
SUFFIXES = {"com", "net", "org", "io", "ai", "co", "us", "uk", "ca", "de",
            "fr", "eu", "info", "biz", "app", "dev", "tech", "cloud", "gov",
            "edu", "me", "tv", "aero", "city", "energy", "solutions", "inc",
            "online", "site", "xyz", "ie", "au", "nz", "se", "nl", "dk"}


def host_brand(host: str) -> str:
    """The brand label of a hostname: geo-comm.com -> geocomm."""
    labels = [l for l in (host or "").lower().split(".") if l and l != "www"]
    # Pop only trailing SUFFIX labels - any two-letter label is a ccTLD for
    # our purposes. Popping short labels generally would eat the brand out of
    # us.foo.com and leave "us".
    while len(labels) > 1 and (len(labels[-1]) == 2 or labels[-1] in SUFFIXES):
        labels.pop()
    return "".join(WORD_RE.findall(labels[-1])) if labels else ""


def same_brand(start: str, final: str, cid: str, name: str) -> bool:
    """Is the page we landed on still this company's?

    thecitybase.com now redirects to eunasolutions.com - Euna bought CityBase.
    Following that and keeping the icon would put the acquirer's mark on the
    acquired company's card, and it would look completely correct while being
    wrong, which is the one kind of error this dataset cannot absorb. A rename
    the brand survives (geo-comm.com -> geocomm.com, rehrig.com ->
    rehrigpacific.com) still matches and is still taken.
    """
    a, b = host_brand(urllib.parse.urlsplit(start).hostname or ""), \
        host_brand(urllib.parse.urlsplit(final).hostname or "")
    if not b or a == b:
        return True
    if a and (a in b or b in a):
        return True
    return any(t in b or b in t
               for t in brand_tokens(cid, name, start) if len(t) >= 5)


def brand_tokens(cid: str, name: str, site: str) -> set[str]:
    """Words that would appear in this company's own logo filename."""
    toks = set()
    host = urllib.parse.urlsplit(site).hostname or ""
    label = host.replace("www.", "").split(".")[0]
    if len(label) >= 3:
        toks.add(label.lower())
    for src in (cid.replace("-", " "), name or ""):
        for w in WORD_RE.findall(src.lower()):
            if len(w) >= 4 and w not in ("inc", "llc", "corp", "group",
                                         "solutions", "systems", "software",
                                         "technologies", "technology"):
                toks.add(w)
    return toks


def markup_logos(base: str, page: str, cid: str, name: str,
                 site: str) -> list[tuple[int, str]]:
    """Images the page's own markup calls a logo, best first.

    The favicon path misses these entirely, and on a WordPress or a modern
    JS site the header logo is very often the ONLY image of the brand on the
    origin - there is no /favicon.png to find. Two guards keep it honest: the
    filename must look like this company's own (or sit high enough in the
    document to be the header), and it must not match NOT_OURS.
    """
    toks = brand_tokens(cid, name, site)
    n = max(len(page), 1)
    out: list[tuple[int, str]] = []
    seen = set()
    for m in ASSET_RE.finditer(page):
        raw = htmllib.unescape(m.group(1)).strip()
        url = urllib.parse.urljoin(base, raw)
        if url in seen:
            continue
        seen.add(url)
        path = urllib.parse.urlsplit(url).path.lower()
        if "logo" not in path:
            continue
        if any(bad in path for bad in NOT_OURS):
            continue
        stem = path.rsplit("/", 1)[-1]
        flat = "".join(WORD_RE.findall(stem))
        mine = any(t in flat for t in toks)
        # Position is the fallback signal: the header renders first, so a
        # logo in the first fifth of the document is the site's own far more
        # often than one three screens down in a customer strip.
        where = m.start() / n
        if not mine and where > 0.2:
            continue
        score = 80 if mine else 55
        score -= int(where * 10)
        if path.endswith(".svg"):
            score += 3          # crisp at any tile size
        elif path.endswith((".jpg", ".jpeg")):
            score -= 5          # a jpeg logo is usually a photo of one
        out.append((score, url))
    out.sort(reverse=True)
    return out[:4]


def manifest_icons(base: str, page: str, get) -> list[tuple[int, str]]:
    """Icons named by the site's web app manifest.

    A manifest lists icons the HTML never links - it is where a modern build
    puts the 192px and 512px marks - so it is the cheapest untried source
    left on a site that declares no <link rel=icon> at all.
    """
    urls = []
    for tag in MANIFEST_RE.findall(page or ""):
        m = HREF_RE.search(tag)
        if m and not m.group(1).startswith("data:"):
            urls.append(urllib.parse.urljoin(base, htmllib.unescape(m.group(1))))
    for blind in ("/site.webmanifest", "/manifest.json"):
        urls.append(urllib.parse.urljoin(base, blind))
    out: list[tuple[int, str]] = []
    for u in urls[:2]:                      # two requests, never a crawl
        blob = get(u)
        if not blob:
            continue
        try:
            doc = json.loads(blob.decode("utf-8", "replace"))
        except Exception:
            continue
        for icon in (doc.get("icons") or [])[:12]:
            src = (icon or {}).get("src")
            if not src or src.startswith("data:"):
                continue
            sizes = re.search(r"(\d+)", str(icon.get("sizes") or ""))
            px = int(sizes.group(1)) if sizes else 0
            # 512 is a splash image more often than a mark; 180-256 is the
            # sweet spot for a 42px tile at 2x.
            rank = 120 - abs(192 - px) // 16
            out.append((rank, urllib.parse.urljoin(u, htmllib.unescape(src))))
        if out:
            break
    out.sort(reverse=True)
    return out[:4]


def candidates(site: str, page: str, cid: str = "", name: str = "",
               get=None) -> list[str]:
    """Icon URLs to try, best first."""
    out: list[tuple[int, str]] = []
    for tag in ICON_RE.findall(page or ""):
        m = HREF_RE.search(tag)
        if not m:
            continue
        href = m.group(1).strip()
        if href.startswith("data:"):
            continue
        size = int(SIZES_RE.search(tag).group(1)) if SIZES_RE.search(tag) else 0
        if "apple-touch" in tag.lower():
            size = max(size, 180)
        out.append((size, urllib.parse.urljoin(site, htmllib.unescape(href))))
    if get is not None:
        out += manifest_icons(site, page, get)
    out += markup_logos(site, page or "", cid, name, site)
    # og:image is the card image a site shows when shared - very often the
    # logo, and present on sites that declare no icon at all
    for tag in OG_RE.findall(page or ""):
        m = CONTENT_RE.search(tag)
        if m and not m.group(1).startswith("data:"):
            out.append((40, urllib.parse.urljoin(site, htmllib.unescape(m.group(1).strip()))))
    out.sort(reverse=True)
    urls = [u for _, u in out]
    urls += [urllib.parse.urljoin(site, path) for path in BLIND_PATHS]
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:16]


def logo_path(cid: str, ext: str, out_dir: pathlib.Path = OUT) -> pathlib.Path:
    """Where this logo is allowed to land, or nowhere.

    out_dir / f"{cid}.{ext}" only lands under assets/logos when cid is a bare
    slug, and an id reaches here from wherever companies.json got it -
    including an outside submission. Resolving the joined path and insisting
    the parent is still out_dir is the layer that holds without trusting
    anything upstream, which is the point of having it as well as the checks
    upstream.
    """
    dest = (out_dir / f"{cid}.{ext}").resolve()
    if dest.parent != out_dir.resolve():
        raise ValueError(f"{cid!r} is not a company id, it is a path")
    return dest


def looks_blocked(status: int, page: str) -> bool:
    """A bot wall, not a website.

    Some hosts answer a non-browser request with a tiny JS-challenge shell -
    202 or 200, a couple of hundred bytes, `<link rel="icon" href="data:;">`
    and nothing else. That is not a site with no icon, and filing it as one
    would be the same lie as any other guess. We do not try to defeat it; we
    record it and move on.
    """
    if status in (401, 403, 429):
        return True
    return len(page) < 1500 and 'href="data:;"' in page


def fetch_one(row: tuple[str, str, str, bool]) -> dict:
    cid, site, name, pending = row
    import requests
    sys.path.insert(0, str(ROOT / "scripts"))
    import ats
    out_dir = PENDING if pending else OUT
    res = {"id": cid, "ok": False, "note": "", "via": None, "url": None,
           "pending": pending}
    page, base, status = "", site, 0
    last = "site unreachable"
    sess = requests.Session()

    def get(url: str, timeout: int = 12) -> bytes | None:
        """One small file. Politeness lives here so every path pays it."""
        try:
            r = sess.get(url, headers=ats.UA, timeout=timeout,
                         allow_redirects=True)
            time.sleep(0.25)
            if not r.ok or len(r.content) > MAX_BYTES * 3:
                return None
            return r.content
        except Exception:
            return None

    # A failure on one host form is a fact about that host form. A
    # certificate that fails on www often passes on the apex and the reverse,
    # and a 403 on one is frequently a 200 on the other - so an HTTP error
    # earns the sibling attempt just as an exception does.
    sibling = (site.replace("://www.", "://") if "://www." in site
               else site.replace("://", "://www."))
    # Last resort: plain http. A good number of these vendors let a
    # certificate lapse years ago and still serve the site, and several of
    # them redirect straight back up to https on the domain they use now. The
    # trade is an unauthenticated hop for one public image, against leaving a
    # live company blank - and same_brand() below is what stops that hop from
    # being followed somewhere it should not go.
    plain = "http://" + site.split("://", 1)[-1] if site.startswith("https") else None
    attempts = [(site, False), (sibling, False)] + ([(plain, True)] if plain else [])
    for attempt, is_plain in attempts:
        if is_plain and not last.startswith("site unreachable"):
            break               # it answered over https; http tells us nothing
        try:
            r = sess.get(attempt, headers=ats.UA, timeout=20,
                         allow_redirects=True)
            status, base = r.status_code, (r.url or attempt)
            if r.ok:
                if not same_brand(site, base, cid, name):
                    res["note"] = (f"redirects to {urllib.parse.urlsplit(base).hostname}"
                                   f" - a different brand, not taken")
                    return res
                page = r.text
                break
            last = f"site answered {r.status_code}"
        except Exception as exc:
            last = f"site unreachable ({type(exc).__name__})"
        time.sleep(0.3)

    if not page:
        res["note"] = last
        if status in (401, 403, 429):
            res["note"] = f"blocked to non-browser clients ({status})"
        return res
    if looks_blocked(status, page):
        # We are looking at a challenge page, not the company's site.
        res["note"] = f"bot wall, not the site ({status}, {len(page)}b)"
        return res

    tried, oversize = 0, ""
    for url in candidates(base, page, cid, name, get):
        tried += 1
        if tried > 16:
            break
        try:
            ir = sess.get(url, headers=ats.UA, timeout=12)
            time.sleep(0.25)
            if not ir.ok:
                continue
            ext, why = sniff(ir.content, ir.headers.get("content-type", ""))
            if not ext:
                if why.startswith("too big") and not oversize:
                    # Worth saying out loud. This is a real logo we chose not
                    # to keep, which is a different fact from a site that has
                    # no icon - and the only one of the two a human can act on.
                    oversize = f"logo found but {why[8:-1]}, over the cap"
                continue
            try:
                dest = logo_path(cid, ext, out_dir)
            except ValueError as exc:
                res["note"] = str(exc)
                return res
            out_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(ir.content)
            res.update(ok=True, note=tidy(url)[:120], url=tidy(url)[:300],
                       ext=ext, via=classify_source(url, base, page))
            return res
        except Exception:
            continue
    res["note"] = (oversize or ("no readable icon on the site" if tried
                                else "no candidate urls on the page"))
    return res


def tidy(url: str) -> str:
    """The URL without its query string, for the log.

    Some sites serve their own icon from a presigned bucket URL whose query
    is a two-kilobyte expiring signature. Keeping it would bury the log in
    noise and record a credential-shaped string we have no business holding.
    """
    return url.split("?", 1)[0]


def classify_source(url: str, base: str, page: str) -> str:
    """Which technique found it - so a bad logo can be traced to the rule
    that chose it, and the rule fixed rather than guessed at."""
    path = urllib.parse.urlsplit(url).path.lower()
    if url in page or urllib.parse.urlsplit(url).path in page:
        if "apple-touch" in path:
            return "apple-touch-icon"
        if "logo" in path:
            return "markup-logo"
        if "favicon" in path or "icon" in path:
            return "declared-icon"
        return "markup"
    if "manifest" in path:
        return "manifest"
    return "blind-path"


def load_proposed() -> dict[str, str]:
    """Websites another pass has PROPOSED but nobody has accepted.

    Read only. Their logos land in assets/logos/pending/ and stay dark until
    the website itself is ruled on.
    """
    if not PROPOSED_SITES.exists():
        return {}
    try:
        doc = json.loads(PROPOSED_SITES.read_text())
    except Exception:
        return {}
    rows = doc.get("proposals", doc) if isinstance(doc, dict) else doc
    out = {}
    if isinstance(rows, dict):
        rows = [dict(v, id=k) if isinstance(v, dict) else {"id": k, "website": v}
                for k, v in rows.items()]
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cid = row.get("id") or row.get("company") or row.get("cid")
        url = (row.get("website") or row.get("url") or row.get("proposed")
               or row.get("site"))
        if cid and isinstance(url, str) and url.startswith("http"):
            out[cid] = url
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--all", action="store_true",
                    help="every company, not just the ones with open roles")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--retry", action="store_true",
                    help="re-attempt companies that failed before. A failure "
                         "is a fact about one attempt, not about the company.")
    ap.add_argument("--proposed", action="store_true",
                    help="also try companies whose only website is a proposal "
                         "in data/proposed_websites.json. Those logos land in "
                         "assets/logos/pending/ and are not live.")
    ap.add_argument("--only", default="",
                    help="comma-separated company ids, for checking one rule")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    companies = json.loads((DATA / "companies.json").read_text())
    board = json.loads((DATA / "board.json").read_text())
    hiring = {o["id"]: o.get("open_roles", 0)
              for o in board.get("organizations", [])}
    log = json.loads(LOG.read_text()) if LOG.exists() else {}
    have = {f.stem for f in OUT.glob("*.*")}
    parked = {f.stem for f in PENDING.glob("*.*")} if PENDING.exists() else set()

    if a.stats:
        print(f"{len(have)} logos on file, {len(log)} companies attempted")
        print(f"  of the {sum(1 for c in companies if hiring.get(c['id'])) } hiring, "
              f"{sum(1 for c in companies if hiring.get(c['id']) and c['id'] in have)} "
              f"have one")
        if parked:
            print(f"  {len(parked)} parked in pending/ behind a proposed website")
        return 0

    proposed = load_proposed() if a.proposed else {}
    only = {s.strip() for s in a.only.split(",") if s.strip()}

    todo = []
    for c in companies:
        cid = c["id"]
        if cid in have or (only and cid not in only):
            continue
        site, pending = c.get("website"), False
        if not site:
            site, pending = proposed.get(cid), True
            if not site or cid in parked:
                continue
        elif not (a.retry or cid not in log):
            continue
        todo.append((cid, site, c.get("name") or cid, pending))
    # Companies with open roles first: they are the ones actually on screen.
    todo.sort(key=lambda t: -hiring.get(t[0], 0))
    if not a.all:
        todo = [t for t in todo if hiring.get(t[0], 0) > 0]
    todo = todo[:a.limit]
    if not todo:
        print("nothing to fetch")
        return 0

    print(f"fetching {len(todo)} logos with {a.workers} workers...", flush=True)
    got = 0
    by_via: dict[str, int] = {}
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(fetch_one, todo), 1):
            entry = {"ok": res["ok"], "note": res["note"][:120]}
            if res["ok"]:
                entry["via"] = res["via"]
                entry["url"] = res["url"]
                if res["pending"]:
                    # not live, and the log must say so or the next reader
                    # will count it as coverage it does not have
                    entry["pending"] = True
                got += 1
                by_via[res["via"]] = by_via.get(res["via"], 0) + 1
            log[res["id"]] = entry
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}, {got} found", flush=True)
    LOG.write_text(json.dumps(log, indent=1))
    total = sum(f.stat().st_size for f in OUT.glob("*.*"))
    print(f"{got} of {len(todo)} found · {len(list(OUT.glob('*.*')))} on file · "
          f"{total/1024:.0f} KB total")
    for via, n in sorted(by_via.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d} via {via}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
