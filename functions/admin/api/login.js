/* THE LOG-IN LINK. This path is under /admin, so Access makes the person
 * sign in before this code runs; all it does afterwards is send them back
 * where they came from. `to` must be a path on this site - a full URL here
 * would turn the login link into an open redirect.
 *
 * Checking the string's first characters was not enough. `to=/%09/evil`
 * decodes to "/<TAB>/evil", which starts with one slash and passed; the
 * browser strips the tab and lands on //evil, another site (found in review,
 * 2026-10-05, the day the site-wide gate began pointing every visitor here).
 * What decides is the resolve: `to` is parsed against this origin the way a
 * browser would parse the Location, must still BE this origin, and is
 * answered as the path it resolved to - checked again, because "/.//evil"
 * resolves to the path "//evil", and that as a bare Location is
 * protocol-relative and leaves. Refusing control characters and spaces
 * first is a second line only: mutation-tested 2026-10-05, removing it
 * changes no outcome the harness can reach. */
export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const to = url.searchParams.get("to") || "/";
  let dest = "/";
  if (to.startsWith("/") && !to.startsWith("//") && !to.includes("\\")
      && !/[\x00-\x20\x7f]/.test(to)) {
    try {
      const d = new URL(to, url.origin);
      const path = d.pathname + d.search + d.hash;
      if (d.origin === url.origin && !path.startsWith("//")) dest = path;
    } catch { dest = "/"; }
  }
  return new Response(null, { status: 302, headers: { location: dest, "cache-control": "no-store" } });
}
