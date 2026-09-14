/**
 * The wizard's provider list, reached the way an operator reaches it.
 *
 * `ProvidersStep.test.tsx` renders that screen with its props set by hand; this file checks
 * that the wizard actually sets them, and it does it from the state a reload leaves behind.
 * The wizard survives a refresh on purpose -- `useSessionState` puts the step in
 * `sessionStorage` and the file's own header calls that load-bearing -- but the provider list
 * is server data and does not travel with it. It used to be a plain `useState([])` filled by
 * one imperative fetch on the password step, so coming back to the providers screen restored
 * the step without the list, and nothing fetched it again: "No integration yet" for an
 * instance with integrations, and a footer offering to skip past them.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { ThemeProvider } from '@/theme';
import type { ProviderItem } from '@/components/features/setup';

let providerRows: ProviderItem[] = [];
/** The first paint after a reload has this, not an answer. */
let providersPending = false;
let providersFail = false;

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/providers') {
        if (providersPending) return new Promise(() => {});
        if (providersFail) return Promise.reject(new Error('backend unreachable'));
        return Promise.resolve(providerRows);
      }
      if (path === '/providers/types') return Promise.resolve({});
      return Promise.resolve({});
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Setup } = await import('./Setup');

const NPM: ProviderItem = { id: 1, name: 'nginx-proxy-manager', type: 'npm' };

/** Exactly what a refresh on the providers screen leaves in the tab. */
function reloadOnProvidersStep() {
  sessionStorage.setItem('vauxtra.setup.step', JSON.stringify('providers'));
  return renderWithProviders(
    <ThemeProvider>
      <Setup onComplete={() => {}} />
    </ThemeProvider>,
  );
}

const shimmers = () => document.querySelectorAll('.animate-shimmer');
const skipButton = () => screen.queryByRole('button', { name: 'setup.providers.skip' });

beforeEach(() => {
  providerRows = [NPM];
  providersPending = false;
  providersFail = false;
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe('Setup, the providers screen after a refresh', () => {
  it('reads the list again instead of coming back empty', async () => {
    reloadOnProvidersStep();
    expect(await screen.findByText('nginx-proxy-manager')).toBeInTheDocument();
    expect(screen.queryByText('setup.providers.empty_title')).toBeNull();
    expect(skipButton()).toBeNull();
  });

  it('does not claim the instance has none of them while that read is in flight', async () => {
    providersPending = true;
    reloadOnProvidersStep();
    await screen.findByText('setup.providers.title');
    expect(screen.queryByText('setup.providers.empty_title')).toBeNull();
    expect(shimmers().length).toBeGreaterThan(0);
    expect(skipButton()).toBeNull();
  });

  it('says the read failed rather than that there is nothing connected', async () => {
    providersFail = true;
    reloadOnProvidersStep();
    expect(await screen.findByText('setup.providers.load_failed')).toBeInTheDocument();
    expect(screen.queryByText('setup.providers.empty_title')).toBeNull();
  });

  it('still says it on an instance that really has none', async () => {
    providerRows = [];
    reloadOnProvidersStep();
    expect(await screen.findByText('setup.providers.empty_title')).toBeInTheDocument();
    expect(shimmers()).toHaveLength(0);
    expect(skipButton()).toBeInTheDocument();
  });
});
