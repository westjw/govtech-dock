/* WHO IS SIGNED IN, and what may they reach.
 *
 * Cloudflare Access sits on /admin for every hostname and puts the verified
 * address of a signed-in person on the request. This function never checks
 * a password, never validates a token and holds no secret: if the header is
 * there, Access put it there, and if it is not, nobody is signed in. What
 * it adds is the owner's ruling from the Users board - a hash of the
 * address looked up in users.json, which carries hashes and handles and
 * never an address - so the site can show "signed in as jane" and open the
 * doors her roles allow: "admin" for the web admin, "hunter" for the closed
 * Job Hunter beta. The address itself is not returned: the page needs a
 * handle, not a person.
 *
 * Fails closed three ways: no header is signed out; a header with no
 * matching hash is signed in with no roles; a users.json that cannot be
 * read is the same as an empty one. */
export async function onRequestGet({ request, env }) {
  const email = request.headers.get("Cf-Access-Authenticated-User-Email");
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
