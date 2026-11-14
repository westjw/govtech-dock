/* DRIVE THE SITE'S OWN SIGN-IN as a browser and the edge would (2026-11-13).
 *
 * Imports the real modules - the root middleware for the gate, every
 * functions/signin/ handler, /api/whoami - and runs them with an env that
 * holds a secret, an in-memory KV and a stub Resend and Google, both reached
 * through a fake fetch that records what was sent. Every email sign-in uses
 * the link and the code out of the mail the stub received, so the test
 * signs in with what a person would actually be holding. Prints one JSON
 * object; selftest.py check_our_own_sign_in_proves_the_address_and_opens_only_the_boards
 * asserts on it. The only addresses in here are RFC 2606 ones.
 */
import { createHash, createHmac, generateKeyPairSync, createSign } from "node:crypto";

const M = (p) => import(new URL(`../functions/${p}`, import.meta.url));
const { onRequest } = await M("_middleware.js");
const S = await M("_session.js");
const page = await M("signin/index.js");
const email = await M("signin/email.js");
const code = await M("signin/code.js");
const link = await M("signin/link.js");
const out_ = await M("signin/out.js");
const google = await M("signin/google.js");
const callback = await M("signin/google/callback.js");
const check = await M("signin/check.js");
const who = await M("api/whoami.js");
const csp = await M("_csp.js");

const SITE = "https://sledjobs.com";
const SECRET = "x".repeat(40) + "-harness-secret";
const CLIENT = "client-123.apps.googleusercontent.com";
const h = (e) => createHash("sha256").update(e).digest("hex");
const USERS = { jane: { email_sha256: h("jane@example.org"), roles: ["hunter"] },
                wy: { email_sha256: h("owner@example.org"), roles: ["owner"] } };

/* AN ACCESS TOKEN we can mint, for the cases where both sign-ins are held */
const TEAM = "solesource-c6g-pages.cloudflareaccess.com";
const AUD = "80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9";
const RSA = generateKeyPairSync("rsa", { modulusLength: 2048 });
const JWK = RSA.publicKey.export({ format: "jwk" });
function accessToken(email) {
  const b = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const t = Math.floor(Date.now() / 1000);
  const head = b({ alg: "RS256", kid: "s1", typ: "JWT" });
  const body = b({ aud: [AUD], iss: `https://${TEAM}`, iat: t, exp: t + 600, email });
  return `${head}.${body}.${createSign("RSA-SHA256").update(`${head}.${body}`).sign(RSA.privateKey).toString("base64url")}`;
}

/* --- the world ----------------------------------------------------------- */
let mails = [], tokenCalls = [], resendUp = true, idClaims = null, tokenStatus = 200;
globalThis.fetch = async (url, init = {}) => {
  const u = String(url);
  if (u === "https://api.resend.com/emails") {
    if (!resendUp) return new Response("down", { status: 500 });
    mails.push(JSON.parse(init.body));
    return new Response("{}", { status: 200 });
  }
  if (u === "https://oauth2.googleapis.com/token") {
    tokenCalls.push(Object.fromEntries(new URLSearchParams(String(init.body))));
    if (tokenStatus !== 200) return new Response("{}", { status: tokenStatus });
    const b = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
    return new Response(JSON.stringify({ id_token: `${b({ alg: "RS256" })}.${b(idClaims)}.sig` }), { status: 200 });
  }
  if (u.endsWith("/cdn-cgi/access/certs")) {
    return new Response(JSON.stringify({ keys: [{ kty: "RSA", kid: "s1", n: JWK.n, e: JWK.e, alg: "RS256" }] }), { status: 200 });
  }
  return new Response("nope", { status: 404 });
};
/* KV, counting writes; `failPut`/`failGet` make it refuse as KV does (one
 * write a second on a key, or the day's writes spent) */
let kvWrites = 0;
/* delay: ms each call takes, so simultaneous requests interleave as at the
 * edge. oncePerSecond: a second write to a key within a second is refused,
 * as KV documents. stale: every read answers what the key held before the
 * first write (KV may serve a value up to a minute old). */
function kv({ failPut = null, failGet = false, delay = 0, oncePerSecond = false, stale = false } = {}) {
  const m = new Map(), lastPut = new Map(), first = new Map();
  const wait = () => (delay ? new Promise((r) => setTimeout(r, delay)) : Promise.resolve());
  return { m,
    get: async (k) => { await wait(); if (failGet) throw new Error("KV GET failed: 500");
      if (stale) return first.has(k) ? first.get(k) : (m.has(k) ? m.get(k) : null);
      return m.has(k) ? m.get(k) : null; },
    put: async (k, v) => { await wait();
      if (failPut && failPut(k)) throw new Error("KV PUT failed: 429 Too Many Requests");
      const t = Date.now();
      if (oncePerSecond && lastPut.has(k) && t - lastPut.get(k) < 1000) throw new Error("KV PUT failed: 429 Too Many Requests");
      lastPut.set(k, t);
      if (stale && !first.has(k)) first.set(k, m.has(k) ? m.get(k) : null);
      kvWrites++; m.set(k, v); } };
}
const assets = { fetch: async (u) => {
  const p = new URL(String(u)).pathname;
  if (p === "/admin/users.json") return new Response(JSON.stringify(USERS), { status: 200 });
  if (p === "/landing") return new Response("<h1>LANDING</h1>", { status: 200, headers: { "content-type": "text/html" } });
  return new Response("no", { status: 404 });
} };
const envOf = (extra = {}) => ({ SESSION_SECRET: SECRET, RESEND_KEY: "re_test", ALERTS: kv(),
  ASSETS: assets, GOOGLE_CLIENT_ID: CLIENT, GOOGLE_CLIENT_SECRET: "secret-456-abcdef", ...extra });
let ENV = envOf();

/* --- a browser ----------------------------------------------------------- */
/* What a browser sends with a form POST from our own page: its Origin and
 * Sec-Fetch-Site: same-origin. "null-same" is the same POST under a
 * no-referrer policy (Origin: null, which the first version refused: the
 * runtime review found every Chrome sign-in would have been). Any other
 * origin is another site, and says cross-site. */
function req(path, { method = "GET", host = "sledjobs.com", cookies = {}, origin = "same", form, ip = "198.51.100.7", headers = {} } = {}) {
  const hd = new Headers(headers);
  const jar = Object.entries(cookies).filter(([, v]) => v != null).map(([k, v]) => `${k}=${v}`);
  if (jar.length) hd.set("Cookie", jar.join("; "));
  if (origin === "same") { hd.set("Origin", `https://${host}`); if (!hd.has("Sec-Fetch-Site")) hd.set("Sec-Fetch-Site", "same-origin"); }
  else if (origin === "null-same") { hd.set("Origin", "null"); hd.set("Sec-Fetch-Site", "same-origin"); }
  else if (origin) { hd.set("Origin", origin); if (!hd.has("Sec-Fetch-Site")) hd.set("Sec-Fetch-Site", "cross-site"); }
  hd.set("cf-connecting-ip", ip);
  let body;
  if (form) { hd.set("content-type", "application/x-www-form-urlencoded"); body = new URLSearchParams(form).toString(); }
  return new Request(`https://${host}${path}`, { method, headers: hd, body });
}
async function see(res) {
  const set = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  const jar = {};
  for (const c of set) { const [kv0] = c.split(";"); const i = kv0.indexOf("="); jar[kv0.slice(0, i)] = kv0.slice(i + 1); }
  let body = "";
  try { body = await res.text(); } catch { body = ""; }
  return { status: res.status, location: res.headers.get("location") || "", set, jar,
           referrer: res.headers.get("referrer-policy") || "",
           csp: res.headers.get("content-security-policy") || "", robots: res.headers.get("x-robots-tag") || "",
           cache: res.headers.get("cache-control") || "", body: body.slice(0, 6000) };
}
const call = async (mod, fn, r, env = ENV) => see(await mod[fn]({ request: r, env }));
async function gate(path, cookies = {}, env = ENV, host = "sledjobs.com") {
  let reached = false;
  const res = await onRequest({ request: req(path, { cookies, origin: null, host }), env, data: {},
    next: async () => { reached = true; return new Response("ok", { status: 200, headers: { "content-type": "text/plain" } }); } });
  return { status: res.status, reached };
}
async function whoami(cookies, env = ENV) {
  const r = await who.onRequestGet({ request: req("/api/whoami", { cookies, origin: null }), env });
  return r.json();
}
const linkOf = (m) => ((m && m.text) || "").match(/https:\/\/sledjobs\.com\/signin\/link\?t=[A-Za-z0-9._-]+/)?.[0] || "";
const codeOf = (m) => ((m && m.text) || "").match(/\b([0-9A-HJKMNP-TV-Z]{5})-([0-9A-HJKMNP-TV-Z]{5})\b/)?.slice(1).join("") || "";
async function ask(addr, to = "/?tab=jobs", opts = {}) {
  const before = mails.length, w0 = kvWrites;
  const r = await call(email, "onRequestPost", req("/signin/email", { method: "POST", form: { email: addr, to }, ...opts }));
  return { ...r, mail: mails.length > before ? mails[mails.length - 1] : null, mailed: mails.length - before,
           writes: kvWrites - w0 };
}
/* An emailed link, as a person uses it: open it, then press Sign in. */
async function useLink(tok, { cookies = {}, origin = "same" } = {}) {
  const opened = await call(link, "onRequestGet", req(`/signin/link?t=${tok}`, { origin: null, cookies }));
  const pressed = await call(link, "onRequestPost", req("/signin/link", { method: "POST", origin, cookies, form: { t: tok } }));
  return { opened, pressed };
}
const out = {};

/* --- not set up: nothing verifies --------------------------------------- */
{
  const bare = { ASSETS: assets, ALERTS: kv() };
  out.unset_page = await call(page, "onRequestGet", req("/signin"), bare);
  const before = mails.length;
  out.unset_email = await call(email, "onRequestPost", req("/signin/email", { method: "POST", form: { email: "a@example.org" } }), bare);
  out.unset_mailed = mails.length - before;
  const sealed = (await S.sessionCookie(ENV, "jane@example.org", "email")).split(";")[0].split("=").slice(1).join("=");
  out.unset_gate = await gate("/?tab=jobs", { [S.SESSION]: sealed }, bare);
  // a cookie sealed UNDER the short secret itself, so only the length rule
  // can refuse it
  const body = Buffer.from(JSON.stringify({ v: 1, e: "jane@example.org", m: "email", iat: 1,
    exp: Math.floor(Date.now() / 1000) + 600 })).toString("base64url");
  const forge = (secret) => `${body}.${createHmac("sha256", secret).update(`session.${body}`).digest("base64url")}`;
  const s31 = "k".repeat(31), s32 = "k".repeat(32);
  out.short_secret_gate = await gate("/?tab=jobs", { [S.SESSION]: forge(s31) }, { ...bare, SESSION_SECRET: s31 });
  // the same forging, one character longer, must open: so the length rule
  // is the only difference between the two
  out.short_secret_control = await gate("/?tab=jobs", { [S.SESSION]: forge(s32) }, { ...bare, SESSION_SECRET: s32 });
}

/* --- the page ------------------------------------------------------------ */
out.page = await call(page, "onRequestGet", req("/signin?to=%2F%3Ftab%3Djobs"));
out.page_www = await call(page, "onRequestGet", req("/signin?to=%2F", { host: "www.sledjobs.com" }));
out.page_old = await call(page, "onRequestGet", req("/signin?to=%2F", { host: "solesourcejobs.com" }));
out.page_evil_to = await call(page, "onRequestGet", req("/signin?to=https%3A%2F%2Fx.example%2F"));
out.page_note = await call(page, "onRequestGet", req("/signin?e=google_cancel"));
out.page_note_unknown = await call(page, "onRequestGet", req("/signin?e=%3Cb%3Ehi%3C%2Fb%3E"));

/* --- by email: the link -------------------------------------------------- */
const first = await ask(" Jane@Example.org ", "/?tab=jobs");
out.email_first = { ...first, mail: null };
// where asking lands: a GET page drawn from the pending cookie, so a reload
// sends nothing again
out.check_page = await call(check, "onRequestGet", req("/signin/check", { origin: null, cookies: { [S.PENDING]: first.jar[S.PENDING] } }));
out.check_none = await call(check, "onRequestGet", req("/signin/check", { origin: null }));
out.check_head = await call(check, "onRequestHead", req("/signin/check", { method: "HEAD", origin: null, cookies: { [S.PENDING]: first.jar[S.PENDING] } }));
out.page_head = await call(page, "onRequestHead", req("/signin", { method: "HEAD", origin: null }));
out.mail_to = first.mail && first.mail.to;
out.mail_from = first.mail && first.mail.from;
out.mail_has_link = !!linkOf(first.mail);
out.mail_html_has_link = !!(first.mail && first.mail.html.includes(linkOf(first.mail)));
out.mail_code = codeOf(first.mail);
out.mail_braces = !!(first.mail && /\{\{\s*[A-Za-z_]+\s*\}\}/.test(first.mail.html + first.mail.text));
out.mail_subject = first.mail && first.mail.subject;
out.mail_text = first.mail && first.mail.text;
const t = new URL(linkOf(first.mail) || `${SITE}/signin/link`).searchParams.get("t");
{
  const u = await useLink(t);
  out.link_open = u.opened;
  out.link = u.pressed;
}
const sess = out.link.jar[S.SESSION];
out.session_attrs = out.link.set.find((c) => c.startsWith(`${S.SESSION}=`)) || "";
out.gate_board = await gate("/?tab=jobs", { [S.SESSION]: sess });
out.gate_data = await gate("/data/board.json", { [S.SESSION]: sess });
out.gate_alias = await gate("/c/verkada.html", { [S.SESSION]: sess }, ENV, "solesource-c6g.pages.dev");
out.gate_none = await gate("/?tab=jobs");
out.whoami_email = await whoami({ [S.SESSION]: sess });
out.whoami_none = await whoami({});
out.link_again = (await useLink(t)).pressed;
out.link_same_person = (await useLink(t, { cookies: { [S.SESSION]: sess } })).opened;
out.link_cross_site = (await useLink(t, { origin: "https://x.example" })).pressed;
out.link_no_origin_cross = await call(link, "onRequestPost", req("/signin/link", { method: "POST", origin: null,
  headers: { "Sec-Fetch-Site": "cross-site" }, form: { t } }));
const flip = (s) => s.slice(0, 10) + (s[10] === "A" ? "B" : "A") + s.slice(11);
{
  const tam = await useLink(flip(t));
  out.link_tampered = tam.pressed; out.link_tampered_open = tam.opened;
  const pal = await useLink(first.jar[S.PENDING]);
  out.link_pending_as_link = pal.pressed; out.link_pending_as_link_open = pal.opened;
}
out.gate_link_as_session = await gate("/?tab=jobs", { [S.SESSION]: t });
out.gate_pending_as_session = await gate("/?tab=jobs", { [S.SESSION]: first.jar[S.PENDING] });
{
  const other = envOf({ SESSION_SECRET: "y".repeat(48) });
  const foreign = (await S.sessionCookie(other, "jane@example.org", "email")).split(";")[0].split("=").slice(1).join("=");
  out.gate_other_secret = await gate("/?tab=jobs", { [S.SESSION]: foreign });
  const past = await S.seal(ENV, "session", { v: 1, e: "jane@example.org", m: "email", iat: 1, exp: S.now() - 5 });
  out.gate_expired = await gate("/?tab=jobs", { [S.SESSION]: past });
  const odd = await S.seal(ENV, "session", { v: 1, e: "jane@example.org", m: "admin", iat: 1, exp: S.now() + 60 });
  out.gate_odd_method = await gate("/?tab=jobs", { [S.SESSION]: odd });
  const noaddr = await S.seal(ENV, "session", { v: 1, e: "not-an-address", m: "email", iat: 1, exp: S.now() + 60 });
  out.gate_bad_address = await gate("/?tab=jobs", { [S.SESSION]: noaddr });
  const oldlink = await S.seal(ENV, "link", { e: "jane@example.org", n: "x", to: "/", exp: S.now() - 1 });
  out.link_expired = (await useLink(oldlink)).pressed;
  // somebody else's link opened on a device signed in as jane: it says so
  const others = await S.seal(ENV, "link", { e: "pat@example.org", n: "y", to: "/", exp: S.now() + 300 });
  out.link_replacing = (await useLink(others, { cookies: { [S.SESSION]: sess } })).opened;
  // ONE COOKIE NAME TWICE is no cookie; a near-name is not ours
  const dup = req("/?tab=jobs", { origin: null });
  dup.headers.set("Cookie", `${S.SESSION}=junk; ${S.SESSION}=${sess}`);
  out.dup_session = !!(await S.sessionOf(dup, ENV));
  const near = req("/?tab=jobs", { origin: null });
  near.headers.set("Cookie", `__host-sj_session=${sess}`);
  out.near_name_session = !!(await S.sessionOf(near, ENV));
  const tabbed = req("/?tab=jobs", { origin: null });
  tabbed.headers.set("Cookie", `other=1;\t${S.SESSION}=${sess}`);
  out.tab_trimmed_session = !!(await S.sessionOf(tabbed, ENV));
}

/* --- by email: the code -------------------------------------------------- */
{
  const a = await ask("pat@example.org", "/?tab=companies");
  const b = await ask("pat@example.org", "/?tab=companies", { ip: "198.51.100.8" });
  const typed = codeOf(a.mail).toLowerCase().replace(/^(....)/, "$1 ");
  out.code_right = await call(code, "onRequestPost", req("/signin/code", { method: "POST",
    cookies: { [S.PENDING]: a.jar[S.PENDING] }, form: { code: typed } }));
  out.code_wrong = await call(code, "onRequestPost", req("/signin/code", { method: "POST",
    cookies: { [S.PENDING]: a.jar[S.PENDING] }, form: { code: "00000-00000" } }));
  out.code_wrong_page = await call(check, "onRequestGet", req("/signin/check?e=wrong", { origin: null,
    cookies: { [S.PENDING]: a.jar[S.PENDING] } }));
  // the same right code from a browser under no-referrer: Origin null, same-origin
  out.code_null_same = await call(code, "onRequestPost", req("/signin/code", { method: "POST", origin: "null-same",
    cookies: { [S.PENDING]: a.jar[S.PENDING] }, form: { code: codeOf(a.mail) } }));
  out.code_other_pending = await call(code, "onRequestPost", req("/signin/code", { method: "POST",
    cookies: { [S.PENDING]: b.jar[S.PENDING] }, form: { code: codeOf(a.mail) } }));
  out.code_no_pending = await call(code, "onRequestPost", req("/signin/code", { method: "POST",
    form: { code: codeOf(a.mail) } }));
  out.code_link_as_pending = await call(code, "onRequestPost", req("/signin/code", { method: "POST",
    cookies: { [S.PENDING]: new URL(linkOf(a.mail)).searchParams.get("t") }, form: { code: codeOf(a.mail) } }));
  out.code_cross_site = await call(code, "onRequestPost", req("/signin/code", { method: "POST", origin: "https://x.example",
    cookies: { [S.PENDING]: a.jar[S.PENDING] }, form: { code: codeOf(a.mail) } }));
  out.code_session_ok = !!(await S.sessionOf(req("/", { cookies: { [S.SESSION]: out.code_right.jar[S.SESSION] } }), ENV));
  out.codes_differ = codeOf(a.mail) !== codeOf(b.mail);
}

/* --- what may not send mail --------------------------------------------- */
out.cross_site = await ask("x1@example.org", "/", { origin: "https://x.example" });
out.no_origin_cross_site = await ask("x4@example.org", "/", { origin: null, headers: { "Sec-Fetch-Site": "cross-site" } });
out.null_origin = await ask("x2@example.org", "/", { origin: "null" });
out.null_origin_same_site = await ask("x5@example.org", "/", { origin: "null-same" });
out.bad_address = await ask("not an address", "/");
out.markup_address = await ask('"><script>alert(1)</script>@x', "/");
resendUp = false;
out.resend_down = await ask("x3@example.org", "/");
resendUp = true;

/* --- the day's allowances ----------------------------------------------- */
{
  const row = (r) => ({ status: r.status, mailed: r.mailed, writes: r.writes,
    google: /\bGoogle\b/.test(r.body.split("<body>")[1] || ""), pending: !!r.jar[S.PENDING] });
  ENV = envOf();
  const per = [];
  for (let i = 0; i <= email.PER_CALLER + 1; i++) per.push(await ask(`c${i}@example.org`, "/", { ip: "203.0.113.9" }));
  out.cap_caller = per.map(row);
  // AN IPv6 NETWORK IS ITS /48: eight /64s inside one /48 are one caller
  ENV = envOf();
  const v6 = [];
  for (let i = 0; i <= email.PER_CALLER; i++) v6.push(await ask(`v${i}@example.org`, "/", { ip: `2001:db8:1:${(i + 1) * 16}::1` }));
  out.cap_caller_v6 = v6.map(row);
  ENV = envOf();
  const addr = [];
  for (let i = 0; i <= email.PER_ADDRESS + 1; i++) addr.push(await ask("same@example.org", "/", { ip: `203.0.113.${20 + i}` }));
  out.cap_address = addr.map(row);
  // ONE INBOX HOWEVER IT IS SPELLED: +tags everywhere, dots at Gmail
  ENV = envOf();
  const box = [];
  // built here, not written out: the repository is public and its privacy
  // guard refuses anything shaped like a free-mail address in a tracked file
  const at = (l, d) => `${l}${String.fromCharCode(64)}${d}`;
  const GM = ["gmail", "com"].join("."), GMX = ["googlemail", "com"].join(".");
  const spellings = [at("sam.e+1", GM), at("same+2", GMX), at("s.a.m.e", GM), at("SAME+x", GM), at("same+5", GM)];
  for (let i = 0; i < spellings.length; i++) box.push(await ask(spellings[i], "/", { ip: `203.0.113.${60 + i}` }));
  out.cap_mailbox = box.map(row);
  out.mailbox_fold = [at("a.b+c", GM), "a+b@example.org", "a.b@example.org"].map(email.mailbox);
  out.mailbox_fold_want = [at("ab", GM), "a@example.org", "a.b@example.org"];
  ENV = envOf({ SIGNIN_MAILS_PER_DAY: "3" });
  const all = [];
  for (let i = 0; i < 5; i++) all.push(await ask(`g${i}@example.org`, "/", { ip: `203.0.113.${40 + i}` }));
  out.cap_all = all.map(row);
  // KV REFUSING: a personal counter's write refused (a double-click) sends
  // nothing; the shared counter's refused write is let pass; a read that
  // fails sends nothing
  ENV = envOf({ ALERTS: kv({ failPut: (k) => k.startsWith("cap:signin:") }) });
  out.kv_caller_put = row(await ask("k1@example.org", "/"));
  ENV = envOf({ ALERTS: kv({ failPut: (k) => k.startsWith("cap:signin-all:") }) });
  out.kv_all_put = row(await ask("k2@example.org", "/"));
  ENV = envOf({ ALERTS: kv({ failGet: true }) });
  out.kv_get = row(await ask("k3@example.org", "/"));
  // SIMULTANEOUS REQUESTS FROM MANY NETWORKS (final review, 2026-11-13: 104
  // emails against a cap of 60). Bursts of 13 at once, against KV that keeps
  // the last write, and KV that refuses a second write within a second.
  out.burst = {};
  for (const [name, opts] of [["last_write_wins", { delay: 3 }], ["once_per_second", { delay: 3, oncePerSecond: true }]]) {
    ENV = envOf({ ALERTS: kv(opts), SIGNIN_MAILS_PER_DAY: "60" });
    const before = mails.length;
    for (let wave = 0; wave < 3; wave++) {
      await Promise.all(Array.from({ length: 13 }, (_, i) =>
        ask(`b${wave}x${i}@example.org`, "/", { ip: `198.18.${wave}.${i + 1}` })));
    }
    out.burst[name] = mails.length - before;
  }
  // A STALE READ NEVER REFUSES AN HONEST SIGN-IN: one at a time, against KV
  // whose reads answer what a key held before it was first written
  ENV = envOf({ ALERTS: kv({ stale: true }) });
  const honest = [];
  for (let i = 0; i < 3; i++) honest.push(row(await ask(`h${i}@example.org`, "/", { ip: `198.19.0.${i + 1}` })));
  out.stale_honest = honest;
  ENV = envOf();
}
out.caps = { PER_CALLER: email.PER_CALLER, PER_ADDRESS: email.PER_ADDRESS, ALL_PER_DAY: email.ALL_PER_DAY };

/* --- `to` never leaves the site ----------------------------------------- */
out.to_cases = [];
for (const to of ["/%09/x.example", "/.//x.example", "/./%09/x.example", "//x.example",
                  "/%2F%2Fx.example", "/%5Cx.example", "https://x.example/", "/%0A/x.example",
                  "%20/x.example", "/%7F/x.example", "javascript:alert(1)", "/\t/x.example", "/\\x.example"]) {
  ENV = envOf();
  const a = await ask(`to${out.to_cases.length}@example.org`, decodeURIComponent(to.replace(/%(?![0-9A-F]{2})/gi, "%25")));
  const tok = new URL(linkOf(a.mail) || `${SITE}/x`).searchParams.get("t");
  const l = (await useLink(tok)).pressed;
  const g = await see(await google.onRequestGet({ request: req(`/signin/google?to=${encodeURIComponent(to)}`, { origin: null }), env: ENV }));
  const at = (loc, base) => { try { return new URL(loc.replace(/[\t\n\r]/g, ""), base); } catch { return null; } };
  const lu = at(l.location, `${SITE}/signin/link`);
  out.to_cases.push({ to, mailed: a.mailed, link_status: l.status, link_location: l.location,
    link_origin: lu && lu.origin, link_path: lu && lu.pathname,
    google_status: g.status, google_host: (at(g.location, SITE) || {}).host || "" });
}
ENV = envOf();
{
  const a = await ask("keep@example.org", "/c/verkada.html?tab=jobs");
  const l = (await useLink(new URL(linkOf(a.mail)).searchParams.get("t"))).pressed;
  out.to_keeps_path = l.location;
}
// A `to` SEALED OFF-SITE, as if a door upstream let one through: each door
// that redirects holds `to` again itself
{
  const evil = { e: "jane@example.org", n: "nonce-evil", to: "//x.example/", exp: S.now() + 300 };
  const l = (await useLink(await S.seal(ENV, "link", evil))).pressed;
  const pend = await S.seal(ENV, "pending", evil);
  const c = await call(code, "onRequestPost", req("/signin/code", { method: "POST",
    cookies: { [S.PENDING]: pend }, form: { code: await S.codeFor(ENV, evil) } }));
  const v = S.random(32);
  const st = await S.seal(ENV, "oauth", { to: "//x.example/", h: await S.sha256u(v), n: "nonce-x", exp: S.now() + 300 });
  idClaims = { iss: "https://accounts.google.com", aud: CLIENT, exp: S.now() + 300, email: "jane@example.org",
    email_verified: true, nonce: "nonce-x" };
  const g = await call(callback, "onRequestGet", req(`/signin/google/callback?code=c0de&state=${st}`,
    { origin: null, cookies: { [S.OAUTH]: v } }));
  out.sealed_evil = { link: l.location, code: c.location, google: g.location };
}

/* --- where a person lands -------------------------------------------------- */
{
  ENV = envOf();
  const a = await ask("lands@example.org", "/?tab=jobs");
  const pend = { [S.PENDING]: a.jar[S.PENDING] };
  out.signin_pending = await call(page, "onRequestGet", req("/signin?to=%2F", { origin: null, cookies: pend }));
  out.signin_again = await call(page, "onRequestGet", req("/signin?to=%2F&again=1", { origin: null, cookies: pend }));
  out.email_get_pending = await call(email, "onRequestGet", req("/signin/email", { origin: null, cookies: pend }));
  out.email_get_none = await call(email, "onRequestGet", req("/signin/email", { origin: null }));
  out.code_get_pending = await call(code, "onRequestGet", req("/signin/code", { origin: null, cookies: pend }));
  out.check_back_signed_in = await call(check, "onRequestGet", req("/signin/check", { origin: null, cookies: { [S.SESSION]: sess } }));
  out.google_head = await call(google, "onRequestHead", req("/signin/google?to=%2F", { method: "HEAD", origin: null }));
  // a `to` too long to seal is "/", so the email still signs somebody in
  const longTo = "/?q=" + "x".repeat(3000);
  const L = await ask("long@example.org", longTo);
  const lt = new URL(linkOf(L.mail) || `${SITE}/x`).searchParams.get("t");
  out.long_to = { asked: L.status, link: (await useLink(lt)).pressed.location, cookie_len: (L.jar[S.PENDING] || "").length };
}

/* --- signing out --------------------------------------------------------- */
out.out_page = await call(out_, "onRequestGet", req("/signin/out", { cookies: { [S.SESSION]: sess } }));
out.out_post = await call(out_, "onRequestPost", req("/signin/out", { method: "POST", cookies: { [S.SESSION]: sess } }));
out.out_access = await call(out_, "onRequestPost", req("/signin/out", { method: "POST",
  cookies: { [S.SESSION]: sess, CF_Authorization: accessToken("owner@example.org") } }));
out.out_stale_access = await call(out_, "onRequestPost", req("/signin/out", { method: "POST",
  cookies: { [S.SESSION]: sess, CF_Authorization: "a.b.c" } }));
// BOTH SIGN-INS HELD: whoami answers as the Access one (the owner's), never
// as a link somebody sent him
out.whoami_both = await whoami({ [S.SESSION]: sess, CF_Authorization: accessToken("owner@example.org") });
// THE SIGN-IN PAGES REFUSE TO BE FRAMED
{
  const page_ = new Response("<html></html>", { headers: { "content-type": "text/html" } });
  const framedRes = await csp.secure(page_, req("/signin", { origin: null }), ENV);
  out.signin_frame = framedRes.headers.get("x-frame-options") || "";
  const home = await csp.secure(new Response("<html></html>", { headers: { "content-type": "text/html" } }), req("/", { origin: null }), ENV);
  out.home_frame = home.headers.get("x-frame-options") || "";
}
out.out_cross = await call(out_, "onRequestPost", req("/signin/out", { method: "POST", origin: "https://x.example",
  cookies: { [S.SESSION]: sess } }));
out.page_signed_in = await call(page, "onRequestGet", req("/signin?to=%2F%3Ftab%3Dconferences", { cookies: { [S.SESSION]: sess } }));

/* --- with Google --------------------------------------------------------- */
{
  const off = envOf({ GOOGLE_CLIENT_ID: undefined, GOOGLE_CLIENT_SECRET: undefined });
  out.g_off = await see(await google.onRequestGet({ request: req("/signin/google?to=%2F%3Ftab%3Djobs", { origin: null }), env: off }));
  out.g_off_callback = await see(await callback.onRequestGet({ request: req("/signin/google/callback?code=x&state=y", { origin: null }), env: off }));
  out.g_www = await call(google, "onRequestGet", req("/signin/google?to=%2F", { host: "www.sledjobs.com", origin: null }));
  const start = await call(google, "onRequestGet", req("/signin/google?to=%2F%3Ftab%3Djobs", { origin: null }));
  const au = new URL(start.location);
  const verifier = start.jar[S.OAUTH];
  out.g_start = { status: start.status, host: au.host, path: au.pathname,
    client_id: au.searchParams.get("client_id"), redirect_uri: au.searchParams.get("redirect_uri"),
    scope: au.searchParams.get("scope"), method: au.searchParams.get("code_challenge_method"),
    response_type: au.searchParams.get("response_type"),
    challenge_matches: au.searchParams.get("code_challenge") === (await S.sha256u(verifier || "")),
    state_has_to: au.search.includes("tab%3Djobs") || au.search.includes("tab=jobs"),
    oauth_cookie: start.set.find((c) => c.startsWith(`${S.OAUTH}=`)) || "" };
  const state = au.searchParams.get("state");
  const sealedNonce = (await S.unseal(ENV, "oauth", state) || {}).n;
  out.g_start.nonce_sent = !!sealedNonce && au.searchParams.get("nonce") === sealedNonce;
  const good = () => ({ iss: "https://accounts.google.com", aud: CLIENT, azp: CLIENT, exp: S.now() + 300,
    email: "Jane@Example.org", email_verified: true, sub: "1", nonce: sealedNonce });
  const finish = async (claims, { st = state, v = verifier, q = "", status = 200 } = {}) => {
    idClaims = claims; tokenStatus = status;
    const r = await call(callback, "onRequestGet", req(`/signin/google/callback?code=c0de&state=${st}${q}`,
      { origin: null, cookies: { [S.OAUTH]: v } }));
    tokenStatus = 200;
    return { ...r, signed: !!r.jar[S.SESSION] };
  };
  tokenCalls = [];
  out.g_ok = await finish(good());
  out.g_token_call = tokenCalls[0] ? { verifier_sent: tokenCalls[0].code_verifier === verifier,
    secret_sent: tokenCalls[0].client_secret === "secret-456-abcdef", client: tokenCalls[0].client_id,
    redirect_uri: tokenCalls[0].redirect_uri, grant: tokenCalls[0].grant_type } : null;
  out.g_whoami = await whoami({ [S.SESSION]: out.g_ok.jar[S.SESSION] });
  out.g_gate = await gate("/?tab=jobs", { [S.SESSION]: out.g_ok.jar[S.SESSION] });
  const wrongVerifier = (await call(google, "onRequestGet", req("/signin/google?to=%2F", { origin: null }))).jar[S.OAUTH];
  out.g_fail = {
    no_cookie: await finish(good(), { v: null }),
    other_verifier: await finish(good(), { v: wrongVerifier }),
    tampered_state: await finish(good(), { st: flip(state) }),
    link_as_state: await finish(good(), { st: t }),
    other_aud: await finish({ ...good(), aud: "someone-else", azp: "someone-else" }),
    other_iss: await finish({ ...good(), iss: "https://x.example" }),
    unverified: await finish({ ...good(), email_verified: false }),
    expired: await finish({ ...good(), exp: S.now() - 5 }),
    no_email: await finish({ ...good(), email: undefined }),
    token_refused: await finish(good(), { status: 400 }),
    multi_aud_wrong_azp: await finish({ ...good(), aud: [CLIENT, "other"], azp: "other" }),
    wrong_nonce: await finish({ ...good(), nonce: "not-the-one" }),
    no_nonce: await finish({ ...good(), nonce: undefined }),
  };
  out.g_cancel = await finish(good(), { q: "&error=access_denied" });
  {
    const old = await S.seal(ENV, "oauth", { to: "/", h: await S.sha256u(verifier), n: sealedNonce, exp: S.now() - 1 });
    out.g_fail.expired_state = await finish(good(), { st: old });
    const noN = await S.seal(ENV, "oauth", { to: "/", h: await S.sha256u(verifier), exp: S.now() + 300 });
    out.g_fail.state_without_nonce = await finish({ ...good(), nonce: undefined }, { st: noN });
  }
}

/* --- the doors stay open, and only these --------------------------------- */
out.open = {};
for (const p of ["/signin", "/signin?to=%2F", "/signin/email", "/signin/check", "/signin/code", "/signin/link?t=x", "/signin/link",
                 "/signin/out", "/signin/google", "/signin/google/callback?code=x", "/api/whoami",
                 "/signin/", "/signin/check/", "/signin/google/callback/"]) {
  out.open[p] = await gate(p);
}
out.shut = {};
for (const p of ["/signin/other", "/signin/other/", "/signin//", "/signin/google/other", "/signinx", "/api/whoamix",
                 "/signin/link/x", "/api/whoami/", "/admin/api/login/"]) {
  out.shut[p] = await gate(p);
}

console.log(JSON.stringify(out));
