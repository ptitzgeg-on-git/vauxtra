/**
 * How one half of the label control is drawn, in the one place both pages read it from.
 *
 * A service carries *tags* and is set to *environments*; a template names both. Wherever the
 * two appear side by side the reader has to be able to tell which is which, and the name
 * alone cannot: the server refuses a duplicate name within one kind and not across the two,
 * so `prod` the tag and `prod` the environment are both allowed and look identical. The tone
 * is what separates them, and it is fixed per half rather than taken from the label's own
 * colour -- the colour is the label's, the tone is its kind's.
 *
 * The Services list settled this first. Templates drew its tag chips another way for as long
 * as it had only tags to draw, which was fine until the environments arrived beside them.
 */
import type { Tone } from '@/components/ui';

/** Which half of the label control a chip belongs to. */
export type LabelHalf = 'tag' | 'environment';

/** The tone that says which half this is, the same on every page that draws one. */
export const labelTone = (half: LabelHalf): Tone => (half === 'tag' ? 'primary' : 'info');

/** The dot in front of the name, tinted the half's tone so a colourless label still reads. */
export const labelDotClass = (half: LabelHalf): string =>
  `inline-block h-2 w-2 rounded-full ${half === 'tag' ? 'bg-primary' : 'bg-info'}`;

/**
 * The label's own colour, or nothing at all. A label may have none, and an inline style of
 * `undefined` leaves the dot on its tone class rather than painting it transparent.
 */
export const labelDotStyle = (color: string | null | undefined) =>
  color ? { backgroundColor: color } : undefined;
