/**
 * General tab: appearance, time zone, automatic health checks, auto-reconcile and the WAN
 * detection policy. Every card is its own small form and posts only the keys it owns to
 * `POST /api/settings`, so saving one card never rewrites another.
 */
import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Check, Clock, Globe, HeartPulse, Monitor, Moon, Palette, RefreshCw, Sun } from 'lucide-react';
import { api } from '@/api/client';
import { LOCALE_TAGS, useI18n, useT } from '@/i18n';
import { useTheme, type Theme } from '@/theme';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import { browserTimeZone, formatDateTime, isValidTimeZone } from '@/lib/format';
import { Button, Field, Input, InlineAlert, SkeletonCard, Switch, Textarea } from '@/components/ui';
import type { AppSettings, SettingsSaveResult } from '@/types/api';
import { SettingsSection } from './SettingsSection';

/** Mirrors `_SETTING_RANGES` in `app/api/settings.py`; the API refuses anything outside. */
const RANGES = {
  check_interval: { min: 1, max: 1440 },
  monitoring_retention_days: { min: 1, max: 365 },
  log_retention_days: { min: 1, max: 365 },
  auto_reconcile_interval: { min: 1, max: 1440 },
  webhook_retry_retention_days: { min: 1, max: 90 },
} as const;

const DEFAULTS = {
  check_interval: '5',
  monitoring_retention_days: '14',
  log_retention_days: '30',
  auto_reconcile_interval: '60',
  webhook_retry_retention_days: '7',
  public_target_sources: 'https://api.ipify.org\nhttps://ifconfig.me/ip\nhttps://icanhazip.com',
  public_target_timeout: '2.0',
  public_target_priority: 'server_public_ip,proxy_provider_host,current',
} as const;

type SaveArgs = { values: Record<string, string>; successMessage: string };

function clampInt(raw: string, min: number, max: number, fallback: number): number {
  const n = Number.parseInt(raw, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function GeneralTab() {
  const t = useT();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery<AppSettings>({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/settings'),
  });
  const settings = settingsQuery.data;

  const save = useMutation({
    mutationFn: ({ values }: SaveArgs) => api.post<SettingsSaveResult>('/settings', values),
    onSuccess: (data, { successMessage }) => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      toast.success(successMessage);
      if (data?.ignored?.length) {
        toast(t('settings.general.ignored_keys', { keys: data.ignored.join(', ') }));
      }
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.general.policy_save_failed'))),
  });

  const onSave = (values: Record<string, string>, successMessage: string) =>
    save.mutate({ values, successMessage });

  // Remount a card when the server values *it* edits change, so its local state re-seeds
  // without an effect. One key per card, over that card's own fields only.
  //
  // It used to be a single key joined over all ten fields, which is not what the sentence
  // above ever described: the four cards share one `save`, whose `onSuccess` invalidates
  // ['settings']. Saving any one of them refetched, one field changed, the joined key
  // changed -- and all four remounted, so the three the operator had not saved yet were
  // reset to the server values. No toast, no warning; whatever was still typed in them
  // was simply gone.
  const seedOf = (...fields: Array<string | undefined>) => (settings ? fields.join('|') : 'none');
  const tzSeed = seedOf(settings?.timezone);
  const healthSeed = seedOf(
    settings?.check_interval,
    settings?.monitoring_retention_days,
    settings?.log_retention_days,
  );
  const reconcileSeed = seedOf(
    settings?.auto_reconcile_enabled,
    settings?.auto_reconcile_interval,
    settings?.webhook_retry_retention_days,
  );
  const wanSeed = seedOf(
    settings?.public_target_sources,
    settings?.public_target_timeout,
    settings?.public_target_priority,
  );

  return (
    <div className="space-y-6">
      <AppearanceCard />

      {settingsQuery.isLoading ? (
        <>
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </>
      ) : settingsQuery.isError ? (
        <InlineAlert
          tone="danger"
          title={t('settings.general.load_failed')}
          action={
            <Button variant="outline" size="sm" onClick={() => settingsQuery.refetch()}>
              {t('ui.error.retry')}
            </Button>
          }
        >
          {translateApiError(settingsQuery.error, t, t('common.error'))}
        </InlineAlert>
      ) : (
        <>
          <TimezoneCard key={`tz-${tzSeed}`} current={settings?.timezone ?? ''} saving={save.isPending} onSave={onSave} />
          <HealthChecksCard key={`hc-${healthSeed}`} settings={settings ?? {}} saving={save.isPending} onSave={onSave} />
          <AutoReconcileCard key={`ar-${reconcileSeed}`} settings={settings ?? {}} saving={save.isPending} onSave={onSave} />
          <WanPolicyCard key={`wan-${wanSeed}`} settings={settings ?? {}} saving={save.isPending} onSave={onSave} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Appearance
// ---------------------------------------------------------------------------

const THEME_OPTIONS: { value: Theme; icon: typeof Sun; labelKey: string }[] = [
  { value: 'light', icon: Sun, labelKey: 'settings.general.theme_light' },
  { value: 'dark', icon: Moon, labelKey: 'settings.general.theme_dark' },
  { value: 'system', icon: Monitor, labelKey: 'settings.general.theme_system' },
];

function AppearanceCard() {
  const t = useT();
  const { theme, resolvedTheme, setTheme } = useTheme();
  const groupId = useId();

  return (
    <SettingsSection
      icon={<Palette />}
      title={t('settings.general.appearance_title')}
      description={t('settings.general.appearance_desc')}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p id={groupId} className="text-sm font-medium text-foreground">
            {t('settings.general.theme_mode')}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('settings.general.theme_active', {
              mode: t(resolvedTheme === 'dark' ? 'settings.general.theme_dark' : 'settings.general.theme_light'),
            })}
            {theme === 'system' && <span> · {t('settings.general.theme_system_hint')}</span>}
          </p>
        </div>
        <div role="radiogroup" aria-labelledby={groupId} className="inline-flex items-center gap-1 rounded-xl bg-muted p-1">
          {THEME_OPTIONS.map((option) => {
            const Icon = option.icon;
            const selected = theme === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setTheme(option.value)}
                className={cn(
                  'inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold transition-colors duration-150',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  selected ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                {t(option.labelKey)}
              </button>
            );
          })}
        </div>
      </div>
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------
// Time zone
// ---------------------------------------------------------------------------

/** All IANA zones the runtime knows, or `null` when `Intl.supportedValuesOf` is missing. */
function listTimeZones(): string[] | null {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  if (typeof intl.supportedValuesOf !== 'function') return null;
  try {
    const zones = intl.supportedValuesOf('timeZone');
    return zones.includes('UTC') ? zones : ['UTC', ...zones];
  } catch {
    return null;
  }
}

const BROWSER_ZONE = '';

function normalizeZoneQuery(text: string): string {
  return text.trim().toLowerCase().replace(/[_\s]+/g, ' ');
}

interface CardProps {
  saving: boolean;
  onSave: (values: Record<string, string>, successMessage: string) => void;
}

function TimezoneCard({ current, saving, onSave }: CardProps & { current: string }) {
  const t = useT();
  const { lang } = useI18n();
  const locale = LOCALE_TAGS[lang] || 'en-US';
  const zones = useMemo(() => listTimeZones(), []);
  const browserZone = useMemo(() => browserTimeZone(), []);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const [value, setValue] = useState(current);
  const [query, setQuery] = useState(current);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  // The clock under the field ticks so the preview shows the zone, not a frozen instant.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const candidate = query.trim();
  const effective = candidate || browserZone;
  const previewZone = effective;
  const valid = candidate === '' || isValidTimeZone(candidate);
  const dirty = effective !== current.trim();

  const options = useMemo(() => {
    if (!zones) return [];
    const needle = normalizeZoneQuery(query);
    const all = [BROWSER_ZONE, ...zones];
    if (!needle || query.trim() === value) return all;
    return all.filter((zone) => zone !== BROWSER_ZONE && normalizeZoneQuery(zone).includes(needle));
  }, [zones, query, value]);

  const labelFor = (zone: string) =>
    zone === BROWSER_ZONE ? t('settings.general.timezone_browser', { zone: browserZone }) : zone;

  const choose = (zone: string) => {
    setValue(zone);
    setQuery(zone);
    setOpen(false);
    setActiveIndex(0);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!zones) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) setOpen(true);
      setActiveIndex((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      if (open && options[activeIndex] !== undefined) {
        e.preventDefault();
        choose(options[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    // The backend refuses an empty zone, so "browser" is stored as the browser's own zone.
    onSave({ timezone: effective }, t('settings.general.timezone_saved'));
  };

  const activeId = open && options[activeIndex] !== undefined ? `${listId}-opt-${activeIndex}` : undefined;

  return (
    <form onSubmit={submit}>
      <SettingsSection
        icon={<Clock />}
        title={t('settings.general.timezone_title')}
        description={t('settings.general.timezone_desc')}
        footer={
          <Button type="submit" loading={saving} disabled={!valid || !dirty}>
            {t('settings.general.save_timezone')}
          </Button>
        }
      >
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Field
            label={t('settings.general.timezone_label')}
            hint={zones ? t('settings.general.timezone_hint') : t('settings.general.timezone_free_hint')}
            error={!valid ? t('settings.general.timezone_invalid') : undefined}
          >
            <div className="relative">
              <Input
                ref={inputRef}
                role={zones ? 'combobox' : undefined}
                aria-expanded={zones ? open : undefined}
                aria-controls={zones ? listId : undefined}
                aria-activedescendant={activeId}
                aria-autocomplete={zones ? 'list' : undefined}
                autoComplete="off"
                spellCheck={false}
                value={query}
                placeholder={t('settings.general.timezone_placeholder', { zone: browserZone })}
                leftIcon={<Globe />}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActiveIndex(0);
                  if (zones) setOpen(true);
                }}
                onFocus={() => {
                  if (zones) setOpen(true);
                }}
                onBlur={() => setOpen(false)}
                onKeyDown={onKeyDown}
              />
              {zones && open && (
                <ul
                  id={listId}
                  role="listbox"
                  aria-label={t('settings.general.timezone_list_aria')}
                  onMouseDown={(e) => e.preventDefault()}
                  className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-elevated animate-in fade-in zoom-in-95"
                >
                  {options.length === 0 && (
                    <li className="px-3 py-2 text-sm text-muted-foreground">{t('settings.general.timezone_no_match')}</li>
                  )}
                  {options.map((zone, index) => {
                    const selected = zone === value;
                    const active = index === activeIndex;
                    return (
                      <li
                        key={zone || '__browser__'}
                        id={`${listId}-opt-${index}`}
                        role="option"
                        aria-selected={selected}
                        onMouseEnter={() => setActiveIndex(index)}
                        onClick={() => choose(zone)}
                        className={cn(
                          'flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-1.5 text-sm',
                          active ? 'bg-accent text-foreground' : 'text-foreground/90',
                          zone === BROWSER_ZONE && 'font-medium',
                        )}
                      >
                        <span className="truncate">{labelFor(zone)}</span>
                        {selected && <Check aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </Field>

          <div className="rounded-xl border border-border bg-muted/40 p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t('settings.general.timezone_preview')}
            </p>
            <p className="mt-1 text-base font-semibold tabular-nums text-foreground">
              {valid ? formatDateTime(now, { locale, timeZone: previewZone, style: 'long' }) : '—'}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {candidate ? previewZone : t('settings.general.timezone_browser', { zone: browserZone })}
            </p>
          </div>
        </div>
      </SettingsSection>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Health checks
// ---------------------------------------------------------------------------

function HealthChecksCard({ settings, saving, onSave }: CardProps & { settings: AppSettings }) {
  const t = useT();
  const serverInterval = settings.check_interval ?? '0';
  const [enabled, setEnabled] = useState(serverInterval !== '' && serverInterval !== '0');
  const [interval, setIntervalValue] = useState(
    serverInterval && serverInterval !== '0' ? serverInterval : DEFAULTS.check_interval,
  );
  const [monitoringDays, setMonitoringDays] = useState(
    settings.monitoring_retention_days || DEFAULTS.monitoring_retention_days,
  );
  const [logDays, setLogDays] = useState(settings.log_retention_days || DEFAULTS.log_retention_days);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = RANGES;
    onSave(
      {
        check_interval: enabled
          ? String(clampInt(interval, r.check_interval.min, r.check_interval.max, Number(DEFAULTS.check_interval)))
          : '0',
        monitoring_retention_days: String(
          clampInt(
            monitoringDays,
            r.monitoring_retention_days.min,
            r.monitoring_retention_days.max,
            Number(DEFAULTS.monitoring_retention_days),
          ),
        ),
        log_retention_days: String(
          clampInt(logDays, r.log_retention_days.min, r.log_retention_days.max, Number(DEFAULTS.log_retention_days)),
        ),
      },
      t('settings.general.monitoring_saved'),
    );
  };

  return (
    <form onSubmit={submit}>
      <SettingsSection
        icon={<HeartPulse />}
        title={t('settings.general.health_title')}
        description={t('settings.general.health_desc')}
        actions={
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            label={t('settings.general.health_enable')}
          />
        }
        footer={
          <Button type="submit" loading={saving}>
            {t('settings.general.save_monitoring')}
          </Button>
        }
      >
        <div className="grid gap-4 md:grid-cols-3">
          <Field
            label={t('settings.general.health_interval')}
            hint={t('settings.general.range_minutes', { min: RANGES.check_interval.min, max: RANGES.check_interval.max })}
          >
            <Input
              type="number"
              inputMode="numeric"
              min={RANGES.check_interval.min}
              max={RANGES.check_interval.max}
              value={interval}
              disabled={!enabled}
              onChange={(e) => setIntervalValue(e.target.value)}
              className="tabular-nums"
            />
          </Field>
          <Field
            label={t('settings.general.retention_monitoring')}
            hint={t('settings.general.range_days', {
              min: RANGES.monitoring_retention_days.min,
              max: RANGES.monitoring_retention_days.max,
            })}
          >
            <Input
              type="number"
              inputMode="numeric"
              min={RANGES.monitoring_retention_days.min}
              max={RANGES.monitoring_retention_days.max}
              value={monitoringDays}
              onChange={(e) => setMonitoringDays(e.target.value)}
              className="tabular-nums"
            />
          </Field>
          <Field
            label={t('settings.general.retention_logs')}
            hint={t('settings.general.range_days', { min: RANGES.log_retention_days.min, max: RANGES.log_retention_days.max })}
          >
            <Input
              type="number"
              inputMode="numeric"
              min={RANGES.log_retention_days.min}
              max={RANGES.log_retention_days.max}
              value={logDays}
              onChange={(e) => setLogDays(e.target.value)}
              className="tabular-nums"
            />
          </Field>
        </div>
        {!enabled && (
          <InlineAlert tone="info">{t('settings.general.health_disabled_hint')}</InlineAlert>
        )}
      </SettingsSection>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Auto-reconcile
// ---------------------------------------------------------------------------

function AutoReconcileCard({ settings, saving, onSave }: CardProps & { settings: AppSettings }) {
  const t = useT();
  const [enabled, setEnabled] = useState(settings.auto_reconcile_enabled === 'true');
  const [interval, setIntervalValue] = useState(
    settings.auto_reconcile_interval && settings.auto_reconcile_interval !== '0'
      ? settings.auto_reconcile_interval
      : DEFAULTS.auto_reconcile_interval,
  );
  const [retryDays, setRetryDays] = useState(
    settings.webhook_retry_retention_days || DEFAULTS.webhook_retry_retention_days,
  );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSave(
      {
        auto_reconcile_enabled: enabled ? 'true' : 'false',
        auto_reconcile_interval: enabled
          ? String(
              clampInt(
                interval,
                RANGES.auto_reconcile_interval.min,
                RANGES.auto_reconcile_interval.max,
                Number(DEFAULTS.auto_reconcile_interval),
              ),
            )
          : '0',
        webhook_retry_retention_days: String(
          clampInt(
            retryDays,
            RANGES.webhook_retry_retention_days.min,
            RANGES.webhook_retry_retention_days.max,
            Number(DEFAULTS.webhook_retry_retention_days),
          ),
        ),
      },
      t('settings.general.reconcile_saved'),
    );
  };

  return (
    <form onSubmit={submit}>
      <SettingsSection
        icon={<RefreshCw />}
        title={t('settings.general.reconcile_title')}
        description={t('settings.general.reconcile_desc')}
        actions={
          <Switch checked={enabled} onCheckedChange={setEnabled} label={t('settings.general.reconcile_enable')} />
        }
        footer={
          <Button type="submit" loading={saving}>
            {t('settings.general.save_reconcile')}
          </Button>
        }
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Field
            label={t('settings.general.reconcile_interval')}
            hint={t('settings.general.range_minutes', {
              min: RANGES.auto_reconcile_interval.min,
              max: RANGES.auto_reconcile_interval.max,
            })}
          >
            <Input
              type="number"
              inputMode="numeric"
              min={RANGES.auto_reconcile_interval.min}
              max={RANGES.auto_reconcile_interval.max}
              value={interval}
              disabled={!enabled}
              onChange={(e) => setIntervalValue(e.target.value)}
              className="tabular-nums"
            />
          </Field>
          <Field
            label={t('settings.general.webhook_retry_retention')}
            hint={t('settings.general.webhook_retry_retention_hint', {
              min: RANGES.webhook_retry_retention_days.min,
              max: RANGES.webhook_retry_retention_days.max,
            })}
          >
            <Input
              type="number"
              inputMode="numeric"
              min={RANGES.webhook_retry_retention_days.min}
              max={RANGES.webhook_retry_retention_days.max}
              value={retryDays}
              onChange={(e) => setRetryDays(e.target.value)}
              className="tabular-nums"
            />
          </Field>
        </div>
      </SettingsSection>
    </form>
  );
}

// ---------------------------------------------------------------------------
// WAN detection policy
// ---------------------------------------------------------------------------

const PRIORITY_VALUES = ['server_public_ip', 'proxy_provider_host', 'current'] as const;

function WanPolicyCard({ settings, saving, onSave }: CardProps & { settings: AppSettings }) {
  const t = useT();
  const [sources, setSources] = useState(settings.public_target_sources || DEFAULTS.public_target_sources);
  const [timeout, setTimeoutValue] = useState(settings.public_target_timeout || DEFAULTS.public_target_timeout);
  const [priority, setPriority] = useState(settings.public_target_priority || DEFAULTS.public_target_priority);

  const priorityInvalid = priority
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean)
    .some((p) => !(PRIORITY_VALUES as readonly string[]).includes(p));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (priorityInvalid) return;
    onSave(
      {
        public_target_sources: sources.trim(),
        public_target_timeout: timeout.trim(),
        public_target_priority: priority.trim(),
      },
      t('settings.general.policy_saved'),
    );
  };

  return (
    <form onSubmit={submit}>
      <SettingsSection
        icon={<Globe />}
        title={t('settings.general.wan_title')}
        description={t('settings.general.wan_desc')}
        footer={
          <Button type="submit" loading={saving} disabled={priorityInvalid}>
            {t('settings.general.save_wan')}
          </Button>
        }
      >
        <Field label={t('settings.general.wan_sources')} hint={t('settings.general.wan_sources_hint')}>
          <Textarea
            rows={4}
            value={sources}
            spellCheck={false}
            onChange={(e) => setSources(e.target.value)}
            className="font-mono text-xs"
          />
        </Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label={t('settings.general.wan_timeout')} hint={t('settings.general.wan_timeout_hint')}>
            <Input
              type="number"
              inputMode="decimal"
              min="0.5"
              max="10"
              step="0.1"
              value={timeout}
              onChange={(e) => setTimeoutValue(e.target.value)}
              className="tabular-nums"
            />
          </Field>
          <Field
            label={t('settings.general.wan_priority')}
            hint={t('settings.general.wan_priority_hint', { values: PRIORITY_VALUES.join(', ') })}
            error={priorityInvalid ? t('settings.general.wan_priority_invalid') : undefined}
          >
            <Input
              value={priority}
              spellCheck={false}
              onChange={(e) => setPriority(e.target.value)}
              className="font-mono text-xs"
            />
          </Field>
        </div>
      </SettingsSection>
    </form>
  );
}
