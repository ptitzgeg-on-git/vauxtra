/**
 * What the palette said about two lists it had never read.
 *
 * `['services']` and `['providers']` are the only place the box learns what the estate holds,
 * and both were destructured down to their data. A read that failed and an instance with
 * nothing registered arrived as the same `undefined`: the two groups simply went missing, and
 * typing the name of an endpoint that exists answered "No matches." -- a claim about the
 * estate, made from a list nobody had read. The palette opens over any page, so the reads are
 * often still in flight on the first keystroke, and that case read the same.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 * `ThemeProvider` is not optional here: `useTheme` throws outside it.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { ThemeProvider } from '@/theme';
import type { Provider, Service } from '@/types/api';

const APP: Service = {
  id: 1,
  subdomain: 'grafana',
  domain: 'example.test',
  target_ip: '10.0.0.20',
  target_port: 3000,
  forward_scheme: 'http',
  websocket: true,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: '',
  dns_ip: '198.51.100.4',
  npm_host_id: 12,
  dns_provider_id: 1,
  proxy_provider_id: 2,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: '2026-01-01 10:00:00',
  created_at: '2026-01-01 09:00:00',
  tags: [],
  environments: [],
};

const NPM: Provider = {
  id: 2,
  name: 'nginx-front',
  type: 'npm',
  url: 'http://10.0.0.30:81',
  username: 'ops',
  enabled: true,
  extra: {},
  created_at: '2026-01-01 09:00:00',
};

type Answer = 'rows' | 'fails' | 'never';

// A `vi.fn(impl)` factory keeps its implementation across `restoreMocks`, so the answers live
// in variables the whole file can reset rather than in per-test `mockImplementation` calls.
let servicesAnswer: Answer = 'rows';
let providersAnswer: Answer = 'rows';
let serviceRows: Service[] = [APP];
const NEVER: Promise<never> = new Promise(() => {});

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/services') {
        if (servicesAnswer === 'fails') return Promise.reject(new Error('services refused'));
        if (servicesAnswer === 'never') return NEVER;
        return Promise.resolve(serviceRows);
      }
      if (path === '/providers') {
        if (providersAnswer === 'fails') return Promise.reject(new Error('providers refused'));
        if (providersAnswer === 'never') return NEVER;
        return Promise.resolve([NPM]);
      }
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { api } = await import('@/api/client');
const { CommandPalette } = await import('./CommandPalette');

const show = () =>
  renderWithProviders(
    <ThemeProvider>
      <CommandPalette open onClose={vi.fn()} />
    </ThemeProvider>,
  );

const field = () => screen.getByRole('combobox');
const banner = () => screen.queryByText('palette.lists_failed');
const reads = (path: string) =>
  (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(([p]) => p === path).length;

describe('CommandPalette, the two lists it searches', () => {
  beforeAll(() => {
    // jsdom implements no layout, so it ships no `scrollIntoView`; the palette calls it on
    // every change of the active row.
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    servicesAnswer = 'rows';
    providersAnswer = 'rows';
    serviceRows = [APP];
    vi.clearAllMocks();
  });

  it('finds an endpoint by name once the list has come back', async () => {
    const user = userEvent.setup();
    show();
    await user.type(field(), 'grafana');
    expect(await screen.findByText('grafana.example.test')).toBeInTheDocument();
    expect(banner()).toBeNull();
  });

  it('does not answer "No matches." while the lists are still being read', async () => {
    servicesAnswer = 'never';
    providersAnswer = 'never';
    const user = userEvent.setup();
    show();
    await user.type(field(), 'grafana');
    expect(await screen.findByText('palette.loading')).toBeInTheDocument();
    expect(screen.queryByText('palette.empty')).toBeNull();
  });

  it('says the lists could not be read rather than answering "No matches."', async () => {
    servicesAnswer = 'fails';
    providersAnswer = 'fails';
    const user = userEvent.setup();
    show();
    await user.type(field(), 'grafana');
    // The banner is what only a failed read draws; the empty text is on screen either way.
    expect(await screen.findByText('palette.lists_failed')).toBeInTheDocument();
    expect(screen.getByText('palette.lists_failed_hint')).toBeInTheDocument();
    expect(screen.getByText('palette.empty_unread')).toBeInTheDocument();
    expect(screen.queryByText('palette.empty')).toBeNull();
  });

  it('keeps saying so while the pages it can still search do match', async () => {
    servicesAnswer = 'fails';
    const user = userEvent.setup();
    show();
    await user.type(field(), 'dashboard');
    expect(await screen.findByText('palette.lists_failed')).toBeInTheDocument();
    expect(screen.getByText('nav.dashboard')).toBeInTheDocument();
  });

  it('lets the operator ask again for the list that failed, and only that one', async () => {
    servicesAnswer = 'fails';
    const user = userEvent.setup();
    show();
    expect(await screen.findByText('palette.lists_failed')).toBeInTheDocument();
    const before = { services: reads('/services'), providers: reads('/providers') };
    servicesAnswer = 'rows';
    await user.click(screen.getByRole('button', { name: 'common.retry' }));
    await waitFor(() => expect(reads('/services')).toBe(before.services + 1));
    expect(reads('/providers')).toBe(before.providers);
    await waitFor(() => expect(banner()).toBeNull());
  });

  it('still answers "No matches." once both lists came back empty', async () => {
    serviceRows = [];
    const user = userEvent.setup();
    show();
    await user.type(field(), 'grafana');
    expect(await screen.findByText('palette.empty')).toBeInTheDocument();
    expect(banner()).toBeNull();
  });
});
