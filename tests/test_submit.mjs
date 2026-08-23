/* Exercise the public submit endpoint the way Cloudflare will call it:
   real Request objects, a stubbed env, and a stubbed GitHub. */
const mod = await import(
  new URL("../functions/api/submit.js", import.meta.url).href
);

let failures = 0;
const check = (ok, label) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) failures++;
};
const post = (body, env = {}) =>
  mod.onRequestPost({
    request: new Request("https://solesourcejobs.com/api/submit", {
      method: "POST",
      body: JSON.stringify(body),
    }),
    env,
  });

// --- no token configured: says so, hands back the manual route
let r = await post({ website: "https://opengov.com" });
let j = await r.json();
check(r.status === 501 && j.error === "not_configured" && j.fallback.includes("add-company.yml"),
  "unconfigured endpoint reports it and offers the GitHub form");

// --- rejects things that are not company websites
for (const [bad, label] of [
  ["not a url", "free text"],
  ["javascript:alert(1)", "javascript: scheme"],
  ["http://localhost:8787/admin", "localhost"],
  ["https://u:p@evil.example.com", "embedded credentials"],
  ["", "empty"],
]) {
  const res = await post({ website: bad }, { GITHUB_SUBMIT_TOKEN: "x" });
  check(res.status === 400, `rejects ${label}`);
}

// --- honeypot: accepted silently, never reaches GitHub
let reached = false;
globalThis.fetch = async () => { reached = true; return new Response("{}", { status: 201 }); };
r = await post({ website: "https://opengov.com", company_fax: "spam" },
  { GITHUB_SUBMIT_TOKEN: "x" });
check(r.status === 200 && !reached, "honeypot submission never reaches GitHub");

// --- happy path: opens a labelled issue carrying only the URL and context
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = { url, init, body: JSON.parse(init.body) };
  return new Response(JSON.stringify({ number: 42, html_url: "https://github.com/x/y/issues/42" }),
    { status: 201 });
};
r = await post(
  { website: "https://www.opengov.com/products", context: "thanks @westjw, they sell to counties" },
  { GITHUB_SUBMIT_TOKEN: "secret-token" });
j = await r.json();
check(r.status === 200 && j.ok && j.number === 42, "happy path returns the issue");
check(sent.body.labels.includes("add-company"), "issue carries the add-company label");
check(sent.body.title === "Add: opengov.com", "title uses the bare hostname");
check(sent.body.body.includes("https://www.opengov.com/products"), "body carries the submitted URL");
check(!sent.body.body.includes("@westjw"), "@mention is defused, cannot ping a person");
check(sent.init.headers.authorization === "Bearer secret-token", "token is sent to GitHub only");

// --- GitHub refusing: never leaks its response to the caller
globalThis.fetch = async () =>
  new Response(JSON.stringify({ message: "API rate limit exceeded for token abc123" }), { status: 403 });
r = await post({ website: "https://opengov.com" }, { GITHUB_SUBMIT_TOKEN: "secret-token" });
j = await r.json();
check(r.status === 502 && !JSON.stringify(j).includes("abc123") && !JSON.stringify(j).includes("rate limit"),
  "a GitHub failure never leaks its detail to an anonymous caller");

// --- GET is not a submission
r = await mod.onRequestGet();
check(r.status === 405, "GET is refused");

console.log(failures ? `\n${failures} FAILURES` : "\nall submit-endpoint checks passed");
process.exit(failures ? 1 : 0);
