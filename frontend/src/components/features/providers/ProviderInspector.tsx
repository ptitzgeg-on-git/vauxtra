import { useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, Check, Globe, Minus, Server, Waypoints } from 'lucide-react';
import { api } from '@/api/client';
import {
  Badge,
  Button,
  Drawer,
  EmptyState,
  InlineAlert,
  ProviderLogo,
  SearchInput,
  SectionHeading,
  Skeleton,
  SkeletonRow,
  cn,
} from '@/components/ui';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import { EM_DASH } from '@/lib/format';
import type { DnsRecord, DnsRecordsResponse, Provider, ProviderHealthDetail, ProxyHost, ProxyHostsResponse } from '@/types/api';
import { type ProviderTypeMeta, fallbackIconByType, isDnsType, isProxyType, isTunnelType } from './providerConstants';
import { tunnelReasonLabel, tunnelStatusKey, tunnelTone } from './providerHealth';

export interface ProviderInspectorProps {
  provider: Provider | null;
  meta?: ProviderTypeMeta;
  open: boolean;
  onClose: () => void;
}

/** Some DNS backends also report a TTL; the shared type only promises `domain`/`answer`. */
type DnsRecordRow = DnsRecord & { ttl?: number };

function BoolCell({ value }: { value: boolean | undefined }) {
  const t = useT();
  if (value === undefined || value === null) return <span className="text-muted-foreground">{EM_DASH}</span>;
  return value ? (
    <span className="inline-flex items-center gap-1 text-success">
      <Check className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="sr-only">{t('providers.yes')}</span>
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-muted-foreground">
      <Minus className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="sr-only">{t('providers.no')}</span>
    </span>
  );
}

function SectionError({ message, onRetry }: { message: string; onRetry: () => void }) {
  const t = useT();
  return (
    <InlineAlert
      tone="danger"
      title={message}
      action={
        <Button size="sm" variant="outline" onClick={onRetry}>
          {t('providers.retry')}
        </Button>
      }
    />
  );
}

function TableSkeleton({ columns }: { columns: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      <SkeletonRow columns={columns} />
      <SkeletonRow columns={columns} />
      <SkeletonRow columns={columns} />
    </div>
  );
}

const TH = 'px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground whitespace-nowrap';
const TD = 'px-3 py-2 align-top';

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 wrap-break-word text-sm text-foreground">{children}</dd>
    </div>
  );
}

export function ProviderInspector({ provider, meta, open, onClose }: ProviderInspectorProps) {
  const t = useT();
  const id = provider?.id;
  const typeKey = String(provider?.type || '').toLowerCase();
  const isTunnel = isTunnelType(typeKey, meta);
  const showHosts = Boolean(provider) && isProxyType(typeKey, meta);
  const showRecords = Boolean(provider) && isDnsType(typeKey, meta);
  const FallbackIcon = fallbackIconByType[typeKey] || Server;

  // Search text is keyed by provider id so opening another provider starts from an empty box.
  const [search, setSearch] = useState<{ id?: number; hosts: string; records: string }>({ hosts: '', records: '' });
  const hostSearch = search.id === id ? search.hosts : '';
  const recordSearch = search.id === id ? search.records : '';
  const setHostSearch = (value: string) => setSearch((prev) => ({ id, hosts: value, records: prev.id === id ? prev.records : '' }));
  const setRecordSearch = (value: string) => setSearch((prev) => ({ id, records: value, hosts: prev.id === id ? prev.hosts : '' }));

  const healthQuery = useQuery<ProviderHealthDetail>({
    queryKey: ['provider-health', id],
    queryFn: () => api.get<ProviderHealthDetail>(`/providers/${id}/health`),
    enabled: open && Boolean(id),
  });

  const hostsQuery = useQuery<ProxyHostsResponse>({
    queryKey: ['provider-proxy-hosts', id],
    queryFn: () => api.get<ProxyHostsResponse>(`/providers/${id}/proxy-hosts`),
    enabled: open && Boolean(id) && showHosts,
  });

  const recordsQuery = useQuery<DnsRecordsResponse>({
    queryKey: ['provider-dns-records', id],
    queryFn: () => api.get<DnsRecordsResponse>(`/providers/${id}/dns-records`),
    enabled: open && Boolean(id) && showRecords,
  });

  const hosts: ProxyHost[] = useMemo(() => (Array.isArray(hostsQuery.data?.hosts) ? hostsQuery.data.hosts : []), [hostsQuery.data]);
  const filteredHosts = useMemo(() => {
    const q = hostSearch.trim().toLowerCase();
    if (!q) return hosts;
    return hosts.filter((h) => (h.domains || []).some((d) => d.toLowerCase().includes(q)) || String(h.target || '').toLowerCase().includes(q));
  }, [hosts, hostSearch]);

  const records: DnsRecordRow[] = useMemo(
    () => (Array.isArray(recordsQuery.data?.records) ? (recordsQuery.data.records as DnsRecordRow[]) : []),
    [recordsQuery.data],
  );
  const filteredRecords = useMemo(() => {
    const q = recordSearch.trim().toLowerCase();
    if (!q) return records;
    return records.filter((r) => String(r.domain || '').toLowerCase().includes(q) || String(r.answer || '').toLowerCase().includes(q));
  }, [records, recordSearch]);
  const hasRecordType = records.some((r) => typeof r.type === 'string' && r.type);
  const hasRecordTtl = records.some((r) => typeof r.ttl === 'number');
  const hasRecordProxied = records.some((r) => typeof r.proxied === 'boolean');

  const health = healthQuery.data?.health;
  const healthOk = healthQuery.data ? Boolean(healthQuery.data.ok ?? health?.ok) : undefined;
  const healthStatus = String(health?.status || '');

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size="xl"
      title={provider?.name || ''}
      description={meta?.label || provider?.type}
      icon={
        provider ? (
          <ProviderLogo type={typeKey} className="h-5 w-5" fallback={<FallbackIcon className="h-5 w-5" />} />
        ) : undefined
      }
    >
      {provider && (
        <div className="space-y-8">
          <section className="space-y-3" aria-labelledby={`inspector-health-${provider.id}`}>
            <SectionHeading
              id={`inspector-health-${provider.id}`}
              size="sm"
              icon={<Activity />}
              title={t('providers.inspector.health')}
            >
              {healthQuery.isSuccess && (
                <Badge tone={healthOk ? 'success' : 'danger'} dot size="sm">
                  {healthOk ? t('providers.inspector.health_ok') : t('providers.inspector.health_ko')}
                </Badge>
              )}
            </SectionHeading>

            {healthQuery.isPending ? (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3" aria-hidden="true">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : healthQuery.isError ? (
              <SectionError
                message={translateApiError(healthQuery.error, t, t('providers.inspector.error_health'))}
                onRetry={() => healthQuery.refetch()}
              />
            ) : (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-border bg-muted/30 p-4 sm:grid-cols-3">
                <Fact label={t('providers.inspector.status')}>
                  {healthStatus ? (
                    isTunnel ? (
                      <Badge tone={tunnelTone(healthStatus)} dot size="sm">
                        {t(tunnelStatusKey(healthStatus))}
                      </Badge>
                    ) : (
                      <span className="capitalize">{healthStatus}</span>
                    )
                  ) : (
                    EM_DASH
                  )}
                </Fact>
                {health?.error && (
                  <Fact label={t('providers.inspector.error')}>
                    <span className="text-destructive">{health.error}</span>
                  </Fact>
                )}
                {health?.reason && (
                  <Fact label={t('providers.inspector.reason')}>{tunnelReasonLabel(health.reason, t)}</Fact>
                )}
                {health?.tunnel_id && (
                  <Fact label={t('providers.inspector.tunnel_id')}>
                    <code className="break-all font-mono text-xs">{health.tunnel_id}</code>
                  </Fact>
                )}
                {typeof (health?.active_connections ?? health?.connections) === 'number' && (
                  <Fact label={t('providers.inspector.active_connections')}>
                    <span className="tabular-nums">{health?.active_connections ?? health?.connections}</span>
                  </Fact>
                )}
                {typeof (health?.active_clients ?? health?.clients) === 'number' && (
                  <Fact label={t('providers.inspector.active_clients')}>
                    <span className="tabular-nums">{health?.active_clients ?? health?.clients}</span>
                  </Fact>
                )}
                {health?.declared_status && (
                  <Fact label={t('providers.inspector.declared_status')}>
                    <span className="capitalize">{health.declared_status}</span>
                  </Fact>
                )}
                {Array.isArray(health?.cloudflared_versions) && health.cloudflared_versions.length > 0 && (
                  <Fact label={t('providers.inspector.cloudflared_versions')}>
                    <span className="font-mono text-xs">{health.cloudflared_versions.join(', ')}</span>
                  </Fact>
                )}
                {typeof health?.zones_visible === 'number' && (
                  <Fact label={t('providers.inspector.zones_visible')}>
                    <span className="tabular-nums">{health.zones_visible}</span>
                  </Fact>
                )}
              </dl>
            )}
          </section>

          {showHosts && (
            <section className="space-y-3" aria-labelledby={`inspector-hosts-${provider.id}`}>
              <SectionHeading
                id={`inspector-hosts-${provider.id}`}
                size="sm"
                icon={isTunnel ? <Waypoints /> : <Server />}
                title={t('providers.inspector.hosts')}
                description={hostsQuery.isSuccess ? t('providers.inspector.hosts_count', { count: hosts.length }) : undefined}
              >
                {hosts.length > 6 && (
                  <SearchInput
                    size="sm"
                    value={hostSearch}
                    onChange={setHostSearch}
                    placeholder={t('providers.inspector.search_hosts')}
                    aria-label={t('providers.inspector.search_hosts')}
                    wrapperClassName="w-56"
                  />
                )}
              </SectionHeading>

              {hostsQuery.isPending ? (
                <TableSkeleton columns={5} />
              ) : hostsQuery.isError ? (
                <SectionError
                  message={translateApiError(hostsQuery.error, t, t('providers.inspector.error_hosts'))}
                  onRetry={() => hostsQuery.refetch()}
                />
              ) : hosts.length === 0 ? (
                <EmptyState compact icon={<Server />} title={t('providers.inspector.empty_hosts')} />
              ) : filteredHosts.length === 0 ? (
                <EmptyState
                  compact
                  title={t('providers.inspector.no_match')}
                  action={
                    <Button size="sm" variant="outline" onClick={() => setHostSearch('')}>
                      {t('ui.search.clear')}
                    </Button>
                  }
                />
              ) : (
                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead className="bg-muted/60">
                      <tr>
                        <th scope="col" className={TH}>{t('providers.inspector.col.domains')}</th>
                        <th scope="col" className={TH}>{t('providers.inspector.col.target')}</th>
                        <th scope="col" className={cn(TH, 'text-center')}>{t('providers.inspector.col.ssl')}</th>
                        <th scope="col" className={cn(TH, 'text-center')}>{t('providers.inspector.col.websocket')}</th>
                        <th scope="col" className={cn(TH, 'text-center')}>{t('providers.inspector.col.enabled')}</th>
                        <th scope="col" className={TH}>{t('providers.inspector.col.cert')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {filteredHosts.map((host) => (
                        <tr key={String(host.id)} className="hover:bg-accent/40">
                          <td className={TD}>
                            <div className="flex flex-col gap-0.5 font-mono text-xs">
                              {(host.domains || []).map((d) => (
                                <span key={d} className="break-all">{d}</span>
                              ))}
                            </div>
                          </td>
                          <td className={cn(TD, 'font-mono text-xs break-all')}>{host.target || EM_DASH}</td>
                          <td className={cn(TD, 'text-center')}><BoolCell value={host.ssl} /></td>
                          <td className={cn(TD, 'text-center')}><BoolCell value={host.websocket} /></td>
                          <td className={cn(TD, 'text-center')}><BoolCell value={host.enabled ?? true} /></td>
                          <td className={cn(TD, 'font-mono text-xs')}>
                            {host.cert_id !== null && host.cert_id !== undefined && host.cert_id !== '' && host.cert_id !== 0
                              ? String(host.cert_id)
                              : <span className="text-muted-foreground">{EM_DASH}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {showRecords && (
            <section className="space-y-3" aria-labelledby={`inspector-records-${provider.id}`}>
              <SectionHeading
                id={`inspector-records-${provider.id}`}
                size="sm"
                icon={<Globe />}
                title={t('providers.inspector.records')}
                description={recordsQuery.isSuccess ? t('providers.inspector.records_count', { count: records.length }) : undefined}
              >
                {records.length > 6 && (
                  <SearchInput
                    size="sm"
                    value={recordSearch}
                    onChange={setRecordSearch}
                    placeholder={t('providers.inspector.search_records')}
                    aria-label={t('providers.inspector.search_records')}
                    wrapperClassName="w-56"
                  />
                )}
              </SectionHeading>

              {recordsQuery.isPending ? (
                <TableSkeleton columns={3} />
              ) : recordsQuery.isError ? (
                <SectionError
                  message={translateApiError(recordsQuery.error, t, t('providers.inspector.error_records'))}
                  onRetry={() => recordsQuery.refetch()}
                />
              ) : records.length === 0 ? (
                <EmptyState compact icon={<Globe />} title={t('providers.inspector.empty_records')} />
              ) : filteredRecords.length === 0 ? (
                <EmptyState
                  compact
                  title={t('providers.inspector.no_match')}
                  action={
                    <Button size="sm" variant="outline" onClick={() => setRecordSearch('')}>
                      {t('ui.search.clear')}
                    </Button>
                  }
                />
              ) : (
                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="w-full min-w-[480px] text-sm">
                    <thead className="bg-muted/60">
                      <tr>
                        <th scope="col" className={TH}>{t('providers.inspector.col.name')}</th>
                        {hasRecordType && <th scope="col" className={TH}>{t('providers.inspector.col.type')}</th>}
                        <th scope="col" className={TH}>{t('providers.inspector.col.value')}</th>
                        {hasRecordTtl && <th scope="col" className={cn(TH, 'text-right')}>{t('providers.inspector.col.ttl')}</th>}
                        {hasRecordProxied && <th scope="col" className={cn(TH, 'text-center')}>{t('providers.inspector.col.proxied')}</th>}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {filteredRecords.map((record, index) => (
                        <tr key={`${record.domain}-${record.answer}-${index}`} className="hover:bg-accent/40">
                          <td className={cn(TD, 'font-mono text-xs break-all')}>{record.domain}</td>
                          {hasRecordType && (
                            <td className={TD}>
                              {record.type ? <Badge tone="neutral" size="sm">{record.type}</Badge> : EM_DASH}
                            </td>
                          )}
                          <td className={cn(TD, 'font-mono text-xs break-all')}>{record.answer || EM_DASH}</td>
                          {hasRecordTtl && (
                            <td className={cn(TD, 'text-right tabular-nums')}>{typeof record.ttl === 'number' ? record.ttl : EM_DASH}</td>
                          )}
                          {hasRecordProxied && (
                            <td className={cn(TD, 'text-center')}><BoolCell value={record.proxied} /></td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </Drawer>
  );
}
