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
import { emptyForm, seedFormForType, type ProviderFormState } from './providerConstants';

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
