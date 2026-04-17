import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { Plus, Globe, Search, LayoutGrid, LayoutList, Pencil, Trash2, RefreshCw, ShieldCheck, CheckSquare, Square, Power, PowerOff, X, Waypoints, ArrowRightLeft, AlertTriangle, Loader2 } from "lucide-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { ExposeModal } from "@/components/features/expose/ExposeModal";
import { useConfirmDialog } from "@/components/ui/ConfirmDialog";
import toast from "react-hot-toast";
import type { Service, Provider, Tag, Environment } from "@/types/api";

type ModeFilter = 'all' | 'tunnel' | 'proxy' | 'dns' | 'disabled';

function useLocalStorage<T>(key: string, fallback: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try { const raw = localStorage.getItem(key); return raw !== null ? JSON.parse(raw) : fallback; }
    catch { return fallback; }
  });
  const set = useCallback((v: T | ((prev: T) => T)) => {
    setValue(prev => {
      const next = typeof v === 'function' ? (v as (prev: T) => T)(prev) : v;
      localStorage.setItem(key, JSON.stringify(next));
      return next;
    });
  }, [key]);
  return [value, set];
}

/* ────────────────────────────────────────────────────────────────
   Provider Sync Panel
   ──────────────────────────────────────────────────────────────── */

export function Services() {
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const [isExposeModalOpen, setIsExposeModalOpen] = useState(false);
  const [createModalNonce, setCreateModalNonce] = useState(0);
  const [search, setSearch] = useLocalStorage('vauxtra.services.search', '');
  const [viewMode, setViewMode] = useLocalStorage<'list' | 'grid'>('vauxtra.services.viewMode', 'list');
  const [modeFilter, setModeFilter] = useLocalStorage<ModeFilter>('vauxtra.services.mode', 'all');
  const [editingService, setEditingService] = useState<Service | null>(null);
  const [driftByService, setDriftByService] = useState<Record<number, Record<string, unknown>>>({});
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [actioningIds, setActioningIds] = useState<Set<number>>(new Set());
  const searchRef = useRef<HTMLInputElement>(null);

  const { data: services, isLoading } = useQuery({
    queryKey: ['services'],
    queryFn: () => api.get('/services')
  });

  const { data: providers } = useQuery({
    queryKey: ['providers'],
    queryFn: () => api.get('/providers')
  });

  const getProvider = (id: number | string | null | undefined) =>
    Array.isArray(providers) ? providers.find((p: Provider) => String(p.id) === String(id ?? '')) : null;

  const buildServicePayload = (service: Service | null, overrides: Record<string, unknown> = {}) => {
    const tagIds = Array.isArray(service?.tags)
      ? service.tags.map((t: Tag) => Number(t?.id)).filter((id: number) => Number.isFinite(id))
      : [];

    const environmentIds = Array.isArray(service?.environments)
      ? service.environments.map((e: Environment) => Number(e?.id)).filter((id: number) => Number.isFinite(id))
      : [];

    return {
      id: Number(overrides.id ?? service?.id ?? 0),
      subdomain: String(overrides.subdomain ?? service?.subdomain ?? '').trim().toLowerCase(),
      domain: String(overrides.domain ?? service?.domain ?? '').trim().toLowerCase(),
      target_ip: String(overrides.target_ip ?? service?.target_ip ?? '').trim(),
      target_port: Number(overrides.target_port ?? service?.target_port ?? 80),
      forward_scheme: (overrides.forward_scheme ?? service?.forward_scheme ?? 'http') === 'https' ? 'https' : 'http',
      websocket: Boolean(overrides.websocket ?? service?.websocket ?? false),
      expose_mode: String(overrides.expose_mode ?? service?.expose_mode ?? 'proxy_dns'),
      public_target_mode: (overrides.public_target_mode ?? service?.public_target_mode ?? 'manual') === 'auto' ? 'auto' : 'manual',
      auto_update_dns: Boolean(overrides.auto_update_dns ?? service?.auto_update_dns ?? false),
      tunnel_provider_id: (overrides.tunnel_provider_id ?? service?.tunnel_provider_id)
        ? Number(overrides.tunnel_provider_id ?? service?.tunnel_provider_id)
        : null,
      tunnel_hostname: String(overrides.tunnel_hostname ?? service?.tunnel_hostname ?? ''),
      enabled: Boolean(overrides.enabled ?? service?.enabled ?? true),
      proxy_provider_id: (overrides.proxy_provider_id ?? service?.proxy_provider_id)
        ? Number(overrides.proxy_provider_id ?? service?.proxy_provider_id)
        : null,
      dns_provider_id: (overrides.dns_provider_id ?? service?.dns_provider_id)
        ? Number(overrides.dns_provider_id ?? service?.dns_provider_id)
        : null,
      dns_ip: String(overrides.dns_ip ?? service?.dns_ip ?? '').trim(),
      tag_ids: tagIds,
      environment_ids: environmentIds,
      icon_url: String(overrides.icon_url ?? service?.icon_url ?? ''),
      extra_proxy_provider_ids: Array.isArray(service?.extra_proxy_provider_ids)
        ? service.extra_proxy_provider_ids.map((id: number) => Number(id)).filter((id: number) => Number.isFinite(id))
        : [],
      extra_dns_provider_ids: Array.isArray(service?.extra_dns_provider_ids)
        ? service.extra_dns_provider_ids.map((id: number) => Number(id)).filter((id: number) => Number.isFinite(id))
        : [],
    };
  };

  const _startAction = (id: number) => setActioningIds((prev) => new Set(prev).add(id));
  const _endAction = (id: number) => setActioningIds((prev) => { const next = new Set(prev); next.delete(id); return next; });

  const toggleStatus = useMutation({
    mutationFn: (data: {service: Service, enabled: boolean}) => {
      _startAction(data.service.id);
      return api.put(`/services/${data.service.id}`, buildServicePayload(data.service, { enabled: data.enabled }));
    },
    onSuccess: (_d, vars) => { _endAction(vars.service.id); queryClient.invalidateQueries({ queryKey: ['services'] }); },
    onError: (_e, vars) => { _endAction(vars.service.id); toast.error('Unable to update service status'); },
  });

  const deleteService = useMutation({
    mutationFn: (id: number) => { _startAction(id); return api.delete(`/services/${id}`); },
    onSuccess: (_d, id) => { _endAction(id); queryClient.invalidateQueries({ queryKey: ['services'] }); },
    onError: (_e, id) => { _endAction(id); toast.error('Delete failed'); },
  });

  const checkDrift = useMutation({
    mutationFn: (id: number) => { _startAction(id); return api.get(`/services/${id}/drift`) as Promise<Record<string, unknown>>; },
    onSuccess: (data: Record<string, unknown>) => {
      const serviceId = Number(data?.service_id || 0);
      if (serviceId) {
        _endAction(serviceId);
        setDriftByService((prev) => ({ ...prev, [serviceId]: data }));
      }
      const issues = Array.isArray(data?.issues) ? data.issues : [];
      if (issues.length === 0) {
        toast.success('No drift detected');
      } else {
        toast.error(`${issues.length} drift issue(s) detected`);
      }
    },
    onError: (err: { response?: { data?: { detail?: string } } }, id) => {
      _endAction(id);
      toast.error(err?.response?.data?.detail || 'Drift check failed');
    },
  });

  const reconcileService = useMutation({
    mutationFn: (id: number) => { _startAction(id); return api.post(`/services/${id}/reconcile`) as Promise<Record<string, unknown>>; },
    onSuccess: (data: Record<string, unknown>, id) => {
      _endAction(id);
      const after = data?.after as Record<string, unknown> | undefined;
      const serviceId = Number(after?.service_id || 0);
      if (serviceId) {
        setDriftByService((prev) => ({ ...prev, [serviceId]: after as Record<string, unknown> }));
      }
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      if (data?.ok) {
        toast.success('Reconcile completed');
      } else {
        toast.error('Reconcile finished with issues');
      }
    },
    onError: (err: { response?: { data?: { detail?: string } } }, id) => {
      _endAction(id);
      toast.error(err?.response?.data?.detail || 'Reconcile failed');
    },
  });

  const bulkAction = useMutation({
    mutationFn: (data: { ids: number[]; action: string }) =>
      api.post('/services/bulk', data) as Promise<{ ok: boolean; affected: number; errors?: string[] }>,
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      setSelectedIds(new Set());
      const labels: Record<string, string> = { enable: 'enabled', disable: 'disabled', delete: 'deleted' };
      const errors = data.errors || [];
      if (errors.length === 0) {
        toast.success(`${data.affected} service(s) ${labels[variables.action] || variables.action}`);
      } else {
        const errorSummary = errors.slice(0, 2).join('; ');
        const moreCount = errors.length > 2 ? ` (+${errors.length - 2} more)` : '';
        toast(`${data.affected} service(s) ${labels[variables.action] || variables.action} with warnings: ${errorSummary}${moreCount}`, { icon: '⚠️', duration: 8000 });
      }
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(err?.response?.data?.detail || 'Bulk action failed');
    },
  });

  const allServices: Service[] = Array.isArray(services) ? services : [];

  const modeCounts = useMemo(() => ({
    all: allServices.length,
    tunnel: allServices.filter(s => Boolean(s.enabled) && s.expose_mode === 'tunnel').length,
    proxy: allServices.filter(s => Boolean(s.enabled) && s.expose_mode !== 'tunnel' && Boolean(s.proxy_provider_id)).length,
    dns: allServices.filter(s => Boolean(s.enabled) && s.expose_mode !== 'tunnel' && !s.proxy_provider_id && Boolean(s.dns_provider_id)).length,
    disabled: allServices.filter(s => !s.enabled).length,
  }), [allServices]);

  const filteredServices = useMemo(() => {
    return allServices.filter((s: Service) => {
      const fqdn = s.subdomain ? `${s.subdomain}.${s.domain}` : s.domain;
      const matchSearch = !search ||
        fqdn.toLowerCase().includes(search.toLowerCase()) ||
        (s.subdomain && s.subdomain.toLowerCase().includes(search.toLowerCase())) ||
        s.domain.toLowerCase().includes(search.toLowerCase());
      if (!matchSearch) return false;
      if (modeFilter === 'disabled') return !s.enabled;
      if (modeFilter === 'tunnel') return Boolean(s.enabled) && s.expose_mode === 'tunnel';
      if (modeFilter === 'proxy') return Boolean(s.enabled) && s.expose_mode !== 'tunnel' && Boolean(s.proxy_provider_id);
      if (modeFilter === 'dns') return Boolean(s.enabled) && s.expose_mode !== 'tunnel' && !s.proxy_provider_id;
      return true; // 'all'
    });
  }, [allServices, search, modeFilter]);

  const toggleSelect = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === filteredServices.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredServices.map(s => s.id)));
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable;

      if (e.key === '/' && !isInput) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (e.key === 'Escape') {
        if (selectedIds.size > 0) { setSelectedIds(new Set()); return; }
        if (document.activeElement === searchRef.current) { searchRef.current?.blur(); return; }
        return;
      }
      if (e.key === 'n' && !isInput && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setCreateModalNonce(v => v + 1);
        setIsExposeModalOpen(true);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'a' && !isInput && filteredServices.length > 0) {
        e.preventDefault();
        toggleSelectAll();
        return;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectedIds.size, filteredServices.length]);

  const isRouteModalOpen = isExposeModalOpen || Boolean(editingService);
  const routeModalMode = editingService ? 'edit' : 'create';
  const routeModalKey = editingService ? `edit-${editingService.id}` : `create-${createModalNonce}`;
  const closeRouteModal = () => {
    setIsExposeModalOpen(false);
    setEditingService(null);
  };

  const activeCount = allServices.filter((s: Service) => s.enabled).length;
  
  // Shared structural styles
  const cardClass = "bg-card border border-border rounded-xl shadow-sm";

  const LOCAL_DNS_TYPES = ['pihole', 'adguard'];

  const getDnsLabel = (srv: Service): { label: string; isLocal: boolean } => {
    const dnsP = getProvider(srv.dns_provider_id);
    if (!dnsP) return { label: 'DNS', isLocal: false };
    const pType = String(dnsP.type || '').toLowerCase();
    if (LOCAL_DNS_TYPES.includes(pType)) return { label: 'Local DNS', isLocal: true };
    return { label: 'External DNS', isLocal: false };
  };

  const modeBadge = (srv: Service) => {
    if (!srv.enabled) return <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground border border-border">Disabled</span>;
    if (srv.expose_mode === 'tunnel') return <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20"><Waypoints className="w-2.5 h-2.5" />Tunnel</span>;
    if (srv.proxy_provider_id && srv.dns_provider_id) {
      const dns = getDnsLabel(srv);
      return <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/20"><ArrowRightLeft className="w-2.5 h-2.5" />Proxy + {dns.label}</span>;
    }
    if (srv.proxy_provider_id) return <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/20"><ArrowRightLeft className="w-2.5 h-2.5" />Proxy</span>;
    if (srv.dns_provider_id) {
      const dns = getDnsLabel(srv);
      if (dns.isLocal) return <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20"><Globe className="w-2.5 h-2.5" />{dns.label}</span>;
      return <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20"><Globe className="w-2.5 h-2.5" />{dns.label}</span>;
    }
    return <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-muted text-muted-foreground border border-border"><Globe className="w-2.5 h-2.5" />Manual</span>;
  };

  if (isLoading) {
    return (
      <div className="flex flex-col space-y-4 max-w-7xl mx-auto pt-10">
        <div className="h-8 w-48 bg-muted rounded animate-pulse mb-4"></div>
        <div className="h-64 bg-card rounded-xl shadow-sm border border-border animate-pulse"></div>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-8 animate-in fade-in duration-200">
      
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Endpoints</h1>
          <span className="text-sm text-muted-foreground font-medium">{activeCount} active</span>
        </div>
        
        <div className="flex items-center gap-2 shrink-0">
          <button 
            onClick={() => {
              setCreateModalNonce((v) => v + 1);
              setIsExposeModalOpen(true);
            }}
            className="flex items-center justify-center gap-2 bg-primary hover:opacity-90 text-primary-foreground px-5 py-2.5 text-sm rounded-lg font-semibold transition-all shadow-sm focus:ring-2 focus:ring-primary/30 outline-none"
          >
            <Plus className="w-4 h-4" />
            Route new service
          </button>
        </div>
      </div>

      {/* Mode filter tabs */}
      <div className="flex flex-wrap gap-2">
        {([ 
          { key: 'all' as ModeFilter, label: 'All', color: '' },
          { key: 'tunnel' as ModeFilter, label: 'Tunnel', color: 'purple' },
          { key: 'proxy' as ModeFilter, label: 'Proxy / Reverse', color: 'teal' },
          { key: 'dns' as ModeFilter, label: 'DNS only', color: 'blue' },
          { key: 'disabled' as ModeFilter, label: 'Disabled', color: 'muted' },
        ] as const).map(({ key, label, color }) => {
          const count = modeCounts[key];
          const active = modeFilter === key;
          const colorMap: Record<string, string> = {
            purple: active ? 'bg-purple-500/15 text-purple-700 dark:text-purple-300 border-purple-500/40' : 'text-muted-foreground border-border hover:border-purple-500/30 hover:text-purple-600',
            teal: active ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/40' : 'text-muted-foreground border-border hover:border-teal-500/30 hover:text-teal-600',
            blue: active ? 'bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/40' : 'text-muted-foreground border-border hover:border-blue-500/30 hover:text-blue-600',
            muted: active ? 'bg-muted text-foreground border-border' : 'text-muted-foreground border-border hover:text-foreground',
            '': active ? 'bg-primary/10 text-primary border-primary/30' : 'text-muted-foreground border-border hover:text-foreground',
          };
          return (
            <button
              key={key}
              onClick={() => { setModeFilter(key); setSelectedIds(new Set()); }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-all ${colorMap[color]}`}
            >
              {label}
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${active ? 'bg-white/20' : 'bg-muted'}`}>{count}</span>
            </button>
          );
        })}
      </div>

      {/* Toolbar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="relative w-full sm:w-[350px] group">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground group-focus-within:text-primary transition-colors" />
          <input 
            ref={searchRef}
            type="text"
            placeholder="Search endpoints... (press /)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-input border border-border focus:border-primary focus:ring-4 focus:ring-primary/10 rounded-lg pl-10 pr-4 py-2 text-sm outline-none transition-all shadow-sm font-medium placeholder:font-normal text-foreground placeholder:text-muted-foreground"
          />
        </div>

        <div className="flex items-center gap-1 bg-muted p-1 rounded-lg border border-border">
          <button 
            onClick={() => setViewMode("list")}
            className={`p-1.5 rounded-md transition-all ${viewMode === 'list' ? 'bg-card shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
          >
            <LayoutList className="w-4 h-4" />
          </button>
          <button 
            onClick={() => setViewMode("grid")}
            className={`p-1.5 rounded-md transition-all ${viewMode === 'grid' ? 'bg-card shadow-sm text-foreground' : 'text-muted-foreground hover:text-foreground'}`}
          >
            <LayoutGrid className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Services List / Grid */}
      {filteredServices.length === 0 ? (
        <div className={`${cardClass} flex flex-col items-center justify-center py-24 bg-muted/30`}>
           <div className="w-12 h-12 rounded-2xl bg-card border border-border shadow-sm flex items-center justify-center mb-5">
             <Globe className="w-6 h-6 text-muted-foreground" />
           </div>
           <h3 className="text-base font-semibold text-foreground">No endpoints found</h3>
           <p className="text-muted-foreground text-sm mt-1.5 mb-6 text-center max-w-sm">
             You haven't routed any traffic yet or your search didn't match anything in the database.
           </p>
           <button
             onClick={() => {
               setCreateModalNonce((v) => v + 1);
               setIsExposeModalOpen(true);
             }}
             className="text-sm text-primary font-semibold hover:opacity-80 transition-colors bg-card border border-border shadow-sm rounded-lg px-4 py-2"
           >
             Create a Route
           </button>
        </div>
      ) : viewMode === 'list' ? (
        <div className={`${cardClass} overflow-hidden`}>
          <table className="w-full text-left border-collapse">
            <thead className="bg-muted/60 border-b border-border">
              <tr>
                <th className="px-3 py-3 w-10 text-center">
                  <button onClick={toggleSelectAll} className="p-0.5 text-muted-foreground hover:text-foreground transition-colors" title="Select all">
                    {selectedIds.size === filteredServices.length && filteredServices.length > 0
                      ? <CheckSquare className="w-4 h-4 text-primary" />
                      : <Square className="w-4 h-4" />}
                  </button>
                </th>
                <th className="px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider w-10 text-center">On</th>
                <th className="px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider w-32">Mode</th>
                <th className="px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Endpoint</th>
                <th className="px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Target</th>
                <th className="px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Provider(s)</th>
                <th className="px-4 py-3 w-24"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {filteredServices.map((srv: Service) => {
                const fullUrl = srv.subdomain ? `${srv.subdomain}.${srv.domain}` : srv.domain;
                const publicHost = srv.expose_mode === 'tunnel' && srv.tunnel_hostname ? srv.tunnel_hostname : fullUrl;
                const proxyProvider = getProvider(srv.expose_mode === 'tunnel' ? srv.tunnel_provider_id : srv.proxy_provider_id);
                const dnsProvider = srv.expose_mode !== 'tunnel' ? getProvider(srv.dns_provider_id) : null;
                const drift = driftByService[Number(srv.id)];
                const driftIssues = Array.isArray(drift?.issues) ? drift.issues.length : 0;
                
                return (
                  <tr key={srv.id} className={`hover:bg-accent/50 transition-colors group ${selectedIds.has(srv.id) ? 'bg-primary/5' : ''} ${!srv.enabled ? 'opacity-60' : ''} ${actioningIds.has(srv.id) ? 'opacity-70' : ''}`}>
                    <td className="px-3 py-3.5 text-center">
                      <button onClick={() => toggleSelect(srv.id)} className="p-0.5 text-muted-foreground hover:text-foreground transition-colors">
                        {selectedIds.has(srv.id) ? <CheckSquare className="w-4 h-4 text-primary" /> : <Square className="w-4 h-4" />}
                      </button>
                    </td>
                    <td className="px-4 py-3.5 text-center">
                      <button 
                        onClick={() => toggleStatus.mutate({ service: srv, enabled: !srv.enabled })}
                        className="inline-flex items-center justify-center p-1 rounded-md transition-colors hover:bg-muted"
                        title={srv.enabled ? "Disable" : "Enable"}
                      >
                        <div className={`w-2 h-2 rounded-full ${srv.enabled ? 'bg-emerald-500' : 'border-2 border-muted-foreground/40 bg-transparent'}`} />
                      </button>
                    </td>
                    <td className="px-4 py-3.5">{modeBadge(srv)}</td>
                    <td className="px-4 py-3.5">
                      <div>
                        <p className="font-semibold text-sm text-foreground flex items-center gap-1">
                          {srv.expose_mode === 'tunnel' ? publicHost : (srv.subdomain || srv.domain)}
                          {srv.status === 'error' && srv.enabled && (
                            <AlertTriangle className="w-3 h-3 text-destructive shrink-0" />
                          )}
                        </p>
                        <a href={`https://${publicHost}`} target="_blank" rel="noreferrer" className="text-xs font-mono text-muted-foreground hover:text-primary transition-colors hover:underline truncate block max-w-[220px]">
                          {publicHost}
                        </a>
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-1 text-xs font-mono">
                        <span className="text-foreground bg-muted border border-border px-1.5 py-0.5 rounded">{srv.target_ip}</span>
                        <span className="text-muted-foreground">:</span>
                        <span className="text-primary bg-primary/10 border border-primary/20 px-1.5 py-0.5 rounded">{srv.target_port}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex flex-col gap-1">
                        {proxyProvider && (
                          <span className="text-[11px] font-medium text-muted-foreground">{proxyProvider.name}</span>
                        )}
                        {dnsProvider && dnsProvider.id !== proxyProvider?.id && (
                          <span className="text-[11px] font-medium text-muted-foreground opacity-70">{dnsProvider.name} <span className="text-[10px]">(DNS)</span></span>
                        )}
                        {!proxyProvider && !dnsProvider && <span className="text-xs text-muted-foreground/50">—</span>}
                        {drift && (
                          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border w-fit ${driftIssues === 0 ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20' : 'bg-destructive/10 text-destructive border-destructive/20'}`}>
                            drift {driftIssues > 0 ? `${driftIssues} issue` : 'ok'}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-right opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                      <div className="flex items-center justify-end gap-1">
                        {actioningIds.has(srv.id) ? (
                          <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                        ) : (
                          <>
                            <button onClick={() => checkDrift.mutate(Number(srv.id))} className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md transition-colors" title="Check drift">
                              <RefreshCw className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={() => reconcileService.mutate(Number(srv.id))} className="p-1.5 text-muted-foreground hover:text-primary hover:bg-primary/10 rounded-md transition-colors" title="Reconcile">
                              <ShieldCheck className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={() => setEditingService(srv)} className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md transition-colors" title="Edit">
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                            <button onClick={async () => { if (await confirm({ title: 'Delete service', message: `Delete route for ${publicHost}?`, confirmLabel: 'Delete', variant: 'danger' })) deleteService.mutate(srv.id); }} className="p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-md transition-colors" title="Delete">
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredServices.map((srv: Service) => {
            const fullUrl = srv.subdomain ? `${srv.subdomain}.${srv.domain}` : srv.domain;
            const publicHost = srv.expose_mode === 'tunnel' && srv.tunnel_hostname ? srv.tunnel_hostname : fullUrl;
            const proxyProvider = getProvider(srv.expose_mode === 'tunnel' ? srv.tunnel_provider_id : srv.proxy_provider_id);
            const dnsProvider = srv.expose_mode !== 'tunnel' ? getProvider(srv.dns_provider_id) : null;
            const provider = proxyProvider || dnsProvider;
            const drift = driftByService[Number(srv.id)];
            const driftIssues = Array.isArray(drift?.issues) ? drift.issues.length : 0;
            
            return (
              <div 
                key={srv.id}
                className={`${cardClass} p-6 flex flex-col hover:shadow-md transition-shadow group relative overflow-hidden ${selectedIds.has(srv.id) ? 'ring-2 ring-primary/40' : ''}`}
              >
                <div className={`absolute top-0 inset-x-0 h-1 transition-colors ${srv.enabled ? (srv.status === 'error' ? 'bg-destructive' : 'bg-emerald-500') : 'bg-border'}`}></div>

                <div className="flex items-start justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <button onClick={() => toggleSelect(srv.id)} className="p-0.5 text-muted-foreground hover:text-foreground transition-colors">
                      {selectedIds.has(srv.id)
                        ? <CheckSquare className="w-4 h-4 text-primary" />
                        : <Square className="w-4 h-4 opacity-0 group-hover:opacity-100 transition-opacity" />}
                    </button>
                    <button 
                      onClick={() => toggleStatus.mutate({ service: srv, enabled: !srv.enabled })}
                      className="p-1 transition-colors group-hover:bg-muted rounded"
                      title={srv.enabled ? 'Disable' : 'Enable'}
                    >
                      <div className={`w-2.5 h-2.5 rounded-full ${srv.enabled ? 'bg-emerald-500' : 'border-2 border-muted-foreground/40 bg-transparent'}`} />
                    </button>
                  </div>

                  <div className="flex gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                    {actioningIds.has(srv.id) ? (
                      <div className="p-1.5"><Loader2 className="w-4 h-4 animate-spin text-muted-foreground" /></div>
                    ) : (
                      <>
                        <button
                          onClick={() => checkDrift.mutate(Number(srv.id))}
                          className="p-1.5 text-muted-foreground hover:text-foreground rounded bg-muted hover:bg-accent transition-colors"
                          title="Check drift"
                        >
                          <RefreshCw className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => reconcileService.mutate(Number(srv.id))}
                          className="p-1.5 text-muted-foreground hover:text-primary rounded bg-muted hover:bg-primary/10 transition-colors"
                          title="Reconcile"
                        >
                          <ShieldCheck className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => setEditingService(srv)} className="p-1.5 text-muted-foreground hover:text-foreground rounded bg-muted hover:bg-accent transition-colors" title="Edit"><Pencil className="w-3.5 h-3.5" /></button>
                        <button onClick={async () => { if (await confirm({ title: 'Delete service', message: `Delete route for ${publicHost}?`, confirmLabel: 'Delete', variant: 'danger' })) deleteService.mutate(srv.id); }} className="p-1.5 text-muted-foreground hover:text-destructive rounded bg-muted hover:bg-destructive/10 transition-colors"><Trash2 className="w-3.5 h-3.5" /></button>
                      </>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1.5 mb-1">
                  <h3 className="font-bold text-foreground text-lg truncate" title={srv.expose_mode === 'tunnel' ? publicHost : (srv.subdomain ? srv.subdomain : srv.domain)}>
                    {srv.expose_mode === 'tunnel' ? publicHost : (srv.subdomain ? srv.subdomain : srv.domain)}
                  </h3>
                  {srv.status === 'error' && srv.enabled && (
                    <AlertTriangle className="w-4 h-4 text-destructive shrink-0" />
                  )}
                </div>
                <div className="mb-1">{modeBadge(srv)}</div>
                <a href={`https://${publicHost}`} target="_blank" rel="noreferrer" className="text-sm font-mono text-muted-foreground hover:text-primary transition-colors hover:underline mb-1 truncate block w-full">
                  {publicHost}
                </a>
                
                {provider && (
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-widest mt-1 mb-6">
                    Routed via {provider.name}
                    {drift && <span className={driftIssues === 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'}>• drift {driftIssues}</span>}
                  </div>
                )}

                <div className="mt-auto pt-4 border-t border-border bg-muted/40 -mx-6 -mb-6 px-6 py-4 flex items-center justify-between">
                   <span className="text-xs font-semibold text-muted-foreground uppercase">Target</span>
                   <div className="flex items-center gap-1 text-xs">
                      <span className="font-mono font-medium text-foreground">{srv.target_ip}</span>
                    <span className="text-muted-foreground">:</span>
                    <span className="font-mono font-semibold text-primary">{srv.target_port}</span>
                   </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Bulk Action Bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-card border border-border shadow-xl rounded-xl px-5 py-3 animate-in slide-in-from-bottom-4 duration-200">
          <span className="text-sm font-semibold text-foreground whitespace-nowrap">
            {selectedIds.size} selected
          </span>
          <div className="w-px h-5 bg-border" />
          <button
            onClick={() => bulkAction.mutate({ ids: [...selectedIds], action: 'enable' })}
            disabled={bulkAction.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-primary/10 text-primary hover:bg-primary/20 transition-colors disabled:opacity-50"
          >
            <Power className="w-3.5 h-3.5" /> Enable
          </button>
          <button
            onClick={() => bulkAction.mutate({ ids: [...selectedIds], action: 'disable' })}
            disabled={bulkAction.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-muted text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-50"
          >
            <PowerOff className="w-3.5 h-3.5" /> Disable
          </button>
          <button
            onClick={async () => {
              if (await confirm({
                title: 'Delete multiple services',
                message: `Delete ${selectedIds.size} service(s)? This will remove all provider routes.`,
                confirmLabel: 'Delete all',
                variant: 'danger',
              })) {
                bulkAction.mutate({ ids: [...selectedIds], action: 'delete' });
              }
            }}
            disabled={bulkAction.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-destructive/10 text-destructive hover:bg-destructive/20 transition-colors disabled:opacity-50"
          >
            <Trash2 className="w-3.5 h-3.5" /> Delete
          </button>
          <div className="w-px h-5 bg-border" />
          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1.5 text-muted-foreground hover:text-foreground rounded-md hover:bg-muted transition-colors"
            title="Clear selection (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <ExposeModal
        key={routeModalKey}
        isOpen={isRouteModalOpen}
        onClose={closeRouteModal}
        mode={routeModalMode as 'create' | 'edit'}
        service={editingService as Record<string, unknown> | null}
      />

      {ConfirmDialogElement}
    </div>
  );
}
