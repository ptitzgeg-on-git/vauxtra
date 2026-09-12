/**
 * On-demand checks — `POST /api/services/{sid}/check`.
 *
 * This is the only route that measures latency, and it measures exactly one service, so
 * the probes are collected as the operator asks for them and kept in memory for the life
 * of the page. The route also writes `status` and `last_checked` on the service, hence the
 * invalidation of `['services']` and `['logs']` after every probe. It is a POST since
 * 1.5.0 for that reason: a route that writes is not a GET.
 */

import { useCallback, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import type { ServiceCheckResult } from '@/types/api';
import { averageLatency, type LatencyProbes } from './uptime';

export interface ServiceProbes {
  /** Everything measured since the page was opened, keyed by service id. */
  probes: LatencyProbes;
  /** The service being checked right now, `null` when idle. */
  checkingId: number | null;
  /** Mean of every latency measured this session, `null` while none has been. */
  average: number | null;
  check: (serviceId: number) => void;
}

export function useServiceProbes(): ServiceProbes {
  const t = useT();
  const queryClient = useQueryClient();
  const [probes, setProbes] = useState<LatencyProbes>({});
  const [checkingId, setCheckingId] = useState<number | null>(null);

  const mutation = useMutation({
    mutationFn: (serviceId: number) => api.post<ServiceCheckResult>(`/services/${serviceId}/check`),
    onMutate: (serviceId: number) => {
      setCheckingId(serviceId);
    },
    onSuccess: async (result, serviceId) => {
      setProbes((current) => ({
        ...current,
        [serviceId]: {
          latencyMs: typeof result.latency_ms === 'number' ? result.latency_ms : null,
          status: result.status,
          at: Date.now(),
          dns: Array.isArray(result.dns_resolved) ? result.dns_resolved : null,
        },
      }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['services'] }),
        queryClient.invalidateQueries({ queryKey: ['logs'] }),
      ]);
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('monitoring.toast.check_one_failed')));
    },
    onSettled: () => {
      setCheckingId(null);
    },
  });

  const { mutate } = mutation;
  const check = useCallback((serviceId: number) => mutate(serviceId), [mutate]);
  const average = useMemo(() => averageLatency(probes), [probes]);

  return { probes, checkingId, average, check };
}
