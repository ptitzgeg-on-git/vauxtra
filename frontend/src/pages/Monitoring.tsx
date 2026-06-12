import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Radio, RefreshCw, Search, Clock3, Server } from "lucide-react";
import { api } from "@/api/client";
import toast from "react-hot-toast";
import { useSearchParams } from "react-router-dom";
import { useT } from "@/i18n";

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

type ServiceHistoryItem = {
  status: "ok" | "error" | "unknown";
  created_at: string;
};

type ServiceHistoryMap = Record<string, ServiceHistoryItem[]>;

type StatusFilter = "all" | "ok" | "error" | "unknown" | "disabled";

export function Monitoring() {
  const t = useT();
  const queryClient = useQueryClient();
  const SERVICES_CACHE_KEY = "vauxtra.cache.services";
  const [searchParams, setSearchParams] = useSearchParams();
  const initialStatus = searchParams.get("status");
  const statusFromQuery: StatusFilter = ["ok", "error", "unknown", "disabled", "all"].includes(initialStatus || "")
    ? (initialStatus as StatusFilter)
    : "all";

  const [statusFilter, setStatusFilter] = useState<StatusFilter>(statusFromQuery);
  const [routeSearch, setRouteSearch] = useState("");
  const [selectedServiceId, setSelectedServiceId] = useState<number | null>(() => {
    const raw = Number(searchParams.get("service") || 0);
    return Number.isFinite(raw) && raw > 0 ? raw : null;
  });

  useEffect(() => {
    setStatusFilter(statusFromQuery);
  }, [statusFromQuery]);

  const parseBackendTimestamp = (dateRaw: string | null): number | null => {
    if (!dateRaw) return null;
    const normalized = dateRaw.includes("T") ? dateRaw : dateRaw.replace(" ", "T");
    const utcLike = /(?:Z|[+-]\d\d:\d\d)$/.test(normalized) ? normalized : `${normalized}Z`;
    const ts = Date.parse(utcLike);
    return Number.isFinite(ts) ? ts : null;
  };

  const { data: services = [], isLoading } = useQuery<ServiceItem[]>({
    queryKey: ["services"],
    queryFn: () => api.get("/services"),
    refetchInterval: 15000,
    initialData: () => {
      try {
        const raw = sessionStorage.getItem(SERVICES_CACHE_KEY);
        if (!raw) return undefined;
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? (parsed as ServiceItem[]) : undefined;
      } catch {
        return undefined;
      }
    },
  });

  const { data: settingsData } = useQuery<Record<string, string>>({
    queryKey: ["settings"],
    queryFn: () => api.get("/settings"),
  });

  const { data: logsResp } = useQuery<LogsResponse>({
    queryKey: ["logs", "monitoring"],
    queryFn: () => api.get(`/logs?per_page=300`),
    refetchInterval: 10000,
  });

  const { data: servicesHistory } = useQuery<ServiceHistoryMap>({
    queryKey: ["services-history"],
    queryFn: () => api.get("/services/history"),
    refetchInterval: 15000,
  });

  const { data: tunnelHealth } = useQuery<TunnelHealthResponse>({
    queryKey: ["providers-tunnel-health"],
    queryFn: () => api.get("/providers/tunnels/health"),
    refetchInterval: 30000,
  });

  const checkAllMutation = useMutation({
    mutationFn: () => api.post<{ checked?: number }>("/services/check-all"),
    onSuccess: async (data) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["services"] }),
        queryClient.refetchQueries({ queryKey: ["services"], type: "active" }),
        queryClient.invalidateQueries({ queryKey: ["logs"] }),
        queryClient.invalidateQueries({ queryKey: ["logs", "monitoring"] }),
        queryClient.invalidateQueries({ queryKey: ["services-history"] }),
      ]);
      toast.success(t("monitoring.toast.checked_count", { count: data?.checked ?? 0 }));
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || t("monitoring.toast.check_failed"));
    },
  });

  const serviceItems = useMemo(() => (Array.isArray(services) ? services : []), [services]);
  useEffect(() => {
    try {
      sessionStorage.setItem(SERVICES_CACHE_KEY, JSON.stringify(serviceItems));
    } catch {
      // Ignore storage quota/private mode errors.
    }
  }, [serviceItems]);

  const tunnelItems = Array.isArray(tunnelHealth?.items) ? tunnelHealth.items : [];
  const unhealthyTunnels = tunnelItems.filter((t) => !t.health?.ok);

  const enabledServices = serviceItems.filter((s) => s.enabled);
  const disabledServices = serviceItems.filter((s) => !s.enabled);
  const okCount = enabledServices.filter((s) => s.status === "ok").length;
  const errorCount = enabledServices.filter((s) => s.status === "error").length;
  const unknownCount = enabledServices.filter((s) => s.status === "unknown").length;
  const disabledCount = disabledServices.length;
  const checkIntervalMinutes = Number(settingsData?.check_interval || 5);
  const hasAutoCheckData = enabledServices.some((s) => Boolean(s.last_checked));

  const fqdn = (s: ServiceItem) => (s.subdomain ? `${s.subdomain}.${s.domain}` : s.domain);

  const effectiveStatus = (s: ServiceItem): StatusFilter => {
    if (!s.enabled) return "disabled";
    if (s.status === "ok" || s.status === "error" || s.status === "unknown") return s.status;
    return "unknown";
  };

  const filteredRoutes = useMemo(() => {
    return serviceItems.filter((s) => {
      const eff = effectiveStatus(s);
      if (statusFilter !== "all" && eff !== statusFilter) return false;
      if (routeSearch) {
        const host = fqdn(s);
        const q = routeSearch.toLowerCase();
        return host.toLowerCase().includes(q) || s.target_ip.includes(q);
      }
      return true;
    });
  }, [serviceItems, statusFilter, routeSearch]);

  useEffect(() => {
    if (filteredRoutes.length === 0) {
      setSelectedServiceId(null);
      return;
    }
    if (!selectedServiceId || !filteredRoutes.some((s) => s.id === selectedServiceId)) {
      setSelectedServiceId(filteredRoutes[0].id);
    }
  }, [filteredRoutes, selectedServiceId]);

  const selectedService = filteredRoutes.find((s) => s.id === selectedServiceId) || null;

  const selectedHistory = useMemo(() => {
    if (!selectedService) return [];
    const key = String(selectedService.id);
    return Array.isArray(servicesHistory?.[key]) ? servicesHistory[key] : [];
  }, [selectedService, servicesHistory]);

  const selectedLogs = useMemo(() => {
    if (!selectedService) return [];
    const host = fqdn(selectedService).toLowerCase();
    const sidToken = `service ${selectedService.id}`;
    const logs = Array.isArray(logsResp?.items) ? logsResp.items : [];
    return logs.filter((log) => {
      const msg = String(log.message || "").toLowerCase();
      return msg.includes(host) || msg.includes(sidToken);
    });
  }, [selectedService, logsResp]);

  const formatAge = (dateRaw: string | null): string => {
    if (!dateRaw) return "never";
    const ts = parseBackendTimestamp(dateRaw);
    if (ts === null) return dateRaw;
    const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (deltaSec < 60) return `${deltaSec}s ago`;
    if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
    if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
    return `${Math.floor(deltaSec / 86400)}d ago`;
  };

  const statusDot = (status: StatusFilter) => {
    if (status === "ok") return "bg-emerald-500";
    if (status === "error") return "bg-destructive";
    if (status === "disabled") return "bg-muted-foreground/40";
    return "bg-yellow-500";
  };

  const handleFilterChange = (next: StatusFilter) => {
    setStatusFilter(next);
    const currentService = searchParams.get("service");
    const params = new URLSearchParams();
    if (next !== "all") params.set("status", next);
    if (currentService) params.set("service", currentService);
    setSearchParams(params, { replace: true });
  };

  const handleSelectService = (serviceId: number) => {
    setSelectedServiceId(serviceId);
    const params = new URLSearchParams(searchParams);
    params.set("service", String(serviceId));
    if (statusFilter !== "all") params.set("status", statusFilter);
    setSearchParams(params, { replace: true });
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
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Monitoring</h1>
          <div className="flex items-center gap-2 text-xs font-semibold">
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />{okCount}</span>
            {errorCount > 0 && <span className="inline-flex items-center gap-1 text-destructive"><span className="w-1.5 h-1.5 rounded-full bg-destructive" />{errorCount}</span>}
            {unknownCount > 0 && <span className="inline-flex items-center gap-1 text-yellow-600 dark:text-yellow-400"><span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />{unknownCount}</span>}
            {disabledCount > 0 && <span className="inline-flex items-center gap-1 text-muted-foreground"><span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40" />{disabledCount}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {hasAutoCheckData
              ? t("monitoring.auto_checks_every", { minutes: checkIntervalMinutes })
              : t("monitoring.auto_checks_waiting")}
          </span>
          <button
            onClick={() => checkAllMutation.mutate()}
            disabled={checkAllMutation.isPending}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-card text-xs font-semibold hover:bg-accent transition-colors"
          >
            {checkAllMutation.isPending ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Radio className="w-3.5 h-3.5" />}
            {t("monitoring.check_all")}
          </button>
        </div>
      </div>

      {unhealthyTunnels.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-destructive/30 bg-destructive/5 text-destructive text-xs font-semibold">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {t("monitoring.tunnels_down", { count: unhealthyTunnels.length })}: {unhealthyTunnels.map((t) => t.name).join(", ")}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <section className="xl:col-span-7 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-foreground mr-2">{t("monitoring.route_health")}</h2>
            <div className="relative flex-1 min-w-[180px] max-w-xs">
              <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                placeholder={t("monitoring.filter_routes_placeholder")}
                value={routeSearch}
                onChange={(e) => setRouteSearch(e.target.value)}
                className="w-full bg-input border border-border rounded-lg pl-9 pr-3 py-1.5 text-sm outline-none focus:border-foreground/30 transition-colors"
              />
            </div>
            <div className="flex items-center gap-1">
              {([
                { key: "all" as StatusFilter, label: t("monitoring.filter.all"), count: serviceItems.length },
                { key: "ok" as StatusFilter, label: t("monitoring.filter.ok"), count: okCount },
                { key: "error" as StatusFilter, label: t("monitoring.filter.error"), count: errorCount },
                { key: "unknown" as StatusFilter, label: t("monitoring.filter.unknown"), count: unknownCount },
                { key: "disabled" as StatusFilter, label: t("monitoring.filter.disabled"), count: disabledCount },
              ] as const).map(({ key, label, count }) => (
                <button
                  key={key}
                  onClick={() => handleFilterChange(key)}
                  className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${statusFilter === key ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground hover:bg-muted"}`}
                >
                  {label} <span className="opacity-60">{count}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="bg-card border border-border rounded-lg overflow-hidden">
            <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-4 px-4 py-2.5 border-b border-border text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
              <span className="w-4" />
              <span>{t("monitoring.table.hostname")}</span>
              <span className="hidden sm:block">{t("monitoring.table.target")}</span>
              <span className="hidden md:block">{t("monitoring.table.provider")}</span>
              <span className="text-right">{t("monitoring.table.checked")}</span>
            </div>
            {filteredRoutes.length === 0 ? (
              <div className="p-6 text-sm text-muted-foreground text-center">
                {statusFilter !== "all" ? t("monitoring.empty.filter") : t("monitoring.empty.all")}
              </div>
            ) : (
              <div className="max-h-[62vh] overflow-auto divide-y divide-border/50">
                {filteredRoutes.map((s) => {
                  const eff = effectiveStatus(s);
                  const selected = selectedServiceId === s.id;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => handleSelectService(s.id)}
                      className={`w-full text-left grid grid-cols-[auto_1fr_auto_auto_auto] gap-4 px-4 py-3 items-center transition-colors text-sm ${selected ? "bg-accent/50" : "hover:bg-accent/40"}`}
                    >
                      <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(eff)}`} />
                      <div className="min-w-0">
                        <p className={`font-medium truncate ${eff === "disabled" ? "text-muted-foreground" : "text-foreground"}`}>{fqdn(s)}</p>
                        {eff === "error" && (
                          <p className="text-[11px] text-destructive mt-0.5">
                            {s.expose_mode === "tunnel" ? t("monitoring.error.check_tunnel") : t("monitoring.error.tcp_unreachable", { target: `${s.target_ip}:${s.target_port}` })}
                          </p>
                        )}
                        {eff === "disabled" && (
                          <p className="text-[11px] text-muted-foreground mt-0.5">{t("monitoring.status.disabled_by_user")}</p>
                        )}
                      </div>
                      <span className="font-mono text-xs text-muted-foreground hidden sm:block">{s.target_ip}:{s.target_port}</span>
                      <span className="text-xs text-muted-foreground hidden md:block truncate max-w-[140px]">{s.proxy_provider_name || s.dns_provider_name || "—"}</span>
                      <span className="text-[11px] text-muted-foreground text-right whitespace-nowrap">{formatAge(s.last_checked)}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        <aside className="xl:col-span-5">
          <div className="bg-card border border-border rounded-lg p-4 h-full min-h-[420px]">
            {!selectedService ? (
              <div className="h-full flex flex-col items-center justify-center text-center text-muted-foreground">
                <Server className="w-8 h-8 mb-2" />
                <p className="text-sm font-medium">{t("monitoring.select_hostname_prompt")}</p>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="border-b border-border pb-3">
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">{t("monitoring.selected_host")}</p>
                  <h3 className="text-lg font-semibold text-foreground truncate">{fqdn(selectedService)}</h3>
                  <p className="text-xs text-muted-foreground mt-1">{t("monitoring.target_label", { target: `${selectedService.target_ip}:${selectedService.target_port}` })}</p>
                </div>

                <div>
                  <p className="text-xs uppercase tracking-wider text-muted-foreground mb-2">{t("monitoring.timeline_title")}</p>
                  {selectedHistory.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("monitoring.timeline_empty")}</p>
                  ) : (
                    <div className="max-h-[200px] overflow-auto rounded-md border border-border divide-y divide-border/50">
                      {selectedHistory.slice().reverse().map((item, idx) => (
                        <div key={`${item.created_at}-${idx}`} className="px-3 py-2 flex items-center justify-between text-xs">
                          <span className="inline-flex items-center gap-1.5">
                            <span className={`w-1.5 h-1.5 rounded-full ${statusDot(item.status as StatusFilter)}`} />
                            <span className={item.status === "error" ? "text-destructive" : item.status === "ok" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>{item.status.toUpperCase()}</span>
                          </span>
                          <span className="text-muted-foreground">{item.created_at}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <p className="text-xs uppercase tracking-wider text-muted-foreground mb-2">{t("monitoring.related_logs_title")}</p>
                  {selectedLogs.length === 0 ? (
                    <p className="text-sm text-muted-foreground">{t("monitoring.related_logs_empty")}</p>
                  ) : (
                    <div className="max-h-[220px] overflow-auto rounded-md border border-border divide-y divide-border/50">
                      {selectedLogs.slice(0, 50).map((log) => (
                        <div key={log.id} className="px-3 py-2 text-xs space-y-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className={`uppercase font-semibold ${log.level === "error" ? "text-destructive" : log.level === "warn" || log.level === "warning" ? "text-yellow-600 dark:text-yellow-400" : "text-muted-foreground"}`}>{log.level}</span>
                            <span className="text-muted-foreground inline-flex items-center gap-1"><Clock3 className="w-3 h-3" />{log.created_at}</span>
                          </div>
                          <p className="text-foreground break-words leading-relaxed">{log.message}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
