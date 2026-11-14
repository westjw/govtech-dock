/* TYPING THE CODE: POST /signin/code, from the "Check your email" page.
 *
 * The code is checked against the pending cookie this device was given when
 * it asked (functions/_session.js codeFor), so it signs in only that device.
 * A wrong code says so and keeps the pending sign-in; an expired one asks
 * for a new email.
 */
import { sameSitePath } from "../_to.js";
import { validEmail } from "../_address.js";
import { fromOurPage, ours, unseal, codeFor, normCode, sameCode, sessionCookie, dropCookie,
         PENDING } from "../_session.js";
import { messagePage, redirect, backToSignin } from "../_signin_page.js";

export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  if (!fromOurPage(request)) {
    return messagePage(403, "That did not come from our sign-in page",
      "Open the sign-in page on this site and ask again.");
  }
  const p = await unseal(env, "pending", ours(request, PENDING));
  const email = p && validEmail(p.e);
  if (!p || !email || typeof p.n !== "string") {
    return messagePage(400, "That sign-in has run out",
      "A code works for 15 minutes, on the device that asked for it. Ask for a new email.");
  }
  const to = sameSitePath(p.to, url.origin);
  let form = null;
  try { form = await request.formData(); } catch { form = null; }
  const typed = form && form.get("code");
  if (!sameCode(normCode(typeof typed === "string" ? typed : ""), await codeFor(env, p))) {
    return redirect("/signin/check?e=wrong");    // a GET page: a reload resends nothing
  }
  return redirect(to, [["Set-Cookie", await sessionCookie(env, email, "email")],
                       ["Set-Cookie", dropCookie(PENDING)]]);
}

/* /signin/code opened as an address: where the person was (see email.js). */
export const onRequestGet = backToSignin;
export const onRequestHead = backToSignin;
