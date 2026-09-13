/**
 * What the change-password screen says about the rest of the instance.
 *
 * Measured against a running instance before this was written: after a password change the
 * browser that made it keeps its session, every other one is refused, the old password opens
 * nothing -- and an admin API key minted beforehand still answers 200 on every route. The
 * screen said "Update the admin password used to access Vauxtra." and nothing else, so the
 * only thing an operator could learn about any of it was by discovering it.
 *
 * The sentence about keys is counted and conditional, which is the part worth a test: an
 * instance with no keys must not carry a permanent warning about an empty list, and one with
 * keys must name how many. `t()` returns the key here -- `renderWithProviders` leaves out
 * `I18nProvider` on purpose -- so a block is on screen iff its key is, and rewording any of
 * these sentences in eight locale files never turns this red.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { ApiKey } from '@/types/api';

const ME = {
  authenticated: true,
  auth_required: true,
  setup_required: false,
  auth_mode: 'password',
  password_source: 'database',
};

let keys: ApiKey[] = [];

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => Promise.resolve(path === '/auth/me' ? ME : keys)),
    post: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { SecurityTab } = await import('./SecurityTab');

function key(id: number): ApiKey {
  return {
    id,
    name: `key-${id}`,
    prefix: `vx_${id}`,
    scopes: ['admin'],
    created_at: '2026-01-01T00:00:00Z',
    last_used_at: null,
  };
}

/** The form itself, waited for, so an assertion never races the two queries. */
const form = () => screen.findByText('settings.auth.change_password_desc');
const sessions = () => screen.queryByText('settings.security.change_scope_sessions');
const keysLine = () => screen.queryByText('settings.security.change_scope_keys');
const links = () => screen.queryAllByText('settings.security.api_keys_cta');

describe('SecurityTab, changing the password', () => {
  beforeEach(() => {
    keys = [];
    vi.clearAllMocks();
  });

  it('says the other browsers are signed out, whether or not any key exists', async () => {
    renderWithProviders(<SecurityTab />);
    await form();
    await waitFor(() => expect(sessions()).not.toBeNull());
  });

  it('says nothing about API keys when the instance has none', async () => {
    renderWithProviders(<SecurityTab />);
    await form();
    await waitFor(() => expect(sessions()).not.toBeNull());
    expect(keysLine()).toBeNull();
  });

  it('warns that the keys survive it when the instance has some', async () => {
    keys = [key(1), key(2)];
    renderWithProviders(<SecurityTab />);
    await form();
    await waitFor(() => expect(keysLine()).not.toBeNull());
  });

  it('leaves one link to the keys when there is nothing to revoke', async () => {
    renderWithProviders(<SecurityTab />);
    await form();
    await waitFor(() => expect(sessions()).not.toBeNull());
    // The standing one at the bottom of the tab, and no second one in the form.
    expect(links()).toHaveLength(1);
  });

  it('puts a second one inside the warning, where the operator is reading', async () => {
    keys = [key(1)];
    renderWithProviders(<SecurityTab />);
    await form();
    await waitFor(() => expect(links()).toHaveLength(2));
  });
});
