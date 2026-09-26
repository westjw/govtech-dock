/* THE DOOR ON /admin, VERIFIED, ON EVERY HOSTNAME.
 *
 * Cloudflare Access covers /admin on sledjobs.com. It did not cover the
 * project's *.pages.dev alias, and on 2026-09-25 that alias answered 200 to
 * anyone for /admin/, /admin/data.json (every queue), /admin/rulings.json
 * (every recorded ruling with the owner's reasons) and /admin/users.json
 * (the owner's email hash). DEPLOY.md said a custom domain "inherits the
 * project's Access policy"; the alias evidently does not. Everything behind
 * /admin was one hostname away from public, and nothing in the code could
 * tell, because the code trusted that a request reaching it had passed a
 * door it could not see.
 *
 * So the door is here now, in front of every file and function under
 * /admin, and it does not trust presence: whoami.js and rule.js read the
 * Cf-Access-* headers and stopped there. This VERIFIES the Access JWT
 * (Cf-Access-Jwt-Assertion header, or the CF_Authorization cookie the
 * browser sends on static requests) against the team's public keys, checks
 * the audience is THIS application and the token is in date, and refuses
 * everything else. Fails closed three ways: no token is 403, an unverifiable
 * token is 403, and keys that cannot be fetched are 503 - never 200.
 *
 * Two paths stay open on purpose. /admin/api/whoami answers {signed_in:
 * false} to the public site's account menu and holds nothing; /admin/api/
 * login is how a person reaches Access in the first place. Both are
 * harmless without a token and both are needed without one.
 *
 * The team domain and application audience are the values the live
 * redirect carries today; ACCESS_TEAM_DOMAIN and ACCESS_AUD override them
 * without a deploy if the application is ever recreated (the AUD tag is on
 * the application's overview page in Zero Trust). A wrong value here means
 * the owner sees a 403 naming the variable, which is the failure mode this
 * file exists to have - "nothing works", never "everyone can read".
 */
const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const OPEN = new Set(["/admin/api/whoami", "/admin/api/login"]);
const KEY_TTL_MS = 60 * 60 * 1000;

let cache = { at: 0, team: null, keys: null };

function b64url(s) {
  s = String(s).replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}
const b64json = (s) => JSON.parse(new TextDecoder().decode(b64url(s)));

function cookie(request, name) {
  const raw = request.headers.get("Cookie") || "";
  for (const part of raw.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

/* The team's current signing keys, cached for an hour per edge. `force`
 * refetches once when a token names a kid we have not seen: Access rotates
 * keys, and a rotation must not lock the owner out for an hour. */
async function signingKeys(team, force) {
  const now = Date.now();
  if (!force && cache.keys && cache.team === team && now - cache.at < KEY_TTL_MS) return cache.keys;
  const res = await fetch(`https://${team}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error(`certs ${res.status}`);
  const body = await res.json();
  const keys = {};
  for (const k of body.keys || []) {
    if (k.kty !== "RSA" || !k.kid || !k.n || !k.e) continue;
    keys[k.kid] = await crypto.subtle.importKey(
      "jwk", { kty: "RSA", n: k.n, e: k.e, alg: "RS256", ext: true },
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  }
  cache = { at: now, team, keys };
  return keys;
}

/* The verified claims, or null. Throws only when the keys cannot be read. */
export async function verify(token, env) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return null;
  let header, claims;
  try { header = b64json(parts[0]); claims = b64json(parts[1]); } catch { return null; }
  if (!header || header.alg !== "RS256" || !header.kid) return null;
  const team = (env && env.ACCESS_TEAM_DOMAIN) || TEAM;
  let keys = await signingKeys(team, false);
  let key = keys[header.kid];
  if (!key) { keys = await signingKeys(team, true); key = keys[header.kid]; }
  if (!key) return null;
  let ok = false;
  try {
    ok = await crypto.subtle.verify({ name: "RSASSA-PKCS1-v1_5" }, key, b64url(parts[2]),
                                    new TextEncoder().encode(`${parts[0]}.${parts[1]}`));
  } catch { ok = false; }
  if (!ok) return null;
  const now = Math.floor(Date.now() / 1000);
  if (typeof claims.exp !== "number" || claims.exp <= now) return null;
  if (typeof claims.nbf === "number" && claims.nbf > now + 60) return null;
  const aud = (env && env.ACCESS_AUD) || AUD;
  const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!auds.includes(aud)) return null;
  if (claims.iss !== `https://${team}`) return null;
  return claims;
}

function refuse(url, status, why) {
  const headers = { "cache-control": "no-store", "x-robots-tag": "noindex" };
  if (url.pathname.startsWith("/admin/api/")) {
    return new Response(JSON.stringify({ error: why }),
      { status, headers: { ...headers, "content-type": "application/json" } });
  }
  return new Response(`${why}\n\nSign in at https://sledjobs.com/admin/\n`,
    { status, headers: { ...headers, "content-type": "text/plain; charset=utf-8" } });
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  if (OPEN.has(url.pathname)) return next();
  const token = request.headers.get("Cf-Access-Jwt-Assertion") || cookie(request, "CF_Authorization");
  if (!token) return refuse(url, 403, "not signed in through Cloudflare Access");
  let claims = null;
  try { claims = await verify(token, env); }
  catch { return refuse(url, 503, "could not verify the sign-in (the Access keys did not answer)"); }
  if (!claims) {
    return refuse(url, 403, "the sign-in could not be verified for this application "
      + "(ACCESS_AUD / ACCESS_TEAM_DOMAIN name which one)");
  }
  return next();
}
