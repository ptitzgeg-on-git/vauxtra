/**
 * The switch that a failed catalogue read used to take off the screen.
 *
 * "Update the DNS record automatically" is offered only when the selected DNS provider
 * declares `supports_auto_public_target`. That question is answered by `metaHasCapability`,
 * which falls back to a hardcoded table whenever `GET /api/providers/types` has not
 * answered, and that table had no entry for this capability at all. A missing entry is not
 * an absent answer: it reads as `false`.
 *
 * So with the catalogue down, the form did three things at once and said none of them. The
 * switch was not rendered. `effectivePublicTargetMode` rewrote `auto` to `manual`, and
 * `effectiveAutoUpdateDns` followed it to `false`, which is what the payload carries. And
 * selecting the DNS provider wrote both of those into the form state on the spot. The rest
 * of the section looked right throughout, because `public_dns` did have an entry, so
 * Cloudflare was still correctly treated as external DNS.
 *
 * These tests render the form with `providerTypeMap={{}}` -- the failing read, exactly -- and
 * `renderWithProviders` leaves out `I18nProvider` on purpose, so the assertions are the
 * locale keys rather than the English behind them.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Provider } from '@/types/api';
import type { FormState } from './types';
import { initialForm } from './types';
import { ServiceForm } from './ServiceForm';

const CLOUDFLARE: Provider = {
  id: 1,
  name: 'Cloudflare at the edge',
  type: 'cloudflare',
  url: 'https://api.cloudflare.com',
  username: 'token',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

/** deSEC declares the same capability, and was floored to `false` by the same absent entry. */
const DESEC: Provider = { ...CLOUDFLARE, id: 2, name: 'deSEC', type: 'desec' };

/** Pi-hole is the negative: it genuinely cannot resolve a public target on its own. */
const PIHOLE: Provider = { ...CLOUDFLARE, id: 3, name: 'Pi-hole', type: 'pihole' };

/** A saved route that had automatic DNS updates on, reopened for editing. */
const editing = (dnsProviderId: string): FormState => ({
  ...initialForm,
  ui_expose_mode: 'dns_only',
  domain: 'example.test',
  subdomain: 'app',
  target_ip: '10.0.0.1',
  target_port: 8080,
  dns_provider_id: dnsProviderId,
  public_target_mode: 'auto',
  auto_update_dns: true,
});

/** Everything the provider read can say beyond its rows, defaulted to "it answered". */
interface ReadState {
  providersError?: boolean;
  isRefetchingProviders?: boolean;
  refetchProviders?: () => void;
}

const renderForm = (formData: FormState, providers: Provider[], read: ReadState = {}) =>
  renderWithProviders(
    <ServiceForm
      formData={formData}
      setFormData={vi.fn()}
      providers={providers}
      domains={['example.test']}
      providersError={read.providersError ?? false}
      isRefetchingProviders={read.isRefetchingProviders ?? false}
      refetchProviders={read.refetchProviders}
      isLoadingProviders={false}
      isLoadingDomains={false}
      providerTypeMap={{}}
      targetSuggestion={undefined}
      isFetchingTargetSuggestion={false}
      refetchTargetSuggestion={vi.fn()}
      tags={[]}
      environments={[]}
      isLoadingTaxonomy={false}
    />,
  );

const autoUpdateSwitch = () => screen.queryByText('expose.field.auto_update_dns');

describe('ServiceForm, with the type catalogue unread', () => {
  it('still offers automatic DNS updates for Cloudflare', () => {
    renderForm(editing('1'), [CLOUDFLARE]);
    expect(autoUpdateSwitch()).not.toBeNull();
  });

  it('still offers them for deSEC', () => {
    renderForm(editing('2'), [DESEC]);
    expect(autoUpdateSwitch()).not.toBeNull();
  });

  it('does not offer them for a DNS provider that cannot do it', () => {
    renderForm(editing('3'), [PIHOLE]);
    expect(autoUpdateSwitch()).toBeNull();
  });

  it('keeps it on when the saved route had it on', () => {
    renderForm(editing('1'), [CLOUDFLARE]);
    const toggle = screen.getByRole('switch', { name: 'expose.field.auto_update_dns' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });
});

/**
 * The second half of the same omission, one file over.
 *
 * `ExposeModal` read `/providers` as `data ?? []` and destructured only `isLoading`, while
 * the three reads under it -- domains, tags, environments -- each carried an `isError` the
 * form already knew how to say. A request that failed is not loading, so the skeleton gave
 * way to a form drawn from an empty list, and that form made claims: no reverse proxy is
 * configured yet, no other proxy provider available, no DNS provider. `validate()` then
 * refused to continue, correctly, and named the operator's setup as the reason -- "choose at
 * least a proxy or a DNS provider" -- for a list nobody had read. The way out it implied was
 * the Providers page, to create integrations that were already there.
 */
describe('ServiceForm, with the provider list unread', () => {
  const empty: FormState = { ...initialForm, domain: 'example.test', target_ip: '10.0.0.1' };
  const alert = () => screen.queryByText('expose.providers.unread');
  const unavailable = () => screen.queryAllByText('ui.error.list_unavailable');

  it('says the list could not be read rather than letting the form speak for it', () => {
    renderForm(empty, [], { providersError: true });
    expect(alert()).not.toBeNull();
  });

  it('says nothing of the sort when the instance really has no provider', () => {
    renderForm(empty, []);
    expect(alert()).toBeNull();
    expect(screen.queryByText('expose.field.extra_proxies_empty')).not.toBeNull();
    expect(screen.queryByText('expose.field.extra_dns_empty')).not.toBeNull();
  });

  it('stays quiet when the failure was a refresh over rows that had already arrived', () => {
    // Those rows are in the selects below, so the lists are no longer speaking for a read.
    renderForm(empty, [CLOUDFLARE], { providersError: true });
    expect(alert()).toBeNull();
  });

  it('withdraws both "you have none of these" lines while the list is unread', () => {
    renderForm(empty, [], { providersError: true });
    expect(unavailable()).toHaveLength(2);
    expect(screen.queryByText('expose.field.extra_proxies_empty')).toBeNull();
    expect(screen.queryByText('expose.field.extra_dns_empty')).toBeNull();
  });

  it('offers a retry that refetches the list', async () => {
    const refetchProviders = vi.fn();
    renderForm(empty, [], { providersError: true, refetchProviders });
    await userEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    expect(refetchProviders).toHaveBeenCalledTimes(1);
  });

  it('reports that retry busy while the refetch is in flight', () => {
    renderForm(empty, [], {
      providersError: true,
      isRefetchingProviders: true,
      refetchProviders: vi.fn(),
    });
    // The busy state also disables the button it is on, so it must mean this read alone.
    // A loading Button puts its spinner's label in front of its own, hence the pattern.
    expect(screen.getByRole('button', { name: /common\.retry/ })).toBeDisabled();
  });

  it('leaves that retry usable when nothing is in flight', () => {
    renderForm(empty, [], { providersError: true, refetchProviders: vi.fn() });
    expect(screen.getByRole('button', { name: 'common.retry' })).toBeEnabled();
  });
});
