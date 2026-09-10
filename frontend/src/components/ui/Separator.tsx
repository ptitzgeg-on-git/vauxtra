import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

export interface SeparatorProps extends HTMLAttributes<HTMLDivElement> {
  orientation?: 'horizontal' | 'vertical';
  /** Purely visual (default); set `false` to expose it as a real `separator` to assistive tech. */
  decorative?: boolean;
}

/** A 1px token-coloured rule, horizontal or vertical. */
export const Separator = forwardRef<HTMLDivElement, SeparatorProps>(function Separator(
  { orientation = 'horizontal', decorative = true, className, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      role={decorative ? 'none' : 'separator'}
      aria-orientation={decorative ? undefined : orientation}
      className={cn('shrink-0 bg-border', orientation === 'horizontal' ? 'h-px w-full' : 'w-px self-stretch', className)}
      {...rest}
    />
  );
});
