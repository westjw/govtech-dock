/* WHO IS SIGNED IN, verified, in one place (2026-11-14).
 *
 * Three readers need the same answer: /api/whoami (the account menu), /api/me
 * (the alerts page filling in the address) and the alerts endpoint (a person
 * subscribing the address they signed in with needs no confirmation email).
 * A second copy of "which sign-in counts" would be a second place to be wrong.
 *
 * ACCESS FIRST when there is one: it is the owner's and the admins', and a
 * link somebody else sent the owner must not make the site answer as that
 * somebody (security review, 2026-11-13). Then the site's own sign-in
 * (functions/_session.js). Never a header: an Access token is checked by its
 * signature, ours by its HMAC. Keys that will not answer fall back to ours,
 * because this only ever recognises a person, it never refuses one.
 */
import { verify, tokenOf } from "./_access.js";
import { sessionOf } from "./_session.js";

/* {email, via: "access" | "email" | "google", iat} or null. `iat` is when
 * the sign-in was made, or null: Job Hunter's sign-in reports it as
 * auth_time (functions/oidc/authorize.js). */
export async function signedIn(request, env) {
  const token = tokenOf(request);
  if (token) {
    try {
      const claims = await verify(token, env);
      // Access's own address, verified by its signature: trimmed and
      // lower-cased, as the Users board hashes it
      const email = claims && typeof claims.email === "string" ? claims.email.trim().toLowerCase() : "";
      if (email) return { email, via: "access", iat: Number.isInteger(claims.iat) ? claims.iat : null };
    } catch (e) { /* the keys did not answer: fall back to ours */ }
  }
  const ours = await sessionOf(request, env);
  return ours ? { email: ours.email, via: ours.method, iat: ours.iat } : null;
}
