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
 * Every number in a sentence asks the same question: does any word around it agree with it?
 *
 * `t()` can answer that for exactly one number per key -- the one named `{count}`, which
 * picks `_one` / `_other` through the locale's own plural rules. Every other number in the
 * same sentence is printed as it arrives, so a word that agrees with it is frozen at
 * whichever form the translator happened to write.
 *
 * The first version of this check only ever looked at `{count}`, which left the numbers most
 * likely to be wrong as the ones it never asked about: `{total}`, `{enabled}`, `{shown}`,
 * `{ok}`, `{failed}`, `{healthy}`. "1 of 5 routes shown" is right and "1 of 1 routes shown"
 * is not, and nothing here could tell them apart.
 *
 * So the question is now put to every placeholder, and the default answer is suspicion:
 *
 *   * a name in `NON_QUANTITY` never carries a number -- a host, a date, an error message --
 *     and passes without a word;
 *   * a name in `QUANTITY` may carry one, and every key that uses it has to say why nothing
 *     inflects around it;
 *   * a name in neither fails, because a placeholder nobody has classified is a placeholder
 *     nobody has read. Writing `{routes}` into a sentence is meant to stop this file.
 *
 * The same name can be either, which is why declarations are per key and not per name:
 * `{providers}` is a list of integration names on the certificates page and a counted phrase
 * in the restore dialog, and `{error}` is a message everywhere except `monitoring.check_summary`,
 * where it is how many services came back down.
 *
 * Three ways out of a declaration, in the order they are worth trying:
 *
 *   1. count the noun in its own key and interpolate the finished phrase, the way
 *      `expose.preflight.summary` is assembled from three `*_count` keys;
 *   2. move the noun next to `{count}` -- "{count} routes shown of {total}" rather than
 *      "{shown} of {total} routes shown". Which of the two numbers the noun belongs to is not
 *      the same in every language: English, German and Dutch hang it on the total, French on
 *      the shown, and `t()` binds one of them. Moving it is what makes the eight locales
 *      agree on which;
 *   3. declare it below, with the reason. A declaration asserts that no language inflects
 *      around this number -- not that the sentence is awkward to translate.
 *
 * What this still cannot see: the reasons are written against `en.json`, and a translator is
 * free to put a noun where English leaves a number bare. The placeholder-set comparison
 * further down catches a locale that loses or invents a placeholder, but not one that adds a
 * word beside a number English wrote alone.
 */
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;
const PLACEHOLDER = /\{(\w+)\}/g;

/** Names that never arrive as a number, so no word can agree with them. */
const NON_QUANTITY = new Set([
  'date', 'detail', 'dns', 'domain', 'expected', 'found', 'fqdn', 'from', 'host', 'id',
  'issuer', 'label', 'mode', 'name', 'provider', 'range', 'reason', 'source', 'state',
  'status', 'target', 'text', 'time', 'to', 'version', 'when', 'zone',
]);

/** Names that may arrive as a number. Every use of one has to be declared below. */
const QUANTITY = new Set([
  'answers', 'badge', 'blocking', 'checks', 'clients', 'connections', 'count', 'days',
  'disk', 'domains', 'enabled', 'environments', 'error', 'errors', 'failed', 'healthy',
  'keys', 'latency', 'max', 'min', 'minutes', 'more', 'ms', 'ok', 'page', 'pages',
  'percent', 'providers', 'score', 'seconds', 'services', 'step', 'tags', 'total',
  'value', 'values', 'warnings', 'webhooks',
]);

/**
 * Why nothing inflects around this number, key by key. Keyed by the plural base, so one entry
 * covers `_one` and `_other` together. `{count}` needs an entry only where the key has no
 * plural forms at all -- where it has them, the forms are the answer.
 */
const DECLARED = new Map(
  Object.entries({
    // The second number inside a sentence that already counts something else. `t()` inflects
    // one placeholder, and these are the ones it does not reach.
    'certificates.empty.none_hint': { providers: 'the integration names, joined into one string' },
    'certificates.meta': { days: 'the certificate warning window, a constant, never 1' },
    'dashboard.attention.certs_expiring': { days: 'the same window constant' },
    'dashboard.stats.certificates_hint': { days: 'the same window constant' },
    'monitoring.check_summary': {
      ok: 'a count before "up", a word no language inflects',
      error: 'a count before "down", the same',
    },
    'monitoring.tunnels.healthy_of': { total: 'a bare denominator after a slash, with no noun of its own' },
    'monitoring.uptime.summary': { percent: 'already formatted by formatPercent' },
    'providers.refresh.failed_count': { total: 'a bare total after "out of"; the noun sits beside {count}' },
    'services.meta': { total: 'a bare total after "of"; the noun sits beside {count}' },
    'settings.backup.restore_settings_dropped': { keys: 'the refused setting names, joined into one string' },
    'settings.general.ignored_keys': { keys: 'the refused setting names, joined into one string' },
    'settings.webhooks.enabled_count': { total: 'a bare total after "of", with no noun of its own' },
    'setup.restore.done_settings': { keys: 'the refused setting names, joined into one string' },
    'templates.count_filtered': { total: 'a bare total after "of", with no noun of its own' },

    // Phrases counted by another key and interpolated whole. This is the way out of the
    // problem above when the noun cannot be moved: each language counts its own noun.
    'dashboard.stats.providers_hint': {
      healthy: 'counted by dashboard.stats.providers_healthy',
      enabled: 'counted by dashboard.stats.providers_enabled',
    },
    'expose.preflight.summary': {
      checks: 'counted by expose.preflight.checks_count',
      blocking: 'counted by expose.preflight.blocking_count',
      warnings: 'counted by expose.preflight.warnings_count',
    },
    'expose.toast.created_warnings': {
      errors: 'the warning messages themselves, joined',
      more: 'counted by expose.toast.more',
    },
    'expose.toast.updated_warnings': {
      errors: 'the warning messages themselves, joined',
      more: 'counted by expose.toast.more',
    },
    'monitoring.tunnels.connections': {
      connections: 'counted by monitoring.tunnels.connection_count',
      clients: 'counted by monitoring.tunnels.client_count',
    },
    'services.bulk.result.checked_mixed': {
      ok: 'counted by services.bulk.result.reachable',
      failed: 'counted by services.bulk.result.unreachable',
    },
    'services.bulk.result.with_errors': {
      errors: 'the failure messages themselves, joined',
      more: 'counted by services.toast.more',
    },
    'services.drift.out_of_sync_body': {
      errors: 'counted by services.drift.errors',
      warnings: 'counted by services.drift.warnings',
    },
    'services.toast.deleted_warnings': {
      errors: 'the failure messages themselves, joined',
      more: 'counted by services.toast.more',
    },
    'settings.backup.restore_confirm_message': {
      services: 'counted by settings.backup.restore_count.services',
      providers: 'counted by settings.backup.restore_count.providers',
      domains: 'counted by settings.backup.restore_count.domains',
      tags: 'counted by settings.backup.restore_count.tags',
      environments: 'counted by settings.backup.restore_count.environments',
      webhooks: 'counted by settings.backup.restore_count.webhooks',
    },

    // A constant, or a bound that cannot be 1 -- and one range where the noun agrees with the
    // span rather than with either end, so `log_retention_days` reaching down to 1 is fine.
    'certificates.stat.expiring_hint': { days: 'the certificate warning window, a constant, never 1' },
    'certificates.stat.valid_hint': { days: 'the same window constant' },
    'providers.delete.deps_more': { count: 'the word "more" does not inflect, and neither do its translations' },
    'settings.auth.new_distinct': { count: 'MIN_PASSWORD_DISTINCT_CHARS, a constant, never 1' },
    'settings.auth.new_min': { min: 'MIN_PASSWORD_LENGTH, a constant, never 1' },
    'settings.auth.new_password': { min: 'the same length constant' },
    'settings.backup.passphrase_distinct': { count: 'the same distinct-characters constant, never 1' },
    'settings.backup.passphrase_min': { min: 'the same length constant' },
    'settings.backup.passphrase_placeholder': { min: 'the same length constant' },
    'settings.general.range_days': {
      min: 'a range bound: the noun agrees with the span, not with either end',
      max: 'the other end of that span',
    },
    'settings.general.range_minutes': {
      min: 'a range bound: the noun agrees with the span, not with either end',
      max: 'the other end of that span',
    },
    'settings.general.webhook_retry_retention_hint': {
      min: 'a range bound: the noun agrees with the span, not with either end',
      max: 'the other end of that span',
    },
    'setup.password.too_short': { min: 'the same length constant' },
    'templates.form.name_too_long': { max: 'TEMPLATE_NAME_MAX, a constant, never 1' },

    // A number with nothing beside it to agree.
    'layout.nav.item_with_badge': { badge: 'a badge in parentheses next to {label}; it can read "9+"' },
    'providers.health.score': { score: 'a score printed as {score}/100' },
    'settings.migration.import_selected': { count: 'a badge in parentheses, nothing agrees with it' },
    'setup.import.finish_import': { count: 'a bare number between a verb and a conjunction' },

    // A unit symbol. "min", "s", "ms" and "%" are symbols, not words, and do not inflect.
    'expose.preflight.detail.target_reachable': { ms: 'followed by the symbol ms' },
    'login.retry_in': { seconds: 'followed by the symbol s' },
    'monitoring.auto_checks_every': { minutes: 'followed by the symbol min' },
    'monitoring.refresh.minutes': { minutes: 'followed by the symbol min' },
    'monitoring.refresh.seconds': { seconds: 'followed by the symbol s' },
    'settings.logs.live_fallback_desc': { seconds: 'followed by the symbol s' },
    'settings.logs.live_unsupported': { seconds: 'followed by the symbol s' },
    'setup.progress.percent': { percent: 'already formatted, followed by %' },

    // A position in a sequence, which is an ordinal and not a quantity.
    'provider_modal.guided.go_to': { step: 'a position in the wizard' },
    'provider_modal.guided.step': { step: 'a position in the wizard', total: 'the wizard length, after "of"' },
    'provider_modal.step': { step: 'a position in the wizard', total: 'the wizard length, after "of"' },
    'settings.logs.page_of': { page: 'a position in the log', pages: 'the page count, after "of"' },
    'setup.progress.step': { step: 'a position in the wizard', total: 'the wizard length, after "of"' },
    'ui.error.page_unavailable': { page: 'the name of the page, not a number' },

    // Already a string by the time it gets here: formatted elsewhere, or a joined list.
    'certificates.days_left': { days: 'formatted by formatDays, whose Intl unit style inflects the unit itself' },
    'certificates.expired_ago': { days: 'the same' },
    'dashboard.stats.services_ok_hint': { percent: 'already formatted by formatPercent' },
    'dashboard.status.disk': { disk: 'already formatted, a percentage' },
    'dashboard.status.latency': { latency: 'already formatted, with its unit' },
    'expose.preflight.detail.tunnel_check_failed': { error: 'the error message text' },
    'monitoring.drawer.dns_resolved': { answers: 'the DNS answers, joined into one string' },
    'monitoring.uptime.aria': { percent: 'already formatted by formatPercent' },
    'providers.card.latency': { value: 'already formatted, with its unit' },
    'providers.card.version': { value: 'a version string, not a number' },
    'providers.diag.detail.provider_error': { error: 'the error message text' },
    'providers.diag.detail.zones_error': { error: 'the error message text' },
    'settings.general.not_applied': { keys: 'the setting names, joined into one string' },
    'settings.general.wan_priority_hint': { values: 'the accepted source names, joined into one string' },
  }),
);

const baseOf = (key) => key.replace(PLURAL_SUFFIX, '');
const namesIn = (value) => new Set([...String(value ?? '').matchAll(PLACEHOLDER)].map((m) => m[1]));
const braced = (names) => names.map((n) => `{${n}}`).join(', ');

let failed = false;

for (const file of files) {
  const locale = JSON.parse(readFileSync(join(localesDir, file), 'utf8'));

  for (const [key, value] of Object.entries(locale)) {
    if (CRUTCH_ALLOWED.has(key)) continue;
    const text = String(value ?? '');
    const crutch = COUNTED.test(text) ? CRUTCH.exec(text) : null;
    if (crutch) {
      failed = true;
      console.error(
        `Locale quality failed for ${file}: key '${key}' writes its plural by hand ` +
          `('${crutch[0]}'). Write '${baseOf(key)}_one' / '${baseOf(key)}_other' and let t() pick.`,
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
const pluralBases = new Set(Object.keys(reference).filter((k) => PLURAL_SUFFIX.test(k)).map(baseOf));
const used = new Set();
let declaredCount = 0;

for (const [key, value] of Object.entries(reference)) {
  const base = baseOf(key);

  for (const name of namesIn(value)) {
    if (NON_QUANTITY.has(name)) continue;

    if (!QUANTITY.has(name)) {
      failed = true;
      console.error(
        `Locale quality failed for en.json: key '${key}' uses '{${name}}', which is in neither ` +
          `QUANTITY nor NON_QUANTITY. Say which it is: a number a sentence could agree with, or ` +
          `a value no word inflects around.`,
      );
      continue;
    }

    // The one number `t()` can inflect, in a key that has the forms to inflect it.
    if (name === 'count' && pluralBases.has(base)) continue;

    if (DECLARED.get(base)?.[name]) {
      used.add(`${base} ${name}`);
      declaredCount += 1;
      continue;
    }

    failed = true;
    if (name === 'count') {
      console.error(
        `Locale quality failed for en.json: key '${key}' counts with {count} but has no plural ` +
          `form, so every number reads the same sentence. Write '${base}_one' / '${base}_other' in ` +
          `every locale that declares 'one' and '${base}_other' alone in ja and zh, or declare ` +
          `'${base}' in DECLARED with the reason nothing inflects around it.`,
      );
    } else {
      console.error(
        `Locale quality failed for en.json: key '${key}' prints '{${name}}' as a number t() cannot ` +
          `inflect, and nothing says why that is safe. Count the noun in its own key and ` +
          `interpolate the finished phrase, or move the noun beside {count}, or declare '${base}' ` +
          `in DECLARED with the reason nothing agrees with '{${name}}'.`,
      );
    }
  }
}

// A declaration is a claim about a sentence. One whose sentence no longer says that is a claim
// about nothing, and it would sit there covering a placeholder that comes back later.
for (const [base, reasons] of DECLARED) {
  for (const [name, why] of Object.entries(reasons)) {
    if (used.has(`${base} ${name}`)) continue;
    failed = true;
    console.error(
      `Locale quality failed for en.json: DECLARED says '${base}' prints '{${name}}' (${why}), ` +
        `which en.json no longer does. Remove the entry.`,
    );
  }
}

// A translation that loses a placeholder leaves a sentence missing its number, and one that
// invents a placeholder leaves the braces on screen. Neither is visible from en.json alone,
// and the parity check compares key names rather than what is inside them.
//
// Compared form by form rather than key by key: pooling `_one` and `_other` together lets a
// number dropped from one of them be covered by the other, which is the shape of the bug
// this is meant to catch. A locale is allowed a form en.json does not have -- parity permits
// `_many` without requiring it -- so those are held to everything the English key prints
// across all its forms.
//
// The single exception is `{count}` in a `_one` form, where "One tunnel is healthy" reads
// better than "1 tunnel is healthy" in most languages and means exactly the same thing. In
// `_other` it stays required: a plural sentence with no number in it is a broken sentence.
function formsByBase(locale) {
  const byBase = new Map();
  for (const key of Object.keys(locale)) {
    const base = baseOf(key);
    byBase.set(base, [...(byBase.get(base) ?? []), key]);
  }
  return byBase;
}

const referenceForms = formsByBase(reference);

for (const file of files) {
  if (file === 'en.json') continue;
  const locale = JSON.parse(readFileSync(join(localesDir, file), 'utf8'));

  for (const [key, value] of Object.entries(locale)) {
    const base = baseOf(key);
    if (!referenceForms.has(base)) continue;

    const counterpart = key in reference ? [key] : referenceForms.get(base);
    const want = new Set(counterpart.flatMap((k) => [...namesIn(reference[k])]));
    const names = namesIn(value);

    const missing = [...want]
      .filter((n) => !names.has(n) && !(n === 'count' && key.endsWith('_one')))
      .sort();
    const extra = [...names].filter((n) => !want.has(n)).sort();
    if (!missing.length && !extra.length) continue;

    failed = true;
    const parts = [];
    if (extra.length) parts.push(`interpolates ${braced(extra)}, which nothing ever fills`);
    if (missing.length) parts.push(`drops ${braced(missing)}, which en.json prints`);
    console.error(`Locale quality failed for ${file}: key '${key}' ${parts.join(', and ')}.`);
  }
}

if (failed) {
  process.exit(1);
}

console.log(
  `Locale semantic quality check passed for ${files.length} locale files ` +
    `(${declaredCount} numbers declared invariant, placeholders identical in every locale).`,
);
