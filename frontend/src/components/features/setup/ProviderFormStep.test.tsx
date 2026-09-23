/**
 * Adding an integration inside the first-run wizard, as 1.5.2 shipped it.
 *
 * The step shares its type picker, its wording and its guided steps with the Integrations
 * dialog, and it disagreed with that dialog on most rules the two had in common:
 *
 * - Picking another type kept the name and the address typed for the one before, so an AdGuard
 *   Home could be validated against the NPM address under an integration still called
 *   "Nginx Proxy Manager". The dialog already re-seeded both in 1.5.2 (`seedFormForType`).
 * - The name was asked under the last guided step's fields, and first in the expert form.
 * - "Next" read the guided marks, "Validate" the short list the dialog read too, and the expert
 *   form marked nothing: the NPM e-mail was required on one screen and skippable on the other.
 * - Editing a field kept the verdict of the last validation, so "Connect" stayed armed for a
 *   password nothing had tested.
 * - The expert form hid the address field of a hosted API that the dialog lets one override.
 *
 * The harness below holds the state the wizard page holds, so the step is exercised the way
 * it runs. `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import {
  emptyForm,
  type ProviderFormState,
  type ProviderTypeMeta,
  type ProviderValidationResult,
} from '@/components/features/providers/providerConstants';
import { ProviderFormStep } from './ProviderFormStep';

/** Shaped like `GET /api/providers/types`: the steps, their marks, nothing the API does not send. */
const TYPES: Record<string, ProviderTypeMeta> = {
  npm: {
    label: 'Nginx Proxy Manager',
    available: true,
    capabilities: { proxy: true },
    placeholder_url: 'http://192.168.1.10:81',
    guided_steps: [
      { title: 'Where it answers', body: 'The admin panel.', fields: [{ key: 'url', label: 'Admin address', input_type: 'url' }] },
      { title: 'The account', body: 'The e-mail you sign in with.', fields: [{ key: 'username', label: 'E-mail' }] },
      { title: 'Its password', body: 'Of that account.', fields: [{ key: 'password', label: 'Password', input_type: 'password' }] },
    ],
  },
  adguard: {
    label: 'AdGuard Home',
    available: true,
    capabilities: { dns: true },
    guided_steps: [
      {
        title: 'Open Settings',
        body: 'The address and the admin account.',
        fields: [
          { key: 'url', label: 'Address', input_type: 'url' },
          { key: 'username', label: 'Account' },
          { key: 'password', label: 'Password', input_type: 'password' },
        ],
      },
    ],
  },
  cloudflare: {
    label: 'Cloudflare',
    available: true,
    capabilities: { dns: true, public_dns: true },
    guided_steps: [
      { title: 'A token', body: 'Scoped to the zone.', fields: [{ key: 'password', label: 'API token', input_type: 'password' }] },
      { title: 'A zone', body: 'Found by the token otherwise.', fields: [{ key: 'username', label: 'Zone ID', optional: true }] },
    ],
  },
};

const NPM_ADDRESS = 'http://127.0.0.1:3081';

function Harness() {
  const [formData, setFormData] = useState<ProviderFormState>(emptyForm);
  const [wizardMode, setWizardMode] = useState<'guided' | 'expert' | null>(null);
  const [guidedStepIndex, setGuidedStepIndex] = useState(0);
  const [validationResult, setValidationResult] = useState<ProviderValidationResult | null>(null);
  return (
    <ProviderFormStep
      formData={formData}
      setFormData={setFormData}
      wizardMode={wizardMode}
      setWizardMode={setWizardMode}
      guidedStepIndex={guidedStepIndex}
      setGuidedStepIndex={setGuidedStepIndex}
      validationResult={validationResult}
      setValidationResult={setValidationResult}
      providerTypes={TYPES}
      onCancel={() => {}}
      // What a passing `POST /providers/validate-draft` leaves behind.
      onValidate={() => setValidationResult({ ok: true })}
      validateIsPending={false}
      onCreate={() => {}}
      createIsPending={false}
    />
  );
}

async function pick(label: string, mode: 'guided' | 'expert') {
  await userEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}`) }));
  await userEvent.click(screen.getByRole('button', { name: new RegExp(`provider_modal\\.mode\\.${mode}`) }));
}

/** Back to the type picker: the footer's "Back" once to the mode choice, once more to the types. */
async function backToTypes() {
  await userEvent.click(screen.getByRole('button', { name: 'common.back' }));
  await userEvent.click(screen.getByRole('button', { name: 'common.back' }));
}

const box = (name: string | RegExp) => screen.getByRole('textbox', { name });
const NAME = /^provider_modal\.field\.name\b/;
const URL_FIELD = /^provider_modal\.field\.url\b/;
const primary = (name: string) => screen.getByRole('button', { name });

describe('Picking a type', () => {
  it('gives another type its own name and an empty address', async () => {
    renderWithProviders(<Harness />);
    await pick('Nginx Proxy Manager', 'expert');
    await userEvent.type(box(URL_FIELD), NPM_ADDRESS);
    await backToTypes();

    await pick('AdGuard Home', 'expert');
    expect(box(NAME)).toHaveValue('AdGuard Home');
    // Otherwise the AdGuard password is validated against the NPM address.
    expect(box(URL_FIELD)).toHaveValue('');
  });

  it('keeps what was typed when the same type is picked again', async () => {
    renderWithProviders(<Harness />);
    await pick('Nginx Proxy Manager', 'expert');
    await userEvent.clear(box(NAME));
    await userEvent.type(box(NAME), 'NPM at the lab');
    await userEvent.type(box(URL_FIELD), NPM_ADDRESS);
    await backToTypes();

    await pick('Nginx Proxy Manager', 'expert');
    expect(box(NAME)).toHaveValue('NPM at the lab');
    expect(box(URL_FIELD)).toHaveValue(NPM_ADDRESS);
  });
});

describe('The name', () => {
  it.each(['guided', 'expert'] as const)('is the first field of the %s form, and a required one', async (mode) => {
    renderWithProviders(<Harness />);
    await pick('Nginx Proxy Manager', mode);
    const [first] = screen.getAllByRole('textbox');
    expect(first).toBe(box(NAME));
    expect(first).toBeRequired();
  });
});

describe('One rule for what is required', () => {
  it('holds the guided "Next" until the step is filled', async () => {
    renderWithProviders(<Harness />);
    await pick('Nginx Proxy Manager', 'guided');
    expect(primary('provider_modal.guided.next')).toBeDisabled();
    await userEvent.type(box(/^Admin address/), NPM_ADDRESS);
    expect(primary('provider_modal.guided.next')).toBeEnabled();
  });

  it('asks the expert form for the NPM e-mail too, and says so', async () => {
    renderWithProviders(<Harness />);
    await pick('Nginx Proxy Manager', 'expert');
    await userEvent.type(box(URL_FIELD), NPM_ADDRESS);
    await userEvent.type(screen.getByLabelText(/^provider_modal\.field\.password/), 'not-a-real-secret');

    expect(box(/^provider_modal\.field\.username/)).toBeRequired();
    expect(primary('provider_modal.footer.validate')).toBeDisabled();
    const missing = screen.getByRole('list', { name: 'provider_modal.missing.title' });
    expect(within(missing).getByText('provider_modal.field.username')).toBeInTheDocument();
  });

  it('says everything is there on the last guided step only once it is', async () => {
    renderWithProviders(<Harness />);
    await pick('AdGuard Home', 'guided');
    expect(screen.queryByText('provider_modal.guided.collected')).toBeNull();
    expect(screen.getByText('provider_modal.missing.title')).toBeInTheDocument();

    await userEvent.type(box(/^Address/), 'http://127.0.0.1:3000');
    await userEvent.type(box(/^Account/), 'admin');
    await userEvent.type(screen.getByLabelText(/^Password/), 'not-a-real-secret');
    expect(screen.getByText('provider_modal.guided.collected')).toBeInTheDocument();
    expect(primary('provider_modal.footer.validate')).toBeEnabled();
  });
});

describe('A verdict', () => {
  it('is dropped by the next edit, so "Connect" never answers for untested values', async () => {
    renderWithProviders(<Harness />);
    await pick('AdGuard Home', 'expert');
    await userEvent.type(box(URL_FIELD), 'http://127.0.0.1:3000');
    await userEvent.type(box(/^provider_modal\.field\.username/), 'admin');
    await userEvent.type(screen.getByLabelText(/^provider_modal\.field\.password/), 'not-a-real-secret');
    await userEvent.click(primary('provider_modal.footer.validate'));
    expect(primary('provider_modal.footer.connect')).toBeEnabled();

    await userEvent.type(screen.getByLabelText(/^provider_modal\.field\.password/), '!');
    expect(screen.queryByRole('button', { name: 'provider_modal.footer.connect' })).toBeNull();
    expect(primary('provider_modal.footer.validate')).toBeEnabled();
  });
});

describe('A hosted API', () => {
  it('shows its address field in the expert form, optional, with what a blank one does', async () => {
    renderWithProviders(<Harness />);
    await pick('Cloudflare', 'expert');
    const url = box(URL_FIELD);
    expect(url).not.toBeRequired();
    expect(url).toHaveAccessibleDescription(/provider_modal\.field\.url_hint_hosted/);
  });
});
