#!/usr/bin/env python3
"""Whether a site's robots.txt lets this crawler read a page.

THE OWNER'S RULING, 2026-10-09: the crawler honours robots.txt. It read
LinkedIn job pages for two companies every night, and the portals of eleven
more whose robots.txt asks every crawler to stay out - Bruker Detection's and
BigBear.ai's iCIMS portals among them, the second one marked internal. A
person may still capture a role by hand; that is a reader, not a crawler.

WHAT IT COVERS. Pages: careers pages, job-board pages, rendered pages,
newsrooms, company sites read for a write-up or a submission. NOT the job
boards' documented JSON feeds (Greenhouse, Lever, Ashby and the rest), which
are interfaces published for programs to call, and not a liveness check that
reads no content (link_check).

THE RULES ARE RFC 9309's, cached per host for the run:
  - 200: the file's rules, for our product token or else `*`;
  - 4xx (none, or refused): no rules, so reading is allowed;
  - 5xx or no answer: treated as "keep out" for this run. A board refused
    this way is unreadable tonight, which the board already says honestly,
    and keeps its dates when it comes back (history_first_seen).
"""
from __future__ import annotations

import threading
import urllib.parse
import urllib.robotparser

# The product token in ats.UA. Kept in step by selftest, not imported, so this
# module has no import of the fetcher it serves.
TOKEN = "govtech-dock"
UA = {"User-Agent": "Mozilla/5.0 (compatible; govtech-dock/1.0; "
                    "+https://github.com/westjw/govtech-dock)"}
TIMEOUT = 15

_CACHE: dict[str, "urllib.robotparser.RobotFileParser | None"] = {}
_LOCK = threading.Lock()


class Refused(Exception):
    """robots.txt asks crawlers not to read this page."""


def _rules(base: str, fetch=None):
    """The parsed rules for one host, None for "keep out" (RFC 9309 unreachable)."""
    rp = urllib.robotparser.RobotFileParser()
    try:
        if fetch is None:
            import requests
            r = requests.get(base + "/robots.txt", headers=UA, timeout=TIMEOUT)
            status, text = r.status_code, r.text
        else:
            status, text = fetch(base + "/robots.txt")
    except Exception:                                   # noqa: BLE001 - no answer
        return None
    if status == 200:
        rp.parse((text or "").splitlines())
        return rp
    if 400 <= status < 500:
        rp.parse([])                                    # unavailable: no rules
        return rp
    return None


# RFC 2606 / RFC 6761 names, which no real site can hold: the suite's
# fixtures live on them, and there is nobody to ask.
_RESERVED = (".test", ".example", ".invalid", ".localhost")
_RESERVED_HOSTS = {"localhost", "example.com", "example.net", "example.org"}


def _reserved(host: str) -> bool:
    h = host.split(":")[0].lower().rstrip(".")
    return (h in _RESERVED_HOSTS or h.endswith(_RESERVED)
            or any(h.endswith("." + r) for r in _RESERVED_HOSTS))


def allowed(url: str, fetch=None) -> bool:
    """True when robots.txt lets this crawler read `url`."""
    p = urllib.parse.urlsplit(url or "")
    if p.scheme not in ("http", "https") or not p.netloc:
        return True                                     # nothing to ask
    if _reserved(p.netloc):
        return True
    base = f"{p.scheme}://{p.netloc.lower()}"
    with _LOCK:
        hit = base in _CACHE
        rp = _CACHE.get(base)
    if not hit:
        rp = _rules(base, fetch)
        with _LOCK:
            _CACHE[base] = rp
    if rp is None:
        return False
    return rp.can_fetch(TOKEN, url)


def check(url: str, fetch=None) -> None:
    """Raise Refused when robots.txt asks crawlers not to read `url`."""
    if not allowed(url, fetch):
        raise Refused(f"robots.txt asks crawlers not to read "
                      f"{urllib.parse.urlsplit(url).netloc}")
