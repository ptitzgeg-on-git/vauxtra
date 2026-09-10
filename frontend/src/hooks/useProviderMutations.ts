/**
 * Shared hook for provider validate-draft / create / update / delete mutations.
 * Used by the Setup wizard and by ProviderModal on the main panel.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT, type TranslateFn } from '@/i18n';
import { isHttpStatus, translateApiError } from '@/lib/errors';
import type { ProviderDeleteConflict, ProviderUpdate } from '@/types/api';
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
  /** Called after a successful update (edit mode). */
  onUpdated?: () => void | Promise<void>;
}

export interface ProviderUpdateVars {
  id: number;
  /** `PUT /providers/{id}` body; leave `password` empty to keep the stored secret. */
  data: ProviderUpdate;
}

export interface ProviderDeleteVars {
  id: number;
  /** Cut the link to every service still pointing at this provider. Ask the user first. */
  force?: boolean;
}

/** The 409 body of `DELETE /providers/{id}`: the services that still point at it. */
export function isProviderDeleteConflict(detail: unknown): detail is ProviderDeleteConflict {
  return Boolean(detail) && typeof detail === 'object' && Array.isArray((detail as ProviderDeleteConflict).services);
}

/**
 * The bullet list shown before forcing the delete through. The API names the role each
 * service fills (proxy, dns, tunnel, extra dns): which link is about to be cut is what
 * decides whether to go ahead. Five rows at most, then a line counting the rest.
 */
export function describeDeleteConflict(detail: ProviderDeleteConflict, t: TranslateFn): string {
  const list = detail.services
    .slice(0, 5)
    .map((s) => (s.roles?.length ? `${s.fqdn} (${s.roles.join(', ')})` : s.fqdn))
    .join('\n\u2022 ');
  const rest = detail.services.length - 5;
  const suffix = rest > 0 ? `\n${t('providers.delete.deps_more', { count: rest })}` : '';
  return `\u2022 ${list}${suffix}`;
}

export function useProviderMutations(
  formData: ProviderFormState,
  setValidationResult: (r: ProviderValidationResult | null) => void,
  opts?: UseProviderMutationsOpts,
) {
  const t = useT();
  const queryClient = useQueryClient();

  const payload = () => buildPayload({
    ...formData,
    name: formData.name.trim() || formData.type,
  });

  /**
   * Every cache entry a provider write can invalidate.
   *
   * The three list-level keys were the only ones refreshed, so the per-provider panels the
   * inspector reads (`ProviderInspector.tsx`) kept showing the state from before the edit:
   * a re-pointed URL still reported the old host as healthy, and the proxy-host / DNS-record
   * tables still listed what the previous credentials had returned. The prefixes below match
   * every `[key, id]` entry at once.
   */
  const invalidateProviderQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['providers'] });
    queryClient.invalidateQueries({ queryKey: ['providers-health'] });
    queryClient.invalidateQueries({ queryKey: ['providers-tunnel-health'] });
    queryClient.invalidateQueries({ queryKey: ['provider-health'] });
    queryClient.invalidateQueries({ queryKey: ['provider-proxy-hosts'] });
    queryClient.invalidateQueries({ queryKey: ['provider-dns-records'] });
  };

  const validateDraft = useMutation({
    mutationFn: async () =>
      api.post<ProviderValidationResult>('/providers/validate-draft', {
        ...payload(),
        write_probe: false,
      }),
    onSuccess: (data: ProviderValidationResult) => {
      setValidationResult(data ?? null);
      if (data?.ok) toast.success(t('provider_modal.toast.validation_ok'));
      else toast.error(t('provider_modal.toast.validation_issues'));
    },
    onError: (err: unknown) => {
      setValidationResult(null);
      toast.error(translateApiError(err, t, t('provider_modal.toast.validation_failed')));
    },
  });

  const createProvider = useMutation({
    mutationFn: () => api.post('/providers', payload()),
    onSuccess: async () => {
      invalidateProviderQueries();
      toast.success(t('provider_modal.toast.created'));
      await opts?.onCreated?.();
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('provider_modal.toast.create_failed')));
    },
  });

  const updateProvider = useMutation({
    mutationFn: ({ id, data }: ProviderUpdateVars) => api.put<{ ok: boolean }>(`/providers/${id}`, data),
    onSuccess: async () => {
      invalidateProviderQueries();
      toast.success(t('provider_modal.toast.updated'));
      await opts?.onUpdated?.();
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('provider_modal.toast.update_failed')));
    },
  });

  /**
   * `force` is opt-in: without it the API answers 409 and lists the services that would be
   * cut loose (`isProviderDeleteConflict`), which is the caller's cue to ask. It used to be
   * hardcoded on, so the wizard unlinked every service silently.
   */
  const deleteProvider = useMutation({
    mutationFn: ({ id, force }: ProviderDeleteVars) =>
      api.delete(`/providers/${id}${force ? '?force=true' : ''}`),
    onSuccess: async () => {
      invalidateProviderQueries();
      toast.success(t('provider_modal.toast.deleted'));
      await opts?.onDeleted?.();
    },
    onError: (err: unknown) => {
      // A 409 is not a failure to report: the caller turns it into the force prompt.
      if (isHttpStatus(err, 409)) return;
      toast.error(translateApiError(err, t, t('provider_modal.toast.delete_failed')));
    },
  });

  return { validateDraft, createProvider, updateProvider, deleteProvider };
}
