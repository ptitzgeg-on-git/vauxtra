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
    const line = await say('fr', 'monitoring.uptime.summary', { percent: '99 %', count: 12 });
    expect(line).toBe('99 % sur 12 contrôle(s)');
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
