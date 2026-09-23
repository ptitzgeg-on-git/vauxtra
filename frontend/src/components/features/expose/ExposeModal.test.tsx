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
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Environment, PreflightResult, Provider, Service, Tag } from '@/types/api';

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

/** The labels the instance offers. */
const TAGS: Tag[] = [
  { id: 1, name: 'prod', color: '' },
  { id: 2, name: 'media', color: '' },
];
const ENVIRONMENTS: Environment[] = [
  { id: 5, name: 'staging', color: '' },
  { id: 6, name: 'lab', color: '' },
];

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

/** A preflight with nothing to report, so the review step has a summary to read. */
const PREFLIGHT: PreflightResult = {
  ok: true,
  public_host: 'grafana.example.test',
  checks: [],
  summary: { blocking_failures: 0, warnings: 0, total: 0 },
};

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path.startsWith('/services/public-target/suggest')) return suggestion();
      if (path === '/providers') return Promise.resolve([PROXY, DNS]);
      if (path === '/providers/types') return Promise.resolve({});
      if (path === '/domains') return Promise.resolve(['example.test']);
      if (path === '/tags') return Promise.resolve(TAGS);
      if (path === '/environments') return Promise.resolve(ENVIRONMENTS);
      return Promise.resolve([]);
    }),
    post: vi.fn((path: string) => Promise.resolve(path === '/services/preflight' ? PREFLIGHT : { ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    patch: vi.fn(() => Promise.resolve(SERVICE)),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { ExposeModal, withHostHighlighted } = await import('./ExposeModal');
const { api } = await import('@/api/client');

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

/**
 * A route without a port, sent from the form.
 *
 * `validate_port_when_forwarded` (`app/api/services.py`) takes port 0 only for a name
 * published in DNS alone: a proxy host or a tunnel rule pointed at it is a route to nowhere.
 * The form required a port in every mode, so the one route that has none could not be
 * saved, and the 0 of a saved one came back as 80.
 */
describe('ExposeModal, a route without a port', () => {
  const DNS_ONLY: Service = {
    ...SERVICE,
    target_port: 0,
    proxy_provider_id: null,
    public_target_mode: 'manual',
    auto_update_dns: false,
    dns_ip: '203.0.113.5',
  };

  beforeEach(() => {
    vi.mocked(api.post).mockClear();
  });

  it('sends a route published in DNS alone to the checks with no port', async () => {
    renderWithProviders(<ExposeModal isOpen onClose={vi.fn()} mode="edit" service={DNS_ONLY} />);
    await targetField();
    await userEvent.click(continueButton());

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/services/preflight', expect.objectContaining({ target_port: 0 })),
    );
    expect(screen.queryByText('expose.validation.port_required')).toBeNull();
  });

  it('refuses a route behind a proxy that has no port', async () => {
    renderWithProviders(<ExposeModal isOpen onClose={vi.fn()} mode="edit" service={{ ...SERVICE, target_port: 0 }} />);
    await targetField();
    // Submitted directly: a click stops at the browser's own `required` on the empty field
    // first, and this is the rule behind it, the one the API applies.
    const button = continueButton();
    if (!(button instanceof HTMLButtonElement) || !button.form) throw new Error('Continue is not tied to the form');
    fireEvent.submit(button.form);

    expect(await screen.findByText('expose.validation.port_required')).not.toBeNull();
    expect(api.post).not.toHaveBeenCalledWith('/services/preflight', expect.anything());
  });
});

/**
 * A change of tags or environments, and nothing else, saved without the checks and the push.
 *
 * Found in production on 2026-09-22 and confirmed in the code: no API route set a label
 * alone, so the only way to label a route was the whole wizard, whose last step is a `PUT`
 * that publishes the route again. That was not tried there, on purpose: a tag cost a round
 * of provider calls, and on a tunnel it rewrote the rule, which came back without its origin
 * settings. No provider holds a label, so an edit that moves labels alone is one `PATCH`,
 * sent from the first step, with the keys that moved.
 */
describe('ExposeModal, an edit that only changes labels', () => {
  const LABELLED: Service = { ...SERVICE, tags: TAGS.slice(0, 1) };

  const open = (service: Service = LABELLED) =>
    renderWithProviders(<ExposeModal isOpen onClose={vi.fn()} mode="edit" service={service} />);

  /** A tag or an environment, once the list it belongs to has loaded. */
  const chip = async (group: string, name: string) =>
    within(await screen.findByRole('group', { name: group })).findByRole('button', { name });

  const saveLabelsButton = () => screen.queryByRole('button', { name: 'expose.labels_only.save' });

  const saveLabels = async () => {
    const button = saveLabelsButton();
    if (!button) throw new Error('the form offers no labels-only save');
    await userEvent.click(button);
  };

  beforeEach(() => {
    vi.mocked(api.post).mockClear();
    vi.mocked(api.put).mockClear();
    vi.mocked(api.patch).mockClear();
  });

  it('saves a tag with one PATCH, and runs neither the checks nor the push', async () => {
    open();
    await userEvent.click(await chip('expose.field.tags', 'media'));

    // The line under the title stops promising the push, and says it once: `getByText`
    // throws on a second copy, so a footer that repeated it would fail here.
    expect(screen.getByText('expose.labels_only.hint')).not.toBeNull();
    expect(screen.queryByText('expose.description.edit')).toBeNull();

    await saveLabels();

    expect(await screen.findByText('expose.done.labels_title')).not.toBeNull();
    // The receipt says no provider was contacted; the line above it must not say otherwise.
    expect(screen.queryByText('expose.description.edit')).toBeNull();
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch).toHaveBeenCalledWith('/services/42', { tag_ids: [1, 2] });
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();

    // Configure is ticked. Review never ran, so it keeps its number rather than a tick.
    const steps = within(screen.getByRole('list', { name: 'expose.steps.label' }));
    expect(steps.queryByText('1')).toBeNull();
    expect(steps.getByText('2')).not.toBeNull();
  });

  it('sends only the half that moved', async () => {
    open();
    await userEvent.click(await chip('expose.field.environments', 'staging'));
    await saveLabels();

    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/services/42', { environment_ids: [5] }));
  });

  it('sends a label that travels with a route change through the checks, as before', async () => {
    open();
    expect(await screen.findByText(FOUND)).not.toBeNull();
    await userEvent.click(await chip('expose.field.tags', 'media'));
    const port = screen.getByRole('textbox', { name: /^expose\.field\.port/ });
    await userEvent.clear(port);
    await userEvent.type(port, '3001');

    expect(saveLabelsButton()).toBeNull();
    expect(screen.getByText('expose.description.edit')).not.toBeNull();
    expect(screen.queryByText('expose.labels_only.hint')).toBeNull();
    await userEvent.click(continueButton());
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/services/preflight',
        expect.objectContaining({ tag_ids: [1, 2], target_port: 3001 }),
      ),
    );
    expect(api.patch).not.toHaveBeenCalled();
  });

  it('keeps an untouched form on Continue, because saving it unchanged publishes it again', async () => {
    open();
    await chip('expose.field.tags', 'prod');

    expect(continueButton()).not.toBeNull();
    expect(saveLabelsButton()).toBeNull();
    expect(screen.queryByText('expose.labels_only.hint')).toBeNull();
    expect(screen.getByText('expose.description.edit')).not.toBeNull();
  });

  it('does not count a tag unticked and ticked again as a change', async () => {
    open({ ...SERVICE, tags: TAGS });
    await userEvent.click(await chip('expose.field.tags', 'prod'));
    expect(saveLabelsButton()).not.toBeNull();

    // Back in the list, at the end of it this time: [2, 1] against the [1, 2] it was saved with.
    await userEvent.click(await chip('expose.field.tags', 'prod'));
    expect(saveLabelsButton()).toBeNull();
    expect(continueButton()).not.toBeNull();
  });

  it('saves while the public-target lookup is failing, which only the route depends on', async () => {
    suggestAnswer = 'fails';
    open();
    expect(await screen.findByText('expose.detect_unread')).not.toBeNull();
    await userEvent.click(await chip('expose.field.tags', 'media'));
    await saveLabels();

    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/services/42', { tag_ids: [1, 2] }));
    expect(screen.queryByText('expose.validation.auto_target_unread')).toBeNull();
  });

  it('stays on the form and says why when the save is refused', async () => {
    vi.mocked(api.patch).mockRejectedValueOnce(new Error('service 42 is gone'));
    open();
    await userEvent.click(await chip('expose.field.tags', 'media'));
    await saveLabels();

    // In the banner: `renderWithProviders` mounts no toaster, so this is the only copy.
    expect(await screen.findByText('service 42 is gone')).not.toBeNull();
    expect(screen.queryByText('expose.done.labels_title')).toBeNull();
    expect(saveLabelsButton()).not.toBeNull();
  });
});

/**
 * The success sentence names the host exactly once, in a monospace run of its own.
 *
 * `expose.done.body` used to be called with no parameter at all, so the last screen of the
 * wizard printed the literal `{host}` and the value was appended after it. Handing `t()` the
 * host fixes the count and loses the monospace run, `t()` returning a plain string;
 * `withHostHighlighted` splits the finished sentence on the host instead, which keeps both.
 *
 * `scripts/check-locale-usage.mjs` already fails the build on the cause: a `t()` call that
 * fills none of the holes its sentence presents. What it cannot see is the rendering. Putting
 * a trailing `<span>{saveOutcome.host}</span>` back next to the fixed call would print the
 * host twice and leave every gate in CI green, so these are assertions on the output.
 *
 * Asserted on the helper rather than through the wizard, because `renderWithProviders` leaves
 * `I18nProvider` out on purpose: `t()` is the context default `(key) => key` and interpolates
 * nothing, so a rendered `done` step would be splitting the string `expose.done.body` and the
 * assertions would be about nothing. Interpolation is `t()`'s half of the job and is done
 * here by `filled()`; the splitting is the half that lives in the component.
 */

const HOST = 'app.xeno.homes';

/** The English string as it stands in `en.json`, placeholder included. */
const EN = '{host} is published on its providers.';

/** What `t()` has already done to the template by the time the component sees it. */
const filled = (template: string) => template.split('{host}').join(HOST);

/** The sentence as the `done` step paints it: one paragraph, host split back out of it. */
const sentence = (template: string) =>
  renderWithProviders(<p>{withHostHighlighted(filled(template), HOST)}</p>);

describe('withHostHighlighted, the sentence the wizard ends on', () => {
  it('prints the host once and leaves no placeholder behind', () => {
    const { container } = sentence(EN);

    expect(container.textContent).toBe('app.xeno.homes is published on its providers.');
    expect(container.textContent).not.toContain('{host}');
    expect(screen.getAllByText(HOST)).toHaveLength(1);
  });

  it('gives the host its own monospace run rather than the whole sentence', () => {
    const { container } = sentence(EN);

    const mono = container.querySelectorAll('.font-mono');
    expect(mono).toHaveLength(1);
    expect(mono[0]).toHaveTextContent(HOST);
    expect(mono[0]?.textContent).toBe(HOST);
  });

  it('keeps the words in the order the translation put them', () => {
    // All eight sentences open on the host today, and none of them has to: a translator is
    // free to move it, and the sentence has to read correctly when they do.
    const { container } = sentence('Der Dienst {host} ist veroeffentlicht.');

    expect(container.textContent).toBe('Der Dienst app.xeno.homes ist veroeffentlicht.');
    expect(screen.getAllByText(HOST)).toHaveLength(1);
  });

  it('drops the host entirely when a translation dropped the placeholder', () => {
    // Deliberately the opposite claim from the one that would be natural to want. The host
    // is recovered by splitting the finished sentence on its value, so a translation with no
    // `{host}` in it leaves nothing to split on and the name is simply absent -- there is no
    // appended copy to fall back to, and no monospace run either. The sentence still reads,
    // which is why nothing on screen announces it; the gate for that case is
    // `scripts/check-locale-quality.mjs` comparing the eight files, not this component.
    const { container } = sentence('Der Dienst ist veroeffentlicht.');

    expect(container.textContent).toBe('Der Dienst ist veroeffentlicht.');
    expect(screen.queryAllByText(HOST)).toHaveLength(0);
    expect(container.querySelectorAll('.font-mono')).toHaveLength(0);
  });
});
