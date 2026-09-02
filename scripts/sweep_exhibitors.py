#!/usr/bin/env python3
"""Fetch an exhibitor directory and stage it for conference_intake.

    python3 scripts/sweep_exhibitors.py --list          # what is ready
    python3 scripts/sweep_exhibitors.py --all           # look, write nothing
    python3 scripts/sweep_exhibitors.py --all --write   # stage them
    python3 scripts/sweep_exhibitors.py --tag "GFOA 2026" --write

WHAT WAS MISSING. conference_intake.py takes a FILE - a staged JSON list of
exhibitors - and turns it into companies, suppliers and candidates. Nothing
produced that file. All seven staged files on disk were assembled by hand,
which is why 45 conferences carry an exhibitor_url and only twelve events have
ever been swept. This is the fetcher between the two.

WHAT IT REFUSES TO DO. It does not decide whether an exhibitor is govtech, it
does not write to companies.json, and it does not mark a conference swept.
It fetches one page, reads the names off it, and writes a staging file a
person reviews before conference_intake runs. Every downstream gate stays
exactly where it was.

THE HARD PART IS TELLING AN EXHIBITOR FROM THE FURNITURE. An exhibitor
directory is a page of company names, and so is the navigation, the footer,
the sponsor tiers and the "browse by category" rail. The rules here are the
ones capture.js learned on job boards, because the problem is identical:

  ANCHORED nav matching, never substring. `^(home|about|register)$` and not
  `register`, or every "Registration Systems Inc" on the floor is deleted.
  This exact bug was introduced and caught in scripts/capture.js on
  2026-09-02; the guard against it lives in selftest.

  A NAME IS NOT A SENTENCE. Exhibitor names are short. A run of text past
  ~70 characters is a description, a tagline or a whole paragraph that
  happened to sit in a link.

  POSITION BEATS PATTERN. The list is whatever repeats. A page whose
  candidates are mostly unique one-offs is a page about a conference rather
  than a list of who is standing at it.

AND IT SAYS WHEN IT FAILED. `found: false` with a `why` is a real answer and
the staged file records it, because "we could not read this directory" and
"nobody exhibited" are opposite facts and this project files them apart. An
empty read is never written as an empty exhibitor list.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                              # noqa: E402

# ANCHORED. A substring match here deletes real companies: "register" would
# take out Registration Systems, "contact" would take out Contact Technologies.
NAV = re.compile(
    r"^(home|about( us)?|contact( us)?|register(ation)?|login|log in|sign in|"
    r"search|menu|back|next|previous|more|read more|learn more|view all|"
    r"see all|all exhibitors|exhibitors?|sponsors?|schedule|agenda|program|"
    r"sessions?|speakers?|hotels?|travel|venue|faq|help|privacy|terms|"
    r"cookies?|accessibility|sitemap|donate|join|renew|membership|news|"
    r"blog|events?|conference|annual (conference|meeting)|expo|trade show|"
    r"floor plan|exhibit(ing)?|become an exhibitor|book a booth|"
    r"[a-z]|[0-9]{1,4}|page [0-9]+)$", re.I)

# A tier label is not a company. These head the sections a directory is
# grouped into and would otherwise be filed as the biggest exhibitor there.
TIER = re.compile(
    r"^(platinum|gold|silver|bronze|diamond|titanium|premier|presenting|"
    r"supporting|contributing|patron|host|media|partner|sponsors?|"
    r"exhibitors?|vendors?)(\s+(sponsors?|partners?|level|tier))?$", re.I)

# NAVIGATION IS A PHRASE, NOT JUST A WORD. The first version of this filter
# was anchored single words and let "Become a Member", "About Webinars" and
# "Access the Agenda Here." through - IMLA staged 69 menu items as exhibitors
# and IAAO staged "Accessibility Statement" and "Author FAQ". What they have
# in common is that they START WITH A VERB OR A PREPOSITION. Company names
# almost never do; site chrome almost always does.
NAV_START = re.compile(
    r"^(about|become|access|view|browse|see|read|learn|join|renew|submit|"
    r"download|explore|discover|find|get|go|click|visit|watch|listen|"
    r"subscribe|follow|share|print|email|call|apply|order|shop|buy|"
    r"request|schedule|book|reserve|add|adding|manage|update|edit|create|"
    r"search|filter|sort|show|hide|skip|jump|return|continue|start|"
    r"how|why|what|when|where|who|our|your|my|the|this|these|all)\b",
    re.I)

# A breadcrumb, a menu path or a sentence. None of them is a company.
NOT_A_NAME = re.compile(r"\s/\s|\s>\s|\s\|\s|\.\.\.$|\?$", re.I)

# THE FOUR THINGS THAT GOT THROUGH ON THE FIRST REAL SWEEP, each seen in the
# staged output of a directory that was otherwise correct:
#
#   a template fragment the page never rendered - AWWA and NAHRO both yielded
#   "' + filterConfig.label + '", which is javascript that reached the markup
#   a booth number - "Booth #1158" repeated down AWWA's and NAHRO's lists
#   the list's own heading - NACUBO offered "2026 Exhibitor List"
#   a filter control - "All", on NLC and NatCon
#
# None is a judgement call and all four are exact, so they are filtered rather
# than left for a person to strike out 40 times.
JUNK = re.compile(
    r"^booth\s*#?\s*\d+|"                          # booth numbers
    r"['\"]\s*\+|\+\s*['\"]|\{\{|\$\{|function\s*\(|"    # template/script debris
    r"^\d{4}\s+(exhibitor|sponsor|vendor)|"        # "2026 Exhibitor List"
    r"^(all|none|other|misc|n/?a|tbd|tba)$", re.I)

# SITE SECTIONS THAT SURVIVED THE FIRST TWO FILTERS, read off GFOA's staged
# file after the chevron fix: "Bylaws", "Privacy Policy", "Staff Directory",
# "Member Communities", "Learning Dashboard", "Empty cart Cart", "Networking
# and Social Events", "Registration for 2027 Opens Fall 2026". Each is exact
# and none is a judgement, so they are filtered rather than struck out by hand.
SECTIONS = re.compile(
    r"^(bylaws|privacy (policy|statement)|terms of (use|service)|staff( directory)?|"
    r"leadership|board of directors|member(ship)? (communities|benefits|directory)|"
    r"learning (dashboard|center|centre)|my (account|dashboard|cart)|"
    r"(empty )?cart( cart)?|checkout|dashboard|newsletter|press( room)?|"
    r"media kit|advertis(e|ing)|exhibitor (prospectus|kit|guide|resources)|"
    r"sponsorship( opportunities)?|code of conduct|photo gallery|"
    r"networking( and| &)? social events|mobile app|plan your (trip|visit)|"
    r"things to do|cancellation policy|health (and|&) safety|attendee (info|faq|"
    r"information)|(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*"
    r"\b(event|session|reception|finale|keynote)s?|registration (for|opens|rates|"
    r"fees|info).*|call for (proposals|speakers|sessions|presentations))$", re.I)

MIN_NAMES = 8          # fewer than this is a page about an event, not a list
MAX_NAME = 70          # past this it is a description, not a name
NAV_SHARE = 0.30       # more chrome than this and the page is not a directory


# A CHEVRON IS NOT PART OF A NAME. GFOA's directory renders every menu item
# twice - once plain, once with a trailing "›" - and the trailing glyph beat
# both the dedupe key and the anchored NAV filter: "Exhibitors ›" is not
# "exhibitors", so it survived, twice. Stripped before anything else looks.
_ARROWS = "\u203a\u00bb\u2192\u25b8\u25be\u25b6\u276f\u00ab\u2039\u2190<>"


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", html_lib.unescape(s or "")).strip()
    return s.strip(_ARROWS + " ")


def host_chrome(host: str | None):
    """The association's own pages, named after itself.

    GFOA's directory offered "Careers at GFOA", "GFOA's Research & Consulting
    Center" and "GFOA's 121st Annual Conference" as exhibitors. A name that
    possesses the host, or sits "at" it, is the host's site and not a company
    on its floor. "Illinois GFOA" - a chapter that really does exhibit - has
    neither shape and is kept.
    """
    h = re.escape((host or "").strip())
    if not h:
        return None
    return re.compile(rf"\b{h}['\u2019]s\b|\bat {h}\b|^{h}\b.*\b(annual|conference|"
                      rf"summit|expo|meeting)\b", re.I)


def looks_like_a_name(s: str, host: str | None = None) -> bool:
    if not s or len(s) < 2 or len(s) > MAX_NAME:
        return False
    if NAV.match(s) or TIER.match(s) or SECTIONS.match(s):
        return False
    hc = host_chrome(host)
    if hc and hc.search(s):
        return False
    if s.count(".") > 2 or s.startswith(("http", "©", "#")):
        return False
    # A name has letters. A price, a booth number and a date do not qualify.
    if not re.search(r"[A-Za-z]{2}", s):
        return False
    # Sentences end in punctuation and contain verbs; names rarely do either.
    if s.endswith((".", "!", "?")) and len(s.split()) > 6:
        return False
    if NOT_A_NAME.search(s) or JUNK.search(s):
        return False
    # "Become a Member", "About Webinars", "Access the Agenda Here." - chrome
    # reads as an instruction, and an instruction opens with a verb. A real
    # name that happens to start this way (All Traffic Solutions, The Gordian
    # Group) is lost, which is the right side to err on: a company wrongly
    # dropped shows up at the next conference, a menu item wrongly kept is
    # published as an exhibitor.
    if NAV_START.match(s) and len(s.split()) > 1:
        return False
    return True


def host_of(tag: str | None) -> str | None:
    """"GFOA 2026" -> "GFOA". The association's short name, off the tag."""
    m = re.match(r"([A-Za-z&]{2,})", str(tag or ""))
    return m.group(1) if m else None


def harvest(page: str, host: str | None = None) -> list[dict]:
    """Names off an exhibitor directory, with a website where the page gave one.

    Reads anchors first - a directory almost always links each exhibitor to
    its own site or to a detail page - then falls back to repeated block
    elements for the directories that are plain text in a table.
    """
    out: dict[str, dict] = {}

    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href, inner = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        name = clean(inner)
        if not looks_like_a_name(name, host):
            continue
        site = href if href.startswith("http") else None
        # An anchor pointing back into the conference's own site is a detail
        # page, not the exhibitor's website. Recorded as None rather than
        # guessed - a wrong website is worse than a missing one.
        key = name.lower()
        if key not in out or (site and not out[key]["website"]):
            out[key] = {"name": name, "website": site}

    if len(out) < MIN_NAMES:
        for m in re.finditer(
                r"<(td|li|h[2-5]|strong|b)\b[^>]*>(.*?)</\1>", page, re.S | re.I):
            name = clean(re.sub(r"<[^>]+>", " ", m.group(2)))
            if looks_like_a_name(name, host) and name.lower() not in out:
                out[name.lower()] = {"name": name, "website": None}

    return list(out.values())


def suspicious(names: list[dict]) -> str | None:
    """Reasons to doubt this is an exhibitor list at all.

    A page that yields six names yielded its navigation. A page where every
    name appears once and none repeat is prose. Saying so is the point: an
    unreadable directory recorded as an empty one is a false absence, and the
    conference would then read as "nobody exhibited".
    """
    if len(names) < MIN_NAMES:
        return (f"only {len(names)} name(s) survived the filter, which is a page "
                f"about an event rather than a list of who is standing at it")
    words = Counter()
    for n in names:
        for w in n["name"].lower().split():
            words[w] += 1
    if names and words and words.most_common(1)[0][1] > len(names) * 0.6:
        top = words.most_common(1)[0][0]
        return (f"{top!r} appears in more than half the names, so these are "
                f"probably section headings or menu items rather than companies")
    return None


# A company's name carries a corporate marker surprisingly often. An
# association's own site sections carry abstract nouns instead.
CORP = re.compile(r"\b(inc|llc|corp|corporation|co|ltd|limited|plc|group|"
                  r"holdings|partners|associates|technologies|technology|"
                  r"systems|solutions|software|services|labs|works|company|"
                  r"consulting|engineering|industries|international|global|"
                  r"america|usa)\b\.?|&", re.I)
SECTION = re.compile(r"^(advocacy|awards|benefits|board of directors|committees|"
                     r"resources|membership|publications|education|training|"
                     r"leadership|governance|history|mission|staff|chapters|"
                     r"collections|authors?|abstracts?|posters?|papers?|"
                     r"proceedings|archives?|newsletters?|webinars?)\b", re.I)


def quality(names: list[dict]) -> tuple[str, str]:
    """How much this page looks like a floor rather than a site map.

    MEASURED ON THE SURVIVORS, and that wording is deliberate. The first
    version of this counted how many names matched the navigation patterns -
    after looks_like_a_name had already deleted every name that matched them.
    It read 0% on every page including the two that were pure navigation, and
    would have staged 58 IMLA menu items and 23 IAAO paper titles as
    exhibitors. A counter placed after the filter that removes its input
    measures the filter, which is the defect this codebase keeps finding.

    So this looks for what a real exhibitor list HAS rather than for what the
    filter already took out: corporate markers, and the absence of the
    abstract nouns an association names its own sections with.

    No single signal separates them cleanly and this does not pretend
    otherwise - it returns a grade and a sentence, and a person decides which
    files to run intake on. Measured against six directories read on
    2026-09-02: NLC 31% corporate, GFOA 19%, PSHRA 16%, 3CMA 7% all read as
    real floors; IAAO 0% corporate with 13% section nouns and NASCIO 2%/23%
    were the association's own site.
    """
    if not names:
        return "empty", "nothing survived the filter"
    corp = sum(1 for n in names if CORP.search(n["name"])) / len(names)
    sect = sum(1 for n in names if SECTION.match(n["name"])) / len(names)
    note = f"{corp:.0%} carry a corporate marker, {sect:.0%} name a site section"
    if sect >= 0.10 or corp < 0.05:
        return "doubtful", note + " - this reads as the association's own pages"
    if corp >= 0.15:
        return "good", note
    return "mixed", note + " - worth a look before running intake"


def merge_into(fresh: dict, out: pathlib.Path) -> bool:
    """Carry a staged file's rulings onto a re-sweep. False means do not write.

    A re-sweep used to rebuild the file from bare {name, website} rows, so
    every is_govtech flag classify had written - and any a person had set -
    was gone. And a directory that answered last week and refuses today
    overwrote the good list with the refusal. Both are the sweep destroying
    work it did not do. Flags travel by name; a refusal never replaces a
    find - it is printed, and the good file stays.
    """
    try:
        old = json.loads(out.read_text())
    except Exception:                                   # noqa: BLE001
        return True
    if old.get("found") and not fresh.get("found"):
        print(f"        kept: {out.name} holds {len(old.get('exhibitors') or [])} "
              f"name(s) from an earlier sweep and today's read refused - not "
              f"overwriting a find with a refusal ({str(fresh.get('why'))[:50]})")
        return False
    flags = {(e.get("name") or "").lower(): e for e in (old.get("exhibitors") or [])
             if "is_govtech" in e}
    for e in fresh.get("exhibitors") or []:
        was = flags.get((e.get("name") or "").lower())
        if was:
            for k in ("is_govtech", "is_govtech_why"):
                if k in was:
                    e[k] = was[k]
    return True


def restage(files: list) -> int:
    """Re-run the name filter over what is already staged. No network.

    For the day the filter learns something - a chevron, a tier label - and
    the 26 files on disk were read before it did. Duplicates that the fix
    now folds together keep whichever copy carried a ruling.
    """
    for f in files:
        d = json.loads(f.read_text())
        if not d.get("found"):
            continue
        before = d.get("exhibitors") or []
        kept: dict[str, dict] = {}
        dropped = []
        host = host_of(d.get("event_tag"))
        for e in before:
            name = clean(e.get("name"))
            if not looks_like_a_name(name, host):
                dropped.append(e.get("name"))
                continue
            key = name.lower()
            row = dict(e, name=name)
            if key in kept:
                # keep the ruling, whichever copy carried it, and any website
                have = kept[key]
                for k in ("is_govtech", "is_govtech_why"):
                    if k not in have and k in row:
                        have[k] = row[k]
                if not have.get("website") and row.get("website"):
                    have["website"] = row["website"]
                continue
            kept[key] = row
        after = sorted(kept.values(), key=lambda r: r["name"].lower())
        if len(after) == len(before) and not dropped:
            continue
        d["exhibitors"] = after
        d["restaged"] = (f"{len(before)} -> {len(after)} names after re-filtering "
                         f"on {dt.date.today().isoformat()}")
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1) + "\n")
        json.loads(tmp.read_text())
        tmp.replace(f)
        print(f"  {f.name[:40]:42} {len(before):4} -> {len(after):4}"
              + (f"   dropped: {', '.join(str(x)[:22] for x in dropped[:4])}" if dropped else ""))
    return 0


def ready(conferences: list, tag: str | None) -> list:
    out = [c for c in conferences if c.get("exhibitor_url")]
    if tag:
        out = [c for c in out if (c.get("event_tag") or "") == tag]
    return out


def sweep(conf: dict) -> dict:
    """One conference, one page, one staged result - found or honestly not."""
    url = conf["exhibitor_url"]
    staged = {
        "event_tag": conf.get("event_tag"),
        "conference": conf.get("conference"),
        "found": False,
        "source_url": url,
        "source_note": "",
        "exhibitors": [],
        "why": None,
    }
    try:
        page = ats._get(url).text
    except Exception as exc:                            # noqa: BLE001
        staged["why"] = f"{type(exc).__name__}: {str(exc)[:90]}"
        return staged

    names = harvest(page, host_of(conf.get("event_tag")))
    doubt = suspicious(names)
    grade, note = quality(names)
    if not doubt and grade == "doubtful":
        doubt = (f"this url is the association's own site rather than a list "
                 f"of who exhibits at it ({note})")
    if doubt:
        staged["why"] = doubt
        staged["source_note"] = (
            f"{len(page)//1024}KB read, {len(names)} candidate name(s) after "
            f"filtering. Not staged as exhibitors: {doubt}")
        return staged

    staged["found"] = True
    staged["quality"] = grade
    staged["exhibitors"] = sorted(names, key=lambda r: r["name"].lower())
    with_site = sum(1 for n in names if n["website"])
    staged["source_note"] = (
        f"Read from {url} by scripts/sweep_exhibitors.py. {len(names)} name(s), "
        f"{with_site} with a website the page itself linked. Quality: {grade} "
        f"({note}). Names only - "
        f"nothing here decides whether a company is govtech, and no website "
        f"was inferred where the page did not give one.")
    return staged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="what is ready to sweep")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tag", help="one event tag")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--restage", action="store_true",
                    help="re-filter the staged files on disk; no network")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if a.restage:
        return restage(sorted(DATA.glob("exhibitors_*.json")))

    conf = json.loads((DATA / "conferences.json").read_text())["conferences"]
    todo = ready(conf, a.tag)

    if a.list or not (a.all or a.tag):
        by = Counter(str(c.get("fetchability")) for c in todo)
        print(f"{len(conf)} conference(s); {len(todo)} carry an exhibitor_url")
        print("  fetchability:", dict(by))
        print(f"  {len(conf) - len(todo)} have no exhibitor_url and need one found\n")
        for c in todo:
            done = (DATA / f"exhibitors_{re.sub(r'[^A-Za-z0-9]+', '_', c.get('event_tag') or '')}.json")
            print(f"  {str(c.get('event_tag'))[:24]:26} {str(c.get('fetchability')):11} "
                  f"{'staged' if done.exists() else '':7} {str(c.get('exhibitor_url'))[:52]}")
        if not (a.all or a.tag):
            print("\n  --all to sweep them, --write to stage the results.")
        return 0

    if a.limit:
        todo = todo[:a.limit]

    found = missed = 0
    for c in todo:
        st = sweep(c)
        tag = st["event_tag"] or c.get("conference")
        if st["found"]:
            found += 1
            print(f"  {st.get('quality','?'):9} {str(tag)[:24]:26} "
                  f"{len(st['exhibitors']):4} name(s)")
        else:
            missed += 1
            print(f"  {'refused':9} {str(tag)[:24]:26} {str(st['why'])[:58]}")
        if a.write:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", str(tag)).strip("_")
            out = DATA / f"exhibitors_{safe}.json"
            if not merge_into(st, out):
                continue
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1) + "\n")
            try:
                json.loads(tmp.read_text())
            except json.JSONDecodeError:
                tmp.unlink(missing_ok=True)
                print(f"        refused: {out.name} did not parse", file=sys.stderr)
                continue
            tmp.replace(out)

    print(f"\n  {found} directory(ies) read, {missed} not")
    if a.write:
        print(f"  staged into data/exhibitors_*.json. NOTHING has reached "
              f"companies.json - run conference_intake.py on a file to do that.")
    else:
        print("  LOOKED ONLY. Re-run with --write to stage them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
