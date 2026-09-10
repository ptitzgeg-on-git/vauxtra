/**
 * The one place that answers "does this provider type do X?".
 *
 * `GET /api/providers/types` serves a `capabilities` map per type (`app/providers/factory.py`
 * → `PROVIDER_TYPES`), and that map always wins. The fallback tables below only decide what
 * the answer looks like before the query has resolved, or for a type an older backend does
 * not describe yet — they are the floor, never the rule.
 *
 * This used to live in three files with three different fallback tables, so a provider could
 * be offered by one picker and silently missing from another. Add a capability here and every
 * picker learns it at once.
 */

import type { Provider, ProviderCapability, ProviderTypeMeta, ProviderTypesResponse } from '@/types/api';

/** Types that shipped before `capabilities` existed in `GET /api/providers/types`. */
const CAPABILITY_FALLBACK: Partial<Record<ProviderCapability, ReadonlySet<string>>> = {
  proxy: new Set(['npm', 'traefik', 'zoraxy']),
  dns: new Set(['cloudflare', 'pihole', 'adguard', 'technitium']),
  public_dns: new Set(['cloudflare']),
  supports_tunnel: new Set(['cloudflare_tunnel']),
};

/**
 * Does the *type* described by `meta` declare `capability`?
 *
 * `hasOwnProperty` rather than a truthiness test: the backend sends `false` explicitly, and a
 * declared `false` must beat the fallback list instead of falling through to it.
 */
export function metaHasCapability(
  capability: ProviderCapability,
  type: string | null | undefined,
  meta?: ProviderTypeMeta,
): boolean {
  const typeKey = String(type || '').toLowerCase();
  const caps = meta?.capabilities;
  if (caps && Object.prototype.hasOwnProperty.call(caps, capability)) return Boolean(caps[capability]);

  const category = String(meta?.category || '').toLowerCase();
  if (capability === 'proxy' && category === 'proxy') return true;
  if (capability === 'dns' && category === 'dns') return true;
  return CAPABILITY_FALLBACK[capability]?.has(typeKey) ?? false;
}

/** Does this provider's *type* declare `capability` in `GET /api/providers/types`? */
export function providerHasCapability(
  provider: Pick<Provider, 'type'>,
  capability: ProviderCapability,
  providerTypes: ProviderTypesResponse,
): boolean {
  const typeKey = String(provider.type || '').toLowerCase();
  return metaHasCapability(capability, typeKey, providerTypes[typeKey]);
}
