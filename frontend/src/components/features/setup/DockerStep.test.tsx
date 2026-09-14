/**
 * What the Docker step said about a list it had never read.
 *
 * `useDockerEndpoints` handed this screen its rows and nothing else, so a read that failed and
 * an instance with no engine registered arrived as the same empty array: no list, no message,
 * and a footer button reading "Skip for now" -- a claim about contents nobody had seen. The
 * shell binds Enter to that button, so the fastest way through the wizard confirmed it.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { DockerEndpoint } from '@/types/api';

const LOCAL: DockerEndpoint = {
  id: 3,
  name: 'docker-lab',
  docker_host: 'tcp://10.0.0.5:2375',
  enabled: true,
  is_default: false,
  created_at: '2026-01-01T00:00:00Z',
};

/** How the read answers: with its rows, with a failure, or never -- still in flight. */
type Answer = 'rows' | 'fails' | 'never';
let answerMode: Answer = 'rows';
/** Separate from the mode, so "this instance really has no engine" stays testable. */
let rows: DockerEndpoint[] = [LOCAL];

const NEVER: Promise<never> = new Promise(() => {});

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path !== '/docker/endpoints') return Promise.resolve([]);
      if (answerMode === 'fails') return Promise.reject(new Error('docker endpoints refused'));
      if (answerMode === 'never') return NEVER;
      return Promise.resolve(rows);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { DockerStep } = await import('./DockerStep');

const show = () => renderWithProviders(<DockerStep onBack={vi.fn()} onContinue={vi.fn()} />);

const shimmers = () => document.querySelectorAll('.animate-shimmer');
const skip = () => screen.queryByRole('button', { name: 'setup.providers.skip' });
const carryOn = () => screen.getByRole('button', { name: 'setup.providers.continue' });

beforeEach(() => {
  answerMode = 'rows';
  rows = [LOCAL];
});

describe('DockerStep, the engine list it never checked', () => {
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
    expect(await screen.findByText('setup.docker.load_failed')).toBeInTheDocument();
    expect(screen.getByText('setup.docker.load_failed_hint')).toBeInTheDocument();
    expect(skip()).toBeNull();
    // A failed read is not a reason to trap them on an optional step.
    expect(carryOn()).toBeEnabled();
    expect(shimmers()).toHaveLength(0);
  });

  it('lets the operator ask for the list again', async () => {
    const { api } = await import('@/api/client');
    answerMode = 'fails';
    show();
    await screen.findByText('setup.docker.load_failed');
    const calls = () =>
      (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(([p]) => p === '/docker/endpoints').length;
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
    expect(await screen.findByText('docker-lab')).toBeInTheDocument();
    expect(screen.getByText('tcp://10.0.0.5:2375')).toBeInTheDocument();
    expect(skip()).toBeNull();
    expect(carryOn()).toBeEnabled();
  });
});
