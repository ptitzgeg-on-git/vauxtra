/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useId, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';

interface FieldContextValue {
  id: string;
  /** Id of the `<label>` element, for controls a `htmlFor` cannot name on its own (a `role="switch"` button). */
  labelId?: string;
  hintId?: string;
  errorId?: string;
  invalid: boolean;
  required: boolean;
}

const FieldContext = createContext<FieldContextValue | null>(null);

export interface FieldControlProps {
  id?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean | 'true' | 'false' | 'grammar' | 'spelling';
  invalid?: boolean;
  required?: boolean;
}

/**
 * Wires a control to the surrounding `Field`: id, `aria-describedby` (hint + error), `aria-invalid`,
 * `required`. Explicit props on the control always win over what the Field provides.
 */
export function useFieldControl(props: FieldControlProps) {
  const field = useContext(FieldContext);
  const fallbackId = useId();
  const id = props.id ?? field?.id ?? fallbackId;
  const ariaInvalid = props['aria-invalid'] === true || props['aria-invalid'] === 'true';
  const invalid = props.invalid ?? ariaInvalid;
  const describedBy = props['aria-describedby'] ?? [field?.errorId, field?.hintId].filter(Boolean).join(' ');
  return {
    id,
    labelId: field?.labelId,
    invalid: Boolean(invalid || field?.invalid),
    required: props.required ?? field?.required ?? false,
    describedBy: describedBy || undefined,
  };
}

export interface FieldProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  label?: ReactNode;
  /** Helper text under the control. */
  hint?: ReactNode;
  /** Error text under the control; also marks the control invalid. */
  error?: ReactNode;
  required?: boolean;
  /** Id of the control; generated when omitted and passed down through context. */
  htmlFor?: string;
  /** Right-hand slot on the label row (a `FieldHint`, a link, a counter). */
  labelAddon?: ReactNode;
  /** Puts the control before the label on one row — for switches and checkboxes. */
  inline?: boolean;
  children: ReactNode;
}

/** Label + control + hint/error, with all the `aria-*` wiring done for whatever control sits inside. */
export function Field({
  label,
  hint,
  error,
  required = false,
  htmlFor,
  labelAddon,
  inline = false,
  className,
  children,
  ...rest
}: FieldProps) {
  const t = useT();
  const generated = useId();
  const id = htmlFor ?? `field-${generated}`;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const labelId = label ? `${id}-label` : undefined;

  const labelEl = label ? (
    <div className={cn('flex items-center gap-1.5', !inline && 'justify-between')}>
      <label htmlFor={id} id={labelId} className="text-xs font-semibold text-foreground leading-tight">
        {label}
        {required && (
          <span className="ml-0.5 text-destructive" aria-hidden="true">
            *
          </span>
        )}
        {required && <span className="sr-only"> ({t('ui.required')})</span>}
      </label>
      {labelAddon}
    </div>
  ) : null;

  const messages = (error || hint) && (
    <div className="space-y-1">
      {error && (
        <p id={errorId} role="alert" className="text-xs font-medium text-destructive">
          {error}
        </p>
      )}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );

  return (
    <FieldContext.Provider value={{ id, labelId, hintId, errorId, invalid: Boolean(error), required }}>
      <div className={cn('space-y-1.5', className)} {...rest}>
        {inline ? (
          <div className="flex items-start gap-3">
            <div className="shrink-0 pt-0.5">{children}</div>
            <div className="min-w-0 flex-1 space-y-1">
              {labelEl}
              {messages}
            </div>
          </div>
        ) : (
          <>
            {labelEl}
            {children}
            {messages}
          </>
        )}
      </div>
    </FieldContext.Provider>
  );
}
