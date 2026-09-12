/**
 * First-run wizard.
 *
 * Nine screens, one rail. `StepName` still carries the nine, but the rail folds `restore` into
 * `welcome` and `provider-form` into `providers` (see `steps.ts`), so opening a sub-screen does
 * not make the progress jump backwards.
 *
 * Two things are load-bearing and unchanged: `useSessionState` (the wizard survives a refresh)
 * and `withoutProviderSecrets` (the credential typed into the provider form never reaches
 * sessionStorage). The keys it owns are listed once, in `SETUP_SESSION_KEYS`.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Monitor, Moon, Sun } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { BrandMark } from '@/components/layout/BrandMark';
import { IconButton, Select, useConfirmDialog } from '@/components/ui';
import { getErrorDetail, getHttpStatus, translateApiError } from '@/lib/errors';
import { SUPPORTED_LANGUAGES, useI18n, type Lang } from '@/i18n';
import { useTheme, type Theme } from '@/theme';
import type { SyncResult } from '@/types/api';
import {
  emptyForm,
  type ProviderFormState,
  type ProviderValidationResult as ValidationResult,
} from '@/components/features/providers/providerConstants';
import { ProviderDeleteConflictBody } from '@/components/features/providers/ProviderDeleteConflictBody';
import {
  createWithdrawChoice,
  isProviderDeleteConflict,
  useProviderMutations,
} from '@/hooks/useProviderMutations';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import {
  DockerStep,
  DoneStep,
  ImportStep,
  NotificationsStep,
  PasswordStep,
  ProviderFormStep,
  ProvidersStep,
  RestoreStep,
  SETUP_SESSION_KEYS,
  SetupProgress,
  SetupStepper,
  WelcomeStep,
} from '@/components/features/setup';
import type { ImportableService, ProviderItem, StepName } from '@/components/features/setup';

const THEME_ICONS: Record<Theme, ReactNode> = { light: <Sun />, dark: <Moon />, system: <Monitor /> };

/* ────────────────────────────────────────────────────────────────
   Helper: persist wizard state in sessionStorage
   ──────────────────────────────────────────────────────────────── */

function useSessionState<T>(
  key: string,
  initial: T,
  /** Applied on the way in and on the way out, to keep secrets out of the store. Must be a
   *  stable reference — an inline arrow would re-run the effect on every render. */
  sanitize?: (value: T) => T,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const storageKey = `vauxtra.setup.${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = sessionStorage.getItem(storageKey);
      if (!stored) return initial;
      const parsed = JSON.parse(stored) as T;
      // Also on read: a value written by an older build may still carry a password.
      return sanitize ? sanitize(parsed) : parsed;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(sanitize ? sanitize(value) : value));
    } catch { /* ignore */ }
  }, [storageKey, value, sanitize]);

  return [value, setValue];
}

/** The wizard survives a reload; the credential typed into it must not.
 *
 *  `sessionStorage` outlives a refresh, comes back with the tab through the browser's
 *  session restore, and is readable by anything running in the page. A proxy admin
 *  password or a Cloudflare API token has no business sitting there: everything else in
 *  the form comes back, that one field is typed again.
 */
const withoutProviderSecrets = (form: ProviderFormState): ProviderFormState => (
  form.password ? { ...form, password: '' } : form
);

/** Drops every key the wizard owns — on finish, and after a restore replaces the whole state. */
function clearWizardSession() {
  SETUP_SESSION_KEYS.forEach((key) => {
    try {
      sessionStorage.removeItem(`vauxtra.setup.${key}`);
    } catch { /* ignore */ }
  });
}

/* ────────────────────────────────────────────────────────────────
   Main Setup Component
   ──────────────────────────────────────────────────────────────── */

export function Setup({ onComplete }: { onComplete: () => void | Promise<void> }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t, lang, setLang } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  // Wizard state
  const [step, setStep] = useSessionState<StepName>('step', 'welcome');

  // Password step
  const [skipPassword, setSkipPassword] = useSessionState<boolean | null>('skipPassword', null);

  // Providers step
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [formData, setFormData] = useSessionState<ProviderFormState>('formData', emptyForm, withoutProviderSecrets);
  const [wizardMode, setWizardMode] = useSessionState<'guided' | 'expert' | null>('wizardMode', null);
  const [guidedStepIndex, setGuidedStepIndex] = useSessionState('guidedStepIndex', 0);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  // Import
  const [importableServices, setImportableServices] = useState<ImportableService[]>([]);
  const [loadingImportable, setLoadingImportable] = useState(false);
  /**
   * The scan came back with nothing because it failed. Without this, it came back with
   * nothing exactly like a provider that has nothing to import — and the wizard drew a
   * green tick over the failure on the screen whose next button ends setup.
   */
  const [importScanFailed, setImportScanFailed] = useState(false);
  const [importing, setImporting] = useState(false);
  const [finishing, setFinishing] = useState(false);

  // Provider types
  const { data: providerTypes } = useProviderTypes();

  /* ─────────────────── API Calls ─────────────────── */

  const refreshProviders = useCallback(async () => {
    try {
      setProviders(await api.get<ProviderItem[]>('/providers'));
    } catch { /* ignore */ }
  }, []);

  const goToProviders = () => {
    setStep('providers');
    void refreshProviders();
  };

  const handleSetPassword = async (password: string) => {
    try {
      await api.post('/auth/setup-password', { password });
      queryClient.invalidateQueries({ queryKey: ['auth-status'] });
      toast.success(t('setup.toast.password_set'));
      goToProviders();
    } catch (err: unknown) {
      toast.error(translateApiError(err, t, t('setup.toast.password_failed')));
      throw err;
    }
  };

  /** Provider id → declared type, so an imported host is drawn with its real provider's logo. */
  const providerTypeById = useMemo(
    () => new Map(providers.map((p) => [p.id, p.type])),
    [providers],
  );

  const loadImportableServices = useCallback(async () => {
    setImportScanFailed(false);
    if (providers.length === 0) {
      setImportableServices([]);
      return;
    }
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
          kind: 'proxy',
          source: (host._provider_name as string) || t('setup.import.source_proxy'),
          // The row's icon follows the provider that served it. Defaulting to 'npm' drew an
          // Nginx Proxy Manager logo on every Traefik or Caddy host.
          type: (host._provider_type as string)
            || (host._provider_id !== undefined ? providerTypeById.get(host._provider_id) : undefined)
            || 'proxy',
          name: domain.split('.')[0] || domain,
          domain,
          target,
          selected: false,
          raw: host,
        });
      }

      for (const rewrite of result.dns_rewrites ?? []) {
        if (rewrite._already_imported) continue;
        const domain = (rewrite.domain as string) || '';
        if (!domain) continue;
        services.push({
          kind: 'dns',
          source: (rewrite._provider_name as string) || t('setup.import.source_dns'),
          type: (rewrite._provider_id !== undefined ? providerTypeById.get(rewrite._provider_id) : undefined) || 'dns',
          name: domain.split('.')[0] || domain,
          domain,
          target: (rewrite.answer as string) || (rewrite.target as string) || '',
          selected: false,
          raw: rewrite,
        });
      }

      setImportableServices(services);
    } catch (err) {
      if (import.meta.env.DEV) console.error('Sync error:', err);
      toast.error(translateApiError(err, t, t('setup.toast.scan_failed')));
      setImportableServices([]);
      setImportScanFailed(true);
    } finally {
      setLoadingImportable(false);
    }
  }, [providers.length, providerTypeById, t]);

  const handleImportAndFinish = async () => {
    const selected = importableServices.filter((s) => s.selected);
    if (selected.length > 0) {
      setImporting(true);
      try {
        const payload = {
          proxy_hosts: selected.filter((s) => s.kind === 'proxy').map((s) => s.raw),
          dns_rewrites: selected.filter((s) => s.kind === 'dns').map((s) => s.raw),
        };
        const result = await api.post<{ imported: number; errors?: string[] }>('/services/import', payload);
        if (result.imported > 0) toast.success(t('setup.toast.imported', { count: result.imported }));
        if (result.errors && result.errors.length > 0) {
          toast.error(t('setup.toast.import_skipped', { count: result.errors.length }));
        }
      } catch (err) {
        if (import.meta.env.DEV) console.error('Import error:', err);
        toast.error(translateApiError(err, t, t('setup.toast.import_failed')));
      } finally {
        setImporting(false);
      }
    }
    setStep('done');
  };

  const handleRestorePrepared = async () => {
    clearWizardSession();
  };

  const handleRestoreFinish = async (summary: { secretsIncluded: boolean }) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['auth-status'] }),
      queryClient.invalidateQueries({ queryKey: ['providers'] }),
      queryClient.invalidateQueries({ queryKey: ['services'] }),
      queryClient.invalidateQueries({ queryKey: ['domains'] }),
      queryClient.invalidateQueries({ queryKey: ['logs'] }),
      queryClient.invalidateQueries(),
    ]);
    // The setup wizard may cache an empty services list before restore.
    // Drop it so the first dashboard paint reflects restored data.
    queryClient.removeQueries({ queryKey: ['services'], exact: true });
    // A prefetch decides whether the dashboard paints instantly or with a spinner. This one
    // used to decide something else: a single rejected request threw out of this handler, so a
    // restore that had already succeeded on the server never showed its toast and never left
    // the restore screen -- which reads as "the restore failed" for an operator who just got
    // their data back. Every query below refetches on mount; losing the head start costs a
    // spinner, nothing more.
    try {
      await Promise.all([
        queryClient.fetchQuery({ queryKey: ['auth-status'], queryFn: () => api.get('/auth/me') }),
        queryClient.fetchQuery({ queryKey: ['providers'], queryFn: () => api.get('/providers') }),
        queryClient.fetchQuery({ queryKey: ['services'], queryFn: () => api.get('/services'), staleTime: 0 }),
        queryClient.fetchQuery({ queryKey: ['health'], queryFn: () => api.get('/health') }),
      ]);
    } catch (err) {
      if (import.meta.env.DEV) console.error('Post-restore prefetch failed:', err);
    }

    toast.success(summary.secretsIncluded ? t('setup.toast.restored') : t('setup.toast.restored_no_secrets'));
    navigate('/');
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
      onDeleted: () => { void refreshProviders(); },
    },
  );

  /**
   * The wizard used to delete with `force=true` from the first click, which unlinked every
   * service pointing at the integration without saying so. Now the plain delete goes first:
   * the API answers 409 with the services at stake, and that list is what the second
   * question shows, on the same screen as the Integrations page.
   */
  const handleDeleteProvider = async (id: number) => {
    const name = providers.find((p) => p.id === id)?.name ?? '';
    try {
      await deleteProviderMutation.mutateAsync({ id, name });
    } catch (error: unknown) {
      // Anything else has already been reported by the mutation's own `onError`.
      const detail = getErrorDetail(error);
      if (getHttpStatus(error) !== 409 || !isProviderDeleteConflict(detail)) return;
      // The checkbox lives inside the dialog and `confirm()` only ever answers yes or no;
      // this box is how its state gets back out. Same flow as the Integrations page.
      const choiceRef = createWithdrawChoice();
      const force = await confirm({
        title: t('providers.delete.deps_title'),
        message: <ProviderDeleteConflictBody name={name} detail={detail} choiceRef={choiceRef} />,
        confirmLabel: t('providers.delete.force_confirm'),
        variant: 'warning',
      });
      if (force) deleteProviderMutation.mutate({ id, name, force: true, withdraw: choiceRef.current });
    }
  };

  /* ─────────────────── Navigation ─────────────────── */

  /**
    * `clearWizardSession()` used to run first. So when `onComplete()` failed -- it refetches
    * `/auth/me`, which is a network call like any other -- the wizard had already erased every
    * answer the operator had just given, and the call site discards the promise (`void
    * finish()`), so the rejection went nowhere: no toast, no navigation, a button that looked
    * like it had not been clicked. Nothing is thrown away now until the server has confirmed,
    * and the button says it is working.
    */
  const finish = async () => {
    setFinishing(true);
    try {
      await onComplete();
    } catch (err) {
      toast.error(translateApiError(err, t, t('setup.toast.finish_failed')));
      setFinishing(false);
      return;
    }
    clearWizardSession();
    queryClient.invalidateQueries({ queryKey: ['providers'] });
    queryClient.invalidateQueries({ queryKey: ['services'] });
    setFinishing(false);
    navigate('/');
  };

  /* ─────────────────── Render ─────────────────── */

  // The rail is a "you are here" for the middle of the wizard. On the first screen there is
  // nothing to locate yet, and on the last one there is nothing left to do.
  const showRail = step !== 'welcome' && step !== 'restore' && step !== 'done';

  const themeLabel = `${t('layout.theme.toggle')} · ${t(`layout.theme.${theme}`)}`;

  return (
    <div className="relative min-h-screen overflow-hidden bg-background font-sans text-foreground">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-aurora" />

      <div className="relative mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-5 sm:px-6 lg:px-8">
        <header className="flex items-center justify-between gap-4">
          <BrandMark size="sm" />
          <div className="flex items-center gap-1.5">
            <IconButton
              label={themeLabel}
              icon={THEME_ICONS[theme]}
              onClick={toggleTheme}
              tooltip
              className="h-9 w-9 text-muted-foreground"
            />
            <Select
              size="sm"
              aria-label={t('layout.language')}
              value={lang}
              onChange={(e) => setLang(e.target.value as Lang)}
              className="w-auto bg-card"
            >
              {SUPPORTED_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.flag} {l.label}
                </option>
              ))}
            </Select>
          </div>
        </header>

        <div
          className={
            showRail
              ? 'flex flex-1 items-start gap-10 py-8 lg:py-12'
              : 'flex flex-1 items-center justify-center py-8'
          }
        >
          {showRail && (
            <aside className="sticky top-8 hidden w-48 shrink-0 lg:block">
              <SetupStepper current={step} />
            </aside>
          )}

          <main className="mx-auto w-full max-w-2xl">
            {showRail && <SetupProgress current={step} className="mb-8 lg:hidden" />}

            {step === 'welcome' && (
              <WelcomeStep
                onFreshInstall={() => setStep('password')}
                onRestore={() => setStep('restore')}
              />
            )}

            {step === 'restore' && (
              <RestoreStep
                onBack={() => setStep('welcome')}
                onPrepared={handleRestorePrepared}
                onFinish={handleRestoreFinish}
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
                providerTypes={providerTypes}
                onAdd={() => { resetProviderForm(); setStep('provider-form'); }}
                onDelete={(id) => void handleDeleteProvider(id)}
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
                onContinue={() => { setStep('import'); void loadImportableServices(); }}
              />
            )}

            {step === 'import' && (
              <ImportStep
                providers={providers}
                importableServices={importableServices}
                loadingImportable={loadingImportable}
                scanFailed={importScanFailed}
                importing={importing}
                onToggle={(idx) => setImportableServices((prev) => prev.map((svc, i) => (i === idx ? { ...svc, selected: !svc.selected } : svc)))}
                onSelectAll={() => setImportableServices((prev) => prev.map((svc) => ({ ...svc, selected: true })))}
                onDeselectAll={() => setImportableServices((prev) => prev.map((svc) => ({ ...svc, selected: false })))}
                onRetry={() => void loadImportableServices()}
                onImportAndFinish={() => void handleImportAndFinish()}
                onBack={() => setStep('docker')}
              />
            )}

            {step === 'done' && (
              <DoneStep
                skipPassword={skipPassword}
                providers={providers}
                onFinish={() => void finish()}
                finishing={finishing}
              />
            )}
          </main>
        </div>
      </div>
      {ConfirmDialogElement}
    </div>
  );
}
