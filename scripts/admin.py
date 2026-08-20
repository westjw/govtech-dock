#!/usr/bin/env python3
"""Local admin backend: the queues that need a person, in one place.

Everything automated here has a residue. Discovery leaves 884 companies with no
board on file and 16 boards it cannot read. The website guesser leaves 106 names
too generic to guess a domain from, plus short names where the page really does
say the word and only a person can tell a Samsung ETF from a govtech company.
The role classifier leaves 386 titles that name a rank with no function. Slug
mismatches leave an acquisition queue. None of that is a bug to be fixed; it is
the part of the work that needs judgment, and until now it lived in five
different CLI flags and three JSON files nobody opens.

The public site is deliberately static and cannot write. This is the other half:
a stdlib HTTP server bound to loopback that serves admin.html and exposes a small
JSON API over data/. Every write is validated against the same invariants
selftest.py enforces and lands atomically, so a bad edit is refused rather than
half-applied. Nothing here touches data/hiring_history/ - snapshots are the audit
trail and change only through refresh.py.

  python scripts/admin.py [--port 8787]
  then open http://127.0.0.1:8787
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import http.server
import json
import os
import pathlib
import re
import socketserver
import sys
import tempfile
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import add_company    # noqa: E402
import discover_ats   # noqa: E402
import find_websites  # noqa: E402
import roles          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

ATS_TYPES = {"ashby", "greenhouse", "lever", "workable", "recruitee", "breezy",
             "smartrecruiters", "bamboohr", "workday", "rippling", "jazzhr",
             "icims", "html", "unknown"}
STATUSES = {"Yes", "Sales (non-AE)", "None found", "Unknown"}

# Words that carry no identity, so two records differing only by these are the
# same company: "Miovision" and "Miovision Technologies Inc." are one vendor.
LEGAL = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|group|holdings|"
                   r"technologies|technology|software|systems|solutions|company)\b", re.I)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def ident(name: str) -> str:
    return norm(LEGAL.sub("", name or ""))


def read(name: str, default):
    p = DATA / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return default


def write_atomic(name: str, payload) -> None:
    """Write via a temp file in the same directory, then replace.

    A partial companies.json is worse than a stale one: the site, the exporter
    and every script read it on the next run, and a truncated write during a
    refresh would take the whole dataset out.
    """
    p = DATA / name
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def validate(companies: list) -> str | None:
    """The invariants selftest.py enforces, checked before a write lands.

    Returning the first failure rather than raising keeps the browser's error
    readable, and refusing the whole write keeps the file consistent.
    """
    schema = read("schema.json", {"sectors": []})
    cats = {s["name"]: set(s["categories"]) for s in schema["sectors"]}
    seen = set()
    for c in companies:
        who = c.get("name", "?")
        for f in ("id", "name", "sector", "category", "ats", "hiring"):
            if c.get(f) in (None, ""):
                return f"{who}: missing {f}"
        if c["id"] in seen:
            return f"duplicate id {c['id']}"
        seen.add(c["id"])
        if c["sector"] not in cats:
            return f"{who}: unknown sector {c['sector']}"
        if c["category"] not in cats[c["sector"]]:
            return f"{who}: category {c['category']} is not in {c['sector']}"
        if (c.get("ats") or {}).get("type") not in ATS_TYPES:
            return f"{who}: bad ats type {(c.get('ats') or {}).get('type')}"
        if (c.get("hiring") or {}).get("status") not in STATUSES:
            return f"{who}: bad hiring status"
    return None


def dismissed() -> dict:
    return read("admin_dismissed.json", {})


def dismiss(queue: str, key: str, why: str) -> None:
    d = dismissed()
    d.setdefault(queue, {})[key] = {"on": dt.date.today().isoformat(), "why": why}
    write_atomic("admin_dismissed.json", d)


def is_dismissed(queue: str, key: str) -> bool:
    return key in dismissed().get(queue, {})


# ---------------------------------------------------------------- queues

def q_duplicates(companies, board) -> list:
    g = collections.defaultdict(list)
    for c in companies:
        k = ident(c["name"])
        if k:
            g[k].append(c)
    out = []
    posts = collections.Counter(p["company_id"] for p in board.get("postings", []))
    for k, v in g.items():
        if len(v) < 2 or is_dismissed("duplicates", k):
            continue
        out.append({"key": k, "members": [{
            "id": c["id"], "name": c["name"], "sector": c["sector"],
            "category": c["category"], "website": c.get("website"),
            "ats": (c.get("ats") or {}).get("type"),
            "postings": posts.get(c["id"], 0),
            "description": c.get("description")} for c in v]})
    # the pair most likely to be a real duplicate is the one where only one side
    # carries data, because merging it loses nothing
    out.sort(key=lambda r: -sum(1 for m in r["members"] if not m["website"]))
    return out


def q_websites(companies, board) -> list:
    return [{"id": c["id"], "name": c["name"], "sector": c["sector"],
             "category": c["category"], "description": c.get("description"),
             "tier": 1 if c["sector"] in ("General Gov", "Public Works", "Parks & Rec")
                     else 2}
            for c in companies
            if not c.get("website") and not is_dismissed("websites", c["id"])]


def q_boards(companies, board) -> list:
    orgs = {o["id"]: o for o in board.get("organizations", [])}
    out = []
    for c in companies:
        o = orgs.get(c["id"], {})
        kind = (c.get("ats") or {}).get("type")
        no_board = kind in (None, "unknown")
        if not (no_board or o.get("unreadable")):
            continue
        if is_dismissed("boards", c["id"]):
            continue
        out.append({"id": c["id"], "name": c["name"], "sector": c["sector"],
                    "website": c.get("website"), "ats": kind,
                    "why": "board unreadable" if o.get("unreadable")
                           else "no board on file",
                    "note": c.get("ats_note"),
                    "tier": o.get("tier") or 3})
    # tier 1 first: a municipal-SaaS board is worth more than an adjacent one
    out.sort(key=lambda r: (r["tier"], 0 if r["website"] else 1, r["name"]))
    return out


def q_placement(companies, board) -> list:
    """Companies whose description disagrees with the sector they are filed in.

    guess_sector is the same routine intake uses, so this asks the question
    intake would have asked if these had come in through the front door.
    """
    out = []
    for c in companies:
        desc = c.get("description")
        if not desc or is_dismissed("placement", c["id"]):
            continue
        try:
            sec, cat, conf, why = add_company.guess_sector(
                f"{c['name']} {desc}".lower())
        except Exception:
            continue
        # Descriptions here are one line, so "high" confidence is close to
        # unreachable and asking for it flagged nothing at all. The sharper
        # question is whether the sector it is filed under scores anywhere: if
        # the description contains no vocabulary from its own sector but plenty
        # from another, the filing is the thing to doubt.
        if not sec or sec == c["sector"] or conf == "low":
            continue
        if any(line.startswith(c["sector"] + " /") for line in (why or [])):
            continue
        out.append({"id": c["id"], "name": c["name"], "description": desc,
                    "current": {"sector": c["sector"], "category": c["category"]},
                    "suggested": {"sector": sec, "category": cat},
                    "why": why})
    return out


def q_unclassified(companies, board) -> list:
    over = read("family_overrides.json", {})
    seen, out = set(), []
    for p in board.get("postings", []):
        if p.get("family") != "other":
            continue
        t = p["title"]
        if t in over or t in seen or is_dismissed("unclassified", t):
            continue
        seen.add(t)
        out.append({"title": t, "company": p["company"], "url": p.get("url"),
                    "location": p.get("location")})
    return out


def q_acquisitions(companies, board) -> list:
    sus = read("ats_suspects.json", {})
    items = sus.get("suspects", sus) if isinstance(sus, dict) else sus
    if isinstance(items, dict):
        items = [{"id": k, **v} for k, v in items.items()]
    return [i for i in items if not is_dismissed("acquisitions", i.get("id", ""))]


def q_review(companies, board) -> list:
    rev = read("website_review.json", {})
    return [{"id": k, **v} for k, v in rev.items()
            if not is_dismissed("review", k)]


QUEUES = {"duplicates": q_duplicates, "websites": q_websites, "boards": q_boards,
          "placement": q_placement, "unclassified": q_unclassified,
          "acquisitions": q_acquisitions, "review": q_review}

LABEL = {"duplicates": "Duplicates", "websites": "Missing websites",
         "boards": "Missing boards", "placement": "Wrong placement",
         "unclassified": "Unclassified roles", "acquisitions": "Acquisitions",
         "review": "Website review"}


# ---------------------------------------------------------------- actions

def act_merge(body: dict) -> dict:
    """Fold one record into another. The survivor keeps every field it has and
    inherits the ones it is missing, so a merge never loses research."""
    keep_id, drop_id = body.get("keep"), body.get("drop")
    companies = read("companies.json", [])
    keep = next((c for c in companies if c["id"] == keep_id), None)
    drop = next((c for c in companies if c["id"] == drop_id), None)
    if not keep or not drop:
        return {"error": "company not found"}
    filled = []
    for k, v in drop.items():
        if k in ("id", "name"):
            continue
        if keep.get(k) in (None, "", {}, []) and v not in (None, "", {}, []):
            keep[k] = v
            filled.append(k)
        # an unknown ATS never wins over a discovered one
        if k == "ats" and (keep.get("ats") or {}).get("type") in (None, "unknown") \
                and (v or {}).get("type") not in (None, "unknown"):
            keep["ats"] = v
            filled.append("ats")
    keep.setdefault("also_known_as", [])
    if drop["name"] not in keep["also_known_as"]:
        keep["also_known_as"].append(drop["name"])
    remaining = [c for c in companies if c["id"] != drop_id]
    err = validate(remaining)
    if err:
        return {"error": err}
    write_atomic("companies.json", remaining)
    return {"ok": True, "message": f"merged {drop['name']} into {keep['name']}"
                                   + (f", inherited {', '.join(sorted(set(filled)))}"
                                      if filled else "")}


def act_patch(body: dict) -> dict:
    """Edit one company's fields. Validation runs on the whole file, so a change
    that breaks a sector/category pairing is refused rather than written."""
    companies = read("companies.json", [])
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    if not c:
        return {"error": "company not found"}
    allowed = {"name", "sector", "category", "website", "description",
               "location", "year_founded", "vendor_type", "parent", "ats_note"}
    for k, v in (body.get("fields") or {}).items():
        if k in allowed:
            c[k] = v
    if "ats" in (body.get("fields") or {}):
        c["ats"] = body["fields"]["ats"]
    err = validate(companies)
    if err:
        return {"error": err}
    write_atomic("companies.json", companies)
    return {"ok": True, "message": f"updated {c['name']}"}


def clean_url(raw: str) -> str | None:
    """A URL with no host is not a URL. An empty box used to become 'https://',
    which fetched, returned nothing, and offered to save a page-scan board whose
    ref was literally 'https://'."""
    u = (raw or "").strip()
    if not u:
        return None
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    host = u.split("//", 1)[1].split("/")[0]
    return u if "." in host and len(host) > 3 else None


def act_verify_website(body: dict) -> dict:
    """Check a URL before writing it. A live page is not evidence - parked
    domains and unrelated businesses all answer on the obvious name - so this
    reports what the page says about itself and lets a person decide."""
    url, name = clean_url(body.get("url")), body.get("name") or ""
    if not url:
        return {"error": "enter a URL first"}
    try:
        r = add_company.fetch(url)
        html = r[0] if isinstance(r, tuple) else r
    except Exception as exc:
        return {"error": f"could not fetch: {type(exc).__name__}"}
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", title.group(1)).strip()[:140] if title else ""
    parked = bool(find_websites.PARKED.search(html[:4000]))
    base = url.split("//", 1)[1].split("/")[0].replace("www.", "").rsplit(".", 1)[0]
    return {"ok": True, "title": title, "parked": parked,
            "identifies": find_websites.identifies(html, name, base),
            "url": url}


def act_verify_board(body: dict) -> dict:
    """Detect the ATS behind a careers URL and prove it returns this company's
    jobs. slug_matches is what keeps an off-site careers link from wiring a
    company to somebody else's board, which is how acquisitions surface."""
    url = clean_url(body.get("url"))
    if not url:
        return {"error": "enter a careers URL first"}
    companies = read("companies.json", [])
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    try:
        block, note, _ = add_company.find_ats(url)
    except Exception as exc:
        return {"error": f"could not read that page: {type(exc).__name__}"}
    if not block:
        titles = []
        try:
            import ats as ats_mod
            titles = [j.get("title", "") for j in ats_mod.fetch_html_titles(url)][:8]
        except Exception:
            pass
        return {"ok": True, "ats": {"type": "html", "ref": url},
                "note": note or "no known ATS detected; would be stored as a page scan",
                "jobs": len(titles), "slug_ok": None, "titles": titles,
                # A scan that read nothing has not proved the board is empty, it
                # has proved we cannot read it. Storing that as a board makes an
                # unreadable page look like a monitored one.
                "empty_scan": not titles}
    try:
        ok, msg = add_company.verify(block)
    except Exception as exc:
        ok, msg = False, f"{type(exc).__name__}"
    slug_ok = None
    ref = block.get("ref")
    if c and isinstance(ref, str):
        slug_ok = discover_ats.slug_matches(ref, c)
    titles = []
    try:
        import ats as ats_mod
        titles = [j.get("title", "") for j in ats_mod.fetch(block)][:8]
    except Exception:
        pass
    return {"ok": True, "ats": block, "note": msg, "verified": ok,
            "slug_ok": slug_ok, "titles": titles, "jobs": len(titles)}


def act_set_board(body: dict) -> dict:
    companies = read("companies.json", [])
    c = next((x for x in companies if x["id"] == body.get("id")), None)
    if not c:
        return {"error": "company not found"}
    block = body.get("ats") or {}
    if block.get("type") not in ATS_TYPES:
        return {"error": f"unknown ats type {block.get('type')}"}
    c["ats"] = {"type": block["type"], "ref": block.get("ref")}
    c["ats_note"] = body.get("note") or "set by hand in admin"
    err = validate(companies)
    if err:
        return {"error": err}
    write_atomic("companies.json", companies)
    return {"ok": True, "message": f"{c['name']} now points at {block['type']}"}


def act_set_family(body: dict) -> dict:
    """Assign a role family to one exact title.

    This is data, not a classifier rule. A title like 'Manager' names a rank with
    no function, so there is no pattern to write - the judgment belongs to the
    posting, and roles.py reads these overrides on top of its patterns. A title
    that does suggest a rule still gets one in roles.py with a selftest case,
    per the house rule.
    """
    title, fam = body.get("title"), body.get("family")
    if fam not in roles.LABEL:
        return {"error": f"unknown family {fam}"}
    over = read("family_overrides.json", {})
    over[title] = {"family": fam, "on": dt.date.today().isoformat()}
    write_atomic("family_overrides.json", over)
    return {"ok": True, "message": f"{title} -> {roles.LABEL[fam]}"}


def act_dismiss(body: dict) -> dict:
    dismiss(body.get("queue", ""), body.get("key", ""), body.get("why", ""))
    return {"ok": True, "message": "dismissed"}


ACTIONS = {"merge": act_merge, "patch": act_patch,
           "verify-website": act_verify_website, "verify-board": act_verify_board,
           "set-board": act_set_board, "set-family": act_set_family,
           "dismiss": act_dismiss}


# ---------------------------------------------------------------- server

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):        # keep the console readable
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"  {self.command} {self.path}\n")

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.path = "/admin.html"
            return super().do_GET()
        if path == "/api/queues":
            companies, board = read("companies.json", []), read("board.json", {})
            return self._json({"counts": {k: len(f(companies, board))
                                          for k, f in QUEUES.items()},
                               "labels": LABEL,
                               "companies": len(companies),
                               "postings": len(board.get("postings", [])),
                               "generated": board.get("generated")})
        if path.startswith("/api/queue/"):
            name = path.rsplit("/", 1)[-1]
            if name not in QUEUES:
                return self._json({"error": "no such queue"}, 404)
            companies, board = read("companies.json", []), read("board.json", {})
            return self._json({"items": QUEUES[name](companies, board)[:400]})
        if path == "/api/schema":
            return self._json(read("schema.json", {}))
        if path == "/api/families":
            return self._json(roles.LABEL)
        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        action = path.rsplit("/", 1)[-1]
        if action not in ACTIONS:
            return self._json({"error": f"unknown action {action}"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad request body"}, 400)
        try:
            out = ACTIONS[action](body)
        except Exception as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
        return self._json(out, 400 if out.get("error") else 200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()

    companies, board = read("companies.json", []), read("board.json", {})
    print("GovTech Dock admin\n")
    for k, f in QUEUES.items():
        print(f"  {len(f(companies, board)):>5}  {LABEL[k]}")
    # Loopback only, on purpose. This writes to companies.json with no auth in
    # front of it, so it must not be reachable from the network.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", a.port), Handler) as srv:
        print(f"\nhttp://127.0.0.1:{a.port}   (loopback only; ctrl-c to stop)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
