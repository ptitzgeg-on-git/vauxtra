import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Database, Plug, Plus, RefreshCw } from 'lucide-react';
import { api } from '@/api/client';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { ProviderModal } from '@/components/features/ProviderModal';
import { ProviderCard } from '@/components/features/providers/ProviderCard';
import { ProviderInspector } from '@/components/features/providers/ProviderInspector';
import {
  PROVIDER_GROUPS,
  type ProviderGroup,
  type ProviderTypeMeta,
  getProviderGroup,
  isTunnelType,
} from '@/components/features/providers/providerConstants';
import {
  DIAGNOSTIC_TTL_MS,
  DIAGNOSTICS_STORAGE_KEY,
  type HealthScore,
  type OperationalStatus,
  type ProviderDiagnostics,
  type ProviderSeverity,
  getHealthScore,
  getOperationalStatus,
  getProviderSeverity,
  isDiagnosticsFresh,
} from '@/components/features/providers/providerHealth';
import {
  Badge,
  Button,
  Chip,
  ChipGroup,
  EmptyState,
  PageHeader,
  SectionHeading,
  SkeletonCard,
  Tooltip,
  buttonVariants,
  useConfirmDialog,
} from '@/components/ui';
import { useFormat } from '@/hooks/useFormat';
import { describeDeleteConflict, isProviderDeleteConflict } from '@/hooks/useProviderMutations';
import { useT } from '@/i18n';
import { getErrorDetail, translateApiError, getHttpStatus } from '@/lib/errors';
import type {
  Provider,
  ProviderHealthStatus,
  ProviderHealthSummary,
  ProvidersHealthMap,
  TunnelHealthResponse,
} from '@/types/api';

type FocusFilter = 'all' | 'issues' | 'healthy';
type DiagnosticsMap = Record<number, ProviderDiagnostics>;

/**
 * What the last manual test round left behind, minus whatever has gone stale.
 *
 * This used to be a mount effect, so the page always painted once with no diagnostics at
 * all and then painted again a tick later: every badge the operator had earned by testing
 * their integrations flickered in on each navigation. `useState` takes its initial value
 * lazily, so the first paint already has them.
 */
/**
 * The rows busy with one action.
 *
 * This was a `number | null` per action, which cannot hold two rows at once. A second click
 * overwrote the first row's id, so that row's spinner stopped and its button re-enabled while
 * its request was still open; and whichever response came back first cleared the tracker
 * outright, wiping a spinner that by then belonged to another row. `toggling` drives
 * `disabled` on the enable/disable switch, so the same slip made a provider clickable in the
 * middle of its own write.
 *
 * Same shape as `actioningIds` in Services.tsx, which never had the bug.
 */
function useBusyIds() {
  const [ids, setIds] = useState<Set<number>>(() => new Set());
  const start = useCallback((id: number) => setIds((prev) => new Set(prev).add(id)), []);
  const end = useCallback((id: number) => {
    setIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);
  return { has: (id: number) => ids.has(id), start, end };
}

function restoreDiagnostics(): DiagnosticsMap {
  try {
    const raw = localStorage.getItem(DIAGNOSTICS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, ProviderDiagnostics>;
    const now = Date.now();
    const restored: DiagnosticsMap = {};
    for (const [id, diag] of Object.entries(parsed || {})) {
      if (isDiagnosticsFresh(diag, now)) restored[Number(id)] = diag;
    }
    return restored;
  } catch {
    try {
      localStorage.removeItem(DIAGNOSTICS_STORAGE_KEY);
    } catch {
      // Storage itself is unavailable; there is nothing to clean up.
    }
    return {};
  }
}
type RouteModal = { mode: 'create' } | { mode: 'edit'; provider: Provider };

interface ProviderSignals {
  diag?: ProviderDiagnostics;
  tunnel?: ProviderHealthStatus;
  auto?: ProviderHealthSummary;
  health: HealthScore;
  status: OperationalStatus;
  severity: ProviderSeverity;
}

/** `GET /providers/health` answers a bare id→summary map today; older builds wrapped it in `{items}`. */
function unwrapHealthMap(raw: ProvidersHealthMap | { items?: ProvidersHealthMap } | undefined): Record<number, ProviderHealthSummary> {
  if (!raw || typeof raw !== 'object') return {};
  const source = 'items' in raw && raw.items && typeof raw.items === 'object' ? raw.items : (raw as ProvidersHealthMap);
  const out: Record<number, ProviderHealthSummary> = {};
  for (const [id, summary] of Object.entries(source)) {
    if (summary && typeof summary === 'object') out[Number(id)] = summary as ProviderHealthSummary;
  }
  return out;
}

export function Providers() {
  const t = useT();
  const queryClient = useQueryClient();
  const { formatDateTime, formatRelative } = useFormat();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const [searchParams, setSearchParams] = useSearchParams();

  const setParam = useCallback(
    (name: string, value: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(name, value);
          else next.delete(name);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // --- state --------------------------------------------------------------
  const [routeModal, setRouteModal] = useState<RouteModal | null>(null);
  const [inspectId, setInspectId] = useState<number | null>(null);
  const [focusFilter, setFocusFilter] = useState<FocusFilter>('all');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [storedDiagnostics, setDiagnostics] = useState<DiagnosticsMap>(restoreDiagnostics);
  const testing = useBusyIds();
  const validating = useBusyIds();
  const deleting = useBusyIds();
  const toggling = useBusyIds();

  // --- data ---------------------------------------------------------------
  const providersQuery = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const typesQuery = useProviderTypes();
  const tunnelHealthQuery = useQuery<TunnelHealthResponse>({
    queryKey: ['providers-tunnel-health'],
    queryFn: () => api.get<TunnelHealthResponse>('/providers/tunnels/health'),
    refetchInterval: 30000,
  });
  const allHealthQuery = useQuery<ProvidersHealthMap | { items?: ProvidersHealthMap }>({
    queryKey: ['providers-health'],
    queryFn: () => api.get<ProvidersHealthMap>('/providers/health'),
    refetchInterval: 60000,
  });

  const providers = useMemo(() => (Array.isArray(providersQuery.data) ? providersQuery.data : []), [providersQuery.data]);
  const typeMap = useMemo(() => typesQuery.data || {}, [typesQuery.data]);
  const metaFor = useCallback((provider: Provider): ProviderTypeMeta | undefined => typeMap[String(provider.type || '').toLowerCase()], [typeMap]);

  const tunnelHealthById = useMemo(() => {
    const out: Record<number, ProviderHealthStatus> = {};
    for (const item of Array.isArray(tunnelHealthQuery.data?.items) ? tunnelHealthQuery.data.items : []) {
      out[Number(item.id)] = item.health || { ok: false };
    }
    return out;
  }, [tunnelHealthQuery.data]);
  const autoHealthById = useMemo(() => unwrapHealthMap(allHealthQuery.data), [allHealthQuery.data]);

  // --- manual diagnostics: restored from localStorage, pruned to known ids --
  // Pruning was an effect, and an effect that prunes is a second render: the list arrived,
  // the page painted with a result for an integration that no longer exists, and only then
  // did the effect write the shorter map. It is a function of what was stored and what the
  // server returned, so it is computed -- one paint, and no second copy to keep in sync.
  const diagnostics = useMemo(() => {
    if (providers.length === 0) return storedDiagnostics;
    const ids = new Set(providers.map((p) => Number(p.id)));
    const next: DiagnosticsMap = {};
    let changed = false;
    for (const [id, diag] of Object.entries(storedDiagnostics)) {
      if (ids.has(Number(id))) next[Number(id)] = diag;
      else changed = true;
    }
    return changed ? next : storedDiagnostics;
  }, [providers, storedDiagnostics]);

  useEffect(() => {
    try {
      localStorage.setItem(DIAGNOSTICS_STORAGE_KEY, JSON.stringify(diagnostics));
    } catch {
      // Private mode / quota: the page still works without persistence.
    }
  }, [diagnostics]);

  const storeDiagnostics = useCallback((id: number, diag: ProviderDiagnostics) => {
    setDiagnostics((prev) => ({ ...prev, [id]: { ...diag, testedAt: Date.now() } }));
  }, []);

  // --- derived signals ----------------------------------------------------
  const signalsById = useMemo(() => {
    const now = Date.now();
    const out: Record<number, ProviderSignals> = {};
    for (const provider of providers) {
      const id = Number(provider.id);
      const raw = diagnostics[id];
      const diag = isDiagnosticsFresh(raw, now) ? raw : undefined;
      const tunnel = tunnelHealthById[id];
      const auto = autoHealthById[id];
      const health = getHealthScore(provider, { diag, tunnel, auto }, t);
      out[id] = {
        diag,
        tunnel,
        auto,
        health,
        status: getOperationalStatus(provider, health),
        severity: getProviderSeverity(provider, health),
      };
    }
    return out;
  }, [providers, diagnostics, tunnelHealthById, autoHealthById, t]);

  const issueCount = providers.filter((p) => ['degraded', 'error'].includes(signalsById[Number(p.id)]?.severity)).length;
  const healthyCount = providers.filter((p) => signalsById[Number(p.id)]?.severity === 'healthy').length;

  const matchesFocus = useCallback(
    (provider: Provider) => {
      const severity = signalsById[Number(provider.id)]?.severity;
      if (focusFilter === 'issues') return severity === 'degraded' || severity === 'error';
      if (focusFilter === 'healthy') return severity === 'healthy';
      return true;
    },
    [focusFilter, signalsById],
  );

  const sections = useMemo(() => {
    const byGroup: Record<ProviderGroup, Provider[]> = { reverse: [], tunnel: [], dns: [], other: [] };
    for (const provider of providers) byGroup[getProviderGroup(String(provider.type || ''), metaFor(provider))].push(provider);
    return PROVIDER_GROUPS.map((group) => ({ group, items: byGroup[group] })).filter((s) => s.items.length > 0);
  }, [providers, metaFor]);
  const visibleSections = sections
    .map((section) => ({ ...section, items: section.items.filter(matchesFocus) }))
    .filter((section) => section.items.length > 0);

  const lastManualCheckAt = Object.values(diagnostics).reduce((max, diag) => Math.max(max, Number(diag?.testedAt || 0)), 0);
  const hasFreshManualCheck = lastManualCheckAt > 0 && Date.now() - lastManualCheckAt <= DIAGNOSTIC_TTL_MS;

  // --- modal / URL contract: ?new=1, ?edit=<id> ---------------------------
  const openCreate = useCallback(() => setRouteModal({ mode: 'create' }), []);
  const openEdit = useCallback((provider: Provider) => setRouteModal({ mode: 'edit', provider }), []);
  const closeModal = useCallback(() => setRouteModal(null), []);

  useEffect(() => {
    if (searchParams.get('new')) {
      setParam('new', null);
      // The URL is the instruction here: `?new=1` means "open the create form", and the
      // form is what the operator followed the link for. Deferring it would paint the page
      // once without it, which is the flicker this rule exists to prevent.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      openCreate();
      return;
    }
    const editId = searchParams.get('edit');
    if (editId) {
      // Pending and failed both mean "the list is not here yet". Only pending was checked,
      // so a failed load left `providers` empty, fell through to the else, and told the
      // operator the integration does not exist -- while `setParam('edit', null)` had
      // already destroyed the deep link, so Retry could not reopen it either.
      if (providersQuery.isPending || providersQuery.isError) return;
      setParam('edit', null);
      const provider = providers.find((p) => String(p.id) === editId);
      if (provider) openEdit(provider);
      else toast.error(t('providers.toast.not_found', { id: editId }));
    }
  }, [
    searchParams,
    providers,
    providersQuery.isPending,
    providersQuery.isError,
    setParam,
    openCreate,
    openEdit,
    t,
  ]);

  // --- actions ------------------------------------------------------------
  const handleRefresh = async () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    try {
      const [fresh] = await Promise.all([
        providersQuery.refetch(),
        typesQuery.refetch(),
        tunnelHealthQuery.refetch(),
        allHealthQuery.refetch(),
      ]);
      const list = Array.isArray(fresh.data) ? fresh.data : providers;
      const enabled = list.filter((p) => Boolean(p.enabled));
      if (enabled.length === 0) {
        toast.success(t('providers.refresh.success_no_enabled'));
        return;
      }
      const entries = await Promise.all(
        enabled.map(async (provider) => {
          try {
            const data = await api.post<ProviderDiagnostics>(`/providers/${provider.id}/test`);
            return [Number(provider.id), { ...data, testedAt: Date.now() }] as const;
          } catch (error: unknown) {
            const detail = translateApiError(error, t, t('providers.toast.connection_failed'));
            return [
              Number(provider.id),
              { ok: false, provider: provider.name, health: { ok: false, status: 'error', error: detail }, testedAt: Date.now() },
            ] as const;
          }
        }),
      );
      setDiagnostics((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
      const failed = entries.filter(([, data]) => !data?.ok).length;
      if (failed === 0) toast.success(t('providers.refresh.success_all_passed'));
      else toast.error(t('providers.refresh.failed_count', { failed, total: enabled.length }));
    } catch {
      toast.error(t('providers.refresh.failed'));
    } finally {
      setIsRefreshing(false);
    }
  };

  const testConnection = useMutation({
    // Marked busy in `onMutate` rather than inside `mutationFn`, so the pairing with
    // `onSettled` is explicit: both are handed the same `provider`, and only that row clears.
    onMutate: (provider: Provider) => testing.start(Number(provider.id)),
    mutationFn: (provider: Provider) => api.post<ProviderDiagnostics>(`/providers/${provider.id}/test`),
    onSuccess: (data, provider) => {
      storeDiagnostics(Number(provider.id), data);
      const name = data?.provider || provider.name;
      if (data?.ok) toast.success(t('providers.toast.test_ok', { name }));
      else toast.error(t('providers.toast.test_failed', { name }));
    },
    onError: (error: unknown, provider) => {
      const msg = translateApiError(error, t, t('providers.toast.connection_failed'));
      toast.error(msg);
      storeDiagnostics(Number(provider.id), { ok: false, provider: provider.name, health: { ok: false, status: 'error', error: msg } });
    },
    onSettled: (_data, _error, provider) => {
      testing.end(Number(provider.id));
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });

  const validateProvider = useMutation({
    onMutate: (provider: Provider) => validating.start(Number(provider.id)),
    mutationFn: (provider: Provider) =>
      api.post<ProviderDiagnostics>(`/providers/${provider.id}/validate`, { write_probe: false }),
    onSuccess: (data, provider) => {
      storeDiagnostics(Number(provider.id), data);
      const name = data?.provider || provider.name;
      if (data?.ok) toast.success(t('providers.toast.validate_ok', { name }));
      else toast.error(t('providers.toast.validate_failed', { name }));
      queryClient.invalidateQueries({ queryKey: ['providers-tunnel-health'] });
    },
    onError: (error: unknown) => {
      toast.error(translateApiError(error, t, t('providers.toast.validate_error')));
    },
    onSettled: (_data, _error, provider) => validating.end(Number(provider.id)),
  });

  const toggleEnabled = useMutation({
    onMutate: ({ provider }: { provider: Provider; enabled: boolean }) => toggling.start(Number(provider.id)),
    mutationFn: ({ provider, enabled }: { provider: Provider; enabled: boolean }) =>
      api.put<{ ok: boolean }>(`/providers/${provider.id}`, { enabled: enabled ? 1 : 0 }),
    onSuccess: (_data, { provider, enabled }) => {
      toast.success(t(enabled ? 'providers.toast.enabled' : 'providers.toast.disabled', { name: provider.name }));
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['providers-health'] });
      queryClient.invalidateQueries({ queryKey: ['providers-tunnel-health'] });
    },
    onError: (error: unknown) => {
      toast.error(translateApiError(error, t, t('providers.toast.update_failed')));
    },
    onSettled: (_data, _error, { provider }) => toggling.end(Number(provider.id)),
  });

  const deleteProvider = useMutation({
    mutationFn: ({ id, force }: { id: number; force?: boolean }) => api.delete(`/providers/${id}${force ? '?force=true' : ''}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      queryClient.invalidateQueries({ queryKey: ['services'] });
      toast.success(t('providers.toast.deleted'));
    },
  });

  const handleDelete = async (provider: Provider) => {
    const id = Number(provider.id);
    const name = provider.name;
    const ok = await confirm({
      title: t('providers.delete.title'),
      message: t('providers.delete.message', { name }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (!ok) return;
    deleting.start(id);
    try {
      await deleteProvider.mutateAsync({ id });
      if (inspectId === id) setInspectId(null);
      deleting.end(id);
    } catch (error: unknown) {
      const detail = getErrorDetail(error);
      if (getHttpStatus(error) === 409 && isProviderDeleteConflict(detail)) {
        const count = detail.services.length;
        const force = await confirm({
          title: t('providers.delete.deps_title'),
          message: t('providers.delete.deps_message', { count, name, list: describeDeleteConflict(detail, t) }),
          confirmLabel: t('providers.delete.force_confirm'),
          variant: 'warning',
        });
        if (force) {
          deleteProvider.mutate(
            { id, force: true },
            {
              onSettled: () => deleting.end(id),
              onError: (err: unknown) => toast.error(translateApiError(err, t, t('providers.toast.delete_failed'))),
              onSuccess: () => {
                if (inspectId === id) setInspectId(null);
              },
            },
          );
        } else {
          deleting.end(id);
        }
      } else {
        toast.error(translateApiError(error, t, t('providers.toast.delete_failed')));
        deleting.end(id);
      }
    }
  };

  const inspectProvider = inspectId === null ? null : providers.find((p) => Number(p.id) === inspectId) || null;
  const total = providers.length;

  // --- render -------------------------------------------------------------
  return (
    <div className="space-y-6 pb-8 animate-in fade-in duration-200">
      <PageHeader
        icon={<Plug />}
        title={t('providers.title')}
        description={t('providers.description')}
        meta={
          <div className="flex flex-wrap items-center gap-2">
            {providersQuery.isSuccess && (
              <Badge tone="neutral" size="sm">
                {t(total === 1 ? 'providers.meta.count_one' : 'providers.meta.count_other', { count: total })}
              </Badge>
            )}
            {hasFreshManualCheck ? (
              <Tooltip content={formatDateTime(new Date(lastManualCheckAt))}>
                <Badge tone="info" size="sm" className="cursor-default">
                  {t('providers.meta.last_check', { when: formatRelative(new Date(lastManualCheckAt)) })}
                </Badge>
              </Tooltip>
            ) : (
              <Badge tone="neutral" size="sm">{t('providers.meta.no_check')}</Badge>
            )}
            {issueCount > 0 && (
              <Badge tone="warning" size="sm" dot>
                {t('providers.filter.issues')} · {issueCount}
              </Badge>
            )}
          </div>
        }
        actions={
          <>
            <Link to="/settings?tab=data" className={buttonVariants({ variant: 'outline' })}>
              <Database className="h-4 w-4" aria-hidden="true" />
              {t('providers.import_link')}
            </Link>
            <Button variant="secondary" leftIcon={<RefreshCw />} loading={isRefreshing} onClick={handleRefresh} disabled={providersQuery.isPending}>
              {t('providers.refresh.button')}
            </Button>
            <Button leftIcon={<Plus />} onClick={openCreate}>
              {t('providers.add')}
            </Button>
          </>
        }
      />

      {providersQuery.isPending ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label={t('ui.loading')}>
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : providersQuery.isError ? (
        <EmptyState
          icon={<Plug />}
          title={t('providers.error_title')}
          description={translateApiError(providersQuery.error, t, t('providers.error_body'))}
          action={
            <Button variant="outline" leftIcon={<RefreshCw />} onClick={() => providersQuery.refetch()}>
              {t('providers.retry')}
            </Button>
          }
        />
      ) : total === 0 ? (
        <EmptyState
          icon={<Plug />}
          title={t('providers.empty_title')}
          description={t('providers.empty_body')}
          action={
            <Button leftIcon={<Plus />} onClick={openCreate}>
              {t('providers.add')}
            </Button>
          }
          actions={
            <Link to="/settings?tab=data" className={buttonVariants({ variant: 'ghost' })}>
              {t('providers.import_link')}
            </Link>
          }
        />
      ) : (
        <>
          <ChipGroup label={t('providers.filter.label')}>
            <Chip size="sm" selected={focusFilter === 'all'} onClick={() => setFocusFilter('all')} count={total}>
              {t('providers.filter.all')}
            </Chip>
            <Chip size="sm" tone="warning" selected={focusFilter === 'issues'} onClick={() => setFocusFilter('issues')} count={issueCount}>
              {t('providers.filter.issues')}
            </Chip>
            <Chip size="sm" tone="success" selected={focusFilter === 'healthy'} onClick={() => setFocusFilter('healthy')} count={healthyCount}>
              {t('providers.filter.healthy')}
            </Chip>
          </ChipGroup>

          {visibleSections.length === 0 ? (
            <EmptyState
              compact
              title={t('providers.no_match_title')}
              description={t('providers.no_match_body')}
              action={
                <Button variant="outline" onClick={() => setFocusFilter('all')}>
                  {t('providers.show_all')}
                </Button>
              }
            />
          ) : (
            visibleSections.map(({ group, items }) => (
              <section key={group} className="space-y-3" aria-label={t(`providers.section.${group}`)}>
                <SectionHeading title={t(`providers.section.${group}`)} size="sm">
                  <Badge tone="neutral" size="sm">{items.length}</Badge>
                </SectionHeading>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {items.map((provider) => {
                    const id = Number(provider.id);
                    const meta = metaFor(provider);
                    const signals = signalsById[id];
                    return (
                      <ProviderCard
                        key={id}
                        provider={provider}
                        meta={meta}
                        health={signals.health}
                        status={signals.status}
                        diagnostics={signals.diag}
                        tunnelHealth={isTunnelType(String(provider.type || ''), meta) ? signals.tunnel : undefined}
                        autoHealth={signals.auto}
                        testing={testing.has(id)}
                        validating={validating.has(id)}
                        deleting={deleting.has(id)}
                        toggling={toggling.has(id)}
                        onTest={() => testConnection.mutate(provider)}
                        onValidate={() => validateProvider.mutate(provider)}
                        onInspect={() => setInspectId(id)}
                        onEdit={() => openEdit(provider)}
                        onDelete={() => handleDelete(provider)}
                        onToggleEnabled={(enabled) => toggleEnabled.mutate({ provider, enabled })}
                      />
                    );
                  })}
                </div>
              </section>
            ))
          )}
        </>
      )}

      <ProviderModal
        isOpen={routeModal !== null}
        onClose={closeModal}
        provider={routeModal?.mode === 'edit' ? routeModal.provider : null}
      />
      <ProviderInspector
        provider={inspectProvider}
        meta={inspectProvider ? metaFor(inspectProvider) : undefined}
        open={inspectId !== null && inspectProvider !== null}
        onClose={() => setInspectId(null)}
      />
      {ConfirmDialogElement}
    </div>
  );
}
