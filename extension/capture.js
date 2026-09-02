/* SLED JOBS capture — the content script.
 *
 * Same harvester the bookmarklet proved out (position first, pattern second;
 * job segment plus something after it), with the two things a bookmarklet
 * cannot do:
 *
 *   - talks straight to the local admin through the background worker, so
 *     capture is one click instead of copy-then-paste
 *   - a single-posting mode: on a page that IS one job (a LinkedIn view page,
 *     a Greenhouse job page), it captures the title and the full JD text,
 *     which is the thing the board never has and scoring always wants
 *
 * It still runs once, on click, over the page you are looking at. No
 * scrolling, no pagination, no timers, no background reading — activeTab
 * means the extension cannot even see a page until it is clicked.
 */
(() => {
  const old = document.getElementById("sscap");
  if (old) { old.remove(); return; }

  /* ---- the panel lives in a shadow root ---------------------------------
   *
   * It used to be a plain div appended to the page, which means the PAGE'S
   * CSS applied to it. On Ashby that collapsed the company-search results on
   * top of each other into an unreadable smear - the first thing the owner
   * said about the first real capture was "this needs to be cleaned up".
   *
   * A shadow root ends that in both directions: the host page's selectors
   * cannot match anything inside, and nothing here leaks out onto a page we
   * are a guest on.
   *
   * `all: initial` on the host is the other half and is easy to miss.
   * Shadow DOM blocks SELECTORS, not INHERITANCE - font, colour, line-height
   * and direction still flow in from whatever the page set on <body>. The
   * reset stops that, and the panel then declares everything it needs.
   *
   * The id stays on the host, because the host is what a second click has to
   * find to close. */
  const host = document.createElement("div");
  host.id = "sscap";
  host.style.cssText = "all:initial;position:fixed;top:0;left:0;width:0;"
    + "height:0;z-index:2147483647";
  const root = host.attachShadow({ mode: "open" });

  const api = (path, body) => new Promise((res) =>
    chrome.runtime.sendMessage({ kind: "api", path, body }, res));

  /* Three different failures used to read as one sentence. "The admin is not
     running" and "the admin refused that" are not the same problem, and only
     the first is fixed by starting the admin - so the reply's own words win
     when it has any. An undefined r is the worker dying before it answered. */
  /* The worker now sends a written sentence for the unreachable case, so
     r.error carries it. This fallback is for the shape where the message
     never arrived at all - the worker asleep, the extension reloaded
     mid-click - which is the only case left where we know nothing. */
  const trouble = (r) =>
    (r && (r.error || (r.data && r.data.error)))
    || "No answer from the extension. Reload the page and click again.";

  /* ---- harvest: list pages -------------------------------------------- */
  const HREF_RE = new RegExp(
    "/(jobs?|careers?|positions?|openings?|vacanc(y|ies)|opportunit(y|ies)"
    + "|postings?|job-details|job_listing)/[^/?#]{3,}"
    + "|[?&](gh_jid|jobid|job_id|jid|reqid|requisitionid|pid|currentJobId)="
    + "|(jobs\\.lever\\.co|jobs\\.ashbyhq\\.com|apply\\.workable\\.com"
    + "|[a-z0-9-]+\\.breezy\\.hr|[a-z0-9-]+\\.recruitee\\.com)"
    + "/[^/?#]+/[^/?#]{3,}", "i");
  const NOT_RE = new RegExp(
    "^(jobs?|careers?|all jobs|view all|search|apply|apply now|learn more|home"
    + "|about|contact|benefits|culture|life at|our team|back|next|previous"
    + "|see all|open positions|current openings|sign in|log in|share|save"
    + "|easy apply|show more|load more|dismiss|report|overview|application)$", "i");
  const LOC_RE = /^(remote|hybrid|on-?site|[A-Z][A-Za-z .'-]+,\s*[A-Z][A-Za-z .]+)/;
  const CHIP_RE = /^(details|apply|view|new|featured|urgent|[A-Z0-9 &/-]{2,26})$/;
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  function harvestList() {
    const seen = [], out = [];
    for (const a of document.querySelectorAll("a[href]")) {
      let href = "";
      try { href = new URL(a.getAttribute("href"), location.href).href; }
      catch (e) { continue; }
      if (!HREF_RE.test(href)) continue;
      // textContent as well as innerText: a hidden or CSS-collapsed anchor
      // has no innerText and the bookmarklet has always read both.
      const raw = a.innerText || a.textContent || "";
      const lines = raw.split("\n").map((x) => x.trim()).filter(Boolean);
      if (!lines.length) continue;
      const body = lines.filter((l) => !(CHIP_RE.test(l) && l === l.toUpperCase()));
      const title = clean(body[0] || "");
      let loc = "";
      for (const l of body.slice(1))
        if (LOC_RE.test(l) && l.length < 64) { loc = l; break; }
      if (!title || title.length < 3 || title.length > 120) continue;
      if (NOT_RE.test(title)) continue;

      // DEDUP ON THE LINK, NOT THE TITLE. Same rule ats.py states at length
      // and for the same reason: two genuinely different requisitions often
      // share a name, and keying on the title deletes the second one. A board
      // with twelve "Field Service Engineer" reqs captured one.
      if (seen.indexOf(href) !== -1) continue;
      seen.push(href);

      // Some boards put the location in a sibling cell rather than inside the
      // anchor. Only look there when the anchor had none, and keep it short -
      // an over-wide row swallows the entire rest of the board. This existed
      // in the bookmarklet from the first commit and was never in the
      // extension, so the extension silently dropped the location on every
      // board of that shape.
      if (!loc) {
        const row = a.closest("li, tr, article, [class*=job], [class*=post]");
        if (row && row.innerText && row.innerText.length < 400) {
          const m = clean(row.innerText).replace(title, "").match(
            /([A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\b|Remote[A-Za-z ,-]{0,20}|Hybrid[A-Za-z ,-]{0,20})/);
          if (m) loc = clean(m[1]).slice(0, 60);
        }
      }
      out.push({ title, url: href, location: loc.slice(0, 60) });
    }
    return out;
  }

  /* ---- harvest: a page that IS one posting ---------------------------- */
  function harvestSingle() {
    // JobPosting structured data first: it is machine-written and carries the
    // exact title and location. Detail pages often have it even when listing
    // pages do not (measured: 0 of 40 listing pages, but details differ).
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const d = JSON.parse(s.textContent);
        const nodes = Array.isArray(d) ? d : [d];
        for (const n of nodes) {
          if (n && (n["@type"] === "JobPosting"
                    || (Array.isArray(n["@type"]) && n["@type"].includes("JobPosting")))) {
            const locObj = n.jobLocation && (Array.isArray(n.jobLocation)
              ? n.jobLocation[0] : n.jobLocation);
            const addr = locObj && locObj.address;
            const loc = addr ? clean([addr.addressLocality, addr.addressRegion]
              .filter(Boolean).join(", ")) : "";
            return { title: clean(n.title), location: loc, url: location.href,
                     jd_text: clean((n.description || "").replace(/<[^>]+>/g, " "))
                       .slice(0, 20000),
                     via: "structured data on the page" };
          }
        }
      } catch (e) { /* not JSON, keep looking */ }
    }
    // NO SCHEMA. Everything past this point is a guess about whether this
    // page is a job at all, so it has to find EVIDENCE before it answers.
    //
    // This used to take the biggest heading unconditionally and call it a
    // posting. On BusPatrol's marketing homepage that produced "School Bus
    // Safety Starts Here" with 2,463 characters of brochure copy as the job
    // description, offered for sending with a straight face. A tool that
    // invents a posting from an h1 is the same failure as a page scan that
    // reports "no jobs" when it could not read - it states something it does
    // not know.
    //
    // Two things count as evidence and neither is a heuristic about wording:
    //
    //   the URL is a known ATS detail page - somebody navigated to one job
    //   there is an APPLY control - a page offering to take an application
    //   is a page about a specific job
    //
    // Anything else returns null and the panel says so. "I cannot tell what
    // this page is" is a real answer and the honest one on a homepage.
    const applyish = [...document.querySelectorAll("a,button,input[type=submit]")]
      .some((el) => {
        const s = clean(el.innerText || el.value || el.getAttribute("aria-label") || "");
        return /^(apply|apply now|apply for this job|submit application|start application|apply here)$/i.test(s);
      });

    if (!onDetailPage && !applyish) return null;

    /* THE FIRST H1 IS OFTEN THE BOARD, NOT THE JOB. On Crelate the page opens
       "Hire Tomorrow - Job Board" and the actual role, "Account Executive,
       SLED - Texas", is the heading below it. Taking h1 blindly captured the
       portal's name as a job title with 3,959 characters of the right JD
       attached to it.
       So walk the headings in document order and skip the ones that name a
       BOARD rather than a role. Anchored on the whole heading where it can
       be - "Careers" is furniture, "Careers Coordinator" is a job. */
    /* ANCHORED AT THE END, not a substring, and that distinction is the whole
       rule. Board furniture ENDS with the board word - "Hire Tomorrow - Job
       Board", "Acme Careers". A job title carries on past it - "Job Board
       Administrator", "Careers Coordinator". A substring match on "job board"
       deletes the administrator, which is the same mistake this project made
       with "report" in the bookmarklet's nav filter a day earlier. */
    const BOARD_HEADING = new RegExp(
      "(^|[\\s\\-\u2013\u2014|:])("
      + "job board|jobs|careers?|open (roles|positions|jobs)|"
      + "current (openings|opportunities)|opportunities|join (us|our team)|"
      + "work (with|for) us|vacancies|search jobs|all jobs|job openings|"
      + "job portal|careers? (portal|cent(er|re)|page|home)"
      + ")$", "i");

    let title = "";
    for (const h of document.querySelectorAll("h1,h2,h3")) {
      const s = clean(h.innerText || "");
      if (!s || s.length < 3 || s.length > 120) continue;
      if (BOARD_HEADING.test(s)) continue;
      title = s;
      break;
    }
    /* Nothing survived - fall back to the tab title, which on a job page is
       usually the role and the employer. Take the part before the separator:
       "Account Executive, SLED - Texas | Hire Tomorrow" is the role. */
    if (!title) {
      title = clean(document.title).split(/\s+[|\u2013\u2014]\s+/)[0].slice(0, 120);
    }
    if (!title || title.length < 3) return null;
    const main = document.querySelector("main, article, [class*=description], [class*=job-details]")
      || document.body;
    return { title, location: "", url: location.href,
             jd_text: clean(main.innerText).slice(0, 20000),
             via: onDetailPage ? "a job page on a board we know"
                               : "an apply button on this page" };
  }

  /* ---- panel ----------------------------------------------------------- */
  /* ---- a detail page is not a board -------------------------------------
   *
   * The mode used to be chosen on a count: fewer than two job-shaped links
   * meant "this is one posting". On an Ashby posting page that test picks the
   * WRONG branch, because Ashby's own "Overview" and "Application" tabs are
   * two job-shaped links - and the first real capture duly filed both of them
   * as jobs at BusPatrol.
   *
   * The URL says which it is, and says so before anything is counted. These
   * are the detail-page shapes of the boards this project actually reads: a
   * slug, then an opaque id. A listing has no id after the slug.
   *
   * The count still decides everywhere else, because most careers pages are
   * neither shape and there the old test is the right one. */
  const DETAIL = [
    /jobs\.ashbyhq\.com\/[^/]+\/[0-9a-f-]{16,}/i,
    /(boards|job-boards)\.greenhouse\.io\/[^/]+\/jobs\/\d+/i,
    /jobs\.lever\.co\/[^/]+\/[0-9a-f-]{16,}/i,
    /\.recruitee\.com\/o\/[^/]+/i,
    /apply\.workable\.com\/[^/]+\/j\/[A-Z0-9]{6,}/i,
    /\.breezy\.hr\/p\/[0-9a-f]{8,}/i,
    /myworkdayjobs\.com\/.+\/job\//i,
    /jobs\.crelate\.com\/portal\/[^/]+\/job\/[a-z0-9]{8,}/i,
  ];
  const onDetailPage = DETAIL.some((re) => re.test(location.href));

  const jobs = onDetailPage ? [] : harvestList();
  const single = (onDetailPage || jobs.length < 2) ? harvestSingle() : null;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* One stylesheet inside the shadow rather than inline attributes on every
     element. It cannot escape and the page cannot reach it, so the panel
     finally looks the same on every site instead of the same on the sites
     that happened to be tested. */
  const css = document.createElement("style");
  css.textContent = `
    :host { all: initial }
    .panel {
      position: fixed; top: 16px; right: 16px; width: 400px; max-height: 86vh;
      overflow: auto; box-sizing: border-box;
      background: #FAF7F0; color: #1F2536;
      border: 1px solid #C9DCE8; border-radius: 0; padding: 14px 16px;
      box-shadow: 0 10px 40px rgba(0,0,0,.28);
      font: 13px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
      text-align: left; letter-spacing: normal; text-transform: none;
    }
    .panel * { box-sizing: border-box; margin: 0; padding: 0; float: none;
               position: static; max-width: none; text-indent: 0 }
    .panel b { font-weight: 700 }
    .panel label { display: flex; gap: 7px; align-items: flex-start;
                   padding: 3px 0; cursor: pointer; line-height: 1.45 }
    .panel input[type=checkbox] { margin: 3px 0 0; flex: 0 0 auto; width: 14px;
                                  height: 14px; accent-color: #0B57C4 }
    .panel input[type=text], .panel input:not([type]) {
      width: 100%; padding: 8px 10px; border: 1px solid #C9DCE8;
      border-radius: 0; background: #fff; color: #1F2536;
      font: 13px/1.5 inherit; font-family: inherit;
    }
    .panel button {
      width: 100%; padding: 10px 14px; border: 0; border-radius: 0;
      background: #0B57C4; color: #FAF7F0; font: 700 13.5px/1.4 inherit;
      font-family: inherit; cursor: pointer;
    }
    .panel button[disabled] { background: #9FB3C4; cursor: default }
    /* THE LINE THAT WAS BROKEN. Each search result is its own block with its
       own line box; the page used to collapse these onto one another. */
    .panel .hit { display: block; padding: 6px 8px; cursor: pointer;
                  border-bottom: 1px solid #EDF3F7; line-height: 1.45 }
    .panel .hit:hover { background: #EDF3F7 }
    .panel a { color: #0B57C4; text-decoration: none }
    .panel a:hover { text-decoration: underline }
  `;


  /* ---- what the board already knows about this company -------------------
   *
   * The first real capture went to BusPatrol: on Ashby, read every night, and
   * already carrying thirteen postings including the Regional Account
   * Executive. The tool let that happen without a word, and filed two of
   * Ashby's own tabs as jobs on top of it.
   *
   * So the moment a company is picked, this says what is already true about
   * it. It does not disable anything - a person standing on a page may have a
   * reason the board does not know - but the default answer is on screen
   * before the button is pressed rather than in a queue afterwards.
   *
   * `ats: unknown` and `ats: html` are the pile that needs a person: 685
   * companies whose site was read and yielded no board a fetcher can use. */
  const STRUCTURED = ["ashby", "greenhouse", "lever", "workable", "recruitee",
                      "breezy", "smartrecruiters", "bamboohr", "workday",
                      "rippling", "jazzhr", "icims", "paylocity", "oracle"];

  function verdictOf(c) {
    const kind = c.ats_type || null;
    if (STRUCTURED.indexOf(kind) !== -1) {
      return c.postings > 0
        ? { tone: "skip",
            text: `Already read. ${c.name} is on ${kind} and the board carries `
                + `${c.postings} posting${c.postings === 1 ? "" : "s"} for them. `
                + `You do not need this one.` }
        : { tone: "note",
            text: `${c.name} is on ${kind}, so the crawler reads it - but the `
                + `board shows no postings. Worth capturing if you can see some.` };
    }
    if (kind === "html" || !kind || kind === "unknown") {
      return { tone: "go",
               text: `No board a fetcher can read. This is exactly the pile `
                   + `capture is for.` };
    }
    return { tone: "note", text: `On file as ${kind}.` };
  }

  /* ---- is THIS PAGE actually this company's? -----------------------------
   *
   * The verdict above says what the board knows about the company. It said
   * nothing about the page, and the first hour of real capturing filed twelve
   * UK IT-support roles from airitcareers.co.uk - Air IT Group, a British MSP
   * - against Air-Transport IT Services of Orlando. Same name, different
   * company, and the panel had no way to notice.
   *
   * So the page's own identity fields - title, description, first h1 - go to
   * the admin, which runs the same identifies() check this project already
   * trusts against websites. The reply is advisory: the button stays live,
   * because a person on a page may know something the check does not. But a
   * mismatch is on screen before Send, which is where it has to be. */
  const meta = (n) => {
    const el = document.querySelector(`meta[name="${n}"],meta[property="${n}"]`);
    return el ? (el.getAttribute("content") || "") : "";
  };
  async function identify(c) {
    const h1 = document.querySelector("h1");
    const r = await api("/api/identify", {
      company_id: c.id, page_url: location.href,
      title: document.title || "",
      meta: meta("description") || meta("og:site_name") || meta("og:title") || "",
      h1: h1 ? (h1.innerText || "") : "",
    });
    const el = box.querySelector("#ss-ident");
    if (!r || !r.ok || !r.data || r.data.error) { el.innerHTML = ""; return; }
    const d = r.data;
    if (d.identifies === true || d.identifies === null) { el.innerHTML = ""; return; }
    el.innerHTML =
      `<div style="border-left:3px solid #a3342a;padding:6px 0 6px 9px;margin:0 0 9px;`
      + `color:#1F2536"><b style="color:#a3342a">Different company?</b> `
      + `${esc(d.says)}</div>`;
  }

  function say(c) {
    const v = verdictOf(c);
    const colour = { skip: "#a3342a", go: "#0F7A4A", note: "#556F82" }[v.tone];
    const verdictEl = box.querySelector("#ss-verdict");
    verdictEl.innerHTML =
      `<div style="border-left:3px solid ${colour};padding:6px 0 6px 9px;`
      + `margin:0 0 9px;color:#1F2536">${esc(v.text)}</div>`;
  }

  const box = document.createElement("div");
  box.className = "panel";

  /* Three states, and the third one used to be missing. A page that is
     neither a board nor a posting - a marketing homepage, an about page -
     now SAYS so instead of offering an invented job. */
  const head = single
    ? `Looks like one posting: <b>${esc(single.title)}</b>`
      + `<div style="color:#7C97AA;font-size:11.5px;margin-top:2px">read from ${esc(single.via)}`
      + `${single.jd_text ? ` · ${single.jd_text.length.toLocaleString()} chars of JD captured` : ""}</div>`
    : jobs.length
    ? `${jobs.length} job links on this page. Uncheck anything that is not a posting.`
    : `<b>Nothing here looks like a job.</b>`
      + `<div style="color:#7C97AA;font-size:11.5px;margin-top:2px">No job links, no `
      + `posting data, and no apply button. If this is a careers page whose jobs `
      + `load in a frame, open the frame directly and click again.</div>`;

  box.innerHTML =
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:9px">
       <b style="font-size:13.5px">SLED JOBS capture</b>
       <span id="ss-x" style="margin-left:auto;cursor:pointer;color:#7C97AA">close</span></div>
     <div style="color:#556F82;margin-bottom:9px">${head}</div>
     <div id="ss-verdict"></div>
     <div id="ss-ident"></div>
     <div id="ss-list" style="margin:0 0 10px;max-height:38vh;overflow:auto"></div>
     <input id="ss-q" placeholder="which company? type a name"
       style="width:100%;padding:8px 10px;border:1px solid #C9DCE8;border-radius:0;
              font:inherit;margin-bottom:5px;box-sizing:border-box">
     <div id="ss-hits"></div>
     <button id="ss-go" style="width:100%;padding:9px;border:0;border-radius:0;margin-top:6px;
       background:#0B57C4;color:#FAF7F0;font:inherit;font-weight:600;cursor:pointer">
       Send to SLED JOBS</button>
     <div id="ss-msg" style="margin-top:8px;color:#556F82"></div>
     <div id="ss-work" style="margin-top:14px;padding-top:11px;
          border-top:1px solid #C9DCE8">
       <div style="display:flex;align-items:center;gap:8px">
         <span style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;
               color:#7C97AA">what to hit next</span>
         <select id="ss-queue" style="margin-left:auto;font:inherit;padding:3px 6px;
                 border:1px solid #C9DCE8;background:#fff;color:#1F2536">
           <option value="boards">no board found</option>
           <option value="founded">founding year</option>
           <option value="blocked">blocked, retry</option>
           <option value="websites">no website</option>
         </select>
       </div>
       <div id="ss-rows" style="margin-top:7px;color:#556F82">loading…</div>
     </div>
     <div id="ss-note" style="margin-top:12px;padding-top:11px;
          border-top:1px solid #C9DCE8">
       <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;
            color:#7C97AA;margin-bottom:6px">record what you found</div>
       <div style="display:flex;gap:6px">
         <select id="ss-kind" style="font:inherit;padding:6px;border:1px solid #C9DCE8;
                 background:#fff;color:#1F2536;flex:0 0 auto">
           <option value="board">board address</option>
           <option value="founded">founding year</option>
           <option value="posts-at">posts at</option>
           <option value="website">website</option>
           <option value="nothing">nothing here</option>
         </select>
         <input id="ss-val" placeholder="paste it" style="flex:1 1 auto">
       </div>
       <button id="ss-save" style="margin-top:7px;background:#1F2536">Save note</button>
       <div id="ss-nmsg" style="margin-top:6px;color:#556F82"></div>
     </div>`;

  const list = box.querySelector("#ss-list");
  const picked = single ? [single] : jobs;
  if (!single) {
    jobs.forEach((j, i) => {
      const row = document.createElement("label");
      row.style.cssText = "display:flex;gap:7px;align-items:flex-start;padding:4px 0";
      row.innerHTML = `<input type="checkbox" data-i="${i}" checked style="margin-top:3px">
        <span><span style="font-weight:500">${esc(j.title)}</span>${
          j.location ? `<span style="color:#7C97AA"> — ${esc(j.location)}</span>` : ""}</span>`;
      list.appendChild(row);
    });
    if (!jobs.length)
      list.innerHTML = `<div style="color:#a8620f">No job links found and this does not read
        as a single posting. If the jobs are in an iframe, open the frame directly and click again.</div>`;
  }

  box.querySelector("#ss-x").onclick = () => host.remove();

  let company = null, timer;
  const q = box.querySelector("#ss-q"), hits = box.querySelector("#ss-hits"),
        msg = box.querySelector("#ss-msg");
  q.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      hits.innerHTML = ""; company = null;
      if (q.value.trim().length < 2) return;
      const r = await api("/api/search-companies", { q: q.value });
      if (!r || !r.ok) {
        msg.innerHTML = `<span style="color:#a3342a">${esc(trouble(r))}</span>`;
        return;
      }
      (r.data.results || []).forEach((c) => {
        const d = document.createElement("div");
        d.className = "hit";
        d.innerHTML = `${esc(c.name)} <span style="color:#7C97AA">${esc(c.sector)}</span>`;
        d.onclick = () => {
          company = c; q.value = c.name; hits.innerHTML = "";
          say(c);
          identify(c);
        };
        hits.appendChild(d);
      });
    }, 200);
  };

  box.querySelector("#ss-go").onclick = async () => {
    if (!company) { msg.textContent = "Pick a company first."; return; }
    const chosen = single ? picked
      : [...box.querySelectorAll("#ss-list input:checked")].map((cb) => jobs[+cb.dataset.i]);
    if (!chosen.length) { msg.textContent = "Nothing selected."; return; }
    msg.textContent = "sending…";
    const r = await api("/api/capture",
      { company_id: company.id, jobs: chosen, page_url: location.href });
    const sent = r && r.ok && !r.data.error;
    /* THREE OUTCOMES, NOT TWO. A capture the worker is HOLDING because the
       admin is off is not a failure - the work is safe and will go when the
       admin comes back - so it is not painted in the refusal colour, and the
       panel closes as it does on a success. Painting it red taught the person
       their click was wasted, which was the opposite of true. */
    const held = r && r.queued;
    msg.innerHTML = sent
      ? `<b style="color:#0B57C4">${esc(r.data.message)}`
        + `${r.flushed ? ` (and ${r.flushed} held earlier)` : ""}</b>`
      : held
      ? `<b style="color:#7a5b00">${esc(trouble(r))}</b>`
      : `<span style="color:#a3342a">${esc(trouble(r))}</span>`;
    /* IT USED TO CLOSE ITSELF after a successful send, and that was wrong.
       The panel is not finished when the jobs land - there is a note to
       record, a worklist to read, sometimes a second capture on the same
       site. Closing on success threw all of that away and made the person
       click the penguin again to get back to where they already were.
       It stays open. The close link is right there. */
    if (sent) {
      box.querySelector("#ss-go").textContent = "Sent \u2014 send again?";
      // The list stays checked, so a second click would send twice. Clearing
      // it makes the button honest about what it would do now.
      box.querySelectorAll("#ss-list input:checked").forEach((cb) => {
        cb.checked = false;
      });
    }
  };

  /* ---- what to hit next ---------------------------------------------------
   *
   * Not a mode and not a menu in front of the tool: a list at the BOTTOM of
   * the panel that is already open. The owner asked to be told what he should
   * be capturing, not to be asked what he is doing.
   *
   * The order is the queue's own and is not re-sorted here. q_boards sorts by
   * conference floor, most-exhibited first, because that list is worked by
   * floor - which is the loop this exists to serve: click a row, land on
   * their site, click the penguin there.
   */
  async function loadWork() {
    const sel = box.querySelector("#ss-queue");
    const rows = box.querySelector("#ss-rows");
    rows.textContent = "loading…";
    const r = await api("/api/worklist", { queue: sel.value, limit: 8 });
    if (!r || !r.ok || !r.data || r.data.error) {
      rows.innerHTML = `<span style="color:#a3342a">${esc(trouble(r))}</span>`;
      return;
    }
    const d = r.data;
    if (!(d.rows || []).length) { rows.textContent = "nothing waiting here."; return; }
    rows.innerHTML =
      `<div style="color:#7C97AA;font-size:11.5px;margin-bottom:5px">`
      + `${d.total} waiting</div>`
      + d.rows.map((c) =>
          `<div class="hit" style="cursor:default">`
          + `<a href="${esc(c.website || "#")}" target="_blank" rel="noopener"`
          + ` style="font-weight:600">${esc(c.name)}</a>`
          + (c.events && c.events.length
              ? ` <span style="color:#7C97AA">${esc(c.events.join(" · "))}</span>` : "")
          + `</div>`).join("");
  }


  /* ---- record what you found --------------------------------------------
   *
   * The half of the loop a capture cannot cover. Standing on a company's
   * site, the useful answer is often not "here are their jobs" - it is
   * "their board is at this address", "founded 2014", "they only post on
   * LinkedIn", or "I looked and there is nothing". None of those is a
   * posting and every one is worth keeping.
   *
   * It goes to task-note, which APPENDS to a staging file. Nothing here
   * touches companies.json - scripts/apply_task_notes.py does that in Python
   * behind validate(), which is why this action can be open to the extension
   * at all.
   *
   * The company is whichever one is picked above. Without one there is
   * nothing to attach a note to, and saying so beats writing it nowhere. */
  function wireNote() {
    const kind = box.querySelector("#ss-kind");
    const val = box.querySelector("#ss-val");
    const nmsg = box.querySelector("#ss-nmsg");
    const sync = () => {
      const none = kind.value === "nothing";
      val.disabled = none;
      val.placeholder = none ? "nothing to type - that IS the finding"
        : { board: "https://boards.greenhouse.io/…",
            founded: "2014",
            "posts-at": "linkedin",
            website: "https://…" }[kind.value] || "paste it";
    };
    kind.onchange = sync;
    sync();

    box.querySelector("#ss-save").onclick = async () => {
      if (!company) {
        nmsg.innerHTML = `<span style="color:#a3342a">Pick a company above `
          + `first - a note has to be about somebody.</span>`;
        return;
      }
      nmsg.textContent = "saving…";
      const r = await api("/api/task-note", {
        kind: kind.value, company_id: company.id,
        value: val.disabled ? "" : val.value.trim(),
        page_url: location.href,
      });
      const ok = r && r.ok && r.data && !r.data.error;
      nmsg.innerHTML = ok
        ? `<b style="color:#0F7A4A">${esc(r.data.message)}</b>`
        : `<span style="color:#a3342a">${esc(trouble(r))}</span>`;
      if (ok) { val.value = ""; loadWork(); }
    };
  }

  root.appendChild(css);
  root.appendChild(box);
  document.body.appendChild(host);

  box.querySelector("#ss-queue").onchange = loadWork;
  wireNote();
  loadWork();
})();
