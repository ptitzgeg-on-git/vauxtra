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
import type {
  Certificate,
  CertificateExpiryResponse,
  CertificateRow,
  UnreachableCertificateSource,
} from '@/types/api';

//: These three used to be declared here instead, each one wider than the route it reads:
//: the row said every key was optional and added an `issuer` and a `provider` that no
//: provider sends, and the payload was a second spelling of `CertificateExpiryResponse`
//: that the Sidebar and the Dashboard already read this same route through. One
//: declaration each now, in `types/api.ts`, re-exported here so nothing that imports them
//: from this module has to move.
export type { Certificate, CertificateRow };
export type { CertificateExpiryResponse as CertificateExpiryPayload };

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

/**
 * The integration filter, reconciled against the integrations that actually answered. The
 * same reconciliation `toCertFilter` does for the status in the address bar, for the same
 * reason: an id naming nothing hides every row, and the control cannot say so -- with no
 * matching `<option>` the select draws blank, and below two integrations it is not drawn
 * at all. A filter nobody can see and nobody can clear is worse than no filter.
 */
export function resolveProviderFilter(raw: string, offered: readonly string[]): string {
  return raw === 'all' || offered.includes(raw) ? raw : 'all';
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
  return `${cert.provider_id}:${cert.id}`;
}

export function certDomains(cert: CertificateRow): string[] {
  //: `domain_names` is back-filled from `domains` on every row, so the fallback is only
  //: there for a provider added later that forgets one of the two spellings. The
  //: `Array.isArray` guard is the same bet: the route hands these straight through from
  //: whatever the appliance answered.
  const domains = cert.domain_names ?? cert.domains;
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
 *
 * `days_remaining` is the backend's own count and is null exactly when it had no date to
 * count from, so it can be read on sight. `remaining_days` is the provider's, and cannot:
 * Zoraxy states `-1` for a certificate whose expiry it could not read and the same -1 for
 * one that expired yesterday. The date is the tiebreaker -- `expires_on` is empty exactly
 * when it would not parse -- so no date means no count, whatever number came with it. That
 * row used to be drawn in red as expired one day ago beside a column saying it had no
 * expiry date at all; it now reads unknown, which is what is known about it.
 */
export function certDays(cert: CertificateRow, now: number): number | null {
  if (typeof cert.days_remaining === 'number' && Number.isFinite(cert.days_remaining)) return cert.days_remaining;
  const raw = certExpiry(cert);
  if (!raw) return null;
  if (typeof cert.remaining_days === 'number' && Number.isFinite(cert.remaining_days)) return cert.remaining_days;
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

/**
 * The search box, read against the hosts, the file name and the integration. The needle is
 * lowered here rather than by the caller: the helper of the same name in
 * `features/services/helpers.ts` lowers its own, and one name under two conventions is a
 * search that silently matches nothing the first time somebody types a capital letter.
 */
export function matchesSearch(cert: CertificateRow, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [...certDomains(cert), cert.nice_name || '', cert.provider_name || ''];
  return haystack.some((value) => value.toLowerCase().includes(needle));
}

/**
 * The names of the certificate stores the route could not read, in the order it named
 * them. Anything that is not a usable name is dropped rather than drawn: the alert exists
 * to tell the operator which integration to go and look at, and a blank entry or a bare
 * `undefined` in that sentence tells them nothing while making the page look broken. The
 * argument is typed `unknown` on purpose -- on the fallback route there is no payload at
 * all, and in the tests every stubbed response field is missing.
 */
export function certSourceNames(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const names: string[] = [];
  for (const entry of raw as UnreachableCertificateSource[]) {
    const name = typeof entry?.name === 'string' ? entry.name.trim() : '';
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
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
