import { memo, useMemo } from 'react';
import { AlertCircle, Eye, Lock, Pencil, RefreshCw, Server, ShieldCheck, Trash2 } from 'lucide-react';
import { Badge, IconButton, InlineAlert, ProviderLogo, Switch, Tooltip, cn } from '@/components/ui';
import { useFormat } from '@/hooks/useFormat';
import { useT } from '@/i18n';
import type { Provider, ProviderHealthStatus, ProviderHealthSummary } from '@/types/api';
import { type ProviderTypeMeta, fallbackIconByType, isTunnelType } from './providerConstants';
import {
  type HealthScore,
  type OperationalStatus,
  type ProviderDiagnostics,
  checkDetailText,
  healthTone,
  tunnelReasonLabel,
  tunnelStatusKey,
  tunnelTone,
} from './providerHealth';

export interface ProviderCardProps {
  provider: Provider;
  meta?: ProviderTypeMeta;
  health: HealthScore;
  status: OperationalStatus;
  /** Only a fresh (within TTL) manual test / validation; stale ones are dropped upstream. */
  diagnostics?: ProviderDiagnostics;
  tunnelHealth?: ProviderHealthStatus;
  autoHealth?: ProviderHealthSummary;
  testing?: boolean;
  validating?: boolean;
  deleting?: boolean;
  toggling?: boolean;
  onTest: () => void;
  onValidate: () => void;
  onInspect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onToggleEnabled: (enabled: boolean) => void;
}

/** `https://npm.lan:81/api` → `npm.lan:81`; anything that is not a URL is shown as typed. */
function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean)));
}

export const ProviderCard = memo(function ProviderCard({
  provider,
  meta,
  health,
  status,
  diagnostics,
  tunnelHealth,
  autoHealth,
  testing = false,
  validating = false,
  deleting = false,
  toggling = false,
  onTest,
  onValidate,
  onInspect,
  onEdit,
  onDelete,
  onToggleEnabled,
}: ProviderCardProps) {
  const t = useT();
  const { formatRelative, formatDateTime, formatLatency } = useFormat();

  const typeKey = String(provider.type || '').toLowerCase();
  const FallbackIcon = fallbackIconByType[typeKey] || Server;
  const typeLabel = meta?.label || provider.type;
  const enabled = Boolean(provider.enabled);
  const isTunnel = isTunnelType(typeKey, meta);
  const testedAt = diagnostics?.testedAt;

  // What the diagnostics block says: validation summary first, then the reasons.
  const report = useMemo(() => {
    const tunnelStatus = String(tunnelHealth?.status || '').toLowerCase();
    const autoUnhealthy = Boolean(autoHealth) && String(autoHealth?.status || '').toLowerCase() !== 'healthy';
    const tunnelFailing = Boolean(tunnelHealth) && ['down', 'degraded', 'error'].includes(tunnelStatus);
    const showAutoFailure = !diagnostics && (tunnelFailing || autoUnhealthy);
    if (!diagnostics && !showAutoFailure) return null;

    const checks = diagnostics?.validation?.checks || [];
    const blocking = checks.filter((c) => c.blocking && !c.ok).length;
    const warnings = checks.filter((c) => !c.blocking && !c.ok).length;
    const firstBlocking = checks.find((c) => c.blocking && !c.ok);
    const firstBlockingDetail = firstBlocking ? checkDetailText(firstBlocking, t) : undefined;
    const failedDetails = unique(
      checks.filter((c) => !c.ok && c.detail).map((c) => checkDetailText(c, t)),
    )
      .filter((detail) => detail !== firstBlockingDetail)
      .slice(0, 3);
    const warningDetails = unique(diagnostics?.validation?.warnings || [])
      .filter((w) => !failedDetails.includes(w) && w !== firstBlockingDetail)
      .slice(0, 3);
    const healthSource = diagnostics?.health || tunnelHealth || autoHealth;
    const healthError = String(healthSource?.error || tunnelReasonLabel(tunnelHealth?.reason, t) || '');
    const showHealthError = Boolean(healthError) && !failedDetails.includes(healthError) && !warningDetails.includes(healthError);

    let title: string;
    let tone: 'success' | 'warning' | 'danger';
    if (checks.length > 0) {
      if (blocking > 0) {
        title = t('providers.diag.action_required', { count: blocking });
        tone = 'danger';
      } else if (warnings > 0) {
        title = t('providers.diag.passed_warnings', { count: warnings });
        tone = 'warning';
      } else {
        title = t('providers.diag.passed');
        tone = diagnostics?.ok === false ? 'danger' : 'success';
      }
    } else if (diagnostics) {
      title = diagnostics.ok ? t('providers.diag.test_ok') : t('providers.diag.test_failed');
      tone = diagnostics.ok ? 'success' : 'danger';
    } else {
      title = t('providers.diag.test_failed');
      tone = 'danger';
    }

    return { title, tone, firstBlockingDetail, failedDetails, warningDetails, healthError, showHealthError };
  }, [autoHealth, diagnostics, t, tunnelHealth]);

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-card transition-shadow hover:shadow-elevated',
        !enabled && 'opacity-80',
      )}
      aria-label={provider.name}
    >
      <div className="flex flex-1 flex-col gap-4 p-5">
        <div className="flex items-start gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-muted text-primary">
            <ProviderLogo type={typeKey} className="h-6 w-6" fallback={<FallbackIcon className="h-6 w-6" />} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-semibold leading-tight text-foreground">{provider.name}</h3>
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
              <span className="font-medium uppercase tracking-wider">{typeLabel}</span>
              <span aria-hidden="true">·</span>
              <span className="truncate font-mono" title={provider.url || undefined}>
                {provider.url ? hostOf(provider.url) : t('providers.card.default_url')}
              </span>
            </p>
          </div>
          <Switch
            size="sm"
            checked={enabled}
            disabled={toggling}
            onCheckedChange={onToggleEnabled}
            aria-label={t('providers.card.toggle', { name: provider.name })}
            className="mt-0.5 shrink-0"
          />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={status.tone} dot size="sm">
            {t(status.labelKey)}
          </Badge>
          {health.score >= 0 && (
            <Tooltip content={health.reason || t('providers.health.score', { score: health.score })}>
              <Badge tone={healthTone[health.severity]} size="sm" className="tabular-nums">
                {t(`providers.health.${health.severity}`)} · {health.score}
              </Badge>
            </Tooltip>
          )}
          {isTunnel && tunnelHealth && (
            <Badge tone={tunnelTone(tunnelHealth.status)} size="sm" dot>
              {t(tunnelStatusKey(tunnelHealth.status))}
              {typeof tunnelHealth.active_connections === 'number' && (
                <span className="tabular-nums"> · {t('providers.tunnel.connections', { count: tunnelHealth.active_connections })}</span>
              )}
            </Badge>
          )}
          {meta?.read_only && (
            <Badge tone="neutral" size="sm" icon={<Lock />}>
              {t('providers.card.read_only')}
            </Badge>
          )}
          {provider.error_message && (
            <Tooltip content={provider.error_message}>
              <Badge tone="danger" size="sm" icon={<AlertCircle />}>
                {t('providers.card.error')}
              </Badge>
            </Tooltip>
          )}
        </div>

        {report && (
          <InlineAlert tone={report.tone} title={report.title} className="text-xs">
            <div className="space-y-1">
              {report.firstBlockingDetail && (
                <p className="truncate" title={report.firstBlockingDetail}>
                  {t('providers.diag.next_step', { detail: report.firstBlockingDetail })}
                </p>
              )}
              {report.failedDetails.map((detail) => (
                <p key={detail} className="truncate text-muted-foreground" title={detail}>
                  – {detail}
                </p>
              ))}
              {report.warningDetails.map((warning) => (
                <p key={warning} className="truncate text-warning" title={warning}>
                  – {warning}
                </p>
              ))}
              {report.showHealthError && (
                <p className="truncate text-destructive" title={report.healthError}>
                  {report.healthError}
                </p>
              )}
              {typeof diagnostics?.latency_ms === 'number' && (
                <p className="text-muted-foreground tabular-nums">{t('providers.card.latency', { value: formatLatency(diagnostics.latency_ms) })}</p>
              )}
              {diagnostics?.version && (
                <p className="text-muted-foreground">{t('providers.card.version', { value: diagnostics.version })}</p>
              )}
            </div>
          </InlineAlert>
        )}
      </div>

      <footer className="mt-auto flex items-center justify-between gap-3 border-t border-border bg-muted/40 px-5 py-3">
        <p className="min-w-0 truncate text-xs text-muted-foreground">
          {testedAt ? (
            <Tooltip content={formatDateTime(new Date(testedAt))}>
              <span className="cursor-default">{t('providers.card.last_test', { when: formatRelative(new Date(testedAt)) })}</span>
            </Tooltip>
          ) : (
            t('providers.card.never_tested')
          )}
        </p>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton
            className="h-8 w-8"
            tooltip
            label={t('providers.actions.test')}
            icon={<RefreshCw />}
            loading={testing}
            onClick={onTest}
          />
          <IconButton
            className="h-8 w-8"
            tooltip
            label={t('providers.actions.validate')}
            icon={<ShieldCheck />}
            loading={validating}
            onClick={onValidate}
          />
          <IconButton className="h-8 w-8" tooltip label={t('providers.actions.inspect')} icon={<Eye />} onClick={onInspect} />
          <IconButton className="h-8 w-8" tooltip label={t('providers.actions.edit')} icon={<Pencil />} onClick={onEdit} />
          <IconButton
            className="h-8 w-8 hover:bg-destructive/10 hover:text-destructive"
            tooltip
            label={t('providers.actions.delete')}
            icon={<Trash2 />}
            loading={deleting}
            onClick={onDelete}
          />
        </div>
      </footer>
    </article>
  );
});
