import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Check, Layers, Pencil, Plus, Tag, X } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  EmptyState,
  Field,
  IconButton,
  InlineAlert,
  Input,
  SearchInput,
  Select,
  Skeleton,
  useConfirmDialog,
} from '@/components/ui';
import type { Environment, Tag as TagType } from '@/types/api';
import { SettingsSection } from './SettingsSection';

/** CSS colour names the backend stores as-is; the label of each one is translated. */
const TAG_COLORS = ['blue', 'teal', 'green', 'red', 'orange', 'purple', 'cyan', 'yellow', 'pink', 'lime', 'indigo'] as const;
const SEARCH_THRESHOLD = 6;

type Kind = 'tags' | 'env';

interface KindConfig {
  queryKey: readonly string[];
  endpoint: string;
  prefix: string;
  defaultColor: (typeof TAG_COLORS)[number];
  icon: typeof Tag;
}

const KINDS: Record<Kind, KindConfig> = {
  tags: { queryKey: ['tags'], endpoint: '/tags', prefix: 'settings.tags', defaultColor: 'blue', icon: Tag },
  env: { queryKey: ['environments'], endpoint: '/environments', prefix: 'settings.env', defaultColor: 'green', icon: Layers },
};

type Item = TagType | Environment;

/** Tags and environments: the two labels a service can carry, edited the same way. */
export function TaxonomyTab() {
  return (
    <div className="space-y-6">
      <TaxonomyEditor kind="tags" />
      <TaxonomyEditor kind="env" />
    </div>
  );
}

function swatchStyle(color: string) {
  return {
    backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)`,
    borderColor: `color-mix(in srgb, ${color} 40%, transparent)`,
    color,
  };
}

function TaxonomyEditor({ kind }: { kind: Kind }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const cfg = KINDS[kind];
  const Icon = cfg.icon;

  const [name, setName] = useState('');
  const [color, setColor] = useState<string>(cfg.defaultColor);
  const [search, setSearch] = useState('');
  /** The row currently open in the inline editor, with its unsaved name and colour. */
  const [editing, setEditing] = useState<{ id: number; name: string; color: string } | null>(null);

  const listQuery = useQuery<Item[]>({
    queryKey: [...cfg.queryKey],
    queryFn: () => api.get<Item[]>(cfg.endpoint),
  });
  const items = useMemo(() => listQuery.data ?? [], [listQuery.data]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [...cfg.queryKey] });
    queryClient.invalidateQueries({ queryKey: ['services'] });
  };

  const create = useMutation({
    mutationFn: (payload: { name: string; color: string }) => api.post(cfg.endpoint, payload),
    onSuccess: () => {
      invalidate();
      setName('');
      toast.success(t(`${cfg.prefix}.created`));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t(`${cfg.prefix}.create_failed`))),
  });

  const update = useMutation({
    mutationFn: ({ id, ...payload }: { id: number; name: string; color: string }) =>
      api.put(`${cfg.endpoint}/${id}`, payload),
    onSuccess: () => {
      invalidate();
      setEditing(null);
      toast.success(t(`${cfg.prefix}.updated`));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t(`${cfg.prefix}.update_failed`))),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`${cfg.endpoint}/${id}`),
    onSuccess: () => {
      invalidate();
      toast.success(t(`${cfg.prefix}.deleted`));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.taxonomy.delete_failed'))),
  });

  const candidate = name.trim();
  const duplicate = candidate !== '' && items.some((item) => item.name.toLowerCase() === candidate.toLowerCase());

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!candidate || duplicate) return;
    create.mutate({ name: candidate, color });
  };

  const editCandidate = editing?.name.trim() ?? '';
  const editDuplicate =
    editing !== null &&
    editCandidate !== '' &&
    items.some((item) => item.id !== editing.id && item.name.toLowerCase() === editCandidate.toLowerCase());
  const canSaveEdit = editing !== null && editCandidate !== '' && !editDuplicate && !update.isPending;

  const submitEdit = (e: FormEvent) => {
    e.preventDefault();
    if (!editing || !canSaveEdit) return;
    update.mutate({ id: editing.id, name: editCandidate, color: editing.color });
  };

  const requestDelete = async (item: Item) => {
    const ok = await confirm({
      title: t('settings.taxonomy.delete_title'),
      message: t('settings.taxonomy.delete_message', { name: item.name }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (ok) remove.mutate(item.id);
  };

  const needle = search.trim().toLowerCase();
  const visible = needle ? items.filter((item) => item.name.toLowerCase().includes(needle)) : items;

  return (
    <SettingsSection
      icon={<Icon />}
      title={t(`${cfg.prefix}.title`)}
      description={t(`${cfg.prefix}.desc`)}
      actions={items.length > 0 ? <Badge tone="neutral">{t('settings.taxonomy.count', { count: items.length })}</Badge> : undefined}
    >
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_11rem_auto] sm:items-start">
        <Field label={t('settings.taxonomy.name_label')} error={duplicate ? t('settings.taxonomy.exists') : undefined}>
          <Input
            value={name}
            placeholder={t(`${cfg.prefix}.name_placeholder`)}
            autoComplete="off"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          label={t(`${cfg.prefix}.color_aria`)}
          labelAddon={
            <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full border" style={swatchStyle(color)} />
          }
        >
          <Select value={color} onChange={(e) => setColor(e.target.value)}>
            {TAG_COLORS.map((c) => (
              <option key={c} value={c}>
                {t(`settings.color.${c}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Button
          type="submit"
          leftIcon={<Plus />}
          loading={create.isPending}
          disabled={!candidate || duplicate}
          className="sm:mt-5.5"
        >
          {t('common.add')}
        </Button>
      </form>

      {items.length > SEARCH_THRESHOLD && (
        <SearchInput value={search} onChange={setSearch} placeholder={t('settings.taxonomy.search_placeholder')} />
      )}

      {listQuery.isLoading ? (
        <div className="flex flex-wrap gap-2">
          <Skeleton className="h-7 w-24 rounded-full" />
          <Skeleton className="h-7 w-20 rounded-full" />
          <Skeleton className="h-7 w-28 rounded-full" />
        </div>
      ) : listQuery.isError ? (
        <InlineAlert
          tone="danger"
          title={t('settings.taxonomy.load_failed')}
          action={
            <Button variant="outline" size="sm" onClick={() => listQuery.refetch()}>
              {t('ui.error.retry')}
            </Button>
          }
        >
          {translateApiError(listQuery.error, t, t('common.error'))}
        </InlineAlert>
      ) : items.length === 0 ? (
        <EmptyState compact icon={<Icon />} title={t(`${cfg.prefix}.empty`)} />
      ) : visible.length === 0 ? (
        <EmptyState compact title={t('settings.taxonomy.no_match')} />
      ) : (
        <ul className="flex flex-wrap items-center gap-2" aria-label={t(`${cfg.prefix}.title`)}>
          {visible.map((item) =>
            editing?.id === item.id ? (
              <li key={item.id} className="w-full">
                <form
                  onSubmit={submitEdit}
                  aria-label={t('settings.taxonomy.edit_form_aria', { name: item.name })}
                  className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-muted/40 p-2"
                >
                  <Input
                    size="sm"
                    // eslint-disable-next-line jsx-a11y/no-autofocus -- the inline edit form appears on click; focus follows it
                    autoFocus
                    autoComplete="off"
                    value={editing.name}
                    maxLength={32}
                    invalid={editDuplicate}
                    aria-label={t('settings.taxonomy.name_label')}
                    onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') setEditing(null);
                    }}
                    className="w-full sm:w-48"
                  />
                  <Select
                    size="sm"
                    value={editing.color}
                    aria-label={t(`${cfg.prefix}.color_aria`)}
                    onChange={(e) => setEditing({ ...editing, color: e.target.value })}
                    wrapperClassName="w-full sm:w-40"
                  >
                    {TAG_COLORS.map((c) => (
                      <option key={c} value={c}>
                        {t(`settings.color.${c}`)}
                      </option>
                    ))}
                  </Select>
                  <span
                    aria-hidden="true"
                    className="inline-block h-3 w-3 shrink-0 rounded-full border"
                    style={swatchStyle(editing.color)}
                  />
                  <IconButton
                    type="submit"
                    variant="primary"
                    label={t('common.save')}
                    icon={<Check />}
                    tooltip
                    loading={update.isPending}
                    disabled={!canSaveEdit}
                    className="h-8 w-8"
                  />
                  <IconButton
                    label={t('common.cancel')}
                    icon={<X />}
                    tooltip
                    disabled={update.isPending}
                    onClick={() => setEditing(null)}
                    className="h-8 w-8"
                  />
                  {editDuplicate && (
                    <span role="alert" className="text-xs font-medium text-destructive">
                      {t('settings.taxonomy.exists')}
                    </span>
                  )}
                </form>
              </li>
            ) : (
              <li
                key={item.id}
                className="inline-flex items-center gap-1.5 rounded-full border py-1 pl-2.5 pr-1 text-xs font-medium"
                style={swatchStyle(item.color)}
              >
                <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ backgroundColor: item.color }} />
                <span className="max-w-48 truncate">{item.name}</span>
                <button
                  type="button"
                  aria-label={t(`${cfg.prefix}.edit_aria`, { name: item.name })}
                  disabled={update.isPending || remove.isPending}
                  onClick={() => setEditing({ id: item.id, name: item.name, color: item.color })}
                  className={cn(
                    'inline-flex h-5 w-5 items-center justify-center rounded-full transition-colors hover:bg-foreground/10',
                    'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                  )}
                >
                  <Pencil aria-hidden="true" className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  aria-label={t(`${cfg.prefix}.delete_aria`, { name: item.name })}
                  disabled={remove.isPending}
                  onClick={() => void requestDelete(item)}
                  className={cn(
                    'inline-flex h-5 w-5 items-center justify-center rounded-full transition-colors hover:bg-foreground/10',
                    'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                  )}
                >
                  <X aria-hidden="true" className="h-3 w-3" />
                </button>
              </li>
            ),
          )}
        </ul>
      )}
      {ConfirmDialogElement}
    </SettingsSection>
  );
}
