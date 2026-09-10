import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Bell, ChevronDown, ExternalLink, Plus, Send, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import { useWebhookActions } from '@/hooks/useWebhookActions';
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
  Select,
  Switch,
  useConfirmDialog,
} from '@/components/ui';
import type { Provider, Service, Webhook } from '@/types/api';
import { SettingsSection } from './SettingsSection';

const APPRISE_URL = 'https://github.com/caronc/apprise';
const SEARCH_THRESHOLD = 6;

type ScopeType = Webhook['scope_type'];

type WebhookRuleUpdate = {
  scope_type: ScopeType;
  scope_ref_id: number | null;
  repeat_interval_minutes: number;
  alert_on_any_down: boolean;
  alert_on_any_up: boolean;
  alert_on_integration_down: boolean;
  alert_on_integration_up: boolean;
  min_down_minutes: number;
};

type TestOutcome = { ok: boolean; error?: string };

/** `POST /api/settings/test-webhook`: one line per enabled webhook. */
interface TestAllResult {
  ok: boolean;
  results: { id: number; name: string; ok: boolean; error?: string | null }[];
}

/** Notification webhooks: add, test, scope and alert rules, enable, delete. */
export function WebhooksTab() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  const hook = useWebhookActions(true);
  const { webhooks, name, setName, url, setUrl, testResult, setTestResult } = hook;

  const { data: providers = [] } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });
  const { data: services = [] } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
  });

  const enabledProviders = useMemo(() => providers.filter((p) => !!p.enabled), [providers]);
  const enabledServices = useMemo(() => services.filter((s) => !!s.enabled), [services]);

  const [scope, setScope] = useState<ScopeType>('all');
  const [refId, setRefId] = useState<number | null>(null);
  const [repeat, setRepeat] = useState(0);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [outcomes, setOutcomes] = useState<Record<number, TestOutcome>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testAll, setTestAll] = useState<TestAllResult | null>(null);

  const updateRules = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: WebhookRuleUpdate }) => api.put(`/webhooks/${id}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['webhooks'] }),
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.webhooks.update_failed'))),
  });

  const testAllMutation = useMutation({
    mutationFn: () => api.post<TestAllResult>('/settings/test-webhook'),
    onSuccess: (data) => {
      setTestAll(data);
      if (data.ok) toast.success(t('settings.webhooks.test_sent'));
      else toast.error(t('settings.webhooks.test_all_partial'));
    },
    onError: (err: unknown) => {
      setTestAll(null);
      toast.error(translateApiError(err, t, t('settings.webhooks.test_failed')));
    },
  });

  const firstRefFor = (type: ScopeType): number | null => {
    if (type === 'provider') return enabledProviders[0]?.id ?? null;
    if (type === 'service') return enabledServices[0]?.id ?? null;
    return null;
  };

  /**
   * A scope with nothing to point at is a dead end: `firstRefFor` returns null, the rule is
   * saved with `scope_ref_id: null`, and the webhook matches nothing instead of matching one
   * provider. So the option is disabled and the field says why.
   */
  const noProviders = enabledProviders.length === 0;
  const noServices = enabledServices.length === 0;
  const scopeUnavailable = (type: ScopeType) =>
    (type === 'provider' && noProviders) || (type === 'service' && noServices);
  const scopeHints = [
    noProviders ? t('settings.webhooks.scope_no_providers') : null,
    noServices ? t('settings.webhooks.scope_no_services') : null,
  ].filter((line): line is string => line !== null);
  const scopeHint = scopeHints.length > 0 ? scopeHints.join(' ') : undefined;

  const changeNewScope = (type: ScopeType) => {
    if (scopeUnavailable(type)) return;
    setScope(type);
    setRefId(firstRefFor(type));
  };

  const canAdd = name.trim() !== '' && url.trim() !== '' && (scope === 'all' || refId !== null);

  const submitNew = (e: FormEvent) => {
    e.preventDefault();
    if (!canAdd) return;
    hook.addWebhook.mutate(
      {
        scope_type: scope,
        scope_ref_id: scope === 'all' ? null : refId,
        repeat_interval_minutes: repeat,
        alert_on_any_down: true,
        alert_on_any_up: true,
        alert_on_integration_down: scope !== 'service',
        alert_on_integration_up: scope !== 'service',
        min_down_minutes: 0,
      },
      {
        onSuccess: () => {
          setScope('all');
          setRefId(null);
          setRepeat(0);
        },
      },
    );
  };

  const updateRule = (webhook: Webhook, patch: Partial<WebhookRuleUpdate>) => {
    const current: WebhookRuleUpdate = {
      scope_type: webhook.scope_type ?? 'all',
      scope_ref_id: webhook.scope_ref_id ?? null,
      repeat_interval_minutes: webhook.repeat_interval_minutes ?? 0,
      alert_on_any_down: !!webhook.alert_on_any_down,
      alert_on_any_up: !!webhook.alert_on_any_up,
      alert_on_integration_down: !!webhook.alert_on_integration_down,
      alert_on_integration_up: !!webhook.alert_on_integration_up,
      min_down_minutes: webhook.min_down_minutes ?? 0,
    };
    const next: WebhookRuleUpdate = { ...current, ...patch };
    if (patch.scope_type !== undefined && patch.scope_type !== current.scope_type) {
      // Nothing to point at: saving would blank the target and quietly mute the webhook.
      if (scopeUnavailable(patch.scope_type)) {
        toast.error(
          patch.scope_type === 'provider'
            ? t('settings.webhooks.scope_no_providers')
            : t('settings.webhooks.scope_no_services'),
        );
        return;
      }
      next.scope_ref_id = firstRefFor(patch.scope_type);
      if (patch.scope_type === 'service') {
        next.alert_on_integration_down = false;
        next.alert_on_integration_up = false;
      }
    }
    updateRules.mutate({ id: webhook.id, patch: next });
  };

  const testOne = (webhook: Webhook) => {
    setTestingId(webhook.id);
    hook.testWebhookById.mutate(webhook.id, {
      onSuccess: () => setOutcomes((prev) => ({ ...prev, [webhook.id]: { ok: true } })),
      onError: (err: unknown) =>
        setOutcomes((prev) => ({
          ...prev,
          [webhook.id]: { ok: false, error: translateApiError(err, t, t('settings.webhooks.test_failed')) },
        })),
      onSettled: () => setTestingId(null),
    });
  };

  const requestDelete = async (webhook: Webhook) => {
    const ok = await confirm({
      title: t('settings.webhooks.delete_title'),
      message: t('settings.webhooks.delete_message', { name: webhook.name }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (ok) hook.deleteWebhook.mutate(webhook.id);
  };

  const toggleExpanded = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const scopeSummary = (webhook: Webhook): string => {
    if (webhook.scope_type === 'provider') {
      const provider = providers.find((p) => p.id === webhook.scope_ref_id);
      return provider
        ? t('settings.webhooks.scope_provider_named', { name: provider.name })
        : t('settings.webhooks.choose_provider');
    }
    if (webhook.scope_type === 'service') {
      const service = services.find((s) => s.id === webhook.scope_ref_id);
      return service
        ? t('settings.webhooks.scope_service_named', { host: `${service.subdomain}.${service.domain}` })
        : t('settings.webhooks.choose_service');
    }
    return t('settings.webhooks.scope_all');
  };

  const needle = search.trim().toLowerCase();
  const visible = needle
    ? webhooks.filter(
        (w) => w.name.toLowerCase().includes(needle) || (w.url_masked ?? '').toLowerCase().includes(needle),
      )
    : webhooks;
  const enabledCount = webhooks.filter((w) => !!w.enabled).length;

  const targetSelect = (
    type: ScopeType,
    value: number | null,
    onChange: (id: number | null) => void,
    disabled = false,
  ) => (
    <Select
      value={value ?? ''}
      disabled={disabled || type === 'all'}
      onChange={(e) => {
        // The placeholder is a prompt, never an answer: the backend rejects a null target on a
        // provider/service scope with a 400, so re-selecting it would only produce a toast.
        if (!e.target.value) return;
        onChange(Number(e.target.value));
      }}
    >
      {type === 'all' && <option value="">{t('settings.webhooks.scope_target_hint')}</option>}
      {type === 'provider' && (
        <>
          <option value="" disabled>
            {t('settings.webhooks.choose_provider')}
          </option>
          {enabledProviders.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </>
      )}
      {type === 'service' && (
        <>
          <option value="" disabled>
            {t('settings.webhooks.choose_service')}
          </option>
          {enabledServices.map((s) => (
            <option key={s.id} value={s.id}>
              {s.subdomain}.{s.domain}
            </option>
          ))}
        </>
      )}
    </Select>
  );

  return (
    <div className="space-y-6">
      <form onSubmit={submitNew}>
        <SettingsSection
          icon={<Plus />}
          title={t('settings.webhooks.add_title')}
          description={
            <>
              {t('settings.webhooks.desc')}{' '}
              <a
                href={APPRISE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline"
              >
                Apprise
                <ExternalLink aria-hidden="true" className="h-3 w-3" />
              </a>
            </>
          }
          footer={
            <>
              <Button
                variant="outline"
                leftIcon={<Send />}
                loading={hook.testWebhookUrl.isPending}
                disabled={url.trim() === ''}
                onClick={() => hook.testWebhookUrl.mutate()}
              >
                {t('settings.webhooks.test_url')}
              </Button>
              <Button type="submit" leftIcon={<Plus />} loading={hook.addWebhook.isPending} disabled={!canAdd}>
                {t('settings.webhooks.add')}
              </Button>
            </>
          }
        >
          <div className="grid gap-4 md:grid-cols-2">
            <Field label={t('settings.webhooks.name_label')} required>
              <Input
                value={name}
                placeholder={t('settings.webhooks.name_placeholder')}
                autoComplete="off"
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label={t('settings.webhooks.url_label')} required hint={t('settings.webhooks.url_hint')}>
              <Input
                value={url}
                placeholder={t('settings.webhooks.url_placeholder')}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  setUrl(e.target.value);
                  if (testResult) setTestResult(null);
                }}
                className="font-mono text-xs"
              />
            </Field>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <Field label={t('settings.webhooks.scope')} hint={scopeHint}>
              <Select value={scope} onChange={(e) => changeNewScope(e.target.value as ScopeType)}>
                <option value="all">{t('settings.webhooks.scope_all')}</option>
                <option value="provider" disabled={noProviders}>
                  {t('settings.webhooks.scope_provider')}
                </option>
                <option value="service" disabled={noServices}>
                  {t('settings.webhooks.scope_service')}
                </option>
              </Select>
            </Field>
            <Field label={t('settings.webhooks.scope_target')}>{targetSelect(scope, refId, setRefId)}</Field>
            <Field label={t('settings.webhooks.repeat_interval')} hint={t('settings.webhooks.repeat_interval_hint')}>
              <Input
                type="number"
                inputMode="numeric"
                min={0}
                max={720}
                value={repeat}
                onChange={(e) => setRepeat(Math.max(0, Math.min(720, Number(e.target.value) || 0)))}
                className="tabular-nums"
              />
            </Field>
          </div>
          {testResult && (
            <InlineAlert
              tone={testResult.ok ? 'success' : 'danger'}
              title={testResult.ok ? t('settings.webhooks.test_sent') : t('settings.webhooks.test_failed')}
              onDismiss={() => setTestResult(null)}
            >
              {testResult.error}
            </InlineAlert>
          )}
        </SettingsSection>
      </form>

      <SettingsSection
        icon={<Bell />}
        title={t('settings.webhooks.title')}
        description={t('settings.webhooks.list_desc')}
        actions={
          <>
            {webhooks.length > 0 && (
              <Badge tone={enabledCount > 0 ? 'success' : 'neutral'} dot>
                {t('settings.webhooks.enabled_count', { enabled: enabledCount, total: webhooks.length })}
              </Badge>
            )}
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Send />}
              loading={testAllMutation.isPending}
              disabled={enabledCount === 0}
              onClick={() => testAllMutation.mutate()}
            >
              {t('settings.webhooks.test_all')}
            </Button>
          </>
        }
      >
        {testAll && (
          <InlineAlert
            tone={testAll.ok ? 'success' : 'warning'}
            title={t('settings.webhooks.test_all_result')}
            onDismiss={() => setTestAll(null)}
          >
            <ul className="mt-1 space-y-1">
              {testAll.results.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge size="sm" tone={r.ok ? 'success' : 'danger'} dot>
                    {r.ok ? t('settings.webhooks.test_ok') : t('settings.webhooks.test_ko')}
                  </Badge>
                  <span className="font-medium text-foreground">{r.name}</span>
                  {!r.ok && r.error && <span className="text-muted-foreground">{r.error}</span>}
                </li>
              ))}
            </ul>
          </InlineAlert>
        )}

        {webhooks.length > SEARCH_THRESHOLD && (
          <SearchInput value={search} onChange={setSearch} placeholder={t('settings.webhooks.search_placeholder')} />
        )}

        {webhooks.length === 0 ? (
          <EmptyState compact icon={<Bell />} title={t('settings.webhooks.empty')} />
        ) : visible.length === 0 ? (
          <EmptyState compact title={t('settings.webhooks.no_match')} />
        ) : (
          <ul className="space-y-3">
            {visible.map((webhook) => {
              const isOpen = expanded.has(webhook.id);
              const outcome = outcomes[webhook.id];
              const enabled = !!webhook.enabled;
              const scopeType: ScopeType = webhook.scope_type ?? 'all';
              return (
                <li
                  key={webhook.id}
                  className={cn(
                    'rounded-xl border border-border bg-card transition-opacity',
                    !enabled && 'opacity-70',
                  )}
                >
                  <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-foreground">{webhook.name}</span>
                        <Badge size="sm" tone={enabled ? 'success' : 'neutral'} dot>
                          {enabled ? t('settings.webhooks.enabled') : t('settings.webhooks.disabled')}
                        </Badge>
                        {outcome && (
                          <Badge size="sm" tone={outcome.ok ? 'success' : 'danger'}>
                            {outcome.ok ? t('settings.webhooks.test_ok') : t('settings.webhooks.test_ko')}
                          </Badge>
                        )}
                      </div>
                      <p className="mt-1 truncate text-xs text-muted-foreground">
                        {scopeSummary(webhook)}
                        <span aria-hidden="true"> · </span>
                        <code className="font-mono">{webhook.url_masked}</code>
                      </p>
                      {outcome && !outcome.ok && outcome.error && (
                        <p className="mt-1 text-xs text-destructive">{outcome.error}</p>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      <Switch
                        size="sm"
                        checked={enabled}
                        aria-label={t('settings.webhooks.toggle_aria', { name: webhook.name })}
                        disabled={hook.toggleWebhook.isPending}
                        onCheckedChange={(checked) => hook.toggleWebhook.mutate({ id: webhook.id, enabled: checked })}
                      />
                      <Button
                        variant="outline"
                        size="sm"
                        leftIcon={<Send />}
                        loading={testingId === webhook.id}
                        onClick={() => testOne(webhook)}
                      >
                        {t('settings.webhooks.test')}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-expanded={isOpen}
                        rightIcon={<ChevronDown className={cn('transition-transform', isOpen && 'rotate-180')} />}
                        onClick={() => toggleExpanded(webhook.id)}
                      >
                        {t('settings.webhooks.alert_rules')}
                      </Button>
                      <IconButton
                        label={t('settings.webhooks.delete_aria', { name: webhook.name })}
                        icon={<Trash2 />}
                        tooltip
                        disabled={hook.deleteWebhook.isPending}
                        onClick={() => void requestDelete(webhook)}
                        className="text-muted-foreground hover:text-destructive"
                      />
                    </div>
                  </div>

                  {isOpen && (
                    <div className="space-y-4 border-t border-border bg-muted/30 p-4 animate-in fade-in">
                      <p className="text-xs text-muted-foreground">{t('settings.webhooks.alert_rules_desc')}</p>
                      <div className="grid gap-4 md:grid-cols-3">
                        <Field label={t('settings.webhooks.scope')} hint={scopeHint}>
                          <Select
                            value={scopeType}
                            disabled={updateRules.isPending}
                            onChange={(e) => updateRule(webhook, { scope_type: e.target.value as ScopeType })}
                          >
                            <option value="all">{t('settings.webhooks.scope_all')}</option>
                            <option value="provider" disabled={noProviders && scopeType !== 'provider'}>
                              {t('settings.webhooks.scope_provider')}
                            </option>
                            <option value="service" disabled={noServices && scopeType !== 'service'}>
                              {t('settings.webhooks.scope_service')}
                            </option>
                          </Select>
                        </Field>
                        <Field label={t('settings.webhooks.scope_target')}>
                          {targetSelect(
                            scopeType,
                            webhook.scope_ref_id ?? null,
                            (id) => updateRule(webhook, { scope_ref_id: id }),
                            updateRules.isPending,
                          )}
                        </Field>
                        <Field label={t('settings.webhooks.repeat_interval')}>
                          <Input
                            type="number"
                            inputMode="numeric"
                            min={0}
                            max={720}
                            defaultValue={webhook.repeat_interval_minutes ?? 0}
                            disabled={updateRules.isPending}
                            onBlur={(e) => {
                              const value = Math.max(0, Math.min(720, Number(e.target.value) || 0));
                              if (value !== (webhook.repeat_interval_minutes ?? 0)) {
                                updateRule(webhook, { repeat_interval_minutes: value });
                              }
                            }}
                            className="tabular-nums"
                          />
                        </Field>
                      </div>
                      <div className="grid gap-4 md:grid-cols-2">
                        <fieldset className="space-y-2">
                          <legend className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                            {t('settings.webhooks.services_label')}
                          </legend>
                          <Checkbox
                            checked={!!webhook.alert_on_any_down}
                            disabled={updateRules.isPending}
                            onChange={(e) => updateRule(webhook, { alert_on_any_down: e.target.checked })}
                            label={t('settings.webhooks.on_service_down')}
                          />
                          <Checkbox
                            checked={!!webhook.alert_on_any_up}
                            disabled={updateRules.isPending}
                            onChange={(e) => updateRule(webhook, { alert_on_any_up: e.target.checked })}
                            label={t('settings.webhooks.on_service_up')}
                          />
                        </fieldset>
                        <fieldset className="space-y-2">
                          <legend className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                            {t('settings.webhooks.integrations_label')}
                          </legend>
                          <Checkbox
                            checked={!!webhook.alert_on_integration_down}
                            disabled={updateRules.isPending || scopeType === 'service'}
                            onChange={(e) => updateRule(webhook, { alert_on_integration_down: e.target.checked })}
                            label={t('settings.webhooks.on_integration_down')}
                          />
                          <Checkbox
                            checked={!!webhook.alert_on_integration_up}
                            disabled={updateRules.isPending || scopeType === 'service'}
                            onChange={(e) => updateRule(webhook, { alert_on_integration_up: e.target.checked })}
                            label={t('settings.webhooks.on_integration_up')}
                          />
                        </fieldset>
                      </div>
                      <Field
                        label={t('settings.webhooks.min_down_minutes')}
                        hint={t('settings.webhooks.min_down_minutes_hint')}
                        className="max-w-xs"
                      >
                        <Input
                          type="number"
                          inputMode="numeric"
                          min={0}
                          max={60}
                          defaultValue={webhook.min_down_minutes ?? 0}
                          disabled={updateRules.isPending}
                          onBlur={(e) => {
                            const value = Math.max(0, Math.min(60, Number(e.target.value) || 0));
                            if (value !== (webhook.min_down_minutes ?? 0)) updateRule(webhook, { min_down_minutes: value });
                          }}
                          className="tabular-nums"
                        />
                      </Field>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </SettingsSection>
      {ConfirmDialogElement}
    </div>
  );
}
