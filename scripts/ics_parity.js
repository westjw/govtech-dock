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
 *
 * THE COUNTER HAS TO KNOW WHAT IT IS COUNTING. The first version incremented
 * on every `{` and decremented on every `}` wherever they appeared, and its
 * comment claimed that beat a regex "because calRange's body contains regex
 * literals holding braces" - which is exactly the case blind counting does NOT
 * survive. It works today only because every brace in those three functions
 * happens to be balanced: one `/[{]/` or one "unmatched { in a string" and the
 * lift silently returns the wrong slice, `new Function` compiles something
 * that is not the shipped parser, and a guard built to execute the real code
 * quietly executes something else. So: strings, template literals, regex
 * literals and comments are skipped rather than counted.
 *
 * AND THE MATCH HAS TO BE UNIQUE. indexOf takes the FIRST occurrence in the
 * file, including one inside an HTML comment or a JS comment - so documenting
 * `function calRange(` in a comment above the real one would lift the prose.
 * Every occurrence is found and more than one is an error that names them. */
function lift(name, kind) {
  const head = kind === "const" ? `const ${name}=` : `function ${name}(`;
  const at = [];
  for (let i = html.indexOf("\n" + head); i >= 0;
       i = html.indexOf("\n" + head, i + 1)) at.push(i);
  if (!at.length) throw new Error(`${name} not found in index.html`);
  if (at.length > 1) {
    throw new Error(
      `${name} appears ${at.length} times at top level in index.html ` +
      `(offsets ${at.join(", ")}). lift() cannot know which one ships - ` +
      `rename one or delete the duplicate.`);
  }
  const i = at[0];
  const open = html.indexOf("{", i);
  if (open < 0) throw new Error(`no body found for ${name}`);

  let depth = 0, k = open;
  // `prev` is the last significant character, which is how a regex literal is
  // told from a division: `/` after a value divides, `/` after an operator or
  // a `(` `,` `=` `:` `[` `!` `&` `|` `?` `{` `}` `;` opens a regex.
  let prev = "";
  while (k < html.length) {
    const c = html[k], two = html.slice(k, k + 2);
    if (two === "//") { k = html.indexOf("\n", k); if (k < 0) break; continue; }
    if (two === "/*") { k = html.indexOf("*/", k) + 2; continue; }
    if (c === '"' || c === "'" || c === "`") {
      k++;
      while (k < html.length && html[k] !== c) k += html[k] === "\\" ? 2 : 1;
      k++; prev = "x"; continue;
    }
    if (c === "/" && /[([{,;:=!&|?+\-*%<>~^]/.test(prev)) {
      k++;                                   // a regex literal
      let cls = false;
      while (k < html.length) {
        if (html[k] === "\\") { k += 2; continue; }
        if (html[k] === "[") cls = true;
        else if (html[k] === "]") cls = false;
        else if (html[k] === "/" && !cls) break;
        k++;
      }
      k++; prev = "x"; continue;
    }
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return html.slice(i + 1, k + 1);
    }
    if (!/\s/.test(c)) prev = c;
    k++;
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
