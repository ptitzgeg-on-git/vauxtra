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
 *
 * Nor over a scan that lost an integration on the way, and the list it does show is the one
 * Settings > Data shows: one row per name, grouped by the zone the import files it under.
 * The rows below are built by `buildRows` from a scan, the way `Setup` builds them, so a
 * test of this screen is a test of the rows it is actually handed.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { NOTHING_TRACKED, buildRows, declaredOf, type SyncRow } from '@/lib/syncRows';
import type { SyncDnsRewrite, SyncProviderReport, SyncProxyHost, SyncResult } from '@/types/api';
import { ImportStep } from './ImportStep';
import type { ProviderItem } from './types';

const PROVIDER: ProviderItem = { id: 1, name: 'npm-lan', type: 'nginx_proxy_manager' };

function proxy(name: string, zone: string): SyncProxyHost {
  return {
    domain_names: [name],
    forward_host: '192.168.1.20',
    forward_port: 8080,
    _provider_id: 1,
    _provider_name: 'npm-lan',
    _provider_type: 'nginx_proxy_manager',
    _zone: zone,
  };
}

function dns(name: string, zone: string, provider: { id: number; name: string }): SyncDnsRewrite {
  return { domain: name, answer: '192.168.1.20', _provider_id: provider.id, _provider_name: provider.name, _zone: zone };
}

/** The rows `Setup` hands this screen for *scan*: `buildRows`, less what is tracked already. */
function rowsOf(scan: SyncResult): SyncRow[] {
  return buildRows(scan, declaredOf(scan.declared_domains), NOTHING_TRACKED).rows.filter(
    (row) => row.status !== 'exists',
  );
}

const ONE = rowsOf({ proxy_hosts: [proxy('api.example.com', 'example.com')] });

/** Two zones, one of them with its apex listed, which the import refuses. */
const TWO_ZONES = rowsOf({
  proxy_hosts: [proxy('api.example.com', 'example.com'), proxy('www.other.net', 'other.net')],
  dns_rewrites: [dns('example.com', 'example.com', { id: 2, name: 'adguard' })],
});

const FAILED: SyncProviderReport = {
  id: 3,
  name: 'cf-main',
  type: 'cloudflare',
  ok: false,
  count: 0,
  error: 'HTTP 403',
};

const BASE = {
  providers: [PROVIDER],
  rows: [] as SyncRow[],
  selected: new Set<string>() as ReadonlySet<string>,
  loadingImportable: false,
  onToggle: () => {},
  onSelect: () => {},
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
    renderWithProviders(<ImportStep {...BASE} rows={ONE} />);

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

  it('names an integration the scan could not read, instead of the green tick', () => {
    // The scan answered, with nothing in it: from one integration because it has nothing,
    // from the other because it could not be read. "Nothing to import" is only true of the
    // first.
    renderWithProviders(<ImportStep {...BASE} failedProviders={[FAILED]} />);

    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('settings.migration.provider_failed_title')).toBeInTheDocument();
    expect(within(alert).getByText('settings.migration.provider_failed_line')).toBeInTheDocument();
    expect(screen.queryByText('setup.import.none_found')).not.toBeInTheDocument();
  });

  it('names it above the list as well, when the others found something', () => {
    renderWithProviders(<ImportStep {...BASE} rows={ONE} failedProviders={[FAILED]} />);

    expect(screen.getByText('settings.migration.provider_failed_title')).toBeInTheDocument();
    expect(screen.getByText('api.example.com')).toBeInTheDocument();
  });

  it('offers a name once, however many integrations answered for it', () => {
    // A proxy host and two DNS answers for one name: the old list had three rows for it,
    // each with its own box, and the import folded all three into one service.
    const rows = rowsOf({
      proxy_hosts: [proxy('api.example.com', 'example.com')],
      dns_rewrites: [
        dns('api.example.com', 'example.com', { id: 2, name: 'adguard' }),
        dns('api.example.com', 'example.com', { id: 4, name: 'technitium' }),
      ],
    });
    renderWithProviders(<ImportStep {...BASE} rows={rows} />);

    expect(screen.getAllByText('api.example.com')).toHaveLength(1);
    expect(screen.getAllByRole('checkbox', { name: /api\.example\.com/ })).toHaveLength(1);
    expect(screen.getByText('settings.migration.providers_pill')).toBeInTheDocument();
  });

  it('files each name under its zone, and a zone box ticks that zone only', async () => {
    const onSelect = vi.fn();
    renderWithProviders(<ImportStep {...BASE} rows={TWO_ZONES} onSelect={onSelect} />);

    const zone = screen.getByRole('region', { name: 'example.com' });
    expect(within(zone).getByText('api.example.com')).toBeInTheDocument();
    expect(within(zone).queryByText('www.other.net')).not.toBeInTheDocument();

    // The apex sits in the zone and is not a choice: its box ticks what can be imported.
    await userEvent.click(screen.getByRole('checkbox', { name: 'example.com' }));
    expect(onSelect).toHaveBeenCalledWith(['api.example.com'], true);
  });

  it('refuses the zone apex on screen, the way the import refuses it', () => {
    renderWithProviders(<ImportStep {...BASE} rows={TWO_ZONES} />);

    expect(screen.getByRole('checkbox', { name: /settings\.migration\.status_apex/ })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /api\.example\.com/ })).toBeEnabled();
  });

  it('selects everything that can be imported, and nothing that cannot', async () => {
    const onSelect = vi.fn();
    renderWithProviders(<ImportStep {...BASE} rows={TWO_ZONES} onSelect={onSelect} />);

    await userEvent.click(screen.getByRole('checkbox', { name: 'setup.import.select_all' }));
    expect(onSelect).toHaveBeenCalledWith(['api.example.com', 'www.other.net'], true);
  });

  it('names the domains an import is about to declare, before the button that does it', () => {
    renderWithProviders(
      <ImportStep {...BASE} rows={TWO_ZONES} selected={new Set(['api.example.com', 'www.other.net'])} />,
    );

    const alert = screen.getByText('setup.import.declares_domains').closest('[role]') as HTMLElement;
    expect(within(alert).getByText('example.com')).toBeInTheDocument();
    expect(within(alert).getByText('other.net')).toBeInTheDocument();
  });

  it('names only the zones of the rows ticked', () => {
    renderWithProviders(<ImportStep {...BASE} rows={TWO_ZONES} selected={new Set(['www.other.net'])} />);

    const alert = screen.getByText('setup.import.declares_domains').closest('[role]') as HTMLElement;
    expect(within(alert).getByText('other.net')).toBeInTheDocument();
    expect(within(alert).queryByText('example.com')).not.toBeInTheDocument();
  });

  it('says nothing about a domain already declared', () => {
    const rows = rowsOf({
      proxy_hosts: [{ ...proxy('api.example.com', 'example.com'), _declared: true }],
      declared_domains: ['example.com'],
    });
    renderWithProviders(<ImportStep {...BASE} rows={rows} selected={new Set(['api.example.com'])} />);

    expect(screen.queryByText('setup.import.declares_domains')).not.toBeInTheDocument();
  });
});
