import { DownloadCloud, Database, FileTerminal, Globe, RefreshCw, Loader2, AlertTriangle, CheckCircle2, AlertCircle, Trash2, Key, Bell, Copy, Plus, Eye, EyeOff, Tag, Layers, Languages, Lock, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useI18n, SUPPORTED_LANGUAGES } from "@/i18n";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { Service, LogsResponse, SyncResult, SyncProxyHost, SyncDnsRewrite, ApiKey, ApiKeyCreated, LogLevel } from "@/types/api";
import { useTheme } from "@/theme";
import { toast } from "react-hot-toast";
import { GeneralTab } from "@/components/features/settings";
import { useWebhookActions } from "@/hooks/useWebhookActions";

const LOCAL_TLDS = ['.lan', '.local', '.home', '.internal', '.localdomain', '.arpa'];

function isLocalDomain(domain: string) {
  return LOCAL_TLDS.some((tld) => domain.endsWith(tld));
}

export function Settings() {
  const VALID_TABS = ["general", "language", "dns", "tags", "environments", "apikeys", "webhooks", "migration", "backup", "logs"];
  const [searchParams] = useSearchParams();
  const activeTab = VALID_TABS.includes(searchParams.get("tab") || "") ? searchParams.get("tab")! : "general";
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const { theme, resolvedTheme, setTheme } = useTheme();
  const { lang, setLang, t } = useI18n();
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
  const [apiKeySearch, setApiKeySearch] = useState("");
  const [webhookSearch, setWebhookSearch] = useState("");
  const [compactApiKeys, setCompactApiKeys] = useState(false);
  const [compactWebhooks, setCompactWebhooks] = useState(false);
  const [logQuery, setLogQuery] = useState('');
  const [logLevelFilter, setLogLevelFilter] = useState<'all' | LogLevel>('all');
  const [logsAutoScroll, setLogsAutoScroll] = useState(true);
  const logContainerRef = useRef<HTMLDivElement | null>(null);
  // Change password
  const [cpCurrent, setCpCurrent] = useState("");
  const [cpNew, setCpNew] = useState("");
  const [cpConfirm, setCpConfirm] = useState("");
  const [recentlyChanged, setRecentlyChanged] = useState(false);
  const recentChangeTimerRef = useRef<number | null>(null);

  const markRecentlyChanged = () => {
    setRecentlyChanged(true);
    if (recentChangeTimerRef.current !== null) {
      window.clearTimeout(recentChangeTimerRef.current);
    }
    recentChangeTimerRef.current = window.setTimeout(() => {
      setRecentlyChanged(false);
      recentChangeTimerRef.current = null;
    }, 5000);
  };

  useEffect(() => {
    return () => {
      if (recentChangeTimerRef.current !== null) {
        window.clearTimeout(recentChangeTimerRef.current);
      }
    };
  }, []);

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
      markRecentlyChanged();
      toast.success(t('settings.api_keys.created'));
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.api_keys.create_failed')),
  });

  const revokeKeyMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/settings/api-keys/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      markRecentlyChanged();
      toast.success(t('settings.api_keys.revoked'));
    },
  });

  // Auth status (to know if password is configured)
  const { data: authStatus } = useQuery<{ authenticated: boolean; auth_required: boolean }>({
    queryKey: ['auth-me'],
    queryFn: () => api.get('/auth/me'),
    enabled: activeTab === 'apikeys',
  });

  const changePasswordMutation = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api.post('/auth/change-password', body),
    onSuccess: () => {
      setCpCurrent(""); setCpNew(""); setCpConfirm("");
      markRecentlyChanged();
      toast.success(t('settings.auth.changed_success'));
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      toast.error(err?.response?.data?.detail || t('settings.auth.change_failed')),
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
    onSuccess: () => { setNewTagName(""); queryClient.invalidateQueries({ queryKey: ['tags'] }); markRecentlyChanged(); toast.success(t('settings.tags.created')); },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.tags.create_failed')),
  });
  const deleteTagMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/tags/${id}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['tags'] }); markRecentlyChanged(); toast.success(t('settings.tags.deleted')); },
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
    onSuccess: () => { setNewEnvName(""); queryClient.invalidateQueries({ queryKey: ['environments'] }); markRecentlyChanged(); toast.success(t('settings.env.created')); },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.env.create_failed')),
  });
  const deleteEnvMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/environments/${id}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['environments'] }); markRecentlyChanged(); toast.success(t('settings.env.deleted')); },
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
      markRecentlyChanged();
      toast.success(t('settings.general.policy_saved'));
    },
    onError: (error: unknown) => {
      const axErr = error as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || t('settings.general.policy_save_failed'));
    },
  });

  const addDomainMutation = useMutation({
    mutationFn: (domain: string) => api.post('/domains', { name: domain }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      setNewDomain("");
      markRecentlyChanged();
    }
  });

  const deleteDomainMutation = useMutation({
    mutationFn: (domain: string) => api.delete(`/domains/${domain}`),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['domains'] }); markRecentlyChanged(); },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.dns.delete_failed')),
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
      markRecentlyChanged();
      if (data.imported > 0) {
        toast.success(t('settings.migration.import_success', { count: data.imported }));
      } else if (data.errors && data.errors.length > 0) {
        toast.error(t('settings.migration.import_exists_or_failed', { count: data.errors.length }));
      } else {
        toast.success(t('settings.migration.sync_complete'));
      }
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || t('settings.migration.import_failed'));
    },
  });

  const clearLogsMutation = useMutation({
    mutationFn: () => api.post('/logs/clear'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      markRecentlyChanged();
    },
  });

  // ── Backup state ──────────────────────────────────────────────────────────
  const [showSecureExport, setShowSecureExport] = useState(false);
  const [securePassphrase, setSecurePassphrase] = useState('');
  const [importPending, setImportPending] = useState<{
    json: Record<string, unknown>;
    needsPassphrase: boolean;
    summary: { services: number; providers: number; domains: number; tags: number; environments: number; webhooks: number };
  } | null>(null);
  const [importPassphrase, setImportPassphrase] = useState('');

  const summarizeBackup = (backup: Record<string, unknown>) => {
    const count = (key: string) => (Array.isArray(backup[key]) ? backup[key].length : 0);
    return {
      services: count('services'),
      providers: count('providers'),
      domains: count('domains'),
      tags: count('tags'),
      environments: count('environments'),
      webhooks: count('webhooks'),
    };
  };

  const generateBackupMutation = useMutation({
    mutationFn: () => api.get('/backup', { responseType: 'blob' }),
    onSuccess: (data: unknown) => {
      const url = window.URL.createObjectURL(new Blob([data as BlobPart]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `vauxtra-backup-${new Date().toISOString().split('T')[0]}.json`);
      document.body.appendChild(link);
      link.click();
      window.URL.revokeObjectURL(url);
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.backup.export_failed')),
  });

  const secureBackupMutation = useMutation({
    mutationFn: (passphrase: string) =>
      api.post('/backup/secure', { passphrase }, { responseType: 'blob' }),
    onSuccess: (data: unknown) => {
      const url = window.URL.createObjectURL(new Blob([data as BlobPart]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `vauxtra-backup-secure-${new Date().toISOString().split('T')[0]}.json`);
      document.body.appendChild(link);
      link.click();
      window.URL.revokeObjectURL(url);
      setShowSecureExport(false);
      setSecurePassphrase('');
      markRecentlyChanged();
      toast.success(t('settings.backup.export_secure_success'));
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.backup.export_failed')),
  });

  const restoreBackupMutation = useMutation({
    mutationFn: (payload: { backup: Record<string, unknown>; passphrase: string }) =>
      api.post('/restore', payload),
    onSuccess: () => {
      setImportPending(null);
      setImportPassphrase('');
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      queryClient.invalidateQueries();
      markRecentlyChanged();
      toast.success(t('settings.backup.restore_success'));
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => toast.error(err?.response?.data?.detail || t('settings.backup.restore_failed')),
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

  const filteredApiKeys = useMemo(() => {
    const q = apiKeySearch.trim().toLowerCase();
    if (!q) return apiKeys;
    return apiKeys.filter((key) =>
      key.name.toLowerCase().includes(q) ||
      key.prefix.toLowerCase().includes(q) ||
      key.scopes.some((scope) => scope.toLowerCase().includes(q)),
    );
  }, [apiKeys, apiKeySearch]);

  const filteredWebhooks = useMemo(() => {
    const q = webhookSearch.trim().toLowerCase();
    if (!q) return webhooks;
    return webhooks.filter((wh) =>
      wh.name.toLowerCase().includes(q) ||
      wh.url.toLowerCase().includes(q),
    );
  }, [webhooks, webhookSearch]);

  const logItems = useMemo(() => (logs?.items ?? []), [logs]);

  const filteredLogs = useMemo(() => {
    const q = logQuery.trim().toLowerCase();
    return logItems.filter((log) => {
      const levelMatch = logLevelFilter === 'all' || log.level === logLevelFilter;
      if (!levelMatch) return false;
      if (!q) return true;
      return (
        log.message.toLowerCase().includes(q) ||
        log.created_at.toLowerCase().includes(q) ||
        log.level.toLowerCase().includes(q)
      );
    });
  }, [logItems, logLevelFilter, logQuery]);

  useEffect(() => {
    if (!logsAutoScroll) return;
    if (!logContainerRef.current) return;
    const el = logContainerRef.current;
    el.scrollTop = el.scrollHeight;
  }, [filteredLogs, logsAutoScroll]);

  const toggleSelectedRow = (key: string) => {
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const submitChangePassword = () => {
    if (!cpCurrent) { toast.error(t('settings.auth.current_required')); return; }
    if (cpNew.length < 8) { toast.error(t('settings.auth.new_min')); return; }
    if (cpNew !== cpConfirm) { toast.error(t('settings.auth.confirm_mismatch')); return; }
    changePasswordMutation.mutate({ current_password: cpCurrent, new_password: cpNew });
  };

  const tabLabelMap: Record<string, string> = {
    general: t('settings.tab.general'),
    language: t('settings.language.title'),
    dns: t('settings.tab.dns'),
    tags: t('settings.tab.tags'),
    environments: t('settings.tab.environments'),
    apikeys: t('settings.tab.apikeys'),
    webhooks: t('settings.tab.webhooks'),
    migration: t('settings.tab.migration'),
    backup: t('settings.tab.backup'),
    logs: t('settings.tab.logs'),
  };

  return (
    <div className="animate-in fade-in duration-300 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold text-foreground">{t('settings.title')}</h2>
          <p className="text-xs text-muted-foreground">System / {tabLabelMap[activeTab] || t('settings.tab.general')}</p>
        </div>
        <Link
          to="/"
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
        >
          {t('nav.dashboard')}
        </Link>
      </div>

      <div className={`min-w-0 transition-all duration-500 ${recentlyChanged ? 'ring-2 ring-primary/30 rounded-xl' : ''}`}>
          {activeTab === "general" && (
            <GeneralTab
              theme={theme}
              resolvedTheme={resolvedTheme}
              setTheme={(t) => setTheme(t as 'light' | 'dark' | 'system')}
              settingsData={settingsData}
              savePolicyMutation={savePolicyMutation}
            />
        )}

        {activeTab === "language" && (
          <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
            <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
              <Languages className="w-5 h-5 text-muted-foreground" />
              {t('settings.language.title')}
            </h3>
            <p className="text-sm text-muted-foreground mb-6">{t('settings.language.description')}</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {SUPPORTED_LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  onClick={() => setLang(l.code)}
                  className={`flex items-center gap-2.5 px-4 py-3 rounded-xl border text-sm font-medium transition-all ${
                    lang === l.code
                      ? 'border-primary bg-primary/5 text-primary ring-1 ring-primary/30'
                      : 'border-border text-foreground hover:border-muted-foreground hover:bg-muted'
                  }`}
                >
                  <span className="text-xl leading-none">{l.flag}</span>
                  <span>{l.label}</span>
                </button>
              ))}
            </div>
            <div className="mt-6 p-4 rounded-lg bg-muted/50 border border-border">
              <p className="text-sm font-medium mb-1">{t('settings.language.contribute')}</p>
              <p className="text-xs text-muted-foreground mb-3">{t('settings.language.contribute_description')}</p>
              <a
                href="https://github.com/ptitzgeg-on-git/vauxtra/blob/main/CONTRIBUTING.md#translations"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
              >
                {t('settings.language.contribute_link')}
              </a>
            </div>
          </div>
        )}

        {activeTab === "dns" && (
          <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                <Globe className="w-5 h-5 text-muted-foreground" />
                {t('settings.dns.title')}
              </h3>
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">{t('settings.dns.desc')}</p>
                
                <form 
                  onSubmit={(e) => { 
                    e.preventDefault(); 
                    const trimmed = newDomain.trim().toLowerCase();
                    if (!trimmed) return;
                    if (Array.isArray(domains) && domains.includes(trimmed)) {
                      toast.error(t('settings.dns.exists', { domain: trimmed }));
                      return;
                    }
                    addDomainMutation.mutate(trimmed); 
                  }} 
                  className="flex gap-2"
                >
                  <input 
                    value={newDomain} 
                    onChange={e => setNewDomain(e.target.value)} 
                    placeholder={t('settings.dns.placeholder')} 
                    required 
                    className="flex-1 p-2 rounded-md bg-input border border-border" 
                  />
                  <button type="submit" disabled={addDomainMutation.isPending} className="bg-primary text-primary-foreground px-4 rounded-md">{t('settings.dns.add')}</button>
                </form>

                <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mt-6">
                  {Array.isArray(domains) && domains.map(domain => {
                    const depCount = existingServices.filter((s) => s.domain === domain).length;
                    return (
                    <div key={domain} className="flex justify-between items-center p-3 bg-muted rounded-md border border-border">
                      <div>
                        <span className="font-medium">{domain}</span>
                        {depCount > 0 && (
                          <p className="text-[11px] text-muted-foreground">{t('settings.dns.service_count', { count: depCount })}</p>
                        )}
                      </div>
                      <button
                        onClick={async () => {
                          const hasServices = depCount > 0;
                          const confirmed = await confirm({
                            title: hasServices ? t('settings.dns.confirm.has_services_title') : t('settings.dns.confirm.delete_title'),
                            message: hasServices
                              ? t('settings.dns.confirm.has_services_message', { count: depCount, domain })
                              : t('settings.dns.confirm.delete_message', { domain }),
                            confirmLabel: t('common.delete'),
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
                {t('settings.tags.title')}
              </h3>
              <p className="text-sm text-muted-foreground mb-4">{t('settings.tags.desc')}</p>
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
                  placeholder={t('settings.tags.name_placeholder')}
                  required
                  className="flex-1 bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <select
                  value={newTagColor}
                  onChange={e => setNewTagColor(e.target.value)}
                  className="bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  aria-label={t('settings.tags.color_aria')}
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
                  <Plus className="w-4 h-4" /> {t('common.add')}
                </button>
              </form>
              {tags.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('settings.tags.empty')}</p>
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
                        aria-label={t('settings.tags.delete_aria', { name: tag.name })}
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
                {t('settings.env.title')}
              </h3>
              <p className="text-sm text-muted-foreground mb-4">{t('settings.env.desc')}</p>
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
                  placeholder={t('settings.env.name_placeholder')}
                  required
                  className="flex-1 bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <select
                  value={newEnvColor}
                  onChange={e => setNewEnvColor(e.target.value)}
                  className="bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  aria-label={t('settings.env.color_aria')}
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
                  <Plus className="w-4 h-4" /> {t('common.add')}
                </button>
              </form>
              {environments.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('settings.env.empty')}</p>
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
                        aria-label={t('settings.env.delete_aria', { name: env.name })}
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
              {t('settings.migration.title')}
            </h3>
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {t('settings.migration.desc')}
              </p>

              <div className="flex flex-wrap gap-3 pt-2">
                <button
                  onClick={() => syncMutation.mutate()}
                  disabled={syncMutation.isPending}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground hover:opacity-90 rounded-md transition-colors font-medium text-sm shadow-sm"
                >
                  <RefreshCw className={`w-4 h-4 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                  {syncMutation.isPending ? t('settings.migration.scanning') : t('settings.migration.scan')}
                </button>

                {syncRows.length > 0 && (
                  <button
                    onClick={async () => {
                      const newCount = allNewKeys.length;
                      const existsCount = syncRows.filter(r => r.status === 'exists').length;
                      const localCount = syncRows.filter(r => r.isLocal && r.status === 'new').length;
                      
                      let message = t('settings.migration.quick_import_message', { count: newCount });
                      if (existsCount > 0) message += `\n${t('settings.migration.quick_import_skipped', { count: existsCount })}`;
                      if (localCount > 0) message += `\n\n${t('settings.migration.quick_import_local_warn', { count: localCount })}`;
                      
                      const confirmed = await confirm({
                        title: t('settings.migration.quick_import_title'),
                        message,
                        confirmLabel: t('settings.migration.import'),
                        variant: localCount > 0 ? 'warning' : 'info',
                      });
                      if (!confirmed) return;
                      importMutation.mutate(syncResult);
                    }}
                    disabled={importMutation.isPending || allNewKeys.length === 0}
                    className="flex items-center gap-2 px-4 py-2 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded-md transition-colors font-medium text-sm shadow-sm border border-border"
                  >
                    {importMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    {importMutation.isPending ? t('settings.migration.importing') : t('settings.migration.quick_import_cta', { count: allNewKeys.length })}
                  </button>
                )}
              </div>

              {/* Preview table */}
              {syncRows.length > 0 && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      {t('settings.migration.discovered', { count: syncRows.length })}
                    </p>
                    <div className="flex gap-2 text-xs">
                      <button
                        className="text-primary hover:underline"
                        onClick={() => setSelectedRows(new Set(allNewKeys))}
                      >{t('settings.migration.select_all_new')}</button>
                      <span className="text-muted-foreground">·</span>
                      <button
                        className="text-muted-foreground hover:text-foreground"
                        onClick={() => setSelectedRows(new Set())}
                      >{t('settings.migration.clear_selection')}</button>
                    </div>
                  </div>

                  <div className="rounded-lg border border-border overflow-hidden">
                    <table className="w-full text-xs">
                      <thead className="bg-muted/50 border-b border-border">
                        <tr>
                          <th className="w-8 px-3 py-2"></th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_subdomain')}</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_domain')}</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_target')}</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_provider')}</th>
                          <th className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_status')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {syncRows.map((row) => {
                          const isSelectable = row.status === 'new';
                          const checked = selectedRows.has(row.key);
                          return (
                            <tr
                              key={row.key}
                              className={`transition-colors ${isSelectable ? 'hover:bg-muted/30 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-1 focus-visible:ring-offset-background' : 'opacity-60'}`}
                              tabIndex={isSelectable ? 0 : -1}
                              aria-selected={isSelectable ? checked : undefined}
                              onKeyDown={(e) => {
                                if (!isSelectable) return;
                                if (e.key !== 'Enter' && e.key !== ' ') return;
                                e.preventDefault();
                                toggleSelectedRow(row.key);
                              }}
                              onClick={() => {
                                if (!isSelectable) return;
                                toggleSelectedRow(row.key);
                              }}
                            >
                              <td className="px-3 py-2">
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  disabled={!isSelectable}
                                  onChange={() => toggleSelectedRow(row.key)}
                                  onClick={(e) => e.stopPropagation()}
                                  className="rounded"
                                  aria-label={t('settings.migration.select_route_aria', { host: row.publicHost })}
                                />
                              </td>
                              <td className="px-3 py-2 font-mono font-medium text-foreground">{row.subdomain || '—'}</td>
                              <td className="px-3 py-2 font-mono text-foreground">{row.domain || '—'}</td>
                              <td className="px-3 py-2 font-mono text-muted-foreground">{row.target || '—'}</td>
                              <td className="px-3 py-2 text-muted-foreground">{row.provider}</td>
                              <td className="px-3 py-2">
                                {row.status === 'exists' ? (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-muted text-muted-foreground border border-border">
                                    <CheckCircle2 className="w-3 h-3" /> {t('settings.migration.status_tracked')}
                                  </span>
                                ) : row.isLocal ? (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/30"
                                    title={t('settings.migration.local_tld_title')}>
                                    <AlertTriangle className="w-3 h-3" /> {t('settings.migration.status_local')}
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-primary/10 text-primary border border-primary/20">
                                    <AlertCircle className="w-3 h-3" /> {t('settings.migration.status_new')}
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
                      {importMutation.isPending ? t('settings.migration.importing') : t('settings.migration.import_selected', { count: selectedRows.size })}
                    </button>

                    {importMutation.data && (
                      <p className="text-xs text-muted-foreground">
                        {t('settings.migration.imported', { count: importMutation.data.imported })}
                        {importMutation.data.errors.length > 0 && (
                          <span className="text-destructive ml-2">{t('settings.migration.errors', { count: importMutation.data.errors.length })}</span>
                        )}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {syncResult && syncRows.length === 0 && (
                <div className="mt-4 p-4 rounded-lg border border-border bg-muted/30 text-sm text-muted-foreground">
                  {t('settings.migration.no_routes')}
                </div>
              )}

            </div>
          </div>
        )}

        {activeTab === "backup" && (
          <div className="space-y-6">
            {/* ── Export ── */}
            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <DownloadCloud className="w-5 h-5 text-muted-foreground" />
                {t('settings.backup.export_title')}
              </h3>
              <p className="text-sm text-muted-foreground mb-5">
                {t('settings.backup.export_desc')}
              </p>

              <div className="flex flex-wrap gap-3">
                {/* Basic export */}
                <button
                  onClick={() => generateBackupMutation.mutate()}
                  disabled={generateBackupMutation.isPending}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg transition-colors font-medium text-sm disabled:opacity-60"
                >
                  {generateBackupMutation.isPending
                    ? <Loader2 className="w-4 h-4 animate-spin" />
                    : <DownloadCloud className="w-4 h-4" />}
                  {t('settings.backup.export_plain')}
                </button>

                {/* Secure export toggle */}
                <button
                  onClick={() => setShowSecureExport(v => !v)}
                  className="flex items-center gap-2 px-4 py-2 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded-lg border border-border transition-colors font-medium text-sm"
                >
                  <Lock className="w-4 h-4" />
                  {t('settings.backup.export_secure')}
                </button>
              </div>

              {/* Inline passphrase form for secure export */}
              {showSecureExport && (
                <div className="mt-4 p-4 rounded-lg border border-border bg-muted/30">
                  <p className="text-xs text-muted-foreground mb-3">
                    {t('settings.backup.secure_export_help')}
                  </p>
                  <form
                    className="flex items-center gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (securePassphrase.length < 8) {
                        toast.error(t('settings.backup.passphrase_min'));
                        return;
                      }
                      secureBackupMutation.mutate(securePassphrase);
                    }}
                  >
                    <input
                      type="password"
                      value={securePassphrase}
                      onChange={e => setSecurePassphrase(e.target.value)}
                      placeholder={t('settings.backup.passphrase_placeholder')}
                      minLength={8}
                      required
                      className="flex-1 bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    />
                    <button
                      type="submit"
                      disabled={secureBackupMutation.isPending || securePassphrase.length < 8}
                      className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium disabled:opacity-60"
                    >
                      {secureBackupMutation.isPending
                        ? <Loader2 className="w-4 h-4 animate-spin" />
                        : <Lock className="w-4 h-4" />}
                      {t('settings.backup.export')}
                    </button>
                    <button
                      type="button"
                      onClick={() => { setShowSecureExport(false); setSecurePassphrase(''); }}
                      className="px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted"
                    >
                      {t('common.cancel')}
                    </button>
                  </form>
                </div>
              )}

              <p className="text-xs text-muted-foreground mt-4">
                <strong>{t('settings.backup.no_credentials')}</strong> — {t('settings.backup.no_credentials_desc')}<br />
                <strong>{t('settings.backup.with_credentials')}</strong> — {t('settings.backup.with_credentials_desc')}
              </p>
            </div>

            {/* ── Import ── */}
            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <Upload className="w-5 h-5 text-muted-foreground" />
                {t('settings.backup.import_title')}
              </h3>
              <p className="text-sm text-muted-foreground mb-5">
                {t('settings.backup.import_desc')} <span className="text-destructive font-medium">{t('settings.backup.import_warning')}</span>
              </p>

              {!importPending ? (
                <label className="inline-flex items-center gap-2 px-4 py-2 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded-lg border border-border cursor-pointer transition-colors font-medium text-sm">
                  <Database className="w-4 h-4" />
                  {t('settings.backup.choose_file')}
                  <input
                    type="file"
                    className="hidden"
                    accept=".json"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      try {
                        const text = await file.text();
                        const json = JSON.parse(text);
                        if (typeof json !== 'object' || !json.version) {
                          toast.error(t('settings.backup.invalid_file'));
                          return;
                        }
                        setImportPending({
                          json,
                          needsPassphrase: !!json.secrets_included,
                          summary: summarizeBackup(json as Record<string, unknown>),
                        });
                      } catch {
                        toast.error(t('settings.backup.invalid_json'));
                      }
                      e.target.value = '';
                    }}
                  />
                </label>
              ) : (
                <div className="p-4 rounded-lg border border-border bg-muted/30 space-y-4">
                  <div className="flex items-center gap-2 text-sm">
                    <Database className="w-4 h-4 text-muted-foreground" />
                    <span className="font-medium">{t('settings.backup.version', { version: importPending.json.version as string })}</span>
                    <span className="text-muted-foreground">·</span>
                    {importPending.needsPassphrase ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                        <Lock className="w-3 h-3" /> {t('settings.backup.encrypted_credentials')}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">
                        {t('settings.backup.no_credentials')}
                      </span>
                    )}
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
                    <div className="px-2 py-1.5 rounded border border-border bg-background">{t('settings.backup.summary.services')}: <strong>{importPending.summary.services}</strong></div>
                    <div className="px-2 py-1.5 rounded border border-border bg-background">{t('settings.backup.summary.providers')}: <strong>{importPending.summary.providers}</strong></div>
                    <div className="px-2 py-1.5 rounded border border-border bg-background">{t('settings.backup.summary.domains')}: <strong>{importPending.summary.domains}</strong></div>
                    <div className="px-2 py-1.5 rounded border border-border bg-background">{t('settings.backup.summary.tags')}: <strong>{importPending.summary.tags}</strong></div>
                    <div className="px-2 py-1.5 rounded border border-border bg-background">{t('settings.backup.summary.environments')}: <strong>{importPending.summary.environments}</strong></div>
                    <div className="px-2 py-1.5 rounded border border-border bg-background">{t('settings.backup.summary.webhooks')}: <strong>{importPending.summary.webhooks}</strong></div>
                  </div>

                  {importPending.needsPassphrase && (
                    <div>
                      <label className="text-xs font-medium text-foreground mb-1.5 block">{t('settings.backup.passphrase_used')}</label>
                      <input
                        type="password"
                        value={importPassphrase}
                        onChange={e => setImportPassphrase(e.target.value)}
                        placeholder={t('settings.backup.passphrase_enter')}
                        className="w-full bg-background border border-input rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                        autoFocus
                      />
                    </div>
                  )}

                  <div className="flex gap-2">
                    <button
                      disabled={restoreBackupMutation.isPending || (importPending.needsPassphrase && !importPassphrase)}
                      onClick={async () => {
                        const summary = importPending.summary;
                        const confirmed = await confirm({
                          title: t('settings.backup.restore_confirm_title'),
                          message: t('settings.backup.restore_confirm_message', {
                            services: summary.services,
                            providers: summary.providers,
                            domains: summary.domains,
                            tags: summary.tags,
                            environments: summary.environments,
                            webhooks: summary.webhooks,
                          }),
                          confirmLabel: t('settings.backup.restore'),
                          variant: 'danger',
                        });
                        if (!confirmed) return;
                        restoreBackupMutation.mutate({
                          backup: importPending.json,
                          passphrase: importPassphrase,
                        });
                      }}
                      className="flex items-center gap-2 px-4 py-2 bg-destructive text-destructive-foreground hover:bg-destructive/90 rounded-lg text-sm font-medium disabled:opacity-60"
                    >
                      {restoreBackupMutation.isPending
                        ? <Loader2 className="w-4 h-4 animate-spin" />
                        : <Upload className="w-4 h-4" />}
                      {restoreBackupMutation.isPending ? t('settings.backup.restoring') : t('settings.backup.restore')}
                    </button>
                    <button
                      onClick={() => { setImportPending(null); setImportPassphrase(''); }}
                      className="px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted"
                    >
                      {t('common.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "apikeys" && (
          <div className="space-y-6">
            {authStatus?.auth_required && (
              <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
                <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                  <Lock className="w-5 h-5 text-muted-foreground" />
                  {t('settings.auth.change_password')}
                </h3>
                <p className="text-sm text-muted-foreground mb-5">
                  {t('settings.auth.change_password_desc')}
                </p>
                <form
                  className="space-y-3 max-w-sm"
                  onSubmit={(e) => {
                    e.preventDefault();
                    submitChangePassword();
                  }}
                >
                  <input
                    type="password"
                    placeholder={t('settings.auth.current_password')}
                    value={cpCurrent}
                    onChange={e => setCpCurrent(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border border-input bg-background text-sm"
                    autoComplete="current-password"
                    required
                  />
                  <input
                    type="password"
                    placeholder={t('settings.auth.new_password')}
                    value={cpNew}
                    onChange={e => setCpNew(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border border-input bg-background text-sm"
                    autoComplete="new-password"
                    minLength={8}
                    required
                  />
                  <input
                    type="password"
                    placeholder={t('settings.auth.confirm_password')}
                    value={cpConfirm}
                    onChange={e => setCpConfirm(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg border border-input bg-background text-sm"
                    autoComplete="new-password"
                    required
                  />
                  <button
                    type="submit"
                    disabled={changePasswordMutation.isPending}
                    className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
                  >
                    {changePasswordMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                    {t('settings.auth.change_password')}
                  </button>
                </form>
              </div>
            )}

            <div className="bg-card border border-border rounded-xl p-6 shadow-sm">
              <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                <Key className="w-5 h-5 text-muted-foreground" />
                {t('settings.api_keys.title')}
              </h3>
              <p className="text-sm text-muted-foreground mb-6">
                {t('settings.api_keys.desc')}
              </p>

              {createdKey && (
                <div className="bg-primary/5 border border-primary/30 rounded-lg p-4 mb-6">
                  <p className="text-sm font-medium text-foreground mb-2">{t('settings.api_keys.created_once')}</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 bg-muted px-3 py-2 rounded font-mono text-sm border border-border break-all">
                      {showKeySecret ? createdKey : createdKey.slice(0, 10) + '•'.repeat(30)}
                    </code>
                    <button
                      onClick={() => setShowKeySecret(!showKeySecret)}
                      className="p-2 rounded-md hover:bg-accent transition-colors"
                      title={showKeySecret ? t('settings.api_keys.hide') : t('settings.api_keys.reveal')}
                    >
                      {showKeySecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => { navigator.clipboard.writeText(createdKey); toast.success(t('settings.api_keys.copied')); }}
                      className="p-2 rounded-md hover:bg-accent transition-colors"
                      title={t('settings.api_keys.copy')}
                    >
                      <Copy className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              )}

              <div className="flex flex-col sm:flex-row gap-3 mb-6">
                <input
                  type="text"
                  placeholder={t('settings.api_keys.name_placeholder')}
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
                    if (!newKeyName.trim()) { toast.error(t('settings.api_keys.name_required')); return; }
                    if (!newKeyScopes.length) { toast.error(t('settings.api_keys.scope_required')); return; }
                    setCreatedKey(null);
                    setShowKeySecret(false);
                    createKeyMutation.mutate({ name: newKeyName, scopes: newKeyScopes });
                  }}
                  disabled={createKeyMutation.isPending}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
                >
                  <Plus className="w-4 h-4" />
                  {t('settings.api_keys.create')}
                </button>
              </div>

              <div className="sticky top-0 z-10 -mx-1 mb-4 px-1 py-2 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80 border-y border-border flex flex-col sm:flex-row gap-2 sm:items-center sm:justify-between">
                <input
                  type="text"
                  value={apiKeySearch}
                  onChange={(e) => setApiKeySearch(e.target.value)}
                  placeholder={t('settings.api_keys.search_placeholder')}
                  className="sm:w-72 px-3 py-1.5 rounded-md border border-input bg-background text-xs"
                />
                <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={compactApiKeys}
                    onChange={(e) => setCompactApiKeys(e.target.checked)}
                    className="rounded"
                  />
                  {t('settings.list.compact_rows')}
                </label>
              </div>

              {apiKeys.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">{t('settings.api_keys.empty')}</p>
              ) : filteredApiKeys.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-6">{t('settings.api_keys.no_match')}</p>
              ) : (
                <div className="space-y-2">
                  {filteredApiKeys.map(key => (
                    <div key={key.id} className={`flex items-center justify-between ${compactApiKeys ? 'px-3 py-2' : 'px-4 py-3'} rounded-lg border border-border bg-background`}>
                      <div className="flex items-center gap-3 min-w-0">
                        <Key className="w-4 h-4 text-muted-foreground shrink-0" />
                        <div className="min-w-0">
                          <span className="font-medium text-sm">{key.name}</span>
                          <div className={`flex items-center gap-2 ${compactApiKeys ? 'mt-0' : 'mt-0.5'}`}>
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
                          {key.last_used_at ? t('settings.api_keys.last_used', { date: key.last_used_at }) : t('settings.api_keys.never_used')}
                        </span>
                        <button
                          onClick={async () => {
                            if (await confirm({
                              title: t('settings.api_keys.revoke_title'),
                              message: t('settings.api_keys.revoke_message', { name: key.name }),
                              confirmLabel: t('settings.api_keys.revoke'),
                              variant: 'danger',
                            }))
                              revokeKeyMutation.mutate(key.id);
                          }}
                          className="text-destructive hover:text-destructive/80 transition-colors p-1"
                          title={t('settings.api_keys.revoke')}
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
                {t('settings.webhooks.title')}
              </h3>
              <p className="text-sm text-muted-foreground mb-6">
                {t('settings.webhooks.desc')} <a href="https://github.com/caronc/apprise" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">Apprise</a>.
              </p>

              <div className="flex flex-col sm:flex-row gap-3 mb-6">
                <input
                  type="text"
                  placeholder={t('settings.webhooks.name_placeholder')}
                  value={newWebhookName}
                  onChange={e => setNewWebhookName(e.target.value)}
                  className="sm:w-48 px-3 py-2 rounded-lg border border-input bg-background text-sm"
                />
                <input
                  type="text"
                  placeholder={t('settings.webhooks.url_placeholder')}
                  value={newWebhookUrl}
                  onChange={e => setNewWebhookUrl(e.target.value)}
                  className="flex-1 px-3 py-2 rounded-lg border border-input bg-background text-sm font-mono"
                />
                <button
                  onClick={() => addWebhookMutation.mutate(undefined, { onSuccess: () => markRecentlyChanged() })}
                  disabled={addWebhookMutation.isPending || !newWebhookName.trim() || !newWebhookUrl.trim()}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
                >
                  <Plus className="w-4 h-4" />
                  {t('settings.webhooks.add')}
                </button>
              </div>

              <div className="sticky top-0 z-10 -mx-1 mb-4 px-1 py-2 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80 border-y border-border flex flex-col sm:flex-row gap-2 sm:items-center sm:justify-between">
                <input
                  type="text"
                  value={webhookSearch}
                  onChange={(e) => setWebhookSearch(e.target.value)}
                  placeholder={t('settings.webhooks.search_placeholder')}
                  className="sm:w-72 px-3 py-1.5 rounded-md border border-input bg-background text-xs"
                />
                <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={compactWebhooks}
                    onChange={(e) => setCompactWebhooks(e.target.checked)}
                    className="rounded"
                  />
                  {t('settings.list.compact_rows')}
                </label>
              </div>

              {webhooks.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">{t('settings.webhooks.empty')}</p>
              ) : filteredWebhooks.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-6">{t('settings.webhooks.no_match')}</p>
              ) : (
                <div className="space-y-2">
                  {filteredWebhooks.map(wh => (
                    <div key={wh.id} className={`flex items-center justify-between ${compactWebhooks ? 'px-3 py-2' : 'px-4 py-3'} rounded-lg border border-border bg-background`}>
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
                          {t('settings.webhooks.test')}
                        </button>
                        <button
                          onClick={() => toggleWebhookMutation.mutate({ id: wh.id, enabled: !wh.enabled }, { onSuccess: () => markRecentlyChanged() })}
                          className={`text-xs px-3 py-1.5 border rounded-md transition-colors ${wh.enabled ? 'border-primary/30 bg-primary/5 text-primary' : 'border-border bg-muted text-muted-foreground'}`}
                        >
                          {wh.enabled ? t('settings.webhooks.enabled') : t('settings.webhooks.disabled')}
                        </button>
                        <button
                          onClick={async () => {
                            if (await confirm({
                              title: t('settings.webhooks.delete_title'),
                              message: t('settings.webhooks.delete_message', { name: wh.name }),
                              confirmLabel: t('common.delete'),
                              variant: 'danger',
                            }))
                              deleteWebhookMutation.mutate(wh.id, { onSuccess: () => markRecentlyChanged() });
                          }}
                          className="text-destructive hover:text-destructive/80 transition-colors p-1"
                          title={t('common.delete')}
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
                <FileTerminal className="w-4 h-4" /> {t('settings.logs.title')}
               </h3>
               <div className="flex items-center gap-2">
                 <button
                   onClick={() => queryClient.invalidateQueries({ queryKey: ['logs'] })}
                   className="text-xs px-3 py-1.5 border border-border bg-background rounded-md hover:bg-accent transition-colors"
                 >
                   {t('settings.logs.refresh')}
                 </button>
                 <button
                   onClick={() => clearLogsMutation.mutate()}
                   disabled={clearLogsMutation.isPending}
                   className="text-xs px-3 py-1.5 border border-border bg-background rounded-md hover:bg-accent transition-colors"
                 >
                   {clearLogsMutation.isPending ? t('settings.logs.clearing') : t('settings.logs.clear')}
                 </button>
               </div>
             </div>
             <div className="px-4 py-3 border-b border-border bg-card/70 flex flex-col sm:flex-row gap-2 sm:items-center sm:justify-between">
               <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
                 <input
                   type="text"
                   value={logQuery}
                   onChange={(e) => setLogQuery(e.target.value)}
                   placeholder={t('settings.logs.search_placeholder')}
                   className="sm:w-72 px-3 py-1.5 rounded-md border border-input bg-background text-xs"
                 />
                 <select
                   value={logLevelFilter}
                   onChange={(e) => setLogLevelFilter(e.target.value as 'all' | LogLevel)}
                   className="px-2.5 py-1.5 rounded-md border border-input bg-background text-xs"
                   aria-label={t('settings.logs.level_filter_aria')}
                 >
                   <option value="all">{t('settings.logs.level_all')}</option>
                    <option value="ok">{t('settings.logs.level_ok')}</option>
                    <option value="info">{t('settings.logs.level_info')}</option>
                    <option value="warning">{t('settings.logs.level_warning')}</option>
                    <option value="error">{t('settings.logs.level_error')}</option>
                 </select>
               </div>
               <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                 <input
                   type="checkbox"
                   checked={logsAutoScroll}
                   onChange={(e) => setLogsAutoScroll(e.target.checked)}
                   className="rounded"
                 />
                 {t('settings.logs.autoscroll')}
               </label>
             </div>
             <div ref={logContainerRef} className="p-4 max-h-[600px] overflow-y-auto font-mono text-xs space-y-2 bg-card text-foreground">
                {filteredLogs.length > 0 ? filteredLogs.map((log, i) => (
                  <div key={i} className="flex gap-4 border-b border-border/40 pb-2">
                    <span className="text-muted-foreground w-32 shrink-0">{log.created_at}</span>
                    <span className="text-primary w-24 shrink-0">[{log.level}]</span>
                    <span>{log.message}</span>
                  </div>
                 )) : (
                   <div className="text-muted-foreground">
                     {logItems.length > 0 ? t('settings.logs.no_match') : t('settings.logs.empty')}
                   </div>
                 )}
             </div>
          </div>
        )}
      </div>

      {ConfirmDialogElement}
    </div>
  );
}
