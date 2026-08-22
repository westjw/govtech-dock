# Putting SoleSource live — the owner's 15 minutes

Everything below needs your accounts and your card, which is why it is yours.
Everything after it is already automated.

## 1. Cloudflare account and domain (~10 min)
1. <https://dash.cloudflare.com> → sign up (free plan is fine).
2. **Domain Registration → Register Domain** → buy the name
   (checked free as of 2026-08-21: `solesourcejobs.com`, `getsolesource.com`,
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

## 4. Let the daily refresh deploy itself (~2 min, optional)
The workflow already contains a deploy step that skips until these exist:
- Repo **Settings → Secrets and variables → Actions**:
  - secret `CLOUDFLARE_API_TOKEN` (dash → My Profile → API Tokens →
    "Edit Cloudflare Workers" template works, or a custom token with
    Pages:Edit)
  - secret `CLOUDFLARE_ACCOUNT_ID` (dash home, right sidebar)
  - variable `CLOUDFLARE_PROJECT` = the Pages project name from step 2
Once set, every 06:00 refresh publishes - behind the sanity gate, which
refuses a board that shrank more than 25% overnight.

That is all of it. Nothing in this file can be done from this machine without
your credentials, which is the correct reason it has not been done.
