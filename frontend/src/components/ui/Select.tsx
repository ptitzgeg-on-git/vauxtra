import { forwardRef, type SelectHTMLAttributes } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useFieldControl } from './Field';
import { CONTROL_BASE, CONTROL_INVALID, CONTROL_SIZES, type ControlSize } from './_internal';

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  invalid?: boolean;
  size?: ControlSize;
  wrapperClassName?: string;
}

/** Native `<select>` styled like `Input`, with a chevron; options come as children. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid, size = 'md', className, wrapperClassName, id, required, children, ...rest },
  ref,
) {
  const control = useFieldControl({ id, invalid, required, 'aria-describedby': rest['aria-describedby'], 'aria-invalid': rest['aria-invalid'] });
  return (
    <div className={cn('relative', wrapperClassName)}>
      <select
        ref={ref}
        id={control.id}
        required={control.required || undefined}
        aria-invalid={control.invalid || undefined}
        aria-describedby={control.describedBy}
        className={cn(
          CONTROL_BASE,
          CONTROL_SIZES[size],
          'appearance-none pr-9 cursor-pointer',
          control.invalid && CONTROL_INVALID,
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
});
