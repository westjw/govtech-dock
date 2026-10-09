#!/usr/bin/env python3
"""The content security policy every page is served with, built from the pages.

WHY ONE EXISTS. Every page here writes text from outside - job titles from
company job boards, company names, news headlines - into markup, guarded by
escaping. A policy is the second wall: if one escape is ever missed, the
browser still refuses to run what got in. It forbids inline handlers
(`script-src-attr 'none'`), `javascript:` urls and any script this build did
not write.

HOW IT CANNOT DRIFT. A hash typed into a file is wrong the next time anyone
edits index.html, and the site goes blank. So nobody types one:
build_site.main() calls write(out) LAST, it reads the pages exactly as they
will ship, hashes every inline script it finds, and writes the policy for
each kind of page into public/meta-csp.json. functions/_csp.js reads that
file and sets the header. An edit changes the page and its hashes in the same
build and the same deploy.

WHAT THE BUILD REFUSES (SystemExit, naming the file). The hashes say "this
script may run", so hashing whatever is on a page would bless anything an
escaping bug let in. Each kind of page has the scripts it is EXPECTED to
carry, cut from their sources, and any other inline script stops the build.
So do an on* attribute, a javascript: url, an external script, a nonce
attribute, a policy in a meta tag, a <base>, <object>, <embed> or <iframe>,
an import map, speculation rules, and a script whose text holds "<script"
(where the browser's tokenizer and ours could disagree about where it ends).
A refused build leaves the previous deployment live; the watchdog says so.

Styles stay 'unsafe-inline': the pages carry thousands of style attributes,
and an injected style can restyle a page but cannot run anything.
"""
from __future__ import annotations

import base64
import hashlib
import html.parser
import json
import pathlib
import re

# Which kind of page answers which address, first match wins, matched on the
# DECODED path (Pages serves /%61lerts as /alerts). "holding" is the gate's
# refusal and is chosen by the middleware, never by a path.
ROUTES = [
    ("admin", r"^/admin(/.*)?$"),
    ("alerts", r"^/alerts(\.html)?/?$"),
    ("claim", r"^/claim(\.html)?/?$"),
    ("company", r"^/c/"),
    ("static", r"^/(s|e)/"),
    ("app", r"^/(index\.html)?$"),
    ("none", r""),
]

NONCE = "{{NONCE}}"
ORIGIN = "{{ORIGIN}}"

# The one external script this policy expects: Cloudflare's Bot Fight Mode
# injects an inline snippet (it takes the response's nonce) that loads
# main.js from this path on our own origin. Seen live on sledjobs.com,
# 2026-10-09. The path is narrow on purpose: 'self' would allow any script
# file this origin serves.
CHALLENGE = "/cdn-cgi/challenge-platform/"

BASE = [
    ("default-src", "'none'"),
    ("script-src", None),                      # filled per kind
    ("script-src-attr", "'none'"),
    ("style-src", "'unsafe-inline' https://fonts.googleapis.com"),
    ("font-src", "'self' https://fonts.gstatic.com"),
    ("img-src", "'self'"),
    ("connect-src", "'self'"),
    ("form-action", "'none'"),
    ("base-uri", "'none'"),
    ("object-src", "'none'"),
]

REFUSED_TAGS = ("base", "object", "embed", "iframe")
URL_ATTRS = ("href", "src", "action", "formaction", "xlink:href")
DATA_TYPES = ("application/ld+json", "application/json")


class Refused(SystemExit):
    pass


def kind_of(rel: str) -> str | None:
    """Which kind of page a shipped HTML file is, by its path under public/."""
    if rel == "index.html":
        return "app"
    if rel in ("alerts.html", "claim.html"):
        return rel[:-5]
    if rel == "404.html":
        return "none"
    if rel in ("admin/index.html", "admin/hunter/index.html"):
        return "admin"
    if re.fullmatch(r"c/[^/]+\.html", rel):
        return "company"
    if re.fullmatch(r"(s|e)/[^/]+\.html", rel):
        return "static"
    return None


class _Page(html.parser.HTMLParser):
    """The inline scripts a page carries, and anything it must not carry."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.bad: list[str] = []
        self._in: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {(k or "").lower(): (v or "") for k, v in attrs}
        for k, v in a.items():
            if k.startswith("on"):
                self.bad.append(f"an inline handler <{tag} {k}=...>")
            if k in URL_ATTRS and re.match(r"\s*javascript:", v, re.I):
                self.bad.append(f"a javascript: url in <{tag} {k}>")
            if k == "nonce":
                self.bad.append(f"a nonce attribute on <{tag}>")
        if tag in REFUSED_TAGS:
            self.bad.append(f"a <{tag}> element")
        if tag == "meta" and a.get("http-equiv", "").lower() == "content-security-policy":
            self.bad.append("a policy in a <meta> tag (it would intersect the header's)")
        if tag == "script":
            t = a.get("type", "").strip().lower()
            if "src" in a:
                self.bad.append(f"an external script ({a['src'][:60]})")
            if t in ("importmap", "speculationrules"):
                self.bad.append(f"a <script type={t}>")
            # a data block is not run, so it is neither hashed nor refused
            self._in = None if t in DATA_TYPES else []

    handle_startendtag = handle_starttag

    def handle_data(self, data):
        if self._in is not None:
            self._in.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in is not None:
            self.scripts.append("".join(self._in))
            self._in = None
        elif tag == "script":
            self._in = None


def read(text: str) -> _Page:
    p = _Page()
    p.feed(text)
    p.close()
    return p


def norm(script: str) -> str:
    """The text a browser hashes: its input stream turns CRLF and lone CR into LF."""
    return script.replace("\r\n", "\n").replace("\r", "\n")


def sha(script: str) -> str:
    d = hashlib.sha256(norm(script).encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(d).decode("ascii") + "'"


def policy(hashes) -> str:
    src = " ".join([f"'nonce-{NONCE}'", ORIGIN + CHALLENGE] + sorted(set(hashes)))
    return "; ".join(f"{k} {src if v is None else v}" for k, v in BASE)


def expected(root: pathlib.Path, brand: dict, hunter: str, lightmark: str) -> dict:
    """The scripts each kind of page may carry, cut from their sources."""
    def of(text):
        return [norm(s) for s in read(text).scripts]
    src = lambda n: (root / n).read_text() if (root / n).exists() else ""  # noqa: E731
    return {
        "app": of(src("index.html")),
        "alerts": of(src("alerts.html")),
        "claim": of(src("claim.html")),
        "admin": of(src("admin-web.html")) + of(hunter),
        "company": of(lightmark),
        "static": [], "none": [],
    }


def build(out: pathlib.Path, allowed: dict) -> dict:
    """Read every shipped page, refuse what it must not carry, and return the
    manifest: routes, one policy per kind, and how many scripts each holds."""
    problems: list[str] = []
    hashes: dict = {k: set() for k, _ in ROUTES}
    count: dict = {k: 0 for k, _ in ROUTES}
    for f in sorted(out.rglob("*.html")):
        rel = f.relative_to(out).as_posix()
        kind = kind_of(rel)
        if kind is None:
            problems.append(f"{rel}: a page of no known kind; csp.kind_of() must name it")
            continue
        page = read(f.read_text(encoding="utf-8"))
        problems += [f"{rel}: {b}" for b in page.bad]
        for s in page.scripts:
            if re.search(r"<script|</script", s, re.I):
                problems.append(f"{rel}: a script whose text holds a script tag")
            if norm(s) not in allowed.get(kind, []):
                problems.append(f"{rel}: an inline script this build did not write "
                                f"({len(s)} chars, starting {s.strip()[:50]!r})")
                continue
            hashes[kind].add(sha(s))
            count[kind] += 1
    if problems:
        raise Refused("the content security policy refuses this build:\n  "
                      + "\n  ".join(problems[:30])
                      + (f"\n  ... and {len(problems) - 30} more" if len(problems) > 30 else ""))
    hashes["holding"] = set()
    return {
        "note": "Written by scripts/csp.py at build time; read by functions/_csp.js. "
                "Do not edit: every hash is recomputed from the pages on every build.",
        "routes": [[k, rx] for k, rx in ROUTES],
        "policies": {k: policy(v) for k, v in hashes.items()},
        "scripts": count,
    }


def write(out: pathlib.Path, root: pathlib.Path, brand: dict, hunter: str, lightmark: str) -> dict:
    m = build(out, expected(root, brand, hunter, lightmark))
    (out / "meta-csp.json").write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    return m
