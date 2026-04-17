import { Globe, Server, Shield, ShieldCheck, Waypoints, Box } from 'lucide-react';

const providerIcons: Record<string, typeof Globe> = {
  cloudflare: Globe,
  cloudflare_tunnel: Waypoints,
  npm: Server,
  traefik: Box,
  pihole: Shield,
  adguard: ShieldCheck,
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
