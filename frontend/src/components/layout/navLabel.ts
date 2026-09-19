/**
 * The accessible name of one navigation entry.
 *
 * `NavItem.alert` is documented in `Sidebar.tsx` as the field that exists so "colour is
 * never the only signal", and the expanded sidebar does draw a warning glyph for it. But
 * that glyph is `aria-hidden`, and the collapsed rail draws no glyph at all -- so an estate
 * with an expired certificate reached a screen reader as "Certificates, 1", the same three
 * words a renewal due next week produces, and reached a sighted reader at rail width as a
 * red badge and nothing else.
 *
 * The count and the alert both belong in the name, because an `aria-label` replaces the
 * whole subtree: whatever is not spelt here is not spoken.
 */

export interface NavName {
  label: string;
  badge?: string;
  alert?: boolean;
}

export function navItemName(
  { label, badge, alert }: NavName,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (badge === undefined) {
    return alert ? t('layout.nav.item_alert', { label }) : label;
  }
  const key = alert ? 'layout.nav.item_alert_with_badge' : 'layout.nav.item_with_badge';
  return t(key, { label, badge });
}
