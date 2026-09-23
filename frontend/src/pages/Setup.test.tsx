/**
 * The wizard's two server-backed screens, reached the way an operator reaches them.
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
 * The import step behind it was bound to a transition in the same way: the scan ran on the
 * step change out of the Docker screen and nowhere else, so a reload landing straight on it
 * scanned nothing and drew the last rung of its ladder -- a green tick reading "No services
 * found to import" -- on the one screen whose next button ends setup.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { ThemeProvider } from '@/theme';
import type { ProviderItem, SyncResult } from '@/components/features/setup';

let providerRows: ProviderItem[] = [];
/** The first paint after a reload has this, not an answer. */
let providersPending = false;
let providersFail = false;
/** `POST /api/services/sync` is the whole content of the import step. */
let syncResult: SyncResult = { proxy_hosts: [], dns_rewrites: [] };
let syncCalls = 0;

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
    post: vi.fn((path: string) => {
      if (path === '/services/sync') {
        syncCalls += 1;
        return Promise.resolve(syncResult);
      }
      return Promise.resolve({ ok: true });
    }),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Setup } = await import('./Setup');
const { api } = await import('@/api/client');

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

/** And what a refresh on the import screen leaves, which replays no transition at all. */
function reloadOnImportStep() {
  sessionStorage.setItem('vauxtra.setup.step', JSON.stringify('import'));
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
  syncResult = { proxy_hosts: [], dns_rewrites: [] };
  syncCalls = 0;
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

describe('Setup, the import screen after a refresh', () => {
  it('scans, instead of ticking a scan it never ran', async () => {
    syncResult = {
      proxy_hosts: [
        {
          domain_names: ['app.example.com'],
          forward_host: '10.0.0.9',
          forward_port: 8080,
          _provider_name: 'nginx-proxy-manager',
          _provider_type: 'npm',
        },
      ],
      dns_rewrites: [],
    };
    reloadOnImportStep();
    expect(await screen.findByText('app.example.com')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.none_found')).toBeNull();
    expect(syncCalls).toBe(1);
  });

  it('says a read failed rather than that no provider is configured', async () => {
    providersFail = true;
    reloadOnImportStep();
    expect(await screen.findByText('setup.import.scan_failed')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.no_providers')).toBeNull();
    expect(screen.queryByText('setup.import.none_found')).toBeNull();
    //: There was no list to scan over, so nothing was asked of the providers either.
    expect(syncCalls).toBe(0);
  });

  it('does not answer for the scan while the list it runs over is in flight', async () => {
    providersPending = true;
    reloadOnImportStep();
    expect(await screen.findByText('setup.import.scanning')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.none_found')).toBeNull();
    expect(screen.queryByText('setup.import.no_providers')).toBeNull();
  });

  it('still ticks when the scan itself came back with nothing', async () => {
    reloadOnImportStep();
    expect(await screen.findByText('setup.import.none_found')).toBeInTheDocument();
    expect(syncCalls).toBe(1);
  });

  it('still says no provider is configured on an instance that has none', async () => {
    providerRows = [];
    reloadOnImportStep();
    expect(await screen.findByText('setup.import.no_providers')).toBeInTheDocument();
    expect(syncCalls).toBe(0);
  });
});

describe('Setup, importing from the scan', () => {
  it('sends back the names ticked, and nothing else', async () => {
    // The import declares the zone of every service it creates, so a name left unticked must
    // not travel at all. The flat list this step used to show kept to that; the zones must too.
    syncResult = {
      proxy_hosts: [
        { domain_names: ['app.example.com'], forward_host: '10.0.0.9', forward_port: 8080, _provider_id: 1, _provider_name: 'nginx-proxy-manager', _provider_type: 'npm', _zone: 'example.com' },
        { domain_names: ['shop.other.net'], forward_host: '10.0.0.8', forward_port: 80, _provider_id: 1, _provider_name: 'nginx-proxy-manager', _provider_type: 'npm', _zone: 'other.net' },
      ],
      dns_rewrites: [],
    };
    reloadOnImportStep();
    await userEvent.click(await screen.findByRole('checkbox', { name: /app\.example\.com/ }));
    await userEvent.click(screen.getByRole('button', { name: 'setup.import.finish_import' }));

    const importCall = vi.mocked(api.post).mock.calls.find(([path]) => path === '/services/import');
    expect(importCall?.[1]).toEqual({ proxy_hosts: [syncResult.proxy_hosts?.[0]], dns_rewrites: [] });
  });
});
