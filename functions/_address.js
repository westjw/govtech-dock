/* WHETHER A STRING IS AN ADDRESS WE WILL MAIL, in one place.
 *
 * Its own file because two layers need it that must not import each other:
 * _mail.js (which reads GATED off _gate.js) and _session.js (which _gate.js
 * reads). It lived in _mail.js until our own sign-in (2026-11-13); _mail.js
 * still exports it, so every endpoint imports it from there as before.
 *
 * Deliberately conservative rather than RFC-complete: this address is going
 * to be handed to a mail API, so anything exotic is likelier to be an attempt
 * at header injection than a real mailbox. */
export function validEmail(raw) {
  const e = String(raw || "").trim().toLowerCase();
  if (e.length < 6 || e.length > 254) return null;
  if (/[\s<>",;\\()\[\]]/.test(e)) return null;
  if (!/^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(e)) return null;
  if (e.includes("..")) return null;
  return e;
}
