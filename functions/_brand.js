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
 *
 * FROM IS DELIBERATELY STILL ON solesourcejobs.com. Resend has that domain
 * verified for sending and has never seen sledjobs.com; moving this line
 * before the new domain is verified makes every alert fail to send while the
 * endpoint still answers 200. It moves the day Resend shows sledjobs.com
 * verified.
 */
export const SITE = "https://sledjobs.com";
export const DOMAIN = "sledjobs.com";
export const NAME = "SLED JOBS";
export const FROM = "SLED JOBS <alerts@solesourcejobs.com>";
