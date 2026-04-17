export type Provider = {
  id: number;
  name: string;
  type: string;
  url: string;
  enabled: boolean | number;
};

export type FormState = {
  domain: string;
  subdomain: string;
  target_ip: string;
  target_port: number;
  forward_scheme: 'http' | 'https';
  websocket: boolean;
  expose_mode: 'proxy_dns' | 'tunnel';
  public_target_mode: 'manual' | 'auto';
  auto_update_dns: boolean;
  tunnel_provider_id: string;
  tunnel_hostname: string;
  proxy_provider_id: string;
  dns_provider_id: string;
  dns_ip: string;
  extra_proxy_provider_ids: string[];
  extra_dns_provider_ids: string[];
};

export const initialForm: FormState = {
  domain: '',
  subdomain: 'app',
  target_ip: '',
  target_port: 80,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_provider_id: '',
  tunnel_hostname: '',
  proxy_provider_id: '',
  dns_provider_id: '',
  dns_ip: '',
  extra_proxy_provider_ids: [],
  extra_dns_provider_ids: [],
};

export const toFormState = (service?: Record<string, unknown> | null): FormState => {
  if (!service) return initialForm;
  return {
    domain: String(service.domain || ''),
    subdomain: String(service.subdomain || ''),
    target_ip: String(service.target_ip || service.target_host || ''),
    target_port: Number(service.target_port || 80),
    forward_scheme: service.forward_scheme === 'https' ? 'https' : 'http',
    websocket: Boolean(service.websocket),
    expose_mode: service.expose_mode === 'tunnel' ? 'tunnel' : 'proxy_dns',
    public_target_mode: service.public_target_mode === 'auto' ? 'auto' : 'manual',
    auto_update_dns: Boolean(service.auto_update_dns),
    tunnel_provider_id: service.tunnel_provider_id ? String(service.tunnel_provider_id) : '',
    tunnel_hostname: String(service.tunnel_hostname || ''),
    proxy_provider_id: service.proxy_provider_id ? String(service.proxy_provider_id) : '',
    dns_provider_id: service.dns_provider_id ? String(service.dns_provider_id) : '',
    dns_ip: String(service.dns_ip || ''),
    extra_proxy_provider_ids: Array.isArray(service.extra_proxy_provider_ids)
      ? (service.extra_proxy_provider_ids as unknown[]).map((id) => String(id))
      : [],
    extra_dns_provider_ids: Array.isArray(service.extra_dns_provider_ids)
      ? (service.extra_dns_provider_ids as unknown[]).map((id) => String(id))
      : [],
  };
};

export const hasCapability = (
  provider: Provider,
  capability: 'proxy' | 'dns' | 'supports_auto_public_target' | 'supports_tunnel',
  providerTypeMap: Record<string, Record<string, unknown>>,
): boolean => {
  const typeKey = (provider.type || '').toLowerCase();
  const meta = providerTypeMap[typeKey] || {};
  const caps = (meta?.capabilities || {}) as Record<string, unknown>;

  if (Object.prototype.hasOwnProperty.call(caps, capability)) return Boolean(caps[capability]);
  if (capability === 'proxy') return meta?.category === 'proxy' || ['npm', 'traefik'].includes(typeKey);
  if (capability === 'dns') return meta?.category === 'dns' || ['cloudflare', 'pihole', 'adguard'].includes(typeKey);
  return false;
};
