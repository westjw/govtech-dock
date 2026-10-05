/* VERIFYING A CLOUDFLARE ACCESS SIGN-IN, in one place.
 *
 * Two doors use this: functions/admin/_middleware.js in front of /admin, and
 * functions/_gate.js in front of the whole site while it is signed-in only.
 * They used to be one door with this code inside it. A second copy of a
 * signature check is a second place for it to be wrong, so the check lives
 * here and both import it; nothing in it changed in the move.
 *
 * The team domain and application audience are the values the live
 * redirect carries today; ACCESS_TEAM_DOMAIN and ACCESS_AUD override them
 * without a code change if the application is ever recreated (the AUD tag is
 * on the application's overview page in Zero Trust). Both doors check the
 * SAME audience, so widening the existing Access application from /admin to
 * the whole site keeps every token valid; creating a second application
 * would not.
 */
export const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
export const AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const KEY_TTL_MS = 60 * 60 * 1000;

let cache = { at: 0, team: null, keys: null };

function b64url(s) {
  s = String(s).replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}
const b64json = (s) => JSON.parse(new TextDecoder().decode(b64url(s)));

export function cookie(request, name) {
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

/* The token a request carries: the header Access adds when it sits in front
 * of the path, or the cookie the browser sends on its own. Either is only a
 * claim until verify() has checked it. */
export function tokenOf(request) {
  return request.headers.get("Cf-Access-Jwt-Assertion") || cookie(request, "CF_Authorization");
}
