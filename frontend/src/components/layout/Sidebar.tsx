import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Globe,
  Settings,
  Languages,
  Tag,
  Key,
  Bell,
  FileTerminal,
  Activity,
  GitMerge,
  ShieldCheck,
  AlertTriangle,
  LogOut,
  Bug,
  ExternalLink,
  BookOpen,
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import type { Service, Provider, HealthResponse } from '@/types/api';

interface CertExpiryResponse {
  expiring_soon_count: number;
}

interface SidebarProps {
  isMobile?: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

/** Tooltip wrapper for collapsed sidebar icon mode */
function NavTooltip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="relative group/tooltip w-full flex justify-center">
      {children}
      <span className="pointer-events-none absolute left-full top-1/2 -translate-y-1/2 ml-2 z-50 whitespace-nowrap rounded-md bg-popover border border-border px-2.5 py-1 text-xs font-medium text-popover-foreground shadow-md opacity-0 group-hover/tooltip:opacity-100 transition-opacity duration-150">
        {label}
      </span>
    </div>
  );
}

export function Sidebar({ isMobile = false, collapsed = false, onToggleCollapse: _onToggleCollapse }: SidebarProps) {
  const location = useLocation();
  const qc = useQueryClient();
  const t = useT();

  const isCollapsed = !isMobile && collapsed;

  const { data: authStatus } = useQuery<{ authenticated: boolean; auth_required: boolean }>({
    queryKey: ['auth-status'],
    queryFn: () => api.get<{ authenticated: boolean; auth_required: boolean }>('/auth/me'),
    staleTime: 120_000,
  });

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

  const { data: health } = useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: () => api.get<HealthResponse>('/health'),
    staleTime: 60_000,
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

  const settingsTab = new URLSearchParams(location.search).get('tab') || 'general';

  const isItemActive = (item: NavItem): boolean => {
    if (item.href.startsWith('/settings?tab=')) {
      const tab = item.href.split('tab=')[1] || 'general';
      return location.pathname === '/settings' && settingsTab === tab;
    }
    return location.pathname === item.href;
  };

  const groups: Array<{ title: string; items: NavItem[] }> = [
    {
      title: t('nav.group.overview'),
      items: [{ icon: <LayoutDashboard size={18} />, label: t('nav.dashboard'), href: '/' }],
    },
    {
      title: t('nav.group.services'),
      items: [
        { icon: <Globe size={18} />, label: t('nav.services'), href: '/services', badge: enabledServicesCount || undefined },
        { icon: <GitMerge size={18} />, label: t('nav.providers'), href: '/providers', badge: healthyProvidersCount || undefined },
      ],
    },
    {
      title: t('nav.group.operations'),
      items: [
        { icon: <Activity size={18} />, label: t('nav.monitoring'), href: '/monitoring', badge: errorServicesCount || undefined, badgeVariant: errorServicesCount > 0 ? 'error' : 'default' },
        { icon: <ShieldCheck size={18} />, label: t('nav.certificates'), href: '/certificates', badge: expiringSoonCount || undefined, badgeVariant: expiringSoonCount > 0 ? 'warn' : 'default' },
      ],
    },
    {
      title: t('nav.group.system'),
      items: [
        { icon: <Settings size={18} />, label: t('settings.tab.general'), href: '/settings?tab=general' },
        { icon: <Languages size={18} />, label: t('settings.language.title'), href: '/settings?tab=language' },
        { icon: <Globe size={18} />, label: t('settings.tab.dns'), href: '/settings?tab=dns' },
        { icon: <Tag size={18} />, label: `${t('settings.tab.tags')} & ${t('settings.tab.environments')}`, href: '/settings?tab=taxonomy' },
        { icon: <Key size={18} />, label: t('settings.tab.apikeys'), href: '/settings?tab=apikeys' },
        { icon: <Bell size={18} />, label: t('settings.tab.webhooks'), href: '/settings?tab=webhooks' },
        { icon: <FileTerminal size={18} />, label: t('settings.tab.logs'), href: '/settings?tab=logs' },
      ],
    },
  ];

  const badgeClass = (variant: NavItem['badgeVariant'] = 'default') => {
    if (variant === 'error') return 'bg-destructive/10 text-destructive border border-destructive/20';
    if (variant === 'warn') return 'bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border border-yellow-500/20';
    return 'bg-muted text-muted-foreground border border-border';
  };

  // ── Collapsed: icon-only mode ──────────────────────────────────────────────
  if (isCollapsed) {
    return (
      <div className="h-full bg-card border-r border-border flex flex-col items-center py-4 gap-1 overflow-hidden" style={{ width: 60 }}>
        {/* Brand icon */}
        <div className="w-9 h-9 rounded-xl bg-primary text-primary-foreground grid place-items-center font-extrabold text-xs shadow-sm shrink-0 mb-4">
          VX
        </div>

        {/* Nav items */}
        <nav className="flex flex-col items-center gap-1 flex-1 w-full px-1.5">
          {groups.flatMap((group) =>
            group.items.map((item) => {
              const isActive = isItemActive(item);
              return (
                <NavTooltip key={item.href} label={item.label}>
                  <Link
                    to={item.href}
                    className={`relative flex items-center justify-center w-9 h-9 rounded-lg transition-all ${
                      isActive
                        ? 'bg-background text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)] ring-1 ring-border/60'
                        : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                    }`}
                  >
                    {item.icon}
                    {item.badge !== undefined && (
                      <span className={`absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-0.5 flex items-center justify-center rounded-full text-[9px] font-bold ${badgeClass(item.badgeVariant)}`}>
                        {item.badge > 99 ? '99+' : item.badge}
                      </span>
                    )}
                    {item.href === '/monitoring' && errorServicesCount > 0 && (
                      <AlertTriangle className="absolute -top-0.5 -right-0.5 w-3 h-3 text-destructive" />
                    )}
                  </Link>
                </NavTooltip>
              );
            }),
          )}
        </nav>

        {/* Footer */}
        <div className="flex flex-col items-center gap-2 mt-auto">
          {authStatus?.auth_required && (
            <NavTooltip label={t('nav.signout')}>
              <button
                onClick={async () => { await api.post('/auth/logout'); qc.invalidateQueries({ queryKey: ['auth-status'] }); }}
                className="flex items-center justify-center w-9 h-9 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                <LogOut size={16} />
              </button>
            </NavTooltip>
          )}
          <span className="text-[9px] font-mono text-muted-foreground/60 pb-1">
            {health?.version ? `v${health.version}` : '—'}
          </span>
        </div>
      </div>
    );
  }

  // ── Expanded: full sidebar ─────────────────────────────────────────────────
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
          <h1 className="text-[17px] font-extrabold tracking-tight leading-none text-foreground">Vauxtra</h1>
          <p className="text-[11px] text-muted-foreground mt-0.5 leading-snug">The missing link in your network stack</p>
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
                const isActive = isItemActive(item);
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
                    <div className={`${isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground'} transition-colors shrink-0`}>
                      {item.icon}
                    </div>
                    <span className="text-sm flex-1">{item.label}</span>
                    {item.href === '/monitoring' && errorServicesCount > 0 && (
                      <AlertTriangle className="w-3.5 h-3.5 text-destructive shrink-0" />
                    )}
                    {item.badge !== undefined && (
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md shrink-0 ${badgeClass(item.badgeVariant)}`}>
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
            onClick={async () => { await api.post('/auth/logout'); qc.invalidateQueries({ queryKey: ['auth-status'] }); }}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <LogOut size={16} />
            <span>{t('nav.signout')}</span>
          </button>
        )}
        <div className="flex items-center justify-between text-muted-foreground text-xs px-2">
          <span className="font-semibold">{t('common.version')}</span>
          <span className="font-mono bg-muted px-1.5 py-0.5 rounded border border-border">
            {health?.version ? `v${health.version}` : '—'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground text-[11px] px-2 pt-1">
          <a href="https://github.com/ptitzgeg-on-git/vauxtra/blob/main/docs/HOWTO.md" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-foreground transition-colors">
            <BookOpen size={12} />
            {t('nav.docs')}
          </a>
          <span className="text-border">|</span>
          <a href="https://github.com/ptitzgeg-on-git/vauxtra/issues/new" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-foreground transition-colors">
            <Bug size={12} />
            {t('nav.report_bug')}
          </a>
          <span className="text-border">|</span>
          <a href="https://github.com/ptitzgeg-on-git/vauxtra" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-foreground transition-colors">
            <ExternalLink size={12} />
            {t('nav.github')}
          </a>
        </div>
      </div>
    </div>
  );
}
