/**
 * The drawer's two unread lists, reached the way an operator reaches them.
 *
 * `ServiceDrawer.test.tsx` renders the panel with the props set by hand; this file checks
 * that the page actually sets them. `/monitoring?service=7` opens the drawer as soon as
 * `GET /api/services` answers, which on a cold page is well before `/api/services/history`
 * and `/api/logs` do -- so the panel opened onto "No timeline data yet for this host" and
 * "No recent logs linked to this hostname", two statements about a service nobody had
 * finished asking about.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import { api } from '@/api/client';
import { renderWithProviders } from '@/test/render';
import type { CheckAllResult, Service } from '@/types/api';

/** Both lists in flight, which is what the first paint of a deep link actually has. */
let historyPending = false;
let logsPending = false;

/** What `POST /api/services/check-all` answers, set by each test that presses the button. */
let checkAllAnswer: CheckAllResult = { checked: 0, ok: 0, error: 0 };

/** `GET /api/settings`: the scheduler runs every 5 min unless a test switches it off. */
let settingsAnswer: Record<string, string> = { check_interval: '5' };

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/services') return Promise.resolve([SERVICE]);
      if (path === '/services/history') {
        if (historyPending) return new Promise(() => {});
        return Promise.resolve({});
      }
      if (path.startsWith('/logs')) {
        if (logsPending) return new Promise(() => {});
        return Promise.resolve({ items: [], total: 0, page: 1, per_page: 200, pages: 1 });
      }
      if (path === '/providers/tunnels/health') {
        return Promise.resolve({ total: 0, healthy: 0, down: 0, items: [] });
      }
      if (path === '/providers/tunnels/access') return Promise.resolve({ total: 0, items: [] });
      if (path === '/settings') return Promise.resolve(settingsAnswer);
      return Promise.resolve({});
    }),
    post: vi.fn((path: string) => {
      if (path === '/services/check-all') return Promise.resolve(checkAllAnswer);
      if (path === '/settings') return Promise.resolve({ ok: true, saved: ['check_interval'], ignored: [], not_applied: [] });
      return Promise.resolve({ ok: true });
    }),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const SERVICE: Service = {
  id: 7,
  subdomain: 'git',
  domain: 'example.test',
  target_ip: '10.0.0.10',
  target_port: 3000,
  forward_scheme: 'http',
  websocket: false,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: false,
  tunnel_hostname: '',
  dns_ip: '',
  npm_host_id: null,
  dns_provider_id: null,
  proxy_provider_id: null,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: '2026-01-01 11:00:00',
  created_at: '2026-01-01 09:00:00',
  tags: [],
  environments: [],
};

const { Monitoring } = await import('./Monitoring');

/** The panel, awaited: it appears only once `/api/services` has named the service. */
const drawer = () => screen.findByRole('dialog');

function openLogs(panel: HTMLElement) {
  fireEvent.click(within(panel).getByRole('tab', { name: /monitoring\.related_logs_title/ }));
}

describe('Monitoring, a deep link opened before the page has read anything', () => {
  beforeEach(() => {
    historyPending = false;
    logsPending = false;
    // `initialData: readServicesCache` reads this, so a previous test must not seed it.
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it('opens the panel without claiming the host has no timeline or logs', async () => {
    historyPending = true;
    logsPending = true;
    renderWithProviders(<Monitoring />, { route: '/monitoring?service=7' });
    const panel = await drawer();

    expect(within(panel).queryByText('monitoring.timeline_empty')).toBeNull();
    expect(panel.querySelectorAll('.animate-shimmer').length).toBeGreaterThan(0);

    openLogs(panel);
    expect(within(panel).queryByText('monitoring.related_logs_empty')).toBeNull();
    expect(panel.querySelectorAll('.animate-shimmer').length).toBeGreaterThan(0);
  });

  it('says both once both requests have come back with nothing', async () => {
    // The claims are not withdrawn, only postponed until there are answers behind them.
    renderWithProviders(<Monitoring />, { route: '/monitoring?service=7' });
    const panel = await drawer();

    expect(await within(panel).findByText('monitoring.timeline_empty')).toBeInTheDocument();

    openLogs(panel);
    expect(await within(panel).findByText('monitoring.related_logs_empty')).toBeInTheDocument();
    expect(panel.querySelectorAll('.animate-shimmer')).toHaveLength(0);
  });
});

/**
 * What the summary of "Check all services" says was left out.
 *
 * The tunnel routes used to be skipped, and the summary said so. They are probed through the
 * Cloudflare edge now, so the only services a check leaves out are the ones without a port,
 * which have nothing to connect to.
 */
describe('Monitoring, the summary of a check of every service', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  async function checkAll(answer: CheckAllResult) {
    checkAllAnswer = answer;
    renderWithProviders(<Monitoring />, { route: '/monitoring' });
    fireEvent.click(await screen.findByRole('button', { name: 'monitoring.check_all' }));
    return screen.findByText('monitoring.check_summary');
  }

  it('says how many services without a port were left out', async () => {
    await checkAll({ checked: 3, ok: 1, error: 0, skipped: 2, skipped_tunnel: 0, skipped_no_port: 2, results: [] });

    expect(screen.getByText('monitoring.check_skipped_no_port')).toBeInTheDocument();
  });

  it('says nothing was left out when every service was probed, tunnels included', async () => {
    await checkAll({ checked: 2, ok: 2, error: 0, skipped: 0, skipped_tunnel: 0, skipped_no_port: 0, results: [] });

    expect(screen.queryByText('monitoring.check_skipped_no_port')).toBeNull();
  });

  it('does not invent a skipped count from an older answer that gives none', async () => {
    // `checked - ok - error` once stood for "tunnels skipped". It stands for nothing now.
    await checkAll({ checked: 3, ok: 1, error: 1 });

    expect(screen.queryByText('monitoring.check_skipped_no_port')).toBeNull();
  });
});

/**
 * Production ran with `check_interval` at 0: no check had run for weeks, and the page only
 * said so in a small grey line of its header. It now says it where it is read, with the one
 * button that fixes it.
 */
describe('Monitoring, when the automatic checks are off', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  it('says so, and switches them back on every 5 minutes', async () => {
    settingsAnswer = { check_interval: '0' };
    try {
      renderWithProviders(<Monitoring />, { route: '/monitoring' });

      expect(await screen.findByText('monitoring.checks_off.title')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'monitoring.checks_off.enable' }));

      await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/settings', { check_interval: '5' }));
    } finally {
      settingsAnswer = { check_interval: '5' };
    }
  });

  it('stays quiet while the scheduler runs', async () => {
    renderWithProviders(<Monitoring />, { route: '/monitoring' });

    expect(await screen.findByText('monitoring.auto_checks_every')).toBeInTheDocument();
    expect(screen.queryByText('monitoring.checks_off.title')).toBeNull();
  });
});
