import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  CornerDownLeft,
  GitMerge,
  Globe,
  Languages,
  LayoutDashboard,
  LayoutTemplate,
  Monitor,
  Moon,
  Plus,
  Search,
  ShieldCheck,
  Sun,
} from 'lucide-react';
import { api } from '@/api/client';
import { SUPPORTED_LANGUAGES, useI18n } from '@/i18n';
import { useTheme, type Theme } from '@/theme';
import { cn } from '@/lib/cn';
import { Kbd } from '@/components/ui';
import { SETTINGS_TABS } from '@/components/features/settings/tabs';
import { useModalDialog } from '@/hooks/useModalDialog';
import { slugId, useScrollLock } from '@/components/ui/_internal';
import type { Provider, Service } from '@/types/api';

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

type GroupKey = 'pages' | 'services' | 'providers' | 'actions';

interface PaletteItem {
  id: string;
  group: GroupKey;
  label: string;
  description?: string;
  /** Extra words the search may match (never displayed). */
  keywords?: string;
  icon: ReactNode;
  run: () => void;
}

const GROUP_ORDER: GroupKey[] = ['pages', 'services', 'providers', 'actions'];
const THEME_ICONS: Record<Theme, ReactNode> = { light: <Sun />, dark: <Moon />, system: <Monitor /> };

/** Same rule as the backend's `_service_public_hostname`: the list rows carry no `public_host`. */
function publicHost(s: Service): string {
  const tunnel = (s.tunnel_hostname ?? '').trim();
  if (s.expose_mode === 'tunnel' && tunnel) return tunnel.toLowerCase();
  return `${s.subdomain ?? ''}.${s.domain ?? ''}`.replace(/^\.+|\.+$/g, '').toLowerCase();
}

function normalize(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

/** Case/accent-insensitive: a substring of the haystack, or every query word a prefix of some haystack word. */
function rank(query: string, item: PaletteItem): number {
  const label = normalize(item.label);
  if (label.startsWith(query)) return 0;
  const hay = normalize(`${item.label} ${item.description ?? ''} ${item.keywords ?? ''}`);
  if (hay.includes(query)) return 1;
  const words = hay.split(/[^a-z0-9]+/).filter(Boolean);
  const tokens = query.split(/\s+/).filter(Boolean);
  return tokens.every((token) => words.some((w) => w.startsWith(token))) ? 2 : -1;
}

/** Ctrl/⌘ K: jump to any page, service or provider, or run a quick action. */
export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  if (!open) return null;
  return <PaletteDialog onClose={onClose} />;
}

/** Mounted only while open, so every opening starts from an empty query and the first item. */
function PaletteDialog({ onClose }: { onClose: () => void }) {
  const { t, lang, setLang } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const dialogRef = useModalDialog<HTMLDivElement>(true, onClose);
  useScrollLock(true);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

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

  // `useModalDialog` focuses the box so a screen reader reads its name; the field is the
  // better landing spot here and this effect runs after the hook's.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const items = useMemo<PaletteItem[]>(() => {
    const go = (href: string) => () => navigate(href);
    const settingsLabel = t('nav.settings');
    // The palette's Settings destinations are the Settings tabs themselves, read from the one
    // authoritative list. Hardcoding them here is how `migration` and `backup` -- two aliases of
    // the same `data` tab -- survived the tab rewrite while `data` and `security` had no entry.
    const settingsTabs: PaletteItem[] = SETTINGS_TABS.map((tab) => {
      const Icon = tab.icon;
      return {
        id: `page:/settings?tab=${tab.id}`,
        group: 'pages',
        label: t(tab.labelKey),
        description: settingsLabel,
        keywords: `settings ${tab.id}`,
        icon: <Icon />,
        run: go(`/settings?tab=${tab.id}`),
      };
    });

    const pages: PaletteItem[] = [
      { id: 'page:/', group: 'pages', label: t('nav.dashboard'), icon: <LayoutDashboard />, run: go('/') },
      { id: 'page:/services', group: 'pages', label: t('nav.services'), icon: <Globe />, run: go('/services') },
      { id: 'page:/providers', group: 'pages', label: t('nav.providers'), icon: <GitMerge />, run: go('/providers') },
      { id: 'page:/templates', group: 'pages', label: t('nav.templates'), icon: <LayoutTemplate />, run: go('/templates') },
      { id: 'page:/monitoring', group: 'pages', label: t('nav.monitoring'), icon: <Activity />, run: go('/monitoring') },
      { id: 'page:/certificates', group: 'pages', label: t('nav.certificates'), icon: <ShieldCheck />, run: go('/certificates') },
      ...settingsTabs,
    ];

    const serviceItems: PaletteItem[] = (services ?? []).map((s) => ({
      id: `service:${s.id}`,
      group: 'services',
      label: publicHost(s),
      description: `${s.forward_scheme}://${s.target_ip}:${s.target_port}`,
      keywords: `${s.subdomain} ${s.domain} ${s.tunnel_hostname ?? ''} ${s.status}`,
      icon: <Globe />,
      run: go(`/services?edit=${s.id}`),
    }));

    const providerItems: PaletteItem[] = (providers ?? []).map((p) => ({
      id: `provider:${p.id}`,
      group: 'providers',
      label: p.name,
      description: [p.type, p.url].filter(Boolean).join(' · '),
      icon: <GitMerge />,
      run: go(`/providers?edit=${p.id}`),
    }));

    const actions: PaletteItem[] = [
      { id: 'action:create-service', group: 'actions', label: t('palette.action.create_service'), icon: <Plus />, run: go('/services?new=1') },
      { id: 'action:add-provider', group: 'actions', label: t('palette.action.add_provider'), icon: <Plus />, run: go('/providers?new=1') },
      {
        id: 'action:toggle-theme',
        group: 'actions',
        label: t('palette.action.toggle_theme'),
        description: t(`layout.theme.${theme}`),
        keywords: 'theme dark light system',
        icon: THEME_ICONS[theme],
        run: toggleTheme,
      },
      ...SUPPORTED_LANGUAGES.filter((l) => l.code !== lang).map(
        (l): PaletteItem => ({
          id: `action:lang-${l.code}`,
          group: 'actions',
          label: `${t('palette.action.switch_language')} · ${l.label}`,
          description: `${l.flag} ${l.code.toUpperCase()}`,
          keywords: `language ${l.code} ${l.label}`,
          icon: <Languages />,
          run: () => setLang(l.code),
        }),
      ),
    ];

    return [...pages, ...serviceItems, ...providerItems, ...actions];
  }, [t, lang, setLang, theme, toggleTheme, navigate, services, providers]);

  const grouped = useMemo(() => {
    const q = normalize(query.trim());
    const limit = q ? 8 : 6;
    return GROUP_ORDER.map((key) => {
      let list = items.filter((i) => i.group === key);
      if (q) {
        list = list
          .map((item) => ({ item, score: rank(q, item) }))
          .filter((x) => x.score >= 0)
          .sort((a, b) => a.score - b.score)
          .map((x) => x.item);
      }
      if (key === 'services' || key === 'providers') list = list.slice(0, limit);
      return { key, items: list };
    }).filter((g) => g.items.length > 0);
  }, [items, query]);

  const flat = useMemo(() => grouped.flatMap((g) => g.items), [grouped]);
  const activeIndex = flat.length === 0 ? -1 : Math.min(active, flat.length - 1);
  const activeItem = activeIndex >= 0 ? flat[activeIndex] : undefined;
  const optionId = (item: PaletteItem) => `${listId}-opt-${slugId(item.id)}`;

  useEffect(() => {
    if (activeIndex < 0) return;
    listRef.current?.querySelector(`[data-index="${activeIndex}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  const select = (item: PaletteItem) => {
    onClose();
    item.run();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const n = flat.length;
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (n) setActive((i) => (Math.min(i, n - 1) + 1) % n);
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (n) setActive((i) => (Math.min(i, n - 1) - 1 + n) % n);
        break;
      case 'Home':
        e.preventDefault();
        setActive(0);
        break;
      case 'End':
        e.preventDefault();
        setActive(Math.max(n - 1, 0));
        break;
      case 'Enter':
        e.preventDefault();
        if (activeItem) select(activeItem);
        break;
      default:
        break;
    }
  };

  const groupTitles: Record<GroupKey, string> = {
    pages: t('palette.group.pages'),
    services: t('palette.group.services'),
    providers: t('palette.group.providers'),
    actions: t('palette.group.actions'),
  };

  let runningIndex = -1;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[10vh] sm:pt-[14vh]">
      <div aria-hidden="true" onClick={onClose} className="absolute inset-0 bg-background/60 backdrop-blur-xs animate-in fade-in animate-duration-150" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('palette.title')}
        className={cn(
          'relative flex w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-elevated outline-hidden',
          'animate-in fade-in zoom-in-95 animate-duration-200',
        )}
      >
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeItem ? optionId(activeItem) : undefined}
            aria-autocomplete="list"
            aria-label={t('palette.title')}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder={t('palette.placeholder')}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            className="h-12 flex-1 border-0 bg-transparent p-0 text-sm text-foreground shadow-none placeholder:text-muted-foreground focus:border-0 focus:ring-0 focus-visible:ring-0"
          />
          <Kbd size="sm" className="hidden sm:inline-flex">
            Esc
          </Kbd>
        </div>

        <div ref={listRef} id={listId} role="listbox" aria-label={t('palette.title')} className="max-h-[60vh] overflow-y-auto p-2">
          {flat.length === 0 ? (
            <div className="px-3 py-10 text-center text-sm text-muted-foreground">{t('palette.empty')}</div>
          ) : (
            grouped.map((group) => (
              <div key={group.key} role="group" aria-labelledby={`${listId}-${group.key}`} className="pb-1">
                <div id={`${listId}-${group.key}`} role="presentation" className="px-3 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  {groupTitles[group.key]}
                </div>
                {group.items.map((item) => {
                  runningIndex += 1;
                  const index = runningIndex;
                  const isActive = index === activeIndex;
                  return (
                    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus -- combobox option; the keys live on the input, which names this one via aria-activedescendant
                    <div
                      key={item.id}
                      id={optionId(item)}
                      role="option"
                      aria-selected={isActive}
                      data-index={index}
                      onMouseEnter={() => setActive(index)}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => select(item)}
                      className={cn(
                        'flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors',
                        isActive ? 'bg-primary/10' : 'hover:bg-accent',
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground [&>svg]:h-4 [&>svg]:w-4',
                          isActive && 'bg-primary/15 text-primary',
                        )}
                      >
                        {item.icon}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-foreground">{item.label}</span>
                        {item.description && <span className="block truncate text-xs text-muted-foreground">{item.description}</span>}
                      </span>
                      {isActive && <CornerDownLeft aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-muted/40 px-4 py-2 text-[11px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Kbd size="sm">↑</Kbd>
            <Kbd size="sm">↓</Kbd>
            <span className="ml-0.5">{t('palette.hint.navigate')}</span>
          </span>
          <span className="inline-flex items-center gap-1">
            <Kbd size="sm">↵</Kbd>
            <span className="ml-0.5">{t('palette.hint.select')}</span>
          </span>
          <span className="inline-flex items-center gap-1">
            <Kbd size="sm">Esc</Kbd>
            <span className="ml-0.5">{t('palette.hint.close')}</span>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
