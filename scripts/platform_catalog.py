#!/usr/bin/env python3
"""The job-posting platforms SLED JOBS reads or meets, in one place.

WHY THIS EXISTS (the owner, 2026-11-22: are we "categorizing the posting
systems and how they work ... to build a system to better do the job")

What the repository knew about each platform was spread over the code - a
reader's docstring in ats.py, the finder's patterns in find_site_boards.py,
the identity probes in verify_boards.py, the public links in
build_board.board_url - and, for the twelve platforms measured live on
2026-11-19..21, over a research file in a scratch directory that a reboot
clears. data/platforms.json holds it now: one entry per platform, written
from that research and that code. Where the two disagree the code is what
runs, and the entry's notes say so. docs/PLATFORMS.md is generated from it.

  python3 scripts/platform_catalog.py                  # the check
  python3 scripts/platform_catalog.py --write          # regenerate the doc
  python3 scripts/platform_catalog.py --write --sweep <find_site_boards results>.json

THE CHECK FAILS when an ats.FETCHERS key has no entry; when an entry says
"read" and FETCHERS has no reader for it, or names a function FETCHERS does
not call; when an entry says anything else and FETCHERS reads it; when an
entry's identity probe is not the one verify_boards asks; when an entry is
malformed; when anything in the catalogue is a local path, an e-mail address
or a token-shaped run of hex (the repository is public, and the research
cited scratch paths and a Comeet board's token); and when docs/PLATFORMS.md
is not what --write would write from the same catalogue. It NOTES, and does
not fail on, a vendor find_site_boards.ELSEWHERE recognises that no entry
names, and a form an entry lists that the finder no longer has: those lists
move in other branches, and a merge should not go red over a tally.

THE LIVE COUNTS ARE NOT COMPARED. The doc says how many companies sit on each
type and how many of them have roles (data/companies.json), and what the last
sweep's sites pointed to (a find_site_boards results file, given with
--sweep). Those sit between <!-- live --> markers, and the comparison leaves
them out: companies.json moves every night, and a results file is not in the
repository. A --write without --sweep keeps the sweep counts the doc already
carries, with their date, so they outlive the file they came from.

Reads data/ and writes docs/PLATFORMS.md only; asks no board anything.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ats               # noqa: E402
import find_site_boards  # noqa: E402
import privacy           # noqa: E402
import verify_boards     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "data" / "platforms.json"
COMPANIES = ROOT / "data" / "companies.json"
DOC = ROOT / "docs" / "PLATFORMS.md"

ORDER = ("read", "build", "maybe", "skip", "seen")
HEADINGS = {"read": "Platforms we read",
            "build": "Researched: worth a reader, not built yet",
            "maybe": "Researched: worth a reader only on conditions",
            "skip": "Researched: cannot or should not be read",
            "seen": "Recognised by the finder, not researched"}
FIELDS = ("name", "status", "spot", "publishes", "robots", "employer", "quirks",
          "reader", "probe", "measured_on")
OPTIONAL = ("forms", "link", "notes", "research_verdict", "why")
PROSE = ("name", "spot", "publishes", "robots", "employer")
NAMES_LISTED = 12           # a platform with this many companies or fewer lists them

LIVE_OPEN, LIVE_CLOSE = "<!-- live -->", "<!-- /live -->"
_LIVE = re.compile(re.escape(LIVE_OPEN) + r".*?" + re.escape(LIVE_CLOSE), re.S)
_SWEEP_DATA = re.compile(r"<!-- sweep-data (\{[^\n]*?\}) -->")
_DAY = r"\d{4}-\d{2}-\d{2}(?:\.\.\d{4}-\d{2}-\d{2})?"
_MEASURED = re.compile(rf"unknown|{_DAY}(?:, {_DAY})*")
# What a scratch file's evidence path looks like, wherever a machine keeps one.
_LOCAL_PATH = re.compile(r"/private/|/Users/|/home/|/tmp/|/var/folders/|scratchpad/|~/")
# A Comeet token is 20-64 hex digits; so is anything else worth not publishing
# (a Paycom client key, a Paycor clientId). A uuid's hyphens keep it short of this.
_TOKEN = re.compile(r"[0-9A-Fa-f]{20,}")


def load(path: pathlib.Path = CATALOGUE) -> dict:
    return json.loads(path.read_text())


def probe_for(kind: str) -> str | None:
    """The identity probe verify_boards asks for a type, by function name: a
    probe of its own, board_says itself for the types it reads inline, or
    None where it has no probe (and a board can never be proven)."""
    fn = verify_boards._PROBES.get(kind)
    if fn is not None:
        return fn.__name__
    return "board_says" if kind in verify_boards.NAMES_ITS_EMPLOYER else None


def _strings(obj, where: str = ""):
    """(where, text) for every string in a JSON value, keys included."""
    if isinstance(obj, str):
        yield where, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{where}/{k}", str(k)
            yield from _strings(v, f"{where}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{where}[{i}]")


def _entry_problems(pid: str, e) -> list[str]:
    if not isinstance(e, dict):
        return [f"{pid}: an entry is an object"]
    out = [f"{pid}: no {f}" for f in FIELDS if f not in e]
    extra = sorted(set(e) - set(FIELDS) - set(OPTIONAL))
    if extra:
        out.append(f"{pid}: field(s) nobody reads: {', '.join(extra)}")
    status = e.get("status")
    if status not in ORDER:
        out.append(f"{pid}: status {status!r} is not one of {', '.join(ORDER)}")
    for f in PROSE:
        if f in e and not (isinstance(e[f], str) and e[f].strip()):
            out.append(f"{pid}: {f} is empty")
    for f in ("quirks", "notes"):
        v = e.get(f, [])
        if not (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)):
            out.append(f"{pid}: {f} is a list of sentences")
    forms = e.get("forms", {})
    if not (isinstance(forms, dict)
            and all(isinstance(v, str) and v.strip() for v in forms.values())):
        out.append(f"{pid}: forms maps each finder vendor id to what it means")
    for f in ("reader", "probe", "link", "research_verdict", "why"):
        if e.get(f) is not None and not (isinstance(e[f], str) and e[f].strip()):
            out.append(f"{pid}: {f} is text or null")
    if not (isinstance(e.get("measured_on"), str) and _MEASURED.fullmatch(e["measured_on"])):
        out.append(f"{pid}: measured_on {e.get('measured_on')!r} is 'unknown', a day, "
                   f"a window (day..day) or a list of them")
    if status in ("build", "maybe", "skip") and not str(e.get("why") or "").strip():
        out.append(f"{pid}: a {status!r} entry says why")
    if e.get("research_verdict") not in (None, "build", "maybe", "skip"):
        out.append(f"{pid}: research_verdict is build, maybe or skip")
    return out


def check(cat: dict | None = None, doc: str | None = None) -> tuple[list[str], list[str]]:
    """(problems, notes). A problem fails the check; a note is said and passes.
    `doc` is docs/PLATFORMS.md's text, compared when given."""
    cat = load() if cat is None else cat
    bad: list[str] = []
    notes: list[str] = []
    platforms = cat.get("platforms") if isinstance(cat, dict) else None
    if not isinstance(platforms, dict) or not platforms:
        return ["data/platforms.json holds no platforms"], notes
    for pid, e in platforms.items():
        bad.extend(_entry_problems(pid, e))
    shaped = not bad

    # A reader nobody catalogued, and an entry that says what the code does not.
    for kind in sorted(ats.FETCHERS):
        if kind not in platforms:
            bad.append(f"ats.FETCHERS reads {kind!r} with {ats.FETCHERS[kind].__name__} "
                       f"and data/platforms.json has no entry for it")
    for pid, e in sorted(platforms.items()):
        if not isinstance(e, dict):
            continue
        fn, status, reader = ats.FETCHERS.get(pid), e.get("status"), e.get("reader")
        name = fn.__name__ if fn is not None else None
        if status == "read" and name is None:
            bad.append(f"{pid} says 'read' and ats.FETCHERS has no reader for it")
        elif status == "read" and reader != name:
            bad.append(f"{pid} names its reader {reader!r}; ats.FETCHERS reads it "
                       f"with {name}")
        elif status != "read" and name is not None:
            bad.append(f"{pid} says {status!r}, and ats.FETCHERS reads it with "
                       f"{name}: it is 'read'")
        elif status != "read" and reader is not None:
            bad.append(f"{pid} says {status!r} and names a reader, {reader!r}")
        want = probe_for(pid)
        if e.get("probe") != want:
            bad.append(f"{pid} names its identity probe {e.get('probe')!r}; verify_boards "
                       + (f"asks {want!r}" if want else "has none for it"))

    # The repository is public.
    for where, s in _strings(cat):
        if _LOCAL_PATH.search(s):
            bad.append(f"{where or '/'}: a local path ({_LOCAL_PATH.search(s).group(0)}...)"
                       f" - nothing on this machine means anything in the repository")
        if privacy.addresses(s):
            bad.append(f"{where or '/'}: an e-mail address")
        if _TOKEN.search(s):
            bad.append(f"{where or '/'}: a run of {len(_TOKEN.search(s).group(0))} hex "
                       f"digits, the shape of a token or a client key")

    # The finder's tally, said and not failed on (see the module's docstring).
    vendors = {v for v, _hint, _rx in find_site_boards.ELSEWHERE}
    owners = {**find_site_boards._NO_BOARD_IN_IT, **find_site_boards.TENANT_FORMS}
    named = set(platforms)
    for pid, e in sorted(platforms.items()):
        forms = (e.get("forms") or {}) if isinstance(e, dict) else {}
        named |= set(forms)
        for f in sorted(forms):
            if f not in vendors:
                notes.append(f"{pid} lists the form {f!r}, which find_site_boards.ELSEWHERE "
                             f"does not have")
            elif owners.get(f, pid) != pid:
                notes.append(f"{pid} lists the form {f!r}; the finder files it under "
                             f"{owners[f]!r}")
    for v in sorted(vendors - named):
        notes.append(f"find_site_boards.ELSEWHERE recognises {v!r}, and no entry names it "
                     f"(as its id or among its forms)")

    if doc is not None:
        if shaped:
            bad.extend(doc_problems(cat, doc))
        else:
            bad.append("docs/PLATFORMS.md was not compared: the catalogue does not read")
    return bad, notes


def masked(text: str) -> str:
    """The doc with what is between each pair of live markers taken out."""
    return _LIVE.sub(LIVE_OPEN + LIVE_CLOSE, text.replace("\r\n", "\n"))


def doc_problems(cat: dict, doc: str) -> list[str]:
    if not doc:
        return ["docs/PLATFORMS.md is missing: python3 scripts/platform_catalog.py --write"]
    want, have = masked(render(cat)).splitlines(), masked(doc).splitlines()
    if want == have:
        return []
    for a, b in zip(want, have):
        if a != b:
            break
    else:
        a, b = (want[len(have)] if len(want) > len(have) else "(the end)",
                have[len(want)] if len(have) > len(want) else "(the end)")
    return [f"docs/PLATFORMS.md is not what --write writes from data/platforms.json (the "
            f"live counts aside): it reads {b[:90]!r} where --write writes {a[:90]!r}. "
            f"Run python3 scripts/platform_catalog.py --write"]


# ------------------------------------------------------------- the counts

def on_file(companies: list) -> dict:
    """Companies per ats.type, how many of them have roles on file, and names."""
    types: dict = {}
    for c in companies:
        kind = (c.get("ats") or {}).get("type") or "unknown"
        row = types.setdefault(kind, {"companies": 0, "with_roles": 0, "names": []})
        row["companies"] += 1
        row["with_roles"] += bool((c.get("hiring") or {}).get("roles"))
        row["names"].append(str(c.get("name") or c.get("id") or "?"))
    as_of = max((str((c.get("hiring") or {}).get("checked") or "") for c in companies),
                default="")
    return {"as_of": as_of or "an unknown day", "types": types}


def swept(results) -> dict:
    """The finder's own run summary (find_site_boards.summary), as numbers: for
    each type, how many companies' own sites point to a board on it; for each
    platform nothing reads, how many sites name it; for each form of a platform
    we read that names no board, how many."""
    companies = results.get("companies") if isinstance(results, dict) else None
    if not isinstance(companies, dict) or not all(isinstance(r, dict) for r in companies.values()):
        raise SystemExit("--sweep: not a find_site_boards.py results file "
                         "({generated, mode, companies: {id: record}})")
    points, no_reader, forms = collections.Counter(), collections.Counter(), collections.Counter()
    for r in companies.values():
        for kind in {str(c.get("type")) for c in r.get("candidates") or []}:
            points[kind] += 1
        for v in {str(e.get("vendor") or "").split(":")[0] for e in r.get("elsewhere") or []}:
            is_form = v in find_site_boards.TENANT_FORMS or v in find_site_boards._NO_BOARD_IN_IT
            (forms if is_form else no_reader)[v] += 1
    return {"generated": str(results.get("generated") or "an unknown time"),
            "mode": str(results.get("mode") or "an unknown mode"),
            "companies": len(companies),
            "pointed": sum(1 for r in companies.values() if r.get("candidates")),
            "points_to": dict(points.most_common()),
            "no_reader_for": dict(no_reader.most_common()),
            "forms": dict(forms.most_common())}


def carried(doc: str) -> dict | None:
    """The sweep counts an earlier write left in the doc, or None."""
    m = _SWEEP_DATA.search(doc or "")
    try:
        got = json.loads(m.group(1)) if m else None
    except ValueError:
        return None
    if not (isinstance(got, dict)
            and all(isinstance(got.get(k), dict) for k in ("points_to", "no_reader_for", "forms"))
            and all(isinstance(got.get(k), str) for k in ("generated", "mode"))
            and all(isinstance(got.get(k), int) for k in ("companies", "pointed"))):
        return None
    return got


# ------------------------------------------------------------- the doc

# CATALOGUE TEXT IS WRITTEN AS TEXT, NOT AS MARKDOWN. It is full of address
# shapes and robots rules: "<slug>" is an HTML tag to GitHub and vanishes,
# "Disallow: */JobBoard/*/Styles" turns into emphasis, "__NEXT_DATA__" into
# bold, and a pair of "~" into strikethrough. So those are escaped, and an
# underscore only where it could start or end emphasis (at a word's edge):
# fetch_html_titles stays readable in the raw file.
_MD_SPECIAL = re.compile(r"([\\<*~])")
_MD_EDGE_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")


def _md(text) -> str:
    return _MD_EDGE_UNDERSCORE.sub(r"\\_", _MD_SPECIAL.sub(r"\\\1", str(text)))


def _finder_kinds() -> set:
    """The types find_site_boards can name a board of (its patterns, and Jibe
    by its script host): only these can have a count of sites pointing to them."""
    return {p.kind for p in find_site_boards.BOARDS} | {"jibe"}


def _anchor(name: str) -> str:
    """The id GitHub gives a heading: lower case, punctuation gone, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", name.lower()).replace(" ", "-")


def _ordered(platforms: dict) -> list[tuple[str, dict]]:
    rank = {s: i for i, s in enumerate(ORDER)}
    return sorted(platforms.items(),
                  key=lambda kv: (rank.get(kv[1].get("status"), len(ORDER)),
                                  str(kv[1].get("name") or kv[0]).lower(), kv[0]))


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _owned(pid: str, e: dict) -> list[str]:
    """The finder vendor ids an entry answers for: its own id and its forms."""
    return [pid] + sorted(e.get("forms") or {})


def _live_line(pid: str, e: dict, live: dict | None, sweep: dict | None) -> str:
    if live is None:
        bits = ["On file today: not counted in this write."]
    else:
        row = live["types"].get(pid)
        if not row:
            bits = ["On file today: no company."]
        else:
            said = f"On file today: {_n(row['companies'], 'company', 'companies')}"
            if row["companies"] <= NAMES_LISTED:
                said += " (" + ", ".join(_md(n) for n in sorted(row["names"], key=str.lower)) + ")"
            bits = [f"{said}, {row['with_roles']} of them with roles."
                    if row["companies"] > 1 else f"{said}, {row['with_roles']} with roles."]
    if sweep:
        parts = []
        if pid in _finder_kinds():
            k = sweep["points_to"].get(pid, 0)
            parts.append("1 company's own site pointed to a board on it" if k == 1 else
                         f"{k} companies' own sites pointed to a board on it")
        for v in _owned(pid, e):
            k = sweep["no_reader_for"].get(v) or sweep["forms"].get(v)
            if k:
                parts.append(f"{_n(k, 'site', 'sites')} named it as `{v}`")
        bits.append(f"The last sweep ({sweep['generated'][:10]}): "
                    + ("; ".join(parts) if parts else "did not meet it") + ".")
    return " ".join(bits)


def _counts_block(cat: dict, live: dict | None, sweep: dict | None) -> list[str]:
    platforms = cat["platforms"]
    out = [LIVE_OPEN, ""]
    if live is None:
        out += ["Not counted in this write.", ""]
    else:
        out += [f"From data/companies.json as last refreshed {live['as_of']}. **On file** is how "
                f"many companies have that `ats.type`; **with roles** is how many of them have "
                f"roles on file (`hiring.roles` is not empty: the AE-type roles the last "
                f"refresh found).", "",
                "| Type | On file | With roles |", "|---|---:|---:|"]
        rows = sorted(live["types"].items(), key=lambda kv: (-kv[1]["companies"], kv[0]))
        for kind, row in rows:
            if kind == "unknown":
                label = "`unknown` (no board on file)"
            elif kind in platforms:
                label = f"[`{kind}`](#{_anchor(platforms[kind].get('name') or kind)})"
            else:
                label = f"`{kind}` (no entry)"
            out.append(f"| {label} | {row['companies']} | {row['with_roles']} |")
        out.append("")
    if not sweep:
        out += ["No sweep counts: give a find_site_boards.py results file with --sweep.", ""]
    else:
        def said(counts: dict) -> str:
            return ", ".join(f"`{k}` {v}" for k, v in counts.items()) or "none"
        out += [f"**The last sweep**: a `find_site_boards.py` results file generated "
                f"{sweep['generated']} in {sweep['mode']} mode, over "
                f"{_n(sweep['companies'], 'company', 'companies')} (a sweep covers the "
                f"companies it was given, not the whole map). "
                f"{_n(sweep.get('pointed', 0), 'company', 'companies')} had a board we read "
                f"named on their own site.", "",
                f"- **Points to** (companies whose own site names a board on it): "
                f"{said(sweep['points_to'])}",
                f"- **No reader for** (platforms nothing here reads, as the finder's summary "
                f"counts them; `other` is a job frame from a host nothing here knows): "
                f"{said(sweep['no_reader_for'])}",
                f"- **Forms of platforms we read** that name no board: {said(sweep['forms'])}",
                "", "<!-- sweep-data " + json.dumps(sweep, sort_keys=True) + " -->", ""]
    out.append(LIVE_CLOSE)
    return out


def _section(pid: str, e: dict, live: dict | None, sweep: dict | None) -> list[str]:
    status = e.get("status")
    head = [f"`{pid}`", f"**{status}**"]
    if e.get("research_verdict") and status == "read":
        head.append(f"researched as {e['research_verdict']}")
    head.append(f"reader `ats.{e['reader']}`" if e.get("reader") else "no reader")
    head.append(f"identity probe `verify_boards.{e['probe']}`" if e.get("probe")
                else "no identity probe")
    head.append(f"measured {e.get('measured_on')}")
    out = [f"### {_md(e.get('name') or pid)}", "", " · ".join(head), ""]
    if e.get("why"):
        out += [f"**Why {e.get('research_verdict') or status}.** {_md(e['why'])}", ""]
    out += [f"**How to spot it.** {_md(e.get('spot'))}", ""]
    if e.get("forms"):
        out += ["**Forms the finder tallies apart** (`find_site_boards.ELSEWHERE`):", ""]
        out += [f"- `{f}`: {_md(t)}" for f, t in sorted(e["forms"].items())] + [""]
    out += [f"**How it publishes.** {_md(e.get('publishes'))}", ""]
    if e.get("link"):
        out += [f"**Public link** (`build_board.board_url`). {_md(e['link'])}", ""]
    out += [f"**robots.txt.** {_md(e.get('robots'))}", "",
            f"**Where it names its employer.** {_md(e.get('employer'))}", ""]
    if e.get("quirks"):
        out += ["**Quirks.**", ""] + [f"- {_md(q)}" for q in e["quirks"]] + [""]
    if e.get("notes"):
        out += ["**Notes.**", ""] + [f"- {_md(q)}" for q in e["notes"]] + [""]
    out += [LIVE_OPEN, "", _live_line(pid, e, live, sweep), "", LIVE_CLOSE, ""]
    return out


def render(cat: dict, live: dict | None = None, sweep: dict | None = None) -> str:
    """docs/PLATFORMS.md. Deterministic: the same catalogue and counts give
    the same bytes, and only what sits between live markers depends on the
    counts."""
    platforms = cat["platforms"]
    ordered = _ordered(platforms)
    by = collections.Counter(e.get("status") for _pid, e in ordered)
    out = ["# Job-posting platforms", "",
           "<!-- Generated by scripts/platform_catalog.py from data/platforms.json. Edit the "
           "JSON, then run: python3 scripts/platform_catalog.py --write [--sweep <a "
           "find_site_boards.py results file>]. selftest's "
           "check_every_reader_has_a_platform_entry holds the two together. -->", ""]
    for para in cat.get("about") or []:
        out += [_md(para), ""]
    out += [f"{len(ordered)} platforms: "
            + ", ".join(f"{by[s]} {s}" for s in ORDER if by[s]) + ".", ""]
    statuses = cat.get("statuses") or {}
    out += ["| Status | Means |", "|---|---|"]
    out += [f"| **{s}** | {_md(statuses.get(s, ''))} |" for s in ORDER] + [""]
    out += ["## The platforms", "",
            "| Platform | Id | Status | Reader (`ats.py`) | Identity probe (`verify_boards.py`) "
            "| Measured |", "|---|---|---|---|---|---|"]
    for pid, e in ordered:
        name = e.get("name") or pid
        out.append(f"| [{_md(name)}](#{_anchor(name)}) | `{pid}` | {e.get('status')} | "
                   + (f"`{e['reader']}`" if e.get("reader") else "-") + " | "
                   + (f"`{e['probe']}`" if e.get("probe") else "-") + " | "
                   + f"{e.get('measured_on')} |")
    out += ["", "## On file today, and what the last sweep saw", ""]
    out += _counts_block(cat, live, sweep) + [""]
    for status in ORDER:
        group = [(pid, e) for pid, e in ordered if e.get("status") == status]
        if not group:
            continue
        out += [f"## {HEADINGS[status]}", ""]
        for pid, e in group:
            out += _section(pid, e, live, sweep)
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check data/platforms.json against the code, or write docs/PLATFORMS.md "
                    "from it.")
    ap.add_argument("--write", action="store_true", help="write docs/PLATFORMS.md")
    ap.add_argument("--sweep", metavar="RESULTS",
                    help="a find_site_boards.py results file whose counts the doc carries "
                         "(with --write)")
    a = ap.parse_args(argv)
    if a.sweep and not a.write:
        ap.error("--sweep is read by --write only")
    cat = load()
    if a.write:
        bad, notes = check(cat)
        for n in notes:
            print(f"note: {n}")
        if bad:
            for b in bad:
                print(f"FAIL: {b}")
            print("not written: the catalogue does not pass its check")
            return 1
        live = on_file(json.loads(COMPANIES.read_text()))
        if a.sweep:
            sweep = swept(json.loads(pathlib.Path(a.sweep).read_text()))
        else:
            sweep = carried(DOC.read_text()) if DOC.exists() else None
        DOC.parent.mkdir(exist_ok=True)
        DOC.write_text(render(cat, live, sweep))
        print(f"wrote {DOC.relative_to(ROOT)}: {len(cat['platforms'])} platforms, counts as "
              f"of {live['as_of']}, "
              + (f"sweep of {sweep['generated']}" if sweep else "no sweep counts"))
        return 0
    doc = DOC.read_text() if DOC.exists() else ""
    bad, notes = check(cat, doc)
    platforms = cat.get("platforms") or {}
    by = collections.Counter(e.get("status") for e in platforms.values() if isinstance(e, dict))
    print(f"data/platforms.json: {len(platforms)} platforms ("
          + ", ".join(f"{s} {by[s]}" for s in ORDER if by[s]) + ")")
    print(f"  ats.FETCHERS: {len(ats.FETCHERS)} readers, "
          f"{sum(1 for k in ats.FETCHERS if k in platforms)} with an entry")
    for n in notes:
        print(f"note: {n}")
    for b in bad:
        print(f"FAIL: {b}")
    print("check passed" if not bad else f"{len(bad)} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
