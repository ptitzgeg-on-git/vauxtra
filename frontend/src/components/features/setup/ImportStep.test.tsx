/**
 * The last step of the wizard may not draw a green tick over a failed scan.
 *
 * When `POST /api/services/sync` failed, `Setup` emptied the list and the toast saying why
 * was gone in a few seconds. What stayed was "No services found to import", under a tick, on
 * the one screen whose next button ends setup -- so the wizard finished having told the
 * operator there was nothing to bring in.
 *
 * Nor over a scan that never ran, nor over a provider list it could not read. The scan used
 * to be fired by the one transition that leads here, and `step` is session-persisted: a
 * reload replayed no transition, so nothing scanned. A provider list that failed leaves
 * `providers` empty, which the ladder used to answer with "No providers configured." Both
 * fell to the same tick, so `scanFailed` now stands for either read.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { ImportStep } from './ImportStep';
import type { ImportableService, ProviderItem } from './types';

const PROVIDER: ProviderItem = { id: 1, name: 'npm-lan', type: 'nginx_proxy_manager' };

const SERVICE: ImportableService = {
  kind: 'proxy',
  source: 'npm-lan',
  type: 'nginx_proxy_manager',
  name: 'api.example.com',
  domain: 'api.example.com',
  target: 'http://192.168.1.20:8080',
  selected: false,
  raw: {} as ImportableService['raw'],
};

const BASE = {
  providers: [PROVIDER],
  importableServices: [] as ImportableService[],
  loadingImportable: false,
  onToggle: () => {},
  onSelectAll: () => {},
  onDeselectAll: () => {},
  onRetry: () => {},
  onImportAndFinish: () => {},
  onBack: () => {},
};

describe('ImportStep', () => {
  it('reports nothing to import when the scan answered and found nothing', () => {
    renderWithProviders(<ImportStep {...BASE} />);

    expect(screen.getByText('setup.import.none_found')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.scan_failed')).not.toBeInTheDocument();
  });

  it('says the scan failed instead, when it did', () => {
    renderWithProviders(<ImportStep {...BASE} scanFailed />);

    expect(screen.getByText('setup.import.scan_failed')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.none_found')).not.toBeInTheDocument();
  });

  it('warns that finishing from here imports nothing', () => {
    // The whole point: the operator is one click from the end of setup, and that click is
    // the only thing the old screen did not talk about.
    renderWithProviders(<ImportStep {...BASE} scanFailed />);

    expect(screen.getByText('setup.import.scan_failed_hint')).toBeInTheDocument();
  });

  it('keeps the retry button attached to the failure, not to a tick', async () => {
    const onRetry = vi.fn();
    renderWithProviders(<ImportStep {...BASE} scanFailed onRetry={onRetry} />);

    // Scoped to the alert on purpose. The "nothing to import" state offers a retry under the
    // same label, so an unscoped query would pass on the very screen this test forbids.
    const failure = screen.getByRole('alert');
    await userEvent.click(within(failure).getByRole('button', { name: 'setup.import.retry' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('says a read failed, rather than that no provider is configured', () => {
    // This asserted the opposite, and was right to while `scanFailed` meant the scan alone.
    // It stands for the provider list as well now, and a list that could not be read leaves
    // `providers` empty -- so "No providers configured." would be a statement about what is
    // configured, made by a screen that had just failed to find out. `Setup` lowers the flag
    // before it early-returns on an empty list, so the pair below only ever means a failure.
    renderWithProviders(<ImportStep {...BASE} providers={[]} scanFailed />);

    expect(screen.getByText('setup.import.scan_failed')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.no_providers')).not.toBeInTheDocument();
  });

  it('still says no provider is configured when the list really came back empty', () => {
    renderWithProviders(<ImportStep {...BASE} providers={[]} />);

    expect(screen.getByText('setup.import.no_providers')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.scan_failed')).not.toBeInTheDocument();
  });

  it('shows the checklist, not the failure, once a scan has succeeded', () => {
    renderWithProviders(<ImportStep {...BASE} importableServices={[SERVICE]} />);

    expect(screen.getByText('api.example.com')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.scan_failed')).not.toBeInTheDocument();
    expect(screen.queryByText('setup.import.none_found')).not.toBeInTheDocument();
  });

  it('answers for neither read while one of them is still in flight', () => {
    // The busy rung sits below the failure one on purpose: a read that failed never settles,
    // so nothing would ever lift a skeleton off it. The rows here are `animate-pulse`, this
    // screen's own marker rather than the shared shimmer, so the line above them is what
    // there is to hold on to.
    renderWithProviders(<ImportStep {...BASE} loadingImportable />);

    expect(screen.getByText('setup.import.scanning')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.none_found')).not.toBeInTheDocument();
    expect(screen.queryByText('setup.import.no_providers')).not.toBeInTheDocument();
  });
});
