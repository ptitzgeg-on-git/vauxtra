import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Every locale must carry every key en.json carries — except for counted sentences, where
 * "the same keys" is the wrong question.
 *
 * A counted sentence is stored as `<base>_<CLDR category>`, and how many categories a
 * language has is a property of the language: Japanese and Chinese have one, English and
 * German two, French and Portuguese three. Demanding the English key set of every file
 * would make a Japanese translator write a singular that `Intl.PluralRules` can never
 * select, and would forbid a future Russian translation the `few` and `many` forms it
 * cannot do without. So plural keys are checked against the locale's own rules:
 *
 *   - `_other` is required everywhere: `t()` falls back to it for a category a file skipped.
 *   - `_one` is required wherever the language declares it — that is the form whose absence
 *     would silently render the plural.
 *   - the language's remaining categories are allowed, never required: French declares
 *     `many`, and it fires at a million routes.
 *   - a category the language does not declare is refused: it is dead weight, and it is
 *     text a translator would have written for nothing.
 *
 * The language tags come from `src/i18n/index.tsx` rather than from the filenames, because
 * `pt.json` is served as `pt-BR` and the rules are the app's, not this script's.
 */

const localesDir = join(process.cwd(), 'src', 'locales');
const files = readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort();

if (!files.includes('en.json')) {
  console.error('Missing reference locale: en.json');
  process.exit(1);
}

function loadJson(file) {
  return JSON.parse(readFileSync(join(localesDir, file), 'utf8'));
}

/** `LOCALE_TAGS` as the app declares it, so this check and the runtime cannot drift. */
function readLocaleTags() {
  const src = readFileSync(join(process.cwd(), 'src', 'i18n', 'index.tsx'), 'utf8');
  const block = src.match(/export const LOCALE_TAGS: Record<Lang, string> = \{([^}]*)\}/);
  if (!block) {
    console.error('Could not read LOCALE_TAGS from src/i18n/index.tsx');
    process.exit(1);
  }
  const tags = {};
  for (const [, lang, tag] of block[1].matchAll(/(\w+):\s*'([\w-]+)'/g)) tags[lang] = tag;
  return tags;
}

const CATEGORIES = ['zero', 'one', 'two', 'few', 'many', 'other'];
const SUFFIXES = CATEGORIES.map((c) => `_${c}`);

/** `["services.count_one", …]` → `{ base: "services.count", category: "one" }`, else null. */
function splitPlural(key) {
  const suffix = SUFFIXES.find((s) => key.endsWith(s));
  return suffix ? { base: key.slice(0, -suffix.length), category: suffix.slice(1) } : null;
}

const localeTags = readLocaleTags();
const en = loadJson('en.json');

const pluralBases = new Set();
for (const key of Object.keys(en)) {
  const split = splitPlural(key);
  if (split) pluralBases.add(split.base);
}

let failed = false;

function fail(file, message) {
  failed = true;
  console.error(`${file}: ${message}`);
}

// A base that is also a plain key is ambiguous: `t(base, { count })` would take the plural
// form and `t(base)` the plain one, from the same name, in the same file.
for (const base of pluralBases) {
  if (base in en) fail('en.json', `'${base}' exists both as a plain key and as a plural base`);
}

const plainKeys = Object.keys(en).filter((k) => !splitPlural(k)).sort();

for (const file of files) {
  const lang = file.replace(/\.json$/, '');
  const tag = localeTags[lang];
  if (!tag) {
    fail(file, `no entry in LOCALE_TAGS — the language cannot be selected in the app`);
    continue;
  }

  const declared = new Set(new Intl.PluralRules(tag).resolvedOptions().pluralCategories);
  const data = loadJson(file);
  const present = new Set(Object.keys(data));

  const required = new Set(plainKeys);
  const allowed = new Set(plainKeys);
  for (const base of pluralBases) {
    required.add(`${base}_other`);
    if (declared.has('one')) required.add(`${base}_one`);
    for (const category of declared) allowed.add(`${base}_${category}`);
    allowed.add(`${base}_other`);
  }

  const missing = [...required].filter((k) => !present.has(k)).sort();
  const extra = [...present].filter((k) => !allowed.has(k)).sort();

  if (missing.length || extra.length) {
    failed = true;
    console.error(`Locale parity failed for ${file} (${tag}: ${[...declared].join(', ')})`);
    if (missing.length) {
      console.error(`  Missing keys (${missing.length}):`);
      for (const key of missing) console.error(`    - ${key}`);
    }
    if (extra.length) {
      console.error(`  Extra keys (${extra.length}):`);
      for (const key of extra) {
        const split = splitPlural(key);
        const why = split && pluralBases.has(split.base)
          ? ` — ${tag} has no '${split.category}' plural category, this form can never be selected`
          : '';
        console.error(`    - ${key}${why}`);
      }
    }
  }
}

if (failed) {
  process.exit(1);
}

console.log(
  `Locale parity check passed for ${files.length} locale files ` +
    `(${plainKeys.length} plain keys, ${pluralBases.size} counted sentences).`,
);
