/* OPENING AN EMAILED LINK DOES NOTHING UNTIL A PERSON PRESSES THE BUTTON.
 *
 * alerts.html confirmed a subscription (?confirm=1) and deleted one (?stop=1)
 * the moment the page loaded, and claim.html confirmed a claim the same way.
 * Work and government mail gateways open links in a sandbox that runs
 * scripts, so a scanner could start a stranger's digests or silently stop
 * somebody's (launch audit, 2026-10-06). This loads each page's own script
 * into a node vm with a small fake DOM, opens the link, lets the page settle,
 * records every POST, and only then presses the button.
 * Prints one JSON object; selftest.py asserts on it. */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const TOKEN = "T".repeat(43);

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

async function run(page, search, getAnswer, postAnswer = null) {
  const html = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
  const scripts = [...html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/g)]
    .filter((m) => !/\bsrc=/.test(m[1] || "") && !/application\/(ld\+)?json/.test(m[1] || ""))
    .map((m) => m[2]);
  const S = stub();
  const els = new Map();
  const el = (key) => {
    if (!els.has(key)) {
      const e = { hidden: true, disabled: false, textContent: "", value: "", dataset: {},
                  style: {}, onclick: null, _html: "",
                  appendChild() {}, append() {}, prepend() {}, remove() {}, before() {},
                  after() {}, insertAdjacentHTML() {}, setAttribute() {}, addEventListener() {},
                  querySelector: () => S, querySelectorAll: () => [],
                  classList: { add() {}, remove() {}, toggle() {}, contains: () => false } };
      Object.defineProperty(e, "innerHTML", {
        get() { return e._html; },
        set(v) { e._html = String(v);
                 for (const m of e._html.matchAll(/id="([\w-]+)"/g)) el("#" + m[1]); },
      });
      els.set(key, e);
    }
    return els.get(key);
  };
  const posts = [];
  const fetch = async (url, init = {}) => {
    if ((init.method || "GET").toUpperCase() === "POST") {
      const body = JSON.parse(init.body || "{}");
      posts.push(body.action);
      if (postAnswer === "offline") throw new TypeError("Failed to fetch");
      const ans = postAnswer || { ok: true, confirmed: true };
      return { ok: !!ans.ok, status: ans.ok ? 200 : 400, json: async () => ans };
    }
    return { ok: true, status: 200, json: async () => getAnswer(String(url)) };
  };
  const storage = { getItem: () => null, setItem() {}, removeItem() {} };
  const doc = new Proxy({}, { get(_t, k) {
    if (k === "querySelector") return (sel) => (sel.startsWith("#") ? el(sel) : S);
    if (k === "getElementById") return (id) => el("#" + id);
    if (k === "querySelectorAll") return () => [];
    return S;
  } });
  const ctx = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    document: doc, navigator: S, history: S, localStorage: storage, sessionStorage: storage,
    location: { search, pathname: "/" + page.replace(".html", ""), hash: "", href: "https://sledjobs.com/",
                origin: "https://sledjobs.com", hostname: "sledjobs.com", reload() {} },
    fetch, setTimeout: (f) => { Promise.resolve().then(f); return 0; }, clearTimeout() {},
    setInterval: () => 0, clearInterval() {}, requestAnimationFrame: () => 0,
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    addEventListener() {}, removeEventListener() {}, confirm: () => true, alert() {},
    URL, URLSearchParams, Intl, Date, Math, JSON, Promise, Map, Set, Symbol, Array, Object,
    String, Number, RegExp, Error, encodeURIComponent, decodeURIComponent, parseInt, parseFloat, isNaN,
  };
  ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
  vm.createContext(ctx);
  const errors = [];
  for (const src of scripts) {
    try { vm.runInContext(src, ctx); } catch (e) { errors.push(String(e && e.message)); }
  }
  const settle = async () => { for (let i = 0; i < 50; i++) await new Promise((r) => setImmediate(r)); };
  await settle();
  const before = [...posts];
  const button = [...els.entries()].find(([k, e]) => ["#linkgo", "#confirmgo"].includes(k) && typeof e.onclick === "function");
  if (button) { button[1].onclick({ preventDefault() {}, stopPropagation() {} }); await settle(); }
  const st = (k) => (els.has(k) ? els.get(k) : null);
  return { errors, onLoad: before, afterClick: [...posts], button: button ? button[0] : null,
           deadShown: st("#dead") ? st("#dead").hidden === false : false,
           confirmMsg: st("#m-confirm") ? st("#m-confirm").textContent : "",
           confirmDisabled: st("#confirmgo") ? st("#confirmgo").disabled : null,
           view: st("#view") ? st("#view")._html.slice(0, 400) : "" };
}

const out = {};
const subAnswer = () => ({ ok: true, confirmed: false, prefs: { cadence: "weekly" }, saved: [], removed: {} });
out.alertsConfirm = await run("alerts.html", `?t=${TOKEN}&confirm=1`, subAnswer);
out.alertsStop = await run("alerts.html", `?t=${TOKEN}&stop=1`, subAnswer);
out.claimConfirm = await run("claim.html", `?t=${TOKEN}`, () => ({ ok: true, confirmed: false,
  name: "Acme", company_id: "acme", email: "jane@example.org", goes_live_without_review: [] }));
// a subscriber ALREADY confirmed opens the link again (it is also the
// settings link): no button, nothing sent, straight to settings
out.alertsConfirmedReopen = await run("alerts.html", `?t=${TOKEN}&confirm=1`,
  () => ({ ...subAnswer(), confirmed: true, last_sent: "2026-10-01" }));
const claimAnswer = () => ({ ok: true, confirmed: false, name: "Acme", company_id: "acme",
  email: "jane@example.org", goes_live_without_review: [] });
out.claimLapsed = await run("claim.html", `?t=${TOKEN}`, claimAnswer, { error: "bad_token" });
out.claimOffline = await run("claim.html", `?t=${TOKEN}`, claimAnswer, "offline");
console.log(JSON.stringify(out));
