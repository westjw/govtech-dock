#!/usr/bin/env python3
"""(width, height) off an image's header, in the standard library only.

    python3 scripts/imgsize.py assets/logos/*.png

WHY THIS EXISTS. Both logo writers cap BYTES and neither caps PIXELS, so a
943x740 PNG weighing 212KB passes fetch_logos' 220KB limit and is then drawn
in a 44px tile. 318 of the 1,916 marks on disk are 256px or wider, 3.96MB of
them, and the largest single 44px tile costs a visitor 212,190 bytes.

A byte cap and a pixel cap are not the same question. A photograph compresses
badly and trips the byte cap; a flat vector-like mark exported at 1000px
compresses beautifully and never does. The second is the common case here and
nothing has ever measured it.

STDLIB ONLY, ON PURPOSE. Pillow is not installed and CLAUDE.md's rule is
stdlib + requests + openpyxl, no new dependencies ever - refresh.py and CI
have to keep running on a bare interpreter. Every format below states its
dimensions in the first few dozen bytes, so reading them needs `struct` and
nothing else. This does not decode, resize or re-encode: it reads a header
and answers, or says it could not.

SVG IS DELIBERATELY NOT HANDLED. It has no pixel dimensions - that is the
point of it - and logos.py already refuses SVG at the door for a different
and better reason (an SVG can carry script).
"""
from __future__ import annotations

import pathlib
import struct
import sys


def dimensions(blob: bytes) -> tuple[int, int] | None:
    """(width, height), or None when the bytes are not a format read here."""
    if len(blob) < 24:
        return None
    # PNG: IHDR is always the first chunk, width and height big-endian at 16.
    if blob[:8] == b"\x89PNG\r\n\x1a\n" and blob[12:16] == b"IHDR":
        return struct.unpack(">II", blob[16:24])
    # GIF: little-endian, straight after the 6-byte signature.
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", blob[6:10])
    # WebP: three sub-formats, and only the lossy one is laid out simply.
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        fourcc = blob[12:16]
        if fourcc == b"VP8 " and len(blob) >= 30:
            return (struct.unpack("<H", blob[26:28])[0] & 0x3FFF,
                    struct.unpack("<H", blob[28:30])[0] & 0x3FFF)
        if fourcc == b"VP8L" and len(blob) >= 25:
            bits = struct.unpack("<I", blob[21:25])[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if fourcc == b"VP8X" and len(blob) >= 30:
            w = blob[24] | blob[25] << 8 | blob[26] << 16
            h = blob[27] | blob[28] << 8 | blob[29] << 16
            return w + 1, h + 1
        return None
    # JPEG: walk the segments to the start-of-frame, which is the only place
    # the size is written. SOF0/1/2/3, 5-7, 9-11, 13-15 - but NOT the DHT,
    # DAC and RST markers that sit in the same numeric range.
    if blob[:2] == b"\xff\xd8":
        i, n = 2, len(blob)
        while i + 9 < n:
            if blob[i] != 0xFF:
                i += 1
                continue
            marker = blob[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > n:
                return None
            seg = struct.unpack(">H", blob[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 9 > n:
                    return None
                h, w = struct.unpack(">HH", blob[i + 5:i + 9])
                return w, h
            i += 2 + seg
        return None
    return None


def of(path) -> tuple[int, int] | None:
    """Dimensions of a file, reading only the head of it."""
    try:
        with open(path, "rb") as fh:
            return dimensions(fh.read(64 * 1024))
    except OSError:
        return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.splitlines()[2].strip())
        return 0
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        wh = of(p)
        print(f"{str(wh) if wh else 'unreadable':>14}  {p.stat().st_size:>9,} B  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
