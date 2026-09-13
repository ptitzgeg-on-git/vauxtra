/**
 * The sentence an operator reads when a call fails.
 *
 * `translateApiError` is the only place that decides whether a failure is about the action
 * the caller names or about something the caller cannot describe. The 5xx half of that
 * decision is not cosmetic: `app/api/providers.py`, `docker.py` and `webhooks.py` raise 502
 * on purpose wherever a provider, a Docker socket or a webhook target refuses, and argue the
 * choice in their own comments. This table is what keeps that distinction from being folded
 * back into a single `>= 500` line, and keeps the proxy case -- a 502 the API never wrote --
 * on the other side of it.
 */

import { describe, expect, it } from 'vitest';
import { translateApiError } from './errors';

/** `t` as the provider hands it out, returning the key so a case can assert on it. */
const t = (key: string) => key;

/** An axios rejection carrying a response, the shape the browser receives. */
function httpError(status: number, data: unknown = { detail: 'boom' }) {
  return {
    isAxiosError: true,
    message: `Request failed with status code ${status}`,
    response: { status, data },
  };
}

describe('translateApiError', () => {
  it('names the session, the scope and the quota rather than the action', () => {
    expect(translateApiError(httpError(401), t, 'fallback')).toBe('common.error.unauthorized');
    expect(translateApiError(httpError(403), t, 'fallback')).toBe('common.error.forbidden');
    expect(translateApiError(httpError(429), t, 'fallback')).toBe('common.error.rate_limited');
  });

  it('leaves a failure about the action itself to the fallback', () => {
    for (const status of [400, 404, 409, 422]) {
      expect(translateApiError(httpError(status), t, 'Failed to add domain')).toBe(
        'Failed to add domain',
      );
    }
  });

  it('blames the far end on a 502 the API wrote, not this server', () => {
    expect(translateApiError(httpError(502), t, 'fallback')).toBe('common.error.upstream');
  });

  it('blames this server on a 502 written by a proxy in front of the API', () => {
    const page = '<html><head><title>502 Bad Gateway</title></head></html>';
    expect(translateApiError(httpError(502, page), t, 'fallback')).toBe('common.error.server');
  });

  it('blames this server on the rest of the 5xx range', () => {
    for (const status of [500, 503, 504]) {
      expect(translateApiError(httpError(status), t, 'fallback')).toBe('common.error.server');
    }
  });

  it('says the server was never reached when no response came back', () => {
    const offline = { isAxiosError: true, message: 'Network Error' };
    expect(translateApiError(offline, t, 'fallback')).toBe('common.error.network');
  });
});
