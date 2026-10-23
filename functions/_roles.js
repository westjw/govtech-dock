/* WHO A VERIFIED ADDRESS IS ON THE USERS BOARD, and what it may reach.
 *
 * Access proves somebody signed in; it says nothing about what they may see.
 * Until 2026-10-22 that was the same thing, because the only people Access
 * let sign in were the owner and the people he listed. The owner then opened
 * the boards to anyone ("Anyone, free": a sign-in by email and a one-time
 * code), and from that day a verified token means a stranger as often as
 * the owner. The admin door asks this file instead of trusting the token.
 *
 * users.json holds no address, only email_sha256 (build_site.py refuses a
 * row with an "@"), so the address is hashed the way the Users board hashed
 * it. It is read through env.ASSETS, which does not pass back through the
 * middleware, so the door can read the file it guards. Fails closed: a
 * users.json that cannot be read, or a revoked row, grants nothing.
 */
export async function sha256(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/* {handle, roles} for this address, or null when the Users board does not
 * know it (or cannot be read). */
export async function whoIs(request, env, email) {
  if (typeof email !== "string" || !email.trim()) return null;
  const key = await sha256(email.trim().toLowerCase());
  let users = {};
  try {
    const res = await env.ASSETS.fetch(new URL("/admin/users.json", request.url));
    if (res.ok) users = await res.json();
  } catch (e) { users = {}; }
  for (const [handle, u] of Object.entries(users || {})) {
    if (u && u.email_sha256 === key && !u.revoked_on) {
      return { handle, roles: Array.isArray(u.roles) ? u.roles : [] };
    }
  }
  return null;
}

/* The roles that open the admin: "owner" covers everything, "admin" is the
 * phone admin the Users board grants. "hunter" is Job Hunter's door and
 * opens nothing here. */
export const ADMIN_ROLES = ["owner", "admin"];
