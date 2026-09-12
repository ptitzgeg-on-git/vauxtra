/**
 * `t()` chooses a plural form from the locale's CLDR rules, not from `count === 1`.
 *
 * Every call site that needed a plural used to write the test itself, and every one of them
 * wrote the English rule: `count === 1 ? singular : plural`. French and Portuguese put zero
 * in the `one` category, so an empty list read "0 intégrations" and "0 modelos" where the
 * language wants "0 intégration" and "0 modelo". Japanese and Chinese have a single form and
 * were being handed a singular they can never select.
 *
 * These tests go through the real `I18nProvider` and the real locale files: the point is the
 * sentence an operator reads, and a stubbed dictionary would only prove the lookup compiles.
 */

import { describe, expect, it, beforeEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider, useT, type Lang } from './index';

function Probe({ k, params }: { k: string; params?: Record<string, string | number> }) {
  const t = useT();
  return <p data-testid="out">{t(k, params)}</p>;
}

/**
 * Render one key in one language and wait for that language's file to land.
 *
 * `cleanup()` first, so a test may ask twice: two live renders means two `out` nodes and
 * `findByTestId` refuses to choose between them.
 */
async function say(lang: Lang, key: string, params?: Record<string, string | number>) {
  cleanup();
  localStorage.setItem('vauxtra_lang', lang);
  render(
    <I18nProvider>
      <Probe k={key} params={params} />
    </I18nProvider>,
  );
  const out = await screen.findByTestId('out');
  return out.textContent ?? '';
}

beforeEach(() => {
  localStorage.clear();
});

describe('t(), plural forms', () => {
  it('puts zero in the singular in French, because French does', async () => {
    // The reachable case: the integrations page with nothing configured yet.
    expect(await say('fr', 'providers.meta.count', { count: 0 })).toBe('0 intégration');
  });

  it('puts zero in the plural in English, because English does', async () => {
    expect(await say('en', 'providers.meta.count', { count: 0 })).toBe('0 integrations');
  });

  it('still takes the singular at one', async () => {
    expect(await say('fr', 'providers.meta.count', { count: 1 })).toBe('1 intégration');
    expect(await say('en', 'templates.count', { count: 1 })).toBe('1 template');
  });

  it('takes the plural above one', async () => {
    expect(await say('fr', 'providers.meta.count', { count: 4 })).toBe('4 intégrations');
  });

  it('falls back to _other for a language that has no singular at all', async () => {
    // ja.json carries no `_one`: the parity check refuses one, and this is why.
    const ja = await say('ja', 'templates.count', { count: 1 });
    expect(ja).toContain('1');
    expect(ja).not.toBe('templates.count');
  });

  it('falls back to _other for a category the file did not write', async () => {
    // French declares a `many` category that fires at a million. No translator should have
    // to write that sentence, so the file stops at `one` and `other`.
    const many = new Intl.PluralRules('fr-FR').select(1_000_000);
    expect(many).toBe('many');
    expect(await say('fr', 'providers.meta.count', { count: 1_000_000 })).toContain('intégrations');
  });

  it('leaves a key with no plural siblings exactly as it was', async () => {
    // A badge, not a sentence: "{label} ({count})" has nothing to inflect anywhere.
    expect(await say('fr', 'layout.nav.item_with_badge', { label: 'Services', badge: 12 })).toBe(
      'Services (12)',
    );
  });
});

describe('t(), the count it prints', () => {
  it('writes the number with the locale separators', async () => {
    // Not hard-coded: ICU picks the separator, and which space French uses is its business.
    expect(await say('fr', 'providers.meta.count', { count: 1234 })).toBe(
      `${new Intl.NumberFormat('fr-FR').format(1234)} intégrations`,
    );
    expect(await say('en', 'providers.meta.count', { count: 1234 })).toBe('1,234 integrations');
  });

  it('leaves a count that arrives as a string alone', async () => {
    // `TranslateParams` now refuses a string `count` at any call site that writes one out,
    // so this goes the way round that a type cannot see: `say()` declares the looser
    // `Record<string, string | number>`, as untyped callers and `Object.fromEntries` do.
    // What the runtime does with it still has to be right — a string cannot select a plural,
    // so it lands on `_other`. Wrong at seven only in languages where seven is not `other`;
    // still a sentence, which the raw key would not be.
    expect(await say('fr', 'providers.meta.count', { count: '7' })).toBe('7 intégrations');
  });
});

describe('t(), the sentences that carried a (s)', () => {
  it('inflects a sentence that used to print its own parenthesis', async () => {
    // Before: "99 % sur 12 contrôle(s)", in all eight files, at every count.
    expect(await say('fr', 'monitoring.uptime.summary', { percent: '99 %', count: 1 })).toBe(
      '99 % sur 1 contrôle',
    );
    expect(await say('fr', 'monitoring.uptime.summary', { percent: '99 %', count: 12 })).toBe(
      '99 % sur 12 contrôles',
    );
  });

  it('agrees the verb too, not only the noun', async () => {
    // The crutch only marked the noun. "{count} service(s) utilise(nt)" was never written,
    // so the singular had to be rewritten by hand for every sentence with a verb in it.
    expect(await say('fr', 'settings.migration.quick_import_skipped', { count: 1 })).toBe(
      '1 service déjà suivi sera ignoré.',
    );
    expect(await say('fr', 'settings.migration.quick_import_skipped', { count: 3 })).toBe(
      '3 services déjà suivis seront ignorés.',
    );
  });

  it('selects on {count} while the other numbers ride along', async () => {
    // `monitoring.check_summary` carries three numbers. Only the first one chooses a form.
    expect(
      await say('fr', 'monitoring.check_summary', { count: 1, ok: 1, error: 0 }),
    ).toBe('1 service vérifié : 1 en ligne, 0 hors ligne');
    expect(
      await say('fr', 'monitoring.check_summary', { count: 7, ok: 6, error: 1 }),
    ).toBe('7 services vérifiés : 6 en ligne, 1 hors ligne');
  });
});

describe('t(), a sentence that counts two different things', () => {
  /**
   * `monitoring.tunnels.connections` used to read "{connections} connexion(s), {clients}
   * client(s)". One `count` cannot choose two forms, so the line is now two counted keys
   * and a joiner -- and the joiner owns the separator, which is not a comma everywhere.
   */
  async function tunnelLine(lang: Lang, connections: number, clients: number) {
    const conn = await say(lang, 'monitoring.tunnels.connection_count', { count: connections });
    const cli = await say(lang, 'monitoring.tunnels.client_count', { count: clients });
    return say(lang, 'monitoring.tunnels.connections', { connections: conn, clients: cli });
  }

  it('inflects each half on its own count', async () => {
    expect(await tunnelLine('fr', 1, 4)).toBe('1 connexion, 4 clients');
    expect(await tunnelLine('fr', 4, 1)).toBe('4 connexions, 1 client');
  });

  it('puts zero in the singular in French, on both halves', async () => {
    expect(await tunnelLine('fr', 0, 0)).toBe('0 connexion, 0 client');
  });

  it('lets the joiner choose the separator, which Japanese does not write as a comma', async () => {
    const line = await tunnelLine('ja', 2, 3);
    expect(line).toContain('\u3001');
    expect(line).not.toContain(',');
  });
});

describe('t(), the number beside the one it inflects on', () => {
  /**
   * These are the sentences the first version of the quality check could not see. It only
   * asked about `{count}`, so a key could carry a second number with a noun of its own and
   * nothing would notice that the noun was frozen at whichever form the file happened to
   * write. "1 route shown of 1" is the shape of the bug.
   */
  it('agrees with the number it inflects on, not with the total', async () => {
    expect(await say('fr', 'services.meta', { count: 1, total: 5 })).toBe(
      '1 route affichée sur 5',
    );
    expect(await say('fr', 'services.meta', { count: 5, total: 5 })).toBe(
      '5 routes affichées sur 5',
    );
  });

  it('reads correctly when both numbers are one', async () => {
    expect(await say('en', 'services.meta', { count: 1, total: 1 })).toBe('1 route shown of 1');
  });

  it('inflects an adjective English leaves alone', async () => {
    // "healthy" is the same word at every count, so en.json writes the two forms identically
    // and only French shows the difference. The English file still needs both: a language
    // does not stop declaring a singular because one of its adjectives ignores it.
    expect(await say('fr', 'monitoring.tunnels.healthy_of', { count: 1, total: 4 })).toBe(
      '1/4 opérationnel',
    );
    expect(await say('fr', 'monitoring.tunnels.healthy_of', { count: 4, total: 4 })).toBe(
      '4/4 opérationnels',
    );
    expect(await say('en', 'monitoring.tunnels.healthy_of', { count: 1, total: 4 })).toBe(
      '1/4 healthy',
    );
  });

  it('lets a language put the total first', async () => {
    // Japanese counts the other way round, "of 5, showing 1". Which number comes first is the
    // file's business; that the noun agrees with `{count}` is not.
    expect(await say('ja', 'services.meta', { count: 1, total: 5 })).toBe(
      '5件中1件のルートを表示',
    );
  });
});

describe('t(), a count that selects a form without printing itself', () => {
  /**
   * Two sentences wrap a joined list and say "these settings" / "These integrations" around
   * it. Both are reachable with exactly one item, and neither can print `{count}` -- the list
   * is already on screen. So the call site passes the length anyway and `{count}` does
   * nothing but choose the form.
   */
  it('singularises the sentence around a one-item list', async () => {
    expect(
      await say('fr', 'settings.general.ignored_keys', { count: 1, keys: 'check_interval' }),
    ).toBe('Le serveur a refusé ce paramètre : check_interval');
  });

  it('pluralises it around a longer one', async () => {
    expect(
      await say('fr', 'settings.general.ignored_keys', {
        count: 2,
        keys: 'check_interval, log_retention_days',
      }),
    ).toBe('Le serveur a refusé ces paramètres : check_interval, log_retention_days');
  });

  it('never prints the count it selected on', async () => {
    const one = await say('en', 'certificates.empty.none_hint', {
      count: 1,
      providers: 'Cloudflare',
    });
    expect(one).toContain('This integration was queried');
    expect(one).not.toContain('1');

    const many = await say('en', 'certificates.empty.none_hint', {
      count: 2,
      providers: 'Cloudflare, Traefik',
    });
    expect(many).toContain('These integrations were queried');
    expect(many).not.toContain('2');
  });
});

describe('t(), a sentence assembled from counted halves', () => {
  /**
   * When a sentence carries more than one noun to inflect, the numbers cannot share `{count}`.
   * Each noun is counted in its own key and the finished phrase is interpolated, which also
   * hands the joiner to the file: French writes " et ", Japanese does not write it at all.
   */
  async function driftLine(lang: Lang, errors: number, warnings: number) {
    const e = await say(lang, 'services.drift.errors', { count: errors });
    const w = await say(lang, 'services.drift.warnings', { count: warnings });
    return say(lang, 'services.drift.out_of_sync_body', { errors: e, warnings: w });
  }

  it('inflects each half on its own count', async () => {
    expect(await driftLine('fr', 1, 3)).toContain('1 erreur et 3 avertissements');
    expect(await driftLine('fr', 3, 1)).toContain('3 erreurs et 1 avertissement');
  });

  it('lets the sentence own its joiner', async () => {
    const ja = await driftLine('ja', 2, 2);
    expect(ja).toContain('エラー2件、警告2件');
    expect(ja).not.toContain(' et ');
  });

  it('counts the two halves of the integrations hint apart', async () => {
    const healthy = await say('fr', 'dashboard.stats.providers_healthy', { count: 1 });
    const enabled = await say('fr', 'dashboard.stats.providers_enabled', { count: 3 });
    expect(await say('fr', 'dashboard.stats.providers_hint', { healthy, enabled })).toBe(
      '1 opérationnelle · 3 activées',
    );
  });

  it('counts six nouns in one sentence, each on its own number', async () => {
    // The restore dialog, where one `{count}` would have had to serve six different words.
    const counts = { services: 1, providers: 2, domains: 1, tags: 0, environments: 1, webhooks: 4 };
    const parts: Record<string, string> = {};
    for (const [key, count] of Object.entries(counts)) {
      parts[key] = await say('fr', `settings.backup.restore_count.${key}`, { count });
    }
    const message = await say('fr', 'settings.backup.restore_confirm_message', parts);
    // Zero is singular in French, and four is not: both in the same sentence.
    expect(message).toContain(
      '1 service, 2 intégrations, 1 domaine, 0 étiquette, 1 environnement, 4 webhooks',
    );
    expect(message).not.toContain('{');
  });
});
