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
 *
 * The same page had a second reading of the same shape, at the other end of the scale. A
 * provider nothing has measured yet scores -1, and `getOperationalStatus` folded that into
 * the branch for a score of 80 or more: no evidence and the best evidence there is were both
 * painted a green "Active". That is every enabled provider between the list arriving and
 * `GET /providers/health` answering, and every enabled provider for good when that request
 * fails -- the page never reads its error. The dashboard does, and it raises "Integration
 * health could not be checked" whose link points at this page.
 * These tests read all three answers for the same provider and require them to agree.
 */

import { describe, expect, it } from 'vitest';
import en from '@/locales/en.json';

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

describe('a provider nothing has measured yet', () => {
  // Not a rare state. `GET /providers/health` covers every enabled provider, so this is the
  // window between the list arriving and that request answering -- and it is where the page
  // stays for good when the request fails, because nothing on the page reads that query's
  // error. The dashboard does: it raises "Integration health could not be checked" and links
  // here.
  const subject = provider(true);
  const health = getHealthScore(subject, SIGNALS.nothing_measured, t);

  it('has no reading to report', () => {
    expect(health.score).toBe(-1);
    expect(health.severity).toBe('unknown');
  });

  it('is not announced as Active', () => {
    // `score < 0` used to share a branch with `score >= 80`: no evidence and the best
    // evidence there is were painted the same green word.
    expect(getOperationalStatus(subject, health).labelKey).not.toBe('providers.status.active');
    expect(getOperationalStatus(subject, health).tone).not.toBe('success');
  });

  it('gets the same answer from the chip, the filter and the badge', () => {
    expect(getOperationalStatus(subject, health).labelKey).toBe('providers.status.unknown');
    expect(getOperationalStatus(subject, health).tone).toBe('neutral');
    expect(getProviderSeverity(subject, health)).toBe('unknown');
    expect(healthTone[health.severity]).toBe('neutral');
    expect(showsHealthBadge(subject, health)).toBe(false);
  });
});

describe('the chip on the card and the counters above it', () => {
  const CASES = [true, false].flatMap((enabled) =>
    Object.entries(SIGNALS).map(([name, signals]) => ({ name: `${name} enabled=${enabled}`, enabled, signals })),
  );

  it('says Active exactly when the page counts the provider healthy', () => {
    // The "Healthy" chip counts `getProviderSeverity(...) === 'healthy'` and its filter hides
    // everything else; the card prints `getOperationalStatus(...).labelKey`. A card reading
    // Active that the Healthy count leaves out is the page contradicting itself in one view.
    for (const { name, enabled, signals } of CASES) {
      const p = provider(enabled);
      const health = getHealthScore(p, signals, t);
      const chipSaysActive = getOperationalStatus(p, health).labelKey === 'providers.status.active';
      const pageCountsHealthy = getProviderSeverity(p, health) === 'healthy';
      expect(chipSaysActive, name).toBe(pageCountsHealthy);
    }
  });

  it('never names a key the locale files do not carry', () => {
    // These keys reach `t()` through a variable, one file away -- `{t(status.labelKey)}`.
    // Both gates over this rule read literal `t()` calls only, until this landed, so a typo
    // here printed the key itself into the chip, in all eight languages, with CI green.
    const keys = Object.keys(en);
    for (const { name, enabled, signals } of CASES) {
      const p = provider(enabled);
      const { labelKey } = getOperationalStatus(p, getHealthScore(p, signals, t));
      expect(keys, `${name} -> ${labelKey}`).toContain(labelKey);
    }
  });
});
