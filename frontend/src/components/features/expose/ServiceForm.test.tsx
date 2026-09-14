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

const renderForm = (formData: FormState, providers: Provider[]) =>
  renderWithProviders(
    <ServiceForm
      formData={formData}
      setFormData={vi.fn()}
      providers={providers}
      domains={['example.test']}
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
