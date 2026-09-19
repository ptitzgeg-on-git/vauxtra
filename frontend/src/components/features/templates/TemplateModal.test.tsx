/**
 * What the template form said about integrations it had never read.
 *
 * Two of its four reference reads carried no failure state at all: `/providers`, which fills
 * the tunnel, proxy and DNS selects, and `/domains`, which fills the domain menu. Both were
 * destructured as `data = []` while the two taxonomy reads beside them each kept an
 * `isError`. So a request that failed and a request still in flight both arrived as an empty
 * list, and an empty list here does not stay quiet: `renderProviderField` replaces the select
 * with a notice stating that no reverse proxy -- or no DNS provider, or no tunnel provider --
 * is configured yet, and offers a link to the Providers page to go and create one. That is a
 * claim about the instance, and it was being made from a read nobody had checked, inviting
 * the operator to create integrations that were already there and closing the half-filled
 * form on the way out.
 *
 * `renderWithProviders` leaves out `I18nProvider` on purpose, so the assertions here are the
 * locale keys rather than the English behind them.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Provider } from '@/types/api';

const PROXY: Provider = {
  id: 7,
  name: 'npm-home',
  type: 'npm',
  url: 'https://proxy.example.test',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

const DNS: Provider = { ...PROXY, id: 8, name: 'cf-home', type: 'cloudflare' };

/** How a read answers: with its rows, with a failure, or never -- still in flight. */
type Answer = 'rows' | 'fails' | 'never';
let providersAnswer: Answer = 'rows';
let domainsAnswer: Answer = 'rows';
/** Separate from the answer, so "this instance really has no proxy" stays testable. */
let providerRows: Provider[] = [PROXY, DNS];

const NEVER: Promise<never> = new Promise(() => {});

function answer<T>(mode: Answer, rows: T, why: string): Promise<T> {
  if (mode === 'fails') return Promise.reject(new Error(why));
  if (mode === 'never') return NEVER;
  return Promise.resolve(rows);
}

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/providers') return answer(providersAnswer, providerRows, 'providers refused');
      if (path === '/domains') return answer(domainsAnswer, ['example.test'], 'domains refused');
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { TemplateModal } = await import('./TemplateModal');

const show = () => renderWithProviders(<TemplateModal open onClose={vi.fn()} template={null} />);

const ready = () => screen.findByText('templates.form.section_exposure');
const alert = () => screen.queryByText('templates.form.lists_unread');
const invitations = () => screen.queryAllByText('templates.form.add_provider');
const hints = () => screen.queryAllByText('templates.form.provider_list_unread');

beforeEach(() => {
  providersAnswer = 'rows';
  domainsAnswer = 'rows';
  providerRows = [PROXY, DNS];
});

describe('TemplateModal, the two lists its choices are drawn from', () => {
  it('names both integrations and offers the domain when both reads answer', async () => {
    show();
    await ready();
    // The only test here that waits for data to land rather than for a notice to appear.
    // The poll is a text query on purpose: `getByRole` with a name recomputes an accessible
    // name for every option in the form, worth paying once and not on every tick.
    await screen.findAllByText('npm-home');
    expect(screen.getByRole('option', { name: 'npm-home' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'cf-home' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'example.test' })).toBeInTheDocument();
    expect(alert()).toBeNull();
    expect(invitations()).toHaveLength(0);
    expect(hints()).toHaveLength(0);
  });

  it('says the integration list could not be read rather than inviting a second npm-home', async () => {
    providersAnswer = 'fails';
    show();
    await ready();
    await waitFor(() => expect(alert()).not.toBeNull());
    expect(invitations()).toHaveLength(0);
    expect(screen.queryByText('templates.form.no_proxy_providers')).toBeNull();
    expect(screen.queryByText('templates.form.no_dns_providers')).toBeNull();
  });

  it('says the same about the domain list', async () => {
    domainsAnswer = 'fails';
    show();
    await ready();
    await waitFor(() => expect(alert()).not.toBeNull());
  });

  it('warns once, with one retry, when both reads fail', async () => {
    providersAnswer = 'fails';
    domainsAnswer = 'fails';
    show();
    await ready();
    await waitFor(() => expect(alert()).not.toBeNull());
    expect(screen.getAllByText('templates.form.lists_unread')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'common.retry' })).toHaveLength(1);
  });

  it('still says the instance has no proxy once the list has come back with none', async () => {
    providerRows = [];
    show();
    await ready();
    // The invitation is a measurement, and this list did come back: three fields, three of
    // them empty, three invitations. Tunnel is behind the mode select, so proxy and DNS.
    await waitFor(() => expect(invitations()).toHaveLength(2));
    expect(screen.queryByText('templates.form.no_proxy_providers')).not.toBeNull();
    expect(alert()).toBeNull();
  });

  it('marks both integration choices incomplete while that list is still in flight', async () => {
    providersAnswer = 'never';
    show();
    await ready();
    await waitFor(() => expect(hints()).toHaveLength(2));
    // Pending is unread too: nothing has come back, so nothing may be claimed from it.
    expect(invitations()).toHaveLength(0);
    expect(alert()).toBeNull();
  });

  it('carries the same hint once the read has failed outright', async () => {
    providersAnswer = 'fails';
    show();
    await ready();
    await waitFor(() => expect(hints()).toHaveLength(2));
  });

  it('refetches only the read that failed', async () => {
    const { api } = await import('@/api/client');
    domainsAnswer = 'fails';
    show();
    await ready();
    await waitFor(() => expect(alert()).not.toBeNull());
    const before = (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([path]) => path === '/providers',
    ).length;
    await userEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    await waitFor(() =>
      expect(
        (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(([path]) => path === '/domains').length,
      ).toBeGreaterThan(1),
    );
    expect(
      (api.get as ReturnType<typeof vi.fn>).mock.calls.filter(([path]) => path === '/providers').length,
    ).toBe(before);
  });
});
