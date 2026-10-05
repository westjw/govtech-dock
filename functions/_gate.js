/* THE WHOLE SITE IS SIGNED-IN ONLY UNTIL LAUNCH.
 *
 * The owner decided on 2026-10-05 that SLED JOBS stays private until it is
 * ready to go live: 15 companies on the board had no website, and the rule
 * since 2026-10-04 is that nothing is published without one. Cloudflare
 * Access is the main door - the existing /admin application, widened to the
 * whole of each hostname (DEPLOY.md, "Signed-in only until launch"). This is
 * the same door in code, for the reason functions/admin/_middleware.js
 * exists: Access never covered the *.pages.dev alias, and on 2026-09-25 that
 * alias served /admin to anyone while sledjobs.com asked for a sign-in. A
 * gate that only Access enforces is one hostname away from open.
 *
 * It runs first in functions/_middleware.js, so it stands in front of every
 * page, every data file and every endpoint the project serves, on every
 * hostname. It checks the same thing the /admin door checks - a VERIFIED
 * Access token for the same application (functions/_access.js) - and fails
 * closed the same three ways: no token is 403, a token that does not verify
 * is 403, and keys that cannot be fetched are 503. Never 200 - WHILE THIS
 * CODE RUNS. On the Workers Free plan, once the day's 100,000 Functions
 * requests are spent, a project left on "Fail open" serves its static files
 * with no Function at all, so no gate and no /admin door, until midnight
 * UTC - and refused requests count toward that allowance, so anyone can
 * spend it. The project must be set to Fail closed (DEPLOY.md §3); that is
 * a dashboard setting no code here can check.
 *
 * Two paths stay open, the same two the /admin door leaves open and for the
 * same reasons: /admin/api/login is how a person reaches Access in the first
 * place, and /admin/api/whoami answers {signed_in: false} and holds nothing.
 * The /admin door still stands behind this one for everything else under
 * /admin, so those paths are checked twice; that is the price of each door
 * being whole on its own.
 *
 * A refused page gets a short holding page with a sign-in link rather than a
 * bare error, because the person most likely to see it is somebody who was
 * sent a link. It names nothing about the board. A refused endpoint or a
 * non-GET gets JSON. Every refusal is noindex and no-store.
 *
 * LAUNCH IS ONE LINE: set GATED to false and push, then narrow the Access
 * application back to /admin (DEPLOY.md). selftest drives the gate in
 * whichever state it is in, so the flip needs no test edit.
 */
import { verify, tokenOf } from "./_access.js";
import { SITE, NAME } from "./_brand.js";

export const GATED = true;

export const OPEN = new Set(["/admin/api/whoami", "/admin/api/login"]);

const HEADERS = { "cache-control": "no-store", "x-robots-tag": "noindex" };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function holdingPage(url, status, line) {
  // Back to the page they asked for once they are signed in. login.js only
  // follows a same-site path, so this cannot become an open redirect.
  const back = `${SITE}/admin/api/login?to=${encodeURIComponent(url.pathname + url.search)}`;
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>${esc(NAME)}</title>
<style>
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    background:#E8F1F7;color:#1F2536;
    font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  main{max-width:34rem;padding:2rem 1.25rem}
  h1{font-size:1.7rem;line-height:1.2;margin:0 0 .6rem;text-wrap:balance}
  p{margin:0 0 1rem}
  a{color:#0B57C4;font-weight:600}
  a:focus-visible{outline:3px solid #0B57C4;outline-offset:3px}
</style></head>
<body><main>
<h1>${esc(NAME)} opens soon.</h1>
<p>${esc(line)}</p>
<p><a href="${esc(back)}">Sign in</a></p>
</main></body></html>
`;
  return new Response(html, {
    status, headers: { ...HEADERS, "content-type": "text/html; charset=utf-8" } });
}

function refuse(request, url, status, error, line) {
  const page = (request.method === "GET" || request.method === "HEAD")
    && !url.pathname.startsWith("/api/") && !url.pathname.startsWith("/admin/api/");
  if (page) return holdingPage(url, status, line);
  return new Response(JSON.stringify({ error }), {
    status, headers: { ...HEADERS, "content-type": "application/json" } });
}

/* null means "let it through"; anything else is the response to send. */
export async function gate(request, env) {
  if (!GATED) return null;
  const url = new URL(request.url);
  if (OPEN.has(url.pathname)) return null;
  const token = tokenOf(request);
  if (!token) {
    return refuse(request, url, 403, "not_signed_in",
      "The board is being finished before it goes public. If you are working on it, sign in.");
  }
  let claims = null;
  try { claims = await verify(token, env); }
  catch {
    return refuse(request, url, 503, "sign_in_unverifiable",
      "Your sign-in could not be checked just now. Try again in a minute.");
  }
  if (!claims) {
    return refuse(request, url, 403, "not_signed_in",
      "Your sign-in has expired or is not for this site. Sign in again.");
  }
  return null;
}
