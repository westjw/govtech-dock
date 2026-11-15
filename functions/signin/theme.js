/* LIGHT OR DARK ON THE SIGN-IN PAGES: GET /signin/theme?set=light|dark&to=...
 *
 * The sign-in pages run no script, so their switch is a link (owner,
 * 2026-11-14: "I want white/dark options on all of them"). This sets the
 * same "sled-theme" cookie the board's own switch writes (index.html), so the
 * choice follows the reader onto the board and back, then returns them to
 * the page they were on, held to this site by _to.js. Not HttpOnly: the
 * board's script writes it too. A theme is all it carries.
 */
import { sameSitePath } from "../_to.js";
import { THEME_COOKIE } from "../_card.js";
import { redirect } from "../_signin_page.js";

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const set = url.searchParams.get("set") === "light" ? "light" : "dark";
  const to = sameSitePath(url.searchParams.get("to"), url.origin);
  return redirect(to, [["Set-Cookie",
    `${THEME_COOKIE}=${set}; Path=/; Max-Age=31536000; Secure; SameSite=Lax`]], 302);
}

export const onRequestHead = onRequestGet;
