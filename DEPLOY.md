# Putting SLED JOBS live — the owner's 15 minutes

Everything below needs your accounts and your card, which is why it is yours.
Everything after it is already automated.

The product is **SLED JOBS** and the domain is **`sledjobs.com`**, bought
2026-09-02. `solesourcejobs.com` is being kept for a separate FEDERAL hiring
board. `data/brand.json` is where the name and domain are written down, and
`functions/_brand.js` restates the four values a Cloudflare Function needs —
change the domain in both or `scripts/selftest.py` fails the build. The GitHub
repo stays `westjw/govtech-dock`; renaming it would break every URL below.

## 1. Cloudflare account and domain (~10 min)
1. <https://dash.cloudflare.com> → sign up (free plan is fine).
2. **Domain Registration → Register Domain** → buy the name.
   Done: `solesourcejobs.com` (2026-08), `sledjobs.com` (2026-09-02). The
   board runs on the second; the first is held for a federal board.

## 1b. Moving the site to sledjobs.com (done in code 2026-09-02; the dashboard half is yours)

The repository already says `sledjobs.com`. What is left is three things in
the Cloudflare dashboard, **in this order**, because step 2 is a security step.

**Known before you start**, checked 2026-09-02: `sledjobs.com` is already on
Cloudflare nameservers (`theo` and `adele`, the same pair as the old domain),
so the zone is in your account and Cloudflare writes the DNS itself. It has no
A or CNAME record yet, so it currently resolves to nothing. Nobody can reach
it until step 1.

1. **~~Pages → Custom domains~~ — DONE, verified 2026-09-03.** `sledjobs.com`
   and `www.sledjobs.com` both resolve to Cloudflare proxy IPs and serve this
   Pages project (HTTP 200, the board's own markup, the same deployment stamp
   as the old domain), and `solesourcejobs.com` is still attached on its own
   IPs so every alert link already mailed still resolves. Commits pushed on
   2026-09-03 appeared on `sledjobs.com/c/axon` about twenty seconds later,
   which only happens through an attached custom domain.

   **THE PARAGRAPH BELOW WAS STALE FOR A DAY AND WAS COPIED INTO A TO-DO LIST
   TWICE**, alongside step 2, which was also already done. Both were written
   before the work happened and neither was re-checked. Verify with step 3's
   two curls before believing any status in this file: this project's own rule
   is that a document is not evidence, and that applies to this document.

   The original instructions, kept for the next hostname: dash.cloudflare.com → **Workers & Pages** →
   the Pages project (`solesource`) → **Custom domains** → *Set up a custom
   domain* → type `sledjobs.com` → **Activate domain**. Because the zone is in
   the same account, Cloudflare creates the record itself — there is no DNS
   step for you. Repeat for `www.sledjobs.com` if you want www to answer.
   **Leave `solesourcejobs.com` attached.** Alert emails already sent carry
   `solesourcejobs.com/alerts?t=…`, and detaching it breaks them.

2. **~~EXTEND ACCESS TO THE NEW HOSTNAME~~ — DONE, verified 2026-09-03.**
   All three hostnames answer `/admin` with a 302 to the same Access
   application: the `aud` claim in the redirect token is identical
   (`80b769d2…`) on `solesourcejobs.com`, `sledjobs.com` and
   `www.sledjobs.com`, while the `hostname` claim differs per host. Following
   the redirect returns an Access login page carrying no admin markup. A
   Pages custom domain inherits the project's Access policy, so adding the
   domains covered this; the step below was never needed and this file said
   otherwise for a day. **Re-verify with the curl in step 3 after adding any
   new hostname** rather than trusting this paragraph.

   The original instruction, kept because it is what to do if a future
   hostname ever answers 200: your Access application
   is scoped to `solesourcejobs.com` — verified live: a request to
   `/admin` there redirects to `solesource-c6g-pages.cloudflareaccess.com`
   with `hostname: solesourcejobs.com` in the token. **A new custom domain is
   NOT covered by it.** Until you do this, `sledjobs.com/admin` is reachable
   by anyone.
   Zero Trust → **Access → Applications** → open the existing application →
   **Add a domain / hostname** → `sledjobs.com`, path `admin` → save. Keep the
   old hostname on the same application; one application can hold both.
   Writes would still be refused without Access headers — the ruling endpoint
   fails closed — but the queue pages would be readable, so do not leave a gap.

3. **Verify, from any machine:**
   ```
   curl -s -o /dev/null -w "%{http_code}\n" https://sledjobs.com/          # expect 200
   curl -s -o /dev/null -w "%{http_code}\n" https://sledjobs.com/admin     # expect 302
   ```
   200 then 302 means the site is live and the admin is behind Access. A 200
   on the second is the gap in step 2.

**The sending address moved late on 2026-09-02 (2026-09-03 UTC, commit 6168048).** Resend verified `sledjobs.com`
(SPF and DKIM added in Cloudflare DNS for the new zone) and `from_email` in
`data/brand.json` and `FROM` in `functions/_brand.js` were changed together
and pushed. Nothing here still sends from solesourcejobs.com.

**Later, when the federal board takes solesourcejobs.com**, remove it from
this Pages project's custom domains first. Every alert link already mailed
breaks at that moment; one confirmation email has ever been sent, so that is
one address.

## 1c. The pages.dev alias was never behind Access — and now it does not matter

Measured 2026-09-25: `https://solesource-c6g.pages.dev/admin/`,
`/admin/data.json`, `/admin/rulings.json` and `/admin/users.json` all answered
**200 with no sign-in**, while the same paths on `sledjobs.com` 302'd to
Access. §1b's claim that a custom domain "inherits the project's Access
policy" is true of the custom domain and false of the `*.pages.dev` alias
(and of preview deployments). Re-measured 2026-10-05: per-deployment and
preview URLs now answer a sign-in from a separate `*.solesource-c6g.pages.dev`
application. The production alias still answers without one.

The fix is in code, so it holds on every hostname: `functions/admin/
_middleware.js` verifies the Access JWT (`Cf-Access-Jwt-Assertion` header or
the `CF_Authorization` cookie) against
`https://solesource-c6g-pages.cloudflareaccess.com/cdn-cgi/access/certs`,
requires the application audience
`80b769d2980cc241d0df575dd9fc1f67d68c6e23eb2e3065396e6ecac95cead9`, and
refuses everything else. Verified after deploy: pages.dev answers **403**,
sledjobs.com still redirects to Access, `whoami` still answers.

Two things to know:

- **If the Access application is ever recreated, its AUD changes** and every
  admin request answers 403 naming the variable. Zero Trust → Access →
  Applications → the app → Overview → *Application Audience (AUD) Tag*; set
  it as the Pages variable `ACCESS_AUD` (and `ACCESS_TEAM_DOMAIN` if the team
  domain changes). No redeploy of code is needed, but a Function reads a new
  variable only after the next deployment (§3b).
- **Optionally also add the alias to the Access application** in the
  dashboard. Belt and braces; the middleware no longer depends on it.

## 2. Pages project (~3 min)
1. **Workers & Pages → Create → Pages → Upload assets** is NOT what we want —
   choose **Connect to Git** instead, pick `westjw/govtech-dock`.
2. Build command: `python3 scripts/build_site.py` · output directory: `public`.
3. Name the project (e.g. `solesource`). First deploy runs on its own.
4. **Custom domains** tab → add the domain you bought. Cloudflare wires DNS
   itself since it is the registrar.

## 3. Signed-in only until launch (decided 2026-10-05)
The whole site stays private until it is ready to go live. Two doors guard it,
and both check a sign-in from the SAME Access application, the one that
already guards `/admin`. So there is no new secret, variable or application.

**The code door: `functions/_gate.js`. Done in code.** It runs first in
`functions/_middleware.js` on every request and every hostname, including
`solesource-c6g.pages.dev`, which Access does not cover (§1c). It only works
while the Function runs: see step 1 below.
- A visitor without a verified sign-in gets a short holding page ("SLED JOBS
  opens soon", with a Sign in link). An endpoint refuses with JSON 403.
- Only `/admin/api/login` and `/admin/api/whoami` stay open, the same two the
  `/admin` door leaves open - plus a mail client's one-click unsubscribe
  (a form POST to `/api/alerts?t=`), because digests keep going out while the
  site is private. That exception only matters while Access covers just
  `/admin`: after step 3 below, Access answers first and the one-click button
  in mail clients cannot work until launch (a second Access application with
  a Bypass would fix it; not worth it for one subscriber).
- `check_the_site_is_signed_in_only_until_launch` in `scripts/selftest.py`
  drives it through `scripts/gate_harness.mjs`.

**Your ~3 minutes in the dashboard. Step 1 is required.**
1. **Fail closed.** dash.cloudflare.com → **Workers & Pages** → `solesource` →
   **Settings** → **Runtime** → **Fail open / closed** → **Fail closed**. Save.
   - Why: this account is on the Workers Free plan, which allows 100,000
     Functions requests a day. Once that is spent, a project left on "Fail
     open" serves every static file without running any Function. That means
     no gate and no `/admin` door, including `/admin/users.json`, until
     midnight UTC.
   - Refused requests count toward the allowance, so anyone can spend it on
     purpose against the pages.dev alias. Found in review, 2026-10-05. The
     `/admin` door has had the same exposure since 2026-09-25.
   - The cost: if the allowance is spent, everyone, you included, gets a
     Cloudflare error page until midnight UTC. Workers Paid removes the daily
     limit, and with it the need for this setting.
   - curl cannot see this setting, so check it in the dashboard.
2. dash.cloudflare.com → **Zero Trust** → **Access controls** → **Applications**.
   Open the EXISTING application, the one whose hostnames are `sledjobs.com`,
   `www.sledjobs.com` and `solesourcejobs.com`, each with path `admin`.
   - **Do not create a new application.** The code accepts exactly one
     audience (AUD) tag (`functions/_access.js`), so two applications on the
     same hostnames cannot both work. If a second one gets created anyway,
     delete it and do step 3 on the original.
   - Only if the original itself is gone: clear the Path on its replacement as
     in step 3, set the Pages variable `ACCESS_AUD` to the replacement's tag,
     and redeploy.
   - There is a SECOND, separate application, for `*.solesource-c6g.pages.dev`
     (measured 2026-10-05). It is what puts a sign-in in front of
     per-deployment and preview URLs. Leave it alone. Its sign-ins carry a
     different tag, so those URLs show the holding page even after you sign
     in. Use sledjobs.com.
3. **Edit** → under its public hostnames, clear the **Path** field (`admin`)
   on all three rows, so each one covers the whole hostname. Save.
   - Add no wildcard such as `*.sledjobs.com`. SLED HQ will live at
     `hq.sledjobs.com`, and its jobs feed must stay reachable for the
     nightly workflow.
4. Leave the policy as it is: your email, one-time PIN. Whoever that policy
   admits sees the whole site, so add an employee there and nowhere else.
5. In the application's settings, check that **Cookie Path Attribute** is OFF.
   If it is on, a sign-in at `/admin` does not carry to the
   rest of the site, and the holding page keeps asking you to sign in.

Steps 2 and 3 and the code deploy can come in either order. Until step 3,
the code door alone guards every hostname, and signing in goes through
`/admin`. That holds only with step 1 done. Until the code deploys, Access
guards the custom domains and the alias stays open. Do step 1 first, either
way.

**Verify, from any machine:**
```
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://sledjobs.com/
curl -s -o /dev/null -w "%{http_code}\n" https://solesource-c6g.pages.dev/
curl -s -o /dev/null -w "%{http_code}\n" https://solesource-c6g.pages.dev/data/board.json
```
- The first should answer 302 to `solesource-c6g-pages.cloudflareaccess.com`,
  which is Access. A 403 means only the code door is up: the dashboard step
  is not done yet.
- The second and third should answer 403, which is the code door on the alias.
- In the dashboard, Settings → Runtime should show **Fail closed** (step 1).
- Then, in a browser, open sledjobs.com, enter the emailed code, and the
  board loads.

**What keeps running:** the nightly workflows, since none of them read the
live site. Alert digests also keep going: one subscriber, and its links work
once signed in.
**What stops:** search indexing, because every page answers with a sign-in,
and the public forms (alerts, add a company, claim).
**At launch:** run `python3 scripts/selftest.py` and see "all checks
passed" (Pages deploys a push without running it; `selftest.yml` reports on
the push afterwards), set `GATED = false` in `functions/_gate.js`, push, wait
for the deploy, then put path `admin` back on the three hostnames.
**Before that, enforce the content security policy** (added 2026-10-09). It
ships REPORTING: browsers run everything and print what they would block in
the console. While still signed in, open the board, a company page, a
conference page, /alerts and /claim on sledjobs.com with the browser's
console open; any line starting "[Report Only] Refused" is something the
policy would break. If there are none, set `CSP_MODE = "enforce"` in
`functions/_csp.js` and push. selftest refuses `GATED = false` while the
policy only reports. In the Cloudflare dashboard, Rocket Loader must stay
off: it rewrites the page's scripts and their hashes stop matching.

**Going private again, if launch shows a problem (about two minutes):**
1. Set `GATED = true` in `functions/_gate.js` and push. Every page, data file
   and endpoint shows the holding page again one deploy later. Or, faster
   and with no push: Pages → Deployments → the last gated deployment →
   **Rollback to this deployment**.
2. Clear the path `admin` on the Access application's hostnames again, so
   signing in covers the whole site (step 3 above).
3. Check: `curl -s -o /dev/null -w "%{http_code}\n" https://sledjobs.com/`
   answers 302 (Access) or 403 (the code gate), never 200.

## 2b. www.solesourcejobs.com loops on sign-in — OPEN, verified 2026-09-03
Signing in at `www.solesourcejobs.com/admin` ends in ERR_TOO_MANY_REDIRECTS.
A redirect rule on that zone sends every www path to the apex, **including
Access's own callback**:

    curl -s -o /dev/null -w '%{http_code} %{redirect_url}' \
      'https://www.solesourcejobs.com/cdn-cgi/access/authorized?nonce=test'
    301 https://solesourcejobs.com/cdn-cgi/access/authorized?nonce=test

Access mints the nonce for the hostname the login STARTED on. Moving the
callback to a different hostname invalidates it, so Access begins again and
the browser loops. The other three hostnames answer 400 to that fake nonce,
which is correct: they process it themselves. `www.sledjobs.com` is fine
because it is a real Pages custom domain rather than a redirect.

**Fix (owner):** add `www.solesourcejobs.com` as a Pages custom domain on
`solesource`, the same as `www.sledjobs.com` — or exclude `/cdn-cgi/*` from
the redirect rule. Then clear cookies for the zone. **Until then, sign in at
`sledjobs.com/admin`.** Re-check with the curl above: anything other than a
301 means it is fixed.

## 3b. The "Add a company" form ~~(~3 min)~~ DONE, verified 2026-09-03
`GITHUB_SUBMIT_TOKEN` is a Pages secret on `solesource` and the form works:
a POST to `/api/submit` opened issue #12. **A Pages Function reads a new
variable only after the NEXT deployment** - saving the secret is not enough,
and the endpoint answered `not_configured` until a push redeployed. The
token is fine-grained, `westjw/govtech-dock` only, Issues read and write.
**Check its expiry before it lapses**; when it does, the form silently goes
back to the fallback link. The original instructions follow. The endpoint
(`functions/api/submit.js`) opens a GitHub issue and needs a token:
GitHub → Settings → Developer settings → Fine-grained tokens → one
repository (`westjw/govtech-dock`), permission **Issues: Read and write**
only. Then Cloudflare Pages → the project → Settings → Variables and
Secrets → add **encrypted** variable `GITHUB_SUBMIT_TOKEN`. Then push or
redeploy: a Function reads a new variable only after the next deployment
(measured 2026-09-03, above; an older line here said the opposite). Verify:
`curl -s -X POST https://sledjobs.com/api/submit -H 'content-type: application/json' -d '{"website":"https://example.com/"}'`
answers with an issue URL instead of `not_configured`. Until then the form's
fallback link opens the same issue template by hand, which works.

## 3c. Log in, and who may reach what (nothing to set up)
Access already covers `/admin` on every hostname (step 2 above), and the
site's account menu now offers **Log in**, which is `/admin/api/login` -
Access asks for a code by email, then sends the person back. Who they may
then reach is the owner's ruling on the desk admin's **Users** tab: `admin`
(the web admin) and `hunter` (the closed Job Hunter beta at
`/admin/hunter/`). `data/users.json` carries a handle, roles and a hash of
the address, never the address; the build refuses to ship a row with an
`@` in it. Verify: signed out, `curl -s -o /dev/null -w "%{http_code}"
https://sledjobs.com/admin/api/whoami` is a 302 to Access; signed in, the
account menu names your handle and shows the doors your roles open.

## 4. Deploying: already handled, and deliberately only one way

Cloudflare Pages is connected to the repository and builds on every push to
main. Its build command is `python3 scripts/build_site.py`, so the sanity
gate runs in the real deploy path: verified 2026-08-23 by feeding it a board
collapsed to 50 postings, which exits 1, fails the build, and leaves the
previous site up.

The workflow used to carry its own deploy step, gated on a variable. It is
gone. Two doors to production is worse than one, and the door that skips
silently when a variable is unset is the one nobody checks.

The CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID secrets are now unused by
any workflow. Nothing breaks if you leave them, but deleting them removes two
credentials that no longer do anything.

## 5. Going public (~5 min)

Do these in order. The first one matters most.

1. **Delete the stale deployments.** Pages → the project → Deployments:
   remove `eaffc723` and `5394c974`. Those were built by a workaround that
   published the whole repo rather than the allowlisted `public/`, and each
   Cloudflare deployment keeps its own permanent hash URL. Behind Access that
   was contained. Without Access it is a live copy of everything.
2. **Repo → Settings → General → bottom → Change visibility → Public.**
   Audited 2026-08-23: no tokens or keys in the tree or in the whole git
   history, no `.env` ever committed, no per-company prospecting notes, no
   personal email in the data. What becomes readable is company facts, public
   job postings, and the conference catalog.

   **Re-check three files that did not exist when that audit ran.** All three
   are committed, so making the repo public publishes them and their history:
   `data/admin_journal.jsonl` (a before-image plus a free-text `why` for every
   admin write), `data/identity_labels.jsonl` (what you said when the website
   check was wrong), and the `notes` field on companies in
   `data/companies.json` (free text you type while ruling). They are clean as
   of 2026-08-24 — no notes recorded at all, and the journal `why` lines are
   plain facts — but they are the three places a private thought would land
   from now on. Skim them before you flip, and again before any later flip.
   None of the three reach the website: `build_site.py` ships `board.json`,
   `sectors.json` and `brand.json` and nothing else.
3. **Take the sign-in off the site (§3, "At launch").** Set `GATED = false`
   in `functions/_gate.js` and push. Once it has deployed, put path `admin`
   back on the Access application's three hostnames. Do NOT delete the
   application: it still guards `/admin`. Do this last, after 1 and 2.

Making the repo public is also what switches ON public submissions: the
`add-company` issue template and its workflow (issue → a bot researches the
company and opens a pull request → you merge) cannot be used by anyone who
cannot see the repo.

## 6. The in-page submission form (~3 min, optional)

`functions/api/submit.js` lets someone add a company **without** a GitHub
account: the form on the site opens the same issue the template does. It works
with no setup, degrading to "submit it on GitHub instead" - so the only thing
this step buys is that a visitor never has to leave the site or make an
account.

- GitHub → Settings → Developer settings → **Fine-grained tokens** → new token,
  scoped to **this repository only**, permission **Issues: Read and write**,
  nothing else. Set an expiry you will actually renew.
- Cloudflare Pages → the project → Settings → **Variables and Secrets** → add
  `GITHUB_SUBMIT_TOKEN` as an **encrypted** variable (Production, and Preview
  if you want it there too), then redeploy so it takes effect.

The endpoint refuses anything that is not an http(s) URL with a real hostname,
carries a honeypot field against form bots, defuses `@mentions` so a
submission cannot ping anyone, and never passes GitHub's error text back to an
anonymous caller. Nothing it receives reaches the dataset: the bot re-derives
every field from the company's own site, and merging stays a human action.

## 7. The web admin (~5 min, two steps IN ORDER)

sledjobs.com/admin is the judgment half of the admin - Vendor scope
and Wrong bucket - workable from any browser, phone included. Rulings
commit to the repo as you, and the daily run applies them with validation.
It ships fail-closed: until both steps below are done, the page is
read-only and every ruling is refused.

1. **Access first.** Zero Trust -> Access -> Applications -> Add ->
   Self-hosted. Application domain: `sledjobs.com`, path: `admin`.
   Policy: your email (and later your employee's), one-time PIN. This is
   what makes the ruling endpoint trust the request; without it, writes are
   refused with "not behind Access".
2. **Then the token.** GitHub -> Settings -> Developer settings ->
   Fine-grained tokens -> new token scoped to ONLY westjw/govtech-dock with
   **Contents: Read and write** (this is more power than the submit token -
   it can write repo files - which is why Access must exist first).
   Cloudflare Pages -> solesource -> Settings -> Variables and Secrets ->
   add `GITHUB_ADMIN_TOKEN`, encrypted, Production. Redeploy to take effect.

Step 1 is done (1b, verified 2026-09-03), so this window is closed; the
paragraph is kept for what it protected. Before it, the /admin page was
publicly viewable. It shows
only company names, descriptions and queue proposals - the same facts the
public board serves - and nothing on it can write.

That is all of it. Nothing in this file can be done from this machine without
your credentials, which is the correct reason it has not been done.
