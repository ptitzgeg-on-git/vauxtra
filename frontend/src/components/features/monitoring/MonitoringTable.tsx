import type { ReactNode } from 'react';
import { Activity, Radio, ServerOff, Waypoints } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { EM_DASH } from '@/lib/format';
import { Badge, EmptyState, IconButton, Skeleton, Tooltip, cn } from '@/components/ui';
import type { Service, ServiceHistoryResponse } from '@/types/api';
import { UptimeStrip } from './UptimeStrip';
import {
  STATUS_LABEL_KEY,
  STATUS_TONE,
  isTunnelService,
  serviceHost,
  serviceStatus,
  serviceTarget,
  summarizeUptime,
  type LatencyProbes,
} from './uptime';

/**
 * The uptime table. One row per service: status, 24 h strip, latency, last check.
 *
 * Latency has no column in `uptime_events`, so the cell shows what the session measured —
 * by a per-row check or by a fleet run, both of which report it — and an em dash until
 * then, never a zero pretending to be a measurement. Tunnel services are marked instead of
 * being shown as stale: neither the scheduler nor `check-all` ever probes them.
 */

export interface MonitoringTableProps {
  services: Service[];
  history: ServiceHistoryResponse | undefined;
  /**
   * `GET /api/services/history` failed. `history` is then `undefined` for every row, which is
   * the same shape as a genuinely quiet service -- and the cell would state "no check in the
   * last 24 h" about hosts that were in fact probed every cycle.
   */
  historyError?: boolean;
  probes: LatencyProbes;
  checkingId: number | null;
  /** Frozen once per render pass so every strip buckets against the same instant. */
  now: number;
  selectedId: number | null;
  loading?: boolean;
  onSelect: (service: Service) => void;
  onCheck: (serviceId: number) => void;
  /** Rendered when there is nothing to show — the page knows whether a filter is on. */
  empty: { title: string; description: string; action?: ReactNode };
}

const HEAD_CELL = 'px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground';

export function MonitoringTable({
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
}: MonitoringTableProps) {
  const t = useT();
  const { formatRelative, formatLatency, formatPercent, formatDateTime } = useFormat();

  if (loading) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2, 3, 4].map((row) => (
          <div key={row} className="grid grid-cols-[1fr_auto] items-center gap-4">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-24" />
          </div>
        ))}
      </div>
    );
  }

  if (services.length === 0) {
    return <EmptyState icon={<ServerOff />} title={empty.title} description={empty.description} action={empty.action} />;
  }

  return (
    // `relative` is not cosmetic: Tailwind ships `sr-only` as `position: absolute`, and with a
    // static scroller the containing block of those spans is a positioned ancestor further up,
    // so the two in the last column escaped this clip at x=842 and handed `main` 467px of
    // phantom scroll width. Positioning the scroller makes it their containing block; they keep
    // their static position and get clipped with the rest of the row.
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[820px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className={HEAD_CELL}>
              {t('monitoring.table.hostname')}
            </th>
            <th scope="col" className={HEAD_CELL}>
              {t('monitoring.table.status')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'w-[220px]')}>
              {t('monitoring.table.uptime_24h')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'text-right')}>
              {t('monitoring.table.latency')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'text-right')}>
              {t('monitoring.table.checked')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'w-10 text-right')}>
              <span className="sr-only">{t('monitoring.table.actions')}</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {services.map((service) => {
            const status = serviceStatus(service);
            const host = serviceHost(service);
            const tunnel = isTunnelService(service);
            const summary = summarizeUptime(history?.[String(service.id)], now);
            const probe = probes[service.id];
            const selected = selectedId === service.id;
            const availability =
              summary.availability === null ? null : formatPercent(summary.availability * 100, 1);

            return (
              <tr
                key={service.id}
                onClick={() => onSelect(service)}
                className={cn(
                  'cursor-pointer align-middle transition-colors',
                  selected ? 'bg-accent/60' : 'hover:bg-accent/40',
                )}
              >
                <td className="px-3 py-2.5">
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect(service);
                    }}
                    className="block max-w-[280px] text-left focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-sm"
                  >
                    <span
                      className={cn(
                        'block truncate font-medium',
                        status === 'disabled' ? 'text-muted-foreground' : 'text-foreground',
                      )}
                    >
                      {host}
                    </span>
                    <span className="mt-0.5 flex items-center gap-1.5 truncate font-mono text-[11px] text-muted-foreground">
                      {tunnel ? <Waypoints className="h-3 w-3 shrink-0" aria-hidden /> : null}
                      {tunnel ? service.tunnel_provider_name || t('monitoring.tunnel_route') : serviceTarget(service)}
                    </span>
                  </button>
                </td>

                <td className="px-3 py-2.5">
                  <div className="flex flex-col items-start gap-1">
                    <Badge tone={STATUS_TONE[status]} dot size="sm">
                      {t(STATUS_LABEL_KEY[status])}
                    </Badge>
                    {status === 'error' && (
                      <span className="text-[11px] text-destructive">
                        {tunnel
                          ? t('monitoring.error.check_tunnel')
                          : t('monitoring.error.tcp_unreachable', { target: serviceTarget(service) })}
                      </span>
                    )}
                    {status === 'disabled' && (
                      <span className="text-[11px] text-muted-foreground">{t('monitoring.status.disabled_by_user')}</span>
                    )}
                  </div>
                </td>

                <td className="px-3 py-2.5">
                  {tunnel && summary.total === 0 ? (
                    // The tooltip opens on hover only -- these spans are not focusable and must not
                    // become tab stops in a table -- so everything it says is also written out for
                    // screen readers, and the bubble stays pure redundancy.
                    <Tooltip content={t('monitoring.tunnel_not_probed_hint')}>
                      <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        <Waypoints className="h-3 w-3" aria-hidden />
                        {t('monitoring.tunnel_not_probed')}
                        <span className="sr-only"> — {t('monitoring.tunnel_not_probed_hint')}</span>
                      </span>
                    </Tooltip>
                  ) : (
                    <div className="space-y-1">
                      <UptimeStrip
                        summary={summary}
                        emptyLabel={historyError ? t('monitoring.uptime.history_failed') : undefined}
                        label={
                          availability
                            ? t('monitoring.uptime.aria', { host, percent: availability })
                            : t('monitoring.uptime.aria_empty', { host })
                        }
                      />
                      {/* Only when there is a percentage to state. With no history the strip
                          already carries the sentence, and printing it again underneath put
                          it twice in the same cell. */}
                      {availability && (
                        <p className="text-[11px] tabular-nums text-muted-foreground">
                          {t('monitoring.uptime.summary', { percent: availability, count: summary.total })}
                        </p>
                      )}
                    </div>
                  )}
                </td>

                <td className="px-3 py-2.5 text-right">
                  {probe ? (
                    <Tooltip content={t('monitoring.latency.measured_at', { when: formatDateTime(new Date(probe.at)) })}>
                      <span
                        className={cn(
                          'tabular-nums',
                          probe.latencyMs === null ? 'text-muted-foreground' : 'text-foreground',
                        )}
                      >
                        {formatLatency(probe.latencyMs)}
                        <span className="sr-only">
                          {' — '}
                          {t('monitoring.latency.measured_at', { when: formatDateTime(new Date(probe.at)) })}
                        </span>
                      </span>
                    </Tooltip>
                  ) : (
                    <span className="tabular-nums text-muted-foreground">{EM_DASH}</span>
                  )}
                </td>

                <td className="px-3 py-2.5 text-right text-[11px] text-muted-foreground">
                  {service.last_checked ? (
                    <Tooltip content={formatDateTime(service.last_checked)}>
                      <span className="whitespace-nowrap">
                        {formatRelative(service.last_checked, now)}
                        <span className="sr-only">
                          {' — '}
                          {t('ui.tooltip.exact_time', { when: formatDateTime(service.last_checked) })}
                        </span>
                      </span>
                    </Tooltip>
                  ) : (
                    <span className="whitespace-nowrap">{t('monitoring.never_checked')}</span>
                  )}
                </td>

                <td className="px-3 py-2.5 text-right">
                  <div onClick={(event) => event.stopPropagation()} role="presentation">
                    <IconButton
                      label={t('monitoring.check_row', { host })}
                      icon={<Radio />}
                      variant="ghost"
                      size="icon"
                      loading={checkingId === service.id}
                      onClick={() => onCheck(service.id)}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Only while the column is empty. Once a check has filled it the sentence is noise,
          telling operators to do the thing they just did. */}
      {Object.keys(probes).length === 0 && (
        <p className="flex items-center gap-1.5 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
          <Activity className="h-3 w-3 shrink-0" aria-hidden />
          {t('monitoring.latency.column_hint')}
        </p>
      )}
    </div>
  );
}

