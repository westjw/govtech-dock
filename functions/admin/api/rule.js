/* Web-admin ruling endpoint: records a decision by committing it to the repo.
 *
 * The division of labour is the whole design. This function only APPENDS to
 * ruling files - vendor scope calls, placement decisions - and the daily run
 * applies them to companies.json in Python, where validate() lives. The web
 * never edits the dataset directly, so a bug here can mis-record an opinion
 * but cannot corrupt the map.
 *
 * Auth is Cloudflare Access. The Access application covering /admin/* must
 * exist BEFORE the GITHUB_ADMIN_TOKEN secret is added: Cloudflare sets the
 * authenticated-user headers only after a request passes Access, and without
 * the app this path would be open to the world. The function refuses to work
 * when the headers are absent, so the failure mode of misconfiguration is
 * "nothing works", never "everyone can write".
 */
const REPO = "westjw/govtech-dock";

const FILES = {
  vendor: "data/vendor_scope_decisions.json",
  place: "data/placement_rulings.json",
  dismiss: "data/admin_dismissed.json",
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

const vkey = (name) =>
  String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export async function onRequestPost({ request, env }) {
  const who = request.headers.get("Cf-Access-Authenticated-User-Email");
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!who || !jwt) {
    return json({ error: "not behind Access - the /admin Access application " +
                         "is missing, so writing is refused" }, 403);
  }
  const token = env.GITHUB_ADMIN_TOKEN;
  if (!token) {
    return json({ error: "GITHUB_ADMIN_TOKEN is not configured" }, 501);
  }

  let body;
  try { body = await request.json(); } catch { return json({ error: "send JSON" }, 400); }
  const kind = body.kind;
  if (!FILES[kind]) return json({ error: "kind must be vendor, place or dismiss" }, 400);

  // Build the entries exactly the shapes the local admin writes, so the two
  // doors stay interchangeable. Every ruling carries who/when/why/what-they-saw.
  const today = new Date().toISOString().slice(0, 10);
  const entries = {};
  if (kind === "vendor") {
    const names = Array.isArray(body.names) ? body.names : [body.name];
    if (!names.every((n) => typeof n === "string" && n.trim()))
      return json({ error: "names must be non-empty strings" }, 400);
    if (!["in", "sled", "out"].includes(body.call))
      return json({ error: "call must be in, sled or out" }, 400);
    for (const name of names) {
      entries[vkey(name)] = {
        call: body.call, name, on: today, by: who,
        // null when nobody typed one, never a stand-in. This used to write
        // `bulk ruling on ${theme}`, which the why-coverage meter counted as
        // a reason - reporting care nobody took, which is the one thing that
        // meter exists to make visible. The local door was cured of exactly
        // this (admin.py act_vendor_scope_all) and this one was not, so the
        // two doors had stopped being interchangeable in the way the comment
        // above still claimed.
        why: (body.why || "").trim() || null,
        bulk: names.length > 1 || undefined,
        via: "web",
        saw: { description: body.description, website: body.website,
               source_event: body.source_event, theme: body.theme },
      };
    }
  } else if (kind === "place") {
    if (!body.id || !body.sector || !body.category)
      return json({ error: "need id, sector and category" }, 400);
    entries[body.id] = {
      sector: body.sector, category: body.category, on: today, by: who,
      why: (body.why || "").trim() || null, via: "web",
      applied: false,   // the daily run moves the company, with validation
      saw: { was: body.was, proposed: body.proposed, description: body.description },
    };
  } else {
    if (!body.key) return json({ error: "need a dismissal key" }, 400);
    // NESTED {queue: {key: rec}}, matching dismiss() in admin.py. The flat
    // "queue:key" shape here is legacy: dismissal_records() still reads both,
    // but every local writer nests now, and two shapes for one file is how the
    // metric consumers came to read only one of them.
    if (!body.queue || typeof body.queue !== "string")
      return json({ error: "need the queue this dismissal belongs to" }, 400);
    entries[body.queue] = {
      [String(body.key)]: {
        on: today, at: new Date().toISOString(), by: who, via: "web",
        why: (body.why || "").trim() || null,
        // A ruling is training data, so it carries what the person SAW.
        // Without this the agree-rate cannot count a "bucket is right" as the
        // overrule it is - and an overrule is the most informative ruling
        // there is, because it is the one where the guesser was wrong.
        saw: body.saw || undefined,
      },
    };
  }

  // Read-merge-write through the Contents API, once-retried on a sha race.
  const path = FILES[kind];
  const gh = (url, init = {}) =>
    fetch(`https://api.github.com/repos/${REPO}/${url}`, {
      ...init,
      headers: {
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "user-agent": "solesource-web-admin",
        ...(init.headers || {}),
      },
    });

  for (let attempt = 0; attempt < 2; attempt++) {
    const cur = await gh(`contents/${path}?ref=main`);
    let sha, data = {};
    if (cur.status === 200) {
      const f = await cur.json();
      sha = f.sha;
      try { data = JSON.parse(atob(f.content.replace(/\n/g, ""))); } catch { data = {}; }
    } else if (cur.status !== 404) {
      return json({ error: "could not read the ruling file" }, 502);
    }
    let added = 0;
    if (kind === "dismiss") {
      // One level deeper, because a dismissal is keyed {queue: {key: rec}}.
      // The flat "add if absent" below would have compared the QUEUE NAME and
      // dropped every dismissal after the first one in that queue, reporting
      // "already ruled - nothing to do" while writing nothing.
      for (const [q, recs] of Object.entries(entries)) {
        if (!data[q] || typeof data[q] !== "object" || Array.isArray(data[q])) data[q] = {};
        for (const [k, v] of Object.entries(recs)) {
          if (!(k in data[q])) { data[q][k] = v; added++; }
        }
      }
    } else {
      for (const [k, v] of Object.entries(entries)) {
        if (!(k in data)) { data[k] = v; added++; }
      }
    }
    if (!added) return json({ ok: true, message: "already ruled - nothing to do" });

    const put = await gh(`contents/${path}`, {
      method: "PUT",
      body: JSON.stringify({
        message: `web ruling: ${kind} x${added} by ${who}`,
        content: btoa(unescape(encodeURIComponent(JSON.stringify(data, null, 1)))),
        sha, branch: "main",
      }),
    });
    if (put.ok) {
      const out = await put.json();
      return json({ ok: true, message: `${added} ruling${added === 1 ? "" : "s"} saved`,
                    commit: out.commit && out.commit.sha });
    }
    if (put.status !== 409) {
      return json({ error: "GitHub refused the write" }, 502);
    }
    // 409: something else committed between read and write - reread and retry once
  }
  return json({ error: "the file moved twice during the write - try again" }, 409);
}

export const onRequestGet = () => json({ error: "POST a ruling" }, 405);
