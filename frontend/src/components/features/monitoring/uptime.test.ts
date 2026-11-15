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
import type { TranslateFn } from '@/i18n';
import {
  autoCheckCadence,
  fleetLatency,
  parseStatusDetail,
  serviceLatency,
  serviceTarget,
  sortForCards,
  statusCaption,
  type LatencyProbes,
} from './uptime';

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

// What the page says about one probe: the line under the badge, the latency, the card order.

const SERVICE = {
  id: 1,
  subdomain: 'app',
  domain: 'example.test',
  target_ip: '10.0.0.10',
  target_port: 8080,
  expose_mode: 'proxy_dns',
  tunnel_hostname: '',
  enabled: true,
  status: 'ok',
  last_checked: '2026-01-01 10:00:00',
} as Service;

const TUNNEL = { ...SERVICE, id: 2, subdomain: 'notes', expose_mode: 'tunnel', target_ip: 'localhost' } as Service;

/** A translator that shows what it was asked: the key, then the values it was handed. */
const t: TranslateFn = (key, params) => (params ? `${key} ${JSON.stringify(params)}` : key);

describe('parseStatusDetail', () => {
  it('splits the code from the HTTP status the edge answered', () => {
    expect(parseStatusDetail('edge_access:302')).toEqual({ code: 'edge_access', http: 302 });
  });

  it('reads a code alone, the shape a TCP probe stores', () => {
    expect(parseStatusDetail('tcp_open')).toEqual({ code: 'tcp_open', http: null });
  });

  it.each([['empty', ''], ['blank', '  '], ['absent', null], ['not loaded', undefined]])(
    'gives nothing when the detail is %s',
    (_label, raw) => {
      expect(parseStatusDetail(raw)).toBeNull();
    },
  );

  it('drops an HTTP part that is not a status', () => {
    expect(parseStatusDetail('edge_ok:later')).toEqual({ code: 'edge_ok', http: null });
  });
});

describe('statusCaption', () => {
  it('stays silent on a healthy service the backend said nothing about', () => {
    expect(statusCaption(SERVICE, t)).toBeNull();
  });

  it('stays silent on a switched-off service and on one without a port', () => {
    expect(statusCaption({ ...SERVICE, enabled: false, status: 'error' }, t)).toBeNull();
    expect(statusCaption({ ...SERVICE, target_port: 0, status: 'error' }, t)).toBeNull();
  });

  it('names the target a failed TCP check could not reach', () => {
    const caption = statusCaption({ ...SERVICE, status: 'error' }, t);
    expect(caption).toEqual({ text: 'monitoring.error.tcp_unreachable {"target":"10.0.0.10:8080"}', failed: true });
  });

  it('calls a tunnel verdict without detail stale instead of blaming the connector', () => {
    // Production read "check the tunnel connector" on routes that answered fine: the verdict
    // came from a TCP dial to `localhost`, written before the edge probe existed.
    expect(statusCaption({ ...TUNNEL, status: 'error' }, t)).toEqual({ text: 'monitoring.detail.stale', failed: false });
  });

  it('says what the edge answered, with its HTTP status', () => {
    const caption = statusCaption({ ...TUNNEL, status_detail: 'edge_access:302' }, t);
    expect(caption?.failed).toBe(false);
    expect(caption?.text).toMatch(/^monitoring\.detail\.edge_access .* · monitoring\.detail\.http \{"status":302\}$/);
  });

  it('writes a failed edge probe in the danger colour', () => {
    const caption = statusCaption({ ...TUNNEL, status: 'error', status_detail: 'edge_tunnel_down:530' }, t);
    expect(caption?.failed).toBe(true);
    expect(caption?.text).toMatch(/^monitoring\.detail\.edge_tunnel_down /);
  });

  it('says nothing about a code this build does not know', () => {
    // A newer backend may send a code the panel has no sentence for yet: no line beats a raw code.
    expect(statusCaption({ ...SERVICE, status_detail: 'quantum_tunnel' }, t)).toBeNull();
  });
});

// The codes themselves are held to `app/probe.py` by `tests/test_probe.py`, which can import them.

describe('serviceLatency', () => {
  it('prefers this session’s check, which is fresher than the row', () => {
    const probe = { latencyMs: 12, status: 'ok' as const, at: 42, dns: null };
    expect(serviceLatency({ ...SERVICE, latency_ms: 99 }, probe)).toEqual({ ms: 12, at: 42 });
  });

  it('falls back to what the last probe stored, dated by the row', () => {
    expect(serviceLatency({ ...SERVICE, latency_ms: 30 }, undefined)).toEqual({
      ms: 30,
      at: Date.parse('2026-01-01T10:00:00Z'),
    });
  });

  it('gives nothing rather than a zero when nothing was measured', () => {
    expect(serviceLatency(SERVICE, undefined)).toBeNull();
    expect(serviceLatency({ ...SERVICE, latency_ms: null }, undefined)).toBeNull();
    expect(serviceLatency({ ...SERVICE, latency_ms: Number.NaN }, undefined)).toBeNull();
  });
});

describe('the latency card', () => {
  const services = [
    { ...SERVICE, id: 1, latency_ms: 99 },
    { ...SERVICE, id: 2, latency_ms: 40 },
    { ...SERVICE, id: 3, latency_ms: 30 },
    { ...SERVICE, id: 4, latency_ms: 500, enabled: false },
    { ...SERVICE, id: 5 },
  ] as Service[];
  const probes: LatencyProbes = {
    1: { latencyMs: 12, status: 'ok', at: 0, dns: null },
    2: { latencyMs: null, status: 'error', at: 0, dns: null },
  };

  it('averages what each enabled service measured last, and only that', () => {
    // 1 from this session (12, not the stored 99), 3 from the row; 2 failed its last check,
    // 4 is switched off and 5 never measured anything.
    expect(fleetLatency(services, probes)).toEqual({ average: 21, count: 2 });
  });

  it('counts the services the mean is taken over, not the checks that ran', () => {
    // The card read "Measured on 3 services" under 12 ms, two of which measured nothing.
    expect(fleetLatency(services.slice(0, 2), probes).count).toBe(1);
  });

  it('counts nothing while nothing has been measured', () => {
    expect(fleetLatency([services[1], services[4]], probes)).toEqual({ average: null, count: 0 });
  });
});

describe('sortForCards', () => {
  it('puts what is down first and what is switched off last, by name in between', () => {
    const services = [
      { ...SERVICE, id: 1, subdomain: 'b', status: 'ok' },
      { ...SERVICE, id: 2, subdomain: 'z', enabled: false, status: 'error' },
      { ...SERVICE, id: 3, subdomain: 'c', status: 'unknown' },
      { ...SERVICE, id: 4, subdomain: 'y', status: 'error' },
      { ...SERVICE, id: 5, subdomain: 'a', status: 'ok' },
    ] as Service[];
    expect(sortForCards(services).map((service) => service.id)).toEqual([4, 3, 5, 1, 2]);
  });

  it('leaves the list it was given alone', () => {
    const services = [
      { ...SERVICE, id: 1, status: 'ok' },
      { ...SERVICE, id: 2, status: 'error' },
    ] as Service[];
    sortForCards(services);
    expect(services.map((service) => service.id)).toEqual([1, 2]);
  });
});
