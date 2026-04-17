import { Monitor, DownloadCloud, Database, FileTerminal, Settings as SettingsIcon, Globe, RefreshCw, Loader2, AlertTriangle, CheckCircle2, AlertCircle, Trash2, Key, Bell, Copy, Plus, Eye, EyeOff, Tag, Layers } from "lucide-react";
import { useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { Service, LogsResponse, SyncResult, SyncProxyHost, SyncDnsRewrite, ApiKey, ApiKeyCreated } from "@/types/api";
import { useTheme } from "@/theme";
import { toast } from "react-hot-toast";
import { GeneralTab, HowtoTab } from "@/components/features/settings";
import { useWebhookActions } from "@/hooks/useWebhookActions";

export function Settings() {
  const VALID_TABS = ["general", "howto", "dns", "tags", "environments", "apikeys", "webhooks", "migration", "backup", "logs"];
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = VALID_TABS.includes(searchParams.get("tab") || "") ? searchParams.get("tab")! : "general";
  const setActiveTab = (tab: string) => setSearchParams({ tab }, { replace: true });
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [newDomain, setNewDomain] = useState("");
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyScopes, setNewKeyScopes] = useState<string[]>(["read"]);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [showKeySecret, setShowKeySecret] = useState(false);
  const [newTagName, setNewTagName] = useState("");
  const [newTagColor, setNewTagColor] = useState("blue");
  const [newEnvName, setNewEnvName] = useState("");
  const [newEnvColor, setNewEnvColor] = useState("green");

  // local TLD patterns considered non-public
  const LOCAL_TLDS = ['.lan', '.local', '.home', '.internal', '.localdomain', '.arpa'];
  const isLocalDomain = (domain: string) => LOCAL_TLDS.some((tld) => domain.endsWith(tld));

  const TAG_COLORS = ["blue","teal","green","red","orange","purple","cyan","yellow","pink","lime","indigo"];
  const ENV_COLORS = TAG_COLORS;

  const { data: logs } = useQuery<LogsResponse>({
    queryKey: ['logs'],
    queryFn: () => api.get('/logs?per_page=100'),
    enabled: activeTab === 'logs'
  });

  const { data: domains } = useQuery({
    queryKey: ['domains'],
    queryFn: () => api.get('/domains'),
    enabled: activeTab === 'dns'
  });

  const { data: settingsData } = useQuery<Record<string, string>>({
    queryKey: ['settings'],
    queryFn: () => api.get('/settings'),
    enabled: activeTab === 'general',
  });

  // Services list for deduplication in Import & Sync, and domain dependency count
  const { data: existingServices = [] } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get('/services'),
    enabled: activeTab === 'migration' || activeTab === 'dns',
  });

  // API Keys
  const { data: apiKeys = [] } = useQuery<ApiKey[]>({
    queryKey: ['api-keys'],
    queryFn: () => api.get('/settings/api-keys'),
    enabled: activeTab === 'apikeys',
  });

  const createKeyMutation = useMutation({
    mutationFn: (body: { name: string; scopes: string[] }) => api.post<ApiKeyCreated>('/settings/api-keys', body),
    onSuccess: (data) => {
      setCreatedKey(data.key);
      setNewKeyName("");
      setNewKeyScopes(["read"]);
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success('API key created');
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || 'Failed to create API key'),
  });

  const revokeKeyMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/settings/api-keys/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success('API key revoked');
    },
  });

  // Webhooks
  const {
    webhooks, name: newWebhookName, setName: setNewWebhookName,
    url: newWebhookUrl, setUrl: setNewWebhookUrl,
    addWebhook: addWebhookMutation, deleteWebhook: deleteWebhookMutation,
    testWebhookById: testWebhookMutation, toggleWebhook: toggleWebhookMutation,
  } = useWebhookActions();

  // Tags
  interface TagItem { id: number; name: string; color: string }
  const { data: tags = [] } = useQuery<TagItem[]>({
    queryKey: ['tags'],
    queryFn: () => api.get('/tags'),
    enabled: activeTab === 'tags',
  });
  const createTagMutation = useMutation({
    mutationFn: (body: { name: string; color: string }) => api.post('/tags', body),
    onSuccess: () => { setNewTagName(""); queryClient.invalidateQueries({ queryKey: ['tags'] }); toast.success('Tag created'); },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || 'Failed to create tag'),
  });
  const deleteTagMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/tags/${id}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['tags'] }); toast.success('Tag deleted'); },
  });

  // Environments
  interface EnvItem { id: number; name: string; color: string }
  const { data: environments = [] } = useQuery<EnvItem[]>({
    queryKey: ['environments'],
    queryFn: () => api.get('/environments'),
    enabled: activeTab === 'environments',
  });
  const createEnvMutation = useMutation({
    mutationFn: (body: { name: string; color: string }) => api.post('/environments', body),
    onSuccess: () => { setNewEnvName(""); queryClient.invalidateQueries({ queryKey: ['environments'] }); toast.success('Environment created'); },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || 'Failed to create environment'),
  });
  const deleteEnvMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/environments/${id}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['environments'] }); toast.success('Environment deleted'); },
  });

  // Build set of existing public_hosts for fast dedup lookup
  const existingPublicHosts = useMemo(() => {
    const hosts = new Set<string>();
    for (const svc of existingServices) {
      const host = svc.public_host || `${svc.subdomain}.${svc.domain}`;
      if (host) hosts.add(host.toLowerCase());
    }
    return hosts;
  }, [existingServices]);

  const savePolicyMutation = useMutation({
    mutationFn: (payload: Record<string, string>) => api.post('/settings', payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      toast.success('WAN detection policy saved');
    },
    onError: (error: unknown) => {
      const axErr = error as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Unable to save WAN policy');
    },
  });

  const addDomainMutation = useMutation({
    mutationFn: (domain: string) => api.post('/domains', { name: domain }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      setNewDomain("");
    }
  });

  const deleteDomainMutation = useMutation({
    mutationFn: (domain: string) => api.delete(`/domains/${domain}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domains'] }),
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || 'Failed to delete domain'),
  });

  const syncMutation = useMutation({
    mutationFn: () => api.post<SyncResult>('/services/sync'),
    onSuccess: (data) => {
      setSyncResult(data);
      setSelectedRows(new Set());
    }
  });

  const importMutation = useMutation<{ imported: number; errors: string[] }, Error, unknown>({
    mutationFn: (payload) => api.post<{ imported: number; errors: string[] }>('/services/import', payload),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      if (data.imported > 0) {
        toast.success(`Imported ${data.imported} service${data.imported > 1 ? 's' : ''} successfully`);
      } else if (data.errors && data.errors.length > 0) {
        toast.error(`${data.errors.length} service(s) already exist or failed`);
      } else {
        toast.success('Sync complete — all services already imported');
      }
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Import failed');
    },
  });

  const clearLogsMutation = useMutation({
    mutationFn: () => api.post('/logs/clear'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['logs'] });
    },
  });

  const generateBackupMutation = useMutation({
    mutationFn: () => api.get('/backup', { responseType: 'blob' }),
    onSuccess: (data: unknown) => {
      const url = window.URL.createObjectURL(new Blob([data as BlobPart]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `vauxtra_backup_${new Date().toISOString().split('T')[0]}.json`);
      document.body.appendChild(link);
      link.click();
      window.URL.revokeObjectURL(url);
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || 'Failed to generate backup'),
  });

  const restoreBackupMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.post('/restore', payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      queryClient.invalidateQueries();
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || 'Failed to restore backup'),
  });

  // Build enriched rows from sync result for the preview table
  const syncRows = useMemo(() => {
    if (!syncResult) return [];
    type SyncRow = {
      key: string;
      subdomain: string;
      domain: string;
      target: string;
      provider: string;
      publicHost: string;
      isLocal: boolean;
      status: 'new' | 'exists' | 'conflict';
      raw: SyncProxyHost | SyncDnsRewrite;
    };
    const rows: SyncRow[] = [];
    const seen = new Set<string>();

    const push = (item: SyncProxyHost | SyncDnsRewrite, providerLabel: string) => {
      const proxyItem = item as SyncProxyHost;
      const dnsItem = item as SyncDnsRewrite;
      const subdomain = (item.subdomain || proxyItem.domain_names?.[0]?.split('.')[0] || proxyItem.domains?.[0]?.split('.')[0] || '').toLowerCase();
      const domain = (item.domain || (proxyItem.domain_names?.[0]?.split('.').slice(1).join('.')) || (proxyItem.domains?.[0]?.split('.').slice(1).join('.')) || '').toLowerCase();
      const target = (proxyItem.forward_host || proxyItem.host)
        ? `${proxyItem.forward_host || proxyItem.host}:${proxyItem.forward_port || proxyItem.port || ''}`
        : (dnsItem.answer || dnsItem.target || '') as string;
      const publicHost = `${subdomain}.${domain}`.replace(/^\./, '');
      const key = publicHost;
      if (seen.has(key)) return;
      seen.add(key);

      const local = isLocalDomain(domain) || isLocalDomain(publicHost);
      let status: SyncRow['status'] = 'new';
      if (existingPublicHosts.has(publicHost)) status = 'exists';

      rows.push({ key, subdomain, domain, target, provider: providerLabel, publicHost, isLocal: local, status, raw: item });
    };

    if (Array.isArray(syncResult.proxy_hosts)) {
      for (const h of syncResult.proxy_hosts) push(h, h._provider_name || 'Proxy');
    }
    if (Array.isArray(syncResult.dns_rewrites)) {
      for (const h of syncResult.dns_rewrites) push(h, h._provider_name || 'DNS');
    }

    return rows;
  }, [syncResult, existingPublicHosts]);

  // Pre-select all "new" rows whenever syncRows changes
  const allNewKeys = useMemo(() => syncRows.filter(r => r.status === 'new').map(r => r.key), [syncRows]);

  const tabs = [
    { id: "general", label: "General", icon: SettingsIcon, group: "Preferences" },
    { id: "dns", label: "DNS Domains", icon: Globe, group: "Preferences" },
    { id: "tags", label: "Tags", icon: Tag, group: "Organization" },
    { id: "environments", label: "Environments", icon: Layers, group: "Organization" },
    { id: "migration", label: "Import & Sync", icon: RefreshCw, group: "Data" },
    { id: "backup", label: "Backup & Restore", icon: Database, group: "Data" },
    { id: "apikeys", label: "API Keys", icon: Key, group: "Security" },
    { id: "webhooks", label: "Webhooks", icon: Bell, group: "Security" },
    { id: "howto", label: "How-To & API", icon: Monitor, group: "Help" },
    { id: "logs", label: "System Logs", icon: FileTerminal, group: "Help" },
  ];

  const groups = ["Preferences", "Organization", "Data", "Security", "Help"];

  return (
    <div className="animate-in fade-in duration-300 max-w-7xl mx-auto">
      {/* Mobile tabs */}
      <div className="lg:hidden mb-6">
        <h2 className="text-xl font-semibold text-foreground mb-4">Settings</h2>
        <div className="flex border-b border-border overflow-x-auto hide-scrollbar">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 border-b-2 font-medium text-sm transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-6">
        {/* Desktop Sidebar */}
        <aside className="w-56 shrink-0 hidden lg:block">
          <div className="sticky top-6 space-y-1">
            <h2 className="text-lg font-semibold text-foreground mb-4">Settings</h2>
            {groups.map((group) => {
              const groupTabs = tabs.filter(t => t.group === group);
              if (groupTabs.length === 0) return null;
              return (
                <div key={group} className="mb-4">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground mb-2 px-3">{group}</p>
                  {groupTabs.map((tab) => (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id)}
                      className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                        activeTab === tab.id
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground hover:text-foreground hover:bg-muted"
                      }`}
                    >
                      <tab.icon className="w-4 h-4" />
                      {tab.label}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </aside>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {activeTab === "general" && (
            <GeneralTab
              theme={theme}
              resolvedTheme={resolvedTheme}
              setTheme={(t) => setTheme(t as 'light' | 'dark' | 'system')}
              settingsData={settingsData}
              savePolicyMutation={savePolicyMutation}
            />
        )}

        {activeTab === "howto" && (
          <HowtoTab />
        )}

        {activeTab === "dns" && (
          <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                <Globe className="w-5 h-5 text-muted-foreground" />
                Root Domains
              </h3>
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">Manage the root domains used globally across your reversed proxied services and Pi-hole / AdGuard setups.</p>
                
                <form 
                  onSubmit={(e) => { 
                    e.preventDefault(); 
                    const trimmed = newDomain.trim().toLowerCase();
                    if (!trimmed) return;
                    if (Array.isArray(domains) && domains.includes(trimmed)) {
                      toast.error(`Domain "${trimmed}" already exists`);
                      return;
                    }
                    addDomainMutation.mutate(trimmed); 
                  }} 
                  className="flex gap-2"
                >
                  <input 
                    value={newDomain} 
                    onChange={e => setNewDomain(e.target.value)} 
                    placeholder="example.com" 
                    required 
                    className="flex-1 p-2 rounded-md bg-input border border-border" 
                  />
                  <button type="submit" disabled={addDomainMutation.isPending} className="bg-primary text-primary-foreground px-4 rounded-md">Add Domain</button>
                </form>

                <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-6">
                  {Array.isArray(domains) && domains.map(domain => {
                    const depCount = existingServices.filter((s) => s.domain === domain).length;
                    return (
                    <div key={domain} className="flex justify-between items-center p-3 bg-muted rounded-md border border-border">
                      <div>
                        <span className="font-medium">{domain}</span>
                        {depCount > 0 && (
                          <p className="text-[11px] text-muted-foreground">{depCount} service{depCount > 1 ? 's' : ''}</p>
                        )}
                      </div>
                      <button
                        onClick={async () => {
                          const hasServices = depCount > 0;
                          const confirmed = await confirm({
                            title: hasServices ? 'Domain has services' : 'Delete domain',
                            message: hasServices
                              ? `${depCount} service${depCount > 1 ? 's' : ''} use${depCount === 1 ? 's' : ''} "${domain}". Deleting it may break existing routes.\n\nDelete anyway?`
                              : `Delete domain "${domain}"?`,
                            confirmLabel: 'Delete',
                            variant: hasServices ? 'warning' : 'danger',
                          });
                          if (confirmed) deleteDomainMutation.mutate(domain);
                        }}
                        className="text-muted-foreground hover:text-destructive p-1"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                    );
                  })}
                </div>
              </div>
          </div>
        )}

        {activeTab === "tags" && (
          <div className="space-y-6">
            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <Tag className="w-5 h-5 text-muted-foreground" />
                Service Tags
              </h3>
              <p className="text-sm text-muted-foreground mb-4">Create tags to organize and filter your services.</p>
              <form
                onSubmit={e => {
                  e.preventDefault();
                  if (!newTagName.trim()) return;
                  createTagMutation.mutate({ name: newTagName.trim(), color: newTagColor });
                }}
                className="flex items-center gap-2 mb-4"
              >
                <input
                  value={newTagName}
                  onChange={e => setNewTagName(e.target.value)}
                  placeholder="Tag name"
                  required
                  className="flex-1 bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <select
                  value={newTagColor}
                  onChange={e => setNewTagColor(e.target.value)}
                  className="bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  aria-label="Tag color"
                >
                  {TAG_COLORS.map(c => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <button
                  type="submit"
                  disabled={createTagMutation.isPending}
                  className="bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-60 px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-1"
                >
                  <Plus className="w-4 h-4" /> Add
                </button>
              </form>
              {tags.length === 0 ? (
                <p className="text-sm text-muted-foreground">No tags yet.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {tags.map(tag => (
                    <span
                      key={tag.id}
                      className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium border"
                      style={{
                        backgroundColor: `color-mix(in srgb, ${tag.color} 15%, transparent)`,
                        borderColor: `color-mix(in srgb, ${tag.color} 40%, transparent)`,
                        color: tag.color,
                      }}
                    >
                      {tag.name}
                      <button
                        onClick={() => deleteTagMutation.mutate(tag.id)}
                        className="ml-1 hover:opacity-70"
                        aria-label={`Delete tag ${tag.name}`}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "environments" && (
          <div className="space-y-6">
            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <Layers className="w-5 h-5 text-muted-foreground" />
                Environments
              </h3>
              <p className="text-sm text-muted-foreground mb-4">Define deployment environments for your services (e.g. production, staging, development).</p>
              <form
                onSubmit={e => {
                  e.preventDefault();
                  if (!newEnvName.trim()) return;
                  createEnvMutation.mutate({ name: newEnvName.trim(), color: newEnvColor });
                }}
                className="flex items-center gap-2 mb-4"
              >
                <input
                  value={newEnvName}
                  onChange={e => setNewEnvName(e.target.value)}
                  placeholder="Environment name"
                  required
                  className="flex-1 bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <select
                  value={newEnvColor}
                  onChange={e => setNewEnvColor(e.target.value)}
                  className="bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  aria-label="Environment color"
                >
                  {ENV_COLORS.map(c => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <button
                  type="submit"
                  disabled={createEnvMutation.isPending}
                  className="bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-60 px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-1"
                >
                  <Plus className="w-4 h-4" /> Add
                </button>
              </form>
              {environments.length === 0 ? (
                <p className="text-sm text-muted-foreground">No environments yet.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {environments.map(env => (
                    <span
                      key={env.id}
                      className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium border"
                      style={{
                        backgroundColor: `color-mix(in srgb, ${env.color} 15%, transparent)`,
                        borderColor: `color-mix(in srgb, ${env.color} 40%, transparent)`,
                        color: env.color,
                      }}
                    >
                      {env.name}
                      <button
                        onClick={() => deleteEnvMutation.mutate(env.id)}
                        className="ml-1 hover:opacity-70"
                        aria-label={`Delete environment ${env.name}`}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "migration" && (
          <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
            <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
              <RefreshCw className="w-5 h-5 text-muted-foreground" />
              Synchronization &amp; Import
            </h3>
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Fetch routes from your active providers (NPM, Traefik, Pi-hole…) and review what
                will be imported. Already-tracked services are highlighted so you never create
                duplicates.
              </p>

              <div className="flex flex-wrap gap-3 pt-2">
                <button
                  onClick={() => syncMutation.mutate()}
                  disabled={syncMutation.isPending}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground hover:opacity-90 rounded-md transition-colors font-medium text-sm shadow-sm"
                >
                  <RefreshCw className={`w-4 h-4 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                  {syncMutation.isPending ? 'Scanning providers…' : 'Scan providers'}
                </button>

                {syncRows.length > 0 && (
                  <button
                    onClick={async () => {
                      const newCount = allNewKeys.length;
                      const existsCount = syncRows.filter(r => r.status === 'exists').length;
                      const localCount = syncRows.filter(r => r.isLocal && r.status === 'new').length;
                      
                      let message = `Quick Import will import all ${newCount} new service${newCount !== 1 ? 's' : ''}.`;
                      if (existsCount > 0) message += `\n${existsCount} already-tracked service${existsCount !== 1 ? 's' : ''} will be skipped.`;
                      if (localCount > 0) message += `\n\n⚠ ${localCount} service${localCount !== 1 ? 's use' : ' uses'} a local TLD — external DNS will not resolve them.`;
                      
                      const confirmed = await confirm({
                        title: 'Quick Import',
                        message,
                        confirmLabel: 'Import',
                        variant: localCount > 0 ? 'warning' : 'info',
                      });
                      if (!confirmed) return;
                      importMutation.mutate(syncResult);
                    }}
                    disabled={importMutation.isPending || allNewKeys.length === 0}
                    className="flex items-center gap-2 px-4 py-2 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded-md transition-colors font-medium text-sm shadow-sm border border-border"
                  >
                    {importMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    {importMutation.isPending ? 'Importing…' : `Quick Import (${allNewKeys.length} new)`}
                  </button>
                )}
              </div>

              {/* Preview table */}
              {syncRows.length > 0 && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      Discovered — {syncRows.length} route{syncRows.length !== 1 ? 's' : ''}
                    </p>
                    <div className="flex gap-2 text-xs">
                      <button
                        className="text-primary hover:underline"
                        onClick={() => setSelectedRows(new Set(allNewKeys))}
                      >Select all new</button>
                      <span className="text-muted-foreground">·</span>
                      <button
                        className="text-muted-foreground hover:text-foreground"
                        onClick={() => setSelectedRows(new Set())}
                      >Clear</button>
                    </div>
                  </div>

                  <div className="rounded-lg border border-border overflow-hidden">
                    <table className="w-full text-xs">
                      <thead className="bg-muted/50 border-b border-border">
                        <tr>
                          <th className="w-8 px-3 py-2"></th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Subdomain</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Domain</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Target</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Provider</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {syncRows.map((row) => {
                          const isSelectable = row.status === 'new';
                          const checked = selectedRows.has(row.key);
                          return (
                            <tr
                              key={row.key}
                              className={`transition-colors ${isSelectable ? 'hover:bg-muted/30 cursor-pointer' : 'opacity-60'}`}
                              onClick={() => {
                                if (!isSelectable) return;
                                setSelectedRows((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(row.key)) next.delete(row.key); else next.add(row.key);
                                  return next;
                                });
                              }}
                            >
                              <td className="px-3 py-2">
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  disabled={!isSelectable}
                                  onChange={() => {}}
                                  className="rounded"
                                />
                              </td>
                              <td className="px-3 py-2 font-mono font-medium text-foreground">{row.subdomain || '—'}</td>
                              <td className="px-3 py-2 font-mono text-foreground">{row.domain || '—'}</td>
                              <td className="px-3 py-2 font-mono text-muted-foreground">{row.target || '—'}</td>
                              <td className="px-3 py-2 text-muted-foreground">{row.provider}</td>
                              <td className="px-3 py-2">
                                {row.status === 'exists' ? (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-muted text-muted-foreground border border-border">
                                    <CheckCircle2 className="w-3 h-3" /> tracked
                                  </span>
                                ) : row.isLocal ? (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/30"
                                    title="Local TLD — external DNS resolvers will not resolve this hostname">
                                    <AlertTriangle className="w-3 h-3" /> local
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-primary/10 text-primary border border-primary/20">
                                    <AlertCircle className="w-3 h-3" /> new
                                  </span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* Import selected */}
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => {
                        const toImport = syncRows.filter(r => selectedRows.has(r.key));
                        if (toImport.length === 0 || !syncResult) { return; }
                        const partialPayload = {
                          ...syncResult,
                          proxy_hosts: (syncResult.proxy_hosts || []).filter((h: SyncProxyHost) => {
                            const subdomain = (h.subdomain || h.domain_names?.[0]?.split('.')[0] || '').toLowerCase();
                            const domain = (h.domain || (h.domain_names?.[0]?.split('.').slice(1).join('.')) || '').toLowerCase();
                            return selectedRows.has(`${subdomain}.${domain}`);
                          }),
                          dns_rewrites: (syncResult.dns_rewrites || []).filter((h: SyncDnsRewrite) => {
                            const subdomain = (h.subdomain || '').toLowerCase();
                            const domain = (h.domain || '').toLowerCase();
                            return selectedRows.has(`${subdomain}.${domain}`);
                          }),
                        };
                        importMutation.mutate(partialPayload);
                      }}
                      disabled={importMutation.isPending || selectedRows.size === 0}
                      className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-60 font-medium"
                    >
                      {importMutation.isPending ? 'Importing…' : `Import selected (${selectedRows.size})`}
                    </button>

                    {importMutation.data && (
                      <p className="text-xs text-muted-foreground">
                        ✓ Imported: {importMutation.data.imported}
                        {importMutation.data.errors.length > 0 && (
                          <span className="text-destructive ml-2">Errors: {importMutation.data.errors.length}</span>
                        )}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {syncResult && syncRows.length === 0 && (
                <div className="mt-4 p-4 rounded-lg border border-border bg-muted/30 text-sm text-muted-foreground">
                  No routes discovered from providers.
                </div>
              )}

            </div>
          </div>
        )}

        {activeTab === "backup" && (
          <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                <DownloadCloud className="w-5 h-5 text-muted-foreground" />
                Data Management
              </h3>
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">Export your complete configuration including services, providers and settings to a JSON file.</p>
                <div className="flex gap-4">
                  <button 
                    onClick={() => generateBackupMutation.mutate()}
                    disabled={generateBackupMutation.isPending}
                    className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 rounded-md transition-colors font-medium text-sm"
                  >
                    <DownloadCloud className="w-4 h-4" /> Export Backup
                  </button>
                  <label className="flex items-center gap-2 px-4 py-2 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded-md cursor-pointer transition-colors border border-border font-medium text-sm">
                    <Database className="w-4 h-4" /> Import Backup
                    <input type="file" className="hidden" accept=".json" onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if(!file) return;
                      try {
                        const text = await file.text();
                        const json = JSON.parse(text);
                        const confirmed = await confirm({
                          title: 'Restore backup',
                          message: 'WARNING: Restoring a backup will DELETE all current services, providers, domains and settings, replacing them with the backup contents. This cannot be undone.',
                          confirmLabel: 'Restore',
                          variant: 'danger',
                        });
                        if (confirmed) {
                          restoreBackupMutation.mutate(json);
                        }
                      } catch {
                        toast.error("Invalid JSON format.");
                      }
                      // Reset input so same file can be selected again
                      e.target.value = '';
                    }} />
                  </label>
                </div>
              </div>
          </div>
        )}

        {activeTab === "apikeys" && (
          <div className="space-y-6">
            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <Key className="w-5 h-5 text-muted-foreground" />
                API Keys
              </h3>
              <p className="text-sm text-muted-foreground mb-6">
                API keys allow external tools (MCP, scripts) to authenticate with Vauxtra. Keys are shown only once at creation.
              </p>

              {createdKey && (
                <div className="bg-primary/5 border border-primary/30 rounded-lg p-4 mb-6">
                  <p className="text-sm font-medium text-foreground mb-2">New API key created — copy it now, it won't be shown again:</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 bg-muted px-3 py-2 rounded font-mono text-sm border border-border break-all">
                      {showKeySecret ? createdKey : createdKey.slice(0, 10) + '•'.repeat(30)}
                    </code>
                    <button
                      onClick={() => setShowKeySecret(!showKeySecret)}
                      className="p-2 rounded-md hover:bg-accent transition-colors"
                      title={showKeySecret ? 'Hide' : 'Reveal'}
                    >
                      {showKeySecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => { navigator.clipboard.writeText(createdKey); toast.success('Copied to clipboard'); }}
                      className="p-2 rounded-md hover:bg-accent transition-colors"
                      title="Copy"
                    >
                      <Copy className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              )}

              <div className="flex flex-col sm:flex-row gap-3 mb-6">
                <input
                  type="text"
                  placeholder="Key name (e.g. MCP, CI/CD)"
                  value={newKeyName}
                  onChange={e => setNewKeyName(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-lg border border-input bg-background text-sm"
                />
                <div className="flex items-center gap-3 text-sm">
                  {["read", "write", "admin"].map(scope => (
                    <label key={scope} className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={newKeyScopes.includes(scope)}
                        onChange={e => {
                          setNewKeyScopes(prev =>
                            e.target.checked ? [...prev, scope] : prev.filter(s => s !== scope)
                          );
                        }}
                        className="rounded"
                      />
                      <span className="capitalize">{scope}</span>
                    </label>
                  ))}
                </div>
                <button
                  onClick={() => {
                    if (!newKeyName.trim()) { toast.error('Name is required'); return; }
                    if (!newKeyScopes.length) { toast.error('Select at least one scope'); return; }
                    setCreatedKey(null);
                    setShowKeySecret(false);
                    createKeyMutation.mutate({ name: newKeyName, scopes: newKeyScopes });
                  }}
                  disabled={createKeyMutation.isPending}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
                >
                  <Plus className="w-4 h-4" />
                  Create Key
                </button>
              </div>

              {apiKeys.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">No API keys yet. Create one to enable MCP or script access.</p>
              ) : (
                <div className="space-y-2">
                  {apiKeys.map(key => (
                    <div key={key.id} className="flex items-center justify-between px-4 py-3 rounded-lg border border-border bg-background">
                      <div className="flex items-center gap-3 min-w-0">
                        <Key className="w-4 h-4 text-muted-foreground shrink-0" />
                        <div className="min-w-0">
                          <span className="font-medium text-sm">{key.name}</span>
                          <div className="flex items-center gap-2 mt-0.5">
                            <code className="text-xs text-muted-foreground font-mono">{key.prefix}•••</code>
                            <span className="text-xs text-muted-foreground">·</span>
                            {key.scopes.map(s => (
                              <span key={s} className="text-xs px-1.5 py-0.5 rounded bg-muted border border-border capitalize">{s}</span>
                            ))}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span className="text-xs text-muted-foreground hidden sm:inline">
                          {key.last_used_at ? `Last used ${key.last_used_at}` : 'Never used'}
                        </span>
                        <button
                          onClick={async () => {
                            if (await confirm({
                              title: 'Revoke API key',
                              message: `Revoke API key "${key.name}"? This cannot be undone.`,
                              confirmLabel: 'Revoke',
                              variant: 'danger',
                            }))
                              revokeKeyMutation.mutate(key.id);
                          }}
                          className="text-destructive hover:text-destructive/80 transition-colors p-1"
                          title="Revoke"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "webhooks" && (
          <div className="space-y-6">
            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <Bell className="w-5 h-5 text-muted-foreground" />
                Notification Webhooks
              </h3>
              <p className="text-sm text-muted-foreground mb-6">
                Webhooks use <a href="https://github.com/caronc/apprise" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Apprise</a> URLs to send notifications (Discord, Slack, Telegram, email, etc.).
              </p>

              <div className="flex flex-col sm:flex-row gap-3 mb-6">
                <input
                  type="text"
                  placeholder="Name (e.g. Discord alerts)"
                  value={newWebhookName}
                  onChange={e => setNewWebhookName(e.target.value)}
                  className="sm:w-48 px-3 py-2 rounded-lg border border-input bg-background text-sm"
                />
                <input
                  type="text"
                  placeholder="Apprise URL (e.g. discord://webhook_id/token)"
                  value={newWebhookUrl}
                  onChange={e => setNewWebhookUrl(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-lg border border-input bg-background text-sm font-mono"
                />
                <button
                  onClick={() => addWebhookMutation.mutate()}
                  disabled={addWebhookMutation.isPending || !newWebhookName.trim() || !newWebhookUrl.trim()}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
                >
                  <Plus className="w-4 h-4" />
                  Add Webhook
                </button>
              </div>

              {webhooks.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">No webhooks configured. Add one to receive notifications when services go down or recover.</p>
              ) : (
                <div className="space-y-2">
                  {webhooks.map(wh => (
                    <div key={wh.id} className="flex items-center justify-between px-4 py-3 rounded-lg border border-border bg-background">
                      <div className="flex items-center gap-3 min-w-0">
                        <Bell className="w-4 h-4 text-muted-foreground shrink-0" />
                        <div className="min-w-0">
                          <span className="font-medium text-sm">{wh.name}</span>
                          <p className="text-xs text-muted-foreground font-mono truncate max-w-xs">{wh.url}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          onClick={() => testWebhookMutation.mutate(wh.id)}
                          disabled={testWebhookMutation.isPending}
                          className="text-xs px-3 py-1.5 border border-border bg-background rounded-md hover:bg-accent transition-colors"
                        >
                          Test
                        </button>
                        <button
                          onClick={() => toggleWebhookMutation.mutate({ id: wh.id, enabled: !wh.enabled })}
                          className={`text-xs px-3 py-1.5 border rounded-md transition-colors ${wh.enabled ? 'border-primary/30 bg-primary/5 text-primary' : 'border-border bg-muted text-muted-foreground'}`}
                        >
                          {wh.enabled ? 'Enabled' : 'Disabled'}
                        </button>
                        <button
                          onClick={async () => {
                            if (await confirm({
                              title: 'Delete webhook',
                              message: `Delete webhook "${wh.name}"?`,
                              confirmLabel: 'Delete',
                              variant: 'danger',
                            }))
                              deleteWebhookMutation.mutate(wh.id);
                          }}
                          className="text-destructive hover:text-destructive/80 transition-colors p-1"
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "logs" && (
          <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
             <div className="px-6 py-4 border-b border-border flex justify-between items-center bg-muted/30">
               <h3 className="font-semibold flex items-center gap-2">
                <FileTerminal className="w-4 h-4" /> Action Logs
               </h3>
               <button
                 onClick={() => clearLogsMutation.mutate()}
                 disabled={clearLogsMutation.isPending}
                 className="text-xs px-3 py-1.5 border border-border bg-background rounded-md hover:bg-accent transition-colors"
               >
                 {clearLogsMutation.isPending ? "Clearing..." : "Clear Logs"}
               </button>
             </div>
             <div className="p-4 max-h-[600px] overflow-y-auto font-mono text-xs space-y-2 bg-card text-foreground">
                {(logs as LogsResponse | undefined)?.items ? (logs as LogsResponse).items.map((log, i) => (
                  <div key={i} className="flex gap-4 border-b border-border/40 pb-2">
                    <span className="text-muted-foreground w-32 shrink-0">{log.created_at}</span>
                    <span className="text-primary w-24 shrink-0">[{log.level}]</span>
                    <span>{log.message}</span>
                  </div>
                )) : <div className="text-muted-foreground">No logs available or API disconnected.</div>}
             </div>
          </div>
        )}
        </div>
      </div>

      {ConfirmDialogElement}
    </div>
  );
}
