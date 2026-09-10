import { useId, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';
import { useModalDialog } from '@/hooks/useModalDialog';
import { useScrollLock } from './_internal';

export type DrawerSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';
export type DrawerSide = 'right' | 'left';

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  size?: DrawerSize;
  side?: DrawerSide;
  /** Clicking the backdrop does not close it (Escape still does). */
  persistent?: boolean;
  hideClose?: boolean;
  icon?: ReactNode;
  /** Accessible name when there is no `title`. */
  'aria-label'?: string;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}

const SIZES: Record<DrawerSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-xl',
  xl: 'max-w-3xl',
  full: 'max-w-none',
};

/**
 * Side panel on the same a11y contract as `Modal` (`useModalDialog`): slides in from the
 * right by default, `side="left"` for navigation.
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  footer,
  size = 'md',
  side = 'right',
  persistent = false,
  hideClose = false,
  icon,
  'aria-label': ariaLabel,
  className,
  bodyClassName,
  children,
}: DrawerProps) {
  const t = useT();
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useModalDialog<HTMLDivElement>(open, onClose);
  useScrollLock(open);

  if (!open || typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div
        aria-hidden="true"
        onClick={persistent ? undefined : onClose}
        className="absolute inset-0 bg-background/60 backdrop-blur-sm animate-in fade-in animate-duration-150"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : ariaLabel}
        aria-describedby={description ? descriptionId : undefined}
        className={cn(
          'absolute inset-y-0 flex h-full w-full flex-col outline-none bg-card text-foreground shadow-elevated',
          'animate-in animate-duration-300',
          side === 'right' ? 'right-0 border-l border-border slide-in-from-right' : 'left-0 border-r border-border slide-in-from-left',
          SIZES[size],
          className,
        )}
      >
        {(title || !hideClose) && (
          <div className="flex items-start gap-4 px-6 pt-5 pb-4 border-b border-border">
            {icon && (
              <span
                aria-hidden="true"
                className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary [&>svg]:h-5 [&>svg]:w-5"
              >
                {icon}
              </span>
            )}
            <div className="min-w-0 flex-1">
              {title && (
                <h2 id={titleId} className="text-base font-semibold leading-tight tracking-tight text-foreground">
                  {title}
                </h2>
              )}
              {description && (
                <p id={descriptionId} className="mt-1 text-sm text-muted-foreground">
                  {description}
                </p>
              )}
            </div>
            {!hideClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label={t('ui.modal.close')}
                className="-mr-2 -mt-1 shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
            )}
          </div>
        )}
        <div className={cn('flex-1 overflow-y-auto px-6 py-5', bodyClassName)}>{children}</div>
        {footer && (
          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-border bg-muted/40 px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
