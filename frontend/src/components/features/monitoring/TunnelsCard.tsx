import { Link } from 'react-router-dom';
import { CloudOff, RefreshCw, Waypoints } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  IconButton,
  InlineAlert,
  ProviderLogo,
  SkeletonRow,
  Tooltip,
  buttonVariants,
  cn,
  type Tone,
} from '@/components/ui';
import type { ProviderHealthStatus, TunnelHealthResponse } from '@/types/api';

/**
 * Cloudflare Tunnel connectors — `GET /api/providers/tunnels/health`.
 *
 * A tunnel is the one thing on this page the TCP prober cannot see: services exposed
 * through it are skipped by the scheduler, so the connector's own health *is* their
 * status. The route reports `{ok, status, connections, clients, reason}` per provider,
 * where `status` is free text from Cloudflare (`healthy`, `degraded`, `inactive`, `down`).
 */

const HEALTH_TONE: Record<string, Tone> = {
  healthy: 'success',
  degraded: 'warning',
  inactive: 'neutral',
  down: 'danger',
  unknown: 'neutral',
};

function healthTone(health: ProviderHealthStatus | undefined): Tone {
  if (!health) return 'neutral';
  const status = (health.status || '').toLowerCase();
  return HEALTH_TONE[status] ?? (health.ok ? 'success' : 'danger');
}

/** `providers.status.*` covers active/degraded/disabled/error; anything else keeps its own key. */
function healthLabelKey(health: ProviderHealthStatus | undefined): string {
  const status = (health?.status || '').toLowerCase();
  if (status === 'healthy') return 'providers.status.active';
  if (status === 'degraded') return 'providers.status.degraded';
  if (status === 'inactive') return 'monitoring.tunnels.inactive';
  if (status === 'down') return 'providers.status.error';
  return health?.ok ? 'providers.status.active' : 'monitoring.tunnels.unknown';
}

export interface TunnelsCardProps {
  data: TunnelHealthResponse | undefined;
  loading: boolean;
  isError: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}

export function TunnelsCard({ data, loading, isError, refreshing, onRefresh }: TunnelsCardProps) {
  const t = useT();
  const { formatNumber } = useFormat();

  const items = Array.isArray(data?.items) ? data.items : [];
  const down = items.filter((item) => !item.health?.ok);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <Waypoints className="h-4 w-4 text-muted-foreground" aria-hidden />
            {t('monitoring.tunnels.title')}
          </CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{t('monitoring.tunnels.description')}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {items.length > 0 && (
            <Badge tone={down.length > 0 ? 'danger' : 'success'} size="sm" dot>
              {t('monitoring.tunnels.healthy_of', { healthy: items.length - down.length, total: items.length })}
            </Badge>
          )}
          <IconButton
            label={t('monitoring.tunnels.refresh')}
            icon={<RefreshCw className={cn(refreshing && 'animate-spin')} />}
            variant="ghost"
            size="icon"
            onClick={onRefresh}
          />
        </div>
      </CardHeader>

      <CardContent>
        {loading ? (
          <div className="space-y-2">
            <SkeletonRow columns={3} />
            <SkeletonRow columns={3} />
          </div>
        ) : isError ? (
          <InlineAlert tone="danger" title={t('monitoring.tunnels.load_failed')}>
            {t('monitoring.tunnels.load_failed_hint')}
          </InlineAlert>
        ) : items.length === 0 ? (
          <EmptyState
            compact
            icon={<CloudOff />}
            title={t('monitoring.tunnels.empty')}
            description={t('monitoring.tunnels.empty_hint')}
            action={
              <Link to="/providers" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
                {t('monitoring.tunnels.open_providers')}
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {items.map((item) => {
              const health = item.health;
              const tone = healthTone(health);
              const connections = health?.active_connections ?? health?.connections;
              const clients = health?.active_clients ?? health?.clients;
              return (
                <li key={item.id} className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                  <div className="flex min-w-0 items-start gap-2.5">
                    <ProviderLogo type="cloudflare_tunnel" className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{item.name}</p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                        {connections !== undefined || clients !== undefined
                          ? t('monitoring.tunnels.connections', {
                              connections: formatNumber(connections ?? 0),
                              clients: formatNumber(clients ?? 0),
                            })
                          : item.tunnel_id
                            ? t('monitoring.tunnels.tunnel_id', { id: item.tunnel_id })
                            : t('monitoring.tunnels.no_metrics')}
                      </p>
                      {health?.error && <p className="mt-0.5 text-[11px] text-destructive">{health.error}</p>}
                      {!health?.error && health?.reason && (
                        <p className="mt-0.5 text-[11px] text-muted-foreground">{health.reason}</p>
                      )}
                    </div>
                  </div>
                  <Tooltip content={health?.status || t('monitoring.tunnels.unknown')}>
                    <Badge tone={tone} size="sm" dot>
                      {t(healthLabelKey(health))}
                    </Badge>
                  </Tooltip>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
