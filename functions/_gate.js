/* THE FRONT DOOR IS PUBLIC; THE BOARDS ARE SIGNED-IN (owner, 2026-10-22).
 *
 * "We need a better landing page, accessing the job, company, and
 * conference boards should be sign in", and anyone may sign in, free (an
 * email and a one-time code). So a visitor with no sign-in who asks for the
 * site's front door, /, gets the landing page (landing.html, no script,
 * nothing of the board's), and the files that page loads - the brand's own
 * art and its share image - are served to anyone. Everything else stays
 * exactly as gated as before: every board page, every company and
 * conference page, every data file and endpoint. A signed-in person at /
 * gets the boards, so one address serves both. The admin door now checks a
 * ROLE as well as a sign-in (functions/_roles.js), because a sign-in no
 * longer means the owner listed you.
 *
 * Before 2026-10-22 the whole site was signed-in only, and the rest of this
 * note is that history, still true of every path but the front door:
 *
 * THE WHOLE SITE IS SIGNED-IN ONLY UNTIL LAUNCH.
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

/* The landing page, served at / to a visitor with no verified sign-in.
 * Pages answers it at /landing (it redirects /landing.html there). */
export const LANDING = "/landing";

/* What the landing page loads, and nothing else: the mascot kit and the
 * front page's share card. Matched on the RAW path with a closed character
 * set, so no encoded slash, dot segment or other spelling can reach a
 * board file through it. Company logos are the board's and stay gated. */
export const PUBLIC = [
  /^\/assets\/mascot\/(svg|png|favicon)\/[A-Za-z0-9@_-]+\.(svg|png)$/,
  /^\/assets\/og\/home\.png$/,
  /^\/robots\.txt$/,
  /^\/sitemap\.xml$/,
  // the sector names, which the alerts page draws its choices from: the
  // public taxonomy, not the board (company-names.json stays gated)
  /^\/data\/sectors\.json$/,
];

/* The front door: / with no query, or only the tracking parameters a
 * shared link picks up. /?tab=jobs, /?role=... and every other query is the
 * board, deep-linked, and needs a sign-in like any board page. */
export function isFrontDoor(url) {
  if (url.pathname !== "/") return false;
  for (const k of url.searchParams.keys()) {
    if (!/^(utm_[a-z]+|ref|gclid|dclid|fbclid|li_fat_id|msclkid|twclid|ttclid|igshid|mc_cid|mc_eid|_hsenc|_hsmi)$/i.test(k)) return false;
  }
  return true;
}

function isPublicFile(request) {
  const raw = new URL(request.url).pathname;
  return PUBLIC.some((rx) => rx.test(raw));
}

/* The landing page, from the site's own files. Never cached by anyone: the
 * same address answers the boards to a signed-in person, so a kept copy of
 * either would show the wrong one after a sign-in or a sign-out. No
 * noindex: this is the page meant to be found. */
async function landingPage(request, env) {
  let res = null;
  try {
    res = env && env.ASSETS ? await env.ASSETS.fetch(new URL(LANDING, request.url).toString()) : null;
  } catch { res = null; }
  if (!res || res.status !== 200) return null;
  const body = request.method === "HEAD" ? null : await res.text();
  return new Response(body, { status: 200, headers: {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store", "vary": "Cookie" } });
}

const HEADERS = { "cache-control": "no-store", "x-robots-tag": "noindex" };

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function holdingPage(url, status, line, heading = "Sign in to see this page.") {
  // Back to the page they asked for once they are signed in. login.js only
  // follows a same-site path, so this cannot become an open redirect.
  const back = `${SITE}/admin/api/login?to=${encodeURIComponent(url.pathname + url.search)}`;
  // THE SIGN-IN PAGE IS OPTION 1 of the landing canvas (owner, 2026-10-22: "I do
  // love 1 sign in"). It names nothing about the page asked for - that page is
  // not public - and carries no script, so the holding policy (no script at
  // all) holds. Its one image is the mascot kit's, which the gate serves to
  // anyone (PUBLIC).
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Sign in to see this page &middot; ${esc(NAME)}</title>
<link rel="icon" href="/assets/mascot/svg/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&amp;display=swap">
<style>
  *,*::before,*::after{box-sizing:border-box}
  body{margin:0;background:#E8F1F7;color:#1F2536;
    font:18px/1.55 Archivo,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  main{max-width:656px;margin:0 auto;padding:120px 48px;display:flex;justify-content:center}
  .card{width:100%;max-width:560px;background:#FAF7F0;border:1px solid #C9DCE8;border-radius:16px;
    padding:48px;display:flex;flex-direction:column;align-items:center;text-align:center}
  .card img{display:block;width:96px;height:96px}
  .kick{margin:16px 0 0;font-size:13px;line-height:18px;font-weight:600;letter-spacing:.1em;
    text-transform:uppercase;color:#556F82}
  h1{margin:12px 0 0;font-size:36px;line-height:40px;font-weight:800;letter-spacing:-.025em;text-wrap:balance}
  p{margin:16px 0 0}
  .next{margin-top:20px;padding-top:16px;width:100%;border-top:1px solid #C9DCE8;
    font-size:15px;line-height:22px;color:#556F82}
  .acts{margin-top:32px;width:100%;display:flex;flex-direction:column;align-items:center;gap:8px}
  .btn{display:flex;align-items:center;justify-content:center;width:100%;max-width:320px;min-height:52px;
    padding:0 24px;border-radius:10px;background:#0B57C4;color:#E8F1F7;font-size:18px;font-weight:600;
    text-decoration:none}
  .btn:hover{background:#1F2536;color:#E8F1F7}
  .about{display:inline-flex;align-items:center;min-height:44px;font-size:16px;font-weight:600;color:#0B57C4}
  .about:hover{color:#1F2536}
  a:focus-visible{outline:3px solid #0B57C4;outline-offset:3px}
  @media (max-width:720px){main{padding:48px 20px}.card{padding:32px 24px}h1{font-size:30px;line-height:36px}}
</style></head>
<body><main><div class="card">
<img src="/assets/mascot/svg/head-offer-in.svg" width="96" height="96" alt="">
<p class="kick">${esc(NAME)}</p>
<h1>${esc(heading)}</h1>
<p>${esc(line)}</p>
<p class="next">Once you are in, subscribe to job alerts and the roles that fit come to you.</p>
<div class="acts">
<a class="btn" href="${esc(back)}">Sign in</a>
<a class="about" href="${esc(SITE)}/">What is ${esc(NAME)}?</a>
</div>
</div></main></body></html>
`;
  return new Response(html, {
    status, headers: { ...HEADERS, "content-type": "text/html; charset=utf-8" } });
}

function refuse(request, url, status, error, line, heading) {
  const page = (request.method === "GET" || request.method === "HEAD")
    && !url.pathname.startsWith("/api/") && !url.pathname.startsWith("/admin/api/");
  if (page) return holdingPage(url, status, line, heading);
  return new Response(JSON.stringify({ error }), {
    status, headers: { ...HEADERS, "content-type": "application/json" } });
}

/* null means "let it through"; anything else is the response to send. */
/* A MAIL CLIENT'S ONE-CLICK UNSUBSCRIBE (RFC 8058) passes the gate. Digests
 * go out while the site is private, they carry List-Unsubscribe, and the
 * mail provider's POST carries no cookie and no sign-in by design - so the
 * gate turned every Unsubscribe button into a silent 403 (third review,
 * 2026-10-07). Only that exact shape passes: a POST to /api/alerts with a
 * well-formed ?t= and a form body. alerts.js then requires the body to say
 * List-Unsubscribe=One-Click and answers the same whatever the token, so it
 * opens nothing and tells nothing. A JSON POST, a GET, or no token is gated. */
function isOneClickUnsubscribe(request, url) {
  if (request.method !== "POST" || url.pathname !== "/api/alerts") return false;
  if (!/^[A-Za-z0-9_-]{40,64}$/.test(url.searchParams.get("t") || "")) return false;
  const ctype = (request.headers.get("content-type") || "").toLowerCase();
  return ctype.includes("application/x-www-form-urlencoded")
      || ctype.includes("multipart/form-data");
}

/* A SUBSCRIBER'S OWN LINK PASSES (review, 2026-10-22). Every alert email
 * carries the subscription's token in its "Change what you get" and "Stop
 * these emails" links (scripts/digest.py) and in the confirmation email
 * (functions/api/alerts.js): the token IS the subscriber's identity, and
 * alerts.js answers the same for any token it does not hold. Gated, a
 * subscriber on a phone that is not signed in had to sign in to stop the
 * emails, which the law on commercial email does not allow and the landing
 * page promised against. So these pass, and only with a well-formed token:
 *   GET/HEAD /alerts?t=<token>        the settings page itself, no data
 *   GET /api/alerts?t=<token>         that subscription's settings
 *   POST /api/alerts, JSON, action confirm/update/sync/stop with a
 *        well-formed body.token      read off a CLONE, so alerts.js still
 *                                     reads the body it was sent
 * A NEW subscription (action "subscribe", no token) still needs a sign-in:
 * the owner wants people to log in and subscribe. */
const TOKEN = /^[A-Za-z0-9_-]{40,64}$/;
const TOKEN_ACTIONS = new Set(["confirm", "update", "sync", "stop"]);

async function isSubscriberLink(request, url) {
  const p = url.pathname;
  const reading = request.method === "GET" || request.method === "HEAD";
  if (reading && /^\/alerts(\.html)?\/?$/.test(p)) return TOKEN.test(url.searchParams.get("t") || "");
  if (p !== "/api/alerts") return false;
  if (request.method === "GET") return TOKEN.test(url.searchParams.get("t") || "");
  if (request.method !== "POST") return false;
  const ctype = (request.headers.get("content-type") || "").toLowerCase();
  if (!ctype.includes("application/json")) return false;
  let body = null;
  try { body = await request.clone().json(); } catch { return false; }
  return !!body && typeof body === "object" && !Array.isArray(body)
    && TOKEN_ACTIONS.has(body.action) && typeof body.token === "string" && TOKEN.test(body.token);
}

const SIGN_IN_LINE = "The job, company and conference boards are for signed-in members. "
  + "Signing in is free: your email and a one-time code, no password.";

export async function gate(request, env) {
  if (!GATED) return null;
  const url = new URL(request.url);
  if (OPEN.has(url.pathname)) return null;
  if (isOneClickUnsubscribe(request, url)) return null;
  if (await isSubscriberLink(request, url)) return null;
  const token = tokenOf(request);
  let claims = null, unverifiable = false;
  if (token) {
    try { claims = await verify(token, env); }
    catch { unverifiable = true; }
  }
  if (claims) return null;
  // NOT SIGNED IN (or a sign-in that cannot be checked): the front door and
  // its own files, and nothing else
  const reading = request.method === "GET" || request.method === "HEAD";
  if (reading && isFrontDoor(url)) {
    const page = await landingPage(request, env);
    if (page) return page;
  }
  if (reading && isPublicFile(request)) return null;
  if (unverifiable) {
    return refuse(request, url, 503, "sign_in_unverifiable",
      "Your sign-in could not be checked just now. Try again in a minute.",
      "We could not check your sign-in.");
  }
  return refuse(request, url, 403, "not_signed_in",
    token ? "Your sign-in has expired or is not for this site. Sign in again." : SIGN_IN_LINE);
}
