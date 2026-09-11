import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const localesDir = join(process.cwd(), 'src', 'locales');
const files = readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort();

const bannedByKey = {
  'settings.webhooks.disabled': ['discapacitado', '障害者'],
  'settings.tab.webhooks': ['webhaken'],
  'settings.backup.summary.webhooks': ['webhaken'],
};

// The same mistake as above, but the wrong word is only wrong in one language:
// `Prestations` is what a caterer sells, `Balises` are the tags in a markup document,
// `Dienstleistungen` is the commercial sense of "services". Each was the only string in
// its file using that word, while the rest of the file said Services, Étiquettes, Dienste.
const bannedByLocaleAndKey = {
  fr: {
    'settings.backup.summary.services': ['prestations'],
    'settings.backup.summary.tags': ['balises'],
  },
  de: {
    'settings.backup.summary.services': ['dienstleistungen'],
  },
  zh: {
    'settings.backup.summary.providers': ['provider'],
  },
};

// Spellings that are not words in their language: each one is a real word with its
// diacritics dropped. A whole panel arrived that way once — "Les cles API ne sont
// affichees qu'une seule fois a la creation" — and nothing here noticed, because every
// key was present and every placeholder matched. Only spellings that can never be right
// belong in this list: `chiffres`, `configure` and `utilise` stay out of it, they are
// ordinary words that happen to neighbour an accented one.
const unaccentedByLocale = {
  fr: [
    'acceder', 'acces', 'affichee', 'affichees', 'apres', 'associes', 'caracteres',
    'cle', 'cles', 'creation', 'creee', 'creez', 'deja', 'derniere', 'desactive',
    'desactivee', 'detectee', 'donnees', 'element', 'elements', 'enregistree', 'etat',
    'etats', 'evenement', 'evenements', 'generee', 'integree', 'necessaire', 'numero',
    'parametre', 'parametres', 'periode', 'portee', 'prefixe', 'premiere', 'remplacee',
    'remplacees', 'reponse', 'requete', 'reseau', 'retablissent', 'securite',
    'selectionnee', 'succes', 'systeme', 'tres', 'utilisee', 'utilisees', 'verifiee',
  ],
  de: ['eintrage', 'zuruck', 'fur', 'uber', 'konnen', 'mussen', 'gultig', 'ubertragen'],
  es: ['configuracion', 'informacion', 'conexion', 'publico', 'version'],
  pt: ['configuracao', 'informacao', 'conexao', 'notificacao', 'servico', 'servicos', 'versao'],
};

// `{count}` is a placeholder and `icone.png` is a path — neither is prose, and both hold
// sequences this check would otherwise read as a dropped accent.
function prose(value) {
  return String(value)
    .replace(/\{[^}]*\}/g, ' ')
    .split(/\s+/)
    .filter((token) => !/[/@\\]/.test(token) && !/\w\.\w/.test(token))
    .join(' ');
}

let failed = false;

for (const file of files) {
  const name = file.replace(/\.json$/, '');
  const locale = JSON.parse(readFileSync(join(localesDir, file), 'utf8'));

  const banned = { ...bannedByKey, ...(bannedByLocaleAndKey[name] || {}) };
  for (const [key, bannedValues] of Object.entries(banned)) {
    const raw = String(locale[key] ?? '').trim().toLowerCase();
    if (!raw) continue;

    const bad = bannedValues.find((v) => raw === v.toLowerCase());
    if (bad) {
      failed = true;
      console.error(`Locale quality failed for ${file}: key '${key}' has banned value '${locale[key]}'`);
    }
  }

  const unaccented = unaccentedByLocale[name];
  if (!unaccented) continue;
  const forbidden = new Set(unaccented);

  for (const [key, value] of Object.entries(locale)) {
    if (typeof value !== 'string') continue;

    // \p{L}, not \w: JavaScript's \w is ASCII even under /u, so `Paramètres` would come
    // back as `Param` and `tres` -- and `tres` is on the list below.
    const words = prose(value).match(/\p{L}+/gu) || [];
    const bad = [...new Set(words.filter((w) => forbidden.has(w.toLowerCase())))];
    if (bad.length) {
      failed = true;
      console.error(`Locale quality failed for ${file}: key '${key}' lost its accents on ${bad.join(', ')}`);
    }
  }
}

if (failed) {
  process.exit(1);
}

console.log(`Locale semantic quality check passed for ${files.length} locale files.`);
