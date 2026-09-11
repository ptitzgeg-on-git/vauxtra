import { type FormEvent, type ReactNode, useId, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRight,
  BookmarkPlus,
  Check,
  CircleAlert,
  CircleCheck,
  FlaskConical,
  Network,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { cn } from '@/lib/cn';
import { translateApiError, isHttpStatus } from '@/lib/errors';
import {
  Badge,
  Button,
  InlineAlert,
  Input,
  Modal,
  ProviderLogo,
  SectionHeading,
  useConfirmDialog,
} from '@/components/ui';
import type {
  DryRunPlan,
  Environment,
  PreflightCheck,
  PreflightRequest,
  PreflightResult,
  PushResult,
  Service,
  ServicePayload,
  Tag,
  Template,
  TemplateIn,
} from '@/types/api';
import { type FormState, type Provider, fqdnOf, initialForm, providerHasCapability, toFormState } from './types';
import { ServiceForm } from './ServiceForm';
import { ServicePreview } from './ServicePreview';

interface ExposeModalProps {
  isOpen: boolean;
  onClose: () => void;
  mode?: 'create' | 'edit';
  service?: Service | null;
  /** Seed for a create form (a template applied through `?template=<id>`); ignored in edit mode. */
  initialState?: FormState | null;
  /** Name of the template the seed came from, shown under the title. */
  templateName?: string | null;
}

interface TargetSuggestion {
  candidates: Array<{ value: string; source: string }>;
  recommended: string;
}

type Step = 'configure' | 'review' | 'done';
const STEPS: Step[] = ['configure', 'review', 'done'];

/** Configure → Review → Done, the current one announced with `aria-current`. */
function Stepper({ current, label, labels }: { current: Step; label: string; labels: Record<Step, string> }) {
  const currentIndex = STEPS.indexOf(current);
  return (
    <ol aria-label={label} className="flex items-center gap-2 text-xs font-medium">
      {STEPS.map((step, index) => {
        const done = index < currentIndex;
        const active = index === currentIndex;
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              aria-current={active ? 'step' : undefined}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition-colors',
                active && 'border-primary bg-primary/10 text-primary',
                done && 'border-success/30 bg-success/10 text-success',
                !active && !done && 'border-border bg-muted text-muted-foreground',
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  'inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-semibold',
                  active && 'bg-primary text-primary-foreground',
                  done && 'bg-success text-success-foreground',
                  !active && !done && 'bg-border text-muted-foreground',
                )}
              >
                {done ? <Check className="h-2.5 w-2.5" /> : index + 1}
              </span>
              {labels[step]}
            </span>
            {index < STEPS.length - 1 && <span aria-hidden="true" className="h-px w-6 bg-border" />}
          </li>
        );
      })}
    </ol>
  );
}

type CheckTone = 'success' | 'warning' | 'danger';
const checkTone = (check: PreflightCheck): CheckTone => (check.ok ? 'success' : check.blocking ? 'danger' : 'warning');
const CHECK_ICONS: Record<CheckTone, ReactNode> = {
  success: <CircleCheck className="h-4 w-4 text-success" />,
  warning: <TriangleAlert className="h-4 w-4 text-warning" />,
  danger: <CircleAlert className="h-4 w-4 text-destructive" />,
};

const isRecordWithErrors = (value: unknown): value is { errors: string[] } =>
  Boolean(value) && typeof value === 'object' && Array.isArray((value as { errors?: unknown }).errors);

export function ExposeModal({
  isOpen,
  onClose,
  mode = 'create',
  service = null,
  initialState = null,
  templateName = null,
}: ExposeModalProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const isEditMode = mode === 'edit' && Boolean(service?.id);
  const serviceId = isEditMode ? Number(service?.id) : null;

  const seedForm = (): FormState => (isEditMode ? toFormState(service) : (initialState ?? initialForm));

  const [formData, setFormData] = useState<FormState>(seedForm);
  const [step, setStep] = useState<Step>('configure');
  const [formError, setFormError] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [dryRun, setDryRun] = useState<DryRunPlan | null>(null);
  const [saveOutcome, setSaveOutcome] = useState<{ host: string; errors: string[] } | null>(null);
  const [templateDraftName, setTemplateDraftName] = useState('');
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const { data: providers = [], isLoading: isLoadingProviders } = useQuery<Provider[]>({
    queryKey: ['providers'],
    queryFn: () => api.get<Provider[]>('/providers'),
    enabled: isOpen,
  });

  const { data: domains = [], isLoading: isLoadingDomains, isError: domainsError } = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
    enabled: isOpen,
  });

  const { data: providerTypes = {} } = useProviderTypes({ enabled: isOpen });

  const { data: tags = [], isLoading: isLoadingTags, isError: tagsError } = useQuery<Tag[]>({
    queryKey: ['tags'],
    queryFn: () => api.get<Tag[]>('/tags'),
    enabled: isOpen,
  });

  const {
    data: environments = [],
    isLoading: isLoadingEnvironments,
    isError: environmentsError,
  } = useQuery<Environment[]>({
    queryKey: ['environments'],
    queryFn: () => api.get<Environment[]>('/environments'),
    enabled: isOpen,
  });

  const providerTypeMap = providerTypes;

  // Derived provider lists for the preview and the payload rules.
  const allProviders = providers.filter((p) => Boolean(p.enabled));
  const proxyProviders = allProviders.filter((p) => providerHasCapability(p, 'proxy', providerTypeMap));
  const dnsProviders = allProviders.filter((p) => providerHasCapability(p, 'dns', providerTypeMap));
  const tunnelProviders = proxyProviders.filter((p) => providerHasCapability(p, 'supports_tunnel', providerTypeMap));

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

  const selectedDnsProvider = selectedDns;
  const selectedDnsSupportsAuto = selectedDnsProvider
    ? providerHasCapability(selectedDnsProvider, 'supports_auto_public_target', providerTypeMap)
    : false;
  const selectedDnsIsExternal = selectedDnsProvider
    ? providerHasCapability(selectedDnsProvider, 'public_dns', providerTypeMap)
    : false;
  const selectedDnsIsLocal = Boolean(selectedDnsProvider) && !selectedDnsIsExternal;
  const shouldSuggestTarget =
    isOpen &&
    formData.expose_mode === 'proxy_dns' &&
    formData.public_target_mode === 'auto' &&
    Boolean(formData.dns_provider_id) &&
    selectedDnsSupportsAuto;

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

  const fqdnPreview = fqdnOf(formData) ?? t('expose.preview.host_placeholder');

  const effectivePublicTargetMode =
    formData.public_target_mode === 'auto' && formData.dns_provider_id && selectedDnsProvider && !selectedDnsSupportsAuto
      ? 'manual'
      : formData.public_target_mode;

  /** Every payload rule lives here: what the API receives on create, edit and preflight. */
  const buildPayload = (): ServicePayload => {
    const tunnelHostname = (formData.tunnel_hostname.trim() || fqdnPreview).toLowerCase();
    const manualDnsTarget = formData.dns_ip.trim();
    const suggestedDnsTarget = String(targetSuggestion?.recommended || '').trim();
    const localDnsFallback =
      formData.expose_mode === 'proxy_dns' && formData.ui_expose_mode === 'dns_only' && selectedDnsIsLocal
        ? formData.target_ip.trim()
        : '';

    const effectiveAutoUpdateDns = effectivePublicTargetMode === 'auto' ? formData.auto_update_dns : false;

    return {
      subdomain: formData.subdomain.trim().toLowerCase(),
      domain: formData.domain.trim().toLowerCase(),
      target_ip: formData.target_ip.trim(),
      target_port: Number(formData.target_port),
      forward_scheme: formData.forward_scheme,
      websocket: formData.websocket,
      expose_mode: formData.expose_mode,
      public_target_mode: formData.expose_mode === 'proxy_dns' ? effectivePublicTargetMode : 'manual',
      auto_update_dns: formData.expose_mode === 'proxy_dns' ? effectiveAutoUpdateDns : false,
      tunnel_provider_id:
        formData.expose_mode === 'tunnel' && formData.tunnel_provider_id ? Number(formData.tunnel_provider_id) : null,
      tunnel_hostname: formData.expose_mode === 'tunnel' ? tunnelHostname : '',
      enabled: isEditMode ? Boolean(service?.enabled ?? true) : true,
      proxy_provider_id:
        formData.expose_mode === 'proxy_dns' && formData.proxy_provider_id ? Number(formData.proxy_provider_id) : null,
      dns_provider_id:
        formData.expose_mode === 'proxy_dns' && formData.dns_provider_id ? Number(formData.dns_provider_id) : null,
      dns_ip: formData.expose_mode === 'proxy_dns' ? manualDnsTarget || suggestedDnsTarget || localDnsFallback : '',
      tag_ids: formData.tag_ids,
      environment_ids: formData.environment_ids,
      icon_url: formData.icon_url,
      extra_proxy_provider_ids: formData.extra_proxy_provider_ids
        .filter(
          (id) => id !== (formData.expose_mode === 'tunnel' ? formData.tunnel_provider_id : formData.proxy_provider_id),
        )
        .map((id) => Number(id)),
      extra_dns_provider_ids:
        formData.expose_mode === 'proxy_dns'
          ? formData.extra_dns_provider_ids.filter((id) => id !== formData.dns_provider_id).map((id) => Number(id))
          : [],
    };
  };

  /** The first thing wrong with the form, as a translated sentence, or null when it can be sent. */
  const validate = (): string | null => {
    if (!formData.domain || !formData.subdomain || !formData.target_ip) {
      return t('expose.validation.required_fields');
    }
    if (formData.expose_mode === 'tunnel' && !formData.tunnel_provider_id) {
      return t('expose.validation.tunnel_provider_required');
    }
    if (
      formData.expose_mode === 'proxy_dns' &&
      !formData.proxy_provider_id &&
      !formData.dns_provider_id &&
      formData.extra_proxy_provider_ids.length === 0 &&
      formData.extra_dns_provider_ids.length === 0
    ) {
      return formData.ui_expose_mode === 'dns_only'
        ? t('expose.validation.dns_required_dns_only')
        : t('expose.validation.provider_required');
    }

    const manualDnsTarget = formData.dns_ip.trim();
    const suggestedDnsTarget = String(targetSuggestion?.recommended || '').trim();

    if (formData.expose_mode === 'proxy_dns' && formData.dns_provider_id) {
      if (effectivePublicTargetMode === 'manual' && !manualDnsTarget) {
        if (formData.ui_expose_mode === 'dns_only') {
          // A local resolver may answer with the service's own LAN address, and that is what
          // `localDnsFallback` publishes. A public zone must not carry one, so `target_ip`
          // gives it nothing: without a target of its own there is simply nothing to write.
          if (!formData.target_ip.trim()) return t('expose.validation.target_required_dns_only');
          if (selectedDnsIsExternal && !suggestedDnsTarget) {
            return t('expose.validation.dns_target_external_required');
          }
        } else if (selectedDnsIsExternal) {
          return t('expose.validation.dns_target_external_required');
        } else if (selectedDnsIsLocal) {
          return t('expose.validation.dns_target_local_required');
        }
      }
      if (effectivePublicTargetMode === 'auto' && !manualDnsTarget && !suggestedDnsTarget) {
        return t('expose.validation.no_auto_target');
      }
    }
    return null;
  };

  const runPreflight = useMutation({
    mutationFn: (body: PreflightRequest) => api.post<PreflightResult>('/services/preflight', body),
    onSuccess: (result) => {
      setPreflight(result);
      setStep('review');
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('expose.preflight.failed')), { duration: 5000 });
    },
  });

  const saveService = useMutation({
    mutationFn: async (payload: ServicePayload) => {
      const persisted = isEditMode
        ? await api.put<Record<string, unknown>>(`/services/${serviceId}`, payload)
        : await api.post<Record<string, unknown>>('/services', payload);

      const needFanoutPush = payload.extra_proxy_provider_ids.length > 0 || payload.extra_dns_provider_ids.length > 0;
      const persistedId = Number(persisted?.id || serviceId || 0);

      if (needFanoutPush && persistedId) {
        const pushResult = await api.post<PushResult>(`/services/${persistedId}/push`);
        return { persisted, pushResult, payload };
      }
      return { persisted, pushResult: null, payload };
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });

      const serviceErrors = isRecordWithErrors(result.persisted) ? result.persisted.errors : [];
      const pushErrors = result.pushResult && Array.isArray(result.pushResult.errors) ? result.pushResult.errors : [];
      const allErrors = [...serviceErrors, ...pushErrors].map(String);
      const host =
        result.payload.expose_mode === 'tunnel' && result.payload.tunnel_hostname
          ? result.payload.tunnel_hostname
          : `${result.payload.subdomain}.${result.payload.domain}`;

      if (allErrors.length === 0) {
        toast.success(isEditMode ? t('expose.toast.updated') : t('expose.toast.created'), { duration: 4500 });
      } else {
        const summary = allErrors.slice(0, 2).join('; ');
        const more = allErrors.length > 2 ? t('expose.toast.more', { count: allErrors.length - 2 }) : '';
        toast(
          isEditMode
            ? t('expose.toast.updated_warnings', { errors: summary, more })
            : t('expose.toast.created_warnings', { errors: summary, more }),
          { icon: <TriangleAlert className="h-4 w-4 text-warning" aria-hidden="true" />, duration: 8000 },
        );
      }
      setSaveOutcome({ host, errors: allErrors });
      setStep('done');
    },
    onError: (err: unknown) => {
      const fallback = isEditMode ? t('expose.toast.update_failed') : t('expose.toast.create_failed');
      toast.error(translateApiError(err, t, fallback), { duration: 5000 });
    },
  });

  const runDryRun = useMutation({
    mutationFn: () => api.post<DryRunPlan>(`/services/${serviceId}/push/dry-run`),
    onSuccess: (plan) => setDryRun(plan),
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('expose.dry_run.failed')), { duration: 5000 });
    },
  });

  const saveTemplate = useMutation({
    mutationFn: (body: TemplateIn) => api.post<Template>('/templates', body),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      setTemplateDraftName('');
      toast.success(t('expose.template.saved', { name: created?.name ?? templateDraftName.trim() }));
    },
    onError: (err: unknown) => {
      if (isHttpStatus(err, 409)) {
        toast.error(t('expose.template.name_exists'));
        return;
      }
      toast.error(translateApiError(err, t, t('expose.template.save_failed')));
    },
  });

  const buildTemplatePayload = (name: string): TemplateIn => {
    const payload = buildPayload();
    return {
      name: name.trim(),
      description: '',
      forward_scheme: payload.forward_scheme,
      target_port: payload.target_port > 0 ? payload.target_port : null,
      websocket: payload.websocket,
      expose_mode: payload.expose_mode,
      proxy_provider_id: payload.proxy_provider_id,
      dns_provider_id: payload.dns_provider_id,
      tunnel_provider_id: payload.tunnel_provider_id,
      public_target_mode: payload.public_target_mode,
      domain: payload.domain,
      dns_ip: payload.dns_ip,
      tag_ids: payload.tag_ids,
      icon_url: payload.icon_url,
    };
  };

  const handleContinue = (e: FormEvent) => {
    e.preventDefault();
    const problem = validate();
    if (problem) {
      setFormError(problem);
      toast.error(problem);
      return;
    }
    setFormError(null);
    runPreflight.mutate({ ...buildPayload(), service_id: serviceId });
  };

  const handleSave = () => {
    if (preflight && preflight.summary.blocking_failures > 0) return;
    saveService.mutate(buildPayload());
  };

  const handleSaveTemplate = () => {
    const name = templateDraftName.trim();
    if (!name) {
      toast.error(t('expose.template.name_required'));
      return;
    }
    saveTemplate.mutate(buildTemplatePayload(name));
  };

  const handleClose = () => {
    setFormData(seedForm());
    setStep('configure');
    setFormError(null);
    setPreflight(null);
    setDryRun(null);
    setSaveOutcome(null);
    setTemplateDraftName('');
    onClose();
  };

  /**
   * Escape closes the dialog even though `persistent` blocks the backdrop, and closing resets
   * every field. A finished exposure -- hostname, target, providers, tags, environments, the
   * preflight already run -- is several minutes of work, and one stray key threw all of it
   * away, with no undo and no trace of what was in the form. The reset is now gated on the
   * wizard having something to lose; Cancel goes through the same gate, so the two ways out
   * of the first step behave alike.
   *
   * `done` is deliberately never dirty: the push has happened, that panel is a receipt, and
   * making the operator confirm the closing of a receipt would be noise. `review` always is
   * -- reaching it costs a preflight round-trip, even when nothing was typed on an edit.
   */
  const isDirty =
    step !== 'done' &&
    (step === 'review' ||
      templateDraftName.trim() !== '' ||
      JSON.stringify(formData) !== JSON.stringify(seedForm()));

  const requestClose = async () => {
    if (
      isDirty &&
      !(await confirm({
        title: t('expose.discard.title'),
        message: t('expose.discard.message'),
        confirmLabel: t('expose.discard.confirm'),
        cancelLabel: t('expose.discard.cancel'),
        variant: 'danger',
      }))
    ) {
      return;
    }
    handleClose();
  };

  const checkLabel = (name: string): string => {
    const key = `expose.preflight.check.${name}`;
    const label = t(key);
    return label === key ? name : label;
  };

  /**
   * What the check found, in the reader's language. The API sends the English sentence in
   * `detail` and the short code it was written from in `detail_key`; the code wins when this
   * build knows it, and the sentence stands in otherwise.
   */
  const checkDetail = (check: PreflightCheck): string => {
    const fallback = String(check.detail || '');
    if (!check.detail_key) return fallback;
    const key = `expose.preflight.detail.${check.detail_key}`;
    const line = t(key, check.detail_params);
    return line === key ? fallback : line;
  };

  const blockingFailures = preflight?.summary.blocking_failures ?? 0;
  const warningCount = preflight?.summary.warnings ?? 0;

  const stepLabels: Record<Step, string> = {
    configure: t('expose.steps.configure'),
    review: t('expose.steps.review'),
    done: t('expose.steps.done'),
  };

  const title = isEditMode ? t('expose.title.edit') : t('expose.title.create');
  const description = templateName
    ? t('expose.from_template', { name: templateName })
    : isEditMode
      ? t('expose.description.edit')
      : t('expose.description.create');

  let footer: ReactNode;
  if (step === 'configure') {
    footer = (
      <>
        <Button variant="ghost" onClick={() => void requestClose()}>
          {t('common.cancel')}
        </Button>
        <Button
          type="submit"
          form={formId}
          loading={runPreflight.isPending}
          disabled={isLoadingProviders}
          rightIcon={<ArrowRight />}
        >
          {t('expose.continue')}
        </Button>
      </>
    );
  } else if (step === 'review') {
    footer = (
      <>
        <Button variant="ghost" onClick={() => setStep('configure')} className="mr-auto">
          {t('common.back')}
        </Button>
        <Button
          variant="outline"
          leftIcon={<RefreshCw />}
          loading={runPreflight.isPending}
          onClick={() => runPreflight.mutate({ ...buildPayload(), service_id: serviceId })}
        >
          {t('expose.preflight.rerun')}
        </Button>
        <Button
          onClick={handleSave}
          loading={saveService.isPending}
          disabled={blockingFailures > 0 || runPreflight.isPending}
          leftIcon={<Check />}
        >
          {isEditMode ? t('expose.save_route') : t('expose.create_route')}
        </Button>
      </>
    );
  } else {
    footer = <Button onClick={handleClose}>{t('ui.close')}</Button>;
  }

  return (
    <Modal
      open={isOpen}
      onClose={() => void requestClose()}
      title={title}
      description={description}
      icon={<Network />}
      size="xl"
      persistent
      footer={footer}
      bodyClassName="space-y-6"
    >
      <Stepper current={step} label={t('expose.steps.label')} labels={stepLabels} />

      {step === 'configure' && (
        <form id={formId} onSubmit={handleContinue} className="space-y-8 animate-in fade-in animate-duration-200">
          {formError && (
            <InlineAlert tone="danger" onDismiss={() => setFormError(null)}>
              {formError}
            </InlineAlert>
          )}

          <ServiceForm
            formData={formData}
            setFormData={setFormData}
            providers={providers}
            domains={domains}
            domainsError={domainsError}
            tagsError={tagsError}
            environmentsError={environmentsError}
            isLoadingProviders={isLoadingProviders}
            isLoadingDomains={isLoadingDomains}
            providerTypeMap={providerTypeMap}
            targetSuggestion={targetSuggestion}
            isFetchingTargetSuggestion={isFetchingTargetSuggestion}
            refetchTargetSuggestion={refetchTargetSuggestion}
            tags={tags}
            environments={environments}
            isLoadingTaxonomy={isLoadingTags || isLoadingEnvironments}
          />

          <ServicePreview
            formData={formData}
            selectedProxy={selectedProxy}
            selectedDns={selectedDns}
            selectedTunnel={selectedTunnel}
            selectedExtraProxies={selectedExtraProxies}
            selectedExtraDns={selectedExtraDns}
          />

          {/* Save as template: the current setup minus subdomain and target, reusable from Templates. */}
          <section className="space-y-3 rounded-2xl border border-dashed border-border bg-muted/30 p-4">
            <SectionHeading
              as="h3"
              size="sm"
              icon={<BookmarkPlus />}
              title={t('expose.template.title')}
              description={t('expose.template.description')}
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                aria-label={t('expose.template.name_label')}
                placeholder={t('expose.template.name_placeholder')}
                value={templateDraftName}
                maxLength={64}
                wrapperClassName="flex-1"
                onChange={(e) => setTemplateDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleSaveTemplate();
                  }
                }}
              />
              <Button
                variant="secondary"
                leftIcon={<BookmarkPlus />}
                loading={saveTemplate.isPending}
                disabled={!templateDraftName.trim()}
                onClick={handleSaveTemplate}
              >
                {t('expose.template.save')}
              </Button>
            </div>
          </section>
        </form>
      )}

      {step === 'review' && preflight && (
        <div className="space-y-6 animate-in fade-in animate-duration-200">
          {blockingFailures > 0 ? (
            <InlineAlert tone="danger" title={t('expose.preflight.blocked_title', { count: blockingFailures })}>
              {t('expose.preflight.blocked_body')}
            </InlineAlert>
          ) : warningCount > 0 ? (
            <InlineAlert tone="warning" title={t('expose.preflight.warnings_title', { count: warningCount })}>
              {t('expose.preflight.warnings_body')}
            </InlineAlert>
          ) : (
            <InlineAlert tone="success" title={t('expose.preflight.passed_title')}>
              {t('expose.preflight.passed_body')}
            </InlineAlert>
          )}

          <section className="space-y-3">
            <SectionHeading
              as="h3"
              size="sm"
              title={t('expose.preflight.title')}
              description={t('expose.preflight.description', { host: preflight.public_host || fqdnPreview })}
            >
              <span className="text-xs text-muted-foreground">
                {t('expose.preflight.summary', {
                  total: preflight.summary.total,
                  blocking: blockingFailures,
                  warnings: warningCount,
                })}
              </span>
            </SectionHeading>
            <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
              {preflight.checks.map((check) => {
                const tone = checkTone(check);
                const detail = checkDetail(check);
                return (
                  <li key={check.name} className="flex items-start gap-3 px-4 py-3">
                    <span aria-hidden="true" className="mt-0.5 shrink-0">
                      {CHECK_ICONS[tone]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-medium text-foreground">{checkLabel(check.name)}</p>
                        <Badge tone={tone} size="sm">
                          {tone === 'success'
                            ? t('expose.preflight.state.pass')
                            : tone === 'danger'
                              ? t('expose.preflight.state.blocking')
                              : t('expose.preflight.state.warning')}
                        </Badge>
                      </div>
                      {detail && <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p>}
                    </div>
                  </li>
                );
              })}
              {preflight.checks.length === 0 && (
                <li className="px-4 py-6 text-center text-sm text-muted-foreground">{t('expose.preflight.no_checks')}</li>
              )}
            </ul>
          </section>

          {isEditMode && (
            <section className="space-y-3">
              <SectionHeading
                as="h3"
                size="sm"
                icon={<FlaskConical />}
                title={t('expose.dry_run.title')}
                description={t('expose.dry_run.description')}
              >
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<FlaskConical />}
                  loading={runDryRun.isPending}
                  onClick={() => runDryRun.mutate()}
                >
                  {dryRun ? t('expose.dry_run.rerun') : t('expose.dry_run.run')}
                </Button>
              </SectionHeading>

              {dryRun && (
                <div className="space-y-4 rounded-2xl border border-border bg-card p-4 animate-in fade-in animate-duration-200">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={dryRun.would_change ? 'warning' : 'success'} dot>
                      {dryRun.would_change ? t('expose.dry_run.would_change') : t('expose.dry_run.no_change')}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">{dryRun.public_host}</span>
                    {dryRun.dns_target && (
                      <span className="text-xs text-muted-foreground">
                        {t('expose.dry_run.dns_target', { target: dryRun.dns_target, source: dryRun.dns_target_source })}
                      </span>
                    )}
                  </div>

                  {dryRun.proxy_actions.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {t('expose.dry_run.proxy_actions')}
                      </p>
                      <ul className="space-y-1.5">
                        {dryRun.proxy_actions.map((action) => (
                          <li key={`proxy-${action.provider_id}`} className="flex items-center gap-2 text-sm">
                            <ProviderLogo type={action.provider_type} className="h-4 w-4" />
                            <span className="font-medium text-foreground">{action.provider_name}</span>
                            <Badge size="sm" tone={action.action === 'skip_read_only' ? 'neutral' : 'info'}>
                              {t(`expose.dry_run.action.${action.action}`)}
                            </Badge>
                            <span className="truncate font-mono text-xs text-muted-foreground">
                              {action.target_host}
                              {action.target_origin ? ` → ${action.target_origin}` : ''}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {dryRun.dns_actions.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {t('expose.dry_run.dns_actions')}
                      </p>
                      <ul className="space-y-1.5">
                        {dryRun.dns_actions.map((action) => (
                          <li key={`dns-${action.provider_id}`} className="flex items-center gap-2 text-sm">
                            <ProviderLogo type={action.provider_type} className="h-4 w-4" />
                            <span className="font-medium text-foreground">{action.provider_name}</span>
                            <Badge size="sm" tone="info">
                              {t(`expose.dry_run.action.${action.action}`)}
                            </Badge>
                            <span className="truncate font-mono text-xs text-muted-foreground">
                              {action.domain} → {action.target}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {dryRun.service_updates.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        {t('expose.dry_run.service_updates')}
                      </p>
                      <ul className="space-y-1.5">
                        {dryRun.service_updates.map((update) => (
                          <li key={update.field} className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="font-mono text-xs text-foreground">{update.field}</span>
                            <span className="font-mono text-xs text-muted-foreground line-through">
                              {String(update.old ?? '—')}
                            </span>
                            <ArrowRight aria-hidden="true" className="h-3 w-3 text-muted-foreground" />
                            <span className="font-mono text-xs text-foreground">{String(update.new ?? '—')}</span>
                            <span className="text-xs text-muted-foreground">({update.source})</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {dryRun.warnings.length > 0 && (
                    <InlineAlert tone="warning" title={t('expose.dry_run.warnings')}>
                      <ul className="list-disc space-y-0.5 pl-4">
                        {dryRun.warnings.map((warning, index) => (
                          <li key={index}>{warning}</li>
                        ))}
                      </ul>
                    </InlineAlert>
                  )}
                  {dryRun.errors.length > 0 && (
                    <InlineAlert tone="danger" title={t('expose.dry_run.errors')}>
                      <ul className="list-disc space-y-0.5 pl-4">
                        {dryRun.errors.map((error, index) => (
                          <li key={index}>{error}</li>
                        ))}
                      </ul>
                    </InlineAlert>
                  )}
                  {dryRun.proxy_actions.length === 0 &&
                    dryRun.dns_actions.length === 0 &&
                    dryRun.service_updates.length === 0 && (
                      <p className="text-sm text-muted-foreground">{t('expose.dry_run.empty')}</p>
                    )}
                  <p className="text-xs text-muted-foreground">{t('expose.dry_run.note')}</p>
                </div>
              )}
            </section>
          )}
        </div>
      )}

      {step === 'done' && saveOutcome && (
        <div className="space-y-5 py-4 text-center animate-in fade-in zoom-in-95 animate-duration-200">
          <span
            aria-hidden="true"
            className={cn(
              'mx-auto inline-flex h-14 w-14 items-center justify-center rounded-2xl',
              saveOutcome.errors.length > 0 ? 'bg-warning/10 text-warning' : 'bg-success/10 text-success',
            )}
          >
            {saveOutcome.errors.length > 0 ? <TriangleAlert className="h-7 w-7" /> : <CircleCheck className="h-7 w-7" />}
          </span>
          <div className="space-y-1">
            <h3 className="text-lg font-semibold text-foreground">
              {isEditMode ? t('expose.done.updated_title') : t('expose.done.created_title')}
            </h3>
            <p className="text-sm text-muted-foreground">
              {t('expose.done.body')} <span className="font-mono text-foreground">{saveOutcome.host}</span>
            </p>
          </div>
          {saveOutcome.errors.length > 0 && (
            <InlineAlert tone="warning" title={t('expose.done.warnings_title')} className="text-left">
              <ul className="list-disc space-y-0.5 pl-4">
                {saveOutcome.errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            </InlineAlert>
          )}
        </div>
      )}
      {ConfirmDialogElement}
    </Modal>
  );
}
