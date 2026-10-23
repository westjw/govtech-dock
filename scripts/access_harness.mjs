/* DRIVE THE /admin DOOR AS THE EDGE WOULD, with keys we hold.
 *
 * Imports the real middleware - a harness that reasons about a copy proves
 * nothing about the file that ships - generates an RSA pair, publishes its
 * public half as the team JWKS through a fake fetch, mints Access-shaped
 * tokens with the private half, and asks the door. Prints one JSON object;
 * selftest.py asserts on it. The only address in here is TLD-less. */
import { generateKeyPairSync, createSign } from "node:crypto";

const MOD = new URL("../functions/admin/_middleware.js", import.meta.url);
const { onRequest, verify } = await import(MOD);

const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const other = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = publicKey.export({ format: "jwk" });
const JWKS = { keys: [{ kty: "RSA", kid: "k1", n: jwk.n, e: jwk.e, alg: "RS256", use: "sig" }] };

function mint(claims, { key = privateKey, kid = "k1" } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { aud: [AUD], iss: `https://${TEAM}`, iat: now, exp: now + 600,
                    email: "person@desk", ...claims };
  const head = b64({ alg: "RS256", kid, typ: "JWT" });
  const body = b64(payload);
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(key).toString("base64url");
  return `${head}.${body}.${sig}`;
}

let certsDown = false, certsFetches = 0;
globalThis.fetch = async (url) => {
  certsFetches++;
  if (certsDown) throw new Error("network down");
  if (String(url).endsWith("/cdn-cgi/access/certs")) return new Response(JSON.stringify(JWKS), { status: 200 });
  return new Response("nope", { status: 404 });
};

// THE USERS BOARD, served the way the edge serves it (env.ASSETS), keyed by
// the hash of each address. person@desk is an admin; the rest are not.
const { createHash } = await import("node:crypto");
const h = (e) => createHash("sha256").update(e).digest("hex");
const USERS = {
  wyeth: { email_sha256: h("owner@desk"), roles: ["owner"] },
  ana: { email_sha256: h("person@desk"), roles: ["admin"] },
  hal: { email_sha256: h("hunter@desk"), roles: ["hunter"] },
  rex: { email_sha256: h("revoked@desk"), roles: ["admin"], revoked_on: "2026-10-01" },
};
let usersDown = false;
const ENV = { ASSETS: { fetch: async (u) => {
  if (usersDown) throw new Error("assets down");
  return String(u).endsWith("/admin/users.json")
    ? new Response(JSON.stringify(USERS), { status: 200 }) : new Response("no", { status: 404 });
} } };

async function ask(path, { header, cookie } = {}) {
  const headers = new Headers();
  if (header) headers.set("Cf-Access-Jwt-Assertion", header);
  if (cookie) headers.set("Cookie", `CF_Authorization=${cookie}; other=1`);
  const req = new Request(`https://solesource-c6g.pages.dev${path}`, { headers });
  let reached = false;
  const data = {};
  const res = await onRequest({ request: req, env: ENV, data, next: async () => { reached = true; return new Response("ok", { status: 200 }); } });
  let body = "";
  try { body = await res.text(); } catch { body = ""; }
  return { status: res.status, reached, json: body.startsWith("{") ? JSON.parse(body) : null, body,
           identity: data.access || null };
}

const out = {};
out.no_token_static = await ask("/admin/users.json");
out.valid_header    = await ask("/admin/users.json", { header: mint({}) });
out.valid_cookie    = await ask("/admin/data.json", { cookie: mint({}) });
out.expired         = await ask("/admin/users.json", { header: mint({ exp: Math.floor(Date.now() / 1000) - 5 }) });
out.wrong_aud       = await ask("/admin/users.json", { header: mint({ aud: ["someone-elses-app"] }) });
out.wrong_iss       = await ask("/admin/users.json", { header: mint({ iss: "https://evil.example" }) });
out.unknown_kid     = await ask("/admin/users.json", { header: mint({}, { kid: "k9" }) });
out.forged_sig      = await ask("/admin/users.json", { header: mint({}, { key: other.privateKey }) });
out.garbage         = await ask("/admin/users.json", { header: "a.b.c" });
out.api_no_token    = await ask("/admin/api/rule");
out.whoami_open     = await ask("/admin/api/whoami");
// SIGNED IN IS NOT ENOUGH: only the Users board's owner and admins get in
out.owner_in        = await ask("/admin/rulings.json", { cookie: mint({ email: "Owner@Desk " }) });
out.stranger        = await ask("/admin/data.json", { cookie: mint({ email: "stranger@desk" }) });
out.hunter_only     = await ask("/admin/data.json", { cookie: mint({ email: "hunter@desk" }) });
out.revoked         = await ask("/admin/data.json", { cookie: mint({ email: "revoked@desk" }) });
out.no_email        = await ask("/admin/data.json", { cookie: mint({ email: undefined }) });
out.stranger_api    = await ask("/admin/api/rule", { cookie: mint({ email: "stranger@desk" }) });
// the Job Hunter beta page is for every signed-in person, at exactly one spelling
out.hunter_page_member   = await ask("/admin/hunter/", { cookie: mint({ email: "hunter@desk" }) });
out.hunter_page_stranger = await ask("/admin/hunter/", { cookie: mint({ email: "stranger@desk" }) });
out.hunter_page_noslash  = await ask("/admin/hunter", { cookie: mint({ email: "stranger@desk" }) });
out.hunter_page_file     = await ask("/admin/hunter/index.html", { cookie: mint({ email: "stranger@desk" }) });
out.hunter_page_anon     = await ask("/admin/hunter/");
usersDown = true;
out.users_down      = await ask("/admin/data.json", { cookie: mint({}) });
usersDown = false;
out.login_open      = await ask("/admin/api/login?to=/");
const before = certsFetches;
await ask("/admin/users.json", { header: mint({}) });
out.keys_cached     = certsFetches === before;        // a second valid token does not refetch
certsDown = true;
// a fresh kid forces a refetch, and with the keys down the door must not open
out.certs_down      = await ask("/admin/users.json", { header: mint({}, { kid: "k2" }) });
out.verify_direct   = !!(await verify(mint({}), {}).catch(() => null));
console.log(JSON.stringify(out));
