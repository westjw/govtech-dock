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
  // NULL MEANS "COULD NOT READ IT", and the caller must not confuse that with
  // "read it, and the thing is not in it". Those are the two facts this whole
  // repository exists to keep apart, and here the difference is 4,439 pages.
  if (!res.ok) return null;
  try { return await res.json(); } catch { return null; }
}

/* Own property only.
 *
 * `idx.roles[role]` walks the prototype chain, so ?role=constructor,
 * ?role=toString, ?role=valueOf and ?role=__proto__ all return a truthy
 * function, sail past the `!r` guard, and render
 * `<title>undefined at undefined - SLED JOBS</title>` with a self-canonical
 * and no noindex. An indexable page asserting a company that does not exist,
 * reachable from the address bar - "never invent a fact to fill a field",
 * defeated by a URL. */
const own = (o, k) => (o && Object.hasOwn(o, k)) ? o[k] : undefined;

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
    // WE COULD NOT READ THE INDEX IS NOT THE ROLE IS GONE.
    //
    // index() returns null when the asset answers non-200 or unparseable JSON:
    // a deploy mid-flight, a WAF page, a 503. The gone-role branch below tests
    // `!r`, and with a null index EVERY role is falsy - so one failed fetch of
    // our own file would serve "That role is no longer listed" with noindex on
    // all 4,439 role pages at once, days after submitting every one of them to
    // Google.
    //
    // That is the asymmetric error at its largest available scale, and it is
    // the same shape as reporting a page we could not read as a company with
    // no jobs. When the index is unreadable we fall through to the site
    // defaults: no claim about this role either way, and nothing marked
    // noindex.
    if (!idx || !idx.roles) return null;
    let r = own(idx.roles, role);
    // A LINK SHARED BEFORE THE ID CHANGED IS NOT A ROLE THAT IS GONE.
    //
    // Posting ids gained a url+location hash on 2026-08-23, so every link
    // shared, saved or EMAILED before that date asks for a prefix of the id it
    // wants. index.html has resolved those by prefix since the day the ids
    // changed; this file did not, and once the gone-role branch existed those
    // urls started serving "That role is no longer listed" with noindex over a
    // page whose body was rendering the live role. digest.py built its links
    // from p["id"], so every alert email sent before 08-23 carries one.
    //
    // A neutral head became a false one, which is worse than the soft 404 the
    // branch was added to prevent.
    //
    // "<asked>::<anything>" and nothing looser, exactly as index.html has it: a
    // bare startsWith would answer a request for "acme::Account Executive"
    // with "acme::Account Executive Assistant", a different job at the same
    // company and indistinguishable from a correct answer.
    if (!r) {
      const pre = role + "::";
      for (const k of Object.keys(idx.roles)) {
        if (k.startsWith(pre)) { r = idx.roles[k]; break; }
      }
    }
    // A ROLE THAT IS GONE MUST NOT BE INDEXED, and this only started mattering
    // when the sitemap grew role pages.
    //
    // Until 2026-08-30 the sitemap listed 468 addresses and not one job page,
    // so nothing sent a crawler at ?role= and a stale id was a page almost
    // nobody reached. It now lists 4,439 of them, which is the point - the
    // JobPosting markup was live and undiscoverable - but it also means Google
    // will fetch every one, and postings come off this board by the hundred:
    // 141 in a single run last week.
    //
    // A gone role answers 200 with the app's generic title. That is a SOFT
    // 404, the shape search engines penalise a domain for, and it would arrive
    // at scale within days of the first crawl. `noindex` is the honest answer:
    // the page still renders and still says the role is no longer listed, it
    // simply stops claiming to be an indexable job posting.
    if (!r) {
      return {
        noindex: true,
        title: `That role is no longer listed \u00b7 ${NAME}`,
        desc: `This posting has come off ${NAME}. It may have been filled, or `
            + `the company's job board stopped listing it.`,
        canonical: `${SITE}/`,
        image: `${SITE}/assets/og/jobs.png`,
      };
    }
    const where = r.w ? ` in ${r.w}` : "";
    return {
      ld: r.ld ? jobLd(role, r, SITE) : null,
      title: `${r.t} at ${r.c} · ${NAME}`,
      desc: `${r.c} is hiring a ${r.t}${where}. Found on ${NAME}, which tracks `
          + `sales roles at state and local government technology companies.`,
      canonical: `${SITE}/?role=${encodeURIComponent(role)}`,
      image: `${SITE}/assets/og/jobs.png`,
    };
  }

  if (co) {
    const idx = await index(env, request, "companies");
    const c = own(idx && idx.companies, co);
    if (!c) return null;
    // The count is a fact about today and the card may be cached for longer
    // than today, so it is said only when there is something to say and never
    // as a number in the title.
    const open = c.r ? ` ${c.r} open sales role${c.r === 1 ? "" : "s"} right now.` : "";
    return {
      title: `${c.n} · ${NAME}`,
      desc: (c.d ? c.d.replace(/\s+/g, " ").trim() + "." : `${c.n} sells into ${c.s || "state and local government"}.`)
          + open,
      // A company with an opening has a prerendered page at /c/<id>.html with
      // the facts in its HTML. That is the canonical one, so the app view and
      // the static page do not compete for the same company. Companies with
      // nothing open have no static page, and ?co= is their only address.
      // Extensionless: Cloudflare 308s /c/<id>.html to /c/<id>, so the .html
      // form was a canonical pointing at a redirect back to the page that
      // declared it. See write_crawl_files.
      canonical: c.r ? `${SITE}/c/${encodeURIComponent(co)}`
                     : `${SITE}/?co=${encodeURIComponent(co)}`,
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

/* JobPosting structured data, on the roles we actually read.
 *
 * Google for Jobs is the one channel that sends high-intent traffic to a board
 * this size. It is also a channel that punishes a lie, so this is emitted only
 * for postings whose description was read (r.ld), never carries a validThrough
 * we do not know, and never claims a salary - the board holds a stated range
 * for some postings, but a range read out of prose is not the same claim as an
 * employer's structured baseSalary and should not be dressed as one.
 */
function jobLd(id, r, site) {
  const loc = r.ci || r.st
    ? { "@type": "Place",
        address: Object.assign({ "@type": "PostalAddress", addressCountry: "US" },
          r.ci ? { addressLocality: r.ci } : {},
          r.st ? { addressRegion: r.st } : {}) }
    : null;
  const o = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    title: r.t,
    description: `${r.c} is hiring a ${r.t}${r.w ? ` in ${r.w}` : ""}.`,
    hiringOrganization: { "@type": "Organization", name: r.c },
    url: `${site}/?role=${encodeURIComponent(id)}`,
    directApply: false,
  };
  // datePosted IS THE EMPLOYER'S OWN DATE, and only theirs. `pd` is written by
  // write_meta_index() from ats.posted_date, which reads whichever publish
  // field each of the seven structured boards actually sends - and returns
  // nothing where a board sends none, which is most of the web. Where it is
  // absent the field stays absent: it is optional in the spec, and a wrong one
  // is not.
  //
  // NEVER `r.d`. That was first_seen, the day WE first saw the row, and 2,183
  // of 3,524 blocks once claimed one of our first two crawl days as the day
  // the employer posted the job. A fact about our crawler dressed as a fact
  // about their hiring. The name is refused explicitly so a future producer
  // writing it back in does not silently reach this line.
  if (r.pd) o.datePosted = r.pd;
  if (loc) o.jobLocation = loc;
  // A JobPosting needs a place or an explicit statement that there is none.
  // `tc` is set only where the posting itself says remote - read verbatim, not
  // inferred - and build_site drops `ld` entirely for a role we cannot put
  // anywhere, so this block is never emitted without one of the two.
  else if (r.tc) o.jobLocationType = "TELECOMMUTE";
  return JSON.stringify(o);
}

class Ld {
  constructor(json) { this.json = json; this.done = false; }
  element(el) {
    if (this.done) return;
    this.done = true;
    el.append(`<script type="application/ld+json">${this.json.replace(/</g, "\\u003c")}<\/script>`,
              { html: true });
  }
}

class Head {
  constructor(m) { this.m = m; this.done = false; }
  element(el) {
    if (this.done) return;
    this.done = true;
    const m = this.m;
    el.append(
      (m.noindex ? `<meta name="robots" content="noindex,follow">\n` : "") +
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

  let rw = new HTMLRewriter()
    .on("title", new Title(meta))
    .on("head", new Head(meta));
  if (meta.ld) rw = rw.on("head", new Ld(meta.ld));
  return rw.transform(res);
}
