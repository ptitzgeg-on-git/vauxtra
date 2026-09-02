import { useEffect, useRef } from 'react';

/** Everything the browser will let you Tab to, minus what is disabled or explicitly removed. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableWithin(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    // `offsetParent` is null for anything display:none — a collapsed wizard step keeps its
    // inputs in the DOM, and Tab must not stop on a field nobody can see.
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

/**
 * The three big modals were plain `<div>`s over the page: no role, no `aria-modal`, no
 * Escape, and Tab walked straight out of them into the page underneath. A screen reader
 * announced nothing when they opened and kept reading the form behind them; a keyboard
 * user could tab into that form, type into it, and never find the way back.
 *
 * Attach the returned ref to the dialog box itself (not the backdrop), and give that same
 * element `role="dialog"`, `aria-modal="true"` and an `aria-labelledby` pointing at its
 * title. `ConfirmDialog` already did the role and the Escape by hand for its own case;
 * this is that behaviour plus the trap and the focus restore, for everyone else.
 */
export function useModalDialog<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const containerRef = useRef<T>(null);
  // Read inside the listener, so a parent re-rendering with a fresh closure does not have to
  // tear the listener down and set it up again. Refreshed in an effect, not during render.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const container = containerRef.current;
    if (!container) return;

    // Whatever had focus when the modal opened — the button that opened it, most of the
    // time. Focus goes back there on close instead of to the top of the document.
    const previous = document.activeElement as HTMLElement | null;

    container.setAttribute('tabindex', '-1');
    // The box itself, not its first field: a screen reader then reads the dialog's title
    // before anything else, and Tab starts from the top of the form.
    container.focus({ preventScroll: true });

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;

      const items = focusableWithin(container);
      if (items.length === 0) {
        e.preventDefault();
        container.focus({ preventScroll: true });
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (!container.contains(active)) {
        // Focus escaped anyway (a click on the backdrop, a browser quirk): pull it back.
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      // Only if focus is still inside the modal that is going away; the user may already
      // have clicked somewhere else, and stealing it back would be worse than doing nothing.
      if (!previous || !document.contains(previous)) return;
      if (container.contains(document.activeElement) || document.activeElement === document.body) {
        previous.focus({ preventScroll: true });
      }
    };
  }, [open]);

  return containerRef;
}
