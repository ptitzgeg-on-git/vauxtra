import { useSyncExternalStore } from 'react';
import { readJSON, writeJSON } from '@/lib/storage';

/**
 * Whether a setup wizard is under way in this tab.
 *
 * The boot gate used to show the wizard only while the server answered `setup_required`, and
 * the server stops saying so as soon as setup has happened in its own sense: a password set
 * (the wizard's password step closes the open setup routes on purpose) or a first integration
 * saved. Both happen in the middle of the wizard. So the recommended path -- set a password --
 * dropped the operator onto an empty dashboard after the second screen, with integrations,
 * notifications, Docker and import never shown; and a reload after the first integration did
 * the same.
 *
 * So the wizard marks itself started when "Fresh install" is chosen, next to the step it
 * already keeps in `sessionStorage` for reloads, and from then on this tab stays in setup --
 * Back to the welcome screen included -- whatever the server now answers. Finishing clears
 * the mark and tells the gate; so does the restore branch, which never sets it.
 * Nothing here widens access: the gate still sends an unauthenticated caller to the login
 * screen first, and every call the wizard makes is checked by the server as before. The
 * restore branch, the one screen that replaces everything without a typed word, stays behind
 * the server's own answer (`canRestore` in `Setup`).
 */

export const SETUP_STORAGE_PREFIX = 'vauxtra.setup.';
const ACTIVE_KEY = `${SETUP_STORAGE_PREFIX}active`;

const isTrue = (value: unknown): value is true => value === true;

const listeners = new Set<() => void>();

export function readWizardInProgress(): boolean {
  // Storage blocked or unreadable: fall back to what the server says, as before.
  return readJSON(ACTIVE_KEY, isTrue, false, 'session');
}

/** Called when the operator chooses "Fresh install". Cleared with the rest of the wizard state. */
export function markWizardStarted(): void {
  // Storage blocked: the wizard then lives as long as the server requires setup, as before.
  writeJSON(ACTIVE_KEY, true, 'session');
  notifyWizardSession();
}

/** Called by the wizard after it writes or clears its state. */
export function notifyWizardSession(): void {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useWizardInProgress(): boolean {
  return useSyncExternalStore(subscribe, readWizardInProgress, () => false);
}
