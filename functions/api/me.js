/* THE SIGNED-IN PERSON'S OWN ADDRESS, to them: GET /api/me (2026-11-14).
 *
 * The alerts page fills in the address a person signed in with, so a
 * newcomer does not type it twice (owner: "do that too, good idea"). Behind
 * the gate like every endpoint, so it answers only a signed-in browser, and
 * only with that browser's own address; no-store, and no CORS header, so no
 * other site can read it. /api/whoami still never carries an address: the
 * account menu needs a handle, not a person.
 */
import { signedIn } from "../_identity.js";

export async function onRequestGet({ request, env }) {
  const me = await signedIn(request, env);
  return new Response(JSON.stringify({ email: me ? me.email : null, via: me ? me.via : null }), {
    status: 200, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
}
