/* The whole reason this is an extension and not a bookmarklet: an extension's
   service worker with a host permission can reach http://127.0.0.1 from a page
   served over https, and the page itself cannot - in Chrome.

   THAT LAST CLAUSE MATTERS AND THIS FILE USED TO OMIT IT. The comment here
   once asserted flatly that "a page on https cannot reach http://127.0.0.1".
   The observation was real; the generalisation was not. Private Network
   Access is a CHROME behaviour and Firefox and Safari have not implemented
   it, so this is a fact about the browser this runs in, not about browsers.
   The clipboard path the bookmarklet uses needs no permission and works
   everywhere, which is why it still exists.

   activeTab + on-click injection means the extension can read NOTHING until
   you click it, and then only the tab you clicked on. Same line the
   bookmarklet drew: reading a page you opened, never harvesting a site.

   ---- the token ----

   Every /api/ call now has to echo a per-process secret in a header. That
   header is what stops a website the owner happens to be visiting from driving
   the admin: a cross-origin page can send a request, but it cannot attach a
   custom header without a preflight, and the admin answers none. This
   extension is not same-origin either, so it is not covered by the shim the
   admin injects into its own page - it has to go and ask.

   /api/token is the route that hands one over, and asking it does not put the
   hole back. Precisely: a page on evil.com can SEND that GET, but it cannot
   read the reply - no response from the admin carries a CORS header, so the
   body is opaque to it - and the admin now also refuses the route outright to
   anything with an http(s) Origin, which every web page's fetch carries and
   none can drop. This worker's requests are exempt from CORS because of the
   host permission, which is exactly the difference being relied on.

   The token dies with the admin process, so a stale one after a restart is
   normal rather than an error. A 403 throws it away and retries once - once,
   because a genuinely wrong token must fail rather than loop. Nothing is
   stored: the token lives in this worker's memory and goes when it sleeps. */
const API = "http://127.0.0.1:8787";

/* ---- captures survive the admin being off ------------------------------

   A capture used to be lost if the admin was not running, which is the
   failure most likely to kill the habit: you click through a conference's
   exhibitor list on a train, and none of it lands. Now an unreachable admin
   queues the capture in the worker's own storage and it goes the moment the
   admin answers again.

   ONLY A CONNECTION FAILURE QUEUES. An admin that answered and REFUSED is a
   different fact - the capture was seen and rejected, and re-sending it
   forever would be a loop, not a retry. Those come back to the panel to be
   read by a person. Chrome throws a TypeError whose message is "Failed to
   fetch" for a closed port, which is what this recognises.

   The queue is capped. A cap that silently drops the OLDEST would lose the
   captures you made first, so it drops nothing and simply refuses to grow
   past the cap - a full queue is a signal to start the admin, not a reason
   to throw away work. */
const QUEUE = "pending_captures";
const QUEUE_MAX = 200;
/* A capture the admin SAW AND REFUSED is not retried - it is kept here with
   the admin's own words, so it is neither re-sent forever nor silently gone.
   The panel reports the count; the body stays so a person can look. */
const REFUSED = "refused_captures";
const REFUSED_MAX = 50;

/* One at a time. enqueue() and drain() both read-modify-write the same key,
   and a capture arriving while a drain is mid-flight was either posted twice
   or overwritten out of the queue. A promise chain serialises them. */
let chain = Promise.resolve();
const serial = (fn) => {
  const run = chain.then(fn, fn);
  chain = run.catch(() => {});
  return run;
};

function flash(text, colour) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color: colour });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2600);
}

const unreachable = (e) =>
  /failed to fetch|networkerror|load failed|network error/i
    .test(String((e && e.message) || e));

async function queued() {
  const got = await chrome.storage.local.get(QUEUE);
  return got[QUEUE] || [];
}

function enqueue(body) {
  return serial(async () => {
    const q = await queued();
    if (q.length >= QUEUE_MAX) return { held: q.length, full: true };
    q.push({ body, at: Date.now() });
    await chrome.storage.local.set({ [QUEUE]: q });
    return { held: q.length, full: false };
  });
}

async function refuse(item, r) {
  const got = await chrome.storage.local.get(REFUSED);
  const list = got[REFUSED] || [];
  list.push({ body: item.body, at: item.at, status: r.status,
              error: (r.data && r.data.error) || ("HTTP " + r.status),
              refused_at: Date.now() });
  while (list.length > REFUSED_MAX) list.shift();
  await chrome.storage.local.set({ [REFUSED]: list });
}

/* Send what is waiting. Stops at the first connection failure rather than
   working through the whole queue against a port that is still shut.

   THREE OUTCOMES, NOT TWO. Sent: gone from the queue. Unreachable: kept, and
   everything behind it kept too. REFUSED - the admin answered 4xx or 5xx -
   moved to the refused list with the reason. The first version put a refusal
   back on the queue, so a capture for a company merged away since the click
   was re-sent on every flush, forever, and the person saw nothing. */
function drain() {
  return serial(async () => {
    const q = await queued();
    if (!q.length) return { sent: 0, refused: 0 };
    const left = [];
    let sent = 0, refused = 0;
    for (let i = 0; i < q.length; i++) {
      try {
        let r = await call({ path: "/api/capture", body: q[i].body }, false);
        if (r.status === 403) r = await call({ path: "/api/capture", body: q[i].body }, true);
        if (r.status < 400) { sent++; continue; }
        await refuse(q[i], r);
        refused++;
      } catch (e) {
        left.push(q[i]);
        if (unreachable(e)) { for (let j = i + 1; j < q.length; j++) left.push(q[j]); break; }
      }
    }
    await chrome.storage.local.set({ [QUEUE]: left });
    if (refused) flash(String(refused), "#a3342a");
    return { sent, refused };
  });
}

let auth = null;                     // {header, token} for this worker's life

async function getAuth(fresh) {
  if (auth && !fresh) return auth;
  const r = await fetch(API + "/api/token", { cache: "no-store" });
  if (!r.ok) throw new Error("the admin would not hand over a token (" + r.status + ")");
  const d = await r.json();
  if (!d || !d.token || !d.header) throw new Error("the admin sent no token");
  auth = { header: d.header, token: d.token };
  return auth;
}

async function call(msg, fresh) {
  const a = await getAuth(fresh);
  const headers = { "Content-Type": "application/json" };
  headers[a.header] = a.token;
  const r = await fetch(API + msg.path, {
    method: msg.body ? "POST" : "GET",
    headers,
    body: msg.body ? JSON.stringify(msg.body) : undefined,
  });
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

/* The reply shape capture.js reads:
     {ok:true,  status, data}   the admin answered and did not refuse
     {ok:false, status, data}   it answered and refused; data.error says why
     {ok:false, error}          we never reached it at all
   The three are kept apart because "the admin is not running" and "the admin
   said no" want different words on screen, and the panel used to show the
   first for both. */
chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg.kind !== "api") return;
  (async () => {
    try {
      let r = await call(msg, false);
      if (r.status === 403) r = await call(msg, true);
      /* The admin answered, so anything waiting can go now. Only captures
         queue, so a search that happens to be the first call after the admin
         starts is what flushes the backlog - which is fine, and means the
         panel is usually already open when it lands. */
      const d = r.status < 400 ? await drain() : { sent: 0, refused: 0 };
      respond({ ok: r.status < 400, status: r.status, data: r.data,
                flushed: d.sent, refused: d.refused });
    } catch (e) {
      /* Unreachable and a capture: keep it rather than lose it. Anything
         else - a refusal, a bad token, a search we could not run - is
         reported as it always was. */
      if (unreachable(e) && msg.path === "/api/capture" && msg.body) {
        const q = await enqueue(msg.body);
        respond(q.full
          ? { ok: false, queued: false, held: q.held,
              error: `The admin is not running, and ${q.held} captures are `
                   + `already waiting - start it before capturing more.` }
          : { ok: false, queued: true, held: q.held,
              error: `The admin is not running, so this is being held `
                   + `(${q.held} waiting). Start it and they will send: `
                   + `python3 scripts/admin.py` });
        return;
      }
      /* Not a capture, and the admin is off: say that in words. Chrome's
         own "Failed to fetch" told the person nothing about what to do. */
      if (unreachable(e)) {
        respond({ ok: false, error: "The admin is not running. Start it in "
                  + "the repo - python3 scripts/admin.py - then click again." });
        return;
      }
      respond({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true;                       // keep the channel open for the async reply
});

/* A worker wakes on click, so this is the earliest chance to flush without
   asking the person to do anything. It fails quietly when the admin is still
   off, which is the normal case. */
chrome.runtime.onStartup.addListener(() => { drain().catch(() => {}); });
chrome.runtime.onInstalled.addListener(() => { drain().catch(() => {}); });

/* ---- pages Chrome will not let anything be injected into ---------------
 *
 * chrome://, the Web Store, devtools, view-source and the new-tab page are
 * off limits to every extension, by design. Clicking there used to call
 * executeScript anyway and throw "Cannot access a chrome:// URL" as an
 * UNHANDLED PROMISE REJECTION - nothing on screen, nothing on the button, and
 * the only trace an error in a console the person would have to know to open.
 *
 * A tool that does nothing and says nothing when you press it is a tool you
 * stop trusting on the page where it matters. So the button answers: a short
 * red badge that says it cannot work here, and then clears itself.
 *
 * The .catch() below is the belt to that braces. Chrome can also refuse for
 * reasons the URL does not show - a page mid-navigation, a PDF viewer, a
 * policy-blocked host - and those must not become silent rejections either. */
const NO_INJECT = new RegExp(
  "^(chrome|chrome-extension|chrome-untrusted|devtools|view-source|about|edge|"
  + "moz-extension|file):|"
  + "^https?://(chrome\\.google\\.com/webstore|chromewebstore\\.google\\.com)", "i");

chrome.action.onClicked.addListener((tab) => {
  if (!tab || !tab.id || NO_INJECT.test(tab.url || "")) {
    flash("n/a", "#a3342a");
    return;
  }
  chrome.scripting
    .executeScript({ target: { tabId: tab.id }, files: ["capture.js"] })
    .catch((e) => {
      flash("!", "#a3342a");
      console.warn("SLED JOBS capture could not run here:", e && e.message);
    });
});
