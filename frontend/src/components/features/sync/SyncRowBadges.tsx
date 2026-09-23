/**
 * The two badges a scanned name carries on the screens that import from a scan: what the
 * import will do with it, and whether more than one record answered for it. Settings > Data
 * and the setup wizard draw the same row, so they read these from one place and cannot say
 * two different things about it.
 */

import { AlertTriangle, CheckCircle2, Layers, Link2 } from 'lucide-react';
import { Badge } from '@/components/ui';
import { useT } from '@/i18n';
import { isImportable, type SyncRow } from '@/lib/syncRows';

interface SyncStatusBadgeProps {
  row: SyncRow;
  /**
   * Draw the "new" badge on a plain new row. The wizard leaves it out: on a first run every
   * row it lists is new, and a badge on all of them says nothing.
   */
  showNew?: boolean;
}

export function SyncStatusBadge({ row, showNew = true }: SyncStatusBadgeProps) {
  const t = useT();
  if (row.status === 'exists') {
    return (
      <Badge size="sm" tone="neutral" icon={<CheckCircle2 />}>
        {t('settings.migration.status_tracked')}
      </Badge>
    );
  }
  if (row.unimportable === 'apex') {
    return (
      <Badge size="sm" tone="neutral" title={t('settings.migration.apex_title', { zone: row.zone })}>
        {t('settings.migration.status_apex')}
      </Badge>
    );
  }
  if (row.unimportable === 'no_dot') {
    return (
      <Badge size="sm" tone="neutral" title={t('settings.migration.no_dot_title')}>
        {t('settings.migration.status_no_dot')}
      </Badge>
    );
  }
  if (row.status === 'link') {
    return (
      <Badge size="sm" tone="info" icon={<Link2 />} title={t('settings.migration.link_title')}>
        {t('settings.migration.status_link')}
      </Badge>
    );
  }
  if (row.isLocal) {
    return (
      <Badge size="sm" tone="warning" icon={<AlertTriangle />} title={t('settings.migration.local_tld_title')}>
        {t('settings.migration.status_local')}
      </Badge>
    );
  }
  if (!showNew) return null;
  return (
    <Badge size="sm" tone="primary">
      {t('settings.migration.status_new')}
    </Badge>
  );
}

/** The pill that says a name was found more than once, and what the import will keep. */
export function SyncRecordsPill({ row }: { row: SyncRow }) {
  const t = useT();
  if (row.records.length < 2) return null;
  const providers = new Set(row.records.map((record) => record.provider)).size;
  const label =
    providers >= 2
      ? t('settings.migration.providers_pill', { count: providers })
      : t('settings.migration.records_pill', { count: row.records.length });
  const titles: string[] = [];
  if (isImportable(row)) {
    const firstProxy = row.records.find((record) => record.kind === 'proxy');
    const firstDns = row.records.find((record) => record.kind === 'dns');
    if (row.status === 'new' && row.proxyCount >= 2 && firstProxy) {
      titles.push(t('settings.migration.proxy_conflict_title', { provider: firstProxy.provider }));
    }
    if (row.dnsCount >= 2 && firstDns) {
      titles.push(t('settings.migration.dns_conflict_title', { provider: firstDns.provider }));
    }
    if (row.status === 'new' && row.proxyCount === 1 && row.dnsCount === 1) {
      titles.push(t('settings.migration.pair_title'));
    }
  }
  return (
    <Badge
      size="sm"
      tone={isImportable(row) && row.dnsCount >= 2 ? 'warning' : 'neutral'}
      icon={<Layers />}
      title={titles.join(' ') || undefined}
    >
      {label}
    </Badge>
  );
}
