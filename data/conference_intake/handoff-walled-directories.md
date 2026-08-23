# Exhibitor lists I cannot read - your half of the sweep

Open the link, copy the exhibitor names (booth list or floor-plan
sidebar), and hand them back in any format: name plus website if shown
is plenty. I classify, dedupe and land them. For PDFs the link IS the
list.

## Interactive floor-plan apps (need a person with a browser)

### AASA National Conference on Education (Superintendents)
- Link: https://smithbucklin.expocad.com/Events/26aasa/index.html
- Platform: ExpoCad FX (smithbucklin.expocad.com)
- Notes: 2026 NCE was Feb 12-14 2026, Nashville. ExpoCad floor plan/exhibitor search is a JS app shell with no names in HTML. The site's Exhibit Hall page (https://nce.aasa.org/nce-exhibit-hall/) now points at the 2027 edition (https://smithbucklin.expocad.com/Events/27aasa/index.html) and does show sponsor names in HTML (Wayfinder, American Reading Company, BrainFuse, CentralReach, Finalsite, Xello) but no full exhibitor list.

### ACA Congress (Corrections (state))
- Link: https://s36.a2zinc.net/clients/ACA/acacongress2026/Public/Exhibitors.aspx
- Platform: a2z (Personify, s36.a2zinc.net)
- Notes: 156th Congress of Correction, July 30-Aug 2, 2026 (Pittsburgh). Direct fetch of the Exhibitors.aspx list page returns an 'Oops, something went wrong' shell, but the directory is live: individual eBooth pages render server-side (verified: Tidal Wave Telecom, booth 1027). Open the list URL in a real browser. ACA Winter 2026 (Long Beach) has a parallel a2z site under /clients/ACA/ACA/.

### AJA Annual (Jails (county))
- Link: https://aja2027.mapyourshow.com/8_0/index.cfm
- Platform: MapYourShow
- Notes: The 2026 edition (45th Annual Conference & Jail Expo, Milwaukee, May 16-20, 2026) directory at aja2026.mapyourshow.com now 302-redirects to americanjail.org - taken down after the event, and americanjail.org/exhibitors-2026 404s. The next edition's MapYourShow site (2027 Conference & Jail Expo, Spokane, May 22-26, 2027) is live but is a JS shell with no names in static HTML.

### APA National Planning Conference (Planning & zoning)
- Link: https://maps.goeshow.com/planning/national/2026/exhibitor_list.cfm
- Platform: eShow (maps.goeshow.com)
- Notes: NPC26 was April 25-28, 2026 in Detroit. planning.org/conference/sponsors/ has rolled over to NPC27 (sales 'open in coming weeks', no names) but links the NPC26 exhibitor list on the eShow platform; fetching it returns only an 'eShow' JS shell with no names - open in a real browser.

### APCO Annual (911 / dispatch)
- Link: https://apco2026.eventscribe.net/exhibitors/floorplan/floorplan.asp
- Platform: CadmiumCD eventScribe / Conference Harvester
- Roughly 250 exhibitors
- Notes: APCO 2026, San Antonio, Aug 2-5 2026 (just held). Official 'Exhibitor List / Floor Plan' link from https://apco2026.org/expo/ goes to the eventScribe floor plan (same data at https://www.conferenceharvester.com/floorplan/v2/index.asp?EventKey=JBBFWNZS); both are JS-rendered with no names in HTML. However, the /expo/ page itself is readable and lists sponsors by name (FirstNet, Axon, L3Harris, Motorola, RapidSOS, Comcast, General Motors, Eventide) — a person opening the floor plan will get the full 250+ exhibitor list.

### ASBO International (Business officials)
- Link: https://s6.goeshow.com/asbo/annual/2026/exhibitor_hall_map.cfm
- Platform: eShow / GoExpo (goeshow.com)
- Notes: School business officials association (asbointl.org), Annual Conference & Expo Oct 14-17 2026, Pittsburgh. Exhibit Hall Floorplan page loads exhibitor data via JS; no names in fetched HTML. Also tried network.asbointl.org exhibitor pages for 2025 and 2026 (no names) and the goeshow exhibit_sales page (booth-purchase shell only). No static/public exhibitor list found for either edition.

### CTAA EXPO (Rural transit)
- Link: https://homebase.map-dynamics.com/ctaaexpo2026/floorplan
- Platform: Map Dynamics
- Notes: EXPO 2026 (Omaha, presented by Forest River). Floorplan link found on https://ctaa.org/trade-show-2026/ ('Floorplan' button). Map Dynamics page is a jQuery app shell; exhibitor data loads via AJAX with no names in static HTML and no discoverable public JSON endpoint. Note: ctaa.org returns 403 to generic fetchers but serves pages to a browser user agent. Sponsors page https://ctaa.org/sponsors-2026/ shows sponsor tiers as logo images (only alt text found: UZURV). Prospectus PDF: https://ctaa.org/wp-content/uploads/2026/04/CTAA-EXPO-2026-Promotional-Opportunities-Prospectus_apr_8.pdf

### IAEM Annual (Emergency management)
- Link: https://iaem2026.mapyourshow.com/8_0/exhview/index.cfm
- Platform: MapYourShow
- Notes: IAEM 74th Annual Conference & EMEX 2026, Long Beach CA, Nov 2026. The 2026 MapYourShow floor plan is live but JS-rendered (no names in HTML); the exhibitor alphalist page (iaem2026.mapyourshow.com/8_0/explore/exhibitor-alphalist.cfm) currently 302-redirects to iaem.org/usconf, suggesting the named exhibitor list is not published yet this far ahead of the show. The 2025 edition's directory (iaem2025.mapyourshow.com) has been taken down (also redirects). Exhibit info hub: https://www.iaem.org/usconf/exhibit/Quick-Facts

### IAFC Fire-Rescue International (Fire)
- Link: https://fri26.mapyourshow.com/8_0/explore/exhibitor-alphalist.cfm
- Platform: MapYourShow
- Notes: FRI 2026 exhibitor directory is live on MapYourShow (alphabetical list + floor plan at fri26.mapyourshow.com), but the fetched HTML is a JS app shell with no exhibitor names. Linked from https://www.iafc.org/events/fri/fri26. FRI 2025's list was on fri2025.eventscribe.net.

### ICC Annual Conference (Fire code / building safety)
- Link: https://www.conferenceharvester.com/floorplan/v2/index.asp?EventKey=BJKYXLVZ
- Platform: ConferenceHarvester (CadmiumCD)
- Roughly 251 exhibitors
- Notes: 2026 Annual Conference & Expo, Nashville Oct 18-21, 2026. Interactive exhibitor floor plan renders via JS only; ~251 exhibitors per third-party listing (Vendelux). A readable SPONSOR list exists at https://www.iccsafe.org/events/conference/sponsors-ac/ (~13 names actually extracted: 4LEAF Inc, Acta Solutions, AHRI, AGA, CISPI, Computronix, Denlar Hoods, IPEX, Lowe's, Mitchell Humphrey, OpenGov, SAFEbuilt, TENMAT). 2025 Cleveland edition floor plan: same platform, EventKey=GFOCKUAP.

### ITS America (Traffic engineering)
- Link: https://www.itsamericaevents.com/expo/en-us/exhibitor-list.html
- Platform: RX Global (Reed Exhibitions) directory app
- Roughly 174 exhibitors
- Notes: Labeled '2026 Exhibitor Directory' covering the 2026 Conference & Expo (June 9-12 2026, Detroit); site chrome now brands the 2027 Salt Lake City edition but the directory data is 2026's. WebFetch gets only the RX app shell - names load client-side (verified in a real browser: counter shows '174 Exhibitors'; e.g. AT&T Connected Solutions, GHD, Arcadis, Michelin Mobility Intelligence, S.M.S Smart Microwave Sensors). A person opening the URL will see the full list with search/filter.

### NACCHO 360 (Local public health)
- Link: https://naccho2026.mapyourshow.com/8_0/explore/exhibitor-gallery.cfm
- Platform: MapYourShow
- Notes: Official 2026 NACCHO360 (July 14-17, Louisville KY) exhibitor directory on MapYourShow; fetched HTML renders the search shell but no exhibitor names ('No exhibitors could be found' placeholder). Fallback: naccho360.org hosts a downloadable '2024_and_2025_NACCHO360_Exhibitor_List.pdf' on its Past NACCHO360 Exhibitors page (naccho360.org/sponsorships-and-exhibits/exhibitor-resource-center/past-naccho360-exhibitors).

### NAFA I&E (Fleet)
- Link: https://www.nafainstitute.org/exhibitors-sponsors/
- Platform: custom HTML (WordPress); full directory was MapYourShow
- Roughly 23 exhibitors
- **Partly done**: 23 sponsors already landed as NAFA I&E 2026; the rest is what is missing.
- Notes: 2026 edition was April 13-15, Cleveland. The readable page is the sponsor list (~23 names, extracted verbatim). The full 230+ exhibitor directory lived at nafa26.mapyourshow.com but that MapYourShow site now 302-redirects to nafainstitute.org post-event, so the sponsor page is the only public list left. Site is already booking 2027 (April 5-7, Pittsburgh).

### NAPT Summit (Pupil transportation)
- Link: https://homebase.map-dynamics.com/napt2026/floorplan
- Platform: Map Dynamics
- Notes: NAPT ACTS 2026 (49th Annual, Louisville KY, Oct 3-7 2026). napt.org/conference has no exhibitor list, only Cvent registration (also a JS shell). Map Dynamics hosts the trade show floorplan/exhibitor viewer (napt2026 instance confirmed live with Floorplan + Exhibitors tabs), but all names render client-side; the napt2025 (Grand Rapids) instance at homebase.map-dynamics.com/napt2025/floorplan is equally JS-walled. A person opening the URL should see the exhibitor tab.

### WEFTEC (Wastewater)
- Link: https://weftec26.mapyourshow.com/8_0/exhview/index.cfm
- Platform: MapYourShow
- Roughly 950 exhibitors
- Notes: WEFTEC 2026, Sept 26-30, New Orleans. Fetched HTML is a MapYourShow app shell (only UI strings like 'Selected Exhibitor'), no company names server-side. WEF claims 950+ exhibitors; exhibitor waitlist already closed. A person should open weftec26.mapyourshow.com.

## PDF programs (openable, just not HTML)

### IIMC Annual (Municipal clerks)
- Link: https://www.iimc.com/DocumentCenter/View/9952/Reno-Program-2026
- Platform: CivicPlus DocumentCenter PDF
- Roughly 43 exhibitors
- Notes: 80th Annual, May 17-21, 2026, Reno NV. Conference program PDF (4.8MB) contains a '2026 Exhibitors' page with ~43 names incl. American Legal Publishing, CivicPlus, ClerkBase, Dominion Voting Systems, Granicus, JustFOIA, Laserfiche, MCCi, OpenGov, Televic - extracted via pdftotext, so a person or PDF pipeline can read it. No HTML exhibitor directory exists; iimcfoundation.com/170/Exhibitors is a prospectus page with no names.

### SWANA WASTECON (Solid waste)
- Link: https://swana.org/docs/default-source/default-document-library/exhibitor-list_11-4-2025.pdf
- Platform: PDF on swana.org; 2026 floor plan on ExpoFP
- Roughly 211 exhibitors
- Notes: WASTECON was rebranded to RCon starting 2025. PDF verified by download: '2025 RCon Exhibitors as of 11.4.2025' with booth numbers (Great Lakes Fusion, AGRU America, Veolia, Machinex, RouteSmart, WSP USA, ...); SWANA says the sold-out 2025 hall had 211 exhibitors. Upcoming RCon 2026 (St. Louis, Sept 29-Oct 1) exposes only a JS floor plan at rcon2026.expofp.com (empty HTML shell) plus swana.org/events/rcon2026/exhibit-hall.

## No public list found (may need your member login, or skip)

### AAMVA Annual (DMV)
- Roughly 90 exhibitors
- Notes: No public exhibitor directory for AIC 2025 (Phoenix) or 2026 (Providence, Sep 29-Oct 1). Tried: AAMVA 2026 and 2025 exhibit-sponsor pages (prospectus/pricing only; 2026 hall sold out with waiting list, typical ~90 exhibitors), searches for exhibitor list/booth numbers/program PDF, the 2026 AIC main page (no sponsor logos in HTML), and third-party ExpoGage (HTTP 403/paywalled). Exhibitor names appear to be attendee-only (conference app). Closest page: https://www.aamva.org/events-education/conferences-meetings/conferences/2026-annual-international-conference/exhibit-sponsor

### Election Center National Conference (Elections)
- Platform: iMIS portal (portal.electioncenter.org) for registration only
- Notes: 41st Annual, Aug 19-21, 2026, Kansas City MO. No public exhibitor/sponsor directory found. Tried: 'Election Center national conference exhibitors/sponsors 2025 and 2026', site:electioncenter.org search, and direct fetches - electioncenter.org and portal.electioncenter.org both return 403 to the fetcher; only login-walled iMIS exhibitor-registration pages (EventKey BOOTH825) surface. Vendor names likely only in the printed program or behind member login.

### Esri UC (GIS)
- Platform: Esri event portal (JS app) - directory currently offline
- Notes: No public exhibitor list is live right now. The 2026 UC (July 13-17 2026, San Diego) had a 'Sponsors and Exhibitors' section at esri.com/en-us/about/events/uc/agenda/sponsors-exhibitors inside a JS event portal, but that URL now redirects to the 2027 UC overview (site rolled over post-event); a Wayback snapshot of the same path is only the portal shell with no names. Also tried: uc/agenda/expo page (no names, live and archived), coolmaps.esri.com/UC/UCMap26 (404), site:esri.com searches, 10times.com (403). Re-check the /uc/agenda/sponsors-exhibitors URL when the 2027 portal populates; 2026 roster otherwise lived in the Esri Events mobile app.

### MESC (Medicaid systems)
- Platform: custom (WordPress/WooCommerce booth-sales site)
- Notes: MESC 2026 (Aug 17-20, Portland OR, run by NESCSO) publishes no public exhibitor/sponsor roster. Tried: 'MESC 2026 exhibitor list' searches, mesconference.org homepage, /sponsorships-2026/ (404), /sponsorships-2026/exhibit-hall/ (static floor-plan image, no names), and the booth-selection chart at mesconference.org/tc_seat_charts/mesc-2026-exhibit-hall-c/ (JS seat-picker, no names in HTML and unclear it ever shows company names). Site claims 'well over 100 exhibitors' (~100+). Third-party gated list exists at vendelux.com/insights/mesc-2026-attendee-list. Known 2026 sponsors from vendor pages: Conduent, Gainwell, Maximus, Acentra Health, PCG, Catalyst Solutions.

### NASPO Annual (Procurement (state))
- Platform: Cvent (events.naspo.org) for event pages; naspo.org behind Cloudflare
- Notes: No public exhibitor or sponsor roster found. Tried: web searches for 2025 and 2026 annual conference sponsor/exhibitor lists; Cvent pages events.naspo.org/event/2025NASPOAnnualConference/summary and the Exchange 2026 page (fetchable, but name no companies); naspo.org pages (naspo-annual, get-involved/partnerships) blocked by Cloudflare challenge to both WebFetch and curl. NASPO Annual (Sept 27-30 2026, Phoenix) is a member-focused event with a Strategic Partner program (20+ partners) rather than a public expo directory - nearest page is https://www.naspo.org/get-involved/partnerships/ (walled). NASPO Exchange is the trade-show-format sibling event.
