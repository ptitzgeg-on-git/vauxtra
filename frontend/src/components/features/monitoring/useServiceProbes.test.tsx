/**
 * The latency column is fed by two routes, and it used to listen to only one.
 *
 * `check-all` opens a TCP connection to every service, so it measures every latency on the
 * way through; it just threw the numbers away. The column therefore stayed empty after a
 * fleet run, and a footnote told operators to go and check rows one at a time. `record()`
 * is the other mouth of the same store. The per-row check also moved from GET to POST,
 * because it writes.
 */

import type { ReactNode } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { makeQueryClient } from '@/test/render';
import type { CheckAllEntry } from '@/types/api';
import { useServiceProbes } from './useServiceProbes';

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn(() => Promise.resolve({})),
    post: vi.fn(() =>
      Promise.resolve({ id: 1, status: 'ok', latency_ms: 12.5, dns_resolved: ['203.0.113.7'] }),
    ),
  },
}));

const { api } = await import('@/api/client');

function wrapper({ children }: { children: ReactNode }) {
  const client = makeQueryClient();
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const FLEET: CheckAllEntry[] = [
  { id: 1, status: 'ok', latency_ms: 4 },
  { id: 2, status: 'error', latency_ms: null },
];

describe('useServiceProbes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('checks one service with POST, the verb of a route that writes', async () => {
    const { result } = renderHook(() => useServiceProbes(), { wrapper });

    act(() => result.current.check(1));

    await waitFor(() => expect(result.current.probes[1]).toBeDefined());
    expect(api.post).toHaveBeenCalledWith('/services/1/check');
    expect(api.get).not.toHaveBeenCalled();
    expect(result.current.probes[1].latencyMs).toBe(12.5);
    expect(result.current.probes[1].dns).toEqual(['203.0.113.7']);
  });

  it('folds what a fleet run measured into the same store', () => {
    const { result } = renderHook(() => useServiceProbes(), { wrapper });

    act(() => result.current.record(FLEET));

    expect(result.current.probes[1].latencyMs).toBe(4);
    expect(result.current.probes[1].status).toBe('ok');
    expect(result.current.average).toBe(4);
  });

  it('records an unreachable service without inventing a zero for it', () => {
    const { result } = renderHook(() => useServiceProbes(), { wrapper });

    act(() => result.current.record(FLEET));

    // A refused connection measured nothing. Zero would read as "answered instantly".
    expect(result.current.probes[2].latencyMs).toBeNull();
    expect(result.current.probes[2].status).toBe('error');
    expect(result.current.average).toBe(4);
  });

  it('keeps the hostname an earlier per-row check resolved', async () => {
    const { result } = renderHook(() => useServiceProbes(), { wrapper });

    act(() => result.current.check(1));
    await waitFor(() => expect(result.current.probes[1]?.dns).toEqual(['203.0.113.7']));

    // `check-all` never resolves anything, so it must not erase what the other route found.
    act(() => result.current.record([{ id: 1, status: 'ok', latency_ms: 9 }]));

    expect(result.current.probes[1].latencyMs).toBe(9);
    expect(result.current.probes[1].dns).toEqual(['203.0.113.7']);
  });

  it('leaves the store untouched when a run reported nothing', () => {
    const { result } = renderHook(() => useServiceProbes(), { wrapper });
    const before = result.current.probes;

    // An instance older than 1.5.0 answers `check-all` without `results`, and the page
    // passes the empty list through. The column must stay as it was, not be rebuilt.
    act(() => result.current.record([]));

    expect(result.current.probes).toBe(before);
  });
});
