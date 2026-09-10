/**
 * Last real step: everything `POST /api/services/sync` found on the connected providers,
 * offered as a checklist. The rows keep the existing `setup.import.*` wording; what is new is
 * the master checkbox (with its indeterminate state) and real checkboxes on each row.
 *
 * Provider colours come from `toneClasses`, never from the `provider_color` the API sends —
 * that field carries raw palette classes and would be the only hardcoded colour on the screen.
 */

import { CheckCircle2, Download, Globe, RefreshCw, Server } from 'lucide-react';
import {
  Badge,
  Button,
  Checkbox,
  EmptyState,
  ProviderLogo,
  cn,
  toneClasses,
  type Tone,
} from '@/components/ui';
import { fallbackIconByType } from '@/components/features/providers/providerConstants';
import { useFormat } from '@/hooks/useFormat';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';
import type { ImportableService, ProviderItem } from './types';

interface ImportStepProps {
  providers: ProviderItem[];
  importableServices: ImportableService[];
  loadingImportable: boolean;
  onToggle: (index: number) => void;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  onRetry: () => void;
  onImportAndFinish: () => void;
  onBack: () => void;
  importing?: boolean;
}

const KIND_TONE: Record<ImportableService['kind'], Tone> = { proxy: 'primary', dns: 'info' };

export function ImportStep({
  providers,
  importableServices,
  loadingImportable,
  onToggle,
  onSelectAll,
  onDeselectAll,
  onRetry,
  onImportAndFinish,
  onBack,
  importing = false,
}: ImportStepProps) {
  const t = useT();
  const { formatNumber } = useFormat();

  const total = importableServices.length;
  const selected = importableServices.filter((s) => s.selected).length;
  const allSelected = total > 0 && selected === total;

  const hasRows = !loadingImportable && providers.length > 0 && total > 0;

  return (
    <SetupStepShell
      icon={<Download />}
      title={t('setup.import.title')}
      description={t('setup.import.subtitle')}
      onBack={onBack}
      backDisabled={importing}
      primary={{
        label: selected > 0 ? t('setup.import.finish_import', { count: selected }) : t('setup.import.finish_skip'),
        onClick: onImportAndFinish,
        loading: importing,
      }}
    >
      {loadingImportable ? (
        <div className="space-y-2" role="status" aria-live="polite">
          <p className="text-sm text-muted-foreground">{t('setup.import.scanning')}</p>
          {[0, 1, 2].map((row) => (
            <div key={row} className="h-14 animate-pulse rounded-xl border border-border bg-muted/60" />
          ))}
        </div>
      ) : providers.length === 0 ? (
        <EmptyState
          compact
          icon={<Server />}
          title={t('setup.import.no_providers')}
          description={t('setup.import.no_providers_hint')}
        />
      ) : total === 0 ? (
        <EmptyState
          compact
          icon={<CheckCircle2 />}
          title={t('setup.import.none_found')}
          description={t('setup.import.none_found_hint')}
          action={
            <Button variant="outline" size="sm" onClick={onRetry} leftIcon={<RefreshCw />}>
              {t('setup.import.retry')}
            </Button>
          }
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-muted/40 px-4 py-3">
            <Checkbox
              checked={allSelected}
              indeterminate={selected > 0 && !allSelected}
              onChange={() => (allSelected ? onDeselectAll() : onSelectAll())}
              label={
                <span className="text-sm font-medium text-foreground">
                  {allSelected ? t('setup.import.deselect_all') : t('setup.import.select_all')}
                </span>
              }
            />
            <span className="nums text-xs text-muted-foreground">
              {t('setup.import.found', { count: total })}
              {selected > 0 && <> · {t('setup.import.selected_count', { count: formatNumber(selected) })}</>}
            </span>
          </div>

          <ul className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {importableServices.map((svc, idx) => {
              const FallbackIcon = fallbackIconByType[svc.type] || Server;
              const tone = toneClasses(KIND_TONE[svc.kind]);
              const rowId = `vx-import-${idx}`;
              return (
                <li key={`${svc.kind}-${svc.source}-${svc.name}-${idx}`}>
                  <label
                    htmlFor={rowId}
                    className={cn(
                      'flex cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 transition-colors',
                      svc.selected
                        ? 'border-primary/30 bg-primary/5'
                        : 'border-border bg-background hover:border-primary/20',
                    )}
                  >
                    <Checkbox id={rowId} checked={Boolean(svc.selected)} onChange={() => onToggle(idx)} />
                    <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg border', tone.bg, tone.text, tone.border)}>
                      <ProviderLogo
                        type={svc.type}
                        className="h-4 w-4"
                        fallback={<FallbackIcon className="h-4 w-4" />}
                      />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">{svc.domain || svc.name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {svc.source}
                        {svc.target ? ` → ${svc.target}` : ''}
                      </span>
                    </span>
                    <Badge size="sm" tone={KIND_TONE[svc.kind]} className="shrink-0">
                      {svc.kind === 'dns' ? <Globe aria-hidden="true" className="h-3 w-3" /> : null}
                      {t(`setup.import.kind_${svc.kind}`)}
                    </Badge>
                  </label>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {hasRows && <p className="border-t border-border pt-4 text-xs text-muted-foreground">{t('setup.import.footer_hint')}</p>}
    </SetupStepShell>
  );
}
