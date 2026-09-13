/**
 * The frame every wizard screen sits in: header, body card, and a Back / Next footer.
 *
 * It also owns the Enter shortcut. A first-run wizard is mostly one field and one button,
 * so Enter has to move forward — but only from somewhere Enter means nothing else: not from
 * a textarea (newline), not from a button, link or select (their own activation), and not
 * mid-composition on an IME. The step passes its primary action; the shell decides when
 * pressing Enter runs it.
 */

import type { KeyboardEvent, ReactNode } from 'react';
import { ArrowLeft, ArrowRight, CornerDownLeft } from 'lucide-react';
import { Button, Card, CardContent, Kbd, type ButtonVariant } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useT } from '@/i18n';

export interface SetupPrimaryAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  icon?: ReactNode;
  variant?: ButtonVariant;
}

export interface SetupStepShellProps {
  /** Lucide element for the header badge. */
  icon: ReactNode;
  title: string;
  description?: ReactNode;
  /** Right-hand slot on the header row (a close button, a badge). */
  headerAside?: ReactNode;
  children: ReactNode;
  /** Renders the body without the surrounding Card, for steps that compose their own panels. */
  bare?: boolean;
  onBack?: () => void;
  backLabel?: string;
  backDisabled?: boolean;
  /** The forward action; also what Enter triggers. */
  primary?: SetupPrimaryAction;
  /** Extra control between Back and the primary button. */
  secondaryAction?: ReactNode;
  /** Small print under the footer. */
  note?: ReactNode;
  className?: string;
}

export function SetupStepShell({
  icon,
  title,
  description,
  headerAside,
  children,
  bare = false,
  onBack,
  backLabel,
  backDisabled = false,
  primary,
  secondaryAction,
  note,
  className,
}: SetupStepShellProps) {
  const t = useT();

  const canSubmit = Boolean(primary && !primary.disabled && !primary.loading);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Enter' || e.defaultPrevented || e.nativeEvent.isComposing) return;
    if (e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return;
    const target = e.target as HTMLElement | null;
    const tag = target?.tagName;
    if (tag === 'TEXTAREA' || tag === 'BUTTON' || tag === 'A' || tag === 'SELECT' || target?.isContentEditable) return;
    if (!canSubmit || !primary) return;
    e.preventDefault();
    primary.onClick();
  };

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- a keyboard shortcut on the step container; it has no pointer affordance to match
    <div
      onKeyDown={handleKeyDown}
      className={cn('animate-in fade-in-up space-y-6', className)}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-primary/10 text-primary [&>svg]:h-5 [&>svg]:w-5"
        >
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-bold tracking-tight text-foreground">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {headerAside}
      </div>

      {bare ? children : (
        <Card>
          <CardContent className="space-y-5 p-5 sm:p-6">{children}</CardContent>
        </Card>
      )}

      {(onBack || primary || secondaryAction) && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          {onBack ? (
            <Button variant="ghost" onClick={onBack} disabled={backDisabled} leftIcon={<ArrowLeft />}>
              {backLabel ?? t('common.back')}
            </Button>
          ) : (
            <span />
          )}

          <div className="flex items-center gap-3">
            {canSubmit && (
              <span className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:inline-flex">
                <Kbd size="sm">
                  <CornerDownLeft className="h-3 w-3" />
                </Kbd>
                {t('setup.enter_hint')}
              </span>
            )}
            {secondaryAction}
            {primary && (
              <Button
                variant={primary.variant ?? 'primary'}
                onClick={primary.onClick}
                disabled={primary.disabled}
                loading={primary.loading}
                rightIcon={primary.loading ? undefined : (primary.icon ?? <ArrowRight />)}
              >
                {primary.label}
              </Button>
            )}
          </div>
        </div>
      )}

      {note && <p className="text-center text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}
