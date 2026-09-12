import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  ArrowRightLeft,
  CircleAlert,
  FilterX,
  Globe,
  Import,
  LayoutGrid,
  LayoutList,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Trash2,
  Waypoints,
  X,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  buttonVariants,
  Checkbox,
  EmptyState,
  IconButton,
  Kbd,
  PageHeader,
  SearchInput,
  Select,
  SkeletonCard,
  SkeletonRow,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  useConfirmDialog,
} from '@/components/ui';
import { ExposeModal } from '@/components/features/expose/ExposeModal';
import { templateToFormState, type FormState } from '@/components/features/expose/types';
import { DriftDrawer } from '@/components/features/services/DriftDrawer';
import { ServiceCard } from '@/components/features/services/ServiceCard';
import { ServiceRow } from '@/components/features/services/ServiceRow';
import {
  MODE_FILTERS,
  buildServicePayload,
  isStatusFilter,
  matchesMode,
  matchesSearch,
  publicHostOf,
  type ModeFilter,
  type StatusFilter,
} from '@/components/features/services/helpers';
import type {
  BulkActionResult,
  DriftResult,
  Environment,
  Provider,
  ReconcileResult,
  Service,
  ServiceCheckResult,
  Tag,
  TemplateApplyResult,
} from '@/types/api';

// ---------------------------------------------------------------------------
// Local persistence (keys are part of the page's contract — keep them)
// ---------------------------------------------------------------------------

function useLocalStorage<T>(key: string, fallback: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw !== null ? (JSON.parse(raw) as T) : fallback;
    } catch {
      return fallback;
    }
  });
  const set = useCallback(
    (v: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const next = typeof v === 'function' ? (v as (prev: T) => T)(prev) : v;
        try {
          localStorage.setItem(key, JSON.stringify(next));
        } catch {
          // Private mode or quota: the in-memory value still wins.
        }
        return next;
      });
    },
    [key],
  );
  return [value, set];
}

type ViewMode = 'list' | 'grid';

type RouteModal = {
  key: string;
  mode: 'create' | 'edit';
  service?: Service;
  initialState?: FormState;
  templateName?: string;
};

type DeleteResult = { ok: boolean; errors?: string[] };

const MODE_ICONS: Record<ModeFilter, ReactElement | undefined> = {
  all: undefined,
  tunnel: <Waypoints />,
  proxy: <ArrowRightLeft />,
  dns: <Globe />,
  disabled: <PowerOff />,
};

const isEditable = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
};

const isModeFilter = (value: string): value is ModeFilter => (MODE_FILTERS as string[]).includes(value);

export function Services() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const [searchParams, setSearchParams] = useSearchParams();

  // --- persisted UI state -------------------------------------------------
  const [search, setSearch] = useLocalStorage('vauxtra.services.search', '');
  const [viewMode, setViewMode] = useLocalStorage<ViewMode>('vauxtra.services.viewMode', 'list');
  const [modeFilter, setModeFilter] = useLocalStorage<ModeFilter>('vauxtra.services.mode', 'all');

  // --- URL-driven filters (shared links from the dashboard) ---------------
  const tagFilter = Number(searchParams.get('tag')) || null;
  const envFilter = Number(searchParams.get('env')) || null;
  const statusParam = searchParams.get('status');
  const statusFilter: StatusFilter | null = isStatusFilter(statusParam) ? statusParam : null;

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

  // --- transient state ----------------------------------------------------
  const [rawSelectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [actioningIds, setActioningIds] = useState<Set<number>>(() => new Set());
  const [checkById, setCheckById] = useState<Record<number, ServiceCheckResult>>({});
  const [driftByService, setDriftByService] = useState<Record<number, DriftResult>>({});
  const [driftErrorById, setDriftErrorById] = useState<Record<number, string>>({});
  const [reconcileByService, setReconcileByService] = useState<Record<number, ReconcileResult>>({});
  const [driftDrawerId, setDriftDrawerId] = useState<number | null>(null);
  const [routeModal, setRouteModal] = useState<RouteModal | null>(null);
  const createNonce = useRef(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const templateFetchRef = useRef<string | null>(null);

  const startAction = useCallback((id: number) => {
    setActioningIds((prev) => new Set(prev).add(id));
  }, []);
  const endAction = useCallback((id: number) => {
    setActioningIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  // --- data ---------------------------------------------------------------
  const servicesQuery = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
  });
  const providersQuery = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const tagsQuery = useQuery<Tag[]>({ queryKey: ['tags'], queryFn: () => api.get<Tag[]>('/tags') });
  const environmentsQuery = useQuery<Environment[]>({
    queryKey: ['environments'],
    queryFn: () => api.get<Environment[]>('/environments'),
  });

  const services = useMemo(() => (Array.isArray(servicesQuery.data) ? servicesQuery.data : []), [servicesQuery.data]);

  // A selection survives a refetch, and some of what it holds may not: a service deleted in
  // another tab, or by the bulk action that just ran. Dropping those ids was an effect, so
  // the toolbar painted "3 selected" over two rows before correcting itself, and a bulk
  // action fired in between carried an id the server no longer knows. The live selection is
  // the stored one intersected with what exists, which is a derivation, not a state.
  const selectedIds = useMemo(() => {
    if (rawSelectedIds.size === 0) return rawSelectedIds;
    const known = new Set(services.map((s) => s.id));
    if ([...rawSelectedIds].every((id) => known.has(id))) return rawSelectedIds;
    return new Set([...rawSelectedIds].filter((id) => known.has(id)));
  }, [services, rawSelectedIds]);
  const providers = useMemo(() => (Array.isArray(providersQuery.data) ? providersQuery.data : []), [providersQuery.data]);
  const tags = useMemo(() => (Array.isArray(tagsQuery.data) ? tagsQuery.data : []), [tagsQuery.data]);
  const environments = useMemo(
    () => (Array.isArray(environmentsQuery.data) ? environmentsQuery.data : []),
    [environmentsQuery.data],
  );

  const invalidateServices = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['services'] });
  }, [queryClient]);
  const invalidateAfterPush = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['services'] });
    queryClient.invalidateQueries({ queryKey: ['logs'] });
    queryClient.invalidateQueries({ queryKey: ['health'] });
  }, [queryClient]);

  /**
   * Anything that pushes to a provider makes a cached drift report obsolete, and those reports
   * live in component state that no query invalidation can reach. Forget them so the row badge
   * stops asserting a state that is no longer true and the drawer re-checks when next opened.
   */
  const forgetDrift = useCallback((ids: number[]) => {
    const drop = <T,>(prev: Record<number, T>) => {
      if (!ids.some((id) => id in prev)) return prev;
      const next = { ...prev };
      ids.forEach((id) => delete next[id]);
      return next;
    };
    setDriftByService(drop);
    setDriftErrorById(drop);
    setReconcileByService(drop);
  }, []);

  // --- modal helpers ------------------------------------------------------
  const openCreate = useCallback((seed?: { initialState: FormState; templateName?: string; templateId?: string }) => {
    createNonce.current += 1;
    const suffix = seed?.templateId ? `template-${seed.templateId}-${createNonce.current}` : `create-${createNonce.current}`;
    setRouteModal({ key: suffix, mode: 'create', initialState: seed?.initialState, templateName: seed?.templateName });
  }, []);
  const openEdit = useCallback((service: Service) => {
    setRouteModal({ key: `edit-${service.id}`, mode: 'edit', service });
  }, []);
  const closeRouteModal = useCallback(() => setRouteModal(null), []);

  // --- URL contract: ?new=1, ?edit=<id>, ?template=<id> -------------------
  useEffect(() => {
    if (searchParams.get('new')) {
      setParam('new', null);
      // The URL is the instruction: `?new=1` means "open the create form", and the form is
      // what the operator followed the link for. Opening it on the next tick instead would
      // paint the page once without it.
      openCreate();
      return;
    }
    const templateId = searchParams.get('template');
    if (templateId) {
      if (templateFetchRef.current === templateId) return;
      templateFetchRef.current = templateId;
      setParam('template', null);
      api
        .get<TemplateApplyResult>(`/templates/${encodeURIComponent(templateId)}/apply`)
        .then((tpl) => {
          openCreate({
            initialState: templateToFormState(tpl),
            templateName: tpl?._template_name ? String(tpl._template_name) : undefined,
            templateId,
          });
        })
        .catch((err) => {
          toast.error(translateApiError(err, t, t('services.toast.template_load_failed')));
        })
        .finally(() => {
          templateFetchRef.current = null;
        });
      return;
    }
    const editId = searchParams.get('edit');
    if (editId) {
      // Pending and failed both mean "the list is not here yet". Only pending was checked,
      // so a failed load left `services` empty, fell through to the else, and told the
      // operator the endpoint does not exist -- while `setParam('edit', null)` had already
      // destroyed the deep link, so Retry could not reopen it either.
      if (servicesQuery.isPending || servicesQuery.isError) return;
      setParam('edit', null);
      const service = services.find((s) => String(s.id) === editId);
      // Same contract as `?new=1`: the link names a service to edit, and the drawer is the
      // page the operator asked for, not a follow-up to it.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (service) openEdit(service);
      else toast.error(t('services.toast.not_found', { id: editId }));
    }
  }, [
    searchParams,
    services,
    servicesQuery.isPending,
    servicesQuery.isError,
    setParam,
    openCreate,
    openEdit,
    t,
  ]);

  // --- filtering ----------------------------------------------------------
  const baseFiltered = useMemo(
    () =>
      services.filter((s) => {
        if (!matchesSearch(s, search)) return false;
        if (tagFilter && !(Array.isArray(s.tags) && s.tags.some((tag) => tag.id === tagFilter))) return false;
        if (envFilter && !(Array.isArray(s.environments) && s.environments.some((env) => env.id === envFilter))) return false;
        if (statusFilter && (s.status || 'unknown') !== statusFilter) return false;
        return true;
      }),
    [services, search, tagFilter, envFilter, statusFilter],
  );

  const modeCounts = useMemo(() => {
    const counts: Record<ModeFilter, number> = { all: 0, tunnel: 0, proxy: 0, dns: 0, disabled: 0 };
    for (const s of baseFiltered) {
      for (const mode of MODE_FILTERS) if (matchesMode(s, mode)) counts[mode] += 1;
    }
    return counts;
  }, [baseFiltered]);

  const filteredServices = useMemo(
    () => baseFiltered.filter((s) => matchesMode(s, modeFilter)),
    [baseFiltered, modeFilter],
  );

  const hasFilters = Boolean(search.trim()) || Boolean(tagFilter) || Boolean(envFilter) || Boolean(statusFilter) || modeFilter !== 'all';
  const clearFilters = useCallback(() => {
    setSearch('');
    setModeFilter('all');
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('tag');
        next.delete('env');
        next.delete('status');
        return next;
      },
      { replace: true },
    );
  }, [setSearch, setModeFilter, setSearchParams]);

  // --- selection ----------------------------------------------------------
  const visibleIds = useMemo(() => filteredServices.map((s) => s.id), [filteredServices]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const someVisibleSelected = visibleIds.some((id) => selectedIds.has(id));

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      const everySelected = visibleIds.length > 0 && visibleIds.every((id) => prev.has(id));
      return everySelected ? new Set() : new Set(visibleIds);
    });
  }, [visibleIds]);
  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  // --- keyboard shortcuts -------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (routeModal || driftDrawerId !== null) return;
      const editing = isEditable(e.target);
      if (e.key === '/' && !editing) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (e.key === 'Escape') {
        if (selectedIds.size > 0) {
          clearSelection();
        } else if (document.activeElement === searchRef.current) {
          searchRef.current?.blur();
        }
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && !editing && visibleIds.length > 0) {
        e.preventDefault();
        toggleSelectAll();
        return;
      }
      if (e.key.toLowerCase() === 'n' && !editing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        openCreate();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [routeModal, driftDrawerId, selectedIds.size, visibleIds.length, clearSelection, toggleSelectAll, openCreate]);

  // --- mutations ----------------------------------------------------------
  const toggleStatus = useMutation({
    mutationFn: (service: Service) =>
      api.put<Service>(`/services/${service.id}`, buildServicePayload(service, { enabled: !service.enabled })),
    onMutate: (service) => startAction(service.id),
    onSuccess: (_data, service) => {
      invalidateAfterPush();
      forgetDrift([service.id]);
      const host = publicHostOf(service);
      toast.success(service.enabled ? t('services.toast.disabled', { host }) : t('services.toast.enabled', { host }));
    },
    onError: (err, service) => {
      toast.error(translateApiError(err, t, t('services.toast.update_failed', { host: publicHostOf(service) })));
    },
    onSettled: (_data, _err, service) => endAction(service.id),
  });

  const deleteService = useMutation({
    mutationFn: (service: Service) => api.delete<DeleteResult>(`/services/${service.id}`),
    onMutate: (service) => startAction(service.id),
    onSuccess: (result, service) => {
      invalidateAfterPush();
      forgetDrift([service.id]);
      setSelectedIds((prev) => {
        if (!prev.has(service.id)) return prev;
        const next = new Set(prev);
        next.delete(service.id);
        return next;
      });
      const host = publicHostOf(service);
      const errors = Array.isArray(result?.errors) ? result.errors : [];
      if (errors.length > 0) {
        const shown = errors.slice(0, 2).join(' · ');
        const more = errors.length > 2 ? t('services.toast.more', { count: errors.length - 2 }) : '';
        toast(t('services.toast.deleted_warnings', { host, errors: shown, more }), { icon: '⚠️', duration: 8000 });
      } else {
        toast.success(t('services.toast.deleted', { host }));
      }
    },
    onError: (err, service) => {
      toast.error(translateApiError(err, t, t('services.toast.delete_failed', { host: publicHostOf(service) })));
    },
    onSettled: (_data, _err, service) => endAction(service.id),
  });

  const checkService = useMutation({
    mutationFn: (service: Service) => api.post<ServiceCheckResult>(`/services/${service.id}/check`),
    onMutate: (service) => startAction(service.id),
    onSuccess: (result, service) => {
      setCheckById((prev) => ({ ...prev, [service.id]: result }));
      invalidateServices();
    },
    onError: (err, service) => {
      toast.error(translateApiError(err, t, t('services.toast.check_failed', { host: publicHostOf(service) })));
    },
    onSettled: (_data, _err, service) => endAction(service.id),
  });

  const checkDrift = useMutation({
    mutationFn: (service: Service) => api.get<DriftResult>(`/services/${service.id}/drift`),
    onMutate: (service) => {
      setDriftErrorById((prev) => {
        if (!(service.id in prev)) return prev;
        const next = { ...prev };
        delete next[service.id];
        return next;
      });
    },
    onSuccess: (result, service) => {
      setDriftByService((prev) => ({ ...prev, [service.id]: result }));
    },
    onError: (err, service) => {
      const message = translateApiError(err, t, t('services.drift.check_failed'));
      setDriftErrorById((prev) => ({ ...prev, [service.id]: message }));
      if (driftDrawerId !== service.id) toast.error(message);
    },
  });

  const reconcileService = useMutation({
    mutationFn: (service: Service) => api.post<ReconcileResult>(`/services/${service.id}/reconcile`),
    onMutate: (service) => startAction(service.id),
    onSuccess: (result, service) => {
      setReconcileByService((prev) => ({ ...prev, [service.id]: result }));
      if (result?.after) setDriftByService((prev) => ({ ...prev, [service.id]: result.after }));
      invalidateAfterPush();
      const host = publicHostOf(service);
      if (result?.ok) toast.success(t('services.toast.reconciled', { host }));
      else toast.error(t('services.toast.reconcile_partial', { host }));
    },
    onError: (err, service) => {
      toast.error(translateApiError(err, t, t('services.toast.reconcile_failed', { host: publicHostOf(service) })));
    },
    onSettled: (_data, _err, service) => endAction(service.id),
  });

  const bulkAction = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: 'enable' | 'disable' | 'delete' }) =>
      api.post<BulkActionResult>('/services/bulk', { ids, action }),
    onMutate: ({ ids }) => setActioningIds((prev) => new Set([...prev, ...ids])),
    onSuccess: (result, { action, ids }) => {
      invalidateAfterPush();
      forgetDrift(ids);
      clearSelection();
      const affected = Number(result?.affected ?? 0);
      const label =
        action === 'enable'
          ? t('services.bulk.result.enabled', { count: affected })
          : action === 'disable'
            ? t('services.bulk.result.disabled', { count: affected })
            : t('services.bulk.result.deleted', { count: affected });
      const errors = Array.isArray(result?.errors) ? result.errors : [];
      if (errors.length > 0) {
        const shown = errors.slice(0, 2).join(' · ');
        const more = errors.length > 2 ? t('services.toast.more', { count: errors.length - 2 }) : '';
        toast(t('services.bulk.result.with_errors', { label, errors: shown, more }), { icon: '⚠️', duration: 8000 });
      } else {
        toast.success(label);
      }
    },
    onError: (err) => {
      toast.error(translateApiError(err, t, t('services.bulk.failed')));
    },
    onSettled: (_data, _err, { ids }) =>
      setActioningIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      }),
  });

  // Bulk "check" has no server endpoint — checks run one by one so the backend is not flooded.
  const [bulkChecking, setBulkChecking] = useState(false);
  const runBulkCheck = useCallback(
    async (targets: Service[]) => {
      setBulkChecking(true);
      let ok = 0;
      let failed = 0;
      for (const service of targets) {
        startAction(service.id);
        try {
          const result = await api.post<ServiceCheckResult>(`/services/${service.id}/check`);
          setCheckById((prev) => ({ ...prev, [service.id]: result }));
          if (result.status === 'ok') ok += 1;
          else failed += 1;
        } catch {
          failed += 1;
        } finally {
          endAction(service.id);
        }
      }
      setBulkChecking(false);
      invalidateServices();
      clearSelection();
      if (failed === 0) toast.success(t('services.bulk.result.checked', { count: ok }));
      else toast(
          t('services.bulk.result.checked_mixed', {
            ok: t('services.bulk.result.reachable', { count: ok }),
            failed: t('services.bulk.result.unreachable', { count: failed }),
          }),
          { icon: '⚠️', duration: 6000 },
        );
    },
    [startAction, endAction, invalidateServices, clearSelection, t],
  );

  // --- confirmed actions --------------------------------------------------
  const handleToggleStatus = useCallback(
    async (service: Service) => {
      const host = publicHostOf(service);
      const enabling = !service.enabled;
      const ok = await confirm({
        title: enabling ? t('services.confirm.enable_title') : t('services.confirm.disable_title'),
        message: enabling ? t('services.confirm.enable_body', { host }) : t('services.confirm.disable_body', { host }),
        confirmLabel: enabling ? t('services.action.enable') : t('services.action.disable'),
        variant: enabling ? 'info' : 'warning',
      });
      if (ok) toggleStatus.mutate(service);
    },
    [confirm, t, toggleStatus],
  );

  const handleDelete = useCallback(
    async (service: Service) => {
      const host = publicHostOf(service);
      const ok = await confirm({
        title: t('services.confirm.delete_title'),
        message: t('services.confirm.delete_body', { host }),
        confirmLabel: t('common.delete'),
        variant: 'danger',
      });
      if (ok) deleteService.mutate(service);
    },
    [confirm, t, deleteService],
  );

  const selectedServices = useMemo(() => services.filter((s) => selectedIds.has(s.id)), [services, selectedIds]);

  const handleBulk = useCallback(
    async (action: 'enable' | 'disable' | 'delete' | 'check') => {
      const count = selectedServices.length;
      if (count === 0) return;
      const ids = selectedServices.map((s) => s.id);
      if (action === 'check') {
        const ok = await confirm({
          title: t('services.confirm.bulk_check_title', { count }),
          message: t('services.confirm.bulk_check_body', { count }),
          confirmLabel: t('services.action.check'),
          variant: 'info',
        });
        if (ok) void runBulkCheck(selectedServices);
        return;
      }
      if (action === 'delete') {
        const ok = await confirm({
          title: t('services.confirm.bulk_delete_title', { count }),
          message: t('services.confirm.bulk_delete_body', { count }),
          confirmLabel: t('common.delete'),
          variant: 'danger',
          requireText: count > 3 ? String(count) : undefined,
        });
        if (ok) bulkAction.mutate({ ids, action });
        return;
      }
      const ok = await confirm({
        title: action === 'enable' ? t('services.confirm.bulk_enable_title', { count }) : t('services.confirm.bulk_disable_title', { count }),
        message: action === 'enable' ? t('services.confirm.bulk_enable_body', { count }) : t('services.confirm.bulk_disable_body', { count }),
        confirmLabel: action === 'enable' ? t('services.action.enable') : t('services.action.disable'),
        variant: action === 'enable' ? 'info' : 'warning',
      });
      if (ok) bulkAction.mutate({ ids, action });
    },
    [selectedServices, confirm, t, runBulkCheck, bulkAction],
  );

  // --- drift drawer -------------------------------------------------------
  const driftService = useMemo(
    () => (driftDrawerId === null ? null : services.find((s) => s.id === driftDrawerId) ?? null),
    [driftDrawerId, services],
  );

  const openDrift = useCallback(
    (service: Service) => {
      setDriftDrawerId(service.id);
      if (!driftByService[service.id]) checkDrift.mutate(service);
    },
    [driftByService, checkDrift],
  );

  const handleReconcile = useCallback(async () => {
    if (!driftService) return;
    const host = publicHostOf(driftService);
    const ok = await confirm({
      title: t('services.drift.reconcile_confirm_title'),
      message: t('services.drift.reconcile_confirm_body', { host }),
      confirmLabel: t('services.drift.reconcile'),
      variant: 'warning',
    });
    if (ok) reconcileService.mutate(driftService);
  }, [driftService, confirm, t, reconcileService]);

  // --- taxonomy click-through ---------------------------------------------
  const onTagClick = useCallback((tag: Tag) => setParam('tag', tagFilter === tag.id ? null : String(tag.id)), [setParam, tagFilter]);
  const onEnvClick = useCallback((env: Environment) => setParam('env', envFilter === env.id ? null : String(env.id)), [setParam, envFilter]);

  // --- render helpers -----------------------------------------------------
  const isBusy = (id: number) => actioningIds.has(id);
  const itemProps = (service: Service) => ({
    service,
    providers,
    selected: selectedIds.has(service.id),
    onToggleSelect: toggleSelect,
    busy: isBusy(service.id),
    checkResult: checkById[service.id],
    drift: driftByService[service.id],
    onToggleStatus: handleToggleStatus,
    onEdit: openEdit,
    onDelete: handleDelete,
    onCheck: (s: Service) => checkService.mutate(s),
    onDrift: openDrift,
    onTagClick,
    onEnvClick,
    activeTagId: tagFilter,
    activeEnvId: envFilter,
  });

  const modeLabel = (mode: ModeFilter) => t(`services.tab.${mode}`);
  const total = services.length;
  const shown = filteredServices.length;
  const bulkBusy = bulkAction.isPending || bulkChecking;

  let listBody: ReactElement;
  if (servicesQuery.isPending) {
    listBody =
      viewMode === 'grid' ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label={t('common.loading')}>
          {Array.from({ length: 6 }, (_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : (
        <div className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-card" aria-busy="true" aria-label={t('common.loading')}>
          {Array.from({ length: 6 }, (_, i) => (
            <SkeletonRow key={i} columns={5} />
          ))}
        </div>
      );
  } else if (servicesQuery.isError) {
    listBody = (
      <EmptyState
        icon={<CircleAlert />}
        title={t('services.error_title')}
        description={translateApiError(servicesQuery.error, t, t('services.error_body'))}
        action={
          <Button variant="primary" leftIcon={<RefreshCw />} loading={servicesQuery.isFetching} onClick={() => servicesQuery.refetch()}>
            {t('ui.error.retry')}
          </Button>
        }
      />
    );
  } else if (total === 0) {
    listBody = (
      <EmptyState
        icon={<Globe />}
        title={t('services.empty_title')}
        description={t('services.empty_body')}
        action={
          <Button variant="primary" leftIcon={<Plus />} onClick={() => openCreate()}>
            {t('services.new')}
          </Button>
        }
        actions={
          <Link to="/settings?tab=data" className={buttonVariants({ variant: 'outline' })}>
            {t('services.import')}
          </Link>
        }
      />
    );
  } else if (shown === 0) {
    listBody = (
      <EmptyState
        icon={<FilterX />}
        title={t('services.no_match_title')}
        description={t('services.no_match_body')}
        action={
          <Button variant="outline" leftIcon={<FilterX />} onClick={clearFilters}>
            {t('services.clear_filters')}
          </Button>
        }
      />
    );
  } else if (viewMode === 'grid') {
    listBody = (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 animate-in fade-in animate-duration-200">
        {filteredServices.map((service) => (
          <ServiceCard key={service.id} {...itemProps(service)} />
        ))}
      </div>
    );
  } else {
    listBody = (
      <div className="overflow-x-auto rounded-2xl border border-border bg-card shadow-card animate-in fade-in animate-duration-200">
        <table className="w-full min-w-4xl text-sm">
          <thead className="border-b border-border bg-muted/40 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <tr>
              <th scope="col" className="w-10 px-3 py-2.5">
                <Checkbox
                  checked={allVisibleSelected}
                  indeterminate={!allVisibleSelected && someVisibleSelected}
                  onChange={toggleSelectAll}
                  aria-label={t('services.select_all')}
                />
              </th>
              <th scope="col" className="w-12 px-1 py-2.5">
                <span className="sr-only">{t('services.column.enabled')}</span>
              </th>
              <th scope="col" className="px-3 py-2.5">
                {t('services.column.endpoint')}
              </th>
              <th scope="col" className="px-3 py-2.5">
                {t('services.column.target')}
              </th>
              <th scope="col" className="px-3 py-2.5">
                {t('services.column.providers')}
              </th>
              <th scope="col" className="px-3 py-2.5">
                {t('services.column.status')}
              </th>
              <th scope="col" className="px-3 py-2.5 text-right">
                <span className="sr-only">{t('services.column.actions')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {filteredServices.map((service) => (
              <ServiceRow key={service.id} {...itemProps(service)} />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader
        icon={<Globe />}
        title={t('services.title')}
        description={t('services.description')}
        meta={
          !servicesQuery.isPending && !servicesQuery.isError ? (
            <span className="text-xs text-muted-foreground">{t('services.meta', { count: shown, total })}</span>
          ) : undefined
        }
        actions={
          <>
            <Link to="/settings?tab=data" className={buttonVariants({ variant: 'outline' })}>
              <span className="inline-flex shrink-0 [&>svg]:h-4 [&>svg]:w-4">
                <Import aria-hidden="true" />
              </span>
              {t('services.import')}
            </Link>
            <Button variant="primary" leftIcon={<Plus />} onClick={() => openCreate()}>
              {t('services.new')}
              <Kbd size="sm" className="ml-1 hidden sm:inline-flex border-primary-foreground/30 bg-primary-foreground/15 text-primary-foreground">
                n
              </Kbd>
            </Button>
          </>
        }
      />

      <Tabs
        value={modeFilter}
        onValueChange={(value) => {
          if (isModeFilter(value)) setModeFilter(value);
        }}
        variant="pill"
        className="space-y-4"
      >
        <div className="space-y-4 rounded-2xl border border-border bg-card p-4 shadow-card">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative min-w-56 flex-1">
              <SearchInput
                ref={searchRef}
                value={search}
                onChange={setSearch}
                placeholder={t('services.search_placeholder')}
                aria-label={t('services.search_label')}
                wrapperClassName="w-full"
              />
              {!search && (
                <Kbd size="sm" className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 sm:inline-flex">
                  /
                </Kbd>
              )}
            </div>
            <Select
              aria-label={t('services.filter.tag')}
              value={tagFilter ? String(tagFilter) : ''}
              onChange={(e) => setParam('tag', e.target.value || null)}
              wrapperClassName="w-40"
            >
              <option value="">{t('services.filter.all_tags')}</option>
              {tags.map((tag) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name}
                </option>
              ))}
            </Select>
            <Select
              aria-label={t('services.filter.environment')}
              value={envFilter ? String(envFilter) : ''}
              onChange={(e) => setParam('env', e.target.value || null)}
              wrapperClassName="w-40"
            >
              <option value="">{t('services.filter.all_environments')}</option>
              {environments.map((env) => (
                <option key={env.id} value={env.id}>
                  {env.name}
                </option>
              ))}
            </Select>
            <Select
              aria-label={t('services.filter.status')}
              value={statusFilter ?? ''}
              onChange={(e) => setParam('status', e.target.value || null)}
              wrapperClassName="w-36"
            >
              <option value="">{t('services.filter.all_statuses')}</option>
              <option value="ok">{t('services.status.ok')}</option>
              <option value="error">{t('services.status.error')}</option>
            </Select>
            {hasFilters && (
              <Button variant="ghost" size="sm" leftIcon={<FilterX />} onClick={clearFilters}>
                {t('services.clear_filters')}
              </Button>
            )}
            <div className="ml-auto inline-flex items-center gap-1 rounded-xl bg-muted p-1" role="group" aria-label={t('services.view.label')}>
              <IconButton
                label={t('services.view.list')}
                icon={<LayoutList />}
                tooltip
                size="sm"
                variant={viewMode === 'list' ? 'secondary' : 'ghost'}
                aria-pressed={viewMode === 'list'}
                className="h-8 w-8"
                onClick={() => setViewMode('list')}
              />
              <IconButton
                label={t('services.view.grid')}
                icon={<LayoutGrid />}
                tooltip
                size="sm"
                variant={viewMode === 'grid' ? 'secondary' : 'ghost'}
                aria-pressed={viewMode === 'grid'}
                className="h-8 w-8"
                onClick={() => setViewMode('grid')}
              />
            </div>
          </div>
          <TabList aria-label={t('services.tab.label')}>
            {MODE_FILTERS.map((mode) => (
              <Tab key={mode} value={mode} icon={MODE_ICONS[mode]} count={modeCounts[mode]}>
                {modeLabel(mode)}
              </Tab>
            ))}
          </TabList>
        </div>

        <TabPanel value={modeFilter} className="space-y-4">
          {selectedIds.size > 0 && (
            <div
              role="toolbar"
              aria-label={t('services.bulk.label')}
              className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-2xl border border-primary/30 bg-card/95 px-4 py-2.5 shadow-elevated backdrop-blur-sm animate-in fade-in animate-duration-200"
            >
              <Badge tone="primary" size="md">
                {t('services.bulk.selected', { count: selectedIds.size })}
              </Badge>
              <div className="flex flex-wrap items-center gap-1.5">
                <Button variant="outline" size="sm" leftIcon={<Power />} disabled={bulkBusy} onClick={() => handleBulk('enable')}>
                  {t('services.action.enable')}
                </Button>
                <Button variant="outline" size="sm" leftIcon={<PowerOff />} disabled={bulkBusy} onClick={() => handleBulk('disable')}>
                  {t('services.action.disable')}
                </Button>
                <Button variant="outline" size="sm" leftIcon={<Activity />} loading={bulkChecking} disabled={bulkBusy} onClick={() => handleBulk('check')}>
                  {t('services.action.check')}
                </Button>
                <Button variant="danger" size="sm" leftIcon={<Trash2 />} loading={bulkAction.isPending} disabled={bulkBusy} onClick={() => handleBulk('delete')}>
                  {t('common.delete')}
                </Button>
              </div>
              <IconButton label={t('services.bulk.clear')} icon={<X />} size="sm" className="ml-auto h-8 w-8" onClick={clearSelection} />
            </div>
          )}
          <div className={cn(bulkBusy && 'pointer-events-none opacity-70 transition-opacity')}>{listBody}</div>
        </TabPanel>
      </Tabs>

      {routeModal && (
        <ExposeModal
          key={routeModal.key}
          isOpen
          onClose={closeRouteModal}
          mode={routeModal.mode}
          service={routeModal.service ?? null}
          initialState={routeModal.initialState ?? null}
          templateName={routeModal.templateName ?? null}
        />
      )}

      <DriftDrawer
        open={driftDrawerId !== null}
        onClose={() => setDriftDrawerId(null)}
        service={driftService}
        drift={driftDrawerId !== null ? driftByService[driftDrawerId] : undefined}
        isChecking={checkDrift.isPending && checkDrift.variables?.id === driftDrawerId}
        checkError={driftDrawerId !== null ? driftErrorById[driftDrawerId] ?? null : null}
        onRecheck={() => {
          if (driftService) checkDrift.mutate(driftService);
        }}
        isReconciling={reconcileService.isPending && reconcileService.variables?.id === driftDrawerId}
        onReconcile={handleReconcile}
        reconcileResult={driftDrawerId !== null ? reconcileByService[driftDrawerId] : undefined}
      />

      {ConfirmDialogElement}
    </div>
  );
}
