#!/usr/bin/env python3
"""Read an exhibitor floor that only draws in a browser.

    python3 scripts/render_floor.py --list                 # the floors that need one
    python3 scripts/render_floor.py --tag "ICC 2026"       # look
    python3 scripts/render_floor.py --all --write          # stage what reads

WHY. 29 catalogue rows hold an exhibitor list in a floor-plan app -
MapYourShow, a2z, eventScribe, goeshow, ExpoCad, Map Dynamics - whose page is
a shell the crawler reads as nothing; the names arrive from JavaScript.
sweep_exhibitors can only say "js-walled", which is true and produces no
company. A browser draws them.

WHAT IT KEEPS FROM THE CRAWLER, so a rendered floor is held to the same bar
as a fetched one:
  - robots.txt is asked at every hop, the way render_fetch and discover_js
    ask it: the address's redirects are walked first (ats.resolve), any
    document the router sees a refused url for is aborted, and nothing is
    kept from a page whose final url is refused. The user agent names us.
  - the names go through sweep_exhibitors' own harvest(), suspicious(),
    quality() and merge_into(): the same filters, the same grade, the same
    rule that a refusal never overwrites a find. A floor graded menu or
    mixed is staged for the reader, who decides, as with any other floor.
  - an edition check: an address that names a year other than the row's tag
    is not read, because its companies would be tagged with an edition they
    did not attend (the five rows rolled on 2026-10-14 are the reason).

WHAT IT DOES NOT DO. It does not log in, solve a challenge or pass as plain
Chrome; a floor behind any of those is reported as such. It clicks only
controls whose text asks to show more of the same list ("show all", "load
more", "view all"), scrolls, and reads the page and its frames once.

Optional, like discover_js: Playwright never enters refresh or the build.
  pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

import ats                                                     # noqa: E402
import robots                                                  # noqa: E402
import sweep_exhibitors as sw                                  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
      "(compatible; govtech-dock/1.0; +https://github.com/westjw/govtech-dock)")
MORE = re.compile(r"^\s*(show|view|see|load)\s+(all|more)\b", re.I)
MAX_CLICKS = 8


def address(c: dict) -> str | None:
    return c.get("exhibitor_url") or c.get("exhibitor_url_candidate")


def other_edition(url: str, tag: str | None) -> str | None:
    """A year in the address that is not the tag's, or None."""
    ty = re.findall(r"20[2-3]\d", tag or "")
    uy = set(re.findall(r"(?<!\d)(20[2-3]\d)(?!\d)", url or ""))
    if ty and uy and ty[-1] not in uy:
        return sorted(uy)[0]
    return None


def walled(conf: list, tag: str | None = None) -> list:
    rows = [c for c in conf if c.get("fetchability") == "js-walled" and address(c)
            and c.get("sled") is not False]
    return [c for c in rows if c.get("event_tag") == tag] if tag else rows


async def render(url: str) -> tuple[str, str | None]:
    """(the page and its frames' html, why not). robots.txt at every hop."""
    from playwright.async_api import async_playwright
    try:
        url = await asyncio.to_thread(ats.resolve, url)
    except ats.RobotsRefused:
        return "", "robots.txt asks crawlers not to read this site"
    except Exception as exc:                                   # noqa: BLE001
        return "", f"{type(exc).__name__}: {str(exc)[:80]}"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 1000})
            page = await ctx.new_page()

            async def only_allowed(route):
                req = route.request
                if req.resource_type == "document" and not await asyncio.to_thread(
                        robots.allowed, req.url):
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()
            await page.route("**/*", only_allowed)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
            except Exception as exc:                           # noqa: BLE001
                return "", f"the page did not load: {str(exc)[:80]}"
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:                                  # noqa: BLE001
                pass
            await page.wait_for_timeout(2500)
            if not await asyncio.to_thread(robots.allowed, page.url):
                return "", "robots.txt asks crawlers not to read the page it landed on"
            for _ in range(MAX_CLICKS):
                clicked = False
                for el in await page.query_selector_all("button, a, [role=button]"):
                    try:
                        txt = (await el.inner_text()).strip()
                    except Exception:                          # noqa: BLE001
                        continue
                    if MORE.match(txt) and len(txt) < 30 and await el.is_visible():
                        try:
                            await el.click(timeout=3000)
                            clicked = True
                            await page.wait_for_timeout(1500)
                            break
                        except Exception:                      # noqa: BLE001
                            continue
                await page.mouse.wheel(0, 20000)
                await page.wait_for_timeout(1200)
                if not clicked:
                    break
            parts = [await page.content()]
            for fr in page.frames[1:]:
                try:
                    if await asyncio.to_thread(robots.allowed, fr.url):
                        parts.append(await fr.content())
                except Exception:                              # noqa: BLE001
                    continue
            return "\n".join(parts), None
        finally:
            await browser.close()


def staged_for(c: dict, html: str, why: str | None, url: str) -> dict:
    """The staged record, exactly as sweep_exhibitors.sweep() would write it."""
    st = {"event_tag": c.get("event_tag"), "conference": c.get("conference"), "found": False,
          "source_url": url, "source_note": "", "exhibitors": [], "why": None}
    if why:
        st["why"] = why
        return st
    names = sw.harvest(html, sw.host_of(c.get("event_tag")))
    doubt = sw.suspicious(names)
    grade, note = sw.quality(names)
    if not doubt and grade == "doubtful":
        doubt = f"this url is the association's own site rather than a list of who exhibits at it ({note})"
    if doubt or len(names) < sw.MIN_NAMES:
        st["why"] = doubt or f"the rendered page held {len(names)} name(s), fewer than {sw.MIN_NAMES}"
        st["source_note"] = (f"rendered in a browser by scripts/render_floor.py; {len(names)} "
                             f"candidate name(s) after filtering. Not staged: {st['why']}")
        return st
    st["found"] = True
    st["quality"] = grade
    st["exhibitors"] = sorted(names, key=lambda r: r["name"].lower())
    st["source_note"] = (f"Rendered in a browser from {url} by scripts/render_floor.py "
                         f"(robots.txt asked at every hop). {len(names)} name(s), "
                         f"{sum(1 for n in names if n['website'])} with a website the page "
                         f"linked. Quality: {grade} ({note}). Names only.")
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tag")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    conf = json.loads((DATA / "conferences.json").read_text())["conferences"]
    todo = walled(conf, a.tag)
    if a.list or not (a.all or a.tag):
        for c in todo:
            print(f"  {str(c.get('event_tag'))[:30]:32} {address(c)[:70]}")
        print(f"\n{len(todo)} floor(s) need a browser. --all or --tag to read, --write to stage.")
        return 0
    found = 0
    for c in todo:
        url, tag = address(c), c.get("event_tag")
        yr = other_edition(url, tag)
        if yr:
            print(f"  skipped  {str(tag)[:30]:32} the address is the {yr} edition's, the tag is not")
            continue
        html, why = asyncio.run(render(url))
        st = staged_for(c, html, why, url)
        if st["found"]:
            found += 1
            print(f"  {st['quality']:8} {str(tag)[:30]:32} {len(st['exhibitors']):4} name(s)")
        else:
            print(f"  refused  {str(tag)[:30]:32} {str(st['why'])[:70]}")
        if a.write:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", str(tag)).strip("_")
            out = DATA / f"exhibitors_{safe}.json"
            if not sw.merge_into(st, out):
                continue
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=1) + "\n")
            json.loads(tmp.read_text())
            tmp.replace(out)
    print(f"\n  {found} floor(s) rendered and staged" + ("" if a.write else " (look only; --write)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
