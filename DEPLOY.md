# Putting SLED JOBS live — the owner's 15 minutes

Everything below needs your accounts and your card, which is why it is yours.
Everything after it is already automated.

The product is **SLED JOBS**; the domain is still `sledjobs.com` until
you buy the new one. `data/brand.json` is where both are written down, and
`functions/_brand.js` restates the four values a Cloudflare Function needs —
change the domain in both or `scripts/selftest.py` fails the build. The GitHub
repo stays `westjw/govtech-dock`; renaming it would break every URL below.

## 1. Cloudflare account and domain (~10 min)
1. <https://dash.cloudflare.com> → sign up (free plan is fine).
2. **Domain Registration → Register Domain** → buy the name
   (checked free as of 2026-08-21: `sledjobs.com`, `getsolesource.com`,
   `solesourcehq.com`; `solesource.com/.io/.co` are taken).

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
