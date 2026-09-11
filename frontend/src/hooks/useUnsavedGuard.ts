import { useCallback, useEffect, useRef } from 'react';
import { useConfirmDialog } from '@/components/ui';
import { useT } from '@/i18n';

/**
 * The question a dialog has to ask before it throws a half-filled form away.
 *
 * `Modal` closes on Escape even when `persistent` blocks the backdrop, and every dialog here
 * resets its fields on the way out. On a form that cost real work -- an API token pasted from
 * a provider's dashboard, a template with its providers, domain and tags chosen one by one --
 * one stray key threw all of it away, with no undo and no trace of what had been in it.
 * `ExposeModal` grew its own answer to that first; this is that answer in one place, so the
 * dialogs that come after it do not each have to remember.
 *
 * Pass whether the form currently has something to lose. `requestClose` asks first when it
 * does and closes straight away when it does not, so a dialog nobody typed into still shuts
 * on the first Escape -- a confirmation that fires every time is one people learn to click
 * through. Give it to the dialog's `onClose` *and* to its Cancel button: the two ways out
 * should not behave differently. Render `UnsavedGuardElement` anywhere inside the dialog;
 * `ConfirmDialog` portals to the body, so its place in the tree does not matter.
 *
 * What must keep calling the plain close is the path that succeeded: after a save there is
 * nothing left to lose, and asking would be asking about a form the server already has.
 */
export function useUnsavedGuard(isDirty: boolean, close: () => void) {
  const t = useT();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  // Read through refs, and refresh them in an effect rather than during render -- the same
  // contract `useModalDialog` is on. `Modal` hands `onClose` to a `keydown` listener; a fresh
  // function on every keystroke would tear that listener down and set it up again for nothing.
  const dirtyRef = useRef(isDirty);
  const closeRef = useRef(close);
  useEffect(() => {
    dirtyRef.current = isDirty;
    closeRef.current = close;
  });

  const requestClose = useCallback(async () => {
    if (
      dirtyRef.current &&
      !(await confirm({
        title: t('ui.unsaved.title'),
        message: t('ui.unsaved.message'),
        confirmLabel: t('ui.unsaved.confirm'),
        cancelLabel: t('ui.unsaved.cancel'),
        variant: 'danger',
      }))
    ) {
      return;
    }
    closeRef.current();
  }, [confirm, t]);

  return { requestClose, UnsavedGuardElement: ConfirmDialogElement };
}
