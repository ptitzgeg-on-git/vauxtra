import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The other three locale checks ask structural questions: does every locale have the same
 * keys, does every key reach a `t()` call, does every sentence print the same placeholders.
 * All three pass on a file where the sidebar says one word, the page title says another, and
 * the confirmation dialog says a third. Nothing in them reads the words.
 *
 * This one does, and only for the handful of places where reading them is cheap and the
 * answer is not a matter of taste:
 *
 *   1. a product noun that English uses for one thing is one word in each translation too;
 *   2. a locale addresses the reader in one register, not two.
 *
 * Both rules were calibrated against the eight files before being written down, and every
 * concept that did not come out at zero divergence was left out rather than shipped with a
 * pile of exceptions. A check that has to be argued with on every run is a check people turn
 * off.
 */

const localesDir = join(process.cwd(), 'src', 'locales');
const LOCALES = ['de', 'es', 'fr', 'ja', 'nl', 'pt', 'zh'];

const reference = JSON.parse(readFileSync(join(localesDir, 'en.json'), 'utf8'));
const locales = new Map(
  LOCALES.map((code) => [code, JSON.parse(readFileSync(join(localesDir, `${code}.json`), 'utf8'))]),
);

let failed = false;

/**
 * A word, not a fragment and not a placeholder name. `{tags}` in "restore {tags} tags" is the
 * number being interpolated and has nothing to do with the noun; matching it once sent this
 * rule looking for a translation of a variable. The lookbehind and lookahead refuse a brace
 * on either side, which is what tells `{domain}` from "domain".
 */
const bareWord = (value, pattern) =>
  new RegExp(String.raw`(?<![\{\p{L}\p{N}_])` + `(?:${pattern})` + String.raw`(?![\}\p{L}\p{N}_])`, 'iu').test(value);

/**
 * "Endpoint" means two different things in this product, and only one of them is a word the
 * translations own.
 *
 * On the Endpoints page it is the thing the user creates, and it has to read the same in the
 * menu, the title and the delete dialog. In the Docker settings and the integration guides it
 * is an API URL -- "the Docker endpoint", "the deSEC endpoint" -- and there a translated word
 * is the right word: "point d'acces Docker" and "eindpunt" are correct Dutch and French for a
 * URL and wrong for the product noun.
 *
 * These prefixes are the second sense. They are excluded from the endpoint rule only, and
 * folding them in was tried first: it asked six locales to un-translate a sentence that was
 * never about the product.
 */
const API_URL_SENSE = /^(settings\.docker\.|provider_guide\.|provider_modal\.|providers\.card\.default_url|settings\.webhooks\.url_hint)/;

/**
 * For each concept: the English word that marks a key as being about it, and the stem each
 * locale settled on. Stems, not whole words: "integracao" does not contain "integracoes" and
 * "certificado" does not contain "certificados", and an early version of this rule reported
 * fifty-one Portuguese failures that were all the plural.
 *
 * `provider` is deliberately absent. It was measured alongside the five below and came out at
 * thirty-eight divergences across a hundred and twenty-six keys, because several locales use
 * the vendor's own name where English says "provider" and that reads better than the noun. A
 * rule that fires thirty-eight times on a file nobody thinks is broken is not measuring the
 * file, it is measuring itself.
 */
const CONCEPTS = [
  {
    name: 'endpoint',
    english: 'endpoints?',
    excludeKeys: API_URL_SENSE,
    stems: { fr: 'endpoint', de: 'endpoint|endpunkt', nl: 'endpoint', es: 'endpoint', pt: 'endpoint', ja: 'エンドポイント', zh: '端点' },
  },
  {
    name: 'integration',
    english: 'integrations?',
    stems: { fr: 'intégrat', de: 'integration', nl: 'integrati', es: 'integraci', pt: 'integraç', ja: '連携', zh: '集成' },
  },
  {
    name: 'certificate',
    english: 'certificates?',
    stems: { fr: 'certificat', de: 'zertifikat', nl: 'certifica', es: 'certificad', pt: 'certificad', ja: '証明書', zh: '证书' },
  },
  {
    name: 'tag',
    english: 'tags?',
    stems: { fr: 'étiquette', de: 'tag', nl: 'tag', es: 'etiqueta', pt: 'tag', ja: 'タグ', zh: '标签' },
  },
  {
    name: 'domain',
    english: 'domains?',
    stems: { fr: 'domaine', de: 'dom(äne|ain)', nl: 'domein', es: 'dominio', pt: 'dom(í|i)nio', ja: 'ドメイン', zh: '域名' },
  },
];

/**
 * A key allowed to name the concept some other way, with the reason it is allowed to. Keep
 * this list short: every entry is a place where the rule above is not true, and a long list
 * means the rule was the wrong one.
 */
const NAMED_DIFFERENTLY = {
  'certificate:fr:certificates.empty.none_unread_hint':
    'On a page already titled Certificats, the sentence uses a pronoun rather than repeat the ' +
    'noun a third time: "si l\'une d\'elles en detient un". Repeating it reads like a machine.',
  'certificate:nl:certificates.empty.none_unread_hint':
    'Same sentence, same reason: "of een ervan er een beheert" is what Dutch does here.',
};

const declared = new Set(Object.keys(NAMED_DIFFERENTLY));
const used = new Set();

for (const concept of CONCEPTS) {
  const keys = Object.keys(reference).filter(
    (key) =>
      typeof reference[key] === 'string' &&
      bareWord(reference[key], concept.english) &&
      !(concept.excludeKeys && concept.excludeKeys.test(key)),
  );

  for (const [code, locale] of locales) {
    const stem = new RegExp(concept.stems[code], 'iu');
    for (const key of keys) {
      // A key absent from a locale is the `_one` plural gap: Japanese and Chinese have no
      // CLDR "one" category, so they carry `_other` alone. Parity already checks this.
      const value = locale[key];
      if (value === undefined) continue;
      if (stem.test(value)) continue;

      const excuse = `${concept.name}:${code}:${key}`;
      if (declared.has(excuse)) {
        used.add(excuse);
        continue;
      }

      failed = true;
      console.error(
        `Locale consistency failed for ${code}.json: key '${key}' is about ${concept.name} in ` +
          `en.json but does not use this locale's word for it (/${concept.stems[code]}/). ` +
          `It reads: ${JSON.stringify(value.slice(0, 90))}. Either use the same word the other ` +
          `${concept.name} keys use, or declare this key in NAMED_DIFFERENTLY with the reason.`,
      );
    }
  }
}

for (const excuse of declared) {
  if (used.has(excuse)) continue;
  failed = true;
  console.error(
    `Locale consistency failed: NAMED_DIFFERENTLY declares '${excuse}', which now names the ` +
      `concept the same way every other key does. Remove the entry.`,
  );
}

/**
 * Rule 2: one way of addressing the reader per locale.
 *
 * Every language here except Japanese and Chinese has to choose between a familiar and a
 * polite second person, and the choice is not a per-sentence one: two registers on the same
 * screen read as two people wrote it. Dutch was found split between `je` and `u`, and Spanish
 * between tu and usted, in both cases with the minority hiding in the settings pages, which
 * are exactly the pages a translator reaches last.
 *
 * So each locale declares the register it actually uses, and the markers of the other one.
 * The markers are the pronouns and the verb forms that belong to one register and cannot be
 * anything else -- never the possessive on its own:
 *
 *   * Spanish `su` / `sus` is third person as much as it is polite second person. It is what
 *     "their own credentials" and "its API" are written with, and asking about it produced
 *     two false reports out of fourteen. Spanish is caught by `usted` and by the polite
 *     imperative instead, which is what the real defect was made of: `Introduzca`, `Guarde`,
 *     `Reinicie`, none of which carry a `usted` anywhere near them.
 *   * Dutch bare `u` is the abbreviation for hour. "24 u" is a duration, not a pronoun, and
 *     `uw` is the marker that means what it looks like.
 *
 * Japanese and Chinese are absent on purpose. Politeness there is carried by verb endings and
 * by whether the subject is named at all, not by a pronoun a regular expression can find, and
 * a rule that cannot be stated cannot be checked.
 */
const REGISTERS = {
  fr: { house: 'vouvoiement', foreign: 'tutoiement', marker: '(ton|ta|tes|tu)' },
  de: { house: 'Sie', foreign: 'du', marker: '(du|dir|dich|dein(e|en|em|er|es)?)' },
  nl: { house: 'je', foreign: 'u', marker: '(uw)' },
  pt: { house: 'você', foreign: 'tu', marker: '(tu|teu|tua|teus|tuas)' },
  es: {
    house: 'tú',
    foreign: 'usted',
    marker:
      '(usted|Guarde|Administre|Defina|Obtenga|Ingrese|Cree|Deje|Rellene|Introduzca|Seleccione' +
      '|Elija|Haga|Vaya|Pulse|Copie|Pegue|Escriba|Revise|Compruebe|Verifique|Utilice|Abra' +
      '|Cierre|Active|Desactive|Edite|Elimine|Borre|Agregue|Confirme|Espere|Vuelva|Consulte' +
      '|Descargue|Cargue|Ejecute|Instale|Inicie|Detenga|Reinicie|Pruebe|Intente|Tenga|Recuerde' +
      '|Genere|Genérelo|Mantenga|Cambie|Modifique|Busque|Solicite|Establezca|Ponga|Quite' +
      '|Retire|Mueva|Arrastre|Suelte|Presione|Añada|Asegúrese|Continúe|Envíe)',
  },
};

for (const [code, { house, foreign, marker }] of Object.entries(REGISTERS)) {
  const locale = locales.get(code);
  const rx = new RegExp(String.raw`(?<![\{\p{L}\p{N}_])` + `(?:${marker})` + String.raw`(?![\}\p{L}\p{N}_])`, 'u');
  for (const [key, value] of Object.entries(locale)) {
    if (typeof value !== 'string') continue;
    const hit = value.match(rx);
    if (!hit) continue;

    failed = true;
    console.error(
      `Locale consistency failed for ${code}.json: key '${key}' addresses the reader with ` +
        `${JSON.stringify(hit[0])}, which is the ${foreign} register. This locale speaks ` +
        `${house} everywhere else. It reads: ${JSON.stringify(value.slice(0, 90))}.`,
    );
  }
}

if (failed) {
  process.exit(1);
}

console.log(
  `Locale consistency check passed for ${LOCALES.length + 1} locale files ` +
    `(${CONCEPTS.length} product nouns spelt one way each, ` +
    `${Object.keys(REGISTERS).length} locales addressing the reader one way each).`,
);
