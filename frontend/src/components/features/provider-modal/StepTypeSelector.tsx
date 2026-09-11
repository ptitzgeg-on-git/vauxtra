import { useMemo, useState } from 'react';
import { Container, Server } from 'lucide-react';
import { Button, Chip, ChipGroup, EmptyState, InlineAlert, ProviderLogo, Skeleton, cn } from '@/components/ui';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import {
  PROVIDER_GROUPS,
  type ProviderGroup,
  type ProviderTypeMeta,
  fallbackIconByType,
  getDescription,
  getProviderGroup,
  isDnsType,
} from '@/components/features/providers/providerConstants';

type Filter = ProviderGroup | 'docker' | 'all';

export interface StepTypeSelectorProps {
  /** Available types, already sorted by label. */
  types: Array<[string, ProviderTypeMeta]>;
  /**
   * `GET /api/providers/types` is still in flight. An empty `types` means two very different
   * things -- "the request has not answered yet" and "this build serves no provider" -- and
   * the second one is a dead end the user cannot act on. Without this flag the first paint of
   * the wizard told every user their install had no integrations at all.
   */
  loading?: boolean;
  /**
   * The request failed. Same reasoning as `loading` one step further: an empty `types` after a
   * failed fetch is not "this build serves no provider", it is "we do not know". Saying the
   * former sends the user looking for a packaging bug that does not exist.
   */
  error?: unknown;
  refreshing?: boolean;
  onRetry?: () => void;
  selectedType: string;
  isDockerMode: boolean;
  onChooseProvider: (type: string, meta: ProviderTypeMeta) => void;
  onChooseDocker: () => void;
}

const CARD =
  'flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
const CARD_IDLE = 'border-border bg-card hover:border-primary/40 hover:bg-accent';
const CARD_SELECTED = 'border-primary bg-primary/10 ring-1 ring-primary/30';

export function StepTypeSelector({
  types,
  loading = false,
  error = null,
  refreshing = false,
  onRetry,
  selectedType,
  isDockerMode,
  onChooseProvider,
  onChooseDocker,
}: StepTypeSelectorProps) {
  const t = useT();
  const [filter, setFilter] = useState<Filter>('all');

  const grouped = useMemo(() => {
    const byGroup: Record<ProviderGroup, Array<[string, ProviderTypeMeta]>> = { reverse: [], tunnel: [], dns: [], other: [] };
    for (const entry of types) byGroup[getProviderGroup(entry[0], entry[1])].push(entry);
    return PROVIDER_GROUPS.map((group) => ({ group, entries: byGroup[group] })).filter((g) => g.entries.length > 0);
  }, [types]);

  const visibleGroups = loading || filter === 'docker' ? [] : filter === 'all' ? grouped : grouped.filter((g) => g.group === filter);
  const showDocker = filter === 'all' || filter === 'docker';
  const groupLabel = (group: ProviderGroup) => t(`providers.section.${group}`);

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold text-foreground">{t('provider_modal.type.title')}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{t('provider_modal.type.description')}</p>
      </div>

      {loading ? (
        <section aria-label={t('provider_modal.type.loading')} className="space-y-2" role="status" aria-busy="true">
          <Skeleton className="h-3 w-28" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className={cn(CARD, CARD_IDLE)}>
                <Skeleton className="mt-0.5 h-10 w-10 shrink-0 rounded-lg" />
                <span className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-4 w-1/2" />
                  <Skeleton className="h-3 w-3/4" />
                </span>
              </div>
            ))}
          </div>
          <span className="sr-only">{t('provider_modal.type.loading')}</span>
        </section>
      ) : (
        <>
        <ChipGroup label={t('provider_modal.type.filter_label')}>
          <Chip size="sm" selected={filter === 'all'} onClick={() => setFilter('all')} count={types.length + 1}>
            {t('provider_modal.type.all')}
          </Chip>
          {grouped.map(({ group, entries }) => (
            <Chip key={group} size="sm" selected={filter === group} onClick={() => setFilter(group)} count={entries.length}>
              {groupLabel(group)}
            </Chip>
          ))}
          <Chip size="sm" selected={filter === 'docker'} onClick={() => setFilter('docker')} icon={<Container />}>
            {t('provider_modal.type.docker_group')}
          </Chip>
        </ChipGroup>

        {error && filter !== 'docker' ? (
          <InlineAlert
            tone="danger"
            title={t('provider_modal.type.load_failed')}
            action={
              onRetry ? (
                <Button variant="outline" size="sm" loading={refreshing} onClick={onRetry}>
                  {t('common.retry')}
                </Button>
              ) : undefined
            }
          >
            {translateApiError(error, t, t('provider_modal.type.load_failed_hint'))}
          </InlineAlert>
        ) : (
          types.length === 0 &&
          filter !== 'docker' && <EmptyState compact icon={<Server />} title={t('provider_modal.type.empty')} />
        )}

        {visibleGroups.map(({ group, entries }) => (
          <section key={group} aria-label={groupLabel(group)} className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{groupLabel(group)}</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {entries.map(([type, meta]) => {
                const FallbackIcon = fallbackIconByType[type] || Server;
                const selected = !isDockerMode && selectedType === type;
                const description =
                  getDescription(type, meta, t) ||
                  (isDnsType(type, meta) ? t('provider_modal.type.dns_fallback') : t('provider_modal.type.proxy_fallback'));
                return (
                  <button
                    key={type}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onChooseProvider(type, meta)}
                    className={cn(CARD, selected ? CARD_SELECTED : CARD_IDLE)}
                  >
                    <span
                      className={cn(
                        'mt-0.5 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border',
                        selected ? 'border-primary/30 bg-primary/10 text-primary' : 'border-border bg-muted text-primary',
                      )}
                    >
                      <ProviderLogo type={type} className="h-6 w-6" fallback={<FallbackIcon className="h-6 w-6" />} />
                    </span>
                    <span className="min-w-0">
                      <span className={cn('block text-sm font-semibold', selected ? 'text-primary' : 'text-foreground')}>
                        {meta.label || type}
                      </span>
                      <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{description}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}

        </>
      )}

      {showDocker && (
        <section aria-label={t('provider_modal.type.docker_group')} className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{t('provider_modal.type.docker_group')}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <button
              type="button"
              aria-pressed={isDockerMode}
              onClick={onChooseDocker}
              className={cn(CARD, isDockerMode ? CARD_SELECTED : CARD_IDLE)}
            >
              <span
                className={cn(
                  'mt-0.5 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border',
                  isDockerMode ? 'border-primary/30 bg-primary/10 text-primary' : 'border-border bg-muted text-primary',
                )}
              >
                <Container className="h-6 w-6" />
              </span>
              <span className="min-w-0">
                <span className={cn('block text-sm font-semibold', isDockerMode ? 'text-primary' : 'text-foreground')}>
                  {t('provider_modal.type.docker_title')}
                </span>
                <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{t('provider_modal.type.docker_description')}</span>
              </span>
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
