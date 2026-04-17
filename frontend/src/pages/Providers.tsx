import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";
import { Settings, Activity, KeySquare, Plus, AlertCircle, RefreshCw, X, ShieldAlert, Trash2, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { ProviderModal } from "@/components/features/ProviderModal";
import { useConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ProviderLogo } from "@/components/ui/ProviderLogos";
import type { Provider, TunnelHealthResponse, TunnelHealthItem } from "@/types/api";

interface ProviderDiagnostics {
  ok?: boolean;
  provider?: string;
  validation?: { checks?: Array<{ name: string; ok: boolean; blocking: boolean; detail?: string }> };
  health?: { ok?: boolean; status?: string; error?: string };
}

export function Providers() {
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [providerDiagnostics, setProviderDiagnostics] = useState<Record<number, ProviderDiagnostics>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [validatingId, setValidatingId] = useState<number | null>(null);

  const { data: providers, isLoading } = useQuery({
    queryKey: ['providers'],
    queryFn: () => api.get('/providers'),
  });

  const { data: providerTypes } = useQuery<Record<string, Record<string, unknown>>>({
    queryKey: ['provider-types'],
    queryFn: () => api.get('/providers/types'),
  });

  const { data: tunnelHealth } = useQuery<TunnelHealthResponse>({
    queryKey: ['providers-tunnel-health'],
    queryFn: () => api.get('/providers/tunnels/health'),
    refetchInterval: 30000,
  });

  // Auto-fetch health for all enabled providers on page load
  const { data: allHealth } = useQuery<{ items: Record<number, { ok?: boolean; status?: string; error?: string }> }>({
    queryKey: ['providers-health'],
    queryFn: () => api.get('/providers/health'),
    refetchInterval: 60000,
  });

  const testConnection = useMutation({
    mutationFn: (id: number) => { setTestingId(id); return api.post(`/providers/${id}/test`) as Promise<ProviderDiagnostics>; },
    onSuccess: (data: ProviderDiagnostics) => {
      const ok = Boolean(data?.ok);
      const providerName = data?.provider ? ` (${data.provider})` : "";

      if (data?.provider) {
        const matchedProvider = providersList.find((p: Provider) => p.name === data.provider);
        if (matchedProvider?.id) {
          setProviderDiagnostics((prev) => ({
            ...prev,
            [matchedProvider.id]: data,
          }));
        }
      }

      if (ok) {
        toast.success(`Connection test successful${providerName}!`);
      } else {
        toast.error(`Connection failed${providerName}`);
      }

      setTestingId(null);
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
    onError: (error: { response?: { data?: { detail?: string } } }) => {
      const msg = error?.response?.data?.detail || "Connection failed";
      toast.error(msg);
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
        [id]: data,
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

  const getHealthScore = (provider: Provider): { score: number; label: string; color: string } => {
    const diag = providerDiagnostics[provider.id];
    const tunnelH = tunnelHealthById[provider.id];
    const autoHealth = allHealth?.items?.[provider.id];
    if (!diag && !tunnelH && !autoHealth) return { score: -1, label: 'Unknown', color: 'text-muted-foreground' };

    let score = 100;
    // Connection test result (manual)
    if (diag) {
      if (!diag.ok) score -= 50;
      if (diag.validation?.checks) {
        const blocking = diag.validation.checks.filter(c => c.blocking && !c.ok).length;
        const warnings = diag.validation.checks.filter(c => !c.blocking && !c.ok).length;
        score -= blocking * 25;
        score -= warnings * 5;
      }
      if (diag.health && !diag.health.ok) score -= 30;
    } else if (autoHealth) {
      // Auto-fetched health (no manual test yet)
      if (!autoHealth.ok) score = 20;
    }
    // Tunnel health
    if (tunnelH) {
      const status = String((tunnelH as Record<string, unknown>)?.status || '');
      if (status === 'healthy') score = Math.max(score, 90);
      else if (status === 'degraded') score = Math.min(score, 60);
      else if (status === 'down') score = Math.min(score, 20);
    }
    if (!provider.enabled) score = Math.min(score, 30);

    score = Math.max(0, Math.min(100, score));
    if (score >= 80) return { score, label: 'Healthy', color: 'text-emerald-600 dark:text-emerald-400' };
    if (score >= 50) return { score, label: 'Degraded', color: 'text-yellow-600 dark:text-yellow-400' };
    return { score, label: 'Unhealthy', color: 'text-destructive' };
  };

  const cardClass = "bg-card border border-border rounded-xl shadow-sm";

  const providersList = Array.isArray(providers) ? providers : [];
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

  const reverseProviders = providersList.filter((p: Provider) => hasCapability(p, 'proxy'));
  const dnsProviders = providersList.filter((p: Provider) => hasCapability(p, 'dns'));
  const tunnelProviders = providersList.filter((p: Provider) => String(p.type || '').toLowerCase() === 'cloudflare_tunnel');
  const otherProviders = providersList.filter((p: Provider) => 
    !hasCapability(p, 'proxy') && 
    !hasCapability(p, 'dns') && 
    String(p.type || '').toLowerCase() !== 'cloudflare_tunnel'
  );

  const providerSections = [
    { id: 'reverse', title: 'Reverse Proxies', items: reverseProviders },
    { id: 'tunnel', title: 'Tunnels', items: tunnelProviders },
    { id: 'dns', title: 'DNS Providers', items: dnsProviders },
    { id: 'other', title: 'Other', items: otherProviders },
  ].filter((section) => section.items.length > 0);

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
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Integrations</h1>
        
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ['providers'] });
              queryClient.invalidateQueries({ queryKey: ['providers-tunnel-health'] });
              queryClient.invalidateQueries({ queryKey: ['providers-health'] });
            }}
            className="flex items-center justify-center gap-2 px-3 py-2.5 text-sm rounded-lg font-semibold transition-all border border-border bg-card hover:bg-accent text-foreground"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button 
            onClick={() => setIsModalOpen(true)}
            className="flex items-center justify-center gap-2 bg-primary hover:opacity-90 text-primary-foreground px-5 py-2.5 text-sm rounded-lg font-semibold transition-all shadow-sm focus:ring-2 focus:ring-primary/30 outline-none"
          >
            <Plus className="w-4 h-4" />
            Add connection
          </button>
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
      ) : (
        <div className="space-y-6">
          {providerSections.map((section) => (
            <section key={section.id} className="space-y-3">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">{section.title}</h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {section.items.map((provider: Provider) => (
                  <div key={provider.id} className={`${cardClass} flex flex-col hover:shadow-md transition-shadow group relative overflow-hidden`}>
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

                              <div className="flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                                <button
                                  onClick={() => testConnection.mutate(provider.id)}
                                  disabled={testConnection.isPending}
                                  className="p-1.5 text-muted-foreground hover:text-primary hover:bg-primary/10 rounded-lg transition-colors border border-transparent hover:border-primary/20"
                                  title="Test connection"
                                >
                                  <RefreshCw className={`w-4 h-4 ${testingId === provider.id && testConnection.isPending ? 'animate-spin text-primary' : ''}`} />
                                </button>
                                <button
                                  onClick={() => validateProvider.mutate(Number(provider.id))}
                                  disabled={validatingId === provider.id}
                                  className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg transition-colors border border-transparent hover:border-border"
                                  title="Validate permissions"
                                >
                                  <ShieldAlert className={`w-4 h-4 ${validatingId === provider.id ? 'animate-pulse text-primary' : ''}`} />
                                </button>
                                <button
                                  onClick={() => setEditingProvider(provider)}
                                    className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-accent rounded-lg transition-colors border border-transparent hover:border-border"
                                  title="Edit integration"
                                >
                                  <Settings className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => handleDeleteProvider(Number(provider.id), provider.name)}
                                  disabled={deletingId === provider.id}
                                  className="p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg transition-colors border border-transparent hover:border-destructive/20"
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
                                const diagnostics = providerDiagnostics[Number(provider.id)];
                                const tunnelHealthState = tunnelHealthById[Number(provider.id)];
                                const autoHealthState = allHealth?.items?.[Number(provider.id)];
                                const health = (tunnelHealthState || diagnostics?.health || autoHealthState) as Record<string, unknown> | undefined;
                                if (!diagnostics && !health) return null;

                                const checks = Array.isArray(diagnostics?.validation?.checks)
                                  ? diagnostics.validation.checks
                                  : [];
                                const blockingFailures = checks.filter((c: { blocking?: boolean; ok?: boolean }) => c?.blocking && !c?.ok).length;

                                return (
                                  <div className="rounded-md border border-border bg-muted/40 p-2 text-xs space-y-1">
                                    {Boolean(health?.status) && (
                                      <p className="text-muted-foreground">
                                        Health: <span className="font-semibold text-foreground">{String(health?.status)}</span>
                                      </p>
                                    )}
                                    {checks.length > 0 && (
                                      <p className={blockingFailures === 0 ? 'text-primary' : 'text-destructive'}>
                                        Validation: {blockingFailures === 0 ? 'OK' : `${blockingFailures} blocking issue(s)`}
                                      </p>
                                    )}
                                    {Boolean(health?.error) && <p className="text-destructive truncate">{String(health?.error)}</p>}
                                  </div>
                                );
                              })()}
                            </div>
                          </div>

                            <div className="mt-auto border-t border-border bg-muted/30 px-6 py-3.5 flex items-center justify-between text-xs">
                              <div className="flex items-center gap-1.5 text-muted-foreground">
                                <Activity className="w-3.5 h-3.5 text-muted-foreground" />
                              <span>{provider.enabled ? 'Active' : 'Disabled'}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              {(() => {
                                const health = getHealthScore(provider);
                                return (
                                  <span className={`text-xs font-semibold ${health.color}`} title={health.score >= 0 ? `Health: ${health.score}/100` : 'Not tested'}>
                                    {health.score >= 0 ? health.label : 'Not tested'}
                                  </span>
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
