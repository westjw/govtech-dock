/* Alert subscriptions and saved-role sync.
 *
 * WHAT IS STORED, AND WHY IT IS SO LITTLE
 *
 * An email address, the settings that address chose, and a list of role ids
 * it saved. No name, no password, no profile, no analytics, no IP log. This
 * is a board people use to look for a job quietly from their current desk;
 * the less it knows, the less there is to leak, subpoena, or regret. The
 * repository is public, so none of this can live in it - it lives in a
 * Cloudflare KV namespace bound as ALERTS and nowhere else.
 *
 * THE IDENTITY MODEL: no accounts, no passwords
 *
 * A subscription IS the account. Signing up mints one long random token,
 * mailed to the address; possession of that token is the whole proof of
 * identity, and it also carries the saved-role sync. So there is no password
 * to handle or breach, no login form to phish, nothing to reset, and
 * unsubscribing genuinely deletes the record rather than flagging it.
 *
 * The cost of that trade, stated honestly: anyone holding the link holds the
 * subscription. That is the same exposure as every newsletter preferences
 * link, it grants reading your own saved roles and changing your own email
 * settings, and it is revocable from the same page. Worth it to avoid
 * standing up a password store for a job board.
 *
 * DOUBLE OPT-IN IS NOT OPTIONAL
 *
 * Nothing is ever mailed to an address that has not clicked its own
 * confirmation link. Otherwise this endpoint is a machine for sending mail
 * to strangers under someone else's name.
 *
 * Setup (one time): create a KV namespace, bind it to the Pages project as
 * ALERTS, and add RESEND_KEY (an API key from a transactional mail provider)
 * as an encrypted variable. Without either, this endpoint reports that it is
 * not configured - it never pretends a signup landed.
 */
import { SITE, FROM } from "../_brand.js";
const MAX_SAVED = 500;          // a person's shortlist, not a scrape target
const MAX_ID = 300;
const CONFIRM_COOLDOWN = 3600;  // seconds between confirmation mails per address

const CADENCES = new Set(["daily", "twice", "weekly"]);
/* These four sets are the SAME vocabulary scripts/roles.py assigns and
 * index.html filters on. They are duplicated here because a Worker cannot
 * import Python, which makes them the one thing in this file that can drift.
 * When they drift the failure is silent and total: a subscriber picks a value
 * this file accepts, no posting ever carries it, and their alert simply never
 * arrives. scripts/selftest.py asserts these lists against roles.py so that
 * cannot happen quietly. */
const FAMILIES = new Set(["gtm", "cs", "ops", "engineering", "product", "data",
                          "policy", "ga", "exec", "field", "other"]);
const SENIORITY = new Set(["junior", "mid", "senior", "leadership"]);
const MODES = new Set(["remote", "hybrid", "onsite"]);
/* Real postal codes only. "ZZ" passes a two-letter regex, stores cleanly, and
 * then matches nothing forever - the subscriber gets silence and no reason for
 * it. Better to drop it at the door. */
const STATES = new Set(("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY "
  + "LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD "
  + "TN TX UT VT VA WA WV WI WY PR VI GU AS MP").split(" "));

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    },
  });

const notConfigured = () =>
  json({ error: "not_configured",
         message: "Alerts are not switched on yet." }, 501);

/* --- small helpers ------------------------------------------------------ */

function mintToken() {
  const b = new Uint8Array(32);
  crypto.getRandomValues(b);
  return btoa(String.fromCharCode(...b))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function emailKey(email) {
  // The address is keyed by hash so a dump of key names is not a mailing
  // list. The value behind it still holds the address - it has to, to send
  // mail - but the index does not hand one over for free.
  const buf = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(email.toLowerCase()));
  return "em:" + [...new Uint8Array(buf)]
    .map((x) => x.toString(16).padStart(2, "0")).join("");
}

/* Deliberately conservative rather than RFC-complete: this address is going
 * to be handed to a mail API, so anything exotic is likelier to be an attempt
 * at header injection than a real mailbox. */
function validEmail(raw) {
  const e = String(raw || "").trim().toLowerCase();
  if (e.length < 6 || e.length > 254) return null;
  if (/[\s<>",;\\()\[\]]/.test(e)) return null;
  if (!/^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(e)) return null;
  if (e.includes("..")) return null;
  return e;
}

/* The role bar. Anything unrecognised is dropped rather than stored: a
 * subscriber record is read later by a sender that trusts its own store. */
function cleanPrefs(raw) {
  const p = raw && typeof raw === "object" ? raw : {};
  const states = Array.isArray(p.states)
    ? [...new Set(p.states.map((s) => String(s).toUpperCase().slice(0, 2))
        .filter((s) => STATES.has(s)))].slice(0, 12)
    : [];
  const n = Number(p.min_count);
  return {
    cadence: CADENCES.has(p.cadence) ? p.cadence : "weekly",
    quota_only: p.quota_only === true,
    family: FAMILIES.has(p.family) ? p.family : null,
    seniority: SENIORITY.has(p.seniority) ? p.seniority : null,
    sector: typeof p.sector === "string" ? p.sector.slice(0, 60) : null,
    work_mode: MODES.has(p.work_mode) ? p.work_mode : null,
    states,
    us_only: p.us_only === true,
    min_count: Number.isFinite(n) ? Math.min(Math.max(Math.round(n), 1), 50) : 1,
  };
}

/* Saved roles carry their own snapshot, not just an id.
 *
 * Ids alone would be smaller, but a role saved on the laptop and opened on
 * the phone a week later would render as a blank row: the posting has come
 * down and no device can look it up any more. The snapshot costs nothing in
 * privacy that the id does not already cost - "this person kept this job" is
 * the sensitive fact, and the id is that fact - so it is stored.
 *
 * Only these fields, at these lengths. The client is not trusted to decide
 * what a saved role contains. */
/* saved_at is not decoration: it is the timestamp a tombstone is compared
 * against. Without it the comparison falls back to saved_on, which is a DATE,
 * and re-saving a role on the same day you unsaved it loses to its own
 * tombstone - the role silently disappears again on the next sync. */
const SAVED_FIELDS = ["id", "title", "company", "company_id", "url", "sector",
                      "category", "family", "seniority", "saved_on", "saved_at"];

function cleanSaved(raw) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    if (!item || typeof item !== "object" || typeof item.id !== "string") continue;
    const id = item.id.slice(0, MAX_ID);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const row = { id };
    for (const f of SAVED_FIELDS) {
      if (f !== "id" && typeof item[f] === "string") row[f] = item[f].slice(0, 200);
    }
    if (item.quota_carrying === true) row.quota_carrying = true;
    out.push(row);
    if (out.length >= MAX_SAVED) break;
  }
  return out;
}

/* Tombstones: id -> when it was unsaved.
 *
 * Without these, sync only ever adds. Unsave a role on the laptop, open the
 * phone - which still holds it - and the next sync puts it back, forever.
 * Union merges are only safe for sets nobody removes from.
 *
 * They are pruned after 90 days: by then every device has long since seen the
 * removal, and a tombstone list that only grows is a slow leak of exactly the
 * data unsaving was supposed to get rid of. */
const TOMBSTONE_DAYS = 90;

function cleanRemoved(raw, prior) {
  const merged = { ...(prior && typeof prior === "object" ? prior : {}) };
  if (raw && typeof raw === "object") {
    for (const [id, when] of Object.entries(raw)) {
      if (typeof id !== "string" || typeof when !== "string") continue;
      const k = id.slice(0, MAX_ID);
      if (!merged[k] || when > merged[k]) merged[k] = when.slice(0, 30);
    }
  }
  const cutoff = new Date(Date.now() - TOMBSTONE_DAYS * 86400000).toISOString();
  const out = {};
  for (const [id, when] of Object.entries(merged)) {
    if (when >= cutoff) out[id] = when;
  }
  // Bound it the same way saved is bounded, newest kept.
  const keys = Object.keys(out).sort((a, b) => out[b].localeCompare(out[a]))
    .slice(0, MAX_SAVED);
  return Object.fromEntries(keys.map((k) => [k, out[k]]));
}

/* A token from a URL, used only as a KV key. Constrain its shape so a crafted
 * one cannot reach for a neighbouring key. */
function cleanToken(raw) {
  const t = String(raw || "");
  return /^[A-Za-z0-9_-]{40,64}$/.test(t) ? t : null;
}

/* --- mail --------------------------------------------------------------- */

async function send(env, to, subject, text, html) {
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.RESEND_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ from: FROM, to: [to], subject, text, html }),
  });
  return res.ok;
}

/* --- the shared email shell --------------------------------------------
 *
 * Every email this project sends wears the site's own band: Penguin ground,
 * the mascot's face on a Belly plate, the Beak rule beneath. Same identity,
 * same reason index.html and alerts.html both restate it - there is no build
 * step, and this is the one fixed thing across every surface.
 *
 * THE DESIGN IS BUILT AROUND A BLOCKED IMAGE, not around a loaded one.
 * Outlook and much of Gmail suppress images by default, so everything that
 * NAMES the product is colour and type: the Penguin bgcolor, the wordmark as
 * live text, the Beak rule as a table cell, the Belly plate as a hard 52px
 * cell with a background. Only the face is a pixel, and it is the only thing
 * allowed to be optional. With images off the band keeps its full height and
 * every cue except the penguin.
 *
 * WHY THE MSO CONDITIONAL EXISTS. Outlook 2007-2016 renders through Word,
 * which does not walk a font stack: it takes the FIRST declared family and,
 * finding Archivo not installed, falls back to Times New Roman - the whole
 * email in a serif nobody chose. The conditional block below hands Word a
 * font it has. The selector list must include div and p, because the type
 * here is set on divs.
 *
 * NO TEMPLATE BRACES REACH A READER. Everything is a function argument;
 * there is no {{TOKEN}} left for a caller to forget. selftest asserts it.
 */
const MASCOT = `${SITE}/assets/mascot/png/head-on-the-hunt.png`;
const FONT = "Archivo,'Helvetica Neue',Helvetica,Arial,sans-serif";

/* A call to action. Padding on an inline anchor is dropped by Word, so the
 * button is a table cell with a bgcolor and the anchor inside it. */
const button = (href, label) =>
`<table role="presentation" cellpadding="0" cellspacing="0" border="0"
 style="border-collapse:collapse"><tr>
 <td bgcolor="#0B57C4" style="background-color:#0B57C4;padding:13px 22px">
 <a href="${href}" style="display:inline-block;color:#FAF7F0;text-decoration:none;
 font-weight:700;font-size:15px;font-family:${FONT}">${label}</a>
 </td></tr></table>`;

function shell(preheader, body, links) {
  const foot = (links || [])
    .map(l => `<a href="${l[1]}" style="color:#556F82;text-decoration:underline">${l[0]}</a>`)
    .join(" &nbsp;&middot;&nbsp; ");
  return `<!doctype html>
<html lang="en" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>${NAME}</title>
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
<!--[if mso]><style type="text/css">body,table,td,a,span,div,p{font-family:'Segoe UI',Arial,sans-serif !important}</style><![endif]-->
<style>
 :root{color-scheme:light only;supported-color-schemes:light only}
 @media (prefers-color-scheme:dark){
  .ice{background-color:#E8F1F7!important}.belly{background-color:#FAF7F0!important}
  .band{background-color:#1F2536!important}.plate{background-color:#FAF7F0!important}
  .beak{background-color:#F5A623!important}.ink{color:#1F2536!important}
  .mute,.mute a{color:#556F82!important}.faint{color:#7C97AA!important}
  .onband,.onband a{color:#E8F1F7!important}.onbandmute{color:#9FB3C4!important}}
 [data-ogsc] .ice{background-color:#E8F1F7!important}
 [data-ogsc] .belly{background-color:#FAF7F0!important}
 [data-ogsc] .band{background-color:#1F2536!important}
 [data-ogsc] .plate{background-color:#FAF7F0!important}
 [data-ogsc] .ink{color:#1F2536!important}
 [data-ogsc] .mute{color:#556F82!important}
 [data-ogsc] .onband{color:#E8F1F7!important}
 [data-ogsc] .onbandmute{color:#9FB3C4!important}
 @media only screen and (max-width:620px){
  .pad{padding-left:20px!important;padding-right:20px!important}
  .wm{font-size:22px!important}
  .kicker{font-size:10px!important;letter-spacing:.05em!important}}
</style>
</head>
<body class="ice" bgcolor="#E8F1F7" style="margin:0;padding:0;width:100%;
 background-color:#E8F1F7;-webkit-text-size-adjust:100%">
<div class="faint" style="display:none;max-height:0;max-width:0;overflow:hidden;
 mso-hide:all;font-size:1px;line-height:1px;opacity:0;color:#E8F1F7">${preheader}</div>
<table role="presentation" class="ice" bgcolor="#E8F1F7" width="100%" cellpadding="0"
 cellspacing="0" border="0" style="width:100%;background-color:#E8F1F7;border-collapse:collapse">
<tr><td align="center" valign="top" bgcolor="#E8F1F7" style="background-color:#E8F1F7;padding:24px 0">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" align="center"
 style="width:100%;max-width:600px;border-collapse:collapse">
 <tr><td class="band pad" bgcolor="#1F2536" style="background-color:#1F2536;padding:18px 24px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
   style="border-collapse:collapse"><tr>
   <td class="plate" bgcolor="#FAF7F0" width="52" height="52" valign="middle" align="center"
    style="background-color:#FAF7F0;width:52px;height:52px;font-size:0;line-height:0;
    mso-line-height-rule:exactly"><a href="${SITE}" style="text-decoration:none"><img
    src="${MASCOT}" width="46" height="46" alt="" style="display:block;width:46px;
    height:46px;border:0;outline:none;text-decoration:none"></a></td>
   <td width="16" style="width:16px;font-size:0;line-height:0">&nbsp;</td>
   <td align="left" valign="middle" style="background-color:#1F2536">
    <div class="wm onband" style="font-family:${FONT};font-size:25px;font-weight:800;
     letter-spacing:.02em;line-height:1.1;color:#E8F1F7"><a href="${SITE}"
     style="color:#E8F1F7;text-decoration:none">${NAME}</a></div>
    <div class="kicker onbandmute" style="padding-top:6px;font-family:${FONT};font-size:12px;
     font-weight:600;letter-spacing:.08em;line-height:1.4;text-transform:uppercase;
     color:#9FB3C4">State &amp; local govtech sales roles</div>
   </td></tr></table>
 </td></tr>
 <tr><td class="beak" height="3" bgcolor="#F5A623" style="background-color:#F5A623;
  height:3px;line-height:3px;font-size:3px;mso-line-height-rule:exactly">&nbsp;</td></tr>
 <tr><td class="belly ink pad" align="left" valign="top" bgcolor="#FAF7F0"
  style="background-color:#FAF7F0;padding:28px 24px;font-family:${FONT};font-size:15px;
  line-height:1.55;color:#1F2536">${body}</td></tr>
 <tr><td class="belly pad" bgcolor="#FAF7F0" style="background-color:#FAF7F0;
  padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  border="0" style="border-collapse:collapse"><tr><td height="1" bgcolor="#C9DCE8"
  style="height:1px;line-height:1px;font-size:0;background-color:#C9DCE8">&nbsp;</td>
  </tr></table></td></tr>
 <tr><td class="belly mute pad" align="left" bgcolor="#FAF7F0" style="background-color:#FAF7F0;
  padding:14px 24px 26px;font-family:${FONT};font-size:12px;line-height:1.7;color:#556F82">
  <a href="${SITE}" style="color:#0B57C4;text-decoration:none;font-weight:700">${NAME}</a>
  &mdash; every open sales role at state and local government technology companies.${
  foot ? `<br>${foot}` : ""}<br>
  <span class="faint" style="color:#7C97AA">It&rsquo;s tough SLEDing out there.</span>
 </td></tr>
</table></td></tr></table></body></html>`;
}

function confirmMail(token, prefs) {
  const link = `${SITE}/alerts?t=${token}&confirm=1`;
  const when = { daily: "every weekday morning",
                 twice: "Tuesday and Thursday mornings",
                 weekly: "Wednesday mornings" }[prefs.cadence];
  const text =
`Confirm your SLED JOBS alerts

Someone - hopefully you - asked for govtech sales role alerts ${when}.
Click to confirm. Until you do, nothing is sent.

${link}

If this was not you, ignore this email. No further mail will be sent to this
address and the request expires on its own.

The same link is your settings page afterwards: change what you get, or stop
the alerts, without a password.`;
  const html = shell(
    `Confirm to start getting govtech sales roles ${when}.`,
    `<div style="font-size:20px;font-weight:800;letter-spacing:-.015em;
 line-height:1.3">Confirm your ${NAME} alerts</div>
<p style="margin:12px 0 0;color:#1F2536">Someone &mdash; hopefully you &mdash; asked
 for govtech sales role alerts <strong>${when}</strong>. Until you confirm,
 nothing is sent.</p>
<div style="padding:20px 0 4px">${button(link, "Confirm alerts")}</div>
<p style="margin:16px 0 0;color:#556F82;font-size:13px">If this was not you,
 ignore this email. Nothing further will be sent to this address and the
 request expires on its own.</p>
<p style="margin:8px 0 0;color:#556F82;font-size:13px">The same link is your
 settings page afterwards &mdash; change what you get, or stop the alerts,
 no password.</p>`,
    []);
  return ["Confirm your SLED JOBS alerts", text, html];
}

/* --- read (settings page) ----------------------------------------------- */

export async function onRequestGet({ request, env }) {
  if (!env.ALERTS) return notConfigured();
  const token = cleanToken(new URL(request.url).searchParams.get("t"));
  if (!token) return json({ error: "bad_token" }, 400);
  const raw = await env.ALERTS.get("sub:" + token);
  if (!raw) return json({ error: "unknown_token" }, 404);
  const sub = JSON.parse(raw);
  return json({
    ok: true,
    // The address is echoed masked. The page only needs to show WHICH mailbox
    // this is, and a settings link that leaks a full address if forwarded is
    // worse than one that says j****@gmail.com.
    email: sub.email.replace(/^(.)[^@]*/, (_m, a) => a + "****"),
    confirmed: !!sub.confirmed,
    prefs: sub.prefs,
    saved: sub.saved || [],
    removed: sub.removed || {},
    last_sent: sub.last_sent || null,
  });
}

/* --- write --------------------------------------------------------------- */

export async function onRequestPost({ request, env }) {
  if (!env.ALERTS) return notConfigured();
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "send JSON" }, 400);
  }
  const action = String(body.action || "");

  if (action === "subscribe") return subscribe(body, env);

  const token = cleanToken(body.token);
  if (!token) return json({ error: "bad_token" }, 400);
  const raw = await env.ALERTS.get("sub:" + token);
  if (!raw) return json({ error: "unknown_token" }, 404);
  const sub = JSON.parse(raw);

  if (action === "confirm") {
    if (!sub.confirmed) {
      sub.confirmed = true;
      await env.ALERTS.put("sub:" + token, JSON.stringify(sub));
    }
    return json({ ok: true, confirmed: true, prefs: sub.prefs });
  }

  if (action === "update") {
    sub.prefs = cleanPrefs(body.prefs);
    await env.ALERTS.put("sub:" + token, JSON.stringify(sub));
    return json({ ok: true, prefs: sub.prefs });
  }

  /* Saved-role sync. Last writer wins by design: the alternative is merge
   * conflicts over a shortlist, and a person's two devices are not two
   * people. The client sends the union of what it holds, so the practical
   * failure is a stale device re-adding something you unsaved - visible,
   * and one click to fix. */
  if (action === "sync") {
    sub.saved = cleanSaved(body.saved);
    sub.removed = cleanRemoved(body.removed, sub.removed);
    await env.ALERTS.put("sub:" + token, JSON.stringify(sub));
    return json({ ok: true, saved: sub.saved, removed: sub.removed });
  }

  if (action === "stop") {
    // Actually delete. A subscription that unsubscribes into a suppression
    // list is still a record of who was interested in leaving their job.
    await env.ALERTS.delete("sub:" + token);
    await env.ALERTS.delete(await emailKey(sub.email));
    return json({ ok: true, stopped: true });
  }

  return json({ error: "unknown action" }, 400);
}

async function subscribe(body, env) {
  if (body.company_fax) return json({ ok: true });   // honeypot, as on submit
  const email = validEmail(body.email);
  const prefs = cleanPrefs(body.prefs);
  // One response for every outcome below. Varying it would turn this endpoint
  // into an oracle for "is this address subscribed", which is exactly the
  // question a job board must never answer about somebody.
  const same = json({ ok: true, check_your_email: true });
  if (!email) return json({ error: "That does not look like an email address." }, 400);
  if (!env.RESEND_KEY) return notConfigured();

  const ek = await emailKey(email);
  const existing = await env.ALERTS.get(ek);
  const now = Math.floor(Date.now() / 1000);

  if (existing) {
    const raw = await env.ALERTS.get("sub:" + existing);
    if (raw) {
      const sub = JSON.parse(raw);
      if (sub.confirmed) {
        // Already subscribed: send the settings link, not a second signup.
        await send(env, email, "Your SLED JOBS alert settings",
          `Your settings link:\n\n${SITE}/alerts?t=${existing}\n\n` +
          `You are already subscribed, so nothing changed. Use the link to ` +
          `adjust what you get or stop the emails.`,
          shell(
            "Your settings link - nothing changed on your subscription.",
            `<div style="font-size:20px;font-weight:800;letter-spacing:-.015em;
 line-height:1.3">Your ${NAME} settings</div>
<p style="margin:12px 0 0">You are already subscribed, so nothing changed.
 Use the link below to adjust what you get, or to stop the emails.</p>
<div style="padding:20px 0 4px">${button(SITE + "/alerts?t=" + existing,
              "Open settings")}</div>
<p style="margin:16px 0 0;color:#556F82;font-size:13px">If you did not ask for
 this, ignore it &mdash; nothing about your subscription has changed.</p>`,
            []));
        return same;
      }
      // Pending: re-send the confirmation, but not more than once an hour, so
      // this cannot be used to bomb somebody else's inbox.
      if (now - (sub.confirm_sent || 0) < CONFIRM_COOLDOWN) return same;
      sub.prefs = prefs;
      sub.confirm_sent = now;
      await env.ALERTS.put("sub:" + existing, JSON.stringify(sub));
      await send(env, email, ...confirmMail(existing, prefs));
      return same;
    }
  }

  const token = mintToken();
  const sub = { email, prefs, saved: [], removed: {}, confirmed: false,
                created: now, confirm_sent: now, last_sent: null };
  await env.ALERTS.put("sub:" + token, JSON.stringify(sub));
  await env.ALERTS.put(ek, token);
  const ok = await send(env, email, ...confirmMail(token, prefs));
  if (!ok) {
    // Do not leave a half-made subscription that never got its link.
    await env.ALERTS.delete("sub:" + token);
    await env.ALERTS.delete(ek);
    return json({ error: "We could not send the confirmation just now. Try again shortly." },
                502);
  }
  return same;
}
