/**
 * The live log stream, and the single reconnect it is allowed.
 *
 * `GET /api/logs/stream` re-reads the credential on every tick, so a stream now ends by
 * itself: changing the password ends every stream opened with the cookie it replaced, and
 * revoking a key ends the one that key opened. The browser that made the change holds the
 * new cookie already, so a view left on `Live` has to come back on its own -- and the
 * browser holding the cookie that was just replaced must not spend the afternoon knocking.
 * One reconnect per switch-on satisfies both, and both halves carry weight: without the
 * retry the operator who did the right thing silently drops to 5 s polling, and without the
 * cap a server that accepts and drops in a loop is reconnected to in a loop.
 *
 * jsdom has no `EventSource`, so the class below is the component's whole view of the
 * network: it records every construction and lets a test end a stream by hand, which is
 * what the server now does. `t()` returns the key here -- `renderWithProviders` leaves out
 * `I18nProvider` on purpose -- so the fallback banner is on screen iff its key is.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { LogsResponse } from '@/types/api';

const EMPTY: LogsResponse = { total: 0, page: 1, per_page: 50, pages: 1, items: [] };

vi.mock('@/api/client', () => ({
  API_BASE_URL: '/api',
  api: {
    get: vi.fn((path: string) => Promise.resolve(path.startsWith('/logs') ? EMPTY : {})),
    post: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { LogsTab } = await import('./LogsTab');

/** Every stream the component has opened, in order, live or closed. */
const opened: FakeEventSource[] = [];

/** Enough of `EventSource` for the component: a URL, the credentials flag, and a close. */
class FakeEventSource {
  url: string;
  withCredentials: boolean;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = init?.withCredentials === true;
    opened.push(this);
  }

  close() {
    this.closed = true;
  }
}

const liveSwitch = () => screen.findByRole('switch', { name: 'settings.logs.live' });
const fallback = () => screen.queryByText('settings.logs.live_fallback_title');

/**
 * What the server now does once the credential behind a stream is ended: it drops it.
 *
 * By position rather than by reference, so a script that asks for a stream the component
 * never opened says exactly that, instead of failing on a property of `undefined`.
 */
async function endStream(index: number): Promise<void> {
  const source = opened[index];
  if (!source) throw new Error(`no stream #${index}: the component opened ${opened.length}`);
  await act(async () => {
    source.onerror?.();
  });
}

describe('LogsTab, the live stream and the one reconnect it is allowed', () => {
  beforeEach(() => {
    opened.length = 0;
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens one credentialed stream when Live is switched on', async () => {
    renderWithProviders(<LogsTab />);
    await userEvent.click(await liveSwitch());

    expect(opened).toHaveLength(1);
    // The API base, not a hardcoded '/api': a build served behind VITE_API_URL has its API
    // somewhere else, and `EventSource` cannot go through the axios instance that knows where.
    expect(opened[0].url).toBe('/api/logs/stream');
    // Without this the cookie never leaves the browser and the first tick refuses the stream.
    expect(opened[0].withCredentials).toBe(true);
    expect(fallback()).toBeNull();
  });

  it('shows a line the stream pushes', async () => {
    renderWithProviders(<LogsTab />);
    await userEvent.click(await liveSwitch());

    await act(async () => {
      opened[0].onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            id: 7,
            level: 'warning',
            message: 'Sign-in refused: wrong password',
            created_at: '2026-01-01T00:00:00Z',
          }),
        }),
      );
    });

    expect(await screen.findByText('Sign-in refused: wrong password')).toBeInTheDocument();
  });

  it('reconnects once when the server ends the stream, and says nothing about it', async () => {
    renderWithProviders(<LogsTab />);
    await userEvent.click(await liveSwitch());
    await endStream(0);

    expect(opened[0].closed).toBe(true);
    expect(opened).toHaveLength(2);
    expect(opened[1].url).toBe('/api/logs/stream');
    // The operator who just changed the password is back on live with the cookie the change
    // handed them. There is nothing to tell them, so no banner appears.
    expect(fallback()).toBeNull();
  });

  it('falls back to polling when the reconnect is refused in its turn', async () => {
    renderWithProviders(<LogsTab />);
    await userEvent.click(await liveSwitch());
    await endStream(0);
    await endStream(1);

    // Whoever holds this credential is not getting a stream. The page says so and stops
    // knocking; the 5 s poll it falls back to answers 401 and puts the login screen up.
    expect(opened).toHaveLength(2);
    expect(opened[1].closed).toBe(true);
    expect(fallback()).not.toBeNull();
  });

  it('closes the stream when Live is switched off', async () => {
    renderWithProviders(<LogsTab />);
    const toggle = await liveSwitch();

    await userEvent.click(toggle);
    expect(opened[0].closed).toBe(false);

    await userEvent.click(toggle);
    expect(opened[0].closed).toBe(true);
    expect(opened).toHaveLength(1);
  });

  it('budgets the reconnect per switch-on, not per component', async () => {
    renderWithProviders(<LogsTab />);
    const toggle = await liveSwitch();

    await userEvent.click(toggle);
    await endStream(0);
    await endStream(1);
    expect(fallback()).not.toBeNull();

    await userEvent.click(toggle);
    await userEvent.click(toggle);

    // Switching Live on is a request for a stream, not a resumption of the one that failed:
    // the operator asking again after signing back in gets the same single retry.
    expect(opened).toHaveLength(3);
    expect(fallback()).toBeNull();
    await endStream(2);
    expect(opened).toHaveLength(4);
    expect(fallback()).toBeNull();
  });
});
