import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, BellRing, ExternalLink, History, Radio, ScrollText, Waypoints } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { EM_DASH } from '@/lib/format';
import {
  Badge,
  Button,
  Drawer,
  EmptyState,
  InlineAlert,
  Separator,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  buttonVariants,
  cn,
} from '@/components/ui';
import type { LogEntry, LogLevel, Service, ServiceHistoryPoint } from '@/types/api';
import { AlertsEditor } from './AlertsEditor';
import { UptimeStrip } from './UptimeStrip';
import {
  STATUS_LABEL_KEY,
  STATUS_TONE,
  isTunnelService,
  serviceHost,
  serviceStatus,
  serviceTarget,
  summarizeUptime,
  type LatencyProbe,
} from './uptime';

/**
 * Everything known about one service, in a side panel: the 24 h timeline from
 * `GET /api/services/history`, the alert rules from `GET/POST /api/services/{sid}/alerts`,
 * and the log lines that mention the host.
 *
 * The timeline is rendered newest first and collapses a run of identical statuses into one
 * entry — the scheduler writes a row every cycle, so a stable service would otherwise fill
 * the panel with 288 identical "ok" lines.
 */

const LOG_TONE: Record<LogLevel, string> = {
  info: 'text-muted-foreground',
  warn: 'text-warning',
  warning: 'text-warning',
  error: 'text-destructive',
};

function logTone(level: string): string {
  return LOG_TONE[level as LogLevel] ?? LOG_TONE.info;
}

interface TimelineRun {
  status: ServiceHistoryPoint['status'];
  from: string;
  to: string;
  count: number;
}

/** Collapse consecutive identical statuses; the panel shows transitions, not heartbeats. */
function collapse(points: ServiceHistoryPoint[]): TimelineRun[] {
  const runs: TimelineRun[] = [];
  for (const point of points) {
    const last = runs[runs.length - 1];
    if (last && last.status === point.status) {
      last.to = point.created_at;
      last.count += 1;
    } else {
      runs.push({ status: point.status, from: point.created_at, to: point.created_at, count: 1 });
    }
  }
  return runs.reverse();
}

export interface ServiceDrawerProps {
  open: boolean;
  onClose: () => void;
  service: Service | null;
  history: ServiceHistoryPoint[];
  /** The history request failed; an empty `history` says nothing about the service. */
  historyError?: boolean;
  logs: LogEntry[];
  /** Same, for the log request behind the third tab. */
  logsError?: boolean;
  probe: LatencyProbe | undefined;
  checking: boolean;
  onCheck: (serviceId: number) => void;
  now: number;
}

export function ServiceDrawer({
  open,
  onClose,
  service,
  history,
  historyError = false,
  logs,
  logsError = false,
  probe,
  checking,
  onCheck,
  now,
}: ServiceDrawerProps) {
  const t = useT();
  const { formatDateTime, formatRelative, formatLatency, formatPercent } = useFormat();
  const serviceId = service?.id ?? null;

  // The selected tab is remembered per service, so opening a different one always lands on
  // the timeline instead of showing the previous service's alert rules for a blink.
  const [tabState, setTabState] = useState<{ serviceId: number | null; tab: string }>({
    serviceId: null,
    tab: 'timeline',
  });
  const tab = tabState.serviceId === serviceId ? tabState.tab : 'timeline';
  const setTab = (value: string) => setTabState({ serviceId, tab: value });

  if (!service) return null;

  const host = serviceHost(service);
  const status = serviceStatus(service);
  const tunnel = isTunnelService(service);
  const summary = summarizeUptime(history, now);
  const runs = collapse(history);
  const availability = summary.availability === null ? null : formatPercent(summary.availability * 100, 1);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size="xl"
      title={host}
      description={tunnel ? t('monitoring.drawer.tunnel_subtitle') : t('monitoring.target_label', { target: serviceTarget(service) })}
      icon={<Activity />}
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <Link
            to={`/services?edit=${service.id}`}
            className={buttonVariants({ variant: 'ghost', size: 'sm' })}
          >
            {t('monitoring.drawer.open_service')}
            <ExternalLink className="ml-1.5 h-3.5 w-3.5" aria-hidden />
          </Link>
          <Button
            size="sm"
            variant="outline"
            leftIcon={<Radio />}
            loading={checking}
            onClick={() => onCheck(service.id)}
          >
            {t('monitoring.check_now')}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONE[status]} dot>
            {t(STATUS_LABEL_KEY[status])}
          </Badge>
          {tunnel && (
            <Badge tone="info" icon={<Waypoints />}>
              {service.tunnel_provider_name || t('monitoring.tunnel_route')}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">
            {service.last_checked
              ? t('monitoring.drawer.last_check', { when: formatRelative(service.last_checked, now) })
              : t('monitoring.never_checked')}
          </span>
        </div>

        <div className="grid gap-3 rounded-xl border border-border bg-muted/30 p-3 sm:grid-cols-3">
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
              {t('monitoring.stat.availability')}
            </p>
            <p className="text-lg font-semibold tabular-nums text-foreground">{availability ?? EM_DASH}</p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
              {t('monitoring.table.latency')}
            </p>
            <p className="text-lg font-semibold tabular-nums text-foreground">
              {probe ? formatLatency(probe.latencyMs) : EM_DASH}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
              {t('monitoring.drawer.events_24h')}
            </p>
            <p className="text-lg font-semibold tabular-nums text-foreground">{summary.total}</p>
          </div>
        </div>

        {probe?.dns && (
          <p className="text-xs text-muted-foreground">
            {probe.dns.length > 0
              ? t('monitoring.drawer.dns_resolved', { answers: probe.dns.join(', ') })
              : t('monitoring.drawer.dns_unresolved', { host })}
          </p>
        )}

        {tunnel && (
          <InlineAlert tone="info" title={t('monitoring.drawer.tunnel_notice')}>
            {t('monitoring.drawer.tunnel_notice_body')}
          </InlineAlert>
        )}

        <UptimeStrip
          summary={summary}
          emptyLabel={historyError ? t('monitoring.uptime.history_failed') : undefined}
          heightClass="h-7"
          label={
            availability
              ? t('monitoring.uptime.aria', { host, percent: availability })
              : t('monitoring.uptime.aria_empty', { host })
          }
        />

        <Separator />

        <Tabs value={tab} onValueChange={setTab}>
          <TabList aria-label={t('monitoring.drawer.tabs_label')}>
            <Tab value="timeline" icon={<History />}>
              {t('monitoring.timeline_title')}
            </Tab>
            <Tab value="alerts" icon={<BellRing />}>
              {t('monitoring.alerts.title')}
            </Tab>
            <Tab value="logs" icon={<ScrollText />} count={logs.length}>
              {t('monitoring.related_logs_title')}
            </Tab>
          </TabList>

          <TabPanel value="timeline" className="pt-4">
            {runs.length === 0 && historyError && !tunnel ? (
              <InlineAlert tone="warning" title={t('monitoring.history.load_failed')}>
                {t('monitoring.history.load_failed_hint')}
              </InlineAlert>
            ) : runs.length === 0 ? (
              <EmptyState
                compact
                icon={<History />}
                title={t('monitoring.timeline_empty')}
                description={
                  tunnel ? t('monitoring.tunnel_not_probed_hint') : t('monitoring.drawer.timeline_empty_hint')
                }
              />
            ) : (
              <ol className="space-y-0">
                {runs.map((run) => (
                  <li key={`${run.status}-${run.from}`} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <span
                        className={cn(
                          'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                          run.status === 'ok'
                            ? 'bg-success'
                            : run.status === 'error'
                              ? 'bg-destructive'
                              : 'bg-warning',
                        )}
                      />
                      <span className="w-px flex-1 bg-border" />
                    </div>
                    <div className="min-w-0 flex-1 pb-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={STATUS_TONE[run.status]} size="sm">
                          {t(STATUS_LABEL_KEY[run.status])}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {t('monitoring.drawer.run_count', { count: run.count })}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {run.from === run.to
                          ? formatDateTime(run.from)
                          : t('monitoring.drawer.run_range', {
                              from: formatDateTime(run.from),
                              to: formatDateTime(run.to),
                            })}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </TabPanel>

          <TabPanel value="alerts" className="pt-4">
            <AlertsEditor serviceId={service.id} host={host} />
          </TabPanel>

          <TabPanel value="logs" className="pt-4">
            {logs.length === 0 && logsError ? (
              <InlineAlert tone="warning" title={t('monitoring.logs.load_failed')}>
                {t('monitoring.logs.load_failed_hint')}
              </InlineAlert>
            ) : logs.length === 0 ? (
              <EmptyState
                compact
                icon={<ScrollText />}
                title={t('monitoring.related_logs_empty')}
                description={t('monitoring.drawer.logs_empty_hint')}
                action={
                  <Link to="/settings?tab=logs" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
                    {t('monitoring.drawer.open_logs')}
                  </Link>
                }
              />
            ) : (
              <ul className="divide-y divide-border/60 rounded-xl border border-border">
                {logs.slice(0, 50).map((log) => (
                  <li key={log.id} className="space-y-1 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className={cn('text-[11px] font-semibold uppercase', logTone(log.level))}>{log.level}</span>
                      <span className="text-[11px] text-muted-foreground">{formatDateTime(log.created_at)}</span>
                    </div>
                    <p className="wrap-break-word text-xs leading-relaxed text-foreground">{log.message}</p>
                  </li>
                ))}
              </ul>
            )}
          </TabPanel>
        </Tabs>
      </div>
    </Drawer>
  );
}
