import type { Environment, Provider, Service, ServicePayload, Tag } from '@/types/api';

export type ModeFilter = 'all' | 'tunnel' | 'proxy' | 'dns' | 'disabled';
export const MODE_FILTERS: ModeFilter[] = ['all', 'tunnel', 'proxy', 'dns', 'disabled'];

export type StatusFilter = 'ok' | 'error';
export const isStatusFilter = (value: string | null): value is StatusFilter => value === 'ok' || value === 'error';

/** DNS providers that answer on the LAN, so their target is a private IP rather than a public one. */
export const LOCAL_DNS_TYPES = ['pihole', 'adguard'];
export const isLocalDnsType = (type: string | null | undefined): boolean =>
  LOCAL_DNS_TYPES.includes(String(type || '').toLowerCase());

/** `subdomain.domain`, or the bare domain for apex routes. */
export const fqdnOf = (service: Pick<Service, 'subdomain' | 'domain'>): string =>
  service.subdomain ? `${service.subdomain}.${service.domain}` : service.domain;

/** The host a visitor types: the tunnel hostname when routed through a tunnel, else the FQDN. */
export const publicHostOf = (service: Service): string =>
  service.expose_mode === 'tunnel' && service.tunnel_hostname ? service.tunnel_hostname : fqdnOf(service);

/** A wildcard host cannot be opened in a tab. */
export const isNavigablePublicHost = (host: string): boolean => Boolean(host) && !host.includes('*');

/** `scheme://ip:port` — what the route forwards to. */
export const targetOf = (service: Service): string =>
  `${service.forward_scheme || 'http'}://${service.target_ip}:${service.target_port}`;

/** How a service reaches the internet, derived from its mode and provider ids. */
export type RouteKind = 'disabled' | 'tunnel' | 'proxy_dns' | 'proxy' | 'dns' | 'none';

export const routeKindOf = (service: Service): RouteKind => {
  if (!service.enabled) return 'disabled';
  if (service.expose_mode === 'tunnel') return 'tunnel';
  if (service.proxy_provider_id && service.dns_provider_id) return 'proxy_dns';
  if (service.proxy_provider_id) return 'proxy';
  if (service.dns_provider_id) return 'dns';
  return 'none';
};

/** The same rule the mode tabs count with. */
export const matchesMode = (service: Service, mode: ModeFilter): boolean => {
  if (mode === 'all') return true;
  if (mode === 'disabled') return !service.enabled;
  if (mode === 'tunnel') return Boolean(service.enabled) && service.expose_mode === 'tunnel';
  if (mode === 'proxy') {
    return Boolean(service.enabled) && service.expose_mode !== 'tunnel' && Boolean(service.proxy_provider_id);
  }
  return (
    Boolean(service.enabled) &&
    service.expose_mode !== 'tunnel' &&
    !service.proxy_provider_id &&
    Boolean(service.dns_provider_id)
  );
};

export const matchesSearch = (service: Service, search: string): boolean => {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  const fqdn = fqdnOf(service).toLowerCase();
  return (
    fqdn.includes(needle) ||
    (service.subdomain ?? '').toLowerCase().includes(needle) ||
    (service.domain ?? '').toLowerCase().includes(needle) ||
    (service.tunnel_hostname ?? '').toLowerCase().includes(needle) ||
    (service.target_ip ?? '').toLowerCase().includes(needle)
  );
};

const idsOf = (rows: Array<Tag | Environment> | undefined): number[] =>
  Array.isArray(rows) ? rows.map((row) => Number(row?.id)).filter((id) => Number.isFinite(id)) : [];

const numberIds = (rows: unknown): number[] =>
  Array.isArray(rows) ? rows.map((id) => Number(id)).filter((id) => Number.isFinite(id)) : [];

/**
 * A full `ServicePayload` from a row plus overrides — `PUT /api/services/{id}` takes the
 * whole record (extra keys rejected), so flipping `enabled` still sends everything.
 */
export const buildServicePayload = (
  service: Service | null,
  overrides: Partial<ServicePayload> = {},
): ServicePayload => {
  const idOrNull = (value: unknown): number | null => (value ? Number(value) : null);
  return {
    // No `id`: it is already in the URL, and the API rejects unknown keys.
    subdomain: String(overrides.subdomain ?? service?.subdomain ?? '').trim().toLowerCase(),
    domain: String(overrides.domain ?? service?.domain ?? '').trim().toLowerCase(),
    target_ip: String(overrides.target_ip ?? service?.target_ip ?? '').trim(),
    target_port: Number(overrides.target_port ?? service?.target_port ?? 80),
    forward_scheme: (overrides.forward_scheme ?? service?.forward_scheme ?? 'http') === 'https' ? 'https' : 'http',
    websocket: Boolean(overrides.websocket ?? service?.websocket ?? false),
    expose_mode: (overrides.expose_mode ?? service?.expose_mode) === 'tunnel' ? 'tunnel' : 'proxy_dns',
    public_target_mode:
      (overrides.public_target_mode ?? service?.public_target_mode ?? 'manual') === 'auto' ? 'auto' : 'manual',
    auto_update_dns: Boolean(overrides.auto_update_dns ?? service?.auto_update_dns ?? false),
    tunnel_provider_id: idOrNull(overrides.tunnel_provider_id ?? service?.tunnel_provider_id),
    tunnel_hostname: String(overrides.tunnel_hostname ?? service?.tunnel_hostname ?? ''),
    enabled: Boolean(overrides.enabled ?? service?.enabled ?? true),
    proxy_provider_id: idOrNull(overrides.proxy_provider_id ?? service?.proxy_provider_id),
    dns_provider_id: idOrNull(overrides.dns_provider_id ?? service?.dns_provider_id),
    dns_ip: String(overrides.dns_ip ?? service?.dns_ip ?? '').trim(),
    tag_ids: overrides.tag_ids ?? idsOf(service?.tags),
    environment_ids: overrides.environment_ids ?? idsOf(service?.environments),
    icon_url: String(overrides.icon_url ?? service?.icon_url ?? ''),
    extra_proxy_provider_ids: overrides.extra_proxy_provider_ids ?? numberIds(service?.extra_proxy_provider_ids),
    extra_dns_provider_ids: overrides.extra_dns_provider_ids ?? numberIds(service?.extra_dns_provider_ids),
  };
};

/** One provider a route touches, with the role it plays there. */
export type ProviderRoleEntry = {
  provider: Provider;
  role: 'tunnel' | 'proxy' | 'dns' | 'extra_proxy' | 'extra_dns';
};

/** Every provider the route is pushed to, primary ones first, each listed once. */
export const providersOf = (service: Service, providers: Provider[]): ProviderRoleEntry[] => {
  const byId = new Map(providers.map((p) => [Number(p.id), p]));
  const entries: ProviderRoleEntry[] = [];
  const seen = new Set<number>();
  const push = (id: unknown, role: ProviderRoleEntry['role']) => {
    const numeric = Number(id);
    if (!numeric || seen.has(numeric)) return;
    const provider = byId.get(numeric);
    if (!provider) return;
    seen.add(numeric);
    entries.push({ provider, role });
  };
  if (service.expose_mode === 'tunnel') {
    push(service.tunnel_provider_id, 'tunnel');
  } else {
    push(service.proxy_provider_id, 'proxy');
    push(service.dns_provider_id, 'dns');
  }
  numberIds(service.extra_proxy_provider_ids).forEach((id) => push(id, 'extra_proxy'));
  if (service.expose_mode !== 'tunnel') {
    numberIds(service.extra_dns_provider_ids).forEach((id) => push(id, 'extra_dns'));
  }
  return entries;
};
