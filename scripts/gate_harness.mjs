/* DRIVE THE SITE-WIDE GATE AS THE EDGE WOULD, with keys we hold.
 *
 * Imports the real root middleware - the gate has to be proven WIRED, not
 * just written - plus functions/_gate.js for its GATED switch. Generates an
 * RSA pair, publishes its public half as the team JWKS through a fake
 * fetch, mints Access-shaped tokens with the private half, and asks. The
 * next() handed to the middleware answers text/plain, so the HTML rewrite
 * (HTMLRewriter, which node lacks) is never reached. Prints one JSON object;
 * selftest.py asserts on it. The only address in here is TLD-less. */
import { generateKeyPairSync, createSign } from "node:crypto";

const { onRequest } = await import(new URL("../functions/_middleware.js", import.meta.url));
const { GATED } = await import(new URL("../functions/_gate.js", import.meta.url));
const login = await import(new URL("../functions/admin/api/login.js", import.meta.url));

const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const other = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = publicKey.export({ format: "jwk" });
const JWKS = { keys: [{ kty: "RSA", kid: "g1", n: jwk.n, e: jwk.e, alg: "RS256", use: "sig" }] };

function mint(claims, { key = privateKey, kid = "g1" } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { aud: [AUD], iss: `https://${TEAM}`, iat: now, exp: now + 600,
                    email: "person@desk", ...claims };
  const head = b64({ alg: "RS256", kid, typ: "JWT" });
  const body = b64(payload);
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(key).toString("base64url");
  return `${head}.${body}.${sig}`;
}

let certsDown = false;
globalThis.fetch = async (url) => {
  if (certsDown) throw new Error("network down");
  if (String(url).endsWith("/cdn-cgi/access/certs")) return new Response(JSON.stringify(JWKS), { status: 200 });
  return new Response("nope", { status: 404 });
};

async function ask(path, { host = "sledjobs.com", method = "GET", header, cookie, ctype, nextStatus = 200 } = {}) {
  const headers = new Headers();
  if (ctype) headers.set("content-type", ctype);
  if (header) headers.set("Cf-Access-Jwt-Assertion", header);
  if (cookie) headers.set("Cookie", `other=1; CF_Authorization=${cookie}`);
  const req = new Request(`https://${host}${path}`, { method, headers });
  let reached = false;
  const res = await onRequest({ request: req, env: {},
    next: async () => { reached = true; return new Response("ok", { status: nextStatus, headers: { "content-type": "text/plain" } }); } });
  let body = "";
  try { body = await res.text(); } catch { body = ""; }
  return { status: res.status, reached,
           xfo: res.headers.get("x-frame-options") || "",
           fa: res.headers.get("content-security-policy") || "",
           type: res.headers.get("content-type") || "",
           robots: res.headers.get("x-robots-tag") || "",
           cache: res.headers.get("cache-control") || "",
           json: body.startsWith("{") ? JSON.parse(body) : null,
           body: body.slice(0, 4000) };
}

const ALIAS = "solesource-c6g.pages.dev";
const out = { gated: GATED };
// ways in that must be shut while the site is gated
out.anon_home        = await ask("/");
out.anon_alias_page  = await ask("/c/verkada.html", { host: ALIAS });
out.anon_board_data  = await ask("/data/board.json");
out.anon_meta        = await ask("/meta-roles.json");
out.anon_head        = await ask("/", { method: "HEAD" });
out.anon_api_post    = await ask("/api/alerts", { method: "POST" });
out.anon_api_get     = await ask("/api/claim?t=x");
out.anon_admin_file  = await ask("/admin/users.json", { host: ALIAS });
// THE ONE SHAPE THAT PASSES while gated: a mail client's one-click
// unsubscribe (RFC 8058) - and every near miss stays shut
const T43 = "t".repeat(43);
const FORM = "application/x-www-form-urlencoded";
out.oneclick_form      = await ask(`/api/alerts?t=${T43}`, { method: "POST", ctype: FORM });
out.oneclick_multipart = await ask(`/api/alerts?t=${T43}`, { method: "POST", ctype: "Multipart/Form-Data; boundary=x" });
out.oneclick_json      = await ask(`/api/alerts?t=${T43}`, { method: "POST", ctype: "application/json" });
out.oneclick_no_token  = await ask("/api/alerts", { method: "POST", ctype: FORM });
out.oneclick_bad_token = await ask("/api/alerts?t=short", { method: "POST", ctype: FORM });
out.oneclick_get       = await ask(`/api/alerts?t=${T43}`, { ctype: FORM });
out.oneclick_elsewhere = await ask(`/api/claim?t=${T43}`, { method: "POST", ctype: FORM });
out.trick_slash      = await ask("/admin/api/login/");
out.trick_case       = await ask("/admin/api/LOGIN");
out.trick_encoded    = await ask("/%61dmin/api/login");
out.trick_double     = await ask("//admin/api/login");
out.trick_dotdot     = await ask("/admin/api/whoami/../../data/board.json");
out.expired          = await ask("/", { header: mint({ exp: Math.floor(Date.now() / 1000) - 5 }) });
out.wrong_aud        = await ask("/", { header: mint({ aud: ["someone-elses-app"] }) });
out.wrong_iss        = await ask("/", { header: mint({ iss: "https://evil.example" }) });
out.unknown_kid      = await ask("/", { header: mint({}, { kid: "g9" }) });
out.forged_sig       = await ask("/", { header: mint({}, { key: other.privateKey }) });
out.garbage          = await ask("/", { cookie: "a.b.c" });
out.xss              = await ask('/"><script>alert(1)</script>?q="><img src=x onerror=alert(1)>');
// the two paths that stay open, and signed-in people
out.login_open       = await ask("/admin/api/login?to=/");
out.whoami_open      = await ask("/admin/api/whoami", { host: ALIAS });
out.valid_header     = await ask("/", { header: mint({}) });
out.valid_cookie     = await ask("/c/verkada.html", { host: ALIAS, cookie: mint({}) });
out.valid_api_post   = await ask("/api/alerts", { method: "POST", cookie: mint({}) });
// keys down: a fresh kid forces a refetch, and the gate must not open
certsDown = true;
out.certs_down       = await ask("/", { header: mint({}, { kid: "g2" }) });
out.certs_down_api   = await ask("/api/alerts", { method: "POST", header: mint({}, { kid: "g3" }) });

// The sign-in link every refused visitor is sent to must only ever send them
// back to this site. Each `to` is resolved the way a browser resolves the
// Location it gets: tabs and newlines stripped, then parsed against the page.
const ORIGIN = "https://sledjobs.com";
async function landing(to) {
  const req = new Request(`${ORIGIN}/admin/api/login?to=${to}`);
  const res = await login.onRequestGet({ request: req });
  const loc = res.headers.get("location") || "";
  const resolved = new URL(loc.replace(/[\t\n\r]/g, ""), `${ORIGIN}/admin/api/login`);
  return { to, status: res.status, location: loc, origin: resolved.origin, path: resolved.pathname + resolved.search };
}
out.login_redirects = [];
for (const to of ["/%09/x.example", "/.//x.example", "/./%09/x.example", "//x.example",
                  "/%2F%2Fx.example", "/%5Cx.example", "https://x.example/", "/%0A/x.example",
                  "%20/x.example", "/%7F/x.example", "javascript:alert(1)"]) {
  out.login_redirects.push(await landing(to));
}
out.login_keeps_path = await landing(encodeURIComponent("/c/verkada.html?tab=jobs"));
// FRAME PROTECTION comes from the middleware itself, on every spelling of the
// two token pages, and nowhere else (the board may be embedded)
// BROWSER CACHING on images and the board's data, and nowhere else
out.caching = {};
for (const p of ["/assets/logos/axon.png", "/assets/mascot/svg/mascot-stand.svg",
                 "/data/board.json", "/data/detail/axon.json", "/", "/alerts",
                 "/data/companies.json", "/assets/logos/axon.png.html"]) {
  out.caching[p] = (await ask(p, { cookie: mint({}) })).cache;
}
out.caching_refused = (await ask("/assets/logos/axon.png")).cache;
out.caching_missing = (await ask("/assets/logos/nope.png", { cookie: mint({}), nextStatus: 404 })).cache;
out.caching_post = (await ask("/data/board.json", { cookie: mint({}), method: "POST" })).cache;
out.frames = {};
for (const p of ["/alerts", "/alerts.html", "/claim", "/claim.html", "/claim?t=abc",
                 "/%61lerts", "/cl%61im.html", "/", "/c/verkada.html"]) {
  out.frames[p] = await ask(p, { cookie: mint({}) });
}
console.log(JSON.stringify(out));
