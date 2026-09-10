/**
 * Shapes and pure helpers for the Templates page.
 *
 * A template is a *service preset*: everything the service form repeats (scheme, port,
 * providers, domain, tags) minus the two things that are always per-service (subdomain and
 * target IP). The backend model lives in `app/api/templates.py`; `TemplateIn` there is the
 * exact body `POST /api/templates` and `PUT /api/templates/{tid}` accept.
 *
 * Everything here is deliberately free of React so the modal, the card and the page agree on
 * one conversion and one validation instead of three.
 */

import type { Tone } from '@/components/ui';
import { providerHasCapability } from '@/lib/providers';
import type {
  ExposeMode,
  ForwardScheme,
  Provider,
  ProviderTypesResponse,
  PublicTargetMode,
  Template,
  TemplateIn,
} from '@/types/api';

/** `useT()`'s signature — helpers that build a message take it rather than importing React. */
export type TFunction = (key: string, params?: Record<string, string | number>) => string;

/** `name` is `VARCHAR(64)` on the server side (`TemplateIn.name_not_empty`). */
export const TEMPLATE_NAME_MAX = 64;

/** The sentinel a `<Select>` uses for "no provider / no domain — decide per service". */
export const UNSET = '';
/** The `<Select>` option that reveals the free-text domain input. */
export const DOMAIN_CUSTOM = '__custom__';

/**
 * The modal's state. Numbers live as strings so a field can be *empty* (the backend accepts
 * `target_port: null` and `proxy_provider_id: null`), which a `number` state cannot express.
 */
export interface TemplateFormState {
  name: string;
  description: string;
  icon_url: string;
  forward_scheme: ForwardScheme;
  /** Digits, or empty for "set the port on each service". */
  target_port: string;
  websocket: boolean;
  expose_mode: ExposeMode;
  proxy_provider_id: string;
  dns_provider_id: string;
  tunnel_provider_id: string;
  public_target_mode: PublicTargetMode;
  domain: string;
  dns_ip: string;
  tag_ids: number[];
}

export type TemplateFormField = 'name' | 'target_port' | 'icon_url' | 'domain' | 'dns_ip';
export type TemplateFormErrors = Partial<Record<TemplateFormField, string>>;

export const emptyTemplateForm: TemplateFormState = {
  name: '',
  description: '',
  icon_url: '',
  forward_scheme: 'http',
  target_port: '',
  websocket: false,
  expose_mode: 'proxy_dns',
  proxy_provider_id: UNSET,
  dns_provider_id: UNSET,
  tunnel_provider_id: UNSET,
  public_target_mode: 'manual',
  domain: '',
  dns_ip: '',
  tag_ids: [],
};

const idToField = (value: number | null | undefined): string =>
  value === null || value === undefined ? UNSET : String(value);

const fieldToId = (value: string): number | null => {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

/** A stored template, opened in the form. */
export function toTemplateForm(template: Template): TemplateFormState {
  return {
    name: template.name ?? '',
    description: template.description ?? '',
    icon_url: template.icon_url ?? '',
    forward_scheme: template.forward_scheme === 'https' ? 'https' : 'http',
    target_port:
      template.target_port === null || template.target_port === undefined ? '' : String(template.target_port),
    websocket: Boolean(template.websocket),
    expose_mode: template.expose_mode === 'tunnel' ? 'tunnel' : 'proxy_dns',
    proxy_provider_id: idToField(template.proxy_provider_id),
    dns_provider_id: idToField(template.dns_provider_id),
    tunnel_provider_id: idToField(template.tunnel_provider_id),
    public_target_mode: template.public_target_mode === 'auto' ? 'auto' : 'manual',
    domain: template.domain ?? '',
    dns_ip: template.dns_ip ?? '',
    tag_ids: Array.isArray(template.tag_ids) ? template.tag_ids.filter((id) => Number.isFinite(id)) : [],
  };
}

/**
 * The form, as the API wants it. The provider ids of the mode that is *not* selected are
 * dropped: leaving a stale `tunnel_provider_id` on a `proxy_dns` template would silently
 * seed the service form with a tunnel the user never chose.
 */
export function toTemplateIn(form: TemplateFormState): TemplateIn {
  const isTunnel = form.expose_mode === 'tunnel';
  const port = form.target_port.trim();
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    forward_scheme: form.forward_scheme,
    target_port: port ? Number(port) : null,
    websocket: form.websocket,
    expose_mode: form.expose_mode,
    proxy_provider_id: isTunnel ? null : fieldToId(form.proxy_provider_id),
    dns_provider_id: isTunnel ? null : fieldToId(form.dns_provider_id),
    tunnel_provider_id: isTunnel ? fieldToId(form.tunnel_provider_id) : null,
    public_target_mode: isTunnel ? 'manual' : form.public_target_mode,
    domain: form.domain.trim(),
    dns_ip: isTunnel ? '' : form.dns_ip.trim(),
    tag_ids: [...form.tag_ids],
    icon_url: form.icon_url.trim(),
  };
}

const DIGITS = /^\d+$/;
const ICON_URL = /^(?:https?:\/\/|\/|data:image\/)/i;
const DOMAIN = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/i;
// An IPv4/IPv6 literal or a hostname — the backend stores `dns_ip` verbatim, so this only
// catches a typo (a space, a scheme, a slash) rather than pretending to be an IP parser.
const HOST_OR_IP = /^[A-Za-z0-9._:%[\]-]+$/;

/** Inline errors for the form, already translated. Empty object = ready to submit. */
export function validateTemplateForm(form: TemplateFormState, t: TFunction): TemplateFormErrors {
  const errors: TemplateFormErrors = {};

  const name = form.name.trim();
  if (!name) errors.name = t('templates.form.name_required');
  else if (name.length > TEMPLATE_NAME_MAX) {
    errors.name = t('templates.form.name_too_long', { max: TEMPLATE_NAME_MAX });
  }

  const port = form.target_port.trim();
  if (port) {
    if (!DIGITS.test(port)) errors.target_port = t('templates.form.port_invalid');
    else {
      const parsed = Number(port);
      if (parsed < 1 || parsed > 65535) errors.target_port = t('templates.form.port_range');
    }
  }

  const icon = form.icon_url.trim();
  if (icon && !ICON_URL.test(icon)) errors.icon_url = t('templates.form.icon_invalid');

  const domain = form.domain.trim();
  if (domain && !DOMAIN.test(domain)) errors.domain = t('templates.form.domain_invalid');

  if (form.expose_mode === 'proxy_dns') {
    const dnsIp = form.dns_ip.trim();
    if (dnsIp && !HOST_OR_IP.test(dnsIp)) errors.dns_ip = t('templates.form.dns_ip_invalid');
  }

  return errors;
}

// ---------------------------------------------------------------------------
// Provider capabilities
// ---------------------------------------------------------------------------

// The rule and its fallback table live in `lib/providers.ts`, which every picker in the app
// now shares. Re-exported here so the Templates screens keep one import.
export { providerHasCapability };

export interface ProviderChoices {
  proxy: Provider[];
  dns: Provider[];
  tunnel: Provider[];
}

/** The three provider lists the form offers, enabled providers only. */
export function splitProviders(providers: Provider[], providerTypes: ProviderTypesResponse): ProviderChoices {
  const enabled = providers.filter((p) => Boolean(p.enabled));
  const proxy = enabled.filter((p) => providerHasCapability(p, 'proxy', providerTypes));
  return {
    proxy,
    dns: enabled.filter((p) => providerHasCapability(p, 'dns', providerTypes)),
    tunnel: proxy.filter((p) => providerHasCapability(p, 'supports_tunnel', providerTypes)),
  };
}

// ---------------------------------------------------------------------------
// Card & list helpers
// ---------------------------------------------------------------------------

// Tag colours are free text on the server (`app/validators.py` falls back to `blue`), while
// the primitives only speak the six semantic tones — this is the bridge.
const TAG_TONES: Record<string, Tone> = {
  red: 'danger',
  pink: 'danger',
  orange: 'warning',
  yellow: 'warning',
  lime: 'success',
  green: 'success',
  teal: 'success',
  blue: 'info',
  azure: 'info',
  cyan: 'info',
  indigo: 'primary',
  purple: 'primary',
  dark: 'neutral',
  secondary: 'neutral',
};

/** The semantic tone a tag's stored colour maps to. */
export function tagTone(color: string | undefined): Tone {
  return TAG_TONES[String(color || '').toLowerCase()] ?? 'neutral';
}

/** `http://…:8096`, or `https://…` when the template leaves the port to the service. */
export function endpointLabel(template: Pick<Template, 'forward_scheme' | 'target_port'>): string {
  const scheme = template.forward_scheme === 'https' ? 'https' : 'http';
  return template.target_port ? `${scheme}://…:${template.target_port}` : `${scheme}://…`;
}

/**
 * A duplicate's name, unique against the names already taken, and never longer than the 64
 * characters the server accepts — so the copy is created instead of bouncing off a 409.
 */
export function duplicateName(base: string, taken: Iterable<string>, suffix: string): string {
  const used = new Set(Array.from(taken, (n) => n.trim().toLowerCase()));
  const fit = (candidate: string) =>
    candidate.length <= TEMPLATE_NAME_MAX
      ? candidate
      : `${base.slice(0, Math.max(1, TEMPLATE_NAME_MAX - (candidate.length - base.length)))}${candidate.slice(base.length)}`;

  for (let n = 1; n < 1000; n += 1) {
    const candidate = fit(n === 1 ? `${base} ${suffix}` : `${base} ${suffix} ${n}`);
    if (!used.has(candidate.trim().toLowerCase())) return candidate;
  }
  return fit(`${base} ${suffix} ${Date.now()}`);
}

/** Lowercased haystack a template is searched on: name, description, domain, port, tag names. */
export function searchHaystack(template: Template, tagNames: string[]): string {
  return [
    template.name,
    template.description,
    template.domain,
    template.dns_ip,
    template.forward_scheme,
    template.target_port ? String(template.target_port) : '',
    ...tagNames,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}
