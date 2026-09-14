/**
 * Two tabs of this panel stated a fact about the service from a request still in flight.
 *
 * `history` and `logs` arrive as plain arrays, and `Monitoring.tsx` builds both from
 * `data ?? []`. Empty therefore means either "nothing happened" or "nobody has been told
 * yet", and the panel printed the first reading of both: "No timeline data yet for this
 * host" and "No recent logs linked to this hostname". Opening the drawer from a deep link
 * on a cold page is exactly the state where both are wrong.
 *
 * The panel already distinguished a *failed* request from an empty one; this is the same
 * distinction for the half of the ladder that was missing. Both are props, so the states are
 * rendered directly rather than mocked.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { describe, expect, it } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { LogEntry, Service, ServiceHistoryPoint } from '@/types/api';
import { ServiceDrawer } from './ServiceDrawer';

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

const POINT: ServiceHistoryPoint = { status: 'ok', created_at: '2026-01-01 11:00:00' };
const LOG: LogEntry = {
  id: 1,
  level: 'info',
  message: 'git.example.test checked',
  created_at: '2026-01-01 11:00:00',
};

function renderDrawer(overrides: Partial<Parameters<typeof ServiceDrawer>[0]> = {}) {
  return renderWithProviders(
    <ServiceDrawer
      open
      onClose={() => {}}
      service={SERVICE}
      history={[]}
      logs={[]}
      probe={undefined}
      checking={false}
      onCheck={() => {}}
      now={Date.parse('2026-01-01T12:00:00Z')}
      {...overrides}
    />,
  );
}

const shimmers = () => document.querySelectorAll('.animate-shimmer');

/** The third tab, reached the way an operator reaches it. */
function openLogs() {
  fireEvent.click(screen.getByRole('tab', { name: /monitoring\.related_logs_title/ }));
}

describe('ServiceDrawer, the timeline before the history has been read', () => {
  it('does not say there is no timeline data while the request is in flight', () => {
    renderDrawer({ historyLoading: true });

    expect(screen.queryByText('monitoring.timeline_empty')).toBeNull();
    // And not by rendering nothing either: something has to hold the place of the timeline.
    expect(shimmers().length).toBeGreaterThan(0);
  });

  it('still says it once the history has come back with nothing in it', () => {
    // The claim is not withdrawn, only postponed until there is an answer behind it.
    renderDrawer();

    expect(screen.getByText('monitoring.timeline_empty')).toBeInTheDocument();
    expect(shimmers()).toHaveLength(0);
  });

  it('says the request failed rather than that the host has no history', () => {
    renderDrawer({ historyError: true });

    expect(screen.getByText('monitoring.history.load_failed')).toBeInTheDocument();
    expect(screen.queryByText('monitoring.timeline_empty')).toBeNull();
  });

  it('draws the timeline once there is history to draw', () => {
    renderDrawer({ history: [POINT] });

    expect(screen.queryByText('monitoring.timeline_empty')).toBeNull();
    expect(shimmers()).toHaveLength(0);
  });
});

describe('ServiceDrawer, the logs before they have been read', () => {
  it('does not say no log mentions the host while the request is in flight', () => {
    renderDrawer({ logsLoading: true });
    openLogs();

    expect(screen.queryByText('monitoring.related_logs_empty')).toBeNull();
    expect(shimmers().length).toBeGreaterThan(0);
  });

  it('still says it once the logs have come back with nothing in them', () => {
    renderDrawer();
    openLogs();

    expect(screen.getByText('monitoring.related_logs_empty')).toBeInTheDocument();
    expect(shimmers()).toHaveLength(0);
  });

  it('says the request failed rather than that no log mentions the host', () => {
    renderDrawer({ logsError: true });
    openLogs();

    expect(screen.getByText('monitoring.logs.load_failed')).toBeInTheDocument();
    expect(screen.queryByText('monitoring.related_logs_empty')).toBeNull();
  });

  it('lists the lines once there are lines to list', () => {
    renderDrawer({ logs: [LOG] });
    openLogs();

    expect(screen.getByText('git.example.test checked')).toBeInTheDocument();
    expect(screen.queryByText('monitoring.related_logs_empty')).toBeNull();
  });
});
