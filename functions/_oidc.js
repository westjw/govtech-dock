/* JOB HUNTER SIGNS IN WITH SLED JOBS, the part every door of it shares
 * (owner, 2026-11-23: of three designs, "C: B plus popout").
 *
 * "Cloudflare Access stays in front of Job Hunter, but its only button
 * becomes 'Sign in with SLED JOBS'. If you're already signed in on the board,
 * you go straight through." So the board is an OpenID Connect provider with
 * ONE client, the existing "Job Hunter beta" Access application:
 *
 *   GET  /oidc/authorize   functions/oidc/authorize.js: the board's own
 *                          sign-in, the Users board's `hunter` or `owner`
 *                          role, then a one-time code back to Access
 *   POST /oidc/token       functions/oidc/token.js: Access trades the code,
 *                          server to server, for an ID token signed here
 *   GET  /oidc/jwks        the public half of the signing key, which Access
 *                          (and Job Hunter, for the popout) verify with
 *   GET  /.well-known/openid-configuration
 *
 * "plus the board's server fetches the scoring popout for you with a
 * Cloudflare service token": functions/api/jh/board-role.js signs a short
 * assertion of who is asking with the same key.
 *
 * THE INTERFACE IS SHARED WITH JOB HUNTER (its scripts/serve.py verifies the
 * popout's assertion against /oidc/jwks): the issuer, ES256 on P-256, the
 * claims and their lifetimes below. Change one side only and the popout or
 * the sign-in stops, so the shapes here are the spec's, not ours to tidy.
 *
 * SECRETS, all Pages secrets, none ever in this public repository
 * (scripts/jh_signin_secrets.mjs makes the first three and prints them once):
 *   OIDC_SIGNING_JWK        the private P-256 key as a JWK carrying its kid
 *   OIDC_SIGNING_JWK_OLD    optional, routine rotations only: the earlier
 *                           key's public half as /oidc/jwks printed it,
 *                           PUBLISHED only, so a token signed just before the
 *                           rotation still verifies (never after a leak)
 *   OIDC_CLIENT_ID          the one client
 *   OIDC_CLIENT_SECRET      at least 32 characters, or the token door is off
 *   OIDC_REDIRECT_URI       optional: REPLACES Access's callback, never widens
 *   JH_SERVICE_TOKEN_ID / JH_SERVICE_TOKEN_SECRET   the popout's way past Access
 *
 * FAILS CLOSED. A missing or malformed key signs nothing and publishes
 * nothing, not even an earlier key beside it; every door says it is not
 * set up rather than guessing.
 */
import { SITE, DOMAIN } from "./_brand.js";
import { TEAM } from "./_access.js";
import { b64u, sha256u, now, sameCode } from "./_session.js";
import { callerNet } from "./_mail.js";

export const ISSUER = SITE;
export const AUTHORIZE = "/oidc/authorize";
export const TOKEN = "/oidc/token";
export const JWKS = "/oidc/jwks";
export const DISCOVERY = "/.well-known/openid-configuration";

/* Job Hunter, and the one route of it the popout reads: the assertion's
 * audience and the proxy's only upstream address. */
export const JH_ORIGIN = `https://hunter.${DOMAIN}`;
export const JH_ROUTE = `${JH_ORIGIN}/api/board-role`;

/* Where Access takes the code. The only address a code is ever sent to. */
export const ACCESS_CALLBACK = `https://${TEAM}/cdn-cgi/access/callback`;

export const CODE_SECONDS = 120;          // a code is good this long, once
export const ID_TOKEN_SECONDS = 300;
export const ASSERTION_SECONDS = 60;
export const REQUEST_MINUTES = 10;        // a sign-in on the way, held this long
export const MIN_SECRET = 32;
/* A row older than this is nobody's sign-in any more: it goes on the next
 * write, so an address sits in the code table only minutes. */
export const STALE_SECONDS = 600;

/* The roles that open Job Hunter (_roles.js): a beta user, and the owner. */
export const JH_ROLES = ["hunter", "owner"];

const enc = new TextEncoder();
const B64U_43 = /^[A-Za-z0-9_-]{43}$/;
const KID = /^[A-Za-z0-9._-]{1,64}$/;

/* THE ONE ADDRESS A CODE GOES TO. Access's callback, or OIDC_REDIRECT_URI in
 * its place: one absolute https address written the way a URL prints it (so
 * the comparison with what Access sends is exact), no credentials, no
 * fragment. An override that is anything else turns the sign-in OFF (null):
 * never a second address, never a pattern. */
export function redirectUri(env) {
  const o = env ? env.OIDC_REDIRECT_URI : undefined;
  if (o === undefined || o === null || o === "") return ACCESS_CALLBACK;
  if (typeof o !== "string" || o.length > 512) return null;
  let u = null;
  try { u = new URL(o); } catch { return null; }
  if (u.protocol !== "https:" || u.username || u.password || u.hash || u.href !== o) return null;
  return o;
}

/* The one client, or null when it is not set up: an id of plain characters,
 * and a secret long enough that guessing it is hopeless. */
export function client(env) {
  const id = env && env.OIDC_CLIENT_ID, secret = env && env.OIDC_CLIENT_SECRET;
  if (typeof id !== "string" || !/^[A-Za-z0-9._~-]{1,128}$/.test(id)) return null;
  if (typeof secret !== "string" || secret.length < MIN_SECRET || secret.length > 512) return null;
  return { id, secret };
}

/* Two strings compared in constant time, whatever their lengths: each is
 * hashed first, so the comparison is always of two 43-character digests. */
export async function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const [x, y] = await Promise.all([sha256u(`oidc-compare|${a}`), sha256u(`oidc-compare|${b}`)]);
  return sameCode(x, y);
}

/* --- the key -------------------------------------------------------------- */

const ECDSA = { name: "ECDSA", namedCurve: "P-256" };
const ES256 = { name: "ECDSA", hash: "SHA-256" };

/* A public JWK, the fields a verifier needs and nothing else (never `d`). */
function publicOf(j) {
  return { kty: "EC", crv: "P-256", x: j.x, y: j.y, kid: j.kid, alg: "ES256", use: "sig" };
}

function parseJwk(raw, needPrivate) {
  let j = null;
  try { j = typeof raw === "string" ? JSON.parse(raw) : null; } catch { return null; }
  return checkJwk(j, needPrivate);
}
function checkJwk(j, needPrivate) {
  if (!j || typeof j !== "object" || Array.isArray(j)) return null;
  if (j.kty !== "EC" || j.crv !== "P-256") return null;
  if (j.alg !== undefined && j.alg !== "ES256") return null;
  if (j.use !== undefined && j.use !== "sig") return null;
  if (typeof j.kid !== "string" || !KID.test(j.kid)) return null;
  for (const k of needPrivate ? ["x", "y", "d"] : ["x", "y"]) {
    if (typeof j[k] !== "string" || !B64U_43.test(j[k])) return null;
  }
  return j;
}

/* The signing key, imported once per isolate (per value of the secret).
 * THE PAIR MUST BE ONE PAIR: a probe signed with `d` has to verify under the
 * published x and y, or every token this signs would be refused by the very
 * keys /oidc/jwks hands out. A second line only: Node's importKey already
 * refuses a `d` that is not x and y's (mutation-tested 2026-11-23, removing
 * the probe changes no outcome the harness can reach), and the probe keeps
 * that true on a runtime that imports without checking. */
let SIGNING = { raw: undefined, key: null };
async function importSigning(raw) {
  const j = parseJwk(raw, true);
  if (!j) return null;
  try {
    const priv = await crypto.subtle.importKey("jwk",
      { kty: "EC", crv: "P-256", x: j.x, y: j.y, d: j.d }, ECDSA, false, ["sign"]);
    const pub = await crypto.subtle.importKey("jwk",
      { kty: "EC", crv: "P-256", x: j.x, y: j.y }, ECDSA, false, ["verify"]);
    const probe = enc.encode("sledjobs-oidc-key-probe");
    const sig = await crypto.subtle.sign(ES256, priv, probe);
    if (!(await crypto.subtle.verify(ES256, pub, sig, probe))) return null;
    return { kid: j.kid, priv, jwk: publicOf(j) };
  } catch { return null; }
}

export async function signingKey(env) {
  const raw = env && env.OIDC_SIGNING_JWK;
  if (typeof raw !== "string" || !raw) return null;
  if (SIGNING.raw !== raw) SIGNING = { raw, key: await importSigning(raw) };
  return SIGNING.key;
}

/* The earlier key, public half only, for a routine rotation (DEPLOY.md
 * §3f): what /oidc/jwks printed before it, pasted whole - {"keys":[ that one
 * key ]} - or that one key alone. The old PRIVATE key cannot be copied
 * across (Pages never shows a secret again) and is never needed; one
 * pasted anyway is accepted and its `d` never read. A document of more or
 * fewer than one key publishes no earlier key. */
let OLD = { raw: undefined, jwk: null };
async function importOld(raw) {
  let j = null;
  try { j = JSON.parse(raw); } catch { return null; }
  if (j && typeof j === "object" && !Array.isArray(j) && Object.prototype.hasOwnProperty.call(j, "keys")) {
    if (!Array.isArray(j.keys) || j.keys.length !== 1) return null;
    j = j.keys[0];
  }
  j = checkJwk(j, false);
  if (!j) return null;
  try {
    await crypto.subtle.importKey("jwk", { kty: "EC", crv: "P-256", x: j.x, y: j.y }, ECDSA, false, ["verify"]);
    return publicOf(j);
  } catch { return null; }
}

/* What /oidc/jwks publishes: the signing key's public half, and the earlier
 * key's when there is one with another kid. NOTHING when the signing key
 * itself is missing, malformed or not one pair, even with an earlier key
 * set: nothing here can sign then, and a page holding the earlier key alone
 * would only keep it trusted (review, 2026-11-23: a bad paste during a
 * rotation published the old key alone, cached five minutes). */
export async function publicKeys(env) {
  const cur = await signingKey(env);
  if (!cur) return [];
  const out = [cur.jwk];
  const raw = env && env.OIDC_SIGNING_JWK_OLD;
  if (typeof raw === "string" && raw) {
    if (OLD.raw !== raw) OLD = { raw, jwk: await importOld(raw) };
    if (OLD.jwk && OLD.jwk.kid !== cur.kid) out.push(OLD.jwk);
  }
  return out;
}

/* A compact JWS, ES256: the header names the key, and the signature is the
 * raw 64 bytes r||s that WebCrypto's ECDSA hands back (JWS's own form, not
 * DER), base64url. Null when there is no key to sign with. */
export async function signJwt(env, claims) {
  const k = await signingKey(env);
  if (!k) return null;
  const head = b64u(enc.encode(JSON.stringify({ alg: "ES256", typ: "JWT", kid: k.kid })));
  const body = b64u(enc.encode(JSON.stringify(claims)));
  const sig = await crypto.subtle.sign(ES256, k.priv, enc.encode(`${head}.${body}`));
  return `${head}.${body}.${b64u(sig)}`;
}

/* The person, as Job Hunter and Access see them: `sub` is stable and holds
 * no address (a hash of it), `email` is the address as the sign-in keeps it. */
export const subOf = (email) => sha256u(`sub:${String(email).trim().toLowerCase()}`);

/* --- the authorize request ------------------------------------------------- */

/* The parameters authorize reads. Each may appear once (RFC 6749 3.1): with
 * two, one reader takes the first and another the last. */
export const PARAMS = ["client_id", "redirect_uri", "response_type", "scope", "state", "nonce",
  "code_challenge", "code_challenge_method", "response_mode"];
export const STATE_MAX = 2048;
export const NONCE_MAX = 256;
const VSCHAR = /^[\x20-\x7e]+$/;

/* The request as asked, or null when a parameter is given twice. */
export function readAuthorize(sp) {
  const q = {};
  for (const k of PARAMS) {
    const all = sp.getAll(k);
    if (all.length > 1) return null;
    q[k] = all.length ? all[0] : null;
  }
  return q;
}

/* The request, held to the one client: the clean copy to act on, or null.
 * Nothing in here is ever sent anywhere: a request that fails any of it is
 * answered with a page on this site, never a redirect to the address it
 * names. */
export function checkAuthorize(q, setup) {
  if (!q || typeof q !== "object" || !setup) return null;
  const s = (k) => (typeof q[k] === "string" ? q[k] : null);
  const given = (k) => q[k] !== null && q[k] !== undefined;
  if (s("client_id") !== setup.client.id) return null;
  if (s("redirect_uri") !== setup.redirect) return null;
  if (s("response_type") !== "code") return null;
  const scope = s("scope");
  if (!scope || scope.length > 256 || !VSCHAR.test(scope) || !scope.split(" ").includes("openid")) return null;
  // PKCE, S256 and nothing else, and REQUIRED
  if (s("code_challenge_method") !== "S256") return null;
  const challenge = s("code_challenge");
  if (!challenge || !B64U_43.test(challenge)) return null;
  const state = s("state");
  if (given("state") && (!state || state.length > STATE_MAX || !VSCHAR.test(state))) return null;
  const nonce = s("nonce");
  if (given("nonce") && (!nonce || nonce.length > NONCE_MAX || !VSCHAR.test(nonce))) return null;
  // the code comes back in the query, never a form or a fragment
  if (given("response_mode") && s("response_mode") !== "query") return null;
  return { client_id: setup.client.id, redirect_uri: setup.redirect, response_type: "code", scope,
    state: given("state") ? state : null, nonce: given("nonce") ? nonce : null,
    code_challenge: challenge, code_challenge_method: "S256",
    response_mode: given("response_mode") ? "query" : null };
}

/* --- the codes, in D1 ------------------------------------------------------ */

/* D1 bound as DB (the same database saved roles use), not the ALERTS KV:
 * spending a code must be atomic, and KV has no compare-and-set. A code is
 * kept only as its SHA-256. The failure counts that slow a guesser live here
 * too: each is a write, and KV's 1,000 writes a day are the sign-in's and the
 * alerts', which a guesser spending one per failure could empty for the
 * whole site; D1's 100,000 a day are beyond a free plan's requests. The
 * panel's per-person count (jh_asks) is here for the same reason: an ask is
 * a write. */
const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS oidc_codes (
     code_hash TEXT PRIMARY KEY,
     email TEXT NOT NULL,
     nonce TEXT,
     code_challenge TEXT NOT NULL,
     client_id TEXT NOT NULL,
     redirect_uri TEXT NOT NULL,
     auth_time INTEGER,
     made_at INTEGER NOT NULL,
     used INTEGER NOT NULL DEFAULT 0)`,
  `CREATE TABLE IF NOT EXISTS oidc_fails (
     k TEXT PRIMARY KEY,
     n INTEGER NOT NULL,
     at INTEGER NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS jh_asks (
     k TEXT PRIMARY KEY,
     n INTEGER NOT NULL,
     at INTEGER NOT NULL)`,
];
/* Per database object, never a shared promise (functions/api/saved.js says
 * why); CREATE ... IF NOT EXISTS twice is harmless. */
const READY = new WeakSet();
async function ready(db) {
  if (READY.has(db)) return;
  await db.batch(SCHEMA.map((s) => db.prepare(s)));
  READY.add(db);
}

/* A new code for this sign-in: 32 random bytes, of which only the hash is
 * kept, with what the token door must hold it to. Stale rows go in the same
 * batch. Throws when D1 does. */
export async function mintCode(env, row) {
  const db = env.DB;
  await ready(db);
  const b = new Uint8Array(32);
  crypto.getRandomValues(b);
  const code = b64u(b);
  const t = now();
  await db.batch([
    db.prepare("DELETE FROM oidc_codes WHERE made_at < ?").bind(t - STALE_SECONDS),
    db.prepare(`INSERT INTO oidc_codes (code_hash, email, nonce, code_challenge, client_id, redirect_uri,
                  auth_time, made_at, used) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)`)
      .bind(await sha256u(code), row.email, row.nonce ?? null, row.code_challenge, row.client_id,
            row.redirect_uri, Number.isInteger(row.auth_time) ? row.auth_time : null, t),
  ]);
  return code;
}

/* SPENT, THEN READ: the row this code names, or null when it is unknown,
 * used or older than CODE_SECONDS. The UPDATE ... WHERE used = 0 is the one
 * atomic step, so of two requests carrying one code only one gets a row.
 * The row goes once it is read. Throws when D1 does. */
export async function spendCode(env, code) {
  if (typeof code !== "string" || !B64U_43.test(code)) return null;
  const db = env.DB;
  await ready(db);
  const h = await sha256u(code), t = now();
  const r = await db.prepare("UPDATE oidc_codes SET used = 1 WHERE code_hash = ? AND used = 0 AND made_at >= ?")
    .bind(h, t - CODE_SECONDS).run();
  if (!r || !r.meta || Number(r.meta.changes) !== 1) return null;
  const row = await db.prepare(`SELECT email, nonce, code_challenge, client_id, redirect_uri, auth_time, made_at
                                FROM oidc_codes WHERE code_hash = ?`).bind(h).first();
  try {
    await db.prepare("DELETE FROM oidc_codes WHERE code_hash = ? OR made_at < ?").bind(h, t - STALE_SECONDS).run();
  } catch { /* the next write clears it */ }
  return row || null;
}

/* --- a guesser, slowed ----------------------------------------------------- */

/* Failed token requests a network may make in an hour (an IPv4 address, or
 * an IPv6 /64). Access itself never fails one; only a wrong secret, code or
 * verifier counts, so a working sign-in leaves nothing here. */
export const FAILS_PER_HOUR = 20;

async function failKey(request) {
  const net = callerNet((request.headers.get("cf-connecting-ip") || "unknown").trim());
  const hour = new Date(Date.now()).toISOString().slice(0, 13);
  return (await sha256u(`oidc-token|${net}|${hour}`)).slice(0, 32);
}

/* A TRY IS TAKEN BEFORE IT IS MADE, by one statement: the network's count
 * goes up only while it is under FAILS_PER_HOUR, so of any number of
 * requests sent at once, only as many as the hour has left are tried,
 * whatever order D1 runs them in. (A count read first and written after
 * the try let a parallel burst through whole: 2026-11-23 review, 200 of 200
 * tried against a cap of 20.) A refused request writes nothing: the WHERE
 * stops the update. Rows past two hours go in the same batch. The try's key
 * (giveBack needs it), or null when this hour's tries are used up. Throws
 * when D1 does. */
export async function takeTry(env, request) {
  await ready(env.DB);
  const k = await failKey(request), t = now();
  const [, r] = await env.DB.batch([
    env.DB.prepare("DELETE FROM oidc_fails WHERE at < ?").bind(t - 7200),
    env.DB.prepare(`INSERT INTO oidc_fails (k, n, at) VALUES (?, 1, ?)
                    ON CONFLICT (k) DO UPDATE SET n = n + 1 WHERE oidc_fails.n < ?`).bind(k, t, FAILS_PER_HOUR),
  ]);
  return r && r.meta && Number(r.meta.changes) === 1 ? k : null;
}

/* A try that was not a failure (a token handed out, a grant type it does
 * not offer, a door that could not answer) is given back, so a working
 * sign-in leaves no count: the row goes when it was the only try, or
 * counts one fewer. A failure keeps its try; it is the count. Never
 * throws: a try that could not be given back stays counted until its hour
 * is over. */
export async function giveBack(env, k) {
  if (!k) return;
  try {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM oidc_fails WHERE k = ? AND n <= 1").bind(k),
      env.DB.prepare("UPDATE oidc_fails SET n = n - 1 WHERE k = ? AND n > 1").bind(k),
    ]);
  } catch { /* it stays counted */ }
}

/* --- the panel, at one person's pace --------------------------------------- */

/* How often one signed-in address may ask the panel's route in a minute.
 * Every ask is a signed assertion and a call to Job Hunter, which keeps each
 * assertion's jti for ten minutes in one book that refuses EVERYBODY once
 * 10,000 are in it, and answers one request at a time (review, 2026-11-23:
 * one beta user's script at 17 a second filled the book, and every panel,
 * the owner's too, said "sign in" for ten minutes). Twenty a minute is 200
 * in Job Hunter's ten, so filling the book would take fifty beta users
 * asking flat out, and it is more than anybody reading roles asks. */
export const ASKS_PER_MINUTE = 20;

/* One ask for this address this minute, by one statement as takeTry's: the
 * count goes up only while it is under ASKS_PER_MINUTE, so asks sent at
 * once are held to it too, and a refused one writes nothing. The row is
 * keyed by a hash of the address and the minute; no address is kept. Rows
 * past two minutes go in the same batch. True: go on. Throws when D1 does. */
export async function takeAsk(env, email) {
  await ready(env.DB);
  const minute = new Date(Date.now()).toISOString().slice(0, 16);
  const k = (await sha256u(`jh-ask|${String(email).trim().toLowerCase()}|${minute}`)).slice(0, 32);
  const t = now();
  const [, r] = await env.DB.batch([
    env.DB.prepare("DELETE FROM jh_asks WHERE at < ?").bind(t - 120),
    env.DB.prepare(`INSERT INTO jh_asks (k, n, at) VALUES (?, 1, ?)
                    ON CONFLICT (k) DO UPDATE SET n = n + 1 WHERE jh_asks.n < ?`).bind(k, t, ASKS_PER_MINUTE),
  ]);
  return !!(r && r.meta && Number(r.meta.changes) === 1);
}

/* --- the pieces each door checks before it does anything ------------------ */

/* Everything the sign-in needs, or null: the client, the one redirect
 * address, a key that signs, and D1. */
export async function setUp(env) {
  const c = client(env), redirect = redirectUri(env);
  if (!c || !redirect || !env || !env.DB) return null;
  if (!(await signingKey(env))) return null;
  return { client: c, redirect };
}

/* For a test: forget the keys this isolate imported. */
export function _reset() { SIGNING = { raw: undefined, key: null }; OLD = { raw: undefined, jwk: null }; }
