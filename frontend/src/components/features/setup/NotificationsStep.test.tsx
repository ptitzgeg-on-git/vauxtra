/**
 * What the notifications step said about a list it had never read.
 *
 * `useWebhookActions` returns its query beside its rows, and says in a comment why. This
 * screen took only the rows, so a failed read drew no list, no message and a footer button
 * reading "Skip for now". The cost is not cosmetic: `POST /api/webhooks` carries no duplicate
 * guard, so an operator who re-adds the target they cannot see ends up with two rows, and
 * every alert from then on fires twice on the same channel.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Webhook } from '@/types/api';

const DISCORD: Webhook = {
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

/** How the read answers: with its rows, with a failure, or never -- still in flight. */
type Answer = 'rows' | 'fails' | 'never';
let answerMode: Answer = 'rows';
/** Separate from the mode, so "this instance really has no target" stays testable. */
let rows: Webhook[] = [DISCORD];

const NEVER: Promise<never> = new Promise(() => {});

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path !== '/webhooks') return Promise.resolve([]);
      if (answerMode === 'fails') return Promise.reject(new Error('webhooks refused'));
      if (answerMode === 'never') return NEVER;
      return Promise.resolve(rows);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { NotificationsStep } = await import('./NotificationsStep');

const show = () => renderWithProviders(<NotificationsStep onBack={vi.fn()} onContinue={vi.fn()} />);

const shimmers = () => document.querySelectorAll('.animate-shimmer');
const skip = () => screen.queryByRole('button', { name: 'setup.providers.skip' });
const carryOn = () => screen.getByRole('button', { name: 'setup.providers.continue' });

beforeEach(() => {
  answerMode = 'rows';
  rows = [DISCORD];
});

describe('NotificationsStep, the target list it never checked', () => {
  it('holds the place of the list while the read is still in flight', async () => {
    answerMode = 'never';
    show();
    await waitFor(() => expect(shimmers().length).toBeGreaterThan(0));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('does not offer to skip a step whose contents are still unknown', async () => {
    answerMode = 'never';
    show();
    await waitFor(() => expect(shimmers().length).toBeGreaterThan(0));
    expect(skip()).toBeNull();
    // The shell runs the primary action on Enter, so this also holds the keyboard.
    expect(carryOn()).toBeDisabled();
  });

  it('says the read failed rather than drawing an empty step', async () => {
    answerMode = 'fails';
    show();
    expect(await screen.findByText('setup.notifications.load_failed')).toBeInTheDocument();
    // The hint is the whole point: it is what stops a second copy of the same target.
    expect(screen.getByText('setup.notifications.load_failed_hint')).toBeInTheDocument();
    expect(skip()).toBeNull();
    expect(carryOn()).toBeEnabled();
    expect(shimmers()).toHaveLength(0);
  });

  it('lets the operator ask for the list again', async () => {
    const { api } = await import('@/api/client');
    answerMode = 'fails';
    show();
    await screen.findByText('setup.notifications.load_failed');
    const calls = () =>
      (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(([p]) => p === '/webhooks').length;
    const before = calls();
    await userEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    await waitFor(() => expect(calls()).toBeGreaterThan(before));
  });

  it('offers to skip once the list has come back with nothing in it', async () => {
    rows = [];
    show();
    await waitFor(() => expect(skip()).not.toBeNull());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(shimmers()).toHaveLength(0);
  });

  it('lists what came back, and offers to carry it forward', async () => {
    show();
    expect(await screen.findByText('ops-discord')).toBeInTheDocument();
    expect(screen.getByText('discord://***')).toBeInTheDocument();
    expect(skip()).toBeNull();
    expect(carryOn()).toBeEnabled();
  });
});
