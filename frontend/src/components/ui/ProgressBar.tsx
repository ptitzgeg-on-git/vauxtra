import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';
import { toneClasses, type Tone } from './tone';

export interface ProgressBarProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** Current value; clamped to `[0, max]`. Ignored when `indeterminate`. */
  value?: number;
  max?: number;
  tone?: Tone;
  size?: 'sm' | 'md';
  /** Accessible name — required unless `aria-labelledby` is given. */
  label?: string;
  /** Sliding bar when the total is unknown. */
  indeterminate?: boolean;
  /** Shows the percentage to the right. */
  showValue?: boolean;
}

/** A horizontal progress bar with `role="progressbar"`; tone colours the fill. */
export const ProgressBar = forwardRef<HTMLDivElement, ProgressBarProps>(function ProgressBar(
  { value = 0, max = 100, tone = 'primary', size = 'md', label, indeterminate = false, showValue = false, className, ...rest },
  ref,
) {
  const safeMax = max > 0 ? max : 100;
  const clamped = Math.min(Math.max(value, 0), safeMax);
  const percent = Math.round((clamped / safeMax) * 100);
  const c = toneClasses(tone);

  return (
    <div ref={ref} className={cn('flex items-center gap-3', className)} {...rest}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={safeMax}
        aria-valuenow={indeterminate ? undefined : clamped}
        className={cn('relative w-full overflow-hidden rounded-full bg-muted', size === 'sm' ? 'h-1.5' : 'h-2.5')}
      >
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-300 ease-out-expo',
            c.dot,
            indeterminate && 'absolute inset-y-0 w-1/3 animate-progress-indeterminate',
          )}
          style={indeterminate ? undefined : { width: `${percent}%` }}
        />
      </div>
      {showValue && !indeterminate && (
        <span className="text-xs font-medium text-muted-foreground tabular-nums w-9 text-right">{percent}%</span>
      )}
    </div>
  );
});
