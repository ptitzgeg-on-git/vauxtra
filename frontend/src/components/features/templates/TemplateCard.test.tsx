/**
 * The card's chip row, where both halves of the label control are shown together.
 *
 * One row, two halves, and the only thing separating them is which list a click toggles:
 * the two tables number their rows apart, so a tag and an environment may share an id, and
 * they may share a name as well because the server only refuses a duplicate within one
 * kind. `prod` the tag and `prod` the environment are both id 1 here on purpose. A row that
 * reads either half through the other would be green on every friendlier fixture.
 */

import { describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { Environment, Provider, Tag, Template } from '@/types/api';
import { TemplateCard } from './TemplateCard';

const TAG: Tag = { id: 1, name: 'prod', color: 'blue' };
const ENVIRONMENT: Environment = { id: 1, name: 'prod', color: 'blue' };
const PROVIDER: Provider = {
  id: 7,
  name: 'npm-home',
  type: 'npm',
  url: 'https://proxy.example.test',
  username: 'admin',
  enabled: true,
  extra: {},
  created_at: '2026-01-01T00:00:00Z',
};

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

interface CardOverrides {
  template?: Template;
  providers?: Provider[];
  providersUnread?: boolean;
  tags?: Tag[];
  environments?: Environment[];
  activeTagIds?: number[];
  activeEnvironmentIds?: number[];
}

function renderCard(over: CardOverrides = {}) {
  const onToggleTag = vi.fn();
  const onToggleEnvironment = vi.fn();
  const byId = <T extends { id: number }>(rows: T[]) => new Map(rows.map((row) => [row.id, row]));
  const view = renderWithProviders(
    <TemplateCard
      template={over.template ?? template()}
      providersById={byId(over.providers ?? [])}
      providersUnread={over.providersUnread ?? false}
      tagsById={byId(over.tags ?? [TAG])}
      environmentsById={byId(over.environments ?? [ENVIRONMENT])}
      activeTagIds={over.activeTagIds ?? []}
      onToggleTag={onToggleTag}
      activeEnvironmentIds={over.activeEnvironmentIds ?? []}
      onToggleEnvironment={onToggleEnvironment}
      onUse={vi.fn()}
      onEdit={vi.fn()}
      onDuplicate={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
  return { ...view, onToggleTag, onToggleEnvironment };
}

/** The row itself, absent entirely when the template names nothing. */
const labelRow = () => screen.queryByRole('group', { name: 'templates.card.labels' });

/**
 * A chip by the half it came from. The title is the one thing on a chip that says which
 * half it is in words, and it is the bare key here because `t()` gives the key back.
 */
const tagChip = () => screen.getByTitle('templates.card.filter_by_tag');
const environmentChip = () => screen.getByTitle('templates.card.filter_by_environment');

/** The dot in front of the name, where the label's own colour rides. */
const dot = (chip: HTMLElement) => chip.querySelector<HTMLElement>('span.h-2.w-2');

describe('the chip row shows both halves and keeps them apart', () => {
  it('draws one chip per label, tags first and environments after', () => {
    renderCard({ template: template({ tag_ids: [1], environment_ids: [1] }) });
    const chips = within(labelRow() as HTMLElement).getAllByRole('button');
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveAttribute('title', 'templates.card.filter_by_tag');
    expect(chips[1]).toHaveAttribute('title', 'templates.card.filter_by_environment');
  });

  it('draws two chips for a tag and an environment that share an id and a name', () => {
    // `tags` and `environments` are separate AUTOINCREMENT tables, so both hold an id 1, and
    // the server only refuses a duplicate name within one of them. Nothing but the half
    // itself separates these two, which is the case a row that reads one half through the
    // other passes anyway.
    renderCard({ template: template({ tag_ids: [1], environment_ids: [1] }) });
    expect(tagChip()).toHaveTextContent('prod');
    expect(environmentChip()).toHaveTextContent('prod');
    expect(tagChip()).not.toBe(environmentChip());
  });

  it('tints the two dots apart even when the labels carry the same colour', () => {
    renderCard({
      tags: [{ id: 1, name: 'prod', color: '' }],
      environments: [{ id: 1, name: 'prod', color: '' }],
      template: template({ tag_ids: [1], environment_ids: [1] }),
    });
    expect(dot(tagChip())).toHaveClass('bg-primary');
    expect(dot(environmentChip())).toHaveClass('bg-info');
  });

  it('puts the label own colour on the dot, leaving the tone to say the half', () => {
    renderCard({
      tags: [{ id: 1, name: 'prod', color: '#123456' }],
      template: template({ tag_ids: [1] }),
    });
    expect(dot(tagChip())).toHaveStyle({ backgroundColor: '#123456' });
    expect(dot(tagChip())).toHaveClass('bg-primary');
  });
});

describe('a chip toggles the filter of its own half and no other', () => {
  it('filters on the tag when the tag chip is clicked', async () => {
    const user = userEvent.setup();
    const { onToggleTag, onToggleEnvironment } = renderCard({
      template: template({ tag_ids: [1], environment_ids: [1] }),
    });
    await user.click(tagChip());
    expect(onToggleTag).toHaveBeenCalledWith(1);
    expect(onToggleEnvironment).not.toHaveBeenCalled();
  });

  it('filters on the environment when the environment chip is clicked', async () => {
    const user = userEvent.setup();
    const { onToggleTag, onToggleEnvironment } = renderCard({
      template: template({ tag_ids: [1], environment_ids: [1] }),
    });
    await user.click(environmentChip());
    expect(onToggleEnvironment).toHaveBeenCalledWith(1);
    expect(onToggleTag).not.toHaveBeenCalled();
  });

  it('reads pressed from its own half filter, not from the id being active anywhere', () => {
    renderCard({
      template: template({ tag_ids: [1], environment_ids: [1] }),
      activeTagIds: [1],
      activeEnvironmentIds: [],
    });
    expect(tagChip()).toHaveAttribute('aria-pressed', 'true');
    expect(environmentChip()).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('what the row does with a label it cannot name', () => {
  it('skips a label whose row is gone rather than drawing a nameless chip', () => {
    // The id lists are TEXT no constraint reaches, so a deleted label leaves its id behind.
    renderCard({ template: template({ tag_ids: [1], environment_ids: [9] }) });
    expect(within(labelRow() as HTMLElement).getAllByRole('button')).toHaveLength(1);
    expect(screen.queryByTitle('templates.card.filter_by_environment')).toBeNull();
  });

  it('draws no row at all for a template that names neither half', () => {
    renderCard();
    expect(labelRow()).toBeNull();
  });

  it('reads a template stored before the second half existed', () => {
    // `environment_ids` arrived with a migration; a row read from an older backup has only
    // the one key, and the card must show the half it does have rather than throw.
    const stored: Partial<Template> = template({ tag_ids: [1] });
    delete stored.environment_ids;
    renderCard({ template: stored as Template });
    expect(within(labelRow() as HTMLElement).getAllByRole('button')).toHaveLength(1);
    expect(tagChip()).toHaveTextContent('prod');
  });
});

describe('what the card says about the integration a template names', () => {
  /**
   * The card is handed a map of providers and an id out of the template, and until now an id
   * the map did not hold meant one thing: the integration was deleted. The map is empty for
   * a second reason as well, and it is the common one -- `/providers` is still in flight, or
   * it failed -- and the card then printed "Provider removed" in warning colour about an
   * integration nobody had touched. `providersUnread` is the card being told which of the
   * two an empty map is.
   */
  it('names the integration when the catalogue holds it', () => {
    renderCard({
      template: template({ proxy_provider_id: 7 }),
      providers: [PROVIDER],
    });
    expect(screen.getByText('npm-home')).toBeInTheDocument();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
    expect(screen.queryByText('templates.card.provider_unread')).toBeNull();
  });

  it('says the integration was removed when the catalogue came back without it', () => {
    // The control. A catalogue that answered and does not hold id 7 is the one case where
    // "removed" is a measurement and not a guess, and it must keep its warning colour.
    renderCard({
      template: template({ proxy_provider_id: 7 }),
      providers: [],
      providersUnread: false,
    });
    const removed = screen.getByText('templates.card.provider_missing');
    expect(removed).toBeInTheDocument();
    expect(removed).toHaveClass('text-warning');
  });

  it('says the list is unread instead of removed when the catalogue never answered', () => {
    renderCard({
      template: template({ proxy_provider_id: 7 }),
      providers: [],
      providersUnread: true,
    });
    expect(screen.getByText('templates.card.provider_unread')).toBeInTheDocument();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
  });

  it('leaves the unread line in the muted colour the card uses for what it does not know', () => {
    renderCard({
      template: template({ proxy_provider_id: 7 }),
      providers: [],
      providersUnread: true,
    });
    expect(screen.getByText('templates.card.provider_unread')).not.toHaveClass('text-warning');
  });

  it('says it once per line a template names, proxy and DNS alike', () => {
    renderCard({
      template: template({ proxy_provider_id: 7, dns_provider_id: 8 }),
      providers: [],
      providersUnread: true,
    });
    expect(screen.getAllByText('templates.card.provider_unread')).toHaveLength(2);
  });

  it('says it of the tunnel line too, the only one a tunnel template draws', () => {
    renderCard({
      template: template({ expose_mode: 'tunnel', tunnel_provider_id: 7 }),
      providers: [],
      providersUnread: true,
    });
    expect(screen.getByText('templates.card.provider_unread')).toBeInTheDocument();
    expect(screen.queryByText('templates.card.proxy')).toBeNull();
  });

  it('draws no line at all for a template that names no integration', () => {
    // An unread catalogue must not invent a line either: the template holds no id, so there
    // is nothing to be unsure about.
    renderCard({ providers: [], providersUnread: true });
    expect(screen.queryByText('templates.card.provider_unread')).toBeNull();
    expect(screen.queryByText('templates.card.provider_missing')).toBeNull();
  });

  it('still names the ones it holds while the id it does not stays unread', () => {
    renderCard({
      template: template({ proxy_provider_id: 7, dns_provider_id: 8 }),
      providers: [PROVIDER],
      providersUnread: true,
    });
    expect(screen.getByText('npm-home')).toBeInTheDocument();
    expect(screen.getAllByText('templates.card.provider_unread')).toHaveLength(1);
  });
});
