/* The whole reason this is an extension and not a bookmarklet: a page on https
   cannot reach http://127.0.0.1 (Chrome blocks fetch and script tags both,
   verified the hard way), but an extension's service worker with a host
   permission can. The content script asks; this fetches; the admin answers.

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
      respond({ ok: r.status < 400, status: r.status, data: r.data });
    } catch (e) {
      respond({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true;                       // keep the channel open for the async reply
});

chrome.action.onClicked.addListener((tab) => {
  chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["capture.js"] });
});
