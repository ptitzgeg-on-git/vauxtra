import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { Minus, TrendingDown, TrendingUp } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { Skeleton } from './Skeleton';
import { toneClasses, type Tone } from './tone';

export interface StatTrend {
  /** Already formatted (e.g. "+12 %"). */
  value: ReactNode;
  direction: 'up' | 'down' | 'flat';
  /** Colour of the trend; defaults to success for up, danger for down, neutral for flat. */
  tone?: Tone;
}

export interface StatCardProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onClick'> {
  label: ReactNode;
  value: ReactNode;
  /** Small line under the value. */
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: Tone;
  trend?: StatTrend;
  loading?: boolean;
  /** Makes the whole card a button. */
  onClick?: () => void;
}

const TREND_ICON = { up: <TrendingUp />, down: <TrendingDown />, flat: <Minus /> } as const;
const TREND_TONE: Record<StatTrend['direction'], Tone> = { up: 'success', down: 'danger', flat: 'neutral' };

/** A KPI tile: label, big tabular value, hint, tinted icon and an optional trend. */
export const StatCard = forwardRef<HTMLDivElement, StatCardProps>(function StatCard(
  { label, value, hint, icon, tone = 'primary', trend, loading = false, onClick, className, ...rest },
  ref,
) {
  const t = useT();
  const c = toneClasses(tone);
  const trendTone = trend ? toneClasses(trend.tone ?? TREND_TONE[trend.direction]) : null;

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
        {icon && (
          <span aria-hidden="true" className={cn('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl [&>svg]:h-4 [&>svg]:w-4', c.bg, c.text)}>
            {icon}
          </span>
        )}
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <p className="text-3xl font-bold tracking-tight text-foreground tabular-nums leading-none">{value}</p>
        )}
        {trend && !loading && trendTone && (
          <span className={cn('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums', trendTone.bg, trendTone.text)}>
            <span aria-hidden="true" className="inline-flex [&>svg]:h-3.5 [&>svg]:w-3.5">{TREND_ICON[trend.direction]}</span>
            <span className="sr-only">{t(`ui.trend.${trend.direction}`)} </span>
            {trend.value}
          </span>
        )}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-3 w-28" />
      ) : (
        hint && <p className="mt-1.5 text-xs text-muted-foreground">{hint}</p>
      )}
    </>
  );

  const surface = 'rounded-2xl border border-border bg-card shadow-card p-5';

  if (onClick) {
    return (
      <div ref={ref} className={cn('relative', className)} {...rest}>
        <button
          type="button"
          onClick={onClick}
          aria-busy={loading || undefined}
          className={cn(
            surface,
            'w-full text-left transition-[transform,box-shadow,border-color] duration-200 ease-out-expo',
            'hover:-translate-y-0.5 hover:shadow-elevated hover:border-primary/30',
          )}
        >
          {body}
        </button>
      </div>
    );
  }

  return (
    <div ref={ref} aria-busy={loading || undefined} className={cn(surface, className)} {...rest}>
      {body}
    </div>
  );
});
