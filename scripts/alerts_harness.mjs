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
/* A KV THAT REALLY EXPIRES, on a clock this file moves: `expiration` is an
   absolute epoch second, `expirationTtl` is relative to the put, and a put
   with neither clears any expiry - the semantics Workers KV documents. The
   first version recorded the TTL and never expired anything, so it could not
   show two keys drifting apart (review, 2026-10-07). */
const KV = new Map();          // key -> {v, exp}
const writes = [];             // every put, in order: {k, exp}
const sent = [];
let clock = 1_800_000_000_000;
Date.now = () => clock;
const nowS = () => Math.floor(clock / 1000);
const DAY = 86400 * 1000;

const env = {
  RESEND_KEY: "test",
  ALERTS: {
    async get(k) {
      const e = KV.get(k);
      if (!e) return null;
      if (e.exp && nowS() >= e.exp) { KV.delete(k); return null; }
      return e.v;
    },
    async put(k, v, opts) {
      const exp = opts && opts.expiration ? opts.expiration
                : opts && opts.expirationTtl ? nowS() + opts.expirationTtl : null;
      KV.set(k, { v, exp });
      writes.push({ k, exp });
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
// seconds left before the key lapses, null for no expiry
const ttlOf = (k) => (k && KV.has(k) ? (KV.get(k).exp ? KV.get(k).exp - nowS() : null) : "missing");
const PREFS = { cadence: "weekly" };

/* 1. A new signup: both keys expire, one confirmation goes out. */
out.signup = await post({ action: "subscribe", email: "pat@example.org", prefs: PREFS });
out.pendingSubTtl = ttlOf(subKey());
out.pendingEmTtl = ttlOf(emKey());
out.mailsAfterSignup = sent.length;
const token = subKey().slice(4);

/* 3. Signing up again within the hour sends nothing. */
await post({ action: "subscribe", email: "pat@example.org", prefs: PREFS });
out.mailsAfterPendingRepeat = sent.length;

/* 2. Settings saved BEFORE confirming must not make the record permanent, nor
   renew it: two days later both keys have five days left, together. */
clock += 2 * DAY;
await post({ action: "update", token, prefs: { cadence: "daily" } });
out.pendingTtlAfterUpdate = ttlOf(subKey());
await post({ action: "sync", token, saved: [], removed: {} });
out.pendingTtlAfterSync = ttlOf(subKey());
out.emTtlAfterSync = ttlOf(emKey());

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
// once capped, an address on file and an unknown one get the same answer -
// the cap is counted before the lookup, or a capped caller learns which
// addresses are subscribed from which ones still answer 200
const capRes = async (email) => {
  const r = await post({ action: "subscribe", email, prefs: PREFS }, "203.0.113.50");
  return JSON.stringify([r.status, r.body]);
};
out.cappedKnown = await capRes("n0@example.org");
out.cappedUnknown = await capRes("never-seen@example.org");

/* 9. ONE MACHINE, A WHOLE IPv6 /64: every address in it is one caller. */
const v6Before = sent.length;
const v6 = [];
for (let i = 1; i <= 15; i++) {
  v6.push((await post({ action: "subscribe", email: `v${i}@example.org`, prefs: PREFS },
                      `2001:db8:1:2::${i.toString(16)}`)).status);
}
out.v6Accepted = v6.filter((c) => c === 200).length;
out.v6Mails = sent.length - v6Before;

/* 10. THE DRIFT: settings on day 5, a re-signup on day 7.5, and confirming
   both links must not leave one address with two subscriptions. */
const cnt = () => [...KV.keys()].filter((k) => k.startsWith("sub:")
  && JSON.parse(KV.get(k).v).email === "q@example.org").length;
await post({ action: "subscribe", email: "q@example.org", prefs: PREFS }, "192.0.2.77");
const tokA = [...KV.keys()].find((k) => k.startsWith("sub:") && JSON.parse(KV.get(k).v).email === "q@example.org").slice(4);
clock += 5 * DAY;
await post({ action: "sync", token: tokA, saved: [], removed: {} });
clock += 2.5 * DAY;
out.driftSubAlive = !!(await env.ALERTS.get("sub:" + tokA));
await post({ action: "subscribe", email: "q@example.org", prefs: PREFS }, "192.0.2.78");
const tokB = [...KV.keys()].find((k) => k.startsWith("sub:") && k !== "sub:" + tokA
  && JSON.parse(KV.get(k).v).email === "q@example.org");
out.confirmOldAfterExpiry = (await post({ action: "confirm", token: tokA })).status;
if (tokB) await post({ action: "confirm", token: tokB.slice(4) });
out.driftSubscriptions = cnt();

/* 11. CONFIRMING NEVER TAKES THE ADDRESS FROM ANOTHER SUBSCRIPTION. A stale
   pending record for an address that already has a confirmed one (written
   before the shared expiry, when the keys could drift) must not move em: to
   itself on confirm - the confirmed subscription would lose its address key
   and "stop" could no longer reach it. */
{
  const LIVE = "L".repeat(43), STALE = "S".repeat(43);
  const ek = "em:stale-fixture";
  KV.set("sub:" + LIVE, { v: JSON.stringify({ email: "r@example.org", confirmed: true, prefs: PREFS }), exp: null });
  KV.set(ek, { v: LIVE, exp: null });
  KV.set("sub:" + STALE, { v: JSON.stringify({ email: "r@example.org", confirmed: false, prefs: PREFS }), exp: null });
  const realKey = (await import("../functions/_mail.js")).emailKey;
  const k = await realKey("r@example.org");
  KV.set(k, KV.get(ek)); KV.delete(ek);
  await post({ action: "confirm", token: STALE });
  out.emAfterStaleConfirm = KV.has(k) ? (KV.get(k).v === LIVE ? "LIVE" : KV.get(k).v.slice(0, 4)) : null;
}

/* 12. ONE-CLICK UNSUBSCRIBE, as a mail client sends it (RFC 8058): a FORM
   POST to the List-Unsubscribe address, the token in the url. */
{
  await post({ action: "subscribe", email: "u@example.org", prefs: PREFS }, "192.0.2.90");
  const tok = [...KV.keys()].find((k) => k.startsWith("sub:") && JSON.parse(KV.get(k).v).email === "u@example.org").slice(4);
  await post({ action: "confirm", token: tok });
  const form = async (body, t) => (await mod.onRequestPost({
    request: new Request(`https://sledjobs.com/api/alerts?t=${t}`, { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" }, body }), env })).status;
  out.oneClickWrongBody = await form("List-Unsubscribe=Nope", tok);
  out.oneClickKeptAfterWrongBody = !!(await env.ALERTS.get("sub:" + tok));
  out.oneClick = await form("List-Unsubscribe=One-Click", tok);
  out.oneClickGone = !(await env.ALERTS.get("sub:" + tok))
    && ![...KV.values()].some((e) => e.v === tok);
}

out.writesWithoutTtlWhilePending = writes
  .filter((w) => w.k === "sub:" + token)
  .slice(0, 3)                                   // signup, update, sync
  .filter((w) => !w.exp).length;

console.log(JSON.stringify(out));
