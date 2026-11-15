/**
 * Access policies picked on a tunnel route, and what the save does with them.
 *
 * Cloudflare refuses an Access application for a hostname the tunnel does not route yet, so
 * the save publishes the route first and asks for the application after. These pin that
 * order and the body sent, and that a refused application leaves a published route reported
 * as published: the receipt says what is missing, it does not turn into a failed save that
 * invites the operator to publish a second time.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it, vi, beforeAll, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { HttpError } from '@/api/httpError';
import type { PreflightResult, Provider, Service, TunnelAccessResponse } from '@/types/api';

/** `cloudflare_tunnel` routes and tunnels in the capability floor, with `/providers/types` empty. */
const TUNNEL: Provider = {
  id: 9,
  name: 'cf-tunnel',
  type: 'cloudflare_tunnel',
  url: '',
  username: '',
  enabled: true,
  extra: {},
  created_at: '2026-01-01 09:00:00',
};

const HOST = 'grafana.example.test';

const SERVICE: Service = {
  id: 42,
  subdomain: 'grafana',
  domain: 'example.test',
  target_ip: '10.0.0.20',
  target_port: 3000,
  forward_scheme: 'http',
  websocket: true,
  expose_mode: 'tunnel',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: HOST,
  dns_ip: '',
  npm_host_id: null,
  dns_provider_id: null,
  proxy_provider_id: null,
  tunnel_provider_id: 9,
  enabled: true,
  status: 'ok',
  last_checked: null,
  created_at: '2026-01-01 09:00:00',
  tags: [],
  environments: [],
};

const ACCESS: TunnelAccessResponse = {
  total: 1,
  items: [
    {
      id: 9,
      name: 'cf-tunnel',
      permission: 'ok',
      reason: '',
      read_permission: 'Access: Apps and Policies Read',
      edit_permission: 'Access: Apps and Policies Edit',
      application_count: 0,
      policies: [{ id: 'pol-staff', name: 'Staff', decision: 'allow', everyone: false, app_count: 2 }],
      counts: { protected: 0, partial: 0, open: 1, unknown: 0 },
      hosts: [
        {
          hostname: HOST,
          state: 'open',
          reason: 'no_application',
          managed: true,
          observed: '',
          apps: [],
          policies: [],
        },
      ],
    },
  ],
};

const PREFLIGHT: PreflightResult = {
  ok: true,
  public_host: HOST,
  checks: [],
  summary: { blocking_failures: 0, warnings: 0, total: 0 },
};

const APPS_PATH = '/providers/9/access/apps';

/** How Cloudflare answers the application: created, or refused for want of Edit. */
let appAnswer: 'created' | 'forbidden' = 'created';

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/providers') return Promise.resolve([TUNNEL]);
      if (path === '/providers/types') return Promise.resolve({});
      if (path === '/providers/tunnels/access') return Promise.resolve(ACCESS);
      if (path === '/domains') return Promise.resolve(['example.test']);
      return Promise.resolve([]);
    }),
    post: vi.fn((path: string) => {
      if (path === '/services/preflight') return Promise.resolve(PREFLIGHT);
      if (path === APPS_PATH) {
        if (appAnswer === 'forbidden') {
          return Promise.reject(
            new HttpError('forbidden', APPS_PATH, {
              status: 403,
              data: { detail: 'the token may not edit Access' },
              headers: {},
            }),
          );
        }
        return Promise.resolve({ ok: true, hostname: HOST, application_id: 'app-new', policy_ids: ['pol-staff'] });
      }
      return Promise.resolve({ ok: true });
    }),
    put: vi.fn(() => Promise.resolve({ ok: true, errors: [] })),
    patch: vi.fn(() => Promise.resolve(SERVICE)),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { ExposeModal } = await import('./ExposeModal');
const { api } = await import('@/api/client');

const open = () => renderWithProviders(<ExposeModal isOpen onClose={vi.fn()} mode="edit" service={SERVICE} />);

const appCalls = () => vi.mocked(api.post).mock.calls.filter(([path]) => path === APPS_PATH);

/** From the filled form to the receipt: Continue runs the checks, then the route is saved. */
const saveRoute = async () => {
  await userEvent.click(screen.getByRole('button', { name: 'expose.continue' }));
  await userEvent.click(await screen.findByRole('button', { name: 'expose.save_route' }));
};

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  appAnswer = 'created';
  vi.mocked(api.post).mockClear();
  vi.mocked(api.put).mockClear();
});

describe('ExposeModal, Access policies on a tunnel route', () => {
  it('publishes the route, then puts it behind the policies ticked', async () => {
    open();
    await userEvent.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    await userEvent.click(screen.getByRole('button', { name: 'expose.continue' }));

    // The review says what the save will do beyond the route.
    expect(await screen.findByText('expose.access.review')).not.toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'expose.save_route' }));

    expect(await screen.findByText('expose.access.done')).not.toBeNull();
    expect(appCalls()).toEqual([[APPS_PATH, { hostname: HOST, policy_ids: ['pol-staff'] }]]);
    // In that order: the API refuses a hostname the tunnel does not serve yet.
    const putOrder = vi.mocked(api.put).mock.invocationCallOrder[0];
    const appOrder = vi.mocked(api.post).mock.invocationCallOrder[
      vi.mocked(api.post).mock.calls.findIndex(([path]) => path === APPS_PATH)
    ];
    expect(putOrder).toBeLessThan(appOrder);
  });

  it('asks for no application when nothing is ticked', async () => {
    open();
    await screen.findByRole('checkbox', { name: /Staff/ });
    await saveRoute();

    expect(await screen.findByText('expose.done.updated_title')).not.toBeNull();
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(appCalls()).toEqual([]);
    expect(screen.queryByText('expose.access.done')).toBeNull();
    expect(screen.queryByText('expose.access.done_failed')).toBeNull();
  });

  it('reports a refused application next to a route that is published', async () => {
    appAnswer = 'forbidden';
    open();
    await userEvent.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    await saveRoute();

    // The receipt of a saved route, with what is missing and where to finish it.
    expect(await screen.findByText('expose.done.updated_title')).not.toBeNull();
    expect(screen.getByText('expose.access.done_failed')).not.toBeNull();
    expect(screen.getByText('monitoring.access.edit_needed')).not.toBeNull();
    expect(screen.getByRole('link', { name: 'expose.access.open_tab' }).getAttribute('href')).toBe(
      '/monitoring?tab=access',
    );
    expect(screen.queryByText('expose.access.done')).toBeNull();
    // Not a failed save: the route went through once and is not offered again.
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'expose.save_route' })).toBeNull();
  });

  it('sends no policy once it is unticked again', async () => {
    open();
    await userEvent.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    // Ticked, then dropped: the save sends what is on screen, not what was once ticked.
    await userEvent.click(screen.getByRole('checkbox', { name: /Staff/ }));
    await saveRoute();

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(appCalls()).toEqual([]);
  });
});
