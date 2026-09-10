/**
 * The integrations the wizard has collected so far, grouped the way the Integrations page
 * groups them (`getProviderGroup`), so the two screens never disagree.
 *
 * Colours come from `toneClasses`, never from the `provider_color` the API sends — that field
 * carries raw palette classes (`bg-orange-500/10`), which would be the only hardcoded colours
 * left on the screen.
 */

import { GitMerge, Plus, Server, Trash2 } from 'lucide-react';
import { Badge, Button, EmptyState, ProviderLogo, cn, toneClasses, useConfirmDialog, type Tone } from '@/components/ui';
import {
  PROVIDER_GROUPS,
  fallbackIconByType,
  getDescription,
  getProviderGroup,
  type ProviderGroup,
  type ProviderTypeMeta,
} from '@/components/features/providers/providerConstants';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';
import type { ProviderItem } from './types';

interface ProvidersStepProps {
  providers: ProviderItem[];
  providerTypes?: Record<string, ProviderTypeMeta>;
  onAdd: () => void;
  onDelete: (id: number) => void;
  deleteIsPending: boolean;
  onBack: () => void;
  onContinue: () => void;
}

const GROUP_TITLE_KEY: Record<ProviderGroup, string> = {
  reverse: 'providers.section.reverse',
  tunnel: 'providers.section.tunnel',
  dns: 'providers.section.dns',
  other: 'providers.section.other',
};

const GROUP_TONE: Record<ProviderGroup, Tone> = {
  reverse: 'primary',
  tunnel: 'primary',
  dns: 'info',
  other: 'neutral',
};

export function ProvidersStep({
  providers,
  providerTypes,
  onAdd,
  onDelete,
  deleteIsPending,
  onBack,
  onContinue,
}: ProvidersStepProps) {
  const t = useT();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const metaOf = (type: string) => providerTypes?.[type];

  const askDelete = async (provider: ProviderItem) => {
    const ok = await confirm({
      title: t('providers.delete.title'),
      message: t('providers.delete.message', { name: provider.name }),
      confirmLabel: t('common.delete'),
      variant: 'danger',
    });
    if (ok) onDelete(provider.id);
  };

  const grouped = PROVIDER_GROUPS.map((group) => ({
    group,
    items: providers.filter((p) => getProviderGroup(p.type, metaOf(p.type)) === group),
  })).filter((entry) => entry.items.length > 0);

  return (
    <SetupStepShell
      icon={<GitMerge />}
      title={t('setup.providers.title')}
      description={t('setup.providers.subtitle')}
      onBack={onBack}
      primary={{
        label: providers.length > 0 ? t('setup.providers.continue') : t('setup.providers.skip'),
        onClick: onContinue,
      }}
    >
      {providers.length === 0 ? (
        <EmptyState
          compact
          icon={<Server />}
          title={t('setup.providers.empty_title')}
          description={t('setup.providers.empty_body')}
        />
      ) : (
        <div className="space-y-6">
          {grouped.map(({ group, items }) => {
            const tone = toneClasses(GROUP_TONE[group]);
            return (
              <section key={group} className="space-y-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                    {t(GROUP_TITLE_KEY[group])}
                  </h3>
                  <Badge size="sm" tone="neutral" className="ml-auto nums">
                    {items.length}
                  </Badge>
                </div>
                <ul className="space-y-2">
                  {items.map((provider) => {
                    const meta = metaOf(provider.type);
                    const FallbackIcon = fallbackIconByType[provider.type] || Server;
                    return (
                      <li
                        key={provider.id}
                        className="group flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 transition-colors hover:border-primary/30"
                      >
                        <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg border', tone.bg, tone.text, tone.border)}>
                          <ProviderLogo
                            type={provider.type}
                            className="h-4 w-4"
                            fallback={<FallbackIcon className="h-4 w-4" />}
                          />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-foreground">{provider.name}</span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {meta?.label || getDescription(provider.type, meta, t)}
                          </span>
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void askDelete(provider)}
                          disabled={deleteIsPending}
                          leftIcon={<Trash2 />}
                          className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                        >
                          {t('common.delete')}
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      <button
        type="button"
        onClick={onAdd}
        className="flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border py-4 text-sm font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Plus aria-hidden="true" className="h-4 w-4" />
        {t('setup.providers.add')}
      </button>

      {ConfirmDialogElement}
    </SetupStepShell>
  );
}
