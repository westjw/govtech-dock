/* THE SIGN-IN CARD'S LOOK, both themes, and the email form, in one place.
 *
 * Two places draw the card: the gate's "Sign in to see this page"
 * (functions/_gate.js holdingPage) and the sign-in pages
 * (functions/_signin_page.js). Both import from here, and this file imports
 * neither, so the two can never drift apart (and nothing loops).
 *
 * LIGHT AND DARK (owner, 2026-11-14: "I want white/dark options on all of
 * them"). The board is dark unless its reader switched (index.html, "Switch
 * theme", localStorage "sled-theme"). These pages run no script, so the
 * board also writes its choice to a cookie of the same name, and the server
 * reads that: dark unless it says light, as on the board. Each card carries
 * its own switch, /signin/theme, which sets the same cookie, and the board
 * picks that up too. Colours are the board's own tokens (index.html :root and
 * its dark block); the Google button follows Google's light and dark button
 * guidelines.
 */
export const THEME_COOKIE = "sled-theme";

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* "light" or "dark" for this request: the cookie, else dark. Only ASCII
 * space and tab are trimmed, as a browser writes the header. */
export function themeOf(request) {
  const raw = (request && request.headers && request.headers.get("Cookie")) || "";
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    if (part.slice(0, i).replace(/^[ \t]+|[ \t]+$/g, "") !== THEME_COOKIE) continue;
    return part.slice(i + 1).replace(/^[ \t]+|[ \t]+$/g, "") === "light" ? "light" : "dark";
  }
  return "dark";
}

export const THEME_CSS = `
  :root{color-scheme:light;--bg:#E8F1F7;--panel:#FAF7F0;--line:#C9DCE8;--ink:#1F2536;--dim:#556F82;
    --accent:#0B57C4;--on-accent:#E8F1F7;--accent-hover:#1F2536;--link:#0B57C4;--field:#FFFFFF;
    --field-line:#9FB3C4;--note:#FFFFFF;--tile:transparent;--beak:#F5A623;
    --g-bg:#FFFFFF;--g-line:#747775;--g-ink:#1F1F1F;--g-hover:#F2F2F2}
  :root[data-theme=dark]{color-scheme:dark;--bg:#1F2536;--panel:#262E42;--line:#39435C;--ink:#E8F1F7;
    --dim:#A8BCCA;--accent:#478EF5;--on-accent:#161B29;--accent-hover:#76ABF9;--link:#76ABF9;
    --field:#1F2536;--field-line:#4A5C71;--note:#2E3852;--tile:#E8F1F7;
    --g-bg:#131314;--g-line:#8E918F;--g-ink:#E3E3E3;--g-hover:#1F1F20}`;

export const CARD_CSS = `${THEME_CSS}
  *,*::before,*::after{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:18px/1.55 Archivo,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  main{max-width:656px;margin:0 auto;padding:120px 48px;display:flex;justify-content:center}
  .card{width:100%;max-width:560px;background:var(--panel);border:1px solid var(--line);border-radius:16px;
    padding:48px;display:flex;flex-direction:column;align-items:center;text-align:center}
  .card img{display:block;width:96px;height:96px;padding:6px;border-radius:50%;background:var(--tile)}
  .kick{margin:16px 0 0;font-size:13px;line-height:18px;font-weight:600;letter-spacing:.1em;
    text-transform:uppercase;color:var(--dim)}
  h1{margin:12px 0 0;font-size:36px;line-height:40px;font-weight:800;letter-spacing:-.025em;text-wrap:balance}
  p{margin:16px 0 0}
  .next{margin-top:20px;padding-top:16px;width:100%;border-top:1px solid var(--line);
    font-size:15px;line-height:22px;color:var(--dim)}
  .next a{color:var(--link)}
  .acts{margin-top:32px;width:100%;display:flex;flex-direction:column;align-items:center;gap:8px}
  .btn{display:flex;align-items:center;justify-content:center;width:100%;max-width:320px;min-height:52px;
    padding:0 24px;border-radius:10px;background:var(--accent);color:var(--on-accent);font-size:18px;font-weight:600;
    text-decoration:none}
  .btn:hover{background:var(--accent-hover);color:var(--on-accent)}
  .btn-g{gap:12px;background:var(--g-bg);border:1px solid var(--g-line);color:var(--g-ink);
    font-family:'Google Sans',Roboto,Archivo,system-ui,sans-serif;font-weight:500}
  .btn-g:hover{background:var(--g-hover);color:var(--g-ink)}
  .about{display:inline-flex;align-items:center;min-height:44px;font-size:16px;font-weight:600;color:var(--link)}
  .about:hover{color:var(--ink)}
  .theme{margin:20px 0 0;font-size:14px}
  .theme a{color:var(--dim);display:inline-flex;align-items:center;min-height:44px}
  .theme a:hover{color:var(--ink)}
  a:focus-visible{outline:3px solid var(--link);outline-offset:3px}
  @media (max-width:720px){main{padding:48px 20px}.card{padding:32px 24px}h1{font-size:30px;line-height:36px}}
`;

export const FORM_CSS = `
  form{width:100%;max-width:320px;margin:0 auto;display:flex;flex-direction:column;align-items:stretch;gap:8px;text-align:left}
  label{font-size:15px;font-weight:600;color:var(--ink)}
  input[type=email],input[type=text]{width:100%;min-height:52px;padding:0 14px;border:1px solid var(--field-line);border-radius:10px;
    background:var(--field);color:var(--ink);font:inherit;font-size:18px}
  input:focus-visible{outline:3px solid var(--link);outline-offset:2px}
  input.code{text-transform:uppercase;letter-spacing:.12em;font-weight:600}
  button.btn{border:0;cursor:pointer;font:inherit;font-weight:600;font-size:18px}
  button.btn:focus-visible{outline:3px solid var(--link);outline-offset:3px}
  .or{margin:16px 0 8px;font-size:14px;color:var(--dim)}
  .note{margin:16px 0 0;width:100%;padding:12px 14px;border-left:3px solid var(--beak);background:var(--note);
    font-size:16px;line-height:22px;text-align:left}
  .sent{font-weight:600;overflow-wrap:anywhere}`;

/* The address box. It posts to THIS host's /signin/email, so it is drawn only
 * on the site's own address (or a local preview), where that is the sign-in
 * a person's cookie will be read on; elsewhere a card links to the site. */
export function emailForm(to, email = "") {
  return `<form method="post" action="/signin/email">
<label for="em">Email address</label>
<input id="em" type="email" name="email" value="${esc(email)}" required autocomplete="email" inputmode="email" maxlength="254">
<input type="hidden" name="to" value="${esc(to)}">
<button class="btn" type="submit">Email me a sign-in link</button>
</form>`;
}

/* The way to the other theme, back to `here` (a path on this site; the
 * switch holds it again with _to.js). */
export function themeSwitch(theme, here) {
  const other = theme === "light" ? "dark" : "light";
  return `<p class="theme"><a href="/signin/theme?set=${other}&amp;to=${esc(encodeURIComponent(here || "/"))}">${other === "light" ? "Light" : "Dark"} theme</a></p>`;
}

/* What a sign-in page carries until the middleware knows the reader's
 * theme (functions/_middleware.js themed()): dark, and an empty switch. */
export const THEME_HTML = '<html lang="en" data-theme="dark">';
export const SWITCH_SLOT = "<!--sj-theme-switch-->";
export const SWITCH_MARK = "<!--sj-theme-switch";
/* A page answered to a POST (a refusal) names where its switch returns to,
 * so the person keeps where they were going (review, 2026-11-14); encoded,
 * so no path can close the comment. */
export const switchSlot = (here) =>
  here ? `<!--sj-theme-switch:${encodeURIComponent(here)}-->` : SWITCH_SLOT;
