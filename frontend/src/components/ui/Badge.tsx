import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { toneClasses, type Tone } from './tone';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  size?: 'sm' | 'md';
  /** Leading status dot in the tone colour. */
  dot?: boolean;
  /** Leading icon (lucide element); sized automatically. */
  icon?: ReactNode;
  /** Solid fill instead of the soft tint. */
  solid?: boolean;
}

/** Small status pill; tone colours it, `dot` or `icon` give it a non-colour signal. */
export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  { tone = 'neutral', size = 'md', dot = false, icon, solid = false, className, children, ...rest },
  ref,
) {
  const c = toneClasses(tone);
  return (
    <span
      ref={ref}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border font-semibold whitespace-nowrap align-middle tabular-nums',
        size === 'sm' ? 'h-5 px-1.5 text-[10px]' : 'h-6 px-2 text-xs',
        solid ? cn(c.solid, 'border-transparent') : cn(c.bg, c.text, c.border),
        className,
      )}
      {...rest}
    >
      {dot && <span aria-hidden="true" className={cn('h-1.5 w-1.5 rounded-full shrink-0', solid ? 'bg-current' : c.dot)} />}
      {icon && <span aria-hidden="true" className="inline-flex shrink-0 [&>svg]:h-3 [&>svg]:w-3">{icon}</span>}
      {children}
    </span>
  );
});
