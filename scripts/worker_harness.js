/* Runs extension/background.js under node with a fake chrome, and reports
   what the queue did. Read by selftest.check_extension_holds_and_refuses. */
const fs = require("fs");
const store = {};
let listener = null; let fetchImpl = null; const badges = [];
global.chrome = {
  storage: { local: { get: async (k) => ({ [k]: store[k] }),
                      set: async (o) => { Object.assign(store, o); } } },
  runtime: { onMessage: { addListener: (fn) => { listener = fn; } },
             onStartup: { addListener() {} }, onInstalled: { addListener() {} } },
  action: { setBadgeText: (o) => badges.push(o.text), setBadgeBackgroundColor() {},
            onClicked: { addListener() {} } },
  scripting: { executeScript: async () => {} },
};
global.fetch = (...a) => fetchImpl(...a);
global.setTimeout = () => 0;
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
if (!listener) { console.log(JSON.stringify({ crash: "no onMessage listener" })); process.exit(0); }
const send = (msg) => new Promise((res) => listener(msg, {}, res));
const resp = (status, body) => ({ ok: status < 400, status, json: async () => body });
const token = (url) => url.endsWith("/api/token");
(async () => {
  const out = {};
  fetchImpl = async () => { throw new TypeError("Failed to fetch"); };
  let r = await send({ kind: "api", path: "/api/capture", body: { company_id: "gone", jobs: [{ title: "AE" }] } });
  out.held = { queued: r.queued, pending: (store.pending_captures || []).length, error: r.error };
  r = await send({ kind: "api", path: "/api/search-companies", body: { q: "x" } });
  out.off_search = r.error;
  let captures = 0;
  fetchImpl = async (url) => {
    if (token(url)) return resp(200, { token: "t", header: "X-Admin-Token" });
    if (url.endsWith("/api/capture")) { captures++; return resp(400, { error: "no such company" }); }
    return resp(200, { hits: [] });
  };
  r = await send({ kind: "api", path: "/api/search-companies", body: { q: "x" } });
  out.refused = { refused: r.refused, pending: (store.pending_captures || []).length,
                  set_aside: (store.refused_captures || []).length,
                  why: ((store.refused_captures || [])[0] || {}).error, captures, badge: badges.slice() };
  r = await send({ kind: "api", path: "/api/search-companies", body: { q: "y" } });
  out.again = { captures, refused: r.refused };
  fetchImpl = async () => { throw new TypeError("Failed to fetch"); };
  await send({ kind: "api", path: "/api/capture", body: { company_id: "a", jobs: [{ title: "1" }] } });
  await send({ kind: "api", path: "/api/capture", body: { company_id: "b", jobs: [{ title: "2" }] } });
  out.kept = (store.pending_captures || []).map((x) => x.body.company_id);
  fetchImpl = async (url) => token(url) ? resp(200, { token: "t", header: "X-Admin-Token" }) : resp(200, { ok: true });
  r = await send({ kind: "api", path: "/api/search-companies", body: { q: "z" } });
  out.sent = { flushed: r.flushed, pending: (store.pending_captures || []).length };
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crash: String((e && e.stack) || e) })); });
