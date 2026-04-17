import { Server, ChevronRight } from 'lucide-react';
import { ProviderLogo } from '@/components/ui/ProviderLogos';
import {
  type ProviderFormState,
  type ProviderValidationResult,
  fallbackIconByType,
  providerColor,
  type GuidedStep,
} from '@/components/features/providers/providerConstants';

interface StepCredentialsProps {
  formData: ProviderFormState;
  setFormData: React.Dispatch<React.SetStateAction<ProviderFormState>>;
  wizardMode: 'guided' | 'expert' | null;
  guidedStepIndex: number;
  setGuidedStepIndex: React.Dispatch<React.SetStateAction<number>>;
  currentGuidedSteps: GuidedStep[];
  selectedMeta: { label?: string; placeholder_url?: string; user_placeholder?: string };
  userLabel: string;
  passLabel: string;
  validationResult: ProviderValidationResult | null;
  setValidationResult: React.Dispatch<React.SetStateAction<ProviderValidationResult | null>>;
}

export function StepCredentials({
  formData,
  setFormData,
  wizardMode,
  guidedStepIndex,
  setGuidedStepIndex,
  currentGuidedSteps,
  selectedMeta,
  userLabel,
  passLabel,
  validationResult,
  setValidationResult,
}: StepCredentialsProps) {
  const updateField = (key: keyof ProviderFormState, value: string) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
    setValidationResult(null);
  };

  return (
    <div className="space-y-5">
      {/* Provider badge + breadcrumb */}
      <div className="flex items-center gap-3 flex-wrap">
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
        {wizardMode === 'guided' && (
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            {guidedStepIndex < currentGuidedSteps.length ? (
              <>
                <span className="text-primary font-medium">Step {guidedStepIndex + 1} of {currentGuidedSteps.length}</span>
                <ChevronRight className="w-3 h-3" />
                <span>Guided setup</span>
              </>
            ) : (
              <>
                <span className="text-primary font-medium">All steps done ✓</span>
                <ChevronRight className="w-3 h-3" />
                <span className="font-medium text-foreground">Name &amp; connect</span>
              </>
            )}
          </span>
        )}
      </div>

      {/* ── GUIDED: current step card with inline fields ── */}
      {wizardMode === 'guided' && guidedStepIndex < currentGuidedSteps.length && (() => {
        const currentStep = currentGuidedSteps[guidedStepIndex];
        return (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-4 space-y-3">
            <span className="text-xs font-bold text-primary uppercase tracking-wider">
              Step {guidedStepIndex + 1} / {currentGuidedSteps.length}
            </span>
            <p className="font-semibold text-foreground text-sm">{currentStep.title}</p>
            <p className="text-sm text-muted-foreground whitespace-pre-line leading-relaxed">
              {currentStep.body}
            </p>

            {/* Inline fields for this step */}
            {currentStep.fields && currentStep.fields.length > 0 && (
              <div className="space-y-3 pt-3 border-t border-primary/20">
                {currentStep.fields.map((field) => (
                  <div key={String(field.key)} className="space-y-1.5">
                    <label className="block text-xs font-semibold text-foreground">
                      {field.label}
                    </label>
                    {field.hint && (
                      <p className="text-xs text-muted-foreground">{field.hint}</p>
                    )}
                    <input
                      type={field.inputType || 'text'}
                      value={String(formData[field.key] ?? '')}
                      onChange={(e) => updateField(field.key, e.target.value)}
                      placeholder={field.placeholder || ''}
                      className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm text-foreground outline-none transition-all shadow-sm font-mono"
                      autoComplete="off"
                    />
                  </div>
                ))}
              </div>
            )}

            <div className="flex gap-2 pt-2">
              {guidedStepIndex > 0 && (
                <button
                  type="button"
                  onClick={() => setGuidedStepIndex((i) => i - 1)}
                  className="px-3 py-1.5 text-xs border border-border rounded-md bg-card hover:bg-accent font-medium"
                >
                  ← Back
                </button>
              )}
              {guidedStepIndex < currentGuidedSteps.length - 1 ? (
                <button
                  type="button"
                  onClick={() => setGuidedStepIndex((i) => i + 1)}
                  className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 font-medium"
                >
                  Next →
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setGuidedStepIndex(currentGuidedSteps.length)}
                  className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 font-medium"
                >
                  Done — name &amp; connect ✓
                </button>
              )}
            </div>

            {/* Step dots */}
            <div className="flex gap-1.5 pt-1">
              {currentGuidedSteps.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setGuidedStepIndex(i)}
                  className={`w-2 h-2 rounded-full transition-colors ${i === guidedStepIndex ? 'bg-primary' : 'bg-muted-foreground/30'}`}
                />
              ))}
            </div>
          </div>
        );
      })()}

      {/* ── GUIDED: final step — name the integration ── */}
      {wizardMode === 'guided' && guidedStepIndex >= currentGuidedSteps.length && (
        <div className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
            <span className="text-sm text-primary font-semibold">✓ All credentials collected</span>
            <button
              type="button"
              onClick={() => setGuidedStepIndex(currentGuidedSteps.length - 1)}
              className="text-xs text-muted-foreground hover:text-foreground underline"
            >
              Review steps
            </button>
          </div>
          <div className="space-y-1.5">
            <label className="block text-xs font-semibold text-foreground uppercase tracking-wider">
              Display Name
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => updateField('name', e.target.value)}
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm"
              placeholder={`e.g. ${selectedMeta.label || formData.type} — main`}
            />
            <p className="text-xs text-muted-foreground">Friendly name shown in Vauxtra.</p>
          </div>
        </div>
      )}

      {/* ── EXPERT MODE: all fields at once ── */}
      {wizardMode === 'expert' && (
        <>
          <div>
            <h3 className="text-[15px] font-bold text-foreground mb-1">Connection Details</h3>
            <p className="text-sm text-muted-foreground">Enter the API credentials for this provider.</p>
          </div>

          <div>
            <label className="block text-xs font-semibold text-foreground uppercase tracking-wider mb-2">Display Name</label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => updateField('name', e.target.value)}
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm"
              placeholder="e.g. Main Cloudflare account"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-foreground uppercase tracking-wider mb-2">URL / Host</label>
            <input
              type="url"
              value={formData.url}
              onChange={(e) => updateField('url', e.target.value)}
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground placeholder:text-muted-foreground outline-none transition-all shadow-sm font-mono"
              placeholder={selectedMeta.placeholder_url || 'https://...'}
            />
            {(formData.type === 'cloudflare' || formData.type === 'cloudflare_tunnel') && (
              <p className="mt-1 text-xs text-muted-foreground">Leave empty to use default Cloudflare API endpoint.</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-semibold text-foreground uppercase tracking-wider mb-2">{userLabel}</label>
            <input
              type="text"
              value={formData.username}
              onChange={(e) => updateField('username', e.target.value)}
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm"
              placeholder={selectedMeta.user_placeholder || ''}
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-foreground uppercase tracking-wider mb-2">{passLabel}</label>
            <input
              type="password"
              value={formData.password}
              onChange={(e) => updateField('password', e.target.value)}
              className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm font-mono"
              placeholder="••••••••"
            />
          </div>

          {formData.type === 'cloudflare_tunnel' && (
            <div>
              <label className="block text-xs font-semibold text-foreground uppercase tracking-wider mb-2">Tunnel ID</label>
              <input
                type="text"
                value={formData.tunnel_id}
                onChange={(e) => updateField('tunnel_id', e.target.value)}
                className="w-full bg-card border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 rounded-lg px-4 py-2.5 text-sm font-medium text-foreground outline-none transition-all shadow-sm font-mono"
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                The UUID shown on the tunnel overview page in Cloudflare Zero Trust.
              </p>
            </div>
          )}
        </>
      )}

      {/* Validation result — shown in both modes */}
      {validationResult && (
        <div className={`rounded-lg border p-3 text-xs ${validationResult.ok ? 'border-primary/30 bg-primary/5' : 'border-destructive/30 bg-destructive/5'}`}>
          <p className={`font-semibold ${validationResult.ok ? 'text-primary' : 'text-destructive'}`}>
            Validation {validationResult.ok ? 'OK' : 'failed'}
          </p>
          <div className="mt-2 space-y-1 text-muted-foreground">
            {(validationResult.validation?.checks || []).slice(0, 5).map((check, idx) => (
              <p key={`${check.name || 'check'}-${idx}`}
                className={check.ok ? 'text-primary' : 'text-destructive'}>
                {check.ok ? '✓' : '✗'} {check.name || 'check'}{check.detail ? `: ${check.detail}` : ''}
              </p>
            ))}
            {validationResult.health?.status && (
              <p>Health: {validationResult.health.status}</p>
            )}
            {validationResult.health?.error && (
              <p className="text-destructive">{validationResult.health.error}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
