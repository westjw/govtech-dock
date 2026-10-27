"""Which sector and sub-sector a conference's buyers sit in.

    CROSSWALK[(block, department)] -> (sector, category or None)

ONE MAP, TWO READERS. supplier_categories files a supplier under the sector
of the shows it exhibited at; build_board stamps each conference row with
the same sector, so a sector's page in the app can list the conferences
that fall into it (owner, 2026-10-27: the Public Works page "will have the
subindustries and conferences that fall into it"). It lived inside
supplier_categories.py until then; a second copy would drift.
"""
from __future__ import annotations

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
