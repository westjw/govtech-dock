/* THE SIGN-IN PAGE, /signin (owner, 2026-11-13: "anyone can sign in", then
 * "build", so that members stop taking Cloudflare Access seats).
 *
 * Google when it is on (GOOGLE in _gate.js), then an address: POST
 * /signin/email mails a link and a code. A person already signed in goes
 * straight on. Every link to here carries `to`, held to a path on this site
 * by _to.js, and the page is always drawn on the site's own address, because
 * the cookie a sign-in sets is the one that address reads.
 */
import { sameSitePath } from "../_to.js";
import { configured, sessionOf, onSite, unseal, ours, dropCookie, PENDING } from "../_session.js";
import { signinForm, messagePage, redirect } from "../_signin_page.js";

/* What another door sends back here as ?e=, said in our words: nothing the
 * address carries is ever printed. */
export const NOTES = {
  google_off: "Google sign-in is not switched on yet. Use your email below.",
  google_cancel: "The Google sign-in was cancelled. Try again, or use your email.",
  google_failed: "Google did not finish the sign-in. Try again, or use your email.",
};

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const away = onSite(url);
  if (away) return redirect(away, [], 302);
  const to = sameSitePath(url.searchParams.get("to"), url.origin);
  if (await sessionOf(request, env)) return redirect(to, [], 302);
  if (!configured(env)) {
    return messagePage(503, "Sign-in is being set up",
      "Signing in is not switched on yet. Try again in a little while.", to);
  }
  // A SIGN-IN ALREADY ASKED FOR on this device: the code box, where the
  // email says to type the code (final review, 2026-11-13: /signin had no
  // code box, so a person holding a code could only ask again, which spent
  // an allowance and made that code stop matching). "again=1" is the check
  // page's "start again with another address", and forgets the pending one.
  const again = url.searchParams.get("again") === "1";
  if (!again && await unseal(env, "pending", ours(request, PENDING))) return redirect("/signin/check", [], 302);
  const e = url.searchParams.get("e");
  return signinForm(to, { message: Object.hasOwn(NOTES, e || "") ? NOTES[e] : "",
    headers: again ? [["Set-Cookie", dropCookie(PENDING)]] : [] });
}

/* A HEAD (curl -I, a link checker) gets the GET's answer: with no handler
 * Pages hands it to the asset server, which says 404. */
export const onRequestHead = onRequestGet;
