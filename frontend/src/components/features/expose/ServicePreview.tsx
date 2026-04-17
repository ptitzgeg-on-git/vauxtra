import { ArrowRight } from 'lucide-react';
import type { FormState, Provider } from './types';

interface ServicePreviewProps {
  formData: FormState;
  selectedProxy: Provider | undefined;
  selectedDns: Provider | undefined;
  selectedTunnel: Provider | undefined;
  selectedExtraProxies: Provider[];
  selectedExtraDns: Provider[];
}

export function ServicePreview({
  formData,
  selectedProxy,
  selectedDns,
  selectedTunnel,
  selectedExtraProxies,
  selectedExtraDns,
}: ServicePreviewProps) {
  const fqdnPreview =
    formData.subdomain && formData.domain
      ? `${formData.subdomain}.${formData.domain}`
      : 'subdomain.domain.tld';

  const publicHostPreview =
    formData.expose_mode === 'tunnel'
      ? formData.tunnel_hostname.trim() || fqdnPreview
      : fqdnPreview;

  return (
    <div>
      <h3 className="text-sm font-bold text-foreground uppercase tracking-widest mb-4">
        4. Route Schema Preview
      </h3>

      <div className="p-4 rounded-xl bg-muted border border-border">
        <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-foreground">
          <span className="px-2 py-1 rounded bg-card border border-border">Browser</span>
          <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="px-2 py-1 rounded bg-card border border-border font-mono">
            {publicHostPreview}
          </span>
          <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="px-2 py-1 rounded bg-secondary border border-border text-secondary-foreground">
            DNS:{' '}
            {formData.expose_mode === 'tunnel'
              ? 'managed by tunnel provider'
              : selectedDns
                ? selectedDns.name
                : 'none'}
            {formData.expose_mode === 'proxy_dns' && selectedExtraDns.length > 0
              ? ` +${selectedExtraDns.length}`
              : ''}
          </span>
          <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="px-2 py-1 rounded bg-primary/10 border border-primary/20 text-primary">
            Proxy:{' '}
            {formData.expose_mode === 'tunnel'
              ? selectedTunnel
                ? selectedTunnel.name
                : 'none'
              : selectedProxy
                ? selectedProxy.name
                : 'none (direct/manual)'}
            {selectedExtraProxies.length > 0 ? ` +${selectedExtraProxies.length}` : ''}
          </span>
          <ArrowRight className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="px-2 py-1 rounded bg-accent border border-border text-accent-foreground font-mono">
            {formData.forward_scheme}://{formData.target_ip || 'target'}:{formData.target_port}
          </span>
        </div>
      </div>
    </div>
  );
}
