/**
 * Templates — the presets a service is created from.
 *
 * A template holds everything the service form repeats (forward scheme, port, providers,
 * domain, both halves of the label control) and nothing that is unique to one service
 * (subdomain, target IP). "Use template" hands the id to the Services page through
 * `?template=<id>`, which fetches `GET /api/templates/{id}/apply` and opens a pre-filled
 * create form.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { LayoutTemplate, Plus, RefreshCw, Rocket } from 'lucide-react';
import { api } from '@/api/client';
import { TemplateCard } from '@/components/features/templates/TemplateCard';
import { TemplateModal } from '@/components/features/templates/TemplateModal';
import {
  duplicateName,
  searchHaystack,
  labelFacets,
  toTemplateForm,
  toTemplateIn,
} from '@/components/features/templates/types';
import {
  Button,
  Chip,
  ChipGroup,
  EmptyState,
  InlineAlert,
  Kbd,
  PageHeader,
  SearchInput,
  SkeletonCard,
  useConfirmDialog,
} from '@/components/ui';
import { labelDotClass, labelDotStyle, labelTone } from '@/lib/labels';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { translateApiError } from '@/lib/errors';
import type { Environment, OkResponse, Provider, Tag, Template } from '@/types/api';

type RouteModal = { mode: 'create' } | { mode: 'edit'; id: number };

const isEditable = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
};

export function Templates() {
  const t = useT();
  const { formatNumber } = useFormat();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchRef = useRef<HTMLInputElement>(null);

  const [search, setSearch] = useState('');
  const [activeTagIds, setActiveTagIds] = useState<number[]>([]);
  const [activeEnvironmentIds, setActiveEnvironmentIds] = useState<number[]>([]);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [duplicatingId, setDuplicatingId] = useState<number | null>(null);

  // --- modal: the URL is the state ----------------------------------------
  // `?new=1` and `?edit=<id>` are read, never copied into a second copy of the truth, so a
  // pasted link opens the same dialog a button does. Both are written with `replace`, which
  // keeps opening and closing a form out of the history stack.
  const routeModal = useMemo<RouteModal | null>(() => {
    if (searchParams.get('new')) return { mode: 'create' };
    const editParam = searchParams.get('edit');
    if (!editParam) return null;
    const id = Number(editParam);
    return Number.isInteger(id) ? { mode: 'edit', id } : null;
  }, [searchParams]);

  const setModal = useCallback(
    (target: RouteModal | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('new');
          next.delete('edit');
          if (target?.mode === 'create') next.set('new', '1');
          if (target?.mode === 'edit') next.set('edit', String(target.id));
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const openCreate = useCallback(() => setModal({ mode: 'create' }), [setModal]);
  const openEdit = useCallback((id: number) => setModal({ mode: 'edit', id }), [setModal]);
  const closeModal = useCallback(() => setModal(null), [setModal]);

  // --- data ---------------------------------------------------------------
  const templatesQuery = useQuery<Template[]>({
    queryKey: ['templates'],
    queryFn: () => api.get<Template[]>('/templates'),
  });
  // The three reads beside the templates, all of them destructured as `data = []` until
  // now. Nothing on this page is a template on its own: the chips, the filter row and the
  // provider line under every card are these three resolved against ids the template
  // stores, and an empty map answered every one of those lookups with a miss. The filter
  // row vanished without a word, the chips came off cards that carry labels, and the
  // provider line went further than silence -- it said "integration deleted", in warning
  // colour, about integrations that were never touched.
  const providersQuery = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const tagsQuery = useQuery<Tag[]>({
    queryKey: ['tags'],
    queryFn: () => api.get<Tag[]>('/tags'),
  });
  const environmentsQuery = useQuery<Environment[]>({
    queryKey: ['environments'],
    queryFn: () => api.get<Environment[]>('/environments'),
  });

  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);
  const providers = useMemo(() => providersQuery.data ?? [], [providersQuery.data]);
  const tags = useMemo(() => tagsQuery.data ?? [], [tagsQuery.data]);
  const environments = useMemo(() => environmentsQuery.data ?? [], [environmentsQuery.data]);
  const providersById = useMemo(() => new Map(providers.map((p) => [p.id, p])), [providers]);
  const tagsById = useMemo(() => new Map(tags.map((tag) => [tag.id, tag])), [tags]);
  const environmentsById = useMemo(() => new Map(environments.map((env) => [env.id, env])), [environments]);

  // Pending counts as unread: these three start at the same moment `/templates` does, and
  // the cards are painted the instant it answers, which is not the instant they do.
  const providersUnread = providersQuery.isPending || providersQuery.isError;
  const contextQueries = [providersQuery, tagsQuery, environmentsQuery];
  const contextFailed = contextQueries.some((query) => query.isError);
  const contextError = contextQueries.find((query) => query.isError)?.error;
  // Only the failed ones count as refreshing. `loading` disables the button it is on,
  // so reading `isFetching` off all three would have let a sibling that never answers
  // hold the retry of the one that did fail shut.
  const contextRefreshing = contextQueries.some((query) => query.isError && query.isFetching);
  const retryContext = () => {
    for (const query of contextQueries) {
      if (query.isError) void query.refetch();
    }
  };

  // --- filtering ----------------------------------------------------------
  // Both halves of the label control filter, and they filter separately: a chip row per
  // half, an active list per half. Two labels of different kinds may share a name (the
  // server only refuses a duplicate within one kind), so one shared list would filter on
  // whichever happened to be clicked.
  const tagFacets = useMemo(() => labelFacets(templates, 'tag_ids', tagsById), [templates, tagsById]);
  const environmentFacets = useMemo(
    () => labelFacets(templates, 'environment_ids', environmentsById),
    [templates, environmentsById],
  );

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const namesOf = (
      template: Template,
      key: 'tag_ids' | 'environment_ids',
      byId: ReadonlyMap<number, Tag | Environment>,
    ) => (template[key] ?? []).map((id) => byId.get(id)?.name ?? '');
    return templates.filter((template) => {
      if (activeTagIds.length > 0 && !activeTagIds.every((id) => (template.tag_ids ?? []).includes(id))) {
        return false;
      }
      if (
        activeEnvironmentIds.length > 0 &&
        !activeEnvironmentIds.every((id) => (template.environment_ids ?? []).includes(id))
      ) {
        return false;
      }
      if (!needle) return true;
      return searchHaystack(template, [
        ...namesOf(template, 'tag_ids', tagsById),
        ...namesOf(template, 'environment_ids', environmentsById),
      ]).includes(needle);
    });
  }, [templates, search, activeTagIds, activeEnvironmentIds, tagsById, environmentsById]);

  const toggleTag = useCallback((tagId: number) => {
    setActiveTagIds((prev) => (prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId]));
  }, []);

  const toggleEnvironment = useCallback((environmentId: number) => {
    setActiveEnvironmentIds((prev) =>
      prev.includes(environmentId) ? prev.filter((id) => id !== environmentId) : [...prev, environmentId],
    );
  }, []);

  const isFiltering = search.trim().length > 0 || activeTagIds.length > 0 || activeEnvironmentIds.length > 0;
  const clearFilters = useCallback(() => {
    setSearch('');
    setActiveTagIds([]);
    setActiveEnvironmentIds([]);
  }, []);

  // --- mutations ----------------------------------------------------------
  const removeTemplate = useMutation<OkResponse, unknown, Template>({
    mutationFn: (template) => api.delete<OkResponse>(`/templates/${template.id}`),
    onSuccess: (_data, template) => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      toast.success(t('templates.toast.deleted', { name: template.name }));
    },
    onError: (err) => toast.error(translateApiError(err, t, t('templates.toast.delete_failed'))),
    onSettled: () => setDeletingId(null),
  });

  const duplicateTemplate = useMutation<Template, unknown, Template>({
    mutationFn: (template) => {
      const body = toTemplateIn(toTemplateForm(template));
      body.name = duplicateName(
        template.name,
        templates.map((existing) => existing.name),
        t('templates.duplicate_suffix'),
      );
      return api.post<Template>('/templates', body);
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      toast.success(t('templates.toast.duplicated', { name: created?.name ?? '' }));
    },
    onError: (err) => toast.error(translateApiError(err, t, t('templates.toast.duplicate_failed'))),
    onSettled: () => setDuplicatingId(null),
  });

  const handleDelete = useCallback(
    async (template: Template) => {
      const ok = await confirm({
        title: t('templates.delete.title'),
        message: t('templates.delete.message', { name: template.name }),
        confirmLabel: t('templates.delete.confirm'),
        variant: 'danger',
      });
      if (!ok) return;
      setDeletingId(template.id);
      removeTemplate.mutate(template);
    },
    [confirm, removeTemplate, t],
  );

  const handleDuplicate = useCallback(
    (template: Template) => {
      setDuplicatingId(template.id);
      duplicateTemplate.mutate(template);
    },
    [duplicateTemplate],
  );

  // A `?edit=<id>` that matches nothing (a deleted template, a stale link) simply opens no
  // dialog — and, because the shortcuts below watch `modalOpen`, it does not freeze the page
  // either. `editingTemplate` stays null while the list loads, so a deep link waits for it.
  const editingTemplate =
    routeModal?.mode === 'edit' ? (templates.find((tpl) => tpl.id === routeModal.id) ?? null) : null;
  const modalOpen = routeModal !== null && (routeModal.mode === 'create' || editingTemplate !== null);

  // --- keyboard: `/` focuses the search, `n` starts a template ------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (modalOpen) return;
      const editing = isEditable(e.target);
      if (e.key === '/' && !editing) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (e.key === 'Escape' && document.activeElement === searchRef.current) {
        searchRef.current?.blur();
        return;
      }
      if (e.key.toLowerCase() === 'n' && !editing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        openCreate();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modalOpen, openCreate]);

  // --- render -------------------------------------------------------------
  const isLoading = templatesQuery.isLoading;
  const loadFailed = templatesQuery.isError;
  const total = templates.length;
  const countLabel = t('templates.count', { count: total });

  return (
    <div className="space-y-6 pb-8 animate-in fade-in duration-200">
      <PageHeader
        icon={<LayoutTemplate />}
        eyebrow={t('templates.eyebrow')}
        title={t('templates.title')}
        description={t('templates.description')}
        meta={
          !isLoading && !loadFailed && total > 0 ? (
            <>
              <span className="tabular-nums">{countLabel}</span>
              {isFiltering && (
                <span className="tabular-nums">{t('templates.count_filtered', { count: visible.length, total: formatNumber(total) })}</span>
              )}
            </>
          ) : undefined
        }
        actions={
          <Button variant="primary" leftIcon={<Plus />} onClick={openCreate}>
            {t('templates.new')}
          </Button>
        }
      />

      {loadFailed && (
        <InlineAlert
          tone="danger"
          title={t('templates.error_title')}
          action={
            <Button
              size="sm"
              variant="outline"
              leftIcon={<RefreshCw />}
              loading={templatesQuery.isFetching}
              onClick={() => void templatesQuery.refetch()}
            >
              {t('templates.retry')}
            </Button>
          }
        >
          {translateApiError(templatesQuery.error, t, t('templates.error_description'))}
        </InlineAlert>
      )}

      {contextFailed && (
        <InlineAlert
          tone="warning"
          title={t('templates.context_failed')}
          action={
            <Button
              size="sm"
              variant="outline"
              leftIcon={<RefreshCw />}
              loading={contextRefreshing}
              onClick={retryContext}
            >
              {t('templates.retry')}
            </Button>
          }
        >
          {translateApiError(contextError, t, t('templates.context_failed_hint'))}
        </InlineAlert>
      )}

      {isLoading && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true">
          {Array.from({ length: 6 }, (_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {!isLoading && !loadFailed && total === 0 && (
        <EmptyState
          icon={<LayoutTemplate />}
          title={t('templates.empty.title')}
          description={t('templates.empty.description')}
          action={
            <Button variant="primary" leftIcon={<Plus />} onClick={openCreate}>
              {t('templates.empty.cta')}
            </Button>
          }
          actions={
            <Button variant="ghost" leftIcon={<Rocket />} onClick={() => navigate('/services')}>
              {t('templates.empty.services')}
            </Button>
          }
        />
      )}

      {!isLoading && !loadFailed && total > 0 && (
        <>
          <div className="space-y-3 rounded-2xl border border-border bg-card p-4 shadow-card">
            <div className="relative">
              <SearchInput
                ref={searchRef}
                value={search}
                onChange={setSearch}
                placeholder={t('templates.search_placeholder')}
                aria-label={t('templates.search_label')}
                wrapperClassName="w-full"
              />
              {!search && (
                <Kbd
                  size="sm"
                  className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 sm:inline-flex"
                >
                  /
                </Kbd>
              )}
            </div>

            {(tagFacets.length > 0 || environmentFacets.length > 0) && (
              <div className="flex flex-wrap items-center gap-2">
                {tagFacets.length > 0 && (
                  <ChipGroup label={t('templates.filter_tags')}>
                    {tagFacets.map((facet) => (
                      <Chip
                        key={facet.id}
                        size="sm"
                        tone={labelTone('tag')}
                        icon={<span className={labelDotClass('tag')} style={labelDotStyle(facet.label.color)} />}
                        selected={activeTagIds.includes(facet.id)}
                        count={facet.count}
                        onClick={() => toggleTag(facet.id)}
                      >
                        {facet.label.name}
                      </Chip>
                    ))}
                  </ChipGroup>
                )}
                {environmentFacets.length > 0 && (
                  <ChipGroup label={t('templates.filter_environments')}>
                    {environmentFacets.map((facet) => (
                      <Chip
                        key={facet.id}
                        size="sm"
                        tone={labelTone('environment')}
                        icon={
                          <span className={labelDotClass('environment')} style={labelDotStyle(facet.label.color)} />
                        }
                        selected={activeEnvironmentIds.includes(facet.id)}
                        count={facet.count}
                        onClick={() => toggleEnvironment(facet.id)}
                      >
                        {facet.label.name}
                      </Chip>
                    ))}
                  </ChipGroup>
                )}
                {isFiltering && (
                  <Button size="sm" variant="ghost" onClick={clearFilters}>
                    {t('templates.clear_filters')}
                  </Button>
                )}
              </div>
            )}
          </div>

          {visible.length === 0 ? (
            <EmptyState
              compact
              icon={<LayoutTemplate />}
              title={t('templates.no_results.title')}
              description={t('templates.no_results.description')}
              action={
                <Button size="sm" variant="outline" onClick={clearFilters}>
                  {t('templates.clear_filters')}
                </Button>
              }
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {visible.map((template) => (
                <TemplateCard
                  key={template.id}
                  template={template}
                  providersById={providersById}
                  providersUnread={providersUnread}
                  tagsById={tagsById}
                  environmentsById={environmentsById}
                  activeTagIds={activeTagIds}
                  onToggleTag={toggleTag}
                  activeEnvironmentIds={activeEnvironmentIds}
                  onToggleEnvironment={toggleEnvironment}
                  onUse={() => navigate(`/services?template=${encodeURIComponent(String(template.id))}`)}
                  onEdit={() => openEdit(template.id)}
                  onDuplicate={() => handleDuplicate(template)}
                  onDelete={() => void handleDelete(template)}
                  duplicating={duplicatingId === template.id}
                  deleting={deletingId === template.id}
                />
              ))}
            </div>
          )}
        </>
      )}

      <TemplateModal open={modalOpen} onClose={closeModal} template={editingTemplate} />
      {ConfirmDialogElement}
    </div>
  );
}
