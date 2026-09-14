/**
 * How many days a certificate has left, and what the page is allowed to say when nobody knows.
 *
 * Three different numbers can arrive on one row and they are not equally trustworthy. The
 * backend's `days_remaining` is null exactly when it had no date to count from, so it can be
 * read on sight. The provider's own `remaining_days` cannot: Zoraxy states `-1` for a
 * certificate whose `ExpireDate` it could not read, and the same -1 for one that expired
 * yesterday. And `expires_on` is `""` exactly when the date would not parse, which is the one
 * thing on the row that can tell those two apart.
 *
 * Reading the count without the date drew the unreadable certificate -- usually Zoraxy's
 * fallback one -- in red as expired one day ago, in the same row whose expiry column said it
 * had no expiry date at all, while the Dashboard card and the sidebar badge counted the same
 * row as fine. Every case below is about which of the three numbers wins.
 */

import { describe, expect, it } from 'vitest';
import type { CertificateRow } from '@/types/api';
import {
  CRITICAL_DAYS,
  WARN_DAYS,
  certBucket,
  certDays,
  certDomains,
  certExpiry,
  certLabel,
  certSourceNames,
  certificateUrgency,
  countBuckets,
  isWildcard,
  matchesSearch,
  providerConsoleUrl,
  resolveProviderFilter,
  sortCertificates,
  toCertFilter,
} from './certificates';

/** A fixed clock: these tests are about arithmetic, not about when they are run. */
const NOW = Date.UTC(2026, 0, 1, 0, 0, 0);
const DAY = 86_400_000;

function row(over: Partial<CertificateRow> = {}): CertificateRow {
  return {
    id: 'app.example.com',
    provider_id: 1,
    provider_name: 'zoraxy',
    nice_name: 'app.example.com',
    domains: ['app.example.com'],
    domain_names: ['app.example.com'],
    expires_on: '',
    ...over,
  };
}

describe('certDays picks which of the three numbers on a row it may believe', () => {
  it('reads the backend count on sight, because null is how it says it had no date', () => {
    expect(certDays(row({ days_remaining: 42, expires_on: '2026-02-12T00:00:00Z' }), NOW)).toBe(42);
  });

  it('keeps a backend count of zero, which expires today rather than says nothing', () => {
    expect(certDays(row({ days_remaining: 0, expires_on: '2026-01-01T06:00:00Z' }), NOW)).toBe(0);
  });

  it('refuses a provider count with no date behind it, whatever the number says', () => {
    // Zoraxy's unreadable certificate as `/expiry` serves it: the backend could not count,
    // so it says null, and -1 is all the provider has to offer.
    const cert = row({ days_remaining: null, remaining_days: -1, expires_on: '', expiry_date_raw: null });
    expect(certDays(cert, NOW)).toBeNull();
    expect(certBucket(certDays(cert, NOW))).toBe('unknown');
  });

  it('refuses it on the fallback route too, where there is no backend count at all', () => {
    // `GET /api/certificates` does no arithmetic, so `days_remaining` is absent rather than
    // null and the provider's -1 is the only number on the row.
    expect(certDays(row({ remaining_days: -1, expires_on: '' }), NOW)).toBeNull();
  });

  it('keeps a provider count of -1 that does come with a date, since the date is the guard', () => {
    const cert = row({ remaining_days: -1, expires_on: '2025-12-31T12:00:00Z' });
    expect(certDays(cert, NOW)).toBe(-1);
  });

  it('prefers the provider count to parsing the date itself when both are there', () => {
    expect(certDays(row({ remaining_days: 120, expires_on: '2026-02-12T00:00:00Z' }), NOW)).toBe(120);
  });

  it('counts from the date when that is all the row has, reading it as UTC', () => {
    // No `Z` on the fallback route's timestamp; `new Date()` would read it as local time and
    // land a day either side of Greenwich.
    expect(certDays(row({ expires_on: '2026-01-11 00:00:00' }), NOW)).toBe(10);
    expect(certDays(row({ expires_on: '2025-12-22 00:00:00' }), NOW)).toBe(-10);
  });

  it('says nothing for a date it cannot read, rather than a number it made up', () => {
    expect(certDays(row({ expires_on: 'Unknown' }), NOW)).toBeNull();
  });
});

describe('certBucket puts every count in exactly one bucket', () => {
  it('has no bucket for a certificate nobody can date', () => {
    expect(certBucket(null)).toBe('unknown');
  });

  it('splits on the two thresholds the page shows and names', () => {
    expect(certBucket(-1)).toBe('expired');
    expect(certBucket(0)).toBe('critical');
    expect(certBucket(CRITICAL_DAYS)).toBe('critical');
    expect(certBucket(CRITICAL_DAYS + 1)).toBe('expiring');
    expect(certBucket(WARN_DAYS)).toBe('expiring');
    expect(certBucket(WARN_DAYS + 1)).toBe('valid');
  });

  it('takes the threshold the provider route stated rather than the built-in one', () => {
    expect(certBucket(45, 60)).toBe('expiring');
    expect(certBucket(45)).toBe('valid');
  });
});

describe('countBuckets and sortCertificates read the same counts the table does', () => {
  const CERTS = [
    row({ id: 'unknown', remaining_days: -1, expires_on: '' }),
    row({ id: 'expired', expires_on: '2025-12-01T00:00:00Z' }),
    row({ id: 'valid', expires_on: '2027-01-01T00:00:00Z' }),
    row({ id: 'critical', expires_on: '2026-01-04T00:00:00Z' }),
  ];

  it('counts the undated certificate as unknown and not as one expired yesterday', () => {
    const counts = countBuckets(CERTS, NOW);
    expect(counts.unknown).toBe(1);
    expect(counts.expired).toBe(1);
    expect(counts.critical).toBe(1);
    expect(counts.valid).toBe(1);
    expect(counts.all).toBe(4);
  });

  it('adds up, so the five buckets always account for the whole table', () => {
    const counts = countBuckets(CERTS, NOW);
    const parts = counts.expired + counts.critical + counts.expiring + counts.valid + counts.unknown;
    expect(parts).toBe(counts.all);
  });

  it('sorts worst first and leaves the undated one last, not at the top in red', () => {
    expect(sortCertificates(CERTS, NOW).map((c) => c.id)).toEqual([
      'expired',
      'critical',
      'valid',
      'unknown',
    ]);
  });
});

describe('what the row is called, and on which of the two spellings', () => {
  it('reads the back-filled spelling, and the provider one when it is missing', () => {
    expect(certDomains(row({ domain_names: ['a.example.com'], domains: ['b.example.com'] }))).toEqual(
      ['a.example.com'],
    );
    expect(certDomains(row({ domain_names: undefined as unknown as string[] }))).toEqual(
      ['app.example.com'],
    );
  });

  it('drops whatever the appliance put in the list that is not a hostname', () => {
    const ragged = [null, '', 'keep.example.com', 7] as unknown as string[];
    expect(certDomains(row({ domain_names: ragged }))).toEqual(['keep.example.com']);
  });

  it('answers with an empty list rather than throwing when the list is not one', () => {
    const notAList = { 0: 'app.example.com' } as unknown as string[];
    expect(certDomains(row({ domain_names: notAList, domains: notAList }))).toEqual([]);
  });

  it('labels the row by its first host, falling back to the name of the file', () => {
    expect(certLabel(row({ domain_names: ['first.example.com', 'second.example.com'] }))).toBe(
      'first.example.com',
    );
    expect(certLabel(row({ domain_names: [], domains: [], nice_name: 'fallback' }))).toBe('fallback');
    expect(certLabel(row({ domain_names: [], domains: [], nice_name: '   ' }))).toBeNull();
  });

  it('calls a certificate wild only when a host of its own starts with a star', () => {
    expect(isWildcard(row({ domain_names: ['*.example.com'] }))).toBe(true);
    expect(isWildcard(row({ domain_names: ['star.example.com'] }))).toBe(false);
    expect(isWildcard(row({ domain_names: ['app.*.example.com'] }))).toBe(false);
  });
});

describe('certExpiry answers with a date only when there is one to answer with', () => {
  it('prefers the parsed date and falls back to whatever the row carried raw', () => {
    expect(certExpiry(row({ expires_on: '2027-01-01T00:00:00Z' }))).toBe('2027-01-01T00:00:00Z');
    expect(certExpiry(row({ expires_on: '', expiry_date_raw: '2027-01-01 00:00:00' }))).toBe(
      '2027-01-01 00:00:00',
    );
  });

  it('says nothing for the blank the backend writes when it could not read one', () => {
    expect(certExpiry(row({ expires_on: '', expiry_date_raw: null }))).toBeNull();
    expect(certExpiry(row({ expires_on: '   ' }))).toBeNull();
  });
});

describe('the search box, the filter in the address bar, and the console link', () => {
  it('matches a host, the file name or the provider, and everything on an empty needle', () => {
    const cert = row({ nice_name: 'npm-14', provider_name: 'Nginx Proxy Manager' });
    expect(matchesSearch(cert, '')).toBe(true);
    expect(matchesSearch(cert, 'app.example')).toBe(true);
    expect(matchesSearch(cert, 'npm-14')).toBe(true);
    expect(matchesSearch(cert, 'nginx')).toBe(true);
    expect(matchesSearch(cert, 'zoraxy')).toBe(false);
  });

  it('lowers and trims the needle itself, the way its namesake does for services', () => {
    const cert = row({ nice_name: 'npm-14', provider_name: 'Nginx Proxy Manager' });
    expect(matchesSearch(cert, 'APP.EXAMPLE')).toBe(true);
    expect(matchesSearch(cert, '  Nginx  ')).toBe(true);
    expect(matchesSearch(cert, '   ')).toBe(true);
  });

  it('keeps only a status the page can actually draw, so a stale link lands on all', () => {
    for (const filter of ['all', 'expired', 'critical', 'expiring', 'valid', 'unknown']) {
      expect(toCertFilter(filter)).toBe(filter);
    }
    expect(toCertFilter('soon')).toBe('all');
    expect(toCertFilter(null)).toBe('all');
    expect(toCertFilter(undefined)).toBe('all');
  });

  it('links into the certificate store for NPM and to the console root otherwise', () => {
    const npm = { id: 1, name: 'npm', type: 'npm', url: 'http://10.0.0.2:81/' };
    expect(providerConsoleUrl(npm)).toBe('http://10.0.0.2:81/nginx/certificates');
    expect(providerConsoleUrl({ ...npm, type: 'NPM' })).toBe('http://10.0.0.2:81/nginx/certificates');
    expect(providerConsoleUrl({ ...npm, type: 'zoraxy' })).toBe('http://10.0.0.2:81');
  });

  it('offers no link at all rather than one the browser cannot follow', () => {
    expect(providerConsoleUrl(undefined)).toBeNull();
    expect(providerConsoleUrl({ id: 1, name: 'z', type: 'zoraxy', url: '' })).toBeNull();
    expect(providerConsoleUrl({ id: 1, name: 'z', type: 'zoraxy', url: '10.0.0.2:8000' })).toBeNull();
  });
});

describe('the date on the row and the bucket it is drawn in agree at the threshold', () => {
  /** The page counts from a date and colours from the count -- both halves, one fixture. */
  const at = (days: number) => new Date(NOW + days * DAY).toISOString();

  it('keeps a date exactly on the warning threshold inside the warning bucket', () => {
    expect(certDays(row({ expires_on: at(WARN_DAYS) }), NOW)).toBe(WARN_DAYS);
    expect(certBucket(certDays(row({ expires_on: at(WARN_DAYS) }), NOW))).toBe('expiring');
    expect(certBucket(certDays(row({ expires_on: at(WARN_DAYS + 1) }), NOW))).toBe('valid');
  });

  it('hands the critical threshold to the red bucket and the next day to the amber one', () => {
    expect(certBucket(certDays(row({ expires_on: at(CRITICAL_DAYS) }), NOW))).toBe('critical');
    expect(certBucket(certDays(row({ expires_on: at(CRITICAL_DAYS + 1) }), NOW))).toBe('expiring');
  });

  it('floors a part-day the way the backend does, so today is still zero and not minus one', () => {
    expect(certDays(row({ expires_on: at(0.5) }), NOW)).toBe(0);
    expect(certBucket(certDays(row({ expires_on: at(0.5) }), NOW))).toBe('critical');
    expect(certDays(row({ expires_on: at(-0.5) }), NOW)).toBe(-1);
  });
});

describe('a filter that stops being one of the choices stops filtering', () => {
  it('keeps the integration the operator chose while it is still offered', () => {
    expect(resolveProviderFilter('2', ['1', '2', '3'])).toBe('2');
  });

  it('falls back to all once the integration it names has stopped answering', () => {
    expect(resolveProviderFilter('2', ['1', '3'])).toBe('all');
    expect(resolveProviderFilter('2', [])).toBe('all');
  });

  it('leaves all alone, including on a page where nothing is offered yet', () => {
    expect(resolveProviderFilter('all', [])).toBe('all');
    expect(resolveProviderFilter('all', ['1'])).toBe('all');
  });
});

describe('naming the certificate stores this call could not read', () => {
  it('keeps them in the order the route named them', () => {
    const raw = [
      { id: 2, name: 'zoraxy-a', type: 'zoraxy' },
      { id: 1, name: 'npm-a', type: 'npm' },
    ];
    expect(certSourceNames(raw)).toEqual(['zoraxy-a', 'npm-a']);
  });

  it('says nothing on a route that carries no such field', () => {
    // The fallback list has none, and the stub every page test runs against answers `{}`.
    expect(certSourceNames(undefined)).toEqual([]);
    expect(certSourceNames(null)).toEqual([]);
    expect(certSourceNames({})).toEqual([]);
    expect(certSourceNames('zoraxy-a')).toEqual([]);
  });

  it('drops an entry with no usable name instead of drawing a blank one', () => {
    const raw = [{ id: 1, name: '   ', type: 'npm' }, { id: 2, type: 'zoraxy' }, null,
                 { id: 3, name: '  zoraxy-a  ', type: 'zoraxy' }];
    expect(certSourceNames(raw)).toEqual(['zoraxy-a']);
  });

  it('names a store once, however many times the route repeats it', () => {
    const raw = [{ id: 1, name: 'npm-a', type: 'npm' }, { id: 1, name: 'npm-a', type: 'npm' }];
    expect(certSourceNames(raw)).toEqual(['npm-a']);
  });
});

describe('certificateUrgency separates a renewal due from a certificate already gone', () => {
  /** Two lapsed weeks ago, one falls due inside the window, one is fine, one has no date. */
  const ESTATE = [
    row({ id: 'gone-a', days_remaining: -47, expires_on: '2025-11-15T00:00:00Z' }),
    row({ id: 'gone-b', days_remaining: -3, expires_on: '2025-12-29T00:00:00Z' }),
    row({ id: 'due', days_remaining: 12, expires_on: '2026-01-13T00:00:00Z' }),
    row({ id: 'fine', days_remaining: 200, expires_on: '2026-07-20T00:00:00Z' }),
    row({ id: 'undated', remaining_days: -1, expires_on: '' }),
  ];

  it('leaves the headline figure exactly as the route counted it', () => {
    //: The route counts `expiring_soon || expired` and the page must not arrive at a second
    //: opinion of the same number. Three: two gone, one due. The undated row is in neither.
    expect(certificateUrgency(ESTATE, 3, NOW).needRenewal).toBe(3);
  });

  it('splits that figure into what has lapsed and what has not, and they add up', () => {
    const urgency = certificateUrgency(ESTATE, 3, NOW);
    expect(urgency.expired).toBe(2);
    expect(urgency.soon).toBe(1);
    expect(urgency.expired + urgency.soon).toBe(urgency.needRenewal);
  });

  it('is danger while anything has lapsed, whatever else the window holds', () => {
    //: An expired certificate is not a renewal due this month: the host is answering with a
    //: broken certificate now. Amber for that is the same colour as a reminder.
    expect(certificateUrgency(ESTATE, 3, NOW).tone).toBe('danger');
    expect(certificateUrgency(ESTATE, 3, NOW).breached).toBe(true);
  });

  it('stays a warning when the window is full but nothing has actually lapsed yet', () => {
    const due = [row({ id: 'due', days_remaining: 12 }), row({ id: 'soon', days_remaining: 2 })];
    const urgency = certificateUrgency(due, 2, NOW);
    expect(urgency.expired).toBe(0);
    expect(urgency.soon).toBe(2);
    expect(urgency.breached).toBe(false);
    expect(urgency.tone).toBe('warning');
  });

  it('says nothing needs renewing rather than drawing an empty badge in a colour', () => {
    const urgency = certificateUrgency([row({ id: 'fine', days_remaining: 200 })], 0, NOW);
    expect(urgency).toMatchObject({ expired: 0, soon: 0, needRenewal: 0, breached: false, tone: 'neutral' });
  });

  it('counts nothing rather than guessing when the certificate list never arrived', () => {
    //: The two readers of this route leave the count at zero when the request failed, so a
    //: missing list is a missing list -- never a zero that reads as an estate in good health.
    expect(certificateUrgency(undefined, 0, NOW)).toMatchObject({ expired: 0, soon: 0, tone: 'neutral' });
  });

  it('never prints a negative half if the two sides of one payload ever disagree', () => {
    expect(certificateUrgency(ESTATE, 1, NOW).soon).toBe(0);
  });

  it('reads the warning window the route stated, not the page default', () => {
    //: `days_remaining` is the backend's own count, so the bucket boundary is the only thing
    //: left for the caller to get wrong -- and `warn_threshold_days` is configurable.
    const estate = [row({ id: 'day-60', days_remaining: 60 })];
    expect(certificateUrgency(estate, 1, NOW, 90).expired).toBe(0);
    expect(certificateUrgency(estate, 1, NOW, 90).soon).toBe(1);
  });
});
