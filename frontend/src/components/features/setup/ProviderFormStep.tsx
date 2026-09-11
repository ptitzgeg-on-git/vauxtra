/**
 * Adding one integration, inside the wizard: pick a type, choose guided or expert, fill it in,
 * validate, connect.
 *
 * The wording is shared with the Integrations modal (`provider_modal.*`) so the same screen
 * never has two names, and the grouping comes from `getProviderGroup`, the same function the
 * Integrations page uses. Colours come from `toneClasses`: the `provider_color` and
 * `category_color` the API sends are raw palette classes (`bg-orange-500/10`, `text-blue-600`)
 * and no screen reads them.
 */

import { useMemo, useState } from 'react';
import { AlertTriangle, BookOpen, CheckCircle2, ChevronRight, Eye, EyeOff, GitMerge, Plus, Server, Shield, X, Zap } from 'lucide-react';
import {
  Badge,
  Card,
  CardContent,
  Field,
  IconButton,
  InlineAlert,
  Input,
  ProviderLogo,
  cn,
  toneClasses,
  type Tone,
} from '@/components/ui';
import {
  PROVIDER_GROUPS,
  canSubmitProvider as canSubmitProviderFn,
  fallbackIconByType,
  getDescription,
  getGuidedSteps,
  getPassLabel,
  getProviderGroup,
  getUserLabel,
  type GuidedStep,
  type ProviderFormState,
  type ProviderGroup,
  type ProviderTypeMeta,
  type ProviderValidationResult,
} from '@/components/features/providers/providerConstants';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';

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

const GROUP_TITLE_KEY: Record<ProviderGroup, string> = {
  reverse: 'providers.section.reverse',
  tunnel: 'providers.section.tunnel',
  dns: 'providers.section.dns',
  other: 'providers.section.other',
};

const GROUP_TONE: Record<ProviderGroup, Tone> = {
  reverse: 'primary',
  tunnel: 'primary',
  dns: 'info',
  other: 'neutral',
};

/** True when a guided step still has a required field the user has not filled. */
function stepIncomplete(step: GuidedStep | undefined, formData: ProviderFormState): boolean {
  if (!step?.fields) return false;
  return step.fields.some((f) => !f.optional && !formData[f.key]?.trim());
}

export function ProviderFormStep({
  formData,
  setFormData,
  wizardMode,
  setWizardMode,
  guidedStepIndex,
  setGuidedStepIndex,
  validationResult,
  setValidationResult,
  providerTypes,
  onCancel,
  onValidate,
  validateIsPending,
  onCreate,
  createIsPending,
}: ProviderFormStepProps) {
  const t = useT();
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  const grouped = useMemo(() => {
    const available = Object.entries(providerTypes || {})
      .filter(([, meta]) => Boolean(meta?.available))
      .sort((a, b) => String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0])));

    return PROVIDER_GROUPS.map((group) => ({
      group,
      items: available.filter(([type, meta]) => getProviderGroup(type, meta) === group),
    })).filter((entry) => entry.items.length > 0);
  }, [providerTypes]);

  const selectedMeta: ProviderTypeMeta = (providerTypes || {})[formData.type] || {};
  const selectedLabel = String(selectedMeta.label || formData.type);
  const guidedSteps = getGuidedSteps(formData.type, selectedMeta, t);
  const currentGuidedStep = guidedSteps[guidedStepIndex];
  const canSubmitProvider = canSubmitProviderFn(formData, selectedMeta);

  const isLastGuided = guidedStepIndex === guidedSteps.length - 1;
  const showExpert = wizardMode === 'expert' || (wizardMode === null && formData.type !== '' && guidedSteps.length === 0);
  const showGuided = wizardMode === 'guided' && Boolean(currentGuidedStep);
  const showModeChoice = Boolean(formData.type) && wizardMode === null && guidedSteps.length > 0;

  const chooseProviderType = (type: string, label: string) => {
    setFormData((prev) => ({ ...prev, type, name: prev.name.trim() ? prev.name : label }));
    setValidationResult(null);
  };

  const clearType = () => {
    setFormData((prev) => ({ ...prev, type: '' }));
    setWizardMode(null);
    setGuidedStepIndex(0);
    setValidationResult(null);
  };

  const setValue = (key: keyof ProviderFormState, value: string) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  const toggleReveal = (key: string) => setRevealed((prev) => ({ ...prev, [key]: !prev[key] }));

  const passwordToggle = (key: string) => (
    <button
      type="button"
      onClick={() => toggleReveal(key)}
      aria-label={revealed[key] ? t('provider_modal.field.hide_password') : t('provider_modal.field.show_password')}
      aria-pressed={Boolean(revealed[key])}
      tabIndex={-1}
      className="rounded-md p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
    >
      {revealed[key] ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
    </button>
  );

  // ─── header ────────────────────────────────────────────────────
  const title = formData.type ? selectedLabel : t('provider_modal.type.title');
  const description = !formData.type
    ? t('provider_modal.type.description')
    : showGuided
      ? t('provider_modal.guided.step', { step: guidedStepIndex + 1, total: guidedSteps.length })
      : showModeChoice
        ? t('provider_modal.mode.label')
        : t('setup.provider_form.expert_subtitle');

  // ─── back ──────────────────────────────────────────────────────
  const back = !formData.type
    ? onCancel
    : showGuided && guidedStepIndex > 0
      ? () => {
          setGuidedStepIndex(guidedStepIndex - 1);
          setValidationResult(null);
        }
      : guidedSteps.length > 0 && wizardMode !== null
        ? () => setWizardMode(null)
        : clearType;

  const backLabel = formData.type ? undefined : t('common.cancel');

  // ─── primary ───────────────────────────────────────────────────
  const busy = validateIsPending || createIsPending;

  const primary = showGuided
    ? !isLastGuided
      ? {
          label: t('provider_modal.guided.next'),
          onClick: () => setGuidedStepIndex(guidedStepIndex + 1),
          disabled: stepIncomplete(currentGuidedStep, formData),
        }
      : validationResult?.ok
        ? {
            label: t('provider_modal.footer.connect'),
            onClick: onCreate,
            disabled: !canSubmitProvider || busy,
            loading: createIsPending,
            icon: <Plus />,
          }
        : {
            label: validateIsPending ? t('provider_modal.footer.validating') : t('provider_modal.footer.validate'),
            onClick: onValidate,
            disabled: !canSubmitProvider || busy || stepIncomplete(currentGuidedStep, formData),
            loading: validateIsPending,
            icon: <Shield />,
          }
    : showExpert
      ? validationResult?.ok
        ? {
            label: t('provider_modal.footer.connect'),
            onClick: onCreate,
            disabled: !canSubmitProvider || busy,
            loading: createIsPending,
            icon: <Plus />,
          }
        : {
            label: validateIsPending ? t('provider_modal.footer.validating') : t('provider_modal.footer.validate'),
            onClick: onValidate,
            disabled: !canSubmitProvider || busy,
            loading: validateIsPending,
            icon: <Shield />,
          }
      : undefined;

  const validationBlock = validationResult ? (
    <InlineAlert
      tone={validationResult.ok ? 'success' : 'danger'}
      title={validationResult.ok ? t('provider_modal.validation.title_ok') : t('provider_modal.validation.title_failed')}
    >
      {validationResult.validation?.checks && validationResult.validation.checks.length > 0 && (
        <ul className="space-y-1">
          {validationResult.validation.checks
            .filter((check) => (validationResult.ok ? true : !check.ok))
            .map((check, i) => (
              <li key={i} className="flex items-start gap-1.5">
                {check.ok ? (
                  <CheckCircle2 aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0 text-success" />
                ) : (
                  <X aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />
                )}
                <span>
                  <span className="font-medium">{check.name || t('provider_modal.validation.check_fallback')}</span>
                  {check.detail ? ` — ${check.detail}` : ''}
                </span>
              </li>
            ))}
        </ul>
      )}
      {validationResult.health?.status && (
        <p className="mt-1">{t('provider_modal.validation.health', { status: validationResult.health.status })}</p>
      )}
    </InlineAlert>
  ) : null;

  return (
    <SetupStepShell
      icon={<GitMerge />}
      title={title}
      description={description}
      headerAside={<IconButton label={t('common.cancel')} icon={<X />} tooltip onClick={onCancel} className="text-muted-foreground" />}
      onBack={back}
      backLabel={backLabel}
      backDisabled={busy}
      primary={primary}
      bare
    >
      {/* 1 ─ type picker */}
      {!formData.type && (
        <div className="space-y-6">
          {grouped.length === 0 && (
            <Card>
              <CardContent className="p-6">
                <p className="text-sm text-muted-foreground">{t('provider_modal.type.empty')}</p>
              </CardContent>
            </Card>
          )}
          {grouped.map(({ group, items }) => {
            const tone = toneClasses(GROUP_TONE[group]);
            return (
              <section key={group} className="space-y-3">
                <h3 className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  {t(GROUP_TITLE_KEY[group])}
                </h3>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {items.map(([type, meta]) => {
                    const FallbackIcon = fallbackIconByType[type] || Server;
                    return (
                      <button
                        key={type}
                        type="button"
                        onClick={() => chooseProviderType(type, String(meta.label || type))}
                        className="flex items-start gap-3 rounded-xl border border-border bg-card p-4 text-left shadow-card transition-[transform,box-shadow,border-color] duration-200 ease-out-expo hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-elevated focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <span className={cn('grid h-11 w-11 shrink-0 place-items-center rounded-lg border', tone.bg, tone.text, tone.border)}>
                          <ProviderLogo type={type} className="h-5 w-5" fallback={<FallbackIcon className="h-5 w-5" />} />
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-semibold text-foreground">{meta.label || type}</span>
                          <span className="mt-0.5 block text-xs text-muted-foreground">{getDescription(type, meta, t)}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {/* 2 ─ guided or expert */}
      {showModeChoice && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => {
              setWizardMode('guided');
              setGuidedStepIndex(0);
            }}
            className="flex h-full flex-col items-start gap-3 rounded-xl border-2 border-primary/30 bg-primary/5 p-5 text-left transition-colors hover:border-primary/50 hover:bg-primary/10 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span aria-hidden="true" className="grid h-10 w-10 place-items-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
              <BookOpen className="h-5 w-5" />
            </span>
            <span className="block">
              <span className="block text-sm font-semibold text-foreground">{t('provider_modal.mode.guided')}</span>
              <span className="mt-1 block text-xs text-muted-foreground">
                {t('setup.provider_form.guided_hint', { count: guidedSteps.length })}
              </span>
            </span>
            <span className="mt-auto inline-flex items-center gap-1 pt-1 text-xs font-semibold text-primary">
              {t('setup.password.recommended')}
              <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
            </span>
          </button>

          <button
            type="button"
            onClick={() => setWizardMode('expert')}
            className="flex h-full flex-col items-start gap-3 rounded-xl border-2 border-transparent bg-muted/50 p-5 text-left transition-colors hover:border-border hover:bg-muted focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span aria-hidden="true" className="grid h-10 w-10 place-items-center rounded-lg border border-border bg-muted text-muted-foreground">
              <Zap className="h-5 w-5" />
            </span>
            <span className="block">
              <span className="block text-sm font-semibold text-foreground">{t('provider_modal.mode.expert')}</span>
              <span className="mt-1 block text-xs text-muted-foreground">{t('setup.provider_form.expert_hint')}</span>
            </span>
          </button>
        </div>
      )}

      {/* 3 ─ guided */}
      {showGuided && currentGuidedStep && (
        <Card>
          <CardContent className="space-y-5 p-5 sm:p-6">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="primary" size="sm" className="nums">
                {t('provider_modal.guided.step', { step: guidedStepIndex + 1, total: guidedSteps.length })}
              </Badge>
              <h3 className="text-base font-semibold text-foreground">{currentGuidedStep.title}</h3>
            </div>

            <p className="whitespace-pre-wrap rounded-xl border border-border bg-muted/50 p-4 text-sm leading-relaxed text-muted-foreground">
              {currentGuidedStep.body}
            </p>

            {currentGuidedStep.fields?.map((field) => {
              const id = `vx-guided-${field.key}`;
              const isSecret = field.inputType === 'password';
              return (
                <Field
                  key={field.key}
                  label={field.label}
                  htmlFor={id}
                  hint={field.hint}
                  labelAddon={
                    field.optional ? (
                      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        {t('provider_modal.guided.optional')}
                      </span>
                    ) : undefined
                  }
                >
                  <Input
                    id={id}
                    type={isSecret && !revealed[field.key] ? 'password' : field.inputType === 'url' ? 'url' : 'text'}
                    value={formData[field.key]}
                    onChange={(e) => setValue(field.key, e.target.value)}
                    placeholder={field.placeholder}
                    className={isSecret ? undefined : 'font-mono'}
                    autoComplete={isSecret ? 'off' : undefined}
                    spellCheck={false}
                    rightIcon={isSecret ? passwordToggle(field.key) : undefined}
                  />
                </Field>
              );
            })}

            {isLastGuided && (
              <div className="space-y-5 border-t border-border pt-5">
                <Field label={t('provider_modal.field.name')} htmlFor="vx-guided-name" hint={t('provider_modal.field.name_hint')}>
                  <Input
                    id="vx-guided-name"
                    value={formData.name}
                    onChange={(e) => setValue('name', e.target.value)}
                    placeholder={t('provider_modal.field.name_placeholder', { label: selectedLabel })}
                  />
                </Field>
                {validationBlock}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* 4 ─ expert */}
      {showExpert && (
        <Card>
          <CardContent className="space-y-5 p-5 sm:p-6">
            <Field label={t('provider_modal.field.name')} htmlFor="vx-expert-name" hint={t('provider_modal.field.name_hint')}>
              <Input
                id="vx-expert-name"
                value={formData.name}
                onChange={(e) => setValue('name', e.target.value)}
                placeholder={t('provider_modal.field.name_placeholder', { label: selectedLabel })}
              />
            </Field>

            {formData.type !== 'cloudflare' && formData.type !== 'cloudflare_tunnel' && (
              <Field label={t('provider_modal.field.url')} htmlFor="vx-expert-url">
                <Input
                  id="vx-expert-url"
                  type="url"
                  value={formData.url}
                  onChange={(e) => setValue('url', e.target.value)}
                  placeholder={selectedMeta.placeholder_url || 'http://'}
                  className="font-mono"
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
            )}

            {formData.type === 'cloudflare_tunnel' && (
              <Field
                label={t('provider_modal.field.tunnel_id')}
                htmlFor="vx-expert-tunnel"
                hint={t('provider_modal.field.tunnel_id_hint')}
              >
                <Input
                  id="vx-expert-tunnel"
                  value={formData.tunnel_id}
                  onChange={(e) => setValue('tunnel_id', e.target.value)}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  className="font-mono"
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
            )}

            <Field label={getUserLabel(formData.type, selectedMeta, t)} htmlFor="vx-expert-user">
              <Input
                id="vx-expert-user"
                value={formData.username}
                onChange={(e) => setValue('username', e.target.value)}
                autoComplete="off"
              />
            </Field>

            <Field label={getPassLabel(formData.type, selectedMeta, t)} htmlFor="vx-expert-pass">
              <Input
                id="vx-expert-pass"
                type={revealed.password ? 'text' : 'password'}
                value={formData.password}
                onChange={(e) => setValue('password', e.target.value)}
                autoComplete="off"
                rightIcon={passwordToggle('password')}
              />
            </Field>

            {!canSubmitProvider && !validationResult && (
              <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <AlertTriangle aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
                {t('setup.provider_form.missing_fields')}
              </p>
            )}

            {validationBlock}
          </CardContent>
        </Card>
      )}
    </SetupStepShell>
  );
}
