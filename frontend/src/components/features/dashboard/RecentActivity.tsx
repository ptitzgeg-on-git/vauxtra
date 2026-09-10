import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, History, RefreshCw } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  InlineAlert,
  SectionHeading,
  SkeletonRow,
  Tooltip,
  buttonVariants,
  cn,
  toneClasses,
  type Tone,
} from '@/components/ui';
import type { LogEntry } from '@/types/api';

export interface RecentActivityProps {
  logs: LogEntry[] | undefined;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  /** Shared "now" so every relative time in the list agrees; ticks from the page. */
  now: number;
}

const LEVEL_TONE: Record<string, Tone> = {
  ok: 'success',
  info: 'info',
  warning: 'warning',
  warn: 'warning',
  error: 'danger',
};

const LEVEL_KEY: Record<string, string> = {
  ok: 'settings.logs.level_ok',
  info: 'settings.logs.level_info',
  warning: 'settings.logs.level_warning',
  warn: 'settings.logs.level_warning',
  error: 'settings.logs.level_error',
};

/** The last system-log entries as a timeline, newest first. */
export function RecentActivity({ logs, loading, error, onRetry, now }: RecentActivityProps) {
  const t = useT();
  const { formatRelative, formatDateTime } = useFormat();

  let body: ReactNode;
  if (loading) {
    body = (
      <div className="space-y-2">
        <SkeletonRow columns={3} />
        <SkeletonRow columns={3} />
        <SkeletonRow columns={3} />
        <SkeletonRow columns={3} />
      </div>
    );
  } else if (error) {
    body = (
      <InlineAlert
        tone="danger"
        action={
          <Button variant="outline" size="sm" leftIcon={<RefreshCw />} onClick={onRetry}>
            {t('ui.error.retry')}
          </Button>
        }
      >
        {t('dashboard.activity.error')}
      </InlineAlert>
    );
  } else if (!logs || logs.length === 0) {
    body = (
      <EmptyState compact icon={<History />} title={t('dashboard.activity.empty')} description={t('dashboard.activity.empty_hint')} />
    );
  } else {
    body = (
      <ol className="relative">
        <span aria-hidden="true" className="absolute bottom-3 left-[5px] top-3 w-px bg-border" />
        {logs.map((log) => {
          const level = String(log.level || 'info').toLowerCase();
          const tone = LEVEL_TONE[level] ?? 'info';
          const c = toneClasses(tone);
          return (
            <li key={log.id} className="relative flex gap-3 py-2 pl-5 animate-in fade-in">
              <span aria-hidden="true" className={cn('absolute left-0 top-[15px] h-[11px] w-[11px] rounded-full ring-4 ring-card', c.dot)} />
              <div className="min-w-0 flex-1">
                <p className="break-words text-sm leading-relaxed text-foreground">{log.message}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge tone={tone} size="sm">
                    {t(LEVEL_KEY[level] ?? 'settings.logs.level_info')}
                  </Badge>
                  <Tooltip content={formatDateTime(log.created_at, 'medium')} placement="top">
                    <span className="text-xs tabular-nums text-muted-foreground">{formatRelative(log.created_at, now)}</span>
                  </Tooltip>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <Card className="p-5 sm:p-6">
      <SectionHeading title={t('dashboard.activity.title')} description={t('dashboard.activity.description')}>
        <Link to="/settings?tab=logs" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
          {t('dashboard.activity.view_all')}
          <ArrowRight aria-hidden="true" className="ml-1.5 h-3.5 w-3.5" />
        </Link>
      </SectionHeading>
      <div className="mt-4">{body}</div>
    </Card>
  );
}
