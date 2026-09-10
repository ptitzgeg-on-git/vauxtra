/* eslint-disable react-refresh/only-export-components */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { Spinner } from './Spinner';

export type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger' | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

const BASE =
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium ' +
  'transition-[color,background-color,border-color,box-shadow,transform] duration-150 ease-out-expo ' +
  'select-none disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98]';

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 hover:shadow-glow',
  secondary: 'bg-secondary text-secondary-foreground border border-border hover:bg-accent',
  outline: 'border border-border bg-card text-foreground shadow-sm hover:bg-accent hover:text-foreground',
  ghost: 'text-muted-foreground hover:bg-accent hover:text-foreground',
  danger: 'bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
  link: 'text-primary underline-offset-4 hover:underline rounded-md px-0 h-auto',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-9 px-4 text-sm',
  lg: 'h-11 px-5 text-base',
  icon: 'h-9 w-9 p-0',
};

export interface ButtonVariantOptions {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}

/** The Button class list on its own, for anchors and `<Link>`s that must look like a button. */
export function buttonVariants({ variant = 'primary', size = 'md', className }: ButtonVariantOptions = {}): string {
  // Variant after size: the `link` variant's `h-auto px-0` must win over the size's height/padding.
  return cn(BASE, SIZES[size], VARIANTS[variant], className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, sets `aria-busy` and disables the button. */
  loading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
}

/** The one button: six variants, four sizes, a loading state and optional icons on either side. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading = false, leftIcon, rightIcon, className, children, disabled, type = 'button', ...rest },
  ref,
) {
  const t = useT();
  const spinnerSize = size === 'lg' ? 'md' : 'sm';
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={buttonVariants({ variant, size, className })}
      {...rest}
    >
      {loading ? (
        <Spinner size={spinnerSize} label={t('ui.loading')} className="shrink-0" />
      ) : (
        leftIcon && <span className="shrink-0 inline-flex [&>svg]:h-4 [&>svg]:w-4">{leftIcon}</span>
      )}
      {children}
      {!loading && rightIcon && <span className="shrink-0 inline-flex [&>svg]:h-4 [&>svg]:w-4">{rightIcon}</span>}
    </button>
  );
});
