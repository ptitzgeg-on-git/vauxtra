/**
 * The 24 h cell used to say the same thing twice, and to say the wrong thing on a failure.
 *
 * `UptimeStrip` prints a sentence inside its dashed box when there is nothing to draw, and
 * the cell printed the same key again in a `<p>` underneath it -- the DOM read "Aucun
 * controle ces 24 dernieres heures | | Aucun controle ces 24 dernieres heures". And when
 * `GET /api/services/history` had *failed*, the box still stated "no check in the last 24
 * hours", which is a claim about the infrastructure made from a request that never came
 * back, directly above a `<p>` saying the opposite.
 */

import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { Service, ServiceHistoryResponse } from '@/types/api';
import { MonitoringTable } from './MonitoringTable';

const SERVICE: Service = {
  id: 1,
  subdomain: 'app',
  domain: 'example.test',
  target_ip: '192.168.1.10',
  target_port: 8080,
  forward_scheme: 'http',
  websocket: true,
  expose_mode: 'proxy_dns',
  public_target_mode: 'manual',
  auto_update_dns: true,
  tunnel_hostname: '',
  dns_ip: '198.51.100.4',
  npm_host_id: 12,
  dns_provider_id: 1,
  proxy_provider_id: 2,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: '2026-01-01 10:00:00',
  created_at: '2026-01-01 09:00:00',
  tags: [],
  environments: [],
};

function renderTable(overrides: Partial<Parameters<typeof MonitoringTable>[0]> = {}) {
  return renderWithProviders(
    <MonitoringTable
      services={[SERVICE]}
      history={undefined}
      probes={{}}
      checkingId={null}
      now={Date.parse('2026-01-01T12:00:00Z')}
      selectedId={null}
      onSelect={() => {}}
      onCheck={() => {}}
      empty={{ title: 'empty.title', description: 'empty.description' }}
      {...overrides}
    />,
  );
}

/** The 24 h cell of the only row, found through its column header rather than by index. */
function uptimeCell(): HTMLElement {
  const headers = screen.getAllByRole('columnheader');
  const column = headers.findIndex((cell) => cell.textContent === 'monitoring.table.uptime_24h');
  expect(column).toBeGreaterThanOrEqual(0);
  return screen.getAllByRole('cell')[column];
}

describe('MonitoringTable, the 24 h cell', () => {
  it('says "no check in the last 24 hours" once, not twice', () => {
    renderTable();

    const cell = uptimeCell();
    expect(within(cell).getAllByText('monitoring.uptime.no_history')).toHaveLength(1);
  });

  it('does not claim a quiet day when the history request failed', () => {
    renderTable({ historyError: true });

    const cell = uptimeCell();
    // The request never came back, so nothing is known about the last 24 hours.
    expect(within(cell).queryByText('monitoring.uptime.no_history')).toBeNull();
    expect(within(cell).getAllByText('monitoring.uptime.history_failed')).toHaveLength(1);
  });

  it('states the percentage once there is history to state it from', () => {
    const history: ServiceHistoryResponse = {
      '1': [
        { status: 'ok', created_at: '2026-01-01 11:00:00' },
        { status: 'ok', created_at: '2026-01-01 11:30:00' },
      ],
    };
    renderTable({ history });

    const cell = uptimeCell();
    expect(within(cell).getByText('monitoring.uptime.summary')).toBeInTheDocument();
    expect(within(cell).queryByText('monitoring.uptime.no_history')).toBeNull();
  });
});

/**
 * A service without a port is published in DNS alone: there is nothing to connect to, and
 * the row said so in three wrong ways. The address read `192.168.1.10:0`, the 24 h cell
 * waited for a check that nothing runs, and the row offered a check button whose only
 * possible answer was "down".
 */
const NO_PORT: Service = {
  ...SERVICE,
  target_port: 0,
  proxy_provider_id: null,
  npm_host_id: null,
  status: 'unknown',
  last_checked: null,
};

describe('MonitoringTable, a service without a port', () => {
  it('says it is not probed, and why, instead of waiting for a check', () => {
    renderTable({ services: [NO_PORT] });

    const cell = uptimeCell();
    expect(within(cell).getByText('monitoring.tunnel_not_probed')).toBeInTheDocument();
    // Written out for screen readers; the tooltip repeats it on hover.
    expect(within(cell).getByText(/monitoring\.no_port_not_probed_hint/)).toBeInTheDocument();
    expect(within(cell).queryByText(/monitoring\.tunnel_not_probed_hint/)).toBeNull();
    expect(within(cell).queryByText('monitoring.uptime.no_history')).toBeNull();
  });

  it('writes the address without a port, and says the service is not testable', () => {
    renderTable({ services: [NO_PORT] });

    expect(screen.getByText('192.168.1.10')).toBeInTheDocument();
    expect(screen.queryByText('192.168.1.10:0')).toBeNull();
    expect(screen.getByText('monitoring.no_port_caption')).toBeInTheDocument();
  });

  it('offers no check button on it', () => {
    renderTable({ services: [NO_PORT] });
    expect(screen.queryByRole('button', { name: 'monitoring.check_row' })).toBeNull();
  });

  it('keeps the check button on a service that has a port', () => {
    renderTable();
    expect(screen.getByRole('button', { name: 'monitoring.check_row' })).toBeInTheDocument();
  });
});
