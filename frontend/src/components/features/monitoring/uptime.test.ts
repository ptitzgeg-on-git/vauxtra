/**
 * What the Monitoring header is allowed to say about the scheduler.
 *
 * Two pages of the same instance used to contradict each other at the same instant: Settings
 * said "Health checks are off", and Monitoring said "Auto checks: every 5 min". Two separate
 * causes, one line apart:
 *
 *   Number(settings?.check_interval) || 5           `0` is the value that disables the
 *                                                   scheduler, and `||` replaced it with 5
 *   services.some((s) => Boolean(s.last_checked))   that column is written by the manual
 *                                                   checks too, so one click on "Check every
 *                                                   service" made the header announce a
 *                                                   scheduler that was not running
 *
 * The rule is now one function reading one setting, and these are its answers.
 */

import { describe, expect, it } from 'vitest';
import type { Service } from '@/types/api';
import { autoCheckCadence, averageLatency, measuredCount, serviceTarget, type LatencyProbes } from './uptime';

describe('autoCheckCadence', () => {
  it('reads zero as off, which is the whole point', () => {
    // `app/settings.py` ranges check_interval over (0, 1440): "0 disables automatic health
    // checks". The one value that carries a meaning is the one `|| 5` used to swallow.
    expect(autoCheckCadence('0')).toEqual({ state: 'off' });
    expect(autoCheckCadence(0)).toEqual({ state: 'off' });
  });

  it('reads a cadence as a cadence, from either a string or a number', () => {
    // The API answers settings as strings; the settings form sends them back as strings too.
    expect(autoCheckCadence('5')).toEqual({ state: 'every', minutes: 5 });
    expect(autoCheckCadence(15)).toEqual({ state: 'every', minutes: 15 });
    expect(autoCheckCadence('1440')).toEqual({ state: 'every', minutes: 1440 });
  });

  it.each([
    ['not loaded yet', undefined],
    ['absent', null],
    ['blank', ''],
    ['not a number', 'later'],
    ['negative, which the backend never stores', '-5'],
  ])('says nothing rather than something wrong when it is %s', (_label, raw) => {
    expect(autoCheckCadence(raw as string | number | null | undefined)).toEqual({ state: 'unknown' });
  });

  it('never answers a cadence it was not given', () => {
    // The defect was a default appearing out of a falsy value. No input may produce a number
    // that was not in it.
    for (const raw of ['0', 0, '', null, undefined, 'later', '-5', 'NaN']) {
      const cadence = autoCheckCadence(raw as string | number | null | undefined);
      if (cadence.state === 'every') expect(String(cadence.minutes)).toBe(String(raw));
    }
  });
});

// The address and the latency card, for a service without a port: `target_port` 0 is a name
// published in DNS alone, which nothing connects to.

describe('serviceTarget', () => {
  const base = { target_ip: '10.0.0.10', target_port: 8080, expose_mode: 'proxy_dns' } as Service;

  it('writes the address and the port', () => {
    expect(serviceTarget(base)).toBe('10.0.0.10:8080');
  });

  it('writes the address alone for a service without a port', () => {
    // `10.0.0.10:0` named a port nothing listens on.
    expect(serviceTarget({ ...base, target_port: 0 })).toBe('10.0.0.10');
  });
});

describe('the latency card', () => {
  const probes: LatencyProbes = {
    1: { latencyMs: 12, status: 'ok', at: 0, dns: null },
    2: { latencyMs: null, status: 'error', at: 0, dns: null },
    3: { latencyMs: null, status: 'unknown', at: 0, dns: ['203.0.113.9'] },
  };

  it('averages the latencies that were measured, and only those', () => {
    expect(averageLatency(probes)).toBe(12);
  });

  it('counts the services the mean is taken over, not the checks that ran', () => {
    // The card read "Measured on 3 services" under 12 ms, two of which measured nothing.
    expect(measuredCount(probes)).toBe(1);
  });

  it('counts nothing while nothing has been measured', () => {
    expect(measuredCount({ 2: probes[2], 3: probes[3] })).toBe(0);
    expect(averageLatency({ 2: probes[2], 3: probes[3] })).toBeNull();
  });
});
