/* WHAT THE BOARD IS AS A SIGN-IN, for any OpenID Connect client that asks:
 * GET /.well-known/openid-configuration (design C, functions/_oidc.js).
 *
 * Access is given the three addresses by hand (DEPLOY.md §3f) and does not
 * need this; it is the standard description, so a client or a person can
 * check what the board offers without reading code. OPEN in
 * functions/_gate.js. Pages Functions routes a folder whose name starts with
 * a dot like any other (wrangler's filepath routing skips nothing).
 */
import { onSite } from "../_session.js";
import { ISSUER, AUTHORIZE, TOKEN, JWKS } from "../_oidc.js";

export function discovery() {
  return {
    issuer: ISSUER,
    authorization_endpoint: ISSUER + AUTHORIZE,
    token_endpoint: ISSUER + TOKEN,
    jwks_uri: ISSUER + JWKS,
    response_types_supported: ["code"],
    response_modes_supported: ["query"],
    grant_types_supported: ["authorization_code"],
    subject_types_supported: ["public"],
    id_token_signing_alg_values_supported: ["ES256"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["client_secret_basic", "client_secret_post"],
    scopes_supported: ["openid", "email"],
    claims_supported: ["iss", "aud", "sub", "email", "email_verified", "iat", "exp", "auth_time", "nonce"],
  };
}

export async function onRequestGet({ request }) {
  const away = onSite(new URL(request.url));
  if (away) return new Response(null, { status: 302, headers: { location: away, "cache-control": "no-store" } });
  return new Response(JSON.stringify(discovery()), { status: 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=300" } });
}

export const onRequestHead = onRequestGet;
