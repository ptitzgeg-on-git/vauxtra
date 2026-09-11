import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, LayoutDashboard, LockOpen, Plug, PlugZap, Plus, RefreshCw, ShieldAlert } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { Button, InlineAlert, PageHeader } from '@/components/ui';
import { ExposeModal } from '@/components/features/expose/ExposeModal';
import { ProviderModal } from '@/components/features/ProviderModal';
import { StatusPill } from '@/components/features/dashboard/StatusPill';
import { StatRow } from '@/components/features/dashboard/StatRow';
import { NeedsAttention, type AttentionItem } from '@/components/features/dashboard/NeedsAttention';
import { RecentActivity } from '@/components/features/dashboard/RecentActivity';
import { IntegrationsGlance } from '@/components/features/dashboard/IntegrationsGlance';
import { QuickActions } from '@/components/features/dashboard/QuickActions';
import type {
  AuthStatus,
  CertificateExpiryResponse,
  LogsResponse,
  Provider,
  ProvidersHealthMap,
  Service,
  Stats,
} from '@/types/api';

const SERVICES_CACHE_KEY = 'vauxtra.cache.services';
const PROVIDERS_CACHE_KEY = 'vauxtra.cache.providers';

/** How many rows the "logs today" count reads; the backend caps `per_page` at 200. */
const TODAY_LOGS_SAMPLE = 200;

function readArrayCache<T>(key: string): T[] | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return undefined;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : undefined;
  } catch {
    return undefined;
  }
}

/** `GET /providers/health` answers a map keyed by id; older builds wrapped it in `{items}`. */
function unwrapHealth(raw: unknown): ProvidersHealthMap | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const maybe = raw as { items?: unknown };
  if (maybe.items && typeof maybe.items === 'object') return maybe.items as ProvidersHealthMap;
  return raw as ProvidersHealthMap;
}

function greetingKey(hour: number): string {
  if (hour < 12) return 'dashboard.greeting.morning';
  if (hour < 18) return 'dashboard.greeting.afternoon';
  return 'dashboard.greeting.evening';
}

export function Dashboard() {
  const t = useT();
  const { formatDate, formatTime, formatNumber } = useFormat();

  const [isCreateServiceOpen, setIsCreateServiceOpen] = useState(false);
  const [isCreateProviderOpen, setIsCreateProviderOpen] = useState(false);

  // One "now" for the whole page so relative times agree; ticks every 30 s.
  const [now, setNow] = useState(() => Date.now());
  const [hour] = useState(() => new Date().getHours());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  // -- Queries ---------------------------------------------------------------
  const {
    data: services,
    isError: servicesError,
    refetch: refetchServices,
    dataUpdatedAt: servicesUpdatedAt,
  } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
    refetchInterval: 30000,
    initialData: () => readArrayCache<Service>(SERVICES_CACHE_KEY),
  });

  const {
    data: providers,
    isError: providersError,
    refetch: refetchProviders,
  } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
    refetchInterval: 60000,
    initialData: () => readArrayCache<Provider>(PROVIDERS_CACHE_KEY),
  });

  const { data: providersHealthRaw, isError: providersHealthError } = useQuery<unknown>({
    queryKey: ['providers-health'],
    queryFn: () => api.get<unknown>('/providers/health'),
    refetchInterval: 60000,
  });
  const providersHealth = useMemo(() => unwrapHealth(providersHealthRaw), [providersHealthRaw]);

  const { data: providerTypes } = useProviderTypes();

  const { data: stats } = useQuery<Stats>({
    queryKey: ['stats'],
    queryFn: () => api.get<Stats>('/stats'),
    refetchInterval: 30000,
  });

  // No `.catch` here, deliberately. It used to resolve the promise with a synthetic
  // `expiring_soon_count: 0`, so the query never reported an error and this card told an
  // operator whose certificate expires in three days that nothing was expiring. Worse, the
  // key is shared with the sidebar and the Certificates page: whichever observer fetched
  // first wrote that fabricated zero into the cache the other two read.
  const { data: certExpiry, isError: certExpiryFailed } = useQuery<CertificateExpiryResponse>({
    queryKey: ['certificates-expiry'],
    queryFn: () => api.get<CertificateExpiryResponse>('/certificates/expiry'),
    refetchInterval: 5 * 60 * 1000,
  });

  const {
    data: logsResp,
    isPending: logsPending,
    isError: logsError,
    refetch: refetchLogs,
  } = useQuery<LogsResponse>({
    queryKey: ['logs', 'dashboard'],
    queryFn: () => api.get<LogsResponse>('/logs?per_page=8'),
    refetchInterval: 15000,
  });

  const { data: todayLogsResp, isError: todayLogsError } = useQuery<LogsResponse>({
    queryKey: ['logs', 'dashboard-today'],
    queryFn: () => api.get<LogsResponse>(`/logs?per_page=${TODAY_LOGS_SAMPLE}`),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const { data: authStatus } = useQuery<AuthStatus>({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/me'),
    staleTime: 120_000,
    retry: false,
  });

  // -- Session cache (survives a reload within the tab) ----------------------
  useEffect(() => {
    if (!Array.isArray(services)) return;
    try {
      sessionStorage.setItem(SERVICES_CACHE_KEY, JSON.stringify(services));
    } catch {
      // Ignore storage errors in private mode/quota limits.
    }
  }, [services]);

  useEffect(() => {
    if (!Array.isArray(providers)) return;
    try {
      sessionStorage.setItem(PROVIDERS_CACHE_KEY, JSON.stringify(providers));
    } catch {
      // Ignore storage errors in private mode/quota limits.
    }
  }, [providers]);

  // -- Derived numbers -------------------------------------------------------
  const servicesReady = Array.isArray(services);
  const providersReady = Array.isArray(providers);
  const allServices = useMemo(() => (Array.isArray(services) ? services : []), [services]);
  const allProviders = useMemo(() => (Array.isArray(providers) ? providers : []), [providers]);

  const enabledServices = allServices.filter((s) => Boolean(s.enabled));
  const servicesInError = enabledServices.filter((s) => s.status === 'error').length;
  const servicesOk = enabledServices.filter((s) => s.status === 'ok').length;

  const enabledProviders = allProviders.filter((p) => Boolean(p.enabled)).length;
  const healthEntries = providersHealth ? Object.values(providersHealth) : [];
  const providersHealthy = healthEntries.filter((h) => h?.status === 'healthy').length;
  const providersFailing = healthEntries.filter((h) => h?.status === 'unhealthy').length;

  const expiringCerts = certExpiry?.expiring_soon_count ?? 0;
  const totalCerts = certExpiry?.total ?? certExpiry?.certificates?.length ?? 0;
  const warnDays = certExpiry?.warn_threshold_days ?? 30;

  const todayKey = formatDate(now, 'short');
  const todayItems = Array.isArray(todayLogsResp?.items) ? todayLogsResp.items : [];
  const logsToday = todayItems.filter((log) => formatDate(log.created_at, 'short') === todayKey).length;
  const logsTodayCapped = todayItems.length >= TODAY_LOGS_SAMPLE && logsToday === todayItems.length;
  const logsTotal = stats?.logs ?? todayLogsResp?.total ?? logsResp?.total;

  const hasError = servicesError || providersError;
  /**
   * Whether anything at all arrived. The offline banner said "showing the last data received"
   * whatever had happened; on a first load in a fresh tab there is no last data, and it said
   * that over a page of cards with nothing in them.
   */
  const hasSomeData = servicesReady || providersReady || Boolean(stats);
  /**
   * A card is loading only while its request is in flight. Once that request has come back
   * and left it with nothing, the card is *unknown*: a dash, or a stated failure. Never a
   * skeleton that pulses until somebody reloads the page, and never a zero — a zero is a
   * measurement, and it reads as good news.
   */
  const servicesUnknown = !servicesReady && !stats;
  const providersUnknown = !providersReady;
  const logsUnknown = !todayLogsResp;
  /**
   * The triage list is read off the two lists themselves and never off `stats`, so it is
   * incomplete as soon as either is missing — including when the counters above found their
   * figures in `stats` and look perfectly healthy.
   */
  const attentionIncomplete = !servicesReady || !providersReady;
  const handleRetry = () => {
    refetchServices();
    refetchProviders();
  };

  // -- Needs attention -------------------------------------------------------
  const attentionItems: AttentionItem[] = [];
  if (servicesInError > 0) {
    attentionItems.push({
      id: 'services-error',
      tone: 'danger',
      icon: <AlertTriangle />,
      title: t(servicesInError === 1 ? 'dashboard.attention.services_error_one' : 'dashboard.attention.services_error_other', {
        count: formatNumber(servicesInError),
      }),
      hint: t('dashboard.attention.services_error_hint'),
      to: '/services?status=error',
    });
  }
  if (providersFailing > 0) {
    attentionItems.push({
      id: 'providers-failing',
      tone: 'danger',
      icon: <PlugZap />,
      title: t(providersFailing === 1 ? 'dashboard.attention.providers_failing_one' : 'dashboard.attention.providers_failing_other', {
        count: formatNumber(providersFailing),
      }),
      to: '/providers',
    });
  } else if (providersHealthError && enabledProviders > 0) {
    attentionItems.push({
      id: 'providers-health-unknown',
      tone: 'warning',
      icon: <PlugZap />,
      title: t('dashboard.attention.providers_health_unknown'),
      hint: t('dashboard.attention.providers_health_unknown_hint'),
      to: '/providers',
    });
  }
  if (certExpiryFailed) {
    attentionItems.push({
      id: 'certs-unknown',
      tone: 'warning',
      icon: <ShieldAlert />,
      title: t('dashboard.attention.certs_unknown'),
      hint: t('dashboard.attention.certs_unknown_hint'),
      to: '/certificates',
    });
  } else if (expiringCerts > 0) {
    attentionItems.push({
      id: 'certs-expiring',
      tone: 'warning',
      icon: <ShieldAlert />,
      title: t(expiringCerts === 1 ? 'dashboard.attention.certs_expiring_one' : 'dashboard.attention.certs_expiring_other', {
        count: formatNumber(expiringCerts),
        days: formatNumber(warnDays),
      }),
      hint: t('dashboard.attention.certs_expiring_hint'),
      to: '/certificates',
    });
  }
  if (authStatus?.auth_mode === 'open') {
    attentionItems.push({
      id: 'open-access',
      tone: 'warning',
      icon: <LockOpen />,
      title: t('security.open_access.title'),
      hint: t('security.open_access.body'),
      to: '/settings?tab=apikeys',
      actionLabel: t('security.open_access.action'),
    });
  }
  if (providersReady && allProviders.length === 0) {
    attentionItems.push({
      id: 'no-providers',
      tone: 'info',
      icon: <Plug />,
      title: t('dashboard.attention.no_providers'),
      hint: t('dashboard.attention.no_providers_hint'),
      to: '/providers?new=1',
    });
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 pb-8 animate-in fade-in animate-duration-300">
      <PageHeader
        eyebrow={t('nav.dashboard')}
        icon={<LayoutDashboard />}
        title={t(greetingKey(hour))}
        description={t('dashboard.description')}
        meta={
          <>
            <StatusPill />
            {servicesUpdatedAt > 0 && (
              <span className="tabular-nums">{t('dashboard.meta.updated', { time: formatTime(servicesUpdatedAt) })}</span>
            )}
          </>
        }
        actions={
          <>
            <Button variant="outline" leftIcon={<Plug />} onClick={() => setIsCreateProviderOpen(true)}>
              {t('dashboard.quick_actions.add_integration')}
            </Button>
            <Button variant="primary" leftIcon={<Plus />} onClick={() => setIsCreateServiceOpen(true)}>
              {t('dashboard.actions.route_service')}
            </Button>
          </>
        }
      />

      {hasError && (
        <InlineAlert
          tone="danger"
          title={t('dashboard.offline.title')}
          action={
            <Button variant="outline" size="sm" leftIcon={<RefreshCw />} onClick={handleRetry}>
              {t('ui.error.retry')}
            </Button>
          }
        >
          {hasSomeData ? t('dashboard.offline.body') : t('dashboard.offline.body_empty')}
        </InlineAlert>
      )}

      <StatRow
        loading={{
          services: servicesUnknown && !servicesError,
          providers: providersUnknown && !providersError,
          certificates: !certExpiry && !certExpiryFailed,
          logs: logsUnknown && !todayLogsError,
        }}
        services={{
          total: stats?.services ?? allServices.length,
          enabled: servicesReady ? enabledServices.length : undefined,
          ok: stats?.services_ok ?? servicesOk,
          error: stats?.services_error ?? servicesInError,
          failed: servicesUnknown,
        }}
        providers={{
          total: allProviders.length,
          enabled: enabledProviders,
          healthy: providersHealthy,
          failed: providersUnknown,
        }}
        certificates={{
          expiring: expiringCerts,
          total: totalCerts,
          thresholdDays: warnDays,
          failed: certExpiryFailed,
        }}
        logs={{ today: logsToday, todayCapped: logsTodayCapped, total: logsTotal, failed: logsUnknown }}
      />

      <NeedsAttention
        items={attentionItems}
        loading={(!servicesReady && !servicesError) || (!providersReady && !providersError)}
        incomplete={attentionIncomplete}
      />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-3">
          <RecentActivity
            logs={Array.isArray(logsResp?.items) ? logsResp.items : undefined}
            loading={logsPending && !logsResp}
            error={logsError && !logsResp}
            onRetry={() => refetchLogs()}
            now={now}
          />
        </div>
        <div className="space-y-6 xl:col-span-2">
          <IntegrationsGlance
            providers={providersReady ? allProviders : undefined}
            loading={providersUnknown && !providersError}
            error={providersUnknown && providersError}
            onRetry={() => refetchProviders()}
            health={providersHealth}
            healthError={providersHealthError}
            types={providerTypes}
            onAddProvider={() => setIsCreateProviderOpen(true)}
          />
          <QuickActions
            onCreateService={() => setIsCreateServiceOpen(true)}
            onAddProvider={() => setIsCreateProviderOpen(true)}
          />
        </div>
      </div>

      <ExposeModal isOpen={isCreateServiceOpen} onClose={() => setIsCreateServiceOpen(false)} mode="create" />
      <ProviderModal isOpen={isCreateProviderOpen} onClose={() => setIsCreateProviderOpen(false)} />
    </div>
  );
}
