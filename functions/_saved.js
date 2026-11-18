/* WHAT A SAVED ROLE IS, in one place (2026-11-18).
 *
 * Two endpoints keep a person's saved roles: the alert subscription's sync
 * (functions/api/alerts.js, keyed by the subscription token) and, since the
 * owner's "saves should follow my sign-in" (2026-11-18), functions/api/
 * saved.js, keyed by the signed-in address. Both store exactly this snapshot
 * and these tombstones, so a second copy of these rules would be a second
 * place for them to drift.
 */
export const MAX_SAVED = 500;          // a person's shortlist, not a scrape target
export const MAX_ID = 300;

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
// `kind` separates a saved COMPANY from a saved role. Dropped, a company saved
// on the laptop came back on the phone as a role "no longer listed" that "may
// have been filled" (launch audit 3, 2026-10-09).
// work_mode joined 2026-11-18, when saves started following the sign-in, and
// territory and office with it, in THEIR shapes (below): the board writes all
// three on a saved role and draws them, so a role saved on the laptop read
// "no stated territory" on the phone without them (review, 2026-11-18: this
// list first kept strings only, and both are objects on every posting).
export const SAVED_FIELDS = ["id", "title", "company", "company_id", "url", "sector",
                      "category", "family", "seniority", "saved_on", "saved_at",
                      "kind", "work_mode"];
// A posting's link is its own: 8 on the live board run past 200 characters.
const FIELD_MAX = { url: 1000 };

/* A device's clock says when it saved or unsaved, and it is all there is. A
 * stamp that is not ISO-8601 UTC is not a stamp, and one more than a day in
 * the future is not a clock running fast but garbage ("9999-12-31", "zzzz")
 * that would sort after every real stamp and never be pruned. Both are
 * dropped; a merely skewed clock is the board's to correct (nowISO()). */
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z$/;
export const FUTURE_MAX_MS = 86400000;
export function stamp(s, now = Date.now()) {
  if (typeof s !== "string" || !ISO.test(s)) return null;
  const t = Date.parse(s);
  return Number.isFinite(t) && t <= now + FUTURE_MAX_MS ? s : null;
}

/* territory {stated, states, region} and office {city, state}: the posting's
 * own shapes, each part bounded, nothing else carried. */
function cleanTerritory(t) {
  if (!t || typeof t !== "object" || Array.isArray(t)) return undefined;
  const out = { stated: t.stated === true };
  if (Array.isArray(t.states)) {
    out.states = t.states.filter((x) => typeof x === "string").slice(0, 60).map((x) => x.slice(0, 40));
  }
  if (typeof t.region === "string") out.region = t.region.slice(0, 60);
  else if (t.region === null) out.region = null;
  return out;
}
function cleanOffice(o) {
  if (o === null) return null;
  if (!o || typeof o !== "object" || Array.isArray(o)) return undefined;
  const out = {};
  if (typeof o.city === "string") out.city = o.city.slice(0, 80);
  if (typeof o.state === "string") out.state = o.state.slice(0, 40);
  else if (o.state === null) out.state = null;
  return out;
}

const when = (r) => String(r.saved_at || r.saved_on || "");

export function cleanSaved(raw, now = Date.now(), limit = MAX_SAVED) {
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
      if (f !== "id" && typeof item[f] === "string") row[f] = item[f].slice(0, FIELD_MAX[f] || 200);
    }
    if (row.saved_at !== undefined && !stamp(row.saved_at, now)) delete row.saved_at;
    // a DATE ahead of the clock is the same garbage as a stamp ahead of it:
    // merged as midnight that day, it made a role impossible to unsave until
    // then (second review, 2026-11-18)
    if (row.saved_on !== undefined && !(/^\d{4}-\d{2}-\d{2}$/.test(row.saved_on)
        && Date.parse(row.saved_on + "T00:00:00.000Z") <= now + FUTURE_MAX_MS)) delete row.saved_on;
    const t = cleanTerritory(item.territory);
    if (t !== undefined) row.territory = t;
    const o = cleanOffice(item.office);
    if (o !== undefined) row.office = o;
    if (item.quota_carrying === true) row.quota_carrying = true;
    out.push(row);
  }
  // NEWEST KEPT at the cap, as the tombstones are: a browser holding more than
  // 500 used to send its oldest 500 and drop the rest without a word. The cut
  // is the caller's: /api/saved takes them all and counts what it holds back.
  return out.sort((a, b) => when(b).localeCompare(when(a))).slice(0, limit);
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
export const TOMBSTONE_DAYS = 90;

export function cleanRemoved(raw, prior, now = Date.now()) {
  const merged = { ...(prior && typeof prior === "object" ? prior : {}) };
  if (raw && typeof raw === "object") {
    for (const [id, when] of Object.entries(raw)) {
      if (typeof id !== "string" || !stamp(when, now)) continue;
      const k = id.slice(0, MAX_ID);
      if (!merged[k] || when > merged[k]) merged[k] = when;
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
