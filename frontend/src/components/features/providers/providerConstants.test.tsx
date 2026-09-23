/**
 * Picking a type fills a name in, and must never fill an address in.
 *
 * The Integrations modal used to seed `url` from the type's `placeholder_url`. That string is
 * also the field's placeholder, so the box looked exactly as it does when empty -- same text,
 * and only the shade of grey between a hint and a value. Nine of the twelve types name
 * `http://192.168.1.10:3000`: the tenth address of the commonest home range, where a machine
 * usually does answer. Anyone who read the box as pre-filled-correctly, or as empty, typed a
 * username and a password next to it and pressed Validate, and the credentials went to
 * whatever is at that address.
 *
 * The first-run wizard, which shares this metadata and this type picker, has only ever seeded
 * the name -- so this was a stray line, not a design.
 */

import { describe, expect, it } from 'vitest';
import {
  describeMissing,
  emptyForm,
  firstIncompleteStep,
  getGuidedSteps,
  missingFields,
  requiredFields,
  seedFormForType,
  type ProviderFormState,
  type ProviderTypeMeta,
} from './providerConstants';

/** What a type's metadata offers. `placeholder_url` is the string that must not reach `url`. */
const PLACEHOLDER_URL = 'http://192.168.1.10:3000';

const TYPED_IN: ProviderFormState = {
  ...emptyForm,
  type: 'npm',
  name: 'NPM at the lab',
  url: 'http://127.0.0.1:3081',
  username: 'admin',
  password: 'secret',
};

describe('seedFormForType', () => {
  it('leaves the address empty when a type is picked', () => {
    expect(seedFormForType(emptyForm, 'adguard', 'AdGuard Home').url).toBe('');
  });

  it('never returns an address that was not already in the form', () => {
    // The only two answers allowed: what the user typed for this same type, or nothing.
    for (const type of ['adguard', 'npm', 'cloudflare', 'technitium', 'zoraxy']) {
      for (const start of [emptyForm, TYPED_IN]) {
        const { url } = seedFormForType(start, type, PLACEHOLDER_URL);
        expect([start.type === type ? start.url : '']).toContain(url);
      }
    }
  });

  it('clears an address typed for a different type', () => {
    // Otherwise the NPM address stays in the box under the AdGuard logo, and the AdGuard
    // password is validated against NPM.
    expect(seedFormForType(TYPED_IN, 'adguard', 'AdGuard Home').url).toBe('');
  });

  it('keeps the address when the same type is picked again', () => {
    const again = seedFormForType(TYPED_IN, 'npm', 'Nginx Proxy Manager');
    expect(again.url).toBe(TYPED_IN.url);
    expect(again.name).toBe(TYPED_IN.name);
  });

  it('seeds the name from the label, which is the one field it may fill', () => {
    expect(seedFormForType(emptyForm, 'adguard', 'AdGuard Home').name).toBe('AdGuard Home');
  });

  it('falls back to the type when the metadata carries no label', () => {
    expect(seedFormForType(emptyForm, 'adguard', 'adguard').name).toBe('adguard');
  });

  it('does not touch the credentials', () => {
    const next = seedFormForType(TYPED_IN, 'adguard', 'AdGuard Home');
    expect(next.username).toBe(TYPED_IN.username);
    expect(next.password).toBe(TYPED_IN.password);
  });
});

/**
 * Which fields a type cannot be validated without.
 *
 * Two rules used to answer, and they disagreed: the marks on the guided steps the API sends,
 * which the setup wizard's "Next" read too, and `canSubmitProvider`, which read a short local
 * list and nothing else. The NPM e-mail had a red asterisk in the guided wizard,
 * "Optional" in the expert form of the same dialog, and could be left blank all the way to a
 * failed login. One rule answers now, and every form reads it.
 */

type Mark = 'required' | 'optional';

/** A type's guided steps reduced to what the rule reads: which fields they ask, and how. */
function asked(...fields: Array<[string, Mark]>): ProviderTypeMeta {
  return {
    guided_steps: [
      { title: '', body: '', fields: fields.map(([key, mark]) => ({ key, label: key, optional: mark === 'optional' })) },
    ],
  };
}

/**
 * The ten types as `PROVIDER_TYPES` in `app/providers/factory.py` served them on 2026-09-22,
 * without `requires_username` or `requires_password`: the API sends neither.
 */
const SERVED: Array<[string, ProviderTypeMeta, Array<keyof ProviderFormState>]> = [
  ['npm', asked(['url', 'required'], ['username', 'required'], ['password', 'required']), ['name', 'url', 'username', 'password']],
  ['zoraxy', asked(['url', 'required'], ['username', 'optional'], ['password', 'optional']), ['name', 'url']],
  ['adguard', asked(['url', 'required'], ['username', 'required'], ['password', 'required']), ['name', 'url', 'username', 'password']],
  ['pihole', asked(['url', 'required'], ['password', 'required']), ['name', 'url', 'password']],
  ['traefik', asked(['url', 'required']), ['name', 'url']],
  ['cloudflare', asked(['password', 'required'], ['username', 'optional']), ['name', 'password']],
  ['technitium', asked(['url', 'required'], ['username', 'required'], ['password', 'required']), ['name', 'url', 'username', 'password']],
  ['powerdns', asked(['url', 'required'], ['username', 'optional'], ['password', 'required']), ['name', 'url', 'password']],
  ['desec', asked(['password', 'required'], ['username', 'optional'], ['url', 'optional']), ['name', 'password']],
  [
    'cloudflare_tunnel',
    asked(['tunnel_id', 'required'], ['password', 'required'], ['username', 'required']),
    ['name', 'username', 'password', 'tunnel_id'],
  ],
];

describe('requiredFields', () => {
  it.each(SERVED)('asks %s for what its login cannot go without', (type, meta, expected) => {
    expect(requiredFields(type, meta)).toEqual(expected);
  });

  it('requires the username a guided step asks for, even where the local list does not', () => {
    // NPM signs in with the e-mail, AdGuard Home with basic auth, Technitium with `user`.
    for (const type of ['npm', 'adguard', 'technitium']) {
      expect(requiredFields(type)).not.toContain('username');
      expect(requiredFields(type, SERVED.find(([t]) => t === type)?.[1])).toContain('username');
    }
  });

  it('lets an optional mark relax nothing the local list requires', () => {
    // A step that calls the Cloudflare token optional does not make it so: nothing logs in without it.
    expect(requiredFields('cloudflare', asked(['password', 'optional']))).toContain('password');
  });

  it('ignores a guided field that is not a field of the form', () => {
    expect(requiredFields('traefik', asked(['url', 'required'], ['zone', 'required']))).toEqual(['name', 'url']);
  });

  it('asks nothing before a type is picked', () => {
    expect(requiredFields('')).toEqual([]);
  });
});

describe('missingFields', () => {
  const npm = SERVED[0][1];

  it('lists the required fields still empty, blanks included, in the order the forms draw them', () => {
    const form = { ...emptyForm, type: 'npm', name: '  ', password: 'x' };
    expect(missingFields(form, npm)).toEqual(['name', 'url', 'username']);
  });

  it('leaves the secret out of an edit, which keeps the stored one when sent blank', () => {
    const form = { ...TYPED_IN, password: '' };
    expect(missingFields(form, npm)).toEqual(['password']);
    expect(missingFields(form, npm, { editMode: true })).toEqual([]);
  });
});

describe('The way through the guided steps', () => {
  const steps = getGuidedSteps('npm', {
    guided_steps: [
      { title: 'Where', body: '', fields: [{ key: 'url', label: 'Admin address' }] },
      { title: 'Who', body: '', fields: [{ key: 'username', label: 'E-mail' }] },
      { title: 'Secret', body: '', fields: [{ key: 'password', label: 'Password' }] },
    ],
  });
  const t = (key: string) => key;

  it('stops at the first step that still asks for something', () => {
    expect(firstIncompleteStep(steps, ['username', 'password'])).toBe(1);
  });

  it('reaches past the last step once nothing a step asks for is missing', () => {
    // The name is asked above the steps, not by one of them: it holds no step back.
    expect(firstIncompleteStep(steps, ['name'])).toBe(steps.length);
  });

  it('names a field as the step asks for it, and points to that step when it is another one', () => {
    expect(describeMissing(['name', 'url', 'password'], 'npm', undefined, t, steps, 2)).toEqual([
      { key: 'name', label: 'provider_modal.field.name', step: undefined },
      { key: 'url', label: 'Admin address', step: 0 },
      { key: 'password', label: 'Password', step: undefined },
    ]);
  });

  it('names a field as the expert form does when no step is given', () => {
    expect(describeMissing(['url', 'username'], 'npm', undefined, t)).toEqual([
      { key: 'url', label: 'provider_modal.field.url', step: undefined },
      { key: 'username', label: 'provider_modal.field.username', step: undefined },
    ]);
  });
});
