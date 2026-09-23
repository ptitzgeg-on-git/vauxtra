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

import { useMemo, useRef, useState } from 'react';
import { BookOpen, ChevronRight, Eye, EyeOff, GitMerge, Plus, Server, Shield, X, Zap } from 'lucide-react';
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
  describeMissing,
  fallbackIconByType,
  getDescription,
  getGuidedSteps,
  getPassLabel,
  getProviderGroup,
  getUserLabel,
  isUrlOptional,
  missingFields,
  requiredFields,
  seedFormForType,
  type ProviderFormState,
  type ProviderGroup,
  type ProviderTypeMeta,
  type ProviderValidationResult,
} from '@/components/features/providers/providerConstants';
import { MissingFields } from '@/components/features/providers/MissingFields';
import { checkFailed, healthStatusLabel } from '@/components/features/providers/providerHealth';
import { ValidationCheckLine } from '@/components/features/providers/ValidationCheckLine';
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
  //: The index comes back from the session, and can outlive the list of steps it counted.
  const stepIndex = Math.min(guidedStepIndex, Math.max(guidedSteps.length - 1, 0));
  const currentGuidedStep = guidedSteps[stepIndex];

  // The rule the Integrations dialog reads (`requiredFields`). This step used to gate "Next" on
  // the guided marks and "Validate" on `canSubmitProvider`, the shorter list the dialog read too,
  // and its expert form marked nothing at all: the NPM e-mail was required on one screen and
  // could be skipped on the other.
  const required = requiredFields(formData.type, selectedMeta);
  const isRequired = (key: keyof ProviderFormState) => required.includes(key);
  const missing = missingFields(formData, selectedMeta);
  const stepMissing = (currentGuidedStep?.fields ?? []).filter((field) => missing.includes(field.key)).map((field) => field.key);
  const canSubmitProvider = Boolean(formData.type) && missing.length === 0;

  const isLastGuided = stepIndex === guidedSteps.length - 1;
  const showExpert = wizardMode === 'expert' || (wizardMode === null && formData.type !== '' && guidedSteps.length === 0);
  const showGuided = wizardMode === 'guided' && Boolean(currentGuidedStep);
  const showModeChoice = Boolean(formData.type) && wizardMode === null && guidedSteps.length > 0;

  // `clearType` empties `type` on the way back to the picker, so the type picked last is kept
  // here: choosing it again is not a change of type.
  const lastTypeRef = useRef(formData.type);

  const chooseProviderType = (type: string, label: string) => {
    // The Integrations dialog's rule: a new type gets its own name and an empty address, the
    // same type picked again keeps both. This step used to keep the name and the address typed
    // for the type before, so an AdGuard Home could be validated against the address typed for
    // NPM, under an integration still called "Nginx Proxy Manager".
    //: Read here, not in the updater: React may run it after the next line, and every pick
    //: would then look like the same type picked again.
    const previous = lastTypeRef.current;
    setFormData((prev) => seedFormForType({ ...prev, type: prev.type || previous }, type, label));
    lastTypeRef.current = type;
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
    // A verdict is about the values it was given. Kept past an edit, it left "Connect" armed for
    // a password nothing had tested. The Integrations dialog drops it on an edit too.
    setValidationResult(null);
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
      ? t('provider_modal.guided.step', { step: stepIndex + 1, total: guidedSteps.length })
      : showModeChoice
        ? t('provider_modal.mode.label')
        : t('setup.provider_form.expert_subtitle');

  // ─── back ──────────────────────────────────────────────────────
  const back = !formData.type
    ? onCancel
    : showGuided && stepIndex > 0
      ? () => {
          setGuidedStepIndex(stepIndex - 1);
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
          onClick: () => setGuidedStepIndex(stepIndex + 1),
          disabled: stepMissing.length > 0,
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
            disabled: !canSubmitProvider || busy,
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
          {/* On a failure, only what failed: a check that was merely not run is not one. */}
          {validationResult.validation.checks
            .filter((check) => (validationResult.ok ? true : checkFailed(check)))
            .map((check, i) => (
              <ValidationCheckLine key={`${check.name || 'check'}-${i}`} check={check} />
            ))}
        </ul>
      )}
      {validationResult.health?.status && (
        <p className="mt-1">{t('provider_modal.validation.health', { status: healthStatusLabel(validationResult.health.status, t) })}</p>
      )}
    </InlineAlert>
  ) : null;

  const optionalAddon = (key: keyof ProviderFormState) =>
    isRequired(key) ? undefined : (
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{t('provider_modal.guided.optional')}</span>
    );

  // First in both modes, as in the Integrations dialog. The guided mode asked for it under the
  // last step's fields and nowhere else, and neither mode said the API refuses a blank one.
  const nameField = (id: string) => (
    <Field label={t('provider_modal.field.name')} htmlFor={id} hint={t('provider_modal.field.name_hint')} required>
      <Input
        id={id}
        value={formData.name}
        onChange={(e) => setValue('name', e.target.value)}
        placeholder={t('provider_modal.field.name_placeholder', { label: selectedLabel })}
        autoComplete="off"
      />
    </Field>
  );

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
            {nameField('vx-guided-name')}

            <div className="space-y-5 border-t border-border pt-5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="primary" size="sm" className="nums">
                  {t('provider_modal.guided.step', { step: stepIndex + 1, total: guidedSteps.length })}
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
                    required={isRequired(field.key)}
                    labelAddon={optionalAddon(field.key)}
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

              {/* A field asked on an earlier step is a link back to it: the password is not kept
                  across a reload, so the last step can be reached with it gone. */}
              <MissingFields
                fields={describeMissing(isLastGuided ? missing : stepMissing, formData.type, selectedMeta, t, guidedSteps, stepIndex)}
                onGoToStep={(step) => {
                  setGuidedStepIndex(step);
                  setValidationResult(null);
                }}
                complete={isLastGuided && !validationResult ? t('provider_modal.guided.collected') : undefined}
              />

              {isLastGuided && validationBlock}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 4 ─ expert */}
      {showExpert && (
        <Card>
          <CardContent className="space-y-5 p-5 sm:p-6">
            {nameField('vx-expert-name')}

            {/* The fields in the Integrations dialog's order, and the address of a hosted API
                shown too: it was hidden for Cloudflare here, and optional there. */}
            <Field
              label={t('provider_modal.field.url')}
              htmlFor="vx-expert-url"
              hint={isUrlOptional(formData.type) ? t('provider_modal.field.url_hint_hosted', { label: selectedLabel }) : undefined}
              required={isRequired('url')}
              labelAddon={optionalAddon('url')}
            >
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

            <Field
              label={getUserLabel(formData.type, selectedMeta, t)}
              htmlFor="vx-expert-user"
              required={isRequired('username')}
              labelAddon={optionalAddon('username')}
            >
              <Input
                id="vx-expert-user"
                value={formData.username}
                onChange={(e) => setValue('username', e.target.value)}
                placeholder={selectedMeta.user_placeholder || undefined}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>

            <Field
              label={getPassLabel(formData.type, selectedMeta, t)}
              htmlFor="vx-expert-pass"
              required={isRequired('password')}
              labelAddon={optionalAddon('password')}
            >
              <Input
                id="vx-expert-pass"
                type={revealed.password ? 'text' : 'password'}
                value={formData.password}
                onChange={(e) => setValue('password', e.target.value)}
                autoComplete="off"
                rightIcon={passwordToggle('password')}
              />
            </Field>

            {formData.type === 'cloudflare_tunnel' && (
              <Field
                label={t('provider_modal.field.tunnel_id')}
                htmlFor="vx-expert-tunnel"
                hint={t('provider_modal.field.tunnel_id_hint')}
                required={isRequired('tunnel_id')}
                labelAddon={optionalAddon('tunnel_id')}
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

            <MissingFields fields={describeMissing(missing, formData.type, selectedMeta, t)} />

            {validationBlock}
          </CardContent>
        </Card>
      )}
    </SetupStepShell>
  );
}
