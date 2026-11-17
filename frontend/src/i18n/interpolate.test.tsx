/**
 * `t()` writes a parameter into its sentence as the text it is, whatever that text holds.
 *
 * The placeholder is swapped by `String.prototype.replace`, and a replacement given as a
 * string is a pattern of its own: `$&` stands for the match, `` $` `` and `$'` for what comes
 * before and after it, `$$` for one dollar sign. A value carrying any of them, such as a host
 * read from an integration's URL, came out as something else than what was passed.
 *
 * Through the real `I18nProvider` and the real locale files, as `plural.test.tsx` does.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider, useT, type Lang } from './index';

function Probe({ k, params }: { k: string; params?: Record<string, string | number> }) {
  const t = useT();
  return <p data-testid="out">{t(k, params)}</p>;
}

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

describe('t(), parameters', () => {
  it.each(['a$&b', 'a$`b', "a$'b", 'a$$b'])('writes %s as it was passed', async (host) => {
    expect(await say('en', 'services.open_host', { host })).toBe(`Open ${host} in a new tab`);
  });
});
