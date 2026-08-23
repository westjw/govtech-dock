/* Public "add a company" endpoint, for people without a GitHub account.
 *
 * It does not write to the dataset. It opens an ISSUE, which is the front
 * door the add-company workflow already watches: a bot derives the name,
 * sector, category and job board itself, opens a pull request, and a person
 * merges it. A submission is a claim, not a fact, and that stays true whether
 * it arrives from a GitHub user or from this form.
 *
 * The only thing trusted out of this request is the URL, and only as far as
 * "is this shaped like a link" - the workflow re-derives everything from the
 * page itself.
 *
 * Setup (one time, and optional): create a fine-grained GitHub token with
 * Issues:write on this one repository, and add it to the Cloudflare Pages
 * project as an encrypted variable named GITHUB_SUBMIT_TOKEN. Without it this
 * endpoint reports that it is not configured and the site falls back to the
 * GitHub issue form, so the feature works either way.
 */
const REPO = "westjw/govtech-dock";
const MAX_CONTEXT = 1200;

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

/* Submitted text is rendered on a GitHub issue. Cap it, drop control
 * characters, and defuse @mentions so a submission cannot ping a person or a
 * team by writing their handle into the box. */
function clean(text) {
  return String(text || "")
    .slice(0, MAX_CONTEXT)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .replace(/@([A-Za-z0-9-])/g, "@\u200b$1")
    .trim();
}

function validUrl(raw) {
  let u;
  try {
    u = new URL(String(raw || "").trim());
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  // A hostname with a dot and no credentials. Keeps out localhost, IPs with
  // embedded auth, and the javascript: family that never reaches here anyway.
  if (!u.hostname.includes(".") || u.username || u.password) return null;
  return u;
}

export async function onRequestPost({ request, env }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "send JSON" }, 400);
  }

  // Honeypot: a field hidden from people and irresistible to form bots. A
  // filled one is silently accepted so the bot does not learn to try again.
  if (body.company_fax) return json({ ok: true, queued: true });

  const url = validUrl(body.website);
  if (!url) {
    return json({ error: "That does not look like a company website. Include https://" }, 400);
  }
  const context = clean(body.context);

  const token = env.GITHUB_SUBMIT_TOKEN;
  const issueBody =
    `${url.href}\n\n` +
    (context ? `**Anything the bot will get wrong**\n\n${context}\n\n` : "") +
    `---\nSubmitted from the public form on solesourcejobs.com. ` +
    `Nothing here is trusted: the bot derives every field from the site itself, ` +
    `and a person reviews the pull request.`;

  if (!token) {
    // Not configured yet. Say so plainly and hand back the manual route
    // rather than pretending the submission landed somewhere.
    return json(
      {
        error: "not_configured",
        fallback: `https://github.com/${REPO}/issues/new?template=add-company.yml`,
      },
      501
    );
  }

  const res = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      accept: "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "solesource-submit",
    },
    body: JSON.stringify({
      title: `Add: ${url.hostname.replace(/^www\./, "")}`,
      body: issueBody,
      labels: ["add-company"],
    }),
  });

  if (!res.ok) {
    // Never surface GitHub's response: it can carry rate-limit detail and the
    // token's own scopes back to an anonymous caller.
    return json(
      {
        error: "GitHub would not accept the submission just now.",
        fallback: `https://github.com/${REPO}/issues/new?template=add-company.yml`,
      },
      502
    );
  }
  const issue = await res.json();
  return json({ ok: true, number: issue.number, url: issue.html_url });
}

export const onRequestGet = () => json({ error: "POST a JSON body" }, 405);
