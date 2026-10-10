import { describe, expect, it } from 'vitest';
import { clampWhole, commitWhole } from './format';

describe('clampWhole', () => {
  it('drops the fraction instead of sending a decimal the API refuses', () => {
    expect(clampWhole('2.5', 0, 60)).toBe(2);
    expect(clampWhole('59.9', 0, 60)).toBe(59);
  });

  it('keeps the value inside its bounds', () => {
    expect(clampWhole('-3', 0, 720)).toBe(0);
    expect(clampWhole('9999', 0, 1440)).toBe(1440);
  });

  it('reads an empty or unreadable field as the minimum', () => {
    expect(clampWhole('', 0, 60)).toBe(0);
    expect(clampWhole('abc', 0, 60)).toBe(0);
  });

  it('reads an overflowing number as the maximum', () => {
    expect(clampWhole('1e400', 0, 720)).toBe(720);
    expect(clampWhole('-1e400', 0, 720)).toBe(0);
  });
});

describe('commitWhole', () => {
  it('writes the clamped value back into the field it read', () => {
    const input = { value: '999' };
    expect(commitWhole(input, 0, 720)).toBe(720);
    expect(input.value).toBe('720');
  });
});
