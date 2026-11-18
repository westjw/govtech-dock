/* RUN THE BOARD'S OWN SAVED-ROLE SYNC, not a copy of it (2026-11-18).
 *
 * index.html's inline script loads into a node vm the way jobcard_harness.mjs
 * does (a DOM that absorbs everything), but with a localStorage that keeps
 * what it is given and a fetch this file answers, so accountSync, mergeRemote
 * and syncSoon run as the page runs them. selftest asserts on the JSON this
 * prints (check_saved_roles_follow_the_sign_in).
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const scripts = [...html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .filter(m => !/\bsrc=/.test(m[1] || "") && !/type=["']?application\/(ld\+)?json/.test(m[1] || ""))
  .map(m => m[2]);

function stub() {
  const fn = function () { return p; };
  const p = new Proxy(fn, {
    get(_t, k) {
      if (k === Symbol.toPrimitive) return () => "";
      if (k === Symbol.iterator) return function* () {};
      if (k === "then") return undefined;
      if (k === "length") return 0;
      return p;
    },
    set() { return true; }, apply() { return p; }, construct() { return p; }, has() { return true; },
  });
  return p;
}
const S = stub();
const store = new Map();
const storage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
  clear: () => store.clear(), key: () => null, get length() { return store.size; },
};
/* fetch: boot fetches (the board data, whoami) never resolve, as in
   jobcard_harness; /api/saved and /api/alerts answer from SERVER. */
const calls = [];
let SERVER = { status: 501, body: { error: "not_configured" } };
let HOLD = null;          // a promise /api/saved waits on, to put a save mid-flight
const fakeFetch = (url, init) => {
  const u = String(url);
  if (u.startsWith("/api/saved") || u.startsWith("/api/alerts")) {
    calls.push({ url: u, method: (init && init.method) || "GET", body: init && init.body ? JSON.parse(init.body) : null });
    const answer = async () => {
      if (HOLD && u.startsWith("/api/saved")) await HOLD;
      const s = typeof SERVER === "function" ? SERVER(u, init) : SERVER;
      return { ok: s.status >= 200 && s.status < 300, status: s.status, json: async () => s.body };
    };
    return answer();
  }
  return new Promise(() => {});
};
const timers = [];
let COOKIE = "";
const doc = new Proxy(S, { get(_t, k) { return k === "cookie" ? COOKIE : S[k]; } });
const ctx = {
  console: { log() {}, warn() {}, error() {}, info() {} },
  document: doc, window: undefined, navigator: S,
  location: { search: "", pathname: "/", hash: "", href: "https://sledjobs.com/", origin: "https://sledjobs.com", hostname: "sledjobs.com" },
  history: S, localStorage: storage, sessionStorage: storage,
  fetch: fakeFetch,
  setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout() {},
  setInterval: () => 0, clearInterval() {}, requestAnimationFrame: () => 0, queueMicrotask() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  addEventListener() {}, removeEventListener() {}, getComputedStyle: () => S,
  IntersectionObserver: function () { return S; }, ResizeObserver: function () { return S; },
  MutationObserver: function () { return S; }, CustomEvent: function () { return S; },
  TextEncoder, TextDecoder, btoa, atob,
  URL, URLSearchParams, Intl, Date, Math, JSON, Promise, Map, Set, WeakMap, Symbol, Array, Object, String, Number, RegExp, Error, encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN,
};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
const loadErrors = [];
scripts.forEach((src, i) => {
  try { vm.runInContext(src, ctx, { filename: `index.html#script${i}` }); }
  catch (e) { loadErrors.push(`script ${i}: ${e && e.message}`); }
});
const run = (code) => vm.runInContext(code, ctx);
const SAVED = run("SAVED_KEY"), TOMBS = run("TOMB_KEY");
const local = () => Object.keys(JSON.parse(store.get(SAVED) || "{}")).sort();
const tombs = () => JSON.parse(store.get(TOMBS) || "{}");
const role = (id, at) => ({ id, title: "AE " + id, company: "Acme", company_id: "acme", saved_on: at.slice(0, 10), saved_at: at });
const reset = (saved, gone) => {
  store.clear(); calls.length = 0; timers.length = 0; HOLD = null;
  store.set(SAVED, JSON.stringify(Object.fromEntries(saved.map((s) => [s.id, s]))));
  store.set(TOMBS, JSON.stringify(gone || {}));
  run('ACCT_SYNC="unknown"; ACCT_CLEAN=false; ACCT_TIMER=null; JOURNAL.clear(); SKEW=0');
};
const K = (name) => run(name);
const ok = (extra) => ({ status: 200, body: { ok: true, owner: "mine-tag-0001", now: new Date().toISOString(), saved: [], removed: {}, ...extra } });
const flush = async () => { while (timers.length) { const t = timers.shift(); await t(); } };
const out = { load_errors: loadErrors };

// 1. no database bound (501): the sign-in's list is off, the alert sync keeps its job
reset([role("a", "2026-11-18T10:00:00.000Z")]);
SERVER = { status: 501, body: { error: "not_configured" } };
let on = await run("accountSync()");
out.off = { returned: on, state: run("ACCT_SYNC"), local: local() };
run("syncSoon()");                         // no alert token on this browser: nothing goes anywhere
await flush();
out.off_save_calls = calls.filter((c) => c.method === "POST").length - 1;   // minus accountSync's own

// 2. the server holds a role this browser has not seen, and has unsaved one this browser holds
reset([role("a", "2026-11-18T10:00:00.000Z"), role("b", "2026-11-18T10:00:00.000Z")]);
SERVER = { status: 200, body: { ok: true, saved: [role("a", "2026-11-18T10:00:00.000Z"), role("c", "2026-11-18T09:00:00.000Z")],
                                removed: { b: "2026-11-18T11:00:00.000Z" } } };
on = await run("accountSync()");
out.on = { returned: on, state: run("ACCT_SYNC"), local: local(), sent: calls[0] && calls[0].body
  ? { saved: calls[0].body.saved.map((s) => s.id).sort(), removed: Object.keys(calls[0].body.removed || {}) } : null };

// 3. once on, a save goes up to the sign-in's list (after the debounce), not to the alert sync
calls.length = 0;
run('ACCT_SYNC="on"');
run("syncSoon()");
await flush();
out.on_save_goes_to = calls.map((c) => c.url.split("?")[0]);

// 4. this browser unsaved a role the server still holds as saved earlier: it stays unsaved
reset([], { d: "2026-11-18T12:00:00.000Z" });
SERVER = { status: 200, body: { ok: true, saved: [role("d", "2026-11-18T08:00:00.000Z")], removed: {} } };
await run("accountSync()");
out.local_unsave_wins = local();

// 5. a save made while the load-time sync is in flight is kept AND sent: the
// bookmark goes through noteChange exactly as toggleSave sends it
reset([role("a", "2026-11-18T10:00:00.000Z")]);
let release; HOLD = new Promise((r) => { release = r; });
// answers the way the real one does: what it holds, plus what it was sent
SERVER = (u, init) => ok({ saved: [role("a", "2026-11-18T10:00:00.000Z"),
  ...JSON.parse(init.body).saved.filter((x) => x.id !== "a")] });
const flying = run("accountSync()");
const all = JSON.parse(store.get(SAVED));
all.e = role("e", "2026-11-18T13:00:00.000Z");
store.set(SAVED, JSON.stringify(all));
run('noteChange("e")');                   // the bookmark pressed mid-flight
release(); await flying;
out.mid_flight_kept = local();
await flush();
const posts = calls.filter((c) => c.url === "/api/saved" && c.method === "POST");
out.mid_flight_sent = { posts: posts.length, second_has_e: !!(posts[1] && posts[1].body.saved.some((x) => x.id === "e")) };
out.mid_flight_clean_after = run("ACCT_CLEAN");

// 6. the server is down (500): the list stays here, the state says so, and the alert sync is not handed it
reset([role("a", "2026-11-18T10:00:00.000Z")]);
SERVER = { status: 500, body: { error: "boom" } };
on = await run("accountSync()");
out.error = { returned: on, state: run("ACCT_SYNC"), local: local() };

// 6b. the server says this copy is somebody else's: the account's list replaces it whole
reset([role("x", "2026-11-18T10:00:00.000Z"), role("y", "2026-11-18T10:00:00.000Z")]);
store.set(run("OWNER_KEY"), "someone-else-tag");
SERVER = { status: 200, body: { ok: true, owner: "mine-tag", replace: true, saved: [role("m", "2026-11-18T09:00:00.000Z")], removed: { q: "2026-11-18T08:00:00.000Z" } } };
await run("accountSync()");
out.replaced = { local: local(), tombs: Object.keys(tombs()), owner: store.get(run("OWNER_KEY")),
  sent_owner: calls[0] && calls[0].body && calls[0].body.owner, flag: store.get(run("ACCT_FLAG")),
  foreign_aside: store.has(SAVED + "@someone-else-tag") };
// a 501 clears the alerts page's flag, so it stops saying the sign-in carries the list
reset([]); store.set(run("ACCT_FLAG"), "1");
SERVER = { status: 501, body: { error: "not_configured" } };
await run("accountSync()");
out.flag_cleared_when_off = !store.has(run("ACCT_FLAG"));

// 6c. WHOSE COPY: a copy tagged A with the cookie naming B is set aside at
// load (never deleted), and B's own set-aside copy comes back
reset([role("aa", "2026-11-18T10:00:00.000Z")], { ab: "2026-11-18T10:30:00.000Z" });
store.set(K("OWNER_KEY"), "tagA-0000000001"); store.set(K("APPLIED_KEY"), JSON.stringify({ aa: "2026-11-11" }));
store.set(SAVED + "@tagB-0000000002", JSON.stringify({ bb: role("bb", "2026-11-18T09:00:00.000Z") }));
COOKIE = "x=1; __Host-sj_owner=tagB-0000000002; y=2";
run("claimAtLoad()");
out.claim_b = { local: local(), applied: store.has(K("APPLIED_KEY")), owner: store.get(K("OWNER_KEY")) || null,
  a_aside: store.has(SAVED + "@tagA-0000000001") && store.has(K("APPLIED_KEY") + "@tagA-0000000001"),
  waiting: run("WAITING_FOR") };
COOKIE = "";

// 6c2. NO COOKIE TO SAY (a cached page, the first load after a deploy): a
// tagged copy is set aside until the answer, and saves made meanwhile are
// tagged PENDING; the answer names its owner, so it comes back - with what
// this page saved on top - and goes up
reset([role("own", "2026-11-18T10:00:00.000Z")]);
store.set(K("OWNER_KEY"), "mine-tag-0001");
run("claimAtLoad()");
out.waiting_hides = { local: local(), waiting: run("WAITING_FOR"), owner: store.get(K("OWNER_KEY")) || null };
const fresh = JSON.parse(store.get(SAVED) || "{}"); fresh.meanwhile = role("meanwhile", "2026-11-18T15:00:00.000Z");
store.set(SAVED, JSON.stringify(fresh)); run('noteChange("meanwhile")'); timers.length = 0;
SERVER = ok({ saved: [role("own", "2026-11-18T10:00:00.000Z")] });
await run("accountSync()");
out.waiting_back = { local: local(), owner: store.get(K("OWNER_KEY")) || null, waiting: run("WAITING_FOR"),
  no_pending_left: ![...store.keys()].some((k) => k.endsWith("@pending")) };
await flush();
const up = calls.filter((c) => c.url === "/api/saved").map((c) => c.body);
out.waiting_back_sent = !!(up[1] && up[1].saved.some((x) => x.id === "meanwhile") && up[1].saved.some((x) => x.id === "own"));

// 6c3. the same, but the answer names somebody else: the set-aside copy
// stays aside, and only this page's own save is on the board
reset([role("theirs", "2026-11-18T10:00:00.000Z")]);
store.set(K("OWNER_KEY"), "tagA-0000000001");
run("claimAtLoad()");
const fresh2 = JSON.parse(store.get(SAVED) || "{}"); fresh2.mine = role("mine", "2026-11-18T15:00:00.000Z");
store.set(SAVED, JSON.stringify(fresh2)); run('noteChange("mine")'); timers.length = 0;
SERVER = ok({ owner: "tagB-0000000002", saved: [] });
await run("accountSync()");
out.waiting_other = { local: local(), owner: store.get(K("OWNER_KEY")) || null,
  a_still_aside: store.has(SAVED + "@tagA-0000000001") };

// 6c4. SIGN OUT SETS NOTHING ASIDE: it sends what is not yet up, and the same
// person signing in again finds everything where it was - applied marks,
// searches, the alert token, a save the server had not taken (third review)
reset([role("s1", "2026-11-18T10:00:00.000Z"), role("s2", "2026-11-18T11:00:00.000Z")]);
store.set(K("OWNER_KEY"), "mine-tag-0001"); store.set(K("APPLIED_KEY"), JSON.stringify({ s1: "2026-11-12" }));
store.set(K("SEARCH_KEY"), JSON.stringify([{ qs: "?q=ae", name: "AE" }])); store.set(run("SYNC_KEY"), "tok-mine");
run('ACCT_SYNC="on"; ACCT_CLEAN=false');
SERVER = (u, init) => ok({ saved: JSON.parse(init.body).saved });
const loc = run("location"); loc.href = "https://sledjobs.com/";
let prevented = false;
await run("signOutHere")({ preventDefault() { prevented = true; }, currentTarget: { href: "https://sledjobs.com/signin/out" } });
out.signout = { prevented, posts: calls.filter((c) => c.url === "/api/saved").length, went: loc.href,
  in_view: local(), owner: store.get(K("OWNER_KEY")) || null };
COOKIE = "__Host-sj_owner=mine-tag-0001";
run("claimAtLoad()");
out.signout_back = { local: local(), applied: JSON.parse(store.get(K("APPLIED_KEY")) || "null"),
  searches: (JSON.parse(store.get(K("SEARCH_KEY")) || "[]")).length, token: store.get(run("SYNC_KEY")) || null };
COOKIE = "";
// nothing to send: Sign out is a plain link
calls.length = 0; run("ACCT_CLEAN=true; ACCT_TIMER=null"); prevented = false;
await run("signOutHere")({ preventDefault() { prevented = true; }, currentTarget: { href: "/signin/out" } });
out.signout_clean = { prevented, posts: calls.length };

// 6c5. THREE PEOPLE IN TURN on one browser: each comes back to their own,
// and a second set-aside MERGES with the first instead of replacing it
reset([role("a1", "2026-11-18T10:00:00.000Z")]);
store.set(K("OWNER_KEY"), "tagA-0000000001"); store.set(K("APPLIED_KEY"), JSON.stringify({ a1: "2026-11-10" }));
const load = (tag) => { COOKIE = `__Host-sj_owner=${tag}`; run("claimAtLoad()"); };
load("tagB-0000000002");
store.set(SAVED, JSON.stringify({ b1: role("b1", "2026-11-18T12:00:00.000Z") }));
load("tagA-0000000001");                            // A is back: a1 and its mark
const aBack = { local: local(), applied: JSON.parse(store.get(K("APPLIED_KEY")) || "null") };
store.set(K("APPLIED_KEY"), JSON.stringify({ ...JSON.parse(store.get(K("APPLIED_KEY"))), a2: "2026-11-18" }));
load("tagC-0000000003");
load("tagB-0000000002");                            // B is back: b1
const bBack = local();
load("tagA-0000000001");                            // A again: a1, and both marks
out.three_people = { a_back: aBack, b_back: bBack, a_again: local(),
  a_marks: Object.keys(JSON.parse(store.get(K("APPLIED_KEY")) || "{}")).sort() };
COOKIE = "";

// 6c7. THE ANSWER ALONE brings a set-aside copy back: no cookie, nothing in
// view, A's copy aside (saves, a mark, the alert token) - the answer names A
reset([]);
store.set(SAVED + "@tagA-0000000001", JSON.stringify({ a7: role("a7", "2026-11-18T10:00:00.000Z") }));
store.set(K("APPLIED_KEY") + "@tagA-0000000001", JSON.stringify({ a7: "2026-11-14" }));
store.set(run("SYNC_KEY") + "@tagA-0000000001", "tok-A");
COOKIE = "";
run("claimAtLoad()");
calls.length = 0; timers.length = 0;
SERVER = (u, init) => ok({ owner: "tagA-0000000001", saved: [] });
await run("accountSync()");
out.answer_restores = { local: local(), applied: JSON.parse(store.get(K("APPLIED_KEY")) || "null"),
  token: store.get(run("SYNC_KEY")) || null, aside_left: [...store.keys()].some((k) => k.includes("@tagA-")) };
await flush();
out.answer_restores_sends = calls.filter((c) => c.url === "/api/saved").some((c) => c.body.saved.some((x) => x.id === "a7"));

// 6c8. a second set-aside under the same tag MERGES with the first: saves by
// the later-wins rule, applied marks and searches both kept
reset([role("x2", "2026-11-18T12:00:00.000Z")]);
store.set(K("OWNER_KEY"), "tagA-0000000001");
store.set(K("APPLIED_KEY"), JSON.stringify({ b: "2026-11-18" }));
store.set(K("SEARCH_KEY"), JSON.stringify([{ qs: "?q=two" }]));
store.set(SAVED + "@tagA-0000000001", JSON.stringify({ x1: role("x1", "2026-11-17T12:00:00.000Z") }));
store.set(K("APPLIED_KEY") + "@tagA-0000000001", JSON.stringify({ a: "2026-11-10" }));
store.set(K("SEARCH_KEY") + "@tagA-0000000001", JSON.stringify([{ qs: "?q=one" }]));
run('park("tagA-0000000001")');
out.park_merges = { saved: Object.keys(JSON.parse(store.get(SAVED + "@tagA-0000000001") || "{}")).sort(),
  applied: Object.keys(JSON.parse(store.get(K("APPLIED_KEY") + "@tagA-0000000001") || "{}")).sort(),
  searches: (JSON.parse(store.get(K("SEARCH_KEY") + "@tagA-0000000001") || "[]")).length, view_empty: !store.has(SAVED) };

// 6c6. a save the server never took, set aside when somebody else signed in,
// goes up when its owner is back
reset([role("r9", "2026-11-18T13:00:00.000Z")]);
store.set(K("OWNER_KEY"), "mine-tag-0001");
load("tagB-0000000002");
load("mine-tag-0001");
calls.length = 0; timers.length = 0;
SERVER = (u, init) => ok({ saved: [] });
await run("accountSync()");
await flush();
out.parked_save_goes_up = calls.filter((c) => c.url === "/api/saved").some((c) => c.body.saved.some((x) => x.id === "r9"));
COOKIE = "";

// 6d3. CLEAN only when the server holds every role this browser does
reset([role("k1", "2026-11-18T10:00:00.000Z"), role("k2", "2026-11-18T10:00:00.000Z")]);
SERVER = ok({ saved: [role("k1", "2026-11-18T10:00:00.000Z")] });
await run("accountSync()");
out.clean_needs_all = run("ACCT_CLEAN");

// 6d4. a run of 409s backs off instead of retrying for ever
reset([]);
run('ACCT_SYNC="on"');
SERVER = { status: 409, body: { error: "busy" } };
let scheduled = 0;
for (let i = 0; i < 6; i++) { timers.length = 0; await run("accountSync()"); scheduled += timers.length; }
out.conflict_retries = scheduled;
run("CONFLICTS=0");

// 6e. the server says this copy is somebody else's, while this page saved j:
// the copy is set aside under its tag, the account's list comes in, and j is
// put back on top and goes up
reset([role("x", "2026-11-18T10:00:00.000Z")]);
store.set(K("OWNER_KEY"), "someone-else-tag");
run('ACCT_SYNC="on"');
const withJ = JSON.parse(store.get(SAVED)); withJ.j = role("j", "2026-11-18T14:00:00.000Z"); store.set(SAVED, JSON.stringify(withJ));
run('noteChange("j")');
timers.length = 0;                          // the debounced send: driven below instead
SERVER = (u) => calls.length <= 1 ? ok({ replace: true, saved: [role("m", "2026-11-18T09:00:00.000Z")] }) : ok({ saved: [role("m", "2026-11-18T09:00:00.000Z"), role("j", "2026-11-18T14:00:00.000Z")] });
await run("accountSync()");
out.replace_keeps_journal = local();
out.replace_sets_aside = store.has(SAVED + "@someone-else-tag");
await flush();
const sentJ = calls.filter((c) => c.url === "/api/saved").map((c) => c.body);
out.replace_sends_journal = !!(sentJ[1] && sentJ[1].owner === "mine-tag-0001" && sentJ[1].saved.some((x) => x.id === "j"));

// 6f. a subscription's copy is folded in once, and only into its own account
reset([role("a", "2026-11-18T10:00:00.000Z")]);
store.set(run("SYNC_KEY"), "tok-123"); store.set(K("OWNER_KEY"), "mine-tag-0001");
run('ACCT_SYNC="on"; ACCT_CLEAN=true');
SERVER = (u) => u.startsWith("/api/alerts") ? { status: 200, body: { ok: true, owner_tag: "someone-else-tag", saved: [role("s8", "2026-11-17T10:00:00.000Z")], removed: {} } } : ok({});
await run("foldSubscription()");
out.fold_foreign = local();
SERVER = (u) => u.startsWith("/api/alerts") ? { status: 200, body: { ok: true, owner_tag: "mine-tag-0001", saved: [role("s9", "2026-11-17T10:00:00.000Z")], removed: {} } } : ok({});
await run("foldSubscription()");
out.fold = { local: local(), alerts_writes: calls.filter((c) => c.url.startsWith("/api/alerts") && c.method !== "GET").length };
await flush();
out.fold_sends = calls.some((c) => c.url === "/api/saved" && c.body && c.body.saved.some((x) => x.id === "s9"));
const gets = calls.filter((c) => c.url.startsWith("/api/alerts")).length;
await run("foldSubscription()");
out.fold_once = calls.filter((c) => c.url.startsWith("/api/alerts")).length === gets;

// 6g. the server's clock: a browser two hours slow stamps on the server's time once told it
reset([]);
const twoHours = 2 * 3600 * 1000;
SERVER = { status: 200, body: { ok: true, owner: "mine-tag-0001", now: new Date(Date.now() + twoHours).toISOString(), saved: [], removed: {} } };
await run("accountSync()");
out.clock_skew_hours = Math.round((Date.parse(run("nowISO()")) - Date.now()) / 3600000);
run("SKEW=0");

// 6h. the day's writes spent (429): it says so, not "the server is down"
reset([]);
SERVER = { status: 429, body: { error: "busy_today" } };
await run("accountSync()");
out.busy = { state: run("ACCT_SYNC"), reason: run("ACCT_REASON") };

// 7. what the account menu and the Saved tab say when it is on
run('ACCT_SYNC="on"');
out.menu_text = run("acctText()");

console.log(JSON.stringify(out));
