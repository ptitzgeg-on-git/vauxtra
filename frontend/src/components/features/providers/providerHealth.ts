/**
 * Health scoring for the Integrations page.
 *
 * Three signals feed one score: the last manual test / validation (kept in
 * localStorage for `DIAGNOSTIC_TTL_MS`), the tunnel health poll, and the
 * automatic `GET /providers/health` summary. The rules are the ones the page
 * has always used; they only moved here so the card and the page share them.
 */
import type { Tone } from '@/components/ui';
import type { Provider, ProviderHealthStatus, ProviderHealthSummary, ProviderValidationCheck } from '@/types/api';

export const DIAGNOSTIC_TTL_MS = 30 * 60 * 1000;
export const DIAGNOSTICS_STORAGE_KEY = 'vauxtra.providers.manual-diagnostics.v1';

/** `POST /providers/{id}/test` or `/validate` answer, stamped with when it ran. */
export interface ProviderDiagnostics {
  ok?: boolean;
  provider?: string;
  validation?: {
    checks?: ProviderValidationCheck[];
    warnings?: string[];
  };
  health?: { ok?: boolean; status?: string; error?: string };
  /** Not sent by the current backend; rendered only when present. */
  latency_ms?: number;
  version?: string;
  testedAt?: number;
}

export type ProviderSeverity = 'healthy' | 'degraded' | 'error' | 'disabled' | 'unknown';

export interface HealthScore {
  /** -1 when nothing has been measured yet. */
  score: number;
  severity: 'ok' | 'degraded' | 'error' | 'unknown';
  reason?: string;
}

export interface OperationalStatus {
  labelKey: string;
  tone: Tone;
}

export interface HealthSignals {
  diag?: ProviderDiagnostics;
  tunnel?: ProviderHealthStatus;
  auto?: ProviderHealthSummary;
}

type Translate = (key: string, params?: Record<string, string | number>) => string;

/**
 * What a validation check says, in the reader's language. The API sends the English sentence
 * in `detail` and the short code it was written from in `detail_code`; the code wins when the
 * build knows it, and the sentence stands in when the API is newer than the locale files.
 */
export function checkDetailText(check: ProviderValidationCheck, t: Translate): string {
  const fallback = String(check.detail || '');
  if (!check.detail_code) return fallback;
  const key = `providers.diag.detail.${check.detail_code}`;
  const line = t(key, check.detail_params);
  return line === key ? fallback : line;
}

/**
 * What a validation check is called, in the reader's language.
 *
 * The API names its checks for itself, not for a reader: `test_connection`, `zones_access`,
 * and -- in the three providers written before the naming convention settled -- `API token`,
 * `List zones`, `DNS write`. Both spellings are folded onto one key, so
 * `providers.diag.check.dns_write` covers the snake_case name and the English phrase alike.
 * An unknown name falls through to itself, which is what a build older than the API has to
 * do anyway.
 *
 * This is the last word, not the first: `checkDetailText` says what the check found, and the
 * name of the check is only worth printing when there is no detail to print instead.
 */
export function checkLabelText(name: string | undefined, t: Translate): string {
  const raw = String(name || '').trim();
  if (!raw) return '';
  const key = `providers.diag.check.${raw.toLowerCase().replace(/\s+/g, '_')}`;
  const label = t(key);
  return label === key ? raw : label;
}

/**
 * The health word in the reader's language. The API answers `healthy`, `degraded`, `down` or
 * `unknown`; those are wire values, and interpolating one into a translated sentence produced
 * half-English lines like "État : healthy". An unknown word is returned as it came, so a newer
 * API never blanks the line.
 */
export function healthStatusLabel(status: string, t: Translate): string {
  const key = `providers.health.status.${status}`;
  const line = t(key);
  return line === key ? status : line;
}

export function isDiagnosticsFresh(diag: ProviderDiagnostics | undefined, now = Date.now()): boolean {
  const testedAt = Number(diag?.testedAt || 0);
  return testedAt > 0 && now - testedAt <= DIAGNOSTIC_TTL_MS;
}

export function getHealthScore(provider: Provider, signals: HealthSignals, t: Translate): HealthScore {
  const { diag, tunnel, auto } = signals;
  if (!diag && !tunnel && !auto) return { score: -1, severity: 'unknown' };

  let score = 100;
  let reason: string | undefined;

  if (diag) {
    if (!diag.ok) {
      score -= 50;
      reason = diag.health?.error || t('providers.health.reason.connection_failed');
    }
    const checks = diag.validation?.checks || [];
    if (checks.length > 0) {
      const blocking = checks.filter((c) => c.blocking && !c.ok);
      const warnings = checks.filter((c) => !c.blocking && !c.ok).length;
      score -= blocking.length * 25;
      score -= warnings * 5;
      if (blocking.length > 0 && !reason) {
        reason =
          checkDetailText(blocking[0], t) || t('providers.health.reason.blocking_checks', { count: blocking.length });
      }
    }
    if (diag.health && !diag.health.ok) {
      score -= 30;
      reason = reason || diag.health.error || diag.health.status || t('providers.health.reason.health_failed');
    }
  } else if (auto) {
    const status = String(auto.status || '').toLowerCase();
    if (status !== 'healthy') {
      score = 20;
      // `status` is always a non-empty string here, so it used to shadow the translated
      // sentence and put the bare English token `unhealthy` in front of the operator.
      reason = auto.error || t('providers.health.reason.unhealthy');
    }
  }

  if (tunnel) {
    const status = String(tunnel.status || '').toLowerCase();
    if (status === 'healthy') score = Math.max(score, 90);
    else if (status === 'degraded') {
      score = Math.min(score, 60);
      reason = reason || t('providers.health.reason.tunnel_degraded');
    } else if (status === 'down') {
      score = Math.min(score, 20);
      reason = reason || t('providers.health.reason.tunnel_down');
    }
  }

  score = Math.max(0, Math.min(100, score));

  // A provider that is switched off is not a provider that is failing. This used to clamp
  // the score to 30, which lands in the `error` band, and the card published a red
  // "Failing · 30" beside the neutral "Disabled" chip on the same row. Nothing else read
  // the clamped number: `getOperationalStatus` and `getProviderSeverity` both answer on
  // `enabled` before they look at the score, so painting that badge red was the only thing
  // the line did -- while the page's issue counter and its Issues filter, which read the
  // latter, call the same provider `disabled` and leave it out. The red badge was therefore
  // unreachable from every filter on the screen that drew it. Whatever the last signals
  // said, they were gathered while the switch was on; there is no live reading of something
  // that is off, and `unknown` is what this file already calls that.
  if (!provider.enabled) return { score, severity: 'unknown' };

  if (score >= 80) return { score, severity: 'ok' };
  if (score >= 50) return { score, severity: 'degraded', reason };
  return { score, severity: 'error', reason };
}

export function getOperationalStatus(provider: Provider, health: HealthScore): OperationalStatus {
  if (!provider.enabled) return { labelKey: 'providers.status.disabled', tone: 'neutral' };
  // `score < 0` means nothing has been measured, and it used to share this line with
  // `score >= 80`: no evidence and the best evidence there is were painted the same green
  // "Active". That is the state the page is in before `GET /providers/health` answers, and
  // the state it stays in when that request fails, because the page never reads the query's
  // error. Every other reader of the same cache entry already calls it unknown --
  // `getProviderSeverity` returns `'unknown'`, so the "Healthy" count leaves the card out and
  // the "Healthy" filter hides it; the dashboard's glance tile shows an "Unknown" chip; and
  // the dashboard raises "Integration health could not be checked", whose link points here,
  // at the one surface that was saying everything was fine. Neutral rather than the
  // dashboard's amber: this is also the ordinary first-paint window, and an alarm that fires
  // on every load is an alarm nobody reads.
  if (health.score < 0) return { labelKey: 'providers.status.unknown', tone: 'neutral' };
  if (health.score >= 80) return { labelKey: 'providers.status.active', tone: 'success' };
  if (health.score >= 50) return { labelKey: 'providers.status.degraded', tone: 'warning' };
  return { labelKey: 'providers.status.error', tone: 'danger' };
}

export function getProviderSeverity(provider: Provider, health: HealthScore): ProviderSeverity {
  if (!provider.enabled) return 'disabled';
  if (health.score < 0) return 'unknown';
  if (health.score >= 80) return 'healthy';
  if (health.score >= 50) return 'degraded';
  return 'error';
}

/**
 * Whether the card publishes a health verdict beside the operational chip.
 *
 * Two states have none to publish: nothing has been measured yet, which is what a score of
 * -1 means, and the provider is switched off, where the chip already says so and a second
 * badge could only describe a reading taken before the switch moved.
 */
export function showsHealthBadge(provider: Provider, health: HealthScore): boolean {
  return Boolean(provider.enabled) && health.score >= 0;
}

export const healthTone: Record<HealthScore['severity'], Tone> = {
  ok: 'success',
  degraded: 'warning',
  error: 'danger',
  unknown: 'neutral',
};

/** Tunnel `health_status()` statuses to a badge tone. */
export function tunnelTone(status: string | undefined): Tone {
  switch (String(status || '').toLowerCase()) {
    case 'healthy':
      return 'success';
    case 'degraded':
      return 'warning';
    case 'down':
      return 'danger';
    case 'inactive':
      return 'neutral';
    default:
      return 'info';
  }
}

const TUNNEL_STATUSES = new Set(['healthy', 'degraded', 'down', 'inactive', 'unknown']);

/** i18n key for a tunnel status, falling back to `unknown` for anything the backend invents later. */
export function tunnelStatusKey(status: string | undefined): string {
  const key = String(status || '').toLowerCase();
  return `providers.tunnel.status.${TUNNEL_STATUSES.has(key) ? key : 'unknown'}`;
}

const KNOWN_TUNNEL_REASONS = new Set(['missing_account_id', 'missing_tunnel_id']);

/** Translate the two reasons the backend names; anything else is shown as sent. */
export function tunnelReasonLabel(reason: string | undefined, t: (key: string) => string): string {
  if (!reason) return '';
  return KNOWN_TUNNEL_REASONS.has(reason) ? t(`providers.tunnel.reason.${reason}`) : reason;
}
