/**
 * Which half of the delete dialog is shown, and what the checkbox is attached to.
 *
 * A provider is named by services and by service templates, and the two lose different
 * things. A service has a hostname that can go dark and a record left live on the server;
 * a template has neither -- it publishes nothing, so there is no record to withdraw. The
 * API answered only the first half for a long time, and a provider that only a template
 * named was deleted with no question asked at all.
 *
 * The third: a notification webhook can be scoped to a provider through a column the
 * schema never declared, so nothing cascades and nothing blanks it. It is the only one of
 * the three the deletion leaves untouched -- same name, same scope, same switch -- which
 * is why its block is the only one that says a row was left switched on.
 *
 * The claim here is structural, so it is asserted on keys rather than on English. The test
 * renders without `I18nProvider` on purpose (see `src/test/render.tsx`): `t()` gives back
 * the key, and rewording a sentence does not turn this red. What must not change is which
 * sentences exist:
 *
 *     templates only   the template block, NO service block, NO withdraw checkbox
 *     webhooks only    the webhook block, NO service block, NO withdraw checkbox
 *     services only    the service block and the checkbox, NO other block
 *     all three        three blocks, and the checkbox
 *
 * The checkbox is the reason the first row matters. It maps to `?withdraw=true`, which asks
 * the provider to take the records down; offering it for a conflict with no records in it
 * is offering to undo something that never happened.
 */

import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import {
  WITHDRAW_BY_DEFAULT,
  providerConflictTitleKey,
  type WithdrawChoice,
} from '@/hooks/useProviderMutations';
import type { ProviderDeleteConflict } from '@/types/api';
import { ProviderDeleteConflictBody } from './ProviderDeleteConflictBody';

const NAME = 'Technitium DNS';

const SERVICE = { id: 1, fqdn: 'git.example.test', roles: ['dns'], still_published: false };
const TEMPLATE = { id: 7, name: 'standard', roles: ['proxy'] };
const WEBHOOK = { id: 3, name: 'on-call', enabled: true };

function show(detail: Partial<ProviderDeleteConflict>) {
  const choiceRef: WithdrawChoice = { current: WITHDRAW_BY_DEFAULT };
  renderWithProviders(
    <ProviderDeleteConflictBody
      name={NAME}
      detail={{ message: 'ignored, the dialog renders this body instead', services: [], ...detail }}
      choiceRef={choiceRef}
    />,
  );
  return choiceRef;
}

/** `t()` returns the key here, so a block is present iff its intro key is on screen. */
const serviceBlock = () => screen.queryByText('providers.delete.deps_intro');
const templateBlock = () => screen.queryByText('providers.delete.tpl_intro');
const hookBlock = () => screen.queryByText('providers.delete.hook_intro');
const withdrawBox = () => screen.queryByRole('checkbox');

describe('ProviderDeleteConflictBody', () => {
  it('offers no withdrawal when only templates name the provider', () => {
    show({ services: [], templates: [TEMPLATE] });

    expect(templateBlock()).toBeInTheDocument();
    // Nothing is published from a template, so every sentence in the service half -- the
    // hostname going dark, the record left behind, the box that takes it down -- is about
    // something that is not happening.
    expect(serviceBlock()).toBeNull();
    expect(withdrawBox()).toBeNull();
    expect(screen.getByText('providers.delete.tpl_effect')).toBeInTheDocument();
  });

  it('says nothing about templates when none name the provider', () => {
    show({ services: [SERVICE] });

    expect(serviceBlock()).toBeInTheDocument();
    expect(withdrawBox()).toBeInTheDocument();
    expect(templateBlock()).toBeNull();
  });

  it('shows both halves when both name the provider', () => {
    show({ services: [SERVICE], templates: [TEMPLATE] });

    expect(serviceBlock()).toBeInTheDocument();
    expect(templateBlock()).toBeInTheDocument();
    expect(withdrawBox()).toBeInTheDocument();
    // The two lists are separate, and each row names its own subject.
    expect(screen.getByText(SERVICE.fqdn)).toBeInTheDocument();
    expect(screen.getByText(TEMPLATE.name)).toBeInTheDocument();
  });

  it('tolerates an instance that does not send the template half yet', () => {
    // `templates` is absent from an older backend's 409. The body must not read `.length`
    // off `undefined` and take the whole dialog down with it.
    show({ services: [SERVICE] });
    expect(serviceBlock()).toBeInTheDocument();
  });

  it('offers no withdrawal when only a webhook is scoped to the provider', () => {
    show({ services: [], webhooks: [WEBHOOK] });

    expect(hookBlock()).toBeInTheDocument();
    // A webhook publishes nothing either, so the whole service half is about something that
    // is not happening here too, and there is no record anywhere to take down.
    expect(serviceBlock()).toBeNull();
    expect(templateBlock()).toBeNull();
    expect(withdrawBox()).toBeNull();
    expect(screen.getByText('providers.delete.hook_effect')).toBeInTheDocument();
  });

  it('says nothing about webhooks when none are scoped to the provider', () => {
    show({ services: [SERVICE], templates: [TEMPLATE] });

    expect(hookBlock()).toBeNull();
  });

  it('says a webhook left switched on is still switched on', () => {
    show({ services: [], webhooks: [WEBHOOK] });

    // The sentence nobody would guess: nothing in the deletion turns the rule off. It is the
    // one dependent of the three that the delete leaves exactly as it found it, which is why
    // it is the only paragraph that has to say so out loud.
    expect(screen.getByText('providers.delete.hook_armed')).toBeInTheDocument();
    expect(screen.getByText('providers.delete.hook_armed_tag')).toBeInTheDocument();
    expect(screen.getByText(WEBHOOK.name)).toBeInTheDocument();
  });

  it('does not call a webhook armed when it is already switched off', () => {
    show({ services: [], webhooks: [{ ...WEBHOOK, enabled: false }] });

    // Still listed, because it is still pointed at a provider that is going. But a rule that
    // was already off loses nothing to the deletion, and telling an operator to go and turn
    // something off that is already off is a false alarm.
    expect(hookBlock()).toBeInTheDocument();
    expect(screen.getByText('providers.delete.hook_off_tag')).toBeInTheDocument();
    expect(screen.queryByText('providers.delete.hook_armed')).toBeNull();
  });

  it('shows all three blocks when all three name the provider', () => {
    show({ services: [SERVICE], templates: [TEMPLATE], webhooks: [WEBHOOK] });

    expect(serviceBlock()).toBeInTheDocument();
    expect(templateBlock()).toBeInTheDocument();
    expect(hookBlock()).toBeInTheDocument();
    expect(withdrawBox()).toBeInTheDocument();
  });

  it('tolerates an instance that does not send the webhook half yet', () => {
    // Same shape as the template half above: `webhooks` is absent from an older backend's
    // 409, and reading `.length` off `undefined` takes the whole dialog down with it.
    show({ services: [SERVICE], templates: [TEMPLATE] });
    expect(serviceBlock()).toBeInTheDocument();
  });

  it('writes the box straight into the ref the request reads', async () => {
    const choiceRef = show({ services: [SERVICE] });
    expect(choiceRef.current).toBe(WITHDRAW_BY_DEFAULT);

    await userEvent.click(screen.getByRole('checkbox'));

    // Unticked means "leave the records live", and the request has to carry that.
    expect(choiceRef.current).toBe(false);
  });
});

describe('providerConflictTitleKey', () => {
  // "Services still depend on it" is simply untrue over a list of templates, and the two
  // call sites used to hard-code it.
  it('names the services only when there are services', () => {
    expect(providerConflictTitleKey({ message: '', services: [SERVICE] })).toBe(
      'providers.delete.deps_title',
    );
  });

  it('names the templates when the services list is empty', () => {
    expect(providerConflictTitleKey({ message: '', services: [], templates: [TEMPLATE] })).toBe(
      'providers.delete.tpl_title',
    );
  });

  it('names the webhooks when nothing else is in the conflict', () => {
    // The third branch, and the one a 409 can now be raised entirely on its own.
    expect(
      providerConflictTitleKey({ message: '', services: [], templates: [], webhooks: [WEBHOOK] }),
    ).toBe('providers.delete.hook_title');
  });
});
