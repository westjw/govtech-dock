/* THE LOG-IN LINK. This path is under /admin, so Access makes the person
 * sign in before this code runs; all it does afterwards is send them back
 * where they came from. `to` must be a path on this site - a full URL here
 * would turn the login link into an open redirect. */
export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  let to = url.searchParams.get("to") || "/";
  if (!to.startsWith("/") || to.startsWith("//") || to.includes("\\")) to = "/";
  return new Response(null, { status: 302, headers: { location: to, "cache-control": "no-store" } });
}
