/**
 * The import report lists one container error per line. The toast collapsed the line breaks
 * into spaces, which ran every error into the next one.
 */

import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { makeQueryClient } from '@/test/render';
import { useDockerDiscovery } from './useDockerDiscovery';

describe('useDockerDiscovery import report', () => {
  it('keeps the line breaks of the per-container errors', async () => {
    const qc = makeQueryClient();
    qc.setQueryData(['docker-endpoints'], []);
    qc.setQueryData(['domains'], ['example.com']);
    vi.spyOn(api, 'post').mockResolvedValue({ imported: 0, skipped: 0, errors: ['web: port in use', 'db: no route'] } as never);
    const error = vi.spyOn(toast, 'error');
    vi.spyOn(toast, 'success');

    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useDockerDiscovery(), { wrapper });

    act(() => result.current.importMutation.mutate());
    await waitFor(() => expect(error).toHaveBeenCalled());

    const [message, options] = error.mock.calls[0];
    expect(String(message).split('\n')).toEqual([
      'settings.docker.import_errors',
      'web: port in use',
      'db: no route',
    ]);
    expect(options?.style?.whiteSpace).toBe('pre-line');
    toast.remove();
  });
});
