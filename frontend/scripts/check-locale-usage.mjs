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
for (const key of Object.keys(en)) {
  const category = CATEGORIES.find((c) => key.endsWith(`_${c}`));
  if (category) known.add(key.slice(0, -(category.length + 1)));
}

const files = sourceFiles(srcDir);
let checked = 0;
let failed = false;

for (const file of files) {
  const shown = file.slice(process.cwd().length + 1).replaceAll('\\', '/');
  const source = readFileSync(file, 'utf8')
    .split('\n')
    .map((line) => (COMMENT_LINE.test(line) ? '' : line))
    .join('\n');

  for (const match of source.matchAll(LITERAL_KEY)) {
    const key = match[2];
    checked += 1;
    if (known.has(key)) continue;
    failed = true;
    const line = source.slice(0, match.index).split('\n').length;
    console.error(
      `${shown}:${line}: t('${key}') names a key that is not in en.json ` +
        `-- t() would print that name to the page.`,
    );
  }
}

if (!checked) {
  console.error('No literal t() call was found at all; this check has lost its subject.');
  process.exit(1);
}

if (failed) {
  process.exit(1);
}

console.log(
  `Locale usage check passed for ${files.length} source files ` +
    `(${checked} literal t() keys, all present in en.json).`,
);
