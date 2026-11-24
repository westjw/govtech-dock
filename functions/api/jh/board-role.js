/* THE JOB HUNTER PANEL, ASKED BY THE BOARD'S OWN SERVER: GET
 * /api/jh/board-role?id=<posting id> (owner, 2026-11-23, design C: "the
 * board's server fetches the scoring popout for you with a Cloudflare
 * service token, so the popout never asks you to sign in").
 *
 * The role page used to fetch Job Hunter itself, across sites, with the
 * person's own Access sign-in at Job Hunter's door; without a live one Access
 * answered with no CORS header and the panel could only say "did not
 * answer". Now the page asks this site (same origin) and this asks Job
 * Hunter, with two proofs Job Hunter checks together for this ONE route:
 *   - the service token (JH_SERVICE_TOKEN_ID / JH_SERVICE_TOKEN_SECRET, as
 *     CF-Access-Client-Id / -Secret), which gets the request past Access;
 *   - X-SJ-Assertion, a 60-second ES256 JWT of who is asking and about which
 *     opening, signed with the board's sign-in key (functions/_oidc.js) and
 *     verified by Job Hunter against /oidc/jwks: A HEADER IS A CLAIM, A
 *     SIGNATURE IS EVIDENCE.
 * The service token passes Access on EVERY path of hunter.<domain>, so
 * everything about the call is fixed here: one address, GET, no cookies, no
 * header of the caller's, no redirect followed, eight seconds (Job Hunter
 * runs on a Mac that sleeps and answers one request at a time). The caller
 * chooses only the id, and only after this has checked, itself, on every
 * hostname and after launch too, that:
 *   1. the request is this site's own page asking (Sec-Fetch-Site), on the
 *      site's own address (the sign-in is a host-only cookie);
 *   2. somebody is signed in (signedIn: ours, or the owner's Access);
 *   3. the Users board gives them `hunter` or `owner`;
 *   4. the id has the shape Job Hunter's board_role takes;
 *   5. they have asked fewer than ASKS_PER_MINUTE times this minute
 *      (takeAsk, D1): Job Hunter keeps every assertion's jti for ten minutes
 *      in one book that refuses everybody when full, so one beta user's
 *      script must not be able to fill it (review, 2026-11-23). Over it:
 *      429 {"error": "slow_down"}, nothing signed, nothing sent.
 * What comes back: Job Hunter's JSON when it answers 200; its 401 and 404
 * reasons (the word alone: no address); and {"failed": "unreachable"} for a
 * timeout, a network error, Access's own login page or anything that is not
 * JSON, and for a count D1 could not take. Nothing is logged: not the
 * assertion, not the secret.
 */
import { onSite, random, now } from "../../_session.js";
import { signedIn } from "../../_identity.js";
import { whoIs } from "../../_roles.js";
import { json } from "../../_mail.js";
import { signJwt, subOf, takeAsk, ISSUER, JH_ROUTE, JH_ROLES, ASSERTION_SECONDS } from "../../_oidc.js";

export const UPSTREAM_MS = 8000;
export const ID_MAX = 600;
const MAX_BYTES = 65536;
const WORD = /^[a-z][a-z_]{0,39}$/;

/* The shape Job Hunter's board_role accepts (scripts/beta_api.py: a string
 * of 1 to 600 characters with "::" in it), and no control character. */
export function cleanId(sp) {
  const all = sp.getAll("id");
  if (all.length !== 1) return null;
  const id = all[0];
  if (!id || id.length > ID_MAX || !id.includes("::") || /[\x00-\x1f\x7f]/.test(id)) return null;
  return id;
}

function serviceToken(env) {
  const id = env && env.JH_SERVICE_TOKEN_ID, secret = env && env.JH_SERVICE_TOKEN_SECRET;
  if (typeof id !== "string" || !/^[A-Za-z0-9._-]{8,128}$/.test(id)) return null;
  if (typeof secret !== "string" || !/^[A-Za-z0-9._-]{16,256}$/.test(secret)) return null;
  return { id, secret };
}

const isObject = (x) => !!x && typeof x === "object" && !Array.isArray(x);

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  if (onSite(url)) return json({ error: "not_here" }, 404);
  // another site's page, or a sibling host's, never sets this off
  const site = request.headers.get("Sec-Fetch-Site");
  if (site !== null && site !== "same-origin" && site !== "none") return json({ error: "not_from_this_site" }, 403);
  const me = await signedIn(request, env);
  if (!me) return json({ error: "not_signed_in" }, 401);
  const who = await whoIs(request, env, me.email);
  if (!who || !who.roles.some((r) => JH_ROLES.includes(r))) return json({ error: "not_allowed" }, 403);
  const id = cleanId(url.searchParams);
  if (!id) return json({ error: "bad_id" }, 400);
  const token = serviceToken(env);
  if (!token || !env.DB) return json({ failed: "not_set_up" }, 503);
  // ONE PERSON'S PACE, counted before anything is signed or sent
  let go = false;
  try { go = await takeAsk(env, me.email); } catch { return json({ failed: "unreachable" }, 502); }
  if (!go) {
    const slow = json({ error: "slow_down" }, 429);
    slow.headers.set("retry-after", "60");
    return slow;
  }
  const t = now();
  const assertion = await signJwt(env, { iss: ISSUER, aud: JH_ROUTE, sub: await subOf(me.email),
    email: me.email, id, iat: t, exp: t + ASSERTION_SECONDS, jti: random(16) });
  if (!assertion) return json({ failed: "not_set_up" }, 503);

  // THE ONE CALL: nothing of the caller's but the id, encoded
  const unreachable = () => json({ failed: "unreachable" }, 502);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), UPSTREAM_MS);
  let res = null, text = "";
  try {
    res = await fetch(`${JH_ROUTE}?id=${encodeURIComponent(id)}`, {
      method: "GET", redirect: "manual", signal: ctl.signal,
      headers: { "CF-Access-Client-Id": token.id, "CF-Access-Client-Secret": token.secret,
                 "X-SJ-Assertion": assertion } });
    text = await res.text();
  } catch { return unreachable(); }
  finally { clearTimeout(timer); }
  if (text.length > MAX_BYTES || !(res.headers.get("content-type") || "").toLowerCase().includes("application/json")) {
    return unreachable();
  }
  let body = null;
  try { body = JSON.parse(text); } catch { return unreachable(); }
  if (!isObject(body)) return unreachable();
  if (res.status === 200) return json(body, 200);
  if (res.status === 401 || res.status === 404) {
    const word = typeof body.error === "string" && WORD.test(body.error) ? body.error
      : res.status === 401 ? "refused" : "not_found";
    return json({ error: word }, res.status);
  }
  if (res.status === 400) return json({ error: "bad_id" }, 400);
  return unreachable();
}
