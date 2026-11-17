/**
 * The edit form over `exposeModalHarness`: what a saved route keeps when it goes through the
 * form again, and the defects only an edit shows, a rename (`ux-audit.md` § 1.1), a route
 * saved on a DNS integration of a type the form does not know, and one opened while the list
 * of integrations could not be read.
 */
import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Provider, Service } from '@/types/api';
import {
  callsTo,
  clickContinue,
  continueToReview,
  failWith,
  field,
  installExposeApi,
  integrations,
  makeEagerQueryClient,
  openCreate,
  openEdit,
  pickOption,
  preflightBodies,
  refocusWindow,
  savedBodies,
  savedService,
  world,
} from './exposeModalHarness';

installExposeApi();

const SAVE = 'expose.save_route';

/**
 * An integration whose type neither `/providers/types` nor the floor in `lib/providers.ts`
 * knows, so the form gives it no role: it is in the list, and no DNS select offers it.
 */
const unknownDns: Provider = {
  id: 11,
  name: 'dns-unknown',
  type: 'unknown-dns',
  url: 'http://192.168.1.4',
  username: '',
  enabled: true,
  extra: {},
  created_at: '2026-01-01 09:00:00',
};

/** Saved behind `npm-front`, its public target found automatically through that integration. */
const autoOnUnknownDns: Partial<Service> = {
  dns_provider_id: unknownDns.id,
  public_target_mode: 'auto',
  auto_update_dns: true,
  dns_ip: '',
};

describe('the edit form keeps what works', () => {
  it('keeps a disabled service disabled', async () => {
    const { user } = openEdit(savedService({ enabled: false }));
    expect((await continueToReview(user)).enabled).toBe(false);
    await user.click(await screen.findByRole('button', { name: SAVE }));
    await waitFor(() => expect(savedBodies()).toHaveLength(1));
    expect(callsTo('put', '/services/42')).toHaveLength(1);
    expect(savedBodies()[0].enabled).toBe(false);
  });

  it('keeps the icon it had', async () => {
    const { user } = openEdit(savedService({ icon_url: 'https://example.com/grafana.png' }));
    expect((await continueToReview(user)).icon_url).toBe('https://example.com/grafana.png');
    await user.click(await screen.findByRole('button', { name: SAVE }));
    await waitFor(() => expect(savedBodies()).toHaveLength(1));
    expect(savedBodies()[0].icon_url).toBe('https://example.com/grafana.png');
  });

  it('sends its own id to the checks', async () => {
    const { user } = openEdit(savedService());
    expect((await continueToReview(user)).service_id).toBe(42);
  });

  // The plan is computed from what is saved, not from the form: no body goes with it.
  it('runs the dry run on the saved route, with no body', async () => {
    const { user } = openEdit(savedService());
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.dry_run.run' }));
    expect(await screen.findByText('expose.dry_run.no_change')).toBeInTheDocument();
    const calls = callsTo('post', '/services/42/push/dry-run');
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toBeUndefined();
  });
});

describe('defects the edit form had', () => {
  // ux-audit.md § 1.1: a tunnel route renamed one key at a time kept the hostname of the
  // first letter left standing.
  it('follows a renamed tunnel route', async () => {
    const tunnel = savedService({
      expose_mode: 'tunnel',
      tunnel_provider_id: 9,
      tunnel_hostname: 'grafana.example.com',
      proxy_provider_id: null,
      dns_provider_id: null,
      dns_ip: '',
    });
    const { user } = openEdit(tunnel);
    await user.type(field.subdomain(), '{backspace}'.repeat('grafana'.length) + 'jellyfin');
    expect((await continueToReview(user)).tunnel_hostname).toBe('jellyfin.example.com');
  });
});

// The automatic public target is read off the route's DNS provider, and only a public zone
// uses what the lookup answers. On an integration the form gives no role, there is no zone to
// read: "Continue" was refused with the proxy's fault (`no_auto_target`), which sent the
// operator to look at a proxy that had done nothing wrong.
describe('refusing the automatic public target on a DNS provider of an unknown type', () => {
  it('names the DNS provider, not the proxy', async () => {
    world.providers = [...world.providers, unknownDns];
    const { user } = openEdit(savedService(autoOnUnknownDns));
    await waitFor(() => expect(callsTo('get', '/providers')).toHaveLength(1));

    await clickContinue(user);
    expect(await screen.findByText('expose.validation.auto_target_dns_unknown')).toBeInTheDocument();
    expect(screen.queryByText('expose.validation.no_auto_target')).toBeNull();
    expect(preflightBodies()).toHaveLength(0);
  });

  // A lookup read for the same proxy by an earlier form is still in the cache when this one
  // opens. On an earlier form, that answer was sent as the record's address, into a zone
  // nobody had said was public; it is not used, and the refusal names the DNS provider.
  it('names it so with a lookup for the same proxy still in the cache', async () => {
    const queryClient = makeEagerQueryClient();
    const first = openCreate({ queryClient });
    await pickOption(field.primaryProxy, 'npm-front');
    await pickOption(field.primaryDns, 'cf-edge');
    await first.user.click(screen.getByRole('switch', { name: 'expose.field.auto_update_dns' }));
    expect(await screen.findByText('10.0.0.40')).toBeInTheDocument();
    first.unmount();

    world.providers = [...world.providers, unknownDns];
    const reads = callsTo('get', '/providers').length;
    const { user } = openEdit(savedService(autoOnUnknownDns), { queryClient });
    await waitFor(() => expect(callsTo('get', '/providers').length).toBeGreaterThan(reads));

    await clickContinue(user);
    expect(await screen.findByText('expose.validation.auto_target_dns_unknown')).toBeInTheDocument();
    expect(screen.queryByText('expose.validation.no_auto_target')).toBeNull();
    expect(preflightBodies()).toHaveLength(0);
  });
});

/** Saved on `cf-edge`, its public target found automatically. */
const autoOnPublicDns: Partial<Service> = {
  dns_provider_id: integrations.cfEdge.id,
  public_target_mode: 'auto',
  auto_update_dns: true,
  dns_ip: '',
};

// With the list of integrations unread, the route's DNS provider is in no list, and the
// refusal said it was missing or of an unknown type: a reason that sent the operator to check
// the route, when it was the list that could not be read.
describe('refusing the automatic public target while the provider list is unread', () => {
  it('says the list could not be read, not that the provider is missing', async () => {
    failWith('get', '/providers', 500);
    const { user } = openEdit(savedService(autoOnPublicDns));
    await waitFor(() => expect(callsTo('get', '/providers')).toHaveLength(1));

    await clickContinue(user);
    expect(await screen.findByText('expose.validation.auto_target_dns_unread')).toBeInTheDocument();
    expect(screen.queryByText('expose.validation.auto_target_dns_unknown')).toBeNull();
    expect(preflightBodies()).toHaveLength(0);
  });

  // The rows an earlier read left in the cache do not prove the provider is missing either:
  // it may have been added since, and the read that failed is the one that would have said so.
  it('says so with the rows of an earlier read still in the cache', async () => {
    world.providers = world.providers.filter((provider) => provider.id !== integrations.cfEdge.id);
    const queryClient = makeEagerQueryClient();
    const { user } = openEdit(savedService(autoOnPublicDns), { queryClient });
    await waitFor(() => expect(queryClient.getQueryState(['providers'])?.status).toBe('success'));

    failWith('get', '/providers', 500);
    const reads = callsTo('get', '/providers').length;
    refocusWindow();
    await waitFor(() => expect(callsTo('get', '/providers').length).toBeGreaterThan(reads));
    await waitFor(() => expect(queryClient.getQueryState(['providers'])?.status).toBe('error'));

    await clickContinue(user);
    expect(await screen.findByText('expose.validation.auto_target_dns_unread')).toBeInTheDocument();
    expect(screen.queryByText('expose.validation.auto_target_dns_unknown')).toBeNull();
    expect(preflightBodies()).toHaveLength(0);
  });
});
