/**
 * Last real step: what `POST /api/services/sync` found on the connected providers, offered as
 * a checklist of names, grouped by the zone the import will file each one under.
 *
 * The rows are built by `lib/syncRows.ts`, the same way Settings > Data builds its table. This
 * screen used to list records instead, each name split at its first dot: a name that a proxy
 * and two DNS integrations all answered for showed three times, and ticking all three sent
 * the import three records that it folded into one service and one refusal. A zone apex was
 * offered like any name, and imported: `example.net` became a service `example` under a
 * domain `net`, declared in passing. And every name sat in one flat list behind "Select all".
 * Measured in production on 2026-09-22 from Settings > Data, a first scan through a Cloudflare
 * token that could read zones beyond the declared one found 91 names, 59 of them in twelve
 * domains nobody had declared, and the import declares the zone of every service it creates.
 * So the zones are visible, each one can be ticked or left whole, and the domains an import is
 * about to declare are named before the button that does it.
 *
 * Two reads stand between this screen and its list: the provider list it filters on, and the
 * scan over it. `loadingImportable` and `scanFailed` are the page's verdict on both at once,
 * because the operator has one question -- is this empty because there is nothing to import,
 * or because something failed. The first is true from the moment the wizard lands here, not
 * from the moment a request goes out: those two are a paint apart, and the rung that paint
 * lands on is a green tick reading "No services found to import".
 *
 * A scan can also succeed while one of its integrations failed: the scan answers, and that
 * integration's routes are simply not in it. Such an integration is named above the list, or
 * in place of it, and the green tick is kept for a scan that lost nothing.
 *
 * Provider colours come from `toneClasses`, never from the `provider_color` the API sends --
 * that field carries raw palette classes and would be the only hardcoded colour on the screen.
 */

import { useMemo } from 'react';
import { CheckCircle2, CloudOff, Download, Globe, RefreshCw, Server, ShieldQuestion } from 'lucide-react';
import {
  Badge,
  Button,
  Checkbox,
  EmptyState,
  InlineAlert,
  ProviderLogo,
  cn,
  toneClasses,
} from '@/components/ui';
import { fallbackIconByType } from '@/components/features/providers/providerConstants';
import { SyncRecordsPill, SyncStatusBadge } from '@/components/features/sync/SyncRowBadges';
import { useT } from '@/i18n';
import { isImportable, zonesDeclaredBy, type SyncRow } from '@/lib/syncRows';
import type { SyncProviderReport } from '@/types/api';
import { SetupStepShell } from './SetupStepShell';
import type { ProviderItem } from './types';

interface ImportStepProps {
  providers: ProviderItem[];
  /** The names the scan offers, in `buildRows` order, without the ones already tracked. */
  rows: SyncRow[];
  /** The keys of the rows ticked. */
  selected: ReadonlySet<string>;
  /** The integrations the scan asked and could not read. */
  failedProviders?: SyncProviderReport[];
  /** Records the scan returned without a name, which no row can hold. */
  nameless?: number;
  loadingImportable: boolean;
  /**
   * The list is empty because a read failed, not because there is nothing to import. Either
   * read counts: the scan, or the provider list it runs over -- a scan that could not start
   * did not complete either, and `setup.import.scan_failed_hint` already states the failure
   * in exactly those words.
   */
  scanFailed?: boolean;
  onToggle: (key: string) => void;
  /** Ticks every key in *keys* when *on*, unticks them otherwise. */
  onSelect: (keys: string[], on: boolean) => void;
  onRetry: () => void;
  onImportAndFinish: () => void;
  onBack: () => void;
  importing?: boolean;
}

export function ImportStep({
  providers,
  rows,
  selected,
  failedProviders = [],
  nameless = 0,
  loadingImportable,
  scanFailed = false,
  onToggle,
  onSelect,
  onRetry,
  onImportAndFinish,
  onBack,
  importing = false,
}: ImportStepProps) {
  const t = useT();

  const importable = useMemo(() => rows.filter(isImportable), [rows]);
  // A key the list no longer holds is not a choice: what the button counts is what is on screen.
  const chosen = useMemo(() => importable.filter((row) => selected.has(row.key)), [importable, selected]);
  const allSelected = importable.length > 0 && chosen.length === importable.length;
  const declaring = useMemo(() => zonesDeclaredBy(chosen), [chosen]);
  // Before any domain is declared, every zone is undeclared, and a mark on all of them says
  // nothing; the list of domains the import would declare is what speaks then.
  const anyDeclared = rows.some((row) => row.declared);

  const groups = useMemo(() => {
    const byZone = new Map<string, SyncRow[]>();
    for (const row of rows) {
      const list = byZone.get(row.zone);
      if (list) list.push(row);
      else byZone.set(row.zone, [row]);
    }
    return [...byZone.entries()];
  }, [rows]);

  /** Provider id to type, for a DNS record: the scan sends a type with proxy hosts only. */
  const typeById = useMemo(() => new Map<number, string>(providers.map((p) => [p.id, p.type])), [providers]);

  const settled = !scanFailed && !loadingImportable && providers.length > 0;
  const hasRows = settled && rows.length > 0;

  const failedAlert = failedProviders.length > 0 && (
    <InlineAlert
      tone="warning"
      title={t('settings.migration.provider_failed_title', { count: failedProviders.length })}
      action={
        <Button variant="outline" size="sm" onClick={onRetry} leftIcon={<RefreshCw />}>
          {t('setup.import.retry')}
        </Button>
      }
    >
      <ul className="space-y-0.5">
        {failedProviders.map((report) => (
          <li key={report.id} className="break-words">
            {report.error
              ? t('settings.migration.provider_failed_line', { name: report.name, detail: report.error })
              : report.name}
          </li>
        ))}
      </ul>
      <p className="mt-1">{t('settings.migration.provider_failed_hint')}</p>
    </InlineAlert>
  );

  const zoneLabel = (zone: string) => zone || t('settings.migration.zone_none');

  return (
    <SetupStepShell
      icon={<Download />}
      title={t('setup.import.title')}
      description={t('setup.import.subtitle')}
      onBack={onBack}
      backDisabled={importing}
      primary={{
        label:
          chosen.length > 0 ? t('setup.import.finish_import', { count: chosen.length }) : t('setup.import.finish_skip'),
        onClick: onImportAndFinish,
        loading: importing,
      }}
    >
      {scanFailed ? (
        // This used to land in the "nothing to import" state below -- under a green tick, on
        // the one screen where the next button ends setup. The toast that said otherwise was
        // gone in a few seconds; the tick stayed, and the operator finished a wizard having
        // been told there was nothing to bring in.
        //
        // It outranks the two rungs below it rather than sitting between them. A provider list
        // that could not be read leaves `providers` empty, so "No providers configured." would
        // have answered for it -- a statement about what is configured, made by a screen
        // that had just failed to find out. The busy rung is below it for the same reason: a
        // read that failed never settles, so nothing would ever lift the skeleton off it.
        <InlineAlert
          tone="danger"
          icon={<CloudOff />}
          title={t('setup.import.scan_failed')}
          action={
            <Button variant="outline" size="sm" onClick={onRetry} leftIcon={<RefreshCw />}>
              {t('setup.import.retry')}
            </Button>
          }
        >
          {t('setup.import.scan_failed_hint')}
        </InlineAlert>
      ) : loadingImportable ? (
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
      ) : rows.length === 0 ? (
        // An integration that could not be read has routes this scan never saw: "nothing to
        // import" would be a claim about them too.
        failedAlert || (
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
        )
      ) : (
        <>
          {failedAlert}

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-muted/40 px-4 py-3">
            <Checkbox
              checked={allSelected}
              indeterminate={chosen.length > 0 && !allSelected}
              disabled={importable.length === 0}
              onChange={() => onSelect(importable.map((row) => row.key), !allSelected)}
              label={
                <span className="text-sm font-medium text-foreground">
                  {allSelected ? t('setup.import.deselect_all') : t('setup.import.select_all')}
                </span>
              }
            />
            <span className="nums text-xs text-muted-foreground">
              {t('setup.import.found', { count: importable.length })}
              {chosen.length > 0 && <> · {t('setup.import.selected_count', { count: chosen.length })}</>}
            </span>
          </div>

          <div className="max-h-96 space-y-4 overflow-y-auto pr-1">
            {groups.map(([zone, zoneRows]) => {
              const keys = zoneRows.filter(isImportable).map((row) => row.key);
              const ticked = keys.filter((key) => selected.has(key)).length;
              const name = <span className="font-mono text-sm font-semibold text-foreground">{zoneLabel(zone)}</span>;
              return (
                <section key={zone} aria-label={zoneLabel(zone)} className="space-y-2">
                  <div className="flex items-center gap-2 px-1">
                    {/* With one zone, the box above already does what this one would. */}
                    {groups.length >= 2 ? (
                      <Checkbox
                        checked={keys.length > 0 && ticked === keys.length}
                        indeterminate={ticked > 0 && ticked < keys.length}
                        disabled={keys.length === 0}
                        onChange={() => onSelect(keys, ticked < keys.length)}
                        label={name}
                      />
                    ) : (
                      name
                    )}
                    {anyDeclared && zone && !zoneRows[0].declared && (
                      <span className="inline-flex text-warning" title={t('settings.migration.zone_undeclared_title')}>
                        <ShieldQuestion aria-hidden="true" className="h-3.5 w-3.5" />
                        <span className="sr-only">{t('settings.migration.zone_undeclared_title')}</span>
                      </span>
                    )}
                    <Badge size="sm" tone="neutral" className="nums">
                      {zoneRows.length}
                    </Badge>
                  </div>

                  <ul className="space-y-2">
                    {zoneRows.map((row) => {
                      const selectable = isImportable(row);
                      const checked = selectable && selected.has(row.key);
                      const lead = row.records[0];
                      // The row's icon follows the provider that served it. Defaulting to 'npm'
                      // drew an Nginx Proxy Manager logo on every Traefik or Caddy host.
                      const type =
                        lead?.providerType ||
                        (lead?.providerId !== undefined ? typeById.get(lead.providerId) : undefined) ||
                        (lead?.kind === 'dns' ? 'dns' : 'proxy');
                      const FallbackIcon = fallbackIconByType[type] || Server;
                      const tone = toneClasses(row.proxyCount > 0 ? 'primary' : 'info');
                      const rowId = `vx-import-${row.key}`;
                      return (
                        <li key={row.key}>
                          <label
                            htmlFor={rowId}
                            className={cn(
                              'flex items-start gap-3 rounded-xl border px-4 py-3 transition-colors',
                              selectable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
                              checked
                                ? 'border-primary/30 bg-primary/5'
                                : cn('border-border bg-background', selectable && 'hover:border-primary/20'),
                            )}
                          >
                            <span className="pt-2">
                              <Checkbox
                                id={rowId}
                                checked={checked}
                                disabled={!selectable}
                                onChange={() => onToggle(row.key)}
                              />
                            </span>
                            <span
                              className={cn(
                                'grid h-8 w-8 shrink-0 place-items-center rounded-lg border',
                                tone.bg,
                                tone.text,
                                tone.border,
                              )}
                            >
                              <ProviderLogo type={type} className="h-4 w-4" fallback={<FallbackIcon className="h-4 w-4" />} />
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-foreground">{row.key}</span>
                              {row.records.map((record, index) => (
                                <span key={index} className="block truncate text-xs text-muted-foreground">
                                  {record.provider}
                                  {record.target ? ` → ${record.target}` : ''}
                                </span>
                              ))}
                            </span>
                            <span className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                              {row.proxyCount > 0 && (
                                <Badge size="sm" tone="primary">
                                  {t('setup.import.kind_proxy')}
                                </Badge>
                              )}
                              {row.dnsCount > 0 && (
                                <Badge size="sm" tone="info">
                                  <Globe aria-hidden="true" className="h-3 w-3" />
                                  {t('setup.import.kind_dns')}
                                </Badge>
                              )}
                              <SyncStatusBadge row={row} showNew={false} />
                              <SyncRecordsPill row={row} />
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </div>

          {declaring.length > 0 && (
            <InlineAlert
              tone={declaring.length >= 2 ? 'warning' : 'info'}
              title={t('setup.import.declares_domains', { count: declaring.length })}
            >
              <ul className="flex flex-wrap gap-1.5">
                {declaring.map((zone) => (
                  <li key={zone}>
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">{zone}</code>
                  </li>
                ))}
              </ul>
              <p className="mt-1">{t('setup.import.declares_domains_hint')}</p>
            </InlineAlert>
          )}
        </>
      )}

      {settled && nameless > 0 && (
        <p className="text-xs text-muted-foreground">{t('settings.migration.nameless', { count: nameless })}</p>
      )}

      {hasRows && <p className="border-t border-border pt-4 text-xs text-muted-foreground">{t('setup.import.footer_hint')}</p>}
    </SetupStepShell>
  );
}
