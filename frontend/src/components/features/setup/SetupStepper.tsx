/**
 * Where am I, and how much is left — the two questions a first-run wizard has to answer
 * before anything else. The rail answers them on a wide screen, the compact bar on a phone.
 */

import { Bell, Check, Container, Download, GitMerge, Lock, PartyPopper, Rocket, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';
import { ProgressBar } from '@/components/ui';
import { useT } from '@/i18n';
import { RAIL_OF, RAIL_STEPS, railIndex, setupProgress, type RailStep } from './steps';
import type { StepName } from './types';

const ICONS: Record<RailStep, LucideIcon> = {
  welcome: Rocket,
  password: Lock,
  providers: GitMerge,
  notifications: Bell,
  docker: Container,
  import: Download,
  done: PartyPopper,
};

type StepState = 'done' | 'current' | 'upcoming';

/** The vertical rail: one row per step, a connector between them, state shown by icon + text. */
export function SetupStepper({ current, className }: { current: StepName; className?: string }) {
  const t = useT();
  const activeIndex = railIndex(current);

  return (
    <nav aria-label={t('setup.rail.title')} className={className}>
      <ol className="relative">
        {RAIL_STEPS.map((step, index) => {
          const state: StepState = index < activeIndex ? 'done' : index === activeIndex ? 'current' : 'upcoming';
          const Icon = ICONS[step];
          const last = index === RAIL_STEPS.length - 1;

          return (
            <li key={step} className="relative flex gap-3 pb-1">
              {/* Connector — drawn behind the marker, coloured up to the current step. */}
              {!last && (
                <span
                  aria-hidden="true"
                  className={cn(
                    'absolute left-[15px] top-8 h-[calc(100%-1rem)] w-px',
                    index < activeIndex ? 'bg-primary/40' : 'bg-border',
                  )}
                />
              )}

              <span
                aria-hidden="true"
                className={cn(
                  'relative z-10 mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full border transition-colors',
                  state === 'done' && 'border-primary/40 bg-primary/10 text-primary',
                  state === 'current' && 'border-primary bg-primary text-primary-foreground shadow-glow',
                  state === 'upcoming' && 'border-border bg-card text-muted-foreground',
                )}
              >
                {state === 'done' ? <Check className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
              </span>

              <span className="flex min-w-0 flex-col pb-4 pt-1.5" aria-current={state === 'current' ? 'step' : undefined}>
                <span
                  className={cn(
                    'truncate text-sm transition-colors',
                    state === 'current' ? 'font-semibold text-foreground' : 'text-muted-foreground',
                    state === 'done' && 'text-foreground/80',
                  )}
                >
                  {t(`setup.rail.${step}`)}
                </span>
                <span className="sr-only">{t(`setup.rail.state.${state}`)}</span>
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** The phone version: "Step 3 of 7 · Integrations" over a progress bar. */
export function SetupProgress({ current, className }: { current: StepName; className?: string }) {
  const t = useT();
  const index = railIndex(current);
  const percent = setupProgress(current);

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-xs font-medium text-muted-foreground">
          {t('setup.progress.step', { step: index + 1, total: RAIL_STEPS.length })}
          <span className="mx-1.5 text-border">·</span>
          <span className="font-semibold text-foreground">{t(`setup.rail.${RAIL_OF[current]}`)}</span>
        </p>
        <p className="nums text-xs text-muted-foreground">{t('setup.progress.percent', { percent })}</p>
      </div>
      <ProgressBar value={percent} label={t('setup.progress.label')} size="sm" />
    </div>
  );
}
