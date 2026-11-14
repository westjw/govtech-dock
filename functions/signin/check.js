/* "CHECK YOUR EMAIL": GET /signin/check, where asking for an email lands.
 *
 * POST /signin/email answers with a redirect here rather than with the page
 * itself (runtime review, 2026-11-13): drawn as the POST's answer, a reload
 * offered to send the form again, which mailed a second email, spent the
 * allowances, and made the first email's code stop matching. This is a GET
 * drawn from the pending cookie alone (it holds the address and `to`), so a
 * reload is only a reload. A wrong code comes back here too (?e=wrong).
 */
import { sameSitePath } from "../_to.js";
import { validEmail } from "../_address.js";
import { unseal, ours, sessionOf, PENDING } from "../_session.js";
import { checkPage, messagePage, redirect } from "../_signin_page.js";

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const p = await unseal(env, "pending", ours(request, PENDING));
  const email = p && validEmail(p.e);
  // Back, after the code signed in (the pending cookie is gone by then): the
  // person is in, not "run out" (final review, 2026-11-13)
  if (!email && await sessionOf(request, env)) return redirect("/", [], 302);
  if (!email) {
    return messagePage(400, "That sign-in has run out",
      "A sign-in email works for 15 minutes, on the device that asked for it. Ask for a new one.");
  }
  const wrong = url.searchParams.get("e") === "wrong";
  return checkPage(email, sameSitePath(p.to, url.origin), wrong
    ? { status: 400, message: "That code does not match the email. Check it and try again." } : {});
}

/* A HEAD gets the GET's answer (see index.js). */
export const onRequestHead = onRequestGet;
