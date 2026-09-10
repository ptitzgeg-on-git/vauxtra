import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { toneClasses } from './tone';

export type InlineAlertTone = 'info' | 'success' | 'warning' | 'danger';

export interface InlineAlertProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  tone?: InlineAlertTone;
  title?: ReactNode;
  /** Replaces the tone's default icon. */
  icon?: ReactNode;
  /** Right-hand slot: a Button, a link. */
  action?: ReactNode;
  /** Shows a close button that calls this. */
  onDismiss?: () => void;
  /** Square corners and no border on the sides — for banners spanning a container. */
  banner?: boolean;
  children?: ReactNode;
}

const ICONS: Record<InlineAlertTone, ReactNode> = {
  info: <Info />,
  success: <CircleCheck />,
  warning: <TriangleAlert />,
  danger: <CircleAlert />,
};

/** A tinted message block with icon, title, body and optional action — never colour alone. */
export const InlineAlert = forwardRef<HTMLDivElement, InlineAlertProps>(function InlineAlert(
  { tone = 'info', title, icon, action, onDismiss, banner = false, className, children, ...rest },
  ref,
) {
  const t = useT();
  const c = toneClasses(tone);
  return (
    <div
      ref={ref}
      role={tone === 'danger' || tone === 'warning' ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-3 border text-sm',
        banner ? 'rounded-none border-x-0 border-t-0 px-4 py-2.5' : 'rounded-xl px-4 py-3',
        c.bg,
        c.border,
        className,
      )}
      {...rest}
    >
      <span aria-hidden="true" className={cn('mt-0.5 shrink-0 inline-flex [&>svg]:h-4 [&>svg]:w-4', c.text)}>
        {icon ?? ICONS[tone]}
      </span>
      <div className="min-w-0 flex-1 space-y-0.5">
        {title && <p className={cn('font-semibold leading-tight', c.text)}>{title}</p>}
        {children && <div className="text-foreground/80 leading-relaxed">{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={t('ui.dismiss')}
          className={cn('shrink-0 -mr-1 -mt-0.5 rounded-md p-1 transition-colors hover:bg-foreground/10', c.text)}
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      )}
    </div>
  );
});
