/**
 * Shared hook for provider create / validate / delete mutations.
 * Used by both Setup wizard and ProviderModal on the main panel.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import {
  type ProviderFormState,
  type ProviderValidationResult,
  buildPayload,
} from '@/components/features/providers/providerConstants';

interface UseProviderMutationsOpts {
  /** Called after a successful create (e.g. close modal, navigate). */
  onCreated?: () => void | Promise<void>;
  /** Called after a successful delete. */
  onDeleted?: () => void | Promise<void>;
}

export function useProviderMutations(
  formData: ProviderFormState,
  setValidationResult: (r: ProviderValidationResult | null) => void,
  opts?: UseProviderMutationsOpts,
) {
  const queryClient = useQueryClient();

  const payload = () => buildPayload({
    ...formData,
    name: formData.name.trim() || formData.type,
  });

  const validateDraft = useMutation({
    mutationFn: async () =>
      api.post<ProviderValidationResult>('/providers/validate-draft', {
        ...payload(),
        write_probe: false,
      }),
    onSuccess: (data: ProviderValidationResult) => {
      setValidationResult(data ?? null);
      toast[data?.ok ? 'success' : 'error'](
        data?.ok ? 'Validation succeeded' : 'Validation found issues',
      );
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      setValidationResult(null);
      toast.error(axErr?.response?.data?.detail || 'Validation failed');
    },
  });

  const createProvider = useMutation({
    mutationFn: () => api.post('/providers', payload()),
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      toast.success('Provider linked successfully');
      await opts?.onCreated?.();
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to connect provider');
    },
  });

  const deleteProvider = useMutation({
    mutationFn: (pid: number) => api.delete(`/providers/${pid}?force=true`),
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
      toast.success('Provider removed');
      await opts?.onDeleted?.();
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to remove provider');
    },
  });

  return { validateDraft, createProvider, deleteProvider };
}
