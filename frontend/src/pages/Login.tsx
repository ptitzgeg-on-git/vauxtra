/**
 * The sign-in screen.
 *
 * `POST /api/auth/login` takes exactly one field (`password`) and is rate limited
 * `5/minute;20/hour` by slowapi, so three answers matter where the old screen showed one
 * "Invalid password": 401 (wrong secret), 429 (the limiter, usually with a `Retry-After`),
 * and anything else (backend down, or the 400 raised when no password is configured at all).
 *
 * There is deliberately no "remember me": the route accepts no such field and
 * `SessionMiddleware` fixes the cookie lifetime at seven days, so the checkbox would be a lie.
 * The screen states the seven days instead.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Activity,
  ArrowRight,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  Monitor,
  Moon,
  ShieldCheck,
  Sun,
  Waypoints,
} from 'lucide-react';
import { api } from '@/api/client';
import { BrandMark } from '@/components/layout/BrandMark';
import { Button, Card, CardContent, Field, IconButton, InlineAlert, Input, Select } from '@/components/ui';
import { translateApiError, getRetryAfterSeconds, isHttpStatus, isNetworkError } from '@/lib/errors';
import { SUPPORTED_LANGUAGES, useI18n, type Lang } from '@/i18n';
import { useTheme, type Theme } from '@/theme';

interface LoginPageProps {
  onSuccess: () => void;
}

const DOCS_AUTH_URL = 'https://github.com/ptitzgeg-on-git/vauxtra/blob/main/docs/HOWTO.md#2-authentication';

const THEME_ICONS: Record<Theme, ReactNode> = { light: <Sun />, dark: <Moon />, system: <Monitor /> };

/** Fallback wait when a 429 arrives without a usable `Retry-After`: one limiter window. */
const DEFAULT_COOLDOWN_SECONDS = 60;

type ErrorKind = 'invalid' | 'rate_limited' | 'other';

function HeroPoint({ icon, title, body }: { icon: ReactNode; title: string; body: string }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden="true"
        className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-card text-primary shadow-card [&>svg]:h-4 [&>svg]:w-4"
      >
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-foreground">{title}</span>
        <span className="block text-sm text-muted-foreground">{body}</span>
      </span>
    </li>
  );
}

export function Login({ onSuccess }: LoginPageProps) {
  const { t, lang, setLang } = useI18n();
  const { theme, toggleTheme } = useTheme();

  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<{ kind: ErrorKind; message: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  /** Seconds left before the limiter lets another attempt through; 0 = free to try. */
  const [cooldown, setCooldown] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((left) => (left > 1 ? left - 1 : 0)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  const blocked = loading || cooldown > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (blocked || !password) return;
    setError(null);
    setLoading(true);
    try {
      await api.post('/auth/login', { password });
      onSuccess();
    } catch (err: unknown) {
      if (isHttpStatus(err, 429)) {
        setCooldown(getRetryAfterSeconds(err) ?? DEFAULT_COOLDOWN_SECONDS);
        setError({ kind: 'rate_limited', message: t('login.too_many_attempts') });
      } else if (isHttpStatus(err, 401)) {
        setError({ kind: 'invalid', message: t('login.invalid') });
      } else if (isNetworkError(err)) {
        setError({ kind: 'other', message: t('login.unreachable') });
      } else {
        setError({ kind: 'other', message: translateApiError(err, t, t('login.failed')) });
      }
    } finally {
      setLoading(false);
    }
  };

  const readCapsLock = (e: React.KeyboardEvent<HTMLInputElement>) => {
    setCapsLock(e.getModifierState?.('CapsLock') ?? false);
  };

  const themeLabel = `${t('layout.theme.toggle')} · ${t(`layout.theme.${theme}`)}`;

  const corner = (
    <div className="flex items-center gap-1.5">
      <IconButton
        label={themeLabel}
        icon={THEME_ICONS[theme]}
        onClick={toggleTheme}
        tooltip
        className="h-9 w-9 text-muted-foreground"
      />
      <Select
        size="sm"
        aria-label={t('layout.language')}
        value={lang}
        onChange={(e) => setLang(e.target.value as Lang)}
        className="w-auto bg-card"
      >
        {SUPPORTED_LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.flag} {l.label}
          </option>
        ))}
      </Select>
    </div>
  );

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-aurora" />

      <div className="relative grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
        {/* Brand panel — the product's face while the operator is still outside. */}
        <aside className="hidden flex-col justify-between border-r border-border/70 px-12 py-14 lg:flex">
          <BrandMark size="lg" withWordmark />

          <div className="max-w-md space-y-8">
            <div className="space-y-3">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-primary">{t('login.eyebrow')}</p>
              <h1 className="text-3xl font-extrabold leading-tight tracking-tight text-foreground">
                {t('layout.tagline')}
              </h1>
            </div>
            <ul className="space-y-5">
              <HeroPoint icon={<Waypoints />} title={t('login.hero.routes_title')} body={t('login.hero.routes_body')} />
              <HeroPoint icon={<ShieldCheck />} title={t('login.hero.certs_title')} body={t('login.hero.certs_body')} />
              <HeroPoint icon={<Activity />} title={t('login.hero.health_title')} body={t('login.hero.health_body')} />
            </ul>
          </div>

          <p className="text-xs text-muted-foreground">{t('login.session_hint')}</p>
        </aside>

        {/* Form panel */}
        <main className="flex flex-col px-5 py-6 sm:px-10 sm:py-8">
          <div className="flex items-center justify-between gap-3">
            <BrandMark size="sm" withWordmark className="lg:hidden" />
            <div className="ml-auto">{corner}</div>
          </div>

          <div className="flex flex-1 items-center justify-center py-10">
            <Card elevated className="w-full max-w-md animate-in fade-in-up">
              <CardContent className="space-y-6 p-6 sm:p-7">
                <div className="space-y-1.5">
                  <span
                    aria-hidden="true"
                    className="mb-2 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary"
                  >
                    <KeyRound className="h-5 w-5" />
                  </span>
                  <h2 className="text-xl font-bold tracking-tight text-foreground">{t('login.title')}</h2>
                  <p className="text-sm text-muted-foreground">{t('login.subtitle')}</p>
                </div>

                {error && (
                  <InlineAlert
                    tone={error.kind === 'rate_limited' ? 'warning' : 'danger'}
                    title={error.message}
                    onDismiss={error.kind === 'rate_limited' ? undefined : () => setError(null)}
                  >
                    {error.kind === 'rate_limited' && cooldown > 0 ? t('login.retry_in', { seconds: cooldown }) : null}
                    {error.kind === 'invalid' ? t('login.invalid_hint') : null}
                  </InlineAlert>
                )}

                <form onSubmit={handleSubmit} className="space-y-4" noValidate>
                  <Field
                    label={t('login.password')}
                    htmlFor="vx-login-password"
                    hint={capsLock ? t('login.caps_lock') : undefined}
                  >
                    <Input
                      ref={inputRef}
                      id="vx-login-password"
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      onKeyUp={readCapsLock}
                      onKeyDown={readCapsLock}
                      onBlur={() => setCapsLock(false)}
                      placeholder={t('login.password_placeholder')}
                      autoComplete="current-password"
                      required
                      size="lg"
                      rightIcon={
                        <button
                          type="button"
                          onClick={() => setShowPassword((v) => !v)}
                          aria-label={showPassword ? t('login.hide_password') : t('login.show_password')}
                          aria-pressed={showPassword}
                          tabIndex={-1}
                          className="rounded-md p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </button>
                      }
                    />
                  </Field>

                  <Button
                    type="submit"
                    size="lg"
                    className="w-full"
                    loading={loading}
                    disabled={blocked || !password}
                    rightIcon={loading ? undefined : <ArrowRight />}
                  >
                    {cooldown > 0 ? t('login.retry_in', { seconds: cooldown }) : t('login.submit')}
                  </Button>
                </form>

                <div className="space-y-3 border-t border-border pt-4">
                  <p className="text-xs text-muted-foreground">{t('login.rate_limit_hint')}</p>
                  <p className="text-xs text-muted-foreground">
                    {t('login.forgot')}{' '}
                    <a
                      href={DOCS_AUTH_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {t('login.docs_link')}
                      <ExternalLink aria-hidden="true" className="h-3 w-3" />
                    </a>
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>

          <p className="text-center text-xs text-muted-foreground lg:hidden">{t('login.session_hint')}</p>
        </main>
      </div>
    </div>
  );
}
