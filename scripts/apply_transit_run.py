"""Apply the 2026-09-10 transit run to the board. --write to land it."""
import json, re, sys, pathlib, datetime as dt
import openpyxl

ROOT = pathlib.Path("/Users/wyethwest/govtech-dock")
DATA = ROOT / "data"
WRITE = "--write" in sys.argv
XLSX = "/Users/wyethwest/Downloads/SLED-transit-200.xlsx"

PRIVATE = re.compile(
    r"\b(private (developments?|operators?|fleets?|sector|companies|campuses)|"
    r"commercial (fleets?|operators?|customers?|clients?|real estate)|corporate|"
    r"OEMs?|manufacturers?|retail|logistics|trucking|rental|consumer|rideshare|"
    r"ride-?hail|airlines?|hotels?|casinos?|leasing|business improvement district|"
    r"employers?|landlords?|b2b)\b", re.I)

def norm_dom(s):
    s = re.sub(r"https?://", "", str(s or "").lower()).strip().strip("/")
    return re.sub(r"^www\.", "", s).split("/")[0]
def nm(s): return re.sub(r"[^a-z0-9]", "", str(s or "").lower())

sys.path.insert(0, str(ROOT / "scripts"))
import admin
co = admin.read_companies()          # journals against this before-state
rows = co if isinstance(co, list) else co["companies"]
by_dom = {norm_dom(c.get("website")): c for c in rows if c.get("website")}
by_nm = {nm(c.get("name")): c for c in rows}

wb = openpyxl.load_workbook(XLSX, read_only=True)
run = list(wb["Companies"].iter_rows(min_row=2, values_only=True))
today = dt.date.today().isoformat()

# ---- 1. buyer, sells_to_gov, sled_only -----------------------------------
imported = sled = 0
for r in run:
    name, site, buyer, sells = r[1], r[2], r[4], r[5]
    c = by_dom.get(norm_dom(site)) or by_nm.get(nm(name))
    if not c:
        continue
    if buyer and str(buyer).strip():
        c["buyer"] = str(buyer).strip()
    if sells:
        c["sells_to_gov"] = str(sells).strip()
    imported += 1
    # THE FLAG IS SET FROM A STATED BUYER, never from the category. It goes on
    # only when the run's buyer sentence names no non-government buyer at all,
    # and the sentence that justified it is recorded beside it so the call is
    # auditable and reversible. Campuses, universities, school districts,
    # airport and transit authorities are all public and do not disqualify.
    if str(sells).strip() == "yes" and buyer and not PRIVATE.search(str(buyer)):
        c["sled_only"] = True
        c["sled_only_why"] = (f"transit run {today}: buyer stated as "
                              f"{str(buyer).strip()[:200]}")
        sled += 1

# ---- 5. founding years and acquisitions ----------------------------------
years = acqs = none_found = 0
for r in run:
    name, site, founded, conf, fsrc, acq = r[1], r[2], r[14], r[15], r[16], r[17]
    c = by_dom.get(norm_dom(site)) or by_nm.get(nm(name))
    if not c:
        continue
    # CONFIRMED ONLY. The run separates confirmed from inferred and the board
    # does not carry a guess as a year.
    if (c.get("year_founded") in (None, "") and founded
            and str(conf).lower().startswith("conf")):
        try:
            y = int(str(founded)[:4])
            if 1800 <= y <= dt.date.today().year:
                c["year_founded"] = y
                c["founded_source"] = str(fsrc or "")[:300] or None
                years += 1
        except (TypeError, ValueError):
            pass
    # "none found" IS A VALUE, not an absence - the run checked and found
    # nothing. Written the way this codebase already writes a checked
    # negative (competitors_none_found), never as a note reading "none
    # found", which is noise on 120 records and reads as data.
    a = str(acq or "").strip()
    if a.lower().startswith("none found"):
        c["acquisitions_checked_on"] = today
        c["acquisitions_none_found"] = True
        none_found += 1
    elif a and not c.get("acquired") and not c.get("parent"):
        c["acquisition_note"] = a[:400]
        c["acquisitions_checked_on"] = today
        acqs += 1

print(f"1. buyer/sells_to_gov imported onto {imported} companies")
print(f"   sled_only set on {sled} of them, each with the sentence that justified it")
print(f"5. {years} founding years added (confirmed only)")
print(f"   {acqs} acquisitions recorded, {none_found} marked checked-and-none-found")

if not WRITE:
    print("\n  LOOKED ONLY. --write to land it.")

# ---- 2. off the board, and written down -----------------------------------
# The run's own words, so the log says why in the researcher's voice rather
# than mine.
BOOT = {
    "motional": "Aptiv-Hyundai robotaxi JV; sells through Uber, Lyft and Uber "
                "Eats. An AV operator, not an agency vendor.",
    "zoox": "Amazon-owned consumer robotaxi service. An AV operator, not an "
            "agency vendor.",
    "weride": "Consumer robotaxi operator, not an agency vendor.",
    "forterra": "Defense autonomy (DoD). Not SLED.",
}
# ---- 3. one company, entered twice ----------------------------------------
MERGE = {
    "spare-labs": ("spare", "sparelabs.com redirects to spare.com; the current "
                            "legal brand is Spare. One company, entered twice."),
    "hopthru": ("swiftly", "hopthru.com fully redirects to goswift.ly and the "
                           "product now ships as 'Swiftly Ridership'."),
    "unwire": ("kuba", "unwire's domain redirects to kubapay.com."),
}
DROP = {
    "transitcheck": "A HealthEquity/WageWorks commuter-benefit brand microsite, "
                    "not an independent company.",
    "curbiq": "A product line of Arcadis IBI Group, not a standalone vendor.",
}

board = json.loads((DATA / "board.json").read_text())
posts = {}
for p in board.get("postings", []):
    posts[p["company_id"]] = posts.get(p["company_id"], 0) + 1

log_path = DATA / "scope_removals.json"
log = json.loads(log_path.read_text()) if log_path.exists() else []
keep, removed = [], []
for c in rows:
    cid = c["id"]
    why = kind = target = None
    if cid in BOOT:
        kind, why = "not_sled", BOOT[cid]
    elif cid in MERGE:
        target, why = MERGE[cid]
        kind = "merged"
    elif cid in DROP:
        kind, why = "not_a_company", DROP[cid]
    if not kind:
        keep.append(c)
        continue
    removed.append((cid, kind, posts.get(cid, 0)))
    if target:
        # A MERGE IS NOT A DELETE. The name people know it by moves onto the
        # survivor, or "Hopthru" stops being findable the day it is folded in.
        keep_row = next((x for x in rows if x["id"] == target), None)
        if keep_row is not None:
            aka = keep_row.setdefault("also_known_as", [])
            if c.get("name") and c["name"] not in aka:
                aka.append(c["name"])
    # THE WHOLE RECORD GOES IN THE LOG, not a stub. A removal nobody can undo
    # is a deletion, and the reason has to travel with the thing it explains.
    log.append({"id": cid, "name": c.get("name"), "kind": kind,
                "merged_into": target, "why": why, "on": today,
                "by": "transit run 2026-09-10, owner-approved",
                "postings_at_removal": posts.get(cid, 0), "record": c})

print(f"2/3. {len(removed)} record(s) come off companies.json:")
for cid, kind, n in removed:
    print(f"     {cid:16} {kind:14} {n} posting(s)")
print(f"     the full record of each is kept in data/scope_removals.json")

if WRITE:
    t = log_path.with_suffix(".tmp")
    t.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    json.loads(t.read_text()); t.replace(log_path)
    # THROUGH THE JOURNAL, and force is stated rather than assumed. This
    # touches far more than the 25-record blast limit - 188 imports and 9
    # removals - and that count was put to the owner before the run.
    out2 = keep if isinstance(co, list) else {**co, "companies": keep}
    bad = admin.save_companies(
        out2, "transit-run-2026-09-10",
        why=(f"Transit research run of 2026-09-10, owner-approved: buyer and "
             f"sells_to_gov imported onto {imported} companies, sled_only set "
             f"on {sled} from a stated buyer, {years} founding years, "
             f"{acqs} acquisitions and {none_found} checked-and-none-found, "
             f"and {len(removed)} records off the board (4 not SLED, 3 merged, "
             f"2 not companies) with the full record kept in "
             f"data/scope_removals.json"),
        by="transit run 2026-09-10, applied by Claude on the owner's approval",
        force=True)
    if bad:
        print("  REFUSED:", bad); sys.exit(1)
    print(f"     written through the journal; companies.json {len(rows)} -> {len(keep)}")
