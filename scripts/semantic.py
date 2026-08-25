"""the concept map behind the Companies tab's search, and the checker for it.

what this is, said plainly
--------------------------
it is not an embedding model. it is a hand-written map from the phrases people
type onto the vocabulary this board already has: 12 sectors and ~50 categories
in data/schema.json. "recreation" resolves to Parks & Rec / Recreation
Management, "911 dispatch" to the four Public Safety departments, "school
buses" to K-12 Schools / Transportation. it behaves like semantic search
because the target vocabulary is bounded and small, and it keeps two
properties an embedding model does not hand you for free:

  deterministic   the same query returns the same companies today and in a
                  year. no model version, no temperature, no drift. a link
                  someone saved keeps meaning what it meant.
  explainable     it can say WHY on screen - "reading 'recreation software'
                  as Parks & Rec / Recreation Management" - because the
                  reasoning IS the answer, not a post-hoc story about a
                  distance. a search a job-hunter cannot audit is a search a
                  job-hunter has to trust blindly, and this board's whole
                  posture is the opposite of that.

what it is bad at, said just as plainly: it only knows the words written down
here. a company doing something real that nobody thought to name is reachable
only by the plain-text fallback, which is why the fallback is still there and
why the UI counts what it left out instead of pretending the bucket is the
whole world.

CANONICAL HERE, RESTATED IN index.html
--------------------------------------
the site is one dependency-free index.html with no build step, so the map has
to exist as a literal in the browser. that means two copies, and two copies
rot. the map below is the source of truth; `--emit` prints the exact JS block;
`--check` fails if index.html's block has drifted from it. same shape as
alerts.js restating roles.py's vocabulary, and for the same reason: drift here
is silent - a phrase quietly stops resolving and the tab just returns fewer
companies, which looks like an honest empty and is not one.

  python3 scripts/semantic.py --check
  python3 scripts/semantic.py --emit            # paste between the sentinels
  python3 scripts/semantic.py --query "recreation software"
  python3 scripts/semantic.py --query "parking" --hiring

`--check` enforces three invariants:
  1. every (sector, category) a phrase points at exists in data/schema.json.
     pointing at a renamed category is how a concept silently matches nothing.
  2. every category in schema.json is reachable from at least one phrase. a
     category no word can reach is invisible to this search.
  3. index.html's embedded block is byte-identical to `--emit`.
it also REPORTS (does not fail on) categories that exist in the schema and
hold zero companies today - Higher Education / Campus Safety is one. that is
a real, honest empty and the tab says so rather than showing nothing.

the match logic is duplicated too (match() here, coKeep() there). it is small
and it has not changed shape; the vocabulary is the part that moves. if that
stops being true, generate the predicate as well.

NOTE: index.html expands place nicknames (nyc -> new york, dfw -> dallas) in
its PLACE table before the leftover text terms are matched. this file does not
model that, on purpose - place aliasing only ever rewrites the plain-text
remainder and can never change which sector or category a query resolves to,
so counts for concept queries agree exactly. a query whose ONLY content is a
nickname will count differently here than in the browser.


=============================================================================
THE SECOND HALF: what an actual embedding path would look like
=============================================================================
sketched, not built. the owner expects to need it; writing down the shape now
means the decision later is a decision and not an archaeology project. nothing
below exists in this repository.

the shape
---------
  1. BUILD TIME (here, in python, offline): for each of the 2,108 companies,
     embed one short document - name, sector, category, description, and the
     `also` buckets, joined. ~40 words. that is the whole corpus: ~85k words.
     write data/vectors.bin plus data/vectors.json (ids, in the same order).
  2. QUANTISE: float32 at 768 dims is 6.5 MB for 2,108 companies, which is
     larger than board.json and lands on every visitor. int8 with a per-vector
     scale is 1.6 MB; binary (1 bit per dim, hamming distance) is 202 KB and
     retains ~90% of the ranking quality on a corpus this small and this
     domain-narrow. binary, with a float32 rescore of the top 200, is the
     right trade here: 202 KB ships, and the rescore needs no extra file if
     the top-200 floats are fetched as a range request from a second file.
     if that second file is a complication, skip the rescore - on 2,108 short
     documents binary alone is already better than substring matching.
  3. QUERY TIME: the query is embedded by functions/api/embed.js, a Pages
     Function calling Cloudflare Workers AI (@cf/baai/bge-base-en-v1.5, 768
     dims, the same model as step 1 - a query embedded by a DIFFERENT model
     than the corpus is noise, and this is the single easiest way to ship a
     search that silently returns garbage).
  4. COSINE IN THE BROWSER: 2,108 dot products over 768 int8 lanes is well
     under a frame. no server-side index, no vector database, no per-query
     cost beyond the embedding call.
  5. FALL BACK to the concept map when the Function is unavailable, when the
     binding is not configured, or when the query is one this map already
     answers exactly. the concept map is not scaffolding to be removed - it
     is the offline path and the explanation layer, and an embedding score
     has no explanation to give.

why the query is embedded at OUR origin and not at the model vendor's
---------------------------------------------------------------------
this is a board people use to job-hunt quietly, often from a work laptop. the
queries are "who is hiring an AE near me" and, sooner or later, the name of
the company someone is trying to leave. sending that string to a third-party
inference endpoint creates a log, somewhere else, tying an IP to an intent
that can cost the person their job. routing it through a Pages Function on
this origin means the query never leaves infrastructure the owner controls,
and Workers AI runs on the same account as the site. it is the same reasoning
that made subscribe answer identically for new and existing addresses: this
site must not become an oracle about somebody's job search.

it also means the corpus embeddings are computed once, offline, by whatever is
convenient - a local model, a batch API, anything - because those are public
company descriptions and carry no user in them. only the QUERY is sensitive,
and only the query needs the private path.

what it would cost
------------------
  build     2,108 embeddings, once per data change. free locally; a few cents
            at any batch API's rate. minutes, not hours.
  ship      202 KB (binary) or 1.6 MB (int8) added to every page load, against
            board.json's current 6.1 MB. binary is noise; int8 is a 26% page.
  runtime   one Workers AI call per SEARCH, not per keystroke - which means
            the input must stop being `oninput` and become submit-on-enter for
            the embedding path, or a 20-character query bills 20 inferences.
            Workers AI bills in neurons; bge-base is among the cheapest units
            they sell. at 10k searches/month this is dollars, not tens of
            dollars, but it is the first per-user recurring cost this project
            would take on and it should be a deliberate one.
  latency   ~50-150 ms for the embedding call, then <10 ms for cosine. the
            concept map answers in under a millisecond with no network, so
            the fallback is also the fast path and should render first.
  ongoing   a model version pin. re-embedding the corpus without re-embedding
            with the same model breaks ranking silently. record the model id
            in data/vectors.json and refuse to score if it differs from what
            the Function reports.

what it buys, honestly
----------------------
on a bounded 50-category vocabulary: less than people expect. the concept map
already answers the queries the owner named. what embeddings buy is the LONG
TAIL - "software for tracking stray dogs", "the thing that does pickleball
court booking" - queries nobody will write a phrase for. that is real, and it
is worth having, and it is not worth breaking determinism or adding a
per-query cost before the free version has been used enough to know which
queries it misses. instrument the misses first: a query that resolves to no
concept AND returns under 3 text matches is the signal, and it costs nothing
to count.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "data" / "schema.json"
BOARD_PATH = ROOT / "data" / "board.json"
INDEX_PATH = ROOT / "index.html"

# the sentinels index.html wraps its copy of the map in
MAP_BEGIN = "/* CONCEPT-MAP */"
MAP_END = "/* /CONCEPT-MAP */"
STOP_BEGIN = "/* CO-STOP */"
STOP_END = "/* /CO-STOP */"

# words that carry no signal on a board where every single row is a govtech
# software vendor. "recreation software" has to mean the recreation companies,
# so "software" cannot survive as a term the description must also contain -
# that is precisely the substring behaviour being replaced. these are dropped
# only AFTER concept phrases have been consumed, so a phrase containing one of
# them still resolves.
CO_STOP = [
    "software", "platform", "platforms", "solution", "solutions",
    "tech", "technology", "technologies", "saas", "app", "apps",
    "product", "products", "tool", "tools", "vendor", "vendors",
    "company", "companies", "provider", "providers", "system", "systems",
    "govtech", "gov", "government", "municipal", "municipality",
    "public sector", "sector", "space", "industry",
]

# Each entry: the phrases a person types, and the buckets they mean.
# A bucket is [sector, category] or [sector, None] for a whole sector.
# Phrases are matched longest-first, so "school buses" is never read as "bus".
CONCEPTS: list[dict[str, Any]] = [
    # ------------------------------------------------------ Public Safety
    {"say": ["public safety", "first responder", "first responders"],
     "go": [["Public Safety", None]]},
    {"say": ["911", "9-1-1", "911 dispatch", "911 call taking", "dispatch",
             "dispatching", "cad", "computer aided dispatch",
             "computer-aided dispatch", "emergency dispatch", "psap",
             "next generation 911", "ng911", "call taking", "emergency call"],
     "go": [["Public Safety", "Police"], ["Public Safety", "Fire"],
            ["Public Safety", "EMS"], ["Public Safety", "Emergency Mgmt"]]},
    {"say": ["police", "policing", "law enforcement", "sheriff", "cops",
             "body camera", "body cameras", "bodycam", "body-worn camera",
             "records management", "police records", "evidence management",
             "digital evidence", "alpr", "license plate reader",
             "gunshot detection", "real time crime center", "investigations",
             "crime", "crime analytics", "citation", "citations"],
     "go": [["Public Safety", "Police"]]},
    {"say": ["fire", "fire department", "fire service", "firefighting",
             "firefighter", "firefighters", "wildfire", "wildfires",
             "station alerting", "fire inspection", "fire records"],
     "go": [["Public Safety", "Fire"]]},
    {"say": ["ems", "emergency medical", "ambulance", "paramedic",
             "paramedics", "epcr", "patient care reporting", "medic"],
     "go": [["Public Safety", "EMS"]]},
    {"say": ["emergency management", "emergency mgmt", "emergency operations",
             "mass notification", "alerting", "disaster", "disasters",
             "disaster recovery", "eoc", "preparedness", "continuity",
             "incident management", "resilience"],
     "go": [["Public Safety", "Emergency Mgmt"]]},
    {"say": ["corrections", "correctional", "jail", "jails", "prison",
             "prisons", "inmate", "inmates", "detention", "offender",
             "offenders", "booking"],
     "go": [["Public Safety", "Corrections"]]},

    # -------------------------------------------------------- Public Works
    {"say": ["public works", "dpw"],
     "go": [["Public Works", None]]},
    {"say": ["waste", "solid waste", "trash", "garbage", "refuse",
             "recycling", "landfill", "landfills", "sanitation", "curbside",
             "hauling", "haulers", "organics", "compost"],
     "go": [["Public Works", "Waste & Recycling"]]},
    {"say": ["streets", "street", "roads", "road", "roadway", "pavement",
             "paving", "pothole", "potholes", "sidewalk", "sidewalks",
             "right of way", "snow", "snow plow", "plowing", "plow",
             "striping", "work zone", "traffic signal", "traffic signals",
             "traffic", "road maintenance", "street maintenance"],
     "go": [["Public Works", "Streets"]]},
    {"say": ["water", "drinking water", "wastewater", "sewer", "sewers",
             "stormwater", "storm water", "hydrant", "hydrants",
             "leak detection", "water quality", "water main", "water mains",
             "treatment plant", "backflow"],
     "go": [["Public Works", "Water"]]},
    # said as one phrase this is unambiguous, and it spans two sectors that
    # a bare "water" or a bare "utility" would each get half of
    {"say": ["water utility", "water utilities", "water district",
             "water meter", "water meters"],
     "go": [["Public Works", "Water"],
            ["Utilities & Energy", "Billing & Customer Systems"]]},
    {"say": ["fleet", "fleet management", "fleet maintenance", "vehicles",
             "asset management", "asset inventory", "work order",
             "work orders", "cmms", "telematics", "gis", "geospatial",
             "mapping", "infrastructure inspection", "maintenance management"],
     "go": [["Public Works", "Fleet & Asset Mgmt"]]},

    # ---------------------------------------------------------- General Gov
    {"say": ["general gov", "general government", "city hall",
             "administration", "clerk", "city clerk", "records",
             "agenda", "agendas", "meeting management", "minutes",
             "public records", "foia"],
     "go": [["General Gov", None]]},
    {"say": ["citizen services", "311", "constituent", "constituents",
             "resident services", "residents", "citizen engagement",
             "service request", "service requests", "self service",
             "digital services", "customer service"],
     "go": [["General Gov", "Citizen Services"]]},
    {"say": ["permitting", "permit", "permits", "licensing", "license",
             "licenses", "land management", "code enforcement", "inspections",
             "inspection", "plan review", "building department", "building",
             "business license", "business licensing", "occupancy"],
     "go": [["General Gov", "Permitting & Licensing"]]},
    {"say": ["zoning", "land use"],
     "go": [["General Gov", "Permitting & Licensing"],
            ["Housing & Community Dev", "Planning & Economic Development"]]},
    {"say": ["finance", "erp", "accounting", "budget", "budgeting",
             "general ledger", "financial management", "audit", "auditing",
             "fund accounting", "treasury"],
     "go": [["General Gov", "Finance & ERP"]]},
    {"say": ["procurement", "purchasing", "bids", "bidding", "bid",
             "rfp", "rfps", "sourcing", "contract management", "contracts",
             "e-procurement", "eprocurement", "supplier management",
             "payments", "payment processing", "billing", "cashiering",
             "revenue collection", "collections"],
     "go": [["General Gov", "Procurement & Payments"]]},
    {"say": ["grants", "grant management", "grant"],
     "go": [["General Gov", "Procurement & Payments"],
            ["Higher Education", "Business & Finance"]]},
    {"say": ["hr", "human resources", "applicant tracking",
             "employee scheduling", "timekeeping", "time and attendance",
             "civil service", "onboarding", "personnel", "employee"],
     "go": [["General Gov", "HR & Workforce"]]},
    {"say": ["payroll"],
     "go": [["General Gov", "HR & Workforce"], ["General Gov", "Finance & ERP"]]},
    {"say": ["workforce"],
     "go": [["General Gov", "HR & Workforce"],
            ["Health & Human Services", "Workforce & Labor"]]},
    {"say": ["cemetery", "cemeteries", "burial", "burials", "funeral",
             "interment", "gravesite", "graves"],
     "go": [["General Gov", "Cemetery Management"]]},
    {"say": ["ai", "artificial intelligence", "machine learning",
             "information technology", "data platform", "data management",
             "cloud", "cybersecurity", "cyber security", "cyber", "infosec",
             "information security", "identity", "chatbot", "llm", "genai",
             "automation", "digital government", "low code", "low-code",
             "integration", "middleware", "api"],
     "go": [["General Gov", "IT & AI Platforms"]]},
    {"say": ["strategy", "performance management", "performance", "open data",
             "dashboards", "dashboard", "transparency", "analytics",
             "community engagement", "public engagement", "surveys",
             "benchmarking", "reporting"],
     "go": [["General Gov", "Strategy & Performance"]]},
    # schema.json carries BOTH "Elections" and "Elections & Voting" in General
    # Gov. every company sits in the first one today, but a search that only
    # knew one spelling would go blind the day a record lands in the other, and
    # nothing would ever say so. the words reach both.
    {"say": ["elections", "election", "voting", "vote", "ballot", "ballots",
             "voter", "voters", "voter registration", "poll worker",
             "poll workers", "polling place", "vote by mail", "redistricting"],
     "go": [["General Gov", "Elections"], ["General Gov", "Elections & Voting"]]},
    {"say": ["libraries", "library", "librarian", "librarians", "ils",
             "integrated library system", "ebooks", "e-books", "circulation",
             "makerspace", "cataloging", "interlibrary loan"],
     "go": [["General Gov", "Libraries"]]},
    {"say": ["animal services", "animal control", "animal shelter", "animal",
             "animals", "pet licensing", "pets", "dog license",
             "stray", "strays"],
     "go": [["General Gov", "Animal Services"]]},

    # ----------------------------------------------------------- Parks & Rec
    {"say": ["parks", "park", "parks and rec", "parks & rec",
             "parks and recreation", "parks department", "open space",
             "green space"],
     "go": [["Parks & Rec", None]]},
    {"say": ["recreation", "rec", "recreation management", "rec management",
             "rec department", "activity registration", "program registration",
             "class registration", "registration", "facility booking",
             "facility reservation", "facility reservations", "reservations",
             "reservation", "rec center", "community center", "memberships",
             "membership", "camp", "camps", "campground", "campgrounds",
             "camping", "campsite", "campsites", "ymca", "aquatics",
             "pool", "pools"],
     "go": [["Parks & Rec", "Recreation Management"]]},
    {"say": ["youth sports", "leagues", "league", "sports league",
             "little league", "team sports", "athletics", "athletic",
             "tournament", "tournaments", "coaches", "scheduling sports",
             "officials", "referees"],
     "go": [["Parks & Rec", "Youth Sports & Leagues"]]},
    {"say": ["trails", "trail", "playground", "playgrounds",
             "park operations", "park maintenance", "irrigation", "turf",
             "splash pad", "ranger", "rangers", "grounds", "landscaping",
             "tree inventory", "urban forestry"],
     "go": [["Parks & Rec", "Park Ops & Safety Tech"]]},
    {"say": ["events", "event", "tourism", "destination", "destinations",
             "visitor", "visitors", "convention", "conventions", "festival",
             "festivals", "venue", "venues", "dmo", "attractions"],
     "go": [["Parks & Rec", "Events & Tourism"]]},
    {"say": ["ticketing", "tickets"],
     "go": [["Parks & Rec", "Events & Tourism"],
            ["Transit & Parking", "Fare & Payments"]]},

    # ---------------------------------------------------------- K-12 Schools
    {"say": ["k-12", "k12", "schools", "school", "school district",
             "school districts", "districts", "district", "edtech",
             "students", "student", "classroom", "teachers", "teacher"],
     "go": [["K-12 Schools", None]]},
    {"say": ["education", "educational"],
     "go": [["K-12 Schools", None], ["Higher Education", None]]},
    {"say": ["school safety", "school security", "visitor management",
             "anonymous tipline", "tip line", "tipline", "threat assessment",
             "emergency drills", "panic button", "weapons detection",
             "bullying", "student safety", "access control"],
     "go": [["K-12 Schools", "School Safety"]]},
    {"say": ["school bus", "school buses", "school transportation",
             "student transportation", "bus routing", "route planning",
             "routing", "bell times"],
     "go": [["K-12 Schools", "Transportation"]]},
    {"say": ["bus", "buses"],
     "go": [["K-12 Schools", "Transportation"],
            ["Transit & Parking", "Fleet & Operations"]]},
    {"say": ["sis", "student information system", "gradebook", "lms",
             "learning management", "attendance", "enrollment",
             "school operations", "food service", "cafeteria", "nutrition",
             "substitute", "special education", "iep", "assessment",
             "curriculum", "tutoring"],
     "go": [["K-12 Schools", "Operations & SIS"]]},

    # ----------------------------------------------------- Transit & Parking
    {"say": ["transit", "public transit", "transportation", "rail",
             "light rail", "subway", "metro", "mobility", "commute",
             "commuting"],
     "go": [["Transit & Parking", None]]},
    {"say": ["fare", "fares", "fare collection", "fare payment",
             "transit payments", "tap to pay", "farebox", "smart card"],
     "go": [["Transit & Parking", "Fare & Payments"]]},
    {"say": ["transit operations", "cad avl", "avl", "run cutting",
             "runcutting", "vehicle health", "operator", "operators",
             "depot", "yard management"],
     "go": [["Transit & Parking", "Fleet & Operations"]]},
    {"say": ["rider", "riders", "rider experience", "passenger information",
             "trip planning", "trip planner", "real time arrivals",
             "gtfs", "wayfinding transit"],
     "go": [["Transit & Parking", "Rider Experience"]]},
    {"say": ["microtransit", "micro transit", "demand response",
             "demand-response", "on demand transit", "paratransit",
             "dial a ride", "dial-a-ride", "autonomous", "autonomous vehicles",
             "self driving", "shuttle", "shuttles", "nemt"],
     "go": [["Transit & Parking", "Demand-Response & AV"]]},
    {"say": ["parking"],
     "go": [["Transit & Parking", "Parking & Curb"],
            ["Airports & Aviation", "Parking & Ground Transport"]]},
    {"say": ["curb", "curb management", "parking meters", "parking meter",
             "parking enforcement", "parking ticket", "parking tickets",
             "permit parking", "garage", "garages", "valet", "loading zone",
             "kerb"],
     "go": [["Transit & Parking", "Parking & Curb"]]},

    # ---------------------------------------------------- Utilities & Energy
    {"say": ["utilities", "utility", "energy", "power", "electric",
             "electricity"],
     "go": [["Utilities & Energy", None]]},
    {"say": ["street lighting", "streetlight", "streetlights",
             "street lights", "street light", "lighting", "poles", "pole",
             "pole attachment", "smart poles", "luminaire", "smart city"],
     "go": [["Utilities & Energy", "Street Lighting & Poles"]]},
    {"say": ["grid", "electric grid", "der", "distributed energy", "solar",
             "microgrid", "ev charging", "ev", "electric vehicle",
             "substation", "outage", "outage management", "ami",
             "advanced metering", "smart meter", "smart meters",
             "demand response energy", "decarbonization", "renewables"],
     "go": [["Utilities & Energy", "Grid & Energy"]]},
    {"say": ["utility billing", "customer information system", "cis",
             "customer billing", "meter to cash", "rate design"],
     "go": [["Utilities & Energy", "Billing & Customer Systems"],
            ["General Gov", "Finance & ERP"]]},
    {"say": ["broadband", "fiber", "connectivity", "internet", "wifi",
             "wi-fi", "digital divide", "rural broadband", "5g",
             "small cell", "small cells", "network", "telecom"],
     "go": [["Utilities & Energy", "Broadband & Connectivity"]]},

    # --------------------------------------------------- Airports & Aviation
    {"say": ["airport", "airports", "aviation", "faa", "airline",
             "airlines", "flight", "flights"],
     "go": [["Airports & Aviation", None]]},
    {"say": ["airfield", "runway", "runways", "taxiway", "taxiways", "ramp",
             "apron", "ground handling", "noise monitoring",
             "wildlife hazard", "deicing", "de-icing", "airside"],
     "go": [["Airports & Aviation", "Airfield & Operations"]]},
    {"say": ["terminal", "terminals", "gate", "gates", "concessions",
             "baggage", "baggage handling", "passenger experience",
             "flight information", "fids", "common use", "wayfinding",
             "landside"],
     "go": [["Airports & Aviation", "Terminal & Passenger Experience"]]},
    {"say": ["screening", "tsa", "checkpoint", "credentialing", "badging",
             "perimeter security", "biometrics", "biometric",
             "security screening"],
     "go": [["Airports & Aviation", "Security & Screening"]]},
    {"say": ["ground transport", "airport parking", "rideshare", "tnc",
             "curbside airport"],
     "go": [["Airports & Aviation", "Parking & Ground Transport"]]},

    # ------------------------------------------------------ Courts & Justice
    {"say": ["justice", "criminal justice", "judiciary", "legal"],
     "go": [["Courts & Justice", None]]},
    {"say": ["courts", "court", "e-filing", "efiling", "electronic filing",
             "docket", "dockets", "jury", "juror", "jurors", "court records",
             "case management", "traffic court"],
     "go": [["Courts & Justice", "Courts & Case Management"]]},
    {"say": ["prosecutor", "prosecutors", "prosecution",
             "district attorney", "public defender", "defender",
             "evidence sharing", "discovery"],
     "go": [["Courts & Justice", "Prosecution & Defense"]]},
    {"say": ["probation", "parole", "supervision", "pretrial", "pre-trial",
             "reentry", "re-entry", "diversion", "electronic monitoring",
             "ankle monitor", "community supervision"],
     "go": [["Courts & Justice", "Probation & Supervision"]]},

    # --------------------------------------------- Health & Human Services
    {"say": ["health and human services", "human services", "hhs",
             "social services", "health", "healthcare", "case worker",
             "caseworker"],
     "go": [["Health & Human Services", None]]},
    {"say": ["public health", "epidemiology", "immunization",
             "immunizations", "vital records", "disease surveillance",
             "contact tracing", "wic", "environmental health",
             "food safety"],
     "go": [["Health & Human Services", "Public Health"]]},
    {"say": ["benefits", "medicaid", "snap", "eligibility", "tanf",
             "entitlement", "safety net", "assistance programs"],
     "go": [["Health & Human Services", "Benefits & Medicaid Systems"]]},
    {"say": ["child welfare", "child and family", "foster care", "foster",
             "cps", "child support", "early childhood", "childcare",
             "child care", "children", "families"],
     "go": [["Health & Human Services", "Child & Family Services"]]},
    {"say": ["behavioral health", "mental health", "substance use",
             "addiction", "crisis", "988", "opioid", "counseling",
             "treatment", "telehealth"],
     "go": [["Health & Human Services", "Behavioral Health"]]},
    {"say": ["aging", "seniors", "senior services", "older adults",
             "veterans", "veteran", "meals on wheels", "adult protective",
             "long term care", "elder"],
     "go": [["Health & Human Services", "Aging & Veterans"]]},
    {"say": ["workforce development", "labor", "unemployment",
             "job training", "workforce boards", "wioa", "reemployment"],
     "go": [["Health & Human Services", "Workforce & Labor"],
            ["General Gov", "HR & Workforce"]]},

    # ------------------------------------------------------ Higher Education
    {"say": ["higher education", "higher ed", "university", "universities",
             "college", "colleges", "campus", "campuses", "student affairs",
             "faculty"],
     "go": [["Higher Education", None]]},
    {"say": ["campus safety", "campus police", "title ix", "clery"],
     "go": [["Higher Education", "Campus Safety"]]},
    {"say": ["bursar", "registrar", "admissions", "advancement", "alumni",
             "research administration", "endowment", "tuition",
             "financial aid"],
     "go": [["Higher Education", "Business & Finance"]]},
    {"say": ["campus it", "student portal", "sso", "course scheduling",
             "space management", "classroom technology"],
     "go": [["Higher Education", "IT & Administration"]]},

    # ----------------------------------------------- Housing & Community Dev
    {"say": ["housing", "affordable housing", "public housing", "section 8",
             "housing authority", "homelessness", "homeless",
             "rental assistance", "hmis", "eviction", "tenant", "tenants",
             "landlord", "short term rental", "str"],
     "go": [["Housing & Community Dev", "Housing & Assistance"]]},
    {"say": ["planning", "economic development", "community development",
             "comprehensive plan", "redevelopment", "business attraction",
             "site selection", "downtown"],
     "go": [["Housing & Community Dev", "Planning & Economic Development"]]},

    # ------------------------------------------------------------- suppliers
    {"say": ["suppliers & services", "suppliers and services", "supplier",
             "suppliers", "consulting", "consultant", "consultants",
             "integrator", "system integrator", "systems integrator",
             "reseller", "resellers", "staffing", "professional services"],
     "go": [["Public Safety", "Suppliers & Services"],
            ["Public Works", "Suppliers & Services"],
            ["General Gov", "Suppliers & Services"],
            ["Parks & Rec", "Suppliers & Services"],
            ["K-12 Schools", "Suppliers & Services"],
            ["Transit & Parking", "Suppliers & Services"],
            ["Utilities & Energy", "Suppliers & Services"],
            ["Airports & Aviation", "Suppliers & Services"],
            ["Courts & Justice", "Suppliers & Services"],
            ["Health & Human Services", "Suppliers & Services"],
            ["Higher Education", "Suppliers & Services"],
            ["Housing & Community Dev", "Suppliers & Services"]]},
]


# --------------------------------------------------------------------- parse

def _phrases() -> list[tuple[str, list[list[Any]]]]:
    out: list[tuple[str, list[list[Any]]]] = []
    for c in CONCEPTS:
        for say in c["say"]:
            out.append((say, c["go"]))
    # longest first so "school buses" is never read as "bus", and
    # "water utility" is never read as "water" plus "utility"
    out.sort(key=lambda p: (-len(p[0]), p[0]))
    return out


def parse(q: str) -> dict[str, Any]:
    """a query -> {go: [[sector, category|None], ...], terms: [str], said: [str]}

    `said` is the phrases that resolved, in the order they were consumed. it is
    what the UI prints back at the person, so it is part of the return value
    and not a debugging aid.
    """
    text = " " + (q or "").lower().replace(",", " ").replace(".", " ") + " "
    go: list[list[Any]] = []
    said: list[str] = []
    for phrase, targets in _phrases():
        pat = re.compile(r"(?:^|\s)" + re.escape(phrase) + r"(?=\s|$)")
        if pat.search(text):
            text = pat.sub(" ", text)
            said.append(phrase)
            for t in targets:
                if t not in go:
                    go.append(t)
    stop_multi = [s for s in CO_STOP if " " in s]
    for s in stop_multi:
        text = text.replace(" " + s + " ", " ")
    stop = {s for s in CO_STOP if " " not in s}
    terms = [w for w in text.split() if w and w not in stop]

    # the SAME query with nothing consumed by a concept - i.e. what the old
    # substring search would have looked for. it is not used to filter; it is
    # used to COUNT what the concept map set aside, so the tab can say "23 more
    # mention this and are filed elsewhere" instead of implying the bucket is
    # the whole world. a bounded vocabulary that will not admit its own edges
    # is how a search quietly deletes a company.
    plain = " " + (q or "").lower().replace(",", " ").replace(".", " ") + " "
    for s in stop_multi:
        plain = plain.replace(" " + s + " ", " ")
    literal = [w for w in plain.split() if w and w not in stop]
    return {"go": go, "terms": terms, "said": said, "literal": literal}


# --------------------------------------------------------------------- match

def buckets(org: dict[str, Any]) -> set[tuple[str, Any]]:
    out = {(org["sector"], org["category"]), (org["sector"], None)}
    for a in (org.get("also") or []):
        out |= {(a["sector"], a["category"]), (a["sector"], None)}
    return out


def in_go(org: dict[str, Any], go: list[list[Any]]) -> bool:
    b = buckets(org)
    return any((t[0], t[1]) in b for t in go)


def haystack(org: dict[str, Any]) -> str:
    also = " ".join(f"{a['sector']} {a['category']}" for a in (org.get("also") or []))
    return " ".join([
        org.get("name") or "", org.get("description") or "",
        org.get("location") or "", org.get("sector") or "",
        org.get("category") or "", also,
    ]).lower()


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _word_starts(name: str) -> list[int]:
    """offsets in `name` a person could plausibly start typing from.

    position 0, anything after a separator, and the capital in a camelCase
    name - this dataset is full of HopSkipDrive, RecDesk, SewerAI, MyRec, and
    "rec desk" has to find RecDesk or the promise that a name always finds the
    company is not kept.
    """
    starts = [0]
    for i in range(1, len(name)):
        prev, cur = name[i - 1], name[i]
        if not prev.isalnum():
            starts.append(i)
        elif cur.isupper() and prev.islower():
            starts.append(i)
    return starts


def name_hit(org: dict[str, Any], raw: str) -> bool:
    """the one rule with no exception: a company name finds the company.

    anchored at a word start rather than free substring. free substring is
    what makes "Rain" return Instructure, because the word "training" contains
    the letters r-a-i-n; 52 results where 10 are meant is the same failure as
    zero results, just louder.
    """
    q = _squash(raw)
    if not q:
        return False
    name = org.get("name") or ""
    return any(_squash(name[i:]).startswith(q) for i in _word_starts(name))


def _term_hit(term: str, hay: str) -> bool:
    """a term matches at a word start, as a prefix. "permit" finds
    "permitting"; "rain" does not find "training"."""
    return re.search(r"(?:^|[^a-z0-9])" + re.escape(term), hay) is not None


def match(org: dict[str, Any], want: dict[str, Any], raw: str) -> bool:
    if name_hit(org, raw):
        return True
    hay = haystack(org)
    terms_ok = all(_term_hit(t, hay) for t in want["terms"])
    if want["go"]:
        return in_go(org, want["go"]) and terms_ok
    return terms_ok


def mentions(org: dict[str, Any], want: dict[str, Any], raw: str) -> bool:
    """a company the WORDS land on that the concept map did not select.

    exactly what the tab used to return and now sets aside: filed in another
    bucket, but the description says the words. counted and one click away,
    never silently dropped.
    """
    if not want["go"] or match(org, want, raw):
        return False
    hay = haystack(org)
    return all(_term_hit(t, hay) for t in want["literal"])


# ---------------------------------------------------------------- js emitter

def emit() -> str:
    """the exact block index.html carries between its sentinels.

    strict JSON on purpose: it round-trips through json.loads for the drift
    check, so the check compares data and not whitespace luck.
    """
    lines = ["const CO_STOP=" + STOP_BEGIN
             + json.dumps(CO_STOP, separators=(",", ":")) + STOP_END + ";",
             "const COCONCEPTS=" + MAP_BEGIN + "["]
    for i, c in enumerate(CONCEPTS):
        body = json.dumps({"say": c["say"], "go": c["go"]}, separators=(",", ":"))
        lines.append(" " + body + ("," if i < len(CONCEPTS) - 1 else ""))
    lines.append("]" + MAP_END + ";")
    return "\n".join(lines)


def _extract(text: str, begin: str, end: str) -> Any:
    i = text.find(begin)
    j = text.find(end, i + 1)
    if i < 0 or j < 0:
        return None
    return json.loads(text[i + len(begin):j])


# ---------------------------------------------------------------- the checks

def check(quiet: bool = False) -> int:
    """returns the number of problems; 0 is a pass.

    `quiet` suppresses the notes and the ok line so selftest.py can call this
    and report through its own fail(). The notes are honest-empty reporting,
    not failures, and they have no place in another script's output.
    """
    problems: list[str] = []
    notes: list[str] = []
    schema = json.loads(SCHEMA_PATH.read_text())
    valid: set[tuple[str, Any]] = set()
    for s in schema["sectors"]:
        valid.add((s["name"], None))
        for c in s["categories"]:
            valid.add((s["name"], c))

    # 1. every target exists in the schema
    for c in CONCEPTS:
        for t in c["go"]:
            if (t[0], t[1]) not in valid:
                problems.append(
                    f"concept {c['say'][0]!r} points at {t[0]} / {t[1]} "
                    "which is not in data/schema.json")

    # 2. every category is reachable from a phrase that names THAT CATEGORY.
    #    reachable-via-its-whole-sector does not count: every sector here has a
    #    whole-sector phrase, so counting those would make this check pass
    #    always and prove nothing. the invariant that matters is that typing
    #    the words for a category gets you the category, not its sector.
    reached: set[tuple[str, Any]] = set()
    for c in CONCEPTS:
        for t in c["go"]:
            if t[1] is not None:
                reached.add((t[0], t[1]))
    for key in sorted(valid, key=lambda k: (k[0], k[1] or "")):
        if key[1] is not None and key not in reached:
            problems.append(
                f"{key[0]} / {key[1]} is in the schema and no phrase names it")

    # 3. no phrase is claimed by two concepts (the second one silently never
    #    fires, because the first consumes the text)
    seen: dict[str, str] = {}
    for c in CONCEPTS:
        for say in c["say"]:
            if say in seen:
                problems.append(
                    f"phrase {say!r} appears in two concepts "
                    f"({seen[say]} and {c['say'][0]})")
            seen[say] = c["say"][0]

    # 4. a phrase that is also a stop word can never survive to be matched
    stopset = set(CO_STOP)
    for c in CONCEPTS:
        for say in c["say"]:
            if say in stopset:
                problems.append(f"phrase {say!r} is also in CO_STOP")

    # 5. index.html has not drifted
    if INDEX_PATH.exists():
        html = INDEX_PATH.read_text()
        got_map = _extract(html, MAP_BEGIN, MAP_END)
        got_stop = _extract(html, STOP_BEGIN, STOP_END)
        if got_map is None:
            problems.append("index.html has no " + MAP_BEGIN + " block")
        elif got_map != [{"say": c["say"], "go": c["go"]} for c in CONCEPTS]:
            problems.append("index.html's concept map has drifted from "
                            "semantic.py - run --emit and paste it back")
        if got_stop is None:
            problems.append("index.html has no " + STOP_BEGIN + " block")
        elif got_stop != CO_STOP:
            problems.append("index.html's CO_STOP has drifted from semantic.py")

    # reported, never failed: a bucket the vocabulary can reach and no company
    # sits in. an honest empty, and the tab is supposed to say so on screen.
    if BOARD_PATH.exists():
        board = json.loads(BOARD_PATH.read_text())
        filled: set[tuple[str, Any]] = set()
        for o in board["organizations"]:
            filled |= buckets(o)
        for key in sorted(valid, key=lambda k: (k[0], k[1] or "")):
            if key[1] is not None and key not in filled:
                notes.append(f"{key[0]} / {key[1]} holds no companies today")

    if not quiet:
        for n in notes:
            print("note: " + n)
    for p in problems:
        print("FAIL: " + p)
    if problems:
        return len(problems)
    if not quiet:
        n_phrases = sum(len(c["say"]) for c in CONCEPTS)
        print(f"concept map ok: {len(CONCEPTS)} concepts, {n_phrases} phrases, "
              f"{len([k for k in valid if k[1] is not None])} categories all reachable")
    return 0


# ------------------------------------------------------------------ the cli

def run_query(q: str, hiring_only: bool) -> int:
    board = json.loads(BOARD_PATH.read_text())
    orgs = board["organizations"]
    want = parse(q)
    hits = [o for o in orgs if match(o, want, q)]
    names = [o for o in hits if name_hit(o, q)]
    hiring = [o for o in hits if o["open_roles"] > 0]

    if want["go"]:
        read = " + ".join(f"{t[0]} / {t[1] or 'all categories'}" for t in want["go"])
    else:
        read = "no concept - plain text on " + " ".join(want["terms"] or ["(nothing)"])
    print(f"query      {q!r}")
    print(f"read as    {read}")
    if want["said"]:
        print(f"phrases    {', '.join(want['said'])}")
    if want["terms"]:
        print(f"text terms {' '.join(want['terms'])}")
    print(f"matches    {len(hits)} companies, {len(hiring)} hiring now")
    if names:
        print(f"by name    {len(names)}: " + ", ".join(o["name"] for o in names[:8]))
    outs = [o for o in orgs if mentions(o, want, q)]
    if outs:
        print(f"set aside  {len(outs)} mention the words, filed elsewhere: "
              + ", ".join(f"{o['name']} [{o['category']}]" for o in outs[:5])
              + (" ..." if len(outs) > 5 else ""))
    wide = [t for t in want["go"] if t[1] is not None]
    if wide:
        secs = sorted({t[0] for t in wide})
        n = len([o for o in orgs
                 if any((s, None) in buckets(o) for s in secs)
                 and not match(o, want, q)])
        print(f"widen      {n} more in {', '.join(secs)} outside those categories")
    shown = hits if not hiring_only else hiring
    for o in sorted(shown, key=lambda o: (-o["open_roles"], o["name"]))[:15]:
        print(f"  {o['open_roles']:4d}  {o['name']}  [{o['sector']} / {o['category']}]")
    if len(shown) > 15:
        print(f"  ... {len(shown) - 15} more")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="validate the map and index.html's copy of it")
    ap.add_argument("--emit", action="store_true",
                    help="print the JS block for index.html")
    ap.add_argument("--query", help="run a query against data/board.json")
    ap.add_argument("--hiring", action="store_true",
                    help="with --query, list only companies hiring now")
    a = ap.parse_args(argv)
    if a.emit:
        print(emit())
        return 0
    if a.query is not None:
        return run_query(a.query, a.hiring)
    # clamped: check() returns a COUNT now, and an exit status is a byte -
    # 256 problems would exit 0 and read as a pass
    return 1 if check() else 0


if __name__ == "__main__":
    sys.exit(main())
