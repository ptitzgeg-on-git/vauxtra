import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

/** A shimmering placeholder block; size it with width/height classes. */
export const Skeleton = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function Skeleton(
  { className, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={cn(
        'rounded-lg bg-muted animate-shimmer',
        'bg-[linear-gradient(90deg,rgb(var(--vx-muted))_0%,rgb(var(--vx-accent))_50%,rgb(var(--vx-muted))_100%)] bg-size-[200%_100%]',
        className,
      )}
      {...rest}
    />
  );
});

export interface SkeletonTextProps extends HTMLAttributes<HTMLDivElement> {
  lines?: number;
}

/** A paragraph of skeleton lines; the last one is shorter, like real text. */
export function SkeletonText({ lines = 3, className, ...rest }: SkeletonTextProps) {
  return (
    <div aria-hidden="true" className={cn('space-y-2', className)} {...rest}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn('h-3.5', i === lines - 1 && lines > 1 ? 'w-2/3' : 'w-full')} />
      ))}
    </div>
  );
}

/** A card-shaped skeleton: icon, title, two lines. */
export function SkeletonCard({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn('rounded-2xl border border-border bg-card shadow-card p-5 sm:p-6 space-y-4', className)}
      {...rest}
    >
      <div className="flex items-center gap-3">
        <Skeleton className="h-10 w-10 rounded-xl" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      </div>
      <SkeletonText lines={2} />
    </div>
  );
}

export interface SkeletonRowProps extends HTMLAttributes<HTMLDivElement> {
  /** Number of cells; the first one is wider. */
  columns?: number;
}

/** A table-row skeleton with `columns` cells. */
export function SkeletonRow({ columns = 4, className, ...rest }: SkeletonRowProps) {
  return (
    <div aria-hidden="true" className={cn('flex items-center gap-4 px-4 py-3', className)} {...rest}>
      {Array.from({ length: columns }, (_, i) => (
        <Skeleton key={i} className={cn('h-4', i === 0 ? 'flex-2' : 'flex-1')} />
      ))}
    </div>
  );
}
