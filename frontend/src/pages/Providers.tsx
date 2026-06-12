import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";
import { Settings, Activity, KeySquare, Plus, AlertCircle, RefreshCw, X, ShieldAlert, Trash2, Loader2, PlayCircle, Database } from "lucide-react";
import { api } from "@/api/client";
import { ProviderModal } from "@/components/features/ProviderModal";
import { useConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ProviderLogo } from "@/components/ui/ProviderLogos";
import { useT } from "@/i18n";
import type { Provider, TunnelHealthResponse, TunnelHealthItem, SyncResult } from "@/types/api";

interface ProviderDiagnostics {
  ok?: boolean;
  provider?: string;
  validation?: {
    checks?: Array<{ name: string; ok: boolean; blocking: boolean; detail?: string }>;
    warnings?: string[];
  };
  health?: { ok?: boolean; status?: string; error?: string };
  testedAt?: number;
}

interface ProviderHealthState {
  ok?: boolean;
  status?: string;
  error?: string | null;
}

interface OperationalStatus {
  labelKey: string;
  textColor: string;
  dotColor: string;
}

export function Providers() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const DIAGNOSTIC_TTL_MS = 30 * 60 * 1000;
  const DIAGNOSTICS_STORAGE_KEY = "vauxtra.providers.manual-diagnostics.v1";
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [focusFilter, setFocusFilter] = useState<'all' | 'issues' | 'healthy'>('all');
  const [providerDiagnostics, setProviderDiagnosticsRaw] = useState<Record<number, ProviderDiagnostics>>({});

  const setProviderDiagnostics = (updater: (prev: Record<number, ProviderDiagnostics>) => Record<number, ProviderDiagnostics>) => {
    setProviderDiagnosticsRaw((prev) => updater(prev));
  };
  const [testingId, setTestingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [validatingId, setValidatingId] = useState<number | null>(null);

  const { data: providers, isLoading, isFetching: isFetchingProviders, refetch: refetchProviders } = useQuery({
    queryKey: ['providers'],
    queryFn: () => api.get('/providers'),
  });

  const { data: providerTypes, refetch: refetchProviderTypes } = useQuery<Record<string, Record<string, unknown>>>({
    queryKey: ['provider-types'],
    queryFn: () => api.get('/providers/types'),
  });

  const { data: tunnelHealth, refetch: refetchTunnelHealth } = useQuery<TunnelHealthResponse>({
    queryKey: ['providers-tunnel-health'],
    queryFn: () => api.get('/providers/tunnels/health'),
    refetchInterval: 30000,
  });

  // Auto-fetch health for all enabled providers on page load
  const { data: allHealth, refetch: refetchAllHealth } = useQuery<Record<string, ProviderHealthState> | { items: Record<string, ProviderHealthState> }>({
    queryKey: ['providers-health'],
    queryFn: () => api.get('/providers/health'),
    refetchInterval: 60000,
  });

  const allHealthItems = (
    allHealth && typeof allHealth === 'object' && 'items' in allHealth
      ? allHealth.items
      : allHealth
  ) || {};

  const allHealthById = Object.fromEntries(
    Object.entries(allHealthItems).map(([id, state]) => [Number(id), state || {}]),
  ) as Record<number, ProviderHealthState>;

  useEffect(() => {
    try {
      const raw = localStorage.getItem(DIAGNOSTICS_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Record<string, ProviderDiagnostics>;
      const now = Date.now();
      const restored = Object.fromEntries(
        Object.entries(parsed || {}).filter(([, diag]) => {
          const testedAt = Number(diag?.testedAt || 0);
          return testedAt > 0 && now - testedAt <= DIAGNOSTIC_TTL_MS;
        }).map(([id, diag]) => [Number(id), diag]),
      ) as Record<number, ProviderDiagnostics>;
      setProviderDiagnosticsRaw(restored);
    } catch {
      localStorage.removeItem(DIAGNOSTICS_STORAGE_KEY);
    }
  }, [DIAGNOSTIC_TTL_MS, DIAGNOSTICS_STORAGE_KEY]);

  useEffect(() => {
    try {
      localStorage.setItem(DIAGNOSTICS_STORAGE_KEY, JSON.stringify(providerDiagnostics));
    } catch {
      // Ignore storage errors (private mode/quota).
    }
  }, [providerDiagnostics]);

  const handleRefresh = async () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    try {
      await Promise.all([
        refetchProviders(),
        refetchProviderTypes(),
        refetchTunnelHealth(),
        refetchAllHealth(),
      ]);

      const enabledProviders = providersList.filter((p: Provider) => Boolean(p.enabled));
      if (enabledProviders.length === 0) {
        toast.success(t('providers.refresh.success_no_enabled'));
        return;
      }

      const diagnosticsEntries = await Promise.all(
        enabledProviders.map(async (provider: Provider) => {
          try {
            const data = await (api.post(`/providers/${provider.id}/test`) as Promise<ProviderDiagnostics>);
            return [provider.id, { ...data, testedAt: Date.now() }] as const;
          } catch (error: unknown) {
            const err = error as { response?: { data?: { detail?: string } } };
            const detail = err?.response?.data?.detail || t('providers.toast.connection_failed');
            return [
              provider.id,
              {
                ok: false,
                provider: provider.name,
                health: { ok: false, status: 'error', error: detail },
                testedAt: Date.now(),
              } satisfies ProviderDiagnostics,
            ] as const;
          }
        }),
      );

      setProviderDiagnostics((prev) => ({
        ...prev,
        ...Object.fromEntries(diagnosticsEntries),
      }));

      const failed = diagnosticsEntries.filter(([, data]) => !data?.ok).length;
      if (failed === 0) {
        toast.success(t('providers.refresh.success_all_passed'));
      } else {
        toast.error(t('providers.refresh.failed_count', { failed, total: enabledProviders.length }));
      }
    } catch {
      toast.error(t('providers.refresh.failed'));
    } finally {
      setIsRefreshing(false);
    }
  };

  const testConnection = useMutation({
    mutationFn: (id: number) => { setTestingId(id); return api.post(`/providers/${id}/test`) as Promise<ProviderDiagnostics>; },
    onSuccess: (data: ProviderDiagnostics, id) => {
      const ok = Boolean(data?.ok);
      const providerName = data?.provider ? ` (${data.provider})` : "";

      setProviderDiagnostics((prev) => ({
        ...prev,
        [id]: { ...data, testedAt: Date.now() },
      }));

      if (ok) {
        toast.success(`Connection test successful${providerName}!`);
      } else {
        toast.error(`Connection failed${providerName}`);
      }

      setTestingId(null);
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
    onError: (error: { response?: { data?: { detail?: string } } }, id) => {
      const msg = error?.response?.data?.detail || "Connection failed";
      toast.error(msg);
      setProviderDiagnostics((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          health: { ok: false, status: 'error', error: msg },
          testedAt: Date.now(),
        },
      }));
      setTestingId(null);
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    }
  });

  const validateProvider = useMutation({
    mutationFn: (id: number) => { setValidatingId(id); return api.post(`/providers/${id}/validate`, { write_probe: false }) as Promise<ProviderDiagnostics>; },
    onSuccess: (data: ProviderDiagnostics, id) => {
      setValidatingId(null);
      setProviderDiagnostics((prev) => ({
        ...prev,
        [id]: { ...data, testedAt: Date.now() },
      }));
      if (data?.ok) {
        toast.success('Provider validation OK');
      } else {
        toast.error('Provider validation failed');
      }
      queryClient.invalidateQueries({ queryKey: ['providers-tunnel-health'] });
    },
    onError: (error: { response?: { data?: { detail?: string } } }) => {
      setValidatingId(null);
      toast.error(error?.response?.data?.detail || 'Validation failed');
    },
  });

  const updateProvider = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.put(`/providers/${data.id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      setEditingProvider(null);
    }
  });

  const deleteProvider = useMutation({
    mutationFn: ({ id, force }: { id: number; force?: boolean }) =>
      api.delete(`/providers/${id}${force ? '?force=true' : ''}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['services'] });
      toast.success('Provider deleted');
    },
  });

  const syncProviders = useMutation({
    mutationFn: () => api.post<SyncResult>('/services/sync'),
    onSuccess: (data) => {
      setSyncResult(data);
      const discovered = (data.proxy_hosts?.length || 0) + (data.dns_rewrites?.length || 0);
      toast.success(`Scan complete: ${discovered} route(s) discovered`);
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(err?.response?.data?.detail || 'Provider scan failed');
    },
  });

  const importFromScan = useMutation({
    mutationFn: (payload: SyncResult) => api.post<{ imported: number; errors: string[] }>('/services/import', payload),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      if ((data.imported || 0) > 0) {
        toast.success(`Imported ${data.imported} service(s)`);
      } else {
        toast.success('No new services to import');
      }
      if ((data.errors || []).length > 0) {
        toast.error(`${data.errors.length} service(s) skipped or failed`);
      }
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(err?.response?.data?.detail || 'Import failed');
    },
  });

  const handleDeleteProvider = async (id: number, name: string) => {
    if (!await confirm({
      title: 'Delete provider',
      message: `Are you sure you want to delete "${name}"?`,
      confirmLabel: 'Delete',
      variant: 'danger',
    })) return;
    setDeletingId(id);
    try {
      await deleteProvider.mutateAsync({ id });
      setDeletingId(null);
    } catch (error: unknown) {
      const err = error as { response?: { status?: number; data?: { detail?: { message?: string; services?: Array<{ fqdn: string }> } | string } } };
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 409 && typeof detail === 'object' && detail?.services) {
        const count = detail.services.length;
        const list = detail.services.slice(0, 5).map((s: { fqdn: string }) => s.fqdn).join('\n• ');
        const suffix = count > 5 ? `\n… and ${count - 5} more` : '';
        if (await confirm({
          title: 'Provider has dependencies',
          message: `${count} service(s) depend on this provider:\n• ${list}${suffix}\n\nDelete anyway? Their provider link will be removed.`,
          confirmLabel: 'Delete anyway',
          variant: 'warning',
        })) {
          deleteProvider.mutate({ id, force: true }, { onSettled: () => setDeletingId(null) });
        } else {
          setDeletingId(null);
        }
      } else {
        const msg = typeof detail === 'object' ? detail?.message : detail;
        toast.error(msg || 'Delete failed');
        setDeletingId(null);
      }
    }
  };


  const getProviderIcon = (type: string) => {
    const key = type?.toLowerCase() || '';
    return <ProviderLogo type={key} className="w-5 h-5 text-primary" />;
  };

  const getStatusColor = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'healthy':
      case 'online': return 'bg-emerald-500';
      case 'offline':
      case 'disabled': return 'bg-muted-foreground/40';
      case 'error': return 'bg-destructive';
      case 'syncing': return 'bg-secondary-foreground animate-pulse';
      default: return 'bg-muted-foreground/40';
    }
  };

  const getHealthScore = (provider: Provider): { score: number; label: string; color: string; reason?: string } => {
    const diagRaw = providerDiagnostics[provider.id];
    const diagFresh = Boolean(diagRaw?.testedAt) && Date.now() - Number(diagRaw?.testedAt || 0) <= DIAGNOSTIC_TTL_MS;
    const diag = diagFresh ? diagRaw : undefined;
    const tunnelH = tunnelHealthById[provider.id];
    const autoHealth = allHealthById[provider.id];
    if (!diag && !tunnelH && !autoHealth) return { score: -1, label: 'Unknown', color: 'text-muted-foreground' };

    let score = 100;
    let reason: string | undefined;
    // Connection test result (manual)
    if (diag) {
      if (!diag.ok) { score -= 50; reason = diag.health?.error || 'Connection failed'; }
      if (diag.validation?.checks) {
        const blocking = diag.validation.checks.filter(c => c.blocking && !c.ok);
        const warnings = diag.validation.checks.filter(c => !c.blocking && !c.ok).length;
        score -= blocking.length * 25;
        score -= warnings * 5;
        if (blocking.length > 0 && !reason) reason = blocking[0].detail || `${blocking.length} blocking check(s) failed`;
      }
      if (diag.health && !diag.health.ok) { score -= 30; reason = reason || diag.health.error || diag.health.status || 'Health check failed'; }
    } else if (autoHealth) {
      const status = String(autoHealth.status || '').toLowerCase();
      const isHealthy = autoHealth.ok ?? status === 'healthy';
      if (!isHealthy) { score = 20; reason = autoHealth.error || status || 'Unhealthy'; }
    }
    // Tunnel health
    if (tunnelH) {
      const status = String((tunnelH as Record<string, unknown>)?.status || '');
      if (status === 'healthy') score = Math.max(score, 90);
      else if (status === 'degraded') { score = Math.min(score, 60); reason = reason || 'Degraded tunnel'; }
      else if (status === 'down') { score = Math.min(score, 20); reason = reason || 'Tunnel is down'; }
    }
    if (!provider.enabled) score = Math.min(score, 30);

    score = Math.max(0, Math.min(100, score));
    if (score >= 80) return { score, label: 'OK', color: 'text-emerald-600 dark:text-emerald-400' };
    if (score >= 50) return { score, label: 'Degraded', color: 'text-yellow-600 dark:text-yellow-400', reason };
    return { score, label: 'Error', color: 'text-destructive', reason };
  };

  const getOperationalStatus = (provider: Provider): OperationalStatus => {
    if (!provider.enabled) {
      return {
        labelKey: 'providers.status.disabled',
        textColor: 'text-muted-foreground',
        dotColor: 'text-muted-foreground',
      };
    }

    const health = getHealthScore(provider);
    if (health.score < 0) {
      return {
        labelKey: 'providers.status.active',
        textColor: 'text-emerald-600 dark:text-emerald-400',
        dotColor: 'text-emerald-500',
      };
    }
    if (health.score >= 80) {
      return {
        labelKey: 'providers.status.active',
        textColor: 'text-emerald-600 dark:text-emerald-400',
        dotColor: 'text-emerald-500',
      };
    }
    if (health.score >= 50) {
      return {
        labelKey: 'providers.status.degraded',
        textColor: 'text-yellow-600 dark:text-yellow-400',
        dotColor: 'text-yellow-500',
      };
    }
    return {
      labelKey: 'providers.status.error',
      textColor: 'text-destructive',
      dotColor: 'text-destructive',
    };
  };

  const getProviderSeverity = (provider: Provider): 'healthy' | 'degraded' | 'error' | 'disabled' | 'unknown' => {
    if (!provider.enabled) return 'disabled';
    const health = getHealthScore(provider);
    if (health.score < 0) return 'unknown';
    if (health.score >= 80) return 'healthy';
    if (health.score >= 50) return 'degraded';
    return 'error';
  };

  const matchesFocusFilter = (provider: Provider): boolean => {
    const severity = getProviderSeverity(provider);
    if (focusFilter === 'issues') return severity === 'degraded' || severity === 'error';
    if (focusFilter === 'healthy') return severity === 'healthy';
    return true;
  };

  const cardClass = "bg-card border border-border rounded-xl shadow-sm";

  const providersList = useMemo(() => (Array.isArray(providers) ? providers : []), [providers]);
  useEffect(() => {
    if (providersList.length === 0) return;
    const ids = new Set(providersList.map((p: Provider) => Number(p.id)));
    setProviderDiagnostics((prev) =>
      Object.fromEntries(
        Object.entries(prev).filter(([id]) => ids.has(Number(id))),
      ) as Record<number, ProviderDiagnostics>,
    );
  }, [providersList]);

  const tunnelHealthItems = Array.isArray(tunnelHealth?.items) ? tunnelHealth.items : [];
  const tunnelHealthById = Object.fromEntries(
    tunnelHealthItems.map((item: TunnelHealthItem) => [Number(item.id), item.health || {}]),
  ) as Record<number, Record<string, unknown>>;
  const providerTypeMap = providerTypes || {};
  const editingTypeKey = String(editingProvider?.type || '').toLowerCase();
  const editingTypeMeta = providerTypeMap[editingTypeKey] || {};
  const editingUserLabel = String(editingTypeMeta?.user_label || 'Username');
  const editingPassLabel = String(editingTypeMeta?.pass_label || 'Secret');

  const hasCapability = (provider: Provider, capability: 'proxy' | 'dns'): boolean => {
    const typeKey = String(provider?.type || '').toLowerCase();
    const meta = providerTypeMap[typeKey] || {};
    const caps = (meta?.capabilities || {}) as Record<string, unknown>;

    if (Object.prototype.hasOwnProperty.call(caps, capability)) {
      return Boolean(caps[capability]);
    }
    if (capability === 'proxy') {
      return meta?.category === 'proxy';
    }
    return meta?.category === 'dns';
  };

  const isTunnelProvider = (provider: Provider): boolean => String(provider.type || '').toLowerCase() === 'cloudflare_tunnel';

  const reverseProviders = providersList.filter((p: Provider) => hasCapability(p, 'proxy') && !isTunnelProvider(p));
  const dnsProviders = providersList.filter((p: Provider) => hasCapability(p, 'dns'));
  const tunnelProviders = providersList.filter((p: Provider) => isTunnelProvider(p));
  const otherProviders = providersList.filter((p: Provider) => 
    !hasCapability(p, 'proxy') && 
    !hasCapability(p, 'dns') && 
    !isTunnelProvider(p)
  );

  const providerSections = [
    { id: 'reverse', title: 'Reverse Proxies', items: reverseProviders },
    { id: 'tunnel', title: 'Tunnels', items: tunnelProviders },
    { id: 'dns', title: 'DNS Providers', items: dnsProviders },
    { id: 'other', title: 'Other', items: otherProviders },
  ].filter((section) => section.items.length > 0);
  const visibleProviderSections = providerSections
    .map((section) => ({ ...section, items: section.items.filter(matchesFocusFilter) }))
    .filter((section) => section.items.length > 0);
  const issueCount = providersList.filter((p: Provider) => {
    const severity = getProviderSeverity(p);
    return severity === 'degraded' || severity === 'error';
  }).length;
  const healthyCount = providersList.filter((p: Provider) => getProviderSeverity(p) === 'healthy').length;
  const lastManualCheckAt = Object.values(providerDiagnostics)
    .map((diag) => Number(diag?.testedAt || 0))
    .filter((ts) => ts > 0)
    .reduce((max, ts) => Math.max(max, ts), 0);
  const hasFreshManualCheck = lastManualCheckAt > 0 && (Date.now() - lastManualCheckAt <= DIAGNOSTIC_TTL_MS);
  const secondaryBtnClass = "inline-flex items-center gap-2 h-9 px-3.5 text-xs rounded-lg font-semibold border border-border bg-background hover:bg-accent text-foreground disabled:opacity-60 disabled:cursor-not-allowed";
  const primaryBtnClass = "inline-flex items-center gap-2 h-9 px-3.5 text-xs rounded-lg font-semibold bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed";
  const iconBtnClass = "inline-flex items-center justify-center h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg transition-colors border border-transparent hover:border-border disabled:opacity-60 disabled:cursor-not-allowed";
  const discoveredCount = (syncResult?.proxy_hosts?.length || 0) + (syncResult?.dns_rewrites?.length || 0);
  const discoveredProxyCount = syncResult?.proxy_hosts?.length || 0;
  const discoveredDnsCount = syncResult?.dns_rewrites?.length || 0;

  if (isLoading) {
    return (
        <div className="flex flex-col space-y-4 max-w-7xl mx-auto pt-10">
          <div className="h-8 w-48 bg-muted rounded animate-pulse mb-4"></div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="h-48 bg-card rounded-xl shadow-sm border border-border animate-pulse"></div>
              <div className="h-48 bg-card rounded-xl shadow-sm border border-border animate-pulse"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-8 animate-in fade-in duration-200">
      
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Integrations</h1>
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            {hasFreshManualCheck ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5">
                Last check {new Date(lastManualCheckAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5">
                Last check: none
              </span>
            )}
            <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5">
              Issues {issueCount}
            </span>
          </div>
        </div>
        
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleRefresh}
            disabled={isRefreshing}
            title={t('providers.refresh.button')}
            aria-label={t('providers.refresh.button')}
            className={secondaryBtnClass}
          >
            <PlayCircle className={`w-4 h-4 ${isRefreshing || isFetchingProviders ? 'animate-pulse' : ''}`} />
            <span>{t('providers.refresh.button')}</span>
          </button>
          <button 
            onClick={() => setIsModalOpen(true)}
            className={`${primaryBtnClass} shadow-sm focus:ring-2 focus:ring-primary/30 outline-none`}
          >
            <Plus className="w-4 h-4" />
            Add connection
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => setFocusFilter('all')}
          className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${focusFilter === 'all' ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground hover:bg-muted'}`}
        >
          All <span className="opacity-60">{providersList.length}</span>
        </button>
        <button
          onClick={() => setFocusFilter('issues')}
          className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${focusFilter === 'issues' ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground hover:bg-muted'}`}
        >
          Focus issues <span className="opacity-60">{issueCount}</span>
        </button>
        <button
          onClick={() => setFocusFilter('healthy')}
          className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${focusFilter === 'healthy' ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground hover:bg-muted'}`}
        >
          Healthy <span className="opacity-60">{healthyCount}</span>
        </button>
      </div>

      <div className="relative overflow-hidden rounded-xl border border-border bg-card p-5 shadow-sm">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(65%_110%_at_100%_0%,rgba(37,99,235,0.08),transparent_60%)]" />
        <div className="relative flex flex-col gap-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="space-y-1.5">
              <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Database className="w-4 h-4 text-muted-foreground" />
                Provider scan & import
              </h2>
              <p className="text-xs text-muted-foreground max-w-2xl">
                Discover routes from active providers and import only what is missing.
              </p>
            </div>
            <div className="flex flex-wrap gap-2 text-xs font-medium">
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-1 text-muted-foreground">
                Total {discoveredCount}
              </span>
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-1 text-muted-foreground">
                Proxy {discoveredProxyCount}
              </span>
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-1 text-muted-foreground">
                DNS {discoveredDnsCount}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              onClick={() => syncProviders.mutate()}
              disabled={syncProviders.isPending}
              className={secondaryBtnClass}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncProviders.isPending ? 'animate-spin' : ''}`} />
              {syncProviders.isPending ? 'Scanning providers...' : 'Scan providers'}
            </button>
            <button
              onClick={() => syncResult && importFromScan.mutate(syncResult)}
              disabled={!syncResult || discoveredCount === 0 || importFromScan.isPending}
              className={primaryBtnClass}
            >
              {importFromScan.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
              {importFromScan.isPending ? 'Importing discovered routes...' : `Import discovered (${discoveredCount})`}
            </button>
            {!syncProviders.isPending && discoveredCount === 0 && (
              <span className="text-[11px] text-muted-foreground">Run a scan to load import candidates.</span>
            )}
          </div>
        </div>
      </div>

        {providersList.length === 0 ? (
          <div className={`${cardClass} flex flex-col items-center justify-center py-24 bg-muted/30`}>
             <div className="w-12 h-12 rounded-2xl bg-card border border-border shadow-sm flex items-center justify-center mb-5">
               <KeySquare className="w-6 h-6 text-muted-foreground" />
           </div>
             <h3 className="text-base font-semibold text-foreground">No integrations found</h3>
             <p className="text-muted-foreground text-sm mt-1.5 mb-6 text-center max-w-sm">
             Connect Cloudflare, Nginx Proxy Manager, or AdGuard to start managing your routing rules.
           </p>
             <button onClick={() => setIsModalOpen(true)} className="text-sm text-primary font-semibold hover:opacity-90 transition-colors bg-card border border-border shadow-sm rounded-lg px-4 py-2">Connect Provider</button>
        </div>
      ) : visibleProviderSections.length === 0 ? (
        <div className={`${cardClass} flex flex-col items-center justify-center py-16 bg-muted/20`}>
          <h3 className="text-base font-semibold text-foreground">No integrations match this filter</h3>
          <p className="text-muted-foreground text-sm mt-1.5 mb-4 text-center max-w-sm">
            Switch filter to view all providers or run a fresh check.
          </p>
          <div className="flex items-center gap-2">
            <button className={secondaryBtnClass} onClick={() => setFocusFilter('all')}>Show all</button>
            <button className={secondaryBtnClass} onClick={handleRefresh} disabled={isRefreshing}>
              <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
              Recheck
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {visibleProviderSections.map((section) => (
            <section key={section.id} className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">{section.title}</h2>
                <span className="text-[11px] text-muted-foreground border border-border rounded-full px-2 py-0.5 bg-muted/40">
                  {section.items.length}
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {section.items.map((provider: Provider) => (
                  <div key={provider.id} className={`${cardClass} flex flex-col hover:shadow-md hover:border-foreground/20 transition-all group relative overflow-hidden`}>
                    {(() => {
                      const providerState = provider.status || (provider.enabled ? 'online' : 'disabled');
                      return (
                        <>
                          <div className={`absolute top-0 inset-x-0 h-1 transition-colors ${getStatusColor(providerState)}`}></div>

                          <div className="p-6">
                            <div className="flex items-start justify-between mb-5">
                              <div className="flex items-center gap-3">
                                  <div className="p-2.5 bg-muted rounded-xl border border-border">
                                  {getProviderIcon(provider.type)}
                                </div>
                                <div>
                                    <h3 className="font-bold text-foreground text-base leading-tight">{provider.name}</h3>
                                  <div className="flex items-center gap-1.5 mt-1">
                                    <div className={`w-1.5 h-1.5 rounded-full ${getStatusColor(providerState)}`}></div>
                                      <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{provider.type}</span>
                                  </div>
                                </div>
                              </div>

                              <div className="flex gap-1.5 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                                <button
                                  onClick={() => testConnection.mutate(provider.id)}
                                  disabled={testConnection.isPending}
                                  className={`${iconBtnClass} hover:text-primary hover:bg-primary/10 hover:border-primary/20`}
                                  title="Test connection"
                                >
                                  <RefreshCw className={`w-4 h-4 ${testingId === provider.id && testConnection.isPending ? 'animate-spin text-primary' : ''}`} />
                                </button>
                                <button
                                  onClick={() => validateProvider.mutate(Number(provider.id))}
                                  disabled={validatingId === provider.id}
                                  className={iconBtnClass}
                                  title="Validate permissions"
                                >
                                  <ShieldAlert className={`w-4 h-4 ${validatingId === provider.id ? 'animate-pulse text-primary' : ''}`} />
                                </button>
                                <button
                                  onClick={() => setEditingProvider(provider)}
                                  className={iconBtnClass}
                                  title="Edit integration"
                                >
                                  <Settings className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => handleDeleteProvider(Number(provider.id), provider.name)}
                                  disabled={deletingId === provider.id}
                                  className={`${iconBtnClass} hover:text-destructive hover:bg-destructive/10 hover:border-destructive/20`}
                                  title="Delete integration"
                                >
                                  {deletingId === provider.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                                </button>
                              </div>
                            </div>

                            <div className="space-y-3 pt-2">
                              <div className="flex items-center text-sm">
                                <span className="w-24 text-muted-foreground font-medium text-xs uppercase tracking-wider">URL</span>
                                <span className="font-mono text-foreground text-[13px] bg-muted px-2 py-0.5 rounded border border-border">
                                  {provider.url || 'Default URL'}
                                </span>
                              </div>

                              {(() => {
                                const diagnosticsRaw = providerDiagnostics[Number(provider.id)];
                                const diagnostics = diagnosticsRaw?.testedAt && (Date.now() - diagnosticsRaw.testedAt <= DIAGNOSTIC_TTL_MS)
                                  ? diagnosticsRaw
                                  : undefined;
                                const tunnelHealthState = tunnelHealthById[Number(provider.id)];
                                const autoHealthState = allHealthById[Number(provider.id)];
                                const tunnelStatus = String(tunnelHealthState?.status || '').toLowerCase();
                                const autoStatus = String(autoHealthState?.status || '').toLowerCase();
                                const autoIsHealthy = autoHealthState
                                  ? (autoHealthState.ok ?? autoStatus === 'healthy')
                                  : true;
                                const showAutoFailure = !diagnostics && (
                                  (Boolean(tunnelHealthState) && ['down', 'degraded', 'error'].includes(tunnelStatus)) ||
                                  (Boolean(autoHealthState) && !autoIsHealthy)
                                );
                                if (!diagnostics && !showAutoFailure) return null;

                                const health = (diagnostics?.health || tunnelHealthState || autoHealthState) as Record<string, unknown> | undefined;

                                const checks = Array.isArray(diagnostics?.validation?.checks)
                                  ? diagnostics.validation.checks
                                  : [];
                                const blockingFailures = checks.filter((c: { blocking?: boolean; ok?: boolean }) => c?.blocking && !c?.ok).length;
                                const warningFailures = checks.filter((c: { blocking?: boolean; ok?: boolean }) => !c?.blocking && !c?.ok).length;
                                const firstBlockingDetail = checks.find(
                                  (c: { blocking?: boolean; ok?: boolean; detail?: string }) => c?.blocking && !c?.ok,
                                )?.detail;
                                const failedDetailsRaw = checks
                                  .filter((c: { ok?: boolean; detail?: string }) => !c?.ok && Boolean(c?.detail))
                                  .map((c: { detail?: string }) => String(c.detail))
                                  .slice(0, 3);
                                const failedDetails = Array.from(new Set(failedDetailsRaw))
                                  .filter((detail) => !firstBlockingDetail || detail !== firstBlockingDetail)
                                  .slice(0, 3);
                                const warningDetailsRaw = Array.isArray(diagnostics?.validation?.warnings)
                                  ? diagnostics.validation.warnings
                                  : [];
                                const warningDetails = Array.from(new Set(warningDetailsRaw))
                                  .filter((warning) => !failedDetails.includes(warning) && (!firstBlockingDetail || warning !== firstBlockingDetail))
                                  .slice(0, 3);
                                const healthError = String(health?.error || '');
                                const showHealthError = Boolean(healthError) && !failedDetails.includes(healthError) && !warningDetails.includes(healthError);
                                const testedAtLabel = diagnostics?.testedAt
                                  ? new Date(diagnostics.testedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                                  : null;

                                return (
                                  <div className="rounded-md border border-border bg-muted/40 p-2 text-xs space-y-1">
                                    {checks.length > 0 && (
                                      <p className={blockingFailures === 0 ? 'text-primary' : 'text-destructive'}>
                                        Validation: {blockingFailures > 0
                                          ? `Action required: ${blockingFailures} blocking check(s) failed`
                                          : warningFailures > 0
                                            ? `Passed with ${warningFailures} warning(s)`
                                            : 'Passed'}
                                      </p>
                                    )}
                                    {firstBlockingDetail && (
                                      <p className="text-muted-foreground truncate" title={firstBlockingDetail}>
                                        Next step: {firstBlockingDetail}
                                      </p>
                                    )}
                                    {failedDetails.map((detail, index) => (
                                      <p key={`${provider.id}-fail-${index}`} className="text-muted-foreground truncate" title={detail}>
                                        - {detail}
                                      </p>
                                    ))}
                                    {warningDetails.map((warning: string, index: number) => (
                                      <p key={`${provider.id}-warn-${index}`} className="text-yellow-600 dark:text-yellow-400 truncate" title={warning}>
                                        - {warning}
                                      </p>
                                    ))}
                                    {testedAtLabel && (
                                      <p className="text-[10px] text-muted-foreground/70">Last manual check: {testedAtLabel}</p>
                                    )}
                                    {showHealthError && <p className="text-destructive truncate">{healthError}</p>}
                                  </div>
                                );
                              })()}
                            </div>
                          </div>

                            <div className="mt-auto border-t border-border bg-muted/30 px-6 py-3.5 flex items-center justify-between text-xs">
                              <div className="flex items-center gap-1.5 text-muted-foreground">
                                {(() => {
                                  const status = getOperationalStatus(provider);
                                  return (
                                    <>
                                      <Activity className={`w-3.5 h-3.5 ${status.dotColor}`} />
                                      <span className={`font-medium ${status.textColor}`}>{t(status.labelKey)}</span>
                                    </>
                                  );
                                })()}
                              </div>
                            <div className="flex items-center gap-2">
                              {(() => {
                                const health = getHealthScore(provider);
                                const diag = providerDiagnostics[provider.id];
                                const testedAt = diag?.testedAt;
                                const ageLabel = testedAt
                                  ? new Date(testedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                                  : null;
                                if (health.score < 0) return null;
                                return (
                                  <div className="flex items-center gap-1.5">
                                    <span className={`text-xs font-semibold ${health.color}`}>
                                      {health.label}
                                    </span>
                                    {health.reason && (
                                      <span className="text-xs text-muted-foreground truncate max-w-[140px]" title={health.reason}>
                                        — {health.reason}
                                      </span>
                                    )}
                                    {ageLabel && (
                                      <span className="text-[10px] text-muted-foreground/60">@ {ageLabel}</span>
                                    )}
                                  </div>
                                );
                              })()}
                            {provider.error_message && (
                              <div className="flex items-center gap-1.5 text-destructive font-medium bg-destructive/10 px-2 py-1 rounded-md border border-destructive/20">
                                <AlertCircle className="w-3.5 h-3.5" />
                                <span className="truncate max-w-[120px]">Error occurred</span>
                              </div>
                            )}
                            </div>
                          </div>
                        </>
                      );
                    })()}
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {/* Connection Modal */}
      <ProviderModal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} />
      
      {/* Basic Editor Stub to match Stripe style */}
      {editingProvider && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 backdrop-blur-sm p-4 animate-in fade-in duration-200">
            <form 
              onSubmit={(e) => {
                e.preventDefault();
                const fd = new FormData(e.currentTarget);
                const extra: Record<string, string> = {};
                if (editingTypeKey === 'cloudflare_tunnel') {
                  extra.tunnel_id = String(fd.get('tunnel_id') || '').trim();
                }
                updateProvider.mutate({
                   id: editingProvider.id,
                   name: fd.get("name"),
                   url: fd.get("url"),
                   username: fd.get("username") || undefined,
                  password: fd.get("password") || undefined,
                  extra,
                });
              }}
              className="bg-card border border-border rounded-xl shadow-2xl max-w-xl w-full flex flex-col font-sans animate-in zoom-in-95 duration-200"
            >
              
              {/* Header */}
                <div className="flex items-center justify-between px-6 py-5 border-b border-border">
                 <div className="flex items-center gap-3">
                      <div className="p-2 bg-primary/10 border border-primary/20 rounded-lg">
                        <Settings className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                      <h2 className="text-lg font-bold text-foreground">Connection Settings</h2>
                      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mt-0.5">Edit {editingProvider.name}</p>
                    </div>
                 </div>
                 <button 
                   type="button"
                   onClick={() => setEditingProvider(null)}
                   className="p-2 text-muted-foreground hover:text-foreground bg-muted hover:bg-accent rounded-lg transition-colors border border-transparent hover:border-border"
                 >
                   <X className="w-4 h-4" />
                 </button>
              </div>
              
              {/* Content */}
              <div className="p-8 space-y-5">
                <div>
                     <h3 className="text-[15px] font-bold text-foreground mb-1">Configuration</h3>
                     <p className="text-sm text-muted-foreground">Update credentials for this provider.</p>
                </div>

                <div>
                    <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Display Name</label>
                  <input 
                    name="name"
                    type="text"
                    defaultValue={editingProvider.name}
                      className="w-full bg-input border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm"
                  />
                </div>

                {editingProvider.type?.toLowerCase() !== 'cloudflare' && (
                  <div>
                      <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">URL / Host</label>
                    <input 
                      name="url"
                      type="url"
                      defaultValue={editingProvider.url}
                        className="w-full bg-input border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm font-mono"
                    />
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">{editingUserLabel}</label>
                    <input 
                      name="username"
                      type="text"
                      defaultValue={editingProvider.username || ''}
                      className="w-full bg-input border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">New {editingPassLabel} (optional)</label>
                    <input 
                      name="password"
                      type="password"
                      placeholder="Leave blank to keep same"
                      className="w-full bg-input border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm font-mono placeholder:text-muted-foreground"
                    />
                  </div>
                </div>

                {editingTypeKey === 'cloudflare_tunnel' && (
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Tunnel ID</label>
                    <input
                      name="tunnel_id"
                      type="text"
                      defaultValue={String(editingProvider?.extra?.tunnel_id || '')}
                      placeholder="UUID of your tunnel"
                      className="w-full bg-input border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm font-mono placeholder:text-muted-foreground"
                    />
                  </div>
                )}

              </div>

              {/* Footer */}
              <div className="bg-muted/30 px-8 py-5 border-t border-border flex items-center justify-between rounded-b-xl">
                 <button 
                   type="button"
                   onClick={() => setEditingProvider(null)}
                   className="px-5 py-2.5 hover:bg-accent bg-card border border-border text-foreground text-sm rounded-lg font-semibold transition-colors shadow-sm"
                 >
                   Cancel
                 </button>
                 <button 
                   type="submit"
                   disabled={updateProvider.isPending}
                   className={`px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm ${updateProvider.isPending ? 'opacity-50 cursor-not-allowed' : ''}`}
                 >
                   {updateProvider.isPending ? 'Saving...' : 'Save Changes'}
                 </button>
              </div>
            </form>
         </div>
      )}

      {ConfirmDialogElement}
    </div>
  );
}
