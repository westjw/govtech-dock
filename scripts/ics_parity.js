/* Run index.html's OWN calRange against every date string in the catalogue.
 *
 * WHY THIS EXISTS. conferences.ics and the "+ calendar" button on a card are
 * two writers of one file format, and on 2026-09-11 they disagreed about all
 * 118 dated events: the button wrote DTSTART and DTEND, the feed wrote DTSTART
 * alone, so a reader who clicked got Fire-Rescue International as August 12-15
 * and a reader who SUBSCRIBED got August 12. The description had already been
 * through this once - build_site.py carries a comment ending "two writers of
 * one file now follow one rule" - and the date was left behind.
 *
 * A guard that re-implements the parser proves nothing about the parser that
 * ships. So this EXECUTES the real function, cut out of index.html by name,
 * exactly as scripts/worker_harness.js executes the real _brand.js rather than
 * defining the constants it is checking for.
 *
 *   node scripts/ics_parity.js            prints one JSON line per event
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

/* Cut a top-level `function name(` ... matching-brace block out of the file.
 * Brace counting rather than a regex, because calRange's body contains both
 * braces and regex literals holding braces. */
function lift(name, kind) {
  const head = kind === "const" ? `const ${name}=` : `function ${name}(`;
  const i = html.indexOf("\n" + head);
  if (i < 0) throw new Error(`${name} not found in index.html`);
  let j = html.indexOf(kind === "const" ? "{" : "{", i);
  let depth = 0;
  for (let k = j; k < html.length; k++) {
    if (html[k] === "{") depth++;
    else if (html[k] === "}") {
      depth--;
      if (depth === 0) return html.slice(i + 1, k + 1);
    }
  }
  throw new Error(`unbalanced braces lifting ${name}`);
}

const src = [lift("CAL_MONTHS", "const"), lift("mkRange"), lift("calRange")]
  .join("\n");
const calRange = new Function(`${src}; return calRange;`)();

const board = JSON.parse(
  fs.readFileSync(path.join(ROOT, "data/board.json"), "utf8"));
const ymd = (d) =>
  `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}` +
  `${String(d.getDate()).padStart(2, "0")}`;

const out = {};
for (const c of board.conferences || []) {
  const rg = calRange(c.dates);
  if (!rg) { out[c.tag] = null; continue; }
  const end = new Date(rg.end);
  end.setDate(end.getDate() + 1);      // DTEND on an all-day event is exclusive
  out[c.tag] = [ymd(rg.start), ymd(end)];
}
process.stdout.write(JSON.stringify(out));
