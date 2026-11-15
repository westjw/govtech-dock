/* THE SIGN-IN PAGES, drawn in one place (owner, 2026-11-13).
 *
 * The card is the gate's sign-in card (option 1 of the landing canvas,
 * functions/_card.js, light and dark) with a form in it. No script: these pages are served
 * under the "signin" policy (scripts/csp.py), which allows no script and
 * lets a form post only to this site. Every page here is noindex, no-store
 * and no-referrer, and names nothing about the board.
 */
import { NAME } from "./_brand.js";
import { G_MARK, GOOGLE } from "./_gate.js";
import { CARD_CSS, FORM_CSS, THEME_HTML, switchSlot, emailForm, esc, themeOf, themeSwitch } from "./_card.js";
import { LINK_MINUTES, SESSION_DAYS, unseal, ours, PENDING } from "./_session.js";

export { esc };

function doc(title, inner, google, here = "") {
  return `<!doctype html>
${THEME_HTML}<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<meta name="referrer" content="same-origin">
<title>${esc(title)} &middot; ${esc(NAME)}</title>
<link rel="icon" href="/assets/mascot/svg/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&amp;display=swap">
${google ? '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@500&amp;display=swap">\n' : ""}<style>
${CARD_CSS}${FORM_CSS}
</style></head>
<body><main><div class="card">
<img src="/assets/mascot/svg/head-offer-in.svg" width="96" height="96" alt="">
<p class="kick">${esc(NAME)}</p>
${inner}
${switchSlot(here)}
</div></main></body></html>
`;
}

/* THE READER'S THEME, put on a sign-in page by the root middleware, which
 * holds the request (these builders do not): light when the sled-theme
 * cookie says so, and the switch to the other one, back to this page. A POST
 * answered with a page (a refusal) switches back to the sign-in page. */
export function themed(html, request) {
  const theme = themeOf(request);
  const url = new URL(request.url);
  let out = String(html);
  if (theme === "light") out = out.replace(THEME_HTML, '<html lang="en" data-theme="light">');
  return out.replace(/<!--sj-theme-switch(?::([A-Za-z0-9%._~!*'()-]*))?-->/, (_m, named) => {
    let here = request.method === "GET" || request.method === "HEAD" ? url.pathname + url.search : "/signin";
    if (named) { try { here = decodeURIComponent(named); } catch { /* keep the default */ } }
    return themeSwitch(theme, here);
  });
}

/* SAME-ORIGIN, NEVER NO-REFERRER. Under no-referrer the Fetch standard sends
 * every POST with `Origin: null`, same-origin forms included, and every form
 * here is checked by its Origin (_session.js fromOurPage): Chrome would have
 * sent each sign-in as "from another site" (runtime review, 2026-11-13).
 * same-origin still tells Google Fonts and every other site nothing. */
export const REFERRER = "same-origin";

/* headers: [[name, value], ...], so a page can set more than one cookie */
export function respond(status, html, headers = []) {
  const h = new Headers({ "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
    "x-robots-tag": "noindex", "referrer-policy": REFERRER });
  for (const [k, v] of headers) h.append(k, v);
  return new Response(html, { status, headers: h });
}

export function redirect(location, headers = [], status = 303) {
  const h = new Headers({ location, "cache-control": "no-store", "referrer-policy": REFERRER });
  for (const [k, v] of headers) h.append(k, v);
  return new Response(null, { status, headers: h });
}

/* A sign-in POST address opened as a GET: the check page while a sign-in is
 * pending on this device, else the sign-in page. Never the asset server's
 * 404, which talks about roles that came off the board. */
export async function backToSignin({ request, env }) {
  const live = await unseal(env, "pending", ours(request, PENDING));
  return redirect(live ? "/signin/check" : "/signin", [], 302);
}

const note = (s) => (s ? `<p class="note" role="alert">${esc(s)}</p>` : "");
const again = (to) => `/signin?to=${encodeURIComponent(to || "/")}`;

/* The sign-in page: Google (when it is on), then an address. */
export function signinForm(to, { email = "", message = "", status = 200, google = GOOGLE, headers = [] } = {}) {
  const inner = `<h1>Sign in</h1>
<p>Free, and no password.</p>
${note(message)}
<div class="acts">
${google ? `<a class="btn btn-g" href="/signin/google?to=${esc(encodeURIComponent(to))}">${G_MARK}Continue with Google</a>
<p class="or">or with your email</p>` : ""}
${emailForm(to, email)}
</div>
<p class="next">We email you a link and a code. There is no account to make and nothing to remember: the sign-in is kept on this device for ${SESSION_DAYS} days.</p>
<a class="about" href="/">What is ${esc(NAME)}?</a>`;
  return respond(status, doc("Sign in", inner, google, `/signin?to=${encodeURIComponent(to)}`), headers);
}

/* After the email has gone: where it went, and the code box. */
export function checkPage(email, to, { message = "", status = 200, headers = [] } = {}) {
  const inner = `<h1>Check your email</h1>
<p>We sent a sign-in link to <span class="sent">${esc(email)}</span>. It works for ${LINK_MINUTES} minutes.</p>
${note(message)}
<div class="acts">
<form method="post" action="/signin/code">
<label for="cd">Opened the email in another app or on another device? Type its code here.</label>
<input id="cd" class="code" type="text" name="code" required autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" maxlength="16" placeholder="XXXXX-XXXXX">
<button class="btn" type="submit">Sign in</button>
</form>
</div>
<p class="next">Nothing there after a minute? Look in spam, or <a href="${esc(again(to))}&amp;again=1">start again with another address</a>.</p>`;
  return respond(status, doc("Check your email", inner, false, "/signin/check"), headers);
}

/* Anything that went wrong, with the one way forward. */
export function messagePage(status, heading, line, to = "/", headers = []) {
  const inner = `<h1>${esc(heading)}</h1>
<p>${esc(line)}</p>
<div class="acts"><a class="btn" href="${esc(again(to))}">Sign in again</a></div>`;
  return respond(status, doc(heading, inner, false, again(to)), headers);
}

/* What an emailed link opens: whose sign-in it is, and one button that
 * posts the token back (functions/signin/link.js says why it is a button). */
export function confirmPage(email, token, replacing = false) {
  const inner = `<h1>Almost there</h1>
<p>Press Sign in to finish signing in as <span class="sent">${esc(email)}</span>.</p>
${replacing ? note("This device is signed in as somebody else now. Continuing signs it in as the address above instead.") : ""}
<div class="acts"><form method="post" action="/signin/link">
<input type="hidden" name="t" value="${esc(token)}">
<button class="btn" type="submit">Sign in</button>
</form></div>
<p class="next">This one extra press keeps company email scanners, which open every link, from signing in for you. Not you, or did not ask? Close this page: nothing happens unless you press Sign in.</p>`;
  return respond(200, doc("Sign in", inner, false));
}

/* After a sign-out by somebody who is also signed in through Access. A
 * plain link, not a redirect: see functions/signin/out.js. */
export function signedOutPage(headers = []) {
  const inner = `<h1>Signed out of the boards</h1>
<p>You are still signed in to the admin through Cloudflare, and that sign-in opens the boards too.</p>
<div class="acts"><a class="btn" href="/cdn-cgi/access/logout">Sign out of the admin too</a>
<a class="about" href="/">Back to the front page</a></div>`;
  return respond(200, doc("Signed out", inner, false), headers);
}

export function signoutPage() {
  const inner = `<h1>Sign out?</h1>
<p>This device will need a new sign-in to see the boards.</p>
<div class="acts"><form method="post" action="/signin/out"><button class="btn" type="submit">Sign out</button></form>
<a class="about" href="/">Stay signed in</a></div>`;
  return respond(200, doc("Sign out", inner, false));
}
