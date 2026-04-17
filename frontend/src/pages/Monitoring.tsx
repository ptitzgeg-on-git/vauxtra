import { useState, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Filter, Radio, RefreshCw, Search } from "lucide-react";
import { api } from "@/api/client";
import toast from "react-hot-toast";

type ServiceItem = {
  id: number;
  subdomain: string;
  domain: string;
  target_ip: string;
  target_port: number;
  status: "ok" | "error" | "unknown";
  enabled: boolean | number;
  last_checked: string | null;
  expose_mode?: string;
  proxy_provider_name?: string;
  dns_provider_name?: string;
};

type LogItem = { id: number; level: string; message: string; created_at: string };

type LogsResponse = {
  items: LogItem[];
  total: number;
  page: number;
  pages: number;
};

type TunnelHealthResponse = {
  total?: number;
  healthy?: number;
  down?: number;
  items?: Array<{
    id: number;
    name: string;
    health?: {
      ok?: boolean;
      status?: string;
      connections?: number;
      clients?: number;
      error?: string;
    };
  }>;
};

type StatusFilter = 'all' | 'ok' | 'error' | 'unknown';
type LogLevel = '' | 'error' | 'ok' | 'info' | 'warning';

export function Monitoring() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [routeSearch, setRouteSearch] = useState('');
  const [logLevel, setLogLevel] = useState<LogLevel>('');
  const [logPage, setLogPage] = useState(1);

  const { data: services = [], isLoading, isFetching: isFetchingServices } = useQuery<ServiceItem[]>({
    queryKey: ['services'],
    queryFn: () => api.get('/services'),
    refetchInterval: 15000,
  });

  const { data: logsResp } = useQuery<LogsResponse>({
    queryKey: ['logs', 'monitoring', logLevel, logPage],
    queryFn: () => api.get(`/logs?per_page=30&page=${logPage}${logLevel ? `&level=${logLevel}` : ''}`),
    refetchInterval: 10000,
  });

  const { data: tunnelHealth } = useQuery<TunnelHealthResponse>({
    queryKey: ['providers-tunnel-health'],
    queryFn: () => api.get('/providers/tunnels/health'),
    refetchInterval: 30000,
  });

  const checkAllMutation = useMutation({
    mutationFn: () => api.post<{ checked?: number }>('/services/check-all'),
    onSuccess: (data) => {
      toast.success(`Checked ${data?.checked ?? 0} service(s)`);
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to check services');
    },
  });

  const serviceItems = Array.isArray(services) ? services : [];
  const logs = Array.isArray(logsResp?.items) ? logsResp.items : [];
  const logPages = logsResp?.pages ?? 1;
  const logTotal = logsResp?.total ?? 0;
  const tunnelItems = Array.isArray(tunnelHealth?.items) ? tunnelHealth.items : [];
  const unhealthyTunnels = tunnelItems.filter((t) => !t.health?.ok);

  const enabledServices = serviceItems.filter((s) => Boolean(s.enabled));
  const okCount = enabledServices.filter((s) => s.status === 'ok').length;
  const errorCount = enabledServices.filter((s) => s.status === 'error').length;
  const unknownCount = enabledServices.filter((s) => s.status === 'unknown').length;

  const filteredRoutes = useMemo(() => {
    return enabledServices.filter((s) => {
      if (statusFilter !== 'all' && s.status !== statusFilter) return false;
      if (routeSearch) {
        const fqdn = s.subdomain ? `${s.subdomain}.${s.domain}` : s.domain;
        const q = routeSearch.toLowerCase();
        return fqdn.toLowerCase().includes(q) || s.target_ip.includes(q);
      }
      return true;
    });
  }, [enabledServices, statusFilter, routeSearch]);

  const formatAge = (dateRaw: string | null): string => {
    if (!dateRaw) return 'never';
    const ts = Date.parse(dateRaw.replace(' ', 'T'));
    if (!Number.isFinite(ts)) return dateRaw;
    const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (deltaSec < 60) return `${deltaSec}s ago`;
    if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
    if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
    return `${Math.floor(deltaSec / 86400)}d ago`;
  };

  const fqdn = (s: ServiceItem) => (s.subdomain ? `${s.subdomain}.${s.domain}` : s.domain);

  const statusDot = (status: string) => {
    if (status === 'ok') return 'bg-emerald-500';
    if (status === 'error') return 'bg-destructive';
    return 'bg-yellow-500';
  };

  const logDot = (level: string) => {
    if (level === 'error') return 'bg-destructive';
    if (level === 'ok') return 'bg-emerald-500';
    if (level === 'warning') return 'bg-yellow-500';
    return 'bg-blue-500';
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-8 animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Monitoring</h1>
          <div className="flex items-center gap-2 text-xs font-semibold">
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />{okCount}</span>
            {errorCount > 0 && <span className="inline-flex items-center gap-1 text-destructive"><span className="w-1.5 h-1.5 rounded-full bg-destructive" />{errorCount}</span>}
            {unknownCount > 0 && <span className="inline-flex items-center gap-1 text-yellow-600 dark:text-yellow-400"><span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />{unknownCount}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isFetchingServices && <RefreshCw className="w-3.5 h-3.5 animate-spin text-muted-foreground" />}
          <button
            onClick={() => checkAllMutation.mutate()}
            disabled={checkAllMutation.isPending}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card text-xs font-semibold hover:bg-accent transition-colors"
          >
            {checkAllMutation.isPending ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Radio className="w-3.5 h-3.5" />}
            Check all
          </button>
        </div>
      </div>

      {/* Tunnel warnings */}
      {unhealthyTunnels.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-destructive/30 bg-destructive/5 text-destructive text-xs font-semibold">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {unhealthyTunnels.length} tunnel{unhealthyTunnels.length > 1 ? 's' : ''} down: {unhealthyTunnels.map(t => t.name).join(', ')}
        </div>
      )}

      {/* ── Route Health ── */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-foreground mr-2">Route Health</h2>
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              placeholder="Filter routes..."
              value={routeSearch}
              onChange={(e) => setRouteSearch(e.target.value)}
              className="w-full bg-input border border-border rounded-lg pl-9 pr-3 py-1.5 text-sm outline-none focus:border-foreground/30 transition-colors"
            />
          </div>
          <div className="flex items-center gap-1">
            {([
              { key: 'all' as StatusFilter, label: 'All', count: enabledServices.length },
              { key: 'ok' as StatusFilter, label: 'OK', count: okCount },
              { key: 'error' as StatusFilter, label: 'Error', count: errorCount },
              { key: 'unknown' as StatusFilter, label: 'Unknown', count: unknownCount },
            ] as const).map(({ key, label, count }) => (
              <button
                key={key}
                onClick={() => setStatusFilter(key)}
                className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${statusFilter === key ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground hover:bg-muted'}`}
              >
                {label} <span className="opacity-60">{count}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-4 px-4 py-2.5 border-b border-border text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
            <span className="w-4" />
            <span>Hostname</span>
            <span className="hidden sm:block">Target</span>
            <span className="hidden md:block">Provider</span>
            <span className="text-right">Checked</span>
          </div>
          {filteredRoutes.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground text-center">
              {statusFilter !== 'all' ? 'No routes match this filter.' : 'No enabled routes to monitor.'}
            </div>
          ) : (
            <div className="max-h-[45vh] overflow-auto divide-y divide-border/50">
              {filteredRoutes.map((s) => (
                <div key={s.id} className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-4 px-4 py-3 items-center hover:bg-accent/40 transition-colors text-sm">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(s.status)}`} />
                  <div className="min-w-0">
                    <p className="font-medium text-foreground truncate">{fqdn(s)}</p>
                    {s.status === 'error' && (
                      <p className="text-[11px] text-destructive mt-0.5">
                        {s.expose_mode === 'tunnel'
                          ? 'Check tunnel connector'
                          : `TCP unreachable on ${s.target_ip}:${s.target_port}`}
                      </p>
                    )}
                  </div>
                  <span className="font-mono text-xs text-muted-foreground hidden sm:block">{s.target_ip}:{s.target_port}</span>
                  <span className="text-xs text-muted-foreground hidden md:block truncate max-w-[120px]">{s.proxy_provider_name || s.dns_provider_name || '—'}</span>
                  <span className="text-[11px] text-muted-foreground text-right whitespace-nowrap">{formatAge(s.last_checked)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* ── Logs ── */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-foreground mr-2">Logs</h2>
          <Filter className="w-3.5 h-3.5 text-muted-foreground" />
          {([
            { key: '' as LogLevel, label: 'All' },
            { key: 'error' as LogLevel, label: 'Error' },
            { key: 'warning' as LogLevel, label: 'Warning' },
            { key: 'ok' as LogLevel, label: 'OK' },
            { key: 'info' as LogLevel, label: 'Info' },
          ] as const).map(({ key, label }) => (
            <button
              key={key}
              onClick={() => { setLogLevel(key); setLogPage(1); }}
              className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${logLevel === key ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground hover:bg-muted'}`}
            >
              {label}
            </button>
          ))}
          <span className="ml-auto text-xs text-muted-foreground">{logTotal} entries</span>
        </div>

        <div className="bg-card border border-border rounded-lg overflow-hidden">
          {logs.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground text-center">No log entries.</div>
          ) : (
            <div className="max-h-[35vh] overflow-auto divide-y divide-border/50">
              {logs.map((log) => (
                <div key={log.id} className="flex items-start gap-3 px-4 py-2.5 hover:bg-accent/30 transition-colors">
                  <span className={`shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full ${logDot(log.level)}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground break-words leading-relaxed">{log.message}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <span className="text-[10px] text-muted-foreground font-mono uppercase">{log.level}</span>
                    <p className="text-[10px] text-muted-foreground">{formatAge(log.created_at)}</p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {logPages > 1 && (
            <div className="flex items-center justify-between px-4 py-2.5 border-t border-border text-xs">
              <button
                onClick={() => setLogPage(Math.max(1, logPage - 1))}
                disabled={logPage <= 1}
                className="px-2.5 py-1 rounded-md border border-border hover:bg-muted disabled:opacity-40 font-semibold"
              >
                Previous
              </button>
              <span className="text-muted-foreground">Page {logPage} / {logPages}</span>
              <button
                onClick={() => setLogPage(Math.min(logPages, logPage + 1))}
                disabled={logPage >= logPages}
                className="px-2.5 py-1 rounded-md border border-border hover:bg-muted disabled:opacity-40 font-semibold"
              >
                Next
              </button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
