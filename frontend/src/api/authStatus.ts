import { queryOptions, type QueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { DIAGNOSTICS_STORAGE_KEY } from '@/components/features/providers/providerHealth';
import type { AuthStatus } from '@/types/api';

/**
 * The one definition of the `/auth/me` query. It used to be declared in seven places with
 * three different `staleTime`s and two keys: `auth-me` held its own copy of the same answer,
 * which neither a 401 nor a logout invalidated, so the settings tabs kept showing the
 * previous session's scope.
 */
export const AUTH_STATUS_KEY = ['auth-status'] as const;

export const authStatusQuery = queryOptions({
  queryKey: AUTH_STATUS_KEY,
  queryFn: () => api.get<AuthStatus>('/auth/me'),
  staleTime: 60_000,
  retry: false,
});

/** Session-scoped caches written by the dashboard and monitoring pages. */
export const SESSION_CACHE_PREFIX = 'vauxtra.cache.';

/** Drop every per-session cache so the next person on this tab starts from nothing. */
export function purgeSessionCaches(): void {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(SESSION_CACHE_PREFIX)) sessionStorage.removeItem(key);
    }
  } catch {
    // Storage can be unavailable (private mode); there is then nothing to purge.
  }
  try {
    // The last provider tests, with their error texts: kept in localStorage, so they
    // would otherwise outlive the session and show to whoever signs in next.
    localStorage.removeItem(DIAGNOSTICS_STORAGE_KEY);
  } catch {
    // Same as above.
  }
}

/**
 * Forget the session in the query cache once the server has ended it.
 *
 * The order matters. The other queries used to be removed while the pages reading them were
 * still mounted: the next render of those pages refetched every one of them without a cookie,
 * and each 401 fired `vauxtra:auth-expired`, which refetched `/auth/me` again. Marking the
 * session as signed out first lets the auth gate swap the pages for the login screen in the
 * same render batch, so nothing is left mounted to refetch what is removed.
 */
export async function clearSessionAfterLogout(qc: QueryClient): Promise<void> {
  purgeSessionCaches();
  // In-flight page requests would otherwise land after the removal and repopulate the cache.
  await qc.cancelQueries({ predicate: (query) => query.queryKey[0] !== AUTH_STATUS_KEY[0] });
  qc.setQueryData<AuthStatus>(AUTH_STATUS_KEY, (prev) => (prev ? { ...prev, authenticated: false } : prev));
  qc.removeQueries({ predicate: (query) => query.queryKey[0] !== AUTH_STATUS_KEY[0] });
  await qc.invalidateQueries({ queryKey: AUTH_STATUS_KEY });
}

/**
 * A request answered 401: ask the server whether the session is over, and if it is, forget
 * it the way a logout does.
 *
 * Only the auth answer used to be refreshed. The pages and the `sessionStorage` caches kept
 * the previous session's data, and whoever signed in next on the tab saw it until each
 * query came back. A 401 alone is not enough to wipe everything (it can be one stale request
 * racing a fresh login), hence the re-check first; a failed re-check keeps everything.
 */
export async function handleAuthExpired(qc: QueryClient): Promise<void> {
  let status: AuthStatus;
  try {
    status = await qc.fetchQuery({ ...authStatusQuery, staleTime: 0 });
  } catch {
    return;
  }
  if (!status.auth_required || status.authenticated) return;
  purgeSessionCaches();
  const notAuth = (query: { queryKey: readonly unknown[] }) => query.queryKey[0] !== AUTH_STATUS_KEY[0];
  await qc.cancelQueries({ predicate: notAuth });
  qc.removeQueries({ predicate: notAuth });
}
