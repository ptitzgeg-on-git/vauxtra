/**
 * Which button a confirmation opens on, and what Enter does to it.
 *
 * The dialog used to decide this from `variant === 'danger'`, reading the variant as a
 * severity dial. It is not one. `warning` is what the *worse* confirmations are built with:
 * forcing an integration out while services still depend on it, deleting a domain that is in
 * use, running a reconcile that writes to a live provider. `danger` is what the harmless ones
 * use. Measured on the running app, both pairs came out the same way round:
 *
 *     domain not in use        danger    opened on Cancel
 *     domain in use            warning   opened on Delete
 *     integration, 1st screen  danger    opened on Cancel
 *     integration, 2nd screen  warning   opened on Force removal
 *
 * So the protected dialog was the inoffensive one, and Enter on the escalated one destroyed
 * the thing it was asking about. The question a dialog has to answer is "does confirming
 * remove something", and only `info` -- which adds -- may open on the confirm button.
 *
 * `EXPECTED_FOCUS` is typed `Record<ConfirmVariant, ...>`, so adding a variant to the union
 * without deciding this fails the build rather than quietly opening on Confirm.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { ConfirmDialog, type ConfirmVariant } from './ConfirmDialog';

const CONFIRM = 'Force removal';
const CANCEL = 'Cancel';

const EXPECTED_FOCUS: Record<ConfirmVariant, 'confirm' | 'cancel'> = {
  danger: 'cancel',
  warning: 'cancel',
  info: 'confirm',
};

function open(props: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  renderWithProviders(
    <ConfirmDialog
      open
      title="Services still depend on it"
      message="1 service still points at Technitium DNS."
      confirmLabel={CONFIRM}
      cancelLabel={CANCEL}
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return {
    onConfirm,
    onCancel,
    confirmButton: screen.getByRole('button', { name: CONFIRM }),
    cancelButton: screen.getByRole('button', { name: CANCEL }),
  };
}

describe('ConfirmDialog opening focus', () => {
  for (const [variant, expected] of Object.entries(EXPECTED_FOCUS) as [ConfirmVariant, 'confirm' | 'cancel'][]) {
    it(`opens on ${expected} for ${variant}`, () => {
      const { confirmButton, cancelButton } = open({ variant });
      expect(document.activeElement).toBe(expected === 'confirm' ? confirmButton : cancelButton);
    });
  }

  it('defaults to cancel when no variant is given', () => {
    const { cancelButton } = open();
    expect(document.activeElement).toBe(cancelButton);
  });

  it('puts the caret in the box when the action has to be typed out', () => {
    open({ variant: 'danger', requireText: 'vxlab.test' });
    expect(document.activeElement).toBe(screen.getByRole('textbox'));
  });
});

describe('ConfirmDialog and a stray Enter', () => {
  for (const [variant, expected] of Object.entries(EXPECTED_FOCUS) as [ConfirmVariant, 'confirm' | 'cancel'][]) {
    it(`${expected === 'cancel' ? 'does not confirm' : 'confirms'} on Enter for ${variant}`, async () => {
      const { onConfirm, onCancel } = open({ variant });
      await userEvent.keyboard('{Enter}');
      if (expected === 'cancel') {
        expect(onConfirm).not.toHaveBeenCalled();
        expect(onCancel).toHaveBeenCalledTimes(1);
      } else {
        expect(onConfirm).toHaveBeenCalledTimes(1);
      }
    });
  }

  it('never confirms on Enter while the typed text is missing', async () => {
    const { onConfirm } = open({ variant: 'danger', requireText: 'vxlab.test' });
    await userEvent.keyboard('{Enter}');
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
