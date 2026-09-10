import type { Service, TemplateApplyResult } from '@/types/api';

export type Provider = {
  id: number;
  name: string;
  type: string;
  url: string;
  enabled: boolean | number;
};

export type UiExposeMode = 'dns_only' | 'dns_proxy' | 'tunnel';

export type FormState = {
  domain: string;
  subdomain: string;
  target_ip: string;
  target_port: number;
  forward_scheme: 'http' | 'https';
  websocket: boolean;
  expose_mode: 'proxy_dns' | 'tunnel';
  /** UI-level selection that maps to expose_mode + visible fields.
   *  dns_only → proxy_dns with no proxy provider (DNS record only)
   *  dns_proxy → proxy_dns with both proxy + DNS providers
   *  tunnel → tunnel mode
   */
  ui_expose_mode: UiExposeMode;
  public_target_mode: 'manual' | 'auto';
  auto_update_dns: boolean;
  tunnel_provider_id: string;
  tunnel_hostname: string;
  proxy_provider_id: string;
  dns_provider_id: string;
  dns_ip: string;
  extra_proxy_provider_ids: string[];
  extra_dns_provider_ids: string[];
  /** Tags attached to the route; written as `tag_ids` on create and edit. */
  tag_ids: number[];
  /** Environments attached to the route; written as `environment_ids` on create and edit. */
  environment_ids: number[];
  /** Kept verbatim from the record (or a template) -- the form has no icon picker. */
  icon_url: string;
};

export const initialForm: FormState = {
  domain: '',
  subdomain: 'app',
  target_ip: '',
  target_port: 80,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'proxy_dns',
  ui_expose_mode: 'dns_proxy',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_provider_id: '',
  tunnel_hostname: '',
  proxy_provider_id: '',
  dns_provider_id: '',
  dns_ip: '',
  extra_proxy_provider_ids: [],
  extra_dns_provider_ids: [],
  tag_ids: [],
  environment_ids: [],
  icon_url: '',
};

/** Numeric ids out of `[{id}]` rows or a plain `[1, 2]` list, anything else dropped. */
const toIdList = (value: unknown): number[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) =>
      item && typeof item === 'object'
        ? Number((item as Record<string, unknown>).id)
        : Number(item),
    )
    .filter((id) => Number.isFinite(id) && id > 0);
};

const uiModeFor = (exposeMode: 'proxy_dns' | 'tunnel', proxyId: string, dnsId: string): UiExposeMode => {
  if (exposeMode === 'tunnel') return 'tunnel';
  if (proxyId) return 'dns_proxy';
  if (dnsId) return 'dns_only';
  return 'dns_proxy'; // default for legacy / empty records
};

/** The edit form, seeded from the row `GET /api/services` returned. */
export const toFormState = (service?: Service | null): FormState => {
  if (!service) return initialForm;
  const exposeMode = service.expose_mode === 'tunnel' ? 'tunnel' : 'proxy_dns';
  const proxyId = service.proxy_provider_id ? String(service.proxy_provider_id) : '';
  const dnsId = service.dns_provider_id ? String(service.dns_provider_id) : '';
  return {
    domain: String(service.domain || ''),
    subdomain: String(service.subdomain || ''),
    target_ip: String(service.target_ip || ''),
    target_port: Number(service.target_port || 80),
    forward_scheme: service.forward_scheme === 'https' ? 'https' : 'http',
    websocket: Boolean(service.websocket),
    expose_mode: exposeMode,
    ui_expose_mode: uiModeFor(exposeMode, proxyId, dnsId),
    public_target_mode: service.public_target_mode === 'auto' ? 'auto' : 'manual',
    auto_update_dns: Boolean(service.auto_update_dns),
    tunnel_provider_id: service.tunnel_provider_id ? String(service.tunnel_provider_id) : '',
    tunnel_hostname: String(service.tunnel_hostname || ''),
    proxy_provider_id: proxyId,
    dns_provider_id: dnsId,
    dns_ip: String(service.dns_ip || ''),
    extra_proxy_provider_ids: (service.extra_proxy_provider_ids ?? []).map((id) => String(id)),
    extra_dns_provider_ids: (service.extra_dns_provider_ids ?? []).map((id) => String(id)),
    // A service row carries `tags: [{id, name, color}]` -- `toIdList` reduces it to the ids
    // the payload writes back as `tag_ids`.
    tag_ids: toIdList(service.tags),
    environment_ids: toIdList(service.environments),
    icon_url: String(service.icon_url || ''),
  };
};

/**
 * A fresh form seeded from `GET /api/templates/{id}/apply`: the template decides scheme,
 * port, mode, providers, domain and tags; subdomain and target stay for the user to fill.
 */
export const templateToFormState = (
  tpl: TemplateApplyResult | Record<string, unknown> | null | undefined,
): FormState => {
  if (!tpl) return initialForm;
  const record = tpl as Record<string, unknown>;
  const exposeMode = record.expose_mode === 'tunnel' ? 'tunnel' : 'proxy_dns';
  const proxyId = record.proxy_provider_id ? String(record.proxy_provider_id) : '';
  const dnsId = record.dns_provider_id ? String(record.dns_provider_id) : '';
  const port = Number(record.target_port);
  return {
    ...initialForm,
    domain: String(record.domain || ''),
    target_port: Number.isFinite(port) && port > 0 ? port : initialForm.target_port,
    forward_scheme: record.forward_scheme === 'https' ? 'https' : 'http',
    websocket: Boolean(record.websocket),
    expose_mode: exposeMode,
    ui_expose_mode: uiModeFor(exposeMode, proxyId, dnsId),
    public_target_mode: record.public_target_mode === 'auto' ? 'auto' : 'manual',
    tunnel_provider_id: record.tunnel_provider_id ? String(record.tunnel_provider_id) : '',
    proxy_provider_id: proxyId,
    dns_provider_id: dnsId,
    dns_ip: String(record.dns_ip || ''),
    tag_ids: toIdList(record.tag_ids),
    icon_url: String(record.icon_url || ''),
  };
};

/** The `subdomain.domain` a form describes, or `null` while either half is missing. */
export const fqdnOf = (formData: Pick<FormState, 'subdomain' | 'domain'>): string | null =>
  formData.subdomain && formData.domain ? `${formData.subdomain}.${formData.domain}` : null;

// The capability rule this file used to carry a third copy of now lives in `lib/providers.ts`.
// That copy had no `supports_tunnel` branch, so both call sites patched around it inline and a
// provider offered by the Templates picker could go missing from this one. One table now.
export { providerHasCapability } from '@/lib/providers';
