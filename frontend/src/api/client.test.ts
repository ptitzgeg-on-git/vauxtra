/**
 * The client every API call goes through, built on `fetch`.
 *
 * What the rest of the panel relies on: a 2xx resolves to the parsed body, anything else
 * rejects with an `HttpError` carrying the status and the body, a call that never got an
 * answer rejects with an `HttpError` without a response, and an aborted call rejects with
 * the browser's `AbortError`, which the error readers treat as no failure at all.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { getErrorMessage, isCanceledError, isHttpStatus, isNetworkError } from '@/lib/errors';
import { HttpError } from './httpError';
import { api } from './client';

function answer(status: number, body: string, headers: Record<string, string> = {}) {
  return vi.fn(() => Promise.resolve(new Response(body || null, { status, headers })));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api', () => {
  it('resolves to the parsed body and sends JSON with the session cookie', async () => {
    const fetchMock = answer(200, '{"id":7}', { 'Content-Type': 'application/json' });
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.post('/domains', { name: 'example.test' })).resolves.toEqual({ id: 7 });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/domains');
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"name":"example.test"}');
    expect(init.credentials).toBe('include');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('resolves an empty body to undefined', async () => {
    vi.stubGlobal('fetch', answer(204, ''));
    await expect(api.delete('/domains/1')).resolves.toBeUndefined();
  });

  it('rejects a non-2xx with the status, the body and the headers', async () => {
    vi.stubGlobal('fetch', answer(429, '{"detail":"Slow down"}', { 'Retry-After': '30' }));

    const err = await api.get('/services').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect(isHttpStatus(err, 429)).toBe(true);
    expect(getErrorMessage(err, 'fallback')).toBe('Slow down');
    expect((err as HttpError).response?.headers['retry-after']).toBe('30');
  });

  it('keeps a proxy error page as text', async () => {
    vi.stubGlobal('fetch', answer(502, 'Bad Gateway'));
    const err = await api.get('/services').catch((e: unknown) => e);
    expect((err as HttpError).response?.data).toBe('Bad Gateway');
  });

  it('reports a call that got no answer as a network error', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))));
    const err = await api.get('/services').catch((e: unknown) => e);
    expect(isNetworkError(err)).toBe(true);
  });

  it('lets an abort through as an abort', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new DOMException('aborted', 'AbortError'))));
    const err = await api.get('/services').catch((e: unknown) => e);
    expect(isCanceledError(err)).toBe(true);
    expect(isNetworkError(err)).toBe(false);
  });

  it('announces an expired session, except on the auth routes', async () => {
    const seen = vi.fn();
    window.addEventListener('vauxtra:auth-expired', seen);
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(null, { status: 401 }))));

    await api.get('/services').catch(() => undefined);
    await api.post('/auth/login', { password: 'x' }).catch(() => undefined);

    window.removeEventListener('vauxtra:auth-expired', seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it('hands a download back as a Blob', async () => {
    vi.stubGlobal('fetch', answer(200, '{"version":1}'));
    const file = await api.get<Blob>('/backup', { responseType: 'blob' });
    // Not `toBeInstanceOf(Blob)`: under jsdom, `fetch` comes from Node and its Blob is not
    // jsdom's. In a browser there is one Blob and the check would hold.
    expect(await file.text()).toBe('{"version":1}');
  });
});
