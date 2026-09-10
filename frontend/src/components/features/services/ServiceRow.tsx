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

/** One `<tr>` of the list view. */
export function ServiceRow(props: ServiceItemProps) {
  const { service, providers, selected, onToggleSelect, busy, checkResult, drift, onEdit, onDelete, onCheck, onDrift } = props;
  const t = useT();
  const host = publicHostOf(service);
  const enabled = Boolean(service.enabled);

  return (
    <tr
      className={cn(
        'group border-b border-border transition-colors last:border-b-0 hover:bg-accent/40',
        selected && 'bg-primary/5',
        busy && 'opacity-60',
        !enabled && 'text-muted-foreground',
      )}
      aria-selected={selected}
      aria-busy={busy}
    >
      <td className="w-10 px-3 py-3 align-top">
        <Checkbox
          checked={selected}
          onChange={() => onToggleSelect(service.id)}
          aria-label={t('services.select_row', { host })}
        />
      </td>
      <td className="w-12 px-1 py-2.5 align-top">
        <StatusToggle service={service} busy={busy} onToggleStatus={props.onToggleStatus} />
      </td>
      <td className="min-w-[16rem] px-3 py-3 align-top">
        <div className="flex flex-col gap-1">
          <HostLink host={host} />
          {service.subdomain && service.expose_mode === 'tunnel' && service.tunnel_hostname && (
            <span className="truncate text-xs text-muted-foreground">{`${service.subdomain}.${service.domain}`}</span>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <ModeBadge service={service} />
            {drift && <DriftIndicator drift={drift} onOpen={() => onDrift(service)} />}
          </div>
          <TaxonomyChips
            service={service}
            onTagClick={props.onTagClick}
            onEnvClick={props.onEnvClick}
            activeTagId={props.activeTagId}
            activeEnvId={props.activeEnvId}
          />
        </div>
      </td>
      <td className="px-3 py-3 align-top">
        <TargetChip service={service} />
      </td>
      <td className="px-3 py-3 align-top">
        <ProviderLogos service={service} providers={providers} />
      </td>
      <td className="px-3 py-3 align-top">
        <div className="flex flex-col gap-1">
          <StatusBadge service={service} />
          {checkResult && <CheckResultInline result={checkResult} />}
        </div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <div className="flex items-center justify-end gap-0.5">
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
      </td>
    </tr>
  );
}
