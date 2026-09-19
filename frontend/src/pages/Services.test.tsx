/**
 * What the routes page says about a filter whose target it cannot name.
 *
 * `?tag=5` and `?env=3` live in the address bar, so both are applied before either list has
 * been read, survive a reload, and outlive whatever they point at. The rows are filtered on
 * the id alone and do not care. The `<Select>` needs an option carrying that value in order
 * to show it, and had none, so it fell back to its first option -- "All tags". Measured with
 * `/tags` failing and `?tag=5` set: every row hidden, the header counter at 0, and the one
 * control that could have accounted for it denying that any filter was on.
 *
 * The halves are tested apart: that an applied filter now has an option of its own carrying
 * its id, and that the option says *which* of the two reasons applies -- a list that came
 * back without the id names something deleted, a list that never came back names nothing.
 *
 * `t()` returns the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose --
 * so `services.filter.tag_gone` is the assertion, not the English sentence behind it.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import type { Environment, Service, Tag } from '@/types/api';

const TAG: Tag = { id: 5, name: 'edge', color: '#3b82f6' };
const ENVIRONMENT: Environment = { id: 3, name: 'prod', color: '#22c55e' };

const service = (id: number, subdomain: string, tags: Tag[], environments: Environment[]): Service => ({
  id,
  subdomain,
  domain: 'example.test',
  target_ip: '10.0.0.1',
  target_port: 8080,
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
  status: 'unknown',
  last_checked: null,
  created_at: '2026-01-01T00:00:00Z',
  tags,
  environments,
});

/** One route the filters keep and one they drop, so "nothing shown" is never the only state. */
const SERVICES: Service[] = [service(1, 'api', [TAG], [ENVIRONMENT]), service(2, 'www', [], [])];

/** A list that never settles, which is what the first paint of this page actually has. */
let tagsPending = false;
/** A list that settles into `isError`, the other way an id never becomes a name. */
let tagsFail = false;
let tagRows: Tag[] = [];
let environmentsPending = false;
let environmentsFail = false;
let environmentRows: Environment[] = [];

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/services') return Promise.resolve(SERVICES);
      if (path === '/tags') {
        if (tagsPending) return new Promise(() => {});
        if (tagsFail) return Promise.reject(new Error('tags unavailable'));
        return Promise.resolve(tagRows);
      }
      if (path === '/environments') {
        if (environmentsPending) return new Promise(() => {});
        if (environmentsFail) return Promise.reject(new Error('environments unavailable'));
        return Promise.resolve(environmentRows);
      }
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { Services } = await import('./Services');

const tagSelect = () => screen.getByRole('combobox', { name: 'services.filter.tag' }) as HTMLSelectElement;
const environmentSelect = () =>
  screen.getByRole('combobox', { name: 'services.filter.environment' }) as HTMLSelectElement;
const labels = (select: HTMLSelectElement) => Array.from(select.options, (option) => option.textContent);

/** Waited for, so no assertion races the four queries this page opens. */
const ready = () => screen.findByRole('combobox', { name: 'services.filter.tag' });

describe('Services, a filter whose target the page cannot name', () => {
  beforeEach(() => {
    tagsPending = false;
    tagsFail = false;
    tagRows = [TAG];
    environmentsPending = false;
    environmentsFail = false;
    environmentRows = [ENVIRONMENT];
    vi.clearAllMocks();
  });

  it('names the tag itself while the list still holds it', async () => {
    renderWithProviders(<Services />, { route: '/services?tag=5' });
    await ready();
    await waitFor(() => expect(labels(tagSelect())).toContain('edge'));
    expect(tagSelect().value).toBe('5');
    expect(labels(tagSelect())).not.toContain('services.filter.tag_gone');
    expect(labels(tagSelect())).not.toContain('services.filter.tag_unread');
  });

  it('calls the tag deleted once the list has come back without it', async () => {
    tagRows = [];
    renderWithProviders(<Services />, { route: '/services?tag=5' });
    await ready();
    await waitFor(() => expect(labels(tagSelect())).toContain('services.filter.tag_gone'));
    expect(labels(tagSelect())).not.toContain('services.filter.tag_unread');
    expect(tagSelect().value).toBe('5');
  });

  it('claims nothing about the tag while its list is still in flight', async () => {
    tagsPending = true;
    renderWithProviders(<Services />, { route: '/services?tag=5' });
    await ready();
    await waitFor(() => expect(labels(tagSelect())).toContain('services.filter.tag_unread'));
    expect(labels(tagSelect())).not.toContain('services.filter.tag_gone');
    expect(tagSelect().value).toBe('5');
  });

  it('claims nothing about the tag when its list could not be loaded', async () => {
    tagsFail = true;
    renderWithProviders(<Services />, { route: '/services?tag=5' });
    await ready();
    await waitFor(() => expect(labels(tagSelect())).toContain('services.filter.tag_unread'));
    expect(labels(tagSelect())).not.toContain('services.filter.tag_gone');
    expect(tagSelect().value).toBe('5');
  });

  it('adds nothing at all when no tag filter is applied', async () => {
    tagsFail = true;
    renderWithProviders(<Services />, { route: '/services' });
    await ready();
    expect(tagSelect().value).toBe('');
    expect(labels(tagSelect())).toEqual(['services.filter.all_tags']);
  });

  it('says the same about the environments, a second list of their own', async () => {
    environmentsFail = true;
    renderWithProviders(<Services />, { route: '/services?env=3' });
    await ready();
    await waitFor(() => expect(labels(environmentSelect())).toContain('services.filter.environment_unread'));
    expect(labels(environmentSelect())).not.toContain('services.filter.environment_gone');
    expect(environmentSelect().value).toBe('3');
  });

  it('calls the environment deleted once its own list has answered', async () => {
    environmentRows = [];
    renderWithProviders(<Services />, { route: '/services?env=3' });
    await ready();
    await waitFor(() => expect(labels(environmentSelect())).toContain('services.filter.environment_gone'));
    expect(environmentSelect().value).toBe('3');
  });

  it('judges the two lists apart, since either can be the one that failed', async () => {
    tagsFail = true;
    environmentRows = [];
    renderWithProviders(<Services />, { route: '/services?tag=5&env=3' });
    await ready();
    await waitFor(() => expect(labels(tagSelect())).toContain('services.filter.tag_unread'));
    expect(labels(environmentSelect())).toContain('services.filter.environment_gone');
    expect(labels(tagSelect())).not.toContain('services.filter.tag_gone');
    expect(labels(environmentSelect())).not.toContain('services.filter.environment_unread');
  });

  /**
   * The other half of the defect, and the reason the option has to exist rather than the
   * filter have to be dropped: the rows are filtered on the id whether or not anything can
   * name it. A control that reads "All tags" over a table missing most of its rows is the
   * page disagreeing with itself, and it is the table that was right.
   */
  it('still hides the routes the unnamed filter excludes', async () => {
    tagsFail = true;
    renderWithProviders(<Services />, { route: '/services?tag=5' });
    await ready();
    await waitFor(() => expect(screen.queryByText('api.example.test')).not.toBeNull());
    expect(screen.queryByText('www.example.test')).toBeNull();
    expect(labels(tagSelect())).toContain('services.filter.tag_unread');
  });
});
