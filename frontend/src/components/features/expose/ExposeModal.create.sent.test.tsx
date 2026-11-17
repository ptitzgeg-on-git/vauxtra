/**
 * What the create form sends, held against what it showed.
 *
 * Each test under a defect says it in words, and was red on the form it was written against.
 * A test marked "Guard" pins down a behaviour that already worked and that no other test
 * reached: taking it out of the form turns that test red.
 */
import { act, screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import {
  callsTo,
  chooseMode,
  clickContinue,
  continueToReview,
  failWith,
  field,
  holdCalls,
  installExposeApi,
  integrations,
  makeEagerQueryClient,
  openCreate,
  pickOption,
  preflightBodies,
  pressEnterIn,
  refocusWindow,
  savedBodies,
  typeDomain,
  world,
} from './exposeModalHarness';
import { templateToFormState } from './types';

installExposeApi();

const SAVE = 'expose.create_route';
const SUGGEST = '/services/public-target/suggest';

async function nameRoute(user: UserEvent, subdomain = 'jellyfin'): Promise<void> {
  await user.clear(field.subdomain());
  await user.type(field.subdomain(), subdomain);
  if (!(field.domain() as HTMLInputElement).value) await typeDomain(user, 'example.com');
}

/** A route behind `npm-front` with a LAN resolver, its record left for the proxy fallback. */
async function lanRouteOnFallback(user: UserEvent): Promise<void> {
  await nameRoute(user);
  await user.type(field.target(), '10.0.0.20');
  await pickOption(field.primaryProxy, 'npm-front');
  await pickOption(field.primaryDns, 'pihole-lan');
}

async function lanRoute(user: UserEvent): Promise<void> {
  await lanRouteOnFallback(user);
  await user.type(field.dnsTarget(), '10.0.0.30');
}

const detectButton = () => screen.getByRole('button', { name: 'expose.detect' });

/** "Detect" is busy while its lookup runs, and free again once it is answered. */
async function detectSettled(): Promise<void> {
  await waitFor(() => expect(detectButton()).not.toHaveAttribute('aria-busy', 'true'));
}

/**
 * Lets every answer already given reach the screen. TanStack Query tells its observers on a
 * timer, so a value that must not move is read after this, not inside a `waitFor` that would
 * pass on its first try.
 */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

describe('a LAN record left to the proxy fallback', () => {
  // The hint and the placeholder name the proxy as the record's target. The lookup made for a
  // public zone a moment before was still cached for this proxy, and a disabled query hands
  // its data back, so the payload sent that public address in place of the one announced.
  it('sends the proxy the hint announces, not a lookup made for a public zone', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(detectButton());
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.40'));

    await pickOption(field.primaryDns, 'pihole-lan');
    expect(field.dnsTarget()).toHaveValue('');
    expect(field.dnsTarget()).toHaveAttribute('placeholder', '10.0.0.30');
    expect(screen.getByText('expose.field.dns_target_proxy_hint')).toBeInTheDocument();
    expect((await continueToReview(user)).dns_ip).toBe('10.0.0.30');
  });

  // The cache outlives the form: a lookup from a discarded form reached the next one, which
  // never showed it.
  it('sends it in the next form too, where that lookup was never shown', async () => {
    const { user, reopen } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(screen.getByRole('switch', { name: 'expose.field.auto_update_dns' }));
    expect(await screen.findByText('10.0.0.40')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'common.cancel' }));
    const confirm = await screen.findByRole('dialog', { name: 'expose.discard.title' });
    await user.click(within(confirm).getByRole('button', { name: 'expose.discard.confirm' }));

    reopen();
    await lanRouteOnFallback(user);
    expect(field.dnsTarget()).toHaveValue('');
    expect(screen.queryByText('10.0.0.40')).toBeNull();
    expect((await continueToReview(user)).dns_ip).toBe('10.0.0.30');
  });

  // In DNS alone the LAN record takes the service's own address, and the form hides the
  // target field to say so. The public lookup went out instead.
  it('sends the service address in DNS alone, not a lookup made for a public zone', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '192.168.1.20');
    await pickOption(field.primaryDns, 'cf-edge');
    await chooseMode(user, 'dns_only');
    await user.click(detectButton());
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.40'));

    await pickOption(field.primaryDns, 'pihole-lan');
    expect(screen.queryByRole('textbox', { name: /^expose\.field\.dns_target_(local|external)\b/ })).toBeNull();
    expect((await continueToReview(user)).dns_ip).toBe('192.168.1.20');
  });
});

describe('a URL typed in the target and sent with Enter', () => {
  // Enter submits the form without leaving the field, so the split made on leaving it never
  // ran: the URL went to the checks whole, and the LAN record in DNS alone pointed at it.
  it('is split before the checks are asked, and shown split on the way back', async () => {
    const { user } = openCreate();
    await nameRoute(user, 'grafana');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'pihole-lan');
    await user.type(field.target(), 'http://10.0.0.20:3000');
    expect(field.target()).toHaveFocus();

    await pressEnterIn(field.target());
    await waitFor(() => expect(preflightBodies()).toHaveLength(1));
    expect(preflightBodies()[0]).toMatchObject({
      target_ip: '10.0.0.20',
      target_port: 3000,
      forward_scheme: 'http',
      dns_ip: '10.0.0.30',
    });

    await user.click(await screen.findByRole('button', { name: 'common.back' }));
    expect(field.target()).toHaveValue('10.0.0.20');
    expect(field.port()).toHaveValue('3000');
  });

  it('is split in DNS alone, where the target is the record as well', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await pickOption(field.primaryDns, 'pihole-lan');
    await chooseMode(user, 'dns_only');
    await user.type(field.target(), 'http://192.168.1.20:8080');

    await pressEnterIn(field.target());
    await waitFor(() => expect(preflightBodies()).toHaveLength(1));
    expect(preflightBodies()[0]).toMatchObject({ target_ip: '192.168.1.20', dns_ip: '192.168.1.20' });
  });
});

describe('a proxy known by a name of one label', () => {
  // `http://npm:81` is how a panel beside the proxy in one Docker network reaches it. That
  // name resolves on that network only, so a LAN record pointing at it sends nobody anywhere.
  it('asks for the LAN record rather than fall back on the name', async () => {
    world.providers = world.providers.map((provider) =>
      provider.id === integrations.npmFront.id ? { ...provider, url: 'http://npm:81' } : provider,
    );
    const { user } = openCreate();
    await lanRouteOnFallback(user);
    await clickContinue(user);
    expect(await screen.findByText('expose.validation.dns_target_local_required')).toBeInTheDocument();
    expect(preflightBodies()).toHaveLength(0);
  });
});

describe('Detect and a target typed by hand', () => {
  // The lookup is slow. An address typed while it ran was overwritten by its answer.
  it('leaves an address typed while the lookup ran', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    await user.type(field.dnsTarget(), '10.0.0.50');
    await lookup.release();
    await detectSettled();
    await settle();
    expect(field.dnsTarget()).toHaveValue('10.0.0.50');
  });

  // Guard: a lookup that failed still holds its previous answer as data.
  it('fills nothing when the lookup fails after an answer', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(detectButton());
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.40'));
    await user.clear(field.dnsTarget());

    world.suggestionFate = 'fails';
    await user.click(detectButton());
    await waitFor(() => expect(callsTo('get', SUGGEST)).toHaveLength(2));
    await detectSettled();
    await settle();
    expect(field.dnsTarget()).toHaveValue('');
  });
});

describe('Detect and a route changed while it waited', () => {
  // Detect looked at the field alone. A DNS switched from the public zone to the LAN while the
  // lookup ran empties the field, as any change of scope does, so the field still held what it
  // held at the click, and the public address the answer named went into the LAN record.
  it('drops the answer once the DNS has changed since the click', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    await pickOption(field.primaryDns, 'pihole-lan');
    world.suggestion = '10.0.0.41';
    await lookup.release();
    await waitFor(() => expect(callsTo('get', SUGGEST)).toHaveLength(1));
    await settle();
    expect(field.dnsTarget()).toHaveValue('');
    expect((await continueToReview(user)).dns_ip).toBe('10.0.0.30');
  });

  // Guard: the DNS is held to the one of the click, not to its scope. A switch to another
  // public zone keeps the field as it is, so a check of scope alone would let the answer in;
  // any change drops it, even to a DNS of the same scope. The second zone is this test's own:
  // `beforeEach` builds `world` again.
  it('drops the answer once the DNS has changed to another zone of the same scope', async () => {
    world.providers = [...world.providers, { ...integrations.cfEdge, id: 11, name: 'cf-edge-b' }];
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    await pickOption(field.primaryDns, 'cf-edge-b');
    world.suggestion = '10.0.0.41';
    await lookup.release();
    await detectSettled();
    await settle();
    expect(field.dnsTarget()).toHaveValue('');
  });

  // The answer is a proxy's: the lookup is asked for the proxy of the click, and read back
  // from the query of the proxy chosen when it answers. Switched back to `npm-back` while it
  // ran, the form wrote what `npm-back` had answered before, which this click never asked.
  it('drops the answer once the proxy has changed since the click', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-back');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(detectButton());
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.40'));
    await user.clear(field.dnsTarget());
    await pickOption(field.primaryProxy, 'npm-front');
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    await pickOption(field.primaryProxy, 'npm-back');
    world.suggestion = '10.0.0.41';
    await lookup.release();
    await waitFor(() => expect(callsTo('get', SUGGEST)).toHaveLength(2));
    await settle();
    expect(field.dnsTarget()).toHaveValue('');
  });

  // Guard, and the control of the three above: with the field, the DNS and the proxy as they
  // were at the click, the answer that comes while the lookup ran is written, and sent.
  it('fills the field when nothing has changed since the click', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    world.suggestion = '10.0.0.41';
    await lookup.release();
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.41'));
    expect((await continueToReview(user)).dns_ip).toBe('10.0.0.41');
  });
});

describe('between the review and the save', () => {
  // The save built its body again from lists read since the review. Back from another tab,
  // the integrations are read again, so a proxy changed there changed the record after the
  // checks, and without them.
  it('saves the route the checks were asked about', async () => {
    const { user, queryClient } = openCreate({ queryClient: makeEagerQueryClient() });
    await lanRouteOnFallback(user);
    const reviewed = await continueToReview(user);
    expect(reviewed.dns_ip).toBe('10.0.0.30');

    const before = callsTo('get', '/providers').length;
    world.providers = world.providers.map((provider) =>
      provider.id === integrations.npmFront.id ? { ...provider, url: 'http://10.0.0.32:81' } : provider,
    );
    refocusWindow();
    await waitFor(() => expect(callsTo('get', '/providers').length).toBeGreaterThan(before));
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    await settle();

    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(savedBodies()).toHaveLength(1));
    expect({ ...savedBodies()[0], service_id: reviewed.service_id }).toEqual(reviewed);
  });

  it('saves it when the proxy was turned off in the meantime', async () => {
    const { user, queryClient } = openCreate({ queryClient: makeEagerQueryClient() });
    await lanRouteOnFallback(user);
    const reviewed = await continueToReview(user);

    const before = callsTo('get', '/providers').length;
    world.providers = world.providers.map((provider) =>
      provider.id === integrations.npmFront.id ? { ...provider, enabled: false } : provider,
    );
    refocusWindow();
    await waitFor(() => expect(callsTo('get', '/providers').length).toBeGreaterThan(before));
    await waitFor(() => expect(queryClient.isFetching()).toBe(0));
    await settle();

    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(savedBodies()).toHaveLength(1));
    expect({ ...savedBodies()[0], service_id: reviewed.service_id }).toEqual(reviewed);
  });

  // Guard: a refusal on screen is about the last try, so the next try takes it down.
  it('takes the last refusal down when the save is tried again', async () => {
    failWith('post', '/services', 409, { detail: 'exists' });
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: SAVE }));
    expect(await screen.findByText('expose.toast.create_failed')).toBeInTheDocument();

    world.failures = {};
    const save = holdCalls('post', '/services');
    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    expect(screen.queryByText('expose.toast.create_failed')).toBeNull();
    await save.release();
    await waitFor(() => expect(savedBodies()).toHaveLength(2));
  });

  // Guard: "Detect" can answer once the review is on screen, and its answer still writes the
  // DNS target when the field, the DNS and the proxy are the ones of the click. "Create" sends
  // the body the checks were asked about, not the form as that answer left it.
  it('saves the target reviewed when a lookup answers after the checks', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.type(field.dnsTarget(), '192.168.1.21');
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    const reviewed = await continueToReview(user);
    expect(reviewed.dns_ip).toBe('192.168.1.21');
    await lookup.release();
    await waitFor(() => expect(callsTo('get', SUGGEST)).toHaveLength(1));
    await settle();

    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(savedBodies()).toHaveLength(1));
    expect({ ...savedBodies()[0], service_id: reviewed.service_id }).toEqual(reviewed);
  });

  // "Check again" while the save was out sent the checks again: answered after the save,
  // they took the receipt back to the review, whose "Create" saved the route a second time.
  // Both buttons that leave the review now wait for the save's answer.
  it('holds "Back" and "Check again" while the save is out', async () => {
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    const save = holdCalls('post', '/services');
    const checks = holdCalls('post', '/services/preflight');
    await user.click(await screen.findByRole('button', { name: SAVE }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'common.back' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'expose.preflight.rerun' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'expose.preflight.rerun' }));
    await settle();
    expect(checks.held).toHaveLength(0);

    await save.release();
    expect(await screen.findByText('expose.done.created_title')).toBeInTheDocument();
    await checks.release();
    await settle();
    expect(screen.getByText('expose.done.created_title')).toBeInTheDocument();
    expect(callsTo('post', '/services')).toHaveLength(1);
    expect(preflightBodies()).toHaveLength(1);
  });

  // "Back" while "Check again" ran left for the form, and the answer, asked by the same
  // opening, brought the review back on the body sent at that click: what had been changed in
  // the form since was not in it, and the operator was back on a step they had left.
  // Guard, for "Create": the checks running already held it, and no test pinned that down.
  // Open, its save could answer first, and the checks, answered after it, would put the
  // review back with "Create" open for a second `POST /services`.
  it('holds "Back" and "Create" while "Check again" runs', async () => {
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    const checks = holdCalls('post', '/services/preflight');
    await user.click(screen.getByRole('button', { name: 'expose.preflight.rerun' }));
    await waitFor(() => expect(checks.held).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'common.back' })).toBeDisabled();
    expect(screen.getByRole('button', { name: SAVE })).toBeDisabled();

    await checks.release();
    await waitFor(() => expect(screen.getByRole('button', { name: 'common.back' })).toBeEnabled());
    expect(screen.getByRole('button', { name: SAVE })).toBeEnabled();
    expect(preflightBodies()).toHaveLength(2);
  });
});

describe('a refused Continue, then a close', () => {
  // Guard: the marks of a refused Continue belong to that form, not to the next one.
  it('opens the next form unmarked', async () => {
    const { user, onClose, reopen } = openCreate();
    await clickContinue(user);
    expect(await screen.findByText('expose.validation.target_required')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'common.cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);

    reopen();
    await waitFor(() => expect(field.mode('tunnel')).toBeEnabled());
    expect(screen.queryAllByText(/^expose\.validation\./)).toEqual([]);
    expect(document.querySelector('[aria-invalid="true"]')).toBeNull();
  });
});

describe('a template saved from DNS alone', () => {
  // The API stores a template's port as nothing or 1 to 65535, never 0, and a template has no
  // mode of its own for DNS alone: it is `proxy_dns` with no proxy.
  it('is saved with no port, and applied with none', async () => {
    const first = openCreate();
    await nameRoute(first.user);
    await chooseMode(first.user, 'dns_only');
    await pickOption(field.primaryDns, 'pihole-lan');
    await first.user.type(field.target(), '192.168.1.20');
    await first.user.clear(field.port());
    await first.user.click(field.target());
    expect(field.port()).toHaveValue('');

    await first.user.type(screen.getByRole('textbox', { name: 'expose.template.name_label' }), 'DNS only');
    await first.user.click(screen.getByRole('button', { name: 'expose.template.save' }));
    await waitFor(() => expect(callsTo('post', '/templates')).toHaveLength(1));
    const saved = callsTo('post', '/templates')[0].body as Record<string, unknown>;
    // No record target either: the one this route falls back on is its own address, which a
    // template applied to another service would carry along (ux-audit.md § 1.2).
    expect(saved).toMatchObject({
      target_port: null,
      expose_mode: 'proxy_dns',
      proxy_provider_id: null,
      dns_provider_id: 3,
      dns_ip: '',
    });
    first.unmount();

    const second = openCreate({ initialState: templateToFormState(saved), templateName: String(saved.name) });
    await nameRoute(second.user);
    await second.user.type(field.target(), '192.168.1.20');
    expect(field.port()).toHaveValue('');
    const sent = await continueToReview(second.user);
    expect(sent.target_port).toBe(0);
    expect(screen.queryByText('expose.validation.port_required')).toBeNull();
  });

  // A route made from a template names the same LAN record as one typed in directly.
  it('points a LAN record at the service once applied and saved', async () => {
    const applied = templateToFormState({
      expose_mode: 'proxy_dns',
      proxy_provider_id: null,
      dns_provider_id: 3,
      dns_ip: '',
      domain: 'example.com',
      target_port: null,
      forward_scheme: 'http',
    });
    expect(applied.ui_expose_mode).toBe('dns_only');
    const { user } = openCreate({ initialState: applied, templateName: 'LAN only' });
    await nameRoute(user);
    await user.type(field.target(), '192.168.1.20');
    expect((await continueToReview(user)).dns_ip).toBe('192.168.1.20');

    await user.click(await screen.findByRole('button', { name: SAVE }));
    await waitFor(() => expect(savedBodies()).toHaveLength(1));
    expect(savedBodies()[0].dns_ip).toBe('192.168.1.20');
  });
});
