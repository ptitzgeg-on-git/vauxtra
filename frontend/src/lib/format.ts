/**
 * Date, number and duration formatting.
 *
 * The backend stores every timestamp as naive UTC text -- SQLite's `datetime('now')`,
 * `YYYY-MM-DD HH:MM:SS`, sometimes with fractional seconds or a `T` separator, never a
 * zone. A bare `new Date(text)` reads that as *local* time and shifts every timestamp by
 * the viewer's offset, so `parseBackendTimestamp` is the only door a backend string goes
 * through. Strings that already carry a zone (`Z`, `+02:00`) pass through untouched.
 *
 * Every `Intl` call is wrapped: an unknown time zone or locale falls back to a plain
 * ISO-like string instead of throwing in the middle of a render. Anything that cannot be
 * parsed renders as an em dash.
 *
 * Pages should not call these directly with a hand-picked locale; `useFormat()` binds them
 * to the active language and the `timezone` setting.
 */

export const EM_DASH = '—';

export type DateInput = Date | string | number | null | undefined;
export type DateStyle = 'short' | 'medium' | 'long';

export interface FormatDateOptions {
  /** BCP-47 tag, e.g. `fr-FR`. */
  locale: string;
  /** IANA zone, e.g. `Europe/Paris`. Empty or invalid falls back to the browser zone. */
  timeZone?: string | null;
  style?: DateStyle;
}

export interface FormatRelativeOptions {
  locale: string;
  /** Reference instant; defaults to the moment of the call. */
  now?: Date | number;
  /** `auto` yields "yesterday"/"now", `always` yields "1 day ago"/"in 0 seconds". */
  numeric?: 'auto' | 'always';
  style?: 'long' | 'short' | 'narrow';
}

// `2026-09-05`, `2026-09-05 14:03:09`, `2026-09-05T14:03:09.123456` -- no zone at the end.
const NAIVE_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?)?$/;

/**
 * A `Date` from a backend timestamp, or `null` when there is nothing to show.
 *
 * Naive strings are read as UTC. Strings with an explicit zone, epoch numbers and `Date`
 * instances pass through (an invalid `Date` becomes `null` so callers never format NaN).
 */
export function parseBackendTimestamp(value: DateInput): Date | null {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === 'number') {
    const fromNumber = new Date(value);
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber;
  }

  const text = String(value).trim();
  if (!text) return null;

  const naive = NAIVE_RE.exec(text);
  if (naive) {
    const [, year, month, day, hour = '0', minute = '0', second = '0', fraction = ''] = naive;
    const millis = fraction ? Number(fraction.padEnd(3, '0').slice(0, 3)) : 0;
    const date = new Date(
      Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second), millis),
    );
    return Number.isNaN(date.getTime()) ? null : date;
  }

  // Anything else (ISO with zone, RFC 2822, ...) is handed to the engine as-is.
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

// ---------------------------------------------------------------------------
// Time zones
// ---------------------------------------------------------------------------

const zoneCache = new Map<string, boolean>();

/** Whether `Intl` accepts this IANA zone name. Cached; never throws. */
export function isValidTimeZone(timeZone: string | null | undefined): boolean {
  if (!timeZone) return false;
  const cached = zoneCache.get(timeZone);
  if (cached !== undefined) return cached;
  let ok = false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone });
    ok = true;
  } catch {
    ok = false;
  }
  zoneCache.set(timeZone, ok);
  return ok;
}

/** The zone the browser itself runs in, `UTC` when even that cannot be read. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/**
 * The zone to format in: the `timezone` setting when it is a real zone, otherwise the
 * browser's. An empty setting means "follow the browser", which is also the default.
 */
export function resolveTimeZone(setting: string | null | undefined): string {
  const candidate = (setting ?? '').trim();
  return candidate && isValidTimeZone(candidate) ? candidate : browserTimeZone();
}

// ---------------------------------------------------------------------------
// Fallbacks
// ---------------------------------------------------------------------------

function pad(n: number, width = 2): string {
  return String(n).padStart(width, '0');
}

/** `2026-09-05 14:03 UTC` -- what is shown when `Intl` refuses the locale or zone. */
export function toIsoLike(date: Date, parts: 'datetime' | 'date' | 'time' = 'datetime'): string {
  const d = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
  const t = `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
  if (parts === 'date') return d;
  if (parts === 'time') return `${t} UTC`;
  return `${d} ${t} UTC`;
}

/**
 * Run a formatter, retrying without the time zone and then without the locale before
 * giving up. `Intl` throws `RangeError` on an unknown zone or a malformed tag; neither
 * should take a page down.
 */
function tryIntl(
  build: (locale: string, timeZone: string | undefined) => string,
  locale: string,
  timeZone: string | undefined,
  fallback: () => string,
): string {
  const attempts: Array<[string, string | undefined]> = [
    [locale, timeZone],
    [locale, undefined],
    ['en-US', timeZone],
    ['en-US', undefined],
  ];
  for (const [loc, tz] of attempts) {
    try {
      return build(loc, tz);
    } catch {
      // next attempt
    }
  }
  return fallback();
}

const DATE_TIME_STYLES: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  short: { dateStyle: 'short', timeStyle: 'short' },
  medium: { dateStyle: 'medium', timeStyle: 'short' },
  long: { dateStyle: 'long', timeStyle: 'medium' },
};

const DATE_STYLES: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  short: { dateStyle: 'short' },
  medium: { dateStyle: 'medium' },
  long: { dateStyle: 'long' },
};

const TIME_STYLES: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  short: { timeStyle: 'short' },
  medium: { timeStyle: 'short' },
  long: { timeStyle: 'medium' },
};

function formatWith(
  input: DateInput,
  styles: Record<DateStyle, Intl.DateTimeFormatOptions>,
  fallbackParts: 'datetime' | 'date' | 'time',
  { locale, timeZone, style = 'medium' }: FormatDateOptions,
): string {
  const date = parseBackendTimestamp(input);
  if (!date) return EM_DASH;
  const zone = timeZone && isValidTimeZone(timeZone) ? timeZone : undefined;
  return tryIntl(
    (loc, tz) => new Intl.DateTimeFormat(loc, { ...styles[style], timeZone: tz }).format(date),
    locale,
    zone,
    () => toIsoLike(date, fallbackParts),
  );
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

/** Date and time, e.g. `5 Sept 2026, 14:03`. Invalid input renders as an em dash. */
export function formatDateTime(input: DateInput, options: FormatDateOptions): string {
  return formatWith(input, DATE_TIME_STYLES, 'datetime', options);
}

/** Date only, e.g. `5 Sept 2026`. */
export function formatDate(input: DateInput, options: FormatDateOptions): string {
  return formatWith(input, DATE_STYLES, 'date', options);
}

/** Time only, e.g. `14:03`. */
export function formatTime(input: DateInput, options: FormatDateOptions): string {
  return formatWith(input, TIME_STYLES, 'time', options);
}

type RelativeUnit = 'year' | 'month' | 'week' | 'day' | 'hour' | 'minute' | 'second';

const RELATIVE_UNITS: Array<[RelativeUnit, number]> = [
  ['year', 365 * 24 * 60 * 60 * 1000],
  ['month', 30 * 24 * 60 * 60 * 1000],
  ['week', 7 * 24 * 60 * 60 * 1000],
  ['day', 24 * 60 * 60 * 1000],
  ['hour', 60 * 60 * 1000],
  ['minute', 60 * 1000],
];

/** The unit and signed amount a difference in milliseconds reads best in. */
export function pickRelativeUnit(diffMs: number): { unit: RelativeUnit; value: number } {
  const abs = Math.abs(diffMs);
  for (const [unit, size] of RELATIVE_UNITS) {
    if (abs >= size) return { unit, value: Math.round(diffMs / size) };
  }
  // Under 45 seconds is "now" -- a monitoring page refreshes faster than that reads.
  if (abs < 45 * 1000) return { unit: 'second', value: 0 };
  return { unit: 'second', value: Math.round(diffMs / 1000) };
}

/**
 * Localized relative time -- `3 min ago`, `in 2 days`, `now`. Past is negative, future
 * positive. Falls back to the absolute date when `Intl.RelativeTimeFormat` is unavailable.
 */
export function formatRelative(input: DateInput, options: FormatRelativeOptions): string {
  const date = parseBackendTimestamp(input);
  if (!date) return EM_DASH;
  const { locale, now = Date.now(), numeric = 'auto', style = 'long' } = options;
  const nowMs = typeof now === 'number' ? now : now.getTime();
  const { unit, value } = pickRelativeUnit(date.getTime() - nowMs);

  if (typeof Intl.RelativeTimeFormat !== 'function') {
    return formatDateTime(date, { locale, style: 'short' });
  }
  return tryIntl(
    (loc) => new Intl.RelativeTimeFormat(loc, { numeric, style }).format(value, unit),
    locale,
    undefined,
    () => toIsoLike(date),
  );
}

// ---------------------------------------------------------------------------
// Numbers
// ---------------------------------------------------------------------------

/** Grouped, localized number. Non-finite input renders as an em dash. */
export function formatNumber(
  value: number | null | undefined,
  locale: string,
  options?: Intl.NumberFormatOptions,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return tryIntl(
    (loc) => new Intl.NumberFormat(loc, options).format(value),
    locale,
    undefined,
    () => String(value),
  );
}

/** A count of days with its unit, e.g. `12 days` / `12 jours` / `12 d` (short). */
export function formatDays(
  value: number | null | undefined,
  locale = 'en-US',
  display: 'long' | 'short' | 'narrow' = 'long',
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  const rounded = Math.round(value);
  return tryIntl(
    (loc) =>
      new Intl.NumberFormat(loc, {
        style: 'unit',
        unit: 'day',
        unitDisplay: display,
        maximumFractionDigits: 0,
      }).format(rounded),
    locale,
    undefined,
    () => `${rounded} d`,
  );
}

/** `42.5 %` from a value already expressed in percent (the way `/api/health` reports disk). */
export function formatPercent(
  value: number | null | undefined,
  locale: string,
  maximumFractionDigits = 1,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return tryIntl(
    (loc) =>
      new Intl.NumberFormat(loc, { style: 'percent', maximumFractionDigits }).format(value / 100),
    locale,
    undefined,
    () => `${value}%`,
  );
}

/** Milliseconds as `12 ms` / `1.2 s`; `null` (a check that never connected) is an em dash. */
export function formatLatency(ms: number | null | undefined, locale: string): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return EM_DASH;
  if (ms >= 1000) {
    return tryIntl(
      (loc) =>
        new Intl.NumberFormat(loc, {
          style: 'unit',
          unit: 'second',
          unitDisplay: 'short',
          maximumFractionDigits: 1,
        }).format(ms / 1000),
      locale,
      undefined,
      () => `${(ms / 1000).toFixed(1)} s`,
    );
  }
  return tryIntl(
    (loc) =>
      new Intl.NumberFormat(loc, {
        style: 'unit',
        unit: 'millisecond',
        unitDisplay: 'short',
        maximumFractionDigits: 0,
      }).format(ms),
    locale,
    undefined,
    () => `${Math.round(ms)} ms`,
  );
}
