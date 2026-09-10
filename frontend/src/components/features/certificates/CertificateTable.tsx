import type { ReactNode } from 'react';
import { ExternalLink, ShieldOff } from 'lucide-react';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { EM_DASH } from '@/lib/format';
import { Badge, EmptyState, ProviderLogo, Skeleton, Tooltip, cn } from '@/components/ui';
import {
  BUCKET_LABEL_KEY,
  BUCKET_TONE,
  certBucket,
  certDays,
  certDomains,
  certExpiry,
  certKey,
  certLabel,
  isWildcard,
  providerConsoleUrl,
  type CertificateRow,
  type CertificateSource,
} from './certificates';

/**
 * One row per certificate: the hosts it covers, the integration it came from, the date it
 * runs out and how long that leaves.
 *
 * Vauxtra never issues a certificate, so every row ends in a link back to the console
 * that owns it — that is where a renewal actually happens.
 */

export interface CertificateTableProps {
  certificates: CertificateRow[];
  /** Providers by id, for the logo and the console link. */
  sources: Map<number, CertificateSource>;
  /** `warn_threshold_days` from the backend so the buckets match its own flags. */
  warnDays: number;
  /** Frozen once per render pass so every countdown is measured from the same instant. */
  now: number;
  loading?: boolean;
  empty: { title: string; description: ReactNode; action?: ReactNode };
}

const HEAD_CELL = 'px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground';

export function CertificateTable({
  certificates,
  sources,
  warnDays,
  now,
  loading = false,
  empty,
}: CertificateTableProps) {
  const t = useT();
  const { formatDate, formatDays } = useFormat();

  if (loading) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2, 3, 4].map((row) => (
          <div key={row} className="grid grid-cols-[1fr_auto] items-center gap-4">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-28" />
          </div>
        ))}
      </div>
    );
  }

  if (certificates.length === 0) {
    return <EmptyState icon={<ShieldOff />} title={empty.title} description={empty.description} action={empty.action} />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className={HEAD_CELL}>
              {t('certificates.table.certificate')}
            </th>
            <th scope="col" className={HEAD_CELL}>
              {t('certificates.table.provider')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'text-right')}>
              {t('certificates.table.expires')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'text-right')}>
              {t('certificates.table.remaining')}
            </th>
            <th scope="col" className={cn(HEAD_CELL, 'w-10 text-right')}>
              <span className="sr-only">{t('certificates.table.source')}</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {certificates.map((cert) => {
            const domains = certDomains(cert);
            const label = certLabel(cert);
            const days = certDays(cert, now);
            const bucket = certBucket(days, warnDays);
            const raw = certExpiry(cert);
            const source = cert.provider_id === undefined ? undefined : sources.get(cert.provider_id);
            const consoleUrl = providerConsoleUrl(source);
            const providerName = source?.name || cert.provider_name || cert.provider || null;
            const countdown =
              days === null
                ? EM_DASH
                : days < 0
                  ? t('certificates.expired_ago', { days: formatDays(Math.abs(days)) })
                  : t('certificates.days_left', { days: formatDays(days) });

            return (
              <tr key={certKey(cert)} className="align-middle transition-colors hover:bg-accent/40">
                <td className="px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={cn(
                        'max-w-[320px] truncate font-medium',
                        label ? 'text-foreground' : 'text-muted-foreground',
                      )}
                    >
                      {label ?? t('certificates.no_domains')}
                    </span>
                    {isWildcard(cert) && (
                      <Badge tone="info" size="sm">
                        {t('certificates.wildcard')}
                      </Badge>
                    )}
                    {cert.is_fallback && (
                      <Badge tone="neutral" size="sm">
                        {t('certificates.fallback')}
                      </Badge>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                    {domains.length > 1 && (
                      <Tooltip content={domains.join(', ')}>
                        <span className="font-mono">{t('certificates.more_domains', { count: domains.length - 1 })}</span>
                      </Tooltip>
                    )}
                    {cert.issuer && <span className="truncate">{t('certificates.issuer', { issuer: cert.issuer })}</span>}
                    {cert.use_dns && <span>{t('certificates.dns_challenge')}</span>}
                  </div>
                </td>

                <td className="px-3 py-2.5">
                  <span className="inline-flex items-center gap-2">
                    <ProviderLogo
                      type={source?.type || 'unknown'}
                      className="h-4 w-4 shrink-0 text-muted-foreground"
                    />
                    <span className={cn('truncate', providerName ? 'text-foreground' : 'text-muted-foreground')}>
                      {providerName ?? t('certificates.unknown_provider')}
                    </span>
                  </span>
                </td>

                <td className="px-3 py-2.5 text-right tabular-nums">
                  {raw ? (
                    <span className="text-foreground">{formatDate(raw)}</span>
                  ) : (
                    <span className="text-muted-foreground">{t('certificates.no_expiry')}</span>
                  )}
                </td>

                <td className="px-3 py-2.5 text-right">
                  <Tooltip content={t(BUCKET_LABEL_KEY[bucket])}>
                    <Badge tone={BUCKET_TONE[bucket]} size="sm" dot>
                      {countdown}
                    </Badge>
                  </Tooltip>
                </td>

                <td className="px-3 py-2.5 text-right">
                  {consoleUrl ? (
                    <a
                      href={consoleUrl}
                      target="_blank"
                      rel="noreferrer noopener"
                      title={t('certificates.open_console', { provider: providerName ?? '' })}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                    >
                      <ExternalLink className="h-4 w-4" aria-hidden />
                      <span className="sr-only">{t('certificates.open_console', { provider: providerName ?? '' })}</span>
                    </a>
                  ) : (
                    <span className="sr-only">{t('certificates.no_console')}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
