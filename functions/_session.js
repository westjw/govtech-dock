/* OUR OWN SIGN-IN, the part every door shares (owner, 2026-11-13).
 *
 * "Anyone can sign in", and then, told that every Cloudflare Access sign-in
 * takes one of the Zero Trust free plan's 50 seats and that the 51st person
 * is turned away: "build". So members no longer sign in through Access at
 * all. Access keeps /admin, for the owner and the admins he lists (a handful
 * of seats); everyone else signs in here, by email or with Google, and holds
 * a cookie this file signs and checks.
 *
 * NOTHING IS STORED. A sign-in is a cookie carrying the address, how it was
 * proved and when it ends, with an HMAC-SHA256 over it under SESSION_SECRET
 * (a Pages secret). No account, no password, no session table: the free
 * plan's KV allows 1,000 writes a day across alerts, claims and ratings, and
 * a sign-in that spent one would ration sign-ins. The price is that one
 * sign-in cannot be taken back on its own; changing SESSION_SECRET signs
 * everybody out at once, which is the revoke.
 *
 * Every sealed value names its PURPOSE inside the MAC ("session", "link",
 * "pending", "oauth"), so a value made for one door never opens another:
 * an emailed link is not a session, and a pending cookie is not a link.
 *
 * FAILS CLOSED. No SESSION_SECRET, or one shorter than 32 characters, and
 * nothing here verifies: the sign-in pages say sign-in is not set up, and
 * the gate falls back to Access alone. A cookie sealed under another secret,
 * expired, cut, or carrying anything but an object is not a sign-in.
 */
import { SITE } from "./_brand.js";
import { validEmail } from "./_address.js";

export const SESSION = "__Host-sj_session";
export const PENDING = "__Host-sj_pending";
export const OAUTH = "__Host-sj_oauth";

export const SESSION_DAYS = 30;
export const LINK_MINUTES = 15;
export const OAUTH_MINUTES = 10;

const enc = new TextEncoder();

export const b64u = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

function unb64u(s) {
  s = String(s).replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

export const now = () => Math.floor(Date.now() / 1000);

export function configured(env) {
  const s = env && env.SESSION_SECRET;
  return typeof s === "string" && s.length >= 32;
}

let KEY = { secret: null, key: null };
async function key(env) {
  const s = env.SESSION_SECRET;
  if (KEY.secret !== s) {
    KEY = { secret: s, key: await crypto.subtle.importKey(
      "raw", enc.encode(s), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]) };
  }
  return KEY.key;
}

export function random(n = 32) {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b64u(b);
}

export async function sha256u(s) {
  return b64u(await crypto.subtle.digest("SHA-256", enc.encode(String(s))));
}

/* payload -> "<base64url json>.<base64url mac>", the mac over purpose too */
export async function seal(env, purpose, payload) {
  if (!configured(env)) throw new Error("SESSION_SECRET is not set");
  const body = b64u(enc.encode(JSON.stringify(payload)));
  const mac = await crypto.subtle.sign("HMAC", await key(env), enc.encode(`${purpose}.${body}`));
  return `${body}.${b64u(mac)}`;
}

/* The payload, or null: not configured, not ours, not this purpose, or
 * expired. crypto.subtle.verify compares in constant time. */
export async function unseal(env, purpose, token) {
  if (!configured(env)) return null;
  const t = String(token || "");
  if (t.length > 4096 || !/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/.test(t)) return null;
  const [body, mac] = t.split(".");
  let ok = false;
  try {
    ok = await crypto.subtle.verify("HMAC", await key(env), unb64u(mac), enc.encode(`${purpose}.${body}`));
  } catch { ok = false; }
  if (!ok) return null;
  let p = null;
  try { p = JSON.parse(new TextDecoder().decode(unb64u(body))); } catch { return null; }
  if (!p || typeof p !== "object" || Array.isArray(p)) return null;
  if (typeof p.exp !== "number" || p.exp <= now()) return null;
  return p;
}

/* A cookie this site sets. __Host- means Secure, Path=/ and no Domain, so it
 * is never sent to another host of ours (hq., hunter.) and cannot be planted
 * by one. HttpOnly: no script reads it. Lax: sent on a top-level arrival
 * from Google or a mail client, never on another site's POST. */
export const setCookie = (name, value, maxAge) =>
  `${name}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
export const dropCookie = (name) => `${name}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

/* ONE OF OUR COOKIES, or null. Ours are __Host-, which no other host can
 * set, so a name that arrives twice is somebody's attempt to have a planted
 * one read first (a sibling host can set "__host-x" or a near-name, never
 * the prefix itself): two of one name is no cookie at all. Only ASCII space
 * and tab are trimmed, as a browser writes the header (security review,
 * 2026-11-13). */
export function ours(request, name) {
  const raw = request.headers.get("Cookie") || "";
  let found = null;
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    if (part.slice(0, i).replace(/^[ \t]+|[ \t]+$/g, "") !== name) continue;
    if (found !== null) return null;
    found = part.slice(i + 1).replace(/^[ \t]+|[ \t]+$/g, "");
  }
  return found;
}

/* {email, method} for this request's sign-in, or null. Never throws. */
export async function sessionOf(request, env) {
  try {
    const p = await unseal(env, "session", ours(request, SESSION));
    if (!p || p.v !== 1 || (p.m !== "email" && p.m !== "google")) return null;
    const email = validEmail(p.e);
    return email ? { email, method: p.m } : null;
  } catch { return null; }
}

export async function sessionCookie(env, email, method) {
  const t = now();
  const token = await seal(env, "session", { v: 1, e: email, m: method, iat: t, exp: t + SESSION_DAYS * 86400 });
  return setCookie(SESSION, token, SESSION_DAYS * 86400);
}

/* THE CODE IN THE EMAIL, for a person who asked on one device and reads mail
 * on another. Ten characters of Crockford base32 (50 bits) derived from the
 * pending sign-in, so nothing is stored and each request has its own.
 * Guessing is not counted - counting would spend a KV write per guess - and
 * does not need to be: the code lives LINK_MINUTES, only beside the pending
 * cookie it was made for, and even a million guesses a day are one in a
 * billion of the space, on any plan. It was eight characters until review
 * (2026-11-13): 40 bits held only while the free plan's 100,000 requests a
 * day capped the guessing. */
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
export const CODE_LENGTH = 10;
export async function codeFor(env, pending) {
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", await key(env),
    enc.encode(`code.${pending.e}.${pending.n}.${pending.exp}`)));
  let out = "";
  for (let i = 0; i < CODE_LENGTH; i++) out += CROCKFORD[mac[i] & 31];
  return out;
}
/* As typed: any case, any spaces or dashes, O for 0 and I or L for 1. */
export const normCode = (s) =>
  String(s || "").toUpperCase().replace(/[^0-9A-Z]/g, "")
    .replace(/O/g, "0").replace(/[IL]/g, "1").slice(0, 16);
export const showCode = (c) => `${c.slice(0, 5)}-${c.slice(5)}`;
export function sameCode(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

/* A POST that writes a cookie or sends mail must come from our own page.
 * Browsers send Origin on every form POST, and a real one must be ours.
 * "null" is what a browser sends under a no-referrer policy (a privacy
 * setting can impose one), from a sandboxed frame, or after a redirect from
 * another site: it passes only when Sec-Fetch-Site, which no page can set,
 * says same-origin. With no Origin at all (an old browser) Sec-Fetch-Site
 * decides, and with neither the POST is allowed: the cookies here are Lax,
 * so a cross-site POST carries none of ours anyway. */
export function fromOurPage(request) {
  const origin = request.headers.get("Origin");
  const site = request.headers.get("Sec-Fetch-Site");
  if (origin === "null") return site === "same-origin";
  if (origin !== null) return origin === new URL(request.url).origin;
  return !site || site === "same-origin" || site === "none";
}

/* Every sign-in happens on the site's own address, so the cookie it sets is
 * the one the site reads (it is host-only). www and the old domain are sent
 * there; a local preview is left where it is. */
export function onSite(url) {
  const site = new URL(SITE);
  if (url.host === site.host) return null;
  if (/^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(url.host)) return null;
  return `${SITE}${url.pathname}${url.search}`;
}
