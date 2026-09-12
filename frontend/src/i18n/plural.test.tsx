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
    expect(await say('fr', 'layout.nav.item_with_badge', { label: 'Services', count: 12 })).toBe(
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
    // A caller that formats the number itself gets what it asked for, and — a string being
    // unable to select a plural — the `_other` form. Wrong at seven only in languages where
    // seven is not `other`; still a sentence, which the raw key would not be.
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
