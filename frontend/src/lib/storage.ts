/**
 * Reading and writing JSON in Web Storage without ever throwing.
 *
 * Storage can be missing, blocked by the browser, full, or hold a value written by an older
 * build. Every page used to wrap its own `getItem`/`JSON.parse`/`setItem` in a try/catch; this
 * is the one place that does it. A read that fails for any reason, or finds a value the
 * guard rejects, answers the fallback. A write that fails is dropped: the in-memory value
 * still wins, it just does not survive a reload.
 */

export type StorageArea = 'local' | 'session';

function storageOf(area: StorageArea): Storage {
  // Reading the property itself throws in some browsers when storage is blocked.
  return area === 'session' ? window.sessionStorage : window.localStorage;
}

/** The parsed value under `key` when `guard` accepts it, `fallback` otherwise. */
export function readJSON<T, F = T>(
  key: string,
  guard: (value: unknown) => value is T,
  fallback: F,
  area: StorageArea = 'local',
): T | F {
  try {
    const raw = storageOf(area).getItem(key);
    if (raw === null || raw === '') return fallback;
    const parsed: unknown = JSON.parse(raw);
    return guard(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

/** Writes `value` as JSON. `false` when storage refused it (private mode, quota). */
export function writeJSON(key: string, value: unknown, area: StorageArea = 'local'): boolean {
  try {
    storageOf(area).setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

/** Removes `key`, ignoring a storage that cannot be reached. */
export function removeKey(key: string, area: StorageArea = 'local'): void {
  try {
    storageOf(area).removeItem(key);
  } catch {
    // Nothing stored that we could reach, so nothing to remove.
  }
}

export const isString = (value: unknown): value is string => typeof value === 'string';
export const isBoolean = (value: unknown): value is boolean => typeof value === 'boolean';
export const isArray = <T = unknown>(value: unknown): value is T[] => Array.isArray(value);
