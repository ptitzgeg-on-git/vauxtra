/**
 * The one reader and writer of Web Storage: it never throws, and it hands back only what
 * the guard accepts.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { isArray, isBoolean, isString, readJSON, removeKey, writeJSON } from './storage';

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe('readJSON', () => {
  it('reads back what writeJSON wrote', () => {
    writeJSON('k', { a: 1 });
    expect(readJSON('k', (v): v is { a: number } => typeof v === 'object' && v !== null, null)).toEqual({ a: 1 });
  });

  it('answers the fallback for a missing or empty key', () => {
    expect(readJSON('missing', isString, 'fb')).toBe('fb');
    localStorage.setItem('empty', '');
    expect(readJSON('empty', isString, 'fb')).toBe('fb');
  });

  it('answers the fallback for a value that is not JSON', () => {
    localStorage.setItem('k', '{not json');
    expect(readJSON('k', isString, 'fb')).toBe('fb');
  });

  it('answers the fallback when the guard rejects the value', () => {
    writeJSON('k', 42);
    expect(readJSON('k', isString, 'fb')).toBe('fb');
    expect(readJSON('k', isArray, undefined)).toBeUndefined();
  });

  it('reads booleans written as plain strings by earlier builds', () => {
    localStorage.setItem('k', 'true');
    expect(readJSON('k', isBoolean, false)).toBe(true);
  });

  it('keeps the two areas apart', () => {
    writeJSON('k', 'session value', 'session');
    expect(readJSON('k', isString, 'fb')).toBe('fb');
    expect(readJSON('k', isString, 'fb', 'session')).toBe('session value');
  });

  it('answers the fallback when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    expect(readJSON('k', isString, 'fb')).toBe('fb');
  });
});

describe('writeJSON', () => {
  it('writes JSON and reports success', () => {
    expect(writeJSON('k', [1, 2])).toBe(true);
    expect(localStorage.getItem('k')).toBe('[1,2]');
  });

  it('reports a refused write instead of throwing', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    expect(writeJSON('k', 'x')).toBe(false);
  });
});

describe('removeKey', () => {
  it('removes the key, and does not throw when storage does', () => {
    writeJSON('k', 1, 'session');
    removeKey('k', 'session');
    expect(sessionStorage.getItem('k')).toBeNull();
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    expect(() => removeKey('k')).not.toThrow();
  });
});
