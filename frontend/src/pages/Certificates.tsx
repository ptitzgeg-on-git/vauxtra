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
  certSourceNames,
  countBuckets,
  matchesSearch,
  resolveProviderFilter,
  sortCertificates,
  toCertFilter,
  type CertFilter,
  type CertificateSource,
} from '@/components/features/certificates/certificates';
import type { Certificate, CertificateExpiryResponse, CertificateRow, Provider } from '@/types/api';

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

  const expiryQuery = useQuery<CertificateExpiryResponse>({
    queryKey: ['certificates-expiry'],
    queryFn: () => api.get<CertificateExpiryResponse>('/certificates/expiry'),
  });

  const listQuery = useQuery<Certificate[]>({
    queryKey: ['certificates'],
    queryFn: () => api.get<Certificate[]>('/certificates'),
    enabled: expiryQuery.isError,
  });

  const providersQuery = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
  });

  const providerTypesQuery = useProviderTypes();

  const usingFallback = expiryQuery.isError;
  const warnDays = expiryQuery.data?.warn_threshold_days ?? WARN_DAYS;

  const certificates = useMemo<CertificateRow[]>(() => {
    const rows = usingFallback ? listQuery.data : expiryQuery.data?.certificates;
    return Array.isArray(rows) ? rows : [];
  }, [usingFallback, listQuery.data, expiryQuery.data]);

  /**
   * The certificate stores this call could not read, named so a partial page cannot pass
   * for a complete one. Primary route only: the fallback list carries no such field, and
   * it already draws its own banner saying the countdowns came from the browser.
   */
  const unreachable = useMemo(
    () => (usingFallback ? [] : certSourceNames(expiryQuery.data?.unreachable)),
    [usingFallback, expiryQuery.data],
  );

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
      const key = String(cert.provider_id);
      if (!key) continue;
      const name = sources.get(cert.provider_id)?.name || cert.provider_name || key;
      if (!seen.has(key)) seen.set(key, name);
    }
    return [...seen.entries()].map(([value, label]) => ({ value, label }));
  }, [certificates, sources]);

  /**
   * The chosen integration, or `all` once it stops being one of the choices. An operator
   * filters to one integration, that integration is removed or stops answering, and the id
   * stays in state: every row is dropped by a filter the select is no longer drawing.
   */
  const activeProviderFilter = useMemo(
    () => resolveProviderFilter(providerFilter, providerOptions.map((option) => option.value)),
    [providerFilter, providerOptions],
  );

  const filtered = useMemo(() => {
    const rows = certificates.filter((cert) => {
      if (statusFilter !== 'all' && certBucket(certDays(cert, now), warnDays) !== statusFilter) return false;
      if (activeProviderFilter !== 'all') {
        if (String(cert.provider_id) !== activeProviderFilter) return false;
      }
      return matchesSearch(cert, search);
    });
    return sortCertificates(rows, now, warnDays);
  }, [certificates, statusFilter, activeProviderFilter, search, now, warnDays]);

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
    //: The capability map too. It is served by Vauxtra rather than by an integration, so
    //: it costs no round-trip out there — and without it the retry below could be offered
    //: for a failure it had no way of clearing.
    void providerTypesQuery.refetch();
  };

  const loading = usingFallback ? listQuery.isPending : expiryQuery.isPending;
  /**
   * An empty table explains itself by naming the integrations behind it, so it may not
   * paint before those two reads are in. The sentence it printed in the meantime was
   * "These integrations were queried and returned nothing: ." — a plural naming nobody, a
   * dangling colon, and a claim that a query happened. A table with rows never waits for
   * them: a row carries its own provider name and `sources` only prettifies it.
   */
  const explainPending =
    certificates.length === 0 && (providersQuery.isFetching || providerTypesQuery.isFetching);
  const refreshing = expiryQuery.isFetching || listQuery.isFetching;
  // Only a real dead end: the expiry route failed *and* the flat list could not stand in.
  const failed = expiryQuery.isError && listQuery.isError;
  const filtersActive =
    statusFilter !== 'all' || activeProviderFilter !== 'all' || search.trim().length > 0;

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
    //: Both rungs below speak about the integrations behind the table, and neither
    //: sentence can be said without having read them. A failed read leaves the list empty,
    //: which is how "No integration exposes a certificate store" came to be a statement
    //: about what is configured, made by a page that had just failed to find out; and a
    //: capability map that never arrived left the count at zero, which is how the other
    //: one came to name nobody. Either way the page says what it knows, which is that it
    //: could not look.
    if (!providersQuery.isSuccess || capableProviders === null) {
      return {
        title: t('certificates.empty.none'),
        description: t('certificates.empty.none_unread_hint'),
        action: (
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            {t('common.retry')}
          </Button>
        ),
      };
    }
    if (capableProviders.length === 0) {
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
        count: capableProviders.length,
        providers: capableProviders.map((provider) => provider.name).join(', '),
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
            {t('certificates.meta', { count: certificates.length, days: formatNumber(warnDays) })}
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

      {unreachable.length > 0 && (
        <InlineAlert
          tone="warning"
          title={t('certificates.unreachable', { count: unreachable.length })}
          action={
            <Link to="/providers" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
              {t('certificates.empty.open_providers')}
            </Link>
          }
        >
          {t('certificates.unreachable_hint', {
            count: unreachable.length,
            providers: unreachable.join(', '),
          })}
        </InlineAlert>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label={t('certificates.stat.valid')}
          value={formatNumber(counts.valid)}
          hint={t('certificates.stat.valid_hint', { days: formatNumber(warnDays) })}
          icon={<ShieldCheck />}
          tone="success"
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'valid' ? 'all' : 'valid')}
        />
        <StatCard
          label={t('certificates.stat.expiring')}
          value={formatNumber(counts.expiring)}
          hint={t('certificates.stat.expiring_hint', { days: formatNumber(warnDays) })}
          icon={<Clock />}
          tone={counts.expiring > 0 ? 'warning' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'expiring' ? 'all' : 'expiring')}
        />
        <StatCard
          label={t('certificates.stat.critical')}
          value={formatNumber(counts.critical)}
          hint={t('certificates.stat.critical_hint')}
          icon={<ShieldAlert />}
          tone={counts.critical > 0 ? 'danger' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'critical' ? 'all' : 'critical')}
        />
        <StatCard
          label={t('certificates.stat.expired')}
          value={formatNumber(counts.expired)}
          hint={t('certificates.stat.expired_hint')}
          icon={<ShieldX />}
          tone={counts.expired > 0 ? 'danger' : 'neutral'}
          loading={loading}
          onClick={() => setStatusFilter(statusFilter === 'expired' ? 'all' : 'expired')}
        />
        <StatCard
          label={t('certificates.stat.unknown')}
          value={formatNumber(counts.unknown)}
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
                  value={activeProviderFilter}
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
            loading={loading || explainPending}
            empty={emptyState}
          />
        </CardContent>
      </Card>
    </div>
  );
}
