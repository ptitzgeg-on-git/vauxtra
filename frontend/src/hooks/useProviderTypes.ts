/**
 * `GET /api/providers/types`, read the same way everywhere.
 *
 * react-query keeps one object under `['provider-types']`; before this hook seven call sites
 * declared it under four incompatible generics, so a field guaranteed in one file was
 * `unknown` in another and had to be cast back. The generic and the `queryFn` are written
 * here, once.
 *
 * The table is static for the life of a backend build, hence the long `staleTime`: opening
 * three modals in a row costs one request, not three.
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { ProviderTypesResponse } from '@/types/api';

export const PROVIDER_TYPES_KEY = ['provider-types'] as const;

const STALE_TIME = 5 * 60_000;

export interface UseProviderTypesOptions {
  /** Modals pass `isOpen` so a closed dialog does not fetch. */
  enabled?: boolean;
}

export function useProviderTypes(
  options: UseProviderTypesOptions = {},
): UseQueryResult<ProviderTypesResponse> {
  return useQuery<ProviderTypesResponse>({
    queryKey: PROVIDER_TYPES_KEY,
    queryFn: () => api.get<ProviderTypesResponse>('/providers/types'),
    staleTime: STALE_TIME,
    enabled: options.enabled ?? true,
  });
}
