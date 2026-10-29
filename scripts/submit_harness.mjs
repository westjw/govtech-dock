/* Drive functions/api/submit.js for real: a fake KV for the daily caps, a
 * fake GitHub that counts the issues it is asked to open, and the actual
 * module imported rather than read.
 *
 * Every accepted submission opens a public issue on the repo and starts the
 * add-company workflow, so until 2026-10-28 one script could open as many as
 * it liked (review). This measures the per-caller cap, the site-wide cap,
 * the JSON-only rule and the refusal with no store to count in.
 *
 * Prints one JSON object of results. selftest asserts on it.
 */
const KV = new Map();
let writes = 0;
const kv = {
  async get(k) { return KV.has(k) ? KV.get(k) : null; },
  async put(k, v) { writes++; KV.set(k, v); },
  async delete(k) { KV.delete(k); },
};
let issues = 0;
globalThis.fetch = async (url) => {
  if (String(url).startsWith("https://api.github.com/")) {
    issues++;
    return new Response(JSON.stringify({ number: issues, html_url: "https://github.test/i/" + issues }),
                        { status: 201, headers: { "content-type": "application/json" } });
  }
  throw new Error("unexpected fetch " + url);
};
const mod = await import("../functions/api/submit.js");
const post = async (ip, { env, type = "application/json", site = "https://acme-gov.test/" } = {}) => {
  const res = await mod.onRequestPost({
    request: new Request("https://sledjobs.com/api/submit", {
      method: "POST",
      headers: new Headers({ "cf-connecting-ip": ip, "content-type": type }),
      body: JSON.stringify({ website: site, context: "hi" }) }),
    env: env || { GITHUB_SUBMIT_TOKEN: "t", ALERTS: kv } });
  return res.status;
};

const out = {};
const one = [];
for (let i = 0; i < 8; i++) one.push(await post("198.51.100.7"));
out.fromOneCaller = one;
out.issuesAfterOneCaller = issues;
out.plainText = await post("198.51.100.8", { type: "text/plain" });
out.plainTextDisguised = await post("198.51.100.8", { type: "text/plain;application/json" });
out.jsonWithCharset = await post("198.51.100.10", { type: "application/json; charset=utf-8" });
let before = issues;
out.noStore = await post("198.51.100.9", { env: { GITHUB_SUBMIT_TOKEN: "t" } });
out.issuesWithNoStore = issues - before;
// many callers, one day: the site-wide cap holds whatever the addresses
const many = [];
for (let i = 0; i < 60; i++) many.push(await post(`203.0.${Math.floor(i / 200)}.${10 + i}`));
out.manyAccepted = many.filter((c) => c === 200).length;
out.manyRefused = many.filter((c) => c === 429).length;
out.issuesTotal = issues;
// once the day is full, a refused attempt writes nothing to the shared store
const keysBefore = KV.size;
const writesBefore = writes;
for (let i = 0; i < 10; i++) await post(`192.0.2.${i}`);
out.writesWhenFull = writes - writesBefore;
out.keysWhenFull = KV.size - keysBefore;
console.log(JSON.stringify(out));
