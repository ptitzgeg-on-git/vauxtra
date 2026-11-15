/**
 * Reading the monitoring data the backend actually keeps.
 *
 * `GET /api/services/history` answers a map keyed by service id (as a string) holding the
 * `uptime_events` rows of the **last 24 hours** — `{status, created_at}` and nothing else.
 * The scheduler writes one row per enabled service on every cycle (`check_interval`
 * minutes), not only when the status flips, so the window is a dense series and can be
 * bucketed into a heat strip without carrying a state forward between events.
 *
 * Three things the table has to be honest about:
 *  - there is no latency column in `uptime_events`. The service row keeps the latency of its
 *    last probe (`latency_ms`), and the two check routes answer a fresher one, with the DNS
 *    answer on top for the per-row check.
 *  - a tunnel route is not dialled: its target is `localhost:<port>` as `cloudflared` sees
 *    it, which the Vauxtra container cannot reach. It is requested by its public name through
 *    the Cloudflare edge instead (`app/probe.py`), and `status_detail` says what came back.
 *  - the services without a port (`target_port` 0, published in DNS alone) are not probed,
 *    for the plain reason that there is nothing to connect to.
 */

import { parseBackendTimestamp } from '@/lib/format';
import type { TranslateFn } from '@/i18n';
import type { Tone } from '@/components/ui';
import { hasNoPort } from '@/components/features/services/helpers';
import type { Service, ServiceHistoryPoint, ServiceHistoryResponse, ServiceStatus } from '@/types/api';

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

/** A service's status as the page shows it: the backend's three, plus "switched off". */
export type MonitoringStatus = ServiceStatus | 'disabled';
export type StatusFilter = MonitoringStatus | 'all';

export const STATUS_FILTERS = ['all', 'ok', 'error', 'unknown', 'disabled'] as const;

/** The `?status=` param, defaulting to `all` for anything unknown. */
export function toStatusFilter(raw: string | null | undefined): StatusFilter {
  return (STATUS_FILTERS as readonly string[]).includes(raw ?? '') ? (raw as StatusFilter) : 'all';
}

export const STATUS_TONE: Record<MonitoringStatus, Tone> = {
  ok: 'success',
  error: 'danger',
  unknown: 'warning',
  disabled: 'neutral',
};

/** Label key per status — the same words the filter chips use. */
export const STATUS_LABEL_KEY: Record<MonitoringStatus, string> = {
  ok: 'monitoring.filter.ok',
  error: 'monitoring.filter.error',
  unknown: 'monitoring.filter.unknown',
  disabled: 'monitoring.filter.disabled',
};

/** Mirrors `_service_public_hostname` in `app/api/services.py`. */
export function serviceHost(service: Service): string {
  if (service.expose_mode === 'tunnel') {
    const host = (service.tunnel_hostname || '').trim().toLowerCase();
    if (host) return host;
  }
  return service.subdomain ? `${service.subdomain}.${service.domain}` : service.domain;
}

/** `ip:port`, or the address alone for a service without a port: there is no `:0` to reach. */
export function serviceTarget(service: Service): string {
  return hasNoPort(service) ? service.target_ip : `${service.target_ip}:${service.target_port}`;
}

export function serviceStatus(service: Service): MonitoringStatus {
  if (!service.enabled) return 'disabled';
  return service.status === 'ok' || service.status === 'error' ? service.status : 'unknown';
}

// ---------------------------------------------------------------------------
// The scheduler's cadence
// ---------------------------------------------------------------------------

/** What the page may say about the scheduler, and nothing more than it can prove. */
export type AutoCheckCadence =
  | { state: 'unknown' }
  | { state: 'off' }
  | { state: 'every'; minutes: number };

/**
 * What `check_interval` says about the scheduler.
 *
 * `0` is the value that turns it off -- `app/settings.py` ranges it over (0, 1440) with the
 * comment "0 disables automatic health checks" -- which is exactly the value a `Number(x) || 5`
 * swallows. The one number carrying a meaning was the one the fallback replaced, so a page
 * whose scheduler was off announced checks every five minutes.
 *
 * The other way this line used to be decided was `services.some((s) => s.last_checked)`. That
 * column is written by the manual checks too (`app/api/services.py`), so clicking "Check every
 * service" once made the header claim a scheduler was running. A trace left by a human is not
 * evidence about a scheduler; the setting is, and it is the only thing read here.
 *
 * Anything unreadable -- settings not loaded yet, an empty string, a value that is not a number
 * -- answers `unknown`, and the page then says nothing at all. Silence is never wrong.
 */
export function autoCheckCadence(raw: string | number | null | undefined): AutoCheckCadence {
  if (raw === null || raw === undefined || raw === '') return { state: 'unknown' };
  const minutes = Number(raw);
  if (!Number.isFinite(minutes) || minutes < 0) return { state: 'unknown' };
  return minutes === 0 ? { state: 'off' } : { state: 'every', minutes };
}

/** Tunnel routes are tested by their public name through Cloudflare, never dialled. */
export function isTunnelService(service: Service): boolean {
  return service.expose_mode === 'tunnel';
}

// ---------------------------------------------------------------------------
// What the last probe saw
// ---------------------------------------------------------------------------

export interface StatusDetail {
  /** One of `DETAIL_CODES` in `app/probe.py`. */
  code: string;
  /** The HTTP status the edge answered, `null` for a TCP probe. */
  http: number | null;
}

/** `edge_access:302` gives `{code: 'edge_access', http: 302}`; empty gives `null`. */
export function parseStatusDetail(raw: string | null | undefined): StatusDetail | null {
  const value = (raw || '').trim();
  if (!value) return null;
  const [code, rest] = value.split(':', 2);
  const http = rest ? Number(rest) : NaN;
  return { code, http: Number.isInteger(http) && http > 0 ? http : null };
}

/**
 * Caption key per probe code. Mirrors `DETAIL_CODES` in `app/probe.py`; `uptime.test.ts`
 * reads that set out of the Python and fails when a code has no key here or in en.json.
 *
 * `tcp_failed` keeps the sentence the table always wrote, with the target in it.
 */
export const DETAIL_KEY: Record<string, string> = {
  tcp_open: 'monitoring.detail.tcp_open',
  tcp_failed: 'monitoring.error.tcp_unreachable',
  no_port: 'monitoring.detail.no_port',
  edge_ok: 'monitoring.detail.edge_ok',
  edge_access: 'monitoring.detail.edge_access',
  edge_tunnel_down: 'monitoring.detail.edge_tunnel_down',
  edge_origin_down: 'monitoring.detail.edge_origin_down',
  edge_origin_error: 'monitoring.detail.edge_origin_error',
  edge_unreachable: 'monitoring.detail.edge_unreachable',
  dns_unresolved: 'monitoring.detail.dns_unresolved',
  dns_not_cloudflare: 'monitoring.detail.dns_not_cloudflare',
  no_hostname: 'monitoring.detail.no_hostname',
};

export interface StatusCaption {
  text: string;
  /** Written in the danger colour: the probe failed. */
  failed: boolean;
}

/**
 * One line under the status badge: what the last probe saw, in words.
 *
 * `null` for a switched-off service, for one without a port (the row has its own caption for
 * that), and for a probe code this build does not know. A tunnel route still in error with no
 * detail at all carries a verdict written before the edge probe existed, by a TCP dial that
 * could never succeed: it is called stale rather than repeated.
 */
export function statusCaption(service: Service, t: TranslateFn): StatusCaption | null {
  const status = serviceStatus(service);
  if (status === 'disabled' || hasNoPort(service)) return null;
  const detail = parseStatusDetail(service.status_detail);
  const failed = status === 'error';
  if (!detail) {
    if (!failed) return null;
    return isTunnelService(service)
      ? { text: t('monitoring.detail.stale'), failed: false }
      : { text: t('monitoring.error.tcp_unreachable', { target: serviceTarget(service) }), failed };
  }
  const key = DETAIL_KEY[detail.code];
  if (!key) return null;
  const text = t(key, { target: serviceTarget(service) });
  return {
    text: detail.http ? `${text} · ${t('monitoring.detail.http', { status: detail.http })}` : text,
    failed,
  };
}

// ---------------------------------------------------------------------------
// The 24 h window
// ---------------------------------------------------------------------------

const UPTIME_WINDOW_MS = 24 * 60 * 60 * 1000;
/** 48 half-hour cells: readable at a table's width, dense enough for a 5 min interval. */
const UPTIME_BUCKETS = 48;

export interface UptimeCounts {
  ok: number;
  error: number;
  unknown: number;
  total: number;
}

export interface UptimeBucket extends UptimeCounts {
  /** Epoch ms of the cell's edges. */
  start: number;
  end: number;
  /** The worst status recorded in the cell; `null` when nothing was recorded. */
  status: ServiceStatus | null;
}

export interface UptimeSummary extends UptimeCounts {
  buckets: UptimeBucket[];
  /** Ratio in `[0, 1]`, `null` when the window holds no event at all. */
  availability: number | null;
  /** Epoch ms of the oldest and newest event kept, `null` when there is none. */
  firstAt: number | null;
  lastAt: number | null;
}

/** `error` beats `unknown` beats `ok`: a cell is as bad as its worst minute. */
const RANK: Record<ServiceStatus, number> = { ok: 0, unknown: 1, error: 2 };

function normalizeStatus(raw: string | null | undefined): ServiceStatus {
  return raw === 'ok' || raw === 'error' ? raw : 'unknown';
}

function emptyCounts(): UptimeCounts {
  return { ok: 0, error: 0, unknown: 0, total: 0 };
}

function tally(counts: UptimeCounts, status: ServiceStatus): void {
  if (status === 'ok') counts.ok += 1;
  else if (status === 'error') counts.error += 1;
  else counts.unknown += 1;
  counts.total += 1;
}

/** How many events of each status the window holds, without building the buckets. */
function countUptime(
  points: ServiceHistoryPoint[] | undefined,
  now: number = Date.now(),
): UptimeCounts {
  const counts = emptyCounts();
  const from = now - UPTIME_WINDOW_MS;
  for (const point of points ?? []) {
    const at = parseBackendTimestamp(point.created_at)?.getTime();
    if (at === undefined || at < from) continue;
    tally(counts, normalizeStatus(point.status));
  }
  return counts;
}

/** The heat strip of one service: `bucketCount` cells over the last 24 h, plus the totals. */
export function summarizeUptime(
  points: ServiceHistoryPoint[] | undefined,
  now: number = Date.now(),
  bucketCount: number = UPTIME_BUCKETS,
): UptimeSummary {
  const size = UPTIME_WINDOW_MS / bucketCount;
  const from = now - UPTIME_WINDOW_MS;
  const buckets: UptimeBucket[] = Array.from({ length: bucketCount }, (_, index) => ({
    ...emptyCounts(),
    start: from + index * size,
    end: from + (index + 1) * size,
    status: null,
  }));

  const totals = emptyCounts();
  let firstAt: number | null = null;
  let lastAt: number | null = null;

  for (const point of points ?? []) {
    const at = parseBackendTimestamp(point.created_at)?.getTime();
    if (at === undefined || at < from) continue;
    const status = normalizeStatus(point.status);
    tally(totals, status);
    if (firstAt === null || at < firstAt) firstAt = at;
    if (lastAt === null || at > lastAt) lastAt = at;

    const index = Math.min(bucketCount - 1, Math.floor((at - from) / size));
    if (index < 0) continue;
    const bucket = buckets[index];
    tally(bucket, status);
    if (bucket.status === null || RANK[status] > RANK[bucket.status]) bucket.status = status;
  }

  return {
    ...totals,
    buckets,
    availability: totals.total ? totals.ok / totals.total : null,
    firstAt,
    lastAt,
  };
}

/** Availability over every service that has history, for the page-level stat. */
export function overallAvailability(
  history: ServiceHistoryResponse | undefined,
  services: Service[],
  now: number = Date.now(),
): number | null {
  let ok = 0;
  let total = 0;
  for (const service of services) {
    const counts = countUptime(history?.[String(service.id)], now);
    ok += counts.ok;
    total += counts.total;
  }
  return total ? ok / total : null;
}

// ---------------------------------------------------------------------------
// Latency
// ---------------------------------------------------------------------------

/** One measurement, from either check route, kept for as long as the page is open. */
export interface LatencyProbe {
  /** Null when the target never answered — the check says `error`, not "0 ms". */
  latencyMs: number | null;
  status: ServiceStatus;
  /** Epoch ms of the measurement. */
  at: number;
  /** What the public hostname resolves to, `null` when the route did not say. */
  dns: string[] | null;
}

export type LatencyProbes = Record<number, LatencyProbe>;

export interface ServiceLatency {
  /** `null` when the last probe did not answer. */
  ms: number | null;
  /** Epoch ms of the measurement, `null` when the row does not say. */
  at: number | null;
}

/**
 * The latency to show for one service: this session's check when there is one, since it also
 * carries the DNS answer, else what the last probe stored on the row. `null` when neither
 * exists, so the cell shows a dash and never a zero pretending to be a measurement.
 */
export function serviceLatency(service: Service, probe: LatencyProbe | undefined): ServiceLatency | null {
  if (probe) return { ms: probe.latencyMs, at: probe.at };
  const stored = service.latency_ms;
  if (typeof stored !== 'number' || !Number.isFinite(stored)) return null;
  return { ms: stored, at: parseBackendTimestamp(service.last_checked)?.getTime() ?? null };
}

/**
 * Mean latency over the enabled services, from `serviceLatency`, and how many it covers.
 *
 * `count` is the services that measured something, never the checks that ran: counting every
 * check once put "measured on 1 service" under a dash, after a check that measured nothing.
 */
export function fleetLatency(services: Service[], probes: LatencyProbes): { average: number | null; count: number } {
  let sum = 0;
  let count = 0;
  for (const service of services) {
    if (!service.enabled) continue;
    const ms = serviceLatency(service, probes[service.id])?.ms;
    if (typeof ms !== 'number' || !Number.isFinite(ms)) continue;
    sum += ms;
    count += 1;
  }
  return { average: count ? sum / count : null, count };
}

// ---------------------------------------------------------------------------
// Card order
// ---------------------------------------------------------------------------

/** Down first, then unknown, then up, switched off last: the cards read like a to-do list. */
const CARD_RANK: Record<MonitoringStatus, number> = { error: 0, unknown: 1, ok: 2, disabled: 3 };

export function sortForCards(services: Service[]): Service[] {
  return [...services].sort(
    (a, b) =>
      CARD_RANK[serviceStatus(a)] - CARD_RANK[serviceStatus(b)] ||
      serviceHost(a).localeCompare(serviceHost(b)),
  );
}
