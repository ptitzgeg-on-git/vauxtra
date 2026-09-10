import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { useFieldControl } from './Field';
import { CONTROL_BASE, CONTROL_INVALID, CONTROL_SIZES, type ControlSize } from './_internal';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  invalid?: boolean;
  size?: ControlSize;
  /** Icon inside the field, on the left. */
  leftIcon?: ReactNode;
  /** Icon or small element inside the field, on the right. */
  rightIcon?: ReactNode;
  /** Class for the wrapper when icons are used. */
  wrapperClassName?: string;
}

/** Text input on the token surface; picks up id / aria wiring from a surrounding `Field`. */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, size = 'md', leftIcon, rightIcon, className, wrapperClassName, id, required, ...rest },
  ref,
) {
  const control = useFieldControl({ id, invalid, required, 'aria-describedby': rest['aria-describedby'], 'aria-invalid': rest['aria-invalid'] });
  const input = (
    <input
      ref={ref}
      id={control.id}
      required={control.required || undefined}
      aria-invalid={control.invalid || undefined}
      aria-describedby={control.describedBy}
      className={cn(
        CONTROL_BASE,
        CONTROL_SIZES[size],
        control.invalid && CONTROL_INVALID,
        leftIcon && 'pl-9',
        rightIcon && 'pr-9',
        className,
      )}
      {...rest}
    />
  );

  if (!leftIcon && !rightIcon) return input;

  return (
    <div className={cn('relative', wrapperClassName)}>
      {leftIcon && (
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground [&>svg]:h-4 [&>svg]:w-4">
          {leftIcon}
        </span>
      )}
      {input}
      {rightIcon && (
        <span className="absolute inset-y-0 right-3 flex items-center text-muted-foreground [&>svg]:h-4 [&>svg]:w-4">
          {rightIcon}
        </span>
      )}
    </div>
  );
});
