#!/usr/bin/env node
/* THE SECRETS JOB HUNTER'S SIGN-IN NEEDS, made on the owner's own machine
 * and shown ONCE (owner, 2026-11-23, design C; DEPLOY.md §3f step 1).
 *
 *   node scripts/jh_signin_secrets.mjs           the signing key, the client
 *                                                id and the client secret:
 *                                                the first setup
 *   node scripts/jh_signin_secrets.mjs --key     a new signing key only, for
 *                                                a rotation or a key that
 *                                                may have leaked (DEPLOY.md
 *                                                §3f: the two are different)
 *   node scripts/jh_signin_secrets.mjs --secret  a new client secret only
 *
 * A plain run after the first setup would make a new key and a new client
 * id too, so changing only the secret is --secret (review, 2026-11-23: the
 * steps once said a plain run, under a screen saying "paste each").
 *
 * THE OWNER RUNS IT, HIMSELF, IN THE macOS TERMINAL APP. Never Claude, in
 * any way (its own shell, a pseudo-terminal, the Claude app's terminal
 * panel): whatever an agent's command prints lands in its transcript, and
 * the private key would leave this machine with it. CLAUDE.md says so too.
 *
 * IT WRITES NOTHING. No file, no log, nothing in this public repository: the
 * values exist on the screen until they are pasted into Cloudflare, and then
 * only there. It prints only to a terminal, so a `> file`, a pipe or an
 * agent's shell gets nothing, and NO SWITCH SAYS OTHERWISE: there was one
 * (--to-pipe), and the refusal named it, which handed an agent the way past
 * (review, 2026-11-23).
 *
 * The key is P-256 for ES256 (functions/_oidc.js signs with it, Access and
 * Job Hunter verify with its public half at /oidc/jwks). Its kid is its own
 * RFC 7638 thumbprint, so a kid can never name two keys. Made by Node's own
 * crypto (OpenSSL), nothing installed.
 */
import { generateKeyPairSync, randomBytes, createHash } from "node:crypto";
import { SITE } from "../functions/_brand.js";

const args = process.argv.slice(2);
const KNOWN = new Set(["--key", "--secret"]);
const unknown = args.filter((a) => !KNOWN.has(a));
if (unknown.length || (args.includes("--key") && args.includes("--secret"))) {
  process.stderr.write(`${unknown.length ? `Unknown option ${unknown[0]}` : "One at a time"}. Use --key for a new signing key `
    + "only, or --secret for a new client secret only.\n");
  process.exit(2);
}
// the refusal names no way past it: an agent reading it must stop here
if (!process.stdout.isTTY) {
  process.stderr.write("Nothing was printed. This makes secrets and shows them only on a terminal, and this is\n"
    + "not one. The site's owner runs it himself, in the macOS Terminal app (DEPLOY.md 3f).\n");
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
  say(`A ROUTINE ROTATION (once a year): first run  curl -s ${SITE}/oidc/jwks  and paste what it`);
  say("prints, the key in use now (its public half only), as a new Pages secret OIDC_SIGNING_JWK_OLD,");
  say("so tokens signed a moment ago still verify. Then replace OIDC_SIGNING_JWK with the key below and");
  say("redeploy. Delete OIDC_SIGNING_JWK_OLD a day later, and redeploy again.");
  say();
  say("IF THE KEY IN USE MAY HAVE LEAKED, the old key must stop being trusted at once: set NO");
  say("OIDC_SIGNING_JWK_OLD (delete it if it is there), replace OIDC_SIGNING_JWK with the key below,");
  say("redeploy, give the \"board popout\" service token a new secret, and restart Job Hunter's server.");
  say("DEPLOY.md 3f has each step.");
  say();
  value("OIDC_SIGNING_JWK", signingKey(), "a Pages secret on the board's project, replacing the current one");
} else if (args.includes("--secret")) {
  say("A NEW CLIENT SECRET for SLED JOBS' Job Hunter sign-in. Shown once; nothing is saved.");
  say();
  say("Paste it in BOTH places, one straight after the other: the Pages secret OIDC_CLIENT_SECRET, and");
  say("the Client secret of the SLED JOBS identity provider in Zero Trust. Leave OIDC_CLIENT_ID and");
  say("OIDC_SIGNING_JWK as they are. Then a new deployment (DEPLOY.md 3f).");
  say();
  value("OIDC_CLIENT_SECRET", randomBytes(32).toString("base64url"),
    "a Pages secret, and the Client secret of the SLED JOBS identity provider in Zero Trust");
} else {
  say("THE THREE SECRETS for SLED JOBS' Job Hunter sign-in. Shown once; nothing is saved.");
  say("This is the FIRST SETUP. Later, a new key alone is --key, a new client secret alone --secret.");
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
