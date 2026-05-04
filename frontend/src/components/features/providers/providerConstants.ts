/**
 * Shared provider types, constants, and guided wizard steps.
 * Used by both ProviderModal (main panel) and Setup (first-run wizard).
 */

import { Globe, Shield, Server, Box, ShieldCheck, Waypoints, Cpu } from 'lucide-react';
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
  project_url?: string;
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
  technitium: Cpu,
};

// ─── Metadata fallbacks (authoritative source is now /api/providers/types) ───

export const descByType: Record<string, string> = {
  cloudflare: 'DNS records via Cloudflare API',
  cloudflare_tunnel: 'Cloudflare Zero Trust Tunnel',
  pihole: 'Local DNS & ad filtering',
  npm: 'Nginx Proxy Manager',
  traefik: 'Dynamic reverse proxy (read-only)',
  adguard: 'DNS sinkhole & filtering',
  technitium: 'Self-hosted authoritative DNS server',
};

export const categoryByType: Record<string, { label: string; color: string }> = {
  cloudflare: { label: 'External DNS', color: 'bg-orange-500/10 text-orange-600 dark:text-orange-400' },
  cloudflare_tunnel: { label: 'Zero Trust', color: 'bg-orange-500/10 text-orange-600 dark:text-orange-400' },
  pihole: { label: 'Local DNS', color: 'bg-red-500/10 text-red-600 dark:text-red-400' },
  npm: { label: 'Reverse Proxy', color: 'bg-green-500/10 text-green-700 dark:text-green-400' },
  traefik: { label: 'Reverse Proxy', color: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  adguard: { label: 'Local DNS', color: 'bg-teal-500/10 text-teal-600 dark:text-teal-400' },
  technitium: { label: 'Local DNS', color: 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400' },
};

export const providerColor: Record<string, string> = {
  cloudflare: 'bg-orange-500/10 text-orange-600 border-orange-500/30 dark:text-orange-400',
  cloudflare_tunnel: 'bg-orange-500/10 text-orange-600 border-orange-500/30 dark:text-orange-400',
  npm: 'bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-400',
  traefik: 'bg-blue-500/10 text-blue-600 border-blue-500/30 dark:text-blue-400',
  pihole: 'bg-red-500/10 text-red-600 border-red-500/30 dark:text-red-400',
  adguard: 'bg-teal-500/10 text-teal-600 border-teal-500/30 dark:text-teal-400',
  technitium: 'bg-indigo-500/10 text-indigo-600 border-indigo-500/30 dark:text-indigo-400',
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
      title: 'Create a dedicated NPM user',
      body: 'Vauxtra needs a user account in Nginx Proxy Manager to manage proxy hosts.\n\n1. Open NPM at http://<npm-host>:81\n2. Go to Users (top-right menu) → Add User\n3. Fill in name, email and a strong password\n4. Under Permissions, enable Manage Proxy Hosts\n5. Save — then use that email and password in the next step\n\nTip: Using a dedicated Vauxtra user (instead of admin) limits blast radius.',
    },
    {
      title: 'Enter the NPM URL',
      body: 'Enter the URL of your NPM admin panel. The default port is 81.',
      fields: [
        {
          key: 'url',
          label: 'NPM URL',
          placeholder: 'http://192.168.1.10:81',
          hint: 'Use the internal IP or hostname. Include the port (default: 81).',
          inputType: 'url',
        },
      ],
    },
    {
      title: 'NPM Credentials',
      body: 'Enter the email and password of the NPM user you created in step 1.',
      fields: [
        {
          key: 'username',
          label: 'Email',
          placeholder: 'vauxtra@example.com',
          hint: 'The email you set when creating the NPM user.',
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
      title: 'Find your Pi-hole API token',
      body: 'Vauxtra uses the Pi-hole API to manage local DNS entries.\n\nTo find your API token:\n  Pi-hole v5:  Settings → API / Web interface → Show API token\n  Pi-hole v6:  Settings → API → Create / show API key\n\nAlternatively, you can use your admin panel password directly.\nThe URL is typically http://<pi-hole-ip> (port 80, no /admin suffix).',
    },
    {
      title: 'Pi-hole URL and credentials',
      body: 'Enter the Pi-hole URL and the API token (or admin password) you located in the previous step.',
      fields: [
        {
          key: 'url',
          label: 'Pi-hole URL',
          placeholder: 'http://192.168.1.53',
          hint: 'IP or hostname only — no /admin suffix needed.',
          inputType: 'url',
        },
        {
          key: 'password',
          label: 'API Token / Admin password',
          placeholder: '(paste API token or admin password)',
          hint: 'Settings → API / Web interface → Show API token (v5) or Settings → API (v6).',
          inputType: 'password',
        },
      ],
    },
  ],
  adguard: [
    {
      title: 'AdGuard Home connection details',
      body: 'Vauxtra uses the AdGuard Home REST API with your web admin credentials.\n\nNo extra configuration is needed in AdGuard — just use the same username and password as the admin panel.\n\nDefault URL: http://<host>:3000\nDefault credentials set during first-run setup.',
      fields: [
        {
          key: 'url',
          label: 'AdGuard URL',
          placeholder: 'http://192.168.1.10:3000',
          hint: 'Default port is 3000. Use the internal IP or hostname.',
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
      body: 'Vauxtra reads Traefik in read-only mode — it never modifies your routing configuration.\n\nYou need to expose the Traefik API on a reachable URL. Two common ways:\n\n  Option A — Insecure (quick test):\n    Add --api.insecure=true to your Traefik static config.\n    API will be available at http://<host>:8080/api/\n\n  Option B — Secure router (recommended):\n    Create a dedicated Traefik entrypoint/router for /api/\n    Add BasicAuth middleware if you want credentials.\n\nLeave username/password blank if no auth is configured.',
      fields: [
        {
          key: 'url',
          label: 'Traefik API URL',
          placeholder: 'http://192.168.1.10:8080',
          hint: 'Full URL to the Traefik API dashboard (no /api suffix needed).',
          inputType: 'url',
        },
      ],
    },
  ],
  technitium: [
    {
      title: 'Prepare your DNS zones',
      body: 'Vauxtra creates A records inside your existing Technitium zones.\n\nBefore connecting, you need at least one DNS zone set up in Technitium.\n\nTo create a zone:\n  1. Open Technitium at http://<host>:5380\n  2. Go to the Zones tab → Add Zone\n  3. Choose Primary Zone, enter your domain (e.g. home.lab or home.local)\n  4. Click Save\n\nVauxtra will auto-detect the correct zone for each service domain it manages.',
    },
    {
      title: 'Technitium credentials',
      body: 'Enter the URL of your Technitium web console and your admin credentials.\nDefault port is 5380.',
      fields: [
        {
          key: 'url',
          label: 'Technitium URL',
          placeholder: 'http://192.168.1.10:5380',
          hint: 'Default port is 5380. Use the internal IP or hostname.',
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
          placeholder: '(web UI password)',
          inputType: 'password',
        },
      ],
    },
  ],
};

export const projectUrlByType: Record<string, string> = {
  npm: 'https://nginxproxymanager.com',
  adguard: 'https://github.com/AdguardTeam/AdGuardHome',
  pihole: 'https://pi-hole.net',
  traefik: 'https://traefik.io',
  cloudflare: 'https://dash.cloudflare.com',
  cloudflare_tunnel: 'https://one.dash.cloudflare.com',
  technitium: 'https://technitium.com/dns',
};

/** Resolve project URL from API meta first, then local fallback. */
export function getProjectUrl(type: string, meta?: ProviderTypeMeta): string | undefined {
  return meta?.project_url || projectUrlByType[type];
}

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
  const passwordOptionalTypes = new Set(['traefik']);
  const requiresPassword = !passwordOptionalTypes.has(formData.type);

  return (
    Boolean(formData.type) &&
    Boolean(formData.name.trim()) &&
    (!requiresPassword || Boolean(formData.password.trim())) &&
    Boolean(formData.url.trim() || formData.type === 'cloudflare' || formData.type === 'cloudflare_tunnel') &&
    (formData.type !== 'cloudflare_tunnel' || (Boolean(formData.tunnel_id.trim()) && Boolean(formData.username.trim())))
  );
}
