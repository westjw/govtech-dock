import { verify, tokenOf } from "../_access.js";
import { sessionOf } from "../_session.js";
import { DOMAIN } from "../_brand.js";
import { sha256 } from "../_roles.js";

/* WHO IS SIGNED IN, and what may they reach: GET /api/whoami.
 *
 * Two sign-ins since 2026-11-13: the site's own (functions/_session.js,
 * email or Google) and Cloudflare Access (the owner and the admins). It was
 * /admin/api/whoami alone, and that path sits under the Access application,
 * so a member signed in our way would have met Access there instead of an
 * answer. This is outside /admin; /admin/api/whoami answers the same.
 *
 * It never checks a password, holds no secret and trusts no header: our
 * cookie is checked by its HMAC, an Access token by its signature (the
 * header Access adds is never read). What it adds is the owner's ruling
 * from the Users board - a hash of the address looked up in users.json,
 * which carries hashes and handles and never an address - so the site can
 * show "signed in as jane" and open the doors her roles allow: "admin" for
 * the web admin, "hunter" for the closed Job Hunter beta. The address itself is
 * never returned, masked or not: the page needs a handle and how the person
 * signed in (`via`), not a person.
 *
 * Fails closed three ways: no verified sign-in is signed out; an address
 * with no matching hash is signed in with no roles; a users.json that
 * cannot be read is the same as an empty one. Keys that will not answer are
 * signed out, because this only ever opens doors. */
export async function whoami(request, env) {
  const me = await signedIn(request, env);
  if (!me) return json({ signed_in: false });
  const key = await sha256(me.email.trim().toLowerCase());
  let users = {};
  try {
    const res = await env.ASSETS.fetch(new URL("/admin/users.json", request.url));
    if (res.ok) users = await res.json();
  } catch (e) { users = {}; }
  for (const [handle, u] of Object.entries(users || {})) {
    if (u && u.email_sha256 === key && !u.revoked_on) {
      const roles = Array.isArray(u.roles) ? u.roles : [];
      const out = { signed_in: true, via: me.via, handle, roles };
      /* JOB HUNTER'S ADDRESS COMES FROM THE BRAND, NEVER THE ADDRESS BAR. On
       * the legacy solesourcejobs.com host a page building it from its own
       * hostname sent every Flag to hunter.solesourcejobs.com, which Job
       * Hunter does not answer (review, 2026-10-20). Only for the people the
       * Flag and the Job Hunter door are drawn for. */
      if (roles.includes("hunter") || roles.includes("owner")) out.hunter_app = `https://hunter.${DOMAIN}/app`;
      return json(out);
    }
  }
  return json({ signed_in: true, via: me.via, handle: null, roles: [] });
}

export const onRequestGet = ({ request, env }) => whoami(request, env);

/* {email, via} from a VERIFIED sign-in, or null. ACCESS FIRST when there is
 * one: it is the owner's and the admins', and a link somebody else sent the
 * owner must not make the account menu answer as that somebody (security
 * review, 2026-11-13). A member carries no Access token, so for them this
 * costs nothing. */
async function signedIn(request, env) {
  const token = tokenOf(request);
  if (token) {
    try {
      const claims = await verify(token, env);
      if (claims && typeof claims.email === "string") return { email: claims.email, via: "access" };
    } catch (e) { /* keys did not answer: fall back to ours */ }
  }
  const ours = await sessionOf(request, env);
  return ours ? { email: ours.email, via: ours.method } : null;
}

function json(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8",
               "cache-control": "no-store" },
  });
}
