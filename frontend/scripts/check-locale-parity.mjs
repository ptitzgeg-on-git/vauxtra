import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const localesDir = join(process.cwd(), 'src', 'locales');
const files = readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort();

if (!files.includes('en.json')) {
  console.error('Missing reference locale: en.json');
  process.exit(1);
}

function loadJson(file) {
  return JSON.parse(readFileSync(join(localesDir, file), 'utf8'));
}

const en = loadJson('en.json');
const enKeys = Object.keys(en).sort();

let failed = false;

for (const file of files) {
  if (file === 'en.json') continue;
  const data = loadJson(file);
  const keys = Object.keys(data).sort();

  const missing = enKeys.filter((k) => !keys.includes(k));
  const extra = keys.filter((k) => !enKeys.includes(k));

  if (missing.length || extra.length) {
    failed = true;
    console.error(`Locale parity failed for ${file}`);
    if (missing.length) {
      console.error(`  Missing keys (${missing.length}):`);
      for (const key of missing) console.error(`    - ${key}`);
    }
    if (extra.length) {
      console.error(`  Extra keys (${extra.length}):`);
      for (const key of extra) console.error(`    - ${key}`);
    }
  }
}

if (failed) {
  process.exit(1);
}

console.log(`Locale parity check passed for ${files.length} locale files.`);
