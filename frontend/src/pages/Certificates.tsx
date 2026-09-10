import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CircleHelp, Clock, Lock, RefreshCw, ShieldAlert, ShieldCheck, ShieldX } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { translateApiError } from '@/lib/errors';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Chip,
  ChipGroup,
  InlineAlert,
  PageHeader,
  SearchInput,
  Select,
  StatCard,
  buttonVariants,
} from '@/components/ui';
import { CertificateTable } from '@/components/features/certificates/CertificateTable';
import {
  BUCKET_LABEL_KEY,
  CERT_FILTERS,
  WARN_DAYS,
  certBucket,
  certDays,
  countBuckets,
  matchesSearch,
  sortCertificates,
  toCertFilter,
  type CertFilter,
  type CertificateExpiryPayload,
  type CertificateRow,
  type CertificateSource,
} from '@/components/features/certificates/certificates';
import type { Provider } from '@/types/api';

/**
 * Certificates — what is about to expire, and where to go and renew it.
 *
 * Vauxtra issues nothing: every row is read live from a proxy integration that keeps its
 * own certificate store (NPM, Zoraxy). Traefik manages ACME internally and exposes no
 * store, which is why a Traefik-only setup legitimately shows an empty page.
 *
 *  - `GET /api/certificates/expiry`  the rows plus `days_remaining` and the warn threshold
 *  - `GET /api/certificates`         the same rows without the maths — fallback only
 *  - `GET /api/providers`            names, logos and the console URL to renew at
 *  - `GET /api/providers/types`      which integrations expose certificates at all
 *
 * Both certificate routes contact every provider on each call, so the fallback is enabled
 * only once the primary has failed — the page never doubles the load on NPM.
 */

export function Certificates() {
  const t = useT();
  const { formatNumber } = useFormat();
  const [searchParams, setSearchParams] = useSearchParams();

  const statusFilter = toCertFilter(searchParams.get('status'));
  const [providerFilter, setProviderFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  // One instant per tick: every countdown on the page is measured from the same "now".
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const expiryQuery = useQuery<CertificateExpiryPayload>({
    queryKey: ['certificates-expiry'],
    queryFn: () => api.get<CertificateExpiryPayload>('/certificates/expiry'),
  });

  const listQuery = useQuery<CertificateRow[]>({
    queryKey: ['certificates'],
    queryFn: () => api.get<CertificateRow[]>('/certificates'),
    enabled: expiryQuery.isError,
  });

  const providersQuery = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });

  const providerTypesQuery = useProviderTypes();

  const usingFallback = expiryQuery.isError;
  const warnDays = expiryQuery.data?.warn_threshold_days ?? WARN_DAYS;

  const certificates = useMemo(() => {
    const rows = usingFallback ? listQuery.data : expiryQuery.data?.certificates;
    return Array.isArray(rows) ? rows : [];
  }, [usingFallback, listQuery.data, expiryQuery.data]);

  const providers = useMemo(
    () => (Array.isArray(providersQuery.data) ? providersQuery.data : []),
    [providersQuery.data],
  );

  const sources = useMemo(() => {
    const map = new Map<number, CertificateSource>();
    for (const provider of providers) {
      map.set(provider.id, { id: provider.id, name: provider.name, type: provider.type, url: provider.url });
    }
    return map;
  }, [providers]);

  /** Enabled integrations whose type declares the `certificates` capability; `null` until known. */
  const capableProviders = useMemo(() => {
    const meta = providerTypesQuery.data;
    if (!meta) return null;
    const capable = new Set(
      Object.entries(meta)
        .filter(([, entry]) => Boolean(entry?.capabilities?.certificates))
        .map(([type]) => type),
    );
    return providers.filter((provider) => Boolean(provider.enabled) && capable.has(provider.type));
  }, [providerTypesQuery.data, providers]);

  const counts = useMemo(() => countBuckets(certificates, now, warnDays), [certificates, now, warnDays]);

  /** Only the integrations that actually answered with a certificate get a filter entry. */
  const providerOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const cert of certificates) {
      const key = cert.provider_id === undefined ? cert.provider_name || cert.provider || '' : String(cert.provider_id);
      if (!key) continue;
      const name =
        (cert.provider_id !== undefined ? sources.get(cert.provider_id)?.name : undefined) ||
        cert.provider_name ||
        cert.provider ||
        key;
      if (!seen.has(key)) seen.set(key, name);
    }
    return [...seen.entries()].map(([value, label]) => ({ value, label }));
  }, [certificates, sources]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = certificates.filter((cert) => {
      if (statusFilter !== 'all' && certBucket(certDays(cert, now), warnDays) !== statusFilter) return false;
      if (providerFilter !== 'all') {
        const key =
          cert.provider_id === undefined ? cert.provider_name || cert.provider || '' : String(cert.provider_id);
        if (key !== providerFilter) return false;
      }
      return matchesSearch(cert, needle);
    });
    return sortCertificates(rows, now, warnDays);
  }, [certificates, statusFilter, providerFilter, search, now, warnDays]);

  const setStatusFilter = (next: CertFilter) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'all') params.delete('status');
    else params.set('status', next);
    setSearchParams(params, { replace: true });
  };

  const clearFilters = () => {
    setProviderFilter('all');
    setSearch('');
    setStatusFilter('all');
  };

  /**
   * One provider round-trip at a time: the flat list is only re-read if the expiry route
   * failed again, so a refresh never asks NPM for its certificates twice.
   */
  const refresh = async () => {
    const result = await expiryQuery.refetch();
    if (result.isError) await listQuery.refetch();
    void providersQuery.refetch();
  };

  const loading = usingFallback ? listQuery.isPending : expiryQuery.isPending;
  const refreshing = expiryQuery.isFetching || listQuery.isFetching;
  // Only a real dead end: the expiry route failed *and* the flat list could not stand in.
  const failed = expiryQuery.isError && listQuery.isError;
  const filtersActive = statusFilter !== 'all' || providerFilter !== 'all' || search.trim().length > 0;

  const emptyState = (() => {
    if (failed) {
      return {
        title: t('certificates.load_failed'),
        description: t('certificates.load_failed_hint'),
        action: (
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            {t('common.retry')}
          </Button>
        ),
      };
    }
    if (filtersActive && certificates.length > 0) {
      return {
        title: t('certificates.empty.filtered'),
        description: t('certificates.empty.filtered_hint'),
        action: (
          <Button variant="outline" size="sm" onClick={clearFilters}>
            {t('certificates.empty.clear_filters')}
          </Button>
        ),
      };
    }
    if (capableProviders !== null && capableProviders.length === 0) {
      return {
        title: t('certificates.empty.no_integration'),
        description: t('certificates.empty.no_integration_hint'),
        action: (
          <Link to="/providers" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            {t('certificates.empty.open_providers')}
          </Link>
        ),
      };
    }
    return {
      title: t('certificates.empty.none'),
      description: t('certificates.empty.none_hint', {
        providers: (capableProviders ?? []).map((provider) => provider.name).join(', '),
      }),
      action: (
        <Link to="/providers" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
          {t('certificates.empty.open_providers')}
        </Link>
      ),
    };
  })();

  return (
    <div className="mx-auto max-w-7xl space-y-6 pb-8 duration-200 animate-in fade-in">
      <PageHeader
        eyebrow={t('nav.group.operations')}
        title={t('nav.certificates')}
        description={t('certificates.page_description')}
        icon={<Lock />}
        meta={
          <span className="text-xs text-muted-foreground">
            {t('certificates.meta', { count: formatNumber(certificates.length), days: warnDays })}
          </span>
        }
        actions={
          <Button variant="outline" leftIcon={<RefreshCw />} loading={refreshing} onClick={() => void refresh()}>
            {t('certificates.refresh')}
          </Button>
        }
      />

      {failed && (
        <InlineAlert
          tone="danger"
          title={t('certificates.load_failed')}
          action={
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              {t('common.retry')}
            </Button>
          }
        >
          {translateApiError(expiryQuery.error, t, t('certificates.load_failed_hint'))}
        </InlineAlert>
      )}

      {usingFallback && !failed && (
        <InlineAlert tone="warning" title={t('certificates.fallback_notice')}>
          {t('certificates.fallback_notice_hint')}
        </InlineAlert>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label={t('certificates.stat.valid')}
          value={counts.valid}
          hint={t('certificates.stat.valid_hint', { days: warnDays })}
          icon={<ShieldCheck />}
          tone="success"
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'valid' ? 'all' : 'valid')}
        />
        <StatCard
          label={t('certificates.stat.expiring')}
          value={counts.expiring}
          hint={t('certificates.stat.expiring_hint', { days: warnDays })}
          icon={<Clock />}
          tone={counts.expiring > 0 ? 'warning' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'expiring' ? 'all' : 'expiring')}
        />
        <StatCard
          label={t('certificates.stat.critical')}
          value={counts.critical}
          hint={t('certificates.stat.critical_hint')}
          icon={<ShieldAlert />}
          tone={counts.critical > 0 ? 'danger' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'critical' ? 'all' : 'critical')}
        />
        <StatCard
          label={t('certificates.stat.expired')}
          value={counts.expired}
          hint={t('certificates.stat.expired_hint')}
          icon={<ShieldX />}
          tone={counts.expired > 0 ? 'danger' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'expired' ? 'all' : 'expired')}
        />
        <StatCard
          label={t('certificates.stat.unknown')}
          value={counts.unknown}
          hint={t('certificates.stat.unknown_hint')}
          icon={<CircleHelp />}
          tone={counts.unknown > 0 ? 'warning' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'unknown' ? 'all' : 'unknown')}
        />
      </div>

      <Card>
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle>{t('certificates.list_title')}</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              {providerOptions.length > 1 && (
                <Select
                  size="sm"
                  aria-label={t('certificates.provider_filter')}
                  value={providerFilter}
                  onChange={(event) => setProviderFilter(event.target.value)}
                  wrapperClassName="w-auto"
                >
                  <option value="all">{t('certificates.all_providers')}</option>
                  {providerOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              )}
              <SearchInput
                size="sm"
                value={search}
                onChange={setSearch}
                placeholder={t('certificates.search_placeholder')}
                aria-label={t('certificates.search_placeholder')}
                wrapperClassName="w-full max-w-xs"
              />
            </div>
          </div>
          <ChipGroup label={t('certificates.filters_label')}>
            {CERT_FILTERS.map((key) => (
              <Chip
                key={key}
                selected={statusFilter === key}
                count={key === 'all' ? counts.all : counts[key]}
                onClick={() => setStatusFilter(key)}
              >
                {key === 'all' ? t('certificates.filter.all') : t(BUCKET_LABEL_KEY[key])}
              </Chip>
            ))}
          </ChipGroup>
        </CardHeader>

        <CardContent className="p-0">
          <CertificateTable
            certificates={filtered}
            sources={sources}
            warnDays={warnDays}
            now={now}
            loading={loading}
            empty={emptyState}
          />
        </CardContent>
      </Card>
    </div>
  );
}
