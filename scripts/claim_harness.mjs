/* Drive functions/api/claim.js for real: a fake KV, a fake mail sender, a
 * fake meta-companies.json, and the actual module imported rather than read.
 *
 * IMPORTED, NOT PARSED. The first version of claim.js used a /x regex flag,
 * which JavaScript does not have. `node --check` accepted the file and
 * `import` did not - the module failed to load at all, and a guard that read
 * the source would have called it healthy. Everything below runs the code.
 *
 * Prints one JSON object of results. selftest asserts on it.
 */
const out = {};
const KV = new Map();
const sent = [];

globalThis.__sent = sent;

const env = {
  RESEND_KEY: "test",
  ALERTS: {
    async get(k) { return KV.has(k) ? KV.get(k) : null; },
    async put(k, v) { KV.set(k, v); },
    async delete(k) { KV.delete(k); },
  },
};

/* meta-companies.json, and the mail API, both answered here. */
const COMPANIES = {
  companies: {
    acme: { n: "Acme", s: "Public Safety", w: "acme.com" },
    "no-site": { n: "No Site", s: "Public Safety", w: "" },
    "co-uk": { n: "Brit Co", s: "Public Safety", w: "britco.co.uk" },
  },
};
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.includes("meta-companies.json")) {
    return { ok: true, json: async () => COMPANIES };
  }
  if (u.includes("api.resend.com")) {
    sent.push(u);
    return { ok: true };
  }
  return { ok: false, json: async () => ({}) };
};

const mod = await import("../functions/api/claim.js");

const post = (body) =>
  mod.onRequestPost({
    request: { json: async () => body, url: "https://sledjobs.com/api/claim" },
    env,
  });
const get = (t) =>
  mod.onRequestGet({ request: { url: "https://x/api/claim?t=" + t }, env });
const read = async (res) => ({ status: res.status, body: await res.json() });

/* --- claiming ----------------------------------------------------------- */
out.freeMail = await read(await post(
  { action: "claim", company_id: "acme", email: "jane@gmail.com" }));
out.platform = await read(await post(
  { action: "claim", company_id: "acme", email: "jane@thing.wixsite.com" }));
out.wrongDomain = await read(await post(
  { action: "claim", company_id: "acme", email: "jane@other.com" }));
out.noWebsite = await read(await post(
  { action: "claim", company_id: "no-site", email: "jane@nosite.com" }));
out.unknownCo = await read(await post(
  { action: "claim", company_id: "nobody", email: "jane@nobody.com" }));
out.good = await read(await post(
  { action: "claim", company_id: "acme", email: "Jane@Acme.com" }));
out.mailsSent = sent.length;
/* a subdomain address is still the same registrable name */
KV.delete([...KV.keys()].find((k) => k.startsWith("claimem:")) || "x");
out.subdomain = await read(await post(
  { action: "claim", company_id: "acme", email: "jane@mail.acme.com" }));
/* co.uk is a PUBLIC SUFFIX, and collapsing to it would let any British
   company claim any other. Both directions: the real one works, a different
   .co.uk does not. */
out.coUk = await read(await post(
  { action: "claim", company_id: "co-uk", email: "jane@britco.co.uk" }));
out.coUkStranger = await read(await post(
  { action: "claim", company_id: "co-uk", email: "jane@someoneelse.co.uk" }));

const token = [...KV.keys()].filter((k) => k.startsWith("claim:"))[0].slice(6);

/* --- reading the token -------------------------------------------------- */
out.beforeConfirm = await read(await get(token));
out.confirm = await read(await post({ action: "confirm", token }));
out.afterConfirm = await read(await get(token));

/* --- proposing ---------------------------------------------------------- */
out.competitors = await read(await post(
  { action: "propose", token, kind: "competitors", note: "drop them" }));
out.description = await read(await post(
  { action: "propose", token, kind: "description",
    description: "Acme sells dispatch software to police departments." }));
out.category = await read(await post(
  { action: "propose", token, kind: "category", wants: "Public Safety / Police",
    why: "our buyers are police chiefs and we sell CAD" }));
out.categoryNoWhy = await read(await post(
  { action: "propose", token, kind: "category", wants: "Police", why: "" }));
out.jobOffDomain = await read(await post(
  { action: "propose", token, kind: "job", title: "Account Executive",
    url: "https://randomjobsite.example/acme/ae" }));
out.jobOwnDomain = await read(await post(
  { action: "propose", token, kind: "job", title: "Account Executive",
    location: "Austin, TX", url: "https://acme.com/careers/ae" }));
out.jobAts = await read(await post(
  { action: "propose", token, kind: "job", title: "Sales Engineer",
    url: "https://boards.greenhouse.io/acme/jobs/1" }));
out.unknownKind = await read(await post(
  { action: "propose", token, kind: "delete_company" }));

/* what actually landed in KV, so selftest can check nothing leaks */
out.propKeys = [...KV.keys()].filter((k) => k.startsWith("claimprop:")).length;
out.propSample = JSON.parse(
  KV.get([...KV.keys()].find((k) => k.startsWith("claimprop:"))));

/* an unconfirmed token may not propose */
const t2raw = JSON.parse(KV.get("claim:" + token));
const T2 = "u".repeat(43);          // a VALID token shape, so cleanToken passes
KV.set("claim:" + T2, JSON.stringify(Object.assign({}, t2raw, { confirmed: false })));
const beforeUnconfirmed = [...KV.keys()].filter((k) => k.startsWith("claimprop:")).length;
out.unconfirmed = await read(await post(
  { action: "propose", token: T2, kind: "description",
    description: "should not be stored at all, ever, no." }));
out.unconfirmedStoredNothing =
  [...KV.keys()].filter((k) => k.startsWith("claimprop:")).length === beforeUnconfirmed;

/* --- releasing ---------------------------------------------------------- */
out.release = await read(await post({ action: "release", token }));
out.afterRelease = await read(await get(token));

console.log(JSON.stringify(out));
