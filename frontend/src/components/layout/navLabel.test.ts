/**
 * `t()` returns its key here, so each assertion names the sentence it wants rather than
 * repeating one language's wording.
 */

import { describe, expect, it } from 'vitest';
import { navItemName } from './navLabel';

const t = (key: string) => key;

describe('navItemName', () => {
  it('leaves a plain entry alone', () => {
    expect(navItemName({ label: 'Dashboard' }, t)).toBe('Dashboard');
  });

  it('carries the count, because an aria-label replaces the badge it covers', () => {
    expect(navItemName({ label: 'Services', badge: '7' }, t)).toBe('layout.nav.item_with_badge');
  });

  it('says something is wrong, instead of leaving it to the badge colour', () => {
    // At rail width the warning glyph is not drawn at all; red on a number was the whole
    // signal, and a screen reader got the same three words as a renewal due next week.
    expect(navItemName({ label: 'Certificates', badge: '1', alert: true }, t)).toBe(
      'layout.nav.item_alert_with_badge',
    );
  });

  it('says it even with no count to hang it on', () => {
    expect(navItemName({ label: 'Monitoring', alert: true }, t)).toBe('layout.nav.item_alert');
  });
});
