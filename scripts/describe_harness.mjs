/* WHAT EACH SHARED ADDRESS IS CALLED, driven through the real describe().
 *
 * functions/_middleware.js titles every page and names its canonical from
 * the address alone. This imports it, answers its meta-*.json reads from
 * fixtures through a fake ASSETS binding, and prints {url: {title,
 * canonical}} for selftest.py to assert on. */
const { describe } = await import(new URL("../functions/_middleware.js", import.meta.url));

const META = {
  "/meta-events.json": { events: { "APCO 2026": { n: "APCO 2026", p: "apco-2026",
                                                  l: "5 of the 32 exhibitors we track here are hiring a seller" } } },
  "/meta-companies.json": { companies: {} },
  "/meta-roles.json": { roles: {} },
};
const env = { ASSETS: { fetch: async (u) => {
  const body = META[new URL(u).pathname];
  return body ? new Response(JSON.stringify(body), { status: 200 })
              : new Response("nope", { status: 404 });
} } };

const urls = ["/", "/?e=APCO%202026", "/?e=Nowhere%202026", "/?us=us",
              "/?st=TX&us=us", "/?tab=companies&csec=Public%20Safety&call=1",
              "/?csec=Public%20Safety", "/?utm_source=x", "/?tab=conferences"];
const out = {};
for (const path of urls) {
  const d = await describe(new Request(`https://sledjobs.com${path}`), env);
  out[path] = d ? { title: d.title, canonical: d.canonical } : null;
}
console.log(JSON.stringify(out));
