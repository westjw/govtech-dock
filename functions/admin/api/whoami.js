/* /admin/api/whoami answers exactly what /api/whoami answers (one
 * implementation, functions/api/whoami.js). The public pages ask /api/whoami
 * since 2026-11-13: this path is under the Access application, which a
 * member signed in the site's own way never passes. Kept for anything that
 * still asks here. */
export { onRequestGet } from "../../api/whoami.js";
