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
  // Two opinion files the daily run applies in Python, behind validate().
  // A merge and a founding year both WRITE companies.json, which a Worker
  // must never do: the whole division of labour here is that a bug in the
  // web half can mis-record an opinion and cannot corrupt the map.
  merge: "data/web_merge_rulings.json",
  founded: "data/web_founded_rulings.json",
  // A GRANT, from a phone. The address is hashed HERE and only the hash is
  // written; the nightly run lands it in users.json through act_user_grant,
  // so a grant made on Sunday opens the door on Monday. Revoking is not
  // offered here on purpose: a revoke that waits a night is not a revoke,
  // and the instant one is the Access policy in the dashboard.
  user: "data/web_user_rulings.json",
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

const vkey = (name) =>
  String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/* WHO IS RULING, as a handle and never as an address.
 *
 * Access hands this function the signed-in person's real email. It used to
 * travel straight into the ruling record and into the commit message, in a
 * repository that is PUBLIC - raw.githubusercontent serves every ruling file
 * to anyone. That is the write the rest of this project refuses everywhere
 * else: users.json holds email_sha256 and no address, whoami.js answers with
 * a handle, and build_site.py REFUSES TO BUILD if a users.json row contains
 * an "@". One door was writing what three others were built to keep out.
 *
 * The same lookup whoami.js already does gives the handle, and it does a
 * second job the write path was skipping entirely: it says whether this
 * person may rule at all. Access proves somebody is signed in; the Users
 * board is what grants "admin", and nothing here ever asked. Fails closed -
 * a users.json that cannot be read grants nothing. */
async function sha256(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function ruler(request, env, email, need = "admin") {
  const key = await sha256(String(email).trim().toLowerCase());
  let users = {};
  try {
    const res = await env.ASSETS.fetch(new URL("/admin/users.json", request.url));
    if (res.ok) users = await res.json();
  } catch (e) { users = {}; }
  for (const [handle, u] of Object.entries(users || {})) {
    if (u && u.email_sha256 === key && !u.revoked_on) {
      const roles = Array.isArray(u.roles) ? u.roles : [];
      // "owner" covers everything; "admin" covers rulings. A grant needs the
      // owner: the Users board is the one door that decides who else may rule.
      if (roles.includes("owner")) return handle;
      if (need === "admin" && roles.includes("admin")) return handle;
      return null;
    }
  }
  return null;
}

export async function onRequestPost({ request, env }) {
  const email = request.headers.get("Cf-Access-Authenticated-User-Email");
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!email || !jwt) {
    return json({ error: "not behind Access - the /admin Access application " +
                         "is missing, so writing is refused" }, 403);
  }
  let body;
  try { body = await request.json(); } catch { return json({ error: "send JSON" }, 400); }
  const kind = body.kind;
  if (!FILES[kind]) return json({ error: "kind must be vendor, place, dismiss, merge, founded or user" }, 400);

  // THE HANDLE IS THE ONLY FORM OF THE PERSON THAT MAY BE STORED.
  const who = await ruler(request, env, email, kind === "user" ? "owner" : "admin");
  if (!who) {
    return json({ error: kind === "user"
      ? "only the owner grants access"
      : "signed in, but the Users board has not granted you admin. Ask the owner to add you." }, 403);
  }
  const token = env.GITHUB_ADMIN_TOKEN;
  if (!token) {
    return json({ error: "GITHUB_ADMIN_TOKEN is not configured" }, 501);
  }

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
  } else if (kind === "merge") {
    // keep and drop, never "merge these two" - which record survives is the
    // ruling, and a merge that picked for you is one nobody can check later.
    if (!body.keep || !body.drop || body.keep === body.drop)
      return json({ error: "need keep and drop, and they must differ" }, 400);
    entries[`${body.keep}<-${body.drop}`] = {
      keep: body.keep, drop: body.drop, on: today, by: who, via: "web",
      why: (body.why || "").trim() || null,
      applied: false,
      saw: { keep_name: body.keep_name, drop_name: body.drop_name,
             signal: body.signal },
    };
  } else if (kind === "founded") {
    const yr = parseInt(body.year, 10);
    // A year outside this range is a typo or a parse of something that was
    // not a year. Refusing here keeps it out of the opinion file entirely,
    // rather than leaving Python to reject it tomorrow.
    if (!body.id || !Number.isInteger(yr) || yr < 1800 || yr > new Date().getFullYear())
      return json({ error: "need an id and a plausible four-digit year" }, 400);
    entries[body.id] = {
      year: yr, on: today, by: who, via: "web",
      why: (body.why || "").trim() || null,
      applied: false,
      saw: { name: body.name, source: body.source },
    };
  } else if (kind === "user") {
    const handle = String(body.handle || "").trim().toLowerCase();
    const addr = String(body.email || "").trim();
    const roles = Array.isArray(body.roles) ? body.roles.filter((r) => ["admin", "hunter"].includes(r)) : [];
    if (!/^[a-z][a-z0-9-]{1,23}$/.test(handle))
      return json({ error: "a handle is 2-24 characters: letters, digits, hyphens, starting with a letter" }, 400);
    if (!addr.includes("@") || !addr.split("@").pop().includes("."))
      return json({ error: "a grant needs the person's email address; it is hashed here and never stored" }, 400);
    if (!roles.length) return json({ error: "grant admin, hunter, or both" }, 400);
    // THE ADDRESS STOPS HERE. Only its hash goes into the record, the commit
    // message names the handle, and the applier hands act_user_grant the
    // hash - nothing on the way to users.json ever holds the address.
    entries[handle] = {
      handle, email_sha256: await sha256(addr.toLowerCase()), roles,
      label: String(body.label || "").trim().slice(0, 80),
      on: today, by: who, via: "web", applied: false,
      why: (body.why || "").trim() || null,
    };
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
