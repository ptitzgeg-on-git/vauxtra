import type { ReactNode } from 'react';
import { ArrowRight, Globe, MonitorSmartphone, Server, Waypoints } from 'lucide-react';
import { useT } from '@/i18n';
import { cn } from '@/lib/cn';
import { Badge, ProviderLogo, SectionHeading, Tooltip } from '@/components/ui';
import { fqdnOf, type FormState, type Provider } from './types';

interface ServicePreviewProps {
  formData: FormState;
  selectedProxy: Provider | undefined;
  selectedDns: Provider | undefined;
  selectedTunnel: Provider | undefined;
  selectedExtraProxies: Provider[];
  selectedExtraDns: Provider[];
}

type NodeTone = 'neutral' | 'primary' | 'info' | 'warning';

const NODE_TONES: Record<NodeTone, string> = {
  neutral: 'border-border bg-card text-foreground',
  primary: 'border-primary/30 bg-primary/5 text-foreground',
  info: 'border-info/30 bg-info/5 text-foreground',
  warning: 'border-warning/30 bg-warning/10 text-foreground',
};

function RouteNode({
  icon,
  label,
  value,
  tone = 'neutral',
  extras,
  mono,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  tone?: NodeTone;
  extras?: Provider[];
  mono?: boolean;
}) {
  const t = useT();
  return (
    <div
      className={cn(
        'flex min-w-0 items-center gap-2.5 rounded-xl border px-3 py-2 shadow-sm transition-colors',
        NODE_TONES[tone],
      )}
    >
      <span
        aria-hidden="true"
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground [&>svg]:h-4 [&>svg]:w-4"
      >
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
        <p className={cn('truncate text-xs font-medium', mono && 'font-mono')}>{value}</p>
      </div>
      {extras && extras.length > 0 && (
        <Tooltip content={extras.map((p) => p.name).join(', ')}>
          <Badge tone="primary" size="sm" aria-label={t('expose.preview.extra_providers', { count: extras.length })}>
            +{extras.length}
          </Badge>
        </Tooltip>
      )}
    </div>
  );
}

function Connector() {
  return <ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground/70" />;
}

/**
 * The route as a diagram: client, public host, then the hops that answer it (tunnel, or
 * DNS + reverse proxy), then the internal target.
 */
export function ServicePreview({
  formData,
  selectedProxy,
  selectedDns,
  selectedTunnel,
  selectedExtraProxies,
  selectedExtraDns,
}: ServicePreviewProps) {
  const t = useT();
  const fqdn = fqdnOf(formData) ?? t('expose.preview.host_placeholder');
  const publicHost =
    formData.expose_mode === 'tunnel' ? formData.tunnel_hostname.trim() || fqdn : fqdn;
  const target = `${formData.forward_scheme}://${formData.target_ip || t('expose.preview.target_placeholder')}:${formData.target_port}`;
  const isTunnel = formData.expose_mode === 'tunnel';
  const isDnsOnly = !isTunnel && formData.ui_expose_mode === 'dns_only';

  return (
    <section aria-label={t('expose.preview.title')} className="space-y-3">
      <SectionHeading size="sm" title={t('expose.preview.title')} description={t('expose.preview.description')} />
      <div className="rounded-2xl border border-border bg-muted/40 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <RouteNode icon={<MonitorSmartphone />} label={t('expose.preview.client')} value={t('expose.preview.client_value')} />
          <Connector />
          <RouteNode icon={<Globe />} label={t('expose.preview.public_host')} value={publicHost} mono tone="primary" />
          <Connector />
          {isTunnel ? (
            <RouteNode
              icon={selectedTunnel ? <ProviderLogo type={selectedTunnel.type} /> : <Waypoints />}
              label={t('expose.preview.tunnel')}
              value={selectedTunnel ? selectedTunnel.name : t('expose.preview.none')}
              tone={selectedTunnel ? 'info' : 'warning'}
              extras={selectedExtraProxies}
            />
          ) : (
            <>
              <RouteNode
                icon={selectedDns ? <ProviderLogo type={selectedDns.type} /> : <Globe />}
                label={t('expose.preview.dns')}
                value={selectedDns ? selectedDns.name : t('expose.preview.none')}
                tone={selectedDns ? 'info' : 'neutral'}
                extras={selectedExtraDns}
              />
              {!isDnsOnly && (
                <>
                  <Connector />
                  <RouteNode
                    icon={selectedProxy ? <ProviderLogo type={selectedProxy.type} /> : <Server />}
                    label={t('expose.preview.proxy')}
                    value={selectedProxy ? selectedProxy.name : t('expose.preview.none')}
                    tone={selectedProxy ? 'info' : 'neutral'}
                    extras={selectedExtraProxies}
                  />
                </>
              )}
            </>
          )}
          <Connector />
          <RouteNode icon={<Server />} label={t('expose.preview.target')} value={target} mono />
        </div>
        {isDnsOnly && (
          <p className="mt-3 text-xs text-muted-foreground">{t('expose.preview.dns_only_note')}</p>
        )}
        {isTunnel && (
          <p className="mt-3 text-xs text-muted-foreground">{t('expose.preview.tunnel_note')}</p>
        )}
      </div>
    </section>
  );
}
