import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { toneClasses, type Tone } from './tone';

export interface ChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  selected?: boolean;
  tone?: Tone;
  icon?: ReactNode;
  /** Small count to the right of the label. */
  count?: number | string;
  size?: 'sm' | 'md';
}

/** A selectable filter chip (`aria-pressed`); selected chips take the tone colour. */
export const Chip = forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { selected = false, tone = 'primary', icon, count, size = 'md', className, children, ...rest },
  ref,
) {
  const c = toneClasses(tone);
  return (
    <button
      ref={ref}
      type="button"
      aria-pressed={selected}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border font-medium whitespace-nowrap transition-colors duration-150',
        size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-sm',
        selected ? cn(c.bg, c.text, c.border) : 'border-border bg-card text-muted-foreground hover:bg-accent hover:text-foreground',
        'disabled:opacity-50 disabled:pointer-events-none',
        className,
      )}
      {...rest}
    >
      {icon && <span aria-hidden="true" className="inline-flex shrink-0 [&>svg]:h-3.5 [&>svg]:w-3.5">{icon}</span>}
      {children}
      {count !== undefined && (
        <span
          className={cn(
            'rounded-full px-1.5 text-[10px] font-semibold tabular-nums leading-4',
            selected ? 'bg-card/70 text-foreground' : 'bg-muted text-muted-foreground',
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
});

export interface ChipGroupProps extends HTMLAttributes<HTMLDivElement> {
  /** Accessible name of the group — what the chips filter. */
  label: string;
}

/** A wrapping row of `Chip`s exposed as a named group. */
export const ChipGroup = forwardRef<HTMLDivElement, ChipGroupProps>(function ChipGroup(
  { label, className, ...rest },
  ref,
) {
  return <div ref={ref} role="group" aria-label={label} className={cn('flex flex-wrap items-center gap-2', className)} {...rest} />;
});
