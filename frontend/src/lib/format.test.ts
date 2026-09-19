/**
 * The version badge.
 *
 * `APP_VERSION` defaults to the word `dev` and is a free-form environment variable, so the
 * sidebar's hard-coded `v` prefix printed `vdev` on every development build, and would print
 * `vmain` or `v` plus a commit sha for anyone who sets it to a branch or a build id. The
 * dashboard pill next to it has always shown the same string bare, so the two disagreed.
 */

import { describe, expect, it } from 'vitest';
import { versionLabel } from './format';

describe('versionLabel', () => {
  it('prefixes a numbered release', () => {
    expect(versionLabel('1.4.0')).toBe('v1.4.0');
    expect(versionLabel('2026.09.1')).toBe('v2026.09.1');
  });

  it('leaves a word alone', () => {
    expect(versionLabel('dev')).toBe('dev');
    expect(versionLabel('main')).toBe('main');
    expect(versionLabel('rc-3')).toBe('rc-3');
  });

  it('answers an em dash when nothing was reported', () => {
    expect(versionLabel(undefined)).toBe('—');
    expect(versionLabel(null)).toBe('—');
    expect(versionLabel('   ')).toBe('—');
  });
});
