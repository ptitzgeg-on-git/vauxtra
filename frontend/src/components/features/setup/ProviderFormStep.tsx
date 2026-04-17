import { useMemo } from 'react';
import {
  ArrowLeft, ArrowRight, GitMerge, Server, X, Loader2, CheckCircle2, AlertTriangle,
  BookOpen, Zap, ChevronRight, Shield, Plus,
} from 'lucide-react';
import { ProviderLogo } from '@/components/ui/ProviderLogos';
import {
  type ProviderFormState,
  type ProviderValidationResult,
  type ProviderTypeMeta,
  type GuidedStep,
  fallbackIconByType as iconByType,
  descByType,
  categoryByType,
  providerColor,
  getGuidedSteps,
  canSubmitProvider as canSubmitProviderFn,
} from '@/components/features/providers/providerConstants';

interface ProviderFormStepProps {
  formData: ProviderFormState;
  setFormData: React.Dispatch<React.SetStateAction<ProviderFormState>>;
  wizardMode: 'guided' | 'expert' | null;
  setWizardMode: (mode: 'guided' | 'expert' | null) => void;
  guidedStepIndex: number;
  setGuidedStepIndex: (i: number) => void;
  validationResult: ProviderValidationResult | null;
  setValidationResult: (r: ProviderValidationResult | null) => void;
  providerTypes: Record<string, ProviderTypeMeta> | undefined;
  onCancel: () => void;
  onValidate: () => void;
  validateIsPending: boolean;
  onCreate: () => void;
  createIsPending: boolean;
}

export function ProviderFormStep({
  formData, setFormData,
  wizardMode, setWizardMode,
  guidedStepIndex, setGuidedStepIndex,
  validationResult, setValidationResult,
  providerTypes,
  onCancel, onValidate, validateIsPending, onCreate, createIsPending,
}: ProviderFormStepProps) {
  const availableProviderTypes = useMemo(() => {
    const entries = Object.entries(providerTypes || {}).filter(([, meta]) => Boolean(meta?.available));
    return entries.sort((a, b) => String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0])));
  }, [providerTypes]);

  const selectedMeta = (providerTypes || {})[formData.type] || {};
  const currentGuidedSteps: GuidedStep[] = getGuidedSteps(formData.type, selectedMeta);
  const currentGuidedStep = currentGuidedSteps[guidedStepIndex];
  const canSubmitProvider = canSubmitProviderFn(formData);

  const chooseProviderType = (type: string, label: string) => {
    setFormData((prev) => ({
      ...prev,
      type,
      name: prev.name.trim() ? prev.name : label,
    }));
    setValidationResult(null);
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
            <GitMerge size={20} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-foreground">
              {formData.type ? (selectedMeta.label || formData.type) : 'Choose Provider'}
            </h2>
            <p className="text-sm text-muted-foreground">
              {formData.type
                ? wizardMode === 'guided'
                  ? `Step ${guidedStepIndex + 1} of ${currentGuidedSteps.length}`
                  : 'Enter connection details'
                : 'Select the service you want to connect'}
            </p>
          </div>
        </div>
        <button
          onClick={onCancel}
          className="p-2 text-muted-foreground hover:text-foreground bg-muted hover:bg-accent rounded-lg transition-colors"
          aria-label="Cancel"
        >
          <X size={18} />
        </button>
      </div>

      {/* Type selection */}
      {!formData.type && (
        <div className="bg-card border border-border rounded-xl p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {availableProviderTypes.map(([type, meta]) => {
              const FallbackIcon = iconByType[type] || Server;
              return (
                <button
                  key={type}
                  onClick={() => chooseProviderType(type, String(meta.label || type))}
                  className="flex items-start gap-4 p-4 rounded-xl text-left border border-border bg-background hover:border-primary/40 hover:bg-primary/5 transition-all"
                >
                  <div className={`p-2.5 rounded-lg border ${providerColor[type] || 'bg-primary/10 text-primary border-primary/20'}`}>
                    <ProviderLogo type={type} className="w-6 h-6" fallback={<FallbackIcon className="w-6 h-6" />} />
                  </div>
                  <div className="min-w-0">
                    <div className="font-semibold text-sm text-foreground">{meta.label || type}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{descByType[type]}</div>
                    {categoryByType[type] && (
                      <span className={`inline-block mt-2 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${categoryByType[type].color}`}>
                        {categoryByType[type].label}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Mode selection */}
      {formData.type && !wizardMode && currentGuidedSteps.length > 0 && (
        <div className="bg-card border border-border rounded-xl p-6 space-y-4">
          <p className="text-sm text-muted-foreground">How do you want to set this up?</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <button
              onClick={() => { setWizardMode('guided'); setGuidedStepIndex(0); }}
              className="flex flex-col items-start gap-3 p-5 rounded-xl border-2 border-primary/30 bg-primary/5 hover:bg-primary/10 hover:border-primary/50 transition-all text-left"
            >
              <div className="p-2 rounded-lg bg-primary/10 border border-primary/20 text-primary">
                <BookOpen className="w-5 h-5" />
              </div>
              <div>
                <p className="font-semibold text-foreground">Guided setup</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Step-by-step instructions ({currentGuidedSteps.length} steps)
                </p>
              </div>
              <span className="text-xs text-primary font-semibold flex items-center gap-1">
                Recommended <ChevronRight className="w-3.5 h-3.5" />
              </span>
            </button>

            <button
              onClick={() => setWizardMode('expert')}
              className="flex flex-col items-start gap-3 p-5 rounded-xl border border-border bg-background hover:bg-muted transition-all text-left"
            >
              <div className="p-2 rounded-lg bg-muted border border-border text-muted-foreground">
                <Zap className="w-5 h-5" />
              </div>
              <div>
                <p className="font-semibold text-foreground">Quick setup</p>
                <p className="text-xs text-muted-foreground mt-1">
                  I already have my credentials ready
                </p>
              </div>
            </button>
          </div>
        </div>
      )}

      {/* No guided steps available */}
      {formData.type && !wizardMode && currentGuidedSteps.length === 0 && (
        <div className="bg-card border border-border rounded-xl p-6">
          <p className="text-sm text-muted-foreground mb-4">Enter connection details for {selectedMeta.label || formData.type}:</p>
        </div>
      )}

      {/* Guided mode */}
      {formData.type && wizardMode === 'guided' && currentGuidedStep && (
        <div className="bg-card border border-border rounded-xl p-6 space-y-5">
          <div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground uppercase tracking-wide mb-2">
              <span className="bg-primary/10 text-primary px-2 py-0.5 rounded font-bold">
                Step {guidedStepIndex + 1}/{currentGuidedSteps.length}
              </span>
            </div>
            <h3 className="text-lg font-bold text-foreground">{currentGuidedStep.title}</h3>
          </div>

          <div className="bg-muted/50 border border-border rounded-lg p-4">
            <pre className="text-sm text-muted-foreground whitespace-pre-wrap font-sans leading-relaxed">
              {currentGuidedStep.body}
            </pre>
          </div>

          {currentGuidedStep.fields && (
            <div className="space-y-4">
              {currentGuidedStep.fields.map((field) => (
                <div key={field.key} className="space-y-1.5">
                  <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    {field.label}
                  </label>
                  <input
                    type={field.inputType || 'text'}
                    value={formData[field.key]}
                    onChange={(e) => setFormData((prev) => ({ ...prev, [field.key]: e.target.value }))}
                    placeholder={field.placeholder}
                    className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono"
                  />
                  {field.hint && (
                    <p className="text-xs text-muted-foreground">{field.hint}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Validation result inline */}
          {guidedStepIndex === currentGuidedSteps.length - 1 && validationResult && (
            <div className={`p-4 rounded-lg border ${validationResult.ok ? 'bg-green-500/5 border-green-500/30' : 'bg-destructive/5 border-destructive/30'}`}>
              <div className="flex items-center gap-2">
                {validationResult.ok ? (
                  <CheckCircle2 size={16} className="text-green-600 dark:text-green-400" />
                ) : (
                  <AlertTriangle size={16} className="text-destructive" />
                )}
                <span className={`text-sm font-semibold ${validationResult.ok ? 'text-green-600 dark:text-green-400' : 'text-destructive'}`}>
                  {validationResult.ok ? 'Connection successful!' : 'Connection failed'}
                </span>
              </div>
              {!validationResult.ok && validationResult.validation?.checks && (
                <ul className="text-xs space-y-1 text-muted-foreground mt-2">
                  {validationResult.validation.checks.filter(c => !c.ok).map((check, i) => (
                    <li key={i} className="flex items-center gap-1.5">
                      <X size={12} className="text-destructive" />
                      {check.name}: {check.detail}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="flex justify-between pt-2">
            <button
              onClick={() => {
                if (guidedStepIndex > 0) {
                  setGuidedStepIndex(guidedStepIndex - 1);
                  setValidationResult(null);
                } else {
                  setWizardMode(null);
                }
              }}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft size={14} />
              Back
            </button>
            {guidedStepIndex < currentGuidedSteps.length - 1 ? (
              <button
                onClick={() => setGuidedStepIndex(guidedStepIndex + 1)}
                disabled={
                  currentGuidedStep.fields &&
                  currentGuidedStep.fields.some((f) => !f.optional && !formData[f.key]?.trim())
                }
                className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
              >
                Next
                <ArrowRight size={16} />
              </button>
            ) : validationResult?.ok ? (
              <button
                onClick={onCreate}
                disabled={createIsPending}
                className="inline-flex items-center gap-2 bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
              >
                {createIsPending ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                Add {selectedMeta.label || formData.type}
              </button>
            ) : (
              <button
                onClick={() => { if (canSubmitProvider) onValidate(); }}
                disabled={
                  !canSubmitProvider || validateIsPending ||
                  (currentGuidedStep.fields && currentGuidedStep.fields.some((f) => !f.optional && !formData[f.key]?.trim()))
                }
                className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
              >
                {validateIsPending ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                Validate & Connect
              </button>
            )}
          </div>
        </div>
      )}

      {/* Expert mode */}
      {formData.type && wizardMode === 'expert' && (
        <div className="bg-card border border-border rounded-xl p-6 space-y-5">
          <div className="grid grid-cols-1 gap-4">
            <div className="space-y-1.5">
              <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">Display name</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                placeholder={selectedMeta.label || formData.type}
                className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>

            {formData.type !== 'cloudflare' && formData.type !== 'cloudflare_tunnel' && (
              <div className="space-y-1.5">
                <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">URL</label>
                <input
                  type="url"
                  value={formData.url}
                  onChange={(e) => setFormData((prev) => ({ ...prev, url: e.target.value }))}
                  placeholder={selectedMeta.placeholder_url || 'http://...'}
                  className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono"
                />
              </div>
            )}

            {formData.type === 'cloudflare_tunnel' && (
              <div className="space-y-1.5">
                <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">Tunnel ID</label>
                <input
                  type="text"
                  value={formData.tunnel_id}
                  onChange={(e) => setFormData((prev) => ({ ...prev, tunnel_id: e.target.value }))}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono"
                />
              </div>
            )}

            {(formData.type !== 'traefik' || formData.username) && (
              <div className="space-y-1.5">
                <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                  {selectedMeta.user_label || (formData.type === 'cloudflare' ? 'Email' : formData.type === 'cloudflare_tunnel' ? 'Account ID' : 'Username')}
                </label>
                <input
                  type="text"
                  value={formData.username}
                  onChange={(e) => setFormData((prev) => ({ ...prev, username: e.target.value }))}
                  placeholder={selectedMeta.user_label || 'Username or email'}
                  className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            )}

            <div className="space-y-1.5">
              <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                {selectedMeta.pass_label || (formData.type.startsWith('cloudflare') ? 'API Token' : 'Password / Token')}
              </label>
              <input
                type="password"
                value={formData.password}
                onChange={(e) => setFormData((prev) => ({ ...prev, password: e.target.value }))}
                placeholder="••••••••"
                className="w-full bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>

          {validationResult && (
            <div className={`p-4 rounded-lg border ${validationResult.ok ? 'bg-green-500/5 border-green-500/30' : 'bg-destructive/5 border-destructive/30'}`}>
              <div className="flex items-center gap-2 mb-2">
                {validationResult.ok ? (
                  <CheckCircle2 size={16} className="text-green-600 dark:text-green-400" />
                ) : (
                  <AlertTriangle size={16} className="text-destructive" />
                )}
                <span className={`text-sm font-semibold ${validationResult.ok ? 'text-green-600 dark:text-green-400' : 'text-destructive'}`}>
                  {validationResult.ok ? 'Validation passed' : 'Validation failed'}
                </span>
              </div>
              {validationResult.validation?.checks && (
                <ul className="text-xs space-y-1 text-muted-foreground">
                  {validationResult.validation.checks.map((check, i) => (
                    <li key={i} className="flex items-center gap-1.5">
                      {check.ok ? (
                        <CheckCircle2 size={12} className="text-green-600 dark:text-green-400" />
                      ) : (
                        <X size={12} className="text-destructive" />
                      )}
                      {check.name}: {check.detail}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="flex justify-between pt-2">
            <button
              onClick={() => setWizardMode(null)}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft size={14} />
              Back
            </button>
            {validationResult?.ok ? (
              <button
                onClick={onCreate}
                disabled={!canSubmitProvider || createIsPending}
                className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all disabled:opacity-50"
              >
                {createIsPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Add provider
              </button>
            ) : (
              <button
                onClick={onValidate}
                disabled={!canSubmitProvider || validateIsPending}
                className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all disabled:opacity-50"
              >
                {validateIsPending ? <Loader2 size={14} className="animate-spin" /> : <Shield size={14} />}
                Validate & Connect
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
