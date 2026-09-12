import { useMemo } from 'react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import type { ServiceStatus } from '@/types/api';
import { STATUS_LABEL_KEY, type UptimeBucket, type UptimeSummary } from './uptime';

/**
 * The 24 h heat strip: one cell per half hour, drawn as plain SVG.
 *
 * No chart library and no raw colours — every cell is `fill="currentColor"` over a token
 * text colour, so the strip follows the theme like everything else. The SVG is stretched
 * with `preserveAspectRatio="none"`, which is why the geometry below is unitless: the
 * viewBox is the layout, the CSS height is the size.
 */

const BAR = 2;
const GAP = 1;
const HEIGHT = 10;

const CELL_CLASS: Record<ServiceStatus, string> = {
  ok: 'text-success',
  error: 'text-destructive',
  unknown: 'text-warning',
};

export interface UptimeStripProps {
  summary: UptimeSummary;
  /** Accessible name — say what the strip shows, e.g. "99.2 % up over 24 h". */
  label: string;
  /**
   * What the dashed box says when there is nothing to draw. Defaults to "no check in the
   * last 24 hours", which is a claim about the infrastructure: a caller whose history
   * request failed knows nothing of the sort and must pass its own sentence.
   */
  emptyLabel?: string;
  /** Tailwind height of the strip; the width always fills its container. */
  heightClass?: string;
  className?: string;
}

/** A 24-hour availability heat strip; empty cells are the hours the scheduler did not run. */
export function UptimeStrip({ summary, label, emptyLabel, heightClass = 'h-5', className }: UptimeStripProps) {
  const t = useT();
  const { formatTime, formatDateTime } = useFormat();

  const width = summary.buckets.length * (BAR + GAP) - GAP;

  const cellTitle = useMemo(
    () =>
      (bucket: UptimeBucket): string => {
        const when = `${formatTime(new Date(bucket.start))} – ${formatTime(new Date(bucket.end))}`;
        if (bucket.status === null) return t('monitoring.uptime.cell_empty', { range: when });
        return t('monitoring.uptime.cell', {
          range: when,
          status: t(STATUS_LABEL_KEY[bucket.status]),
          count: bucket.total,
        });
      },
    [formatTime, t],
  );

  if (summary.total === 0) {
    // `title` because the span truncates: the box is one table cell wide and the sentence
    // is longer than that on most locales.
    const empty = emptyLabel ?? t('monitoring.uptime.no_history');
    return (
      <div
        className={cn('flex items-center rounded-md border border-dashed border-border px-2', heightClass, className)}
        title={empty}
      >
        <span className="truncate text-[10px] text-muted-foreground">{empty}</span>
      </div>
    );
  }

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${width} ${HEIGHT}`}
      preserveAspectRatio="none"
      className={cn('w-full overflow-visible', heightClass, className)}
    >
      <title>{label}</title>
      {summary.buckets.map((bucket, index) => (
        <rect
          key={bucket.start}
          x={index * (BAR + GAP)}
          y={0}
          width={BAR}
          height={HEIGHT}
          rx={0.8}
          fill="currentColor"
          className={cn(
            'transition-opacity duration-150 hover:opacity-70',
            bucket.status === null ? 'text-muted-foreground/25' : CELL_CLASS[bucket.status],
          )}
        >
          <title>{cellTitle(bucket)}</title>
        </rect>
      ))}
      {summary.lastAt !== null && (
        <desc>{t('monitoring.uptime.last_event', { when: formatDateTime(new Date(summary.lastAt)) })}</desc>
      )}
    </svg>
  );
}
