/* The mail and token half of the public endpoints, in one place.
 *
 * alerts.js grew these first and claim.js needs every one of them: the same
 * Resend call, the same brand shell so two emails from this project do not
 * look like two projects, the same conservative address check, the same
 * 32-byte token. Copying them would have been the third restatement of the
 * brand after brand.json and _brand.js, and the one nobody would remember to
 * update. A Pages Function cannot import a repo file at runtime, so this is
 * the layer that stops the duplication spreading further.
 *
 * check_mail_is_built_from_the_shell asserts both endpoints import from here
 * rather than hand-rolling their own.
 */
import { FROM, SITE, NAME, DOMAIN } from "./_brand.js";

const MASCOT = `${SITE}/assets/mascot/png/head-on-the-hunt.png`;
const FONT = "Archivo,'Helvetica Neue',Helvetica,Arial,sans-serif";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      "referrer-policy": "no-referrer",
    },
  });

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
/* AN IPv6 CALLER IS ITS /64. One machine is routinely handed a whole /64,
 * and Cloudflare passes the full address, so keyed on the /128 a script that
 * walked 2001:db8:1:2::1, ::2, ::3 ... was a new caller every request and no
 * cap ever refused it (review, 2026-10-07: 40 of 40 sign-ups accepted). An
 * IPv4 address stays whole. */
function callerNet(ip) {
  if (!ip.includes(":")) return ip;
  const [head, tail] = ip.toLowerCase().split("::");
  const a = head ? head.split(":") : [];
  const b = tail !== undefined ? (tail ? tail.split(":") : []) : null;
  const full = b === null ? a : [...a, ...Array(Math.max(0, 8 - a.length - b.length)).fill("0"), ...b];
  return full.slice(0, 4).map((g) => g.replace(/^0+(?=.)/, "")).join(":") + "::/64";
}

/* WHO IS ASKING, as a key nobody can turn back into an address or follow
 * across days: SHA-256 of the caller's IP and today's date. The date is IN
 * the hash, so yesterday's key cannot be recomputed and nothing links one
 * day's activity to the next. The IP ALONE: ratings hashed the user-agent in
 * too, and a new user-agent string reset the cap (launch audit, 2026-10-06).
 * Every per-caller daily cap in functions/ uses this one. */
async function callerKey(request) {
  const h = request && request.headers;
  const ip = callerNet((h && h.get && h.get("cf-connecting-ip")) || "unknown");
  const day = new Date().toISOString().slice(0, 10);
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(`${ip}|${day}`));
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

/* A DAILY ALLOWANCE PER CALLER for anything that sends mail. Counted on
 * every attempt, BEFORE the address is looked up, so how fast a caller runs
 * out can never depend on whether an address is subscribed or claimed: the
 * endpoints answer identically for every address state, and this must not
 * become the oracle they refuse to be. A refusal writes nothing. Until
 * 2026-10-06 neither endpoint limited a caller at all: one script could
 * spend the day's 100 Resend mails, or mail five strangers at each of 2,026
 * companies (launch audit). */
async function underDailyCap(env, request, scope, cap) {
  const k = `cap:${scope}:` + (await callerKey(request));
  const n = Number((await env.ALERTS.get(k)) || 0);
  if (n >= cap) return false;
  await env.ALERTS.put(k, String(n + 1), { expirationTtl: 60 * 60 * 26 });
  return true;
}

function validEmail(raw) {
  const e = String(raw || "").trim().toLowerCase();
  if (e.length < 6 || e.length > 254) return null;
  if (/[\s<>",;\\()\[\]]/.test(e)) return null;
  if (!/^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(e)) return null;
  if (e.includes("..")) return null;
  return e;
}

/* A token from a URL, used only as a KV key. Constrain its shape so a crafted
 * one cannot reach for a neighbouring key. */
function cleanToken(raw) {
  const t = String(raw || "");
  return /^[A-Za-z0-9_-]{40,64}$/.test(t) ? t : null;
}

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

const button = (href, label) =>
`<table role="presentation" cellpadding="0" cellspacing="0" border="0"
 style="border-collapse:collapse"><tr>
 <td bgcolor="#0B57C4" style="background-color:#0B57C4;padding:13px 22px">
 <a href="${href}" style="display:inline-block;color:#FAF7F0;text-decoration:none;
 font-weight:700;font-size:15px;font-family:${FONT}">${label}</a>
 </td></tr></table>`;

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

export { json, mintToken, emailKey, callerKey, callerNet, underDailyCap, validEmail, cleanToken, send, button, shell,
         FONT, MASCOT, FROM, SITE, NAME, DOMAIN };
