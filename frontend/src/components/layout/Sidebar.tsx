import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Bell,
  BookOpen,
  Bug,
  ExternalLink,
  FileTerminal,
  GitMerge,
  Globe,
  Key,
  Keyboard,
  Languages,
  LayoutDashboard,
  LayoutTemplate,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  Tag,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api } from '@/api/client';
import { SUPPORTED_LANGUAGES, useI18n, type Lang } from '@/i18n';
import { useTheme, type Theme } from '@/theme';
import { cn } from '@/lib/cn';
import { Badge, IconButton, Kbd, Select, Separator, Tooltip, buttonVariants, toneClasses, type Tone } from '@/components/ui';
import { isMacPlatform } from '@/components/ui/_internal';
import type { HealthResponse, Provider, Service } from '@/types/api';
import { BrandMark } from './BrandMark';

interface AuthStatus {
  authenticated: boolean;
  auth_required: boolean;
  auth_mode?: 'password' | 'open';
  setup_required?: boolean;
}

interface CertExpiryResponse {
  expiring_soon_count: number;
}

export interface SidebarProps {
  /** Rendered inside the mobile drawer: full width, close button, no collapse. */
  isMobile?: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  onOpenPalette?: () => void;
  onOpenShortcuts?: () => void;
  /** Called after a navigation link is clicked (the mobile drawer closes itself). */
  onNavigate?: () => void;
  /** Mobile only: the close button in the header. */
  onClose?: () => void;
}

type NavItem = {
  icon: ReactNode;
  label: string;
  href: string;
  badge?: number;
  tone?: Tone;
  /** An extra warning glyph next to the label (colour is never the only signal). */
  alert?: boolean;
};

type NavGroup = { title: string; items: NavItem[] };

const DOCS_URL = 'https://github.com/ptitzgeg-on-git/vauxtra/blob/main/docs/HOWTO.md';
const BUG_URL = 'https://github.com/ptitzgeg-on-git/vauxtra/issues/new';
const GITHUB_URL = 'https://github.com/ptitzgeg-on-git/vauxtra';

const THEME_ICONS: Record<Theme, ReactNode> = { light: <Sun />, dark: <Moon />, system: <Monitor /> };

const formatBadge = (n: number) => (n > 99 ? '99+' : String(n));

function FooterLink({ href, label, icon }: { href: string; label: string; icon: ReactNode }) {
  return (
    <Tooltip content={label}>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={label}
        className={buttonVariants({ variant: 'ghost', size: 'icon', className: 'h-8 w-8 text-muted-foreground' })}
      >
        <span className="inline-flex [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
      </a>
    </Tooltip>
  );
}

/** The application sidebar: brand, search, grouped navigation with live counts, and the footer tools. */
export function Sidebar({
  isMobile = false,
  collapsed = false,
  onToggleCollapse,
  onOpenPalette,
  onOpenShortcuts,
  onNavigate,
  onClose,
}: SidebarProps) {
  const location = useLocation();
  const qc = useQueryClient();
  const { t, lang, setLang } = useI18n();
  const { theme, toggleTheme } = useTheme();

  const isCollapsed = !isMobile && collapsed;

  const { data: authStatus } = useQuery<AuthStatus>({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/me'),
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

  // This query shares its key with the dashboard and the Certificates page. It used to
  // `.catch` into `{ expiring_soon_count: 0 }`, which resolved the promise -- so the shared
  // cache entry held a fabricated zero, the warning badge vanished on a backend hiccup, and
  // whichever of the three observers happened to fetch first decided what the other two
  // read. A failed check now leaves the badge off because the count is unknown, not zero.
  const { data: certExpiry, isSuccess: certExpiryKnown } = useQuery<CertExpiryResponse>({
    queryKey: ['certificates-expiry'],
    queryFn: () => api.get<CertExpiryResponse>('/certificates/expiry'),
    staleTime: 5 * 60_000,
  });

  const enabledServicesCount = services?.filter((s) => s.enabled).length ?? 0;
  const errorServicesCount = services?.filter((s) => s.enabled && s.status === 'error').length ?? 0;
  const healthyProvidersCount = providers?.filter((p) => p.enabled).length ?? 0;
  const expiringSoonCount = certExpiryKnown ? (certExpiry?.expiring_soon_count ?? 0) : 0;

  const settingsTab = new URLSearchParams(location.search).get('tab') || 'general';

  const isItemActive = (item: NavItem): boolean => {
    if (item.href.startsWith('/settings?tab=')) {
      const tab = item.href.split('tab=')[1] || 'general';
      return location.pathname === '/settings' && settingsTab === tab;
    }
    if (item.href === '/') return location.pathname === '/';
    return location.pathname === item.href || location.pathname.startsWith(`${item.href}/`);
  };

  const groups: NavGroup[] = [
    {
      title: t('nav.group.overview'),
      items: [{ icon: <LayoutDashboard />, label: t('nav.dashboard'), href: '/' }],
    },
    {
      title: t('nav.group.services'),
      items: [
        { icon: <Globe />, label: t('nav.services'), href: '/services', badge: enabledServicesCount || undefined },
        { icon: <GitMerge />, label: t('nav.providers'), href: '/providers', badge: healthyProvidersCount || undefined },
        { icon: <LayoutTemplate />, label: t('nav.templates'), href: '/templates' },
      ],
    },
    {
      title: t('nav.group.operations'),
      items: [
        {
          icon: <Activity />,
          label: t('nav.monitoring'),
          href: '/monitoring',
          badge: errorServicesCount || undefined,
          tone: errorServicesCount > 0 ? 'danger' : 'neutral',
          alert: errorServicesCount > 0,
        },
        {
          icon: <ShieldCheck />,
          label: t('nav.certificates'),
          href: '/certificates',
          badge: expiringSoonCount || undefined,
          tone: expiringSoonCount > 0 ? 'warning' : 'neutral',
        },
      ],
    },
    {
      title: t('nav.group.system'),
      items: [
        { icon: <Settings />, label: t('settings.tab.general'), href: '/settings?tab=general' },
        { icon: <Languages />, label: t('settings.language.title'), href: '/settings?tab=language' },
        { icon: <Globe />, label: t('settings.tab.dns'), href: '/settings?tab=dns' },
        { icon: <Tag />, label: t('settings.tab.taxonomy'), href: '/settings?tab=taxonomy' },
        { icon: <Key />, label: t('settings.tab.apikeys'), href: '/settings?tab=apikeys' },
        { icon: <Bell />, label: t('settings.tab.webhooks'), href: '/settings?tab=webhooks' },
        { icon: <FileTerminal />, label: t('settings.tab.logs'), href: '/settings?tab=logs' },
      ],
    },
  ];

  const version = health?.version ? `v${health.version}` : '—';
  const modKey = isMacPlatform() ? '⌘' : 'Ctrl';
  const themeLabel = `${t('layout.theme.toggle')} · ${t(`layout.theme.${theme}`)}`;
  const currentLang = SUPPORTED_LANGUAGES.find((l) => l.code === lang) ?? SUPPORTED_LANGUAGES[0];

  const cycleLanguage = () => {
    const index = SUPPORTED_LANGUAGES.findIndex((l) => l.code === lang);
    setLang(SUPPORTED_LANGUAGES[(index + 1) % SUPPORTED_LANGUAGES.length].code);
  };

  const signOut = async () => {
    await api.post('/auth/logout');
    qc.invalidateQueries({ queryKey: ['auth-status'] });
  };

  // ── Collapsed: 64px icon rail ────────────────────────────────────────────────
  if (isCollapsed) {
    return (
      <aside className="flex h-full w-16 flex-col items-center overflow-hidden border-r border-border bg-card py-3">
        <BrandMark size="md" className="mb-2" />
        {onToggleCollapse && (
          <IconButton
            label={t('layout.sidebar.expand')}
            icon={<PanelLeftOpen />}
            onClick={onToggleCollapse}
            tooltip
            tooltipPlacement="right"
            className="h-8 w-8 text-muted-foreground"
          />
        )}
        <IconButton
          label={`${t('layout.search')} (${modKey} K)`}
          icon={<Search />}
          onClick={onOpenPalette}
          tooltip
          tooltipPlacement="right"
          className="mt-1 h-9 w-9 border border-border bg-background text-muted-foreground hover:border-primary/40"
        />

        <nav aria-label={t('layout.nav')} className="mt-3 flex w-full flex-1 flex-col items-center gap-1 overflow-y-auto scrollbar-none px-2">
          {groups.map((group, gi) => (
            <div key={group.title} className="flex w-full flex-col items-center gap-1">
              {gi > 0 && <Separator className="my-1 w-8" />}
              {group.items.map((item) => {
                const active = isItemActive(item);
                const tone = toneClasses(item.tone ?? 'neutral');
                return (
                  <Tooltip key={item.href} content={item.label} placement="right">
                    <Link
                      to={item.href}
                      onClick={onNavigate}
                      // The badge is the only surfacing of "3 checks are failing" at this width, and an
                      // `aria-label` replaces the whole subtree in the name -- so the count goes into the name.
                      aria-label={
                        item.badge !== undefined
                          ? t('layout.nav.item_with_badge', { label: item.label, count: formatBadge(item.badge) })
                          : item.label
                      }
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors [&>svg]:h-[18px] [&>svg]:w-[18px]',
                        active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                      )}
                    >
                      {active && <span aria-hidden="true" className="absolute -left-2 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />}
                      {item.icon}
                      {item.badge !== undefined && (
                        <span
                          className={cn(
                            'absolute -right-1 -top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-bold tabular-nums ring-2 ring-card',
                            item.tone && item.tone !== 'neutral' ? tone.solid : 'bg-muted text-muted-foreground',
                          )}
                        >
                          {formatBadge(item.badge)}
                        </span>
                      )}
                    </Link>
                  </Tooltip>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="mt-auto flex flex-col items-center gap-1 pt-2">
          <IconButton label={themeLabel} icon={THEME_ICONS[theme]} onClick={toggleTheme} tooltip tooltipPlacement="right" className="h-8 w-8 text-muted-foreground" />
          <Tooltip content={`${t('layout.language.next')} · ${currentLang.label}`} placement="right">
            <button
              type="button"
              onClick={cycleLanguage}
              aria-label={`${t('layout.language.next')} · ${currentLang.label}`}
              className={buttonVariants({ variant: 'ghost', size: 'icon', className: 'h-8 w-8 text-base leading-none' })}
            >
              <span aria-hidden="true">{currentLang.flag}</span>
            </button>
          </Tooltip>
          <IconButton label={t('layout.shortcuts')} icon={<Keyboard />} onClick={onOpenShortcuts} tooltip tooltipPlacement="right" className="h-8 w-8 text-muted-foreground" />
          {authStatus?.auth_required && (
            <IconButton label={t('nav.signout')} icon={<LogOut />} onClick={signOut} tooltip tooltipPlacement="right" className="h-8 w-8 text-muted-foreground" />
          )}
          <span className="pt-1 font-mono text-[10px] text-muted-foreground">{version}</span>
        </div>
      </aside>
    );
  }

  // ── Expanded: full sidebar ───────────────────────────────────────────────────
  return (
    <aside className={cn('flex h-full flex-col border-r border-border bg-card text-foreground', isMobile ? 'w-full' : 'w-72')}>
      <div className="flex items-center justify-between gap-2 px-4 pb-3 pt-5">
        <div className="flex min-w-0 items-center gap-3">
          <BrandMark size="md" />
          <div className="min-w-0">
            <p className="text-base font-extrabold leading-none tracking-tight text-foreground">Vauxtra</p>
            <p className="mt-1 truncate text-[11px] leading-snug text-muted-foreground">{t('layout.tagline')}</p>
          </div>
        </div>
        {isMobile
          ? onClose && <IconButton label={t('layout.menu.close')} icon={<X />} onClick={onClose} className="h-8 w-8 text-muted-foreground" />
          : onToggleCollapse && (
              <IconButton
                label={t('layout.sidebar.collapse')}
                icon={<PanelLeftClose />}
                onClick={onToggleCollapse}
                tooltip
                className="h-8 w-8 text-muted-foreground"
              />
            )}
      </div>

      <div className="px-4 pb-3">
        <button
          type="button"
          onClick={onOpenPalette}
          className="flex h-9 w-full items-center gap-2 rounded-xl border border-border bg-background px-3 text-sm text-muted-foreground shadow-sm transition-colors hover:border-primary/40 hover:text-foreground"
        >
          <Search aria-hidden="true" className="h-4 w-4 shrink-0" />
          <span className="flex-1 truncate text-left">{t('layout.search')}</span>
          <span aria-hidden="true" className="inline-flex items-center gap-0.5">
            <Kbd size="sm">{modKey}</Kbd>
            <Kbd size="sm">K</Kbd>
          </span>
        </button>
      </div>

      <nav aria-label={t('layout.nav')} className="flex-1 space-y-5 overflow-y-auto px-3 py-2">
        {groups.map((group) => (
          <div key={group.title}>
            <p className="mb-1.5 px-3 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">{group.title}</p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isItemActive(item);
                return (
                  <li key={item.href}>
                    <Link
                      to={item.href}
                      onClick={onNavigate}
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'group relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors',
                        active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                      )}
                    >
                      {active && <span aria-hidden="true" className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />}
                      <span
                        className={cn(
                          'shrink-0 transition-colors [&>svg]:h-[18px] [&>svg]:w-[18px]',
                          active ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground',
                        )}
                      >
                        {item.icon}
                      </span>
                      <span className="flex-1 truncate">{item.label}</span>
                      {item.alert && <TriangleAlert aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-destructive" />}
                      {item.badge !== undefined && (
                        <Badge size="sm" tone={item.tone ?? 'neutral'}>
                          {formatBadge(item.badge)}
                        </Badge>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="space-y-3 border-t border-border px-4 py-3">
        <div className="flex items-center gap-1">
          <IconButton label={themeLabel} icon={THEME_ICONS[theme]} onClick={toggleTheme} tooltip className="h-8 w-8 shrink-0 text-muted-foreground" />
          <IconButton label={t('layout.shortcuts')} icon={<Keyboard />} onClick={onOpenShortcuts} tooltip className="h-8 w-8 shrink-0 text-muted-foreground" />
          <Select
            size="sm"
            aria-label={t('layout.language')}
            value={lang}
            onChange={(e) => setLang(e.target.value as Lang)}
            wrapperClassName="min-w-0 flex-1"
            className="h-8 rounded-lg bg-background text-xs"
          >
            {SUPPORTED_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.flag} {l.label}
              </option>
            ))}
          </Select>
          {authStatus?.auth_required && (
            <IconButton label={t('nav.signout')} icon={<LogOut />} onClick={signOut} tooltip className="h-8 w-8 shrink-0 text-muted-foreground" />
          )}
        </div>
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <span className="font-semibold">{t('common.version')}</span>
            <span className="truncate rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">{version}</span>
          </span>
          <span className="flex shrink-0 items-center">
            <FooterLink href={DOCS_URL} label={t('nav.docs')} icon={<BookOpen />} />
            <FooterLink href={BUG_URL} label={t('nav.report_bug')} icon={<Bug />} />
            <FooterLink href={GITHUB_URL} label={t('nav.github')} icon={<ExternalLink />} />
          </span>
        </div>
      </div>
    </aside>
  );
}
