import { useEffect, useMemo, useRef, useState } from 'react';
import { Container, Pencil, Plug } from 'lucide-react';
import { Button, Field, Input, Modal } from '@/components/ui';
import {
  type ProviderFormState,
  type ProviderTypeMeta,
  type ProviderValidationResult,
  canSubmitProvider,
  emptyForm,
  getGuidedSteps,
  isUrlOptional,
  requiresUsername,
  seedFormForType,
} from '@/components/features/providers/providerConstants';
import { StepCredentials, StepTypeSelector, type WizardMode } from '@/components/features/provider-modal';
import { useProviderMutations } from '@/hooks/useProviderMutations';
import { useProviderTypes } from '@/hooks/useProviderTypes';
import { useDockerEndpoints } from '@/hooks/useDockerEndpoints';
import { useUnsavedGuard } from '@/hooks/useUnsavedGuard';
import { useT } from '@/i18n';
import type { Provider, ProviderUpdate } from '@/types/api';

const DEFAULT_DOCKER_HOST = 'unix:///var/run/docker.sock';

export interface ProviderModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** When given, the modal edits this provider instead of creating one. */
  provider?: Provider | null;
}

function formFromProvider(provider: Provider): ProviderFormState {
  const extra = (provider.extra || {}) as Record<string, unknown>;
  return {
    name: provider.name || '',
    type: String(provider.type || ''),
    url: provider.url || '',
    username: provider.username || '',
    password: '',
    tunnel_id: typeof extra.tunnel_id === 'string' ? extra.tunnel_id : '',
  };
}

export function ProviderModal({ isOpen, onClose, provider = null }: ProviderModalProps) {
  const t = useT();
  const editMode = Boolean(provider);
  const editingId = provider?.id ?? null;
  const providerRef = useRef(provider);
  useEffect(() => {
    providerRef.current = provider;
  }, [provider]);

  const [step, setStep] = useState<1 | 2>(1);
  const [wizardMode, setWizardMode] = useState<WizardMode>('guided');
  const [guidedStepIndex, setGuidedStepIndex] = useState(0);
  const [formData, setFormData] = useState<ProviderFormState>(emptyForm);
  const [validationResult, setValidationResult] = useState<ProviderValidationResult | null>(null);
  const [isDockerMode, setIsDockerMode] = useState(false);
  const docker = useDockerEndpoints();
  /**
   * Whether the operator has typed anything the wizard did not put there itself. Picking a
   * type seeds `name` and `url` from the provider's metadata, so comparing the form against
   * `emptyForm` would call a dialog dirty the moment a tile is clicked; the flag is set from
   * the two paths a human can type through instead, and so cannot drift from that seeding.
   */
  const [edited, setEdited] = useState(false);

  const typesQuery = useProviderTypes({ enabled: isOpen });
  const { data: providerTypes, isLoading: typesLoading } = typesQuery;

  const availableTypes = useMemo(() => {
    const entries = Object.entries(providerTypes || {}).filter(([, meta]) => Boolean(meta?.available));
    return entries.sort((a, b) => String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0])));
  }, [providerTypes]);

  const selectedMeta: ProviderTypeMeta | undefined = (providerTypes || {})[formData.type];
  const guidedSteps = useMemo(
    () => getGuidedSteps(formData.type, selectedMeta, t),
    [formData.type, selectedMeta, t],
  );

  // Edit mode: seed the form from the provider each time the modal opens on one.
  useEffect(() => {
    if (!isOpen) return;
    const current = providerRef.current;
    if (!current) return;
    setFormData(formFromProvider(current));
    setStep(2);
    setWizardMode('expert');
    setGuidedStepIndex(0);
    setIsDockerMode(false);
    setValidationResult(null);
    setEdited(false);
  }, [isOpen, editingId]);

  const resetAndClose = () => {
    setStep(1);
    setWizardMode('guided');
    setGuidedStepIndex(0);
    setFormData(emptyForm);
    setValidationResult(null);
    setIsDockerMode(false);
    setEdited(false);
    docker.setName('');
    docker.setHost(DEFAULT_DOCKER_HOST);
    onClose();
  };

  /**
   * Escape closes the dialog even while `persistent` blocks the backdrop, and closing wipes
   * every field. What gets wiped is a provider's credentials -- a Cloudflare token pasted out
   * of another tab, a proxy manager password -- none of which the form can offer back. Cancel
   * goes through the same gate, so the two ways out of the wizard behave alike. A save that
   * succeeded still closes straight through `resetAndClose`: there is nothing left to lose.
   */
  const { requestClose, UnsavedGuardElement } = useUnsavedGuard(edited, resetAndClose);

  const { validateDraft, createProvider, updateProvider } = useProviderMutations(formData, setValidationResult, {
    onCreated: resetAndClose,
    onUpdated: resetAndClose,
  });

  const updateField = (key: keyof ProviderFormState, value: string) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
    setValidationResult(null);
    setEdited(true);
  };

  const chooseDockerType = () => {
    setIsDockerMode(true);
    setFormData(emptyForm);
    setValidationResult(null);
  };

  const chooseProviderType = (type: string, meta: ProviderTypeMeta) => {
    setIsDockerMode(false);
    // `seedFormForType` carries the rule that the URL is never seeded from `placeholder_url`.
    setFormData((prev) => seedFormForType(prev, type, String(meta.label || type)));
    setGuidedStepIndex(0);
    setValidationResult(null);
  };

  const handleAddDockerEndpoint = async () => {
    try {
      await docker.addEndpoint.mutateAsync();
      resetAndClose();
    } catch {
      /* toast already shown by the hook */
    }
  };

  const handleSave = () => {
    const current = provider;
    if (!current) return;
    const data: ProviderUpdate = {
      name: formData.name.trim(),
      url: formData.url.trim(),
      username: formData.username.trim(),
      password: formData.password || undefined,
    };
    if (formData.type === 'cloudflare_tunnel') {
      data.extra = { ...(current.extra || {}), tunnel_id: formData.tunnel_id.trim() };
    }
    updateProvider.mutate({ id: current.id, data });
  };

  const canContinue = Boolean(formData.type) || isDockerMode;
  const canSubmit = canSubmitProvider(formData, selectedMeta);
  const canSave =
    Boolean(formData.name.trim()) &&
    Boolean(formData.url.trim() || isUrlOptional(formData.type)) &&
    (!requiresUsername(formData.type, selectedMeta) || Boolean(formData.username.trim())) &&
    (formData.type !== 'cloudflare_tunnel' || Boolean(formData.tunnel_id.trim()));

  const busy = validateDraft.isPending || createProvider.isPending || updateProvider.isPending || docker.addEndpoint.isPending;

  const title = editMode ? t('provider_modal.edit_title') : t('provider_modal.title');
  const description = editMode
    ? t('provider_modal.edit.description', { name: provider?.name || '' })
    : t('provider_modal.step', { step, total: 2 });

  let footer: React.ReactNode;
  if (editMode) {
    footer = (
      <div className="flex w-full items-center justify-end gap-2">
        <Button variant="outline" onClick={() => void requestClose()} disabled={updateProvider.isPending}>
          {t('common.cancel')}
        </Button>
        <Button onClick={handleSave} loading={updateProvider.isPending} disabled={!canSave}>
          {updateProvider.isPending ? t('provider_modal.footer.saving') : t('common.save')}
        </Button>
      </div>
    );
  } else if (step === 1) {
    footer = (
      <div className="flex w-full items-center justify-between gap-2">
        <Button variant="outline" onClick={() => void requestClose()}>
          {t('common.cancel')}
        </Button>
        <Button onClick={() => setStep(2)} disabled={!canContinue}>
          {t('provider_modal.footer.continue')}
        </Button>
      </div>
    );
  } else if (isDockerMode) {
    footer = (
      <div className="flex w-full items-center justify-between gap-2">
        <Button variant="outline" onClick={() => setStep(1)} disabled={docker.addEndpoint.isPending}>
          {t('common.back')}
        </Button>
        <Button onClick={handleAddDockerEndpoint} loading={docker.addEndpoint.isPending} disabled={!docker.canSubmit}>
          {docker.addEndpoint.isPending ? t('provider_modal.docker.submitting') : t('provider_modal.docker.submit')}
        </Button>
      </div>
    );
  } else {
    footer = (
      <div className="flex w-full items-center justify-between gap-2">
        <Button variant="outline" onClick={() => setStep(1)} disabled={busy}>
          {t('common.back')}
        </Button>
        <div className="flex items-center gap-2">
          {validationResult?.ok ? (
            <>
              <Button variant="ghost" onClick={() => validateDraft.mutate()} loading={validateDraft.isPending} disabled={busy || !canSubmit}>
                {t('provider_modal.footer.revalidate')}
              </Button>
              <Button onClick={() => createProvider.mutate()} loading={createProvider.isPending} disabled={busy || !canSubmit}>
                {createProvider.isPending ? t('provider_modal.footer.connecting') : t('provider_modal.footer.connect')}
              </Button>
            </>
          ) : (
            <Button onClick={() => validateDraft.mutate()} loading={validateDraft.isPending} disabled={busy || !canSubmit}>
              {validateDraft.isPending ? t('provider_modal.footer.validating') : t('provider_modal.footer.validate')}
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <Modal
      open={isOpen}
      onClose={() => void requestClose()}
      size="lg"
      title={title}
      description={description}
      icon={editMode ? <Pencil /> : isDockerMode && step === 2 ? <Container /> : <Plug />}
      footer={footer}
      persistent={busy}
    >
      {step === 1 && !editMode ? (
        <StepTypeSelector
          types={availableTypes}
          loading={typesLoading}
          error={typesQuery.isError ? typesQuery.error : null}
          refreshing={typesQuery.isFetching}
          onRetry={() => void typesQuery.refetch()}
          selectedType={formData.type}
          isDockerMode={isDockerMode}
          onChooseProvider={chooseProviderType}
          onChooseDocker={chooseDockerType}
        />
      ) : isDockerMode && !editMode ? (
        <div className="space-y-5">
          <div>
            <h3 className="text-base font-semibold text-foreground">{t('provider_modal.docker.title')}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{t('provider_modal.docker.description')}</p>
          </div>
          <Field label={t('provider_modal.docker.name')} required>
            <Input
              type="text"
              value={docker.name}
              onChange={(e) => {
                docker.setName(e.target.value);
                setEdited(true);
              }}
              placeholder={t('provider_modal.docker.name_placeholder')}
              autoComplete="off"
              // eslint-disable-next-line jsx-a11y/no-autofocus -- a surface the operator just opened lands focus on its first field
              autoFocus
            />
          </Field>
          <Field
            label={t('provider_modal.docker.host')}
            required
            hint={
              <span className="block space-y-1">
                <span className="block">
                  <span className="font-semibold text-foreground">{t('provider_modal.docker.hint_local')}</span>{' '}
                  <code className="rounded-sm bg-muted px-1 py-0.5 font-mono">unix:///var/run/docker.sock</code>
                </span>
                <span className="block">
                  <span className="font-semibold text-foreground">{t('provider_modal.docker.hint_tcp')}</span>{' '}
                  <code className="rounded-sm bg-muted px-1 py-0.5 font-mono">tcp://192.168.1.10:2375</code>
                </span>
                <span className="block">
                  <span className="font-semibold text-foreground">{t('provider_modal.docker.hint_ssh')}</span>{' '}
                  <code className="rounded-sm bg-muted px-1 py-0.5 font-mono">ssh://user@host</code>
                </span>
              </span>
            }
          >
            <Input
              type="text"
              value={docker.host}
              onChange={(e) => {
                docker.setHost(e.target.value);
                setEdited(true);
              }}
              placeholder={DEFAULT_DOCKER_HOST}
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
            />
          </Field>
        </div>
      ) : (
        <StepCredentials
          formData={formData}
          onChange={updateField}
          meta={selectedMeta}
          guidedSteps={guidedSteps}
          mode={wizardMode}
          onModeChange={setWizardMode}
          guidedStepIndex={guidedStepIndex}
          onGuidedStepChange={setGuidedStepIndex}
          validationResult={validationResult}
          editMode={editMode}
        />
      )}
      {UnsavedGuardElement}
    </Modal>
  );
}
