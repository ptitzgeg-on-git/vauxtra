/**
 * What the integrations page says when a read behind its cards did not answer.
 *
 * `GET /providers/health` and `GET /providers/tunnels/health` are the two readings behind
 * every verdict on this page. When they fail, `getHealthScore` returns a score of -1 and
 * `getProviderSeverity` calls each integration `unknown` -- which is correct, and which the
 * two filters below count as neither an issue nor a healthy one. So a full list published
 * `Issues - 0` and `Healthy - 0`, the warning badge in the header went away, and nothing on
 * the screen said a request had failed. The dashboard links here with the sentence
 * "Integration health could not be checked"; the page it opened contradicted it.
 *
 * `GET /providers/types` is the third. It is the catalogue that turns a stored slug into a
 * name -- `npm` is "Nginx Proxy Manager" there -- and it carries `read_only`, the only place
 * the page says an integration cannot be written to. `typeMap` fell back to `{}` on a failed
 * read, which is the right floor and not the defect: `lib/providers.ts` still groups the
 * known types from its own table, so the sections held. What was missing is that nothing
 * said the floor had been used, so every card silently dropped to its slug and Traefik's
 * read-only badge went away, on a page that otherwise looked perfectly healthy.
 *
 * `t()` returns the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose --
 * so a block is on screen iff its key is, and rewording the sentence in eight locale files
 * never turns this red.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { api } from '@/api/client';
import { DIAGNOSTICS_STORAGE_KEY } from '@/components/features/providers/providerHealth';
import type { Provider } from '@/types/api';

const toastSpy = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }));
vi.mock('react-hot-toast', () => ({ default: toastSpy }));

const PROVIDER: Provider = {
  id: 1,
  name: 'NPM at the lab',
  type: 'npm',
  url: 'http://10.0.0.1:81',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

/** Traefik is the one shipped type that declares `read_only`, so it is the badge's fixture. */
const TRAEFIK: Provider = { ...PROVIDER, id: 2, name: 'Traefik at the edge', type: 'traefik' };

/** A second proxy, so "Test all" has two answers to wait for. */
const SECOND: Provider = { ...PROVIDER, id: 3, name: 'NPM in the garage' };

/** What `GET /providers/types` serves, trimmed to the fields these tests read. */
const TYPES = {
  npm: { label: 'Nginx Proxy Manager', category: 'proxy', capabilities: { proxy: true } },
  traefik: {
    label: 'Traefik',
    category: 'proxy',
    read_only: true,
    capabilities: { proxy: true },
  },
};

let autoFails = false;
/** The periodic health round never answers, the way a slow integration keeps it open. */
let autoHangs = false;
let tunnelFails = false;
let typesFails = false;
/** Set when a test wants the *second* read of the catalogue to answer. */
let typesRecoversOnRetry = false;
let typesCalls = 0;
let providerList: Provider[] = [PROVIDER];
/** Test calls that stay open until the test releases them, by path. */
let heldTests: string[] = [];
const release = new Map<string, () => void>();

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/providers') return Promise.resolve(providerList);
      if (path === '/providers/types') {
        typesCalls += 1;
        const answers = !typesFails || (typesRecoversOnRetry && typesCalls > 1);
        return answers ? Promise.resolve(TYPES) : Promise.reject(new Error('down'));
      }
      if (path === '/providers/health') {
        if (autoHangs) return new Promise(() => {});
        return autoFails ? Promise.reject(new Error('down')) : Promise.resolve({});
      }
      if (path === '/providers/tunnels/health') {
        return tunnelFails ? Promise.reject(new Error('down')) : Promise.resolve({ items: [] });
      }
      return Promise.resolve({});
    }),
    post: vi.fn((path: string) => {
      if (!heldTests.includes(path)) return Promise.resolve({ ok: true });
      return new Promise((resolve) => release.set(path, () => resolve({ ok: true })));
    }),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Providers } = await import('./Providers');

/** The filter bar, waited for, so an assertion never races the providers query. */
const filters = () => screen.findByText('providers.filter.all');
const banner = () => screen.queryByText('providers.health.load_failed');
const catalogBanner = () => screen.queryByText('providers.catalog.load_failed');

beforeEach(() => {
  autoFails = false;
  autoHangs = false;
  tunnelFails = false;
  typesFails = false;
  typesRecoversOnRetry = false;
  typesCalls = 0;
  providerList = [PROVIDER];
  heldTests = [];
  release.clear();
  window.localStorage.removeItem(DIAGNOSTICS_STORAGE_KEY);
  vi.clearAllMocks();
});

describe('Providers, when the health checks cannot be reached', () => {
  it('stays quiet while both checks answer', async () => {
    renderWithProviders(<Providers />);
    await filters();
    expect(banner()).toBeNull();
  });

  it('says so when the periodic health request fails', async () => {
    autoFails = true;
    renderWithProviders(<Providers />);
    await filters();
    await waitFor(() => expect(banner()).not.toBeNull());
  });

  it('says so when only the tunnel request fails', async () => {
    tunnelFails = true;
    renderWithProviders(<Providers />);
    await filters();
    await waitFor(() => expect(banner()).not.toBeNull());
  });
});

describe('Providers, when the type catalogue cannot be read', () => {
  it('stays quiet, and names the type, while the catalogue answers', async () => {
    renderWithProviders(<Providers />);
    await filters();

    expect(screen.getByText('Nginx Proxy Manager')).toBeInTheDocument();
    expect(catalogBanner()).toBeNull();
  });

  it('says so when the catalogue read fails', async () => {
    typesFails = true;
    renderWithProviders(<Providers />);
    await filters();

    await waitFor(() => expect(catalogBanner()).not.toBeNull());
    // The health checks answered, so the older banner has to stay away: these are two
    // failures with two retry buttons, and folding them into one would offer the wrong one.
    expect(banner()).toBeNull();
  });

  it('falls back to the stored slug, which is what the banner is there to explain', async () => {
    typesFails = true;
    renderWithProviders(<Providers />);
    await filters();

    await waitFor(() => expect(catalogBanner()).not.toBeNull());
    expect(screen.queryByText('Nginx Proxy Manager')).toBeNull();
    expect(screen.getByText('npm')).toBeInTheDocument();
  });

  it('keeps the read-only badge while the catalogue answers', async () => {
    providerList = [TRAEFIK];
    renderWithProviders(<Providers />);
    await filters();

    expect(await screen.findByText('providers.card.read_only')).toBeInTheDocument();
    expect(catalogBanner()).toBeNull();
  });

  it('loses the read-only badge when it fails, and says why', async () => {
    providerList = [TRAEFIK];
    typesFails = true;
    renderWithProviders(<Providers />);
    await filters();

    // `read_only` is the only place the page says an integration cannot be written to, and
    // there is no fallback table for it: a failed catalogue turns the claim off silently.
    await waitFor(() => expect(catalogBanner()).not.toBeNull());
    expect(screen.queryByText('providers.card.read_only')).toBeNull();
  });

  it('re-reads the catalogue from the banner, not just from Test all', async () => {
    // `handleRefresh` already refetched this query, but it is behind the header's Test all
    // button, which also runs a connection test against every enabled integration. A banner
    // about one failed read has to be able to clear that read on its own.
    typesFails = true;
    typesRecoversOnRetry = true;
    renderWithProviders(<Providers />);
    await filters();
    await waitFor(() => expect(catalogBanner()).not.toBeNull());

    await userEvent.click(screen.getByRole('button', { name: 'common.retry' }));

    expect(await screen.findByText('Nginx Proxy Manager')).toBeInTheDocument();
    expect(catalogBanner()).toBeNull();
    expect(typesCalls).toBeGreaterThan(1);
  });
});

describe('Providers, when the last manual test has gone stale', () => {
  const seed = (minutesAgo: number) =>
    window.localStorage.setItem(
      DIAGNOSTICS_STORAGE_KEY,
      JSON.stringify({ 1: { ok: true, testedAt: Date.now() - minutesAgo * 60 * 1000 } }),
    );

  it('names the hour it ran while the verdict is still fresh', async () => {
    seed(5);
    renderWithProviders(<Providers />);
    await filters();

    expect(screen.getByText('providers.card.last_test')).toBeInTheDocument();
    expect(screen.queryByText('providers.card.never_tested')).toBeNull();
  });

  it('still names it once the verdict has expired', async () => {
    // Past the TTL the card used to say "Never tested" about an integration validated forty
    // minutes earlier, because the stale entry was deleted on restore and written back gone.
    // The verdict is right to expire; the date it ran is a different fact and does not.
    seed(40);
    renderWithProviders(<Providers />);
    await filters();

    expect(screen.getByText('providers.card.last_test')).toBeInTheDocument();
    expect(screen.queryByText('providers.card.never_tested')).toBeNull();
  });

  it('says never only when nothing was ever stored', async () => {
    renderWithProviders(<Providers />);
    await filters();

    expect(screen.getByText('providers.card.never_tested')).toBeInTheDocument();
    expect(screen.queryByText('providers.card.last_test')).toBeNull();
  });
});

describe('Providers, Test all', () => {
  const testAll = () => screen.getByRole('button', { name: /providers\.refresh\.button/ });

  it('sends every test at once, without waiting for the periodic health round', async () => {
    // The button used to await four refetches before sending a test, and one of them is
    // the server-side round that tests every integration in turn. A refetch of a query
    // still loading joins the request in flight, so the click waited for as long as that
    // round did -- with no card moving -- and here, where it never answers, forever.
    autoHangs = true;
    providerList = [PROVIDER, SECOND];
    renderWithProviders(<Providers />);
    await filters();

    await userEvent.click(testAll());

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/providers/1/test');
      expect(api.post).toHaveBeenCalledWith('/providers/3/test');
    });
  });

  it('spins the card still waiting, counts on the button, and settles when the last one answers', async () => {
    providerList = [PROVIDER, SECOND];
    heldTests = ['/providers/3/test'];
    renderWithProviders(<Providers />);
    await filters();

    await userEvent.click(testAll());

    const progress = await screen.findByRole('button', { name: /providers\.refresh\.progress/ });
    expect(progress).toBeDisabled();
    await waitFor(() => {
      const busy = screen.getAllByRole('button', { name: 'providers.actions.test' }).filter((b) => b.getAttribute('aria-busy') === 'true');
      expect(busy).toHaveLength(1);
    });

    release.get('/providers/3/test')?.();

    await waitFor(() => expect(testAll()).toBeEnabled());
    expect(toastSpy.success).toHaveBeenCalledWith('providers.refresh.success_all_passed');
    expect(screen.queryByRole('button', { name: /providers\.refresh\.progress/ })).toBeNull();
  });

  it('says there is nothing to test rather than claiming a refresh', async () => {
    providerList = [{ ...PROVIDER, enabled: false }];
    renderWithProviders(<Providers />);
    await filters();

    await userEvent.click(testAll());

    expect(toastSpy).toHaveBeenCalledWith('providers.refresh.none_enabled');
    expect(api.post).not.toHaveBeenCalled();
  });
});
