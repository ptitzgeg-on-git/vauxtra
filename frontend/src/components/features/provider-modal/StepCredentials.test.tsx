/**
 * The credentials step of the Integrations dialog: where the name is asked, what holds the
 * guided "Next" back, and what the panel says once it has run out of steps.
 *
 * A type whose wizard has a single step -- adguard and traefik, two of the ten the API serves --
 * used to print "Step 1 of 1" above a single pagination dot whose only destination was the step
 * already on screen. The first block keeps that fixed.
 *
 * The rest is about the guided mode of 1.5.2. Measured in production on 2026-09-22: the last
 * step ended on a button called "Name the integration", which led past the steps to a screen
 * holding the name field and a green "Everything is filled in" -- printed whatever the steps
 * held, over a form with an empty URL, username and password. In the code, "Next" moved on
 * over empty required fields and the dots jumped to any step, so an empty form could reach that
 * banner. The name was asked nowhere else in guided mode, and the NPM e-mail was required in the
 * guided steps while the expert form of the same dialog called it optional.
 *
 * So: the name comes first, in both modes. "Next" and the dots wait for the step's required
 * fields. The last step names what is still missing, with a link to the step that asks for it,
 * and says everything is there only when it is. There is no button past the last step: the
 * footer's "Validate" is the one action left, and the only blue one.
 *
 * Each dot is a button that jumps to its step. They carried `role="listitem"` once, which
 * overrode the button role and left the set announced as a list nobody could be told was
 * clickable; they are asked for by the role they actually have.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { buttonVariants } from '@/components/ui';
import {
  emptyForm,
  getGuidedSteps,
  type ProviderFormState,
  type ProviderTypeMeta,
  type ProviderValidationResult,
} from '@/components/features/providers/providerConstants';
import type { GuidedStepField } from '@/types/api';
import { StepCredentials, type WizardMode } from './StepCredentials';

const STEP_COUNTER = 'provider_modal.guided.step';
const DOTS = 'provider_modal.mode.guided';
const NEXT = 'provider_modal.guided.next';
const BACK = 'provider_modal.guided.back';
const MISSING = 'provider_modal.missing.title';
const COLLECTED = 'provider_modal.guided.collected';
const NAME = /^provider_modal\.field\.name\b/;

const field = (key: string, extra: Partial<GuidedStepField> = {}): GuidedStepField => ({ key, label: `label:${key}`, ...extra });

/** Shaped like the AdGuard Home wizard the API serves: one step, three fields. */
const ONE_STEP: ProviderTypeMeta = {
  label: 'AdGuard Home',
  guided_steps: [
    {
      title: 'Open Settings > General',
      body: 'Copy the address and the admin account.',
      fields: [field('url', { input_type: 'url' }), field('username'), field('password', { input_type: 'password' })],
    },
  ],
};

/** Shaped like the NPM wizard: one required field per step. */
const THREE_STEPS: ProviderTypeMeta = {
  label: 'Nginx Proxy Manager',
  guided_steps: [
    { title: 'Where it answers', body: 'The address of the admin panel.', fields: [field('url', { input_type: 'url' })] },
    { title: 'The account', body: 'The e-mail you sign in with.', fields: [field('username')] },
    { title: 'Its password', body: 'The password of that account.', fields: [field('password', { input_type: 'password' })] },
  ],
};

/** Shaped like the Zoraxy wizard: the account is optional. */
const OPTIONAL_ACCOUNT: ProviderTypeMeta = {
  label: 'Zoraxy',
  guided_steps: [
    { title: 'Where it answers', body: 'The address.', fields: [field('url', { input_type: 'url' }), field('username', { optional: true })] },
    { title: 'Its password', body: 'If any.', fields: [field('password', { input_type: 'password', optional: true })] },
  ],
};

const FILLED: Partial<ProviderFormState> = {
  name: 'NPM at the lab',
  url: 'http://127.0.0.1:3081',
  username: 'admin@example.com',
  password: 'not-a-real-secret',
};

/**
 * What `variant="primary"` sets and no other variant does, asked of `Button` itself rather
 * than written out here -- renaming a class in `Button.tsx` must not leave this test quietly
 * matching a token that no longer exists. The first assertion of `is primary while there is a
 * next step, so one button still leads` below is that guarantee.
 *
 * The `bg-primary` of the active pagination dot is not enough on its own to be caught.
 */
const PRIMARY_ONLY = buttonVariants({ variant: 'primary', size: 'sm' })
  .split(' ')
  .filter((c) => !buttonVariants({ variant: 'outline', size: 'sm' }).split(' ').includes(c));

const isPrimary = (el: HTMLElement) => PRIMARY_ONLY.every((c) => el.classList.contains(c));

interface PanelOptions {
  type?: string;
  meta?: ProviderTypeMeta;
  form?: Partial<ProviderFormState>;
  index?: number;
  mode?: WizardMode;
  editMode?: boolean;
  validationResult?: ProviderValidationResult | null;
}

function panel({
  type = 'npm',
  meta = THREE_STEPS,
  form = {},
  index = 0,
  mode = 'guided',
  editMode = false,
  validationResult = null,
}: PanelOptions = {}) {
  const onGuidedStepChange = vi.fn();
  renderWithProviders(
    <StepCredentials
      formData={{ ...emptyForm, type, ...form }}
      onChange={vi.fn()}
      meta={meta}
      guidedSteps={getGuidedSteps(type, meta)}
      mode={mode}
      onModeChange={vi.fn()}
      guidedStepIndex={index}
      onGuidedStepChange={onGuidedStepChange}
      validationResult={validationResult}
      editMode={editMode}
    />,
  );
  return { onGuidedStepChange };
}

const dots = () => within(screen.getByRole('group', { name: DOTS })).getAllByRole('button');

describe('A one-step wizard is not a journey', () => {
  it('counts nothing when there is nothing to count', () => {
    panel({ type: 'adguard', meta: ONE_STEP });
    expect(screen.queryByText(STEP_COUNTER)).toBeNull();
    expect(screen.queryByRole('group', { name: DOTS })).toBeNull();
  });

  it('still counts, and still paginates, as soon as there are two steps', () => {
    panel();
    expect(screen.getByText(STEP_COUNTER)).toBeInTheDocument();
    expect(dots()).toHaveLength(3);
  });

  it('shows the step itself either way', () => {
    panel({ type: 'adguard', meta: ONE_STEP });
    expect(screen.getByText('Open Settings > General')).toBeInTheDocument();
    expect(screen.getByText('Copy the address and the admin account.')).toBeInTheDocument();
  });
});

describe('The name is asked first, in every mode', () => {
  it.each([
    ['the first guided step', { index: 0 }],
    ['the last guided step', { index: 2 }],
    ['the expert form', { mode: 'expert' as const }],
    ['an edit', { editMode: true, form: FILLED }],
  ])('on %s', (_label, options) => {
    panel(options);
    const name = screen.getByRole('textbox', { name: NAME });
    expect(name).toBeRequired();
    // Before every other field: the guided panel used to ask for it after the last step only.
    const others = screen.getAllByRole('textbox').filter((box) => box !== name);
    for (const other of others) {
      expect(name.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });
});

describe('"Next" waits for what the step asks', () => {
  it('is held back while a required field of the step is empty', () => {
    panel({ index: 0 });
    expect(screen.getByRole('button', { name: NEXT })).toBeDisabled();
    expect(screen.getByText(MISSING)).toBeInTheDocument();
  });

  it('moves on once the step is filled', async () => {
    const { onGuidedStepChange } = panel({ index: 0, form: { url: FILLED.url } });
    await userEvent.click(screen.getByRole('button', { name: NEXT }));
    expect(onGuidedStepChange).toHaveBeenCalledWith(1);
  });

  it('does not wait for a field the step marks optional', () => {
    panel({ type: 'zoraxy', meta: OPTIONAL_ACCOUNT, index: 0, form: { url: FILLED.url } });
    expect(screen.getByRole('button', { name: NEXT })).toBeEnabled();
    expect(screen.queryByText(MISSING)).toBeNull();
  });

  it('does not let the dots jump past the first step still missing something', () => {
    panel({ index: 0, form: { url: FILLED.url } });
    expect(dots().map((dot) => (dot as HTMLButtonElement).disabled)).toEqual([false, false, true]);
  });

  it('is primary while there is a next step, so one button still leads', () => {
    panel({ index: 0, form: { url: FILLED.url } });
    expect(PRIMARY_ONLY.length).toBeGreaterThan(0);
    expect(isPrimary(screen.getByRole('button', { name: NEXT }))).toBe(true);
  });
});

describe('The last step says what is missing, and that everything is there only when it is', () => {
  it('names what is missing instead of calling the form complete', () => {
    panel({ index: 2 });
    expect(screen.getByText(MISSING)).toBeInTheDocument();
    expect(screen.queryByText(COLLECTED)).toBeNull();
  });

  it('links a field asked on an earlier step back to that step', async () => {
    // What a reload leaves behind: the session keeps the step, not the password -- and here
    // the address was cleared from the expert tab after the steps were done.
    const { onGuidedStepChange } = panel({ index: 2, form: { ...FILLED, url: '' } });
    await userEvent.click(screen.getByRole('button', { name: 'label:url' }));
    expect(onGuidedStepChange).toHaveBeenCalledWith(0);
  });

  it('says everything is there once it is', () => {
    panel({ index: 2, form: FILLED });
    expect(screen.getByText(COLLECTED)).toBeInTheDocument();
    expect(screen.queryByText(MISSING)).toBeNull();
  });

  it('stops saying so once the connection has been tested', () => {
    // "Validate the connection to continue" under a verdict that already did.
    panel({ index: 2, form: FILLED, validationResult: { ok: true } });
    expect(screen.queryByText(COLLECTED)).toBeNull();
  });

  it.each([
    ['a single step', { type: 'adguard', meta: ONE_STEP, index: 0 }],
    ['the last of three', { index: 2 }],
  ])('has no button of its own that leaves the panel on %s', (_label, options) => {
    panel(options);
    expect(screen.queryByRole('button', { name: NEXT })).toBeNull();
    // Only the footer's "Validate" is blue then; this panel draws none.
    expect(screen.queryAllByRole('button').filter(isPrimary)).toEqual([]);
  });

  it('can still go back', async () => {
    const { onGuidedStepChange } = panel({ index: 2, form: FILLED });
    await userEvent.click(screen.getByRole('button', { name: BACK }));
    expect(onGuidedStepChange).toHaveBeenCalledWith(1);
  });

  it('lands on the last step from the index of the screen that used to follow it', () => {
    // A dialog left on "Name the integration" held `guidedSteps.length` as its index.
    panel({ index: 3, form: FILLED });
    expect(screen.getByText('Its password')).toBeInTheDocument();
  });
});

describe('The expert form reads the same rule', () => {
  it('requires the NPM e-mail, which the guided steps always did', () => {
    panel({ mode: 'expert' });
    expect(screen.getByRole('textbox', { name: /provider_modal\.field\.username/ })).toBeRequired();
  });

  it('marks what a type can go without, once', () => {
    panel({ type: 'zoraxy', meta: OPTIONAL_ACCOUNT, mode: 'expert' });
    expect(screen.getByRole('textbox', { name: /provider_modal\.field\.username/ })).not.toBeRequired();
    // The account and the password: one "Optional" each, and none in the label itself.
    expect(screen.getAllByText('provider_modal.guided.optional')).toHaveLength(2);
  });

  it('lists what is missing, without links to steps it does not show', () => {
    panel({ mode: 'expert', form: { name: FILLED.name } });
    const list = screen.getByRole('list', { name: MISSING });
    expect(within(list).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'provider_modal.field.url',
      'provider_modal.field.username',
      'provider_modal.field.password',
    ]);
    expect(within(list).queryAllByRole('button')).toEqual([]);
  });

  it('keeps the stored secret out of what an edit is missing', () => {
    panel({ editMode: true, form: { ...FILLED, password: '' } });
    expect(screen.queryByText(MISSING)).toBeNull();
  });
});
