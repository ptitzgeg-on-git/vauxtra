/**
 * An answer that comes after the dialog that asked for it was closed. The callbacks of a
 * request run whatever happened since: the dialog closed and left mounted, or closed and
 * opened again as the same instance, the way `Dashboard` keeps it (`close` and `reopen`
 * here), or unmounted, the way `Services` drops it. Each request carries the opening it was
 * started in. A read whose opening is gone is dropped (the checks, the dry run, the lookup
 * "Detect" waits for). A write whose opening is gone is said in a toast alone (the save, the
 * labels, the template; ux-audit.md § 3.7), and the form on screen stays as it is.
 *
 * The checks can also be answered after the form that asked for them has changed, since it
 * stays open to edits while they run. The review then shows, and "Create" saves and protects,
 * the body and the Access policies they were asked about, kept as one pair from the click on
 * Continue.
 *
 * Each test was red on the code it was written against, save the two marked "Guard". One
 * pins down what such a fix has to keep: the answer of the opening on screen. The other holds
 * a count the review already took from the pair, which no other test told apart from the
 * form's. Its own file because it replaces `react-hot-toast` and `useT`, as the toast tests
 * do: `t()` writes its parameters out, so the host a sentence names can be read.
 */
import { act, screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Tag } from '@/types/api';
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
  openCreate,
  openEdit,
  pickOption,
  preflightBodies,
  savedBodies,
  savedService,
  tunnelAccessItem,
  typeDomain,
  world,
} from './exposeModalHarness';

const { toastMock, fakeT } = vi.hoisted(() => ({
  toastMock: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
  fakeT: (key: string, params?: Record<string, unknown>) =>
    params
      ? `${key}(${Object.entries(params)
          .map(([name, value]) => `${name}=${String(value)}`)
          .join(', ')})`
      : key,
}));

vi.mock('react-hot-toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-hot-toast')>()),
  default: toastMock,
  toast: toastMock,
}));

vi.mock('@/i18n', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/i18n')>()),
  useT: () => fakeT,
}));

installExposeApi();

beforeEach(() => {
  toastMock.mockClear();
  toastMock.success.mockClear();
  toastMock.error.mockClear();
});

const SAVE = 'expose.create_route';
const DRY_RUN = '/services/42/push/dry-run';
const SUGGEST = '/services/public-target/suggest';
const PROD: Tag = { id: 1, name: 'prod', color: '' };
const MEDIA: Tag = { id: 2, name: 'media', color: '' };

const accessCalls = () => callsTo('post', /^\/providers\/\d+\/access\/apps$/);

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

async function lanRoute(user: UserEvent): Promise<void> {
  await user.clear(field.subdomain());
  await user.type(field.subdomain(), 'jellyfin');
  await typeDomain(user, 'example.com');
  await user.type(field.target(), '10.0.0.20');
  await pickOption(field.primaryProxy, 'npm-front');
  await pickOption(field.primaryDns, 'pihole-lan');
  await user.type(field.dnsTarget(), '10.0.0.30');
}

/** Both tunnels, each offering a policy of its own. */
const twoTunnels = () => ({
  total: 2,
  items: [
    tunnelAccessItem(integrations.cfTunnel, { id: 'pol-staff', name: 'Staff' }),
    tunnelAccessItem(integrations.cfTunnelB, { id: 'pol-ops', name: 'Ops' }),
  ],
});

/** Both tunnels, `cf-tunnel` offering a second policy, Family, to tick without leaving it. */
const twoPoliciesOnCfTunnel = () => {
  const access = twoTunnels();
  const [staff] = access.items[0].policies;
  access.items[0] = { ...access.items[0], policies: [staff, { ...staff, id: 'pol-family', name: 'Family' }] };
  return access;
};

/**
 * A tunnel route behind `cf-tunnel` with its Access policy ticked: what the checks of a closed
 * dialog asked about. `cf-tunnel-b` offers another, for a form switched to it while they run,
 * and `access` replaces the tunnels on offer.
 */
async function protectedTunnelRoute(user: UserEvent, access = twoTunnels()): Promise<void> {
  world.tunnelAccess = access;
  await user.clear(field.subdomain());
  await user.type(field.subdomain(), 'jellyfin');
  await typeDomain(user, 'example.com');
  await user.type(field.target(), '10.0.0.20');
  await chooseMode(user, 'tunnel');
  await pickOption(field.tunnelProvider, 'cf-tunnel');
  await user.click(await screen.findByRole('checkbox', { name: /Staff/ }));
}

/** A tag, once the list it belongs to has loaded. */
const tagChip = async (name: string) =>
  within(await screen.findByRole('group', { name: 'expose.field.tags' })).findByRole('button', { name });

const templateName = () => screen.getByRole('textbox', { name: 'expose.template.name_label' });

const detectButton = () => screen.getByRole('button', { name: 'expose.detect' });

/** Proxy and public DNS chosen: the DNS target is drawn, with "Detect" beside it. */
async function publicZoneRoute(): Promise<void> {
  await pickOption(field.primaryProxy, 'npm-front');
  await pickOption(field.primaryDns, 'cf-edge');
}

/**
 * Leaves the dialog through the question that guards a form with something in it: from the
 * form by "Cancel", from the review by the dialog's own X, since "Back" is off while the save
 * is out. The parent then closes it, with `close`.
 */
async function discard(user: UserEvent, by: 'common.cancel' | 'ui.modal.close'): Promise<void> {
  await user.click(screen.getByRole('button', { name: by }));
  const question = await screen.findByRole('dialog', { name: 'expose.discard.title' });
  await user.click(within(question).getByRole('button', { name: 'expose.discard.confirm' }));
}

describe('checks answered after the dialog closed', () => {
  // The answer came after the dialog was closed and put the review back on it: opened again,
  // the dialog showed a tunnel route nobody was looking at one click from being published, and
  // without the Access policy ticked for it, since closing had emptied the form that held it.
  it('are dropped, and the dialog opens again on the form', async () => {
    const checks = holdCalls('post', '/services/preflight');
    const { user, close, reopen } = openCreate();
    await protectedTunnelRoute(user);
    await clickContinue(user);
    await waitFor(() => expect(checks.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();

    await checks.release();
    await waitFor(() => expect(preflightBodies()).toHaveLength(1));
    await settle();
    reopen();
    await settle();
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull();
    expect(await screen.findByRole('button', { name: 'expose.continue' })).toBeInTheDocument();
    expect(screen.queryByText(/^expose\.access\.review\(/)).toBeNull();
    expect(savedBodies()).toHaveLength(0);
    expect(accessCalls()).toHaveLength(0);
  });

  // Opened again before the answer: the answer took the new form to the review of the route
  // the closed one had asked about, over what was being typed.
  it('are dropped by the form opened since, which keeps what was typed in it', async () => {
    const checks = holdCalls('post', '/services/preflight');
    const { user, close, reopen } = openCreate();
    await protectedTunnelRoute(user);
    await clickContinue(user);
    await waitFor(() => expect(checks.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();
    reopen();
    await user.clear(field.subdomain());
    await user.type(field.subdomain(), 'grafana');

    await checks.release();
    await waitFor(() => expect(preflightBodies()).toHaveLength(1));
    await settle();
    expect(screen.queryByRole('button', { name: SAVE })).toBeNull();
    expect(field.subdomain()).toHaveValue('grafana');
    expect(savedBodies()).toHaveLength(0);
  });

  // A refusal of checks nobody was waiting for was said in a toast, and in the banner of the
  // next form, which had asked for nothing.
  it('are dropped when refused, with neither a banner nor a toast', async () => {
    failWith('post', '/services/preflight', 500);
    const checks = holdCalls('post', '/services/preflight');
    const { user, close, reopen } = openCreate();
    await lanRoute(user);
    await clickContinue(user);
    await waitFor(() => expect(checks.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();

    await checks.release();
    await waitFor(() => expect(preflightBodies()).toHaveLength(1));
    await settle();
    reopen();
    await settle();
    expect(await screen.findByRole('button', { name: 'expose.continue' })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('common.error.server');
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe('checks answered after the form changed', () => {
  // The review froze the body the checks were asked about, but "Create" read the policies
  // from the form: switched to the other tunnel while they ran, it asked `cf-tunnel` to put
  // the route behind the policy `cf-tunnel-b` offers.
  it('keep "Create" to the policies they were asked with', async () => {
    const checks = holdCalls('post', '/services/preflight');
    const { user } = openCreate();
    await protectedTunnelRoute(user);
    await clickContinue(user);
    await waitFor(() => expect(checks.held).toHaveLength(1));
    await pickOption(field.tunnelProvider, 'cf-tunnel-b');
    await user.click(await screen.findByRole('checkbox', { name: /Ops/ }));

    await checks.release();
    expect(await screen.findByText('expose.access.review(count=1, host=jellyfin.example.com)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(accessCalls()).toHaveLength(1));
    expect(accessCalls()[0]).toMatchObject({
      path: '/providers/9/access/apps',
      body: { hostname: 'jellyfin.example.com', policy_ids: ['pol-staff'] },
    });
  });

  // Guard: the alert counts the policies of the pair, not those of the form. In the test
  // above, the switch of tunnel empties the form's policies and the one ticked after puts
  // their count back at one, so an alert that counted the form's said the same.
  it('keep the alert to the number of policies they were asked with', async () => {
    const checks = holdCalls('post', '/services/preflight');
    const { user } = openCreate();
    await protectedTunnelRoute(user, twoPoliciesOnCfTunnel());
    await clickContinue(user);
    await waitFor(() => expect(checks.held).toHaveLength(1));
    await user.click(screen.getByRole('checkbox', { name: /Family/ }));
    expect(screen.getByRole('checkbox', { name: /Staff/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Family/ })).toBeChecked();

    await checks.release();
    expect(await screen.findByText('expose.access.review(count=1, host=jellyfin.example.com)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(accessCalls()).toHaveLength(1));
    expect(accessCalls()[0].body).toEqual({ hostname: 'jellyfin.example.com', policy_ids: ['pol-staff'] });
  });

  // The review named the hostname the form held once it was shown, and the save protected
  // the one the checks were asked about: a hostname typed while they ran was announced, and
  // another was put behind the policy.
  it('keep the review to the host they were asked about', async () => {
    const checks = holdCalls('post', '/services/preflight');
    const { user } = openCreate();
    await protectedTunnelRoute(user);
    await clickContinue(user);
    await waitFor(() => expect(checks.held).toHaveLength(1));
    await user.clear(field.tunnelHostname());
    await user.type(field.tunnelHostname(), 'media.example.com');

    await checks.release();
    expect(await screen.findByText('expose.access.review(count=1, host=jellyfin.example.com)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(accessCalls()).toHaveLength(1));
    expect(accessCalls()[0].body).toEqual({ hostname: 'jellyfin.example.com', policy_ids: ['pol-staff'] });
  });
});

describe('a save answered after the dialog closed', () => {
  // The answer put the receipt on the closed dialog, and the dialog opened again on it.
  it('is said in a toast, and the dialog opens again on the form', async () => {
    const save = holdCalls('post', '/services');
    const { user, close, reopen } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: SAVE }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    await discard(user, 'ui.modal.close');
    close();

    await save.release();
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith('expose.toast.created(host=jellyfin.example.com)', { duration: 4500 }),
    );
    reopen();
    await settle();
    expect(screen.queryByText('expose.done.created_title')).toBeNull();
    expect(await screen.findByRole('button', { name: 'expose.continue' })).toBeInTheDocument();
  });

  // Opened again before the answer: the receipt replaced the new form, and what was typed in
  // it was gone once the receipt was closed.
  it('is said in a toast, and the form opened since keeps what was typed in it', async () => {
    const save = holdCalls('post', '/services');
    const { user, close, reopen } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: SAVE }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    await discard(user, 'ui.modal.close');
    close();
    reopen();
    await user.clear(field.subdomain());
    await user.type(field.subdomain(), 'grafana');

    await save.release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledTimes(1));
    await settle();
    expect(screen.queryByText('expose.done.created_title')).toBeNull();
    expect(field.subdomain()).toHaveValue('grafana');
  });
});

describe('labels saved after the dialog closed', () => {
  // The answer put the receipt of a labels-only edit on the form opened since.
  it('are said in a toast, and the form opened since stays the form', async () => {
    world.tags = [PROD, MEDIA];
    const patch = holdCalls('patch', '/services/42');
    const { user, close, reopen } = openEdit(savedService({ tags: [PROD] }));
    await user.click(await tagChip('media'));
    await user.click(screen.getByRole('button', { name: 'expose.labels_only.save' }));
    await waitFor(() => expect(patch.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();
    reopen();

    await patch.release();
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith(expect.stringMatching(/^expose\.toast\.labels_updated\(/), {
        duration: 4500,
      }),
    );
    await settle();
    expect(screen.queryByText('expose.done.labels_title')).toBeNull();
    expect(await screen.findByRole('button', { name: 'expose.continue' })).toBeInTheDocument();
  });

  // The refusal was said in the banner of the form opened since, which had saved nothing.
  it('are said refused in a toast alone', async () => {
    world.tags = [PROD, MEDIA];
    failWith('patch', '/services/42', 500);
    const patch = holdCalls('patch', '/services/42');
    const { user, close, reopen } = openEdit(savedService({ tags: [PROD] }));
    await user.click(await tagChip('media'));
    await user.click(screen.getByRole('button', { name: 'expose.labels_only.save' }));
    await waitFor(() => expect(patch.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();
    reopen();

    await patch.release();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('common.error.server', { duration: 5000 }));
    await settle();
    expect(document.body).not.toHaveTextContent('common.error.server');
  });
});

describe('a dry run answered after the dialog closed', () => {
  // The plan of the closed dialog was shown on the review opened since, which never ran one.
  it('is dropped by the review opened since', async () => {
    const plan = holdCalls('post', DRY_RUN);
    const { user, close, reopen } = openEdit(savedService());
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.dry_run.run' }));
    await waitFor(() => expect(plan.held).toHaveLength(1));
    await discard(user, 'ui.modal.close');
    close();
    reopen();
    await continueToReview(user);

    await plan.release();
    await waitFor(() => expect(callsTo('post', DRY_RUN)).toHaveLength(1));
    await settle();
    expect(screen.queryByText('expose.dry_run.no_change')).toBeNull();
    expect(screen.getByRole('button', { name: 'expose.dry_run.run' })).toBeInTheDocument();
  });

  // Its refusal was said in a toast, about a dry run nobody was waiting for.
  it('is dropped when refused, with no toast', async () => {
    failWith('post', DRY_RUN, 500);
    const plan = holdCalls('post', DRY_RUN);
    const { user, close } = openEdit(savedService());
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.dry_run.run' }));
    await waitFor(() => expect(plan.held).toHaveLength(1));
    await discard(user, 'ui.modal.close');
    close();

    await plan.release();
    await waitFor(() => expect(callsTo('post', DRY_RUN)).toHaveLength(1));
    await settle();
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe('a template saved after the dialog closed', () => {
  // The answer emptied the name field of the form opened since, over the name typed there.
  it('is said in a toast, and the name typed since stays', async () => {
    const save = holdCalls('post', '/templates');
    const { user, close, reopen } = openCreate();
    await lanRoute(user);
    await user.type(templateName(), 'LAN media');
    await user.click(screen.getByRole('button', { name: 'expose.template.save' }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();
    reopen();
    await user.type(templateName(), 'LAN media 2');

    await save.release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('expose.template.saved(name=LAN media)'));
    await settle();
    expect(templateName()).toHaveValue('LAN media 2');
  });
});

describe('a lookup answered after the dialog closed', () => {
  // "Detect" kept what the field held at the click, not the opening it was clicked in. Closed
  // and opened again while the lookup ran, the dialog wrote the answer into the form opened
  // since, whose field held what the closed one had held: nothing.
  it('leaves the DNS target of the form opened since as it is', async () => {
    const { user, close, reopen } = openCreate();
    await publicZoneRoute();
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));
    await discard(user, 'common.cancel');
    close();
    reopen();
    await publicZoneRoute();

    world.suggestion = '10.0.0.41';
    await lookup.release();
    await waitFor(() => expect(detectButton()).not.toHaveAttribute('aria-busy', 'true'));
    await settle();
    expect(field.dnsTarget()).toHaveValue('');
  });

  // Guard: in the opening that asked, the answer still fills a field left as it was, with what
  // the lookup answers after the click and not what it held before.
  it('fills the DNS target of the form that asked', async () => {
    const { user } = openCreate();
    await publicZoneRoute();
    const lookup = holdCalls('get', SUGGEST);
    await user.click(detectButton());
    await waitFor(() => expect(lookup.held).toHaveLength(1));

    world.suggestion = '10.0.0.41';
    await lookup.release();
    await waitFor(() => expect(field.dnsTarget()).toHaveValue('10.0.0.41'));
  });
});
