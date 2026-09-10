import { forwardRef, useCallback, useEffect, useRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { Check, Minus } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useFieldControl } from './Field';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  invalid?: boolean;
  /** Mixed state (e.g. "some rows selected"); shown as a dash. */
  indeterminate?: boolean;
  /** Inline label to the right; clicking it toggles the box. */
  label?: ReactNode;
  description?: ReactNode;
}

/** Native checkbox restyled on the tokens, with optional inline label and an indeterminate state. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { invalid, indeterminate = false, label, description, className, id, required, disabled, ...rest },
  forwardedRef,
) {
  const control = useFieldControl({ id, invalid, required, 'aria-describedby': rest['aria-describedby'], 'aria-invalid': rest['aria-invalid'] });
  const innerRef = useRef<HTMLInputElement | null>(null);

  const setRefs = useCallback(
    (node: HTMLInputElement | null) => {
      innerRef.current = node;
      if (typeof forwardedRef === 'function') forwardedRef(node);
      else if (forwardedRef) forwardedRef.current = node;
    },
    [forwardedRef],
  );

  useEffect(() => {
    if (innerRef.current) innerRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  const box = (
    <span className="relative inline-flex h-4 w-4 shrink-0">
      <input
        ref={setRefs}
        type="checkbox"
        id={control.id}
        required={control.required || undefined}
        disabled={disabled}
        aria-invalid={control.invalid || undefined}
        aria-describedby={control.describedBy}
        className={cn(
          'peer h-4 w-4 cursor-pointer appearance-none rounded border border-border bg-input shadow-sm transition-colors',
          'checked:border-primary checked:bg-primary indeterminate:border-primary indeterminate:bg-primary',
          'focus:ring-2 focus:ring-primary/25 focus:ring-offset-0',
          'disabled:cursor-not-allowed disabled:opacity-50',
          control.invalid && 'border-destructive',
          className,
        )}
        {...rest}
      />
      <Check
        aria-hidden="true"
        strokeWidth={3}
        className={cn(
          'pointer-events-none absolute inset-0 m-auto h-3 w-3 text-primary-foreground opacity-0 transition-opacity',
          'peer-checked:opacity-100',
          indeterminate && 'peer-checked:opacity-0',
        )}
      />
      <Minus
        aria-hidden="true"
        strokeWidth={3}
        className={cn(
          'pointer-events-none absolute inset-0 m-auto h-3 w-3 text-primary-foreground opacity-0 transition-opacity',
          indeterminate && 'opacity-100',
        )}
      />
    </span>
  );

  if (!label && !description) return box;

  return (
    <div className="flex items-start gap-2.5">
      <span className="pt-0.5">{box}</span>
      <label htmlFor={control.id} className={cn('min-w-0 select-none cursor-pointer', disabled && 'opacity-60 cursor-not-allowed')}>
        {label && <span className="block text-sm font-medium text-foreground leading-tight">{label}</span>}
        {description && <span className="block text-xs text-muted-foreground mt-0.5">{description}</span>}
      </label>
    </div>
  );
});
