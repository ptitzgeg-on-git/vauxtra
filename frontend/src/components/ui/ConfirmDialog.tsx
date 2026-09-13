/* eslint-disable react-refresh/only-export-components */
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Info, X } from 'lucide-react';
import { useModalDialog } from '../../hooks/useModalDialog';
import { useT } from '../../i18n';
import { cn } from '@/lib/cn';
import { Button, type ButtonVariant } from './Button';
import { useScrollLock } from './_internal';

export type ConfirmVariant = 'danger' | 'warning' | 'info';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** A string keeps its line breaks; pass a node for emphasis, a list, or a name in bold. */
  message?: ReactNode;
  /** Defaults to `common.confirm`. */
  confirmLabel?: string;
  /** Defaults to `common.cancel`. */
  cancelLabel?: string;
  variant?: ConfirmVariant;
  /**
   * The exact text the user has to type before the confirm button enables.
   *
   * It guards the actions that destroy more than the one object on screen: the two that
   * empty the database (`POST /api/reset` and `POST /api/restore`, same sixteen tables)
   * ask for RESET and RESTORE, and a bulk deletion asks for the number of routes it is
   * about to delete. Deleting one service, one provider or one domain does not: the dialog
   * names the thing, and the thing is what goes.
   *
   * This comment used to say "restore" while the restore dialog passed nothing, which is
   * how a whole database sat behind one unguarded click. Add the action here only when the
   * call site actually passes the word.
   */
  requireText?: string;
  /** While true the confirm button spins and neither Escape, the backdrop nor the buttons close the dialog. */
  loading?: boolean;
  /** Replaces the variant's icon. */
  icon?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The confirm button is a `Button` like every other button in the app; `warning` and `info` have
 * no Button variant of their own, so they borrow `primary`'s shape and repaint it on their token.
 */
const VARIANT_STYLES: Record<ConfirmVariant, { icon: string; variant: ButtonVariant; buttonClass?: string }> = {
  danger: {
    icon: 'bg-destructive/10 text-destructive',
    variant: 'danger',
  },
  warning: {
    icon: 'bg-warning/10 text-warning',
    variant: 'primary',
    buttonClass: 'bg-warning text-warning-foreground hover:bg-warning/90 hover:shadow-sm',
  },
  info: {
    icon: 'bg-info/10 text-info',
    variant: 'primary',
    buttonClass: 'bg-info text-info-foreground hover:bg-info/90 hover:shadow-sm',
  },
};

/**
 * Styled confirmation dialog in place of `window.confirm()`.
 *
 * Anything that removes something opens with focus on Cancel, so a stray Enter cannot destroy
 * what the dialog is asking about; only `info`, which adds, opens on Confirm. Escape, the
 * backdrop and the close button all cancel, unless
 * `loading` says the confirmed action is still running. The panel is mounted fresh on
 * every opening, so the typed confirmation text never carries over.
 */
export function ConfirmDialog({ open, ...panel }: ConfirmDialogProps) {
  if (!open || typeof document === 'undefined') return null;
  /**
   * Portalled to the body, like `Modal` and `Drawer`, and for the two reasons they were. A
   * confirmation is regularly asked from inside a modal -- discarding a half-filled wizard, for
   * one -- and both overlays sit at `z-50`: rendered where it is declared, the confirm is
   * earlier in the document than the modal's own portal and paints underneath it, invisible.
   * And `fixed` is measured against the nearest transformed ancestor: the modal panel carries
   * `zoom-in-95`, which would become the containing block and shrink `inset-0` to the panel.
   */
  return createPortal(<ConfirmDialogPanel {...panel} />, document.body);
}

function ConfirmDialogPanel({
  title,
  message,
  confirmLabel,
  cancelLabel,
  variant = 'danger',
  requireText,
  loading = false,
  icon,
  onConfirm,
  onCancel,
}: Omit<ConfirmDialogProps, 'open'>) {
  const t = useT();
  const titleId = useId();
  const messageId = useId();
  const inputId = useId();
  const [typed, setTyped] = useState('');
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Read inside the handlers so the Escape listener in useModalDialog sees the live value.
  const loadingRef = useRef(loading);
  useEffect(() => {
    loadingRef.current = loading;
  });
  const requestCancel = useCallback(() => {
    if (!loadingRef.current) onCancel();
  }, [onCancel]);

  const dialogRef = useModalDialog<HTMLDivElement>(true, requestCancel);
  // Same contract as Modal and Drawer; the hook nests, so a confirm opened over a modal does not
  // release the modal's lock when it closes.
  useScrollLock(true);

  // useModalDialog focuses the box first (so the title is announced); the safest control next.
  // The test is "does confirming destroy something", not "which colour is the icon".
  // `warning` is not a gentler `danger` here: it is what the worse confirmations use -- the
  // forced removal of an integration services still depend on, a domain that is in use, a
  // reconcile that writes to a live provider. Keying the focus on `danger` alone armed the
  // destructive button on exactly those. Only `info`, which adds without removing, opens on
  // Confirm.
  useEffect(() => {
    const target = requireText
      ? inputRef.current
      : variant === 'info'
        ? confirmRef.current
        : cancelRef.current;
    target?.focus({ preventScroll: true });
  }, [requireText, variant]);

  const textMatches = !requireText || typed.trim() === requireText.trim();
  const canConfirm = textMatches && !loading;

  const handleConfirm = () => {
    if (canConfirm) onConfirm();
  };

  const handleBackdropClick = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) requestCancel();
  };

  const handleInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleConfirm();
    }
  };

  const styles = VARIANT_STYLES[variant];
  const Icon = variant === 'info' ? Info : AlertTriangle;
  const confirmText = confirmLabel ?? t('common.confirm');
  const cancelText = cancelLabel ?? t('common.cancel');
  const hasMessage = message !== undefined && message !== null && message !== '';

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- the backdrop; the keyboard path out of a dialog is Escape, handled by useModalDialog
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-xs animate-in fade-in animate-duration-150"
      onClick={handleBackdropClick}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={hasMessage ? messageId : undefined}
        aria-busy={loading || undefined}
        className="w-full max-w-md overflow-hidden rounded-2xl border border-border bg-card text-foreground shadow-elevated outline-hidden animate-in zoom-in-95 fade-in animate-duration-200"
      >
        <div className="flex items-start gap-4 p-5">
          <div className={`shrink-0 rounded-xl p-2.5 ${styles.icon}`} aria-hidden="true">
            {icon ?? <Icon className="h-5 w-5" />}
          </div>
          <div className="min-w-0 flex-1 pt-1">
            <h2 id={titleId} className="text-base font-semibold leading-snug text-foreground">
              {title}
            </h2>
            {hasMessage && (
              <div id={messageId} className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {typeof message === 'string' ? <p className="whitespace-pre-wrap">{message}</p> : message}
              </div>
            )}
            {requireText && (
              <div className="mt-4">
                <label htmlFor={inputId} className="block text-xs font-medium text-foreground">
                  {t('ui.confirm.type_to_confirm', { text: requireText })}
                </label>
                <input
                  ref={inputRef}
                  id={inputId}
                  type="text"
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  onKeyDown={handleInputKeyDown}
                  disabled={loading}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  placeholder={requireText}
                  aria-invalid={typed.length > 0 && !textMatches ? true : undefined}
                  className="mt-1.5 w-full rounded-xl border border-border bg-input px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground disabled:opacity-60"
                />
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={requestCancel}
            disabled={loading}
            className="-mr-1.5 -mt-1.5 shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
            aria-label={t('ui.modal.close')}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border bg-muted/40 px-5 py-3.5">
          <Button ref={cancelRef} variant="outline" onClick={requestCancel} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            ref={confirmRef}
            variant={styles.variant}
            onClick={handleConfirm}
            disabled={!canConfirm}
            loading={loading}
            className={cn('font-semibold', styles.buttonClass)}
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  );
}

export interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
  requireText?: string;
  icon?: ReactNode;
}

/**
 * Promise-style confirmation.
 *
 *   const { confirm, ConfirmDialogElement } = useConfirmDialog();
 *   if (await confirm({ title: t('services.confirm.delete_title'), message: ..., variant: 'danger' })) { ... }
 *   return <>{ConfirmDialogElement}</>;
 *
 * Asking a second question while the first is open answers the first with `false`; so does
 * unmounting (a route change) -- no caller is left awaiting forever.
 */
export function useConfirmDialog() {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const settle = useCallback((value: boolean) => {
    const resolve = resolveRef.current;
    resolveRef.current = null;
    setOptions(null);
    resolve?.(value);
  }, []);

  const confirm = useCallback((next: ConfirmOptions): Promise<boolean> => {
    resolveRef.current?.(false);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
      setOptions(next);
    });
  }, []);

  useEffect(
    () => () => {
      resolveRef.current?.(false);
      resolveRef.current = null;
    },
    [],
  );

  const ConfirmDialogElement = (
    <ConfirmDialog
      open={options !== null}
      title={options?.title ?? ''}
      message={options?.message}
      confirmLabel={options?.confirmLabel}
      cancelLabel={options?.cancelLabel}
      variant={options?.variant}
      requireText={options?.requireText}
      icon={options?.icon}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  );

  return { confirm, ConfirmDialogElement };
}
