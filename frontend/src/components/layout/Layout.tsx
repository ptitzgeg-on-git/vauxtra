import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Menu, Search, ShieldAlert } from 'lucide-react';
import { api } from '@/api/client';
import { anyDialogOpen } from '@/hooks/useModalDialog';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { Button, Drawer, IconButton, InlineAlert } from '@/components/ui';
import { BrandMark } from './BrandMark';
import { CommandPalette } from './CommandPalette';
import { ShortcutsHelp } from './ShortcutsHelp';
import { Sidebar } from './Sidebar';

const STORAGE_KEY = 'vauxtra_sidebar_collapsed';

/** `g` then one of these keys navigates; the pair must land within 1.2 s. */
const CHORDS: Record<string, string> = {
  d: '/',
  e: '/services',
  p: '/providers',
  s: '/settings?tab=general',
  m: '/monitoring',
  c: '/certificates',
  t: '/templates',
  ',': '/settings',
};

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
}

/**
 * The wizard offers a *Skip* button on the password step, and nothing ever mentioned it
 * again: an instance answering every request with the admin scope looked exactly like a
 * protected one. This is the only place in the interface that says otherwise, so it is
 * deliberately not dismissible.
 */
function OpenAccessBanner() {
  const t = useT();
  const navigate = useNavigate();
  const { data } = useQuery<{ auth_mode?: string }>({
    queryKey: ['auth-status'],
    queryFn: () => api.get('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });

  if (data?.auth_mode !== 'open') return null;

  return (
    <InlineAlert
      tone="warning"
      banner
      role="status"
      icon={<ShieldAlert />}
      title={t('security.open_access.title')}
      action={
        <Button variant="outline" size="sm" onClick={() => navigate('/settings?tab=apikeys')}>
          {t('security.open_access.action')}
        </Button>
      }
    >
      {t('security.open_access.body')}
    </InlineAlert>
  );
}

/** The application shell: sidebar, mobile header + drawer, main region, palette and shortcuts. */
export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const t = useT();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(collapsed));
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  useEffect(() => {
    let awaitingSecondKey = false;
    let resetTimer: number | null = null;

    const resetCombo = () => {
      awaitingSecondKey = false;
      if (resetTimer !== null) {
        window.clearTimeout(resetTimer);
        resetTimer = null;
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      // Ctrl/⌘ K opens the palette from anywhere, a text field included — but never over
      // another dialog. The palette navigates, and navigating out from under a half-filled
      // modal leaves it floating over a page it has nothing to do with. It still closes the
      // palette it opened: that is the one dialog it is allowed to answer over.
      if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((v) => (v ? false : !anyDialogOpen()));
        return;
      }
      if (isTypingTarget(event.target)) return;
      // Everything below either navigates or opens a dialog, and one is already open: `g` then
      // `s` used to leave a modal standing over the services page while its form belonged to
      // the dashboard, and `?` stacked the shortcut sheet on top of it.
      if (anyDialogOpen()) return;

      if (event.key === '?' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        setShortcutsOpen(true);
        return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;

      const key = event.key.toLowerCase();

      if (!awaitingSecondKey) {
        if (key === 'g') {
          awaitingSecondKey = true;
          resetTimer = window.setTimeout(resetCombo, 1200);
        }
        return;
      }

      const target = CHORDS[key];
      if (target) {
        navigate(target);
        event.preventDefault();
      }
      resetCombo();
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      resetCombo();
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [navigate]);

  const closeMobile = () => setMobileOpen(false);
  const openPalette = () => setPaletteOpen(true);
  const openShortcuts = () => setShortcutsOpen(true);

  return (
    <div className="flex h-screen bg-background text-foreground">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-xl focus:border focus:border-border focus:bg-card focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:shadow-elevated"
      >
        {t('layout.skip_to_content')}
      </a>

      {/* Desktop sidebar */}
      <div
        className={cn(
          'relative z-10 hidden shrink-0 transition-[width] duration-200 ease-out-expo md:block',
          collapsed ? 'w-16' : 'w-72',
        )}
      >
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((v) => !v)}
          onOpenPalette={openPalette}
          onOpenShortcuts={openShortcuts}
        />
      </div>

      {/* Mobile header */}
      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-card/85 px-3 backdrop-blur-md md:hidden">
        <BrandMark size="sm" withWordmark />
        <div className="flex items-center gap-1">
          <IconButton label={t('layout.search')} icon={<Search />} onClick={openPalette} />
          <IconButton label={t('layout.menu.open')} icon={<Menu />} onClick={() => setMobileOpen(true)} aria-expanded={mobileOpen} />
        </div>
      </header>

      {/* Mobile navigation drawer */}
      <Drawer
        open={mobileOpen}
        onClose={closeMobile}
        side="left"
        size="sm"
        hideClose
        aria-label={t('layout.mobile_nav')}
        bodyClassName="overflow-hidden p-0"
      >
        <Sidebar
          isMobile
          onClose={closeMobile}
          onNavigate={closeMobile}
          onOpenPalette={() => {
            closeMobile();
            openPalette();
          }}
          onOpenShortcuts={() => {
            closeMobile();
            openShortcuts();
          }}
        />
      </Drawer>

      {/* Main content */}
      <main id="main" tabIndex={-1} className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden outline-none focus-visible:ring-0">
        <div className="pt-14 md:pt-0">
          <OpenAccessBanner />
        </div>
        <div key={location.pathname} className="animate-in fade-in-up animate-duration-300 p-4 sm:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ShortcutsHelp open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  );
}
