import { useId } from 'react';
import { cn } from '@/lib/cn';

export interface BrandMarkProps {
  size?: 'sm' | 'md' | 'lg';
  /** Shows the "Vauxtra" wordmark after the mark. */
  withWordmark?: boolean;
  className?: string;
}

const SIZES = {
  sm: { box: 'h-7 w-7', text: 'text-sm' },
  md: { box: 'h-9 w-9', text: 'text-base' },
  lg: { box: 'h-12 w-12', text: 'text-xl' },
} as const;

/** The Vauxtra mark: a primary-to-glow gradient square with a cut "V", plus an optional wordmark. */
export function BrandMark({ size = 'md', withWordmark = false, className }: BrandMarkProps) {
  const gradientId = `vx-brand-${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  return (
    <span className={cn('inline-flex select-none items-center gap-2.5', className)}>
      <svg
        viewBox="0 0 32 32"
        className={cn('shrink-0 drop-shadow-sm', SIZES[size].box)}
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" style={{ stopColor: 'rgb(var(--vx-primary))' }} />
            <stop offset="100%" style={{ stopColor: 'rgb(var(--vx-primary-glow))' }} />
          </linearGradient>
        </defs>
        <rect width="32" height="32" rx="9" fill={`url(#${gradientId})`} />
        <path
          d="M8 9.5h4.4l3.6 9.6 3.6-9.6H24l-6.2 14h-3.6z"
          style={{ fill: 'rgb(var(--vx-primary-foreground))' }}
        />
        <path d="M18.4 9.5H24l-2.2 5h-5.6z" style={{ fill: 'rgb(var(--vx-primary-foreground))', opacity: 0.55 }} />
      </svg>
      {withWordmark && (
        <span className={cn('font-extrabold tracking-tight text-foreground leading-none', SIZES[size].text)}>Vauxtra</span>
      )}
    </span>
  );
}
