/**
 * What this module answers when `GET /api/providers/types` has not answered.
 *
 * `metaHasCapability` reads the live `capabilities` map first and a hardcoded table,
 * `CAPABILITY_FALLBACK`, last. That table is the only answer before the query resolves and
 * for as long as it fails, and it cannot say "unknown": a capability it does not name reads
 * as `false` for every type, which the screens act on.
 *
 * It named four of the six capabilities in `ProviderCapability` and had never been compared
 * with `PROVIDER_TYPES`, so five cells were wrong at once. The worst was silent rather than
 * visible: with no catalogue, Cloudflare read as unable to resolve a public target on its
 * own, and the expose form both hides the auto-update switch and rewrites the payload to
 * `public_target_mode: 'manual'` when that is false. `ServiceForm.test.tsx` holds that end.
 *
 * `scripts/check_capability_parity.py` holds the table to the backend cell by cell, so the
 * cases below are the ones with a consequence, plus the precedence rules the fallback must
 * not have broken by growing: a declared `false` still beats it, and a type it has never
 * heard of still gets `false` rather than a guess.
 */

import { describe, expect, it } from 'vitest';
import { metaHasCapability, providerHasCapability } from './providers';
import type { Provider, ProviderTypeMeta } from '@/types/api';

/** The whole point of the fallback: there is no meta to read. */
const NO_CATALOGUE = undefined;

const provider = (type: string): Pick<Provider, 'type'> => ({ type });

describe('metaHasCapability, with no catalogue to read', () => {
  it('lets Cloudflare and deSEC resolve a public target on their own', () => {
    expect(metaHasCapability('supports_auto_public_target', 'cloudflare', NO_CATALOGUE)).toBe(true);
    expect(metaHasCapability('supports_auto_public_target', 'desec', NO_CATALOGUE)).toBe(true);
  });

  it('leaves that off for a DNS provider that cannot, which is most of them', () => {
    expect(metaHasCapability('supports_auto_public_target', 'pihole', NO_CATALOGUE)).toBe(false);
    expect(metaHasCapability('supports_auto_public_target', 'powerdns', NO_CATALOGUE)).toBe(false);
  });

  it('keeps certificates with the two types that hold them', () => {
    expect(metaHasCapability('certificates', 'npm', NO_CATALOGUE)).toBe(true);
    expect(metaHasCapability('certificates', 'zoraxy', NO_CATALOGUE)).toBe(true);
    expect(metaHasCapability('certificates', 'traefik', NO_CATALOGUE)).toBe(false);
  });

  it('counts a tunnel as a proxy, which is how the tunnel list is derived', () => {
    expect(metaHasCapability('proxy', 'cloudflare_tunnel', NO_CATALOGUE)).toBe(true);
    expect(metaHasCapability('supports_tunnel', 'cloudflare_tunnel', NO_CATALOGUE)).toBe(true);
  });

  it('still answers false for a type it has never heard of', () => {
    expect(metaHasCapability('proxy', 'some-future-proxy', NO_CATALOGUE)).toBe(false);
    expect(metaHasCapability('certificates', '', NO_CATALOGUE)).toBe(false);
    expect(metaHasCapability('certificates', null, NO_CATALOGUE)).toBe(false);
  });
});

describe('metaHasCapability, when the catalogue did answer', () => {
  it('lets a declared false beat the fallback, in both directions', () => {
    const withoutCertificates: ProviderTypeMeta = {
      label: 'Nginx Proxy Manager',
      category: 'proxy',
      capabilities: { certificates: false },
    } as ProviderTypeMeta;
    expect(metaHasCapability('certificates', 'npm', withoutCertificates)).toBe(false);

    const withCertificates: ProviderTypeMeta = {
      label: 'Traefik',
      category: 'proxy',
      capabilities: { certificates: true },
    } as ProviderTypeMeta;
    expect(metaHasCapability('certificates', 'traefik', withCertificates)).toBe(true);
  });

  it('falls through to the fallback for a capability the catalogue left out', () => {
    const silent: ProviderTypeMeta = {
      label: 'Cloudflare',
      category: 'dns',
      capabilities: { dns: true },
    } as ProviderTypeMeta;
    expect(metaHasCapability('supports_auto_public_target', 'cloudflare', silent)).toBe(true);
  });
});

describe('providerHasCapability', () => {
  it('reads a provider through an empty catalogue without asking it to be non-empty', () => {
    expect(providerHasCapability(provider('cloudflare'), 'supports_auto_public_target', {})).toBe(true);
    expect(providerHasCapability(provider('CloudFlare'), 'public_dns', {})).toBe(true);
    expect(providerHasCapability(provider('npm'), 'certificates', {})).toBe(true);
  });
});
