/**
 * The guided panel: what it shows when there is only one step, and which of its buttons is
 * painted as the action to take.
 *
 * Two defects sat here together. A type whose wizard has a single step -- adguard and traefik,
 * two of the ten the API serves -- printed "Step 1 of 1" above a single pagination dot whose
 * only destination was the step already on screen. And the button that leaves the panel was
 * `primary`, the same blue as the footer's "Validate" a hundred and sixty pixels below, while
 * being the one of the two that creates nothing.
 *
 * The finding proposed dropping that button along with the counter. Reading the panel showed
 * that would be wrong, and the last test here is why: it is the only way to reach the field
 * where the integration is named. So it stays, demoted and renamed after its destination.
 *
 * A third defect hid behind this file rather than being caught by it: each dot is a button
 * that jumps to its step, and each carried `role="listitem"`, which overrode the button role
 * and left the set announced as a list nobody could be told was clickable. The dots are asked
 * for by the role they actually have now.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { buttonVariants } from '@/components/ui';
import { emptyForm, type GuidedStep } from '@/components/features/providers/providerConstants';
import { StepCredentials } from './StepCredentials';

const STEP_COUNTER = 'provider_modal.guided.step';
const DOTS = 'provider_modal.mode.guided';
const FINISH = 'provider_modal.guided.finish';
const NEXT = 'provider_modal.guided.next';

const ONE_STEP: GuidedStep[] = [
  { title: 'Open Settings > General', body: 'Copy the address and the admin account.' },
];
const THREE_STEPS: GuidedStep[] = [
  { title: 'Open the admin panel', body: 'Sign in as an administrator.' },
  { title: 'Create a token', body: 'Advanced > API, then Generate.' },
  { title: 'Copy it here', body: 'The token is shown once.' },
];

/**
 * What `variant="primary"` sets and no other variant does, asked of `Button` itself rather
 * than written out here -- renaming a class in `Button.tsx` must not leave this test quietly
 * matching a token that no longer exists. `test_the_marker_is_real` below is that guarantee.
 *
 * The `bg-primary` of the active pagination dot is not enough on its own to be caught.
 */
const PRIMARY_ONLY = buttonVariants({ variant: 'primary', size: 'sm' })
  .split(' ')
  .filter((c) => !buttonVariants({ variant: 'outline', size: 'sm' }).split(' ').includes(c));

const isPrimary = (el: HTMLElement) => PRIMARY_ONLY.every((c) => el.classList.contains(c));

function panel(guidedSteps: GuidedStep[], guidedStepIndex = 0) {
  const onGuidedStepChange = vi.fn();
  renderWithProviders(
    <StepCredentials
      formData={{ ...emptyForm, type: 'adguard' }}
      onChange={vi.fn()}
      guidedSteps={guidedSteps}
      mode="guided"
      onModeChange={vi.fn()}
      guidedStepIndex={guidedStepIndex}
      onGuidedStepChange={onGuidedStepChange}
      validationResult={null}
    />,
  );
  return { onGuidedStepChange };
}

describe('A one-step wizard is not a journey', () => {
  it('counts nothing when there is nothing to count', () => {
    panel(ONE_STEP);
    expect(screen.queryByText(STEP_COUNTER)).toBeNull();
    expect(screen.queryByRole('group', { name: DOTS })).toBeNull();
  });

  it('still counts, and still paginates, as soon as there are two steps', () => {
    panel(THREE_STEPS);
    expect(screen.getByText(STEP_COUNTER)).toBeInTheDocument();
    const dots = within(screen.getByRole('group', { name: DOTS })).getAllByRole('button');
    expect(dots).toHaveLength(THREE_STEPS.length);
  });

  it('shows the step itself either way', () => {
    panel(ONE_STEP);
    expect(screen.getByText(ONE_STEP[0].title)).toBeInTheDocument();
    expect(screen.getByText(ONE_STEP[0].body)).toBeInTheDocument();
  });
});

describe('Only the button that moves forward is blue', () => {
  it('the marker it looks for is a real class', () => {
    expect(PRIMARY_ONLY.length).toBeGreaterThan(0);
  });

  it.each([
    ['a single step', ONE_STEP, 0],
    ['the last of three', THREE_STEPS, THREE_STEPS.length - 1],
  ])('leaves the panel without arming a second primary button on %s', (_label, steps, index) => {
    panel(steps, index);
    expect(isPrimary(screen.getByRole('button', { name: FINISH }))).toBe(false);
  });

  it('keeps "next" primary while there is a next step, so one button still leads', () => {
    panel(THREE_STEPS, 0);
    expect(isPrimary(screen.getByRole('button', { name: NEXT }))).toBe(true);
    expect(screen.queryByRole('button', { name: FINISH })).toBeNull();
  });
});

describe('The last step leads somewhere', () => {
  it('opens the naming step, which is why the button was kept', async () => {
    // The finding asked for this button to be removed on a one-step wizard. It must not be:
    // past `guidedSteps.length` the panel hands over to the field that names the integration,
    // and no other control does.
    const { onGuidedStepChange } = panel(ONE_STEP);
    await userEvent.click(screen.getByRole('button', { name: FINISH }));
    expect(onGuidedStepChange).toHaveBeenCalledWith(ONE_STEP.length);
  });
});
