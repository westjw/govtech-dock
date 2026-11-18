/* SAVED ROLES FOLLOW THE SIGN-IN (owner, 2026-11-18: "yes build that, saves
 * should follow my sign-in").
 *
 * Until now a saved role lived in the browser that saved it, and followed a
 * person to another device only through the alert subscription's sync. The
 * owner signed in from a private window, found none of his saved roles, and
 * asked whether he had made a second account. There are no accounts - a
 * sign-in proves an address and nothing else is stored for it - so the
 * address is what a saved list now hangs from.
 *
 *   GET    /api/saved                 what this sign-in has saved
 *   POST   /api/saved {saved, removed, owner}
 *                                     merge this browser's list in; answers
 *                                     with the merged list, so one request
 *                                     both sends and fetches
 *
 * WHERE: D1, bound to the Pages project as DB, not the ALERTS KV. The free
 * plan's KV allows 1,000 writes a day for the WHOLE account, and the sign-in
 * spends three of them on every email it sends (functions/signin/email.js):
 * saves written there would compete with the way in. D1's free plan allows
 * 100,000 rows written a day, and a merge writes only the rows it changed.
 * No DB binding: 501 not_configured, and the board keeps saves on the device
 * exactly as it always did. Nothing pretends a save left the browser.
 *
 * WHOSE: the signed-in address from _identity.signedIn (Access first, then
 * the site's own sign-in), HASHED. The address itself is never stored here:
 * the rows hold a hash, role ids and the role's own public details. Only
 * what Gmail guarantees is one inbox is folded (its dots and +tags); at any
 * other domain the exact address is the key, because a sign-in proves only
 * the address typed, and a catch-all mailbox reading "alice+x@corp" is not
 * Alice (review, 2026-11-18). A list split in two is an inconvenience; two
 * people sharing one list would be a leak.
 *
 * WHAT: the snapshot and the tombstones the alert sync already keeps
 * (functions/_saved.js), one row per (owner, role). A role is saved when its
 * saved_at is later than its removed_at. Merging takes the later of each, so
 * unsaving on the phone beats a stale laptop, and saving again beats an old
 * unsave - the rules the board's own merge has used since 2026-10-07.
 *
 * ONE WRITER AT A TIME, PER PERSON. A merge reads the list, decides, and
 * writes; two of those at once each decided on the same old read, so twelve
 * parallel POSTs wrote 6,000 rows past a 3,000-row cap and a 500-role limit
 * (review, 2026-11-18). Now:
 *   - the read is ONE snapshot: the version and the rows come from a single
 *     D1 batch (a transaction), so a commit cannot land between them (it did
 *     between two reads, and a stale merge went through - second review);
 *   - the write is a COMPARE-AND-SET: the first statement of its batch
 *     inserts into saved_cas a value that is NULL unless the person's
 *     version is still the one read, and saved_cas.ok is NOT NULL, so a
 *     stale writer's batch fails whole and reads again. Nothing is claimed
 *     and nothing has to be cleaned up, so no stale writer can slip into a
 *     gap and no leftover can jam a person's later writes (a first version
 *     deleted its lock rows below the new version, and both happened -
 *     second review, 2026-11-18).
 * So the caps are checked against exactly what is there.
 *
 * A BORROWED BROWSER IS NOT A MERGE. The board keeps a copy of the list in
 * the browser, so the list a browser sends up may be somebody else's: the
 * person who signed in on this machine before. Every answer carries `owner`,
 * a short tag of the account (a second hash, not the row key) that the board
 * keeps beside its copy; the page also learns it before drawing anything,
 * from the __Host-sj_owner cookie functions/_middleware.js sets. A POST whose
 * `owner` names another account is not merged in: the account's own list
 * comes back with `replace: true`. A copy with no tag yet (saves made before
 * this existed) merges into the first account that signs in on it, which is
 * the person whose saves they are.
 *
 * LIMITS: 500 saved roles a person (MAX_SAVED: past it, the newest additions
 * that fit land and the answer says `capped`, counting every role held back,
 * so a device never stops syncing over it and never loses one silently), a
 * request body under 600 KB, SAVED_WRITES_PER_DAY rows written a person a
 * day (3,000; a person saving a role writes one), and a POST must come from
 * this site's own page (fromOurPage), so another site cannot make a
 * signed-in browser rewrite its list.
 *
 * NO DELETE HERE, ON PURPOSE. A list a person wants gone is unsaved role by
 * role (tombstones, pruned at 90 days); a one-shot delete does not stick
 * while any of their browsers holds a copy that uploads it again, so it would
 * promise what it cannot keep (second review, 2026-11-18).
 */
import { signedIn } from "../_identity.js";
import { fromOurPage, sha256u } from "../_session.js";
import { json } from "../_mail.js";
import { cleanSaved, cleanRemoved, MAX_SAVED, TOMBSTONE_DAYS } from "../_saved.js";

export const MAX_BODY = 600_000;
export const WRITES_PER_DAY = 3000;
const TRIES = 4;
// a body of 600 KB holds about a thousand roles; this bounds the work, the
// 500 cap is the merge's to apply, counted
const MAX_INCOMING = 5000;

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS saved_roles (
     owner TEXT NOT NULL,
     id TEXT NOT NULL,
     item TEXT,
     saved_at TEXT,
     removed_at TEXT,
     PRIMARY KEY (owner, id))`,
  `CREATE TABLE IF NOT EXISTS saved_owners (
     owner TEXT PRIMARY KEY,
     v INTEGER NOT NULL,
     day TEXT NOT NULL,
     writes INTEGER NOT NULL)`,
  // always empty: the compare-and-set inserts into it and the same batch
  // deletes the row again; a NULL from a stale version fails NOT NULL
  `CREATE TABLE IF NOT EXISTS saved_cas (ok INTEGER NOT NULL)`,
];
/* A boolean set once the tables exist, never a shared promise: a promise made
 * by one request and awaited by others hangs them all when the first request
 * is cancelled mid-batch (review, 2026-11-18). CREATE ... IF NOT EXISTS twice
 * at once is harmless. */
let SCHEMA_OK = false;
async function ready(db) {
  if (SCHEMA_OK) return;
  await db.batch(SCHEMA.map((s) => db.prepare(s)));
  SCHEMA_OK = true;
}

/* One inbox, as far as a sign-in can know it: lower-cased, and at Gmail the
 * dots and the +tag folded, because Gmail delivers all of them to one box. */
export function inboxOf(email) {
  const e = String(email || "").trim().toLowerCase();
  const at = e.lastIndexOf("@");
  if (at < 1) return e;
  let local = e.slice(0, at), domain = e.slice(at + 1);
  if (domain === "gmail.com" || domain === "googlemail.com") {
    local = local.split("+")[0].replace(/\./g, "");
    domain = "gmail.com";
  }
  return `${local}@${domain}`;
}

/* "o1:" + a hash of the inbox. The prefix names the scheme, so a change to
 * how owners are keyed can live beside the old rows instead of colliding. */
export async function ownerKey(email) {
  return "o1:" + (await sha256u("saved-roles|" + inboxOf(email)));
}

/* The tag the board keeps beside its copy: not the owner key, so the key
 * rows are stored under never leaves the database. */
export async function ownerTag(email) {
  return (await sha256u("saved-owner-tag|" + inboxOf(email))).slice(0, 16);
}

const alive = (r) => !!(r.item && r.saved_at && !(r.removed_at && r.removed_at >= r.saved_at));
const today = () => new Date().toISOString().slice(0, 10);

/* What the board reads back: the saved snapshots, newest first, and the
 * tombstones still young enough to matter (_saved.js prunes at 90 days). */
function stateOf(rows) {
  const saved = [], removed = {};
  for (const r of rows) {
    if (alive(r)) {
      try { saved.push(JSON.parse(r.item)); } catch { /* a row we cannot read is not a save */ }
    } else if (r.removed_at) {
      removed[r.id] = r.removed_at;
    }
  }
  saved.sort((a, b) => String(b.saved_at || b.saved_on || "").localeCompare(String(a.saved_at || a.saved_on || "")));
  return { saved, removed: cleanRemoved(removed, {}) };
}

async function rowsOf(db, owner) {
  const r = await db.prepare(
    "SELECT id, item, saved_at, removed_at FROM saved_roles WHERE owner = ?").bind(owner).all();
  return r.results || [];
}

async function who(request, env) {
  const me = await signedIn(request, env);
  return me && me.email ? { key: await ownerKey(me.email), tag: await ownerTag(me.email) } : null;
}

export async function onRequestGet({ request, env }) {
  if (!env.DB) return json({ error: "not_configured" }, 501);
  const me = await who(request, env);
  if (!me) return json({ error: "not_signed_in" }, 403);
  try {
    await ready(env.DB);
    return json({ ok: true, owner: me.tag, now: new Date().toISOString(), ...stateOf(await rowsOf(env.DB, me.key)) });
  } catch (e) {
    return json({ error: "unavailable" }, 503);
  }
}

/* The merge, decided on one read of the list. Returns what to write, or null
 * when nothing changes. */
function merge(rows, incoming, gone, now) {
  const cur = new Map(rows.map((r) => [r.id, r]));
  const next = new Map(rows.map((r) => [r.id, { ...r }]));
  const blank = (id) => ({ id, item: null, saved_at: null, removed_at: null });
  // tombstones first: they can only take a role away, so they never meet the cap
  for (const [id, when] of Object.entries(gone)) {
    const row = next.get(id) || blank(id);
    if (!row.removed_at || when > row.removed_at) row.removed_at = when;
    next.set(id, row);
  }
  // THE LATER OF EACH, per role, newest saves first. A save with no timestamp
  // at all (one from before saved_at existed) counts as saved on its day.
  let living = [...next.values()].filter(alive).length, capped = 0;
  for (const s of incoming) {
    const at = String(s.saved_at || (s.saved_on ? `${s.saved_on}T00:00:00.000Z` : "1970-01-01T00:00:00.000Z"));
    const row = next.get(s.id) || blank(s.id);
    if (row.saved_at && !(at > row.saved_at)) continue;
    const was = alive(row);
    const cand = { ...row, item: JSON.stringify(s), saved_at: at };
    if (!was && alive(cand)) {
      if (living >= MAX_SAVED) { capped++; continue; }
      living++;
    }
    next.set(s.id, cand);
  }
  // Tombstones past the window go, like the alert sync's: by then every
  // device has seen the removal, and a list that only grows keeps exactly
  // what unsaving was meant to get rid of.
  const cutoff = new Date(now - TOMBSTONE_DAYS * 86400000).toISOString();
  const drop = [];
  for (const [id, row] of next) {
    if (!alive(row) && (!row.removed_at || row.removed_at < cutoff)) {
      next.delete(id);
      if (cur.has(id)) drop.push(id);
    }
  }
  const changed = [...next.values()].filter((r) => {
    const c = cur.get(r.id);
    return !c || c.item !== r.item || c.saved_at !== r.saved_at || c.removed_at !== r.removed_at;
  });
  return { next, changed, drop, capped };
}

export async function onRequestPost({ request, env }) {
  if (!env.DB) return json({ error: "not_configured" }, 501);
  if (!fromOurPage(request)) return json({ error: "not_from_this_site" }, 403);
  const me = await who(request, env);
  if (!me) return json({ error: "not_signed_in" }, 403);
  const owner = me.key;
  let raw = "";
  try { raw = await request.text(); } catch { return json({ error: "bad_body" }, 400); }
  if (raw.length > MAX_BODY) return json({ error: "too_large" }, 413);
  let body = null;
  try { body = JSON.parse(raw); } catch { return json({ error: "bad_body" }, 400); }
  if (!body || typeof body !== "object") return json({ error: "bad_body" }, 400);

  const now = Date.now();
  // Somebody else's copy (see the header): read, never merged.
  const replace = typeof body.owner === "string" && body.owner !== "" && body.owner !== me.tag;
  const incoming = replace ? [] : cleanSaved(body.saved, now, MAX_INCOMING);
  const gone = replace ? {} : cleanRemoved(body.removed, {}, now);
  const cap = Number(env.SAVED_WRITES_PER_DAY) > 0 ? Number(env.SAVED_WRITES_PER_DAY) : WRITES_PER_DAY;
  try {
    await ready(env.DB);
    for (let attempt = 0; attempt < TRIES; attempt++) {
      // ONE SNAPSHOT: the version and the rows from the same transaction
      const [metaR, rowsR] = await env.DB.batch([
        env.DB.prepare("SELECT v, day, writes FROM saved_owners WHERE owner = ?").bind(owner),
        env.DB.prepare("SELECT id, item, saved_at, removed_at FROM saved_roles WHERE owner = ?").bind(owner),
      ]);
      const meta = ((metaR && metaR.results) || [])[0] || null;
      const rows = (rowsR && rowsR.results) || [];
      const v0 = meta ? Number(meta.v) || 0 : 0;
      const { next, changed, drop, capped } = merge(rows, incoming, gone, now);
      const writes = changed.length + drop.length;
      const answer = (extra) => json({ ok: true, owner: me.tag, replace, capped, now: new Date(now).toISOString(),
        ...extra, ...stateOf([...next.values()]) });
      if (!writes) return answer({ wrote: 0 });
      const used = meta && meta.day === today() ? Number(meta.writes) || 0 : 0;
      if (used + writes > cap) return json({ error: "busy_today" }, 429);
      const stmts = [
        // THE COMPARE-AND-SET: NULL, and so a failed batch, unless the
        // version is still the one this merge read
        env.DB.prepare(`INSERT INTO saved_cas (ok) VALUES (CASE WHEN COALESCE(
            (SELECT v FROM saved_owners WHERE owner = ?), 0) = ? THEN 1 END)`).bind(owner, v0),
        ...changed.map((r) => env.DB.prepare(
          `INSERT INTO saved_roles (owner, id, item, saved_at, removed_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (owner, id) DO UPDATE SET item = excluded.item,
             saved_at = excluded.saved_at, removed_at = excluded.removed_at`)
          .bind(owner, r.id, r.item, r.saved_at, r.removed_at)),
        ...drop.map((id) => env.DB.prepare("DELETE FROM saved_roles WHERE owner = ? AND id = ?").bind(owner, id)),
        env.DB.prepare(
          `INSERT INTO saved_owners (owner, v, day, writes) VALUES (?, ?, ?, ?)
           ON CONFLICT (owner) DO UPDATE SET v = excluded.v, day = excluded.day, writes = excluded.writes`)
          .bind(owner, v0 + 1, today(), used + writes),
        env.DB.prepare("DELETE FROM saved_cas"),
      ];
      try {
        await env.DB.batch(stmts);
        return answer({ wrote: writes });
      } catch (e) {
        if (!/NOT NULL constraint failed: saved_cas/i.test(String(e && e.message))) throw e;
        // another request wrote first: read what it left and decide again
      }
    }
    return json({ error: "busy" }, 409);
  } catch (e) {
    return json({ error: "unavailable" }, 503);
  }
}
