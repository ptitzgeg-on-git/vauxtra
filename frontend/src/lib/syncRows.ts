/**
 * What a provider scan offers, one row per name, and what an import of some of those rows
 * sends back. Read by the two screens that import from a scan: Settings > Data, and the last
 * step of the setup wizard.
 *
 * The rules are the import's (`import_services` in `app/api/sync.py`), copied once, so a
 * name is cut where the import cuts it, at its zone. The two screens used to build their rows
 * on their own and disagreed with each other: Settings > Data showed a name once, under
 * whichever record had been read first, and the wizard offered it three times when a proxy
 * and two DNS integrations all answered for it -- three rows that the import then folded
 * into one service and one refusal.
 */

import type { Service, SyncDnsRewrite, SyncProxyHost, SyncResult } from '@/types/api';

const LOCAL_TLDS = ['.lan', '.local', '.home', '.internal', '.localdomain', '.arpa'];

function isLocalDomain(domain: string): boolean {
  return LOCAL_TLDS.some((tld) => domain.endsWith(tld));
}

/** A name the way the import compares it: `_imported_name` in `app/api/sync.py`. */
export function importedName(value: unknown): string {
  return String(value ?? '').trim().toLowerCase();
}

export function bareZone(value: unknown): string {
  return importedName(value).replace(/^\.+|\.+$/g, '');
}

/**
 * The name the import files a proxy host under: its first non-blank name, which is the one
 * `import_services` keeps. A host serving several names becomes one service, and the import
 * sets the other names aside.
 */
export function proxyName(host: SyncProxyHost): string {
  const names = host.domains?.length ? host.domains : (host.domain_names ?? []);
  for (const name of names) {
    const clean = importedName(name);
    if (clean) return clean;
  }
  return '';
}

export function dnsName(record: SyncDnsRewrite): string {
  return importedName(record.domain);
}

/**
 * *name* split at its zone, the way `_split_for_import` splits it. The hint is the `_zone` the
 * scan computed, which already prefers a declared domain. Without one, everything after the
 * first dot, which is what the import falls back to as well.
 */
export function splitName(name: string, zoneHint: string): { subdomain: string; zone: string } {
  const hint = bareZone(zoneHint);
  let zone = '';
  if (hint && (name === hint || name.endsWith(`.${hint}`))) zone = hint;
  else if (name.includes('.')) zone = name.slice(name.indexOf('.') + 1);
  if (!zone) return { subdomain: name, zone: '' };
  return { subdomain: name === zone ? '' : name.slice(0, -(zone.length + 1)), zone };
}

export type RecordKind = 'proxy' | 'dns';

/** One record the scan found for a name: a proxy host, or a DNS answer. */
export interface FoundRecord {
  kind: RecordKind;
  provider: string;
  /** The integration that listed it, so a screen can draw its logo. */
  providerId?: number;
  /** Sent with proxy hosts only; a DNS record's type is read from its integration. */
  providerType?: string;
  target: string;
}

/**
 * `new`: no service holds this name. `link`: a service holds it without a DNS record, and
 * importing the record fills that half -- `linked` in the answer. `exists`: a service holds it
 * whole, and the import would pass it over.
 */
export type RowStatus = 'new' | 'link' | 'exists';

export interface SyncRow {
  /** The whole name, lowercased: what the import and the selection both key on. */
  key: string;
  subdomain: string;
  zone: string;
  /** The zone is one the operator declared in Settings > DNS domains. */
  declared: boolean;
  /** Proxy hosts first, then DNS records, each in the order the integrations were asked. */
  records: FoundRecord[];
  proxyCount: number;
  dnsCount: number;
  isLocal: boolean;
  status: RowStatus;
  /** Why the import would refuse this name, when it would (`_split_for_import`). */
  unimportable: 'apex' | 'no_dot' | null;
}

function recordTarget(kind: RecordKind, item: SyncProxyHost | SyncDnsRewrite): string {
  if (kind === 'dns') {
    const record = item as SyncDnsRewrite;
    return String(record.answer || record.target || '');
  }
  const host = item as SyncProxyHost;
  const forward = host.forward_host || host.host;
  if (!forward) return '';
  const port = host.forward_port || host.port;
  return port ? `${forward}:${port}` : String(forward);
}

export function isImportable(row: SyncRow): boolean {
  return row.status !== 'exists' && row.unimportable === null;
}

/** The services that already publish a name, keyed the way the import looks them up. */
export interface Tracked {
  /** `subdomain.domain`, the one match `_tracked_row` makes before it links a record. */
  byName: Map<string, Service>;
  /** Every other name a service answers under: its public host, its tunnel hostname. */
  hosts: Set<string>;
}

/**
 * For a screen that has no list of services to hand. The scan's own `_already_imported`
 * still marks what the server knows is tracked; what is lost is only the `link` offer.
 */
export const NOTHING_TRACKED: Tracked = { byName: new Map(), hosts: new Set() };

export function trackServices(services: Service[]): Tracked {
  const byName = new Map<string, Service>();
  const hosts = new Set<string>();
  for (const svc of services) {
    // Spelled exactly as `_tracked_row` compares it, empty subdomain included: a link the
    // table promises has to be a link the import makes.
    byName.set(importedName(`${svc.subdomain ?? ''}.${svc.domain ?? ''}`), svc);
    for (const other of [svc.public_host, svc.tunnel_hostname]) {
      const host = importedName(other);
      if (host) hosts.add(host);
    }
  }
  return { byName, hosts };
}

/** The domains a scan was compared against, cleaned the way the rows were. */
export function declaredOf(source: readonly string[] | undefined): string[] {
  return [...new Set((source ?? []).map(bareZone).filter(Boolean))];
}

export function buildRows(scan: SyncResult, declaredDomains: string[], tracked: Tracked) {
  const byName = new Map<string, { proxies: SyncProxyHost[]; dns: SyncDnsRewrite[] }>();
  const bucket = (name: string) => {
    let found = byName.get(name);
    if (!found) {
      found = { proxies: [], dns: [] };
      byName.set(name, found);
    }
    return found;
  };
  // A record with no name cannot become a row: the import refuses it for the same reason.
  let nameless = 0;
  for (const host of scan.proxy_hosts ?? []) {
    const name = proxyName(host);
    if (name) bucket(name).proxies.push(host);
    else nameless += 1;
  }
  for (const record of scan.dns_rewrites ?? []) {
    const name = dnsName(record);
    if (name) bucket(name).dns.push(record);
    else nameless += 1;
  }

  const underDeclared = (name: string) => declaredDomains.some((d) => name === d || name.endsWith(`.${d}`));

  const rows: SyncRow[] = [];
  for (const [name, { proxies, dns }] of byName) {
    // The zone of a DNS record is the one the integration read it from, which a proxy cannot
    // know; `import_services` prefers it for the same reason.
    const first = dns[0] ?? proxies[0];
    const { subdomain, zone } = splitName(name, String(dns[0]?._zone || dns[0]?.zone || proxies[0]?._zone || ''));
    const service = tracked.byName.get(name);
    const known =
      service !== undefined ||
      tracked.hosts.has(name) ||
      proxies.some((host) => host._already_imported === true) ||
      dns.some((record) => record._already_imported === true);
    // Only the half the service is missing, and never for a tunnel, which writes its own
    // record: the import makes exactly this test before it links (`_tracked_row`).
    const linkable =
      service !== undefined && service.dns_provider_id == null && service.expose_mode !== 'tunnel' && dns.length > 0;
    rows.push({
      key: name,
      subdomain,
      zone,
      declared: typeof first._declared === 'boolean' ? first._declared : underDeclared(name),
      records: [
        ...proxies.map((host) => ({
          kind: 'proxy' as const,
          provider: host._provider_name || 'Proxy',
          providerId: host._provider_id,
          providerType: host._provider_type,
          target: recordTarget('proxy', host),
        })),
        ...dns.map((record) => ({
          kind: 'dns' as const,
          provider: record._provider_name || 'DNS',
          providerId: record._provider_id,
          target: recordTarget('dns', record),
        })),
      ],
      proxyCount: proxies.length,
      dnsCount: dns.length,
      isLocal: isLocalDomain(zone) || isLocalDomain(name),
      status: !known ? 'new' : linkable ? 'link' : 'exists',
      unimportable: !name.includes('.') ? 'no_dot' : name === zone ? 'apex' : null,
    });
  }
  // Declared zones first, then by zone and name: the same scan reads the same way twice.
  rows.sort((a, b) => {
    if (a.declared !== b.declared) return a.declared ? -1 : 1;
    if (a.zone !== b.zone) return a.zone < b.zone ? -1 : 1;
    if (a.subdomain !== b.subdomain) return a.subdomain < b.subdomain ? -1 : 1;
    return 0;
  });
  return { rows, nameless };
}

/**
 * Only the rows chosen, and only the half of each that can be written: a `link` row sends its
 * DNS records and not the proxy host its service already has.
 *
 * "Quick import" used to send the scan back whole, tracked names included, and the import
 * linked every DNS record whose name a service held -- re-pointing a service that already had
 * a DNS integration, and handing one to a tunnel service, which stores none. The server now
 * refuses both; this is the other half of that fix, so a click never asks for them.
 */
export function payloadFor(scan: SyncResult, rows: SyncRow[]): SyncResult {
  const created = new Set(rows.filter((row) => row.status === 'new').map((row) => row.key));
  const written = new Set(rows.map((row) => row.key));
  return {
    proxy_hosts: (scan.proxy_hosts ?? []).filter((host) => created.has(proxyName(host))),
    dns_rewrites: (scan.dns_rewrites ?? []).filter((record) => written.has(dnsName(record))),
  };
}

/**
 * The zones an import of *rows* will declare: `import_services` declares the zone of every
 * service it creates. Measured in production on 2026-09-22, a scan that one Cloudflare token
 * could read beyond the declared zone offered names in twelve zones nobody had declared.
 * Importing them would have declared domains nobody had asked for, and every domain picker in
 * the product lists a declared domain from then on.
 */
export function zonesDeclaredBy(rows: SyncRow[]): string[] {
  const zones = new Set<string>();
  for (const row of rows) {
    if (row.status === 'new' && !row.declared && row.zone) zones.add(row.zone);
  }
  return [...zones].sort();
}
