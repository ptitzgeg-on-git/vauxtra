/**
 * Provider sync -- the proxy hosts and DNS records an integration already serves, offered as
 * routes Vauxtra can adopt.
 *
 * Split out of `DataTab.tsx`, which had grown to four unrelated screens in one 1 100-line
 * file: nothing here is shared with Docker discovery, backup export or restore beyond the
 * `SettingsSection` frame they all sit in.
 *
 * One row per name, not per record. Measured in production on 2026-09-22: the first scan of a
 * fresh instance offered 91 routes behind "Quick Import (91 new)" and a single confirmation,
 * 59 of them in twelve zones nobody had declared, which one Cloudflare token could read; and
 * `jellyfin.example.org`, which a proxy and two DNS integrations all answer for, showed once,
 * under the proxy, as proxy hosts were listed before DNS records, with nothing saying the
 * others had been folded away. So a row now carries every record found for its name, the zone
 * the import will file it under and whether the operator declared that zone; the table opens
 * on the declared zones; and an import sends the names chosen on screen, never the scan whole.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { RefreshCw, Search, ShieldCheck, ShieldQuestion, Upload, XCircle } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  Checkbox,
  Chip,
  ChipGroup,
  EmptyState,
  InlineAlert,
  Switch,
  buttonVariants,
  useConfirmDialog,
} from '@/components/ui';
import type { ImportResult, Service, SyncResult } from '@/types/api';
import { buildRows, declaredOf, isImportable, payloadFor, trackServices, type SyncRow } from '@/lib/syncRows';
import { SyncRecordsPill, SyncStatusBadge } from '@/components/features/sync/SyncRowBadges';
import { SettingsSection } from '../SettingsSection';

export function SyncSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [declaredOnly, setDeclaredOnly] = useState(true);
  const [zoneFilter, setZoneFilter] = useState<string | null>(null);

  const { data: existingServices = [] } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
  });

  // Read before any scan, so a panel with no declared domain says so before the button is
  // pressed. Reported from production on 2026-09-22: with none declared, four clicks on "Scan
  // providers" produced no request, no error and no message, and the report put it down to the
  // missing domain by elimination. That was not reproduced, and no code path holds the request
  // back; what was missing either way is a word on why a declared domain matters. The scan
  // also answers this question (`declared_domains`), and the rows are marked against that
  // answer; this read is the fresher one for the notice.
  const domainsQuery = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
  });

  const tracked = useMemo(() => trackServices(existingServices), [existingServices]);

  const declaredDomains = useMemo(
    () => declaredOf(Array.isArray(syncResult?.declared_domains) ? syncResult.declared_domains : domainsQuery.data),
    [syncResult, domainsQuery.data],
  );

  const noDomainDeclared = domainsQuery.isSuccess
    ? domainsQuery.data.length === 0
    : Array.isArray(syncResult?.declared_domains) && syncResult.declared_domains.length === 0;

  const syncMutation = useMutation({
    mutationFn: () => api.post<SyncResult>('/services/sync'),
    onSuccess: (data) => {
      setSyncResult(data);
      setSelectedRows(new Set());
      setZoneFilter(null);
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.migration.scan_failed'))),
  });

  const importMutation = useMutation<ImportResult, Error, unknown>({
    mutationFn: (payload) => api.post<ImportResult>('/services/import', payload),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      // The import declares the zone of every service it creates.
      queryClient.invalidateQueries({ queryKey: ['domains'] });
      // The four outcomes are not exclusive, and reading them as if they were lost
      // most of what happened. `imported > 0` painted the whole run green, so a batch
      // that created two services and refused a third reported only the two; and the
      // refusals were read only when nothing at all was imported, so a refusal that
      // shared a run with a success was never spoken. Each outcome now gets its own line.
      //
      // The count carries the line because the reason is not lost with it: every refusal
      // is written to the journal by `_refuse_import`, and `logs` is one of the queries
      // invalidated just above, so Recent activity holds the sentence. Rows set aside on
      // purpose are not failures and get no line each -- the run gets one.
      const skipped = data.skipped?.length ?? 0;
      const refused = data.errors?.length ?? 0;
      if (data.imported > 0) {
        toast.success(t('settings.migration.import_success', { count: data.imported }));
      }
      if (data.linked > 0) {
        toast.success(t('settings.migration.import_linked', { count: data.linked }));
      }
      if (skipped > 0) toast(t('settings.migration.import_skipped', { count: skipped }));
      if (refused > 0) toast.error(t('settings.migration.import_errors', { count: refused }));
      if (!data.imported && !data.linked && !skipped && !refused) {
        toast.success(t('settings.migration.import_nothing'));
      }
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.migration.import_failed'))),
  });

  const { rows: syncRows, nameless } = useMemo(
    () => (syncResult ? buildRows(syncResult, declaredDomains, tracked) : { rows: [] as SyncRow[], nameless: 0 }),
    [syncResult, declaredDomains, tracked],
  );

  // The filter only means something once a domain is declared: with none, it would hide
  // every row, and the notice above the table says what to do instead.
  const filterDeclared = declaredOnly && declaredDomains.length > 0;
  const inDeclared = useMemo(
    () => (filterDeclared ? syncRows.filter((row) => row.declared) : syncRows),
    [filterDeclared, syncRows],
  );
  const undeclaredCount = syncRows.filter((row) => !row.declared).length;

  const zones = useMemo(() => {
    const counts = new Map<string, { zone: string; declared: boolean; count: number }>();
    for (const row of inDeclared) {
      const entry = counts.get(row.zone);
      if (entry) entry.count += 1;
      else counts.set(row.zone, { zone: row.zone, declared: row.declared, count: 1 });
    }
    return [...counts.values()];
  }, [inDeclared]);
  // A zone the declared filter has just hidden is no longer a filter, it is an empty table.
  const activeZone = zoneFilter !== null && zones.some((z) => z.zone === zoneFilter) ? zoneFilter : null;
  const visibleRows = useMemo(
    () => (activeZone === null ? inDeclared : inDeclared.filter((row) => row.zone === activeZone)),
    [activeZone, inDeclared],
  );

  const importableRows = syncRows.filter(isImportable);
  const visibleImportable = visibleRows.filter(isImportable);
  const visibleImportableKeys = visibleImportable.map((row) => row.key);
  // A ticked row the filters now hide is not imported: what is sent is what is on screen.
  const chosenRows = visibleImportable.filter((row) => selectedRows.has(row.key));
  const newCount = visibleRows.filter((row) => row.status === 'new' && row.unimportable === null).length;

  const failedProviders = (syncResult?.providers ?? []).filter((report) => !report.ok);

  const toggleRow = (key: string) =>
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  /** What a confirmation has to say about *rows* before they are written, one line each. */
  const describe = (rows: SyncRow[], opening: string | null) => {
    const created = rows.filter((row) => row.status === 'new').length;
    const linked = rows.filter((row) => row.status === 'link').length;
    const lines: string[] = [];
    if (created > 0 && opening) lines.push(opening);
    if (linked > 0) lines.push(t('settings.migration.quick_import_link', { count: linked }));
    const warnings: string[] = [];
    // A zone and a TLD are the operator's to check only for a name Vauxtra does not hold
    // yet: a `link` row fills in a service that already lives there.
    const undeclared = rows.filter((row) => row.status === 'new' && !row.declared).length;
    const local = rows.filter((row) => row.status === 'new' && row.isLocal).length;
    const conflicts = rows.filter((row) => row.dnsCount >= 2).length;
    if (undeclared > 0) warnings.push(t('settings.migration.quick_import_undeclared_warn', { count: undeclared }));
    if (local > 0) warnings.push(t('settings.migration.quick_import_local_warn', { count: local }));
    if (conflicts > 0) warnings.push(t('settings.migration.quick_import_conflicts', { count: conflicts }));
    return { lines, warnings };
  };

  const quickImport = async () => {
    if (!syncResult || visibleImportable.length === 0) return;
    const created = visibleImportable.filter((row) => row.status === 'new').length;
    const { lines, warnings } = describe(
      visibleImportable,
      t('settings.migration.quick_import_message', { count: created }),
    );
    const hidden = importableRows.length - visibleImportable.length;
    if (hidden > 0) lines.push(t('settings.migration.quick_import_hidden', { count: hidden }));
    const ok = await confirm({
      title: t('settings.migration.quick_import_title'),
      message: [lines.join('\n'), ...warnings].join('\n\n'),
      confirmLabel: t('settings.migration.import'),
      variant: warnings.length > 0 ? 'warning' : 'info',
      // This writes services in bulk. A held Enter must not be the answer.
      initialFocus: 'cancel',
    });
    if (ok) importMutation.mutate(payloadFor(syncResult, visibleImportable));
  };

  const importSelected = async () => {
    if (!syncResult || chosenRows.length === 0) return;
    const created = chosenRows.filter((row) => row.status === 'new').length;
    const { lines, warnings } = describe(
      chosenRows,
      t('settings.migration.import_selected_message', { count: created }),
    );
    const ok = await confirm({
      title: t('settings.migration.import_selected_title'),
      message: [lines.join('\n'), ...warnings].join('\n\n'),
      confirmLabel: t('settings.migration.import'),
      variant: warnings.length > 0 ? 'warning' : 'info',
      initialFocus: 'cancel',
    });
    if (ok) importMutation.mutate(payloadFor(syncResult, chosenRows));
  };

  const allVisibleSelected =
    visibleImportableKeys.length > 0 && visibleImportableKeys.every((key) => selectedRows.has(key));
  const someSelected = chosenRows.length > 0 && !allVisibleSelected;

  return (
    <SettingsSection
      icon={<RefreshCw />}
      title={t('settings.migration.title')}
      description={t('settings.migration.desc')}
      actions={
        <>
          {syncRows.length > 0 && (
            <>
              <Badge tone="neutral">{t('settings.migration.discovered', { count: syncRows.length })}</Badge>
              <Badge tone="primary">{t('settings.migration.new_count', { count: newCount })}</Badge>
            </>
          )}
          <Button
            variant={syncRows.length > 0 ? 'outline' : 'primary'}
            size="sm"
            leftIcon={<RefreshCw className={cn(syncMutation.isPending && 'animate-spin')} />}
            loading={syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
          >
            {syncMutation.isPending ? t('settings.migration.scanning') : t('settings.migration.scan')}
          </Button>
        </>
      }
      footer={
        syncRows.length > 0 ? (
          <>
            {importMutation.data && (
              <p className="mr-auto text-xs text-muted-foreground tabular-nums">
                {t('settings.migration.imported', { count: importMutation.data.imported })}
                {importMutation.data.errors.length > 0 && (
                  <span className="ml-2 text-destructive">
                    {t('settings.migration.errors', { count: importMutation.data.errors.length })}
                  </span>
                )}
              </p>
            )}
            <Button
              variant="secondary"
              leftIcon={<Upload />}
              loading={importMutation.isPending}
              disabled={visibleImportable.length === 0}
              onClick={() => void quickImport()}
            >
              {t('settings.migration.quick_import_cta', { count: visibleImportable.length })}
            </Button>
            <Button
              leftIcon={<Upload />}
              loading={importMutation.isPending}
              disabled={chosenRows.length === 0}
              onClick={() => void importSelected()}
            >
              {t('settings.migration.import_selected', { count: chosenRows.length })}
            </Button>
          </>
        ) : undefined
      }
    >
      {noDomainDeclared && (
        <InlineAlert
          tone="warning"
          title={t('settings.migration.no_domain_title')}
          action={
            <Link to="/settings?tab=dns" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
              {t('settings.migration.no_domain_cta')}
            </Link>
          }
        >
          {t('settings.migration.no_domain_body')}
        </InlineAlert>
      )}

      {failedProviders.length > 0 && (
        <InlineAlert
          tone="warning"
          title={t('settings.migration.provider_failed_title', { count: failedProviders.length })}
          action={
            <Link to="/providers" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
              {t('settings.migration.provider_failed_cta')}
            </Link>
          }
        >
          <ul className="space-y-0.5">
            {failedProviders.map((report) => (
              <li key={report.id} className="break-words">
                {t('settings.migration.provider_failed_line', { name: report.name, detail: report.error || '—' })}
              </li>
            ))}
          </ul>
          <p className="mt-1">{t('settings.migration.provider_failed_hint')}</p>
        </InlineAlert>
      )}

      {(syncResult?.providers?.length ?? 0) > 0 && (
        <ul aria-label={t('settings.migration.providers_label')} className="flex flex-wrap items-center gap-1.5">
          {syncResult?.providers?.map((report) => (
            <li key={report.id}>
              {report.ok ? (
                <Badge
                  size="sm"
                  tone="neutral"
                  title={t('settings.migration.provider_listed', { count: report.count, name: report.name })}
                >
                  {report.name}
                  <span className="font-normal text-muted-foreground">{report.count}</span>
                </Badge>
              ) : (
                <Badge size="sm" tone="danger" icon={<XCircle />} title={report.error || undefined}>
                  {report.name}
                </Badge>
              )}
            </li>
          ))}
        </ul>
      )}

      {syncResult && syncRows.length === 0 && (
        <EmptyState compact icon={<Search />} title={t('settings.migration.no_routes')} />
      )}

      {syncRows.length > 0 && (
        <>
          {(declaredDomains.length > 0 || zones.length >= 2) && (
            <div className="space-y-3">
              {declaredDomains.length > 0 && (
                <Switch
                  size="sm"
                  checked={declaredOnly}
                  onCheckedChange={setDeclaredOnly}
                  label={t('settings.migration.declared_only')}
                  description={
                    filterDeclared
                      ? t('settings.migration.declared_only_hidden', { count: undeclaredCount })
                      : t('settings.migration.declared_only_shown', { count: undeclaredCount })
                  }
                />
              )}
              {zones.length >= 2 && (
                <ChipGroup label={t('settings.migration.zones_label')}>
                  <Chip size="sm" selected={activeZone === null} count={inDeclared.length} onClick={() => setZoneFilter(null)}>
                    {t('settings.migration.zone_all')}
                  </Chip>
                  {zones.map((entry) => (
                    <Chip
                      key={entry.zone}
                      size="sm"
                      selected={activeZone === entry.zone}
                      count={entry.count}
                      icon={entry.declared ? <ShieldCheck /> : <ShieldQuestion />}
                      onClick={() => setZoneFilter(activeZone === entry.zone ? null : entry.zone)}
                    >
                      {entry.zone || t('settings.migration.zone_none')}
                    </Chip>
                  ))}
                </ChipGroup>
              )}
            </div>
          )}

          {visibleRows.length === 0 ? (
            <EmptyState
              compact
              icon={<Search />}
              title={t('settings.migration.no_routes_declared')}
              description={t('settings.migration.declared_only_hidden', { count: undeclaredCount })}
            />
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Checkbox
                  checked={allVisibleSelected}
                  indeterminate={someSelected}
                  disabled={visibleImportableKeys.length === 0}
                  onChange={(e) => setSelectedRows(e.target.checked ? new Set(visibleImportableKeys) : new Set())}
                  label={t('settings.migration.select_all_new')}
                  description={t('settings.migration.selected_count', { count: chosenRows.length })}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={selectedRows.size === 0}
                  onClick={() => setSelectedRows(new Set())}
                >
                  {t('settings.migration.clear_selection')}
                </Button>
              </div>

              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full min-w-160 text-xs">
                  <thead className="border-b border-border bg-muted/50">
                    <tr>
                      <th scope="col" className="w-10 px-3 py-2">
                        <span className="sr-only">{t('settings.migration.col_select')}</span>
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_subdomain')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_domain')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_target')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_provider')}</th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">{t('settings.migration.col_status')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {visibleRows.map((row) => {
                      const selectable = isImportable(row);
                      const checked = selectable && selectedRows.has(row.key);
                      return (
                        <tr
                          key={row.key}
                          className={cn(
                            'align-top transition-colors',
                            selectable ? 'hover:bg-muted/30' : 'opacity-60',
                            checked && 'bg-primary/5',
                          )}
                        >
                          <td className="px-3 py-2">
                            <Checkbox
                              checked={checked}
                              disabled={!selectable}
                              onChange={() => toggleRow(row.key)}
                              aria-label={t('settings.migration.select_route_aria', { host: row.key })}
                            />
                          </td>
                          <td className="px-3 py-2 font-mono font-medium text-foreground">{row.subdomain || '—'}</td>
                          <td className="px-3 py-2 font-mono text-foreground">
                            <span className="inline-flex items-center gap-1">
                              {row.zone || '—'}
                              {!row.declared && row.zone && (
                                <span className="inline-flex text-warning" title={t('settings.migration.zone_undeclared_title')}>
                                  <ShieldQuestion aria-hidden="true" className="h-3.5 w-3.5" />
                                  <span className="sr-only">{t('settings.migration.zone_undeclared_title')}</span>
                                </span>
                              )}
                            </span>
                          </td>
                          <td className="px-3 py-2 font-mono text-muted-foreground">
                            <ul className="space-y-0.5">
                              {row.records.map((record, index) => (
                                <li key={index}>{record.target || '—'}</li>
                              ))}
                            </ul>
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            <ul className="space-y-0.5">
                              {row.records.map((record, index) => (
                                <li key={index}>{record.provider}</li>
                              ))}
                            </ul>
                          </td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap items-center gap-1">
                              <SyncStatusBadge row={row} />
                              <SyncRecordsPill row={row} />
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}

      {nameless > 0 && (
        <p className="text-xs text-muted-foreground">{t('settings.migration.nameless', { count: nameless })}</p>
      )}
      {ConfirmDialogElement}
    </SettingsSection>
  );
}

// ─── Docker discovery ─────────────────────────────────────────────────────────
