/**
 * What the templates page says while the three lists beside `/templates` have not answered.
 *
 * A template stores ids: a proxy or DNS or tunnel integration, a list of tags, a list of
 * environments. None of those ids is a name, so every name on this page comes out of
 * `/providers`, `/tags` and `/environments`, and all three were read as `data = []`. An
 * empty map then answered every lookup with a miss, and a miss had exactly one meaning
 * written for it: the row was deleted. Measured with `/providers` failing -- every card
 * naming an integration printed "Provider removed" in warning colour about integrations
 * nobody had touched, and the filter row was gone with nothing said in its place.
 *
 * `t()` returns the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose
 * -- so `templates.card.provider_unread` is the assertion, not the sentence behind it.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Environment, Provider, Tag, Template } from '@/types/api';

const TAG: Tag = { id: 1, name: 'edge', color: '#3b82f6' };
const ENVIRONMENT: Environment = { id: 1, name: 'prod', color: '#22c55e' };
const PROVIDER: Provider = {
  id: 7,
  name: 'npm-home',
  type: 'npm',
  url: 'https://proxy.example.test',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

const TEMPLATE: Template = {
  id: 1,
  name: 'standard',
  description: '',
  forward_scheme: 'http',
  target_port: null,
  websocket: false,
  expose_mode: 'proxy_dns',
  proxy_provider_id: 7,
  dns_provider_id: null,
  tunnel_provider_id: null,
  public_target_mode: 'manual',
  domain: 'example.test',
  dns_ip: '',
  tag_ids: [1],
  environment_ids: [1],
  icon_url: '',
  created_at: '2026-01-01T00:00:00Z',
};

/**
 * How each of the three reads answers: with its rows, with a failure, or never -- a request
 * still in flight. The page could tell none of the three apart from an empty list.
 */
type Answer = 'rows' | 'fails' | 'never';
let providersAnswer: Answer = 'rows';
let tagsAnswer: Answer = 'rows';
let environmentsAnswer: Answer = 'rows';

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
      if (path === '/templates') return Promise.resolve([TEMPLATE]);
      if (path === '/providers') return answer(providersAnswer, [PROVIDER], 'providers refused');
      if (path === '/tags') return answer(tagsAnswer, [TAG], 'tags refused');
      if (path === '/environments') return answer(environmentsAnswer, [ENVIRONMENT], 'envs refused');
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Templates } = await import('./Templates');

const show = () => renderWithProviders(<Templates />, { route: '/templates' });

/** Waited for, so no assertion races the reads this page opens. */
const ready = () => screen.findByRole('heading', { name: 'standard' });

const tagFacets = () => screen.queryByRole('group', { name: 'templates.filter_tags' });
const environmentFacets = () => screen.queryByRole('group', { name: 'templates.filter_environments' });
const alert = () => screen.queryByText('templates.context_failed');
const retry = () => screen.getByRole('button', { name: 'templates.retry' });

describe('Templates, the three lists a card is named from', () => {
  beforeEach(() => {
    providersAnswer = 'rows';
    tagsAnswer = 'rows';
    environmentsAnswer = 'rows';
    vi.clearAllMocks();
  });

  it('names everything and says nothing when all three answer', async () => {
    // The control the rest is measured against: one card, its integration named, both
    // halves of the filter row drawn, and no warning anywhere.
    show();
    await ready();
    await waitFor(() => expect(screen.getByText('npm-home')).toBeInTheDocument());
    await screen.findByRole('group', { name: 'templates.filter_tags' });
    await screen.findByRole('group', { name: 'templates.filter_environments' });
    expect(alert()).toBeNull();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
    expect(screen.queryByText('templates.card.provider_unread')).toBeNull();
  });

  it('says the integration catalogue could not be read rather than leaving it blank', async () => {
    providersAnswer = 'fails';
    show();
    await ready();
    expect(await screen.findByText('templates.context_failed')).toBeInTheDocument();
  });

  it('carries the reason the read gave, not the template list own wording', async () => {
    providersAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('templates.context_failed');
    expect(screen.getByText('providers refused')).toBeInTheDocument();
    expect(screen.queryByText('templates.error_description')).toBeNull();
  });

  it('stops calling a live integration deleted once the catalogue has failed', async () => {
    // The finding. An id the map does not hold used to mean one thing, and the card stated
    // it in warning colour: "Provider removed", about an integration nobody had touched.
    providersAnswer = 'fails';
    show();
    await ready();
    expect(await screen.findByText('templates.card.provider_unread')).toBeInTheDocument();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
  });

  it('says unread while the catalogue is merely still in flight', async () => {
    // Pending is the state the first paint of this page is actually in: `/templates` and
    // `/providers` start together and the cards are drawn the instant the first answers.
    providersAnswer = 'never';
    show();
    await ready();
    expect(await screen.findByText('templates.card.provider_unread')).toBeInTheDocument();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
    // Nothing failed, so the page has nothing to warn about yet.
    expect(alert()).toBeNull();
  });

  it('still calls an integration deleted when the catalogue came back without it', async () => {
    // The positive control. A catalogue that answered and does not hold id 7 is the one
    // case where "removed" is a measurement, and it has to survive the fix.
    providersAnswer = 'rows';
    const rows: Provider[] = [];
    const { queryClient } = show();
    await ready();
    queryClient.setQueryData(['providers'], rows);
    expect(await screen.findByText('templates.card.provider_missing')).toBeInTheDocument();
    expect(screen.queryByText('templates.card.provider_unread')).toBeNull();
  });

  it('says so for a label read too, not only for the integrations', async () => {
    tagsAnswer = 'fails';
    show();
    await ready();
    expect(await screen.findByText('templates.context_failed')).toBeInTheDocument();
    expect(screen.getByText('tags refused')).toBeInTheDocument();
  });

  it('says so for the environments read as readily as for the tags one', async () => {
    environmentsAnswer = 'fails';
    show();
    await ready();
    expect(await screen.findByText('templates.context_failed')).toBeInTheDocument();
    expect(screen.getByText('envs refused')).toBeInTheDocument();
  });

  it('warns once for all three rather than stacking one warning per read', async () => {
    providersAnswer = 'fails';
    tagsAnswer = 'fails';
    environmentsAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('templates.context_failed');
    expect(screen.getAllByText('templates.context_failed')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'templates.retry' })).toHaveLength(1);
  });

  it('drops the filter row when the labels cannot be read, and brings it back on retry', async () => {
    // The chips are drawn from labels resolved by id, so a failed read took the whole row
    // away without a word. The warning is what accounts for it; the retry is what undoes it.
    tagsAnswer = 'fails';
    environmentsAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('templates.context_failed');
    expect(tagFacets()).toBeNull();
    expect(environmentFacets()).toBeNull();

    tagsAnswer = 'rows';
    environmentsAnswer = 'rows';
    await userEvent.click(retry());
    const tags = await screen.findByRole('group', { name: 'templates.filter_tags' });
    const envs = await screen.findByRole('group', { name: 'templates.filter_environments' });
    expect(within(tags).getByText('edge')).toBeInTheDocument();
    expect(within(envs).getByText('prod')).toBeInTheDocument();
    expect(alert()).toBeNull();
  });

  it('names the integration once the retry answers', async () => {
    providersAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('templates.card.provider_unread');

    providersAnswer = 'rows';
    await userEvent.click(retry());
    await waitFor(() => expect(screen.getByText('npm-home')).toBeInTheDocument());
    expect(screen.queryByText('templates.card.provider_unread')).toBeNull();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
    expect(alert()).toBeNull();
  });

  it('retries only the reads that failed', async () => {
    const { api } = await import('@/api/client');
    tagsAnswer = 'fails';
    show();
    await ready();
    await screen.findByText('templates.context_failed');
    vi.mocked(api.get).mockClear();

    tagsAnswer = 'rows';
    await userEvent.click(retry());
    await waitFor(() => expect(alert()).toBeNull());
    const paths = vi.mocked(api.get).mock.calls.map(([path]) => path);
    expect(paths).toContain('/tags');
    expect(paths).not.toContain('/providers');
    expect(paths).not.toContain('/environments');
  });

  it('leaves the retry usable while a sibling read is still in flight', async () => {
    // The retry is the only way back from the warning, and it sits on a button whose
    // busy state also disables it. Read across all three, a sibling that never answers
    // would hold that button shut for good, on the one page that is telling the
    // operator something is wrong. Only the read that failed may claim it.
    const { api } = await import('@/api/client');
    tagsAnswer = 'fails';
    environmentsAnswer = 'never';
    show();
    await ready();
    await screen.findByText('templates.context_failed');
    expect(retry()).toBeEnabled();

    vi.mocked(api.get).mockClear();
    tagsAnswer = 'rows';
    await userEvent.click(retry());
    await waitFor(() => expect(alert()).toBeNull());
    expect(vi.mocked(api.get).mock.calls.map(([path]) => path)).toContain('/tags');
    await screen.findByRole('group', { name: 'templates.filter_tags' });
  });
});
