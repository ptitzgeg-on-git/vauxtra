import { useState } from 'react';
import { Check, ChevronLeft, ChevronRight, Eye, EyeOff, ExternalLink, Lock, Server, X } from 'lucide-react';
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
import type { ProviderCapability } from '@/types/api';
import {
  type GuidedStep,
  type ProviderFormState,
  type ProviderTypeMeta,
  type ProviderValidationResult,
  fallbackIconByType,
  getPassLabel,
  getProjectUrl,
  getUserLabel,
  isUrlOptional,
  listCapabilities,
  requiresPassword,
  requiresUsername,
} from '@/components/features/providers/providerConstants';
import { checkDetailText, checkLabelText } from '@/components/features/providers/providerHealth';

export type WizardMode = 'guided' | 'expert';

export interface StepCredentialsProps {
  formData: ProviderFormState;
  onChange: (key: keyof ProviderFormState, value: string) => void;
  meta?: ProviderTypeMeta;
  guidedSteps: GuidedStep[];
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
  const needsUsername = requiresUsername(type, meta);
  const needsPassword = !editMode && requiresPassword(type, meta);
  const urlOptional = isUrlOptional(type);
  const isTunnel = type === 'cloudflare_tunnel';
  const guidedAvailable = !editMode && guidedSteps.length > 0;
  const effectiveMode: WizardMode = guidedAvailable ? mode : 'expert';
  const guidedDone = guidedStepIndex >= guidedSteps.length;
  const currentStep = guidedSteps[guidedStepIndex];

  const renderGuidedField = (field: NonNullable<GuidedStep['fields']>[number], index: number) => {
    const optional = field.optional || (field.key === 'url' && urlOptional);
    const value = String(formData[field.key] ?? '');
    return (
      <Field
        key={String(field.key)}
        label={field.label}
        hint={field.hint}
        required={!optional}
        labelAddon={optional ? <span className="text-xs text-muted-foreground">{t('provider_modal.guided.optional')}</span> : undefined}
      >
        {field.inputType === 'password' ? (
          <SecretInput value={value} onChange={(v) => onChange(field.key, v)} placeholder={field.placeholder} autoFocus={index === 0} />
        ) : (
          <Input
            type={field.inputType === 'url' ? 'url' : 'text'}
            value={value}
            onChange={(e) => onChange(field.key, e.target.value)}
            placeholder={field.placeholder}
            autoComplete="off"
            spellCheck={false}
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

      {guidedAvailable && (
        <Tabs variant="segmented" value={effectiveMode} onValueChange={(v) => onModeChange(v as WizardMode)}>
          <TabList aria-label={t('provider_modal.mode.label')}>
            <Tab value="guided">{t('provider_modal.mode.guided')}</Tab>
            <Tab value="expert">{t('provider_modal.mode.expert')}</Tab>
          </TabList>
        </Tabs>
      )}

      {effectiveMode === 'guided' && !guidedDone && currentStep && (
        <div className="space-y-3 rounded-xl border border-primary/20 bg-primary/5 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-primary">
              {t('provider_modal.guided.step', { step: guidedStepIndex + 1, total: guidedSteps.length })}
            </span>
            <div className="flex gap-1.5" role="list" aria-label={t('provider_modal.mode.guided')}>
              {guidedSteps.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  role="listitem"
                  aria-label={t('provider_modal.guided.go_to', { step: i + 1 })}
                  aria-current={i === guidedStepIndex ? 'step' : undefined}
                  onClick={() => onGuidedStepChange(i)}
                  className={cn('h-2 w-2 rounded-full transition-colors', i === guidedStepIndex ? 'bg-primary' : 'bg-muted-foreground/30 hover:bg-muted-foreground/60')}
                />
              ))}
            </div>
          </div>
          <p className="text-sm font-semibold text-foreground">{currentStep.title}</p>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{currentStep.body}</p>

          {currentStep.fields && currentStep.fields.length > 0 && (
            <div className="space-y-3 border-t border-primary/20 pt-3">{currentStep.fields.map(renderGuidedField)}</div>
          )}

          <div className="flex gap-2 pt-1">
            {guidedStepIndex > 0 && (
              <Button type="button" size="sm" variant="outline" leftIcon={<ChevronLeft />} onClick={() => onGuidedStepChange(guidedStepIndex - 1)}>
                {t('provider_modal.guided.back')}
              </Button>
            )}
            {guidedStepIndex < guidedSteps.length - 1 ? (
              <Button type="button" size="sm" rightIcon={<ChevronRight />} onClick={() => onGuidedStepChange(guidedStepIndex + 1)}>
                {t('provider_modal.guided.next')}
              </Button>
            ) : (
              <Button type="button" size="sm" rightIcon={<Check />} onClick={() => onGuidedStepChange(guidedSteps.length)}>
                {t('provider_modal.guided.finish')}
              </Button>
            )}
          </div>
        </div>
      )}

      {effectiveMode === 'guided' && guidedDone && (
        <div className="space-y-4">
          <InlineAlert
            tone="success"
            title={t('provider_modal.guided.collected')}
            action={
              <Button type="button" size="sm" variant="ghost" onClick={() => onGuidedStepChange(guidedSteps.length - 1)}>
                {t('provider_modal.guided.review')}
              </Button>
            }
          />
          <Field label={t('provider_modal.field.name')} hint={t('provider_modal.field.name_hint')} required>
            <Input
              type="text"
              value={formData.name}
              onChange={(e) => onChange('name', e.target.value)}
              placeholder={t('provider_modal.field.name_placeholder', { label: typeLabel })}
              autoFocus
            />
          </Field>
        </div>
      )}

      {effectiveMode === 'expert' && (
        <div className="space-y-4">
          <Field label={t('provider_modal.field.name')} hint={t('provider_modal.field.name_hint')} required>
            <Input
              type="text"
              value={formData.name}
              onChange={(e) => onChange('name', e.target.value)}
              placeholder={t('provider_modal.field.name_placeholder', { label: typeLabel })}
            />
          </Field>

          <Field
            label={t('provider_modal.field.url')}
            hint={urlOptional ? t('provider_modal.field.url_hint_cloudflare') : undefined}
            required={!urlOptional}
            labelAddon={urlOptional ? <span className="text-xs text-muted-foreground">{t('provider_modal.guided.optional')}</span> : undefined}
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

          <Field
            label={userLabel}
            required={needsUsername}
            labelAddon={!needsUsername ? <span className="text-xs text-muted-foreground">{t('provider_modal.guided.optional')}</span> : undefined}
          >
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
            required={needsPassword}
            hint={editMode ? t('provider_modal.field.password_edit_hint') : undefined}
            labelAddon={!needsPassword && !editMode ? <span className="text-xs text-muted-foreground">{t('provider_modal.guided.optional')}</span> : undefined}
          >
            <SecretInput value={formData.password} onChange={(v) => onChange('password', v)} placeholder={editMode ? '••••••••' : undefined} />
          </Field>

          {isTunnel && (
            <Field label={t('provider_modal.field.tunnel_id')} hint={t('provider_modal.field.tunnel_id_hint')} required>
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
        </div>
      )}

      {validationResult && (
        <InlineAlert
          tone={validationResult.ok ? 'success' : 'danger'}
          title={validationResult.ok ? t('provider_modal.validation.title_ok') : t('provider_modal.validation.title_failed')}
        >
          <ul className="mt-1 space-y-1 text-xs">
            {(validationResult.validation?.checks || []).slice(0, 6).map((check, idx) => {
              // Same reason as the setup wizard: the check name and its detail both arrive in
              // English from the API, and both have a locale key waiting for them.
              const label = checkLabelText(check.name, t);
              const detail = checkDetailText(check, t);
              return (
                <li key={`${check.name || 'check'}-${idx}`} className={cn('flex items-start gap-1.5', check.ok ? 'text-success' : 'text-destructive')}>
                  {check.ok ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> : <X className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
                  <span>
                    {label || t('provider_modal.validation.check_fallback')}
                    {detail ? `: ${detail}` : ''}
                  </span>
                </li>
              );
            })}
            {(validationResult.validation?.warnings || []).length > 0 && (
              <li className="text-warning">
                {t('provider_modal.validation.warnings')} {(validationResult.validation?.warnings || []).join(' · ')}
              </li>
            )}
            {validationResult.health?.status && (
              <li className="text-muted-foreground">{t('provider_modal.validation.health', { status: validationResult.health.status })}</li>
            )}
            {validationResult.health?.error && <li className="text-destructive">{validationResult.health.error}</li>}
          </ul>
        </InlineAlert>
      )}
    </div>
  );
}
