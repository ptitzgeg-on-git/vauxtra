import type { ReactNode } from 'react';
import {
  ArrowRightLeft,
  Check,
  CircleAlert,
  Copy,
  ExternalLink,
  Globe,
  Power,
  PowerOff,
  Waypoints,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { useFormat } from '@/hooks/useFormat';
import { Badge, Chip, IconButton, ProviderLogo, Tooltip } from '@/components/ui';
import type { Tone } from '@/components/ui';
import type { DriftResult, Environment, Provider, Service, ServiceCheckResult, Tag } from '@/types/api';
import {
  isLocalDnsType,
  isNavigablePublicHost,
  providersOf,
  routeKindOf,
  targetOf,
  type ProviderRoleEntry,
  type RouteKind,
} from './helpers';

/** Everything a row or a card needs; the page owns the state, the items only render it. */
export interface ServiceItemProps {
  service: Service;
  providers: Provider[];
  selected: boolean;
  onToggleSelect: (id: number) => void;
  /** A mutation is in flight for this service — actions are disabled, the row dims. */
  busy: boolean;
  /** Result of the last "Check now" this session, if any. */
  checkResult?: ServiceCheckResult;
  /** Last drift report this session, if any. */
  drift?: DriftResult;
  onToggleStatus: (service: Service) => void;
  onEdit: (service: Service) => void;
  onDelete: (service: Service) => void;
  onCheck: (service: Service) => void;
  onDrift: (service: Service) => void;
  onTagClick: (tag: Tag) => void;
  onEnvClick: (env: Environment) => void;
  activeTagId: number | null;
  activeEnvId: number | null;
}

const MODE_TONES: Record<RouteKind, Tone> = {
  disabled: 'neutral',
  tunnel: 'primary',
  proxy_dns: 'info',
  proxy: 'info',
  dns: 'info',
  none: 'danger',
};

const MODE_ICONS: Record<RouteKind, ReactNode> = {
  disabled: <PowerOff />,
  tunnel: <Waypoints />,
  proxy_dns: <ArrowRightLeft />,
  proxy: <ArrowRightLeft />,
  dns: <Globe />,
  none: <CircleAlert />,
};

/** "Tunnel", "Proxy + Local DNS", "External DNS"… derived from mode and provider types. */
export function ModeBadge({ service, className }: { service: Service; className?: string }) {
  const t = useT();
  const kind = routeKindOf(service);
  const dnsLabel = isLocalDnsType(service.dns_type) ? t('services.mode.dns_local') : t('services.mode.dns_external');
  const label =
    kind === 'disabled'
      ? t('services.mode.disabled')
      : kind === 'tunnel'
        ? t('services.mode.tunnel')
        : kind === 'proxy_dns'
          ? t('services.mode.proxy_dns', { dns: dnsLabel })
          : kind === 'proxy'
            ? t('services.mode.proxy')
            : kind === 'dns'
              ? dnsLabel
              : t('services.mode.none');
  const tone: Tone = kind === 'dns' && isLocalDnsType(service.dns_type) ? 'warning' : MODE_TONES[kind];
  return (
    <Badge tone={tone} size="sm" icon={MODE_ICONS[kind]} className={cn('whitespace-nowrap', className)}>
      {label}
    </Badge>
  );
}

const STATUS_TONES: Record<string, Tone> = { ok: 'success', error: 'danger', unknown: 'neutral' };

/** Health dot + label over the relative last-check time, with the exact time as a tooltip. */
export function StatusBadge({ service, className }: { service: Service; className?: string }) {
  const t = useT();
  const { formatRelative, formatDateTime } = useFormat();
  const status = service.status || 'unknown';
  const tone = STATUS_TONES[status] ?? 'neutral';
  const label =
    status === 'ok' ? t('services.status.ok') : status === 'error' ? t('services.status.error') : t('services.status.unknown');
  const lastCheck = service.last_checked
    ? t('services.last_check', { when: formatRelative(service.last_checked) })
    : t('services.never_checked');
  // The tooltip only opens on hover -- this span is not focusable and must not become a tab stop
  // in a table -- so the exact time is also written out for screen readers rather than living
  // in the bubble alone.
  const exact = service.last_checked ? formatDateTime(service.last_checked) : null;
  return (
    <Tooltip content={exact ?? lastCheck}>
      <span className={cn('inline-flex flex-col items-start gap-0.5', className)}>
        <Badge tone={tone} size="sm" dot>
          {label}
        </Badge>
        <span className="text-[11px] text-muted-foreground">
          {lastCheck}
          {exact && <span className="sr-only"> — {t('ui.tooltip.exact_time', { when: exact })}</span>}
        </span>
      </span>
    </Tooltip>
  );
}

/** The public host as a link (or plain text for wildcards) plus a copy button. */
export function HostLink({ host, className }: { host: string; className?: string }) {
  const t = useT();
  const navigable = isNavigablePublicHost(host);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(host);
      toast.success(t('services.host_copied'));
    } catch {
      toast.error(t('services.copy_failed'));
    }
  };
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1', className)}>
      {navigable ? (
        <a
          href={`https://${host}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex min-w-0 items-center gap-1 truncate font-mono text-sm font-medium text-foreground hover:text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
          title={t('services.open_host', { host })}
        >
          <span className="truncate">{host}</span>
          <ExternalLink aria-hidden="true" className="h-3 w-3 shrink-0 text-muted-foreground" />
        </a>
      ) : (
        <span className="truncate font-mono text-sm font-medium text-foreground" title={t('services.wildcard_host')}>
          {host}
        </span>
      )}
      <IconButton
        label={t('services.copy_host', { host })}
        icon={<Copy />}
        size="icon"
        className="h-7 w-7 shrink-0 text-muted-foreground"
        onClick={copy}
      />
    </span>
  );
}

/** `scheme://ip:port` in mono. */
export function TargetChip({ service, className }: { service: Service; className?: string }) {
  return (
    <code
      className={cn(
        'inline-flex max-w-full items-center truncate rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground',
        className,
      )}
    >
      {targetOf(service)}
    </code>
  );
}

const ROLE_KEYS: Record<ProviderRoleEntry['role'], string> = {
  tunnel: 'services.role.tunnel',
  proxy: 'services.role.proxy',
  dns: 'services.role.dns',
  extra_proxy: 'services.role.extra_proxy',
  extra_dns: 'services.role.extra_dns',
};

/** Provider logos with a name · role tooltip; falls back to the denormalised names when the list is not loaded. */
export function ProviderLogos({ service, providers, className }: { service: Service; providers: Provider[]; className?: string }) {
  const t = useT();
  const entries = providersOf(service, providers);
  if (entries.length === 0) {
    const fallback = [service.tunnel_provider_name, service.proxy_provider_name, service.dns_provider_name].filter(Boolean);
    return (
      <span className={cn('text-xs text-muted-foreground', className)}>
        {fallback.length > 0 ? fallback.join(', ') : t('services.no_provider')}
      </span>
    );
  }
  return (
    <span className={cn('inline-flex items-center gap-1', className)} role="list" aria-label={t('services.providers')}>
      {entries.map(({ provider, role }) => {
        const name = `${provider.name} · ${t(ROLE_KEYS[role])}`;
        return (
          // `role="listitem"` sits on this wrapper, not on the chip: Tooltip renders its own span
          // around its child, and an intervening generic element breaks the list's ownership of
          // its items. The chip itself is decoration -- no tab stop, because there is nothing to
          // operate; the name is carried here, where the list can read it.
          <span key={`${role}-${provider.id}`} role="listitem" aria-label={name} className="inline-flex">
            <Tooltip content={name}>
              <span
                className={cn(
                  'inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border bg-card',
                  !provider.enabled && 'opacity-50',
                )}
              >
                <ProviderLogo type={provider.type} className="h-4 w-4" />
              </span>
            </Tooltip>
          </span>
        );
      })}
    </span>
  );
}

const dotStyle = (color: string | null | undefined) => (color ? { backgroundColor: color } : undefined);

/** Tags and environments as filter chips — clicking one narrows the list. */
export function TaxonomyChips({
  service,
  onTagClick,
  onEnvClick,
  activeTagId,
  activeEnvId,
  className,
}: Pick<ServiceItemProps, 'service' | 'onTagClick' | 'onEnvClick' | 'activeTagId' | 'activeEnvId'> & { className?: string }) {
  const t = useT();
  const tags = Array.isArray(service.tags) ? service.tags : [];
  const envs = Array.isArray(service.environments) ? service.environments : [];
  if (tags.length === 0 && envs.length === 0) return null;
  return (
    <span className={cn('flex flex-wrap items-center gap-1', className)}>
      {tags.map((tag) => (
        <Chip
          key={`tag-${tag.id}`}
          size="sm"
          tone="primary"
          selected={activeTagId === tag.id}
          onClick={() => onTagClick(tag)}
          aria-label={t('services.filter_by_tag', { name: tag.name })}
          icon={<span className="inline-block h-2 w-2 rounded-full bg-primary" style={dotStyle(tag.color)} />}
        >
          {tag.name}
        </Chip>
      ))}
      {envs.map((env) => (
        <Chip
          key={`env-${env.id}`}
          size="sm"
          tone="info"
          selected={activeEnvId === env.id}
          onClick={() => onEnvClick(env)}
          aria-label={t('services.filter_by_env', { name: env.name })}
          icon={<span className="inline-block h-2 w-2 rounded-full bg-info" style={dotStyle(env.color)} />}
        >
          {env.name}
        </Chip>
      ))}
    </span>
  );
}

/** Inline result of "Check now": status, latency and what DNS resolved to. */
export function CheckResultInline({ result, className }: { result: ServiceCheckResult; className?: string }) {
  const t = useT();
  const { formatLatency } = useFormat();
  const tone: Tone = result.status === 'ok' ? 'success' : result.status === 'error' ? 'danger' : 'neutral';
  const label =
    result.status === 'ok' ? t('services.check.ok') : result.status === 'error' ? t('services.check.error') : t('services.check.unknown');
  const resolved = Array.isArray(result.dns_resolved) ? result.dns_resolved : [];
  return (
    <span
      role="status"
      className={cn('inline-flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground animate-in fade-in animate-duration-200', className)}
    >
      <Badge tone={tone} size="sm" icon={result.status === 'ok' ? <Check /> : <CircleAlert />}>
        {label}
      </Badge>
      {result.latency_ms != null && <span className="tabular-nums">{formatLatency(result.latency_ms)}</span>}
      {resolved.length > 0 && (
        <span className="font-mono" title={t('services.check.resolved_to')}>
          {resolved.join(', ')}
        </span>
      )}
      {result.status === 'error' && resolved.length === 0 && result.dns_resolved !== null && (
        <span>{t('services.check.no_resolution')}</span>
      )}
    </span>
  );
}

/** A small badge on rows that were drift-checked; clicking opens the report drawer. */
export function DriftIndicator({ drift, onOpen, className }: { drift: DriftResult; onOpen: () => void; className?: string }) {
  const t = useT();
  const errors = drift.issues.filter((i) => i.severity === 'error').length;
  const warns = drift.issues.length - errors;
  const tone: Tone = drift.ok ? 'success' : errors > 0 ? 'danger' : 'warning';
  const label = drift.ok
    ? t('services.drift.in_sync')
    : errors > 0
      ? t('services.drift.errors', { count: errors })
      : t('services.drift.warnings', { count: warns });
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn('inline-flex rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring', className)}
      aria-label={t('services.drift.open_report', { state: label })}
    >
      <Badge tone={tone} size="sm" dot>
        {label}
      </Badge>
    </button>
  );
}

/** The enable/disable toggle shared by rows and cards. */
export function StatusToggle({ service, busy, onToggleStatus }: Pick<ServiceItemProps, 'service' | 'busy' | 'onToggleStatus'>) {
  const t = useT();
  const enabled = Boolean(service.enabled);
  return (
    <IconButton
      label={enabled ? t('services.action.disable') : t('services.action.enable')}
      icon={enabled ? <Power /> : <PowerOff />}
      tooltip
      className={cn('h-8 w-8', enabled ? 'text-success hover:text-success' : 'text-muted-foreground')}
      aria-pressed={enabled}
      disabled={busy}
      onClick={() => onToggleStatus(service)}
    />
  );
}
