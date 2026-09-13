/**
 * Which half of the delete dialog is shown, and what the checkbox is attached to.
 *
 * A provider is named by services and by service templates, and the two lose different
 * things. A service has a hostname that can go dark and a record left live on the server;
 * a template has neither -- it publishes nothing, so there is no record to withdraw. The
 * API answered only the first half for a long time, and a provider that only a template
 * named was deleted with no question asked at all.
 *
 * The claim here is structural, so it is asserted on keys rather than on English. The test
 * renders without `I18nProvider` on purpose (see `src/test/render.tsx`): `t()` gives back
 * the key, and rewording a sentence does not turn this red. What must not change is which
 * sentences exist:
 *
 *     templates only   the template block, NO service block, NO withdraw checkbox
 *     services only    the service block and the checkbox, NO template block
 *     both             both blocks, and the checkbox
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
});
