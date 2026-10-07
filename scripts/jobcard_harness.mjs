/* RUN THE REAL JOB CARD, not a copy of it.
 *
 * The board's list became cards on 2026-09-27. Everything a card says comes
 * out of jobCardHTML in index.html's inline script, and nothing in selftest
 * executed that script - the suite read index.html as text, which is how an
 * approval screen once shipped with every write dead (the admin's CALL
 * ReferenceError, five weeks). So this loads the page's own script into a
 * node vm with a DOM that absorbs everything, sets the board data the card
 * reads, and calls jobCardHTML on fixture groups. selftest.py does the
 * asserting; this prints one JSON object.
 *
 * The stub DOM is a Proxy that answers every property and every call with
 * itself, so the page's top-level wiring (listeners, the boot fetch) runs to
 * the end without doing anything. fetch never resolves, so boot waits
 * forever and harmlessly. */
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
      if (k === "then") return undefined;          // never a thenable
      if (k === "length") return 0;
      return p;
    },
    set() { return true; },
    apply() { return p; },
    construct() { return p; },
    has() { return true; },
  });
  return p;
}
const S = stub();
const storage = { getItem: () => null, setItem() {}, removeItem() {}, clear() {}, key: () => null, length: 0 };
const ctx = {
  console: { log() {}, warn() {}, error() {}, info() {} },
  document: S, window: undefined, navigator: S, location: { search: "", pathname: "/", hash: "", href: "http://x/", origin: "http://x", hostname: "x" },
  history: S, localStorage: storage, sessionStorage: storage,
  fetch: () => new Promise(() => {}), setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0,
  clearInterval() {}, requestAnimationFrame: () => 0, queueMicrotask() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  addEventListener() {}, removeEventListener() {}, getComputedStyle: () => S,
  IntersectionObserver: function () { return S; }, ResizeObserver: function () { return S; },
  MutationObserver: function () { return S; }, CustomEvent: function () { return S; },
  URL, URLSearchParams, Intl, Date, Math, JSON, Promise, Map, Set, WeakMap, Symbol, Array, Object, String, Number, RegExp, Error, encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN,
};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
const errors = [];
scripts.forEach((src, i) => {
  try { vm.runInContext(src, ctx, { filename: `index.html#script${i}` }); }
  catch (e) { errors.push(`script ${i}: ${e && e.message}`); }
});

const post = (over) => Object.assign({
  id: "acme::Account Executive", title: "Account Executive", company: "Acme Civic",
  company_id: "acme-civic", family: "gtm", quota_carrying: true, work_mode: "not stated",
  office: null, territory: null, location: "", first_seen: "2026-09-20", posted: null,
  comp: null, jd_seen: true, opening_id: "acme::ae", source: "ats",
}, over);
const cases = {
  plain: { lead: post({}), rows: [post({})] },
  paid: { lead: post({ comp: { min: 90000, max: 120000, period: "year", currency: "USD" }, work_mode: "remote", location: "Remote" }), rows: null },
  office: { lead: post({ office: { city: "Austin", state: "TX" }, work_mode: "hybrid", posted: "2026-09-18" }), rows: null },
  territory: { lead: post({ territory: { stated: true, states: ["TX", "OK", "KS", "MO"], region: null } }), rows: null },
  unread: { lead: post({ jd_seen: false }), rows: null },
  unrecorded: (() => { const p = post({}); delete p.comp; return { lead: p, rows: [p] }; })(),
  notquota: { lead: post({ quota_carrying: false, family: "cs" }), rows: null },
  hostile: { lead: post({ title: `<img src=x onerror=alert(1)> "AE" &amp;`, company: `<b>Evil</b> & Co`, location: `<script>x</script>`, comp: null }), rows: null },
  group_same: (() => {
    const a = post({ id: "g::1", location: "Denver, CO", office: { city: "Denver", state: "CO" }, comp: { min: 80000, max: 100000, period: "year", currency: "USD" }, work_mode: "onsite" });
    const b = post({ id: "g::2", location: "Tulsa, OK", office: { city: "Tulsa", state: "OK" }, comp: { min: 80000, max: 100000, period: "year", currency: "USD" }, work_mode: "onsite" });
    return { lead: a, rows: [a, b] };
  })(),
  group_diff: (() => {
    const a = post({ id: "h::1", office: { city: "Denver", state: "CO" }, comp: { min: 80000, max: 100000, period: "year", currency: "USD" }, work_mode: "remote" });
    const b = post({ id: "h::2", office: { city: "Tulsa", state: "OK" }, comp: { min: 70000, max: 90000, period: "year", currency: "USD" }, work_mode: "onsite" });
    return { lead: a, rows: [a, b] };
  })(),
  nostate: { lead: post({ office: { city: "Itasca", state: null }, location: "Itasca" }), rows: null },
  group_noplace: (() => {
    const rs = [1, 2, 3].map(i => post({ id: `n::${i}`, location: "", office: null }));
    return { lead: rs[0], rows: rs };
  })(),
  group_samecity: (() => {
    const rs = [1, 2].map(i => post({ id: `c::${i}`, office: { city: "Itasca", state: "IL" }, posted: i === 1 ? "2026-09-01" : "2026-09-10" }));
    return { lead: rs[0], rows: rs };
  })(),
  group_partpay: (() => {
    const a = post({ id: "q::1", office: { city: "Denver", state: "CO" }, comp: { min: 80000, max: 100000, period: "year", currency: "USD" } });
    const b = post({ id: "q::2", office: { city: "Tulsa", state: "OK" }, comp: null, jd_seen: true });
    return { lead: a, rows: [a, b] };
  })(),
  group_mixedsilence: (() => {
    const a = post({ id: "m::1", office: { city: "Denver", state: "CO" }, comp: null, jd_seen: true });
    const b = post({ id: "m::2", office: { city: "Tulsa", state: "OK" }, comp: null, jd_seen: false });
    return { lead: a, rows: [a, b] };
  })(),
  remote_bare: { lead: post({ work_mode: "remote", location: "Remote" }), rows: null },
  workday_count: { lead: post({ location: "3 Locations" }), rows: null },
  group_workday: (() => {
    const a = post({ id: "w::1", location: "30 Locations" });
    const b = post({ id: "w::2", location: "2 Locations" });
    return { lead: a, rows: [a, b] };
  })(),
  group_modehalf: (() => {
    const a = post({ id: "r::1", work_mode: "remote", location: "Remote" });
    const b = post({ id: "r::2", work_mode: "not stated", location: "Georgia" });
    return { lead: a, rows: [a, b] };
  })(),
  group_halfdated: (() => {
    const a = post({ id: "d::1", office: { city: "Austin", state: "TX" }, posted: "2026-09-01" });
    const b = post({ id: "d::2", office: { city: "Dallas", state: "TX" }, posted: "2026-09-10" });
    const c = post({ id: "d::3", office: { city: "Waco", state: "TX" }, posted: null });
    return { lead: a, rows: [a, b, c] };
  })(),
  group_stale: (() => {
    const a = post({ id: "s::1", first_seen: "2026-09-27", office: { city: "Austin", state: "TX" }, posted: "2026-02-27" });
    const b = post({ id: "s::2", first_seen: "2026-09-27", office: { city: "Dallas", state: "TX" }, posted: "2026-09-01" });
    return { lead: a, rows: [a, b] };
  })(),
  stale_today: { lead: post({ first_seen: "2026-09-27", posted: "2026-03-13" }), rows: null },
  group_later: (() => {
    const a = post({ id: "l::1", first_seen: "2026-08-25", office: { city: "Austin", state: "TX" } });
    const b = post({ id: "l::2", first_seen: "2026-09-25", office: { city: "Dallas", state: "TX" } });
    return { lead: a, rows: [a, b] };
  })(),
};
Object.values(cases).forEach(c => { if (!c.rows) c.rows = [c.lead]; });

let out = {};
try {
  vm.runInContext(`D = {generated: "2026-09-27", logos: {}, postings: []};`, ctx);
  ctx.__cases = cases;
  out = vm.runInContext(`(() => { const r = {}; for (const [k, g] of Object.entries(__cases)) {
      try { r[k] = jobCardHTML(g); } catch (e) { r[k] = "THREW: " + e.message; } }
    return r; })()`, ctx);
} catch (e) {
  errors.push(`calling jobCardHTML: ${e && e.message}`);
}
/* THE SAME QUESTION IN BOTH LANGUAGES. What a 0 means on a company is
   decided by boardState() here and board_state() in build_site.py, and they
   once disagreed with the truth together (2026-10-06: 862 companies with no
   board printed "0 open roles"). Every organization on the committed board is
   run through the page's own functions so selftest can hold the two. */
let boards = null;
try {
  const b = JSON.parse(readFileSync(new URL("../data/board.json", import.meta.url), "utf8"));
  ctx.__orgs = b.organizations || [];
  boards = vm.runInContext(`(() => { const r = {};
      for (const o of __orgs) r[o.id] = [boardState(o), openCount(o), scopeNote(o)];
      return r; })()`, ctx);
} catch (e) {
  errors.push(`calling boardState/openCount: ${e && e.message}`);
}
/* THE APP'S COMPANY VIEW, RUN. boardState() and openCount() agreeing with
   build_site proved nothing about co(), which could still print `${open}` and
   "read every night" while every check stayed green (review, 2026-10-07).
   co() writes one string to $("#view").innerHTML, so #view is a recorder and
   everything else stays the absorbing stub. */
let views = null;
try {
  const base = { sector: "Public Safety", category: "Police", description: "x",
                 open_roles: 0, quota_roles: 0, website: "https://f.example",
                 board_url: "https://f.example/careers", ats: "html" };
  const orgs = [
    { ...base, id: "read-co", name: "Read Co", ats: "greenhouse", enumerable: true },
    { ...base, id: "unread-co", name: "Unread Co", enumerable: false },
    { ...base, id: "unreadable-co", name: "Unreadable Co", ats: "lever", enumerable: true, unreadable: "404" },
    { ...base, id: "none-co", name: "None Co", ats: "unknown", enumerable: true,
      no_board_on_file: true, probe: "none-found", board_url: "https://f.example" },
    { ...base, id: "blocked-co", name: "Blocked Co", ats: "unknown", enumerable: true,
      no_board_on_file: true, probe: "blocked", board_url: "https://f.example" },
    { ...base, id: "unprobed-co", name: "Unprobed Co", ats: "unknown", enumerable: true,
      no_board_on_file: true, probe: null, board_url: "https://f.example" },
    { ...base, id: "scoped-co", name: "Scoped Co", ats: "icims", enumerable: true,
      offtopic_dropped: 52, federal_dropped: 2 },
    { ...base, id: "rival-co", name: "Rival Co", ats: "greenhouse", enumerable: true,
      competitors: [{ id: "unread-co", why: "a" }, { id: "none-co", why: "b" }, { id: "read-co", why: "c" }] },
  ];
  ctx.__fix = { generated: "2026-10-07", logos: {}, postings: [], organizations: orgs, conferences: [] };
  const rec = { innerHTML: "" };
  const viewEl = new Proxy(rec, { get(t, k) { return k in t ? t[k] : S; },
                                  set(t, k, v) { t[k] = v; return true; } });
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "querySelector") return (sel) => (sel === "#view" ? viewEl : S);
    if (k === "getElementById") return (id) => (id === "view" ? viewEl : S);
    return S;
  } });
  views = {};
  for (const o of orgs) {
    rec.innerHTML = "";
    try {
      vm.runInContext(`D = __fix; (typeof DETAIL === "object" && DETAIL) && (DETAIL[${JSON.stringify(o.id)}] = {}); co(${JSON.stringify(o.id)}, true);`, ctx);
      views[o.id] = String(rec.innerHTML);
    } catch (e) { views[o.id] = "THREW: " + (e && e.message); }
  }
} catch (e) {
  errors.push(`running co(): ${e && e.message}`);
}
console.log(JSON.stringify({ errors, cards: out, boards, views }));
