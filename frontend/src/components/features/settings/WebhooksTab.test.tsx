/**
 * A rule that is switched on and can never fire.
 *
 * `webhooks.scope_ref_id` names a provider or a service through a bare INTEGER column: no
 * foreign key, so deleting the target cascades nothing and blanks nothing. The row survives
 * with its scope intact, the scheduler's scope match answers false from then on for ever,
 * and this tab used to render it as "Choose a provider" -- word for word the line a rule
 * nobody had finished configuring gets. Two opposite states, one sentence.
 *
 * The trap in fixing that is the third state. Before the providers query answers, its list
 * is empty too, and an id that matches nothing in an empty list is not a deleted provider:
 * it is a provider nobody has fetched yet. Claiming "deleted" there would be a false alarm
 * on every single load of the page, which is worse than the silence it replaced. So the
 * claim is gated on `isSuccess`, and the loading case is a test of its own below.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { Provider, Service, Webhook } from '@/types/api';

let webhookRows: Webhook[] = [];
let providerRows: Provider[] = [];
let serviceRows: Service[] = [];
/** A query that never settles, which is what the first paint of this tab actually has. */
let providersPending = false;

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/webhooks') return Promise.resolve(webhookRows);
      if (path === '/services') return Promise.resolve(serviceRows);
      if (path === '/providers') {
        return providersPending ? new Promise(() => {}) : Promise.resolve(providerRows);
      }
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { WebhooksTab } = await import('./WebhooksTab');

const PROVIDER: Provider = {
  id: 42,
  name: 'cloudflare-home',
  type: 'cloudflare',
  url: 'https://api.cloudflare.com',
  username: '',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

const SERVICE: Service = {
  id: 7,
  subdomain: 'git',
  domain: 'example.test',
  target_ip: '10.0.0.10',
  target_port: 3000,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: '',
  dns_ip: '',
  npm_host_id: null,
  dns_provider_id: null,
  proxy_provider_id: null,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: null,
  created_at: '2026-01-01T00:00:00Z',
  tags: [],
  environments: [],
};

function webhook(over: Partial<Webhook> = {}): Webhook {
  return {
    id: 1,
    name: 'on-call',
    url_masked: 'json://',
    enabled: true,
    created_at: '2026-01-01T00:00:00Z',
    scope_type: 'provider',
    scope_ref_id: PROVIDER.id,
    repeat_interval_minutes: 0,
    alert_on_any_down: false,
    alert_on_any_up: false,
    alert_on_integration_down: false,
    alert_on_integration_up: false,
    min_down_minutes: 0,
    ...over,
  };
}

/** The row itself, awaited, so nothing below races the three queries. */
const row = () => screen.findByText('on-call');
const brokenBadge = () => screen.queryByText('settings.webhooks.scope_gone_badge');

describe('WebhooksTab, a scope whose target is gone', () => {
  beforeEach(() => {
    webhookRows = [webhook()];
    providerRows = [PROVIDER];
    serviceRows = [SERVICE];
    providersPending = false;
    vi.clearAllMocks();
  });

  it('names the provider while the provider is still there', async () => {
    renderWithProviders(<WebhooksTab />);
    await row();

    expect(screen.getByText('settings.webhooks.scope_provider_named')).toBeInTheDocument();
    expect(brokenBadge()).toBeNull();
  });

  it('says the provider is gone once the list has come back without it', async () => {
    providerRows = [];
    renderWithProviders(<WebhooksTab />);
    await row();

    // The enabled badge next to this one is telling the truth -- the rule is switched on --
    // and that is exactly the problem: on its own it reads as a rule that works.
    expect(await screen.findByText('settings.webhooks.scope_provider_gone')).toBeInTheDocument();
    expect(brokenBadge()).not.toBeNull();
  });

  it('says the same about a service that was deleted under it', async () => {
    webhookRows = [webhook({ scope_type: 'service', scope_ref_id: SERVICE.id })];
    serviceRows = [];
    renderWithProviders(<WebhooksTab />);
    await row();

    expect(await screen.findByText('settings.webhooks.scope_service_gone')).toBeInTheDocument();
    expect(brokenBadge()).not.toBeNull();
  });
});

describe('WebhooksTab, what is not a deleted target', () => {
  beforeEach(() => {
    webhookRows = [webhook()];
    providerRows = [PROVIDER];
    serviceRows = [SERVICE];
    providersPending = false;
    vi.clearAllMocks();
  });

  it('claims nothing while the providers have not answered yet', async () => {
    // The false alarm this gate exists for: an empty list is the first paint of every load,
    // and an unmatched id in it means "not fetched", never "deleted".
    providersPending = true;
    renderWithProviders(<WebhooksTab />);
    await row();

    expect(screen.queryByText('settings.webhooks.scope_provider_gone')).toBeNull();
    expect(brokenBadge()).toBeNull();
    expect(screen.getByText('settings.webhooks.choose_provider')).toBeInTheDocument();
  });

  it('leaves a scope nobody has chosen yet alone', async () => {
    // A NULL target is a rule half configured, not a rule pointed at something deleted. It
    // keeps the placeholder it always had, and gets no warning.
    webhookRows = [webhook({ scope_ref_id: null })];
    renderWithProviders(<WebhooksTab />);
    await row();

    expect(screen.getByText('settings.webhooks.choose_provider')).toBeInTheDocument();
    expect(brokenBadge()).toBeNull();
  });

  it('says nothing at all about a rule scoped to everything', async () => {
    webhookRows = [webhook({ scope_type: 'all', scope_ref_id: null })];
    providerRows = [];
    serviceRows = [];
    renderWithProviders(<WebhooksTab />);
    // Read inside the row: "All services and integrations" is also an option in the add
    // form above, and a match there would say nothing about what the row claims.
    const card = (await row()).closest('li') as HTMLElement;

    expect(within(card).getByText('settings.webhooks.scope_all')).toBeInTheDocument();
    expect(brokenBadge()).toBeNull();
  });
});
