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
 *
 * The second half answers a report from production (2026-09-22): "Importer la sélection
 * (32)" asked its question, nobody clicked Importer, and 32 services were written anyway.
 * It was not reproduced, so these tests pin down every way a question can end. Two answer
 * yes: the confirm button, and Enter in the typed-text box. Losing the focus answers
 * nothing; unmounting and a second question answer no. And one way it could answer yes
 * without anyone meaning it is now closed: the import asked with `info`, which opened on
 * Importer, and a held Enter repeats, so the press that opened the question could go on to
 * answer it. The report describes a click, not a key: this is a way it can happen, not what
 * happened that day.
 */

import { useEffect } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, createEvent, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { ConfirmDialog, useConfirmDialog, type ConfirmOptions, type ConfirmVariant } from './ConfirmDialog';

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

const IMPORT: ConfirmOptions = {
  title: 'Import the selected routes?',
  message: '32 routes will be imported as Vauxtra services.',
  confirmLabel: CONFIRM,
  cancelLabel: CANCEL,
  variant: 'info',
};

/** A page with one button that asks `question` through the hook, the way every caller does. */
function Asker({ question, onAnswer }: { question: ConfirmOptions; onAnswer: (ok: boolean) => void }) {
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  return (
    <>
      <button type="button" onClick={() => void confirm(question).then(onAnswer)}>
        Ask
      </button>
      {ConfirmDialogElement}
    </>
  );
}

/** The hook's `confirm`, handed out so a test can ask twice without a click in between. */
function Hook({ onReady }: { onReady: (confirm: (next: ConfirmOptions) => Promise<boolean>) => void }) {
  const { confirm, ConfirmDialogElement } = useConfirmDialog();
  useEffect(() => onReady(confirm), [confirm, onReady]);
  return ConfirmDialogElement;
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

  it('opens on cancel when the caller asks for it, even on an info dialog', () => {
    const { cancelButton } = open({ variant: 'info', initialFocus: 'cancel' });
    expect(document.activeElement).toBe(cancelButton);
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

  it('cancels on Enter when an info dialog was asked to open on cancel', async () => {
    const { onConfirm, onCancel } = open({ variant: 'info', initialFocus: 'cancel' });
    await userEvent.keyboard('{Enter}');
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('ConfirmDialog and a held Enter', () => {
  it('does not take the Enter that asked the question as its answer', async () => {
    const onAnswer = vi.fn();
    renderWithProviders(<Asker question={IMPORT} onAnswer={onAnswer} />);
    screen.getByRole('button', { name: 'Ask' }).focus();

    // One press, held: the first key down opens the dialog, the repeats land inside it.
    await userEvent.keyboard('{Enter>4/}');

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: CONFIRM }));
    expect(onAnswer).not.toHaveBeenCalled();
  });

  it('still confirms on a fresh press once the held one is released', async () => {
    const onAnswer = vi.fn();
    renderWithProviders(<Asker question={IMPORT} onAnswer={onAnswer} />);
    screen.getByRole('button', { name: 'Ask' }).focus();

    await userEvent.keyboard('{Enter>4/}');
    await userEvent.keyboard('{Enter}');

    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith(true));
  });

  it('refuses the repeat on the confirm button itself, and only the repeat', () => {
    const { confirmButton } = open({ variant: 'info' });

    const held = createEvent.keyDown(confirmButton, { key: 'Enter', repeat: true });
    fireEvent(confirmButton, held);
    const fresh = createEvent.keyDown(confirmButton, { key: 'Enter' });
    fireEvent(confirmButton, fresh);

    // The browser clicks a button on an Enter key down whose default was not prevented.
    expect(held.defaultPrevented).toBe(true);
    expect(fresh.defaultPrevented).toBe(false);
  });

  it('does not submit the typed text on a repeat either', () => {
    const { onConfirm } = open({ variant: 'danger', requireText: 'RESET' });
    const box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: 'RESET' } });

    fireEvent.keyDown(box, { key: 'Enter', repeat: true });
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe('useConfirmDialog, every way a question ends', () => {
  it('answers nothing when the focus leaves the dialog or the window', async () => {
    const onAnswer = vi.fn();
    renderWithProviders(<Asker question={IMPORT} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }));
    const confirmButton = screen.getByRole('button', { name: CONFIRM });

    fireEvent.focusOut(confirmButton);
    confirmButton.blur();
    fireEvent.blur(window);
    await act(async () => {});

    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // The witness: the same question does end, and on the button that says so.
    await userEvent.click(screen.getByRole('button', { name: CANCEL }));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith(false));
  });

  it('answers no when the page that asked goes away', async () => {
    const onAnswer = vi.fn();
    const { unmount } = renderWithProviders(<Asker question={IMPORT} onAnswer={onAnswer} />);
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }));

    unmount();

    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith(false));
    expect(onAnswer).toHaveBeenCalledTimes(1);
  });

  it('answers the first question no when a second one is asked', async () => {
    let ask: ((next: ConfirmOptions) => Promise<boolean>) | undefined;
    renderWithProviders(<Hook onReady={(confirm) => (ask = confirm)} />);

    let first: Promise<boolean> | undefined;
    let second: Promise<boolean> | undefined;
    act(() => {
      first = ask?.({ ...IMPORT, title: 'First' });
    });
    act(() => {
      second = ask?.({ ...IMPORT, title: 'Second' });
    });

    await expect(first).resolves.toBe(false);
    expect(screen.getByRole('dialog', { name: 'Second' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: CONFIRM }));
    await expect(second).resolves.toBe(true);
  });
});
