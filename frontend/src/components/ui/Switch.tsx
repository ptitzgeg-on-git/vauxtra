import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { useFieldControl } from './Field';

export interface SwitchProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange' | 'value' | 'type'> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  size?: 'sm' | 'md';
  invalid?: boolean;
  /** Forwarded to the `Field` wiring (a button has no native `required`). */
  required?: boolean;
  /** Inline label to the right; clicking it toggles the switch. */
  label?: ReactNode;
  description?: ReactNode;
}

/** An accessible toggle (`role="switch"`), with an optional inline label. */
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked, onCheckedChange, size = 'md', invalid, label, description, className, id, disabled, required, ...rest },
  ref,
) {
  const control = useFieldControl({ id, invalid, required, 'aria-describedby': rest['aria-describedby'] });
  const track = size === 'sm' ? 'h-5 w-9' : 'h-6 w-11';
  const thumb = size === 'sm' ? 'h-4 w-4' : 'h-5 w-5';
  const travel = size === 'sm' ? 'translate-x-4' : 'translate-x-5';

  // A `<button role="switch">` takes its name from its own subtree first, and this one's only
  // child is the `aria-hidden` thumb -- so `<label htmlFor>` alone leaves it unnamed on some
  // screen readers. The text is wired by id instead: the inline label here, or the surrounding
  // `Field`'s label when there is one. The description is a description, not part of the name.
  const labelId = `${control.id}-label`;
  const descriptionId = `${control.id}-description`;
  const labelledBy = label ? labelId : control.labelId;
  const describedBy =
    [description ? descriptionId : undefined, control.describedBy].filter(Boolean).join(' ') || undefined;

  const button = (
    <button
      ref={ref}
      type="button"
      role="switch"
      id={control.id}
      aria-checked={checked}
      aria-invalid={control.invalid || undefined}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'relative inline-flex shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200',
        'disabled:cursor-not-allowed disabled:opacity-50',
        track,
        checked ? 'bg-primary' : 'bg-muted-foreground/30',
        control.invalid && 'ring-2 ring-destructive/40',
        className,
      )}
      {...rest}
    >
      <span
        aria-hidden="true"
        className={cn(
          'pointer-events-none inline-block rounded-full bg-card shadow-sm ring-0 transition-transform duration-200 ease-out-expo',
          thumb,
          checked ? travel : 'translate-x-0',
        )}
      />
    </button>
  );

  if (!label && !description) return button;

  return (
    <div className="flex items-start gap-3">
      {button}
      {/* Not a `<label>`: label activation is not specified for `<button>` the way it is for an
          input, so the click target is wired explicitly and the association carried by the ids. */}
      <div
        onClick={() => {
          if (!disabled) onCheckedChange(!checked);
        }}
        className={cn('min-w-0 select-none', disabled ? 'opacity-60' : 'cursor-pointer')}
      >
        {label && (
          <span id={labelId} className="block text-sm font-medium text-foreground leading-tight">
            {label}
          </span>
        )}
        {description && (
          <span id={descriptionId} className="block text-xs text-muted-foreground mt-0.5">
            {description}
          </span>
        )}
      </div>
    </div>
  );
});
