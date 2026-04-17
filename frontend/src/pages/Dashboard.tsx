import {
  Globe,
  Server,
  ShieldCheck,
  ArrowRight,
  Activity,
  AlertTriangle,
  ShieldAlert,
  RefreshCw,
  WifiOff,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { Link } from 'react-router-dom';
import type { Service, Provider, CertificateExpiryResponse, CertificateExpiry } from '@/types/api';

type LogItem = { id: number; level: string; message: string; created_at: string };
type LogsResponse = { items: LogItem[]; total: number };

export function Dashboard() {
  const { data: services, isError: servicesError, refetch: refetchServices } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
    refetchInterval: 30000,
  });

  const { data: providers, isError: providersError, refetch: refetchProviders } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
    refetchInterval: 60000,
  });

  const { data: certExpiry } = useQuery<CertificateExpiryResponse>({
    queryKey: ['certificates-expiry'],
    queryFn: () =>
      api.get<CertificateExpiryResponse>('/certificates/expiry').catch(
        (): CertificateExpiryResponse => ({ certificates: [], total: 0, expiring_soon_count: 0, warn_threshold_days: 30 }),
      ),
    refetchInterval: 5 * 60 * 1000,
  });

  const { data: logsResp } = useQuery<LogsResponse>({
    queryKey: ['logs', 'dashboard'],
    queryFn: () => api.get<LogsResponse>('/logs?per_page=6'),
    refetchInterval: 15000,
  });

  const hasError = servicesError || providersError;
  const handleRetry = () => {
    refetchServices();
    refetchProviders();
  };

  const allServices = services ?? [];
  const enabledServices = allServices.filter((s) => s.enabled);
  const errorServicesCount = enabledServices.filter((s) => s.status === 'error').length;
  const okServicesCount = enabledServices.filter((s) => s.status === 'ok').length;
  const activeProvidersCount = providers?.filter((p) => p.enabled).length ?? 0;
  const totalProvidersCount = providers?.length ?? 0;
  const expiringSoonCount = certExpiry?.expiring_soon_count ?? 0;
  const totalCerts = certExpiry?.certificates.length ?? 0;
  const logs = Array.isArray(logsResp?.items) ? logsResp.items : [];

  // Certs sorted by urgency (lowest days_remaining first)
  const urgentCerts: CertificateExpiry[] = (certExpiry?.certificates ?? [])
    .filter((c) => c.expiring_soon || c.expired)
    .sort((a, b) => (a.days_remaining ?? 999) - (b.days_remaining ?? 999))
    .slice(0, 5);

  const getProviderName = (id: number | null | undefined): string => {
    if (!providers || id == null) return '—';
    const p = providers.find((prov) => prov.id === id);
    return p ? p.name : '—';
  };

  const formatAge = (dateRaw: string): string => {
    const ts = Date.parse(dateRaw.replace(' ', 'T'));
    if (!Number.isFinite(ts)) return dateRaw;
    const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (deltaSec < 60) return `${deltaSec}s ago`;
    if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
    if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
    return `${Math.floor(deltaSec / 86400)}d ago`;
  };

  const logDot = (level: string) => {
    if (level === 'error') return 'bg-destructive';
    if (level === 'ok') return 'bg-emerald-500';
    if (level === 'warning') return 'bg-yellow-500';
    return 'bg-blue-500';
  };

  return (
    <div className="space-y-5 pb-8 animate-in fade-in duration-200">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Overview</h1>
      </div>

      {/* Backend connectivity error */}
      {hasError && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-lg border border-destructive/30 bg-destructive/5 text-destructive text-sm">
          <WifiOff className="w-4 h-4 shrink-0" />
          <span className="font-medium">Unable to reach the Vauxtra backend. Data may be stale.</span>
          <button onClick={handleRetry} className="ml-auto flex items-center gap-1.5 text-xs font-semibold hover:underline">
            <RefreshCw className="w-3.5 h-3.5" /> Retry
          </button>
        </div>
      )}

      {/* Alert banners — compact */}
      {(errorServicesCount > 0 || expiringSoonCount > 0) && (
        <div className="flex flex-wrap gap-2">
          {errorServicesCount > 0 && (
            <Link to="/monitoring" className="flex items-center gap-2 px-3 py-2 rounded-lg border border-destructive/30 bg-destructive/5 text-destructive text-xs font-semibold hover:bg-destructive/10 transition-colors">
              <AlertTriangle className="w-3.5 h-3.5" />
              {errorServicesCount} route{errorServicesCount > 1 ? 's' : ''} in error
            </Link>
          )}
          {expiringSoonCount > 0 && (
            <Link to="/certificates" className="flex items-center gap-2 px-3 py-2 rounded-lg border border-yellow-500/30 bg-yellow-500/5 text-yellow-600 dark:text-yellow-400 text-xs font-semibold hover:bg-yellow-500/10 transition-colors">
              <ShieldAlert className="w-3.5 h-3.5" />
              {expiringSoonCount} cert{expiringSoonCount > 1 ? 's' : ''} expiring soon
            </Link>
          )}
        </div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Link to="/services" className="bg-card border border-border rounded-lg p-4 hover:border-foreground/20 transition-colors group">
          <div className="flex items-center justify-between mb-2">
            <Globe className="w-4 h-4 text-muted-foreground" />
            {errorServicesCount > 0 && <span className="w-2 h-2 rounded-full bg-destructive" />}
          </div>
          <p className="text-2xl font-bold text-foreground">{enabledServices.length}<span className="text-sm font-normal text-muted-foreground ml-1">/ {allServices.length}</span></p>
          <p className="text-xs text-muted-foreground mt-0.5">Active endpoints</p>
        </Link>

        <Link to="/monitoring" className="bg-card border border-border rounded-lg p-4 hover:border-foreground/20 transition-colors group">
          <div className="flex items-center justify-between mb-2">
            <Activity className="w-4 h-4 text-muted-foreground" />
            <span className={`text-xs font-mono font-semibold ${okServicesCount === enabledServices.length && enabledServices.length > 0 ? 'text-emerald-600 dark:text-emerald-400' : errorServicesCount > 0 ? 'text-destructive' : 'text-muted-foreground'}`}>
              {enabledServices.length > 0 ? `${Math.round((okServicesCount / enabledServices.length) * 100)}%` : '—'}
            </span>
          </div>
          <p className="text-2xl font-bold text-foreground">{okServicesCount}<span className="text-sm font-normal text-muted-foreground ml-1">ok</span></p>
          <p className="text-xs text-muted-foreground mt-0.5">Route health</p>
        </Link>

        <Link to="/providers" className="bg-card border border-border rounded-lg p-4 hover:border-foreground/20 transition-colors group">
          <div className="flex items-center justify-between mb-2">
            <Server className="w-4 h-4 text-muted-foreground" />
          </div>
          <p className="text-2xl font-bold text-foreground">{activeProvidersCount}<span className="text-sm font-normal text-muted-foreground ml-1">/ {totalProvidersCount}</span></p>
          <p className="text-xs text-muted-foreground mt-0.5">Integrations</p>
        </Link>

        <Link to="/certificates" className="bg-card border border-border rounded-lg p-4 hover:border-foreground/20 transition-colors group">
          <div className="flex items-center justify-between mb-2">
            <ShieldCheck className="w-4 h-4 text-muted-foreground" />
            {expiringSoonCount > 0 && <span className="w-2 h-2 rounded-full bg-yellow-500" />}
          </div>
          <p className="text-2xl font-bold text-foreground">{totalCerts}</p>
          <p className="text-xs text-muted-foreground mt-0.5">Certificates</p>
        </Link>
      </div>

      {/* Main content */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Endpoints table */}
        <div className="lg:col-span-3 bg-card border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h3 className="text-sm font-semibold text-foreground">Endpoints</h3>
            <Link to="/services" className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
              All <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
          {allServices.length > 0 ? (
            <div className="divide-y divide-border/60">
              {allServices.slice(0, 8).map((srv) => {
                const fqdn = srv.subdomain ? `${srv.subdomain}.${srv.domain}` : srv.domain;
                return (
                  <div key={srv.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-accent/50 transition-colors text-sm">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${srv.enabled ? (srv.status === 'ok' ? 'bg-emerald-500' : srv.status === 'error' ? 'bg-destructive' : 'bg-yellow-500') : 'bg-muted-foreground/30'}`} />
                    <span className="font-medium text-foreground truncate min-w-0 flex-1">{fqdn}</span>
                    <span className="text-xs text-muted-foreground font-mono shrink-0 hidden sm:block">{srv.target_ip}:{srv.target_port}</span>
                    <span className="text-[11px] text-muted-foreground shrink-0">{getProviderName(srv.proxy_provider_id)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No endpoints configured.{' '}
              <Link to="/services" className="text-foreground hover:underline">Create one →</Link>
            </div>
          )}
        </div>

        {/* Right column */}
        <div className="lg:col-span-2 space-y-4">
          {/* Expiring certificates — only when there are urgent ones */}
          {urgentCerts.length > 0 && (
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                <h3 className="text-sm font-semibold text-foreground">Expiring certificates</h3>
                <Link to="/certificates" className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
                  All <ArrowRight className="w-3 h-3" />
                </Link>
              </div>
              <div className="divide-y divide-border/60">
                {urgentCerts.map((cert) => {
                  const days = cert.days_remaining;
                  const isExpired = cert.expired;
                  return (
                    <div key={cert.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isExpired ? 'bg-destructive' : 'bg-yellow-500'}`} />
                      <span className="font-medium text-foreground truncate flex-1" title={cert.domain_names.join(', ')}>
                        {cert.domain_names[0] || '—'}
                        {cert.domain_names.length > 1 && <span className="text-muted-foreground ml-1">+{cert.domain_names.length - 1}</span>}
                      </span>
                      <span className={`text-xs font-semibold shrink-0 ${isExpired ? 'text-destructive' : (days !== null && days <= 7) ? 'text-destructive' : 'text-yellow-600 dark:text-yellow-400'}`}>
                        {isExpired ? 'Expired' : days !== null ? `${days}d left` : '—'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Provider list */}
          <div className="bg-card border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground">Integrations</h3>
              <Link to="/providers" className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
                Manage <ArrowRight className="w-3 h-3" />
              </Link>
            </div>
            {providers && providers.length > 0 ? (
              <div className="divide-y divide-border/60">
                {providers.map((p) => (
                  <div key={p.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${p.enabled ? 'bg-emerald-500' : 'bg-muted-foreground/30'}`} />
                    <span className="font-medium text-foreground truncate flex-1">{p.name}</span>
                    <span className="text-[11px] text-muted-foreground uppercase tracking-wide">{p.type}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-6 text-center text-sm text-muted-foreground">
                No providers.{' '}
                <Link to="/providers" className="text-foreground hover:underline">Add one →</Link>
              </div>
            )}
          </div>

          {/* Recent activity logs */}
          {logs.length > 0 && (
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                <h3 className="text-sm font-semibold text-foreground">Recent activity</h3>
                <Link to="/monitoring" className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
                  Logs <ArrowRight className="w-3 h-3" />
                </Link>
              </div>
              <div className="divide-y divide-border/60">
                {logs.map((log) => (
                  <div key={log.id} className="flex items-start gap-3 px-4 py-2 text-sm">
                    <span className={`shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full ${logDot(log.level)}`} />
                    <p className="flex-1 min-w-0 text-xs text-foreground break-words leading-relaxed">{log.message}</p>
                    <span className="shrink-0 text-[10px] text-muted-foreground whitespace-nowrap">{formatAge(log.created_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
