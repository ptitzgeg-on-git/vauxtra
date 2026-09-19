/**
 * Who decides that a route finds its public target automatically, and what that costs.
 *
 * The decision has two consumers that must never disagree: the payload `ExposeModal` sends,
 * and the switch `ServiceForm` draws. It used to be written once for each, and the two
 * copies differed by a single clause -- the payload asked whether the selected DNS provider
 * had been found at all, the form did not. So for a `dns_provider_id` naming a provider the
 * catalogue could not resolve, deleted or simply not answered for yet, the form computed
 * `manual` with the automatic update off while the payload sent `auto` with it on.
 *
 * Nothing ever showed it. The form's value has exactly one consumer, the switch, and the
 * switch is only drawn for a provider that resolves -- which is every input where the two
 * agreed. The disagreement lived only where nobody could see it, and would have surfaced
 * the day someone widened that condition.
 *
 * The payload's reading is the one that survived: a provider that cannot be resolved has
 * said nothing, and reading nothing as "cannot" is what dropped an operator's automatic
 * update the last time it happened -- see `CAPABILITY_FALLBACK` in `lib/providers.ts` and
 * the case `providers.test.ts` keeps for it.
 */

import { describe, expect, it } from 'vitest';
// The two consumers read as text, so a second writer of the rule is a failing test
// rather than a thing someone has to notice in review.
import exposeModalSource from './ExposeModal.tsx?raw';
import serviceFormSource from './ServiceForm.tsx?raw';
import {
  autoPublicTarget,
  initialForm,
  preflightDetailText,
  publicTargetSourceLabel,
  type FormState,
} from './types';
import type { Provider, ProviderTypesResponse } from '@/types/api';

/** Two DNS providers that differ in the only way this rule cares about. */
const CATALOGUE: ProviderTypesResponse = {
  cloudflare: { capabilities: { dns: true, public_dns: true, supports_auto_public_target: true } },
  pihole: { capabilities: { dns: true, public_dns: false, supports_auto_public_target: false } },
};

const CLOUDFLARE: Pick<Provider, 'type'> = { type: 'cloudflare' };
const PIHOLE: Pick<Provider, 'type'> = { type: 'pihole' };

/** What the catalogue answers for a provider it has never heard of: the empty case. */
const UNRESOLVED = undefined;

const form = (over: Partial<FormState> = {}): FormState => ({ ...initialForm, ...over });

describe('a provider that says it cannot resolve a public target', () => {
  it('sends the target back to manual and the automatic update with it', () => {
    const answer = autoPublicTarget(
      form({ public_target_mode: 'auto', auto_update_dns: true, dns_provider_id: '2' }),
      PIHOLE,
      CATALOGUE,
    );
    expect(answer).toEqual({ mode: 'manual', autoUpdateDns: false, canOfferAuto: false });
  });

  it('offers no switch, because there is nothing true it could be set to', () => {
    const answer = autoPublicTarget(form({ dns_provider_id: '2' }), PIHOLE, CATALOGUE);
    expect(answer.canOfferAuto).toBe(false);
  });
});

describe('a provider that says it can', () => {
  it('leaves the mode alone and passes the operator own setting through', () => {
    for (const saved of [true, false]) {
      const answer = autoPublicTarget(
        form({ public_target_mode: 'auto', auto_update_dns: saved, dns_provider_id: '1' }),
        CLOUDFLARE,
        CATALOGUE,
      );
      expect(answer).toEqual({ mode: 'auto', autoUpdateDns: saved, canOfferAuto: true });
    }
  });

  it('still reports the update off while the target is manual', () => {
    const answer = autoPublicTarget(
      form({ public_target_mode: 'manual', auto_update_dns: true, dns_provider_id: '1' }),
      CLOUDFLARE,
      CATALOGUE,
    );
    expect(answer.mode).toBe('manual');
    expect(answer.autoUpdateDns).toBe(false);
    expect(answer.canOfferAuto).toBe(true);
  });
});

describe('a provider the catalogue cannot resolve', () => {
  it('changes nothing, because it has not said it cannot', () => {
    const answer = autoPublicTarget(
      form({ public_target_mode: 'auto', auto_update_dns: true, dns_provider_id: '999' }),
      UNRESOLVED,
      CATALOGUE,
    );
    expect(answer.mode).toBe('auto');
    expect(answer.autoUpdateDns).toBe(true);
  });

  it('is the row the form and the payload used to answer differently', () => {
    // The form said `manual` with the update off; the payload sent `auto` with it on. Both
    // now come from this call, so there is one answer to disagree with.
    const state = form({ public_target_mode: 'auto', auto_update_dns: true, dns_provider_id: '999' });
    expect(autoPublicTarget(state, UNRESOLVED, CATALOGUE)).toEqual(
      autoPublicTarget(state, UNRESOLVED, CATALOGUE),
    );
    expect(autoPublicTarget(state, UNRESOLVED, CATALOGUE).mode).not.toBe('manual');
  });

  it('draws no switch either, so the preserved setting is not presented as a choice', () => {
    const answer = autoPublicTarget(form({ dns_provider_id: '999' }), UNRESOLVED, CATALOGUE);
    expect(answer.canOfferAuto).toBe(false);
  });
});

describe('no DNS provider at all', () => {
  it('is not a provider saying no', () => {
    const answer = autoPublicTarget(
      form({ public_target_mode: 'auto', auto_update_dns: true, dns_provider_id: '' }),
      UNRESOLVED,
      CATALOGUE,
    );
    expect(answer.mode).toBe('auto');
    expect(answer.autoUpdateDns).toBe(true);
    expect(answer.canOfferAuto).toBe(false);
  });
});

describe('a catalogue that never answered', () => {
  it('does not turn a provider that can into one that cannot', () => {
    // `CAPABILITY_FALLBACK` in `lib/providers.ts` is the floor for exactly this moment, and
    // dropping an operator's automatic update while the catalogue was down is what put it
    // there. Read through this rule so the consequence is pinned where it is felt.
    const answer = autoPublicTarget(
      form({ public_target_mode: 'auto', auto_update_dns: true, dns_provider_id: '1' }),
      CLOUDFLARE,
      {},
    );
    expect(answer).toEqual({ mode: 'auto', autoUpdateDns: true, canOfferAuto: true });
  });

  it('still says no for a provider the floor does not name', () => {
    const answer = autoPublicTarget(
      form({ public_target_mode: 'auto', auto_update_dns: true, dns_provider_id: '2' }),
      PIHOLE,
      {},
    );
    expect(answer.mode).toBe('manual');
    expect(answer.autoUpdateDns).toBe(false);
  });
});

describe('the rule has one writer', () => {
  it('is not asked again by the form that draws the answer', () => {
    // `ServiceForm` used to derive the mode itself and differed from the payload by one
    // clause. It now reads `publicTarget`, so there is nothing left for it to get wrong.
    expect(serviceFormSource).not.toContain('supports_auto_public_target');
  });

  it('is asked once by the modal, and not to decide the mode', () => {
    // The one read left decides whether to spend a request on a target suggestion at all,
    // which is a different question from what the target mode is. A second read would mean
    // the decision had grown a third writer, and that is what this count is here to notice.
    const source = exposeModalSource;
    expect(source.split('supports_auto_public_target').length - 1).toBe(1);
    expect(source).toContain('publicTarget.mode');
  });
});

describe('publicTargetSourceLabel', () => {
  // The preflight prints this word inside its own sentence, so an untranslated one reads
  // "Cible DNS 10.0.0.99 (manual)" on a French panel.
  const t = (key: string) => (key === 'expose.dry_run.source.manual' ? 'saisie a la main' : key);

  it('translates a word the build knows', () => {
    expect(publicTargetSourceLabel('manual', t)).toBe('saisie a la main');
  });

  it('returns an unknown word as it came, so a newer API never blanks the line', () => {
    expect(publicTargetSourceLabel('some_future_source', t)).toBe('some_future_source');
  });

  it('answers nothing when the API sent nothing', () => {
    expect(publicTargetSourceLabel('', t)).toBe('');
    expect(publicTargetSourceLabel(undefined, t)).toBe('');
  });
});

describe('preflightDetailText', () => {
  // The catalogue this panel really ships: the sentence, and the words it prints inside it.
  const CATALOGUE: Record<string, string> = {
    'expose.preflight.detail.dns_resolved_local': 'Cible DNS sur le LAN : {target} ({source})',
    'expose.preflight.detail.dns_resolved_public': 'Cible DNS publique : {target} ({source})',
    'expose.dry_run.source.manual': 'saisie a la main',
  };
  const t = (key: string, vars?: Record<string, string | number>) => {
    const line = CATALOGUE[key];
    if (line === undefined) return key;
    return line.replace(/\{(\w+)\}/g, (_m, name) => String(vars?.[name] ?? `{${name}}`));
  };

  it('translates the source word instead of printing the wire word', () => {
    expect(
      preflightDetailText(
        {
          detail: 'Resolved LAN DNS target: 10.0.0.99 (manual)',
          detail_key: 'dns_resolved_local',
          detail_params: { target: '10.0.0.99', source: 'manual' },
        },
        t,
      ),
    ).toBe('Cible DNS sur le LAN : 10.0.0.99 (saisie a la main)');
  });

  it('says LAN or public according to the code the server chose', () => {
    const params = { target: '203.0.113.9', source: 'manual' };
    expect(
      preflightDetailText({ detail: '', detail_key: 'dns_resolved_public', detail_params: params }, t),
    ).toBe('Cible DNS publique : 203.0.113.9 (saisie a la main)');
  });

  it('falls back to the English sentence for a code this build does not know', () => {
    expect(
      preflightDetailText(
        { detail: 'Resolved DNS target: 10.0.0.99 (manual)', detail_key: 'dns_resolved_sideways' },
        t,
      ),
    ).toBe('Resolved DNS target: 10.0.0.99 (manual)');
  });

  it('leaves a sentence with no source word alone', () => {
    expect(preflightDetailText({ detail: 'Host is free' }, t)).toBe('Host is free');
  });
});
