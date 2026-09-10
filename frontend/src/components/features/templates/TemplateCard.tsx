import { memo, type ReactNode } from 'react';
import { Cable, Copy, Globe, Link2, Network, Pencil, Server, Trash2, Wand2 } from 'lucide-react';
import { Badge, Button, Chip, IconButton, Tooltip, cn } from '@/components/ui';
import { useFormat } from '@/hooks/useFormat';
import { useT } from '@/i18n';
import type { Provider, Tag, Template } from '@/types/api';
import { TemplateIcon } from './TemplateIcon';
import { endpointLabel, tagTone } from './types';

export interface TemplateCardProps {
  template: Template;
  /** Every provider, by id — a template stores ids, the card shows names. */
  providersById: Map<number, Provider>;
  /** Every tag, by id, same reason. */
  tagsById: Map<number, Tag>;
  /** Tag ids the list is currently filtered on; a card chip toggles one. */
  activeTagIds: number[];
  onToggleTag: (tagId: number) => void;
  onUse: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  duplicating?: boolean;
  deleting?: boolean;
}

interface DetailProps {
  icon: ReactNode;
  label: string;
  children: ReactNode;
  mono?: boolean;
}

function Detail({ icon, label, children, mono = false }: DetailProps) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span aria-hidden="true" className="shrink-0 text-muted-foreground [&>svg]:h-3.5 [&>svg]:w-3.5">
        {icon}
      </span>
      <dt className="sr-only">{label}</dt>
      <dd className={cn('min-w-0 truncate text-xs text-muted-foreground', mono && 'font-mono')}>{children}</dd>
    </div>
  );
}

export const TemplateCard = memo(function TemplateCard({
  template,
  providersById,
  tagsById,
  activeTagIds,
  onToggleTag,
  onUse,
  onEdit,
  onDuplicate,
  onDelete,
  duplicating = false,
  deleting = false,
}: TemplateCardProps) {
  const t = useT();
  const { formatRelative, formatDateTime } = useFormat();

  const isTunnel = template.expose_mode === 'tunnel';
  const tags = (template.tag_ids ?? []).map((id) => ({ id, tag: tagsById.get(id) })).filter((entry) => entry.tag);

  /** A provider id as a name, or the reason there is no name to show. */
  const providerLabel = (id: number | null | undefined): { text: string; missing: boolean } | null => {
    if (id === null || id === undefined) return null;
    const provider = providersById.get(id);
    if (!provider) return { text: t('templates.card.provider_missing'), missing: true };
    return { text: provider.name, missing: false };
  };

  const proxy = isTunnel ? null : providerLabel(template.proxy_provider_id);
  const dns = isTunnel ? null : providerLabel(template.dns_provider_id);
  const tunnel = isTunnel ? providerLabel(template.tunnel_provider_id) : null;

  const renderProvider = (entry: { text: string; missing: boolean }) => (
    <span className={cn(entry.missing && 'text-warning')}>{entry.text}</span>
  );

  return (
    <article
      className="group flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-card transition-shadow hover:shadow-elevated focus-within:shadow-elevated"
      aria-label={template.name}
    >
      <div className="flex flex-1 flex-col gap-3 p-5">
        <div className="flex items-start gap-3">
          <TemplateIcon url={template.icon_url} size="md" />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-semibold leading-tight text-foreground" title={template.name}>
              {template.name}
            </h3>
            <p className={cn('mt-1 text-xs leading-relaxed', template.description ? 'text-muted-foreground line-clamp-2' : 'text-muted-foreground/70 italic')}>
              {template.description || t('templates.card.no_description')}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Badge
            tone={isTunnel ? 'primary' : 'info'}
            size="sm"
            icon={isTunnel ? <Cable /> : <Server />}
          >
            {isTunnel ? t('templates.mode.tunnel') : t('templates.mode.proxy_dns')}
          </Badge>
          {Boolean(template.websocket) && (
            <Badge tone="neutral" size="sm">
              {t('templates.card.websocket')}
            </Badge>
          )}
          {!isTunnel && template.public_target_mode === 'auto' && (
            <Badge tone="success" size="sm">
              {t('templates.card.auto_target')}
            </Badge>
          )}
        </div>

        <dl className="grid gap-1.5">
          <Detail icon={<Link2 />} label={t('templates.card.endpoint')} mono>
            {endpointLabel(template)}
          </Detail>
          {template.domain && (
            <Detail icon={<Globe />} label={t('templates.card.domain')} mono>
              {template.domain}
            </Detail>
          )}
          {proxy && <Detail icon={<Server />} label={t('templates.card.proxy')}>{renderProvider(proxy)}</Detail>}
          {dns && <Detail icon={<Globe />} label={t('templates.card.dns')}>{renderProvider(dns)}</Detail>}
          {tunnel && <Detail icon={<Cable />} label={t('templates.card.tunnel')}>{renderProvider(tunnel)}</Detail>}
          {!isTunnel && template.dns_ip && (
            <Detail icon={<Network />} label={t('templates.card.dns_ip')} mono>
              {template.dns_ip}
            </Detail>
          )}
        </dl>

        {tags.length > 0 && (
          <div role="group" aria-label={t('templates.card.tags')} className="flex flex-wrap items-center gap-1.5 pt-0.5">
            {tags.map(({ id, tag }) => (
              <Chip
                key={id}
                size="sm"
                tone={tagTone(tag?.color)}
                selected={activeTagIds.includes(id)}
                onClick={() => onToggleTag(id)}
                title={t('templates.card.filter_by_tag', { name: tag?.name ?? '' })}
              >
                {tag?.name}
              </Chip>
            ))}
          </div>
        )}

        {template.created_at && (
          <div className="mt-auto pt-1">
            <Tooltip content={formatDateTime(template.created_at)}>
              <span className="text-[11px] text-muted-foreground/80">
                {t('templates.card.created', { date: formatRelative(template.created_at) })}
              </span>
            </Tooltip>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-border bg-muted/30 px-4 py-3">
        <Button size="sm" variant="primary" leftIcon={<Wand2 />} onClick={onUse}>
          {t('templates.card.use')}
        </Button>
        <div className="ml-auto flex items-center gap-1">
          <IconButton
            size="sm"
            variant="ghost"
            label={t('templates.card.edit', { name: template.name })}
            icon={<Pencil />}
            onClick={onEdit}
          />
          <IconButton
            size="sm"
            variant="ghost"
            label={t('templates.card.duplicate', { name: template.name })}
            icon={<Copy />}
            loading={duplicating}
            disabled={duplicating || deleting}
            onClick={onDuplicate}
          />
          <IconButton
            size="sm"
            variant="ghost"
            className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            label={t('templates.card.delete', { name: template.name })}
            icon={<Trash2 />}
            loading={deleting}
            disabled={duplicating || deleting}
            onClick={onDelete}
          />
        </div>
      </div>
    </article>
  );
});
