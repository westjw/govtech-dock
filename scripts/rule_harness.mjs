/* DRIVE rule.js AS A SIGNED-IN PERSON, and prove no address survives.
 *
 * The write path used to stamp the Access email into every ruling record and
 * into the commit message, in a repository that is public. This imports the
 * real module - no re-implementation, because a harness that reasons about a
 * copy proves nothing about the file that ships - and hands it a fake ASSETS
 * (serving a users.json), a fake GitHub (recording what would be PUT) and the
 * Access headers Cloudflare would set. Then it reads back everything the
 * function tried to write and asserts an address is nowhere in it.
 *
 * Prints one JSON object. selftest.py reads it and does the asserting. */
import { createHash } from "node:crypto";

const MOD = new URL("../functions/admin/api/rule.js", import.meta.url);
const { onRequestPost } = await import(MOD);

const sha = (s) => createHash("sha256").update(String(s).trim().toLowerCase()).digest("hex");

const OWNER = "wyeth.west@example.org";      // the signed-in person
const STRANGER = "someone.else@example.org"; // signed in, no row
const REVOKED = "gone@example.org";          // had a row, revoked

const USERS = {
  wyeth: { email_sha256: sha(OWNER), roles: ["owner", "admin"], revoked_on: null },
  sam:   { email_sha256: sha("sam@example.org"), roles: ["admin"], revoked_on: null },
  jane:  { email_sha256: sha("jane@example.org"), roles: ["hunter"], revoked_on: null },
  ghost: { email_sha256: sha(REVOKED), roles: ["admin"], revoked_on: "2026-09-01" },
};

function envFor(putLog) {
  return {
    GITHUB_ADMIN_TOKEN: "fake-token",
    ASSETS: {
      fetch: async () => new Response(JSON.stringify(USERS),
                                      { headers: { "content-type": "application/json" } }),
    },
    __puts: putLog,
  };
}

// stand in for GitHub: report the file as empty, record every write. `served`
// overrides what the GET hands back, for the cases where the file is unreadable.
let served = null;
function fakeFetch(putLog) {
  return async (url, init = {}) => {
    const u = String(url);
    if ((init.method || "GET") === "GET") {
      return new Response(JSON.stringify(served || { content: btoa("{}"), encoding: "base64", sha: "deadbeef" }),
                          { status: 200 });
    }
    putLog.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ commit: { sha: "abc1234" } }), { status: 200 });
  };
}

async function call(email, body, putLog) {
  const headers = new Headers();
  if (email) {
    headers.set("Cf-Access-Authenticated-User-Email", email);
    headers.set("Cf-Access-Jwt-Assertion", "fake.jwt.value");
  }
  const req = new Request("https://sledjobs.com/admin/api/rule",
                          { method: "POST", headers, body: JSON.stringify(body) });
  const res = await onRequestPost({ request: req, env: envFor(putLog) });
  return { status: res.status || 200, body: await res.json() };
}

const out = { cases: {}, wrote: [] };
const RULING = { kind: "vendor", call: "out", name: "Acme Payroll", why: "horizontal" };

// 1. the owner rules: it must land, and carry a handle
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call(OWNER, RULING, puts);
  out.cases.owner_rules = { status: r.status, ok: !!r.body.ok, error: r.body.error || null };
  out.wrote = puts;
}
// 2. signed in, but the Users board never granted admin
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call("jane@example.org", RULING, puts);
  out.cases.wrong_role = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
}
// 3. signed in, no row at all
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call(STRANGER, RULING, puts);
  out.cases.no_row = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
}
// 4. a revoked admin
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call(REVOKED, RULING, puts);
  out.cases.revoked = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
}
// 5. nobody signed in
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call(null, RULING, puts);
  out.cases.anonymous = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
}

// 6. a GRANT: the owner may, an admin may not, and the address never lands
const GRANT = { kind: "user", email: "newperson@example.org", handle: "newperson",
                roles: ["hunter"], label: "a beta tester" };
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call(OWNER, GRANT, puts);
  const rec = puts.length ? JSON.parse(Buffer.from(puts[0].content, "base64").toString("utf8")) : {};
  out.cases.owner_grants = { status: r.status, ok: !!r.body.ok, wrote: puts.length,
    hash_ok: !!(rec.newperson && rec.newperson.email_sha256 === sha("newperson@example.org")),
    roles: rec.newperson && rec.newperson.roles, applied: rec.newperson && rec.newperson.applied };
  out.wrote.push(...puts);
}
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call("sam@example.org", GRANT, puts);
  out.cases.admin_cannot_grant = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
  out.wrote.push(...puts);
}
{
  const puts = [];
  globalThis.fetch = fakeFetch(puts);
  const r = await call(OWNER, { ...GRANT, roles: ["owner"] }, puts);
  out.cases.owner_role_not_grantable = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
}

// 7. A FILE THAT CANNOT BE READ IS NEVER WRITTEN OVER. Unparseable content,
// and the Contents API's empty non-base64 body for a file over 1 MB, both
// used to become {} and be PUT back - every prior ruling gone.
for (const [name, bad] of [["unparseable", { content: btoa("{not json"), encoding: "base64", sha: "s1" }],
                           ["too_large", { content: "", encoding: "none", sha: "s2", size: 2000000 }]]) {
  const puts = [];
  served = bad;
  globalThis.fetch = fakeFetch(puts);
  const r = await call(OWNER, RULING, puts);
  out.cases["unreadable_" + name] = { status: r.status, ok: !!r.body.ok, wrote: puts.length };
}
served = null;

// EVERYTHING the function tried to send GitHub, as one string: the record
// bodies AND the commit messages. An address anywhere in here is the bug.
out.everything_written = JSON.stringify(out.wrote) +
  " " + out.wrote.map(p => {
    try { return Buffer.from(p.content, "base64").toString("utf8"); }
    catch { return ""; }
  }).join(" ");
console.log(JSON.stringify(out));
