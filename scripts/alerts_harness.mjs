/* Drive functions/api/alerts.js for real: a fake KV that records each write's
 * options, a fake mail sender, a clock this file moves, and the actual module
 * imported rather than read.
 *
 * Nothing executed alerts.js's request handlers until 2026-10-06. Two
 * promises in it were false and no check could have said so: the
 * confirmation mail's "the request expires on its own" (nothing expired it)
 * and the pending branch's "so this cannot be used to bomb somebody else's
 * inbox" (the confirmed branch, a few lines up, mailed on every request).
 *
 * Prints one JSON object of results. selftest asserts on it.
 */
const out = {};
const KV = new Map();          // key -> {v, ttl}
const writes = [];             // every put, in order: {k, ttl}
const sent = [];
let clock = 1_800_000_000_000;
Date.now = () => clock;

const env = {
  RESEND_KEY: "test",
  ALERTS: {
    async get(k) { return KV.has(k) ? KV.get(k).v : null; },
    async put(k, v, opts) {
      const ttl = opts && opts.expirationTtl ? opts.expirationTtl : null;
      KV.set(k, { v, ttl });
      writes.push({ k, ttl });
    },
    async delete(k) { KV.delete(k); },
  },
};
globalThis.fetch = async (url, init) => {
  if (String(url).includes("api.resend.com")) {
    sent.push(JSON.parse(init.body).subject);
    return { ok: true };
  }
  return { ok: false, json: async () => ({}) };
};

const mod = await import("../functions/api/alerts.js");
/* Every request below comes from a DIFFERENT caller unless one is named,
   because the cooldowns are what stop many callers aiming at one address;
   the per-caller cap (case 8) is what stops one caller aiming at many. */
let nextIp = 0;
const post = async (body, ip) => {
  const res = await mod.onRequestPost({
    request: { json: async () => body, url: "https://sledjobs.com/api/alerts",
               headers: new Headers({ "cf-connecting-ip": ip || `198.51.100.${++nextIp % 250}` }) },
    env,
  });
  return { status: res.status, body: await res.json() };
};
const subKey = () => [...KV.keys()].find((k) => k.startsWith("sub:"));
const emKey = () => [...KV.keys()].find((k) => k.startsWith("em:"));
const ttlOf = (k) => (k && KV.has(k) ? KV.get(k).ttl : "missing");
const PREFS = { cadence: "weekly" };

/* 1. A new signup: both keys expire, one confirmation goes out. */
out.signup = await post({ action: "subscribe", email: "pat@example.org", prefs: PREFS });
out.pendingSubTtl = ttlOf(subKey());
out.pendingEmTtl = ttlOf(emKey());
out.mailsAfterSignup = sent.length;
const token = subKey().slice(4);

/* 2. Settings saved BEFORE confirming must not make the record permanent. */
await post({ action: "update", token, prefs: { cadence: "daily" } });
out.pendingTtlAfterUpdate = ttlOf(subKey());
await post({ action: "sync", token, saved: [], removed: {} });
out.pendingTtlAfterSync = ttlOf(subKey());

/* 3. Signing up again within the hour sends nothing. */
await post({ action: "subscribe", email: "pat@example.org", prefs: PREFS });
out.mailsAfterPendingRepeat = sent.length;

/* 4. Confirming makes both keys permanent. */
out.confirm = await post({ action: "confirm", token });
out.confirmedSubTtl = ttlOf(subKey());
out.confirmedEmTtl = ttlOf(emKey());

/* 5. An already-subscribed address: one settings mail, then a cooldown. */
const before = sent.length;
for (let i = 0; i < 25; i++) {
  out.repeatAnswer = await post({ action: "subscribe", email: "pat@example.org", prefs: PREFS });
}
out.settingsMailsFrom25Requests = sent.length - before;
out.confirmedTtlAfterRepeat = ttlOf(subKey());
clock += 3601 * 1000;
await post({ action: "subscribe", email: "pat@example.org", prefs: PREFS });
out.settingsMailsAfterAnHour = sent.length - before;

/* 6. The answer never varies with the address's state. */
out.freshAnswer = (await post({ action: "subscribe", email: "lee@example.org", prefs: PREFS })).body;

/* 7. Unsubscribing deletes both keys. */
await post({ action: "stop", token });
out.afterStop = [...KV.keys()].filter((k) => KV.get(k).v === token || k === "sub:" + token).length;

/* 8. ONE CALLER, MANY ADDRESSES: the per-caller allowance. */
const mailsBefore = sent.length;
const capped = [];
for (let i = 0; i < 15; i++) {
  capped.push((await post({ action: "subscribe", email: `n${i}@example.org`, prefs: PREFS },
                          "203.0.113.50")).status);
}
out.oneCallerAccepted = capped.filter((c) => c === 200).length;
out.oneCallerRefused = capped.filter((c) => c === 429).length;
out.oneCallerMails = sent.length - mailsBefore;

out.writesWithoutTtlWhilePending = writes
  .filter((w) => w.k === "sub:" + token)
  .slice(0, 3)                                   // signup, update, sync
  .filter((w) => !w.ttl).length;

console.log(JSON.stringify(out));
