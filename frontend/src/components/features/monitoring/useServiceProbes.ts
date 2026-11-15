/**
 * On-demand checks: `POST /api/services/{sid}/check`, plus whatever a fleet run measured.
 *
 * The service row keeps the latency of its last probe; this store holds what was measured
 * since the page was opened, which is fresher and, for a per-row check, also carries the DNS
 * answer. Two routes produce it and both land here: the per-row check, and
 * `POST /api/services/check-all`, which reports what it measured through `record()`. Both
 * routes write `status`, `status_detail`, `latency_ms` and `last_checked`, hence the
 * invalidation of `['services']` and `['logs']` after a probe.
 */

import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import type { CheckAllEntry, ServiceCheckResult } from '@/types/api';
import type { LatencyProbes } from './uptime';

export interface ServiceProbes {
  /** Everything measured since the page was opened, keyed by service id. */
  probes: LatencyProbes;
  /** The service being checked right now, `null` when idle. */
  checkingId: number | null;
  check: (serviceId: number) => void;
  /**
   * Fold the per-service results of a fleet check into the same store. It carries no DNS
   * answer, so a hostname an earlier per-row check resolved is kept rather than erased.
   */
  record: (entries: CheckAllEntry[]) => void;
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

  const record = useCallback((entries: CheckAllEntry[]) => {
    if (entries.length === 0) return;
    const at = Date.now();
    setProbes((current) => {
      const next = { ...current };
      for (const entry of entries) {
        next[entry.id] = {
          latencyMs: typeof entry.latency_ms === 'number' ? entry.latency_ms : null,
          status: entry.status,
          at,
          dns: current[entry.id]?.dns ?? null,
        };
      }
      return next;
    });
  }, []);

  return { probes, checkingId, check, record };
}
