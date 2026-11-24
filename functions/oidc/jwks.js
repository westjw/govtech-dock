/* THE KEYS THAT VERIFY WHAT THE BOARD SIGNS: GET /oidc/jwks (design C,
 * functions/_oidc.js).
 *
 * Access reads it to check the ID tokens /oidc/token hands it; Job Hunter
 * reads it to check the popout's assertion. Public halves only - kty, crv,
 * x, y, kid, alg, use, never `d` - the signing key's and, during a rotation,
 * the earlier one's (OIDC_SIGNING_JWK_OLD). OPEN in functions/_gate.js: the
 * readers carry no sign-in. Cached five minutes by anyone, so a new key is
 * seen within five minutes of a deploy. No key: 503, and nothing cached.
 */
import { onSite } from "../_session.js";
import { publicKeys } from "../_oidc.js";

export async function onRequestGet({ request, env }) {
  const away = onSite(new URL(request.url));
  if (away) return new Response(null, { status: 302, headers: { location: away, "cache-control": "no-store" } });
  const keys = await publicKeys(env);
  if (!keys.length) {
    return new Response(JSON.stringify({ error: "not_configured" }), { status: 503,
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
  }
  return new Response(JSON.stringify({ keys }), { status: 200,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=300" } });
}

export const onRequestHead = onRequestGet;
