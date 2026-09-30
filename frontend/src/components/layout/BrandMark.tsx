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

/**
 * The Vauxtra mark: two routes converging on one node, drawn as a "V" on a flat primary tile.
 * The right-hand route stops short of the node -- the missing link of the tagline.
 * Same geometry as `public/favicon.svg` and `docs/assets/logo.svg`; change all three together.
 */
export function BrandMark({ size = 'md', withWordmark = false, className }: BrandMarkProps) {
  return (
    <span className={cn('inline-flex select-none items-center gap-2.5', className)}>
      <svg
        viewBox="0 0 32 32"
        className={cn('shrink-0', SIZES[size].box)}
        aria-hidden="true"
        focusable="false"
      >
        <rect width="32" height="32" rx="7" style={{ fill: 'rgb(var(--vx-primary))' }} />
        <g style={{ stroke: 'rgb(var(--vx-primary-fg))' }} strokeWidth="2.6" strokeLinecap="round" fill="none">
          <path d="M8.5 9 16 23" />
          <path d="M23.5 9 19.6 16.3" />
        </g>
        <g style={{ fill: 'rgb(var(--vx-primary-fg))' }}>
          <circle cx="8.5" cy="9" r="2.5" />
          <circle cx="23.5" cy="9" r="2.5" />
          <circle cx="16" cy="23" r="3" />
        </g>
      </svg>
      {withWordmark && (
        <span className={cn('font-semibold tracking-tight text-foreground leading-none', SIZES[size].text)}>Vauxtra</span>
      )}
    </span>
  );
}
