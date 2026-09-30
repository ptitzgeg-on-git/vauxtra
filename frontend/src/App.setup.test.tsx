/**
 * The boot gate and a setup wizard under way.
 *
 * The gate showed the wizard only while the server answered `setup_required`, and the server
 * stops saying so mid-wizard: the password step closes setup on purpose, and so does the
 * first saved integration. Choosing the recommended option -- set a password -- therefore
 * landed on an empty dashboard after the second screen, and a reload after the first
 * integration did the same. The wizard's own saved step now keeps the screen until it ends.
 *
 * `t()` returns the key here, so the assertions name screens rather than English.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { ThemeProvider } from '@/theme';

const SETUP_DONE = { authenticated: true, auth_required: false, setup_required: false, auth_mode: 'open' };
let authAnswer: Record<string, unknown> = SETUP_DONE;

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => Promise.resolve(path === '/auth/me' ? authAnswer : [])),
    post: vi.fn(() => Promise.resolve({})),
    put: vi.fn(() => Promise.resolve({})),
    delete: vi.fn(() => Promise.resolve({})),
  },
}));

const { AuthGate } = await import('./App');

const gate = () =>
  renderWithProviders(
    <ThemeProvider>
      <AuthGate />
    </ThemeProvider>,
  );

/** A wizard started in this tab ("Fresh install" chosen) and left on `step`. */
const startedAt = (step: string) => {
  sessionStorage.setItem('vauxtra.setup.step', JSON.stringify(step));
  sessionStorage.setItem('vauxtra.setup.active', 'true');
};
/** Only a step saved, as a tab that opened the wizard but never chose "Fresh install" leaves it. */
const savedStep = (step: string) => sessionStorage.setItem('vauxtra.setup.step', JSON.stringify(step));

describe('the boot gate, with a setup wizard under way in this tab', () => {
  afterEach(() => {
    sessionStorage.clear();
    authAnswer = SETUP_DONE;
  });

  it('keeps the wizard once the server no longer requires setup', async () => {
    startedAt('providers');
    gate();
    expect(await screen.findByText('setup.providers.title')).toBeInTheDocument();
  });

  it('still asks for the password first when the panel is protected', async () => {
    authAnswer = { ...SETUP_DONE, auth_required: true, authenticated: false, auth_mode: 'password' };
    startedAt('providers');
    gate();
    expect(await screen.findByText('login.eyebrow')).toBeInTheDocument();
    expect(screen.queryByText('setup.providers.title')).toBeNull();
  });

  it('does not count a wizard nobody started as under way', async () => {
    savedStep('welcome');
    gate();
    expect((await screen.findAllByText('nav.dashboard')).length).toBeGreaterThan(0);
    expect(screen.queryByText('setup.welcome.title')).toBeNull();
  });

  it('opens the app when nothing is under way and setup is done', async () => {
    gate();
    expect((await screen.findAllByText('nav.dashboard')).length).toBeGreaterThan(0);
  });

  it('does not offer the unguarded restore once the instance is no longer unconfigured', async () => {
    // The wizard's restore asks for no typed word, which is only acceptable while there is
    // nothing to lose. A resumed wizard can be walked back to its welcome screen.
    startedAt('password');
    gate();
    await userEvent.click(await screen.findByRole('button', { name: 'common.back' }));
    expect(await screen.findByText('setup.welcome.fresh_title')).toBeInTheDocument();
    expect(screen.queryByText('setup.welcome.restore_title')).toBeNull();
  });

  it('offers it on an instance the server still calls unconfigured', async () => {
    authAnswer = { ...SETUP_DONE, setup_required: true };
    gate();
    expect(await screen.findByText('setup.welcome.restore_title')).toBeInTheDocument();
  });
});
