#!/usr/bin/env python3
"""Read what a person typed on an admin row and propose what to do about it.

THE POINT. Every queue row is a fixed set of buttons, and the answer a person
has is often not one of them. Wiring Rain Bird's real job board took four
steps by hand: read the page, work out which fetcher reads it, verify the
board is theirs, then call set-board with the right type and ref. What the
owner wanted to do was paste the link on the row and be understood.

SEMANTIC DOES NOT MEAN A LANGUAGE MODEL. Almost everything a person types
here is a URL, and a URL can be PROBED: fetched, matched against the ATS
marker table, read for job links, and checked against the company on the row.
That is deterministic, free, testable offline, and it answers with evidence -
"ten job titles on your own domain" - rather than a confident guess. The
phrases that are not URLs are a small bounded map, in the spirit of the
company search: about a dozen shapes somebody wrote down, not a model. A
sentence this file cannot read is recorded as a note rather than guessed at,
which is what the suggestion box already did and is still the right floor.

IT PROPOSES. IT NEVER WRITES. Every proposal names an EXISTING action and the
exact body to send it, and the person clicks to accept. Nothing here is a new
door into companies.json: set-board still verifies, save-website still probes,
patch still validates, and a proposal for an action outside ACCEPTABLE is
refused by the endpoint rather than run. That separation is the whole safety
story - the parser can be wrong, and the worst it can do is offer.

read() is pure and takes no network, so every phrasing is a unit test.
propose() is where the fetching happens, once, against the thing that was
actually typed.
"""
from __future__ import annotations

import re
import sys
import pathlib
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# The only actions a proposal may name. A parser bug can then propose the
# wrong THING, which a person sees and rejects; it can never propose an
# operation nobody reviewed. Every one of these already has its own gates.
ACCEPTABLE = {"set-board", "save-website", "patch", "place", "also",
              "posts-at", "merge", "suggest"}

URL = re.compile(r"https?://[^\s<>\"')]+", re.I)

# A LISTING PAGE, NOT A BROCHURE. The same rule posts_at uses for a link that
# claims to be where somebody posts: /careers-home is a landing page and
# /search-jobs is a board, and the difference is exactly what cost Rain Bird
# ten open roles.
JOBSY = re.compile(r"/jobs?(/|$|\?)|/careers?(/|$|\?)|/search-jobs|/openings|"
                   r"/positions|/vacanc|/opportunit|/join|/work-with-us", re.I)

_RENAME = re.compile(r"\b(?:rename(?:\s+it)?\s+to|they'?re\s+called|"
                     r"actually\s+called|real\s+name\s+is|should\s+be\s+called)\s+"
                     r"(.{2,80})", re.I)
_MERGE = re.compile(r"\b(?:same\s+(?:as|company\s+as)|merge\s+(?:it\s+)?(?:into|with)|"
                    r"duplicate\s+of|this\s+is)\s+([A-Za-z0-9][\w .,&'\-]{1,60})", re.I)
_BUCKET = re.compile(r"\b(?:files?\s+under|belongs?\s+(?:in|under)|should\s+be\s+(?:in|under)|"
                     r"move\s+(?:it\s+)?to|bucket\s+is)\s+(.{3,60})", re.I)
_ALSO = re.compile(r"\b(?:also|as\s+well\s+as|and\s+also)\s+(?:in|under)\s+(.{3,60})", re.I)
_NO_SITE = re.compile(r"\b(no|hasn'?t|has\s+no|there\s+is\s+no)\s+"
                      r"(site|website|web\s?site)\b", re.I)
_NOT_HIRING = re.compile(r"\b(not|no(?:t)?\s+currently)\s+hiring\b", re.I)

# Where a link points, when it is not the company's own site. The keys are
# posts_at's, so a proposal can be handed straight to that door.
_PLACES = [("linkedin", r"linkedin\.com"), ("indeed", r"indeed\.com"),
           ("glassdoor", r"glassdoor\."), ("ziprecruiter", r"ziprecruiter\.com"),
           ("builtin", r"builtin\."), ("wellfound", r"wellfound\.com|angel\.co"),
           ("govportal", r"governmentjobs\.com|neogov|usajobs\.gov|\.gov/")]


def _host(u: str) -> str:
    try:
        return (urllib.parse.urlsplit(u).netloc or "").lower().replace("www.", "")
    except ValueError:
        return ""


def _same_site(a: str, b: str) -> bool:
    """Two hosts belonging to the same organisation, by registrable-ish root.

    careers.bigbear.ai and bigbear.ai are one site; rainbird.com and
    rainbird.co.uk are not, and neither is bigbear.ai and icims.com.
    """
    ha, hb = _host(a), _host(b)
    if not ha or not hb:
        return False
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def read(text: str, company: dict | None = None) -> dict:
    """What the person meant. Pure: no network, no file reads.

    Returns {"kind": ..., ...}. "note" is the honest floor for anything this
    map does not recognise - guessing at a sentence is how a chat box starts
    writing things nobody asked for.
    """
    t = (text or "").strip()
    if not t:
        return {"kind": "empty"}
    site = (company or {}).get("website") or ""
    urls = URL.findall(t)
    words = URL.sub(" ", t)              # the prose, with links taken out

    if urls:
        u = urls[0].rstrip(".,;)")
        place = next((k for k, pat in _PLACES if re.search(pat, u, re.I)), None)
        if place:
            return {"kind": "posts_at", "where": place, "url": u}
        if site and _same_site(u, site):
            # their own domain: a listing page is a board, anything else is
            # the website itself
            return {"kind": "board" if JOBSY.search(u) else "website", "url": u}
        if not site:
            return {"kind": "website", "url": u}
        # a third-party host that is not a jobs site we know: it may be an
        # ATS, which only a probe can tell, so say so rather than guess
        return {"kind": "board", "url": u, "offsite": True}

    m = _RENAME.search(words)
    if m:
        return {"kind": "rename", "name": m.group(1).strip(" .\"'")}
    m = _ALSO.search(words)
    if m:
        return {"kind": "also", "bucket": m.group(1).strip(" .\"'")}
    m = _BUCKET.search(words)
    if m:
        return {"kind": "bucket", "bucket": m.group(1).strip(" .\"'")}
    m = _MERGE.search(words)
    if m:
        return {"kind": "merge", "name": m.group(1).strip(" .\"'")}
    if _NO_SITE.search(words):
        return {"kind": "no_site"}
    if _NOT_HIRING.search(words):
        return {"kind": "not_hiring"}
    return {"kind": "note"}


def _bucket_of(phrase: str, schema: dict) -> tuple[str, str] | None:
    """Match "public safety / police", "police", "Courts" to a real shelf.

    Only ever returns a pair the schema actually has. A bucket that does not
    exist is not a bucket, and inventing one is how a filter stops finding
    anybody.
    """
    p = re.sub(r"\s+", " ", (phrase or "")).strip().lower().strip(".")
    if not p:
        return None
    parts = [x.strip() for x in re.split(r"\s*[/>|]\s*|\s+->\s+", p) if x.strip()]
    pairs = [(s["name"], c) for s in schema.get("sectors", []) for c in s["categories"]]
    if len(parts) >= 2:
        for s, c in pairs:
            if s.lower() == parts[0] and c.lower() == parts[1]:
                return s, c
    for s, c in pairs:                       # a category names its sector for us
        if c.lower() == p:
            return s, c
    hits = [(s, c) for s, c in pairs if c.lower().startswith(p)]
    return hits[0] if len(hits) == 1 else None


def propose(intent: dict, company: dict, *, schema: dict,
            companies: list | None = None, probe=None) -> dict:
    """Turn an intent into {action, body, says, evidence} or {"error": ...}.

    `probe` is injected so this is testable without the internet: it takes a
    url and returns {"kind": <ats type or None>, "ref": ..., "titles": [...],
    "host": ..., "error": ...}.
    """
    kind = intent.get("kind")
    cid = company.get("id")
    if kind in ("empty", "note"):
        return {"action": "suggest", "body": {"id": cid},
                "says": "I could not read that as an instruction, so it will be "
                        "recorded on the row as a note.",
                "evidence": []}

    if kind == "website":
        return {"action": "save-website",
                "body": {"id": cid, "url": intent["url"]},
                "says": f"Save {_host(intent['url'])} as their website. That also "
                        f"fetches their logo and looks for a job board.",
                "evidence": [f"the link is on {_host(intent['url'])}"]}

    if kind == "posts_at":
        return {"action": "posts-at",
                "body": {"id": cid, "where": intent["where"], "url": intent["url"]},
                "says": f"Record that they post on {intent['where']}, linking to "
                        f"that page. It does not become a board we read.",
                "evidence": [f"the link is a {intent['where']} address"]}

    if kind == "rename":
        keep = [a for a in (company.get("also_known_as") or [])]
        if company.get("name") and company["name"] not in keep:
            keep.append(company["name"])
        return {"action": "patch",
                "body": {"id": cid, "fields": {"name": intent["name"],
                                               "also_known_as": keep}},
                "says": f"Rename to {intent['name']!r}, keeping {company.get('name')!r} "
                        f"as an alias so the old name still finds them.",
                "evidence": []}

    if kind in ("bucket", "also"):
        pair = _bucket_of(intent.get("bucket", ""), schema)
        if not pair:
            return {"error": f"{intent.get('bucket')!r} is not a sector or category "
                             f"on this board, so I will not guess at it"}
        s, c = pair
        if kind == "also":
            return {"action": "also", "body": {"id": cid, "sector": s, "category": c},
                    "says": f"Add {s} / {c} beside their primary shelf. A company may "
                            f"sit on two.",
                    "evidence": [f"{s} / {c} is a real shelf on this board"]}
        return {"action": "place", "body": {"id": cid, "sector": s, "category": c},
                "says": f"Move their primary placement to {s} / {c}.",
                "evidence": [f"{s} / {c} is a real shelf on this board"]}

    if kind == "merge":
        name = intent["name"]
        want = re.sub(r"[^a-z0-9]+", "", name.lower())
        hits = [c for c in (companies or [])
                if want and want == re.sub(r"[^a-z0-9]+", "", (c.get("name") or "").lower())]
        if not hits:
            hits = [c for c in (companies or [])
                    if want and want in re.sub(r"[^a-z0-9]+", "", (c.get("name") or "").lower())]
        if len(hits) != 1:
            return {"error": f"{name!r} matches {len(hits)} companies on this board, "
                             f"so I cannot tell which one you mean"}
        other = hits[0]
        if other["id"] == cid:
            return {"error": "that is this same company"}
        return {"action": "merge",
                "body": {"keep": other["id"], "drop": cid},
                "says": f"Fold {company.get('name')} into {other.get('name')}, keeping "
                        f"{other.get('name')} and its name as an alias.",
                "evidence": [f"{other.get('name')} is on the board as {other['id']}"]}

    if kind == "no_site":
        return {"action": "suggest",
                "body": {"id": cid, "argument": "no website exists for this company"},
                "says": "Record that they have no website. Use the row's own "
                        "'No site exists' button to file it against the queue.",
                "evidence": []}

    if kind == "not_hiring":
        return {"action": "suggest",
                "body": {"id": cid, "argument": "reported as not hiring"},
                "says": "Record that as a note. Nothing here writes a hiring status: "
                        "'we saw nothing' and 'they told us' are different facts.",
                "evidence": []}

    if kind == "board":
        if probe is None:
            return {"error": "no probe available to read that page"}
        seen = probe(intent["url"])
        if seen.get("error"):
            return {"error": f"could not read that page: {seen['error']}"}
        ev = []
        host = seen.get("host") or _host(intent["url"])
        own = _same_site(intent["url"], company.get("website") or "")
        ev.append(f"the page is on {host}"
                  + (" - their own domain" if own else ", not their own domain"))
        if seen.get("kind") and seen.get("kind") != "html":
            ev.append(f"it carries {seen['kind']} markers")
            body = {"id": cid, "ats": {"type": seen["kind"], "ref": seen.get("ref")}}
            says = (f"Wire their board as {seen['kind']}"
                    + (f" ({seen.get('ref')})" if seen.get("ref") else "") + ".")
        else:
            titles = seen.get("titles") or []
            if not titles:
                return {"error": "that page has no job listings our reader can "
                                 "enumerate, so wiring it would record zero openings "
                                 "as a fact about them"}
            ev.append(f"{len(titles)} job titles read from it, first: {titles[0]!r}")
            body = {"id": cid, "ats": {"type": "html", "ref": intent["url"]}}
            says = f"Wire that page as their board; it lists {len(titles)} roles."
        if not own:
            ev.append("CHECK: an off-domain board wants a second look before it lands")
        return {"action": "set-board", "body": body, "says": says, "evidence": ev}

    return {"error": f"I do not know how to act on {kind!r}"}
