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
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { DockerContainer, DockerEndpoint, Provider } from '@/types/api';

let endpointRows: DockerEndpoint[] = [];
/** A query that never settles, which is what the first paint of this section actually has. */
let endpointsPending = false;
/** A query that settles into `isError`, the other way a list is not an answer about anything. */
let endpointsFail = false;

/**
 * How the two lists an import is configured from answer: with their rows, with a failure,
 * or never -- a request still in flight. The panel could tell none of the three apart from
 * a list that came back empty.
 */
type Answer = 'rows' | 'fails' | 'never';
let providersAnswer: Answer = 'rows';
let domainsAnswer: Answer = 'rows';
/** Separate from the answer, so "the instance really has no domain" stays testable. */
let domainRows: string[] = ['example.test'];

/** A request that never comes back, so `isPending` stays true for the whole test. */
const NEVER: Promise<never> = new Promise(() => {});

function answer<T>(mode: Answer, rows: T, why: string): Promise<T> {
  if (mode === 'fails') return Promise.reject(new Error(why));
  if (mode === 'never') return NEVER;
  return Promise.resolve(rows);
}

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/docker/endpoints') {
        if (endpointsPending) return new Promise(() => {});
        if (endpointsFail) return Promise.reject(new Error('docker unreachable'));
        return Promise.resolve(endpointRows);
      }
      if (path === '/providers') {
        return answer(providersAnswer, [PROXY_PROVIDER, DNS_PROVIDER], 'providers refused');
      }
      if (path === '/domains') return answer(domainsAnswer, domainRows, 'domains refused');
      if (path.startsWith('/docker/containers')) return Promise.resolve([CONTAINER]);
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

const PROXY_PROVIDER: Provider = {
  id: 4,
  name: 'npm-home',
  type: 'npm',
  url: 'https://proxy.example.test',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

const DNS_PROVIDER: Provider = {
  id: 5,
  name: 'cf-home',
  type: 'cloudflare',
  url: 'https://api.example.test',
  username: '',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

/** One container the scan finds, with a port, so the import button has something to write. */
const CONTAINER: DockerContainer = {
  id: 'c1',
  name: 'grafana',
  image: 'grafana/grafana',
  status: 'running',
  target_ip: '10.0.0.10',
  target_port: 3000,
  labels: {},
  suggested_subdomain: 'grafana',
  suggested_scheme: 'http',
  websocket: false,
  suggestion: {
    subdomain: 'grafana',
    target_port: 3000,
    forward_scheme: 'http',
    websocket: false,
    confidence: 'high',
    source: 'port_heuristic',
    middlewares: [],
    tls_resolver: null,
  },
  endpoint_id: 3,
  endpoint_name: 'nas',
  existing_service: null,
};

const addButtons = () => screen.getAllByRole('button', { name: 'settings.docker.add_endpoint' });
const shimmers = () => document.querySelectorAll('.animate-shimmer');

describe('DockerSection, the endpoint list before it has been read', () => {
  beforeEach(() => {
    endpointRows = [ENDPOINT];
    endpointsPending = false;
    endpointsFail = false;
    providersAnswer = 'rows';
    domainsAnswer = 'rows';
    domainRows = ['example.test'];
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

/**
 * The two lists the import writes from, as opposed to the endpoint list, which only says
 * where to look.
 *
 * `/providers` fills both integration selects, and both were read as `data = []`. An empty
 * list narrows each of them to a single "None" option, which reads as a choice rather than
 * as the absence of one -- and the import then goes through, writing one route per selected
 * container with no integration attached. `/domains` is the same read on the other side: an
 * empty list is the sentence "this instance has no domain", and it is also what empties
 * `effectiveDomain`, which is what the import button reads to disable itself.
 *
 * `t()` gives back the key here, so these assertions survive any rewording of the eight
 * locale files.
 */
describe('DockerSection, the two lists an import is configured from', () => {
  beforeEach(() => {
    endpointRows = [ENDPOINT];
    endpointsPending = false;
    endpointsFail = false;
    providersAnswer = 'rows';
    domainsAnswer = 'rows';
    domainRows = ['example.test'];
    vi.clearAllMocks();
  });

  const show = () => renderWithProviders(<DockerSection />);
  /** Waited for, so no assertion races the reads this panel opens. */
  const ready = () => screen.findByText('settings.docker.endpoint_label');
  const importAlert = () => screen.queryByText('settings.docker.import_lists_failed');
  const retryImport = () => screen.getByRole('button', { name: 'common.retry' });
  const providerHints = () => screen.queryAllByText('settings.docker.provider_list_unread');
  const option = (name: string) => screen.queryByRole('option', { name });

  it('names both integrations and offers the real domain when both lists answer', async () => {
    // The control the rest is measured against: every choice the import needs is a real
    // one, and nothing on the panel says otherwise.
    show();
    await ready();
    expect(await screen.findByRole('option', { name: 'npm-home' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'cf-home' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'example.test' })).toBeInTheDocument();
    expect(importAlert()).toBeNull();
    expect(providerHints()).toHaveLength(0);
    expect(option('settings.docker.domain_unread')).toBeNull();
    expect(option('settings.docker.domain_none')).toBeNull();
  });

  it('says the integration list could not be read rather than narrowing it in silence', async () => {
    providersAnswer = 'fails';
    show();
    await ready();

    expect(await screen.findByText('settings.docker.import_lists_failed')).toBeInTheDocument();
    // The reason the read gave, not this panel's own wording for it.
    expect(screen.getByText('providers refused')).toBeInTheDocument();
    // Both selects, because the import writes through both.
    expect(providerHints()).toHaveLength(2);
  });

  it('says the same about the domain list', async () => {
    domainsAnswer = 'fails';
    show();
    await ready();

    expect(await screen.findByText('settings.docker.import_lists_failed')).toBeInTheDocument();
    expect(screen.getByText('domains refused')).toBeInTheDocument();
  });

  it('warns once, with one retry, when both reads fail', async () => {
    providersAnswer = 'fails';
    domainsAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('settings.docker.import_lists_failed');

    expect(screen.getAllByText('settings.docker.import_lists_failed')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'common.retry' })).toHaveLength(1);
  });

  it('offers the domain as unread rather than as absent', async () => {
    // "No domain configured" is a claim about the instance. Only a list that came back
    // can make it, and this one did not.
    domainsAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('settings.docker.import_lists_failed');

    expect(option('settings.docker.domain_unread')).toBeInTheDocument();
    expect(option('settings.docker.domain_none')).toBeNull();
  });

  it('still says the instance has no domain once the list has come back with none', async () => {
    // The claim is not withdrawn, only postponed until there is an answer behind it.
    domainRows = [];
    show();
    await ready();

    expect(await screen.findByRole('option', { name: 'settings.docker.domain_none' })).toBeInTheDocument();
    expect(option('settings.docker.domain_unread')).toBeNull();
    expect(importAlert()).toBeNull();
  });

  it('marks both integration choices incomplete while that list is still in flight', async () => {
    // Pending counts as unread: both reads start with the endpoint read, and this form is
    // drawn the moment that one answers, which is not the moment these two do.
    providersAnswer = 'never';
    show();
    await ready();

    await waitFor(() => expect(providerHints()).toHaveLength(2));
    // And no warning: a read still in flight has not failed.
    expect(importAlert()).toBeNull();
  });

  it('refetches only the read that failed', async () => {
    const { api } = await import('@/api/client');
    providersAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('settings.docker.import_lists_failed');

    vi.mocked(api.get).mockClear();
    providersAnswer = 'rows';
    await userEvent.click(retryImport());
    await waitFor(() => expect(importAlert()).toBeNull());

    const paths = vi.mocked(api.get).mock.calls.map(([path]) => path);
    expect(paths).toContain('/providers');
    expect(paths).not.toContain('/domains');
    expect(await screen.findByRole('option', { name: 'npm-home' })).toBeInTheDocument();
  });

  it('leaves the retry usable while a sibling read is still in flight', async () => {
    // The retry is the only way back from the warning, and it sits on a button whose busy
    // state also disables it. Read across both, a sibling that never answers would hold
    // that button shut for good. Only the read that failed may claim it.
    providersAnswer = 'fails';
    domainsAnswer = 'never';
    show();
    await ready();
    await screen.findByText('settings.docker.import_lists_failed');
    expect(retryImport()).toBeEnabled();

    providersAnswer = 'rows';
    await userEvent.click(retryImport());
    await waitFor(() => expect(importAlert()).toBeNull());
    expect(await screen.findByRole('option', { name: 'npm-home' })).toBeInTheDocument();
  });

  it('makes the operator confirm a write that will attach no integration', async () => {
    // The point of no return. "None" in both selects is what the import writes, and with
    // the list unread it is not a choice anybody made -- so the last dialog before the
    // write says so, and opens on Cancel the way every destructive one does.
    providersAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('settings.docker.import_lists_failed');

    await userEvent.click(screen.getByRole('button', { name: 'settings.docker.discover' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'settings.docker.import_selected' }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('settings.docker.import_without_provider');
    await waitFor(() =>
      expect(within(dialog).getByRole('button', { name: 'common.cancel' })).toHaveFocus(),
    );
  });

  it('asks the ordinary way once the integration list has been read', async () => {
    show();
    await ready();
    await screen.findByRole('option', { name: 'npm-home' });

    await userEvent.click(screen.getByRole('button', { name: 'settings.docker.discover' }));
    await userEvent.click(
      await screen.findByRole('button', { name: 'settings.docker.import_selected' }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('settings.docker.import_message');
    expect(dialog).not.toHaveTextContent('settings.docker.import_without_provider');
    // `info` opens on Confirm, `danger` on Cancel: nothing is being warned about here.
    await waitFor(() =>
      expect(within(dialog).getByRole('button', { name: 'settings.migration.import' })).toHaveFocus(),
    );
  });
});
