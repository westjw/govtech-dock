#!/usr/bin/env python3
"""Turn the latest refresh diff into an alert about companies that just opened
AE reqs - the warm doors. Written for the weekly Action, but runs anywhere.

  python scripts/alert.py /tmp/body.md   # writes body, prints "alert" or "none"

Prints "alert" and writes a markdown body when there is something worth an
issue; prints "none" and writes nothing when there isn't. Always exits 0, so
the caller branches on the printed word rather than on an exit code.

Deliberately quiet: only transitions *into* "Yes" are alert-worthy. A company
going Yes -> None found is a door closing, which is visible in the diff on the
site and doesn't need to interrupt anyone's Monday.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def new_ae_openings(diff: dict) -> list[dict]:
    """Companies whose status became "Yes" since the previous snapshot."""
    if not diff.get("previous"):
        # First snapshot: everything looks new. Alerting here would mean a
        # 50-line issue that says nothing about what changed.
        return []
    return [c for c in diff.get("changes", [])
            if c.get("to") == "Yes" and c.get("from") != "Yes"]


def render(hits: list[dict], companies: list[dict], date: str, previous: str) -> str:
    by_id = {c["id"]: c for c in companies}
    lines = [f"Since the {previous} run, **{len(hits)} "
             f"{'company' if len(hits) == 1 else 'companies'}** opened AE reqs.",
             ""]
    for h in hits:
        comp = by_id.get(h["id"], {})
        hiring = comp.get("hiring", {})
        where = " · ".join(x for x in (comp.get("location"), comp.get("sector"),
                                       comp.get("category")) if x)
        lines.append(f"### {h['company']}")
        if where:
            lines.append(f"*{where}*")
        if comp.get("description"):
            lines.append(f"\n{comp['description']}")
        lines.append(f"\n- was **{h['from']}**, now **Yes**")
        for role in hiring.get("roles", [])[:5]:
            title = role.get("title", "").strip() or "(untitled role)"
            loc = f" — {role['location']}" if role.get("location") else ""
            url = role.get("url", "")
            lines.append(f"- [{title}{loc}]({url})" if url else f"- {title}{loc}")
        if "[page scan - verify]" in (hiring.get("note") or ""):
            lines.append("- ⚠️ found by page scan, not a structured job board — "
                         "**verify before acting on this one**")
        if comp.get("website"):
            lines.append(f"- {comp['website']}")
        lines.append("")
    lines.append("---")
    lines.append(f"From the {date} refresh. Close this once you've worked the list.")
    return "\n".join(lines)


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    diff = json.loads((DATA / "latest_diff.json").read_text())
    hits = new_ae_openings(diff)
    if not hits:
        print("none")
        return 0
    companies = json.loads((DATA / "companies.json").read_text())
    body = render(hits, companies, diff.get("date", "?"), diff.get("previous", "?"))
    if out:
        out.write_text(body)
    else:
        print(body, file=sys.stderr)
    print("alert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
