/** First screen: the fork between a fresh install and restoring an existing backup. */

import type { ReactNode } from 'react';
import { ArrowRight, Clock, Upload, Zap } from 'lucide-react';
import { BrandMark } from '@/components/layout/BrandMark';
import { cn } from '@/components/ui';
import { useT } from '@/i18n';

function ChoiceCard({
  icon,
  title,
  body,
  onClick,
  recommended = false,
  recommendedLabel,
}: {
  icon: ReactNode;
  title: string;
  body: string;
  onClick: () => void;
  recommended?: boolean;
  recommendedLabel: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group flex h-full w-full flex-col gap-3 rounded-2xl border bg-card p-5 text-left shadow-card',
        'transition-[transform,box-shadow,border-color] duration-200 ease-out-expo',
        'hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-elevated',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        recommended ? 'border-primary/30' : 'border-border',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'grid h-11 w-11 place-items-center rounded-xl border [&>svg]:h-5 [&>svg]:w-5',
          recommended ? 'border-primary/25 bg-primary/10 text-primary' : 'border-border bg-muted text-muted-foreground',
        )}
      >
        {icon}
      </span>
      <span className="block">
        <span className="block text-sm font-semibold text-foreground">{title}</span>
        <span className="mt-1 block text-xs text-muted-foreground">{body}</span>
      </span>
      <span
        className={cn(
          'mt-auto inline-flex items-center gap-1 text-xs font-semibold',
          recommended ? 'text-primary' : 'text-muted-foreground',
        )}
      >
        {recommended ? recommendedLabel : null}
        <ArrowRight aria-hidden="true" className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
      </span>
    </button>
  );
}

export function WelcomeStep({ onFreshInstall, onRestore }: { onFreshInstall: () => void; onRestore: () => void }) {
  const t = useT();

  return (
    <div className="animate-in fade-in-up space-y-8 text-center">
      <div className="space-y-4">
        <BrandMark size="lg" className="justify-center" />
        <div className="space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-primary">{t('setup.welcome.eyebrow')}</p>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground">{t('setup.welcome.title')}</h1>
          <p className="mx-auto max-w-md text-sm text-muted-foreground">{t('setup.welcome.subtitle')}</p>
        </div>
        <p className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground">
          <Clock aria-hidden="true" className="h-3.5 w-3.5" />
          {t('setup.welcome.time_hint')}
        </p>
      </div>

      <div className="mx-auto grid max-w-lg grid-cols-1 gap-4 sm:grid-cols-2">
        <ChoiceCard
          icon={<Zap />}
          title={t('setup.welcome.fresh_title')}
          body={t('setup.welcome.fresh_body')}
          onClick={onFreshInstall}
          recommended
          recommendedLabel={t('setup.welcome.recommended')}
        />
        <ChoiceCard
          icon={<Upload />}
          title={t('setup.welcome.restore_title')}
          body={t('setup.welcome.restore_body')}
          onClick={onRestore}
          recommendedLabel={t('setup.welcome.recommended')}
        />
      </div>

      <p className="mx-auto max-w-md text-xs text-muted-foreground">{t('setup.welcome.footer')}</p>
    </div>
  );
}
