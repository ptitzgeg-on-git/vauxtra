import { Activity, GitCompareArrows, Pencil, Trash2 } from 'lucide-react';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { Checkbox, IconButton } from '@/components/ui';
import { publicHostOf } from './helpers';
import {
  CheckResultInline,
  DriftIndicator,
  HostLink,
  ModeBadge,
  ProviderLogos,
  StatusBadge,
  StatusToggle,
  TargetChip,
  TaxonomyChips,
  type ServiceItemProps,
} from './ServiceBits';

const TOP_BAR: Record<string, string> = {
  ok: 'bg-success',
  error: 'bg-destructive',
  unknown: 'bg-muted-foreground/40',
};

/** One card of the grid view; the top bar carries the health colour. */
export function ServiceCard(props: ServiceItemProps) {
  const { service, providers, selected, onToggleSelect, busy, checkResult, drift, onEdit, onDelete, onCheck, onDrift } = props;
  const t = useT();
  const host = publicHostOf(service);
  const enabled = Boolean(service.enabled);
  const status = enabled ? service.status || 'unknown' : 'unknown';

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-card transition-shadow hover:shadow-elevated',
        selected && 'ring-2 ring-primary/40',
        busy && 'opacity-60',
      )}
      aria-busy={busy}
      aria-label={host}
    >
      <div aria-hidden="true" className={cn('h-1 w-full', TOP_BAR[status] ?? TOP_BAR.unknown)} />
      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-start gap-2">
          <Checkbox
            checked={selected}
            onChange={() => onToggleSelect(service.id)}
            aria-label={t('services.select_row', { host })}
            className="mt-1"
          />
          <div className="min-w-0 flex-1">
            <HostLink host={host} />
            {service.expose_mode === 'tunnel' && service.tunnel_hostname && service.subdomain && (
              <p className="truncate text-xs text-muted-foreground">{`${service.subdomain}.${service.domain}`}</p>
            )}
          </div>
          <StatusToggle service={service} busy={busy} onToggleStatus={props.onToggleStatus} />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <ModeBadge service={service} />
          {drift && <DriftIndicator drift={drift} onOpen={() => onDrift(service)} />}
        </div>

        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-xs">
          <dt className="text-muted-foreground">{t('services.column.target')}</dt>
          <dd className="min-w-0">
            <TargetChip service={service} />
          </dd>
          <dt className="text-muted-foreground">{t('services.column.providers')}</dt>
          <dd className="min-w-0">
            <ProviderLogos service={service} providers={providers} />
          </dd>
        </dl>

        <TaxonomyChips
          service={service}
          onTagClick={props.onTagClick}
          onEnvClick={props.onEnvClick}
          activeTagId={props.activeTagId}
          activeEnvId={props.activeEnvId}
        />

        <div className="mt-auto flex flex-col gap-1.5 border-t border-border pt-3">
          <div className="flex items-start justify-between gap-2">
            <StatusBadge service={service} />
            <div className="flex items-center gap-0.5">
              <IconButton
                label={t('services.action.check')}
                icon={<Activity />}
                tooltip
                className="h-8 w-8"
                disabled={busy}
                onClick={() => onCheck(service)}
              />
              <IconButton
                label={t('services.action.drift')}
                icon={<GitCompareArrows />}
                tooltip
                className="h-8 w-8"
                disabled={busy}
                onClick={() => onDrift(service)}
              />
              <IconButton
                label={t('services.action.edit')}
                icon={<Pencil />}
                tooltip
                className="h-8 w-8"
                disabled={busy}
                onClick={() => onEdit(service)}
              />
              <IconButton
                label={t('services.action.delete')}
                icon={<Trash2 />}
                tooltip
                className="h-8 w-8 text-muted-foreground hover:text-destructive"
                disabled={busy}
                onClick={() => onDelete(service)}
              />
            </div>
          </div>
          {checkResult && <CheckResultInline result={checkResult} />}
        </div>
      </div>
    </article>
  );
}
