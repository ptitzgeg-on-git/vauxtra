import type { ComponentType } from 'react';
import { Globe, Server, Shield, ShieldCheck, Waypoints, Box } from 'lucide-react';

type LogoProps = { className?: string };

/**
 * Inline marks for providers that have no lucide equivalent. They follow the
 * lucide conventions (24px grid, 2px stroke, currentColor) so they sit next to
 * the lucide icons without a visible style break, and they stay inline so the
 * UI never depends on an external image.
 */
function ZoraxyMark({ className }: LogoProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 2 L21 7 L21 17 L12 22 L3 17 L3 7 Z" />
      <path d="M8.5 8.5 H15.5 L8.5 15.5 H15.5" />
    </svg>
  );
}

function TechnitiumMark({ className }: LogoProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M8 9 H16" />
      <path d="M12 9 V16" />
    </svg>
  );
}

const providerIcons: Record<string, ComponentType<LogoProps>> = {
  cloudflare: Globe,
  cloudflare_tunnel: Waypoints,
  npm: Server,
  traefik: Box,
  zoraxy: ZoraxyMark,
  pihole: Shield,
  adguard: ShieldCheck,
  technitium: TechnitiumMark,
  docker: Box,
};

export function ProviderLogo({
  type,
  className = 'w-6 h-6',
  fallback,
}: {
  type: string;
  className?: string;
  fallback?: React.ReactNode;
}) {
  const Icon = providerIcons[type.toLowerCase()];
  if (Icon) {
    return <Icon className={className} />;
  }
  return fallback ? <>{fallback}</> : <Server className={className} />;
}
