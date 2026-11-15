/**
 * What the delete dialog of the routes page says about an Access application.
 *
 * A route protected from the panel carries the id of the application Vauxtra created, and
 * deleting the service deletes that application too. The dialog has to say so before the
 * click: the login in front of the route goes with it. A route without an id says nothing
 * about Access, since Vauxtra will not touch what it did not create.
 *
 * `t()` returns the key here (`renderWithProviders` leaves out `I18nProvider`), so the keys
 * are the assertion.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Service } from '@/types/api';

const service = (id: number, subdomain: string, accessAppId: string): Service => ({
  id,
  subdomain,
  domain: 'example.test',
  target_ip: 'localhost',
  target_port: 8080,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'tunnel',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: '',
  dns_ip: '',
  npm_host_id: null,
  dns_provider_id: null,
  proxy_provider_id: null,
  tunnel_provider_id: 4,
  enabled: true,
  status: 'unknown',
  access_app_id: accessAppId,
  last_checked: null,
  created_at: '2026-01-01T00:00:00Z',
  tags: [],
  environments: [],
});

const SERVICES: Service[] = [service(1, 'board', 'app-9'), service(2, 'listo', '')];

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => Promise.resolve(path === '/services' ? SERVICES : [])),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true, errors: [] })),
  },
}));

const { Services } = await import('./Services');

const rowOf = async (host: string) => {
  const cell = await screen.findByText(host);
  const row = cell.closest('tr');
  if (!row) throw new Error(`no row for ${host}`);
  return row;
};

describe('Services, deleting a route Vauxtra protected', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('says the Access application goes with the service', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Services />, { route: '/services' });
    const row = await rowOf('board.example.test');
    await user.click(within(row).getByRole('button', { name: 'services.action.delete' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('services.confirm.delete_body');
    expect(dialog.textContent).toContain('services.confirm.delete_body_access');
  });

  it('says nothing about Access for a route Vauxtra did not protect', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Services />, { route: '/services' });
    const row = await rowOf('listo.example.test');
    await user.click(within(row).getByRole('button', { name: 'services.action.delete' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('services.confirm.delete_body');
    expect(dialog.textContent).not.toContain('services.confirm.delete_body_access');
  });

  it('counts the protected routes in a bulk delete', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Services />, { route: '/services' });
    await rowOf('board.example.test');
    await user.click(screen.getByRole('checkbox', { name: 'services.select_all' }));
    await user.click(screen.getByRole('button', { name: 'common.delete' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('services.confirm.bulk_delete_body');
    expect(dialog.textContent).toContain('services.confirm.bulk_delete_body_access');
  });
});
