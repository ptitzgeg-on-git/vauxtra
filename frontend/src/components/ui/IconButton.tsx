import { forwardRef, type ReactNode } from 'react';
import { useT } from '@/i18n';
import { Button, type ButtonProps } from './Button';
import { Spinner } from './Spinner';
import { Tooltip, type TooltipPlacement } from './Tooltip';

export interface IconButtonProps extends Omit<ButtonProps, 'children' | 'leftIcon' | 'rightIcon' | 'aria-label'> {
  /** Accessible name — required, this button has no visible text. */
  label: string;
  icon: ReactNode;
  /** Show `label` as a tooltip on hover/focus instead of a native title. */
  tooltip?: boolean;
  tooltipPlacement?: TooltipPlacement;
}

/** A square icon-only button that always has an accessible name. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  {
    label,
    icon,
    tooltip = false,
    tooltipPlacement = 'top',
    variant = 'ghost',
    size = 'icon',
    className,
    loading = false,
    disabled,
    ...rest
  },
  ref,
) {
  const t = useT();
  // `loading` deliberately never reaches Button: Button paints its spinner *next to* its
  // children, and a square button has room for exactly one glyph -- spinner + icon + the base
  // `gap-2` overflow an `h-9 w-9` box. Here the spinner replaces the icon, and the disabled /
  // aria-busy half of Button's loading contract is reproduced by hand.
  const button = (
    <Button
      ref={ref}
      variant={variant}
      size={size}
      aria-label={label}
      aria-busy={loading || undefined}
      title={tooltip ? undefined : label}
      className={className}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? (
        <Spinner size={size === 'lg' ? 'md' : 'sm'} label={t('ui.loading')} />
      ) : (
        <span className="inline-flex [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
      )}
    </Button>
  );

  if (!tooltip) return button;
  return (
    <Tooltip content={label} placement={tooltipPlacement}>
      {button}
    </Tooltip>
  );
});
