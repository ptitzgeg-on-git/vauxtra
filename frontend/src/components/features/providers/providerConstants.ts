/**
 * Shared provider types, constants, and guided wizard steps.
 * Used by both ProviderModal (main panel) and Setup (first-run wizard).
 */

import { Globe, GlobeLock, Shield, Server, Box, Database, ShieldCheck, Waypoints, Cpu, Route } from 'lucide-react';
import type { ComponentType } from 'react';
import type { TranslateFn } from '@/i18n';
import { metaHasCapability } from '@/lib/providers';
import type { ProviderCapability, ProviderTypeMeta, ProviderTypesResponse } from '@/types/api';

/**
 * The locale value for `key`, or `raw` when the key is not translated.
 *
 * Every string in this file also exists in `src/locales/*.json`; the English prose kept here
 * (and the prose the API serves in `guided_steps`) is the fallback shown before the locale
 * file has finished loading, and for a key a translator has not reached yet. `t()` returns the
 * key itself when it misses, which is what makes the comparison below work.
 */
function tr(t: TranslateFn | undefined, key: string, raw: string): string {
  if (!t) return raw;
  const out = t(key);
  return out === key ? raw : out;
}

// ─── Types ──────────────────────────────────────────────────────

export type ProviderFormState = {
  name: string;
  type: string;
  url: string;
  username: string;
  password: string;
  tunnel_id: string;
};

export type GuidedField = {
  key: keyof ProviderFormState;
  label: string;
  placeholder?: string;
  hint?: string;
  inputType?: 'text' | 'password' | 'url';
  optional?: boolean;
};

export type GuidedStep = {
  title: string;
  body: string;
  fields?: GuidedField[];
};

export type ProviderValidationResult = {
  ok: boolean;
  validation?: {
    checks?: Array<{ name?: string; ok?: boolean; detail?: string; blocking?: boolean }>;
    warnings?: Array<string>;
  };
  health?: {
    ok?: boolean;
    status?: string;
    error?: string;
  };
};

/** Raw guided step field as returned by the backend API (snake_case keys). */
export type ApiGuidedField = {
  key: string;
  label: string;
  placeholder?: string;
  hint?: string;
  input_type?: string;
  optional?: boolean;
};

/** Raw guided step as returned by the backend API. */
export type ApiGuidedStep = {
  title: string;
  body: string;
  fields?: ApiGuidedField[];
};

/**
 * `types/api.ts` owns the shape of a `GET /api/providers/types` entry; this file used to
 * declare a second, divergent copy. Both names are kept so the provider screens do not have to
 * change their imports, but they are one type.
 */
export type { ProviderTypeMeta, ProviderTypesResponse };

/** Alias kept for the provider screens; `ProviderTypesResponse` is the same thing. */
export type ProviderTypeMap = ProviderTypesResponse;

// ─── Constants ──────────────────────────────────────────────────

export const emptyForm: ProviderFormState = {
  name: '',
  type: '',
  url: '',
  username: '',
  password: '',
  tunnel_id: '',
};

export const fallbackIconByType: Record<string, ComponentType<{ className?: string; size?: number }>> = {
  cloudflare: Globe,
  cloudflare_tunnel: Waypoints,
  pihole: Shield,
  npm: Server,
  traefik: Box,
  zoraxy: Route,
  adguard: ShieldCheck,
  technitium: Cpu,
  powerdns: Database,
  desec: GlobeLock,
};

/**
 * The three classifiers below are the `(type, meta)` face of the one capability rule in
 * `lib/providers.ts` — `providerHasCapability` is the `(provider, map)` face of the same
 * function. Neither carries a fallback table of its own.
 */

/** True when the type serves as a reverse proxy. */
export function isProxyType(type: string, meta?: ProviderTypeMeta): boolean {
  return metaHasCapability('proxy', type, meta);
}

/** True when the type manages DNS records. */
export function isDnsType(type: string, meta?: ProviderTypeMeta): boolean {
  return metaHasCapability('dns', type, meta);
}

// ─── Metadata fallbacks (authoritative source is now /api/providers/types) ───

export const descByType: Record<string, string> = {
  cloudflare: 'DNS records via Cloudflare API',
  cloudflare_tunnel: 'Cloudflare Zero Trust Tunnel',
  pihole: 'Local DNS & ad filtering',
  npm: 'Nginx Proxy Manager',
  traefik: 'Dynamic reverse proxy (read-only)',
  zoraxy: 'Zoraxy reverse proxy',
  adguard: 'DNS sinkhole & filtering',
  technitium: 'Self-hosted authoritative DNS server',
  powerdns: 'PowerDNS Authoritative Server zone records',
  desec: 'Public DNS records via the deSEC API',
};

/**
 * The one-line description of a provider type, translated.
 *
 * `/api/providers/types` serves `description` in English on every route, so the locale value
 * wins; the API string is the fallback for a type this build does not know (a newer backend).
 */
export function getDescription(type: string, meta?: ProviderTypeMeta, t?: TranslateFn): string {
  const raw = meta?.description || descByType[type] || '';
  if (!type) return raw;
  return tr(t, `providers.type.${type}.desc`, raw);
}

/**
 * What the credential fields are called for this type: "Email" for NPM, "API Token" for
 * Cloudflare, "API key / password" for Pi-hole. The API sends those names in English
 * (`user_label` / `pass_label`), so the per-type locale key wins over them, and the generic
 * "Username" / "Password" is the last resort.
 */
export function getUserLabel(type: string, meta: ProviderTypeMeta | undefined, t: TranslateFn): string {
  return tr(t, `provider_modal.field.${type}.user_label`, meta?.user_label || t('provider_modal.field.username'));
}

export function getPassLabel(type: string, meta: ProviderTypeMeta | undefined, t: TranslateFn): string {
  return tr(t, `provider_modal.field.${type}.pass_label`, meta?.pass_label || t('provider_modal.field.password'));
}

// ─── Guided wizard steps ────────────────────────────────────────

/** Convert API guided steps (snake_case) to frontend GuidedStep[] (camelCase). */
function parseApiSteps(apiSteps: ApiGuidedStep[]): GuidedStep[] {
  return apiSteps.map((s) => ({
    title: s.title,
    body: s.body,
    fields: s.fields?.map((f) => ({
      key: f.key as keyof ProviderFormState,
      label: f.label,
      placeholder: f.placeholder,
      hint: f.hint,
      inputType: (f.input_type || 'text') as GuidedField['inputType'],
      optional: f.optional,
    })),
  }));
}

/**
 * Translate a wizard: `provider_guide.<type>.step_<n>.title|body` and, per field,
 * `provider_guide.<type>.step_<n>.<field>.label|hint|placeholder`.
 *
 * The keys are derived from the position of the step rather than written next to each string,
 * so the wizards in `app/providers/factory.py` must keep the order the locale files were
 * written from. A placeholder that is a URL, an e-mail or a UUID has no key on purpose: it
 * stays as it is in every language.
 */
function localizeSteps(type: string, steps: GuidedStep[], t?: TranslateFn): GuidedStep[] {
  if (!t) return steps;
  return steps.map((step, index) => {
    const base = `provider_guide.${type}.step_${index + 1}`;
    return {
      title: tr(t, `${base}.title`, step.title),
      body: tr(t, `${base}.body`, step.body),
      fields: step.fields?.map((field) => ({
        ...field,
        label: tr(t, `${base}.${field.key}.label`, field.label),
        hint: field.hint === undefined ? undefined : tr(t, `${base}.${field.key}.hint`, field.hint),
        placeholder:
          field.placeholder === undefined
            ? undefined
            : tr(t, `${base}.${field.key}.placeholder`, field.placeholder),
      })),
    };
  });
}

/**
 * The guided wizard for a type, translated. The single entry point all UI components use.
 *
 * `GET /api/providers/types` is the only source: `PROVIDER_TYPES` ships `guided_steps` for
 * every type it serves. There is deliberately no local copy of the prose — the one that used
 * to live here was English only, so a French or German operator met a screen of English in the
 * middle of onboarding. An empty list means "the types have not arrived yet" and the caller
 * shows its loading state or the expert form, never placeholder copy.
 */
export function getGuidedSteps(type: string, meta?: ProviderTypeMeta, t?: TranslateFn): GuidedStep[] {
  if (!meta?.guided_steps?.length) return [];
  return localizeSteps(type, parseApiSteps(meta.guided_steps), t);
}

export const projectUrlByType: Record<string, string> = {
  npm: 'https://nginxproxymanager.com',
  adguard: 'https://github.com/AdguardTeam/AdGuardHome',
  pihole: 'https://pi-hole.net',
  traefik: 'https://traefik.io',
  zoraxy: 'https://zoraxy.aroz.org',
  cloudflare: 'https://dash.cloudflare.com',
  cloudflare_tunnel: 'https://one.dash.cloudflare.com',
  technitium: 'https://technitium.com/dns',
  powerdns: 'https://doc.powerdns.com/authoritative/',
  desec: 'https://desec.io',
};

/** Resolve project URL from API meta first, then local fallback. */
export function getProjectUrl(type: string, meta?: ProviderTypeMeta): string | undefined {
  return meta?.project_url || projectUrlByType[type];
}

// ─── Helpers ────────────────────────────────────────────────────

/**
 * The form state after the user picks a type in the Integrations modal.
 *
 * `url` is the whole reason this is a function rather than a spread. The type metadata carries
 * a `placeholder_url`, and seeding the field from it puts a value in the box that is
 * indistinguishable from the grey hint, because it IS the hint: the same string is the
 * placeholder. Nine of the twelve types name `http://192.168.1.10:3000`, the tenth address of
 * the commonest home range, where a real machine usually answers. Left untouched by someone
 * who read a filled box as empty, the username and password are sent there. So a type change
 * clears the URL, a re-pick of the same type keeps what was typed, and nothing else may ever
 * put a value in it. The first-run wizard has only ever seeded the name.
 */
export function seedFormForType(prev: ProviderFormState, type: string, label: string): ProviderFormState {
  const sameType = prev.type === type;
  return {
    ...prev,
    type,
    name: sameType && prev.name.trim() ? prev.name : label,
    url: sameType ? prev.url : '',
  };
}

export function buildPayload(formData: ProviderFormState) {
  return {
    name: formData.name.trim(),
    type: formData.type,
    url: formData.url.trim(),
    username: formData.username.trim(),
    password: formData.password,
    extra: formData.type === 'cloudflare_tunnel'
      ? { tunnel_id: formData.tunnel_id.trim() }
      : {},
  };
}

const passwordOptionalTypes = new Set(['traefik', 'zoraxy']);
const urlOptionalTypes = new Set(['cloudflare', 'cloudflare_tunnel', 'desec']);

/**
 * Whether a secret is mandatory for this type. The API may say so explicitly
 * (`requires_password`); otherwise the local list of auth-less proxies decides.
 */
export function requiresPassword(type: string, meta?: Pick<ProviderTypeMeta, 'requires_password'>): boolean {
  if (typeof meta?.requires_password === 'boolean') return meta.requires_password;
  return !passwordOptionalTypes.has(type);
}

/** Whether a username / account id is mandatory: API flag first, tunnel account id otherwise. */
export function requiresUsername(type: string, meta?: Pick<ProviderTypeMeta, 'requires_username'>): boolean {
  if (typeof meta?.requires_username === 'boolean') return meta.requires_username;
  return type === 'cloudflare_tunnel';
}

/** Hosted types fall back to their public API endpoint when the URL is left empty. */
export function isUrlOptional(type: string): boolean {
  return urlOptionalTypes.has(type);
}

export function canSubmitProvider(
  formData: ProviderFormState,
  meta?: Pick<ProviderTypeMeta, 'requires_password' | 'requires_username'>,
): boolean {
  return (
    Boolean(formData.type) &&
    Boolean(formData.name.trim()) &&
    (!requiresPassword(formData.type, meta) || Boolean(formData.password.trim())) &&
    (!requiresUsername(formData.type, meta) || Boolean(formData.username.trim())) &&
    Boolean(formData.url.trim() || isUrlOptional(formData.type)) &&
    (formData.type !== 'cloudflare_tunnel' || Boolean(formData.tunnel_id.trim()))
  );
}

// ─── Capabilities & grouping ────────────────────────────────────

/** True when the type drives a tunnel (ingress routes rather than a classic reverse proxy). */
export function isTunnelType(type: string, meta?: ProviderTypeMeta): boolean {
  return metaHasCapability('supports_tunnel', type, meta);
}

/** Section a provider type belongs to on the Integrations page and in the type picker. */
export type ProviderGroup = 'reverse' | 'tunnel' | 'dns' | 'other';

export const PROVIDER_GROUPS: ProviderGroup[] = ['reverse', 'tunnel', 'dns', 'other'];

export function getProviderGroup(type: string, meta?: ProviderTypeMeta): ProviderGroup {
  if (isTunnelType(type, meta)) return 'tunnel';
  if (isProxyType(type, meta)) return 'reverse';
  if (isDnsType(type, meta)) return 'dns';
  return 'other';
}

/** Capability flags shown as badges, in display order. */
export const CAPABILITY_BADGES: ProviderCapability[] = ['proxy', 'dns', 'public_dns', 'supports_tunnel', 'certificates'];

/** The capabilities a type declares true, in `CAPABILITY_BADGES` order. */
export function listCapabilities(meta?: Pick<ProviderTypeMeta, 'capabilities'>): ProviderCapability[] {
  const caps = meta?.capabilities || {};
  return CAPABILITY_BADGES.filter((key) => caps[key] === true);
}
