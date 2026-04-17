import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Settings, X, ChevronRight, BookOpen, Zap, Server, Container } from 'lucide-react';
import { api } from '@/api/client';
import { ProviderLogo } from '@/components/ui/ProviderLogos';
import {
  type ProviderFormState,
  type ProviderTypeMap,
  type ProviderValidationResult,
  emptyForm,
  fallbackIconByType,
  categoryByType,
  providerColor,
  getGuidedSteps,
  canSubmitProvider,
} from '@/components/features/providers/providerConstants';
import { StepTypeSelector, StepCredentials } from '@/components/features/provider-modal';
import { useProviderMutations } from '@/hooks/useProviderMutations';
import { useDockerEndpoints } from '@/hooks/useDockerEndpoints';

interface ProviderModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ProviderModal({ isOpen, onClose }: ProviderModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [wizardMode, setWizardMode] = useState<'guided' | 'expert' | null>(null);
  const [guidedStepIndex, setGuidedStepIndex] = useState(0);
  const [formData, setFormData] = useState<ProviderFormState>(emptyForm);
  const [validationResult, setValidationResult] = useState<ProviderValidationResult | null>(null);

  // Docker endpoint
  const [isDockerMode, setIsDockerMode] = useState(false);
  const docker = useDockerEndpoints();

  const { data: providerTypes } = useQuery<ProviderTypeMap>({
    queryKey: ['provider-types'],
    queryFn: () => api.get('/providers/types'),
    enabled: isOpen,
  });

  const availableProviderTypes = useMemo(() => {
    const entries = Object.entries(providerTypes || {}).filter(([, meta]) => Boolean(meta?.available));
    return entries.sort((a, b) => String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0])));
  }, [providerTypes]);

  // Group providers by category for display
  const groupedProviders = useMemo(() => {
    const groups: Record<string, Array<[string, ProviderTypeMap[string]]>> = {};
    const categoryOrder = ['External DNS', 'Zero Trust', 'Local DNS', 'Reverse Proxy'];
    for (const entry of availableProviderTypes) {
      const cat = categoryByType[entry[0]]?.label || 'Other';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(entry);
    }
    // Sort groups by predefined order
    return categoryOrder
      .filter((cat) => groups[cat]?.length)
      .map((cat) => ({ category: cat, providers: groups[cat] }));
  }, [availableProviderTypes]);

  const selectedMeta = (providerTypes || {})[formData.type] || {};
  const userLabel = selectedMeta.user_label || 'Username';
  const passLabel = selectedMeta.pass_label || 'Password / token';

  const resetAndClose = () => {
    setStep(1);
    setWizardMode(null);
    setGuidedStepIndex(0);
    setFormData(emptyForm);
    setValidationResult(null);
    setIsDockerMode(false);
    docker.setName('');
    docker.setHost('unix:///var/run/docker.sock');
    onClose();
  };

  const { validateDraft, createProvider } = useProviderMutations(
    formData, setValidationResult, { onCreated: resetAndClose },
  );

  const chooseDockerType = () => {
    setIsDockerMode(true);
    setFormData(emptyForm);
    setValidationResult(null);
  };

  const handleAddDockerEndpoint = async () => {
    try {
      await docker.addEndpoint.mutateAsync();
      resetAndClose();
    } catch { /* toast already shown by hook */ }
  };

  const chooseProviderType = (type: string, label: string, placeholderUrl: string) => {
    setIsDockerMode(false);
    setFormData((prev) => ({
      ...prev,
      type,
      name: prev.name.trim() ? prev.name : label,
      url: prev.url.trim() ? prev.url : (placeholderUrl || ''),
    }));
    setValidationResult(null);
  };

  const canContinue = Boolean(formData.type) || isDockerMode;
  const canSubmit = canSubmitProvider(formData);

  const currentGuidedSteps = getGuidedSteps(formData.type, selectedMeta);
  const totalSteps = isDockerMode ? 2 : (currentGuidedSteps.length > 0 ? 3 : 2);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/70 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-card border border-border rounded-xl shadow-2xl max-w-xl w-full flex flex-col font-sans animate-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between px-6 py-5 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 border border-primary/20 rounded-lg">
              <Settings className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-foreground">Add Integration</h2>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mt-0.5">Step {step} of {totalSteps}</p>
            </div>
          </div>
          <button
            onClick={resetAndClose}
            className="p-2 text-muted-foreground hover:text-foreground bg-muted hover:bg-accent rounded-lg transition-colors border border-transparent hover:border-border"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-8 overflow-y-auto max-h-[70vh]">
          {step === 1 ? (
            <StepTypeSelector
              groupedProviders={groupedProviders}
              selectedType={formData.type}
              isDockerMode={isDockerMode}
              onChooseProvider={chooseProviderType}
              onChooseDocker={chooseDockerType}
            />
          ) : step === 2 && isDockerMode ? (
            /* ── Step 2 (Docker): Connection form ── */
            <div className="space-y-5">
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-semibold bg-blue-500/10 text-blue-600 border-blue-500/30 dark:text-blue-400">
                <Container className="w-4 h-4" />
                Docker Host
              </div>

              <div>
                <h3 className="text-[15px] font-bold text-foreground mb-1">Docker Endpoint</h3>
                <p className="text-sm text-muted-foreground">
                  Connect a Docker daemon to discover and import containers as services.
                </p>
              </div>

              <div className="space-y-4">
                <div className="space-y-1.5">
                  <label className="block text-xs font-semibold text-foreground uppercase tracking-wider">Name</label>
                  <input
                    type="text"
                    value={docker.name}
                    onChange={(e) => docker.setName(e.target.value)}
                    className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm"
                    placeholder="e.g. Local Docker"
                    autoComplete="off"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="block text-xs font-semibold text-foreground uppercase tracking-wider">Docker Host</label>
                  <input
                    type="text"
                    value={docker.host}
                    onChange={(e) => docker.setHost(e.target.value)}
                    className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm font-mono"
                    placeholder="unix:///var/run/docker.sock"
                    autoComplete="off"
                  />
                  <div className="text-xs text-muted-foreground space-y-1 pt-1">
                    <p><span className="font-semibold text-foreground">Local</span> — <code className="bg-muted px-1 py-0.5 rounded font-mono">unix:///var/run/docker.sock</code> (requires socket mount in compose)</p>
                    <p><span className="font-semibold text-foreground">TCP</span> — <code className="bg-muted px-1 py-0.5 rounded font-mono">tcp://192.168.1.10:2375</code> (plaintext) or <code className="bg-muted px-1 py-0.5 rounded font-mono">:2376</code> for TLS</p>
                    <p><span className="font-semibold text-foreground">SSH</span> — <code className="bg-muted px-1 py-0.5 rounded font-mono">ssh://user@host</code> (passwordless SSH key in Vauxtra's <code className="bg-muted px-1 rounded">~/.ssh/</code>)</p>
                  </div>
                </div>
              </div>
            </div>
          ) : step === 2 ? (
            /* ── Step 2: Setup mode (Guided vs Expert) ── */
            <div className="space-y-6">
              {/* Provider badge */}
              {formData.type && (() => {
                const FallbackIcon = fallbackIconByType[formData.type] || Server;
                const color = providerColor[formData.type] || 'bg-primary/10 text-primary border-primary/20';
                return (
                  <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-semibold ${color}`}>
                    <ProviderLogo type={formData.type} className="w-4 h-4" fallback={<FallbackIcon className="w-4 h-4" />} />
                    {selectedMeta.label || formData.type}
                  </div>
                );
              })()}

              <div>
                <h3 className="text-[15px] font-bold text-foreground mb-1">How do you want to set this up?</h3>
                <p className="text-sm text-muted-foreground">
                  {currentGuidedSteps.length > 0
                    ? 'Choose guided mode to walk through prerequisites step by step, or jump straight to the credentials form.'
                    : 'Fill in the connection details to link this provider.'}
                </p>
              </div>

              {currentGuidedSteps.length > 0 ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <button
                    onClick={() => { setWizardMode('guided'); setGuidedStepIndex(0); setStep(3); }}
                    className="flex flex-col items-start gap-3 p-5 rounded-xl border-2 border-primary/30 bg-primary/5 hover:bg-primary/10 hover:border-primary/50 transition-all text-left shadow-sm"
                  >
                    <div className="p-2 rounded-lg bg-primary/10 border border-primary/20 text-primary">
                      <BookOpen className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="font-semibold text-foreground">Guided setup</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        Walk through {currentGuidedSteps.length} prerequisite steps with instructions before entering credentials.
                      </p>
                    </div>
                    <span className="text-xs text-primary font-semibold flex items-center gap-1">
                      Start guide <ChevronRight className="w-3.5 h-3.5" />
                    </span>
                  </button>

                  <button
                    onClick={() => { setWizardMode('expert'); setStep(3); }}
                    className="flex flex-col items-start gap-3 p-5 rounded-xl border border-border bg-card hover:bg-muted hover:border-border transition-all text-left shadow-sm"
                  >
                    <div className="p-2 rounded-lg bg-muted border border-border text-muted-foreground">
                      <Zap className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="font-semibold text-foreground">Expert mode</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        Skip the guide and fill in credentials directly. Use this if you already have everything ready.
                      </p>
                    </div>
                    <span className="text-xs text-muted-foreground font-semibold flex items-center gap-1">
                      Go to form <ChevronRight className="w-3.5 h-3.5" />
                    </span>
                  </button>
                </div>
              ) : (
                /* No guided steps for this provider — go straight to form */
                <div className="pt-2">
                  <p className="text-sm text-muted-foreground mb-4">No prerequisites needed for this provider. Click Continue to enter connection details.</p>
                </div>
              )}
            </div>
          ) : (
            /* ── Step 3: Guided wizard (field-per-step) OR Expert form ── */
            <StepCredentials
              formData={formData}
              setFormData={setFormData}
              wizardMode={wizardMode}
              guidedStepIndex={guidedStepIndex}
              setGuidedStepIndex={setGuidedStepIndex}
              currentGuidedSteps={currentGuidedSteps}
              selectedMeta={selectedMeta}
              userLabel={userLabel}
              passLabel={passLabel}
              validationResult={validationResult}
              setValidationResult={setValidationResult}
            />
          )}
        </div>

        {/* Footer — adapts to step */}
        <div className="bg-muted/50 px-8 py-5 border-t border-border flex items-center justify-between rounded-b-xl">
          {step === 1 ? (
            <>
              <button
                onClick={resetAndClose}
                className="px-5 py-2.5 hover:bg-accent bg-card border border-border text-foreground text-sm rounded-lg font-semibold transition-colors shadow-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => setStep(2)}
                disabled={!canContinue}
                className={`px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm ${!canContinue ? 'opacity-50 cursor-not-allowed' : 'hover:shadow-md'}`}
              >
                Continue
              </button>
            </>
          ) : step === 2 && isDockerMode ? (
            <>
              <button
                onClick={() => setStep(1)}
                className="px-5 py-2.5 hover:bg-accent bg-card border border-border text-foreground text-sm rounded-lg font-semibold transition-colors shadow-sm"
              >
                Back
              </button>
              <button
                onClick={handleAddDockerEndpoint}
                disabled={!docker.canSubmit || docker.addEndpoint.isPending}
                className={`px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm ${(!docker.canSubmit || docker.addEndpoint.isPending) ? 'opacity-60 cursor-not-allowed' : 'hover:shadow-md'}`}
              >
                {docker.addEndpoint.isPending ? 'Adding...' : 'Add Docker Endpoint'}
              </button>
            </>
          ) : step === 2 ? (
            <>
              <button
                onClick={() => setStep(1)}
                className="px-5 py-2.5 hover:bg-accent bg-card border border-border text-foreground text-sm rounded-lg font-semibold transition-colors shadow-sm"
              >
                Back
              </button>
              {/* If no guided steps, skip to credentials directly */}
              {currentGuidedSteps.length === 0 && (
                <button
                  onClick={() => { setWizardMode('expert'); setStep(3); }}
                  className="px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm hover:shadow-md"
                >
                  Continue
                </button>
              )}
            </>
          ) : (
            <>
              <button
                onClick={() => setStep(2)}
                className="px-5 py-2.5 hover:bg-accent bg-card border border-border text-foreground text-sm rounded-lg font-semibold transition-colors shadow-sm"
              >
                Back
              </button>
              {/* Show Validate button until validation passes, then show Connect */}
              {validationResult?.ok ? (
                <button
                  onClick={() => createProvider.mutate()}
                  disabled={createProvider.isPending || !canSubmit}
                  className={`px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm flex items-center gap-2 ${(createProvider.isPending || !canSubmit) ? 'opacity-70 cursor-not-allowed' : 'hover:shadow-md'}`}
                >
                  {createProvider.isPending ? 'Connecting...' : 'Connect Integration'}
                </button>
              ) : (
                <button
                  onClick={() => validateDraft.mutate()}
                  disabled={validateDraft.isPending || !canSubmit || createProvider.isPending}
                  className={`px-5 py-2.5 bg-primary hover:opacity-90 text-primary-foreground text-sm rounded-lg font-semibold transition-all shadow-sm ${(validateDraft.isPending || !canSubmit || createProvider.isPending) ? 'opacity-60 cursor-not-allowed' : ''}`}
                >
                  {validateDraft.isPending ? 'Validating...' : 'Validate & Connect'}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
