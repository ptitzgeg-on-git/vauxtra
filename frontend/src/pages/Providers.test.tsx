/**
 * What the integrations page says when it cannot reach its own health checks.
 *
 * `GET /providers/health` and `GET /providers/tunnels/health` are the two readings behind
 * every verdict on this page. When they fail, `getHealthScore` returns a score of -1 and
 * `getProviderSeverity` calls each integration `unknown` -- which is correct, and which the
 * two filters below count as neither an issue nor a healthy one. So a full list published
 * `Issues - 0` and `Healthy - 0`, the warning badge in the header went away, and nothing on
 * the screen said a request had failed. The dashboard links here with the sentence
 * "Integration health could not be checked"; the page it opened contradicted it.
 *
 * `t()` returns the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose --
 * so a block is on screen iff its key is, and rewording the sentence in eight locale files
 * never turns this red.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { DIAGNOSTICS_STORAGE_KEY } from '@/components/features/providers/providerHealth';
import type { Provider } from '@/types/api';

const PROVIDER: Provider = {
  id: 1,
  name: 'NPM at the lab',
  type: 'npm',
  url: 'http://10.0.0.1:81',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

let autoFails = false;
let tunnelFails = false;

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/providers') return Promise.resolve([PROVIDER]);
      if (path === '/providers/types') return Promise.resolve({});
      if (path === '/providers/health') {
        return autoFails ? Promise.reject(new Error('down')) : Promise.resolve({});
      }
      if (path === '/providers/tunnels/health') {
        return tunnelFails ? Promise.reject(new Error('down')) : Promise.resolve({ items: [] });
      }
      return Promise.resolve({});
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Providers } = await import('./Providers');

/** The filter bar, waited for, so an assertion never races the providers query. */
const filters = () => screen.findByText('providers.filter.all');
const banner = () => screen.queryByText('providers.health.load_failed');

describe('Providers, when the health checks cannot be reached', () => {
  beforeEach(() => {
    autoFails = false;
    tunnelFails = false;
    window.localStorage.removeItem(DIAGNOSTICS_STORAGE_KEY);
    vi.clearAllMocks();
  });

  it('stays quiet while both checks answer', async () => {
    renderWithProviders(<Providers />);
    await filters();
    expect(banner()).toBeNull();
  });

  it('says so when the periodic health request fails', async () => {
    autoFails = true;
    renderWithProviders(<Providers />);
    await filters();
    await waitFor(() => expect(banner()).not.toBeNull());
  });

  it('says so when only the tunnel request fails', async () => {
    tunnelFails = true;
    renderWithProviders(<Providers />);
    await filters();
    await waitFor(() => expect(banner()).not.toBeNull());
  });
});
