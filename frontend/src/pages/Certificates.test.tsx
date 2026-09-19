/**
 * What the certificates page says when it could not read the integrations behind its table.
 *
 * An empty table explains itself by naming the integrations it queried, and two reads stand
 * behind that sentence: `GET /providers` for the names, and `GET /providers/types` for which
 * of them expose a certificate store at all. Neither was checked before it was said.
 *
 * A failed types read left the capability list `null`, the last rung defended against that
 * with `?? []`, and `Intl.PluralRules` picks `_other` for zero -- so the page printed "These
 * integrations were queried and returned nothing: ." A failed providers read left that list
 * empty instead, which the rung above reads as "No integration exposes a certificate store":
 * a statement about what is configured, from a page that had just failed to find out.
 *
 * `t()` returns the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose --
 * so a rung is on screen iff its key is, and rewording it in eight locale files never turns
 * this red. Both rungs title themselves `certificates.empty.none`, so every assertion below
 * is on the description, which is the half that differs.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Provider } from '@/types/api';

const NPM: Provider = {
  id: 1,
  name: 'NPM at the lab',
  type: 'npm',
  url: 'http://10.0.0.1:81',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

/** Traefik manages ACME internally and exposes no store, so it is the honest empty case. */
const TRAEFIK: Provider = { ...NPM, id: 2, name: 'Traefik at the edge', type: 'traefik' };

const TYPES = {
  npm: { capabilities: { certificates: true } },
  traefik: { capabilities: { certificates: false } },
};

/** No certificate anywhere: every rung this file is about lives under an empty table. */
const NO_CERTS = {
  certificates: [],
  total: 0,
  expiring_soon_count: 0,
  warn_threshold_days: 30,
  unreachable: [],
};

type Answer = 'ok' | 'fails' | 'pending';

let providersAnswer: Answer = 'ok';
let typesAnswer: Answer = 'ok';
let providerList: Provider[] = [NPM];
/** Set when a test wants the *second* read of the capability map to answer. */
let typesRecoversOnRetry = false;
let typesCalls = 0;

function answer<T>(mode: Answer, value: T): Promise<T> {
  if (mode === 'fails') return Promise.reject(new Error('unreachable'));
  //: A read that never settles. `pending` is the third state the page used to collapse into
  //: the other two, and the only one a resolved-or-rejected promise cannot express.
  if (mode === 'pending') return new Promise<T>(() => {});
  return Promise.resolve(value);
}

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/certificates/expiry') return Promise.resolve(NO_CERTS);
      if (path === '/certificates') return Promise.resolve([]);
      if (path === '/providers') return answer(providersAnswer, providerList);
      if (path === '/providers/types') {
        typesCalls += 1;
        return answer(typesRecoversOnRetry && typesCalls > 1 ? 'ok' : typesAnswer, TYPES);
      }
      return Promise.resolve({});
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Certificates } = await import('./Certificates');

const unread = () => screen.queryByText('certificates.empty.none_unread_hint');
const named = () => screen.queryByText('certificates.empty.none_hint');
const noStore = () => screen.queryByText('certificates.empty.no_integration');

describe('Certificates, when it could not read the integrations behind the table', () => {
  beforeEach(() => {
    providersAnswer = 'ok';
    typesAnswer = 'ok';
    typesRecoversOnRetry = false;
    typesCalls = 0;
    providerList = [NPM];
    vi.clearAllMocks();
  });

  it('says it could not look, rather than naming nobody', async () => {
    typesAnswer = 'fails';
    renderWithProviders(<Certificates />);

    expect(await screen.findByText('certificates.empty.none_unread_hint')).toBeInTheDocument();
    expect(named()).toBeNull();
    expect(noStore()).toBeNull();
  });

  it('says it could not look, rather than that no integration exposes a store', async () => {
    providersAnswer = 'fails';
    renderWithProviders(<Certificates />);

    expect(await screen.findByText('certificates.empty.none_unread_hint')).toBeInTheDocument();
    expect(noStore()).toBeNull();
  });

  it('answers for neither read while one of them is still in flight', async () => {
    providersAnswer = 'pending';
    const { container } = renderWithProviders(<Certificates />);

    // The page is up -- the header paints before any of the four reads answer -- so the
    // absences below are the empty state staying away, not the screen failing to render.
    expect(await screen.findByText('nav.certificates')).toBeInTheDocument();
    await waitFor(() =>
      expect(container.querySelectorAll('.animate-shimmer').length).toBeGreaterThan(0),
    );

    expect(unread()).toBeNull();
    expect(named()).toBeNull();
    expect(noStore()).toBeNull();
  });

  it('still says no integration exposes a store once both reads came back', async () => {
    providerList = [TRAEFIK];
    renderWithProviders(<Certificates />);

    expect(await screen.findByText('certificates.empty.no_integration')).toBeInTheDocument();
    expect(unread()).toBeNull();
  });

  it('still names the integrations it queried when there were some to query', async () => {
    renderWithProviders(<Certificates />);

    expect(await screen.findByText('certificates.empty.none_hint')).toBeInTheDocument();
    expect(unread()).toBeNull();
  });

  it('re-reads the capability map on retry, not just the certificates', async () => {
    // The map is what failed, so a retry that refetches everything *but* it is a button
    // offered for a failure it has no way of clearing. The header's own refresh button is
    // named `certificates.refresh`, so the query below cannot pick it up by accident.
    typesAnswer = 'fails';
    typesRecoversOnRetry = true;
    renderWithProviders(<Certificates />);
    await screen.findByText('certificates.empty.none_unread_hint');

    await userEvent.click(screen.getByRole('button', { name: 'common.retry' }));

    expect(await screen.findByText('certificates.empty.none_hint')).toBeInTheDocument();
    expect(unread()).toBeNull();
    expect(typesCalls).toBeGreaterThan(1);
  });
});
