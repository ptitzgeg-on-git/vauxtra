/**
 * The wizard's step model, kept apart from the components so both `Setup.tsx` and the
 * stepper read the same list.
 *
 * `StepName` has nine members but the rail shows seven: `restore` is a branch of `welcome`
 * and `provider-form` is a sub-screen of `providers`. Folding them keeps the rail from
 * jumping around when the user opens a sub-screen.
 */

import type { StepName } from './types';

export const RAIL_STEPS = ['welcome', 'password', 'providers', 'notifications', 'docker', 'import', 'done'] as const;

export type RailStep = (typeof RAIL_STEPS)[number];

/** Which rail entry lights up for each of the nine wizard screens. */
export const RAIL_OF: Record<StepName, RailStep> = {
  welcome: 'welcome',
  restore: 'welcome',
  password: 'password',
  providers: 'providers',
  'provider-form': 'providers',
  notifications: 'notifications',
  docker: 'docker',
  import: 'import',
  done: 'done',
};

/** 0-based position of a screen in the rail. */
export function railIndex(step: StepName): number {
  const rail = RAIL_OF[step] ?? 'welcome';
  const index = RAIL_STEPS.indexOf(rail);
  return index < 0 ? 0 : index;
}

/** Completion percentage: the first screen is 0 %, the last is 100 %. */
export function setupProgress(step: StepName): number {
  return Math.round((railIndex(step) / (RAIL_STEPS.length - 1)) * 100);
}

/** Session keys the wizard owns; cleared together when it finishes or a restore lands. */
export const SETUP_SESSION_KEYS = ['step', 'skipPassword', 'formData', 'wizardMode', 'guidedStepIndex'] as const;
