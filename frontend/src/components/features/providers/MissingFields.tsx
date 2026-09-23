import { Check, CircleAlert } from 'lucide-react';
import { cn } from '@/components/ui';
import { useT } from '@/i18n';
import type { MissingField } from './providerConstants';

export interface MissingFieldsProps {
  fields: MissingField[];
  /** Takes the operator to the guided step a field is asked on. Without it, no field is a link. */
  onGoToStep?: (step: number) => void;
  /** Said instead when nothing is missing. Nothing at all is said when it is omitted. */
  complete?: string;
}

const PILL = 'inline-flex items-center rounded-md border border-border bg-background px-1.5 py-0.5 font-medium text-foreground';

/**
 * The line under a provider form that says why its button is still disabled.
 *
 * Both forms disabled "Validate" (and the guided "Next") until the required fields were filled,
 * and neither said which ones: the button simply did nothing. The guided panel of the
 * Integrations dialog went further the other way and closed on a green "Everything is filled
 * in" that it printed whatever the form held. This line is the one place either form answers
 * the question, and `complete` is said only when it is true.
 */
export function MissingFields({ fields, onGoToStep, complete }: MissingFieldsProps) {
  const t = useT();
  if (fields.length === 0) {
    if (!complete) return null;
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Check aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-success" />
        {complete}
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <CircleAlert aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        {t('provider_modal.missing.title')}
      </span>
      <ul className="flex flex-wrap gap-1.5" aria-label={t('provider_modal.missing.title')}>
        {fields.map((field) => (
          <li key={field.key}>
            {field.step !== undefined && onGoToStep ? (
              <button
                type="button"
                onClick={() => onGoToStep(field.step as number)}
                title={t('provider_modal.guided.go_to', { step: field.step + 1 })}
                className={cn(
                  PILL,
                  'transition-colors hover:border-primary/50 hover:text-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
                )}
              >
                {field.label}
              </button>
            ) : (
              <span className={PILL}>{field.label}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
