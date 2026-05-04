import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const localesDir = join(process.cwd(), 'src', 'locales');
const files = readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort();

const bannedByKey = {
  'settings.webhooks.disabled': ['discapacitado', '障害者'],
  'settings.tab.webhooks': ['webhaken'],
  'settings.backup.summary.webhooks': ['webhaken'],
};

let failed = false;

for (const file of files) {
  const locale = JSON.parse(readFileSync(join(localesDir, file), 'utf8'));

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
