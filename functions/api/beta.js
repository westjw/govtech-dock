/* REDEEMING A JOB HUNTER BETA CODE.
 *
 * The owner mints codes with scripts/beta_codes.py and hands them to people
 * he has met. This is where one is typed in. It writes a redemption record
 * and nothing else.
 *
 * WHAT THIS IS NOT, AND THE COMMENT IS LOAD-BEARING: it is not auth. A code
 * is a BEARER token - whoever holds the string holds the code, including
 * anyone it was forwarded to - and the reply here gates a PAGE, not data.
 * Nobody's resume, fact bank or employment history is behind it, because
 * job-hunter's server binds 127.0.0.1 deliberately and there is no
 * per-request owner yet. When there is, this file is NOT what should stand
 * in front of it; see SPEC-jobhunter.md.
 *
 * THE REDEMPTION IS A CONSENT RECORD. SPEC-jobhunter.md (2026-09-18) says a
 * consent record, a right-to-delete path and a retention period must exist
 * before a real person is onboarded. This writes the first: which code, when,
 * and the exact version of the words they were shown. The other two are not
 * built, so a redemption admits somebody to a LIST and starts no processing.
 *
 * NO ADDRESS IS ASKED FOR OR STORED. A beta list is not a reason to start a
 * contact database, and the owner already knows who he handed a code to -
 * that is what the note on the minted code is for.
 */
import { json } from "../_mail.js";

/* The words a redeemer agrees to, versioned. Stored WITH the redemption so a
 * consent record says what was consented to, rather than pointing at a page
 * that has since been edited - which is a record of nothing. */
export const CONSENT = {
  version: "2026-09-23",
  text: "Job Hunter is a closed beta. It is not running yet: redeeming a code "
      + "puts you on the list and the owner will be in touch before anything "
      + "of yours is collected. Nothing you send is processed until you are "
      + "told, in writing, what is kept and how to have it deleted.",
};

const SHAPE = /^JH-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$/;

export async function onRequestPost({ request, env }) {
  if (!env.ALERTS) return json({ error: "not_configured" }, 501);
  let body = {};
  try { body = await request.json(); } catch (e) { return json({ error: "bad_body" }, 400); }

  /* Uppercased and trimmed before the shape test: a code is read off a screen
   * and typed by somebody else, and "jh-w60a-n8z9 " is the same code. */
  const code = String(body.code || "").trim().toUpperCase();
  if (!SHAPE.test(code)) return json({ error: "bad_code" }, 400);

  /* A DAY CAP ON GUESSES, per the claim endpoint's own pattern. 40 bits is
   * not a guessable space, but a door with no cap invites somebody to find
   * out, and the log would fill with their attempts rather than with people. */
  const day = "betatry:" + new Date().toISOString().slice(0, 10);
  const tries = Number((await env.ALERTS.get(day)) || 0);
  if (tries > 500) return json({ error: "too_many" }, 429);
  await env.ALERTS.put(day, String(tries + 1), { expirationTtl: 60 * 60 * 26 });

  const live = JSON.parse((await env.ALERTS.get("beta:codes")) || "{}");
  if (!live[code]) return json({ ok: false, why: "not_a_code" });

  /* ONE CODE, ONE PERSON, and the second person is TOLD rather than silently
   * let in beside the first. A shared code cannot be revoked for one holder,
   * which is the whole reason beta_codes.py mints one per person. */
  const key = "betaredeem:" + code;
  const already = await env.ALERTS.get(key);
  if (already) {
    const rec = JSON.parse(already);
    return json({ ok: false, why: "already_redeemed", on: rec.on });
  }
  const rec = {
    code,
    on: new Date().toISOString().slice(0, 10),
    at: new Date().toISOString(),
    consent_version: CONSENT.version,
    consent_text: CONSENT.text,
  };
  await env.ALERTS.put(key, JSON.stringify(rec));
  return json({ ok: true, on: rec.on, consent: CONSENT });
}

/* Whether a code is live and unredeemed, so the page can tell somebody their
 * code is spent without making them type it twice. Deliberately answers the
 * same shape for "never minted" and "revoked": this endpoint must not become
 * a way to find out which codes exist. */
export async function onRequestGet({ request, env }) {
  if (!env.ALERTS) return json({ error: "not_configured" }, 501);
  const code = String(new URL(request.url).searchParams.get("code") || "")
    .trim().toUpperCase();
  if (!SHAPE.test(code)) return json({ error: "bad_code" }, 400);
  const live = JSON.parse((await env.ALERTS.get("beta:codes")) || "{}");
  const redeemed = await env.ALERTS.get("betaredeem:" + code);
  return json({ known: !!live[code], redeemed: !!redeemed });
}
