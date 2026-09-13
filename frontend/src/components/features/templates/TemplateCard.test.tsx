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
      providersById={new Map<number, Provider>()}
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
