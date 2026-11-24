/* DRIVE THE JOB HUNTER PANEL'S OWN-SITE ROUTE AS A BROWSER AND JOB HUNTER
 * WOULD (owner, 2026-11-23, design C: "the board's server fetches the
 * scoring popout for you with a Cloudflare service token").
 *
 * Imports the real functions/api/jh/board-role.js (and the root middleware,
 * for the gate in front of it), with a P-256 key this file generated, a fake
 * service token, the Users board served as the edge serves it, and a fake
 * fetch standing in for Job Hunter that RECORDS every request the function
 * makes and answers the way Job Hunter, Access or a sleeping Mac would.
 * Prints one JSON object; selftest.py
 * check_the_panel_asks_job_hunter_through_the_board asserts on it, and
 * verifies the assertion's signature again in Python. The only addresses in
 * here are RFC 2606 ones, and every key and token is made here, per run.
 */
import { generateKeyPairSync, createHash, createPublicKey, createSign, randomBytes,
         verify as nodeVerify } from "node:crypto";

let clock = Date.parse("2026-11-23T15:00:00.000Z");
Date.now = () => clock;

/* NOTHING MAY BE LOGGED: not the assertion, not the secret */
const logged = [];
for (const k of ["log", "info", "warn", "error", "debug", "trace"]) {
  console[k] = (...a) => { logged.push(`${k}: ${a.map(String).join(" ").slice(0, 200)}`); };
}
/* The proxy's timer: recorded, and fired at once when a case wants Job
 * Hunter to never answer (nobody waits eight seconds in a test). */
const delays = [];
const realSetTimeout = globalThis.setTimeout;
let fastTimers = false;
globalThis.setTimeout = (fn, ms, ...a) => { delays.push(ms); return realSetTimeout(fn, fastTimers ? 30 : ms, ...a); };

const M = (p) => import(new URL(`../functions/${p}`, import.meta.url));
const { onRequest } = await M("_middleware.js");
const S = await M("_session.js");
const O = await M("_oidc.js");
const px = await M("api/jh/board-role.js");

const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const ACCESS_AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const b64u = (buf) => Buffer.from(buf).toString("base64url");
const hex = (s) => createHash("sha256").update(s).digest("hex");
const KEY = { ...generateKeyPairSync("ec", { namedCurve: "P-256" }).privateKey.export({ format: "jwk" }), kid: "proxy-test-key" };
const RSA = generateKeyPairSync("rsa", { modulusLength: 2048 });
const RSA_JWK = RSA.publicKey.export({ format: "jwk" });
const TOKEN_ID = `harness${randomBytes(8).toString("hex")}.access`;
const TOKEN_SECRET = randomBytes(32).toString("hex");
const SESSION_SECRET = randomBytes(36).toString("base64url");

function accessToken(email) {
  const t = Math.floor(clock / 1000);
  const b = (o) => b64u(JSON.stringify(o));
  const head = b({ alg: "RS256", kid: "a1", typ: "JWT" });
  const body = b({ aud: [ACCESS_AUD], iss: `https://${TEAM}`, iat: t, exp: t + 3600, email });
  return `${head}.${body}.${createSign("RSA-SHA256").update(`${head}.${body}`).sign(RSA.privateKey).toString("base64url")}`;
}

/* --- Job Hunter, as the function meets it ----------------------------------- */
const calls = [];
let mode = "found";
const jsonRes = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const ANSWERS = {
  found: () => jsonRes(200, { found: true, id: "sig-1", org: "Acme Gov", title: "Account Executive", status: "open",
    fit: { as_is: 71, ceiling: 88, review: "accepted" } }),
  missing: () => jsonRes(200, { found: false }),
  no_account: () => jsonRes(401, { error: "no_account", email: "hunter@example.org" }),
  no_token: () => jsonRes(401, { error: "no_token" }),
  refused_sentence: () => jsonRes(401, { error: "that token is not one we issued" }),
  not_found: () => jsonRes(404, { error: "no such endpoint /api/board-role" }),
  bad: () => jsonRes(400, { error: "id must be a SLED JOBS role id" }),
  busy: () => jsonRes(503, { error: "busy" }),
  forbidden_json: () => jsonRes(403, { error: "forbidden" }),
  tunnel_down: () => new Response("<html><body>Bad gateway</body></html>", { status: 502, headers: { "content-type": "text/html" } }),
  access_redirect: () => new Response("", { status: 302, headers: { location: `https://${TEAM}/cdn-cgi/access/login/hunter.example`, "content-type": "text/html" } }),
  access_page: () => new Response("<html><title>Sign in</title></html>", { status: 200, headers: { "content-type": "text/html; charset=utf-8" } }),
  json_array: () => new Response("[1,2,3]", { status: 200, headers: { "content-type": "application/json" } }),
  not_json: () => new Response("{nope", { status: 200, headers: { "content-type": "application/json" } }),
  huge: () => new Response(JSON.stringify({ found: true, pad: "x".repeat(70000) }), { status: 200, headers: { "content-type": "application/json" } }),
  no_type: () => new Response(JSON.stringify({ found: true }), { status: 200, headers: { "content-type": "" } }),
  network: () => { throw new TypeError("fetch failed"); },
};
globalThis.fetch = async (url, init = {}) => {
  const u = String(url);
  if (u === `https://${TEAM}/cdn-cgi/access/certs`) {
    return new Response(JSON.stringify({ keys: [{ kty: "RSA", kid: "a1", n: RSA_JWK.n, e: RSA_JWK.e, alg: "RS256" }] }), { status: 200 });
  }
  const headers = {};
  new Headers(init.headers || {}).forEach((v, k) => { headers[k] = v; });
  calls.push({ url: u, method: init.method || "GET", redirect: init.redirect || "follow", headers,
    signal: !!init.signal, body: init.body === undefined || init.body === null ? null : String(init.body),
    credentials: init.credentials || null, mode });
  if (mode === "slow") {
    return new Promise((_, reject) => {
      if (init.signal) init.signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  }
  return ANSWERS[mode]();
};

const USERS = {
  hana: { email_sha256: hex("hunter@example.org"), roles: ["hunter"] },
  boss: { email_sha256: hex("owner@example.org"), roles: ["owner"] },
  ada: { email_sha256: hex("admin@example.org"), roles: ["admin"] },
  rex: { email_sha256: hex("gone@example.org"), roles: ["hunter"], revoked_on: "2026-11-01" },
};
let usersDown = false;
const ASSETS = { fetch: async (u) => {
  if (usersDown) throw new Error("assets down");
  return new URL(String(u)).pathname === "/admin/users.json"
    ? new Response(JSON.stringify(USERS), { status: 200 }) : new Response("no", { status: 404 });
} };
const ENV = { SESSION_SECRET, OIDC_SIGNING_JWK: JSON.stringify(KEY), JH_SERVICE_TOKEN_ID: TOKEN_ID,
  JH_SERVICE_TOKEN_SECRET: TOKEN_SECRET, ASSETS };
const sessionFor = async (email) => (await S.sessionCookie(ENV, email, "email")).split(";")[0].split("=").slice(1).join("=");
const HUNTER = await sessionFor("hunter@example.org");

function req(path, { host = "sledjobs.com", cookies = { [S.SESSION]: HUNTER }, headers = {}, site = "same-origin" } = {}) {
  const hd = new Headers(headers);
  const jar = Object.entries(cookies).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`);
  if (jar.length) hd.set("Cookie", jar.join("; "));
  if (site !== null) hd.set("Sec-Fetch-Site", site);
  return new Request(`https://${host}${path}`, { headers: hd });
}
async function ask(id, opts = {}, env = ENV) {
  const before = calls.length;
  const path = id === undefined ? "/api/jh/board-role" : `/api/jh/board-role?id=${encodeURIComponent(id)}`;
  const res = await px.onRequestGet({ request: req(opts.path || path, opts), env });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { json = null; }
  return { status: res.status, json, body: text, cache: res.headers.get("cache-control") || "",
    calls: calls.length - before, call: calls.length > before ? calls[calls.length - 1] : null };
}
function verifyJwt(jwt, keys) {
  const [h, b, s] = String(jwt || "").split(".");
  let head = null, claims = null;
  try { head = JSON.parse(Buffer.from(h, "base64url")); claims = JSON.parse(Buffer.from(b, "base64url")); } catch { return { ok: false }; }
  const k = keys.find((x) => x.kid === head.kid);
  let ok = false;
  try {
    ok = !!k && nodeVerify("sha256", Buffer.from(`${h}.${b}`),
      { key: createPublicKey({ key: k, format: "jwk" }), dsaEncoding: "ieee-p1363" }, Buffer.from(s, "base64url"));
  } catch { ok = false; }
  return { ok, head, claims, sig_bytes: Buffer.from(s || "", "base64url").length };
}
const KEYS = await O.publicKeys(ENV);
const ID = "acme-gov::Account Executive, SLED::a1b2c3";
const out = {};

/* --- who may ask ------------------------------------------------------------- */
out.anon = await ask(ID, { cookies: {} });
out.no_role = await ask(ID, { cookies: { [S.SESSION]: await sessionFor("plain@example.org") } });
out.admin_only = await ask(ID, { cookies: { [S.SESSION]: await sessionFor("admin@example.org") } });
out.revoked = await ask(ID, { cookies: { [S.SESSION]: await sessionFor("gone@example.org") } });
usersDown = true;
out.users_down = await ask(ID);
usersDown = false;
out.cross_site = await ask(ID, { site: "cross-site" });
out.same_site = await ask(ID, { site: "same-site" });
out.typed = await ask(ID, { site: "none" });
out.no_fetch_meta = await ask(ID, { site: null });
out.www = await ask(ID, { host: "www.sledjobs.com" });
out.alias = await ask(ID, { host: "solesource-c6g.pages.dev" });
out.access_owner = await ask(ID, { cookies: { CF_Authorization: accessToken("owner@example.org") } });
out.access_owner_email = out.access_owner.call ? verifyJwt(out.access_owner.call.headers["x-sj-assertion"], KEYS).claims.email : null;

/* --- what may be asked ---------------------------------------------------------- */
out.bad_id = {};
for (const [k, path] of Object.entries({
  missing: "/api/jh/board-role", empty: "/api/jh/board-role?id=", no_colons: "/api/jh/board-role?id=acme",
  too_long: `/api/jh/board-role?id=${encodeURIComponent("a::" + "b".repeat(px.ID_MAX))}`,
  newline: `/api/jh/board-role?id=${encodeURIComponent("a::b\nc")}`, nul: `/api/jh/board-role?id=${encodeURIComponent("a::\u0000")}`,
  twice: `/api/jh/board-role?id=a::b&id=c::d`,
})) out.bad_id[k] = await ask(undefined, { path });
out.longest = await ask("a::" + "b".repeat(px.ID_MAX - 3));

/* --- not set up ------------------------------------------------------------------- */
out.no_token = await ask(ID, {}, { ...ENV, JH_SERVICE_TOKEN_ID: undefined });
out.no_token_secret = await ask(ID, {}, { ...ENV, JH_SERVICE_TOKEN_SECRET: "" });
out.odd_token = await ask(ID, {}, { ...ENV, JH_SERVICE_TOKEN_ID: "has space\r\nX-Evil: 1" });
out.no_key = await ask(ID, {}, { ...ENV, OIDC_SIGNING_JWK: undefined });

/* --- THE ONE CALL: exactly the fixed address, the two token headers, the
 * assertion, and nothing of the caller's ----------------------------------------- */
mode = "found";
const hostile = { "X-SJ-Assertion": "forged.by.caller", "CF-Access-Client-Id": "caller-id", "CF-Access-Client-Secret": "caller-secret",
  Authorization: "Bearer caller", "X-Forwarded-Host": "evil.example", "X-Upstream": "https://evil.example/" };
const good = await ask(ID, { headers: hostile, cookies: { [S.SESSION]: HUNTER, other: "1" } });
const v = verifyJwt(good.call && good.call.headers["x-sj-assertion"], KEYS);
out.good = { status: good.status, json: good.json, cache: good.cache, calls: good.calls, call: good.call,
  assertion: v, assertion_jwt: good.call && good.call.headers["x-sj-assertion"],
  want: { iss: O.ISSUER, aud: O.JH_ROUTE, sub: b64u(createHash("sha256").update("sub:hunter@example.org").digest()),
          email: "hunter@example.org", id: ID, iat: Math.floor(clock / 1000), token_id: TOKEN_ID, token_secret: TOKEN_SECRET },
  delays: delays.slice(-1) };
const second = await ask(ID);
out.jti_differs = !!second.call && verifyJwt(second.call.headers["x-sj-assertion"], KEYS).claims.jti !== v.claims.jti;
out.keys = KEYS;

/* ids with every character a URL or a query parser treats specially: what Job
 * Hunter reads back off the upstream address must be the id, exactly */
const TRICKY = ["zed::Développement d'affaires \"AE\"::9", "a b::c+d::e%20f", "x::y&id=other::z", "q::r#frag::s",
  "u::v=w;x::y", "slash::/../admin::z", "emoji::\u{1F427} AE::1", "plus::a+b::c", "tab::a b::c", "hash::%23::%"];
out.tricky = [];
for (const id of TRICKY) {
  const r = await ask(id);
  const a = r.call ? verifyJwt(r.call.headers["x-sj-assertion"], KEYS) : { claims: {} };
  out.tricky.push({ id, status: r.status, url: r.call ? r.call.url : "", read_back: r.call ? new URL(r.call.url).searchParams.get("id") : null,
    claim_id: a.claims && a.claims.id, ok: a.ok });
}

/* --- what comes back -------------------------------------------------------------- */
out.relay = {};
for (const m of Object.keys(ANSWERS)) {
  mode = m;
  const r = await ask(ID);
  out.relay[m] = { status: r.status, json: r.json, calls: r.calls };
}
mode = "slow";
fastTimers = true;
out.relay.slow = await ask(ID);
out.relay.slow = { status: out.relay.slow.status, json: out.relay.slow.json, calls: out.relay.slow.calls };
fastTimers = false;
out.slow_delay = delays[delays.length - 1];
out.upstream_ms = px.UPSTREAM_MS;
mode = "found";

/* --- through the gate ---------------------------------------------------------------- */
async function viaMiddleware(cookies) {
  const before = calls.length;
  const r = req(`/api/jh/board-role?id=${encodeURIComponent(ID)}`, { cookies });
  const res = await onRequest({ request: r, env: ENV, data: {}, next: async () => px.onRequestGet({ request: r, env: ENV }) });
  let json = null;
  try { json = JSON.parse(await res.text()); } catch { json = null; }
  return { status: res.status, json, calls: calls.length - before };
}
out.mw_signed_in = await viaMiddleware({ [S.SESSION]: HUNTER });
out.mw_anon = await viaMiddleware({});

/* NOTHING SECRET IN ANY ANSWER, nothing logged */
const answers = JSON.stringify([out.anon, out.no_role, out.good.json, out.relay, out.bad_id, out.no_token]);
out.leaks = { secret: answers.includes(TOKEN_SECRET), token_id: answers.includes(TOKEN_ID),
  assertion: !!out.good.assertion_jwt && answers.includes(out.good.assertion_jwt), email_relayed: JSON.stringify(out.relay).includes("hunter@example.org") };
out.logged = logged;
process.stdout.write(JSON.stringify(out) + "\n");
