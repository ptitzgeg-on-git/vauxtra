import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Key, Lock, ShieldAlert, ShieldCheck } from 'lucide-react';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import { MIN_PASSWORD_DISTINCT_CHARS, MIN_PASSWORD_LENGTH, isPasswordStrongEnough } from '@/constants';
import { Button, Checkbox, Field, InlineAlert, Input, SkeletonCard, buttonVariants } from '@/components/ui';
import type { AuthStatus } from '@/types/api';
import { SettingsSection } from './SettingsSection';

/** The admin password: set one when the instance runs open, change it otherwise. */
export function SecurityTab() {
  const t = useT();
  const authQuery = useQuery<AuthStatus>({
    queryKey: ['auth-me'],
    queryFn: () => api.get<AuthStatus>('/auth/me'),
  });

  /**
   * Which card is shown is decided by the data, and only by the data.
   *
   * The error branch used to come first. `/auth/me` is refetched in the background -- on
   * window focus, like every query here -- and a refetch that failed swapped whatever card
   * was on screen for an alert. React Query keeps the last good data through that, so
   * nothing was actually unknown; but the card was gone, and with it the password somebody
   * was halfway through typing. The retry then mounted a fresh, empty one. A blip behind a
   * reverse proxy was enough, and the operator did nothing to cause it.
   *
   * So a failure that still has data behind it is reported *above* the form rather than in
   * place of it. The alert is a sibling of the card, not a branch around it: rendering
   * nothing in its slot leaves the card at the same position, which is what keeps React
   * from treating it as a new one.
   */
  const status = authQuery.data;

  return (
    <div className="space-y-6">
      {authQuery.isLoading ? (
        <SkeletonCard />
      ) : authQuery.isError && !status ? (
        <InlineAlert
          tone="danger"
          title={t('settings.security.load_failed')}
          action={
            <Button variant="outline" size="sm" loading={authQuery.isFetching} onClick={() => authQuery.refetch()}>
              {t('ui.error.retry')}
            </Button>
          }
        >
          {translateApiError(authQuery.error, t, t('common.error'))}
        </InlineAlert>
      ) : (
        <div className="space-y-4">
          {authQuery.isError && (
            <InlineAlert
              tone="warning"
              title={t('settings.security.refresh_failed')}
              action={
                <Button variant="outline" size="sm" loading={authQuery.isFetching} onClick={() => authQuery.refetch()}>
                  {t('ui.error.retry')}
                </Button>
              }
            >
              {translateApiError(authQuery.error, t, t('common.error'))}
            </InlineAlert>
          )}
          {status?.password_source === 'environment' ? (
            <EnvManagedPasswordCard />
          ) : status && !status.auth_required ? (
            <SetPasswordCard />
          ) : (
            <ChangePasswordCard />
          )}
        </div>
      )}

      <SettingsSection
        icon={<Key />}
        title={t('settings.security.api_keys_title')}
        description={t('settings.security.api_keys_desc')}
        actions={
          <Link to="/settings?tab=apikeys" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            {t('settings.security.api_keys_cta')}
          </Link>
        }
      />
    </div>
  );
}

function SetPasswordCard() {
  const t = useT();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [show, setShow] = useState(false);
  const [touched, setTouched] = useState(false);

  const setPasswordMutation = useMutation({
    mutationFn: (value: string) => api.post('/auth/setup-password', { password: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['auth-status'] });
      queryClient.invalidateQueries({ queryKey: ['auth-me'] });
      setPassword('');
      setConfirmPassword('');
      setTouched(false);
      toast.success(t('settings.auth.changed_success'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.auth.change_failed'))),
  });

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const tooPlain = password.length >= MIN_PASSWORD_LENGTH && new Set(password).size < MIN_PASSWORD_DISTINCT_CHARS;
  const mismatch = confirmPassword.length > 0 && confirmPassword !== password;
  const canSubmit = isPasswordStrongEnough(password) && confirmPassword === password;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!canSubmit) return;
    setPasswordMutation.mutate(password);
  };

  return (
    <form onSubmit={submit}>
      <SettingsSection
        tone="warning"
        icon={<ShieldAlert />}
        title={t('settings.auth.set_password')}
        description={t('settings.auth.set_password_desc')}
        footer={
          <Button type="submit" loading={setPasswordMutation.isPending} disabled={!canSubmit}>
            {t('settings.auth.set_password')}
          </Button>
        }
      >
        <InlineAlert tone="warning" title={t('settings.security.open_access_title')}>
          {t('settings.security.open_access_desc')}
        </InlineAlert>
        <div className="grid gap-4 md:grid-cols-2">
          <Field
            label={t('settings.auth.new_password', { min: MIN_PASSWORD_LENGTH })}
            required
            error={
              tooPlain
                ? t('settings.auth.new_distinct', { count: MIN_PASSWORD_DISTINCT_CHARS })
                : tooShort || (touched && password.length === 0)
                  ? t('settings.auth.new_min', { min: MIN_PASSWORD_LENGTH })
                  : undefined
            }
          >
            <Input
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              value={password}
              minLength={MIN_PASSWORD_LENGTH}
              leftIcon={<Lock />}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Field
            label={t('settings.auth.confirm_password')}
            required
            error={mismatch ? t('settings.auth.confirm_mismatch') : undefined}
          >
            <Input
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              value={confirmPassword}
              leftIcon={<Lock />}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </Field>
        </div>
        <Checkbox label={t('settings.auth.show_passwords')} checked={show} onChange={(e) => setShow(e.target.checked)} />
      </SettingsSection>
    </form>
  );
}

/** `APP_PASSWORD` decides logins, so this screen explains rather than offering a form.
 *
 * The form used to be shown here too. Submitting it wrote a hash `check_password` never
 * reads and bumped the session epoch, so the operator was logged out of everything and the
 * password they had just chosen was refused. The API returns 409 now; this says why before
 * anyone gets that far.
 */
function EnvManagedPasswordCard() {
  const t = useT();
  return (
    <SettingsSection
      icon={<Lock />}
      title={t('settings.security.env_password_title')}
      description={t('settings.security.env_password_desc')}
    >
      <InlineAlert tone="info" title={t('settings.security.env_password_how_title')}>
        {t('settings.security.env_password_how')}
      </InlineAlert>
    </SettingsSection>
  );
}

function ChangePasswordCard() {
  const t = useT();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [show, setShow] = useState(false);
  const [touched, setTouched] = useState(false);

  const changePassword = useMutation({
    mutationFn: (payload: { current_password: string; new_password: string }) =>
      api.post('/auth/change-password', payload),
    onSuccess: () => {
      setCurrent('');
      setNext('');
      setConfirmPassword('');
      setTouched(false);
      toast.success(t('settings.auth.changed_success'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.auth.change_failed'))),
  });

  const currentMissing = touched && current.length === 0;
  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_LENGTH;
  const tooPlain = next.length >= MIN_PASSWORD_LENGTH && new Set(next).size < MIN_PASSWORD_DISTINCT_CHARS;
  const mismatch = confirmPassword.length > 0 && confirmPassword !== next;
  const canSubmit = current.length > 0 && isPasswordStrongEnough(next) && confirmPassword === next;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!canSubmit) return;
    changePassword.mutate({ current_password: current, new_password: next });
  };

  return (
    <form onSubmit={submit}>
      <SettingsSection
        icon={<ShieldCheck />}
        title={t('settings.auth.change_password')}
        description={t('settings.auth.change_password_desc')}
        footer={
          <Button type="submit" loading={changePassword.isPending} disabled={!canSubmit}>
            {t('settings.auth.change_password')}
          </Button>
        }
      >
        <Field
          label={t('settings.auth.current_password')}
          required
          error={currentMissing ? t('settings.auth.current_required') : undefined}
        >
          <Input
            type={show ? 'text' : 'password'}
            autoComplete="current-password"
            value={current}
            leftIcon={<Lock />}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field
            label={t('settings.auth.new_password', { min: MIN_PASSWORD_LENGTH })}
            required
            error={
              tooPlain
                ? t('settings.auth.new_distinct', { count: MIN_PASSWORD_DISTINCT_CHARS })
                : tooShort
                  ? t('settings.auth.new_min', { min: MIN_PASSWORD_LENGTH })
                  : undefined
            }
          >
            <Input
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              value={next}
              minLength={MIN_PASSWORD_LENGTH}
              leftIcon={<Lock />}
              onChange={(e) => setNext(e.target.value)}
            />
          </Field>
          <Field
            label={t('settings.auth.confirm_password')}
            required
            error={mismatch ? t('settings.auth.confirm_mismatch') : undefined}
          >
            <Input
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              value={confirmPassword}
              leftIcon={<Lock />}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </Field>
        </div>
        <Checkbox label={t('settings.auth.show_passwords')} checked={show} onChange={(e) => setShow(e.target.checked)} />
      </SettingsSection>
    </form>
  );
}
