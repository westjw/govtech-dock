/* THE CODE, TRADED FOR AN ID TOKEN: POST /oidc/token (design C,
 * functions/_oidc.js).
 *
 * Called by Cloudflare Access, server to server, with no sign-in of ours -
 * so it is OPEN in functions/_gate.js and checks everything itself, in this
 * order:
 *   1. the caller has not failed FAILS_PER_HOUR times this hour (a wrong
 *      secret, code or verifier counts; Access's own calls never fail);
 *   2. a form body, each parameter once;
 *   3. the client: client_secret_basic OR client_secret_post (either, never
 *      both), id and secret compared in constant time against
 *      OIDC_CLIENT_ID and OIDC_CLIENT_SECRET (under 32 characters, and this
 *      door is off);
 *   4. grant_type=authorization_code;
 *   5. the code found, unused and under CODE_SECONDS old, and SPENT, before
 *      anything else about it is compared, so a code gets one try;
 *   6. the redirect_uri and client_id it was made for, and a code_verifier
 *      whose S256 is the challenge it was made with.
 * Then an ID token for five minutes (iss, aud = the client, sub, email,
 * email_verified, iat, exp, auth_time, nonce) and an access token nobody
 * keeps. Every failure is OAuth's own error word and nothing else: no
 * detail that tells a guesser which part was wrong.
 */
import { onSite, random, sha256u, sameCode, now } from "../_session.js";
import { setUp, sameSecret, spendCode, signJwt, subOf, failuresOf, countFailure, ISSUER,
         ID_TOKEN_SECONDS, FAILS_PER_HOUR } from "../_oidc.js";

const MAX_BODY = 8192;
const ONCE = ["grant_type", "code", "redirect_uri", "code_verifier", "client_id", "client_secret"];
/* RFC 7636 4.1: 43 to 128 unreserved characters */
const VERIFIER = /^[A-Za-z0-9._~-]{43,128}$/;

function answer(status, body, headers = []) {
  const h = new Headers({ "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store", "pragma": "no-cache" });
  for (const [k, v] of headers) h.append(k, v);
  return new Response(JSON.stringify(body), { status, headers: h });
}
const oauthError = (status, error, headers = []) => answer(status, { error }, headers);
const badClient = () => oauthError(401, "invalid_client", [["WWW-Authenticate", 'Basic realm="oidc"']]);

/* client_secret_basic: "Basic base64(id:secret)", each part form-encoded
 * first (RFC 6749 2.3.1). null: no Authorization header; false: one that
 * cannot be read. */
function basicOf(h) {
  if (h === null || h === undefined) return null;
  const m = /^Basic +([A-Za-z0-9+/]+={0,2})$/i.exec(String(h).trim());
  if (!m) return false;
  let s = "";
  try { s = atob(m[1]); } catch { return false; }
  const i = s.indexOf(":");
  if (i < 0) return false;
  const dec = (v) => { try { return decodeURIComponent(v.replace(/\+/g, " ")); } catch { return null; } };
  const id = dec(s.slice(0, i)), secret = dec(s.slice(i + 1));
  return id === null || secret === null ? false : { id, secret };
}

export async function onRequestPost({ request, env }) {
  // Access asks the issuer's own address; nowhere else answers
  if (onSite(new URL(request.url))) return oauthError(400, "invalid_request");
  const setup = await setUp(env);
  if (!setup) return oauthError(503, "temporarily_unavailable");
  let fails = 0;
  try { fails = await failuresOf(env, request); } catch { return oauthError(503, "temporarily_unavailable"); }
  if (fails >= FAILS_PER_HOUR) return oauthError(429, "temporarily_unavailable", [["Retry-After", "3600"]]);
  const failed = async (res) => { await countFailure(env, request); return res; };

  const ctype = (request.headers.get("content-type") || "").toLowerCase();
  if (!ctype.startsWith("application/x-www-form-urlencoded")) return oauthError(400, "invalid_request");
  let raw = "";
  try { raw = await request.text(); } catch { return oauthError(400, "invalid_request"); }
  if (raw.length > MAX_BODY) return oauthError(400, "invalid_request");
  const form = new URLSearchParams(raw);
  if (ONCE.some((k) => form.getAll(k).length > 1)) return oauthError(400, "invalid_request");

  // THE CLIENT, by one method
  const basic = basicOf(request.headers.get("authorization"));
  if (basic === false) return failed(badClient());
  let id = null, secret = null;
  if (basic) {
    if (form.has("client_secret")) return oauthError(400, "invalid_request");
    if (form.has("client_id") && form.get("client_id") !== basic.id) return failed(badClient());
    ({ id, secret } = basic);
  } else {
    id = form.get("client_id");
    secret = form.get("client_secret");
  }
  const [idOk, secretOk] = await Promise.all([sameSecret(id, setup.client.id), sameSecret(secret, setup.client.secret)]);
  if (!idOk || !secretOk) return failed(badClient());

  if (form.get("grant_type") !== "authorization_code") return oauthError(400, "unsupported_grant_type");

  // SPENT FIRST: whatever follows, this code is used up
  let row = null;
  try { row = await spendCode(env, form.get("code")); } catch { return oauthError(503, "temporarily_unavailable"); }
  if (!row) return failed(oauthError(400, "invalid_grant"));
  const verifier = form.get("code_verifier") || "";
  const pkce = VERIFIER.test(verifier) && sameCode(await sha256u(verifier), String(row.code_challenge || ""));
  const bound = row.client_id === setup.client.id && form.get("redirect_uri") === row.redirect_uri;
  if (!pkce || !bound) return failed(oauthError(400, "invalid_grant"));

  const t = now();
  const claims = { iss: ISSUER, aud: setup.client.id, sub: await subOf(row.email), email: row.email,
    email_verified: true, iat: t, exp: t + ID_TOKEN_SECONDS };
  const at = Number(row.auth_time);
  if (Number.isInteger(at) && at > 0 && at <= t) claims.auth_time = at;
  if (typeof row.nonce === "string" && row.nonce) claims.nonce = row.nonce;
  const idToken = await signJwt(env, claims);
  if (!idToken) return oauthError(503, "temporarily_unavailable");
  return answer(200, { access_token: random(32), token_type: "Bearer", expires_in: ID_TOKEN_SECONDS,
    id_token: idToken });
}

/* Asked as a page or a link: not how a token is ever got. */
export async function onRequestGet() {
  return oauthError(405, "invalid_request", [["Allow", "POST"]]);
}
