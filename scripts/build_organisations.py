#!/usr/bin/env python3
"""Derive data/organisations.json — the body behind the events.

WHY THIS DID NOT EXIST
----------------------
conferences.json is one flat list of events. The organiser exists only as a
substring of the event name and the tag: there is no code, no url, no way to
say that three rows are all run by APA. That was fine while the catalogue was
139 hand-curated events. It is not fine now: 190 of 516 staged organisations
run more than one event, and APA alone runs a national conference, an online
edition, a policy meeting, several schools and 47 chapter conferences.

A reader looking at "NRPA Directors School" cannot get from there to "NRPA
Annual Conference", and a seller asking "what does NRPA run" has nowhere to
look.

DERIVED, NEVER AUTHORED
-----------------------
Every field here is read from a file that already holds it — conferences.json,
national_events.json, state_events.json. Nothing is invented and nothing is
written back to those files. Re-running this rebuilds it from scratch, which
is the property that lets it be regenerated on every build without a merge
question.

A CLASS OF BODIES IS NOT A BODY
-------------------------------
The registry says "State associations", "State affiliates", "Various" where it
means a class of organisation rather than a named one. Those rows are real
events and belong in the catalogue, but they are not an organisation with a
website and a history, and grouping ten unrelated state bodies under one code
would say they were. They carry `is_a_class: true` and a page should not
pretend otherwise.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "organisations.json"

# Codes the parser mints from a description rather than a name. Each is a
# CLASS of organisation - "the fifty state sheriffs' associations" - and the
# events under it are real while the body is not one body.
CLASS_CODES = {
    "STATEASSOCIATIONS", "STATEAFFILIATES", "VARIOUS", "STATEAGENCIES",
    "STATEDOTS", "STATEJUDICIARIES", "STATEAOCS", "STATESAGS", "STATEOFFICES",
    "STATELTAPCENTERS", "UNSPECIFIED", "FIRE", "STATE",
}

NOTE = (
    "Derived, never authored: every field is read from conferences.json, "
    "national_events.json or state_events.json and nothing is written back. "
    "Rebuilt from scratch on every run. `is_a_class` marks a code the registry "
    "used for a CLASS of body ('State associations') rather than a named one - "
    "the events are real, the organisation is not one organisation."
)


def _lead(tag: str) -> str:
    """The leading token of an event tag, which is how conferences.json names
    its organiser today - there being no field for it."""
    m = re.match(r"^([A-Za-z0-9&.'/-]{2,16})(?:\s|$)", (tag or "").strip())
    return m.group(1).upper() if m else ""


def _read(name: str, key: str) -> list:
    p = DATA / name
    if not p.exists():
        return []
    d = json.loads(p.read_text())
    return d.get(key, []) if isinstance(d, dict) else d


def build() -> dict:
    orgs: dict = {}

    def slot(code: str, name: str, *, is_org_name: bool = False) -> dict:
        """`is_org_name` says the caller read this from a field that names the
        ORGANISATION, not the event.

        conferences.json has no organisation field at all, so the catalogue
        pass can only offer an event name. Letting that compete on length made
        APA read "APA National Planning Conference" - an event - while
        national_events.json held "American Planning Association" three rows
        later. An event name is a last resort, never a tie-break.
        """
        code = (code or "").strip().upper() or "UNSPECIFIED"
        o = orgs.setdefault(code, {
            "code": code, "name": name or code, "url": None,
            "is_a_class": code in CLASS_CODES, "_named": False,
            "scopes": set(), "blocks": set(), "departments": set(),
            "events": [],
        })
        if not name:
            return o
        if is_org_name:
            if not o["_named"] or len(name) > len(o["name"]):
                o["name"], o["_named"] = name, True
        elif not o["_named"] and (o["name"] == code or len(name) > len(o["name"])):
            o["name"] = name
        return o

    # 1. the published catalogue. Its organiser is the tag's leading token,
    #    because there is no other field carrying it.
    for c in _read("conferences.json", "conferences"):
        tag = c.get("event_tag") or ""
        se = c.get("state_event") or {}
        code = (se.get("org_code") or _lead(tag)).upper()
        o = slot(code, c.get("conference") or "")
        if c.get("url") and not o["url"]:
            o["url"] = c["url"]
        if c.get("block"):
            o["blocks"].add(c["block"])
        if c.get("department"):
            o["departments"].add(c["department"])
        o["scopes"].add("SLED" if c.get("sled") is not False else "NON-SLED")
        o["events"].append({
            "source": "catalogue", "tag": tag,
            "name": c.get("conference"), "url": c.get("url"),
            "dates": c.get("dates"), "city": c.get("city"),
            "swept": bool(c.get("swept")),
            "exhibitor_url": c.get("exhibitor_url"),
            "published": True,
        })

    # 2. the staged national catalogue
    for r in _read("national_events.json", "events"):
        o = slot(r.get("org_code"), r.get("org_name") or "", is_org_name=True)
        if r.get("org_url") and not o["url"]:
            o["url"] = r["org_url"]
        if r.get("block"):
            o["blocks"].add(r["block"])
        if r.get("department"):
            o["departments"].add(r["department"])
        o["scopes"].add(r.get("scope") or "SLED")
        o["events"].append({
            "source": "staged", "key": r.get("key"),
            "name": r.get("event_name"), "url": r.get("org_url"),
            "status": r.get("status"), "harvest": bool(r.get("harvest")),
            "registry_status": r.get("registry_status"),
            "published": bool(r.get("promoted")),
        })

    # 3. the state chapters, which already model an organisation properly
    for r in _read("state_events.json", "events"):
        o = slot(r.get("org_code"), r.get("org_name") or "", is_org_name=True)
        if r.get("org_url") and not o["url"]:
            o["url"] = r["org_url"]
        if r.get("department"):
            o["departments"].add(r["department"])
        o["scopes"].add("SLED")
        o["events"].append({
            "source": "chapter", "key": r.get("org_code"),
            "name": r.get("event_name"), "url": r.get("org_url"),
            "status": r.get("status"), "geo": r.get("geo"),
            "parent_national": r.get("parent_national"),
            "published": bool(r.get("promoted")),
        })

    out = []
    for o in orgs.values():
        ev = o["events"]
        o["scopes"] = sorted(o["scopes"])
        o["blocks"] = sorted(o["blocks"])
        o["departments"] = sorted(o["departments"])
        o["event_count"] = len(ev)
        o["published_count"] = sum(1 for e in ev if e.get("published"))
        o["swept_count"] = sum(1 for e in ev if e.get("swept"))
        # A FLOOR NOBODY HAS READ IS NOT A FLOOR WITH NOTHING ON IT. The count
        # a page shows must be able to say which of those it means.
        o["harvest_count"] = sum(1 for e in ev if e.get("harvest"))
        o.pop("_named", None)
        out.append(o)
    out.sort(key=lambda o: (-o["event_count"], o["code"]))
    return {"note": NOTE, "organisations": out}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    payload = build()
    orgs = payload["organisations"]
    real = [o for o in orgs if not o["is_a_class"]]
    multi = [o for o in real if o["event_count"] > 1]
    print(f"  organisations   {len(orgs)}  ({len(real)} named, "
          f"{len(orgs) - len(real)} a class of body)")
    print(f"  running >1 event {len(multi)}")
    print(f"  with a url      {sum(1 for o in orgs if o['url'])}")
    print(f"  events reached  {sum(o['event_count'] for o in orgs)}")
    print("\n  biggest:")
    for o in real[:6]:
        print(f"    {o['code']:<14} {o['event_count']:>3} events, "
              f"{o['published_count']} published, {o['swept_count']} swept"
              f"   {o['name'][:38]}")
    if not a.write:
        print("\n  dry run. nothing written. pass --write")
        return 0
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
