/* THE LOG-IN LINK. This path is under /admin, so Access makes the person
 * sign in before this code runs; all it does afterwards is send them back
 * where they came from. `to` must be a path on this site, held there by
 * _to.js, which /signin/google shares. */
import { sameSitePath } from "../../_to.js";

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const dest = sameSitePath(url.searchParams.get("to"), url.origin);
  return new Response(null, { status: 302, headers: { location: dest, "cache-control": "no-store" } });
}
