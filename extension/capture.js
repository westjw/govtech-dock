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
    + "|easy apply|show more|load more|dismiss|report)$", "i");
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
    // Fallback: the biggest heading plus the page text. Cruder, and the panel
    // shows exactly what it grabbed before anything is sent.
    const h = document.querySelector("h1") || document.querySelector("h2");
    const title = clean(h ? h.innerText : document.title).slice(0, 120);
    const main = document.querySelector("main, article, [class*=description], [class*=job-details]")
      || document.body;
    return { title, location: "", url: location.href,
             jd_text: clean(main.innerText).slice(0, 20000),
             via: "page heading and text" };
  }

  /* ---- panel ----------------------------------------------------------- */
  const jobs = harvestList();
  const single = jobs.length < 2 ? harvestSingle() : null;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const box = document.createElement("div");
  box.id = "sscap";
  box.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;"
    + "width:400px;max-height:86vh;overflow:auto;background:#FAF7F0;color:#1F2536;"
    + "border:1px solid #C9DCE8;border-radius:0;padding:14px 16px;"
    + "box-shadow:0 10px 40px rgba(0,0,0,.28);"
    + "font:13px/1.5 ui-sans-serif,-apple-system,'Segoe UI',Roboto,sans-serif";

  const head = single
    ? `Looks like one posting: <b>${esc(single.title)}</b>`
      + `<div style="color:#7C97AA;font-size:11.5px;margin-top:2px">read from ${esc(single.via)}`
      + `${single.jd_text ? ` · ${single.jd_text.length.toLocaleString()} chars of JD captured` : ""}</div>`
    : `${jobs.length} job links on this page. Uncheck anything that is not a posting.`;

  box.innerHTML =
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:9px">
       <b style="font-size:13.5px">SLED JOBS capture</b>
       <span id="ss-x" style="margin-left:auto;cursor:pointer;color:#7C97AA">close</span></div>
     <div style="color:#556F82;margin-bottom:9px">${head}</div>
     <div id="ss-list" style="margin:0 0 10px;max-height:38vh;overflow:auto"></div>
     <input id="ss-q" placeholder="which company? type a name"
       style="width:100%;padding:8px 10px;border:1px solid #C9DCE8;border-radius:0;
              font:inherit;margin-bottom:5px;box-sizing:border-box">
     <div id="ss-hits"></div>
     <button id="ss-go" style="width:100%;padding:9px;border:0;border-radius:0;margin-top:6px;
       background:#0B57C4;color:#FAF7F0;font:inherit;font-weight:600;cursor:pointer">
       Send to SLED JOBS</button>
     <div id="ss-msg" style="margin-top:8px;color:#556F82"></div>`;

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

  box.querySelector("#ss-x").onclick = () => box.remove();

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
        d.style.cssText = "padding:5px 8px;border-radius:6px;cursor:pointer";
        d.innerHTML = `${esc(c.name)} <span style="color:#7C97AA">${esc(c.sector)}</span>`;
        d.onmouseenter = () => (d.style.background = "#f1eeea");
        d.onmouseleave = () => (d.style.background = "");
        d.onclick = () => { company = c; q.value = c.name; hits.innerHTML = ""; };
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
    if (sent || held) setTimeout(() => box.remove(), held ? 4200 : 2400);
  };

  document.body.appendChild(box);
})();
