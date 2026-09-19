import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Activity, Plug, Plus, ScrollText } from 'lucide-react';
import { useT } from '@/i18n';
import { Card, Kbd, SectionHeading, cn, toneClasses, type Tone } from '@/components/ui';
import { isMacPlatform } from '@/components/ui/_internal';

export interface QuickActionsProps {
  onCreateService: () => void;
  onAddProvider: () => void;
}

type Action = {
  id: string;
  tone: Tone;
  icon: ReactNode;
  title: string;
  hint: string;
  keys: string[];
  /** Keys pressed together (Ctrl+K) rather than in sequence (G then P). */
  chord: boolean;
  onClick: () => void;
};

/** Four one-click actions with the keyboard shortcut that does the same thing. */
export function QuickActions({ onCreateService, onAddProvider }: QuickActionsProps) {
  const t = useT();
  const navigate = useNavigate();
  const modKey = isMacPlatform() ? '⌘' : 'Ctrl';

  const actions: Action[] = [
    {
      id: 'create-service',
      tone: 'primary',
      icon: <Plus />,
      title: t('dashboard.quick_actions.create_endpoint'),
      hint: t('dashboard.quick_actions.create_endpoint_hint'),
      keys: [modKey, 'K'],
      chord: true,
      onClick: onCreateService,
    },
    {
      id: 'add-provider',
      tone: 'info',
      icon: <Plug />,
      title: t('dashboard.quick_actions.add_integration'),
      hint: t('dashboard.quick_actions.add_integration_hint'),
      keys: ['G', 'P'],
      chord: false,
      onClick: onAddProvider,
    },
    {
      id: 'check-health',
      tone: 'success',
      icon: <Activity />,
      title: t('dashboard.quick_actions.check_health'),
      hint: t('dashboard.quick_actions.check_health_hint'),
      keys: ['G', 'M'],
      chord: false,
      onClick: () => navigate('/monitoring'),
    },
    {
      id: 'view-logs',
      tone: 'neutral',
      icon: <ScrollText />,
      title: t('dashboard.quick_actions.view_logs'),
      hint: t('dashboard.quick_actions.view_logs_hint'),
      keys: ['G', 'S'],
      chord: false,
      onClick: () => navigate('/settings?tab=logs'),
    },
  ];

  return (
    <Card className="@container p-5 sm:p-6">
      <SectionHeading title={t('dashboard.quick_actions.title')} description={t('dashboard.quick_actions.description')} />
      {/* Card width, not window width: this card sits in a narrow column and `sm:` split
          the tiles in two there, leaving "Créer un..." for a four-word label. */}
      <ul className="mt-4 grid grid-cols-1 gap-2 @[38rem]:grid-cols-2">
        {actions.map((action) => {
          const c = toneClasses(action.tone);
          return (
            <li key={action.id}>
              <button
                type="button"
                onClick={action.onClick}
                className="group flex w-full items-center gap-3 rounded-xl border border-border p-3 text-left text-sm transition-colors hover:border-primary/30 hover:bg-accent focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span
                  aria-hidden="true"
                  className={cn('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl [&>svg]:h-4 [&>svg]:w-4', c.bg, c.text)}
                >
                  {action.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-foreground">{action.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{action.hint}</span>
                </span>
                <span className="hidden shrink-0 items-center gap-1 @[22rem]:inline-flex" aria-hidden="true">
                  {action.keys.map((key, index) => (
                    <span key={key} className="inline-flex items-center gap-1">
                      {index > 0 && (
                        <span className="text-[10px] text-muted-foreground">{action.chord ? '+' : t('layout.shortcuts.then')}</span>
                      )}
                      <Kbd size="sm">{key}</Kbd>
                    </span>
                  ))}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
        <Kbd size="sm">?</Kbd>
        <span>{t('dashboard.quick_actions.help')}</span>
      </p>
    </Card>
  );
}
