import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Globe,
  Settings,
  Activity,
  GitMerge,
  ShieldCheck,
  AlertTriangle,
  LogOut,
  Bug,
  ExternalLink,
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Service, Provider } from '@/types/api';
import pkg from '../../../package.json';

interface CertExpiryResponse {
  expiring_soon_count: number;
}

export function Sidebar({ isMobile = false }: { isMobile?: boolean }) {
  const location = useLocation();
  const qc = useQueryClient();

  const { data: authStatus } = useQuery<{ authenticated: boolean; auth_required: boolean }>({
    queryKey: ['auth-status'],
    staleTime: 120_000,
  });

  // Badge data — lightweight queries, React Query deduplicates with Dashboard
  const { data: services } = useQuery<Service[]>({
    queryKey: ['services'],
    queryFn: () => api.get<Service[]>('/services'),
    staleTime: 30_000,
  });

  const { data: providers } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
    staleTime: 30_000,
  });

  const { data: certExpiry } = useQuery<CertExpiryResponse>({
    queryKey: ['certificates-expiry'],
    queryFn: () =>
      api
        .get<CertExpiryResponse>('/certificates/expiry')
        .catch((): CertExpiryResponse => ({ expiring_soon_count: 0 })),
    staleTime: 5 * 60_000,
  });

  const enabledServicesCount = services?.filter((s) => s.enabled).length ?? 0;
  const errorServicesCount = services?.filter((s) => s.enabled && s.status === 'error').length ?? 0;
  const healthyProvidersCount = providers?.filter((p) => p.enabled).length ?? 0;
  const expiringSoonCount = certExpiry?.expiring_soon_count ?? 0;

  type NavItem = {
    icon: React.ReactNode;
    label: string;
    href: string;
    badge?: number;
    badgeVariant?: 'default' | 'error' | 'warn';
  };

  const groups: Array<{ title: string; items: NavItem[] }> = [
    {
      title: 'Overview',
      items: [
        { icon: <LayoutDashboard size={18} />, label: 'Dashboard', href: '/' },
      ],
    },
    {
      title: 'Services',
      items: [
        {
          icon: <Globe size={18} />,
          label: 'Endpoints',
          href: '/services',
          badge: enabledServicesCount || undefined,
        },
        {
          icon: <GitMerge size={18} />,
          label: 'Integrations',
          href: '/providers',
          badge: healthyProvidersCount || undefined,
        },
      ],
    },
    {
      title: 'Operations',
      items: [
        { icon: <Activity size={18} />, label: 'Monitoring', href: '/monitoring', badge: errorServicesCount || undefined, badgeVariant: errorServicesCount > 0 ? 'error' : 'default' },
        {
          icon: <ShieldCheck size={18} />,
          label: 'Certificates',
          href: '/certificates',
          badge: expiringSoonCount || undefined,
          badgeVariant: expiringSoonCount > 0 ? 'warn' : 'default',
        },
      ],
    },
    {
      title: 'System',
      items: [
        { icon: <Settings size={18} />, label: 'Settings', href: '/settings' },
      ],
    },
  ];

  const badgeClass = (variant: NavItem['badgeVariant'] = 'default') => {
    if (variant === 'error')
      return 'bg-destructive/10 text-destructive border border-destructive/20';
    if (variant === 'warn')
      return 'bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/20';
    return 'bg-muted text-muted-foreground border border-border';
  };

  return (
    <div
      className={`h-full bg-card border-r border-border flex flex-col pt-6 font-sans antialiased text-foreground ${
        isMobile ? 'w-full' : 'w-72'
      }`}
    >
      {/* Brand */}
      <div className="px-4 mb-8 mt-1 flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary text-primary-foreground grid place-items-center font-extrabold tracking-tight text-sm shadow-sm shrink-0">
          VX
        </div>
        <div className="min-w-0">
          <h1 className="text-[17px] font-extrabold tracking-tight leading-none text-foreground">
            Vauxtra
          </h1>
          <p className="text-[11px] text-muted-foreground mt-0.5 leading-snug">
            The missing link in your network stack
          </p>
        </div>
      </div>

      {/* Navigation groups */}
      <nav className="flex-1 px-4 space-y-5 overflow-y-auto">
        {groups.map((group) => (
          <div key={group.title}>
            <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 px-3 mb-1.5">
              {group.title}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = location.pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    to={item.href}
                    className={`flex items-center gap-3 px-3 py-2 rounded-lg font-medium transition-all group outline-none focus:ring-2 focus:ring-primary/20 ${
                      isActive
                        ? 'bg-background text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)] ring-1 ring-border/60'
                        : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                    }`}
                  >
                    <div
                      className={`${
                        isActive
                          ? 'text-primary'
                          : 'text-muted-foreground group-hover:text-foreground'
                      } transition-colors shrink-0`}
                    >
                      {item.icon}
                    </div>
                    <span className="text-sm flex-1">{item.label}</span>

                    {/* Error indicator for monitoring */}
                    {item.href === '/monitoring' && errorServicesCount > 0 && (
                      <AlertTriangle className="w-3.5 h-3.5 text-destructive shrink-0" />
                    )}

                    {/* Count badge */}
                    {item.badge !== undefined && (
                      <span
                        className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md shrink-0 ${badgeClass(item.badgeVariant)}`}
                      >
                        {item.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-6 mt-auto space-y-3">
        {authStatus?.auth_required && (
          <button
            onClick={async () => {
              await api.post('/auth/logout');
              qc.invalidateQueries({ queryKey: ['auth-status'] });
            }}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <LogOut size={16} />
            <span>Sign out</span>
          </button>
        )}
        <div className="flex items-center justify-between text-muted-foreground text-xs px-2">
          <span className="font-semibold">Version</span>
          <span className="font-mono bg-muted px-1.5 py-0.5 rounded border border-border">
            v{pkg.version}
          </span>
        </div>
        <div className="flex items-center gap-3 text-muted-foreground text-[11px] px-2 pt-1">
          <a
            href="https://github.com/ptitzgeg-on-git/vauxtra/issues/new"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
          >
            <Bug size={12} />
            Report a bug
          </a>
          <span className="text-border">|</span>
          <a
            href="https://github.com/ptitzgeg-on-git/vauxtra"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 hover:text-foreground transition-colors"
          >
            <ExternalLink size={12} />
            GitHub
          </a>
        </div>
      </div>
    </div>
  );
}
