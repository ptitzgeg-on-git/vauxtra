/**
 * The screens' copy of the import's rules, held to the import's answers.
 *
 * `syncRows.ts` repeats what `import_services` (`app/api/sync.py`) does with a scan, so a row
 * on screen says what a click on it will do. Each case below is a place where a screen used
 * to say something else: a name cut at its first dot rather than at its zone, one name offered
 * once per record, a zone apex offered as if it were a name, and "Quick import" sending back
 * the whole scan, names already tracked included.
 */

import { describe, expect, it } from 'vitest';
import type { Service, SyncDnsRewrite, SyncProxyHost, SyncResult } from '@/types/api';
import {
  NOTHING_TRACKED,
  buildRows,
  declaredOf,
  payloadFor,
  proxyName,
  splitName,
  trackServices,
  zonesDeclaredBy,
} from './syncRows';

function proxy(names: string[], extra: Partial<SyncProxyHost> = {}): SyncProxyHost {
  return {
    domain_names: names,
    forward_host: '10.0.0.9',
    forward_port: 8080,
    _provider_id: 1,
    _provider_name: 'npm',
    _provider_type: 'nginx_proxy_manager',
    ...extra,
  };
}

function dns(name: string, extra: Partial<SyncDnsRewrite> = {}): SyncDnsRewrite {
  return { domain: name, answer: '10.0.0.9', _provider_id: 2, _provider_name: 'adguard', ...extra };
}

function service(extra: Partial<Service>): Service {
  return {
    subdomain: 'api',
    domain: 'example.com',
    expose_mode: 'proxy_dns',
    tunnel_hostname: '',
    dns_provider_id: null,
    ...extra,
  } as Service;
}

describe('splitName', () => {
  it('splits at the zone the scan found, not at the first dot', () => {
    // The first dot filed `a.b.example.net` as `a` under a domain `b.example.net`, which the
    // import then declared; `_split_for_import` files it as `a.b` under `example.net`.
    expect(splitName('a.b.example.net', 'example.net')).toEqual({ subdomain: 'a.b', zone: 'example.net' });
  });

  it('falls back to the first dot without a zone, as the import does', () => {
    expect(splitName('a.b.example.net', '')).toEqual({ subdomain: 'a', zone: 'b.example.net' });
  });

  it('ignores a zone the name is not under', () => {
    expect(splitName('app.other.net', 'example.net')).toEqual({ subdomain: 'app', zone: 'other.net' });
  });

  it('reads the zone the way the import cleans it', () => {
    expect(splitName('app.example.net', ' .Example.NET. ')).toEqual({ subdomain: 'app', zone: 'example.net' });
  });

  it('leaves nothing in front of an apex, and no zone behind a single label', () => {
    expect(splitName('example.net', 'example.net')).toEqual({ subdomain: '', zone: 'example.net' });
    expect(splitName('nas', '')).toEqual({ subdomain: 'nas', zone: '' });
  });
});

describe('proxyName', () => {
  it('is the first name that is not blank, lowercased', () => {
    expect(proxyName(proxy(['  ', 'App.Example.com', 'b.example.com']))).toBe('app.example.com');
  });

  it('reads `domains` before `domain_names`, and falls through an empty one', () => {
    expect(proxyName({ domains: ['a.example.com'], domain_names: ['b.example.com'] })).toBe('a.example.com');
    expect(proxyName({ domains: [], domain_names: ['b.example.com'] })).toBe('b.example.com');
  });
});

describe('buildRows', () => {
  it('holds a name once, whatever answered for it, proxy first', () => {
    const scan: SyncResult = {
      proxy_hosts: [proxy(['api.example.com'])],
      dns_rewrites: [dns('api.example.com'), dns('API.example.com', { _provider_id: 3, _provider_name: 'cloudflare' })],
    };
    const { rows } = buildRows(scan, [], NOTHING_TRACKED);

    expect(rows).toHaveLength(1);
    expect(rows[0].records.map((r) => [r.kind, r.provider])).toEqual([
      ['proxy', 'npm'],
      ['dns', 'adguard'],
      ['dns', 'cloudflare'],
    ]);
    expect([rows[0].proxyCount, rows[0].dnsCount]).toEqual([1, 2]);
    expect(rows[0].records[0].target).toBe('10.0.0.9:8080');
  });

  it("prefers the zone a DNS integration reported over the proxy's", () => {
    // `import_services` makes the same choice: a proxy knows no zone, the DNS side read the
    // record from one.
    const scan: SyncResult = {
      proxy_hosts: [proxy(['a.b.example.net'], { _zone: 'b.example.net' })],
      dns_rewrites: [dns('a.b.example.net', { _zone: 'example.net' })],
    };
    const [row] = buildRows(scan, [], NOTHING_TRACKED).rows;

    expect([row.subdomain, row.zone]).toEqual(['a.b', 'example.net']);
  });

  it('marks the names the import refuses, and why', () => {
    const scan: SyncResult = { dns_rewrites: [dns('example.com', { _zone: 'example.com' }), dns('nas')] };
    const rows = buildRows(scan, [], NOTHING_TRACKED).rows;

    expect(rows.find((r) => r.key === 'example.com')?.unimportable).toBe('apex');
    expect(rows.find((r) => r.key === 'nas')?.unimportable).toBe('no_dot');
  });

  it('counts the records that have no name instead of drawing them', () => {
    const scan: SyncResult = { proxy_hosts: [proxy([' '])], dns_rewrites: [dns('')] };
    expect(buildRows(scan, [], NOTHING_TRACKED)).toEqual({ rows: [], nameless: 2 });
  });

  it('puts the declared zones first, then reads by zone and name', () => {
    const scan: SyncResult = {
      proxy_hosts: [
        proxy(['b.zeta.net'], { _zone: 'zeta.net', _declared: false }),
        proxy(['z.mine.eu'], { _zone: 'mine.eu', _declared: true }),
        proxy(['a.alpha.org'], { _zone: 'alpha.org', _declared: false }),
        proxy(['a.mine.eu'], { _zone: 'mine.eu', _declared: true }),
      ],
    };
    const keys = buildRows(scan, ['mine.eu'], NOTHING_TRACKED).rows.map((r) => r.key);

    expect(keys).toEqual(['a.mine.eu', 'z.mine.eu', 'a.alpha.org', 'b.zeta.net']);
  });

  it('reads `declared` from the declared domains when the scan did not say', () => {
    const scan: SyncResult = { proxy_hosts: [proxy(['app.mine.eu']), proxy(['app.other.net'])] };
    const rows = buildRows(scan, declaredOf(['Mine.EU.']), NOTHING_TRACKED).rows;

    expect(rows.map((r) => [r.key, r.declared])).toEqual([
      ['app.mine.eu', true],
      ['app.other.net', false],
    ]);
  });

  it('offers a link only for the half a tracked service is missing', () => {
    const scan: SyncResult = { dns_rewrites: [dns('api.example.com'), dns('tv.example.com'), dns('db.example.com')] };
    const tracked = trackServices([
      service({}),
      // A tunnel writes its own record: the import never links one to it.
      service({ subdomain: 'tv', expose_mode: 'tunnel' }),
      // Already has its DNS half.
      service({ subdomain: 'db', dns_provider_id: 2 }),
    ]);
    const status = Object.fromEntries(buildRows(scan, [], tracked).rows.map((r) => [r.key, r.status]));

    expect(status).toEqual({ 'api.example.com': 'link', 'tv.example.com': 'exists', 'db.example.com': 'exists' });
  });

  it('takes the scan at its word when it says a name is tracked', () => {
    const scan: SyncResult = { proxy_hosts: [proxy(['app.example.com'], { _already_imported: true })] };
    expect(buildRows(scan, [], NOTHING_TRACKED).rows[0].status).toBe('exists');
  });
});

describe('payloadFor', () => {
  const scan: SyncResult = {
    proxy_hosts: [proxy(['new.example.com']), proxy(['api.example.com']), proxy(['left.example.com'])],
    dns_rewrites: [dns('new.example.com'), dns('api.example.com'), dns('left.example.com')],
  };

  it('sends only the rows chosen, and of a link only its DNS half', () => {
    const tracked = trackServices([service({})]);
    const rows = buildRows(scan, [], tracked).rows.filter((r) => r.key !== 'left.example.com');
    const payload = payloadFor(scan, rows);

    expect(payload.proxy_hosts?.map(proxyName)).toEqual(['new.example.com']);
    expect(payload.dns_rewrites?.map((r) => r.domain)).toEqual(['new.example.com', 'api.example.com']);
  });

  it('sends nothing when nothing is chosen', () => {
    expect(payloadFor(scan, [])).toEqual({ proxy_hosts: [], dns_rewrites: [] });
  });
});

describe('zonesDeclaredBy', () => {
  it('names the undeclared zones of the new rows, once each, sorted', () => {
    const scan: SyncResult = {
      proxy_hosts: [
        proxy(['a.zeta.net'], { _zone: 'zeta.net' }),
        proxy(['b.zeta.net'], { _zone: 'zeta.net' }),
        proxy(['a.alpha.org'], { _zone: 'alpha.org' }),
        proxy(['a.mine.eu'], { _zone: 'mine.eu', _declared: true }),
        proxy(['old.gone.io'], { _zone: 'gone.io', _already_imported: true }),
      ],
    };
    const rows = buildRows(scan, ['mine.eu'], NOTHING_TRACKED).rows;

    expect(zonesDeclaredBy(rows)).toEqual(['alpha.org', 'zeta.net']);
  });
});
