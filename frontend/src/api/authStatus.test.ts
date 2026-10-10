/**
 * Signing out has to leave nothing of the session behind on the tab: the services and
 * integrations lists were kept in `sessionStorage` for whoever signed in next.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement } from 'react';
import { act, render, screen } from '@testing-library/react';
import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { makeQueryClient } from '@/test/render';
import type { AuthStatus } from '@/types/api';
import { api } from '@/api/client';
import {
  AUTH_STATUS_KEY,
  authStatusQuery,
  clearSessionAfterLogout,
  handleAuthExpired,
  purgeSessionCaches,
} from './authStatus';

describe('purgeSessionCaches', () => {
  afterEach(() => sessionStorage.clear());

  it('removes every vauxtra.cache.* entry and nothing else', () => {
    sessionStorage.setItem('vauxtra.cache.services', '[]');
    sessionStorage.setItem('vauxtra.cache.providers', '[]');
    sessionStorage.setItem('vauxtra.setup.step', '"providers"');

    purgeSessionCaches();

    expect(sessionStorage.getItem('vauxtra.cache.services')).toBeNull();
    expect(sessionStorage.getItem('vauxtra.cache.providers')).toBeNull();
    expect(sessionStorage.getItem('vauxtra.setup.step')).toBe('"providers"');
  });

  it('forgets the provider diagnostics kept in localStorage', () => {
    localStorage.setItem('vauxtra.providers.manual-diagnostics.v1', '{"1":{"ok":false}}');
    localStorage.setItem('vauxtra.lang', '"fr"');

    purgeSessionCaches();

    expect(localStorage.getItem('vauxtra.providers.manual-diagnostics.v1')).toBeNull();
    expect(localStorage.getItem('vauxtra.lang')).toBe('"fr"');
    localStorage.clear();
  });
});

describe('authStatusQuery', () => {
  it('is the one key every reader of /auth/me shares', () => {
    expect(authStatusQuery.queryKey).toEqual(['auth-status']);
    expect(AUTH_STATUS_KEY).toEqual(['auth-status']);
  });
});

const SIGNED_IN: AuthStatus = { authenticated: true, auth_required: true, setup_required: false };
const SIGNED_OUT: AuthStatus = { authenticated: false, auth_required: true, setup_required: false };

describe('clearSessionAfterLogout', () => {
  afterEach(() => sessionStorage.clear());

  it('marks the session signed out before it removes anything', async () => {
    const qc = makeQueryClient();
    qc.setQueryData(AUTH_STATUS_KEY, SIGNED_IN);
    qc.setQueryData(['services'], []);
    qc.setQueryData(['providers'], []);

    // What the auth answer said at the moment each page query left the cache.
    const seenAtRemoval: Array<boolean | undefined> = [];
    const unsubscribe = qc.getQueryCache().subscribe((event) => {
      if (event.type === 'removed') {
        seenAtRemoval.push(qc.getQueryData<AuthStatus>(AUTH_STATUS_KEY)?.authenticated);
      }
    });
    // The refetch after the invalidation answers like the server would without a cookie.
    qc.setQueryDefaults(AUTH_STATUS_KEY, { queryFn: async () => SIGNED_OUT });

    await clearSessionAfterLogout(qc);
    unsubscribe();

    expect(seenAtRemoval).toEqual([false, false]);
    expect(qc.getQueryData(['services'])).toBeUndefined();
    expect(qc.getQueryData(['providers'])).toBeUndefined();
  });

  it('does not let a still-mounted page refetch what was removed', async () => {
    // The burst of 401s: a page re-rendered after its query was removed fetches it again,
    // and without a cookie every one of those requests is a 401.
    const qc = makeQueryClient();
    qc.setQueryData(AUTH_STATUS_KEY, SIGNED_IN);
    const pageFetch = vi.fn(async () => ['row']);
    // Takes a moment, like the network: the gate renders once with `isFetching` in between.
    const authFetch = vi.fn(() => new Promise<AuthStatus>((resolve) => setTimeout(() => resolve(SIGNED_OUT), 30)));

    function Page() {
      const { data } = useQuery({ queryKey: ['services'], queryFn: pageFetch });
      return createElement('p', null, `rows:${data?.length ?? 0}`);
    }
    // The same decision as `AuthGate`, reading `isFetching` too so an auth refetch alone
    // re-renders it, as it does in the app.
    function Gate() {
      const { data, isFetching } = useQuery({ queryKey: AUTH_STATUS_KEY, queryFn: authFetch });
      if (data?.auth_required && !data.authenticated) return createElement('p', null, 'login');
      return createElement('div', { 'data-fetching': String(isFetching) }, createElement(Page));
    }

    render(createElement(QueryClientProvider, { client: qc }, createElement(Gate)));
    await screen.findByText('rows:1');
    expect(pageFetch).toHaveBeenCalledTimes(1);

    await act(async () => {
      await clearSessionAfterLogout(qc);
    });

    expect(await screen.findByText('login')).toBeInTheDocument();
    expect(pageFetch).toHaveBeenCalledTimes(1);
  });
});

describe('handleAuthExpired', () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it('forgets the previous session once the server says it is over', async () => {
    const qc = makeQueryClient();
    qc.setQueryData(AUTH_STATUS_KEY, SIGNED_IN);
    qc.setQueryData(['services'], ['row']);
    sessionStorage.setItem('vauxtra.cache.services', '["row"]');
    vi.spyOn(api, 'get').mockResolvedValue(SIGNED_OUT);

    await handleAuthExpired(qc);

    expect(qc.getQueryData<AuthStatus>(AUTH_STATUS_KEY)?.authenticated).toBe(false);
    expect(qc.getQueryData(['services'])).toBeUndefined();
    expect(sessionStorage.getItem('vauxtra.cache.services')).toBeNull();
  });

  it('keeps everything when the session is still valid or cannot be checked', async () => {
    const qc = makeQueryClient();
    qc.setQueryData(AUTH_STATUS_KEY, SIGNED_IN);
    qc.setQueryData(['services'], ['row']);
    sessionStorage.setItem('vauxtra.cache.services', '["row"]');

    vi.spyOn(api, 'get').mockResolvedValueOnce(SIGNED_IN).mockRejectedValueOnce(new Error('offline'));
    await handleAuthExpired(qc);
    await handleAuthExpired(qc);

    expect(qc.getQueryData(['services'])).toEqual(['row']);
    expect(sessionStorage.getItem('vauxtra.cache.services')).toBe('["row"]');
  });
});
