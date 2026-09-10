/**
 * Certificate helpers — shapes, buckets and provider links.
 *
 * `GET /api/certificates/expiry` is the page's source of truth: it returns every
 * certificate each proxy provider exposes, already carrying `days_remaining`,
 * `expiring_soon` and `expired`. `GET /api/certificates` returns the same rows without
 * the expiry maths, and is only used as a fallback — both routes call every provider
 * live, so the page never runs the two at once.
 *
 * The provider payloads are not uniform: NPM numbers its certificates and spells the
 * hosts `domains`, Zoraxy keys them by file name and adds `remaining_days`, and the
 * backend back-fills `domain_names`. Everything below reads whichever spelling arrived.
 */

import type { Tone } from '@/components/ui';
import { parseBackendTimestamp } from '@/lib/format';

export interface CertificateRow {
  /** NPM uses an integer id, Zoraxy the certificate file name. */
  id: number | string;
  provider_id?: number;
  provider_name?: string;
  /** Legacy spelling from `GET /api/certificates` before providers were named. */
  provider?: string;
  nice_name?: string;
  domains?: string[];
  domain_names?: string[];
  expires_on?: string | null;
  expiry_date_raw?: string | null;
  days_remaining?: number | null;
  /** Zoraxy computes its own countdown. */
  remaining_days?: number | null;
  expiring_soon?: boolean;
  expired?: boolean;
  issuer?: string | null;
  use_dns?: boolean;
  is_fallback?: boolean;
}

export interface CertificateExpiryPayload {
  certificates: CertificateRow[];
  total: number;
  expiring_soon_count: number;
  /** `_EXPIRY_WARN_DAYS` in `app/api/certificates.py`; 30 at the time of writing. */
  warn_threshold_days: number;
}

/** Days below which a certificate stops being a reminder and becomes an incident. */
export const CRITICAL_DAYS = 7;
/** Fallback for `warn_threshold_days` when only `GET /api/certificates` answered. */
export const WARN_DAYS = 30;

export const CERT_BUCKETS = ['expired', 'critical', 'expiring', 'valid', 'unknown'] as const;
export type CertBucket = (typeof CERT_BUCKETS)[number];

export type CertFilter = 'all' | CertBucket;
export const CERT_FILTERS = ['all', ...CERT_BUCKETS] as const;

export function toCertFilter(raw: string | null | undefined): CertFilter {
  return (CERT_FILTERS as readonly string[]).includes(raw ?? '') ? (raw as CertFilter) : 'all';
}

export const BUCKET_TONE: Record<CertBucket, Tone> = {
  expired: 'danger',
  critical: 'danger',
  expiring: 'warning',
  valid: 'success',
  unknown: 'neutral',
};

export const BUCKET_LABEL_KEY: Record<CertBucket, string> = {
  expired: 'certificates.status.expired',
  critical: 'certificates.status.critical',
  expiring: 'certificates.status.expiring',
  valid: 'certificates.status.valid',
  unknown: 'certificates.status.unknown',
};

/** Stable across providers: two providers can both hand back a certificate numbered 1. */
export function certKey(cert: CertificateRow): string {
  return `${cert.provider_id ?? cert.provider_name ?? 'p'}:${cert.id}`;
}

export function certDomains(cert: CertificateRow): string[] {
  const domains = cert.domain_names ?? cert.domains ?? [];
  return Array.isArray(domains) ? domains.filter((d): d is string => typeof d === 'string' && d.length > 0) : [];
}

/** The hosts if the provider listed any, otherwise whatever name it gave the file. */
export function certLabel(cert: CertificateRow): string | null {
  const domains = certDomains(cert);
  if (domains.length > 0) return domains[0];
  const nice = (cert.nice_name || '').trim();
  return nice.length > 0 ? nice : null;
}

export function isWildcard(cert: CertificateRow): boolean {
  return certDomains(cert).some((domain) => domain.startsWith('*.'));
}

export function certExpiry(cert: CertificateRow): string | null {
  const raw = cert.expires_on || cert.expiry_date_raw || '';
  return typeof raw === 'string' && raw.trim().length > 0 ? raw : null;
}

/**
 * Days until expiry, negative once past. The backend already did the arithmetic on
 * `/expiry`; the fallback route has not, so the date is parsed here with the same floor
 * Python's `timedelta.days` applies.
 */
export function certDays(cert: CertificateRow, now: number): number | null {
  if (typeof cert.days_remaining === 'number' && Number.isFinite(cert.days_remaining)) return cert.days_remaining;
  if (typeof cert.remaining_days === 'number' && Number.isFinite(cert.remaining_days)) return cert.remaining_days;
  const raw = certExpiry(cert);
  if (!raw) return null;
  // The fallback route serves a naive UTC timestamp (`YYYY-MM-DD HH:MM:SS`, no `Z`), which
  // `new Date()` reads as *local* time -- so a cert expiring at 01:00 UTC could be counted a
  // day late east of Greenwich and a day early west of it. `parseBackendTimestamp` pins UTC.
  const parsed = parseBackendTimestamp(raw);
  if (!parsed) return null;
  const at = parsed.getTime();
  return Math.floor((at - now) / 86_400_000);
}

/** Mutually exclusive, so the five counters always add up to the total. */
export function certBucket(days: number | null, warnDays = WARN_DAYS): CertBucket {
  if (days === null) return 'unknown';
  if (days < 0) return 'expired';
  if (days <= CRITICAL_DAYS) return 'critical';
  if (days <= warnDays) return 'expiring';
  return 'valid';
}

export type CertCounts = Record<CertBucket, number> & { all: number };

export function countBuckets(certs: CertificateRow[], now: number, warnDays = WARN_DAYS): CertCounts {
  const counts: CertCounts = { all: certs.length, expired: 0, critical: 0, expiring: 0, valid: 0, unknown: 0 };
  for (const cert of certs) counts[certBucket(certDays(cert, now), warnDays)] += 1;
  return counts;
}

const BUCKET_ORDER: Record<CertBucket, number> = { expired: 0, critical: 1, expiring: 2, valid: 3, unknown: 4 };

/** Worst first — the same order `/expiry` returns, applied to the fallback route too. */
export function sortCertificates(certs: CertificateRow[], now: number, warnDays = WARN_DAYS): CertificateRow[] {
  return [...certs].sort((a, b) => {
    const daysA = certDays(a, now);
    const daysB = certDays(b, now);
    const rank = BUCKET_ORDER[certBucket(daysA, warnDays)] - BUCKET_ORDER[certBucket(daysB, warnDays)];
    if (rank !== 0) return rank;
    if (daysA === null || daysB === null) return 0;
    return daysA - daysB;
  });
}

export function matchesSearch(cert: CertificateRow, needle: string): boolean {
  if (!needle) return true;
  const haystack = [...certDomains(cert), cert.nice_name || '', cert.provider_name || cert.provider || ''];
  return haystack.some((value) => value.toLowerCase().includes(needle));
}

/** A provider row, reduced to what a certificate link needs. */
export interface CertificateSource {
  id: number;
  name: string;
  type: string;
  url: string;
}

/**
 * Where the operator renews it. NPM keeps its certificate store on a stable admin path;
 * for anything else the console root is the honest answer rather than an invented deep
 * link that would 404.
 */
export function providerConsoleUrl(source: CertificateSource | undefined): string | null {
  const base = (source?.url || '').trim().replace(/\/+$/, '');
  if (!base || !/^https?:\/\//i.test(base)) return null;
  if ((source?.type || '').toLowerCase() === 'npm') return `${base}/nginx/certificates`;
  return base;
}
