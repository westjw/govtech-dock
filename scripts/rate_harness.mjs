/* Drive functions/api/rate.js for real: a fake KV that counts reads and
 * writes, a fake edge cache, and the actual module imported rather than read.
 *
 * The ratings endpoint shares the KV namespace that alerts and claims live
 * in, and on the free plan that namespace has 100,000 reads and 1,000 writes
 * a day for the whole account. Until 2026-10-06 one view of the conferences
 * tab cost a read per conference (138), and the per-caller vote cap reset
 * with a new user-agent string. This measures both.
 *
 * Prints one JSON object of results. selftest asserts on it.
 */
const KV = new Map();
let reads = 0, writes = 0;
const env = {
  ALERTS: {
    async get(k) { reads++; return KV.has(k) ? KV.get(k) : null; },
    async put(k, v) { writes++; KV.set(k, v); },
    async delete(k) { KV.delete(k); },
  },
};
const edge = new Map();
globalThis.caches = {
  default: {
    async match(req) { const r = edge.get(req.url); return r ? r.clone() : undefined; },
    async put(req, res) { edge.set(req.url, res); },
  },
};

const mod = await import("../functions/api/rate.js");
const hdr = (ip, ua) => new Headers({ "cf-connecting-ip": ip, "user-agent": ua,
                                      "content-type": "application/json" });
const get = async (q) => {
  const res = await mod.onRequestGet({
    request: new Request("https://sledjobs.com/api/rate?" + q), env });
  return { status: res.status, body: await res.json() };
};
const vote = async (tag, ip, ua) => {
  const res = await mod.onRequestPost({
    request: new Request("https://sledjobs.com/api/rate", {
      method: "POST", headers: hdr(ip, ua), body: JSON.stringify({ tag, score: 1 }) }),
    env });
  return res.status;
};

const out = {};
const tags = Array.from({ length: 138 }, (_, i) => `Conf ${i} 2026`);
KV.set("worth:Conf 1 2026", JSON.stringify({ n: 4, sum: 3 }));

out.first = await get("tags=" + encodeURIComponent(tags.join(",")));
out.readsFirstView = reads;
await get("tags=" + encodeURIComponent(tags.join(",")));
await get("tags=" + encodeURIComponent([...tags].reverse().join(",")));
out.readsAfterThreeViews = reads;
out.shownAverage = (out.first.body.ratings || []).find((r) => r.tag === "Conf 1 2026");
delete out.first;

// one caller, a new user-agent on every vote
const codes = [];
for (let i = 0; i < 30; i++) codes.push(await vote("Conf 2 2026", "198.51.100.7", `agent/${i}`));
out.acceptedFromOneIp = codes.filter((c) => c === 200).length;
out.refusedFromOneIp = codes.filter((c) => c === 429).length;
out.otherIp = await vote("Conf 2 2026", "203.0.113.9", "agent/x");
out.writes = writes;

console.log(JSON.stringify(out));
