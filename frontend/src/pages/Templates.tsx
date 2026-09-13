/**
 * Templates — the presets a service is created from.
 *
 * A template holds everything the service form repeats (forward scheme, port, providers,
 * domain, tags) and nothing that is unique to one service (subdomain, target IP). "Use
 * template" hands the id to the Services page through `?template=<id>`, which fetches
 * `GET /api/templates/{id}/apply` and opens a pre-filled create form.
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
  tagTone,
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
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { translateApiError } from '@/lib/errors';
import type { OkResponse, Provider, Tag, Template } from '@/types/api';

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
  const { data: providers = [] } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const { data: tags = [] } = useQuery<Tag[]>({
    queryKey: ['tags'],
    queryFn: () => api.get<Tag[]>('/tags'),
  });

  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);
  const providersById = useMemo(() => new Map(providers.map((p) => [p.id, p])), [providers]);
  const tagsById = useMemo(() => new Map(tags.map((tag) => [tag.id, tag])), [tags]);

  // --- filtering ----------------------------------------------------------
  const tagFacets = useMemo(() => {
    const counts = new Map<number, number>();
    for (const template of templates) {
      for (const id of template.tag_ids ?? []) counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([id, count]) => ({ tag: tagsById.get(id), id, count }))
      .filter((facet) => Boolean(facet.tag))
      .sort((a, b) => (a.tag?.name ?? '').localeCompare(b.tag?.name ?? ''));
  }, [templates, tagsById]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return templates.filter((template) => {
      if (activeTagIds.length > 0 && !activeTagIds.every((id) => (template.tag_ids ?? []).includes(id))) {
        return false;
      }
      if (!needle) return true;
      const tagNames = (template.tag_ids ?? []).map((id) => tagsById.get(id)?.name ?? '');
      return searchHaystack(template, tagNames).includes(needle);
    });
  }, [templates, search, activeTagIds, tagsById]);

  const toggleTag = useCallback((tagId: number) => {
    setActiveTagIds((prev) => (prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId]));
  }, []);

  const isFiltering = search.trim().length > 0 || activeTagIds.length > 0;
  const clearFilters = useCallback(() => {
    setSearch('');
    setActiveTagIds([]);
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

            {tagFacets.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <ChipGroup label={t('templates.filter_tags')}>
                  {tagFacets.map((facet) => (
                    <Chip
                      key={facet.id}
                      size="sm"
                      tone={tagTone(facet.tag?.color)}
                      selected={activeTagIds.includes(facet.id)}
                      count={facet.count}
                      onClick={() => toggleTag(facet.id)}
                    >
                      {facet.tag?.name}
                    </Chip>
                  ))}
                </ChipGroup>
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
                  tagsById={tagsById}
                  activeTagIds={activeTagIds}
                  onToggleTag={toggleTag}
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
