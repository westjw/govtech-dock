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
  TextEncoder, TextDecoder, btoa, atob,
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
  /* WHERE "CLAIM THIS PAGE" GOES, and the SLED HQ mark (2026-10-22): the
     same company with board.json's claim_at off and on, verified and not.
     selftest holds each against build_site's static page for that company. */
  const claimOrg = { ...base, id: "claim&co", name: "Claim Co", website: "https://claim.example" };
  for (const [k, extra, verified] of [
    ["off", { claim_at: null }, null],
    ["on", { claim_at: "https://hq.sledjobs.com/claim?company=", hq_post_at: "https://hq.sledjobs.com/recruit" }, null],
    ["verified_on", { claim_at: "https://hq.sledjobs.com/claim?company=", hq_post_at: "https://hq.sledjobs.com/recruit" }, "2026-10-02"],
    ["verified_off", { claim_at: null, hq_post_at: "https://hq.sledjobs.com/recruit" }, "2026-10-02"]]) {
    ctx.__claimFix = { ...ctx.__fix, ...extra, postings: [],
                       organizations: [verified ? { ...claimOrg, hq_verified: verified } : claimOrg] };
    views[`__claim_${k}`] = grab(`D = __claimFix; (typeof DETAIL === "object" && DETAIL) && (DETAIL["claim&co"] = {}); co("claim&co", true);`);
  }
} catch (e) {
  errors.push(`running co(): ${e && e.message}`);
}
/* THE FRONT PAGE ON THE REAL BOARD: home(), so every number a click turns
   into the jobs tab can be held to that tab's default set. `words` is the
   page's own numWords() over 0..120, so the headline's spelling is checked
   against an independent table. */
let front = null;
try {
  ctx.__real = JSON.parse(readFileSync(new URL("../data/board.json", import.meta.url), "utf8"));
  front = vm.runInContext(`(() => { D = __real;
      return { words: Array.from({ length: 121 }, (_, i) => numWords(i)) }; })()`, ctx);
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
  /* A QUIET RUN: the same board read again a day later, so nothing was first
     seen on the crawl. The headline must drop its "new" claim, not print a
     zero. */
  const day = new Date(Date.parse(ctx.__real.generated) + 86400000).toISOString().slice(0, 10);
  rec2.innerHTML = "";
  vm.runInContext(`D = Object.assign({}, __real, { generated: ${JSON.stringify(day)} }); LAST_VISIT = ""; home();`, ctx);
  front.quiet = String(rec2.innerHTML);
  /* A SELLER DEBUT: the company of the first role new this run marked as one,
     so the debut line has something true to say. */
  const deb = vm.runInContext(`(() => { const p = boardDefault(__real.postings).find((x) =>
      x.first_seen === __real.generated && !boardDefault(__real.postings).some((y) =>
        y.opening_id === x.opening_id && (y.first_seen || "") < __real.generated));
    if (!p) return null;
    D = Object.assign({}, __real, { organizations: __real.organizations.map((o) =>
      o.id === p.company_id ? Object.assign({}, o, { seller_debut: true }) : o) });
    LAST_VISIT = ""; home(); return p.company; })()`, ctx);
  front.debut = { name: deb, home: String(rec2.innerHTML) };
  /* THREE BOARDS TODAY'S DATA CANNOT SHOW, each made from the real one:
     - few: one role new this run (the rest re-dated a day earlier), so the
       cards top up with older roles and the section is no longer all new;
     - later: no conference on today, so the floor leads with what is next
       and must leave past editions out;
     - unread: the company hiring the most sellers on a board we cannot
       read, which Hiring hardest must leave out as the spotlight does. */
  const variant = (code) => { rec2.innerHTML = ""; const r = vm.runInContext(`(() => { ${code} })()`, ctx);
    return Object.assign({ home: String(rec2.innerHTML) }, r || {}); };
  front.few = variant(`const gen = __real.generated, q = boardDefault(__real.postings);
    const keep = q.find((x) => x.first_seen === gen && !q.some((y) => y.opening_id === x.opening_id && (y.first_seen || "") < gen));
    if (!keep) return null;
    const before = new Date(Date.parse(gen) - 86400000).toISOString().slice(0, 10);
    D = Object.assign({}, __real, { postings: __real.postings.map((p) =>
      p.first_seen === gen && p.opening_id !== keep.opening_id ? Object.assign({}, p, { first_seen: before }) : p) });
    LAST_VISIT = ""; home(); return { keep: keep.opening_id };`);
  front.later = variant(`const conferences = (__real.conferences || []).filter((r) => !isNow(r.dates));
    D = Object.assign({}, __real, { conferences }); LAST_VISIT = ""; home();
    return { tags: conferences.map((r) => r.tag) };`);
  front.unread = variant(`const top = __real.organizations.filter((o) => (o.quota_roles || 0) > 0 && boardState(o) === "read")
      .sort((a, b) => b.quota_roles - a.quota_roles)[0];
    if (!top) return null;
    D = Object.assign({}, __real, { organizations: __real.organizations.map((o) =>
      o.id === top.id ? Object.assign({}, o, { enumerable: false }) : o) });
    LAST_VISIT = ""; home(); return { hidden: top.id };`);
  /* THE DOORS, PRESSED. Every link the front page drew is handed to home()'s
     own wiring as an element carrying its attributes, then clicked plainly
     and with a modifier; goTab, openRole, co, cfOpenPanel and scrollTo
     record what they were asked. The search box is typed into and sent both
     ways. A door that draws right and goes somewhere else fails here. */
  const unesc = (v) => v.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#92;/g, "\\")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  const anchors = [...front.home.matchAll(/<a\s([^>]*)>/g)].map((m) => {
    const at = {};
    m[1].replace(/([\w-]+)(?:="([^"]*)")?/g, (_, k, v) => { at[k] = v === undefined ? "" : unesc(v); return ""; });
    const dataset = {};
    for (const [k, v] of Object.entries(at)) if (k.startsWith("data-"))
      dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    return { at, dataset, getAttribute: (k) => (k in at ? at[k] : null), onclick: null, onkeydown: null };
  });
  const box = { value: "", onkeydown: null }, goBtn = { onclick: null };
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "querySelectorAll") return (sel) => {
      const m = /^#view a\[([\w-]+)\]$/.exec(sel);
      return m ? anchors.filter((a) => m[1] in a.at) : [];
    };
    if (k === "querySelector") return (sel) => (sel === "#view" ? v2 : sel === "#h-q" ? box : sel === "#h-go" ? goBtn : S);
    if (k === "getElementById") return (id) => (id === "view" ? v2 : S);
    return S;
  } });
  const calls = [];
  ctx.__calls = calls;
  ctx.scrollTo = (x, y) => calls.push(["scrollTo", x, y]);
  rec2.innerHTML = "";
  vm.runInContext(`(() => { globalThis.__keep = { goTab, openRole, co, cfOpenPanel };
    goTab = (t, u) => __calls.push(["goTab", t, u ? String(u) : null]);
    openRole = (id) => __calls.push(["openRole", id]);
    co = (id) => __calls.push(["co", id]);
    cfOpenPanel = (t, f) => __calls.push(["cfOpenPanel", t, !!f]);
    D = __real; LAST_VISIT = ""; home(); })()`, ctx);
  const press = (fn, ev) => { calls.length = 0; let prevented = false;
    fn(Object.assign({ button: 0, preventDefault() { prevented = true; } }, ev));
    return { calls: calls.slice(), prevented }; };
  const doors = anchors.map((a) => ({ href: a.at.href || "", data: a.dataset, wired: !!a.onclick,
    plain: a.onclick ? press(a.onclick, {}) : null,
    modified: a.onclick ? [press(a.onclick, { metaKey: true }), press(a.onclick, { ctrlKey: true }),
                           press(a.onclick, { shiftKey: true }), press(a.onclick, { button: 1 })] : null }));
  box.value = "Ver kada & co";
  const search = { click: goBtn.onclick ? press(() => goBtn.onclick(), {}) : null,
                   enter: box.onkeydown ? press((e) => box.onkeydown(Object.assign(e, { key: "Enter" })), {}) : null,
                   other: box.onkeydown ? press((e) => box.onkeydown(Object.assign(e, { key: "a" })), {}) : null };
  front.doors = { doors, search };
  /* THE DESK'S DAYS, PRESSED through ACTS as dispatch() would: each pick
     must press that button alone and move the alert link to its day; a
     value that is not one of the three must change nothing. */
  const deskBtns = [...front.home.matchAll(/data-click="deskCadence" data-arg="(\w+)"\s+aria-pressed="(\w+)"/g)]
    .map((m) => { const at = { "aria-pressed": m[2] };
      return { dataset: { arg: m[1] }, setAttribute: (k, v) => { at[k] = String(v); },
               getAttribute: (k) => (k in at ? at[k] : null) }; });
  const alertAt = { href: (/id="h-alert" href="([^"]*)"/.exec(front.home) || [])[1] || "" };
  const alertEl = { setAttribute: (k, v) => { alertAt[k] = String(v); }, getAttribute: (k) => alertAt[k] ?? null };
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "querySelectorAll") return (sel) => (sel === ".fp-cad button" ? deskBtns : []);
    if (k === "querySelector") return (sel) => (sel === "#view" ? v2 : sel === "#h-alert" ? alertEl : S);
    if (k === "getElementById") return (id) => (id === "view" ? v2 : id === "h-alert" ? alertEl : S);
    return S;
  } });
  const deskState = () => ({ href: alertAt.href,
    pressed: deskBtns.filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.dataset.arg) });
  front.desk = { start: deskState(), picks: [] };
  for (const v of [...deskBtns.map((b) => b.dataset.arg), "hourly"]) {
    vm.runInContext(`ACTS.deskCadence({ dataset: { arg: ${JSON.stringify(v)} } })`, ctx);
    front.desk.picks.push(Object.assign({ arg: v }, deskState()));
  }
  vm.runInContext(`(() => { ({ goTab, openRole, co, cfOpenPanel } = __keep); })()`, ctx);
  delete ctx.scrollTo;
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "querySelector") return (sel) => (sel === "#view" ? v2 : S);
    if (k === "getElementById") return (id) => (id === "view" ? v2 : S);
    return S;
  } });
  // the Market intel and How tabs, on the real board
  for (const [k, fn] of [["intel", "intel"], ["how", "how"]]) {
    try { rec2.innerHTML = ""; vm.runInContext(`D = __real; ${fn}();`, ctx); front[k] = String(rec2.innerHTML); }
    catch (e) { errors.push(`running ${fn}(): ${e && e.message}`); }
  }
} catch (e) {
  errors.push(`running the front page: ${e && e.message}`);
}
/* THE JOB HUNTER PANEL: the page's own jhPanelHTML on Job Hunter's answers
   (each silence, a role it does not hold, roles it does in every state its
   board can be in, and one whose every string is written to be markup), so
   selftest can hold what it prints. */
try {
  front = front || {};
  front.jhpanel = vm.runInContext(`(() => {
    const X = "<x-jh>";
    const role = (o) => jhPanelHTML(Object.assign({ found: true, id: "sig 1", org: "Acme Gov",
      title: "Account Executive", status: "open", fit: null, band: null,
      gates: { fails: 0, checks: 0, first_fail: null }, application: null,
      tailored: false, resume_sent: false, flagged_on: null }, o));
    return {
      app: jhApp(),
      // Job Hunter's silences, as openJhPanel names them
      signin: jhPanelHTML({ failed: "signin" }),
      unreachable: jhPanelHTML({ failed: "unreachable" }),
      no_account: jhPanelHTML({ failed: "no_account" }),
      status: jhPanelHTML({ failed: 503 }),
      none: jhPanelHTML(null),
      missing: jhPanelHTML({ found: false }),
      // a role with everything: a failing gate, a score the person rejected,
      // an interview (crm.LABEL's own word), a resume sent, a flag, closed
      found: role({ status: "closed", fit: { as_is: 71, ceiling: 88, review: "rejected" },
        band: "strong", gates: { fails: 1, checks: 0, first_fail: "base under the floor" },
        application: { status: "interview", label: "interviewing", applied_on: "2026-10-14" },
        tailored: true, resume_sent: true, flagged_on: "2026-10-20" }),
      applied: role({ fit: { as_is: 64, ceiling: 80, review: "accepted" },
        application: { status: "applied", label: "applied", applied_on: "2026-10-14" },
        tailored: true }),
      pending: role({ fit: { as_is: 50, ceiling: 70, review: "pending" } }),
      // Job Hunter holds a score with no ceiling (an accepted 54)
      noCeiling: role({ fit: { as_is: 54, ceiling: null, review: "accepted" } }),
      unscored: role({ band: "possible", status: "unknown" }),
      // every string Job Hunter sends, written to be markup
      hostile: role({ id: '"><x-jh>/?#&', title: X, org: X, status: X,
        fit: { as_is: X, ceiling: X, review: "accepted" },
        gates: { fails: 1, checks: 0, first_fail: X },
        application: { status: "screen", label: X, applied_on: X }, flagged_on: X }),
      hostile_failed: jhPanelHTML({ failed: X }),
    };
  })()`, ctx);
} catch (e) {
  errors.push(`running jhPanelHTML: ${e && e.message}`);
}
/* AND THE REPLY BEFORE IT: the page's own jhAnswer on the replies a fetch
   can hand it, so the silence each one becomes is held, not assumed. */
try {
  front.jhpanel.answers = await vm.runInContext(`(async () => {
    const reply = (o) => Object.assign({ type: "cors", ok: false, status: 200,
      json: async () => { throw new Error("no body"); } }, o);
    const body = (x) => async () => x;
    return {
      redirect: await jhAnswer(reply({ type: "opaqueredirect", status: 0 })),
      no_account: await jhAnswer(reply({ status: 401, json: body({ error: "no_account" }) })),
      no_token: await jhAnswer(reply({ status: 401, json: body({ error: "no_token" }) })),
      forbidden: await jhAnswer(reply({ status: 403 })),
      down: await jhAnswer(reply({ status: 503, json: body({ error: "busy" }) })),
      found: await jhAnswer(reply({ ok: true, json: body({ found: true, id: "s" }) })),
    };
  })()`, ctx);
} catch (e) {
  errors.push(`running jhAnswer: ${e && e.message}`);
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
// THE ROLE PAGE'S PAY SENTENCE, on postings of every kind a company holds:
// read and silent, never read, a hand capture, hourly, CAD, and yearly USD
let paySentence = null;
try {
  paySentence = vm.runInContext(`(() => {
    const yr = (min, max, cur) => ({ min, max, currency: cur, period: "year" });
    const P = (comp, extra) => Object.assign({ id: "x", comp }, extra);
    const mixed = [
      P(yr(90000, 120000, "USD"), { jd_seen: true }),
      P(yr(80026, 110009, "CAD"), { jd_seen: true }),
      P({ min: 24.5, max: 24.5, currency: "USD", period: "hour" }, { jd_seen: true }),
      P(null, { jd_seen: true }), P(null, { jd_seen: true }),
      P(null, { jd_seen: false }), P(null, { jd_seen: false }), P(null, { jd_seen: false }),
      P(null, { jd_seen: false, source: "manual" }),
    ];
    const allUsd = [P(yr(60000, 70000, "USD"), { jd_seen: true }),
                    P(yr(80000, 95000, null), { jd_seen: true })];
    // which tab an incoming address opens, as boot and popstate read it
    const t = (q) => tabFromUrl(new URLSearchParams(q), "home");
    return { mixed: coPaySentence(mixed), allUsd: coPaySentence(allUsd),
             none: coPaySentence([P(null, { jd_seen: true })]),
             tabs: { e: t("e=APCO%202026"), list: t("csec=Public%20Safety&call=1"),
                     board: t("us=us"), plain: t("utm_source=x"), co: t("co=x") } };
  })()`, ctx);
} catch (e) {
  errors.push(`driving coPaySentence(): ${e && e.message}`);
}
/* THE COMPANIES TAB'S CARD, on every real company (2026-10-19: the list
   became cards). coCard builds an <a> and sets its innerHTML, so
   createElement hands back a recorder and everything else stays the
   absorbing stub. selftest holds each card's count corner to the honesty
   order the list row kept. */
let coCards = null;
try {
  const made = [];
  const rec = () => {
    const t = { innerHTML: "", className: "", href: "" };
    made.push(t);
    return new Proxy(t, { get(o, k) { return k in o ? o[k] : S; },
                          set(o, k, v) { o[k] = v; return true; } });
  };
  ctx.document = new Proxy(S, { get(_t, k) {
    if (k === "createElement") return () => rec();
    return S[k];
  } });
  ctx.__real2 = JSON.parse(readFileSync(new URL("../data/board.json", import.meta.url), "utf8"));
  coCards = vm.runInContext(`(() => { D = __real2; const r = {};
      for (const o of D.organizations) {
        const a = coCard(o, "", "", true);
        r[o.id] = { html: String(a.innerHTML), cls: String(a.className), href: String(a.href) };
      }
      // a record carrying only the legacy single \`conference\` field
      const l = coCard({ id: "legacy-co", name: "Legacy Co", sector: "General Gov",
        category: "Finance & ERP", conference: "GFOA 2026", open_roles: 0, quota_roles: 0,
        no_board_on_file: true, probe: "none-found" }, "", "", true);
      r.__legacy = { html: String(l.innerHTML) };
      return r; })()`, ctx);
} catch (e) {
  errors.push(`running coCard(): ${e && e.message}`);
}
/* FLAG FOR JOB HUNTER: what the board puts in the link, decoded by
   selftest. A title with an accent, a quote and an apostrophe, because the
   payload is UTF-8 JSON inside base64url and must come back exactly. */
let flags = null;
try {
  ctx.__fp = post({ id: "zed::D\u00e9veloppement d'affaires \"AE\"::9", title: "D\u00e9veloppement d'affaires \"AE\"",
                    company: "Zed Civic", url: "https://zed.example/jobs/9?src=x", location: "Austin, TX" });
  ctx.__fo = post({ id: "o::1", title: "AE", company: "Office Co", url: "", location: "",
                    office: { city: "Denver", state: "CO" } });
  flags = vm.runInContext(`({ full: jhFlagHref(__fp), office: jhFlagHref(__fo) })`, ctx);
} catch (e) {
  errors.push(`running jhFlagHref(): ${e && e.message}`);
}
/* FOR EXHIBITORS: the page's own cfFactsHTML on a conference whose facts
   carry a hostile link, a passed deadline, one ahead and markup in a figure. */
let factsPanel = null;
try {
  ctx.__facts = { tag: "X 2027", facts: { edition: "2027", read_on: "2026-10-27",
    costs: [{ what: "Booth <b>10x10</b>", amount: "$2,950", source: "https://x.test/prospectus" }],
    links: [{ kind: "prospectus", url: "https://x.test/p.pdf" }, { kind: "booth", url: "javascript:alert(1)" },
            { kind: "nonsense", url: "https://x.test/n" }],
    deadlines: [{ what: "Early-bird ends", date: "2001-01-01", source: "https://x.test/r" },
                { what: "Exhibitor deadline", date: "2099-05-01", source: "https://x.test/e" }],
    audience: { attendance: "6,000+", who: "public works directors", source: "https://x.test/about" } } };
  factsPanel = { html: vm.runInContext("cfFactsHTML(__facts)", ctx),
                 none: vm.runInContext("cfFactsHTML({tag:'Y'})", ctx) };
} catch (e) { errors.push(`running cfFactsHTML: ${e && e.message}`); }
/* THE CONFERENCES BOX on a company page (owner, 2026-10-28): the page's own
   coConfGroups and coConfs on a company tagged at an edition ahead, one
   behind, one whose row has rolled to next year's dates and kept the tag,
   one moved to prior_tags, one with no dates, one whose dates are for an
   older edition, one no row knows, and a hostile name. selftest holds the
   groups to build_site.co_conferences on the same fixture. */
let coConfBox = null;
try {
  ctx.__confRows = [
    { tag: "Ahead 2099", name: "Ahead Expo", dates: "March 3-5, 2099", city: "Reno, NV" },
    { tag: "Past 2020", name: "Past Expo", dates: "May 4-6, 2020", city: "Austin, TX" },
    { tag: "Rolled 2025", name: "Rolled Summit", dates: "September 25-29, 2099" },
    { tag: "New 2027", name: "New Fair", dates: null, dates_confidence: "unannounced" },
    { tag: "Moved 2099", name: "Moved Forum", dates: "June 1-2, 2099", prior_tags: ["Moved 2024"] },
    { tag: "Early 2098", name: "Stale Dates", dates: "April 1-2, 2020" },
    { tag: "Evil 2099", name: "<img src=x onerror=alert(1)>", dates: "July 7-8, 2099" },
  ];
  ctx.__confOrg = { id: "conf-co", name: "Conf Co", conferences: ["Ahead 2099", "Past 2020", "Rolled 2025",
    "New 2027", "Moved 2024", "Early 2098", "Ghost 2026", "Evil 2099"] };
  coConfBox = vm.runInContext(`(() => { D = { conferences: __confRows, organizations: [] };
    const g = coConfGroups(__confOrg), ts = l => l.map(i => i.t);
    return { groups: { now: ts(g.now), ahead: ts(g.ahead), undated: ts(g.undated), went: ts(g.went) },
             html: coConfs(__confOrg), none: coConfs({ id: "x", name: "X", conferences: [] }) }; })()`, ctx);
} catch (e) { errors.push(`running coConfs: ${e && e.message}`); }
console.log(JSON.stringify({ errors, cards: out, boards, views, front, ratings, fresh, staleNews, acts, paySentence, coCards, flags, factsPanel, coConfBox }));
