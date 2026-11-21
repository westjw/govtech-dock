#!/usr/bin/env python3
"""Does this job board actually belong to the company we filed it against?

WHY THIS EXISTS

The owner opened a "Founding Account Executive" posting on this board and asked
whether it had anything to do with government. It did not: Concourse, an AI
company for corporate finance teams, backed by a16z. Seven of its postings were
on the public board and one was counted in the headline as a seller wanted.

Somebody found "Concourse" on the NIGP 2026 exhibitor list, searched for an
ATS, found jobs.ashbyhq.com/concourse and wired it. discover_ats.slug_matches
returned TRUE, because the slug does match the name - two entirely different
companies are called Concourse. A name-based check cannot tell them apart, and
that is the failure CLAUDE.md warns about by name: never point a company at
another company's board.

WHAT THIS CHECKS INSTEAD, AND WHY NOT THE OBVIOUS THING

The obvious test - "do the postings mention government?" - is a bad one. Plenty
of genuine govtech companies post "Senior Backend Engineer" with no public
sector vocabulary anywhere in it, so that test would flag hundreds of correct
boards and teach whoever reads the report to ignore it. A check that cries wolf
is worse than no check.

So this asks the boards to identify THEMSELVES. Nearly every board states the
employer's own name somewhere - greenhouse puts company_name on every job,
workable names the account, ashby and lever name the organisation on the
board's own hosted page - and that name is written by the employer rather than
inferred by us. Comparing it to what we have on file is a direct identity
claim rather than a guess.

Where a board states no name, this falls back to the domain the apply links
point at, which is weaker and is reported as weaker. Anything it cannot judge
comes back as "unknown", never as "fine": a check that cannot see is not a
check that passed.

TWO JUDGES, AND ONLY ONE OF THEM MAY WIRE A BOARD UNATTENDED (2026-11-18)

judge() is the forgiving one. It tells a person at the desk whether a board
looks like theirs, so it lets "Tyler" stand for "Tyler Technologies" - and,
because its test is a substring with no floor, it lets "Go" stand for
"Google" too. That is how it has always read the three types that named
their employer before 2026-11-18 (greenhouse, workable, jibe), and three
doors and a finder were built on it, so on those three it is left exactly
as it was.

IT IS NOT FORGIVING ABOUT THE NAMES IT COULD NOT SEE UNTIL THAT DAY. On the
twelve types board_says reads a name on since then, judge() used to answer
"unknown", and the doors treat "unknown" as not proven. Handing those new
names to the substring test would have had the page belt wire a Lever board
calling itself "Jacobs" to "StreetLight Data (Jacobs)", and
wire_embedded --write file it as clean with no person looking (blind review,
2026-11-18). So on those types judge() says "matches" only where proven()
would pass the name; a name that merely contains theirs, or sits inside it,
is still "unknown", as that board always was.

proven() is the strict one. The owner ruled (2026-11-18) that a found board
may be wired without him only when two separate checks agree: the company's
own website points to the board, and THE BOARD ITSELF NAMES THAT COMPANY AS
THE EMPLOYER. proven() is that second check, in code. It passes only a name
the board stated in its own words (board_says' "stated"), only when EVERY
name the board states is the company's, and only on equality once case,
punctuation, a legal form and a trailing country are set aside. Never a
substring, a prefix, a slug or a host. A parent's name, an operating entity's
name, the group's bare name where the record is one country's arm of it, a
board that gives another website as its own, a board that names nobody: each
is a refusal that says why, and a refusal is the owner's queue, not a wiring.

  python scripts/verify_boards.py [--limit 40] [--type greenhouse] [--all]
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import pathlib
import re
import sys
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ats  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Words a company name carries and a board never bothers to repeat. Stripped
# before comparison so "Tyler Technologies" still matches a board that calls
# itself "Tyler".
NOISE = {"inc", "inc.", "llc", "ltd", "limited", "corp", "corporation", "co",
         "company", "technologies", "technology", "tech", "software",
         "solutions", "systems", "group", "holdings", "labs", "the", "a"}


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def core(name: str) -> str:
    """The distinctive part of a name, with the corporate furniture removed."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w]
    keep = [w for w in words if w.lower() not in NOISE]
    return squash("".join(keep or words))


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


# ------------------------------------------------------------ the strict name
#
# FOR proven() ONLY. NOISE above is judge()'s list and is deliberately wide:
# it drops "Technologies", "Systems", "Group", so a person at the desk is not
# bothered by "Tyler" against "Tyler Technologies". That width is exactly what
# an unattended wiring cannot have. "Xplor" is not "Xplor Recreation" - it is
# the parent, and its board carries the parent's 22 requisitions. "Zonar" is
# not provably "Zonar Systems". So the strict list holds only what a REGISTERED
# name adds to a brand and nothing a second company could be told apart by.
LEGAL_FORMS = {"inc", "incorporated", "llc", "ltd", "limited", "corp",
               "corporation", "co", "company", "gmbh", "plc", "lp", "llp",
               "pty", "sa", "ag", "bv"}

# One country tag, at the very end of a name: a board that calls itself
# "Acusensus United States" is Acusensus, and "IDEMIA North America" is
# IDEMIA. Each tag keeps what it MEANS, because two names that both carry a
# tag and carry different ones ("Acme US", "Acme Canada") are two offices
# with two boards, not one name spelled twice. Longest first, so "United
# States" is read whole.
GEOGRAPHY = ((("united", "states"), "US"), (("north", "america"), "North America"),
             (("us",), "US"), (("usa",), "US"), (("americas",), "Americas"),
             (("canada",), "Canada"), (("uk",), "UK"))


def strict(name: str) -> tuple[str, str | None]:
    """A name with only its furniture removed: (the name, its country tag).

    Removed, and nothing else: case; punctuation and spacing; a leading
    "the"; the legal form at the END of the name (any number of them, so
    "Pty Ltd" and "Co., Ltd." both go); and one country tag at the end,
    which is handed back rather than thrown away.

    AT THE END ONLY, and that is the point of doing it by words rather than
    by letters. "SA Power Networks" and "Ag Leader" carry a legal-form word
    at the FRONT, where it is part of the name; and the "inc" inside "Brinc"
    is not a word at all (wire_embedded.LEGAL insists on a separator for the
    same reason: without one Brinc loses its own "inc" and is refused its
    own board). And the last word is never removed: a company called
    "Company" keeps its name.

    "US" IS A COUNTRY ONLY IN CAPITALS. Written "Us" it is the pronoun:
    Unite Us is a company on this map, and read as Unite plus a country it
    was the same company as a board calling itself "Unite" - as "Join" was
    "Join Us". "U.S." closes up to "US" first and so still counts.
    """
    # Full stops and apostrophes close up ("Inc." is Inc, "S.A." is SA,
    # "Moody's" is Moodys); every other mark is a space. Letters of any
    # alphabet are kept, so two names in another script cannot both squash
    # to nothing and so to each other.
    text = re.sub("[.'`" + chr(0x2019) + "]", "", str(name or ""))
    folded = re.findall(r"[^\W_]+", text.casefold())
    written = re.findall(r"[^\W_]+", text)
    if len(written) != len(folded):       # a script that folds into more words
        written = folded                  # has no capital "US" to be asked about
    words = list(zip(folded, written))
    if len(words) > 1 and words[0][0] == "the":
        words = words[1:]

    def shed_legal_form():
        while len(words) > 1 and words[-1][0] in LEGAL_FORMS:
            words.pop()

    place = None
    shed_legal_form()                     # "Acme USA, Inc." -> Acme USA
    for tag, means in GEOGRAPHY:
        if len(words) > len(tag) and tuple(w for w, _ in words[-len(tag):]) == tag:
            if tag == ("us",) and words[-1][1] != "US":
                continue                  # "Unite Us": a pronoun, not a place
            del words[-len(tag):]
            place = means
            break
    shed_legal_form()                     # "Acme Inc. USA" -> Acme
    return "".join(w for w, _ in words), place


def _same(board: tuple, ours: tuple) -> bool:
    """A board's strict name is this company's: the same name, and where OUR
    name carries a country, the board's carries that same one. An empty name
    equals nothing.

    WHICH SIDE THE COUNTRY IS ON DECIDES IT. A board that adds a country to
    our name ("Acusensus United States" for Acusensus) is one office of the
    company on file, and what it lists is theirs. The other way round is the
    parent's board: "Esri Canada" is a company of its own on this map, and a
    board that calls itself plain "Esri" is the group's, carrying the whole
    group's roles. A local arm's own site usually links the group's board,
    so the first check passes too and nothing else would have stopped it:
    all 17 names on file that end in a country passed on the bare name
    (blind review, 2026-11-18). Where the bare name really is theirs, a
    person adds it to also_known_as and it passes from then on.
    """
    return bool(board[0]) and board[0] == ours[0] and (
        ours[1] is None or board[1] == ours[1])


def _quoted(names: list) -> str:
    return ", ".join(f'"{n}"' for n in names[:4]) + (
        f" and {len(names) - 4} more" if len(names) > 4 else "")


def _site(url) -> str:
    """The host a website address names - lower case, no "www." - or ""
    when it names none."""
    if not isinstance(url, str) or not url.strip():
        return ""
    u = url.strip()
    try:
        host = urllib.parse.urlparse(u if "//" in u else "//" + u).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".").removeprefix("www.")
    return host if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host) else ""


def _website(company: dict, said: dict) -> tuple:
    """Does the website the BOARD gives as its employer's agree with the one
    on file? ("same" | "other" | None, the board's host, the host on file).

    Ashby and JazzHR boards state the employer's website beside its name,
    typed by the employer. The same site is the same host, or one sitting
    under the other (careers.acme.com and acme.com) - page_belt._same_site's
    rule. None when either side has no website to compare: nothing to
    compare is not a disagreement.
    """
    theirs, ours = _site(said.get("site")), _site(company.get("website"))
    if not theirs or not ours:
        return None, theirs, ours
    same = theirs == ours or theirs.endswith("." + ours) or ours.endswith("." + theirs)
    return ("same" if same else "other"), theirs, ours


def _named(company: dict, said: dict) -> dict:
    """The strict NAME test, which is proven() before it looks at the
    board's stated website. judge() asks this half alone, to tell a name
    that is theirs on a board that gives somebody else's website (a second
    look) from a name that only resembles theirs (not proven)."""
    ours = _clean(company.get("name"))
    aka = company.get("also_known_as")
    aliases = [_clean(a) for a in aka if _clean(a)] if isinstance(aka, (list, tuple)) else []
    names = said.get("names")
    names = [_clean(n) for n in names if _clean(n)] if isinstance(names, (list, tuple)) else []
    how = said.get("how") or "its answer carried no name"

    def no(why: str, name=None) -> dict:
        return {"ok": False, "why": why, "name": name}

    if not strict(ours)[0]:
        return no(f'there is no company name on file to hold the board to '
                  f'(the record says "{ours}")')
    if not names or said.get("stated") is not True:
        loose = names[0] if names else _clean(said.get("name"))
        if loose:
            return no(f'"{loose}" does not count as the board naming its '
                      f'employer ({how}): only a name the board states for '
                      f'itself, over the whole of it, says anything about '
                      f'"{ours}" - never one read off a slug, an address, a '
                      f'host or the first page of a longer board')
        return no(f'the board states no employer name ({how}), so nothing on '
                  f'it says it is "{ours}"')

    mine = [(k, strict(k)) for k in [ours] + aliases]
    whose = {n: next((k for k, key in mine if _same(strict(n), key)), None)
             for n in names}
    foreign = [n for n in names if whose[n] is None]
    if foreign:
        bad, theirs = foreign[0], strict(foreign[0])
        lead = (f'the board calls itself "{bad}"' if len(names) == 1 else
                f'the board states {len(names)} employer names '
                f'({_quoted(names)}) and "{bad}" is one of them')
        known = f'"{ours}"' + (" or any other name on file for them"
                               if aliases else "")
        base = strict(ours)[0]
        # the same name with the countries apart: every such name on file
        # carries a country (or _same would have passed it)
        twins = [key for _, key in mine if theirs[0] and theirs[0] == key[0]]
        if twins and theirs[1] is None:
            reason = (f"the name on file carries a country ({twins[0][1]}) "
                      f"and the board's does not, and a board under the bare "
                      f"name is the whole group's, not that one country's "
                      f"company")
        elif twins:
            reason = ("the same name with a different country on it, which "
                      "is a different office's board")
        elif theirs[0] and (theirs[0] in base or base in theirs[0]):
            reason = ("one name only contains the other, and a name inside "
                      "another name is never taken for the same company")
        else:
            reason = ("a different name, which is what a parent's, an "
                      "operating entity's or another company's board looks "
                      "like")
        return no(f"{lead}, which is not {known}: {reason}", bad)

    # EVERY NAME IS THEIRS, BUT UNDER TWO DIFFERENT COUNTRIES: "Acme US",
    # "Acme Canada" and "Acme UK" on one board is a group hiring for several
    # of its companies at once. Each name alone would pass for "Acme"; the
    # three together say this is not one company's board.
    places = [(n, strict(n)[1]) for n in names if strict(n)[1]]
    if len({p for _, p in places}) > 1:
        odd = next(n for n, p in places if p != places[0][1])
        return no(f'the board states {len(names)} employer names '
                  f'({_quoted(names)}) under different countries '
                  f'({", ".join(sorted({p for _, p in places}))}), "{odd}" '
                  f'among them: one board hiring for several countries\' '
                  f'companies is a group\'s board, and whether it is '
                  f'"{ours}"\'s is a person\'s call', odd)

    aside = ("the same name once case, punctuation, the legal form and a "
             "trailing country are set aside")
    first, match = names[0], whose[names[0]]
    if len(names) > 1:
        why = (f'the board states {len(names)} names ({_quoted(names)}) and '
               f'every one is "{ours}" or another name on file for it')
    elif match != ours:
        why = (f'the board calls itself "{first}", and "{match}" is on file as '
               f'another name for "{ours}"' + ("" if match == first else f": {aside}"))
    else:
        why = (f'the board calls itself "{first}" and the name on file is '
               f'"{ours}": ' + ("the same name" if first == ours else aside))
    return {"ok": True, "why": why, "name": first}


def proven(company: dict, said: dict) -> dict:
    """THE STRICT GATE: has this board, in its own words, named this company?

    Returns {"ok": bool, "why": str, "name": str or None}. `why` is written
    for the owner's queue and the journal, and quotes both names. `name` is
    the stated name the answer turned on: the board's name on a pass, the
    first name that is not theirs on a refusal, None when the board stated
    none.

    ok is True ONLY when all of these hold:

      1. board_says marked the answer `stated` - the employer said it through
         the board's own feed or hosted page, and over the whole board. A
         name worked out from a slug, a tenant id, the tail of an address or
         a host is somebody's guess, and the names on the first page of a
         longer board are not every name it states.
      2. the board states at least one name.
      3. EVERY name it states is this company's name or one of its
         also_known_as, compared with strict() and nothing looser. A country
         on OUR name must be on the board's too (_same), and the board's own
         names may not carry two different countries.
      4. where the board gives its employer's website (ashby, jazzhr) and
         there is one on file, they are the same site.

    WHAT IT REFUSES, each one a mistake this data has already made or nearly
    made: a parent's name (Prepared's own careers page links greenhouse/axon,
    some 500 requisitions, every one stamped "Axon"); an operating entity
    under another name (Circuit's board is registered to TFR Transit Inc and
    calls itself "Circuit Transit" - probably right, and "probably" is the
    owner's word to say, after which the name belongs in also_known_as so
    nobody doubts it twice); a name that merely CONTAINS theirs or sits
    inside it ("Go" in "Google", "Rave" in "Travelers" - the judge() test
    that has no floor); the group's bare name where the record is one
    country's arm ("Esri" for Esri Canada); a board stating several employers
    of which one is not them, which is what a parent's or a group's board
    looks like from outside; and ANOTHER COMPANY OF THE SAME NAME, which is
    where this module started - two companies are called Concourse, the
    board's name matched, and the website the board gave as its own was the
    other one's. The name cannot tell those two apart. The website can, so
    where a board states one it is held to the one on file: a refusal only,
    never a pass, because a website proves nothing about a name that is not
    theirs.

    It reads name, also_known_as and - only ever to refuse - website.
    parent, acquired, brands and board_owner are NOT consulted, on purpose:
    a company whose record says its parent is Axon is not thereby entitled
    to Axon's board.

    A refusal is not a verdict that the board is wrong. It is "a person has
    to look", and the caller sends it to the owner's queue with `why`.
    """
    company = company if isinstance(company, dict) else {}
    said = said if isinstance(said, dict) else {}
    v = _named(company, said)
    where, theirs, ours_site = _website(company, said)
    if where == "other" and v["ok"]:
        ours = _clean(company.get("name"))
        return {"ok": False, "name": v["name"], "why": (
            f'the board calls itself "{v["name"]}", which is a name on file '
            f'for "{ours}", but it gives its website as {theirs} and the one '
            f'on file is {ours_site}: two companies can carry one name, and '
            f'the website is what tells them apart')}
    if where == "same":
        # said for the record on a pass, and for the owner on a refusal: a
        # board under another name that gives THEIR website is usually theirs
        v = dict(v, why=f'{v["why"]}; the board gives its website as '
                        f'{theirs}, which is the one on file')
    elif where == "other":
        v = dict(v, why=f'{v["why"]}; the board also gives its website as '
                        f'{theirs}, where the one on file is {ours_site}')
    return v


# ------------------------------------------------- reading a board's own name
#
# WHERE EACH BOARD SAYS WHOSE IT IS. Measured on live boards, 2026-11-18,
# before a line of this was written (3 to 10 boards per vendor):
#
#   in the feed the reader already reads - no extra request
#     greenhouse       jobs[].company_name
#     workable         the account's name
#     jibe             jobs[].data.hiring_organization  (one page of 50 is
#                                                asked for; `stated` only when
#                                                that page is the whole board)
#     breezy           [].company.name          (an EMPTY board's feed is "[]",
#                                                so then the hosted page's
#                                                breezy-doc-title is asked)
#     recruitee        offers[].company_name
#     smartrecruiters  content[].company.name   (empty feed: the hosted page's
#                                                og:site_name)
#     gem              jobBoardExternal.teamDisplayName
#
#   on the board's own hosted page - a PAGE, so robots.txt is asked at every
#   hop, and a hop that leaves the board's host ends the read (_board_page)
#     ashby            window.__appData organization.name   (the feed carries
#     lever            <meta name="twitter:title">           no name at all on
#     bamboohr         <meta property="og:site_name">        these three)
#     rippling         __NEXT_DATA__ ...jobBoard.companyName
#     jazzhr           the JSON-LD Organization block
#     gusto            <title>Careers at X</title>, agreeing with the <h1>
#     paylocity        window.pageData ModuleTitle
#     icims            the meta description's own sentence  (`n` is the first
#                                                page only: n_is_partial)
#   (lever's page is read as far as the end of its <head> and no further;
#   ashby and jazzhr also state the employer's website, which proven() holds
#   to the one on file)
#
#   nowhere, so no probe and no pass
#     adp              the cid is opaque and every name-shaped field comes back
#                      blank to a public caller
#     workday          a name exists per POSTING (hiringOrganization), not per
#                      board, and one tenant states several ("Veritone, Inc."
#                      and "Broadbean Technology Limited" on one site). A
#                      sample of postings cannot say "every name"; not built.
#     oracle, html     no company on file / a page is not a board
#
# WHAT IS NEVER READ AS A NAME, because each of these sits right beside the
# name and is a different thing: the slug ("snd" is Social News Desk,
# "goswift" is Swiftly), the tail of a Paylocity address ("34ED-LLC" is
# Centegix), Breezy's friendly_id, SmartRecruiters' identifier ("Skyward1"),
# Rippling's jobBoard.title and Breezy's og:title (slogans the employer types:
# "Open Opportunities", "Come work with us."), BambooHR's <title> (the word
# "BambooHR" on every board), and an inactive JazzHR board's title ("JazzHR -
# Inactive Career Page").
#
# A PROBE THAT READS THE WRONG THING FAILS SAFE. proven() needs equality with
# the company's own name, so a vendor that redesigns its page gives us a name
# that matches nobody, and every board on it goes to the owner's queue until
# the probe is fixed. The dangerous direction would be a probe that ECHOES
# what we asked for - which is why none of these ever looks at the ref.

# HOW MUCH OF A PAGE IS READ, AND HOW. Every pattern below runs over a page
# somebody else serves, so each is written to be read in ONE pass: a tag is
# matched only as far as the next "<" or ">" ([^<>], never [^>]), and what
# lies between an opening tag and its closing tag is found by looking for
# the close once (_inside), never by a lazy (.*?) that starts again at every
# opening tag. The first versions did start again: 300 KB of unclosed <h1>
# held one board_says call for 77 seconds and 700 KB of unclosed ld+json
# script tags for 49 (blind review, 2026-11-18). Only the vendors' own hosts
# are ever read, so that was theirs to trigger and nobody else's - but a
# probe one odd page can stall is a batch that stops in the middle.
PAGE_MAX = 2_000_000     # characters of a page that any pattern is run over
HEAD_MAX = 64 * 1024     # bytes read of a page whose name is in its <head>
NAME_MAX = 200           # longer than this is not a name, whatever field held it

_META_TAG = re.compile(r"<meta\b[^<>]{0,4000}>", re.I)
_ATTR = re.compile(r"""([\w:-]{1,60})\s{0,8}=\s{0,8}(?:"([^"]*)"|'([^']*)')""")
_TAG = re.compile(r"<[^<>]*>")
# (how a block opens, how it closes) - read with _inside
_H1 = (re.compile(r"<h1\b[^<>]*>", re.I), re.compile(r"</h1\s*>", re.I))
_TITLE = (re.compile(r"<title\b[^<>]*>", re.I), re.compile(r"</title\s*>", re.I))
_LD_JSON = (re.compile(r'<script\b[^<>]*\btype="application/ld\+json"[^<>]*>', re.I),
            re.compile(r"</script\s*>", re.I))
_NEXT_DATA = (re.compile(r'<script id="__NEXT_DATA__"[^<>]*>'), re.compile(r"</script>"))
_GUSTO_NAME = re.compile(r"Careers at (\S.*)", re.I)
_ICIMS_NAME = re.compile(
    r"View all current positions at\s+(.*?)\s+across all categories and locations\.",
    re.I | re.S)
_JAZZHR_ROW = re.compile(
    r'<a\b[^<>]{0,400}?\shref="(https?://[^"<>]{0,300}applytojob\.com/apply/'
    r'[^"<>]{1,300})"[^<>]{0,400}>([^<]+)</a>')
_ICIMS_ROW = re.compile(
    r'<a\s+href="([^"<>]{1,400})"[^<>]{0,400}class="iCIMS_Anchor"[^<>]{0,400}'
    r'title="[^"<>]{1,400}"')
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _clean(name) -> str:
    """A stated name as a person reads it: one line, no stray spaces. Ashby's
    Miovision is stated as "Miovision " and Gusto's GUARDIAN RFID likewise."""
    return re.sub(r"\s+", " ", name).strip() if isinstance(name, str) else ""


def _dig(obj, *keys):
    """obj[k1][k2]..., or None the moment a level is not a dict. A vendor's
    payload is somebody else's shape and may change under us."""
    for k in keys:
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def _count(n, fallback: int) -> int:
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else fallback


def _label(ref) -> str:
    """A slug that is safe to put in front of a vendor's domain.

    The slug of a FOUND board comes out of somebody's web page. Dropped into
    f"https://{slug}.breezy.hr" unchecked, "evil.example/x#" sends this
    crawler wherever a page author liked. One DNS label, or no request.
    """
    s = str(ref or "").strip()
    if not _LABEL.fullmatch(s):
        raise ats.AtsError(f"not a board slug: {ref!r}")
    return s


def _path(ref) -> str:
    """A slug as one path segment (an Ashby slug on file has a space in it)."""
    s = str(ref or "").strip()
    if not s or "/" in s:
        raise ats.AtsError(f"not a board slug: {ref!r}")
    return urllib.parse.quote(s, safe="")


def _meta(page: str, key: str) -> str:
    """What the first <meta> tag named `key` says, or "".

    By attribute rather than by position, because the vendors agree on
    nothing: Lever writes name= then content=, Recruitee content= first,
    Gusto name= and value=.
    """
    key = key.lower()
    for tag in _META_TAG.findall(page or ""):
        if key not in tag.lower():
            continue
        attrs = {k.lower(): (a or b) for k, a, b in _ATTR.findall(tag)}
        if key in ((attrs.get("name") or "").lower(),
                   (attrs.get("property") or "").lower()):
            return _clean(html_lib.unescape(
                attrs.get("content") or attrs.get("value") or ""))
    return ""


def _inside(page: str, tags: tuple, most: int = 1) -> list:
    """What lies between an opening tag and the closing tag after it, for
    the first `most` such pairs - found in one pass over the page.

    The close is looked for ONCE, from where the opening tag ended, and the
    next opening tag is looked for after it. A page of opening tags that are
    never closed is therefore read once and yields nothing, where a pattern
    like <h1>(.*?)</h1> reads to the end of the page again from every one
    of them.
    """
    opens, closes = tags
    out, pos = [], 0
    while len(out) < most:
        m = opens.search(page or "", pos)
        c = closes.search(page, m.end()) if m else None
        if not c:
            break
        out.append(page[m.end():c.start()])
        pos = c.end()
    return out


def _embedded(page: str, marker: str):
    """The JSON object a page assigns right after `marker`, or None.

    raw_decode reads exactly one JSON value and stops, wherever it ends. A
    pattern like \\{.*?\\}; stops at the first "};" instead, which is inside
    the first job description that happens to contain one.
    """
    m = re.search(marker, page or "")
    if not m:
        return None
    return json.JSONDecoder().raw_decode(page, m.end())[0]


def _state(out: dict, names, how: str, whole: bool = True) -> bool:
    """Record what the board STATED about its employer. The only place
    `stated` is ever set, so nothing reaches proven() as a statement unless a
    probe read it off the board.

    `whole` is False when the names come from part of a board (the first
    page of a longer jibe site). They are still recorded - the desk's
    judge() has always read a name off that page - but they are NOT marked
    `stated`: a board is held to EVERY name it states, and nobody has read
    the rest of this one.
    """
    seen = sorted({_clean(n) for n in names} - {""})
    if not seen or any(len(n) > NAME_MAX for n in seen):
        return False
    out["names"], out["name"] = seen, seen[0]
    out["stated"], out["how"] = bool(whole), how
    return True


def _failure(exc: BaseException) -> tuple:
    """Why a board could not be read: (kind, the reason in words).

    THE KIND IS FOR THE DOOR, which has to treat three things differently
    and could not tell them apart while every one of them read "could not
    read the board (AtsError)":

      refused     robots.txt closes the page, or the server answered 401,
                  403 or 429. An answer for today. Never asked again in the
                  same run, and never a reason to file the board as wrong.
      gone        404 or 410: there is no board at that address.
      unreadable  everything else - the network failed, the server erred,
                  the answer was in a shape nobody recognises, the address
                  led off to another site, the reference could not be a
                  board's address at all. Not a wrong board and not an empty
                  one; the words say which.

    THE WORDS ARE FOR A PERSON: the status and the address, which is what
    the message of an AtsError already carries. Never the text of a library's
    own exception (a connection error prints an object's memory address),
    and never a Python class name where there is a sentence to give instead:
    the one place a class name is kept is an error nobody foresaw, where it
    is all there is to go on.
    """
    text = _clean(str(exc))[:200]
    if isinstance(exc, ats.RobotsRefused):
        # "disallowed" is the site's own word; a robots.txt that answered
        # with a server error only keeps us out until it answers properly
        closed = getattr(exc, "kind", "disallowed") == "disallowed"
        return ("refused" if closed else "unreadable"), text
    if isinstance(exc, ats.AtsError):
        status = re.match(r"HTTP (\d{3})\b", text)
        if status:
            code = int(status.group(1))
            return ("refused" if code in (401, 403, 429) else
                    "gone" if code in (404, 410) else "unreadable"), text
        if text.startswith("network error") and "robots.txt" not in text:
            cause = type(exc.__cause__).__name__ if exc.__cause__ else ""
            return "unreadable", "network error: " + (
                "it timed out" if "Timeout" in cause else
                "the certificate was not accepted" if "SSL" in cause else
                "no connection could be made" if "Connection" in cause else
                "the request failed")
        return "unreadable", text
    return "unreadable", (f"the answer was in a shape this reader does not know "
                          f"({type(exc).__name__})")


def said_transient(text) -> bool:
    """Do these words - an AtsError's message, or the detail a reader hands
    back - name a failure of the day? See `transient`."""
    t = _clean(str(text or ""))
    status = re.match(r"HTTP (\d{3})\b", t)
    if status:
        code = int(status.group(1))
        return code == 429 or 500 <= code <= 599
    if t.startswith("network error"):
        return True                 # no answer at all, robots.txt's included
    # robots.txt answered with a server error: kept out until it answers
    return bool(re.match(r"robots\.txt on \S+ answered HTTP 5\d\d\b", t))


def transient(exc) -> bool:
    """WAS THIS A FAILURE OF THE DAY? True only for a network error, a
    timeout, an HTTP 5xx, an HTTP 429, or a robots.txt that did not answer
    (no answer, or a 5xx); False for everything else.

    BY CAUSE, NOT BY `failed`'s LABEL. `failed` is three piles for a person
    to read, and its "unreadable" pile also holds failures that come back
    every day: a board whose page leads off to another host, an answer in a
    shape nobody recognises, a non-JSON feed, a 400. A door that asks again
    tomorrow only for what can answer tomorrow needs this instead - and its
    429, which `failed` calls "refused", is the most passing failure there
    is. A robots.txt that DISALLOWS is the site's own word and stays so.
    """
    if isinstance(exc, ats.RobotsRefused):
        return getattr(exc, "kind", "disallowed") != "disallowed"
    if isinstance(exc, ats.RateLimited):
        return True
    if isinstance(exc, ats.AtsError):
        return said_transient(str(exc))
    # the network failing where nothing wrapped it (a stream cut mid-read):
    # the HTTP library's own error, told by where it was raised from
    return type(exc).__module__.split(".")[0] in ("requests", "urllib3")


def _fell_short(out: dict, exc: BaseException, lead: str) -> None:
    """A HALF-READ BOARD HAS STATED NOTHING. Whatever a probe had noted
    before it fell over is withdrawn, so "could not read" can never arrive
    at proven() carrying a name; `failed` says what kind of failure it was,
    `transient` whether it was one of the day, and `how` says it in words."""
    kind, words = _failure(exc)
    out["names"], out["name"], out["stated"], out["site"] = [], None, False, None
    out["failed"], out["how"] = kind, f"{lead}: {words}"
    out["transient"] = transient(exc)


def _decoded(raw: bytes, resp) -> str:
    """The first bytes of a page, as text. A cut can land inside one UTF-8
    character, so the tail is trimmed back to a whole one before the charset
    is judged - or ats.best_charset would call an accented page Latin-1 for
    the sake of its last byte, and every name on it would read wrong."""
    for back in range(4):
        try:
            raw[:len(raw) - back].decode("utf-8")
        except UnicodeDecodeError:
            continue
        raw = raw[:len(raw) - back]
        break
    headers = getattr(resp, "headers", None) or {}
    enc = ats.best_charset(raw, headers.get("content-type", ""),
                           getattr(resp, "encoding", None))
    try:
        return raw.decode(enc or "utf-8", "replace")
    except LookupError:                       # a charset nobody has heard of
        return raw.decode("utf-8", "replace")


def _board_page(url: str, head: int = 0) -> str:
    """A board's own hosted page - and never a page its address led off to.

    A PAGE, so robots.txt is asked at every hop (owner's ruling, 2026-10-09):
    this is ats._page with one more condition on each hop, and a refusal is
    an answer, never a retry.

    THE CONDITION: every hop stays on the host that was asked. Measured
    2026-11-18 with a slug that does not exist: <slug>.applytojob.com/apply/
    answers 302 to info.jazzhr.com and on to www.jazzhr.com/job-seekers, a
    marketing page carrying a JSON-LD Organization block of its own - so
    every dead or mistyped JazzHR board "stated" that its employer was
    JazzHR. A vendor's own site is not a board, and what it says about
    itself is not an employer naming itself. The SAME HOST, not the same
    domain: BambooHR, Breezy, Recruitee and iCIMS all keep their marketing
    site on the domain their tenants' boards sit under. A redirect that
    stays on the host is followed as before (a Gusto board whose name
    segment changed still answers at its new address).

    `head` BYTES, where the name is in the page's <head>. Lever's board page
    is some 700 KB of inlined CSS with the board's name in its first
    kilobyte (jobs.lever.co/goswift: 714,503 bytes, measured 2026-11-18),
    and it was downloaded whole for one tag, once for each of the 33 Lever
    boards on file and again for every one found. Asked for as a stream and
    hung up on after `head` bytes - what ats.resolve and the robots.txt
    reader already do - it costs the first 64 KB. Any other page is read
    whole and cut at PAGE_MAX before a pattern sees it.
    """
    home = host_of(url)

    def stay(hop: str):
        # asked BEFORE the hop's robots.txt is, so a host a redirect names
        # is never sent anything (blind review, 2026-11-21)
        if host_of(hop) != home:
            raise ats.AtsError(f"{url} leads off to {host_of(hop)}, which is "
                               f"not the board")

    def one(hop: str):
        if head:
            return ats._one(hop, stream=True)
        return ats._get(hop, allow_redirects=False)

    resp = ats.follow(url, one, may=stay)
    if not head:
        return (resp.text or "")[:PAGE_MAX]
    try:
        # ats._one hands back whatever answered. The two things ats._get
        # does about that are done here: a 429 is honoured at the host gate
        # before it is reported, and anything but a 200 is not a page.
        if resp.status_code == 429:
            ats._back_off(url, resp)
        if resp.status_code != 200:
            raise ats.AtsError(f"HTTP {resp.status_code} for {url}")
        raw = b""
        for chunk in resp.iter_content(chunk_size=16384):
            raw += chunk
            if len(raw) >= head:
                break
    finally:
        resp.close()
    return _decoded(raw[:head], resp)


def _own_page(out: dict, url: str, read, head: int = 0) -> None:
    """Read the board's own hosted page for the name its feed does not state.

    Kept apart from the feed read on purpose. When the page cannot be read,
    what the feed already said - the count, the apply hosts - still stands,
    and `how` says which half failed.
    """
    try:
        read(_board_page(url, head), out)
    except Exception as exc:                        # noqa: BLE001 - see board_says
        _fell_short(out, exc, f"{out.get('how') or 'the board'}; its own page "
                              f"could not be read")


def _ashby_page(page: str, out: dict) -> None:
    # jobs.ashbyhq.com/<slug> assigns the whole board to window.__appData,
    # once per page, and its organization block carries the employer's
    # display name and the website the employer gave Ashby. The slug is in
    # there too (hostedJobsPageSlug) and is not the name: "snd" is Social
    # News Desk, "unison-software" is Unison.
    org = _dig(_embedded(page, r"window\.__appData\s*=\s*"), "organization")
    if _dig(org, "isDemoOrg") is True:
        out["how"] = "ashby marks this board a demonstration, not an employer"
    elif _state(out, [_dig(org, "name")],
                "ashby organization name, stated on the board's own page"):
        site = _dig(org, "publicWebsite")
        out["site"] = site if isinstance(site, str) and site.strip() else None
    else:
        out["how"] = "ashby apply hosts (the board's own page names no organization)"


def _lever_page(page: str, out: dict) -> None:
    # The display name the employer set in Lever ("Swiftly, Inc." where the
    # slug is goswift). twitter:title rather than <title>, which carries the
    # same name with whatever trailing space the employer typed, and rather
    # than og:title, which is the name plus the word "jobs".
    if not _state(out, [_meta(page, "twitter:title")],
                  "lever board name, stated on the board's own page"):
        out["how"] = "lever apply hosts (the board's own page names nobody)"


def _breezy(ref, out: dict) -> None:
    slug = _label(ref)
    rows = ats._json(ats._get(f"https://{slug}.breezy.hr/json"))
    if not isinstance(rows, list):
        raise ats.AtsError("unexpected breezy payload")
    out["n"] = len(rows)
    # company.name, never company.friendly_id: that one repeats the slug
    # ("vetro-fibermap-inc" where the name is "VETRO, Inc").
    if _state(out, [_dig(r, "company", "name") for r in rows],
              "breezy company name, stated on every position"):
        return
    if rows:
        out["how"] = "breezy positions name no company"
        return
    # AN EMPTY BOARD NAMES NOBODY IN ITS FEED - Polco's answers "[]", two
    # bytes. Its hosted page still says whose it is, in a tag of its own.
    # Not og:title, which is a slogan the employer types ("Join Our Team").
    out["how"] = "breezy feed is empty, so it names nobody"
    _own_page(out, f"https://{slug}.breezy.hr/", _breezy_page)


def _breezy_page(page: str, out: dict) -> None:
    if not _state(out, [_meta(page, "breezy-doc-title")],
                  "breezy board title, stated on the board's own page "
                  "(its feed is empty)"):
        out["how"] = "breezy feed is empty and the board's own page names nobody"


def _recruitee(ref, out: dict) -> None:
    slug = _label(ref)
    d = ats._json(ats._get(f"https://{slug}.recruitee.com/api/offers/"))
    offers = _dig(d, "offers")
    if not isinstance(offers, list):
        raise ats.AtsError("unexpected recruitee payload")
    out["n"] = len(offers)
    # An empty Recruitee board states nothing and gets no fallback: the
    # hosted page's og:site_name was seen on two boards only, and two is not
    # a pattern to wire companies on. It goes to the owner's queue.
    if not _state(out, [_dig(o, "company_name") for o in offers],
                  "recruitee company_name, stated on every offer"):
        out["how"] = ("recruitee offers name no company" if offers else
                      "recruitee board lists no offers, so it names nobody")


def _smartrecruiters(ref, out: dict) -> None:
    ident = _path(ref)
    d = ats._json(ats._get(f"https://api.smartrecruiters.com/v1/companies/"
                           f"{ident}/postings?limit=100&offset=0"))
    rows = _dig(d, "content")
    if not isinstance(rows, list):
        raise ats.AtsError("unexpected smartrecruiters payload")
    out["n"] = _count(_dig(d, "totalFound"), len(rows))
    # company.name, never company.identifier ("Skyward1", "IFS1"). It is the
    # ACCOUNT's name, so a product line on its parent's account reads as the
    # parent - "Xplor" for Xplor Recreation - which proven() refuses, rightly.
    if _state(out, [_dig(r, "company", "name") for r in rows],
              "smartrecruiters company name, stated on every posting read"):
        return
    if rows:
        out["how"] = "smartrecruiters postings name no company"
        return
    # NO POSTINGS, NO NAME, and this feed cannot tell a live empty board from
    # an identifier that never existed. The hosted careers page can.
    out["how"] = "smartrecruiters feed lists no postings, so it names nobody"
    _own_page(out, f"https://careers.smartrecruiters.com/{ident}",
              _smartrecruiters_page)


def _smartrecruiters_page(page: str, out: dict) -> None:
    if not _state(out, [_meta(page, "og:site_name")],
                  "smartrecruiters og:site_name, stated on the board's own "
                  "page (its feed is empty)"):
        out["how"] = ("smartrecruiters feed lists no postings and the board's "
                      "own page names nobody")


def _bamboohr(ref, out: dict) -> None:
    slug = _label(ref)
    # The list first: it is what the reader reads, it gives the count, and a
    # tenant that does not answer it is not a board to go looking further at.
    d = ats._json(ats._get(f"https://{slug}.bamboohr.com/careers/list"))
    if not isinstance(d, dict) or "result" not in d:
        raise ats.AtsError("unexpected bamboohr payload")
    rows = d.get("result") if isinstance(d.get("result"), list) else []
    out["n"] = _count(_dig(d, "meta", "totalCount"), len(rows))
    out["how"] = "bamboohr list (the feed states no employer name)"
    # The list names nobody. The careers page does, in og:site_name - and
    # NOT in its <title>, which is the bare word "BambooHR" on every board.
    _own_page(out, f"https://{slug}.bamboohr.com/careers", _bamboohr_page)


def _bamboohr_page(page: str, out: dict) -> None:
    if not _state(out, [_meta(page, "og:site_name")],
                  "bamboohr og:site_name, stated on the board's own page"):
        out["how"] = "bamboohr list (the board's own page names nobody)"


def _rippling(ref, out: dict) -> None:
    # The same page the reader reads. companyName is a field of its own,
    # separate from the board's title, which is free text ("Open
    # Opportunities", "Current Job Openings", "MCM Careers").
    page = _board_page(f"https://ats.rippling.com/{_path(ref)}/jobs")
    data = _inside(page, _NEXT_DATA)
    props = _dig(json.loads(data[0]) if data else None, "props", "pageProps")
    for q in _dig(props, "dehydratedState", "queries") or []:
        key = _dig(q, "queryKey")
        if isinstance(key, list) and "job-posts" in key:
            out["n"] = _count(_dig(q, "state", "data", "totalItems"), out["n"])
    board = _dig(props, "apiData", "jobBoard")
    if _dig(board, "isTest") is True:
        out["how"] = "rippling marks this a test board, not an employer's"
    elif not _state(out, [_dig(board, "companyName")],
                    "rippling companyName, stated on the board's own page"):
        out["how"] = "rippling page states no companyName"


def _jazzhr(ref, out: dict) -> None:
    # The same page the reader reads. The employer is a JSON-LD Organization
    # block, with the website the employer typed beside it. An INACTIVE board
    # has no block at all and is titled "JazzHR - Inactive Career Page" - so
    # the name is read from the block and never from the title, or every
    # switched-off board would say its employer is JazzHR.
    page = _board_page(f"https://{_label(ref)}.applytojob.com/apply/")
    out["n"] = sum(1 for _, t in _JAZZHR_ROW.findall(page)
                   if 4 <= len(html_lib.unescape(t).strip()) <= 90)
    for raw in _inside(page, _LD_JSON, most=20):
        try:
            block = json.loads(raw)
        except ValueError:
            continue
        if _dig(block, "@type") == "Organization" and _state(
                out, [block.get("name")],
                "jazzhr Organization block, stated on the board's own page"):
            site = block.get("url")
            out["site"] = site if isinstance(site, str) and site.strip() else None
            return
    out["how"] = "jazzhr page carries no Organization block (an inactive board has none)"


def _gem(ref, out: dict) -> None:
    # The one call the reader already makes. teamDisplayName is the name the
    # employer's Gem team carries; the reader uses it only to tell a live
    # empty board from a dead slug, and jobBoardExternal is null for a slug
    # that does not exist - which names nobody.
    data, _ = ats._gem_payload(ats._gem_slug(ref))
    posts = _dig(data, "oatsExternalJobPostings", "jobPostings")
    out["n"] = len(posts) if isinstance(posts, list) else 0
    board = _dig(data, "jobBoardExternal")
    if not isinstance(board, dict):
        out["how"] = "gem says no such board exists"
    elif not _state(out, [board.get("teamDisplayName")],
                    "gem teamDisplayName, stated by the board's own API"):
        out["how"] = "gem board states no team name"


def _gusto(ref, out: dict) -> None:
    # The same page the reader reads, and the weakest statement of the lot:
    # no field, only the title pattern "Careers at X". So it is believed only
    # when the page's first heading says the same X. A CLOSED board is titled
    # the bare word "Careers" and has no heading, and names nobody. The ref's
    # own name segment is never read: kaiden-incorporated-<uuid> is Kaiden AI
    # and now redirects to kaiden-ai-<uuid>.
    page = _board_page(ats.GUSTO_BASE + "boards/" + ats._gusto_seg(ref))
    out["n"] = len(set(ats._GUSTO_HREF.findall(page)))
    title, h1 = _inside(page, _TITLE), _inside(page, _H1)
    m = _GUSTO_NAME.fullmatch(_clean(html_lib.unescape(title[0]))) if title else None
    name = m.group(1) if m else ""
    head = _clean(html_lib.unescape(_TAG.sub(" ", h1[0]))) if h1 else ""
    if not name:
        out["how"] = "gusto page names nobody (a closed board's title is the bare word Careers)"
    elif head != name:
        out["how"] = "gusto page title and heading do not agree on a name"
    else:
        _state(out, [name], "gusto page title and heading, stated on the board's own page")


def _paylocity(ref, out: dict) -> None:
    # The board address on file IS the page. ModuleTitle is the name the
    # employer gave the board, keyed by the uuid: the same page fetched with
    # its tail cut off states the same title. It is NOT the tail - Centegix's
    # address ends "34ED-LLC" and Circuit's "TFR-Transit-Inc", while the
    # boards call themselves "Centegix" and "Circuit Transit".
    url = str(ref or "").strip()
    if host_of(url) != "recruiting.paylocity.com" or not url.startswith("http"):
        out["how"] = "paylocity ref is not a recruiting.paylocity.com board"
        return
    data = _embedded(_board_page(url), r"window\.pageData\s*=\s*")
    jobs = _dig(data, "Jobs")
    out["n"] = sum(1 for j in jobs if str(_dig(j, "JobTitle") or "").strip()) \
        if isinstance(jobs, list) else 0
    if not _state(out, [_dig(data, "ModuleTitle")],
                  "paylocity ModuleTitle, stated on the board's own page"):
        out["how"] = "paylocity page states no ModuleTitle"


def _icims(ref, out: dict) -> None:
    # The first page the reader reads. iCIMS writes one sentence of its own
    # into the meta description - "View all current positions at X across
    # all categories and locations." - and X is the ACCOUNT holder. Not the
    # <title>, which the employer rewrites on half of them. The account can
    # be a third party's: Selux's portal says "Council of Industry", an
    # association running a member's board, and that is the owner's call.
    ref = str(ref or "").strip()
    if ref.startswith("http"):
        # A FULL ADDRESS MUST BE AN iCIMS ONE. Every other probe names its
        # vendor's host itself; this one took the host from the ref, so an
        # address out of somebody's page sent the crawler to whatever host
        # it named and read what came back as an iCIMS portal.
        host = host_of(ref).lower()
        if not host.endswith(".icims.com"):
            out["how"] = "icims ref is not an icims.com board"
            return
        base = ref
    else:
        base = f"https://{_label(ref)}.icims.com/jobs/search?ss=1"
    page = _board_page(f"{base}{'&' if '?' in base else '?'}in_iframe=1&pr=0")
    # THE FIRST PAGE ONLY, AND THE ANSWER SAYS SO. The portal pages by
    # number (pr=0, pr=1, ...) and this reads one page, so `n` is what that
    # page showed - 50 on a portal of 50 and on a portal of 5,000 alike.
    # Anything that sizes a board (the owner's "skip the giants") must take
    # the count from the reader, which pages to the end.
    out["n"] = len(set(_ICIMS_ROW.findall(page)))
    out["n_is_partial"] = True
    m = _ICIMS_NAME.match(_meta(page, "description"))
    if not (m and _state(out, [m.group(1)],
                         "icims portal description, stated on the board's own page")):
        out["how"] = "icims page states no employer name"


# A type is here only once a probe for it has been measured on live boards
# and pinned by selftest (check_a_board_is_proven_only_by_its_own_stated_name).
# Everything else answers "no identity probe written for <kind>", states
# nothing, and so can never pass proven().
def _hirebridge(ref, out: dict) -> None:
    # companyname is on every row of the feed the reader reads, and
    # companyurl is the website the account gave Hirebridge (empty on
    # Tribute's), held to the one on file like ashby's. AN EMPTY FEED NAMES
    # NOBODY: a live board with nothing open and a company id nobody holds
    # both answer "[]" (cid 9999999, measured 2026-11-19).
    rows = ats.hirebridge_rows(ref)
    out["n"] = sum(1 for j in rows if ats._text(j.get("jobtitle")))
    out["hosts"] = {host_of(str(j.get("url") or "")) for j in rows[:8]}
    if not _state(out, [j.get("companyname") for j in rows],
                  "hirebridge companyname, stated on every row of the feed"):
        out["how"] = ("hirebridge rows name no company" if rows else
                      "hirebridge feed lists no jobs, so it names nobody (a company "
                      "id nobody holds answers the same way)")
        return
    sites = {ats._text(j.get("companyurl")) for j in rows} - {""}
    if len(sites) > 1:
        # every name the same and two websites: two companies sharing a name
        # on one account, which the website is there to tell apart. Not proven.
        out.update(stated=False, how=f"hirebridge rows give {len(sites)} different "
                                     f"websites ({', '.join(sorted(sites)[:3])}), so which "
                                     f"company the name is is not stated")
    elif sites:
        out["site"] = sites.pop()


def _applicantpro(ref, out: dict) -> None:
    # The board's /jobs/ page carries courierCurrentRouteData, JSON with
    # TWO names, and both are stated: career_site_name is the site the
    # employer set up, organization_name the ApplicantPro account behind it.
    # They differ on a sister brand's site - Black Mountain Software's board
    # is on Caselle's account (2026-11-19) - and a board is held to every
    # name it states, so such a board goes to the owner's queue. The page
    # gives no count, so the list it asks for is asked too, as the reader
    # does: two requests.
    host, page = ats.applicantpro_page(ref)
    # (the page tests "=== 'undefined'" before it assigns, so the
    # marker must be followed by the object itself)
    data = _embedded(page, r"courierCurrentRouteData\s*=\s*(?=\{)")
    out["hosts"] = {host}
    out["n"] = sum(1 for j in ats.applicantpro_jobs(host, page)
                   if isinstance(j, dict) and ats._text(j.get("title")))
    # The SITE's own name is required: without it the account's name alone
    # would prove a sister brand's site for the account (blind review,
    # 2026-11-21).
    if not _clean(_dig(data, "career_site_name")):
        out["how"] = "applicantpro page states no career site name"
    elif not _state(out, [_dig(data, "career_site_name"), _dig(data, "organization_name")],
                    "applicantpro career_site_name and organization_name, stated on "
                    "the board's own page"):
        out["how"] = "applicantpro page states no career site name"


def _teamtailor(ref, out: dict) -> None:
    # The feed the reader reads states the site's name as its own title, and
    # every posting repeats it as hiringOrganization.name; all of them are
    # stated, so a site posting for two companies shows both. A site with
    # nothing open still has its title (Heimdall Power, Corporater, Gecko,
    # Heja, 2026-11-19). It never states a website: hiringOrganization.sameAs
    # is the career site itself.
    host, d = ats.teamtailor_feed(ref)
    items = d["items"]
    out["n"] = sum(1 for i in items if ats._text(i.get("title")))
    out["hosts"] = {host}
    if not _state(out, [d.get("title")] + [_dig(i, "_jobposting", "hiringOrganization", "name")
                                           for i in items],
                  "teamtailor feed title and each posting's hiringOrganization, stated "
                  "by the career site"):
        out["how"] = "teamtailor feed states no name"


_PINPOINT_NAME = re.compile(r"Careers at (.+)")


def _pinpoint(ref, out: dict) -> None:
    # The feed names nobody. The board states its name in two places of its
    # own - its RSS channel title and its front page's og:title, both
    # "Careers at X" - and, like gusto's title and heading, X is believed
    # only when the two agree. An empty board still states it (Democracy
    # Works, 2026-11-19). Never the slug: "clearview" is Clearview AI.
    base = ats.pinpoint_base(ref)
    out["n"] = sum(1 for j in ats._json(ats._page(f"{base}/postings.json", same_host=True))
                   .get("data") or [] if isinstance(j, dict) and ats._text(j.get("title")))
    out["hosts"] = {host_of(base)}
    rss = ats._page(f"{base}/jobs.rss", same_host=True).text or ""
    m = re.search(r"<channel>\s*<title>(.*?)</title>", rss[:20000], re.S)
    a = _PINPOINT_NAME.fullmatch(_clean(html_lib.unescape(m.group(1)))) if m else None
    page = _board_page(base + "/")
    b = _PINPOINT_NAME.fullmatch(_clean(html_lib.unescape(_meta(page, "og:title"))))
    if not a or not b:
        out["how"] = "pinpoint board states no 'Careers at' name in its feed or on its page"
    elif a.group(1) != b.group(1):
        out["how"] = "pinpoint feed title and page title do not agree on a name"
    else:
        _state(out, [a.group(1)], "pinpoint RSS channel title and page og:title, stated by the board")


def _zohorecruit(ref, out: dict) -> None:
    # The page the reader reads: its meta input's org_info is the ACCOUNT -
    # its company_name and the website it gave - so a parent's board states
    # the parent (QuickLaunch's link goes to ElevenX Capital's). Never the
    # page's title (Cuseum's is "Jobs at Careers", the page's name) nor the
    # portal label ("exc" is ElevenX). A board set to hide the name states
    # nothing.
    _url, rows, meta = ats.zoho_board(ref)
    org = meta.get("org_info") if isinstance(meta.get("org_info"), dict) else {}
    out["n"] = sum(1 for j in rows if j.get("Publish") is True
                   and ats._text(j.get("Posting_Title") or j.get("Job_Opening_Name")))
    out["hosts"] = {host_of(str(ref))}
    if org.get("hide_company_name") is True:
        out["how"] = "zohorecruit board hides its company name"
    elif _state(out, [org.get("company_name")], "zohorecruit org_info.company_name, "
                                                "stated on the board's own page"):
        site = ats._text(org.get("website"))
        out["site"] = site or None
    else:
        out["how"] = "zohorecruit page states no company name"


def _comeet(ref, out: dict) -> None:
    # company_name on every row of the list the reader reads; a hosted
    # board's page also states the account's name and the website it gave,
    # and both are held to the record. An empty list names nobody unless the
    # board is a hosted page, which still names itself.
    _uid, rows, company = ats.comeet_board(ref)
    out["n"] = sum(1 for j in rows if ats._text(j.get("name")))
    out["hosts"] = {"www.comeet.com"}
    names = [j.get("company_name") for j in rows] + [company.get("name")]
    if not _state(out, names, "comeet company_name on every position" +
                  (", and the hosted board's own name" if company else "")):
        out["how"] = ("comeet board lists no positions, so it names nobody" if not rows
                      else "comeet positions name no company")
        return
    if ats._text(company.get("website")):
        out["site"] = ats._text(company.get("website"))


_JOBVITE_TITLE = re.compile(r"(.+?) Careers")


def _jobvite(ref, out: dict) -> None:
    # The list page names its career site only in its <title>, "<Name>
    # Careers" - a pattern around a name, like gusto's - so it is believed
    # only when a posting's own schema.org block names the same employer.
    # They can disagree: Absolute's title says "Absolute Security" and its
    # postings "Absolute Software" (2026-11-19), and such a board states
    # nothing. A board with no posting to ask states nothing either.
    _base, page = ats.jobvite_page(ref)
    rows = ats.jobvite_rows(page)
    out["n"] = len(rows)
    out["hosts"] = {"jobs.jobvite.com"}
    title = _inside(page, _TITLE)
    m = _JOBVITE_TITLE.fullmatch(_clean(html_lib.unescape(title[0]))) if title else None
    if not m:
        out["how"] = "jobvite page title names no career site"
        return
    try:
        posting = _board_page(rows[0][0])
    except ats.AtsError as exc:
        if "leads off" not in str(exc):
            raise
        # an embedded board's posting sends a browser to the employer's own
        # page, as its list does; asked for itself, it answers
        posting = _board_page(rows[0][0] + "?nl=1&fr=false")
    org = None
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
                            posting, re.S)[:3]:
        try:
            d = json.loads(block)
        except ValueError:
            continue
        h = d.get("hiringOrganization") if isinstance(d, dict) else None
        org = h.get("name") if isinstance(h, dict) else h if isinstance(h, str) else org
    if _clean(org) != _clean(m.group(1)):
        out["how"] = (f"jobvite page title says \"{_clean(m.group(1))}\" and its posting "
                      f"names \"{_clean(org) or 'nobody'}\": they do not agree")
        return
    _state(out, [m.group(1)], "jobvite page title and a posting's hiringOrganization, "
                             "stated by the career site")


_PROBES = {"breezy": _breezy, "recruitee": _recruitee,
           "smartrecruiters": _smartrecruiters, "bamboohr": _bamboohr,
           "rippling": _rippling, "jazzhr": _jazzhr, "gem": _gem,
           "gusto": _gusto, "paylocity": _paylocity, "icims": _icims,
           "hirebridge": _hirebridge, "applicantpro": _applicantpro,
           "teamtailor": _teamtailor, "pinpoint": _pinpoint,
           "zohorecruit": _zohorecruit, "comeet": _comeet, "jobvite": _jobvite}
# Every type whose board can state its employer at all - the only types a
# found board can be proven on. For a caller that wants to say "no probe for
# this ATS" without making a request to find out.
NAMES_ITS_EMPLOYER = frozenset(_PROBES) | {"greenhouse", "workable", "jibe",
                                           "ashby", "lever"}


def board_says(kind: str, ref) -> dict:
    """Ask the board who it belongs to.

    Returns {name, hosts, n, how, names, stated, site, kind, failed,
    transient, n_is_partial}:

      name    the employer name the board states, or None. Where it states
              several, the alphabetically first - kept for the callers that
              read one name. proven() reads `names`.
      hosts   the hosts its apply links point at (a set).
      n       how many postings it showed when asked.
      how     where the answer came from, in words a person can read.
      names   EVERY distinct employer name the board states, sorted; empty
              when it states none. A board that states two is saying
              something a single name hides (a parent's board, a group's).
      stated  True only when the names were stated by the employer through
              the board itself - its feed or its own hosted page - and are
              the names of the WHOLE board. Never a slug, a tenant id, the
              tail of an address or a host; and False, with the names that
              were read still listed, when only part of the board was read
              (the first page of a jibe site longer than a page).
      site    the employer's own website where the board states one (ashby,
              jazzhr), else None. proven() holds it to the website on file.
      kind    the type that was asked, as given.
      failed  None when the board was read; otherwise why it was not, for a
              caller that must act on it: "refused" (robots.txt, 401, 403,
              429 - never asked again in the same run), "gone" (404, 410),
              "unreadable" (anything else; `how` says what). Set too when
              the feed answered and only the board's own page did not.
      transient
              True only when the failure was one of the day - a network
              error, a timeout, an HTTP 5xx, an HTTP 429, or a robots.txt
              that did not answer - and False otherwise, a board that read
              included. By cause, not by `failed`: see transient().
      n_is_partial
              True when `n` counts only part of the board: on icims it is
              the first page. Take a board's size from the reader
              (add_company.verify), which pages to the end.

    NEVER RAISES. A board that cannot be read, or answers in a shape nobody
    recognises, comes back with no name and `how` saying so - and no name is
    never a pass.
    """
    out = {"name": None, "hosts": set(), "n": 0, "how": None,
           "names": [], "stated": False, "site": None,
           "kind": kind, "failed": None, "transient": False, "n_is_partial": False}
    try:
        if kind == "greenhouse":
            d = ats._json(ats._get(
                f"https://boards-api.greenhouse.io/v1/boards/{ref}/jobs"))
            jobs = d.get("jobs") or []
            out["n"] = len(jobs)
            # EVERY distinct name goes into `names`. A parent's hub stamps
            # different company_names on different jobs, and `name` alone -
            # the alphabetically first, which is all this used to keep -
            # lets a hub pass whenever that one happens to match.
            _state(out, [j.get("company_name") for j in jobs],
                   "greenhouse company_name, stated by the employer")
            out["hosts"] = {host_of(j.get("absolute_url", "")) for j in jobs[:8]}
        elif kind == "html":
            # AN HTML BOARD IS A PAGE, AND THE PAGE'S HOST IS THE EVIDENCE.
            # 829 companies sit on this type and none of them could be
            # verified at all - judge() answered "unknown" for every one, so
            # a careers URL pasted onto the wrong company was checked by
            # nobody. A listing on the company's own domain cannot be
            # somebody else's board; one on a third-party host is exactly
            # what wants a second look, and says so rather than passing.
            out["hosts"] = {host_of(str(ref))}
            out["n"] = len(ats.fetch_html_titles(str(ref)))
            out["how"] = "html board: the page's own host"
        elif kind == "jibe":
            # Jibe states the employer on every row, which is the strongest
            # signal any of these boards gives: hiring_organization is what
            # the careers site prints on the posting itself.
            d = ats._json(ats._get(f"https://{ref}/api/jobs?page=1&limit=50"
                                   if not str(ref).startswith("http")
                                   else f"{str(ref).rstrip('/')}/api/jobs?page=1&limit=50"))
            jobs = [j.get("data") or {} for j in (d.get("jobs") or [])]
            total = d.get("totalCount")
            out["n"] = _count(total, 0) or len(jobs)
            # ONE PAGE IS NOT THE BOARD. This asks for the first 50 postings,
            # as it always has, and the employer is stated per POSTING - so
            # on a site of 600 the first 50 saying "Acme" says nothing about
            # the sister companies further down, and "stated on every
            # posting" was a claim about 550 rows nobody read. The names are
            # the whole board's only when the page read covers the count the
            # site gives, or - where it gives none - when the page came back
            # short of full. Otherwise they are kept for the desk and NOT
            # marked stated. (No company on file is on jibe yet. One that
            # is, and is longer than a page, goes to the owner's queue until
            # this pages to the end.)
            counted = _count(total, 0) > 0
            whole = total <= len(jobs) if counted else len(jobs) < 50
            _state(out, [j.get("hiring_organization") for j in jobs],
                   "jibe hiring_organization, stated on every posting" if whole else
                   f"jibe hiring_organization on the first {len(jobs)} of "
                   f"{total if counted else 'an unknown number of'} postings; the "
                   f"rest were not read, so these may not be every name the "
                   f"board states", whole=whole)
            out["hosts"] = {host_of(j.get("apply_url", "")) for j in jobs[:8]}
        elif kind == "ashby":
            d = ats._json(ats._get(
                f"https://api.ashbyhq.com/posting-api/job-board/{ref}"))
            jobs = d.get("jobs") or []
            out["n"] = len(jobs)
            out["hosts"] = {host_of(j.get("applyUrl") or j.get("jobUrl") or "")
                            for j in jobs[:8]}
            out["how"] = "ashby apply hosts (the feed states no employer name)"
            # THE FEED NAMES NOBODY - it is {jobs, apiVersion} and nothing
            # else - so this branch could only ever answer "unknown": of the
            # 56 ashby boards in data/board_identity.json not one matched.
            # The board's own page names the organisation. One more request,
            # and only once the feed has answered: a slug whose feed is a 404
            # is not asked a second time.
            _own_page(out, f"https://jobs.ashbyhq.com/{_path(ref)}", _ashby_page)
        elif kind == "lever":
            d = ats._json(ats._get(
                f"https://api.lever.co/v0/postings/{ref}?mode=json"))
            jobs = d if isinstance(d, list) else []
            out["n"] = len(jobs)
            out["hosts"] = {host_of(j.get("applyUrl") or j.get("hostedUrl") or "")
                            for j in jobs[:8]}
            out["how"] = "lever apply hosts (the feed states no employer name)"
            # Same shape as ashby: the feed is a bare list of postings with
            # no company key anywhere, and the hosted list page names the
            # board. That page is ~700 KB of inlined CSS with the name in
            # its first kilobyte, so only its head is read (_board_page) -
            # once, and never per posting.
            _own_page(out, f"https://jobs.lever.co/{_path(ref)}", _lever_page,
                      head=HEAD_MAX)
        elif kind == "workable":
            d = ats._json(ats._get(
                f"https://apply.workable.com/api/v1/widget/accounts/{ref}?details=true"))
            out["n"] = len(d.get("jobs") or [])
            _state(out, [d.get("name") or (d.get("account") or {}).get("name")],
                   "workable account name, stated by the employer")
        elif kind in _PROBES:
            _PROBES[kind](ref, out)
        else:
            out["how"] = f"no identity probe written for {kind}"
    except Exception as exc:                            # noqa: BLE001
        _fell_short(out, exc, "could not read the board")
    out["hosts"] = {h for h in out["hosts"] if h}
    return out


# The types whose name judge() has read since the day it was written. Every
# other type that names its employer began doing so on 2026-11-18, when
# board_says learned to read it.
_ALWAYS_NAMED = frozenset({"greenhouse", "workable", "jibe"})


def judge(company: dict, said: dict) -> dict:
    """Compare what the board says about itself with what we have on file.

    THE FORGIVING TEST, AND WHERE IT STOPS. A name is "theirs" here when one
    name contains the other once the corporate furniture is dropped (core):
    "Tyler" for "Tyler Technologies". That is right for a person at the desk
    and wrong as proof, because it has no floor - "Jacobs" is inside
    "StreetLight Data (Jacobs)", and the core of "BS&A Software" is "bs",
    which is inside "Hobbs Group".

    It is kept, unchanged, for the answers it has always judged: the three
    types in _ALWAYS_NAMED, and an answer that does not say it was `stated`
    (a caller's own dict). On every other type this function answered
    "unknown" until 2026-11-18, and the doors were built on that: the page
    belt wires on "matches" and otherwise wants the slug to agree,
    wire_embedded files "matches" as clean with nobody looking. So a name
    this function could not see before is believed only on the strict test
    (_named, which is proven() before the website):

      matches   the strict test passes - the board's own name IS theirs.
      CHECK     it passes, but the board gives another website as its own.
                Two companies can carry one name; a person looks.
      unknown   the names only share ground. Exactly what that board
                answered before it could be read, with the reason in `why`.
      MISMATCH  the names share nothing: somebody else's board.
    """
    ours = core(company.get("name", ""))
    aliases = [core(a) for a in (company.get("also_known_as") or [])]
    site = host_of(company.get("website") or "").replace("www.", "")
    verdict, why = "unknown", said.get("how") or ""

    if said.get("name"):
        theirs = core(said["name"])
        near = bool(theirs) and (theirs == ours or theirs in aliases
                                 or theirs in ours or ours in theirs)
        kind = said.get("kind")
        loosely = (said.get("stated") is not True
                   or (isinstance(kind, str) and kind in _ALWAYS_NAMED))
        if not near:
            verdict = "MISMATCH"
            why = (f'board calls itself "{said["name"]}", '
                   f'we have it as "{company.get("name")}"')
        elif loosely:
            verdict = "matches"
            why = f'board calls itself "{said["name"]}"'
        else:
            named = _named(company, said)
            where, theirs_site, ours_site = _website(company, said)
            if not named["ok"]:
                verdict, why = "unknown", named["why"]
            elif where == "other":
                verdict = "CHECK"
                why = (f'board calls itself "{said["name"]}", but gives its '
                       f'website as {theirs_site}, and we have {ours_site}')
            else:
                verdict = "matches"
                why = f'board calls itself "{said["name"]}"'
    elif said.get("hosts"):
        # weaker: an apply host on the company's own domain is corroboration,
        # an ATS-owned host (ashby, lever) tells us nothing either way
        own = [h for h in said["hosts"] if site and site.split(".")[0] in h]
        generic = all(re.search(r"(ashbyhq|lever|greenhouse|workable|myworkday)",
                                h) for h in said["hosts"])
        if own:
            verdict, why = "matches", f"apply links point at {sorted(own)[0]}"
        elif generic:
            verdict = "unknown"
            why = ("every apply link is on the ATS's own domain, so the board "
                   "never says whose it is")
        else:
            verdict = "CHECK"
            why = f"apply links point at {sorted(said['hosts'])[:2]}"
    return {"verdict": verdict, "why": why, "postings": said.get("n", 0)}



# Government vocabulary, used ONLY as a reason to look - never as a verdict.
#
# Measured before trusting it: Concourse, the corporate-finance company that
# started all this, scores 1 hit in 37,540 characters of job descriptions.
# But Seneca - genuinely govtech, autonomous wildfire-suppression drones -
# scores ZERO in 23,540, because a drone engineer's job ad talks about drones.
#
# So a low score is not evidence a board is wrong. It is evidence nobody has
# checked. That distinction is the whole point: this produces a queue for a
# person, and the asymmetric error rule says a wrong removal is invisible and
# permanent while a wrong flag costs somebody two seconds.
GOV = re.compile(r"\b(government|public sector|municipal|municipalit\w*|county|"
                 r"citywide|state and local|SLED|agenc(y|ies)|constituent|"
                 r"resident|taxpayer|public safety|school district|K-12|"
                 r"procurement|permitting|civic|federal)\b", re.I)


def vocabulary(kind: str, ref) -> dict | None:
    """How often a board's own job descriptions talk about government.

    Only worth asking where the ATS hands descriptions over for free in the
    same call. Anything needing a request per posting is not worth 271 boards
    of somebody else's bandwidth for a signal this soft.
    """
    try:
        if kind == "ashby":
            d = ats._json(ats._get(
                f"https://api.ashbyhq.com/posting-api/job-board/{ref}"))
            texts = [j.get("descriptionPlain") or "" for j in d.get("jobs") or []]
        elif kind == "greenhouse":
            d = ats._json(ats._get(
                f"https://boards-api.greenhouse.io/v1/boards/{ref}/jobs?content=true"))
            texts = [ats.plain(j.get("content") or "") for j in d.get("jobs") or []]
        elif kind == "lever":
            d = ats._json(ats._get(
                f"https://api.lever.co/v0/postings/{ref}?mode=json"))
            texts = [j.get("descriptionPlain") or "" for j in (d if isinstance(d, list) else [])]
        else:
            return None
    except Exception:
        return None
    blob = " ".join(texts)
    if len(blob) < 2000:          # too little text to conclude anything from
        return None
    hits = len(GOV.findall(blob))
    return {"chars": len(blob), "hits": hits,
            "per1k": round(1000 * hits / len(blob), 2)}



def redirect_tell(c: dict) -> dict | None:
    """Does this company's own careers page hand you to somebody else?

    The universal acquisition signal, and the only one that works on the 187
    boards where the ATS states no owner. An acquired company's careers page
    stops being theirs: Prepared's links to Axon's greenhouse board, and the
    pattern this repo already records - Rave to Motorola, RoadBotics to
    Michelin - looks the same from outside.

    Compares the FINAL host after redirects against the company's own domain.
    An ATS host is not a tell, because everybody's board is on somebody else's
    domain; a DIFFERENT COMPANY'S domain is.
    """
    site = (c.get("website") or "").rstrip("/")
    if not site:
        return None
    own = host_of(site).replace("www.", "").split(".")[0].lower()
    for path in ("/careers", "/jobs", "/company/careers"):
        try:
            r = ats._get(site + path)
        except Exception:
            continue
        final = host_of(getattr(r, "url", "") or "")
        if not final:
            continue
        bare = final.replace("www.", "").split(".")[0].lower()
        if bare == own or ATS_HOST.search(final):
            return None                      # their own site, or any ATS
        return {"from": site + path, "to": final,
                "why": f"their careers page ends up on {final}, which is "
                       f"neither their domain nor an ATS"}
    return None


ATS_HOST = re.compile(
    r"(greenhouse|lever|ashbyhq|workable|bamboohr|myworkday|recruitee|breezy|"
    r"smartrecruiters|applytojob|paylocity|rippling|icims|jobvite|teamtailor|"
    r"workday|oraclecloud|taleo|successfactors|jazzhr)", re.I)


def slug_tell(c: dict) -> dict | None:
    """Is the board's slug somebody else's name?

    Prepared's careers page names the greenhouse slug "axon". A slug that
    shares nothing with the company's name is not proof of anything - plenty
    are legitimately odd - but combined with the redirect it is the shape of
    an acquisition, and on its own it is worth one look.
    """
    ref = (c.get("ats") or {}).get("ref")
    if not isinstance(ref, str) or ref.startswith("http") or len(ref) < 3:
        return None
    ours = core(c.get("name", ""))
    theirs = squash(ref)
    if not ours or not theirs:
        return None
    if theirs in ours or ours in theirs:
        return None
    for a in (c.get("also_known_as") or []):
        if theirs in core(a) or core(a) in theirs:
            return None
    return {"slug": ref, "why": f'the board slug is "{ref}", which shares '
                                f'nothing with "{c.get("name")}"'}


def audit(c: dict) -> dict:
    """One board on file, asked whose it is: the row main() prints and files.

    WHERE THE NAME SETTLES NOTHING, ASK WHAT THE BOARD TALKS ABOUT. That is
    every board that named nobody ("unknown") - and, since 2026-11-18, an
    ashby or lever board whose stated name matches. Those two types named
    nobody until then, so every one of them was asked this, and it is the
    only thing here that ever noticed Concourse: the name on that board IS
    the name on file, because two companies carry it. A matching name on
    them therefore still gets the vocabulary look, unless the board also
    gives the website we have on file as its own - which a second company
    of the same name cannot do.
    """
    kind, ref = c["ats"]["type"], c["ats"].get("ref")
    said = board_says(kind, ref)
    r = judge(c, said)
    homonym = (r["verdict"] == "matches" and bool(said.get("name"))
               and kind in ("ashby", "lever")
               and _website(c, said)[0] != "same")
    if r["verdict"] == "unknown" or homonym:
        v = vocabulary(kind, ref)
        if v:
            r["vocab"] = v
            if v["per1k"] < 0.05:
                r["verdict"] = "LOOK"
                r["why"] = (
                    f'board calls itself "{said["name"]}", the name we have, '
                    f'and {v["hits"]} government words in {v["chars"]:,} '
                    f'characters of its own job ads - two companies can share '
                    f'a name, and nothing else here says which one this is'
                    if homonym else
                    f'{v["hits"]} government words in {v["chars"]:,} '
                    f'characters of its own job ads - not proof it is '
                    f'the wrong board, but nothing corroborates it either')
    return {"id": c["id"], "name": c["name"], "type": kind, "ref": ref, **r}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--type", help="only this ats type")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pause", type=float, default=0.4)
    a = ap.parse_args()

    companies = json.loads((DATA / "companies.json").read_text())
    todo = [c for c in companies
            if (c.get("ats") or {}).get("type") in
            ("greenhouse", "ashby", "lever", "workable")]
    if a.type:
        todo = [c for c in todo if c["ats"]["type"] == a.type]
    if not a.all:
        todo = todo[:a.limit]

    print(f"checking {len(todo)} boards\n")
    rows, counts = [], {"matches": 0, "MISMATCH": 0, "CHECK": 0, "unknown": 0}
    for i, c in enumerate(todo, 1):
        r = audit(c)
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        rows.append(r)
        if r["verdict"] in ("MISMATCH", "CHECK", "LOOK"):
            print(f"  {r['verdict']:9} {c['name'][:30]:32} {r['why'][:82]}")
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)}", flush=True)
        time.sleep(a.pause)

    out = DATA / "board_identity.json"
    out.write_text(json.dumps(rows, indent=1) + "\n")
    print(f"\n{counts}")
    print(f"written to {out.relative_to(ROOT)}")
    print("\n'unknown' is not 'fine' - it means the board never said whose it "
          "is, or could not be read to say.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
