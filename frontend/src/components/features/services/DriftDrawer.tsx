import type { ReactNode } from 'react';
import { ArrowRight, CircleAlert, CircleCheck, GitCompareArrows, RefreshCw, TriangleAlert, CloudUpload } from 'lucide-react';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { Badge, Button, Drawer, EmptyState, InlineAlert, SectionHeading, Skeleton } from '@/components/ui';
import type { Tone } from '@/components/ui';
import type { DriftIssue, DriftResult, ReconcileResult, Service } from '@/types/api';
import { publicHostOf } from './helpers';

export interface DriftDrawerProps {
  open: boolean;
  onClose: () => void;
  service: Service | null;
  drift: DriftResult | undefined;
  isChecking: boolean;
  checkError: string | null;
  onRecheck: () => void;
  isReconciling: boolean;
  /** The page asks for confirmation first, then pushes — the drawer only requests it. */
  onReconcile: () => void;
  reconcileResult: ReconcileResult | undefined;
}

/** `services.drift.type.<type>` for the known issue types; the raw type otherwise. */
const KNOWN_ISSUE_TYPES = new Set([
  'missing_proxy_route',
  'proxy_origin_mismatch',
  'proxy_check_failed',
  'missing_dns_rewrite',
  'dns_target_mismatch',
  'dns_check_failed',
]);

type Translate = (key: string, params?: Record<string, string | number>) => string;

function issueLabel(t: Translate, issue: DriftIssue): string {
  return KNOWN_ISSUE_TYPES.has(issue.type) ? t(`services.drift.type.${issue.type}`) : issue.type;
}

/**
 * What drifted, in the reader's language. The API sends the English sentence in `detail` and
 * the short code it was written from in `detail_key`; a `*_check_failed` issue carries the
 * provider's own error and no code, so its sentence is shown as it came.
 */
function issueDetail(t: Translate, issue: DriftIssue): string {
  const fallback = String(issue.detail || '');
  if (!issue.detail_key) return fallback;
  const key = `services.drift.detail.${issue.detail_key}`;
  const line = t(key, issue.detail_params);
  return line === key ? fallback : line;
}

function IssueList({ issues }: { issues: DriftIssue[] }) {
  const t = useT();
  if (issues.length === 0) return null;
  return (
    <ul className="space-y-2">
      {issues.map((issue, index) => {
        const tone: Tone = issue.severity === 'error' ? 'danger' : 'warning';
        const detail = issueDetail(t, issue);
        return (
          <li
            key={`${issue.type}-${issue.provider}-${index}`}
            className="flex items-start gap-3 rounded-xl border border-border bg-card px-3 py-2.5"
          >
            <span aria-hidden="true" className={cn('mt-0.5 shrink-0', issue.severity === 'error' ? 'text-destructive' : 'text-warning')}>
              {issue.severity === 'error' ? <CircleAlert className="h-4 w-4" /> : <TriangleAlert className="h-4 w-4" />}
            </span>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={tone} size="sm">
                  {issue.severity === 'error' ? t('services.drift.severity.error') : t('services.drift.severity.warn')}
                </Badge>
                <span className="text-sm font-medium text-foreground">{issueLabel(t, issue)}</span>
                {issue.provider && <span className="text-xs text-muted-foreground">· {issue.provider}</span>}
              </div>
              {detail && <p className="wrap-break-word text-xs text-muted-foreground">{detail}</p>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function DriftSummary({ drift }: { drift: DriftResult }) {
  const t = useT();
  const errors = drift.issues.filter((i) => i.severity === 'error').length;
  const warns = drift.issues.length - errors;
  if (drift.ok) {
    return (
      <InlineAlert tone="success" icon={<CircleCheck />} title={t('services.drift.in_sync_title')}>
        {t('services.drift.in_sync_body', { host: drift.public_host })}
      </InlineAlert>
    );
  }
  return (
    <InlineAlert
      tone={errors > 0 ? 'danger' : 'warning'}
      title={t('services.drift.out_of_sync_title', { count: drift.issues.length })}
    >
      {t('services.drift.out_of_sync_body', {
        errors: t('services.drift.errors', { count: errors }),
        warnings: t('services.drift.warnings', { count: warns }),
      })}
    </InlineAlert>
  );
}

function CountBadges({ drift }: { drift: DriftResult }) {
  const t = useT();
  const errors = drift.issues.filter((i) => i.severity === 'error').length;
  const warns = drift.issues.length - errors;
  if (drift.ok) {
    return (
      <Badge tone="success" size="sm" dot>
        {t('services.drift.in_sync')}
      </Badge>
    );
  }
  return (
    <span className="inline-flex flex-wrap gap-1">
      {errors > 0 && (
        <Badge tone="danger" size="sm" dot>
          {t('services.drift.errors', { count: errors })}
        </Badge>
      )}
      {warns > 0 && (
        <Badge tone="warning" size="sm" dot>
          {t('services.drift.warnings', { count: warns })}
        </Badge>
      )}
    </span>
  );
}

const pushErrorsOf = (push: Record<string, unknown> | undefined): string[] => {
  const errors = push?.errors;
  return Array.isArray(errors) ? errors.map((e) => String(e)) : [];
};

function ReconcileSummary({ result }: { result: ReconcileResult }) {
  const t = useT();
  const pushErrors = pushErrorsOf(result.push);
  const pushOk = result.push && typeof result.push.ok === 'boolean' ? Boolean(result.push.ok) : pushErrors.length === 0;
  return (
    <section aria-label={t('services.drift.reconcile_result')} className="space-y-3">
      <SectionHeading size="sm" title={t('services.drift.reconcile_result')} description={t('services.drift.reconcile_result_desc')} />
      <InlineAlert
        tone={result.ok ? 'success' : pushOk ? 'warning' : 'danger'}
        icon={result.ok ? <CircleCheck /> : <TriangleAlert />}
        title={result.ok ? t('services.drift.reconciled_title') : t('services.drift.reconcile_partial_title')}
      >
        {result.ok ? t('services.drift.reconciled_body') : t('services.drift.reconcile_partial_body')}
      </InlineAlert>
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-xl border border-border bg-muted/40 p-3">
        <div className="min-w-0 space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t('services.drift.before')}</p>
          <CountBadges drift={result.before} />
        </div>
        <ArrowRight aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
        <div className="min-w-0 space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t('services.drift.after')}</p>
          <CountBadges drift={result.after} />
        </div>
      </div>
      {pushErrors.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-destructive">{t('services.drift.push_errors')}</p>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {pushErrors.map((error, index) => (
              <li key={index} className="wrap-break-word">
                {error}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.after.issues.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-foreground">{t('services.drift.remaining_issues')}</p>
          <IssueList issues={result.after.issues} />
        </div>
      )}
    </section>
  );
}

/** Drift report for one route: what the providers actually serve versus what Vauxtra expects. */
export function DriftDrawer({
  open,
  onClose,
  service,
  drift,
  isChecking,
  checkError,
  onRecheck,
  isReconciling,
  onReconcile,
  reconcileResult,
}: DriftDrawerProps) {
  const t = useT();
  const host = service ? publicHostOf(service) : '';
  const canReconcile = Boolean(drift) && !drift?.ok && !isChecking && !isReconciling;

  let body: ReactNode;
  if (isChecking && !drift) {
    body = (
      <div className="space-y-3" aria-busy="true" aria-label={t('services.drift.checking')}>
        <Skeleton className="h-16 w-full rounded-xl" />
        <Skeleton className="h-12 w-full rounded-xl" />
        <Skeleton className="h-12 w-full rounded-xl" />
      </div>
    );
  } else if (checkError && !drift) {
    body = (
      <EmptyState
        compact
        icon={<CircleAlert />}
        title={t('services.drift.check_failed')}
        description={checkError}
        action={
          <Button variant="outline" size="sm" leftIcon={<RefreshCw />} onClick={onRecheck}>
            {t('ui.error.retry')}
          </Button>
        }
      />
    );
  } else if (drift) {
    body = (
      <div className="space-y-5">
        <DriftSummary drift={drift} />
        {checkError && (
          <InlineAlert tone="danger" title={t('services.drift.check_failed')}>
            {checkError}
          </InlineAlert>
        )}
        {drift.issues.length > 0 && (
          <section aria-label={t('services.drift.issues')} className="space-y-2">
            <SectionHeading size="sm" title={t('services.drift.issues')}>
              <CountBadges drift={drift} />
            </SectionHeading>
            <IssueList issues={drift.issues} />
          </section>
        )}
        {reconcileResult && <ReconcileSummary result={reconcileResult} />}
        <p className="text-xs text-muted-foreground">{t('services.drift.note')}</p>
      </div>
    );
  } else {
    body = (
      <EmptyState
        compact
        icon={<GitCompareArrows />}
        title={t('services.drift.empty_title')}
        description={t('services.drift.empty_body')}
        action={
          <Button variant="primary" size="sm" leftIcon={<RefreshCw />} onClick={onRecheck}>
            {t('services.drift.check')}
          </Button>
        }
      />
    );
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size="lg"
      icon={<GitCompareArrows />}
      title={t('services.drift.title')}
      description={host ? t('services.drift.description', { host }) : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={isReconciling}>
            {t('ui.close')}
          </Button>
          <Button variant="outline" leftIcon={<RefreshCw />} loading={isChecking} disabled={isReconciling} onClick={onRecheck}>
            {t('services.drift.recheck')}
          </Button>
          <Button variant="primary" leftIcon={<CloudUpload />} loading={isReconciling} disabled={!canReconcile} onClick={onReconcile}>
            {t('services.drift.reconcile')}
          </Button>
        </>
      }
    >
      {body}
    </Drawer>
  );
}
