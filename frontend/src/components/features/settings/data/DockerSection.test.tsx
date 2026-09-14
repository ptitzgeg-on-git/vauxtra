/**
 * "No Docker endpoint" is a claim about the instance, made from a list nobody had read.
 *
 * `hasEndpoints` is `endpointsQuery.data ?? []` measured for length, so it is false twice
 * over: on a fresh instance, and on every cold load of this screen before
 * `GET /api/docker/endpoints` answers. The second one puts an "Add endpoint" button in front
 * of an operator who already has one -- and `useDockerDiscovery` states that very principle
 * in its own comment, about the failure half, which was the only half it guarded.
 *
 * The header carries an "Add endpoint" button of its own at all times, so the count of them
 * is what separates the two states: one button is the header, two is the header plus the
 * invitation underneath it.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { DockerEndpoint } from '@/types/api';

let endpointRows: DockerEndpoint[] = [];
/** A query that never settles, which is what the first paint of this section actually has. */
let endpointsPending = false;
/** A query that settles into `isError`, the other way a list is not an answer about anything. */
let endpointsFail = false;

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/docker/endpoints') {
        if (endpointsPending) return new Promise(() => {});
        if (endpointsFail) return Promise.reject(new Error('docker unreachable'));
        return Promise.resolve(endpointRows);
      }
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { DockerSection } = await import('./DockerSection');

const ENDPOINT: DockerEndpoint = {
  id: 3,
  name: 'nas',
  docker_host: 'tcp://10.0.0.10:2375',
  enabled: true,
  is_default: true,
  created_at: '2026-01-01T00:00:00Z',
};

const addButtons = () => screen.getAllByRole('button', { name: 'settings.docker.add_endpoint' });
const shimmers = () => document.querySelectorAll('.animate-shimmer');

describe('DockerSection, the endpoint list before it has been read', () => {
  beforeEach(() => {
    endpointRows = [ENDPOINT];
    endpointsPending = false;
    endpointsFail = false;
    vi.clearAllMocks();
  });

  it('does not say the instance has no endpoint while the list is in flight', async () => {
    endpointsPending = true;
    renderWithProviders(<DockerSection />);
    await screen.findByText('settings.docker.title');

    expect(screen.queryByText('settings.docker.no_endpoints')).toBeNull();
    // The claim and the button under it are one gesture; neither belongs here yet.
    expect(addButtons()).toHaveLength(1);
    // And not by rendering nothing either: something has to hold the place of the list.
    expect(shimmers().length).toBeGreaterThan(0);
  });

  it('still says it once the list has come back with nothing in it', async () => {
    // The claim is not withdrawn, only postponed until there is an answer behind it.
    endpointRows = [];
    renderWithProviders(<DockerSection />);

    expect(await screen.findByText('settings.docker.no_endpoints')).toBeInTheDocument();
    expect(addButtons()).toHaveLength(2);
    expect(shimmers()).toHaveLength(0);
  });

  it('says the request failed rather than that there is no endpoint', async () => {
    endpointsFail = true;
    renderWithProviders(<DockerSection />);

    expect(await screen.findByText('settings.docker.endpoints_load_failed')).toBeInTheDocument();
    expect(screen.queryByText('settings.docker.no_endpoints')).toBeNull();
    expect(addButtons()).toHaveLength(1);
  });

  it('shows the endpoint picker once there is an endpoint to pick', async () => {
    renderWithProviders(<DockerSection />);

    expect(await screen.findByText('settings.docker.endpoint_label')).toBeInTheDocument();
    expect(screen.queryByText('settings.docker.no_endpoints')).toBeNull();
    expect(shimmers()).toHaveLength(0);
  });
});
