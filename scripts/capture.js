/* GovTech Dock — page capture.
 *
 * 537 careers pages on file have no enumerable job list our fetcher can reach.
 * Not JS shells hiding one: rendering them in headless Chromium finds nothing
 * either. They are third-party widgets in iframes, session-gated boards, and
 * pages that only draw a list after an interaction. A person looking at the
 * page sees the jobs regardless, because their browser runs all of it.
 *
 * So this reads what is already on screen and hands it to the admin server.
 *
 * The handoff is the clipboard, not a request. A page on https cannot reach
 * http://127.0.0.1 - Chrome blocks both fetch and a script tag - so anything
 * that posted directly would fail on every real careers site. Copying is the
 * one channel that works everywhere, including LinkedIn, and needs no
 * permissions, no extension review, and no server reachable from the page.
 *
 * It runs once, when clicked, over the current document. It does not scroll,
 * paginate, follow links, log in, or run on a timer - the difference between
 * reading a page you opened and harvesting a site.
 */
(() => {
  const old = document.getElementById('gtd-cap');
  if (old) old.remove();

  /* ---- what a job link looks like ------------------------------------ */

  // Boards differ wildly, so this leans on the href rather than the markup:
  // essentially every ATS puts the job id in the path. It has to be the job
  // SEGMENT PLUS something after it - '/careers' alone matches every nav link
  // on a careers page, which is how the first version came back with
  // CHALLENGES, SOLUTIONS and Cookie Preferences.
  const HREF_RE = new RegExp(
    '/(jobs?|careers?|positions?|openings?|vacanc(y|ies)|opportunit(y|ies)'
    + '|postings?|job-details|job_listing)/[^/?#]{3,}'
    + '|[?&](gh_jid|jobid|job_id|jid|reqid|requisitionid|pid|currentJobId)='
    // ATS hosts that put the id straight after the company slug, with no job
    // word anywhere in the path - Lever and Ashby are the common ones.
    + '|(jobs\\.lever\\.co|jobs\\.ashbyhq\\.com|apply\\.workable\\.com'
    + '|[a-z0-9-]+\\.breezy\\.hr|[a-z0-9-]+\\.recruitee\\.com)'
    + '/[^/?#]+/[^/?#]{3,}', 'i');

  // Anchors that are navigation, not postings. A careers page is full of these.
  const NOT_RE = new RegExp(
    '^(jobs?|careers?|all jobs|view all|search|apply|apply now|learn more|home'
    + '|about|contact|benefits|culture|life at|our team|back|next|previous'
    + '|see all|open positions|current openings|sign in|log in|share|save'
    + '|easy apply|show more|load more|dismiss|report|overview|application)$', 'i');

  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  // A location line, not a job title: "Austin, TX", "Remote - US", "Hybrid".
  const LOC_RE = /^(remote|hybrid|on-?site|[A-Z][A-Za-z .'-]+,\s*[A-Z][A-Za-z .]+)/;
  // Department chips and row furniture. Boards stack these above and below the
  // title inside the same anchor, which is why the first version produced
  // "GROWTH Account Executive - Software Manchester, England DETAILS".
  const CHIP_RE = /^(details|apply|view|new|featured|urgent|[A-Z0-9 &/-]{2,26})$/;

  function harvest() {
    const seen = [], out = [];
    for (const a of document.querySelectorAll('a[href]')) {
      let href = '';
      try { href = new URL(a.getAttribute('href'), location.href).href; }
      catch (e) { continue; }
      if (!HREF_RE.test(href)) continue;

      // innerText keeps the line breaks the board laid out with, and those
      // lines are the structure: chips, title, location, a button.
      const lines = (a.innerText || a.textContent || '')
        .split('\n').map(x => x.trim()).filter(Boolean);
      if (!lines.length) continue;

      // Position first, pattern second. Testing the location pattern first
      // stole the title whenever a title happened to look like a place:
      // "Database Administrator, Infrastructure - UK" matched, and the row came
      // back with Manchester as the job. Boards put the title first, so trust
      // that and only search the lines after it for a location.
      const body = lines.filter(line =>
        !(CHIP_RE.test(line) && line === line.toUpperCase()));
      const title = clean(body[0] || '');
      let loc = '';
      for (const line of body.slice(1)) {
        if (LOC_RE.test(line) && line.length < 64) { loc = line; break; }
      }
      if (!title || title.length < 3 || title.length > 120) continue;
      if (NOT_RE.test(title)) continue;
      // DEDUP ON THE LINK, NOT THE TITLE - the rule ats.py documents at
      // length. Two different requisitions often share a name, and keying on
      // the title silently deletes the second.
      if (seen.indexOf(href) !== -1) continue;
      seen.push(href);

      // Some boards put the location in a sibling cell rather than inside the
      // anchor. Only look there when the anchor had none, and keep it short -
      // an over-wide row swallows the entire rest of the board.
      if (!loc) {
        const row = a.closest('li, tr, article, [class*=job], [class*=post]');
        if (row && row.innerText && row.innerText.length < 400) {
          const m = clean(row.innerText).replace(title, '').match(
            /([A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\b|Remote[A-Za-z ,-]{0,20}|Hybrid[A-Za-z ,-]{0,20})/);
          if (m) loc = clean(m[1]).slice(0, 60);
        }
      }
      out.push({ title: title, url: href, location: loc.slice(0, 60) });
    }
    return out;
  }

  /* ---- the overlay ---------------------------------------------------- */

  const jobs = harvest();
  const esc = s => String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const box = document.createElement('div');
  box.id = 'gtd-cap';
  box.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;'
    + 'width:392px;max-height:84vh;overflow:auto;background:#fff;color:#1a1815;'
    + 'border:1px solid #d9d4cd;border-radius:11px;padding:14px 16px;'
    + 'box-shadow:0 10px 40px rgba(0,0,0,.28);'
    + 'font:13px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif';

  box.innerHTML =
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:9px">'
    + '<b style="font-size:13.5px">GovTech Dock capture</b>'
    + '<span id="gtd-x" style="margin-left:auto;cursor:pointer;color:#969086">'
    + 'close</span></div>'
    + '<div style="color:#6a655d;margin-bottom:9px">' + jobs.length
    + ' job link' + (jobs.length === 1 ? '' : 's') + ' on this page.'
    + (jobs.length ? ' Uncheck anything that is not a posting.' : '') + '</div>'
    + '<div id="gtd-list" style="margin:0 0 10px;max-height:46vh;overflow:auto"></div>'
    + '<button id="gtd-go" style="width:100%;padding:9px;border:0;border-radius:8px;'
    + 'background:#2f6f4f;color:#fff;font:inherit;font-weight:600;cursor:pointer">'
    + 'Copy for admin</button>'
    + '<div id="gtd-msg" style="margin-top:8px;color:#6a655d"></div>';

  const list = box.querySelector('#gtd-list');
  if (!jobs.length) {
    list.innerHTML = '<div style="color:#a8620f">No job links found. If you can '
      + 'see jobs on screen they are probably inside an iframe &mdash; right-click '
      + 'the list, choose <i>This Frame &rarr; Show Only This Frame</i>, then click '
      + 'the bookmarklet again.</div>';
    box.querySelector('#gtd-go').disabled = true;
    box.querySelector('#gtd-go').style.opacity = '.45';
  }
  jobs.forEach((j, i) => {
    const row = document.createElement('label');
    row.style.cssText = 'display:flex;gap:7px;align-items:flex-start;padding:4px 0';
    row.innerHTML = '<input type="checkbox" data-i="' + i + '" checked '
      + 'style="margin-top:3px"><span><span style="font-weight:500">'
      + esc(j.title) + '</span>'
      + (j.location ? '<span style="color:#969086"> &mdash; ' + esc(j.location)
                      + '</span>' : '') + '</span>';
    list.appendChild(row);
  });

  box.querySelector('#gtd-x').onclick = () => box.remove();

  box.querySelector('#gtd-go').onclick = async () => {
    const msg = box.querySelector('#gtd-msg');
    const picked = [].slice.call(box.querySelectorAll('#gtd-list input:checked'))
      .map(cb => jobs[+cb.dataset.i]);
    if (!picked.length) { msg.textContent = 'Nothing selected.'; return; }
    const payload = JSON.stringify({
      source: 'gtd-capture', page_url: location.href,
      page_title: document.title, jobs: picked
    });
    let ok = false;
    try {
      await navigator.clipboard.writeText(payload);
      ok = true;
    } catch (e) {
      // Clipboard needs a secure context and a user gesture. Both hold here,
      // but a site's permissions policy can still refuse, so fall back to a
      // textarea the person can copy out of by hand.
      const ta = document.createElement('textarea');
      ta.value = payload;
      ta.style.cssText = 'width:100%;height:88px;margin-top:8px;font:11px monospace';
      box.appendChild(ta);
      ta.select();
      try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
    }
    msg.innerHTML = ok
      ? '<b style="color:#2f6f4f">' + picked.length + ' copied.</b> Paste it into '
        + 'the Capture tab at 127.0.0.1:8787 and pick the company there.'
      : 'Could not reach the clipboard. Copy the text above by hand.';
  };

  document.body.appendChild(box);
})();
