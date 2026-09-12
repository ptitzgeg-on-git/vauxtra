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

if (failed) {
  process.exit(1);
}

console.log(`Locale semantic quality check passed for ${files.length} locale files.`);
