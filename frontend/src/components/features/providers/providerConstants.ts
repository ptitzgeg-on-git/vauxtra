/**
 * Shared provider types, constants, and guided wizard steps.
 * Used by both ProviderModal (main panel) and Setup (first-run wizard).
 */

import { Globe, Shield, Server, Box, ShieldCheck, Waypoints } from 'lucide-react';
import type { ComponentType } from 'react';

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

export type ProviderTypeMeta = {
  label?: string;
  category?: string;
  available?: boolean;
  read_only?: boolean;
  placeholder_url?: string;
  user_label?: string;
  pass_label?: string;
  user_placeholder?: string;
  description?: string;
  category_label?: string;
  category_color?: string;
  provider_color?: string;
  guided_steps?: ApiGuidedStep[];
};

export type ProviderTypeMap = Record<string, ProviderTypeMeta>;

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
  adguard: ShieldCheck,
};

// ─── Metadata fallbacks (authoritative source is now /api/providers/types) ───

export const descByType: Record<string, string> = {
  cloudflare: 'DNS records via Cloudflare API',
  cloudflare_tunnel: 'Cloudflare Zero Trust Tunnel',
  pihole: 'Local DNS & ad filtering',
  npm: 'Nginx Proxy Manager',
  traefik: 'Dynamic reverse proxy (read-only)',
  adguard: 'DNS sinkhole & filtering',
};

export const categoryByType: Record<string, { label: string; color: string }> = {
  cloudflare: { label: 'External DNS', color: 'bg-orange-500/10 text-orange-600 dark:text-orange-400' },
  cloudflare_tunnel: { label: 'Zero Trust', color: 'bg-orange-500/10 text-orange-600 dark:text-orange-400' },
  pihole: { label: 'Local DNS', color: 'bg-red-500/10 text-red-600 dark:text-red-400' },
  npm: { label: 'Reverse Proxy', color: 'bg-green-500/10 text-green-700 dark:text-green-400' },
  traefik: { label: 'Reverse Proxy', color: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  adguard: { label: 'Local DNS', color: 'bg-teal-500/10 text-teal-600 dark:text-teal-400' },
};

export const providerColor: Record<string, string> = {
  cloudflare: 'bg-orange-500/10 text-orange-600 border-orange-500/30 dark:text-orange-400',
  cloudflare_tunnel: 'bg-orange-500/10 text-orange-600 border-orange-500/30 dark:text-orange-400',
  npm: 'bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-400',
  traefik: 'bg-blue-500/10 text-blue-600 border-blue-500/30 dark:text-blue-400',
  pihole: 'bg-red-500/10 text-red-600 border-red-500/30 dark:text-red-400',
  adguard: 'bg-teal-500/10 text-teal-600 border-teal-500/30 dark:text-teal-400',
};

/** Resolve description from API meta first, then local fallback. */
export function getDescription(type: string, meta?: ProviderTypeMeta): string {
  return meta?.description || descByType[type] || '';
}

/** Resolve category from API meta first, then local fallback. */
export function getCategory(type: string, meta?: ProviderTypeMeta): { label: string; color: string } | undefined {
  if (meta?.category_label) {
    return { label: meta.category_label, color: meta.category_color || '' };
  }
  return categoryByType[type];
}

/** Resolve provider color from API meta first, then local fallback. */
export function getProviderColor(type: string, meta?: ProviderTypeMeta): string {
  return meta?.provider_color || providerColor[type] || 'bg-primary/10 text-primary border-primary/20';
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
 * Resolve guided steps: prefer API-served steps, fall back to local constants.
 * This is the single entry point all UI components should use.
 */
export function getGuidedSteps(type: string, meta?: ProviderTypeMeta): GuidedStep[] {
  if (meta?.guided_steps?.length) {
    return parseApiSteps(meta.guided_steps);
  }
  return _localGuidedSteps[type] || [];
}

/** Local fallback — kept for offline / unknown providers. */
const _localGuidedSteps: Record<string, GuidedStep[]> = {
  cloudflare_tunnel: [
    {
      title: 'Create a tunnel in Cloudflare Zero Trust',
      body: 'Go to dash.cloudflare.com → Zero Trust → Networks → Tunnels → Create a tunnel.\nChoose the Cloudflared connector type and give it a name (e.g. "homelab").\n\nVauxtra manages ingress routes inside the tunnel — it does not run cloudflared itself.',
    },
    {
      title: 'Paste your Tunnel ID',
      body: 'From the tunnel overview page, copy the Tunnel ID (UUID format). Paste it below.',
      fields: [
        {
          key: 'tunnel_id',
          label: 'Tunnel ID',
          placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
          hint: 'Zero Trust → Networks → Tunnels → click your tunnel → Overview tab.',
          inputType: 'text',
        },
      ],
    },
    {
      title: 'Create a Cloudflare API Token',
      body: 'My Profile → API Tokens → Create Token → Custom Token.\nRequired permissions:\n  • Account → Cloudflare Tunnel → Edit\n  • Zone → DNS → Edit (select your zone)\n\nCopy the generated token and paste it below.',
      fields: [
        {
          key: 'password',
          label: 'API Token',
          placeholder: '(paste token here)',
          hint: 'Never share this token — it grants Tunnel and DNS write access.',
          inputType: 'password',
        },
      ],
    },
    {
      title: 'Enter your Cloudflare Account ID',
      body: 'Your Account ID is a 32-character hex string shown in the right sidebar of dash.cloudflare.com (any zone overview page).',
      fields: [
        {
          key: 'username',
          label: 'Account ID',
          placeholder: 'a1b2c3d4e5f6… (32 hex chars)',
          hint: 'Right sidebar on dash.cloudflare.com → select any domain.',
          inputType: 'text',
        },
      ],
    },
  ],
  cloudflare: [
    {
      title: 'Create a Cloudflare API Token',
      body: 'Go to My Profile → API Tokens → Create Token.\nUse the "Edit zone DNS" template, or a Custom Token with:\n  • Zone → DNS → Edit (select your zone)\n\nCopy the generated token and paste it below.',
      fields: [
        {
          key: 'password',
          label: 'API Token',
          placeholder: '(paste token here)',
          hint: 'Zone-scoped token with DNS:Edit permission.',
          inputType: 'password',
        },
      ],
    },
    {
      title: 'Zone ID (usually not needed)',
      body: 'Your API token already defines which zones it can access.\n\nLeave this blank unless you want to override the token scope.\nVauxtra will auto-detect zones from your token permissions.',
      fields: [
        {
          key: 'username',
          label: 'Zone ID',
          placeholder: '(leave blank - auto-detected from token)',
          hint: 'Only needed if your token covers multiple zones and you want to restrict to one.',
          inputType: 'text',
          optional: true,
        },
      ],
    },
  ],
  npm: [
    {
      title: 'Enter your NPM URL',
      body: "Nginx Proxy Manager's admin panel is typically at http://<npm-host>:81. Enter the full URL below.",
      fields: [
        {
          key: 'url',
          label: 'NPM URL',
          placeholder: 'http://npm:81',
          hint: 'Default admin port is 81. Use the internal hostname or IP.',
          inputType: 'url',
        },
      ],
    },
    {
      title: 'NPM Credentials',
      body: 'In NPM go to Users → Add User. Create a user with "Manage Proxy Hosts" permission. Enter its credentials below.',
      fields: [
        {
          key: 'username',
          label: 'Email',
          placeholder: 'user@example.com',
          hint: 'The NPM user email.',
          inputType: 'text',
        },
        {
          key: 'password',
          label: 'Password',
          placeholder: '(NPM user password)',
          inputType: 'password',
        },
      ],
    },
  ],
  pihole: [
    {
      title: 'Pi-hole URL and API key',
      body: 'Find your API token in Pi-hole Settings → API / Web interface → Show API token.\nEnter the Pi-hole URL and token below.',
      fields: [
        {
          key: 'url',
          label: 'Pi-hole URL',
          placeholder: 'http://10.0.0.53',
          hint: 'IP or hostname of your Pi-hole. No /admin suffix needed.',
          inputType: 'url',
        },
        {
          key: 'password',
          label: 'API Token / Admin password',
          placeholder: '(paste API token or admin password)',
          hint: 'Settings → API / Web interface → Show API token.',
          inputType: 'password',
        },
      ],
    },
  ],
  adguard: [
    {
      title: 'AdGuard Home credentials',
      body: 'AdGuard Home uses the same username/password as the web admin panel (port 3000 by default).',
      fields: [
        {
          key: 'url',
          label: 'AdGuard URL',
          placeholder: 'http://adguard:3000',
          hint: 'Default port is 3000.',
          inputType: 'url',
        },
        {
          key: 'username',
          label: 'Username',
          placeholder: 'admin',
          inputType: 'text',
        },
        {
          key: 'password',
          label: 'Password',
          placeholder: '(admin panel password)',
          inputType: 'password',
        },
      ],
    },
  ],
  traefik: [
    {
      title: 'Expose the Traefik API',
      body: 'Vauxtra reads Traefik config read-only — it does not write any files.\n\nExpose the API at a reachable URL (e.g. --api.insecure=true or via a dedicated router). Credentials are optional unless you added BasicAuth.',
      fields: [
        {
          key: 'url',
          label: 'Traefik API URL',
          placeholder: 'http://traefik:8080',
          hint: 'The Traefik API endpoint. No auth required unless configured.',
          inputType: 'url',
        },
      ],
    },
  ],
};

// ─── Helpers ────────────────────────────────────────────────────

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

export function canSubmitProvider(formData: ProviderFormState): boolean {
  return (
    Boolean(formData.type) &&
    Boolean(formData.name.trim()) &&
    Boolean(formData.password.trim()) &&
    Boolean(formData.url.trim() || formData.type === 'cloudflare' || formData.type === 'cloudflare_tunnel') &&
    (formData.type !== 'cloudflare_tunnel' || (Boolean(formData.tunnel_id.trim()) && Boolean(formData.username.trim())))
  );
}
