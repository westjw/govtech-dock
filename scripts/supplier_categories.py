#!/usr/bin/env python3
"""Put every supplier into the categories the govtech companies use.

    python3 scripts/supplier_categories.py            # the spread, nothing written
    python3 scripts/supplier_categories.py --write    # data/supplier_categories.json
    python3 scripts/supplier_categories.py --id spiewak

Step 2 of the supplier work. The owner, 2026-10-05: "throw them in the
categories we have for govtech" - the twelve sectors and their categories in
data/schema.json, so a supplier and a software company that sell to the same
fire department sit in the same place.

TWO KINDS OF EVIDENCE, AND EACH ANSWERS ONE QUESTION.
  1. WHERE IT SELLS: the conference it exhibited at. Every supplier came off
     an exhibitor list, every conference is filed under a department
     (conferences.json block/department), and CROSSWALK below turns that
     department into a sector - and into a category when the department is
     that specific (a wastewater show is Public Works / Water). The source
     is the exhibitor list itself: its url and the name as it was printed.
  2. WHAT IT SELLS: its own website, read by supplier_pages.py. A category
     is assigned from the site only when the page text names it in at least
     two distinct ways (VOCAB), and the assignment quotes the sentence it
     rests on. One stray word is not a category; a menu is not a product.
When the site names nothing specific, the supplier sits in its sector's
"Suppliers & Services" - the honest answer for a company that sells to the
whole department - and never in a guessed category.

WHO IS PUBLISHED is decided here too, under the owner's rule of 2026-10-04
(nothing without a website): a website step 1 confirmed as theirs, or a found
website whose own text sells into the sector the conference says they sell
to. Everything else is held, sorted but unpublished, until a site turns up.
Junk and duplicates are never published (data/supplier_identity.json).

The output is the export SLED HQ reads (agreed 2026-10-04/05): fixed sector
keys and category slugs in the header, rows that refer only by key.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import pathlib
import re
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_profiles as fp  # noqa: E402
import supplier_pages as SP  # noqa: E402
from supplier_identity import clean_name, root  # noqa: E402

OUT = DATA / "supplier_categories.json"
PAGES = SP.cache_dir()            # one per machine (supplier_pages.cache_dir)
GENERIC = "Suppliers & Services"

# THE KEYS ARE FIXED FOREVER. SLED HQ joins on them; a sector renamed in
# schema.json is a new NAME on the same key, never a new key. selftest fails
# if a key changes or disappears.
SECTOR_KEYS = {
    "Public Safety": "public-safety", "Public Works": "public-works",
    "General Gov": "general-gov", "Parks & Rec": "parks-rec",
    "K-12 Schools": "k12-schools", "Transit & Parking": "transit-parking",
    "Utilities & Energy": "utilities-energy", "Airports & Aviation": "airports-aviation",
    "Courts & Justice": "courts-justice", "Health & Human Services": "health-human-services",
    "Higher Education": "higher-education", "Housing & Community Dev": "housing-community-dev",
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower().replace("&", "and")).strip("-")


def category_key(sector: str, category: str) -> str:
    return f"{SECTOR_KEYS[sector]}/{slug(category)}"


# --- 1. where it sells: conference department -> (sector, category or None)
# None means the department buys across the sector: GENERIC unless the
# supplier's own site names something specific. Checked 2026-10-05 against
# how the 2,041 govtech companies seen at each kind of conference are filed.
CROSSWALK = {
    ("Clerk, records, elections, legal", "Communications / PIO"): ("General Gov", "Citizen Services"),
    ("Clerk, records, elections, legal", "Elections"): ("General Gov", "Elections"),
    ("Clerk, records, elections, legal", "Municipal attorneys"): ("Courts & Justice", None),
    ("Clerk, records, elections, legal", "Municipal clerks"): ("General Gov", None),
    ("Clerk, records, elections, legal", "Records management"): ("General Gov", None),
    ("Clerk, records, elections, legal", "State attorneys general"): ("Courts & Justice", None),
    ("Community development", "Code enforcement"): ("General Gov", "Permitting & Licensing"),
    ("Community development", "Economic development"): ("Housing & Community Dev", "Planning & Economic Development"),
    ("Community development", "Housing authorities"): ("Housing & Community Dev", "Housing & Assistance"),
    ("Community development", "Planning & zoning"): ("Housing & Community Dev", "Planning & Economic Development"),
    ("Community development", "Real estate / land use"): ("Housing & Community Dev", "Planning & Economic Development"),
    ("Community development", "State housing finance"): ("Housing & Community Dev", "Housing & Assistance"),
    ("Courts, corrections, justice", "Corrections (state)"): ("Public Safety", "Corrections"),
    ("Courts, corrections, justice", "Court tech"): ("Courts & Justice", "Courts & Case Management"),
    ("Courts, corrections, justice", "Courts"): ("Courts & Justice", None),
    ("Courts, corrections, justice", "Jails (county)"): ("Public Safety", "Corrections"),
    ("Courts, corrections, justice", "Juvenile justice"): ("Courts & Justice", None),
    ("Courts, corrections, justice", "Probation & parole"): ("Courts & Justice", "Probation & Supervision"),
    ("Executive / administration", "Cities (elected)"): ("General Gov", None),
    ("Executive / administration", "City & county management"): ("General Gov", None),
    ("Executive / administration", "Counties"): ("General Gov", None),
    ("Executive / administration", "Performance / innovation"): ("General Gov", "Strategy & Performance"),
    ("Executive / administration", "State governors"): ("General Gov", None),
    ("Executive / administration", "State legislatures"): ("General Gov", None),
    ("Executive / administration", "Townships / small gov"): ("General Gov", None),
    ("Finance, procurement, HR, IT", "Assessors / property tax"): ("General Gov", "Finance & ERP"),
    ("Finance, procurement, HR, IT", "Auditors"): ("General Gov", "Finance & ERP"),
    ("Finance, procurement, HR, IT", "Employee benefits"): ("General Gov", "HR & Workforce"),
    ("Finance, procurement, HR, IT", "Finance / budget"): ("General Gov", "Finance & ERP"),
    ("Finance, procurement, HR, IT", "GIS"): ("General Gov", "IT & AI Platforms"),
    ("Finance, procurement, HR, IT", "Human resources"): ("General Gov", "HR & Workforce"),
    ("Finance, procurement, HR, IT", "IT / CIO (local)"): ("General Gov", "IT & AI Platforms"),
    ("Finance, procurement, HR, IT", "IT / CIO (state)"): ("General Gov", "IT & AI Platforms"),
    ("Finance, procurement, HR, IT", "IT / innovation"): ("General Gov", "IT & AI Platforms"),
    # Procurement officers buy everything, so a procurement show says
    # nothing about the category - and "Procurement & Payments" is the
    # category for procurement SOFTWARE, which most of these do not sell.
    ("Finance, procurement, HR, IT", "Procurement (local)"): ("General Gov", None),
    ("Finance, procurement, HR, IT", "Procurement (state)"): ("General Gov", None),
    ("Finance, procurement, HR, IT", "Retirement systems"): ("General Gov", "Finance & ERP"),
    ("Finance, procurement, HR, IT", "Risk management"): ("General Gov", None),
    ("Finance, procurement, HR, IT", "State budget"): ("General Gov", "Finance & ERP"),
    ("Finance, procurement, HR, IT", "State tax"): ("General Gov", "Finance & ERP"),
    ("Finance, procurement, HR, IT", "Treasurers"): ("General Gov", "Finance & ERP"),
    ("Health and human services", "Aging services"): ("Health & Human Services", "Aging & Veterans"),
    ("Health and human services", "Animal services"): ("General Gov", "Animal Services"),
    ("Health and human services", "Behavioral health"): ("Health & Human Services", "Behavioral Health"),
    ("Health and human services", "Child support"): ("Health & Human Services", "Child & Family Services"),
    ("Health and human services", "Child welfare"): ("Health & Human Services", "Child & Family Services"),
    ("Health and human services", "Health IT"): ("Health & Human Services", None),
    ("Health and human services", "Health care fraud"): ("Health & Human Services", "Benefits & Medicaid Systems"),
    ("Health and human services", "Human services"): ("Health & Human Services", None),
    ("Health and human services", "Local public health"): ("Health & Human Services", "Public Health"),
    ("Health and human services", "Medicaid integrity"): ("Health & Human Services", "Benefits & Medicaid Systems"),
    ("Health and human services", "Medicaid leadership"): ("Health & Human Services", "Benefits & Medicaid Systems"),
    ("Health and human services", "Medicaid systems"): ("Health & Human Services", "Benefits & Medicaid Systems"),
    ("Health and human services", "Public health data"): ("Health & Human Services", "Public Health"),
    ("Health and human services", "State public health"): ("Health & Human Services", "Public Health"),
    ("Health and human services", "Veterans services"): ("Health & Human Services", "Aging & Veterans"),
    ("Health and human services", "Workforce / unemployment"): ("Health & Human Services", "Workforce & Labor"),
    ("Higher education", "Business / finance"): ("Higher Education", "Business & Finance"),
    ("Higher education", "Campus safety"): ("Higher Education", "Campus Safety"),
    ("Higher education", "Facilities"): ("Higher Education", None),
    ("Higher education", "Financial aid"): ("Higher Education", "Business & Finance"),
    ("Higher education", "IT"): ("Higher Education", "IT & Administration"),
    ("Higher education", "Registrars"): ("Higher Education", "IT & Administration"),
    ("K-12 education", "Business officials"): ("K-12 Schools", None),
    ("K-12 education", "Principals"): ("K-12 Schools", None),
    ("K-12 education", "Pupil transportation"): ("K-12 Schools", "Transportation"),
    ("K-12 education", "School boards"): ("K-12 Schools", None),
    ("K-12 education", "School nutrition"): ("K-12 Schools", None),
    ("K-12 education", "School safety"): ("K-12 Schools", "School Safety"),
    ("K-12 education", "Special education"): ("K-12 Schools", None),
    ("K-12 education", "Superintendents"): ("K-12 Schools", None),
    ("K-12 education", "Technology directors"): ("K-12 Schools", None),
    ("Parks, recreation, libraries", "Parks & recreation"): ("Parks & Rec", None),
    ("Parks, recreation, libraries", "Public libraries"): ("General Gov", "Libraries"),
    ("Parks, recreation, libraries", "Rec facilities"): ("Parks & Rec", None),
    ("Parks, recreation, libraries", "Youth sports"): ("Parks & Rec", "Youth Sports & Leagues"),
    # 911 and dispatch buy for police, fire and EMS alike; the govtech
    # companies seen at APCO split evenly between Police and EMS.
    ("Public safety", "911 / dispatch"): ("Public Safety", None),
    ("Public safety", "EMS"): ("Public Safety", "EMS"),
    ("Public safety", "Emergency management"): ("Public Safety", "Emergency Mgmt"),
    ("Public safety", "Fire"): ("Public Safety", "Fire"),
    ("Public safety", "Fire code / building safety"): ("General Gov", "Permitting & Licensing"),
    ("Public safety", "Police"): ("Public Safety", "Police"),
    ("Public safety", "Sheriffs"): ("Public Safety", None),
    ("Public works and infrastructure", "Broadband"): ("Utilities & Energy", "Broadband & Connectivity"),
    ("Public works and infrastructure", "County engineers"): ("Public Works", "Streets"),
    ("Public works and infrastructure", "Facilities"): ("Public Works", None),
    ("Public works and infrastructure", "Fleet"): ("Public Works", "Fleet & Asset Mgmt"),
    ("Public works and infrastructure", "Public power"): ("Utilities & Energy", "Grid & Energy"),
    ("Public works and infrastructure", "Public works"): ("Public Works", None),
    ("Public works and infrastructure", "Solid waste"): ("Public Works", "Waste & Recycling"),
    ("Public works and infrastructure", "Stormwater"): ("Public Works", "Water"),
    ("Public works and infrastructure", "Streets / roads / DOT"): ("Public Works", "Streets"),
    ("Public works and infrastructure", "Traffic engineering"): ("Public Works", "Streets"),
    ("Public works and infrastructure", "Wastewater"): ("Public Works", "Water"),
    ("Public works and infrastructure", "Water"): ("Public Works", "Water"),
    ("Transportation", "Airports"): ("Airports & Aviation", None),
    ("Transportation", "DMV"): ("General Gov", "Permitting & Licensing"),
    ("Transportation", "Ports"): ("Transit & Parking", None),
    ("Transportation", "Rural transit"): ("Transit & Parking", None),
    ("Transportation", "Transit"): ("Transit & Parking", None),
}

# --- 2. what it sells: the words a page uses for each category -------------
# Phrases, not single loose words, wherever a word has an everyday meaning:
# "water-resistant" is not a water utility and "fire" is not a fire
# department. A category needs TWO distinct terms from its list.
V = {
    ("Public Safety", "Fire"): r"fire (department|service|rescue|apparatus|truck|engine|chief|station|fighting|fighter)s?|firefight\w*|turnout gear|scba|self-contained breathing|wildland|extrication|fire hose|hose nozzle|ladder truck|aerial ladder|pumper",
    ("Public Safety", "Police"): r"law enforcement|police|sheriff'?s? (office|department)|patrol|body[- ]worn|body cam\w*|duty gear|holsters?|less[- ]lethal|tasers?|ammunition|firearms? training|evidence (management|storage)|crime scene|forensic|interview room|tactical (gear|equipment|vest)|ballistic|k-?9|officer safety|use of force|pistols?|handguns?|rifles?|carbines?|firearms?|gun safes?|duty belts?|body armou?r|armou?r|law enforcement (agencies|officers|training)|patrol (cars?|vehicles?)|driving simulators?|use[- ]of[- ]force training|interrogation",
    ("Public Safety", "EMS"): r"\bems\b|ambulances?|paramedics?|emergency medical|stretchers?|defibrillat\w*|\baeds?\b|patient (care|transport)|prehospital|first responders? medical|cardiac arrest|cpr|naloxone|narcan|overdose",
    ("Public Safety", "Emergency Mgmt"): r"emergency management|emergency operations|disaster (response|recovery|preparedness)|mass notification|emergency (alert|notification)|preparedness|flood (barrier|protection|control)|evacuation|\b911\b|computer[- ]aided dispatch|dispatch (center|console)|public safety (radio|communications)|two[- ]way radio|land mobile radio|interoperab\w*|sandbags?|flood response|hazmat|hazardous materials|emergency response (equipment|plans?)",
    ("Public Safety", "Corrections"): r"correction(s|al)|jails?|inmates?|detention|incarcerat\w*|prisons?|commissary|inmate (healthcare|health care|phones?|communications)|correctional (healthcare|health care|facilities)|extraditions?|prisoner transport\w*",
    ("Public Works", "Water"): r"wastewater|stormwater|drinking water|water (treatment|utility|utilities|main|meter|distribution|infrastructure|quality|system)s?|sewers?|sewer(age)?|lift stations?|pump stations?|hydrants?|clarifier|filtration|biosolids|manholes?|collection systems?|valves?|pipe(line)?s?|drainage|culverts?|erosion control|pipe (inspection|rehabilitation|lining)|sewer (cleaning|inspection)|catch basins?|storm drains?|water quality",
    ("Public Works", "Streets"): r"pavement|asphalt|roadways?|street (maintenance|sweep\w*)|traffic (signal|control|safety|calming|management)s?|signage|pavement marking|striping|guardrails?|bridges?|snow (and|&) ice|de-?icing|road salt|crack seal\w*|potholes?|right[- ]of[- ]way|work zones?|pothole patch\w*|road (base|construction|maintenance|stabilization)|paving|curbs? and gutters?|sweepers?|line striping|crosswalks?",
    ("Public Works", "Waste & Recycling"): r"solid waste|recycl\w+|refuse|garbage|trash|landfills?|compost\w*|roll-?off|dumpsters?|waste (collection|hauling|management)|organics|refuse (bodies|containers|trucks)|garbage trucks?|waste containers?|carts and containers",
    ("Public Works", "Fleet & Asset Mgmt"): r"fleet (management|services|vehicles?)|truck bodies|upfit\w*|telematics|gps tracking|fuel (management|systems?)|asset management|work orders?|equipment rental|municipal vehicles?|heavy equipment|vehicle maintenance|excavators?|backhoes?|loaders?|dozers?|dump trucks?|construction equipment|fuel cards?|fleet fuel|vehicle wash\w*|fleet vehicles?|work trucks?|municipal trucks?",
    ("General Gov", "Citizen Services"): r"\b311\b|citizen engagement|resident engagement|constituent|government websites?|civic engagement|public meetings?|agenda management|community engagement|customer service portal",
    ("General Gov", "Permitting & Licensing"): r"permitting|permits?|licens(e|ing)|code enforcement|building (department|inspection|code|safety)|inspections?|plan review",
    ("General Gov", "Finance & ERP"): r"\berp\b|accounting|budgeting|payroll|financial (management|software|reporting|services)|banking|treasury|audit(ing|s)?|property tax|investment management|public finance",
    ("General Gov", "Procurement & Payments"): r"e-?procurement|procurement (software|platform|solutions?)|bid management|purchasing cards?|p-?cards?|payment processing|online payments?|merchant services|cooperative (purchasing|contracts?)|purchasing cooperative|job order contracting",
    ("General Gov", "HR & Workforce"): r"human resources|\bhr\b|employee benefits|benefits administration|recruit(ing|ment)|staffing|retirement plans?|workers'? comp\w*|employee (wellness|assistance)|training (programs?|courses?)|prescription (drug )?benefits?|employee (health|benefits) (plans?|programs?)|self-insured",
    ("General Gov", "Cemetery Management"): r"cemeter(y|ies)|burial|columbari\w+|memorial park",
    ("General Gov", "IT & AI Platforms"): r"cyber ?security|data centers?|managed (it|services)|it (services|consulting|infrastructure|staffing)|network(ing)? (solutions|infrastructure|security)|software development|\bgis\b|geospatial|digital transformation|enterprise software|it modernization",
    ("General Gov", "Strategy & Performance"): r"strategic planning|performance management|management consulting|organizational (development|assessment)|consulting firm|leadership development|process improvement",
    ("General Gov", "Elections"): r"elections?|voting|voters?|ballots?|poll(ing)? (place|worker|book)s?",
    ("General Gov", "Libraries"): r"librar(y|ies)|librarians?|books?|e-?books?|audiobooks?|publish(er|ing)|reading|literacy|bookmobile|periodicals",
    ("General Gov", "Animal Services"): r"animal (shelters?|control|welfare|services|care|cruelty)|shelter (software|management)|pet (adoption|licensing|microchip\w*)|veterinar\w+|kennels?|spay|neuter",
    ("Parks & Rec", "Recreation Management"): r"recreation (management|software)|program registration|membership management|activity registration|recreation centers?",
    ("Parks & Rec", "Youth Sports & Leagues"): r"youth sports|leagues?|sports equipment|athletic equipment|team uniforms|referees?|coach(es|ing)|jerseys|sports uniforms|team apparel",
    ("Parks & Rec", "Camps & Youth Programs"): r"summer camps?|day camps?|youth programs?|after[- ]school",
    ("Parks & Rec", "Outdoor Rec & Reservations"): r"campgrounds?|campsites?|reservations? (system|software)|trails?|outdoor recreation|marinas?|rv parks?|ziplines?|zip lines?|treetop|ropes courses?|glamping|cabins",
    ("Parks & Rec", "Facility Booking & Workforce"): r"facility (booking|scheduling|rental)|room booking|space reservation",
    ("Parks & Rec", "Aquatics"): r"aquatics?|swimming pools?|\bpools?\b|water parks?|splash pads?|lifeguards?|pool (equipment|chemicals|covers)|pool chemicals?|chlorine|flocculation|pool filtration|aquatic play|spray parks?",
    ("Parks & Rec", "Grounds, Irrigation & Turf"): r"\bturf\b|mowers?|mowing|irrigation|landscap\w+|grounds (maintenance|care|equipment)|athletic fields?|synthetic turf|sod|fertiliz\w+|weed control|turf care",
    # ONE category in schema.json, and it was two keys here: build() files
    # only under a name the schema holds, so 76 published suppliers -
    # playground and site-furnishing makers off parks shows - reached SLED
    # HQ with no placement at all (found 2026-10-23 from HQ's own count).
    # A shelter is a park's (Enwood's pavilions, Kwik Goal's team shelters)
    # unless an animal's: "animal shelters", "shelters and rescues".
    ("Parks & Rec", "Park Assets, Forestry & Visitor Data"): r"playgrounds?|play (equipment|structures?)|site furnishings|park benches|benches|picnic tables?|shade (structures?|sails?)|(?<!animal )(?<!services )(?<!rescues & )(?<!rescues and )shelters?(?! (?:and|&) rescues?)|skate ?parks?|bleachers|outdoor fitness|park (amenities|equipment)|restrooms?|bollards|trash receptacles|poured[- ]in[- ]place|rubber surfacing|safety surfacing|playground surfacing|disc golf|pickleball|climbing (structures?|nets?)|play structures?|outdoor musical|sports courts?|court surfacing|running tracks?|urban forestry|tree (care|inventory|planting)|arborists?|visitor (counting|counters?|data)|trail counters?",
    ("Parks & Rec", "Weather & Outdoor Safety"): r"lightning (detection|prediction)|weather (alert|monitoring|stations?)|outdoor warning",
    ("Parks & Rec", "Screening, Safety & Compliance"): r"background (checks?|screening)|volunteer screening|safety compliance",
    ("Parks & Rec", "Destination Marketing & Tourism"): r"tourism|destination marketing|visitors? bureau|\bdmos?\b|convention (and|&) visitors",
    ("Parks & Rec", "Events, Venues & Ticketing"): r"ticketing|special events|event (management|production|rentals?)|staging|tents|venues?|festivals?|stadiums?",
    ("Parks & Rec", "Volunteer Management"): r"volunteer management|volunteer (scheduling|software|engagement)",
    ("K-12 Schools", "School Safety"): r"school (safety|security)|visitor management|panic (button|alarm)|classroom (locks?|security)|security film|student safety|threat assessment",
    ("K-12 Schools", "Transportation"): r"school bus(es)?|student transportation|pupil transportation",
    ("K-12 Schools", "Operations & SIS"): r"student information|\bsis\b|school (nutrition|food service|cafeteria)|child nutrition|k-12 (software|operations)|district operations|classroom technology|education technology|edtech",
    ("Transit & Parking", "Fare & Payments"): r"fare (collection|payment|system|box)s?|fareboxes?|transit (payment|ticketing)|mobile ticketing|smart cards?",
    ("Transit & Parking", "Fleet & Operations"): r"\bbus(es)?\b|transit (vehicles?|agencies|operations|fleet)|motor ?coach\w*|rail(cars?|way|road)?|light rail|electric bus(es)?|cad/?avl|scheduling software|transit maintenance|zero[- ]emission|transit bus(es)?|bus parts|coach(es)?|trams?|rolling stock|rail vehicles?|railway|trains?|bus hvac|driver seats?|bus air conditioning|train control|traction|brake systems?|vehicle diagnostics|electric buses|bus depots?",
    ("Transit & Parking", "Rider Experience"): r"passenger information|real[- ]time (arrival|information)|bus shelters?|rider (experience|information)|digital signage|onboard (wifi|wi-fi|information)|wayfinding|passenger information (systems?|displays?)|stop announcements?|ada (audio|announcements?)|on-?board displays?|platform screens?",
    ("Transit & Parking", "Demand-Response & AV"): r"paratransit|microtransit|demand[- ]response|on[- ]demand transit|autonomous (vehicle|shuttle)s?|dial-a-ride|wheelchair (lifts?|securement)|mobility (devices|equipment)",
    ("Transit & Parking", "Parking & Curb"): r"parking (management|enforcement|meters?|garages?|operations|permits?)|curb (management|space)|citations?|license plate recognition|\blpr\b|pay stations?",
    ("Utilities & Energy", "Street Lighting & Poles"): r"street ?lights?|street lighting|light poles|utility poles|led lighting|area lighting",
    ("Utilities & Energy", "Grid & Energy"): r"electric utilit(y|ies)|power (grid|distribution|generation|systems?)|transformers?|substations?|energy (storage|efficiency|management|services)|solar|generators?|ev charging|electric vehicle charging|microgrids?|public power|energy savings performance|espc|microgrid\w*|battery (energy )?storage|charging infrastructure|ev chargers?|charging stations?|electrification",
    ("Utilities & Energy", "Billing & Customer Systems"): r"utility billing|customer information systems?|meter data management|\bami\b|advanced metering|smart meters?",
    ("Utilities & Energy", "Broadband & Connectivity"): r"broadband|fiber(-optic)?|wireless (networks?|connectivity)|connectivity|internet service|telecommunications?",
    ("Airports & Aviation", "Airfield & Operations"): r"airfield|runways?|taxiways?|ground support equipment|aircraft (rescue|deicing|towing|maintenance)|\barff\b|airfield lighting|\bfod\b|aviation fuel|hangars?|runway rubber removal|airfield ground lighting|aircraft guidance|ground lighting",
    ("Airports & Aviation", "Terminal & Passenger Experience"): r"terminals?|passengers?|baggage (handling|systems?)|jet bridges?|passenger boarding|concessions|airport (signage|seating|wayfinding)",
    ("Airports & Aviation", "Security & Screening"): r"checkpoints?|security screening|\btsa\b|x-?ray (screening|systems?)|access control|perimeter (security|intrusion)",
    ("Airports & Aviation", "Parking & Ground Transport"): r"airport parking|ground transportation|shuttle (bus|service)s?|rental car (facilit|center)",
    ("Courts & Justice", "Courts & Case Management"): r"courts?(rooms?)?|judicial|case management|dockets?|jury|court (reporting|technology|records)",
    ("Courts & Justice", "Prosecution & Defense"): r"prosecutors?|district attorneys?|public defenders?|legal research|litigation|attorneys?|law firms?",
    ("Courts & Justice", "Probation & Supervision"): r"probation|parole|electronic monitoring|ankle monitor\w*|gps monitoring|drug testing|community supervision|pretrial",
    ("Health & Human Services", "Public Health"): r"public health|vaccines?|immuniz\w+|epidemiolog\w+|health departments?|disease (surveillance|prevention)|laborator(y|ies)",
    ("Health & Human Services", "Benefits & Medicaid Systems"): r"medicaid|benefits eligibility|\bsnap\b|\bwic\b|\btanf\b|eligibility (determination|systems?)|managed care",
    ("Health & Human Services", "Child & Family Services"): r"child welfare|foster care|child support|family services|adoption services|child protective",
    ("Health & Human Services", "Behavioral Health"): r"behavioral health|mental health|substance (use|abuse)|addiction|crisis (services|response|line)|recovery services|psychiatric",
    ("Health & Human Services", "Case Management & Social Care"): r"social services|human services|homeless\w*|case managers?|social work\w*|housing assistance",
    ("Health & Human Services", "Aging & Veterans"): r"older adults|seniors?|aging services|elder care|veterans?|assisted living|home care",
    ("Health & Human Services", "Workforce & Labor"): r"workforce development|job training|unemployment|apprenticeships?|job seekers?|career services",
    ("Higher Education", "IT & Administration"): r"higher education|universit(y|ies)|colleges?|campus (it|technology|administration)|student (success|records)|registrars?|enrollment",
    ("Higher Education", "Business & Finance"): r"financial aid|tuition|student accounts|bursar|campus (finance|payments)",
    ("Higher Education", "Campus Safety"): r"campus (safety|security|police)|university police",
    ("Housing & Community Dev", "Housing & Assistance"): r"affordable housing|housing authorit(y|ies)|public housing|section 8|housing choice|rental assistance|homeownership",
    ("Housing & Community Dev", "Planning & Economic Development"): r"economic development|urban planning|zoning|land use|community development|site selection|downtown revitali\w+|real estate development",
}
VOCAB = {k: re.compile(r"\b(" + v + r")\b", re.I) for k, v in V.items()}

# Words that place a page in a SECTOR without naming a category. Used only to
# confirm a found website (does this site sell into the part of government
# the conference says this supplier sells to?), never to assign a category.
SECTOR_WORDS = {
    "Public Safety": r"public safety|first responders?|law enforcement|police|\bpd\b|sheriffs?|fire(fighters?| departments?| service)|\bems\b|emergency services|public safety professionals",
    "Public Works": r"public works|municipal (infrastructure|utilities|water)|infrastructure|utilities|water|wastewater|streets?|roads?|highways?|\bdot\b",
    "General Gov": r"government|municipal(ities)?|cities|counties|county|local government|public sector|public agencies|state (and|&) local",
    "Parks & Rec": r"parks?|recreation|playgrounds?|aquatics?|outdoor|athletic|sports|camps?|trails?",
    "K-12 Schools": r"k-?12|school districts?|schools?|students?|classrooms?|educators?|teachers?",
    "Transit & Parking": r"transit|transportation|bus(es)?|rail|parking|passengers?|riders?|mobility",
    "Utilities & Energy": r"utilit(y|ies)|energy|electric|power|lighting|broadband|fiber",
    "Airports & Aviation": r"airports?|aviation|aircraft|airlines?|airfield|terminals?",
    "Courts & Justice": r"courts?|justice|legal|judicial|attorneys?|probation|parole",
    "Health & Human Services": r"health|human services|social services|medicaid|behavioral|patients?|care",
    "Higher Education": r"higher education|universit(y|ies)|colleges?|campus(es)?|students?",
    "Housing & Community Dev": r"housing|community development|economic development|planning|zoning",
}
SECTOR_RX = {k: re.compile(r"\b(" + v + r")\b", re.I) for k, v in SECTOR_WORDS.items()}


def sells_into(pages: list[dict], sector: str) -> int:
    """Distinct sector-or-category terms the pages use for one sector."""
    rxs = [SECTOR_RX[sector]] + [rx for (sec, _), rx in VOCAB.items() if sec == sector]
    terms = set()
    for pg in pages:
        for rx in rxs:
            terms.update(m.group(0).lower() for m in rx.finditer(pg["text"]))
    return len(terms)

# A registrar's for-sale page: "forsale.dynadot.com/concept-development.com"
# was on file as a supplier's website and passed step 1.
FOR_SALE = re.compile(r"//forsale\.|dynadot\.com|afternic\.com|sedo\.com|hugedomains\.com|"
                      r"//dan\.com|undeveloped\.com|/forsale", re.I)

MIN_TERMS = 2          # distinct terms a page must use before a category is assigned
QUOTE_MAX = 300


def schema_pairs() -> dict:
    s = json.loads((DATA / "schema.json").read_text())
    return {x["name"]: list(x["categories"]) for x in s["sectors"]}


# No address leaves this script: a quoted sentence or a name off an exhibitor
# list can carry one, and the export is committed to a public repository
# (same rule as step 1's _scrub).
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")


# NOR A PHONE NUMBER, NOR AN @. SLED HQ refuses any placement whose quote
# carries a contact, and refused Compass Health Network's at main 126d1cf
# (2026-10-23): "BEHAVIORAL HEALTH CRISIS CENTERS: 833-356-2427 (TOLL FREE)",
# picked as the sentence that says what the company sells. So what_it_sells
# never quotes a sentence carrying a phone number or an @ (HQ reads an @
# before a letter as an address, a brand's "Sparks@Play" too), and build()
# blanks any phone that still reaches a quote or a name. Only a number shaped
# like a phone is caught: "CRISIS LINE: 988" is not one, and is quoted.
# Read the way HQ's detector reads (src/lib/bid-record-privacy.ts
# contactSpans, with its contactNorm): digits in groups joined by a dash,
# dot, slash, parenthesis or space, shaped like a phone - 3-3-4, 1-3-3-4, ten
# digits, eleven starting with 1, 3-7, 6-4, or 3-4 joined by a dash that is
# not a year - and not glued to a letter, a $ or a longer number
# ("$4,100,000.00", "2024-001-0042").
PHONE_RUN = re.compile(r"(?<![\w$#,./=?&])(?:\+\s*)?\(?\d+(?:(?:[ \t]*[-./][ \t]*|[ \t\n]+"
                       r"|[ \t]*\)[ \t]*[-./]?[ \t]*|[ \t]*\([ \t]*)\d+)*\)?(?![.,]\d)")
PHONE_SHAPES = {"3,3,4", "1,3,3,4", "10", "11", "3,7", "1,10", "6,4"}
# HQ's contactNorm, one character for one so spans still fit
CONTACT_NORM = str.maketrans(
    {**{chr(c): "-" for c in [*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D, 0xAD]},
     **{chr(c): " " for c in [0xA0, *range(0x2000, 0x200E), 0x202F, 0x205F, 0x2060, 0x3000, 0xFEFF]},
     "＠": "@", "﹫": "@"})
AT = re.compile(r"(?:@|\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>)\s*[^\W\d_]", re.I)


def carries_at(s: str) -> bool:
    return bool(AT.search((s or "").translate(CONTACT_NORM)))


def phone_spans(s: str) -> list[tuple[int, int]]:
    out = []
    s = (s or "").translate(CONTACT_NORM)
    for m in PHONE_RUN.finditer(s):
        groups = [(d.group(0), m.start() + d.start(), m.start() + d.end())
                  for d in re.finditer(r"\d+", m.group(0))]
        i = 0
        while i < len(groups):
            before = s[groups[i - 1][2]:groups[i][1]] if i else " "
            hit = None
            if re.fullmatch(r"[ \t\n(]*", re.sub(r"\($", "", before)):
                for j in range(i, min(len(groups), i + 4)):
                    after = s[groups[j][2]:groups[j + 1][1]] if j + 1 < len(groups) else " "
                    if not re.fullmatch(r"[ \t\n(]*", re.sub(r"^\)", "", after)):
                        continue
                    gs = [g for g, _, _ in groups[i:j + 1]]
                    shape = ",".join(str(len(g)) for g in gs)
                    ok = shape in PHONE_SHAPES
                    if shape in ("11", "1,10", "1,3,3,4"):
                        ok = gs[0][0] == "1"
                    if shape == "3,4":
                        ok = "-" in s[groups[i][2]:groups[j][1]] \
                            and not re.fullmatch(r"(19|20)\d\d", gs[1])
                    if shape == "10" and gs[0][0] == "0":
                        ok = False
                    if ok:
                        hit = j
                        break
            if hit is not None:
                a, b = groups[i][1], groups[hit][2]
                # "(512) 555-0142" takes its "(", "+1 512..." its "+"; a ")"
                # after the number is the sentence's ("(Ph: 512-555-0142)")
                if re.search(r"\(\s*$", s[:a]) and re.match(r"\s*\)", s[groups[i][2]:]):
                    a = re.search(r"\(\s*$", s[:a]).start()
                plus = re.search(r"\+\s*$", s[:a])
                out.append((plus.start() if plus else a, b))
                i = hit + 1
            else:
                i += 1
    return out


def without_phones(s: str) -> str:
    for _ in range(3):                     # a blank can join what is left into a new run
        spans = phone_spans(s)
        if not spans:
            break
        for a, b in reversed(spans):
            s = s[:a] + "[phone]" + s[b:]
    return s


def clip(quote: str) -> str:
    """At most QUOTE_MAX characters, as HQ counts them (code points)."""
    if len(quote) <= QUOTE_MAX:
        return quote
    cut = quote[:QUOTE_MAX - 3]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "..."


# A WEBSITE OF THEIR OWN. SLED HQ stores a published supplier only when its
# website is a domain of its own (HQ's websiteDomain(), src/lib/auth/
# domains.ts: the claim flow's one definition), and refused two at main
# 126d1cf (HQ, 2026-10-23):
#  - A LINK WRAPPER names the address it opens in its own query, and opens
#    it: www-youngwilliams-com's "website" was an Outlook safelinks link, and
#    the pages read through it are Young Williams' own (child support, SNAP,
#    housing assistance). The address is taken out of the wrapper, never
#    guessed. A wrapper that names none, or a link shortener or mail filter
#    whose address cannot be read, is no website.
#  - A SHARED HOST - a site builder's or a host's subdomain, a preview build
#    (f1rst-preview.netlify.app), a cloud bucket - or a page on somebody
#    else's site (github.com/acme, linktr.ee/acme: only the bare homepage is
#    the host's own) is not a domain of the supplier's own, and holds it.
#  - What HQ's URL check refuses is never published: a bare host gets
#    "https://" (as an exhibitor list prints it, "playcraftsystems.com"), a
#    scheme is lower-cased, and an address that is not a web address at all
#    (a space in its host, a typo in its scheme) stays "website unread", in
#    the admin's Supplier websites queue where a person can give the right one.
# The lists are HQ's, copied so the two products agree on what a website is;
# HQ's door still refuses, by name, anything these miss.
WRAPPERS = (               # (host pattern, path prefix, query keys)
    (re.compile(r"(^|\.)safelinks\.protection\.(outlook\.com|office365\.us)$"), "", ("url",)),
    (re.compile(r"^(www\.)?google\.(com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})$"), "/url", ("q", "url")),
)
# redirectors whose target is encoded or held on their side: never a website
REDIRECTORS = {"urldefense.com", "urldefense.proofpoint.com", "protect-us.mimecast.com",
               "linkprotect.cudasvc.com", "clicktime.symantec.com", "t.co", "bit.ly", "lnkd.in",
               "tinyurl.com", "ow.ly", "goo.gl", "rebrand.ly", "buff.ly"}
PLATFORM = re.compile(
    r"(^|\.)(wixsite|squarespace|weebly|godaddysites|sites\.google|wordpress|blogspot|myshopify|"
    r"webflow\.io|netlify\.app|vercel\.app|github\.io|linkedin|facebook|herokuapp|azurewebsites|"
    r"appspot|firebaseapp|web\.app|pages\.dev|workers\.dev|notion\.site|carrd\.co|business\.site|"
    r"square\.site|mystrikingly|jimdosite|webnode|yolasite|wpengine|hubspotpagebuilder|substack|"
    r"crunchbase|twitter|instagram|youtube|gitlab\.io|bitbucket\.io|gitbook\.io|amazonaws|"
    r"cloudfront\.net|azureedge\.net|windows\.net|googleapis|googleusercontent|onrender\.com|"
    r"fly\.dev|glitch\.me|surge\.sh|replit\.app|framer\.website|framer\.app|tumblr\.com|ghost\.io|"
    r"beehiiv\.com|canva\.site)\.")
PROFILE_HOSTS = {
    "github.com", "gitlab.com", "bitbucket.org", "medium.com", "notion.so",
    "linktr.ee", "beacons.ai", "bio.link", "about.me",
    "x.com", "tiktok.com", "threads.net", "reddit.com",
    "google.com", "g.page", "apple.com",
    "wellfound.com", "angel.co", "ycombinator.com", "producthunt.com",
    "g2.com", "capterra.com", "glassdoor.com", "indeed.com", "zoominfo.com",
    "bloomberg.com", "pitchbook.com", "yelp.com", "bbb.org", "dnb.com"}
FREE_MAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com",
    "proton.me", "protonmail.com", "pm.me", "gmx.com", "mail.com", "zoho.com",
    "hey.com", "fastmail.com", "msn.com", "comcast.net", "verizon.net"}
NOT_OWN = "the website on file is not a domain of their own"
NOT_WEB = "website unread (the address on file is not a web address)"
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST = re.compile(rf"(?:{LABEL}\.)+[a-z]{{2,}}")


def web_address(url: str | None) -> str:
    """The address as HQ's URL check takes it: a scheme, lower-cased, and an
    entity-encoded "&amp;" read as "&". A bare host gets "https://", the
    scheme supplier_pages reads it over."""
    u = (url or "").strip()
    while "&amp;" in u:
        u = u.replace("&amp;", "&")
    m = re.match(r"([a-z][a-z0-9+.-]*)://", u, re.I)
    if m:
        return m.group(1).lower() + u[m.end(1):]
    return f"https://{u}" if u else ""


def unwrap(url: str | None) -> str | None:
    """The address a link wrapper opens; any other address as given; None
    for a wrapper that names no http(s) address."""
    u = (url or "").strip()
    for _ in range(5):                                   # a wrapper inside a wrapper
        p = urlsplit(web_address(u))
        h = (p.hostname or "").lower()
        for host, path, keys in WRAPPERS:
            if host.search(h) and p.path.startswith(path):
                q = parse_qs(p.query)
                target = next((q[k][0] for k in keys if q.get(k)), "")
                if not re.match(r"https?://", target, re.I):
                    return None
                u = target
                break
        else:
            return u
    return None


def site_problem(url: str) -> str | None:
    """Why this address cannot be published as the supplier's website, or
    None when it can: NOT_OWN for a page on someone else's host, NOT_WEB for
    what is not a web address at all."""
    p = urlsplit(web_address(url))
    h = (p.hostname or "").lower().rstrip(".")
    h = h[4:] if h.startswith("www.") else h
    if PLATFORM.search(f".{h}.") or h in REDIRECTORS:     # before any other test:
        return NOT_OWN                                    # "clean_harbors.wixsite.com"
    if p.scheme not in ("http", "https") or not HOST.fullmatch(h) or "@" in p.netloc:
        return NOT_WEB        # a space in the host, a typo in the scheme, an IP, an email
    reg = root(h)
    if reg in FREE_MAIL:
        return NOT_OWN
    rest = (p.path if p.path not in ("", "/") else "") + p.query + p.fragment
    return NOT_OWN if reg in PROFILE_HOSTS and (h != reg or rest) else None


def not_own(url: str) -> bool:
    """True when this address cannot be published as their website."""
    return site_problem(url) is not None


def own_site(url: str | None) -> tuple[str | None, str | None]:
    """(the address to publish, why there is none). Both None when nothing
    is on file; the address is never one HQ's door refuses."""
    if not url:
        return None, None
    u = unwrap(url)
    if u is None:
        return None, NOT_OWN
    problem = site_problem(u)
    return (None, problem) if problem else (web_address(u), None)


# --- where each supplier exhibited ------------------------------------------
TAG_RE = re.compile(r"exhibited at ([^;,\n]+?)(?:$| -|;|,)")


def conference_index() -> tuple[dict, dict]:
    """event tag -> (block, department, url); plus a year-free fallback."""
    cfs = json.loads((DATA / "conferences.json").read_text())["conferences"]
    by_tag, by_stem = {}, {}
    for c in cfs:
        place = (c.get("block"), c.get("department"))
        url = c.get("exhibitor_url") or c.get("url")
        for t in [c.get("event_tag")] + list(c.get("prior_tags") or []):
            if not t:
                continue
            by_tag[t] = (place, url)
            by_stem.setdefault(re.sub(r"\s*\d{4}$", "", t), (place, url))
    # each exhibitor list's own address is the better source when we have it
    for p in DATA.glob("exhibitors_*.json"):
        try:
            d = json.loads(p.read_text())
        except Exception:                                   # noqa: BLE001
            continue
        t, u = d.get("event_tag"), d.get("source_url")
        if t in by_tag and u:
            by_tag[t] = (by_tag[t][0], u)
    return by_tag, by_stem


def tags_of(s: dict) -> list[str]:
    out = []
    for m in TAG_RE.finditer(s.get("description") or ""):
        t = m.group(1).strip()
        if t not in out:
            out.append(t)
    src = (s.get("source") or "").replace("conference sweep:", "").strip()
    for t in [x.strip() for x in src.split(";") if x.strip()]:
        if t not in out:
            out.append(t)
    return out


def where_it_sells(s: dict, by_tag: dict, by_stem: dict) -> list[dict]:
    out, seen = [], set()
    for t in tags_of(s):
        hit = by_tag.get(t) or by_stem.get(re.sub(r"\s*\d{4}$", "", t))
        if not hit:
            continue
        (place, url) = hit
        mapped = CROSSWALK.get(place)
        if not mapped or mapped in seen:
            continue
        seen.add(mapped)
        out.append({"sector": mapped[0], "category": mapped[1], "conference": t,
                    "source": {"url": url, "quote": ADDRESS.sub("[address]", s.get("name") or "")}})
    return out


# --- what it sells, from its own pages --------------------------------------
SENT = re.compile(r"(?<=[.!?])\s+|\n+")



def own_text(sid: str) -> tuple[list[dict], str | None]:
    p = PAGES / f"{sid}.json"
    if not p.exists():
        return [], "not read"
    rec = json.loads(p.read_text())
    pages = [x for x in rec.get("pages") or [] if x.get("text")]
    if not pages:
        return [], rec.get("unread") or "no readable page"
    try:
        pages = fp.dechrome(pages)
    except Exception:                                       # noqa: BLE001
        pass
    return [x for x in pages if x.get("text")], None


def what_it_sells(pages: list[dict]) -> list[dict]:
    """Categories the pages name in >= MIN_TERMS distinct ways, strongest
    first, each with the sentence that carries its most-used term."""
    found = []
    for (sector, cat), rx in VOCAB.items():
        terms = collections.Counter()
        best = None
        for pg in pages:
            for sent in SENT.split(pg["text"]):
                sent = sent.strip()
                if len(sent) < 25:
                    continue                     # a menu label, not a sentence
                for m in rx.finditer(sent):
                    t = m.group(0).lower()
                    terms[t] += 1
                    # a contact line is never the quote; nor is a sentence with an
                    # @ in it, which HQ's door reads as an address before a letter
                    # ("Sparks@Play is your trusted source...", refused 2026-10-23)
                    if phone_spans(sent) or carries_at(sent):
                        continue
                    if best is None or (len(sent) <= QUOTE_MAX and len(best[1]) > QUOTE_MAX):
                        best = (pg["url"], sent)
        if len(terms) >= MIN_TERMS and best:
            quote = clip(best[1])
            quote = ADDRESS.sub("[address]", quote)
            found.append({"sector": sector, "category": cat,
                          "terms": len(terms), "uses": sum(terms.values()),
                          "source": {"url": best[0], "quote": quote}})
    found.sort(key=lambda f: (-f["terms"], -f["uses"]))
    return found


# --- putting it together ----------------------------------------------------
def place(s: dict, ident: dict, by_tag: dict, by_stem: dict, ruling: dict | None = None) -> dict:
    sid = s["id"]
    verdict = (ident or {}).get("verdict")
    row = {"supplier_id": sid, "name": s.get("name"), "verdict": verdict}
    if verdict in ("not_a_company", "listing_menu"):
        row["status"] = "not_a_company"
        return row
    # A GOVERNMENT BODY BUYS FROM SUPPLIERS (merge_supplier_records, owner
    # 2026-10-23): a state department filed off a floor is not one.
    if s.get("not_a_supplier"):
        row["status"] = "not_a_company"
        return row
    if verdict in ("duplicate", "related") and ident.get("duplicate_of"):
        row["status"] = {"merged_into": {"kind": "company" if ident.get("on_board") else "supplier",
                                         "id": ident["duplicate_of"]}}
        return row

    where = where_it_sells(s, by_tag, by_stem)
    pages, unread = own_text(sid)
    sells = what_it_sells(pages) if pages else []
    sectors = {w["sector"] for w in where}

    assignments = []
    # ONE category from its own site: the one the pages name in the most
    # distinct ways. Measured 2026-10-05 against 201 blind labels: the top
    # category was right 62% of the time, every category it named 48%.
    # It must sit in a sector the conferences say this supplier sells to -
    # unless every conference was a GENERALIST show (cities, counties,
    # procurement: General Gov with no category), which says only that it
    # sells to government, so its own site decides the sector.
    generalist = not where or all(w["sector"] == "General Gov" and w["category"] is None
                                  for w in where)
    for f in sells:
        if not generalist and f["sector"] not in sectors:
            continue
        assignments.append({"sector": f["sector"], "category": f["category"],
                            "basis": "own_site", "terms": f["terms"], "source": f["source"]})
        break
    covered = {a["sector"] for a in assignments}
    for w in where:
        if w["sector"] in covered or (generalist and assignments):
            continue
        cat = w["category"] or GENERIC
        assignments.append({"sector": w["sector"], "category": cat,
                            "basis": "conference", "conference": w["conference"],
                            "source": w["source"]})
        covered.add(w["sector"])
    row["assignments"] = assignments

    on_file = (ident or {}).get("url") or (ident or {}).get("website_was")
    site, problem = own_site(on_file)
    if on_file and FOR_SALE.search(on_file):
        row["status"], row["held"] = "held", "the website on file is a domain for sale"
    elif problem:
        row["status"], row["held"] = "held", problem
    elif verdict in ("theirs", "found", "unconfirmed", "found_review") and site and pages:
        # A WEBSITE IS CONFIRMED BY WHAT IT SELLS: its own text speaks the
        # language of a sector the conference says this supplier sells to
        # (two distinct terms), or names a category outright. This holds for
        # step 1's "theirs" too: that check matched the NAME on the page, and
        # a same-named business passes it (advanced-drainage.com is an
        # Indiana septic contractor, acuity.com an insurer). Measured on 60
        # "theirs" sites the reviewers checked: precision 87% -> 93%, at the
        # cost of holding 10 real ones whose pages say too little.
        agrees = (any(a["basis"] == "own_site" for a in assignments)
                  or any(sells_into(pages, sec) >= MIN_TERMS for sec in sectors))
        if agrees:
            row["status"], row["website"] = "publish", site
        else:
            row["status"], row["held"] = "held", "website not confirmed by what it sells"
    else:
        row["status"], row["held"] = "held", ("no website" if not site else
                                              f"website unread ({unread or verdict})")
    # A PERSON'S WORD BEATS THE TEXT TEST, both ways (admin "Supplier
    # websites", data/supplier_rulings.json). The placement is untouched: a
    # ruling is about whose website it is, not where the supplier sells.
    # The address must still be a domain of their own: a person rules on
    # WHOSE it is, and a page on a shared host is nobody's domain.
    v = (ruling or {}).get("verdict")
    given = on_file if v == "theirs" else (ruling or {}).get("url") if v == "other" else None
    if given:
        released, problem = own_site(given)
        if released:
            row["status"], row["website"], row["website_by"] = "publish", released, "person"
            row.pop("held", None)
        else:
            row.pop("website", None)
            row["status"], row["held"] = "held", problem
    elif v == "not_theirs":
        row.pop("website", None)
        row["status"], row["held"] = "held", "a person said the website on file is not theirs"
    return row


def one_per_id(suppliers: list) -> list:
    """One record per id. Two records carrying one id were the same company
    filed twice from two exhibitor floors (panasonic-connect-toughbook:
    "Panasonic Connect (TOUGHBOOK)" off AWWA and APCO, "Panasonic Connect /
    TOUGHBOOK" off IAFC), so they are read as one: the first record, with
    every conference both of them name. suppliers.json is not rewritten."""
    out, at = [], {}
    for s in suppliers:
        if s["id"] not in at:
            at[s["id"]] = len(out)
            out.append(dict(s))
            continue
        first = out[at[s["id"]]]
        first["description"] = "; ".join(x for x in (first.get("description"), s.get("description")) if x)
        first["source"] = "; ".join(x for x in (first.get("source"), s.get("source")) if x)
        first["website"] = first.get("website") or s.get("website")
    return out


def build(suppliers: list, ident_rows: dict) -> dict:
    by_tag, by_stem = conference_index()
    pairs = schema_pairs()
    rulings = {}
    rp = DATA / "supplier_rulings.json"
    if rp.exists():
        rulings = json.loads(rp.read_text())
    rows = []
    for s in one_per_id(suppliers):
        rows.append(place(s, ident_rows.get(s["id"]) or {}, by_tag, by_stem,
                          rulings.get(s["id"])))
    sectors = [{"key": SECTOR_KEYS[n], "name": n} for n in pairs]
    industries = []
    for n, cats in pairs.items():
        for c in cats:
            industries.append({"slug": category_key(n, c), "name": c, "sector": SECTOR_KEYS[n]})
    suppliers_out, assignments = [], []
    unfiled = collections.Counter()
    for r in rows:
        out_row = {k: r[k] for k in ("supplier_id", "name", "status", "website", "website_by", "held")
                   if k in r}
        # A NAME OFF AN EXHIBITOR LIST CAN BE AN ADDRESS: 67 "names" are a
        # contact's email and a few carry one glued on. Step 1's cleaner
        # takes the glued part off; anything left is scrubbed.
        out_row["name"] = without_phones(ADDRESS.sub("[address]", clean_name(r.get("name") or "")))
        suppliers_out.append(out_row)
        for a in r.get("assignments") or []:
            if a["category"] not in pairs.get(a["sector"], []):
                unfiled[(a["sector"], a["category"])] += 1
                continue                 # never file under a name the schema lacks
            src = a["source"]
            url = unwrap(src["url"]) or src["url"]
            assignments.append({"supplier_id": r["supplier_id"],
                                "industry": category_key(a["sector"], a["category"]),
                                "basis": a["basis"],
                                "source": {"url": web_address(url) if url else url,
                                           "quote": clip(without_phones(src["quote"] or ""))}})
    # Said, never silent: a category the schema renamed lost 76 published
    # suppliers' only placement and nothing printed a word.
    for (sec, cat), n in unfiled.items():
        print(f"warning: {n} placement(s) under {sec} / {cat}, a category schema.json "
              f"does not hold, were left out", file=sys.stderr)
    return {"v": 1, "generated": dt.date.today().isoformat(),
            "note": "Suppliers in the govtech sectors and categories (data/schema.json). "
                    "Every assignment quotes its source; status follows the owner's rule "
                    "that nothing is published without a confirmed website.",
            "sectors": sectors, "industries": industries,
            "suppliers": suppliers_out, "assignments": assignments}


NOT_READ = "website unread (not read)"


def drift(export: dict, suppliers: list) -> dict:
    """How far the export is behind suppliers.json: {"new": ids never placed,
    "gone": ids it still lists that suppliers.json no longer holds}.

    A row marked merged_into is not gone: it is the export saying where the
    supplier went (merge_supplier_twins), and it stays until HQ has read it.
    Counting those made the selftest note fire on an export rebuilt minutes
    earlier."""
    sids = {s["id"] for s in suppliers}
    rows = export.get("suppliers") or []
    xids = {r["supplier_id"] for r in rows}
    merged = {r["supplier_id"] for r in rows if isinstance(r.get("status"), dict)}
    return {"new": sorted(sids - xids), "gone": sorted(xids - sids - merged)}


def cache_losses(out: dict, previous: dict) -> list[str]:
    """Suppliers the last export published that this build holds only because
    their page is not in data/supplier_pages here.

    THE CACHE IS GITIGNORED AND 102 MB, so a checkout can lack it, and then
    own_text() says "not read" for every supplier: all 3,038 published ones
    would leave SLED HQ with "website unread", a fact about this checkout
    reported as a fact about them. A page the last export read and this one
    could not is our absence, never theirs."""
    was = {r.get("supplier_id"): r.get("status") for r in previous.get("suppliers") or []}
    return [r["supplier_id"] for r in out.get("suppliers") or []
            if r.get("held") == NOT_READ and was.get(r["supplier_id"]) == "publish"]


def carry_merges(out: dict, previous: dict) -> int:
    """Keep the record of where a supplier went after it has left the file.

    merge_supplier_twins takes a twin out of suppliers.json and marks its row
    here merged_into a board company; a build reads suppliers.json, so the
    next one dropped the row and HQ lost the only word on where it went. The
    10-22 refresh dropped five (Oracle America, PowerDMS...). A merged row
    whose supplier is not in this build is carried forward as it was, with
    no assignments; a supplier back in the file is placed afresh. Returns
    how many were carried."""
    have = {r["supplier_id"] for r in out.get("suppliers") or []}
    carried = 0
    for r in previous.get("suppliers") or []:
        if isinstance(r.get("status"), dict) and r.get("supplier_id") not in have:
            out["suppliers"].append({"supplier_id": r["supplier_id"], "name": r.get("name"),
                                     "status": r["status"]})
            have.add(r["supplier_id"])
            carried += 1
    return carried


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--id", default="")
    ap.add_argument("--allow-uncached", action="store_true",
                    help="write even though suppliers the last export published have no page "
                         "here (only after re-reading them with supplier_pages.py)")
    a = ap.parse_args()
    suppliers = json.loads((DATA / "suppliers.json").read_text())
    ident = json.loads((DATA / "supplier_identity.json").read_text())["rows"]
    if a.id:
        by_tag, by_stem = conference_index()
        s = next(x for x in one_per_id(suppliers) if x["id"] == a.id)
        print(json.dumps(place(s, ident.get(a.id) or {}, by_tag, by_stem), indent=1))
        return 0
    out = build(suppliers, ident)
    st = collections.Counter(s["status"] if isinstance(s["status"], str) else "merged"
                             for s in out["suppliers"])
    print("status:", dict(st))
    basis = collections.Counter(x["basis"] for x in out["assignments"])
    print("assignments:", len(out["assignments"]), dict(basis))
    generic = sum(1 for x in out["assignments"] if x["industry"].endswith("/suppliers-and-services"))
    print(f"  in a sector's Suppliers & Services: {generic}")
    top = collections.Counter(x["industry"] for x in out["assignments"]).most_common(25)
    for k, n in top:
        print(f"  {n:5}  {k}")
    if a.write:
        if not PAGES.is_dir():
            print(f"refused: the page cache {PAGES} is not on this machine, so every "
                  f"published supplier would read as unread. Copy it to {SP.SHARED}, "
                  f"or rebuild it with supplier_pages.py --write.", file=sys.stderr)
            return 1
        previous = json.loads(OUT.read_text()) if OUT.exists() else {}
        carried = carry_merges(out, previous)
        if carried:
            print(f"kept {carried} merged supplier(s) that have left suppliers.json")
        lost = cache_losses(out, previous)
        if lost and not a.allow_uncached:
            print(f"refused: {len(lost)} supplier(s) the last export published have no page "
                  f"in {PAGES} ({', '.join(lost[:6])}"
                  f"{' ...' if len(lost) > 6 else ''}). Read them with supplier_pages.py "
                  f"--ids, or --allow-uncached once you have.", file=sys.stderr)
            return 1
        OUT.write_text(json.dumps(out, indent=1) + "\n")
        print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
