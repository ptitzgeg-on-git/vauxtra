import type { ReactNode } from 'react';
import { Radio, ServerOff, Unplug, Waypoints } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { EM_DASH } from '@/lib/format';
import { Badge, EmptyState, IconButton, SkeletonCard, cn } from '@/components/ui';
import type { Service, ServiceHistoryResponse } from '@/types/api';
import { hasNoPort } from '@/components/features/services/helpers';
import { UptimeStrip } from './UptimeStrip';
import {
  STATUS_LABEL_KEY,
  STATUS_TONE,
  isTunnelService,
  serviceHost,
  serviceLatency,
  serviceStatus,
  serviceTarget,
  sortForCards,
  statusCaption,
  summarizeUptime,
  type LatencyProbes,
  type MonitoringStatus,
} from './uptime';

/**
 * The services as a grid of tiles, the default view of the monitoring page.
 *
 * Same data as `MonitoringTable`, read at a glance instead of row by row: what is down comes
 * first (`sortForCards`), each tile says in one line what the last probe saw, and the 24 h
 * strip sits under it. A tile opens the drawer the way a row does: the host name is the one
 * real button, and its `::after` stretches over the whole tile, so the tile is clickable
 * without a click handler on a list item, and the check button sits above that layer instead
 * of being nested in another button.
 */

const ACCENT: Record<MonitoringStatus, string> = {
  error: 'border-destructive/50',
  unknown: 'border-warning/40',
  ok: 'border-border',
  disabled: 'border-border',
};

export interface ServiceCardsProps {
  services: Service[];
  history: ServiceHistoryResponse | undefined;
  historyError?: boolean;
  probes: LatencyProbes;
  checkingId: number | null;
  now: number;
  selectedId: number | null;
  loading?: boolean;
  onSelect: (service: Service) => void;
  onCheck: (serviceId: number) => void;
  empty: { title: string; description: string; action?: ReactNode };
}

export function ServiceCards({
  services,
  history,
  historyError = false,
  probes,
  checkingId,
  now,
  selectedId,
  loading = false,
  onSelect,
  onCheck,
  empty,
}: ServiceCardsProps) {
  const t = useT();
  const { formatRelative, formatLatency, formatPercent } = useFormat();

  if (loading) {
    return (
      <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((tile) => (
          <SkeletonCard key={tile} />
        ))}
      </div>
    );
  }

  if (services.length === 0) {
    return <EmptyState icon={<ServerOff />} title={empty.title} description={empty.description} action={empty.action} />;
  }

  return (
    <ul className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
      {sortForCards(services).map((service) => {
        const status = serviceStatus(service);
        const host = serviceHost(service);
        const tunnel = isTunnelService(service);
        const noPort = hasNoPort(service);
        const summary = summarizeUptime(history?.[String(service.id)], now);
        const latency = serviceLatency(service, probes[service.id]);
        const caption = statusCaption(service, t);
        const selected = selectedId === service.id;
        const availability = summary.availability === null ? null : formatPercent(summary.availability * 100, 1);

        return (
          <li
            key={service.id}
            className={cn(
              'relative flex min-w-0 flex-col gap-2.5 rounded-xl border bg-card p-3.5 shadow-xs transition-colors',
              ACCENT[status],
              selected ? 'bg-accent/60 ring-2 ring-ring/40' : 'hover:bg-accent/40',
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <button
                type="button"
                onClick={() => onSelect(service)}
                className="min-w-0 text-left after:absolute after:inset-0 after:rounded-xl after:content-[''] focus-visible:outline-hidden focus-visible:after:ring-2 focus-visible:after:ring-ring"
              >
                <span
                  className={cn(
                    'block truncate text-sm font-semibold',
                    status === 'disabled' ? 'text-muted-foreground' : 'text-foreground',
                  )}
                >
                  {host}
                </span>
                <span className="mt-0.5 flex items-center gap-1.5 truncate font-mono text-[11px] text-muted-foreground">
                  {tunnel ? <Waypoints className="h-3 w-3 shrink-0" aria-hidden /> : null}
                  {noPort ? <Unplug className="h-3 w-3 shrink-0" aria-hidden /> : null}
                  {tunnel ? service.tunnel_provider_name || t('monitoring.tunnel_route') : serviceTarget(service)}
                </span>
              </button>
              <Badge tone={STATUS_TONE[status]} dot size="sm" className="shrink-0">
                {t(STATUS_LABEL_KEY[status])}
              </Badge>
            </div>

            <p
              className={cn(
                'min-h-4 text-xs',
                caption?.failed ? 'text-destructive' : 'text-muted-foreground',
              )}
            >
              {caption
                ? caption.text
                : status === 'disabled'
                  ? t('monitoring.status.disabled_by_user')
                  : noPort
                    ? t('monitoring.no_port_caption')
                    : null}
            </p>

            {noPort && summary.total === 0 ? null : (
              <UptimeStrip
                summary={summary}
                heightClass="h-3"
                emptyLabel={historyError ? t('monitoring.uptime.history_failed') : undefined}
                label={
                  availability
                    ? t('monitoring.uptime.aria', { host, percent: availability })
                    : t('monitoring.uptime.aria_empty', { host })
                }
              />
            )}

            <div className="mt-auto flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
              <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 tabular-nums">
                <span>{availability ?? EM_DASH}</span>
                <span className={cn(latency?.ms != null && 'text-foreground')}>
                  {latency ? formatLatency(latency.ms) : EM_DASH}
                </span>
                <span className="whitespace-nowrap">
                  {service.last_checked ? formatRelative(service.last_checked, now) : t('monitoring.never_checked')}
                </span>
              </span>
              {!noPort && (
                <div className="relative z-10">
                  <IconButton
                    label={t('monitoring.check_row', { host })}
                    icon={<Radio />}
                    variant="ghost"
                    size="icon"
                    className="-my-1 -mr-1"
                    loading={checkingId === service.id}
                    onClick={() => onCheck(service.id)}
                  />
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
