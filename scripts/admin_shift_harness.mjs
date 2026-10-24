/* RUN THE DESK ADMIN'S SHIFT, not a copy of it.
 *
 * The shift (option 5 of the admin canvas, 2026-10-23) is a loop of awaits
 * against a single-threaded server, and its defects are orderings: Undo sent
 * while the card on screen was still being written, a write that landed
 * after its card left and was credited to the next one, a refused code that
 * left nowhere to paste a new one. A blind review found them by running the
 * page's own script in a node vm against a server that answers in order;
 * this is that harness, kept. It loads admin.html's script with a small DOM
 * (enough for el(), querySelector and a MutationObserver), answers /api/
 * calls from fixtures one at a time, presses keys through the page's own
 * listeners and prints one JSON object of scenario -> what happened.
 * selftest.py does the asserting. */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const PAGE = new URL("../admin.html", import.meta.url);

// --- a DOM just big enough ---------------------------------------------------
function makeDom() {
  const observers = [];
  const doc = {};
  class Node_ {
    constructor() { this.childNodes = []; this.parentNode = null; }
    get firstChild() { return this.childNodes[0] || null; }
    get children() { return this.childNodes.filter(n => n instanceof El); }
    _changed() {
      for (const o of observers) if (o.target === this && o.active && !o.pending) {
        o.pending = true;
        queueMicrotask(() => { o.pending = false; if (o.active) o.cb([], o); });
      }
    }
    appendChild(n) {
      if (typeof n === "string") n = new Text_(n);
      if (n.parentNode) n.parentNode.removeChild(n);
      this.childNodes.push(n); n.parentNode = this; this._changed(); return n;
    }
    append(...ns) { ns.forEach(n => this.appendChild(n)); }
    prepend(n) { this.insertBefore(n, this.firstChild); }
    removeChild(n) {
      const i = this.childNodes.indexOf(n);
      if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; this._changed(); }
      return n;
    }
    insertBefore(n, ref) {
      if (!ref) return this.appendChild(n);
      if (typeof n === "string") n = new Text_(n);
      if (n.parentNode) n.parentNode.removeChild(n);
      const i = this.childNodes.indexOf(ref);
      this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, n);
      n.parentNode = this; this._changed(); return n;
    }
    replaceChild(n, old) { this.insertBefore(n, old); this.removeChild(old); return old; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceWith(n) { if (this.parentNode) this.parentNode.replaceChild(n, this); }
    before(n) { if (this.parentNode) this.parentNode.insertBefore(n, this); }
    get textContent() { return this.childNodes.map(c => c.textContent).join(""); }
    set textContent(v) {
      this.childNodes.forEach(c => { c.parentNode = null; });
      this.childNodes = [];
      if (v !== "" && v != null) this.appendChild(new Text_(String(v))); else this._changed();
    }
  }
  class Text_ extends Node_ {
    constructor(t) { super(); this.data = t; }
    get textContent() { return this.data; }
    set textContent(v) { this.data = String(v); }
  }
  class ClassList {
    constructor(e) { this.e = e; }
    _get() { return (this.e.className || "").split(/\s+/).filter(Boolean); }
    add(...c) { const s = new Set(this._get()); c.forEach(x => s.add(x)); this.e.className = [...s].join(" "); }
    remove(...c) { this.e.className = this._get().filter(x => !c.includes(x)).join(" "); }
    contains(c) { return this._get().includes(c); }
    toggle(c, f) { const on = f === undefined ? !this.contains(c) : f; if (on) this.add(c); else this.remove(c); return on; }
  }
  class El extends Node_ {
    constructor(tag) {
      super(); this.tagName = tag.toUpperCase(); this.className = ""; this.id = "";
      this.dataset = {}; this.attrs = {}; this.style = {}; this.classList = new ClassList(this);
      this.disabled = false; this.value = "";
    }
    set innerHTML(v) { this.textContent = v ? String(v) : ""; }
    get innerHTML() { return this.textContent; }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }
    getAttribute(k) {
      if (k === "id") return this.id || null;
      if (k === "class") return this.className;
      if (k.startsWith("data-")) { const d = this.dataset[k.slice(5)]; return d === undefined ? null : d; }
      return k in this.attrs ? this.attrs[k] : null;
    }
    removeAttribute(k) { delete this.attrs[k]; }
    focus() { doc.activeElement = this; }
    blur() { if (doc.activeElement === this) doc.activeElement = doc.body; }
    click() { if (!this.disabled && this.onclick) this.onclick({ preventDefault() {}, target: this }); }
    addEventListener() {}
    querySelectorAll(sel) { return qsa(this, sel); }
    querySelector(sel) { return qsa(this, sel)[0] || null; }
    closest(sel) {
      const parts = parse(sel);
      for (let n = this; n instanceof El; n = n.parentNode)
        if (parts.some(p => matchComplex(n, p))) return n;
      return null;
    }
    get previousElementSibling() {
      if (!this.parentNode) return null;
      const s = this.parentNode.children; return s[s.indexOf(this) - 1] || null;
    }
  }
  // selectors: tag#id.cls[attr=v] joined by ' ', '>' or '+', comma lists
  function parse(sel) {
    return sel.split(",").map(s => s.trim()).filter(Boolean).map(s => {
      const out = []; let comb = " ";
      for (const t of s.replace(/\s*([>+])\s*/g, " $1 ").split(/\s+/)) {
        if (t === ">" || t === "+") { comb = t; continue; }
        out.push({ comb, c: compound(t) }); comb = " ";
      }
      return out;
    });
  }
  function compound(t) {
    const c = { tag: null, id: null, cls: [], attrs: [] };
    const re = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]/g;
    let m;
    while ((m = re.exec(t))) {
      if (m[1] && m.index === 0) c.tag = m[1].toUpperCase();
      else if (m[2]) c.id = m[2];
      else if (m[3]) c.cls.push(m[3]);
      else if (m[4]) c.attrs.push([m[4], m[5]]);
    }
    return c;
  }
  function matchCompound(e, c) {
    if (!(e instanceof El)) return false;
    if (c.tag && e.tagName !== c.tag) return false;
    if (c.id && e.id !== c.id) return false;
    for (const k of c.cls) if (!e.classList.contains(k)) return false;
    for (const [k, v] of c.attrs) {
      const a = e.getAttribute(k);
      if (a === null || a === undefined) return false;
      if (v !== undefined && String(a) !== v) return false;
    }
    return true;
  }
  function matchComplex(e, parts, i = parts.length - 1) {
    if (!matchCompound(e, parts[i].c)) return false;
    if (i === 0) return true;
    const comb = parts[i].comb;
    if (comb === ">") return e.parentNode instanceof El && matchComplex(e.parentNode, parts, i - 1);
    if (comb === "+") { const p = e.previousElementSibling; return !!p && matchComplex(p, parts, i - 1); }
    for (let a = e.parentNode; a instanceof El; a = a.parentNode)
      if (matchComplex(a, parts, i - 1)) return true;
    return false;
  }
  function walk(root, f) { for (const c of root.childNodes) if (c instanceof El) { f(c); walk(c, f); } }
  function qsa(root, sel) {
    const parts = parse(sel); const out = [];
    walk(root, e => { if (parts.some(p => matchComplex(e, p))) out.push(e); });
    return out;
  }
  const docEl = new El("html"), body = new El("body");
  docEl.appendChild(body);
  const listeners = {};
  Object.assign(doc, {
    documentElement: docEl, body, activeElement: body,
    createElement: t => new El(t), createTextNode: t => new Text_(String(t)),
    querySelector: s => docEl.querySelector(s), querySelectorAll: s => docEl.querySelectorAll(s),
    getElementById: id => docEl.querySelector("#" + id),
    addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
    _listeners: listeners,
  });
  class MO {
    constructor(cb) { this.cb = cb; this.active = false; }
    observe(target) { this.target = target; this.active = true; observers.push(this); }
    disconnect() { this.active = false; }
  }
  return { doc, MO };
}

// --- the page, against a server that answers in order --------------------
function load(handlers, code = "ABC123") {
  const page = readFileSync(PAGE, "utf8");
  let script = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1])
    .reduce((a, b) => (b.length > a.length ? b : a));
  script = script.replace(/\nboot\(\);\n/, "\n");
  script += "\n;globalThis.__get = () => ({SHIFT, BELT, TAB, RENDER});"
          + " globalThis.__setMETA = v => { META = v; };\n";
  const { doc, MO } = makeDom();
  const nav = doc.createElement("nav"); nav.id = "tabs";
  const sub = doc.createElement("div"); sub.id = "sub";
  const view = doc.createElement("div"); view.id = "view";
  doc.body.append(sub, nav, view);
  const pending = [], log = [], store = { code };
  const ctx = {
    console, document: doc, MutationObserver: MO, setTimeout, clearTimeout,
    setInterval: () => 1, clearInterval: () => {}, queueMicrotask, Promise, URL, URLSearchParams,
    location: { href: "http://127.0.0.1:8795/", search: "", hash: "", pathname: "/" },
    history: { replaceState() {} }, confirm: () => true,
    fetch: (path, init) => new Promise(resolve => {
      pending.push({ path, body: init && init.body ? JSON.parse(init.body) : null, resolve });
    }),
  };
  ctx.window = ctx; ctx.globalThis = ctx;
  ctx.addEventListener = () => {};
  ctx.gtdCode = { get: () => store.code, set: v => { store.code = (v || "").trim().toUpperCase(); } };
  vm.createContext(ctx);
  vm.runInContext(script, ctx, { filename: "admin.html" });
  ctx.__setMETA({ counts: {}, labels: {}, filterable: [], generated: "2026-10-23" });
  const flush = () => new Promise(r => setImmediate(r));
  async function step() {
    await flush();
    const q = pending.shift();
    if (!q) return false;
    const u = new URL(q.path, "http://x");
    const h = handlers[u.pathname];
    const [status, ans] = h ? h(u, q.body) : [404, { error: "no handler " + u.pathname }];
    log.push(u.pathname + (q.body ? " " + JSON.stringify(q.body) : ""));
    if (status === "down") q.resolve(Promise.reject(new TypeError("Failed to fetch")));
    else q.resolve({ status, json: async () => JSON.parse(JSON.stringify(ans)) });
    for (let i = 0; i < 5; i++) await flush();
    return true;
  }
  async function drain() { while (await step()); }
  function key(k) {
    const e = { key: k, target: doc.activeElement || doc.body, preventDefault() {} };
    for (const f of doc._listeners.keydown || []) f(e);
  }
  const run = () => ctx.__get().SHIFT.run;
  const state = () => {
    const r = run();
    return { todo: r.todo.map(i => r.cards[i].key),
             cards: Object.fromEntries(r.cards.map(c => [c.key, { status: c.status, ids: c.ids }])),
             last: r.last && { key: r.cards[r.last.k].key, ids: r.last.ids, status: r.last.status } };
  };
  return { ctx, doc, pending, step, drain, flush, key, log, store, state };
}

// --- fixtures ----------------------------------------------------------------
const placeCo = id => ({ id, name: "Co " + id, sector: "General Gov", category: "Suppliers & Services",
  proposed_sector: "Public Safety", proposed_category: "Records", confidence: "medium",
  description: "sells " + id, open_roles: 1 });

function server(cards, extra = {}) {
  const ruled = new Set(), byEntry = {};
  let n = 0;
  const items = Object.fromEntries(cards.map(c => [c.key, c.item]));
  const h = {
    "/api/shift": () => [200, { minutes: 10, dealt_at: "2026-10-24T10:00:00-04:00",
      health: { exact: 50, score: 50, parts: [] }, cards, groups: [] }],
    "/api/agree": () => [200, { ruled: 0, agreed: 0, by_confidence: [] }],
    "/api/shift-progress": () => [200, { ruled: ruled.size, by_queue: {}, undone: 0,
      proposed: { ruled: 0, agreed: 0, considered: 0 }, health: { exact: 50, score: 50 } }],
    "/api/shift-card": u => {
      const k = u.searchParams.get("key");
      return [200, ruled.has(k) ? { pending: false, item: null } : { pending: true, item: items[k] }];
    },
    "/api/place": (_u, b) => {
      ruled.add(b.id);
      const ids = [`J#${++n}`, `J#${++n}`];
      ids.forEach(i => { byEntry[i] = b.id; });
      return [200, { ok: true, message: "placed " + b.id, journal: ids }];
    },
    "/api/undo": (_u, b) => {
      b.entries.forEach(i => ruled.delete(byEntry[i]));
      return [200, { ok: true, message: "undid " + b.entries.join(","), undone: b.entries }];
    },
    "/api/check-code": () => [200, { ok: true, message: "ruling code accepted for this run" }],
    "/api/shifts": () => [200, { shifts: [{ started_at: "2026-10-23T09:00:00-04:00", minutes: 20,
      ruled: 4, proposed: { ruled: 2, agreed: 1 }, moved: 0.2 }] }],
    "/api/shift-log": (_u, b) => [200, { ok: true, shift: { ruled: ruled.size, by_queue: {}, undone: 0,
      proposed: { ruled: 0, agreed: 0, considered: 0 }, health_end: 50, moved: 0, skipped: b.skipped } }],
    ...extra,
  };
  return { h, ruled };
}
const keyed = ks => ks.map(k => ({ queue: "miscategorized", label: "Wrong bucket", key: k, keyed: true,
  moves: "Right bucket, public rows", no_points: null, points: 0.1, item: placeCo(k) }));

// a list card whose write lands and whose card stays up a moment, the way
// Missing websites removes its row 300-1400 ms after the save answers
function listCards(ks) {
  return ks.map(k => ({ queue: "fake", label: "Fake queue", key: k, keyed: false,
    moves: null, no_points: "test", points: 0, item: { id: k, name: "Co " + k } }));
}
function addFakeRenderer(S) {
  S.ctx.__get().RENDER.fake = it => {
    const n = S.ctx.document.createElement("div"); n.className = "item";
    const b = S.ctx.document.createElement("button"); b.className = "act go"; b.textContent = "Save " + it.id;
    b.onclick = async () => {
      const r = await S.ctx.api("/api/fake-write", { id: it.id });
      if (!r.error) S.ctx.__later = () => n.remove();     // removed when the test says so
    };
    n.appendChild(b);
    return n;
  };
}
const fakeWrite = (ruled, counter) => ({ "/api/fake-write": (_u, b) => {
  ruled.add(b.id); return [200, { ok: true, message: "saved " + b.id, journal: [`W#${++counter.n}`] }]; } });

const out = {};

// 1. Undo pressed while the card on screen is being written is refused, and
//    nothing is credited to the wrong card.
{
  const sv = server(keyed(["A", "B", "C"]));
  const S = load(sv.h);
  S.ctx.startShift(10); await S.drain();
  S.key("a"); S.key("Enter"); await S.drain();            // A ruled
  S.key("a"); S.key("Enter"); await S.flush();            // B's place in flight
  S.key("u"); await S.drain();
  out.undo_waits_for_the_write = { state: S.state(), sent_undo: S.log.some(l => l.startsWith("/api/undo")),
                                   on_disk: [...sv.ruled] };
}

// 2. Undo of the last ruling brings that card back in front of the person.
{
  const sv = server(keyed(["A", "B"]));
  const S = load(sv.h);
  S.ctx.startShift(10); await S.drain();
  S.key("a"); S.key("Enter"); await S.drain();
  S.key("u"); await S.drain();
  out.undo_brings_the_card_back = { state: S.state(), on_disk: [...sv.ruled],
    undo_sent: S.log.filter(l => l.startsWith("/api/undo")) };
}

// 3. Skip while a list card's write is in flight is refused; a write that has
//    landed makes Skip a ruling, never "unruled"; ids stay with their card.
{
  const counter = { n: 0 };
  const sv = server(listCards(["P1", "P2", "P3"]));
  Object.assign(sv.h, fakeWrite(sv.ruled, counter));
  const S = load(sv.h);
  addFakeRenderer(S);
  S.ctx.startShift(10); await S.drain();
  const save = () => S.doc.querySelector("#sh-slot button.act.go");
  save().click(); await S.flush();                         // P1's write in flight
  S.key("j"); await S.drain();                             // refused, then the write lands
  const afterRefusedSkip = S.state();
  S.key("j"); await S.drain();                             // card still up: Skip now
  const afterLandedSkip = S.state();
  save().click(); await S.drain();                         // P2 written...
  S.ctx.__later(); await S.drain();                        // ...and removes itself
  out.skip_waits_and_ids_stay_put = { afterRefusedSkip, afterLandedSkip, final: S.state() };
}

// 4. A refused code is forgotten, so the home draws a box to paste the new
//    one into, and the shifts already listed stay listed.
{
  const sv = server(keyed(["A"]), { "/api/check-code": () => [403, { error: "the ruling code is not this run's",
                                                                     code_required: true }] });
  const S = load(sv.h, "OLD999");
  S.ctx.showShiftHome(); await S.drain();
  S.key("Enter"); await S.drain();
  out.a_refused_code_gets_a_box = { boxes: S.doc.querySelectorAll("#shift-host input.txt").length,
    stored: S.store.code, codebar: !!S.doc.querySelector("#codebar"),
    listed: S.doc.querySelectorAll(".sh-shift").length, tab: S.ctx.__get().TAB };
}

// 5. A card the server cannot be asked about stays in the hand.
{
  const sv = server(keyed(["A", "B", "C"]));
  const S = load(sv.h);
  S.ctx.startShift(10); await S.drain();
  sv.h["/api/shift-card"] = () => ["down"];
  S.key("j"); await S.drain();                             // A skipped; B cannot be checked
  out.a_failed_check_keeps_the_hand = { state: S.state(),
    says: (S.doc.querySelector("#sh-slot") || { textContent: "" }).textContent };
}

// 6. A deal that fails gives the tabs back.
{
  const sv = server(keyed(["A"]), { "/api/shift": () => ["down"] });
  const S = load(sv.h);
  S.ctx.showShiftHome(); await S.drain();
  out.a_failed_deal_leaves_the_doors_open = { body: S.doc.body.className,
    buttons: S.doc.querySelectorAll("#view button").map(b => b.textContent) };
}

// 7. A card ruled elsewhere since the deal is not shown, and is counted gone.
{
  const sv = server(keyed(["A", "B"]));
  sv.ruled.add("A");
  const S = load(sv.h);
  S.ctx.startShift(10); await S.drain();
  out.a_card_ruled_elsewhere_is_not_shown = { state: S.state() };
}

// 8. End shift sends the statuses' count of skips and shows the server's record.
{
  const sv = server(keyed(["A", "B", "C"]));
  const S = load(sv.h);
  S.ctx.startShift(10); await S.drain();
  S.key("a"); S.key("Enter"); await S.drain();
  S.key("j"); await S.drain();
  S.ctx.shiftEnd(); await S.drain();
  const sent = S.log.find(l => l.startsWith("/api/shift-log")) || "";
  out.end_shift_logs_the_servers_count = { sent: sent ? JSON.parse(sent.slice(sent.indexOf("{"))) : null,
    says: (S.doc.querySelector("#shift-host") || { textContent: "" }).textContent };
}

// 9. Two writes from one card, the first of which takes the card away (a
//    founding-year card's Save and Confirm both sent): the second lands after
//    the next card is up and is still that card's.
{
  const counter = { n: 0 };
  const sv = server(listCards(["Q1", "Q2"]).map(c => Object.assign(c, { queue: "fake2" })));
  Object.assign(sv.h, fakeWrite(sv.ruled, counter));
  const S = load(sv.h);
  S.ctx.__get().RENDER.fake2 = it => {
    const n = S.ctx.document.createElement("div"); n.className = "item";
    for (const label of ["one", "two"]) {
      const b = S.ctx.document.createElement("button"); b.className = "act " + label; b.textContent = label;
      b.onclick = async () => {
        const r = await S.ctx.api("/api/fake-write", { id: it.id + "-" + label });
        if (!r.error) n.remove();
      };
      n.appendChild(b);
    }
    return n;
  };
  S.ctx.startShift(10); await S.drain();
  S.doc.querySelector("#sh-slot button.one").click();
  S.doc.querySelector("#sh-slot button.two").click();
  await S.drain();
  out.two_writes_stay_with_their_card = { state: S.state() };
}

// 10. Undo pressed after the card's write has landed but before the card has
//     moved on (the belt waits for the agree-rate) is refused too.
{
  const sv = server(keyed(["A", "B", "C"]));
  const S = load(sv.h);
  S.ctx.startShift(10); await S.drain();
  S.key("a"); S.key("Enter"); await S.drain();            // A ruled
  S.key("a"); S.key("Enter");
  await S.step();                                          // B's place answered...
  S.key("u");                                              // ...its agree read not yet
  await S.drain();
  out.undo_waits_for_the_card_to_move_on = { state: S.state(),
    sent_undo: S.log.some(l => l.startsWith("/api/undo")) };
}

// 11. A Check that writes nothing does not hold Skip back.
{
  const sv = server(listCards(["R1", "R2"]).map(c => Object.assign(c, { queue: "fake3" })));
  sv.h["/api/verify-website"] = () => [200, { ok: true, message: "read" }];
  const S = load(sv.h);
  S.ctx.__setMETA({ counts: {}, labels: {}, filterable: [], read_only_posts: ["verify-website"] });
  S.ctx.__get().RENDER.fake3 = it => {
    const n = S.ctx.document.createElement("div"); n.className = "item";
    const b = S.ctx.document.createElement("button"); b.className = "act check"; b.textContent = "Check";
    b.onclick = () => S.ctx.api("/api/verify-website", { url: "https://" + it.id + ".test" });
    n.appendChild(b);
    return n;
  };
  S.ctx.startShift(10); await S.drain();
  S.doc.querySelector("#sh-slot button.check").click(); await S.flush();   // the read in flight
  S.key("j"); await S.drain();
  out.a_read_does_not_hold_skip = { state: S.state() };
}

// 12. A ruling refused for want of a code, with none ever stored, asks for
//     one rather than calling it a code from an earlier run.
{
  const sv = server(keyed(["A"]), { "/api/place": () => [403, { error: "needs the code", code_required: true }] });
  const S = load(sv.h, "");
  S.ctx.api("/api/place", { id: "A" }); await S.drain();
  const bar = S.doc.querySelector("#codebar .cb-t");
  out.no_code_is_not_a_stale_code = { says: bar ? bar.textContent : null };
}

// 13. A code typed on the home and left by another door goes with the person.
{
  const sv = server(keyed(["A"]));
  const S = load(sv.h, "");
  S.ctx.showShiftHome(); await S.drain();
  const box = S.doc.querySelector("#shift-host input.txt");
  if (box) box.value = "new123";
  S.ctx.shiftLeave();
  out.a_typed_code_is_kept = { box: !!box, stored: S.store.code };
}

console.log(JSON.stringify(out));
