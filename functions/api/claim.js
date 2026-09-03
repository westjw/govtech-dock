/* Claiming a company page.
 *
 * WHAT A CLAIM IS. Somebody at the company proves they can read mail at the
 * company's own domain, and in exchange the page says so and they can send
 * us corrections. That is the whole of it. There is no account, no password
 * and no login: the subscription token IS the identity, exactly as alerts.js
 * decided, and this project should never grow a password store.
 *
 * WHAT A CLAIM IS NOT. It is not edit access to the map. Everything a
 * claimant sends is a PROPOSAL that lands in the admin queue beside the
 * evidence, and a person accepts it - the same deal an outside submission
 * gets, for the same reason: the cost of a wrong fact on a public market map
 * is paid by everyone reading it. A company that could rewrite its own entry
 * unreviewed would be a company that could rewrite its competitors' context.
 *
 * THREE THINGS A CLAIMANT MAY NEVER TOUCH, and they are refused here rather
 * than filtered later:
 *   - competitors. Who a buyer shortlists against you is not yours to edit,
 *     and a market map where vendors curate their own rivals is worthless.
 *   - their category. They may REQUEST one; the taxonomy belongs to the
 *     board, because a company filing itself into a busier shelf is the
 *     oldest trick in directory listings.
 *   - anything about another company.
 *
 * PHASE 1 IS AN EXACT DOMAIN MATCH. jane@acme.com claims acme.com and
 * nothing else. Subsidiaries, a mail domain that differs from the website,
 * and acquired brands are all real and all judgment calls, so they get a
 * link that files a submission for a person rather than a rule that guesses.
 */
import { json, mintToken, emailKey, validEmail, cleanToken, send, button, shell,
         SITE, NAME } from "../_mail.js";

/* Addresses that prove nothing. A gmail address is not evidence of working
 * anywhere, and a site builder's domain is shared by everyone on it. */
const FREE_MAIL = new Set([
  "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
  "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com",
  "proton.me", "protonmail.com", "pm.me", "gmx.com", "mail.com", "zoho.com",
  "hey.com", "fastmail.com", "msn.com", "comcast.net", "verizon.net",
]);
/* JavaScript HAS NO /x FLAG. This was first written multi-line with one,
 * which `node --check` accepts and `import` does not - the module failed to
 * load at all. Kept on one line, and the guard imports rather than checks. */
const PLATFORM = /(^|\.)(wixsite|squarespace|weebly|godaddysites|sites\.google|wordpress|blogspot|myshopify|webflow\.io|netlify\.app|vercel\.app|github\.io|linkedin|facebook)\./;

/* What a claimant may send. `competitors` is deliberately absent and is
 * refused BY NAME below, because a silent drop would let somebody believe
 * they had edited it. */
const KINDS = new Set(["description", "profile", "job", "category", "contact"]);

const CAP = { description: 300, profile: 1600, note: 600, title: 120, location: 90 };
const MAX_PER_COMPANY = 3;          // three people at one company is a team
const MAIL_PER_DAY = 5;             // per company, so a claim page is not a mailer
const COOLDOWN_MS = 60 * 60 * 1000; // per address

const today = () => new Date().toISOString().slice(0, 10);

/* The registrable name, well enough for a comparison against a host we
 * published ourselves. Not a public-suffix list: this only ever compares two
 * strings that both came from the same place, and a wrong answer refuses a
 * claim rather than granting one. */
function registrable(host) {
  const h = String(host || "").toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(h)) return "";
  const p = h.split(".");
  if (p.length <= 2) return h;
  // co.uk, com.au, org.nz and friends
  if (/^(co|com|org|net|gov|ac|edu)\.[a-z]{2}$/.test(p.slice(-2).join("."))) {
    return p.slice(-3).join(".");
  }
  return p.slice(-2).join(".");
}

const mask = (e) => {
  const [u, d] = String(e || "").split("@");
  if (!u || !d) return "";
  return (u.length <= 2 ? u[0] + "*" : u.slice(0, 2) + "*".repeat(Math.min(6, u.length - 2)))
    + "@" + d;
};

const clip = (v, n) => String(v == null ? "" : v).trim().slice(0, n);

async function companyFrom(request, id) {
  const res = await fetch(new URL("/meta-companies.json", request.url));
  if (!res.ok) return null;
  const all = await res.json();
  const c = (all.companies || {})[id];
  return c ? { id, name: c.n, host: c.w || "", sector: c.s } : null;
}

/* --- read: what this token is ------------------------------------------- */

export async function onRequestGet({ request, env }) {
  if (!env.ALERTS) return json({ error: "not_configured" }, 501);
  const url = new URL(request.url);
  const token = cleanToken(url.searchParams.get("t"));
  if (!token) return json({ error: "bad_token" }, 400);
  const raw = await env.ALERTS.get("claim:" + token);
  if (!raw) return json({ error: "bad_token" }, 400);
  const rec = JSON.parse(raw);
  return json({
    ok: true,
    company_id: rec.company_id,
    name: rec.name,
    email: mask(rec.email),              // never the address itself
    confirmed: !!rec.confirmed,
    created: rec.created,
    may_edit: ["description", "profile", "job", "contact"],
    may_request: ["category"],
    may_not_edit: ["competitors"],
    proposals: rec.proposals || 0,
  });
}

/* --- write -------------------------------------------------------------- */

export async function onRequestPost({ request, env }) {
  if (!env.ALERTS) return json({ error: "not_configured" }, 501);
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ error: "send JSON" }, 400);
  }
  const action = String(body.action || "");
  if (action === "claim") return startClaim(body, env, request);
  if (action === "confirm") return confirmClaim(body, env);
  if (action === "propose") return propose(body, env);
  if (action === "release") return release(body, env);
  return json({ error: "unknown action" }, 400);
}

async function startClaim(body, env, request) {
  const id = String(body.company_id || "").trim().toLowerCase();
  const email = validEmail(body.email);
  if (!/^[a-z0-9][a-z0-9-]{0,80}$/.test(id)) return json({ error: "bad company" }, 400);
  if (!email) return json({ error: "That does not look like an email address." }, 400);

  const co = await companyFrom(request, id);
  if (!co) return json({ error: "We do not have that company on file." }, 404);
  if (!co.host) {
    return json({
      error: "no_website",
      message: `We have no website on file for ${co.name}, so there is no domain to `
             + `check an address against. Send us the website first and we will `
             + `come back to this.`,
    }, 400);
  }

  const dom = registrable(email.split("@")[1]);
  const want = registrable(co.host);
  if (FREE_MAIL.has(dom) || PLATFORM.test("." + dom + ".")) {
    return json({
      error: "not_a_company_address",
      message: `A ${dom} address does not show you work at ${co.name}. `
             + `Use an address at ${want}.`,
    }, 400);
  }
  /* THE MISMATCH REPLY NAMES THE DOMAIN, and that is deliberate. It is a
   * public fact about a public website, printed on the company's own page.
   * Every OTHER outcome answers identically, so this endpoint never becomes
   * an oracle for "does this person have an account here". */
  if (dom !== want) {
    return json({
      error: "wrong_domain",
      message: `Claiming ${co.name} needs an address at ${want}.`,
      differs: `${SITE}/?tab=submit&co=${encodeURIComponent(id)}`,
    }, 400);
  }

  // rate limits, per address and per company
  const ek = await emailKey(email);
  const seenRaw = await env.ALERTS.get("claimem:" + ek);
  if (seenRaw) {
    const seen = JSON.parse(seenRaw);
    if (Date.now() - (seen.at || 0) < COOLDOWN_MS) {
      return json({ ok: true, check_your_email: true });   // silent, not an oracle
    }
  }
  const dayKey = `claimrl:${id}:${today()}`;
  const sent = Number((await env.ALERTS.get(dayKey)) || 0);
  if (sent >= MAIL_PER_DAY) return json({ ok: true, check_your_email: true });

  const held = JSON.parse((await env.ALERTS.get("claimco:" + id)) || "[]");
  if (held.length >= MAX_PER_COMPANY) {
    return json({
      error: "already_claimed",
      message: `${co.name} already has the most people we allow on one page. `
             + `Ask one of them to release it, or write to us.`,
    }, 409);
  }

  const token = mintToken();
  const rec = { company_id: id, name: co.name, email, domain: want,
                confirmed: false, created: new Date().toISOString(), proposals: 0 };
  await env.ALERTS.put("claim:" + token, JSON.stringify(rec),
                       { expirationTtl: 60 * 60 * 24 * 30 });
  await env.ALERTS.put("claimem:" + ek, JSON.stringify({ at: Date.now(), token }),
                       { expirationTtl: 60 * 60 * 24 * 2 });
  await env.ALERTS.put(dayKey, String(sent + 1), { expirationTtl: 60 * 60 * 26 });

  const link = `${SITE}/claim?t=${token}`;
  const ok = await send(env, email, `Claim ${co.name} on ${NAME}`,
    `Confirm you can read mail at ${want} to claim ${co.name}: ${link}`,
    shell(`Confirm your claim on ${co.name}`,
      `<p style="margin:0 0 14px">Somebody asked to claim <strong>${co.name}</strong>
       on ${NAME} using this address. If that was you, confirm below.</p>
       <p style="margin:0 0 18px">Claiming lets you send corrections to the page and
       post your open roles. Every change is reviewed by a person before it appears,
       and you can hand the claim back at any time.</p>
       ${button(link, "Confirm the claim")}
       <p style="margin:18px 0 0;font-size:13px;color:#556F82">If this was not you,
       ignore this email and nothing happens.</p>`,
      [["Their page", `${SITE}/c/${id}`], [NAME, SITE]]));
  if (!ok) return json({ error: "We could not send that email just now." }, 502);
  return json({ ok: true, check_your_email: true });
}

async function confirmClaim(body, env) {
  const token = cleanToken(body.token);
  if (!token) return json({ error: "bad_token" }, 400);
  const raw = await env.ALERTS.get("claim:" + token);
  if (!raw) return json({ error: "bad_token" }, 400);
  const rec = JSON.parse(raw);
  if (!rec.confirmed) {
    rec.confirmed = true;
    rec.confirmed_at = new Date().toISOString();
    await env.ALERTS.put("claim:" + token, JSON.stringify(rec));
    const held = JSON.parse((await env.ALERTS.get("claimco:" + rec.company_id)) || "[]");
    if (!held.includes(token)) {
      held.push(token);
      await env.ALERTS.put("claimco:" + rec.company_id, JSON.stringify(held));
    }
  }
  return json({ ok: true, company_id: rec.company_id, name: rec.name });
}

async function propose(body, env) {
  const token = cleanToken(body.token);
  if (!token) return json({ error: "bad_token" }, 400);
  const raw = await env.ALERTS.get("claim:" + token);
  if (!raw) return json({ error: "bad_token" }, 400);
  const rec = JSON.parse(raw);
  if (!rec.confirmed) return json({ error: "confirm your email first" }, 403);

  const kind = String(body.kind || "");
  /* REFUSED BY NAME. A silent drop would let somebody believe they had
   * edited their competitors, and believing it is worse than being told no. */
  if (kind === "competitors") {
    return json({
      error: "not_editable",
      message: "Who a buyer shortlists against you is not something a company can "
             + "edit here. If a competitor on your page is wrong, tell us why and "
             + "a person will look at it.",
    }, 400);
  }
  if (!KINDS.has(kind)) return json({ error: "unknown kind" }, 400);

  const p = { kind, at: new Date().toISOString(), by_domain: rec.domain };
  if (kind === "description") {
    p.description = clip(body.description, CAP.description);
    if (p.description.length < 20) return json({ error: "too short" }, 400);
  } else if (kind === "profile") {
    const paras = (Array.isArray(body.paragraphs) ? body.paragraphs : [])
      .map((x) => clip(x, CAP.profile)).filter(Boolean).slice(0, 3);
    if (!paras.length) return json({ error: "write at least one paragraph" }, 400);
    if (paras.join(" ").length > CAP.profile) return json({ error: "too long" }, 400);
    p.paragraphs = paras;
  } else if (kind === "job") {
    p.title = clip(body.title, CAP.title);
    p.location = clip(body.location, CAP.location);
    p.url = clip(body.url, 400);
    if (p.title.length < 3) return json({ error: "a job needs a title" }, 400);
    if (!/^https?:\/\//.test(p.url)) return json({ error: "a job needs a link" }, 400);
    /* ON THEIR OWN DOMAIN OR AN ATS. A claimant linking anywhere else is a
     * claimant advertising somewhere we cannot check is theirs. */
    const h = registrable(new URL(p.url).hostname);
    const atsish = /(greenhouse|lever|ashbyhq|workable|smartrecruiters|bamboohr|myworkdayjobs|icims|rippling|breezy|recruitee|applytojob|paylocity|jobvite|jazzhr)\./.test(new URL(p.url).hostname);
    if (h !== rec.domain && !atsish) {
      return json({
        error: "off_domain",
        message: `Link the posting on ${rec.domain} or on your applicant tracking `
               + `system. We cannot show a job we cannot check is yours.`,
      }, 400);
    }
  } else if (kind === "category") {
    /* A REQUEST, NOT AN EDIT. The taxonomy belongs to the board: a company
     * filing itself onto a busier shelf is the oldest trick in directory
     * listings, and a shelf is only worth something because a reader
     * browsing it expected to find what is on it. */
    p.wants = clip(body.wants, 120);
    p.why = clip(body.why, CAP.note);
    if (!p.wants) return json({ error: "which category?" }, 400);
    if (p.why.length < 10) {
      return json({ error: "say why it belongs there - a person reads this" }, 400);
    }
  } else if (kind === "contact") {
    p.note = clip(body.note, CAP.note);
    if (p.note.length < 5) return json({ error: "say something" }, 400);
  }

  /* A TIMESTAMP IS NOT A KEY. Three proposals sent in one click - a
   * description, a category request and a job - all landed on the same
   * millisecond and overwrote each other, so two of the three vanished
   * silently. The token tail plus randomness makes the key unique per
   * proposal, and the timestamp stays in front so the listing sorts. */
  const key = `claimprop:${rec.company_id}:${Date.now()}:${mintToken().slice(0, 8)}`;
  await env.ALERTS.put(key, JSON.stringify(Object.assign(p, {
    company_id: rec.company_id, token_tail: token.slice(-6),
  })), { expirationTtl: 60 * 60 * 24 * 90 });
  rec.proposals = (rec.proposals || 0) + 1;
  await env.ALERTS.put("claim:" + token, JSON.stringify(rec));
  return json({ ok: true, queued: true, kind,
                message: kind === "category"
                  ? "Sent. A person reads category requests; we will not move a "
                    + "company onto a shelf because it asked."
                  : "Sent. A person reviews every change before it appears." });
}

async function release(body, env) {
  const token = cleanToken(body.token);
  if (!token) return json({ error: "bad_token" }, 400);
  const raw = await env.ALERTS.get("claim:" + token);
  if (!raw) return json({ ok: true });          // already gone
  const rec = JSON.parse(raw);
  const held = JSON.parse((await env.ALERTS.get("claimco:" + rec.company_id)) || "[]");
  const left = held.filter((t) => t !== token);
  await env.ALERTS.put("claimco:" + rec.company_id, JSON.stringify(left));
  await env.ALERTS.delete("claim:" + token);
  return json({ ok: true, released: true });
}
