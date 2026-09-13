/**
 * Shared hook for provider validate-draft / create / update / delete mutations.
 * Used by the Setup wizard and by ProviderModal on the main panel.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT, type TranslateFn } from '@/i18n';
import { isHttpStatus, translateApiError } from '@/lib/errors';
import type { ProviderDeleteConflict, ProviderDeleteResult, ProviderUpdate } from '@/types/api';
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
  /**
   * Take those services' records off the provider before it goes, instead of leaving them
   * live on a server Vauxtra will no longer be able to see. Only read with `force`.
   */
  withdraw?: boolean;
  /**
   * The provider's name, for the toast. The form this hook is built on holds the provider
   * being *created*, which in the wizard is never the one being deleted.
   */
  name?: string;
}

/**
 * The withdrawal checkbox lives inside the confirm dialog, and `confirm()` resolves to a
 * boolean and nothing else. The checkbox writes into this box; the caller reads it back once
 * the question is answered. A plain object rather than a `useRef`: it is built outside React.
 */
export interface WithdrawChoice {
  current: boolean;
}

/**
 * Ticked to start with: leaving records live on a server Vauxtra can no longer see is not
 * an outcome anyone asks for on purpose, and the box says plainly what unticking it keeps.
 */
export const WITHDRAW_BY_DEFAULT = true;

/** Builds the box, so the checkbox and the request cannot start on different answers. */
export function createWithdrawChoice(): WithdrawChoice {
  return { current: WITHDRAW_BY_DEFAULT };
}

/**
 * `?force=true&withdraw=true`, in that order, and nothing at all for a plain delete.
 * Exported because the Integrations page owns its own delete mutation (it has no provider
 * form to hand this hook) and the two must build the same URL.
 */
export function providerDeleteQuery({ force, withdraw }: ProviderDeleteVars): string {
  const params: string[] = [];
  if (force) params.push('force=true');
  if (force && withdraw) params.push('withdraw=true');
  return params.length ? `?${params.join('&')}` : '';
}

/** The 409 body of `DELETE /providers/{id}`: the services that still point at it. */
export function isProviderDeleteConflict(detail: unknown): detail is ProviderDeleteConflict {
  return Boolean(detail) && typeof detail === 'object' && Array.isArray((detail as ProviderDeleteConflict).services);
}

/**
 * Which title the confirm dialog wears. `deps_title` says "services still depend on it",
 * which is simply untrue when the only thing depending on it is a template. It lives beside
 * the guard that produces the detail, so the Integrations page and the Setup wizard cannot
 * answer this differently.
 */
export function providerConflictTitleKey(detail: ProviderDeleteConflict): string {
  return detail.services?.length ? 'providers.delete.deps_title' : 'providers.delete.tpl_title';
}


/**
 * What to say once the delete has gone through. A withdrawal that failed halfway is the one
 * outcome a plain "deleted" toast would hide: the integration is gone from Vauxtra and some
 * of its records are still live on the server, which is exactly the state the checkbox was
 * ticked to avoid. Returns null when there is nothing to add.
 */
export function describeWithdrawal(result: unknown, name: string, t: TranslateFn): string | null {
  const errors = (result as ProviderDeleteResult | null | undefined)?.errors;
  if (!Array.isArray(errors) || errors.length === 0) return null;
  return t('providers.delete.withdraw_failed', { count: errors.length, name });
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
    mutationFn: (vars: ProviderDeleteVars) =>
      api.delete<ProviderDeleteResult>(`/providers/${vars.id}${providerDeleteQuery(vars)}`),
    onSuccess: async (result: ProviderDeleteResult, vars: ProviderDeleteVars) => {
      invalidateProviderQueries();
      // The delete blanks the provider columns of every template that named it. Without
      // this the Templates page goes on showing the provider that is no longer there.
      if (result?.unlinked_templates?.length) {
        queryClient.invalidateQueries({ queryKey: ['templates'] });
      }
      const partial = describeWithdrawal(result, vars.name ?? '', t);
      if (partial) toast.error(partial, { duration: 8000 });
      else toast.success(t('provider_modal.toast.deleted'));
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
