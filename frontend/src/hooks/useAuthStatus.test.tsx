/**
 * `/auth/me` is one answer, and it belongs in one cache entry.
 *
 * Measured on 60f4522: six components asked that route under two different react-query keys
 * -- the boot gate, the layout banner, the sidebar and the dashboard under
 * `['auth-status']`, the two settings tabs under `['auth-me']`. Of the eight places that
 * invalidate once the answer could have changed, five named `['auth-status']` alone, so
 * signing out, signing in, finishing setup and tripping the 401 handler each refreshed the
 * shell and left the settings copy where it was. The settings copy also declared no
 * `staleTime`, which means zero, so opening Settings drew a skeleton and spent a round trip
 * re-fetching an answer the shell was already holding.
 *
 * These tests are about the key and the freshness window, not about the markup. The client
 * built below deliberately leaves out the `staleTime: Infinity` that `makeQueryClient` sets,
 * because that default would answer for the hook and hide the thing being pinned. `t()`
 * returns the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose -- so
 * each card is identified by the sentence it asks for, not by the sentence it prints.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, screen } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { renderWithProviders } from '@/test/render';
import type { AuthStatus } from '@/types/api';

const OPEN: AuthStatus = {
  authenticated: true,
  auth_required: false,
  setup_required: false,
  auth_mode: 'open',
};

const CLOSED: AuthStatus = {
  authenticated: true,
  auth_required: true,
  setup_required: false,
  auth_mode: 'password',
  password_source: 'database',
};

let me: AuthStatus = CLOSED;
let meFetches = 0;

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path !== '/auth/me') return Promise.resolve(path === '/settings' ? {} : []);
      meFetches += 1;
      return Promise.resolve(me);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({})),
  },
}));

const { AUTH_STATUS_KEY, authStatusQuery } = await import('./useAuthStatus');
const { SecurityTab } = await import('@/components/features/settings/SecurityTab');
const { ApiKeysTab } = await import('@/components/features/settings/ApiKeysTab');

/** The app's own query defaults, minus the one that would answer in the hook's place. */
function client(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

describe('useAuthStatus, one answer in one cache entry', () => {
  beforeEach(() => {
    me = CLOSED;
    meFetches = 0;
  });

  it('paints the security tab from the entry the shell already filled', async () => {
    const queryClient = client();
    queryClient.setQueryData(AUTH_STATUS_KEY, CLOSED);

    renderWithProviders(<SecurityTab />, { queryClient });

    expect(await screen.findByText('settings.auth.change_password_desc')).toBeTruthy();
    expect(meFetches).toBe(0);
  });

  it('paints the keys tab warning from that same entry', async () => {
    const queryClient = client();
    me = OPEN;
    queryClient.setQueryData(AUTH_STATUS_KEY, OPEN);

    renderWithProviders(<ApiKeysTab />, { queryClient });

    expect(await screen.findByText('settings.api_keys.open_access_hint')).toBeTruthy();
    expect(meFetches).toBe(0);
  });

  it('is seeded, not duplicated, by the wizard fetch that runs before the first paint', async () => {
    const queryClient = client();
    await queryClient.fetchQuery(authStatusQuery);
    expect(meFetches).toBe(1);

    renderWithProviders(<SecurityTab />, { queryClient });

    expect(await screen.findByText('settings.auth.change_password_desc')).toBeTruthy();
    expect(meFetches).toBe(1);
  });

  it('follows the shell key alone once the answer has changed', async () => {
    const queryClient = client();
    me = OPEN;
    queryClient.setQueryData(AUTH_STATUS_KEY, OPEN);
    renderWithProviders(<SecurityTab />, { queryClient });
    await screen.findByText('settings.auth.set_password_desc');

    // The one spelling sign-out, login, setup-complete and the 401 handler all use.
    me = CLOSED;
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: AUTH_STATUS_KEY });
    });

    expect(await screen.findByText('settings.auth.change_password_desc')).toBeTruthy();
    expect(meFetches).toBe(1);
  });
});
