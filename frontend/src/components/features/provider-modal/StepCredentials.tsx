import { useState } from 'react';
import { ChevronLeft, ChevronRight, Eye, EyeOff, ExternalLink, Lock, Server } from 'lucide-react';
import {
  Badge,
  Button,
  Field,
  InlineAlert,
  Input,
  ProviderLogo,
  Tab,
  TabList,
  Tabs,
  cn,
} from '@/components/ui';
import { useT } from '@/i18n';
import { MissingFields } from '@/components/features/providers/MissingFields';
import { healthStatusLabel } from '@/components/features/providers/providerHealth';
import { ValidationCheckLine } from '@/components/features/providers/ValidationCheckLine';
import type { ProviderCapability } from '@/types/api';
import {
  type WizardStep,
  type ProviderFormState,
  type ProviderTypeMeta,
  type ProviderValidationResult,
  describeMissing,
  fallbackIconByType,
  firstIncompleteStep,
  getPassLabel,
  getProjectUrl,
  getUserLabel,
  isUrlOptional,
  listCapabilities,
  missingFields,
  requiredFields,
} from '@/components/features/providers/providerConstants';

export type WizardMode = 'guided' | 'expert';

export interface StepCredentialsProps {
  formData: ProviderFormState;
  onChange: (key: keyof ProviderFormState, value: string) => void;
  meta?: ProviderTypeMeta;
  guidedSteps: WizardStep[];
  mode: WizardMode;
  onModeChange: (mode: WizardMode) => void;
  guidedStepIndex: number;
  onGuidedStepChange: (index: number) => void;
  validationResult: ProviderValidationResult | null;
  /** Editing an existing provider: everything prefilled, blank secret keeps the stored one. */
  editMode?: boolean;
}

const CAPABILITY_HINT: Partial<Record<ProviderCapability, string>> = {
  proxy: 'provider_modal.capabilities.proxy_hint',
  dns: 'provider_modal.capabilities.dns_hint',
  public_dns: 'provider_modal.capabilities.public_dns_hint',
  supports_tunnel: 'provider_modal.capabilities.tunnel_hint',
  certificates: 'provider_modal.capabilities.certificates_hint',
};

interface SecretInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}

/** Password / token input with a show-hide toggle in the right slot. */
function SecretInput({ value, onChange, placeholder, autoFocus }: SecretInputProps) {
  const t = useT();
  const [visible, setVisible] = useState(false);
  return (
    <Input
      type={visible ? 'text' : 'password'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      autoComplete="new-password"
      spellCheck={false}
      // eslint-disable-next-line jsx-a11y/no-autofocus -- the caller passes true only for the first field of a freshly shown step
      autoFocus={autoFocus}
      className="font-mono"
      rightIcon={
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? t('provider_modal.field.hide_password') : t('provider_modal.field.show_password')}
          aria-pressed={visible}
          className="pointer-events-auto rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      }
    />
  );
}

export function StepCredentials({
  formData,
  onChange,
  meta,
  guidedSteps,
  mode,
  onModeChange,
  guidedStepIndex,
  onGuidedStepChange,
  validationResult,
  editMode = false,
}: StepCredentialsProps) {
  const t = useT();
  const type = formData.type;
  const FallbackIcon = fallbackIconByType[type] || Server;
  const typeLabel = meta?.label || type;
  const projectUrl = getProjectUrl(type, meta);
  const capabilities = listCapabilities(meta);
  const userLabel = getUserLabel(type, meta, t);
  const passLabel = getPassLabel(type, meta, t);
  const urlOptional = isUrlOptional(type);
  const isTunnel = type === 'cloudflare_tunnel';
  const guidedAvailable = !editMode && guidedSteps.length > 0;
  const effectiveMode: WizardMode = guidedAvailable ? mode : 'expert';

  // One rule for the asterisks, the guided "Next", the dots and the footer's button
  // (`requiredFields`). There used to be two: the guided asterisks read the API's marks, the
  // expert form and the footer a local list, and "Next" and the dots read neither.
  const required = requiredFields(type, meta);
  const isRequired = (key: keyof ProviderFormState) => required.includes(key) && !(editMode && key === 'password');
  const missing = missingFields(formData, meta, { editMode });
  const optionalAddon = (key: keyof ProviderFormState) =>
    isRequired(key) ? undefined : <span className="text-xs text-muted-foreground">{t('provider_modal.guided.optional')}</span>;

  // An index past the last step was the "everything is filled in" screen, which is gone; it
  // lands on the last step instead of on nothing.
  const stepIndex = Math.min(guidedStepIndex, Math.max(guidedSteps.length - 1, 0));
  const currentStep = guidedSteps[stepIndex];
  const isLastStep = stepIndex === guidedSteps.length - 1;
  const reachable = firstIncompleteStep(guidedSteps, missing);
  const stepMissing = (currentStep?.fields ?? []).filter((field) => missing.includes(field.key)).map((field) => field.key);

  const renderGuidedField = (field: NonNullable<WizardStep['fields']>[number], index: number) => {
    const value = String(formData[field.key] ?? '');
    return (
      <Field
        key={String(field.key)}
        label={field.label}
        hint={field.hint}
        required={isRequired(field.key)}
        labelAddon={optionalAddon(field.key)}
      >
        {field.inputType === 'password' ? (
          // eslint-disable-next-line jsx-a11y/no-autofocus -- a surface the operator just opened lands focus on its first field
          <SecretInput value={value} onChange={(v) => onChange(field.key, v)} placeholder={field.placeholder} autoFocus={index === 0} />
        ) : (
          <Input
            type={field.inputType === 'url' ? 'url' : 'text'}
            value={value}
            onChange={(e) => onChange(field.key, e.target.value)}
            placeholder={field.placeholder}
            autoComplete="off"
            spellCheck={false}
            // eslint-disable-next-line jsx-a11y/no-autofocus -- a surface the operator just opened lands focus on its first field
            autoFocus={index === 0}
            className="font-mono"
          />
        )}
      </Field>
    );
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-1.5 text-sm font-semibold text-foreground">
          <ProviderLogo type={type} className="h-4 w-4" fallback={<FallbackIcon className="h-4 w-4" />} />
          {typeLabel}
        </span>
        {projectUrl && (
          <a
            href={projectUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
            {t('provider_modal.project_link')}
          </a>
        )}
      </div>

      <section className="rounded-xl border border-border bg-muted/30 p-4" aria-label={t('provider_modal.capabilities.title')}>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{t('provider_modal.capabilities.title')}</p>
        {capabilities.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">{t('provider_modal.capabilities.none')}</p>
        ) : (
          <>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {capabilities.map((cap) => (
                <Badge key={cap} tone="primary" size="sm">
                  {t(`providers.capability.${cap}`)}
                </Badge>
              ))}
              {meta?.read_only && (
                <Badge tone="neutral" size="sm" icon={<Lock />}>
                  {t('providers.card.read_only')}
                </Badge>
              )}
            </div>
            <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
              {capabilities.map((cap) => {
                const hint = CAPABILITY_HINT[cap];
                return hint ? <li key={cap}>{t(hint)}</li> : null;
              })}
              {meta?.read_only && <li>{t('provider_modal.capabilities.read_only_hint')}</li>}
            </ul>
          </>
        )}
      </section>

      {/* The name comes first, in both modes. The guided panel used to ask for it only on a
          screen of its own past the last step, reached through a button called "Name the
          integration" and topped with a green "Everything is filled in" that was printed
          whatever the steps held. */}
      <Field label={t('provider_modal.field.name')} hint={t('provider_modal.field.name_hint')} required>
        <Input
          type="text"
          value={formData.name}
          onChange={(e) => onChange('name', e.target.value)}
          placeholder={t('provider_modal.field.name_placeholder', { label: typeLabel })}
          autoComplete="off"
        />
      </Field>

      {guidedAvailable && (
        <Tabs variant="segmented" value={effectiveMode} onValueChange={(v) => onModeChange(v as WizardMode)}>
          <TabList aria-label={t('provider_modal.mode.label')}>
            <Tab value="guided">{t('provider_modal.mode.guided')}</Tab>
            <Tab value="expert">{t('provider_modal.mode.expert')}</Tab>
          </TabList>
        </Tabs>
      )}

      {effectiveMode === 'guided' && currentStep && (
        <div className="space-y-3 rounded-xl border border-primary/20 bg-primary/5 p-4">
          {/* A single step is not a journey: no "1 of 1", no lone dot to click. */}
          {guidedSteps.length > 1 && (
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary">
                {t('provider_modal.guided.step', { step: stepIndex + 1, total: guidedSteps.length })}
              </span>
              <div className="flex gap-1.5" role="group" aria-label={t('provider_modal.mode.guided')}>
                {guidedSteps.map((_, i) => {
                  const locked = i > reachable;
                  return (
                    <button
                      key={i}
                      type="button"
                      aria-label={t('provider_modal.guided.go_to', { step: i + 1 })}
                      aria-current={i === stepIndex ? 'step' : undefined}
                      disabled={locked}
                      onClick={() => onGuidedStepChange(i)}
                      className={cn(
                        'h-2 w-2 rounded-full transition-colors',
                        i === stepIndex
                          ? 'bg-primary'
                          : locked
                            ? 'cursor-not-allowed bg-muted-foreground/15'
                            : 'bg-muted-foreground/30 hover:bg-muted-foreground/60',
                      )}
                    />
                  );
                })}
              </div>
            </div>
          )}
          <p className="text-sm font-semibold text-foreground">{currentStep.title}</p>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{currentStep.body}</p>

          {currentStep.fields && currentStep.fields.length > 0 && (
            <div className="space-y-3 border-t border-primary/20 pt-3">{currentStep.fields.map(renderGuidedField)}</div>
          )}

          {/* On the last step the line covers the whole form, and a field asked on an earlier
              step is a link back to it; before that, only what holds "Next" back. */}
          <MissingFields
            fields={
              isLastStep
                ? describeMissing(missing, type, meta, t, guidedSteps, stepIndex)
                : describeMissing(stepMissing, type, meta, t, guidedSteps, stepIndex)
            }
            onGoToStep={onGuidedStepChange}
            complete={isLastStep && !validationResult ? t('provider_modal.guided.collected') : undefined}
          />

          {(stepIndex > 0 || !isLastStep) && (
            <div className="flex gap-2 pt-1">
              {stepIndex > 0 && (
                <Button type="button" size="sm" variant="outline" leftIcon={<ChevronLeft />} onClick={() => onGuidedStepChange(stepIndex - 1)}>
                  {t('provider_modal.guided.back')}
                </Button>
              )}
              {!isLastStep && (
                <Button
                  type="button"
                  size="sm"
                  rightIcon={<ChevronRight />}
                  disabled={stepMissing.length > 0}
                  onClick={() => onGuidedStepChange(stepIndex + 1)}
                >
                  {t('provider_modal.guided.next')}
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {effectiveMode === 'expert' && (
        <div className="space-y-4">
          <Field
            label={t('provider_modal.field.url')}
            hint={urlOptional ? t('provider_modal.field.url_hint_hosted', { label: typeLabel }) : undefined}
            required={isRequired('url')}
            labelAddon={optionalAddon('url')}
          >
            <Input
              type="url"
              value={formData.url}
              onChange={(e) => onChange('url', e.target.value)}
              placeholder={meta?.placeholder_url || 'https://'}
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
            />
          </Field>

          <Field label={userLabel} required={isRequired('username')} labelAddon={optionalAddon('username')}>
            <Input
              type="text"
              value={formData.username}
              onChange={(e) => onChange('username', e.target.value)}
              placeholder={meta?.user_placeholder || ''}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>

          <Field
            label={passLabel}
            required={isRequired('password')}
            hint={editMode ? t('provider_modal.field.password_edit_hint') : undefined}
            labelAddon={editMode ? undefined : optionalAddon('password')}
          >
            <SecretInput value={formData.password} onChange={(v) => onChange('password', v)} placeholder={editMode ? '••••••••' : undefined} />
          </Field>

          {isTunnel && (
            <Field
              label={t('provider_modal.field.tunnel_id')}
              hint={t('provider_modal.field.tunnel_id_hint')}
              required={isRequired('tunnel_id')}
              labelAddon={optionalAddon('tunnel_id')}
            >
              <Input
                type="text"
                value={formData.tunnel_id}
                onChange={(e) => onChange('tunnel_id', e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                autoComplete="off"
                spellCheck={false}
                className="font-mono"
              />
            </Field>
          )}

          <MissingFields fields={describeMissing(missing, type, meta, t)} />
        </div>
      )}

      {validationResult && (
        <InlineAlert
          tone={validationResult.ok ? 'success' : 'danger'}
          title={validationResult.ok ? t('provider_modal.validation.title_ok') : t('provider_modal.validation.title_failed')}
        >
          <ul className="mt-1 space-y-1 text-xs">
            {/* Every check. The list used to stop at six without a word. A Cloudflare tunnel
                answers seven when it is given a hostname, the seventh being the DNS read, but
                this dialog sends none: here it is a guard, not a fix. */}
            {(validationResult.validation?.checks || []).map((check, idx) => (
              <ValidationCheckLine key={`${check.name || 'check'}-${idx}`} check={check} />
            ))}
            {(validationResult.validation?.warnings || []).length > 0 && (
              <li className="text-warning">
                {t('provider_modal.validation.warnings')} {(validationResult.validation?.warnings || []).join(' · ')}
              </li>
            )}
            {validationResult.health?.status && (
              <li className="text-muted-foreground">
                {t('provider_modal.validation.health', { status: healthStatusLabel(validationResult.health.status, t) })}
              </li>
            )}
            {validationResult.health?.error && <li className="text-destructive">{validationResult.health.error}</li>}
          </ul>
        </InlineAlert>
      )}
    </div>
  );
}
