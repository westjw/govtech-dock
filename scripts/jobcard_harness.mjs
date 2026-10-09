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
/* The document records how the page registers its listeners and absorbs
   everything else, so selftest can hold the action dispatcher to capture. */
const listeners = [], listenerFns = [];
const docRec = new Proxy(S, { get(_t, k) {
  if (k === "addEventListener") return (type, fn, opt) => {
    listeners.push([String(type), opt === true || !!(opt && opt.capture)]);
    listenerFns.push([String(type), fn]); };
  return S[k];
} });
const storage = { getItem: () => null, setItem() {}, removeItem() {}, clear() {}, key: () => null, length: 0 };
const ctx = {
  console: { log() {}, warn() {}, error() {}, info() {} },
  document: docRec, window: undefined, navigator: S, location: { search: "", pathname: "/", hash: "", href: "http://x/", origin: "http://x", hostname: "x" },
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
  // found by hand: the card says nothing re-reads it; a group says so only
  // when every posting in it was
  by_hand: { lead: post({ source: "manual" }), rows: null },
  group_halfhand: (() => {
    const a = post({ id: "h::1", source: "manual", office: { city: "Austin", state: "TX" } });
    const b = post({ id: "h::2", office: { city: "Dallas", state: "TX" } });
    return { lead: a, rows: [a, b] };
  })(),
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
/* The credibility line over the list: "links checked today" must say which
   postings it does not cover. */
let fresh = null;
try {
  fresh = vm.runInContext(`(() => { const keep = D.postings;
      D.postings = [{source: "manual"}, {source: "ats"}, {source: "manual"}];
      const a = freshness(D.generated);
      D.postings = [{source: "ats"}];
      const b = freshness(D.generated);
      D.postings = keep; return [a, b]; })()`, ctx);
} catch (e) {
  errors.push(`calling freshness: ${e && e.message}`);
}
/* A company whose newest news is over two years old says so in the heading. */
let staleNews = null;
try {
  staleNews = vm.runInContext(`(() => {
      const det = n => ({ news: [{ date: n, headline: "x", kind: "press", url: "https://a.test/x" }] });
      const o = { news_state: "items", news_checked_on: "2026-09-20" };
      return [coNews(o, "a.test", det("2023-05-02")), coNews(o, "a.test", det("2026-08-01")),
              coNews({ news_state: "hosts_news", news_by: "Bloomerang", news_checked_on: "2026-09-20" },
                     "bloomerang.com", { news: [] })]; })()`, ctx);
} catch (e) {
  errors.push(`calling coNews: ${e && e.message}`);
}
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
    { ...base, id: "quote-co", name: "Quote Co", ats: "greenhouse", enumerable: true, open_roles: 1 },
    // a parent named by its NAME, one nobody on the board answers to, and
    // brands with and without a live record of their own
    { ...base, id: "child-co", name: "Child Co", parent: "Read Co" },
    { ...base, id: "orphan-co", name: "Orphan Co", parent: "Nobody Holdings" },
    { ...base, id: "brand-co", name: "Brand Co", brands: [{ name: "Brand A" }, { name: "Brand B", record: "read-co" }] },
    // ONE OPENING in two cities that state different pay, and one more
    { ...base, id: "multi-co", name: "Multi Co", ats: "greenhouse", enumerable: true, open_roles: 2, quota_roles: 2 },
  ];
  // A title with an apostrophe, a double quote and a backslash: what a job
  // board hands us, and what an onclick="openRole('...')" could never carry.
  const QUOTE_ID = "quote-co::D\u00e9veloppement d'affaires \"AE\" \\ x::1a2b";
  const qp = post({ id: QUOTE_ID, title: "D\u00e9veloppement d'affaires \"AE\" \\ x", company: "Quote Co",
                    company_id: "quote-co", opening_id: "quote-co::q" });
  const mp = (i, city, min) => post({ id: `multi-co::AE::${i}`, title: "Account Executive", company: "Multi Co",
    company_id: "multi-co", opening_id: "multi-co::AE", location: `${city}, TX`, office: { city, state: "TX" },
    comp: { min, max: min + 20000, period: "year", currency: "USD" } });
  const multi = [mp(1, "Austin", 90000), mp(2, "Dallas", 100000),
    post({ id: "multi-co::SE::1", title: "Sales Engineer", company: "Multi Co", company_id: "multi-co",
           opening_id: "multi-co::SE" })];
  ctx.__fix = { generated: "2026-10-07", logos: {}, postings: [qp, ...multi], organizations: orgs, conferences: [] };
  ctx.__quoteId = QUOTE_ID;
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
  // "Show N more" shows the rest of the group, and "Show fewer" puts it back
  // (it redrew the same three roles from 2026-09 to 10-09)
  ctx.__fix.postings = Array.from({ length: 5 }, (_, i) => post({ id: `quote-co::r${i}`,
    title: `Role ${i}`, company: "Quote Co", company_id: "quote-co", opening_id: `quote-co::o${i}` }));
  const grab = (js) => { rec.innerHTML = ""; vm.runInContext(js, ctx); return String(rec.innerHTML); };
  views.__more_before = grab(`CO_OPEN_GROUPS = null; D = __fix; co("quote-co", true);`);
  views.__more_after = grab(`coShowAll("gtm");`);
  views.__more_back = grab(`coShowAll("gtm");`);
  // and an opened group does not follow the reader to the next company
  ctx.__fix.postings = ctx.__fix.postings.concat(Array.from({ length: 5 }, (_, i) => post({
    id: `read-co::r${i}`, title: `Role ${i}`, company: "Read Co", company_id: "read-co",
    opening_id: `read-co::o${i}` })));
  grab(`co("quote-co", true); coShowAll("gtm");`);
  views.__more_next_co = grab(`co("read-co", true);`);
  // Back: popstate sets CO from the url BEFORE it calls co(CO, true)
  grab(`co("read-co", true); coShowAll("gtm");`);
  views.__more_back_button = grab(`CO = "quote-co"; co(CO, true);`);
} catch (e) {
  errors.push(`running co(): ${e && e.message}`);
}
/* THE FRONT PAGE ON THE REAL BOARD: buildSlides() and home(), so every number
   a click turns into the jobs tab can be held to that tab's default set. */
let front = null;
try {
  ctx.__real = JSON.parse(readFileSync(new URL("../data/board.json", import.meta.url), "utf8"));
  front = vm.runInContext(`(() => { D = __real;
      const slides = buildSlides().map((x) => ({ kick: x.kick, h: String(x.h), p: String(x.p) }));
      return { slides }; })()`, ctx);
  const rec2 = { innerHTML: "" };
  const v2 = new Proxy(rec2, { get(t, k) { return k in t ? t[k] : S; },
                               set(t, k, v) { t[k] = v; return true; } });
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "querySelector") return (sel) => (sel === "#view" ? v2 : S);
    if (k === "getElementById") return (id) => (id === "view" ? v2 : S);
    return S;
  } });
  // a returning visitor last here three days before the crawl, so the
  // "since you were last here" line is drawn too
  const g = new Date(Date.parse(ctx.__real.generated) - 3 * 86400000).toISOString().slice(0, 10);
  front.lastVisit = g;
  vm.runInContext(`D = __real; LAST_VISIT = ${JSON.stringify(g)}; home();`, ctx);
  front.home = String(rec2.innerHTML);
} catch (e) {
  errors.push(`running the front page: ${e && e.message}`);
}
/* A VISITOR'S OWN VOTE AGAINST A STALE EDGE COPY: loadRatings() run for real
   with a fetch answering n=2, a stored fresh row of n=3, and the paint and
   note functions recording what they were handed. */
let ratings = null;
try {
  ratings = {};
  const store = new Map();
  ctx.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null),
                       setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "querySelectorAll") return (sel) => (sel === "[data-rate]" ? [{ dataset: { rate: "X 2026" } }] : []);
    if (k === "querySelector" || k === "getElementById") return () => S;
    return S;
  } });
  let serverN = 2;
  ctx.fetch = async () => ({ ok: true, json: async () => ({ ok: true,
    ratings: [{ tag: "X 2026", n: serverN, min_shown: 3, average: null, needs: 3 - serverN }] }) });
  vm.runInContext(`__painted = []; __noted = [];
    paintRating = (b, d) => { if (d) __painted.push(d.n); };
    cfRatingNote = (rows) => { __noted.push(rows.map((r) => r.n)); };`, ctx);
  const runOnce = async (label, fresh, age) => {
    store.set("sled-rated-fresh", JSON.stringify(fresh ? { "X 2026": { row: { tag: "X 2026", n: fresh }, at: Date.now() - age } } : {}));
    vm.runInContext(`__painted = []; __noted = [];`, ctx);
    await vm.runInContext(`loadRatings()`, ctx);
    ratings[label] = { painted: vm.runInContext(`__painted.slice()`, ctx), noted: vm.runInContext(`__noted.slice()`, ctx) };
  };
  await runOnce("freshWins", 3, 1000);
  await runOnce("freshExpired", 3, 11 * 60 * 1000);
  serverN = 4;
  await runOnce("serverCaughtUp", 3, 1000);
} catch (e) {
  errors.push(`running loadRatings: ${e && e.message}`);
}
/* NO CODE IN MARKUP: the action dispatcher and the logo listener, run for
   real. Every control names an action in data-click/-input/-change and the
   value it needs in data-arg; dispatch() is the one place that turns that
   into a call. The actions are stubbed in the page's own scope (they are
   globals) and handed fake events. */
let acts = null;
try {
  ctx.__listenerFns = listenerFns;
  ctx.__qid = vm.runInContext("typeof __quoteId === 'string' ? __quoteId : ''", ctx) || "q::it's \"x\" \\ y::1";
  acts = vm.runInContext(`(() => {
    const got = [], keep = { openRole, alertOnCompany, coShowAll, lightMark, toggleQuota };
    openRole = (id) => got.push(["openRole", id]);
    alertOnCompany = (id) => got.push(["alertOnCompany", id]);
    coShowAll = (f) => got.push(["coShowAll", f]);
    toggleQuota = (b) => got.push(["toggleQuota", b && b.tagName]);
    lightMark = (img) => got.push(["lightMark", img && img.tagName]);
    const el = (tag, ds, attrs) => ({ tagName: tag, dataset: ds, removed: false,
      hasAttribute: (n) => Object.prototype.hasOwnProperty.call(attrs || {}, n),
      remove() { this.removed = true; } });
    const ev = (kind, target, type) => ({ type: type || kind, target: target === null ? null : {
        closest: (sel) => (target && sel === "[data-" + kind + "]" ? target : null) },
      prevented: false, preventDefault() { this.prevented = true; } });
    const r = {};
    let e = ev("click", el("A", { click: "openRole", arg: __qid }));
    dispatch("click", e); r.openRole = { got: got.splice(0), prevented: e.prevented };
    e = ev("click", el("BUTTON", { click: "alertOnCompany", arg: "acme-civic" }));
    dispatch("click", e); r.alert = { got: got.splice(0), prevented: e.prevented };
    e = ev("click", el("A", { click: "coShowAll", arg: "gtm" }));
    dispatch("click", e); r.coShowAll = { got: got.splice(0), prevented: e.prevented };
    e = ev("click", el("BUTTON", { click: "toggleQuota" }));
    dispatch("click", e); r.toggle = { got: got.splice(0) };
    const odd = {};
    for (const name of ["constructor", "toString", "__proto__", "hasOwnProperty", "nope"]) {
      e = ev("click", el("A", { click: name }));
      try { dispatch("click", e); odd["k_" + name] = { got: got.splice(0), prevented: e.prevented }; }
      catch (x) { odd["k_" + name] = { threw: String(x && x.message) }; }
    }
    r.odd = odd;
    e = ev("click", null); dispatch("click", e); r.noTarget = got.splice(0);
    e = ev("input", el("INPUT", { click: "openRole", arg: "x" }));
    dispatch("input", e); r.wrongKind = got.splice(0);
    const img = el("IMG", {}, { "data-mark": "" }), plain = el("IMG", {}, {});
    logoEvent({ type: "load", target: img }); r.loadMarked = got.splice(0);
    logoEvent({ type: "load", target: plain }); r.loadPlain = got.splice(0);
    logoEvent({ type: "error", target: img }); r.errorMarked = img.removed;
    logoEvent({ type: "error", target: plain }); r.errorPlain = plain.removed;
    r.actKeys = Object.keys(ACTS);
    r.frozen = Object.isFrozen(ACTS);
    // THE LISTENERS THE PAGE REGISTERED, FIRED: a listener wired to the wrong
    // kind or to nothing does nothing here, as it would in a browser
    const fire = (type, ev) => { for (const [t, fn] of __listenerFns) if (t === type) fn(ev); };
    const keep2 = { nearChanged, famPill };
    nearChanged = () => got.push(["nearChanged"]);
    famPill = () => got.push(["famPill"]);
    fire("click", ev("click", el("A", { click: "openRole", arg: "via-listener" })));
    fire("input", ev("input", el("INPUT", { input: "nearChanged" })));
    fire("change", ev("change", el("SELECT", { change: "famPill" })));
    fire("load", { type: "load", target: el("IMG", {}, { "data-mark": "" }) });
    const errImg = el("IMG", {}, { "data-mark": "" });
    fire("error", { type: "error", target: errImg });
    r.fired = { got: got.splice(0), errorRemoved: errImg.removed };
    // ENTER ON A ROW, SPACE ON A BUTTON: the keydown listener the page
    // registered, fired with focusable rows of each kind
    const kd = (tag, role, key, tabIndex = 0) => { let clicked = 0;
      const t = { tagName: tag, tabIndex, getAttribute: (n) => (n === "role" ? role : null), click() { clicked++; } };
      fire("keydown", { type: "keydown", key, target: t, preventDefault() {} }); return clicked; };
    r.kbd = { enterLink: kd("DIV", "link", "Enter"), spaceLink: kd("DIV", "link", " "),
              spaceButton: kd("SPAN", "button", " "), enterButton: kd("SPAN", "button", "Enter"),
              realButton: kd("BUTTON", "button", "Enter"), notFocusable: kd("DIV", "link", "Enter", -1),
              noRole: kd("DIV", null, "Enter") };
    Object.assign(globalThis, keep, keep2);
    return r; })()`, ctx);
  acts.qid = ctx.__qid;
  acts.listeners = listeners;
} catch (e) {
  errors.push(`driving dispatch(): ${e && e.message}`);
}
console.log(JSON.stringify({ errors, cards: out, boards, views, front, ratings, fresh, staleNews, acts }));
