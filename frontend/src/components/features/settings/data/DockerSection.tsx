/**
 * Docker discovery -- containers found on an engine, each with a suggested route.
 *
 * Split out of `DataTab.tsx`; see `SyncSection.tsx` for why.
 */

import { useMemo, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, CheckCircle2, Container, Plus, RefreshCw, Search, Star, Trash2, Upload } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { useDockerDiscovery, type DockerContainer } from '@/hooks/useDockerDiscovery';
import { isDnsType, isProxyType } from '@/components/features/providers/providerConstants';
import { Badge, Button, Checkbox, EmptyState, Field, IconButton, InlineAlert, Input, Select, useConfirmDialog, type Tone } from '@/components/ui';
import { translateApiError } from '@/lib/errors';
import type { Provider } from '@/types/api';
import { SectionEyebrow, SettingsSection } from '../SettingsSection';

const DOCKER_HOST_RE = /^(unix|tcp|ssh):\/\//;
const CONFIDENCE_TONE: Record<DockerContainer['suggestion']['confidence'], Tone> = {
  high: 'success',
  medium: 'warning',
  low: 'neutral',
};

export function DockerSection() {
  const t = useT();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const docker = useDockerDiscovery();
  const [showAddEndpoint, setShowAddEndpoint] = useState(false);

  const { data: providers = [] } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const proxyProviders = useMemo(() => providers.filter((p) => isProxyType(p.type)), [providers]);
  const dnsProviders = useMemo(() => providers.filter((p) => isDnsType(p.type)), [providers]);

  const canAddEndpoint =
    docker.newDockerEndpointName.trim() !== '' && DOCKER_HOST_RE.test(docker.newDockerEndpointHost.trim());

  const submitEndpoint = (e: FormEvent) => {
    e.preventDefault();
    if (!canAddEndpoint) return;
    docker.addEndpointMutation.mutate(undefined, { onSuccess: () => setShowAddEndpoint(false) });
  };

  const requestDeleteEndpoint = async () => {
    const endpoint = docker.selectedEndpoint;
    if (!endpoint) return;
    const ok = await confirm({
      title: t('settings.docker.delete_endpoint_title'),
      message: t('settings.docker.delete_endpoint_message', { name: endpoint.name }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (ok) docker.deleteEndpointMutation.mutate(String(endpoint.id));
  };

  const containers = docker.dockerContainers;
  const selected = docker.selectedDockerIds;
  const selectable = containers.filter((c) => (c.suggestion?.target_port ?? c.target_port) !== null);
  const allSelected = selectable.length > 0 && selectable.every((c) => selected.includes(c.id));
  const someSelected = selected.length > 0 && !allSelected;

  const toggleContainer = (id: string) =>
    docker.setSelectedDockerIds(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  const requestImport = async () => {
    if (selected.length === 0) return;
    const ok = await confirm({
      title: t('settings.docker.import_title'),
      message: t('settings.docker.import_message', { count: selected.length, domain: docker.effectiveDomain || '—' }),
      confirmLabel: t('settings.migration.import'),
      variant: 'info',
    });
    if (ok) docker.importMutation.mutate();
  };

  const hasEndpoints = docker.dockerEndpoints.length > 0;

  return (
    <SettingsSection
      icon={<Container />}
      title={t('settings.docker.title')}
      description={t('settings.docker.desc')}
      actions={
        <Button
          variant="outline"
          size="sm"
          leftIcon={<Plus />}
          aria-expanded={showAddEndpoint}
          onClick={() => setShowAddEndpoint((v) => !v)}
        >
          {t('settings.docker.add_endpoint')}
        </Button>
      }
      footer={
        containers.length > 0 ? (
          <Button
            leftIcon={<Upload />}
            loading={docker.importMutation.isPending}
            disabled={selected.length === 0 || !docker.effectiveDomain}
            onClick={() => void requestImport()}
          >
            {t('settings.docker.import_selected', { count: selected.length })}
          </Button>
        ) : undefined
      }
    >
      {showAddEndpoint && (
        <form onSubmit={submitEndpoint} className="space-y-3 rounded-xl border border-border bg-muted/30 p-4 animate-in fade-in">
          <SectionEyebrow>{t('settings.docker.add_endpoint_title')}</SectionEyebrow>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] md:items-start">
            <Field label={t('settings.docker.endpoint_name_label')} required>
              <Input
                value={docker.newDockerEndpointName}
                placeholder={t('settings.docker.endpoint_name_placeholder')}
                autoComplete="off"
                onChange={(e) => docker.setNewDockerEndpointName(e.target.value)}
              />
            </Field>
            <Field label={t('settings.docker.endpoint_host_label')} required hint={t('settings.docker.endpoint_host_hint')}>
              <Input
                value={docker.newDockerEndpointHost}
                placeholder="unix:///var/run/docker.sock"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => docker.setNewDockerEndpointHost(e.target.value)}
                className="font-mono text-xs"
              />
            </Field>
            <div className="flex gap-2 md:mt-5.5">
              <Button type="submit" loading={docker.addEndpointMutation.isPending} disabled={!canAddEndpoint}>
                {t('common.add')}
              </Button>
              <Button variant="ghost" onClick={() => setShowAddEndpoint(false)}>
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        </form>
      )}

      {docker.endpointsQuery.isError ? (
        <InlineAlert
          tone="danger"
          title={t('settings.docker.endpoints_load_failed')}
          action={
            <Button
              variant="outline"
              size="sm"
              loading={docker.endpointsQuery.isFetching}
              onClick={() => void docker.endpointsQuery.refetch()}
            >
              {t('common.retry')}
            </Button>
          }
        >
          {translateApiError(docker.endpointsQuery.error, t, t('settings.docker.endpoints_load_failed_hint'))}
        </InlineAlert>
      ) : !hasEndpoints ? (
        <EmptyState
          compact
          icon={<Container />}
          title={t('settings.docker.no_endpoints')}
          description={t('settings.docker.no_endpoints_desc')}
          action={
            <Button size="sm" leftIcon={<Plus />} onClick={() => setShowAddEndpoint(true)}>
              {t('settings.docker.add_endpoint')}
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <Field
              label={t('settings.docker.endpoint_label')}
              labelAddon={
                docker.selectedEndpoint && !!docker.selectedEndpoint.is_default ? (
                  <Badge size="sm" tone="primary" icon={<Star />}>
                    {t('settings.docker.default_badge')}
                  </Badge>
                ) : undefined
              }
            >
              <div className="flex items-center gap-2">
                <Select
                  value={docker.effectiveEndpointId}
                  onChange={(e) => docker.setDockerEndpointId(e.target.value)}
                  wrapperClassName="min-w-0 flex-1"
                >
                  {docker.dockerEndpoints.map((ep) => (
                    <option key={ep.id} value={String(ep.id)}>
                      {ep.name} · {ep.docker_host}
                    </option>
                  ))}
                </Select>
                <IconButton
                  label={t('settings.docker.test_endpoint')}
                  icon={<RefreshCw className={cn(docker.testEndpointMutation.isPending && 'animate-spin')} />}
                  tooltip
                  variant="outline"
                  disabled={!docker.effectiveEndpointId || docker.testEndpointMutation.isPending}
                  onClick={() => docker.testEndpointMutation.mutate(docker.effectiveEndpointId)}
                />
                <IconButton
                  label={t('settings.docker.set_default')}
                  icon={<Star />}
                  tooltip
                  variant="outline"
                  disabled={
                    !docker.effectiveEndpointId ||
                    !!docker.selectedEndpoint?.is_default ||
                    docker.setDefaultEndpointMutation.isPending
                  }
                  onClick={() => docker.setDefaultEndpointMutation.mutate(docker.effectiveEndpointId)}
                />
                <IconButton
                  label={t('settings.docker.delete_endpoint')}
                  icon={<Trash2 />}
                  tooltip
                  variant="outline"
                  disabled={!docker.selectedEndpoint || docker.deleteEndpointMutation.isPending}
                  onClick={() => void requestDeleteEndpoint()}
                  className="text-muted-foreground hover:text-destructive"
                />
              </div>
            </Field>
            <Field label={t('settings.docker.domain_label')} required hint={t('settings.docker.domain_hint')}>
              <Select value={docker.effectiveDomain} onChange={(e) => docker.setDockerDomain(e.target.value)}>
                {docker.domains.length === 0 && <option value="">{t('settings.docker.domain_none')}</option>}
                {docker.domains.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <Field label={t('settings.docker.proxy_provider_label')}>
              <Select value={docker.dockerProxyProviderId} onChange={(e) => docker.setDockerProxyProviderId(e.target.value)}>
                <option value="">{t('settings.docker.provider_none')}</option>
                {proxyProviders.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('settings.docker.dns_provider_label')}>
              <Select value={docker.dockerDnsProviderId} onChange={(e) => docker.setDockerDnsProviderId(e.target.value)}>
                <option value="">{t('settings.docker.provider_none')}</option>
                {dnsProviders.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t('settings.docker.dns_ip_label')} hint={t('settings.docker.dns_ip_hint')}>
              <Input
                value={docker.dockerDnsIp}
                placeholder="10.0.0.10"
                inputMode="decimal"
                autoComplete="off"
                disabled={!docker.dockerDnsProviderId}
                onChange={(e) => docker.setDockerDnsIp(e.target.value)}
                className="font-mono"
              />
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              leftIcon={<Search />}
              loading={docker.discoverMutation.isPending}
              disabled={!docker.effectiveEndpointId}
              onClick={() => docker.discoverMutation.mutate()}
            >
              {docker.discoverMutation.isPending ? t('settings.docker.discovering') : t('settings.docker.discover')}
            </Button>
            {containers.length > 0 && (
              <Badge tone="neutral">{t('settings.docker.discovered', { count: containers.length })}</Badge>
            )}
          </div>

          {docker.discoverMutation.isSuccess && containers.length === 0 && (
            <EmptyState compact icon={<Box />} title={t('settings.docker.empty')} />
          )}

          {containers.length > 0 && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected}
                  disabled={selectable.length === 0}
                  onChange={(e) => docker.setSelectedDockerIds(e.target.checked ? selectable.map((c) => c.id) : [])}
                  label={t('settings.docker.select_all_ready')}
                  description={t('settings.docker.selected_count', { count: selected.length })}
                />
                <Button variant="ghost" size="sm" disabled={selected.length === 0} onClick={() => docker.setSelectedDockerIds([])}>
                  {t('settings.migration.clear_selection')}
                </Button>
              </div>
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full min-w-3xl text-xs">
                  <thead className="border-b border-border bg-muted/50">
                    <tr>
                      <th scope="col" className="w-10 px-3 py-2">
                        <span className="sr-only">{t('settings.migration.col_select')}</span>
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.docker.col_container')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.docker.col_subdomain')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.docker.col_target')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.docker.col_confidence')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_status')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {containers.map((c) => {
                      const port = c.suggestion?.target_port ?? c.target_port;
                      const scheme = c.suggestion?.forward_scheme ?? c.suggested_scheme ?? 'http';
                      const subdomain = c.suggestion?.subdomain ?? c.suggested_subdomain;
                      const confidence = c.suggestion?.confidence ?? 'low';
                      const canSelect = port !== null;
                      const checked = selected.includes(c.id);
                      return (
                        <tr key={c.id} className={cn('transition-colors', canSelect ? 'hover:bg-muted/30' : 'opacity-60', checked && 'bg-primary/5')}>
                          <td className="px-3 py-2">
                            <Checkbox
                              checked={checked}
                              disabled={!canSelect}
                              onChange={() => toggleContainer(c.id)}
                              aria-label={t('settings.docker.select_container_aria', { name: c.name })}
                            />
                          </td>
                          <td className="px-3 py-2">
                            <div className="font-medium text-foreground">{c.name}</div>
                            <div className="truncate font-mono text-[11px] text-muted-foreground">{c.image}</div>
                          </td>
                          <td className="px-3 py-2 font-mono text-foreground">
                            {subdomain}
                            {docker.effectiveDomain && <span className="text-muted-foreground">.{docker.effectiveDomain}</span>}
                          </td>
                          <td className="px-3 py-2 font-mono text-muted-foreground tabular-nums">
                            {canSelect ? `${scheme}://${c.target_ip}:${port}` : t('settings.docker.no_port')}
                          </td>
                          <td className="px-3 py-2">
                            <Badge size="sm" tone={CONFIDENCE_TONE[confidence]} dot>
                              {t(`settings.docker.confidence_${confidence}`)}
                            </Badge>
                          </td>
                          <td className="px-3 py-2">
                            {c.existing_service ? (
                              <Badge size="sm" tone="neutral" icon={<CheckCircle2 />} title={c.existing_service.fqdn}>
                                {t('settings.migration.status_tracked')}
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
        </>
      )}
      {ConfirmDialogElement}
    </SettingsSection>
  );
}

// ─── Backup export ────────────────────────────────────────────────────────────
