/* "SIGN IN WITH SLED JOBS", the board's half: GET /oidc/authorize
 * (owner, 2026-11-23, design C; functions/_oidc.js has the whole of it).
 *
 * Access sends a person here from Job Hunter's door. In order:
 *   1. The request is held to the ONE client: its client_id, Access's
 *      callback exactly (_oidc.redirectUri), response_type=code, openid in
 *      the scope, PKCE S256 with a challenge. Anything else is a page on THIS
 *      site saying the sign-in cannot be finished, and NO redirect: an
 *      address this has not checked is never sent anywhere.
 *   2. Not signed in (functions/_identity.js signedIn, which also sees the
 *      owner's Access sign-in): the request is sealed under SESSION_SECRET
 *      (purpose "oidc-request", ten minutes) into a __Host- cookie, and the
 *      person goes to /signin?to=/oidc/authorize?r=<ref>, where <ref> is 16
 *      random bytes the sealed request carries too. A seal does not shorten
 *      anything - it holds every byte of Access's state plus a MAC - and a
 *      `to` past 1,024 characters is "/" (functions/_to.js), so the request
 *      itself never rides in `to`; the reference does. Back here signed in,
 *      the reference must match the cookie this browser holds.
 *   3. Signed in, but neither `hunter` nor `owner` on the Users board
 *      (functions/_roles.js): a page saying Job Hunter is a closed beta and
 *      how to ask for a place. No code.
 *   4. Allowed: a one-time code (_oidc.mintCode: 32 random bytes, kept only
 *      as a hash, 120 seconds, spent once) and a 200 page that goes on to
 *      Access's callback with the code and the state by <meta refresh> and a
 *      link - NEVER a redirect. A person who just typed the emailed code
 *      arrives here through a form's redirect chain, and the sign-in pages'
 *      form-action 'self' makes Chrome refuse that chain the moment it leaves
 *      this site; a new navigation from a page is no form's. The page runs
 *      no script, is no-store, and refuses to be framed.
 * Served on the site's own address only: the sign-in cookie is host-only.
 */
import { NAME } from "../_brand.js";
import { signedIn } from "../_identity.js";
import { whoIs } from "../_roles.js";
import { configured, onSite, seal, unseal, ours, random, now, setCookie, dropCookie, sameCode } from "../_session.js";
import { redirect } from "../_signin_page.js";
import { CARD_CSS, FORM_CSS, esc, themeOf } from "../_card.js";
import { setUp, readAuthorize, checkAuthorize, mintCode, JH_ORIGIN, JH_ROLES, AUTHORIZE,
         REQUEST_MINUTES } from "../_oidc.js";

/* The sign-in on its way, this browser's own: sealed, HttpOnly, ten minutes. */
export const REQUEST = "__Host-sj_oidc";
export const PURPOSE = "oidc-request";
const REF = /^[A-Za-z0-9_-]{22}$/;
/* Past this a browser may drop the cookie (4 KB, name and attributes in). */
const HELD_MAX = 3800;
const QUERY_MAX = 8192;

function page(request, status, { title, heading, lines, acts, refresh = null, headers = [] }) {
  const theme = themeOf(request) === "light" ? "light" : "dark";
  const html = `<!doctype html>
<html lang="en" data-theme="${theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<meta name="referrer" content="no-referrer">
${refresh ? `<meta http-equiv="refresh" content="0;url=${esc(refresh)}">\n` : ""}<title>${esc(title)} &middot; ${esc(NAME)}</title>
<link rel="icon" href="/assets/mascot/svg/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&amp;display=swap">
<style>
${CARD_CSS}${FORM_CSS}
</style></head>
<body><main><div class="card">
<img src="/assets/mascot/svg/head-offer-in.svg" width="96" height="96" alt="">
<p class="kick">${esc(NAME)}</p>
<h1>${esc(heading)}</h1>
${lines.map((l) => `<p>${l}</p>`).join("\n")}
<div class="acts">${acts}</div>
</div></main></body></html>
`;
  const h = new Headers({ "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
    "x-robots-tag": "noindex", "referrer-policy": "no-referrer", "x-frame-options": "DENY" });
  for (const [k, v] of headers) h.append(k, v);
  return new Response(request.method === "HEAD" ? null : html, { status, headers: h });
}

const back = `<a class="btn" href="${esc(JH_ORIGIN)}/app">Back to Job Hunter</a>`;
const forget = (held) => (held ? [["Set-Cookie", dropCookie(REQUEST)]] : []);

/* Every refusal of the request itself reads the same, so it tells a prober
 * nothing about which part was wrong. */
const refused = (request, held = false) => page(request, 400, {
  title: "Sign-in not finished", heading: "This sign-in cannot be finished",
  lines: [`The request ${esc(NAME)} was sent does not match what it expects, so nobody was signed in.`,
          `Go back to Job Hunter and press Sign in with ${esc(NAME)} again.`],
  acts: back, headers: forget(held) });

const ranOut = (request) => page(request, 400, {
  title: "Sign-in ran out", heading: "That sign-in ran out",
  lines: [`Signing in took longer than ${REQUEST_MINUTES} minutes, or it finished in another browser.`,
          `Go back to Job Hunter and press Sign in with ${esc(NAME)} again.`],
  acts: back, headers: [["Set-Cookie", dropCookie(REQUEST)]] });

const notSetUp = (request) => page(request, 503, {
  title: "Being set up", heading: "Job Hunter sign-in is being set up",
  lines: [`Signing in to Job Hunter with ${esc(NAME)} is not switched on yet. Try again in a little while.`],
  acts: `<a class="btn" href="/">Back to ${esc(NAME)}</a>` });

const busy = (request, held) => page(request, 503, {
  title: "Try again", heading: "We could not finish signing you in",
  lines: ["Something went wrong on our side. Try again in a minute."],
  acts: back, headers: forget(held) });

/* SIGNED IN, NO PLACE IN THE BETA: whose sign-in it is (the person's own
 * address, to them: a wrong one is the likeliest reason) and how to ask. */
const closedBeta = (request, email, held) => page(request, 403, {
  title: "Closed beta", heading: "Job Hunter is in a closed beta",
  lines: [`You are signed in to ${esc(NAME)} as <span class="sent">${esc(email)}</span>, and that address has no place in Job Hunter's beta yet.`,
          "Job Hunter is open to the people its owner has invited. If he invited you, ask him to add the address above; if he gave you a beta code, the beta page takes it."],
  acts: `<a class="btn" href="/beta/">Open the beta page</a>
<a class="about" href="/signin/out">Signed in with another address? Sign out</a>
<a class="about" href="/">Back to ${esc(NAME)}</a>`,
  headers: forget(held) });

/* ON TO ACCESS: the code and the state, by a navigation that is not a form's. */
const onward = (request, dest, held) => page(request, 200, {
  title: "Signing you in", heading: "On to Job Hunter",
  lines: [`Signed in with ${esc(NAME)}. Taking you to Job Hunter now.`],
  acts: `<a class="btn" href="${esc(dest)}">Continue to Job Hunter</a>`,
  refresh: dest, headers: forget(held) });

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const away = onSite(url);
  if (away) return redirect(away, [], 302);
  // the client, the one address, a key that signs, D1, and the sign-in's own
  // secret (the request on its way is sealed with it)
  const setup = await setUp(env);
  if (!setup || !configured(env)) return notSetUp(request);
  if (url.search.length > QUERY_MAX) return refused(request);

  // BACK FROM THE SIGN-IN: the request is the one this browser holds, and
  // only that one (nothing else in the address is read)
  let q = null, held = false;
  if (url.searchParams.has("r")) {
    const r = url.searchParams.get("r");
    if ([...url.searchParams.keys()].length !== 1 || !REF.test(r)) return refused(request);
    const box = await unseal(env, PURPOSE, ours(request, REQUEST));
    if (!box || typeof box.r !== "string" || !sameCode(box.r, r) || !box.q || typeof box.q !== "object") {
      return ranOut(request);
    }
    q = box.q;
    held = true;
  } else {
    q = readAuthorize(url.searchParams);
  }
  const ask = checkAuthorize(q, setup);
  if (!ask) return refused(request, held);

  const me = await signedIn(request, env);
  if (!me) {
    const ref = random(16);
    let box = "";
    try { box = await seal(env, PURPOSE, { r: ref, q: ask, exp: now() + REQUEST_MINUTES * 60 }); }
    catch { return notSetUp(request); }
    if (box.length > HELD_MAX) return refused(request);
    return redirect(`/signin?to=${encodeURIComponent(`${AUTHORIZE}?r=${ref}`)}`,
      [["Set-Cookie", setCookie(REQUEST, box, REQUEST_MINUTES * 60)]], 302);
  }

  const who = await whoIs(request, env, me.email);
  if (!who || !who.roles.some((x) => JH_ROLES.includes(x))) return closedBeta(request, me.email, held);

  let code = "";
  try {
    code = await mintCode(env, { email: me.email, nonce: ask.nonce, code_challenge: ask.code_challenge,
      client_id: ask.client_id, redirect_uri: ask.redirect_uri, auth_time: me.iat });
  } catch { return busy(request, held); }
  const dest = new URL(ask.redirect_uri);
  dest.searchParams.set("code", code);
  if (ask.state !== null) dest.searchParams.set("state", ask.state);
  return onward(request, dest.href, held);
}
