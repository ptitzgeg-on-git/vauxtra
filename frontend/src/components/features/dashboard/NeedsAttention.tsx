import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, CloudOff, Sparkles } from 'lucide-react';
import { useT } from '@/i18n';
import { Card, InlineAlert, SectionHeading, SkeletonRow, cn, toneClasses, type Tone } from '@/components/ui';

export interface AttentionItem {
  id: string;
  tone: Tone;
  icon: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  to: string;
  /** Label of the trailing action; defaults to "Open". */
  actionLabel?: ReactNode;
}

export interface NeedsAttentionProps {
  items: AttentionItem[];
  /** True only while a source is still in flight — not once one has come back empty because it failed. */
  loading: boolean;
  /**
   * A source this list is built from came back with nothing because it failed. The list is
   * then not a statement about the panel, only about the part of it that answered — and
   * "all clear" would be the panel vouching for services it could not read.
   */
  incomplete?: boolean;
}

/** The triage list: one row per thing worth a look, or a friendly "all clear" when nothing is. */
export function NeedsAttention({ items, loading, incomplete = false }: NeedsAttentionProps) {
  const t = useT();

  if (loading) {
    return (
      <Card className="p-5 sm:p-6">
        <SectionHeading title={t('dashboard.attention.title')} description={t('dashboard.attention.description')} />
        <div className="mt-4 space-y-2">
          <SkeletonRow columns={3} />
          <SkeletonRow columns={3} />
        </div>
      </Card>
    );
  }

  const partial = incomplete ? (
    <InlineAlert tone="warning" icon={<CloudOff />} title={t('dashboard.attention.partial')} className="animate-in fade-in">
      {t('dashboard.attention.partial_body')}
    </InlineAlert>
  ) : null;

  // An empty list means "nothing needs attention" only when everything it triages answered.
  if (items.length === 0) {
    return (
      partial ?? (
        <InlineAlert tone="success" icon={<Sparkles />} title={t('dashboard.attention.all_clear')} className="animate-in fade-in">
          {t('dashboard.attention.all_clear_body')}
        </InlineAlert>
      )
    );
  }

  return (
    <div className="space-y-4">
      {partial}
      <Card className="p-5 sm:p-6 animate-in fade-in">
        <SectionHeading title={t('dashboard.attention.title')} description={t('dashboard.attention.description')}>
          <span className="text-xs font-semibold tabular-nums text-muted-foreground">{items.length}</span>
        </SectionHeading>
        <ul className="mt-4 divide-y divide-border/60">
          {items.map((item) => {
            const c = toneClasses(item.tone);
            return (
              <li key={item.id}>
                <Link
                  to={item.to}
                  className="group -mx-2 flex items-center gap-3 rounded-xl px-2 py-2.5 text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span
                    aria-hidden="true"
                    className={cn('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl [&>svg]:h-4 [&>svg]:w-4', c.bg, c.text)}
                  >
                    {item.icon}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-foreground">{item.title}</span>
                    {item.hint && <span className="block truncate text-xs text-muted-foreground">{item.hint}</span>}
                  </span>
                  <span className="inline-flex shrink-0 items-center gap-1 text-xs font-semibold text-muted-foreground transition-colors group-hover:text-foreground">
                    {item.actionLabel ?? t('dashboard.attention.open')}
                    <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </Card>
    </div>
  );
}
