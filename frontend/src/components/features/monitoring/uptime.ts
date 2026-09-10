/**
 * Reading the monitoring data the backend actually keeps.
 *
 * `GET /api/services/history` answers a map keyed by service id (as a string) holding the
 * `uptime_events` rows of the **last 24 hours** — `{status, created_at}` and nothing else.
 * The scheduler writes one row per enabled service on every cycle (`check_interval`
 * minutes), not only when the status flips, so the window is a dense series and can be
 * bucketed into a heat strip without carrying a state forward between events.
 *
 * Two things the table has to be honest about:
 *  - there is no latency column in `uptime_events`. Latency exists only in the answer of
 *    `GET /api/services/{sid}/check`, which measures one service on demand.
 *  - `expose_mode === 'tunnel'` services are skipped by both the scheduler and
 *    `POST /api/services/check-all` (TCP against a tunnel target always fails), so they
 *    have no history and no `last_checked` — that is expected, not a fault.
 */

import { parseBackendTimestamp } from '@/lib/format';
import type { Tone } from '@/components/ui';
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

export function serviceTarget(service: Service): string {
  return `${service.target_ip}:${service.target_port}`;
}

export function serviceStatus(service: Service): MonitoringStatus {
  if (!service.enabled) return 'disabled';
  return service.status === 'ok' || service.status === 'error' ? service.status : 'unknown';
}

/** Tunnel endpoints are never TCP-checked; the Cloudflare API is what says they are up. */
export function isTunnelService(service: Service): boolean {
  return service.expose_mode === 'tunnel';
}

// ---------------------------------------------------------------------------
// The 24 h window
// ---------------------------------------------------------------------------

export const UPTIME_WINDOW_MS = 24 * 60 * 60 * 1000;
/** 48 half-hour cells: readable at a table's width, dense enough for a 5 min interval. */
export const UPTIME_BUCKETS = 48;

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
export function countUptime(
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
// On-demand latency
// ---------------------------------------------------------------------------

/** One `GET /api/services/{sid}/check` result, kept for as long as the page is open. */
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

/** Mean of the latencies measured this session, `null` while none has been. */
export function averageLatency(probes: LatencyProbes): number | null {
  const values = Object.values(probes)
    .map((probe) => probe.latencyMs)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}
