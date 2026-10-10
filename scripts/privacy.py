#!/usr/bin/env python3
"""What never reaches a tracked file: an e-mail address.

THE REPOSITORY IS PUBLIC. On 2026-10-09, 140 records across 23 tracked data
files were somebody's e-mail address - 73 of them people's work mailboxes,
harvested off sheriffs'-association exhibitor floors as if they were company
names, and sitting in data/suppliers.json where raw.githubusercontent serves
them to anyone. The guard at the time counted "business-contact files" and
let each hold its number. The rule is simpler now and matches the owner's:
NO address in a tracked file. Our own brand mailbox and RFC 2606 placeholders
are not addresses in this sense; an '@2x' image filename is not one at all.

Two writers quote other people's pages into tracked files - the news sweep
(headlines) and proposal ingest (evidence, quotes, "apply by e-mail to ...").
Both redact here before writing, and the write-up door compares a quote to a
page through the same redaction, so a stored quote still matches the page it
was read from.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import brand                                                    # noqa: E402

ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
REDACTED = "[address removed]"

# Not anybody's mailbox: RFC 2606 placeholders, the invented domains this
# codebase's own fixtures use, and our own brand's (read from brand.json, the
# one place the domain is written down).
RESERVED = {"example.com", "example.org", "example.net", "email.com",
            "acme.com", "evil.com", "yourcompany.com", "nobody.com",
            "nosite.com", "other.com", "britco.co.uk", "someoneelse.co.uk",
            brand.DOMAIN.lower(), brand.FROM.rsplit("@", 1)[-1].rstrip(">").lower()}
# Machines, not people: no-reply senders and an ATS's forwarding mailbox.
MACHINE = ("noreply", "no-reply", "@jobs.workablemail.com")
_IMAGE = re.compile(r"\.(png|jpe?g|webp|gif|svg|avif|ico)$", re.I)


def is_address(s: str) -> bool:
    """A string that is somebody's address: not a placeholder, a machine or an
    image filename that happens to carry an @."""
    s = (s or "").strip().lower()
    if not ADDRESS.fullmatch(s):
        return False
    dom = s.rsplit("@", 1)[-1]
    if dom in RESERVED or dom.endswith((".test", ".invalid", ".localhost")):
        return False
    if any(dom.endswith("." + r) for r in RESERVED):        # mail.acme.com
        return False
    if any(m in s for m in MACHINE) or _IMAGE.search(dom):
        return False
    return True


def addresses(text: str) -> list[str]:
    """Every address in `text` that is somebody's, deduped, in order."""
    out: list[str] = []
    for a in ADDRESS.findall(text or ""):
        if is_address(a) and a.lower() not in out:
            out.append(a.lower())
    return out


def redact(text: str) -> str:
    """`text` with every address that is somebody's replaced by REDACTED."""
    if not isinstance(text, str) or "@" not in text:
        return text
    return ADDRESS.sub(lambda m: REDACTED if is_address(m.group(0)) else m.group(0), text)


def redact_deep(obj):
    """`redact` over every string in a JSON-shaped value; the shape is kept."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_deep(v) for k, v in obj.items()}
    return obj
