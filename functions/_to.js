/* WHERE A SIGN-IN SENDS A PERSON BACK TO, in one place.
 *
 * Two links take a `to`: /admin/api/login (functions/admin/api/login.js) and
 * /signin/google (functions/signin/google.js). `to` must be a path on this
 * site - a full URL would turn either link into an open redirect - and a
 * second copy of this check would be a second place for it to be wrong.
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
/* A `to` longer than this is "/": every sign-in seals `to` into a link, a
 * cookie or Google's state, and past about 3,000 characters those no longer
 * fit (a browser drops a cookie over 4 KB), so an email went out that could
 * never sign anybody in (final review, 2026-11-13). */
export const MAX_TO = 1024;

export function sameSitePath(to, origin) {
  to = to || "/";
  if (typeof to !== "string" || to.length > MAX_TO) return "/";
  let dest = "/";
  if (to.startsWith("/") && !to.startsWith("//") && !to.includes("\\")
      && !/[\x00-\x20\x7f]/.test(to)) {
    try {
      const d = new URL(to, origin);
      const path = d.pathname + d.search + d.hash;
      if (d.origin === origin && !path.startsWith("//")) dest = path;
    } catch { dest = "/"; }
  }
  return dest;
}
