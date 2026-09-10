import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

export interface KbdProps extends HTMLAttributes<HTMLElement> {
  size?: 'sm' | 'md';
}

/** A keycap: `<Kbd>⌘</Kbd><Kbd>K</Kbd>`. */
export const Kbd = forwardRef<HTMLElement, KbdProps>(function Kbd({ className, size = 'md', ...rest }, ref) {
  return (
    <kbd
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center rounded-md border border-border bg-muted font-mono font-medium text-muted-foreground',
        'shadow-[inset_0_-1px_0_0_rgb(var(--vx-border))]',
        size === 'sm' ? 'h-5 min-w-5 px-1 text-[10px]' : 'h-6 min-w-6 px-1.5 text-[11px]',
        className,
      )}
      {...rest}
    />
  );
});
