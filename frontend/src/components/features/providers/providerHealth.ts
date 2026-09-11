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
 * and -- in the three providers written earlier -- `API token`, `List zones`, `DNS write`.
 * Both spellings are folded onto one key, so `providers.diag.check.dns_write` covers the
 * snake_case name and the English phrase alike. An unknown name falls through to itself,
 * which is what a build older than the API has to do anyway.
 */
export function checkLabelText(name: string | undefined, t: Translate): string {
  const raw = String(name || '').trim();
  if (!raw) return '';
  const key = `providers.diag.check.${raw.toLowerCase().replace(/\s+/g, '_')}`;
  const label = t(key);
  return label === key ? raw : label;
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
      reason = auto.error || status || t('providers.health.reason.unhealthy');
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

  if (!provider.enabled) score = Math.min(score, 30);
  score = Math.max(0, Math.min(100, score));

  if (score >= 80) return { score, severity: 'ok' };
  if (score >= 50) return { score, severity: 'degraded', reason };
  return { score, severity: 'error', reason };
}

export function getOperationalStatus(provider: Provider, health: HealthScore): OperationalStatus {
  if (!provider.enabled) return { labelKey: 'providers.status.disabled', tone: 'neutral' };
  if (health.score < 0 || health.score >= 80) return { labelKey: 'providers.status.active', tone: 'success' };
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
