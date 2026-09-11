/**
 * Provider sync -- the proxy hosts and DNS records an integration already serves, offered as
 * routes Vauxtra can adopt.
 *
 * Split out of `DataTab.tsx`, which had grown to four unrelated screens in one 1 100-line
 * file: nothing here is shared with Docker discovery, backup export or restore beyond the
 * `SettingsSection` frame they all sit in.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { AlertTriangle, CheckCircle2, RefreshCw, Search, Upload } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import { Badge, Button, Checkbox, EmptyState, useConfirmDialog } from '@/components/ui';
import type { ImportResult, Service, SyncDnsRewrite, SyncProxyHost, SyncResult } from '@/types/api';
import { SettingsSection } from '../SettingsSection';

const LOCAL_TLDS = ['.lan', '.local', '.home', '.internal', '.localdomain', '.arpa'];

function isLocalDomain(domain: string): boolean {
  return LOCAL_TLDS.some((tld) => domain.endsWith(tld));
}


type SyncRow = {
  key: string;
  subdomain: string;
  domain: string;
  target: string;
  provider: string;
  publicHost: string;
  isLocal: boolean;
  status: 'new' | 'exists';
};

/**
 * The identity of one scanned route, computed once.
 *
 * The table rows and the "import only what is ticked" payload filter MUST agree on this
 * string: they used to compute it separately, and the DNS branch forgot to strip the empty
 * subdomain's leading dot, so a rewrite for a bare `foo.example.com` was keyed
 * `foo.example.com` in the table and looked up as `.foo.example.com` in the filter --
 * every ticked DNS row was silently dropped from the import.
 */
function syncItemParts(item: SyncProxyHost | SyncDnsRewrite) {
  const proxyItem = item as SyncProxyHost;
  const subdomain = (
    item.subdomain ||
    proxyItem.domain_names?.[0]?.split('.')[0] ||
    proxyItem.domains?.[0]?.split('.')[0] ||
    ''
  ).toLowerCase();
  const domain = (
    item.domain ||
    proxyItem.domain_names?.[0]?.split('.').slice(1).join('.') ||
    proxyItem.domains?.[0]?.split('.').slice(1).join('.') ||
    ''
  ).toLowerCase();
  const publicHost = `${subdomain}.${domain}`.replace(/^\./, '');
  return { subdomain, domain, publicHost };
}

function syncItemKey(item: SyncProxyHost | SyncDnsRewrite): string {
  return syncItemParts(item).publicHost;
}

export function SyncSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());

  const { data: existingServices = [] } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
  });

  const existingPublicHosts = useMemo(() => {
    const hosts = new Set<string>();
    for (const svc of existingServices) {
      const host = svc.public_host || `${svc.subdomain}.${svc.domain}`;
      if (host) hosts.add(host.toLowerCase());
    }
    return hosts;
  }, [existingServices]);

  const syncMutation = useMutation({
    mutationFn: () => api.post<SyncResult>('/services/sync'),
    onSuccess: (data) => {
      setSyncResult(data);
      setSelectedRows(new Set());
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.migration.scan_failed'))),
  });

  const importMutation = useMutation<ImportResult, Error, unknown>({
    mutationFn: (payload) => api.post<ImportResult>('/services/import', payload),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      if (data.imported > 0) {
        toast.success(t('settings.migration.import_success', { count: data.imported }));
      } else if (data.errors && data.errors.length > 0) {
        toast.error(t('settings.migration.import_exists_or_failed', { count: data.errors.length }));
      } else {
        toast.success(t('settings.migration.sync_complete'));
      }
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.migration.import_failed'))),
  });

  const syncRows = useMemo<SyncRow[]>(() => {
    if (!syncResult) return [];
    const rows: SyncRow[] = [];
    const seen = new Set<string>();

    const push = (item: SyncProxyHost | SyncDnsRewrite, providerLabel: string) => {
      const proxyItem = item as SyncProxyHost;
      const dnsItem = item as SyncDnsRewrite;
      const { subdomain, domain, publicHost } = syncItemParts(item);
      const target =
        proxyItem.forward_host || proxyItem.host
          ? `${proxyItem.forward_host || proxyItem.host}:${proxyItem.forward_port || proxyItem.port || ''}`
          : ((dnsItem.answer || dnsItem.target || '') as string);
      if (seen.has(publicHost)) return;
      seen.add(publicHost);
      // The backend already knows what it imported (it matches on provider ids and tunnel
      // hostnames too); the local host set only catches what shares an exact FQDN.
      const known = item._already_imported === true || existingPublicHosts.has(publicHost);
      rows.push({
        key: publicHost,
        subdomain,
        domain,
        target,
        provider: providerLabel,
        publicHost,
        isLocal: isLocalDomain(domain) || isLocalDomain(publicHost),
        status: known ? 'exists' : 'new',
      });
    };

    if (Array.isArray(syncResult.proxy_hosts)) {
      for (const h of syncResult.proxy_hosts) push(h, h._provider_name || 'Proxy');
    }
    if (Array.isArray(syncResult.dns_rewrites)) {
      for (const h of syncResult.dns_rewrites) push(h, h._provider_name || 'DNS');
    }
    return rows;
  }, [syncResult, existingPublicHosts]);

  const allNewKeys = useMemo(() => syncRows.filter((r) => r.status === 'new').map((r) => r.key), [syncRows]);

  const toggleRow = (key: string) =>
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const quickImport = async () => {
    if (!syncResult) return;
    const newCount = allNewKeys.length;
    const existsCount = syncRows.filter((r) => r.status === 'exists').length;
    const localCount = syncRows.filter((r) => r.isLocal && r.status === 'new').length;

    let message = t('settings.migration.quick_import_message', { count: newCount });
    if (existsCount > 0) message += `\n${t('settings.migration.quick_import_skipped', { count: existsCount })}`;
    if (localCount > 0) message += `\n\n${t('settings.migration.quick_import_local_warn', { count: localCount })}`;

    const ok = await confirm({
      title: t('settings.migration.quick_import_title'),
      message,
      confirmLabel: t('settings.migration.import'),
      variant: localCount > 0 ? 'warning' : 'info',
    });
    if (ok) importMutation.mutate(syncResult);
  };

  const importSelected = async () => {
    if (!syncResult || selectedRows.size === 0) return;
    const ok = await confirm({
      title: t('settings.migration.import_selected_title'),
      message: t('settings.migration.import_selected_message', { count: selectedRows.size }),
      confirmLabel: t('settings.migration.import'),
      variant: 'info',
    });
    if (!ok) return;
    const payload: SyncResult = {
      ...syncResult,
      proxy_hosts: (syncResult.proxy_hosts || []).filter((h) => selectedRows.has(syncItemKey(h))),
      dns_rewrites: (syncResult.dns_rewrites || []).filter((h) => selectedRows.has(syncItemKey(h))),
    };
    importMutation.mutate(payload);
  };

  const allNewSelected = allNewKeys.length > 0 && allNewKeys.every((k) => selectedRows.has(k));
  const someSelected = selectedRows.size > 0 && !allNewSelected;

  return (
    <SettingsSection
      icon={<RefreshCw />}
      title={t('settings.migration.title')}
      description={t('settings.migration.desc')}
      actions={
        <>
          {syncRows.length > 0 && (
            <>
              <Badge tone="neutral">{t('settings.migration.discovered', { count: syncRows.length })}</Badge>
              <Badge tone="primary">{t('settings.migration.new_count', { count: allNewKeys.length })}</Badge>
            </>
          )}
          <Button
            variant={syncRows.length > 0 ? 'outline' : 'primary'}
            size="sm"
            leftIcon={<RefreshCw className={cn(syncMutation.isPending && 'animate-spin')} />}
            loading={syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
          >
            {syncMutation.isPending ? t('settings.migration.scanning') : t('settings.migration.scan')}
          </Button>
        </>
      }
      footer={
        syncRows.length > 0 ? (
          <>
            {importMutation.data && (
              <p className="mr-auto text-xs text-muted-foreground tabular-nums">
                {t('settings.migration.imported', { count: importMutation.data.imported })}
                {importMutation.data.errors.length > 0 && (
                  <span className="ml-2 text-destructive">
                    {t('settings.migration.errors', { count: importMutation.data.errors.length })}
                  </span>
                )}
              </p>
            )}
            <Button
              variant="secondary"
              leftIcon={<Upload />}
              loading={importMutation.isPending}
              disabled={allNewKeys.length === 0}
              onClick={() => void quickImport()}
            >
              {t('settings.migration.quick_import_cta', { count: allNewKeys.length })}
            </Button>
            <Button
              leftIcon={<Upload />}
              loading={importMutation.isPending}
              disabled={selectedRows.size === 0}
              onClick={() => void importSelected()}
            >
              {t('settings.migration.import_selected', { count: selectedRows.size })}
            </Button>
          </>
        ) : undefined
      }
    >
      {syncResult && syncRows.length === 0 && (
        <EmptyState compact icon={<Search />} title={t('settings.migration.no_routes')} />
      )}

      {syncRows.length > 0 && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Checkbox
              checked={allNewSelected}
              indeterminate={someSelected}
              disabled={allNewKeys.length === 0}
              onChange={(e) => setSelectedRows(e.target.checked ? new Set(allNewKeys) : new Set())}
              label={t('settings.migration.select_all_new')}
              description={t('settings.migration.selected_count', { count: selectedRows.size })}
            />
            <Button variant="ghost" size="sm" disabled={selectedRows.size === 0} onClick={() => setSelectedRows(new Set())}>
              {t('settings.migration.clear_selection')}
            </Button>
          </div>

          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-160 text-xs">
              <thead className="border-b border-border bg-muted/50">
                <tr>
                  <th scope="col" className="w-10 px-3 py-2">
                    <span className="sr-only">{t('settings.migration.col_select')}</span>
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_subdomain')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_domain')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_target')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_provider')}</th>
                  <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_status')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {syncRows.map((row) => {
                  const selectable = row.status === 'new';
                  const checked = selectedRows.has(row.key);
                  return (
                    <tr
                      key={row.key}
                      className={cn('transition-colors', selectable ? 'hover:bg-muted/30' : 'opacity-60', checked && 'bg-primary/5')}
                    >
                      <td className="px-3 py-2">
                        <Checkbox
                          checked={checked}
                          disabled={!selectable}
                          onChange={() => toggleRow(row.key)}
                          aria-label={t('settings.migration.select_route_aria', { host: row.publicHost })}
                        />
                      </td>
                      <td className="px-3 py-2 font-mono font-medium text-foreground">{row.subdomain || '—'}</td>
                      <td className="px-3 py-2 font-mono text-foreground">{row.domain || '—'}</td>
                      <td className="px-3 py-2 font-mono text-muted-foreground">{row.target || '—'}</td>
                      <td className="px-3 py-2 text-muted-foreground">{row.provider}</td>
                      <td className="px-3 py-2">
                        {row.status === 'exists' ? (
                          <Badge size="sm" tone="neutral" icon={<CheckCircle2 />}>
                            {t('settings.migration.status_tracked')}
                          </Badge>
                        ) : row.isLocal ? (
                          <Badge size="sm" tone="warning" icon={<AlertTriangle />} title={t('settings.migration.local_tld_title')}>
                            {t('settings.migration.status_local')}
                          </Badge>
                        ) : (
                          <Badge size="sm" tone="primary">
                            {t('settings.migration.status_new')}
                          </Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {ConfirmDialogElement}
    </SettingsSection>
  );
}

// ─── Docker discovery ─────────────────────────────────────────────────────────
