#!/usr/bin/env python3
"""Find the website a conference organisation publishes, and confirm it.

WHAT THIS IS NOT
----------------
It is not a domain guesser. It PROPOSES a small number of addresses an
association plausibly uses - nrpa.org from NRPA - and then requires the page
that answers to NAME THE ORGANISATION before writing anything down. A proposal
nothing confirms is discarded and the row stays `needs_url`, which is the
honest state it was already in.

That distinction is the whole design. Acronyms collide badly in this space:
aca.org could be the American Camp Association, the American Correctional
Association or the American Counseling Association, and all three are real
bodies that run conferences. A script that took the first 200 would file one
association's exhibitor floor under another's name.

find_event_directories.name_from_own_site() sets the same bar for chapters -
the site's own title or heading has to say who it is - and this is that rule
applied to national bodies, which have no parent listing to be found through.

WHY IT EXISTS AT ALL
--------------------
find_event_directories stage 1 resolves a chapter by walking its national
PARENT's published list of chapters. A national body IS the parent; there is
no listing above it. Stages 2 and 3 need only an org_url, so this fills the
one gap and everything downstream runs unchanged.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ats  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Words that carry no identity. "American Association of X" shares three of
# these with two hundred other bodies, so they cannot be what confirms one.
STOP = {
    "the", "of", "and", "for", "national", "american", "association", "assn",
    "society", "council", "conference", "institute", "federation", "alliance",
    "organization", "organisation", "int", "intl", "international", "us",
    "united", "states", "state", "inc", "group", "north", "america",
    "professional", "public", "center", "centre", "board", "commission",
}

TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
META = re.compile(
    r"""<meta\b[^>]*(?:property|name)\s*=\s*["'](?:og:site_name|og:title|description)["']"""
    r"""[^>]*content\s*=\s*["']([^"']*)["']""", re.I)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


def words(name: str, code: str = "") -> set:
    """The distinctive words in a name, EXCLUDING the acronym itself.

    The acronym cannot corroborate the acronym. When a body's recorded name
    is just its code - NCDA, ARTBA, Bobit - there is nothing to check the
    page against, and a rule that counted the code as a distinctive word
    confirmed ncda.org because the page said NCDA. It would have confirmed
    any of the three associations that answer at aca.org just as happily.
    """
    ac = norm(code)
    return {w for w in norm(name).split()
            if w not in STOP and len(w) > 2 and w != ac}


def candidates(code: str, name: str) -> list:
    """Addresses this body plausibly publishes at. Proposals, not answers."""
    out = []
    ac = (code or "").lower()
    if re.fullmatch(r"[a-z][a-z0-9]{1,7}", ac):
        out += [f"https://www.{ac}.org", f"https://{ac}.org",
                f"https://www.{ac}.com", f"https://{ac}.net"]
    slug = re.sub(r"[^a-z0-9]", "", norm(name))
    if 6 <= len(slug) <= 28 and slug != ac:
        out += [f"https://www.{slug}.org", f"https://{slug}.com"]
    seen, uniq = set(), []
    for u in out:
        h = u.split("//", 1)[-1].lstrip("www.")
        if h in seen:
            continue
        seen.add(h)
        uniq.append(u)
    return uniq[:5]


def page_names_it(html: str, code: str, name: str) -> tuple:
    """(confirmed, why). The page has to say who it is."""
    heads = []
    for rx in (TITLE, H1, META):
        heads += [m if isinstance(m, str) else m for m in rx.findall(html or "")]
    blob = norm(" ".join(re.sub(r"<[^>]+>", " ", h) for h in heads[:8]))
    if not blob:
        return False, "the page has no title, heading or description to read"
    full = norm(name)
    if full and len(full) > 12 and full in blob:
        return True, f"its own heading reads {full!r}"
    want = words(name, code)
    hit = {w for w in want if w in blob}
    ac = norm(code)
    has_ac = bool(ac) and re.search(rf"\b{re.escape(ac)}\b", blob) is not None
    # AN ACRONYM ALONE IS NOT AN IDENTITY. aca.org answers for at least three
    # associations that all run conferences; the distinctive words are what
    # separate them.
    # HOW MANY DISTINCTIVE WORDS WE HAVE, not a fixed two. "Council of State
    # Governments" reduces to {governments} once the words it shares with two
    # hundred other associations are dropped, so demanding two could never
    # confirm it. The rule is: every distinctive word we hold, up to two, must
    # be on the page - alongside the acronym.
    if has_ac and want and len(hit) >= min(2, len(want)):
        return True, f"names {ac.upper()} and {sorted(hit)[:3]}"
    if len(hit) >= 3:
        return True, f"names {sorted(hit)[:4]}"
    if has_ac and not want:
        # An acronym with no name behind it corroborates nothing. aca.org
        # answers for at least three associations that all run conferences.
        return False, f"only the acronym {ac.upper()}, and no name to corroborate it"
    return False, (f"names {sorted(hit)} of {sorted(want)[:4]}"
                   f"{' plus the acronym' if has_ac else ''} - not enough to be sure")


def resolve(code: str, name: str, log=print) -> dict:
    for url in candidates(code, name):
        try:
            html = ats._get(url).text
        except ats.AtsError as exc:
            log(f"      {url:<40} {str(exc)[:44]}")
            continue
        ok, why = page_names_it(html, code, name)
        log(f"      {url:<40} {'CONFIRMED' if ok else 'no'} - {why[:60]}")
        if ok:
            return {"url": url, "why": why}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-events", type=int, default=2,
                    help="only bodies running at least this many events")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    orgs = json.loads((DATA / "organisations.json").read_text())["organisations"]
    todo = [o for o in orgs
            if not o["is_a_class"] and not o.get("url")
            and o["event_count"] >= a.min_events]
    todo.sort(key=lambda o: -o["event_count"])
    todo = todo[:a.limit]
    print(f"resolving {len(todo)} organisation(s), {a.min_events}+ events each\n")

    found = {}
    for o in todo:
        print(f"  {o['code']:<16} {o['name'][:44]}")
        got = resolve(o["code"], o["name"])
        if got:
            found[o["code"]] = got
        print()

    print(f"confirmed {len(found)} of {len(todo)}")
    for k, v in found.items():
        print(f"  {k:<16} {v['url']}")
    if not a.write:
        print("\ndry run. nothing written. pass --write")
        return 0

    p = DATA / "national_events.json"
    payload = json.loads(p.read_text())
    # READ, EDIT IN PLACE, WRITE BACK - which is what keeps the "registry"
    # stamp on the file. Rebuilding the envelope here instead would drop it
    # and find_event_directories would refuse the registry outright.
    if payload.get("registry") != "national":
        print(f"refusing: {p.name} is stamped {payload.get('registry')!r}, "
              f"not 'national'", file=sys.stderr)
        return 1
    n = 0
    for r in payload["events"]:
        got = found.get(r.get("org_code"))
        if got and not r.get("org_url"):
            r["org_url"] = got["url"]
            r["org_url_why"] = got["why"][:200]
            r["status"] = "org_found"
            n += 1
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(p)
    print(f"\nwrote org_url onto {n} staged event row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
