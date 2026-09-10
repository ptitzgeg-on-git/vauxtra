import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface SectionHeadingProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  title: ReactNode;
  description?: ReactNode;
  /** Right-hand slot: actions, a badge, a count. */
  children?: ReactNode;
  as?: 'h2' | 'h3' | 'h4';
  size?: 'sm' | 'md';
  icon?: ReactNode;
}

/** Heading for a section inside a page or card: title, description, and a right slot. */
export const SectionHeading = forwardRef<HTMLDivElement, SectionHeadingProps>(function SectionHeading(
  { title, description, children, as: Tag = 'h2', size = 'md', icon, className, ...rest },
  ref,
) {
  return (
    <div ref={ref} className={cn('flex flex-wrap items-start justify-between gap-x-4 gap-y-2', className)} {...rest}>
      <div className="min-w-0 flex items-start gap-2.5">
        {icon && (
          <span aria-hidden="true" className="mt-0.5 inline-flex shrink-0 text-muted-foreground [&>svg]:h-4 [&>svg]:w-4">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <Tag className={cn('font-semibold tracking-tight text-foreground', size === 'sm' ? 'text-sm' : 'text-base')}>{title}</Tag>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {children && <div className="flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  );
});
