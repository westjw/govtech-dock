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
 * FROM MOVED TO sledjobs.com ON 2026-09-03, the day Resend verified it. It
 * was held on the old domain deliberately: sending from a domain Resend has
 * never seen fails silently, with this endpoint still answering 200, which is
 * the shape of failure this project spends most of its guards on. The three
 * records were checked live before the move - a DKIM key distinct from the old
 * domain's, an SPF include for amazonses.com, and an MX at
 * feedback-smtp.us-east-1, the same region the old domain sends from.
 *
 * ALERTS ALREADY MAILED carry solesourcejobs.com links and still resolve,
 * because that domain remains attached to this Pages project. They break the
 * day it is handed to the federal board, and one confirmation email has ever
 * been sent, so that blast radius is one address.
 */
export const SITE = "https://sledjobs.com";
export const DOMAIN = "sledjobs.com";
export const NAME = "SLED JOBS";
export const FROM = "SLED JOBS <alerts@sledjobs.com>";
