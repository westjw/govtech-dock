/* THE LINK IN THE EMAIL: GET /signin/link?t=... asks, POST signs in.
 *
 * Opening the link draws one button, "Sign in as <address>", and pressing it
 * posts the same token back (security review, 2026-11-13). Until then a GET
 * signed in on the spot, which had two costs:
 *   - a mail scanner that opens every link (Outlook's Safe Links, Mimecast)
 *     was handed a real 30-day sign-in for the person's address;
 *   - a link somebody else sent you replaced your own sign-in with theirs,
 *     without a word.
 * A scanner never submits a form, and the button names whose sign-in it is.
 * The POST must come from our own page (Origin), like every POST here.
 *
 * The link is not single-use (nothing is stored), so a scanner that opened
 * it first has spent nothing; it works for LINK_MINUTES, on any device.
 */
import { sameSitePath } from "../_to.js";
import { validEmail } from "../_address.js";
import { unseal, sessionOf, sessionCookie, dropCookie, fromOurPage, PENDING } from "../_session.js";
import { confirmPage, messagePage, redirect } from "../_signin_page.js";

async function fromLink(env, t) {
  const p = await unseal(env, "link", t);
  const email = p && validEmail(p.e);
  return email ? { email, to: p.to } : null;
}

const runOut = () => messagePage(400, "That link has run out",
  "A sign-in link works for 15 minutes. Ask for a new one.");

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const t = url.searchParams.get("t");
  const p = await fromLink(env, t);
  if (!p) return runOut();
  const to = sameSitePath(p.to, url.origin);
  // already signed in as this very address: nothing to confirm
  const now = await sessionOf(request, env);
  if (now && now.email === p.email) return redirect(to, [], 302);
  return confirmPage(p.email, t, !!now);
}

export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  if (!fromOurPage(request)) {
    return messagePage(403, "That did not come from our sign-in page",
      "Open the link from the email again and press Sign in.");
  }
  let form = null;
  try { form = await request.formData(); } catch { form = null; }
  const t = form && form.get("t");
  const p = await fromLink(env, typeof t === "string" ? t : "");
  if (!p) return runOut();
  return redirect(sameSitePath(p.to, url.origin),
    [["Set-Cookie", await sessionCookie(env, p.email, "email")], ["Set-Cookie", dropCookie(PENDING)]]);
}

/* A HEAD (curl -I, a link checker) gets the GET's answer: with no handler
 * Pages hands it to the asset server, which says 404. */
export const onRequestHead = onRequestGet;
