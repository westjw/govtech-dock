import { verify, tokenOf } from "../../_access.js";

/* WHO IS SIGNED IN, and what may they reach.
 *
 * Cloudflare Access signs people in for /admin, but it does not cover the
 * pages.dev alias, and this endpoint is open, so no door runs in front of it.
 * It never checks a password, holds no secret and trusts no header: it
 * verifies the Access token the browser carries (verifiedEmail, below) and
 * reads the address from it. What
 * it adds is the owner's ruling from the Users board - a hash of the
 * address looked up in users.json, which carries hashes and handles and
 * never an address - so the site can show "signed in as jane" and open the
 * doors her roles allow: "admin" for the web admin, "hunter" for the closed
 * Job Hunter beta. The address itself is not returned: the page needs a
 * handle, not a person.
 *
 * Fails closed three ways: no verified token is signed out; an address with no
 * matching hash is signed in with no roles; a users.json that cannot be
 * read is the same as an empty one. */
export async function onRequestGet({ request, env }) {
  const email = await verifiedEmail(request, env);
  if (!email) return json({ signed_in: false });
  const key = await sha256(email.trim().toLowerCase());
  let users = {};
  try {
    const res = await env.ASSETS.fetch(new URL("/admin/users.json", request.url));
    if (res.ok) users = await res.json();
  } catch (e) { users = {}; }
  for (const [handle, u] of Object.entries(users || {})) {
    if (u && u.email_sha256 === key && !u.revoked_on) {
      return json({ signed_in: true, handle, roles: Array.isArray(u.roles) ? u.roles : [] });
    }
  }
  return json({ signed_in: true, handle: null, roles: [] });
}

/* THE ADDRESS IS READ FROM A VERIFIED TOKEN, NOT A HEADER. This endpoint is
 * open (the public account menu asks it), so the /admin door does not run in
 * front of it, and it used to take Cf-Access-Authenticated-User-Email on
 * trust - a header a client can send on the pages.dev alias, where no Access
 * application stands. It verifies the same Access token the door does
 * (functions/_access.js): no token or a bad one is signed out, and keys that
 * will not answer are signed out too, because this only ever opens doors. */
async function verifiedEmail(request, env) {
  const token = tokenOf(request);
  if (!token) return null;
  try {
    const claims = await verify(token, env);
    return claims && typeof claims.email === "string" ? claims.email : null;
  } catch (e) {
    return null;
  }
}

async function sha256(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

function json(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8",
               "cache-control": "no-store" },
  });
}
