import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { Inbox } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';

export interface EmptyStateProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  icon?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  /** Primary call to action (a Button). */
  action?: ReactNode;
  /** Secondary actions, rendered after `action`. */
  actions?: ReactNode;
  /** Less padding and a smaller icon, for empty states inside a card or table. */
  compact?: boolean;
}

/** Centered "nothing here" block with icon, title, description and a call to action. */
export const EmptyState = forwardRef<HTMLDivElement, EmptyStateProps>(function EmptyState(
  { icon, title, description, action, actions, compact = false, className, ...rest },
  ref,
) {
  const t = useT();
  return (
    <div
      ref={ref}
      className={cn(
        'flex flex-col items-center justify-center text-center rounded-2xl border border-dashed border-border bg-muted/30',
        compact ? 'px-4 py-8 gap-2' : 'px-6 py-16 gap-3',
        className,
      )}
      {...rest}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-flex items-center justify-center rounded-2xl bg-card text-muted-foreground shadow-card border border-border',
          compact ? 'h-10 w-10 [&>svg]:h-5 [&>svg]:w-5' : 'h-14 w-14 [&>svg]:h-7 [&>svg]:w-7 mb-1',
        )}
      >
        {icon ?? <Inbox />}
      </span>
      <p className={cn('font-semibold text-foreground', compact ? 'text-sm' : 'text-base')}>{title ?? t('ui.empty.title')}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {(action || actions) && (
        <div className={cn('flex flex-wrap items-center justify-center gap-2', compact ? 'mt-1' : 'mt-3')}>
          {action}
          {actions}
        </div>
      )}
    </div>
  );
});
