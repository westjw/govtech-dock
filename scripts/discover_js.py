"""Find the ATS behind a JS-walled careers page, by rendering it.

  pip install playwright && python -m playwright install chromium
  python scripts/discover_js.py            # every company currently Unknown
  python scripts/discover_js.py noats      # only those with no ATS on file
  python scripts/discover_js.py js         # only those with a URL that won't parse

NOT part of the weekly refresh, and deliberately so. A JS board fetches its
listings from a JSON endpoint; this renders the page, watches the network, and
prints the endpoint. You then write a normal `ats` entry into companies.json
and `refresh.py` reads it with plain requests forever after - so the browser is
a one-off discovery tool and CI stays stdlib-only.

Read-only: prints findings, writes nothing. Verify a slug with a real fetch
before trusting it - `lever` slugs are lowercase, and a careers link that goes
off-site sometimes lands on a *different company's* board.
"""
import asyncio, json, re, sys
from urllib.parse import urljoin, urlparse

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parent.parent)
from playwright.async_api import async_playwright

ATS_URL_PATS = [
    ("ashby",   r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?(?:embed\?org=)?([a-z0-9._-]+)"),
    ("greenhouse", r"(?:boards|job-boards|api)[-.]?greenhouse\.io/(?:v1/boards/)?(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]+)"),
    ("lever",   r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)"),
    ("workable", r"([a-z0-9-]+)\.workable\.com|apply\.workable\.com/api/v3/accounts/([a-z0-9-]+)|apply\.workable\.com/([a-z0-9-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("breezy",  r"([a-z0-9-]+)\.breezy\.hr"),
    ("smartrecruiters", r"(?:api|careers)\.smartrecruiters\.com/(?:v1/companies/)?([A-Za-z0-9_-]+)"),
    ("workday", r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com"),
    ("rippling", r"ats\.rippling\.com/([a-z0-9-]+)"),
    ("jazzhr",  r"([a-z0-9-]+)\.applytojob\.com"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
    ("icims",   r"([a-z0-9-]+)\.icims\.com"),
    ("paylocity", r"recruiting\.paylocity\.com/[Rr]ecruiting/[Jj]obs/[^/]+/([A-Za-z0-9-]+)"),
    ("trinet",  r"app\.trinethire\.com/companies/([0-9]+-[a-z0-9-]+)"),
    ("jobvite", r"jobs\.jobvite\.com/([a-z0-9-]+)"),
    ("pinpoint", r"([a-z0-9-]+)\.pinpointhq\.com"),
    ("dover",   r"app\.dover\.io/([A-Za-z0-9-]+)"),
    ("teamtailor", r"([a-z0-9-]+)\.teamtailor\.com"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
    ("hrmdirect", r"([a-z0-9-]+)\.hrmdirect\.com"),
    ("adp",     r"workforcenow\.adp\.com/mascsr/default/mdf/recruitment/recruitment\.html\?cid=([a-f0-9-]+)"),
    ("successfactors", r"([a-z0-9-]+)\.(?:successfactors|sapsf)\.com"),
    ("taleo",   r"([a-z0-9-]+)\.taleo\.net"),
    ("clearcompany", r"([a-z0-9-]+)\.clearcompany\.com"),
    ("jobscore", r"careers\.jobscore\.com/careers/([a-z0-9-]+)"),
    ("polymer", r"([a-z0-9-]+)\.polymer\.co"),
]

# third-party junk that matches "job|career" but isn't a board
NOISE = re.compile(r"userway|google|linkedin|facebook|doubleclick|hubspot|segment|"
                   r"cookiebot|onetrust|gtm|analytics|cloudflare|sentry|intercom|"
                   r"drift|qualified|6sense|demandbase", re.I)

CAREER_LINK = re.compile(r"career|job|opening|position|join|work-with|we-are-hiring", re.I)


def fingerprint(text):
    hits = {}
    for name, pat in ATS_URL_PATS:
        for m in re.finditer(pat, text, re.I):
            if NOISE.search(m.group(0)):
                continue
            slug = next((g for g in m.groups() if g), "")
            if slug and slug not in ("www", "jobs", "careers", "api", "apply"):
                hits.setdefault(name, slug)
    return hits


async def load(ctx, url, seen):
    page = await ctx.new_page()
    page.on("request", lambda r: seen.append(r.url)
            if r.resource_type in ("xhr", "fetch", "document", "script") else None)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        dom = await page.content()
        try:
            text = await page.inner_text("body")
        except Exception:
            text = ""
        links = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)")
        await page.close()
        return dom, text, links, None
    except Exception as e:
        await page.close()
        return "", "", [], str(e)[:60]


async def probe_one(browser, comp, sem):
    cid = comp["id"]
    site = (comp.get("website") or "").rstrip("/")
    ref = comp["ats"].get("ref")
    start = ref if isinstance(ref, str) and ref.startswith("http") else site
    if not start:
        return cid, "-", {}, [], "no website", 0
    async with sem:
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            viewport={"width": 1280, "height": 900})
        seen, best_text, err = [], 0, None
        try:
            dom, text, links, err = await load(ctx, start, seen)
            best_text = len(text)
            hits = fingerprint("\n".join(seen) + "\n" + dom)
            # follow the site's own careers links (incl. off-site ATS links)
            cand, host = [], urlparse(start).netloc
            for h in links:
                if not CAREER_LINK.search(h) or NOISE.search(h):
                    continue
                if urlparse(h).netloc != host:      # off-site => likely the ATS itself
                    cand.insert(0, h)
                else:
                    cand.append(h)
            tried = 0
            for u in dict.fromkeys(cand):
                if hits or tried >= 3:
                    break
                tried += 1
                d2, t2, l2, e2 = await load(ctx, u, seen)
                best_text = max(best_text, len(t2))
                hits = fingerprint("\n".join(seen) + "\n" + d2)
                if hits:
                    start = u
                    break
            api = [u for u in seen
                   if re.search(r"(job|career|position|opening|posting|requisition)", u, re.I)
                   and not NOISE.search(u) and ("api" in u.lower() or ".json" in u.lower())]
        finally:
            await ctx.close()
        return cid, start, hits, api[:3], err, best_text


async def main():
    cs = json.load(open(REPO + "/data/companies.json"))
    targets = [c for c in cs if c["hiring"]["status"] == "Unknown"]
    grp = sys.argv[1] if len(sys.argv) > 1 else None
    if grp == "noats":
        targets = [c for c in targets if c["ats"]["type"] == "unknown"]
    elif grp == "js":
        targets = [c for c in targets if c["ats"]["type"] != "unknown"]
    # --limit N bounds the run. One long unbounded Chromium session is how a
    # discovery tool turns into a machine-killer; a bounded batch is a
    # measurement you can repeat.
    if "--limit" in sys.argv:
        n = int(sys.argv[sys.argv.index("--limit") + 1])
        targets = targets[:n]
    print(f"rendering {len(targets)} companies\n", flush=True)
    sem = asyncio.Semaphore(2)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        res = await asyncio.gather(*[probe_one(browser, c, sem) for c in targets])
        await browser.close()
    found = 0
    for cid, url, hits, api, err, n in sorted(res, key=lambda r: (not r[2], r[0])):
        if hits:
            found += 1
            print(f"ATS {cid:<26} " + "  ".join(f"{k}={v!r}" for k, v in hits.items()))
            print(f"    {url}")
        elif api:
            print(f"API {cid:<26} {api[0][:120]}")
        else:
            print(f"--  {cid:<26} {n} chars" + (f", error: {err}" if err else "") + f"  ({url})")
    print(f"\n{found}/{len(res)} fingerprinted")


asyncio.run(main())
