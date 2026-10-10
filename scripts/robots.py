#!/usr/bin/env python3
"""Whether a site's robots.txt lets this crawler read a page.

THE OWNER'S RULING, 2026-10-09: the crawler honours robots.txt. It read
LinkedIn job pages for two companies every night, and the careers pages of
seven more whose robots.txt asks crawlers to stay out - Bruker Detection's and
BigBear.ai's iCIMS portals among them, the second one marked internal: 303
postings in all. A person may still capture a role by hand; that is a reader,
not a crawler.

WHAT IT COVERS. Pages: careers pages, job-board pages, rendered pages,
newsrooms, company sites read for a write-up or a submission, and every hop of
a redirect on the way to one (ats.follow). NOT the job boards' documented JSON
feeds (Greenhouse, Lever, Ashby and the rest), which are interfaces published
for programs to call, and not a liveness check that reads no content.

THE RULES ARE RFC 9309's, and this module reads them itself. The standard
library's urllib.robotparser predates the RFC and gets it wrong in ways that
read pages a site has closed: it takes the FIRST matching rule where the RFC
takes the longest ("Allow: /" then "Disallow: /jobs/" read /jobs/), it ignores
the * and $ wildcards the RFC says a crawler MUST support, it ends a group at
a blank line, and a byte-order mark costs it the whole first group. Found by
the blind review of the first version, 2026-10-09.

WHAT EACH ANSWER MEANS:
  - 200: the file's rules, for our product token or else `*`;
  - 4xx (none, or refused): no rules, so reading is allowed;
  - 5xx: keep out, and ask again within the hour;
  - no answer at all: keep out for THIS request only. It is a network
    failure, not a refusal, and is reported as one ("network error: ..."), so
    the board crawl's one transient retry applies and a site that was down
    for a moment is not filed as a site that said no.
"""
from __future__ import annotations

import functools
import re
import threading
import time
import urllib.parse
from typing import NamedTuple

# The product token in ats.UA. Kept in step by selftest, not imported, so this
# module loads before (and without) the fetcher it serves.
TOKEN = "govtech-dock"
UA = {"User-Agent": "Mozilla/5.0 (compatible; govtech-dock/1.0; "
                    "+https://github.com/westjw/govtech-dock)"}
TIMEOUT = 15
MAX_BYTES = 500 * 1024        # RFC 9309 2.5: parse at least the first 500 KiB

# HOW LONG AN ANSWER HOLDS. A nightly crawl is under an hour; the admin server
# runs for days, and a cache with no expiry turned one timeout into a site
# refused until somebody restarted it.
TTL_RULES = 24 * 3600         # 200 or 4xx: the file as read
TTL_SERVER = 3600             # 5xx: keep out, and ask again within the hour
TTL_NETWORK = 0               # no answer: never cached; the next read asks

_CACHE: dict[str, tuple[float, "_Answer"]] = {}
_TABLE = threading.Lock()                       # guards the dicts only
_HOST_LOCKS: dict[str, threading.Lock] = {}     # one robots.txt request per host


class Refused(Exception):
    """robots.txt asks crawlers not to read this page, or would not say."""

    def __init__(self, why: str, kind: str = "disallowed",
                 error: BaseException | None = None):
        super().__init__(why)
        self.kind, self.error = kind, error


class Verdict(NamedTuple):
    ok: bool
    why: str = ""                 # "" when ok
    kind: str = "allowed"         # allowed | disallowed | server | network
    error: BaseException | None = None


ALLOWED = Verdict(True)


class _Answer(NamedTuple):
    rules: list | None            # [(allow, pattern)] for us; None = keep out
    kind: str = "allowed"         # how a None came about: server | network
    detail: str = ""              # "HTTP 503" or the exception's name
    error: BaseException | None = None
    ttl: float = TTL_RULES


# ---- the file ---------------------------------------------------------------

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                        "0123456789-._~")
_HEX = re.compile(rb"[0-9A-Fa-f]{2}")


def _norm(s: str) -> str:
    """One spelling for comparison (RFC 9309 2.2.2): non-ASCII and controls
    percent-encoded as UTF-8, escapes of unreserved characters decoded, every
    other escape in upper case."""
    b, out, i = s.encode("utf-8", "surrogatepass"), [], 0
    while i < len(b):
        c = b[i]
        if c == 0x25 and _HEX.fullmatch(b[i + 1:i + 3]):
            v = int(b[i + 1:i + 3], 16)
            out.append(chr(v) if chr(v) in _UNRESERVED else f"%{v:02X}")
            i += 3
            continue
        out.append(chr(c) if 0x21 <= c <= 0x7E else f"%{c:02X}")
        i += 1
    return "".join(out)


_LINE = re.compile(r"^\s*([A-Za-z-]+)\s*:\s*(.*?)\s*$")


def parse(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    """The groups in a robots.txt: [(product tokens, [(allow, pattern)])].

    A group is one or more user-agent lines and the rules after them. A blank
    line does not end it (the RFC's grammar allows them inside one); the next
    user-agent line after a rule does. Lines this does not know (sitemap,
    crawl-delay) neither start nor end a group. Rules before any user-agent
    line belong to nobody.
    """
    groups, cur, in_rules = [], None, False
    for raw in (text or "").lstrip("\ufeff").splitlines():
        m = _LINE.match(raw.split("#", 1)[0])
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2)
        if key == "user-agent":
            if cur is None or in_rules:
                cur, in_rules = ([], []), False
                groups.append(cur)
            cur[0].append(_agent(val))
        elif key in ("allow", "disallow") and cur is not None:
            in_rules = True
            if val:                                     # an empty rule matches nothing
                cur[1].append((key == "allow", _norm(val)))
    return groups


def _agent(val: str) -> str:
    """The product token a user-agent line names: `*`, or its leading run of
    letters, underscores and hyphens ("govtech-dock/1.0" names govtech-dock),
    compared without case (RFC 9309 2.2.1)."""
    v = val.strip()
    if v.startswith("*"):
        return "*"
    m = re.match(r"[A-Za-z_-]+", v)
    return m.group(0).lower() if m else ""


def rules_for(groups, token: str = TOKEN) -> list[tuple[bool, str]]:
    """Every rule of every group naming us, combined; else every `*` group's."""
    token = token.lower()
    mine = [r for agents, rules in groups if token in agents for r in rules]
    if any(token in agents for agents, _ in groups):
        return mine
    return [r for agents, rules in groups if "*" in agents for r in rules]


@functools.lru_cache(maxsize=4096)
def _pattern(pat: str) -> re.Pattern:
    anchored = pat.endswith("$")
    body = pat[:-1] if anchored else pat
    return re.compile(".*".join(re.escape(p) for p in body.split("*"))
                      + (r"\Z" if anchored else ""), re.S)


def decide(rules: list[tuple[bool, str]], url: str) -> bool:
    """RFC 9309 2.2.2: the longest matching pattern decides, an Allow wins a
    tie, and with nothing matching the page may be read."""
    p = urllib.parse.urlsplit(url)
    path = _norm((p.path or "/") + (f"?{p.query}" if p.query else ""))
    best = None
    for allow, pat in rules:
        if _pattern(pat).match(path):
            key = (len(pat), allow)                     # longer, then Allow
            if best is None or key > best:
                best = key
    return True if best is None else best[1]


# ---- asking -----------------------------------------------------------------

def _rules(base: str, fetch=None) -> _Answer:
    """What one host's robots.txt says to us. `fetch(url) -> (status, text)`
    stands in for the network in tests."""
    try:
        if fetch is None:
            status, text = _ask(base + "/robots.txt")
        else:
            status, text = fetch(base + "/robots.txt")
    except Exception as exc:                            # noqa: BLE001 - no answer
        return _Answer(None, "network", type(exc).__name__, exc, TTL_NETWORK)
    if status == 200:
        return _Answer(rules_for(parse(text or "")))
    if 400 <= status < 500:
        return _Answer([])                              # unavailable: no rules
    return _Answer(None, "server", f"HTTP {status}", None, TTL_SERVER)


def _ask(url: str) -> tuple[int, str]:
    """GET one robots.txt, PACED like every other request at that host and
    read no further than the RFC asks."""
    import requests
    try:
        import ats
        ats._host_gate(url)
    except ImportError:                                 # pragma: no cover
        pass
    r = requests.get(url, headers=UA, timeout=TIMEOUT, stream=True)
    try:
        raw = b""
        for chunk in r.iter_content(chunk_size=65536):
            raw += chunk
            if len(raw) >= MAX_BYTES:
                break
    finally:
        r.close()
    return r.status_code, raw[:MAX_BYTES].decode("utf-8", errors="replace")


# RFC 2606 / RFC 6761 names, which no real site can hold: the suite's
# fixtures live on them, and there is nobody to ask.
_RESERVED = (".test", ".example", ".invalid", ".localhost")
_RESERVED_HOSTS = {"localhost", "example.com", "example.net", "example.org"}


def _reserved(host: str) -> bool:
    h = host.split(":")[0].lower().rstrip(".")
    return (h in _RESERVED_HOSTS or h.endswith(_RESERVED)
            or any(h.endswith("." + r) for r in _RESERVED_HOSTS))


def verdict(url: str, fetch=None) -> Verdict:
    """May this crawler read `url`, and if not, why not."""
    p = urllib.parse.urlsplit(url or "")
    if p.scheme not in ("http", "https") or not p.netloc:
        return ALLOWED                                  # nothing to ask
    if _reserved(p.netloc) or p.path == "/robots.txt":
        return ALLOWED
    host = p.netloc.lower()
    base = f"{p.scheme}://{host}"
    with _TABLE:
        lock = _HOST_LOCKS.setdefault(base, threading.Lock())
    # ONE REQUEST PER HOST, the rest wait for its answer. Ten workers reaching
    # a shared board host at once (jobs.gusto.com, ats.rippling.com) sent ten
    # robots.txt requests together, and whichever finished last was believed.
    with lock:
        hit = _CACHE.get(base)
        if hit is None or hit[0] <= time.monotonic():
            ans = _rules(base, fetch)
            hit = (time.monotonic() + ans.ttl, ans)
            if ans.ttl > 0:
                _CACHE[base] = hit
    ans = hit[1]
    if ans.rules is None:
        if ans.kind == "network":
            return Verdict(False, f"network error: robots.txt on {host} did not "
                                  f"answer ({ans.detail})", "network", ans.error)
        return Verdict(False, f"robots.txt on {host} answered {ans.detail}; "
                              f"kept out until it answers", "server")
    if decide(ans.rules, url):
        return ALLOWED
    return Verdict(False, f"robots.txt asks crawlers not to read {host}",
                   "disallowed")


def allowed(url: str, fetch=None) -> bool:
    """True when robots.txt lets this crawler read `url`."""
    return verdict(url, fetch).ok


def check(url: str, fetch=None) -> None:
    """Raise Refused when this crawler may not read `url`."""
    v = verdict(url, fetch)
    if not v.ok:
        raise Refused(v.why, v.kind, v.error)
