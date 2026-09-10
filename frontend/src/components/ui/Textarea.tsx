import { forwardRef, type TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';
import { useFieldControl } from './Field';
import { CONTROL_BASE, CONTROL_INVALID } from './_internal';

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

/** Multi-line input on the token surface; wired to a surrounding `Field` like `Input`. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, className, id, required, rows = 3, ...rest },
  ref,
) {
  const control = useFieldControl({ id, invalid, required, 'aria-describedby': rest['aria-describedby'], 'aria-invalid': rest['aria-invalid'] });
  return (
    <textarea
      ref={ref}
      id={control.id}
      rows={rows}
      required={control.required || undefined}
      aria-invalid={control.invalid || undefined}
      aria-describedby={control.describedBy}
      className={cn(CONTROL_BASE, 'min-h-[80px] px-3 py-2 text-sm leading-relaxed', control.invalid && CONTROL_INVALID, className)}
      {...rest}
    />
  );
});
