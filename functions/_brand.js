/* The JS half of data/brand.json.
 *
 * A Pages Function cannot read a file out of the repository at runtime, so the
 * few brand facts the endpoints need are restated here. That duplication is
 * the thing that will rot, so scripts/selftest.py fails the build if these
 * values drift from data/brand.json - the same guard the alerts vocabulary
 * already has, for the same reason: the failure would otherwise be silent.
 *
 * When the domain changes, edit data/brand.json AND this file, then point the
 * Cloudflare Pages custom domain at the new name. selftest will tell you if
 * you did only one of the first two.
 */
export const SITE = "https://solesourcejobs.com";
export const DOMAIN = "solesourcejobs.com";
export const NAME = "SLED JOBS";
export const FROM = "SLED JOBS <alerts@solesourcejobs.com>";
