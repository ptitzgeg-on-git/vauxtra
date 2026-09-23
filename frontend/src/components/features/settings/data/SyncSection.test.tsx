/**
 * The provider scan, read against what production showed on 2026-09-22.
 *
 * The first scan of a fresh instance offered 91 routes behind one "Quick Import (91 new)",
 * 59 of them in twelve zones nobody had declared, which one Cloudflare token could read.
 * A name three integrations answer for showed once, under whichever had been read first. And
 * with no domain declared, four clicks on "Scan providers" were reported to say nothing at
 * all; that was not reproduced, and the panel now says why a domain matters. So these tests
 * pin down: one row per name, with every record found for it; the table opening on the
 * declared zones and saying how many it hides; the notice that asks for a domain; an
 * integration that failed being named rather than missing; and an import that sends the
 * names on screen, and for a name Vauxtra already tracks, only the half it lacks.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { api } from '@/api/client';
import { renderWithProviders } from '@/test/render';
import type { Service, SyncDnsRewrite, SyncProxyHost, SyncResult } from '@/types/api';

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const { SyncSection } = await import('./SyncSection');

const DECLARED = { _zone: 'example.test', _declared: true };

function proxy(name: string, target: string, extra: Partial<SyncProxyHost> = {}): SyncProxyHost {
  const [host, port] = target.split(':');
  return {
    domains: [name],
    forward_host: host,
    forward_port: Number(port),
    _provider_name: 'npm-home',
    _already_imported: false,
    ...DECLARED,
    ...extra,
  };
}

function dns(name: string, answer: string, extra: Partial<SyncDnsRewrite> = {}): SyncDnsRewrite {
  return { domain: name, answer, _provider_name: 'adguard', _already_imported: false, ...DECLARED, ...extra };
}

/**
 * One scan holding every case at once: a proxy host and its DNS record (`app`, spelt with a
 * capital the way NPM keeps what was typed), a name two DNS integrations answer for
 * (`jellyfin`), a tracked service missing its DNS half (`grafana`), a tracked service that is
 * whole (`wiki`), the zone apex, a name in a zone nobody declared (`shop.other.test`), a
 * record with no name, and an integration that could not be read.
 */
function scan(overrides: Partial<SyncResult> = {}): SyncResult {
  return {
    declared_domains: ['example.test'],
    providers: [
      { id: 1, name: 'npm-home', type: 'npm', ok: true, count: 2, error: '' },
      { id: 2, name: 'adguard', type: 'adguard', ok: true, count: 5, error: '' },
      { id: 3, name: 'cloudflare', type: 'cloudflare', ok: true, count: 3, error: '' },
      { id: 4, name: 'technitium', type: 'technitium', ok: false, count: 0, error: 'HTTP 401' },
    ],
    proxy_hosts: [
      proxy('App.example.test', '10.0.0.5:8080'),
      proxy('grafana.example.test', '10.0.0.7:3000', { _already_imported: true }),
    ],
    dns_rewrites: [
      dns('app.example.test', '10.0.0.1'),
      dns('jellyfin.example.test', '10.0.0.6'),
      dns('jellyfin.example.test', 'tunnel.cfargotunnel.test', { _provider_name: 'cloudflare', zone: 'example.test' }),
      dns('grafana.example.test', '10.0.0.1', { _already_imported: true }),
      dns('wiki.example.test', '10.0.0.1', { _already_imported: true }),
      dns('example.test', '203.0.113.9', { _provider_name: 'cloudflare', zone: 'example.test' }),
      dns('shop.other.test', '203.0.113.10', {
        _provider_name: 'cloudflare',
        zone: 'other.test',
        _zone: 'other.test',
        _declared: false,
      }),
      dns('', '10.0.0.9', { _zone: undefined, _declared: undefined }),
    ],
    ...overrides,
  };
}

const SERVICES = [
  { id: 1, subdomain: 'grafana', domain: 'example.test', dns_provider_id: null, expose_mode: 'proxy', public_host: '', tunnel_hostname: '' },
  { id: 2, subdomain: 'wiki', domain: 'example.test', dns_provider_id: 2, expose_mode: 'proxy', public_host: '', tunnel_hostname: '' },
] as unknown as Service[];

let domainRows: string[] | Error = ['example.test'];
let scanAnswer: SyncResult = scan();

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);

beforeEach(() => {
  domainRows = ['example.test'];
  scanAnswer = scan();
  vi.clearAllMocks();
  get.mockImplementation(((path: string) => {
    if (path === '/services') return Promise.resolve(SERVICES);
    if (path === '/domains') return domainRows instanceof Error ? Promise.reject(domainRows) : Promise.resolve(domainRows);
    return Promise.resolve([]);
  }) as typeof api.get);
  post.mockImplementation(((path: string) => {
    if (path === '/services/sync') return Promise.resolve(scanAnswer);
    if (path === '/services/import') return Promise.resolve({ imported: 2, linked: 1, skipped: [], errors: [] });
    return Promise.resolve({});
  }) as typeof api.post);
});

async function scanNow() {
  renderWithProviders(<SyncSection />);
  await userEvent.click(screen.getByRole('button', { name: 'settings.migration.scan' }));
  await screen.findByRole('table');
}

/** The table row whose subdomain cell reads *subdomain*. */
function rowOf(subdomain: string): HTMLElement {
  const cell = screen.getByText(subdomain, { selector: 'td' });
  const row = cell.closest('tr');
  if (!row) throw new Error(`no row for ${subdomain}`);
  return row;
}

function subdomainsShown(): string[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[1].textContent ?? '');
}

function importPayload(): SyncResult {
  const call = post.mock.calls.find(([path]) => path === '/services/import');
  if (!call) throw new Error('nothing was imported');
  return call[1] as SyncResult;
}

describe('SyncSection, one row per name', () => {
  it('shows a name several integrations answer for once, with every answer and a pill', async () => {
    await scanNow();

    const row = rowOf('jellyfin');
    expect(within(row).getByText('10.0.0.6')).toBeInTheDocument();
    expect(within(row).getByText('tunnel.cfargotunnel.test')).toBeInTheDocument();
    expect(within(row).getByText('adguard')).toBeInTheDocument();
    expect(within(row).getByText('cloudflare')).toBeInTheDocument();
    expect(within(row).getByText('settings.migration.providers_pill')).toBeInTheDocument();
    expect(screen.getAllByText('jellyfin', { selector: 'td' })).toHaveLength(1);
  });

  it('files a proxy host and its DNS record under one name, whatever the case it was typed in', async () => {
    await scanNow();

    const row = rowOf('app');
    expect(within(row).getByText('10.0.0.5:8080')).toBeInTheDocument();
    expect(within(row).getByText('10.0.0.1')).toBeInTheDocument();
    expect(within(row).getByText('settings.migration.status_new')).toBeInTheDocument();
  });

  it('marks a tracked service missing its DNS half as a link, and a whole one as tracked', async () => {
    await scanNow();

    expect(within(rowOf('grafana')).getByText('settings.migration.status_link')).toBeInTheDocument();
    expect(within(rowOf('wiki')).getByText('settings.migration.status_tracked')).toBeInTheDocument();
    expect(within(rowOf('wiki')).getByRole('checkbox')).toBeDisabled();
  });

  it('does not offer the zone apex, which the import would refuse', async () => {
    await scanNow();

    const apex = screen.getByText('settings.migration.status_apex').closest('tr');
    expect(apex).not.toBeNull();
    expect(within(apex as HTMLElement).getByRole('checkbox')).toBeDisabled();
  });

  it('counts the records that carry no name instead of dropping them', async () => {
    await scanNow();
    expect(screen.getByText('settings.migration.nameless')).toBeInTheDocument();
  });
});

describe('SyncSection, the declared zones', () => {
  it('opens on the declared zones and says that it hides the others', async () => {
    await scanNow();

    expect(screen.getByRole('switch', { name: 'settings.migration.declared_only' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('settings.migration.declared_only_hidden')).toBeInTheDocument();
    expect(screen.queryByText('shop', { selector: 'td' })).toBeNull();
    // One declared zone left on screen: a zone filter would have nothing to choose between.
    expect(screen.queryByRole('group', { name: 'settings.migration.zones_label' })).toBeNull();
  });

  it('shows every zone once the switch is off, marked, and filters by zone', async () => {
    await scanNow();
    await userEvent.click(screen.getByRole('switch', { name: 'settings.migration.declared_only' }));

    expect(screen.getByText('settings.migration.declared_only_shown')).toBeInTheDocument();
    const shop = rowOf('shop');
    expect(within(shop).getAllByText('settings.migration.zone_undeclared_title').length).toBeGreaterThan(0);

    const zones = screen.getByRole('group', { name: 'settings.migration.zones_label' });
    await userEvent.click(within(zones).getByRole('button', { name: /^other\.test/ }));
    expect(subdomainsShown()).toEqual(['shop']);

    await userEvent.click(within(zones).getByRole('button', { name: /^settings\.migration\.zone_all/ }));
    expect(subdomainsShown()).toContain('shop');
    expect(subdomainsShown()).toContain('jellyfin');
  });

  it('files declared zones first, then by zone and name', async () => {
    await scanNow();
    await userEvent.click(screen.getByRole('switch', { name: 'settings.migration.declared_only' }));
    expect(subdomainsShown()).toEqual(['—', 'app', 'grafana', 'jellyfin', 'wiki', 'shop']);
  });
});

describe('SyncSection, what the panel says before and around a scan', () => {
  it('asks for a DNS domain before the scan when none is declared, and links to it', async () => {
    domainRows = [];
    renderWithProviders(<SyncSection />);

    const notice = await screen.findByRole('alert');
    expect(within(notice).getByText('settings.migration.no_domain_title')).toBeInTheDocument();
    expect(within(notice).getByRole('link', { name: 'settings.migration.no_domain_cta' })).toHaveAttribute(
      'href',
      '/settings?tab=dns',
    );
    // The notice explains; it does not take the button away.
    expect(screen.getByRole('button', { name: 'settings.migration.scan' })).toBeEnabled();
  });

  it('does not claim there is no domain while the list of domains could not be read', async () => {
    domainRows = new Error('domains refused');
    const { queryClient } = renderWithProviders(<SyncSection />);
    // Settled into an error, not merely asked: a notice drawn from `data ?? []` would only
    // appear once the read had failed, so looking any earlier proves nothing.
    await waitFor(() => expect(queryClient.getQueryState(['domains'])?.status).toBe('error'));

    expect(screen.queryByText('settings.migration.no_domain_title')).toBeNull();
  });

  it('takes the answer of the scan when the list of domains could not be read', async () => {
    domainRows = new Error('domains refused');
    scanAnswer = scan({ declared_domains: [], providers: [] });
    await scanNow();

    expect(screen.getByText('settings.migration.no_domain_title')).toBeInTheDocument();
    // With nothing declared, a declared-only filter would hide every row: it is not offered.
    expect(screen.queryByRole('switch', { name: 'settings.migration.declared_only' })).toBeNull();
    expect(subdomainsShown()).toContain('shop');
  });

  it('names the integration it could not read and points at the integrations page', async () => {
    await scanNow();

    const alert = screen
      .getAllByRole('alert')
      .find((node) => within(node).queryByText('settings.migration.provider_failed_title'));
    expect(alert).toBeDefined();
    expect(within(alert as HTMLElement).getByRole('link', { name: 'settings.migration.provider_failed_cta' })).toHaveAttribute(
      'href',
      '/providers',
    );
    const scanned = screen.getByRole('list', { name: 'settings.migration.providers_label' });
    expect(within(scanned).getByText('technitium')).toBeInTheDocument();
    expect(within(scanned).getByText('npm-home')).toBeInTheDocument();
  });
});

describe('SyncSection, what an import sends', () => {
  it('asks first, on Cancel, and sends only the importable names on screen', async () => {
    await scanNow();
    await userEvent.click(screen.getByRole('button', { name: 'settings.migration.quick_import_cta' }));

    const dialog = await screen.findByRole('dialog');
    // A held Enter must not be the answer to a bulk write.
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'common.cancel' }));
    expect(within(dialog).getByText(/settings\.migration\.quick_import_hidden/)).toBeInTheDocument();
    expect(within(dialog).getByText(/settings\.migration\.quick_import_conflicts/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalledWith('/services/import', expect.anything());

    await userEvent.click(within(dialog).getByRole('button', { name: 'settings.migration.import' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/services/import', expect.anything()));

    const payload = importPayload();
    // The proxy host of the new name, never the one a tracked service already has.
    expect(payload.proxy_hosts?.map((host) => host.domains?.[0])).toEqual(['App.example.test']);
    // New names and the missing DNS half; not the tracked one, the apex, nor the hidden zone.
    expect(payload.dns_rewrites?.map((record) => record.domain)).toEqual([
      'app.example.test',
      'jellyfin.example.test',
      'jellyfin.example.test',
      'grafana.example.test',
    ]);
  });

  it('sends nothing when the question is answered no', async () => {
    await scanNow();
    await userEvent.click(screen.getByRole('button', { name: 'settings.migration.quick_import_cta' }));
    const dialog = await screen.findByRole('dialog');

    await userEvent.click(within(dialog).getByRole('button', { name: 'common.cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(post).not.toHaveBeenCalledWith('/services/import', expect.anything());
  });

  it('sends only the DNS record of a link row, not the proxy host its service has', async () => {
    await scanNow();
    await userEvent.click(within(rowOf('grafana')).getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'settings.migration.import_selected' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'settings.migration.import' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/services/import', expect.anything()));

    const payload = importPayload();
    expect(payload.proxy_hosts).toEqual([]);
    expect(payload.dns_rewrites?.map((record) => record.domain)).toEqual(['grafana.example.test']);
  });

  it('does not send a ticked row the filters have since hidden', async () => {
    await scanNow();
    const toggle = screen.getByRole('switch', { name: 'settings.migration.declared_only' });
    await userEvent.click(toggle);
    await userEvent.click(within(rowOf('shop')).getByRole('checkbox'));
    expect(screen.getByRole('button', { name: 'settings.migration.import_selected' })).toBeEnabled();

    await userEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'settings.migration.import_selected' })).toBeDisabled();
  });
});
