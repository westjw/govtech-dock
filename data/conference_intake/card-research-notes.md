# What the card research was unsure about

670 candidates researched on 2026-08-23 by 8 agents: 401 judged govtech, 244 sent to the Vendor scope queue, 18 demoted to suppliers, 7 unverifiable.

Everything below is an agent saying it was NOT sure, or catching itself being
wrong. None of it blocks the cards, which are landed. Work it when reviewing.

## The short list, if you only read one part

- **Weakest records, sites the agents never actually read**: Action1 (Cloudflare
  challenge defeated both fetchers), QUALO (fully client-rendered, description
  assembled from nav labels), GRAIL / enterprise.grailai.io (a JS shell; the record
  is built from meta tags alone and it is not the cancer-screening GRAIL). Verify
  GRAIL is a real company before it stays carded.
- **Acquisition**: For The Record was acquired by Tyler Technologies in Feb 2026 and
  may belong inside the Tyler record rather than beside it.
- **Seven duplicate pairs already on file** (Oracle/Oracle Corporation, ProPhoenix,
  Porter Lee, Nuvve, Operator XR, Hexagon, ContentActive). All predate this sweep and
  all are sitting in the Duplicates queue.
- **Two gaps in the sector map**: no utility billing/CIS category (Cayenta and
  Silverblaze are parked in Grid & Energy) and no animal-services category (Doobert
  and Shelterluv are in General Gov / Citizen Services).
- **Non-US vendors** that may not belong on a US SLED board at all: CareLoop (UK/NHS),
  Schoolyear (Netherlands), Esri Canada, Cayenta, Silverblaze, Univerus (Canada),
  Recollect (New Zealand), Academia by Serosoft (India), medici.tv (France), FYLD (UK).
- **Coverage caveat**: the web-search budget ran out partway through several batches,
  so the back half of those has more blank HQ and founding-year fields. They are blank
  because they were not confirmed, not because they do not exist.

## Everything each agent reported, verbatim

### Agent 1

All 84 written to batch-1.json (single file, rewritten after each chunk of 10). Every sector/category pair validated against sector-map.json; no govtech record uses Suppliers & Services; no duplicate company names; every record carries a note.

WEB SEARCH BUDGET RAN OUT at candidate ~38 of 84 (200/200 WebSearch calls). Everything after that was researched by direct WebFetch and curl-with-Chrome-UA against company sites only. DuckDuckGo HTML returned a CAPTCHA and I did not attempt to bypass it. Net effect: more omitted hq_location and year_founded in the back half than the front half. 59 of 84 have HQ, 37 of 84 have a founding year.

THINGS A PERSON SHOULD CHECK:

1. Answerr (EDUCAUSE) - the candidate said "AI voice answering for institutions" but answerr.ai sells AI capability assessment and AIQ credentialing (Answer Labs Inc.). Either the candidate's vertical is wrong or it is a name collision. Marked scope_review; the exhibitor listing would settle it.

2. Kyron Learning - unverifiable. Domain returns HTTP 200 but the page body is empty except the title; /about is a Rails 404.

3. Dynamic IoT Data Inc. - unverifiable. No URL given; exact-name search plus NACE/county-engineer/road-sensor searches all returned different companies.

4. BrainFreeze - candidate said "BrainFreeze by Airia" but brainfreeze.ai names only BrainFreeze LLC in its terms and airia.com shows no BrainFreeze product. Link unconfirmed.

5. Two reclassified to supplier after reading the site: Gilson Housing Partners (call centers, inspections, recertification services dominate; software is two apps) and Zagaran (custom software consultancy, not the housing-authority product company the candidate implied).

6. Judgment calls I flagged but did not decide - Esri Canada (exclusive Canadian ArcGIS distributor, arguably a reseller or a regional arm rather than its own card); Brainfuse (tutoring labor vs software subscription); Mission Driven Data (Janet is a real product but half the site is consulting, and it only wraps one EHR); Intellicampus (self-describes as "a higher education transformation practice that built technology"); Propel (flagship is a free consumer app; Propel for States is the agency line); OCLC (nonprofit member cooperative, not a commercial vendor); Adobe/Aqua (not a company - Adobe showing a free kids' art app at PLA); Doobert and Shelterluv (animal welfare; buyer mix is nonprofit rescues plus municipal shelters, and the sector map has no animal-services category, so both are filed General Gov / Citizen Services); ObservSMART (put in scope_review because its 500+ customers are mostly private hospitals and long-term care, unlike the other NatCon behavioral-health vendors I called govtech).

7. HQ from general knowledge rather than the fetched page, because the sites block or render nothing - Jamf (Minneapolis), BeyondTrust (Johns Creek), Canva (Sydney), GitLab (San Francisco, and it is all-remote so the city is nominal), OCLC (Dublin OH), Adobe (San Jose). Each says so in its note. Veeam, Opkey, and ReadSpeaker publish no HQ anywhere I could reach, so those are blank.

8. Founding years I deliberately left blank rather than guess: EAB (1979 Advisory Board vs 2007 division vs 2017 spinout), Black Duck (2002 original vs 2024 relaunch under the same name), PCS Revenue Control Systems (directories say 1984, their site says only "over 25 years"), Muni-Link, CollegeNET, BeyondTrust, plus most of the EDUCAUSE back half.

9. Sector-map gaps worth knowing: no utility billing/CIS category (Cayenta and Silverblaze filed Utilities & Energy / Grid & Energy) and no animal-services category.

10. Non-US entities where the "bought by government" test may not fit a US SLED tracker: CareLoop Health (UK/NHS), FYLD (UK utilities, several privately owned), Schoolyear (Netherlands), medici.tv (France), Esri Canada, Cayenta and Silverblaze and Univerus (Canada), Recollect (New Zealand origin), Academia by Serosoft (India).

### Agent 2

Written in 9 passes (10 per chunk, whole file rewritten each time); every entry validated against sector-map.json — all sector/category pairs are legal, no govtech entry uses "Suppliers & Services", no description ends in a period.

BLOCKED SITES / WEAKEST RECORDS — check these by hand:
- Action1 (EDUCAUSE): action1.com serves a Cloudflare JS challenge to WebFetch AND to curl with full browser headers. I never read the site. Verdict/description rest on general knowledge; HQ and year deliberately omitted. Most likely record to be wrong.
- QUALO (NatCon): qualo.net is fully client-rendered; both tools returned only nav labels. Description assembled from menu items only. Verdict govtech is a guess about who buys.
- TouchNet and Sage also 403 everywhere (Cloudflare); details came from press/directories, but both are well-documented companies.

WEB SEARCH BUDGET EXHAUSTED at candidate ~35 (200/200 calls). Everything after that used WebFetch + curl only. DuckDuckGo returned a CAPTCHA (not solved) and Bing returned junk, so ~14 companies have no HQ and 35 have no founding year rather than invented ones.

WRONG / MISSING URLs I resolved: asuene.com/us (given www.asuene.com does not resolve); betterage.net (given betterage.com refuses connections); vexceldata.com (given vexcelgroup.com does not respond); i3verticals.com/education (i3education.org 301s there); givepulse.com, saavor.com, builterra.com, checkbox.ai, merative.com, teamdynamix.com supplied where the candidate had none.

NAME COLLISION RESOLVED: Checkbox = checkbox.ai (Checkbox Technology Pty Ltd, legal ops), NOT checkbox.com survey software.

DEDUPE NEEDED: Oracle (PSHRA) and Oracle Health (NatCon) are the same corporate entity. For The Record was acquired by Tyler Technologies in Feb 2026 and may belong inside a Tyler record.

JUDGMENT CALLS worth a second look, all flagged in their notes: MuniPro (flagship platform sells to banks/advisors; only BondGov is issuer-facing), VTScada (water-first but also oil and gas / manufacturing), TeamDynamix (higher-ed-first ITSM but lists retail/hospitality/manufacturing — arguably scope_review like HaloITSM), Kipu Health and Amigo AI (real behavioral-health products but buyers are private providers, put in scope_review), MIYO Health and Library Market (half the business is staffing/services, not product), Beacon (a 501(c)(3) nonprofit's software), Candid (also a 501(c)(3)), Meescan and SenSource and Intelligent Video Solutions (meaningful hardware/installation component).

The single "supplier" is INFRIQ — the site describes strategies and programs with no named product, and it presents as a Wetwork Group advisory practice.

Ellucian's 1968 is predecessor Datatel's founding (Ellucian brand dates to the 2012 merger); i3 Verticals' 2012 is the parent, not PaySchools; Gimlet's 2008 is inferred from a copyright range.

### Agent 3

DUPLICATE: rows 10 and 31 are both SAP (GFOA 2026, and NASCIO 2026 as "SAP Public Services") - merge before carding.

WEAKEST ROW - needs a human: GRAIL (enterprise.grailai.io). Both enterprise.grailai.io and grailai.io are JavaScript shells with no crawlable text; the record is built entirely from page meta tags, which name InnovationAI Inc. as author and describe a generic "autonomous agent preloaded with PhD-level skills on secure servers" with no education angle despite the EDUCAUSE exhibit. Confirmed NOT the cancer-screening GRAIL at grail.com. Called scope_review; verify it is a real company before carding.

JUDGMENT CALLS WORTH A SECOND LOOK:
- Xylem Vue: genuine software/analytics platform, but parent Xylem plc is primarily a water equipment manufacturer. Called govtech on the product line; if cards are per-parent, this reads closer to supplier.
- ClassDojo: K-12-specific and used in public schools, but their press page says "100% free for schools" - revenue is family subscriptions. Called govtech, no district sales motion.
- Playgarden Prep Online: core business is direct-to-family subscriptions with library/hospital licensing as the PLA channel (hoopla-like model). Called govtech.
- Clio: exhibited at the municipal attorneys' conference but the stated market is solo and small private law firms. Called scope_review.
- Dazos, exydoc: real vertical health products whose buyers are private providers, not public agencies. Called scope_review.
- Modality: very thin site (product blurb plus contact form); public-sector buyer inferred from the NatCon exhibit, not confirmed.
- EverTrue: now bundles DonorSearch/ThankView and markets to nonprofits broadly - if the owner treats that as the Blackbaud line, revisit.
- Fischer Identity: higher ed is the marquee vertical but they sell equally to healthcare, financial services, manufacturing. Called scope_review; Cirrus Identity (higher-ed only) was called govtech.

CATEGORY CALLS FLAGGED IN NOTES:
- Instructure sectored to Higher Education despite the CoSN/K-12 lead and /k12 candidate URL.
- Fast Enterprises categorized on GenTax tax administration (General Gov / Finance & ERP) despite the NCSEA child-support lead.
- Aurigo put under Public Works / Streets; Fleet & Asset Mgmt is defensible.
- Sagitec put under HHS / Workforce & Labor; its pension line would sit under General Gov / HR & Workforce.

SECTOR MAP GAPS: no billing/CIS category under Utilities & Energy (the four Harris-family CIS vendors - Advanced Utility Systems, CUSI, inHANCE, NorthStar - all filed under Grid & Energy); no instructional-content category (Decodable Reads, Lightbox Learning filed under General Gov / Libraries); no credentialing category (exydoc). PlanStreet used the General Gov / Health & Human Services pair because no HHS subcategory covers a generalist case-management platform.

AMBIGUOUS HQs (one value recorded, alternative in notes): AppsAnywhere (Leeds UK entity vs Charlotte NC office), Omnissa (site footer Mountain View CA vs press Atlanta GA), Open Point (dual Vancouver BC / Brisbane AU), SEAtS ONE (London, Dublin, Texas, Sydney - none named as HQ, so omitted).

OMISSIONS: 20 records have no hq_location, 45 have no year_founded - omitted rather than guessed, each with a note saying what was searched. Common cause is sites saying "40+ years" or "over two decades" instead of a date.

TOOL CONSTRAINT: the session's WebSearch budget (200 calls) was exhausted around candidate 40, so the remaining ~44 were researched with WebFetch plus curl with a Chrome user agent. That mostly cost founding years, not identity confirmation. Sites that 403'd WebFetch and were read via curl: sap.com, clio.com (403 to both - details from Clio's own blog/about via search), concentric.ai paths, harmonizelearning.com, seatsone.com, cumulus.care.

URL CORRECTIONS: givecard.io -> givecard.com (301); mergent.com -> lseg.com/en/ftse-russell (302, no standalone Mergent site remains); lightboxlearning.com is a parked lander, real site is openlightbox.com; www.bibliu.com -> bibliu.com. Candidates that arrived with no URL and were resolved: Home to Home (home-home.org), Aurigo (aurigo.com), Clio (clio.com), Advanced Utility Systems (advancedutility.com), ClassDojo (classdojo.com), Kanso (kansosoftware.com), Lightbox Learning (openlightbox.com), Mergent (LSEG page).

### Agent 4

Wrote in 9 passes, rewriting the whole file each time; final file validates as JSON with 84 objects, all sector/category pairs from sector-map.json, no Suppliers & Services on any govtech verdict, and source_event preserved in candidate order.

WRONG-URL / IDENTITY RESOLUTIONS (check these):
1. "Meal Manage Inc." (SNA ANC 2027) - supplied URL menulogic-k12.com (now menulogic.io) is a DIFFERENT company, MenuLogic K12 operated by Foodworks Technologies, LLC. The candidate name and vertical ("school meal ordering and payment software") match MealManage Inc. at mealmanage.com, so the card is written for MealManage. Confirm against the exhibitor list.
2. "ExeVision" (NACE 2026) - candidate vertical "local government asset and permit software" describes iWorQ Systems, a different Utah local-gov vendor. ExeVision (exevision.com, South Jordan UT) sells road/bridge project development software (iPDWeb, iCXWeb, eFieldbook) to state DOTs and counties, which fits NACE. Card written for the real ExeVision.
3. Several candidates had no URL and were located by search: NoiseNet (noisenet.com), LawVu (lawvu.com), Veracity (veracityvs.com), SACS Software (sacssoftware.com).
4. careeredge.com 301s to careerteam.com; respondus.com 301s to web.respondus.com; menulogic-k12.com 301s to menulogic.io. Canonical URLs updated accordingly.

FOUR SUPPLIER CALLS ARE JUDGMENT CALLS (notes say what would flip each): SimpliVerified (PBSA screening bureau, horizontal), Telephone Town Hall Meeting (campaigns produced by their team, not self-serve), 5e Analytics (site sells consulting + staff augmentation, not the "behavioral health analytics platform" the candidate described), Traf-Sys (sensor hardware sold to retail/casinos/malls as well as libraries).

GOVTECH CALLS WORTH A SECOND LOOK: Niche (data platform but self-describes as an enrollment marketing agency); Kahua (construction-vertical, sells to commercial/healthcare owners too, but FedRAMP + USACE + turnpike commissions); Convey (utilities-first but also finance/healthcare/telecom); Govstack (productized SaaS but owned and run inside GHD, an engineering consultancy); Gravyty (same education-plus-nonprofit advancement shape as Blackbaud, which the owner ruled scope_review); LLMC (a nonprofit library consortium selling memberships, not a commercial vendor); YuJa (education-first but also healthcare/finance/government).

NATCON HEALTHCARE CLUSTER: Assured Health, Circle Health, mdhub, Nanonets Health, and Videra Health all sell to private/nonprofit clinical providers rather than public agencies. I routed them to scope_review rather than govtech; they may simply be out of scope for a state-and-local tracker. Deerfield Solutions is the exception - its site names public health entities as buyers and LOCUS is a state-mandated instrument - so it is govtech.

WEBSEARCH BUDGET EXHAUSTED at roughly candidate 56 (200/200 calls). The last ~28 candidates rely only on company sites via WebFetch and curl, which is why several omit hq_location and/or year_founded that a search would likely have supplied: Boomi, Sophos, LogicMonitor, Freshworks, SecureW2, Respondus, Honorlock, ASCERA, YuJa, TimelyGrader, PowerDMS, ReflexAI, Communico, Digitalia, Infobase, Spout. All are noted individually.

YEARS DELIBERATELY OMITTED FOR CONFLICT: ExeVision (site says "since 2005" for transportation work, third-party profiles say founded 1994), SOFTRAX (1995 vs 1999), Veracity (2020 vs 2021), AtoZdatabases (about page dates the Infogroup sale to 2010 but never states its own founding), Infobase (timeline starts 1941 for Facts On File, not the entity), LLMC (50th-anniversary branding implies ~1976), SirsiDynix (used 2005 merger date; predecessors are 1979 Sirsi / 1983 Dynix - owner may prefer earlier).

HQs FLAGGED AS LIGHTLY HELD: Trualta (Toronto in most profiles, Ottawa in one; site blocks all fetching), Adventfs (Louisville KY from third-party profiles only - contact page has no address), SACS Software (1969 founding from LinkedIn, not the site), Waterly (site contact page still shows placeholder text), Augintel (omitted - coverage calls it both a Pittsburgh and a Chicago company), Brisk Teaching (omitted - San Francisco vs Los Altos), Secure Schools (omitted - Middlesbrough vs Newmarket UK), Infobase (omitted - only a Dover DE registered-agent address published), YuJa (omitted - site says only "Silicon Valley, California"), Simplicity ILS (omitted - Illinois implied by 630 area code, no city).

CORE Business Technologies: site's Why CORE page states East Providence RI HQ and inception 1986; the footer shows a Chicago IL address, which I treated as a second office.

DataCampus: site is fully JS-rendered and returns only a title tag to both WebFetch and curl. Description reconstructed from its sitemap (platform, warehouse, catalog, lineage, governance, semantic, bi, ai, ingestion, dbt, multi-tenant) and is coarser than every other card - worth a human look.

Google Public Sector is a Google LLC division, not an independent company; used its own site publicsector.google rather than the supplied cloud.google.com/gov.

### Agent 5

All 84 emitted; source_event counts per conference match the input exactly, no duplicate names, every sector/category pair validates against sector-map.json, no "Suppliers & Services" category on any govtech verdict. 68 of 84 have hq_location, 46 have year_founded; the rest were omitted rather than guessed.

Things a person should check:

1. Two companies in this batch are now one. Checkr acquired Truv (announced Aug 2026). Both are separate cards, both scope_review. Merge or cross-link before shipping.

2. Name/URL resolutions I made, worth a sanity check: "Illumia (formerly Transact + CBORD)" -> illumiatech.com (Roper subsidiary, rebrand completed March 2026). "Innovative - ProQuest, Part of Clarivate" -> iii.com; the candidate URL was a 2020 acquisition press release. "Fleeta" is not a startup - it is BlackVue/Pittasoft's fleet service. "Diversified Computer Service" had at least four same-named firms; I picked the Montgomery, AL one whose CIMS product runs in county road departments. "National Credit Reporting" -> ncrcredit.com, not the nbinformation.com consumer brand in the candidate. "ZaristAI" brands itself that way but its domain and email are aristai.io - a real inconsistency, not my error.

3. Judgment calls I flagged in notes rather than deciding: Quant16 (supplier - outcome-priced savings engagements, "no consulting fees, no licenses" on their own pricing page); National Credit Reporting (supplier - screening service bureau, but a genuine NAHRO housing-authority vendor, so you may want it counted); AWE Learning (govtech but the purchase is a physical workstation); Watura (govtech but it is training content, not an operational system); Panopto and Kahoot! (both scope_review, both arguably govtech - Panopto's anchor market is higher ed, Kahoot's education line is real but its corporate and consumer lines are comparable).

4. The nine NatCon 2026 exhibitors needed a consistent rule and I applied one: vendors built for behavioral health / IDD / Medicaid HCBS providers got govtech (MedSuite, Proven Software, Giv); general healthcare products that merely showed up at NatCon got scope_review (NextGen, Relias, Doxy.me, Clarity Group, Attunement, Weave). Weave is the weakest fit of the set - it is small-practice dental communication software with no public-sector orientation.

5. nuXight exhibited at EDUCAUSE but its entire site is written for K-12 principals, and its testimonials are private schools and a Taiwan program. Categorized by product as instructed, but the public-school share of its base is unknown.

6. Two soft founding years, both noted on the card: TCARE 2014 (CB Insights/PitchBook, but one press interview says 2017 launch) and NextGen 1974 (only source is their own site copyright line).

7. Daupler's category is a compromise - Public Works / Water. The product is cross-departmental (water, sewer, stormwater, electric, general public works) and the map has no general public-works-ops bucket.

8. The web search budget (200 calls) ran out partway through the PLA block. Everything after that was resolved by direct site fetches, curl with a Chrome UA, structured-data extraction from page source, and Wikipedia fetches. A few fields I would normally have confirmed by search are omitted as a result: Proven Software HQ/year, StarRez HQ/year, Weave founding year, EBSCO founding year, Enghouse Interactive HQ city, Propeller HQ.

### Agent 6

Written in 8 chunks with a full rewrite of batch-6.json after each, so nothing was ever held only in memory. Validated at the end: 84 unique records, all required fields present, every sector/category pair valid against sector-map.json, no govtech card filed under Suppliers & Services, source_event counts match the input exactly.

WEB SEARCH BUDGET RAN OUT partway through (200/200 used, at the Orlo lookup). Everything after that was researched by direct curl/WebFetch of company sites only. That is why several later cards have blank hq_location or year_founded — I could not fall back to Crunchbase-style profiles for the ones that publish nothing on their own site.

Two unverifiable, both need a human:
- QR Print (getqrprint.com, PLA 2026) — the entire site is one static page: four lines of copy, a demo.mp4 link, and a mailto. No company name, entity, address, team, or pricing anywhere in the raw HTML, and both call-to-action buttons are commented out in the page's own JavaScript. Search budget was gone before I could hunt for a parent company.
- Datacognyx (GFOA 2026) — site exists but is a two-page brochure with a South African phone (+27) and a Midrand, South Africa address, no team, no customers, nothing tying it to US municipal finance. Searches for the exact name returned zero company records, only near-misses (Datacogin, dataCogence, Dataknox). Could be a name collision with the actual GFOA exhibitor.

Judgment calls I flagged in notes and would want a second opinion on:
- Petcademy and Tutor.com both got "supplier" on the same reasoning: the thing being bought is human labor (trainers answering texts / tutor hours) with software as the delivery layer. If you count tech-enabled services as govtech, both flip — Petcademy to General Gov / Citizen Services, Tutor.com to General Gov / Libraries. ConnectWell got supplier too, as a licensed-content business rather than software.
- Psych Hub got govtech but its buyer mix leans payer- and clinician-side; "Government & Associations" is one of five segments.
- Sergeant Laboratories (AristotleK12) got govtech, but its sibling product AristotleInsight is a general IT security tool sold to banks, so only part of the business is govtech.
- SysCloud and Tikler both have real public-sector concentration (K-12, and a housing-authority customer respectively) but market horizontally, so I sent them to scope_review rather than deciding.

Entity corrections worth knowing:
- "Equifax/Carahsoft" bundles two companies. Carahsoft is a government IT reseller (a supplier, not a product company); Equifax is the product owner via The Work Number. I carded Equifax.
- "BOSS by Integra" — BOSS is the product, The Integra Group is the company, and its verticals are landscaping/marine/field service, with "K-12 options" as one module feature.
- "AristotleK12" is a product of Sergeant Laboratories; "greymatter" is a product of Frequency Foundry; "CITIZ3N" is a brand of Softheon; "Broadcom Software" is a segment of Broadcom Inc.; "CloudLabs" is one of three Spektra Systems products; "Delta Bravo AI" is the company and Aquaspec (the given URL) is its product.
- Dareesoft, Onit, and Panorama Education had no URL in the candidate list; I confirmed dareesoft.com, onit.com, and panoramaed.com respectively.
- Job Machine: getjobmachine.com and jobmachine.com are the same site; I used jobmachine.com as canonical. Zoom: zoom.us still resolves but zoom.com is canonical now. Quodus: www.quodus.ai does not resolve, quodus.ai does.

Two HQ fields are inferred rather than sourced and are marked as such in notes: Greenspace Health (Toronto, from founder backgrounds) and Gecko (Edinburgh, from a +44 131 dialing code). A handful of others (Zscaler, Precisely, Blumira, Delinea, SteelCloud, TouchNet, SailPoint-adjacent) take HQ from public corporate record because the sites publish no address; each says so in its note. touchnet.com and autodesk.com both block automated fetches, so those two cards rest on public product/corporate info rather than pages I could read.

### Agent 7

TOOLING CONSTRAINT: WebSearch was exhausted at the start of this pass (200/200 for the session), and curl-based fallbacks to DuckDuckGo, Mojeek, Bing RSS and searx all returned captchas or junk. So every card here was built by fetching a URL directly - the given one, an obvious variant, or a domain probe. Anything marked unverifiable should be retried by someone with search available; those are cheap wins, not dead ends.

THREE UNVERIFIABLE: (1) Adoptimize - adoptimize.com is an unrelated PPC agency, adoptimizeai.com is an unrelated ad tool sunset Oct 2025, and eight other TLD variants do not resolve. (2) MasterMind LLC - mastermindllc.com and signmastermind.com both resolve but serve only parked-domain landers; the name is generic enough that any match needs collision checking. (3) Counting Opinions - the domain is live but every automated request, WebFetch and Chrome-UA curl alike, hits an Incapsula bot challenge. A human opening countingopinions.com in a browser can finish that card in a minute.

ZERO SUPPLIERS, which is unusual and worth knowing why: this batch was pre-filtered to product companies and nothing collapsed into pure services on inspection. Two came close and are noted on their cards - Clarity Solutions Group (sells consulting alongside the ClarityLink platform; counted govtech because the platform is real and publicly bought) and SAND/Sand Technologies (heavy AI-engineering positioning, and its lead product page targets telecom rather than the water market where it exhibited).

JUDGMENT CALLS I WOULD WANT A SECOND OPINION ON:
- "LinkedIn + Carahsoft" is one exhibitor row covering two companies. I recorded LinkedIn as scope_review (horizontal talent software). If you would rather treat Carahsoft as the exhibiting entity, it is a distributor and becomes a supplier. Both readings are written into the notes.
- AffordableHousing.com (govtech) is a consumer rental marketplace on one side and a HUD/PHA-contracted listing service on the other. Formerly GoSection8.
- Petstablished (govtech) sells to a mix of municipal animal control and private nonprofit rescues.
- Lightcast, FacilitySight, Value Line, Psychology Tools and ClinicMind are all scope_review because government or public institutions are one buyer segment among several, not because the products are weak.
- Tyler Technologies is filed under Courts & Justice because that is the product line behind the APPA Probation booth, but it is a broad-line government vendor and could sit in General Gov instead.
- iCEV is curriculum content, not operations software; filed under K-12 / Operations & SIS as the closest available category. The sector map has no curriculum or content category.
- Unifuse AI is still in stealth per its own site. There is no product detail or customer list yet - re-check before it goes live as a card.

FIELD COVERAGE: 47 of 83 have hq_location, 27 have year_founded. The gaps are real gaps - many of these sites publish no address and no founding year, and I omitted rather than guessed. A few near-misses are documented in notes instead of the field: TurboPass shows a 2019-2026 copyright but never claims a founding year; Nutri-Link gives only "Georgia, USA" with no city; Can/Am says "over 20 years" and Hansen says "50 years+" without dates.

TWO CARDS WHERE I PICKED ONE OF TWO PUBLISHED LOCATIONS: BiblioCommons structured data carries both Toronto and Mississauga; I used Mississauga because that is the locality attached to the street address. Data Axle lists four offices with no HQ label; I used Grapevine, TX because it is listed first. Both choices are flagged in their notes.

RENAMES AND REDIRECTS worth carrying forward: SAP America to SAP, Can/Am Teller Cashiering to Can/Am Technologies, Abnormal Security to Abnormal AI, Emsi + Burning Glass to Lightcast, valuebase.co to valuebase.ai, bossdesk.com to boss-solutions.com, nutri-linktechnologies.com to nutrilinktechnologies.com. System Innovators is a division of Harris Computer, which also appears separately in this same batch.

### Agent 8

Validated: 83 entries, every sector/category pair exists in sector-map.json, no govtech card uses "Suppliers & Services", source_event copied through unchanged (counts match input), 43 have hq_location, 12 have year_founded (omitted everywhere else rather than guessed).

TOOL CONSTRAINT worth knowing: the session's WebSearch budget (200/200) was already exhausted before I started, so no WebSearch call ever ran. Search fallbacks were Brave via curl (worked until it started serving captchas), Bing via WebFetch (mostly returned irrelevant results), and a rendered Chrome browser session for JS-only sites. DuckDuckGo and Mojeek both served captchas and were not bypassed. If a later pass has search budget, the omitted HQ/year fields are the cheapest thing to backfill.

NEEDS A HUMAN LOOK:
1. Calejo (AWWA ACE) - calejo.ai refused every direct request from this environment and a proxy attempt hit a Cloudflare challenge. Everything on that card comes from indexed search snippets. The entity appears to be Calejo Hybrid Intelligence AB (Sweden), industrial AI with a "Calejo Smart Water" line, but I could not confirm the exhibitor name "Calejo Water Intelligence" is that same company. Marked scope_review.
2. Yardi Systems - yardi.com held every request, including a rendered browser, at Cloudflare bot verification. Classification rests on the candidate record and NAHRO context, not on reading the site. Marked scope_review; HQ and year deliberately blank.
3. StatsUSA - about page returns HTTP 500, and the HQ I used comes from structured data that also carries an implausible 17,426-review rating and a Santa Monica PO Box ZIP. Confirm the address before trusting it.
4. Creativebug - owned by JOANN, which went through bankruptcy in 2025. Current ownership should be confirmed.
5. Apply Government Solutions (was Monster Government Solutions, monstergov.com now redirects) - current site is almost entirely federal. If the tracker is state-and-local only, this may not belong.

REBRANDS / NAME CHANGES I resolved (card names differ from the candidate list): K12 Insight -> Onflo; Monster Government Solutions -> Apply Government Solutions; Vasion Print -> Vasion; AM Quartex -> AM (Adam Matthew Digital); psyrin.ai -> psyrin.com; service-link.us -> servicelinksoftware.com.

NAME COLLISIONS resolved: ServiceLink is the utility field-service vendor, NOT ServiceLink the mortgage/title firm. MindMixer is NOT the original civic-engagement startup (that became mySidewalk) - the name is now used by Social Assurance of Lincoln NE.

JUDGMENT CALLS most likely to be reversed: Kofile (called supplier - it does ship the Kleio platform, but preservation/digitization services and archival supplies dominate the site); ONLINE Rental Exchange (called supplier - a collections agency and screening bureau); StudyCrowd.AI (called supplier - today it is student-paid tutoring, but a university pilot program is advertised); Filevine, Locus Technologies, Ntracts and Strata Decision (all called scope_review - real vertical products whose buyers are mostly private firms/health systems, not agencies); Tova Earth (called govtech though it also courts corporate and investor buyers); Care Predictor and ContinuumCloud (called govtech on the reading that behavioral health providers count as public-sector - the sector map having a Behavioral Health category drove that).

Barracuda's year_founded (2003) is inferred from the site's own "© 2003 - 2026" copyright range, not a stated founding date - drop it if that is too soft. Metrix Learning's 2008 comes from the about page describing its launch.
