#!/usr/bin/env python3
"""The one door that writes assets/logos/<id>.<ext>.

    python3 scripts/logos.py --company acme --url https://acme.com/logo.png
    python3 scripts/logos.py --company acme --url ... --domain acme.com --write

WHY A MODULE AND NOT FOUR LINES IN THE RULING. A logo is the only claimant
edit that puts a BINARY from somebody else's server into a public repository,
and it is the only one where the file on disk is the record - there is no
logo field on a company, `build_board` globs the directory into a manifest
(build_board.py:1649) and the page builds a src from the extension it finds.
So every rule about what may land has to live where the write happens, or the
second caller will not have it.

THE EXTENSION COMES FROM THE BYTES, NEVER THE URL. A file named logo.png that
is actually something else would be served by us, from our origin, under a
name that says image. Sniffed, and refused when the sniff and the claim
disagree.

SVG IS REFUSED BY NAME. 108 SVG logos are already on file and they are fine -
we put them there. An SVG is a document that can carry script and external
references, and the difference between "we chose this one" and "anyone who
proves a mailbox can upload one" is the whole of it. Refused with the reason
said out loud, because a silent downgrade to PNG would leave somebody
believing their vector logo is on the page.

ONE FILE PER COMPANY. The manifest is `{stem: suffix}` built by globbing, so
acme.png and acme.svg both present means the extension the page gets depends
on directory order. Landing a logo removes the other extensions for that id.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGOS = ROOT / "assets" / "logos"

MAX_BYTES = 512 * 1024      # a logo is not a photograph
MIN_BYTES = 128             # below this it is an error page or a tracking pixel

# (extension, magic prefix). Ordered, first match wins.
MAGIC = (
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("jpg", b"\xff\xd8\xff"),
    ("gif", b"GIF87a"),
    ("gif", b"GIF89a"),
    ("webp", b"RIFF"),          # confirmed below: bytes 8..12 are WEBP
    ("ico", b"\x00\x00\x01\x00"),
)
KEEP = {"png", "jpg", "gif", "webp", "ico"}


def registrable(host: str) -> str:
    """The registrable name, well enough to compare two hosts we both hold."""
    h = str(host or "").lower().strip().rstrip(".")
    h = h[4:] if h.startswith("www.") else h
    if not h or "." not in h:
        return ""
    p = h.split(".")
    if len(p) <= 2:
        return h
    if len(p[-2]) <= 3 and len(p[-1]) == 2:      # co.uk, com.au, org.nz
        return ".".join(p[-3:])
    return ".".join(p[-2:])


def sniff(blob: bytes) -> str:
    """The extension these BYTES are, or '' when they are not an image we take."""
    for ext, magic in MAGIC:
        if blob.startswith(magic):
            if ext == "webp" and blob[8:12] != b"WEBP":
                continue        # RIFF is also .wav and .avi
            return ext
    return ""


def _fetch(url: str) -> tuple[bytes, str]:
    """(bytes, error). Never raises: a claimant's server is not ours."""
    import requests
    try:
        r = requests.get(url, timeout=20, stream=True,
                         headers={"user-agent": "sledjobs-logo/1.0"})
    except Exception as e:
        return b"", f"could not be fetched ({type(e).__name__})"
    if r.status_code != 200:
        return b"", f"answered {r.status_code}"
    # READ WITH A CEILING, not r.content. A content-length header is the
    # server's claim about itself; the cap has to bind on what actually
    # arrives or a 4GB response is a 4GB read.
    blob = b""
    for chunk in r.iter_content(8192):
        blob += chunk
        if len(blob) > MAX_BYTES:
            return b"", f"is larger than {MAX_BYTES // 1024}KB"
    return blob, ""


def check(cid: str, url: str, domain: str) -> str:
    """The refusal, or '' if this URL may be fetched. No network."""
    if not cid or not str(cid).replace("-", "").isalnum():
        return f"{cid!r} is not a company id"
    u = str(url or "").strip()
    if not u.lower().startswith("https://"):
        return "a logo has to come over https from the company's own site"
    host = u.split("//", 1)[-1].split("/")[0].split(":")[0]
    if not registrable(host):
        return f"{host!r} is not a host we can check"
    if domain and registrable(host) != registrable(domain):
        # SAME RULE AS A JOB LINK, AND FOR A STRONGER REASON: we will serve
        # these bytes from our own origin under the company's name.
        return (f"the logo is on {registrable(host)} and the claim is from "
                f"{registrable(domain)}. Host it on your own domain.")
    if u.lower().rstrip("/").endswith(".svg"):
        return ("SVG is not accepted from a claimant. An SVG is a document "
                "that can carry script and fetch other files, and we serve it "
                "from our own origin. Send a PNG.")
    return ""


def install(cid: str, url: str, domain: str, by: str, write: bool = False,
            fetch=None) -> dict:
    """Fetch a claimant's logo and make it the one on file.

    `fetch` is injectable so a guard can drive this whole door without a
    network: a check that stubs the write and not the fetch is a check that
    reaches out to somebody's server every time the suite runs.
    """
    bad = check(cid, url, domain)
    if bad:
        return {"error": bad}
    blob, err = _fetch(url) if fetch is None else fetch(url)
    if err:
        return {"error": f"that logo {err}"}
    if len(blob) < MIN_BYTES:
        return {"error": f"that is {len(blob)} bytes - too small to be a logo. "
                         f"An error page or a tracking pixel looks like this."}
    ext = sniff(blob)
    if not ext:
        return {"error": "those bytes are not a PNG, JPEG, GIF, WebP or ICO. "
                         "The name of a file is not what it is, and we would "
                         "be serving it from our origin under yours."}
    if ext not in KEEP:
        return {"error": f"{ext} is not a format we serve"}
    if not write:
        return {"ok": True, "dry_run": True, "ext": ext, "bytes": len(blob),
                "message": f"{len(blob)} bytes, a real {ext}; would land as "
                           f"assets/logos/{cid}.{ext}"}

    LOGOS.mkdir(parents=True, exist_ok=True)
    dest = LOGOS / f"{cid}.{ext}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(blob)
    tmp.replace(dest)
    # ONE FILE PER COMPANY: the manifest is a glob, so a leftover acme.svg
    # beside a new acme.png makes the rendered src depend on directory order.
    removed = []
    for other in LOGOS.glob(f"{cid}.*"):
        if other != dest and other.stem == cid:
            other.unlink()
            removed.append(other.name)
    return {"ok": True, "ext": ext, "bytes": len(blob), "replaced": removed,
            # relative when it can be, absolute when it cannot: a guard
            # points LOGOS at a temp dir, and a path helper that raises there
            # turns a passing check into a crash in the suite.
            "path": str(dest.relative_to(ROOT)
                        if dest.is_relative_to(ROOT) else dest), "by": by,
            "message": f"{cid}.{ext} ({len(blob) // 1024}KB)"
                       + (f", replacing {', '.join(removed)}" if removed else "")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--company", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--domain", default="", help="the claimant's domain")
    ap.add_argument("--by", default="owner")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    res = install(a.company, a.url, a.domain, a.by, write=a.write)
    print(res.get("error") or res.get("message"))
    return 1 if res.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
