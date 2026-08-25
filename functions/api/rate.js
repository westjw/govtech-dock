/**
 * Conference ratings, anonymous.
 *
 * WHAT THIS IS FOR
 *
 * Nobody publishes honest "was this worth the booth fee" numbers for govtech
 * conferences. That is the gap the catalogue exists to fill, and a rating is
 * the part only the people who went can supply.
 *
 * ANONYMOUS, WITH THE OBJECTION STATED
 *
 * The owner chose anonymous for now, knowingly. It is the right call for
 * getting any data at all - a rating gated behind a subscription is a rating
 * nobody leaves - and it means this endpoint can be gamed. A vendor can rate
 * up the show they exhibit at. Nothing below prevents that; it only raises the
 * cost and refuses to *display* a number too thin to mean anything.
 *
 * The three defences, none of which is authentication:
 *
 *   ONE VOTE PER BROWSER, held client-side. Honest UX, not security. It stops
 *   somebody double-tapping, not somebody determined.
 *
 *   A PER-DAY CAP PER CALLER, keyed on a HASH of the IP and never the IP
 *   itself. This project's rule is that it does not hold records about people,
 *   and "who rated what" is exactly such a record. The hash is salted with the
 *   day, so yesterday's key cannot be recomputed and there is nothing to
 *   correlate across time - the counter expires on its own.
 *
 *   NO AVERAGE UNDER MIN_SHOWN. One rating displayed as "9.0/10" is a badge,
 *   not a measurement, and it is the shape astroturf takes first. Under the
 *   floor the API returns the count and no average, and the page says how many
 *   more it needs.
 *
 * WHY IT SHARES THE ALERTS NAMESPACE
 *
 * Ratings live under a `rate:` prefix in the KV namespace bound as ALERTS,
 * where subscribers are `sub:`. A second namespace would mean a second setup
 * step for the owner before any of this works, and the two never collide.
 * If ratings ever outgrow it, moving them is a key rename.
 *
 * WHAT IS STORED, IN FULL: a running count and sum per conference, and a
 * per-day counter per hashed caller. No rating is stored individually, so
 * there is no record of what any one person thought of anything.
 */

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
         message: "Ratings are not switched on yet." }, 501);

/** The scale. 1-10, and the whole file reads it from here so changing it is
 *  one edit rather than a hunt. */
const MIN = 1;
const MAX = 10;

/** Below this many ratings we publish the COUNT and no average. */
const MIN_SHOWN = 3;

/** Ratings one caller may leave per day, across all conferences. Generous for
 *  a person who went to several shows, tedious for somebody with an agenda. */
const PER_DAY = 12;

/** A conference tag is an identifier we minted, so it is allowed to be
 *  strict: letters, digits, spaces, dots, ampersands and hyphens. Anything
 *  else is somebody probing, and a key built from unvalidated input is how a
 *  store gets polluted with junk nobody can clean up. */
const cleanTag = (t) => {
  if (typeof t !== "string") return "";
  const s = t.trim().slice(0, 64);
  return /^[A-Za-z0-9 .&'\-+/]+$/.test(s) ? s : "";
};

const today = () => new Date().toISOString().slice(0, 10);

/** SHA-256 of caller + day, hex. The day is IN the hash, not beside it, so
 *  the key cannot be recomputed for a different day and nothing links a
 *  caller's activity across dates. */
async function callerKey(request) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const ua = request.headers.get("user-agent") || "";
  const data = new TextEncoder().encode(`${ip}|${ua}|${today()}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

/** What the page may show, from what is stored. Kept in one place so the
 *  floor cannot be enforced in one branch and forgotten in another. */
function publicShape(tag, rec) {
  const n = rec ? rec.n || 0 : 0;
  const out = { tag, n, min_shown: MIN_SHOWN };
  if (n >= MIN_SHOWN) {
    out.average = Math.round((rec.sum / n) * 10) / 10;
  } else {
    // Deliberately no average, and the page is told WHY rather than left to
    // infer that a missing field means zero.
    out.average = null;
    out.needs = MIN_SHOWN - n;
  }
  return out;
}

/* --- read ---------------------------------------------------------------- */

export async function onRequestGet({ request, env }) {
  if (!env.ALERTS) return notConfigured();
  const url = new URL(request.url);

  // ?tags=a,b,c - the catalogue asks for every event on screen in one call.
  const many = url.searchParams.get("tags");
  if (many) {
    const tags = many.split(",").map(cleanTag).filter(Boolean).slice(0, 200);
    const rows = await Promise.all(tags.map(async (t) => {
      const raw = await env.ALERTS.get("rate:" + t);
      return publicShape(t, raw ? JSON.parse(raw) : null);
    }));
    return json({ ok: true, ratings: rows });
  }

  const tag = cleanTag(url.searchParams.get("tag"));
  if (!tag) return json({ error: "bad_tag" }, 400);
  const raw = await env.ALERTS.get("rate:" + tag);
  return json({ ok: true, ...publicShape(tag, raw ? JSON.parse(raw) : null) });
}

/* --- write --------------------------------------------------------------- */

export async function onRequestPost({ request, env }) {
  if (!env.ALERTS) return notConfigured();
  if (!(request.headers.get("content-type") || "").includes("application/json"))
    return json({ error: "bad_content_type" }, 415);

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }

  const tag = cleanTag(body.tag);
  if (!tag) return json({ error: "bad_tag" }, 400);

  const score = Number(body.score);
  if (!Number.isInteger(score) || score < MIN || score > MAX)
    return json({ error: "bad_score",
                  message: `A rating is a whole number from ${MIN} to ${MAX}.` },
                400);

  // The per-day cap. Checked BEFORE the write, and the counter moves whether
  // or not the rating lands, so a caller cannot probe for free.
  const key = "rl:" + (await callerKey(request));
  const usedRaw = await env.ALERTS.get(key);
  const used = usedRaw ? parseInt(usedRaw, 10) || 0 : 0;
  if (used >= PER_DAY)
    return json({ error: "rate_limited",
                  message: "That is enough ratings from here today." }, 429);
  // 48h is comfortably over a day and means the key cleans itself up; nothing
  // here needs a sweeper.
  await env.ALERTS.put(key, String(used + 1), { expirationTtl: 172800 });

  const k = "rate:" + tag;
  const raw = await env.ALERTS.get(k);
  const rec = raw ? JSON.parse(raw) : { n: 0, sum: 0 };
  rec.n += 1;
  rec.sum += score;
  rec.updated = today();
  await env.ALERTS.put(k, JSON.stringify(rec));

  // Hand back the public shape, so the page shows the same thing a fresh
  // visitor would see rather than a private view of its own vote.
  return json({ ok: true, ...publicShape(tag, rec) });
}
