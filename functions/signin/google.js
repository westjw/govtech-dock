/* "CONTINUE WITH GOOGLE": GET /signin/google?to=... starts Google's own
 * sign-in (owner, 2026-11-02 for the button; 2026-11-13 for doing it
 * ourselves, so a Google sign-in takes no Cloudflare Access seat).
 *
 * OpenID Connect's authorization code flow with PKCE, against an OAuth
 * client the owner makes in Google Cloud (DEPLOY.md §3a step 3): GOOGLE_CLIENT_ID
 * and GOOGLE_CLIENT_SECRET are Pages secrets. This end:
 *   - mints a verifier and keeps it in a short-lived cookie on this device;
 *   - sends Google its hash (the PKCE challenge) and a STATE sealed under
 *     SESSION_SECRET that carries `to`, the same hash and an expiry, so the
 *     callback can tell its own sign-in from one another site started;
 *   - asks for openid and email, nothing else.
 * functions/signin/google/callback.js finishes it.
 *
 * With either secret missing it sends the person to the email sign-in,
 * which is harmless; the buttons show only once GOOGLE in _gate.js is true.
 * `to` is held to a path on this site by _to.js, as every sign-in link is.
 */
import { sameSitePath } from "../_to.js";
import { configured, onSite, random, sha256u, seal, now, setCookie, OAUTH, OAUTH_MINUTES } from "../_session.js";
import { redirect } from "../_signin_page.js";

export const AUTH = "https://accounts.google.com/o/oauth2/v2/auth";
export const TOKEN = "https://oauth2.googleapis.com/token";
export const CALLBACK = "/signin/google/callback";

export const googleReady = (env) =>
  configured(env) && typeof env.GOOGLE_CLIENT_ID === "string" && env.GOOGLE_CLIENT_ID.length > 10
  && typeof env.GOOGLE_CLIENT_SECRET === "string" && env.GOOGLE_CLIENT_SECRET.length > 10;

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const to = sameSitePath(url.searchParams.get("to"), url.origin);
  if (!googleReady(env)) return redirect(`/signin?to=${encodeURIComponent(to)}&e=google_off`, [], 302);
  const away = onSite(url);
  if (away) return redirect(away, [], 302);
  const verifier = random(32);
  const challenge = await sha256u(verifier);
  // a nonce too: state and PKCE already bind the answer to this device, and
  // Google's page lists nonce as required, so it is sealed in the state and
  // must come back inside the ID token
  const nonce = random(16);
  const state = await seal(env, "oauth", { to, h: challenge, n: nonce, exp: now() + OAUTH_MINUTES * 60 });
  const q = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: url.origin + CALLBACK,
    response_type: "code",
    scope: "openid email",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    nonce,
    prompt: "select_account",
  });
  return redirect(`${AUTH}?${q}`, [["Set-Cookie", setCookie(OAUTH, verifier, OAUTH_MINUTES * 60)]], 302);
}

/* A HEAD (a link checker, a link preview) is answered without minting a
 * verifier or a state: the sign-in page it would lead to. */
export async function onRequestHead() {
  return redirect("/signin", [], 302);
}
