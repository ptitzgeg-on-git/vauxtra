import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const localesDir = join(process.cwd(), 'src', 'locales');
const files = readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort();

const bannedByKey = {
  'settings.webhooks.disabled': ['discapacitado', '障害者'],
  'settings.tab.webhooks': ['webhaken'],
  'settings.backup.summary.webhooks': ['webhaken'],
};

/**
 * A parenthesised ending next to a count is a plural written by hand: "certificat(s)",
 * "service(s)", "Eintrag(e)". It reads as a placeholder in every language and inflects in
 * none, and it hides the fact that several of these languages do not build a plural by
 * adding letters at all. Write `key_one` / `key_other` and let `t()` ask the locale.
 *
 * Only a short run of letters counts, and only beside `{count}`: "(24 h)", "(days)" and
 * "(任意)" are parentheticals, and `http(s)` is a protocol, none of which vary with a number.
 */
const CRUTCH = /\((\p{L}{1,4})\)/u;
const COUNTED = /\{count\}/;
const CRUTCH_ALLOWED = new Set(['settings.general.wan_sources_hint', 'templates.form.icon_invalid']);

/**
 * The same defect one step earlier: a sentence that carries `{count}` and no plural form at
 * all. Nobody wrote "(s)" because nobody wrote a plural, so `t()` hands the single form back
 * for every number and the screen says "1 keys", "1 entries", "1 etapes". Forty-one sentences
 * shipped that way, and in French, Spanish and Portuguese the noun, the adjective and the
 * participle all agree with the number, so the single form was wrong for half the values it
 * ever showed.
 *
 * `check-locale-parity.mjs` cannot see this one: it asks whether a plural family that EXISTS
 * is complete, and here there is no family. So the question is asked here, of `en.json` alone
 * -- the file the plural bases are read from, and the only file a locale may not add a key to.
 *
 * `COUNTED_INVARIANT` is the way out, and it is deliberately narrow: a number in parentheses
 * used as a badge, a bare number between a verb and a conjunction, a constant that is never 1.
 * Adding to it asserts that no language inflects around this number, not that the sentence is
 * awkward to translate.
 */
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;
const COUNTED_INVARIANT = new Map([
  ['layout.nav.item_with_badge', 'a badge in parentheses next to {label}'],
  ['providers.delete.deps_more', '"more" does not inflect, and neither do its translations'],
  ['settings.auth.new_distinct', 'the count is MIN_PASSWORD_DISTINCT_CHARS, a constant, never 1'],
  ['settings.backup.passphrase_distinct', 'the same constant, never 1'],
  ['settings.migration.import_selected', 'a badge in parentheses, nothing agrees with it'],
  ['setup.import.finish_import', 'a bare number between a verb and a conjunction'],
]);

let failed = false;

for (const file of files) {
  const locale = JSON.parse(readFileSync(join(localesDir, file), 'utf8'));

  for (const [key, value] of Object.entries(locale)) {
    if (CRUTCH_ALLOWED.has(key)) continue;
    const text = String(value ?? '');
    const crutch = COUNTED.test(text) ? CRUTCH.exec(text) : null;
    if (crutch) {
      failed = true;
      const base = key.replace(/_(zero|one|two|few|many|other)$/, '');
      console.error(
        `Locale quality failed for ${file}: key '${key}' writes its plural by hand ` +
          `('${crutch[0]}'). Write '${base}_one' / '${base}_other' and let t() pick.`,
      );
    }
  }

  for (const [key, bannedValues] of Object.entries(bannedByKey)) {
    const raw = String(locale[key] ?? '').trim().toLowerCase();
    if (!raw) continue;

    const bad = bannedValues.find((v) => raw === v.toLowerCase());
    if (bad) {
      failed = true;
      console.error(`Locale quality failed for ${file}: key '${key}' has banned value '${locale[key]}'`);
    }
  }
}

const reference = JSON.parse(readFileSync(join(localesDir, 'en.json'), 'utf8'));
let invariantUsed = 0;

for (const [key, value] of Object.entries(reference)) {
  if (PLURAL_SUFFIX.test(key)) continue;
  if (!COUNTED.test(String(value ?? ''))) continue;
  if (COUNTED_INVARIANT.has(key)) {
    invariantUsed += 1;
    continue;
  }
  failed = true;
  console.error(
    `Locale quality failed for en.json: key '${key}' counts with {count} but has no plural ` +
      `form, so every number reads the same sentence. Write '${key}_one' / '${key}_other' in ` +
      `every locale that declares 'one' and '${key}_other' alone in ja and zh, or list the key ` +
      `in COUNTED_INVARIANT with the reason nothing inflects around it.`,
  );
}

// The allow-list is a claim about six sentences. A name that no longer exists is a claim about
// nothing, and it would sit there silently covering a key that could come back later.
for (const [key, why] of COUNTED_INVARIANT) {
  const gone = !(key in reference);
  if (gone || !COUNTED.test(String(reference[key] ?? ''))) {
    failed = true;
    console.error(
      `Locale quality failed for en.json: COUNTED_INVARIANT lists '${key}' (${why}), which ` +
        `en.json ${gone ? 'no longer has' : 'no longer counts with {count}'}. Remove the entry.`,
    );
  }
}

if (failed) {
  process.exit(1);
}

console.log(
  `Locale semantic quality check passed for ${files.length} locale files ` +
    `(${invariantUsed} counted sentences declared invariant).`,
);
