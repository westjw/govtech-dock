/* THE DOOR ON /admin, VERIFIED, ON EVERY HOSTNAME.
 *
 * Cloudflare Access covers /admin on sledjobs.com. It did not cover the
 * project's *.pages.dev alias, and on 2026-09-25 that alias answered 200 to
 * anyone for /admin/, /admin/data.json (every queue), /admin/rulings.json
 * (every recorded ruling with the owner's reasons) and /admin/users.json
 * (the owner's email hash). DEPLOY.md said a custom domain "inherits the
 * project's Access policy"; the alias evidently does not. Everything behind
 * /admin was one hostname away from public, and nothing in the code could
 * tell, because the code trusted that a request reaching it had passed a
 * door it could not see.
 *
 * So the door is here now, in front of every file and function under
 * /admin, and it does not trust presence: whoami.js and rule.js read the
 * Cf-Access-* headers and stopped there. This VERIFIES the Access JWT
 * (Cf-Access-Jwt-Assertion header, or the CF_Authorization cookie the
 * browser sends on static requests) against the team's public keys, checks
 * the audience is THIS application and the token is in date, and refuses
 * everything else. Fails closed three ways: no token is 403, an unverifiable
 * token is 403, and keys that cannot be fetched are 503 - never 200.
 *
 * Two paths stay open on purpose. /admin/api/whoami answers {signed_in:
 * false} to the public site's account menu and holds nothing; /admin/api/
 * login is how a person reaches Access in the first place. Both are
 * harmless without a token and both are needed without one.
 *
 * The signature check, the team domain and the application audience live in
 * functions/_access.js, shared with the site-wide gate in functions/_gate.js
 * (ACCESS_TEAM_DOMAIN and ACCESS_AUD still override them). A wrong value
 * means the owner sees a 403 naming the variable, which is the failure mode
 * this file exists to have - "nothing works", never "everyone can read".
 */
import { verify, tokenOf } from "../_access.js";

export { verify };

const OPEN = new Set(["/admin/api/whoami", "/admin/api/login"]);

function refuse(url, status, why) {
  const headers = { "cache-control": "no-store", "x-robots-tag": "noindex" };
  if (url.pathname.startsWith("/admin/api/")) {
    return new Response(JSON.stringify({ error: why }),
      { status, headers: { ...headers, "content-type": "application/json" } });
  }
  return new Response(`${why}\n\nSign in at https://sledjobs.com/admin/\n`,
    { status, headers: { ...headers, "content-type": "text/plain; charset=utf-8" } });
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  if (OPEN.has(url.pathname)) return next();
  const token = tokenOf(request);
  if (!token) return refuse(url, 403, "not signed in through Cloudflare Access");
  let claims = null;
  try { claims = await verify(token, env); }
  catch { return refuse(url, 503, "could not verify the sign-in (the Access keys did not answer)"); }
  if (!claims) {
    return refuse(url, 403, "the sign-in could not be verified for this application "
      + "(ACCESS_AUD / ACCESS_TEAM_DOMAIN name which one)");
  }
  // THE VERIFIED IDENTITY TRAVELS WITH THE REQUEST. The handlers behind this
  // door read the person from here, never from Cf-Access-Authenticated-User-
  // Email: that header is put on by Access where Access runs, and on the
  // pages.dev alias, which no Access application covers, a client can send
  // it itself (launch audit, 2026-10-06). context.data is the Pages way to
  // hand a value from middleware to the function it runs.
  if (context.data && typeof context.data === "object") {
    context.data.access = { email: typeof claims.email === "string" ? claims.email : null };
  }
  return next();
}
