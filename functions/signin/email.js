/* ASKING FOR A SIGN-IN EMAIL: POST /signin/email, from the form on /signin.
 *
 * It mails a link and a code for the same sign-in, and sets a pending cookie
 * on this device for the code to be checked against (functions/_session.js
 * says why nothing is stored). The link signs in whichever device opens it
 * and confirms; the code signs in only the device that asked.
 *
 * THREE ALLOWANCES A DAY, because the free plan sends 100 emails a day for
 * the whole site and the alert digests go out of the same allowance:
 *   ALL_PER_DAY for every sign-in email together (SIGNIN_MAILS_PER_DAY
 *               overrides it), so sign-ins can never spend the digests';
 *   PER_CALLER  per network: an IPv4 address, or an IPv6 /48 (a free tunnel
 *               hands one person a /48), so one person cannot walk a /56 of
 *               /64s through it. Generous (20), because an office, a
 *               conference's Wi-Fi or a phone carrier puts many people
 *               behind one address (final review, 2026-11-13);
 *   PER_ADDRESS per MAILBOX: "+tags" and, at Gmail, dots are one inbox, so
 *               nobody's inbox can be filled from here by spelling it 60
 *               ways (security review, 2026-11-13).
 * Each is a KV counter that expires after 26 hours. ALL OF THEM ARE READ
 * BEFORE ANY IS WRITTEN, and a request any one refuses writes nothing: the
 * first version wrote each before checking the next, so refusals still
 * spent the account's 1,000 KV writes a day, which alerts, claims and
 * ratings share (same review). A sign-in that sends spends three writes, so
 * sign-ins can never take more than three times ALL_PER_DAY.
 *
 * KV IS NOT A COUNTER. It has no atomic increment, takes one write a second
 * on a key, and another location may read a value up to a minute old. The
 * first version wrote the shared key last and let a refused write there
 * pass, so a burst of requests from many networks all read the same count
 * and all sent: 104 emails against a cap of 60 in the final review's run
 * (2026-11-13). Now the SHARED key is written first, a refused write there
 * refuses the request, and the write carries a mark of its own that is read
 * back: a request whose mark was overwritten by a simultaneous one sends
 * nothing. That holds at one location; across locations the count is still
 * approximate, and Resend's own 100 a day is the backstop. A firm cap needs
 * a Durable Object (DEPLOY.md §3a says so). A double-click is two requests
 * on the same personal keys: the second write is refused, nothing is sent
 * for it, and the page says the email may be on its way already.
 *
 * Anyone may sign in, so this answers honestly: there is no account whose
 * existence the reply could give away.
 */
import { sameSitePath } from "../_to.js";
import { send, shell, button, callerNet, FONT, SITE, NAME } from "../_mail.js";
import { validEmail } from "../_address.js";
import { GOOGLE } from "../_gate.js";
import { configured, fromOurPage, seal, codeFor, showCode, random, sha256u, now, setCookie,
         PENDING, LINK_MINUTES } from "../_session.js";
import { signinForm, messagePage, redirect, backToSignin } from "../_signin_page.js";

export const PER_CALLER = 20;
export const PER_ADDRESS = 4;
export const ALL_PER_DAY = 60;

const day = () => new Date().toISOString().slice(0, 10);
const orGoogle = (google) => (google ? ", or use Google" : "");

/* One inbox, however it is spelled: the part after "+" dropped everywhere,
 * and Gmail's dots and googlemail.com folded. For the allowance's key only:
 * the address signed in stays exactly as typed. */
export function mailbox(email) {
  let [local, domain] = email.split("@");
  local = local.split("+")[0];
  if (domain === "gmail.com" || domain === "googlemail.com") {
    local = local.replace(/\./g, "");
    domain = "gmail.com";
  }
  return `${local}@${domain}`;
}

/* The network asking, hashed with the day: an IPv4 address whole, an IPv6
 * address by its /48 (callerNet hands back the /64). */
async function network(request) {
  let net = callerNet(request.headers.get("cf-connecting-ip") || "unknown");
  if (net.endsWith("::/64")) net = net.split(":").slice(0, 3).join(":") + "::/48";
  return (await sha256u(`${net}|${day()}`)).slice(0, 32);
}

const REFUSED = {
  all: (g) => `Email sign-in has reached today's limit${orGoogle(g)}. Try again tomorrow.`,
  caller: (g) => `That is enough sign-in emails from this network today${orGoogle(g)}. Try again from another network, such as your phone's mobile data, or tomorrow.`,
  address: () => "We have sent this address enough sign-in emails today. Use the newest one, or try tomorrow.",
};

function mail(link, code) {
  const shown = showCode(code);
  const text = `Sign in to ${NAME}:\n\n${link}\n\nOr type this code on the sign-in page: ${shown}\n\n`
    + `The link and the code work for ${LINK_MINUTES} minutes. If you did not ask to sign in, `
    + `ignore this email: nothing happens unless it is used.\n`;
  const html = shell(`Your sign-in code is ${shown}`,
    `<div style="font-size:20px;font-weight:800;margin:0 0 12px">Sign in to ${NAME}</div>
<p style="margin:0 0 18px">Here is your link. It works for ${LINK_MINUTES} minutes.</p>
${button(link, "Sign in")}
<p style="margin:22px 0 6px">Reading this on another device? Type this code on the sign-in page:</p>
<div style="font-family:${FONT};font-size:26px;font-weight:800;letter-spacing:.14em;color:#1F2536">${shown}</div>
<p style="margin:22px 0 0;font-size:13px;color:#556F82">If you did not ask to sign in, ignore this email:
nothing happens unless it is used.</p>`, []);
  return [`Sign in to ${NAME}`, text, html];
}

export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  if (!fromOurPage(request)) {
    return messagePage(403, "That did not come from our sign-in page",
      "Open the sign-in page on this site and ask again.");
  }
  if (!configured(env) || !env.RESEND_KEY || !env.ALERTS) {
    return messagePage(503, "Email sign-in is being set up",
      "Signing in by email is not switched on yet. Try again in a little while.");
  }
  let form = null;
  try { form = await request.formData(); } catch { form = null; }
  const field = (k) => { const v = form && form.get(k); return typeof v === "string" ? v : ""; };
  const to = sameSitePath(field("to"), url.origin);
  const typed = field("email").slice(0, 254);
  const email = validEmail(typed);
  if (!email) {
    return signinForm(to, { email: typed, status: 400,
      message: "That does not look like an email address. Check it and try again." });
  }
  const all = Number(env.SIGNIN_MAILS_PER_DAY) > 0 ? Number(env.SIGNIN_MAILS_PER_DAY) : ALL_PER_DAY;
  const caps = [
    ["all", `cap:signin-all:${day()}`, all],
    ["caller", `cap:signin:${await network(request)}`, PER_CALLER],
    ["address", `cap:signin-to:${(await sha256u(`${mailbox(email)}|${day()}`)).slice(0, 32)}`, PER_ADDRESS],
  ];
  // everything that can fail is done BEFORE a counter is spent
  const pending = { e: email, n: random(12), to, exp: now() + LINK_MINUTES * 60 };
  const link = `${SITE}/signin/link?t=${await seal(env, "link", pending)}`;
  const letter = mail(link, await codeFor(env, pending));
  const cookie = setCookie(PENDING, await seal(env, "pending", pending), LINK_MINUTES * 60);
  const busy = () => signinForm(to, { email, status: 503,
    message: `Email sign-in is busy just now. Try again in a minute${orGoogle(GOOGLE)}.` });
  const counts = [];
  try {
    for (const [why, k, cap] of caps) {
      const n = Number.parseInt(await env.ALERTS.get(k), 10) || 0;
      if (n >= cap) return signinForm(to, { email, status: 429, message: REFUSED[why](GOOGLE) });
      counts.push([why, k, n]);
    }
  } catch { return busy(); }
  const ttl = { expirationTtl: 60 * 60 * 26 };
  // THE SHARED KEY FIRST, with this request's own mark, read back
  const [, allKey, allN] = counts[0];
  const mark = `${allN + 1}:${pending.n}`;
  try {
    await env.ALERTS.put(allKey, mark, ttl);
    // Refused only on PROOF of a simultaneous request: the same or a higher
    // count under another mark. A stale read (KV may hand back the value from
    // before our write, or nothing) is no proof, and never refuses anybody.
    const back = String((await env.ALERTS.get(allKey)) || "");
    const [c, who] = [Number.parseInt(back, 10) || 0, back.split(":")[1] || ""];
    if (c >= allN + 1 && who !== pending.n) return busy();
  } catch { return busy(); }
  for (const [, k, n] of counts.slice(1)) {
    try {
      await env.ALERTS.put(k, String(n + 1), ttl);
    } catch {
      return signinForm(to, { email, status: 429,
        message: "If you just asked, the email is on its way: check your inbox. Otherwise try again in a minute." });
    }
  }
  let sent = false;
  try { sent = await send(env, email, ...letter); } catch { sent = false; }
  if (!sent) {
    return signinForm(to, { email, status: 502,
      message: "We could not send the email just now. Try again in a minute." });
  }
  // ON TO A GET PAGE, so a reload never sends the form (and the email) again
  return redirect("/signin/check", [["Set-Cookie", cookie]]);
}

/* /signin/email opened as an address (a restored tab, Enter in the address
 * bar after a refusal): where the person was, never the asset server's 404. */
export const onRequestGet = backToSignin;
export const onRequestHead = backToSignin;
