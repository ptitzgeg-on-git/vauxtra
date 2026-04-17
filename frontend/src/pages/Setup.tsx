import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient, useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import type { SyncResult } from '@/types/api';
import {
  type ProviderFormState,
  type ProviderValidationResult as ValidationResult,
  type ProviderTypeMeta,
  emptyForm,
  getGuidedSteps,
} from '@/components/features/providers/providerConstants';
import { useProviderMutations } from '@/hooks/useProviderMutations';
import {
  WelcomeStep,
  RestoreStep,
  PasswordStep,
  ProvidersStep,
  ProviderFormStep,
  NotificationsStep,
  DockerStep,
  ImportStep,
  DoneStep,
} from '@/components/features/setup';
import type { StepName, ProviderItem, ImportableService } from '@/components/features/setup';

/* ────────────────────────────────────────────────────────────────
   Helper: persist wizard state in sessionStorage
   ──────────────────────────────────────────────────────────────── */

function useSessionState<T>(key: string, initial: T): [T, React.Dispatch<React.SetStateAction<T>>] {
  const storageKey = `vauxtra.setup.${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = sessionStorage.getItem(storageKey);
      return stored ? JSON.parse(stored) : initial;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(value));
    } catch { /* ignore */ }
  }, [storageKey, value]);

  return [value, setValue];
}

/* ────────────────────────────────────────────────────────────────
   Main Setup Component
   ──────────────────────────────────────────────────────────────── */

export function Setup({ onComplete }: { onComplete: () => void | Promise<void> }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Wizard state
  const [step, setStep] = useSessionState<StepName>('step', 'welcome');
  const [progress, setProgress] = useState(0);

  // Password step
  const [skipPassword, setSkipPassword] = useSessionState<boolean | null>('skipPassword', null);

  // Providers step
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [formData, setFormData] = useSessionState<ProviderFormState>('formData', emptyForm);
  const [wizardMode, setWizardMode] = useSessionState<'guided' | 'expert' | null>('wizardMode', null);
  const [guidedStepIndex, setGuidedStepIndex] = useSessionState('guidedStepIndex', 0);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  // Import
  const [importableServices, setImportableServices] = useState<ImportableService[]>([]);
  const [loadingImportable, setLoadingImportable] = useState(false);

  // Provider types
  const { data: providerTypes } = useQuery<Record<string, ProviderTypeMeta>>({
    queryKey: ['provider-types'],
    queryFn: () => api.get('/providers/types'),
    staleTime: 60_000,
  });

  const currentGuidedSteps = getGuidedSteps(formData.type, (providerTypes || {})[formData.type]);

  // Progress bar
  useEffect(() => {
    const stepMap: Record<StepName, number> = {
      welcome: 0, restore: 5, password: 14, providers: 28,
      'provider-form': 42, notifications: 56, docker: 70, import: 85, done: 100,
    };
    setProgress(stepMap[step] || 0);
  }, [step]);

  // Auto-switch to expert mode when no guided steps available
  useEffect(() => {
    if (formData.type && !wizardMode && currentGuidedSteps.length === 0) {
      setWizardMode('expert');
    }
  }, [formData.type, wizardMode, currentGuidedSteps.length]);

  /* ─────────────────── API Calls ─────────────────── */

  const refreshProviders = async () => {
    try { setProviders(await api.get<ProviderItem[]>('/providers')); } catch { /* ignore */ }
  };

  const goToProviders = () => { setStep('providers'); refreshProviders(); };

  const handleSetPassword = async (password: string) => {
    try {
      await api.post('/auth/setup-password', { password });
      queryClient.invalidateQueries({ queryKey: ['auth-status'] });
      toast.success('Password configured');
      goToProviders();
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to set password');
      throw err;
    }
  };

  const loadImportableServices = async () => {
    if (providers.length === 0) { setImportableServices([]); return; }
    setLoadingImportable(true);
    try {
      const result = await api.post<SyncResult>('/services/sync');
      const services: ImportableService[] = [];

      for (const host of result.proxy_hosts ?? []) {
        if (host._already_imported) continue;
        const names = (host.domain_names as string[] | undefined) ?? (host.domains as string[] | undefined) ?? [];
        const domain = names[0] ?? (host.domain as string) ?? '';
        if (!domain) continue;
        const target = (host.forward_host || host.host)
          ? `${host.forward_host || host.host}${(host.forward_port || host.port) ? `:${host.forward_port || host.port}` : ''}`
          : '';
        services.push({
          kind: 'proxy', source: (host._provider_name as string) || 'proxy',
          type: (host._provider_type as string) || 'npm', name: domain.split('.')[0] || domain,
          domain, target, selected: false, raw: host,
        });
      }

      for (const rewrite of result.dns_rewrites ?? []) {
        if (rewrite._already_imported) continue;
        const domain = (rewrite.domain as string) || '';
        if (!domain) continue;
        services.push({
          kind: 'dns', source: (rewrite._provider_name as string) || 'dns',
          type: 'dns', name: domain.split('.')[0] || domain, domain,
          target: (rewrite.answer as string) || (rewrite.target as string) || '',
          selected: false, raw: rewrite,
        });
      }

      setImportableServices(services);
    } catch (err) {
      if (import.meta.env.DEV) console.error('Sync error:', err);
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to scan providers');
      setImportableServices([]);
    } finally {
      setLoadingImportable(false);
    }
  };

  const handleImportAndFinish = async () => {
    const selected = importableServices.filter(s => s.selected);
    if (selected.length > 0) {
      try {
        const payload = {
          proxy_hosts: selected.filter(s => s.kind === 'proxy').map(s => s.raw),
          dns_rewrites: selected.filter(s => s.kind === 'dns').map(s => s.raw),
        };
        const result = await api.post<{ imported: number; errors?: string[] }>('/services/import', payload);
        if (result.imported > 0) toast.success(`Imported ${result.imported} service${result.imported > 1 ? 's' : ''}`);
        if (result.errors && result.errors.length > 0) toast.error(`${result.errors.length} service(s) skipped`);
      } catch (err) {
        if (import.meta.env.DEV) console.error('Import error:', err);
        const axErr = err as { response?: { data?: { detail?: string } } };
        toast.error(axErr?.response?.data?.detail || 'Import failed');
      }
    }
    setStep('done');
  };

  /* ─────────────────── Provider Form Logic ─────────────────── */

  const resetProviderForm = () => {
    setFormData(emptyForm);
    setWizardMode(null);
    setGuidedStepIndex(0);
    setValidationResult(null);
  };

  const { validateDraft, createProvider, deleteProvider: deleteProviderMutation } = useProviderMutations(
    formData,
    setValidationResult,
    {
      onCreated: async () => { await refreshProviders(); resetProviderForm(); setStep('providers'); },
      onDeleted: () => refreshProviders(),
    },
  );

  /* ─────────────────── Navigation ─────────────────── */

  const finish = async () => {
    ['step', 'skipPassword', 'formData', 'wizardMode', 'guidedStepIndex'].forEach((key) => {
      sessionStorage.removeItem(`vauxtra.setup.${key}`);
    });
    queryClient.invalidateQueries({ queryKey: ['providers'] });
    queryClient.invalidateQueries({ queryKey: ['services'] });
    await onComplete();
    navigate('/');
  };

  /* ─────────────────── Render ─────────────────── */

  return (
    <div className="min-h-screen bg-background flex flex-col font-sans">
      {/* Progress bar */}
      <div className="fixed top-0 left-0 right-0 h-1 bg-muted z-50">
        <div className="h-full bg-primary transition-all duration-500 ease-out" style={{ width: `${progress}%` }} />
      </div>

      <div className="flex-1 flex items-center justify-center p-6 pt-8">
        <div className="w-full max-w-2xl">

          {step === 'welcome' && (
            <WelcomeStep
              onFreshInstall={() => setStep('password')}
              onRestore={() => setStep('restore')}
            />
          )}

          {step === 'restore' && (
            <RestoreStep
              onBack={() => setStep('welcome')}
              onSuccess={() => { toast.success('Backup restored successfully!'); navigate('/'); }}
            />
          )}

          {step === 'password' && (
            <PasswordStep
              onBack={() => setStep('welcome')}
              onContinue={goToProviders}
              onSetPassword={handleSetPassword}
              skipPassword={skipPassword}
              setSkipPassword={setSkipPassword}
            />
          )}

          {step === 'providers' && (
            <ProvidersStep
              providers={providers}
              onAdd={() => { resetProviderForm(); setStep('provider-form'); }}
              onDelete={(id) => deleteProviderMutation.mutate(id)}
              deleteIsPending={deleteProviderMutation.isPending}
              onBack={() => setStep('password')}
              onContinue={() => setStep('notifications')}
            />
          )}

          {step === 'provider-form' && (
            <ProviderFormStep
              formData={formData}
              setFormData={setFormData}
              wizardMode={wizardMode}
              setWizardMode={setWizardMode}
              guidedStepIndex={guidedStepIndex}
              setGuidedStepIndex={setGuidedStepIndex}
              validationResult={validationResult}
              setValidationResult={setValidationResult}
              providerTypes={providerTypes}
              onCancel={() => { resetProviderForm(); setStep('providers'); }}
              onValidate={() => validateDraft.mutate()}
              validateIsPending={validateDraft.isPending}
              onCreate={() => createProvider.mutate()}
              createIsPending={createProvider.isPending}
            />
          )}

          {step === 'notifications' && (
            <NotificationsStep
              onBack={() => setStep('providers')}
              onContinue={() => setStep('docker')}
            />
          )}

          {step === 'docker' && (
            <DockerStep
              onBack={() => setStep('notifications')}
              onContinue={() => { setStep('import'); loadImportableServices(); }}
            />
          )}

          {step === 'import' && (
            <ImportStep
              providers={providers}
              importableServices={importableServices}
              loadingImportable={loadingImportable}
              onToggle={(idx) => setImportableServices(prev => prev.map((svc, i) => i === idx ? { ...svc, selected: !svc.selected } : svc))}
              onSelectAll={() => setImportableServices(prev => prev.map(svc => ({ ...svc, selected: true })))}
              onDeselectAll={() => setImportableServices(prev => prev.map(svc => ({ ...svc, selected: false })))}
              onRetry={loadImportableServices}
              onImportAndFinish={handleImportAndFinish}
              onBack={() => setStep('docker')}
            />
          )}

          {step === 'done' && (
            <DoneStep
              skipPassword={skipPassword}
              providers={providers}
              onFinish={finish}
            />
          )}

        </div>
      </div>
    </div>
  );
}
