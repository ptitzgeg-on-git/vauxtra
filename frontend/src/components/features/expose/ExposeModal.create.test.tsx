/**
 * The create form, mounted whole over `exposeModalHarness`: what a new route sends, and what
 * the operator is told on the way.
 *
 * Two kinds of test live here. Most pin a behaviour that already works and must survive the
 * rework of this form (`ux-audit.md` § 2.2). The ones under "defects" describe what the form
 * should do and did not when they were written: each names the audit entry it comes from.
 */
import { screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { optionLabels, selectValue } from '@/test/select';
import type { PreflightRequest } from '@/types/api';
import {
  apiLog,
  callsTo,
  chooseMode,
  clickContinue,
  continueToReview,
  failWith,
  field,
  installExposeApi,
  integrations,
  makeEagerQueryClient,
  openCreate,
  pickOption,
  preflightBodies,
  refocusWindow,
  savedBodies,
  tunnelAccessItem,
  typeDomain,
  world,
} from './exposeModalHarness';
import { initialForm } from './types';

installExposeApi();

const SAVE = 'expose.create_route';

async function nameRoute(user: UserEvent, subdomain = 'jellyfin'): Promise<void> {
  await user.clear(field.subdomain());
  await user.type(field.subdomain(), subdomain);
  await typeDomain(user, 'example.com');
}

/** A route behind `npm-front`, published on the LAN resolver at an address typed by hand. */
async function lanRoute(user: UserEvent, subdomain = 'jellyfin'): Promise<void> {
  await nameRoute(user, subdomain);
  await user.type(field.target(), '10.0.0.20');
  await pickOption(field.primaryProxy, 'npm-front');
  await pickOption(field.primaryDns, 'pihole-lan');
  await user.type(field.dnsTarget(), '10.0.0.30');
}

async function saveFromReview(user: UserEvent): Promise<void> {
  await user.click(await screen.findByRole('button', { name: SAVE }));
}

const twoTunnels = () => ({
  total: 2,
  items: [
    tunnelAccessItem(integrations.cfTunnel, { id: 'pol-staff', name: 'Staff' }),
    tunnelAccessItem(integrations.cfTunnelB, { id: 'pol-ops', name: 'Ops' }),
  ],
});

const suggestUrls = () => callsTo('get', '/services/public-target/suggest').map((call) => call.url);
const accessCalls = () => callsTo('post', /^\/providers\/\d+\/access\/apps$/);

describe('the create form keeps what works', () => {
  it('clears a LAN target when the DNS provider becomes a public one', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'pihole-lan');
    await user.type(field.dnsTarget(), '10.0.0.50');
    await pickOption(field.primaryDns, 'cf-edge');
    expect(field.dnsTarget()).toHaveValue('');
  });

  it('keeps it when the scope stays the same', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'pihole-lan');
    await user.type(field.dnsTarget(), '10.0.0.50');
    await pickOption(field.primaryDns, 'adguard-lan');
    expect(field.dnsTarget()).toHaveValue('10.0.0.50');
  });

  it('keeps a hostname the operator typed', async () => {
    const { user } = openCreate();
    await typeDomain(user, 'example.com');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    await user.clear(field.tunnelHostname());
    await user.type(field.tunnelHostname(), 'media.example.com');
    await user.clear(field.subdomain());
    await user.type(field.subdomain(), 'jellyfin');
    await user.type(field.target(), '10.0.0.20');
    expect((await continueToReview(user)).tunnel_hostname).toBe('media.example.com');
  });

  it('sends "*" and "*.dev" as typed', async () => {
    const { user } = openCreate();
    await lanRoute(user, '*');
    expect((await continueToReview(user)).subdomain).toBe('*');
    await user.click(screen.getByRole('button', { name: 'common.back' }));
    await user.clear(field.subdomain());
    await user.type(field.subdomain(), '*.dev');
    expect((await continueToReview(user)).subdomain).toBe('*.dev');
  });

  it('asks before discarding', async () => {
    const { user, onClose } = openCreate();
    await user.type(field.subdomain(), 'x');
    await user.click(screen.getByRole('button', { name: 'common.cancel' }));
    const confirm = await screen.findByRole('dialog', { name: 'expose.discard.title' });
    expect(onClose).not.toHaveBeenCalled();
    await user.click(within(confirm).getByRole('button', { name: 'expose.discard.confirm' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes without asking when nothing moved', async () => {
    const { user, onClose } = openCreate();
    // Every list read first: a form that changed itself on the way in would be "dirty".
    await waitFor(() => expect(field.mode('tunnel')).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'common.cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog', { name: 'expose.discard.title' })).toBeNull();
  });

  it('opens empty again after a close', async () => {
    const { user, reopen } = openCreate();
    await user.clear(field.subdomain());
    await user.type(field.subdomain(), 'jellyfin');
    await user.click(screen.getByRole('button', { name: 'common.cancel' }));
    const confirm = await screen.findByRole('dialog', { name: 'expose.discard.title' });
    await user.click(within(confirm).getByRole('button', { name: 'expose.discard.confirm' }));
    reopen();
    expect(field.subdomain()).toHaveValue(initialForm.subdomain);
  });

  it('lists the provider errors of a half-done save', async () => {
    world.saveResult = { ok: false, errors: ['npm-front: proxy host refused', 'pihole-lan: record locked'] };
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await saveFromReview(user);
    expect(await screen.findByText('expose.done.warnings_title')).toBeInTheDocument();
    expect(screen.getByText('npm-front: proxy host refused')).toBeInTheDocument();
    expect(screen.getByText('pihole-lan: record locked')).toBeInTheDocument();
  });

  it('publishes once, through the save itself', async () => {
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    expect(savedBodies()).toHaveLength(1);
    expect(apiLog.filter((call) => call.method !== 'get' && /\/push\b/.test(call.path))).toEqual([]);
  });

  it('posts the template fields', async () => {
    const { user } = openCreate();
    await lanRoute(user);
    await user.type(screen.getByRole('textbox', { name: 'expose.template.name_label' }), 'LAN media');
    await user.click(screen.getByRole('button', { name: 'expose.template.save' }));
    await waitFor(() => expect(callsTo('post', '/templates')).toHaveLength(1));
    expect(callsTo('post', '/templates')[0].body).toEqual({
      name: 'LAN media',
      description: '',
      forward_scheme: 'http',
      target_port: 80,
      websocket: false,
      expose_mode: 'proxy_dns',
      proxy_provider_id: 7,
      dns_provider_id: 3,
      tunnel_provider_id: null,
      public_target_mode: 'manual',
      domain: 'example.com',
      dns_ip: '10.0.0.30',
      tag_ids: [],
      environment_ids: [],
      icon_url: '',
    });
  });

  it('shows the field error of a 422 rather than "the checks failed"', async () => {
    failWith('post', '/services/preflight', 422, {
      detail: [{ loc: ['body', 'subdomain'], msg: 'refused', type: 'value_error' }],
    });
    const { user } = openCreate();
    await lanRoute(user);
    await clickContinue(user);
    expect(await screen.findByText('expose.preflight.rejected')).toBeInTheDocument();
    expect(screen.queryByText('expose.preflight.failed')).toBeNull();
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull();
  });

  it('splits a pasted URL', async () => {
    const { user } = openCreate();
    await user.click(field.target());
    await user.paste('https://grafana.example.com:3000/login');
    expect(field.target()).toHaveValue('grafana.example.com');
    expect(field.port()).toHaveValue('3000');
    expect(selectValue(field.scheme())).toBe('https');
  });

  it('publishes the service address on a LAN resolver', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '192.168.1.20');
    await pickOption(field.primaryDns, 'pihole-lan');
    await chooseMode(user, 'dns_only');
    expect(await continueToReview(user)).toMatchObject({
      dns_ip: '192.168.1.20',
      dns_provider_id: 3,
      proxy_provider_id: null,
    });
  });

  it('asks again for another proxy', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(screen.getByRole('switch', { name: 'expose.field.auto_update_dns' }));
    await waitFor(() => expect(suggestUrls()).toContain('/services/public-target/suggest?proxy_provider_id=7'));
    await pickOption(field.primaryProxy, 'npm-back');
    await waitFor(() => expect(suggestUrls()).toContain('/services/public-target/suggest?proxy_provider_id=6'));
  });

  it('drops the policies on a change of tunnel', async () => {
    world.tunnelAccess = twoTunnels();
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    await user.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    await pickOption(field.tunnelProvider, 'cf-tunnel-b');
    expect(await screen.findByRole('checkbox', { name: /Ops/ })).not.toBeChecked();
    await continueToReview(user);
    expect(screen.queryByText('expose.access.review')).toBeNull();
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    expect(accessCalls()).toEqual([]);
  });

  it('drops them when leaving the tunnel', async () => {
    world.tunnelAccess = twoTunnels();
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    await user.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    await chooseMode(user, 'dns_proxy');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    expect(await screen.findByRole('checkbox', { name: /Staff/ })).not.toBeChecked();
    await continueToReview(user);
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    expect(accessCalls()).toEqual([]);
  });

  it('asks for no application on a route that is not a tunnel', async () => {
    world.tunnelAccess = twoTunnels();
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    expect(accessCalls()).toEqual([]);
  });

  it('protects a tunnel route with the policies ticked, after the save', async () => {
    world.tunnelAccess = twoTunnels();
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    await user.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    await continueToReview(user);
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    expect(accessCalls().map((call) => call.body)).toEqual([
      { hostname: 'jellyfin.example.com', policy_ids: ['pol-staff'] },
    ]);
    const save = apiLog.indexOf(callsTo('post', '/services')[0]);
    expect(apiLog.indexOf(accessCalls()[0])).toBeGreaterThan(save);
  });

  it('never offers the primary as an extra', async () => {
    const { user } = openCreate();
    await lanRoute(user);
    const proxies = screen.getByRole('group', { name: 'expose.field.extra_proxies' });
    const dns = screen.getByRole('group', { name: 'expose.field.extra_dns' });
    expect(within(proxies).queryByRole('checkbox', { name: 'npm-front' })).toBeNull();
    expect(within(dns).queryByRole('checkbox', { name: 'pihole-lan' })).toBeNull();
    await user.click(within(proxies).getByRole('checkbox', { name: 'npm-back' }));
    await user.click(within(dns).getByRole('checkbox', { name: 'adguard-lan' }));
    await pickOption(field.primaryProxy, 'npm-back');
    await pickOption(field.primaryDns, 'adguard-lan');
    expect(within(proxies).queryByRole('checkbox', { name: 'npm-back' })).toBeNull();
    expect(within(dns).queryByRole('checkbox', { name: 'adguard-lan' })).toBeNull();
    expect(await continueToReview(user)).toMatchObject({
      proxy_provider_id: 6,
      dns_provider_id: 4,
      extra_proxy_provider_ids: [],
      extra_dns_provider_ids: [],
    });
  });

  it('offers no dry run on a new service', async () => {
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    expect(await screen.findByRole('button', { name: SAVE })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'expose.dry_run.run' })).toBeNull();
  });

  it('withholds the save behind a blocking check', async () => {
    world.preflight = (body: PreflightRequest) => ({
      ok: false,
      public_host: `${body.subdomain}.${body.domain}`,
      checks: [{ name: 'public_host_conflict', ok: false, blocking: true, detail: 'taken' }],
      summary: { blocking_failures: 1, warnings: 0, total: 1 },
    });
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    expect(await screen.findByText('expose.preflight.blocked_title')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: SAVE })).toBeDisabled();
  });

  it('says the domains could not be read', async () => {
    failWith('get', '/domains', 500);
    openCreate();
    expect(await screen.findByText('expose.field.domain_hint_unavailable')).toBeInTheDocument();
  });

  it('says the tags could not be read', async () => {
    failWith('get', '/tags', 500);
    openCreate();
    expect(await screen.findByText('ui.error.list_unavailable')).toBeInTheDocument();
    expect(screen.getAllByText('ui.error.list_unavailable')).toHaveLength(1);
    expect(screen.getByText(/expose\.field\.environments_empty/)).toBeInTheDocument();
  });

  it('says the environments could not be read', async () => {
    failWith('get', '/environments', 500);
    openCreate();
    expect(await screen.findByText('ui.error.list_unavailable')).toBeInTheDocument();
    expect(screen.getAllByText('ui.error.list_unavailable')).toHaveLength(1);
    expect(screen.getByText(/expose\.field\.tags_empty/)).toBeInTheDocument();
  });

  it('brings the error into view', async () => {
    failWith('post', '/services/preflight', 500);
    const { user } = openCreate();
    await lanRoute(user);
    await clickContinue(user);
    const scroll = vi.mocked(Element.prototype.scrollIntoView);
    await waitFor(() =>
      expect(scroll.mock.contexts.some((element) => within(element as HTMLElement).queryByRole('alert'))).toBe(true),
    );
  });

  it('refreshes the lists a save changes', async () => {
    const { user, queryClient } = openCreate();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    await lanRoute(user);
    await continueToReview(user);
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toEqual(expect.arrayContaining([['services'], ['health'], ['logs']]));
    expect(keys).not.toContainEqual(['providers-tunnel-access']);
  });

  it('refreshes the Access tab too when policies went out', async () => {
    world.tunnelAccess = twoTunnels();
    const { user, queryClient } = openCreate();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    await user.click(await screen.findByRole('checkbox', { name: /Staff/ }));
    await continueToReview(user);
    await saveFromReview(user);
    await screen.findByText('expose.done.created_title');
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toContainEqual(['providers-tunnel-access']);
  });
});

describe('defects the create form had', () => {
  // ux-audit.md § 1.1: each keystroke rewrote the tunnel hostname, until one of them left a
  // name that no longer looked derived, and there it stayed.
  it('follows the address when the subdomain is erased and retyped', async () => {
    const { user } = openCreate();
    await typeDomain(user, 'example.com');
    await chooseMode(user, 'tunnel');
    await pickOption(field.tunnelProvider, 'cf-tunnel');
    await user.type(field.subdomain(), '{backspace}'.repeat(initialForm.subdomain.length) + 'jellyfin');
    await user.type(field.target(), '10.0.0.20');
    expect((await continueToReview(user)).tunnel_hostname).toBe('jellyfin.example.com');
  });

  // § 1.2: behind a proxy, a LAN record points at the proxy, whose address the form knows.
  it('publishes a LAN record at the proxy it goes through', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'pihole-lan');
    await clickContinue(user);
    expect(screen.queryByText('expose.validation.dns_target_local_required')).toBeNull();
    await waitFor(() => expect(preflightBodies()).toHaveLength(1));
    expect(preflightBodies()[0].dns_ip).toBe('10.0.0.30');
  });

  // § 1.2: the proxy's address was copied into the field once, then outlived the proxy.
  it('follows the proxy when it changes', async () => {
    const { user } = openCreate();
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryDns, 'pihole-lan');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryProxy, 'npm-back');
    expect(field.dnsTarget()).toHaveValue('');
    expect((await continueToReview(user)).dns_ip).toBe('10.0.0.31');
  });

  // § 1.10: the field was parsed on every keystroke, so "http://g" became the host "g".
  it('leaves a half-typed URL alone', async () => {
    const { user } = openCreate();
    await user.type(field.target(), 'http://grafana');
    expect.soft(field.target()).toHaveValue('http://grafana');
    await user.clear(field.target());
    await user.type(field.target(), 'http://1');
    expect.soft(field.target()).toHaveValue('http://1');
  });

  // § 1.10: a URL typed whole is split when the field is left, as a pasted one is. Left by a
  // click on the next field, not by Tab: jsdom has no layout, so `offsetParent` is null for
  // every element, and the modal's focus trap (`useModalDialog`) then counts only the focused
  // field as tabbable and keeps Tab on it.
  it('splits a typed URL once the field is left', async () => {
    const { user } = openCreate();
    await user.type(field.target(), 'http://grafana.example.com:3000');
    await user.click(field.port());
    expect(field.target()).toHaveValue('grafana.example.com');
    expect(field.port()).toHaveValue('3000');
    expect(selectValue(field.scheme())).toBe('http');
  });

  // § 1.11: "Detect" read the suggestion before asking for it again.
  it('fills the address Detect finds', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(screen.getByRole('button', { name: 'expose.detect' }));
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.40'));
  });

  it('fills the fresh answer of Detect, not the one before', async () => {
    const { user } = openCreate();
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(screen.getByRole('switch', { name: 'expose.field.auto_update_dns' }));
    expect(await screen.findByText('10.0.0.40')).toBeInTheDocument();
    world.suggestion = '10.0.0.41';
    await user.click(screen.getByRole('button', { name: 'expose.detect' }));
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.41'));
  });

  // § 1.8: a refused save said so in a toast for five seconds, in the opposite corner.
  it('says in the dialog why the save failed', async () => {
    failWith('post', '/services', 409, { detail: 'exists' });
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await saveFromReview(user);
    expect(await screen.findByText('expose.toast.create_failed')).toBeInTheDocument();
  });

  // § 1.6: the browser's own check stopped the first empty field, one at a time, in English.
  it('shows every missing field at once, on the field', async () => {
    const { user } = openCreate();
    await clickContinue(user);
    expect(await screen.findByText('expose.validation.domain.empty')).toBeInTheDocument();
    expect(screen.getByText('expose.validation.target_required')).toBeInTheDocument();
    expect(screen.getByText('expose.validation.provider_required')).toBeInTheDocument();
    expect(field.domain()).toHaveFocus();
    expect(field.domain()).toHaveAttribute('aria-invalid', 'true');
    expect(field.target()).toHaveAttribute('aria-invalid', 'true');
    expect(preflightBodies()).toHaveLength(0);
  });

  // § 1.7: every way to set up what the form lacks left the form, and the form behind it.
  it('opens the setup pages in a new tab', async () => {
    world.providers = [];
    world.domains = [];
    openCreate();
    const wanted = ['/providers', '/settings?tab=dns', '/settings?tab=tags', '/settings?tab=environments'];
    await waitFor(() => {
      const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
      expect(hrefs).toEqual(expect.arrayContaining(wanted));
    });
    const links = screen.getAllByRole('link').filter((link) => wanted.includes(link.getAttribute('href') ?? ''));
    expect(links).toHaveLength(5);
    for (const link of links) {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link.getAttribute('rel') ?? '').toMatch(/\bnoopener\b/);
    }
  });

  // § 1.7: a tab that opens with no word said leaves a screen reader user on a page they did
  // not see coming; each of those links says so in words the screen does not show.
  it('tells a screen reader that a setup link opens a new tab', async () => {
    world.providers = [];
    world.domains = [];
    openCreate();
    const wanted = ['/providers', '/settings?tab=dns', '/settings?tab=tags', '/settings?tab=environments'];
    await waitFor(() => {
      const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
      expect(hrefs).toEqual(expect.arrayContaining(wanted));
    });
    const links = screen.getAllByRole('link').filter((link) => wanted.includes(link.getAttribute('href') ?? ''));
    expect(links).toHaveLength(5);
    for (const link of links) {
      expect(link).toHaveAccessibleName(/ui\.opens_in_new_tab$/);
      // jsdom trims the text of each element it reads a name from, so the space that keeps
      // the two phrases apart is read off the hidden text itself.
      const hidden = within(link).getByText('ui.opens_in_new_tab');
      expect(hidden).toHaveClass('sr-only');
      expect(hidden.textContent).toBe(' ui.opens_in_new_tab');
    }
  });

  // § 1.7: back from that tab, the lists say what was just added there.
  it('reads every list a setup link feeds again when the window gets focus back', async () => {
    openCreate({ queryClient: makeEagerQueryClient() });
    await waitFor(() => expect(callsTo('get', '/providers')).toHaveLength(1));
    await waitFor(() => expect(callsTo('get', '/domains')).toHaveLength(1));
    await waitFor(() => expect(callsTo('get', '/tags')).toHaveLength(1));
    await waitFor(() => expect(callsTo('get', '/environments')).toHaveLength(1));
    refocusWindow();
    await waitFor(() => expect(callsTo('get', '/providers').length).toBeGreaterThan(1));
    await waitFor(() => expect(callsTo('get', '/domains').length).toBeGreaterThan(1));
    await waitFor(() => expect(callsTo('get', '/tags').length).toBeGreaterThan(1));
    await waitFor(() => expect(callsTo('get', '/environments').length).toBeGreaterThan(1));
  });
});

// The automatic public target is read off the route's DNS provider: its zone is public or not,
// and only a public zone uses what the lookup answers. With that provider gone from the list,
// "Continue" was refused with the proxy's fault (`no_auto_target`), which sent the operator to
// look at the proxy; on an earlier form, the lookup kept from before was sent instead. Of the
// four states ux-audit.md § 3.3 reads on the line, none says the provider itself is missing.
describe('refusing the automatic public target with no DNS provider to read', () => {
  // Deleted in another tab: back here, the integrations are read again without it.
  it('names the DNS provider deleted in another tab, not the proxy', async () => {
    const { user } = openCreate({ queryClient: makeEagerQueryClient() });
    await nameRoute(user);
    await user.type(field.target(), '10.0.0.20');
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await user.click(screen.getByRole('switch', { name: 'expose.field.auto_update_dns' }));
    expect(await screen.findByText('10.0.0.40')).toBeInTheDocument();

    const reads = callsTo('get', '/providers').length;
    world.providers = world.providers.filter((provider) => provider.id !== integrations.cfEdge.id);
    refocusWindow();
    await waitFor(() => expect(callsTo('get', '/providers').length).toBeGreaterThan(reads));
    await waitFor(() => expect(optionLabels(field.primaryDns())).not.toContain('cf-edge'));

    await clickContinue(user);
    expect(await screen.findByText('expose.validation.auto_target_dns_unknown')).toBeInTheDocument();
    expect(screen.queryByText('expose.validation.no_auto_target')).toBeNull();
    expect(preflightBodies()).toHaveLength(0);
  });
});
