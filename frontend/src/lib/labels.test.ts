import { describe, expect, it } from 'vitest';
import { LABEL_COLORS, labelColor, labelDotStyle } from './labels';

describe('labelColor', () => {
  it('sends every name the picker offers through its theme token', () => {
    for (const name of LABEL_COLORS) {
      expect(labelColor(name)).toBe(`rgb(var(--vx-label-${name}))`);
    }
  });

  it('matches the stored name whatever its case', () => {
    expect(labelColor('Blue')).toBe('rgb(var(--vx-label-blue))');
  });

  it('leaves a colour it does not name untouched', () => {
    expect(labelColor('#ff8800')).toBe('#ff8800');
  });
});

describe('labelDotStyle', () => {
  it('paints the tuned colour, not the raw keyword', () => {
    expect(labelDotStyle('purple')).toEqual({ backgroundColor: 'rgb(var(--vx-label-purple))' });
  });

  it('draws nothing for a label without a colour', () => {
    expect(labelDotStyle('')).toBeUndefined();
    expect(labelDotStyle(null)).toBeUndefined();
  });
});
