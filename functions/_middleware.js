/* Give every address its own title, description and share card.
 *
 * The site is one index.html, so every url on it shared ONE <title> and had no
 * description and no card at all. A role dropped into a Slack channel or a
 * LinkedIn comment unfurled as a naked link that told nobody what it was, and
 * a crawler saw the same page whatever it asked for. That is not a small
 * cosmetic gap: it is the difference between 2,113 company records being
 * findable and being invisible.
 *
 * HTMLRewriter streams, so this rewrites the head as the page flows past
 * without buffering it. The body is untouched - the app still renders
 * everything client-side exactly as before, and a visitor with JavaScript sees
 * no difference. This is for the readers that never run it.
 *
 * WHAT IT WILL NOT DO. It never invents. A posting whose office we do not know
 * gets no place in its description, a company with nothing open gets no count,
 * and an id that names nothing falls through to the site defaults rather than
 * writing a title about a thing that does not exist. The index it reads is
 * built by build_site.py from the same board.json the page reads, so the two
 * can never disagree about what a role is called.
 */
import { SITE, NAME } from "./_brand.js";

const TAGLINE = "Every open sales role at state and local government technology companies.";

/* One fetch per edge per deploy rather than one per visitor. The index is a
 * static asset on our own origin, so this is an internal hop, and the board is
 * rebuilt daily - an hour of staleness costs a title that names yesterday's
 * count, which is why the count is not in the title. */
async function index(env, request, which) {
  const url = new URL(request.url);
  url.pathname = `/meta-${which}.json`;
  url.search = "";
  const res = await fetch(url.toString(), {
    cf: { cacheTtl: 3600, cacheEverything: true },
  });
  if (!res.ok) return null;
  try { return await res.json(); } catch { return null; }
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* What this address is, or null to leave the defaults alone. */
async function describe(request, env) {
  const u = new URL(request.url);
  const role = u.searchParams.get("role");
  const co = u.searchParams.get("co");
  const tab = u.searchParams.get("tab");

  if (role) {
    const idx = await index(env, request, "roles");
    const r = idx && idx.roles && idx.roles[role];
    if (!r) return null;
    const where = r.w ? ` in ${r.w}` : "";
    return {
      title: `${r.t} at ${r.c} · ${NAME}`,
      desc: `${r.c} is hiring a ${r.t}${where}. Found on ${NAME}, which tracks `
          + `sales roles at state and local government technology companies.`,
      canonical: `${SITE}/?role=${encodeURIComponent(role)}`,
      image: `${SITE}/assets/og/jobs.png`,
    };
  }

  if (co) {
    const idx = await index(env, request, "companies");
    const c = idx && idx.companies && idx.companies[co];
    if (!c) return null;
    // The count is a fact about today and the card may be cached for longer
    // than today, so it is said only when there is something to say and never
    // as a number in the title.
    const open = c.r ? ` ${c.r} open sales role${c.r === 1 ? "" : "s"} right now.` : "";
    return {
      title: `${c.n} · ${NAME}`,
      desc: (c.d ? c.d.replace(/\s+/g, " ").trim() + "." : `${c.n} sells into ${c.s || "state and local government"}.`)
          + open,
      canonical: `${SITE}/?co=${encodeURIComponent(co)}`,
      image: `${SITE}/assets/og/companies.png`,
    };
  }

  const TABS = {
    jobs: ["Sales jobs in govtech", "Every open sales role at state and local government technology companies, in one list."],
    companies: ["Govtech companies", "The companies selling technology to state and local government, and which of them are hiring."],
    conferences: ["Govtech conferences", "Where these companies exhibit, with dates, so you know which floor to stand on."],
    market: ["Govtech market intel", "What the hiring across state and local government technology actually looks like."],
    alerts: ["Job alerts", "Get the new sales roles by email, on the days you choose, above the threshold you set."],
    saved: null,
  };
  if (tab && TABS[tab]) {
    const [t, d] = TABS[tab];
    return {
      title: `${t} · ${NAME}`, desc: d,
      canonical: `${SITE}/?tab=${encodeURIComponent(tab)}`,
      image: `${SITE}/assets/og/${tab}.png`,
    };
  }
  if (!role && !co && !tab && u.pathname === "/") {
    return {
      title: `${NAME} · ${TAGLINE}`, desc: TAGLINE,
      canonical: `${SITE}/`, image: `${SITE}/assets/og/home.png`,
    };
  }
  return null;
}

class Head {
  constructor(m) { this.m = m; this.done = false; }
  element(el) {
    if (this.done) return;
    this.done = true;
    const m = this.m;
    el.append(
      `<link rel="canonical" href="${esc(m.canonical)}">\n` +
      `<meta name="description" content="${esc(m.desc)}">\n` +
      `<meta property="og:type" content="website">\n` +
      `<meta property="og:site_name" content="${esc(NAME)}">\n` +
      `<meta property="og:title" content="${esc(m.title)}">\n` +
      `<meta property="og:description" content="${esc(m.desc)}">\n` +
      `<meta property="og:url" content="${esc(m.canonical)}">\n` +
      `<meta property="og:image" content="${esc(m.image)}">\n` +
      `<meta name="twitter:card" content="summary_large_image">\n` +
      `<meta name="twitter:title" content="${esc(m.title)}">\n` +
      `<meta name="twitter:description" content="${esc(m.desc)}">\n` +
      `<meta name="twitter:image" content="${esc(m.image)}">\n`,
      { html: true });
  }
}

class Title {
  constructor(m) { this.m = m; }
  element(el) { el.setInnerContent(this.m.title); }
}

export async function onRequest(context) {
  const { request, next } = context;
  const res = await next();
  const type = res.headers.get("content-type") || "";
  if (!type.includes("text/html")) return res;

  let meta = null;
  try { meta = await describe(request, context.env); }
  catch { meta = null; }          // a head-tag rewrite must never cost a page
  if (!meta) return res;

  return new HTMLRewriter()
    .on("title", new Title(meta))
    .on("head", new Head(meta))
    .transform(res);
}
