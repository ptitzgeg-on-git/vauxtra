/**
 * The formatters from `lib/format.ts`, bound to the active language and the `timezone`
 * setting -- so a page writes `formatDateTime(service.last_checked)` and nothing else.
 *
 * The zone comes from `GET /settings` (key `timezone`, IANA name, empty = follow the
 * browser). The query shares its key with the Settings and Monitoring pages, so saving a
 * new zone there re-renders every date in the app without a reload.
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { LOCALE_TAGS, useI18n } from '@/i18n';
import {
  formatDate,
  formatDateTime,
  formatDays,
  formatLatency,
  formatNumber,
  formatPercent,
  formatRelative,
  formatTime,
  parseBackendTimestamp,
  resolveTimeZone,
  type DateInput,
  type DateStyle,
} from '@/lib/format';

export interface Formatters {
  /** BCP-47 tag of the active language, e.g. `fr-FR`. */
  locale: string;
  /** IANA zone every date is rendered in. */
  timeZone: string;
  formatDateTime: (value: DateInput, style?: DateStyle) => string;
  formatDate: (value: DateInput, style?: DateStyle) => string;
  formatTime: (value: DateInput, style?: DateStyle) => string;
  /** `3 min ago`, `in 2 days`, `now`. Pass `now` to keep a list consistent within one render. */
  formatRelative: (value: DateInput, now?: Date | number) => string;
  formatNumber: (value: number | null | undefined, options?: Intl.NumberFormatOptions) => string;
  formatDays: (value: number | null | undefined, display?: 'long' | 'short' | 'narrow') => string;
  /** From a value already in percent, the way `/api/health` reports disk usage. */
  formatPercent: (value: number | null | undefined, maximumFractionDigits?: number) => string;
  formatLatency: (ms: number | null | undefined) => string;
  parseBackendTimestamp: typeof parseBackendTimestamp;
}

export function useFormat(): Formatters {
  const { lang } = useI18n();
  const locale = LOCALE_TAGS[lang] || 'en-US';

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<Record<string, string>>('/settings'),
    staleTime: 5 * 60_000,
  });
  const timeZone = resolveTimeZone(settings?.timezone);

  return useMemo<Formatters>(
    () => ({
      locale,
      timeZone,
      formatDateTime: (value, style) => formatDateTime(value, { locale, timeZone, style }),
      formatDate: (value, style) => formatDate(value, { locale, timeZone, style }),
      formatTime: (value, style) => formatTime(value, { locale, timeZone, style }),
      formatRelative: (value, now) => formatRelative(value, { locale, now }),
      formatNumber: (value, options) => formatNumber(value, locale, options),
      formatDays: (value, display) => formatDays(value, locale, display),
      formatPercent: (value, maximumFractionDigits) => formatPercent(value, locale, maximumFractionDigits),
      formatLatency: (ms) => formatLatency(ms, locale),
      parseBackendTimestamp,
    }),
    [locale, timeZone],
  );
}
