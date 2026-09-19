/**
 * The pure half of the Templates screen: what a stored template becomes on the form, and
 * what the form sends back.
 *
 * `toTemplateIn` is where a template stopped carrying its environments. The form held both
 * halves of the label control and the body it built named one of them, so the save answered
 * 201 and the template came back naming no environment -- which is what a template where
 * none was chosen also looks like. Every case below runs over both halves for that reason:
 * a test that spells the tag half out and leaves the environment half to a second case
 * written by hand beside it has the same shape the defect had.
 */

import { describe, expect, it } from 'vitest';
import type { Environment, Tag, Template } from '@/types/api';
import { emptyTemplateForm, labelFacets, searchHaystack, toTemplateForm, toTemplateIn } from './types';

/** The two halves of the label control, by the key each is stored and sent under. */
const HALVES = ['tag_ids', 'environment_ids'] as const;

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

const label = (id: number, name: string, color = 'blue'): Tag & Environment => ({ id, name, color });

describe('both halves of the label control survive the round trip', () => {
  it('reads each half off the stored template', () => {
    for (const key of HALVES) {
      expect(toTemplateForm(template({ [key]: [7, 9] }))[key]).toEqual([7, 9]);
    }
  });

  it('reads a half the template does not carry at all as naming nothing', () => {
    // A template written before environments were storable answers with no such key. The
    // form has to open on it, and `[]` is what it named.
    for (const key of HALVES) {
      const stored = { ...template({ tag_ids: [1], environment_ids: [2] }) } as Record<string, unknown>;
      delete stored[key];
      expect(toTemplateForm(stored as unknown as Template)[key]).toEqual([]);
    }
  });

  it('drops an entry that is not a number rather than sending it on', () => {
    for (const key of HALVES) {
      const stored = { ...template(), [key]: [1, null, 'two', 3] } as unknown as Template;
      expect(toTemplateForm(stored)[key]).toEqual([1, 3]);
    }
  });

  it('sends both halves back, which is the whole of this defect', () => {
    const body = toTemplateIn({ ...emptyTemplateForm, name: 'std', tag_ids: [1], environment_ids: [2] });
    expect(body.tag_ids).toEqual([1]);
    expect(body.environment_ids).toEqual([2]);
  });

  it('sends a copy of each list, so the form is not edited by the save', () => {
    const form = { ...emptyTemplateForm, name: 'std', tag_ids: [1], environment_ids: [2] };
    const body = toTemplateIn(form);
    for (const key of HALVES) {
      body[key].push(99);
      expect(form[key]).not.toContain(99);
    }
  });
});

describe('labelFacets', () => {
  const byId = new Map<number, Tag | Environment>([
    [1, label(1, 'zeta')],
    [2, label(2, 'alpha')],
  ]);

  it('counts, for either half, only what the templates actually name', () => {
    for (const key of HALVES) {
      const facets = labelFacets([template({ [key]: [1, 2] }), template({ id: 2, [key]: [2] })], key, byId);
      expect(facets.map((f) => [f.label.name, f.count])).toEqual([
        ['alpha', 2],
        ['zeta', 1],
      ]);
    }
  });

  it('offers no filter for a label whose row is gone', () => {
    // `_drop_dead_labels` (`app/api/templates.py`) takes the id off the template on the next
    // read, so a chip for it would match nothing and vanish on its own.
    for (const key of HALVES) {
      expect(labelFacets([template({ [key]: [404] })], key, byId)).toEqual([]);
    }
  });

  it('reads one half without seeing the other', () => {
    // A tag and an environment may share an id: the two tables number their rows apart.
    const both = template({ tag_ids: [1], environment_ids: [2] });
    expect(labelFacets([both], 'tag_ids', byId).map((f) => f.label.name)).toEqual(['zeta']);
    expect(labelFacets([both], 'environment_ids', byId).map((f) => f.label.name)).toEqual(['alpha']);
  });
});

describe('searchHaystack', () => {
  it('carries the names it is given, whichever half they came from', () => {
    const hay = searchHaystack(template({ name: 'Standard', target_port: 8096 }), ['prod', 'staging']);
    for (const needle of ['standard', 'example.test', '8096', 'prod', 'staging']) {
      expect(hay).toContain(needle);
    }
  });
});
