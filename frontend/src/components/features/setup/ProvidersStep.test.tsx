/**
 * The wizard screen listing the integrations collected so far.
 *
 * `providers` comes from a list this screen does not own, and an unread list leaves behind the
 * same empty array an instance with nothing connected leaves. The screen used to read only the
 * second meaning, and it read it three times over: "No integration yet", a body asking for one
 * to be added, and a footer button reading "Skip for now" -- three statements about a list it
 * had not seen. The footer one is the sharpest, because the shell binds Enter to it.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { ProvidersStep } from './ProvidersStep';
import type { ProviderItem } from './types';

const NPM: ProviderItem = { id: 1, name: 'nginx-proxy-manager', type: 'npm' };

function renderStep(overrides: Partial<Parameters<typeof ProvidersStep>[0]> = {}) {
  return renderWithProviders(
    <ProvidersStep
      providers={[]}
      onAdd={() => {}}
      onDelete={() => {}}
      deleteIsPending={false}
      onBack={() => {}}
      onContinue={() => {}}
      {...overrides}
    />,
  );
}

const shimmers = () => document.querySelectorAll('.animate-shimmer');
const footer = () => screen.queryByRole('button', { name: 'setup.providers.skip' });

describe('ProvidersStep, the list it was handed', () => {
  it('does not say the instance has no integration while the list is in flight', () => {
    renderStep({ loading: true });
    expect(screen.queryByText('setup.providers.empty_title')).toBeNull();
    // And not by rendering nothing either: something has to hold the place of the list.
    expect(shimmers().length).toBeGreaterThan(0);
  });

  it('does not offer to skip a step whose contents are still unknown', () => {
    renderStep({ loading: true });
    expect(footer()).toBeNull();
    const carryOn = screen.getByRole('button', { name: 'setup.providers.continue' });
    // The shell runs the primary action on Enter, so this also holds the keyboard.
    expect(carryOn).toBeDisabled();
  });

  it('says the read failed rather than that there is nothing to show', () => {
    renderStep({ loadFailed: true });
    expect(screen.getByText('setup.providers.load_failed')).toBeInTheDocument();
    expect(screen.getByText('setup.providers.load_failed_hint')).toBeInTheDocument();
    expect(screen.queryByText('setup.providers.empty_title')).toBeNull();
    expect(footer()).toBeNull();
  });

  it('lets the operator ask for the list again', () => {
    const onRetry = vi.fn();
    renderStep({ loadFailed: true, onRetry });
    fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('still says it once the list has come back with nothing in it', () => {
    renderStep();
    expect(screen.getByText('setup.providers.empty_title')).toBeInTheDocument();
    expect(shimmers()).toHaveLength(0);
    expect(footer()).toBeInTheDocument();
  });

  it('lists what came back, and offers to carry it forward', () => {
    renderStep({ providers: [NPM] });
    expect(screen.getByText('nginx-proxy-manager')).toBeInTheDocument();
    expect(screen.queryByText('setup.providers.empty_title')).toBeNull();
    expect(footer()).toBeNull();
    expect(screen.getByRole('button', { name: 'setup.providers.continue' })).toBeEnabled();
  });
});
