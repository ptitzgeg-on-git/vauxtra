/**
 * A service without a port, as the Services page describes it.
 *
 * `target_port` 0 is a name published in DNS alone: nothing forwards traffic to it, so
 * nothing can connect to it either. The import used to give such a route port 80, a port it
 * read nowhere: the page printed it, and a bulk check probed it and counted the route as
 * down. Two of the three services a production run reported down on 2026-09-22 were such
 * routes.
 *
 * `t` is a recorder here: it writes back the key and the parameters it was given, so the
 * assertions read which sentence was chosen and what went into it.
 */

import { describe, expect, it } from 'vitest';
import type { Service } from '@/types/api';
import { bulkCheckSummary, hasNoPort, targetOf } from './helpers';

const t = (key: string, params?: Record<string, string | number>) =>
  params
    ? `${key}(${Object.entries(params)
        .map(([name, value]) => `${name}=${value}`)
        .join(', ')})`
    : key;

function service(extra: Partial<Service>): Service {
  return {
    target_ip: '10.0.0.5',
    target_port: 8443,
    forward_scheme: 'https',
    expose_mode: 'proxy_dns',
    ...extra,
  } as Service;
}

describe('hasNoPort', () => {
  it('reads 0 as no port, whether the API sent a number or a string', () => {
    expect(hasNoPort(service({ target_port: 0 }))).toBe(true);
    expect(hasNoPort(service({ target_port: '0' as unknown as number }))).toBe(true);
  });

  it('counts a tunnel as a tunnel, as `check_all` does: it is left out first, for its own reason', () => {
    expect(hasNoPort(service({ target_port: 0, expose_mode: 'tunnel' }))).toBe(false);
  });

  it('concludes nothing from a port it was not given', () => {
    expect(hasNoPort(service({ target_port: undefined }))).toBe(false);
    expect(hasNoPort(service({}))).toBe(false);
  });
});

describe('targetOf', () => {
  it('writes the whole URL of a route that has a port', () => {
    expect(targetOf(service({}))).toBe('https://10.0.0.5:8443');
  });

  it('writes the address alone when there is no port, never `:0`', () => {
    expect(targetOf(service({ target_port: 0 }))).toBe('10.0.0.5');
  });
});

describe('bulkCheckSummary', () => {
  it('reports a clean run as a success', () => {
    expect(bulkCheckSummary({ ok: 3, failed: 0, untested: 0 }, t)).toEqual({
      message: 'services.bulk.result.checked(count=3)',
      tone: 'success',
    });
  });

  it('keeps the routes without a port out of the failures', () => {
    const { message, tone } = bulkCheckSummary({ ok: 2, failed: 0, untested: 1 }, t);

    expect(tone).toBe('success');
    expect(message).toBe(
      'services.bulk.result.checked_with_untested(' +
        'checked=services.bulk.result.checked(count=2), untested=services.bulk.result.untested(count=1))',
    );
    expect(message).not.toContain('unreachable');
  });

  it('says only what it left untested when it could test nothing', () => {
    expect(bulkCheckSummary({ ok: 0, failed: 0, untested: 2 }, t)).toEqual({
      message: 'services.bulk.result.untested(count=2)',
      tone: 'neutral',
    });
  });

  it('still warns about a route that did not answer, next to one it could not test', () => {
    const { message, tone } = bulkCheckSummary({ ok: 1, failed: 1, untested: 1 }, t);

    expect(tone).toBe('warning');
    expect(message).toContain('services.bulk.result.unreachable(count=1)');
    expect(message).toContain('services.bulk.result.untested(count=1)');
  });
});
