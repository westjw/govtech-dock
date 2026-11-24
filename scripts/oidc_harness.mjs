/* DRIVE "SIGN IN WITH SLED JOBS" AS ACCESS AND A BROWSER WOULD (owner,
 * 2026-11-23, design C).
 *
 * Imports the real modules - functions/oidc/*, the discovery document, the
 * root middleware for the gate, and the site's own sign-in pages - and runs
 * them with an env holding a P-256 key this file generated, a D1 that IS
 * SQLite (node:sqlite: D1 is SQLite, so the endpoints' own SQL runs), a KV
 * and a stub Resend for the sign-in email, the Users board served as the
 * edge serves it, and a clock this file moves. The whole round trip is run:
 * authorize, the code, the token, the ID token verified with the published
 * keys. Then every refusal. Prints one JSON object; selftest.py
 * check_job_hunter_signs_in_with_sled_jobs asserts on it, and verifies the
 * ID token's signature again in Python with its own P-256 code. The only
 * addresses in here are RFC 2606 ones. Nothing in it is a real secret: every
 * key and secret is made here, on each run.
 */
import { generateKeyPairSync, createHash, createPublicKey, createSign, randomBytes,
         verify as nodeVerify } from "node:crypto";

let DatabaseSync = null;
try { ({ DatabaseSync } = await import("node:sqlite")); }
catch (e) { process.stdout.write(JSON.stringify({ skip: `node:sqlite is not available in node ${process.version}` }) + "\n"); process.exit(0); }

/* THE CLOCK, moved by this file */
let clock = Date.parse("2026-11-23T12:00:00.000Z");
Date.now = () => clock;
const sec = () => Math.floor(clock / 1000);

/* NOTHING IN THE SIGN-IN MAY LOG: every console call is recorded, and the
 * result is printed with process.stdout, never console */
const logged = [];
for (const k of ["log", "info", "warn", "error", "debug", "trace"]) {
  console[k] = (...a) => { logged.push(`${k}: ${a.map(String).join(" ").slice(0, 200)}`); };
}

const M = (p) => import(new URL(`../functions/${p}`, import.meta.url));
const { onRequest } = await M("_middleware.js");
const G = await M("_gate.js");
const S = await M("_session.js");
const O = await M("_oidc.js");
const authorize = await M("oidc/authorize.js");
const token = await M("oidc/token.js");
const jwksMod = await M("oidc/jwks.js");
const disco = await M(".well-known/openid-configuration.js");
const signinPage = await M("signin/index.js");
const signinEmail = await M("signin/email.js");
const signinCode = await M("signin/code.js");

const CALLBACK = "https://solesource-c6g-pages.cloudflareaccess.com/cdn-cgi/access/callback";
const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const ACCESS_AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const b64u = (buf) => Buffer.from(buf).toString("base64url");
const sha = (s) => createHash("sha256").update(s).digest();
const hex = (s) => createHash("sha256").update(s).digest("hex");

/* --- keys, made on every run ------------------------------------------------ */
const ecJwk = (kid) => ({ ...generateKeyPairSync("ec", { namedCurve: "P-256" }).privateKey.export({ format: "jwk" }), kid });
const CUR = ecJwk("test-key-now");
const OLDK = ecJwk("test-key-before");
const OTHER = ecJwk("test-key-other");
const RSA = generateKeyPairSync("rsa", { modulusLength: 2048 });
const RSA_JWK = RSA.publicKey.export({ format: "jwk" });
const CLIENT_ID = "jh-harness-client";
const CLIENT_SECRET = randomBytes(32).toString("base64url");
const SESSION_SECRET = randomBytes(36).toString("base64url");

function accessToken(email, iat = sec()) {
  const b = (o) => b64u(JSON.stringify(o));
  const head = b({ alg: "RS256", kid: "a1", typ: "JWT" });
  const body = b({ aud: [ACCESS_AUD], iss: `https://${TEAM}`, iat, exp: iat + 3600, email });
  return `${head}.${body}.${createSign("RSA-SHA256").update(`${head}.${body}`).sign(RSA.privateKey).toString("base64url")}`;
}

/* --- the world ---------------------------------------------------------------- */
const mails = [], fetched = [];
globalThis.fetch = async (url, init = {}) => {
  const u = String(url);
  fetched.push(new URL(u).host);
  if (u === "https://api.resend.com/emails") { mails.push(JSON.parse(init.body)); return new Response("{}", { status: 200 }); }
  if (u === `https://${TEAM}/cdn-cgi/access/certs`) {
    return new Response(JSON.stringify({ keys: [{ kty: "RSA", kid: "a1", n: RSA_JWK.n, e: RSA_JWK.e, alg: "RS256" }] }), { status: 200 });
  }
  return new Response("nope", { status: 404 });
};

/* D1 that IS SQLite. `down` makes every call throw, as a D1 outage would.
 * holdSpends(n): the next n spends of a code each wait until all n have been
 * asked, then run in order, so two redemptions both reach the UPDATE before
 * either reads or deletes the row - the interleaving the atomic `used = 0`
 * is there for (a mutation run found the plain race never produced it).
 * holdFails(n): the same for the next n calls that read or write the
 * failure count, so a burst all reaches the count before any request is
 * done with it - the interleaving a count read first and written after
 * loses to, however close together the read and the write sit. */
function d1() {
  const db = new DatabaseSync(":memory:");
  const box = { down: false, hold: null };
  const holdOn = (re, n) => { let release; const p = new Promise((r) => { release = r; }); box.hold = { re, n, count: 0, p, release }; };
  box.holdSpends = (n) => holdOn(/^\s*UPDATE oidc_codes/i, n);
  box.holdFails = (n) => holdOn(/^\s*(SELECT|INSERT|UPDATE|DELETE)\b[\s\S]*\boidc_fails\b/i, n);
  const wait = async (sqls) => {
    const g = box.hold;
    if (!g || !sqls.some((s) => g.re.test(s))) return;
    g.count++;
    if (g.count >= g.n) { box.hold = null; g.release(); } else await g.p;
  };
  const run1 = (sql, p) => {
    if (/^\s*SELECT/i.test(sql)) return { results: db.prepare(sql).all(...p), success: true };
    const r = db.prepare(sql).run(...p);
    return { success: true, meta: { changes: Number(r.changes) } };
  };
  const stmt = (sql, p = []) => ({ _sql: sql, _p: p, bind: (...a) => stmt(sql, a),
    async run() { if (box.down) throw new Error("D1_ERROR: down"); await wait([sql]); return run1(sql, p); },
    async first() { if (box.down) throw new Error("D1_ERROR: down"); await wait([sql]); return db.prepare(sql).get(...p) ?? null; },
    async all() { if (box.down) throw new Error("D1_ERROR: down"); await wait([sql]); return run1(sql, p); } });
  return Object.assign(box, { raw: db, prepare: (s) => stmt(s),
    async batch(xs) {
      if (box.down) throw new Error("D1_ERROR: down");
      await wait(xs.map((x) => x._sql));
      db.exec("BEGIN");
      try { const o = xs.map((x) => run1(x._sql, x._p)); db.exec("COMMIT"); return o; }
      catch (e) { db.exec("ROLLBACK"); throw e; }
    } });
}
const rows = (DB, table = "oidc_codes") => {
  try { return DB.raw.prepare(`SELECT * FROM ${table}`).all(); } catch { return []; }
};
function kv() {
  const m = new Map();
  return { m, get: async (k) => (m.has(k) ? m.get(k) : null), put: async (k, v) => { m.set(k, v); }, delete: async (k) => { m.delete(k); } };
}
const USERS = {
  hana: { email_sha256: hex("hunter@example.org"), roles: ["hunter"] },
  boss: { email_sha256: hex("owner@example.org"), roles: ["owner"] },
  ada: { email_sha256: hex("admin@example.org"), roles: ["admin"] },
  rex: { email_sha256: hex("gone@example.org"), roles: ["hunter"], revoked_on: "2026-11-01" },
};
let usersDown = false;
const ASSETS = { fetch: async (u) => {
  if (usersDown) throw new Error("assets down");
  const p = new URL(String(u)).pathname;
  if (p === "/admin/users.json") return new Response(JSON.stringify(USERS), { status: 200 });
  return new Response("no", { status: 404 });
} };
const envOf = (extra = {}) => {
  const e = { SESSION_SECRET, OIDC_SIGNING_JWK: JSON.stringify(CUR), OIDC_CLIENT_ID: CLIENT_ID,
    OIDC_CLIENT_SECRET: CLIENT_SECRET, DB: d1(), ALERTS: kv(), ASSETS, RESEND_KEY: "re_harness", ...extra };
  for (const k of Object.keys(e)) if (e[k] === undefined) delete e[k];
  return e;
};

/* --- a browser, and Access ---------------------------------------------------- */
function req(path, { method = "GET", host = "sledjobs.com", cookies = {}, headers = {}, body, form, ip = "198.51.100.20", origin = null } = {}) {
  const hd = new Headers(headers);
  const jar = Object.entries(cookies).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`);
  if (jar.length) hd.set("Cookie", jar.join("; "));
  if (origin === "same") { hd.set("Origin", `https://${host}`); hd.set("Sec-Fetch-Site", "same-origin"); }
  hd.set("cf-connecting-ip", ip);
  let b = body;
  if (form) { hd.set("content-type", "application/x-www-form-urlencoded"); b = new URLSearchParams(form).toString(); }
  return new Request(`https://${host}${path}`, { method, headers: hd, body: b });
}
async function see(res) {
  const set = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  const jar = {};
  for (const c of set) { const [kv0] = c.split(";"); const i = kv0.indexOf("="); jar[kv0.slice(0, i)] = kv0.slice(i + 1); }
  let body = "";
  try { body = await res.text(); } catch { body = ""; }
  let json = null;
  try { json = JSON.parse(body); } catch { json = null; }
  return { status: res.status, location: res.headers.get("location") || "", set, jar, body,
    json, headers: Object.fromEntries(res.headers) };
}
const pkce = () => { const verifier = randomBytes(32).toString("base64url"); return { verifier, challenge: b64u(sha(verifier)) }; };
const ask = (over = {}) => ({ client_id: CLIENT_ID, redirect_uri: CALLBACK, response_type: "code", scope: "openid email",
  state: "st-harness", nonce: "n-harness", code_challenge: pkce().challenge, code_challenge_method: "S256", ...over });
/* null drops a parameter, an array sends it more than once */
function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined) continue;
    for (const x of Array.isArray(v) ? v : [v]) sp.append(k, x);
  }
  return sp.toString();
}
const authz = async (env, params, opts = {}) =>
  see(await authorize.onRequestGet({ request: req(`/oidc/authorize?${qs(params)}`, opts), env }));
const authzAt = async (env, path, opts = {}) => see(await authorize.onRequestGet({ request: req(path, opts), env }));
const unhtml = (s) => String(s).replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
function onward(body) {
  const m = /<meta http-equiv="refresh" content="0;url=([^"]*)">/.exec(body);
  const a = /<a class="btn" href="([^"]*)">Continue to Job Hunter<\/a>/.exec(body);
  return { refresh: m ? unhtml(m[1]) : "", link: a ? unhtml(a[1]) : "" };
}
const codeIn = (r) => { try { return new URL(onward(r.body).refresh).searchParams.get("code") || ""; } catch { return ""; } };
/* Each token call comes from its own network (198.18.0.0/15, the testing
 * range) unless a case names one: only the rate-limit cases share a caller. */
let IP_N = 0;
const nextIp = () => { IP_N++; return `198.18.${Math.floor(IP_N / 250)}.${(IP_N % 250) + 1}`; };
async function tok(env, form, { basic = null, ip = nextIp(), ctype = "application/x-www-form-urlencoded", raw = null, method = "POST", host = "sledjobs.com" } = {}) {
  const headers = { "content-type": ctype };
  if (basic) headers.authorization = basic;
  const body = raw !== null ? raw : (form ? qs(form) : "");
  const r = req("/oidc/token", { method, host, headers, body: method === "GET" ? undefined : body, ip });
  return see(await (method === "GET" ? token.onRequestGet({ request: r, env }) : token.onRequestPost({ request: r, env })));
}
const basicOf = (id, secret) => "Basic " + Buffer.from(`${id}:${secret}`).toString("base64");
const goodForm = (code, verifier, over = {}) => ({ grant_type: "authorization_code", code, redirect_uri: CALLBACK,
  code_verifier: verifier, client_id: CLIENT_ID, client_secret: CLIENT_SECRET, ...over });
const sessionFor = async (env, email, method = "email") =>
  (await S.sessionCookie(env, email, method)).split(";")[0].split("=").slice(1).join("=");
async function jwksOf(env) {
  const r = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"), env }));
  return r.json && r.json.keys ? r.json.keys : [];
}
function verifyJwt(jwt, keys) {
  const [h, b, s] = String(jwt).split(".");
  let head = null, claims = null;
  try { head = JSON.parse(Buffer.from(h, "base64url")); claims = JSON.parse(Buffer.from(b, "base64url")); } catch { return { ok: false }; }
  const k = keys.find((x) => x.kid === head.kid);
  let ok = false;
  try {
    ok = !!k && nodeVerify("sha256", Buffer.from(`${h}.${b}`),
      { key: createPublicKey({ key: k, format: "jwk" }), dsaEncoding: "ieee-p1363" }, Buffer.from(s, "base64url"));
  } catch { ok = false; }
  return { ok, head, claims, sig_bytes: Buffer.from(s, "base64url").length };
}
/* a code for `email`, signed in by our own sign-in, ready for the token door */
async function codeFor(env, email = "hunter@example.org", over = {}) {
  const p = pkce();
  const r = await authz(env, ask({ code_challenge: p.challenge, ...over }), { cookies: { [S.SESSION]: await sessionFor(env, email) } });
  return { code: codeIn(r), verifier: p.verifier, r };
}
const tamper = (jwt) => { const parts = jwt.split("."); const s = parts[2]; parts[2] = (s[0] === "A" ? "B" : "A") + s.slice(1); return parts.join("."); };

const out = { gated: G.GATED };

/* --- the discovery document and the keys ----------------------------------- */
{
  const env = envOf();
  const d = await see(await disco.onRequestGet({ request: req("/.well-known/openid-configuration"), env }));
  out.discovery = { status: d.status, type: d.headers["content-type"], cache: d.headers["cache-control"], body: d.json };
  const dw = await see(await disco.onRequestGet({ request: req("/.well-known/openid-configuration", { host: "www.sledjobs.com" }), env }));
  out.discovery_www = { status: dw.status, location: dw.location };
  const j = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"), env }));
  out.jwks = { status: j.status, type: j.headers["content-type"], cache: j.headers["cache-control"], keys: j.json && j.json.keys,
    has_d: /"d"\s*:/.test(j.body), kid: CUR.kid };
  const jo = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"), env: envOf({ OIDC_SIGNING_JWK_OLD: JSON.stringify(OLDK) }) }));
  out.jwks_old = { status: jo.status, kids: (jo.json && jo.json.keys || []).map((k) => k.kid), has_d: /"d"\s*:/.test(jo.body) };
  const same = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"), env: envOf({ OIDC_SIGNING_JWK_OLD: JSON.stringify(CUR) }) }));
  out.jwks_same_old = { kids: (same.json && same.json.keys || []).map((k) => k.kid) };
  const pairBad = { ...CUR, d: OTHER.d, kid: "mismatched" };
  const noD = { ...CUR }; delete noD.d;
  const cases = { none: undefined, garbled: "{not json", rsa: JSON.stringify({ kty: "RSA", kid: "r", n: RSA_JWK.n, e: RSA_JWK.e }),
    p384: JSON.stringify({ ...CUR, crv: "P-384" }), no_kid: JSON.stringify({ ...CUR, kid: undefined }),
    alg_rs: JSON.stringify({ ...CUR, alg: "RS256" }), public_only: JSON.stringify(noD), mismatched_pair: JSON.stringify(pairBad) };
  out.jwks_off = {};
  for (const [k, v] of Object.entries(cases)) {
    const r = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"), env: envOf({ OIDC_SIGNING_JWK: v }) }));
    out.jwks_off[k] = { status: r.status, cache: r.headers["cache-control"] || "", keys: r.json && r.json.keys ? r.json.keys.length : 0,
      body: r.json };
  }
  // AN EARLIER KEY NEVER STANDS ALONE (review, 2026-11-23): the signing key
  // missing, garbled, half a pair or public only, with OIDC_SIGNING_JWK_OLD
  // set, publishes nothing at all
  out.jwks_off_with_old = {};
  for (const [k, v] of Object.entries({ none: undefined, garbled: "{not json", mismatched_pair: JSON.stringify(pairBad),
    public_only: JSON.stringify(noD) })) {
    const r = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"),
      env: envOf({ OIDC_SIGNING_JWK: v, OIDC_SIGNING_JWK_OLD: JSON.stringify(OLDK) }) }));
    out.jwks_off_with_old[k] = { status: r.status, cache: r.headers["cache-control"] || "",
      keys: r.json && r.json.keys ? r.json.keys.length : 0, body: r.json };
  }
  // THE EARLIER KEY AS THE OWNER CAN HAVE IT: /oidc/jwks's own answer from
  // before the rotation, pasted whole or its one entry (Pages never shows a
  // secret again, so the old private half cannot be copied across); a
  // private one pasted anyway still publishes only its public half
  const printed = (await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"),
    env: envOf({ OIDC_SIGNING_JWK: JSON.stringify(OLDK) }) }))).body;
  const entry = JSON.parse(printed).keys[0];
  out.jwks_old_forms = {};
  for (const [k, v] of Object.entries({ document: printed, entry: JSON.stringify(entry), private: JSON.stringify(OLDK),
    two_keys: JSON.stringify({ keys: [entry, { ...entry, kid: "test-key-twin" }] }), no_keys: '{"keys":[]}',
    keys_not_list: JSON.stringify({ keys: entry }) })) {
    const r = await see(await jwksMod.onRequestGet({ request: req("/oidc/jwks"), env: envOf({ OIDC_SIGNING_JWK_OLD: v }) }));
    out.jwks_old_forms[k] = { status: r.status, kids: (r.json && r.json.keys || []).map((x) => x.kid), has_d: /"d"\s*:/.test(r.body) };
  }
}

/* --- the round trip, signed in by the site's own sign-in ------------------- */
const STATE = "st8 a+b/c=d&e%f?g#h~i";
{
  const env = envOf();
  const sess = await sessionFor(env, "hunter@example.org");
  const sessIat = sec();
  clock += 90 * 1000;    // the session was made a while ago: auth_time is ITS time
  const p = pkce();
  const a = await authz(env, ask({ state: STATE, nonce: "n-0S6_WzA2Mj", code_challenge: p.challenge }), { cookies: { [S.SESSION]: sess } });
  const on = onward(a.body);
  const dest = new URL(on.refresh || "https://x.invalid/");
  const code = dest.searchParams.get("code") || "";
  const stored = rows(env.DB);
  out.round = { status: a.status, location: a.location, cache: a.headers["cache-control"], xfo: a.headers["x-frame-options"],
    referrer: a.headers["referrer-policy"], refresh: on.refresh, link: on.link, script: /<script/i.test(a.body),
    form: /<form/i.test(a.body), dest: dest.origin + dest.pathname, keys: [...dest.searchParams.keys()],
    state: dest.searchParams.get("state"), code_len: code.length,
    stored: stored.length, stored_hash_ok: stored.length === 1 && stored[0].code_hash === b64u(sha(code)),
    stored_raw_code: JSON.stringify(stored).includes(code), stored_email: stored[0] && stored[0].email };
  const t = await tok(env, goodForm(code, p.verifier));
  const keys = await jwksOf(env);
  const v = verifyJwt(t.json && t.json.id_token, keys);
  out.round.token = { status: t.status, cache: t.headers["cache-control"], pragma: t.headers["pragma"],
    keys: t.json ? Object.keys(t.json).sort() : [], token_type: t.json && t.json.token_type,
    expires_in: t.json && t.json.expires_in, access_len: t.json && String(t.json.access_token || "").length };
  out.round.id = { ok: v.ok, head: v.head, claims: v.claims, sig_bytes: v.sig_bytes, now: sec(), session_iat: sessIat,
    sub_want: b64u(sha("sub:hunter@example.org")), tampered_ok: t.json ? verifyJwt(tamper(t.json.id_token), keys).ok : null };
  out.round.id_token = t.json && t.json.id_token;
  out.round.jwks = keys;
  out.round.rows_after = rows(env.DB).length;
  // the same code again
  const again = await tok(env, goodForm(code, p.verifier));
  out.round.reused = { status: again.status, json: again.json };
}

/* client_secret_basic, the other way Access may authenticate */
{
  const env = envOf();
  const c = await codeFor(env);
  const f = goodForm(c.code, c.verifier); delete f.client_id; delete f.client_secret;
  const t = await tok(env, f, { basic: basicOf(CLIENT_ID, CLIENT_SECRET) });
  const v = verifyJwt(t.json && t.json.id_token, await jwksOf(env));
  out.basic = { status: t.status, ok: v.ok, email: v.claims && v.claims.email };
  // basic, with the same client_id repeated in the body (allowed)
  const c2 = await codeFor(env);
  const f2 = goodForm(c2.code, c2.verifier); delete f2.client_secret;
  out.basic_with_id = (await tok(env, f2, { basic: basicOf(CLIENT_ID, CLIENT_SECRET) })).status;
}

/* THE OWNER SIGNED IN THROUGH ACCESS ONLY: signedIn() sees him, auth_time is Access's */
{
  const env = envOf();
  const iat = sec() - 300;
  const p = pkce();
  const a = await authz(env, ask({ code_challenge: p.challenge }), { cookies: { CF_Authorization: accessToken("owner@example.org", iat) } });
  const t = await tok(env, goodForm(codeIn(a), p.verifier));
  const v = verifyJwt(t.json && t.json.id_token, await jwksOf(env));
  out.access_owner = { status: a.status, token: t.status, ok: v.ok, email: v.claims && v.claims.email,
    auth_time: v.claims && v.claims.auth_time, want_auth_time: iat };
  const s = await authz(env, ask(), { cookies: { [S.SESSION]: await sessionFor(env, "owner@example.org") } });
  out.session_owner = { status: s.status, code: codeIn(s).length };
}

/* --- authorize refuses, and never sends anybody anywhere ------------------- */
{
  const env = envOf();
  const sess = await sessionFor(env, "hunter@example.org");
  const bad = {
    wrong_client: { client_id: "someone-else" }, no_client: { client_id: null },
    client_twice: { client_id: [CLIENT_ID, CLIENT_ID] },
    redirect_other_host: { redirect_uri: "https://evil.example/cdn-cgi/access/callback" },
    redirect_other_team: { redirect_uri: "https://other-team.cloudflareaccess.com/cdn-cgi/access/callback" },
    redirect_trailing: { redirect_uri: CALLBACK + "/" }, redirect_query: { redirect_uri: CALLBACK + "?next=evil.example" },
    redirect_http: { redirect_uri: CALLBACK.replace("https:", "http:") },
    redirect_case: { redirect_uri: CALLBACK.replace("callback", "Callback") },
    redirect_userinfo: { redirect_uri: CALLBACK.replace("https://", "https://evil.example@") },
    redirect_port: { redirect_uri: CALLBACK.replace(".com/", ".com:443/") },
    redirect_missing: { redirect_uri: null },
    redirect_twice: { redirect_uri: [CALLBACK, "https://evil.example/cb"] },
    redirect_twice_rev: { redirect_uri: ["https://evil.example/cb", CALLBACK] },
    type_token: { response_type: "token" }, type_hybrid: { response_type: "code id_token" }, type_missing: { response_type: null },
    scope_no_openid: { scope: "email" }, scope_missing: { scope: null },
    pkce_missing: { code_challenge: null, code_challenge_method: null }, pkce_no_challenge: { code_challenge: null },
    pkce_plain: { code_challenge_method: "plain" }, pkce_no_method: { code_challenge_method: null },
    pkce_short: { code_challenge: "a".repeat(42) }, pkce_long: { code_challenge: "a".repeat(44) },
    pkce_chars: { code_challenge: "+".repeat(43) },
    mode_form_post: { response_mode: "form_post" }, mode_fragment: { response_mode: "fragment" },
    state_long: { state: "s".repeat(O.STATE_MAX + 1) }, state_control: { state: "a\u0001b" }, state_empty: { state: "" },
    state_twice: { state: ["a", "b"] }, nonce_long: { nonce: "n".repeat(O.NONCE_MAX + 1) },
    query_long: { padding: "p".repeat(9000) },
  };
  out.refused = {};
  for (const [k, over] of Object.entries(bad)) {
    const before = rows(env.DB).length;
    const r = await authz(env, ask(over), { cookies: { [S.SESSION]: sess } });
    out.refused[k] = { status: r.status, location: r.location, refresh: /http-equiv="refresh"/i.test(r.body),
      callback: r.body.includes("cloudflareaccess.com"), evil: r.body.includes("evil.example"),
      code: rows(env.DB).length - before, set: r.set.filter((c) => !c.startsWith("__Host-sj_oidc=;")),
      cache: r.headers["cache-control"], xfo: r.headers["x-frame-options"] };
  }
  // the controls: the same requests, right, mint
  const ok = await authz(env, ask(), { cookies: { [S.SESSION]: sess } });
  out.refused_control = { status: ok.status, code: codeIn(ok).length };
  const okNoState = await authz(env, ask({ state: null, nonce: null }), { cookies: { [S.SESSION]: sess } });
  out.no_state = { status: okNoState.status, keys: [...new URL(onward(okNoState.body).refresh || "https://x.invalid/").searchParams.keys()] };
  const okMode = await authz(env, ask({ response_mode: "query", scope: "openid email profile" }), { cookies: { [S.SESSION]: sess } });
  out.mode_query = { status: okMode.status };
  const www = await authz(env, ask(), { host: "www.sledjobs.com", cookies: { [S.SESSION]: sess } });
  out.authorize_www = { status: www.status, location: www.location };
}

/* --- not signed in: to the sign-in and back, for real ----------------------- */
{
  const env = envOf();
  const p = pkce();
  const LONG = "L".repeat(1900) + "-end";
  const a = await authz(env, ask({ state: LONG, code_challenge: p.challenge }), {});
  const loc = new URL(a.location || "/", "https://sledjobs.com");
  const to = loc.searchParams.get("to") || "";
  const held = a.jar[authorize.REQUEST] || "";
  out.detour = { status: a.status, location: a.location, path: loc.pathname, to, to_len: to.length,
    location_len: a.location.length, set: a.set.map((c) => c.replace(/=[^;]*/, "=<value>")),
    held_len: held.length, code: rows(env.DB).length };
  // the sign-in page keeps that `to`
  const pg = await see(await signinPage.onRequestGet({ request: req(a.location), env }));
  out.detour.page_to = unhtml((/name="to" value="([^"]*)"/.exec(pg.body) || [])[1] || "");
  // the email, and the code typed on this device
  const em = await see(await signinEmail.onRequestPost({ request: req("/signin/email", { method: "POST", origin: "same",
    form: { email: "hunter@example.org", to } }), env }));
  const mail = mails[mails.length - 1] || {};
  const code = (String(mail.text || "").match(/\b([0-9A-HJKMNP-TV-Z]{5})-([0-9A-HJKMNP-TV-Z]{5})\b/) || []).slice(1).join("");
  const cd = await see(await signinCode.onRequestPost({ request: req("/signin/code", { method: "POST", origin: "same",
    cookies: { [S.PENDING]: em.jar[S.PENDING] }, form: { code } }), env }));
  out.detour.mailed = em.status;
  out.detour.after_code = { status: cd.status, location: cd.location };
  const sess = cd.jar[S.SESSION];
  const back = await authzAt(env, cd.location || "/oidc/authorize", { cookies: { [S.SESSION]: sess, [authorize.REQUEST]: held } });
  const dest = new URL(onward(back.body).refresh || "https://x.invalid/");
  out.detour.back = { status: back.status, dest: dest.origin + dest.pathname, state_kept: dest.searchParams.get("state") === LONG,
    dropped: back.set.some((c) => c.startsWith(`${authorize.REQUEST}=;`) && /Max-Age=0/.test(c)) };
  const t = await tok(env, goodForm(dest.searchParams.get("code") || "", p.verifier));
  out.detour.token = t.status;
  // AND EVERY WAY BACK THAT IS NOT THIS BROWSER'S OWN
  const ref = new URL(cd.location || "/", "https://sledjobs.com").searchParams.get("r") || "";
  const otherRef = randomBytes(16).toString("base64url");
  const sealedSession = sess;    // a value sealed for another purpose
  const flip = (s) => (s ? (s[0] === "A" ? "B" : "A") + s.slice(1) : s);
  const tries = {
    ref_mismatch: [`/oidc/authorize?r=${otherRef}`, held],
    no_cookie: [`/oidc/authorize?r=${ref}`, null],
    tampered: [`/oidc/authorize?r=${ref}`, flip(held)],
    other_purpose: [`/oidc/authorize?r=${ref}`, sealedSession],
    extra_params: [`/oidc/authorize?r=${ref}&redirect_uri=${encodeURIComponent("https://evil.example/cb")}`, held],
    bad_ref: ["/oidc/authorize?r=abc", held],
    two_refs: [`/oidc/authorize?r=${ref}&r=${ref}`, held],
  };
  out.resume_refused = {};
  for (const [k, [path, cookie]] of Object.entries(tries)) {
    const before = rows(env.DB).length;
    const r = await authzAt(env, path, { cookies: { [S.SESSION]: sess, [authorize.REQUEST]: cookie } });
    out.resume_refused[k] = { status: r.status, location: r.location, refresh: /http-equiv="refresh"/i.test(r.body),
      evil: r.body.includes("evil.example"), code: rows(env.DB).length - before };
  }
  // the held request ten minutes on
  const p2 = pkce();
  const a2 = await authz(env, ask({ code_challenge: p2.challenge }), {});
  const ref2 = new URL(new URL(a2.location, "https://sledjobs.com").searchParams.get("to"), "https://sledjobs.com").searchParams.get("r");
  clock += 11 * 60 * 1000;
  const late = await authzAt(env, `/oidc/authorize?r=${ref2}`, { cookies: { [S.SESSION]: await sessionFor(env, "hunter@example.org"),
    [authorize.REQUEST]: a2.jar[authorize.REQUEST] } });
  out.resume_refused.expired = { status: late.status, location: late.location, refresh: /http-equiv="refresh"/i.test(late.body) };
  // a state that would not fit a cookie once sealed is refused, never half-held
  const fat = await authz(env, ask({ state: '"'.repeat(O.STATE_MAX) }), {});
  out.detour_too_big = { status: fat.status, location: fat.location, set: fat.set.filter((c) => !c.includes("=;")) };
}

/* --- signed in, but not given Job Hunter ----------------------------------- */
{
  const env = envOf();
  out.no_role = {};
  for (const [k, email] of [["not_on_board", "plain@example.org"], ["admin_only", "admin@example.org"], ["revoked", "gone@example.org"]]) {
    const r = await authz(env, ask(), { cookies: { [S.SESSION]: await sessionFor(env, email) } });
    out.no_role[k] = { status: r.status, location: r.location, refresh: /http-equiv="refresh"/i.test(r.body),
      beta: r.body.includes("closed beta"), names: r.body.includes(email), to_beta: r.body.includes('href="/beta/"'),
      code: rows(env.DB).length };
  }
  usersDown = true;
  const r = await authz(env, ask(), { cookies: { [S.SESSION]: await sessionFor(env, "hunter@example.org") } });
  usersDown = false;
  out.no_role.users_down = { status: r.status, refresh: /http-equiv="refresh"/i.test(r.body), code: rows(env.DB).length };
}

/* --- not set up: every door says so and does nothing ----------------------- */
{
  const offs = {
    no_client_id: { OIDC_CLIENT_ID: undefined }, odd_client_id: { OIDC_CLIENT_ID: "has space" },
    // 31: the spec's floor is 32 characters, written here, not read off the module
    short_secret: { OIDC_CLIENT_SECRET: "s".repeat(31) }, no_secret: { OIDC_CLIENT_SECRET: undefined },
    no_key: { OIDC_SIGNING_JWK: undefined }, garbled_key: { OIDC_SIGNING_JWK: "{" },
    mismatched_key: { OIDC_SIGNING_JWK: JSON.stringify({ ...CUR, d: OTHER.d }) },
    no_db: { DB: undefined },
    override_http: { OIDC_REDIRECT_URI: "http://plain.example/cb" }, override_junk: { OIDC_REDIRECT_URI: "not a url" },
    override_fragment: { OIDC_REDIRECT_URI: "https://ok.example/cb#x" },
    override_unprinted: { OIDC_REDIRECT_URI: "https://OK.example/cb" },
  };
  out.not_set_up = {};
  for (const [k, extra] of Object.entries(offs)) {
    const env = envOf(extra);
    const sess = await sessionFor(envOf(), "hunter@example.org");
    const a = await authz(env, ask(), { cookies: { [S.SESSION]: sess } });
    const t = await tok(env, goodForm("x".repeat(43), "v".repeat(43)));
    out.not_set_up[k] = { authorize: a.status, refresh: /http-equiv="refresh"/i.test(a.body), token: t.status, error: t.json && t.json.error };
  }
  const noSession = envOf({ SESSION_SECRET: undefined });
  out.not_set_up.no_session_secret = { authorize: (await authz(noSession, ask())).status };
  // and a secret of exactly 32 characters is enough
  {
    const s32 = "t".repeat(32), env = envOf({ OIDC_CLIENT_SECRET: s32 });
    const c = await codeFor(env);
    out.secret_32 = (await tok(env, goodForm(c.code, c.verifier, { client_secret: s32 }))).status;
  }
  // A GOOD OVERRIDE REPLACES the callback and widens nothing
  const OVR = "https://*.cloudflareaccess.com/cdn-cgi/access/callback";
  const env = envOf({ OIDC_REDIRECT_URI: OVR });
  const sess = await sessionFor(env, "hunter@example.org");
  const r1 = await authz(env, ask({ redirect_uri: OVR }), { cookies: { [S.SESSION]: sess } });
  const r2 = await authz(env, ask({ redirect_uri: CALLBACK }), { cookies: { [S.SESSION]: sess } });
  const r3 = await authz(env, ask({ redirect_uri: "https://x.cloudflareaccess.com/cdn-cgi/access/callback" }), { cookies: { [S.SESSION]: sess } });
  out.override = { exact: r1.status, dest: (onward(r1.body).refresh || "").split("?")[0], default_refused: r2.status, pattern_refused: r3.status };
}

/* --- the token door refuses, with OAuth's word and nothing else ------------- */
{
  const env = envOf();
  const T = {};
  const one = async (k, fn) => { T[k] = await fn(); };
  const pick = (r) => ({ status: r.status, json: r.json, cache: r.headers["cache-control"] || "",
    www: r.headers["www-authenticate"] || "" });
  await one("wrong_secret_post", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier, { client_secret: CLIENT_SECRET + "x" }))); });
  await one("wrong_secret_basic", async () => { const c = await codeFor(env); const f = goodForm(c.code, c.verifier); delete f.client_id; delete f.client_secret;
    return pick(await tok(env, f, { basic: basicOf(CLIENT_ID, "wrong-" + CLIENT_SECRET) })); });
  await one("wrong_client_id", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier, { client_id: "another-client" }))); });
  await one("no_credentials", async () => { const c = await codeFor(env); const f = goodForm(c.code, c.verifier); delete f.client_id; delete f.client_secret; return pick(await tok(env, f)); });
  await one("both_methods", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier), { basic: basicOf(CLIENT_ID, CLIENT_SECRET) })); });
  await one("basic_garbled", async () => { const c = await codeFor(env); const f = goodForm(c.code, c.verifier); delete f.client_secret; return pick(await tok(env, f, { basic: "Basic !!!" })); });
  await one("bearer_header", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier), { basic: "Bearer abc" })); });
  await one("basic_other_body_id", async () => { const c = await codeFor(env); const f = goodForm(c.code, c.verifier, { client_id: "another-client" }); delete f.client_secret;
    return pick(await tok(env, f, { basic: basicOf(CLIENT_ID, CLIENT_SECRET) })); });
  await one("grant_password", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier, { grant_type: "password" }))); });
  await one("wrong_verifier", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, pkce().verifier))); });
  await one("no_verifier", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, null))); });
  await one("verifier_bad_chars", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier.slice(0, 42) + "+"))); });
  await one("verifier_is_challenge", async () => { const p = pkce(); const r = await authz(env, ask({ code_challenge: p.challenge }), { cookies: { [S.SESSION]: await sessionFor(env, "hunter@example.org") } });
    return pick(await tok(env, goodForm(codeIn(r), p.challenge))); });
  await one("wrong_redirect", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier, { redirect_uri: "https://evil.example/cb" }))); });
  await one("no_redirect", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier, { redirect_uri: null }))); });
  await one("unknown_code", async () => pick(await tok(env, goodForm(randomBytes(32).toString("base64url"), pkce().verifier))));
  await one("malformed_code", async () => pick(await tok(env, goodForm("x", pkce().verifier))));
  await one("no_code", async () => pick(await tok(env, goodForm(null, pkce().verifier))));
  await one("not_form", async () => { const c = await codeFor(env); return pick(await tok(env, null, { ctype: "application/json", raw: JSON.stringify(goodForm(c.code, c.verifier)) })); });
  await one("code_twice", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm([c.code, c.code], c.verifier))); });
  await one("big_body", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier, { pad: "p".repeat(9000) }))); });
  await one("get", async () => pick(await tok(env, null, { method: "GET" })));
  await one("off_site", async () => { const c = await codeFor(env); return pick(await tok(env, goodForm(c.code, c.verifier), { host: "www.sledjobs.com" })); });
  // A FAILED TRY SPENDS THE CODE: the right verifier after a wrong one
  await one("spent_by_a_wrong_try", async () => {
    const c = await codeFor(env);
    await tok(env, goodForm(c.code, pkce().verifier));
    return pick(await tok(env, goodForm(c.code, c.verifier)));
  });
  // THE CODE'S CLIENT: minted for one client, traded after the client changed
  await one("client_changed", async () => {
    const c = await codeFor(env);
    const env2 = { ...env, OIDC_CLIENT_ID: "jh-another-client", OIDC_CLIENT_SECRET: "q".repeat(40) };
    return pick(await tok(env2, goodForm(c.code, c.verifier, { client_id: "jh-another-client", client_secret: "q".repeat(40) })));
  });
  // TWO REDEMPTIONS AT ONCE: exactly one ID token, as they come, and with
  // both made to reach the UPDATE before either reads the row
  {
    const c = await codeFor(env);
    const both = await Promise.all([tok(env, goodForm(c.code, c.verifier)), tok(env, goodForm(c.code, c.verifier))]);
    T.race = { statuses: both.map((r) => r.status).sort() };
    const c2 = await codeFor(env);
    env.DB.holdSpends(3);
    const three = await Promise.all([1, 2, 3].map(() => tok(env, goodForm(c2.code, c2.verifier))));
    T.race_interleaved = { statuses: three.map((r) => r.status).sort() };
  }
  // EXPIRY: a code is good 120 seconds, and not 121
  {
    const c = await codeFor(env);
    clock += 120 * 1000;
    T.at_120 = { status: (await tok(env, goodForm(c.code, c.verifier))).status };
    const c2 = await codeFor(env);
    clock += 121 * 1000;
    T.at_121 = pick(await tok(env, goodForm(c2.code, c2.verifier)));
  }
  // D1 down: no token, and nothing pretends
  {
    const c = await codeFor(env);
    env.DB.down = true;
    T.db_down = pick(await tok(env, goodForm(c.code, c.verifier)));
    env.DB.down = false;
  }
  out.token_refused = T;
}

/* --- a request that is not the client is counted, per network; the client
 * never is (review, 2026-11-24) -------------------------------------------- */
const fails = (env) => rows(env.DB, "oidc_fails").map((r) => r.n);
const junkCode = () => randomBytes(32).toString("base64url");
{
  const env = envOf();
  const A = "192.0.2.77", B = "192.0.2.88";
  // twenty wrong secrets from one network: each counted, each invalid_client
  const statuses = [];
  for (let i = 0; i < O.FAILS_PER_HOUR; i++) {
    statuses.push((await tok(env, goodForm(junkCode(), pkce().verifier, { client_secret: "nope" + i }), { ip: A })).status);
  }
  const failsAtCap = fails(env);
  // the next one is refused, unread, and writes nothing
  const capped = await tok(env, goodForm(junkCode(), pkce().verifier, { client_secret: "nope-over" }), { ip: A });
  const failsAfter = fails(env);
  const again = await tok(env, goodForm(junkCode(), pkce().verifier, { client_id: null, client_secret: null }), { ip: A });
  // THE CLIENT, from that network with its hour used up: a token, no try
  const c = await codeFor(env);
  const client = await tok(env, goodForm(c.code, c.verifier), { ip: A });
  const clientOk = verifyJwt(client.json && client.json.id_token, await jwksOf(env)).ok;
  const failsAfterClient = fails(env);
  // and the client's own failures from there: OAuth's word, never 429, never counted
  const cv = await codeFor(env);
  const clientFails = {
    unknown_code: (await tok(env, goodForm(junkCode(), pkce().verifier), { ip: A })).status,
    wrong_verifier: (await tok(env, goodForm(cv.code, pkce().verifier), { ip: A })).status,
    grant_password: (await tok(env, goodForm(junkCode(), pkce().verifier, { grant_type: "password" }), { ip: A })).status,
  };
  const failsAfterClientFails = fails(env);
  // another network is its own
  const other = await tok(env, goodForm(junkCode(), pkce().verifier, { client_secret: "nope" }), { ip: B });
  // a working sign-in writes nothing
  const before = JSON.stringify(rows(env.DB, "oidc_fails"));
  const c2 = await codeFor(env);
  const good = await tok(env, goodForm(c2.code, c2.verifier), { ip: "192.0.2.99" });
  const afterGood = JSON.stringify(rows(env.DB, "oidc_fails"));
  // IPv6: one /64 is one network for the count, and the client is never refused in it
  const v6 = envOf();
  for (let i = 0; i < O.FAILS_PER_HOUR; i++) await tok(v6, goodForm("x", "y", { client_secret: "no" }), { ip: `2001:db8:1:2::${(i + 1).toString(16)}` });
  const v6Neighbour = await tok(v6, goodForm("x", "y", { client_secret: "no" }), { ip: "2001:db8:1:2:ffff::9" });
  const v6Other = await tok(v6, goodForm("x", "y", { client_secret: "no" }), { ip: "2001:db8:1:3::1" });
  const c3 = await codeFor(v6);
  const v6Client = await tok(v6, goodForm(c3.code, c3.verifier), { ip: "2001:db8:1:2:ffff::9" });
  // D1 down: a request that is not the client is still refused, never let through
  env.DB.down = true;
  const downNotClient = await tok(env, goodForm(junkCode(), pkce().verifier, { client_secret: "nope-down" }), { ip: "192.0.2.111" });
  env.DB.down = false;
  // the next hour, the same network's count starts again
  clock += 3600 * 1000;
  const nextHour = await tok(env, goodForm(junkCode(), pkce().verifier, { client_secret: "nope-later" }), { ip: A });
  out.rate = { statuses, capped: { status: capped.status, json: capped.json, retry: capped.headers["retry-after"] || "" },
    fails_at_cap: failsAtCap, fails_after_refusal: failsAfter, again: again.status,
    client: client.status, client_ok: clientOk, fails_after_client: failsAfterClient,
    client_fails: clientFails, fails_after_client_fails: failsAfterClientFails,
    other_network: other.status, good: good.status, good_wrote: before !== afterGood,
    v6_neighbour: v6Neighbour.status, v6_other: v6Other.status, v6_client: v6Client.status,
    down_not_client: { status: downNotClient.status, json: downNotClient.json },
    next_hour: nextHour.status, cap: O.FAILS_PER_HOUR };
}

/* CLOUDFLARE'S OWN SHARED ADDRESS (review, 2026-11-24). Every Worker's
 * cross-zone subrequest arrives with CF-Connecting-IP 2a06:98c0:3600::103,
 * whoever owns the Worker, and Access calls from Cloudflare too: a count
 * that refused everybody from there refused Access. Twenty-five requests
 * from that address with no or wrong client credentials, then THE client
 * from the same address and from another in its /64: tokens, and no try
 * taken. */
{
  const env = envOf();
  const W = "2a06:98c0:3600::103", W64 = "2a06:98c0:3600::1";
  const junk = [
    (i) => [goodForm(junkCode(), pkce().verifier, { client_id: null, client_secret: null }), {}],
    (i) => [goodForm(junkCode(), pkce().verifier, { client_secret: "wrong-" + i }), {}],
    (i) => { const f = goodForm(junkCode(), pkce().verifier); delete f.client_id; delete f.client_secret;
             return [f, { basic: basicOf(CLIENT_ID, "wrong-" + i) }]; },
    (i) => [goodForm(junkCode(), pkce().verifier, { client_id: "someone-" + i }), {}],
    (i) => { const f = goodForm(junkCode(), pkce().verifier); delete f.client_secret; return [f, { basic: "Basic !!!" }]; },
  ];
  const statuses = [];
  for (let i = 0; i < 25; i++) {
    const [form, opts] = junk[i % junk.length](i);
    statuses.push((await tok(env, form, { ...opts, ip: W })).status);
  }
  const counted = fails(env);
  const c = await codeFor(env);
  const same = await tok(env, goodForm(c.code, c.verifier), { ip: W });
  const sameOk = verifyJwt(same.json && same.json.id_token, await jwksOf(env)).ok;
  const c2 = await codeFor(env);
  const neighbour = await tok(env, goodForm(c2.code, c2.verifier), { ip: W64 });
  const c3 = await codeFor(env);
  const f3 = goodForm(c3.code, c3.verifier); delete f3.client_id; delete f3.client_secret;
  const basicSame = await tok(env, f3, { basic: basicOf(CLIENT_ID, CLIENT_SECRET), ip: W });
  out.shared_caller = { statuses, counted, same: same.status, same_ok: sameOk, neighbour: neighbour.status,
    basic: basicSame.status, counted_after: fails(env) };
}

/* A BURST: two hundred wrong secrets sent at once from one network. Each is
 * counted by one statement once its secret is found wrong, so twenty are
 * counted and the rest refused; a count read first and written after let
 * all two hundred through (2026-11-23 review). THE CLIENT is not held by
 * it: five good exchanges sent in the middle of the burst, from the same
 * network, are five tokens, and so is one after it; five at once from
 * another network leave no count. */
{
  const env = envOf();
  const ip = "203.0.113.9";
  const N = 200;
  const mid = [];
  for (let i = 0; i < 5; i++) mid.push(await codeFor(env));
  env.DB.holdFails(N);
  const sent = [];
  for (let i = 0; i < N; i++) {
    sent.push(tok(env, goodForm(junkCode(), pkce().verifier, { client_secret: "guess-" + i }), { ip }));
    if (i % 40 === 20) sent.push(tok(env, goodForm(mid[i / 40 | 0].code, mid[i / 40 | 0].verifier), { ip }).then((r) => ({ ...r, good: true })));
  }
  const res = await Promise.all(sent);
  const statuses = {}, midStatuses = [];
  for (const r of res) {
    if (r.good) { midStatuses.push(r.status); continue; }
    statuses[r.status] = (statuses[r.status] || 0) + 1;
  }
  const counted = fails(env);
  const c = await codeFor(env);
  const after = await tok(env, goodForm(c.code, c.verifier), { ip });
  const codes = [];
  for (let i = 0; i < 5; i++) codes.push(await codeFor(env));
  const goods = await Promise.all(codes.map((g) => tok(env, goodForm(g.code, g.verifier), { ip: "203.0.113.10" })));
  out.burst = { sent: N, statuses, counted, mid: midStatuses, after: after.status,
    good_statuses: goods.map((r) => r.status), rows_after_goods: fails(env) };
}

/* --- the gate: these four pass signed out, nothing beside them --------------- */
{
  const env = envOf();
  async function gate(path, { method = "GET", host = "sledjobs.com", body, ctype } = {}) {
    let reached = false;
    const headers = {};
    if (ctype) headers["content-type"] = ctype;
    const res = await onRequest({ request: req(path, { method, host, body, headers }), env, data: {},
      next: async () => { reached = true; return new Response("ok", { status: 200, headers: { "content-type": "text/plain" } }); } });
    let json = null;
    try { json = JSON.parse(await res.text()); } catch { json = null; }
    return { status: res.status, reached, error: json && json.error };
  }
  out.gate_open = {
    "/oidc/authorize": await gate(`/oidc/authorize?${qs(ask())}`),
    "/oidc/token": await gate("/oidc/token", { method: "POST", body: "grant_type=authorization_code", ctype: "application/x-www-form-urlencoded" }),
    "/oidc/jwks": await gate("/oidc/jwks"),
    "/.well-known/openid-configuration": await gate("/.well-known/openid-configuration"),
    "/oidc/jwks (alias)": await gate("/oidc/jwks", { host: "solesource-c6g.pages.dev" }),
  };
  out.gate_shut = {};
  for (const p of ["/oidc/token/", "/oidc/authorize/", "/oidc", "/oidc/", "/oidc/other", "/oidc/jwks/x", "/OIDC/token",
                   "/%6fidc/token", "/.well-known/other", "/.well-known/openid-configuration/", "/.well-known",
                   "/api/jh/board-role?id=a::b"]) {
    out.gate_shut[p] = await gate(p);
  }
  out.open_set = [...G.OPEN].sort();
  // the page, through the root middleware and the real handler: framed out
  const sess = await sessionFor(env, "hunter@example.org");
  const r = req(`/oidc/authorize?${qs(ask())}`, { cookies: { [S.SESSION]: sess } });
  const res = await onRequest({ request: r, env, data: {}, next: async () => authorize.onRequestGet({ request: r, env }) });
  out.mw_authorize = { status: res.status, xfo: res.headers.get("x-frame-options") || "",
    csp: res.headers.get("content-security-policy") || "", cache: res.headers.get("cache-control") || "" };
}

out.logged = logged;
out.fetched = [...new Set(fetched)].sort();
process.stdout.write(JSON.stringify(out) + "\n");
