/**
 * How the panel is protected: a password (`POST /api/auth/setup-password`, rate limited
 * 3/minute) or open access.
 *
 * The meter grades length only, because that is the whole of the backend rule
 * (`validate_password_strength`: twelve characters, no character classes). Grading symbols
 * would hand out a full bar for `Password1!` while the server counts the same twelve.
 */

import { useState } from 'react';
import { AlertTriangle, ChevronRight, Eye, EyeOff, Globe, Lock } from 'lucide-react';
import { Button, Field, InlineAlert, Input, cn } from '@/components/ui';
import { MIN_PASSWORD_LENGTH } from '@/constants';
import { useT } from '@/i18n';
import { SetupStepShell } from './SetupStepShell';

interface PasswordStepProps {
  onBack: () => void;
  onContinue: () => void;
  onSetPassword: (password: string) => Promise<void>;
  skipPassword: boolean | null;
  setSkipPassword: (v: boolean | null) => void;
}

/** Four levels, all derived from length — the only thing the server checks. */
const STRENGTH_TONES = ['bg-destructive', 'bg-warning', 'bg-primary', 'bg-success'] as const;

function ModeCard({
  selected,
  onClick,
  icon,
  title,
  body,
  footer,
  tone,
}: {
  selected: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  title: string;
  body: string;
  footer: React.ReactNode;
  tone: 'primary' | 'warning';
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        'flex h-full flex-col items-start gap-3 rounded-xl border-2 p-5 text-left transition-all',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected
          ? tone === 'primary'
            ? 'border-primary bg-primary/5'
            : 'border-warning/50 bg-warning/10'
          : 'border-transparent bg-muted/50 hover:border-border hover:bg-muted',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'grid h-10 w-10 place-items-center rounded-lg border [&>svg]:h-5 [&>svg]:w-5',
          selected
            ? tone === 'primary'
              ? 'border-primary/20 bg-primary/10 text-primary'
              : 'border-warning/30 bg-warning/10 text-warning'
            : 'border-border bg-muted text-muted-foreground',
        )}
      >
        {icon}
      </span>
      <span className="block">
        <span className="block text-sm font-semibold text-foreground">{title}</span>
        <span className="mt-1 block text-xs text-muted-foreground">{body}</span>
      </span>
      <span className="mt-auto pt-1">{footer}</span>
    </button>
  );
}

export function PasswordStep({ onBack, onContinue, onSetPassword, skipPassword, setSkipPassword }: PasswordStepProps) {
  const t = useT();
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [settingPassword, setSettingPassword] = useState(false);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirmPassword.length > 0 && password !== confirmPassword;
  const canSubmit = password.length >= MIN_PASSWORD_LENGTH && password === confirmPassword;

  const setupPassword = async () => {
    if (!canSubmit || settingPassword) return;
    setSettingPassword(true);
    try {
      await onSetPassword(password);
    } catch {
      /* the toast is raised by the caller; stay on the step */
    } finally {
      setSettingPassword(false);
    }
  };

  const strength = password.length >= 20 ? 4 : password.length >= 16 ? 3 : password.length >= MIN_PASSWORD_LENGTH ? 2 : 1;

  const strengthLabel = tooShort
    ? t('setup.password.too_short', { min: MIN_PASSWORD_LENGTH })
    : strength === 4
      ? t('setup.password.strength_strong')
      : strength === 3
        ? t('setup.password.strength_good')
        : t('setup.password.strength_ok');

  const primary =
    skipPassword === true
      ? {
          label: t('setup.password.continue_open'),
          onClick: onContinue,
          variant: 'secondary' as const,
          icon: <Globe />,
        }
      : skipPassword === false
        ? {
            label: settingPassword ? t('setup.password.submitting') : t('setup.password.submit'),
            onClick: () => void setupPassword(),
            disabled: !canSubmit,
            loading: settingPassword,
            icon: <Lock />,
          }
        : undefined;

  return (
    <SetupStepShell
      icon={<Lock />}
      title={t('setup.password.title')}
      description={t('setup.password.subtitle')}
      onBack={onBack}
      backDisabled={settingPassword}
      primary={primary}
      bare
    >
      <div className="space-y-5">
        {(skipPassword === null || !password) && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <ModeCard
              selected={skipPassword === false}
              onClick={() => setSkipPassword(false)}
              icon={<Lock />}
              title={t('setup.password.protect_title')}
              body={t('setup.password.protect_body')}
              tone="primary"
              footer={
                skipPassword === false ? (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-primary">
                    {t('setup.password.selected')}
                    <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
                  </span>
                ) : (
                  <span className="text-xs font-medium text-muted-foreground">{t('setup.password.recommended')}</span>
                )
              }
            />
            <ModeCard
              selected={skipPassword === true}
              onClick={() => setSkipPassword(true)}
              icon={<Globe />}
              title={t('setup.password.open_title')}
              body={t('setup.password.open_body')}
              tone="warning"
              footer={
                skipPassword === true ? (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-warning">
                    <AlertTriangle aria-hidden="true" className="h-3 w-3" />
                    {t('setup.password.not_recommended')}
                  </span>
                ) : (
                  <span className="text-xs font-medium text-muted-foreground">{t('setup.password.open_hint')}</span>
                )
              }
            />
          </div>
        )}

        {skipPassword === false && (
          <div className="animate-in fade-in space-y-5 rounded-2xl border border-border bg-card p-5 shadow-card sm:p-6">
            <Field
              label={t('setup.password.password_label')}
              htmlFor="vx-setup-password"
              error={tooShort ? t('setup.password.too_short', { min: MIN_PASSWORD_LENGTH }) : undefined}
            >
              <Input
                id="vx-setup-password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t('setup.password.password_placeholder')}
                autoComplete="new-password"
                size="lg"
                autoFocus
                rightIcon={
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? t('provider_modal.field.hide_password') : t('provider_modal.field.show_password')}
                    aria-pressed={showPassword}
                    tabIndex={-1}
                    className="rounded-md p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                }
              />
            </Field>

            {password.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex gap-1" aria-hidden="true">
                  {[1, 2, 3, 4].map((level) => (
                    <span
                      key={level}
                      className={cn(
                        'h-1 flex-1 rounded-full transition-colors',
                        level <= strength ? STRENGTH_TONES[strength - 1] : 'bg-muted',
                      )}
                    />
                  ))}
                </div>
                <p className="text-xs text-muted-foreground" role="status">
                  {strengthLabel}
                </p>
              </div>
            )}

            <Field
              label={t('setup.password.confirm_label')}
              htmlFor="vx-setup-password-confirm"
              error={mismatch ? t('setup.password.mismatch') : undefined}
            >
              <Input
                id="vx-setup-password-confirm"
                type={showPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder={t('setup.password.confirm_placeholder')}
                autoComplete="new-password"
                size="lg"
              />
            </Field>

            <Button variant="link" size="sm" onClick={() => setSkipPassword(null)} className="text-muted-foreground">
              {t('setup.password.change_choice')}
            </Button>
          </div>
        )}

        {skipPassword === true && (
          <div className="animate-in fade-in space-y-4">
            <InlineAlert tone="warning" title={t('setup.password.open_warning_title')}>
              {t('setup.password.open_warning_body')}
            </InlineAlert>
            <Button variant="link" size="sm" onClick={() => setSkipPassword(null)} className="text-muted-foreground">
              {t('setup.password.change_choice')}
            </Button>
          </div>
        )}
      </div>
    </SetupStepShell>
  );
}
