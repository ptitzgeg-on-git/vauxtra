import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { TunnelAccessResponse } from '@/types/api';

export const TUNNEL_ACCESS_KEY = ['providers-tunnel-access'] as const;

/**
 * `GET /api/providers/tunnels/access`: per tunnel integration, what its token may read of
 * Cloudflare Access and the protection of every route.
 *
 * Each read costs several Cloudflare calls per integration (applications, policies, tunnel
 * configuration), and an Access setup changes when someone edits it, not every few seconds.
 * So it is never polled: read once per visit, kept five minutes, and refreshed by hand or
 * after Vauxtra itself creates an application.
 */
export function useTunnelAccess() {
  return useQuery<TunnelAccessResponse>({
    queryKey: TUNNEL_ACCESS_KEY,
    queryFn: () => api.get<TunnelAccessResponse>('/providers/tunnels/access'),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}

/** Routes open to everyone, across every integration whose token could read Access. */
export function openRouteCount(data: TunnelAccessResponse | undefined): number {
  const items = Array.isArray(data?.items) ? data.items : [];
  return items.reduce((sum, item) => sum + (item.permission === 'ok' ? item.counts?.open ?? 0 : 0), 0);
}
