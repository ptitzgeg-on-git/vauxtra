import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Every key a `t()` call spells out by hand must exist in en.json.
 *
 * The two locale gates beside this one read `src/locales/*.json` and never open a component,
 * so between them they answer "do the eight files agree with each other" and cannot answer
 * "does the code ask for a name nobody wrote". The second question has a cost an operator
 * sees: `t()` returns the key itself when it misses, so one typo ships as
 * `settings.logs.autoscrol` printed in the middle of the page, in all eight languages at
 * once, and every check in CI stays green while it happens.
 *
 * Only literal names are checked. `providerConstants.ts` assembles
 * `provider_guide.${type}.step_${index + 1}` and `TaxonomyTab.tsx` keeps its prefix in a
 * config object, and resolving either of those takes a guess -- a gate that guesses fails on
 * code that is correct, which is worse than a gate that skips. A literal needs no guess, and
 * it is what nearly every call site writes: the assembled families are a handful, the spelt
 * ones number in the thousands.
 *
 * The reverse question -- which keys no code path can reach -- is deliberately not asked
 * here. A dead key is inert, a wrongly deleted one is a raw key name in front of an
 * operator, and answering it means resolving exactly the templates this check refuses to
 * guess at.
 */

const srcDir = join(process.cwd(), 'src');
const localesDir = join(srcDir, 'locales');

/** A counted sentence is stored as `<base>_<category>`; `t()` is given the base. */
const CATEGORIES = ['zero', 'one', 'two', 'few', 'many', 'other'];

/**
 * The first argument of a `t()` call, when it is one unbroken literal on one line.
 *
 * `$` is refused inside the quotes so a template with a hole in it is skipped rather than
 * read as the key `provider_guide.`, a backslash is refused because an escape means the text
 * on the page is not the text in the file, and a newline is refused because a key is never a
 * multi-line string. The whitespace before the quote is not so restricted: Prettier moves a
 * long argument to its own line, and that call is the same call.
 */
const LITERAL_KEY = /\bt\(\s*(['"`])([^'"`\\$\n]*)\1/g;

/**
 * A `labelKey: '<key>'` field, which is a key spelt out by hand with no `t(` in front of it.
 *
 * Twenty-one keys reach the page this way and not one of them was checked here: every
 * settings tab and group, the three theme buttons, and the operational chip on every
 * integration card. The value is handed to `t()` one file away -- `{t(status.labelKey)}` --
 * so a typo in one fails exactly as a typo in a `t()` call does, printing
 * `providers.status.actve` into the chip in all eight languages while CI stays green. It is
 * a literal and needs no guess, which is the only thing this gate asks of a key.
 */
const LABEL_KEY = /\blabelKey:\s*(['"`])([^'"`\\$\n]*)\1/g;

/** Both ways a key is written out by hand, each with the shape a failure should quote. */
const KEY_FORMS = [
  { quote: (key) => `t('${key}')`, pattern: LITERAL_KEY },
  { quote: (key) => `labelKey: '${key}'`, pattern: LABEL_KEY },
];

/**
 * A line that opens with `//`, `*` or `/*` is prose, and prose shows examples:
 * `ConfirmDialog.tsx` documents its own API with a `t('services.confirm.delete_title')` in a
 * JSDoc block. An illustrative key is not a call, and failing the build over one would teach
 * the next reader to stop writing examples.
 *
 * Such a line is blanked rather than dropped, so that the line numbers in a failure still
 * point at the file as it is, and so that a call wrapped across two lines is still one
 * subject. Matching only at the start of a line keeps this out of the business of lexing
 * JavaScript: a continuation line of a template literal could open with `*` and be blanked
 * for nothing, which checks fewer keys and never fails a file that is right.
 */
const COMMENT_LINE = /^\s*(\/\/|\*|\/\*)/;

/** A hole a sentence expects to be filled, as `{name}`. */
const PLACEHOLDER = /\{(\w+)\}/g;

/**
 * The text of a `t()` call's remaining arguments, or '' when the key was its only one.
 *
 * Read by balancing parentheses from just after the key rather than by a regex, because the
 * arguments are an object literal and can hold parentheses of their own. Quotes are tracked
 * so a paren inside a string does not close the call early; template literals are tracked
 * the same way, which is coarse -- a `${...}` hole inside one is not parsed -- and coarse is
 * safe here, since the worst case is arguments that read as longer than they are and a
 * placeholder found in text that is still part of the same call.
 */
function argsAfter(source, start) {
  let depth = 1;
  let quote = null;
  for (let i = start; i < source.length; i += 1) {
    const ch = source[i];
    if (quote) {
      if (ch === '\\') i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === '`') quote = ch;
    else if (ch === '(') depth += 1;
    else if (ch === ')') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i);
    }
  }
  return '';
}

function sourceFiles(dir) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name),
  )) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (path !== localesDir) found.push(...sourceFiles(path));
    } else if (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx')) {
      found.push(path);
    }
  }
  return found;
}

const en = JSON.parse(readFileSync(join(localesDir, 'en.json'), 'utf8'));
const known = new Set(Object.keys(en));
/** Every hole a key can present, a counted sentence's categories folded into its base. */
const holes = new Map();
const noteHoles = (key, text) => {
  const found = holes.get(key) ?? new Set();
  for (const [, name] of text.matchAll(PLACEHOLDER)) found.add(name);
  holes.set(key, found);
};
for (const [key, text] of Object.entries(en)) {
  noteHoles(key, text);
  const category = CATEGORIES.find((c) => key.endsWith(`_${c}`));
  if (category) {
    const base = key.slice(0, -(category.length + 1));
    known.add(base);
    noteHoles(base, text);
  }
}

const files = sourceFiles(srcDir);
let checked = 0;
let filled = 0;
let failed = false;

/**
 * Whether a `t()` call fills every hole its sentence presents.
 *
 * The two gates beside this one compare the eight locale files to each other, so they see a
 * placeholder that one language dropped and are blind to one that no call site fills: the
 * files agree, the key exists, and `{host}` is printed to the operator verbatim -- in all
 * eight languages, which is how `expose.done.body` announced a published route as
 * "{host} is published on its providers." on the last screen of the wizard.
 *
 * Arguments that spread another object, or that build their names, are skipped rather than
 * guessed at: `{ ...counts }` may well carry the hole, and a gate that fails on correct code
 * is worse than one that checks less.
 */
function unfilledHoles(key, args) {
  const wanted = holes.get(key);
  if (!wanted || wanted.size === 0) return [];
  if (args.includes('...') || args.includes('[')) return [];
  return [...wanted].filter((name) => !new RegExp(`\\b${name}\\s*[:,}]`).test(args));
}

for (const file of files) {
  const shown = file.slice(process.cwd().length + 1).replaceAll('\\', '/');
  const source = readFileSync(file, 'utf8')
    .split('\n')
    .map((line) => (COMMENT_LINE.test(line) ? '' : line))
    .join('\n');

  for (const { quote, pattern } of KEY_FORMS) {
    for (const match of source.matchAll(pattern)) {
      const key = match[2];
      checked += 1;
      if (known.has(key)) continue;
      failed = true;
      const line = source.slice(0, match.index).split('\n').length;
      console.error(
        `${shown}:${line}: ${quote(key)} names a key that is not in en.json ` +
          `-- t() would print that name to the page.`,
      );
    }
  }

  for (const match of source.matchAll(LITERAL_KEY)) {
    const key = match[2];
    if (!known.has(key)) continue;
    const missing = unfilledHoles(key, argsAfter(source, match.index + match[0].length));
    filled += 1;
    if (missing.length === 0) continue;
    failed = true;
    const line = source.slice(0, match.index).split('\n').length;
    console.error(
      `${shown}:${line}: t('${key}') fills none of ` +
        `${missing.map((name) => `{${name}}`).join(', ')} ` +
        `-- the sentence would print that hole to the page as it is written.`,
    );
  }
}

if (!checked) {
  console.error('No key spelt out by hand was found at all; this check has lost its subject.');
  process.exit(1);
}

if (failed) {
  process.exit(1);
}

console.log(
  `Locale usage check passed for ${files.length} source files ` +
    `(${checked} keys spelt out by hand, all present in en.json; ` +
    `${filled} t() calls fill every hole their sentence presents).`,
);
