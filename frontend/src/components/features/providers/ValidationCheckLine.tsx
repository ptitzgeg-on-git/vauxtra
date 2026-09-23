import { AlertTriangle, Check, Minus, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/components/ui';
import { useT } from '@/i18n';
import type { ProviderValidationCheck } from '@/types/api';
import { type CheckTone, checkDetailText, checkLabelText, checkTone } from './providerHealth';

const ICON: Record<CheckTone, LucideIcon> = { success: Check, skipped: Minus, warning: AlertTriangle, danger: X };

const TEXT: Record<CheckTone, string> = {
  success: 'text-success',
  skipped: 'text-muted-foreground',
  warning: 'text-warning',
  danger: 'text-destructive',
};

/**
 * One line of a validation answer, drawn the same way wherever an integration is checked.
 *
 * The Integrations modal and the setup wizard each drew their own line: one coloured the
 * whole of it, the other only its icon, and both drew anything that was not `ok` as a red
 * cross. The tone is `checkTone`'s, so a check that was not run reads as grey and a
 * non-blocking one as amber, in both places.
 *
 * `check.name` is the server's identifier for the check (`test_connection`), not a sentence
 * anyone wrote to be read. `checkDetailText` turns `detail_code` into the reader's language;
 * with no detail, `checkLabelText` translates the name, and the generic word is what is left
 * when the check carries no name at all.
 */
export function ValidationCheckLine({ check }: { check: ProviderValidationCheck }) {
  const t = useT();
  const tone = checkTone(check);
  const Icon = ICON[tone];
  return (
    <li data-tone={tone} className={cn('flex items-start gap-1.5', TEXT[tone])}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{checkDetailText(check, t) || checkLabelText(check.name, t) || t('provider_modal.validation.check_fallback')}</span>
    </li>
  );
}
