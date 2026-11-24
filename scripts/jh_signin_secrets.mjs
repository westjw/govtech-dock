#!/usr/bin/env node
/* THE SECRETS JOB HUNTER'S SIGN-IN NEEDS, made on the owner's own machine
 * and shown ONCE (owner, 2026-11-23, design C; DEPLOY.md §3f step 1).
 *
 *   node scripts/jh_signin_secrets.mjs         the signing key, the client
 *                                              id and the client secret
 *   node scripts/jh_signin_secrets.mjs --key   a new signing key only, for a
 *                                              rotation (DEPLOY.md §3f says
 *                                              how)
 *
 * IT WRITES NOTHING. No file, no log, nothing in this public repository: the
 * values exist on the screen until they are pasted into Cloudflare, and then
 * only there. It refuses when its output is not a terminal, so a stray
 * `> file` cannot put a private key on disk; --to-pipe says that is meant
 * (`| pbcopy`, or the selftest reading it).
 *
 * The key is P-256 for ES256 (functions/_oidc.js signs with it, Access and
 * Job Hunter verify with its public half at /oidc/jwks). Its kid is its own
 * RFC 7638 thumbprint, so a kid can never name two keys. Made by Node's own
 * crypto (OpenSSL), nothing installed.
 */
import { generateKeyPairSync, randomBytes, createHash } from "node:crypto";

const args = process.argv.slice(2);
const KNOWN = new Set(["--key", "--to-pipe"]);
const unknown = args.filter((a) => !KNOWN.has(a));
if (unknown.length) {
  process.stderr.write(`Unknown option ${unknown[0]}. Use --key for a new signing key only.\n`);
  process.exit(2);
}
if (!process.stdout.isTTY && !args.includes("--to-pipe")) {
  process.stderr.write("This prints secrets, so it prints them only to a terminal. Run it in Terminal as it is,\n"
    + "or add --to-pipe if you mean to send them elsewhere (for example | pbcopy).\n");
  process.exit(2);
}

function signingKey() {
  const { privateKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
  const j = privateKey.export({ format: "jwk" });
  // RFC 7638: the required members in lexicographic order, no whitespace
  const thumb = createHash("sha256").update(JSON.stringify({ crv: j.crv, kty: j.kty, x: j.x, y: j.y })).digest("base64url");
  return JSON.stringify({ kty: "EC", crv: "P-256", x: j.x, y: j.y, d: j.d, kid: thumb, alg: "ES256", use: "sig" });
}

const out = [];
const say = (s = "") => out.push(s);
const value = (name, v, where) => { say(name); say(v); say(`  (${where})`); say(); };

if (args.includes("--key")) {
  say("A NEW SIGNING KEY for SLED JOBS' Job Hunter sign-in. Shown once; nothing is saved.");
  say();
  say("First copy the CURRENT OIDC_SIGNING_JWK into a new secret OIDC_SIGNING_JWK_OLD (so tokens");
  say("signed a moment ago still verify), then replace OIDC_SIGNING_JWK with this, then redeploy.");
  say();
  value("OIDC_SIGNING_JWK", signingKey(), "a Pages secret on the board's project, replacing the current one");
} else {
  say("THE THREE SECRETS for SLED JOBS' Job Hunter sign-in. Shown once; nothing is saved.");
  say("Paste each into Workers & Pages > the board's project > Settings > Variables and secrets,");
  say("Type Secret, Production. Never paste them anywhere else, and never into a file in the repo.");
  say();
  value("OIDC_SIGNING_JWK", signingKey(), "a Pages secret only: it never goes to Zero Trust");
  value("OIDC_CLIENT_ID", `job-hunter-${randomBytes(8).toString("hex")}`,
    "a Pages secret, and the App ID / Client ID of the SLED JOBS identity provider in Zero Trust");
  value("OIDC_CLIENT_SECRET", randomBytes(32).toString("base64url"),
    "a Pages secret, and the Client secret of the SLED JOBS identity provider in Zero Trust");
  say("Then a new deployment, so the Functions see them (DEPLOY.md 3f, step 1).");
}
process.stdout.write(out.join("\n") + "\n");
