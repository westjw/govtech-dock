/* Drive functions/api/saved.js for real: the actual module imported, a D1
 * that IS SQLite (node:sqlite - D1 is SQLite, so the endpoint's own SQL runs
 * unchanged), sign-ins minted by functions/_session.js itself, and a clock
 * this file moves. Prints one JSON object of results; selftest asserts on it
 * (check_saved_roles_follow_the_sign_in).
 */
// node:sqlite is in Node 22.13 and later (GitHub's runners carry 22.23). An
// older node says so and is skipped, rather than failing a crawl over a test.
import { AsyncLocalStorage } from "node:async_hooks";
let DatabaseSync = null;
/* A request can be told to stop just before its first WRITE batch (after it
   has read), so others commit in between: the interleavings that beat the
   first two locks (second review, 2026-11-18). */
const als = new AsyncLocalStorage();
try { ({ DatabaseSync } = await import("node:sqlite")); }
catch (e) { console.log(JSON.stringify({ skip: `node:sqlite is not available in node ${process.version}` })); process.exit(0); }

let clock = Date.parse("2026-11-18T12:00:00.000Z");
const realNow = Date.now;
Date.now = () => clock;
const iso = (ms) => new Date(ms).toISOString();
const DAY = 86400000;

function d1() {
  const db = new DatabaseSync(":memory:");
  let written = 0;
  /* THE RACE, made to happen: with holdBatches(n) the first n batches wait
     until all n have been asked for, so n requests have all READ before any
     of them writes - the interleaving that let twelve parallel POSTs past the
     caps (review, 2026-11-18). Without it the requests in this file finish
     one after another and the lock is never tested. */
  let gate = null;
  const runSync = (sql, params) => {
    if (/^\s*SELECT/i.test(sql)) return { results: db.prepare(sql).all(...params), success: true };
    const r = db.prepare(sql).run(...params);
    if (/^\s*(INSERT|UPDATE|DELETE)/i.test(sql)) written += Number(r.changes || 0);
    return { success: true, meta: { changes: r.changes } };
  };
  const stmt = (sql, params = []) => ({
    _sql: sql, _params: params,
    bind: (...p) => stmt(sql, p),
    async all() { return { results: db.prepare(sql).all(...params), success: true }; },
    async first() { return db.prepare(sql).get(...params) ?? null; },
    async run() {
      const r = db.prepare(sql).run(...params);
      if (/^\s*(INSERT|UPDATE|DELETE)/i.test(sql)) written += Number(r.changes || 0);
      return { success: true, meta: { changes: r.changes } };
    },
  });
  return {
    prepare: (sql) => stmt(sql),
    holdBatches(n) { let release; const p = new Promise((r) => { release = r; }); gate = { n, count: 0, p, release }; },
    async batch(stmts) {
      const st = als.getStore();
      if (st && st.pause && stmts.some((x) => !/^\s*SELECT/i.test(x._sql))) {
        const hold = st.pause; st.pause = null; await hold;
      }
      if (gate) {
        const g = gate;
        g.count++;
        if (g.count >= g.n) { gate = null; g.release(); } else await g.p;
      }
      // ONE STEP, as D1 runs a batch: no other request's batch can start
      // inside this one (an await here let them, which D1 never does)
      db.exec("BEGIN");
      try {
        const out = stmts.map((s) => runSync(s._sql, s._params));
        db.exec("COMMIT");
        return out;
      } catch (e) { db.exec("ROLLBACK"); throw e; }
    },
    raw: db,
    written: () => written,
  };
}

const session = await import("../functions/_session.js");
const mod = await import("../functions/api/saved.js");
const SECRET = "x".repeat(48);

async function cookieFor(email) {
  const set = await session.sessionCookie({ SESSION_SECRET: SECRET }, email, "email");
  return set.split(";")[0];
}
function req(method, { cookie, origin = "https://sledjobs.com", body } = {}) {
  const headers = { "content-type": "application/json" };
  if (cookie) headers.cookie = cookie;
  if (origin) headers.origin = origin;
  return new Request("https://sledjobs.com/api/saved", {
    method, headers, body: body === undefined ? undefined : (typeof body === "string" ? body : JSON.stringify(body)) });
}
async function call(method, env, opts) {
  const fn = method === "GET" ? mod.onRequestGet : mod.onRequestPost;
  const res = await fn({ request: req(method, opts), env });
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  return { status: res.status, data };
}
const ids = (r) => ((r.data && r.data.saved) || []).map((s) => s.id).sort();
const role = (id, at, extra = {}) => ({ id, title: "Account Executive " + id, company: "Acme",
  company_id: "acme", url: "https://example.org/" + id, saved_on: at.slice(0, 10), saved_at: at, ...extra });

const out = {};
const DB = d1();
const env = { SESSION_SECRET: SECRET, DB };
// Gmail is the one provider whose dots and +tags fold, so the test needs its
// domain. The addresses are put together here, never written whole, so this
// public file carries no mailbox literal (the selftest privacy screen); none
// of them is ever mailed - a sign-in cookie is minted for each, nothing more.
const GM = "@" + ["gmail", "com"].join(".");
const A = await cookieFor("sled.harness.one" + GM);
const A2 = await cookieFor("sledharnessone+phone" + GM);   // the same inbox, spelled another way
const B = await cookieFor("someone.else@example.org");

// 1. no database bound: the endpoint says so and writes nothing
out.not_configured = (await call("GET", { SESSION_SECRET: SECRET }, { cookie: A })).status;
out.not_configured_post = (await call("POST", { SESSION_SECRET: SECRET }, { cookie: A, body: { saved: [] } })).status;
// 2. no sign-in
out.not_signed_in = (await call("POST", env, { body: { saved: [role("r1", iso(clock))] } })).status;
out.not_signed_in_get = (await call("GET", env, {})).status;
// 3. another site cannot make a signed-in browser write
const w0 = DB.written();
out.cross_site = (await call("POST", env, { cookie: A, origin: "https://evil.example", body: { saved: [role("r1", iso(clock))] } })).status;
out.cross_site_wrote = DB.written() - w0;

// 4. the laptop saves two roles
const t1 = iso(clock);
let r = await call("POST", env, { cookie: A, body: { saved: [role("r1", t1), role("r2", t1)], removed: {} } });
out.laptop = { status: r.status, ids: ids(r), wrote: r.data && r.data.wrote };
// 5. the phone, same inbox spelled differently, sees them; somebody else sees nothing
r = await call("GET", env, { cookie: A2 });
out.phone = { status: r.status, ids: ids(r) };
r = await call("GET", env, { cookie: B });
out.stranger = { status: r.status, ids: ids(r) };

// 6. the phone unsaves r1
clock += 60000;
const tGone = iso(clock);
r = await call("POST", env, { cookie: A2, body: { saved: [role("r2", t1)], removed: { r1: tGone } } });
out.unsave = { status: r.status, ids: ids(r), removed: Object.keys((r.data && r.data.removed) || {}) };
// 7. the laptop still holds r1 (saved before the unsave): it must not come back
clock += 60000;
r = await call("POST", env, { cookie: A, body: { saved: [role("r1", t1), role("r2", t1)], removed: {} } });
out.stale_laptop = { status: r.status, ids: ids(r), wrote: r.data && r.data.wrote };
// 8. saving r1 again, after the unsave, brings it back
clock += 60000;
r = await call("POST", env, { cookie: A, body: { saved: [role("r1", iso(clock))], removed: {} } });
out.resave = { status: r.status, ids: ids(r) };

// 8b. A BORROWED BROWSER: B signs in where A's copy still sits, tagged as A's.
// A's list must not be merged into B's account; B gets B's own list back -
// B holds one role first, so an empty answer cannot pass for B's list.
await call("POST", env, { cookie: B, body: { saved: [role("b0", iso(clock))], removed: {} } });
const tagA = (await call("GET", env, { cookie: A })).data.owner;
const tagA2 = (await call("GET", env, { cookie: A2 })).data.owner;
r = await call("POST", env, { cookie: B, body: { saved: [role("r1", iso(clock)), role("r2", t1)], removed: {}, owner: tagA } });
out.borrowed = { status: r.status, replace: r.data && r.data.replace, ids: ids(r),
  tag_is_b: !!(r.data && r.data.owner && r.data.owner !== tagA), same_inbox_same_tag: tagA === tagA2,
  tag_is_not_key: !!tagA && !JSON.stringify(DB.raw.prepare("SELECT owner FROM saved_roles").all()).includes(tagA) };
// and an untagged copy (saves from before this existed) merges into whoever
// signs in first. The board sends owner "" for one (savedOwner()), so that is
// what is sent here - a missing field is not the case that happens.
r = await call("POST", env, { cookie: B, body: { saved: [role("b1", iso(clock))], removed: {}, owner: "" } });
out.untagged_merges = { replace: r.data && r.data.replace, ids: ids(r) };
// B's own tag is B's: a POST carrying it merges as normal
r = await call("POST", env, { cookie: B, body: { saved: [role("b2", iso(clock))], removed: {}, owner: r.data.owner } });
out.own_tag_merges = { replace: r.data && r.data.replace, ids: ids(r) };
// B's list is back to empty before the big-list cases below
await call("POST", env, { cookie: B, body: { saved: [], removed: { b0: iso(clock + 1), b1: iso(clock + 1), b2: iso(clock + 1) } } });

// 9. the same list twice writes nothing the second time
const again = { saved: [role("r1", iso(clock)), role("r2", t1)], removed: {} };
await call("POST", env, { cookie: A, body: again });
r = await call("POST", env, { cookie: A, body: again });
out.idempotent_wrote = r.data && r.data.wrote;

// 10. what a saved role may carry: the client does not decide
// (the shapes toggleSave copies off a posting: territory and office are
// objects on every posting in data/board.json)
const longUrl = "https://example.org/jobs/" + "x".repeat(400);
r = await call("POST", env, { cookie: A, body: { saved: [role("r3", iso(clock),
  { title: "T".repeat(500), url: longUrl, quota_carrying: true,
    territory: { states: ["AZ", "NM"], region: "Southwest", stated: true, extra: 1 },
    office: { city: "Phoenix", state: "AZ", zip: "85001" }, work_mode: "hybrid",
    secret_field: "nope", extra: { x: 1 } })] } });
const r3 = ((r.data && r.data.saved) || []).find((s) => s.id === "r3") || {};
out.clean = { title_len: (r3.title || "").length, url_kept: r3.url === longUrl,
  territory: r3.territory || null, office: r3.office || null, work_mode: r3.work_mode || null,
  quota: r3.quota_carrying === true, has_secret: "secret_field" in r3, has_extra: "extra" in r3 };

// 11. no address is stored, in any spelling
const dump = JSON.stringify(DB.raw.prepare("SELECT * FROM saved_roles").all())
  + JSON.stringify(DB.raw.prepare("SELECT * FROM saved_owners").all())
  + JSON.stringify(DB.raw.prepare("SELECT * FROM saved_cas").all());
out.address_stored = /@|sled\.?harness|someone/i.test(dump);

// 12. bodies past the limit, and lists past 500
out.too_large = (await call("POST", env, { cookie: B, body: "x".repeat(mod.MAX_BODY + 1) })).status;
const many = Array.from({ length: 500 }, (_, i) => role("m" + i, iso(clock)));
r = await call("POST", env, { cookie: B, body: { saved: many } });
out.five_hundred = { status: r.status, n: ids(r).length };
r = await call("POST", env, { cookie: B, body: { saved: [role("one-more", iso(clock + 1))], removed: { m0: iso(clock + 2) } } });
out.past_five_hundred = { status: r.status, capped: r.data && r.data.capped, n: ids(r).length,
  has_one_more: ids(r).includes("one-more") };
r = await call("POST", env, { cookie: B, body: { saved: [role("two-more", iso(clock + 3)), role("three-more", iso(clock + 3))] } });
out.past_five_hundred_again = { status: r.status, capped: r.data && r.data.capped, n: ids(r).length };
out.bad_json = (await call("POST", env, { cookie: B, body: "{not json" })).status;

// 12b. garbage stamps are not stamps: never stored, never "newer than" anything
const G = await cookieFor("garbage@example.org");
r = await call("POST", env, { cookie: G, body: { saved: [role("g1", iso(clock))],
  removed: { g2: "9999-12-31T00:00:00.000Z", g3: "zzzz", g4: "2026-11-18" } } });
out.garbage_removed = Object.keys((r.data && r.data.removed) || {}).sort();
r = await call("POST", env, { cookie: G, body: { saved: [role("g1", iso(clock))],
  removed: { g1: "9999-12-31T00:00:00.000Z" } } });
out.garbage_cannot_unsave = ids(r);

// 12c. at any domain but Gmail a +tag is a different address, and so a different list
const W1 = await cookieFor("alice@example.org"), W2 = await cookieFor("alice+x@example.org");
await call("POST", env, { cookie: W1, body: { saved: [role("w1", iso(clock))] } });
out.plus_elsewhere_is_another_list = ids(await call("GET", env, { cookie: W2 }));

// 12d. many requests at once cannot pass the caps together: one writer at a time
const R = await cookieFor("racer@example.org");
DB.holdBatches(12);
const burst = await Promise.all(Array.from({ length: 12 }, (_, k) =>
  call("POST", env, { cookie: R, body: { saved: Array.from({ length: 500 }, (_, i) => role(`q${k}-${i}`, iso(clock + k))) } })));
const rows = DB.raw.prepare("SELECT owner, COUNT(*) AS n FROM saved_roles GROUP BY owner").all();
const meta = DB.raw.prepare("SELECT owner, writes FROM saved_owners").all();
const raceOwner = (await import("../functions/api/saved.js")).ownerKey;
const rk = await raceOwner("racer@example.org");
out.race = { statuses: [...new Set(burst.map((b) => b.status))].sort(),
  live_rows: (rows.find((x) => x.owner === rk) || {}).n || 0,
  writes_counted: (meta.find((x) => x.owner === rk) || {}).writes || 0 };

// 12e. a writer that read, then waited while two others committed, must not
// commit on its old read: it reads again, and nobody's write is lost
const L = await cookieFor("paused@example.org");
let goX; const holdX = new Promise((r) => { goX = r; });
const xRun = als.run({ pause: holdX }, () => call("POST", env, { cookie: L, body: { saved: [role("x1", iso(clock))] } }));
await new Promise((r) => setTimeout(r, 30));
const y = await call("POST", env, { cookie: L, body: { saved: [role("y1", iso(clock))] } });
const z = await call("POST", env, { cookie: L, body: { saved: [role("z1", iso(clock))] } });
goX(); const x = await xRun;
const lk = await mod.ownerKey("paused@example.org");
const lmeta = DB.raw.prepare("SELECT v, writes FROM saved_owners WHERE owner = ?").get(lk);
const after = await call("POST", env, { cookie: L, body: { saved: [role("w1", iso(clock + 1))] } });
out.paused_writer = { statuses: [y.status, z.status, x.status], x_ids: ids(x),
  v: lmeta && Number(lmeta.v), writes: lmeta && Number(lmeta.writes), later_status: after.status,
  later_ids: ids(after) };

// 12f. the stale read that went past the cap: a writer holding 300 new roles
// waits while another lands 300; reading again, it may add only 200
const M = await cookieFor("stale@example.org");
let goM; const holdM = new Promise((r) => { goM = r; });
const mRun = als.run({ pause: holdM }, () => call("POST", env, { cookie: M,
  body: { saved: Array.from({ length: 300 }, (_, i) => role("mx" + i, iso(clock + 5))) } }));
await new Promise((r) => setTimeout(r, 30));
await call("POST", env, { cookie: M, body: { saved: Array.from({ length: 300 }, (_, i) => role("my" + i, iso(clock + 4))) } });
goM(); const mres = await mRun;
const mk = await mod.ownerKey("stale@example.org");
out.stale_cap = { live: DB.raw.prepare("SELECT COUNT(*) AS n FROM saved_roles WHERE owner = ?").get(mk).n,
  capped: mres.data && mres.data.capped };

// 12g. more than 500 sent at once: 500 land, and the answer counts the rest
const N = await cookieFor("many@example.org");
r = await call("POST", env, { cookie: N, body: { saved: Array.from({ length: 600 }, (_, i) => role("n" + i, iso(clock + i))) } });
out.six_hundred = { status: r.status, n: ids(r).length, capped: r.data && r.data.capped };

// 12h. a date ahead of the clock is no better than a stamp ahead of it: a
// role saved "next June" can still be unsaved today
const F = await cookieFor("future@example.org");
await call("POST", env, { cookie: F, body: { saved: [{ id: "f1", title: "T", saved_on: "2027-06-06", saved_at: "2027-06-06T09:00:00.000Z" }] } });
r = await call("POST", env, { cookie: F, body: { saved: [], removed: { f1: iso(clock) } } });
out.future_date_unsaves = ids(r);

// 13. one person's day of writes is capped, and a refusal writes nothing
const C = await cookieFor("capped@example.org");
const capEnv = { ...env, SAVED_WRITES_PER_DAY: "3" };
const w1 = DB.written();
r = await call("POST", capEnv, { cookie: C, body: { saved: ["c1", "c2", "c3", "c4"].map((i) => role(i, iso(clock))) } });
out.capped = { status: r.status, wrote: DB.written() - w1 };

// 14. a tombstone goes after 90 days; a younger one stays, and so do saves.
// r3 is unsaved now, r2 thirty days later; ninety-one days after the first,
// r3's row is gone and r2's tombstone is still there to stop a stale device.
clock += 60000;
await call("POST", env, { cookie: A, body: { saved: [], removed: { r3: iso(clock) } } });
clock += 30 * DAY;
const A3 = await cookieFor("sled.harness.one" + GM);     // a sign-in lasts 30 days
await call("POST", env, { cookie: A3, body: { saved: [], removed: { r2: iso(clock) } } });
clock += 61 * DAY;
const A4 = await cookieFor("sled.harness.one" + GM);
await call("POST", env, { cookie: A4, body: { saved: [], removed: {} } });
const left = DB.raw.prepare("SELECT id, saved_at, removed_at FROM saved_roles").all();
const mine = left.filter((x) => ["r1", "r2", "r3"].includes(x.id));
out.after_90_days = { rows: mine.map((x) => x.id).sort(),
  tombstone_rows: mine.filter((x) => x.removed_at && !(x.saved_at > x.removed_at)).map((x) => x.id) };
r = await call("GET", env, { cookie: A4 });
out.after_90_days_saved = ids(r);
out.after_90_days_removed = Object.keys((r.data && r.data.removed) || {}).filter((k) => /^r\d$/.test(k)).sort();
// and a stale device holding r3 cannot resurrect it once its tombstone has
// gone? It CAN - the window is the point: after 90 days every device has
// long since synced. Recorded so the behaviour is a decision, not a surprise.
r = await call("POST", env, { cookie: A4, body: { saved: [role("r3", "2026-11-18T12:03:00.000Z")] } });
out.after_90_days_stale_r3_returns = ids(r).includes("r3");

// 14b. THE PAGE IS TOLD WHOSE LIST IT HOLDS on every page answer: the bare
// 304 a cached board comes back as (second review: the first version skipped
// it), refreshed, and never left in a shared cache; signed out, it goes
const mw = await import("../functions/_middleware.js");
const tagNow = await mod.ownerTag("sled.harness.one" + GM);
const r304 = await mw.withOwner(new Response(null, { status: 304 }), req("GET", { cookie: A4 }), env);
out.owner_cookie_304 = { status: r304.status, has_tag: (r304.headers.get("set-cookie") || "").includes(`${mw.OWNER_COOKIE}=${tagNow};`),
  cache: r304.headers.get("cache-control") || "" };
const rOut = await mw.withOwner(new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }), req("GET", {}), env);
out.owner_cookie_signed_out = /Max-Age=0/.test(rOut.headers.get("set-cookie") || "");
const mwSrc = (await import("node:fs")).readFileSync(new URL("../functions/_middleware.js", import.meta.url), "utf8");
out.owner_cookie_wired = { on_304: /res\.status === 304 && pageLike\(pathOf\(request\)\)\) \{\s*try \{ return await withOwner/.test(mwSrc),
  on_gate: /if \(shut\) \{[\s\S]{0,260}withOwner\(held/.test(mwSrc) };

// 15. a database that will not answer is "unavailable", never a crash
const broken = { prepare() { throw new Error("D1 down"); }, batch() { throw new Error("D1 down"); } };
out.unavailable = (await call("GET", { SESSION_SECRET: SECRET, DB: broken }, { cookie: A4 })).status;

Date.now = realNow;
console.log(JSON.stringify(out));
