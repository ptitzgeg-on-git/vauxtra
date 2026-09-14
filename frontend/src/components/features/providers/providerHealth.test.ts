/**
 * A provider you switch off is not a provider that is failing.
 *
 * `getHealthScore` used to clamp the score of a disabled provider to 30, which lands in the
 * `error` band, so the card published a red "Failing · 30" badge beside the neutral
 * "Disabled" chip on the same row. The clamp had no other consumer: `getOperationalStatus`
 * and `getProviderSeverity` both answer on `enabled` before they ever look at the score, so
 * painting that badge red was the only thing the line did.
 *
 * It also made the badge unreachable. The Integrations page counts issues and filters on
 * `getProviderSeverity`, which calls the same provider `disabled` -- so the counter said
 * zero, the Issues filter hid the card, and the red badge existed only on the screen that
 * said nothing was wrong.
 *
 * These tests read all three answers for the same provider and require them to agree.
 */

import { describe, expect, it } from 'vitest';

import type { Provider } from '@/types/api';

import {
  getHealthScore,
  getOperationalStatus,
  getProviderSeverity,
  healthTone,
  showsHealthBadge,
  type HealthSignals,
} from './providerHealth';

/** The key itself, so an assertion names the key rather than an English sentence. */
const t = (key: string) => key;

function provider(enabled: boolean): Provider {
  return {
    id: 1,
    name: 'NPM at the lab',
    type: 'npm',
    url: 'http://10.0.0.1:81',
    username: 'admin',
    enabled,
    extra: {},
    created_at: '2026-01-01T00:00:00Z',
  };
}

/** What each signal set says about a provider that is switched on. */
const SIGNALS: Record<string, HealthSignals> = {
  nothing_measured: {},
  answering: { diag: { ok: true, testedAt: 1, validation: { checks: [] } } },
  refusing: { diag: { ok: false, testedAt: 1, health: { ok: false, error: 'connection refused' } } },
};

describe('a provider that is switched off', () => {
  it('is never scored as failing, whatever the last signals said', () => {
    for (const [name, signals] of Object.entries(SIGNALS)) {
      const health = getHealthScore(provider(false), signals, t);
      expect(health.severity, name).not.toBe('error');
      expect(health.severity, name).not.toBe('degraded');
    }
  });

  it('gets the same answer from the chip, the filter and the badge', () => {
    for (const [name, signals] of Object.entries(SIGNALS)) {
      const p = provider(false);
      const health = getHealthScore(p, signals, t);
      expect(getOperationalStatus(p, health).labelKey, name).toBe('providers.status.disabled');
      expect(getOperationalStatus(p, health).tone, name).toBe('neutral');
      expect(getProviderSeverity(p, health), name).toBe('disabled');
      expect(healthTone[health.severity], name).toBe('neutral');
    }
  });

  it('publishes no health verdict on the card at all', () => {
    // The chip already says "Disabled". A second badge would have to describe a reading
    // taken while the switch was on, and the card has no way to say that.
    for (const [name, signals] of Object.entries(SIGNALS)) {
      const p = provider(false);
      expect(showsHealthBadge(p, getHealthScore(p, signals, t)), name).toBe(false);
    }
  });
});

describe('a provider that is switched on', () => {
  it('still reports what it measured', () => {
    const p = provider(true);
    expect(getHealthScore(p, SIGNALS.answering, t).severity).toBe('ok');
    expect(getHealthScore(p, SIGNALS.refusing, t).severity).toBe('error');
    expect(getHealthScore(p, SIGNALS.nothing_measured, t).score).toBe(-1);
  });

  it('shows the badge once there is something to show', () => {
    const p = provider(true);
    expect(showsHealthBadge(p, getHealthScore(p, SIGNALS.answering, t))).toBe(true);
    expect(showsHealthBadge(p, getHealthScore(p, SIGNALS.refusing, t))).toBe(true);
    // Nothing measured yet is not a verdict either, and never was.
    expect(showsHealthBadge(p, getHealthScore(p, SIGNALS.nothing_measured, t))).toBe(false);
  });

  it('is counted by the page exactly when the card calls it an incident', () => {
    // The page reads `getProviderSeverity`; the card reads `getHealthScore`. Whenever one
    // of them says incident, so must the other -- that is the agreement the red badge on a
    // switched-off provider broke.
    const p = provider(true);
    for (const [name, signals] of Object.entries(SIGNALS)) {
      const health = getHealthScore(p, signals, t);
      const cardSaysIncident = health.severity === 'error' || health.severity === 'degraded';
      const pageSaysIncident = ['degraded', 'error'].includes(getProviderSeverity(p, health));
      expect(pageSaysIncident, name).toBe(cardSaysIncident);
    }
  });
});
