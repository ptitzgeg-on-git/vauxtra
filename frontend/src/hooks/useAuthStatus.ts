/**
 * `GET /api/auth/me`, read the same way everywhere.
 *
 * Six components ask the server who the caller is, and they used to ask under two different
 * react-query keys: `['auth-status']` in the shell -- the boot gate, the layout banner, the
 * sidebar, the dashboard -- and `['auth-me']` in the two settings tabs. One route, one byte
 * stream, two cache entries, and therefore two lifetimes.
 *
 * That cost both halves something. Of the eight places that invalidate after the answer
 * could have changed -- a 401 from the interceptor, a login, setup completion, a sign-out, a
 * password set in the wizard -- five named `['auth-status']` only, so the settings copy was
 * refreshed by accident or not at all. Meanwhile `['auth-me']` declared no `staleTime`, which
 * means zero: opening Settings drew a skeleton and spent a round trip re-fetching an answer
 * the shell already held, fresh, a few hundred milliseconds old -- and again on every
 * remount, which is what switching between the two tabs is.
 *
 * Key, `queryFn`, `staleTime` and `retry` are written here once, and `AUTH_STATUS_KEY` is the
 * only spelling of the key in the codebase, so the two entries cannot drift apart again.
 *
 * `staleTime` is one minute, the shortest of the values the old call sites carried. It is a
 * ceiling on how long a verdict can sit unquestioned when nothing invalidates; every event
 * that actually changes the answer invalidates, so the ceiling is a backstop, not the path.
 *
 * `retry: false` because the boot gate distinguishes "no answer" from "no password set", and
 * it can only do that if one failed request ends the query. Observers of a shared entry do
 * not each get their own retry policy, so this has to be the entry's policy.
 */

import { queryOptions, useQuery, type UseQueryResult } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { AuthStatus } from '@/types/api';

export const AUTH_STATUS_KEY = ['auth-status'] as const;

const STALE_TIME = 60_000;

/**
 * The whole description of the query, so that the wizard's `fetchQuery` -- which seeds the
 * entry before the first dashboard paint -- cannot seed it under different terms than the
 * hook reads it under.
 */
export const authStatusQuery = queryOptions({
  queryKey: AUTH_STATUS_KEY,
  queryFn: () => api.get<AuthStatus>('/auth/me'),
  staleTime: STALE_TIME,
  retry: false,
});

export function useAuthStatus(): UseQueryResult<AuthStatus> {
  return useQuery(authStatusQuery);
}
