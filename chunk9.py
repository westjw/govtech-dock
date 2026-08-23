import json
OUT="/private/tmp/claude-501/-Users-wyethwest/bae54666-b02f-4b55-98ed-4e5a5f866107/scratchpad/dock-work/data/conference_intake/researched/batch-2.json"
d=json.load(open(OUT))
d["researched"]+=[
{"verdict":"scope_review","company_name":"ScreenComply","website":"https://screencomply.ai","description":"Software that detects undisclosed AI assistance in live interviews, proctored exams, and recorded video sessions","sector":"Higher Education","category":"IT & Administration","source_event":"EDUCAUSE 2026","notes":"Three named markets: technical hiring, higher education and certification proctoring, and assessment platforms, so corporate recruiting is co-equal with education. Site notes SDVOSB certification and FedRAMP-aligned controls. HQ and founding year are not published on the site and were not found by these searches; the company appears to be very early stage."},
{"verdict":"scope_review","company_name":"Smartsheet","website":"https://www.smartsheet.com","description":"Work and project portfolio management platform sold to enterprises across every industry","hq_location":"Bellevue, WA","year_founded":2005,"sector":"General Gov","category":"IT & AI Platforms","source_event":"EDUCAUSE 2026","notes":"Horizontal work management vendor marketing on 85%+ Fortune 500 adoption; government and higher education are two of six named verticals. Legal name Smartsheet Inc. HQ and founding year from general reference sources because the site does not publish either."},
{"verdict":"govtech","company_name":"TeamDynamix","website":"https://www.teamdynamix.com","description":"No-code IT service management, asset management, project portfolio, and integration platform for universities, school districts, and government agencies","hq_location":"Columbus, OH","year_founded":2001,"sector":"Higher Education","category":"IT & Administration","source_event":"EDUCAUSE 2026","notes":"No URL was supplied; teamdynamix.com confirmed as the company site. Close to the scope-review line: higher ed, K-12, and public sector lead its market list, but it also sells to healthcare, financial services, manufacturing, hospitality, and retail, which makes it arguably horizontal ITSM like the HaloITSM candidate. Portfolio company of K1 Investment Management."},
{"verdict":"govtech","company_name":"Wooclap","website":"https://www.wooclap.com","description":"Live classroom polling and interactive question software for university instructors and corporate trainers","hq_location":"Brussels, Belgium","year_founded":2015,"sector":"Higher Education","category":"IT & Administration","source_event":"EDUCAUSE 2026","notes":"Higher education is the lead market (Duke, HEC Paris, University of Edinburgh) but corporate L&D is a stated second market, so the public-sector share is worth checking. HQ and founding year from general reference sources; neither appears on the site's about or contact pages."}
]
json.dump(d,open(OUT,"w"),indent=1)
r=d["researched"]
print(len(r),"total")
from collections import Counter
print(Counter(x["verdict"] for x in r))
# validate sector/category against map
m=json.load(open("/private/tmp/claude-501/-Users-wyethwest/bae54666-b02f-4b55-98ed-4e5a5f866107/scratchpad/sector-map.json"))
bad=[(x["company_name"],x.get("sector"),x.get("category")) for x in r if x.get("sector") not in m or x.get("category") not in m.get(x.get("sector"),[])]
print("BAD SECTOR/CAT:",bad)
print("govtech w/ Suppliers:",[x["company_name"] for x in r if x["verdict"]=="govtech" and x.get("category")=="Suppliers & Services"])
print("missing fields:",[x.get("company_name") for x in r if not all(k in x for k in ("verdict","company_name","website","description","sector","category","source_event"))])
print("desc ends with period:",[x["company_name"] for x in r if x["description"].endswith(".")])
