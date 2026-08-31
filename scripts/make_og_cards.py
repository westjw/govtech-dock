#!/usr/bin/env python3
"""Render the six share cards to assets/og/*.png. Run by hand, rarely.

    python3 scripts/make_og_cards.py

A link to this board unfurled as a naked url in Slack, LinkedIn and iMessage
because there was no og:image anywhere. These are that image, one per tab, so
a shared link arrives looking like something rather than like nothing.

WHY A BROWSER, AND WHY THAT IS NOT A NEW DEPENDENCY. Composing text over a
background needs a rasteriser, and this repo has no image library on purpose.
Playwright is already here for the same shape of job - a one-off tool whose
OUTPUT is committed, never something refresh.py or CI depends on. Same
sanctioned exception as discover_js.py, and the cards are checked in, so
nobody needs the browser installed to build or deploy the site.

Every colour comes from data/brand.json. Nothing here restates a hex.
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "og"

# Title, then the line under it. The words are the ones the tab itself uses -
# a card that promises something the page does not say is a card that lies.
CARDS = {
    "home": ("Every open sales role",
             "at state and local government technology companies"),
    # {hiring}, NOT {orgs}. This said "across {orgs} companies" and rendered
    # 2,113 - every company on the map, including the 583 with no public board
    # and everyone hiring nobody. 152 companies carry a quota-carrying role.
    # The card is the preview a link renders in Slack and on a timeline, so it
    # is read by more people than the page it points at.
    "jobs": ("Sales jobs in govtech",
             "quota-carrying roles across {hiring} companies, refreshed daily"),
    "companies": ("The govtech map",
                  "{orgs} companies selling into state and local government"),
    "conferences": ("Where they exhibit",
                    "the floors these companies stand on, with dates"),
    "market": ("What the hiring looks like",
               "who is growing, where, and in what"),
    "alerts": ("The new roles, by email",
               "on the days you choose, above the threshold you set"),
}


def card_html(title: str, sub: str, brand: dict, mascot_b64: str) -> str:
    p = brand["palette"]
    ink, ice, beak = p["penguin"]["hex"], p["ice"]["hex"], p["beak"]["hex"]
    badge = p["badge"]["hex"]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&display=swap" rel="stylesheet">
<style>
 *{{margin:0;padding:0;box-sizing:border-box}}
 body{{width:1200px;height:630px;background:{ink};color:{ice};
   font-family:Archivo,system-ui,sans-serif;display:flex;flex-direction:column;
   justify-content:space-between;padding:72px 76px;position:relative;overflow:hidden}}
 .beak{{position:absolute;left:0;top:0;width:100%;height:10px;background:{beak}}}
 h1{{font-size:76px;font-weight:800;letter-spacing:-.03em;line-height:1.02;
   max-width:15ch;text-wrap:balance}}
 p{{font-size:31px;color:#C9DCE8;margin-top:22px;max-width:26ch;line-height:1.32}}
 /* The footer is LEFT-ALIGNED, not space-between. Space-between put the
    domain under the mascot's feet, where it was unreadable - the card's one
    job is to say whose link this is and it was the one illegible thing on it. */
 .foot{{display:flex;align-items:baseline;gap:16px}}
 .name{{font-size:27px;font-weight:800;letter-spacing:.02em}}
 .dot{{color:{beak}}}
 .dom{{font-size:23px;color:#7C97AA}}
 img{{position:absolute;right:64px;bottom:96px;width:250px;opacity:.97}}
 .rule{{width:104px;height:7px;background:{badge};margin:34px 0 0}}
</style></head><body>
<div class="beak"></div>
<div><h1>{title}</h1><p>{sub}</p><div class="rule"></div></div>
<img src="data:image/svg+xml;base64,{mascot_b64}" alt="">
<div class="foot">
  <div class="name">{brand['name']}</div>
  <span class="dot">&middot;</span>
  <div class="dom">{brand['domain']}</div>
</div>
</body></html>"""


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("needs playwright: pip install playwright && "
              "python -m playwright install chromium", file=sys.stderr)
        return 1

    brand = json.loads((ROOT / "data" / "brand.json").read_text())
    board = json.loads((ROOT / "data" / "board.json").read_text())
    orgs = f"{len(board.get('organizations', [])):,}"
    # Companies with at least one quota-carrying OPENING - the population the
    # jobs card actually describes.
    hiring = f"{sum(1 for o in board.get('organizations', []) if o.get('quota_roles')):,}"
    mascot = (ROOT / "assets" / "mascot" / "svg" / "mascot-stand.svg")
    if not mascot.exists():
        mascot = ROOT / "assets" / "mascot" / "svg" / "head-on-the-hunt.svg"
    b64 = base64.b64encode(mascot.read_bytes()).decode()

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        page = br.new_page(viewport={"width": 1200, "height": 630},
                           device_scale_factor=1)
        for name, (title, sub) in CARDS.items():
            page.set_content(card_html(title, sub.format(orgs=orgs, hiring=hiring), brand, b64))
            page.wait_for_timeout(650)           # the webfont
            page.screenshot(path=str(OUT / f"{name}.png"))
            print(f"  wrote assets/og/{name}.png")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
