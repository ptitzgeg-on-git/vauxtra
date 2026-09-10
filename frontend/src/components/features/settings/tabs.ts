/**
 * The Settings sub-navigation: one entry per tab, grouped the way the sidebar groups them.
 *
 * `?tab=` is a public contract -- the sidebar, the command palette, the dashboard widgets
 * and the service form all deep-link into it -- so every historical value keeps resolving:
 * `tags` / `environments` land on `taxonomy`, and `dataops` / `migration` / `backup` land on
 * `data`.
 */
import {
  Bell,
  Database,
  FileTerminal,
  Globe,
  Key,
  Languages,
  Settings2,
  ShieldCheck,
  Tag,
  type LucideIcon,
} from 'lucide-react';

export type SettingsTabId =
  | 'general'
  | 'language'
  | 'dns'
  | 'taxonomy'
  | 'apikeys'
  | 'webhooks'
  | 'data'
  | 'logs'
  | 'security';

export type SettingsGroupId = 'preferences' | 'organization' | 'data' | 'security';

export interface SettingsTabDef {
  id: SettingsTabId;
  /** i18n key of the label. */
  labelKey: string;
  icon: LucideIcon;
  group: SettingsGroupId;
}

export const SETTINGS_TABS: readonly SettingsTabDef[] = [
  { id: 'general', labelKey: 'settings.tab.general', icon: Settings2, group: 'preferences' },
  { id: 'language', labelKey: 'settings.tab.language', icon: Languages, group: 'preferences' },
  { id: 'dns', labelKey: 'settings.tab.dns', icon: Globe, group: 'organization' },
  { id: 'taxonomy', labelKey: 'settings.tab.taxonomy', icon: Tag, group: 'organization' },
  { id: 'data', labelKey: 'settings.tab.data', icon: Database, group: 'data' },
  { id: 'logs', labelKey: 'settings.tab.logs', icon: FileTerminal, group: 'data' },
  { id: 'apikeys', labelKey: 'settings.tab.apikeys', icon: Key, group: 'security' },
  { id: 'webhooks', labelKey: 'settings.tab.webhooks', icon: Bell, group: 'security' },
  { id: 'security', labelKey: 'settings.tab.security', icon: ShieldCheck, group: 'security' },
];

export const SETTINGS_GROUPS: readonly { id: SettingsGroupId; labelKey: string }[] = [
  { id: 'preferences', labelKey: 'settings.group.preferences' },
  { id: 'organization', labelKey: 'settings.group.organization' },
  { id: 'data', labelKey: 'settings.group.data' },
  { id: 'security', labelKey: 'settings.group.security' },
];

/** Every value `?tab=` has ever accepted, including the aliases. */
export const VALID_TABS: readonly string[] = [
  'general',
  'language',
  'dns',
  'taxonomy',
  'tags',
  'environments',
  'apikeys',
  'webhooks',
  'dataops',
  'migration',
  'backup',
  'data',
  'logs',
  'security',
];

const TAB_ALIASES: Record<string, SettingsTabId> = {
  tags: 'taxonomy',
  environments: 'taxonomy',
  dataops: 'data',
  migration: 'data',
  backup: 'data',
};

/** The tab to show for a raw `?tab=` value; unknown values fall back to `general`. */
export function resolveSettingsTab(raw: string | null | undefined): SettingsTabId {
  const value = raw ?? '';
  if (!VALID_TABS.includes(value)) return 'general';
  if (value in TAB_ALIASES) return TAB_ALIASES[value];
  return value as SettingsTabId;
}
