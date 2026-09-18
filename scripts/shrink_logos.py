#!/usr/bin/env python3
"""Bring oversized logo marks down to the size they are actually drawn at.

    python3 scripts/shrink_logos.py              # what it would do
    python3 scripts/shrink_logos.py --write

MEASURED 2026-09-18: 361 of the 1,916 marks in assets/logos are 256px or
larger on a side, 5.02MB between them, and the largest is 943x740 weighing
212,190 bytes. Every surface that draws a logo draws it at 44, 48, 64 or -
once, on a company page - 96px. So a visitor loading a jobs page downloads,
at worst, a quarter-megapixel image to fill a 44px square.

WHY A BYTE CAP DID NOT CATCH THIS. fetch_logos caps at 220KB and logos.py at
512KB, and both count bytes only. A photograph compresses badly and trips a
byte cap; a flat mark exported at 1000px compresses beautifully and sails
through. Pixels and bytes are different questions and only one was ever
asked.

MAX_PX = 192 is 2x the largest size any page draws (96px on /c/), which
covers a 2x display with headroom. Going lower would be sharper on bytes and
would start to show on a retina company page.

LOCAL ONLY, AND THAT IS DELIBERATE. This shells out to `sips`, which is macOS
only, exactly as discover_js.py shells out to Playwright - a one-off tool
whose OUTPUT is committed so the run path never needs it. CLAUDE.md's rule
stands: refresh.py and CI stay stdlib + requests + openpyxl.

IT ONLY EVER SHRINKS. A result that is larger than the original, or that
cannot be read back, or that lost its transparency, is discarded and the
original kept - a worse logo is worse than a big one, and a broken one is a
company's mark missing from a public page.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGOS = ROOT / "assets" / "logos"
sys.path.insert(0, str(ROOT / "scripts"))

import imgsize                                                  # noqa: E402

MAX_PX = 192
# sips rewrites these in place happily; .ico and .svg have no business here
# (an .ico is already small and multi-resolution, an .svg has no pixels).
SHRINKABLE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def has_alpha(p: pathlib.Path) -> bool | None:
    """True/False for a PNG, None for anything else (unknowable cheaply)."""
    try:
        head = p.read_bytes()[:26]
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return head[25] in (4, 6)          # colour type 4 = gray+A, 6 = RGB+A


def oversized() -> list[tuple[pathlib.Path, tuple[int, int], int]]:
    out = []
    for p in sorted(LOGOS.iterdir()):
        if not p.is_file() or p.suffix.lower() not in SHRINKABLE:
            continue
        wh = imgsize.of(p)
        if wh and max(wh) > MAX_PX:
            out.append((p, wh, p.stat().st_size))
    return out


def shrink(p: pathlib.Path, tmp: pathlib.Path) -> tuple[bool, str, int]:
    """(replaced, why not, new size). Never leaves a worse file behind."""
    before = p.stat().st_size
    alpha_before = has_alpha(p)
    out = tmp / p.name
    r = subprocess.run(["sips", "-Z", str(MAX_PX), str(p), "--out", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        return False, f"sips refused it: {(r.stderr or '').strip()[:60]}", before
    after = out.stat().st_size
    wh = imgsize.of(out)
    if not wh:
        return False, "the result could not be read back", before
    if max(wh) > MAX_PX:
        return False, f"the result is still {wh}", before
    if after >= before:
        return False, f"the result is not smaller ({after:,} >= {before:,})", before
    if alpha_before and has_alpha(out) is False:
        return False, "the result lost its transparency", before
    shutil.copy2(out, p)
    return True, "", after


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()
    if not shutil.which("sips"):
        print("sips is not on this machine. This tool is macOS-only by design; "
              "its output is committed so nothing in the run path needs it.")
        return 1

    todo = oversized()
    if not todo:
        print(f"nothing over {MAX_PX}px. Every mark is already the size it is drawn at.")
        return 0
    total = sum(sz for _, _, sz in todo)
    print(f"\n{len(todo)} mark(s) larger than {MAX_PX}px, {total:,} bytes.\n")
    for p, wh, sz in sorted(todo, key=lambda r: -r[2])[:a.show]:
        print(f"  {sz:>9,} B  {str(wh):>12}  {p.name}")
    if len(todo) > a.show:
        print(f"  ... and {len(todo) - a.show} more")
    if not a.write:
        print("\ndry run: nothing written. Add --write to shrink them.")
        return 0

    done = saved = 0
    kept: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for p, wh, sz in todo:
            ok, why, after = shrink(p, tmp)
            if ok:
                done += 1
                saved += sz - after
            else:
                kept.append(f"{p.name}: {why}")
    print(f"\nshrank {done} of {len(todo)}, {saved:,} bytes saved.")
    if kept:
        print(f"{len(kept)} left as they were, each named:")
        for k in kept[:12]:
            print(f"  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
