import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle, toneClasses, type Tone } from '@/components/ui';

export interface SettingsSectionProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** Right-hand slot of the header: a Button, a Badge, a Switch. */
  actions?: ReactNode;
  /** Bottom row, usually the submit button of the form wrapping the section. */
  footer?: ReactNode;
  /** Tints the icon square; `danger` also tints the border, for the danger zone. */
  tone?: Tone;
  id?: string;
  className?: string;
  contentClassName?: string;
  children?: ReactNode;
}

/**
 * One card of a settings tab: tinted icon, title, description, actions on the right, body,
 * optional footer. Every tab is a stack of these, so the page reads as one rhythm.
 */
export function SettingsSection({
  icon,
  title,
  description,
  actions,
  footer,
  tone = 'primary',
  id,
  className,
  contentClassName,
  children,
}: SettingsSectionProps) {
  const c = toneClasses(tone);
  return (
    <Card id={id} className={cn('overflow-hidden', tone === 'danger' && 'border-destructive/30', className)}>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          {icon && (
            <span
              aria-hidden="true"
              className={cn(
                'mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl [&>svg]:h-4 [&>svg]:w-4',
                c.bg,
                c.text,
              )}
            >
              {icon}
            </span>
          )}
          <div className="min-w-0 space-y-1">
            <CardTitle>{title}</CardTitle>
            {description && <CardDescription>{description}</CardDescription>}
          </div>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
      </CardHeader>
      {children !== undefined && children !== null && (
        <CardContent className={cn('space-y-4', contentClassName)}>{children}</CardContent>
      )}
      {footer && <CardFooter className="justify-end">{footer}</CardFooter>}
    </Card>
  );
}

/** Small uppercase label above a group of controls inside a section. */
export function SectionEyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={cn('text-[11px] font-semibold uppercase tracking-wider text-muted-foreground', className)}>{children}</p>
  );
}
