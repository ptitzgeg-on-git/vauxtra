import { CircleHelp } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { Tooltip, type TooltipPlacement } from './Tooltip';

interface FieldHintProps {
  text: string;
  placement?: TooltipPlacement;
  className?: string;
}

/** A small "?" next to a label that explains the field on hover and keyboard focus. */
export function FieldHint({ text, placement = 'top', className }: FieldHintProps) {
  const t = useT();
  return (
    <Tooltip content={text} placement={placement} contentClassName="max-w-[16rem] whitespace-normal font-normal">
      <button
        type="button"
        aria-label={t('ui.field_hint')}
        className={cn(
          'inline-flex items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-offset-0',
          className,
        )}
      >
        <CircleHelp aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
    </Tooltip>
  );
}
