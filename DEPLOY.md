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

**The sending address has NOT moved and must not yet.** Resend has
`solesourcejobs.com` verified with SPF and DKIM in this same Cloudflare DNS
and has never seen `sledjobs.com`. Moving `from_email` before the new domain
is verified there makes every alert fail to send while the endpoint still
answers 200. To move it: Resend → **Domains → Add Domain** → `sledjobs.com` →
it prints DKIM and SPF records → add them in Cloudflare DNS for the new zone →
wait for **Verified** → then change `from_email` in `data/brand.json` and
`FROM` in `functions/_brand.js` together, and push.

**Later, when the federal board takes solesourcejobs.com**, remove it from
this Pages project's custom domains first. Every alert link already mailed
breaks at that moment; one confirmation email has ever been sent, so that is
one address.

## 2. Pages project (~3 min)
1. **Workers & Pages → Create → Pages → Upload assets** is NOT what we want —
   choose **Connect to Git** instead, pick `westjw/govtech-dock`.
2. Build command: `python3 scripts/build_site.py` · output directory: `public`.
3. Name the project (e.g. `solesource`). First deploy runs on its own.
4. **Custom domains** tab → add the domain you bought. Cloudflare wires DNS
   itself since it is the registrar.

## 3. Password protection while you soft-launch (~2 min)
**Zero Trust → Access → Applications → Add** → type Self-hosted → your domain
→ policy: Emails ending in your address, or a one-time PIN. Free for up to 50
users, and real auth rather than a shared password.

## 3b. The "Add a company" form (~3 min, optional but visible)
**Verified live 2026-09-03: the form answers `not_configured` and shows
"The form is not wired up on this deployment yet."** The endpoint
(`functions/api/submit.js`) opens a GitHub issue and needs a token:
GitHub → Settings → Developer settings → Fine-grained tokens → one
repository (`westjw/govtech-dock`), permission **Issues: Read and write**
only. Then Cloudflare Pages → the project → Settings → Variables and
Secrets → add **encrypted** variable `GITHUB_SUBMIT_TOKEN`. Redeploy is
not needed for Functions variables. Verify:
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
3. **Zero Trust → Access → Applications → delete the Access application you
   made in §3 (Password protection).**
   That takes the login gate off the domain. Do it last, after 1 and 2.

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

Until step 1 is done the /admin page itself is publicly viewable. It shows
only company names, descriptions and queue proposals - the same facts the
public board serves - and nothing on it can write. Do step 1 promptly
anyway.

That is all of it. Nothing in this file can be done from this machine without
your credentials, which is the correct reason it has not been done.
