import type { ComponentType } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Settings as SettingsIcon } from 'lucide-react';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { ErrorBoundary, PageHeader, Tab, TabList, TabPanel, Tabs, buttonVariants } from '@/components/ui';
import {
  ApiKeysTab,
  DataTab,
  DnsTab,
  GeneralTab,
  LanguageTab,
  LogsTab,
  SecurityTab,
  SETTINGS_GROUPS,
  SETTINGS_TABS,
  TaxonomyTab,
  WebhooksTab,
  resolveSettingsTab,
  type SettingsTabId,
} from '@/components/features/settings';

const PANELS: Record<SettingsTabId, ComponentType> = {
  general: GeneralTab,
  language: LanguageTab,
  dns: DnsTab,
  taxonomy: TaxonomyTab,
  apikeys: ApiKeysTab,
  webhooks: WebhooksTab,
  data: DataTab,
  logs: LogsTab,
  security: SecurityTab,
};

/**
 * The Settings page shell: header, the tab navigation (a grouped sidebar on desktop, a
 * scrollable tab strip on smaller screens) and the active panel. `?tab=` stays the single
 * source of truth so every deep link keeps working; each panel owns its own queries.
 */
export function Settings() {
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = resolveSettingsTab(searchParams.get('tab'));
  const Panel = PANELS[activeTab];

  const go = (value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', value);
    setSearchParams(next);
  };

  const hrefFor = (id: SettingsTabId) => {
    const next = new URLSearchParams(searchParams);
    next.set('tab', id);
    return `/settings?${next.toString()}`;
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <PageHeader
        icon={<SettingsIcon />}
        eyebrow={t('settings.eyebrow')}
        title={t('settings.title')}
        description={t('settings.description')}
        actions={
          <Link to="/" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            <ArrowLeft aria-hidden="true" className="h-4 w-4" />
            {t('common.back')}
          </Link>
        }
      />

      <div className="lg:grid lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-8">
        <aside className="hidden lg:block">
          <nav aria-label={t('settings.nav_aria')} className="sticky top-6 space-y-5">
            {SETTINGS_GROUPS.map((group) => {
              const tabs = SETTINGS_TABS.filter((tab) => tab.group === group.id);
              if (tabs.length === 0) return null;
              return (
                <div key={group.id}>
                  <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {t(group.labelKey)}
                  </p>
                  <ul className="space-y-0.5">
                    {tabs.map((tab) => {
                      const Icon = tab.icon;
                      const active = tab.id === activeTab;
                      return (
                        <li key={tab.id}>
                          <Link
                            to={hrefFor(tab.id)}
                            aria-current={active ? 'page' : undefined}
                            className={cn(
                              'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150',
                              'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
                              active
                                ? 'bg-primary/10 text-primary'
                                : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                            )}
                          >
                            <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                            <span className="truncate">{t(tab.labelKey)}</span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </nav>
        </aside>

        <Tabs value={activeTab} onValueChange={go} className="min-w-0 gap-5">
          <TabList aria-label={t('settings.nav_aria')} className="lg:hidden">
            {SETTINGS_TABS.map((tab) => {
              const Icon = tab.icon;
              return (
                <Tab key={tab.id} value={tab.id} icon={<Icon />}>
                  {t(tab.labelKey)}
                </Tab>
              );
            })}
          </TabList>
          <TabPanel key={activeTab} value={activeTab}>
            <ErrorBoundary resetKey={activeTab}>
              <Panel />
            </ErrorBoundary>
          </TabPanel>
        </Tabs>
      </div>
    </div>
  );
}
