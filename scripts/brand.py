#!/usr/bin/env python3
"""Read data/brand.json. One import, so nothing hardcodes the domain again."""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA = json.loads((ROOT / "data" / "brand.json").read_text())

NAME = _DATA["name"]
TAGLINE = _DATA["tagline"]
LINE = _DATA["line"]
SITE = _DATA["site"]
DOMAIN = _DATA["domain"]
FROM = f'{NAME} <{_DATA["from_email"]}>'
PALETTE = {k: v["hex"] for k, v in _DATA["palette"].items()}


def all_of() -> dict:
    return _DATA
