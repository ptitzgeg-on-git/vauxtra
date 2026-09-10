import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { Badge, Tooltip, cn } from '@/components/ui';
import type { HealthResponse } from '@/types/api';

type PillState = 'checking' | 'online' | 'offline';

/**
 * Live backend status for the dashboard header. Shares the `['health']` query with the
 * sidebar; clicking it re-checks. Never colour alone: the label says the state.
 */
export function StatusPill() {
  const t = useT();
  const { formatLatency, formatPercent } = useFormat();

  const { data, isError, isPending, isFetching, refetch } = useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => api.get<HealthResponse>('/health'),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const state: PillState = isPending && !data ? 'checking' : isError || (data && !data.ok) ? 'offline' : 'online';

  const tone = state === 'online' ? 'success' : state === 'offline' ? 'danger' : 'neutral';
  const label = t(`dashboard.status.${state}`);

  const details = data ? (
    <div className="space-y-0.5 text-left">
      <p className="font-medium">{label}</p>
      <p className="tabular-nums">{t('dashboard.status.latency', { latency: formatLatency(data.latency_ms) })}</p>
      <p className="tabular-nums">{t('dashboard.status.disk', { disk: formatPercent(data.disk_usage) })}</p>
      {data.version && (
        <p>
          {t('common.version')} {data.version}
        </p>
      )}
    </div>
  ) : (
    label
  );

  return (
    <Tooltip content={details} placement="bottom">
      <button
        type="button"
        onClick={() => refetch()}
        aria-label={t('dashboard.status.refresh')}
        className={cn(
          'inline-flex rounded-full transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isFetching && 'opacity-70',
        )}
      >
        <Badge tone={tone} dot size="sm" className={cn(state === 'checking' && 'animate-pulse')}>
          <span aria-live="polite">{label}</span>
        </Badge>
      </button>
    </Tooltip>
  );
}
