/**
 * Curtain call. The celebration is CSS and one inline SVG — no confetti library, nothing to
 * download: a ring that pulses out, a check that draws itself once the component mounts, and a
 * dozen absolutely positioned chips that fall. `index.css` already neutralises every animation
 * under `prefers-reduced-motion`, so none of this needs a `motion-reduce:` variant.
 *
 * Two of the four summary rows are counted from a read this screen fires itself, and both used
 * to print `formatNumber(rows.length)` whatever the read did. A request that never answered
 * left a zero beside "Notification targets" on the one screen whose whole job is to tell the
 * operator what they just configured, three steps after they configured it. `StatRow` settled
 * the rule for this repo -- zero and "we could not ask" look identical as a number, and only
 * one of them means everything is fine -- and six tiles on the dashboard already follow it.
 */

import { useEffect, useState } from 'react';
import { ArrowRight, Bell, BookOpen, Container, GitMerge, Lock, Settings, Unlock } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Badge, Button, Card, InlineAlert, cn, toneClasses, type Tone } from '@/components/ui';
import { useDockerEndpoints } from '@/hooks/useDockerEndpoints';
import { useWebhookActions } from '@/hooks/useWebhookActions';
import { useFormat } from '@/hooks/useFormat';
import { EM_DASH } from '@/lib/format';
import { useT } from '@/i18n';
import type { ProviderItem } from './types';

interface DoneStepProps {
  skipPassword: boolean | null;
  providers: ProviderItem[];
  onFinish: () => void;
  /** The wizard is still waiting on the server; the button says so instead of looking idle. */
  finishing?: boolean;
}

const DOCS_URL = 'https://github.com/ptitzgeg-on-git/vauxtra/blob/main/docs/HOWTO.md';

/** Twelve chips, spread across the header, each with its own delay and drift. */
const CONFETTI = [
  { left: '8%', delay: 0, duration: 2600, tone: 'primary' },
  { left: '17%', delay: 420, duration: 3000, tone: 'success' },
  { left: '26%', delay: 900, duration: 2400, tone: 'info' },
  { left: '34%', delay: 180, duration: 3200, tone: 'warning' },
  { left: '43%', delay: 1200, duration: 2800, tone: 'primary' },
  { left: '52%', delay: 640, duration: 2500, tone: 'success' },
  { left: '61%', delay: 1500, duration: 3100, tone: 'info' },
  { left: '69%', delay: 300, duration: 2700, tone: 'primary' },
  { left: '76%', delay: 1050, duration: 2900, tone: 'warning' },
  { left: '84%', delay: 760, duration: 2300, tone: 'success' },
  { left: '91%', delay: 1650, duration: 3000, tone: 'info' },
  { left: '96%', delay: 220, duration: 2600, tone: 'primary' },
] as const;

function Celebration({ drawn }: { drawn: boolean }) {
  return (
    <div aria-hidden="true" className="pointer-events-none relative mx-auto h-32 w-full max-w-sm select-none">
      {CONFETTI.map((chip, i) => {
        const tone = toneClasses(chip.tone as Tone);
        return (
          <span
            key={i}
            className={cn('absolute top-0 h-2 w-1.5 rounded-[2px] animate-bounce', tone.solid)}
            style={{
              left: chip.left,
              animationDelay: `${chip.delay}ms`,
              animationDuration: `${chip.duration}ms`,
              transform: `rotate(${(i * 37) % 90}deg)`,
              opacity: 0.75,
            }}
          />
        );
      })}

      <span className="absolute left-1/2 top-1/2 h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/10 animate-ping [animation-duration:2.4s]" />
      <span className="absolute left-1/2 top-1/2 h-16 w-16 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/15 animate-ping [animation-delay:600ms] [animation-duration:2.4s]" />

      <span className="animate-in zoom-in-95 absolute left-1/2 top-1/2 grid h-20 w-20 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-primary/20 bg-primary/10 shadow-glow">
        <svg viewBox="0 0 48 48" className="h-10 w-10 text-primary" fill="none" strokeWidth={4} strokeLinecap="round" strokeLinejoin="round">
          <path
            d="M12 25.5 L20.5 34 L36 15"
            stroke="currentColor"
            strokeDasharray={48}
            strokeDashoffset={drawn ? 0 : 48}
            style={{ transition: 'stroke-dashoffset 700ms cubic-bezier(0.16, 1, 0.3, 1) 250ms' }}
          />
        </svg>
      </span>
    </div>
  );
}

export function DoneStep({ skipPassword, providers, onFinish, finishing }: DoneStepProps) {
  const t = useT();
  const { formatNumber } = useFormat();
  const { endpoints, endpointsQuery } = useDockerEndpoints();
  const { webhooks, webhooksQuery } = useWebhookActions();
  const [drawn, setDrawn] = useState(false);

  // A read still in flight is unknown too: the celebration mounts the instant the wizard
  // finishes, so both requests are typically still open on the first paint, and a figure that
  // settles a moment later is better than a zero that was never true.
  const webhooksUnread = webhooksQuery.isLoading || webhooksQuery.isError;
  const endpointsUnread = endpointsQuery.isLoading || endpointsQuery.isError;
  const anyUnread = webhooksQuery.isError || endpointsQuery.isError;

  useEffect(() => {
    const id = window.requestAnimationFrame(() => setDrawn(true));
    return () => window.cancelAnimationFrame(id);
  }, []);

  const rows = [
    {
      key: 'password',
      icon: skipPassword ? <Unlock /> : <Lock />,
      tone: (skipPassword ? 'warning' : 'success') as Tone,
      // One label, because both branches said "Panel access" in all eight locales.
      // What changes with `skipPassword` is the value beside it, and the icon and the
      // tone around it.
      label: t('setup.done.summary_access'),
      value: skipPassword ? t('setup.done.value_open') : t('setup.done.value_protected'),
      active: true,
    },
    {
      key: 'providers',
      icon: <GitMerge />,
      tone: 'primary' as Tone,
      label: t('setup.done.summary_providers'),
      value: formatNumber(providers.length),
      active: providers.length > 0,
    },
    {
      key: 'webhooks',
      icon: <Bell />,
      tone: 'info' as Tone,
      label: t('setup.done.summary_webhooks'),
      value: webhooksUnread ? EM_DASH : formatNumber(webhooks.length),
      active: !webhooksUnread && webhooks.length > 0,
    },
    {
      key: 'docker',
      icon: <Container />,
      tone: 'info' as Tone,
      label: t('setup.done.summary_docker'),
      value: endpointsUnread ? EM_DASH : formatNumber(endpoints.length),
      active: !endpointsUnread && endpoints.length > 0,
    },
  ];

  return (
    <div className="animate-in fade-in-up space-y-6">
      <div className="space-y-4 text-center">
        <Celebration drawn={drawn} />
        <div className="space-y-2">
          <Badge tone="success" size="sm">
            {t('setup.done.badge')}
          </Badge>
          <h2 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">{t('setup.done.title')}</h2>
          <p className="mx-auto max-w-md text-sm text-muted-foreground">{t('setup.done.subtitle')}</p>
        </div>
      </div>

      <Card className="mx-auto max-w-lg p-5 sm:p-6">
        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{t('setup.done.summary_title')}</p>
        <dl className="mt-4 space-y-3">
          {rows.map((row) => {
            const tone = toneClasses(row.tone);
            return (
              <div key={row.key} className="flex items-center gap-3">
                <span
                  aria-hidden="true"
                  className={cn(
                    'grid h-8 w-8 shrink-0 place-items-center rounded-lg border [&>svg]:h-4 [&>svg]:w-4',
                    row.active ? cn(tone.bg, tone.text, tone.border) : 'border-border bg-muted text-muted-foreground/50',
                  )}
                >
                  {row.icon}
                </span>
                <dt className="flex-1 text-sm text-foreground">{row.label}</dt>
                <dd className={cn('nums text-sm font-semibold', row.active ? 'text-foreground' : 'text-muted-foreground')}>
                  {row.value}
                </dd>
              </div>
            );
          })}
        </dl>

        {anyUnread && (
          /* Only a failed read draws this, not one still in flight: the dash is enough while
             the request is open, and a notice that appears for a moment on every visit would
             be noise on the one screen meant to feel finished. */
          <InlineAlert
            className="mt-4"
            tone="warning"
            title={t('setup.done.counts_unread')}
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (webhooksQuery.isError) void webhooksQuery.refetch();
                  if (endpointsQuery.isError) void endpointsQuery.refetch();
                }}
              >
                {t('common.retry')}
              </Button>
            }
          >
            {t('setup.done.counts_unread_hint')}
          </InlineAlert>
        )}
      </Card>

      <div className="flex flex-col items-center gap-3">
        <Button size="lg" onClick={onFinish} loading={finishing} rightIcon={<ArrowRight />} className="w-full max-w-xs">
          {t('setup.done.go_dashboard')}
        </Button>
        <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-xs">
          <Link
            to="/settings"
            className="inline-flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            <Settings aria-hidden="true" className="h-3.5 w-3.5" />
            {t('setup.done.link_settings')}
          </Link>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            <BookOpen aria-hidden="true" className="h-3.5 w-3.5" />
            {t('setup.done.link_docs')}
          </a>
        </div>
      </div>
    </div>
  );
}
