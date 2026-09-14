/**
 * What the form said about a lookup that had never answered.
 *
 * `['public-target-suggest']` asks the server to find the selected proxy's public address,
 * and `suggest_public_targets` gets there by making a real outbound call: the read is both
 * slow and failable. It was destructured down to `data`, `isFetching` and `refetch`, with no
 * failure state at all, and the answer's `recommended` is an empty string when nothing
 * replies. A lookup that failed and a proxy that genuinely has no public target therefore
 * arrived in exactly the same shape.
 *
 * Two things followed from that. The Detect button spun, stopped, and changed nothing on
 * screen, so an operator could press it repeatedly with no way to tell it apart from a
 * proxy with nothing to report. And `validate()` refused to continue with "This proxy
 * cannot detect its public target" -- a verdict on the proxy, handed down when the app had
 * never received an answer about it, sending the operator off to check credentials over a
 * request that had simply timed out.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it, vi, beforeAll, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Provider, Service } from '@/types/api';

const PROXY: Provider = {
  id: 7,
  name: 'npm-front',
  type: 'npm',
  url: 'http://10.0.0.30:81',
  username: 'ops',
  enabled: true,
  extra: {},
  created_at: '2026-01-01 09:00:00',
};

/** Cloudflare is one of the two types that declare `supports_auto_public_target`. */
const DNS: Provider = { ...PROXY, id: 8, name: 'cf-edge', type: 'cloudflare' };

/** An existing route, so the form opens filled and only the public target is missing. */
const SERVICE: Service = {
  id: 42,
  subdomain: 'grafana',
  domain: 'example.test',
  target_ip: '10.0.0.20',
  target_port: 3000,
  forward_scheme: 'http',
  websocket: true,
  expose_mode: 'proxy_dns',
  public_target_mode: 'auto',
  auto_update_dns: true,
  tunnel_hostname: '',
  dns_ip: '',
  npm_host_id: null,
  dns_provider_id: 8,
  proxy_provider_id: 7,
  tunnel_provider_id: null,
  enabled: true,
  status: 'ok',
  last_checked: null,
  created_at: '2026-01-01 09:00:00',
  tags: [],
  environments: [],
};

/** How the suggestion answers: with an address, with none, with a failure, or never. */
type Answer = 'address' | 'nothing' | 'fails' | 'never';

// A `vi.fn(impl)` factory keeps its implementation across `restoreMocks`, so the answer
// lives in a variable the whole file resets rather than in per-test `mockImplementation`.
let suggestAnswer: Answer = 'address';
const NEVER: Promise<never> = new Promise(() => {});
const FOUND = '203.0.113.9';

function suggestion(): Promise<unknown> {
  if (suggestAnswer === 'fails') return Promise.reject(new Error('public target lookup refused'));
  if (suggestAnswer === 'never') return NEVER;
  return Promise.resolve({
    candidates: suggestAnswer === 'address' ? [{ value: FOUND, source: 'wan' }] : [],
    recommended: suggestAnswer === 'address' ? FOUND : '',
  });
}

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path.startsWith('/services/public-target/suggest')) return suggestion();
      if (path === '/providers') return Promise.resolve([PROXY, DNS]);
      if (path === '/providers/types') return Promise.resolve({});
      if (path === '/domains') return Promise.resolve(['example.test']);
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { ExposeModal } = await import('./ExposeModal');

const show = () =>
  renderWithProviders(<ExposeModal isOpen onClose={vi.fn()} mode="edit" service={SERVICE} />);

/** The DNS target box is only drawn once the provider list has named the DNS provider. */
const targetField = () => screen.findByLabelText('expose.field.dns_target_external');
const continueButton = () => screen.getByRole('button', { name: 'expose.continue' });

const unreadAlert = () => screen.queryByText('expose.detect_unread');
const noneAlert = () => screen.queryByText('expose.detect_none');
const detectedHint = () => screen.queryByText('expose.detected');

beforeAll(() => {
  // jsdom implements no layout, so it ships no `scrollIntoView`; the form error scrolls
  // itself into view the moment `validate()` refuses.
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  suggestAnswer = 'address';
});

describe('ExposeModal, and the public-target lookup', () => {
  it('offers the address once the lookup answers with one', async () => {
    show();
    await targetField();
    expect(await screen.findByText(FOUND)).not.toBeNull();
    expect(detectedHint()).not.toBeNull();
    expect(unreadAlert()).toBeNull();
    expect(noneAlert()).toBeNull();
  });

  it('says the lookup failed rather than passing a verdict on the proxy', async () => {
    suggestAnswer = 'fails';
    show();
    await targetField();
    expect(await screen.findByText('expose.detect_unread')).not.toBeNull();
    // The proxy is not the subject here: nothing was learnt about it either way.
    expect(noneAlert()).toBeNull();
    expect(detectedHint()).toBeNull();
  });

  it('says the proxy has no public target once the lookup answers with none', async () => {
    suggestAnswer = 'nothing';
    show();
    await targetField();
    expect(await screen.findByText('expose.detect_none')).not.toBeNull();
    expect(unreadAlert()).toBeNull();
  });

  it('says neither while the lookup is still in flight', async () => {
    suggestAnswer = 'never';
    show();
    await targetField();
    expect(unreadAlert()).toBeNull();
    expect(noneAlert()).toBeNull();
  });
});

describe('ExposeModal, refusing to continue without a public target', () => {
  it('names the failed lookup, not the proxy', async () => {
    suggestAnswer = 'fails';
    show();
    await targetField();
    await userEvent.click(continueButton());
    expect(await screen.findByText('expose.validation.auto_target_unread')).not.toBeNull();
    expect(screen.queryByText('expose.validation.no_auto_target')).toBeNull();
  });

  it('says the lookup is still running rather than concluding from it', async () => {
    suggestAnswer = 'never';
    show();
    await targetField();
    await userEvent.click(continueButton());
    expect(await screen.findByText('expose.validation.auto_target_pending')).not.toBeNull();
    expect(screen.queryByText('expose.validation.no_auto_target')).toBeNull();
  });

  it('still blames the proxy when the lookup answered and found nothing', async () => {
    suggestAnswer = 'nothing';
    show();
    await targetField();
    await userEvent.click(continueButton());
    expect(await screen.findByText('expose.validation.no_auto_target')).not.toBeNull();
    expect(screen.queryByText('expose.validation.auto_target_unread')).toBeNull();
  });
});

describe('ExposeModal, once a target is typed by hand', () => {
  it('drops "nothing was detected", which the typed target settles', async () => {
    suggestAnswer = 'nothing';
    show();
    const field = await targetField();
    expect(await screen.findByText('expose.detect_none')).not.toBeNull();
    await userEvent.type(field, '203.0.113.20');
    await waitFor(() => expect(noneAlert()).toBeNull());
  });

  it('keeps the failed lookup on screen, which it does not', async () => {
    suggestAnswer = 'fails';
    show();
    const field = await targetField();
    expect(await screen.findByText('expose.detect_unread')).not.toBeNull();
    await userEvent.type(field, '203.0.113.20');
    // The automatic-update switch below reads the same lookup, so the failure still
    // describes something the operator is about to decide on.
    expect(unreadAlert()).not.toBeNull();
  });
});
