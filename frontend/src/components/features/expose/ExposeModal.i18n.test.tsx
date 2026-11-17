/**
 * The words of the edit form's dry run, read through the real catalogues.
 *
 * The plan is computed from the saved route: `POST /services/{id}/push/dry-run` takes no
 * body, so whatever the form holds is not in it. The description promised "what the
 * providers would change on save", which is the form's edits, and the operator read a
 * "no change" as "my edits are harmless" (ux-audit.md § 1.3). It must name the saved version.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { I18nProvider, useT, type Lang } from '@/i18n';

const DESCRIPTION = 'expose.dry_run.description';

function Probe({ k }: { k: string }) {
  const t = useT();
  return <p data-testid="out">{t(k)}</p>;
}

async function say(lang: Lang, key: string): Promise<string> {
  cleanup();
  localStorage.setItem('vauxtra_lang', lang);
  render(
    <I18nProvider>
      <Probe k={key} />
    </I18nProvider>,
  );
  return (await screen.findByTestId('out')).textContent ?? '';
}

beforeEach(() => {
  localStorage.clear();
});

describe('the dry run description', () => {
  it('describes the saved version, not the form about to be saved', async () => {
    const english = await say('en', DESCRIPTION);
    expect.soft(english).toMatch(/\bsaved version\b/);
    expect.soft(english).not.toMatch(/\bon save\b/);
  });

  it('says the same in every language', async () => {
    const savedVersion: Record<Exclude<Lang, 'en'>, RegExp> = {
      fr: /version enregistrée/,
      de: /gespeicherte Version/,
      es: /versión guardada/,
      nl: /opgeslagen versie/,
      pt: /versão salva/,
      ja: /保存済みのバージョン/,
      zh: /已保存版本/,
    };
    for (const [lang, pattern] of Object.entries(savedVersion) as [Lang, RegExp][]) {
      expect.soft(await say(lang, DESCRIPTION), lang).toMatch(pattern);
    }
  });
});

// Said when the target field splits an address: on a paste and on a value typed then left,
// for a URL and for `host:port` alike. It spoke of "the pasted URL" in every case.
const SPLIT = 'expose.target_parsed';

describe('the sentence said when an address is split', () => {
  it('says what happened, in words true of every way there', async () => {
    expect.soft(await say('en', SPLIT)).toBe('The address was split across the fields.');
    expect.soft(await say('fr', SPLIT)).toBe("L'adresse a été répartie dans les champs.");
  });

  it('names neither a paste nor a URL, in any language', async () => {
    const pasted: Record<Lang, RegExp> = {
      en: /pasted/i,
      fr: /coll[ée]e/i,
      de: /eingefügt/i,
      es: /pegad/i,
      nl: /geplakt/i,
      pt: /colad/i,
      ja: /貼り付け/,
      zh: /粘贴/,
    };
    for (const [lang, pattern] of Object.entries(pasted) as [Lang, RegExp][]) {
      const said = await say(lang, SPLIT);
      expect.soft(said, lang).not.toMatch(pattern);
      expect.soft(said, lang).not.toMatch(/URL/);
    }
  });
});
