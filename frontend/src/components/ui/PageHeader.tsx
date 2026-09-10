import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface PageHeaderProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Small uppercase line above the title (a section name). */
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** Right-hand actions (Buttons). */
  actions?: ReactNode;
  /** Small line under the description: counts, last refresh, status badges. */
  meta?: ReactNode;
  /** Icon shown in a tinted square to the left of the title. */
  icon?: ReactNode;
}

/** The header at the top of every page: eyebrow, title, description, meta line and actions. */
export const PageHeader = forwardRef<HTMLElement, PageHeaderProps>(function PageHeader(
  { eyebrow, title, description, actions, meta, icon, className, ...rest },
  ref,
) {
  return (
    <header
      ref={ref}
      className={cn('flex flex-col gap-4 md:flex-row md:items-start md:justify-between', className)}
      {...rest}
    >
      <div className="min-w-0 flex items-start gap-4">
        {icon && (
          <span
            aria-hidden="true"
            className="mt-0.5 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary [&>svg]:h-5 [&>svg]:w-5"
          >
            {icon}
          </span>
        )}
        <div className="min-w-0 space-y-1">
          {eyebrow && (
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{eyebrow}</p>
          )}
          <h1 className="text-2xl font-bold tracking-tight text-foreground leading-tight">{title}</h1>
          {description && <p className="text-sm text-muted-foreground max-w-2xl">{description}</p>}
          {meta && <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-1 text-xs text-muted-foreground">{meta}</div>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2 md:justify-end">{actions}</div>}
    </header>
  );
});
