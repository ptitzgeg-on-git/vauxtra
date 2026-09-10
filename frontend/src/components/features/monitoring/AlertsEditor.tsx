import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { BellOff, BellRing, RotateCcw, Save } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  Checkbox,
  EmptyState,
  Field,
  InlineAlert,
  Input,
  SkeletonRow,
  Switch,
  buttonVariants,
  cn,
} from '@/components/ui';
import type { OkResponse, ServiceAlertRow, ServiceAlertsConfig, Webhook } from '@/types/api';

/**
 * The per-service alert rules, bound to `GET`/`POST /api/services/{sid}/alerts`.
 *
 * The POST **replaces** every rule of the service (`set_service_alerts` deletes them all
 * first), so the editor always sends the full list, and a row with no webhook selected is
 * simply absent from it. `min_down_minutes` is the delay before a "down" alert fires — the
 * route stores it as an int, `0` meaning "as soon as the check fails".
 */

interface AlertDraft {
  on_up: boolean;
  on_down: boolean;
  min_down_minutes: number;
}

const DEFAULT_DRAFT: AlertDraft = { on_up: true, on_down: true, min_down_minutes: 0 };

function toDraft(rows: ServiceAlertRow[] | undefined): Record<number, AlertDraft> {
  const draft: Record<number, AlertDraft> = {};
  for (const row of rows ?? []) {
    draft[row.webhook_id] = {
      on_up: Boolean(row.on_up),
      on_down: Boolean(row.on_down),
      min_down_minutes: Number(row.min_down_minutes) || 0,
    };
  }
  return draft;
}

function sameDraft(a: Record<number, AlertDraft>, b: Record<number, AlertDraft>): boolean {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((key) => {
    const left = a[Number(key)];
    const right = b[Number(key)];
    return (
      right !== undefined &&
      left.on_up === right.on_up &&
      left.on_down === right.on_down &&
      left.min_down_minutes === right.min_down_minutes
    );
  });
}

export interface AlertsEditorProps {
  serviceId: number;
  /** Shown next to the heading so the operator knows what the rules are attached to. */
  host: string;
}

/** Which webhooks this one service notifies, and on what. */
export function AlertsEditor({ serviceId, host }: AlertsEditorProps) {
  const t = useT();
  const queryClient = useQueryClient();

  const {
    data: webhooks,
    isPending: webhooksPending,
    isError: webhooksError,
    refetch: refetchWebhooks,
  } = useQuery<Webhook[]>({
    queryKey: ['webhooks'],
    queryFn: () => api.get<Webhook[]>('/webhooks'),
  });

  const {
    data: alerts,
    isPending: alertsPending,
    isError: alertsError,
    refetch: refetchAlerts,
  } = useQuery<ServiceAlertRow[]>({
    queryKey: ['service-alerts', serviceId],
    queryFn: () => api.get<ServiceAlertRow[]>(`/services/${serviceId}/alerts`),
  });

  const saved = useMemo(() => toDraft(alerts), [alerts]);

  // `null` means "follow the server": until the operator touches something, a background
  // refetch is free to change what is on screen. The first edit pins a local copy, so a
  // poll landing mid-edit can no longer throw the unsaved rules away.
  const [edited, setEdited] = useState<Record<number, AlertDraft> | null>(null);
  const draft = edited ?? saved;
  const dirty = edited !== null && !sameDraft(edited, saved);

  const save = useMutation({
    mutationFn: (body: ServiceAlertsConfig) => api.post<OkResponse>(`/services/${serviceId}/alerts`, body),
    onSuccess: async () => {
      setEdited(null);
      await queryClient.invalidateQueries({ queryKey: ['service-alerts', serviceId] });
      toast.success(t('monitoring.alerts.saved'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('monitoring.alerts.save_failed'))),
  });

  const toggle = (webhookId: number, enabled: boolean) => {
    setEdited((current) => {
      const next = { ...(current ?? saved) };
      if (enabled) next[webhookId] = next[webhookId] ?? { ...DEFAULT_DRAFT };
      else delete next[webhookId];
      return next;
    });
  };

  const patch = (webhookId: number, values: Partial<AlertDraft>) => {
    setEdited((current) => {
      const base = current ?? saved;
      const existing = base[webhookId] ?? { ...DEFAULT_DRAFT };
      return { ...base, [webhookId]: { ...existing, ...values } };
    });
  };

  const submit = () => {
    save.mutate({
      alerts: Object.entries(draft).map(([webhookId, values]) => ({
        webhook_id: Number(webhookId),
        on_up: values.on_up,
        on_down: values.on_down,
        min_down_minutes: values.min_down_minutes,
      })),
    });
  };

  if (webhooksPending || alertsPending) {
    return (
      <div className="space-y-2">
        <SkeletonRow columns={3} />
        <SkeletonRow columns={3} />
      </div>
    );
  }

  if (webhooksError || alertsError) {
    return (
      <InlineAlert
        tone="danger"
        title={t('monitoring.alerts.load_failed')}
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void refetchWebhooks();
              void refetchAlerts();
            }}
          >
            {t('common.retry')}
          </Button>
        }
      >
        {t('monitoring.alerts.load_failed_hint')}
      </InlineAlert>
    );
  }

  const rows = webhooks ?? [];
  const selectedCount = Object.keys(draft).length;

  if (rows.length === 0) {
    return (
      <EmptyState
        compact
        icon={<BellOff />}
        title={t('monitoring.alerts.no_webhooks')}
        description={t('monitoring.alerts.no_webhooks_hint')}
        action={
          <Link to="/settings?tab=webhooks" className={buttonVariants({ variant: 'primary', size: 'sm' })}>
            {t('monitoring.alerts.configure_webhooks')}
          </Link>
        }
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t('monitoring.alerts.description', { host })}</p>

      <ul className="space-y-2">
        {rows.map((webhook) => {
          const values = draft[webhook.id];
          const selected = values !== undefined;
          return (
            <li
              key={webhook.id}
              className={cn(
                'rounded-xl border p-3 transition-colors',
                selected ? 'border-primary/30 bg-primary/5' : 'border-border bg-card',
              )}
            >
              <div className="flex items-start gap-3">
                <Checkbox
                  checked={selected}
                  onChange={(event) => toggle(webhook.id, event.target.checked)}
                  aria-label={t('monitoring.alerts.notify_through', { name: webhook.name })}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">{webhook.name}</span>
                    {!webhook.enabled && (
                      <Badge tone="neutral" size="sm">
                        {t('monitoring.alerts.webhook_disabled')}
                      </Badge>
                    )}
                  </div>
                  <p className="truncate font-mono text-[11px] text-muted-foreground">{webhook.url_masked}</p>
                </div>
              </div>

              {selected && (
                <div className="mt-3 grid gap-3 border-t border-border/60 pt-3 sm:grid-cols-3">
                  <Switch
                    checked={values.on_down}
                    onCheckedChange={(checked) => patch(webhook.id, { on_down: checked })}
                    label={t('monitoring.alerts.on_down')}
                    size="sm"
                  />
                  <Switch
                    checked={values.on_up}
                    onCheckedChange={(checked) => patch(webhook.id, { on_up: checked })}
                    label={t('monitoring.alerts.on_up')}
                    size="sm"
                  />
                  <Field
                    label={t('monitoring.alerts.min_down_minutes')}
                    hint={t('monitoring.alerts.min_down_minutes_hint')}
                  >
                    <Input
                      type="number"
                      min={0}
                      max={1440}
                      size="sm"
                      value={String(values.min_down_minutes)}
                      onChange={(event) =>
                        patch(webhook.id, {
                          min_down_minutes: Math.max(0, Math.min(1440, Number(event.target.value) || 0)),
                        })
                      }
                    />
                  </Field>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
        <p className="text-xs text-muted-foreground">
          {t('monitoring.alerts.selected_count', { count: selectedCount })}
        </p>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" leftIcon={<RotateCcw />} disabled={!dirty} onClick={() => setEdited(null)}>
            {t('monitoring.alerts.reset')}
          </Button>
          <Button
            size="sm"
            leftIcon={selectedCount > 0 ? <BellRing /> : <Save />}
            loading={save.isPending}
            disabled={!dirty}
            onClick={submit}
          >
            {t('common.save')}
          </Button>
        </div>
      </div>
    </div>
  );
}
