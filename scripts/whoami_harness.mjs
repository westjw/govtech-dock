/* DRIVE /admin/api/whoami AND /admin/api/login AS THE EDGE WOULD.
 *
 * whoami is the public account menu's question - "who is signed in, and what
 * may they open" - and it is OPEN: the /admin door does not run in front of
 * it. Until 2026-10-06 it took Cf-Access-Authenticated-User-Email on trust, a
 * header a client can send on the pages.dev alias where no Access application
 * stands. It now verifies the Access token itself, so this imports the real
 * modules, generates an RSA pair, serves its public half as the team JWKS
 * through a fake fetch, and mints Access-shaped tokens - the same rig as
 * access_harness.mjs. Prints one JSON object; selftest.py asserts on it.
 * Addresses here are example.org. */
import { generateKeyPairSync, createSign, createHash } from "node:crypto";

const who = await import(new URL("../functions/admin/api/whoami.js", import.meta.url));
const login = await import(new URL("../functions/admin/api/login.js", import.meta.url));

const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");
const sha = (s) => createHash("sha256").update(s).digest("hex");

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const other = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = publicKey.export({ format: "jwk" });
const JWKS = { keys: [{ kty: "RSA", kid: "k1", n: jwk.n, e: jwk.e, alg: "RS256", use: "sig" }] };

function mint(email, { key = privateKey, aud = [AUD] } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const head = b64({ alg: "RS256", kid: "k1", typ: "JWT" });
  const body = b64({ aud, iss: `https://${TEAM}`, iat: now, exp: now + 600, email });
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(key).toString("base64url");
  return `${head}.${body}.${sig}`;
}

let certsDown = false;
globalThis.fetch = async (url) => {
  if (certsDown) throw new Error("network down");
  if (String(url).endsWith("/cdn-cgi/access/certs")) return new Response(JSON.stringify(JWKS), { status: 200 });
  return new Response("nope", { status: 404 });
};

const USERS = {
  jane: { email_sha256: sha("jane.doe@example.org"), roles: ["hunter"], revoked_on: null },
  old: { email_sha256: sha("old@example.org"), roles: ["admin"], revoked_on: "2026-09-01" },
};
const env = { ASSETS: { fetch: async () => new Response(JSON.stringify(USERS), { status: 200 }) } };

async function ask({ token, cookie, header } = {}) {
  const headers = new Headers();
  if (token) headers.set("Cf-Access-Jwt-Assertion", token);
  if (cookie) headers.set("Cookie", `CF_Authorization=${cookie}`);
  if (header) headers.set("Cf-Access-Authenticated-User-Email", header);
  const res = await who.onRequestGet({
    request: new Request("https://solesource-c6g.pages.dev/admin/api/whoami", { headers }), env });
  return res.json();
}

const out = {};
out.none = await ask();
out.jane = await ask({ token: mint(" Jane.Doe@Example.org ") });
out.jane_cookie = await ask({ cookie: mint("jane.doe@example.org") });
out.old = await ask({ token: mint("old@example.org") });
out.stranger = await ask({ token: mint("who@example.org") });
out.header_only = await ask({ header: "jane.doe@example.org" });
out.forged = await ask({ token: mint("jane.doe@example.org", { key: other.privateKey }) });
out.other_app = await ask({ token: mint("jane.doe@example.org", { aud: ["someone-elses-app"] }) });
certsDown = true;
out.keys_down = await ask({ token: mint("jane.doe@example.org").replace(/^[^.]+/, b64({ alg: "RS256", kid: "k9", typ: "JWT" })) });
certsDown = false;
out.janeRaw = JSON.stringify(out.jane);

const loc = async (to) => (await login.onRequestGet({
  request: new Request("https://sledjobs.com/admin/api/login?to=" + encodeURIComponent(to)) })).headers.get("location");
out.home = await loc("/");
out.co = await loc("/?co=brinc");
out.evil = await loc("https://evil.example/");
out.prot = await loc("//evil.example/");
console.log(JSON.stringify(out));
