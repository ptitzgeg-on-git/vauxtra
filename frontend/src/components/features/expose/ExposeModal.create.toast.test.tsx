/**
 * What the create form says in a toast. Its own file because it replaces `react-hot-toast`
 * and `useT`: the toast is read from its call, and `t()` writes its parameters out so that a
 * count passed to a sentence can be checked without any language's wording.
 */
import { screen, waitFor, within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { failWith, field, holdCalls, installExposeApi, openCreate, pickOption, typeDomain, world, continueToReview } from './exposeModalHarness';

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

async function lanRoute(user: UserEvent): Promise<void> {
  await user.clear(field.subdomain());
  await user.type(field.subdomain(), 'jellyfin');
  await typeDomain(user, 'example.com');
  await user.type(field.target(), '10.0.0.20');
  await pickOption(field.primaryProxy, 'npm-front');
  await pickOption(field.primaryDns, 'pihole-lan');
  await user.type(field.dnsTarget(), '10.0.0.30');
}

/** The server answers a saved template without the name it was saved under. */
function answerTemplatesWithoutName(): void {
  const post = vi.mocked(api.post);
  const answerNow = post.getMockImplementation() as unknown as
    | ((url: string, ...rest: unknown[]) => Promise<unknown>)
    | undefined;
  if (!answerNow) throw new Error('api.post is not spied on: installExposeApi() comes first');
  post.mockImplementation((async (url: string, ...rest: unknown[]) => {
    const answer = await answerNow(url, ...rest);
    return url === '/templates' ? { id: 99 } : answer;
  }) as unknown as typeof api.post);
}

describe('the create form toasts', () => {
  it('counts the errors past the second', async () => {
    world.saveResult = {
      ok: false,
      errors: ['npm-front: refused', 'pihole-lan: locked', 'adguard-lan: locked', 'cf-edge: locked'],
    };
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.create_route' }));
    await screen.findByText('expose.done.created_title');
    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(toastMock.mock.calls[0][0]).toBe(
      'expose.toast.created_warnings(errors=npm-front: refused; pihole-lan: locked, more=expose.toast.more(count=2))',
    );
  });

  it('says the name is taken on 409', async () => {
    failWith('post', '/templates', 409, { detail: 'name exists' });
    const { user } = openCreate();
    await lanRoute(user);
    await user.type(screen.getByRole('textbox', { name: 'expose.template.name_label' }), 'LAN media');
    await user.click(screen.getByRole('button', { name: 'expose.template.save' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('expose.template.name_exists'));
  });

  // Guard: an answer without the name still gets a toast that names the template, the one
  // that was sent, and not what the name field holds by the time the answer comes.
  it('names the template that was sent when the answer leaves the name out', async () => {
    answerTemplatesWithoutName();
    const save = holdCalls('post', '/templates');
    const { user } = openCreate();
    await lanRoute(user);
    const name = screen.getByRole('textbox', { name: 'expose.template.name_label' });
    await user.type(name, 'LAN media');
    await user.click(screen.getByRole('button', { name: 'expose.template.save' }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    await user.type(name, ' 2');

    await save.release();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('expose.template.saved(name=LAN media)'));
    expect(toastMock.success).toHaveBeenCalledTimes(1);
  });
});

// ux-audit.md § 3.7, for a failure too: the dialog says why a save failed under its buttons
// (§ 1.8), so a toast saying it again is for a dialog closed before the answer, and only then.
describe('a refused save', () => {
  it('is said in the dialog alone while the dialog is open', async () => {
    failWith('post', '/services', 409, { detail: 'exists' });
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.create_route' }));
    expect(await screen.findByText('expose.toast.create_failed')).toBeInTheDocument();
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  // "Back" while the save is out used to leave the review before the refusal came: it was then
  // said nowhere, and shown later on a review where nothing had been tried yet. "Back" is off
  // until the answer, so the refusal lands on the review that asked, and the next review,
  // checked again from the form, starts without it.
  it('is said once on the review that asked, and not on the next one', async () => {
    failWith('post', '/services', 409, { detail: 'exists' });
    const save = holdCalls('post', '/services');
    const { user } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.create_route' }));
    await waitFor(() => expect(save.held).toHaveLength(1));
    await user.click(screen.getByRole('button', { name: 'common.back' }));
    await save.release();

    expect(await screen.findAllByText('expose.toast.create_failed')).toHaveLength(1);
    expect(toastMock.error).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'common.back' }));
    await continueToReview(user);
    expect(await screen.findByRole('button', { name: 'expose.create_route' })).toBeEnabled();
    expect(screen.queryByText('expose.toast.create_failed')).toBeNull();
  });

  it('is said in a toast when the dialog was closed before the answer, and not in the next form', async () => {
    failWith('post', '/services', 409, { detail: 'exists' });
    const save = holdCalls('post', '/services');
    const { user, close, reopen } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.create_route' }));
    await waitFor(() => expect(save.held).toHaveLength(1));

    // The dialog's own X: "Back" is off while the save is out.
    await user.click(screen.getByRole('button', { name: 'ui.modal.close' }));
    const discard = await screen.findByRole('dialog', { name: 'expose.discard.title' });
    await user.click(within(discard).getByRole('button', { name: 'expose.discard.confirm' }));
    close();

    await save.release();
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith('expose.toast.create_failed', { duration: 5000 }),
    );

    reopen();
    await lanRoute(user);
    await continueToReview(user);
    expect(await screen.findByRole('button', { name: 'expose.create_route' })).toBeEnabled();
    expect(screen.queryByText('expose.toast.create_failed')).toBeNull();
  });

  // Open again by the time the answer comes is not the dialog that asked: the refusal goes to
  // a toast all the same, and the new form is left as clean as it opened.
  it('is said in a toast when the dialog was closed and opened again before the answer', async () => {
    failWith('post', '/services', 409, { detail: 'exists' });
    const save = holdCalls('post', '/services');
    const { user, close, reopen } = openCreate();
    await lanRoute(user);
    await continueToReview(user);
    await user.click(await screen.findByRole('button', { name: 'expose.create_route' }));
    await waitFor(() => expect(save.held).toHaveLength(1));

    // The dialog's own X: "Back" is off while the save is out.
    await user.click(screen.getByRole('button', { name: 'ui.modal.close' }));
    const discard = await screen.findByRole('dialog', { name: 'expose.discard.title' });
    await user.click(within(discard).getByRole('button', { name: 'expose.discard.confirm' }));
    close();
    reopen();

    await save.release();
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith('expose.toast.create_failed', { duration: 5000 }),
    );

    await lanRoute(user);
    await continueToReview(user);
    expect(await screen.findByRole('button', { name: 'expose.create_route' })).toBeEnabled();
    expect(screen.queryByText('expose.toast.create_failed')).toBeNull();
  });
});
