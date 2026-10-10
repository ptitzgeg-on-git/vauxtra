/**
 * A bulk answer that changed nothing is not a success: `affected: 0` with no error used to
 * be reported under a green tick ("0 routes enabled").
 */

import { describe, expect, it } from 'vitest';
import { bulkOutcomeOf } from './helpers';

describe('bulkOutcomeOf', () => {
  it('is a success when routes were changed and nothing failed', () => {
    expect(bulkOutcomeOf({ ok: true, affected: 3, errors: [] })).toBe('success');
  });

  it('is a no-op, not a success, when nothing was affected', () => {
    expect(bulkOutcomeOf({ ok: true, affected: 0, errors: [] })).toBe('noop');
  });

  it('treats a missing or empty answer as a no-op', () => {
    expect(bulkOutcomeOf(undefined)).toBe('noop');
    expect(bulkOutcomeOf({})).toBe('noop');
  });

  it('puts errors first, whatever the count', () => {
    expect(bulkOutcomeOf({ ok: true, affected: 0, errors: ['npm: 502'] })).toBe('warning');
    expect(bulkOutcomeOf({ ok: true, affected: 2, errors: ['npm: 502'] })).toBe('warning');
  });
});
