import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import {
  Activity,
  ArrowDownCircle,
  CircleHelp,
  Gauge,
  LayoutGrid,
  LayoutList,
  Power,
  Radio,
  ShieldCheck,
  Timer,
  Waypoints,
} from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { translateApiError } from '@/lib/errors';
import { isArray, readJSON, writeJSON } from '@/lib/storage';
import { EM_DASH } from '@/lib/format';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  Chip,
  ChipGroup,
  IconButton,
  InlineAlert,
  PageHeader,
  ProgressBar,
  SearchInput,
  Select,
  StatCard,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  cn,
} from '@/components/ui';
import { AccessPanel } from '@/components/features/monitoring/AccessPanel';
import { MonitoringTable } from '@/components/features/monitoring/MonitoringTable';
import { ServiceCards } from '@/components/features/monitoring/ServiceCards';
import { ServiceDrawer } from '@/components/features/monitoring/ServiceDrawer';
import { TunnelsCard } from '@/components/features/monitoring/TunnelsCard';
import { useServiceProbes } from '@/components/features/monitoring/useServiceProbes';
import { openRouteCount, useTunnelAccess } from '@/components/features/monitoring/useTunnelAccess';
import {
  STATUS_FILTERS,
  STATUS_LABEL_KEY,
  autoCheckCadence,
  fleetLatency,
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
  SettingsSaveResult,
  TunnelHealthResponse,
} from '@/types/api';

/**
 * Monitoring: is every published endpoint answering, has it been answering all day, and does
 * it ask for a login.
 *
 * The figures sit above three tabs, kept in the URL (`?tab=`) so a link lands on the right one:
 *  - services: every service, as tiles or as a table, with the drawer behind each one;
 *  - tunnels:  the Cloudflare connectors, the reason a whole tunnel goes down at once;
 *  - access:   which tunnel routes Cloudflare Access protects, and the button that protects one.
 *
 * Data sources, all read-only except the check routes, the settings switch and Protect:
 *  - `GET  /api/services`                 the rows, their last status, `status_detail`, latency
 *  - `GET  /api/services/history`         the `uptime_events` of the last 24 h, per service
 *  - `GET  /api/logs`                     the recent lines, matched to a host in the drawer
 *  - `GET  /api/providers/tunnels/health` the Cloudflare connectors
 *  - `GET  /api/providers/tunnels/access` the Access state of every tunnel route
 *  - `POST /api/services/check-all`       probe everything now
 *  - `POST /api/services/{sid}/check`     probe one service and resolve its hostname
 *  - `POST /api/settings`                 turn the automatic checks back on, from the banner
 */

const SERVICES_CACHE_KEY = 'vauxtra.cache.services';
const REFRESH_STORAGE_KEY = 'vauxtra.monitoring.refresh';
const VIEW_STORAGE_KEY = 'vauxtra.monitoring.view';

/** `get_logs` clamps `per_page` to 200: asking for 300 silently returned 200. */
const LOGS_PAGE_SIZE = 200;
/** How many recent lines the drawer searches through; two clamped pages. */
const LOGS_SAMPLE = 300;

const REFRESH_CHOICES = [0, 10_000, 15_000, 30_000, 60_000, 300_000] as const;
const DEFAULT_REFRESH = 15_000;

/** What the banner switches the checks to: the default of a fresh install. */
const DEFAULT_CHECK_INTERVAL = '5';

const TABS = ['services', 'tunnels', 'access'] as const;
type MonitoringTab = (typeof TABS)[number];

function toTab(raw: string | null): MonitoringTab {
  return (TABS as readonly string[]).includes(raw ?? '') ? (raw as MonitoringTab) : 'services';
}

type ViewMode = 'cards' | 'table';

function readRefreshChoice(): number {
  try {
    const raw = Number(localStorage.getItem(REFRESH_STORAGE_KEY));
    return (REFRESH_CHOICES as readonly number[]).includes(raw) ? raw : DEFAULT_REFRESH;
  } catch {
    return DEFAULT_REFRESH;
  }
}

function readViewMode(): ViewMode {
  try {
    return localStorage.getItem(VIEW_STORAGE_KEY) === 'table' ? 'table' : 'cards';
  } catch {
    return 'cards';
  }
}

function readServicesCache(): Service[] | undefined {
  return readJSON(SERVICES_CACHE_KEY, isArray<Service>, undefined, 'session');
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

/**
 * `checked` counts every enabled service. Tunnel routes are probed through Cloudflare like
 * the rest; only a service without a port is left out, and the summary says how many.
 */
interface CheckSummary {
  checked: number;
  ok: number;
  error: number;
  skippedNoPort: number;
}

export function Monitoring() {
  const t = useT();
  const { formatNumber, formatPercent, formatLatency } = useFormat();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const tab = toTab(searchParams.get('tab'));
  const statusFilter = toStatusFilter(searchParams.get('status'));
  const selectedId = Number(searchParams.get('service')) || null;

  const [search, setSearch] = useState('');
  const [refreshMs, setRefreshMs] = useState<number>(readRefreshChoice);
  const [savedView, setSavedView] = useState<ViewMode>(readViewMode);
  const [summary, setSummary] = useState<CheckSummary | null>(null);

  // The table needs a desktop's width; below `md` it only scrolls sideways, so phones get the
  // tiles whatever was saved, and the switch that could not change that is hidden.
  const isNarrow = useMediaQuery('(max-width: 767px)');
  const view: ViewMode = isNarrow ? 'cards' : savedView;

  // One instant per tick, shared by every strip and every relative date on the page.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    // Private mode: the choice simply does not survive a reload.
    writeJSON(REFRESH_STORAGE_KEY, refreshMs);
  }, [refreshMs]);

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, savedView);
    } catch {
      // Same.
    }
  }, [savedView]);

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

  const accessQuery = useTunnelAccess();

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
    writeJSON(SERVICES_CACHE_KEY, servicesQuery.data, 'session');
  }, [servicesQuery.data]);

  const probes = useServiceProbes();

  const checkAll = useMutation({
    mutationFn: () => api.post<CheckAllResult>('/services/check-all'),
    onSuccess: async (result) => {
      const checked = result?.checked ?? 0;
      setSummary({
        checked,
        ok: result?.ok ?? 0,
        error: result?.error ?? 0,
        skippedNoPort: result?.skipped_no_port ?? 0,
      });
      // The fleet check measured every latency on its way through; `results` is absent on
      // an instance older than 1.5.0, and the column then stays as it was.
      probes.record(result?.results ?? []);
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

  // Checks were found off in production: every status on this page was then frozen at the
  // last manual check, and the only way back was a setting three screens away.
  const enableChecks = useMutation({
    mutationFn: () => api.post<SettingsSaveResult>('/settings', { check_interval: DEFAULT_CHECK_INTERVAL }),
    onSuccess: async (result) => {
      const refused = [...(result?.ignored ?? []), ...(result?.not_applied ?? [])].includes('check_interval');
      if (refused) toast.error(t('monitoring.checks_off.enable_failed'));
      else toast.success(t('monitoring.checks_off.enabled'));
      await queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('monitoring.checks_off.enable_failed'))),
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

  // `autoCheckCadence` carries why this is read from the setting and never from `last_checked`.
  const cadence = autoCheckCadence(settings?.check_interval);
  const latency = useMemo(() => fleetLatency(services, probes.probes), [services, probes.probes]);
  const tunnelCount = Array.isArray(tunnelsQuery.data?.items) ? tunnelsQuery.data.items.length : 0;
  const openRoutes = openRouteCount(accessQuery.data);

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
    (next: { status?: StatusFilter; service?: number | null; tab?: MonitoringTab }) => {
      const params = new URLSearchParams(searchParams);
      if (next.status !== undefined) {
        if (next.status === 'all') params.delete('status');
        else params.set('status', next.status);
      }
      if (next.service !== undefined) {
        if (next.service === null) params.delete('service');
        else params.set('service', String(next.service));
      }
      if (next.tab !== undefined) {
        if (next.tab === 'services') params.delete('tab');
        else params.set('tab', next.tab);
      }
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  /** A figure filters the services, so it also brings their tab forward. */
  const toggleStatus = (status: StatusFilter) =>
    setParams({ status: statusFilter === status ? 'all' : status, tab: 'services' });

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

  const empty =
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
      : { title: t('monitoring.empty.all'), description: t('monitoring.empty.all_hint') };

  const listProps = {
    services: filtered,
    history: historyQuery.data,
    historyError: historyQuery.isError,
    probes: probes.probes,
    checkingId: probes.checkingId,
    now,
    selectedId,
    loading,
    onSelect: (service: Service) => setParams({ service: service.id }),
    onCheck: probes.check,
    empty,
  };

  return (
    <div className="mx-auto max-w-screen-2xl space-y-6 pb-8 duration-200 animate-in fade-in">
      <PageHeader
        eyebrow={t('nav.group.operations')}
        title={t('nav.monitoring')}
        description={t('monitoring.page_description')}
        icon={<Activity />}
        meta={
          cadence.state === 'every' ? (
            <span className="text-xs text-muted-foreground">
              {t('monitoring.auto_checks_every', { minutes: cadence.minutes })}
            </span>
          ) : cadence.state === 'off' ? (
            <Link
              to="/settings?tab=general"
              className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              {t('monitoring.auto_checks_disabled')}
            </Link>
          ) : null
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

      {cadence.state === 'off' && (
        <InlineAlert
          tone="warning"
          icon={<Power />}
          title={t('monitoring.checks_off.title')}
          action={
            <Button size="sm" loading={enableChecks.isPending} onClick={() => enableChecks.mutate()}>
              {t('monitoring.checks_off.enable')}
            </Button>
          }
        >
          {t('monitoring.checks_off.body')}
        </InlineAlert>
      )}

      {checkAll.isPending && (
        <InlineAlert tone="info" title={t('monitoring.check_running')}>
          <ProgressBar indeterminate size="sm" label={t('monitoring.check_running')} className="mt-2" />
        </InlineAlert>
      )}

      {!checkAll.isPending && summary && (
        <InlineAlert
          tone={summary.error > 0 ? 'warning' : 'success'}
          title={t('monitoring.check_summary', {
            count: summary.checked,
            ok: formatNumber(summary.ok),
            error: formatNumber(summary.error),
          })}
          onDismiss={() => setSummary(null)}
        >
          {summary.skippedNoPort > 0 && (
            <p>{t('monitoring.check_skipped_no_port', { count: summary.skippedNoPort })}</p>
          )}
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

      {/* Two columns from the smallest screen up: one card per row pushed the tabs a whole phone
          screen down. The fifth card takes the full width until three columns fit. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label={t('monitoring.stat.up')}
          value={formatNumber(counts.ok)}
          hint={t('monitoring.stat.up_hint')}
          icon={<ShieldCheck />}
          tone="success"
          loading={loading}
          onClick={() => toggleStatus('ok')}
        />
        <StatCard
          label={t('monitoring.stat.down')}
          value={formatNumber(counts.error)}
          hint={t('monitoring.stat.down_hint')}
          icon={<ArrowDownCircle />}
          tone={counts.error > 0 ? 'danger' : 'neutral'}
          loading={loading}
          onClick={() => toggleStatus('error')}
        />
        <StatCard
          label={t('monitoring.stat.unknown')}
          value={formatNumber(counts.unknown)}
          hint={t('monitoring.stat.unknown_hint')}
          icon={<CircleHelp />}
          tone={counts.unknown > 0 ? 'warning' : 'neutral'}
          loading={loading}
          onClick={() => toggleStatus('unknown')}
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
          value={latency.average === null ? EM_DASH : formatLatency(latency.average)}
          hint={
            latency.count > 0
              ? t('monitoring.stat.latency_stored_hint', { count: latency.count })
              : t('monitoring.stat.latency_empty')
          }
          icon={<Timer />}
          tone="neutral"
          loading={loading}
          className="col-span-2 lg:col-span-1"
        />
      </div>

      <Tabs value={tab} onValueChange={(value) => setParams({ tab: toTab(value) })}>
        <TabList aria-label={t('monitoring.tabs_label')}>
          <Tab value="services" icon={<Activity />} count={counts.all}>
            {t('monitoring.tab.services')}
          </Tab>
          <Tab value="tunnels" icon={<Waypoints />} count={tunnelCount || undefined}>
            {t('monitoring.tab.tunnels')}
          </Tab>
          <Tab value="access" icon={<ShieldCheck />} count={openRoutes || undefined}>
            {t('monitoring.tab.access')}
          </Tab>
        </TabList>

        <TabPanel value="services" className="pt-4">
          {/* `min-w-0`: a grid or flex item defaults to `min-width: auto`, so the table's
              `min-w-[820px]` climbed back up and sized the card at 820px on a phone, which
              `main` then cut off with no scrollbar. */}
          <Card className="min-w-0">
            <CardHeader className="gap-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <SearchInput
                  size="sm"
                  value={search}
                  onChange={setSearch}
                  placeholder={t('monitoring.filter_routes_placeholder')}
                  aria-label={t('monitoring.filter_routes_placeholder')}
                  wrapperClassName="w-full max-w-sm"
                />
                <div
                  className="hidden items-center gap-1 rounded-xl bg-muted p-1 md:inline-flex"
                  role="group"
                  aria-label={t('monitoring.view_label')}
                >
                  <IconButton
                    label={t('monitoring.view.cards')}
                    icon={<LayoutGrid />}
                    tooltip
                    size="sm"
                    variant="ghost"
                    aria-pressed={view === 'cards'}
                    className={cn('h-8 w-8', view === 'cards' ? 'bg-card text-primary shadow-sm hover:bg-card' : 'text-muted-foreground')}
                    onClick={() => setSavedView('cards')}
                  />
                  <IconButton
                    label={t('monitoring.view.table')}
                    icon={<LayoutList />}
                    tooltip
                    size="sm"
                    variant="ghost"
                    aria-pressed={view === 'table'}
                    className={cn('h-8 w-8', view === 'table' ? 'bg-card text-primary shadow-sm hover:bg-card' : 'text-muted-foreground')}
                    onClick={() => setSavedView('table')}
                  />
                </div>
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
              {view === 'cards' ? <ServiceCards {...listProps} /> : <MonitoringTable {...listProps} />}
            </CardContent>
          </Card>
        </TabPanel>

        <TabPanel value="tunnels" className="pt-4">
          <TunnelsCard
            data={tunnelsQuery.data}
            loading={tunnelsQuery.isPending}
            isError={tunnelsQuery.isError}
            refreshing={tunnelsQuery.isFetching}
            onRefresh={() => void tunnelsQuery.refetch()}
          />
        </TabPanel>

        <TabPanel value="access" className="pt-4">
          <AccessPanel
            data={accessQuery.data}
            loading={accessQuery.isPending}
            isError={accessQuery.isError}
            refreshing={accessQuery.isFetching}
            onRefresh={() => void accessQuery.refetch()}
          />
        </TabPanel>
      </Tabs>

      <ServiceDrawer
        open={selectedService !== null}
        onClose={() => setParams({ service: null })}
        service={selectedService}
        history={selectedHistory}
        historyError={historyQuery.isError}
        historyLoading={historyQuery.isLoading}
        logs={selectedLogs}
        logsError={logsQuery.isError}
        logsLoading={logsQuery.isLoading}
        probe={selectedService ? probes.probes[selectedService.id] : undefined}
        checking={probes.checkingId === selectedService?.id}
        onCheck={probes.check}
        now={now}
      />
    </div>
  );
}
