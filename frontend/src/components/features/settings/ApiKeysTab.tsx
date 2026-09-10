import { useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Copy, Eye, EyeOff, Key, Plus, ShieldAlert, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  Checkbox,
  EmptyState,
  Field,
  IconButton,
  InlineAlert,
  Input,
  SearchInput,
  SkeletonRow,
  buttonVariants,
  useConfirmDialog,
  type Tone,
} from '@/components/ui';
import type { ApiKey, ApiKeyCreated, AuthStatus } from '@/types/api';
import { SettingsSection } from './SettingsSection';

const SCOPES = ['read', 'write', 'admin'] as const;
const SCOPE_TONE: Record<string, Tone> = { read: 'info', write: 'warning', admin: 'danger' };
const SEARCH_THRESHOLD = 6;

/** API keys for MCP and scripts; the secret is shown once, right after creation. */
export function ApiKeysTab() {
  const t = useT();
  const queryClient = useQueryClient();
  const { formatDateTime, formatRelative } = useFormat();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const keysQuery = useQuery<ApiKey[]>({
    queryKey: ['api-keys'],
    queryFn: () => api.get<ApiKey[]>('/settings/api-keys'),
  });
  const { data: authStatus } = useQuery<AuthStatus>({
    queryKey: ['auth-me'],
    queryFn: () => api.get<AuthStatus>('/auth/me'),
  });

  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<string[]>(['read']);
  const [touched, setTouched] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [copied, setCopied] = useState(false);
  const [search, setSearch] = useState('');

  const createKey = useMutation({
    mutationFn: (payload: { name: string; scopes: string[] }) => api.post<ApiKeyCreated>('/settings/api-keys', payload),
    onSuccess: (data) => {
      setCreatedKey(data.key);
      setReveal(false);
      setCopied(false);
      setName('');
      setScopes(['read']);
      setTouched(false);
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success(t('settings.api_keys.created'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.api_keys.create_failed'))),
  });

  const revokeKey = useMutation({
    mutationFn: (id: number) => api.delete(`/settings/api-keys/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      toast.success(t('settings.api_keys.revoked'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.api_keys.revoke_failed'))),
  });

  const keys = useMemo(() => keysQuery.data ?? [], [keysQuery.data]);
  const needle = search.trim().toLowerCase();
  const visible = needle
    ? keys.filter(
        (key) =>
          key.name.toLowerCase().includes(needle) ||
          key.prefix.toLowerCase().includes(needle) ||
          key.scopes.some((scope) => scope.toLowerCase().includes(needle)),
      )
    : keys;

  const nameMissing = touched && name.trim() === '';
  const scopeMissing = touched && scopes.length === 0;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!name.trim() || scopes.length === 0) return;
    createKey.mutate({ name: name.trim(), scopes });
  };

  const toggleScope = (scope: string, checked: boolean) =>
    setScopes((prev) => (checked ? [...new Set([...prev, scope])] : prev.filter((s) => s !== scope)));

  const copyKey = async () => {
    if (!createdKey) return;
    try {
      await navigator.clipboard.writeText(createdKey);
      setCopied(true);
      toast.success(t('settings.api_keys.copied'));
    } catch {
      toast.error(t('settings.api_keys.copy_failed'));
    }
  };

  const requestRevoke = async (key: ApiKey) => {
    const ok = await confirm({
      title: t('settings.api_keys.revoke_title'),
      message: t('settings.api_keys.revoke_message', { name: key.name }),
      confirmLabel: t('settings.api_keys.revoke'),
      variant: 'danger',
    });
    if (ok) revokeKey.mutate(key.id);
  };

  const masked = createdKey ? `${createdKey.slice(0, 10)}${'•'.repeat(30)}` : '';

  return (
    <div className="space-y-6">
      {authStatus && !authStatus.auth_required && (
        <InlineAlert
          tone="warning"
          icon={<ShieldAlert />}
          title={t('settings.security.open_access_title')}
          action={
            <Link to="/settings?tab=security" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
              {t('settings.auth.set_password')}
            </Link>
          }
        >
          {t('settings.api_keys.open_access_hint')}
        </InlineAlert>
      )}

      <form onSubmit={submit}>
        <SettingsSection
          icon={<Key />}
          title={t('settings.api_keys.create_title')}
          description={t('settings.api_keys.desc')}
          footer={
            <Button type="submit" leftIcon={<Plus />} loading={createKey.isPending}>
              {t('settings.api_keys.create')}
            </Button>
          }
        >
          <div className="grid gap-4 md:grid-cols-2">
            <Field
              label={t('settings.api_keys.name_label')}
              required
              error={nameMissing ? t('settings.api_keys.name_required') : undefined}
            >
              <Input
                value={name}
                placeholder={t('settings.api_keys.name_placeholder')}
                autoComplete="off"
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <fieldset className="space-y-2">
              <legend className="text-xs font-semibold leading-tight text-foreground">{t('settings.api_keys.scopes_label')}</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {SCOPES.map((scope) => (
                  <Checkbox
                    key={scope}
                    checked={scopes.includes(scope)}
                    onChange={(e) => toggleScope(scope, e.target.checked)}
                    label={t(`settings.api_keys.scope_${scope}`)}
                    description={t(`settings.api_keys.scope_${scope}_desc`)}
                  />
                ))}
              </div>
              {scopeMissing && (
                <p role="alert" className="text-xs font-medium text-destructive">
                  {t('settings.api_keys.scope_required')}
                </p>
              )}
            </fieldset>
          </div>

          {createdKey && (
            <InlineAlert tone="success" title={t('settings.api_keys.created_once')} onDismiss={() => setCreatedKey(null)}>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                <code className="min-w-0 flex-1 select-all break-all rounded-lg border border-border bg-card px-3 py-2 font-mono text-xs text-foreground">
                  {reveal ? createdKey : masked}
                </code>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    leftIcon={reveal ? <EyeOff /> : <Eye />}
                    onClick={() => setReveal((v) => !v)}
                  >
                    {reveal ? t('settings.api_keys.hide') : t('settings.api_keys.reveal')}
                  </Button>
                  <Button variant="secondary" size="sm" leftIcon={<Copy />} onClick={() => void copyKey()}>
                    {copied ? t('settings.api_keys.copied_short') : t('settings.api_keys.copy')}
                  </Button>
                </div>
              </div>
            </InlineAlert>
          )}
        </SettingsSection>
      </form>

      <SettingsSection
        icon={<Key />}
        title={t('settings.api_keys.title')}
        actions={keys.length > 0 ? <Badge tone="neutral">{t('settings.api_keys.count', { count: keys.length })}</Badge> : undefined}
      >
        {keys.length > SEARCH_THRESHOLD && (
          <SearchInput value={search} onChange={setSearch} placeholder={t('settings.api_keys.search_placeholder')} />
        )}

        {keysQuery.isLoading ? (
          <div className="divide-y divide-border rounded-xl border border-border">
            <SkeletonRow columns={4} />
            <SkeletonRow columns={4} />
            <SkeletonRow columns={4} />
          </div>
        ) : keysQuery.isError ? (
          <InlineAlert
            tone="danger"
            title={t('settings.api_keys.load_failed')}
            action={
              <Button variant="outline" size="sm" onClick={() => keysQuery.refetch()}>
                {t('ui.error.retry')}
              </Button>
            }
          >
            {translateApiError(keysQuery.error, t, t('common.error'))}
          </InlineAlert>
        ) : keys.length === 0 ? (
          <EmptyState compact icon={<Key />} title={t('settings.api_keys.empty')} />
        ) : visible.length === 0 ? (
          <EmptyState compact title={t('settings.api_keys.no_match')} />
        ) : (
          <ul className="divide-y divide-border rounded-xl border border-border">
            {visible.map((key) => (
              <li key={key.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{key.name}</span>
                    <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                      {key.prefix}•••
                    </code>
                    {key.scopes.map((scope) => (
                      <Badge key={scope} size="sm" tone={SCOPE_TONE[scope] ?? 'neutral'}>
                        {scope}
                      </Badge>
                    ))}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                    {t('settings.api_keys.created_at', { date: formatDateTime(key.created_at, 'medium') })}
                    <span aria-hidden="true"> · </span>
                    {key.last_used_at
                      ? t('settings.api_keys.last_used', { date: formatRelative(key.last_used_at) })
                      : t('settings.api_keys.never_used')}
                  </p>
                </div>
                <IconButton
                  label={t('settings.api_keys.revoke_aria', { name: key.name })}
                  icon={<Trash2 />}
                  tooltip
                  disabled={revokeKey.isPending}
                  onClick={() => void requestRevoke(key)}
                  className="self-end text-muted-foreground hover:text-destructive sm:self-auto"
                />
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>
      {ConfirmDialogElement}
    </div>
  );
}
