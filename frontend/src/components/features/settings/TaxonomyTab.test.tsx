/**
 * What a label is holding, and which question deleting it asks.
 *
 * The two taxonomy lists were the only place in this product where a deletion counted
 * nothing. A provider names the services, the templates and the webhooks that point at it; a
 * root domain names the services and templates built on it; a tag on one service and a tag on
 * forty asked the same one-line question, over the two lists that are the easiest thing here
 * to delete by accident.
 *
 * The other half was not said at all. A tag is held in two places that behave nothing alike:
 * `service_tags` cascades, so the services are unlinked on the spot and go on being
 * published; `service_templates.tag_ids_json` is TEXT that no constraint reaches, so the id
 * survives the delete and is dropped on the next read, and the next service built from that
 * template starts without the tag. An environment has only the first of those, because no
 * template names an environment -- which is why the template sentence must never appear over
 * one, even though `tags` and `environments` are separate AUTOINCREMENT tables whose first
 * rows are both id 1.
 *
 * `t()` gives back the key here -- `renderWithProviders` leaves out `I18nProvider` on purpose
 * -- so these assertions survive any rewording in the eight locale files. What they pin is
 * which sentence is asked, and over which rows.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { makeQueryClient, renderWithProviders } from '@/test/render';
import { api } from '@/api/client';
import type { Environment, Service, Tag, Template } from '@/types/api';

let tagRows: Tag[] = [];
let envRows: Environment[] = [];
let serviceRows: Service[] = [];
let templateRows: Template[] = [];

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path === '/tags') return Promise.resolve(tagRows);
      if (path === '/environments') return Promise.resolve(envRows);
      if (path === '/services') return Promise.resolve(serviceRows);
      if (path === '/templates') return Promise.resolve(templateRows);
      return Promise.resolve([]);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
    put: vi.fn(() => Promise.resolve({ ok: true })),
    delete: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { TaxonomyTab } = await import('./TaxonomyTab');

/** The two lists the tab edits side by side, spelled as the locale prefixes are. */
type Kind = 'tags' | 'env';

const TAG: Tag = { id: 1, name: 'prod', color: 'blue' };
const ENV: Environment = { id: 1, name: 'staging', color: 'green' };

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
    icon_url: '',
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

/** Services holding the label, one per hostname. */
function holders(kind: Kind, ...hosts: string[]): Service[] {
  return hosts.map((host, i) => {
    const [sub, ...rest] = host.split('.');
    const held = kind === 'tags' ? { tags: [TAG] } : { environments: [ENV] };
    return service({ id: i + 1, subdomain: sub, domain: rest.join('.'), ...held });
  });
}

/**
 * Renders the tab with the dependent rows already in cache.
 *
 * The dialog body is built at the moment the X is clicked, so a count that lands afterwards
 * never reaches it. Seeding the two shared keys makes that ordering a fact rather than a race:
 * `makeQueryClient` holds them fresh for ever, and the chip only appears once the list query
 * the mock answers has resolved, which is after both of these are already in place.
 */
function show(): void {
  const queryClient = makeQueryClient();
  queryClient.setQueryData(['services'], serviceRows);
  queryClient.setQueryData(['templates'], templateRows);
  renderWithProviders(<TaxonomyTab />, { queryClient });
}

/** The chip of the only label in one of the two lists. */
async function chipOf(kind: Kind): Promise<HTMLElement> {
  const list = await screen.findByLabelText(`settings.${kind}.title`);
  return within(list).getAllByRole('listitem')[0];
}

/** Renders the tab and opens the delete question for the only label in one of the two lists. */
async function askToDelete(kind: Kind): Promise<HTMLElement> {
  show();
  const chip = await chipOf(kind);
  await userEvent.click(within(chip).getByLabelText(`settings.${kind}.delete_aria`));
  return screen.findByRole('dialog');
}

const usageBadge = (chip: HTMLElement) => within(chip).queryByText('settings.taxonomy.usage_count');

describe('TaxonomyTab, what a label is holding', () => {
  beforeEach(() => {
    tagRows = [TAG];
    envRows = [ENV];
    serviceRows = [];
    templateRows = [];
    vi.clearAllMocks();
  });

  it('shows no count over a label nothing carries', async () => {
    // The environment is held and the tag is not, over the same single service. `tags` and
    // `environments` are separate AUTOINCREMENT tables, so both rows here are id 1, and a map
    // keyed by id alone would show the environment's count on the tag.
    serviceRows = holders('env', 'git.example.test');
    show();

    expect(usageBadge(await chipOf('env'))).toBeInTheDocument();
    expect(usageBadge(await chipOf('tags'))).toBeNull();
  });

  it('counts the services carrying the label', async () => {
    serviceRows = holders('tags', 'git.example.test', 'api.example.test');
    show();

    const chip = await chipOf('tags');
    // The digits are aria-hidden; the sentence beside them is what is read out loud.
    expect(within(chip).getByText('2')).toBeInTheDocument();
    expect(usageBadge(chip)).toBeInTheDocument();
  });

  it('leaves a template out of the number on the chip', async () => {
    // The chip counts what is published. A template publishes nothing, so counting it here
    // would put a number on a label no running service carries.
    templateRows = [template({ tag_ids: [TAG.id] })];
    show();

    expect(usageBadge(await chipOf('tags'))).toBeNull();
  });
});

describe('TaxonomyTab, which question deleting a label asks', () => {
  beforeEach(() => {
    tagRows = [TAG];
    envRows = [ENV];
    serviceRows = [];
    templateRows = [];
    vi.clearAllMocks();
  });

  it('asks the plain question over a label nothing holds', async () => {
    const dialog = await askToDelete('tags');

    // Nothing changes but the label, so there is nothing to list. Asking the long question
    // over a tag created by mistake a minute ago is how a confirmation stops being read.
    expect(within(dialog).getByText('settings.taxonomy.delete_message')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.taxonomy.in_use_carried')).toBeNull();
    expect(within(dialog).queryByText('settings.taxonomy.in_use_effect')).toBeNull();
  });

  it('names the services and what they keep when a tag is carried', async () => {
    serviceRows = holders('tags', 'git.example.test', 'api.example.test');
    const dialog = await askToDelete('tags');

    expect(within(dialog).getByText('settings.taxonomy.in_use_carried')).toBeInTheDocument();
    expect(within(dialog).getByText('git.example.test')).toBeInTheDocument();
    expect(within(dialog).getByText('api.example.test')).toBeInTheDocument();
    // The sentence the old one-liner never said: the rows above go on being published.
    expect(within(dialog).getByText('settings.taxonomy.in_use_effect')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.taxonomy.delete_message')).toBeNull();
  });

  it('says an environment is set, not carried', async () => {
    // One list, two sentences: a service carries a tag and is set to an environment, and the
    // single line both used to share could only be true of one of them.
    serviceRows = holders('env', 'git.example.test');
    const dialog = await askToDelete('env');

    expect(within(dialog).getByText('settings.taxonomy.in_use_set')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.taxonomy.in_use_carried')).toBeNull();
  });

  it('names the templates that come back one tag shorter', async () => {
    serviceRows = holders('tags', 'git.example.test');
    templateRows = [template({ id: 4, name: 'Reverse proxy', tag_ids: [TAG.id] })];
    const dialog = await askToDelete('tags');

    // Two disappearances that behave nothing alike, so two paragraphs. The services are
    // unlinked by the cascade and go on routing; the template keeps the dead id until the
    // next read, drops it silently then, and the next service built from it starts without.
    expect(within(dialog).getByText('settings.taxonomy.in_use_carried')).toBeInTheDocument();
    expect(within(dialog).getByText('settings.taxonomy.in_use_templates')).toBeInTheDocument();
    expect(within(dialog).getByText('settings.taxonomy.in_use_templates_effect')).toBeInTheDocument();
    expect(within(dialog).getByText('Reverse proxy')).toBeInTheDocument();
  });

  it('never names a template over an environment', async () => {
    // The adversarial case, and it is a real one: the two tables number their rows
    // independently, so this template names tag 1 while the environment being deleted is
    // also id 1. No template names an environment, and a dialog saying one did would be
    // telling the operator something untrue about a row the deletion does not touch.
    serviceRows = holders('env', 'git.example.test');
    templateRows = [template({ id: 4, name: 'Reverse proxy', tag_ids: [ENV.id] })];
    const dialog = await askToDelete('env');

    expect(within(dialog).getByText('settings.taxonomy.in_use_set')).toBeInTheDocument();
    expect(within(dialog).queryByText('settings.taxonomy.in_use_templates')).toBeNull();
    expect(within(dialog).queryByText('Reverse proxy')).toBeNull();
  });

  it('stops naming at five and counts the rest', async () => {
    const hosts = Array.from({ length: 7 }, (_, i) => `svc${i + 1}.example.test`);
    serviceRows = holders('tags', ...hosts);
    const dialog = await askToDelete('tags');

    // A dialog printing forty hostnames is a dialog nobody reads. The cap lives in
    // `DependentList` so the two "still in use" bodies in Settings cannot drift apart.
    for (const named of hosts.slice(0, 5)) {
      expect(within(dialog).getByText(named)).toBeInTheDocument();
    }
    for (const unnamed of hosts.slice(5)) {
      expect(within(dialog).queryByText(unnamed)).toBeNull();
    }
    expect(within(dialog).getByText('settings.taxonomy.in_use_more')).toBeInTheDocument();
  });

  it('deletes the label once the long question is answered', async () => {
    serviceRows = holders('tags', 'git.example.test');
    const dialog = await askToDelete('tags');

    await userEvent.click(within(dialog).getByRole('button', { name: 'common.delete' }));

    // The witness. A dialog that counted correctly and then asked the wrong route, or asked
    // nothing at all, would satisfy every assertion above it.
    expect(api.delete).toHaveBeenCalledWith(`/tags/${TAG.id}`);
  });
});
