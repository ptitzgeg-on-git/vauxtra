import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, CircleAlert, Plug, Plus, RefreshCw } from 'lucide-react';
import { useT } from '@/i18n';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  InlineAlert,
  ProviderLogo,
  SectionHeading,
  SkeletonRow,
  Tooltip,
  buttonVariants,
  type Tone,
} from '@/components/ui';
import type { Provider, ProviderTypesResponse, ProvidersHealthMap } from '@/types/api';

export interface IntegrationsGlanceProps {
  providers: Provider[] | undefined;
  loading: boolean;
  health: ProvidersHealthMap | undefined;
  healthError: boolean;
  types: ProviderTypesResponse | undefined;
  /** The list came back empty because the request failed — not because there are none. */
  error: boolean;
  onRetry: () => void;
  onAddProvider: () => void;
  /** How many tiles to show before the "+N more" link. */
  limit?: number;
}

type HealthBadge = { tone: Tone; label: string; error?: string | null; dot: boolean };

/** One tile per integration: logo, name, type and its last health check. */
export function IntegrationsGlance({
  providers,
  loading,
  health,
  healthError,
  types,
  error,
  onRetry,
  onAddProvider,
  limit = 6,
}: IntegrationsGlanceProps) {
  const t = useT();

  const badgeFor = (p: Provider): HealthBadge => {
    if (!p.enabled) return { tone: 'neutral', label: t('providers.status.disabled'), dot: true };
    const entry = health?.[String(p.id)];
    if (entry?.status === 'unhealthy') {
      return { tone: 'danger', label: t('dashboard.integrations.health.unhealthy'), error: entry.error, dot: false };
    }
    if (entry?.status === 'healthy') return { tone: 'success', label: t('dashboard.integrations.health.healthy'), dot: true };
    if (healthError) return { tone: 'warning', label: t('dashboard.integrations.health.unknown'), dot: true };
    return { tone: 'neutral', label: t('dashboard.integrations.health.checking'), dot: true };
  };

  let body: ReactNode;
  if (loading) {
    body = (
      <div className="space-y-2">
        <SkeletonRow columns={3} />
        <SkeletonRow columns={3} />
        <SkeletonRow columns={3} />
      </div>
    );
  } else if (error) {
    // "No integrations yet" and "the list did not load" are the same empty array. Only one of
    // them should be invited to connect a first one; the other has to say what happened.
    body = (
      <InlineAlert
        tone="danger"
        action={
          <Button variant="outline" size="sm" leftIcon={<RefreshCw />} onClick={onRetry}>
            {t('ui.error.retry')}
          </Button>
        }
      >
        {t('dashboard.integrations.error')}
      </InlineAlert>
    );
  } else if (!providers || providers.length === 0) {
    body = (
      <EmptyState
        compact
        icon={<Plug />}
        title={t('dashboard.integrations.empty_title')}
        description={t('dashboard.integrations.empty_body')}
        action={
          <Button leftIcon={<Plus />} onClick={onAddProvider}>
            {t('dashboard.quick_actions.add_integration')}
          </Button>
        }
      />
    );
  } else {
    const shown = providers.slice(0, limit);
    const rest = providers.length - shown.length;
    body = (
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {shown.map((p) => {
          const badge = badgeFor(p);
          const typeLabel = types?.[p.type]?.label ?? p.type;
          const pill = (
            <Badge tone={badge.tone} size="sm" dot={badge.dot} icon={badge.dot ? undefined : <CircleAlert />}>
              {badge.label}
            </Badge>
          );
          return (
            <li key={p.id} className="animate-in fade-in">
              <Link
                to="/providers"
                className="flex h-full items-center gap-3 rounded-xl border border-border p-3 text-sm transition-colors hover:border-primary/30 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span aria-hidden="true" className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
                  <ProviderLogo type={p.type} className="h-5 w-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-foreground">{p.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">{typeLabel}</span>
                </span>
                {badge.error ? (
                  <Tooltip content={badge.error} placement="top">
                    {pill}
                  </Tooltip>
                ) : (
                  pill
                )}
              </Link>
            </li>
          );
        })}
        {rest > 0 && (
          <li className="sm:col-span-2">
            <Link
              to="/providers"
              className="flex items-center justify-center rounded-xl border border-dashed border-border px-3 py-2 text-xs font-semibold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t('dashboard.integrations.more', { count: rest })}
            </Link>
          </li>
        )}
      </ul>
    );
  }

  return (
    <Card className="p-5 sm:p-6">
      <SectionHeading title={t('dashboard.integrations.title')}>
        <Link to="/providers" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
          {t('dashboard.integrations.manage')}
          <ArrowRight aria-hidden="true" className="ml-1.5 h-3.5 w-3.5" />
        </Link>
      </SectionHeading>
      <div className="mt-4">{body}</div>
    </Card>
  );
}
