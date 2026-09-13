#!/usr/bin/env python3
"""Which companies' websites no longer belong to them?

    python3 scripts/site_identity.py              # the shortlist
    python3 scripts/site_identity.py --write      # into the admin's queue

FOUND BY AN AGENT, NOT BY THIS PIPELINE, which is why it exists. Asked to
write a description of ParkHub from ParkHub's own pages, an agent answered
"unsure" and said why: all seven cached urls for parkhub.com serve JustPark's
site - JustPark twelve times, ParkHub zero. It refused to describe the wrong
company. Nothing in the fetch path had noticed, and a writer that guessed
would have published a description of JustPark under ParkHub's name.

`find_websites.identifies()` asks this question at DISCOVERY, off a page's
title, meta and h1 - and it is the right gate there. It never runs again. A
domain that was theirs in March and is a casino in September passes every
check this repo has, because nothing re-asks.

THREE KINDS COME OUT AND THEY NEED OPPOSITE TREATMENT:

  SPAM / SQUATTED. watchtowerrobotics.com answers 200 with the title
  "LANGIT99: Situs Slot Gacor Terupdate Mahjong Ways 2 Gampang Jackpot".
  gwfathom.com answers "VPN Friendly BTC Crypto Casinos USA". The board links
  both. That is a public job board sending visitors to gambling spam under a
  vendor's name, and it is the only kind here that is not a judgement call.

  ACQUIRED. cartegraph.com names OpenGov, ecivis.com names Euna, dossier
  names AMCS. The link is arguably right - that IS where the product lives
  now - and the RECORD is wrong, which is the Acquisitions queue's question,
  not this one's. Proposed, never applied.

  UNREADABLE. A one-page JS app, an error page, a site whose name lives only
  in a logo. No finding at all - just a page we cannot read, which is a fact
  about the fetch.

A ZERO IS A SIGNAL, NOT A VERDICT. This counts and names; a person rules.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import admin                                                    # noqa: E402
import fetch_profiles as fp                                     # noqa: E402

OUT = "site_identity.json"

# Words a govtech vendor's site does not carry. Indonesian gambling spam is
# what actually turned up, twice, so the vocabulary is what those pages say.
SPAM = re.compile(r"\b(slot|casino|mahjong|togel|judi|maxwin|gacor|rtp|poker|"
                  r"betting|taruhan|situs|bandar|jackpot|spins?|sportsbook)\b", re.I)
SPAM_MIN = 5          # one mention is a coincidence; five is the page's subject

# Dropped before a name is matched: every vendor is a "Technologies" or a
# "Solutions", so those tokens identify nobody.
GENERIC = {"inc", "llc", "ltd", "corp", "corporation", "company", "co",
           "technologies", "technology", "solutions", "systems", "software",
           "group", "holdings", "labs", "services", "the", "and", "of",
           "international", "global", "usa", "us", "america", "north"}
STOP = set("""the and for with from that this what your you our are was were
has have had will can all not but they their there when where which who how
more most some any other into over under than then them been being about
after before also each such only own same too very just now new get see make
made use used using work works working help helps learn read view home about
contact careers login sign search menu skip content page site privacy policy
terms cookie cookies accept close open next back top let we us it is be as at
by on or an a in to of""".split())

# THE WORDS EVERY VENDOR SITE SHOUTS, and the reason the first version of this
# reported Cartegraph as being about "Management". A page that says Management
# 252 times and OpenGov 154 times is a page about OpenGov; the frequency
# ranking alone picks the product noun and buries the only token that answers
# the question. Same for ResourceX ("PRODUCT", "NAME" - literal unfilled
# template placeholders) and Syrinix ("Monitor", "Chlorine").
FURNITURE = set("""management managed software solutions solution platform
product products service services government governments public sector data
cloud security support customers customer overview features feature pricing
resources resource company contact careers blog news events webinar webinars
learn more demo request login signin signup free trial team teams partner
partners industry industries case studies study whitepaper ebook guide
technology technologies system systems tools tool app apps mobile web online
digital smart automated integration integrations api dashboard report
reports analytics insights training education school schools city cities
county counties state states agency agencies department departments
monitor monitoring monitors sensor sensors network networks water
error errors severity notice warning exception failed failure page
# LITERAL UNFILLED TEMPLATE PLACEHOLDERS, which is not a metaphor: resourcex.net
# serves the string NAME 252 times and PRODUCT 378, burying Tyler at 182 - the
# only token on the page that answers the question.
name names product title description placeholder lorem ipsum undefined
null none filename message string value default example sample test
based opens started explore type mass continue information
instagram facebook linkedin twitter youtube tiktok cookie cookies accept
english espanol francais deutsch""".split())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _core(name: str) -> list:
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name or "") if t]
    return [t for t in toks if t.lower() not in GENERIC] or toks


def names_itself(text: str, names: list) -> bool:
    """Does this page carry the company's name anywhere in its text?

    DELIBERATELY GENEROUS. The question is not "is this a good page about
    them", it is "is this page about them AT ALL". A single distinctive token
    counts, and every recorded alias counts as a name in its own right -
    a person answered that question already and this must not re-litigate it.
    """
    flat = _norm(text)
    for n in names:
        k = _core(n)
        if not k:
            continue
        if _norm("".join(k)) and _norm("".join(k)) in flat:
            return True
        if any(len(t) >= 4 and _norm(t) in flat for t in k):
            return True
    return False


def dominant(text: str, own: set) -> list:
    """The capitalised names this page DOES carry, most frequent first.

    What turns a blank into a finding. "We cannot see their name" and "this is
    somebody else's site, and here is whose" are different rows for a person.
    """
    cap = collections.Counter(
        w for w in re.findall(r"\b[A-Z][A-Za-z0-9]{3,}\b", text)
        if w.lower() not in STOP and w.lower() not in own
        and w.lower() not in FURNITURE)
    return cap.most_common(3)


# HOW OFTEN A NAME HAS TO APPEAR BEFORE IT IS THE PAGE'S SUBJECT. Measured
# against what the sweep actually returned: every genuine case names its new
# owner in the dozens or hundreds - Euna 294, AMCS 210, OpenGov 154, JustPark
# 84 - while the pages with nothing to say top out in single figures on a
# social-media link or a random token from a JS bundle.
OWNER_MIN = 8


def measure(companies: list) -> list:
    idx = fp.index()
    rows = []
    for c in companies:
        cid = c.get("id")
        e = idx.get(cid)
        if not e or e.get("unread"):
            continue
        rec = fp.load(cid)
        if not rec:
            continue
        pages = [p for p in (rec.get("about") or []) if p.get("text")]
        if not pages:
            continue
        text = "\n".join(p["text"] for p in pages)
        names = [c.get("name") or ""] + list(c.get("also_known_as") or [])
        if names_itself(text, names):
            continue
        own = {t.lower() for n in names for t in _core(n)}
        spam = len(SPAM.findall(text))
        named = dominant(text, own)
        top = named[0] if named else None
        # THREE KINDS, AND THE THIRD IS THE HONEST ONE. A page whose strongest
        # remaining name is a social link or a token out of a JS bundle is not
        # evidence that somebody bought them - it is a page we could not read.
        # Reporting it as an acquisition would send a person to rule on
        # nothing, which is how a queue teaches people to skim it.
        if spam >= SPAM_MIN:
            kind = "spam"
        elif top and top[1] >= OWNER_MIN:
            kind = "names_another_company"
        else:
            kind = "unreadable"
        rows.append({
            "id": cid, "name": c.get("name"), "website": c.get("website"),
            "fetched_on": rec.get("fetched_on"), "pages": len(pages),
            "spam_markers": spam,
            # THE LINK IS THE HARM. A stale cache on a company with no website
            # on file is a fact about data/site_pages/ and reaches no reader.
            "linked": bool(c.get("website")),
            "kind": kind,
            "names_instead": named,
        })
    rows.sort(key=lambda r: (-r["spam_markers"], not r["linked"], r["name"] or ""))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    companies = admin.read_companies()
    rows = measure(companies)
    spam = [r for r in rows if r["kind"] == "spam"]
    live = [r for r in spam if r["linked"]]
    print(f"{len(rows)} cached site record(s) never name the company they are "
          f"filed under, of {len(companies)} companies\n")
    if live:
        print(f"  {len(live)} SERVE GAMBLING SPAM AND ARE LINKED FROM THE BOARD:")
        for r in live:
            print(f"     {str(r['name'])[:28]:30} {str(r['website'])[:44]:46} "
                  f"{r['spam_markers']} markers")
    for r in spam:
        if not r["linked"]:
            print(f"  {str(r['name'])[:28]:30} spam, but no website on file - "
                  f"stale cache only, reaches no reader")
    other = [r for r in rows if r["kind"] == "names_another_company"]
    print(f"\n  {len(other)} name somebody else instead - the Acquisitions "
          f"queue's question, not this one's:")
    for r in other:
        who = ", ".join(f"{w}x{n}" for w, n in r["names_instead"]) or "(nothing)"
        print(f"     {str(r['name'])[:26]:28} -> {who[:54]}")
    dead = [r for r in rows if r["kind"] == "unreadable"]
    print(f"\n  {len(dead)} carry no name at all we can read - an error page, a "
          f"JS bundle, a name that lives only in a logo. NOT a finding about "
          f"the company:")
    for r in dead:
        print(f"     {str(r['name'])[:26]:28} {r['pages']}p  "
              f"fetched {r.get('fetched_on')}")
    if not a.write:
        print("\ndry run: nothing written")
        return 0
    bad = admin.save_decisions(OUT, {"rows": rows}, "site-identity",
                               why=f"{len(rows)} site(s) that do not name the "
                                   f"company on file, {len(live)} of them "
                                   f"gambling spam linked from the board",
                               by="site-identity", force=len(rows) > 25)
    if bad:
        print("  REFUSED:", bad)
        return 1
    print(f"\n  {len(rows)} row(s) written to data/{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
