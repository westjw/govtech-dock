/* GOOGLE'S ANSWER: GET /signin/google/callback?code=...&state=...
 *
 * Checks, in order, and any failure sends the person back to /signin with a
 * fixed note (nothing Google or the address bar says is printed):
 *   1. the state is ours (sealed under SESSION_SECRET for "oauth", in date);
 *   2. this device holds the verifier whose hash the state carries, so a
 *      code started on another device or by another site is refused;
 *   3. Google's token endpoint, asked directly over TLS with our client
 *      secret and the verifier, hands back an ID token. OpenID Connect
 *      Core 3.1.3.7 lets a client that got the token that way rely on TLS
 *      instead of checking Google's signature; its claims are still held:
 *      issued by Google, for OUR client, in date, and the address VERIFIED
 *      by Google (email_verified), never just stated, and carrying the
 *      nonce the state sealed.
 * Then the same session cookie an email sign-in gets, method "google".
 */
import { sameSitePath } from "../../_to.js";
import { validEmail } from "../../_address.js";
import { ours, unseal, sha256u, sameCode, sessionCookie, dropCookie, now, OAUTH } from "../../_session.js";
import { redirect } from "../../_signin_page.js";
import { googleReady, TOKEN, CALLBACK } from "../google.js";

const ISSUERS = new Set(["https://accounts.google.com", "accounts.google.com"]);

export function idClaims(idToken, clientId, nonce) {
  const parts = String(idToken || "").split(".");
  if (parts.length !== 3) return null;
  let c = null;
  try {
    let s = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    c = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(s), (ch) => ch.charCodeAt(0))));
  } catch { return null; }
  if (!c || typeof c !== "object" || Array.isArray(c)) return null;
  if (!ISSUERS.has(c.iss)) return null;
  const auds = Array.isArray(c.aud) ? c.aud : [c.aud];
  if (!auds.includes(clientId)) return null;
  if (Array.isArray(c.aud) && c.aud.length > 1 && c.azp !== clientId) return null;
  if (typeof c.exp !== "number" || c.exp <= now()) return null;
  if (c.email_verified !== true && c.email_verified !== "true") return null;
  if (typeof nonce !== "string" || !nonce || c.nonce !== nonce) return null;
  return typeof c.email === "string" ? c : null;
}

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const back = (e, to = "/") =>
    redirect(`/signin?to=${encodeURIComponent(to)}&e=${e}`, [["Set-Cookie", dropCookie(OAUTH)]], 302);
  if (!googleReady(env)) return back("google_off");
  const st = await unseal(env, "oauth", url.searchParams.get("state"));
  if (!st || typeof st.h !== "string" || typeof st.n !== "string") return back("google_failed");
  const to = sameSitePath(st.to, url.origin);
  if (url.searchParams.has("error")) return back("google_cancel", to);
  const verifier = ours(request, OAUTH);
  if (!verifier || !/^[A-Za-z0-9_-]{43}$/.test(verifier) || !sameCode(await sha256u(verifier), st.h)) {
    return back("google_failed", to);
  }
  const code = url.searchParams.get("code") || "";
  if (!code || code.length > 2048) return back("google_failed", to);
  let tok = null;
  try {
    const res = await fetch(TOKEN, { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
      body: new URLSearchParams({ code, client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET,
        redirect_uri: url.origin + CALLBACK, grant_type: "authorization_code", code_verifier: verifier }) });
    tok = res.ok ? await res.json() : null;
  } catch { tok = null; }
  const claims = idClaims(tok && tok.id_token, env.GOOGLE_CLIENT_ID, st.n);
  const email = claims && validEmail(claims.email);
  if (!email) return back("google_failed", to);
  return redirect(to, [["Set-Cookie", await sessionCookie(env, email, "google")],
                       ["Set-Cookie", dropCookie(OAUTH)]], 302);
}
