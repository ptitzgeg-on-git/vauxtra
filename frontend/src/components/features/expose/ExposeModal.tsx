import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Network, X } from 'lucide-react';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import { type FormState, type Provider, initialForm, toFormState, hasCapability } from './types';
import { ServiceForm } from './ServiceForm';
import { ServicePreview } from './ServicePreview';

interface ExposeModalProps {
  isOpen: boolean;
  onClose: () => void;
  mode?: 'create' | 'edit';
  service?: Record<string, unknown> | null;
}

interface TargetSuggestion {
  candidates: Array<{ value: string; source: string }>;
  recommended: string;
}

export function ExposeModal({ isOpen, onClose, mode = 'create', service = null }: ExposeModalProps) {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState<FormState>(() => toFormState(service));
  const isEditMode = mode === 'edit' && Boolean(service?.id);

  const { data: providers = [], isLoading: isLoadingProviders } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
    enabled: isOpen,
  });

  const { data: domains = [], isLoading: isLoadingDomains } = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
    enabled: isOpen,
  });

  const { data: providerTypes = {} } = useQuery<Record<string, Record<string, unknown>>>({
    queryKey: ['provider-types'],
    queryFn: () => api.get<Record<string, Record<string, unknown>>>('/providers/types'),
    enabled: isOpen,
  });

  const providerTypeMap = providerTypes;

  // Derived provider lists for preview
  const allProviders = providers.filter((p) => Boolean(p.enabled));
  const proxyProviders = allProviders.filter((p) => hasCapability(p, 'proxy', providerTypeMap));
  const dnsProviders = allProviders.filter((p) => hasCapability(p, 'dns', providerTypeMap));
  const tunnelProviders = proxyProviders.filter(
    (p) =>
      hasCapability(p, 'supports_tunnel', providerTypeMap) ||
      (p.type || '').toLowerCase() === 'cloudflare_tunnel',
  );

  const selectedProxy = proxyProviders.find((p) => String(p.id) === formData.proxy_provider_id);
  const selectedDns = dnsProviders.find((p) => String(p.id) === formData.dns_provider_id);
  const selectedTunnel = tunnelProviders.find((p) => String(p.id) === formData.tunnel_provider_id);
  const selectedExtraProxies = useMemo(
    () => proxyProviders.filter((p) => formData.extra_proxy_provider_ids.includes(String(p.id))),
    [proxyProviders, formData.extra_proxy_provider_ids],
  );
  const selectedExtraDns = useMemo(
    () => dnsProviders.filter((p) => formData.extra_dns_provider_ids.includes(String(p.id))),
    [dnsProviders, formData.extra_dns_provider_ids],
  );

  // Public target suggestion
  const suggestionUrl = formData.proxy_provider_id
    ? `/services/public-target/suggest?proxy_provider_id=${encodeURIComponent(formData.proxy_provider_id)}`
    : '/services/public-target/suggest';

  const selectedDnsProvider = dnsProviders.find((p) => String(p.id) === formData.dns_provider_id);
  const shouldSuggestTarget =
    isOpen &&
    formData.expose_mode === 'proxy_dns' &&
    formData.public_target_mode === 'auto' &&
    Boolean(formData.dns_provider_id) &&
    (selectedDnsProvider
      ? hasCapability(selectedDnsProvider, 'supports_auto_public_target', providerTypeMap)
      : false);

  const {
    data: targetSuggestion,
    isFetching: isFetchingTargetSuggestion,
    refetch: refetchTargetSuggestion,
  } = useQuery<TargetSuggestion>({
    queryKey: ['public-target-suggest', formData.proxy_provider_id],
    queryFn: () => api.get<TargetSuggestion>(suggestionUrl),
    enabled: shouldSuggestTarget,
    staleTime: 45_000,
  });

  const fqdnPreview =
    formData.subdomain && formData.domain
      ? `${formData.subdomain}.${formData.domain}`
      : 'subdomain.domain.tld';

  const buildPayload = () => {
    const tunnelHostname = (formData.tunnel_hostname.trim() || fqdnPreview).toLowerCase();
    const manualDnsTarget = formData.dns_ip.trim();
    const suggestedDnsTarget = String(targetSuggestion?.recommended || '').trim();

    const effectivePublicTargetMode =
      formData.public_target_mode === 'auto' &&
      formData.dns_provider_id &&
      selectedDnsProvider &&
      !hasCapability(selectedDnsProvider, 'supports_auto_public_target', providerTypeMap)
        ? 'manual'
        : formData.public_target_mode;

    const effectiveAutoUpdateDns =
      effectivePublicTargetMode === 'auto' ? formData.auto_update_dns : false;

    return {
      subdomain: formData.subdomain.trim().toLowerCase(),
      domain: formData.domain.trim().toLowerCase(),
      target_ip: formData.target_ip.trim(),
      target_port: Number(formData.target_port),
      forward_scheme: formData.forward_scheme,
      websocket: formData.websocket,
      expose_mode: formData.expose_mode,
      public_target_mode:
        formData.expose_mode === 'proxy_dns' ? effectivePublicTargetMode : 'manual',
      auto_update_dns: formData.expose_mode === 'proxy_dns' ? effectiveAutoUpdateDns : false,
      tunnel_provider_id:
        formData.expose_mode === 'tunnel' && formData.tunnel_provider_id
          ? Number(formData.tunnel_provider_id)
          : null,
      tunnel_hostname: formData.expose_mode === 'tunnel' ? tunnelHostname : '',
      enabled: isEditMode ? Boolean(service?.enabled ?? true) : true,
      proxy_provider_id:
        formData.expose_mode === 'proxy_dns' && formData.proxy_provider_id
          ? Number(formData.proxy_provider_id)
          : null,
      dns_provider_id:
        formData.expose_mode === 'proxy_dns' && formData.dns_provider_id
          ? Number(formData.dns_provider_id)
          : null,
      dns_ip:
        formData.expose_mode === 'proxy_dns' ? manualDnsTarget || suggestedDnsTarget : '',
      tag_ids:
        isEditMode && Array.isArray(service?.tags)
          ? (service.tags as Array<Record<string, unknown>>)
              .map((t) => Number(t?.id))
              .filter((id) => Number.isFinite(id))
          : [],
      environment_ids:
        isEditMode && Array.isArray(service?.environments)
          ? (service.environments as Array<Record<string, unknown>>)
              .map((env) => Number(env?.id))
              .filter((id) => Number.isFinite(id))
          : [],
      icon_url: isEditMode ? String(service?.icon_url || '') : '',
      extra_proxy_provider_ids: formData.extra_proxy_provider_ids
        .filter(
          (id) =>
            id !==
            (formData.expose_mode === 'tunnel'
              ? formData.tunnel_provider_id
              : formData.proxy_provider_id),
        )
        .map((id) => Number(id)),
      extra_dns_provider_ids:
        formData.expose_mode === 'proxy_dns'
          ? formData.extra_dns_provider_ids
              .filter((id) => id !== formData.dns_provider_id)
              .map((id) => Number(id))
          : [],
    };
  };

  const saveService = useMutation({
    mutationFn: async (payload: ReturnType<typeof buildPayload>) => {
      const persisted = isEditMode
        ? await api.put<Record<string, unknown>>(`/services/${service!.id}`, payload)
        : await api.post<Record<string, unknown>>('/services', payload);

      const needFanoutPush =
        payload.extra_proxy_provider_ids.length > 0 || payload.extra_dns_provider_ids.length > 0;
      const serviceId = Number((persisted as Record<string, unknown>)?.id || service?.id || 0);

      if (needFanoutPush && serviceId) {
        const pushResult = await api.post<Record<string, unknown>>(`/services/${serviceId}/push`);
        return { persisted, pushResult };
      }
      return { persisted, pushResult: null };
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });

      const serviceErrors = Array.isArray((result.persisted as Record<string, unknown>)?.errors)
        ? ((result.persisted as Record<string, unknown>).errors as string[])
        : [];
      const pushErrors = Array.isArray((result.pushResult as Record<string, unknown> | null)?.errors)
        ? ((result.pushResult as Record<string, unknown>).errors as string[])
        : [];
      const allErrors = [...serviceErrors, ...pushErrors];

      if (allErrors.length === 0) {
        toast.success(
          isEditMode ? 'Route updated successfully' : 'Route created and pushed successfully',
          { duration: 4500 },
        );
      } else {
        const errorSummary = allErrors.slice(0, 2).join('; ');
        const moreCount = allErrors.length > 2 ? ` (+${allErrors.length - 2} more)` : '';
        toast(
          isEditMode
            ? `Route updated with warnings: ${errorSummary}${moreCount}`
            : `Route created with warnings: ${errorSummary}${moreCount}`,
          { icon: '⚠️', duration: 8000 },
        );
      }
      setFormData(isEditMode ? toFormState(service) : initialForm);
      onClose();
    },
    onError: (err: { response?: { data?: { detail?: string } } }) => {
      toast.error(
        err?.response?.data?.detail ||
          (isEditMode ? 'Failed to update route' : 'Failed to create route'),
        { duration: 5000 },
      );
    },
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.domain || !formData.subdomain || !formData.target_ip) {
      toast.error('Please complete domain, subdomain and target.');
      return;
    }
    if (formData.expose_mode === 'tunnel' && !formData.tunnel_provider_id) {
      toast.error('Please select a tunnel provider.');
      return;
    }

    const manualDnsTarget = formData.dns_ip.trim();
    const suggestedDnsTarget = String(targetSuggestion?.recommended || '').trim();
    const effectiveMode =
      formData.public_target_mode === 'auto' &&
      formData.dns_provider_id &&
      selectedDnsProvider &&
      !hasCapability(selectedDnsProvider, 'supports_auto_public_target', providerTypeMap)
        ? 'manual'
        : formData.public_target_mode;

    if (formData.expose_mode === 'proxy_dns' && formData.dns_provider_id) {
      if (effectiveMode === 'manual' && !manualDnsTarget) {
        toast.error('Please enter the DNS public target (reverse proxy IP/FQDN).');
        return;
      }
      if (effectiveMode === 'auto' && !manualDnsTarget && !suggestedDnsTarget) {
        toast.error('No public target detected automatically. Enter one manually.');
        return;
      }
    }

    const payload = buildPayload();
    saveService.mutate(payload);
  };

  const handleClose = () => {
    setFormData(isEditMode ? toFormState(service) : initialForm);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/70 backdrop-blur-sm animate-in fade-in duration-200 overflow-y-auto pt-20">
      <div className="bg-card border border-border rounded-xl shadow-2xl max-w-4xl w-full flex flex-col font-sans animate-in zoom-in-95 duration-200 my-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-border pb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 border border-primary/20 rounded-lg">
              <Network className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-foreground leading-tight">
                {isEditMode ? 'Edit Route' : 'Route New Service'}
              </h2>
              <p className="text-xs font-medium text-muted-foreground mt-0.5">
                {isEditMode
                  ? 'Update route design: domain, target, providers and push strategy.'
                  : 'Complete route design: domain, target, providers and push strategy.'}
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-muted-foreground hover:text-foreground bg-muted hover:bg-accent rounded-lg transition-colors border border-transparent hover:border-border"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="flex flex-col h-full max-h-full">
          <div className="p-6 md:p-8 overflow-y-auto space-y-8 flex-1">
            <ServiceForm
              formData={formData}
              setFormData={setFormData}
              providers={providers}
              domains={domains}
              isLoadingProviders={isLoadingProviders}
              isLoadingDomains={isLoadingDomains}
              providerTypeMap={providerTypeMap}
              targetSuggestion={targetSuggestion}
              isFetchingTargetSuggestion={isFetchingTargetSuggestion}
              refetchTargetSuggestion={refetchTargetSuggestion}
            />

            <ServicePreview
              formData={formData}
              selectedProxy={selectedProxy}
              selectedDns={selectedDns}
              selectedTunnel={selectedTunnel}
              selectedExtraProxies={selectedExtraProxies}
              selectedExtraDns={selectedExtraDns}
            />
          </div>

          {/* Footer */}
          <div className="bg-muted/50 px-6 py-4 border-t border-border flex items-center justify-end rounded-b-xl shrink-0 mt-auto gap-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleClose}
                className="px-5 py-2.5 hover:bg-accent bg-card border border-border text-foreground text-sm rounded-lg font-semibold transition-colors shadow-sm"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saveService.isPending || isLoadingProviders}
                className={`px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm flex items-center gap-2 ${
                  saveService.isPending ? 'opacity-70 cursor-not-allowed' : 'hover:shadow-md'
                }`}
              >
                {saveService.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {isEditMode ? 'Saving...' : 'Creating...'}
                  </>
                ) : isEditMode ? (
                  'Save Route'
                ) : (
                  'Create Route'
                )}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
