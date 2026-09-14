/**
 * What a root domain is still holding, and which question deleting it asks.
 *
 * `services.domain` and `service_templates.domain` are two bare TEXT columns naming the same
 * root, and this tab counted only the first. A domain no service used but a template did
 * showed the neutral "0 services" badge and the plain "Delete domain?" question -- the same
 * two things an unused domain shows, over a domain that was in use.
 *
 * The sentence the in-use question replaced was measurably false the other way round. It
 * said deleting the domain "may break existing routes"; nothing at runtime reads the
 * `domains` table at all -- the scheduler, the DNS push and the proxy push all work off
 * `services.domain` -- so every row listed keeps its hostname and stays published. What is
 * actually lost is the name in the three pickers, and `INSERT OR IGNORE INTO domains` in the
 * Docker scan and the provider import puts it back on its own.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on
 * purpose -- so these assertions survive any rewording in the eight locale files. What they
 * pin is which sentence is asked, and over which rows.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { api } from '@/api/client';
import type { Service, Template } from '@/types/api';

let domainRows: string[] = [];
let serviceRows: Service[] = [];
let templateRows: Template[] = [];

/**
 * How one of the two usage reads answers: with its rows, with a failure, or never -- a
 * request still in flight. Both are states the tab used to be unable to tell from an empty
 * list, which is the whole subject of the last describe block below.
 */
type Answer = 'rows' | 'fails' | 'never';
let servicesAnswer: Answer = 'rows';
let templatesAnswer: Answer = 'rows';

/** A request that never comes back, so `isPending` stays true for the whole test. */
const NEVER: Promise<never> = new Promise(() => {});

function answer<T>(mode: Answer, rows: T): Promise<T> {
  if (mode === 'fails') return Promise.reject(new Error('read refused'));
  if (mode === 'never') return NEVER;
  return Promise.resolve(rows);
}

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/domains') return Promise.resolve(domainRows);
      if (path === '/services') return answer(servicesAnswer, serviceRows);
      if (path === '/templates') return answer(templatesAnswer, templateRows);
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { DnsTab } = await import('./DnsTab');

function service(over: Partial<Service> = {}): Service {
  return {
    id: 1,
    subdomain: 'git',
    domain: 'example.test',
    target_ip: '10.0.0.10',
    target_port: 3000,
    forward_scheme: 'http',
    websocket: false,
    expose_mode: 'proxy_dns',
    public_target_mode: 'manual',
    auto_update_dns: false,
    tunnel_hostname: '',
    dns_ip: '',
    npm_host_id: null,
    dns_provider_id: null,
    proxy_provider_id: null,
    tunnel_provider_id: null,
    enabled: true,
    status: 'ok',
    last_checked: null,
    created_at: '2026-01-01T00:00:00Z',
    tags: [],
    environments: [],
    ...over,
  };
}

function template(over: Partial<Template> = {}): Template {
  return {
    id: 1,
    name: 'standard',
    description: '',
    forward_scheme: 'http',
    target_port: null,
    websocket: false,
    expose_mode: 'proxy_dns',
    proxy_provider_id: null,
    dns_provider_id: null,
    tunnel_provider_id: null,
    public_target_mode: 'manual',
    domain: 'example.test',
    dns_ip: '',
    tag_ids: [],
    environment_ids: [],
    icon_url: '',
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

/** The row for a domain, awaited, so nothing below races the three queries. */
async function rowFor(domain: string): Promise<HTMLElement> {
  return (await screen.findByText(domain)).closest('li') as HTMLElement;
}

/** Renders the tab, opens the delete question for `domain`, and hands back the dialog. */
async function askToDelete(domain: string): Promise<HTMLElement> {
  renderWithProviders(<DnsTab />);
  await userEvent.click(within(await rowFor(domain)).getByRole('button'));
  return screen.findByRole('dialog');
}

const templateBadge = () => screen.queryByText('settings.dns.template_count');

describe('DnsTab, what a root domain is holding', () => {
  beforeEach(() => {
    domainRows = ['example.test'];
    serviceRows = [];
    templateRows = [];
    servicesAnswer = 'rows';
    templatesAnswer = 'rows';
    vi.clearAllMocks();
  });

  it('shows no template badge when no template names the domain', async () => {
    serviceRows = [service()];
    renderWithProviders(<DnsTab />);
    await rowFor('example.test');

    expect(screen.getByText('settings.dns.service_count')).toBeInTheDocument();
    expect(templateBadge()).toBeNull();
  });

  it('shows a template badge when a template names the domain', async () => {
    templateRows = [template()];
    renderWithProviders(<DnsTab />);
    await rowFor('example.test');

    // The whole row used to read "0 services" and nothing else over exactly this.
    expect(templateBadge()).toBeInTheDocument();
  });

  it('leaves a template that names no root out of the domain it names none of', async () => {
    // A template may leave the domain to the service it creates, and that blank names no
    // root. The empty name is a row `domains` can really hold -- `app/api/sync.py` splits an
    // imported `host.` into `("host", "")` and inserts the second half -- so the map keyed by
    // root domain is read at the empty key too, and a badge there counts templates against a
    // domain they are not built on.
    domainRows = ['', 'example.test'];
    templateRows = [template({ domain: '' })];
    renderWithProviders(<DnsTab />);

    const list = (await rowFor('example.test')).parentElement as HTMLElement;
    const nameless = within(list).getAllByRole('listitem')[0];
    expect(within(nameless).queryByText('settings.dns.template_count')).toBeNull();
    expect(templateBadge()).toBeNull();
  });

  it('keeps a neighbouring domain out of the count', async () => {
    domainRows = ['example.test', 'other.test'];
    serviceRows = [service({ id: 1, subdomain: 'git', domain: 'other.test' })];
    templateRows = [template({ id: 1, domain: 'other.test' })];
    renderWithProviders(<DnsTab />);

    expect(within(await rowFor('example.test')).queryByText('settings.dns.template_count')).toBeNull();
    expect(
      within(await rowFor('other.test')).getByText('settings.dns.template_count'),
    ).toBeInTheDocument();
  });
});

describe('DnsTab, which question deleting a domain asks', () => {
  beforeEach(() => {
    domainRows = ['example.test'];
    serviceRows = [];
    templateRows = [];
    servicesAnswer = 'rows';
    templatesAnswer = 'rows';
    vi.clearAllMocks();
  });

  it('asks the plain question over a domain nothing is built on', async () => {
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('settings.dns.confirm.delete_title')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.dns.confirm.in_use_title')).toBeNull();
  });

  it('asks the in-use question over a domain only a template holds', async () => {
    templateRows = [template()];
    const dialog = await askToDelete('example.test');

    // The defect in one assertion: a template holds the name just as a service does, and
    // this used to be the plain "Delete domain?" with nothing else in it.
    expect(within(dialog).getByText('settings.dns.confirm.in_use_title')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.dns.confirm.delete_title')).toBeNull();
    expect(within(dialog).getByText('settings.dns.confirm.in_use_templates')).toBeInTheDocument();
    // No service is built on the name, so the sentence that counts services is not asked.
    expect(within(dialog).queryByText('settings.dns.confirm.in_use_services')).toBeNull();
    expect(within(dialog).getByText('standard')).toBeInTheDocument();
  });

  it('lists both kinds of holder in the same body', async () => {
    serviceRows = [service()];
    templateRows = [template()];
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('settings.dns.confirm.in_use_services')).toBeInTheDocument();
    expect(within(dialog).getByText('settings.dns.confirm.in_use_templates')).toBeInTheDocument();
    expect(within(dialog).getByText('git.example.test')).toBeInTheDocument();
    expect(within(dialog).getByText('standard')).toBeInTheDocument();
    // The three sentences that replaced "may break existing routes": nothing stops working,
    // the pickers lose the name, and a scan or an import puts the name back.
    expect(within(dialog).getByText('settings.dns.confirm.in_use_effect')).toBeInTheDocument();
    expect(within(dialog).getByText('settings.dns.confirm.in_use_pickers')).toBeInTheDocument();
    expect(within(dialog).getByText('settings.dns.confirm.in_use_returns')).toBeInTheDocument();
  });
});

describe('DnsTab, how the in-use body names what it found', () => {
  beforeEach(() => {
    domainRows = ['example.test'];
    serviceRows = [];
    templateRows = [];
    servicesAnswer = 'rows';
    templatesAnswer = 'rows';
    vi.clearAllMocks();
  });

  it('names an apex route by the domain alone', async () => {
    // An apex route stores an empty subdomain, and the naive `sub + "." + domain` join names
    // it `.example.test` -- a hostname that is not the one published.
    serviceRows = [service({ subdomain: '' })];
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('example.test')).toBeInTheDocument();
  });

  it('stops naming and starts counting past five holders', async () => {
    serviceRows = [1, 2, 3, 4, 5, 6, 7].map((n) => service({ id: n, subdomain: `s${n}` }));
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('s1.example.test')).toBeInTheDocument();
    expect(within(dialog).getByText('s5.example.test')).toBeInTheDocument();
    expect(within(dialog).queryByText('s6.example.test')).toBeNull();
    expect(within(dialog).queryByText('s7.example.test')).toBeNull();
    expect(within(dialog).getByText('settings.dns.confirm.in_use_more')).toBeInTheDocument();
  });

  it('names every holder while there are five or fewer', async () => {
    serviceRows = [1, 2, 3, 4, 5].map((n) => service({ id: n, subdomain: `s${n}` }));
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('s5.example.test')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.dns.confirm.in_use_more')).toBeNull();
  });
});

describe('DnsTab, what the answer to the question does', () => {
  beforeEach(() => {
    domainRows = ['example.test'];
    serviceRows = [service()];
    templateRows = [template()];
    servicesAnswer = 'rows';
    templatesAnswer = 'rows';
    vi.clearAllMocks();
  });

  it('deletes the domain once the in-use question is confirmed', async () => {
    const dialog = await askToDelete('example.test');
    await userEvent.click(within(dialog).getByRole('button', { name: 'common.delete' }));

    // The name is what leaves; the row and the template listed above it are untouched.
    expect(vi.mocked(api.delete)).toHaveBeenCalledWith('/domains/example.test');
  });

  it('deletes nothing when the question is cancelled', async () => {
    const dialog = await askToDelete('example.test');
    await userEvent.click(within(dialog).getByRole('button', { name: 'common.cancel' }));

    expect(vi.mocked(api.delete)).not.toHaveBeenCalled();
  });
});

/**
 * The badge and the question both read `dependents`, and `dependents` was built from two
 * queries destructured as `data = []`: nothing in the component could tell a domain nothing
 * is built on from a `/services` that failed, or that had simply not come back yet. The
 * badge then stated "0 services" -- a count, not a blank -- and the deletion asked the
 * question written for an unused domain. Nothing refuses it further down:
 * `DELETE /api/domains/{name}` looks the holders up only to journal them.
 */
describe('DnsTab, a usage read that has not answered', () => {
  beforeEach(() => {
    domainRows = ['example.test'];
    serviceRows = [service()];
    templateRows = [template()];
    servicesAnswer = 'rows';
    templatesAnswer = 'rows';
    vi.clearAllMocks();
  });

  it('says the usage is unknown rather than counting zero while a read is in flight', async () => {
    servicesAnswer = 'never';
    renderWithProviders(<DnsTab />);

    const row = await rowFor('example.test');
    expect(within(row).queryByText('settings.dns.service_count')).toBeNull();
    expect(within(row).getByText('settings.dns.usage_unknown')).toBeInTheDocument();
  });

  it('says the usage is unknown when the services read failed', async () => {
    servicesAnswer = 'fails';
    renderWithProviders(<DnsTab />);
    await screen.findByText('settings.dns.usage_failed');

    const row = await rowFor('example.test');
    expect(within(row).queryByText('settings.dns.service_count')).toBeNull();
    expect(within(row).getByText('settings.dns.usage_unknown')).toBeInTheDocument();
  });

  it('says so out loud, with a retry, and not only on the rows', async () => {
    templatesAnswer = 'fails';
    renderWithProviders(<DnsTab />);

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('settings.dns.usage_failed')).toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: 'ui.error.retry' })).toBeInTheDocument();
  });

  it('counts again once the retry answers', async () => {
    servicesAnswer = 'fails';
    renderWithProviders(<DnsTab />);
    const alert = await screen.findByRole('alert');

    servicesAnswer = 'rows';
    await userEvent.click(within(alert).getByRole('button', { name: 'ui.error.retry' }));

    expect(await screen.findByText('settings.dns.service_count')).toBeInTheDocument();
    expect(screen.queryByText('settings.dns.usage_unknown')).toBeNull();
  });

  it('asks the unknown question, not the plain one, when a read failed', async () => {
    // One service and one template are built on this domain. The reads that would have said
    // so never answered, and an empty `dependents` is exactly what the plain question is for.
    servicesAnswer = 'fails';
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('settings.dns.confirm.unknown_title')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.dns.confirm.delete_title')).toBeNull();
    expect(within(dialog).queryByText('settings.dns.confirm.in_use_title')).toBeNull();
  });

  it('asks it for the templates read as readily as for the services read', async () => {
    templatesAnswer = 'fails';
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('settings.dns.confirm.unknown_title')).toBeInTheDocument();
  });

  it('asks the unknown question while a read is still in flight', async () => {
    servicesAnswer = 'never';
    const dialog = await askToDelete('example.test');

    expect(within(dialog).getByText('settings.dns.confirm.unknown_title')).toBeInTheDocument();
  });

  it('still deletes the domain once the unknown question is confirmed', async () => {
    // Withholding the deletion would strand the tab on a read it may never get. What changes
    // is which question is asked, not whether the operator may answer it.
    servicesAnswer = 'fails';
    const dialog = await askToDelete('example.test');
    await userEvent.click(within(dialog).getByRole('button', { name: 'common.delete' }));

    expect(vi.mocked(api.delete)).toHaveBeenCalledWith('/domains/example.test');
  });
});
