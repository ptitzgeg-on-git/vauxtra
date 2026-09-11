import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Activity, ArrowDownCircle, CircleHelp, Gauge, Radio, ShieldCheck, Timer } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { translateApiError } from '@/lib/errors';
import { EM_DASH } from '@/lib/format';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Chip,
  ChipGroup,
  InlineAlert,
  PageHeader,
  ProgressBar,
  SearchInput,
  Select,
  StatCard,
} from '@/components/ui';
import { MonitoringTable } from '@/components/features/monitoring/MonitoringTable';
import { ServiceDrawer } from '@/components/features/monitoring/ServiceDrawer';
import { TunnelsCard } from '@/components/features/monitoring/TunnelsCard';
import { useServiceProbes } from '@/components/features/monitoring/useServiceProbes';
import {
  STATUS_FILTERS,
  STATUS_LABEL_KEY,
  overallAvailability,
  serviceHost,
  serviceStatus,
  serviceTarget,
  toStatusFilter,
  type StatusFilter,
} from '@/components/features/monitoring/uptime';
import type {
  CheckAllResult,
  LogEntry,
  LogsResponse,
  Service,
  ServiceHistoryResponse,
  TunnelHealthResponse,
} from '@/types/api';

/**
 * Monitoring — is every published endpoint answering, and has it been answering all day.
 *
 * Data sources, all read-only except the two check routes:
 *  - `GET  /api/services`                the rows, their last known status and `last_checked`
 *  - `GET  /api/services/history`        the `uptime_events` of the last 24 h, per service
 *  - `GET  /api/logs`                    the recent lines, matched to a host in the drawer
 *  - `GET  /api/providers/tunnels/health` the Cloudflare connectors
 *  - `POST /api/services/check-all`      probe everything now
 *  - `GET  /api/services/{sid}/check`    probe one service and measure its latency
 */

const SERVICES_CACHE_KEY = 'vauxtra.cache.services';
const REFRESH_STORAGE_KEY = 'vauxtra.monitoring.refresh';

/** `get_logs` clamps `per_page` to 200 — asking for 300 silently returned 200. */
const LOGS_PAGE_SIZE = 200;
/** How many recent lines the drawer searches through; two clamped pages. */
const LOGS_SAMPLE = 300;

const REFRESH_CHOICES = [0, 10_000, 15_000, 30_000, 60_000, 300_000] as const;
const DEFAULT_REFRESH = 15_000;

function readRefreshChoice(): number {
  try {
    const raw = Number(localStorage.getItem(REFRESH_STORAGE_KEY));
    return (REFRESH_CHOICES as readonly number[]).includes(raw) ? raw : DEFAULT_REFRESH;
  } catch {
    return DEFAULT_REFRESH;
  }
}

function readServicesCache(): Service[] | undefined {
  try {
    const raw = sessionStorage.getItem(SERVICES_CACHE_KEY);
    if (!raw) return undefined;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Service[]) : undefined;
  } catch {
    return undefined;
  }
}

/**
 * The recent log lines, in pages the backend will actually serve.
 *
 * The page used to ask for `per_page=300` and quietly receive 200, so anything older than
 * the 200th line never reached the drawer's filter. Two clamped pages restore the depth.
 */
async function fetchRecentLogs(): Promise<LogEntry[]> {
  const first = await api.get<LogsResponse>(`/logs?page=1&per_page=${LOGS_PAGE_SIZE}`);
  const items = Array.isArray(first.items) ? [...first.items] : [];
  const wantMore = items.length >= LOGS_PAGE_SIZE && items.length < LOGS_SAMPLE && (first.pages ?? 1) > 1;
  if (!wantMore) return items;
  const second = await api.get<LogsResponse>(`/logs?page=2&per_page=${LOGS_PAGE_SIZE}`);
  const rest = Array.isArray(second.items) ? second.items : [];
  return items.concat(rest.slice(0, LOGS_SAMPLE - items.length));
}

interface CheckSummary extends CheckAllResult {
  /** `checked` counts every enabled service, tunnels included — and the route skips those. */
  skipped: number;
}

export function Monitoring() {
  const t = useT();
  const { formatPercent, formatLatency, formatNumber } = useFormat();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusFilter = toStatusFilter(searchParams.get('status'));
  const selectedId = Number(searchParams.get('service')) || null;

  const [search, setSearch] = useState('');
  const [refreshMs, setRefreshMs] = useState<number>(readRefreshChoice);
  const [summary, setSummary] = useState<CheckSummary | null>(null);

  // One instant per tick, shared by every strip and every relative date on the page.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(REFRESH_STORAGE_KEY, String(refreshMs));
    } catch {
      // Private mode: the choice simply does not survive a reload.
    }
  }, [refreshMs]);

  const autoRefresh = refreshMs > 0 ? refreshMs : false;

  const servicesQuery = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
    refetchInterval: autoRefresh,
    initialData: readServicesCache,
  });

  const { data: settings } = useQuery<Record<string, string>>({
    queryKey: ['settings'],
    queryFn: () => api.get<Record<string, string>>('/settings'),
  });

  const logsQuery = useQuery<LogEntry[]>({
    queryKey: ['logs', 'monitoring'],
    queryFn: fetchRecentLogs,
    refetchInterval: autoRefresh,
  });
  const logs = logsQuery.data;

  const historyQuery = useQuery<ServiceHistoryResponse>({
    queryKey: ['services-history'],
    queryFn: () => api.get<ServiceHistoryResponse>('/services/history'),
    refetchInterval: autoRefresh,
  });

  const tunnelsQuery = useQuery<TunnelHealthResponse>({
    queryKey: ['providers-tunnel-health'],
    queryFn: () => api.get<TunnelHealthResponse>('/providers/tunnels/health'),
    // Every poll asks Cloudflare over the network; never faster than every 30 s.
    refetchInterval: refreshMs > 0 ? Math.max(refreshMs, 30_000) : false,
  });

  const services = useMemo(
    () => (Array.isArray(servicesQuery.data) ? servicesQuery.data : []),
    [servicesQuery.data],
  );

  // The RAW query result, never the normalised memo: on the first render `data` is `undefined`
  // and the memo is `[]`, and writing that `[]` would seed `readServicesCache()` -- the
  // `initialData` of the shared `['services']` key -- with an empty list on the next load. Every
  // page reading that key would then render its "no services" empty state instead of a skeleton.
  useEffect(() => {
    if (!Array.isArray(servicesQuery.data)) return;
    try {
      sessionStorage.setItem(SERVICES_CACHE_KEY, JSON.stringify(servicesQuery.data));
    } catch {
      // Ignore storage quota / private mode errors.
    }
  }, [servicesQuery.data]);

  const probes = useServiceProbes();

  const checkAll = useMutation({
    mutationFn: () => api.post<CheckAllResult>('/services/check-all'),
    onSuccess: async (result) => {
      const checked = result?.checked ?? 0;
      const ok = result?.ok ?? 0;
      const error = result?.error ?? 0;
      setSummary({ checked, ok, error, skipped: Math.max(0, checked - ok - error) });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['services'] }),
        queryClient.invalidateQueries({ queryKey: ['services-history'] }),
        queryClient.invalidateQueries({ queryKey: ['logs'] }),
      ]);
      toast.success(t('monitoring.toast.checked_count', { count: checked }));
    },
    onError: (err: unknown) => {
      setSummary(null);
      toast.error(translateApiError(err, t, t('monitoring.toast.check_failed')));
    },
  });

  // --- counts -------------------------------------------------------------

  const counts = useMemo(() => {
    const tally = { all: services.length, ok: 0, error: 0, unknown: 0, disabled: 0 };
    for (const service of services) tally[serviceStatus(service)] += 1;
    return tally;
  }, [services]);

  const availability = useMemo(
    () => overallAvailability(historyQuery.data, services, now),
    [historyQuery.data, services, now],
  );

  const checkIntervalMinutes = Number(settings?.check_interval) || 5;
  const hasAutoCheckData = services.some((service) => Boolean(service.last_checked));
  const probedCount = Object.keys(probes.probes).length;

  // --- filtering ----------------------------------------------------------

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return services.filter((service) => {
      if (statusFilter !== 'all' && serviceStatus(service) !== statusFilter) return false;
      if (!needle) return true;
      return (
        serviceHost(service).toLowerCase().includes(needle) ||
        serviceTarget(service).toLowerCase().includes(needle) ||
        (service.proxy_provider_name || '').toLowerCase().includes(needle) ||
        (service.tunnel_provider_name || '').toLowerCase().includes(needle)
      );
    });
  }, [services, statusFilter, search]);

  const setParams = useCallback(
    (next: { status?: StatusFilter; service?: number | null }) => {
      const params = new URLSearchParams(searchParams);
      if (next.status !== undefined) {
        if (next.status === 'all') params.delete('status');
        else params.set('status', next.status);
      }
      if (next.service !== undefined) {
        if (next.service === null) params.delete('service');
        else params.set('service', String(next.service));
      }
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const selectedService = useMemo(
    () => services.find((service) => service.id === selectedId) ?? null,
    [services, selectedId],
  );

  const selectedLogs = useMemo(() => {
    if (!selectedService) return [];
    const host = serviceHost(selectedService).toLowerCase();
    const token = `service ${selectedService.id}`;
    return (logs ?? []).filter((log) => {
      const message = String(log.message || '').toLowerCase();
      return message.includes(host) || message.includes(token);
    });
  }, [selectedService, logs]);

  const selectedHistory = useMemo(() => {
    if (!selectedService) return [];
    const points = historyQuery.data?.[String(selectedService.id)];
    return Array.isArray(points) ? points : [];
  }, [selectedService, historyQuery.data]);

  // --- render -------------------------------------------------------------

  const loading = servicesQuery.isPending && services.length === 0;

  return (
    <div className="mx-auto max-w-7xl space-y-6 pb-8 duration-200 animate-in fade-in">
      <PageHeader
        eyebrow={t('nav.group.operations')}
        title={t('nav.monitoring')}
        description={t('monitoring.page_description')}
        icon={<Activity />}
        meta={
          <span className="text-xs text-muted-foreground">
            {hasAutoCheckData
              ? t('monitoring.auto_checks_every', { minutes: checkIntervalMinutes })
              : t('monitoring.auto_checks_waiting')}
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Select
              size="sm"
              aria-label={t('monitoring.refresh_label')}
              value={String(refreshMs)}
              onChange={(event) => setRefreshMs(Number(event.target.value))}
              wrapperClassName="w-auto"
            >
              {REFRESH_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {choice === 0
                    ? t('monitoring.refresh.off')
                    : choice < 60_000
                      ? t('monitoring.refresh.seconds', { seconds: choice / 1000 })
                      : t('monitoring.refresh.minutes', { minutes: choice / 60_000 })}
                </option>
              ))}
            </Select>
            <Button
              leftIcon={<Radio />}
              loading={checkAll.isPending}
              onClick={() => {
                setSummary(null);
                checkAll.mutate();
              }}
            >
              {t('monitoring.check_all')}
            </Button>
          </div>
        }
      />

      {checkAll.isPending && (
        <InlineAlert tone="info" title={t('monitoring.check_running')}>
          <ProgressBar indeterminate size="sm" label={t('monitoring.check_running')} className="mt-2" />
        </InlineAlert>
      )}

      {!checkAll.isPending && summary && (
        <InlineAlert
          tone={summary.error > 0 ? 'warning' : 'success'}
          title={t('monitoring.check_summary', {
            checked: summary.checked,
            ok: summary.ok,
            error: summary.error,
          })}
          onDismiss={() => setSummary(null)}
        >
          {summary.skipped > 0 ? t('monitoring.check_skipped', { count: summary.skipped }) : null}
        </InlineAlert>
      )}

      {servicesQuery.isError && (
        <InlineAlert
          tone="danger"
          title={t('monitoring.load_failed')}
          action={
            <Button variant="outline" size="sm" onClick={() => void servicesQuery.refetch()}>
              {t('common.retry')}
            </Button>
          }
        >
          {translateApiError(servicesQuery.error, t, t('monitoring.load_failed_hint'))}
        </InlineAlert>
      )}

      {historyQuery.isError && (
        <InlineAlert
          tone="warning"
          title={t('monitoring.history.load_failed')}
          action={
            <Button variant="outline" size="sm" loading={historyQuery.isFetching} onClick={() => void historyQuery.refetch()}>
              {t('common.retry')}
            </Button>
          }
        >
          {translateApiError(historyQuery.error, t, t('monitoring.history.load_failed_hint'))}
        </InlineAlert>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label={t('monitoring.stat.up')}
          value={counts.ok}
          hint={t('monitoring.stat.up_hint')}
          icon={<ShieldCheck />}
          tone="success"
          loading={loading}
          onClick={() => setParams({ status: statusFilter === 'ok' ? 'all' : 'ok' })}
        />
        <StatCard
          label={t('monitoring.stat.down')}
          value={counts.error}
          hint={t('monitoring.stat.down_hint')}
          icon={<ArrowDownCircle />}
          tone={counts.error > 0 ? 'danger' : 'neutral'}
          loading={loading}
          onClick={() => setParams({ status: statusFilter === 'error' ? 'all' : 'error' })}
        />
        <StatCard
          label={t('monitoring.stat.unknown')}
          value={counts.unknown}
          hint={t('monitoring.stat.unknown_hint')}
          icon={<CircleHelp />}
          tone={counts.unknown > 0 ? 'warning' : 'neutral'}
          loading={loading}
          onClick={() => setParams({ status: statusFilter === 'unknown' ? 'all' : 'unknown' })}
        />
        <StatCard
          label={t('monitoring.stat.availability')}
          value={availability === null ? EM_DASH : formatPercent(availability * 100, 1)}
          hint={
            historyQuery.isError
              ? t('monitoring.stat.availability_failed')
              : availability === null
                ? t('monitoring.stat.availability_empty')
                : t('monitoring.stat.availability_hint')
          }
          icon={<Gauge />}
          tone={availability !== null && availability < 0.99 ? 'warning' : 'info'}
          loading={loading || historyQuery.isPending}
        />
        <StatCard
          label={t('monitoring.stat.latency')}
          value={probes.average === null ? EM_DASH : formatLatency(probes.average)}
          hint={
            probedCount > 0
              ? t('monitoring.stat.latency_hint', { count: formatNumber(probedCount) })
              : t('monitoring.stat.latency_empty')
          }
          icon={<Timer />}
          tone="neutral"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <Card className="xl:col-span-8">
          <CardHeader className="gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle>{t('monitoring.route_health')}</CardTitle>
              <SearchInput
                size="sm"
                value={search}
                onChange={setSearch}
                placeholder={t('monitoring.filter_routes_placeholder')}
                aria-label={t('monitoring.filter_routes_placeholder')}
                wrapperClassName="w-full max-w-xs"
              />
            </div>
            <ChipGroup label={t('monitoring.filters_label')}>
              {STATUS_FILTERS.map((key) => (
                <Chip
                  key={key}
                  selected={statusFilter === key}
                  count={key === 'all' ? counts.all : counts[key]}
                  onClick={() => setParams({ status: key })}
                >
                  {key === 'all' ? t('monitoring.filter.all') : t(STATUS_LABEL_KEY[key])}
                </Chip>
              ))}
            </ChipGroup>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            <MonitoringTable
              services={filtered}
              history={historyQuery.data}
              historyError={historyQuery.isError}
              probes={probes.probes}
              checkingId={probes.checkingId}
              now={now}
              selectedId={selectedId}
              loading={loading}
              onSelect={(service) => setParams({ service: service.id })}
              onCheck={probes.check}
              empty={
                statusFilter !== 'all' || search
                  ? {
                      title: t('monitoring.empty.filter'),
                      description: t('monitoring.empty.filter_hint'),
                      action: (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setSearch('');
                            setParams({ status: 'all' });
                          }}
                        >
                          {t('monitoring.empty.clear_filters')}
                        </Button>
                      ),
                    }
                  : { title: t('monitoring.empty.all'), description: t('monitoring.empty.all_hint') }
              }
            />
          </CardContent>
        </Card>

        <div className="xl:col-span-4">
          <TunnelsCard
            data={tunnelsQuery.data}
            loading={tunnelsQuery.isPending}
            isError={tunnelsQuery.isError}
            refreshing={tunnelsQuery.isFetching}
            onRefresh={() => void tunnelsQuery.refetch()}
          />
        </div>
      </div>

      <ServiceDrawer
        open={selectedService !== null}
        onClose={() => setParams({ service: null })}
        service={selectedService}
        history={selectedHistory}
        historyError={historyQuery.isError}
        logs={selectedLogs}
        logsError={logsQuery.isError}
        probe={selectedService ? probes.probes[selectedService.id] : undefined}
        checking={probes.checkingId === selectedService?.id}
        onCheck={probes.check}
        now={now}
      />
    </div>
  );
}
