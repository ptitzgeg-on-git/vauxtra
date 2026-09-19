/**
 * The two summary figures the celebration screen counted from reads it never checked.
 *
 * "Notification targets" and "Docker hosts" both printed `formatNumber(rows.length)` whatever
 * the read behind them did, so a request that failed -- or one still open, which it usually
 * is, since this screen mounts the instant the wizard finishes -- put a zero beside a line
 * whose whole job is to tell the operator what they just configured. `StatRow` settled the
 * rule for this repo: a dash, not a nought, for a figure nobody could ask for.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { EM_DASH } from '@/lib/format';
import type { DockerEndpoint, Webhook } from '@/types/api';

const ENDPOINT: DockerEndpoint = {
  id: 3,
  name: 'docker-lab',
  docker_host: 'tcp://10.0.0.5:2375',
  enabled: true,
  is_default: false,
  created_at: '2026-01-01T00:00:00Z',
};
const WEBHOOK: Webhook = {
  id: 5,
  name: 'ops-discord',
  url_masked: 'discord://***',
  enabled: true,
  created_at: '2026-01-01T00:00:00Z',
  scope_type: 'all',
  scope_ref_id: null,
  repeat_interval_minutes: 0,
  alert_on_any_down: false,
  alert_on_any_up: false,
  alert_on_integration_down: false,
  alert_on_integration_up: false,
  min_down_minutes: 0,
};

/** How a read answers: with its rows, with a failure, or never -- still in flight. */
type Answer = 'rows' | 'fails' | 'never';
let webhooksAnswer: Answer = 'rows';
let endpointsAnswer: Answer = 'rows';
/** Separate from the mode, so "this instance really has no target" stays testable. */
let webhookRows: Webhook[] = [WEBHOOK];

const NEVER: Promise<never> = new Promise(() => {});

function answer<T>(mode: Answer, rows: T, why: string): Promise<T> {
  if (mode === 'fails') return Promise.reject(new Error(why));
  if (mode === 'never') return NEVER;
  return Promise.resolve(rows);
}

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/webhooks') return answer(webhooksAnswer, webhookRows, 'webhooks refused');
      if (path === '/docker/endpoints') return answer(endpointsAnswer, [ENDPOINT], 'docker refused');
      // `useFormat` reads `/settings`; an empty object means "follow the browser's zone".
      return Promise.resolve({});
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { DoneStep } = await import('./DoneStep');

const show = () =>
  renderWithProviders(<DoneStep skipPassword={false} providers={[]} onFinish={vi.fn()} />);

/** The figure printed beside a summary label, whatever it happens to be. */
const valueFor = (labelKey: string) =>
  screen.getByText(labelKey).parentElement?.querySelector('dd')?.textContent;

const notice = () => screen.queryByText('setup.done.counts_unread');

beforeEach(() => {
  webhooksAnswer = 'rows';
  endpointsAnswer = 'rows';
  webhookRows = [WEBHOOK];
});

describe('DoneStep, the two figures it counted from reads it never checked', () => {
  it('prints both counts once both reads answer', async () => {
    show();
    await waitFor(() => expect(valueFor('setup.done.summary_webhooks')).toBe('1'));
    expect(valueFor('setup.done.summary_docker')).toBe('1');
    expect(notice()).toBeNull();
  });

  it('shows a dash, not a nought, while a read is still in flight', async () => {
    webhooksAnswer = 'never';
    show();
    await waitFor(() => expect(valueFor('setup.done.summary_docker')).toBe('1'));
    expect(valueFor('setup.done.summary_webhooks')).toBe(EM_DASH);
    // A request that is merely open is not worth a notice on the screen meant to feel
    // finished; the dash already says the figure is not in yet.
    expect(notice()).toBeNull();
  });

  it('shows a dash once the read has failed outright, and says why', async () => {
    webhooksAnswer = 'fails';
    show();
    // Not on the dash: it is already there while the request is open, so it would pass
    // before the failure had landed. The notice is what only a failed read draws.
    await waitFor(() => expect(notice()).not.toBeNull());
    expect(valueFor('setup.done.summary_webhooks')).toBe(EM_DASH);
    expect(screen.getByText('setup.done.counts_unread_hint')).toBeInTheDocument();
    // The read that did answer keeps its figure.
    expect(valueFor('setup.done.summary_docker')).toBe('1');
  });

  it('warns once, with one retry, when both reads fail', async () => {
    webhooksAnswer = 'fails';
    endpointsAnswer = 'fails';
    show();
    await waitFor(() => expect(notice()).not.toBeNull());
    expect(screen.getAllByText('setup.done.counts_unread')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'common.retry' })).toHaveLength(1);
    expect(valueFor('setup.done.summary_webhooks')).toBe(EM_DASH);
    expect(valueFor('setup.done.summary_docker')).toBe(EM_DASH);
  });

  it('still prints a nought once a read has come back with nothing in it', async () => {
    webhookRows = [];
    show();
    await waitFor(() => expect(valueFor('setup.done.summary_docker')).toBe('1'));
    // Zero is a measurement here, and the screen is entitled to state it.
    expect(valueFor('setup.done.summary_webhooks')).toBe('0');
    expect(notice()).toBeNull();
  });

  it('refetches only the read that failed', async () => {
    const { api } = await import('@/api/client');
    webhooksAnswer = 'fails';
    show();
    await waitFor(() => expect(notice()).not.toBeNull());
    const calls = (path: string) =>
      (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(([p]) => p === path).length;
    const before = calls('/docker/endpoints');
    await userEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    await waitFor(() => expect(calls('/webhooks')).toBeGreaterThan(1));
    expect(calls('/docker/endpoints')).toBe(before);
  });
});
