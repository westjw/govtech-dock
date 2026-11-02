/* "CONTINUE WITH GOOGLE" (owner, 2026-11-02: "Can I have the cloudflare sign
 * in and future google sign in on the landing page").
 *
 * Cloudflare Access has no link that picks one sign-in method when an
 * application allows two; it shows its own chooser. So Google gets an Access
 * application of its own on this one path, allowing Google only, with
 * instant authentication on, which skips the chooser and goes straight to
 * Google (DEPLOY.md §3d). Once Google has answered, Access lets the request
 * through to here, and all this does is send the person on to
 * /admin/api/login. That is the main application, which allows Google too,
 * so Access issues its token from the sign-in it already holds (the global
 * session on the team domain) without asking again. The site still checks
 * one audience, the main application's: this application's token is never
 * read, so it can open nothing.
 *
 * Until that application exists nothing guards this path, and it sends the
 * person to the ordinary sign-in, which is harmless. The landing page and the
 * sign-in page show the button only once GOOGLE is true in _gate.js. `to` is
 * held to a path on this site exactly as login.js holds it (_to.js), and the
 * gate leaves this path open (OPEN) for the same reason it leaves login open. */
import { sameSitePath } from "../_to.js";

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const dest = sameSitePath(url.searchParams.get("to"), url.origin);
  return new Response(null, { status: 302, headers: {
    location: `/admin/api/login?to=${encodeURIComponent(dest)}`, "cache-control": "no-store" } });
}
