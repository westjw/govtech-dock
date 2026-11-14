/* SIGNING OUT: GET /signin/out asks, POST does it.
 *
 * A GET only draws the question, so a link or an image on another page
 * cannot sign anybody out. The POST drops our cookies. A person also signed
 * in through Access (the owner, the admins) is told so and given a plain
 * link to Access's own sign-out: a redirect there from the form would be a
 * form navigation leaving this site, which the sign-in pages' policy
 * (form-action 'self') refuses in Chrome (security review, 2026-11-13).
 */
import { tokenOf, verify } from "../_access.js";
import { fromOurPage, dropCookie, SESSION, PENDING } from "../_session.js";
import { signoutPage, signedOutPage, messagePage, redirect } from "../_signin_page.js";

export async function onRequestGet() {
  return signoutPage();
}

export async function onRequestPost({ request, env }) {
  if (!fromOurPage(request)) {
    return messagePage(403, "That did not come from this site",
      "Open the sign-out page on this site to sign out.");
  }
  const drop = [["Set-Cookie", dropCookie(SESSION)], ["Set-Cookie", dropCookie(PENDING)]];
  let access = false;
  try { access = !!(tokenOf(request) && await verify(tokenOf(request), env)); } catch { access = false; }
  return access ? signedOutPage(drop) : redirect("/", drop);
}

/* A HEAD (curl -I, a link checker) gets the GET's answer: with no handler
 * Pages hands it to the asset server, which says 404. */
export const onRequestHead = onRequestGet;
